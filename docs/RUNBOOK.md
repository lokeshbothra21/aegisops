# AegisOps runbook

Operational situations and what to do. Started Week 1 (PROJECT.md §17); grows with every incident we hit ourselves. Entries are ordered by layer: local dev, ingest, deploy, data, agent.

## Local development

| Situation | Action |
|---|---|
| `make test` fails with connection refused on 5433 | Docker (OrbStack) is not running or the container is down. `open -a OrbStack`, then `make db-up`. Data persists in the `aegis-pgdata` volume. |
| `test_migrated_schema_matches_models` fails | Models and migrations drifted. `make db-revision m="describe change"`, inspect the generated file, `make db-migrate`. Never edit an applied migration. |
| Demo (compose) won't start on 16 GB | Close apps, `make demo-up` (minimal profile), verify arm64 images, `docker system prune`. |
| Grafana unresponsive / CPU pegged | Demo ships a 175 MB limit that Grafana 13 thrashes at. `make demo-up` applies `docker update --memory 400m grafana`; if started another way, run that by hand. |

## Ingest (collector → API)

| Situation | Action |
|---|---|
| Collector logs `415 Unsupported media type` from `otlphttp` | Exporter is sending binary protobuf. Set `encoding: json` on the `otlphttp` exporter (`infra/otel-demo/otelcol-config-extras.yml`, E1.3). |
| Collector logs `413 Payload too large` | Batch too big for `AEGIS_INGEST_MAX_BODY_BYTES` (default 16 MiB, checked raw and after gzip). Lower `send_batch_max_size` in the collector `batch` processor, or raise the env var. |
| Collector logs `400 Invalid OTLP/JSON` | The `detail` field names the first bad path (e.g. `resourceSpans.0.scopeSpans: ...`). 4xx is not retried by the collector; fix the exporter config, data is lost for that batch only. |
| Collector logs 5xx / connection refused and retries | API down or database unreachable; check `GET /readyz` (503 = DB). Collector retries with backoff, nothing is lost while its queue holds. From a container the API is `http://host.docker.internal:8000/ingest`. |
| API log `ingest.traces rejected=N` | Spans without a valid 32-hex trace id / 16-hex span id are dropped and reported in the OTLP `partialSuccess` response. Check which SDK emits them. |
| Rows arrive with `service = unknown_service` | Resource is missing `service.name`. Fix the SDK/collector `resource` processor; the row is kept so nothing is silently lost. |

## Deploy (Cloud Run) — from PROJECT.md §17, verify each when E11.3 lands

| Situation | Action |
|---|---|
| Deploy failed health check | Traffic stayed on the previous revision; read Cloud Run logs; fix; redeploy. |
| Need manual rollback | `gcloud run services update-traffic aegisops-api --to-revisions=<prev>=100` |
| Supabase paused | Keep-alive cron (E11.5) should prevent it; else restore from the dashboard and verify `/readyz`. |
| Schema change needed | New Alembic migration; CI runs it against a fresh DB; deploy runs `alembic upgrade head` as a pre-start step. |
| Key leaked | Rotate in the provider, update Secret Manager, redeploy, add a note to `SECURITY.md`. |

## Agent (fill in from Week 3)

| Situation | Action |
|---|---|
| Gemini 429 / quota exhausted | Router falls back to Groq; if both exhausted, public mode serves cached runs; check Langfuse for the burst source. |
| Cost spike | Check `runs` for tool_calls/tokens outliers; lower the global daily cap in config; rotate the key if abused. |
| Checkpoint/resume broken | Inspect `langgraph_*` tables for the thread_id; `POST /incidents/{id}/runs` restarts a fresh thread. |
