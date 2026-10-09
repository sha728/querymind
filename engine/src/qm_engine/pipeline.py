"""The single orchestration path shared by the product and the evaluation harness (design §6).

Steps 1-9 and 12 of design §6.2: input checks, schema linking (T22), few-shot selection (T23),
prompt, generation, extraction, validation, execution, self-correction (T24) and the chart
recommendation (step 10, T30). Summaries (step 11, T31) are added later.

LLM transport errors (``LLMError``) are not turned into a status: they propagate so the API can
return 503 and the harness can apply its rate-limit rules (design §10.3, §12).
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Literal

from qm_engine.charts import ChartSpec, recommend_chart
from qm_engine.config import EngineConfig
from qm_engine.execution.base import ExecResult, ExecutionError, Executor, ResultColumn
from qm_engine.extract import extract
from qm_engine.fewshot import FewShotSelector
from qm_engine.linking import SchemaLinker
from qm_engine.llm.client import ChatResult, LLMClient
from qm_engine.llm.embeddings import Embedder
from qm_engine.observability import Timer, get_logger
from qm_engine.prompts import Example, build_messages, correction_turns
from qm_engine.safety.validator import validate
from qm_engine.schema.models import Dialect, SchemaSnapshot
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
    chart: ChartSpec | None = None  # set on success (design §9)

    @property
    def row_count(self) -> int:
        return len(self.rows)


@dataclass(frozen=True)
class _Outcome:
    """How one model reply ended (steps 6-8)."""

    kind: Literal["success", "retryable", "blocked", "cannot_answer"]
    sql: str | None
    stage: Stage
    message: str | None
    error_code: str | None = None
    error: str | None = None
    exec_result: ExecResult | None = None
    validation_ms: int = 0
    execution_ms: int = 0


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
        embedder: Embedder | None = None,
        fewshot: FewShotSelector | None = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.cfg = cfg
        self.llm = llm
        self.executor = executor
        self.linker = SchemaLinker(cfg, embedder)
        self.fewshot = fewshot
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

        # Step 2: schema linking (design §6.3).
        with Timer() as t_link:
            linking = await self.linker.link(question, schema)

        # Step 3: few-shot examples by similarity (design §6.4).
        examples: Sequence[Example] = ()
        with Timer() as t_fewshot:
            if cfg.few_shot_enabled:
                if self.fewshot is None:
                    raise ValueError("few-shot is enabled but no example pool was given")
                examples = await self.fewshot.select(question, cfg.few_shot_k)

        # Step 4: prompt with the linked (or full) schema and the examples.
        messages = build_messages(
            dialect=dialect,
            schema_text=serialize_schema(schema, linking.tables if linking.applied else None),
            examples=examples,
            question=question,
            unanswerable=cfg.unanswerable_enabled,
            today=self._today() if cfg.include_date else None,
        )

        # Steps 5-9: generate, extract, validate, execute; on a retryable failure append the
        # correction turns (design §8.2) and try again, up to max_corrections times.
        max_generations = 1 + (cfg.max_corrections if cfg.self_correction_enabled else 0)
        attempts: list[Attempt] = []
        totals = {"generation": 0, "validation": 0, "execution": 0, "pacing": 0}
        model = cfg.llm_model

        def finish(
            status: Status,
            *,
            sql: str | None,
            message: str | None,
            exec_result: ExecResult | None = None,
        ) -> PipelineResult:
            chart = (
                recommend_chart(exec_result.columns, exec_result.rows)
                if exec_result is not None
                else None
            )
            return PipelineResult(
                chart=chart,
                status=status,
                sql=sql,
                message=message,
                columns=exec_result.columns if exec_result else (),
                rows=exec_result.rows if exec_result else [],
                truncated=exec_result.truncated if exec_result else False,
                attempts=tuple(attempts),
                linking=Linking(mode=linking.mode, applied=linking.applied, tables=linking.tables),
                few_shot_ids=tuple(e.id for e in examples),
                timings=Timings(
                    linking_ms=t_link.elapsed_ms,
                    fewshot_ms=t_fewshot.elapsed_ms,
                    pacing_ms=totals["pacing"],
                    generation_ms=totals["generation"],
                    validation_ms=totals["validation"],
                    execution_ms=totals["execution"],
                ),
                usage=Usage(
                    model=model,
                    prompt_tokens=_sum_tokens([a.prompt_tokens for a in attempts]),
                    completion_tokens=_sum_tokens([a.completion_tokens for a in attempts]),
                ),
            )

        for n in range(1, max_generations + 1):
            chat: ChatResult = await self.llm.chat(
                messages, purpose="generation" if n == 1 else "correction"
            )
            model = chat.model
            totals["generation"] += chat.latency_ms
            totals["pacing"] += chat.pacing_ms
            outcome = await self._attempt(chat.text, dialect)
            totals["validation"] += outcome.validation_ms
            totals["execution"] += outcome.execution_ms
            attempts.append(
                Attempt(
                    n=n,
                    sql=outcome.sql,
                    stage=outcome.stage,
                    error_code=outcome.error_code,
                    error=outcome.error[:MAX_STORED_ERROR_CHARS] if outcome.error else None,
                    latency_ms=chat.latency_ms + outcome.validation_ms + outcome.execution_ms,
                    prompt_tokens=chat.prompt_tokens,
                    completion_tokens=chat.completion_tokens,
                )
            )

            if outcome.kind == "success":
                # Step 10 (chart) runs in finish(); step 11 (summary) arrives in T31.
                return finish(
                    "success", sql=outcome.sql, message=None, exec_result=outcome.exec_result
                )
            if outcome.kind == "cannot_answer":
                return finish("cannot_answer", sql=None, message=outcome.message)
            if outcome.kind == "blocked":
                return finish("blocked", sql=outcome.sql, message=outcome.message)

            # Retryable failure (design D7): correct within the same conversation, if allowed.
            if n < max_generations:
                _log.info("correction_attempt", n=n + 1, error_code=outcome.error_code)
                messages = messages + correction_turns(
                    chat.text, outcome.error_code or "ERROR", outcome.error or ""
                )
                continue
            return finish("failed", sql=outcome.sql, message=outcome.message)

        raise AssertionError("unreachable: the loop always returns")  # pragma: no cover

    async def _attempt(self, reply: str, dialect: Dialect) -> _Outcome:
        """Steps 6-8 for one model reply."""
        cfg = self.cfg

        # Step 6: extract.
        extraction = extract(reply, unanswerable_enabled=cfg.unanswerable_enabled)
        if extraction.kind == "cannot_answer":
            return _Outcome(
                "cannot_answer",
                sql=None,
                stage="extract",
                message=f"This can't be answered from this database: {extraction.reason}",
            )
        if extraction.kind == "empty":
            msg = "The reply contained no SQL query."
            return _Outcome(
                "retryable",
                sql=None,
                stage="extract",
                message=msg,
                error_code="EMPTY_SQL",
                error=msg,
            )
        sql = extraction.sql

        # Step 7: validate (before any database contact).
        with Timer() as t_val:
            verdict = validate(sql, dialect)
        if verdict.rejection is not None:
            r = verdict.rejection
            if r.blocking:
                _log.warning("sql_blocked", code=r.code)
                return _Outcome(
                    "blocked",
                    sql=sql,
                    stage="validate",
                    message=f"The generated query was blocked by the safety validator "
                    f"({r.code}: {r.message})",
                    error_code=r.code,
                    error=r.message,
                    validation_ms=t_val.elapsed_ms,
                )
            _log.info("sql_rejected", code=r.code)
            return _Outcome(
                "retryable",
                sql=sql,
                stage="validate",
                message=r.message,
                error_code=r.code,
                error=r.message,
                validation_ms=t_val.elapsed_ms,
            )

        # Step 8: execute.
        try:
            with Timer() as t_exec:
                exec_result = await self.executor.execute(
                    sql, cfg.row_limit, cfg.statement_timeout_ms
                )
        except ExecutionError as e:
            if not e.retryable:
                _log.error("read_only_violation", code=e.code)
                return _Outcome(
                    "blocked",
                    sql=sql,
                    stage="execute",
                    message=f"The generated query was blocked by the database ({e.code}).",
                    error_code=e.code,
                    error=e.message,
                    validation_ms=t_val.elapsed_ms,
                    execution_ms=t_exec.elapsed_ms,
                )
            return _Outcome(
                "retryable",
                sql=sql,
                stage="execute",
                message=e.message,
                error_code=e.code,
                error=e.message,
                validation_ms=t_val.elapsed_ms,
                execution_ms=t_exec.elapsed_ms,
            )
        _log.info(
            "sql_executed",
            row_count=exec_result.row_count,
            truncated=exec_result.truncated,
            latency_ms=t_exec.elapsed_ms,
        )
        return _Outcome(
            "success",
            sql=sql,
            stage="execute",
            message=None,
            exec_result=exec_result,
            validation_ms=t_val.elapsed_ms,
            execution_ms=t_exec.elapsed_ms,
        )
