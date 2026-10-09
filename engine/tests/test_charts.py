import pytest

from qm_engine.charts import TABLE_ONLY, ChartSpec, recommend_chart
from qm_engine.execution.base import ResultColumn


def cols(*spec: str) -> list[ResultColumn]:
    """'name:type' pairs -> result columns."""
    out = []
    for s in spec:
        name, norm = s.split(":")
        out.append(ResultColumn(name, norm, norm))
    return out


def rows(n: int, *values: object) -> list[tuple[object, ...]]:
    return [tuple(values) for _ in range(n)]


# --- C1: empty or scalar ---


def test_c1_no_rows() -> None:
    assert recommend_chart(cols("a:text", "b:integer"), []) == TABLE_ONLY


def test_c1_single_value() -> None:
    assert recommend_chart(cols("count:integer"), [(42,)]) == TABLE_ONLY


# --- C2: time series ---


def test_c2_line_for_date_and_measures() -> None:
    spec = recommend_chart(
        cols("month:date", "orders:integer", "revenue:numeric", "freight:numeric", "avg:numeric"),
        rows(12, "2026-01-01", 10, 99.5, 3.2, 1.0),
    )
    assert spec == ChartSpec(
        "line", ("line", "bar", "table"), x="month", y=("orders", "revenue", "freight")
    )


@pytest.mark.parametrize("name", ["year", "month", "order_year", "Year"])
def test_c2_integer_year_is_temporal(name: str) -> None:
    spec = recommend_chart(cols(f"{name}:integer", "total:numeric"), [(2024, 1.0), (2025, 2.0)])
    assert spec.recommended == "line" and spec.x == name


def test_c2_needs_two_rows() -> None:
    spec = recommend_chart(cols("day:date", "total:numeric"), [("2026-01-01", 5.0)])
    assert spec == TABLE_ONLY


# --- C3: pie ---


def test_c3_pie_for_one_category_one_measure_few_rows() -> None:
    spec = recommend_chart(cols("country:text", "customers:integer"), rows(4, "France", 11))
    assert spec == ChartSpec("pie", ("pie", "bar", "table"), x="country", y=("customers",))


def test_c3_boundary_six_rows_is_pie_seven_is_bar() -> None:
    six = recommend_chart(cols("c:text", "n:integer"), rows(6, "x", 1))
    seven = recommend_chart(cols("c:text", "n:integer"), rows(7, "x", 1))
    assert six.recommended == "pie"
    assert seven.recommended == "bar" and "pie" not in seven.allowed


def test_c3_negative_values_are_not_a_pie() -> None:
    spec = recommend_chart(cols("c:text", "delta:numeric"), [("a", 5.0), ("b", -2.0)])
    assert spec.recommended == "bar" and "pie" not in spec.allowed


# --- C4: bar ---


def test_c4_bar_for_categories_and_up_to_three_measures() -> None:
    spec = recommend_chart(
        cols("product:text", "units:integer", "revenue:numeric"), rows(20, "Chai", 5, 90.0)
    )
    assert spec == ChartSpec("bar", ("bar", "table"), x="product", y=("units", "revenue"))


def test_c4_boundary_fifty_rows_is_bar_fifty_one_is_table() -> None:
    assert recommend_chart(cols("c:text", "n:integer"), rows(50, "x", 1)).recommended == "bar"
    assert recommend_chart(cols("c:text", "n:integer"), rows(51, "x", 1)) == TABLE_ONLY


def test_c4_pie_also_allowed_when_pie_conditions_hold() -> None:
    # Two category columns (so not C3) but one non-negative measure and few rows.
    spec = recommend_chart(
        cols("country:text", "city:text", "n:integer"), rows(5, "UK", "London", 3)
    )
    assert spec.recommended == "bar" and spec.allowed == ("bar", "table", "pie")


def test_c4_more_than_three_measures_is_table() -> None:
    spec = recommend_chart(
        cols("c:text", "a:numeric", "b:numeric", "d:numeric", "e:numeric"), rows(5, "x", 1, 2, 3, 4)
    )
    assert spec == TABLE_ONLY


# --- C5 and roles ---


def test_c5_no_numeric_column() -> None:
    assert recommend_chart(cols("name:text", "city:text"), rows(5, "a", "b")) == TABLE_ONLY


@pytest.mark.parametrize("name", ["customer_id", "id", "Order_ID"])
def test_id_columns_are_not_measures(name: str) -> None:
    spec = recommend_chart(cols("name:text", f"{name}:integer"), rows(4, "a", 1))
    assert spec == TABLE_ONLY  # an id is not a quantity to chart


def test_boolean_is_categorical() -> None:
    spec = recommend_chart(
        cols("discontinued:boolean", "products:integer"), [(True, 8), (False, 69)]
    )
    assert spec.recommended == "pie" and spec.x == "discontinued"


def test_nulls_ignored_for_pie_values() -> None:
    spec = recommend_chart(cols("c:text", "n:numeric"), [("a", None), ("b", 3.0), ("c", 1.0)])
    assert spec.recommended == "pie"
