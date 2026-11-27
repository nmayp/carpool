"""Grouped requests: many independent tasks, one native call.

Every task and every source carries a stable ID. The model must return one
structured answer per task with the ID of the source it used. Missing,
duplicate, invalid-source and rejected answers fall back individually.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from .types import Source, Task

BATCH_INSTRUCTIONS = """You will answer {n} independent tasks.
Rules:
- Answer each task ONLY from the sources listed under that task. Never use another task's sources.
- Give the shortest exact answer (a name, number, date or short phrase), copied verbatim from the source. No explanations, no parentheses.
- Cite the ID of the one source that supports the answer.
- If sources disagree, prefer approved/current sources over drafts or superseded ones.
Return ONLY a JSON array, one object per task, no prose:
[{{"id": "<task id>", "answer": "<answer>", "source": "<source id>"}}]
"""

SINGLE_INSTRUCTIONS = """Answer the task ONLY from the sources below.
Give the shortest exact answer, copied verbatim from the source (no explanations, no parentheses), and cite the ID of the one source that supports it.
If sources disagree, prefer approved/current sources over drafts or superseded ones.
Return ONLY a JSON object, no prose: {"answer": "<answer>", "source": "<source id>"}
"""


def _render_task(task: Task, sources: list[Source]) -> str:
    lines = [f"### TASK {task.id}", f"Question: {task.question}", "Sources:"]
    for s in sources:
        lines.append(f"[{s.id}] {s.text}")
    return "\n".join(lines)


def build_batch_prompt(items: list[tuple[Task, list[Source]]]) -> str:
    body = "\n\n".join(_render_task(t, srcs) for t, srcs in items)
    return BATCH_INSTRUCTIONS.format(n=len(items)) + "\n" + body + "\n"


def build_single_prompt(task: Task) -> str:
    return SINGLE_INSTRUCTIONS + "\n" + _render_task(task, task.sources) + "\n"


_PAREN_TAIL = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]\s*$")


def clean_answer(ans: str) -> str:
    """Drop a trailing parenthetical the model added as explanation."""
    prev = None
    while prev != ans:
        prev, ans = ans, _PAREN_TAIL.sub("", ans).strip()
    return ans


def _extract_json(text: str, open_ch: str, close_ch: str):
    text = re.sub(r"```(?:json)?", "", text)
    start, end = text.find(open_ch), text.rfind(close_ch)
    if start == -1 or end <= start:
        raise ValueError("no JSON found")
    return json.loads(text[start: end + 1])


def parse_batch(text: str, items: list[tuple[Task, list[Source]]]):
    """Return ({task_id: (answer, source_id)}, {task_id: failure_reason})."""
    allowed = {t.id: {s.id for s in srcs} for t, srcs in items}
    ok: dict[str, tuple[str, str]] = {}
    bad: dict[str, str] = {}
    try:
        rows = _extract_json(text, "[", "]")
        if not isinstance(rows, list):
            raise ValueError("not a list")
    except (ValueError, json.JSONDecodeError):
        return {}, {tid: "unparseable batch response" for tid in allowed}

    seen: dict[str, int] = {}
    for row in rows:
        if isinstance(row, dict) and "id" in row:
            seen[str(row["id"])] = seen.get(str(row["id"]), 0) + 1
    for row in rows:
        if not isinstance(row, dict):
            continue
        tid = str(row.get("id", ""))
        if tid not in allowed or tid in bad or tid in ok:
            continue
        if seen.get(tid, 0) > 1:
            bad[tid] = "duplicate answer"
            continue
        ans, src = clean_answer(str(row.get("answer", "")).strip()), str(row.get("source", "")).strip()
        if not ans:
            bad[tid] = "empty answer"
        elif src not in allowed[tid]:
            # covers hallucinated IDs and cross-task evidence leakage
            bad[tid] = f"invalid source '{src}'"
        else:
            ok[tid] = (ans, src)
    for tid in allowed:
        if tid not in ok and tid not in bad:
            bad[tid] = "missing answer"
    return ok, bad


def parse_single(text: str, task: Task) -> tuple[Optional[tuple[str, str]], Optional[str]]:
    try:
        obj = _extract_json(text, "{", "}")
    except (ValueError, json.JSONDecodeError):
        return None, "unparseable response"
    ans, src = clean_answer(str(obj.get("answer", "")).strip()), str(obj.get("source", "")).strip()
    if not ans:
        return None, "empty answer"
    if task.source(src) is None:
        return None, f"invalid source '{src}'"
    return (ans, src), None
