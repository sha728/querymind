"""Execution accuracy (EX) and hardness via the vendored official Spider code (design D5, §10.4).

EX here is **Spider 1.0 dev EX on the original databases**: the official execution-match
function, run on the question's own database only (not the test-suite distilled databases).
Prediction failures count as incorrect; gold queries that fail are reported and excluded.

These functions are synchronous and the vendored code calls ``asyncio.run`` internally, so
async callers must run them in a worker thread (``asyncio.to_thread``).
"""

import asyncio
import functools
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from qm_eval.vendor.test_suite import exec_eval, process_sql
from qm_eval.vendor.test_suite.evaluation import Evaluator
from qm_eval.vendor.test_suite.parse import remove_distinct

Hardness = Literal["easy", "medium", "hard", "extra"]


@dataclass(frozen=True)
class Score:
    correct: bool
    gold_error: str | None = None  # set when the gold query itself fails; excluded from EX

    @property
    def scored(self) -> bool:
        return self.gold_error is None


def _gold_error(db: Path, gold_sql: str) -> str | None:
    """Run the gold query exactly as the official matcher would; return its error, if any."""
    g = remove_distinct(exec_eval.postprocess(gold_sql))
    flag, value = asyncio.run(exec_eval.exec_on_db(str(db), g))
    return None if flag != "exception" else str(value)


def execution_match(db: Path, pred_sql: str | None, gold_sql: str, timeout_s: float) -> Score:
    """Official EX for one question. ``pred_sql=None`` means the pipeline produced no SQL."""
    exec_eval.TIMEOUT = timeout_s  # read by the patched read-only connection (see NOTICE.md)
    if pred_sql is None or not pred_sql.strip():
        # Never hand an empty prediction to the matcher: an empty statement returns no rows
        # and would "match" any gold query with an empty result.
        error = _gold_error(db, gold_sql)
        return Score(correct=False, gold_error=error)
    try:
        matched = exec_eval.eval_exec_match(
            str(db),
            pred_sql,
            gold_sql,
            plug_value=False,
            keep_distinct=False,
            progress_bar_for_each_datapoint=False,
        )
    except AssertionError as e:  # the official code asserts that the gold query executes
        return Score(correct=False, gold_error=str(e) or "gold query failed")
    return Score(correct=matched == 1)


@functools.lru_cache(maxsize=256)
def _schema(db: str) -> process_sql.Schema:
    return process_sql.Schema(process_sql.get_schema(db))


def hardness(db: Path, gold_sql: str) -> Hardness:
    """Official Spider difficulty bucket of the gold query."""
    sql = process_sql.get_sql(_schema(str(db)), gold_sql)
    return Evaluator().eval_hardness(sql)  # type: ignore[no-any-return]
