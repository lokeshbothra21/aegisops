# Day 22 · 13 Oct 2026 · Locking the front door: the ingest token (PR #57, E9.8, ADR-024)

## Why
On Day 21, explaining how a company would send us telemetry, we found that **`/ingest` had no lock**. It was built for one sender, our own Collector on the same laptop. But the same code runs on the public Cloud Run URL, so anyone who found the URL could:
- **fill the database** with junk (Supabase's free tier is small);
- **plant text the agent later reads.** A fake log line saying "ignore your instructions" would go straight into the store the tools query. That's the prompt-injection attack our S13 scenario tests, delivered from outside.

## What we built
1. **A token, sent as a standard header:** `Authorization: Bearer <token>`. "Bearer" means "whoever holds this token is allowed in", like a ticket. The OTel Collector can add any header to what it sends, so the producer side is one line of config.
2. **Checked before the body is read.** The check runs as a FastAPI *dependency* on the whole ingest router, so it runs *before* the handler. A stranger's request is rejected before we read it, check its size or unzip it. Unzipping matters: a tiny gzip file can expand to gigabytes (a *zip bomb*). Why spend memory on someone we're about to reject?
3. **Compared in constant time.** Normal string comparison (`==`) stops at the first wrong character, so a wrong token that starts correctly takes a tiny bit longer to reject. Measuring that over thousands of tries can leak the token character by character (a *timing attack*). `hmac.compare_digest` always takes the same time. The admin-token check used `!=`; it now uses `compare_digest` too.
4. **The right error codes:**
   - **401 Unauthorized** plus `WWW-Authenticate: Bearer` for a missing or wrong token. The Collector treats 4xx as "don't retry", which is right for a wrong key.
   - **503 "Ingest disabled"** when production has no token configured at all.
5. **Fail closed in production, open on the laptop:**

| Token set? | Laptop (`local`/`test`) | Production (`prod`) |
|---|---|---|
| Yes | Token required | Token required |
| No | Open: the only sender is our own Collector on the same machine | **503: closed** |

   The startup log prints which mode is active: `ingest=token`, `open` or `disabled`.
6. **Production stays closed for now.** It has no sender: its data comes from imported scenario captures. So we didn't create a secret. Production ingest is simply off until a real sender exists.
7. **Senders updated:** the Collector config sends `Authorization: Bearer ${AEGISOPS_INGEST_TOKEN}`, and `aegis-scenario run` gains `--ingest-token` for S13's injected log line.

## How we checked it
- **13 new tests:**
  - the right token works on traces, logs and metrics;
  - six kinds of wrong header all get 401: none, empty, `Basic`, wrong token, `Bearer` with nothing after it, and the token with an extra character;
  - a wrong token gets 401 even with a body that would otherwise be rejected as the wrong type or too large (proof that the body isn't read);
  - `bearer` in lowercase works;
  - no token is open locally and 503 in prod;
  - the runner sends the header.
- **Against a real API process:** no header → 401, wrong token → 401, right token → 200.

## Decisions and alternatives (ADR-024)
- **Why not Google's own auth (Cloud Run IAM)?** It would lock the whole service, and the public demo lives on the same URL.
- **Why not mutual TLS (both sides present certificates)?** It's stronger, but Cloud Run's front door ends TLS before our code sees it, and every sender would need certificates to manage.
- **Why not store a hash of the token?** With one token in Secret Manager, a hash doesn't change who can read the secret. Once every customer has their own token in a database (ADR-023), we will store hashes.

## Terms introduced

**Bearer token.** A secret sent in `Authorization: Bearer <token>`. Whoever bears it is let in, so it must travel only over HTTPS and never be logged.

**Constant-time comparison.** Comparing secrets in a way that always takes the same time whatever the input, so response timing can't leak how much of a guess was right (`hmac.compare_digest`).

**Timing attack.** Recovering a secret by measuring tiny differences in how long a system takes to reject different guesses.

**Zip bomb.** A small compressed file that expands to a huge size and exhausts memory when unpacked. Defence: authenticate first, and cap the decompressed size (we do both).

**FastAPI dependency.** A function FastAPI runs before a route's handler. Attached to a router, it guards every route on it, and it can reject the request before the handler starts.

**401 vs 403 vs 503.** 401: "I don't know who you are" (missing or wrong credentials). 403: "I know who you are, and the answer is no." 503: "this service isn't available right now", which here means it's switched off on purpose.

## Interview questions
1. *How do you authenticate telemetry producers?* A bearer token in `Authorization`, which the OTel Collector sends through its standard `headers:` setting. It's checked in a router dependency before the body is read, compared in constant time, and answered with 401 + `WWW-Authenticate`. Unset in production means 503: fail closed.
2. *Why check before reading the body?* An unauthenticated sender shouldn't cost us memory or CPU. The body may be gzip, and a zip bomb is cheap to send and expensive to unpack.
3. *Why is `==` wrong for comparing tokens?* It returns at the first mismatch, so timing leaks a prefix. Use `hmac.compare_digest`.
4. *Why not Cloud Run IAM or mTLS?* IAM would lock the public demo on the same service. mTLS isn't passed through Cloud Run's front end, and it means certificate management for every producer.
