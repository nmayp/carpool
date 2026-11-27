#!/usr/bin/env python3
"""Pilot: can LLM-written code replace LLM calls, and what does it cost?

Three tasks, three kinds of input:
  hdfs    real log lines -> event type (regular text)
  bank    real customer messages -> intent, 77 classes (needs meaning)
  squad   real questions over passages -> answer (open-ended; negative control)

Flow per task: an LLM labels a training sample (the "logged calls"), an LLM writes a
function from them, the function must pass a gate on a validation set, and we report
coverage, precision against GOLD labels and cost on a separate test set.

Stages (all cached on disk, so re-running never pays twice):
  python bench/compile_pilot.py data | teacher | single | compile | report

Costs about $2.3 on Claude (set CAP_USD to change the $6 stop). Data: PolyAI/banking77 (Hugging Face), HDFS_2k from
loghub, and this repo's SQuAD files. Results and the generated code are in results/compile-pilot/ and are
described in docs/RESULTS.md section 12.

This script ran BEFORE carpool.compile existed and has no promotion gate of its own: the gate rule in the library
(at least 95% agreement and a 95% lower bound of at least 85% on held-out calls) was fixed beforehand and applied by
hand to the numbers it prints. The prompts here are the ones the library uses.
"""
import concurrent.futures as cf
import csv
import hashlib
import io
import json
import math
import os
import random
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from carpool.agents import make_agent_backend  # noqa: E402

W = str(REPO / "results" / "compile-pilot")  # report.json, single.json and the generated code land here
CACHE = os.environ.get("COMPILE_PILOT_CACHE", os.path.expanduser("~/.cache/carpool-compile-pilot"))
os.makedirs(W, exist_ok=True)
os.makedirs(CACHE, exist_ok=True)
CAP = float(os.environ.get("CAP_USD", "6"))
BACKEND = make_agent_backend("claude-lean")
SEED = 20261127


# ---------------------------------------------------------------- llm + spend
def spent():
    t = 0.0
    for f in os.listdir(CACHE):
        if f.endswith(".json") and not f.startswith("data-"):
            t += json.load(open(os.path.join(CACHE, f))).get("usd", 0)
    return t


def llm(prompt, tag):
    key = hashlib.sha256((tag + prompt).encode()).hexdigest()[:24]
    p = os.path.join(CACHE, key + ".json")
    if os.path.exists(p):
        return json.load(open(p))
    if spent() > CAP:
        raise SystemExit(f"spend cap ${CAP} reached")
    c = BACKEND(prompt)
    out = {"text": c.text, "usd": c.cost_usd, "in": c.input_tokens, "out": c.output_tokens, "tag": tag}
    json.dump(out, open(p, "w"))
    return out


def wilson_lb(k, n, z=1.96):
    if n == 0:
        return 0.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    a = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - a) / d


# ---------------------------------------------------------------------- data
def cached_json(name, fn):
    p = os.path.join(CACHE, "data-" + name + ".json")
    if os.path.exists(p):
        return json.load(open(p))
    v = fn()
    json.dump(v, open(p, "w"))
    return v


def hf(dataset, split, offset, length=100):
    u = ("https://datasets-server.huggingface.co/rows?dataset=%s&config=default&split=%s&offset=%d&length=%d"
         % (urllib.parse.quote(dataset, safe=""), split, offset, length))
    err = None
    for i in range(5):
        try:
            return json.load(urllib.request.urlopen(u, timeout=60))
        except Exception as e:  # noqa: BLE001
            err, _ = e, time.sleep(2 * (i + 1))
    raise err


def load_bank():
    def build():
        rng = random.Random(SEED)
        first = hf("PolyAI/banking77", "train", 0, 1)
        names = [f for f in first["features"] if f["name"] == "label"][0]["type"]["names"]

        def pages(split, total, n):
            rows = []
            for o in rng.sample(range(0, total - 100, 100), n):
                rows += [[r["row"]["text"], names[r["row"]["label"]]] for r in hf("PolyAI/banking77", split, o)["rows"]]
            return rows

        train, test = pages("train", 10003, 3), pages("test", 3080, 3)
        rng.shuffle(test)
        return {"labels": names, "train": train, "val": test[:150], "test": test[150:]}
    return cached_json("bank", build)


def load_hdfs():
    def build():
        rng = random.Random(SEED)
        raw = urllib.request.urlopen(
            "https://raw.githubusercontent.com/logpai/loghub/master/HDFS/HDFS_2k.log_structured.csv", timeout=60
        ).read().decode()
        rows = list(csv.DictReader(io.StringIO(raw)))
        templates = {r["EventId"]: r["EventTemplate"] for r in rows}
        items = [[r["Content"], r["EventId"]] for r in rows]
        rng.shuffle(items)
        return {"templates": templates, "train": items[:100], "val": items[100:200], "test": items[200:400]}
    return cached_json("hdfs", build)


