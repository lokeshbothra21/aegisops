"""Public run admission (E9.4): visitor runs end at the proposal, one running run per
visitor, a daily cap with a cached-run fallback, admin bypass, and scenario replay."""

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.requests import Request

from aegisops_api.db import create_engine, create_session_factory
from aegisops_api.incidents.lifecycle import open_incident
from aegisops_api.main import create_app
from aegisops_api.models import Run, RunStatus, Scenario
from aegisops_api.runs.limits import client_ip, utc_midnight, visitor_key
from aegisops_api.settings import Settings
from aegisops_tools.testing import NOW, delete_scenario, seed_payment_failure
from tests.conftest import ADMIN_TOKEN

CASSETTE = "packages/agent/tests/cassettes/s1_payment_failure.yaml"
HDR = {"X-Admin-Token": ADMIN_TOKEN}
VISITOR = "203.0.113.7"  # TEST-NET-3: never a real client
XFF = {"X-Forwarded-For": VISITOR}


def _settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "env": "test",
        "jobs_enabled": False,
        "alerts_enabled": False,
        "admin_token": SecretStr(ADMIN_TOKEN),
        "recorded_llm_path": CASSETTE,
        "verify_delay_s": 0.2,
        "public_mode": True,
        "trust_forwarded_for": True,
        "public_daily_run_cap": 1000,
    }
    return Settings(**{**base, **over})


class World:
    """One app, one seeded scenario with its incident; cleaned up afterwards."""

    def __init__(self, c: AsyncClient, engine: AsyncEngine, sc: str, incidents: list[int]) -> None:
        self.c, self.engine, self.sc, self.incident_id = c, engine, sc, incidents[0]
        self.incidents = incidents  # every incident here is deleted afterwards
        self.settings: Settings = c._transport.app.state.settings  # type: ignore[attr-defined]

    async def other_incident(self) -> int:
        async with create_session_factory(self.engine)() as s:
            inc = await open_incident(
                s, service="cart", alert_rule_id=None, summary="x", scenario_id=None, now=NOW
            )
            await s.commit()
            self.incidents.append(inc.id)
            return inc.id

    async def add_run(
        self, status: RunStatus, *, by: str | None, age: timedelta, incident_id: int | None = None
    ) -> int:
        async with create_session_factory(self.engine)() as s:
            started = datetime.now(UTC) - age
            run = Run(
                incident_id=incident_id or self.incident_id,
                thread_id=f"t-{uuid4().hex}",
                status=status,
                requested_by=by,
                started_at=started,
                finished_at=None if status is RunStatus.running else started,
            )
            s.add(run)
            await s.commit()
            return run.id


@pytest.fixture
def world_factory() -> Callable[..., Any]:
    async def make(**over: Any) -> AsyncIterator[World]:
        settings = _settings(**over)
        app = create_app(settings)
        engine = create_engine(settings)
        sc = f"T-{uuid4().hex[:8]}"
        await seed_payment_failure(engine, sc)
        async with create_session_factory(engine)() as s:
            inc = await open_incident(
                s,
                service="payment",
                alert_rule_id=None,
                summary="high-error-rate: payment 0.83",
                scenario_id=sc,
                now=NOW,
            )
            s.add(
                Scenario(
                    key=sc,
                    title="payment failure",
                    fault_type="flag",
                    expected_service="payment",
                    expected_category="dependency_errors",
                    expected_actions=["toggle_flag"],
                    window_start=NOW - timedelta(minutes=10),
                    window_end=NOW + timedelta(minutes=5),
                )
            )
            await s.commit()
            incidents = [inc.id]
        try:
            async with (
                LifespanManager(app),
                AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c,
            ):
                yield World(c, engine, sc, incidents)
        finally:
            await delete_scenario(engine, sc)
            async with create_session_factory(engine)() as s:
                runs = "SELECT id FROM runs WHERE incident_id = ANY(:i)"
                ids = {"i": incidents}
                for table in ("remediations", "run_events", "audit_log"):
                    await s.execute(
                        text(f"DELETE FROM {table} WHERE run_id IN ({runs})"),
                        ids,
                    )
                await s.execute(text("DELETE FROM runs WHERE incident_id = ANY(:i)"), ids)
                await s.execute(text("DELETE FROM incidents WHERE id = ANY(:i)"), ids)
                await s.execute(text("DELETE FROM scenarios WHERE key = :k"), {"k": sc})
                await s.commit()
            await engine.dispose()

    return make


async def _wait(c: AsyncClient, run_id: int, status: str, polls: int = 150) -> dict[str, Any]:
    for _ in range(polls):
        body = (await c.get(f"/api/v1/runs/{run_id}")).json()
        if body["status"] == status:
            return dict(body)
        await asyncio.sleep(0.2)
    raise AssertionError(f"run {run_id} never reached {status}: {body}")


async def test_visitor_run_ends_at_the_proposal(world_factory) -> None:  # type: ignore[no-untyped-def]
    async for w in world_factory():
        r = await w.c.post(f"/api/v1/incidents/{w.incident_id}/runs", json={}, headers=XFF)
        assert r.status_code == 202 and r.json()["served"] == "new", r.text
        run_id = r.json()["id"]
        body = await _wait(w.c, run_id, "succeeded")
        assert body["remediation"]["action"] == "toggle_flag"
        assert body["remediation"]["decision"] == "not_offered"
        assert body["root_cause"]["category"] == "dependency_errors"
        async with create_session_factory(w.engine)() as s:
            who = (
                await s.execute(text("SELECT requested_by FROM runs WHERE id = :r"), {"r": run_id})
            ).scalar()
            types = (
                await s.execute(
                    text("SELECT type FROM run_events WHERE run_id = :r ORDER BY seq"),
                    {"r": run_id},
                )
            ).scalars()
            types = list(types)
        assert who == visitor_key(VISITOR, w.settings) and VISITOR not in str(who)
        assert types[-2:] == ["approval_not_offered", "end"]
        # nobody can approve it afterwards: the run is over, not paused
        approve = await w.c.post(f"/api/v1/runs/{run_id}/approve", json={}, headers=HDR)
        assert approve.status_code == 409


