from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from fakes import FakeEmbedder, FakeExecutor, ScriptedLLM, result
from qm_engine.config import EngineConfig
from qm_engine.fewshot import (
    FewShotPool,
    FewShotSelector,
    load_spider_train_pool,
    load_yaml_pool,
    top_k_examples,
)
from qm_engine.pipeline import Pipeline
from qm_engine.prompts import Example
from qm_engine.schema.models import Column, SchemaSnapshot, Table

SPIDER_FIXTURE = Path(__file__).parent / "eval" / "fixtures" / "spider_mini"
YAML = """\
- id: nw-01
  question: How many customers are there?
  sql: SELECT count(*) FROM customers
- id: nw-02
  question: List the five most expensive products.
  sql: SELECT product_name FROM products ORDER BY unit_price DESC LIMIT 5
"""


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# --- loaders ---


def test_spider_train_loader_produces_id_question_sql() -> None:
    pool = load_spider_train_pool(SPIDER_FIXTURE / "train_spider.json")
    assert pool.examples == (
        Example(
            question="How many orders are there?",
            sql="SELECT count(*) FROM orders",
            id="train-00000",
        ),
        Example(
            question="List all customer ids.",
            sql="SELECT customer_id FROM customers",
            id="train-00001",
        ),
    )
    assert pool.source == "train_spider.json" and len(pool.sha256) == 64


def test_eval_pool_never_loads_dev() -> None:
    with pytest.raises(ValueError, match="dev must never be a pool"):
        load_spider_train_pool(SPIDER_FIXTURE / "dev.json")


def test_eval_pool_refuses_any_non_train_file(tmp_path: Path) -> None:
    renamed = write(tmp_path, "my_examples.json", "[]")
    with pytest.raises(ValueError, match="Spider train file"):
        load_spider_train_pool(renamed)


def test_yaml_loader_produces_id_question_sql(tmp_path: Path) -> None:
    pool = load_yaml_pool(write(tmp_path, "northwind.yaml", YAML))
    assert [(e.id, e.question, e.sql) for e in pool.examples] == [
        ("nw-01", "How many customers are there?", "SELECT count(*) FROM customers"),
        (
            "nw-02",
            "List the five most expensive products.",
            "SELECT product_name FROM products ORDER BY unit_price DESC LIMIT 5",
        ),
    ]


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("- id: a\n  question: q\n", "missing"),
        ("- {id: a, question: q, sql: s}\n- {id: a, question: r, sql: t}\n", "duplicate"),
        ("id: a\n", "list"),
    ],
)
def test_yaml_loader_rejects_bad_files(tmp_path: Path, text: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        load_yaml_pool(write(tmp_path, "bad.yaml", text))


def test_pool_cache_key_changes_when_file_changes(tmp_path: Path) -> None:
    path = write(tmp_path, "pool.yaml", YAML)
    before = load_yaml_pool(path).cache_key
    path.write_text(YAML + "- {id: nw-03, question: q, sql: SELECT 1}\n", encoding="utf-8")
    after = load_yaml_pool(path).cache_key
    assert before != after and before.startswith("fewshot-")


# --- selection ---


def pool_of(*ids: str) -> FewShotPool:
    return FewShotPool(tuple(Example(f"q {i}", f"SELECT {i}", i) for i in ids), "t", "0" * 64)


def test_top_k_by_similarity_with_id_tiebreak() -> None:
    pool = pool_of("b", "a", "c")
    vectors = np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])  # b and a tie
    chosen = top_k_examples(pool, vectors, np.array([1.0, 0.0]), k=2)
    assert [e.id for e in chosen] == ["a", "b"]


