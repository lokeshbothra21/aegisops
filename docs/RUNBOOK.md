# AegisOps runbook

Operational situations and what to do. Started Week 1 (PROJECT.md §17); grows with every incident we hit ourselves. Entries are ordered by layer: local dev, ingest, deploy, data, agent.

## Local development

| Situation | Action |
|---|---|
| `docker ps` shows nothing, no volumes, "everything is gone" | Check `docker context ls` first: Docker Desktop registers its own empty daemon and can take over the CLI context. `docker context use orbstack` brings every container and volume back. Nothing was deleted. |
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
| No deploy/restart events although containers changed | The container watcher is on only when `AEGIS_DOCKER_SOCKET` points at the Docker socket (OrbStack: `~/.orbstack/run/docker.sock`); `jobs.start` must list `container_watcher`. It watches one Compose project (`AEGIS_DOCKER_COMPOSE_PROJECT`, default `opentelemetry-demo`). `container_watcher.unreachable` in logs = wrong socket path or daemon down. |
| Uptime workflow red | Open the run: `/livez` failed 3× 10 s apart. Check Cloud Run revisions/logs (Deploy section). A single red run after a deploy can be a cold start racing the deploy; two in a row is an outage. |
| A job logs `job.ok` but nothing is in the DB | Fixed 19 Sep (runner commit bug). If it recurs: the job must not `return` from inside a session block that is a bare async generator; use `async with session_scope(...)`. |
| Job failing every tick (`job.failed` in logs) | The runner never stops on errors; read the `error=` field, fix, restart. Each tick is its own transaction, so a failure leaves no partial rows. |

## Scenarios and replay

| Situation | Action |
|---|---|
| Run a scenario end to end | Demo + API up, then `uv run aegis-scenario run S1 --admin-token $AEGIS_ADMIN_TOKEN` (needs `AEGIS_FLAGD_CONFIG_PATH`). Prints a `RunReport` (fault_at, incident_at, ttd_s, captured counts). Exit 1 = no incident opened within the timeout. |
| The demo is left broken after a failed run | The runner reverts the fault in `finally`; if the process was killed, `make flag name=<flag> variant=off` (see `bench/scenarios.yaml` for the revert variant, e.g. `loadGeneratorVUs` → `5`). |
| Re-capture a scenario with a different window | `POST /api/v1/admin/capture` with the same key: old tags are released first. Windows must not overlap other scenarios. |
| Share a captured scenario / seed CI or Supabase | `aegis-scenario export S1` → `bench/fixtures/S1.jsonl.gz` (gitignored, publish as a release asset); `aegis-scenario import <file>` elsewhere. |
| Replay gives a strange answer ("no telemetry", revert blamed) | Check the frozen clock: `aegis-scenario investigate` uses the expected service's first incident time. If the scenario has no incident for that service, it falls back to the window end, which is after the revert. |
| Replay the agent on a captured scenario | `aegis-scenario investigate S1 [--recorded cassette.yaml]` = `aegis-investigate --scenario S1 --frozen-now <window_end>`. |
| `not captured` from investigate | `GET /api/v1/scenarios` must list the key; run or import it first. |

## Alerting

| Situation | Action |
|---|---|
| No incident opened although the fault is visible | Check `jobs.start` lists `alerts` and `alerts.rules_seeded` ran (`AEGIS_ALERTS_ENABLED`). Readers need span_metrics rows: `select count(*) from metric_points where metric_name='traces.span.metrics.calls' and ts > now()-interval '2 minutes'`. A service with < 5 calls in the window never fires a *rate* rule (the `error-burst` count rule still can). `for_windows` evaluations × 15 s must elapse after the first symptom; at the demo's default 5 VUs the payment path sees ~5 calls/min, which bounds detection to ~60 s after the first error. |
| Too many incidents / flapping | Tune the rule row in `alert_rules` (threshold, `window_s`, `for_windows`); the seed never overwrites it. Delete the row to get the default from `config/alerts.yaml` back at next start. |
| `alerts.unknown_metric` in logs | A rule row names a metric with no reader; fix or delete the row. Valid metrics: `error_count`, `error_rate`, `p95_ratio`, `container_memory_pct`, `kafka_lag`. |
| Incident stuck `open` after recovery | Auto-resolve needs `AEGIS_ALERT_RECOVERY_WINDOWS` (3) healthy ticks **and** status `open`; anything investigated is the agent's/human's to close. |
| API answers 503 "Database unavailable" | Postgres unreachable (connection refused / pool error). Local: `make db-up`. Prod: Supabase paused or secret wrong; `/readyz` shows the same. |

