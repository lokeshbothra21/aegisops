# ADR-013: Ingest accepts OTLP/JSON only, not binary protobuf

**Status:** Accepted, 16 Sep 2026 (E1.1)

## Context
OTLP over HTTP has two encodings: binary protobuf (the default for most exporters) and JSON (the protobuf JSON mapping with OpenTelemetry-specific deviations: hex trace/span ids instead of base64, int64 fields as strings). The collector's `otlphttp` exporter can emit either (`encoding: json`).

## Decision
`/ingest/v1/{traces,logs,metrics}` accept `Content-Type: application/json` only. Binary protobuf is answered with 415 and a message that names the `encoding: json` setting. Parsing is a hand-written set of Pydantic models covering the subset we store, with `extra="ignore"` so newer collectors never break ingest.

## Alternatives considered
- **Protobuf via `opentelemetry-proto` + `google.protobuf.json_format`.** Adds a heavy dependency, and the standard protobuf JSON parser decodes `bytes` fields as base64, which silently corrupts the hex ids the collector actually sends.
- **Support both encodings.** Double the parsing paths and tests for a single producer we control (our own collector layer).

## Consequences
- Zero protobuf dependency; the parser is ~200 lines and fully unit-tested with fixture payloads.
- `normalize_id` still accepts base64 ids as a fallback for other encoders.
- If a second producer ever needs binary protobuf, add it as a separate code path behind the same conversion layer.
