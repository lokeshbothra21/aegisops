"""Shared fixtures. Integration tests need Postgres from `docker compose up -d`
(or the CI service container); AEGIS_DATABASE_URL overrides the default."""

from collections.abc import AsyncIterator

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from aegisops_api.main import create_app
from aegisops_api.settings import Settings

ADMIN_TOKEN = "test-admin-token"  # pragma: allowlist secret


@pytest.fixture
def settings() -> Settings:
    # jobs_enabled=False: the periodic retention job would delete the fixture rows
    # (dated 2024) that tests insert into the shared local database.
    return Settings(env="test", jobs_enabled=False, admin_token=SecretStr(ADMIN_TOKEN))


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    """HTTP client against an in-process app with its lifespan (engine) running."""
    app = create_app(settings)
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c,
    ):
        yield c
