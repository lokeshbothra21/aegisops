"""The investigation graph (E3.1): triage -> plan -> investigate -> root_cause.

Every node is `async def node(state) -> partial state`, reads its prompt from
`prompts/`, calls the model through the router with a Pydantic schema (E3.2), and
records usage. A budget breach anywhere jumps to `root_cause` with `partial=True`
(E3.3). State is checkpointed after each node by LangGraph's Postgres saver (ADR-006).
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

import structlog
from langgraph.graph import END, START, StateGraph
from langgraph.types import Checkpointer
from pydantic import BaseModel

from aegisops_agent.llm import LLMClient, LLMResult
from aegisops_agent.schemas import (
    Budget,
    Findings,
    Hypotheses,
    RootCause,
    RootCauseCategory,
    Triage,
    Usage,
)
from aegisops_agent.tools import BudgetExceededError, ToolRunner, tool_catalogue

log = structlog.get_logger()
PROMPTS = Path(__file__).parent / "prompts"
PROMPT_VERSION = "2026-09-23.1"


class AgentState(TypedDict, total=False):
    # input
    incident_id: int | None
    service: str
    alert_summary: str
    # per node outputs (JSON-serialisable so the checkpointer can store them)
    triage: dict[str, Any]
    hypotheses: dict[str, Any]
    tool_results: dict[str, list[dict[str, Any]]]  # hypothesis id -> [{tool, args, result}]
    findings: dict[str, Any]
    root_cause: dict[str, Any]
    # bookkeeping
    usage: dict[str, Any]
    budget_exceeded: str | None
    events: list[dict[str, Any]]
    started_at: float
    prompt_version: str


def load_prompt(node: str) -> str:
    return (PROMPTS / "_shared.md").read_text() + "\n\n" + (PROMPTS / f"{node}.md").read_text()


@dataclass
class Deps:
    """Everything a node needs that is not state: model access and tool access."""

    llm: LLMClient
    tools: ToolRunner
    budget: Budget
    clock: Callable[[], float] = time.monotonic


def _event(state: AgentState, node: str, kind: str, **payload: Any) -> list[dict[str, Any]]:
    return [*state.get("events", []), {"node": node, "type": kind, **payload}]


def _usage(state: AgentState) -> Usage:
    return Usage.model_validate(state.get("usage") or {})


def _account(usage: Usage, result: LLMResult[Any]) -> Usage:
    usage.llm_calls += 1
    usage.tokens_in += result.tokens_in
    usage.tokens_out += result.tokens_out
    usage.model_fallbacks += int(result.fallback)
    return usage


def _check_budget(state: AgentState, deps: Deps, usage: Usage) -> str | None:
    if usage.tokens > deps.budget.max_tokens:
        return "tokens"
    if deps.clock() - state.get("started_at", deps.clock()) > deps.budget.max_seconds:
        return "seconds"
    if usage.tool_calls > deps.budget.max_tool_calls:
        return "tool_calls"
    return None


async def _ask[T: BaseModel](
    deps: Deps, node: str, user: dict[str, Any], schema: type[T]
) -> LLMResult[T]:
    return await deps.llm.complete(node, load_prompt(node), json.dumps(user, default=str), schema)


type NodeFn = Callable[[AgentState], Awaitable[AgentState]]


def build_nodes(deps: Deps) -> dict[str, NodeFn]:
    async def triage(state: AgentState) -> AgentState:
        usage = _usage(state)
        snapshot: dict[str, Any] = {}
        try:
            for tool in ("get_error_rate", "get_latency_percentiles", "get_top_error_logs"):
                snapshot[tool] = await deps.tools.call(
                    "triage", "-", tool, {"service": state["service"], "window_minutes": 5}
                )
        except BudgetExceededError as exc:
            return {
                "budget_exceeded": exc.what,
                "usage": usage.model_dump(),
                "events": _event(state, "triage", "budget_exceeded", what=exc.what),
            }
        usage.tool_calls = deps.tools.usage.tool_calls
        result = await _ask(
            deps,
            "triage",
            {"alert": state["alert_summary"], "service": state["service"], "snapshot": snapshot},
            Triage,
        )
        _account(usage, result)
        return {
            "triage": result.value.model_dump(),
            "usage": usage.model_dump(),
            "budget_exceeded": _check_budget(state, deps, usage),
            "events": _event(
                state, "triage", "output", **result.value.model_dump(), model=result.model
            ),
        }

    async def plan(state: AgentState) -> AgentState:
        usage = _usage(state)
        service = state["triage"]["service"]
        context: dict[str, Any] = {}
        try:
            context["dependencies"] = await deps.tools.call(
                "plan", "-", "get_service_dependencies", {"service": service, "depth": 2}
            )
            context["recent_changes"] = await deps.tools.call(
                "plan", "-", "get_recent_changes", {"window_minutes": 60}
            )
        except BudgetExceededError as exc:
            return {
                "budget_exceeded": exc.what,
                "usage": usage.model_dump(),
                "events": _event(state, "plan", "budget_exceeded", what=exc.what),
            }
        usage.tool_calls = deps.tools.usage.tool_calls
        user = {
            "triage": state["triage"],
            **context,
            "available_tools": tool_catalogue("investigate"),
        }
        result = await _ask(deps, "plan", user, Hypotheses)
        _account(usage, result)
        return {
            "hypotheses": result.value.model_dump(),
            "usage": usage.model_dump(),
            "budget_exceeded": _check_budget(state, deps, usage),
            "events": _event(
                state,
                "plan",
                "output",
                hypotheses=[h.statement for h in result.value.items],
                model=result.model,
            ),
        }

    async def investigate(state: AgentState) -> AgentState:
        usage = _usage(state)
        hyps = Hypotheses.model_validate(state["hypotheses"])
        results: dict[str, list[dict[str, Any]]] = {}
        breached: str | None = None
        for h in hyps.items:
            results[h.id] = []
            for req in h.tools_to_run:
                try:
                    out = await deps.tools.call("investigate", h.id, req.tool, req.args.as_kwargs())
                except BudgetExceededError as exc:
                    breached = exc.what
                    break
                results[h.id].append(
                    {"tool": req.tool, "args": req.args.as_kwargs(), "result": out}
                )
            if breached:
                break
        usage.tool_calls = deps.tools.usage.tool_calls
        if breached:
            return {
                "tool_results": results,
                "budget_exceeded": breached,
                "usage": usage.model_dump(),
                "events": _event(state, "investigate", "budget_exceeded", what=breached),
            }
        result = await _ask(
            deps,
            "investigate",
            {"hypotheses": state["hypotheses"], "tool_results": results},
            Findings,
        )
        _account(usage, result)
        return {
            "tool_results": results,
            "findings": result.value.model_dump(),
            "usage": usage.model_dump(),
            "budget_exceeded": _check_budget(state, deps, usage),
            "events": _event(
                state,
                "investigate",
                "output",
                supported=[f.hypothesis_id for f in result.value.items if f.supports],
                model=result.model,
            ),
        }

    async def root_cause(state: AgentState) -> AgentState:
        usage = _usage(state)
        partial = state.get("budget_exceeded") is not None
        user = {
            "alert": state["alert_summary"],
            "triage": state.get("triage"),
            "hypotheses": state.get("hypotheses"),
            "findings": state.get("findings"),
            "partial": partial,
            "budget_exceeded": state.get("budget_exceeded"),
            "allowed_categories": [c.value for c in RootCauseCategory],
        }
        try:
            result = await _ask(deps, "root_cause", user, RootCause)
        except Exception as exc:
            log.warning("root_cause.llm_failed", error=str(exc)[:200])
            rc = RootCause(
                service=state.get("triage", {}).get("service", state["service"]),
                category=RootCauseCategory.no_incident,
                statement=f"investigation could not conclude: {str(exc)[:200]}",
                confidence=0.0,
                partial=True,
            )
            return {
                "root_cause": rc.model_dump(),
                "usage": usage.model_dump(),
                "events": _event(state, "root_cause", "failed", error=str(exc)[:200]),
            }
        _account(usage, result)
        rc = result.value
        if partial:
            rc.partial = True
        return {
            "root_cause": rc.model_dump(),
            "usage": usage.model_dump(),
            "events": _event(state, "root_cause", "output", **rc.model_dump(), model=result.model),
        }

    return {"triage": triage, "plan": plan, "investigate": investigate, "root_cause": root_cause}


def _after(node_next: str) -> Callable[[AgentState], Literal["root_cause"] | str]:
    def route(state: AgentState) -> str:
        return "root_cause" if state.get("budget_exceeded") else node_next

    return route


def build_graph(deps: Deps, checkpointer: Checkpointer | None = None) -> Any:
    nodes = build_nodes(deps)
    g: StateGraph[AgentState] = StateGraph(AgentState)
    for name, fn in nodes.items():
        g.add_node(name, cast(Any, fn))  # LangGraph's node protocol is not expressible for mypy
    g.add_edge(START, "triage")
    g.add_conditional_edges("triage", _after("plan"), {"plan": "plan", "root_cause": "root_cause"})
    g.add_conditional_edges(
        "plan", _after("investigate"), {"investigate": "investigate", "root_cause": "root_cause"}
    )
    g.add_conditional_edges("investigate", _after("root_cause"), {"root_cause": "root_cause"})
    g.add_edge("root_cause", END)
    return g.compile(checkpointer=checkpointer, name="aegisops-investigation")
