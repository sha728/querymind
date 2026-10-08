"""Evaluation runner: drives the product pipeline over Spider questions (design §10.2, §10.4).

The pipeline, schema introspection and executor are imported from ``qm_engine``; nothing is
reimplemented here (R9.2). The runner only sets the eval-only configuration (design §6.7),
scores each final prediction with the official matcher, and writes the run directory:

    runs/<UTC timestamp>_<tag>/config.json     every setting needed to reproduce the run
    runs/<UTC timestamp>_<tag>/results.jsonl   one line per question, appended as it finishes
    runs/<UTC timestamp>_<tag>/summary.json    computed from results.jsonl at the end
"""

import asyncio
import json
import os
import platform
import subprocess
from collections import Counter
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit

from qm_engine.config import EngineConfig, LinkingMode
from qm_engine.execution.sqlite import SqliteExecutor
from qm_engine.llm.client import LLMClient, LLMError
from qm_engine.observability import get_logger
from qm_engine.pipeline import Pipeline, PipelineResult
from qm_engine.schema.introspect_sqlite import introspect_sqlite
from qm_engine.schema.models import SchemaSnapshot
from qm_eval.scoring import execution_match, hardness
from qm_eval.spider import (
    SpiderExample,
    Split,
    db_path,
    load_split,
    manifest_hash,
    subset_hash,
    verify_manifest,
)

DEFAULT_RUNS_DIR = Path(__file__).resolve().parents[1] / "runs"
HARDNESS_ORDER = ("easy", "medium", "hard", "extra")

# Rate limits never count as wrong answers (design §10.3): a retry hint up to this many seconds
# is waited out and the same question re-run; anything else interrupts the run (resumable).
MAX_WAIT_S = 60.0
MAX_WAITS_PER_QUESTION = 5

# config.json fields that must be identical for `--resume` to continue a run.
RESUME_KEYS = (
    "split",
    "full",
    "seed",
    "question_id_hash",
    "timeout_ms",
    "flags",
    "llm",
    "git_sha",
    "git_dirty",
    "spider_manifest_hash",
)

_log = get_logger()


@dataclass(frozen=True)
class RunSpec:
    data_root: Path
    manifest: Path
    question_ids: tuple[int, ...]
    full: bool
    seed: int | None
    tag: str
    timeout_ms: int = 30_000  # per query, gold and predicted (design §10.3)
    split: Split = "dev"
    # Techniques are off unless a `qm-eval` option turns them on (design §6.7 v0.6);
    # product defaults from .env never leak into an eval run.
    linking_mode: LinkingMode = "off"
    few_shot: bool = False
    self_correction: bool = False
    max_corrections: int = 2


_DEFAULT_TECHNIQUES = RunSpec(
    data_root=Path(), manifest=Path(), question_ids=(), full=False, seed=None, tag=""
)


class RunInterrupted(Exception):
    """The run stopped on an LLM rate limit or outage; continue later with ``--resume``."""

    def __init__(self, run_dir: Path, question_id: int, error: LLMError) -> None:
        self.run_dir = run_dir
        self.question_id = question_id
        self.error = error
        super().__init__(f"interrupted at question {question_id}: {error}")


class ResumeMismatch(Exception):
    """The current settings differ from the run being resumed."""


class RunLocked(Exception):
    """Another process holds this run folder's lock (design §10.3 v0.6)."""


LOCK_NAME = ".qm-eval.lock"


@contextmanager
def run_lock(run_dir: Path) -> Iterator[None]:
    """One writer per run folder: exclusive-create a lock file, remove it on exit.

    No automatic stale-lock detection: probing a PID with ``os.kill(pid, 0)`` terminates the
    process on Windows. A lock left by a killed process is deleted by hand.
    """
    path = run_dir / LOCK_NAME
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        holder = path.read_text(encoding="utf-8", errors="replace").strip()
        raise RunLocked(
            f"{run_dir} is locked by another qm-eval process ({holder}). "
            f"If no qm-eval is running, delete {path} and retry."
        ) from None
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(f"pid={os.getpid()} host={platform.node()} since={datetime.now(UTC).isoformat()}")
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


