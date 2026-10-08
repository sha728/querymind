import json
import shutil
from importlib.metadata import entry_points
from pathlib import Path

import pytest

from fakes import FakeEmbedder, ScriptedLLM
from qm_engine.config import EngineConfig
from qm_engine.llm.client import LLMError
from qm_eval import cli, runner
from qm_eval.runner import GitInfo, read_results
from qm_eval.spider import write_manifest

FIXTURE = Path(__file__).parent / "fixtures" / "spider_mini"
REPLY = "```sql\nSELECT count(*) FROM customers\n```"


class Env:
    """A fixture copy plus the knobs the CLI reads (config and LLM factories)."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = tmp_path / "spider"
        shutil.copytree(FIXTURE, self.root)
        self.manifest = tmp_path / "manifest.sha256"
        write_manifest(self.root, self.manifest)
        self.runs = tmp_path / "runs"
        self.cfg_overrides: dict[str, object] = {}
        self.llm = ScriptedLLM()
        monkeypatch.setattr(cli, "make_config", self._config)
        monkeypatch.setattr(cli, "make_llm", lambda cfg: self.llm)
        self.embedder = FakeEmbedder()
        self.embedder_created = 0

        def make_embedder(cfg: EngineConfig) -> FakeEmbedder:
            self.embedder_created += 1
            return self.embedder

        monkeypatch.setattr(cli, "make_embedder", make_embedder)

    def _config(self) -> EngineConfig:
        base: dict[str, object] = {
            "cerebras_api_key": "k",
            "cache_dir": self.root.parent / "cache",
            "self_correction_enabled": False,
            "linking_mode": "off",
            "few_shot_enabled": False,
        }
        return EngineConfig(_env_file=None, **(base | self.cfg_overrides))  # type: ignore[arg-type]

    def args(self, *extra: str) -> list[str]:
        return [
            "run",
            *extra,
            "--data-root",
            str(self.root),
            "--manifest",
            str(self.manifest),
            "--runs-dir",
            str(self.runs),
            "--tag",
            "t",
        ]

    def run_dir(self) -> Path:
        (only,) = list(self.runs.iterdir())
        return only


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    return Env(tmp_path, monkeypatch)


def test_entry_point_is_declared() -> None:
    (ep,) = [e for e in entry_points(group="console_scripts") if e.name == "qm-eval"]
    assert ep.load() is cli.main


def test_cli_smoke_run(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    env.llm = ScriptedLLM(REPLY, REPLY, REPLY)
    assert cli.main(env.args("--subset", "3", "--seed", "42")) == 0

    run_dir = env.run_dir()
    config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    assert config["n_questions"] == 3 and config["seed"] == 42
    assert len(config["question_ids"]) == 3
    assert len(read_results(run_dir / "results.jsonl")) == 3
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["complete"] is True
    assert "EX " in capsys.readouterr().out


def test_full_run_uses_every_question(env: Env) -> None:
    env.llm = ScriptedLLM(*[REPLY] * 5)
    assert cli.main(env.args("--full")) == 0
    config = json.loads((env.run_dir() / "config.json").read_text(encoding="utf-8"))
    assert config["full"] is True and config["seed"] is None
    assert config["question_ids"] == [0, 1, 2, 3, 4]


def test_resume_skips_done_and_appends_rest(env: Env) -> None:
    env.llm = ScriptedLLM(REPLY, LLMError("LLM_RATE_LIMITED", "daily quota", retry_after_s=3600))
    assert cli.main(env.args("--subset", "3")) == 75
    run_dir = env.run_dir()
    assert len(read_results(run_dir / "results.jsonl")) == 1

    env.llm = ScriptedLLM(REPLY, REPLY)
    assert cli.main(env.args("--subset", "3", "--resume", str(run_dir))) == 0
    rows = read_results(run_dir / "results.jsonl")
    assert len(rows) == 3
    assert [r["question_id"] for r in rows] == sorted(r["question_id"] for r in rows)
    assert len(env.llm.calls) == 2  # only the two remaining questions were asked


# --- resume refuses on any mismatch (one test per category) ---


def _start_run(env: Env) -> Path:
    env.llm = ScriptedLLM(REPLY, LLMError("LLM_RATE_LIMITED", "daily quota", retry_after_s=3600))
    assert cli.main(env.args("--subset", "3")) == 75
    env.llm = ScriptedLLM(REPLY, REPLY)
    return env.run_dir()


def _assert_refused(env: Env, run_dir: Path, capsys: pytest.CaptureFixture[str], field: str,
                    *extra: str) -> None:  # fmt: skip
    capsys.readouterr()
    assert cli.main(env.args("--subset", "3", *extra, "--resume", str(run_dir))) == 1
    err = capsys.readouterr().err
    assert "Cannot resume" in err and field in err
    assert len(read_results(run_dir / "results.jsonl")) == 1  # nothing appended
    assert env.llm.calls == []


def test_resume_refuses_other_git_sha(
    env: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = _start_run(env)
    monkeypatch.setattr(runner, "git_info", lambda _: GitInfo(sha="0" * 40, dirty=False))
    _assert_refused(env, run_dir, capsys, "git_sha")


def test_env_technique_defaults_do_not_affect_resume(env: Env) -> None:
    # Design §6.7 v0.6: .env technique values never reach an eval run, so changing them
    # neither changes the recorded flags nor blocks --resume. Flag changes made through
    # RunSpec are refused (see test_runner.test_resume_refuses_changed_technique_flag).
    run_dir = _start_run(env)
    env.cfg_overrides = {"max_corrections": 1, "few_shot_enabled": True}
    assert cli.main(env.args("--subset", "3", "--resume", str(run_dir))) == 0


def test_resume_refuses_changed_model_or_provider(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = _start_run(env)
    env.cfg_overrides = {"llm_model": "qwen-3.8-27b"}
    _assert_refused(env, run_dir, capsys, "llm")
    env.cfg_overrides = {"llm_provider": "groq", "groq_api_key": "g"}
    _assert_refused(env, run_dir, capsys, "llm")


def test_resume_refuses_changed_subset(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = _start_run(env)
    _assert_refused(env, run_dir, capsys, "question_id_hash", "--seed", "7")


def test_resume_refuses_non_run_directory(
    env: Env, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env.llm = ScriptedLLM()
    assert cli.main(env.args("--subset", "3", "--resume", str(tmp_path))) == 1
    assert "no config.json" in capsys.readouterr().err


# --- rate limits never count as wrong answers ---


def test_short_rate_limit_is_waited_out_and_question_rerun(env: Env) -> None:
    short = LLMError("LLM_RATE_LIMITED", "per-minute limit", retry_after_s=0.01)
    env.llm = ScriptedLLM(REPLY, short, REPLY, REPLY)
    assert cli.main(env.args("--subset", "3")) == 0
    rows = read_results(env.run_dir() / "results.jsonl")
    assert len(rows) == 3
    assert all(r["status"] == "success" for r in rows)
    assert len(env.llm.calls) == 4  # the limited question was asked again


@pytest.mark.parametrize(
    "error",
    [
        LLMError("LLM_RATE_LIMITED", "daily quota", retry_after_s=3600),
        LLMError("LLM_RATE_LIMITED", "limit, no hint"),
        LLMError("LLM_UNAVAILABLE", "provider error 503"),
    ],
    ids=["long-hint", "no-hint", "outage"],
)
def test_long_limit_or_outage_interrupts_without_recording(
    env: Env, error: LLMError, capsys: pytest.CaptureFixture[str]
) -> None:
    env.llm = ScriptedLLM(REPLY, error)
    assert cli.main(env.args("--subset", "3")) == 75
    run_dir = env.run_dir()
    rows = read_results(run_dir / "results.jsonl")
    assert len(rows) == 1  # the interrupted question is NOT recorded as wrong
    assert not (run_dir / "summary.json").exists()
    assert f"--resume {run_dir}" in capsys.readouterr().err


def test_manifest_mismatch_exits_1(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    (env.root / "tables.json").write_text("[2]", encoding="utf-8")
    assert cli.main(env.args("--subset", "3")) == 1
    assert "Refused" in capsys.readouterr().err


def test_bad_subset_size_is_a_usage_error(env: Env) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(env.args("--subset", "0"))
    assert exit_info.value.code == 2
    assert cli.main(env.args("--subset", "99")) == 2  # larger than the split


def test_second_process_on_same_run_is_refused(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    from qm_eval.runner import LOCK_NAME

    run_dir = _start_run(env)
    (run_dir / LOCK_NAME).write_text("pid=999 host=elsewhere", encoding="utf-8")
    capsys.readouterr()
    assert cli.main(env.args("--subset", "3", "--resume", str(run_dir))) == 1
    err = capsys.readouterr().err
    assert "locked by another qm-eval" in err and LOCK_NAME in err
    assert len(read_results(run_dir / "results.jsonl")) == 1


# --- schema linking (T22) ---


def test_linking_on_records_tables_and_recall(env: Env) -> None:
    env.llm = ScriptedLLM(REPLY, REPLY, REPLY)
    assert cli.main(env.args("--subset", "3", "--linking", "on")) == 0
    run_dir = env.run_dir()
    config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    assert config["flags"]["linking_mode"] == "on"
    assert config["embedding"]["used"] is True
    rows = read_results(run_dir / "results.jsonl")
    for r in rows:
        assert r["linking_applied"] is True
        assert r["linked_tables"]
        gold = runner.gold_tables(r["gold_sql"])
        assert gold is not None
        linked = {t.lower() for t in r["linked_tables"]}
        assert r["linking_recall"] == round(len(gold & linked) / len(gold), 4)
    assert env.embedder_created == 1


def test_linking_defaults_off_and_needs_no_embedder(env: Env) -> None:
    env.cfg_overrides = {"linking_mode": "auto"}  # a product default must not apply
    env.llm = ScriptedLLM(REPLY, REPLY, REPLY)
    assert cli.main(env.args("--subset", "3")) == 0
    config = json.loads((env.run_dir() / "config.json").read_text(encoding="utf-8"))
    assert config["flags"]["linking_mode"] == "off"
    assert config["embedding"]["used"] is False
    rows = read_results(env.run_dir() / "results.jsonl")
    assert all(r["linking_applied"] is False and r["linking_recall"] is None for r in rows)
    assert env.embedder_created == 0


def test_linking_top_k_option_recorded_and_summarized(env: Env) -> None:
    env.llm = ScriptedLLM(REPLY, REPLY, REPLY)
    assert cli.main(env.args("--subset", "3", "--linking", "on", "--linking-top-k", "2")) == 0
    run_dir = env.run_dir()
    config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    assert config["flags"]["linking_top_k"] == 2
    rows = read_results(run_dir / "results.jsonl")
    assert all(r["n_schema_tables"] == 6 for r in rows)  # the fixture database has 6 tables
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    stats = summary["linking"]
    assert stats["n_applied"] == 3
    removed = sum(1 for r in rows if len(r["linked_tables"]) < 6)
    assert stats["n_removed_any_table"] == removed
    recalls = [r["linking_recall"] for r in rows]
    assert stats["mean_linking_recall"] == round(sum(recalls) / len(recalls), 4)


def test_linking_off_summary_has_no_linking_stats(env: Env) -> None:
    env.llm = ScriptedLLM(REPLY, REPLY, REPLY)
    assert cli.main(env.args("--subset", "3")) == 0
    summary = json.loads((env.run_dir() / "summary.json").read_text(encoding="utf-8"))
    assert summary["linking"] is None


def test_bad_linking_top_k_is_a_usage_error(env: Env) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(env.args("--subset", "3", "--linking-top-k", "0"))
    assert exit_info.value.code == 2


# --- few-shot (T23) ---


def test_few_shot_on_uses_train_pool_and_records_it(env: Env) -> None:
    env.llm = ScriptedLLM(REPLY, REPLY, REPLY)
    assert cli.main(env.args("--subset", "3", "--few-shot", "on")) == 0
    run_dir = env.run_dir()
    config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    assert config["flags"]["few_shot_enabled"] is True and config["flags"]["few_shot_k"] == 3
    assert config["few_shot_pool"]["source"] == "train_spider.json"
    assert config["few_shot_pool"]["n_examples"] == 2
    assert config["embedding"]["used"] is True
    rows = read_results(run_dir / "results.jsonl")
    for r in rows:
        assert r["few_shot_ids"]  # examples were chosen...
        assert all(i.startswith("train-") for i in r["few_shot_ids"])  # ...only from train
    assert "### Examples" in env.llm.calls[0][1]["content"]
    assert env.embedder_created == 1


def test_few_shot_env_default_does_not_apply(env: Env) -> None:
    env.cfg_overrides = {"few_shot_enabled": True}  # product default must not leak
    env.llm = ScriptedLLM(REPLY, REPLY, REPLY)
    assert cli.main(env.args("--subset", "3")) == 0
    config = json.loads((env.run_dir() / "config.json").read_text(encoding="utf-8"))
    assert config["flags"]["few_shot_enabled"] is False
    assert config["few_shot_pool"] is None
    assert all(not r["few_shot_ids"] for r in read_results(env.run_dir() / "results.jsonl"))
    assert "### Examples" not in env.llm.calls[0][1]["content"]
    assert env.embedder_created == 0


def test_resume_refuses_changed_few_shot(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = _start_run(env)
    _assert_refused(env, run_dir, capsys, "flags", "--few-shot", "on")


# --- self-correction (T24) ---

BAD = "```sql\nSELECT no_such_column FROM customers\n```"


def test_self_correction_on_recovers_and_is_recorded(env: Env) -> None:
    env.llm = ScriptedLLM(BAD, REPLY, REPLY, REPLY)
    args = env.args("--subset", "3", "--self-correction", "on", "--max-corrections", "1")
    assert cli.main(args) == 0
    run_dir = env.run_dir()
    flags = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))["flags"]
    assert (flags["self_correction_enabled"], flags["max_corrections"]) == (True, 1)
    rows = read_results(run_dir / "results.jsonl")
    assert [r["n_attempts"] for r in rows] == [2, 1, 1]
    assert rows[0]["status"] == "success"
    assert rows[0]["attempts"][0]["error_code"] == "EXECUTION_ERROR"
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["attempts"] == {"1": 2, "2": 1}
    assert summary["n_corrected_to_success"] == 1


def test_self_correction_env_default_does_not_apply(env: Env) -> None:
    env.cfg_overrides = {"self_correction_enabled": True, "max_corrections": 2}
    env.llm = ScriptedLLM(BAD, REPLY, REPLY)
    assert cli.main(env.args("--subset", "3")) == 0
    flags = json.loads((env.run_dir() / "config.json").read_text(encoding="utf-8"))["flags"]
    assert flags["self_correction_enabled"] is False
    rows = read_results(env.run_dir() / "results.jsonl")
    assert rows[0]["status"] == "failed" and rows[0]["n_attempts"] == 1  # no retry


def test_resume_refuses_changed_self_correction(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = _start_run(env)
    _assert_refused(env, run_dir, capsys, "flags", "--self-correction", "on")


def test_negative_max_corrections_is_a_usage_error(env: Env) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(env.args("--subset", "3", "--max-corrections", "-1"))
    assert exit_info.value.code == 2