## Tools / MCP server

| Situation | Action |
|---|---|
| Try the agent's tools by hand | `uv run aegis-telemetry` speaks MCP over stdio; point any MCP client at it (Claude Desktop, an IDE, or `mcp` SDK `stdio_client`). Env: `AEGIS_DATABASE_URL`; replay: `AEGIS_SCENARIO_ID` + `AEGIS_FROZEN_NOW=<ISO>`. |
| Latency numbers look 1000× off | Check `unit` on the `traces.span.metrics.duration` rows for that service; the tools normalise ms/s per series and report `bucket_layouts` when SDKs disagree. |
| Local telemetry vanished after `make check` | Fixed 22 Sep: the retention test used a future cutoff. If it recurs, look for a test calling `run_retention` with `now` in the future. |
| A tool returns `truncated: true` | Payload exceeded 4 KB; lists were trimmed. Narrow the window or lower `limit`; never raise the cap (token budget). |
| A tool returns no data for a service | Check the service name spelling (it is `service.name` from the resource, e.g. `product-catalog`), the window, and that the API is ingesting (`/readyz`, `ingest.*` logs). Container metrics key on the container name. |
| `IndeterminateDatatypeError` / `could not determine data type of parameter` | asyncpg cannot infer a bound parameter's type in that SQL position; wrap it in `CAST(:p AS text)` (or `text[]`, `timestamptz`). |

## Deploy (Cloud Run, project `aegisops-508519`, region asia-south1)

Local gcloud: `export CLOUDSDK_ACTIVE_CONFIG_NAME=aegisops` (account lokesh8946891910); the default config belongs to the ERP project.

| Situation | Action |
|---|---|
| `deploy-api` failed at "Probe the tagged revision" | Traffic stayed on the previous revision. Open the tagged URL from the job log, then `gcloud run services logs read aegisops-api --region asia-south1 --limit 50`. Fix, merge, redeploy. |
| Need manual rollback | `gcloud run revisions list --service aegisops-api --region asia-south1`, then `gcloud run services update-traffic aegisops-api --region asia-south1 --to-revisions=<prev>=100`. |
| Deploy failed at `auth` (WIF) | The provider only trusts `lokeshbothra21/aegisops`. A fork or renamed repo cannot deploy. Check the repo variables `GCP_WIF_PROVIDER` / `GCP_DEPLOY_SA`. |
| Unexpected GCP bill | Budget alert "aegisops guardrail" (₹500) emails at 50 %, 100 % and forecast. Check `--min-instances` is 0 and Artifact Registry cleanup kept ≤ 5 images. |
| Redeploy without a code change | Actions → deploy-api → Run workflow (`workflow_dispatch`). |
| Supabase paused | The uptime cron requires `/readyz` every 6 h, which should prevent it (a red uptime run is the alarm). Else restore from the Supabase dashboard and re-run the uptime workflow. |
| Deploy failed at "Creating Revision... container failed to start" | The container exited before listening. Read the revision's logs: `gcloud logging read 'resource.labels.revision_name="<rev>"' --limit 60`. 26 Sep example: `FileNotFoundError: config/models.yaml` (config missing from the image). Traffic stays on the old revision. |
| Deploy failed at `/readyz` | The new revision cannot reach Supabase: wrong/rotated password in `aegis-database-url`, project paused, or pooler host/region wrong. Traffic stayed on the old revision. Fix the secret (`gcloud secrets versions add aegis-database-url --data-file=-`) and redeploy. |
| Rotate a production secret | `printf '%s' "$NEW" \| gcloud secrets versions add <name> --data-file=-`, then Actions → deploy-api → Run workflow. Secrets: `aegis-database-url`, `aegis-admin-token`, `aegis-gemini-api-key`, `aegis-groq-api-key`. | <!-- pragma: allowlist secret -->
| Migrate Supabase by hand | `AEGIS_DATABASE_URL=<pooler URL with +asyncpg> uv run alembic upgrade head` in `apps/api` (the container also runs it at start). Use the **Session pooler** URL, never the IPv6-only direct host. |
| Schema change needed | New Alembic migration; CI runs it against a fresh DB; deploy runs `alembic upgrade head` as a pre-start step. |
| Key leaked | Rotate in the provider, update Secret Manager, redeploy, add a note to `SECURITY.md`. |

## Agent

