# ADR-009: The agent runs inside the SSE request on Cloud Run, not in a separate worker

**Status:** Accepted, 13 Sep 2026

## Context
A run takes up to 180 s and streams progress to the browser. The textbook design is API → queue → worker pool. Free tiers do not include a queue or a second always-on service.

## Decision
`POST /incidents/{id}/runs` starts the graph inside the request that serves `GET /runs/{id}/events` as Server-Sent Events. Cloud Run's request timeout is set to 900 s and `--session-affinity` keeps the stream on one instance. Checkpoints (ADR-006) cover crashes: a new request can resume the thread.

## Alternatives considered
- **Cloud Tasks / Pub/Sub + worker service.** Two services, both idle most of the time, and Pub/Sub push has its own timeouts. Not free at min-instances 1.
- **Background task in the same process.** Cloud Run may throttle CPU when no request is open; keeping the SSE request open is what keeps the CPU allocated.

## Consequences
- Concurrency is capped at 10 per instance so a run never starves.
- If the instance dies mid-run, the UI reconnects and the graph resumes from the last checkpoint. This is the interview answer for "what happens if the Cloud Run instance dies".
- Revisit if the project ever needs > 3 concurrent runs.
