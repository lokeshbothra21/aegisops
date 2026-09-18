# ADR-003: Postgres is the single store for telemetry, agent state and vectors

**Status:** Accepted, 13 Sep 2026

## Context
The agent needs to query spans, logs and metrics; the graph needs durable checkpoints; incident memory needs vector similarity search. The obvious "correct" stack is a tracing backend (Jaeger), a metrics backend (Prometheus), a log store (OpenSearch), a relational DB and a vector DB. The budget is ₹0/month and one person.

## Decision
One Postgres 17 database (pgvector extension) holds everything: raw telemetry rows (`spans`, `logs`, `metric_points`), derived tables (`service_edges`, `change_events`), incidents/runs/evidence, LangGraph checkpoints, and postmortem embeddings. Local: a `pgvector/pgvector:pg17` container. Production: Supabase free tier.

## Alternatives considered
- **Query Jaeger + Prometheus + OpenSearch APIs directly.** Three query languages, three clients, no way to freeze a window for replay, and the demo's backends are not persistent.
- **ClickHouse.** Excellent for telemetry, but no free hosted tier that fits ₹0, and a second store for state/vectors would still be needed.
- **Vector DB (Pinecone/Qdrant) for memory.** pgvector at our scale (hundreds of postmortems) is indistinguishable in quality.

## Consequences
- Replay mode is trivial: tag rows with `scenario_id` and query by it (ADR-004).
- Tools are thin SQL aggregates, which keeps LLM context small (every tool response ≤ 4 KB).
- Volume must be controlled: the collector layer samples traces and allowlists metrics (ADR-014), and a 24 h retention job deletes untagged rows.
- Denormalised `service` column on every row so hot queries never join; JSONB `attrs` keeps everything else.
