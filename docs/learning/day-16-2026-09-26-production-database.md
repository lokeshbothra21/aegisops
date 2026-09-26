# Day 16 · 26 Sep 2026 · Production database: Supabase, secrets, keep-alive (PR #29, E11.5)

## What we did
- **Supabase** (Postgres 17.6, Singapore) migrated to schema `0006` with the same Alembic migrations the laptop and CI use: thirteen tables, 12 MB of the free tier's 500 MB.
- **Secrets in GCP Secret Manager:** `aegis-database-url`, `aegis-admin-token` (new, random, production-only), `aegis-gemini-api-key`, `aegis-groq-api-key`. Created from `.env` by piping values into `gcloud` so nothing was printed; the Cloud Run runtime service account already had `secretAccessor`.
- **Deploy:** `deploy-api.yml` mounts the secrets as environment variables (`--set-secrets`) and sets `AEGIS_PUBLIC_MODE=true`: production can investigate and propose but never executes an action (§10.3). `/readyz` is now a **hard gate**: a revision that cannot reach Supabase never receives traffic.
- **Keep-alive (E11.5):** the uptime workflow now requires `/readyz` to answer 200 every 6 hours. That is a `SELECT 1` on Supabase, which stops the free-tier project from pausing after 7 idle days, and a red run if it ever does.

## Live result
LIVE_RESULT_PLACEHOLDER

## Terms introduced

**Connection pooler (Supavisor).** A proxy in front of Postgres that holds a small set of real database connections and hands them to many clients. *Session mode* (port 5432) gives each client a connection for its whole session, so prepared statements work (asyncpg and psycopg need that). *Transaction mode* (6543) shares connections per transaction and breaks prepared statements unless the driver disables them.

**IPv6-only direct host.** New Supabase projects expose `db.<ref>.supabase.co` over IPv6 only; Cloud Run egress is IPv4 by default. The pooler host has an IPv4 address, which is why production uses it.

**Pooler tenant routing.** The pooler username is `postgres.<project-ref>`; the pooler for the wrong region answers "tenant/user not found". That is how the real region (ap-southeast-1) was found without guessing further.

**Secret Manager + `--set-secrets`.** Secrets live in GCP, versioned; Cloud Run resolves `name:latest` at start and injects the value as an environment variable. The repository and the deploy logs never contain the value. Rotating a key is "add a version, redeploy".

**Readiness as a deploy gate.** Liveness says the process runs; readiness says it can serve (database reachable). Gating traffic on readiness turns "wrong password" or "database paused" into a failed deploy instead of an outage.

**Keep-alive.** Free tiers pause idle resources. A scheduled cheap query is the standard fix; doing it through the real health endpoint also tests the full path (DNS, pooler, credentials).

**Public mode.** A deployment flag: the policy caps autonomy at 1 and `execute` records "execution disabled". The public demo can show every step up to approval without being able to change anything.

## Interview questions
1. *Why does production connect through a pooler?* IPv4 reachability from Cloud Run, and a bounded number of real connections for a scale-to-zero service; session mode keeps prepared statements working.
2. *How do secrets reach the container?* Secret Manager, mounted by Cloud Run at start via `--set-secrets`; least-privilege accessor role on the runtime service account; never in the repo or logs.
3. *What stops a bad database config from causing an outage?* `/readyz` gates the traffic shift; the old revision keeps serving.
4. *How do you keep a free-tier database from pausing?* The uptime cron requires `/readyz`, a real `SELECT 1`, every 6 hours.
