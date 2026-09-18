"""Liveness and readiness probes (E8.4).

- /livez answers as soon as the process is up (liveness). It is NOT /healthz on
  purpose: Google Frontend reserves that exact path on *.run.app domains and answers
  it with its own 404 before the request reaches the container (found 18 Sep 2026).
- /readyz answers 200 only when dependencies are usable (today: Postgres answers
  SELECT 1), otherwise 503. A failing /readyz keeps a broken revision from
  receiving traffic (PROJECT.md §15).
"""

from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from aegisops_api import __version__
from aegisops_api.db import ping

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str


class ReadyResponse(BaseModel):
    status: Literal["ready", "degraded"]
    checks: dict[str, bool]


@router.get("/livez", response_model=HealthResponse)
async def livez() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@router.get(
    "/readyz",
    response_model=ReadyResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadyResponse}},
)
async def readyz(request: Request, response: Response) -> ReadyResponse:
    checks = {"database": await ping(request.app.state.engine)}
    ready = all(checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyResponse(status="ready" if ready else "degraded", checks=checks)
