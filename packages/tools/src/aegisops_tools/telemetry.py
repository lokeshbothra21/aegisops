"""The nine read tools (PROJECT.md §9.1). Async functions over an AsyncSession + ToolContext.

Each returns a plain dict (JSON-able) that the MCP server wraps with `wrap_untrusted`.
SQL only; every query filters by `scenario_id IS NOT DISTINCT FROM :scenario_id` so the
same code serves live rows (NULL) and a replayed scenario.
"""

import json
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from aegisops_tools.context import ToolContext

SERVER_KIND = "SPAN_KIND_SERVER"
ERROR_STATUS = "STATUS_CODE_ERROR"


def _p(ctx: ToolContext, start: datetime, end: datetime, **extra: Any) -> dict[str, Any]:
    return {"start": start, "end": end, "scenario_id": ctx.scenario_id, **extra}


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") if dt else None


# --- 1. error rate -----------------------------------------------------------------

_CALLS_SQL = text(
    """
    WITH pts AS (
        SELECT attrs->>'span.name' AS sn, attrs->>'status.code' AS sc, value, ts
        FROM metric_points
        WHERE metric_name = 'traces.span.metrics.calls' AND service = :service
          AND attrs->>'span.kind' = :kind
          AND ts >= CAST(:start AS timestamptz) - interval '1 hour' AND ts < :end
          AND scenario_id IS NOT DISTINCT FROM :scenario_id
    ), latest AS (
        SELECT DISTINCT ON (sn, sc) sn, sc, value FROM pts
        WHERE ts >= :start ORDER BY sn, sc, ts DESC
    ), base AS (
        SELECT DISTINCT ON (sn, sc) sn, sc, value FROM pts
        WHERE ts < :start ORDER BY sn, sc, ts DESC
    )
    , series AS (
        SELECT l.sc,
               CASE WHEN l.value >= coalesce(b.value, 0)
                    THEN l.value - coalesce(b.value, 0) ELSE l.value END AS calls
        FROM latest l LEFT JOIN base b USING (sn, sc)
    )
    SELECT coalesce(sum(calls), 0)                                  AS total,
           coalesce(sum(calls) FILTER (WHERE sc = :err), 0)         AS errors
    FROM series
    """
)

_ERRORS_PER_MINUTE_SQL = text(
    """
    SELECT date_trunc('minute', start_ts) AS m, count(*) AS n
    FROM spans
    WHERE service = :service AND kind IN ('SERVER', 'CONSUMER') AND status_code = 'ERROR'
      AND start_ts >= :start AND start_ts < :end
      AND scenario_id IS NOT DISTINCT FROM :scenario_id
    GROUP BY 1 ORDER BY 1
    """
)


async def get_error_rate(
    session: AsyncSession, ctx: ToolContext, service: str, window_minutes: int = 5
) -> dict[str, Any]:
    """Error rate for a service over the window: rate (from unsampled span-metrics counters),
    totals, and an exact per-minute series of error spans (all error traces are kept)."""
    start, end = ctx.window(window_minutes)
    total, errors = (
        await session.execute(
            _CALLS_SQL, _p(ctx, start, end, service=service, kind=SERVER_KIND, err=ERROR_STATUS)
        )
    ).one()
    series = (
        await session.execute(_ERRORS_PER_MINUTE_SQL, _p(ctx, start, end, service=service))
    ).all()
    total_f, errors_f = float(total or 0), float(errors or 0)
    return {
        "service": service,
        "window": {"start": _iso(start), "end": _iso(end)},
        "calls": int(total_f),
        "errors": int(errors_f),
        "error_rate": round(errors_f / total_f, 4) if total_f else None,
        "errors_per_minute": [{"minute": _iso(m), "errors": int(n)} for m, n in series[-30:]],
    }


# --- 2. latency percentiles -------------------------------------------------------

_HIST_SQL = text(
    """
    WITH pts AS (
        SELECT attrs->>'span.name' AS sn, attrs->>'status.code' AS sc, ts, unit,
               attrs->'otel.histogram' AS h
        FROM metric_points
        WHERE metric_name = 'traces.span.metrics.duration' AND service = :service
          AND attrs->>'span.kind' = :kind
          AND ts >= CAST(:start AS timestamptz) - interval '1 hour' AND ts < :end
          AND scenario_id IS NOT DISTINCT FROM :scenario_id
    ), latest AS (
        SELECT DISTINCT ON (sn, sc) sn, sc, h, unit FROM pts
        WHERE ts >= :start ORDER BY sn, sc, ts DESC
    ), base AS (
        SELECT DISTINCT ON (sn, sc) sn, sc, h FROM pts
        WHERE ts < :start ORDER BY sn, sc, ts DESC
    )
    SELECT b.h, l.h, l.unit FROM latest l LEFT JOIN base b USING (sn, sc)
    """
)

