import os
from collections.abc import Callable
from pathlib import Path

import pytest

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"


@pytest.fixture
def snapshot() -> Callable[[str, str], None]:
    """Compare text with tests/snapshots/<name>. Set QM_UPDATE_SNAPSHOTS=1 to (re)write."""

    def check(name: str, text: str) -> None:
        path = SNAPSHOT_DIR / name
        if os.environ.get("QM_UPDATE_SNAPSHOTS") == "1":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
            return
        assert path.exists(), f"missing snapshot {name}; run with QM_UPDATE_SNAPSHOTS=1"
        assert text == path.read_text(encoding="utf-8"), f"snapshot {name} changed"

    return check


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """Always print the M4 unsafe-SQL block rate (design §7.5) when the safety tests ran."""
    if not any(
        "test_safety.py" in r.nodeid
        for reports in terminalreporter.stats.values()
        for r in reports
        if hasattr(r, "nodeid")
    ):
        return
    from test_safety import UNSAFE, block_rate

    blocked, total = block_rate(UNSAFE)
    terminalreporter.write_line(
        f"M4 unsafe-SQL block rate: {blocked}/{total} = {100 * blocked / total:.1f}%"
    )
