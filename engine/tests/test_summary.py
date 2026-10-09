from collections.abc import Callable

from fakes import FakeExecutor, ScriptedLLM, result
from qm_engine.llm.client import LLMError
from qm_engine.summary import MAX_SUMMARY_ROWS, SUMMARY_SYSTEM, build_summary_messages, summarise
from test_pipeline import GOOD, QUESTION, SQLITE_SCHEMA, pipeline

COLUMNS = [("customer", "text"), ("total", "numeric"), ("first_order", "date")]
SUMMARY = "Ernst Handel spent the most, followed by Save-a-lot Markets."


def render(messages: list) -> str:
    return "\n\n".join(f"=== {m['role']} ===\n{m['content']}" for m in messages) + "\n"


def many_rows(n: int) -> list[tuple[object, ...]]:
    return [(f"Customer {i}", round(1000.5 - i, 2), f"2026-0{1 + i % 9}-15") for i in range(n)]


# --- prompt (design §8.3) ---


def test_summary_prompt_snapshot(snapshot: Callable[[str, str], None]) -> None:
    res = result(
        COLUMNS,
        [("Ernst Handel", 12345.67, "2026-08-01"), ("Save-a-lot Markets", 9876.5, None)],
    )
    snapshot("prompts/summary.txt", render(build_summary_messages("Who spent most?", res)))


def test_summary_prompt_truncated_snapshot(snapshot: Callable[[str, str], None]) -> None:
    res = result(COLUMNS, many_rows(25), truncated=True)
    snapshot("prompts/summary-truncated.txt", render(build_summary_messages("Top?", res)))


def test_prompt_sends_at_most_20_rows_and_notes_truncation() -> None:
    _, user = build_summary_messages("Top?", result(COLUMNS, many_rows(25), truncated=True))
    lines = user["content"].splitlines()
    assert lines[2].startswith(f"Rows ({MAX_SUMMARY_ROWS} of 25; the result was cut off")
    assert len(lines[3:]) == MAX_SUMMARY_ROWS
    assert lines[-1].startswith('["Customer 19"')


def test_prompt_without_truncation_has_no_note() -> None:
    _, user = build_summary_messages("Top?", result(COLUMNS, many_rows(3)))
    assert "Rows (3 of 3):" in user["content"]
    assert "cut off" not in user["content"]


def test_rows_are_compact_json_and_keep_unicode() -> None:
    _, user = build_summary_messages("?", result([("city", "text")], [("München",)]))
    assert user["content"].endswith('["München"]')


# --- summarise() ---


async def test_summarise_strips_text_and_reports_usage() -> None:
    llm = ScriptedLLM(f"  {SUMMARY}\n", prompt_tokens=80, completion_tokens=15, latency_ms=300)
    out = await summarise(llm, QUESTION, result())
    assert (out.text, out.latency_ms, out.prompt_tokens, out.completion_tokens) == (
        SUMMARY,
        300,
        80,
        15,
    )


async def test_summarise_failure_returns_none() -> None:
    out = await summarise(ScriptedLLM(LLMError("LLM_UNAVAILABLE", "down")), QUESTION, result())
    assert out.text is None and out.prompt_tokens is None


async def test_summarise_empty_reply_returns_none() -> None:
    out = await summarise(ScriptedLLM("   "), QUESTION, result())
    assert out.text is None


# --- pipeline step 11 ---


async def test_pipeline_adds_summary_tokens_and_timing() -> None:
    llm = ScriptedLLM(GOOD, SUMMARY, prompt_tokens=100, completion_tokens=20, latency_ms=50)
    ex = FakeExecutor(result([("count", "integer")], [(3,)]))
    res = await pipeline(llm, ex, summary_enabled=True).run(QUESTION, SQLITE_SCHEMA)

    assert res.status == "success" and res.summary == SUMMARY
    assert res.usage is not None
    assert (res.usage.prompt_tokens, res.usage.completion_tokens) == (200, 40)
    assert res.timings.summary_ms == 50 and res.timings.generation_ms == 50
    assert len(res.attempts) == 1  # the summary call is not an attempt
    summary_call = llm.calls[1]
    assert summary_call[0]["content"] == SUMMARY_SYSTEM
    assert f"Question: {QUESTION}" in summary_call[1]["content"]


async def test_pipeline_summary_failure_keeps_success() -> None:
    llm = ScriptedLLM(GOOD, LLMError("LLM_RATE_LIMITED", "quota", retry_after_s=60))
    ex = FakeExecutor(result([("count", "integer")], [(3,)]))
    res = await pipeline(llm, ex, summary_enabled=True).run(QUESTION, SQLITE_SCHEMA)

    assert res.status == "success" and res.summary is None
    assert res.rows == [(3,)]
    assert res.usage is not None
    assert (res.usage.prompt_tokens, res.usage.completion_tokens) == (100, 20)


async def test_pipeline_summary_disabled_makes_no_call() -> None:
    llm = ScriptedLLM(GOOD)
    ex = FakeExecutor(result([("count", "integer")], [(3,)]))
    res = await pipeline(llm, ex, summary_enabled=False).run(QUESTION, SQLITE_SCHEMA)
    assert len(llm.calls) == 1
    assert res.summary is None and res.timings.summary_ms == 0


async def test_pipeline_no_rows_makes_no_summary_call() -> None:
    llm = ScriptedLLM(GOOD)
    ex = FakeExecutor(result([("count", "integer")], []))
    res = await pipeline(llm, ex, summary_enabled=True).run(QUESTION, SQLITE_SCHEMA)
    assert len(llm.calls) == 1 and res.summary is None


async def test_pipeline_blocked_makes_no_summary_call() -> None:
    llm = ScriptedLLM("```sql\nDROP TABLE singer\n```")
    res = await pipeline(llm, FakeExecutor(), summary_enabled=True).run(QUESTION, SQLITE_SCHEMA)
    assert res.status == "blocked" and len(llm.calls) == 1 and res.summary is None
