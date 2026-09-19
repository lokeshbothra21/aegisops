"""Read-only incident endpoints (§7): list with status filter and cursor, detail."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aegisops_api.db import get_session
from aegisops_api.errors import ProblemError
from aegisops_api.models import Incident, IncidentStatus

router = APIRouter(prefix="/api/v1", tags=["incidents"])
Session = Annotated[AsyncSession, Depends(get_session)]


class IncidentOut(BaseModel):
    id: int
    opened_at: datetime
    closed_at: datetime | None
    service: str
    alert_rule_id: int | None
    status: IncidentStatus
    autonomy_level: int
    scenario_id: str | None
    summary: str | None

    model_config = {"from_attributes": True}


class IncidentPage(BaseModel):
    items: list[IncidentOut]
    next_cursor: int | None


@router.get("/incidents", response_model=IncidentPage)
async def list_incidents(
    session: Session,
    status_: Annotated[IncidentStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[int | None, Query(description="id of the last item seen")] = None,
) -> IncidentPage:
    stmt = select(Incident).order_by(Incident.id.desc()).limit(limit + 1)
    if status_ is not None:
        stmt = stmt.where(Incident.status == status_)
    if cursor is not None:
        stmt = stmt.where(Incident.id < cursor)
    rows = list((await session.scalars(stmt)).all())
    more = len(rows) > limit
    rows = rows[:limit]
    return IncidentPage(
        items=[IncidentOut.model_validate(r) for r in rows],
        next_cursor=rows[-1].id if more and rows else None,
    )


@router.get("/incidents/{incident_id}", response_model=IncidentOut)
async def get_incident(incident_id: int, session: Session) -> IncidentOut:
    incident = await session.get(Incident, incident_id)
    if incident is None:
        raise ProblemError(status.HTTP_404_NOT_FOUND, "Incident not found", f"id {incident_id}")
    return IncidentOut.model_validate(incident)
