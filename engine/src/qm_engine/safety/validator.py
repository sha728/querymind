"""AST safety validator: runs before any database contact (design §7, R4.2, R4.3).

Rules are evaluated in a fixed order and the first failure wins. Forbidden content is
checked across *all* statements before statements are counted, so ``SELECT 1; DROP TABLE x``
is a blocking ``FORBIDDEN_STATEMENT``, not a retryable ``MULTIPLE_STATEMENTS``.
"""

import logging
from dataclasses import dataclass
from typing import Literal

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

from qm_engine.safety.forbidden import FORBIDDEN_STATEMENT_NODES, is_forbidden_function
from qm_engine.schema.models import Dialect

# sqlglot logs a warning for every statement it falls back to parsing as `Command`.
# Those statements are rejected by V4; the warning is noise.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

MAX_SQL_CHARS = 20_000

RejectCode = Literal[
    "EMPTY_SQL",
    "TOO_LONG",
    "PARSE_ERROR",
    "FORBIDDEN_STATEMENT",
    "FORBIDDEN_FUNCTION",
    "MULTIPLE_STATEMENTS",
    "NOT_SELECT",
]

# Blocking rejections end the request as `blocked` and are never sent back for correction.
BLOCKING_CODES: frozenset[RejectCode] = frozenset({"FORBIDDEN_STATEMENT", "FORBIDDEN_FUNCTION"})


@dataclass(frozen=True)
class Rejection:
    code: RejectCode
    message: str  # Shown to the model in the correction prompt (design §8.2) and to the user.

    @property
    def blocking(self) -> bool:
        return self.code in BLOCKING_CODES


@dataclass(frozen=True)
class ValidationResult:
    rejection: Rejection | None = None
    statement: exp.Expression | None = None  # the single parsed SELECT when accepted

    @property
    def ok(self) -> bool:
        return self.rejection is None


def _reject(code: RejectCode, message: str) -> ValidationResult:
    return ValidationResult(rejection=Rejection(code, message))


def _function_names(node: exp.Func) -> set[str]:
    """Every name a function node can go by: its source name for unknown functions, and
    its canonical and alias names for functions sqlglot models as typed nodes."""
    if isinstance(node, exp.Anonymous):
        return {node.name}
    return {node.sql_name(), *getattr(type(node), "_sql_names", ())}


def _is_select_root(node: exp.Expression) -> bool:
    while isinstance(node, exp.Subquery):
        node = node.this
    return isinstance(node, exp.Select | exp.SetOperation)


def validate(sql: str, dialect: Dialect) -> ValidationResult:
    # V1 / V2 before parsing.
    if not sql.strip():
        return _reject("EMPTY_SQL", "The reply contained no SQL query.")
    if len(sql) > MAX_SQL_CHARS:
        return _reject("TOO_LONG", f"The query is longer than {MAX_SQL_CHARS} characters.")

    # V3
    try:
        parsed = sqlglot.parse(sql, read=dialect)
    except (ParseError, TokenError) as e:
        return _reject("PARSE_ERROR", f"The query could not be parsed: {str(e)[:300]}")
    statements = [s for s in parsed if s is not None and not isinstance(s, exp.Semicolon)]
    if not statements:
        return _reject("EMPTY_SQL", "The reply contained no SQL query.")

    # V4: walk every node of every statement.
    for stmt in statements:
        for node in stmt.walk():
            if isinstance(node, FORBIDDEN_STATEMENT_NODES):
                return _reject(
                    "FORBIDDEN_STATEMENT",
                    f"Statement type '{type(node).__name__}' is not allowed; "
                    "only read-only SELECT queries can run.",
                )

    # V5: dangerous functions anywhere, in any statement.
    for stmt in statements:
        for node in stmt.find_all(exp.Func):
            for name in sorted(_function_names(node)):
                if is_forbidden_function(name):
                    return _reject(
                        "FORBIDDEN_FUNCTION",
                        f"Function '{name.lower()}' is not allowed.",
                    )

    # V6
    if len(statements) > 1:
        return _reject("MULTIPLE_STATEMENTS", "Return exactly one statement.")

    # V7
    (stmt,) = statements
    if not _is_select_root(stmt):
        return _reject("NOT_SELECT", "Return a SELECT query only.")

    return ValidationResult(statement=stmt)
