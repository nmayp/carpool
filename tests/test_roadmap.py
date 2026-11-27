import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace as NS

from carpool import MockBackend, Task
from carpool.backends import AnthropicBatchBackend, make_backend
from carpool.cli import load_tasks, main
from carpool.router import run_carpool
from carpool.sizing import CrowdingEstimate, best_group_size, run_adaptive

ROOT = Path(__file__).resolve().parents[1]


# --- adaptive group size -----------------------------------------------------

def test_best_group_size_matches_closed_form():
    # worked example: H=26000, E=600, phi=0.001 -> b* ~ 31.3, integer optimum 31
    assert best_group_size(26_000, 600, 0.001, 1, 100) == 31
    assert best_group_size(26_000, 600, 0.001, 1, 20) == 20  # capacity limit binds
    assert best_group_size(26_000, 600, 0.0, 1, 48) == 48


def test_crowding_estimate_learns_from_redos():
    est = CrowdingEstimate()
    est.observe(8, 0, H=26_000, E=600)
    clean = est.phi
    est.observe(16, 6, H=26_000, E=600)
    assert est.phi > clean  # redos raise the crowding estimate
    assert est.recommend(current=16, hi=48) < best_group_size(26_000, 600, clean, 1, 48)


def test_recommend_grows_at_most_double():
    est = CrowdingEstimate()
    est.observe(4, 0, H=26_000, E=600)
    assert est.recommend(current=4, hi=48) == 8


def test_run_adaptive_grows_groups_when_nothing_fails():
    tasks = load_tasks(None)
    mock = MockBackend({t.id: t.answers[0] for t in tasks}, overhead=16_000)
    res, sizes = run_adaptive(tasks, mock, start=2, max_size=48)
    assert sizes[0] == 2 and sizes[1] == 4 and sum(sizes) == 24
    assert len(res.answers) == 24 and res.ledger.n_calls == len(sizes)


# --- Message Batches backend --------------------------------------------------

class FakeBatches:
    def __init__(self):
        self.created = None

    def create(self, requests):
        self.created = requests
        return NS(id="b1", processing_status="in_progress")

    def retrieve(self, _id):
        return NS(id="b1", processing_status="ended")

    def results(self, _id):
        # results arrive in any order; one errored
        usage = NS(input_tokens=10, cache_read_input_tokens=5, cache_creation_input_tokens=0, output_tokens=3)
        ok = lambda cid, text: NS(custom_id=cid, result=NS(type="succeeded", message=NS(
            stop_reason="end_turn", content=[NS(type="text", text=text)], usage=usage)))
        return [ok("r1", "second"), NS(custom_id="r2", result=NS(type="errored")), ok("r0", "first")]


def test_batch_backend_keys_results_by_id():
    fake = NS(messages=NS(batches=FakeBatches()))
    b = AnthropicBatchBackend(client=fake, poll_seconds=0)
    out = b.map(["p0", "p1", "p2"])
    assert [c.text for c in out] == ["first", "second", ""]
    assert out[0].input_tokens == 15 and out[0].cached_input_tokens == 5 and out[0].output_tokens == 3
    reqs = fake.messages.batches.created
    assert [r["custom_id"] for r in reqs] == ["r0", "r1", "r2"]
    assert "fallbacks" not in reqs[0]["params"]  # not accepted by the Batches API


def test_run_carpool_uses_map_for_groups_and_redos():
    tasks = load_tasks(None)[:6]
    mock = MockBackend({t.id: t.answers[0] for t in tasks})
    seen = []

    class Mapper:
        def map(self, prompts):
            seen.append(len(prompts))
            return [mock(p) for p in prompts]

        def __call__(self, p):
            raise AssertionError("map should be used")

    res = run_carpool(tasks, Mapper(), max_batch=3)
    assert seen[0] == 2 and len(res.answers) == 6


def test_make_backend_batch_spec():
    try:
        b = make_backend("anthropic-batch:claude-haiku-5-5")
    except ImportError:
        return  # anthropic SDK not installed in this environment
    assert b.model == "claude-haiku-5-5"


# --- carpool stats ------------------------------------------------------------

def test_stats_command(capsys):
    assert main(["stats", str(ROOT / "results" / "ops24-claude-code.json")]) == 0
    out = capsys.readouterr().out
    assert "baseline" in out and "carpool" in out and "%" in out


# --- fan-out hook -------------------------------------------------------------

def run_hook(event, data_dir):
    env = {**os.environ, "CLAUDE_PLUGIN_DATA": str(data_dir)}
    p = subprocess.run([sys.executable, str(ROOT / "hooks" / "fanout_hint.py")], input=json.dumps(event),
                       capture_output=True, text=True, env=env, timeout=10)
    assert p.returncode == 0
    return p.stdout.strip()


def test_hook_hints_once_after_three_subagents(tmp_path):
    ev = {"session_id": "s1", "tool_name": "Agent", "tool_input": {"prompt": "x"}}
    assert run_hook(ev, tmp_path) == ""
    assert run_hook(ev, tmp_path) == ""
    out = json.loads(run_hook(ev, tmp_path))
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in out["hookSpecificOutput"]  # never changes permissions
    assert "ONE pass" in out["hookSpecificOutput"]["additionalContext"]
    assert run_hook(ev, tmp_path) == ""  # cooldown


def test_hook_ignores_other_tools_and_bad_input(tmp_path):
    assert run_hook({"session_id": "s2", "tool_name": "Bash"}, tmp_path) == ""
    p = subprocess.run([sys.executable, str(ROOT / "hooks" / "fanout_hint.py")], input="not json",
                       capture_output=True, text=True, timeout=10)
    assert p.returncode == 0 and p.stdout == ""


def test_hooks_json_valid():
    cfg = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    h = cfg["hooks"]["PreToolUse"][0]
    assert "Agent" in h["matcher"] and "${CLAUDE_PLUGIN_ROOT}" in h["hooks"][0]["command"]


def test_ollama_sends_context_window(monkeypatch):
    import carpool.agents as ag

    sent = {}

    def fake_post(url, body, headers, timeout=600):
        sent.update(url=url, body=body)
        return {"response": "ok", "prompt_eval_count": 7, "eval_count": 1}

    monkeypatch.setattr(ag, "_post", fake_post)
    c = ag.OllamaBackend("qwen2.5:1.5b", host="localhost:11434")("hi")
    assert sent["url"] == "http://localhost:11434/api/generate"
    assert sent["body"]["options"]["num_ctx"] >= 32768  # default 2k-4k would truncate grouped prompts
    assert (c.text, c.input_tokens, c.output_tokens) == ("ok", 7, 1)
