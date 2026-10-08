import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

import qm_engine.pipeline
from fakes import ScriptedLLM
from qm_engine.config import EngineConfig
from qm_engine.llm.client import LLMError
from qm_eval import runner
from qm_eval.runner import (
    EvalRunner,
    RunInterrupted,
    RunSpec,
    eval_config,
    git_info,
    percentile,
    read_results,
)
from qm_eval.spider import subset_hash, write_manifest

FIXTURE = Path(__file__).parent / "fixtures" / "spider_mini"
NOW = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)

REPLIES = [
    "```sql\nSELECT count(*) FROM customers\n```",  # q0: correct
    "```sql\nSELECT name FROM products ORDER BY price ASC\n```",  # q1: wrong order -> incorrect
    "```sql\nDROP TABLE orders\n```",  # q2: blocked -> incorrect
]

R93_FIELDS = {
    "question_id",
    "db_id",
    "hardness",
    "question",
    "gold_sql",
    "pred_sql",
    "status",
    "correct",
    "gold_error",
    "error_code",
    "error",
    "attempts",
    "n_attempts",
    "prompt_tokens",
    "completion_tokens",
    "linking_applied",
    "linked_tables",
    "n_schema_tables",
    "linking_recall",
    "few_shot_ids",
    "timings",
}


@pytest.fixture
def spec(tmp_path: Path) -> RunSpec:
    root = tmp_path / "spider"
    shutil.copytree(FIXTURE, root)
    manifest = tmp_path / "manifest.sha256"
    write_manifest(root, manifest)
    return RunSpec(
        data_root=root,
        manifest=manifest,
        question_ids=(0, 1, 2),
        full=False,
        seed=42,
        tag="smoke",
        timeout_ms=5000,
    )


def base_cfg() -> EngineConfig:
    # A product-style config: the runner must override the eval-only settings itself.
    return EngineConfig(
        _env_file=None,  # type: ignore[call-arg]
        cerebras_api_key="csk-SECRET-123",
        self_correction_enabled=False,
        linking_mode="off",
        few_shot_enabled=False,
    )


def make_runner(spec: RunSpec, llm: ScriptedLLM, tmp_path: Path) -> EvalRunner:
    return EvalRunner(spec, base_cfg(), llm, runs_dir=tmp_path / "runs", now=lambda: NOW)


async def test_run_writes_config_results_and_summary(spec: RunSpec, tmp_path: Path) -> None:
    llm = ScriptedLLM(*REPLIES, prompt_tokens=200, completion_tokens=30)
    run_dir = await make_runner(spec, llm, tmp_path).run()

    assert run_dir.name == "20261008T120000Z_smoke"

    config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    assert config["n_questions"] == 3
    assert config["question_id_hash"] == subset_hash([0, 1, 2])
    assert config["seed"] == 42 and config["full"] is False
    assert config["flags"]["row_limit"] is None
    assert config["flags"]["unanswerable_enabled"] is False
    assert config["llm"] == {
        "provider": "cerebras",
        "model": "gpt-oss-120b",
        "base_url_host": "api.cerebras.ai",
        "reasoning_effort": "low",
        "max_tokens": 4096,
        "min_interval_s": 12.0,
        "temperature": 0,
    }
    assert set(config["versions"]) == {"qm_engine", "sqlglot", "python"}
    assert "git_sha" in config and isinstance(config["git_dirty"], bool)
    assert len(config["spider_manifest_hash"]) == 64
    assert "csk-SECRET-123" not in (run_dir / "config.json").read_text(encoding="utf-8")

    rows = read_results(run_dir / "results.jsonl")
    assert [r["question_id"] for r in rows] == [0, 1, 2]
    for r in rows:
        assert set(r) == R93_FIELDS
    assert [r["status"] for r in rows] == ["success", "success", "blocked"]
    assert [r["correct"] for r in rows] == [True, False, False]
    assert rows[0]["pred_sql"] == "SELECT count(*) FROM customers"
    assert rows[0]["hardness"] == "easy"
    assert rows[2]["error_code"] == "FORBIDDEN_STATEMENT"
    assert rows[0]["attempts"][0]["prompt_tokens"] == 200
    assert rows[0]["linking_recall"] is None  # added in T22

    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert (summary["n"], summary["n_scored"], summary["n_correct"]) == (3, 3, 1)
    assert summary["ex"] == 0.3333
    assert summary["by_hardness"]["easy"]["n_correct"] == 1
    assert summary["n_gold_errors"] == 0
    assert summary["status_counts"] == {"success": 2, "blocked": 1}
    assert summary["prompt_tokens"] == {"total": 600, "mean_per_question": 200.0, "n_missing": 0}
    assert summary["complete"] is True
    assert summary["reportable"] is False  # subset run


async def test_eval_prompts_use_sqlite_and_no_date(spec: RunSpec, tmp_path: Path) -> None:
    llm = ScriptedLLM(*REPLIES)
    await make_runner(spec, llm, tmp_path).run()
    system = llm.calls[0][0]["content"]
    assert system.startswith("You are an expert SQLite SQL writer.")
    assert "Today's date" not in system
    assert "CANNOT_ANSWER" not in system


