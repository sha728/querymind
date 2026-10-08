"""Every number in the README's metrics section must appear in docs/metrics.md (T20, T49).

docs/metrics.md is the single source of measured numbers (copied from run outputs); the README
may only repeat them, never introduce new ones.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
START, END = "<!-- metrics:start -->", "<!-- metrics:end -->"

# Numbers such as 0.8114, 1,034, 3,106, 592.5, 152 — optionally preceded by $ (kept out).
NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")


def numbers(text: str) -> set[str]:
    # Ignore identifiers that are not measurements: commit SHAs and run folder names.
    text = re.sub(r"`[^`]*`", " ", text)
    return {m.group(0).rstrip(",") for m in NUMBER.finditer(text)}


def metrics_section(readme: str) -> str:
    assert START in readme and END in readme, "README is missing the metrics markers"
    return readme.split(START, 1)[1].split(END, 1)[0]


def test_readme_numbers_all_come_from_metrics_doc() -> None:
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    metrics = (REPO / "docs" / "metrics.md").read_text(encoding="utf-8")
    section = metrics_section(readme)
    found = numbers(section)
    assert found, "no numbers found in the README metrics section"
    missing = sorted(n for n in found if n not in metrics)
    assert not missing, f"README numbers not in docs/metrics.md: {missing}"


def test_number_extraction() -> None:
    sample = "EX **0.8114** (839 / 1,034), p95 3,106 ms, $0.3537 per 1,000, run `20261008T0807`"
    assert numbers(sample) == {"0.8114", "839", "1,034", "3,106", "0.3537", "1,000"}
