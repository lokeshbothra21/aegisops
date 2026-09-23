"""Tool access for the graph: registry over aegisops_tools, node-scoped authorization
(E9.2, first version) and budget accounting (E3.3).

Every call is recorded (`ToolCallRecord`) for the audit trail and the verifier. Tool
results reach the model only through `wrap_untrusted`.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aegisops_agent.schemas import Budget, ToolCallRecord, Usage
from aegisops_tools import telemetry
from aegisops_tools.context import ToolContext
from aegisops_tools.untrusted import wrap_untrusted

log = structlog.get_logger()

# Which node may call which tool (PROJECT.md §9.1). Anything not listed is denied.
NODE_TOOLS: dict[str, frozenset[str]] = {
    "triage": frozenset({"get_error_rate", "get_latency_percentiles", "get_top_error_logs"}),
    "plan": frozenset(
        {"get_service_dependencies", "get_recent_changes", "search_similar_incidents"}
    ),
    "investigate": frozenset(telemetry.TOOLS) - {"search_similar_incidents"},
    "correlate_changes": frozenset({"get_recent_changes", "get_service_dependencies"}),
}


class BudgetExceededError(Exception):
    def __init__(self, what: str) -> None:
        super().__init__(what)
        self.what = what


def tool_catalogue(node: str) -> list[dict[str, Any]]:
    """Name, description and argument names of the tools a node may use (shown to the model)."""
    out = []
    for name in sorted(NODE_TOOLS.get(node, frozenset())):
        fn = telemetry.TOOLS[name]
        params = [p for p in list(inspect.signature(fn).parameters)[2:]]
        out.append(
            {"tool": name, "args": params, "description": (fn.__doc__ or "").strip().split("\n")[0]}
        )
    return out


@dataclass
class ToolRunner:
    factory: async_sessionmaker[AsyncSession]
    ctx: ToolContext
    budget: Budget = field(default_factory=Budget)
    usage: Usage = field(default_factory=Usage)
    records: list[ToolCallRecord] = field(default_factory=list)

    async def call(self, node: str, hypothesis_id: str, tool: str, args: dict[str, Any]) -> str:
        """Run one tool for `node`; returns the untrusted envelope or an error envelope.

        Denied and failed calls still count against the budget: a model that keeps asking
        for the wrong thing runs out of moves.
        """
        if self.usage.tool_calls >= self.budget.max_tool_calls:
            raise BudgetExceededError("tool_calls")
        self.usage.tool_calls += 1
        if tool not in NODE_TOOLS.get(node, frozenset()):
            return self._fail(
                hypothesis_id, tool, args, f"tool {tool!r} is not available to node {node!r}"
            )
        fn = telemetry.TOOLS.get(tool)
        if fn is None:
            return self._fail(hypothesis_id, tool, args, f"unknown tool {tool!r}")
        allowed = set(list(inspect.signature(fn).parameters)[2:])
        clean = {k: v for k, v in args.items() if k in allowed}
        try:
            async with self.factory() as session:
                payload = await fn(session, self.ctx, **clean)
        except Exception as exc:
            return self._fail(hypothesis_id, tool, clean, f"{type(exc).__name__}: {str(exc)[:200]}")
        out = wrap_untrusted(payload)
        self.records.append(
            ToolCallRecord(
                hypothesis_id=hypothesis_id, tool=tool, args=clean, ok=True, bytes=len(out)
            )
        )
        log.info("tool.ok", node=node, tool=tool, bytes=len(out))
        return out

    def _fail(self, hypothesis_id: str, tool: str, args: dict[str, Any], error: str) -> str:
        self.records.append(
            ToolCallRecord(
                hypothesis_id=hypothesis_id, tool=tool, args=args, ok=False, bytes=0, error=error
            )
        )
        log.warning("tool.failed", tool=tool, error=error)
        return wrap_untrusted({"error": error})