UNIT_TO_MS = {"ms": 1.0, "s": 1000.0, "us": 0.001, "ns": 1e-6}


def _as_dict(v: Any) -> dict[str, Any] | None:
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        loaded = json.loads(v)
        return loaded if isinstance(loaded, dict) else None
    return None


def percentile_from_buckets(bounds: list[float], counts: list[int], q: float) -> float | None:
    total = sum(counts)
    if total == 0:
        return None
    target, cum = q * total, 0
    for i, c in enumerate(counts):
        prev = cum
        cum += c
        if cum >= target and c > 0:
            lo = bounds[i - 1] if i > 0 else 0.0
            if i >= len(bounds):
                return lo
            return lo + (bounds[i] - lo) * ((target - prev) / c)
    return bounds[-1] if bounds else None


async def _percentiles_ms(
    session: AsyncSession, ctx: ToolContext, service: str, start: datetime, end: datetime
) -> dict[str, Any]:
    """Merge series with identical (ms-normalised) bucket bounds; report the largest group.

    The demo emits `traces.span.metrics.duration` in ms from most SDKs and in s from a few,
    so bounds are converted to ms before merging; layouts that differ cannot be summed.
    """
    rows = (
        await session.execute(_HIST_SQL, _p(ctx, start, end, service=service, kind=SERVER_KIND))
    ).all()
    groups: dict[tuple[float, ...], list[int]] = {}
    for first, last, unit in rows:
        f, cur = _as_dict(first), _as_dict(last)
        if cur is None:
            continue
        scale = UNIT_TO_MS.get(unit or "ms", 1.0)
        bounds = tuple(round(float(x) * scale, 6) for x in cur.get("explicit_bounds", []))
        lc = [int(x) for x in cur.get("bucket_counts", [])]
        fc = [int(x) for x in f.get("bucket_counts", [])] if f else [0] * len(lc)
        delta = [max(y - x, 0) for x, y in zip(fc, lc, strict=False)] if len(fc) == len(lc) else lc
        if len(delta) != len(bounds) + 1:
            continue
        acc = groups.setdefault(bounds, [0] * len(delta))
        for i, v in enumerate(delta):
            acc[i] += v
    out: dict[str, Any] = {"samples": 0, "p50_ms": None, "p95_ms": None, "p99_ms": None}
    if not groups:
        return out
    bounds_t, counts = max(groups.items(), key=lambda kv: sum(kv[1]))
    out["samples"] = sum(counts)
    if len(groups) > 1:
        out["bucket_layouts"] = len(groups)
    for name, q in (("p50_ms", 0.5), ("p95_ms", 0.95), ("p99_ms", 0.99)):
        pct = percentile_from_buckets(list(bounds_t), counts, q) if sum(counts) else None
        out[name] = round(pct, 1) if pct is not None else None
    return out


async def get_latency_percentiles(
    session: AsyncSession, ctx: ToolContext, service: str, window_minutes: int = 5
) -> dict[str, Any]:
    """p50/p95/p99 server-span latency now vs the previous hour (baseline), unsampled histograms."""
    start, end = ctx.window(window_minutes)
    b_start, b_end = ctx.window(60, ending_minutes_ago=window_minutes)
    now = await _percentiles_ms(session, ctx, service, start, end)
    base = await _percentiles_ms(session, ctx, service, b_start, b_end)
    ratio = (
        round(now["p95_ms"] / base["p95_ms"], 2)
        if now.get("p95_ms") and base.get("p95_ms")
        else None
    )
    return {
        "service": service,
        "window": {"start": _iso(start), "end": _iso(end)},
        "now": now,
        "baseline_prev_hour": base,
        "p95_ratio": ratio,
    }


# --- 3. top error logs -------------------------------------------------------------

_LOGS_SQL = text(
    """
    SELECT body, ts, trace_id, severity_text
    FROM logs
    WHERE service = :service AND severity_num >= 17
      AND ts >= :start AND ts < :end
      AND scenario_id IS NOT DISTINCT FROM :scenario_id
    ORDER BY ts DESC LIMIT 500
    """
)
_SIG_NUM = re.compile(r"\b0x[0-9a-f]+\b|\b[0-9a-f]{12,}\b|\d+(\.\d+)?", re.I)
_SIG_WS = re.compile(r"\s+")


