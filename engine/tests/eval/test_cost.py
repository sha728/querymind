import json
from datetime import date
from pathlib import Path

import pytest

from qm_eval import cli
from qm_eval.cost import DEFAULT_PRICING, compute_cost, format_report, load_pricing

PRICING = """\
- provider: cerebras
  model: gpt-oss-120b
  input_usd_per_mtok: 0.35
  output_usd_per_mtok: 0.75
  source_url: https://example.test/pricing
  retrieved_on: 2026-10-08
"""


def make_run(tmp_path: Path, provider: str, model: str, usage: list[tuple]) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "config.json").write_text(
        json.dumps({"llm": {"provider": provider, "model": model}}), encoding="utf-8"
    )
    lines = [
        json.dumps({"question_id": i, "prompt_tokens": p, "completion_tokens": c})
        for i, (p, c) in enumerate(usage)
    ]
    (run_dir / "results.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return run_dir


@pytest.fixture
def pricing(tmp_path: Path) -> Path:
    path = tmp_path / "pricing.yaml"
    path.write_text(PRICING, encoding="utf-8")
    return path


def test_cost_matches_hand_calculation(tmp_path: Path, pricing: Path) -> None:
    run = make_run(tmp_path, "cerebras", "gpt-oss-120b", [(1000, 200), (2000, 300), (None, None)])
    report = compute_cost(run, load_pricing(pricing))
    # By hand: prompt 3,000 x $0.35/M + completion 500 x $0.75/M = $0.00105 + $0.000375
    assert (report.prompt_tokens, report.completion_tokens) == (3000, 500)
    assert report.cost_usd == pytest.approx(0.001425)
    # Per 1,000 questions over the 2 questions with usage: 0.001425 / 2 x 1000 = $0.7125
    assert report.per_1k_usd == pytest.approx(0.7125)
    assert (report.n_questions, report.n_with_usage, report.n_missing_usage) == (3, 2, 1)

    text = format_report(report)
    assert "$0.7125 per 1,000 questions" in text
    assert "estimate at list price retrieved on 2026-10-08" in text
    assert "1 missing usage, not estimated" in text
    assert "free tier are billed $0" in text


def test_ollama_reports_zero_api_cost(tmp_path: Path, pricing: Path) -> None:
    run = make_run(tmp_path, "ollama", "qwen2.5-coder:7b", [(800, 50), (900, 60)])
    report = compute_cost(run, load_pricing(pricing))
    assert report.cost_usd is None and report.price is None
    assert report.prompt_tokens == 1700
    assert "$0 API cost (local hardware)" in format_report(report)


def test_unknown_model_is_refused(tmp_path: Path, pricing: Path) -> None:
    run = make_run(tmp_path, "cerebras", "some-other-model", [(1, 1)])
    with pytest.raises(KeyError, match="some-other-model"):
        compute_cost(run, load_pricing(pricing))


def test_no_usage_at_all(tmp_path: Path, pricing: Path) -> None:
    run = make_run(tmp_path, "cerebras", "gpt-oss-120b", [(None, None)])
    report = compute_cost(run, load_pricing(pricing))
    assert report.cost_usd == 0.0 and report.per_1k_usd is None
    assert "no question reported token usage" in format_report(report)


def test_cli_cost_command(
    tmp_path: Path, pricing: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = make_run(tmp_path, "cerebras", "gpt-oss-120b", [(1000, 200)])
    assert cli.main(["cost", str(run), "--pricing", str(pricing)]) == 0
    assert "per 1,000 questions" in capsys.readouterr().out


def test_cli_cost_refuses_unknown_model(
    tmp_path: Path, pricing: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = make_run(tmp_path, "groq", "unknown", [(1, 1)])
    assert cli.main(["cost", str(run), "--pricing", str(pricing)]) == 1
    assert "no list price" in capsys.readouterr().err


def test_committed_pricing_file_has_sources_and_dates() -> None:
    prices = load_pricing(DEFAULT_PRICING)
    keys = {(p.provider, p.model) for p in prices}
    assert {
        ("cerebras", "gpt-oss-120b"),
        ("cerebras", "qwen-3.8-27b"),
        ("groq", "openai/gpt-oss-120b"),
    } <= keys
    for p in prices:
        assert p.source_url.startswith("https://")
        assert isinstance(p.retrieved_on, date)
        assert p.input_usd_per_mtok > 0 and p.output_usd_per_mtok > 0