async def test_interrupted_run_leaves_valid_lines_and_no_summary(
    spec: RunSpec, tmp_path: Path
) -> None:
    llm = ScriptedLLM(REPLIES[0], REPLIES[1], LLMError("LLM_RATE_LIMITED", "quota"))
    r = make_runner(spec, llm, tmp_path)
    run_dir = r.create_run_dir()
    with pytest.raises(RunInterrupted) as err:
        await r.run(run_dir)
    assert err.value.question_id == 2 and err.value.run_dir == run_dir
    lines = (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["question_id"] for line in lines] == [0, 1]
    assert not (run_dir / "summary.json").exists()


async def test_gold_error_is_excluded_from_ex(spec: RunSpec, tmp_path: Path) -> None:
    dev = spec.data_root / "dev.json"
    items = json.loads(dev.read_text(encoding="utf-8"))
    items[1]["query"] = "SELECT * FROM no_such_table"
    dev.write_text(json.dumps(items), encoding="utf-8")
    write_manifest(spec.data_root, spec.manifest)

    run_dir = await make_runner(spec, ScriptedLLM(*REPLIES), tmp_path).run()
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["n_gold_errors"] == 1
    assert (summary["n_scored"], summary["n_correct"], summary["ex"]) == (2, 1, 0.5)
    rows = read_results(run_dir / "results.jsonl")
    assert rows[1]["hardness"] is None and rows[1]["gold_error"] is not None


async def test_manifest_mismatch_refuses_to_run(spec: RunSpec, tmp_path: Path) -> None:
    (spec.data_root / "tables.json").write_text("[1]", encoding="utf-8")
    llm = ScriptedLLM(*REPLIES)
    with pytest.raises(Exception, match="manifest"):
        await make_runner(spec, llm, tmp_path).run()
    assert llm.calls == []


def test_runner_uses_the_product_pipeline() -> None:
    assert runner.Pipeline is qm_engine.pipeline.Pipeline  # R9.2: same code, not a copy


def test_eval_config_overrides_product_settings() -> None:
    product = EngineConfig(
        _env_file=None,  # type: ignore[call-arg]
        cerebras_api_key="k",
        dialect="postgres",
        row_limit=1000,
        include_date=True,
        unanswerable_enabled=True,
        summary_enabled=True,
    )
    cfg = eval_config(product)
    assert (cfg.dialect, cfg.row_limit) == ("sqlite", None)
    assert not (cfg.include_date or cfg.unanswerable_enabled or cfg.summary_enabled)
    assert cfg.api_key is not None and cfg.api_key.get_secret_value() == "k"


def test_git_info_outside_a_repo_is_dirty(tmp_path: Path) -> None:
    info = git_info(tmp_path)
    assert info.sha is None and info.dirty is True


def test_git_info_in_this_repo() -> None:
    info = git_info(Path(__file__).resolve().parents[3])
    assert info.sha is not None and len(info.sha) == 40


def test_percentile_matches_linear_interpolation() -> None:
    assert percentile([], 50) is None
    assert percentile([10.0], 95) == 10.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5
    assert percentile([float(x) for x in range(1, 101)], 95) == pytest.approx(95.05)


def test_product_technique_defaults_do_not_leak_into_eval(tmp_path: Path) -> None:
    """Design §6.7 v0.6: techniques are off unless the RunSpec turns them on."""
    product = EngineConfig(
        _env_file=None,  # type: ignore[call-arg]
        cerebras_api_key="k",
        linking_mode="auto",
        few_shot_enabled=True,
        self_correction_enabled=True,
        max_corrections=5,
    )
    cfg = eval_config(product)
    assert cfg.linking_mode == "off"
    assert not cfg.few_shot_enabled and not cfg.self_correction_enabled
    assert cfg.max_corrections == 2


async def test_config_json_records_effective_technique_flags(spec: RunSpec, tmp_path: Path) -> None:
    product = EngineConfig(
        _env_file=None,  # type: ignore[call-arg]
        cerebras_api_key="k",
        linking_mode="auto",
        few_shot_enabled=True,
        self_correction_enabled=True,
    )
    r = EvalRunner(
        spec, product, ScriptedLLM(*REPLIES), runs_dir=tmp_path / "runs", now=lambda: NOW
    )
    run_dir = await r.run()
    flags = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))["flags"]
    assert (flags["linking_mode"], flags["few_shot_enabled"], flags["self_correction_enabled"]) == (
        "off",
        False,
        False,
    )


def test_run_spec_turns_techniques_on(spec: RunSpec) -> None:
    from dataclasses import replace

    on = replace(spec, linking_mode="on", few_shot=True, self_correction=True, max_corrections=1)
    cfg = eval_config(base_cfg(), on)
    assert (cfg.linking_mode, cfg.few_shot_enabled, cfg.self_correction_enabled) == (
        "on",
        True,
        True,
    )
    assert cfg.max_corrections == 1


