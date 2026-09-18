# ADR-008: Deterministic alerting and deterministic evidence verification

**Status:** Accepted, 13 Sep 2026

## Context
Two places invite "let the LLM do it": deciding *that* there is an incident, and deciding whether the LLM's own root-cause claims are true. Both are where hallucination does the most damage.

## Decision
- **Detection is code.** An alert evaluator runs rules (5xx rate, p95 vs baseline, memory, Kafka lag) every 30 s over SQL aggregates and opens incidents. No LLM is involved in detection.
- **Verification is code.** After `root_cause`, a `verify_evidence` node (no LLM) checks that every cited evidence reference exists in the store and that every numeric claim is within ±20 % of the actual value. Unverifiable claims are dropped and confidence is scaled by verified/cited.

## Alternatives considered
- **LLM anomaly detection over dashboards.** Expensive per tick, non-reproducible, and false positives are the failure mode the benchmark measures.
- **LLM-as-judge for verification.** Judging hallucinations with the same class of model that produced them; not defensible.

## Consequences
- "LLM proposes, code verifies" is the reliability story of the project (Goal G2: evidence precision ≥ 0.9).
- Verifier and policy code must have 100 % branch coverage (PROJECT.md §14.4), including adversarial fabricated references.
- Deterministic pieces are also what make replay mode reproducible.
