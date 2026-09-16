"""Application settings, loaded from environment variables (12-factor).

Every value has a safe local default so `uv run uvicorn` works with no .env file.
Production values come from Cloud Run environment variables and Secret Manager.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
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


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
