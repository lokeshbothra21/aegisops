# Day 9 · 21 Sep 2026 · Container change watcher and uptime ping (PR #17, E2.2 observation half, E8.4)

## What we did
- **Container watcher (E2.2):** every 10 s, list the demo's Compose containers through the Docker Engine API over its Unix socket, inspect each, build a per-service snapshot (image, image id, start time, restart count, replica count), and diff against the previous one. Image changed → `deploy`; start time or restart count changed → `restart`; container count changed → `scale`. Recorded as `change_events` with actor `docker`. The execution half (the agent *performing* restarts/rollbacks) is Week 6.
- **Uptime ping (E8.4 complete):** a scheduled GitHub Actions workflow hits `/livez` every 6 hours with three attempts (cold starts allowed) and turns red on failure; `/readyz` is reported until Supabase exists, then it becomes the keep-alive.
- 68 tests, 97 %.

## Live result
Baseline saw 25 Compose services. `docker restart payment` at 13:50:39Z → `restart` change event at 13:50:54Z (15 s, one poll interval plus the container's restart time), with the old and new `StartedAt`. Then **a gap:** `docker kill currency` produced *no* event within 30 s. Docker treats a kill through the API as a user stop, so the restart policy did not bring the container back, and nothing the watcher compared (image, start time, restart count) had changed. Fix: `replicas` now counts **running** containers, so a dead container is a `scale 1 → 0` event and its return is `scale 0 → 1` plus a `restart`. Retest: `docker kill currency` at 13:54:58Z → `scale 1 → 0` at 13:55:03Z (5 s); `docker start currency` at 13:55:23Z → `scale 0 → 1` and `restart` events in the same second.

## Terms introduced

**Docker Engine API.** Docker's daemon is an HTTP server on a Unix socket (`/var/run/docker.sock`, OrbStack: `~/.orbstack/run/docker.sock`). `docker ps` is `GET /containers/json`; `docker inspect` is `GET /containers/{id}/json`. Talking to it directly with an HTTP client avoids the Docker SDK dependency and makes the watcher trivially testable with a fake transport.

**Unix domain socket.** A file-system endpoint for local inter-process communication; no TCP port, permissions via file mode. httpx supports it with `AsyncHTTPTransport(uds=path)`; the URL host is then ignored.

**Compose labels.** Compose stamps every container with `com.docker.compose.project` and `com.docker.compose.service`. Filtering by project label (`?filters={"label":[...]}`) scopes the watcher to the demo and grouping by service label turns containers into services.

**Image tag vs image id.** A tag (`demo:3.0.0-payment`) is a mutable name; the id (`sha256:…`) is the content hash. Comparing both catches a re-pull of the same tag with different content, which is exactly the "silent deploy" a root-cause analysis wants to know about.

**RestartCount and StartedAt.** Docker's own counters: `RestartCount` increments when the restart policy restarts a crashed container; `StartedAt` changes on any start, including `docker restart`. Together they separate "someone restarted it" from "it crashed and came back".

**Replica / scale event.** Number of *running* containers for one Compose service. The demo pins `container_name` so it cannot scale out, but a container that dies and stays dead is a scale-down, which is the most important "scale" event of all.

**Snapshot-and-diff (again).** Same pattern as the flag watcher: baseline first, then only differences become events. It is stateless across restarts and needs no hooks into the tools that make the change.

**Mock transport.** `httpx.MockTransport(handler)` routes requests to a Python function; tests script the Docker API's responses and mutate them between ticks. No Docker needed in CI.

**Read-only observation vs execution.** The watcher only reads. Writing (restart, rollback) is a privileged action that lives behind approval (ADR-005, ADR-007) and is built in Week 6.

**Scheduled workflow (`schedule:` cron).** GitHub runs the workflow on a cron; a red run emails the owner. Cheapest possible uptime monitor for a ₹0 project; also the mechanism for the Supabase keep-alive.

**Cold start.** A scale-to-zero service takes a few seconds to start on the first request; the ping retries three times, ten seconds apart, so a cold start is not a false alarm.

## Interview questions
0. *What did the live test catch that unit tests did not?* A killed container that Docker does not restart changed none of the fields we compared. Lesson: enumerate the failure states of the thing you watch (running, exited, restarting), not just its attributes.
1. *How do you detect a deploy without hooking the CI system?* Watch the running containers: image tag or image id changed for a service since the last snapshot.
2. *How do you tell a crash-restart from a manual restart?* `RestartCount` increases only when the restart policy acts; `StartedAt` changes for both.
3. *Why talk to the Docker socket directly instead of the SDK?* Two endpoints, one HTTP client we already have, and a fake transport makes it unit-testable.
4. *Why is only the observation half here and not the actions?* Actions are privileged and gated by approval; separating read from write is the security boundary.
5. *How do you monitor a scale-to-zero service for free?* A cron workflow that pings liveness with retries; failures surface as red runs and emails.
