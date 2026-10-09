"""Engine HTTP API with fakes: no real LLM, database or embedder (design §4.3, §12, §13.1)."""

import asyncio
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from fakes import FakeExecutor, ScriptedLLM, result
from qm_engine.api import (
    EngineDeps,
    SchemaState,
    backoff_delays,
    create_app,
    load_schema_with_backoff,
)
from qm_engine.config import EngineConfig
from qm_engine.execution.base import ExecResult, ExecutionError, TargetDBUnavailable
from qm_engine.llm.client import LLMError
from qm_engine.schema.models import Column, ForeignKey, SchemaSnapshot, Table

KEY = "test-internal-key"
AUTH = {"X-Internal-Key": KEY}
GOOD = "```sql\nSELECT name, count(*) AS n FROM singer GROUP BY name\n```"

SINGER = Table(
    "singer",
    (
        Column("singer_id", "integer", "integer", False, True, ("1", "2")),
        Column("name", "text", "text", True, False, ("Joe Sharp",)),
        Column("country_id", "integer", "integer", True, False, ()),
    ),
    (ForeignKey(("country_id",), "country", ("country_id",)),),
)
COUNTRY = Table("country", (Column("country_id", "integer", "integer", False, True, ("1",)),))
SNAP_1 = SchemaSnapshot.build("postgres", [SINGER], datetime(2026, 10, 9, 8, 0, tzinfo=UTC))
SNAP_2 = SchemaSnapshot.build(
    "postgres", [SINGER, COUNTRY], datetime(2026, 10, 9, 9, 0, tzinfo=UTC)
)


def cfg(**kwargs: object) -> EngineConfig:
    base: dict[str, object] = {
        "dialect": "postgres",
        "linking_mode": "off",
        "few_shot_enabled": False,
        "self_correction_enabled": False,
        "summary_enabled": False,
        "unanswerable_enabled": False,
        "include_date": False,
        "cerebras_api_key": "k",
        "engine_internal_key": KEY,
    }
    return EngineConfig(_env_file=None, **(base | kwargs))  # type: ignore[arg-type]


class HealthAwareExecutor(FakeExecutor):
    """Answers the /health probe itself; queues outcomes for real queries."""

    def __init__(
        self, *outcomes: ExecResult | ExecutionError | Exception, db_up: bool = True
    ) -> None:
        super().__init__(*outcomes)  # type: ignore[arg-type]
        self.db_up = db_up

    async def execute(self, sql: str, row_cap: int | None, timeout_ms: int) -> ExecResult:
        if sql == "SELECT 1":
            if not self.db_up:
                raise TargetDBUnavailable("connection refused")
            return result()
        if (
            self.outcomes
            and isinstance(self.outcomes[0], Exception)
            and not isinstance(self.outcomes[0], ExecutionError)
        ):
            raise self.outcomes.pop(0)  # type: ignore[misc]
        return await super().execute(sql, row_cap, timeout_ms)


class Introspector:
    """Returns (or raises) queued outcomes; repeats the last one forever."""

    def __init__(self, *outcomes: SchemaSnapshot | Exception) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    async def __call__(self) -> SchemaSnapshot:
        self.calls += 1
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


ClientFactory = Callable[..., TestClient]


async def short_sleep(_: float) -> None:
    await asyncio.sleep(0.01)


def make_deps(
    llm: ScriptedLLM | None = None,
    executor: HealthAwareExecutor | None = None,
    introspect: Introspector | None = None,
    *,
    llm_up: bool = True,
    config: EngineConfig | None = None,
) -> EngineDeps:
    async def ping() -> bool:
        return llm_up

    return EngineDeps(
        cfg=config or cfg(),
        llm=llm or ScriptedLLM(),
        executor=executor or HealthAwareExecutor(),
        introspect=introspect or Introspector(SNAP_1),
        llm_ping=ping,
        sleep=short_sleep,
    )


def wait_for_schema(client: TestClient) -> None:
    for _ in range(200):
        if client.get("/health").json()["checks"]["schema_cache"] == "ok":
            return
        time.sleep(0.01)
    raise AssertionError("schema never loaded")


@pytest.fixture
def client_for() -> Iterator[ClientFactory]:
    clients: list[TestClient] = []

    def make(deps: EngineDeps, *, wait: bool = True) -> TestClient:
        c = TestClient(create_app(deps), raise_server_exceptions=False)
        c.__enter__()
        clients.append(c)
        if wait:
            wait_for_schema(c)
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)


# --- /health (anonymous, R10.3) ---


