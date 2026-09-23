import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from aegisops_tools.db import engine_from_env


@pytest.fixture
async def engine():  # type: ignore[no-untyped-def]
    e: AsyncEngine = engine_from_env()
    try:
        yield e
    finally:
        await e.dispose()
