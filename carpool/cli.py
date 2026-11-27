"""carpool CLI.

  carpool prompt tasks.jsonl            # print ONE batched prompt (paste into any chat app)
  carpool parse  tasks.jsonl reply.txt  # validate the pasted reply, list what needs a retry
  carpool run    tasks.jsonl --backend anthropic | cmd:"claude -p"
  carpool bench  [tasks.jsonl]          # offline: 24 calls vs 1 carpool, mock model
  carpool stats  results/run.json       # summarise a saved benchmark (bench/real.py output)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .backends import MockBackend, make_backend
from .batch import build_batch_prompt, parse_batch
from .compact import preflight
from .ledger import pct_change, regressions, score
from .router import run_carpool, run_individual
from .types import Task

FIXTURE = Path(__file__).parent / "data" / "ops24.jsonl"


def load_tasks(path: str | None) -> list[Task]:
    p = Path(path) if path else FIXTURE
    return [Task.from_dict(json.loads(l)) for l in p.read_text().splitlines() if l.strip()]


def _items(tasks, compact):
    out = []
    for t in tasks:
        s = preflight(t) if compact else None
        out.append((t, [s] if s else t.sources))
    return out


def cmd_prompt(a):
    print(build_batch_prompt(_items(load_tasks(a.tasks), not a.full)), end="")


def cmd_parse(a):
    tasks = load_tasks(a.tasks)
    ok, bad = parse_batch(Path(a.reply).read_text(), _items(tasks, not a.full))
    for tid, (ans, src) in ok.items():
        print(json.dumps({"id": tid, "answer": ans, "source": src}))
    for tid, why in bad.items():
        print(f"RETRY {tid}: {why}", file=sys.stderr)
    return 1 if bad else 0


def _table(rows):
    cols = list(rows[0])
    w = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols}
    line = "  ".join(c.ljust(w[c]) for c in cols)
    print(line + "\n" + "-" * len(line))
    for r in rows:
        print("  ".join(str(r[c]).ljust(w[c]) for c in cols))


def cmd_stats(a):
    d = json.loads(Path(a.results).read_text())
    runs = d["runs"]
    base = runs[0]

    def acc(r):
        return int(str(r["accepted"]).split("/")[0]) or 1

    rows = []
    for r in runs:
        row = {"route": r["route"], "calls": r["calls"], "accepted": r["accepted"],
               "total_tokens": f"{r['total_tokens']:,}", "usd": f"{r.get('usd', 0):.3f}"}
        if r is base:
            row["Δ usd/acc"] = row["Δ tokens/acc"] = "—"
            row["regressions"] = "—"
        else:
            bu = base.get("usd") or 0
            row["Δ usd/acc"] = f"{pct_change(bu / acc(base), r.get('usd', 0) / acc(r)):+.1f}%" if bu else "n/a"
            row["Δ tokens/acc"] = f"{pct_change(base['total_tokens'] / acc(base), r['total_tokens'] / acc(r)):+.1f}%"
            row["regressions"] = len(r.get("regressions") or [])
        rows.append(row)
    print(f"{d.get('tasks')} · {d.get('n')} tasks · backend {d.get('backend')}\n")
    _table(rows)
    spent = sum(r.get("usd") or 0 for r in runs)
    if spent:
        print(f"\ntotal spend in this file: ${spent:.2f} (list price)")


def cmd_run(a):
    tasks = load_tasks(a.tasks)
    if a.adaptive:
        from .sizing import run_adaptive

        res, sizes = run_adaptive(tasks, make_backend(a.backend), start=min(8, a.max_batch),
                                  max_size=a.max_batch, compact=not a.full)
        print(f"group sizes used: {sizes}", file=sys.stderr)
    else:
        res = run_carpool(tasks, make_backend(a.backend), compact=not a.full, max_batch=a.max_batch)
    for t in tasks:
        ans = res.answers[t.id]
        print(json.dumps({"id": t.id, "answer": ans.answer, "source": ans.source_id,
                          "route": ans.route, "fallback": ans.fallback_reason}))
    L = res.ledger
    est = " (estimated)" if L.estimated else ""
    print(f"\n{len(tasks)} tasks, {L.n_calls} calls, {L.total_tokens:,} tokens{est}", file=sys.stderr)


def cmd_bench(a):
    tasks = load_tasks(a.tasks)
    if a.backend == "mock":
        gold = {t.id: t.answers[0] for t in tasks}
        backend = MockBackend(gold, overhead=a.overhead)
    else:
        backend = make_backend(a.backend)
    base = run_individual(tasks, backend)
    pool = run_carpool(tasks, backend, compact=not a.full, max_batch=a.max_batch)
    r0 = score("one call per task", tasks, base.answers, base.ledger)
    r1 = score("carpool", tasks, pool.answers, pool.ledger)
    _table([r0.row(), r1.row()])
    regs = regressions(tasks, base.answers, pool.answers)
    d_tot = pct_change(r0.per_accepted(base.ledger.total_tokens), r1.per_accepted(pool.ledger.total_tokens))
    d_unc = pct_change(r0.per_accepted(base.ledger.uncached_tokens), r1.per_accepted(pool.ledger.uncached_tokens))
    routes = {}
    for ans in pool.answers.values():
        routes[ans.route] = routes.get(ans.route, 0) + 1
    print(f"\ntokens per accepted answer: {d_tot:+.1f}%   uncached+output per accepted: {d_unc:+.1f}%")
    print(f"paired regressions: {len(regs)} {regs if regs else ''}")
    print(f"carpool routes: {routes}   fallbacks: {pool.fallbacks or 'none'}")
    if base.ledger.estimated:
        print("note: token counts are estimated (chars/4 + per-call overhead). Run with a real backend for real numbers.")
    if a.json:
        Path(a.json).write_text(json.dumps({"baseline": r0.row(), "carpool": r1.row(),
                                            "regressions": regs}, indent=2))


def main(argv=None):
    p = argparse.ArgumentParser(prog="carpool", description="Many agent tasks, one ride.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("prompt", help="print one batched prompt for a tasks file")
    s.add_argument("tasks", nargs="?")
    s.add_argument("--full", action="store_true", help="disable compact evidence")
    s.set_defaults(fn=cmd_prompt)

    s = sub.add_parser("parse", help="validate a reply to `carpool prompt`")
    s.add_argument("tasks")
    s.add_argument("reply")
    s.add_argument("--full", action="store_true")
    s.set_defaults(fn=cmd_parse)

    s = sub.add_parser("run", help="run tasks through a backend")
    s.add_argument("tasks")
    s.add_argument("--backend", default="anthropic",
                   help='anthropic | anthropic-batch (50%% price, async) | claude-code | codex | ollama:<model> | cmd:"..."')
    s.add_argument("--max-batch", type=int, default=24)
    s.add_argument("--adaptive", action="store_true", help="learn the group size from observed redo rates")
    s.add_argument("--full", action="store_true")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("stats", help="summarise a saved benchmark results file")
    s.add_argument("results")
    s.set_defaults(fn=cmd_stats)

    s = sub.add_parser("bench", help="baseline vs carpool, paired")
    s.add_argument("tasks", nargs="?")
    s.add_argument("--backend", default="mock")
    s.add_argument("--overhead", type=int, default=16_000, help="mock per-call harness tokens")
    s.add_argument("--max-batch", type=int, default=24)
    s.add_argument("--full", action="store_true")
    s.add_argument("--json")
    s.set_defaults(fn=cmd_bench)

    a = p.parse_args(argv)
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
