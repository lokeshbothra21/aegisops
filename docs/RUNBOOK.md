# AegisOps runbook

Operational situations and what to do. Started Week 1 (PROJECT.md §17); grows with every incident we hit ourselves. Entries are ordered by layer: local dev, ingest, deploy, data, agent.

## Local development

| Situation | Action |
|---|---|
| `make test` fails with connection refused on 5433 | Docker (OrbStack) is not running or the container is down. `open -a OrbStack`, then `make db-up`. Data persists in the `aegis-pgdata` volume. |
| `test_migrated_schema_matches_models` fails | Models and migrations drifted. `make db-revision m="describe change"`, inspect the generated file, `make db-migrate`. Never edit an applied migration. |
| Demo (compose) won't start on 16 GB | Close apps, `make demo-up` (minimal profile), verify arm64 images, `docker system prune`. Minimal mode measured ~2.5 GB (OpenSearch alone 1 GB). |
| `make demo-up` fails at `demo-check` | The demo checkout, `infra/otel-demo/aegisops.env` pin and `infra/otel-demo/VERSION` disagree. `cd ../opentelemetry-demo && git checkout <VERSION>`, then fix the pin. Never run the demo's own `make start-*`: it skips our collector layer and overrides. |
| Grafana unresponsive / CPU pegged | Demo ships a 175 MB limit that Grafana 13 thrashes at. `compose.aegisops.yaml` raises it to 400M; check with `docker stats grafana`. |
| Need to inject a fault by hand | `make flag name=paymentFailure variant=100%` (flagd reloads within seconds; `variant=off` clears). Flags and variants: `../opentelemetry-demo/src/flagd/demo.flagd.json`. |
| Collector log full of `kafkametrics ... lookup kafka: no such host` | Demo noise: minimal mode has no Kafka but the collector still loads the full config. Harmless; ignore unless running `compose.full.yaml`. |

## Ingest (collector → API)

| Situation | Action |
|---|---|
| Collector logs `415 Unsupported media type` from `otlphttp` | Exporter is sending binary protobuf. Set `encoding: json` on the `otlphttp` exporter (`infra/otel-demo/otelcol-config-extras.yml`, E1.3). |
| Collector logs `413 Payload too large` | Batch too big for `AEGIS_INGEST_MAX_BODY_BYTES` (default 16 MiB, checked raw and after gzip). Lower `send_batch_max_size` in the collector `batch` processor, or raise the env var. |
| Collector logs `400 Invalid OTLP/JSON` | The `detail` field names the first bad path (e.g. `resourceSpans.0.scopeSpans: ...`). 4xx is not retried by the collector; fix the exporter config, data is lost for that batch only. |
| Collector logs 5xx / connection refused and retries | API down or database unreachable; check `GET /readyz` (503 = DB). Collector retries with backoff, nothing is lost while its queue holds. From a container the API is `http://host.docker.internal:8000/ingest`. |
| API log `ingest.traces rejected=N` | Spans without a valid 32-hex trace id / 16-hex span id are dropped and reported in the OTLP `partialSuccess` response. Check which SDK emits them. |
| Rows arrive with `service = unknown_service` | Resource is missing `service.name`. Expected for `docker_stats` metrics (use `attrs->'otel.resource'->>'container.name'`); for anything else fix the SDK/collector `resource` processor. The row is kept so nothing is silently lost. |
| No rows although the demo is up | Is the API running on the host (`make api`)? `make demo-logs` shows the exporter's retries; `curl localhost:8000/readyz` must say ready. Collector reaches the host as `host.docker.internal` (set in `aegisops.env`). |
| Too many / too few metric rows | The allowlist is the `filter/aegisops_metrics` OTTL statement in `otelcol-config-extras.yml`; sampling for traces is `tail_sampling/aegisops` (15% of non-error traces). |

## Data and jobs

| Situation | Action |
|---|---|
| Local DB growing / disk | Retention runs hourly in the API while it is up; force it: `curl -X POST -H "X-Admin-Token: $AEGIS_ADMIN_TOKEN" localhost:8000/api/v1/admin/retention/run`. Tagged (`scenario_id`) rows are never deleted. |
| `service_edges` empty or stale | Refreshed every 15 min for the current + previous hour. Force: `POST /api/v1/admin/service-edges/run?hours=N` (N ≤ 48). Edges need parent→child spans across services; an INTERNAL-only trace produces none. |
| Admin route answers 503 "Admin disabled" | `AEGIS_ADMIN_TOKEN` is unset on that deployment. Set it (Secret Manager in prod) and redeploy. 401 means the header is missing or wrong. |
| Flag toggles not appearing in `change_events` | The watcher is on only when `AEGIS_FLAGD_CONFIG_PATH` points at the demo's `src/flagd/demo.flagd.json` (see `.env.example`); `jobs.start` must list `flag_watcher`. Changes made while the API was down are not back-filled. Unknown flags are recorded with `service = NULL`; add them to `config/targets/otel-demo.yaml`. |
| A job logs `job.ok` but nothing is in the DB | Fixed 19 Sep (runner commit bug). If it recurs: the job must not `return` from inside a session block that is a bare async generator; use `async with session_scope(...)`. |
| Job failing every tick (`job.failed` in logs) | The runner never stops on errors; read the `error=` field, fix, restart. Each tick is its own transaction, so a failure leaves no partial rows. |

## Deploy (Cloud Run, project `aegisops-508519`, region asia-south1)

Local gcloud: `export CLOUDSDK_ACTIVE_CONFIG_NAME=aegisops` (account lokesh8946891910); the default config belongs to the ERP project.

| Situation | Action |
|---|---|
| `deploy-api` failed at "Probe the tagged revision" | Traffic stayed on the previous revision. Open the tagged URL from the job log, then `gcloud run services logs read aegisops-api --region asia-south1 --limit 50`. Fix, merge, redeploy. |
| Need manual rollback | `gcloud run revisions list --service aegisops-api --region asia-south1`, then `gcloud run services update-traffic aegisops-api --region asia-south1 --to-revisions=<prev>=100`. |
| Deploy failed at `auth` (WIF) | The provider only trusts `lokeshbothra21/aegisops`. A fork or renamed repo cannot deploy. Check the repo variables `GCP_WIF_PROVIDER` / `GCP_DEPLOY_SA`. |
| Unexpected GCP bill | Budget alert "aegisops guardrail" (₹500) emails at 50 %, 100 % and forecast. Check `--min-instances` is 0 and Artifact Registry cleanup kept ≤ 5 images. |
| Redeploy without a code change | Actions → deploy-api → Run workflow (`workflow_dispatch`). |
| Supabase paused | Keep-alive cron (E11.5) should prevent it; else restore from the dashboard and verify `/readyz`. |
| Schema change needed | New Alembic migration; CI runs it against a fresh DB; deploy runs `alembic upgrade head` as a pre-start step. |
| Key leaked | Rotate in the provider, update Secret Manager, redeploy, add a note to `SECURITY.md`. |

## Agent (fill in from Week 3)

| Situation | Action |
|---|---|
| Gemini 429 / quota exhausted | Router falls back to Groq; if both exhausted, public mode serves cached runs; check Langfuse for the burst source. |
| Cost spike | Check `runs` for tool_calls/tokens outliers; lower the global daily cap in config; rotate the key if abused. |
| Checkpoint/resume broken | Inspect `langgraph_*` tables for the thread_id; `POST /incidents/{id}/runs` restarts a fresh thread. |
