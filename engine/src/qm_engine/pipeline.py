"""The single orchestration path shared by the product and the evaluation harness (design §6).

T13 implements the zero-shot path: steps 1, 4-8 and 12 of design §6.2. Schema linking (step 2,
T22), few-shot selection (step 3, T23), self-correction (step 9, T24), charts (step 10, T30)
and summaries (step 11, T31) are added by later tasks. Until T24, a failed attempt ends the
request even when ``self_correction_enabled`` is set.

LLM transport errors (``LLMError``) are not turned into a status: they propagate so the API can
return 503 and the harness can apply its rate-limit rules (design §10.3, §12).
"""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Literal

from qm_engine.config import EngineConfig
from qm_engine.execution.base import ExecResult, ExecutionError, Executor, ResultColumn
from qm_engine.extract import extract
from qm_engine.llm.client import ChatResult, LLMClient
from qm_engine.observability import Timer, get_logger
from qm_engine.prompts import build_messages
from qm_engine.safety.validator import validate
from qm_engine.schema.models import SchemaSnapshot
from qm_engine.schema.serialize import serialize_schema

MAX_QUESTION_CHARS = 1000
MAX_STORED_ERROR_CHARS = 2000  # query_attempts.error (design §5.1)

Status = Literal["success", "failed", "blocked", "cannot_answer"]
Stage = Literal["extract", "validate", "execute"]

_log = get_logger()


class QuestionError(ValueError):
    """The question is empty or too long (``VALIDATION_FAILED``, design §6.2 step 1)."""

    code = "VALIDATION_FAILED"


@dataclass(frozen=True)
class Attempt:
    n: int
    sql: str | None  # None when extraction found no SQL
    stage: Stage  # where the attempt ended
    error_code: str | None
    error: str | None
    latency_ms: int  # LLM call + validation + execution for this attempt
    prompt_tokens: int | None
    completion_tokens: int | None


@dataclass(frozen=True)
class Timings:
    linking_ms: int = 0
    fewshot_ms: int = 0
    generation_ms: int = 0
    validation_ms: int = 0
    execution_ms: int = 0
    summary_ms: int = 0
    pacing_ms: int = 0  # free-tier pacing wait before LLM calls; included in total_ms only
    total_ms: int = 0  # wall clock of the whole request


@dataclass(frozen=True)
class Usage:
    model: str
    prompt_tokens: int | None  # None if the provider reported no usage (never estimated)
    completion_tokens: int | None


@dataclass(frozen=True)
class Linking:
    mode: str
    applied: bool
    tables: tuple[str, ...] = ()


@dataclass(frozen=True)
class PipelineResult:
    status: Status
    sql: str | None
    message: str | None
    columns: tuple[ResultColumn, ...] = ()
    rows: list[tuple[object, ...]] = field(default_factory=list)
    truncated: bool = False
    attempts: tuple[Attempt, ...] = ()
    linking: Linking = Linking(mode="off", applied=False)
    few_shot_ids: tuple[str, ...] = ()
    timings: Timings = Timings()
    usage: Usage | None = None

    @property
    def row_count(self) -> int:
        return len(self.rows)


def _sum_tokens(values: list[int | None]) -> int | None:
    known = [v for v in values if v is not None]
    return sum(known) if known else None