@dataclass(frozen=True)
class GitInfo:
    sha: str | None
    dirty: bool


def git_info(repo_dir: Path) -> GitInfo:
    """Current commit and whether the tree has any change (a dirty run is never reportable)."""

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=repo_dir, capture_output=True, text=True, check=True
        ).stdout.strip()

    try:
        return GitInfo(sha=git("rev-parse", "HEAD"), dirty=bool(git("status", "--porcelain")))
    except (OSError, subprocess.CalledProcessError):
        return GitInfo(sha=None, dirty=True)


def eval_config(base: EngineConfig, spec: RunSpec | None = None) -> EngineConfig:
    """The product pipeline with the eval-only settings of design §6.7.

    Technique flags come from the ``RunSpec`` (default: all off), never from ``base``.
    """
    spec_techniques = spec or _DEFAULT_TECHNIQUES
    return EngineConfig.model_validate(
        {
            **base.model_dump(),
            "dialect": "sqlite",
            "row_limit": None,
            "unanswerable_enabled": False,
            "summary_enabled": False,
            "include_date": False,
            "linking_mode": spec_techniques.linking_mode,
            "few_shot_enabled": spec_techniques.few_shot,
            "self_correction_enabled": spec_techniques.self_correction,
            "max_corrections": spec_techniques.max_corrections,
        }
    )


def build_config_record(
    spec: RunSpec, cfg: EngineConfig, git: GitInfo, started_at: datetime
) -> dict[str, object]:
    return {
        "tag": spec.tag,
        "split": spec.split,
        "full": spec.full,
        "seed": spec.seed,
        "n_questions": len(spec.question_ids),
        "question_id_hash": subset_hash(spec.question_ids),
        "question_ids": list(spec.question_ids),
        "timeout_ms": spec.timeout_ms,
        "flags": {
            "linking_mode": cfg.linking_mode,
            "few_shot_enabled": cfg.few_shot_enabled,
            "self_correction_enabled": cfg.self_correction_enabled,
            "max_corrections": cfg.max_corrections,
            "unanswerable_enabled": cfg.unanswerable_enabled,
            "include_date": cfg.include_date,
            "row_limit": cfg.row_limit,
        },
        "llm": {
            "provider": cfg.llm_provider,
            "model": cfg.llm_model,
            "base_url_host": urlsplit(cfg.base_url).netloc,  # never the key
            "reasoning_effort": cfg.llm_reasoning_effort,
            "max_tokens": cfg.llm_max_tokens,
            "min_interval_s": cfg.min_interval_s,
            "temperature": 0,
        },
        "versions": {
            "qm_engine": version("qm-engine"),
            "sqlglot": version("sqlglot"),
            "python": platform.python_version(),
        },
        "git_sha": git.sha,
        "git_dirty": git.dirty,
        "spider_manifest_hash": manifest_hash(spec.manifest),
        "started_at": started_at.isoformat(),
    }


def _safe_hardness(db: Path, gold_sql: str) -> str | None:
    """Official bucket, or None when the official parser cannot parse the gold query."""
    try:
        return hardness(db, gold_sql)
    except Exception as e:  # the vendored parser raises KeyError/AssertionError on bad SQL
        _log.warning("hardness_unavailable", error=f"{type(e).__name__}: {e}")
        return None


