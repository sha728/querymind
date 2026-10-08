"""SchemaSnapshot -> `CREATE TABLE` prompt text (design §8.1)."""

import re
from collections.abc import Sequence

from qm_engine.schema.models import Column, Dialect, SchemaSnapshot, Table

_NUMERIC_TYPES = frozenset({"integer", "numeric"})

# Identifiers that need no quoting. PostgreSQL folds unquoted names to lower case,
# so mixed-case names must be quoted there; SQLite is case-insensitive.
_PLAIN_IDENT = {
    "postgres": re.compile(r"^[a-z_][a-z0-9_]*$"),
    "sqlite": re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$"),
}


def quote_ident(name: str, dialect: Dialect) -> str:
    if _PLAIN_IDENT[dialect].match(name):
        return name
    return '"' + name.replace('"', '""') + '"'


def _sample_literal(value: str, column: Column) -> str:
    value = " ".join(value.split())  # keep the comment on one line
    if column.norm_type in _NUMERIC_TYPES:
        return value
    return "'" + value.replace("'", "''") + "'"


def _idents(names: Sequence[str], dialect: Dialect) -> str:
    return ", ".join(quote_ident(n, dialect) for n in names)


def serialize_table(table: Table, dialect: Dialect) -> str:
    pk = table.primary_key
    inline_fks = {fk.columns[0]: fk for fk in table.foreign_keys if len(fk.columns) == 1}

    # Each entry is (definition, trailing comment or "").
    lines: list[tuple[str, str]] = []
    for col in table.columns:
        parts = [quote_ident(col.name, dialect), col.db_type]
        if len(pk) == 1 and col.primary_key:
            parts.append("PRIMARY KEY")
        fk = inline_fks.get(col.name)
        if fk is not None:
            parts.append(
                f"REFERENCES {quote_ident(fk.ref_table, dialect)}"
                f"({_idents(fk.ref_columns, dialect)})"
            )
        comment = ""
        if col.samples:
            comment = "e.g. " + ", ".join(_sample_literal(s, col) for s in col.samples)
        lines.append((" ".join(parts), comment))

    if len(pk) > 1:
        lines.append((f"PRIMARY KEY ({_idents(pk, dialect)})", ""))
    for fk in table.foreign_keys:
        if len(fk.columns) > 1:
            lines.append(
                (
                    f"FOREIGN KEY ({_idents(fk.columns, dialect)}) "
                    f"REFERENCES {quote_ident(fk.ref_table, dialect)}"
                    f"({_idents(fk.ref_columns, dialect)})",
                    "",
                )
            )

    body = []
    for i, (definition, comment) in enumerate(lines):
        sep = "," if i < len(lines) - 1 else ""
        text = f"  {definition}{sep}"
        if comment:
            text += f"  -- {comment}"
        body.append(text)

    return f"CREATE TABLE {quote_ident(table.name, dialect)} (\n" + "\n".join(body) + "\n);"


def serialize_schema(snapshot: SchemaSnapshot, table_names: Sequence[str] | None = None) -> str:
    """Serialize all tables, or only ``table_names`` (in snapshot order) after linking."""
    wanted = None if table_names is None else set(table_names)
    if wanted is not None:
        missing = wanted - {t.name for t in snapshot.tables}
        if missing:
            raise KeyError(f"unknown tables: {sorted(missing)}")
    return "\n\n".join(
        serialize_table(t, snapshot.dialect)
        for t in snapshot.tables
        if wanted is None or t.name in wanted
    )
