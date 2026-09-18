# Day 4 · 17 Sep 2026 · Collector layer and the first live fault (PR #5, E1.3)

## What we did
Wired the demo's collector to our API with a config layer that adds our own pipelines, tail sampling and a metric allowlist; persisted the Grafana fix and version pin in our repo; added `make demo-up/down/check`, `make flag`. Proved the Week 1 exit criterion: `paymentFailure=100%` → ERROR spans in Postgres 6 s later.

## Terms introduced

**OpenTelemetry Collector.** A standalone process that *receives* telemetry (receivers), *processes* it (processors), and *exports* it (exporters), wired into *pipelines* per signal. Config is YAML. Files are merged; **arrays are replaced, not appended**, which is why we add new pipelines instead of editing the demo's.

**Receiver / processor / exporter / connector.** Receiver = input (e.g. `otlp`, `docker_stats`). Processor = transform (batch, filter, sample, add attributes). Exporter = output (`otlphttp/aegisops`, `otlp_grpc/jaeger`). Connector = an exporter of one pipeline that is a receiver of another; `span_metrics` turns traces into request/error/latency metrics.

**Fan-out.** One receiver feeding several pipelines. Our `traces/aegisops` pipeline reads the same `otlp` receiver as the demo's `traces` pipeline; both get every span.

**Head vs tail sampling.** *Head* (probabilistic) decides per span as it arrives, so it cannot know whether a later child span will error. *Tail* buffers all spans of a trace for `decision_wait` (5 s), then decides per trace with policies. Ours: keep every trace containing an ERROR span; keep 15 % of the rest. Cost: memory for the buffer (ADR-014).

**Sampling policy.** `status_code` policy (match ERROR) OR `probabilistic` policy (15 %). A trace is kept if any policy says yes.

**OTTL.** OpenTelemetry Transformation Language, used by the `filter` and `transform` processors. Our allowlist is one statement: drop the metric if `not IsMatch(name, "<regex>")`.

**Metric allowlist.** Only series alerting needs: `traces.span.metrics.*`, `container.memory.usage.total`, `container.cpu.utilization`, `kafka.consumer_group.lag`, `http.server.request.duration`, `rpc.server.duration`, `app.*`. Result: 8 metric names instead of hundreds; still enough for 5xx rate, p95, memory, lag.

**span_metrics connector.** Computes `traces.span.metrics.calls` (counter) and `traces.span.metrics.duration` (histogram) per service/span name/status. Because it sits on the demo's *unsampled* pipeline, our error rates are exact even though our stored traces are sampled.

**docker_stats receiver.** Scrapes container CPU/memory from the Docker socket. Its rows have no `service.name` (so `service = unknown_service`); the container name is in `attrs.otel.resource.container.name`. Noted for the alert evaluator.

**Exporter retry and queue.** `retry_on_failure` (exponential backoff up to 5 min) and `sending_queue` (buffer up to 2000 batches) mean an API restart loses nothing that fits in the queue.

**Batching.** Grouping many items into one request. The exporter batches by size/time (`min_size 200`, `max_size 1000`, `flush_timeout 2s`) so we send ~150 KB gzipped bodies, not one span per request.

**host.docker.internal.** A hostname that resolves from inside a container to the host machine. The collector (container) reaches the API (host process) at `http://host.docker.internal:8000/ingest`. `extra_hosts: host-gateway` makes it work on Linux too.

**Compose override files.** `docker compose -f a.yaml -f b.yaml` merges b over a. Volumes merge by container path, so mounting our extras file at the same path *replaces* the demo's stub. `deploy.resources.limits.memory` sets the container cgroup limit (Grafana 400M).

**--env-file layering.** Later env files override earlier ones; `aegisops.env` (ours) carries `DEMO_VERSION=3.0.0` and the ingest endpoint. `.gitignore` ignores `.env.*`, hence the name.

**Version pinning and `make demo-check`.** Checkout tag = pin = `VERSION` file, or `make demo-up` refuses. Reproducibility from a fresh clone.

**Collector `validate`.** `otelcol validate --config ...` parses and type-checks the merged config without starting. Exit 0 before we ever ran it live.

**Fault injection.** Deliberately breaking a system to test detection/response. Here: flipping `paymentFailure` to `100%` by editing `demo.flagd.json`; flagd hot-reloads the file within seconds.

**Time to detect (the raw material).** Toggle at 15:59:43Z; first ERROR spans stored 15:59:49Z. The alert evaluator (Week 2) will add its own delay on top; the Week 2 target is an incident open within 60 s.

**Whole-trace capture.** 77 spans per error trace arrived intact (frontend-proxy → frontend → checkout → payment → …). This is what lets the agent walk parent → child to the `exception.message`.

## Interview questions
1. *Why tail sampling instead of probabilistic?* Only tail sampling can keep every *trace* that contains an error; head sampling drops the child span that carries the exception.
2. *How do you keep accurate error rates while sampling traces?* Compute them on the unsampled pipeline with the span-metrics connector; store only the metrics.
3. *What does "arrays are replaced, not appended" imply for collector config layering?* You cannot add one exporter to an existing pipeline in a later file; you either repeat the whole list or add a new pipeline. We add pipelines.
4. *How does a container reach a process on the host?* `host.docker.internal` (Docker Desktop/OrbStack) or `extra_hosts: host-gateway` (Linux).
5. *What was the measured detection latency at the storage layer?* 6 s from flag toggle to first ERROR span in Postgres, dominated by the 5 s tail-sampling wait.
