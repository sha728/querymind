"""Deny-lists for the safety validator (design §7.2 V4, §7.3 V5).

sqlglot node class names change between versions, so they are listed here by name and
resolved once at import. A missing name raises immediately (and a test checks it), so a
sqlglot upgrade fails loudly instead of silently weakening rule V4.
"""

from sqlglot import exp

# V4: any of these node types anywhere in any statement's tree is a blocking rejection.
FORBIDDEN_STATEMENT_NODE_NAMES: tuple[str, ...] = (
    # DML
    "Insert",
    "Update",
    "Delete",
    "Merge",
    # DDL
    "Create",
    "Drop",
    "Alter",
    "TruncateTable",
    # DCL
    "Grant",
    "Revoke",
    # Bulk I/O
    "Copy",
    # Unparsed statements (sqlglot's fallback): VACUUM, DO, CALL, SET ROLE, EXPLAIN, ...
    "Command",
    # Session / transaction control
    "Set",
    "Transaction",
    "Commit",
    "Rollback",
    # SELECT ... INTO (creates a table) and FOR UPDATE / FOR SHARE (takes row locks)
    "Into",
    "Lock",
    # SQLite
    "Pragma",
    "Attach",
    "Detach",
)


def resolve_node_classes(names: tuple[str, ...]) -> tuple[type[exp.Expression], ...]:
    missing = [n for n in names if not hasattr(exp, n)]
    if missing:
        raise ImportError(
            f"sqlglot.exp is missing node classes required by the validator: {missing}"
        )
    classes = tuple(getattr(exp, n) for n in names)
    not_nodes = [
        n for n, c in zip(names, classes, strict=True) if not issubclass(c, exp.Expression)
    ]
    if not_nodes:
        raise ImportError(f"not sqlglot expression classes: {not_nodes}")
    return classes


FORBIDDEN_STATEMENT_NODES = resolve_node_classes(FORBIDDEN_STATEMENT_NODE_NAMES)