| Situation | Action |
|---|---|
| Run an investigation by hand | `uv run aegis-investigate --service payment --alert "<alert summary>"` (live rows) or add `--scenario <id> --frozen-now <ISO>` for replay. `--recorded packages/agent/tests/cassettes/s1_payment_failure.yaml` needs no model key. Output: one JSON document on stdout; logs on stderr. |
| `no client for 'gemini:...' (set AEGIS_GEMINI_API_KEY)` | Put the key in `.env` locally / Secret Manager in prod. `config/models.yaml` names providers; both `AEGIS_GEMINI_API_KEY` and `AEGIS_GROQ_API_KEY` should exist so fallback works. |
| `gemini 404 ... no longer available` | The pinned model was retired. List models: `curl "https://generativelanguage.googleapis.com/v1beta/models?key=$AEGIS_GEMINI_API_KEY"` and update `config/models.yaml` (pin an exact id, not `-latest`). |
| `gemini 400 Invalid JSON payload ... Unknown name` | A Pydantic schema feature Gemini's subset rejects; extend `UNSUPPORTED_KEYWORDS` / the `anyOf` handling in `llm._schema_for`. |
| `model_fallback` warnings | Primary returned 429/5xx/timeout; the secondary answered. Frequent fallbacks = quota exhausted; check the provider console. |
| Run ends with `partial: true` | A budget tripped (`budget_exceeded` says which: tool_calls / tokens / seconds). The report is still valid but lower-confidence. Raise the budget only for benchmarking. |
| `model output failed ... validation` | The model returned JSON that does not match the schema; retryable, the router falls back once. Persistent → tighten the prompt in `packages/agent/src/aegisops_agent/prompts/`. |
| Verified confidence much lower than claimed | Read `verification.dropped[].reason`: invented metric names, fabricated ids, numbers off by > 20 %. That is the verifier working; tune prompts in `prompts/` if a pattern repeats (e.g. tell the model the exact metric names the tools expose). |
| Groq `429 ... tokens per minute (TPM): Limit 8000` | Free tier: one ~7k-token prompt per minute. The router honours `Retry-After` and alternates providers (4 attempts); a run with both providers down degrades to a partial report. For sustained runs use Gemini as primary and expect fallbacks, or upgrade the Groq tier. |
| Gemini `503 high demand` / timeouts | Router falls back to Groq per node (`model_fallback` with the exception type). Provider timeout is 30 s. Frequent = check Google AI Studio status; consider swapping primary/secondary in `config/models.yaml` temporarily. |
| Start and watch a run over the API | `curl -X POST localhost:8000/api/v1/incidents/<id>/runs -d '{}' -H 'content-type: application/json'` → 202 with the run id; `curl -N localhost:8000/api/v1/runs/<run>/events` streams node events (reconnect with `?after=<last seq>`). |
| Run paused at `awaiting_approval` | `POST /api/v1/runs/<run>/approve` or `/reject` with `X-Admin-Token` and `{"by": "...", "note": "..."}`. Approve → incident `remediating`; reject → `investigating` and a new run may start. |
| `409 Run already active` | One running or awaiting-approval run per incident. Decide the pending one first. |
| `503 Agent disabled` on /runs | `AEGIS_AGENT_ENABLED=false`, or the checkpointer could not reach Postgres at startup (`agent.checkpointer_unavailable` in logs; expected on Cloud Run until Supabase). |
| Same scenario, very different confidence run to run | Model non-determinism and which provider answered (see `model` on the run). Verified confidence is what the policy uses; compare several runs, never one. |
| Approved fix did nothing | Check the remediation's `outcome` (`GET /runs/<id>`): `rejected:` = validation (not allowlisted in `config/policy.yaml`); `no live backend configured` = `AEGIS_FLAGD_CONFIG_PATH`/`AEGIS_DOCKER_SOCKET` unset; `not supported by the local demo target` = scale/rollback. Every attempt is in `audit_log`. |
| Incident marked `failed` after an approved fix | Post-action verification found the rule still breaching over post-fix data (`outcome.value_after`). The fix did not work, or traffic was too thin to judge; investigate or start a new run. |
| Who changed what? | `select ts, node, tool, actor, ok, args from audit_log where run_id = <run> order by id;` Actors: `agent`, `admin:<name>`, `policy:auto`. |
| Drift test complains about `checkpoint*` tables | They belong to LangGraph's saver, not Alembic; `include_object` in `models/base.py` must skip them. |
| Gemini 429 / quota exhausted | Router falls back to Groq; if both exhausted, public mode serves cached runs; check Langfuse for the burst source. |
| Cost spike | Check `runs` for tool_calls/tokens outliers; lower the global daily cap in config; rotate the key if abused. |
| Checkpoint/resume broken | Inspect `langgraph_*` tables for the thread_id; `POST /incidents/{id}/runs` restarts a fresh thread. |
