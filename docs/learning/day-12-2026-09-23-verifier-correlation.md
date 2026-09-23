# Day 12 · 23 Sep 2026 (third PR) · Evidence verifier, change correlation, follow-up round (PR #23, E4.1, E4.2, E3.4)

## What we did
The other half of ADR-008, "LLM proposes, code verifies", plus the two things Day 11's wrong answer asked for:
- **`verify_evidence` (E4.1):** a deterministic node after `root_cause`. Every cited `EvidenceRef` is checked against the store: span refs must be a real trace/span id; log refs must match a log line (and a claimed count must be within ±20 %); metric refs must have data and a claimed value must match the recomputation for at least one standard window (5/15/30/60 min) within ±20 %; change refs must match an event by timestamp (±60 s) or by an identifying token (flag name, service, type). Unverifiable refs are **dropped with a reason**; `confidence := claimed × verified / cited`; the model's original confidence is kept as `claimed_confidence` for the UI.
- **Adversarial tests (E4.2):** fabricated trace ids, malformed ids, a log ref containing an injected instruction, a metric with a wrong number, a metric that does not exist, a change on the wrong flag, a timestamp with no event, a ref with no identifying token, and an empty scenario. Verifier at **100 % line coverage**.
- **`correlate_changes` (E3.4):** deterministic scoring of change events in ±30 min of the alert: recency (1 at the alert → 0 at 30 min before; changes *after* the alert × 0.3) × proximity (1 on the service, 0.7 on a direct caller/callee from `service_edges`, 0.3 elsewhere). The scored list goes into the `root_cause` prompt as data. No model call.
- **Bounded follow-up round (ADR-016 promise):** `Findings.follow_up` may name up to three more tool requests; `investigate` runs them once and asks the model again. Max five model calls per run.
- Graph is now triage → plan → investigate → correlate_changes → root_cause → verify_evidence. 109 tests, 95 %.

## Live result (real model, seeded S1)
Claimed: `config_regression` at **0.86**, citing the flag change by timestamp, a log signature, and a metric named `payment_error_rate`. Verified: the change and the log survived; the metric was **dropped** ("no data for service 'payment' in the last hour": the model invented a metric name) → confidence **0.573**, cited 3 / verified 2. Correlation put `paymentFailure: off -> 100%` first at 0.867, and this time the model cited it, unlike Day 11. Gemini answered "high demand" (503) on `plan` and timed out on `investigate` and `root_cause`; all three fell back to Groq. 183 s end to end, most of it two 60 s timeouts → provider timeout now 30 s.

## Terms introduced

**Evidence verification (implemented).** Existence + numeric agreement checks for every citation, done by code against the same store the tools read. The model's job is to propose; truth is checked outside it.

**Claimed vs verified confidence.** Keep both. The gap is a calibration signal: a model that claims 0.9 and verifies at 0.45 is over-confident; the benchmark measures this (§12.2 evidence precision; §26.1 calibration).

**Tolerance bands.** ±20 % relative, plus an absolute band (0.01) near zero so a rate of 0.00 vs 0.005 is not a "miss". A recomputation of exactly 0 with a non-zero claim always fails.

**Window ambiguity.** A number the model cites came from *some* tool window; recompute over the standard windows and accept any match rather than guessing which one. Strict enough to catch invention, lenient enough for honest citations.

**Identifying token.** For change refs the model rarely quotes a timestamp; accept a flag name, service or type token (≥ 4 chars) that appears in the event. Deterministic and cheap; false positives are bounded by the window.

**Temporal correlation.** "What changed just before it broke" as a score, not a prose argument: recency × proximity in the dependency graph. The single most useful prior in incident response, and one the model ignored until it was handed to it as data.

**Reaction vs cause.** A change *after* the alert cannot have caused it; scored low (× 0.3) but shown, because operators' reactions are context.

**Bounded follow-up.** One extra round of ≤ 3 tool calls: enough for the model to check a surprise, not enough to become an open loop.

**Provider timeout as a fallback trigger.** A slow provider is a down provider for budget purposes; 30 s then try the secondary. Two 60 s timeouts once consumed two thirds of the run budget.

**Model invents names.** `payment_error_rate` looks right and does not exist. Verification by name against a known set (and the tools' own `note`) is what turns that into a dropped ref instead of a believed one.

## Interview questions
1. *How do you verify an LLM's claims without another LLM?* Structured citations (kind, id, claim, number) checked by code against the store; drop what fails; scale confidence by the verified fraction.
2. *Why keep the claimed confidence?* Calibration: the gap between claimed and verified is a metric we publish.
3. *Why ±20 % and multiple windows?* Tool numbers depend on the window; accept any standard window, but a number off by more than a fifth is not a citation of the data.
4. *What did correlation change in practice?* The model went from ignoring the flag flip to citing it, once the scored change list was in its input.
5. *Why not let the model loop on tools freely?* Bounded follow-up gives one chance to react; cost stays at ≤ 5 model calls and tests stay deterministic.
6. *What happens when the primary provider is slow rather than down?* A 30 s timeout is treated as down; the router falls back and logs the exception type.