def result_record(
    ex: SpiderExample,
    level: str | None,
    res: PipelineResult,
    correct: bool,
    gold_error: str | None,
    linking_recall: float | None = None,
) -> dict[str, object]:
    """One results.jsonl line (R9.3)."""
    last = res.attempts[-1] if res.attempts else None
    usage = res.usage
    return {
        "question_id": ex.question_id,
        "db_id": ex.db_id,
        "hardness": level,
        "question": ex.question,
        "gold_sql": ex.query,
        "pred_sql": res.sql,
        "status": res.status,
        "correct": correct,
        "gold_error": gold_error,
        "error_code": last.error_code if last else None,
        "error": last.error if last else None,
        "attempts": [
            {
                "sql": a.sql,
                "stage": a.stage,
                "error_code": a.error_code,
                "error": a.error,
                "latency_ms": a.latency_ms,
                "prompt_tokens": a.prompt_tokens,
                "completion_tokens": a.completion_tokens,
            }
            for a in res.attempts
        ],
        "n_attempts": len(res.attempts),
        "prompt_tokens": usage.prompt_tokens if usage else None,
        "completion_tokens": usage.completion_tokens if usage else None,
        "linking_applied": res.linking.applied,
        "linked_tables": list(res.linking.tables),
        "linking_recall": linking_recall,
        "few_shot_ids": list(res.few_shot_ids),
        "timings": asdict(res.timings),
    }


def check_resume(original: dict[str, object], current: dict[str, object]) -> None:
    """Raise ``ResumeMismatch`` naming every field that differs from the original run."""
    diffs = [k for k in RESUME_KEYS if original.get(k) != current.get(k)]
    if diffs:
        details = "; ".join(f"{k}: run={original.get(k)!r}, now={current.get(k)!r}" for k in diffs)
        raise ResumeMismatch(f"Cannot resume, settings differ ({', '.join(diffs)}). {details}")


