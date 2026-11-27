"""Real-model benchmark: ablation + batch-size sweep, paired against the baseline.

    python bench/real.py --backend claude-code --tasks carpool/data/ops24.jsonl --out results/ops24-claude-code.json
    python bench/real.py --backend claude-code --tasks bench/data/squad48.jsonl --sweep 4,12,24,48

Arms (same tasks, same prompts, same scorer):
  baseline        one call per task, full evidence
  compact-only    one call per task, compact evidence when preflight passes, verify + fallback
  batch-only      one grouped call, full evidence for every task
  carpool         one grouped call, compact when safe, verify + individual fallback
Sweep: carpool with groups of size k.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from carpool.backends import make_backend  # noqa: E402
from carpool.cli import load_tasks  # noqa: E402
from carpool.compact import normalize  # noqa: E402
from carpool.ledger import regressions, score  # noqa: E402
from carpool.router import run_carpool, run_compact_individual, run_individual  # noqa: E402


def lenient(task, ans) -> bool:
    """Equivalent scoring: gold contained in the answer or the answer in gold
    (credits 'the Design Assurance Team' vs 'Design Assurance Team', '12' vs '12 days')."""
    if ans is None or not ans.answer:
        return False
    a = normalize(ans.answer)
    return any(g and (normalize(g) in a or (a in normalize(g) and len(a) >= 3)) for g in map(str, task.answers))


def summarize(name, tasks, res, base=None, wall=None):
    rep = score(name, tasks, res.answers, res.ledger)
    row = rep.row()
    row["lenient"] = f"{sum(lenient(t, res.answers.get(t.id)) for t in tasks)}/{len(tasks)}"
    row["wall_seconds"] = round(wall, 1) if wall is not None else None
    row["cached_input"] = res.ledger.cached_input_tokens
    row["usd"] = round(res.ledger.cost_usd, 4)
    if base is not None:
        row["regressions"] = regressions(tasks, base.answers, res.answers)
        row["lenient_regressions"] = [t.id for t in tasks if lenient(t, base.answers.get(t.id)) and not lenient(t, res.answers.get(t.id))]
    row["fallbacks"] = res.fallbacks
    row["routes"] = {}
    for a in res.answers.values():
        row["routes"][a.route] = row["routes"].get(a.route, 0) + 1
    row["calls_detail"] = [
        {"in": c.input_tokens, "cached": c.cached_input_tokens, "out": c.output_tokens,
         "s": round(c.seconds, 2), "usd": round(c.cost_usd, 5)} for c in res.ledger.calls
    ]
    row["answers"] = {k: [v.answer, v.source_id, v.route] for k, v in sorted(res.answers.items())}
    return row


def timed(fn):
    t0 = time.perf_counter()
    r = fn()
    return r, time.perf_counter() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="claude-code")
    ap.add_argument("--tasks", default=None)
    ap.add_argument("--arms", default="baseline,compact-only,batch-only,carpool")
    ap.add_argument("--sweep", default="", help="comma-separated group sizes for carpool")
    ap.add_argument("--repeats", type=int, default=1, help="repeat the grouped arms (cheap) to show variance")
    ap.add_argument("--workers", type=int, default=4, help="concurrency for one-call-per-task arms")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    tasks = load_tasks(a.tasks)
    backend = make_backend(a.backend)
    out = {"backend": a.backend, "tasks": a.tasks or "ops24", "n": len(tasks), "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "runs": []}
    path = Path(a.out or f"results/{Path(out['tasks']).stem}-{a.backend.replace(':', '_')}.json")
    path.parent.mkdir(parents=True, exist_ok=True)

    def save():
        path.write_text(json.dumps(out, indent=1))

    def log(row):
        keep = {k: row[k] for k in ("route", "accepted", "lenient", "calls", "total_tokens", "uncached+output", "usd", "wall_seconds")}
        print(json.dumps({**keep, "regressions": row.get("regressions")}), flush=True)
        out["runs"].append(row)
        save()

    arms = a.arms.split(",")
    base, wall = timed(lambda: run_individual(tasks, backend, workers=a.workers))
    log(summarize("baseline", tasks, base, wall=wall))

    if "compact-only" in arms:
        r, w = timed(lambda: run_compact_individual(tasks, backend, workers=a.workers))
        log(summarize("compact-only", tasks, r, base, w))
    for rep in range(a.repeats):
        sfx = f"#{rep+1}" if a.repeats > 1 else ""
        if "batch-only" in arms:
            r, w = timed(lambda: run_carpool(tasks, backend, compact=False, max_batch=len(tasks)))
            log(summarize("batch-only" + sfx, tasks, r, base, w))
        if "carpool" in arms:
            r, w = timed(lambda: run_carpool(tasks, backend, max_batch=len(tasks)))
            log(summarize("carpool" + sfx, tasks, r, base, w))
    for k in [int(x) for x in a.sweep.split(",") if x]:
        if k >= len(tasks) and "carpool" in arms:
            continue  # already measured
        r, w = timed(lambda: run_carpool(tasks, backend, max_batch=k))
        log(summarize(f"carpool k={k}", tasks, r, base, w))
    out["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save()
    print(f"saved {path}")


if __name__ == "__main__":
    main()
