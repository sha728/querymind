"""OpenAI-compatible chat client used for every LLM call (design D1, §6.1, R2.5, R10.5).

One implementation serves Ollama (local) and Groq (hosted): provider, base URL, model and key
come from ``EngineConfig``. Requests go straight to ``{base_url}/chat/completions`` with
``httpx`` so retries and error mapping are explicit (design §12).
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict

import httpx

from qm_engine.config import EngineConfig
from qm_engine.observability import Timer, get_logger

DEFAULT_MAX_TOKENS = 512
MAX_RETRY_AFTER_S = 5.0  # honour Retry-After only up to this (design §12)
DEFAULT_RETRY_DELAY_S = 1.0
CONTEXT_WARN_RATIO = 0.9  # warn when a prompt nears Ollama's num_ctx (design §14.2)

LLMErrorCode = Literal["LLM_UNAVAILABLE", "LLM_RATE_LIMITED"]

_log = get_logger()


class Message(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class ChatResult:
    text: str
    prompt_tokens: int | None  # None when the provider did not report usage (never estimated)
    completion_tokens: int | None
    latency_ms: int
    model: str


class LLMError(Exception):
    def __init__(self, code: LLMErrorCode, message: str) -> None:
        self.code: LLMErrorCode = code
        self.message = message
        super().__init__(f"{code}: {message}")


class LLMClient(Protocol):
    async def chat(
        self, messages: list[Message], *, max_tokens: int = DEFAULT_MAX_TOKENS, purpose: str = ""
    ) -> ChatResult: ...


class _Retry(Exception):
    def __init__(self, delay_s: float, error: LLMError) -> None:
        self.delay_s = delay_s
        self.error = error


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Seconds from a numeric Retry-After header; None if absent or unparseable."""
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


class OpenAICompatibleClient:
    def __init__(
        self,
        cfg: EngineConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.cfg = cfg
        self._sleep = sleep
        headers = {"Content-Type": "application/json"}
        if cfg.llm_api_key is not None:
            headers["Authorization"] = f"Bearer {cfg.llm_api_key.get_secret_value()}"
        self._http = httpx.AsyncClient(
            base_url=cfg.llm_base_url.rstrip("/"),
            headers=headers,
            timeout=cfg.llm_timeout_s,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    def _body(self, messages: list[Message], max_tokens: int) -> dict[str, object]:
        body: dict[str, object] = {
            "model": self.cfg.llm_model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if self.cfg.llm_provider == "ollama":
            body["options"] = {"num_ctx": self.cfg.llm_num_ctx}
        return body

    async def chat(
        self, messages: list[Message], *, max_tokens: int = DEFAULT_MAX_TOKENS, purpose: str = ""
    ) -> ChatResult:
        body = self._body(messages, max_tokens)
        with Timer() as t:
            try:
                data = await self._post(body)
            except _Retry as first:
                _log.warning(
                    "llm_retry", purpose=purpose, code=first.error.code, delay_s=first.delay_s
                )
                await self._sleep(first.delay_s)
                try:
                    data = await self._post(body)
                except _Retry as second:
                    raise second.error from None
        return self._parse(data, t.elapsed_ms, purpose)

    async def _post(self, body: dict[str, object]) -> dict[str, object]:
        try:
            response = await self._http.post("/chat/completions", json=body)
        except httpx.TimeoutException as e:
            # Not retried: a second full timeout would exceed the API's engine timeout.
            raise LLMError(
                "LLM_UNAVAILABLE", f"LLM request timed out after {self.cfg.llm_timeout_s} s"
            ) from e
        except httpx.TransportError as e:
            raise _Retry(
                DEFAULT_RETRY_DELAY_S, LLMError("LLM_UNAVAILABLE", f"LLM unreachable: {e}")
            ) from e

        status = response.status_code
        if status == 429:
            error = LLMError("LLM_RATE_LIMITED", "LLM provider rate limit reached")
            delay = _retry_after_seconds(response)
            delay = DEFAULT_RETRY_DELAY_S if delay is None else delay
            if delay > MAX_RETRY_AFTER_S:
                raise error
            raise _Retry(delay, error)
        if status >= 500:
            raise _Retry(
                DEFAULT_RETRY_DELAY_S,
                LLMError("LLM_UNAVAILABLE", f"LLM provider error {status}"),
            )
        if status >= 400:
            raise LLMError(
                "LLM_UNAVAILABLE", f"LLM request rejected ({status}): {response.text[:300]}"
            )
        try:
            data = response.json()
        except json.JSONDecodeError as e:
            raise LLMError("LLM_UNAVAILABLE", "LLM returned a non-JSON response") from e
        if not isinstance(data, dict):
            raise LLMError("LLM_UNAVAILABLE", "LLM returned an unexpected response shape")
        return data

    def _parse(self, data: dict[str, object], latency_ms: int, purpose: str) -> ChatResult:
        choices = data.get("choices")
        message = choices[0].get("message") if isinstance(choices, list) and choices else None
        if not isinstance(message, dict):
            raise LLMError("LLM_UNAVAILABLE", "LLM response had no message content")
        text = message.get("content") or ""

        usage = data.get("usage")
        prompt_tokens = completion_tokens = None
        if isinstance(usage, dict):
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")
        if prompt_tokens is None or completion_tokens is None:
            _log.warning("usage_missing", purpose=purpose, model=self.cfg.llm_model)

        if (
            self.cfg.llm_provider == "ollama"
            and prompt_tokens is not None
            and prompt_tokens > CONTEXT_WARN_RATIO * self.cfg.llm_num_ctx
        ):
            _log.warning(
                "prompt_near_context_limit",
                purpose=purpose,
                prompt_tokens=prompt_tokens,
                num_ctx=self.cfg.llm_num_ctx,
            )

        model = data.get("model")
        result = ChatResult(
            text=str(text),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            model=model if isinstance(model, str) else self.cfg.llm_model,
        )
        _log.info(
            "llm_call",
            purpose=purpose,
            model=result.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
        )
        return result
