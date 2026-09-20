# Day 8 · 20 Sep 2026 · The alert evaluator (PR #16, E2.3)

## What we did
Deterministic detection (ADR-008). Rules are rows in `alert_rules`, seeded by name from `config/alerts.yaml` at startup. Every 30 s the evaluator computes each rule's metric per service over its window, tracks consecutive breaching ticks, and opens an incident through the state machine when the streak reaches `for_windows`. Un-investigated incidents auto-resolve after 3 healthy ticks. Also: database-down errors are now a 503 problem response with `Retry-After`, and startup no longer depends on the database.

## Live result
Third drill, default 5 VUs: `paymentFailure=100%` at 16:27:46Z → change event 16:27:48 → first payment ERROR span 16:27:54 (+8 s) → incidents at 16:28:43 for payment and checkout (`high-error-rate`, rate 0.57) and frontend (`error-burst`, 8 errors/60 s). **Fault → incident: 57 s. First error → incident: 49 s.** Zero incidents during the 3-minute warm-up (no false positives). Flag off at 16:29:46 → payment incident auto-resolved 16:32:34 (window drained, then 3 healthy ticks). The two earlier drills, before the fixes, measured 116 s and 118 s.

## What the first two live drills taught us (and what changed)
1. **A leaked test rule fired instead of the real one.** The seed test's cleanup had silently not applied after a reformat; a rule with threshold 0.2 and `for_windows: 1` sat in the table. Cleanup fixed and verified, and the evaluator now logs-and-skips a rule whose metric has no reader instead of crashing the tick.
2. **Fault → incident took 116 s, then 118 s.** Where the time went: the demo's load generator (5 virtual users) reaches payment only every ~10–15 s, so the first error span appeared 13–30 s after the flag; the span-metrics connector flushed every **60 s** (default); a **brand-new counter series contributed nothing** until its second sample because the window delta was "newest − oldest inside the window"; and `MIN_CALLS = 10` needs ~2 minutes of payment traffic at 5 VUs.
3. **Changes:** window delta is now "newest − last sample *before* the window" (0 for a new series, reset-aware); our collector layer sets `span_metrics.metrics_flush_interval: 10s`; ticks every 15 s; `MIN_CALLS = 5`; and a new **`error_count`** reader over the spans table (exact, because tail sampling keeps every error trace) drives an **`error-burst`** rule: ≥ 5 error server spans in 60 s for 2 evaluations. Rate and burst rules attach to the same incident (one active per service).
4. **The honest floor.** Detection latency is bounded by traffic × sample requirement. With the fixes, 5 VUs gave 57 s from the fault, just inside the Week 2 criterion, but a quieter path would not make it: at ~2 calls/min no rule can be statistically confident within 60 s of the fault, only within ~60 s of the first symptom. The scenario runner (E7.2) will set `loadGeneratorVUs` per episode so the benchmark's detection numbers are comparable.
5. **Cascades are expected.** checkout's error rate also spikes because payment fails; both services get an incident. Deduplicating a cascade into one incident is the agent's job (service edges), not the detector's.

## How a tick works
1. Load enabled rules. For each, call the reader for its metric over `[now − window_s, now)` → `{service: value}`.
2. `breach = value comparator threshold`. Breaching increments `streak[(rule, service)]`; healthy resets it and increments a healthy streak.
3. Streak ≥ `for_windows` and no active incident for that service → `open_incident()` with a summary such as `high-error-rate: error_rate for payment = 0.42 > 0.05 over 120s for 2 evaluations`.
4. Healthy ≥ `recovery_windows` and the incident is still `open` (nobody investigated) and belongs to this rule → `resolved`, summary appended. Investigated incidents belong to the agent.

## Why the readers look the way they do
**Our spans are tail-sampled** (all error traces, 15 % of the rest), so `errors / total` over the spans table would be wildly inflated. Rates therefore come from the collector's span-metrics connector, which counts on *unsampled* traffic and exports **cumulative counters** every ~10 s. A window's count for one series is `max(value) − min(value)` inside the window; series are `(service, span.name, status.code)` for SERVER spans; sum errors and totals per service. Services with fewer than 10 calls in the window get no reading, so a single failed request on a quiet service cannot fire a rate rule.