def squad_text(r):
    return "QUESTION: " + r["question"] + "\nPASSAGES:\n" + "\n".join("[%s] %s" % (s["id"], s["text"]) for s in r["sources"])


def load_squad():
    tr = [json.loads(l) for l in open(str(REPO / "bench/data/squad48.jsonl"))]
    te = [json.loads(l) for l in open(str(REPO / "bench/data/squad48-holdout.jsonl"))]
    lean = json.load(open(str(REPO / "results/squad48-holdout-claude-lean.json")))["runs"][0]
    teacher = {k: v[0] for k, v in lean["answers"].items()}
    per_call = lean["usd"] / lean["calls"]
    return {
        "train": [[[r["question"], "\n".join(s["text"] for s in r["sources"])], r["answers"]] for r in tr],
        "test": [[[r["question"], "\n".join(s["text"] for s in r["sources"])], r["answers"]] for r in te],
        "teacher": [teacher.get(r["id"]) for r in te],
        "per_call": per_call,
    }


def norm(s):
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\b(a|an|the)\b", " ", str(s).lower())).split()


def squad_ok(pred, golds):
    if pred is None:
        return False
    p = " ".join(norm(pred))
    for g in golds:
        gg = " ".join(norm(g))
        if p == gg or (gg and gg in p and len(p) <= 2 * len(gg) + 10):
            return True
    return False


# ------------------------------------------------------------------- teacher
def parse_map(text, n):
    m = re.search(r"\{.*\}", text, re.S)
    out = [None] * n
    if not m:
        return out
    try:
        d = json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return out
    for k, v in d.items():
        if str(k).isdigit() and 1 <= int(k) <= n:
            out[int(k) - 1] = v
    return out


def prompt_bank(labels, msgs):
    return ("Classify each customer message into exactly one intent from this list:\n" + "\n".join(labels)
            + "\n\nMessages:\n" + "\n".join("%d. %s" % (i + 1, m) for i, m in enumerate(msgs))
            + '\n\nReply with only a JSON object mapping each message number to its intent, like {"1": "intent_name"}.')


def prompt_hdfs(templates, msgs):
    t = "\n".join("%s: %s" % (k, v) for k, v in sorted(templates.items(), key=lambda kv: int(kv[0][1:])))
    return ("Each log line below was produced by exactly one of these event templates (<*> marks a variable):\n" + t
            + "\n\nLog lines:\n" + "\n".join("%d. %s" % (i + 1, m) for i, m in enumerate(msgs))
            + '\n\nReply with only a JSON object mapping each line number to its event id, like {"1": "E5"}.')


def teach(kind, ctx, msgs, chunk=10):
    pf = prompt_bank if kind == "bank" else prompt_hdfs
    chunks = [msgs[i:i + chunk] for i in range(0, len(msgs), chunk)]

    def one(ch):
        o = llm(pf(ctx, ch), "teacher-" + kind)
        return parse_map(o["text"], len(ch)), o["usd"]

    with cf.ThreadPoolExecutor(5) as ex:
        res = list(ex.map(one, chunks))
    labels, usd = [], 0.0
    for m, u in res:
        labels += m
        usd += u
    return labels, usd


def stage_teacher():
    for kind, d in (("hdfs", load_hdfs()), ("bank", load_bank())):
        ctx = d["templates"] if kind == "hdfs" else d["labels"]
        for split in ("train", "val", "test"):
            msgs = [x[0] for x in d[split]]
            lab, usd = teach(kind, ctx, msgs)
            gold = [x[1] for x in d[split]]
            acc = sum(a == b for a, b in zip(lab, gold)) / len(gold)
            print(f"{kind} {split}: n={len(gold)} teacher acc vs gold={acc:.3f} cost=${usd:.3f}", flush=True)
    print(f"total spend so far: ${spent():.2f}")


def stage_single():
    """Cost and accuracy of the one-call-per-item baseline, on 12 items per task."""
    out = {}
    for kind, d in (("hdfs", load_hdfs()), ("bank", load_bank())):
        ctx = d["templates"] if kind == "hdfs" else d["labels"]
        pf = prompt_bank if kind == "bank" else prompt_hdfs
        items = d["test"][:12]

        def one(it):
            o = llm(pf(ctx, [it[0]]), "single-" + kind)
            return parse_map(o["text"], 1)[0] == it[1], o["usd"]

        with cf.ThreadPoolExecutor(4) as ex:
            r = list(ex.map(one, items))
        out[kind] = {"acc": sum(a for a, _ in r) / len(r), "per_call": sum(u for _, u in r) / len(r)}
        print(kind, out[kind], flush=True)
    json.dump(out, open(os.path.join(W, "single.json"), "w"))


