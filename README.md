# QueryMind

Ask a relational database a question in plain English and get back a safe, **read-only** SQL
query, its result and a chart. QueryMind generates SQL with an LLM, checks it with a SQL-parser
safety validator before anything touches the database, runs it through a read-only connection
with a timeout and row cap, and repairs failing queries automatically.

The text-to-SQL engine is **measured**, not just demoed: a harness scores it on the
[Spider 1.0](https://yale-lily.github.io/spider) benchmark using the official execution-match
code, and every reported number comes from a reproducible run in this repo.

> **Status:** the engine, safety validator, Spider harness and zero-shot baseline are complete
> (M-A). Schema linking, few-shot examples and self-correction are implemented and switchable in
> the harness (M-B), but **their effect on accuracy has not been measured** (the ablation runs
> were deferred). Next: the .NET API and React UI (M-C).

## Results

<!-- metrics:start -->
**A0 zero-shot baseline: Spider 1.0 dev EX on the original databases, full dev set (reportable)**

| EX | easy | medium | hard | extra |
|---|---|---|---|---|
| **0.8114** (839 / 1,034) | 0.9073 | 0.8408 | 0.7989 | 0.6024 |

- Model: `gpt-oss-120b` on the Cerebras free tier, reasoning effort `low`, temperature 0.
- Technique: zero-shot, full schema in the prompt; no schema linking, no few-shot, no self-correction.
- Date: 2026-10-08, commit `e8d6569`, run `20261008T080708Z_A0`.
- Tokens per question: 842.9 prompt, 78.3 completion. Engine latency excluding free-tier pacing:
  p50 592.5 ms, p95 3,106 ms.
- Cost: $0 billed (free tier); $0.3537 per 1,000 questions estimated at list price.
- Safety test suite: 152 / 152 unsafe statements blocked (100%), printed by every `pytest` run.
- Per-technique effect (schema linking, few-shot, self-correction): **not measured**. No claim is
  made that any of them improves accuracy.
<!-- metrics:end -->

Details, provenance and notes for every number: [`docs/metrics.md`](docs/metrics.md).

## How it works

```
question ──► prompt (schema + question) ──► LLM ──► extract SQL
         ──► safety validator (sqlglot AST: single SELECT only, no DML/DDL/DCL,
             no dangerous functions) ──► read-only execution (timeout, row cap)
         ──► rows + columns  (eval: scored with the official Spider matcher)
```

Safety does not rely on the prompt: the database role is read-only, every query runs in a
read-only transaction, and the validator rejects anything that is not a single `SELECT` before
any database contact.

## Run the engine tests

Requires Python 3.11. From `engine/`:

```
uv venv --python 3.11 .venv
uv pip install -e ".[dev]"
.venv/Scripts/python -m pytest        # Windows; use .venv/bin/python on Linux/macOS
.venv/Scripts/ruff check .
```

(Without uv: `python3.11 -m venv .venv` and `pip install -e ".[dev]"`.) Tests use fakes only; no
API key, database server or Spider download is needed. CI runs the same checks on every pull
request.

## Run the Spider evaluation

1. Download Spider 1.0 and extract it to `engine/eval/data/spider/`.
2. Copy `.env.example` to `.env` and set `QM_CEREBRAS_API_KEY`.
3. From `engine/`: `.venv/Scripts/qm-eval run --subset 200 --seed 42 --tag try`

Full instructions, rate limits, resuming and exit codes: [`engine/eval/README.md`](engine/eval/README.md).

## Repository layout

```
engine/            Python 3.11 text-to-SQL engine (pipeline, validator, executors, LLM client)
engine/eval/       Spider harness: qm-eval CLI, scoring (vendored official code), pricing
engine/tests/      pytest suite, including the safety corpus
docs/metrics.md    every measured number, with its run and configuration
```

The .NET API (`/api`), React UI (`/frontend`) and seeded demo database (`/db`) arrive in milestone M-C.