async def test_one_running_run_per_visitor_and_forged_entries_are_ignored(world_factory) -> None:  # type: ignore[no-untyped-def]
    async for w in world_factory():
        key = visitor_key(VISITOR, w.settings)
        # the visitor's run on some other incident is still running
        other = await w.add_run(
            RunStatus.running,
            by=key,
            age=timedelta(minutes=1),
            incident_id=await w.other_incident(),
        )
        forged = {"X-Forwarded-For": f"198.51.100.1, {VISITOR}"}  # left entry is client-supplied
        r = await w.c.post(f"/api/v1/incidents/{w.incident_id}/runs", json={}, headers=forged)
        assert r.status_code == 429 and r.headers["retry-after"] == "30"
        assert f"run {other}" in r.json()["detail"]
        # a different visitor is not affected
        r2 = await w.c.post(
            f"/api/v1/incidents/{w.incident_id}/runs",
            json={},
            headers={"X-Forwarded-For": "203.0.113.8"},
        )
        assert r2.status_code == 202, r2.text
        await _wait(w.c, r2.json()["id"], "succeeded")


async def test_a_stalled_run_blocks_nobody(world_factory) -> None:  # type: ignore[no-untyped-def]
    async for w in world_factory():
        key = visitor_key(VISITOR, w.settings)
        await w.add_run(RunStatus.running, by=key, age=timedelta(minutes=11))
        r = await w.c.post(f"/api/v1/incidents/{w.incident_id}/runs", json={}, headers=XFF)
        assert r.status_code == 202, r.text
        await _wait(w.c, r.json()["id"], "succeeded")


async def test_daily_cap_serves_the_last_finished_run(world_factory) -> None:  # type: ignore[no-untyped-def]
    async for w in world_factory(public_daily_run_cap=0):
        r = await w.c.post(f"/api/v1/incidents/{w.incident_id}/runs", json={}, headers=XFF)
        assert r.status_code == 429
        assert 0 < int(r.headers["retry-after"]) <= 86400
        assert "00:00 UTC" in r.json()["detail"]
        done = await w.add_run(RunStatus.succeeded, by="admin", age=timedelta(hours=1))
        r = await w.c.post(f"/api/v1/incidents/{w.incident_id}/runs", json={}, headers=XFF)
        assert r.status_code == 200 and r.json()["served"] == "cached"
        assert r.json()["id"] == done


async def test_admin_is_not_limited_and_still_gets_the_approval_pause(world_factory) -> None:  # type: ignore[no-untyped-def]
    async for w in world_factory(public_daily_run_cap=0):
        r = await w.c.post(
            f"/api/v1/incidents/{w.incident_id}/runs", json={}, headers={**HDR, **XFF}
        )
        assert r.status_code == 202 and r.json()["served"] == "new", r.text
        body = await _wait(w.c, r.json()["id"], "awaiting_approval")
        assert body["remediation"]["decision"] == "pending"


async def test_replay_starts_then_joins_the_live_run(world_factory) -> None:  # type: ignore[no-untyped-def]
    async for w in world_factory():
        paused = await w.add_run(RunStatus.awaiting_approval, by="admin", age=timedelta(0))
        r = await w.c.post(f"/api/v1/scenarios/{w.sc}/replay", headers=XFF)
        assert r.status_code == 200 and r.json()["served"] == "joined"
        assert r.json()["id"] == paused
        async with create_session_factory(w.engine)() as s:
            await s.execute(
                text("UPDATE runs SET status = 'succeeded' WHERE id = :r"), {"r": paused}
            )
            await s.commit()
        r = await w.c.post(f"/api/v1/scenarios/{w.sc}/replay", headers=XFF)
        assert r.status_code == 202 and r.json()["served"] == "new", r.text
        assert r.json()["incident_id"] == w.incident_id
        await _wait(w.c, r.json()["id"], "succeeded")


async def test_replay_unknown_or_empty_scenario_is_404(world_factory) -> None:  # type: ignore[no-untyped-def]
    async for w in world_factory():
        assert (await w.c.post("/api/v1/scenarios/nope-404/replay")).status_code == 404
        async with create_session_factory(w.engine)() as s:
            await s.execute(
                text("UPDATE incidents SET scenario_id = NULL WHERE id = :i"),
                {"i": w.incident_id},
            )
            await s.commit()
        r = await w.c.post(f"/api/v1/scenarios/{w.sc}/replay")
        assert r.status_code == 404 and r.json()["title"] == "Scenario has no incident"


def _request(headers: dict[str, str], client: tuple[str, int] = ("10.0.0.1", 1)) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "client": client})


def test_client_ip_uses_the_socket_peer_unless_forwarded_is_trusted() -> None:
    req = _request({"X-Forwarded-For": "1.1.1.1, 2.2.2.2"})
    assert client_ip(req, Settings(env="test")) == "10.0.0.1"
    assert client_ip(req, Settings(env="test", trust_forwarded_for=True)) == "2.2.2.2"
    assert client_ip(_request({}), Settings(env="test", trust_forwarded_for=True)) == "10.0.0.1"


def test_utc_midnight() -> None:
    now = datetime(2026, 9, 27, 23, 59, tzinfo=UTC)
    assert utc_midnight(now) == datetime(2026, 9, 27, tzinfo=UTC)
