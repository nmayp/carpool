# The evidence behind carpool

A summary of the working paper "Lowering the Cost of Agent Work at a Fixed Quality Target: Direct tools, compact evidence, verified fallback, and batching" (revised, 26 November 2026).

## Policy

- Exact operations run against the source with a contract check (unique identity, field present). Report them separately from reasoning, because taking inference out of exact tasks can dominate a combined result.
- Compact evidence is used only when a preflight finds one eligible source and answer span. Verify the compact answer and fall back to the original full-evidence request if the check fails.
- Batching: independent tasks share one native request, each bound to a stable ID, with one structured answer + source ID per task. Missing, duplicate, invalid-source and rejected answers fall back individually.

Accounting: `R_p = Σ C_p,i / Σ A_p,i`. That is total resources (checks, failed attempts and retries included, a shared call counted once) divided by independently accepted answers. A paired regression is a case the original passed and the optimized route failed.

Compression pays when `c_s + c_v + f·c_r < c_d`.
Final correctness with a replacing retry: `q_s(1−β) + q_s·β·q_r⁺ + (1−q_s)·α·q_r⁻`.

## Results

| experiment | original | optimized | note |
|---|---|---|---|
| 1,000 exact order lookups | — | 1000/1000, 0 model calls | index ≈ 1/9 of scan time; aggregation index pays back after ~5 queries |
| SQuAD fresh 32-q holdout (local Qwen2.5-1.5B) | 17 accepted | 19 accepted, 0 regressions | −32% tokens/accepted, −33% time |
| Native Codex, preflight only (24 tasks) | 24/24 | 24/24 | −0.20%: compression alone barely helps when every task needs its own call |
| Native Codex, groups of 3 | 24/24 | 24/24 | −65.7% total tokens, but uncached+output +37.7% |
| Native Codex, one group of 24 | 24/24, 420,507 tok | 24/24, 22,916 tok | −94.6% total, −47.1% uncached+output, 0 regressions, 11.4 s vs 26.5 s |

The one-line smoke test reported 16,071 input tokens (13,952 of them cached) for an 8-token reply: the fixed harness cost that batching spreads across tasks.

## Scope

- One client (Codex CLI 0.172.0-alpha.2, ChatGPT Plus sign-in), authored operational evidence with short keyed answers.
- Both routes ran within the included subscription allowance, so there was no incremental charge, and per-route allowance use was not measured. Savings on a metered API are unmeasured.
- Iterations used different fresh fixtures, so batch size and compact-evidence selection were not separated by a controlled ablation.
- Lessons that carpool's verifier builds in: a source-string check can accept a wrong span that occurs in the source. A same-sentence check can reject correct cross-sentence evidence (case R07). Proper-name truncation ("London" for "London Exhibition") was the last regression left.
