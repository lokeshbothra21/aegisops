"""Incident state machine (PROJECT.md §5.4, E2.4).

open → investigating → awaiting_approval → remediating → resolved | failed

`awaiting_approval` may skip to `resolved` (nothing to do) or `failed` (rejected +
nothing else possible); `investigating` may go straight to `failed` (budget
exceeded with no usable root cause). Every other jump is a bug and raises.
Terminal states set `closed_at`.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aegisops_api.models import Incident, IncidentStatus

S = IncidentStatus
ALLOWED: dict[IncidentStatus, frozenset[IncidentStatus]] = {
    S.open: frozenset({S.investigating, S.resolved, S.failed}),
    S.investigating: frozenset({S.awaiting_approval, S.resolved, S.failed}),
    S.awaiting_approval: frozenset({S.remediating, S.resolved, S.failed}),
    S.remediating: frozenset({S.resolved, S.failed}),
    S.resolved: frozenset(),
    S.failed: frozenset(),
}
TERMINAL = frozenset({S.resolved, S.failed})


class IllegalTransitionError(Exception):
    def __init__(self, current: IncidentStatus, target: IncidentStatus) -> None:
        super().__init__(f"incident cannot go from {current} to {target}")
        self.current, self.target = current, target


def transition(incident: Incident, to: IncidentStatus, *, now: datetime | None = None) -> Incident:
    """Mutate `incident` in place if the move is legal; raise IllegalTransitionError otherwise."""
    if to not in ALLOWED[incident.status]:
        raise IllegalTransitionError(incident.status, to)
    incident.status = to
    if to in TERMINAL:
        incident.closed_at = now or datetime.now(UTC)
    return incident


async def open_incident(
    session: AsyncSession,
    *,
    service: str,
    alert_rule_id: int | None,
    summary: str,
    scenario_id: str | None = None,
    autonomy_level: int = 1,
    now: datetime | None = None,
) -> Incident:
    incident = Incident(
        opened_at=now or datetime.now(UTC),
        service=service,
        alert_rule_id=alert_rule_id,
        status=S.open,
        autonomy_level=autonomy_level,
        scenario_id=scenario_id,
        summary=summary,
    )
    session.add(incident)
    await session.flush()
    return incident


async def active_incident(
    session: AsyncSession, *, service: str, scenario_id: str | None = None
) -> Incident | None:
    """The non-terminal incident for a service, if any (the evaluator must not open duplicates)."""
    stmt = (
        select(Incident)
        .where(
            Incident.service == service,
            Incident.status.not_in(list(TERMINAL)),
            Incident.scenario_id.is_(None)
            if scenario_id is None
            else Incident.scenario_id == scenario_id,
        )
        .order_by(Incident.opened_at.desc())
        .limit(1)
    )
    return (await session.scalars(stmt)).first()
