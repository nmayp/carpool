# Measured results

Every number below comes from a raw JSON file in [`results/`](../results/) and can be reproduced with
[`bench/real.py`](../bench/real.py) and [`bench/e2e_skill.py`](../bench/e2e_skill.py).

**Setup.** Claude Code CLI (`claude -p --output-format json`), default model at run time (Claude Sonnet 5.5).
Each call is a fresh, non-persisted session in an empty directory, so it pays the full agent harness
(system prompt, tool definitions), about 26k input tokens even for a one-word reply. Token counts and USD come
from Claude Code's own usage report. USD is the list price the CLI reports, not an invoice.
Baseline calls ran 4 at a time. Total spend for everything on this page: $43.53.

**Scoring.** *Strict* means normalized exact match against the answer key (Unicode, case, whitespace, final
punctuation and a leading article are normalized). *Equivalent* also accepts an answer that contains the key or
is contained in it ("12" vs "12 days"). A regression is a task the baseline got right (strict) and the
optimized route got wrong. Both routes run on the same tasks with the same scorer.

## Headline

| set | baseline | carpool | cost per accepted answer | regressions |
|---|---|---|---|---|
| ops24 (fictional ops, 24 tasks, 3 repeats) | 24 calls, 24/24, $1.159 | 1 call, 24/24, $0.052–0.065 | −94.4% to −95.5% | 0 |
| SQuAD-48 holdout (public, 48 tasks, 2 repeats) | 48 calls, 40/48, $2.449 | 2 calls, 44–45/48, $0.106–0.178 | −93.4% to −96.1% | 0 |

Zero regressions in n tasks bounds the true regression rate at about 3/n with 95% confidence
(0.117 for n=24, 0.06 for n=48): if the true rate were higher, seeing no regression in n tasks would have less
than a 5% chance.

## 1. ops24: fictional operational evidence

8 single-sentence, 8 cross-sentence, 8 competing/stale-evidence tasks (`carpool/data/ops24.jsonl`).

| route | calls | strict | equiv. | total tokens | uncached+out | USD | Δ USD/acc | Δ tokens/acc | regr. |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 24 | 24/24 | 24/24 | 640,189 | 279,805 | $1.159 | — | — | — |
| compact-only | 24 | 24/24 | 24/24 | 639,207 | 272,383 | $1.130 | −2.5% | −0.2% | 0 |
| batch-only #1 | 1 | 24/24 | 24/24 | 30,970 | 15,954 | $0.069 | −94.0% | −95.2% | 0 |
| carpool #1 | 1 | 24/24 | 24/24 | 29,977 | 14,961 | $0.065 | −94.4% | −95.3% | 0 |
| batch-only #2 | 1 | 24/24 | 24/24 | 30,969 | 11,471 | $0.052 | −95.5% | −95.2% | 0 |
| carpool #2 | 1 | 24/24 | 24/24 | 29,996 | 11,488 | $0.052 | −95.5% | −95.3% | 0 |
| batch-only #3 | 1 | 24/24 | 24/24 | 30,969 | 11,471 | $0.052 | −95.5% | −95.2% | 0 |
| carpool #3 | 1 | 24/24 | 24/24 | 29,978 | 11,470 | $0.052 | −95.5% | −95.3% | 0 |

Compact evidence on its own saved 0.2% of tokens, close to the 0.2% the original working paper reported for the same arm.

## 2. SQuAD-48, first run: ablation and group-size sweep

48 SQuAD 1.1 dev questions (CC BY-SA 4.0). Each comes with its gold paragraph and 2 same-article distractors (`bench/data/squad48.jsonl`).

