import json
from collections.abc import Callable, Iterator

import httpx
import pytest

from qm_engine.config import EngineConfig
from qm_engine.llm.client import ChatResult, LLMError, Message, OpenAICompatibleClient
from qm_engine.observability import configure_logging

MESSAGES: list[Message] = [
    {"role": "system", "content": "You write SQL."},
    {"role": "user", "content": "How many customers?"},
]


def ok_body(text: str = "```sql\nSELECT 1\n```", usage: dict | None = None) -> dict:
    body: dict = {
        "model": "qwen2.5-coder:7b",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}}],
    }
    if usage is not None:
        body["usage"] = usage
    return body


USAGE = {"prompt_tokens": 120, "completion_tokens": 12, "total_tokens": 132}


class Recorder:
    """Fake transport: replays queued responses and records every request."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def make_cfg(**kwargs: object) -> EngineConfig:
    return EngineConfig(_env_file=None, **kwargs)  # type: ignore[arg-type]


@pytest.fixture
def logs(capsys: pytest.CaptureFixture[str]) -> Iterator[Callable[[], list[dict]]]:
    configure_logging(level="DEBUG")

    def read() -> list[dict]:
        return [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.strip()]

    yield read


def client(rec: Recorder, sleeps: Sleeps | None = None, **cfg: object) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        make_cfg(**cfg), transport=httpx.MockTransport(rec), sleep=sleeps or Sleeps()
    )


# --- request shape ---


async def test_ollama_request_shape() -> None:
    rec = Recorder(httpx.Response(200, json=ok_body(usage=USAGE)))
    c = client(rec, llm_base_url="http://localhost:11434/v1/", llm_num_ctx=8192)
    await c.chat(MESSAGES, max_tokens=256)

    (req,) = rec.requests
    assert req.method == "POST"
    assert str(req.url) == "http://localhost:11434/v1/chat/completions"
    assert "authorization" not in req.headers
    body = rec.bodies()[0]
    assert body["model"] == "qwen2.5-coder:7b"
    assert body["messages"] == MESSAGES
    assert body["temperature"] == 0
    assert body["max_tokens"] == 256
    assert body["stream"] is False
    assert body["options"] == {"num_ctx": 8192}


async def test_groq_request_has_key_and_no_num_ctx() -> None:
    rec = Recorder(httpx.Response(200, json=ok_body(usage=USAGE)))
    c = client(
        rec,
        llm_provider="groq",
        llm_api_key="gsk-test",
        llm_base_url="https://api.groq.com/openai/v1",
        llm_model="openai/gpt-oss-120b",
    )
    await c.chat(MESSAGES)

    (req,) = rec.requests
    assert str(req.url) == "https://api.groq.com/openai/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer gsk-test"
    body = rec.bodies()[0]
    assert "options" not in body
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["max_tokens"] == 512  # default
    assert body["temperature"] == 0


# --- response parsing and usage ---


async def test_usage_captured() -> None:
    rec = Recorder(httpx.Response(200, json=ok_body("SELECT 1", usage=USAGE)))
    result = await client(rec).chat(MESSAGES)
    assert isinstance(result, ChatResult)
    assert result.text == "SELECT 1"
    assert (result.prompt_tokens, result.completion_tokens) == (120, 12)
    assert result.model == "qwen2.5-coder:7b"
    assert result.latency_ms >= 0


async def test_missing_usage_gives_none_and_logs(logs: Callable[[], list[dict]]) -> None:
    rec = Recorder(httpx.Response(200, json=ok_body()))
    result = await client(rec).chat(MESSAGES, purpose="generation")
    assert result.prompt_tokens is None
    assert result.completion_tokens is None
    events = {line["event"]: line for line in logs()}
    assert events["usage_missing"]["level"] == "warning"
    assert events["usage_missing"]["purpose"] == "generation"


async def test_llm_call_logged_without_api_key(logs: Callable[[], list[dict]]) -> None:
    rec = Recorder(httpx.Response(200, json=ok_body(usage=USAGE)))
    await client(rec, llm_provider="groq", llm_api_key="gsk-secret").chat(MESSAGES)
    lines = logs()
    call = next(line for line in lines if line["event"] == "llm_call")
    assert call["prompt_tokens"] == 120
    assert "gsk-secret" not in json.dumps(lines)


async def test_null_content_becomes_empty_text() -> None:
    body = ok_body(usage=USAGE)
    body["choices"][0]["message"]["content"] = None
    result = await client(Recorder(httpx.Response(200, json=body))).chat(MESSAGES)
    assert result.text == ""


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"error": "x"}),
        httpx.Response(200, text="not json"),
    ],
)
async def test_malformed_response_is_unavailable(response: httpx.Response) -> None:
    with pytest.raises(LLMError) as err:
        await client(Recorder(response)).chat(MESSAGES)
    assert err.value.code == "LLM_UNAVAILABLE"


# --- retries and error mapping ---


async def test_429_retried_once_honouring_retry_after() -> None:
    sleeps = Sleeps()
    rec = Recorder(
        httpx.Response(429, headers={"Retry-After": "2"}),
        httpx.Response(200, json=ok_body(usage=USAGE)),
    )
    result = await client(rec, sleeps).chat(MESSAGES)
    assert result.prompt_tokens == 120
    assert len(rec.requests) == 2
    assert sleeps.calls == [2.0]


async def test_429_twice_maps_to_rate_limited() -> None:
    sleeps = Sleeps()
    rec = Recorder(
        httpx.Response(429, headers={"Retry-After": "1"}),
        httpx.Response(429, headers={"Retry-After": "1"}),
    )
    with pytest.raises(LLMError) as err:
        await client(rec, sleeps).chat(MESSAGES)
    assert err.value.code == "LLM_RATE_LIMITED"
    assert len(rec.requests) == 2
    assert sleeps.calls == [1.0]


async def test_429_with_long_retry_after_is_not_retried() -> None:
    sleeps = Sleeps()
    rec = Recorder(httpx.Response(429, headers={"Retry-After": "30"}))
    with pytest.raises(LLMError) as err:
        await client(rec, sleeps).chat(MESSAGES)
    assert err.value.code == "LLM_RATE_LIMITED"
    assert len(rec.requests) == 1
    assert sleeps.calls == []


async def test_429_without_retry_after_waits_default() -> None:
    sleeps = Sleeps()
    rec = Recorder(httpx.Response(429), httpx.Response(200, json=ok_body(usage=USAGE)))
    await client(rec, sleeps).chat(MESSAGES)
    assert sleeps.calls == [1.0]


async def test_5xx_retried_once_then_unavailable() -> None:
    rec = Recorder(httpx.Response(503), httpx.Response(500))
    with pytest.raises(LLMError) as err:
        await client(rec).chat(MESSAGES)
    assert err.value.code == "LLM_UNAVAILABLE"
    assert len(rec.requests) == 2


async def test_5xx_then_success() -> None:
    rec = Recorder(httpx.Response(502), httpx.Response(200, json=ok_body(usage=USAGE)))
    result = await client(rec).chat(MESSAGES)
    assert result.text


async def test_timeout_is_unavailable_and_not_retried() -> None:
    rec = Recorder(httpx.ReadTimeout("slow"))
    with pytest.raises(LLMError) as err:
        await client(rec).chat(MESSAGES)
    assert err.value.code == "LLM_UNAVAILABLE"
    assert "timed out" in err.value.message
    assert len(rec.requests) == 1


async def test_connection_error_retried_once() -> None:
    rec = Recorder(httpx.ConnectError("refused"), httpx.Response(200, json=ok_body(usage=USAGE)))
    result = await client(rec).chat(MESSAGES)
    assert result.text
    assert len(rec.requests) == 2


@pytest.mark.parametrize("status", [400, 401, 404])
async def test_client_errors_fail_without_retry(status: int) -> None:
    rec = Recorder(httpx.Response(status, text="bad model"))
    with pytest.raises(LLMError) as err:
        await client(rec).chat(MESSAGES)
    assert err.value.code == "LLM_UNAVAILABLE"
    assert str(status) in err.value.message
    assert len(rec.requests) == 1


# --- context-length warning ---


async def test_warns_when_prompt_nears_num_ctx(logs: Callable[[], list[dict]]) -> None:
    usage = {"prompt_tokens": 7400, "completion_tokens": 10}  # > 0.9 * 8192 = 7372.8
    rec = Recorder(httpx.Response(200, json=ok_body(usage=usage)))
    await client(rec, llm_num_ctx=8192).chat(MESSAGES)
    warn = [line for line in logs() if line["event"] == "prompt_near_context_limit"]
    assert len(warn) == 1
    assert warn[0]["level"] == "warning"
    assert warn[0]["prompt_tokens"] == 7400


async def test_no_warning_below_threshold_or_for_groq(logs: Callable[[], list[dict]]) -> None:
    below = {"prompt_tokens": 7000, "completion_tokens": 10}
    big = {"prompt_tokens": 9000, "completion_tokens": 10}
    await client(Recorder(httpx.Response(200, json=ok_body(usage=below)))).chat(MESSAGES)
    await client(
        Recorder(httpx.Response(200, json=ok_body(usage=big))),
        llm_provider="groq",
        llm_api_key="k",
    ).chat(MESSAGES)
    assert not [line for line in logs() if line["event"] == "prompt_near_context_limit"]
