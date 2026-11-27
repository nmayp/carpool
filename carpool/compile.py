"""carpool compile: turn logged model calls into a plain function, behind a gate.

    carpool compile calls.jsonl --task "map a log line to its event id" --out compiled/solve.py

A model reads some of your logged (input, output) pairs and writes a small Python function. The function may
abstain by returning None. It is kept only if, on logged calls it never saw, the answers it does give agree with
the logged outputs often enough. Whatever it abstains on still goes to the model (see `Cascade`).

Stdlib only. The candidate code is run in a subprocess with a CPU limit and a timeout. That limits damage from a
runaway loop; it is not a security sandbox. Read a compiled file before you deploy it: it runs as you.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import math
import os
import random
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .compact import normalize

MIN_HELD_OUT = 30  # fewer held-out calls than this cannot clear a 95% lower bound of 85% anyway


def wilson_lower(k: int, n: int, z: float = 1.96) -> float:
    """Lower end of the Wilson score interval for k successes in n trials (95% by default)."""
    if n == 0:
        return 0.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    a = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - a) / d


def same_answer(a: Any, b: Any, mode: str = "exact") -> bool:
    """Do two answers agree?  "exact": identical. "loose": same after normalising case, spacing, punctuation and a
    leading article; or, for answers with numbers, the same numbers and one side's words are a subset of the
    other's ("7" agrees with "7 days"; "7 days" does not agree with "7 weeks" or "17 days")."""
    if a == b:
        return True
    if mode == "exact" or not isinstance(a, str) or not isinstance(b, str):
        return False
    if normalize(a) == normalize(b):
        return True
    na, nb = re.findall(r"\d+(?:[.,]\d+)?", normalize(a)), re.findall(r"\d+(?:[.,]\d+)?", normalize(b))
    wa, wb = set(re.findall(r"[^\W\d_]+", normalize(a))), set(re.findall(r"[^\W\d_]+", normalize(b)))
    return bool(na) and na == nb and (wa <= wb or wb <= wa)


@dataclass
class Gate:
    """When a compiled function is good enough to use. Measured on held-out logged calls."""

    min_precision: float = 0.95    # answers it gives that match the logged output
    min_lower_bound: float = 0.85  # 95% lower bound on that precision (so a lucky small sample does not pass)
    min_coverage: float = 0.10     # share of calls it answers at all; below this the file is not worth keeping


@dataclass
class CompileResult:
    code: str
    promoted: bool
    reason: str
    fn: str
    task: str
    n_train: int
    n_held_out: int
    answered: int
    agreed: int
    coverage: float
    precision: Optional[float]
    lower_bound: float
    cost_usd: float
    calls: int
    gate: dict = field(default_factory=dict)
    match: str = "exact"
    split: str = "internal split of the log"

    def report(self) -> dict:
        d = asdict(self)
        d.pop("code")
        return d


# ------------------------------------------------------------------- prompts
def signature(fn: str, example_input: Any) -> str:
    if isinstance(example_input, list):
        return f"{fn}({', '.join(f'arg{i + 1}' for i in range(len(example_input)))})"
    return f"{fn}(text)"


def synth_prompt(task: str, sig: str, context: str, examples: list) -> str:
    ex = "\n".join("%r => %r" % (x, y) for x, y in examples)
    return f"""Write a Python 3 function `{sig}` that replaces a language-model call.
Task: {task}
It must return the correct answer, or None when it is not confident. Abstaining is cheap (the language model handles those inputs); a wrong answer is not.
Rules: standard library only (re is fine); no network; deterministic; it will run on thousands of NEW inputs, so generalise from the examples instead of memorising them; keep it under 250 lines.
{context}
Examples (input => answer):
{ex}

Reply with one Python code block containing the complete module and nothing else."""


def fix_prompt(code: str, fn: str, wrong: list, abstained: list) -> str:
    w = "\n".join("%r => expected %r, got %r" % (x, y, p) for x, y, p in wrong)
    a = "\n".join("%r => expected %r" % (x, y) for x, y in abstained)
    return f"""Here is a Python function `{fn}` that replaces a language-model call:

```python
{code}
```

On the training examples it gave these WRONG answers:
{w or '(none)'}

and abstained (returned None) on these, which it should cover when it can do so reliably:
{a or '(none)'}

Improve it: fix the wrong answers, cover more of the abstained inputs by generalising (never by hard-coding a single example), and keep abstaining when unsure. Reply with one Python code block containing the complete module and nothing else."""