| route | calls | strict | equiv. | total tokens | USD | Δ USD/acc | regr. |
|---|---|---|---|---|---|---|---|
| baseline | 48 | 26/48 | 45/48 | 1,311,995 | $2.446 | — | — |
| compact-only | 60 | 29/48 | 45/48 | 1,618,114 | $2.884 | +5.7% | 0 |
| batch-only #1 | 1 | 33/48 | 46/48 | 66,881 | $0.219 | −93.0% | 0 |
| batch-only #2 | 1 | 33/48 | 46/48 | 66,934 | $0.066 | −97.9% | 0 |
| carpool #1 | 8 | 34/48 | 46/48 | 235,975 | $0.446 | −86.1% | 0 |
| carpool #2 | 11 | 34/48 | 45/48 | 318,172 | $0.517 | −83.8% | 0 |
| carpool k=1 | 58 | 33/48 | 46/48 | 1,566,979 | $2.826 | −9.0% | 1 |
| carpool k=4 | 20 | 34/48 | 45/48 | 555,906 | $1.016 | −68.2% | 0 |
| carpool k=12 | 13 | 33/48 | 44/48 | 370,564 | $0.679 | −78.1% | 1 |
| carpool k=24 | 11 | 33/48 | 44/48 | 317,462 | $0.584 | −81.2% | 1 |

What this run showed, and what changed because of it:

- Compact evidence without grouping cost 5.7% more, because each verifier rejection paid for a whole extra harness. Shortening evidence saves only part of one task's text, while every redo pays the whole fixed part again.
- The verifier rejected too many correct answers: 7–10 retries per carpool run. The cause was the
  model adding explanations in parentheses ("Flax cultivation (supporting the growth of…)"), so the
  answer-in-source check failed. The one regression (Q21) is the same problem in a retry answer. Fix: the
  prompt now asks for verbatim answers, and trailing parentheticals are stripped before checking.
- Cost per accepted answer falls steadily as the group grows: k=1 → 4 → 12 → 24 → 48.

## 3. SQuAD-48 holdout: after the fix, on fresh questions

We designed the fix after looking at section 2, so to avoid tuning on the test set we evaluated it on 48
different SQuAD questions with no overlap (`bench/data/squad48-holdout.jsonl`, seed 5678).

| route | calls | strict | equiv. | total tokens | uncached+out | USD | Δ USD/acc | Δ tokens/acc | regr. |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 48 | 40/48 | 48/48 | 1,313,117 | 592,349 | $2.449 | — | — | — |
| batch-only #1 | 1 | 45/48 | 48/48 | 67,072 | 52,056 | $0.218 | −92.1% | −95.5% | 0 |
| batch-only #2 | 1 | 45/48 | 48/48 | 67,025 | 12,203 | $0.062 | −97.7% | −95.5% | 0 |
| carpool #1 | 2 | 44/48 | 48/48 | 73,017 | 41,534 | $0.178 | −93.4% | −94.9% | 0 |
| carpool #2 | 2 | 45/48 | 48/48 | 73,060 | 23,111 | $0.106 | −96.1% | −95.1% | 0 |

Verifier retries dropped from 7–10 to 1, and grouped answers were more often exact (44–45 vs 40 strict). One
possible reason is that the grouped prompt's format rules apply to every task at once; we haven't tested that.

## 4. End-to-end: does installing the plugin change a real agent?

A Claude Code agent triages 20 support tickets (area + order ID) and counts 400 orders by status, with and
without `--plugin-dir` pointing at this repo. 3 runs each, order alternated.

| arm | USD per run | turns | correct | subagents |
|---|---|---|---|---|
| control | 0.136, 0.075, 0.074 | 3, 4, 4 | 20, 20, 20 (+ counts) | 0, 0, 0 |
| with carpool | 0.083, 0.072, 0.074 | 5, 4, 4 | 20, 20, 20 (+ counts) | 0, 0, 0 |

There was no measurable effect. Claude Code already reads all items in one go and uses tools for the counts,
so the plugin had no per-item fan-out to remove. The $0.136 control run is the first run with a cold prompt cache,
so it is not evidence of a difference. A long-document variant (12 contracts, ~2k words each) behaved the same:
the control agent grepped, got 12/12 in 5 turns, and spawned no subagents.

The 90%+ savings apply where something makes one model call per item: scripts, pipelines, batch jobs, agent
frameworks and fan-out workflows. The library, CLI and MCP server are for those. A well-behaved interactive
agent won't get cheaper just because the skill is installed.