async def test_selector_is_deterministic_and_caches_pool(tmp_path: Path) -> None:
    pool = pool_of(*[f"ex-{i:02d}" for i in range(10)])
    emb = FakeEmbedder()
    selector = FewShotSelector(pool, emb, cache_dir=tmp_path)
    first = await selector.select("How many?", 3)
    second = await selector.select("How many?", 3)
    assert [e.id for e in first] == [e.id for e in second] and len(first) == 3
    assert sum(1 for _, kind in emb.calls if kind == "document") == 1  # pool embedded once
    assert (tmp_path / pool.cache_key / "fake-embed.npy").is_file()


async def test_selector_picks_most_similar(tmp_path: Path) -> None:
    pool = pool_of("x", "y", "z")
    emb = FakeEmbedder({"q x": [0.0, 1.0], "q y": [1.0, 0.0], "q z": [0.7, 0.7], "Q": [1.0, 0.1]})
    chosen = await FewShotSelector(pool, emb, cache_dir=tmp_path).select("Q", 2)
    assert [e.id for e in chosen] == ["y", "z"]


# --- pipeline integration ---

SCHEMA = SchemaSnapshot.build(
    "sqlite",
    [Table("orders", (Column("order_id", "integer", "integer", False, True, ("1",)),))],
    datetime(2026, 10, 8, tzinfo=UTC),
)


def cfg(tmp_path: Path, **kwargs: object) -> EngineConfig:
    base: dict[str, object] = {
        "cerebras_api_key": "k",
        "cache_dir": tmp_path,
        "dialect": "sqlite",
        "row_limit": None,
        "linking_mode": "off",
        "self_correction_enabled": False,
        "include_date": False,
        "unanswerable_enabled": False,
        "summary_enabled": False,
    }
    return EngineConfig(_env_file=None, **(base | kwargs))  # type: ignore[arg-type]


async def test_pipeline_puts_selected_examples_in_prompt(
    tmp_path: Path, snapshot: Callable[[str, str], None]
) -> None:
    pool = load_spider_train_pool(SPIDER_FIXTURE / "train_spider.json")
    q = "How many orders were placed?"
    emb = FakeEmbedder(
        {
            "How many orders are there?": [1.0, 0.0],
            "List all customer ids.": [0.0, 1.0],
            q: [1.0, 0.0],
        }
    )
    llm = ScriptedLLM("```sql\nSELECT count(*) FROM orders\n```")
    p = Pipeline(
        cfg(tmp_path, few_shot_enabled=True, few_shot_k=1),
        llm,
        FakeExecutor(result()),
        fewshot=FewShotSelector(pool, emb, cache_dir=tmp_path),
    )
    res = await p.run(q, SCHEMA)
    assert res.few_shot_ids == ("train-00000",)
    assert res.timings.fewshot_ms >= 0
    snapshot("prompts/pipeline-fewshot-user.txt", llm.calls[0][1]["content"] + "\n")


async def test_pipeline_without_pool_but_enabled_is_an_error(tmp_path: Path) -> None:
    p = Pipeline(cfg(tmp_path, few_shot_enabled=True), ScriptedLLM(), FakeExecutor())
    with pytest.raises(ValueError, match="no example pool"):
        await p.run("q", SCHEMA)


async def test_pipeline_few_shot_off_has_no_examples(tmp_path: Path) -> None:
    llm = ScriptedLLM("```sql\nSELECT 1\n```")
    p = Pipeline(cfg(tmp_path, few_shot_enabled=False), llm, FakeExecutor(result()))
    res = await p.run("q", SCHEMA)
    assert res.few_shot_ids == ()
    assert "### Examples" not in llm.calls[0][1]["content"]


async def test_warm_up_embeds_pool_before_first_question(tmp_path: Path) -> None:
    pool = pool_of("a", "b")
    emb = FakeEmbedder()
    selector = FewShotSelector(pool, emb, cache_dir=tmp_path)
    await selector.warm_up()
    assert [kind for _, kind in emb.calls] == ["document"]
    await selector.select("q", 1)
    assert [kind for _, kind in emb.calls] == ["document", "query"]  # pool not embedded again
