"""Paired comparison of two eval runs (design §10.5, M2).

Questions are paired by ``question_id``; only questions present and scored (no gold error) in
both runs are compared. With b = wrong in A but right in B and c = right in A but wrong in B,
the exact McNemar test is the two-sided binomial test of b against b + c at p = 0.5. It is the
right test for two systems answering the same questions.
"""

import json
from dataclasses import dataclass
from math import comb
from pathlib import Path

from qm_eval.runner import HARDNESS_ORDER, read_results


def mcnemar_exact_p(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value: 2 * P(X <= min(b, c)), X ~ Binomial(b + c, 0.5)."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(comb(n, i) for i in range(min(b, c) + 1)) / 2**n
    return min(1.0, 2 * tail)


@dataclass(frozen=True)
class Comparison:
    n_paired: int
    only_in_a: int
    only_in_b: int
    ex_a: float | None
    ex_b: float | None
    delta: float | None
    b_fixed: int  # wrong in A, right in B
    c_broken: int  # right in A, wrong in B
    p_value: float
    by_hardness: dict[str, dict[str, object]]
    config_a: dict[str, object]
    config_b: dict[str, object]


def _scored(run_dir: Path) -> dict[int, dict[str, object]]:
    return {
        int(r["question_id"]): r  # type: ignore[call-overload]
        for r in read_results(run_dir / "results.jsonl")
        if r["gold_error"] is None
    }


def _ex(rows: list[dict[str, object]]) -> float | None:
    return round(sum(1 for r in rows if r["correct"]) / len(rows), 4) if rows else None


def _settings(run_dir: Path) -> dict[str, object]:
    cfg = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    return {
        "tag": cfg.get("tag"),
        "model": cfg.get("llm", {}).get("model"),
        "flags": cfg.get("flags"),
        "full": cfg.get("full"),
        "git_sha": (cfg.get("git_sha") or "")[:7],
    }


def compare_runs(run_a: Path, run_b: Path) -> Comparison:
    a, b = _scored(run_a), _scored(run_b)
    ids = sorted(set(a) & set(b))
    pairs = [(a[q], b[q]) for q in ids]
    fixed = sum(1 for x, y in pairs if not x["correct"] and y["correct"])
    broken = sum(1 for x, y in pairs if x["correct"] and not y["correct"])
    ex_a, ex_b = _ex([x for x, _ in pairs]), _ex([y for _, y in pairs])

    by_hardness: dict[str, dict[str, object]] = {}
    for h in HARDNESS_ORDER:
        hp = [(x, y) for x, y in pairs if x["hardness"] == h]
        ha, hb = _ex([x for x, _ in hp]), _ex([y for _, y in hp])
        by_hardness[h] = {
            "n": len(hp),
            "ex_a": ha,
            "ex_b": hb,
            "delta": round(hb - ha, 4) if ha is not None and hb is not None else None,
        }

    return Comparison(
        n_paired=len(ids),
        only_in_a=len(set(a) - set(b)),
        only_in_b=len(set(b) - set(a)),
        ex_a=ex_a,
        ex_b=ex_b,
        delta=round(ex_b - ex_a, 4) if ex_a is not None and ex_b is not None else None,
        b_fixed=fixed,
        c_broken=broken,
        p_value=mcnemar_exact_p(fixed, broken),
        by_hardness=by_hardness,
        config_a=_settings(run_a),
        config_b=_settings(run_b),
    )


def format_comparison(c: Comparison, name_a: str, name_b: str) -> str:
    lines = []
    if c.only_in_a or c.only_in_b:
        lines.append(
            f"WARNING: question sets differ ({c.only_in_a} only in A, {c.only_in_b} only in B); "
            f"comparing the {c.n_paired} questions present in both."
        )
    lines += [
        f"A: {name_a}  {c.config_a}",
        f"B: {name_b}  {c.config_b}",
        f"paired questions: {c.n_paired}",
        f"EX A {c.ex_a}  ->  EX B {c.ex_b}   delta {c.delta:+.4f}"
        if c.delta is not None
        else "EX: n/a (no paired questions)",
        f"flips: {c.b_fixed} wrong->right, {c.c_broken} right->wrong "
        f"(net {c.b_fixed - c.c_broken:+d})",
        f"exact McNemar p = {c.p_value:.4g}",
        "by hardness (n, EX A -> EX B, delta):",
    ]
    for h, v in c.by_hardness.items():
        delta = v["delta"]
        shown = f"{delta:+.4f}" if isinstance(delta, float) else "n/a"
        lines.append(f"  {h:<6} n={v['n']:<4} {v['ex_a']} -> {v['ex_b']}  {shown}")
    return "\n".join(lines)