## 5. Adaptive group size (learned during the run)

`carpool run --adaptive` starts with small groups and grows them using b* = √(H / (φ(H+E))), the size that minimises the
per-task cost H/b + φ(b−1)(H+E). It measures the fixed part H from real usage and learns the crowding rate φ from the
retries it sees (growth is capped at doubling per round). Run on the same 48-question SQuAD holdout, starting at 4
(`results/squad48-holdout-adaptive-claude-code.json`):

| route | group sizes | calls | strict | equiv. | total tokens | USD | Δ USD/acc vs baseline | regr. |
|---|---|---|---|---|---|---|---|---|
| baseline (§3) | 1 × 48 | 48 | 40/48 | 48/48 | 1,313,117 | $2.449 | — | — |
| carpool adaptive | 4, 8, 16, 20 | 5 | 46/48 | 48/48 | 153,464 | $0.660 | −76.6% | 0 |

That is cheaper than separate calls but dearer than one fixed group of 48 ($0.11–0.18), because each of the
small early groups pays the fixed part again. Use adaptive sizing when you don't yet know how large a group the
model handles well, and set the size directly once you do.

## 6. Fan-out hook (live check)

The plugin now ships a `PreToolUse` hook (`hooks/`). It counts subagent launches, and on the 3rd within
3 minutes it adds a short note to the model's context suggesting one shared pass. It shows one line to the user
and never blocks anything. In a live Claude Code run asked to launch 4 subagents for 4 one-line questions, the
hook fired once, at the 3rd launch, and the model received and quoted the note. The check cost $0.40. We haven't
yet measured whether the note changes agent behaviour on real workloads.

## 7. A small local model: Ollama, Qwen2.5-1.5B on 4 CPU cores

The same harness on a raw local API (`--backend ollama:qwen2.5:1.5b`, temperature 0, context window set to
32k so grouped prompts aren't cut off). This setting is the opposite of Claude Code in two ways: the fixed part
of each call is only about 200 tokens instead of about 26,000, and the model is far weaker. We expected small savings, and a failure rate that grows with group size. Tokens are Ollama's own counts; there is no price.

**ops24, original set.** Grouping broke the stale-evidence tasks: in the shared prompt the model cited the
draft copy on all 8 of them, where alone it picked the approved one.

| route | calls | strict | equiv. | total tokens | regr. |
|---|---|---|---|---|---|
| baseline | 24 | 18/24 | 24/24 | 5,654 | — |
| compact-only | 27 | 21/24 | 24/24 | 5,688 | 0 |
| batch-only | 1 | 16/24 | 16/24 | 3,336 | 8 |
| carpool | 1 | 16/24 | 16/24 | 2,787 | 8 |

We added two checks to every grouped answer: it may not cite a source marked as a draft, and its value must
appear in the source it cites. The first fresh set (`bench/data/ops24-fresh.jsonl`) showed why the second was
needed: the model wrote the draft's number while citing the approved source (batch-only 16/24, 8 regressions,
before the second check existed). Both checks were designed after seeing these failures, so we evaluated them on
a third set with new names and numbers (`bench/data/ops24-fresh2.jsonl`):

| route | calls | strict | equiv. | total tokens | regr. |
|---|---|---|---|---|---|
| baseline | 24 | 19/24 | 24/24 | 5,835 | — |
| batch-only | 1 | 24/24 | 24/24 | 3,518 | 0 |
| carpool | 9 | 24/24 | 24/24 | 5,029 | 0 |

In the carpool run the model cited a draft once and repeated task IDs seven times. The checks caught all 8 and
redid them individually, which is why it took 9 calls and saved only 14% of tokens. The batch-only prompt
happened to come back clean.

**SQuAD-48 holdout.**

