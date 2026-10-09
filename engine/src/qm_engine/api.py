"""Internal engine HTTP API (design §4.1, §4.3, §11, §12; R1.2, R1.3, R8.1, R10.1, R10.3).

Only the .NET API calls this service. Every endpoint except ``/health`` requires the shared
``X-Internal-Key``. Requests take only the question: pipeline options come from engine
configuration, so a caller cannot weaken the row cap or other limits (design A4).

``create_app`` takes its collaborators as an ``EngineDeps`` so tests can use fakes;
``build_deps`` wires the real ones and ``main`` serves them with uvicorn.
"""

import asyncio
import hmac
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from qm_engine.config import EngineConfig
from qm_engine.execution.base import Executor, TargetDBUnavailable
from qm_engine.fewshot import FewShotSelector, load_yaml_pool
from qm_engine.llm.client import LLMClient, LLMError
from qm_engine.llm.embeddings import Embedder, EmbeddingError
from qm_engine.observability import correlation_context, correlation_id_var, get_logger
from qm_engine.pipeline import Pipeline, PipelineResult, QuestionError
from qm_engine.schema.models import SchemaSnapshot

CORRELATION_HEADER = "X-Correlation-ID"
INTERNAL_KEY_HEADER = "X-Internal-Key"
_CORRELATION_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

BACKOFF_FIRST_S = 1.0
BACKOFF_MAX_S = 30.0
LLM_CHECK_TTL_S = 60.0
DB_CHECK_TIMEOUT_S = 3.0

_log = get_logger()


# --- dependencies -------------------------------------------------------------------------


@dataclass
class EngineDeps:
    cfg: EngineConfig
    llm: LLMClient
    executor: Executor
    introspect: Callable[[], Awaitable[SchemaSnapshot]]
    llm_ping: Callable[[], Awaitable[bool]]
    embedder: Embedder | None = None
    fewshot: FewShotSelector | None = None
    startup: Callable[[], Awaitable[None]] | None = None
    shutdown: Callable[[], Awaitable[None]] | None = None
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    clock: Callable[[], float] = time.monotonic


class SchemaState:
    """The one in-memory schema snapshot (design §5.3).

    A refresh introspects first and then replaces the reference in a single assignment, so a
    request always sees one whole snapshot, and a failed refresh keeps the previous one.
    """

    def __init__(self) -> None:
        self.snapshot: SchemaSnapshot | None = None
        self._lock = asyncio.Lock()

    async def refresh(self, introspect: Callable[[], Awaitable[SchemaSnapshot]]) -> SchemaSnapshot:
        async with self._lock:  # one introspection at a time
            new = await introspect()
            self.snapshot = new
        _log.info("schema_loaded", tables=len(new.tables), schema_hash=new.schema_hash)
        return new


def backoff_delays() -> Iterator[float]:
    """1, 2, 4 … capped at 30 s, forever (design §6.2 step 0)."""
    delay = BACKOFF_FIRST_S
    while True:
        yield delay
        delay = min(delay * 2, BACKOFF_MAX_S)


async def load_schema_with_backoff(
    state: SchemaState,
    introspect: Callable[[], Awaitable[SchemaSnapshot]],
    sleep: Callable[[float], Awaitable[None]],
) -> SchemaSnapshot:
    """Startup introspection: retry until it succeeds; /health is ``degraded`` meanwhile."""
    for attempt, delay in enumerate(backoff_delays(), start=1):
        try:
            return await state.refresh(introspect)
        except Exception as e:  # any failure: DB down, role missing, network
            _log.warning(
                "schema_load_failed", attempt=attempt, retry_in_s=delay, error=type(e).__name__
            )
            await sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


class _TTLCheck:
    """Caches a boolean health check for ``ttl_s`` seconds (the LLM check, design §4.3)."""

    def __init__(
        self, check: Callable[[], Awaitable[bool]], ttl_s: float, clock: Callable[[], float]
    ) -> None:
        self._check = check
        self._ttl_s = ttl_s
        self._clock = clock
        self._value: bool | None = None
        self._at = 0.0

    async def __call__(self) -> bool:
        now = self._clock()
        if self._value is None or now - self._at >= self._ttl_s:
            try:
                self._value = await self._check()
            except Exception:
                self._value = False
            self._at = now
        return self._value


# --- request and response shapes (snake_case, design §4.1) --------------------------------


class QueryRequest(BaseModel):
    """Only the question. Extra fields (e.g. ``row_limit``) are rejected (design A4)."""

    model_config = ConfigDict(extra="forbid")

    question: str


@dataclass
class ApiError(Exception):
    status_code: int
    code: str
    message: str
    headers: dict[str, str] = field(default_factory=dict)


def error_body(code: str, message: str, correlation_id: str | None) -> dict[str, object]:
    return {"error": {"code": code, "message": message, "correlation_id": correlation_id}}


