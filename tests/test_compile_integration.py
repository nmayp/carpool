"""compile + carpool: code answers what it can, the model gets only what is left."""
import json
import re
import sys
from pathlib import Path

import pytest

from carpool import Completion, MockBackend, Task
from carpool.cli import load_tasks, main
from carpool.compile import CompiledTasks, task_input, task_text
from carpool.ledger import regressions, score
from carpool.router import run_carpool, run_individual
from carpool.sizing import run_adaptive

# Compiled code for the "Which team maintains ..." questions only, written the way a model would write it.
TEAM_CODE = '''
import re
def solve(question, sources):
    if not question.startswith("Which team maintains"):
        return None
    m = re.search(r"is maintained by the ([A-Z][A-Za-z ]+?)\\.", sources)
    return m.group(1) if m else None
'''
ONE_ARG = "def solve(question):\n    return 'x' if 'maintains' in question else None\n"


class Counting:
    """Wraps a backend and records every prompt it is asked."""

    def __init__(self, inner):
        self.inner, self.prompts = inner, []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.inner(prompt)


@pytest.fixture
def tasks():
    return load_tasks(None)  # the bundled 24 ops tasks: 8 team questions, 8 cross-sentence, 8 stale-evidence


@pytest.fixture
def mock(tasks):
    return MockBackend({t.id: t.answers[0] for t in tasks})


def team_ids(tasks):
    return {t.id for t in tasks if t.question.startswith("Which team")}


# --- the wrapper --------------------------------------------------------------

def test_task_text_marks_untrusted_sources(tasks):
    stale = next(t for t in tasks if t.id.startswith("C"))
    assert "[untrusted] DRAFT, superseded" in task_text(stale)
    assert task_text(next(t for t in tasks if t.id.startswith("S"))).count("[untrusted]") == 0


def test_function_arity_decides_what_it_is_called_with(tasks):
    t = tasks[0]
    assert task_input(t, 1) == t.question
    assert task_input(t, 2) == [t.question, task_text(t)]
    assert CompiledTasks(TEAM_CODE).n_args == 2 and CompiledTasks(ONE_ARG).n_args == 1


def test_compiled_tasks_answers_and_abstains(tasks):
    out = CompiledTasks(TEAM_CODE).answers(tasks)
    for t, o in zip(tasks, out):
        assert (o is not None) == t.question.startswith("Which team")
        if o is not None:
            assert o == t.answers[0]


def test_blank_and_non_string_answers_are_handled():
    code = "def solve(q):\n    return {'a': '', 'b': '   ', 'c': 7}.get(q)\n"
    ts = [Task(i, q, []) for i, q in (("1", "a"), ("2", "b"), ("3", "c"), ("4", "d"))]
    assert CompiledTasks(code).answers(ts) == [None, None, "7", None]


def test_runaway_or_broken_code_means_every_task_goes_to_the_model():
    ts = [Task("1", "q", [])]
    assert CompiledTasks("def solve(q):\n    while True:\n        pass\n", timeout=2).answers(ts) == [None]
    assert CompiledTasks("def solve(q):\n    raise RuntimeError\n").answers(ts) == [None]


def test_loading_code_without_the_function_is_an_error_not_a_silent_skip():
    with pytest.raises(ValueError, match="cannot find function 'solve'"):
        CompiledTasks("x = 1")
    with pytest.raises(ValueError):
        CompiledTasks("def solve(:")


def test_loading_does_not_run_the_code():
    # the signature is read with ast; module-level code must not execute in this process
    marker = "raise SystemExit('executed at load')\n"
    assert CompiledTasks(marker + "def solve(q):\n    return None\n").n_args == 1


# --- routing ------------------------------------------------------------------

def test_model_sees_only_the_tasks_code_could_not_answer(tasks, mock):
    spy = Counting(mock)
    res = run_carpool(tasks, spy, compiled=CompiledTasks(TEAM_CODE))
    done = team_ids(tasks)
    assert {i for i, a in res.answers.items() if a.route == "compiled"} == done
    sent = "\n".join(spy.prompts)
    assert not any(re.search(rf"\b{tid}\b", sent) for tid in done)  # no compiled task id reaches the model
    assert all(t.id in sent for t in tasks if t.id not in done)
    assert res.ledger.n_calls == 1 and score("x", tasks, res.answers, res.ledger).accepted == 24


