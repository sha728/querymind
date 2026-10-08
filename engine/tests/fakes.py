"""Test doubles shared by pipeline and harness tests (design §13.1). No real LLM or DB."""

from collections.abc import Sequence

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
