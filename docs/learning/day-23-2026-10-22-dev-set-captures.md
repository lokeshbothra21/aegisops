# Day 23 · 22 Oct 2026 · Capturing the dev set, and everything it exposed (PR #62, ADR-025)

## Why
Milestone M1 (31 Oct) is "the public URL replays S1–S8". Until today only S1 was captured. A capture is a recording: we break the demo shop on purpose, let AegisOps detect it, fix it, and save that window of telemetry, so the agent can be replayed on it any time without a live shop.

## The day in order
1. **Production was down.** Since about 21 Oct 12:00 UTC, the Google Cloud billing account had been closed (`open: False`), so Secret Manager refused requests (`BILLING_DISABLED`) and the API couldn't start. After the account was reopened, Cloud Run still answered *429 "no available instance"* for about 20 minutes. My manual redeploy failed its health probe and never received traffic: the deploy gate did its job. Healthy from 08:02.
2. **The first capture round** (S2–S6, S8): two successes, two misses, two that never started.
3. **Investigating each failure** found four bugs in our own harness and one gap in detection.
4. **Fixed them, recaptured S3, S4, S6 and S8, and replayed the agent on each.**

## What the failures turned out to be

### S3: the fault never actually happened
`productCatalogFailure` has a **targeting rule** in flagd: "if the product is OLJCESPC7Z then *off*, else *off*". flagd checks targeting **before** the default, and our runner only changed the default. So the fault never fired: zero error spans in the whole capture. The "detection miss" was correct behaviour, because nothing was broken.

**Fix:** when a flag has a rule shaped `if condition then A else B`, setting a variant also sets A. Setting "off" restores the original rule. This is what the demo's own UI does. It matters most for the agent's **revert** action, which would otherwise have reported the fault as fixed while it was still on.

### S4 and S8: names that don't exist
Our catalogue asked for variant `on`. The demo calls those variants `100%` (`cartFailure`) and `1000x` (`emailMemoryLeak`). The runner refused, correctly, but only after a 2-minute warm-up.

**Fixes:** the right names; a **pre-flight check** that validates variants before the warm-up; and a test against a copy of the pinned demo's own flag file, so this can't reach a live run again.

### S6: we had no CPU rule, and our CPU number was wrong
- We had alert rules for errors, latency, memory and Kafka, but **none for CPU**.
- The CPU metric we stored, `container.cpu.utilization`, behaves on Docker Desktop and OrbStack like a **lifetime average**: it only ever goes up. ad's value climbed in a straight line during the fault and stayed high after it was turned off. The agent's container tool had been showing the model this number.

**Fix (ADR-025):** use `container.cpu.usage.total`, a **counter** of CPU time used in nanoseconds. Its rate is cores in use:

```
cores = (counter now − counter before the window) ÷ seconds between them ÷ 1,000,000,000
```

We measured instead of guessing: healthy containers use under 0.3 cores, and `adHighCpu` burns about 4. New rule: **more than 1.5 cores, two checks in a row.** S6 was then detected in 81 s, and the agent cited "3.983 cores", which the verifier recomputed and confirmed.

### S8: a fast leak looks like a crash loop
At 1000× the email service's memory fills in about a minute, the kernel kills it (**OOM kill**), Docker restarts it, and the cycle repeats. Memory saw-tooths between 50% and 88%. Our memory rule averages over 2 minutes and needs more than 90% three times in a row, so it never fires. This is a **real detection gap**, recorded, not tuned away. The fix candidates are a restart-rate rule (the standard "crash loop" alert) or reading peak memory instead of the average. That decision belongs to the benchmark stage.

## Results (one episode each)

| | Detected in | Agent: service | Agent: category | Fix | Confidence |
|---|---|---|---|---|---|
| S2 | 37 s | ✅ | ❌ `app_bug` | ✅ revert flag | 0.85 → 0.85 |
| S3 | 55 s | ✅ | `config_regression` | ✅ | 0.85 (partial) |
| S4 | 205 s | ✅ | `config_regression` | ✅ | 0.78 → 0.52 |
| S5 | 268 s | ✅ | `config_regression` | ✅ | 0.95 |
| S6 | 81 s | ✅ | `config_regression` | ✅ | 0.95 |
| S8 | missed | | | | |

