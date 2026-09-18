# ADR-005: Operational remediation only, rollback first; no code patches

**Status:** Accepted, 13 Sep 2026

## Context
Once a root cause is found, what should the agent do about it? Generating a code fix is the flashy option. Real SRE practice during an incident is to *restore service first*: roll back, toggle the feature off, restart, scale. Fixing the code happens later in daylight.

## Decision
The action set is fixed and small: `toggle_flag`, `restart_service`, `scale_service`, `rollback_deployment`. A category → action mapping table picks the default; risk tiers (low/medium) and the autonomy policy decide whether a human must approve. No code is written or merged by the agent.

## Alternatives considered
- **Code-patching agent.** Slow (minutes to hours), unverifiable during an incident, and the ForgePilot space (ADR-001).
- **Free-form shell commands proposed by the LLM.** Unbounded blast radius; impossible to allowlist.

## Consequences
- Actions are an allowlisted, parameter-validated module executed with fixed argv and no shell (PROJECT.md §11).
- Credible with SRE interviewers: "mean time to restore" beats "mean time to fix".
- Far less work, which is what makes a published benchmark reachable.
