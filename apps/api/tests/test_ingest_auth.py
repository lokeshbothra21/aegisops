"""E9.8 / ADR-024: the ingest token. Checked before the body is read; open on a laptop when
unset, closed (503) in prod when unset."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from aegisops_api.main import create_app
from aegisops_api.settings import Settings

INGEST_TOKEN = "test-ingest-token"  # pragma: allowlist secret
EMPTY_LOGS = {"resourceLogs": []}


@asynccontextmanager
async def client_for(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c,
    ):
        yield c


def with_token(**kw: object) -> Settings:
    return Settings(env="test", jobs_enabled=False, ingest_token=SecretStr(INGEST_TOKEN), **kw)  # type: ignore[arg-type]


@pytest.mark.parametrize("signal", ["traces", "logs", "metrics"])
async def test_right_token_is_accepted_on_every_signal(signal: str) -> None:
    body = {"traces": {"resourceSpans": []}, "logs": EMPTY_LOGS, "metrics": {"resourceMetrics": []}}
    async with client_for(with_token()) as c:
        r = await c.post(
            f"/ingest/v1/{signal}",
            json=body[signal],
            headers={"Authorization": f"Bearer {INGEST_TOKEN}"},
        )
    assert r.status_code == 200


@pytest.mark.parametrize(
    "auth",
    [None, "", f"Basic {INGEST_TOKEN}", "Bearer wrong", "Bearer", f"Bearer {INGEST_TOKEN}x"],
)
async def test_missing_or_wrong_token_is_401(auth: str | None) -> None:
    headers = {"Authorization": auth} if auth is not None else {}
    async with client_for(with_token()) as c:
        r = await c.post("/ingest/v1/logs", json=EMPTY_LOGS, headers=headers)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert r.headers["content-type"].startswith("application/problem+json")


async def test_token_is_checked_before_the_body() -> None:
    """A wrong token gets 401 even for a body that would otherwise be 415 or 413: we do not
    read, let alone decompress, what an unauthenticated sender posts."""
    async with client_for(with_token(ingest_max_body_bytes=1024)) as c:
        r = await c.post(
            "/ingest/v1/logs",
            content=b"x" * 4096,
            headers={"Content-Type": "text/plain", "Authorization": "Bearer wrong"},
        )
    assert r.status_code == 401


async def test_scheme_is_case_insensitive() -> None:
    async with client_for(with_token()) as c:
        r = await c.post(
            "/ingest/v1/logs", json=EMPTY_LOGS, headers={"Authorization": f"bearer {INGEST_TOKEN}"}
        )
    assert r.status_code == 200


async def test_no_token_is_open_locally() -> None:
    async with client_for(Settings(env="local", jobs_enabled=False, ingest_token=None)) as c:
        r = await c.post("/ingest/v1/logs", json=EMPTY_LOGS)
    assert r.status_code == 200


async def test_no_token_in_prod_fails_closed() -> None:
    async with client_for(Settings(env="prod", jobs_enabled=False, ingest_token=None)) as c:
        r = await c.post("/ingest/v1/logs", json=EMPTY_LOGS)
    assert r.status_code == 503
    assert "AEGIS_INGEST_TOKEN" in r.json()["detail"]
