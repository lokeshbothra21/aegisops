"""The nine read tools against Postgres with a synthetic scenario (raw SQL, no ORM)."""

import inspect
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from aegisops_tools import telemetry
from aegisops_tools.context import ToolContext
from aegisops_tools.db import engine_from_env, session_factory
from aegisops_tools.server import build_server, public_signature
from aegisops_tools.telemetry import log_signature
from aegisops_tools.untrusted import CLOSE, OPEN

NOW = datetime(2035, 5, 5, 12, 0, tzinfo=UTC)


async def seed(engine: AsyncEngine, sc: str) -> None:
    """A payment failure: checkout -> payment errors in the last 5 min, calm hour before."""
    h = lambda counts: json.dumps({"bucket_counts": counts, "explicit_bounds": [0.1, 0.5, 1.0]})  # noqa: E731
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
                {"ts": NOW + timedelta(minutes=offset), "h": h(counts), "sc": sc},
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


@pytest.fixture
async def scenario():  # type: ignore[no-untyped-def]
    engine = engine_from_env()
    sc = f"T-{uuid4().hex[:8]}"
    await seed(engine, sc)
    ctx = ToolContext(scenario_id=sc, frozen_now=NOW)
    try:
        async with session_factory(engine)() as s:
            yield s, ctx, engine
    finally:
        await engine.dispose()


async def test_error_rate_uses_counters_and_exact_error_series(scenario) -> None:  # type: ignore[no-untyped-def]
    s, ctx, _ = scenario
    r = await telemetry.get_error_rate(s, ctx, "payment", window_minutes=5)
    assert (
        r["calls"] == 18 and r["errors"] == 8
    )  # deltas from the last sample before the window (-6 min): ok 130->140, err 0->8
    assert r["error_rate"] == round(8 / 18, 4)
    assert r["errors_per_minute"] == [
        {"minute": (NOW - timedelta(minutes=2)).isoformat(timespec="seconds"), "errors": 1}
    ]


async def test_latency_percentiles_now_vs_baseline(scenario) -> None:  # type: ignore[no-untyped-def]
    s, ctx, _ = scenario
    r = await telemetry.get_latency_percentiles(s, ctx, "payment", window_minutes=5)
    assert r["now"]["samples"] == 20 and r["now"]["p95_ms"] == 975.0
    assert r["baseline_prev_hour"]["samples"] == 100 and r["baseline_prev_hour"]["p95_ms"] == 95.0
    assert r["p95_ratio"] > 9


async def test_top_error_logs_groups_by_signature(scenario) -> None:  # type: ignore[no-untyped-def]
    s, ctx, _ = scenario
    r = await telemetry.get_top_error_logs(s, ctx, "payment", window_minutes=5, limit=5)
    assert r["scanned"] == 3
    assert (
        r["groups"][0]["count"] == 2
        and r["groups"][0]["signature"] == "charge failed for order # amount #"
    )
    assert r["groups"][0]["trace_id"] and len(r["groups"][0]["sample"]) <= 500
    assert log_signature("req 0xdeadbeef took 12.5ms id 0123456789abcdef") == "req # took #ms id #"


async def test_error_traces_are_compact_trees_with_exception(scenario) -> None:  # type: ignore[no-untyped-def]
    s, ctx, _ = scenario
    r = await telemetry.get_error_traces(s, ctx, "payment", window_minutes=5, limit=2)
    assert len(r["traces"]) == 1
    spans = r["traces"][0]["spans"]
    assert [x["depth"] for x in spans] == [0, 1, 2]
    assert spans[2] == {
        "depth": 2,
        "service": "payment",
        "name": "Charge",
        "kind": "SERVER",
        "status": "ERROR",
        "ms": 200.0,
        "error": "Payment request failed. Invalid token.",
    }
    assert "attrs" not in spans[0]


