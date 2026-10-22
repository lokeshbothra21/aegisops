# Connect your own app

AegisOps reads OpenTelemetry. Anything that emits traces, logs and metrics over OTLP can feed it, not only the OpenTelemetry Demo. This guide connects your own service in five steps:

1. Instrument the app with an OTel SDK
2. Run an OTel Collector that exports to AegisOps
3. Set `encoding: json` (the gotcha)
4. Check that data arrives
5. Know what works beyond the demo

The data path is the same one the demo uses:

```
your app --OTLP (gRPC or HTTP, any encoding)--> OTel Collector --OTLP/HTTP JSON--> AegisOps /ingest/v1/{traces,logs,metrics} --> Postgres
```

Before you start, have AegisOps running locally (see [Run locally](../README.md#run-locally)):

```bash
make db-up && make db-migrate     # Postgres 17 + pgvector on :5433
make api                          # API on http://localhost:8000
```

## 1. Instrument the app

Use the official zero-code (auto) instrumentation for your language. You don't need to change any code to get server spans, HTTP metrics and logs:

- **Python:** [opentelemetry-instrument](https://opentelemetry.io/docs/zero-code/python/)
- **Node.js:** [@opentelemetry/auto-instrumentations-node](https://opentelemetry.io/docs/zero-code/js/)
- **Java:** [the Java agent](https://opentelemetry.io/docs/zero-code/java/agent/)

Other languages work the same way: any OTel SDK that exports OTLP.

Two settings matter, through the [standard SDK environment variables](https://opentelemetry.io/docs/languages/sdk-configuration/otlp-exporter/):

```bash
export OTEL_SERVICE_NAME=my-service                  # becomes `service` on every row
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf     # to the collector; any OTLP protocol is fine here
```

- **`OTEL_SERVICE_NAME` is how AegisOps tells services apart.** Alerts, incidents and the agent's tools all key on it. Without it, rows are stored as `unknown_service` and every app lands in the same bucket.
- **Point the SDK at the collector, not at AegisOps.** SDKs default to binary protobuf, which AegisOps does not accept (step 3). The collector converts the data for you.

## 2. Run an OTel Collector

The collector receives from your app, computes span metrics, and exports to AegisOps. Below is a minimal config you can copy. Save it as `otelcol-aegisops.yaml`:

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

connectors:
  # Request, error and latency counters on 100% of traffic. The error-rate and
  # latency alert rules read these (`traces.span.metrics.*`); without it only
  # the error-burst rule can fire.
  span_metrics:
    metrics_flush_interval: 10s

exporters:
  otlphttp/aegisops:
    # The base path: the exporter appends /v1/traces, /v1/logs and /v1/metrics.
    endpoint: ${env:AEGISOPS_INGEST_ENDPOINT}
    # Checked only when the API sets AEGIS_INGEST_TOKEN; an empty value is fine on a laptop.
    headers:
      Authorization: "Bearer ${env:AEGISOPS_INGEST_TOKEN}"
    encoding: json          # required: AegisOps answers binary protobuf with 415
    compression: gzip
    retry_on_failure:
      enabled: true
    sending_queue:
      enabled: true

service:
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [otlphttp/aegisops, span_metrics]
    metrics:
      receivers: [otlp, span_metrics]
      exporters: [otlphttp/aegisops]
    logs:
      receivers: [otlp]
      exporters: [otlphttp/aegisops]
```

Run it with the contrib distribution, which includes the `span_metrics` connector. This is the same image and version the demo uses (`COLLECTOR_CONTRIB_IMAGE` in the demo's `.env`):

```bash
docker run --rm -p 4317:4317 -p 4318:4318 \
  -e AEGISOPS_INGEST_ENDPOINT=http://host.docker.internal:8000/ingest \
  -e AEGISOPS_INGEST_TOKEN \
  --add-host host.docker.internal:host-gateway \
  -v "$PWD/otelcol-aegisops.yaml:/etc/otelcol-contrib/config.yaml" \
  ghcr.io/open-telemetry/opentelemetry-collector-releases/opentelemetry-collector-contrib:0.157.0
```

- **`AEGISOPS_INGEST_ENDPOINT`:** where the API is, as seen from the collector. If the collector runs in Docker and the API on the host (`make api`), use `http://host.docker.internal:8000/ingest`; `--add-host` makes that name work on Linux too. If you run the collector binary directly on the host ([installation](https://opentelemetry.io/docs/collector/installation/)), use `http://localhost:8000/ingest`.
- **`AEGISOPS_INGEST_TOKEN`:** must equal the API's `AEGIS_INGEST_TOKEN` ([ADR-024](adr/ADR-024-ingest-bearer-token.md)). If the API has no token set (the laptop default), leave it empty: ingest is open locally. In production (`AEGIS_ENV=prod`) a token is required, and without one ingest answers 503.
- **Component names.** Older collectors call the connector `spanmetrics` instead of `span_metrics`; 0.157.0 accepts both. 0.157.0 also logs `"otlphttp" alias is deprecated; use "otlp_http" instead`. That is a warning only: `otlphttp` still works, and it is the name the demo config uses.

The demo's working version of this exporter is in [`infra/otel-demo/otelcol-config-extras.yml`](../infra/otel-demo/otelcol-config-extras.yml) (the `otlphttp/aegisops` exporter, lines 21–28). That file adds two things a busy app may want too:

- **Tail sampling** (`tail_sampling/aegisops`): keeps every error trace and 15% of the rest.
- **A metric allowlist** (`filter/aegisops_metrics`): stores only the metrics the alert rules read.

The snippet above stores everything your app sends. That is fine for a test, but unfiltered metrics grow fast; see [ADR-014](adr/ADR-014-tail-sampling-and-metric-allowlist.md).

## 3. The gotcha: `encoding: json`

AegisOps accepts **OTLP/JSON only**. The `otlphttp` exporter sends binary protobuf unless you set `encoding: json`, and AegisOps answers protobuf with **415 Unsupported Media Type** ([ADR-013](adr/ADR-013-otlp-json-only-ingest.md)). The collector does not retry 4xx responses, so those batches are dropped and nothing reaches the database.

If AegisOps gets no data, check `encoding: json` first. It only matters on the exporter that points at AegisOps; your app can still send protobuf or gRPC to the collector.

## 4. Check that data arrives

**API log.** Every accepted request logs an `ingest.<signal>` line with the number of rows stored and rejected, like:

```
ingest.traces   stored=42 rejected=0 bytes=18034
ingest.metrics  stored=310 rejected=0 bytes=95120
ingest.logs     stored=17 rejected=0 bytes=6211
```

**Database.** Query the local Postgres (`make db-up` names the container `aegis-postgres`):

```bash
docker exec -it aegis-postgres psql -U aegis -d aegis
```

```sql
-- Spans: your service should be here, with a recent max(start_ts)
SELECT service, count(*), max(start_ts) FROM spans
WHERE start_ts > now() - interval '5 minutes' GROUP BY service;

-- Logs
SELECT service, count(*), max(ts) FROM logs
WHERE ts > now() - interval '5 minutes' GROUP BY service;

-- Metrics: expect traces.span.metrics.calls and traces.span.metrics.duration
-- (from the connector) next to your app's own metrics
SELECT metric_name, count(*) FROM metric_points
WHERE service = 'my-service' AND ts > now() - interval '5 minutes'
GROUP BY metric_name ORDER BY metric_name;
```

If a service shows up as `unknown_service`, `OTEL_SERVICE_NAME` is not set (step 1).

**Responses.** The collector logs export failures (in Docker: `docker logs <container>`). AegisOps answers:

| Status | Meaning | Fix |
|---|---|---|
| 200 `{}` | Stored. | |
| 200 with `partialSuccess` | Some items were dropped (`rejectedSpans`, `rejectedLogRecords` or `rejectedDataPoints`) because their trace or span ids are missing or malformed. The API log shows `rejected=N`. | Check which SDK emits them. |
| 400 | The body is not valid OTLP/JSON, or the gzip is broken. | Usually a hand-built request; the collector's output parses. |
| 401 | The API has `AEGIS_INGEST_TOKEN` set, and the collector sent no token or a different one. | Set `AEGISOPS_INGEST_TOKEN` to the same value. |
| 413 Payload too large | One batch is over `AEGIS_INGEST_MAX_BODY_BYTES` (default 16 MiB), checked both as sent and after gunzip. | Lower the exporter's batch size (`sending_queue.batch.max_size`) or raise the limit. |
| 415 Unsupported media type | The body is binary protobuf, not JSON. | Add `encoding: json` (step 3). |
| 415 Unsupported content encoding | Compression other than gzip. | Use `compression: gzip` or none. |
| 503 Ingest disabled | `AEGIS_ENV=prod` with no `AEGIS_INGEST_TOKEN`: fails closed by design. | Configure a token on the API. |
| 5xx / connection refused | API down or database unreachable (`curl localhost:8000/readyz`). | The collector retries with backoff, so nothing is lost while its queue holds. |

The collector does not retry 4xx responses, so batches rejected with a 4xx are lost. Fix the config and new data flows again. [`docs/RUNBOOK.md`](RUNBOOK.md#ingest-collector--api) has the same cases with more detail.

## 5. What else matters

**Alert rules apply to every service.** [`config/alerts.yaml`](../config/alerts.yaml) has no per-service list. The evaluator checks every service that has data, every 15 s, and opens an incident when a rule holds. Each rule needs a specific signal from your app:

| Rule | Reads | Your app needs |
|---|---|---|
| `error-burst` | ERROR server and consumer spans in `spans` | Traces with span status set on errors (auto-instrumentation does this for HTTP 5xx) |
| `high-error-rate` | `traces.span.metrics.calls` for server spans | The `span_metrics` connector; at least 5 calls in the window |
| `p95-latency-regression` | `traces.span.metrics.duration` for server spans | The `span_metrics` connector; at least 5 calls in the window, and an hour of history to compare against |
| `container-memory-high`, `container-cpu-high` | `container.memory.percent`, `container.cpu.usage.total` | The collector's `docker_stats` receiver; the container name must equal `service.name` |
| `kafka-consumer-lag` | `kafka.consumer_group.lag` | Kafka metrics in the collector |

Thresholds are seeded into the `alert_rules` table at API start and never overwritten, so you can tune them in the database.

**Changes are tracked only for the demo's setup.** The agent correlates incidents with recent changes (`change_events`). There are two watchers:

- **Feature flags: flagd only.** The API watches a flagd JSON file (`AEGIS_FLAGD_CONFIG_PATH`). A flag maps to a service through a target file in [`config/targets/`](../config/targets/) ([`otel-demo.yaml`](../config/targets/otel-demo.yaml) is the only one so far; `AEGIS_TARGET_CONFIG_PATH` picks it). Other flag systems are not tracked.
- **Deploys, restarts and scale changes:** containers of one Docker Compose project on the same host (`AEGIS_DOCKER_SOCKET`, `AEGIS_DOCKER_COMPOSE_PROJECT`), keyed by Compose service name.

Without either watcher, investigations still run on traces, logs and metrics, just without change correlation.

**Actions are demo-only today.** Remediations are allowlisted per service in [`config/policy.yaml`](../config/policy.yaml), and only demo services are listed. On your own app, AegisOps investigates and recommends but does not act.

**Retention.** Telemetry older than `AEGIS_RETENTION_HOURS` (default 24) is deleted, except rows tagged to a scenario.

For connecting a company's systems beyond one laptop (outbound-only, revocable), see [In a real company](../README.md#in-a-real-company) and [ADR-023](adr/ADR-023-outbound-only-integration.md).
