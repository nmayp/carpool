"""Compiled by carpool 0.2.0 from 168 logged calls.
Task: answer a question about a site's equipment from short evidence passages
Held-out check (held-out calls supplied separately, loose match): answered 32 of 48; 32 matched the logged output (precision 100.0%, 95% lower bound 89.3%).
Returns None when unsure: send those inputs to the model.
Read this file before you deploy it: it runs as you.
"""
import re


def solve(arg1, arg2):
    try:
        q = arg1.strip()
        lines = [l.strip() for l in arg2.split("\n") if l.strip()]
        trusted = [l for l in lines if not l.lower().startswith("[untrusted]")]

        m = re.match(r"Which team maintains the (.+?) at (.+?)\?$", q)
        if m:
            eq, site = re.escape(m.group(1)), re.escape(m.group(2))
            pat = re.compile(
                r"the " + eq + r" at " + site + r" is maintained by (?:the )?(.+?)\.(?:\s|$)",
                re.I)
            found = set()
            for l in trusted:
                mm = pat.search(l)
                if mm:
                    found.add(mm.group(1).strip())
            if len(found) == 1:
                return found.pop()
            return None

        m = re.match(r"What is the approved inspection interval for the (.+?) at (.+?)\?$", q)
        if m:
            eq, site = re.escape(m.group(1)), re.escape(m.group(2))
            pat = re.compile(
                r"inspection interval for the " + eq + r" at " + site + r" is (\d+) days", re.I)
            found = set()
            for l in trusted:
                mm = pat.search(l)
                if mm:
                    found.add(mm.group(1))
            if len(found) == 1:
                return found.pop() + " days"
            return None

        # "How many days after commissioning ..." questions: the expected
        # answer format (bare number vs. number with "days") is inconsistent
        # across examples and not predictable from the input, so abstain.
        return None
    except Exception:
        return None