def query_body(r: PipelineResult) -> dict[str, object]:
    """Design §4.3. Result fields are null unless the status is ``success`` (design §4.2)."""
    ok = r.status == "success"
    chart = r.chart
    return {
        "status": r.status,
        "sql": r.sql,
        "message": r.message,
        "columns": (
            [{"name": c.name, "type": c.norm_type, "db_type": c.db_type} for c in r.columns]
            if ok
            else None
        ),
        "rows": [list(row) for row in r.rows] if ok else None,
        "row_count": r.row_count if ok else None,
        "truncated": r.truncated if ok else None,
        "chart": (
            {
                "recommended": chart.recommended,
                "allowed": list(chart.allowed),
                "x": chart.x,
                "y": list(chart.y),
            }
            if ok and chart is not None
            else None
        ),
        "summary": r.summary if ok else None,
        "attempts": [
            {
                "n": a.n,
                "sql": a.sql,
                "error_code": a.error_code,
                "error": a.error,
                "stage": a.stage,
                "latency_ms": a.latency_ms,
                "prompt_tokens": a.prompt_tokens,
                "completion_tokens": a.completion_tokens,
            }
            for a in r.attempts
        ],
        "linking": {
            "mode": r.linking.mode,
            "applied": r.linking.applied,
            "tables": list(r.linking.tables),
        },
        "few_shot_ids": list(r.few_shot_ids),
        "timings": dict(r.timings.__dict__),
        "usage": (
            {
                "model": r.usage.model,
                "prompt_tokens": r.usage.prompt_tokens,
                "completion_tokens": r.usage.completion_tokens,
            }
            if r.usage
            else None
        ),
    }


def schema_body(s: SchemaSnapshot) -> dict[str, object]:
    """Design §4.3 ``/v1/schema``."""
    return {
        "schema_hash": s.schema_hash,
        "introspected_at": s.introspected_at.isoformat().replace("+00:00", "Z"),
        "dialect": s.dialect,
        "tables": [
            {
                "name": t.name,
                "columns": [
                    {
                        "name": c.name,
                        "type": c.norm_type,
                        "nullable": c.nullable,
                        "primary_key": c.primary_key,
                        "samples": list(c.samples),
                    }
                    for c in t.columns
                ],
                "foreign_keys": [
                    {
                        "columns": list(fk.columns),
                        "ref_table": fk.ref_table,
                        "ref_columns": list(fk.ref_columns),
                    }
                    for fk in t.foreign_keys
                ],
            }
            for t in s.tables
        ],
    }


# --- app ----------------------------------------------------------------------------------


def _correlation_id(request: Request) -> str:
    given = request.headers.get(CORRELATION_HEADER, "")
    return given if _CORRELATION_ID_RE.fullmatch(given) else str(uuid.uuid4())


def _key_ok(given: str | None, expected: str) -> bool:
    return given is not None and hmac.compare_digest(given.encode(), expected.encode())


