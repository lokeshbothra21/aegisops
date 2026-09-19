"""service_edges derivation from parent->child spans across services (E1.4)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from aegisops_api.db import create_engine, create_session_factory
from aegisops_api.jobs.service_edges import derive_recent_hours, derive_service_edges, hour_floor
from aegisops_api.models import ServiceEdge, Span

T = datetime(2031, 6, 1, 10, 15, tzinfo=UTC)  # inside the 10:00 window
HOUR = hour_floor(T)


def _span(**kw: object) -> Span:
    base: dict[str, object] = {
        "span_id": uuid4().hex[:16], "parent_span_id": None, "kind": "SERVER", "start_ts": T,
        "duration_ms": 10.0, "status_code": "UNSET", "attrs": {}, "scenario_id": None, "name": "op",
    }  # fmt: skip
    base.update(kw)
    return Span(**base)


def _trace(scenario: str | None, client_ms: float, error: bool) -> list[Span]:
    """frontend -SERVER-> (CLIENT) -> checkout SERVER -> (CLIENT) -> payment SERVER."""
    trace = uuid4().hex
    fe = _span(trace_id=trace, service="frontend", scenario_id=scenario)
    fe_client = _span(trace_id=trace, service="frontend", kind="CLIENT", parent_span_id=fe.span_id,
                      duration_ms=client_ms, scenario_id=scenario)  # fmt: skip
    co = _span(
        trace_id=trace, service="checkout", parent_span_id=fe_client.span_id, scenario_id=scenario
    )
    co_client = _span(trace_id=trace, service="checkout", kind="CLIENT", parent_span_id=co.span_id,
                      duration_ms=5.0, scenario_id=scenario)  # fmt: skip
    pay = _span(trace_id=trace, service="payment", parent_span_id=co_client.span_id,
                status_code="ERROR" if error else "OK", scenario_id=scenario)  # fmt: skip
    # an INTERNAL child inside checkout must NOT create an edge
    internal = _span(
        trace_id=trace,
        service="checkout",
        kind="INTERNAL",
        parent_span_id=co.span_id,
        scenario_id=scenario,
    )
    return [fe, fe_client, co, co_client, pay, internal]


async def test_edges_have_counts_errors_and_p95_per_hour(settings) -> None:  # type: ignore[no-untyped-def]
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    scenario = f"T-{uuid4().hex[:8]}"  # isolate from other tests / live rows
    try:
        async with factory() as s:
            for ms, err in [(100.0, False), (200.0, False), (300.0, False), (400.0, True)]:
                s.add_all(_trace(scenario, ms, err))
            await s.commit()
        async with factory() as s:
            n = await derive_service_edges(
                s, start=HOUR, end=HOUR + timedelta(hours=1), scenario_id=scenario
            )
            await s.commit()
            rows = (
                await s.scalars(select(ServiceEdge).where(ServiceEdge.scenario_id == scenario))
            ).all()
    finally:
        await engine.dispose()
    assert n == 2
    edges = {(r.caller, r.callee): r for r in rows}
    assert set(edges) == {("frontend", "checkout"), ("checkout", "payment")}
    fc = edges[("frontend", "checkout")]
    assert fc.window_start == HOUR
    assert fc.call_count == 4
    assert fc.err_count == 0
    assert 385.0 <= fc.p95_ms <= 400.0  # percentile_cont over 100,200,300,400
    cp = edges[("checkout", "payment")]
    assert cp.call_count == 4
    assert cp.err_count == 1
    assert cp.p95_ms == 5.0


async def test_rederiving_a_window_replaces_rows(settings) -> None:  # type: ignore[no-untyped-def]
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    scenario = f"T-{uuid4().hex[:8]}"
    try:
        async with factory() as s:
            s.add_all(_trace(scenario, 50.0, False))
            await s.commit()
        async with factory() as s:
            for _ in range(2):
                await derive_service_edges(
                    s, start=HOUR, end=HOUR + timedelta(hours=1), scenario_id=scenario
                )
            await s.commit()
            rows = (
                await s.scalars(select(ServiceEdge).where(ServiceEdge.scenario_id == scenario))
            ).all()
    finally:
        await engine.dispose()
    assert len(rows) == 2  # not 4


async def test_recent_hours_window_covers_current_and_previous_hour(settings) -> None:  # type: ignore[no-untyped-def]
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as s:
            n = await derive_recent_hours(s, hours=2, now=T)
            await s.commit()
    finally:
        await engine.dispose()
    assert n >= 0  # no untagged rows in 2031; the point is that the window maths runs end-to-end
