"""Retention deletes only untagged rows older than the window (E1.5)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aegisops_api.db import create_engine, create_session_factory
from aegisops_api.jobs.retention import run_retention
from aegisops_api.models import Log, MetricPoint, Span
from aegisops_api.settings import Settings

NOW = datetime(
    2020, 1, 2, 12, tzinfo=UTC
)  # far PAST: the cutoff is in 2020, so real rows are never deleted


def _span(ts: datetime, scenario: str | None, trace: str) -> Span:
    return Span(
        trace_id=trace, span_id=uuid4().hex[:16], service="svc", name="op", kind="SERVER",
        start_ts=ts, duration_ms=1.0, status_code="OK", attrs={}, scenario_id=scenario,
    )  # fmt: skip


async def test_retention_keeps_tagged_and_recent_rows(settings: Settings) -> None:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    trace = uuid4().hex
    old, recent = NOW - timedelta(hours=30), NOW - timedelta(hours=1)
    try:
        async with factory() as s:
            s.add_all(
                [
                    _span(old, None, trace),  # deleted
                    _span(old, "S1", trace),  # kept: tagged fixture
                    _span(recent, None, trace),  # kept: inside window
                    Log(
                        ts=old, service="svc", severity_num=9, body="x", attrs={}, scenario_id=None
                    ),
                    MetricPoint(ts=old, service="svc", metric_name="m", value=1.0, attrs={}),
                    MetricPoint(
                        ts=old,
                        service="svc",
                        metric_name="m",
                        value=1.0,
                        attrs={},
                        scenario_id="S1",
                    ),
                ]
            )
            await s.commit()
        async with factory() as s:
            deleted = await run_retention(s, older_than=timedelta(hours=24), now=NOW)
            await s.commit()
            remaining = await _count(s, trace)
    finally:
        await engine.dispose()
    assert deleted["spans"] >= 1
    assert deleted["logs"] >= 1
    assert deleted["metric_points"] >= 1
    assert remaining == {"S1", None}  # tagged old row + untagged recent row survive


async def _count(s: AsyncSession, trace: str) -> set[str | None]:
    rows = (await s.scalars(select(Span.scenario_id).where(Span.trace_id == trace))).all()
    return set(rows)


async def test_retention_is_a_noop_when_nothing_is_old(settings: Settings) -> None:
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as s:
            before = await s.scalar(select(func.count()).select_from(Span))
            deleted = await run_retention(s, older_than=timedelta(days=3650), now=NOW)
            after = await s.scalar(select(func.count()).select_from(Span))
    finally:
        await engine.dispose()
    assert deleted == {"spans": 0, "logs": 0, "metric_points": 0}
    assert before == after
