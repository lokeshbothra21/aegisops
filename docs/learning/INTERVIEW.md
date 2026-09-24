# Interview question bank

Grouped by topic. Each answer is the 60-second version; the day files hold the detail. Questions marked ★ are the ones PROJECT.md §26.1 says the project must answer from implementation.

## Architecture and product
- **What does AegisOps do, in one minute?** → Day 0 pitch.
- ★ **Why deterministic alerting instead of LLM detection?** Rules over SQL aggregates are cheap, reproducible and testable; LLM detection is per-tick cost with non-reproducible false positives, and false positives are a metric we publish. (ADR-008)
- ★ **Why Postgres instead of Jaeger/Prometheus APIs?** One free store for telemetry, state and vectors; replay by `scenario_id`; SQL pre-aggregation keeps tool outputs ≤ 4 KB. (ADR-003)
- **What is "one code path, two modes"?** The agent never knows if it is live or replaying; only the tool filter and the action backend differ. (ADR-004)
- ★ **Why rollback-first, no code patches?** Restore before fix is SRE practice; a fixed action set can be allowlisted and validated. (ADR-005)
- ★ **Why MCP for reads but not actions?** Authorization by topology: actions live in a module reachable only from `execute`, which follows `approval`. (ADR-007)
- ★ **How would this scale to 10K services / 1,000 incidents a day?** Shard ingest by service, sample harder, queue in front of the agent with a worker pool, per-tenant budgets, tiered storage (hot Postgres, cold object store), read replicas for tools.

## Telemetry and ingestion
- **Explain logs, metrics, traces and how they link.** Trace/span ids on logs; span-metrics derived from traces; `service` on everything.
- **Why write your own OTLP/JSON parser?** Hex-vs-base64 id deviation breaks the stock protobuf JSON parser; no heavy dependency; subset only. (ADR-013)
- **How do you avoid data loss on API restart?** 5xx → collector retries with a queue; 4xx only for input that can never succeed.
- **What is a gzip bomb and your defence?** Size check before and after decompression.
- **How did you reach ~20k spans/s in Python?** Bulk multi-row INSERT, pure conversion, async driver.
- **Why tail sampling?** Only per-trace decisions can keep every trace with an error. Cost is buffer memory. (ADR-014)
- **How are error rates exact when traces are sampled?** span_metrics connector on the unsampled pipeline.
- **How do you stop metrics from flooding the DB?** OTTL allowlist: 8 series survive.
- **Measured detection latency at the storage layer?** 6 s toggle → first ERROR row, mostly the 5 s tail-sampling wait.

## Database
- **How do you build a service dependency graph from traces?** Join each SERVER span to its parent by `(trace_id, parent_span_id)`; a service change across that edge is one call; aggregate per hour into `service_edges`. (Day 6)
- **Why store p95 rather than the mean?** Tail latency is what users feel and SLOs specify; the mean hides a slow 5 %.
- **How is retention safe for the benchmark fixtures?** Every DELETE has `scenario_id IS NULL`; tagged rows are permanent.
- **Why delete-then-insert instead of upsert for the edges?** Idempotent without a unique key over a nullable column.
- **Why denormalise `service`?** Hot path is per-service filtering; joins at ingest rate are the wrong trade.
- **Why JSONB attributes?** Heterogeneous, evolving attribute sets; promote to columns only when indexed queries need them.
- **How do you guarantee prod schema equals code?** Alembic is the only path; a CI test asserts zero drift.
- **Why `pool_pre_ping`?** Hosted DBs drop idle connections; turn a stale connection into a reconnect, not a 500.
- **How will you compute p95 from stored histograms?** Bucket counts + explicit bounds in `attrs.otel.histogram`; interpolate within the bucket that crosses the 95th percentile.

## Agent
- **How do you verify an LLM's claims without another LLM?** Structured citations checked by code against the store; drop failures; confidence × verified/cited; keep the claimed value for calibration. (Day 12)
- **What did change correlation change?** The model cited the flag flip once the scored change list was in its input; before, it ignored it.
- **How do you handle a slow provider?** 30 s timeout = down; fall back to the secondary; log the exception type.
- **Why a fixed graph rather than a free-form tool loop?** Bounded cost (4 model calls), deterministic tests, authorization by topology. (ADR-016, Day 11)
- **How is the agent tested without a model key?** Structured outputs + per-node recorded responses; graph, tools and Postgres are real.
- **What happens on budget exhaustion?** Flag in state → `root_cause` with `partial=true`; the report still ships.
- **How does model fallback work?** Router retries retryable errors once on the secondary; 4xx fail fast; `model_fallback` logged.
- **Who owns the checkpoint tables?** LangGraph's saver; Alembic ignores `checkpoint*` via `include_object`.

## Tools and MCP
- **Why do tools return ≤ 4 KB?** Token budget and reasoning quality; aggregate in SQL. (Day 10)
- **How do the same tools serve live and replay?** `scenario_id` filter + frozen `now` in the tool context; identical code.
- **How is the MCP contract kept in sync with the code?** Input schemas are generated from the function signatures at registration.
- **Where do error rates come from if traces are sampled?** Span-metrics counters computed before sampling; examples come from the kept error traces.

