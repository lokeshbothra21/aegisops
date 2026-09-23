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
| [017](ADR-017-dependency-licence-policy.md) | Dependency licence policy: strong copyleft denied, weak copyleft (LGPL/MPL) allowed as unmodified libraries |
