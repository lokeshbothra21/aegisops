import pytest
from httpx import ASGITransport, AsyncClient

from aegisops_api.main import create_app
from aegisops_api.settings import Settings


@pytest.fixture
async def client() -> AsyncClient:
    app = create_app(Settings(env="test"))
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_healthz_reports_ok_and_version(client: AsyncClient) -> None:
    async with client:
        r = await client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"]


async def test_readyz_reports_checks(client: AsyncClient) -> None:
    async with client:
        r = await client.get("/readyz")
    assert r.status_code == 200
    assert r.json() == {"status": "ready", "checks": {"database": True}}