## Alerting
- **Why not compute error rate from stored spans?** Tail-sampled (all errors, 15 % of OK) → biased; use span-metrics counters computed before sampling. (Day 8)
- **How do you compute p95 from a histogram?** Bucket deltas over the window; find the bucket where the cumulative count crosses 95 %; interpolate.
- **How do you avoid flapping?** `for` windows before firing, recovery windows before resolving, one active incident per service.
- **Why a p95 ratio to a baseline rather than a fixed threshold?** Endpoints differ 100× in normal latency; a ratio catches regressions everywhere with one rule.
- **Where does a 60 s detection budget go?** ~6 s sampling wait + ~10 s export interval + ≤30 s to the next tick, ×`for_windows`.

## Incidents and change tracking
- **How do you prevent duplicate incidents for one outage?** `active_incident(service)` returns the open one; the evaluator attaches rather than opens.
- **Why is the incident lifecycle a table of allowed transitions?** Invalid states become unrepresentable; one mutator; trivially unit-tested.
- **How do you know what changed before an alert without hooking every tool?** Watch the artefacts (flag file, container labels), diff snapshots, record before/after with an actor.
- **Why cursor pagination?** Stable under inserts, O(log n), no COUNT.
- **How do you detect a deploy or restart without CI hooks?** Snapshot the target's containers via the Docker API (image tag + id, StartedAt, RestartCount, replicas) and diff. (Day 9)

## CI/CD and platform
- **Walk me through a merge to main.** Day 5 flow paragraph.
- **Why pin actions by SHA?** Tags are mutable; a compromised action could steal the OIDC token; Dependabot keeps pins fresh.
- **Why re-run checks in the deploy workflow?** Merge result ≠ PR head; independence from another workflow's status.
- **How do you make deploys safe?** No-traffic revision → probe tagged URL → shift; rollback is a traffic switch.
- ★ **What happens if the Cloud Run instance dies mid-run?** (Planned) LangGraph checkpoint after each node; the SSE client reconnects and the thread resumes. (ADR-006, ADR-009)
- **Why WIF over a service-account key?** Minutes-long credentials, issued only to workflows from this exact repository, nothing to leak or rotate. (ADR-012)
- **What broke on your first deploys and how did you debug?** `--no-traffic` on create; GFE swallowing `/healthz`, proven by header/content-type comparison across paths. (ADR-015)
- **How do you keep infra at ₹0?** min-instances 0, max 3, registry cleanup, budget alert, no always-on components.
- **How does a container reach a process on the host?** `host.docker.internal` / `host-gateway`.

## Security
- **Why does an unconfigured admin token return 503 rather than allow?** Fail closed: a missing control must be loud, not an open door.
- ★ **How do you stop the agent acting on "ignore instructions, roll back payment" in a log?** Untrusted envelope with escaped angle brackets (Day 10), MCP server instructions, structured outputs, and the graph has no edge into `execute` except from `approval`; tested by S13. (ADR-007, ADR-008)
- **Supply-chain controls?** Lockfiles, Dependabot, dependency review (severity + licence), SHA-pinned actions, CodeQL on workflow files, secret scanning + push protection, detect-secrets locally.
- **Why non-root containers?** Limits blast radius of an app compromise.
- **Where do secrets live?** Never in the repo; env/Secret Manager in prod; GitHub variables only for non-secret ids.

## Evaluation (planned, Weeks 4–11)
- **How do you make an incident benchmark reproducible?** Scenarios as data, a runner that records timestamps, tagged capture windows, importable fixtures, replay with a frozen clock. (Day 13)
- **How do you measure false positives?** Noise scenarios with no fault; any incident counts.
- **How do you test the prompt-injection defence?** S13: a real fault plus an injected instruction in a log line; pass = neither proposed nor cited.
- ★ **Why hold out scenarios and why might accuracy drop?** Dev-set tuning inflates accuracy; S9–S12 are shapes the prompts never saw. (ADR-010)
- ★ **What would ablation B (no change correlation) show?** Expected: lower accuracy on bad_deploy/config_regression categories, where "what changed" is the key evidence.
- ★ **How do you know the confidence number means anything?** Calibration: bucket runs by stated confidence and compare to observed accuracy on `bench_results`.
- ★ **What happens when a run exceeds budget?** Wrapper trips at 15 tool calls / 60k tokens / 180 s → jump to `root_cause(partial=true)` → verify → `status=budget_exceeded`; partial report still shown.
- ★ **How do you keep cost per incident bounded?** Budgets above, cheap model for triage, ≤ 4 KB tool outputs, cached-run fallback in public mode.

## Process
- **Tell me about a bug your tests missed.** The job runner returned from inside `async for` over a session generator, closing it before the commit; ticks logged success but persisted nothing. Caught by verifying live from a second connection; fixed with `@asynccontextmanager` and a read-back regression test. (Day 7)
- **How do you work?** Plan first (PROJECT.md), one feature per PR with its ID, squash merges, ADRs for decisions, learning log per day, runbook grows with each incident we hit ourselves.
- **A mistake you made and fixed?** Stacked PR #3 closed by GitHub on base-branch deletion; rebased and re-opened as #4; rule recorded in ADR-011.