def create_app(deps: EngineDeps) -> FastAPI:
    cfg = deps.cfg
    if cfg.engine_internal_key is None or not cfg.engine_internal_key.get_secret_value():
        raise ValueError("ENGINE_INTERNAL_KEY must be set for the engine API")
    internal_key = cfg.engine_internal_key.get_secret_value()

    state = SchemaState()
    pipeline = Pipeline(cfg, deps.llm, deps.executor, embedder=deps.embedder, fewshot=deps.fewshot)
    llm_check = _TTLCheck(deps.llm_ping, LLM_CHECK_TTL_S, deps.clock)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if deps.startup is not None:
            await deps.startup()
        loader = asyncio.create_task(load_schema_with_backoff(state, deps.introspect, deps.sleep))
        try:
            yield
        finally:
            loader.cancel()
            try:
                await loader
            except (asyncio.CancelledError, Exception):
                pass
            if deps.shutdown is not None:
                await deps.shutdown()

    app = FastAPI(
        title="QueryMind engine (internal)",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.schema = state

    @app.middleware("http")
    async def correlation_and_auth(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        cid = _correlation_id(request)
        with correlation_context(cid, request.headers.get("X-User-Id")):
            if request.url.path.startswith("/v1") and not _key_ok(
                request.headers.get(INTERNAL_KEY_HEADER), internal_key
            ):
                _log.warning("unauthorized", path=request.url.path)
                response: Response = JSONResponse(
                    error_body("UNAUTHORIZED", "Missing or invalid internal key.", cid), 401
                )
            else:
                try:
                    response = await call_next(request)
                except Exception:
                    _log.exception("unhandled_error", path=request.url.path)
                    response = JSONResponse(
                        error_body("INTERNAL_ERROR", "Internal error.", cid), 500
                    )
            response.headers[CORRELATION_HEADER] = cid
            return response

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, e: ApiError) -> JSONResponse:
        body = error_body(e.code, e.message, correlation_id_var.get())
        return JSONResponse(body, e.status_code, headers=e.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, e: RequestValidationError) -> JSONResponse:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'][1:]) or 'body'}: {err['msg']}"
            for err in e.errors()
        )
        return JSONResponse(
            error_body("VALIDATION_FAILED", problems, correlation_id_var.get()), 400
        )

    def _current_schema() -> SchemaSnapshot:
        snapshot = state.snapshot
        if snapshot is None:
            raise ApiError(503, "TARGET_DB_UNAVAILABLE", "The schema has not been loaded yet.")
        return snapshot

    # --- endpoints ---

    @app.get("/health")
    async def health() -> dict[str, object]:
        async def db_ok() -> bool:
            try:
                await asyncio.wait_for(
                    deps.executor.execute("SELECT 1", 1, 2000), DB_CHECK_TIMEOUT_S
                )
            except Exception:
                return False
            return True

        snapshot = state.snapshot
        checks = {
            "target_db": "ok" if await db_ok() else "error",
            "llm": "ok" if await llm_check() else "error",
            "schema_cache": "ok" if snapshot is not None else "loading",
        }
        return {
            "status": "ok" if all(v == "ok" for v in checks.values()) else "degraded",
            "checks": checks,
            "schema_hash": snapshot.schema_hash if snapshot else None,
            "model": cfg.llm_model,
        }

    @app.post("/v1/query")
    async def query(body: QueryRequest) -> dict[str, object]:
        snapshot = _current_schema()  # read once: the whole request uses this snapshot
        _log.info("ask_received", question_chars=len(body.question))
        try:
            result = await pipeline.run(body.question, snapshot)
        except QuestionError as e:
            raise ApiError(400, e.code, str(e)) from e
        except LLMError as e:
            headers = (
                {"Retry-After": str(max(1, round(e.retry_after_s)))}
                if e.retry_after_s is not None
                else {}
            )
            _log.warning("llm_error", code=e.code)
            raise ApiError(503, e.code, "The language model is unavailable.", headers) from e
        except EmbeddingError as e:
            _log.warning("embedding_error", error=str(e))
            raise ApiError(503, "LLM_UNAVAILABLE", "The embedding model is unavailable.") from e
        except TargetDBUnavailable as e:
            _log.warning("target_db_unavailable", error=str(e))
            raise ApiError(503, e.code, "Database unavailable.") from e
        return query_body(result)

    @app.get("/v1/schema")
    async def schema() -> dict[str, object]:
        return schema_body(_current_schema())

    @app.post("/v1/schema/refresh")
    async def schema_refresh() -> dict[str, object]:
        try:
            snapshot = await state.refresh(deps.introspect)
        except Exception as e:
            _log.warning("schema_refresh_failed", error=type(e).__name__)
            raise ApiError(
                503, "TARGET_DB_UNAVAILABLE", "Schema refresh failed; the previous schema is kept."
            ) from e
        return schema_body(snapshot)

    return app


# --- production wiring --------------------------------------------------------------------


def build_deps(cfg: EngineConfig) -> EngineDeps:
    """Real collaborators: OpenAI-compatible LLM, pooled read-only executor, Ollama embedder."""
    from qm_engine.execution.postgres import PostgresExecutor
    from qm_engine.llm.client import OpenAICompatibleClient
    from qm_engine.llm.embeddings import OllamaEmbedder
    from qm_engine.schema.introspect_pg import introspect_pg

    if cfg.dialect != "postgres":
        raise ValueError("the engine API serves the postgres target database only")
    llm = OpenAICompatibleClient(cfg)
    executor = PostgresExecutor(cfg)
    needs_embedder = cfg.linking_mode != "off" or cfg.few_shot_enabled
    embedder = OllamaEmbedder(cfg) if needs_embedder else None
    fewshot = (
        FewShotSelector(load_yaml_pool(Path(cfg.few_shot_pool)), embedder, cache_dir=cfg.cache_dir)
        if cfg.few_shot_enabled and embedder is not None
        else None
    )

    async def startup() -> None:
        await executor.open(wait=False)  # connects in the background; /health shows progress

    async def shutdown() -> None:
        await executor.close()
        await llm.aclose()
        if embedder is not None:
            await embedder.aclose()

    return EngineDeps(
        cfg=cfg,
        llm=llm,
        executor=executor,
        introspect=lambda: introspect_pg(cfg),
        llm_ping=llm.ping,
        embedder=embedder,
        fewshot=fewshot,
        startup=startup,
        shutdown=shutdown,
    )


def app_factory() -> FastAPI:
    """Entry point for ``uvicorn qm_engine.api:app_factory --factory``."""
    from qm_engine.observability import configure_logging

    configure_logging()
    return create_app(build_deps(EngineConfig()))


def main() -> None:
    """``qm-engine``: serve the API (port 8000, all interfaces inside the container)."""
    import uvicorn

    from qm_engine.execution.postgres import use_selector_event_loop_on_windows

    use_selector_event_loop_on_windows()
    uvicorn.run("qm_engine.api:app_factory", factory=True, host="0.0.0.0", port=8000)
