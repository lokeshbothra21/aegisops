# Day 14 · 25 Sep 2026 · Runs API, remediation, human approval (PR #27, E5.1, E5.2, E5.4 first cut)

## What we did
The agent stops being a command-line tool and becomes part of the service.
- **Runs (E5.2):** migration `0005` adds `runs`, `run_events`, `remediations`. `POST /api/v1/incidents/{id}/runs` starts an investigation in the API process (ADR-009) and returns 202; `GET /api/v1/runs/{id}` gives status, root cause, remediation, tokens, cost, tool calls, duration; `GET /api/v1/runs/{id}/events` is a **Server-Sent Events** stream that replays stored events after `?after=seq` and then follows live ones until the run ends. One active run per incident (409 otherwise).
- **Remediation (E5.1):** a deterministic `remediate` node maps the verified root cause to one of four actions with a risk tier (PROJECT.md §10.1), with one override that beats the table: a flag change on the service or a neighbour, scoring ≥ 0.5 and *before* the alert, is proposed as "toggle it back". Zero verified confidence or `no_incident` means report only.
- **Approval (E5.2):** an `approval` node calls LangGraph's `interrupt()`. The graph checkpoints and stops; the run and incident move to `awaiting_approval`. `POST /runs/{id}/approve` or `/reject` (admin token) records the decision and resumes the same thread with `Command(resume=...)`; approve moves the incident to `remediating`, reject back to `investigating`.
- **Policy (E5.4, first cut):** `config/policy.yaml` — autonomy level × risk × verified confidence decides whether a proposal may skip the human; allowlisted flags and services; **public mode never executes**. Execution itself is Week 6: the graph currently ends at a recorded decision.
- **Cost per run:** `config/models.yaml` gains list prices; the run row stores `cost_usd`.
- 141 tests, 94 %.

## Live result (real API, real model, captured S1)
First attempt: the model concluded at 0.2, the verifier cut it to 0.08, `remediate` proposed nothing, approval was skipped and the run ended. Correct behaviour for a weak answer, but no approval to test. Second attempt: `config_regression` claimed 0.66 → **verified 0.22**, `remediate` proposed `toggle_flag paymentFailure → off` (low risk), policy said "autonomy level 1 waits for a human", the SSE stream delivered eight events and `approval_requested`, the incident moved to `awaiting_approval`, `approve` by `shreyas` resumed the graph, the run ended `succeeded`, the incident moved to `remediating`. 13 tool calls, 11,306 / 4,743 tokens, **$0.0038** at list prices, four provider fallbacks.

Two things to be honest about: the same captured data gave 0.08 and 0.22 on consecutive runs (model non-determinism plus which provider answered); and a verified confidence of 0.22 still produced a proposal a human was asked to approve. That is by design at autonomy level 1, and exactly why level 2 requires ≥ 0.85 to act alone.

## Bugs the live run and tests caught
1. **Thread id collision.** `inc-{id}-{unix second}` collided when two runs started in the same second → unique-violation. Now a random suffix.
2. **Integrity errors reported as outages.** The Day 8 handler mapped every `DBAPIError` to 503 "Database unavailable", including a constraint violation. `IntegrityError` now maps to **409 Conflict**.
3. **Root cause lost at the end.** The run's `root_cause` was read from the *last* graph step, which is `approval`. State is now merged across steps.
4. **Duration measured the wrong leg.** After a resume, `duration_ms` covered only the resume (29 ms). It is now wall time from `started_at`, approval wait included.
5. **Coverage lied.** Route handlers showed 0 % while their tests passed: SQLAlchemy's async layer runs code inside greenlets that coverage did not trace. `concurrency = ["greenlet", "thread"]` fixed the measurement (runs route 0 → 96 %).

## Terms introduced

**Server-Sent Events (SSE).** A one-way HTTP stream (`text/event-stream`) of `id:`, `event:`, `data:` lines. Simpler than WebSockets for server→client progress; browsers reconnect automatically and send `Last-Event-ID`. Ours: event name = node, id = sequence number.

**Replay-then-follow.** Serve stored events after the client's last seen id, then attach to the live queue and skip anything already sent. A reconnect never loses or duplicates an event. The durable log (`run_events`) is the source of truth; the in-memory queue is only a fast path.

**202 Accepted.** "Started, not finished." The run continues in a background task; the client polls or streams.

**Human-in-the-loop interrupt.** LangGraph's `interrupt(value)` saves the checkpoint and stops the graph; the value reaches the caller as `__interrupt__`. `Command(resume=answer)` on the same thread continues from exactly that node, with `answer` as `interrupt()`'s return value. The pause can last seconds or days; the process can restart in between.

**Remediation proposal vs execution.** Proposing is deterministic and cheap; executing is privileged. Keeping them in different nodes, with approval between, is what makes "no action without approval" a property of the graph (ADR-007).

**Autonomy level.** Per-incident setting: 1 always asks, 2 may act alone on low risk ≥ 0.85, 3 on medium ≥ 0.90. Public mode caps at 1 and disables execution.

**Flag-revert override.** When correlation says a flag flipped on the affected service just before the alert, "flip it back" beats the category table: it is the smallest, most reversible fix.

**Cost per run.** tokens × list price per million, per model used. Free tiers bill $0; list prices make runs and variants comparable in the benchmark.

**IntegrityError vs OperationalError.** A constraint violation is the caller's conflict (409); a refused connection is an outage (503). Mapping both to 503 hid a real bug.

**Coverage and greenlets.** SQLAlchemy async runs ORM code inside greenlets; coverage must be told (`concurrency`) or it silently under-reports.

## Interview questions
1. *How does a human approve an agent's action without the agent holding a process open?* Checkpoint at an interrupt; the approval request is durable; `Command(resume=...)` on the same thread continues later, even after a restart.
2. *Why SSE and not WebSockets?* One-way progress, plain HTTP, automatic reconnect with `Last-Event-ID`; replay-then-follow from a durable log makes reconnects lossless.
3. *How do you stop a low-confidence answer from acting?* Verified (not claimed) confidence feeds the policy; level 1 always asks; level 2 needs ≥ 0.85; public mode never executes.
4. *Tell me about a bug that only showed up live.* Root cause lost at run end because it was read from the last step; and duration measuring only the resume leg. Both invisible in unit tests that never paused.
5. *Why did your coverage report say 0 % for code you knew ran?* SQLAlchemy async greenlets; configured coverage concurrency.
