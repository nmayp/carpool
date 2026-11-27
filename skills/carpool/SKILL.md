---
name: carpool
description: Cut agent token spend without losing quality. Use whenever a task contains several independent questions, lookups, files, records or sub-tasks that need answers — before spawning subagents, looping one model call per item, or reading a big table into context. Run exact operations as code, carpool independent reasoning into one request with stable IDs, send compact evidence only when one source clearly wins, verify every answer, and retry only the failures.
---

# carpool: many tasks, one ride

Why make 24 trips when one carpool do trick.

Every separate model call or subagent pays for the harness again: system
prompt, tool schemas, instructions. In Claude Code that is about 26,000 input
tokens per call before the task itself. We measured 48 independent questions in
a few requests instead of 48: cost per accepted answer fell about 80% against
one lean call per task (93–96% against one full agent call per task), with 0
regressions. Compact evidence on its own, still one call per task, did not save
money. The saving comes from sharing the call.

Follow these rules in order.

## 1. Do exact work with code

If an answer is exact and repeatable (lookup by ID, count, filter, sum, date
math, grep, diff, schema check), run a tool or a script instead of reading the
data into context and reasoning over it.

- Check the contract first: the key is unique and the field exists and is non-empty.
- Report exact results separately from reasoning results.
- An index pays off only after several queries, so count what it costs to build.

## 2. Put independent reasoning in one request

When you have 2 or more independent questions that need judgment (per-file
reviews, per-record classifications, per-ticket triage, "check each of these"),
don't spawn one subagent or make one call per item. Send a single request:

```
You will answer N independent tasks. Answer each ONLY from its own sources.
Return ONLY a JSON array: [{"id": "<task id>", "answer": "...", "source": "<source id>"}]

### TASK T01
Question: ...
Sources:
[T01-a] ...
[T01-b] ...

### TASK T02
...
```

- Give every task and every source a stable ID. Name sources after their task (`T01-a`), so a citation of another task's source shows up as leakage.
- Ask for one structured answer and one source ID per task.
- Only group tasks that are independent. If one answer feeds another, they don't share a request.

## 3. Send compact evidence only when it's clearly safe

Before the call, look at each task's evidence:

- If one current, non-draft source clearly wins (it's the only relevant one, or it leads the runner-up by a wide margin), send just that source.
- If the evidence is weak, tied or ambiguous, or a draft or stale copy competes, send the full evidence straight away rather than trying the cheap route and paying twice.

A grouped request can carry full evidence for some tasks and compact evidence for others.

## 4. Check every answer and retry only the failures

After the call, redo a task on its own, with full evidence, if its answer is:

- missing, duplicated or empty
- citing a source ID that belongs to another task, or a draft or superseded source
- not actually in the source it cites
- in a passage that doesn't mention the question's terms
- a bare number that appears with different units in the source
- a name cut short ("London" where the source says "London Exhibition")

The retry replaces the first answer. All other answers are accepted from the shared request.

## 5. Count the full cost

Cost is everything spent divided by accepted answers, including checks, failed
attempts and retries, with a shared call counted once. A change only counts as a
win if no task that used to pass now fails. Report any such regression even
when savings elsewhere make up for it.

## When not to carpool

- Tasks that depend on each other, or open-ended work where each item needs a long conversation.
- A single task: there is nothing to share.
- Groups so large that one bad answer could drag the rest down. Split into groups of about 10–48. Small local models need smaller groups.

If you write or run code that loops over items calling a model (a script, pipeline or batch job),
replace that loop with one grouped call plus per-item checks. Use the carpool MCP tools or library if they're available.

Reference implementation and benchmark: `pip install carpool-agents`, then `carpool bench`.