# ------------------------------------------------------------------- compile
RUNNER = r'''
import sys, json, importlib.util
spec = importlib.util.spec_from_file_location("m", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
fn = getattr(m, sys.argv[2])
inputs = json.load(sys.stdin)
out = []
for x in inputs:
    try:
        out.append(fn(*x) if isinstance(x, list) else fn(x))
    except Exception:
        out.append(None)
json.dump(out, sys.stdout)
'''


def run_code(code, fn, inputs):
    with tempfile.TemporaryDirectory() as td:
        open(os.path.join(td, "m.py"), "w").write(code)
        open(os.path.join(td, "r.py"), "w").write(RUNNER)
        try:
            r = subprocess.run([sys.executable, "-I", os.path.join(td, "r.py"), os.path.join(td, "m.py"), fn],
                               input=json.dumps(inputs), capture_output=True, text=True, timeout=120, cwd=td)
            out = json.loads(r.stdout)
            return out if len(out) == len(inputs) else [None] * len(inputs)
        except Exception:  # noqa: BLE001
            return [None] * len(inputs)


def extract(text):
    m = re.findall(r"```(?:python)?\n(.*?)```", text, re.S)
    return max(m, key=len) if m else text


def synth_prompt(desc, fn, sig, labels_block, examples):
    ex = "\n".join("%r => %r" % (x, y) for x, y in examples)
    return f"""Write a Python 3 function `{sig}` that replaces a language-model call.
Task: {desc}
It must return the correct answer, or None when it is not confident. Abstaining is cheap (the language model handles those inputs); a wrong answer is not.
Rules: standard library only (re is fine); no network; deterministic; it will run on thousands of NEW inputs, so generalise from the examples instead of memorising them; keep it under 250 lines.
{labels_block}
Examples (input => answer):
{ex}

Reply with one Python code block containing the complete module and nothing else."""


def fix_prompt(code, fn, wrong, abst):
    w = "\n".join("%r => expected %r, got %r" % (x, y, p) for x, y, p in wrong)
    a = "\n".join("%r => expected %r" % (x, y) for x, y in abst)
    return f"""Here is a Python function `{fn}` that replaces a language-model call:

```python
{code}
```

On the training examples it gave these WRONG answers:
{w or '(none)'}

and abstained (returned None) on these, which it should cover when it can do so reliably:
{a or '(none)'}

Improve it: fix the wrong answers, cover more of the abstained inputs by generalising (never by hard-coding a single example), and keep abstaining when unsure. Reply with one Python code block containing the complete module and nothing else."""


def compile_fn(kind, desc, fn, sig, labels_block, train, ok):
    """train: list of [input, answer]. Returns (code, usd). Two repair rounds on the TRAIN set only."""
    tag = "compile-" + kind
    usd = 0.0
    o = llm(synth_prompt(desc, fn, sig, labels_block, train), tag)
    usd += o["usd"]
    code = extract(o["text"])
    for _ in range(2):
        res = run_code(code, fn, [x for x, _ in train])
        wrong = [(x, y, p) for (x, y), p in zip(train, res) if p is not None and not ok(p, y)][:25]
        abst = [(x, y) for (x, y), p in zip(train, res) if p is None][:15]
        if not wrong and len(abst) <= 0.1 * len(train):
            break
        o = llm(fix_prompt(code, fn, wrong, abst), tag + "-fix")
        usd += o["usd"]
        code = extract(o["text"])
    return code, usd


def evaluate(code, fn, split, teacher_lab, ok, to_in=lambda x: x):
    outs = run_code(code, fn, [to_in(x[0]) for x in split])
    acc = [i for i, o in enumerate(outs) if o is not None]
    gold_ok = sum(ok(outs[i], split[i][1]) for i in acc)
    tea_ok = sum(1 for i in acc if outs[i] == teacher_lab[i])
    return outs, acc, gold_ok, tea_ok


