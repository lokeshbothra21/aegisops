"""Hand-written OTLP/JSON payloads shaped like the collector's `otlphttp` exporter output.

IDs are deliberately low-entropy (repeated bytes) so `detect-secrets` stays quiet.
Helpers take a trace id so integration tests can find their own rows.
"""

from typing import Any

T0 = 1_726_000_000_000_000_000  # 2024-09-10T20:26:40Z in ns
TRACE_ID = "ab" * 16
SPAN_ID = "cd" * 8
PARENT_ID = "ef" * 8

RESOURCE = {
    "attributes": [
        {"key": "service.name", "value": {"stringValue": "checkout"}},
        {"key": "service.namespace", "value": {"stringValue": "opentelemetry-demo"}},
        {"key": "telemetry.sdk.language", "value": {"stringValue": "go"}},
    ]
}
SCOPE = {"name": "go.opentelemetry.io/otel/sdk/tracer", "version": "1.28.0"}


def traces(trace_id: str = TRACE_ID, *, enum_style: str = "int") -> dict[str, Any]:
    kind: int | str = 2 if enum_style == "int" else "SPAN_KIND_SERVER"
    code: int | str = 2 if enum_style == "int" else "STATUS_CODE_ERROR"
    return {
        "resourceSpans": [
            {
                "resource": RESOURCE,
                "scopeSpans": [
                    {
                        "scope": SCOPE,
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": SPAN_ID,
                                "parentSpanId": PARENT_ID,
                                "name": "oteldemo.CheckoutService/PlaceOrder",
                                "kind": kind,
                                "startTimeUnixNano": str(T0),
                                "endTimeUnixNano": str(T0 + 250_000_000),
                                "attributes": [
                                    {"key": "rpc.system", "value": {"stringValue": "grpc"}},
                                    {"key": "rpc.grpc.status_code", "value": {"intValue": "13"}},
                                    {"key": "app.order.amount", "value": {"doubleValue": 42.5}},
                                    {"key": "app.retry", "value": {"boolValue": False}},
                                    {
                                        "key": "app.items",
                                        "value": {
                                            "arrayValue": {
                                                "values": [
                                                    {"stringValue": "OLJCESPC7Z"},
                                                    {"intValue": "2"},
                                                ]
                                            }
                                        },
                                    },
                                ],
                                "events": [
                                    {
                                        "timeUnixNano": str(T0 + 100_000_000),
                                        "name": "exception",
                                        "attributes": [
                                            {
                                                "key": "exception.message",
                                                "value": {"stringValue": "payment declined"},
                                            }
                                        ],
                                    }
                                ],
                                "status": {"code": code, "message": "PaymentService failed"},
                            },
                            {
                                # minimal span: no parent, no status -> UNSET/INTERNAL defaults
                                "traceId": trace_id,
                                "spanId": "12" * 8,
                                "name": "child",
                                "startTimeUnixNano": str(T0),
                                "endTimeUnixNano": str(T0 + 1_000_000),
                            },
                        ],
                    }
                ],
            }
        ]
    }


def logs(trace_id: str = TRACE_ID) -> dict[str, Any]:
    return {
        "resourceLogs": [
            {
                "resource": RESOURCE,
                "scopeLogs": [
                    {
                        "scope": {"name": "checkout"},
                        "logRecords": [
                            {
                                "timeUnixNano": str(T0),
                                "severityNumber": 17,
                                "severityText": "ERROR",
                                "body": {"stringValue": "failed to charge card"},
                                "attributes": [
                                    {"key": "log.iostream", "value": {"stringValue": "stderr"}}
                                ],
                                "traceId": trace_id,
                                "spanId": SPAN_ID,
                            },
                            {
                                # structured body, no timestamp -> observed time, enum by name
                                "observedTimeUnixNano": str(T0 + 5_000_000_000),
                                "severityNumber": "SEVERITY_NUMBER_WARN2",
                                "body": {
                                    "kvlistValue": {
                                        "values": [
                                            {"key": "msg", "value": {"stringValue": "slow"}},
                                            {"key": "ms", "value": {"intValue": "900"}},
                                        ]
                                    }
                                },
                                "traceId": trace_id,
                            },
                        ],
                    }
                ],
            }
        ]
    }


def metrics(service: str = "checkout") -> dict[str, Any]:
    resource = {"attributes": [{"key": "service.name", "value": {"stringValue": service}}]}
    return {
        "resourceMetrics": [
            {
                "resource": resource,
                "scopeMetrics": [
                    {
                        "scope": {"name": "otelcol/hostmetrics"},
                        "metrics": [
                            {
                                "name": "process.runtime.go.mem.heap_alloc",
                                "unit": "By",
                                "gauge": {
                                    "dataPoints": [
                                        {"timeUnixNano": str(T0), "asInt": "1048576"},
                                    ]
                                },
                            },
                            {
                                "name": "http.server.request.count",
                                "sum": {
                                    "dataPoints": [
                                        {
                                            "timeUnixNano": str(T0),
                                            "asDouble": 12,
                                            "attributes": [
                                                {
                                                    "key": "http.response.status_code",
                                                    "value": {"intValue": "500"},
                                                }
                                            ],
                                        }
                                    ],
                                    "aggregationTemporality": 2,
                                    "isMonotonic": True,
                                },
                            },
                            {
                                "name": "http.server.request.duration",
                                "unit": "s",
                                "histogram": {
                                    "dataPoints": [
                                        {
                                            "timeUnixNano": str(T0),
                                            "count": "3",
                                            "sum": 0.9,
                                            "bucketCounts": ["1", "1", "1", "0"],
                                            "explicitBounds": [0.1, 0.5, 1.0],
                                            "min": 0.05,
                                            "max": 0.8,
                                        }
                                    ],
                                    "aggregationTemporality": "AGGREGATION_TEMPORALITY_DELTA",
                                },
                            },
                            {
                                "name": "rpc.client.duration",
                                "exponentialHistogram": {
                                    "dataPoints": [
                                        {
                                            "timeUnixNano": str(T0),
                                            "count": "2",
                                            "sum": 3.0,
                                            "scale": 3,
                                            "zeroCount": "0",
                                            "positive": {"offset": 5, "bucketCounts": ["1", "1"]},
                                        }
                                    ],
                                    "aggregationTemporality": 2,
                                },
                            },
                            {
                                "name": "queue.latency",
                                "summary": {
                                    "dataPoints": [
                                        {
                                            "timeUnixNano": str(T0),
                                            "count": "10",
                                            "sum": 5.0,
                                            "quantileValues": [
                                                {"quantile": 0.5, "value": 0.4},
                                                {"quantile": 0.99, "value": 1.2},
                                            ],
                                        }
                                    ]
                                },
                            },
                            {"name": "", "gauge": {"dataPoints": []}},  # nameless -> rejected
                        ],
                    }
                ],
            }
        ]
    }
