# ADR-021: The web UI's browser calls the API directly (CORS), not through a Vercel proxy

**Status:** Accepted, 10 Oct 2026

## Context
The web UI (E10) is hosted on Vercel; the API runs on Cloud Run. The two have different origins, so a browser will only let the UI read API responses if the API allows it (CORS). The usual way to avoid CORS is to proxy: a Next.js rewrite, or server-side code on Vercel, forwards `/api/*` to Cloud Run so the browser only ever talks to one origin.

Three things the UI needs make the proxy the wrong choice here:
- **Visitor identity.** Public admission (ADR-018) limits each visitor by the right-most `X-Forwarded-For` entry, which Google's front end appends with the address it received the request from. Through a proxy, that address would be Vercel's egress IP for every visitor: one shared "visitor", so the first person's run would block everyone else's.
- **Long-lived streams.** A run's event stream (SSE) stays open for up to three minutes. Vercel functions have execution limits, and proxies may buffer responses; a stream must reach the browser event by event.
- **No secrets to hide.** The public API needs no credentials for reads or for starting a visitor run. The operator token for approvals is typed by the operator and never stored on Vercel (§14.5: public API URL only, no secrets in the browser build).

## Decision
- The browser calls the API at `NEXT_PUBLIC_API_URL` directly: fetches, run starts, and `EventSource` for the stream.
- The API adds Starlette's `CORSMiddleware`:
  - origins allowed by **one regex, full match**, `AEGIS_CORS_ORIGIN_REGEX` (default: `http://localhost` or `127.0.0.1` on any port; production: the UI's Vercel domains);
  - methods `GET` and `POST`; request headers `Content-Type`, `X-Admin-Token` and `Last-Event-ID`; `Retry-After` exposed so the UI can show when to try again after a 429;
  - **no credentials** (no cookies anywhere in this system), preflight cached 10 minutes.
- Two API additions the UI needs: `GET /incidents/{id}/runs` (newest first), and the SSE endpoint honours **`Last-Event-ID`**, so an `EventSource` reconnect resumes instead of replaying the whole run.

## Alternatives considered
- **Next.js rewrites to Cloud Run.** One origin, no CORS. Rejected for the three reasons above, mainly that it collapses every visitor into one for admission control.
- **Server-rendered pages fetching from Cloud Run.** Also hides the API, but each page render would wait on a scale-from-zero API (several seconds cold), and run starts would still have to come from the browser for visitor identity.
- **A list of allowed origins instead of a regex.** Simpler to read, but Vercel gives every preview deployment its own URL; a regex covers them. Full-match semantics (Starlette uses `fullmatch`) and a test with `https://aegisops.vercel.app.evil.example` guard against the classic prefix-match mistake.

## Consequences
- The API URL is public (it already was).
- CORS is not a security boundary for this API: reads are public anyway, and the protected routes require the admin token whatever the origin. It stops other websites from using a visitor's browser to start runs that count against that visitor.
- If a load balancer or CDN is ever put in front of Cloud Run, the `X-Forwarded-For` position changes (RUNBOOK covers this), and the same reasoning applies.