class Pipeline:
    def __init__(
        self,
        cfg: EngineConfig,
        llm: LLMClient,
        executor: Executor,
        *,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.cfg = cfg
        self.llm = llm
        self.executor = executor
        self._today = today

    async def run(self, question: str, schema: SchemaSnapshot) -> PipelineResult:
        if schema.dialect != self.cfg.dialect:
            raise ValueError(f"schema dialect {schema.dialect} != config {self.cfg.dialect}")
        with Timer() as total:
            result = await self._run(self._normalise(question), schema)
        result = replace(result, timings=replace(result.timings, total_ms=total.elapsed_ms))
        _log.info(
            "ask_completed",
            status=result.status,
            attempts=len(result.attempts),
            prompt_tokens=result.usage.prompt_tokens if result.usage else None,
            completion_tokens=result.usage.completion_tokens if result.usage else None,
            **result.timings.__dict__,
        )
        return result

    @staticmethod
    def _normalise(question: str) -> str:
        q = question.strip()
        if not q:
            raise QuestionError("The question is empty.")
        if len(q) > MAX_QUESTION_CHARS:
            raise QuestionError(f"The question is longer than {MAX_QUESTION_CHARS} characters.")
        return q

    async def _run(self, question: str, schema: SchemaSnapshot) -> PipelineResult:
        cfg = self.cfg
        dialect = schema.dialect

        # Steps 2-4: full schema (linking arrives in T22), no examples (T23), prompt.
        messages = build_messages(
            dialect=dialect,
            schema_text=serialize_schema(schema),
            question=question,
            unanswerable=cfg.unanswerable_enabled,
            today=self._today() if cfg.include_date else None,
        )

        generation_ms = validation_ms = execution_ms = 0

        # Step 5: generate.
        chat: ChatResult = await self.llm.chat(messages, purpose="generation")
        generation_ms += chat.latency_ms

        def finish(
            status: Status,
            *,
            sql: str | None,
            stage: Stage,
            message: str | None,
            error_code: str | None = None,
            error: str | None = None,
            exec_result: ExecResult | None = None,
        ) -> PipelineResult:
            attempt = Attempt(
                n=1,
                sql=sql,
                stage=stage,
                error_code=error_code,
                error=error[:MAX_STORED_ERROR_CHARS] if error else None,
                latency_ms=chat.latency_ms + validation_ms + execution_ms,
                prompt_tokens=chat.prompt_tokens,
                completion_tokens=chat.completion_tokens,
            )
            attempts = (attempt,)
            return PipelineResult(
                status=status,
                sql=sql,
                message=message,
                columns=exec_result.columns if exec_result else (),
                rows=exec_result.rows if exec_result else [],
                truncated=exec_result.truncated if exec_result else False,
                attempts=attempts,
                linking=Linking(mode=cfg.linking_mode, applied=False),
                timings=Timings(
                    pacing_ms=chat.pacing_ms,
                    generation_ms=generation_ms,
                    validation_ms=validation_ms,
                    execution_ms=execution_ms,
                ),
                usage=Usage(
                    model=chat.model,
                    prompt_tokens=_sum_tokens([a.prompt_tokens for a in attempts]),
                    completion_tokens=_sum_tokens([a.completion_tokens for a in attempts]),
                ),
            )

        # Step 6: extract.
        extraction = extract(chat.text, unanswerable_enabled=cfg.unanswerable_enabled)
        if extraction.kind == "cannot_answer":
            return finish(
                "cannot_answer",
                sql=None,
                stage="extract",
                message=f"This can't be answered from this database: {extraction.reason}",
            )
        if extraction.kind == "empty":
            msg = "The reply contained no SQL query."
            return finish(
                "failed", sql=None, stage="extract", message=msg, error_code="EMPTY_SQL", error=msg
            )
        sql = extraction.sql

        # Step 7: validate (before any database contact).
        with Timer() as t:
            verdict = validate(sql, dialect)
        validation_ms += t.elapsed_ms
        if verdict.rejection is not None:
            r = verdict.rejection
            if r.blocking:
                _log.warning("sql_blocked", code=r.code)
                return finish(
                    "blocked",
                    sql=sql,
                    stage="validate",
                    message=f"The generated query was blocked by the safety validator "
                    f"({r.code}: {r.message})",
                    error_code=r.code,
                    error=r.message,
                )
            _log.info("sql_rejected", code=r.code)
            # Step 9 (self-correction) arrives in T24; until then the request ends here.
            return finish(
                "failed",
                sql=sql,
                stage="validate",
                message=r.message,
                error_code=r.code,
                error=r.message,
            )

        # Step 8: execute.
        try:
            with Timer() as t:
                exec_result = await self.executor.execute(
                    sql, cfg.row_limit, cfg.statement_timeout_ms
                )
        except ExecutionError as e:
            execution_ms += t.elapsed_ms
            if not e.retryable:
                _log.error("read_only_violation", code=e.code)
                return finish(
                    "blocked",
                    sql=sql,
                    stage="execute",
                    message=f"The generated query was blocked by the database ({e.code}).",
                    error_code=e.code,
                    error=e.message,
                )
            return finish(
                "failed",
                sql=sql,
                stage="execute",
                message=e.message,
                error_code=e.code,
                error=e.message,
            )
        execution_ms += t.elapsed_ms
        _log.info(
            "sql_executed",
            row_count=exec_result.row_count,
            truncated=exec_result.truncated,
            latency_ms=t.elapsed_ms,
        )

        # Step 12: return (charts T30, summary T31).
        return finish("success", sql=sql, stage="execute", message=None, exec_result=exec_result)
