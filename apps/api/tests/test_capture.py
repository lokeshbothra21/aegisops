"""Scenario capture (E1.6): tag a window, upsert the catalogue row, expose it (§7)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import select, text

from aegisops_api.db import create_engine, create_session_factory
from aegisops_api.models import Log, Scenario, Span
from aegisops_api.scenarios.capture import capture_window
from aegisops_api.settings import Settings
from tests.conftest import ADMIN_TOKEN

T0 = datetime(2036, 2, 2, 10, 0, tzinfo=UTC)  # far future: no real rows live here


def _span(ts: datetime, svc: str) -> Span:
    return Span(
        trace_id=uuid4().hex,
        span_id=uuid4().hex[:16],
        service=svc,
        name="op",
        kind="SERVER",
        start_ts=ts,
        duration_ms=1.0,
        status_code="OK",
        attrs={},
    )


async def test_capture_tags_only_the_window_and_upserts_the_row(settings: Settings) -> None:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    svc, key = f"cap-{uuid4().hex[:6]}", f"CAP-{uuid4().hex[:6]}"
    try:
        async with factory() as s:
            s.add_all(
                [
                    _span(T0 - timedelta(minutes=1), svc),  # before window
                    _span(T0 + timedelta(minutes=1), svc),  # inside
                    _span(T0 + timedelta(minutes=4), svc),  # inside
                    _span(T0 + timedelta(minutes=6), svc),  # after
                    Log(
                        ts=T0 + timedelta(minutes=2),
                        service=svc,
                        severity_num=9,
                        body="x",
                        attrs={},
                    ),
                ]
            )
            await s.commit()
        async with factory() as s:
            res = await capture_window(
                s,
                key=key,
                start=T0,
                end=T0 + timedelta(minutes=5),
                title="t",
                fault_type="flag",
                expected_service=svc,
                expected_category="dependency_errors",
                expected_actions=["toggle_flag"],
            )
            await s.commit()
            tagged = (
                await s.scalars(
                    select(Span.scenario_id).where(Span.service == svc).order_by(Span.start_ts)
                )
            ).all()
            log_tag = await s.scalar(select(Log.scenario_id).where(Log.service == svc))
            row = (await s.scalars(select(Scenario).where(Scenario.key == key))).one()
            first_actions, first_end = list(row.expected_actions), row.window_end
            # re-capture with a narrower window: old tags released, new ones applied
            res2 = await capture_window(
                s, key=key, start=T0, end=T0 + timedelta(minutes=2), title="t2", fault_type="flag"
            )
            await s.commit()
            tagged2 = (
                await s.scalars(
                    select(Span.scenario_id).where(Span.service == svc).order_by(Span.start_ts)
                )
            ).all()
            await s.refresh(row)
            await s.execute(text("DELETE FROM spans WHERE service = :s"), {"s": svc})
            await s.execute(text("DELETE FROM logs WHERE service = :s"), {"s": svc})
            await s.execute(text("DELETE FROM scenarios WHERE key = :k"), {"k": key})
            await s.commit()
    finally:
        await engine.dispose()
    assert res.tagged["spans"] == 2 and res.tagged["logs"] == 1
    assert "service_edges_derived" in res.tagged  # edges derived for the window before tagging
    assert tagged == [None, key, key, None] and log_tag == key
    assert first_actions == ["toggle_flag"] and first_end == T0 + timedelta(minutes=5)
    assert res2.tagged["spans"] == 1 and tagged2 == [None, key, None, None]
    assert row.title == "t2"


async def test_capture_rejects_empty_window(settings: Settings) -> None:
    import pytest

    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as s:
            with pytest.raises(ValueError, match="after"):
                await capture_window(s, key="X", start=T0, end=T0, title="t", fault_type="flag")
    finally:
        await engine.dispose()


async def test_scenario_endpoints(client: AsyncClient) -> None:
    key = f"API-{uuid4().hex[:6]}"
    body = {
        "key": key,
        "window_start": T0.isoformat(),
        "window_end": (T0 + timedelta(minutes=3)).isoformat(),
        "title": "payment failure",
        "fault_type": "flag",
        "expected_service": "payment",
        "expected_category": "dependency_errors",
        "expected_actions": ["toggle_flag"],
    }
    r = await client.post("/api/v1/admin/capture", json=body)
    assert r.status_code == 401
    r = await client.post(
        "/api/v1/admin/capture", json=body, headers={"X-Admin-Token": ADMIN_TOKEN}
    )
    assert (
        r.status_code == 200
        and r.json()["key"] == key
        and set(r.json()["tagged"]) >= {"spans", "incidents"}
    )
    r = await client.get(f"/api/v1/scenarios/{key}")
    assert r.status_code == 200 and r.json()["expected_actions"] == ["toggle_flag"]
    r = await client.get("/api/v1/scenarios")
    assert key in {s["key"] for s in r.json()}
    r = await client.post(
        "/api/v1/admin/capture",
        json={**body, "window_end": T0.isoformat()},
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    assert r.status_code == 422 and r.json()["title"] == "Invalid window"
    r = await client.get("/api/v1/scenarios/nope")
    assert r.status_code == 404
    r = await client.post(
        "/api/v1/admin/capture",
        json={**body, "key": "bad key!"},
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    assert r.status_code == 422
    # cleanup
    from aegisops_api.db import create_engine, create_session_factory

    engine = create_engine(client._transport.app.state.settings)  # type: ignore[attr-defined]
    try:
        async with create_session_factory(engine)() as s:
            await s.execute(text("DELETE FROM scenarios WHERE key = :k"), {"k": key})
            await s.commit()
    finally:
        await engine.dispose()
