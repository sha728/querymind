"""Few-shot example pools and similarity-based selection (design D4, §6.2 step 3, §6.4, R2.3).

Pools:
- eval: Spider ``train_spider.json`` (~7,000 questions). Dev is never a pool: the loader refuses
  any file that is not a Spider train file, so dev answers cannot leak into prompts.
- product: a hand-written YAML file (``engine/fewshot/northwind.yaml``, T32).

Examples are question -> SQL only, without their schema. Pool questions are embedded once as
documents and cached under the pool file's SHA-256 (design §6.4); each question is embedded as a
query and the top ``k`` pool examples by cosine similarity are chosen, ties broken by ID.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from qm_engine.llm.embeddings import Embedder, embed_documents_cached
from qm_engine.observability import get_logger
from qm_engine.prompts import Example

SPIDER_TRAIN_FILES = frozenset({"train_spider.json", "train_others.json"})

_log = get_logger()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class FewShotPool:
    examples: tuple[Example, ...]
    source: str  # file name, recorded in config.json
    sha256: str  # cache key for the pool embeddings (design §6.4)

    @property
    def cache_key(self) -> str:
        return f"fewshot-{self.sha256}"


def load_spider_train_pool(path: Path) -> FewShotPool:
    """The eval pool. Refuses dev (or any non-train) file by name."""
    if path.name not in SPIDER_TRAIN_FILES:
        raise ValueError(
            f"few-shot eval pool must be a Spider train file {sorted(SPIDER_TRAIN_FILES)}, "
            f"not {path.name!r} (dev must never be a pool)"
        )
    with path.open(encoding="utf-8") as f:
        items = json.load(f)
    examples = tuple(
        Example(question=it["question"], sql=it["query"], id=f"train-{i:05d}")
        for i, it in enumerate(items)
    )
    return FewShotPool(examples, path.name, file_sha256(path))


def load_yaml_pool(path: Path) -> FewShotPool:
    """The product pool: a list of ``{id, question, sql}`` mappings."""
    with path.open(encoding="utf-8") as f:
        items = yaml.safe_load(f)
    if not isinstance(items, list):
        raise ValueError(f"{path} must contain a list of examples")
    examples = []
    for it in items:
        missing = {"id", "question", "sql"} - set(it)
        if missing:
            raise ValueError(f"{path}: example {it.get('id', '?')} is missing {sorted(missing)}")
        examples.append(Example(question=str(it["question"]), sql=str(it["sql"]), id=str(it["id"])))
    ids = [e.id for e in examples]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path}: duplicate example ids")
    return FewShotPool(tuple(examples), path.name, file_sha256(path))


def top_k_examples(
    pool: FewShotPool, vectors: np.ndarray, query_vector: np.ndarray, k: int
) -> list[Example]:
    scores = vectors @ query_vector
    order = sorted(
        range(len(pool.examples)), key=lambda i: (-float(scores[i]), pool.examples[i].id)
    )
    return [pool.examples[i] for i in order[:k]]


class FewShotSelector:
    def __init__(self, pool: FewShotPool, embedder: Embedder, *, cache_dir: Path) -> None:
        self.pool = pool
        self.embedder = embedder
        self.cache_dir = cache_dir
        self._vectors: np.ndarray | None = None

    async def warm_up(self) -> None:
        """Embed (or load) the pool now, so per-question timings exclude this one-off cost."""
        await self._pool_vectors()

    async def _pool_vectors(self) -> np.ndarray:
        if self._vectors is None:
            self._vectors = await embed_documents_cached(
                self.embedder,
                [e.question for e in self.pool.examples],
                cache_dir=self.cache_dir,
                key=self.pool.cache_key,
            )
        return self._vectors

    async def select(self, question: str, k: int) -> Sequence[Example]:
        vectors = await self._pool_vectors()
        query_vector = (await self.embedder.embed([question], "query"))[0]
        chosen = top_k_examples(self.pool, vectors, query_vector, k)
        _log.info("fewshot_done", pool=self.pool.source, ids=[e.id for e in chosen])
        return chosen
