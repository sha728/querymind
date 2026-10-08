"""In-memory schema model shared by introspection, linking and prompting (design §5.3)."""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Dialect = Literal["postgres", "sqlite"]


@dataclass(frozen=True)
class Column:
    name: str
    db_type: str
    norm_type: str
    nullable: bool
    primary_key: bool
    samples: tuple[str, ...] = ()


@dataclass(frozen=True)
class ForeignKey:
    columns: tuple[str, ...]
    ref_table: str
    ref_columns: tuple[str, ...]


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]
    foreign_keys: tuple[ForeignKey, ...] = ()

    @property
    def primary_key(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns if c.primary_key)


@dataclass(frozen=True)
class SchemaSnapshot:
    dialect: Dialect
    tables: tuple[Table, ...]
    schema_hash: str
    introspected_at: datetime

    @classmethod
    def build(
        cls, dialect: Dialect, tables: Iterable[Table], introspected_at: datetime
    ) -> "SchemaSnapshot":
        tables = tuple(tables)
        return cls(dialect, tables, compute_schema_hash(tables), introspected_at)

    def table(self, name: str) -> Table:
        for t in self.tables:
            if t.name == name:
                return t
        raise KeyError(name)


def compute_schema_hash(tables: Iterable[Table]) -> str:
    """SHA-256 of the canonical structure: tables, columns, types, keys.

    Samples are excluded so that data drift does not invalidate cached embeddings.
    Table order is irrelevant; column order is part of the structure.
    """
    canonical = sorted(
        (
            {
                "name": t.name,
                "columns": [
                    {
                        "name": c.name,
                        "db_type": c.db_type,
                        "norm_type": c.norm_type,
                        "nullable": c.nullable,
                        "primary_key": c.primary_key,
                    }
                    for c in t.columns
                ],
                "foreign_keys": sorted(
                    [list(fk.columns), fk.ref_table, list(fk.ref_columns)] for fk in t.foreign_keys
                ),
            }
            for t in tables
        ),
        key=lambda t: t["name"],
    )
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
