"""PostgreSQL access for the product: read-only connections and the query executor
(design §6.5, R1.1, R4.1, R4.4, R5.1).

Every connection uses the read-only role from configuration and runs read-only transactions.
"""

import asyncio
import sys
from datetime import date, datetime, time
from decimal import Decimal

import psycopg
from psycopg import sql
from psycopg_pool import AsyncConnectionPool, PoolTimeout

from qm_engine.config import EngineConfig
from qm_engine.execution.base import (
    ExecErrorCode,
    ExecResult,
    ExecutionError,
    ResultColumn,
    TargetDBUnavailable,
)

CONNECT_TIMEOUT_S = 10


def conninfo(cfg: EngineConfig) -> str:
    """Connection string for the read-only role (password never logged)."""
    password = cfg.target_db_ro_password.get_secret_value() if cfg.target_db_ro_password else ""
    return psycopg.conninfo.make_conninfo(
        host=cfg.target_db_host,
        port=cfg.target_db_port,
        dbname=cfg.target_db_name,
        user=cfg.target_db_ro_user,
        password=password,
        connect_timeout=CONNECT_TIMEOUT_S,
        application_name="querymind-engine",
    )


def use_selector_event_loop_on_windows() -> None:
    """psycopg's async mode cannot run on Windows' default ProactorEventLoop.

    Call once at process start in host-run entry points (tests, qm-eval, the API when run
    on Windows). No effect on Linux, where the engine container runs.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]


async def connect_ro(cfg: EngineConfig) -> psycopg.AsyncConnection:
    """An autocommit-off connection whose transactions all start with BEGIN READ ONLY.

    This is layer L2 (design §7.1); the role's own default_transaction_read_only and missing
    privileges (layer L1) apply as well.
    """
    conn = await psycopg.AsyncConnection.connect(conninfo(cfg))
    await conn.set_read_only(True)
    return conn


# --- query executor (T29, design §6.5) -------------------------------------------------

# SQLSTATE -> engine error code. Read-only and privilege refusals are blocking: reaching them
# means the validator let a write through (logged at ERROR by the pipeline).
_SQLSTATE_CODES: dict[str, ExecErrorCode] = {
    "57014": "TIMEOUT",  # query_canceled (statement_timeout)
    "25006": "READ_ONLY_VIOLATION",  # read_only_sql_transaction
    "42501": "READ_ONLY_VIOLATION",  # insufficient_privilege
}

# PostgreSQL type name (from the result OID) -> normalized type (design §4.2)
_PG_TYPE_NORM: dict[str, str] = {
    "int2": "integer",
    "int4": "integer",
    "int8": "integer",
    "numeric": "numeric",
    "float4": "numeric",
    "float8": "numeric",
    "money": "numeric",
    "text": "text",
    "varchar": "text",
    "bpchar": "text",
    "name": "text",
    "bool": "boolean",
    "date": "date",
    "timestamp": "timestamp",
    "timestamptz": "timestamp",
}

# SQLSTATEs (besides class 08, connection exception) meaning the server is unavailable.
_UNAVAILABLE_SQLSTATES = frozenset({"57P01", "57P02", "57P03"})  # admin/crash shutdown, starting

IDLE_IN_TRANSACTION_MARGIN_MS = 5000


def encode_value(value: object) -> object:
    """JSON-ready value (design §4.2): numbers stay numbers, dates become ISO strings."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, bytes | bytearray | memoryview):
        return "<binary>"
    return value


def _result_column(name: str, type_oid: int, conn: psycopg.AsyncConnection) -> ResultColumn:
    """Name the result type from its OID and normalize it (design §4.2)."""
    info = conn.adapters.types.get(type_oid)
    pg_name = info.name if info is not None else str(type_oid)
    return ResultColumn(name=name, norm_type=_PG_TYPE_NORM.get(pg_name, "other"), db_type=pg_name)


def _message(e: psycopg.Error) -> str:
    diag = e.diag
    parts = [diag.message_primary or str(e).strip()]
    if diag.message_hint:
        parts.append(f"HINT: {diag.message_hint}")
    return " ".join(parts)


def _is_unavailable(e: psycopg.Error) -> bool:
    """Connection-level failures, as opposed to errors in the query itself."""
    if isinstance(e, PoolTimeout):
        return True
    state = e.sqlstate or ""
    if state.startswith("08") or state in _UNAVAILABLE_SQLSTATES:
        return True
    # A dropped connection has no SQLSTATE at all.
    return isinstance(e, psycopg.OperationalError) and not state


class PostgresExecutor:
    """Runs one validated SELECT per call on a pooled read-only connection.

    Per query: BEGIN READ ONLY; SET LOCAL statement_timeout; DECLARE a server-side cursor;
    FETCH row_cap + 1; ROLLBACK. Fetching one extra row detects truncation without rewriting
    the SQL, so the query's own ORDER BY is kept (design E2).
    """

    def __init__(self, cfg: EngineConfig, *, min_size: int = 1, max_size: int = 5) -> None:
        self.pool = AsyncConnectionPool(
            conninfo(cfg),
            min_size=min_size,
            max_size=max_size,
            configure=self._configure,
            open=False,
        )

    @staticmethod
    async def _configure(conn: psycopg.AsyncConnection) -> None:
        await conn.set_read_only(True)

    async def open(self, *, wait: bool = True) -> None:
        """Open the pool. ``wait=False`` returns at once and connects in the background (the
        API starts even while the database is down, and reports ``degraded``)."""
        await self.pool.open(wait=wait, timeout=CONNECT_TIMEOUT_S)

    async def close(self) -> None:
        await self.pool.close()

    async def execute(self, sql_text: str, row_cap: int | None, timeout_ms: int) -> ExecResult:
        try:
            async with self.pool.connection() as conn, conn.transaction(force_rollback=True):
                await conn.execute(
                    sql.SQL("SET LOCAL statement_timeout = {}").format(sql.Literal(timeout_ms))
                )
                await conn.execute(
                    sql.SQL("SET LOCAL idle_in_transaction_session_timeout = {}").format(
                        sql.Literal(timeout_ms + IDLE_IN_TRANSACTION_MARGIN_MS)
                    )
                )
                async with conn.cursor(name="qm_cur") as cur:
                    await cur.execute(sql_text)  # type: ignore[arg-type]
                    rows = (
                        await cur.fetchall()
                        if row_cap is None
                        else await cur.fetchmany(row_cap + 1)
                    )
                    description = cur.description or []
                    columns = tuple(_result_column(d.name, d.type_code, conn) for d in description)
        except psycopg.Error as e:
            if _is_unavailable(e):
                raise TargetDBUnavailable(str(e).strip() or type(e).__name__) from e
            code = _SQLSTATE_CODES.get(e.sqlstate or "", "EXECUTION_ERROR")
            raise ExecutionError(code, _message(e)) from e

        truncated = row_cap is not None and len(rows) > row_cap
        if truncated:
            rows = rows[:row_cap]
        encoded = [tuple(encode_value(v) for v in row) for row in rows]
        return ExecResult(columns=columns, rows=encoded, truncated=truncated)
