"""Metric readers: one SQL aggregate per rule metric, returning {service: value} for a window.

Why not the spans table: our stored spans are tail-sampled (all error traces, 15 % of
the rest), so any rate computed from them is biased. The collector's span_metrics
connector counts on UNSAMPLED traffic and exports cumulative counters every ~10 s;
a window's count per series is the newest value in the window minus the last
sample before the window (zero for a series that did not exist yet).

Readers return nothing for a service when there is not enough data to be meaningful
(`MIN_CALLS`), so a quiet service can never trip a rate rule on one request.
"""

import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

type Readings = dict[str, float]
type Reader = Callable[[AsyncSession, datetime, datetime, str | None], Awaitable[Readings]]

MIN_CALLS = 5  # below this a rate is noise; the demo's payment path sees ~5 calls/min at 5 VUs
CALLS_METRIC = "traces.span.metrics.calls"
DURATION_METRIC = "traces.span.metrics.duration"
SERVER_KIND = "SPAN_KIND_SERVER"
ERROR_STATUS = "STATUS_CODE_ERROR"

_ERROR_RATE_SQL = text(
    """
    WITH pts AS (
        SELECT service, attrs->>'span.name' AS sn, attrs->>'status.code' AS sc, value, ts
        FROM metric_points
        WHERE metric_name = :metric
          AND attrs->>'span.kind' = :kind
          AND ts >= CAST(:start AS timestamptz) - interval '1 hour' AND ts < :end
          AND scenario_id IS NOT DISTINCT FROM :scenario_id
    ), latest AS (
        SELECT DISTINCT ON (service, sn, sc) service, sn, sc, value
        FROM pts WHERE ts >= :start ORDER BY service, sn, sc, ts DESC
    ), base AS (
        SELECT DISTINCT ON (service, sn, sc) service, sn, sc, value
        FROM pts WHERE ts < :start ORDER BY service, sn, sc, ts DESC
    ), series AS (
        -- window count = latest - last sample before the window (0 for a brand-new series);
        -- a counter that went DOWN was reset, so its latest value is the count
        SELECT l.service, l.sc,
               CASE WHEN l.value >= coalesce(b.value, 0) THEN l.value - coalesce(b.value, 0)
                    ELSE l.value END AS calls
        FROM latest l LEFT JOIN base b USING (service, sn, sc)
    )
    SELECT service,
           sum(calls)                                        AS total,
           coalesce(sum(calls) FILTER (WHERE sc = :err), 0)  AS errors
    FROM series
    GROUP BY service
    """
)


async def error_rate(
    session: AsyncSession, start: datetime, end: datetime, scenario_id: str | None
) -> Readings:
    rows = await session.execute(
        _ERROR_RATE_SQL,
        {
            "metric": CALLS_METRIC,
            "kind": SERVER_KIND,
            "err": ERROR_STATUS,
            "start": start,
            "end": end,
            "scenario_id": scenario_id,
        },
    )
    out: Readings = {}
    for service, total, errors in rows:
        if total and total >= MIN_CALLS:
            out[service] = float(errors) / float(total)
    return out


_HIST_ENDPOINTS_SQL = text(
    """
    WITH pts AS (
        SELECT service, attrs->>'span.name' AS sn, attrs->>'status.code' AS sc, ts, unit,
               attrs->'otel.histogram' AS h
        FROM metric_points
        WHERE metric_name = :metric
          AND attrs->>'span.kind' = :kind
          AND ts >= CAST(:start AS timestamptz) - interval '1 hour' AND ts < :end
          AND scenario_id IS NOT DISTINCT FROM :scenario_id
    ), latest AS (
        SELECT DISTINCT ON (service, sn, sc) service, sn, sc, h, unit
        FROM pts WHERE ts >= :start ORDER BY service, sn, sc, ts DESC
    ), base AS (
        SELECT DISTINCT ON (service, sn, sc) service, sn, sc, h
        FROM pts WHERE ts < :start ORDER BY service, sn, sc, ts DESC
    )
    SELECT l.service, b.h AS first_h, l.h AS last_h, l.unit
    FROM latest l LEFT JOIN base b USING (service, sn, sc)
    """
)


def _delta_buckets(
    first: dict[str, Any] | None, last: dict[str, Any]
) -> tuple[list[float], list[int]]:
    """Window distribution = latest buckets minus the last sample before the window.

    No earlier sample means the series is new and counts from zero.
    """
    bounds = [float(b) for b in last.get("explicit_bounds", [])]
    b = last.get("bucket_counts", [])
    a = first.get("bucket_counts", []) if first else [0] * len(b)
    if len(a) != len(b) or len(b) != len(bounds) + 1:
        return bounds, [
            int(x) for x in b
        ]  # series restarted or shape changed: use the latest as-is
    deltas = [max(int(y) - int(x), 0) for x, y in zip(a, b, strict=True)]
    return bounds, deltas


