import json
import subprocess
import sys

from carpool.agents import (parse_claude_code, parse_codex_events, parse_gemini_api,
                            parse_gemini_cli, parse_ollama, parse_openai)
from carpool.mcp_server import handle

TASKS = [
    {"id": "T01", "question": "Which team maintains the pump?",
     "sources": [{"id": "T01-a", "text": "The pump is maintained by the Design Assurance Team."},
                 {"id": "T01-b", "text": "Parking moved to the east lot."}]},
    {"id": "T02", "question": "Where was the prize first shown?",
     "sources": [{"id": "T02-a", "text": "The prize was first shown at the London Exhibition in 1851."},
                 {"id": "T02-b", "text": "Tickets cost two shillings."}]},
]


def call(name, args):
    r = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    return r["result"]


def test_mcp_lists_tools():
    r = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert {t["name"] for t in r["result"]["tools"]} == {"carpool_prompt", "carpool_verify", "carpool_run", "carpool_lookup"}


def test_mcp_prompt_then_verify():
    p = call("carpool_prompt", {"tasks": TASKS})["structuredContent"]
    assert "### TASK T02" in p["prompt"] and p["compact_tasks"] == ["T01", "T02"]
    reply = [{"id": "T01", "answer": "Design Assurance Team", "source": "T01-a"},
             {"id": "T02", "answer": "London", "source": "T02-a"}]
    v = call("carpool_verify", {"tasks": TASKS, "reply": reply})["structuredContent"]
    assert [a["id"] for a in v["accepted"]] == ["T01"]
    assert v["redo_individually_with_full_evidence"][0]["id"] == "T02"


def test_mcp_lookup(tmp_path):
    f = tmp_path / "orders.csv"
    f.write_text("order_id,status\nW1,shipped\nW2,pending\nW3,pending\n")
    r = call("carpool_lookup", {"path": str(f), "key": "order_id", "value": "W2", "field": "status",
                                "count_where": {"status": "pending"}})["structuredContent"]
    assert r == {"records": 3, "model_calls": 0, "count": 2, "result": "pending"}
    f.write_text("order_id,status\nW1,a\nW1,b\n")
    assert call("carpool_lookup", {"path": str(f), "key": "order_id"})["isError"]


def test_mcp_stdio_roundtrip():
    msgs = [{"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}]
    p = subprocess.run([sys.executable, "-m", "carpool.mcp_server"], input="\n".join(map(json.dumps, msgs)) + "\n",
                       capture_output=True, text=True, timeout=30)
    lines = [json.loads(l) for l in p.stdout.splitlines()]
    assert len(lines) == 2 and lines[0]["result"]["serverInfo"]["name"] == "carpool"


# --- usage parsers -----------------------------------------------------------

def test_parse_claude_code():
    c = parse_claude_code(json.dumps({"result": "ok", "total_cost_usd": 0.04, "usage": {
        "input_tokens": 2, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 900, "output_tokens": 4}}))
    assert (c.text, c.input_tokens, c.cached_input_tokens, c.output_tokens, c.cost_usd) == ("ok", 1002, 900, 4, 0.04)


def test_parse_codex():
    out = "\n".join(json.dumps(e) for e in [
        {"type": "thread.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "[]"}},
        {"type": "turn.completed", "usage": {"input_tokens": 16071, "cached_input_tokens": 13952, "output_tokens": 8}}])
    c = parse_codex_events(out)
    assert (c.text, c.input_tokens, c.cached_input_tokens, c.output_tokens) == ("[]", 16071, 13952, 8)


def test_parse_gemini_cli():
    c = parse_gemini_cli(json.dumps({"response": "hi", "stats": {"models": {"g": {"tokens": {
        "prompt": 50, "cached": 10, "candidates": 3, "thoughts": 2}}}}}))
    assert (c.input_tokens, c.cached_input_tokens, c.output_tokens) == (50, 10, 5)


def test_parse_http_apis():
    o = parse_openai({"choices": [{"message": {"content": "x"}}],
                      "usage": {"prompt_tokens": 10, "completion_tokens": 2, "prompt_tokens_details": {"cached_tokens": 4}}})
    assert (o.input_tokens, o.cached_input_tokens, o.output_tokens) == (10, 4, 2)
    g = parse_gemini_api({"candidates": [{"content": {"parts": [{"text": "a"}, {"text": "think", "thought": True}]}}],
                          "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 1, "thoughtsTokenCount": 3}})
    assert (g.text, g.input_tokens, g.output_tokens) == ("a", 7, 4)
    l = parse_ollama({"response": "z", "prompt_eval_count": 9, "eval_count": 1})
    assert (l.input_tokens, l.output_tokens) == (9, 1)
