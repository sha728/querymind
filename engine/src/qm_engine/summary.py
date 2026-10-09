"""Result summary: the second LLM call after a successful query (design §6.2 step 11, §8.3, R5.3).

At most ``MAX_SUMMARY_ROWS`` rows are sent. A failed or empty summary call never fails the
answer: the caller gets ``None`` and the request stays ``success``. Off in eval runs.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass

from qm_engine.execution.base import ExecResult
from qm_engine.llm.client import LLMClient, LLMError, Message
from qm_engine.observability import Timer, get_logger

MAX_SUMMARY_ROWS = 20

SUMMARY_SYSTEM = (
    "You summarise SQL query results for a non-technical reader. Use only the data provided. "
    "Do not speculate beyond it. Reply with one or two plain-English sentences. No markdown."
)

SUMMARY_USER = """\
Question: {question}
Columns: {columns}
Rows ({shown} of {row_count}{truncated_note}):
{rows}"""

TRUNCATED_NOTE = "; the result was cut off at the row limit, so more rows exist"

_log = get_logger()


@dataclass(frozen=True)
class SummaryOutcome:
    text: str | None  # None when the call failed or returned nothing
    latency_ms: int
    pacing_ms: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


def _row_json(row: Sequence[object]) -> str:
    return json.dumps(list(row), ensure_ascii=False, separators=(",", ":"), default=str)


def build_summary_messages(question: str, result: ExecResult) -> list[Message]:
    """The §8.3 prompt with the first ``MAX_SUMMARY_ROWS`` rows as compact JSON lines."""
    shown = result.rows[:MAX_SUMMARY_ROWS]
    user = SUMMARY_USER.format(
        question=question,
        columns=", ".join(c.name for c in result.columns),
        shown=len(shown),
        row_count=result.row_count,
        truncated_note=TRUNCATED_NOTE if result.truncated else "",
        rows="\n".join(_row_json(r) for r in shown),
    )
    return [{"role": "system", "content": SUMMARY_SYSTEM}, {"role": "user", "content": user}]


async def summarise(llm: LLMClient, question: str, result: ExecResult) -> SummaryOutcome:
    with Timer() as t:
        try:
            chat = await llm.chat(build_summary_messages(question, result), purpose="summary")
        except LLMError as e:
            _log.warning("summary_failed", code=e.code)
            chat = None
    if chat is None:
        return SummaryOutcome(text=None, latency_ms=t.elapsed_ms)
    text = chat.text.strip() or None
    if text is None:
        _log.warning("summary_empty")
    return SummaryOutcome(
        text=text,
        latency_ms=chat.latency_ms,
        pacing_ms=chat.pacing_ms,
        prompt_tokens=chat.prompt_tokens,
        completion_tokens=chat.completion_tokens,
    )
