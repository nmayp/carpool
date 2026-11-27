#!/usr/bin/env python3
"""PreToolUse hook: notice subagent fan-out and suggest carpooling.

Counts subagent launches per session. When THRESHOLD launches happen within
WINDOW seconds, it adds a short note to the model's context (and shows one
line to the user). It never blocks or changes the permission decision, and it
fires at most once per COOLDOWN seconds per session.
"""
import json
import os
import sys
import tempfile
import time

THRESHOLD = int(os.environ.get("CARPOOL_FANOUT_THRESHOLD", "3"))
WINDOW = float(os.environ.get("CARPOOL_FANOUT_WINDOW", "180"))
COOLDOWN = float(os.environ.get("CARPOOL_FANOUT_COOLDOWN", "900"))

NOTE = (
    "carpool: {n} subagents launched in the last {w} s. Each one pays the full agent context again "
    "(often 20k+ tokens) before doing its task. If these are independent, short questions (per file, "
    "per record, per ticket), answer them in ONE pass instead: give each item an ID, return a JSON "
    "array of {{id, answer, source}}, check each answer against its own evidence, and redo only the "
    "ones that fail. Keep subagents for long, open-ended or dependent work."
)


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if event.get("tool_name") not in ("Agent", "Task"):
        return 0
    session = "".join(c for c in str(event.get("session_id", "default")) if c.isalnum() or c in "-_")[:80]
    base = os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.join(tempfile.gettempdir(), "carpool-hook")
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, f"fanout-{session}.json")
    now = time.time()
    try:
        with open(path) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {"times": [], "last_hint": 0}
    state["times"] = [t for t in state.get("times", []) if now - t <= WINDOW] + [now]
    hint = len(state["times"]) >= THRESHOLD and now - state.get("last_hint", 0) >= COOLDOWN
    if hint:
        state["last_hint"] = now
    with open(path, "w") as f:
        json.dump(state, f)
    if hint:
        n = len(state["times"])
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": NOTE.format(n=n, w=int(WINDOW)),
            },
            "systemMessage": f"carpool: {n} subagents in a few minutes. Suggested one shared pass instead.",
        }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
