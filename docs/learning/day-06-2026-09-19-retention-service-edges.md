# Day 6 · 19 Sep 2026 · Retention, service edges, admin routes, job runner (PR #14, E1.5, E1.4)

## What we did
Week 2 begins. Two periodic jobs and the plumbing to run and trigger them:
- **Retention (E1.5):** delete untagged telemetry older than 24 h; tagged (`scenario_id`) rows are permanent fixtures.
- **Service edges (E1.4):** derive the service dependency graph, hourly, from parent→child span pairs that cross a service boundary: `caller, callee, call_count, err_count, p95_ms` per hour window. Migration `0002_service_edges`.
- **Job runner:** a 40-line asyncio scheduler started in the FastAPI lifespan; each tick runs in its own DB session and a failing tick is logged, never fatal.
- **Admin routes:** `POST /api/v1/admin/retention/run` and `/service-edges/run`, protected by `X-Admin-Token`. No token configured → 503 (fail closed), wrong token → 401.
- 36 tests, 98 % coverage; new modules at 100 %.

## How the edge derivation works
Take every SERVER (or CONSUMER) span `c`. Find its parent `p` in the same trace via `p.span_id = c.parent_span_id`. If `p.service ≠ c.service`, that is one call from `p.service` to `c.service`. The parent is normally the CLIENT span, so `p.duration_ms` includes the network hop: that is the latency the caller *experienced*. Group by `date_trunc('hour', c.start_ts), caller, callee` → count, sum of errors (either side ERROR), `percentile_cont(0.95)` of the parent duration. Recomputing a window deletes then inserts, so re-running is safe.

## Terms introduced

**Retention policy.** A rule for how long data is kept. Rolling 24 h for live rows keeps the laptop DB and Supabase's 500 MB bounded; fixtures are exempt because replay and the benchmark depend on them being immutable. (NFR-05)

**Idempotent job.** Running it twice has the same effect as once. Retention: rows already deleted are gone. Edges: delete-then-insert per window. Idempotence is what makes "just re-run it" a safe operational answer.

**Derived table / materialised aggregate.** A table computed from raw data so a hot query becomes one indexed read. `service_edges` is read by the agent's `get_service_dependencies` tool instead of joining millions of spans at question time. Trade: staleness up to one refresh interval (15 min here).

**Service dependency graph.** Nodes are services, edges are "A calls B". Built from traces, not from config, so it reflects reality. Used by `plan` (depth-2 neighbourhood of the alerting service) and to reason "checkout is failing because payment, which it calls, is failing".

**Parent/child spans and span kinds in practice.** In a trace, a CLIENT span in the caller is the parent of a SERVER span in the callee. INTERNAL spans stay inside one service and never create edges. PRODUCER→CONSUMER is the async (queue) equivalent.

**Percentile (p95) and `percentile_cont`.** The value below which 95 % of observations fall; robust to outliers unlike the mean. Postgres `percentile_cont(0.95) WITHIN GROUP (ORDER BY x)` interpolates between neighbours (100,200,300,400 → 385). p95 is the standard latency SLO number.

**`IS NOT DISTINCT FROM`.** SQL null-safe equality. `scenario_id = NULL` is never true; `scenario_id IS NOT DISTINCT FROM :param` matches both a value and NULL-to-NULL. Lets one query serve live rows (NULL) and a scenario.

**`date_trunc` windows.** `date_trunc('hour', ts)` buckets timestamps into hour windows; the same idea powers the alert evaluator's 5-minute windows next.

**CTE (`WITH pairs AS ...`).** A named subquery; keeps the join and the aggregate readable and lets Postgres plan them together.

**Delete-then-insert vs upsert.** Upsert (`INSERT ... ON CONFLICT DO UPDATE`) needs a unique key; ours would include a nullable `scenario_id`, which unique indexes treat awkwardly. Replacing the window's rows is simpler and equally idempotent at our size.

**Background task in an ASGI app / lifespan-managed scheduler.** `asyncio.create_task` per job in the FastAPI lifespan; tasks are cancelled on shutdown. Works on Cloud Run only while an instance is alive (min-instances 0), which is acceptable for maintenance jobs; the alert evaluator will revisit this.

**Fail closed.** When a security control is unconfigured, deny (503) rather than allow. Admin routes with no `AEGIS_ADMIN_TOKEN` refuse everything.

**Bearer-style header auth (`X-Admin-Token`).** A shared secret in a request header, compared server-side. Adequate for one admin (the plan's v1 scope); real multi-user systems use OAuth/OIDC and RBAC.

**`SecretStr`.** Pydantic type whose `repr`/logs show `**********`; `get_secret_value()` reveals it on purpose only.

**Clamping input.** `hours` is clamped to 1..48 so an admin typo cannot rescan a year of spans.

**`rowcount`.** Rows affected by a DML statement; how retention reports what it deleted. In SQLAlchemy 2 the typed result is `CursorResult`.

**Test isolation on a shared DB.** Tests use a unique `scenario_id` or far-future timestamps (2030/2031) so they never see live demo rows or each other, and the test settings disable the job runner so retention cannot delete fixture rows mid-test. One deliberate side effect remains: the admin retention test really deletes untagged rows older than 24 h in the shared local database, which is exactly what it would do in production.

**Alembic revision naming.** Autogenerate produces a random hex id; we rename to `0002` so `alembic history` reads in order. Down-grade, rename, upgrade.

## Live result
Against the running demo, three minutes after start: 16 edges for the current hour. Examples: frontend-proxy → frontend, 137 calls, p95 308 ms; frontend → product-catalog, 67 calls, p95 608 ms; load-generator → flagd, p95 1.1 ms. The runner's log shows the edge count climbing 13 → 14 → 16 on successive ticks as new service pairs appear.

## Decisions
- Own asyncio runner instead of APScheduler for now (two jobs). Recorded in changelog; will become an ADR only if APScheduler is adopted for E2.3.
- Edge latency = parent (client) span duration, not callee server duration: the caller's experience is what alerting and the agent reason about.

## Interview questions
1. *How do you build a service dependency graph from traces?* Join each SERVER span to its parent by `(trace_id, parent_span_id)`; a service change on that edge is a call; aggregate per hour.
2. *Why store p95 and not the mean?* Tail latency is what users feel and what SLOs specify; the mean hides a slow 5 %.
3. *Why is the job idempotent and why does that matter operationally?* Delete-then-insert per window; safe to re-run after a crash or a bug fix without double counting.
4. *What happens to fixtures during retention?* Nothing: `scenario_id IS NULL` is in every DELETE.
5. *Why fail closed on admin auth?* An unconfigured control must not become an open door; 503 is loud, 200 is silent.
6. *Where does the scheduler run in a scale-to-zero platform?* Inside the API process while an instance is alive; acceptable for maintenance, revisited for alerting.
