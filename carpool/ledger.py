"""Resource and quality accounting.

R_p = sum_i C_p,i / sum_i A_p,i  — total resources per ACCEPTED answer,
including checks, failed attempts and retries. A shared call counts once.
A paired regression = the original route succeeded and the optimized one
failed on the same case. Report regressions even when gains offset them.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .compact import normalize
from .types import Answer, Completion, Task


@dataclass
class Ledger:
    calls: list[Completion] = field(default_factory=list)
    local_seconds: float = 0.0  # routing, checks

    def add(self, c: Completion) -> Completion:
        self.calls.append(c)
        return c

    @property
    def n_calls(self) -> int:
        return len(self.calls)

    @property
    def input_tokens(self) -> int:
        return sum(c.input_tokens for c in self.calls)

    @property
    def cached_input_tokens(self) -> int:
        return sum(c.cached_input_tokens for c in self.calls)

    @property
    def output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.calls)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def uncached_tokens(self) -> int:
        return self.input_tokens - self.cached_input_tokens + self.output_tokens

    @property
    def seconds(self) -> float:
        return sum(c.seconds for c in self.calls) + self.local_seconds

    @property
    def cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.calls)

    @property
    def estimated(self) -> bool:
        return any(c.estimated for c in self.calls)


def is_accepted(task: Task, answer: Answer | None) -> bool:
    if answer is None or not task.answers:
        return False
    got = normalize(answer.answer)
    return any(got == normalize(g) for g in task.answers)


@dataclass
class Report:
    name: str
    accepted: int
    total: int
    ledger: Ledger

    def per_accepted(self, value: float) -> float:
        return value / self.accepted if self.accepted else float("inf")

    def row(self) -> dict:
        L = self.ledger
        return {
            "route": self.name,
            "accepted": f"{self.accepted}/{self.total}",
            "calls": L.n_calls,
            "total_tokens": L.total_tokens,
            "uncached+output": L.uncached_tokens,
            "tokens/accepted": round(self.per_accepted(L.total_tokens), 1),
            "uncached/accepted": round(self.per_accepted(L.uncached_tokens), 1),
            "seconds": round(L.seconds, 2),
            "usd/accepted": round(self.per_accepted(L.cost_usd), 4),
        }


def score(name: str, tasks: list[Task], answers: dict[str, Answer], ledger: Ledger) -> Report:
    acc = sum(is_accepted(t, answers.get(t.id)) for t in tasks)
    return Report(name, acc, len(tasks), ledger)


def regressions(tasks: list[Task], original: dict[str, Answer], optimized: dict[str, Answer]) -> list[str]:
    return [
        t.id
        for t in tasks
        if is_accepted(t, original.get(t.id)) and not is_accepted(t, optimized.get(t.id))
    ]


def pct_change(before: float, after: float) -> float:
    return (after - before) / before * 100 if before else 0.0
