---
description: Carpool the pending independent sub-tasks into one verified request instead of many calls
argument-hint: "[what to carpool, e.g. 'review each changed file']"
---

Apply the carpool skill to: $ARGUMENTS

1. List the independent items. Take out any that are exact operations and do those with a tool or script.
2. Give every remaining item an ID (T01, T02, …) and its evidence source IDs (T01-a, …).
3. Answer all of them in a single pass and return a JSON array of `{"id","answer","source"}`.
4. Check each answer against the source it cites. Redo only the ones that fail, one at a time, with full evidence.
5. Finish with one line: the number of items, model passes used and retries.
