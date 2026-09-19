"""Retention (E1.5, NFR-05): delete live telemetry older than the window.

Rows with a `scenario_id` are captured fixtures for replay/benchmark and are
never touched. Everything else is a 24 h rolling window (PROJECT.md §6).
One DELETE per table, all in the caller's transaction.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, delete
from sqlalchemy.ext.asyncio import AsyncSession

from aegisops_api.models import Log, MetricPoint, Span

type Counts = dict[str, int]


async def run_retention(
    session: AsyncSession, *, older_than: timedelta, now: datetime | None = None
) -> Counts:
    """Delete untagged rows older than `now - older_than`. Returns rows deleted per table."""
    cutoff = (now or datetime.now(UTC)) - older_than
    deleted: Counts = {}
    for name, table, ts_col in (
        ("spans", Span, Span.start_ts),
        ("logs", Log, Log.ts),
        ("metric_points", MetricPoint, MetricPoint.ts),
    ):
        result = await session.execute(
            delete(table).where(table.scenario_id.is_(None), ts_col < cutoff)
        )
        deleted[name] = int(cast(CursorResult[Any], result).rowcount or 0)
    return deleted