**p95 from a cumulative histogram.** Take the earliest and latest point per series in the window, subtract bucket counts elementwise (the window's distribution), sum across series per service, then interpolate linearly inside the bucket where the cumulative count crosses 95 %. The baseline is the same computation over the previous hour; the rule fires on the *ratio*, so a slow-by-design endpoint does not alert and a 2× regression on a fast one does.

**Memory** is the mean of `container.memory.percent` per container over the window (demo container names equal service names). **Kafka lag** is the max of `kafka.consumer_group.lag` per consumer group (full demo mode only).

## Terms introduced

**Alerting rule, threshold, window, `for` duration.** The Prometheus vocabulary: an expression over a window that must hold for a duration before firing. Ours: `metric comparator threshold` over `window_s`, for `for_windows` consecutive 30 s evaluations. The `for` clause is what turns a spike into a non-event.

**Hysteresis / recovery windows.** Requiring several healthy evaluations before resolving, so a flapping metric does not open and close incidents every tick.

**Flapping.** An alert that oscillates between firing and resolved. Defences: `for_windows`, recovery windows, and one active incident per service.

**Cumulative vs delta counters.** A cumulative counter only goes up (until a restart); the value in a window is the difference between its ends. Deltas would be simpler but the demo's Prometheus exporter needs cumulative, and we share the connector.

**Counter reset.** After a process restart a cumulative counter starts at 0; `max − min` inside a window that straddles a reset undercounts (never overcounts). Acceptable; noted.

**Rate vs ratio rules.** `error_rate` is a fraction of the window's own traffic; `p95_ratio` compares now with a baseline. Ratios need a baseline period and a minimum sample size.

**Baseline.** "Normal" for comparison. Ours is the previous hour of the same metric. Sophisticated systems use same-hour-last-week or seasonal models; a one-hour trailing baseline is enough for a demo that runs for minutes.

**Minimum sample size (`MIN_CALLS`).** Below 10 calls a rate is noise; the reader returns nothing rather than a misleading 100 %.

**Percentile from histogram buckets.** With bucket boundaries `b0 < b1 < …` and counts, find the bucket where the cumulative count reaches q·total, then interpolate between its bounds. Exact per-request latencies are never stored; this is how every metrics system computes p95.

**Streak state in memory.** Streaks live in the evaluator object, not the database; a restart resets them, delaying a fire by at most `for_windows` ticks. Simple beats durable here.

**Idempotent seeding.** Insert rules missing by name; never update existing rows. Operators tune thresholds in the database; deleting a row restores the default on the next start.

**Fail-open startup vs fail-closed auth.** Startup tolerates a missing database (warn, continue) because a container that cannot start cannot report `/livez`; admin auth denies when unconfigured. Different failure modes, deliberately.

**`Retry-After`.** HTTP header telling clients when to try again; sent with the 503 for database outages.

**Monkeypatching a registry in tests.** Injecting a fake reader into `READERS` by key to test the evaluator's streak logic without telemetry; the key is unique per test and cleaned up, and the evaluator skips unknown metrics so leftover rules cannot crash a tick.

**Test pollution through shared tables.** Rules are global rows; a test that leaves one behind changes other tests' behaviour. Fix: unique names plus cleanup in `finally`, and defensive code (skip unknown metric).

## Decisions
- Rates from span-metrics counters, not from sampled spans (see above).
- `for_windows` counts consecutive evaluations, not disjoint windows; documented in `config/alerts.yaml`.
- Auto-resolve only `open` incidents of the same rule; anything the agent touched is left alone.
- Alerts live in the API for now (`aegisops_api/alerts`), not `packages/alerts`, because they need the ORM models; splitting later means moving models into a shared package first.

## Interview questions
1. *Why not compute error rate from your stored spans?* They are tail-sampled: every error trace kept, 15 % of the rest; the ratio is meaningless. Use counters computed before sampling.
2. *How do you get p95 from a Prometheus-style histogram?* Bucket deltas over the window, find the bucket where cumulative count crosses 95 %, interpolate.
3. *How do you avoid alert flapping?* `for` windows before firing, recovery windows before resolving, one active incident per service.
4. *Why compare p95 to a baseline instead of a fixed threshold?* Endpoints differ by 100× in normal latency; a ratio catches regressions everywhere with one rule.
5. *What happens if someone adds a rule with a metric typo?* The evaluator logs and skips it; the tick continues. Seeding validates metrics up front.
6. *Where does the detection budget go?* Traffic pacing to the first symptom (demo: 13–30 s), ~6 s tail-sampling wait, 10 s metrics flush, ≤ 15 s to the next tick, × `for_windows` evaluations, plus the minimum sample size for rate rules.
7. *Why both a rate rule and a count rule?* Rates need enough total calls (slow on quiet services, robust on busy ones); counts are exact and fast because every error trace is kept, but say nothing about proportion. Together they cover both regimes.
