#!/usr/bin/env python3
"""UserPromptSubmit hook: advise one shared pass BEFORE the agent decides to fan out.

Runs when the user's message arrives, before any tool call. If the message describes
several independent items and does not ask for subagents, it adds one short note to
the model's context. It never blocks anything.

The user wins when in doubt:
  * a request for subagents ("delegate each module", "one subagent per file",
    "use the Agent tool", "spawn ...") -> silent;
  * subagents mentioned only with permissive wording ("you may use subagents
    if that helps", "feel free to use subagents") -> treated as permission, note added;
  * any other mention of subagents/parallel work -> treated as a request, silent.

Every decision is logged to $CLAUDE_PLUGIN_DATA/prompt-hint.log for auditing.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time

SUBAGENT = re.compile(r"\b(sub-?agents?|agent tool|delegat\w*|spawn\w*|fan[- ]?out|in parallel|paralleli[sz]\w*)\b", re.I)
PERMISSIVE = re.compile(
    r"\b(you may|you can|may use|can use|feel free|if (that|it) helps|if useful|if needed|optionally|are allowed to|"
    r"if you (want|like|wish))\b", re.I)
PLURAL_ITEMS = (r"(files?|modules?|tickets?|records?|items?|questions?|documents?|docs|pages?|repos?|repositories|"
                r"functions?|tests?|classes|components?|endpoints?|issues?|rows?|entries|urls?|links?|papers?|"
                r"articles?|emails?|reviews?|tasks?|products?|customers?|orders?|services?|packages?|pull requests?|prs?)")
EACH = re.compile(rf"\b(each|every|all( the| of the)?|per)\s+(\w+\s+){{0,3}}{PLURAL_ITEMS}\b", re.I)
COUNT = re.compile(rf"\b(\d{{1,4}})\s+(\w+\s+){{0,2}}{PLURAL_ITEMS}\b", re.I)
BULLETS = re.compile(r"^\s*([-*•]|\d+[.)])\s+\S", re.M)

NOTE = (
    "carpool: this request covers several independent items{count}. Launching one subagent per item makes each "
    "pay the full agent context again (in a measured code-review task, 4-6x the cost for the same result). "
    "Unless an item needs a long, open-ended or dependent investigation, handle them in one pass yourself: "
    "read them together, give each an ID, answer each from its own evidence, check each answer, and redo only "
    "the ones that fail.{permission}"
)


def decide(prompt: str) -> tuple[str, str | None]:
    """Return (decision, note). decision is one of: request, permission, independent, none."""
    count = None
    m = COUNT.search(prompt)
    if m and int(m.group(1)) >= 3:
        count = int(m.group(1))
    many = bool(EACH.search(prompt)) or count is not None or len(BULLETS.findall(prompt)) >= 3
    if not many:
        return "none", None
    mentions = SUBAGENT.search(prompt)
    if mentions:
        # look for permissive wording near the mention (same sentence)
        start = max(prompt.rfind(".", 0, mentions.start()), prompt.rfind("\n", 0, mentions.start())) + 1
        end_candidates = [i for i in (prompt.find(".", mentions.end()), prompt.find("\n", mentions.end())) if i != -1]
        sentence = prompt[start: min(end_candidates) if end_candidates else len(prompt)]
        if not PERMISSIVE.search(sentence):
            return "request", None
        perm = " The user allowed subagents but did not require them."
        return "permission", NOTE.format(count=f" ({count})" if count else "", permission=perm)
    return "independent", NOTE.format(count=f" ({count})" if count else "", permission="")


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    prompt = str(event.get("prompt", ""))
    if event.get("agent_id"):
        # The prompt a subagent receives: the fan-out decision was already made upstream.
        decision, note = "subagent", None
    else:
        decision, note = decide(prompt)
    base = os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.join(tempfile.gettempdir(), "carpool-hook")
    try:
        os.makedirs(base, exist_ok=True)
        with open(os.path.join(base, "prompt-hint.log"), "a") as f:
            f.write(json.dumps({"t": time.time(), "session": event.get("session_id"), "decision": decision}) + "\n")
    except OSError:
        pass
    if note:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": note}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
