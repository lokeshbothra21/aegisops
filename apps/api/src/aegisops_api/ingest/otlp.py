"""Pydantic models for the OTLP/JSON wire format (the subset AegisOps stores).

OTLP/JSON is the protobuf JSON mapping with a few deviations we must honour
(https://opentelemetry.io/docs/specs/otlp/#json-protobuf-encoding):

- `traceId` / `spanId` are **hex** strings, not base64. Some encoders still emit
  base64, so `normalize_id` accepts both.
- 64-bit integers (`timeUnixNano`, `asInt`, `count`, ...) arrive as **strings**.
- Enums (`kind`, `status.code`, `aggregationTemporality`) may be the integer or
  the `SPAN_KIND_SERVER` style name depending on the encoder.

Unknown fields are ignored so a newer collector never breaks ingest.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

type JsonScalar = str | int | float | bool | None
type Int64 = int | str  # protobuf int64/uint64 is a string in JSON; small values may be ints


class OtlpModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


def to_int(value: Int64 | None, default: int = 0) -> int:
    if value is None:
        return default
    return int(value)


def normalize_id(raw: str | None, hex_len: int) -> str | None:
    """Return a lowercase hex id of `hex_len` chars, or None for empty/invalid ids."""
    if not raw:
        return None
    if len(raw) == hex_len:
        try:
            bytes.fromhex(raw)
            return raw.lower()
        except ValueError:
            pass
    try:  # base64 fallback (standard protobuf JSON mapping)
        decoded = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(decoded) * 2 != hex_len:
        return None
    return decoded.hex()


class AnyValue(OtlpModel):
    """`opentelemetry.proto.common.v1.AnyValue`: exactly one field is set."""

    string_value: str | None = Field(default=None, alias="stringValue")
    bool_value: bool | None = Field(default=None, alias="boolValue")
    int_value: Int64 | None = Field(default=None, alias="intValue")
    double_value: float | None = Field(default=None, alias="doubleValue")
    bytes_value: str | None = Field(default=None, alias="bytesValue")
    array_value: ArrayValue | None = Field(default=None, alias="arrayValue")
    kvlist_value: KeyValueList | None = Field(default=None, alias="kvlistValue")

    def to_python(self) -> Any:
        if self.string_value is not None:
            return self.string_value
        if self.bool_value is not None:
            return self.bool_value
        if self.int_value is not None:
            return to_int(self.int_value)
        if self.double_value is not None:
            return self.double_value
        if self.bytes_value is not None:
            return self.bytes_value  # keep base64 as-is; JSONB cannot hold bytes
        if self.array_value is not None:
            return [v.to_python() for v in self.array_value.values]
        if self.kvlist_value is not None:
            return attributes_to_dict(self.kvlist_value.values)
        return None

    def to_text(self) -> str:
        """Log bodies: strings verbatim, everything else as compact JSON."""
        value = self.to_python()
        if isinstance(value, str):
            return value
        return json.dumps(value, separators=(",", ":"), default=str)


class ArrayValue(OtlpModel):
    values: list[AnyValue] = Field(default_factory=list)


class KeyValue(OtlpModel):
    key: str
    value: AnyValue = Field(default_factory=AnyValue)


class KeyValueList(OtlpModel):
    values: list[KeyValue] = Field(default_factory=list)


def attributes_to_dict(attributes: list[KeyValue]) -> dict[str, Any]:
    return {kv.key: kv.value.to_python() for kv in attributes}


class Resource(OtlpModel):
    attributes: list[KeyValue] = Field(default_factory=list)


class InstrumentationScope(OtlpModel):
    name: str = ""
    version: str = ""
    attributes: list[KeyValue] = Field(default_factory=list)


# --- traces ---------------------------------------------------------------

SPAN_KINDS = {0: "INTERNAL", 1: "INTERNAL", 2: "SERVER", 3: "CLIENT", 4: "PRODUCER", 5: "CONSUMER"}
STATUS_CODES = {0: "UNSET", 1: "OK", 2: "ERROR"}
TEMPORALITIES = {0: "UNSPECIFIED", 1: "DELTA", 2: "CUMULATIVE"}


def enum_name(value: int | str | None, table: dict[int, str], prefix: str, default: str) -> str:
    """Map `2` or `"SPAN_KIND_SERVER"` to `"SERVER"`; unknown values fall back to `default`."""
    if value is None:
        return default
    if isinstance(value, int):
        return table.get(value, default)
    name = value.removeprefix(prefix).upper()
    return name if name in table.values() else default


class SpanEvent(OtlpModel):
    time_unix_nano: Int64 | None = Field(default=None, alias="timeUnixNano")
    name: str = ""
    attributes: list[KeyValue] = Field(default_factory=list)


class SpanLink(OtlpModel):
    trace_id: str | None = Field(default=None, alias="traceId")
    span_id: str | None = Field(default=None, alias="spanId")
    attributes: list[KeyValue] = Field(default_factory=list)


class SpanStatus(OtlpModel):
    code: int | str | None = None
    message: str | None = None


class OtlpSpan(OtlpModel):
    trace_id: str | None = Field(default=None, alias="traceId")
    span_id: str | None = Field(default=None, alias="spanId")
    parent_span_id: str | None = Field(default=None, alias="parentSpanId")
    name: str = ""
    kind: int | str | None = None
    start_time_unix_nano: Int64 | None = Field(default=None, alias="startTimeUnixNano")
    end_time_unix_nano: Int64 | None = Field(default=None, alias="endTimeUnixNano")
    attributes: list[KeyValue] = Field(default_factory=list)
    events: list[SpanEvent] = Field(default_factory=list)
    links: list[SpanLink] = Field(default_factory=list)
    status: SpanStatus = Field(default_factory=SpanStatus)


class ScopeSpans(OtlpModel):
    scope: InstrumentationScope = Field(default_factory=InstrumentationScope)
    spans: list[OtlpSpan] = Field(default_factory=list)


class ResourceSpans(OtlpModel):
    resource: Resource = Field(default_factory=Resource)
    scope_spans: list[ScopeSpans] = Field(default_factory=list, alias="scopeSpans")


class ExportTraceServiceRequest(OtlpModel):
    resource_spans: list[ResourceSpans] = Field(default_factory=list, alias="resourceSpans")


# --- logs -----------------------------------------------------------------


class OtlpLogRecord(OtlpModel):
    time_unix_nano: Int64 | None = Field(default=None, alias="timeUnixNano")
    observed_time_unix_nano: Int64 | None = Field(default=None, alias="observedTimeUnixNano")
    severity_number: int | str | None = Field(default=None, alias="severityNumber")
    severity_text: str | None = Field(default=None, alias="severityText")
    body: AnyValue = Field(default_factory=AnyValue)
    attributes: list[KeyValue] = Field(default_factory=list)
    trace_id: str | None = Field(default=None, alias="traceId")
    span_id: str | None = Field(default=None, alias="spanId")


class ScopeLogs(OtlpModel):
    scope: InstrumentationScope = Field(default_factory=InstrumentationScope)
    log_records: list[OtlpLogRecord] = Field(default_factory=list, alias="logRecords")


class ResourceLogs(OtlpModel):
    resource: Resource = Field(default_factory=Resource)
    scope_logs: list[ScopeLogs] = Field(default_factory=list, alias="scopeLogs")


class ExportLogsServiceRequest(OtlpModel):
    resource_logs: list[ResourceLogs] = Field(default_factory=list, alias="resourceLogs")


# --- metrics --------------------------------------------------------------


class NumberDataPoint(OtlpModel):
    attributes: list[KeyValue] = Field(default_factory=list)
    time_unix_nano: Int64 | None = Field(default=None, alias="timeUnixNano")
    as_double: float | None = Field(default=None, alias="asDouble")
    as_int: Int64 | None = Field(default=None, alias="asInt")

    def value(self) -> float:
        if self.as_double is not None:
            return self.as_double
        return float(to_int(self.as_int))


class HistogramDataPoint(OtlpModel):
    attributes: list[KeyValue] = Field(default_factory=list)
    time_unix_nano: Int64 | None = Field(default=None, alias="timeUnixNano")
    count: Int64 | None = None
    sum: float | None = None
    bucket_counts: list[Int64] = Field(default_factory=list, alias="bucketCounts")
    explicit_bounds: list[float] = Field(default_factory=list, alias="explicitBounds")
    min: float | None = None
    max: float | None = None


class ExponentialBuckets(OtlpModel):
    offset: int = 0
    bucket_counts: list[Int64] = Field(default_factory=list, alias="bucketCounts")


class ExponentialHistogramDataPoint(OtlpModel):
    attributes: list[KeyValue] = Field(default_factory=list)
    time_unix_nano: Int64 | None = Field(default=None, alias="timeUnixNano")
    count: Int64 | None = None
    sum: float | None = None
    scale: int = 0
    zero_count: Int64 | None = Field(default=None, alias="zeroCount")
    positive: ExponentialBuckets = Field(default_factory=ExponentialBuckets)
    negative: ExponentialBuckets = Field(default_factory=ExponentialBuckets)
    min: float | None = None
    max: float | None = None


class QuantileValue(OtlpModel):
    quantile: float = 0.0
    value: float = 0.0


class SummaryDataPoint(OtlpModel):
    attributes: list[KeyValue] = Field(default_factory=list)
    time_unix_nano: Int64 | None = Field(default=None, alias="timeUnixNano")
    count: Int64 | None = None
    sum: float | None = None
    quantile_values: list[QuantileValue] = Field(default_factory=list, alias="quantileValues")


class Gauge(OtlpModel):
    data_points: list[NumberDataPoint] = Field(default_factory=list, alias="dataPoints")


class Sum(OtlpModel):
    data_points: list[NumberDataPoint] = Field(default_factory=list, alias="dataPoints")
    aggregation_temporality: int | str | None = Field(default=None, alias="aggregationTemporality")
    is_monotonic: bool = Field(default=False, alias="isMonotonic")


class Histogram(OtlpModel):
    data_points: list[HistogramDataPoint] = Field(default_factory=list, alias="dataPoints")
    aggregation_temporality: int | str | None = Field(default=None, alias="aggregationTemporality")


class ExponentialHistogram(OtlpModel):
    data_points: list[ExponentialHistogramDataPoint] = Field(
        default_factory=list, alias="dataPoints"
    )
    aggregation_temporality: int | str | None = Field(default=None, alias="aggregationTemporality")


class Summary(OtlpModel):
    data_points: list[SummaryDataPoint] = Field(default_factory=list, alias="dataPoints")


class OtlpMetric(OtlpModel):
    name: str = ""
    description: str = ""
    unit: str = ""
    gauge: Gauge | None = None
    sum: Sum | None = None
    histogram: Histogram | None = None
    exponential_histogram: ExponentialHistogram | None = Field(
        default=None, alias="exponentialHistogram"
    )
    summary: Summary | None = None


class ScopeMetrics(OtlpModel):
    scope: InstrumentationScope = Field(default_factory=InstrumentationScope)
    metrics: list[OtlpMetric] = Field(default_factory=list)


class ResourceMetrics(OtlpModel):
    resource: Resource = Field(default_factory=Resource)
    scope_metrics: list[ScopeMetrics] = Field(default_factory=list, alias="scopeMetrics")


class ExportMetricsServiceRequest(OtlpModel):
    resource_metrics: list[ResourceMetrics] = Field(default_factory=list, alias="resourceMetrics")
