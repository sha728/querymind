"""Read-only SQLite executor for Spider evaluation (design §6.5, R4.4, R9.6)."""

import asyncio
import sqlite3
import time
from pathlib import Path

from qm_engine.execution.base import ExecResult, ExecutionError, ResultColumn

# The progress handler runs every N virtual-machine instructions to check the deadline.
_PROGRESS_EVERY_N_OPS = 1000

# SQLite storage class of a Python value -> (normalized type, storage class name).
_VALUE_TYPES: dict[type, tuple[str, str]] = {
    int: ("integer", "INTEGER"),
    float: ("numeric", "REAL"),
    str: ("text", "TEXT"),
    bytes: ("other", "BLOB"),
}


def connect_ro(path: Path) -> sqlite3.Connection:
    """Open ``path`` read-only. Invalid UTF-8 in TEXT values is replaced, not fatal."""
    if not path.is_file():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
    return conn


def _column_types(rows: list[tuple[object, ...]], n_cols: int) -> list[tuple[str, str]]:
    """SQLite result columns carry no declared type; infer it from the values."""
    out = []
    for i in range(n_cols):
        kinds = {_VALUE_TYPES.get(type(r[i]), ("other", "NULL")) for r in rows if r[i] is not None}
        if not kinds:
            out.append(("other", "NULL"))
        elif kinds <= {("integer", "INTEGER"), ("numeric", "REAL")}:
            out.append(max(kinds, key=lambda k: k[0] == "numeric"))
        elif len(kinds) == 1:
            out.append(kinds.pop())
        else:
            out.append(("other", "MIXED"))
    return out


class SqliteExecutor:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def execute(self, sql: str, row_cap: int | None, timeout_ms: int) -> ExecResult:
        return await asyncio.to_thread(self._execute_sync, sql, row_cap, timeout_ms)

    def _execute_sync(self, sql: str, row_cap: int | None, timeout_ms: int) -> ExecResult:
        deadline = time.monotonic() + timeout_ms / 1000
        conn = connect_ro(self.path)
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), _PROGRESS_EVERY_N_OPS)
        try:
            cur = conn.execute(sql)
            rows = cur.fetchall() if row_cap is None else cur.fetchmany(row_cap + 1)
            names = [d[0] for d in cur.description or ()]
        except sqlite3.Error as e:
            raise self._map_error(e, deadline) from e
        finally:
            conn.close()

        truncated = row_cap is not None and len(rows) > row_cap
        if truncated:
            rows = rows[:row_cap]
        columns = tuple(
            ResultColumn(name, norm, db)
            for name, (norm, db) in zip(names, _column_types(rows, len(names)), strict=True)
        )
        return ExecResult(columns=columns, rows=rows, truncated=truncated)

    @staticmethod
    def _map_error(e: sqlite3.Error, deadline: float) -> ExecutionError:
        msg = str(e)
        if "interrupted" in msg.lower() and time.monotonic() >= deadline:
            return ExecutionError("TIMEOUT", "Query exceeded the statement timeout.")
        if "readonly database" in msg.lower():
            return ExecutionError("READ_ONLY_VIOLATION", msg)
        return ExecutionError("EXECUTION_ERROR", msg)
