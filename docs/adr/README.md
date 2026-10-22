# Architecture Decision Records

One file per decision that shaped the system. Format: Context (the problem and forces), Decision, Alternatives considered, Consequences (good and bad), Status. Numbering follows PROJECT.md §22; 001–012 were made in the plan (13 Sep 2026), 013+ were made while building.

| ADR | Decision |
|---|---|
| [001](ADR-001-one-flagship-project.md) | One flagship project; the code-patching agent idea was rejected |
| [002](ADR-002-otel-demo-as-target.md) | The OpenTelemetry Demo (pinned) is the target system |
| [003](ADR-003-postgres-single-store.md) | Postgres is the single store for telemetry, agent state and vectors |
| [004](ADR-004-replay-mode.md) | Replay mode for the public demo, CI and benchmark |
| [005](ADR-005-rollback-first-remediation.md) | Operational remediation only, rollback first; no code patches |
| [006](ADR-006-langgraph-postgres-checkpointer.md) | LangGraph with a Postgres checkpointer |
| [007](ADR-007-mcp-reads-only.md) | MCP for read tools only; actions are an in-process module behind approval |
| [008](ADR-008-deterministic-alerting-and-verification.md) | Deterministic alerting and deterministic evidence verification |
| [009](ADR-009-agent-in-sse-request.md) | The agent runs inside the SSE request on Cloud Run, no worker |
| [010](ADR-010-held-out-scenarios.md) | Held-out scenarios S9–S12 evaluated once |
| [011](ADR-011-trunk-based-squash-conventional.md) | Trunk-based development, squash merges, Conventional Commits |
| [012](ADR-012-workload-identity-federation.md) | Keyless deploys with Workload Identity Federation |
| [013](ADR-013-otlp-json-only-ingest.md) | Ingest accepts OTLP/JSON only, not binary protobuf |
| [014](ADR-014-tail-sampling-and-metric-allowlist.md) | Tail sampling for traces and an allowlist for metrics in our collector layer |
| [015](ADR-015-livez-not-healthz.md) | Liveness probe is `/livez` because Google Frontend reserves `/healthz` |
| [016](ADR-016-plan-then-execute-tool-calling.md) | Plan-then-execute tool calling instead of a free-form tool loop |
| [019](ADR-019-langfuse-sdk-explicit-instrumentation.md) | Langfuse via the official SDK, instrumented at three seams, on a dedicated TracerProvider |
| [018](ADR-018-public-run-admission-in-postgres.md) | Public run admission enforced in the app and counted in Postgres (advisory lock), cached run past the cap |
| [017](ADR-017-dependency-licence-policy.md) | Dependency licence policy: strong copyleft denied, weak copyleft (LGPL/MPL) allowed as unmodified libraries |
| [020](ADR-020-dco-not-cla.md) | Outside contributions under the DCO (sign-off checked by a workflow), not a CLA |
| [021](ADR-021-browser-calls-api-directly.md) | The web UI's browser calls the API directly (CORS, regex full match), not through a Vercel proxy |
| [022](ADR-022-web-stack.md) | Web UI stack: Next.js 16 (security fixes), client-rendered pages, npm, Tailwind tokens without a component kit, Vitest + Playwright |
| [023](ADR-023-outbound-only-integration.md) | Outside the laptop, every integration is outbound from the customer: pushed telemetry and changes, notifications to customer-created endpoints, actions through a runner inside their network (design; not built in v1) |
| [024](ADR-024-ingest-bearer-token.md) | `/ingest` takes a bearer token checked before the body is read; open on a laptop when unset, 503 in production |
| [025](ADR-025-cpu-from-usage-counter.md) | CPU is the rate of `container.cpu.usage.total` in cores (rule > 1.5 cores), not docker's `container.cpu.utilization`, which acts as a lifetime average on Docker Desktop/OrbStack |
