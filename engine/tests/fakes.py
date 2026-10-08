"""Test doubles shared by pipeline and harness tests (design §13.1). No real LLM or DB."""

import hashlib
from collections.abc import Mapping, Sequence

import numpy as np

from qm_engine.execution.base import ExecResult, ExecutionError, ResultColumn
from qm_engine.llm.client import ChatResult, LLMError, Message


class ScriptedLLM:
    """Returns queued replies in order and records every conversation it was sent."""

    def __init__(
        self,
        *replies: str | LLMError,
        model: str = "scripted-model",
        prompt_tokens: int | None = 100,
        completion_tokens: int | None = 20,
        latency_ms: int = 50,
    ) -> None:
        self.replies = list(replies)
        self.calls: list[list[Message]] = []
        self.model = model
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.latency_ms = latency_ms

    async def chat(
        self, messages: list[Message], *, max_tokens: int | None = None, purpose: str = ""
    ) -> ChatResult:
        self.calls.append([dict(m) for m in messages])  # type: ignore[misc]
        if not self.replies:
            raise AssertionError("ScriptedLLM has no reply left")
        reply = self.replies.pop(0)
        if isinstance(reply, LLMError):
            raise reply
        return ChatResult(
            text=reply,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            latency_ms=self.latency_ms,
            model=self.model,
        )


def result(
    columns: Sequence[tuple[str, str]] = (("n", "integer"),),
    rows: Sequence[tuple[object, ...]] = ((1,),),
    truncated: bool = False,
) -> ExecResult:
    return ExecResult(
        columns=tuple(ResultColumn(name, norm, norm.upper()) for name, norm in columns),
        rows=list(rows),
        truncated=truncated,
    )


class FakeExecutor:
    """Returns queued results (or raises queued errors) and records every call."""

    def __init__(self, *outcomes: ExecResult | ExecutionError) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, int | None, int]] = []

    async def execute(self, sql: str, row_cap: int | None, timeout_ms: int) -> ExecResult:
        self.calls.append((sql, row_cap, timeout_ms))
        if not self.outcomes:
            raise AssertionError("FakeExecutor has no outcome left")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, ExecutionError):
            raise outcome
        return outcome


class FakeEmbedder:
    """Deterministic embeddings without a model.

    ``vectors`` maps exact texts to chosen vectors (for ranking tests); any other text gets a
    stable pseudo-random unit vector derived from its SHA-256. Records every call.
    """

    def __init__(
        self, vectors: Mapping[str, Sequence[float]] | None = None, *, dim: int = 8
    ) -> None:
        self.model = "fake-embed"
        self.vectors = dict(vectors or {})
        self.dim = len(next(iter(self.vectors.values()))) if self.vectors else dim
        self.calls: list[tuple[list[str], str]] = []

    def _vector(self, text: str) -> np.ndarray:
        if text in self.vectors:
            v = np.asarray(self.vectors[text], dtype=np.float32)
        else:
            seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")
            v = np.random.default_rng(seed).standard_normal(self.dim).astype(np.float32)
        n = np.linalg.norm(v)
        return v / n if n else v

    async def embed(self, texts: Sequence[str], kind: str) -> np.ndarray:
        self.calls.append((list(texts), kind))
        return np.stack([self._vector(t) for t in texts]) if texts else np.zeros((0, self.dim))
