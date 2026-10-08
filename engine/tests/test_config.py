import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from qm_engine.config import EngineConfig

# Documented defaults: design §6.6 (pipeline) and §14.2 (LLM, embeddings, target DB).
DEFAULTS: dict[str, Any] = {
    "dialect": "postgres",
    "linking_mode": "auto",
    "linking_token_threshold": 2000,
    "linking_top_k": 5,
    "few_shot_enabled": True,
    "few_shot_k": 3,
    "few_shot_pool": "fewshot/northwind.yaml",
    "self_correction_enabled": True,
    "max_corrections": 2,
    "row_limit": 1000,
    "statement_timeout_ms": 10000,
    "unanswerable_enabled": True,
    "summary_enabled": True,
    "include_date": True,
    "llm_provider": "cerebras",
    "llm_model": "gpt-oss-120b",
    "llm_base_url": None,
    "cerebras_api_key": None,  # required for the default provider; checked separately
    "groq_api_key": None,
    "llm_reasoning_effort": "low",
    "llm_max_tokens": 4096,
    "llm_min_interval_s": None,
    "llm_num_ctx": 8192,
    "llm_timeout_s": 60,
    "ollama_base_url": "http://localhost:11434/v1",
    "embed_base_url": "http://host.docker.internal:11434/v1",
    "embed_model": "nomic-embed-text",
    "embed_batch_size": 64,
    "cache_dir": Path(".engine_cache"),
    "target_db_host": "target-db",
    "target_db_port": 5432,
    "target_db_name": "northwind",
    "target_db_ro_user": "querymind_ro",
    "target_db_ro_password": None,
}


REPO_ROOT = Path(__file__).resolve().parents[2]


def make(**kwargs: object) -> EngineConfig:
    """Build a config that ignores any developer .env file (design §6.6).

    The default provider (Cerebras) requires a key, so a dummy one is supplied unless the
    test sets it.
    """
    kwargs.setdefault("cerebras_api_key", "test-cerebras-key")
    return EngineConfig(_env_file=None, **kwargs)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith(("QM_", "TARGET_DB_")):
            monkeypatch.delenv(key)


def test_every_documented_key_has_its_default() -> None:
    cfg = make()
    assert set(DEFAULTS) == set(EngineConfig.model_fields)
    for name, expected in DEFAULTS.items():
        if name != "cerebras_api_key":
            assert getattr(cfg, name) == expected, name


def test_default_provider_requires_its_key() -> None:
    with pytest.raises(ValidationError, match="QM_CEREBRAS_API_KEY"):
        EngineConfig(_env_file=None)


