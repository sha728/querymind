"""Embeddings for schema linking and few-shot selection (design D3, D4, §6.1, §6.3, §5.3).

The default embedder calls Ollama's OpenAI-compatible ``/embeddings`` endpoint with
``nomic-embed-text``. nomic models need task prefixes, ``search_query:`` for the question and
``search_document:`` for what is searched. Vectors come back L2-normalised, so cosine similarity
is a dot product.

``embed_documents_cached`` stores document embeddings at
``{cache_dir}/{key}/{model}.npy``. The key is the schema hash (design §5.3) or the few-shot pool
hash (design §6.4), so the same documents are embedded only once.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Literal, Protocol

import httpx
import numpy as np

from qm_engine.config import EngineConfig
from qm_engine.observability import Timer, get_logger

Kind = Literal["query", "document"]

_log = get_logger()


class EmbeddingError(Exception):
    """The embedding endpoint failed or returned an unexpected response."""


class Embedder(Protocol):
    model: str

    async def embed(self, texts: Sequence[str], kind: Kind) -> np.ndarray:
        """Return an (n, dim) float32 array of L2-normalised vectors."""
        ...


def _normalise(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (vectors / norms).astype(np.float32)


def task_prefix(model: str, kind: Kind) -> str:
    """nomic-embed models are trained with these prefixes; others take raw text."""
    if "nomic-embed" in model:
        return "search_query: " if kind == "query" else "search_document: "
    return ""


def cache_path(cache_dir: Path, key: str, model: str) -> Path:
    safe_model = model.replace("/", "_").replace(":", "_")
    return cache_dir / key / f"{safe_model}.npy"


class OllamaEmbedder:
    def __init__(
        self, cfg: EngineConfig, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.model = cfg.embed_model
        self.batch_size = cfg.embed_batch_size
        self._http = httpx.AsyncClient(
            base_url=cfg.embed_base_url.rstrip("/"), timeout=cfg.llm_timeout_s, transport=transport
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def embed(self, texts: Sequence[str], kind: Kind) -> np.ndarray:
        prefix = task_prefix(self.model, kind)
        rows: list[list[float]] = []
        with Timer() as t:
            for start in range(0, len(texts), self.batch_size):
                batch = [prefix + text for text in texts[start : start + self.batch_size]]
                rows.extend(await self._request(batch))
        _log.info("embed", model=self.model, kind=kind, n=len(texts), latency_ms=t.elapsed_ms)
        if not rows:
            return np.zeros((0, 0), dtype=np.float32)
        return _normalise(np.asarray(rows, dtype=np.float32))

    async def _request(self, batch: list[str]) -> list[list[float]]:
        try:
            response = await self._http.post(
                "/embeddings", json={"model": self.model, "input": batch}
            )
        except httpx.HTTPError as e:
            raise EmbeddingError(f"embedding endpoint unreachable: {e}") from e
        if response.status_code != 200:
            raise EmbeddingError(
                f"embedding request failed ({response.status_code}): {response.text[:300]}"
            )
        try:
            data = sorted(response.json()["data"], key=lambda d: d["index"])
            vectors = [d["embedding"] for d in data]
        except (ValueError, KeyError, TypeError) as e:
            raise EmbeddingError("embedding response had an unexpected shape") from e
        if len(vectors) != len(batch):
            raise EmbeddingError(f"asked for {len(batch)} embeddings, got {len(vectors)}")
        return vectors


async def embed_documents_cached(
    embedder: Embedder, texts: Sequence[str], *, cache_dir: Path, key: str
) -> np.ndarray:
    """Document embeddings, read from ``{cache_dir}/{key}/{model}.npy`` when present.

    A cached array with a different number of rows is treated as a miss and replaced.
    """
    path = cache_path(cache_dir, key, embedder.model)
    if path.is_file():
        cached = np.load(path)
        if cached.shape[0] == len(texts):
            _log.info("embed_cache_hit", key=key, model=embedder.model, n=len(texts))
            return cached
        _log.warning("embed_cache_stale", key=key, cached=cached.shape[0], expected=len(texts))
    vectors = await embedder.embed(texts, "document")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.npy")
    np.save(tmp, vectors)
    tmp.replace(path)  # atomic: a crash never leaves a half-written cache file
    return vectors
