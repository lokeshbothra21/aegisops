"""Admin routes: token auth and manual job triggers (E1.5, §7)."""

from httpx import AsyncClient

from aegisops_api.main import create_app
from aegisops_api.settings import Settings
from tests.conftest import ADMIN_TOKEN

HDR = {"X-Admin-Token": ADMIN_TOKEN}


async def test_retention_requires_token(client: AsyncClient) -> None:
    r = await client.post("/api/v1/admin/retention/run")
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")
    r = await client.post("/api/v1/admin/retention/run", headers={"X-Admin-Token": "wrong"})
    assert r.status_code == 401


async def test_retention_runs_with_token(client: AsyncClient) -> None:
    r = await client.post("/api/v1/admin/retention/run", headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert body["older_than_hours"] == 24
    assert set(body["deleted"]) == {"spans", "logs", "metric_points"}


async def test_service_edges_trigger_clamps_hours(client: AsyncClient) -> None:
    r = await client.post("/api/v1/admin/service-edges/run?hours=999", headers=HDR)
    assert r.status_code == 200
    assert r.json()["hours"] == 48


async def test_admin_is_503_when_no_token_configured() -> None:
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport

    app = create_app(Settings(env="test", jobs_enabled=False, admin_token=None))
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c,
    ):
        r = await c.post("/api/v1/admin/retention/run", headers=HDR)
    assert r.status_code == 503
    assert r.json()["title"] == "Admin disabled"