def test_env_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QM_DIALECT", "sqlite")
    monkeypatch.setenv("QM_LINKING_MODE", "on")
    monkeypatch.setenv("QM_FEW_SHOT_ENABLED", "false")
    monkeypatch.setenv("QM_MAX_CORRECTIONS", "0")
    monkeypatch.setenv("QM_ROW_LIMIT", "50")
    monkeypatch.setenv("QM_LLM_PROVIDER", "groq")
    monkeypatch.setenv("QM_GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("QM_LLM_REASONING_EFFORT", "medium")
    monkeypatch.setenv("QM_LLM_MIN_INTERVAL_S", "0.5")
    monkeypatch.setenv("QM_LLM_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("TARGET_DB_HOST", "localhost")
    monkeypatch.setenv("TARGET_DB_RO_PASSWORD", "pw")

    cfg = make()

    assert cfg.dialect == "sqlite"
    assert cfg.linking_mode == "on"
    assert cfg.few_shot_enabled is False
    assert cfg.max_corrections == 0
    assert cfg.row_limit == 50
    assert cfg.llm_provider == "groq"
    assert cfg.api_key is not None
    assert cfg.api_key.get_secret_value() == "gsk-test"
    assert cfg.base_url == "https://api.groq.com/openai/v1"
    assert cfg.llm_reasoning_effort == "medium"
    assert cfg.min_interval_s == 0.5
    assert cfg.llm_model == "openai/gpt-oss-120b"
    assert cfg.target_db_host == "localhost"
    assert cfg.target_db_ro_password is not None
    assert cfg.target_db_ro_password.get_secret_value() == "pw"


@pytest.mark.parametrize("bad", [None, 0, -1])
def test_product_rejects_disabled_or_nonpositive_row_limit(bad: int | None) -> None:
    with pytest.raises(ValidationError, match="row_limit"):
        make(dialect="postgres", row_limit=bad)


def test_eval_may_disable_row_limit() -> None:
    cfg = make(dialect="sqlite", row_limit=None)
    assert cfg.row_limit is None


def test_eval_still_rejects_nonpositive_row_limit() -> None:
    with pytest.raises(ValidationError, match="row_limit"):
        make(dialect="sqlite", row_limit=0)


def test_invalid_enum_value_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QM_DIALECT", "mysql")
    with pytest.raises(ValidationError):
        make()


def test_groq_requires_its_own_key() -> None:
    with pytest.raises(ValidationError, match="QM_GROQ_API_KEY"):
        make(llm_provider="groq")  # a Cerebras key does not count


def test_ollama_needs_no_key() -> None:
    cfg = make(llm_provider="ollama", cerebras_api_key=None)
    assert cfg.api_key is None
    assert cfg.base_url == "http://localhost:11434/v1"
    assert cfg.min_interval_s == 0.0


@pytest.mark.parametrize(
    ("provider", "url", "interval"),
    [
        ("cerebras", "https://api.cerebras.ai/v1", 12.0),
        ("groq", "https://api.groq.com/openai/v1", 2.0),
    ],
)
def test_provider_defaults(provider: str, url: str, interval: float) -> None:
    cfg = make(llm_provider=provider, groq_api_key="g")
    assert cfg.base_url == url
    assert cfg.min_interval_s == interval


def test_explicit_base_url_overrides_provider_default() -> None:
    assert make(llm_base_url="http://proxy:8080/v1").base_url == "http://proxy:8080/v1"


def test_active_key_follows_provider() -> None:
    cfg = make(cerebras_api_key="c-key", groq_api_key="g-key")
    assert cfg.api_key is not None and cfg.api_key.get_secret_value() == "c-key"
    groq = make(llm_provider="groq", cerebras_api_key="c-key", groq_api_key="g-key")
    assert groq.api_key is not None and groq.api_key.get_secret_value() == "g-key"


def test_secrets_not_exposed_in_repr() -> None:
    cfg = make(
        cerebras_api_key="csk-secret", groq_api_key="sk-secret", target_db_ro_password="db-secret"
    )
    text = repr(cfg) + str(cfg.model_dump())
    assert "csk-secret" not in text
    assert "sk-secret" not in text
    assert "db-secret" not in text


def test_config_is_immutable() -> None:
    cfg = make()
    with pytest.raises(ValidationError):
        cfg.row_limit = None  # type: ignore[misc]


# --- .env loading (design E9) ---


KEY = "QM_CEREBRAS_API_KEY=test-key"


def _write(path: Path, *lines: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return path


def test_loads_values_from_env_file(tmp_path: Path) -> None:
    env = _write(
        tmp_path / ".env",
        "QM_LLM_MODEL=from-file",
        "QM_CEREBRAS_API_KEY=from-file-key",
        "TARGET_DB_PORT=5433",
        "TARGET_DB_RO_PASSWORD=pw",
        "APP_DB_NAME=x",
    )
    cfg = EngineConfig(_env_file=env)
    assert cfg.llm_model == "from-file"
    assert cfg.target_db_port == 5433
    assert cfg.target_db_ro_password is not None
    assert cfg.target_db_ro_password.get_secret_value() == "pw"


def test_env_var_overrides_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _write(tmp_path / ".env", "QM_LLM_MODEL=from-file", "QM_MAX_CORRECTIONS=5", KEY)
    monkeypatch.setenv("QM_LLM_MODEL", "from-env")
    cfg = EngineConfig(_env_file=env)
    assert cfg.llm_model == "from-env"
    assert cfg.max_corrections == 5


def test_code_value_overrides_env_and_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _write(tmp_path / ".env", "QM_LINKING_MODE=auto", KEY)
    monkeypatch.setenv("QM_LINKING_MODE", "off")
    assert EngineConfig(_env_file=env, linking_mode="on").linking_mode == "on"


def test_missing_file_is_ignored(tmp_path: Path) -> None:
    cfg = EngineConfig(_env_file=tmp_path / "does-not-exist.env", cerebras_api_key="k")
    assert cfg.llm_model == DEFAULTS["llm_model"]


def test_default_search_order_parent_then_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path / ".env", "QM_LLM_MODEL=parent", "QM_FEW_SHOT_K=7", KEY)
    _write(tmp_path / "engine" / ".env", "QM_LLM_MODEL=cwd")
    monkeypatch.chdir(tmp_path / "engine")
    cfg = EngineConfig()
    assert cfg.llm_model == "cwd"  # ./.env wins over ../.env
    assert cfg.few_shot_k == 7  # keys only in ../.env still load


def test_env_example_parses_with_host_values() -> None:
    cfg = EngineConfig(_env_file=REPO_ROOT / ".env.example", cerebras_api_key="k")
    assert cfg.target_db_host == "localhost"
    assert cfg.target_db_port == 5433
    assert cfg.llm_provider == "cerebras"  # inline comment stripped
    assert cfg.llm_model == "gpt-oss-120b"
    assert cfg.llm_base_url is None  # empty value treated as unset
    assert cfg.base_url == "https://api.cerebras.ai/v1"
    assert cfg.groq_api_key is None
    assert cfg.llm_reasoning_effort == "low"
    assert cfg.min_interval_s == 12.0
    assert cfg.ollama_base_url == "http://localhost:11434/v1"
    assert cfg.embed_base_url == "http://localhost:11434/v1"
    assert cfg.row_limit == 1000
