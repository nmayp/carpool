"""Exact operations: zero model calls.

If an operation is exact and repeatable (lookup, count, filter, sum), run it
against the source with a contract check. Don't pay a model to read a table.
Index construction cost is measured so you can report it honestly.
"""
from __future__ import annotations

import time
from collections import Counter
from typing import Any, Iterable


class ContractError(ValueError):
    pass


class Index:
    def __init__(self, records: Iterable[dict], key: str, required: tuple[str, ...] = ()):
        t0 = time.perf_counter()
        self.key, self.required = key, required
        self._by_key: dict[Any, dict] = {}
        self._records = list(records)
        for r in self._records:
            if key not in r:
                raise ContractError(f"record without '{key}': {r}")
            k = r[key]
            if k in self._by_key:
                raise ContractError(f"duplicate {key} {k!r}: identity is not unique")
            missing = [f for f in required if r.get(f) in (None, "")]
            if missing:
                raise ContractError(f"{key} {k!r} missing required {missing}")
            self._by_key[k] = r
        self._counts: dict[tuple[str, ...], Counter] = {}
        self.build_seconds = time.perf_counter() - t0

    def get(self, k: Any, field: str | None = None) -> Any:
        r = self._by_key.get(k)
        if r is None:
            raise KeyError(f"no record with {self.key}={k!r}")
        return r if field is None else r[field]

    def count(self, **where: Any) -> int:
        fields = tuple(sorted(where))
        if fields not in self._counts:
            self._counts[fields] = Counter(tuple(r.get(f) for f in fields) for r in self._records)
        return self._counts[fields][tuple(where[f] for f in fields)]

    def __len__(self) -> int:
        return len(self._by_key)
