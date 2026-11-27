# carpool rules (paste into AGENTS.md, CLAUDE.md, .cursorrules, GEMINI.md, or a system prompt)

Follow these rules, in order, to keep token use down:

1. Do exact work with code. Run lookups, counts, filters, sums, date math, grep and diffs with tools or scripts instead of reading the data into context. Check first that keys are unique and fields are present.
2. Put independent reasoning in one request. For 2 or more independent items that need judgment, use a single request, not one call or subagent per item. Give each task and each source a stable ID (T01, T01-a) and return a JSON array of `{"id","answer","source"}`.
3. Send compact evidence only when one current source clearly wins. If the evidence is tied or weak, or a draft competes with it, send the full evidence straight away.
4. Check every answer and retry only the failures, one at a time with full evidence. Retry when an answer is missing or duplicated, cites another task's source or a draft, isn't in its cited source, or is a name cut short.
5. Measure cost per accepted answer, retries included. A change only counts as a win if no task that used to pass now fails.