def _append_line(path: Path, record: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_results(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear-interpolation percentile (numpy's default), q in [0, 100]."""
    if not values:
        return None
    xs = sorted(values)
    pos = (len(xs) - 1) * q / 100
    lo, hi = int(pos), min(int(pos) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def _ex(rows: list[dict[str, object]]) -> dict[str, object]:
    scored = [r for r in rows if r["gold_error"] is None]
    n_correct = sum(1 for r in scored if r["correct"])
    return {
        "n": len(rows),
        "n_scored": len(scored),
        "n_correct": n_correct,
        "ex": round(n_correct / len(scored), 4) if scored else None,
    }


def summarize(run_dir: Path, *, ended_at: datetime | None = None) -> dict[str, object]:
    """Compute summary.json from config.json and results.jsonl (never from memory)."""
    config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    rows = read_results(run_dir / "results.jsonl")
    complete = len(rows) == config["n_questions"]

    def tokens(key: str) -> dict[str, object]:
        known = [r[key] for r in rows if isinstance(r[key], int)]
        return {
            "total": sum(known) if known else None,
            "mean_per_question": round(sum(known) / len(known), 1) if known else None,
            "n_missing": len(rows) - len(known),
        }

    # Engine latency per question, excluding free-tier pacing waits (not part of the engine).
    latencies = [
        float(r["timings"]["total_ms"] - r["timings"].get("pacing_ms", 0))  # type: ignore[index,union-attr,operator]
        for r in rows
    ]
    pacing = [float(r["timings"].get("pacing_ms", 0)) for r in rows]  # type: ignore[index,union-attr]
    summary = {
        **_ex(rows),
        "by_hardness": {h: _ex([r for r in rows if r["hardness"] == h]) for h in HARDNESS_ORDER},
        "n_gold_errors": sum(1 for r in rows if r["gold_error"] is not None),
        "status_counts": dict(Counter(str(r["status"]) for r in rows)),
        "prompt_tokens": tokens("prompt_tokens"),
        "completion_tokens": tokens("completion_tokens"),
        "latency_ms": {
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "excludes": "free-tier pacing waits",
        },
        "pacing_ms_total": sum(pacing),
        "complete": complete,
        "reportable": bool(config["full"] and not config["git_dirty"] and complete),
        "started_at": config["started_at"],
        "ended_at": (ended_at or datetime.now(UTC)).isoformat(),
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return summary


class EvalRunner:
    def __init__(
        self,
        spec: RunSpec,
        base_cfg: EngineConfig,
        llm: LLMClient,
        *,
        runs_dir: Path = DEFAULT_RUNS_DIR,
        repo_dir: Path | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.spec = spec
        self.cfg = eval_config(base_cfg, spec)
        self.llm = llm
        self.runs_dir = runs_dir
        self.repo_dir = repo_dir or Path(__file__).resolve().parents[2]
        self._now = now
        self._sleep = sleep
        self._schemas: dict[str, SchemaSnapshot] = {}

    def _schema(self, db_id: str) -> SchemaSnapshot:
        if db_id not in self._schemas:
            self._schemas[db_id] = introspect_sqlite(db_path(self.spec.data_root, db_id))
        return self._schemas[db_id]

    def _config_record(self, started: datetime) -> dict[str, object]:
        return build_config_record(self.spec, self.cfg, git_info(self.repo_dir), started)

    def create_run_dir(self) -> Path:
        started = self._now()
        run_dir = self.runs_dir / f"{started.strftime('%Y%m%dT%H%M%SZ')}_{self.spec.tag}"
        run_dir.mkdir(parents=True, exist_ok=False)
        (run_dir / "config.json").write_text(
            json.dumps(self._config_record(started), indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return run_dir

    def check_resumable(self, run_dir: Path) -> None:
        """Refuse to continue ``run_dir`` unless the current settings match its config.json."""
        config_path = run_dir / "config.json"
        if not config_path.is_file():
            raise ResumeMismatch(f"Not a run directory (no config.json): {run_dir}")
        original = json.loads(config_path.read_text(encoding="utf-8"))
        check_resume(original, self._config_record(self._now()))

    async def _run_question(
        self, pipeline: Pipeline, ex: SpiderExample, run_dir: Path
    ) -> PipelineResult:
        """Run one question, waiting out short rate limits (design §10.3)."""
        waits = 0
        while True:
            try:
                return await pipeline.run(ex.question, self._schema(ex.db_id))
            except LLMError as e:
                hint = e.retry_after_s
                short = e.code == "LLM_RATE_LIMITED" and hint is not None and hint <= MAX_WAIT_S
                if short and hint is not None and waits < MAX_WAITS_PER_QUESTION:
                    waits += 1
                    _log.warning("eval_rate_limited_wait", question_id=ex.question_id, wait_s=hint)
                    await self._sleep(hint)
                    continue
                raise RunInterrupted(run_dir, ex.question_id, e) from e

    async def run(self, run_dir: Path | None = None) -> Path:
        """Run every question not yet in results.jsonl, then write summary.json."""
        spec = self.spec
        verify_manifest(spec.data_root, spec.manifest)
        run_dir = run_dir or self.create_run_dir()
        with run_lock(run_dir):
            await self._run_locked(run_dir)
        summarize(run_dir, ended_at=self._now())
        return run_dir

    async def _run_locked(self, run_dir: Path) -> None:
        spec = self.spec
        results_path = run_dir / "results.jsonl"
        done = {int(r["question_id"]) for r in read_results(results_path)}  # type: ignore[call-overload]

        examples = {ex.question_id: ex for ex in load_split(spec.data_root, spec.split)}
        todo = [examples[q] for q in spec.question_ids if q not in done]
        _log.info("eval_start", run_dir=str(run_dir), todo=len(todo), done=len(done))

        for i, ex in enumerate(todo, 1):
            db = db_path(spec.data_root, ex.db_id)
            pipeline = Pipeline(self.cfg, self.llm, SqliteExecutor(db))
            res = await self._run_question(pipeline, ex, run_dir)
            pred = res.sql if res.status == "success" else None
            score = await asyncio.to_thread(
                execution_match, db, pred, ex.query, spec.timeout_ms / 1000
            )
            level = await asyncio.to_thread(_safe_hardness, db, ex.query)
            _append_line(
                results_path, result_record(ex, level, res, score.correct, score.gold_error)
            )
            _log.info(
                "eval_question",
                n=len(done) + i,
                of=len(spec.question_ids),
                question_id=ex.question_id,
                status=res.status,
                correct=score.correct,
            )
