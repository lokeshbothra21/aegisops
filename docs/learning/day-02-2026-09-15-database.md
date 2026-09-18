# Day 2 · 15 Sep 2026 · Postgres, schema and migrations (PR #2, E1.2)

## What we did
Local Postgres 17 with pgvector via docker-compose (host port 5433), async SQLAlchemy engine, ORM models for `spans`, `logs`, `metric_points`, Alembic with the first migration, a drift test, and `/readyz` pinging the DB.

## Terms introduced

**PostgreSQL 17.** The relational database. Chosen as the *single* store (ADR-003). Version 17 to match new Supabase projects.

**pgvector.** A Postgres extension adding a `vector` column type and similarity search (cosine/L2) with HNSW indexes. Used later for postmortem embeddings ("find similar past incidents").

**Supabase.** Hosted Postgres with a free tier (500 MB), used for production. Pauses after 7 days idle, hence the planned keep-alive cron.

**Docker Compose.** Declarative multi-container setup. `docker-compose.yml` defines the `aegis-postgres` container with a health check and a named volume so data survives restarts. Host port 5433 because the ERP's Homebrew Postgres already uses 5432.

**Health check (container).** `pg_isready` polled by Docker; `docker compose up --wait` blocks until healthy, so `make db-up` returns only when the DB accepts connections.

**SQLAlchemy 2.0 (async).** The Python SQL toolkit and ORM. 2.0 style uses `Mapped[...]` type annotations for columns and an `AsyncEngine`/`AsyncSession` over **asyncpg**, the fastest Postgres driver for asyncio.

**Connection pool.** Reused DB connections (`pool_size=5, max_overflow=5`). `pool_pre_ping=True` tests a connection before use so a paused Supabase does not surface as a random failure.

**ORM model / declarative base.** A Python class per table; `Base.metadata` describes the whole schema and is what Alembic compares against.

**Mixin.** `TimestampedRow` adds `id bigserial` and `created_at` to every table without repeating code.

**Alembic.** Schema migration tool for SQLAlchemy. Each migration is a versioned Python file with `upgrade()`/`downgrade()`. `alembic upgrade head` applies all; `--autogenerate` diffs models vs database to draft a migration. Rule: one migration per PR that touches schema; never edit an applied migration.

**Schema drift test.** `compare_metadata()` asserts the live database equals the models. If someone hand-edits the DB or forgets a migration, CI fails.

**Denormalisation.** Copying `service` onto every span/log/metric row so hot queries ("errors for checkout in the last 5 min") never join. A deliberate trade of storage for query speed.

**JSONB.** Postgres' binary JSON column. `attrs` keeps every OTLP attribute without a schema change per attribute; `attrs->>'http.response.status_code'` queries it; GIN indexes can index it later.

**Composite index.** An index over several columns in order, e.g. `(service, start_ts)`: fast for "this service, this time range". Column order matters: leading column must be in the WHERE.

**BigInteger surrogate key.** `id bigserial`: an auto-incrementing 64-bit primary key unrelated to the data. Telemetry has no natural unique key we trust (span ids can collide across traces).

**Timezone-aware timestamps.** `DateTime(timezone=True)` = `timestamptz`; everything is stored in UTC and rendered later.

**Retention.** Untagged rows (no `scenario_id`) are deleted after 24 h (E1.5); tagged rows are permanent fixtures.

**Integration test.** A test that talks to the real Postgres container, as opposed to a unit test with no I/O. `conftest.py` builds an in-process app with `httpx.ASGITransport`, so tests hit the real routes without a network socket.

## Interview questions
1. *Why denormalise `service` onto every row?* The hot path is per-service filtering; a join per query at 500 spans/s is the wrong trade.
2. *Why JSONB for attributes instead of columns?* Attribute sets differ per language/SDK and evolve; JSONB keeps everything; promote a key to a column only when a query needs an index.
3. *How do you guarantee the schema in prod equals the code?* Alembic migrations are the only way the schema changes, and a CI test asserts zero drift.
4. *Why `pool_pre_ping`?* Hosted DBs drop idle connections; pre-ping turns a stale connection into a transparent reconnect instead of a 500.
