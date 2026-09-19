# Learning log

A day-by-day teaching record of AegisOps: what was built, every term it introduced explained from first principles, the decision and its alternatives, what broke, and interview questions with answers. Written so the author can explain every line of the repo in an interview.

- One file per working day: `day-NN-YYYY-MM-DD-topic.md`. Sections: What we did · How it flows (when useful) · Terms introduced · Decisions · Interview questions.
- [`GLOSSARY.md`](GLOSSARY.md): every term, two lines each, with the day it first appeared. Revision sheet.
- [`INTERVIEW.md`](INTERVIEW.md): the question bank grouped by topic. Each answer is the 60-second version.
- Decisions live in [`../adr/`](../adr/README.md); this log points at them rather than repeating them.

**Rule (PROJECT.md §20.1):** every PR that changes behaviour or introduces a tool/concept updates that day's file, the glossary and, when relevant, the question bank, in the same PR.

| Day | Date | Topic | PRs |
|---|---|---|---|
| [0](day-00-2026-09-13-plan-and-architecture.md) | 13–14 Sep | Plan, architecture, the demo, twelve decisions | — |
| [1](day-01-2026-09-14-scaffold.md) | 14 Sep | Monorepo, FastAPI, tooling, probes | #1 |
| [2](day-02-2026-09-15-database.md) | 15 Sep | Postgres 17 + pgvector, SQLAlchemy, Alembic, drift test | #2 |
| [3](day-03-2026-09-16-ingest.md) | 16 Sep | OTLP/JSON ingest, RFC 7807, stacked-PR lesson | #4 |
| [4](day-04-2026-09-17-collector.md) | 17 Sep | Collector layer, tail sampling, allowlist, first live fault | #5 |
| [5](day-05-2026-09-18-platform.md) | 18 Sep | CI, CodeQL, Dependabot, Docker, WIF, Cloud Run | #6–#12 |
| [6](day-06-2026-09-19-retention-service-edges.md) | 19 Sep | Retention, service edges, job runner, admin routes | #14 |
| [7](day-07-2026-09-19-incidents-change-events.md) | 19 Sep | Incident tables + lifecycle, flag change watcher, cursor pagination, the runner-commit bug | #15 |
