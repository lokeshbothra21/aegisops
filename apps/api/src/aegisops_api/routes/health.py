"""Liveness and readiness probes (E8.4).

- /healthz answers as soon as the process is up. Cloud Run uses it to decide the
  container is alive.
- /readyz answers only when dependencies are usable. Today that is a stub; on
  Day 2 it pings the database. A failing /readyz keeps a broken revision from
  receiving traffic (see PROJECT.md §15).
"""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from aegisops_api import __version__

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str


class ReadyResponse(BaseModel):
    status: Literal["ready", "degraded"]
    checks: dict[str, bool]


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@router.get("/readyz", response_model=ReadyResponse)
async def readyz() -> ReadyResponse:
    checks = {"database": True}  # Day 2: real SELECT 1 against Postgres
    status: Literal["ready", "degraded"] = "ready" if all(checks.values()) else "degraded"
    return ReadyResponse(status=status, checks=checks)
