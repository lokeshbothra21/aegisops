"""Database engine and session management (async SQLAlchemy + asyncpg).

One engine per process, created in the app lifespan and disposed on shutdown.
Request handlers get a session from `get_session`; tests get one from a fixture.
"""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from aegisops_api.settings import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,  # drop dead connections (Supabase pauses / restarts)
        echo=settings.log_level == "DEBUG",
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def ping(engine: AsyncEngine) -> bool:
    """Return True if the database answers `SELECT 1` (used by /readyz)."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield a session and commit on success, roll back on error."""
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one committed-or-rolled-back session per request."""
    async for session in session_scope(request.app.state.session_factory):
        yield session
