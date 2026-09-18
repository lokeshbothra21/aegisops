# ADR-001: One flagship project; ForgePilot rejected

**Status:** Accepted, 13 Sep 2026

## Context
Fourteen build weeks are available between mid-September and the 22 December code freeze, split by a six-week gap. Two candidate projects existed: AegisOps (incident response over real telemetry) and ForgePilot (an agent that writes and merges code patches). Interviews open 1 March 2027.

## Decision
Build AegisOps only. Everything in the plan serves that one deliverable.

## Alternatives considered
- **Both projects.** Two half-finished projects show breadth but no depth; neither would reach a published benchmark.
- **ForgePilot.** The software-engineering-agent space is owned by well-funded incumbents (Devin, Copilot Workspace, SWE-agent). A solo project cannot differentiate there, and evaluation would depend on SWE-bench-style harnesses that are expensive to run.

## Consequences
- One codebase, one story, one benchmark to defend in interviews.
- Operational remediation (rollback, restart, flag toggle) is enough; no code generation is in scope (see ADR-005).
- If the schedule slips, features are cut from AegisOps (PROJECT.md §19 names what may be cut) rather than adding a second project.
