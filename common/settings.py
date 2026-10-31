"""Single source of configuration for AX-13.

Everything (keys, endpoints, model ids) is read from `.env` at the repo root.
No other module reads os.environ or hard-codes a key or model id.

    from common.settings import get_settings
    s = get_settings()
    s.postgres_dsn
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- run ----
    ax13_env: str = "dev"                 # dev | demo
    ax13_seed: int | None = None          # optional override of data/config/sim_config.yaml seed

    # ---- LLMs (LiteLLM model ids, provider prefix included) ----
    openai_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    ax13_model_reason: str | None = None
    ax13_model_extract: str | None = None
    ax13_model_fallback: str | None = None
    ax13_embed_model: str = "openai/text-embedding-3-small"

    # ---- Postgres ----
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "ax13"
    postgres_user: str = "ax13"
    postgres_password: SecretStr = SecretStr("")
    postgres_schema: str = "ax13"

    # ---- Qdrant ----
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: SecretStr | None = None

    # ---- OpenTelemetry / Grafana ----
    otel_service_name: str = "ax13"
    otel_exporter_otlp_endpoint: str = "http://localhost:4318"
    otel_exporter_otlp_protocol: str = "http/protobuf"
    grafana_url: str = "http://localhost:3000"

    # ---- LangSmith (dev only) ----
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "ax13-dev"

    # ---- Voice (Phase 4) ----
    hf_token: SecretStr | None = None
    sarvam_api_key: SecretStr | None = None

    # ---- paths ----
    @property
    def data_config_dir(self) -> Path:
        return REPO_ROOT / "data" / "config"

    @property
    def data_out_dir(self) -> Path:
        return REPO_ROOT / "data" / "out"

    @property
    def postgres_dsn(self) -> str:
        """libpq DSN for psycopg."""
        return (f"host={self.postgres_host} port={self.postgres_port} dbname={self.postgres_db} "
                f"user={self.postgres_user} password={self.postgres_password.get_secret_value()}")

    @property
    def postgres_url(self) -> str:
        """SQLAlchemy URL (psycopg 3 driver)."""
        return (f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password.get_secret_value()}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
