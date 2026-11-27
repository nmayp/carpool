"""carpool CLI.

  carpool prompt tasks.jsonl            # print ONE batched prompt (paste into any chat app)
  carpool parse  tasks.jsonl reply.txt  # validate the pasted reply, list what needs a retry
  carpool run    tasks.jsonl --backend anthropic | cmd:"claude -p"
  carpool bench  [tasks.jsonl]          # offline: 24 calls vs 1 carpool, mock model
  carpool stats  results/run.json       # summarise a saved benchmark (bench/real.py output)
  carpool compile calls.jsonl --task "..." --out compiled/solve.py   # logged model calls -> a plain function
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


def _compiled(a):
    if not getattr(a, "compiled", None):
        return None
    from .compile import CompiledTasks

    try:
        return CompiledTasks.from_file(a.compiled, a.compiled_fn)
    except (OSError, ValueError) as e:
        sys.exit(f"carpool: --compiled: {e}")


def cmd_run(a):
    tasks = load_tasks(a.tasks)
    compiled = _compiled(a)
    if a.adaptive:
        from .sizing import run_adaptive

        res, sizes = run_adaptive(tasks, make_backend(a.backend), start=min(8, a.max_batch),
                                  max_size=a.max_batch, compact=not a.full, compiled=compiled)
        print(f"group sizes used: {sizes}", file=sys.stderr)
    else:
        res = run_carpool(tasks, make_backend(a.backend), compact=not a.full, max_batch=a.max_batch,
                          compiled=compiled)
    for t in tasks:
        ans = res.answers[t.id]
        print(json.dumps({"id": t.id, "answer": ans.answer, "source": ans.source_id,
                          "route": ans.route, "fallback": ans.fallback_reason}))
    L = res.ledger
    est = " (estimated)" if L.estimated else ""
    n_code = sum(1 for x in res.answers.values() if x.route == "compiled")
    code = f" ({n_code} answered by compiled code)" if compiled is not None else ""
    print(f"\n{len(tasks)} tasks{code}, {L.n_calls} calls, {L.total_tokens:,} tokens{est}", file=sys.stderr)


def _load_pairs(log, answers, a):
    """Logged (input, output) pairs from a JSONL log, or from a tasks file plus the answers `carpool run` printed."""
    pairs = []
    if answers:
        from .compile import task_input

        given = {}
        for line in Path(answers).read_text().splitlines():
            if line.strip():
                d = json.loads(line)
                if d.get("id") is not None and str(d.get("answer", "")).strip():
                    given[str(d["id"])] = d["answer"]
        pairs = [(task_input(t, 2), given[t.id]) for t in load_tasks(log) if t.id in given]
        print(f"{len(pairs)} of the tasks in {log} have a logged answer", file=sys.stderr)
    else:
        for n, line in enumerate(Path(log).read_text().splitlines(), 1):
            if line.strip():
                d = json.loads(line)
                if a.input_key not in d or a.output_key not in d:
                    sys.exit(f"{log}:{n}: each line needs \"{a.input_key}\" and \"{a.output_key}\" keys")
                pairs.append((d[a.input_key], d[a.output_key]))
    return pairs


def cmd_compile(a):
    from .compile import Gate, compile_log, render

    match = a.match or ("loose" if a.answers else "exact")
    pairs = _load_pairs(a.log, a.answers, a)
    held = None
    if a.held_out:
        held = _load_pairs(a.held_out, a.held_out_answers if a.answers else None, a)
    context = Path(a.context_file).read_text() if a.context_file else ""
    if a.answers and not context:
        from .compile import TASK_CONTEXT

        context = TASK_CONTEXT
    gate = Gate(a.min_precision, a.min_lower_bound, a.min_coverage)
    try:
        r = compile_log(pairs, make_backend(a.backend), a.task, fn=a.func, context=context,
                        val_frac=a.val, seed=a.seed, gate=gate, match=match, held_out=held)
    except ValueError as e:
        sys.exit(f"carpool compile: {e}")
    p = "n/a" if r.precision is None else f"{r.precision:.1%}"
    print(f"learned from {r.n_train} logged calls, checked on {r.n_held_out} it had not seen ({r.split}; {r.match} match)")
    print(f"answers {r.coverage:.0%} of calls; {r.agreed}/{r.answered} match the logged output "
          f"(precision {p}, 95% lower bound {r.lower_bound:.1%})")
    print(f"cost to compile: ${r.cost_usd:.3f} in {r.calls} calls" if r.cost_usd else f"{r.calls} model calls to compile")
    if a.call_cost and r.coverage > 0:
        print(f"break-even: about {r.cost_usd / (a.call_cost * r.coverage):,.0f} calls at ${a.call_cost}/call")
    print(("PROMOTED: " if r.promoted else "NOT PROMOTED: ") + r.reason)
    if r.promoted:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render(r))
        print(f"wrote {out}. Read it before you deploy it: it runs as you. It returns None when unsure; "
              f"send those inputs to the model (carpool.compile.Cascade does this).")
    if a.json:
        Path(a.json).write_text(json.dumps(r.report(), indent=2))
    return 0 if r.promoted else 2


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
    compiled = _compiled(a)
    both = run_carpool(tasks, backend, compact=not a.full, max_batch=a.max_batch, compiled=compiled) if compiled else None
    r2 = score("carpool + compiled", tasks, both.answers, both.ledger) if both else None
    _table([r0.row(), r1.row()] + ([r2.row()] if r2 else []))
    regs = regressions(tasks, base.answers, pool.answers)
    d_tot = pct_change(r0.per_accepted(base.ledger.total_tokens), r1.per_accepted(pool.ledger.total_tokens))
    d_unc = pct_change(r0.per_accepted(base.ledger.uncached_tokens), r1.per_accepted(pool.ledger.uncached_tokens))
    routes = {}
    for ans in pool.answers.values():
        routes[ans.route] = routes.get(ans.route, 0) + 1
    print(f"\ntokens per accepted answer: {d_tot:+.1f}%   uncached+output per accepted: {d_unc:+.1f}%")
    print(f"paired regressions: {len(regs)} {regs if regs else ''}")
    print(f"carpool routes: {routes}   fallbacks: {pool.fallbacks or 'none'}")
    if both:
        regs2 = regressions(tasks, base.answers, both.answers)
        d2 = pct_change(r0.per_accepted(base.ledger.total_tokens), r2.per_accepted(both.ledger.total_tokens))
        routes2 = {}
        for ans in both.answers.values():
            routes2[ans.route] = routes2.get(ans.route, 0) + 1
        print(f"with compiled code first: tokens per accepted answer {d2:+.1f}% against one call per task; "
              f"paired regressions: {len(regs2)} {regs2 if regs2 else ''}; routes: {routes2}")
    if base.ledger.estimated:
        print("note: token counts are estimated (chars/4 + per-call overhead). Run with a real backend for real numbers.")
    if a.json:
        out = {"baseline": r0.row(), "carpool": r1.row(), "regressions": regs}
        if both:
            out["carpool_compiled"] = r2.row()
            out["regressions_compiled"] = regressions(tasks, base.answers, both.answers)
        Path(a.json).write_text(json.dumps(out, indent=2))


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
    s.add_argument("--compiled", help="a file made by `carpool compile`: tasks it answers skip the model")
    s.add_argument("--compiled-fn", default="solve")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("stats", help="summarise a saved benchmark results file")
    s.add_argument("results")
    s.set_defaults(fn=cmd_stats)

    s = sub.add_parser("compile", help="turn logged model calls into a plain function, behind a gate")
    s.add_argument("log", help='JSONL of logged calls: {"input": ..., "output": ...} per line')
    s.add_argument("--task", required=True, help="one sentence saying what the model call does")
    s.add_argument("--out", default="compiled/solve.py")
    s.add_argument("--backend", default="claude-lean", help="the model that writes the function (any carpool backend)")
    s.add_argument("--fn", dest="func", default="solve", help="name of the generated function")
    s.add_argument("--context-file", help="text added to the prompt, such as the list of allowed answers")
    s.add_argument("--answers", help="treat LOG as a tasks file and read the logged answers from this file "
                   "(JSONL with id and answer, as `carpool run` prints)")
    s.add_argument("--match", choices=["exact", "loose"],
                   help='how an answer is compared with the logged one (default: loose with --answers, else exact; '
                        '"loose" ignores case, articles and a unit word: "7" agrees with "7 days")')
    s.add_argument("--held-out", help="judge on these calls (same format as LOG) instead of a random share of LOG")
    s.add_argument("--held-out-answers", help="with --answers: the answers file for --held-out")
    s.add_argument("--input-key", default="input")
    s.add_argument("--output-key", default="output")
    s.add_argument("--val", type=float, default=0.3, help="share of logged calls held out to judge the function")
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--min-precision", type=float, default=0.95)
    s.add_argument("--min-lower-bound", type=float, default=0.85)
    s.add_argument("--min-coverage", type=float, default=0.10)
    s.add_argument("--call-cost", type=float, help="what one model call costs you, in USD, to estimate break-even")
    s.add_argument("--json", help="write the report here")
    s.set_defaults(fn=cmd_compile)

    s = sub.add_parser("bench", help="baseline vs carpool, paired")
    s.add_argument("tasks", nargs="?")
    s.add_argument("--backend", default="mock")
    s.add_argument("--overhead", type=int, default=16_000, help="mock per-call harness tokens")
    s.add_argument("--max-batch", type=int, default=24)
    s.add_argument("--full", action="store_true")
    s.add_argument("--compiled", help="also run carpool with this compiled file answering first")
    s.add_argument("--compiled-fn", default="solve")
    s.add_argument("--json")
    s.set_defaults(fn=cmd_bench)

    a = p.parse_args(argv)
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
