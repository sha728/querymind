# Vendored: test-suite-sql-eval

- **Source:** https://github.com/taoyds/test-suite-sql-eval
- **Commit:** `e97acc546ecbee8fa27fa8dbf025ef61493a876c` (default branch `master`, retrieved 2026-10-08)
- **Licence:** Apache License 2.0, confirmed from the repository metadata and the `LICENSE` file copied here unchanged.
- **Files:** `exec_eval.py`, `parse.py`, `process_sql.py`, `evaluation.py`, `LICENSE`.
- **Purpose in QueryMind:** the official Spider execution-match function (`eval_exec_match`) and difficulty function (`Evaluator.eval_hardness`), used by `qm_eval/scoring.py` (design D5, §10.4).

Copyright for the original code remains with its authors (Ruiqi Zhong, Tao Yu, Dan Klein and contributors). Code style is kept as published; the directory is excluded from ruff.

## Changes made by QueryMind

Every change is marked with a `QueryMind patch` comment.

1. **Relative imports** (`from .parse import …`, `from .process_sql import …`, `from .exec_eval import …`) so the files work as a package.
2. **`exec_eval.get_cursor_from_path`**
   - Opens the database **read-only** (`?mode=ro` URI). The original opened it read-write.
   - Enforces `TIMEOUT` with a SQLite progress handler. The original `asyncio.wait_for` timeout never fires, because `exec_on_db_` never awaits, so a slow query could hang a run. `qm_eval.scoring` sets `TIMEOUT` per call.
   - Neither change alters the result of any query that completes within the timeout.
3. **`process_sql.tokenize`** calls `word_tokenize(string, preserve_line=True)`. This skips nltk's sentence splitter, which needs separately downloaded `punkt` data.
   - **Verified on 2026-10-08:** hardness for all 1,034 Spider 1.0 dev gold queries was identical with and without this change (0 differences). The distribution, easy 248 / medium 446 / hard 174 / extra 166, matches the published Spider dev split.

## How QueryMind calls it

`execution_match` calls `eval_exec_match(db, pred, gold, plug_value=False, keep_distinct=False, progress_bar_for_each_datapoint=False)`. It runs on the question's original database only (each Spider dev database folder holds exactly one `.sqlite` file). This is reported as **Spider 1.0 dev EX on the original databases**, not test-suite accuracy.
