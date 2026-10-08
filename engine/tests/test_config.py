import os
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
    "llm_provider": "ollama",
    "llm_base_url": "http://host.docker.internal:11434/v1",
    "llm_model": "qwen2.5-coder:7b",
    "llm_api_key": None,
    "llm_num_ctx": 8192,
    "llm_timeout_s": 60,
    "embed_base_url": "http://host.docker.internal:11434/v1",
    "embed_model": "nomic-embed-text",
    "target_db_host": "target-db",
    "target_db_name": "northwind",
    "target_db_ro_user": "querymind_ro",
    "target_db_ro_password": None,
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith(("QM_", "TARGET_DB_")):
            monkeypatch.delenv(key)


def test_every_documented_key_has_its_default() -> None:
    cfg = EngineConfig()
    assert set(DEFAULTS) == set(EngineConfig.model_fields)
    for name, expected in DEFAULTS.items():
        assert getattr(cfg, name) == expected, name


def test_env_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QM_DIALECT", "sqlite")
    monkeypatch.setenv("QM_LINKING_MODE", "on")
    monkeypatch.setenv("QM_FEW_SHOT_ENABLED", "false")
    monkeypatch.setenv("QM_MAX_CORRECTIONS", "0")
    monkeypatch.setenv("QM_ROW_LIMIT", "50")
    monkeypatch.setenv("QM_LLM_PROVIDER", "groq")
    monkeypatch.setenv("QM_LLM_API_KEY", "gsk-test")
    monkeypatch.setenv("QM_LLM_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("TARGET_DB_HOST", "localhost")
    monkeypatch.setenv("TARGET_DB_RO_PASSWORD", "pw")

    cfg = EngineConfig()

    assert cfg.dialect == "sqlite"
    assert cfg.linking_mode == "on"
    assert cfg.few_shot_enabled is False
    assert cfg.max_corrections == 0
    assert cfg.row_limit == 50
    assert cfg.llm_provider == "groq"
    assert cfg.llm_api_key is not None
    assert cfg.llm_api_key.get_secret_value() == "gsk-test"
    assert cfg.llm_model == "openai/gpt-oss-120b"
    assert cfg.target_db_host == "localhost"
    assert cfg.target_db_ro_password is not None
    assert cfg.target_db_ro_password.get_secret_value() == "pw"


@pytest.mark.parametrize("bad", [None, 0, -1])
def test_product_rejects_disabled_or_nonpositive_row_limit(bad: int | None) -> None:
    with pytest.raises(ValidationError, match="row_limit"):
        EngineConfig(dialect="postgres", row_limit=bad)


def test_eval_may_disable_row_limit() -> None:
    cfg = EngineConfig(dialect="sqlite", row_limit=None)
    assert cfg.row_limit is None


def test_eval_still_rejects_nonpositive_row_limit() -> None:
    with pytest.raises(ValidationError, match="row_limit"):
        EngineConfig(dialect="sqlite", row_limit=0)


def test_invalid_enum_value_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QM_DIALECT", "mysql")
    with pytest.raises(ValidationError):
        EngineConfig()


def test_groq_requires_api_key() -> None:
    with pytest.raises(ValidationError, match="llm_api_key"):
        EngineConfig(llm_provider="groq")


def test_secrets_not_exposed_in_repr() -> None:
    cfg = EngineConfig(llm_api_key="sk-secret", target_db_ro_password="db-secret")
    text = repr(cfg) + str(cfg.model_dump())
    assert "sk-secret" not in text
    assert "db-secret" not in text


def test_config_is_immutable() -> None:
    cfg = EngineConfig()
    with pytest.raises(ValidationError):
        cfg.row_limit = None  # type: ignore[misc]
