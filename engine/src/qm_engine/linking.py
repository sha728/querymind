"""Schema linking: put only the tables relevant to the question in the prompt (design §6.3, R2.2).

1. Decide: ``on`` links, ``off`` keeps the full schema, ``auto`` links only when the serialized
   schema's token estimate (ceil(chars / 4)) is above ``linking_token_threshold``.
2. Embed one document per table (name, typed columns, sample values), cached per schema hash.
3. Embed the question and rank tables by cosine similarity; keep the top ``linking_top_k``
   (ties broken by table name).
4. FK bridge: for every pair of selected tables, add the tables on the shortest FK path between
   them when that path has at most 2 intermediate tables, so join paths stay intact.
5. Columns are not pruned (design §15.3).
"""

import math
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations

import numpy as np

from qm_engine.config import EngineConfig
from qm_engine.llm.embeddings import Embedder, embed_documents_cached
from qm_engine.observability import get_logger
from qm_engine.schema.models import SchemaSnapshot, Table
from qm_engine.schema.serialize import serialize_schema

MAX_BRIDGE_INTERMEDIATES = 2

_log = get_logger()


@dataclass(frozen=True)
class LinkingResult:
    mode: str
    applied: bool
    tables: tuple[str, ...]  # linked tables in schema order; empty when not applied
    estimated_tokens: int


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def table_document(table: Table) -> str:
    """The text embedded for a table (design §6.3 step 2; the embedder adds its prefix)."""
    cols = ", ".join(f"{c.name} ({c.norm_type})" for c in table.columns)
    samples = "; ".join(f"{c.name}: {', '.join(c.samples[:2])}" for c in table.columns if c.samples)
    doc = f"table {table.name}. columns: {cols}."
    return f"{doc} example values: {samples}" if samples else doc


def should_link(mode: str, schema: SchemaSnapshot, threshold: int) -> tuple[bool, int]:
    estimate = estimate_tokens(serialize_schema(schema))
    if mode == "on":
        return True, estimate
    if mode == "off":
        return False, estimate
    return estimate > threshold, estimate


def _fk_graph(schema: SchemaSnapshot) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {t.name: set() for t in schema.tables}
    for t in schema.tables:
        for fk in t.foreign_keys:
            if fk.ref_table in graph and fk.ref_table != t.name:
                graph[t.name].add(fk.ref_table)
                graph[fk.ref_table].add(t.name)
    return graph


def _shortest_path(graph: dict[str, set[str]], start: str, goal: str) -> list[str] | None:
    """BFS with neighbours visited in sorted order, so ties resolve deterministically."""
    previous: dict[str, str | None] = {start: None}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        if node == goal:
            path = [node]
            while (prev := previous[path[-1]]) is not None:
                path.append(prev)
            return path[::-1]
        for nxt in sorted(graph[node]):
            if nxt not in previous:
                previous[nxt] = node
                queue.append(nxt)
    return None


def fk_bridge(schema: SchemaSnapshot, selected: Sequence[str]) -> set[str]:
    """Selected tables plus the intermediates on short FK paths between each selected pair."""
    graph = _fk_graph(schema)
    result = set(selected)
    for a, b in combinations(sorted(selected), 2):
        path = _shortest_path(graph, a, b)
        if path is not None and len(path) - 2 <= MAX_BRIDGE_INTERMEDIATES:
            result.update(path[1:-1])
    return result


def rank_tables(
    names: Sequence[str], doc_vectors: np.ndarray, query_vector: np.ndarray, top_k: int
) -> list[str]:
    scores = doc_vectors @ query_vector
    order = sorted(range(len(names)), key=lambda i: (-float(scores[i]), names[i]))
    return [names[i] for i in order[:top_k]]


class SchemaLinker:
    def __init__(self, cfg: EngineConfig, embedder: Embedder | None) -> None:
        self.cfg = cfg
        self.embedder = embedder

    async def link(self, question: str, schema: SchemaSnapshot) -> LinkingResult:
        cfg = self.cfg
        apply, estimate = should_link(cfg.linking_mode, schema, cfg.linking_token_threshold)
        if not apply:
            return LinkingResult(cfg.linking_mode, False, (), estimate)
        if self.embedder is None:
            raise ValueError("schema linking needs an embedder (QM_EMBED_* settings)")

        names = [t.name for t in schema.tables]
        docs = [table_document(t) for t in schema.tables]
        doc_vectors = await embed_documents_cached(
            self.embedder, docs, cache_dir=cfg.cache_dir, key=schema.schema_hash
        )
        query_vector = (await self.embedder.embed([question], "query"))[0]
        top = rank_tables(names, doc_vectors, query_vector, cfg.linking_top_k)
        linked = fk_bridge(schema, top)
        tables = tuple(n for n in names if n in linked)  # keep schema order
        _log.info(
            "linking_done",
            mode=cfg.linking_mode,
            estimated_tokens=estimate,
            top_k=top,
            linked=list(tables),
        )
        return LinkingResult(cfg.linking_mode, True, tables, estimate)
