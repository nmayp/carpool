#!/usr/bin/env python3
"""compile + carpool on a mixed workload, paired against one lean call per task.

    python bench/compile_combo.py log       # carpool answers 120 routine tasks: these are the "logged calls"
    python bench/compile_combo.py heldout   # and 48 more, kept apart: the calls the gate judges on
    python bench/compile_combo.py compile   # a model writes a function from the first 120; the gate decides
    python bench/compile_combo.py measure   # 24 routine + 48 open-ended tasks, three ways
    python bench/compile_combo.py routine   # only the 24 routine tasks, arms in the order C B B C (cache balance)
    python bench/compile_combo.py report

Costs about $1 to $1.5 on Claude (`claude-lean`); CAP_USD (default 3) stops it. Results: results/compile-combo/.

The workload for `measure` never appears in the logs: 24 routine ops tasks from a disjoint fixture
(bench/data/ops24-fresh2.jsonl) and the 48 SQuAD holdout questions, which compiled code should leave alone.
Arms (same tasks, same scorer):
  one call per task     lean Claude call per task. The SQuAD half is the saved run in
                        results/squad48-holdout-claude-lean.json, not repeated.
  carpool               grouped, verified, individual fallback
  carpool + compiled    the compiled function answers first; carpool gets the rest
`carpool + compiled` runs first, so any prompt caching from it helps `carpool`, not the other way round.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "bench"))

from carpool.backends import make_backend  # noqa: E402
from carpool.cli import load_tasks  # noqa: E402
from carpool.compile import TASK_CONTEXT, CompiledTasks, compile_log, render, task_input  # noqa: E402
from carpool.ledger import Ledger  # noqa: E402
from carpool.router import run_carpool, run_individual, Result  # noqa: E402
from carpool.types import Answer, Completion  # noqa: E402
from real import lenient, summarize  # noqa: E402

OUT = REPO / "results" / "compile-combo"
CAP = float(os.environ.get("CAP_USD", "3"))
LOG_SETS = ["carpool/data/ops24.jsonl", "bench/data/ops24-fresh.jsonl", "bench/data/ops24-fresh3.jsonl",
            "bench/data/ops24-fresh4.jsonl", "bench/data/ops24-fresh5.jsonl"]
HELD_SETS = ["bench/data/ops24-fresh6.jsonl", "bench/data/ops24-fresh7.jsonl"]
TASK = "answer a question about a site's equipment from short evidence passages"


def spend_add(stage: str, usd: float) -> float:
    p = OUT / "spend.json"
    d = json.loads(p.read_text()) if p.exists() else {}
    d[stage] = round(d.get(stage, 0) + usd, 4)
    d["total"] = round(sum(v for k, v in d.items() if k != "total"), 4)
    p.write_text(json.dumps(d, indent=1))
    if d["total"] > CAP:
        raise SystemExit(f"spend cap ${CAP} exceeded (${d['total']})")
    return d["total"]


def log_tasks():
    return [t for f in LOG_SETS for t in load_tasks(str(REPO / f))]


def stage_log(sets=None, name="logged-answers", label="the log run"):
    OUT.mkdir(parents=True, exist_ok=True)
    tasks = [t for f in sets for t in load_tasks(str(REPO / f))] if sets else log_tasks()
    res = run_carpool(tasks, make_backend("claude-lean"), max_batch=24)
    with open(OUT / f"{name}.jsonl", "w") as f:
        for t in tasks:
            a = res.answers[t.id]
            f.write(json.dumps({"id": t.id, "answer": a.answer, "source": a.source_id, "route": a.route,
                                "fallback": a.fallback_reason}) + "\n")
    row = summarize(f"carpool ({label})", tasks, res)
    (OUT / f"{name.replace('logged-answers', 'log-run')}.json").write_text(json.dumps(row, indent=1))
    print(f"{len(tasks)} tasks, accepted {row['accepted']}, calls {row['calls']}, ${row['usd']}")
    print("total spend $", spend_add(label.split()[0] if label != "the log run" else "log", res.ledger.cost_usd))


def stage_heldout():
    stage_log(HELD_SETS, "heldout-answers", "held-out run")


def _pairs(sets, name):
    got = {}
    for line in (OUT / f"{name}.jsonl").read_text().splitlines():
        d = json.loads(line)
        if d["answer"].strip():
            got[d["id"]] = d["answer"]
    return [(task_input(t, 2), got[t.id]) for f in sets for t in load_tasks(str(REPO / f)) if t.id in got]


def stage_compile():
    pairs = _pairs(LOG_SETS, "logged-answers")
    held = _pairs(HELD_SETS, "heldout-answers")
    # "loose": the log mixes "7" and "7 days" for the same question, and both are right
    r = compile_log(pairs, make_backend("claude-lean"), TASK, fn="solve", context=TASK_CONTEXT, match="loose",
                    held_out=held)
    rep = r.report()
    (OUT / "compile-report.json").write_text(json.dumps(rep, indent=1))
    (OUT / ("solve.py" if r.promoted else "solve-rejected.py")).write_text(render(r))
    print(json.dumps({k: rep[k] for k in ("promoted", "reason", "n_train", "n_held_out", "answered", "agreed",
                                          "coverage", "precision", "lower_bound", "cost_usd", "calls", "match", "split")},
                     indent=1))
    print("total spend $", spend_add("compile", r.cost_usd))


def saved_squad_baseline(tasks):
    d = json.loads((REPO / "results/squad48-holdout-claude-lean.json").read_text())["runs"][0]
    ledger = Ledger()
    for c in d["calls_detail"]:
        ledger.add(Completion(text="", input_tokens=c["in"], cached_input_tokens=c["cached"], output_tokens=c["out"],
                              seconds=c["s"], cost_usd=c["usd"]))
    answers = {k: Answer(k, v[0], v[1], v[2]) for k, v in d["answers"].items()}
    return Result(answers, ledger)


def stage_measure():
    ops = load_tasks(str(REPO / "bench/data/ops24-fresh2.jsonl"))
    squad = load_tasks(str(REPO / "bench/data/squad48-holdout.jsonl"))
    tasks = ops + squad
    code = CompiledTasks.from_file(str(OUT / "solve.py"))
    backend = make_backend("claude-lean")
    arms = {}
    t0 = time.perf_counter()
    arms["carpool + compiled"] = (run_carpool(tasks, backend, max_batch=24, compiled=code), time.perf_counter() - t0)
    spend_add("measure", arms["carpool + compiled"][0].ledger.cost_usd)
    t0 = time.perf_counter()
    arms["carpool"] = (run_carpool(tasks, backend, max_batch=24), time.perf_counter() - t0)
    spend_add("measure", arms["carpool"][0].ledger.cost_usd)
    t0 = time.perf_counter()
    ops_base = run_individual(ops, backend, workers=4)
    spend_add("measure", ops_base.ledger.cost_usd)
    sq = saved_squad_baseline(squad)
    base_ledger = Ledger(calls=ops_base.ledger.calls + sq.ledger.calls)
    base = Result({**ops_base.answers, **sq.answers}, base_ledger)
    arms["one call per task"] = (base, time.perf_counter() - t0)

    rows = {}
    for name in ("one call per task", "carpool", "carpool + compiled"):
        res, wall = arms[name]
        rows[name] = summarize(name, tasks, res, None if name == "one call per task" else base, wall)
    cr = arms["carpool + compiled"][0]
    compiled_ids = [i for i, a in cr.answers.items() if a.route == "compiled"]
    by_id = {t.id: t for t in tasks}
    info = {
        "compiled_answered": len(compiled_ids),
        "compiled_answered_ops": sum(1 for i in compiled_ids if i in {t.id for t in ops}),
        "compiled_answered_squad": sum(1 for i in compiled_ids if i in {t.id for t in squad}),
        "compiled_correct_strict": sum(1 for i in compiled_ids if cr.answers[i].answer in by_id[i].answers or
                                       any(cr.answers[i].answer.casefold() == g.casefold() for g in by_id[i].answers)),
        "compiled_correct_lenient": sum(lenient(by_id[i], cr.answers[i]) for i in compiled_ids),
    }
    (OUT / "combined.json").write_text(json.dumps({"workload": {"ops_fresh2": len(ops), "squad_holdout": len(squad)},
                                                   "compiled": info, "arms": rows}, indent=1))
    stage_report()


def stage_routine():
    ops = load_tasks(str(REPO / "bench/data/ops24-fresh2.jsonl"))
    code = CompiledTasks.from_file(str(OUT / "solve.py"))
    backend = make_backend("claude-lean")
    runs = []
    for arm in ("carpool + compiled", "carpool", "carpool", "carpool + compiled"):
        res = run_carpool(ops, backend, max_batch=24, compiled=code if "compiled" in arm else None)
        spend_add("routine", res.ledger.cost_usd)
        row = summarize(arm, ops, res)
        runs.append({"arm": arm, "calls": row["calls"], "strict": row["accepted"], "lenient": row["lenient"],
                     "usd": row["usd"], "total_tokens": res.ledger.total_tokens,
                     "cached_input": row["cached_input"], "routes": row["routes"], "fallbacks": row["fallbacks"]})
        print(runs[-1], flush=True)
    (OUT / "routine-only.json").write_text(json.dumps({"tasks": len(ops), "runs": runs}, indent=1))


def stage_report():
    d = json.loads((OUT / "combined.json").read_text())
    print("compiled code:", d["compiled"])
    print("%-20s %6s %8s %8s %9s %9s %12s %s" % ("arm", "calls", "strict", "lenient", "usd", "uncached", "usd/accepted", "regressions"))
    for name, r in d["arms"].items():
        print("%-20s %6s %8s %8s %9.4f %9s %12s %s" % (
            name, r["calls"], r["accepted"], r["lenient"], r["usd"], r["uncached+output"], r["usd/accepted"],
            (len(r["regressions"]), len(r["lenient_regressions"])) if "regressions" in r else "-"))


if __name__ == "__main__":
    globals()["stage_" + (sys.argv[1] if len(sys.argv) > 1 else "report")]()
