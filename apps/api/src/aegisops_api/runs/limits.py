"""Public run admission (E9.4, PROJECT.md §11 "quota exhaustion / cost bomb").

Every run costs model quota, and on the public deployment anyone may start one. Before a
run starts, `admit` answers one of:

  admin   valid X-Admin-Token: no limits
  local   not public mode (laptop, CI): no limits
  public  admitted as a visitor: one running run per visitor, a global daily cap

and on the cap it serves the incident's last finished run instead (`cached`), so the demo
still shows a full investigation when the day's quota is spent.

Counts live in Postgres, not memory: Cloud Run may run several instances, each with its own
memory, and every one of them must see the same count. A transaction-scoped advisory lock
makes check-then-insert atomic across instances, so two visitors cannot both take the last
slot.

Visitors are identified by an HMAC of their IP, never the IP itself: the key is secret, so
the stored value cannot be reversed by hashing all 4 billion IPv4 addresses.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Request, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from aegisops_api.errors import ProblemError
from aegisops_api.models import Incident, Run, RunStatus
from aegisops_api.settings import Settings

ADMIN = "admin"
LOCAL = "local"
PUBLIC_PREFIX = "public:"
# A run is `running` for at most the 180 s budget. On Cloud Run the CPU is throttled once no
# request is open, so a run nobody watches can stall; after this long it no longer counts.
STALE_AFTER = timedelta(minutes=10)
LOCK_KEY = 0x4145_4749_5334  # "AEGIS4": one advisory lock for all admission decisions
FINISHED = (RunStatus.succeeded, RunStatus.budget_exceeded)


@dataclass(frozen=True)
class Admission:
    requested_by: str
    cached: Run | None = None  # set when the cap is reached and a finished run exists

    @property
    def public(self) -> bool:
        return self.requested_by.startswith(PUBLIC_PREFIX)


def is_admin(request: Request, settings: Settings) -> bool:
    expected = settings.admin_token.get_secret_value() if settings.admin_token else ""
    given = request.headers.get("x-admin-token") or ""
    return bool(expected) and hmac.compare_digest(given, expected)


def client_ip(request: Request, settings: Settings) -> str:
    """The caller's IP. Behind Cloud Run the socket peer is Google's front end, and the real
    client is the entry Google appends to X-Forwarded-For: the right-most one. Entries to its
    left came from the client and can be forged, so they are ignored."""
    if settings.trust_forwarded_for:
        xff = request.headers.get("x-forwarded-for", "")
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return request.client.host if request.client else "unknown"


def visitor_key(ip: str, settings: Settings) -> str:
    secret = settings.admin_token.get_secret_value() if settings.admin_token else settings.env
    digest = hmac.new(secret.encode(), ip.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{PUBLIC_PREFIX}{digest}"


def utc_midnight(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def is_live(run: Run, now: datetime) -> bool:
    """Running and recent enough to still be making progress."""
    return run.status is RunStatus.running and now - run.started_at < STALE_AFTER


async def last_finished(session: AsyncSession, incident_id: int) -> Run | None:
    return (
        await session.scalars(
            select(Run)
            .where(Run.incident_id == incident_id, Run.status.in_(FINISHED))
            .order_by(Run.finished_at.desc().nulls_last(), Run.id.desc())
            .limit(1)
        )
    ).first()


async def admit(
    session: AsyncSession,
    request: Request,
    settings: Settings,
    incident: Incident,
    *,
    now: datetime | None = None,
) -> Admission:
    if is_admin(request, settings):
        return Admission(ADMIN)
    if not settings.public_mode:
        return Admission(LOCAL)
    now = now or datetime.now(UTC)
    key = visitor_key(client_ip(request, settings), settings)
    # held until this request's transaction commits (after the run row is inserted)
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": LOCK_KEY})

    mine = (
        await session.scalars(
            select(Run).where(
                Run.requested_by == key,
                Run.status == RunStatus.running,
                Run.started_at > now - STALE_AFTER,
            )
        )
    ).first()
    if mine is not None:
        raise ProblemError(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "One run at a time",
            f"run {mine.id} is still running; watch it at /api/v1/runs/{mine.id}/events",
            headers={"Retry-After": "30"},
        )

    today = await session.scalar(
        select(func.count())
        .select_from(Run)
        .where(Run.requested_by.like(f"{PUBLIC_PREFIX}%"), Run.started_at >= utc_midnight(now))
    )
    if int(today or 0) >= settings.public_daily_run_cap:
        cached = await last_finished(session, incident.id)
        if cached is not None:
            return Admission(key, cached=cached)
        reset = utc_midnight(now) + timedelta(days=1)
        raise ProblemError(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Daily run limit reached",
            f"the public demo allows {settings.public_daily_run_cap} new runs per day "
            "and this incident has no finished run to show; try again after 00:00 UTC",
            headers={"Retry-After": str(int((reset - now).total_seconds()))},
        )
    return Admission(key)
