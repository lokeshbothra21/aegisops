# Day 10 · 22 Sep 2026 · The agent's tools: nine read-only telemetry tools and the MCP server (PR #20, E9.1, W3)

## What we did
Week 3 begins with the foundation the agent stands on. The `tools` package (`packages/tools`) now holds:
- **Nine read tools** (PROJECT.md §9.1) as async functions over an `AsyncSession` and a `ToolContext`: error rate, latency percentiles, top error logs, error traces, compare windows, service dependencies, recent changes, container metrics, similar incidents (an honest stub until E6).
- **Untrusted-content wrapping (E9.1):** every result is JSON inside `<telemetry untrusted="true">…</telemetry>`, angle brackets escaped so the closing tag cannot be forged from inside the data, capped at 4 KB by trimming lists and marking `truncated: true`.
- **The `aegis-telemetry` MCP server** (ADR-007): the same nine functions exposed over stdio with input schemas derived from their Python signatures; mode (live vs replay) comes from the environment, never from the caller.
- Raw SQL only: the package does not import the API's ORM, which resolves the circular-dependency question from Day 6.
- 85 tests, 96 %.

## Live result
Live, over the demo with `paymentFailure=100%`: all nine tools answered through the MCP server over stdio, every payload inside the envelope and under the cap (largest 2.7 KB: a focused 22-span error trace). frontend p50/p95/p99 = 3.7 / 209.6 / 489.7 ms over 2,041 samples (plausible after the unit fix; 180 s before it). `compare_windows(payment)`: error rate 0.0 → 0.83 across the fault, p95 46 → 7 ms (fast failures). `get_recent_changes` returned the flag flip with before/after and actor. `get_service_dependencies(checkout)` listed six callees with checkout→payment at 3 errors / 5 calls. The same calls with `AEGIS_FROZEN_NOW` set to the drill's end reproduced the numbers: replay-style querying works on live rows.

## What the first live pass caught (and what changed)
1. **Latency was 1000× too high.** The span-metrics duration histogram is emitted in **milliseconds** by most demo SDKs and in **seconds** by a few (2452 vs 218 rows in one hour), and the code assumed seconds. Both the tools and the alert reader now convert each series' bounds to ms using the row's `unit` column, merge only series with identical normalised bounds, and report the largest group (`bucket_layouts` flags when layouts differ). The alert rule was unaffected (a *ratio*), but the numbers the agent would have read were wrong.
2. **Error traces were the wrong 60 spans.** "First 60 by start time" of a 100-span checkout trace never reached the payment error. `focus_trace` now walks the tree depth-first from the roots, keeps every ERROR span with its ancestors and children, then fills to 30 spans in tree order. Output dropped from 6.4 KB to under the cap.
3. **The 4 KB cap did not trim nested lists.** `shrink` now finds the longest list up to three levels deep (e.g. `traces[0].spans`) and trims it by a quarter until the payload fits.
4. **Two tests were polluting the local database.** The runner regression test and the flag-watcher test wrote change events with no scenario id (they looked like real flag flips in `get_recent_changes`); both now delete their rows. Worse, the retention test used a *future* `now` (2030) with a 24 h window, which deleted every live telemetry row on the laptop on every `make check`. It now uses a *past* `now` (2020) so the cutoff can never reach real data. CI was never affected (fresh DB), which is exactly why nobody noticed.
5. **Envoy access logs are not error logs.** `get_top_error_logs` filters `severity_num >= 17`; frontend-proxy lines have severity 0, so the tool returns nothing for it. Correct, and worth knowing when the agent asks.

## Design rules the tools follow
1. **Small.** ≤ 4 KB per response; numbers pre-aggregated in SQL; span trees carry service/name/kind/status/duration and the exception message, never raw attributes. A model reasons better over 30 numbers than 3,000 rows, and the token budget (60k per run) is finite.
2. **Honest about sampling.** Rates come from span-metrics counters (unsampled); error *lists* come from spans (every error trace is kept). The two are labelled so the model cannot confuse a sampled count with a rate.
3. **Mode-blind.** Every query filters `scenario_id IS NOT DISTINCT FROM :scenario_id` and uses `ctx.now`; live and replay are the same code path (ADR-004).
4. **Bounded inputs.** Windows clamp to 4 h, limits to 5–20, depth to 2. The model can ask for "everything" and still get something small.
5. **Data, not instructions.** The envelope plus the server's instructions text tell the model that content inside is data. Defence in depth with structured outputs and graph topology (Day 0).

