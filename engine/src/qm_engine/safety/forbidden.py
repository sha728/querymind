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


# V5: functions that must never run (design §7.3). Matched on the lower-cased function
# name with any schema prefix stripped (`pg_catalog.PG_SLEEP` -> `pg_sleep`).
FORBIDDEN_FUNCTIONS: frozenset[str] = frozenset(
    {
        # Sleep / DoS (generate_series stays allowed; the statement timeout bounds it)
        "pg_sleep",
        "pg_sleep_for",
        "pg_sleep_until",
        # File / OS access
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_ls_logdir",
        "pg_ls_waldir",
        "pg_ls_tmpdir",
        "pg_stat_file",
        # Large objects
        "lo_import",
        "lo_export",
        "lo_get",
        "lo_put",
        "lo_open",
        "lo_creat",
        "lo_create",
        "lo_unlink",
        "lo_from_bytea",
        # Dynamic SQL inside a SELECT (executes a SQL string, which could bypass V4)
        "query_to_xml",
        "query_to_xml_and_xmlschema",
        "query_to_xmlschema",
        "cursor_to_xml",
        "table_to_xml",
        "schema_to_xml",
        "database_to_xml",
        # Server control
        "pg_terminate_backend",
        "pg_cancel_backend",
        "pg_reload_conf",
        "pg_rotate_logfile",
        "pg_switch_wal",
        "pg_promote",
        # Settings
        "set_config",
        "current_setting",
        # SQLite
        "load_extension",
        "readfile",
        "writefile",
        "edit",
        "fts3_tokenizer",
    }
)

# Whole families blocked by prefix: remote/cross-database calls and advisory locks
# (session and transaction-level, blocking and `try_` variants).
FORBIDDEN_FUNCTION_PREFIXES: tuple[str, ...] = ("dblink", "pg_advisory_", "pg_try_advisory_")


def is_forbidden_function(name: str) -> bool:
    n = name.lower().rsplit(".", 1)[-1]
    return n in FORBIDDEN_FUNCTIONS or n.startswith(FORBIDDEN_FUNCTION_PREFIXES)
