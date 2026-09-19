"""Admin routes (🔒 X-Admin-Token, PROJECT.md §7): manual triggers for the periodic jobs.

`require_admin` is the only auth in v1. With no token configured the routes answer
503 rather than silently allowing, so a misconfigured deploy fails closed.
"""

from datetime import timedelta
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Header, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from aegisops_api.db import get_session
from aegisops_api.errors import ProblemError
from aegisops_api.jobs.retention import run_retention
from aegisops_api.jobs.service_edges import derive_recent_hours
from aegisops_api.settings import Settings

log = structlog.get_logger()
router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def require_admin(request: Request, x_admin_token: Annotated[str | None, Header()] = None) -> None:
    settings: Settings = request.app.state.settings
    expected = settings.admin_token.get_secret_value() if settings.admin_token else ""
    if not expected:
        raise ProblemError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Admin disabled",
            "AEGIS_ADMIN_TOKEN is not configured on this deployment.",
        )
    if x_admin_token != expected:
        raise ProblemError(
            status.HTTP_401_UNAUTHORIZED, "Unauthorized", "missing or wrong X-Admin-Token"
        )


Admin = Depends(require_admin)
Session = Annotated[AsyncSession, Depends(get_session)]


class RetentionResult(BaseModel):
    older_than_hours: float
    deleted: dict[str, int]


class EdgesResult(BaseModel):
    hours: int
    rows: int


@router.post("/retention/run", response_model=RetentionResult, dependencies=[Admin])
async def retention_run(request: Request, session: Session) -> RetentionResult:
    settings: Settings = request.app.state.settings
    older_than = timedelta(hours=settings.retention_hours)
    deleted = await run_retention(session, older_than=older_than)
    log.info("admin.retention", actor="admin", deleted=deleted)
    return RetentionResult(older_than_hours=settings.retention_hours, deleted=deleted)


@router.post("/service-edges/run", response_model=EdgesResult, dependencies=[Admin])
async def service_edges_run(session: Session, hours: int = 2) -> EdgesResult:
    hours = max(1, min(hours, 48))
    rows = await derive_recent_hours(session, hours=hours)
    log.info("admin.service_edges", actor="admin", hours=hours, rows=rows)
    return EdgesResult(hours=hours, rows=rows)
