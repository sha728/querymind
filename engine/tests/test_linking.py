from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from fakes import FakeEmbedder, FakeExecutor, ScriptedLLM, result
from qm_engine.config import EngineConfig
from qm_engine.linking import (
    SchemaLinker,
    estimate_tokens,
    fk_bridge,
    rank_tables,
    should_link,
    table_document,
)
from qm_engine.pipeline import Pipeline
from qm_engine.schema.models import Column, ForeignKey, SchemaSnapshot, Table
from qm_engine.schema.serialize import serialize_schema

NOW = datetime(2026, 10, 8, tzinfo=UTC)


def table(name: str, *fks: str) -> Table:
    cols = (
        Column("id", "integer", "integer", False, True, ("1",)),
        *(Column(f"{ref}_id", "integer", "integer", True, False) for ref in fks),
    )
    return Table(name, cols, tuple(ForeignKey((f"{ref}_id",), ref, ("id",)) for ref in fks))


# Chain a - b - c - d - e (each references the previous), plus an isolated table z.
CHAIN = SchemaSnapshot.build(
    "sqlite",
    [table("a"), table("b", "a"), table("c", "b"), table("d", "c"), table("e", "d"), table("z")],
    NOW,
)


def cfg(tmp_path: Path, **kwargs: object) -> EngineConfig:
    base: dict[str, object] = {
        "cerebras_api_key": "k",
        "cache_dir": tmp_path,
        "dialect": "sqlite",
        "few_shot_enabled": False,
    }
    return EngineConfig(_env_file=None, **(base | kwargs))  # type: ignore[arg-type]


def embedder_preferring(schema: SchemaSnapshot, question: str, order: list[str]) -> FakeEmbedder:
    """Fake vectors so that table similarity to the question follows `order` (best first)."""
    n = len(schema.tables)
    vectors: dict[str, list[float]] = {question: [1.0] + [0.0] * n}
    for rank, name in enumerate(order):
        t = schema.table(name)
        score = 1.0 - 0.1 * rank
        vectors[table_document(t)] = [
            score,
            *[0.0] * rank,
            (1 - score**2) ** 0.5,
            *[0.0] * (n - rank - 1),
        ]
    return FakeEmbedder(vectors)


# --- decide ---


def test_off_and_on_ignore_the_threshold() -> None:
    assert should_link("off", CHAIN, threshold=1)[0] is False
    assert should_link("on", CHAIN, threshold=10**9)[0] is True


def test_auto_switches_exactly_above_the_threshold() -> None:
    estimate = estimate_tokens(serialize_schema(CHAIN))
    assert should_link("auto", CHAIN, threshold=estimate) == (False, estimate)
    assert should_link("auto", CHAIN, threshold=estimate - 1) == (True, estimate)


async def test_off_returns_all_tables_without_embedding(tmp_path: Path) -> None:
    emb = FakeEmbedder()
    res = await SchemaLinker(cfg(tmp_path, linking_mode="off"), emb).link("q", CHAIN)
    assert res.applied is False and res.tables == ()
    assert emb.calls == []


# --- rank / top-k ---


def test_rank_tables_top_k_with_name_tiebreak() -> None:
    names = ["b", "a", "c"]
    docs = np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
    assert rank_tables(names, docs, np.array([1.0, 0.0]), top_k=2) == ["a", "b"]


async def test_on_returns_top_k_tables(tmp_path: Path) -> None:
    q = "which z?"
    emb = embedder_preferring(CHAIN, q, ["z", "e", "a", "b", "c", "d"])
    res = await SchemaLinker(cfg(tmp_path, linking_mode="on", linking_top_k=1), emb).link(q, CHAIN)
    assert res.applied is True and res.tables == ("z",)


async def test_fewer_tables_than_k_returns_all(tmp_path: Path) -> None:
    small = SchemaSnapshot.build("sqlite", [table("x"), table("y")], NOW)
    res = await SchemaLinker(
        cfg(tmp_path, linking_mode="on", linking_top_k=5), FakeEmbedder()
    ).link("q", small)
    assert res.tables == ("x", "y")