async def test_compare_windows_reports_deltas_and_new_signatures(scenario) -> None:  # type: ignore[no-untyped-def]
    s, ctx, _ = scenario
    r = await telemetry.compare_windows(s, ctx, "payment", after_minutes=5, before_minutes=30)
    assert r["after"]["error_rate"] == round(8 / 18, 4)
    assert r["before"]["error_rate"] == 0.0  # 100 -> 130 ok calls, no errors
    assert r["delta"]["error_rate"] == round(8 / 18, 4)
    assert "charge failed for order # amount #" in r["new_log_signatures"]


async def test_service_dependencies_walks_two_hops(scenario) -> None:  # type: ignore[no-untyped-def]
    s, ctx, _ = scenario
    r1 = await telemetry.get_service_dependencies(s, ctx, "payment", depth=1)
    assert r1["callers"] == ["checkout"] and r1["callees"] == ["flagd"]
    r2 = await telemetry.get_service_dependencies(s, ctx, "payment", depth=2)
    assert {(e["caller"], e["callee"]) for e in r2["edges"]} == {
        ("frontend", "checkout"),
        ("checkout", "payment"),
        ("checkout", "cart"),
        ("payment", "flagd"),
    }
    edge = next(e for e in r2["edges"] if e["callee"] == "payment")
    assert edge["errors"] == 8 and edge["p95_ms"] == 250.0


async def test_recent_changes_window_and_service_filter(scenario) -> None:  # type: ignore[no-untyped-def]
    s, ctx, _ = scenario
    r = await telemetry.get_recent_changes(s, ctx, window_minutes=30)
    assert [c["type"] for c in r["changes"]] == ["flag"]
    assert (
        r["changes"][0]["after"] == {"flag": "paymentFailure", "variant": "100%"}
        and r["changes"][0]["actor"] == "flagd-file"
    )
    r2 = await telemetry.get_recent_changes(s, ctx, window_minutes=120, service="email")
    assert [c["service"] for c in r2["changes"]] == ["email"]


async def test_container_metrics_series(scenario) -> None:  # type: ignore[no-untyped-def]
    s, ctx, _ = scenario
    r = await telemetry.get_container_metrics(s, ctx, "payment", window_minutes=15)
    assert len(r["memory_bytes"]) == 3 and r["memory_bytes"][-1]["value"] == 102000000.0
    assert r["cpu_utilization"][0]["value"] == 0.1 and r["memory_percent"] == []


async def test_similar_incidents_is_an_honest_stub(scenario) -> None:  # type: ignore[no-untyped-def]
    s, ctx, _ = scenario
    r = await telemetry.search_similar_incidents(s, ctx, "payment failing", k=9)
    assert r["results"] == [] and r["k"] == 3 and "E6" in r["note"]


async def test_mcp_server_exposes_nine_wrapped_tools(scenario) -> None:  # type: ignore[no-untyped-def]
    _, ctx, engine = scenario
    server = build_server(engine, ctx)
    tools = {t.name: t for t in await server.list_tools()}
    assert set(tools) == set(telemetry.TOOLS)
    schema = tools["get_error_rate"].input_schema
    assert set(schema["properties"]) == {"service", "window_minutes"} and schema["required"] == [
        "service"
    ]
    result = await server.call_tool("get_recent_changes", {"window_minutes": 30})
    body = result.content[0].text  # type: ignore[union-attr]
    assert body.startswith(OPEN) and body.endswith(CLOSE)
    assert json.loads(body[len(OPEN) : -len(CLOSE)])["changes"][0]["type"] == "flag"


def test_public_signature_drops_session_and_ctx() -> None:
    sig = public_signature(telemetry.get_top_error_logs)
    assert list(sig.parameters) == ["service", "window_minutes", "limit"]
    assert sig.return_annotation is str
    assert inspect.signature(telemetry.get_top_error_logs).parameters["session"]


def test_context_window_is_clamped_and_shifted() -> None:
    ctx = ToolContext(frozen_now=NOW)
    start, end = ctx.window(10_000, ending_minutes_ago=5)
    assert end == NOW - timedelta(minutes=5) and (end - start) == timedelta(minutes=240)
