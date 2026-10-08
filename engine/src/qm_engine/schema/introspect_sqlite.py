"""PRAGMA-based introspection of a SQLite database (design §6.2 step 0, R1.2, R9.6).

Used for Spider evaluation. The database is opened read-only (``mode=ro``).
"""

import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from sqlglot import exp

from qm_engine.execution.sqlite import connect_ro
from qm_engine.observability import get_logger
from qm_engine.schema.models import Column, ForeignKey, SchemaSnapshot, Table

MAX_SAMPLES = 3
MAX_SAMPLE_CHARS = 50

_log = get_logger()


def normalize_sqlite_type(declared: str) -> str:
    """Map a declared SQLite column type to the engine's normalized type (design §4.2).

    Follows SQLite's affinity rules, with date/time/boolean names recognised first.
    """
    t = declared.strip().lower()
    if not t:
        return "other"
    if "datetime" in t or "timestamp" in t:
        return "timestamp"
    if t.startswith("date"):
        return "date"
    if t.startswith("bool"):
        return "boolean"
    if "int" in t:
        return "integer"
    if "char" in t or "clob" in t or "text" in t:
        return "text"
    if "blob" in t:
        return "other"
    if any(k in t for k in ("real", "floa", "doub", "num", "dec")):
        return "numeric"
    return "other"


def _quote(name: str) -> str:
    return exp.to_identifier(name, quoted=True).sql(dialect="sqlite")


def _samples(conn: sqlite3.Connection, table: str, column: str, declared: str) -> tuple[str, ...]:
    if "blob" in declared.lower():
        return ()
    rows = conn.execute(
        f"SELECT DISTINCT {_quote(column)} FROM {_quote(table)} "
        f"WHERE {_quote(column)} IS NOT NULL LIMIT {MAX_SAMPLES}"
    ).fetchall()
    return tuple(str(v)[:MAX_SAMPLE_CHARS] for (v,) in rows if not isinstance(v, bytes))


def _foreign_keys(
    conn: sqlite3.Connection, table: str, by_lower_name: dict[str, str], pks: dict[str, list[str]]
) -> tuple[ForeignKey, ...]:
    groups: dict[int, list[tuple[int, str, str, str | None]]] = defaultdict(list)
    for fk_id, seq, ref_table, from_col, to_col, *_ in conn.execute(
        f"PRAGMA foreign_key_list({_quote(table)})"
    ):
        groups[fk_id].append((seq, ref_table, from_col, to_col))

    fks = []
    for fk_id in sorted(groups):
        parts = sorted(groups[fk_id])
        ref = by_lower_name.get(parts[0][1].lower())
        if ref is None:
            _log.warning("fk_dropped", table=table, ref_table=parts[0][1], reason="unknown table")
            continue
        from_cols = tuple(p[2] for p in parts)
        to_cols = [p[3] for p in parts]
        if any(c is None for c in to_cols):
            to_cols = pks[ref]  # `REFERENCES parent` without columns means its primary key
        if len(to_cols) != len(from_cols):
            _log.warning("fk_dropped", table=table, ref_table=ref, reason="column count")
            continue
        fks.append(ForeignKey(from_cols, ref, tuple(str(c) for c in to_cols)))
    return tuple(fks)


def introspect_sqlite(path: Path, *, now: datetime | None = None) -> SchemaSnapshot:
    conn = connect_ro(path)
    try:
        names = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY rowid"
            )
        ]
        by_lower_name = {n.lower(): n for n in names}

        infos = {n: conn.execute(f"PRAGMA table_info({_quote(n)})").fetchall() for n in names}
        # table_info rows: (cid, name, type, notnull, dflt_value, pk); pk > 0 is its position.
        pks = {
            n: [r[1] for r in sorted((r for r in rows if r[5]), key=lambda r: r[5])]
            for n, rows in infos.items()
        }

        tables = []
        for name in names:
            columns = tuple(
                Column(
                    name=col,
                    db_type=declared,
                    norm_type=normalize_sqlite_type(declared),
                    nullable=not notnull and not pk,
                    primary_key=bool(pk),
                    samples=_samples(conn, name, col, declared),
                )
                for _cid, col, declared, notnull, _default, pk in infos[name]
            )
            tables.append(Table(name, columns, _foreign_keys(conn, name, by_lower_name, pks)))
    finally:
        conn.close()

    return SchemaSnapshot.build("sqlite", tables, now or datetime.now(UTC))
