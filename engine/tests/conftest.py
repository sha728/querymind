import os
from collections.abc import Callable
from pathlib import Path

import pytest

from qm_engine.config import EngineConfig
from qm_engine.execution.postgres import connect_ro, use_selector_event_loop_on_windows

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"

use_selector_event_loop_on_windows()  # psycopg async needs it on Windows hosts


@pytest.fixture(scope="session")
def target_cfg() -> EngineConfig:
    """Config for the running target-db (from .env); skips when it is not reachable.

    Integration tests need `docker compose up target-db` (design §13.1). In CI the database
    service arrives with T46; until then these tests skip.
    """
    import asyncio

    try:
        cfg = EngineConfig(cerebras_api_key="unused-in-db-tests")  # type: ignore[call-arg]
    except Exception as e:
        pytest.skip(f"target-db settings unavailable: {e}")
    if cfg.target_db_ro_password is None:
        pytest.skip("TARGET_DB_RO_PASSWORD not set; start target-db with docker compose")

    async def probe() -> None:
        conn = await connect_ro(cfg)
        await conn.close()

    try:
        asyncio.run(probe())
    except Exception as e:
        pytest.skip(
            f"target-db not reachable at {cfg.target_db_host}:{cfg.target_db_port} "
            f"(run `docker compose up -d target-db`): {type(e).__name__}"
        )
    return cfg


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
