# ADR-010: Held-out scenarios S9–S12 evaluated once

**Status:** Accepted, 13 Sep 2026

## Context
Prompts and tools will be tuned on the faults available during development. Reporting accuracy on those same faults would be training-set accuracy.

## Decision
Scenarios S1–S8 (the demo's built-in flags) are the dev set. S9–S12 are custom faults built in Week 8 (bad deploy, DB pool exhaustion, a stopped dependency, an Envoy route delay). They are run **once**, in Week 11, and the numbers are published unedited alongside the dev-set numbers. Three "no incident" scenarios (N1–N3) measure false positives.

## Alternatives considered
- **Report dev-set accuracy only.** Easy and inflated.
- **k-fold over the eight built-in faults.** Still leaks: the faults are all flag toggles of the same shape.

## Consequences
- An honest generalisation gap will be visible; the plan expects held-out accuracy to be lower and treats explaining *why* as a feature (the Failures page).
- Custom fault overlays must be reproducible from the repo (`infra/faults/`).
