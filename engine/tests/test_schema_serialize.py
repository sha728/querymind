from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from qm_engine.schema.models import Column, ForeignKey, SchemaSnapshot, Table, compute_schema_hash
from qm_engine.schema.serialize import quote_ident, serialize_schema, serialize_table

NOW = datetime(2026, 10, 8, tzinfo=UTC)

CUSTOMERS = Table(
    name="customers",
    columns=(
        Column("customer_id", "text", "text", False, True, ("VINET", "TOMSP")),
        Column("company_name", "varchar", "text", False, False, ("Vins et alcools Chevalier",)),
    ),
)
ORDERS = Table(
    name="orders",
    columns=(
        Column("order_id", "integer", "integer", False, True, ("10248", "10249", "10250")),
        Column("customer_id", "text", "text", True, False, ("VINET", "TOMSP")),
        Column("order_date", "date", "date", True, False, ("2026-07-04",)),
    ),
    foreign_keys=(ForeignKey(("customer_id",), "customers", ("customer_id",)),),
)
ORDER_DETAILS = Table(
    name="order_details",
    columns=(
        Column("order_id", "integer", "integer", False, True, ()),
        Column("product_id", "integer", "integer", False, True, ()),
        Column("unit_price", "real", "numeric", False, False, ("14.0", "9.8")),
        Column("note", "text", "text", True, False, ("it's\nfine",)),
    ),
    foreign_keys=(ForeignKey(("order_id",), "orders", ("order_id",)),),
)
SHIPMENTS = Table(
    name="Shipments",
    columns=(
        Column("order_id", "integer", "integer", False, False, ()),
        Column("product_id", "integer", "integer", False, False, ()),
        Column("Ship Date", "text", "text", True, False, ()),
    ),
    foreign_keys=(
        ForeignKey(("order_id", "product_id"), "order_details", ("order_id", "product_id")),
    ),
)


def snapshot(*tables: Table, dialect: str = "postgres") -> SchemaSnapshot:
    return SchemaSnapshot.build(dialect, tables, NOW)  # type: ignore[arg-type]


# --- serialization (snapshot tests pin the design §8.1 format) ---


def test_orders_matches_design_example() -> None:
    assert serialize_table(ORDERS, "postgres") == (
        "CREATE TABLE orders (\n"
        "  order_id integer PRIMARY KEY,  -- e.g. 10248, 10249, 10250\n"
        "  customer_id text REFERENCES customers(customer_id),  -- e.g. 'VINET', 'TOMSP'\n"
        "  order_date date  -- e.g. '2026-07-04'\n"
        ");"
    )


def test_composite_pk_numeric_samples_and_escaping() -> None:
    assert serialize_table(ORDER_DETAILS, "postgres") == (
        "CREATE TABLE order_details (\n"
        "  order_id integer REFERENCES orders(order_id),\n"
        "  product_id integer,\n"
        "  unit_price real,  -- e.g. 14.0, 9.8\n"
        "  note text,  -- e.g. 'it''s fine'\n"
        "  PRIMARY KEY (order_id, product_id)\n"
        ");"
    )


def test_composite_fk_and_identifier_quoting_postgres() -> None:
    assert serialize_table(SHIPMENTS, "postgres") == (
        'CREATE TABLE "Shipments" (\n'
        "  order_id integer,\n"
        "  product_id integer,\n"
        '  "Ship Date" text,\n'
        "  FOREIGN KEY (order_id, product_id) REFERENCES order_details(order_id, product_id)\n"
        ");"
    )


def test_sqlite_does_not_quote_mixed_case() -> None:
    assert quote_ident("Shipments", "sqlite") == "Shipments"
    assert quote_ident("Ship Date", "sqlite") == '"Ship Date"'
    assert quote_ident("Shipments", "postgres") == '"Shipments"'
    assert quote_ident('we"ird', "postgres") == '"we""ird"'


def test_serialize_schema_all_and_subset_keep_snapshot_order() -> None:
    snap = snapshot(CUSTOMERS, ORDERS, ORDER_DETAILS)
    full = serialize_schema(snap)
    assert full.split("\n\n") == [
        serialize_table(CUSTOMERS, "postgres"),
        serialize_table(ORDERS, "postgres"),
        serialize_table(ORDER_DETAILS, "postgres"),
    ]
    subset = serialize_schema(snap, ["order_details", "customers"])
    assert subset == serialize_table(CUSTOMERS, "postgres") + "\n\n" + serialize_table(
        ORDER_DETAILS, "postgres"
    )


def test_serialize_schema_rejects_unknown_table() -> None:
    with pytest.raises(KeyError, match="nope"):
        serialize_schema(snapshot(ORDERS), ["nope"])


# --- schema hash ---


def test_hash_is_stable_and_order_independent_for_tables() -> None:
    a = snapshot(CUSTOMERS, ORDERS)
    b = snapshot(ORDERS, CUSTOMERS)
    assert a.schema_hash == b.schema_hash == compute_schema_hash([CUSTOMERS, ORDERS])
    assert len(a.schema_hash) == 64
    # Pinned literal: an accidental change to the canonical form must be deliberate.
    assert a.schema_hash == "0312d6cdd3a1a68a1918db25060043d81f7124aa362d24f411fa985177f6ded3"


def test_hash_ignores_samples() -> None:
    changed_cols = tuple(replace(c, samples=("X", "Y", "Z")) for c in ORDERS.columns)
    assert compute_schema_hash([replace(ORDERS, columns=changed_cols)]) == compute_schema_hash(
        [ORDERS]
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: replace(c, name="order_ts"),
        lambda c: replace(c, db_type="timestamp"),
        lambda c: replace(c, norm_type="timestamp"),
        lambda c: replace(c, nullable=False),
        lambda c: replace(c, primary_key=True),
    ],
)
def test_hash_changes_when_a_column_changes(mutate: Callable[[Column], Column]) -> None:
    cols = list(ORDERS.columns)
    cols[2] = mutate(cols[2])
    assert compute_schema_hash([replace(ORDERS, columns=tuple(cols))]) != compute_schema_hash(
        [ORDERS]
    )


def test_hash_changes_when_fk_or_table_set_changes() -> None:
    base = compute_schema_hash([CUSTOMERS, ORDERS])
    assert compute_schema_hash([CUSTOMERS, replace(ORDERS, foreign_keys=())]) != base
    assert compute_schema_hash([CUSTOMERS]) != base
