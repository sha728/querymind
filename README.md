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
> were deferred). The full app (.NET API, React UI, seeded Northwind demo database) runs with one
> `docker compose up` (M-C).

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

## Run the app with Docker

Five services: `frontend` (React, served by nginx), `api` (ASP.NET Core: accounts, history),
`engine` (Python: text-to-SQL), `app-db` (PostgreSQL for users and history) and `target-db`
(the Northwind demo database, reached only through a read-only role).

**Prerequisites**

- Docker with Compose v2.
- [Ollama](https://ollama.com) running on this machine with the embedding model:
  `ollama pull nomic-embed-text`. It is used for schema linking and picking few-shot examples.
- A free [Cerebras](https://cloud.cerebras.ai) API key for the language model (`gpt-oss-120b`).

**Start**

```
cp .env.example .env      # then fill in every empty value (see the comments in the file)
docker compose up --build
```

Open <http://localhost:8080>, create an account and ask, for example, *"Which 5 customers spent
the most last month?"*. The first start builds the images and loads Northwind; later starts take
seconds. The admin account from `ADMIN_EMAIL`/`ADMIN_PASSWORD` is created on the first start.

- `http://localhost:8080/health` reports the whole stack (API, app database, engine).
- Only the frontend port is published to the network side (`FRONTEND_BIND`, loopback by default).
  The Northwind database also listens on `127.0.0.1:5433` so the host-run integration tests can
  reach it; nothing else is published.
- On the Cerebras free tier the client waits about 12 s between model calls, so an answer with a
  summary takes roughly 15–20 s. `docker compose down` stops everything; add `-v` to also delete
  the databases.

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
engine/            Python 3.11 text-to-SQL engine (pipeline, validator, executors, LLM client, HTTP API)
engine/eval/       Spider harness: qm-eval CLI, scoring (vendored official code), pricing
engine/tests/      pytest suite, including the safety corpus
api/               ASP.NET Core (.NET 10) API: auth, ask, history, schema proxy; xUnit tests
frontend/          React + TypeScript (Vite) UI: ask, chart, history, schema; Vitest tests
db/target/         Northwind seed, read-only role and date shift for the demo database
docker-compose.yml the full stack
docs/metrics.md    every measured number, with its run and configuration
```

Other test suites: `dotnet test api/QueryMind.sln` (needs Docker for its PostgreSQL containers)
and, from `frontend/`, `npm ci`, `npm run lint`, `npx tsc --noEmit`, `npm test`.
