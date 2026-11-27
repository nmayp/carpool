"""carpool MCP server — stdio JSON-RPC, stdlib only.

Tools:
  carpool_prompt   tasks -> ONE grouped prompt (the calling agent answers it in a single pass)
  carpool_verify   tasks + reply -> accepted answers, plus which tasks to redo and why
  carpool_run      tasks -> full pipeline through CARPOOL_BACKEND (e.g. claude-code, openai:gpt-x, ollama:qwen3)
  carpool_lookup   exact get/count over a .jsonl/.json/.csv file with a contract check — zero model calls

Register:  claude mcp add carpool -- carpool-mcp
"""
from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

from . import __version__
from .batch import build_batch_prompt, parse_batch
from .compact import preflight, verify
from .exact import ContractError, Index
from .types import Task

PROTOCOL = "2025-06-18"

TASKS_SCHEMA = {
    "type": "array",
    "description": "Independent tasks. Give every task and source a stable id (T01, T01-a).",
    "items": {
        "type": "object",
        "required": ["id", "question", "sources"],
        "properties": {
            "id": {"type": "string"},
            "question": {"type": "string"},
            "sources": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "text"],
                    "properties": {
                        "id": {"type": "string"},
                        "text": {"type": "string"},
                        "trusted": {"type": "boolean", "description": "false for drafts / superseded copies"},
                    },
                },
            },
        },
    },
}

TOOLS = [
    {
        "name": "carpool_prompt",
        "description": (
            "Use BEFORE spawning subagents or making one call per item. Turns 2+ independent questions "
            "(per file, per record, per ticket) into ONE grouped prompt with stable IDs, sending compact "
            "evidence only where one source clearly wins. Answer the returned prompt in a single pass, "
            "then call carpool_verify with your JSON reply."
        ),
        "inputSchema": {"type": "object", "required": ["tasks"],
                        "properties": {"tasks": TASKS_SCHEMA,
                                       "compact": {"type": "boolean", "default": True}}},
    },
    {
        "name": "carpool_verify",
        "description": (
            "Check a grouped reply. Returns accepted answers and the tasks to redo individually with full "
            "evidence (missing, duplicate, wrong-task source, not in source, truncated name...)."
        ),
        "inputSchema": {"type": "object", "required": ["tasks", "reply"],
                        "properties": {"tasks": TASKS_SCHEMA,
                                       "reply": {"description": "JSON array text or array of {id, answer, source}"},
                                       "compact": {"type": "boolean", "default": True}}},
    },
    {
        "name": "carpool_run",
        "description": "Run the full carpool pipeline (one grouped call, verify, individual fallback) on the backend in CARPOOL_BACKEND.",
        "inputSchema": {"type": "object", "required": ["tasks"],
                        "properties": {"tasks": TASKS_SCHEMA, "max_batch": {"type": "integer", "default": 24}}},
    },
    {
        "name": "carpool_lookup",
        "description": (
            "Exact operation with zero model calls: get a field by key, or count records matching fields, "
            "in a .jsonl/.json/.csv file. Fails loudly if keys repeat or required fields are missing. "
            "Use this instead of reading tables into context."
        ),
        "inputSchema": {"type": "object", "required": ["path", "key"],
                        "properties": {"path": {"type": "string"}, "key": {"type": "string"},
                                       "value": {"description": "key value to fetch"},
                                       "field": {"type": "string"},
                                       "count_where": {"type": "object", "description": "e.g. {\"status\": \"pending\"}"},
                                       "required": {"type": "array", "items": {"type": "string"}}}},
    },
]


def _tasks(args) -> list[Task]:
    return [Task.from_dict(t) for t in args["tasks"]]


def _items(tasks, compact):
    out, picked = [], {}
    for t in tasks:
        s = preflight(t) if compact else None
        if s:
            picked[t.id] = s
        out.append((t, [s] if s else t.sources))
    return out, picked


def tool_prompt(args):
    tasks = _tasks(args)
    items, picked = _items(tasks, args.get("compact", True))
    return {"prompt": build_batch_prompt(items), "compact_tasks": sorted(picked), "full_tasks": sorted(t.id for t in tasks if t.id not in picked)}


def tool_verify(args):
    tasks = _tasks(args)
    items, picked = _items(tasks, args.get("compact", True))
    reply = args["reply"]
    text = reply if isinstance(reply, str) else json.dumps(reply)
    ok, bad = parse_batch(text, items)
    accepted = []
    by_id = {t.id: t for t in tasks}
    for tid, (ans, src) in ok.items():
        cited = by_id[tid].source(src)
        if cited is not None and not cited.trusted:
            bad[tid] = "cites an untrusted (draft/superseded) source"
            continue
        why = verify(by_id[tid], ans, picked.get(tid) or cited) if cited is not None else "no cited source"
        if why:
            bad[tid] = f"verifier: {why}"
            continue
        accepted.append({"id": tid, "answer": ans, "source": src})
    return {"accepted": accepted, "redo_individually_with_full_evidence": [{"id": k, "reason": v} for k, v in sorted(bad.items())]}


def tool_run(args):
    from .backends import make_backend
    from .router import run_carpool

    spec = os.environ.get("CARPOOL_BACKEND")
    if not spec:
        raise ValueError("set CARPOOL_BACKEND (e.g. claude-code, openai:<model>, ollama:<model>) or use carpool_prompt + carpool_verify")
    res = run_carpool(_tasks(args), make_backend(spec), max_batch=int(args.get("max_batch", 24)))
    L = res.ledger
    return {"answers": [{"id": a.task_id, "answer": a.answer, "source": a.source_id, "route": a.route,
                         "fallback": a.fallback_reason} for a in res.answers.values()],
            "calls": L.n_calls, "total_tokens": L.total_tokens, "uncached_tokens": L.uncached_tokens,
            "estimated": L.estimated}


def load_records(path: str) -> list[dict]:
    p = Path(path).expanduser()
    if p.suffix == ".jsonl":
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    if p.suffix == ".csv":
        with p.open(newline="") as f:
            return list(csv.DictReader(f))
    d = json.loads(p.read_text())
    return d if isinstance(d, list) else next(v for v in d.values() if isinstance(v, list))


def tool_lookup(args):
    ix = Index(load_records(args["path"]), key=args["key"], required=tuple(args.get("required", ())))
    out = {"records": len(ix), "model_calls": 0}
    if args.get("count_where"):
        out["count"] = ix.count(**args["count_where"])
    if "value" in args:
        v = args["value"]
        try:
            out["result"] = ix.get(v, args.get("field"))
        except KeyError:
            # csv values are strings; retry with string form
            out["result"] = ix.get(str(v), args.get("field"))
    return out


HANDLERS = {"carpool_prompt": tool_prompt, "carpool_verify": tool_verify, "carpool_run": tool_run, "carpool_lookup": tool_lookup}


def handle(msg: dict):
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:  # notification
        return None
    if method == "initialize":
        result = {"protocolVersion": msg.get("params", {}).get("protocolVersion", PROTOCOL),
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "carpool", "version": __version__}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        p = msg.get("params", {})
        fn = HANDLERS.get(p.get("name"))
        if fn is None:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool {p.get('name')}"}}
        try:
            data = fn(p.get("arguments") or {})
            result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=1)}],
                      "structuredContent": data}
        except (ContractError, KeyError, ValueError, OSError, RuntimeError, StopIteration) as e:
            result = {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            out = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            out = handle(msg)
        if out is not None:
            sys.stdout.write(json.dumps(out) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
