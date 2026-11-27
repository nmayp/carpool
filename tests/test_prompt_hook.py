import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks"))
sys.path.insert(0, str(ROOT / "bench"))

from prompt_hint import decide  # noqa: E402
from e2e_hook import TASK, TASK_EXPLICIT  # noqa: E402


@pytest.mark.parametrize("prompt", [
    "Delegate each module to its own subagent and collect the findings.",
    "Use one subagent per file in src/ to review all the files.",
    "Spawn a subagent for each of the 12 tickets.",
    "Review all 8 services in parallel.",
    "Use the Agent tool for every repo in the list.",
    TASK_EXPLICIT,
])
def test_requests_for_subagents_are_respected(prompt):
    assert decide(prompt) == ("request", None)


@pytest.mark.parametrize("prompt", [
    "Review each of the 10 modules in ./src. You may use subagents if that helps.",
    "Summarise all 20 papers. Feel free to use subagents.",
    TASK,
])
def test_permission_only_gets_a_note(prompt):
    decision, note = decide(prompt)
    assert decision == "permission"
    assert "did not require" in note


@pytest.mark.parametrize("prompt", [
    "Review each of the 10 modules in ./src for bugs.",
    "Triage every ticket in tickets/ and tag its product area.",
    "Check these:\n- a.py\n- b.py\n- c.py\n- d.py",
])
def test_independent_items_get_a_note(prompt):
    decision, note = decide(prompt)
    assert decision == "independent" and note.startswith("carpool:")


@pytest.mark.parametrize("prompt", [
    "Fix the failing test in test_api.py.",
    "What does this function do?",
    "Refactor the payment module to use the new client.",
    "Read 2 files and compare them.",
])
def test_single_or_small_tasks_are_left_alone(prompt):
    assert decide(prompt) == ("none", None)


def test_count_is_mentioned():
    _, note = decide("Review each of the 10 modules in ./src for bugs.")
    assert "(10)" in note


def run(event, tmp_path):
    env = {**os.environ, "CLAUDE_PLUGIN_DATA": str(tmp_path)}
    p = subprocess.run([sys.executable, str(ROOT / "hooks" / "prompt_hint.py")], input=json.dumps(event),
                       capture_output=True, text=True, env=env, timeout=10)
    assert p.returncode == 0
    return p.stdout.strip()


def test_hook_output_shape_and_log(tmp_path):
    out = json.loads(run({"session_id": "s", "prompt": "Review each of the 10 modules in ./src."}, tmp_path))
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "UserPromptSubmit" and "additionalContext" in hso
    assert "decision" not in out  # never blocks the prompt
    assert run({"session_id": "s", "prompt": "Delegate each module to a subagent."}, tmp_path) == ""
    log = [json.loads(l) for l in (tmp_path / "prompt-hint.log").read_text().splitlines()]
    assert [r["decision"] for r in log] == ["independent", "request"]


def test_hooks_json_registers_both_events():
    cfg = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]
    assert "prompt_hint.py" in cfg["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert "Agent" in cfg["PreToolUse"][0]["matcher"]


def test_subagent_prompts_are_skipped(tmp_path):
    ev = {"session_id": "s", "agent_id": "a1", "prompt": "Review each of the 10 modules in ./src."}
    assert run(ev, tmp_path) == ""
    log = [json.loads(l) for l in (tmp_path / "prompt-hint.log").read_text().splitlines()]
    assert log[-1]["decision"] == "subagent"
