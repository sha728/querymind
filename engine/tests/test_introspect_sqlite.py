import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from qm_engine.schema.introspect_sqlite import introspect_sqlite, normalize_sqlite_type
from qm_engine.schema.models import ForeignKey, SchemaSnapshot

FIXTURE = Path(__file__).parent / "fixtures" / "mini.sqlite"
NOW = datetime(2026, 10, 8, tzinfo=UTC)


@pytest.fixture(scope="module")
def snap() -> SchemaSnapshot:
    return introspect_sqlite(FIXTURE, now=NOW)


def test_tables_in_creation_order_without_views(snap: SchemaSnapshot) -> None:
    assert snap.dialect == "sqlite"
    assert snap.introspected_at == NOW
    assert [t.name for t in snap.tables] == [
        "customers",
        "products",
        "orders",
        "order_items",
        "shipments",
        "audit_log",
    ]


def test_columns_types_nullability(snap: SchemaSnapshot) -> None:
    cols = {c.name: c for c in snap.table("orders").columns}
    assert [c.name for c in snap.table("orders").columns] == [
        "order_id",
        "customer_id",
        "order_date",
        "shipped_at",
        "amount",
    ]
    assert (cols["order_id"].db_type, cols["order_id"].norm_type) == ("INT", "integer")
    assert cols["order_date"].norm_type == "date"
    assert cols["shipped_at"].norm_type == "timestamp"
    assert cols["amount"].norm_type == "numeric"
    assert cols["customer_id"].nullable is True
    assert cols["order_id"].nullable is False  # primary key

    products = {c.name: c for c in snap.table("products").columns}
    assert products["name"].nullable is False  # NOT NULL
    assert products["discontinued"].norm_type == "boolean"
    assert products["image"].norm_type == "other"


def test_primary_keys_single_and_composite(snap: SchemaSnapshot) -> None:
    assert snap.table("customers").primary_key == ("customer_id",)
    assert snap.table("products").primary_key == ("product_id",)
    assert set(snap.table("order_items").primary_key) == {"order_id", "product_id"}
    assert snap.table("shipments").primary_key == ()


def test_foreign_keys(snap: SchemaSnapshot) -> None:
    # Mixed-case target name resolved to the real table name.
    assert snap.table("orders").foreign_keys == (
        ForeignKey(("customer_id",), "customers", ("customer_id",)),
    )
    # `REFERENCES products` without columns resolves to the parent's primary key.
    assert set(snap.table("order_items").foreign_keys) == {
        ForeignKey(("order_id",), "orders", ("order_id",)),
        ForeignKey(("product_id",), "products", ("product_id",)),
    }
    # Composite FK keeps column order.
    assert snap.table("shipments").foreign_keys == (
        ForeignKey(("order_id", "product_id"), "order_items", ("order_id", "product_id")),
    )
    # FK to a table that does not exist is dropped.
    assert snap.table("audit_log").foreign_keys == ()


def test_samples_at_most_three_distinct_non_null(snap: SchemaSnapshot) -> None:
    for table in snap.tables:
        for col in table.columns:
            assert len(col.samples) <= 3, (table.name, col.name)
            assert len(set(col.samples)) == len(col.samples)
            assert all(len(s) <= 50 for s in col.samples)

    orders = {c.name: c for c in snap.table("orders").columns}
    assert orders["customer_id"].samples == ("ALFKI", "ANATR")  # distinct
    assert orders["shipped_at"].samples == ("1996-07-16 10:00:00",)  # NULLs skipped
    assert orders["order_id"].samples == ("10248", "10249", "10250")


def test_binary_skipped_and_bad_utf8_tolerated(snap: SchemaSnapshot) -> None:
    products = {c.name: c for c in snap.table("products").columns}
    assert products["image"].samples == ()  # BLOB column: no samples
    assert products["price"].samples == ("18.0", "19.0", "10.0")
    # customers.notes holds an invalid UTF-8 byte; it is decoded with a replacement char.
    notes = {c.name: c for c in snap.table("customers").columns}["notes"].samples
    assert notes == ("x", "�A")


def test_long_values_truncated_to_50_chars(tmp_path: Path) -> None:
    db = tmp_path / "long.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t VALUES (?)", ("y" * 80,))
    conn.commit()
    conn.close()
    (col,) = introspect_sqlite(db).table("t").columns
    assert col.samples == ("y" * 50,)


def test_hash_stable_across_runs() -> None:
    assert introspect_sqlite(FIXTURE).schema_hash == introspect_sqlite(FIXTURE).schema_hash


def test_opens_read_only() -> None:
    before = FIXTURE.stat().st_mtime_ns
    introspect_sqlite(FIXTURE)
    assert FIXTURE.stat().st_mtime_ns == before


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        introspect_sqlite(tmp_path / "nope.sqlite")


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        ("int", "integer"),
        ("BIGINT", "integer"),
        ("varchar(255)", "text"),
        ("TEXT", "text"),
        ("real", "numeric"),
        ("double", "numeric"),
        ("float", "numeric"),
        ("number", "numeric"),
        ("decimal(10,2)", "numeric"),
        ("NUMERIC", "numeric"),
        ("date", "date"),
        ("datetime", "timestamp"),
        ("TIMESTAMP", "timestamp"),
        ("bool", "boolean"),
        ("boolean", "boolean"),
        ("blob", "other"),
        ("", "other"),
        ("year", "other"),
    ],
)
def test_normalize_sqlite_type(declared: str, expected: str) -> None:
    assert normalize_sqlite_type(declared) == expected