def test_compiled_answers_cost_no_tokens_and_have_no_source(tasks, mock):
    base = run_carpool(tasks, mock)
    both = run_carpool(tasks, mock, compiled=CompiledTasks(TEAM_CODE))
    assert both.ledger.total_tokens < base.ledger.total_tokens
    assert all(a.source_id == "" and a.fallback_reason is None for a in both.answers.values() if a.route == "compiled")


def test_everything_compiled_means_zero_model_calls(tasks):
    spy = Counting(MockBackend({}))
    everything = "def solve(q, s):\n    return 'anything'\n"
    res = run_carpool(tasks, spy, compiled=CompiledTasks(everything))
    assert spy.prompts == [] and res.ledger.n_calls == 0 and len(res.answers) == 24


def test_code_that_abstains_everywhere_changes_nothing(tasks, mock):
    plain = run_carpool(tasks, mock)
    shy = run_carpool(tasks, mock, compiled=CompiledTasks("def solve(q, s):\n    return None\n"))
    assert {k: (v.answer, v.source_id, v.route) for k, v in plain.answers.items()} == \
           {k: (v.answer, v.source_id, v.route) for k, v in shy.answers.items()}
    assert plain.ledger.n_calls == shy.ledger.n_calls and plain.ledger.total_tokens == shy.ledger.total_tokens


def test_compiled_answers_are_not_reverified_so_the_gate_is_the_only_guard(tasks, mock):
    wrong = "def solve(q, s):\n    return 'Nobody' if q.startswith('Which team') else None\n"
    res = run_carpool(tasks, mock, compiled=CompiledTasks(wrong))
    assert score("x", tasks, res.answers, res.ledger).accepted == 24 - len(team_ids(tasks))
    base = run_individual(tasks, mock)
    assert sorted(regressions(tasks, base.answers, res.answers)) == sorted(team_ids(tasks))


def test_group_size_limit_applies_to_the_leftover_tasks_only(tasks, mock):
    spy = Counting(mock)
    run_carpool(tasks, spy, max_batch=8, compiled=CompiledTasks(TEAM_CODE))
    assert len(spy.prompts) == 2  # 16 left over, groups of 8


def test_adaptive_runner_accepts_compiled_code(tasks, mock):
    res, sizes = run_adaptive(tasks, mock, start=4, compiled=CompiledTasks(TEAM_CODE))
    assert sum(sizes) == 16 and len(res.answers) == 24
    assert sum(a.route == "compiled" for a in res.answers.values()) == 8


# --- command line -------------------------------------------------------------

def test_bench_shows_a_compiled_arm_and_writes_it_to_json(tmp_path, capsys):
    code = tmp_path / "solve.py"
    code.write_text(TEAM_CODE)
    out = tmp_path / "bench.json"
    assert main(["bench", "--compiled", str(code), "--json", str(out)]) == 0
    text = capsys.readouterr().out
    assert "carpool + compiled" in text and "with compiled code first" in text
    data = json.loads(out.read_text())
    assert data["carpool_compiled"]["calls"] == 1 and data["regressions_compiled"] == []
    assert data["carpool_compiled"]["total_tokens"] < data["carpool"]["total_tokens"]


