"""LLM reply -> SQL or CANNOT_ANSWER (design §6.2 step 6, R2.4)."""

import re
from dataclasses import dataclass
from typing import Literal

CANNOT_ANSWER_PREFIX = "CANNOT_ANSWER:"

# First fenced block. A language tag counts only when a line break follows it, so a one-line
# fence (```SELECT 1```) keeps its SQL. An unterminated fence (a reply cut off by max_tokens)
# still yields its content.
_FENCE = re.compile(r"```(?:[ \t]*[A-Za-z0-9_+-]*[ \t]*\n)?(.*?)(?:```|\Z)", re.DOTALL)


@dataclass(frozen=True)
class Extraction:
    kind: Literal["sql", "cannot_answer", "empty"]
    sql: str = ""
    reason: str = ""


def _cannot_answer(text: str) -> str | None:
    if text.upper().startswith(CANNOT_ANSWER_PREFIX):
        return text[len(CANNOT_ANSWER_PREFIX) :].strip()
    return None


def extract(reply: str, *, unanswerable_enabled: bool) -> Extraction:
    text = reply.strip()

    if unanswerable_enabled and (reason := _cannot_answer(text)) is not None:
        return Extraction("cannot_answer", reason=reason)

    match = _FENCE.search(text)
    sql = (match.group(1) if match else text).strip()

    # The sentinel can also arrive inside a fence.
    if unanswerable_enabled and (reason := _cannot_answer(sql)) is not None:
        return Extraction("cannot_answer", reason=reason)

    if not sql:
        return Extraction("empty")
    return Extraction("sql", sql=sql)
