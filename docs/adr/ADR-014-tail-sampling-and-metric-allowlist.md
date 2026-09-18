# ADR-014: Tail sampling for traces and an allowlist for metrics in our collector layer

**Status:** Accepted, 17 Sep 2026 (E1.3)

## Context
The demo emits far more than a laptop Postgres should store for 24 hours: every span of every request, and thousands of metric points every ten seconds across host, container and application scrapers. The plan (NFR-04) asked for "10–20 % sampling of OK spans, keep all error spans". A plain probabilistic sampler cannot express "keep all error *traces*", because it decides per span before knowing whether a sibling errored.

## Decision
Our collector layer (`infra/otel-demo/otelcol-config-extras.yml`) adds separate `*/aegisops` pipelines fed by the same OTLP receiver, leaving the demo's own pipelines untouched.
- **Traces:** `tail_sampling` with two policies: keep every trace containing an ERROR span; probabilistically keep 15 % of the rest. `decision_wait: 5s`.
- **Metrics:** an OTTL `filter` that drops every metric not matching an allowlist (span-metrics, container memory/CPU, Kafka lag, HTTP/RPC server duration, `app.*`).
- **Logs:** unsampled.

## Alternatives considered
- **Head/probabilistic sampling.** Loses the child span that carries the exception on a trace whose root looks fine.
- **Store everything, rely on retention.** Tens of millions of metric rows per day; the laptop database and Supabase's 500 MB would fill within hours.
- **Sample in the API instead of the collector.** Wastes network and CPU on rows we discard; the collector is the right place.

## Consequences
- Measured: error traces arrive whole (77 spans/trace); 8 metric names survive the allowlist; the agent still gets per-service request/error/latency series computed on *unsampled* traffic because the span-metrics connector sits on the demo's pipeline.
- Extending alerting to a new series means editing one regex.
- Tail sampling buffers traces in memory; `num_traces: 50000` is sized for the demo, not for production scale.
