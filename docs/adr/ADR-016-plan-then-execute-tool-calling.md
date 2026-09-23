# ADR-016: Plan-then-execute tool calling instead of a free-form tool loop

**Status:** Accepted, 23 Sep 2026 (E3.1)

## Context
The `investigate` node must gather evidence with the read tools under a hard budget (15 tool calls, 60k tokens, 180 s). The common agent pattern is a free-form loop: the model picks a tool, sees the result, picks another, until it decides to stop. It is flexible but hard to bound, hard to test deterministically, and every step is a model call.

## Decision
`plan` produces at most three hypotheses, each with an explicit list of tool requests (name + arguments, validated against a catalogue of the tools that node may use). `investigate` executes those requests deterministically, in order, under the budget, then makes **one** model call to turn all results into findings with evidence references. The graph is fixed: triage → plan → investigate → root_cause, with a conditional jump to root_cause on any budget breach.

## Alternatives considered
- **Free-form tool loop (ReAct).** One model call per tool call: 15 tool calls ≈ 15 model calls, most of the token budget spent re-reading context. Non-deterministic paths make cassette replay brittle and the budget hard to reason about.
- **Fully deterministic runbook (no planning).** Cheap and testable but throws away the point of an LLM: choosing what to look at given the alert and recent changes.

## Consequences
- A full run is four model calls regardless of how many tools run; the budget is dominated by tool output size, which the tools cap at 4 KB each.
- Cassette tests are stable: the same plan yields the same tool calls.
- Node-scoped tool authorization is enforced at execution time; the model cannot reach a tool its node is not allowed.
- The model cannot react to a surprising tool result mid-investigation. Mitigation planned: a second, bounded "follow-up" round in Week 4 alongside `correlate_changes` (E3.4).
