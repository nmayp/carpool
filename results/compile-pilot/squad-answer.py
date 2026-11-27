import re
from difflib import SequenceMatcher

_STOP = set("""a an the of in on at to for from by with and or is are was were be been being what which who whom whose when where why how
did does do has have had that this these those it its as into than then there their his her they he she not no can could would should
may might will shall about over under between during also such some any many much more most other one two located called named known""".split())


def _stem(w):
    w = w.lower()
    if w.endswith("'s") and len(w) > 3:
        w = w[:-2]
    for suf in ("ing", "ed", "es", "s", "ly"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[:-len(suf)]
    return w


def _words(s):
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", s)


def _content(s):
    return {_stem(w) for w in _words(s) if w.lower() not in _STOP}


def _in(w, sc):
    """Membership with light fuzzy matching for long words (typos / inflection)."""
    if w in sc:
        return True
    if len(w) >= 6:
        for x in sc:
            if (len(x) >= 5 and abs(len(x) - len(w)) <= 1 and x[0] == w[0]
                    and SequenceMatcher(None, w, x).ratio() >= 0.88):
                return True
    return False


def _sentences(passages):
    if isinstance(passages, str):
        passages = [passages]
    out = []
    for p in passages:
        for line in str(p).split("\n"):
            for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"'(])", line):
                s = s.strip()
                if len(s) > 15:
                    out.append(s)
    return out


_UNITS = {"kilometers", "kilometres", "miles", "dollars", "inches", "feet", "meters", "metres", "years",
          "people", "percent", "days", "hours", "km", "tons", "tonnes", "pounds", "acres", "men"}
_MAG = {"million", "billion", "trillion", "thousand", "hundred"}
_QUAL = re.compile(r"\b(over|nearly|almost|about|approximately|around|roughly)\s+$", re.I)
_NW = (r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|"
       r"forty|fifty|hundred)")


