"""Database engine and session management (async SQLAlchemy + asyncpg).

One engine per process, created in the app lifespan and disposed on shutdown.
Request handlers get a session from `get_session`; tests get one from a fixture.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

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


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """`async with session_scope(factory) as s:` commits on success, rolls back on error.

    A context manager, not a bare async generator: a caller that `return`s from
    inside `async for` over a generator closes it at the `yield`, so the commit
    after the yield never runs (found live on 19 Sep 2026: the job runner logged
    results that were never persisted).
    """
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one committed-or-rolled-back session per request."""
    async with session_scope(request.app.state.session_factory) as session:
        yield session
