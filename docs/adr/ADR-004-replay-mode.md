# ADR-004: Replay mode for the public demo, CI and benchmark

**Status:** Accepted, 13 Sep 2026

## Context
The public demo must run at ₹0, be deterministic enough to benchmark, and never execute real actions against infrastructure a stranger can trigger. Running the 15-service demo in the cloud costs money and is flaky.

## Decision
Two modes over **one code path**. *Live mode* on the laptop streams telemetry from the demo and performs real actions. *Replay mode* serves pre-captured telemetry rows tagged with a `scenario_id`, freezes time at the capture window, and returns recorded action outcomes. The agent code does not know which mode it is in; only the tool layer (query filter) and the action backend differ.

## Alternatives considered
- **Live demo in the cloud.** Cost, flakiness, and anyone could inject faults.
- **Recorded LLM responses ("cassettes") for the public demo.** Deterministic but dishonest as a demo; kept only for graph unit tests.
- **Public mode that only shows screenshots/videos.** Not interactive; weak portfolio value.

## Consequences
- Capture tooling (E1.6) and fixture export/import become must-have features.
- Every telemetry table carries `scenario_id`; retention never deletes tagged rows.
- The benchmark is reproducible by anyone from the published fixtures.
- Interview line: "the same agent that ran live on my laptop runs in replay on Cloud Run, and I can prove it because it is one code path."
