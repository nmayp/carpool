"""Core data types. Stdlib only."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Source:
    """One piece of evidence. `trusted=False` marks drafts/stale copies the
    compact route must never pick on its own."""

    id: str
    text: str
    trusted: bool = True


@dataclass
class Task:
    id: str
    question: str
    sources: list[Source]
    answers: list[str] = field(default_factory=list)  # optional gold, for scoring only

    def source(self, source_id: str) -> Optional[Source]:
        for s in self.sources:
            if s.id == source_id:
                return s
        return None

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        return cls(
            id=str(d["id"]),
            question=d["question"],
            sources=[
                Source(id=str(s["id"]), text=s["text"], trusted=s.get("trusted", True))
                for s in d["sources"]
            ],
            answers=list(d.get("answers", [])),
        )


@dataclass
class Completion:
    text: str
    input_tokens: int = 0  # total input, cached included
    cached_input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    estimated: bool = False  # True when the backend cannot report real usage
    cost_usd: float = 0.0  # when the backend reports a price (list price, not your bill)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def uncached_tokens(self) -> int:
        return self.input_tokens - self.cached_input_tokens + self.output_tokens


@dataclass
class Answer:
    task_id: str
    answer: str
    source_id: str
    route: str  # "batch-compact" | "batch-full" | "single-full" | "exact"
    fallback_reason: Optional[str] = None