def test_health_is_anonymous_and_ok_once_schema_loaded(client_for: ClientFactory) -> None:
    client = client_for(make_deps())
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {
        "status": "ok",
        "checks": {"target_db": "ok", "llm": "ok", "schema_cache": "ok"},
        "schema_hash": SNAP_1.schema_hash,
        "model": "gpt-oss-120b",
    }


def test_health_degraded_until_schema_loads(client_for: ClientFactory) -> None:
    intro = Introspector(ConnectionError("db down"))
    client = client_for(make_deps(introspect=intro), wait=False)
    body = client.get("/health").json()
    assert body["status"] == "degraded"
    assert body["checks"]["schema_cache"] == "loading"
    assert body["schema_hash"] is None

    q = client.post("/v1/query", json={"question": "How many?"}, headers=AUTH)
    assert q.status_code == 503 and q.json()["error"]["code"] == "TARGET_DB_UNAVAILABLE"

    intro.outcomes = [SNAP_1]  # the database comes back: the retry loop picks it up
    wait_for_schema(client)
    assert client.get("/health").json()["status"] == "ok"
    assert intro.calls >= 2


@pytest.mark.parametrize(("db_up", "llm_up"), [(False, True), (True, False)])
def test_health_degraded_when_a_dependency_is_down(
    client_for: ClientFactory,
    db_up: bool,
    llm_up: bool,
) -> None:
    client = client_for(make_deps(executor=HealthAwareExecutor(db_up=db_up), llm_up=llm_up))
    body = client.get("/health").json()
    assert body["status"] == "degraded"
    assert body["checks"]["target_db"] == ("ok" if db_up else "error")
    assert body["checks"]["llm"] == ("ok" if llm_up else "error")


def test_llm_check_is_cached_for_60_seconds(client_for: ClientFactory) -> None:
    now = [1000.0]
    pings = []

    async def ping() -> bool:
        pings.append(now[0])
        return True

    deps = make_deps()
    deps.llm_ping = ping
    deps.clock = lambda: now[0]
    client = client_for(deps)  # wait_for_schema already polled /health
    client.get("/health")
    assert len(pings) == 1
    now[0] += 59
    client.get("/health")
    assert len(pings) == 1
    now[0] += 1
    client.get("/health")
    assert len(pings) == 2


# --- X-Internal-Key on every /v1 endpoint (R8.1) ---

V1_CALLS = [
    ("POST", "/v1/query", {"question": "How many singers?"}),
    ("GET", "/v1/schema", None),
    ("POST", "/v1/schema/refresh", None),
    ("GET", "/v1/does-not-exist", None),
]


@pytest.mark.parametrize(("method", "path", "body"), V1_CALLS)
@pytest.mark.parametrize("headers", [{}, {"X-Internal-Key": "wrong"}, {"X-Internal-Key": ""}])
def test_v1_requires_internal_key(
    client_for: ClientFactory,
    method: str,
    path: str,
    body: dict | None,
    headers: dict,
) -> None:
    llm = ScriptedLLM()  # no replies: any LLM call would fail the test
    intro = Introspector(SNAP_1)
    client = client_for(make_deps(llm=llm, introspect=intro))
    calls_before = intro.calls
    r = client.request(method, path, json=body, headers=headers | {"X-Correlation-ID": "c-401"})
    assert r.status_code == 401
    assert r.json() == {
        "error": {
            "code": "UNAUTHORIZED",
            "message": "Missing or invalid internal key.",
            "correlation_id": "c-401",
        }
    }
    assert r.headers["X-Correlation-ID"] == "c-401"
    assert llm.calls == [] and intro.calls == calls_before


def test_create_app_refuses_to_start_without_a_key() -> None:
    with pytest.raises(ValueError, match="ENGINE_INTERNAL_KEY"):
        create_app(make_deps(config=cfg(engine_internal_key=None)))


# --- /v1/query (design §4.3) ---


