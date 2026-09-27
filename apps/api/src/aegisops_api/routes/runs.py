"""Runs (§7): start, public scenario replay, summary, SSE event stream, approve / reject (🔒).

Starting a run goes through admission (E9.4, `runs/limits.py`): visitors on the public
deployment are limited, and past the daily cap they are served the last finished run.
`served` says which: `new` (202), `joined` (someone's live run, 200) or `cached` (200).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from aegisops_api.db import get_session
from aegisops_api.errors import ProblemError
from aegisops_api.models import (
    Decision,
    Incident,
    Remediation,
    Run,
    RunEvent,
    RunStatus,
    Scenario,
)
from aegisops_api.routes.admin import require_admin
from aegisops_api.runs.limits import STALE_AFTER, Admission, admit
from aegisops_api.runs.service import RunManager
from aegisops_api.settings import Settings

router = APIRouter(prefix="/api/v1", tags=["runs"])
Session = Annotated[AsyncSession, Depends(get_session)]


def manager(request: Request) -> RunManager:
    m = getattr(request.app.state, "runs", None)
    if m is None:
        raise ProblemError(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Agent disabled", "AEGIS_AGENT_ENABLED is false"
        )
    return m  # type: ignore[no-any-return]


class StartRun(BaseModel):
    model: str = Field(default="", max_length=64)
    variant: str = Field(default="D", max_length=16)


class RemediationOut(BaseModel):
    id: int
    action: str
    params: dict[str, Any]
    risk: str
    confidence: float
    rationale: str | None
    decision: str
    decided_by: str | None
    decided_at: datetime | None
    executed_at: datetime | None = None
    outcome: dict[str, Any] | None = None

    model_config = {"from_attributes": True}


class RunOut(BaseModel):
    id: int
    incident_id: int
    thread_id: str
    status: RunStatus
    model: str
    variant: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    tool_calls: int
    duration_ms: int | None
    started_at: datetime
    finished_at: datetime | None
    root_cause: dict[str, Any] | None
    error: str | None
    remediation: RemediationOut | None = None
    served: Literal["new", "joined", "cached"] = "new"

    model_config = {"from_attributes": True}


class DecisionIn(BaseModel):
    by: str = Field(default="admin", max_length=64)
    note: str = Field(default="", max_length=400)


async def _run_out(
    session: AsyncSession, run: Run, served: Literal["new", "joined", "cached"] = "new"
) -> RunOut:
    rem = (
        await session.scalars(
            select(Remediation).where(Remediation.run_id == run.id).order_by(Remediation.id.desc())
        )
    ).first()
    out = RunOut.model_validate(run)
    out.remediation = RemediationOut.model_validate(rem) if rem else None
    out.served = served
    return out


async def _active_run(session: AsyncSession, incident_id: int) -> Run | None:
    """Paused for approval, or running and recent (a stalled run older than STALE_AFTER
    no longer blocks the incident)."""
    return (
        await session.scalars(
            select(Run).where(
                Run.incident_id == incident_id,
                or_(
                    Run.status == RunStatus.awaiting_approval,
                    and_(
                        Run.status == RunStatus.running,
                        Run.started_at > datetime.now(UTC) - STALE_AFTER,
                    ),
                ),
            )
        )
    ).first()


async def _admitted_run(
    session: AsyncSession, request: Request, response: Response, incident: Incident, body: StartRun
) -> RunOut:
    settings: Settings = request.app.state.settings
    admission: Admission = await admit(session, request, settings, incident)
    if admission.cached is not None:
        response.status_code = status.HTTP_200_OK
        return await _run_out(session, admission.cached, "cached")
    run = await manager(request).start(
        session,
        incident,
        model=body.model,
        variant=body.variant,
        requested_by=admission.requested_by,
        proposal_only=admission.public,
    )
    return await _run_out(session, run)


@router.post(
    "/incidents/{incident_id}/runs", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED
)
async def start_run(
    incident_id: int, body: StartRun, request: Request, response: Response, session: Session
) -> RunOut:
    incident = await session.get(Incident, incident_id)
    if incident is None:
        raise ProblemError(status.HTTP_404_NOT_FOUND, "Incident not found", str(incident_id))
    active = await _active_run(session, incident_id)
    if active is not None:
        raise ProblemError(
            status.HTTP_409_CONFLICT,
            "Run already active",
            f"run {active.id} is {active.status.value}",
        )
    return await _admitted_run(session, request, response, incident, body)


@router.post("/scenarios/{key}/replay", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED)
async def replay_scenario(
    key: str, request: Request, response: Response, session: Session
) -> RunOut:
    """The public demo button: investigate a captured scenario's incident. If a run on it is
    already live, join that one (free, and everyone watches the same stream)."""
    scenario = (await session.scalars(select(Scenario).where(Scenario.key == key))).first()
    if scenario is None:
        raise ProblemError(status.HTTP_404_NOT_FOUND, "Scenario not found", key)
    incident = (
        await session.scalars(
            select(Incident)
            .where(Incident.scenario_id == key)
            .order_by(
                (Incident.service == (scenario.expected_service or "")).desc(),
                Incident.opened_at,
                Incident.id,
            )
            .limit(1)
        )
    ).first()
    if incident is None:
        raise ProblemError(
            status.HTTP_404_NOT_FOUND, "Scenario has no incident", f"{key} captured no incident"
        )
    active = await _active_run(session, incident.id)
    if active is not None:
        response.status_code = status.HTTP_200_OK
        return await _run_out(session, active, "joined")
    return await _admitted_run(session, request, response, incident, StartRun())


@router.get("/runs/{run_id}", response_model=RunOut)
async def get_run(run_id: int, session: Session) -> RunOut:
    run = await session.get(Run, run_id)
    if run is None:
        raise ProblemError(status.HTTP_404_NOT_FOUND, "Run not found", str(run_id))
    return await _run_out(session, run)


@router.get("/runs/{run_id}/events")
async def run_events(
    run_id: int, request: Request, session: Session, after: Annotated[int, Query(ge=0)] = 0
) -> EventSourceResponse:
    """SSE: replay stored events with seq > `after`, then live ones until the run ends.
    Event names are the node names; `run`/`end` closes the stream."""
    run = await session.get(Run, run_id)
    if run is None:
        raise ProblemError(status.HTTP_404_NOT_FOUND, "Run not found", str(run_id))
    stored = (
        await session.scalars(
            select(RunEvent)
            .where(RunEvent.run_id == run_id, RunEvent.seq > after)
            .order_by(RunEvent.seq)
        )
    ).all()
    mgr = manager(request)

    async def gen() -> AsyncIterator[dict[str, Any]]:
        last = after
        for ev in stored:
            last = ev.seq
            yield {
                "id": str(ev.seq),
                "event": ev.node,
                "data": json.dumps(
                    {
                        "seq": ev.seq,
                        "node": ev.node,
                        "type": ev.type,
                        "payload": ev.payload,
                        "ts": ev.ts.isoformat(),
                    },
                    default=str,
                ),
            }
        if run.status in (RunStatus.running,):
            async for live in mgr.subscribe(run_id):
                if live["seq"] <= last:
                    continue
                yield {
                    "id": str(live["seq"]),
                    "event": live["node"],
                    "data": json.dumps(live, default=str),
                }

    return EventSourceResponse(gen())


@router.post("/runs/{run_id}/approve", response_model=RunOut, dependencies=[Depends(require_admin)])
async def approve(run_id: int, body: DecisionIn, request: Request, session: Session) -> RunOut:
    return await _decide(run_id, Decision.approved, body, request, session)


@router.post("/runs/{run_id}/reject", response_model=RunOut, dependencies=[Depends(require_admin)])
async def reject(run_id: int, body: DecisionIn, request: Request, session: Session) -> RunOut:
    return await _decide(run_id, Decision.rejected, body, request, session)


async def _decide(
    run_id: int, decision: Decision, body: DecisionIn, request: Request, session: AsyncSession
) -> RunOut:
    run = await session.get(Run, run_id)
    if run is None:
        raise ProblemError(status.HTTP_404_NOT_FOUND, "Run not found", str(run_id))
    try:
        await manager(request).decide(session, run, decision, body.by, body.note)
    except ValueError as exc:
        raise ProblemError(status.HTTP_409_CONFLICT, "Not awaiting approval", str(exc)) from exc
    return await _run_out(session, run)
