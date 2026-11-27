import json
import sys
from pathlib import Path

import pytest

from carpool import Cascade, Completion, Gate, compile_log, load_compiled
from carpool.cli import main
from carpool.compile import extract_code, fix_prompt, render, run_code, signature, synth_prompt, wilson_lower

# A toy recurring "model call": read the block id out of an HDFS-style log line.
LINES = [f"Receiving block blk_{1000 + i} src: /10.0.0.{i % 9}:50010 dest: /10.0.0.{(i + 3) % 9}:50010" for i in range(120)]
PAIRS = [(line, f"blk_{1000 + i}") for i, line in enumerate(LINES)]

GOOD = "```python\nimport re\ndef solve(text):\n    m = re.search(r'blk_\\d+', text)\n    return m.group(0) if m else None\n```"
WRONG = "```python\ndef solve(text):\n    return 'blk_0'\n```"  # answers everything, wrongly
SHY = "```python\ndef solve(text):\n    return None\n```"  # abstains on everything
LOOP = "```python\ndef solve(text):\n    while True:\n        pass\n```"


class Fake:
    """A backend that returns canned replies in order and counts calls."""

    def __init__(self, *replies, cost=0.01):
        self.replies, self.cost, self.n = list(replies), cost, 0

    def __call__(self, prompt):
        r = self.replies[min(self.n, len(self.replies) - 1)]
        self.n += 1
        return Completion(text=r, cost_usd=self.cost)


def test_wilson_lower_matches_known_values():
    assert wilson_lower(100, 100) == pytest.approx(0.963, abs=1e-3)
    assert wilson_lower(0, 0) == 0.0
    assert wilson_lower(16, 17) < 0.85  # a good-looking small sample must not pass


def test_extract_code_takes_the_longest_block_or_the_raw_text():
    assert extract_code("x\n```python\na = 1\n```\n```\nb = 22222\n```") == "b = 22222\n"
    assert extract_code("def f(): pass") == "def f(): pass"


def test_signature_follows_the_input_shape():
    assert signature("solve", "text") == "solve(text)"
    assert signature("solve", ["q", "context"]) == "solve(arg1, arg2)"


def test_prompts_carry_the_task_and_the_examples():
    p = synth_prompt("read the block id", "solve(text)", "Allowed answers: x", [("in", "out")])
    assert "read the block id" in p and "Allowed answers: x" in p and "'in' => 'out'" in p
    f = fix_prompt("def solve(t): return 1", "solve", [("a", "b", "c")], [("d", "e")])
    assert "expected 'b', got 'c'" in f and "'d' => expected 'e'" in f


def test_good_function_is_promoted_and_matches_logged_outputs():
    r = compile_log(PAIRS, Fake(GOOD), "read the block id")
    assert r.promoted and r.coverage == 1.0 and r.precision == 1.0
    assert r.n_train + r.n_held_out == 120 and r.calls == 1 and r.cost_usd == pytest.approx(0.01)


def test_wrong_function_is_rejected_by_precision():
    r = compile_log(PAIRS, Fake(WRONG), "read the block id", repairs=0)
    assert not r.promoted and "precision" in r.reason


def test_function_that_never_answers_is_rejected_by_coverage():
    r = compile_log(PAIRS, Fake(SHY), "read the block id", repairs=0)
    assert not r.promoted and r.answered == 0 and "only 0%" in r.reason


def test_repair_round_uses_the_training_part_only_and_can_fix_a_function():
    fake = Fake(WRONG, GOOD)
    r = compile_log(PAIRS, fake, "read the block id", repairs=1)
    assert fake.n == 2 and r.promoted


def test_small_log_is_refused_instead_of_judged_on_a_handful():
    with pytest.raises(ValueError, match="held-out"):
        compile_log(PAIRS[:40], Fake(GOOD), "x")


def test_null_outputs_are_refused_because_none_means_abstain():
    with pytest.raises(ValueError, match="null"):
        compile_log(PAIRS[:-1] + [("line", None)], Fake(GOOD), "x")


def test_runaway_code_abstains_instead_of_hanging():
    assert run_code(LOOP.split("```python\n")[1].rstrip("`\n"), "solve", ["a", "b"], timeout=2) == [None, None]


def test_crashing_function_abstains_per_input():
    code = "def solve(text):\n    return 1 // int(text)\n"
    assert run_code(code, "solve", ["1", "0", "2"]) == [1, None, 0]


def test_gate_thresholds_can_be_tightened():
    r = compile_log(PAIRS, Fake(GOOD), "x", gate=Gate(min_precision=1.0, min_lower_bound=0.999))
    assert not r.promoted and "lower bound" in r.reason


def test_rendered_file_loads_and_cascade_falls_back_to_the_model(tmp_path):
    r = compile_log(PAIRS, Fake(GOOD), "read the block id")
    path = tmp_path / "solve.py"
    path.write_text(render(r))
    assert path.read_text().startswith('"""Compiled by carpool')
    fast = load_compiled(str(path))
    asked = []
    both = Cascade(fast, lambda x: asked.append(x) or "from-model")
    assert both("Receiving block blk_7 src: a") == "blk_7"
    assert both("no id on this line") == "from-model"
    assert asked == ["no id on this line"] and both.hits == 1 and both.misses == 1 and both.coverage == 0.5


def test_cli_end_to_end_writes_the_file_only_when_promoted(tmp_path, capsys):
    log = tmp_path / "calls.jsonl"
    log.write_text("\n".join(json.dumps({"input": x, "output": y}) for x, y in PAIRS))
    reply = tmp_path / "reply.md"
    reply.write_text(GOOD)
    out = tmp_path / "out" / "solve.py"
    code = main(["compile", str(log), "--task", "read the block id", "--out", str(out),
                 "--backend", f"cmd:cat {reply}", "--call-cost", "0.01", "--json", str(tmp_path / "r.json")])
    assert code == 0 and out.exists()
    assert "PROMOTED" in capsys.readouterr().out
    assert json.loads((tmp_path / "r.json").read_text())["promoted"] is True

    reply.write_text(WRONG)
    out2 = tmp_path / "out" / "rejected.py"
    code = main(["compile", str(log), "--task", "read the block id", "--out", str(out2), "--backend", f"cmd:cat {reply}"])
    assert code == 2 and not out2.exists()
    assert "NOT PROMOTED" in capsys.readouterr().out