def test_query_success_shape_and_correlation_id(client_for: ClientFactory) -> None:
    llm = ScriptedLLM(GOOD, prompt_tokens=230, completion_tokens=41, latency_ms=900)
    ex = HealthAwareExecutor(result([("name", "text"), ("n", "integer")], [("Joe", 2), ("Ann", 1)]))
    client = client_for(make_deps(llm=llm, executor=ex))
    r = client.post(
        "/v1/query",
        json={"question": "Songs per singer?"},
        headers=AUTH | {"X-Correlation-ID": "corr-123", "X-User-Id": "u-1"},
    )
    assert r.status_code == 200
    assert r.headers["X-Correlation-ID"] == "corr-123"
    body = r.json()
    assert list(body) == [
        "status", "sql", "message", "columns", "rows", "row_count", "truncated", "chart",
        "summary", "attempts", "linking", "few_shot_ids", "timings", "usage",
    ]  # fmt: skip
    assert body["status"] == "success"
    assert body["sql"] == "SELECT name, count(*) AS n FROM singer GROUP BY name"
    assert body["message"] is None
    assert body["columns"] == [
        {"name": "name", "type": "text", "db_type": "TEXT"},
        {"name": "n", "type": "integer", "db_type": "INTEGER"},
    ]
    assert body["rows"] == [["Joe", 2], ["Ann", 1]]
    assert (body["row_count"], body["truncated"]) == (2, False)
    assert body["chart"] == {
        "recommended": "pie", "allowed": ["pie", "bar", "table"], "x": "name", "y": ["n"],
    }  # fmt: skip
    assert body["summary"] is None  # summaries are off in this config
    (attempt,) = body["attempts"]
    assert attempt["n"] == 1 and attempt["stage"] == "execute" and attempt["error_code"] is None
    assert (attempt["prompt_tokens"], attempt["completion_tokens"]) == (230, 41)
    assert body["linking"] == {"mode": "off", "applied": False, "tables": []}
    assert body["few_shot_ids"] == []
    assert set(body["timings"]) >= {
        "linking_ms", "fewshot_ms", "generation_ms", "validation_ms", "execution_ms",
        "summary_ms", "total_ms",
    }  # fmt: skip
    assert body["timings"]["generation_ms"] == 900
    assert body["usage"] == {
        "model": "scripted-model",
        "prompt_tokens": 230,
        "completion_tokens": 41,
    }
    # The product row cap from config reaches the executor.
    assert ex.calls[-1][1] == 1000


@pytest.mark.parametrize("headers", [{}, {"X-Correlation-ID": "bad id; <script>"}])
def test_missing_or_malformed_correlation_id_is_replaced(
    client_for: ClientFactory,
    headers: dict,
) -> None:
    client = client_for(make_deps(llm=ScriptedLLM(GOOD), executor=HealthAwareExecutor(result())))
    r = client.post("/v1/query", json={"question": "q?"}, headers=AUTH | headers)
    assert r.status_code == 200
    assert uuid.UUID(r.headers["X-Correlation-ID"]).version == 4


