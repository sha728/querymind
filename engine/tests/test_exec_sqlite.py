import time
from pathlib import Path

import pytest

from qm_engine.execution.base import ExecutionError, ResultColumn
from qm_engine.execution.sqlite import SqliteExecutor

FIXTURE = Path(__file__).parent / "fixtures" / "mini.sqlite"
TIMEOUT_MS = 10_000

INFINITE = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT count(*) FROM c"


@pytest.fixture
def ex() -> SqliteExecutor:
    return SqliteExecutor(FIXTURE)


async def test_select_returns_rows_columns_and_types(ex: SqliteExecutor) -> None:
    res = await ex.execute(
        "SELECT order_id, customer_id, amount FROM orders ORDER BY order_id", None, TIMEOUT_MS
    )
    assert res.columns == (
        ResultColumn("order_id", "integer", "INTEGER"),
        ResultColumn("customer_id", "text", "TEXT"),
        ResultColumn("amount", "numeric", "REAL"),
    )
    assert res.rows == [(10248, "ALFKI", 440.0), (10249, "ALFKI", 1863.4), (10250, "ANATR", 1552.6)]
    assert res.row_count == 3
    assert res.truncated is False


async def test_column_types_from_aliases_nulls_and_mixed(ex: SqliteExecutor) -> None:
    res = await ex.execute(
        "SELECT NULL AS empty_col, 1 AS i, 2.5 AS f, x'00' AS b UNION ALL SELECT NULL, 2, 3, x'01'",
        None,
        TIMEOUT_MS,
    )
    assert [(c.name, c.norm_type, c.db_type) for c in res.columns] == [
        ("empty_col", "other", "NULL"),
        ("i", "integer", "INTEGER"),
        ("f", "numeric", "REAL"),  # int + float in one column -> numeric
        ("b", "other", "BLOB"),
    ]


async def test_insert_fails_because_file_is_read_only(ex: SqliteExecutor) -> None:
    before = FIXTURE.stat().st_mtime_ns
    with pytest.raises(ExecutionError) as err:
        await ex.execute("INSERT INTO audit_log VALUES (2, 1)", None, TIMEOUT_MS)
    assert err.value.code == "READ_ONLY_VIOLATION"
    assert err.value.retryable is False
    assert FIXTURE.stat().st_mtime_ns == before


async def test_infinite_query_times_out(ex: SqliteExecutor) -> None:
    start = time.monotonic()
    with pytest.raises(ExecutionError) as err:
        await ex.execute(INFINITE, None, 300)
    elapsed = time.monotonic() - start
    assert err.value.code == "TIMEOUT"
    assert err.value.retryable is True
    assert elapsed < 0.3 + 1.0


async def test_row_cap_truncates(ex: SqliteExecutor) -> None:
    res = await ex.execute("SELECT customer_id FROM customers ORDER BY customer_id", 2, TIMEOUT_MS)
    assert res.rows == [("ALFKI",), ("ANATR",)]
    assert res.truncated is True


async def test_row_cap_equal_to_rows_is_not_truncated(ex: SqliteExecutor) -> None:
    res = await ex.execute("SELECT order_id FROM orders", 3, TIMEOUT_MS)
    assert res.row_count == 3
    assert res.truncated is False


async def test_row_cap_none_returns_all_rows(ex: SqliteExecutor) -> None:
    res = await ex.execute("SELECT customer_id FROM customers", None, TIMEOUT_MS)
    assert res.row_count == 5
    assert res.truncated is False


async def test_sql_error_is_retryable_execution_error(ex: SqliteExecutor) -> None:
    with pytest.raises(ExecutionError) as err:
        await ex.execute("SELECT no_such_column FROM orders", None, TIMEOUT_MS)
    assert err.value.code == "EXECUTION_ERROR"
    assert "no_such_column" in err.value.message
    assert err.value.retryable is True


async def test_error_message_truncated_to_500_chars(ex: SqliteExecutor) -> None:
    with pytest.raises(ExecutionError) as err:
        await ex.execute(f"SELECT {'x' * 900} FROM orders", None, TIMEOUT_MS)
    assert len(err.value.message) <= 500


async def test_invalid_utf8_text_does_not_fail(ex: SqliteExecutor) -> None:
    res = await ex.execute("SELECT notes FROM customers WHERE customer_id = 'BADUT'", None, 1000)
    assert res.rows == [("�A",)]
