"""OTLP/HTTP receivers (E1.1): the collector's `otlphttp` exporter posts here.

Contract (https://opentelemetry.io/docs/specs/otlp/#otlphttp):
- `POST /ingest/v1/{traces,logs,metrics}` with `Content-Type: application/json`
  (the collector needs `encoding: json`; binary protobuf answers 415).
- `Content-Encoding: gzip` is honoured (the exporter compresses by default).
- Success is 200 with an `Export*ServiceResponse`: `{}` or a `partialSuccess`
  block naming how many items were dropped and why.
- Client faults are 4xx problem+json and the collector will not retry them;
  5xx (DB down) makes it retry with backoff, so nothing is lost on a blip.

Handlers only decode the body and hand off; parsing lives in `ingest.otlp`,
mapping in `ingest.convert`, SQL in `ingest.store` (PROJECT.md §14.3).
"""

import gzip
import zlib
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Request, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from aegisops_api.db import get_session
from aegisops_api.errors import ProblemError
from aegisops_api.ingest import convert, otlp, store
from aegisops_api.ingest.convert import Row
from aegisops_api.settings import Settings

log = structlog.get_logger()
router = APIRouter(prefix="/ingest/v1", tags=["ingest"])


def export_response(counter: str, rejected: int, message: str) -> dict[str, Any]:
    """Export*ServiceResponse: `{}` on full success, else a `partialSuccess` block."""
    if rejected == 0:
        return {}
    return {"partialSuccess": {counter: rejected, "errorMessage": message}}


async def read_body(request: Request) -> bytes:
    settings: Settings = request.app.state.settings
    limit = settings.ingest_max_body_bytes
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/json":
        raise ProblemError(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Unsupported media type",
            "Send OTLP/JSON with Content-Type: application/json "
            "(collector otlphttp exporter: `encoding: json`).",
        )
    raw = await request.body()
    if len(raw) > limit:
        raise ProblemError(
            status.HTTP_413_CONTENT_TOO_LARGE, "Payload too large", f"limit is {limit} bytes"
        )
    encoding = request.headers.get("content-encoding", "").lower()
    if encoding in ("gzip", "x-gzip"):
        try:
            raw = gzip.decompress(raw)
        except (OSError, EOFError, zlib.error) as exc:
            raise ProblemError(status.HTTP_400_BAD_REQUEST, "Bad gzip body", str(exc)) from exc
        if len(raw) > limit:
            raise ProblemError(
                status.HTTP_413_CONTENT_TOO_LARGE,
                "Payload too large",
                f"decompressed size exceeds {limit} bytes",
            )
    elif encoding not in ("", "identity"):
        raise ProblemError(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Unsupported content encoding",
            f"{encoding!r}; use gzip or none",
        )
    return raw


def parse[T: otlp.OtlpModel](model: type[T], raw: bytes) -> T:
    try:
        return model.model_validate_json(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        raise ProblemError(
            status.HTTP_400_BAD_REQUEST, "Invalid OTLP/JSON", f"{loc}: {first['msg']}"
        ) from exc


async def _ingest[T: otlp.OtlpModel](
    request: Request,
    session: AsyncSession,
    model: type[T],
    to_rows: Callable[[T], tuple[list[Row], int]],
    insert: Callable[[AsyncSession, list[Row]], Awaitable[None]],
    signal: str,
    counter: str,
) -> dict[str, Any]:
    raw = await read_body(request)
    rows, rejected = to_rows(parse(model, raw))
    await insert(session, rows)
    log.info(f"ingest.{signal}", stored=len(rows), rejected=rejected, bytes=len(raw))
    return export_response(counter, rejected, "missing or malformed identifiers")


@router.post("/traces", status_code=status.HTTP_200_OK)
async def ingest_traces(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, Any]:
    return await _ingest(
        request,
        session,
        otlp.ExportTraceServiceRequest,
        convert.spans_to_rows,
        store.insert_spans,
        "traces",
        "rejectedSpans",
    )


@router.post("/logs", status_code=status.HTTP_200_OK)
async def ingest_logs(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, Any]:
    return await _ingest(
        request,
        session,
        otlp.ExportLogsServiceRequest,
        convert.logs_to_rows,
        store.insert_logs,
        "logs",
        "rejectedLogRecords",
    )


@router.post("/metrics", status_code=status.HTTP_200_OK)
async def ingest_metrics(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, Any]:
    return await _ingest(
        request,
        session,
        otlp.ExportMetricsServiceRequest,
        convert.metrics_to_rows,
        store.insert_metric_points,
        "metrics",
        "rejectedDataPoints",
    )
