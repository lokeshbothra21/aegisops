"""Derive `service_edges` from spans (E1.4).

An edge caller→callee exists when a SERVER (or CONSUMER) span in one service has a
parent span, in the same trace, belonging to a different service. The parent is
normally the CLIENT/PRODUCER span, so its duration includes the network hop; that
is the latency the caller experienced, and the p95 we store. An edge call is an
error if either side is ERROR.

Windows are one hour, keyed by the callee span's start time truncated to the hour.
Recomputing a window replaces its rows (delete + insert), so the job is idempotent
and can be re-run for the current hour as it fills.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession

DERIVE_SQL = text(
    """
    WITH pairs AS (
        SELECT
            date_trunc('hour', c.start_ts)                          AS window_start,
            p.service                                               AS caller,
            c.service                                               AS callee,
            p.duration_ms                                           AS duration_ms,
            (c.status_code = 'ERROR' OR p.status_code = 'ERROR')::int AS is_err
        FROM spans c
        JOIN spans p
          ON p.trace_id = c.trace_id
         AND p.span_id  = c.parent_span_id
        WHERE c.kind IN ('SERVER', 'CONSUMER')
          AND p.service <> c.service
          AND c.start_ts >= :start AND c.start_ts < :end
          AND c.scenario_id IS NOT DISTINCT FROM :scenario_id
          AND p.scenario_id IS NOT DISTINCT FROM :scenario_id
    )
    INSERT INTO service_edges
        (window_start, caller, callee, call_count, err_count, p95_ms, scenario_id)
    SELECT window_start, caller, callee,
           count(*)::int,
           sum(is_err)::int,
           coalesce(percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms), 0)::float,
           :scenario_id
    FROM pairs
    GROUP BY window_start, caller, callee
    """
)

CLEAR_SQL = text(
    """
    DELETE FROM service_edges
    WHERE window_start >= :start AND window_start < :end
      AND scenario_id IS NOT DISTINCT FROM :scenario_id
    """
)


def hour_floor(ts: datetime) -> datetime:
    return ts.replace(minute=0, second=0, microsecond=0)


async def derive_service_edges(
    session: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    scenario_id: str | None = None,
) -> int:
    """Recompute edges for callee spans with start_ts in [start, end). Returns rows written."""
    params = {"start": start, "end": end, "scenario_id": scenario_id}
    await session.execute(CLEAR_SQL, params)
    result = await session.execute(DERIVE_SQL, params)
    return int(cast(CursorResult[Any], result).rowcount or 0)


async def derive_recent_hours(
    session: AsyncSession, *, hours: int = 2, now: datetime | None = None
) -> int:
    """Hourly job: refresh the current hour and the previous `hours-1` complete hours."""
    current = hour_floor(now or datetime.now(UTC))
    start = current - timedelta(hours=hours - 1)
    end = current + timedelta(hours=1)
    return await derive_service_edges(session, start=start, end=end)
