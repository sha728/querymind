"""Engine configuration loaded from environment variables (design §6.6, §14.2)."""

from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Dialect = Literal["postgres", "sqlite"]
LinkingMode = Literal["auto", "on", "off"]
LLMProvider = Literal["ollama", "groq"]


class EngineConfig(BaseSettings):
    """All engine settings.

    Pipeline and LLM keys use the ``QM_`` prefix. Target DB credentials keep the
    ``TARGET_DB_*`` names shared with docker-compose (R1.1). The evaluation harness
    builds this object in-process with its own values (design §10.2).
    """

    model_config = SettingsConfigDict(
        env_prefix="QM_",
        populate_by_name=True,
        extra="ignore",
        frozen=True,
    )

    # --- Pipeline (design §6.6) ---
    dialect: Dialect = "postgres"
    linking_mode: LinkingMode = "auto"
    linking_token_threshold: int = Field(default=2000, gt=0)
    linking_top_k: int = Field(default=5, ge=1)
    few_shot_enabled: bool = True
    few_shot_k: int = Field(default=3, ge=1)
    few_shot_pool: str = "fewshot/northwind.yaml"
    self_correction_enabled: bool = True
    max_corrections: int = Field(default=2, ge=0)
    row_limit: int | None = 1000
    statement_timeout_ms: int = Field(default=10000, gt=0)
    unanswerable_enabled: bool = True
    summary_enabled: bool = True
    include_date: bool = True

    # --- LLM, OpenAI-compatible (design §14.2, D1) ---
    llm_provider: LLMProvider = "ollama"
    llm_base_url: str = "http://host.docker.internal:11434/v1"
    llm_model: str = "qwen2.5-coder:7b"
    llm_api_key: SecretStr | None = None
    llm_num_ctx: int = Field(default=8192, gt=0)
    llm_timeout_s: float = Field(default=60, gt=0)

    # --- Embeddings ---
    embed_base_url: str = "http://host.docker.internal:11434/v1"
    embed_model: str = "nomic-embed-text"

    # --- Target DB, read-only role (R1.1, R4.1) ---
    target_db_host: str = Field(default="target-db", validation_alias="TARGET_DB_HOST")
    target_db_name: str = Field(default="northwind", validation_alias="TARGET_DB_NAME")
    target_db_ro_user: str = Field(default="querymind_ro", validation_alias="TARGET_DB_RO_USER")
    target_db_ro_password: SecretStr | None = Field(
        default=None, validation_alias="TARGET_DB_RO_PASSWORD"
    )

    @model_validator(mode="after")
    def _check_limits_and_provider(self) -> Self:
        if self.row_limit is not None and self.row_limit <= 0:
            raise ValueError("row_limit must be a positive integer")
        # R4.4: the product (PostgreSQL) always caps rows; only eval (SQLite) may disable it.
        if self.dialect == "postgres" and self.row_limit is None:
            raise ValueError("row_limit cannot be disabled for the postgres (product) dialect")
        if self.llm_provider == "groq" and self.llm_api_key is None:
            raise ValueError("llm_api_key is required when llm_provider is groq")
        return self
