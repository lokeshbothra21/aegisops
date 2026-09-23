"""Change correlation (E3.4): deterministic scoring of change events around the alert.

score = recency x proximity
  recency   1.0 at the alert, linearly to 0 at 30 min before; changes AFTER the alert
            get 0.3 x recency (they cannot have caused it but may be a reaction)
  proximity 1.0 on the triaged service, 0.7 on a direct callee/caller, 0.3 elsewhere

No model call: the scored list goes straight into the root_cause prompt so the model sees
"what changed, how recently, how close" as data. (The plan allowed one summarising call;
skipped: the structured list is already short and the model reads it directly.)
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from aegisops_agent.schemas import ChangeCorrelation, ScoredChange
from aegisops_tools.context import ToolContext

WINDOW_MINUTES = 30


async def neighbours(session: AsyncSession, ctx: ToolContext, service: str) -> set[str]:
    start = ctx.now - timedelta(hours=3)
    rows = (
        await session.execute(
            text(
                """
                SELECT caller, callee FROM service_edges
                WHERE (caller = :s OR callee = :s) AND window_start >= :start
                  AND scenario_id IS NOT DISTINCT FROM :sc
                """
            ),
            {"s": service, "start": start, "sc": ctx.scenario_id},
        )
    ).all()
    out: set[str] = set()
    for caller, callee in rows:
        out.update({caller, callee})
    out.discard(service)
    return out


def _summary(typ: str, before: dict[str, Any], after: dict[str, Any]) -> str:
    if typ == "flag":
        flag = after.get("flag", before.get("flag", "?"))
        return f"{flag}: {before.get('variant', '?')} -> {after.get('variant', '?')}"
    if typ == "deploy":
        return f"image {before.get('image', '?')} -> {after.get('image', '?')}"
    if typ == "scale":
        return f"replicas {before.get('replicas', '?')} -> {after.get('replicas', '?')}"
    if typ == "restart":
        return (
            f"restart_count {before.get('restart_count', '?')} -> {after.get('restart_count', '?')}"
        )
    return typ


async def correlate_changes(
    session: AsyncSession, ctx: ToolContext, service: str, alert_at: datetime | None = None
) -> ChangeCorrelation:
    alert_at = alert_at or ctx.now
    near = await neighbours(session, ctx, service)
    rows = (
        await session.execute(
            text(
                """
                SELECT ts, type, service, before, after FROM change_events
                WHERE ts >= :start AND ts < :end AND scenario_id IS NOT DISTINCT FROM :sc
                ORDER BY ts DESC LIMIT 50
                """
            ),
            {
                "start": alert_at - timedelta(minutes=WINDOW_MINUTES),
                "end": alert_at + timedelta(minutes=WINDOW_MINUTES),
                "sc": ctx.scenario_id,
            },
        )
    ).all()
    events: list[ScoredChange] = []
    for ts, typ, svc, before, after in rows:
        before_d = before if isinstance(before, dict) else {}
        after_d = after if isinstance(after, dict) else {}
        minutes_before = (alert_at - ts).total_seconds() / 60
        if minutes_before >= 0:
            recency = max(0.0, 1 - minutes_before / WINDOW_MINUTES)
        else:
            recency = 0.3 * max(0.0, 1 + minutes_before / WINDOW_MINUTES)
        proximity = 1.0 if svc == service else 0.7 if svc in near else 0.3
        events.append(
            ScoredChange(
                ts=ts.isoformat(timespec="seconds"),
                type=str(typ),
                service=svc,
                summary=_summary(str(typ), before_d, after_d)[:200],
                minutes_before_alert=round(minutes_before, 1),
                score=round(recency * proximity, 3),
            )
        )
    events.sort(key=lambda e: -e.score)
    return ChangeCorrelation(
        alert_at=alert_at.isoformat(timespec="seconds"),
        events=events[:10],
        temporal_score=events[0].score if events else 0.0,
    )
