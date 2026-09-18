# ADR-006: LangGraph with a Postgres checkpointer

**Status:** Accepted, 13 Sep 2026

## Context
The investigation is a multi-step workflow with a tool-calling loop, a hard budget, and a human approval step that may take minutes or hours. If the process dies mid-run, the run must resume, not restart.

## Decision
Model the agent as an explicit LangGraph state machine (triage → plan → investigate → correlate_changes → root_cause → verify_evidence → remediate → approval → execute → postmortem). Persist state after every node with `langgraph-checkpoint-postgres` into the same database (ADR-003). The `approval` node uses LangGraph's `interrupt()`; approving calls `POST /runs/{id}/approve`, which resumes the same thread.

## Alternatives considered
- **CrewAI / AutoGen.** Weak or no durable human-in-the-loop interrupts at the time of the decision.
- **Hand-rolled loop.** Full control, but re-implementing checkpoints, streaming and interrupts is exactly the undifferentiated work a framework should absorb.
- **Temporal / a queue + workers.** Excellent durability, but infra we cannot run for free (ADR-009).

## Consequences
- Every node is a pure function of state + tools with a Pydantic output schema (testable in isolation).
- Graph structure is data, so a test can walk the compiled graph and assert `execute` is reachable only from `approval` (ADR-007).
- Vendor coupling to LangGraph 1.x; acceptable for a portfolio project with a fixed end date.
