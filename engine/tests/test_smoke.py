import tomllib
from importlib.metadata import version
from pathlib import Path

import qm_engine

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _project() -> dict:
    with PYPROJECT.open("rb") as f:
        return tomllib.load(f)["project"]


def test_package_imports() -> None:
    assert qm_engine.__version__ == "0.1.0"


def test_sqlglot_pinned_exactly_and_installed() -> None:
    pins = [d for d in _project()["dependencies"] if d.startswith("sqlglot")]
    assert len(pins) == 1
    name, _, pinned = pins[0].partition("==")
    assert name == "sqlglot" and pinned, "sqlglot must be pinned with =="
    assert version("sqlglot") == pinned


def test_qm_eval_entry_point_declared() -> None:
    assert _project()["scripts"]["qm-eval"] == "qm_eval.cli:main"
