"""Builds tests/eval/fixtures/spider_mini/, a tiny dataset in Spider 1.0's layout.

Run: python tests/eval/fixtures/build_spider_mini.py
It reuses tests/fixtures/mini.sqlite as the database of db_id "mini".
"""

import json
import shutil
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE / "spider_mini"
SOURCE_DB = HERE.parents[1] / "fixtures" / "mini.sqlite"

DEV = [
    ("How many customers are there?", "SELECT count(*) FROM customers"),
    (
        "List the names of all products ordered by price descending.",
        "SELECT name FROM products ORDER BY price DESC",
    ),
    (
        "What is the total order amount per customer?",
        "SELECT customer_id, sum(amount) FROM orders GROUP BY customer_id",
    ),
    ("Which products are discontinued?", "SELECT name FROM products WHERE discontinued = 1"),
    (
        "How many items were ordered in order 10248?",
        "SELECT sum(quantity) FROM order_items WHERE order_id = 10248",
    ),
]
TRAIN = [
    ("How many orders are there?", "SELECT count(*) FROM orders"),
    ("List all customer ids.", "SELECT customer_id FROM customers"),
]


def _items(pairs: list[tuple[str, str]]) -> list[dict]:
    return [{"db_id": "mini", "question": q, "query": sql} for q, sql in pairs]


def build(root: Path = ROOT) -> Path:
    if root.exists():
        shutil.rmtree(root)
    (root / "database" / "mini").mkdir(parents=True)
    shutil.copyfile(SOURCE_DB, root / "database" / "mini" / "mini.sqlite")
    for name, pairs in (("dev.json", DEV), ("train_spider.json", TRAIN)):
        (root / name).write_text(json.dumps(_items(pairs), indent=2) + "\n", encoding="utf-8")
    (root / "tables.json").write_text("[]\n", encoding="utf-8")
    (root / "dev_gold.sql").write_text(
        "".join(f"{sql}\tmini\n" for _, sql in DEV), encoding="utf-8"
    )
    return root


if __name__ == "__main__":
    print(build())
