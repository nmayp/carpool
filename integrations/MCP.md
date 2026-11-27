# carpool as an MCP server

One server works in every MCP client. It uses only the standard library and talks over stdio.

```bash
pip install carpool-agents
```

| tool | what it does | model calls |
|---|---|---|
| `carpool_prompt` | turns N independent questions into one grouped prompt with stable IDs, using compact evidence where one source clearly wins | 0 (your agent answers the prompt in a single pass) |
| `carpool_verify` | checks the grouped reply and lists which tasks to redo individually, and why | 0 |
| `carpool_run` | full pipeline through `CARPOOL_BACKEND` (`claude-code`, `codex`, `openai:<model>`, `gemini:<model>`, `ollama:<model>`) | 1 + retries |
| `carpool_lookup` | exact get/count over `.jsonl` / `.json` / `.csv`, with a contract check (unique keys, required fields) | 0 |

## Claude Code

```bash
claude mcp add carpool -- carpool-mcp
```

## Cursor / Windsurf / Cline / Claude Desktop

Add to the client's MCP config (`~/.cursor/mcp.json`, `claude_desktop_config.json`, …):

```json
{
  "mcpServers": {
    "carpool": { "command": "carpool-mcp" }
  }
}
```

## Codex CLI

`~/.codex/config.toml`:

```toml
[mcp_servers.carpool]
command = "carpool-mcp"
```

## Gemini CLI

`~/.gemini/settings.json`:

```json
{ "mcpServers": { "carpool": { "command": "carpool-mcp" } } }
```

To let `carpool_run` call a model itself, add an env entry, e.g. `"env": {"CARPOOL_BACKEND": "ollama:qwen3"}`.
Without one, use `carpool_prompt`, answer the prompt, then `carpool_verify`. That needs no keys, because the agent you're already talking to answers the prompt.

Tested live with Claude Code: `carpool_lookup` answered a status lookup and a count from a CSV without reading the file into context. The other client configs follow each client's documented MCP format but have not been run here.
