# Contributing to carpool

Thanks for helping. The most useful contributions right now are measurements, so most of this page is about
those.

## Share a benchmark result

carpool's claims are only as good as the runs behind them, and so far they cover Claude Code and one small local
model. Results from other clients and models are the most valuable thing you can send.

1. Install from a clone: `pip install -e ".[dev]"`.
2. Run a backend you have access to, for example:

   ```bash
   python bench/real.py --backend codex --tasks bench/data/squad48-holdout.jsonl --repeats 2
   ```

   Other backends: `gemini-cli`, `ollama:<model>`, `openai:<model>` (any OpenAI-compatible endpoint, set
   `OPENAI_BASE_URL`), `anthropic`.
3. Open a pull request that adds the JSON file under `results/`, and fill in the "Benchmark result" issue form or
   the PR template. Bad results are as welcome as good ones; `docs/RESULTS.md` reports failures too.

## Report a bug or a wrong answer

Use the issue forms. For a wrong answer that carpool accepted, include the task (question, sources, IDs) and the
model's reply: those cases improve the checker.

## Change code

- Python 3.10+. Keep the core library, MCP server and hooks on the standard library. Setup uses
  `tomli` on Python 3.10, and share-card rendering uses the optional `card` extra.
- Run `pytest -q` before opening a PR. CI runs it on Python 3.10, 3.12 and 3.14.
- A change to the checker or the prompts must say how it was evaluated. If you tuned it on a task set, test it on
  a different one (`bench/make_squad.py` and `bench/make_fixtures.py` can generate fresh, disjoint sets).
- Keep claims in the docs tied to a file in `results/`.
