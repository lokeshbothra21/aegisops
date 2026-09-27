# Day 17 · 27 Sep 2026 · Public run limits: who may spend the model quota (PR #32, E9.4)

## Why this came first
Yesterday production connected to Supabase, and starting a run is public by design: a visitor clicks "replay S1" and watches the agent work. Every run calls Gemini or Groq on free tiers. Without limits, one script in a loop could spend the day's quota, and the demo would be broken for everyone else. So limits come before any scenario is imported into production.

## What we did
- **Admission (`runs/limits.py`).** Before any run starts, `admit` decides:
  - valid `X-Admin-Token` → `admin`, no limits;
  - not public mode (laptop, CI) → `local`, no limits;
  - otherwise a visitor → `public:<hash>`, and two rules apply.
- **Rule 1: one running run per visitor.** A second start while yours is running → **429** "One run at a time", `Retry-After: 30`, and the detail names your run so the UI can reattach to its stream.
- **Rule 2: a daily cap** (`AEGIS_PUBLIC_DAILY_RUN_CAP`, default 30 new visitor runs per UTC day). Past it, the visitor gets the incident's **last finished run** (`served: "cached"`, 200). Only if there is none: 429 with `Retry-After` = seconds until 00:00 UTC.
- **Visitor runs end at the proposal.** Nobody can approve on the public deployment, so a paused run would stay "active" forever and lock its visitor out after one click. Visitor runs now finish as `succeeded` with the remediation marked `not_offered`, and the stream ends with `approval_not_offered` then `end`. Admin runs still pause for approval.
- **`POST /api/v1/scenarios/{key}/replay`** (§7): the demo's one button. It finds the scenario's captured incident; if a run on it is already live, you **join** it (`served: "joined"`, free, everyone watches one stream); otherwise admission runs and a new run starts.
- **Stalled runs stop blocking.** A run `running` for more than 10 minutes no longer counts, for the visitor limit or for "one active run per incident".
- **Migration `0007`**: `runs.requested_by` plus an index on `(requested_by, started_at)`, which is exactly the shape of the two counting queries.
- `AEGIS_TRUST_FORWARDED_FOR=true` in the deploy workflow. ADR-018. 173 tests.

## How it flows
```
POST /scenarios/S1/replay
  └─ scenario → its incident
       ├─ live run on it?  → 200 served=joined   (no quota spent)
       └─ admit()
            ├─ admin token        → start, pauses for approval as before
            ├─ not public mode    → start
            └─ visitor
                 ├─ BEGIN; pg_advisory_xact_lock(AEGIS4)   one decision at a time, across instances
                 ├─ my running run?       → 429 One run at a time
                 ├─ public runs today ≥ cap
                 │     ├─ finished run exists → 200 served=cached
                 │     └─ none               → 429 until 00:00 UTC
                 └─ start run (requested_by=public:<hmac>)  → 202 served=new
                    COMMIT  (lock released)
```

## Terms introduced

**Rate limiting / admission control.** Rate limiting caps how often something may happen. Admission control is the gate that applies it at the door, before work starts. We limit *runs*, not HTTP requests: reading a run is cheap, starting one spends quota.

**429 Too Many Requests + `Retry-After`.** The HTTP status for "you are over a limit", with a header telling the client how many seconds to wait. A well-behaved client (and our future UI) waits instead of hammering.

**Why the count lives in Postgres.** Cloud Run can run up to three copies (instances) of the app, each with its own memory, and it deletes them when idle. An in-memory counter would be three separate counters that reset to zero whenever an instance stops. The database is the one place every instance sees the same numbers. We did not even need a counter table: the `runs` rows already record who started what and when.

**Race condition, and the advisory lock that removes it.** Two visitors click at the same moment with one slot left. Both instances count 29, both see "under 30", both insert: 31 runs. That is a race: the result depends on timing. A **Postgres advisory lock** is a named lock the application defines (ours is the number `0x414547495334`, "AEGIS4"). `pg_advisory_xact_lock` waits until nobody else holds it and releases it automatically when the transaction ends. Wrapping "count, then insert" in it makes the pair atomic: the second visitor counts only after the first one's insert is committed.

**X-Forwarded-For (XFF).** Behind Cloud Run the TCP connection comes from Google's front end, not the visitor, so the socket address is useless. Google appends the real client IP to the `X-Forwarded-For` header. Anything *to the left* of Google's entry was sent by the client and can be forged ("I am 1.2.3.4"). So we take the **right-most** entry, and only when `AEGIS_TRUST_FORWARDED_FOR` says we are behind a proxy that sets it. Locally the socket peer is used.

**HMAC (keyed hash) instead of storing IPs.** An IP address is personal data, and we only need "same visitor or not". A plain SHA-256 of an IP is not private: there are only about 4 billion IPv4 addresses, so an attacker can hash them all and reverse it. **HMAC** mixes in a secret key (the production admin token), so without the key the hash cannot be recomputed. We store 16 hex characters: `public:3fa1…`.

**Cached-run fallback (graceful degradation).** When a limit is hit, serve something useful instead of an error. A visitor past the cap still sees a complete investigation of the same incident, just not a fresh one. `served` tells the UI which it got.

**Join instead of duplicate.** If a run on this incident is already live, a second visitor gets the same run and stream. That costs no quota, and it keeps the "one active run per incident" rule without a confusing 409.

**Stale run.** On Cloud Run, CPU is throttled to almost nothing when no request is open (ADR-009: the run advances while someone watches its SSE stream). A run nobody watches can stall in `running`. After 10 minutes, far beyond the 180-second agent budget, it stops counting as live, so it cannot lock a visitor or an incident forever.

## Decisions
- ADR-018: admission in the app, counted in Postgres, with an advisory lock; rejected in-memory counters (per instance), Redis (a new paid service), Cloud Armor (needs a load balancer, and it limits requests rather than runs).

## Honest gaps
- Right-most XFF is the documented behaviour for direct Cloud Run ingress. It cannot be proven live until production has an incident to run on (after the S1 import); the check then is two requests with forged left entries producing one visitor key.
- If the providers run out **during** a run, the run still ends as the router's partial report, not a cached run. The admission-time fallback covers the cap only.

## Interview questions
1. *How do you rate-limit across several instances?* Keep the state in the shared database. Our runs table already holds requester, status and start time, so two indexed counts answer both rules. Make check-then-insert atomic with a transaction-scoped advisory lock.
2. *What is a race condition? Give one from your project.* Two visitors take the last daily slot at once; both count 29 and both insert. `pg_advisory_xact_lock` serialises the decision.
3. *How do you get the client IP behind a proxy, and why not the left-most XFF entry?* The proxy appends the true peer on the right; everything to its left is client-controlled and forgeable.
4. *Why HMAC and not SHA-256 for the IP?* The IPv4 space is small enough to brute-force a plain hash; a secret key makes it one-way for anyone without the key.
5. *What happens when the demo is over its limit?* It serves the last finished run for that incident; a 429 with `Retry-After` only when there is nothing to show.