async def test_tables_keep_schema_order(tmp_path: Path) -> None:
    q = "q"
    emb = embedder_preferring(CHAIN, q, ["z", "a", "e", "b", "c", "d"])
    res = await SchemaLinker(cfg(tmp_path, linking_mode="on", linking_top_k=2), emb).link(q, CHAIN)
    assert res.tables == ("a", "z")


# --- FK bridge ---


def test_bridge_adds_intermediates_up_to_two_hops() -> None:
    # a .. d: path a-b-c-d has 2 intermediates -> b and c are added.
    assert fk_bridge(CHAIN, ["a", "d"]) == {"a", "b", "c", "d"}


def test_bridge_skips_paths_longer_than_two_intermediates() -> None:
    # a .. e: path a-b-c-d-e has 3 intermediates -> nothing added.
    assert fk_bridge(CHAIN, ["a", "e"]) == {"a", "e"}


def test_bridge_ignores_unconnected_tables() -> None:
    assert fk_bridge(CHAIN, ["a", "z"]) == {"a", "z"}


async def test_linker_applies_bridge(tmp_path: Path) -> None:
    q = "q"
    emb = embedder_preferring(CHAIN, q, ["a", "c", "z", "b", "d", "e"])
    res = await SchemaLinker(cfg(tmp_path, linking_mode="on", linking_top_k=2), emb).link(q, CHAIN)
    assert res.tables == ("a", "b", "c")  # a, c selected; b bridges them


# --- caching and errors ---


async def test_table_embeddings_cached_per_schema_hash(tmp_path: Path) -> None:
    emb = FakeEmbedder()
    linker = SchemaLinker(cfg(tmp_path, linking_mode="on"), emb)
    await linker.link("q1", CHAIN)
    await linker.link("q2", CHAIN)
    document_calls = [c for c in emb.calls if c[1] == "document"]
    assert len(document_calls) == 1
    assert (tmp_path / CHAIN.schema_hash / "fake-embed.npy").is_file()


async def test_linking_without_embedder_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="embedder"):
        await SchemaLinker(cfg(tmp_path, linking_mode="on"), None).link("q", CHAIN)


def test_table_document_format() -> None:
    t = Table(
        "singer",
        (
            Column("name", "text", "text", True, False, ("Joe", "Ann", "Bo")),
            Column("age", "int", "integer", True, False),
        ),
    )
    assert table_document(t) == (
        "table singer. columns: name (text), age (integer). example values: name: Joe, Ann"
    )


# --- pipeline integration ---


async def test_pipeline_prompt_contains_only_linked_tables(tmp_path: Path) -> None:
    q = "which z?"
    emb = embedder_preferring(CHAIN, q, ["z", "e", "a", "b", "c", "d"])
    llm = ScriptedLLM("```sql\nSELECT id FROM z\n```")
    p = Pipeline(
        cfg(tmp_path, linking_mode="on", linking_top_k=1, row_limit=None),
        llm,
        FakeExecutor(result()),
        embedder=emb,
    )
    res = await p.run(q, CHAIN)
    user = llm.calls[0][1]["content"]
    assert "CREATE TABLE z" in user and "CREATE TABLE a" not in user
    assert (res.linking.mode, res.linking.applied, res.linking.tables) == ("on", True, ("z",))
    assert res.timings.linking_ms >= 0


async def test_pipeline_full_schema_when_linking_off(tmp_path: Path) -> None:
    llm = ScriptedLLM("```sql\nSELECT 1\n```")
    p = Pipeline(cfg(tmp_path, linking_mode="off", row_limit=None), llm, FakeExecutor(result()))
    res = await p.run("q", CHAIN)
    assert llm.calls[0][1]["content"].count("CREATE TABLE") == 6
    assert res.linking.applied is False and res.linking.tables == ()
