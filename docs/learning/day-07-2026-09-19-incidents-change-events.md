# Day 7 · 19 Sep 2026 (second PR) · Incident tables, lifecycle, flag change watcher (PR #15, E2.4, E2.1)

## What we did
- **Schema (E2.4):** migration `0003` adds `change_events`, `alert_rules`, `incidents`. Enums stored as VARCHAR + CHECK.
- **Incident lifecycle:** a state machine in code (`incidents/lifecycle.py`): open → investigating → awaiting_approval → remediating → resolved | failed. Illegal moves raise. Terminal states set `closed_at`. `active_incident()` prevents duplicate incidents per service.
- **Flag watcher (E2.1):** polls `demo.flagd.json` mtime every 2 s; on change, diffs each flag's `defaultVariant` and writes a `change_events(type=flag)` row with before/after and the affected service, mapped through `config/targets/otel-demo.yaml`.
- **Read-only incident API:** `GET /api/v1/incidents?status=&limit=&cursor=` and `GET /api/v1/incidents/{id}`.
- **A real bug found by the live check** and fixed: the job runner never committed (see below). 53 tests, 97 %.

## Live result
`make flag name=paymentFailure variant=100%` at 13:38:03Z → `change_events` row at 13:38:04.7Z (service=payment, off → 100%, value 1), then the reverse 4 s later. About 1.7 s of latency, bounded by the 2 s poll.

## The bug: an early `return` inside `async for` skips the commit
`session_scope` was an *async generator*: `yield session` then `await session.commit()`. The runner did `async for session in session_scope(...): result = await fn(session); return result`. Returning from inside the loop closes the generator at the `yield`, so the code after it (the commit) never runs. The tick logged `job.ok result=16` while the transaction was silently discarded. FastAPI's `Depends` drives generators to completion, which is why the admin endpoints persisted and the runner did not.

Fix: make `session_scope` an `@asynccontextmanager` and use `async with`, and log `job.ok` *after* the block so the log line means "persisted". A regression test writes a row through `run_once` and reads it back from a fresh session.

Lesson: **a green unit test that checks a return value proves nothing about persistence.** Verify side effects from a second connection, and verify live.

## Terms introduced

**Change event.** A record that something in the target changed: flag flip, deploy, restart, scale, commit. `before`/`after` JSONB, `actor`, `service`. The agent's `correlate_changes` node asks "what changed in the 30 minutes before the alert?" Deploy/restart/scale events come in E2.2.

**Incident.** The unit of work: one service, one alert rule, a status, an autonomy level, timestamps. Runs (agent investigations) attach to it later.

**Alert rule.** `metric comparator threshold` over `window_s` seconds, must hold for `for_windows` consecutive windows before firing. Rows are data, so rules can change without a deploy. Seeded in E2.3.

**State machine (explicit).** All legal transitions in one table (`ALLOWED`); the only way to change status is `transition()`. Interviewers like this because it makes invalid states unrepresentable and the graph testable.

**Terminal state.** A state with no exits (`resolved`, `failed`); reaching one sets `closed_at`.

**Enum storage: native vs VARCHAR + CHECK.** Postgres native enums are types; adding a value is a type migration and removing is painful. `native_enum=False` stores a string with a CHECK constraint; adding a value is an ordinary constraint change. `values_callable` stores the value (`">="`) instead of the member name (`gte`).

**Foreign key.** `incidents.alert_rule_id → alert_rules.id`: the DB refuses an incident pointing at a rule that does not exist.

**Keyset (cursor) pagination.** `WHERE id < cursor ORDER BY id DESC LIMIT n+1`. Stable under inserts and O(log n), unlike OFFSET which scans and drifts. Fetching `limit+1` rows tells you whether there is a next page without a COUNT.

**mtime polling.** Checking a file's modification time on an interval; cheap, dependency-free, and good enough for a file that changes a few times an hour. Inotify/`watchfiles` would be event-driven but adds a dependency.

**Baseline snapshot.** The first read records nothing; only differences from the last snapshot become events. Changes made while the process is down are lost (runbook entry).

**Target config (`config/targets/`).** Anything that names a specific system (flag names, services) lives in YAML, not code, so a second target (the ERP, after code freeze) is a new file, not a fork.

**Actor.** Who made the change. `flagd-file` for edits seen on disk; later `agent`, `admin`, `policy:auto` for actions the system takes.

**Async context manager vs async generator.** `@asynccontextmanager` turns a generator into something used with `async with`, which guarantees the code after `yield` runs on exit (normal or exceptional). A bare generator consumed with `async for` offers no such guarantee if the consumer leaves early.

**Regression test.** A test written to pin a fixed bug so it cannot come back.

**`from_attributes`.** Pydantic setting that lets a response model be built directly from an ORM object.

## Interview questions
1. *How do you prevent two incidents for the same outage?* `active_incident(service)` returns the non-terminal one; the evaluator attaches to it instead of opening another.
2. *Why cursor pagination instead of offset?* Stable under concurrent inserts, index-friendly, no COUNT needed.
3. *Why store enums as strings with a CHECK?* Cheap to evolve; native enum types make add/remove a type migration.
4. *Tell me about a bug your tests missed.* The runner-commit bug: tests asserted return values, not persistence; the live check caught it; fixed with a context manager and a read-back test.
5. *How do you record "what changed" without instrumenting every deploy tool?* Watch the artefacts that change (flag file now; container labels next) and diff snapshots.
