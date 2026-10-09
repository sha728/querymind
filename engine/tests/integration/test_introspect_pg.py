"""Integration tests against the running Northwind target-db (docker compose up target-db)."""

from qm_engine.config import EngineConfig
from qm_engine.schema.introspect_pg import introspect_pg, normalize_pg_type
from qm_engine.schema.models import ForeignKey

NORTHWIND_TABLES = {
    "categories",
    "customer_customer_demo",
    "customer_demographics",
    "customers",
    "employee_territories",
    "employees",
    "order_details",
    "orders",
    "products",
    "region",
    "shippers",
    "suppliers",
    "territories",
    "us_states",
}


async def test_finds_northwind_tables_keys_and_samples(target_cfg: EngineConfig) -> None:
    snap = await introspect_pg(target_cfg)
    assert snap.dialect == "postgres"
    assert {t.name for t in snap.tables} == NORTHWIND_TABLES

    orders = snap.table("orders")
    assert orders.primary_key == ("order_id",)
    assert ForeignKey(("customer_id",), "customers", ("customer_id",)) in orders.foreign_keys
    assert ForeignKey(("employee_id",), "employees", ("employee_id",)) in orders.foreign_keys

    details = snap.table("order_details")
    assert set(details.primary_key) == {"order_id", "product_id"}  # composite key
    assert {fk.ref_table for fk in details.foreign_keys} == {"orders", "products"}

    total_fks = sum(len(t.foreign_keys) for t in snap.tables)
    assert total_fks == 13
    assert all(t.primary_key for t in snap.tables)  # every Northwind table has a primary key

    for t in snap.tables:
        for c in t.columns:
            assert len(c.samples) <= 3, (t.name, c.name)
            assert len(set(c.samples)) == len(c.samples)
            assert all(len(s) <= 50 for s in c.samples)

    cols = {c.name: c for c in orders.columns}
    assert cols["order_id"].norm_type == "integer" and not cols["order_id"].nullable
    assert cols["order_date"].norm_type == "date"
    assert cols["freight"].norm_type == "numeric"
    assert cols["ship_country"].norm_type == "text"
    assert len(cols["order_date"].samples) == 3


async def test_binary_columns_have_no_samples(target_cfg: EngineConfig) -> None:
    snap = await introspect_pg(target_cfg)
    binary = [(t.name, c.name) for t in snap.tables for c in t.columns if c.db_type == "bytea"]
    assert binary, "Northwind has bytea columns (e.g. employees.photo)"
    for table, column in binary:
        col = next(c for c in snap.table(table).columns if c.name == column)
        assert col.samples == () and col.norm_type == "other"


async def test_schema_hash_and_samples_stable_across_runs(target_cfg: EngineConfig) -> None:
    first = await introspect_pg(target_cfg)
    second = await introspect_pg(target_cfg)
    assert first.schema_hash == second.schema_hash
    assert first.tables == second.tables  # samples are ordered, so prompts are stable


def test_normalize_pg_type() -> None:
    assert normalize_pg_type("smallint") == "integer"
    assert normalize_pg_type("character varying") == "text"
    assert normalize_pg_type("real") == "numeric"
    assert normalize_pg_type("timestamp with time zone") == "timestamp"
    assert normalize_pg_type("bytea") == "other"
