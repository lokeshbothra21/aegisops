"""Alert evaluator and readers (E2.3).

Counters -> rates, histogram deltas -> p95, breach streaks -> incidents.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aegisops_api.alerts import readers
from aegisops_api.alerts.evaluator import (
    Evaluator,
    RuleSpec,
    ensure_default_rules,
    load_rules_file,
)
from aegisops_api.alerts.readers import percentile_from_buckets
from aegisops_api.db import create_engine, create_session_factory
from aegisops_api.incidents.lifecycle import transition
from aegisops_api.models import AlertRule, Comparator, Incident, IncidentStatus, MetricPoint
from aegisops_api.settings import Settings

NOW = datetime(2034, 3, 1, 12, 0, tzinfo=UTC)
WINDOW = timedelta(seconds=120)


# --- pure functions -----------------------------------------------------------


def test_percentile_interpolates_within_the_crossing_bucket() -> None:
    bounds = [0.1, 0.5, 1.0]
    # 10 obs: 8 in (0,0.1], 1 in (0.1,0.5], 1 in (0.5,1.0], 0 above -> p95 lands in the 3rd bucket
    assert percentile_from_buckets(bounds, [8, 1, 1, 0], 0.95) == pytest.approx(0.75)
    assert percentile_from_buckets(bounds, [0, 0, 0, 0], 0.95) is None
    # everything in the unbounded last bucket -> its lower bound
    assert percentile_from_buckets(bounds, [0, 0, 0, 5], 0.95) == 1.0


def test_rules_file_loads_and_names_known_metrics() -> None:
    specs = load_rules_file("config/alerts.yaml")
    assert {s.metric for s in specs} <= set(readers.READERS)
    assert {s.name for s in specs} >= {"high-error-rate", "p95-latency-regression"}


# --- readers against Postgres ---------------------------------------------------


def _calls(
    service: str, scenario: str, status: str, values: list[float], span_name: str = "op"
) -> list[MetricPoint]:
    """Cumulative span_metrics.calls points, one every 10 s ending at NOW."""
    n = len(values)
    return [
        MetricPoint(
            ts=NOW - timedelta(seconds=10 * (n - i)),
            service=service,
            metric_name=readers.CALLS_METRIC,
            value=v,
            attrs={"span.kind": readers.SERVER_KIND, "span.name": span_name, "status.code": status},
            scenario_id=scenario,
        )
        for i, v in enumerate(values)
    ]


def _hist(service: str, scenario: str, ts: datetime, counts: list[int]) -> MetricPoint:
    return MetricPoint(
        ts=ts,
        service=service,
        metric_name=readers.DURATION_METRIC,
        value=float(sum(counts)),
        unit="s",
        attrs={
            "span.kind": readers.SERVER_KIND,
            "span.name": "op",
            "status.code": "STATUS_CODE_UNSET",
            "otel.histogram": {"bucket_counts": counts, "explicit_bounds": [0.1, 0.5, 1.0]},
        },
        scenario_id=scenario,
    )


async def test_error_rate_uses_counter_deltas_and_min_calls(settings: Settings) -> None:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    sc = f"T-{uuid4().hex[:8]}"
    try:
        async with factory() as s:
            # payment (13 points, 10 s apart = the first one is BEFORE the 120 s window and is the
            # baseline): OK counter 100 -> 180 (+80 in window), ERROR counter 0 -> 40 -> 40/120
            s.add_all(
                _calls(
                    "payment",
                    sc,
                    "STATUS_CODE_UNSET",
                    [100] + [100 + 8 * i for i in range(1, 11)] + [180, 180],
                )
            )
            s.add_all(
                _calls(
                    "payment",
                    sc,
                    "STATUS_CODE_ERROR",
                    [0] + [4 * i for i in range(1, 11)] + [40, 40],
                )
            )
            # a brand-new series (no sample before the window) counts from zero: 3 < MIN_CALLS
            s.add_all(_calls("quiet", sc, "STATUS_CODE_ERROR", [0, 3]))
            await s.commit()
            got = await readers.error_rate(s, NOW - WINDOW, NOW, sc)
    finally:
        await engine.dispose()
    assert got == {"payment": pytest.approx(40 / 120)}


async def test_p95_and_ratio_from_histogram_deltas(settings: Settings) -> None:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    sc = f"T-{uuid4().hex[:8]}"
    try:
        async with factory() as s:
            # baseline hour: 100 fast requests (all <= 0.1 s)
            s.add(_hist("checkout", sc, NOW - timedelta(minutes=55), [0, 0, 0, 0]))
            s.add(_hist("checkout", sc, NOW - timedelta(minutes=10), [100, 0, 0, 0]))
            # current window: 20 more requests, all in (0.5, 1.0] -> p95 ~ 0.975 s
            s.add(_hist("checkout", sc, NOW - timedelta(seconds=100), [100, 0, 0, 0]))
            s.add(_hist("checkout", sc, NOW - timedelta(seconds=10), [100, 0, 20, 0]))
            await s.commit()
            now_p95 = await readers.p95_ms(s, NOW - WINDOW, NOW, sc)
            ratio = await readers.p95_ratio(s, NOW - WINDOW, NOW, sc)
    finally:
        await engine.dispose()
    assert now_p95["checkout"] == pytest.approx(975.0)
    assert ratio["checkout"] > 9  # baseline p95 = 0.095 s -> ratio ~ 10


async def test_error_count_counts_error_server_spans(settings: Settings) -> None:
    from aegisops_api.models import Span

    engine = create_engine(settings)
    factory = create_session_factory(engine)
    sc = f"T-{uuid4().hex[:8]}"

    def span(service: str, kind: str, status: str, age_s: int) -> Span:
        return Span(
            trace_id=uuid4().hex,
            span_id=uuid4().hex[:16],
            service=service,
            name="op",
            kind=kind,
            start_ts=NOW - timedelta(seconds=age_s),
            duration_ms=1.0,
            status_code=status,
            attrs={},
            scenario_id=sc,
        )

    try:
        async with factory() as s:
            s.add_all(
                [
                    span("payment", "SERVER", "ERROR", 10),
                    span("payment", "SERVER", "ERROR", 20),
                    span("payment", "CLIENT", "ERROR", 30),  # client side: not counted
                    span("payment", "SERVER", "OK", 40),  # not an error
                    span("payment", "SERVER", "ERROR", 500),  # outside the window
                    span("checkout", "CONSUMER", "ERROR", 15),
                ]
            )
            await s.commit()
            got = await readers.error_count(s, NOW - WINDOW, NOW, sc)
    finally:
        await engine.dispose()
    assert got == {"payment": 2.0, "checkout": 1.0}


async def test_container_memory_and_kafka_readers(settings: Settings) -> None:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    sc = f"T-{uuid4().hex[:8]}"
    try:
        async with factory() as s:
            for v in (80.0, 100.0):
                s.add(
                    MetricPoint(
                        ts=NOW - timedelta(seconds=30),
                        service="unknown_service",
                        metric_name="container.memory.percent",
                        value=v,
                        attrs={"otel.resource": {"container.name": "email"}},
                        scenario_id=sc,
                    )
                )
            s.add(
                MetricPoint(
                    ts=NOW - timedelta(seconds=30),
                    service="kafka",
                    metric_name="kafka.consumer_group.lag",
                    value=1500.0,
                    attrs={"group": "accounting"},
                    scenario_id=sc,
                )
            )
            await s.commit()
            mem = await readers.container_memory_pct(s, NOW - WINDOW, NOW, sc)
            lag = await readers.kafka_lag(s, NOW - WINDOW, NOW, sc)
    finally:
        await engine.dispose()
    assert mem == {"email": 90.0}
    assert lag == {"accounting": 1500.0}


# --- evaluator ------------------------------------------------------------------


async def _rule(
    s: AsyncSession, name: str, metric: str, threshold: float, for_windows: int
) -> AlertRule:
    r = AlertRule(
        name=name,
        metric=metric,
        comparator=Comparator.gt,
        threshold=threshold,
        window_s=120,
        for_windows=for_windows,
    )
    s.add(r)
    await s.flush()
    return r


async def test_streak_opens_one_incident_then_auto_resolves(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    sc = f"T-{uuid4().hex[:8]}"
    metric = f"fake_{sc}"
    feed: dict[str, float] = {}

    async def fake_reader(
        session: AsyncSession, start: datetime, end: datetime, scenario_id: str | None
    ) -> dict[str, float]:
        return dict(feed)

    monkeypatch.setitem(readers.READERS, metric, fake_reader)
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    ev = Evaluator(scenario_id=sc, recovery_windows=2)
    try:
        async with factory() as s:
            await _rule(s, f"rule-{sc}", metric, threshold=0.05, for_windows=2)
            await s.commit()
        async with factory() as s:
            feed.update({"payment": 0.5, "cart": 0.0})
            r1 = await ev.tick(s, now=NOW)  # streak 1: no incident yet
            assert [e.opened_incident_id for e in r1 if e.rule == f"rule-{sc}"] == [None, None]
            r2 = await ev.tick(s, now=NOW + timedelta(seconds=30))  # streak 2: opens
            opened = [e for e in r2 if e.opened_incident_id]
            assert len(opened) == 1 and opened[0].service == "payment" and opened[0].streak == 2
            r3 = await ev.tick(s, now=NOW + timedelta(seconds=60))  # still breaching: no duplicate
            assert not [e for e in r3 if e.opened_incident_id]
            await s.commit()
            inc = await s.get(Incident, opened[0].opened_incident_id)
            assert inc is not None and inc.status is IncidentStatus.open
            assert "payment = 0.5 > 0.05" in (inc.summary or "")
            feed["payment"] = 0.0  # recovery
            await ev.tick(s, now=NOW + timedelta(seconds=90))
            r5 = await ev.tick(s, now=NOW + timedelta(seconds=120))
            await s.commit()
            assert [e.resolved_incident_id for e in r5 if e.service == "payment"] == [inc.id]
            await s.refresh(inc)
            assert inc.status is IncidentStatus.resolved and "auto-resolved" in (inc.summary or "")
    finally:
        await _drop_rule(factory, f"rule-{sc}")
        await engine.dispose()


async def _drop_rule(factory: Any, name: str) -> None:
    from sqlalchemy import delete

    async with factory() as s:
        await s.execute(
            delete(Incident).where(
                Incident.alert_rule_id.in_(select(AlertRule.id).where(AlertRule.name == name))
            )
        )
        await s.execute(delete(AlertRule).where(AlertRule.name == name))
        await s.commit()


async def test_investigated_incident_is_not_auto_resolved(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    sc = f"T-{uuid4().hex[:8]}"
    metric = f"fake_{sc}"
    feed = {"checkout": 1.0}

    async def fake_reader(*_: Any) -> dict[str, float]:
        return dict(feed)

    monkeypatch.setitem(readers.READERS, metric, fake_reader)
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    ev = Evaluator(scenario_id=sc, recovery_windows=1)
    try:
        async with factory() as s:
            await _rule(s, f"rule-{sc}", metric, threshold=0.5, for_windows=1)
            r = await ev.tick(s, now=NOW)
            inc = await s.get(
                Incident, next(e.opened_incident_id for e in r if e.opened_incident_id)
            )
            assert inc is not None
            transition(inc, IncidentStatus.investigating)  # the agent picked it up
            feed["checkout"] = 0.0
            r2 = await ev.tick(s, now=NOW + timedelta(seconds=30))
            await s.commit()
            assert all(e.resolved_incident_id is None for e in r2)
            await s.refresh(inc)
            assert inc.status is IncidentStatus.investigating
    finally:
        await _drop_rule(factory, f"rule-{sc}")
        await engine.dispose()


async def test_ensure_default_rules_is_idempotent_and_respects_edits(settings: Settings) -> None:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    name = f"seed-{uuid4().hex[:8]}"
    specs = [RuleSpec(name=name, metric="error_rate", comparator=Comparator.gt, threshold=0.05)]
    try:
        async with factory() as s:
            assert await ensure_default_rules(s, specs) == 1
            await s.commit()
            row = (await s.scalars(select(AlertRule).where(AlertRule.name == name))).one()
            row.threshold = 0.2  # operator tunes it in the DB
            await s.commit()
            assert await ensure_default_rules(s, specs) == 0  # not re-inserted
            await s.refresh(row)
            assert row.threshold == 0.2  # and not overwritten
            with pytest.raises(ValueError, match="unknown metric"):
                await ensure_default_rules(
                    s, [RuleSpec(name="x", metric="nope", comparator=Comparator.gt, threshold=1)]
                )
    finally:
        await _drop_rule(factory, name)
        await engine.dispose()
