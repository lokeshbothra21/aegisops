"""Integration: POST /ingest/v1/* -> rows in Postgres (needs the compose database)."""

import gzip
import json
import uuid

from httpx import AsyncClient
from sqlalchemy import func, select

from aegisops_api.db import create_engine, create_session_factory
from aegisops_api.models import Log, MetricPoint, Span
from aegisops_api.settings import Settings
from tests.fixtures import otlp as fx


def fresh_trace_id() -> str:
    return uuid.uuid4().hex  # 32 hex chars, unique per test run


async def count_spans(settings: Settings, trace_id: str) -> int:
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as s:
            n = await s.scalar(select(func.count()).where(Span.trace_id == trace_id))
    finally:
        await engine.dispose()
    return int(n or 0)


async def test_traces_are_stored(client: AsyncClient, settings: Settings) -> None:
    trace_id = fresh_trace_id()
    r = await client.post("/ingest/v1/traces", json=fx.traces(trace_id))
    assert r.status_code == 200
    assert r.json() == {}
    assert await count_spans(settings, trace_id) == 2


async def test_gzip_body_is_accepted(client: AsyncClient, settings: Settings) -> None:
    trace_id = fresh_trace_id()
    body = gzip.compress(json.dumps(fx.traces(trace_id)).encode())
    r = await client.post(
        "/ingest/v1/traces",
        content=body,
        headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
    )
    assert r.status_code == 200
    assert await count_spans(settings, trace_id) == 2


async def test_partial_success_reports_rejected_spans(client: AsyncClient) -> None:
    r = await client.post("/ingest/v1/traces", json=fx.traces(trace_id="!!"))
    assert r.status_code == 200
    assert r.json()["partialSuccess"]["rejectedSpans"] == 2


async def test_logs_are_stored_with_trace_link(client: AsyncClient, settings: Settings) -> None:
    trace_id = fresh_trace_id()
    r = await client.post("/ingest/v1/logs", json=fx.logs(trace_id))
    assert r.status_code == 200
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as s:
            rows = (await s.scalars(select(Log).where(Log.trace_id == trace_id))).all()
    finally:
        await engine.dispose()
    assert {row.body for row in rows} == {"failed to charge card", '{"msg":"slow","ms":900}'}
    assert {row.service for row in rows} == {"checkout"}


async def test_metrics_are_stored_per_point(client: AsyncClient, settings: Settings) -> None:
    service = f"svc-{uuid.uuid4().hex[:8]}"
    r = await client.post("/ingest/v1/metrics", json=fx.metrics(service))
    assert r.status_code == 200
    assert r.json()["partialSuccess"]["rejectedDataPoints"] == 1
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as s:
            rows = (
                await s.scalars(select(MetricPoint).where(MetricPoint.service == service))
            ).all()
    finally:
        await engine.dispose()
    assert {row.metric_name for row in rows} == {
        "process.runtime.go.mem.heap_alloc",
        "http.server.request.count",
        "http.server.request.duration",
        "rpc.client.duration",
        "queue.latency",
    }
    hist = next(r for r in rows if r.metric_name == "http.server.request.duration")
    assert hist.attrs["otel.histogram"]["bucket_counts"] == [1, 1, 1, 0]


async def test_empty_request_is_a_no_op(client: AsyncClient) -> None:
    r = await client.post("/ingest/v1/traces", json={})
    assert r.status_code == 200
    assert r.json() == {}


async def test_protobuf_content_type_is_415_problem(client: AsyncClient) -> None:
    r = await client.post(
        "/ingest/v1/traces", content=b"\x0a\x00", headers={"Content-Type": "application/x-protobuf"}
    )
    assert r.status_code == 415
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["status"] == 415
    assert "encoding: json" in body["detail"]
    assert body["instance"] == "/ingest/v1/traces"


async def test_malformed_json_is_400_problem(client: AsyncClient) -> None:
    r = await client.post(
        "/ingest/v1/logs",
        content=b'{"resourceLogs": [',
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400
    assert r.json()["title"] == "Invalid OTLP/JSON"


async def test_wrong_shape_is_400_with_location(client: AsyncClient) -> None:
    r = await client.post("/ingest/v1/metrics", json={"resourceMetrics": "nope"})
    assert r.status_code == 400
    assert r.json()["detail"].startswith("resourceMetrics:")


async def test_bad_gzip_is_400(client: AsyncClient) -> None:
    r = await client.post(
        "/ingest/v1/traces",
        content=b"not gzip",
        headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
    )
    assert r.status_code == 400
    assert r.json()["title"] == "Bad gzip body"


async def test_unknown_content_encoding_is_415(client: AsyncClient) -> None:
    r = await client.post(
        "/ingest/v1/traces",
        content=b"{}",
        headers={"Content-Type": "application/json", "Content-Encoding": "br"},
    )
    assert r.status_code == 415


async def test_oversized_body_is_413() -> None:
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport

    from aegisops_api.main import create_app

    app = create_app(Settings(env="test", ingest_max_body_bytes=1024))
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c,
    ):
        r = await c.post("/ingest/v1/traces", json=fx.traces())
        assert r.status_code == 413
        # gzip bomb: tiny on the wire, too big decompressed
        bomb = gzip.compress(b'{"resourceSpans": [], "pad": "' + b"a" * 4096 + b'"}')
        r = await c.post(
            "/ingest/v1/traces",
            content=bomb,
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
        )
        assert r.status_code == 413
        assert "decompressed" in r.json()["detail"]


async def test_unknown_route_is_problem_json(client: AsyncClient) -> None:
    r = await client.get("/nope")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["title"] == "Not Found"
