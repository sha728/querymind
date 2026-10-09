import json
from pathlib import Path

import pytest

from qm_eval import cli
from qm_eval.compare import compare_runs, format_comparison, mcnemar_exact_p


def make_run(root: Path, name: str, rows: list[tuple[int, str, bool, bool]]) -> Path:
    """rows: (question_id, hardness, correct, gold_error)."""
    run = root / name
    run.mkdir(parents=True)
    (run / "config.json").write_text(
        json.dumps(
            {
                "tag": name,
                "llm": {"model": "m"},
                "flags": {"x": 1},
                "full": False,
                "git_sha": "abc1234ff",
            }
        ),
        encoding="utf-8",
    )
    lines = [
        json.dumps(
            {
                "question_id": q,
                "hardness": h,
                "correct": ok,
                "gold_error": "boom" if gold_err else None,
            }
        )
        for q, h, ok, gold_err in rows
    ]
    (run / "results.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return run


# --- exact McNemar, checked by hand ---


def test_mcnemar_hand_calculation() -> None:
    # b=2, c=8, n=10: P(X<=2) = (C(10,0)+C(10,1)+C(10,2)) / 2^10 = (1+10+45)/1024 = 56/1024
    # two-sided p = 2 * 56/1024 = 0.109375
    assert mcnemar_exact_p(2, 8) == pytest.approx(0.109375)
    assert mcnemar_exact_p(8, 2) == pytest.approx(0.109375)  # symmetric
    # b=0, c=6: 2 * 1/64 = 0.03125
    assert mcnemar_exact_p(0, 6) == pytest.approx(0.03125)


def test_mcnemar_edge_cases() -> None:
    assert mcnemar_exact_p(0, 0) == 1.0  # no discordant pairs
    assert mcnemar_exact_p(5, 5) == 1.0  # capped at 1


# --- comparison ---


def test_delta_flips_and_p_value(tmp_path: Path) -> None:
    # 12 paired questions. A: q0-q5 right, q6-q11 wrong.
    # B fixes q6, q7 (wrong->right) and breaks q0 (right->wrong); others unchanged.
    a_rows = [(q, "easy" if q < 6 else "hard", q < 6, False) for q in range(12)]
    b_correct = {q: (q < 6 and q != 0) or q in (6, 7) for q in range(12)}
    b_rows = [(q, "easy" if q < 6 else "hard", b_correct[q], False) for q in range(12)]
    a = make_run(tmp_path, "A", a_rows)
    b = make_run(tmp_path, "B", b_rows)

    c = compare_runs(a, b)
    assert c.n_paired == 12
    assert (c.ex_a, c.ex_b) == (0.5, 0.5833)  # 6/12 -> 7/12
    assert c.delta == pytest.approx(0.0833)
    assert (c.b_fixed, c.c_broken) == (2, 1)
    assert c.p_value == pytest.approx(1.0)  # 2 * (C(3,0)+C(3,1))/8 = 1.0
    assert c.by_hardness["easy"] == {"n": 6, "ex_a": 1.0, "ex_b": 0.8333, "delta": -0.1667}
    assert c.by_hardness["hard"]["delta"] == pytest.approx(0.3333)
    text = format_comparison(c, "A", "B")
    assert "flips: 2 wrong->right, 1 right->wrong (net +1)" in text
    assert "WARNING" not in text


def test_only_intersection_is_compared_and_mismatch_warns(tmp_path: Path) -> None:
    a = make_run(
        tmp_path,
        "A",
        [(1, "easy", True, False), (2, "easy", False, False), (3, "easy", True, False)],
    )
    b = make_run(
        tmp_path,
        "B",
        [(2, "easy", True, False), (3, "easy", True, False), (4, "easy", True, False)],
    )
    c = compare_runs(a, b)
    assert c.n_paired == 2
    assert (c.only_in_a, c.only_in_b) == (1, 1)
    assert (c.b_fixed, c.c_broken) == (1, 0)
    assert format_comparison(c, "A", "B").startswith("WARNING: question sets differ")


def test_gold_errors_are_excluded_from_pairs(tmp_path: Path) -> None:
    a = make_run(tmp_path, "A", [(1, "easy", False, True), (2, "easy", True, False)])
    b = make_run(tmp_path, "B", [(1, "easy", True, False), (2, "easy", True, False)])
    c = compare_runs(a, b)
    assert c.n_paired == 1 and c.b_fixed == 0


def test_cli_compare(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    a = make_run(tmp_path, "A", [(1, "easy", False, False)])
    b = make_run(tmp_path, "B", [(1, "easy", True, False)])
    assert cli.main(["compare", str(a), str(b)]) == 0
    out = capsys.readouterr().out
    assert "exact McNemar p = 1" in out and "delta +1.0000" in out


def test_cli_compare_refuses_missing_results(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a = make_run(tmp_path, "A", [(1, "easy", True, False)])
    assert cli.main(["compare", str(a), str(tmp_path / "nope")]) == 1
    assert "no results.jsonl" in capsys.readouterr().err
