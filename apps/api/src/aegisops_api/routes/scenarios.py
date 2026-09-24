"""Scenarios (§7): public catalogue for the replay demo, admin capture (E1.6)."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aegisops_api.db import get_session
from aegisops_api.errors import ProblemError
from aegisops_api.models import Scenario
from aegisops_api.routes.admin import require_admin
from aegisops_api.scenarios.capture import CaptureResult, capture_window

router = APIRouter(prefix="/api/v1", tags=["scenarios"])
Session = Annotated[AsyncSession, Depends(get_session)]


class ScenarioOut(BaseModel):
    key: str
    title: str
    fault_type: str
    expected_service: str | None
    expected_category: str | None
    expected_actions: list[str]
    window_start: datetime
    window_end: datetime
    notes: str | None

    model_config = {"from_attributes": True}


class CaptureRequest(BaseModel):
    key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    window_start: datetime
    window_end: datetime
    title: str = Field(max_length=200)
    fault_type: str = Field(max_length=64)
    expected_service: str | None = None
    expected_category: str | None = None
    expected_actions: list[str] = Field(default_factory=list)
    notes: str | None = None


class CaptureOut(BaseModel):
    key: str
    window_start: datetime
    window_end: datetime
    tagged: dict[str, int]


@router.get("/scenarios", response_model=list[ScenarioOut])
async def list_scenarios(session: Session) -> list[ScenarioOut]:
    rows = (await session.scalars(select(Scenario).order_by(Scenario.key))).all()
    return [ScenarioOut.model_validate(r) for r in rows]


@router.get("/scenarios/{key}", response_model=ScenarioOut)
async def get_scenario(key: str, session: Session) -> ScenarioOut:
    row = (await session.scalars(select(Scenario).where(Scenario.key == key))).first()
    if row is None:
        raise ProblemError(status.HTTP_404_NOT_FOUND, "Scenario not found", key)
    return ScenarioOut.model_validate(row)


@router.post("/admin/capture", response_model=CaptureOut, dependencies=[Depends(require_admin)])
async def capture(req: CaptureRequest, session: Session) -> CaptureOut:
    try:
        res: CaptureResult = await capture_window(
            session,
            key=req.key,
            start=req.window_start,
            end=req.window_end,
            title=req.title,
            fault_type=req.fault_type,
            expected_service=req.expected_service,
            expected_category=req.expected_category,
            expected_actions=req.expected_actions,
            notes=req.notes,
        )
    except ValueError as exc:
        raise ProblemError(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid window", str(exc)
        ) from exc
    return CaptureOut(
        key=res.key, window_start=res.window_start, window_end=res.window_end, tagged=res.tagged
    )