def _as_dict(value: object) -> dict[str, Any] | None:
    """JSONB from a raw `text()` query may arrive decoded (dict) or as a JSON string."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else None
    return None


def percentile_from_buckets(bounds: list[float], counts: list[int], q: float) -> float | None:
    """Linear interpolation inside the bucket where the cumulative count crosses q.

    Buckets are (-inf, b0], (b0, b1], ..., (b_{n-1}, +inf). The last, unbounded bucket
    returns its lower bound (there is nothing to interpolate towards).
    """
    total = sum(counts)
    if total == 0:
        return None
    target = q * total
    cum = 0
    for i, c in enumerate(counts):
        prev = cum
        cum += c
        if cum >= target and c > 0:
            lo = bounds[i - 1] if i > 0 else 0.0
            if i >= len(bounds):
                return lo
            hi = bounds[i]
            return lo + (hi - lo) * ((target - prev) / c)
    return bounds[-1] if bounds else None


UNIT_TO_MS = {"ms": 1.0, "s": 1000.0, "us": 0.001, "ns": 1e-6}


async def p95_ms(
    session: AsyncSession, start: datetime, end: datetime, scenario_id: str | None
) -> Readings:
    """p95 of server-span duration per service over the window, in ms.

    Series merge only when their (unit-normalised) bucket bounds match; per service the
    largest such group wins. The demo mixes `ms` and `s` histograms across SDKs.
    """
    rows = await session.execute(
        _HIST_ENDPOINTS_SQL,
        {
            "metric": DURATION_METRIC,
            "kind": SERVER_KIND,
            "start": start,
            "end": end,
            "scenario_id": scenario_id,
        },
    )
    groups: dict[tuple[str, tuple[float, ...]], list[int]] = {}
    for service, first_h, last_h, unit in rows:
        first_h, last_h = _as_dict(first_h), _as_dict(last_h)
        if last_h is None:
            continue
        scale = UNIT_TO_MS.get(unit or "ms", 1.0)
        bounds = tuple(round(float(b) * scale, 6) for b in last_h.get("explicit_bounds", []))
        _, counts = _delta_buckets(first_h, last_h)
        if len(counts) != len(bounds) + 1:
            continue
        acc = groups.setdefault((service, bounds), [0] * len(counts))
        for i, v in enumerate(counts):
            acc[i] += v
    best: dict[str, tuple[tuple[float, ...], list[int]]] = {}
    for (service, bounds), counts in groups.items():
        if service not in best or sum(counts) > sum(best[service][1]):
            best[service] = (bounds, counts)
    out: Readings = {}
    for service, (bounds, counts) in best.items():
        if sum(counts) < MIN_CALLS:
            continue
        p = percentile_from_buckets(list(bounds), counts, 0.95)
        if p is not None:
            out[service] = p  # already in ms
    return out


async def p95_ratio(
    session: AsyncSession, start: datetime, end: datetime, scenario_id: str | None
) -> Readings:
    """p95 now / p95 over the previous hour (baseline). Services without a baseline are skipped."""
    window = end - start
    now = await p95_ms(session, start, end, scenario_id)
    if not now:
        return {}
    base = await p95_ms(session, start - timedelta(hours=1), start, scenario_id)
    return {
        s: now[s] / base[s] for s in now if s in base and base[s] > 0 and window.total_seconds() > 0
    }


_CONTAINER_MEM_SQL = text(
    """
    SELECT attrs->'otel.resource'->>'container.name' AS container, avg(value) AS pct
    FROM metric_points
    WHERE metric_name = 'container.memory.percent'
      AND ts >= :start AND ts < :end
      AND scenario_id IS NOT DISTINCT FROM :scenario_id
    GROUP BY 1
    """
)


async def container_memory_pct(
    session: AsyncSession, start: datetime, end: datetime, scenario_id: str | None
) -> Readings:
    """Average memory % per container over the window. Demo container names equal service names."""
    rows = await session.execute(
        _CONTAINER_MEM_SQL, {"start": start, "end": end, "scenario_id": scenario_id}
    )
    return {c: float(p) for c, p in rows if c}


_KAFKA_LAG_SQL = text(
    """
    SELECT coalesce(attrs->>'group', service) AS grp, max(value) AS lag
    FROM metric_points
    WHERE metric_name IN ('kafka.consumer_group.lag', 'kafka.consumer_group.lag_sum')
      AND ts >= :start AND ts < :end
      AND scenario_id IS NOT DISTINCT FROM :scenario_id
    GROUP BY 1
    """
)


async def kafka_lag(
    session: AsyncSession, start: datetime, end: datetime, scenario_id: str | None
) -> Readings:
    rows = await session.execute(
        _KAFKA_LAG_SQL, {"start": start, "end": end, "scenario_id": scenario_id}
    )
    return {g: float(lag) for g, lag in rows if g}


_ERROR_COUNT_SQL = text(
    """
    SELECT service, count(*) AS errors
    FROM spans
    WHERE kind IN ('SERVER', 'CONSUMER')
      AND status_code = 'ERROR'
      AND start_ts >= :start AND start_ts < :end
      AND scenario_id IS NOT DISTINCT FROM :scenario_id
    GROUP BY service
    """
)


async def error_count(
    session: AsyncSession, start: datetime, end: datetime, scenario_id: str | None
) -> Readings:
    """ERROR server spans per service in the window, from the spans table.

    Exact despite sampling: tail sampling keeps EVERY trace with an error. It is the
    fast signal (spans land ~6 s after the fact) that complements the rate rule, which
    needs enough total calls to be meaningful.
    """
    rows = await session.execute(
        _ERROR_COUNT_SQL, {"start": start, "end": end, "scenario_id": scenario_id}
    )
    return {service: float(n) for service, n in rows}


READERS: dict[str, Reader] = {
    "error_rate": error_rate,
    "error_count": error_count,
    "p95_ratio": p95_ratio,
    "container_memory_pct": container_memory_pct,
    "kafka_lag": kafka_lag,
}
