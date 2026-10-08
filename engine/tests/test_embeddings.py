import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from fakes import FakeEmbedder
from qm_engine.config import EngineConfig
from qm_engine.llm.embeddings import (
    EmbeddingError,
    OllamaEmbedder,
    cache_path,
    embed_documents_cached,
    task_prefix,
)


class FakeOllama:
    """Fake /v1/embeddings: vector i = [len(text), i, 1]; records request bodies."""

    def __init__(self, status: int = 200) -> None:
        self.bodies: list[dict] = []
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        if self.status != 200:
            return httpx.Response(self.status, text="model not found")
        data = [
            {"index": i, "embedding": [float(len(t)), float(i), 1.0]}
            for i, t in enumerate(body["input"])
        ]
        return httpx.Response(200, json={"data": list(reversed(data)), "model": body["model"]})


def make(fake: FakeOllama, **cfg: object) -> OllamaEmbedder:
    config = EngineConfig(
        _env_file=None,  # type: ignore[call-arg]
        cerebras_api_key="k",
        embed_base_url="http://localhost:11434/v1",
        **cfg,  # type: ignore[arg-type]
    )
    return OllamaEmbedder(config, transport=httpx.MockTransport(fake))


async def test_query_and_document_prefixes_for_nomic() -> None:
    fake = FakeOllama()
    e = make(fake)
    await e.embed(["How many singers?"], "query")
    await e.embed(["table singer", "table concert"], "document")
    assert fake.bodies[0] == {
        "model": "nomic-embed-text",
        "input": ["search_query: How many singers?"],
    }
    assert fake.bodies[1]["input"] == [
        "search_document: table singer",
        "search_document: table concert",
    ]


def test_no_prefix_for_other_models() -> None:
    assert task_prefix("mxbai-embed-large", "query") == ""
    assert task_prefix("nomic-embed-text:v1.5", "document") == "search_document: "


async def test_vectors_are_normalised_and_in_input_order() -> None:
    vecs = await make(FakeOllama()).embed(["ab", "abcd"], "document")
    assert vecs.shape == (2, 3) and vecs.dtype == np.float32
    assert np.allclose(np.linalg.norm(vecs, axis=1), 1.0)
    # The fake returns data in reverse order; the embedder must restore it by index.
    assert vecs[0][1] == 0.0 and vecs[1][1] > 0.0


async def test_batches_are_split_by_batch_size() -> None:
    fake = FakeOllama()
    vecs = await make(fake, embed_batch_size=2).embed(["a", "b", "c", "d", "e"], "document")
    assert [len(b["input"]) for b in fake.bodies] == [2, 2, 1]
    assert vecs.shape[0] == 5


async def test_http_error_raises_embedding_error() -> None:
    with pytest.raises(EmbeddingError, match="404"):
        await make(FakeOllama(status=404)).embed(["x"], "query")


# --- disk cache ---


async def test_cache_written_at_key_model_path_and_reused(tmp_path: Path) -> None:
    fake = FakeOllama()
    e = make(fake)
    docs = ["table singer", "table concert"]

    first = await embed_documents_cached(e, docs, cache_dir=tmp_path, key="schema-abc")
    path = tmp_path / "schema-abc" / "nomic-embed-text.npy"
    assert path.is_file()
    assert cache_path(tmp_path, "schema-abc", "nomic-embed-text") == path
    assert len(fake.bodies) == 1

    second = await embed_documents_cached(e, docs, cache_dir=tmp_path, key="schema-abc")
    assert len(fake.bodies) == 1  # no second HTTP call
    assert np.array_equal(first, second)


async def test_different_key_misses_cache(tmp_path: Path) -> None:
    fake = FakeOllama()
    e = make(fake)
    await embed_documents_cached(e, ["t1"], cache_dir=tmp_path, key="schema-abc")
    await embed_documents_cached(e, ["t1"], cache_dir=tmp_path, key="schema-def")
    assert len(fake.bodies) == 2
    assert (tmp_path / "schema-def" / "nomic-embed-text.npy").is_file()


async def test_wrong_row_count_is_treated_as_a_miss(tmp_path: Path) -> None:
    fake = FakeOllama()
    e = make(fake)
    await embed_documents_cached(e, ["t1", "t2"], cache_dir=tmp_path, key="k")
    vecs = await embed_documents_cached(e, ["t1", "t2", "t3"], cache_dir=tmp_path, key="k")
    assert vecs.shape[0] == 3 and len(fake.bodies) == 2


def test_model_names_are_safe_file_names(tmp_path: Path) -> None:
    path = cache_path(tmp_path, "k", "hf.co/org/model:latest")
    assert path.name == "hf.co_org_model_latest.npy"


# --- FakeEmbedder (used by linking and few-shot tests) ---


async def test_fake_embedder_is_deterministic_and_normalised() -> None:
    fake = FakeEmbedder()
    a = await fake.embed(["same text"], "query")
    b = await fake.embed(["same text"], "document")
    assert np.array_equal(a, b)
    assert np.isclose(np.linalg.norm(a[0]), 1.0)
    chosen = FakeEmbedder({"q": [1.0, 0.0], "t": [0.0, 2.0]})
    out = await chosen.embed(["q", "t"], "document")
    assert np.allclose(out, [[1.0, 0.0], [0.0, 1.0]])
