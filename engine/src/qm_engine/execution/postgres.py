"""PostgreSQL access for the product: the read-only connection (design §6.5, R1.1, R4.1).

Every connection uses the read-only role from configuration and runs read-only transactions.
The query executor (T29) is added to this module.
"""

import asyncio
import sys

import psycopg

from qm_engine.config import EngineConfig

CONNECT_TIMEOUT_S = 10


def conninfo(cfg: EngineConfig) -> str:
    """Connection string for the read-only role (password never logged)."""
    password = cfg.target_db_ro_password.get_secret_value() if cfg.target_db_ro_password else ""
    return psycopg.conninfo.make_conninfo(
        host=cfg.target_db_host,
        port=cfg.target_db_port,
        dbname=cfg.target_db_name,
        user=cfg.target_db_ro_user,
        password=password,
        connect_timeout=CONNECT_TIMEOUT_S,
        application_name="querymind-engine",
    )


def use_selector_event_loop_on_windows() -> None:
    """psycopg's async mode cannot run on Windows' default ProactorEventLoop.

    Call once at process start in host-run entry points (tests, qm-eval, the API when run
    on Windows). No effect on Linux, where the engine container runs.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]


async def connect_ro(cfg: EngineConfig) -> psycopg.AsyncConnection:
    """An autocommit-off connection whose transactions all start with BEGIN READ ONLY.

    This is layer L2 (design §7.1); the role's own default_transaction_read_only and missing
    privileges (layer L1) apply as well.
    """
    conn = await psycopg.AsyncConnection.connect(conninfo(cfg))
    await conn.set_read_only(True)
    return conn