def test_bench_with_a_bad_compiled_file_exits_with_a_message(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("x = 1\n")
    with pytest.raises(SystemExit) as e:
        main(["bench", "--compiled", str(bad)])
    assert "--compiled" in str(e.value)


def test_compile_from_a_tasks_file_and_the_answers_carpool_printed(tmp_path, capsys):
    tasks_file = tmp_path / "tasks.jsonl"
    rows = []
    for i in range(40):  # 40 team questions with distinct names, as a log of past work
        rows.append({"id": f"T{i}", "question": f"Which team maintains pump {i}?",
                     "sources": [{"id": f"T{i}-a", "text": f"The pump {i} is maintained by the Crew {chr(65 + i % 26)}{i}."}]})
    tasks_file.write_text("\n".join(json.dumps(r) for r in rows))
    answers = tmp_path / "answers.jsonl"
    answers.write_text("\n".join(json.dumps({"id": f"T{i}", "answer": f"Crew {chr(65 + i % 26)}{i}", "route": "batch-compact"})
                                 for i in range(40)) + '\n{"id": "T99", "answer": "ignored: not in tasks"}\n')
    code = ("```python\nimport re\ndef solve(question, sources):\n"
            "    m = re.search(r'maintained by the (Crew \\w+)', sources)\n    return m.group(1) if m else None\n```")
    reply = tmp_path / "reply.md"
    reply.write_text(code)
    out = tmp_path / "solve.py"
    # 40 tasks with --val 0.8 leaves 32 held out, enough for the gate
    rc = main(["compile", str(tasks_file), "--answers", str(answers), "--task", "name the maintaining team",
               "--val", "0.8", "--out", str(out), "--backend", f"cmd:cat {reply}"])
    assert rc == 0 and out.exists()
    assert "40 of the tasks in" in capsys.readouterr().err
    # and the saved file plugs straight back into the router
    t = load_tasks(str(tasks_file))
    res = run_carpool(t, MockBackend({}), compiled=CompiledTasks.from_file(str(out)))
    assert res.ledger.n_calls == 0 and res.answers["T7"].answer == "Crew H7"


def test_compile_from_tasks_tells_the_model_how_untrusted_evidence_is_marked(tmp_path):
    tasks_file = tmp_path / "tasks.jsonl"
    tasks_file.write_text("\n".join(json.dumps({"id": f"T{i}", "question": f"q{i}?", "sources": [{"id": "a", "text": f"t{i}"}]})
                                    for i in range(40)))
    answers = tmp_path / "a.jsonl"
    answers.write_text("\n".join(json.dumps({"id": f"T{i}", "answer": f"t{i}"}) for i in range(40)))
    reply, seen = tmp_path / "reply.md", tmp_path / "prompt.txt"
    reply.write_text("```python\ndef solve(question, sources):\n    return sources\n```")
    main(["compile", str(tasks_file), "--answers", str(answers), "--task", "echo", "--val", "0.8",
          "--out", str(tmp_path / "s.py"), "--backend", f"cmd:sh -c 'cat > {seen}; cat {reply}'"])
    assert "[untrusted]" in seen.read_text() and "never come from them" in seen.read_text()


def test_cli_run_with_compiled_code_only_sends_the_rest_to_the_model(tmp_path, capsys):
    import carpool

    tasks_path = Path(carpool.__file__).parent / "data" / "ops24.jsonl"
    code = tmp_path / "solve.py"
    code.write_text(TEAM_CODE)
    log = tmp_path / "prompts.txt"
    model = tmp_path / "model.py"  # a stand-in for a real model: answers from the bundled gold, records the prompt
    model.write_text(
        "import sys\nsys.path.insert(0, %r)\nfrom carpool import MockBackend\nfrom carpool.cli import load_tasks\n"
        "p = sys.stdin.read()\nopen(%r, 'a').write(p)\n"
        "print(MockBackend({t.id: t.answers[0] for t in load_tasks(None)})(p).text)\n"
        % (str(Path(carpool.__file__).parents[1]), str(log)))
    rc = main(["run", str(tasks_path), "--backend", f"cmd:{sys.executable} {model}", "--compiled", str(code)])
    cap = capsys.readouterr()
    rows = [json.loads(l) for l in cap.out.splitlines() if l.startswith("{")]
    assert rc == 0 and len(rows) == 24
    assert sum(r["route"] == "compiled" for r in rows) == 8 and all(r["source"] == "" for r in rows if r["route"] == "compiled")
    assert "(8 answered by compiled code)" in cap.err
    sent = log.read_text()
    assert not any(re.search(rf"\b{r['id']}\b", sent) for r in rows if r["route"] == "compiled")


def test_cli_run_with_a_bad_compiled_file_exits_before_calling_the_model(tmp_path):
    import carpool

    bad = tmp_path / "bad.py"
    bad.write_text("x = 1\n")
    tasks_path = Path(carpool.__file__).parent / "data" / "ops24.jsonl"
    with pytest.raises(SystemExit) as e:
        main(["run", str(tasks_path), "--backend", "cmd:false", "--compiled", str(bad)])
    assert "--compiled" in str(e.value)
