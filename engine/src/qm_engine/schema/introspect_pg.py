"""PostgreSQL introspection of the target database's ``public`` schema (design §6.2 step 0, R1.2).

Tables, columns and types come from ``information_schema``; primary and foreign keys from
``pg_constraint`` (column numbers mapped to names), which keeps composite keys in declared
order. Up to 3 distinct non-null sample values per column are read through the read-only
role inside a read-only transaction; binary, JSON and array columns get no samples
(design §5.3). Samples are ordered so prompts are stable between refreshes.
"""

from collections import defaultdict
from datetime import UTC, datetime

import psycopg
from psycopg import sql

from qm_engine.config import EngineConfig
from qm_engine.execution.postgres import connect_ro
from qm_engine.observability import Timer, get_logger
from qm_engine.schema.models import Column, ForeignKey, SchemaSnapshot, Table

MAX_SAMPLES = 3
MAX_SAMPLE_CHARS = 50
SAMPLE_TIMEOUT_MS = 2000
SCHEMA = "public"

_log = get_logger()

# information_schema data_type -> normalized type (design §4.2)
_NORM: dict[str, str] = {
    "smallint": "integer",
    "integer": "integer",
    "bigint": "integer",
    "numeric": "numeric",
    "real": "numeric",
    "double precision": "numeric",
    "money": "numeric",
    "text": "text",
    "character varying": "text",
    "character": "text",
    "boolean": "boolean",
    "date": "date",
    "timestamp without time zone": "timestamp",
    "timestamp with time zone": "timestamp",
}
_NO_SAMPLES = frozenset({"bytea", "json", "jsonb", "ARRAY", "USER-DEFINED", "xml"})

_COLUMNS = """
SELECT c.table_name, c.column_name, c.data_type, c.udt_name, c.is_nullable
FROM information_schema.columns c
JOIN information_schema.tables t
  ON t.table_schema = c.table_schema AND t.table_name = c.table_name
WHERE c.table_schema = %s AND t.table_type = 'BASE TABLE'
ORDER BY c.table_name, c.ordinal_position
"""

_CONSTRAINTS = """
SELECT con.contype,
       rel.relname AS table_name,
       ARRAY(SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY k(num, ord)
             JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.num
             ORDER BY k.ord) AS columns,
       ref.relname AS ref_table,
       ARRAY(SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY k(num, ord)
             JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.num
             ORDER BY k.ord) AS ref_columns
FROM pg_constraint con
JOIN pg_class rel ON rel.oid = con.conrelid
JOIN pg_namespace ns ON ns.oid = rel.relnamespace
LEFT JOIN pg_class ref ON ref.oid = con.confrelid
WHERE ns.nspname = %s AND con.contype IN ('p', 'f')
ORDER BY rel.relname, con.conname
"""


def normalize_pg_type(data_type: str) -> str:
    return _NORM.get(data_type, "other")


async def _table_samples(
    conn: psycopg.AsyncConnection, table: str, columns: list[str]
) -> dict[str, tuple[str, ...]]:
    """Up to 3 distinct non-null values per column, in one round trip per table.

    Each column's values are ordered by the column itself and numbered, so the result is
    deterministic; values are cast to text only after ordering.
    """
    if not columns:
        return {}
    parts = [
        sql.SQL(
            "SELECT {name} AS col, rn, v::text AS val FROM ("
            "SELECT v, row_number() OVER (ORDER BY v) AS rn FROM ("
            "SELECT DISTINCT {col} AS v FROM {schema}.{tbl} WHERE {col} IS NOT NULL "
            "ORDER BY 1 LIMIT {n}) d) s"
        ).format(
            name=sql.Literal(column),
            col=sql.Identifier(column),
            schema=sql.Identifier(SCHEMA),
            tbl=sql.Identifier(table),
            n=sql.Literal(MAX_SAMPLES),
        )
        for column in columns
    ]
    query = sql.SQL(" UNION ALL ").join(parts)
    async with conn.transaction():
        await conn.execute(
            sql.SQL("SET LOCAL statement_timeout = {}").format(sql.Literal(SAMPLE_TIMEOUT_MS))
        )
        rows = await (await conn.execute(query)).fetchall()
    found: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for column, rn, value in rows:
        found[column].append((rn, value))
    return {c: tuple(v[:MAX_SAMPLE_CHARS] for _, v in sorted(found.get(c, []))) for c in columns}


async def introspect_pg(cfg: EngineConfig, *, now: datetime | None = None) -> SchemaSnapshot:
    with Timer() as t:
        conn = await connect_ro(cfg)
        try:
            async with conn.transaction():
                col_rows = await (await conn.execute(_COLUMNS, (SCHEMA,))).fetchall()
                con_rows = await (await conn.execute(_CONSTRAINTS, (SCHEMA,))).fetchall()

            pks: dict[str, set[str]] = defaultdict(set)
            fks: dict[str, list[ForeignKey]] = defaultdict(list)
            for contype, table, columns, ref_table, ref_columns in con_rows:
                if contype == "p":
                    pks[table].update(columns)
                else:
                    fks[table].append(ForeignKey(tuple(columns), ref_table, tuple(ref_columns)))

            cols_by_table: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
            for table, column, data_type, _udt, is_nullable in col_rows:
                cols_by_table[table].append((column, data_type, is_nullable))

            by_table: dict[str, list[Column]] = {}
            for table, cols in cols_by_table.items():
                sampled = [c for c, data_type, _ in cols if data_type not in _NO_SAMPLES]
                samples = await _table_samples(conn, table, sampled)
                by_table[table] = [
                    Column(
                        name=column,
                        db_type=data_type,
                        norm_type=normalize_pg_type(data_type),
                        nullable=is_nullable == "YES",
                        primary_key=column in pks[table],
                        samples=samples.get(column, ()),
                    )
                    for column, data_type, is_nullable in cols
                ]
        finally:
            await conn.close()

    tables = [Table(name, tuple(cols), tuple(fks[name])) for name, cols in by_table.items()]
    snapshot = SchemaSnapshot.build("postgres", tables, now or datetime.now(UTC))
    _log.info(
        "schema_introspected",
        dialect="postgres",
        tables=len(tables),
        schema_hash=snapshot.schema_hash,
        latency_ms=t.elapsed_ms,
    )
    return snapshot
