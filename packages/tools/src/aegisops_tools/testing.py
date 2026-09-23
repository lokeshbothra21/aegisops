"""Synthetic scenario fixtures shared by the tools and agent test suites.

`seed_payment_failure` writes, under one `scenario_id`, the rows a payment-failure
incident produces: span-metrics counters and histograms, one error trace with an
exception event, error logs, service edges and change events. Raw SQL, no ORM.
"""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from aegisops_tools.db import session_factory

NOW = datetime(2035, 5, 5, 12, 0, tzinfo=UTC)
"""The frozen "now" the seed is relative to."""


def _hist(counts: list[int]) -> str:
    return json.dumps({"bucket_counts": counts, "explicit_bounds": [0.1, 0.5, 1.0]})


async def seed_payment_failure(engine: AsyncEngine, sc: str) -> None:
    """A payment failure: checkout -> payment errors in the last 5 min, calm hour before."""
    async with session_factory(engine)() as s:
        # span_metrics counters (cumulative) for payment SERVER spans
        for offset, ok, err in ((-70, 100, 0), (-6, 130, 0), (-1, 140, 8)):
            for status, v in (("STATUS_CODE_UNSET", ok), ("STATUS_CODE_ERROR", err)):
                await s.execute(
                    text("""
                    INSERT INTO metric_points (ts, service, metric_name, value, attrs, scenario_id)
                    VALUES (:ts, 'payment', 'traces.span.metrics.calls', :v,
                            jsonb_build_object('span.kind','SPAN_KIND_SERVER','span.name','Charge','status.code', CAST(:st AS text)), :sc)
                """),
                    {"ts": NOW + timedelta(minutes=offset), "v": v, "st": status, "sc": sc},
                )
        # duration histograms: baseline fast, now slow
        for offset, counts in ((-70, [0, 0, 0, 0]), (-6, [100, 0, 0, 0]), (-1, [100, 0, 20, 0])):
            await s.execute(
                text("""
                INSERT INTO metric_points (ts, service, metric_name, value, unit, attrs, scenario_id)
                VALUES (:ts, 'payment', 'traces.span.metrics.duration', 0, 's',
                        jsonb_build_object('span.kind','SPAN_KIND_SERVER','span.name','Charge','status.code','STATUS_CODE_UNSET',
                                           'otel.histogram', CAST(:h AS jsonb)), :sc)
            """),
                {"ts": NOW + timedelta(minutes=offset), "h": _hist(counts), "sc": sc},
            )
        # a trace: checkout CLIENT -> payment SERVER (ERROR with exception event)
        t1 = uuid4().hex
        await s.execute(
            text("""
            INSERT INTO spans (trace_id, span_id, parent_span_id, service, name, kind, start_ts, duration_ms, status_code, status_message, attrs, scenario_id) VALUES
            (:t, 'aaaaaaaaaaaaaaaa', NULL, 'checkout', 'PlaceOrder', 'SERVER', :ts, 300, 'ERROR', 'payment failed', '{}', :sc),
            (:t, 'bbbbbbbbbbbbbbbb', 'aaaaaaaaaaaaaaaa', 'checkout', 'PaymentService/Charge', 'CLIENT', :ts, 250, 'ERROR', NULL, '{}', :sc),
            (:t, 'cccccccccccccccc', 'bbbbbbbbbbbbbbbb', 'payment', 'Charge', 'SERVER', :ts, 200, 'ERROR', 'Invalid token',
             '{"otel.events": [{"name": "exception", "attrs": {"exception.message": "Payment request failed. Invalid token."}}]}', :sc)
        """),
            {"t": t1, "ts": NOW - timedelta(minutes=2), "sc": sc},
        )
        # error logs: two lines differing only by numbers -> one signature
        for i, body in enumerate(
            (
                "charge failed for order 1001 amount 42.5",
                "charge failed for order 1002 amount 17.0",
                "connection refused",
            )
        ):
            await s.execute(
                text("""
                INSERT INTO logs (ts, service, severity_num, severity_text, body, trace_id, attrs, scenario_id)
                VALUES (:ts, 'payment', 17, 'ERROR', :body, :t, '{}', :sc)
            """),
                {"ts": NOW - timedelta(minutes=1, seconds=i), "body": body, "t": t1, "sc": sc},
            )
        # service edges for the current hour
        await s.execute(
            text("""
            INSERT INTO service_edges (window_start, caller, callee, call_count, err_count, p95_ms, scenario_id) VALUES
            (:w, 'frontend', 'checkout', 120, 10, 400, :sc), (:w, 'checkout', 'payment', 60, 8, 250, :sc),
            (:w, 'checkout', 'cart', 60, 0, 10, :sc), (:w, 'payment', 'flagd', 60, 0, 2, :sc)
        """),
            {"w": NOW.replace(minute=0, second=0, microsecond=0), "sc": sc},
        )
        # change events: the flag flip 4 min ago, an unrelated restart an hour ago
        await s.execute(
            text("""
            INSERT INTO change_events (ts, type, service, before, after, actor, scenario_id) VALUES
            (:t1, 'flag', 'payment', '{"flag":"paymentFailure","variant":"off"}', '{"flag":"paymentFailure","variant":"100%"}', 'flagd-file', :sc),
            (:t2, 'restart', 'email', '{}', '{}', 'docker', :sc)
        """),
            {"t1": NOW - timedelta(minutes=4), "t2": NOW - timedelta(minutes=70), "sc": sc},
        )
        # container metrics for payment
        for i in range(3):
            await s.execute(
                text("""
                INSERT INTO metric_points (ts, service, metric_name, value, attrs, scenario_id) VALUES
                (:ts, 'unknown_service', 'container.memory.usage.total', :mem, '{"otel.resource": {"container.name": "payment"}}', :sc),
                (:ts, 'unknown_service', 'container.cpu.utilization', :cpu, '{"otel.resource": {"container.name": "payment"}}', :sc)
            """),
                {
                    "ts": NOW - timedelta(minutes=3 - i),
                    "mem": 100e6 + i * 1e6,
                    "cpu": 0.1 * (i + 1),
                    "sc": sc,
                },
            )
        await s.commit()


async def delete_scenario(engine: AsyncEngine, sc: str) -> None:
    async with session_factory(engine)() as s:
        for table in ("spans", "logs", "metric_points", "service_edges", "change_events"):
            await s.execute(text(f"DELETE FROM {table} WHERE scenario_id = :sc"), {"sc": sc})  # noqa: S608
        await s.commit()
