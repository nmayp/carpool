"""Selective compact evidence: preflight before the call, verify after it.

Policy from the paper (section 1):
  * Try compact input only when a preflight identifies ONE eligible source
    with a clear lead. Otherwise send full evidence immediately.
  * Verify the compact answer against the evidence. If verification fails,
    fall back to the original full-evidence request.
"""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Optional

from .types import Source, Task

STOPWORDS = frozenset(
    """a an and are as at be by did do does for from has have how in is it its
    of on or that the this to was were what when where which who whom whose why
    with after before many much""".split()
)
ARTICLES = ("the ", "a ", "an ")
_WORD = re.compile(r"[\w']+", re.UNICODE)
_NUM = re.compile(r"\d+(?:[.,]\d+)?")


UNITS = frozenset(
    """second seconds minute minutes hour hours day days week weeks month months year years
    percent % km kilometres kilometers mile miles m metres meters kg kilograms tonnes tons lb
    pounds people persons men deaths million billion thousand times degrees""".split()
)


def stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


def tokenize(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.casefold()) if w not in STOPWORDS]


def stems(text: str) -> set[str]:
    return {stem(w) for w in tokenize(text)}


def normalize(text: str) -> str:
    """Unicode, case, whitespace, terminal punctuation, optional leading article."""
    t = unicodedata.normalize("NFKC", text).casefold().strip()
    t = re.sub(r"\s+", " ", t)
    t = t.rstrip(".!?;:,").strip().strip("\"'")
    for art in ARTICLES:
        if t.startswith(art):
            t = t[len(art):]
            break
    return t


def bm25_scores(query: str, docs: list[str], k1: float = 1.5, b: float = 0.75) -> list[float]:
    toks = [tokenize(d) for d in docs]
    n = len(toks)
    if n == 0:
        return []
    avgdl = sum(len(t) for t in toks) / n or 1.0
    q = set(tokenize(query))
    scores = []
    for t in toks:
        s = 0.0
        dl = len(t)
        for term in q:
            tf = t.count(term)
            if not tf:
                continue
            df = sum(1 for d in toks if term in d)
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            s += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / avgdl))
        scores.append(s)
    return scores


def preflight(task: Task, margin: float = 0.40) -> Optional[Source]:
    """Return the single source eligible for compact input, or None.

    Eligible means: trusted, top BM25 score > 0, and leading the runner-up by
    at least `margin` of the top score. Untrusted sources still count as
    competitors, so a draft that looks just like the approved copy blocks the
    compact route instead of being silently ignored.
    """
    if len(task.sources) < 2:
        return None  # nothing to compress
    scores = bm25_scores(task.question, [s.text for s in task.sources])
    ranked = sorted(zip(scores, task.sources), key=lambda p: p[0], reverse=True)
    (top, best), (second, _) = ranked[0], ranked[1]
    if top <= 0 or not best.trusted:
        return None
    if (top - second) < margin * top:
        return None
    return best


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def verify(task: Task, answer: str, source: Source) -> Optional[str]:
    """Check a compact answer against its evidence. Returns a rejection reason
    or None if the answer is accepted.

    Checks (paper section 2): source occurrence, question relevance of the
    passage holding the answer, ambiguous numeric spans, and likely incomplete
    proper names ("London" when the source says "London Exhibition").
    """
    a = normalize(answer)
    if not a:
        return "empty answer"
    src_norm = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", source.text).casefold())
    if a not in src_norm:
        return "answer not found in cited source"

    # Relevance: the passage containing the answer must share a content term
    # with the question. Look at the answer's sentence plus the one before it,
    # because evidence often builds forward ("X began on 2 June. It was signed
    # off 12 days later.") — the paper's R07 false rejection.
    q_terms = stems(task.question)
    sents = _sentences(source.text)
    hits = [i for i, s in enumerate(sents) if a in normalize(s) or a in s.casefold()]
    if hits and q_terms:
        relevant = False
        for i in hits:
            window = " ".join(sents[max(0, i - 1): i + 1])
            if q_terms & stems(window):
                relevant = True
                break
        if not relevant:
            return "answer passage does not mention the question terms"

    # Ambiguous numeric span: a bare number that appears with different units.
    if _NUM.fullmatch(a):
        units = {u for u in re.findall(rf"\b{re.escape(a)}\s*([a-z%]+)", src_norm) if u in UNITS}
        if len(units) > 1:
            return f"ambiguous number ({', '.join(sorted(units))})"

    # Proper-name continuation: answer ends in a capitalised word that the
    # source continues with another capitalised word.
    raw = answer.strip().rstrip(".!?")
    if raw[:1].isupper():
        for m in re.finditer(re.escape(raw), source.text):
            tail = source.text[m.end():]
            nxt = re.match(r"\s+([A-Z][\w'-]*)", tail)
            if nxt and not re.match(r"[.!?]", tail):
                return f"name looks incomplete (source continues with '{nxt.group(1)}')"
    return None