def log_signature(body: str) -> str:
    """Collapse numbers, hex ids and whitespace so lines differing only by values group together."""
    return _SIG_WS.sub(" ", _SIG_NUM.sub("#", body)).strip()[:200]


async def get_top_error_logs(
    session: AsyncSession, ctx: ToolContext, service: str, window_minutes: int = 5, limit: int = 10
) -> dict[str, Any]:
    """Error-level logs grouped by signature: count, first/last seen, a sample body, a trace id."""
    start, end = ctx.window(window_minutes)
    limit = max(1, min(int(limit), 20))
    rows = (await session.execute(_LOGS_SQL, _p(ctx, start, end, service=service))).all()
    groups: dict[str, dict[str, Any]] = {}
    for body, ts, trace_id, sev in rows:
        sig = log_signature(body)
        g = groups.setdefault(
            sig,
            {
                "signature": sig,
                "count": 0,
                "first_seen": ts,
                "last_seen": ts,
                "sample": body[:500],
                "trace_id": trace_id,
                "severity": sev,
            },
        )
        g["count"] += 1
        g["first_seen"] = min(g["first_seen"], ts)
        g["last_seen"] = max(g["last_seen"], ts)
        g["trace_id"] = g["trace_id"] or trace_id
    top = sorted(groups.values(), key=lambda g: -g["count"])[:limit]
    for g in top:
        g["first_seen"], g["last_seen"] = _iso(g["first_seen"]), _iso(g["last_seen"])
    return {
        "service": service,
        "window": {"start": _iso(start), "end": _iso(end)},
        "scanned": len(rows),
        "groups": top,
    }


# --- 4. error traces ---------------------------------------------------------------

_ERROR_TRACE_IDS_SQL = text(
    """
    SELECT trace_id, min(start_ts) AS t
    FROM spans
    WHERE service = :service AND status_code = 'ERROR'
      AND start_ts >= :start AND start_ts < :end
      AND scenario_id IS NOT DISTINCT FROM :scenario_id
    GROUP BY trace_id ORDER BY t DESC LIMIT :limit
    """
)
_TRACE_SPANS_SQL = text(
    """
    SELECT span_id, parent_span_id, service, name, kind, status_code, status_message,
           duration_ms, start_ts,
           attrs->'otel.events'->0->'attrs'->>'exception.message' AS exception
    FROM spans
    WHERE trace_id = :trace_id AND scenario_id IS NOT DISTINCT FROM :scenario_id
    ORDER BY start_ts LIMIT 300
    """
)
MAX_TRACE_SPANS = 30


def focus_trace(rows: list[Any]) -> list[dict[str, Any]]:
    """Depth-first order from the roots; keep every ERROR span, its ancestors and its children,
    then fill with the remaining spans in tree order, at most MAX_TRACE_SPANS."""
    by_id = {r.span_id: r for r in rows}
    children: dict[str | None, list[Any]] = {}
    for r in rows:
        parent = r.parent_span_id if r.parent_span_id in by_id else None
        children.setdefault(parent, []).append(r)
    order: list[tuple[int, Any]] = []

    def walk(span_id: str | None, depth: int) -> None:
        for r in sorted(children.get(span_id, []), key=lambda x: x.start_ts):
            order.append((depth, r))
            walk(r.span_id, depth + 1)

    walk(None, 0)
    keep: set[str] = set()
    for _, r in order:
        if r.status_code == "ERROR":
            keep.add(r.span_id)
            parent = r.parent_span_id
            while parent in by_id:
                keep.add(parent)
                parent = by_id[parent].parent_span_id
            keep.update(c.span_id for c in children.get(r.span_id, []))
    chosen = [x for x in order if x[1].span_id in keep]
    if len(chosen) < MAX_TRACE_SPANS:
        extra = [x for x in order if x[1].span_id not in keep][: MAX_TRACE_SPANS - len(chosen)]
        chosen = sorted(chosen + extra, key=order.index)
    out = []
    for depth, r in chosen[:MAX_TRACE_SPANS]:
        entry: dict[str, Any] = {
            "depth": depth,
            "service": r.service,
            "name": r.name[:80],
            "kind": r.kind,
            "status": r.status_code,
            "ms": round(float(r.duration_ms), 1),
        }
        if r.status_code == "ERROR":
            entry["error"] = (r.exception or r.status_message or "")[:200]
        out.append(entry)
    return out