def test_blocked_is_200_with_null_result_fields(client_for: ClientFactory) -> None:
    llm = ScriptedLLM("```sql\nSELECT pg_sleep(10)\n```")
    client = client_for(make_deps(llm=llm))
    r = client.post("/v1/query", json={"question": "Sleep"}, headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "blocked" and body["sql"] == "SELECT pg_sleep(10)"
    assert "FORBIDDEN_FUNCTION" in body["message"]
    for key in ("columns", "rows", "row_count", "truncated", "chart", "summary"):
        assert body[key] is None, key


@pytest.mark.parametrize(
    "body",
    [
        {"question": "Top customers?", "row_limit": 1000000},  # design A4
        {"question": "Top customers?", "linking_mode": "off"},
        {"question": "Top customers?", "max_corrections": 0},
        {},
        {"question": 42},
    ],
)
def test_extra_or_invalid_body_fields_are_rejected(client_for: ClientFactory, body: dict) -> None:
    llm = ScriptedLLM()
    client = client_for(make_deps(llm=llm))
    r = client.post("/v1/query", json=body, headers=AUTH | {"X-Correlation-ID": "c-400"})
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "VALIDATION_FAILED" and err["correlation_id"] == "c-400"
    assert llm.calls == []


@pytest.mark.parametrize("question", ["", "   ", "x" * 1001])
def test_invalid_question_is_400(client_for: ClientFactory, question: str) -> None:
    client = client_for(make_deps())
    r = client.post("/v1/query", json={"question": question}, headers=AUTH)
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_FAILED"


def test_llm_rate_limit_is_503_with_retry_after(client_for: ClientFactory) -> None:
    llm = ScriptedLLM(LLMError("LLM_RATE_LIMITED", "quota", retry_after_s=42.4))
    client = client_for(make_deps(llm=llm))
    r = client.post("/v1/query", json={"question": "q?"}, headers=AUTH)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "LLM_RATE_LIMITED"
    assert r.headers["Retry-After"] == "42"


def test_llm_unavailable_is_503_without_retry_after(client_for: ClientFactory) -> None:
    client = client_for(make_deps(llm=ScriptedLLM(LLMError("LLM_UNAVAILABLE", "boom"))))
    r = client.post("/v1/query", json={"question": "q?"}, headers=AUTH)
    assert r.status_code == 503 and r.json()["error"]["code"] == "LLM_UNAVAILABLE"
    assert "Retry-After" not in r.headers


def test_target_db_unavailable_is_503_not_a_correction(client_for: ClientFactory) -> None:
    llm = ScriptedLLM(GOOD, GOOD, GOOD)
    ex = HealthAwareExecutor(TargetDBUnavailable("pool timeout"))
    client = client_for(make_deps(llm=llm, executor=ex, config=cfg(self_correction_enabled=True)))
    r = client.post("/v1/query", json={"question": "q?"}, headers=AUTH)
    assert r.status_code == 503 and r.json()["error"]["code"] == "TARGET_DB_UNAVAILABLE"
    assert len(llm.calls) == 1  # the model was not asked to "fix" an outage


def test_unhandled_error_is_500_without_details(client_for: ClientFactory) -> None:
    ex = HealthAwareExecutor(RuntimeError("secret internals"))
    client = client_for(make_deps(llm=ScriptedLLM(GOOD), executor=ex))
    r = client.post("/v1/query", json={"question": "q?"}, headers=AUTH | {"X-Correlation-ID": "c5"})
    assert r.status_code == 500
    assert r.json() == {
        "error": {"code": "INTERNAL_ERROR", "message": "Internal error.", "correlation_id": "c5"}
    }
    assert r.headers["X-Correlation-ID"] == "c5"
    assert "secret" not in r.text


# --- /v1/schema and refresh (R1.2, R1.3) ---


def test_schema_returns_snapshot(client_for: ClientFactory) -> None:
    client = client_for(make_deps())
    body = client.get("/v1/schema", headers=AUTH).json()
    assert body["schema_hash"] == SNAP_1.schema_hash
    assert body["introspected_at"] == "2026-10-09T08:00:00Z"
    assert body["dialect"] == "postgres"
    (table,) = body["tables"]
    assert table["name"] == "singer"
    assert table["columns"][0] == {
        "name": "singer_id", "type": "integer", "nullable": False, "primary_key": True,
        "samples": ["1", "2"],
    }  # fmt: skip
    assert table["foreign_keys"] == [
        {"columns": ["country_id"], "ref_table": "country", "ref_columns": ["country_id"]}
    ]


def test_refresh_swaps_the_snapshot(client_for: ClientFactory) -> None:
    intro = Introspector(SNAP_1)
    client = client_for(make_deps(introspect=intro))
    intro.outcomes = [SNAP_2]
    r = client.post("/v1/schema/refresh", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["schema_hash"] == SNAP_2.schema_hash
    assert [t["name"] for t in r.json()["tables"]] == ["singer", "country"]
    assert client.get("/v1/schema", headers=AUTH).json()["schema_hash"] == SNAP_2.schema_hash
    assert client.get("/health").json()["schema_hash"] == SNAP_2.schema_hash


def test_failed_refresh_keeps_the_previous_snapshot(client_for: ClientFactory) -> None:
    intro = Introspector(SNAP_1)
    client = client_for(make_deps(introspect=intro))
    intro.outcomes = [ConnectionError("db down")]
    r = client.post("/v1/schema/refresh", headers=AUTH)
    assert r.status_code == 503 and r.json()["error"]["code"] == "TARGET_DB_UNAVAILABLE"
    assert client.get("/v1/schema", headers=AUTH).json()["schema_hash"] == SNAP_1.schema_hash


async def test_refresh_is_atomic_for_readers() -> None:
    """Readers see the old snapshot until the new one is complete, then the new one."""
    state = SchemaState()
    state.snapshot = SNAP_1
    release = asyncio.Event()
    seen: list[str] = []

    async def slow_introspect() -> SchemaSnapshot:
        await release.wait()
        return SNAP_2

    task = asyncio.create_task(state.refresh(slow_introspect))
    await asyncio.sleep(0)
    assert state.snapshot is not None
    seen.append(state.snapshot.schema_hash)  # mid-refresh
    release.set()
    await task
    seen.append(state.snapshot.schema_hash)
    assert seen == [SNAP_1.schema_hash, SNAP_2.schema_hash]


# --- startup retries with backoff (design §6.2 step 0) ---


def test_backoff_doubles_to_a_30_second_cap() -> None:
    delays = backoff_delays()
    assert [next(delays) for _ in range(8)] == [1, 2, 4, 8, 16, 30, 30, 30]


async def test_startup_load_retries_with_backoff_until_success() -> None:
    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)

    intro = Introspector(*(ConnectionError("down") for _ in range(4)), SNAP_1)
    state = SchemaState()
    snap = await load_schema_with_backoff(state, intro, fake_sleep)
    assert snap is SNAP_1 and state.snapshot is SNAP_1
    assert slept == [1, 2, 4, 8]
    assert intro.calls == 5