## Terms introduced

**MCP server / tool / input schema.** An MCP server advertises tools with JSON-Schema inputs; a client (the agent, an IDE, Claude) lists and calls them. Our schema is derived from each Python function's signature minus its two internal parameters, so the code and the contract cannot drift.

**stdio transport.** The client launches the server as a subprocess and speaks JSON-RPC over stdin/stdout. Simplest transport; no ports, no auth surface. HTTP transports exist for remote servers.

**Instructions (MCP).** A server-level text the client passes to the model. Ours states the untrusted rule.

**Untrusted-content envelope.** A tagged wrapper around external data. Attackers put instructions in data (log lines, span names); the envelope plus escaping keeps a `</telemetry>` inside a log from closing the envelope early.

**Output escaping.** `<` and `>` become `<` / `>` in the JSON. Same idea as HTML escaping: the data survives, the syntax cannot be forged.

**Size cap with graceful truncation.** Dropping list tails until the payload fits, then flagging it, instead of failing or blowing the context.

**Log signature.** Numbers, hex ids and whitespace collapsed to `#` so "charge failed for order 1001" and "...1002" group as one signature with a count. This is what turns 500 log lines into 5 rows.

**Span tree flattening.** A trace as a list of (depth, service, name, kind, status, ms); depth computed by walking `parent_span_id`. Readable by a model, no nested JSON.

**Baseline comparison.** `compare_windows` and `get_latency_percentiles` compute the same statistic over "now" and "before" and return the delta, so the model sees change, not just level.

**Frozen now.** In replay, `ToolContext.frozen_now` is the capture window's end; every "last N minutes" is relative to it. This is how a scenario captured on a Tuesday replays identically on a Friday.

**asyncpg parameter typing.** Postgres must know each parameter's type at prepare time; `jsonb_build_object(..., :p)` or `:p IS NULL` is ambiguous → `CAST(:p AS text)`. A recurring gotcha with raw SQL through asyncpg.

**Hour-labelled buckets.** `service_edges.window_start` is the *start* of the hour, so a query for "up to now" must extend its end by one hour or it drops the current hour when `now` is exactly on the hour. Found by a test at 12:00:00.

**Unit normalisation.** Never trust a metric's unit by name; read the `unit` column and convert. Same metric, two SDKs, two units.

**Depth-first tree focus.** Choose which spans to show by relevance (errors, their ancestors, their children), not by arrival order.

**Destructive tests need a safe direction.** A test that deletes "everything older than X" must pick X so that real data can never qualify; a past cutoff is safe, a future one is a wipe.

**Test module name collision.** Two `tests/` packages with `__init__.py` both import as `tests.*`; the second one fails to import. The tools tests are plain modules (no `__init__.py`) with unique file names.

**Per-file lint ignores.** Long SQL/JSON literals in test fixtures are exempt from the line-length rule; production code is not.

## Interview questions
1. *Why do tools return ≤ 4 KB?* Token budget and reasoning quality; SQL aggregates are cheap, model context is not.
2. *How do you stop a log line from hijacking the agent?* Untrusted envelope with escaped brackets, server instructions, structured outputs, and no edge into `execute` except from `approval`.
3. *How does the same tool work live and in replay?* `scenario_id` filter plus a frozen `now` in the context; the tool code is identical.
4. *Why MCP for these and not for actions?* Reads are safe to expose to any client; actions are privileged and gated by graph topology (ADR-007).
5. *Where do error rates come from if you sample traces?* From span-metrics counters computed before sampling; error *examples* come from the kept error traces.
6. *How do you keep the tool contract and the code in sync?* The MCP input schema is generated from the function signature at registration.
7. *Tell me about a unit bug.* Duration histograms arrived in ms and s from different SDKs; the code assumed seconds and reported p95 of 180 s for a 180 ms endpoint. Fixed by reading the unit per row and normalising bounds before merging; found by looking at a live number and asking whether it was plausible.
