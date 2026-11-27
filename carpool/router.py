"""The carpool policy: preflight -> one grouped call -> validate -> individual fallback."""
from __future__ import annotations

import time
from dataclasses import dataclass

from .backends import Backend
from .batch import build_batch_prompt, build_single_prompt, parse_batch, parse_single
from .compact import preflight, verify
from .ledger import Ledger
from .types import Answer, Source, Task


@dataclass
class Result:
    answers: dict[str, Answer]
    ledger: Ledger

    @property
    def fallbacks(self) -> dict[str, str]:
        return {a.task_id: a.fallback_reason for a in self.answers.values() if a.fallback_reason}


def _map(fn, items, workers: int):
    if workers <= 1:
        return [fn(x) for x in items]
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(fn, items))


def run_individual(tasks: list[Task], backend: Backend, workers: int = 1) -> Result:
    """Baseline: one full-evidence call per task."""
    ledger = Ledger()
    answers = {a.task_id: a for a in _map(lambda t: _single(t, backend, ledger), tasks, workers)}
    return Result(answers, ledger)


def run_compact_individual(tasks: list[Task], backend: Backend, margin: float = 0.40, workers: int = 1) -> Result:
    """Ablation arm: compact evidence + verify + fallback, but still one call per task."""
    ledger = Ledger()

    def one(t: Task) -> Answer:
        chosen = preflight(t, margin)
        if chosen is None:
            return _single(t, backend, ledger)
        slim = Task(t.id, t.question, [chosen], t.answers)
        c = ledger.add(backend(build_single_prompt(slim)))
        parsed, err = parse_single(c.text, slim)
        why = err or verify(t, parsed[0], chosen)
        if why:
            return _single(t, backend, ledger, reason=why)
        return Answer(t.id, parsed[0], parsed[1], "single-compact")

    answers = {a.task_id: a for a in _map(one, tasks, workers)}
    return Result(answers, ledger)


def _single(task: Task, backend: Backend, ledger: Ledger, reason: str | None = None) -> Answer:
    c = ledger.add(backend(build_single_prompt(task)))
    parsed, _err = parse_single(c.text, task)
    ans, src = parsed if parsed else ("", "")
    return Answer(task.id, ans, src, "single-full", fallback_reason=reason)


def call_many(backend: Backend, prompts: list[str]) -> list:
    """Send several independent prompts. Backends with a `map` method (e.g. the
    Message Batches backend) submit them together; others are called in turn."""
    if not prompts:
        return []
    if hasattr(backend, "map"):
        return list(backend.map(prompts))
    return [backend(p) for p in prompts]


def apply_compiled(tasks: list[Task], compiled) -> tuple[dict[str, Answer], list[Task], float]:
    """Answer what compiled code can answer (no model call) and return the rest. Compiled answers are not
    re-verified: the function was checked against logged calls when it was compiled."""
    t0 = time.perf_counter()
    outs = compiled.answers(tasks)
    done = {t.id: Answer(t.id, o, "", "compiled") for t, o in zip(tasks, outs) if o is not None}
    rest = [t for t in tasks if t.id not in done]
    return done, rest, time.perf_counter() - t0


def run_carpool(
    tasks: list[Task],
    backend: Backend,
    *,
    compact: bool = True,
    margin: float = 0.40,
    max_batch: int = 24,
    compiled=None,
) -> Result:
    """`compiled`: a carpool.compile.CompiledTasks. Tasks it answers skip the model; the rest are carpooled."""
    ledger, answers = Ledger(), {}
    if compiled is not None:
        answers, tasks, secs = apply_compiled(tasks, compiled)
        ledger.local_seconds += secs

    # Phase 1: build every group prompt (preflight decides compact vs full evidence).
    t0 = time.perf_counter()
    groups = []
    for i in range(0, len(tasks), max_batch):
        group = tasks[i: i + max_batch]
        items: list[tuple[Task, list[Source]]] = []
        compacted: dict[str, Source] = {}
        for t in group:
            chosen = preflight(t, margin) if compact else None
            if chosen is not None:
                compacted[t.id] = chosen
                items.append((t, [chosen]))
            else:
                items.append((t, t.sources))
        groups.append((group, items, compacted))
    ledger.local_seconds += time.perf_counter() - t0

    # Phase 2: one request per group.
    replies = call_many(backend, [build_batch_prompt(items) for _g, items, _c in groups])

    # Phase 3: validate every answer; collect the tasks that must be redone.
    t0 = time.perf_counter()
    redo: list[tuple[Task, str]] = []
    for (group, items, compacted), c in zip(groups, replies):
        ledger.add(c)
        ok, bad = parse_batch(c.text, items)
        for tid, (ans, src) in ok.items():
            task = next(t for t in group if t.id == tid)
            cited = task.source(src)
            if cited is not None and not cited.trusted:
                # Shared prompts can make a model grab a draft/superseded copy it would
                # skip when asked alone. Never accept a grouped answer that cites one.
                bad[tid] = "cites an untrusted (draft/superseded) source"
                continue
            # Every grouped answer must be supported by the source it cites. A model that
            # juggles many tasks can mix up values between sources (e.g. a draft's number
            # attributed to the approved copy), not only when evidence was shortened.
            why = verify(task, ans, compacted.get(tid) or cited) if cited is not None else "no cited source"
            if why:
                bad[tid] = f"verifier: {why}"
                continue
            route = "batch-compact" if tid in compacted else "batch-full"
            answers[tid] = Answer(tid, ans, src, route)
        redo += [(t, bad[t.id]) for t in group if t.id in bad]
    ledger.local_seconds += time.perf_counter() - t0

    # Phase 4: redo failures individually with full evidence.
    for (t, reason), c in zip(redo, call_many(backend, [build_single_prompt(t) for t, _ in redo])):
        ledger.add(c)
        parsed, _err = parse_single(c.text, t)
        ans, src = parsed if parsed else ("", "")
        answers[t.id] = Answer(t.id, ans, src, "single-full", fallback_reason=reason)
    return Result(answers, ledger)
