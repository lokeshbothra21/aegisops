"""CORS for the web UI (ADR-021): allowed origins get the headers, others do not."""

from httpx import AsyncClient

from aegisops_api.main import create_app
from aegisops_api.settings import Settings


async def test_local_ui_may_call_the_api(client: AsyncClient) -> None:
    r = await client.get("/livez", headers={"Origin": "http://localhost:3000"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert r.headers["access-control-expose-headers"] == "Retry-After"
    assert "access-control-allow-credentials" not in r.headers  # no cookies, ever


async def test_preflight_allows_the_operator_token_and_sse_resume(client: AsyncClient) -> None:
    r = await client.options(
        "/api/v1/runs/1/approve",
        headers={
            "Origin": "http://127.0.0.1:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-admin-token",
        },
    )
    assert r.status_code == 200
    allowed = r.headers["access-control-allow-headers"].lower()
    assert "x-admin-token" in allowed and "last-event-id" in allowed
    assert "DELETE" not in r.headers["access-control-allow-methods"]


async def test_other_origins_get_no_cors_headers(client: AsyncClient) -> None:
    for origin in ("https://evil.example", "http://localhost.evil.example", "https://localhost"):
        r = await client.get("/livez", headers={"Origin": origin})
        assert "access-control-allow-origin" not in r.headers, origin


async def test_production_regex_admits_vercel_domains_only() -> None:
    regex = r"https://aegisops(-[a-z0-9-]+)?\.vercel\.app"
    app = create_app(Settings(env="test", jobs_enabled=False, cors_origin_regex=regex))
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport

    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c,
    ):
        for origin, ok in [
            ("https://aegisops.vercel.app", True),
            ("https://aegisops-git-feat-ui-someone.vercel.app", True),
            ("https://aegisops.vercel.app.evil.example", False),  # full match, not prefix
            ("http://localhost:3000", False),
        ]:
            r = await c.get("/livez", headers={"Origin": origin})
            assert ("access-control-allow-origin" in r.headers) is ok, origin
