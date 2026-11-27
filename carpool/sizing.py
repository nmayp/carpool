"""Learn the group size from what actually happens.

The per-task cost of groups of size b is

    g(b) = H/b + E + f(b) (H + E),   with crowding  f(b) = f0 + phi (b - 1),

which is lowest at  b* = sqrt(H / (phi (H + E))).

H (the fixed part of every request) and E (one task's part) are measured from
the calls. phi is learned from redo counts: if a group of size b produces
N redos, E[N] = b f(b) ~= phi * b (b - 1) when f0 is small, so the
maximum-likelihood estimate over several groups (Poisson model) is
sum of redos / sum of b (b - 1). We add a prior worth PRIOR_REDOS redos over
PRIOR_REDOS / PRIOR_PHI task pairs (a typical crowding rate of 0.001):

    phi_hat = (sum of redos + 0.5) / (sum of b (b - 1) + 500).

With no data this gives phi = 0.001 (b* around 30 for a large H); every real
redo pulls the estimate up quickly. Growth is limited to doubling per round,
so one lucky group cannot push the size far beyond what has been tested.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .backends import Backend, estimate_tokens
from .batch import build_batch_prompt
from .ledger import Ledger
from .router import Result, run_carpool
from .types import Task


PRIOR_PHI = 0.001
PRIOR_REDOS = 0.5


def best_group_size(H: float, E: float, phi: float, lo: int = 1, hi: int = 48) -> int:
    """Whole-number minimiser of g(b) = H/b + phi*(b-1)*(H+E) on [lo, hi]."""
    if phi <= 0:
        return hi
    b = math.sqrt(H / (phi * (H + E)))

    def g(k: int) -> float:
        return H / k + phi * (k - 1) * (H + E)

    cands = {max(lo, min(hi, math.floor(b))), max(lo, min(hi, math.ceil(b)))}
    return min(cands, key=g)


@dataclass
class CrowdingEstimate:
    redos: int = 0
    pairs: int = 0  # sum of b (b - 1)
    H_samples: list[float] = field(default_factory=list)
    E_samples: list[float] = field(default_factory=list)

    def observe(self, b: int, redos: int, H: float | None = None, E: float | None = None) -> None:
        self.redos += redos
        self.pairs += b * (b - 1)
        if H is not None:
            self.H_samples.append(H)
        if E is not None:
            self.E_samples.append(E)

    @property
    def phi(self) -> float:
        return (self.redos + PRIOR_REDOS) / (self.pairs + PRIOR_REDOS / PRIOR_PHI)

    @property
    def H(self) -> float:
        return sorted(self.H_samples)[len(self.H_samples) // 2] if self.H_samples else 20_000.0

    @property
    def E(self) -> float:
        return sorted(self.E_samples)[len(self.E_samples) // 2] if self.E_samples else 500.0

    def recommend(self, current: int, hi: int = 48) -> int:
        b = best_group_size(self.H, self.E, self.phi, 1, hi)
        return max(1, min(b, 2 * current))


def run_adaptive(tasks: list[Task], backend: Backend, *, start: int = 8, max_size: int = 48,
                 compact: bool = True) -> tuple[Result, list[int]]:
    """Process tasks in rounds, re-estimating the best group size after each round.
    Returns the combined result and the group sizes used."""
    est, sizes = CrowdingEstimate(), []
    ledger, answers = Ledger(), {}
    k, i = max(1, start), 0
    while i < len(tasks):
        chunk = tasks[i: i + k]
        res = run_carpool(chunk, backend, compact=compact, max_batch=len(chunk))
        sizes.append(len(chunk))
        answers.update(res.answers)
        ledger.calls += res.ledger.calls
        ledger.local_seconds += res.ledger.local_seconds
        # The first call is the group request: its input minus the prompt text is the fixed part H.
        group_call = res.ledger.calls[0]
        prompt_tokens = estimate_tokens(build_batch_prompt([(t, t.sources) for t in chunk]))
        H = max(group_call.input_tokens - prompt_tokens, 0) if group_call.input_tokens else None
        E = prompt_tokens / len(chunk)
        est.observe(len(chunk), len(res.fallbacks), H, E)
        i += len(chunk)
        k = est.recommend(len(chunk), max_size)
    return Result(answers, ledger), sizes
