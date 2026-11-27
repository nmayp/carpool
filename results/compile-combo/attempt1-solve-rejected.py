"""Compiled by carpool 0.2.0 from 120 logged calls.
Task: answer a question about a site's equipment from short evidence passages
Held-out check: answered 36 of 36; 31 matched the logged output (precision 86.1%, 95% lower bound 71.3%).
Returns None when unsure: send those inputs to the model.
Read this file before you deploy it: it runs as you.
"""
import re


def solve(arg1, arg2):
    q = (arg1 or "").strip()
    text = arg2 or ""
    lines = [l.strip() for l in text.split("\n")]
    trusted = [l for l in lines if l and not l.lower().startswith("[untrusted]")]

    def uniq(vals):
        s = set(vals)
        return s.pop() if len(s) == 1 else None

    m = re.match(r"^What is the approved inspection interval for the (.+?) at (.+?)\?$", q)
    if m:
        x, y = map(re.escape, m.groups())
        pat = re.compile(r"inspection interval for the " + x + " at " + y +
                         r" is (\d+) days?\b", re.I)
        vals = []
        for l in trusted:
            for mm in pat.finditer(l):
                vals.append(mm.group(1))
        v = uniq(vals)
        return v + " days" if v else None

    m = re.match(r"^How many days after commissioning of the (.+?) at (.+?) was the pressure test signed off\?$", q)
    if m:
        x, y = map(re.escape, m.groups())
        pat = re.compile(r"Commissioning of the " + x + " at " + y +
                         r" began.*?signed off (\d+) days? later", re.I)
        vals = []
        for l in trusted:
            for mm in pat.finditer(l):
                vals.append(mm.group(1))
        v = uniq(vals)
        if not v:
            return None
        return v

    m = re.match(r"^Which team maintains the (.+?) at (.+?)\?$", q)
    if m:
        x, y = map(re.escape, m.groups())
        pat = re.compile(r"The " + x + " at " + y +
                         r" is maintained by the ([^.]+?)\.", re.I)
        vals = []
        for l in trusted:
            for mm in pat.finditer(l):
                vals.append(mm.group(1).strip())
        return uniq(vals)

    return None
