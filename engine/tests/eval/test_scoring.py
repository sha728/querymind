import time
from pathlib import Path

import pytest

from qm_eval.scoring import Score, execution_match, hardness

DB = Path(__file__).parent / "fixtures" / "spider_mini" / "database" / "mini" / "mini.sqlite"
T = 10.0


def ex(pred: str | None, gold: str, timeout_s: float = T) -> Score:
    return execution_match(DB, pred, gold, timeout_s)


# --- execution match ---


def test_identical_query_is_correct() -> None:
    assert ex("SELECT count(*) FROM customers", "SELECT count(*) FROM customers") == Score(True)


def test_row_order_ignored_without_order_by() -> None:
    gold = "SELECT customer_id FROM customers"
    assert ex("SELECT customer_id FROM customers ORDER BY customer_id DESC", gold).correct


def test_row_order_matters_with_gold_order_by() -> None:
    gold = "SELECT name FROM products ORDER BY price DESC"
    assert ex("SELECT name FROM products ORDER BY price DESC", gold).correct
    assert not ex("SELECT name FROM products ORDER BY price ASC", gold).correct


def test_multiset_semantics_duplicates_count() -> None:
    gold = "SELECT customer_id FROM orders"  # ALFKI appears twice
    assert ex("SELECT customer_id FROM orders", gold).correct
    assert not ex("SELECT customer_id FROM orders WHERE customer_id = 'ALFKI'", gold).correct


def test_distinct_is_ignored_like_the_official_metric() -> None:
    # keep_distinct=False: the official matcher strips DISTINCT from both queries.
    assert ex("SELECT DISTINCT customer_id FROM orders", "SELECT customer_id FROM orders").correct


def test_column_order_is_ignored_like_the_official_metric() -> None:
    gold = "SELECT name, price FROM products"
    assert ex("SELECT price, name FROM products", gold).correct


def test_different_result_is_incorrect() -> None:
    gold = "SELECT count(*) FROM customers"
    assert ex("SELECT count(*) FROM products", gold) == Score(False)


def test_prediction_error_is_incorrect_not_gold_error() -> None:
    result = ex("SELECT no_such_column FROM customers", "SELECT count(*) FROM customers")
    assert result == Score(False)
    assert result.scored


@pytest.mark.parametrize("pred", [None, "", "   "])
def test_missing_prediction_is_incorrect(pred: str | None) -> None:
    # Gold here returns no rows; an empty statement must not "match" it.
    gold = "SELECT name FROM products WHERE price > 1000"
    assert ex(pred, gold) == Score(False)


def test_gold_error_is_reported_and_excluded() -> None:
    result = ex("SELECT 1", "SELECT * FROM no_such_table")
    assert result.correct is False
    assert result.gold_error is not None
    assert not result.scored


def test_gold_error_reported_even_without_prediction() -> None:
    result = ex(None, "SELECT * FROM no_such_table")
    assert result.gold_error is not None and not result.scored


def test_slow_prediction_times_out_as_incorrect() -> None:
    slow = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT count(*) FROM c"
    start = time.monotonic()
    assert ex(slow, "SELECT count(*) FROM customers", timeout_s=0.3) == Score(False)
    assert time.monotonic() - start < 0.3 * 2 + 1.0  # gold + pred, each bounded


def test_scoring_opens_database_read_only() -> None:
    before = DB.stat().st_mtime_ns
    assert not ex("DELETE FROM audit_log", "SELECT count(*) FROM audit_log").correct
    assert DB.stat().st_mtime_ns == before


# --- hardness (official buckets, values from the vendored function) ---


@pytest.mark.parametrize(
    ("gold", "expected"),
    [
        ("SELECT count(*) FROM customers", "easy"),
        ("SELECT name ,  price FROM products ORDER BY price DESC", "medium"),
        (
            "SELECT customer_id FROM orders GROUP BY customer_id HAVING count(*) > 1 "
            "ORDER BY count(*) DESC LIMIT 1",
            "hard",
        ),
        (
            "SELECT name FROM products WHERE product_id NOT IN "
            "(SELECT product_id FROM order_items) UNION SELECT name FROM products WHERE price > 15",
            "extra",
        ),
    ],
)
def test_hardness(gold: str, expected: str) -> None:
    assert hardness(DB, gold) == expected
