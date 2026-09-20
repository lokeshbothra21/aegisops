"""Application settings, loaded from environment variables (12-factor).

Every value has a safe local default so `uv run uvicorn` works with no .env file.
Production values come from Cloud Run environment variables and Secret Manager.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AEGIS_", env_file=".env", extra="ignore")

    env: Literal["local", "test", "prod"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = Field(default=False, description="Emit JSON logs (True in prod).")

    database_url: str = Field(
        default="postgresql+asyncpg://aegis:aegis@localhost:5433/aegis",  # pragma: allowlist secret
        description="SQLAlchemy async URL.",
    )

    admin_token: SecretStr | None = Field(
        default=None,
        description="X-Admin-Token for mutating routes (§7). Unset = admin routes answer 503.",
    )

    jobs_enabled: bool = Field(default=True, description="Run periodic jobs in this process.")
    retention_hours: float = Field(
        default=24, gt=0, description="Delete untagged rows older than this."
    )
    retention_interval_s: float = Field(
        default=3600, ge=10, description="How often retention runs."
    )
    service_edges_interval_s: float = Field(
        default=900,
        ge=10,
        description="How often service_edges is refreshed (current + previous hour).",
    )

    target_config_path: str = Field(
        default="config/targets/otel-demo.yaml",
        description="Target-system specifics (flags -> services). Relative to the repo root.",
    )
    flagd_config_path: str | None = Field(
        default=None,
        description="demo.flagd.json to watch for flag changes (E2.1). Unset = watcher off.",
    )
    flag_watch_interval_s: float = Field(default=2, ge=0.2, description="flagd file poll interval.")

    alerts_enabled: bool = Field(default=True, description="Run the alert evaluator job.")
    alerts_config_path: str = Field(
        default="config/alerts.yaml", description="Default alert rules (seeded by name)."
    )
    alert_interval_s: float = Field(default=15, ge=5, description="Evaluator tick interval.")
    alert_recovery_windows: int = Field(
        default=3,
        ge=1,
        description="Healthy ticks before an un-investigated incident auto-resolves.",
    )

    ingest_max_body_bytes: int = Field(
        default=16 * 1024 * 1024,
        ge=1024,
        description="Reject OTLP payloads larger than this (raw and decompressed).",
    )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
