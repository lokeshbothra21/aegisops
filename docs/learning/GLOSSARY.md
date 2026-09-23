# Glossary

Alphabetical. "D0" = first appeared on Day 0, etc. Full explanations live in the day files.

| Term | Meaning | Day |
|---|---|---|
| Ablation | Removing one component to measure its contribution; variants A/B/C/D of the agent | D0 |
| ADR | Architecture Decision Record: context, decision, alternatives, consequences, one page | D0 |
| Agent (LLM) | A program where a model picks tools and conclusions step by step within a budget | D0 |
| Alembic | Schema migration tool for SQLAlchemy; `upgrade head` applies versioned migrations | D2 |
| AnyValue | OTLP's tagged union for attribute values (string/int/double/bool/bytes/array/kvlist) | D3 |
| Application factory | `create_app(settings)` builds an isolated app instance per test | D1 |
| Artifact (CI) | A downloadable output of a job (coverage.xml) | D5 |
| Artifact Registry | GCP container image registry; ours has a keep-5 / delete-30-days cleanup policy | D5 |
| ASGI | Async interface between a Python web app and its server (uvicorn) | D1 |
| asyncpg | Fast asyncio Postgres driver used by SQLAlchemy | D2 |
| Attribute (OTel) | Key/value fact on a resource, scope, span, log or metric point | D3 |
| Batching | Grouping items into one request; exporter batches ~200–1000 items | D4 |
| Budget alert | GCP billing alert; ours ₹500 at 50 %, 100 %, forecast | D5 |
| Cache (CI) | Reused files across runs keyed on a hash (uv.lock, Docker layers) | D5 |
| CodeQL | GitHub semantic code analysis for security bugs; Python + workflow files | D5 |
| Composite index | Index over several columns; leading column must be in the filter | D2 |
| Concurrency group | Cancels or serialises workflow runs on the same ref | D5 |
| Connection pool | Reused DB connections; `pool_pre_ping` detects dead ones | D2 |
| Connector (collector) | Exporter of one pipeline that is the receiver of another (span_metrics) | D4 |
| Content-Encoding gzip | Compressed request body; server must inflate; check size before and after | D3 |
| Conventional Commits | `type(scope): message` commit format | D1 |
| Coverage | Share of code lines executed by tests | D3 |
| Dependabot | GitHub bot for vulnerability alerts and scheduled dependency-update PRs | D5 |
| Dependency review | PR check that blocks vulnerable or badly-licensed new dependencies | D5 |
| Denormalisation | Copying `service` onto every row to avoid joins | D2 |
| Deterministic | Same input → same output; used for detection and verification | D0 |
| Docker Compose / override | Multi-container config; later `-f` files merge over earlier ones | D2, D4 |
| docker_stats receiver | Collector receiver scraping container CPU/memory from the Docker socket | D4 |
| Entrypoint script | Container start script: migrate if DB configured, then `exec uvicorn` | D5 |
| Evidence verification | Code checks every LLM-cited reference exists and numbers are within ±20 % | D0 |
| executemany | One INSERT with many rows; basis of 20k spans/s ingest | D3 |
| Exporter (collector) | Output of a pipeline (otlphttp/aegisops, otlp_grpc/jaeger) | D4 |
| Fan-out | One receiver feeding several pipelines | D4 |
| FastAPI | Async Python web framework with Pydantic validation and OpenAPI | D1 |
| Fault injection | Deliberately breaking a system (flag toggle) to test response | D4 |
| Feature flag / flagd | Runtime switch; flagd serves flags from a JSON file it hot-reloads | D0 |
| Fixture (test) | Reusable test data/setup | D3 |
| Gauge / Sum / Histogram | OTel metric types: value now / counter / bucketed distribution | D3 |
| GFE (Google Frontend) | Google's edge proxy for run.app; reserves `/healthz` | D5 |
| GitHub Actions | GitHub CI/CD: workflows → jobs → steps, triggered by events | D5 |
| GitHub variables vs secrets | Non-secret config vs encrypted, masked values | D5 |
| Head vs tail sampling | Per-span decision on arrival vs per-trace decision after buffering | D4 |
| Health check (container) | Docker probe (`pg_isready`) gating `--wait` | D2 |
| Held-out set | Test cases never used during tuning; S9–S12 | D0 |
| HITL | Human-in-the-loop: approval node interrupts the graph | D0 |
| host.docker.internal | Hostname resolving to the host from inside a container | D4 |
| IAM role / service account | Google permission bundles / non-human identities | D5 |
| Integration test | Test against the real Postgres container | D2 |
| JSONB | Postgres binary JSON column; `attrs` | D2 |
| LangGraph | Agent framework: graph of nodes, shared state, checkpoints, interrupts | D0 |
| Layer caching (Docker) | Unchanged Dockerfile layers are reused; deps before source | D5 |
| Least privilege | Each identity/workflow gets only the permissions it needs | D5 |
| Lifespan | FastAPI startup/shutdown hook (engine create/dispose) | D1 |
| Liveness vs readiness | Process is up vs can serve traffic; `/livez`, `/readyz` | D1 |
| Lockfile | Exact resolved dependency versions (`uv.lock`); `UV_FROZEN` | D1 |
| Log severity number | OTel 1–24 scale; ERROR = 17 | D3 |
| MCP | Model Context Protocol: standard for exposing tools to LLM apps; reads only here | D0 |
| Metric allowlist | OTTL filter keeping only alert-relevant metric names | D4 |
| Microservices | Independently deployed services calling each other; failures propagate | D0 |
| Migration | Versioned, ordered schema change | D2 |
| Mixin | Class adding shared columns (`id`, `created_at`) | D2 |
| Monorepo | One repo, several packages | D1 |
| MoSCoW | Must/Should/Could/Won't prioritisation | D0 |
| Multi-stage build | Builder stage installs; runtime stage copies only the result | D5 |
| mypy --strict | Static type checking, all strictness on | D1 |
| NFR | Non-functional requirement (latency, cost, throughput, coverage) | D0 |
| Non-root container | `USER aegis`; limits blast radius | D5 |
| Observability | Understanding a system from its logs, metrics and traces | D0 |
| OIDC token | Short-lived signed identity token GitHub mints per workflow run | D5 |
| ORM | Python classes mapped to tables (SQLAlchemy declarative) | D2 |
| OTel Collector | Receives, processes, exports telemetry via YAML pipelines; arrays replace | D4 |
| OTLP | OpenTelemetry Protocol; protobuf over gRPC/HTTP; JSON variant with hex ids | D3 |
| OTTL | Collector transformation language for filter/transform processors | D4 |
| Partial success | OTLP response reporting dropped items without failing the batch | D3 |
| pgvector | Postgres extension for vector similarity search | D2 |
| Pinning by SHA | `uses: action@<commit>`; immutable, unlike tags | D5 |
| Pipeline (collector) | receivers → processors → exporters for one signal | D4 |
| pre-commit / detect-secrets | Commit-time checks incl. secret detection with a baseline | D1 |
| Probe-before-traffic | Deploy no-traffic revision, probe tagged URL, then shift traffic | D5 |
| Processor (collector) | Transform step (batch, filter, tail_sampling, memory_limiter) | D4 |
| Prompt injection | Data that tries to instruct the model; defended by wrapping + topology | D0 |
| Protobuf | Binary schema-based serialisation; OTLP's native format | D3 |
| Pure function | No I/O; output depends only on input (`convert.py`) | D3 |
| Pydantic | Validation from type hints; every boundary is a model | D1 |
| Rebase / force-with-lease | Replay commits on a new base; safe force push | D3 |
| Receiver (collector) | Input of a pipeline (otlp, docker_stats) | D4 |
| Replay mode | Serve captured telemetry by `scenario_id`; same code path as live | D0 |
| Resource / scope (OTel) | Producer identity / instrumentation library | D3 |
| Retention | Delete untagged rows after 24 h | D2 |
| Revision (Cloud Run) | Immutable deployment; traffic split and tags per revision | D5 |
| RFC 7807 | `application/problem+json` error format | D3 |
| Root cause vs symptom | The failing thing vs the visible effect | D0 |
| Ruff | Linter + formatter | D1 |
| Runner | Ephemeral VM a CI job runs on | D5 |
| Sampling policy | Rule in tail_sampling (status_code, probabilistic) | D4 |
| Schema drift test | Asserts live DB equals ORM models | D2 |
| Semantic conventions | Standard OTel attribute names | D3 |
| Service container (CI) | Sidecar container (Postgres) for a job | D5 |
| Squash merge | PR becomes one commit on main | D1 |
| span_metrics | Connector computing calls/duration metrics from traces | D4 |
| Span / trace | One operation / the tree of spans for one request | D0 |
| Span kind / status / events / links | SERVER/CLIENT/…; UNSET/OK/ERROR; timestamped annotations; cross-trace refs | D3 |
| Stacked PR | PR based on an unmerged branch; GitHub closes it when the base is deleted | D3 |
| structlog | Structured (key=value / JSON) logging | D1 |
| Supabase | Hosted Postgres free tier for prod; pauses after 7 idle days | D2 |
| Surrogate key | `id bigserial` unrelated to the data | D2 |
| Temporality | CUMULATIVE vs DELTA for sums/histograms | D3 |
| 12-factor config | Config from environment variables only | D1 |
| 413 / 415 | Payload too large / unsupported media type | D3 |
| uv / workspace | Fast Python project manager; multi-package repo with one lockfile | D1 |
| Version pinning | Fixed demo tag 3.0.0 checked by `make demo-check` | D4 |
| WIF | Workload Identity Federation: swap GitHub's OIDC token for Google credentials, keyless | D5 |
| Admin token (`X-Admin-Token`) | Shared-secret header auth for mutating routes; unset → 503 (fail closed) | D6 |
| CTE (`WITH ... AS`) | Named subquery inside a statement | D6 |
| `date_trunc` window | Bucketing timestamps into fixed windows (hour, 5 min) | D6 |
| Derived table | Aggregate computed by a job so hot queries are one indexed read (`service_edges`) | D6 |
| Fail closed | Deny when a control is unconfigured | D6 |
| Idempotent job | Re-running has no extra effect (delete-then-insert per window) | D6 |
| `IS NOT DISTINCT FROM` | Null-safe SQL equality | D6 |
| Job runner (asyncio) | Lifespan-managed periodic tasks; one session per tick; failures logged not fatal | D6 |
| p95 / `percentile_cont` | 95th percentile latency; interpolating percentile in Postgres | D6 |
| Retention policy | Delete untagged rows after 24 h; fixtures exempt | D6 |
| `rowcount` / `CursorResult` | Rows affected by DML in SQLAlchemy 2 | D6 |
| `SecretStr` | Pydantic type that masks secrets in logs | D6 |
| Service dependency graph | Services as nodes, calls as edges, built from traces | D6 |
| Service edge | (hour, caller, callee, call_count, err_count, p95_ms) | D6 |
| Actor | Who made a change: `flagd-file`, later `agent`, `admin`, `policy:auto` | D7 |
| Alert rule | `metric comparator threshold` over a window, for N consecutive windows; stored as data | D7 |
| Async context manager | `async with` block whose exit code always runs; safer than a bare async generator | D7 |
| Baseline snapshot | First read of a watched file records nothing; later diffs become events | D7 |
| Change event | Record of a flag/deploy/restart/scale/commit with before/after JSON | D7 |
| Enum storage (VARCHAR + CHECK) | `native_enum=False`; values evolve without a type migration | D7 |
| Foreign key | DB-enforced reference (`incidents.alert_rule_id`) | D7 |
| Incident | One service, one rule, a status, an autonomy level; runs attach to it | D7 |
| Keyset / cursor pagination | `WHERE id < cursor LIMIT n+1`; stable and index-friendly | D7 |
| mtime polling | Detect file changes by modification time on an interval | D7 |
| Regression test | Pins a fixed bug so it cannot return | D7 |
| State machine (explicit) | All legal transitions in one table; `transition()` is the only mutator | D7 |
| Target config | `config/targets/*.yaml`: system-specific names kept out of code | D7 |
| Terminal state | No exits; sets `closed_at` | D7 |
| Alerting rule / `for` duration | Expression over a window that must hold for N evaluations before firing | D8 |
| Baseline | "Normal" for comparison; ours is the previous hour of the same metric | D8 |
| Counter reset | Cumulative counter restarts at 0 after a process restart; window deltas undercount | D8 |
| Cumulative vs delta | Ever-increasing total vs per-interval increment; window value = max − min of a cumulative | D8 |
| Flapping | Alert oscillating between firing and resolved | D8 |
| Hysteresis / recovery windows | Several healthy evaluations before resolving | D8 |
| Minimum sample size | Below `MIN_CALLS` a rate is noise; no reading | D8 |
| Percentile from buckets | Interpolate inside the bucket where cumulative count crosses q | D8 |
| Rate vs ratio rule | Fraction of the window's traffic vs comparison to a baseline | D8 |
| `Retry-After` | HTTP header: when to retry; sent with 503 on DB outage | D8 |
| Streak state | Consecutive breaching/healthy ticks per (rule, service), kept in memory | D8 |
| Test pollution | Shared rows left by one test changing another's behaviour; fix with unique names + cleanup | D8 |
| Detection floor | Latency bound set by traffic rate × minimum sample size; not fixable by faster ticks | D8 |
| Error burst rule | Absolute count of error server spans in a window; exact under tail sampling | D8 |
| Cascade (alert) | Downstream services alerting because an upstream one fails; one incident per service, merged later by the agent | D8 |
| `metrics_flush_interval` | How often the span-metrics connector emits counters (10 s in our layer) | D8 |
| Cold start | First request to a scaled-to-zero service takes seconds; monitors must retry | D9 |
| Compose labels | `com.docker.compose.project` / `.service` on every container | D9 |
| Docker Engine API | The daemon's HTTP API on a Unix socket (`/containers/json`, `/containers/{id}/json`) | D9 |
| Image tag vs image id | Mutable name vs content hash; compare both to catch silent re-pulls | D9 |
| Mock transport | `httpx.MockTransport`: script HTTP responses in tests | D9 |
| RestartCount / StartedAt | Docker counters that separate crash-restarts from manual restarts | D9 |
| Scheduled workflow | GitHub Actions `schedule:` cron; our uptime ping | D9 |
| Unix domain socket | File-system IPC endpoint; httpx `uds=` | D9 |
| asyncpg parameter typing | Ambiguous SQL parameters need `CAST(:p AS type)` | D10 |
| Baseline comparison (tools) | Same statistic over now and before, returned as a delta | D10 |
| Frozen now | Replay's fixed "now"; every relative window hangs off it | D10 |
| Hour-labelled bucket | Row keyed by the hour it starts in; extend query end by 1 h | D10 |
| Input schema (MCP) | JSON Schema of a tool's arguments; ours derived from the Python signature | D10 |
| Instructions (MCP) | Server-level text passed to the model; states the untrusted rule | D10 |
| Log signature | Numbers/hex/whitespace collapsed so similar lines group | D10 |
| MCP server / tool | Advertises callable tools to LLM clients over a transport | D10 |
| Output escaping | `<`/`>` → `\u003c`/`\u003e` so a closing tag cannot be forged from data | D10 |
| Size cap / graceful truncation | Trim list tails to fit 4 KB, mark `truncated: true` | D10 |
| Span tree flattening | Trace as (depth, service, name, kind, status, ms) rows | D10 |
| stdio transport | JSON-RPC over a subprocess's stdin/stdout | D10 |
| Untrusted-content envelope | `<telemetry untrusted="true">…</telemetry>` around every tool result | D10 |
| Depth-first tree focus | Show error spans, ancestors and children first; fill in tree order | D10 |
| Destructive test direction | Pick cutoffs so real data can never qualify (past, not future) | D10 |
| Unit normalisation | Read the `unit` column and convert per series before merging | D10 |
| Argument filtering | Only a tool's real parameters are forwarded; invented ones are dropped | D11 |
| Budget wrapper | Counts tool calls/tokens/time; a breach routes to a partial root cause | D11 |
| Cassette (recorded model) | Canned per-node model outputs replayed in tests and replay demos | D11 |
| Checkpointer / thread_id | State persisted after each node under a thread; resume and interrupts | D11 |
| Conditional edge | Graph edge whose target is chosen from state | D11 |
| `include_object` (Alembic) | Hook to exclude foreign tables (LangGraph's `checkpoint*`) | D11 |
| JSON mode / response schema | Model asked for schema-conforming JSON; validated with Pydantic | D11 |
| LangGraph StateGraph | Nodes = functions of state; fixed topology; checkpointed | D11 |
| Model router / fallback | One place that knows providers; per-node models; secondary on 429/5xx | D11 |
| Node-scoped tool authorization | Allowlist of tools per node, enforced at execution | D11 |
| Plan-then-execute | Planner names tools; deterministic execution; one call to interpret (ADR-016) | D11 |
| Prompt versioning | Prompts as files; version recorded in run state | D11 |
| Retryable vs non-retryable | 429/5xx/timeout → fallback; 4xx → fail fast | D11 |
| TypedDict state | LangGraph state as a dict of declared, JSON-serialisable keys | D11 |
| Weak vs strong copyleft | LGPL/MPL: import freely, share library changes; GPL/AGPL: whole program (and network use) | D11 |
