# QueryMind — measured results

Every number on this page is copied from a `summary.json` (or `qm-eval cost` output) produced by
an actual run in this repo. Nothing is rounded up or estimated unless labelled as an estimate.
Run folders live in `engine/eval/runs/` (git-ignored); each holds `config.json`,
`results.jsonl` and `summary.json`.

## M1 — A0 zero-shot baseline, Spider 1.0 dev (full)

**Spider 1.0 dev EX on the original databases** (official execution-match code from
`test-suite-sql-eval`, vendored at `e97acc5`; not test-suite / distilled-database accuracy).

| | |
|---|---|
| **EX** | **0.8114** (839 / 1,034) |
| Gold errors (excluded) | 0 |
| Statuses | success 1,032 · failed 2 (1 `TIMEOUT`, 1 `PARSE_ERROR`) · blocked 0 |
| Reportable | `true` (full dev set, clean tree, complete) |

| Hardness | n | correct | EX |
|---|---|---|---|
| easy | 248 | 225 | 0.9073 |
| medium | 446 | 375 | 0.8408 |
| hard | 174 | 139 | 0.7989 |
| extra | 166 | 100 | 0.6024 |

**Configuration**

| | |
|---|---|
| Run | `20261008T080708Z_A0` |
| Date | 2026-10-08 (08:07–16:11 UTC; paused hourly by the free-tier request limit, resumed with `--resume`) |
| Git commit | `e8d6569` (clean tree) |
| Provider / model | Cerebras free tier / `gpt-oss-120b` |
| Reasoning effort / temperature / max tokens | `low` / 0 / 4096 |
| Technique | **zero-shot**: full schema, no schema linking, no few-shot examples, no self-correction (see note 2) |
| Eval settings | SQLite, no row cap, no date, `CANNOT_ANSWER` off, per-query timeout 30 s |

**Tokens and latency**

| | |
|---|---|
| Prompt tokens | 871,510 total · 842.9 per question |
| Completion tokens (incl. reasoning) | 80,979 total · 78.3 per question |
| Requests | 1 per question (1,034) |
| Engine latency per question, excluding free-tier pacing | p50 592.5 ms · p95 3,106 ms |

**Notes**

1. **Duplicate-record repair.** Two resume loops ran concurrently for about 45 minutes, so 38
   questions (ids 559–630) were written twice. Both copies were identical in predicted SQL,
   correctness and status (38/38). The first copy of each was kept; the original file and a
   `REPAIR.md` are in the run folder. The EX above is computed on the 1,034 unique questions.
2. **Recorded flags vs. effective technique.** `config.json` lists `linking_mode: auto`,
   `few_shot_enabled: true`, `self_correction_enabled: true` (product defaults from `.env`). At
   commit `e8d6569` the pipeline implements none of these (they arrive in T22–T24). The results
   confirm it: every question made exactly 1 LLM call, `linking_applied` is false and
   `few_shot_ids` is empty for all 1,034 questions. The run is a true zero-shot baseline.

## A0 subset (iteration run, not reportable)

| Run | Questions | EX | easy | medium | hard | extra |
|---|---|---|---|---|---|---|
| `20261008T072520Z_A0-subset` (seed 42, commit `e8d6569`) | 200 | 0.79 (158/200) | 38/46 | 82/92 | 20/30 | 18/32 |

Tokens: 827.0 prompt + 79.4 completion per question. Engine latency (excl. pacing): p50 554.5 ms, p95 1,698 ms.

## M5 — cost

| Run | Billed | Estimate at list price (retrieved 2026-10-08) |
|---|---|---|
| A0 full (1,034 questions) | **$0** (Cerebras free tier) | $0.3658 total · **$0.3537 per 1,000 questions** |

List price: Cerebras `gpt-oss-120b` $0.35 / M input tokens, $0.75 / M output tokens
(https://www.cerebras.ai/pricing). Completion tokens include reasoning tokens.

## Quota-budget gate (T19 step 4)

Measured on the A0 subset: 1.0 request and ~906 tokens per question. Planned full runs: A0 + A1–A4
(5 × 1,034 questions) ≈ 5,170 requests and ≈ 4.7 M tokens, about **4.7 days** of the 1 M
tokens/day free quota, under the 14-day gate. This is a lower bound for A3/A4 (self-correction adds
calls) and A2/A4 (few-shot adds prompt tokens). Observed in practice: the binding limit was
**150 requests/hour**, giving about 145 questions per hour.
