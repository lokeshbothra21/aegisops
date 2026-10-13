# ADR-024: `/ingest` takes a bearer token, checked before the body, and fails closed in production

**Status:** Accepted, 13 Oct 2026

## Context
`POST /ingest/v1/{traces,logs,metrics}` was built for one producer: our own Collector on the same laptop (ADR-013). It had no authentication. The same routes ship in the production image, and production is a public Cloud Run URL. Anyone who knew the URL could write spans, logs and metrics into the production database. That would fill the free Supabase tier and plant text that the agent's tools later read, a prompt-injection path that skips our own S13 scenario. ADR-023 also makes `/ingest` the front door for any real company. The gap was found while answering "how would a company connect?" on 13 Oct.

## Decision
- **`Authorization: Bearer <AEGIS_INGEST_TOKEN>`** on every ingest route. The Collector's `otlphttp` exporter sends it through its standard `headers:` setting; our scenario runner sends it for S13's injected log line.
- **The check is a router-level dependency, so it runs before the handler reads the body.** An unauthenticated sender gets a 401 before we read, size-check or gunzip anything. This matters because the body can be a gzip bomb.
- **Constant-time comparison** (`hmac.compare_digest`). The admin-token check moves to it too; it had used `!=`.
- **401 carries `WWW-Authenticate: Bearer`** and a problem+json body. The Collector treats 4xx as permanent and does not retry, which is right for a wrong key.
- **Unset token:**
  - Production (`AEGIS_ENV=prod`): the routes answer **503 "Ingest disabled"**, the same fail-closed rule as the admin routes.
  - Laptop (`local`/`test`): the routes stay **open**, because the only producer is our Collector on the same machine and a fresh clone should work with no setup.
  - The startup log says which mode is active (`ingest=token|open|disabled`).
- **Production gets no token for now.** It has no producer: its data comes from scenario imports. So production ingest is simply closed. A secret `aegis-ingest-token` gets added when a real producer exists.

## Alternatives considered
- **Cloud Run IAM (`--no-allow-unauthenticated`).** Google checks an identity token before the request reaches us. But the same service serves the public demo, so it has to allow unauthenticated requests. Splitting ingest into a second service would double the deploy surface for one route.
- **mTLS between Collector and API.** The strongest option, and the Collector supports it, but Cloud Run's front end terminates TLS and doesn't pass client certificates through. It also means certificate issuance and rotation for every producer.
- **Store only a hash of the token.** Worth it once tokens live in a database, one per customer (ADR-023). With one token in Secret Manager it adds a step without changing who can read the secret. Revisit with multi-tenancy.
- **Drop the ingest routes from the production image.** That closes the hole today but breaks the ADR-023 path. Fail-closed configuration gives the same safety with no code fork.
- **Require the token on the laptop too.** Safer by default, but every `make demo-up` would need a matching secret on both sides, for a socket that only listens on the developer's machine.

## Consequences
- Production `/ingest` answers 503 from this deploy on. Nothing used it.
- A local API with `AEGIS_INGEST_TOKEN` set needs the same value in `AEGISOPS_INGEST_TOKEN` for the Collector, or exports fail with 401 and are dropped. The RUNBOOK covers this.
- One shared token can't tell producers apart or be revoked for one of them. That's fine for one producer; per-producer tokens come with multi-tenancy.