| route | calls | strict | equiv. | total tokens | regr. |
|---|---|---|---|---|---|
| baseline (run A) | 48 | 18/48 | 30/48 | 32,677 | — |
| batch-only, one group of 48 (run A) | 48 | 20/48 | 28/48 | 58,001 | 1 |
| baseline (run B) | 48 | 21/48 | 29/48 | 32,636 | — |
| carpool, groups of 8 (run B) | 28 | 24/48 | 31/48 | 30,049 | 4 |

With 48 questions in one prompt the reply was unusable, so 47 tasks were redone one by one and the run used 77%
more tokens than the baseline. Grouping only pays while the redo rate f stays below 1 − 1/b, and here it didn't. In groups of 8, 2 of the 6
replies were unusable (16 redos) and the verifier rejected 6 more answers. The run saved 8% of tokens and
accepted 3 more answers in total, but had 4 regressions against 7 improvements: the verifier let through some
wrong answers this model gave, so the answers it accepted were right less often than the baseline's, the condition carpool needs. The two baselines
differ (18 vs 21) although both ran at temperature 0, so single runs of this model are noisy.

What this shows: with a tiny fixed part there is little to save, and a weak model needs small groups. Even then,
grouping can cost some quality, so check for regressions before relying on it.

## 8. Does the fan-out hook change what an agent does?

No, not measurably. The workload was 10 independent Python modules, each with one planted bug, in three
variants. There were three arms: plain Claude Code, the carpool plugin without its hook, and the full plugin
(`bench/e2e_hook.py`, results in `results/e2e-hook-*-claude-code.json`, 19 runs, $10.69).

| variant | arm | runs | runs with subagents | subagents | USD per run | bugs found |
|---|---|---|---|---|---|---|
| small modules, subagents allowed | plain | 3 | 0 | 0 | 0.19–0.21 | 10/10 each |
| | plugin, no hook | 3 | 0 | 0 | 0.18–0.20 | 10/10 each |
| | full plugin | 3 | 0 | 0 | 0.18–0.20 | 10/10 each |
| ~650-line modules, subagents allowed | plain | 2 | 1 | 0, 5 | 0.22, 0.90 | 10/10 each |
| | plugin, no hook | 2 | 2 | 10, 10 | 1.31, 1.22 | 10/10 each |
| | full plugin | 2 | 0 | 0, 0 | 0.31, 0.20 | 10/10 each |
| ~650-line modules, user asks for one subagent per module | plugin, no hook | 2 | 2 | 10, 10 | 1.22, 1.20 | 10/10 each |
| | full plugin | 2 | 2 | 10, 10 | 1.19, 1.19 | 10/10 each |

- With small modules nobody fanned out, so the hook never had anything to act on.
- With large modules, fanning out was hit and miss, and when it happened it cost 4–6 times more for the same
  10/10. The full-plugin runs happened not to fan out, but the hook never fired in them (it acts on the 3rd
  launch and there was no first), so the difference between the arms is chance at 2 runs each, not the hook.
- When the user asked for one subagent per module, the hook fired (its state file shows the note at the 3rd
  of 10 launches), but the agent had already issued all 10 launches as one batch, so the note came too late to
  change anything. Here going ahead was also the right call, because the user asked for it.

A note at the 3rd launch can't change a decision the agent makes in one step. A hook that could would have to
act before the first launch, for example when the prompt arrives, and it would need care not to overrule a
user who asks for subagents. We haven't built that.

## 9. A hook that acts before the first launch

`hooks/prompt_hint.py` runs when the user's message arrives (`UserPromptSubmit`), before any tool call. If the
message covers several independent items and doesn't ask for subagents, it adds one note suggesting a single
pass. A request for subagents ("delegate each module to its own subagent") makes it stay silent; a mention with
permissive wording only ("you may use subagents if that helps") counts as permission. It never blocks, and it
logs each decision. We compared the plugin with the old launch-time hook only (v1) against the plugin with both
hooks (v2), then reran the arm without hooks as a control on the same task seeds (`results/e2e-prompthook-*`,
14 runs, $7.97).

