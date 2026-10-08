"""Executor protocol and result/error types shared by the SQLite and PostgreSQL executors."""

from dataclasses import dataclass
from typing import Literal, Protocol

ExecErrorCode = Literal["TIMEOUT", "EXECUTION_ERROR", "READ_ONLY_VIOLATION"]

MAX_ERROR_CHARS = 500  # DB error text passed to the correction prompt (design §6.5)


@dataclass(frozen=True)
class ResultColumn:
    name: str
    norm_type: str  # integer | numeric | text | boolean | date | timestamp | other
    db_type: str


@dataclass(frozen=True)
class ExecResult:
    columns: tuple[ResultColumn, ...]
    rows: list[tuple[object, ...]]
    truncated: bool

    @property
    def row_count(self) -> int:
        return len(self.rows)


class ExecutionError(Exception):
    """A query failed in the database. ``READ_ONLY_VIOLATION`` is blocking; others retryable."""

    def __init__(self, code: ExecErrorCode, message: str) -> None:
        self.code: ExecErrorCode = code
        self.message = message[:MAX_ERROR_CHARS]
        super().__init__(f"{code}: {self.message}")

    @property
    def retryable(self) -> bool:
        return self.code != "READ_ONLY_VIOLATION"


class Executor(Protocol):
    async def execute(self, sql: str, row_cap: int | None, timeout_ms: int) -> ExecResult:
        """Run one validated SELECT. Raises ``ExecutionError``."""
        ...