async def get_error_traces(
    session: AsyncSession, ctx: ToolContext, service: str, window_minutes: int = 5, limit: int = 3
) -> dict[str, Any]:
    """Most recent traces with an ERROR span in the service, as compact span trees (no attrs).

    Each tree is focused: error spans, their ancestors and children first, then other spans
    in depth-first order, at most 30 spans."""
    start, end = ctx.window(window_minutes)
    limit = max(1, min(int(limit), 5))
    ids = (
        await session.execute(
            _ERROR_TRACE_IDS_SQL, _p(ctx, start, end, service=service, limit=limit)
        )
    ).all()
    traces = []
    for trace_id, _t in ids:
        rows = (
            await session.execute(
                _TRACE_SPANS_SQL, {"trace_id": trace_id, "scenario_id": ctx.scenario_id}
            )
        ).all()
        traces.append(
            {"trace_id": trace_id, "total_spans": len(rows), "spans": focus_trace(list(rows))}
        )
    return {
        "service": service,
        "window": {"start": _iso(start), "end": _iso(end)},
        "traces": traces,
    }


# --- 5. compare windows ------------------------------------------------------------


async def compare_windows(
    session: AsyncSession,
    ctx: ToolContext,
    service: str,
    after_minutes: int = 5,
    before_minutes: int = 30,
) -> dict[str, Any]:
    """Deltas between the last `after_minutes` and the `before_minutes` before them.

    Error rate, p95 and log signatures that appeared.
    """
    a_start, a_end = ctx.window(after_minutes)
    b_start, b_end = ctx.window(before_minutes, ending_minutes_ago=after_minutes)

    async def summarise(start: datetime, end: datetime) -> dict[str, Any]:
        total, errors = (
            await session.execute(
                _CALLS_SQL, _p(ctx, start, end, service=service, kind=SERVER_KIND, err=ERROR_STATUS)
            )
        ).one()
        pct = await _percentiles_ms(session, ctx, service, start, end)
        logs = (await session.execute(_LOGS_SQL, _p(ctx, start, end, service=service))).all()
        sigs: dict[str, int] = {}
        for body, *_ in logs:
            s = log_signature(body)
            sigs[s] = sigs.get(s, 0) + 1
        t = float(total or 0)
        return {
            "calls": int(t),
            "error_rate": round(float(errors or 0) / t, 4) if t else None,
            "p95_ms": pct.get("p95_ms"),
            "signatures": sigs,
        }

    before, after = await summarise(b_start, b_end), await summarise(a_start, a_end)
    new_sigs = [s for s in after["signatures"] if s not in before["signatures"]]
    return {
        "service": service,
        "before": {
            "start": _iso(b_start),
            "end": _iso(b_end),
            "calls": before["calls"],
            "error_rate": before["error_rate"],
            "p95_ms": before["p95_ms"],
        },
        "after": {
            "start": _iso(a_start),
            "end": _iso(a_end),
            "calls": after["calls"],
            "error_rate": after["error_rate"],
            "p95_ms": after["p95_ms"],
        },
        "delta": {
            "error_rate": round(after["error_rate"] - before["error_rate"], 4)
            if after["error_rate"] is not None and before["error_rate"] is not None
            else None,
            "p95_ms": round(after["p95_ms"] - before["p95_ms"], 1)
            if after["p95_ms"] is not None and before["p95_ms"] is not None
            else None,
        },
        "new_log_signatures": new_sigs[:10],
    }


# --- 6. service dependencies -------------------------------------------------------

_EDGES_SQL = text(
    """
    SELECT caller, callee, sum(call_count) AS calls, sum(err_count) AS errs, max(p95_ms) AS p95
    FROM service_edges
    WHERE (caller = ANY(CAST(:services AS text[])) OR callee = ANY(CAST(:services AS text[])))
      AND window_start >= :start AND window_start < :end
      AND scenario_id IS NOT DISTINCT FROM :scenario_id
    GROUP BY caller, callee
    """
)


async def get_service_dependencies(
    session: AsyncSession, ctx: ToolContext, service: str, depth: int = 1, hours: int = 3
) -> dict[str, Any]:
    """Callers and callees from `service_edges` (last `hours`), expanded `depth` hops (<= 2)."""
    depth = max(1, min(int(depth), 2))
    # edges are labelled by the hour they START in, so include the hour that contains `now`
    start = ctx.now - timedelta(hours=max(1, min(int(hours), 24)))
    end = ctx.now + timedelta(hours=1)
    frontier, seen, edges = {service}, {service}, {}
    for _ in range(depth):
        rows = (
            await session.execute(_EDGES_SQL, _p(ctx, start, end, services=list(frontier)))
        ).all()
        nxt: set[str] = set()
        for caller, callee, calls, errs, p95 in rows:
            edges[(caller, callee)] = {
                "caller": caller,
                "callee": callee,
                "calls": int(calls),
                "errors": int(errs),
                "p95_ms": round(float(p95), 1),
            }
            nxt.update({caller, callee})
        frontier = nxt - seen
        seen |= nxt
    ordered = sorted(edges.values(), key=lambda e: -e["calls"])[:40]
    return {
        "service": service,
        "depth": depth,
        "callers": sorted({e["caller"] for e in ordered if e["callee"] == service}),
        "callees": sorted({e["callee"] for e in ordered if e["caller"] == service}),
        "edges": ordered,
    }


