# Spider evaluation harness (`qm-eval`)

Runs the QueryMind engine (the same pipeline the product uses) on the Spider 1.0 dev set and
scores **execution accuracy (EX)** with the official matcher. Design: `specs/design.md` §10.

## 1. Get Spider 1.0

1. Download `spider_data.zip` from https://yale-lily.github.io/spider (Google Drive, ~200 MB).
2. Extract **only** these into `engine/eval/data/spider/` (git-ignored):
   `dev.json`, `train_spider.json`, `tables.json`, `dev_gold.sql`, and the `database/` folder.
   (`test_database/` and the `__MACOSX/` entries are not needed.)
3. Every run checks the files against `engine/eval/spider_manifest.sha256` and refuses to start
   if anything differs.

## 2. Configure the model

Settings come from the repo-root `.env` (copy `.env.example`). Reported runs use:

```
QM_LLM_PROVIDER=cerebras
QM_LLM_MODEL=gpt-oss-120b
QM_CEREBRAS_API_KEY=...
QM_LLM_REASONING_EFFORT=low
```

## 3. Run

From `engine/` (after `uv pip install -e .[dev]` into `.venv`):

```
.venv/Scripts/qm-eval run --subset 200 --seed 42 --tag A0-subset   # iteration
.venv/Scripts/qm-eval run --full --tag A0                         # reported numbers
.venv/Scripts/qm-eval cost eval/runs/<run folder>                  # tokens and cost
```

Each run writes `eval/runs/<UTC time>_<tag>/` with `config.json`, `results.jsonl` (one line per
question) and `summary.json`. A run is `reportable` only if it is `--full`, complete, and was made
on a clean git tree.

## 4. Rate limits and resuming

Free tiers limit requests per minute, hour and day (Cerebras free tier: 5/min, 150/hour). The
client paces requests; rate limits are **never** scored as wrong answers:

- a short limit (retry hint ≤ 60 s) is waited out and the same question is asked again;
- anything else stops the run with **exit code 75**, writing nothing for that question.

Continue with the same arguments plus `--resume eval/runs/<run folder>`. Resume refuses if the git
commit, clean state, any flag, the model/provider or the question list differ from the original
run.

Exit codes: `0` finished · `1` refused (manifest or resume mismatch) · `2` usage error ·
`75` interrupted, resumable.

**Practical tips for long runs**

- A full run takes about 7 hours on the free tier. Keep the machine awake: sleep pauses the run.
- Don't commit or edit tracked files while a run may still need `--resume` (the commit must
  match). For long runs, use a separate clean checkout: `git worktree add ../querymind-eval HEAD`.
- Never run two `qm-eval` processes against the same run folder.
