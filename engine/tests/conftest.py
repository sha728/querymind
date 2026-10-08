import pytest


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
