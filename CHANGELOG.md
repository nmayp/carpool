# Changelog

## Unreleased

- README: a Related work section crediting batch prompting and explaining what carpool adds.
- Fair baseline: one lean call per task (`--backend claude-lean`). carpool still cut cost per accepted answer by
  78–83% on the first run, with 0 regressions; the README now leads with that figure.
- README, figures, demo and social card restyled: Latin Modern type, black, white and one blue.
- Prompt-time hook (`UserPromptSubmit`): when a request covers several independent items and doesn't ask for
  subagents, adds one note suggesting a single pass. Explicit requests for subagents are left alone.
- Experiments on whether the hooks change agent behaviour (`docs/RESULTS.md` §8–§9).
- README: badges, animated demo, how-it-works diagram, results charts.
- Community files: contributing guide, code of conduct, security policy, issue forms, PR template, citation file.

## 0.2.0 — 27 Nov 2026

- `carpool run --adaptive`: learns how much crowding hurts from observed retries and sizes groups with
  b* = √(H/(φ(H+E))).
- `--backend anthropic-batch`: Anthropic Message Batches API at 50% of the price, for work that isn't urgent.
- `carpool stats`: summary table of a saved benchmark run.
- Launch-time hook (`PreToolUse`): notes subagent fan-out at the 3rd launch.
- Stricter checks: a grouped answer that cites a draft source, or doesn't appear in the source it cites, is redone.
- Ollama backend sets a 32k context window; results on Qwen2.5-1.5B published, failures included.

## 0.1.0 — 26–27 Nov 2026

- First release: grouped requests with stable IDs, preflight for compact evidence, per-answer checks and
  individual retries, exact lookups as code.
- Backends that report real usage: Claude Code, Codex, Gemini CLI, Anthropic, OpenAI-compatible, Gemini API,
  Ollama, plus any CLI via `cmd:`.
- MCP server (`carpool-mcp`) with prompt, verify, run and lookup tools.
- Measured on Claude Code: 93–96% lower cost per accepted answer than one agent call per task, with 0 regressions on two task sets.