| variant | arm | runs | runs with subagents | USD per run | bugs found |
|---|---|---|---|---|---|
| ~650-line modules, subagents allowed | v1: launch-time hook only | 4 | 0 | 0.21–0.24 | 10/10 each |
| | v2: both hooks | 4 | 0 | 0.20–0.24 | 10/10 each |
| | control: no hooks | 4 | 3 (10, 10, 5) | 0.25–1.24 | 10/10 each |
| user asks for one subagent per module | v2 | 2 | 2 (10, 10) | 1.33, 1.22 | 10/10 each |

What we can and can't conclude:

- The new hook works as designed. Its log shows the note added in all 4 v2 runs, and in both runs where the user
  asked for subagents it stayed silent and the agent launched all 10, as asked.
- Plugins with a hook registered on the subagent tool fanned out in 0 of 10 large-module runs (§8 and here);
  arms without one fanned out in 6 of 8. Chance would give a split this lopsided about 0.15% of the time.
- But the old launch-time hook did just as well, and it produced no output in those runs (it only speaks at the
  3rd launch). So the reduction can't be credited to the new note, and we don't know what causes it. One possible
  explanation is that registering a hook on the subagent tool changes how the agent decides, even when the hook
  stays silent. We haven't tested that; a run with a hook that does nothing would.
- The live safety check also showed the prompt hook firing on each subagent's own prompt. It now skips prompts
  that carry an `agent_id` (the documented marker for subagents), which we haven't yet confirmed live.

## 10. Is it just having a hook registered?

To test that, we added an arm whose hook on the subagent tool does nothing at all: it reads its input and exits
without output (`results/e2e-noophook-large-claude-code.json`, 4 runs, $1.80). Same task, same four seeds:

| arm (seeds 300–303) | runs that fanned out | USD per run |
|---|---|---|
| no hooks (control, §9) | 3 of 4 | 0.25–1.24 |
| hook that does nothing | 1 of 4 | 0.19–1.22 |
| launch-time hook only (v1, §9) | 0 of 4 | 0.21–0.24 |
| both hooks (v2, §9) | 0 of 4 | 0.20–0.24 |

All runs found 10/10. Pooled with §8, arms with any hook registered on the subagent tool fanned out in 1 of 14
large-module runs, and arms without one in 6 of 8 (a split this lopsided by chance has a probability of about
0.2%). The do-nothing hook fanning out less than the control suggests that having a hook registered matters,
not what the hook says. But 4 runs is too few to tell the do-nothing hook apart from the real ones, and we
don't know why registration would change the agent's choice. So treat this as an open question, not a feature.

## 11. Against a fair baseline: one lean call per task

The headline numbers compare carpool with one full Claude Code agent call per task. That call carries about 26,000
tokens of fixed setup, so it flatters any method that removes it. A fairer baseline is one lean call per task:
the same model through Claude Code, but with no tools and a one-line system prompt (`--backend claude-lean`,
about 1,100 tokens of fixed setup per call). Results in `results/*-claude-lean.json`, $1.90.

| set | one lean call per task | carpool, first run | carpool, repeat run | regressions |
|---|---|---|---|---|
| 24 ops tasks | 24 calls · 24/24 · $0.281 | 1 call · 24/24 · $0.047 (**−83%**) | $0.018 (−94%) | 0 |
| 48 SQuAD questions | 48 calls · 42/48 · $0.817 | 3 calls · 46/48 · $0.195 (**−78%**) | $0.051 (−94%) | 0 |

- The fair saving is about 80%, not 93–96%. The first run is the number to quote: on the repeat the provider had
  cached the grouped prompt, which a real workload running new tasks wouldn't get.
- It stays large because even a lean call here carries about 1,100 tokens of fixed setup, while one task is only
  300–600 tokens. The saving shrinks as each task's own evidence grows: carpool can at most remove the fixed part,
  about H/(H+E) of each call. With a raw API call and a short system prompt the fixed part is
  far smaller; on the local model in §7 the savings were 8–51%.
- Grouping also didn't cost quality here: carpool accepted more answers than the lean baseline (46 vs 42 on SQuAD).

