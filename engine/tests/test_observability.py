import json
import time
from collections.abc import Iterator

import pytest

from qm_engine.observability import (
    REDACTED,
    Timer,
    configure_logging,
    correlation_context,
    correlation_id_var,
    get_logger,
)


@pytest.fixture(autouse=True)
def json_logging(capsys: pytest.CaptureFixture[str]) -> Iterator[None]:
    configure_logging(level="DEBUG")
    yield
    assert correlation_id_var.get() is None


def _lines(capsys: pytest.CaptureFixture[str]) -> list[dict]:
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def test_each_line_is_json_with_required_fields(capsys: pytest.CaptureFixture[str]) -> None:
    log = get_logger()
    log.info("ask_received", question_len=42)
    log.warning("sql_blocked", code="FORBIDDEN_FUNCTION")

    lines = _lines(capsys)
    assert len(lines) == 2
    for line in lines:
        for field in ("ts", "level", "service", "event", "correlation_id"):
            assert field in line
        assert line["service"] == "engine"
    assert lines[0]["event"] == "ask_received"
    assert lines[0]["level"] == "info"
    assert lines[0]["question_len"] == 42
    assert lines[1]["level"] == "warning"


def test_correlation_id_bound_inside_context_only(capsys: pytest.CaptureFixture[str]) -> None:
    log = get_logger()
    with correlation_context("6f1c0000-0000-4000-8000-000000000001"):
        log.info("inside")
    log.info("outside")

    inside, outside = _lines(capsys)
    assert inside["correlation_id"] == "6f1c0000-0000-4000-8000-000000000001"
    assert outside["correlation_id"] is None


def test_sensitive_values_redacted_but_token_counts_kept(
    capsys: pytest.CaptureFixture[str],
) -> None:
    get_logger().info(
        "llm_call",
        password="hunter2",
        api_key="sk-live",
        token="eyJ.abc",
        llm_api_key="gsk-x",
        headers={"Authorization": "Bearer abc", "X-Internal-Key": "k"},
        prompt_tokens=1830,
        completion_tokens=96,
    )

    (line,) = _lines(capsys)
    assert line["password"] == REDACTED
    assert line["api_key"] == REDACTED
    assert line["token"] == REDACTED
    assert line["llm_api_key"] == REDACTED
    assert line["headers"]["Authorization"] == REDACTED
    assert line["prompt_tokens"] == 1830
    assert line["completion_tokens"] == 96
    raw = json.dumps(line)
    for secret in ("hunter2", "sk-live", "eyJ.abc", "gsk-x", "Bearer abc"):
        assert secret not in raw


def test_level_filtering(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="WARNING")
    log = get_logger()
    log.info("dropped")
    log.warning("kept")

    assert [line["event"] for line in _lines(capsys)] == ["kept"]


def test_timer_records_elapsed_ms() -> None:
    with Timer() as t:
        time.sleep(0.05)
    assert 45 <= t.elapsed_ms < 1000


def test_timer_records_even_when_block_raises() -> None:
    t = Timer()
    with pytest.raises(RuntimeError), t:
        time.sleep(0.01)
        raise RuntimeError("boom")
    assert t.elapsed_ms >= 5
