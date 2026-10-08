"""Structured JSON logging, correlation-ID context and stage timers (design §11)."""

import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from types import TracebackType
from typing import Literal, Self, TextIO

import structlog
from structlog.typing import EventDict, FilteringBoundLogger, WrappedLogger

SERVICE_NAME = "engine"
REDACTED = "[REDACTED]"

# Key names whose values are never logged (design §11.2). Matched case-insensitively,
# exactly or as a suffix, so `llm_api_key` is redacted but `prompt_tokens` is not.
_SENSITIVE_KEYS = frozenset(
    {"password", "api_key", "token", "access_token", "authorization", "secret", "internal_key"}
)
_SENSITIVE_SUFFIXES = ("_password", "_api_key", "_token", "_secret", "_internal_key")

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def _is_sensitive(key: str) -> bool:
    k = key.lower()
    return k in _SENSITIVE_KEYS or k.endswith(_SENSITIVE_SUFFIXES)


def _redact(value: object) -> object:
    if isinstance(value, dict):
        return {k: REDACTED if _is_sensitive(str(k)) else _redact(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_redact(v) for v in value]
    return value


def _add_service(_: WrappedLogger, __: str, event: EventDict) -> EventDict:
    event["service"] = SERVICE_NAME
    return event


def _add_correlation_id(_: WrappedLogger, __: str, event: EventDict) -> EventDict:
    event["correlation_id"] = correlation_id_var.get()
    return event


def _redact_processor(_: WrappedLogger, __: str, event: EventDict) -> EventDict:
    return {k: REDACTED if _is_sensitive(k) else _redact(v) for k, v in event.items()}


class _CurrentStreamLogger:
    """Writes each line to whatever ``sys.stdout``/``sys.stderr`` is *at write time*.

    Binding the stream object at configure time breaks when it is later replaced or closed
    (e.g. pytest's output capture).
    """

    def __init__(self, target: Literal["stdout", "stderr"]) -> None:
        self._target = target

    def msg(self, message: str) -> None:
        print(message, file=getattr(sys, self._target), flush=True)

    log = debug = info = warn = warning = error = critical = exception = fatal = msg


def configure_logging(
    level: str = "INFO",
    stream: TextIO | None = None,
    *,
    target: Literal["stdout", "stderr"] = "stdout",
) -> None:
    """Configure structlog to write one JSON object per line.

    Lines go to ``stream`` if given, otherwise to the *current* ``sys.stdout`` or
    ``sys.stderr`` (``target``), looked up at each write.
    """
    factory = (
        structlog.PrintLoggerFactory(file=stream)
        if stream is not None
        else (lambda *_: _CurrentStreamLogger(target))
    )
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
            _add_service,
            _add_correlation_id,
            structlog.processors.format_exc_info,
            _redact_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=factory,
        cache_logger_on_first_use=False,
    )


def get_logger() -> FilteringBoundLogger:
    return structlog.get_logger()


@contextmanager
def correlation_context(correlation_id: str) -> Iterator[None]:
    """Bind a correlation ID to every log line emitted inside the block (R10.1)."""
    token = correlation_id_var.set(correlation_id)
    try:
        yield
    finally:
        correlation_id_var.reset(token)


class Timer:
    """Measures wall-clock time of a block with ``time.perf_counter`` (R10.4)."""

    def __init__(self) -> None:
        self._start: float | None = None
        self.elapsed_ms: int = 0

    def __enter__(self) -> Self:
        self._start = time.perf_counter()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self._start is not None
        self.elapsed_ms = round((time.perf_counter() - self._start) * 1000)