def stage_compile():
    single = json.load(open(os.path.join(W, "single.json")))
    report = {}
    eq = lambda p, y: p == y  # noqa: E731

    # ---- hdfs + bank (classification)
    for kind in ("hdfs", "bank"):
        d = load_hdfs() if kind == "hdfs" else load_bank()
        ctx = d["templates"] if kind == "hdfs" else d["labels"]
        tl, tusd = teach(kind, ctx, [x[0] for x in d["train"]])
        train = [[x[0], l] for x, l in zip(d["train"], tl) if l is not None]
        if kind == "hdfs":
            desc = "map one HDFS log line (message part) to its event id (an id such as E5)."
            lb = "Allowed answers and the templates they stand for (<*> is a variable):\n" + "\n".join(
                "%s: %s" % (k, v) for k, v in sorted(d["templates"].items(), key=lambda kv: int(kv[0][1:])))
        else:
            desc = "map one customer message from a banking app to its intent label."
            lb = "Allowed answers (77 intent labels):\n" + "\n".join(d["labels"])
        code, cusd = compile_fn(kind, desc, "classify", "classify(text)", lb, train, eq)
        open(os.path.join(W, f"{kind}-classify.py"), "w").write(code)
        vl, _ = teach(kind, ctx, [x[0] for x in d["val"]])
        sl, _ = teach(kind, ctx, [x[0] for x in d["test"]])
        _, vacc, vgold, vtea = evaluate(code, "classify", d["val"], vl, eq)
        outs, tacc, tgold, ttea = evaluate(code, "classify", d["test"], sl, eq)
        n = len(d["test"])
        llm_ok = sum(a == x[1] for a, x in zip(sl, d["test"]))
        casc_ok = sum((outs[i] == d["test"][i][1]) if outs[i] is not None else (sl[i] == d["test"][i][1]) for i in range(n))
        report[kind] = {
            "gate_val_accepted": len(vacc), "gate_val_agree_teacher": vtea,
            "gate_val_precision_teacher": vtea / len(vacc) if vacc else 0, "gate_val_wilson_lb": wilson_lb(vtea, len(vacc)),
            "test_n": n, "coverage": len(tacc) / n,
            "precision_gold_on_accepted": tgold / len(tacc) if tacc else None,
            "precision_gold_wilson_lb": wilson_lb(tgold, len(tacc)),
            "llm_acc_gold": llm_ok / n, "cascade_acc_gold": casc_ok / n,
            "teacher_label_usd": tusd, "synth_usd": cusd, "train_n": len(train),
            "single_per_call": single[kind]["per_call"], "single_acc_12": single[kind]["acc"],
        }
        print(kind, json.dumps(report[kind], indent=1), flush=True)

    # ---- squad (negative control; trained on GOLD answers, which favours the code)
    s = load_squad()
    tr = [[x, a[0]] for x, a in s["train"]][:40]
    desc = "answer a factual question from the given passages with a short answer span copied from the text."
    ok1 = lambda p, y: squad_ok(p, [y])  # noqa: E731
    code, cusd = compile_fn("squad", desc, "answer", "answer(question, passages)", "", tr, ok1)
    open(os.path.join(W, "squad-answer.py"), "w").write(code)
    outs = run_code(code, "answer", [x for x, _ in s["test"]])
    acc = [i for i, o in enumerate(outs) if o is not None]
    gold_ok = sum(squad_ok(outs[i], s["test"][i][1]) for i in acc)
    n = len(s["test"])
    llm_ok = sum(squad_ok(t, g) for t, (_, g) in zip(s["teacher"], s["test"]))
    casc_ok = sum(squad_ok(outs[i], s["test"][i][1]) if outs[i] is not None else squad_ok(s["teacher"][i], s["test"][i][1]) for i in range(n))
    report["squad"] = {"test_n": n, "coverage": len(acc) / n,
                       "precision_gold_on_accepted": gold_ok / len(acc) if acc else None,
                       "precision_gold_wilson_lb": wilson_lb(gold_ok, len(acc)),
                       "llm_acc_gold": llm_ok / n, "cascade_acc_gold": casc_ok / n,
                       "teacher_label_usd": 0.0, "synth_usd": cusd, "train_n": len(tr),
                       "single_per_call": s["per_call"], "single_acc_12": None}
    print("squad", json.dumps(report["squad"], indent=1), flush=True)
    json.dump(report, open(os.path.join(W, "report.json"), "w"), indent=1)
    print(f"total spend: ${spent():.2f}")


def stage_report():
    r = json.load(open(os.path.join(W, "report.json")))
    print("%-6s %8s %10s %10s %10s %10s" % ("task", "coverage", "prec(gold)", "LLM acc", "cascade", "break-even"))
    for k, v in r.items():
        one = v["synth_usd"] + v["teacher_label_usd"]
        save = v["single_per_call"] * v["coverage"]
        be = one / save if save > 0 else float("inf")
        pg = v["precision_gold_on_accepted"]
        print("%-6s %7.0f%% %10s %9.0f%% %9.0f%% %10s" % (
            k, 100 * v["coverage"], "-" if pg is None else "%.0f%%" % (100 * pg), 100 * v["llm_acc_gold"],
            100 * v["cascade_acc_gold"], "never" if be == float("inf") else "%d items" % be))


if __name__ == "__main__":
    st = sys.argv[1] if len(sys.argv) > 1 else "data"
    if st == "data":
        for name, fn in (("hdfs", load_hdfs), ("bank", load_bank)):
            d = fn()
            print(name, {k: (len(v) if hasattr(v, "__len__") else v) for k, v in d.items()})
        s = load_squad()
        print("squad", len(s["train"]), len(s["test"]), "per_call", round(s["per_call"], 4))
    else:
        globals()["stage_" + st]()