## 12. Compiling logged calls into code (`carpool compile`)

Question: when a model call keeps seeing the same kind of input, can a model write a plain function that replaces
most of those calls, and what happens when it can't? `carpool compile` has a model read logged (input, output)
pairs, write a function that may abstain, and keep it only if it passes a gate on logged calls it never saw.
Anything the function abstains on goes to the model. Script: `bench/compile_pilot.py`. Results, spend and the
generated code: `results/compile-pilot/`. Total spend $2.27, Claude Sonnet 5.5 through `claude-lean`.

The three tasks use real data and go from regular to open-ended:

- **hdfs**: HDFS log lines (loghub HDFS_2k, 200 test lines) to one of 14 event ids. The templates are in the prompt.
- **bank**: customer messages (Banking77, 150 test messages) to one of 77 intents.
- **squad**: the 48 SQuAD holdout questions from §3, answered from passages. A negative control.

For each task, the model labelled a training sample (the "logged calls"; 100 lines, 300 messages), a second call
wrote the function with two repair rounds against the training sample only, and a validation set was used for the
gate. The gate rule was fixed before the run: at least 95% agreement with the logged outputs and a 95% lower bound
of at least 85%. The pilot script has no gate of its own, so the rule was applied by hand to the numbers below.

| task | code answers | precision of those answers (vs real labels) | model alone | code + model | cost to build | break-even |
|---|---|---|---|---|---|---|
| hdfs | 100% (200/200) | 100% (lower bound 98.1%) | 100% | 100% | $0.13 | 18 calls |
| bank | 4.7% (7/150) | 100% on 7 answers (lower bound 64.6%) | 78% | 78% | $0.60 | 1,631 calls |
| squad | 16.7% (8/48) | 50% (lower bound 21.5%) | 95.8% | 87.5% | $0.79 | none |

- **Regular text compiles.** On log lines the function answered every line correctly and replaced every model
  call, for about $0.13. A one-line call here cost $0.0069, so it paid for itself after 18 lines.
- **Meaning doesn't.** On banking intents the function answered 5% of messages. On validation it agreed with the
  logged label on 16 of the 17 it answered (94.1%, lower bound 73.0%), so the gate refused it. Without the model
  as fallback there would be nothing left to use; with it, the saving would have been about 5% of the calls.
- **The gate matters.** On SQuAD the function was wrong on half of what it answered, and using it anyway cost 4
  of 48 answers (95.8% to 87.5%). The SQuAD function had no validation split, so no gate was run there; the
  numbers are the test result.
- Break-even is the cost to build divided by the saving per call times the share of calls the code answers. It
  assumes the model alone would have been right as often as code plus model, which holds for hdfs and bank and not
  for squad.

Caveats, all of which matter:

- One run per task, one seed, and small test sets (200, 150 and 48). The intervals are wide.
- hdfs is the easy end: the templates were in the prompt, so most of the function can be read off them.
- The same model labelled the training sample and wrote the function. A weaker or stronger writer is untested.
- squad was trained on the real answers, not on logged model outputs, which favours the code, and the writer got
  only 40 questions.
- Call costs are for short lean prompts ($0.007 to $0.017). Agent-sized calls cost more, so the saving per
  replaced call is larger, but this isn't measured here.
- `Gate.min_coverage` (default 10%) was added after the run and isn't part of these results. It doesn't change
  them: hdfs passes it, and bank and squad already fail on precision.

## Limits

- Two setups: Claude Code with Claude Sonnet 5.5, and Ollama with Qwen2.5-1.5B on CPU. The Codex, Gemini CLI and
  OpenAI-compatible backends are implemented and unit-tested against recorded payloads, but not benchmarked.
- USD is list price from the CLI. Cache state varies between runs: a cold first run costs more, which is why
  repeats are reported separately rather than averaged.
- 24 and 48 tasks per set, with short extractive answers. Long-form or open-ended answers are untested.
- The ops24 tasks were written by the authors. SQuAD is public but easy for current models.
