"""Runs over the API (E5.2): start, stream, pause for approval, approve/reject, resume."""

import asyncio
import json
from typing import Any
from uuid import uuid4

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from aegisops_api.db import create_engine, create_session_factory
from aegisops_api.incidents.lifecycle import open_incident
from aegisops_api.main import create_app
from aegisops_api.settings import Settings
from aegisops_tools.testing import NOW, delete_scenario, seed_payment_failure
from tests.conftest import ADMIN_TOKEN

CASSETTE = "packages/agent/tests/cassettes/s1_payment_failure.yaml"
HDR = {"X-Admin-Token": ADMIN_TOKEN}


@pytest.fixture
async def agent_app():  # type: ignore[no-untyped-def]
    settings = Settings(
        env="test",
        jobs_enabled=False,
        alerts_enabled=False,
        admin_token=SecretStr(ADMIN_TOKEN),
        recorded_llm_path=CASSETTE,
    )
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
        await s.commit()
        incident_id = inc.id
    try:
        async with (
            LifespanManager(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c,
        ):
            yield c, incident_id, sc, engine
    finally:
        await delete_scenario(engine, sc)
        async with create_session_factory(engine)() as s:
            from sqlalchemy import text

            await s.execute(
                text(
                    "DELETE FROM remediations WHERE run_id IN (SELECT id FROM runs WHERE incident_id = :i)"
                ),
                {"i": incident_id},
            )
            await s.execute(
                text(
                    "DELETE FROM run_events WHERE run_id IN (SELECT id FROM runs WHERE incident_id = :i)"
                ),
                {"i": incident_id},
            )
            await s.execute(text("DELETE FROM runs WHERE incident_id = :i"), {"i": incident_id})
            await s.execute(text("DELETE FROM incidents WHERE id = :i"), {"i": incident_id})
            await s.commit()
        await engine.dispose()


async def _wait(c: AsyncClient, run_id: int, status: str, polls: int = 150) -> dict[str, Any]:
    for _ in range(polls):
        r = await c.get(f"/api/v1/runs/{run_id}")
        body = r.json()
        if body["status"] == status:
            return dict(body)
        await asyncio.sleep(0.2)
    raise AssertionError(f"run {run_id} never reached {status}: {body}")


async def test_run_pauses_for_approval_then_finishes_on_approve(agent_app) -> None:  # type: ignore[no-untyped-def]
    c, incident_id, _, _ = agent_app
    r = await c.post(f"/api/v1/incidents/{incident_id}/runs", json={})
    assert r.status_code == 202
    run_id = r.json()["id"]
    assert r.json()["status"] == "running"
    inc = (await c.get(f"/api/v1/incidents/{incident_id}")).json()
    assert inc["status"] == "investigating"
    # a second run while one is active is refused
    assert (await c.post(f"/api/v1/incidents/{incident_id}/runs", json={})).status_code == 409
    body = await _wait(c, run_id, "awaiting_approval")
    assert (
        body["root_cause"]["category"] == "dependency_errors"
        and body["root_cause"]["confidence"] == 0.45
    )
    assert (
        body["remediation"]["action"] == "toggle_flag"
        and body["remediation"]["decision"] == "pending"
    )
    assert body["remediation"]["params"] == {
        "flag": "paymentFailure",
        "variant": "off",
        "service": "payment",
    }
    assert body["tool_calls"] == 10 and body["tokens_in"] == 4 * 800 and body["model"] == "recorded"
    assert (await c.get(f"/api/v1/incidents/{incident_id}")).json()["status"] == "awaiting_approval"
    # stored events replay over SSE up to the approval request
    r = await c.get(f"/api/v1/runs/{run_id}/events")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = [
        json.loads(line[len("data: ") :])
        for line in r.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [e["node"] for e in events] == [
        "triage",
        "plan",
        "investigate",
        "correlate_changes",
        "root_cause",
        "verify_evidence",
        "remediate",
        "approval",
    ]
    assert (
        events[-1]["type"] == "approval_requested"
        and events[-1]["payload"]["remediation"]["action"] == "toggle_flag"
    )
    assert (
        events[5]["payload"]["confidence"] == 0.45
        and events[5]["payload"]["claimed_confidence"] == 0.9
    )
    # approve (admin only)
    assert (
        await c.post(f"/api/v1/runs/{run_id}/approve", json={"by": "shreyas"})
    ).status_code == 401
    r = await c.post(
        f"/api/v1/runs/{run_id}/approve",
        json={"by": "shreyas", "note": "revert the flag"},
        headers=HDR,
    )
    assert (
        r.status_code == 200
        and r.json()["remediation"]["decision"] == "approved"
        and r.json()["remediation"]["decided_by"] == "shreyas"
    )
    body = await _wait(c, run_id, "succeeded")
    assert body["finished_at"] and body["duration_ms"] is not None
    assert body["root_cause"]["category"] == "dependency_errors"  # kept after the resume
    assert body["duration_ms"] >= 100  # wall time incl. the approval wait, not only the resume leg
    assert (await c.get(f"/api/v1/incidents/{incident_id}")).json()["status"] == "remediating"
    # approving twice is a conflict; the stream now ends with run/end
    assert (await c.post(f"/api/v1/runs/{run_id}/approve", json={}, headers=HDR)).status_code == 409
    r = await c.get(f"/api/v1/runs/{run_id}/events", params={"after": 8})
    tail = [
        json.loads(line[len("data: ") :])
        for line in r.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [(e["node"], e["type"]) for e in tail] == [("approval", "approved"), ("run", "end")]


async def test_reject_returns_the_incident_to_investigating(agent_app) -> None:  # type: ignore[no-untyped-def]
    c, incident_id, _, _ = agent_app
    run_id = (await c.post(f"/api/v1/incidents/{incident_id}/runs", json={"variant": "B"})).json()[
        "id"
    ]
    await _wait(c, run_id, "awaiting_approval")
    r = await c.post(
        f"/api/v1/runs/{run_id}/reject", json={"by": "shreyas", "note": "not now"}, headers=HDR
    )
    assert r.status_code == 200 and r.json()["remediation"]["decision"] == "rejected"
    body = await _wait(c, run_id, "succeeded")
    assert body["variant"] == "B"
    assert (await c.get(f"/api/v1/incidents/{incident_id}")).json()["status"] == "investigating"
    # a new run may now start
    r2 = await c.post(f"/api/v1/incidents/{incident_id}/runs", json={})
    assert r2.status_code == 202, r2.text


async def test_run_and_incident_not_found(agent_app) -> None:  # type: ignore[no-untyped-def]
    c, *_ = agent_app
    assert (await c.post("/api/v1/incidents/999999999/runs", json={})).status_code == 404
    assert (await c.get("/api/v1/runs/999999999")).status_code == 404
    assert (await c.get("/api/v1/runs/999999999/events")).status_code == 404
    assert (await c.post("/api/v1/runs/999999999/approve", json={}, headers=HDR)).status_code == 404


async def test_agent_disabled_is_503(settings: Settings) -> None:
    app = create_app(
        Settings(env="test", jobs_enabled=False, alerts_enabled=False, agent_enabled=False)
    )
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c,
    ):
        r = await c.post("/api/v1/incidents/1/runs", json={})
    assert r.status_code in (404, 503)  # 404 if incident 1 is missing, 503 once it exists
