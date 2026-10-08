from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlglot import exp

from qm_engine.safety.forbidden import (
    FORBIDDEN_STATEMENT_NODE_NAMES,
    FORBIDDEN_STATEMENT_NODES,
    resolve_node_classes,
)
from qm_engine.safety.validator import BLOCKING_CODES, MAX_SQL_CHARS, validate

CORPUS_PATH = Path(__file__).with_name("safety_cases.yaml")


def load_corpus() -> list[dict[str, Any]]:
    with CORPUS_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


CORPUS = load_corpus()
UNSAFE = [c for c in CORPUS if c["expected_code"] is not None]
ALLOWED = [c for c in CORPUS if c["expected_code"] is None]


def block_rate(cases: list[dict[str, Any]]) -> tuple[int, int]:
    """M4: unsafe cases rejected before reaching the DB / total unsafe cases."""
    blocked = sum(1 for c in cases if not validate(c["sql"], c["dialect"]).ok)
    return blocked, len(cases)


def test_corpus_is_well_formed() -> None:
    ids = [c["id"] for c in CORPUS]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    for c in CORPUS:
        assert set(c) == {"id", "category", "dialect", "sql", "expected_code"}, c["id"]
        assert c["dialect"] in ("postgres", "sqlite"), c["id"]
        assert (c["category"] == "allowed") == (c["expected_code"] is None), c["id"]
    required = {
        "dml", "ddl", "dcl", "copy", "multi_statement", "data_modifying_cte",
        "select_into", "locking", "utility", "sqlite_specific", "allowed",
    }  # fmt: skip
    assert required <= {c["category"] for c in CORPUS}


@pytest.mark.parametrize("case", UNSAFE, ids=[c["id"] for c in UNSAFE])
def test_unsafe_case_rejected_with_expected_code(case: dict[str, Any]) -> None:
    result = validate(case["sql"], case["dialect"])
    assert not result.ok
    assert result.rejection is not None
    assert result.rejection.code == case["expected_code"]
    assert result.rejection.blocking == (case["expected_code"] in BLOCKING_CODES)
    assert result.statement is None


@pytest.mark.parametrize("case", ALLOWED, ids=[c["id"] for c in ALLOWED])
def test_allowed_case_passes(case: dict[str, Any]) -> None:
    result = validate(case["sql"], case["dialect"])
    assert result.ok, result.rejection
    assert result.statement is not None


def test_m4_block_rate_is_100_percent() -> None:
    blocked, total = block_rate(UNSAFE)
    print(f"\nM4 unsafe-SQL block rate: {blocked}/{total} = {100 * blocked / total:.1f}%")
    assert total > 0
    assert blocked == total


def test_too_long_rejected_before_parsing() -> None:
    sql = "SELECT " + " + ".join(["1"] * (MAX_SQL_CHARS // 4 + 10))
    assert len(sql) > MAX_SQL_CHARS
    result = validate(sql, "postgres")
    assert result.rejection is not None
    assert result.rejection.code == "TOO_LONG"
    assert result.rejection.blocking is False


def test_retryable_messages_match_correction_prompt() -> None:
    multi = validate("SELECT 1; SELECT 2", "postgres").rejection
    not_select = validate("VALUES (1)", "postgres").rejection
    assert multi is not None and multi.message == "Return exactly one statement."
    assert not_select is not None and not_select.message == "Return a SELECT query only."


def test_every_forbidden_node_name_resolves_in_pinned_sqlglot() -> None:
    assert len(FORBIDDEN_STATEMENT_NODES) == len(FORBIDDEN_STATEMENT_NODE_NAMES)
    for name, cls in zip(FORBIDDEN_STATEMENT_NODE_NAMES, FORBIDDEN_STATEMENT_NODES, strict=True):
        assert cls is getattr(exp, name)
        assert issubclass(cls, exp.Expression)


def test_missing_node_name_fails_loudly() -> None:
    with pytest.raises(ImportError, match="NoSuchNode"):
        resolve_node_classes(("Insert", "NoSuchNode"))
