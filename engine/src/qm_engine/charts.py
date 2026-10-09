"""Chart recommendation from the result's shape (design §9, R6.1).

A pure function: column roles come from the normalized type (and a few name conventions), and
the first matching rule wins. ``allowed`` lists only chart types that can actually be drawn
from the data, so the UI's switcher (R6.2) never offers an impossible chart.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from qm_engine.execution.base import ResultColumn

ChartType = Literal["bar", "line", "pie", "table"]

MAX_PIE_ROWS = 6
MAX_BAR_ROWS = 50
MAX_SERIES = 3
_TEMPORAL_INT_NAMES = frozenset({"year", "month"})


@dataclass(frozen=True)
class ChartSpec:
    recommended: ChartType
    allowed: tuple[ChartType, ...]
    x: str | None = None
    y: tuple[str, ...] = ()


TABLE_ONLY = ChartSpec("table", ("table",))


def _is_id(name: str) -> bool:
    n = name.lower()
    return n == "id" or n.endswith("_id")


def _is_temporal(c: ResultColumn) -> bool:
    if c.norm_type in ("date", "timestamp"):
        return True
    n = c.name.lower()
    return c.norm_type == "integer" and (n in _TEMPORAL_INT_NAMES or n.endswith("_year"))


def _is_numeric(c: ResultColumn) -> bool:
    return c.norm_type in ("integer", "numeric") and not _is_id(c.name) and not _is_temporal(c)


def _is_categorical(c: ResultColumn) -> bool:
    return c.norm_type in ("text", "boolean")


def _non_negative(rows: Sequence[Sequence[object]], index: int) -> bool:
    values = [r[index] for r in rows if r[index] is not None]
    return bool(values) and all(isinstance(v, int | float) and v >= 0 for v in values)


def recommend_chart(columns: Sequence[ResultColumn], rows: Sequence[Sequence[object]]) -> ChartSpec:
    n_rows = len(rows)
    idx = {c.name: i for i, c in enumerate(columns)}
    temporal = [c.name for c in columns if _is_temporal(c)]
    numeric = [c.name for c in columns if _is_numeric(c)]
    categorical = [c.name for c in columns if _is_categorical(c)]

    # C1: nothing to plot, or a single value.
    if n_rows == 0 or (n_rows == 1 and len(columns) == 1):
        return TABLE_ONLY

    # C2: a time axis with at least one measure.
    if temporal and numeric and n_rows >= 2:
        return ChartSpec(
            "line", ("line", "bar", "table"), x=temporal[0], y=tuple(numeric[:MAX_SERIES])
        )

    pie_ok = (
        len(numeric) == 1 and 2 <= n_rows <= MAX_PIE_ROWS and _non_negative(rows, idx[numeric[0]])
    )

    # C3: one category and one non-negative measure with few slices.
    if len(categorical) == 1 and len(numeric) == 1 and pie_ok:
        return ChartSpec("pie", ("pie", "bar", "table"), x=categorical[0], y=(numeric[0],))

    # C4: categories against one to three measures.
    if categorical and 1 <= len(numeric) <= MAX_SERIES and 2 <= n_rows <= MAX_BAR_ROWS:
        allowed: tuple[ChartType, ...] = ("bar", "table", "pie") if pie_ok else ("bar", "table")
        return ChartSpec("bar", allowed, x=categorical[0], y=tuple(numeric))

    # C5: anything else.
    return TABLE_ONLY
