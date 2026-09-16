"""Pure functions: OTLP request models -> row dicts for bulk insert.

Storage decisions (PROJECT.md §6, changelog 1.0.6):

- `service` comes from the resource attribute `service.name`; rows without one
  get `unknown_service` (the OTel SDK default) so they are never dropped.
- `attrs` holds the signal's own attributes at the top level (so tools can do
  `attrs->>'http.response.status_code'`), plus everything else under `otel.*`
  keys that cannot collide with semantic-convention names: `otel.resource`,
  `otel.scope`, `otel.events`, `otel.links`, and per-metric-type detail.
- Metrics are one row per data point. Gauge/Sum store the number. Histogram,
  ExponentialHistogram and Summary store `count` in `value` and keep the
  buckets / quantiles in `attrs["otel.histogram"]` etc. so p95 can be derived
  later (E2.3) without a wide table today.

No I/O here; everything is unit-testable with dict fixtures.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from aegisops_api.ingest import otlp
from aegisops_api.ingest.otlp import (
    SPAN_KINDS,
    STATUS_CODES,
    TEMPORALITIES,
    attributes_to_dict,
    enum_name,
    normalize_id,
    to_int,
)

UNKNOWN_SERVICE = "unknown_service"
type Row = dict[str, Any]


def ns_to_datetime(nanos: otlp.Int64 | None) -> datetime:
    n = to_int(nanos)
    return datetime.fromtimestamp(n / 1_000_000_000, tz=UTC)


def service_name(resource: otlp.Resource) -> str:
    for kv in resource.attributes:
        if kv.key == "service.name":
            value = kv.value.to_python()
            if isinstance(value, str) and value:
                return value[:128]
    return UNKNOWN_SERVICE


def _context(resource: otlp.Resource, scope: otlp.InstrumentationScope) -> dict[str, Any]:
    ctx: dict[str, Any] = {"otel.resource": attributes_to_dict(resource.attributes)}
    if scope.name:
        ctx["otel.scope"] = {"name": scope.name, "version": scope.version}
    return ctx


# --- traces ---------------------------------------------------------------


def span_row(span: otlp.OtlpSpan, service: str, ctx: dict[str, Any]) -> Row | None:
    trace_id = normalize_id(span.trace_id, 32)
    span_id = normalize_id(span.span_id, 16)
    if trace_id is None or span_id is None:
        return None  # not addressable -> cannot be evidence; drop and count
    start_ns = to_int(span.start_time_unix_nano)
    end_ns = to_int(span.end_time_unix_nano, start_ns)
    attrs: dict[str, Any] = {**attributes_to_dict(span.attributes), **ctx}
    if span.events:
        attrs["otel.events"] = [
            {
                "ts": ns_to_datetime(e.time_unix_nano).isoformat(),
                "name": e.name,
                "attrs": attributes_to_dict(e.attributes),
            }
            for e in span.events
        ]
    if span.links:
        attrs["otel.links"] = [
            {
                "trace_id": normalize_id(link.trace_id, 32),
                "span_id": normalize_id(link.span_id, 16),
                "attrs": attributes_to_dict(link.attributes),
            }
            for link in span.links
        ]
    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": normalize_id(span.parent_span_id, 16),
        "service": service,
        "name": span.name[:256],
        "kind": enum_name(span.kind, SPAN_KINDS, "SPAN_KIND_", "INTERNAL"),
        "start_ts": ns_to_datetime(start_ns),
        "duration_ms": max(end_ns - start_ns, 0) / 1_000_000,
        "status_code": enum_name(span.status.code, STATUS_CODES, "STATUS_CODE_", "UNSET"),
        "status_message": span.status.message or None,
        "attrs": attrs,
        "scenario_id": None,
    }


def spans_to_rows(req: otlp.ExportTraceServiceRequest) -> tuple[list[Row], int]:
    """Return (rows, rejected_count)."""
    rows: list[Row] = []
    rejected = 0
    for rs in req.resource_spans:
        service = service_name(rs.resource)
        for ss in rs.scope_spans:
            ctx = _context(rs.resource, ss.scope)
            for span in ss.spans:
                row = span_row(span, service, ctx)
                if row is None:
                    rejected += 1
                else:
                    rows.append(row)
    return rows, rejected


# --- logs -----------------------------------------------------------------


def log_row(rec: otlp.OtlpLogRecord, service: str, ctx: dict[str, Any]) -> Row:
    ts_ns = to_int(rec.time_unix_nano) or to_int(rec.observed_time_unix_nano)
    severity = rec.severity_number
    return {
        "ts": ns_to_datetime(ts_ns),
        "service": service,
        "severity_num": severity if isinstance(severity, int) else _severity_from_name(severity),
        "severity_text": (rec.severity_text or None) and rec.severity_text[:16],
        "body": rec.body.to_text(),
        "trace_id": normalize_id(rec.trace_id, 32),
        "span_id": normalize_id(rec.span_id, 16),
        "attrs": {**attributes_to_dict(rec.attributes), **ctx},
        "scenario_id": None,
    }


def _severity_from_name(name: str | None) -> int:
    """`SEVERITY_NUMBER_ERROR` -> 17 (the OTLP enum value)."""
    if not name:
        return 0
    base = {"TRACE": 1, "DEBUG": 5, "INFO": 9, "WARN": 13, "ERROR": 17, "FATAL": 21}
    token = name.removeprefix("SEVERITY_NUMBER_").upper()
    for level, number in base.items():
        if token.startswith(level):
            suffix = token[len(level) :]
            return number + (int(suffix) - 1 if suffix.isdigit() else 0)
    return 0


def logs_to_rows(req: otlp.ExportLogsServiceRequest) -> tuple[list[Row], int]:
    rows: list[Row] = []
    for rl in req.resource_logs:
        service = service_name(rl.resource)
        for sl in rl.scope_logs:
            ctx = _context(rl.resource, sl.scope)
            rows.extend(log_row(rec, service, ctx) for rec in sl.log_records)
    return rows, 0


# --- metrics --------------------------------------------------------------


def _point(
    metric: otlp.OtlpMetric,
    service: str,
    ctx: dict[str, Any],
    ts: otlp.Int64 | None,
    value: float,
    attributes: list[otlp.KeyValue],
    extra: dict[str, Any],
) -> Row:
    return {
        "ts": ns_to_datetime(ts),
        "service": service,
        "metric_name": metric.name[:256],
        "value": value,
        "unit": metric.unit[:32] or None,
        "attrs": {**attributes_to_dict(attributes), **ctx, **extra},
        "scenario_id": None,
    }


def _temporality(value: int | str | None) -> str:
    return enum_name(value, TEMPORALITIES, "AGGREGATION_TEMPORALITY_", "UNSPECIFIED")


def metric_rows(metric: otlp.OtlpMetric, service: str, ctx: dict[str, Any]) -> list[Row]:
    if metric.gauge is not None:
        return [
            _point(metric, service, ctx, p.time_unix_nano, p.value(), p.attributes, {})
            for p in metric.gauge.data_points
        ]
    if metric.sum is not None:
        extra = {
            "otel.sum": {
                "temporality": _temporality(metric.sum.aggregation_temporality),
                "monotonic": metric.sum.is_monotonic,
            }
        }
        return [
            _point(metric, service, ctx, p.time_unix_nano, p.value(), p.attributes, extra)
            for p in metric.sum.data_points
        ]
    if metric.histogram is not None:
        temporality = _temporality(metric.histogram.aggregation_temporality)
        return [
            _point(
                metric,
                service,
                ctx,
                p.time_unix_nano,
                float(to_int(p.count)),
                p.attributes,
                {
                    "otel.histogram": {
                        "temporality": temporality,
                        "sum": p.sum,
                        "min": p.min,
                        "max": p.max,
                        "bucket_counts": [to_int(c) for c in p.bucket_counts],
                        "explicit_bounds": p.explicit_bounds,
                    }
                },
            )
            for p in metric.histogram.data_points
        ]
    if metric.exponential_histogram is not None:
        temporality = _temporality(metric.exponential_histogram.aggregation_temporality)
        return [
            _point(
                metric,
                service,
                ctx,
                p.time_unix_nano,
                float(to_int(p.count)),
                p.attributes,
                {
                    "otel.exponential_histogram": {
                        "temporality": temporality,
                        "sum": p.sum,
                        "min": p.min,
                        "max": p.max,
                        "scale": p.scale,
                        "zero_count": to_int(p.zero_count),
                        "positive": {
                            "offset": p.positive.offset,
                            "bucket_counts": [to_int(c) for c in p.positive.bucket_counts],
                        },
                        "negative": {
                            "offset": p.negative.offset,
                            "bucket_counts": [to_int(c) for c in p.negative.bucket_counts],
                        },
                    }
                },
            )
            for p in metric.exponential_histogram.data_points
        ]
    if metric.summary is not None:
        return [
            _point(
                metric,
                service,
                ctx,
                p.time_unix_nano,
                float(to_int(p.count)),
                p.attributes,
                {
                    "otel.summary": {
                        "sum": p.sum,
                        "quantiles": {str(q.quantile): q.value for q in p.quantile_values},
                    }
                },
            )
            for p in metric.summary.data_points
        ]
    return []


def metrics_to_rows(req: otlp.ExportMetricsServiceRequest) -> tuple[list[Row], int]:
    rows: list[Row] = []
    rejected = 0
    for rm in req.resource_metrics:
        service = service_name(rm.resource)
        for sm in rm.scope_metrics:
            ctx = _context(rm.resource, sm.scope)
            for metric in sm.metrics:
                if not metric.name:
                    rejected += 1
                    continue
                rows.extend(metric_rows(metric, service, ctx))
    return rows, rejected