## What the results teach

**1. The verifier checks evidence, not reasoning (S2).** The model said "app bug, no recent configuration changes", while change correlation (plain code) had scored the `paymentUnreachable` flip at 0.69. The model cited one real number, so the verifier passed it at 85%. **Real evidence can support a wrong conclusion.** The fix was still right, because the "revert the flag that changed just before the alert" rule in remediation is code. Candidate improvement: a code check that flags a root cause claiming "no changes" when correlation found one.

**2. The verifier keeps catching invented metrics (S4).** `cart_error_rate` doesn't exist; it was dropped, and confidence fell from 78% to 52%. Same pattern as S1's `payment_error_rate`.

**3. "Which category is right?" is a scoring question (S3–S6).** Every dev fault is injected with a feature flag, so "a config change caused it" (`config_regression`) is *true*. The catalogue expects the mechanism (`memory_leak`, `cpu_saturation`). The benchmark must decide whether to accept both, require the mechanism, or score service and fix separately, and decide it *before* looking at more results.

**4. Free model tiers are flaky.** Gemini returned 503 ("high demand") on most runs, and Groq hit its rate limit once. In S3, Groq labelled a piece of evidence `trace` instead of `span`, which failed our schema, so the investigation step ran as a partial result. The fallback and partial-result design kept every run finishing.

**5. Detection speed depends on traffic.** Busy services (checkout, ~37 s) are detected faster than quiet ones (cart, 205 s), because a rate needs enough requests to mean something.

## Smaller findings
- **Infrastructure containers** (opensearch, prometheus) sit above 90% memory and opened incidents during captures. They're kept in the fixtures as noise.
- **Two minutes of warm-up isn't enough cool-down** after the previous scenario: leftover checkout errors from S3 opened an incident before S4's fault.
- **Tests share the local database** and rewrite the `alert_rules` table, which left an old incident unable to auto-resolve. Never run `make check` during a capture.

## Terms introduced

**Capture / fixture.** A capture tags a window of telemetry with the scenario key. A fixture is that window exported to a file (`S3.jsonl.gz`) so it can be replayed anywhere.

**flagd targeting.** A rule inside a flag ("if product_id is X, use variant A, else B"), evaluated **before** the default variant.

**Pre-flight check.** Validating inputs before starting expensive work, so a typo fails in a second instead of after a two-minute warm-up.

**Gauge vs cumulative counter.** A gauge is a current level (memory in use now). A counter only goes up (total CPU time used). For a counter, the rate over time is what's meaningful.

**OOM kill.** The kernel ends a process that exceeds its memory limit. With a restart policy, the container comes back and may be killed again: a **crash loop**.

**Cool-down.** Waiting after a fault is reverted until the system is back to normal, so the next experiment doesn't inherit the last one's symptoms.

## Interview questions
1. *Your verifier passed a wrong root cause. Why, and what would you do?* It checks that cited evidence exists and matches the data, not that the conclusion follows. S2's single citation was real. Next step: a code check that contradicts "no recent changes" when correlation found one, and scoring service, category and fix separately.
2. *How do you compute CPU usage from container metrics?* From the cumulative CPU-time counter: Δns ÷ Δseconds ÷ 10⁹ = cores, handling counter resets. Not a vendor's "utilization" gauge unless you've verified its semantics; on Docker Desktop it behaved like a lifetime average.
3. *How did you choose the CPU threshold?* Measured: healthy containers under 0.3 cores, the fault about 4. 1.5 is about 5× the busiest healthy one and still below the fault on a 2-core machine. Tuned on a dev scenario and recorded in an ADR.
4. *Why might a memory leak not trigger a memory alert?* A fast leak gets the container OOM-killed and restarted, so memory saw-tooths and a windowed average never crosses the threshold. Alert on restarts or on peak memory.
5. *Tell me about a bug in your test harness.* flagd evaluates targeting before the default variant, so our fault injection silently did nothing for one scenario. The capture showed zero error spans, which is how we caught it. The agent's own revert action had the same bug.
