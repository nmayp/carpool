"""End-to-end: does INSTALLING carpool change what a real agent spends?

A Claude Code agent gets a multi-item job in a fresh directory:
  triage N support tickets (product area + order ID) and count orders by status
  from a CSV. Half the work is reasoning (area), half is exact (IDs, counts).

Arms, each run in a fresh temp dir with identical files:
  control   plain `claude -p`
  carpool   `claude -p --plugin-dir <this repo>`  (skill + /carpool command available)

Scored against a hidden key. Usage and cost come from Claude Code's own JSON.

    python bench/e2e_skill.py --runs 3 --out results/e2e-skill-claude-code.json
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
AREAS = {
    "billing": ["I was charged twice for", "my invoice shows the wrong amount for", "refund still missing for"],
    "shipping": ["the parcel never arrived for", "tracking has not moved for", "courier left the box at the wrong address for"],
    "account": ["I cannot log in to see", "password reset emails never come, so I can't check", "2FA locks me out of"],
    "product": ["the blender's lid cracked on", "the charger stopped working, shipped in", "screen has dead pixels, came in"],
}
STATUSES = ["shipped", "pending", "delivered", "cancelled"]

TASK = """Triage every support ticket in ./tickets/ (one file each).
For each ticket decide the product area (exactly one of: billing, shipping, account, product)
and extract the order ID it mentions (format ORD-#####).
Also, using ./orders.csv, count how many orders have each status.
Write ./triage.json as:
{"tickets": [{"file": "<name>", "area": "<area>", "order_id": "<id>"}], "status_counts": {"<status>": <n>}}
Do not ask questions; just do it."""


def make_workspace(d: Path, n: int, seed: int):
    rng = random.Random(seed)
    (d / "tickets").mkdir()
    key = {"tickets": {}, "status_counts": {}}
    rows = []
    for i in range(400):
        st = rng.choice(STATUSES)
        rows.append((f"ORD-{10000 + i}", st))
        key["status_counts"][st] = key["status_counts"].get(st, 0) + 1
    for i in range(n):
        area = rng.choice(list(AREAS))
        oid = rows[rng.randrange(len(rows))][0]
        name = f"ticket_{i+1:02d}.txt"
        body = (f"From: customer{i}@example.com\nSubject: help please\n\n"
                f"Hi, {rng.choice(AREAS[area])} order {oid}. "
                f"I've been a customer for {rng.randint(1, 9)} years and this is frustrating.\n"
                f"Thanks,\nCustomer {i}\n")
        (d / "tickets" / name).write_text(body)
        key["tickets"][name] = {"area": area, "order_id": oid}
    (d / "orders.csv").write_text("order_id,status\n" + "".join(f"{o},{s}\n" for o, s in rows))
    return key


CONTRACT_TASK = """Review every contract in ./contracts/ (one file each; they are long).
For each one, find the APPROVED termination notice period in days (ignore drafts and superseded clauses)
and the name of the counterparty.
Write ./review.json as {"contracts": [{"file": "<name>", "notice_days": <int>, "counterparty": "<name>"}]}
Do not ask questions; just do it."""

FILLER = ("The parties shall cooperate in good faith on all reporting obligations described herein. "
          "Invoices are payable within the period stated in Schedule B unless otherwise agreed in writing. "
          "Confidential information excludes material that is publicly available through no fault of the recipient. "
          "Each party shall maintain insurance at levels customary for its industry. ")
PARTIES = ["Halvard Logistics", "Quill Analytics", "Brenmoor Foods", "Ostend Marine", "Tamsin Labs", "Ferrow Energy",
           "Kestrel Media", "Orrin Health", "Calder Textiles", "Vantor Robotics", "Elbrook Water", "Sallow Retail"]


def make_contracts(d: Path, n: int, seed: int):
    rng = random.Random(seed)
    (d / "contracts").mkdir()
    key = {}
    for i in range(n):
        party = PARTIES[i % len(PARTIES)]
        draft, final = rng.choice([15, 20, 45]), rng.choice([30, 60, 90])
        paras = [FILLER * 3 for _ in range(14)]
        paras.insert(rng.randrange(2, 6), f"DRAFT (superseded, do not apply): either party may terminate on {draft} days' notice.")
        paras.insert(rng.randrange(8, 14), f"Clause 14.2 (approved, executed version): either party may terminate this agreement on {final} days' written notice.")
        name = f"contract_{i+1:02d}.md"
        (d / "contracts" / name).write_text(f"# Master Services Agreement\n\nBetween Northwind Ltd and {party}.\n\n" + "\n\n".join(paras))
        key[name] = {"notice_days": final, "counterparty": party}
    return key


def score_contracts(d: Path, key) -> dict:
    try:
        got = json.loads((d / "review.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {"tickets_correct": 0, "counts_correct": None, "valid": False}
    by = {c.get("file"): c for c in got.get("contracts", []) if isinstance(c, dict)}
    ok = sum(1 for f, k in key.items() if str(by.get(f, {}).get("notice_days")) == str(k["notice_days"])
             and k["counterparty"].lower() in str(by.get(f, {}).get("counterparty", "")).lower())
    return {"tickets_correct": ok, "counts_correct": None, "valid": True}


def score(d: Path, key) -> dict:
    try:
        got = json.loads((d / "triage.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {"tickets_correct": 0, "counts_correct": False, "valid": False}
    by = {t.get("file"): t for t in got.get("tickets", []) if isinstance(t, dict)}
    ok = sum(1 for f, k in key["tickets"].items()
             if by.get(f, {}).get("area") == k["area"] and by.get(f, {}).get("order_id") == k["order_id"])
    counts = {k: int(v) for k, v in (got.get("status_counts") or {}).items()} == key["status_counts"]
    return {"tickets_correct": ok, "counts_correct": counts, "valid": True}


def run_arm(arm: str, n: int, seed: int, model: str | None, scenario: str = "tickets"):
    with tempfile.TemporaryDirectory(prefix=f"carpool-e2e-{arm}-") as tmp:
        d = Path(tmp)
        if scenario == "contracts":
            key, prompt, scorer = make_contracts(d, n, seed), CONTRACT_TASK, score_contracts
        else:
            key, prompt, scorer = make_workspace(d, n, seed), TASK, score
        argv = ["claude", "-p", "--output-format", "json", "--no-session-persistence", "--strict-mcp-config",
                "--allowedTools", "Read", "Write", "Edit", "Bash", "Glob", "Grep", "Task", "Agent", "Skill"]
        if model:
            argv += ["--model", model]
        if arm == "carpool":
            argv += ["--plugin-dir", str(REPO)]
        t0 = time.perf_counter()
        p = subprocess.run(argv, input=prompt, capture_output=True, text=True, cwd=d, timeout=1800)
        dt = time.perf_counter() - t0
        try:
            j = json.loads(p.stdout)
        except json.JSONDecodeError:
            return {"arm": arm, "error": (p.stderr or p.stdout)[-500:]}
        u = j.get("usage", {})
        sub = j.get("subagent_stats", {})
        return {
            "arm": arm, "scenario": scenario, "seed": seed, "seconds": round(dt, 1),
            "usd": j.get("total_cost_usd"), "turns": j.get("num_turns"),
            "input": (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)),
            "cached": u.get("cache_read_input_tokens", 0), "output": u.get("output_tokens", 0),
            "subagents": sub.get("spawned", 0),
            **scorer(d, key), "n": n,
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--model", default=None)
    ap.add_argument("--scenario", default="tickets", choices=["tickets", "contracts"])
    ap.add_argument("--arms", default="control,carpool")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    a.out = a.out or f"results/e2e-{a.scenario}-claude-code.json"
    out = {"scenario": a.scenario, "task": CONTRACT_TASK if a.scenario == "contracts" else TASK, "n": a.n, "runs": []}
    arms = a.arms.split(",")
    for r in range(a.runs):
        for arm in (arms if r % 2 == 0 else arms[::-1]):  # alternate order
            row = run_arm(arm, a.n, seed=100 + r, model=a.model, scenario=a.scenario)
            print(json.dumps(row), flush=True)
            out["runs"].append(row)
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            Path(a.out).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
