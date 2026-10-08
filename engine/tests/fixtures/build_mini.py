"""Builds tests/fixtures/mini.sqlite. Run: python tests/fixtures/build_mini.py

The schema deliberately includes quirks found in real Spider databases:
mixed-case FK targets, FKs without target columns, a dangling FK, composite keys,
identifiers with spaces, BLOB columns and non-UTF-8 text.
"""

import sqlite3
from pathlib import Path

PATH = Path(__file__).with_name("mini.sqlite")

SCHEMA = """
CREATE TABLE customers (
    customer_id TEXT PRIMARY KEY,
    company_name VARCHAR(40) NOT NULL,
    notes TEXT
);
CREATE TABLE products (
    product_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    price REAL,
    discontinued BOOLEAN,
    image BLOB
);
CREATE TABLE orders (
    order_id INT PRIMARY KEY,
    customer_id TEXT REFERENCES Customers(customer_id),
    order_date DATE,
    shipped_at DATETIME,
    amount NUMBER
);
CREATE TABLE order_items (
    order_id INT,
    product_id INT,
    quantity INT NOT NULL,
    PRIMARY KEY (product_id, order_id),
    FOREIGN KEY (order_id) REFERENCES orders(order_id),
    FOREIGN KEY (product_id) REFERENCES products
);
CREATE TABLE shipments (
    order_id INT,
    product_id INT,
    "Ship Date" TEXT,
    FOREIGN KEY (order_id, product_id) REFERENCES order_items(order_id, product_id)
);
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY,
    ref INT REFERENCES missing_table(id)
);
CREATE VIEW v_customers AS SELECT customer_id FROM customers;
"""

DATA = """
INSERT INTO customers VALUES
    ('ALFKI', 'Alfreds Futterkiste', NULL),
    ('ANATR', 'Ana Trujillo Emparedados', 'x'),
    ('ANTON', 'Antonio Moreno Taqueria', NULL),
    ('AROUT', 'Around the Horn', NULL);
INSERT INTO customers VALUES ('BADUT', 'Bad UTF8 Ltd', CAST(x'ff41' AS TEXT));
INSERT INTO products VALUES
    (1, 'Chai', 18.0, 0, x'00ff'),
    (2, 'Chang', 19.0, 0, NULL),
    (3, 'Aniseed Syrup', 10.0, 1, NULL);
INSERT INTO orders VALUES
    (10248, 'ALFKI', '1996-07-04', '1996-07-16 10:00:00', 440.0),
    (10249, 'ALFKI', '1996-07-05', NULL, 1863.4),
    (10250, 'ANATR', '1996-07-08', NULL, 1552.6);
INSERT INTO order_items VALUES (10248, 1, 12), (10248, 2, 10), (10249, 3, 5);
INSERT INTO shipments VALUES (10248, 1, '1996-07-16');
INSERT INTO audit_log VALUES (1, 99);
"""


def build(path: Path = PATH) -> Path:
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA + DATA)
        conn.commit()
    finally:
        conn.close()
    return path


if __name__ == "__main__":
    print(build())
