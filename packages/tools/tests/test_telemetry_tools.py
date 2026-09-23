"""The nine read tools against Postgres with a synthetic scenario (raw SQL, no ORM)."""

import inspect
import json
from datetime import timedelta
from uuid import uuid4

import pytest

from aegisops_tools import telemetry
from aegisops_tools.context import ToolContext
from aegisops_tools.db import engine_from_env, session_factory
from aegisops_tools.server import build_server, public_signature
from aegisops_tools.telemetry import log_signature
from aegisops_tools.testing import NOW
from aegisops_tools.testing import seed_payment_failure as seed
from aegisops_tools.untrusted import CLOSE, OPEN


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
