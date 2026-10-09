"""The curated Northwind few-shot pool (design D4, §6.4, §13.1, R2.3).

Every example is shown to the model as a correct answer, so each one must pass the safety
validator and execute against target-db through the read-only executor.
"""

import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from qm_engine.config import EngineConfig
from qm_engine.execution.postgres import PostgresExecutor
from qm_engine.fewshot import load_yaml_pool
from qm_engine.prompts import Example
from qm_engine.safety.validator import validate

POOL_PATH = Path(__file__).parents[2] / "fewshot" / "northwind.yaml"
POOL = load_yaml_pool(POOL_PATH)
TIMEOUT_MS = 10_000

# Required coverage (tasks.md T32): pattern -> minimum number of examples using it.
COVERAGE = {
    "join": (r"\bJOIN\b", 5),
    "aggregation": (r"\b(count|sum|avg)\s*\(", 5),
    "top-N": (r"\bLIMIT\s+\d+", 3),
    "date range": (r"order_date\s*(>=|<|>)|\bCURRENT_DATE\b|date_trunc", 3),
    "window function": (r"\bOVER\s*\(", 3),
}


def test_pool_size_and_ids() -> None:
    assert 20 <= len(POOL.examples) <= 30
    assert all(e.id.startswith("nw-") for e in POOL.examples)
    assert len({e.question.lower() for e in POOL.examples}) == len(POOL.examples)


@pytest.mark.parametrize("kind", list(COVERAGE))
def test_pool_covers_required_kinds(kind: str) -> None:
    pattern, minimum = COVERAGE[kind]
    hits = [e.id for e in POOL.examples if re.search(pattern, e.sql, re.IGNORECASE)]
    assert len(hits) >= minimum, f"{kind}: only {hits}"


@pytest.mark.parametrize("example", POOL.examples, ids=[e.id for e in POOL.examples])
def test_example_passes_validator(example: Example) -> None:
    verdict = validate(example.sql, "postgres")
    assert verdict.rejection is None, verdict.rejection


@pytest.fixture(scope="module")
async def ex(target_cfg: EngineConfig) -> AsyncIterator[PostgresExecutor]:
    executor = PostgresExecutor(target_cfg, max_size=2)
    await executor.open()
    try:
        yield executor
    finally:
        await executor.close()


@pytest.mark.parametrize("example", POOL.examples, ids=[e.id for e in POOL.examples])
async def test_example_executes(ex: PostgresExecutor, example: Example) -> None:
    res = await ex.execute(example.sql, 1000, TIMEOUT_MS)
    assert res.columns  # ran and described its result