# --- 7. recent changes -------------------------------------------------------------

_CHANGES_SQL = text(
    """
    SELECT ts, type, service, before, after, actor
    FROM change_events
    WHERE ts >= :start AND ts < :end
      AND (CAST(:service AS text) IS NULL OR service = CAST(:service AS text) OR service IS NULL)
      AND scenario_id IS NOT DISTINCT FROM :scenario_id
    ORDER BY ts DESC LIMIT 30
    """
)


async def get_recent_changes(
    session: AsyncSession, ctx: ToolContext, window_minutes: int = 60, service: str | None = None
) -> dict[str, Any]:
    """Change events (flag/deploy/restart/scale) in the window, newest first; optional service."""
    start, end = ctx.window(window_minutes)
    rows = (await session.execute(_CHANGES_SQL, _p(ctx, start, end, service=service))).all()
    return {
        "window": {"start": _iso(start), "end": _iso(end)},
        "changes": [
            {
                "ts": _iso(ts),
                "type": str(t),
                "service": svc,
                "before": _as_dict(b) or {},
                "after": _as_dict(a) or {},
                "actor": actor,
            }
            for ts, t, svc, b, a, actor in rows
        ],
    }


# --- 8. container metrics ----------------------------------------------------------

_CONTAINER_SQL = text(
    """
    SELECT metric_name, date_trunc('minute', ts) AS m, avg(value) AS v
    FROM metric_points
    WHERE metric_name IN ('container.cpu.utilization', 'container.memory.usage.total',
                          'container.memory.percent')
      AND attrs->'otel.resource'->>'container.name' = :service
      AND ts >= :start AND ts < :end
      AND scenario_id IS NOT DISTINCT FROM :scenario_id
    GROUP BY 1, 2 ORDER BY 2
    """
)


async def get_container_metrics(
    session: AsyncSession, ctx: ToolContext, service: str, window_minutes: int = 15
) -> dict[str, Any]:
    """Per-minute CPU and memory of the service's container (container name = service name)."""
    start, end = ctx.window(window_minutes)
    rows = (await session.execute(_CONTAINER_SQL, _p(ctx, start, end, service=service))).all()
    series: dict[str, list[dict[str, Any]]] = {
        "cpu_utilization": [],
        "memory_bytes": [],
        "memory_percent": [],
    }
    key = {
        "container.cpu.utilization": "cpu_utilization",
        "container.memory.usage.total": "memory_bytes",
        "container.memory.percent": "memory_percent",
    }
    for name, m, v in rows:
        series[key[name]].append(
            {"minute": _iso(m), "value": round(float(v), 3 if name.endswith("utilization") else 1)}
        )
    return {
        "service": service,
        "window": {"start": _iso(start), "end": _iso(end)},
        **{k: v[-30:] for k, v in series.items()},
    }


# --- 9. similar incidents (E6, not built yet) --------------------------------------


async def search_similar_incidents(
    session: AsyncSession, ctx: ToolContext, text_query: str, k: int = 3
) -> dict[str, Any]:
    """Past postmortems similar to the query. Incident memory (E6) is not built yet: empty list."""
    return {
        "query": text_query[:200],
        "k": max(1, min(int(k), 3)),
        "results": [],
        "note": "incident memory not available yet (E6, Week 10)",
    }


ToolFn = Callable[..., Awaitable[dict[str, Any]]]

TOOLS: dict[str, ToolFn] = {
    "get_error_rate": get_error_rate,
    "get_latency_percentiles": get_latency_percentiles,
    "get_top_error_logs": get_top_error_logs,
    "get_error_traces": get_error_traces,
    "compare_windows": compare_windows,
    "get_service_dependencies": get_service_dependencies,
    "get_recent_changes": get_recent_changes,
    "get_container_metrics": get_container_metrics,
    "search_similar_incidents": search_similar_incidents,
}
