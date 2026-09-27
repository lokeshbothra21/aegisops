# ADR-018: Public run admission is enforced in the app, counted in Postgres

**Status:** Accepted, 27 Sep 2026

## Context
On the public deployment anyone may start an investigation, and every run spends free-tier model quota (Gemini, Groq). §11 names the threat ("bot spams replay") and the controls: one concurrent run per visitor, a global daily cap, a cached-run fallback. Cloud Run may run up to three instances, each with its own memory, and scales to zero between visits.

## Decision
- An `admit` step (`runs/limits.py`) runs before every run start: a valid admin token passes; outside public mode everything passes; a visitor is limited.
- **State lives in Postgres**, on the `runs` rows themselves: `requested_by` (`admin`, `local`, or `public:<HMAC of IP>`) plus `status` and `started_at` are enough to count "running for this visitor" and "public runs since 00:00 UTC". No new table, no counter to drift from reality.
- **A transaction-scoped advisory lock** (`pg_advisory_xact_lock`) serialises admission, so check-then-insert is atomic across instances.
- **Past the cap, serve the incident's last finished run** (`served: "cached"`), 429 with `Retry-After` only if there is none.
- **Visitor runs end at the proposal** (`decision: not_offered`): nobody can approve on the public deployment, and a paused run would count as active forever.
- **Scenario replay joins a live run** instead of starting a second one on the same incident.
- Client IP = right-most `X-Forwarded-For` entry, only when `AEGIS_TRUST_FORWARDED_FOR=true` (Cloud Run); stored only as an HMAC.

## Alternatives considered
- **In-memory counters (e.g. slowapi).** Each instance counts separately and forgets on scale-to-zero; three instances triple the cap.
- **Redis / Memorystore.** The textbook rate-limit store, but a new paid service for one counter; Postgres already holds the truth.
- **Cloud Armor or API Gateway rate limiting.** Needs a load balancer (a monthly cost) and limits requests, not runs: it cannot tell "start a run" from "read a run" cheaply, nor serve a cached run.
- **A separate `rate_limits` table.** A second source of truth that must be kept in sync with `runs`.

## Consequences
- The cap is approximate only in one harmless way: stalled runs older than 10 minutes stop counting as running (Cloud Run throttles CPU when no request is open, so an unwatched run can stall).
- Admission adds one indexed count query and a lock held for milliseconds; fine at demo traffic, and the lock would be the first thing to revisit at real scale.
- The "quota exhausted mid-run" fallback is still the router's partial report, not a cached run; a follow-up can serve the cached run when a run fails on provider errors.
- The right-most `X-Forwarded-For` rule assumes direct Cloud Run ingress; behind an external load balancer the client is the second-from-right entry.
