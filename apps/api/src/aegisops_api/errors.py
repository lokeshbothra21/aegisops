"""RFC 7807 `application/problem+json` for every error the API returns (PROJECT.md §7)."""

from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import DBAPIError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_MEDIA_TYPE = "application/problem+json"


class Problem(BaseModel):
    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    errors: list[dict[str, Any]] | None = None


def problem_response(
    request: Request,
    status_code: int,
    title: str,
    detail: str | None = None,
    errors: list[dict[str, Any]] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = Problem(
        title=title,
        status=status_code,
        detail=detail,
        instance=str(request.url.path),
        errors=errors,
    )
    return JSONResponse(
        body.model_dump(exclude_none=True),
        status_code=status_code,
        media_type=PROBLEM_MEDIA_TYPE,
        headers=dict(headers) if headers else None,
    )


class ProblemError(Exception):
    """Raise from any layer to answer with a specific problem+json (thin routes)."""

    def __init__(self, status_code: int, title: str, detail: str | None = None) -> None:
        super().__init__(detail or title)
        self.status_code = status_code
        self.title = title
        self.detail = detail


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _problem(request: Request, exc: ProblemError) -> JSONResponse:
        return problem_response(request, exc.status_code, exc.title, exc.detail)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        title = exc.detail if isinstance(exc.detail, str) else "HTTP error"
        return problem_response(request, exc.status_code, title, headers=exc.headers)

    @app.exception_handler(OperationalError)
    @app.exception_handler(DBAPIError)
    @app.exception_handler(OSError)
    async def _db_unavailable(request: Request, exc: Exception) -> JSONResponse:
        # Connection refused / pool errors: the service is up but its dependency is not.
        return problem_response(
            request,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Database unavailable",
            type(exc).__name__,
            headers={"Retry-After": "10"},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(map(str, e["loc"])), "msg": e["msg"], "type": e["type"]}
            for e in exc.errors()
        ]
        return problem_response(
            request,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Validation failed",
            errors=errors,
        )
