"""Run one investigation: the API drives the graph step by step (streaming, interrupts);
the CLI runs it end to end (Week 3 exit criterion: S1 -> RootCause JSON).

    uv run aegis-investigate --service payment --alert "high-error-rate: error_rate = 0.83"
    uv run aegis-investigate ... --scenario S1 --frozen-now 2026-09-22T17:35:40+00:00
    uv run aegis-investigate ... --recorded cassettes/s1.yaml   (no model key needed)

The checkpointer needs a psycopg URL; we derive it from AEGIS_DATABASE_URL.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine

from aegisops_agent.actions import ActionExecutor
from aegisops_agent.graph import PROMPT_VERSION, AgentState, Deps, build_graph
from aegisops_agent.llm import LLMClient, RecordedLLM, Router, load_models_config
from aegisops_agent.remediation import Policy, load_policy
from aegisops_agent.schemas import Budget, VerifiedRootCause
from aegisops_agent.tools import ToolRunner
from aegisops_tools.context import ToolContext
from aegisops_tools.db import DEFAULT_URL, engine_from_env, session_factory

log = structlog.get_logger()
MODELS_CONFIG = Path(__file__).resolve().parents[4] / "config" / "models.yaml"
POLICY_CONFIG = Path(__file__).resolve().parents[4] / "config" / "policy.yaml"


def psycopg_url(sqlalchemy_url: str) -> str:
    return sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://", 1)


class StepEvent(BaseModel):
    """One graph step as seen by the API: the node that ran and its state update, or an
    interrupt (approval requested) carrying the payload the human must decide on."""

    node: str
    update: dict[str, Any]
    interrupt: dict[str, Any] | None = None


def make_graph(
    *,
    engine: AsyncEngine,
    llm: LLMClient,
    ctx: ToolContext,
    budget: Budget | None = None,
    policy: Policy | None = None,
    checkpointer: AsyncPostgresSaver | None = None,
    executor: ActionExecutor | None = None,
    run_id: int | None = None,
) -> tuple[Any, ToolRunner]:
    budget = budget or Budget()
    audit = executor.audit if executor is not None else None
    tools = ToolRunner(
        factory=session_factory(engine), ctx=ctx, budget=budget, audit=audit, run_id=run_id
    )
    deps = Deps(llm=llm, tools=tools, budget=budget, policy=policy, executor=executor)
    return build_graph(deps, checkpointer=checkpointer), tools


def initial_state(
    *,
    service: str,
    alert_summary: str,
    incident_id: int | None,
    autonomy_level: int = 1,
    public_mode: bool = False,
    auto_decision: str | None = None,
    run_id: int | None = None,
) -> AgentState:
    return {
        "run_id": run_id,
        "incident_id": incident_id,
        "service": service,
        "alert_summary": alert_summary,
        "autonomy_level": autonomy_level,
        "public_mode": public_mode,
        "auto_decision": auto_decision,
        "usage": {},
        "events": [],
        "started_at": time.monotonic(),
        "prompt_version": PROMPT_VERSION,
    }


def resume_command(decision: str, by: str, note: str = "") -> Command:  # type: ignore[type-arg]
    return Command(resume={"decision": decision, "by": by, "note": note})


async def stream_steps(graph: Any, inp: Any, thread_id: str) -> AsyncIterator[StepEvent]:
    """Drive the graph one node at a time. `inp` is the initial state or a resume Command."""
    config = {"configurable": {"thread_id": thread_id}}
    async for update in graph.astream(inp, config=config, stream_mode="updates"):
        for node, payload in update.items():
            if node == "__interrupt__":
                intr = payload[0] if isinstance(payload, (list, tuple)) else payload
                value = getattr(intr, "value", intr)
                yield StepEvent(node="approval", update={}, interrupt=dict(value or {}))
            else:
                yield StepEvent(node=node, update=dict(payload or {}))


async def run_investigation(
    *,
    engine: AsyncEngine,
    llm: LLMClient,
    ctx: ToolContext,
    service: str,
    alert_summary: str,
    thread_id: str,
    incident_id: int | None = None,
    budget: Budget | None = None,
    checkpointer: AsyncPostgresSaver | None = None,
    policy: Policy | None = None,
    autonomy_level: int = 1,
    public_mode: bool = False,
    auto_decision: str = "rejected",
) -> tuple[VerifiedRootCause, AgentState]:
    """End to end in one process. Without a checkpointer the graph cannot pause, so the
    approval node is answered with `auto_decision` right away; with one, the interrupt is
    resumed the same way (no human here: CLI and tests)."""
    graph, tools = make_graph(
        engine=engine, llm=llm, ctx=ctx, budget=budget, policy=policy, checkpointer=checkpointer
    )
    initial = initial_state(
        service=service,
        alert_summary=alert_summary,
        incident_id=incident_id,
        autonomy_level=autonomy_level,
        public_mode=public_mode,
        auto_decision=None if checkpointer is not None else auto_decision,
    )
    config = {"configurable": {"thread_id": thread_id}}
    final: AgentState = await graph.ainvoke(initial, config=config)
    if "__interrupt__" in final and checkpointer is not None:
        final = await graph.ainvoke(resume_command(auto_decision, "cli"), config=config)
    final["tool_records"] = [r.model_dump() for r in tools.records]  # type: ignore[typeddict-unknown-key]
    return VerifiedRootCause.model_validate(final["verified_root_cause"]), final


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="aegis-investigate")
    p.add_argument("--service", required=True)
    p.add_argument("--alert", required=True, help="alert summary text")
    p.add_argument("--scenario", default=None, help="scenario_id for replay; unset = live rows")
    p.add_argument("--frozen-now", default=None, help="ISO timestamp used as now (replay)")
    p.add_argument("--recorded", default=None, help="cassette file: replay model outputs")
    p.add_argument("--thread", default=None, help="checkpoint thread id (default: new)")
    p.add_argument("--no-checkpoint", action="store_true")
    p.add_argument("--models", default=str(MODELS_CONFIG))
    p.add_argument("--policy", default=str(POLICY_CONFIG))
    p.add_argument(
        "--decision",
        default="rejected",
        choices=["approved", "rejected"],
        help="what the CLI answers when the graph asks for approval (no human here)",
    )
    return p.parse_args(argv)


async def _main(argv: list[str]) -> int:
    args = _parse_args(argv)
    engine = engine_from_env()
    ctx = ToolContext(
        scenario_id=args.scenario,
        frozen_now=datetime.fromisoformat(args.frozen_now) if args.frozen_now else None,
    )
    llm: LLMClient = (
        RecordedLLM.from_file(args.recorded)
        if args.recorded
        else Router.from_env(load_models_config(args.models))
    )
    policy = load_policy(args.policy)
    thread_id = args.thread or f"cli-{int(time.time())}"
    common: dict[str, Any] = {
        "engine": engine,
        "llm": llm,
        "ctx": ctx,
        "service": args.service,
        "alert_summary": args.alert,
        "thread_id": thread_id,
        "policy": policy,
        "auto_decision": args.decision,
    }
    try:
        if args.no_checkpoint:
            rc, final = await run_investigation(**common)
        else:
            url = psycopg_url(os.environ.get("AEGIS_DATABASE_URL", DEFAULT_URL))
            async with AsyncPostgresSaver.from_conn_string(url) as saver:
                await saver.setup()
                rc, final = await run_investigation(**common, checkpointer=saver)
    finally:
        await engine.dispose()
    out: dict[str, Any] = {
        "thread_id": thread_id,
        "root_cause": rc.model_dump(),
        "claimed_root_cause": final.get("root_cause"),
        "correlation": final.get("correlation"),
        "remediation": final.get("remediation"),
        "policy_decision": final.get("policy_decision"),
        "approval": final.get("approval"),
        "usage": final.get("usage"),
        "budget_exceeded": final.get("budget_exceeded"),
        "events": [
            {k: v for k, v in e.items() if k in ("node", "type", "model")}
            for e in final.get("events", [])
        ],
    }
    print(json.dumps(out, indent=2, default=str))
    return 0


def main() -> None:
    # logs to stderr so stdout is exactly one JSON document (pipe-friendly)
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    sys.exit(asyncio.run(_main(sys.argv[1:])))


if __name__ == "__main__":
    main()
