"""Engine for the tools package (standalone, e.g. when run as an MCP server process)."""

import os

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DEFAULT_URL = "postgresql+asyncpg://aegis:aegis@localhost:5433/aegis"  # pragma: allowlist secret


def engine_from_env() -> AsyncEngine:
    return create_async_engine(
        os.environ.get("AEGIS_DATABASE_URL", DEFAULT_URL), pool_size=3, pool_pre_ping=True
    )


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
