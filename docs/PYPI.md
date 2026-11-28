# carpool

**Why make 24 trips when one carpool do trick.**

Many independent LLM tasks, one verified request: about 80% cheaper per accepted answer than one lean call per
task, 93–96% cheaper than one Claude Code agent call per task, with 0 regressions (measured on Claude).

![carpool stats on real Claude Code results](https://raw.githubusercontent.com/nmayp/carpool/main/docs/assets/demo.svg)

If your script, pipeline or agent makes a separate model call for every item, each call pays for the whole
harness again: system prompt, tool schemas, instructions. In Claude Code that's about 26,000 input tokens before
your task starts. carpool puts independent tasks in one request, checks every answer against the source it cites,
and redoes only the ones that fail.

```bash
pip install carpool-agents
carpool bench          # offline demo with a mock model, no API key
```

- **Library and CLI** with backends that report real usage: Claude Code, Codex, Gemini CLI,
  Anthropic (including the Message Batches API), any OpenAI-compatible endpoint, Gemini API, Ollama.
- **MCP server** (`carpool-mcp`) for Claude Code, Cursor, Windsurf, Cline, Codex, Gemini CLI and Claude Desktop.
- **Claude Code plugin** with a skill, a `/carpool` command and hooks.

| set | one call per task | carpool | cost per accepted answer | regressions |
|---|---|---|---|---|
| 24 ops tasks, 3 repeats | 24 calls · 24/24 · $1.159 | 1 call · 24/24 · $0.052–0.065 | −94% to −96% | 0 |
| 48 SQuAD questions, 2 repeats | 48 calls · 40/48 · $2.449 | 2 calls · 44–45/48 · $0.106–0.178 | −93% to −96% | 0 |

On a small local model the savings were small and grouping cost some quality. Every result, failures included,
is in [RESULTS.md](https://github.com/nmayp/carpool/blob/main/docs/RESULTS.md). Documentation and install options are on
[GitHub](https://github.com/nmayp/carpool).
