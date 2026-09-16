"""Unit: OTLP/JSON -> row dicts. No database."""

import base64
from datetime import UTC, datetime

from aegisops_api.ingest import convert, otlp
from tests.fixtures import otlp as fx


def test_span_rows_carry_ids_service_timing_and_attrs() -> None:
    req = otlp.ExportTraceServiceRequest.model_validate(fx.traces())
    rows, rejected = convert.spans_to_rows(req)
    assert rejected == 0
    assert len(rows) == 2
    root, child = rows
    assert root["trace_id"] == fx.TRACE_ID
    assert root["span_id"] == fx.SPAN_ID
    assert root["parent_span_id"] == fx.PARENT_ID
    assert root["service"] == "checkout"
    assert root["kind"] == "SERVER"
    assert root["status_code"] == "ERROR"
    assert root["status_message"] == "PaymentService failed"
    assert root["start_ts"] == datetime.fromtimestamp(fx.T0 / 1e9, tz=UTC)
    assert root["duration_ms"] == 250.0
    # own attributes flat, with int64/double/bool/array decoded
    assert root["attrs"]["rpc.grpc.status_code"] == 13
    assert root["attrs"]["app.order.amount"] == 42.5
    assert root["attrs"]["app.retry"] is False
    assert root["attrs"]["app.items"] == ["OLJCESPC7Z", 2]
    # context under otel.* keys
    assert root["attrs"]["otel.resource"]["service.namespace"] == "opentelemetry-demo"
    assert root["attrs"]["otel.scope"]["name"] == fx.SCOPE["name"]
    assert root["attrs"]["otel.events"][0]["attrs"]["exception.message"] == "payment declined"
    # defaults on the minimal span
    assert child["parent_span_id"] is None
    assert child["kind"] == "INTERNAL"
    assert child["status_code"] == "UNSET"
    assert child["duration_ms"] == 1.0


def test_enums_accepted_by_name() -> None:
    req = otlp.ExportTraceServiceRequest.model_validate(fx.traces(enum_style="name"))
    rows, _ = convert.spans_to_rows(req)
    assert rows[0]["kind"] == "SERVER"
    assert rows[0]["status_code"] == "ERROR"


def test_base64_ids_are_normalised_to_hex() -> None:
    hex_trace = "0a" * 16
    b64_trace = base64.b64encode(bytes.fromhex(hex_trace)).decode()
    payload = fx.traces(trace_id=b64_trace)
    rows, rejected = convert.spans_to_rows(otlp.ExportTraceServiceRequest.model_validate(payload))
    assert rejected == 0
    assert rows[0]["trace_id"] == hex_trace


def test_span_without_valid_ids_is_rejected_not_stored() -> None:
    payload = fx.traces(trace_id="not-hex-not-base64!")
    rows, rejected = convert.spans_to_rows(otlp.ExportTraceServiceRequest.model_validate(payload))
    assert rows == []
    assert rejected == 2


def test_missing_service_name_falls_back_to_unknown_service() -> None:
    payload = fx.traces()
    payload["resourceSpans"][0]["resource"] = {"attributes": []}
    rows, _ = convert.spans_to_rows(otlp.ExportTraceServiceRequest.model_validate(payload))
    assert rows[0]["service"] == convert.UNKNOWN_SERVICE


def test_log_rows_handle_structured_bodies_and_named_severity() -> None:
    req = otlp.ExportLogsServiceRequest.model_validate(fx.logs())
    rows, rejected = convert.logs_to_rows(req)
    assert rejected == 0
    plain, structured = rows
    assert plain["body"] == "failed to charge card"
    assert plain["severity_num"] == 17
    assert plain["severity_text"] == "ERROR"
    assert plain["trace_id"] == fx.TRACE_ID
    assert plain["span_id"] == fx.SPAN_ID
    assert plain["attrs"]["log.iostream"] == "stderr"
    assert structured["body"] == '{"msg":"slow","ms":900}'
    assert structured["severity_num"] == 14  # WARN2
    assert structured["ts"] == datetime.fromtimestamp((fx.T0 + 5_000_000_000) / 1e9, tz=UTC)
    assert structured["span_id"] is None


def test_metric_rows_one_per_point_with_type_detail_in_attrs() -> None:
    req = otlp.ExportMetricsServiceRequest.model_validate(fx.metrics())
    rows, rejected = convert.metrics_to_rows(req)
    assert rejected == 1  # the nameless metric
    by_name = {r["metric_name"]: r for r in rows}
    assert set(by_name) == {
        "process.runtime.go.mem.heap_alloc",
        "http.server.request.count",
        "http.server.request.duration",
        "rpc.client.duration",
        "queue.latency",
    }
    gauge = by_name["process.runtime.go.mem.heap_alloc"]
    assert gauge["value"] == 1048576.0
    assert gauge["unit"] == "By"
    counter = by_name["http.server.request.count"]
    assert counter["value"] == 12.0
    assert counter["attrs"]["http.response.status_code"] == 500
    assert counter["attrs"]["otel.sum"] == {"temporality": "CUMULATIVE", "monotonic": True}
    hist = by_name["http.server.request.duration"]
    assert hist["value"] == 3.0
    assert hist["attrs"]["otel.histogram"] == {
        "temporality": "DELTA",
        "sum": 0.9,
        "min": 0.05,
        "max": 0.8,
        "bucket_counts": [1, 1, 1, 0],
        "explicit_bounds": [0.1, 0.5, 1.0],
    }
    exp = by_name["rpc.client.duration"]["attrs"]["otel.exponential_histogram"]
    assert exp["scale"] == 3
    assert exp["positive"] == {"offset": 5, "bucket_counts": [1, 1]}
    summary = by_name["queue.latency"]["attrs"]["otel.summary"]
    assert summary["quantiles"] == {"0.5": 0.4, "0.99": 1.2}
    assert all(r["service"] == "checkout" for r in rows)


def test_unknown_fields_are_ignored() -> None:
    payload = fx.traces()
    payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["flags"] = 256
    payload["resourceSpans"][0]["schemaUrl"] = "https://opentelemetry.io/schemas/1.26.0"
    rows, _ = convert.spans_to_rows(otlp.ExportTraceServiceRequest.model_validate(payload))
    assert len(rows) == 2
