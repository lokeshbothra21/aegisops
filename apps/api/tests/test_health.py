from httpx import AsyncClient

from aegisops_api.main import create_app
from aegisops_api.settings import Settings


async def test_livez_reports_ok_and_version(client: AsyncClient) -> None:
    r = await client.get("/livez")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"]


async def test_readyz_is_ready_when_database_answers(client: AsyncClient) -> None:
    r = await client.get("/readyz")
    assert r.status_code == 200
    assert r.json() == {"status": "ready", "checks": {"database": True}}


async def test_readyz_is_503_when_database_unreachable() -> None:
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport

    bad = Settings(
        env="test",
        database_url="postgresql+asyncpg://x:x@127.0.0.1:1/none",  # pragma: allowlist secret
    )
    app = create_app(bad)
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c,
    ):
        r = await c.get("/readyz")
    assert r.status_code == 503
    assert r.json()["checks"]["database"] is False


async def test_integrity_error_is_409_not_503() -> None:
    """A unique-constraint violation must not be reported as a database outage."""
    from fastapi import FastAPI
    from httpx import ASGITransport
    from sqlalchemy.exc import IntegrityError

    from aegisops_api.errors import install_error_handlers

    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise IntegrityError("INSERT ...", {}, Exception("duplicate key"))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/boom")
    assert r.status_code == 409 and r.json()["title"] == "Conflict"
