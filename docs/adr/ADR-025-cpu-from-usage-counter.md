# ADR-025: CPU is the rate of `container.cpu.usage.total` (cores), not `container.cpu.utilization`

**Status:** Accepted, 22 Oct 2026

## Context
The first S6 capture (`adHighCpu`) opened no incident. Two reasons came to light:
1. **There was no CPU alert rule at all.** The rules covered error rate, error bursts, p95 against a baseline, memory and Kafka lag (ADR-008). A CPU-saturation scenario couldn't be detected unless latency happened to rise.
2. **The CPU metric we stored was wrong for this purpose.** On Docker Desktop and OrbStack, docker_stats' `container.cpu.utilization` behaved like a **lifetime average**:
   - ad's value climbed in a straight line from 0.01 to 1.06 during the fault, then stayed at 1.06 after the revert;
   - load-generator rose steadily from 0.004 to 0.318 over an hour, while `docker stats` showed it swinging between 1% and 93%.

   It never falls, so a threshold on it can't fire correctly or clear. The agent's `get_container_metrics` tool returned the same numbers, so the model had been reading them too.

docker_stats also emits **`container.cpu.usage.total`**: total CPU time the container has used, in nanoseconds, as a monotonic cumulative counter. Its rate is unambiguous: ns per second ÷ 10⁹ = cores in use.

## Decision
- **Collector:** allowlist `container.cpu.usage.total`. `container.cpu.utilization` stays allowlisted for comparison only; nothing reads it.
- **Alert reader `container_cpu_cores`:** for each container, (latest counter value in the window − last value before it) ÷ the seconds between those two samples ÷ 10⁹. This is the same "counter delta" method as the error-rate reader. A counter that went down (container restarted) gives no reading for that window, rather than a negative or huge one.
- **Rule `container-cpu-high`:** more than **1.5 cores** for 2 evaluations over a 120 s window. Measured live on this machine (10 cores):
  - healthy containers use under 0.07 cores, except load-generator at about 0.28;
  - `adHighCpu` burns about 4 cores (`docker stats`: 456%).

  1.5 is about 5× the busiest healthy container, and still below what the fault produces on a 2-core machine, where it would cap at 2.
- **Tool and verifier:** `get_container_metrics` returns `cpu_cores` per minute, calculated from the same counter. The verifier recomputes `cpu_cores` claims. `cpu_utilization` is gone from both, so a model can't cite a number we no longer stand behind.

## Alternatives considered
- **Keep `container.cpu.utilization` and threshold its slope.** It separates the fault in this data, but it reverse-engineers an undocumented artefact of one Docker runtime, in arbitrary units.
- **Threshold `container.cpu.utilization` directly, as documented.** On Linux Docker it may well be correct, but here it isn't, and the demo runs on laptops. A rule that depends on which Docker runtime you use isn't reproducible (G5).
- **A ratio to each container's own baseline** (like p95). Tiny containers swing by large ratios at near-zero load (0.01 → 0.06 is "6×"), so it needs a floor anyway, and an absolute core count reads more clearly in an incident summary: "ad = 2.4 cores".
- **No CPU rule, relying on latency.** In S6, ad's latency didn't regress enough within 5 minutes. CPU saturation that hasn't hurt users *yet* is exactly what an operator wants to know about.

## Consequences
- S6 detected live in **81 s** ("ad = 2.398 cores > 1.5"). The replayed agent read "3.983 cores" from the tool, and the verifier confirmed it.
- The threshold is an absolute number tuned on one machine and one target (a dev scenario: allowed, recorded here). Containers with CPU limits or larger hosts may need a different value, and operators can edit the rule row (rules in the database aren't overwritten on start).
- Captures before 22 Oct (S1, S2, S5) don't contain the counter, so `cpu_cores` is empty in their replays. Their faults aren't CPU faults.