def _numbers(sent, qnums):
    res = []
    for m in re.finditer(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(%?)(?:\s+([A-Za-z]+))?", sent):
        num, pct, nxt = m.group(1), m.group(2), m.group(3)
        if num.rstrip(",") in qnums:
            continue
        txt = num.rstrip(",") + pct
        if nxt:
            n = nxt.lower()
            if n in _MAG:
                txt += " " + nxt
                m2 = re.match(r"\s+([A-Za-z]+)", sent[m.end():])
                if m2 and m2.group(1).lower() in _UNITS:
                    txt += " " + m2.group(1)
            elif n in _UNITS:
                txt += " " + nxt
        qm = _QUAL.search(sent[:m.start()])
        if qm:
            txt = qm.group(1).lower() + " " + txt
        res.append((txt, m.start()))
    for m in re.finditer(r"\b(" + _NW + r")\s+([A-Za-z]+)\b", sent, re.I):
        if m.group(2).lower() in _UNITS:
            txt = m.group(1) + " " + m.group(2)
            qm = _QUAL.search(sent[:m.start()])
            if qm:
                txt = qm.group(1).lower() + " " + txt
            res.append((txt, m.start()))
    return res


def _years(sent, qlow):
    res = []
    for m in re.finditer(r"\b(1\d{3}|20\d{2})\b", sent):
        y = m.group(1)
        if y in qlow:
            continue
        tail = sent[m.end():m.end() + 4]
        t = y
        if re.match(r"\s*(BP|BC|AD|BCE|CE)\b", tail):
            t = y + " " + tail.strip().split()[0]
        res.append((t, m.start()))
    return res


def _near(sent, pos, qc):
    ws = _words(sent[:pos])[-6:]
    return any(w.lower() not in _STOP and _in(_stem(w), qc) for w in ws)


def _select(cands, ambiguous, qc):
    texts = list(dict.fromkeys(c[0] for c in cands))
    if not texts:
        return None
    if not ambiguous and len(texts) == 1:
        return texts[0]
    near = list(dict.fromkeys(t for t, s, p in cands if _near(s, p, qc)))
    if len(near) == 1:
        return near[0]
    return None


def _guard(q, sent):
    sc = _content(sent)
    for w in _words(q)[1:]:
        if w[0].isupper() and w.lower() not in _STOP and not _in(_stem(w), sc):
            return False
    return True


_CAP = r"\b[A-Z][\w'\-\.]*(?:\s+(?:de |van |von |of )?[A-Z][\w'\-\.]*)*"
_AUXW = {"is", "was", "were", "are", "been", "be", "by", "has", "had", "have", "being", "to", "that",
         "which", "who", "and", "or", "but", "he", "she", "it", "they", "will", "would", "did", "does", "do"}
_NUMQ = re.compile(r"^\s*(?:what|which)\s+(?:is|was|are|were)\s+the\s+(?:[a-z\-]+\s+){0,2}?"
                   r"(?:rate|percentage|percent|distance|amount|total|population|age|length|height|cost|price|"
                   r"speed|depth|weight|width|temperature)\b")
_PREPB = {"to", "in", "on", "for", "by", "with", "from", "at", "as", "that", "into", "over"}


def _adjacent(sent, s, e, qc):
    nm = re.match(r"[\s,]*([A-Za-z0-9][\w'\-]*)", sent[e:])
    pm = re.search(r"([A-Za-z0-9][\w'\-]*)[\s,]*$", sent[:s])
    for x in (nm, pm):
        if x and x.group(1).lower() not in _STOP and _in(_stem(x.group(1)), qc):
            return True
    return False


def _prev_in_q(sent, s, qc):
    ws = _words(sent[:s])
    if not ws:
        return False
    i = len(ws) - 1
    if ws[i].lower() in ("the", "a", "an") and i > 0:
        i -= 1
    w = ws[i]
    return w.lower() not in _STOP and _in(_stem(w), qc)


def _clean_phrase(t):
    t = t.strip()
    t = re.sub(r"^(?:the|a|an)\s+", "", t, flags=re.I)
    if not t or len(t.split()) > 8:
        return None
    return t


def _subject_phrase(sent, noun):
    toks = sent.split()
    if len(toks) < 5 or toks[0].lower() not in ("the", "a", "an"):
        return None
    if _stem(toks[1].strip(",.;:")) != _stem(noun) or toks[2].lower() != "of":
        return None
    j = 3
    out = toks[:3]
    while j < len(toks) and j - 3 < 4:
        t = toks[j]
        tl = t.lower().strip(",.;:")
        if tl in _STOP or tl in _AUXW or tl in _PREPB:
            break
        nxt = toks[j + 1].lower().strip(",.;:") if j + 1 < len(toks) else ""
        if j > 3 and nxt in _PREPB:
            break
        out.append(t.strip(",.;:"))
        if t[-1] in ",.;:":
            break
        j += 1
    if len(out) < 4:
        return None
    return " ".join(out)


def answer(question, passages):
    try:
        q = question.strip()
        ql = q.lower()
        qc = _content(q)
        if not qc:
            return None
        sents = _sentences(passages)
        scored = []
        for s in sents:
            sc = _content(s)
            hit = sum(1 for w in qc if _in(w, sc))
            scored.append((hit / len(qc), s))
        if not scored:
            return None
        scored.sort(key=lambda x: -x[0])
        best, sent = scored[0]
        if best < 0.6:
            return None
        ties = []
        for sc_, s in scored:
            if sc_ >= best - 0.01 and s not in ties:
                ties.append(s)
        ambiguous = len(ties) > 1 and best < 0.9
        qlow = ql
        qtoks = {w.lower() for w in _words(q)}
        qnums = {n.rstrip(",") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", q)}

        # ---- numeric questions
        howmany = re.match(r"^\s*(how many|how much)\b", ql)
        whatnum = re.match(r"^\s*(what|which)\s+number\b", ql) or _NUMQ.match(ql)
        if howmany or whatnum:
            if whatnum and not howmany and not _guard(q, sent):
                return None
            pool = ties if ambiguous else [sent]
            cands = []
            for s in pool:
                for t, p in _numbers(s, qnums):
                    cands.append((t, s, p))
            r = _select(cands, ambiguous, qc)
            if r is None and not ambiguous and len(cands) == 2:
                if re.search(r"\bbetween\s+[\d,\.]+\s+and\s+[\d,\.]+", sent):
                    ordered = sorted(cands, key=lambda c: c[2])
                    return ordered[1][0]
            return r

        # ---- year questions
        yearq = re.search(r"\b(what|which) year\b", ql) or (
            re.search(r"\bwhen\b", ql) and not re.search(r"\bwhen\s+\w+\s+(and|or)\b", ql)
            and not re.search(r"\bwhat\b", ql))
        if yearq:
            pool = ties if ambiguous else [sent]
            cands = []
            for s in pool:
                for t, p in _years(s, qlow):
                    cands.append((t, s, p))
            return _select(cands, ambiguous, qc)

        if ambiguous:
            return None

        # ---- nickname / term questions answered by a quoted phrase
        if (re.match(r"^\s*(what|which)\b", ql)
                and re.search(r"\b(nickname|nicknamed|called|named|known as|term|title|label|labeled|labelled)\b", ql)):
            quotes = re.findall(r'["\u201c]([^"\u201d]{2,60})["\u201d]', sent)
            quotes = [x.strip() for x in quotes if x.strip().lower() not in ql and len(x.split()) <= 8]
            quotes = list(dict.fromkeys(quotes))
            if len(quotes) == 1:
                return quotes[0]

        # ---- where questions
        if re.match(r"^\s*where\b", ql):
            m = re.search(r"\b(?:is|are|was|were)\s+(?:also\s+|primarily\s+|mostly\s+)?"
                          r"(?:located\s+|situated\s+|found\s+)?(?:in|on|at|near|along|within|inside)\s+"
                          r"(.+?)\s*(?:[.;,:!?]|$)", sent)
            if m:
                t = _clean_phrase(m.group(1))
                if t and len(t.split()) <= 6:
                    return t
            return None

        # ---- "what ... become"
        if re.search(r"\bwhat\b.*\bbecom(?:e|es|ing)\b", ql):
            m = re.search(r"\b(?:become|becomes|became)\s+(.+?)\s*(?:[.;:!?,]|$)", sent)
            if m:
                return _clean_phrase(m.group(1))

        # ---- "what produces/causes ..."
        if re.match(r"^\s*(?:what|which)\s+(?:produce|produces|cause|causes|caused|creates|created|generates)\b", ql):
            for pat in (r"\bresult of\s+(.+?)\s*(?:[.;,:!?]|$)",
                        r"\b(?:caused|produced|created|generated) by\s+(.+?)\s*(?:[.;,:!?]|$)"):
                m = re.search(pat, sent)
                if m:
                    t = _clean_phrase(m.group(1))
                    if t and len(t.split()) <= 6:
                        return t

        # ---- "what sort/kind of X"
        mk = re.search(r"\b(?:what|which)\s+(?:sort|kind|type|form)s?\s+of\s+([a-z\-]+)", ql)
        if mk:
            noun = mk.group(1)
            found = []
            for m in re.finditer(r"\b([A-Za-z][\w\-]*)\s+" + re.escape(noun) + r"\b", sent):
                w = m.group(1)
                if w.lower() in _STOP or w.lower() in qtoks or _in(_stem(w), qc):
                    continue
                found.append(w)
            found = list(dict.fromkeys(found))
            if len(found) == 1:
                w = found[0]
                ma = re.search(r",\s+((?:a|an)\s+" + re.escape(w) + r"\s+" + re.escape(noun) + r")\s*[,.;]",
                               sent, re.I)
                if ma:
                    return ma.group(1)
                return w
            return None

        # ---- who questions
        if re.match(r"^\s*who\b", ql):
            cands = []
            for m in re.finditer(_CAP, sent):
                t = m.group(0).strip(" .")
                if m.start() == 0:
                    continue
                ws = [w for w in t.split() if w.lower() not in qlow.split()]
                if not ws or any(_stem(w) in qc for w in t.split()):
                    continue
                if t.lower() in _STOP:
                    continue
                cands.append((t, m.start(), m.end()))
            uniq = list(dict.fromkeys(c[0] for c in cands))
            if len(uniq) == 1:
                return uniq[0]
            if len(uniq) > 1:
                adj = list(dict.fromkeys(t for t, s, e in cands if _adjacent(sent, s, e, qc)))
                if len(adj) == 1:
                    return adj[0]
                return None
            mv = re.match(r"^\s*who\s+([a-z]+)", ql)
            if mv and mv.group(1) not in _AUXW and mv.group(1) not in _STOP:
                m = re.search(r"\b" + re.escape(mv.group(1)) + r"\b", sent, re.I)
                if m:
                    pre = sent[:m.start()].strip()
                    pw = pre.split()
                    if (pre and pre[0].isupper() and "," not in pre and len(pw) <= 8
                            and not any(w.lower() in _AUXW for w in pw)
                            and not any(_in(_stem(w), qc) for w in pw if w.lower() not in _STOP)):
                        return pre
            return None

        # ---- what/which
        if re.match(r"^\s*(what|which)\b", ql):
            # sentence subject: "The <noun> of ... <verb> ..."
            mw = re.match(r"^\s*(?:what|which)\s+([a-z\-]+)\b", ql)
            if mw and mw.group(1) not in _STOP and mw.group(1) not in ("number", "year", "type", "kind", "sort", "form"):
                sp = _subject_phrase(sent, mw.group(1))
                if sp:
                    return sp
            cands = []
            for m in re.finditer(_CAP, sent):
                if m.start() == 0:
                    continue
                t = m.group(0).strip(" .")
                toks = t.split()
                if not toks:
                    continue
                if t.lower() in _STOP:
                    continue
                if all(w.lower() in qtoks or _in(_stem(w), qc) for w in toks):
                    continue
                if not _prev_in_q(sent, m.start(), qc):
                    continue
                cands.append(t)
            cands = list(dict.fromkeys(cands))
            if len(cands) == 1:
                return cands[0]
            return None
        return None
    except Exception:
        return None