async def test_resume_refuses_changed_technique_flag(spec: RunSpec, tmp_path: Path) -> None:
    from dataclasses import replace

    from qm_eval.runner import ResumeMismatch

    first = make_runner(spec, ScriptedLLM(), tmp_path)
    run_dir = first.create_run_dir()
    changed = EvalRunner(
        replace(spec, self_correction=True),
        base_cfg(),
        ScriptedLLM(),
        runs_dir=tmp_path / "runs",
        now=lambda: NOW,
    )
    with pytest.raises(ResumeMismatch, match="flags"):
        changed.check_resumable(run_dir)


# --- one writer per run folder (design §10.3 v0.6) ---


async def test_locked_run_dir_is_refused_without_writing(spec: RunSpec, tmp_path: Path) -> None:
    from qm_eval.runner import LOCK_NAME, RunLocked

    llm = ScriptedLLM(*REPLIES)
    r = make_runner(spec, llm, tmp_path)
    run_dir = r.create_run_dir()
    (run_dir / LOCK_NAME).write_text("pid=1234 host=other", encoding="utf-8")
    with pytest.raises(RunLocked, match="delete"):
        await r.run(run_dir)
    assert not (run_dir / "results.jsonl").exists()
    assert llm.calls == []
    assert (run_dir / LOCK_NAME).exists()  # someone else's lock is never removed


async def test_lock_is_released_after_finishing(spec: RunSpec, tmp_path: Path) -> None:
    from qm_eval.runner import LOCK_NAME

    run_dir = await make_runner(spec, ScriptedLLM(*REPLIES), tmp_path).run()
    assert not (run_dir / LOCK_NAME).exists()


async def test_lock_is_released_after_interruption(spec: RunSpec, tmp_path: Path) -> None:
    from qm_eval.runner import LOCK_NAME

    llm = ScriptedLLM(REPLIES[0], LLMError("LLM_RATE_LIMITED", "quota"))
    r = make_runner(spec, llm, tmp_path)
    run_dir = r.create_run_dir()
    with pytest.raises(RunInterrupted):
        await r.run(run_dir)
    assert not (run_dir / LOCK_NAME).exists()


# --- linking recall (design §6.3 step 7) ---


def test_gold_tables_excludes_ctes_and_lowercases() -> None:
    from qm_eval.runner import gold_tables

    assert gold_tables("SELECT T1.name FROM Singer AS T1 JOIN concert AS T2 ON 1") == {
        "singer",
        "concert",
    }
    assert gold_tables("WITH x AS (SELECT * FROM a) SELECT * FROM x JOIN b") == {"a", "b"}
    assert gold_tables("SELECT FROM WHERE (") is None


def test_linking_recall() -> None:
    from qm_eval.runner import linking_recall

    gold = "SELECT * FROM singer JOIN concert ON 1"
    assert linking_recall(gold, ["Singer", "concert", "stadium"]) == 1.0
    assert linking_recall(gold, ["singer"]) == 0.5
    assert linking_recall(gold, []) == 0.0
    assert linking_recall("SELECT 1", ["singer"]) is None  # no gold tables


# --- linking top-k and summary statistics (T22a, design §10.5 v0.7) ---


def test_linking_top_k_comes_from_run_spec_only(spec: RunSpec) -> None:
    from dataclasses import replace

    product = EngineConfig(_env_file=None, cerebras_api_key="k", linking_top_k=9)  # type: ignore[call-arg]
    assert eval_config(product, spec).linking_top_k == 5  # design default, not .env
    assert eval_config(product, replace(spec, linking_top_k=3)).linking_top_k == 3


async def test_resume_refuses_changed_linking_top_k(spec: RunSpec, tmp_path: Path) -> None:
    from dataclasses import replace

    from qm_eval.runner import ResumeMismatch

    on = replace(spec, linking_mode="on", linking_top_k=3)
    run_dir = make_runner(on, ScriptedLLM(), tmp_path).create_run_dir()
    changed = EvalRunner(
        replace(on, linking_top_k=5), base_cfg(), ScriptedLLM(), runs_dir=tmp_path / "runs",
        now=lambda: NOW,
    )  # fmt: skip
    with pytest.raises(ResumeMismatch, match="flags"):
        changed.check_resumable(run_dir)


def test_linking_stats() -> None:
    from qm_eval.runner import _linking_stats

    def row(linked: int, total: int, recall: float | None) -> dict[str, object]:
        return {
            "linking_applied": True,
            "linked_tables": ["t"] * linked,
            "n_schema_tables": total,
            "linking_recall": recall,
        }

    rows = [row(3, 6, 1.0), row(4, 4, 1.0), row(2, 5, 0.5), row(1, 3, None)]
    assert _linking_stats(rows) == {
        "n_applied": 4,
        "n_removed_any_table": 3,
        "share_removed_any_table": 0.75,
        "mean_linking_recall": 0.8333,
        "share_recall_1": 0.6667,
    }
    assert _linking_stats([{"linking_applied": False}]) is None
