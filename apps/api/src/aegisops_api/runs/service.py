"""RunManager: one investigation per run row, driven step by step.

start(incident)  -> Run row (running), incident -> investigating, background task
task             -> for each graph step: persist a run_event, publish to subscribers;
                    on interrupt: Remediation row (pending), run/incident -> awaiting_approval
decide(run, ...) -> Remediation decision recorded, graph resumed in a new task
finish           -> run status succeeded | failed | budget_exceeded, usage + cost stored

Events are the durable stream: SSE clients replay `run_events` from the DB, then follow
the live queue, so a reconnect never loses anything (§7 `/runs/{id}/events`).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import structlog
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from aegisops_agent.actions import ActionExecutor
from aegisops_agent.llm import LLMClient
from aegisops_agent.remediation import Policy
from aegisops_agent.run import StepEvent, initial_state, make_graph, resume_command, stream_steps
from aegisops_agent.schemas import Budget
from aegisops_api.alerts.evaluator import COMPARE
from aegisops_api.alerts.readers import READERS
from aegisops_api.incidents.lifecycle import transition
from aegisops_api.models import (
    ActionKind,
    AlertRule,
    Decision,
    Incident,
    IncidentStatus,
    Remediation,
    Risk,
    Run,
    RunEvent,
    RunStatus,
)
from aegisops_tools.context import ToolContext

log = structlog.get_logger()
END_MARKER: dict[str, Any] = {"node": "run", "type": "end"}
VERIFY_GRACE_S = 15.0  # > the span-metrics connector's 10 s flush interval


@dataclass
class RunHandle:
    run_id: int
    thread_id: str
    subscribers: list[asyncio.Queue[dict[str, Any] | None]] = field(default_factory=list)
    task: asyncio.Task[None] | None = None
    seq: int = 0
    done: bool = False


@dataclass
class RunManager:
    engine: AsyncEngine
    factory: async_sessionmaker[AsyncSession]
    llm_factory: Callable[[], LLMClient]
    policy: Policy | None
    checkpointer: AsyncPostgresSaver | None
    prices: dict[str, dict[str, float]] = field(default_factory=dict)
    public_mode: bool = False
    budget: Budget = field(default_factory=Budget)
    executor: ActionExecutor | None = None
    verify_delay_s: float = 90.0
    handles: dict[int, RunHandle] = field(default_factory=dict)
    verifications: set[asyncio.Task[None]] = field(default_factory=set)

    # --- public API ---------------------------------------------------------------

    async def start(
        self, session: AsyncSession, incident: Incident, *, model: str = "", variant: str = "D"
    ) -> Run:
        thread_id = f"inc-{incident.id}-{uuid4().hex[:12]}"
        run = Run(
            incident_id=incident.id,
            thread_id=thread_id,
            model=model,
            variant=variant,
            status=RunStatus.running,
            started_at=datetime.now(UTC),
        )
        session.add(run)
        if incident.status is IncidentStatus.open:
            transition(incident, IncidentStatus.investigating)
        await session.flush()
        handle = RunHandle(run_id=run.id, thread_id=thread_id)
        self.handles[run.id] = handle
        ctx = ToolContext(
            scenario_id=incident.scenario_id,
            frozen_now=incident.opened_at if incident.scenario_id else None,
        )
        inp = initial_state(
            run_id=run.id,
            service=incident.service,
            alert_summary=incident.summary or "",
            incident_id=incident.id,
            autonomy_level=incident.autonomy_level,
            public_mode=self.public_mode,
        )
        handle.task = asyncio.create_task(self._drive(handle, ctx, inp), name=f"run:{run.id}")
        return run

    async def decide(
        self, session: AsyncSession, run: Run, decision: Decision, by: str, note: str = ""
    ) -> Remediation:
        """Record the human decision and resume the paused graph."""
        if run.status is not RunStatus.awaiting_approval:
            raise ValueError(f"run {run.id} is {run.status.value}, not awaiting approval")
        rem = (
            await session.scalars(
                select(Remediation)
                .where(Remediation.run_id == run.id, Remediation.decision == Decision.pending)
                .order_by(Remediation.id.desc())
            )
        ).first()
        if rem is None:
            raise ValueError(f"run {run.id} has no pending remediation")
        rem.decision, rem.decided_by, rem.decided_at = decision, by, datetime.now(UTC)
        if self.executor is not None and self.executor.audit is not None:
            await self.executor.audit.write(
                run_id=run.id,
                node="approval",
                tool=rem.action.value,
                args={"decision": decision.value, "note": note, **(rem.params or {})},
                ok=True,
                actor=f"admin:{by}",
            )
        run.status = RunStatus.running
        incident = await session.get(Incident, run.incident_id)
        if incident is not None and incident.status is IncidentStatus.awaiting_approval:
            transition(
                incident,
                IncidentStatus.remediating
                if decision is Decision.approved
                else IncidentStatus.investigating,
            )
        await session.flush()
        handle = self.handles.get(run.id) or RunHandle(
            run_id=run.id, thread_id=run.thread_id, seq=await self._last_seq(session, run.id)
        )
        self.handles[run.id] = handle
        handle.done = False
        scenario_ctx = (
            ToolContext(
                scenario_id=incident.scenario_id,
                frozen_now=incident.opened_at if incident and incident.scenario_id else None,
            )
            if incident
            else ToolContext()
        )
        handle.task = asyncio.create_task(
            self._drive(handle, scenario_ctx, resume_command(decision.value, by, note)),
            name=f"run:{run.id}:resume",
        )
        return rem

    async def subscribe(self, run_id: int) -> AsyncIterator[dict[str, Any]]:
        """Live events for a run after the caller replayed the stored ones. Ends on run end."""
        handle = self.handles.get(run_id)
        if handle is None or handle.done:
            return
        q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        handle.subscribers.append(q)
        try:
            while True:
                item = await q.get()
                if item is None:
                    return
                yield item
        finally:
            handle.subscribers.remove(q)

    async def stop(self) -> None:
        for h in self.handles.values():
            if h.task and not h.task.done():
                h.task.cancel()
        for t in self.verifications:
            t.cancel()

    # --- internals ------------------------------------------------------------------

    async def _last_seq(self, session: AsyncSession, run_id: int) -> int:
        from sqlalchemy import func

        n = await session.scalar(select(func.max(RunEvent.seq)).where(RunEvent.run_id == run_id))
        return int(n or 0)

    async def _emit(
        self, handle: RunHandle, node: str, type_: str, payload: dict[str, Any]
    ) -> None:
        handle.seq += 1
        event = {
            "seq": handle.seq,
            "node": node,
            "type": type_,
            "payload": payload,
            "ts": datetime.now(UTC).isoformat(),
        }
        async with self.factory() as s:
            s.add(
                RunEvent(
                    run_id=handle.run_id,
                    seq=handle.seq,
                    node=node,
                    type=type_,
                    payload=payload,
                    ts=datetime.now(UTC),
                )
            )
            await s.commit()
        for q in list(handle.subscribers):
            q.put_nowait(event)

    def _cost(self, model: str, tokens_in: int, tokens_out: int) -> float:
        p = self.prices.get(model) or {}
        return round(tokens_in / 1e6 * p.get("in", 0.0) + tokens_out / 1e6 * p.get("out", 0.0), 6)

    async def _drive(self, handle: RunHandle, ctx: ToolContext, inp: Any) -> None:
        t0 = time.monotonic()
        graph, tools = make_graph(
            run_id=handle.run_id,
            executor=self.executor,
            engine=self.engine,
            llm=self.llm_factory(),
            ctx=ctx,
            budget=self.budget,
            policy=self.policy,
            checkpointer=self.checkpointer,
        )
        acc: dict[str, Any] = {}  # merged state across steps: the last step alone is not enough
        models_used: set[str] = set()
        try:
            async for step in stream_steps(graph, inp, handle.thread_id):
                if step.interrupt is not None:
                    await self._pause(handle, step)
                    return
                acc.update(step.update)
                for ev in step.update.get("events", [])[
                    -1:
                ]:  # the node appended its own event last
                    if ev.get("model"):
                        models_used.add(str(ev["model"]))
                    await self._emit(
                        handle,
                        step.node,
                        ev.get("type", "output"),
                        {k: v for k, v in ev.items() if k not in ("node", "type")},
                    )
                if "usage" in step.update:
                    await self._store_usage(handle, step.update["usage"], models_used)
            await self._finish(handle, acc, tools, t0, models_used)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("run.crashed", run_id=handle.run_id)
            await self._fail(handle, f"{type(exc).__name__}: {str(exc)[:300]}")

    async def _store_usage(
        self, handle: RunHandle, usage: dict[str, Any], models: set[str]
    ) -> None:
        async with self.factory() as s:
            run = await s.get(Run, handle.run_id)
            if run is None:
                return
            run.tokens_in = int(usage.get("tokens_in", 0))
            run.tokens_out = int(usage.get("tokens_out", 0))
            run.tool_calls = int(usage.get("tool_calls", 0))
            run.model = ",".join(sorted(models))[:64]
            run.cost_usd = sum(self._cost(m, run.tokens_in, run.tokens_out) for m in models) / max(
                len(models), 1
            )
            await s.commit()

    async def _pause(self, handle: RunHandle, step: StepEvent) -> None:
        intr = step.interrupt or {}
        rem = intr.get("remediation") or {}
        async with self.factory() as s:
            run = await s.get(Run, handle.run_id)
            assert run is not None
            run.status = RunStatus.awaiting_approval
            run.root_cause = intr.get("root_cause")
            s.add(
                Remediation(
                    run_id=run.id,
                    action=ActionKind(rem.get("action", "none")),
                    params=rem.get("params") or {},
                    risk=Risk(rem.get("risk", "low")),
                    confidence=float(rem.get("confidence", 0.0)),
                    rationale=rem.get("rationale"),
                    decision=Decision.pending,
                )
            )
            incident = await s.get(Incident, run.incident_id)
            if incident is not None and incident.status is IncidentStatus.investigating:
                transition(incident, IncidentStatus.awaiting_approval)
            await s.commit()
        await self._emit(
            handle,
            "approval",
            "approval_requested",
            {"remediation": rem, "policy": intr.get("policy")},
        )
        handle.done = True
        for q in list(handle.subscribers):
            q.put_nowait(None)

    async def _finish(
        self, handle: RunHandle, last: dict[str, Any], tools: Any, t0: float, models: set[str]
    ) -> None:
        verify = False
        rem_id: int | None = None
        execution: dict[str, Any] | None = last.get("execution")
        async with self.factory() as s:
            run = await s.get(Run, handle.run_id)
            assert run is not None
            state_rc = last.get("verified_root_cause")  # `last` is the merged state of this drive
            if state_rc is not None:
                run.root_cause = state_rc
            budget = last.get("budget_exceeded")
            run.status = RunStatus.budget_exceeded if budget else RunStatus.succeeded
            run.finished_at = datetime.now(UTC)
            # wall time from start, including any wait for approval (not just this drive)
            run.duration_ms = int((run.finished_at - run.started_at).total_seconds() * 1000)
            run.prompt_version = last.get("prompt_version")
            approval = last.get("approval") or {}
            rem = (
                await s.scalars(
                    select(Remediation)
                    .where(Remediation.run_id == run.id)
                    .order_by(Remediation.id.desc())
                )
            ).first()
            proposed = last.get("remediation") or {}
            if rem is None and (proposed.get("action") or "none") != "none":
                # policy auto-approved: no interrupt ever created the row
                rem = Remediation(
                    run_id=run.id,
                    action=ActionKind(proposed["action"]),
                    params=proposed.get("params") or {},
                    risk=Risk(proposed.get("risk", "low")),
                    confidence=float(proposed.get("confidence", 0.0)),
                    rationale=proposed.get("rationale"),
                    decision=Decision.pending,
                )
                s.add(rem)
            if rem is not None and approval.get("decision") == "auto":
                rem.decision, rem.decided_by, rem.decided_at = (
                    Decision.auto,
                    "policy",
                    datetime.now(UTC),
                )
            if rem is not None and execution is not None:
                rem.executed_at = datetime.now(UTC)
                rem.outcome = execution
                incident = await s.get(Incident, run.incident_id)
                if incident is not None and incident.status is IncidentStatus.investigating:
                    transition(incident, IncidentStatus.remediating)  # auto path: no human wait
                verify = bool(execution.get("ok"))
                if (
                    not verify
                    and incident is not None
                    and incident.status is IncidentStatus.remediating
                ):
                    transition(incident, IncidentStatus.failed)
            await s.flush()
            rem_id = rem.id if rem is not None else None
            await s.commit()
        await self._emit(
            handle, "run", "end", {"status": run.status.value, "duration_ms": run.duration_ms}
        )
        if verify and rem_id is not None:
            task = asyncio.create_task(
                self._verify(handle, run.incident_id, rem_id, execution or {}),
                name=f"verify:{run.id}",
            )
            self.verifications.add(task)
            task.add_done_callback(self.verifications.discard)
        handle.done = True
        for q in list(handle.subscribers):
            q.put_nowait(None)

    async def _verify(
        self, handle: RunHandle, incident_id: int, rem_id: int, execution: dict[str, Any]
    ) -> None:
        """Background task wrapper: log failures instead of letting them vanish."""
        try:
            await self._verify_inner(handle, incident_id, rem_id, execution)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("run.verification_failed", run_id=handle.run_id, incident_id=incident_id)

    async def _verify_inner(
        self, handle: RunHandle, incident_id: int, rem_id: int, execution: dict[str, Any]
    ) -> None:
        """E5.5: after the action, re-evaluate the rule that opened the incident. Cleared ->
        resolved; still breaching -> failed. Replayed actions use the recorded result."""
        await asyncio.sleep(self.verify_delay_s)
        async with self.factory() as s:
            incident = await s.get(Incident, incident_id)
            rem = await s.get(Remediation, rem_id)
            if incident is None or rem is None or incident.status is not IncidentStatus.remediating:
                log.info(
                    "run.verification_skipped",
                    run_id=handle.run_id,
                    incident_status=incident.status.value if incident else None,
                    remediation=rem is not None,
                )
                return
            value: float | None = None
            basis = "recorded" if execution.get("simulated") else "no_rule"
            if execution.get("simulated"):
                cleared = execution.get("alert_cleared")
            elif incident.alert_rule_id is None:
                cleared = None
            else:
                rule = await s.get(AlertRule, incident.alert_rule_id)
                if rule is None:
                    cleared = None
                else:
                    now = datetime.now(UTC)
                    # only data AFTER the action + a grace period: span-metrics counters flush
                    # every ~10 s, so the first sample after the action still carries errors from
                    # just before it (found live: a working fix was judged "still breaching")
                    since = (rem.executed_at or now) + timedelta(seconds=VERIFY_GRACE_S)
                    start = max(now - timedelta(seconds=rule.window_s), since)
                    readings = await READERS[rule.metric](s, start, now, incident.scenario_id)
                    value = readings.get(incident.service)
                    if value is not None:
                        basis = rule.metric
                        cleared = not COMPARE[rule.comparator](value, rule.threshold)
                    else:
                        # too little traffic for the rule (a rate below MIN_CALLS): fall back to the
                        # exact signal, error server spans since the action (all errors are kept)
                        errors = await READERS["error_count"](s, since, now, incident.scenario_id)
                        value = errors.get(incident.service, 0.0)
                        basis = "error_spans_since_action"
                        cleared = value == 0
            rem.outcome = {
                **(rem.outcome or {}),
                "alert_cleared": cleared,
                "verified_at": datetime.now(UTC).isoformat(),
                "value_after": value,
                "basis": basis,
            }
            if cleared is True:
                transition(incident, IncidentStatus.resolved)
                incident.summary = f"{incident.summary} | resolved by {rem.action.value}"
            elif cleared is False:
                transition(incident, IncidentStatus.failed)
            await s.commit()
        await self._emit(
            handle,
            "verification",
            "cleared" if cleared else ("still_breaching" if cleared is False else "unknown"),
            {"alert_cleared": cleared, "value_after": value},
        )

    async def _fail(self, handle: RunHandle, error: str) -> None:
        async with self.factory() as s:
            run = await s.get(Run, handle.run_id)
            if run is not None:
                run.status, run.error, run.finished_at = RunStatus.failed, error, datetime.now(UTC)
                incident = await s.get(Incident, run.incident_id)
                if incident is not None and incident.status is IncidentStatus.investigating:
                    transition(incident, IncidentStatus.failed)
                await s.commit()
        await self._emit(handle, "run", "failed", {"error": error})
        handle.done = True
        for q in list(handle.subscribers):
            q.put_nowait(None)
