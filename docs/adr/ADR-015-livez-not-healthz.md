# ADR-015: Liveness probe is `/livez` because Google Frontend reserves `/healthz`

**Status:** Accepted, 18 Sep 2026 (E8.4, E11.3)

## Context
The plan specified `/healthz` and `/readyz`, following the Kubernetes convention. The second Cloud Run deploy created the service and the container started cleanly, yet the probe step received Google's own HTML 404 page for `/healthz` while `/readyz` and every other path reached the application.

## Decision
Rename the liveness endpoint to `/livez` everywhere: route, tests, Dockerfile `HEALTHCHECK`, CI container probe, deploy probe, runbook and plan. `/readyz` is unchanged.

## Alternatives considered
- **Keep `/healthz` and probe a different path only in the deploy workflow.** Leaves a route that works locally and silently fails in production; a trap for the next reader.
- **Custom domain to bypass the run.app front end.** Cost and scope for a naming problem.

## Consequences
- `/livez` is also a Kubernetes-recognised name, so the convention argument still holds.
- Recorded here because it is the kind of production surprise an interviewer asks about: "what broke on your first deploy and how did you find it?" Answer: compared response `Content-Type` and `Server` headers across paths to prove the 404 came from Google Frontend, not from the app.
