"""Engine configuration loaded from environment variables (design §6.6, §14.2)."""

from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Dialect = Literal["postgres", "sqlite"]
LinkingMode = Literal["auto", "on", "off"]
LLMProvider = Literal["cerebras", "groq", "ollama"]
ReasoningEffort = Literal["low", "medium", "high"]

# Default base URL per hosted provider (design E11). Ollama uses `ollama_base_url`.
PROVIDER_BASE_URLS: dict[str, str] = {
    "cerebras": "https://api.cerebras.ai/v1",
    "groq": "https://api.groq.com/openai/v1",
}
# Default minimum seconds between requests, from free-tier requests/minute (design E11).
PROVIDER_MIN_INTERVAL_S: dict[str, float] = {"cerebras": 12.0, "groq": 2.0, "ollama": 0.0}


class EngineConfig(BaseSettings):
    """All engine settings.

    Pipeline and LLM keys use the ``QM_`` prefix. Target DB credentials keep the
    ``TARGET_DB_*`` names shared with docker-compose (R1.1). The evaluation harness
    builds this object in-process with its own values (design §10.2).

    Loading order, lowest to highest priority (design §6.6, E9): defaults, ``../.env``,
    ``./.env``, environment variables, values passed in code. Both files are optional,
    so this works from the repo root, from ``engine/``, and in a container (no file).
    Pass ``_env_file=None`` to ignore ``.env`` files entirely (tests do).
    """

    model_config = SettingsConfigDict(
        env_prefix="QM_",
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
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

    # --- LLM, OpenAI-compatible (design D1, E11, §14.2) ---
    llm_provider: LLMProvider = "cerebras"
    llm_model: str = "gpt-oss-120b"
    llm_base_url: str | None = None  # None -> provider default (see `base_url`)
    cerebras_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    llm_reasoning_effort: ReasoningEffort = "low"  # sent to cerebras/groq only
    llm_max_tokens: int = Field(default=4096, gt=0)  # room for reasoning tokens
    llm_min_interval_s: float | None = Field(default=None, ge=0)  # None -> provider default
    llm_num_ctx: int = Field(default=8192, gt=0)  # ollama only: for the 0.9 x warning
    llm_timeout_s: float = Field(default=60, gt=0)
    ollama_base_url: str = "http://localhost:11434/v1"

    # --- Embeddings (schema linking, few-shot; design D3, D4) ---
    embed_base_url: str = "http://host.docker.internal:11434/v1"
    embed_model: str = "nomic-embed-text"
    embed_batch_size: int = Field(default=64, gt=0)
    cache_dir: Path = Field(default=Path(".engine_cache"), validation_alias="ENGINE_CACHE_DIR")

    # --- Target DB, read-only role (R1.1, R4.1) ---
    target_db_host: str = Field(default="target-db", validation_alias="TARGET_DB_HOST")
    target_db_port: int = Field(default=5432, gt=0, lt=65536, validation_alias="TARGET_DB_PORT")
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
        if self.llm_provider != "ollama" and self.api_key is None:
            raise ValueError(
                f"QM_{self.llm_provider.upper()}_API_KEY is required when llm_provider is "
                f"{self.llm_provider}"
            )
        return self

    @property
    def base_url(self) -> str:
        """The chat endpoint base URL: explicit override, else the provider default."""
        if self.llm_base_url:
            return self.llm_base_url
        if self.llm_provider == "ollama":
            return self.ollama_base_url
        return PROVIDER_BASE_URLS[self.llm_provider]

    @property
    def api_key(self) -> SecretStr | None:
        """The active provider's key (Ollama needs none)."""
        return {"cerebras": self.cerebras_api_key, "groq": self.groq_api_key}.get(self.llm_provider)

    @property
    def min_interval_s(self) -> float:
        if self.llm_min_interval_s is not None:
            return self.llm_min_interval_s
        return PROVIDER_MIN_INTERVAL_S[self.llm_provider]
