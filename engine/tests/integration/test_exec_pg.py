"""PostgresExecutor against the running Northwind target-db (design §6.5, §7.1 L1/L2)."""

import time
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal

import psycopg
import pytest

from qm_engine.config import EngineConfig
from qm_engine.execution.base import ExecutionError
from qm_engine.execution.postgres import (
    _SQLSTATE_CODES,
    PostgresExecutor,
    conninfo,
    encode_value,
)

TIMEOUT_MS = 10_000


@pytest.fixture
async def ex(target_cfg: EngineConfig) -> AsyncIterator[PostgresExecutor]:
    executor = PostgresExecutor(target_cfg, max_size=2)
    await executor.open()
    try:
        yield executor
    finally:
        await executor.close()


# --- cursor + FETCH cap+1 ---


async def test_row_cap_truncates_and_keeps_order_by(ex: PostgresExecutor) -> None:
    res = await ex.execute("SELECT order_id FROM orders ORDER BY order_id DESC", 5, TIMEOUT_MS)
    assert res.row_count == 5
    assert res.truncated is True
    ids = [r[0] for r in res.rows]
    assert ids == sorted(ids, reverse=True)  # the query's own ORDER BY survives the cap
    assert ids[0] == 11077  # Northwind's highest order id


async def test_exactly_cap_rows_is_not_truncated(ex: PostgresExecutor) -> None:
    res = await ex.execute("SELECT shipper_id FROM shippers", 6, TIMEOUT_MS)
    assert res.row_count == 6 and res.truncated is False


async def test_row_cap_none_returns_all_rows(ex: PostgresExecutor) -> None:
    res = await ex.execute("SELECT customer_id FROM customers", None, TIMEOUT_MS)
    assert res.row_count == 91 and res.truncated is False


# --- timeout ---


async def test_statement_timeout_maps_to_timeout(ex: PostgresExecutor) -> None:
    start = time.monotonic()
    with pytest.raises(ExecutionError) as err:
        await ex.execute("SELECT pg_sleep(5)", 10, 500)  # validator bypassed on purpose
    assert err.value.code == "TIMEOUT"
    assert err.value.retryable is True
    assert time.monotonic() - start < 3.0


async def test_executor_still_works_after_a_timeout(ex: PostgresExecutor) -> None:
    with pytest.raises(ExecutionError):
        await ex.execute("SELECT pg_sleep(5)", 10, 300)
    res = await ex.execute("SELECT count(*) FROM orders", 10, TIMEOUT_MS)
    assert res.rows == [(830,)]


# --- writes refused by the database itself (validator bypassed on purpose) ---
#
# Three independent barriers sit below the validator (design §7.1):
#   cursor: the executor runs every query as DECLARE ... CURSOR FOR <sql>, which only accepts
#           a query: writes are syntax errors, data-modifying CTEs are refused outright;
#   L2:     every pooled connection runs READ ONLY transactions;
#   L1:     the role has no write or create privileges, even with read-only switched off.

WRITES = [
    "INSERT INTO region VALUES (99, 'Nowhere')",
    "UPDATE products SET unit_price = 0",
    "DELETE FROM order_details",
    "CREATE TABLE x (a int)",
    "DROP TABLE orders",
    "WITH d AS (DELETE FROM region RETURNING *) SELECT * FROM d",
]


@pytest.mark.parametrize("statement", WRITES)
async def test_executor_refuses_every_write(ex: PostgresExecutor, statement: str) -> None:
    with pytest.raises(ExecutionError) as err:
        await ex.execute(statement, 10, TIMEOUT_MS)
    msg = err.value.message
    assert "syntax error" in msg or "must not contain data-modifying statements" in msg


async def test_l2_pooled_connections_are_read_only(ex: PostgresExecutor) -> None:
    async with ex.pool.connection() as conn:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            async with conn.transaction(force_rollback=True):
                await conn.execute("INSERT INTO region VALUES (99, 'Nowhere')")


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO region VALUES (99, 'Nowhere')",
        "UPDATE products SET unit_price = 0",
        "CREATE TABLE x (a int)",
    ],
)
async def test_l1_privileges_refuse_writes_even_with_read_only_off(
    target_cfg: EngineConfig, statement: str
) -> None:
    conn = await psycopg.AsyncConnection.connect(conninfo(target_cfg), autocommit=True)
    try:
        await conn.execute("SET default_transaction_read_only = off")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute(statement)
    finally:
        await conn.close()


def test_privilege_and_read_only_errors_map_to_blocking_code() -> None:
    # If one of these ever surfaces through the executor, it must block, never retry.
    for sqlstate in ("25006", "42501"):
        assert _SQLSTATE_CODES[sqlstate] == "READ_ONLY_VIOLATION"
    assert _SQLSTATE_CODES["57014"] == "TIMEOUT"


async def test_data_is_unchanged_after_refused_writes(ex: PostgresExecutor) -> None:
    for statement in ("DELETE FROM order_details", "UPDATE products SET unit_price = 0"):
        with pytest.raises(ExecutionError):
            await ex.execute(statement, 10, TIMEOUT_MS)
    res = await ex.execute(
        "SELECT (SELECT count(*) FROM order_details), (SELECT min(unit_price) FROM products)",
        10,
        TIMEOUT_MS,
    )
    count, min_price = res.rows[0]
    assert count == 2155 and min_price > 0


# --- errors ---


async def test_sql_error_is_retryable_with_primary_message(ex: PostgresExecutor) -> None:
    with pytest.raises(ExecutionError) as err:
        await ex.execute("SELECT no_such_column FROM orders", 10, TIMEOUT_MS)
    assert err.value.code == "EXECUTION_ERROR" and err.value.retryable
    assert 'column "no_such_column" does not exist' in err.value.message
    assert len(err.value.message) <= 500


# --- types and values (design §4.2) ---


async def test_column_types_and_value_encoding(ex: PostgresExecutor) -> None:
    res = await ex.execute(
        "SELECT o.order_id, o.customer_id, o.order_date, o.freight, "
        "od.unit_price * od.quantity::numeric AS total, o.order_date > '2000-01-01' AS recent, "
        "e.photo "
        "FROM orders o JOIN order_details od USING (order_id) JOIN employees e USING (employee_id) "
        "ORDER BY o.order_id LIMIT 1",
        10,
        TIMEOUT_MS,
    )
    assert [(c.name, c.norm_type) for c in res.columns] == [
        ("order_id", "integer"),
        ("customer_id", "text"),
        ("order_date", "date"),
        ("freight", "numeric"),
        ("total", "numeric"),
        ("recent", "boolean"),
        ("photo", "other"),
    ]
    order_id, customer_id, order_date, freight, total, recent, photo = res.rows[0]
    assert isinstance(order_id, int) and isinstance(customer_id, str)
    assert isinstance(order_date, str) and date.fromisoformat(order_date)  # ISO-8601 string
    assert isinstance(freight, float) and isinstance(total, float)  # numeric -> JSON number
    assert recent is True
    assert photo == "<binary>"


def test_encode_value() -> None:
    assert encode_value(Decimal("12.50")) == 12.5
    assert encode_value(date(2026, 9, 6)) == "2026-09-06"
    assert encode_value(b"\x00\x01") == "<binary>"
    assert encode_value(memoryview(b"x")) == "<binary>"
    assert encode_value(None) is None
    assert encode_value("text") == "text"