def extract_code(text: str) -> str:
    blocks = re.findall(r"```(?:python)?\n(.*?)```", text, re.S)
    return max(blocks, key=len) if blocks else text


# ------------------------------------------------------------------- running
_RUNNER = r'''
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


def run_code(code: str, fn: str, inputs: list, timeout: int = 120) -> list:
    """Run `fn` from `code` on every input in a subprocess. Any failure, crash or timeout gives None
    (an abstention) for every input, never an exception."""
    kw: dict = {}
    if os.name == "posix":
        import resource

        def limits():
            resource.setrlimit(resource.RLIMIT_CPU, (timeout, timeout))
            resource.setrlimit(resource.RLIMIT_FSIZE, (10_000_000, 10_000_000))

        kw["preexec_fn"] = limits
    with tempfile.TemporaryDirectory(prefix="carpool-compile-") as td:
        Path(td, "m.py").write_text(code)
        Path(td, "r.py").write_text(_RUNNER)
        try:
            p = subprocess.run([sys.executable, "-I", str(Path(td, "r.py")), str(Path(td, "m.py")), fn],
                               input=json.dumps(inputs), capture_output=True, text=True,
                               timeout=timeout, cwd=td, **kw)
            out = json.loads(p.stdout)
        except Exception:  # noqa: BLE001 - timeouts, crashes and bad output all mean "abstain"
            return [None] * len(inputs)
    return out if isinstance(out, list) and len(out) == len(inputs) else [None] * len(inputs)


# ------------------------------------------------------------------- compile
def compile_log(pairs: list, backend: Callable, task: str, *, fn: str = "solve", context: str = "",
                val_frac: float = 0.3, seed: int = 1, repairs: int = 2, gate: Optional[Gate] = None,
                timeout: int = 120, match: str = "exact", held_out: Optional[list] = None) -> CompileResult:
    """pairs: logged (input, output) pairs. backend: any carpool backend (a callable prompt -> Completion).

    Learns from the training part, repairs against the training part only, and judges on the held-out part.
    By default the held-out part is a random share of `pairs`; pass `held_out` (more logged pairs, for example
    from a later period) to judge on calls that were kept apart from the start. `match` is how an answer is
    compared with the logged one: "exact" or "loose" (see `same_answer`)."""
    gate = gate or Gate()
    if match not in ("exact", "loose"):
        raise ValueError("match must be 'exact' or 'loose'")
    pairs = [(x, y) for x, y in pairs]
    if any(y is None for _, y in pairs + list(held_out or [])):
        raise ValueError("logged outputs must not be null: None is how the function abstains")
    if held_out is None:
        rng = random.Random(seed)
        rng.shuffle(pairs)
        n_val = max(1, round(len(pairs) * val_frac))
        val, train = pairs[:n_val], pairs[n_val:]
    else:
        val, train = [(x, y) for x, y in held_out], pairs
    if len(val) < MIN_HELD_OUT:
        raise ValueError(f"need at least {MIN_HELD_OUT} held-out calls to judge a function, got {len(val)}; "
                         "log more calls or raise --val")
    sig = signature(fn, train[0][0])
    cost, calls = 0.0, 0

    def ask(prompt: str) -> str:
        nonlocal cost, calls
        c = backend(prompt)
        cost += float(getattr(c, "cost_usd", 0.0) or 0.0)
        calls += 1
        return c.text

    code = extract_code(ask(synth_prompt(task, sig, context, train)))
    for _ in range(repairs):
        res = run_code(code, fn, [x for x, _ in train], timeout)
        wrong = [(x, y, p) for (x, y), p in zip(train, res) if p is not None and not same_answer(p, y, match)][:25]
        abstained = [(x, y) for (x, y), p in zip(train, res) if p is None][:15]
        if not wrong and len(abstained) <= 0.1 * len(train):
            break
        code = extract_code(ask(fix_prompt(code, fn, wrong, abstained)))

    outs = run_code(code, fn, [x for x, _ in val], timeout)
    answered = [i for i, o in enumerate(outs) if o is not None]
    agreed = sum(1 for i in answered if same_answer(outs[i], val[i][1], match))
    coverage = len(answered) / len(val)
    precision = agreed / len(answered) if answered else None
    lb = wilson_lower(agreed, len(answered))
    reasons = []
    if precision is None or precision < gate.min_precision:
        reasons.append("precision %s is below %.0f%%" % ("n/a" if precision is None else f"{precision:.1%}", 100 * gate.min_precision))
    if lb < gate.min_lower_bound:
        reasons.append(f"95% lower bound {lb:.1%} is below {gate.min_lower_bound:.0%}")
    if coverage < gate.min_coverage:
        reasons.append(f"it answers only {coverage:.0%} of calls (minimum {gate.min_coverage:.0%})")
    return CompileResult(
        code=code, promoted=not reasons, reason="passed the gate" if not reasons else "; ".join(reasons),
        fn=fn, task=task, n_train=len(train), n_held_out=len(val), answered=len(answered), agreed=agreed,
        coverage=coverage, precision=precision, lower_bound=lb, cost_usd=cost, calls=calls, gate=asdict(gate),
        match=match, split="internal split of the log" if held_out is None else "held-out calls supplied separately")


def render(result: CompileResult) -> str:
    """The file to save: the code with a header saying where it came from and how it was checked."""
    from . import __version__

    p = "n/a" if result.precision is None else f"{result.precision:.1%}"
    head = (f'"""Compiled by carpool {__version__} from {result.n_train + result.n_held_out} logged calls.\n'
            f"Task: {result.task}\n"
            f"Held-out check ({result.split}, {result.match} match): answered {result.answered} of {result.n_held_out}; "
            f"{result.agreed} matched the logged output (precision {p}, 95% lower bound {result.lower_bound:.1%}).\n"
            f"Returns None when unsure: send those inputs to the model.\n"
            f'Read this file before you deploy it: it runs as you.\n"""\n')
    return head + result.code.lstrip("\n")


def load_compiled(path: str, fn: str = "solve") -> Callable:
    """Import a compiled file and return its function."""
    spec = importlib.util.spec_from_file_location("carpool_compiled", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, fn)


class Cascade:
    """Code first, model for what the code abstains on. `hits` and `misses` count which path each call took."""

    def __init__(self, fast: Callable, slow: Callable):
        self.fast, self.slow, self.hits, self.misses = fast, slow, 0, 0

    def __call__(self, *args):
        try:
            out = self.fast(*args)
        except Exception:  # noqa: BLE001 - a crashing compiled function counts as an abstention
            out = None
        if out is not None:
            self.hits += 1
            return out
        self.misses += 1
        return self.slow(*args)

    @property
    def coverage(self) -> float:
        n = self.hits + self.misses
        return self.hits / n if n else 0.0


# ------------------------------------------------------- compiled code + carpool
def task_text(task) -> str:
    """The evidence of a task as one string, for functions that read it. Sources that carpool would not trust
    (drafts, superseded copies) are marked, so a function can learn to skip them."""
    return "\n".join(("[untrusted] " if not s.trusted else "") + s.text for s in task.sources)


# Added to the prompt when the examples are carpool tasks (`carpool compile --answers`).
TASK_CONTEXT = ("The examples are questions answered from evidence. Argument 2 of the function is the evidence text, "
                "one passage per line. Passages that start with [untrusted] are drafts or superseded copies: the "
                "answer must never come from them. Give each answer in the same form as the examples.")


def task_input(task, n_args: int):
    """What a compiled function is called with for a task: the question alone (one argument), or the question and
    the evidence text (two arguments). This is also how `carpool compile --answers` builds its examples."""
    return task.question if n_args <= 1 else [task.question, task_text(task)]


class CompiledTasks:
    """A compiled function applied to carpool tasks. Runs in the same subprocess runner as the gate, so a runaway
    function costs a timeout, not a hang. Returns None for every task it abstains on."""

    def __init__(self, code: str, fn: str = "solve", timeout: int = 120):
        self.code, self.fn, self.timeout = code, fn, timeout
        try:  # read the signature without running the code
            node = next(n for n in ast.parse(code).body if isinstance(n, ast.FunctionDef) and n.name == fn)
        except (StopIteration, SyntaxError) as e:
            raise ValueError(f"cannot find function '{fn}' in the compiled code") from e
        params = node.args.posonlyargs + node.args.args
        self.n_args = max(1, min(len(params), 2))

    @classmethod
    def from_file(cls, path: str, fn: str = "solve", timeout: int = 120) -> "CompiledTasks":
        return cls(Path(path).read_text(), fn, timeout)

    def answers(self, tasks: list) -> list:
        outs = run_code(self.code, self.fn, [task_input(t, self.n_args) for t in tasks], self.timeout)
        return [None if o is None or not str(o).strip() else str(o) for o in outs]
