"""Does the fan-out hook change what a real agent does?

The workload invites fan-out: 10 independent Python modules, each with exactly one
planted bug, and a prompt that allows subagents. Three arms, identical except for
what is installed:

  plain    plain `claude -p`
  skill    the carpool plugin WITHOUT its hook (skill + /carpool command only)
  hook/v2  the full carpool plugin (skill + command + prompt-time and launch-time hooks)
  v1       the plugin with only the launch-time hook (before the prompt-time hook existed)
  noop     the plugin with a hook on the subagent tool that does nothing (tests whether registration alone matters)

Measured per run: subagents spawned, turns, tokens and list-price USD (Claude Code's
own report), and bugs found (file correct and line within 3 of the planted bug).

    python bench/e2e_hook.py --runs 3 --out results/e2e-hook-claude-code.json
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

TASK_EXPLICIT = """Review each of the 10 modules in ./src for bugs. Each module contains exactly one bug.
Delegate each module to its own subagent (the Agent tool), one subagent per module, and collect their findings.
Write ./review.json as a JSON array: [{"file": "<file name>", "line": <line number>, "bug": "<one sentence>"}]
with one entry per module. Do not ask questions; just do it."""

TASK = """Review each of the 10 modules in ./src for bugs. Each module contains exactly one bug.
The modules are independent of each other, so you may use subagents (the Agent tool) to review
them in parallel if that helps.
Write ./review.json as a JSON array: [{"file": "<file name>", "line": <line number>, "bug": "<one sentence>"}]
with one entry per module. Do not ask questions; just do it."""

# Correct helper functions used as filler (each is a list of lines).
FILLER = [
    ["def clamp(x, lo, hi):", "    if x < lo:", "        return lo", "    if x > hi:", "        return hi", "    return x"],
    ["def mean(values):", "    if not values:", "        raise ValueError('empty')", "    return sum(values) / len(values)"],
    ["def chunks(seq, size):", "    out = []", "    for i in range(0, len(seq), size):", "        out.append(seq[i:i + size])", "    return out"],
    ["def normalize_name(name):", "    parts = name.strip().split()", "    return ' '.join(p.capitalize() for p in parts)"],
    ["def count_words(text):", "    counts = {}", "    for word in text.lower().split():", "        counts[word] = counts.get(word, 0) + 1", "    return counts"],
    ["def flatten(nested):", "    out = []", "    for item in nested:", "        if isinstance(item, list):", "            out.extend(flatten(item))",
     "        else:", "            out.append(item)", "    return out"],
    ["def safe_div(a, b, default=0.0):", "    if b == 0:", "        return default", "    return a / b"],
    ["def unique(seq):", "    seen = set()", "    out = []", "    for x in seq:", "        if x not in seen:", "            seen.add(x)", "            out.append(x)", "    return out"],
    ["def percent(part, whole):", "    if whole == 0:", "        return 0.0", "    return 100.0 * part / whole"],
    ["def last_n(seq, n):", "    if n <= 0:", "        return []", "    return list(seq[-n:])"],
]

# (name, lines, index of the buggy line within the function, description)
BUGS = [
    ("sum_first_n", ["def sum_first_n(values, n):", "    total = 0", "    for i in range(n + 1):", "        total += values[i]", "    return total"], 2,
     "off-by-one: range(n + 1) sums n+1 items"),
    ("is_adult", ["def is_adult(age):", "    return age > 18"], 1, "age 18 should count as adult (>=)"),
    ("average_price", ["def average_price(items):", "    total = sum(item['price'] for item in items)", "    return total / len(total)"], 2,
     "divides by len(total) instead of len(items)"),
    ("find_index", ["def find_index(seq, target):", "    for i, x in enumerate(seq):", "        if x == target:", "            return i", "    return 0"], 4,
     "returns 0 instead of -1/None when not found"),
    ("apply_discount", ["def apply_discount(price, pct):", "    return price * pct / 100"], 1, "returns the discount, not the discounted price"),
    ("merge_dicts", ["def merge_dicts(a, b):", "    out = a", "    out.update(b)", "    return out"], 1, "mutates the caller's dict a (no copy)"),
    ("celsius_to_f", ["def celsius_to_f(c):", "    return c * 9 / 5 - 32"], 1, "should add 32, not subtract"),
    ("max_of", ["def max_of(values):", "    best = 0", "    for v in values:", "        if v > best:", "            best = v", "    return best"], 1,
     "starting at 0 is wrong for all-negative input"),
    ("days_in_month", ["def days_in_month(month):", "    if month in (4, 6, 9, 11):", "        return 30", "    if month == 2:", "        return 29", "    return 31"], 4,
     "February returns 29 without a leap-year check"),
    ("strip_prefix", ["def strip_prefix(s, prefix):", "    if s.startswith(prefix):", "        return s[len(prefix) + 1:]", "    return s"], 2,
     "off-by-one: drops one extra character"),
]


def big_filler(rng: random.Random, n: int) -> list[list[str]]:
    """n distinct, correct helper functions, so large modules are not just repeats."""
    out = []
    for j in range(n):
        base = FILLER[j % len(FILLER)]
        name = base[0].split("(")[0].replace("def ", "")
        new = f"{name}_{j}"
        out.append([base[0].replace(f"def {name}(", f"def {new}(")] + base[1:])
    rng.shuffle(out)
    return out


def make_workspace(d: Path, seed: int, size: str = "small") -> dict:
    rng = random.Random(seed)
    (d / "src").mkdir()
    key = {}
    bugs = BUGS[:]
    rng.shuffle(bugs)
    for i, (name, lines, bad, _desc) in enumerate(bugs):
        filler = rng.sample(FILLER, 6) if size == "small" else big_filler(rng, 90)
        pos = rng.randrange(1, len(filler))
        blocks = filler[:pos] + [lines] + filler[pos:]
        out = [f'"""Utility module {i + 1}."""', ""]
        for blk in blocks:
            if blk is lines:
                key_line = len(out) + 1 + bad
            out += blk + ["", ""]
        fname = f"module_{i + 1:02d}.py"
        (d / "src" / fname).write_text("\n".join(out))
        key[fname] = key_line
    return key


def score(d: Path, key: dict) -> int:
    try:
        got = json.loads((d / "review.json").read_text())
    except (OSError, json.JSONDecodeError):
        return 0
    found = 0
    for f, line in key.items():
        for g in got if isinstance(got, list) else []:
            if isinstance(g, dict) and Path(str(g.get("file", ""))).name == f:
                try:
                    if abs(int(g.get("line")) - line) <= 3:
                        found += 1
                        break
                except (TypeError, ValueError):
                    pass
    return found


def plugin_without_hook(tmp: Path) -> Path:
    dst = tmp / "carpool-nohook"
    shutil.copytree(REPO, dst, ignore=shutil.ignore_patterns(".git", "results", "bench", "docs", "tests", "hooks", "*.pdf"))
    return dst


def plugin_v1(tmp: Path) -> Path:
    """The plugin with only the launch-time (PreToolUse) hook, as it was before the prompt-time hook."""
    dst = tmp / "carpool-v1"
    shutil.copytree(REPO, dst, ignore=shutil.ignore_patterns(".git", "results", "bench", "docs", "tests", "*.pdf"))
    cfg = json.loads((dst / "hooks" / "hooks.json").read_text())
    cfg["hooks"].pop("UserPromptSubmit", None)
    (dst / "hooks" / "hooks.json").write_text(json.dumps(cfg, indent=2))
    (dst / "hooks" / "prompt_hint.py").unlink()
    return dst


def plugin_noop(tmp: Path) -> Path:
    """The plugin with a hook registered on the subagent tool that does nothing at all."""
    dst = tmp / "carpool-noop"
    shutil.copytree(REPO, dst, ignore=shutil.ignore_patterns(".git", "results", "bench", "docs", "tests", "*.pdf"))
    for f in (dst / "hooks").glob("*.py"):
        f.unlink()
    (dst / "hooks" / "noop.py").write_text("import sys\nsys.stdin.read()\n")
    cfg = {"description": "no-op hook for testing", "hooks": {"PreToolUse": [{"matcher": "Agent|Task", "hooks": [
        {"type": "command", "command": 'python3 "${CLAUDE_PLUGIN_ROOT}"/hooks/noop.py', "timeout": 10}]}]}}
    (dst / "hooks" / "hooks.json").write_text(json.dumps(cfg, indent=2))
    return dst


def run_arm(arm: str, seed: int, nohook_dir: Path, model: str | None, scenario: str = "small", v1_dir: Path | None = None,
            noop_dir: Path | None = None):
    with tempfile.TemporaryDirectory(prefix=f"carpool-hook-{arm}-") as tmp:
        d = Path(tmp)
        key = make_workspace(d, seed, "small" if scenario == "small" else "large")
        prompt = TASK_EXPLICIT if scenario == "explicit" else TASK
        argv = ["claude", "-p", "--output-format", "json", "--no-session-persistence", "--strict-mcp-config",
                "--allowedTools", "Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent", "Task", "Skill"]
        if model:
            argv += ["--model", model]
        if arm in ("hook", "v2"):  # full plugin: prompt-time + launch-time hooks
            argv += ["--plugin-dir", str(REPO)]
        elif arm == "v1":
            argv += ["--plugin-dir", str(v1_dir)]
        elif arm == "noop":
            argv += ["--plugin-dir", str(noop_dir)]
        elif arm == "skill":
            argv += ["--plugin-dir", str(nohook_dir)]
        t0 = time.perf_counter()
        p = subprocess.run(argv, input=prompt, capture_output=True, text=True, cwd=d, timeout=2400)
        dt = time.perf_counter() - t0
        try:
            j = json.loads(p.stdout)
        except json.JSONDecodeError:
            return {"arm": arm, "seed": seed, "error": (p.stderr or p.stdout)[-500:]}
        u = j.get("usage", {})
        sub = j.get("subagent_stats", {}) or {}
        return {
            "arm": arm, "scenario": scenario, "seed": seed, "seconds": round(dt, 1), "usd": j.get("total_cost_usd"),
            "turns": j.get("num_turns"), "subagents": sub.get("spawned", 0),
            "input": u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0),
            "cached": u.get("cache_read_input_tokens", 0), "output": u.get("output_tokens", 0),
            "bugs_found": score(d, key), "n": len(key),
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--seed0", type=int, default=200)
    ap.add_argument("--arms", default="plain,skill,hook")
    ap.add_argument("--model", default=None)
    ap.add_argument("--scenario", default="small", choices=["small", "large", "explicit"],
                    help="small: 10 short modules; large: ~600-line modules; explicit: large + user asks for one subagent per module")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    a.out = a.out or f"results/e2e-hook-{a.scenario}-claude-code.json"
    arms = a.arms.split(",")
    out = {"scenario": a.scenario, "task": TASK_EXPLICIT if a.scenario == "explicit" else TASK, "arms": arms, "runs": []}
    with tempfile.TemporaryDirectory(prefix="carpool-nohook-") as tmp:
        nohook = plugin_without_hook(Path(tmp))
        v1 = plugin_v1(Path(tmp))
        noop = plugin_noop(Path(tmp))
        for r in range(a.runs):
            order = arms[r % len(arms):] + arms[:r % len(arms)]  # rotate order between runs
            for arm in order:
                row = run_arm(arm, seed=a.seed0 + r, nohook_dir=nohook, model=a.model, scenario=a.scenario, v1_dir=v1, noop_dir=noop)
                print(json.dumps(row), flush=True)
                out["runs"].append(row)
                Path(a.out).parent.mkdir(parents=True, exist_ok=True)
                Path(a.out).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
