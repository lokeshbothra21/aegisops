"""Run one investigation end to end (Week 3 exit criterion: S1 -> RootCause JSON).

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
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.ext.asyncio import AsyncEngine

from aegisops_agent.graph import PROMPT_VERSION, AgentState, Deps, build_graph
from aegisops_agent.llm import LLMClient, RecordedLLM, Router, load_models_config
from aegisops_agent.schemas import Budget, RootCause
from aegisops_agent.tools import ToolRunner
from aegisops_tools.context import ToolContext
from aegisops_tools.db import DEFAULT_URL, engine_from_env, session_factory

log = structlog.get_logger()
MODELS_CONFIG = Path(__file__).resolve().parents[4] / "config" / "models.yaml"


def psycopg_url(sqlalchemy_url: str) -> str:
    return sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://", 1)


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
) -> tuple[RootCause, AgentState]:
    budget = budget or Budget()
    tools = ToolRunner(factory=session_factory(engine), ctx=ctx, budget=budget)
    graph = build_graph(Deps(llm=llm, tools=tools, budget=budget), checkpointer=checkpointer)
    initial: AgentState = {
        "incident_id": incident_id,
        "service": service,
        "alert_summary": alert_summary,
        "usage": {},
        "events": [],
        "started_at": time.monotonic(),
        "prompt_version": PROMPT_VERSION,
    }
    final: AgentState = await graph.ainvoke(
        initial, config={"configurable": {"thread_id": thread_id}}
    )
    final["tool_records"] = [r.model_dump() for r in tools.records]  # type: ignore[typeddict-unknown-key]
    return RootCause.model_validate(final["root_cause"]), final


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="aegis-investigate")
    p.add_argument("--service", required=True)
    p.add_argument("--alert", required=True, help="alert summary text")
    p.add_argument("--scenario", default=None, help="scenario_id for replay; unset = live rows")
    p.add_argument("--frozen-now", default=None, help="ISO timestamp used as now (replay)")
    p.add_argument(
        "--recorded",
        default=None,
        help="cassette file: replay model outputs instead of calling a model",
    )
    p.add_argument("--thread", default=None, help="checkpoint thread id (default: new)")
    p.add_argument("--no-checkpoint", action="store_true")
    p.add_argument("--models", default=str(MODELS_CONFIG))
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
    thread_id = args.thread or f"cli-{int(time.time())}"
    saver_cm = (
        None
        if args.no_checkpoint
        else AsyncPostgresSaver.from_conn_string(
            psycopg_url(os.environ.get("AEGIS_DATABASE_URL", DEFAULT_URL))
        )
    )
    try:
        if saver_cm is None:
            rc, final = await run_investigation(
                engine=engine,
                llm=llm,
                ctx=ctx,
                service=args.service,
                alert_summary=args.alert,
                thread_id=thread_id,
            )
        else:
            async with saver_cm as saver:
                await saver.setup()
                rc, final = await run_investigation(
                    engine=engine,
                    llm=llm,
                    ctx=ctx,
                    service=args.service,
                    alert_summary=args.alert,
                    thread_id=thread_id,
                    checkpointer=saver,
                )
    finally:
        await engine.dispose()
    out: dict[str, Any] = {
        "thread_id": thread_id,
        "root_cause": rc.model_dump(),
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
