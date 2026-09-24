"""Tag every live row in [start, end) with `scenario_id` and upsert the `scenarios` row.

Idempotent: re-capturing the same key first clears the old tag from rows that carry it,
then tags the new window. Rows already tagged with ANOTHER scenario are left alone
(windows must not overlap; the runner enforces it).
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from aegisops_api.jobs.service_edges import derive_service_edges
from aegisops_api.models import Scenario

TAGGED_TABLES: dict[str, str] = {  # table -> timestamp column
    "spans": "start_ts",
    "logs": "ts",
    "metric_points": "ts",
    "change_events": "ts",
    "service_edges": "window_start",
    "incidents": "opened_at",
}


@dataclass
class CaptureResult:
    key: str
    window_start: datetime
    window_end: datetime
    tagged: dict[str, int] = field(default_factory=dict)


async def capture_window(
    session: AsyncSession,
    *,
    key: str,
    start: datetime,
    end: datetime,
    title: str,
    fault_type: str,
    expected_service: str | None = None,
    expected_category: str | None = None,
    expected_actions: list[str] | None = None,
    notes: str | None = None,
) -> CaptureResult:
    if end <= start:
        raise ValueError("window_end must be after window_start")
    result = CaptureResult(key=key, window_start=start, window_end=end)
    # the 15-min edges job may not have covered the window yet: derive it now so replay has
    # a dependency graph (live rows only; they are tagged just below)
    hour = start.replace(minute=0, second=0, microsecond=0)
    result.tagged["service_edges_derived"] = await derive_service_edges(
        session, start=hour, end=end
    )
    for table, col in TAGGED_TABLES.items():
        # release rows a previous capture of this key tagged, then tag the new window
        await session.execute(
            text(f"UPDATE {table} SET scenario_id = NULL WHERE scenario_id = :key"),  # noqa: S608 - table names are ours
            {"key": key},
        )
        # service_edges windows are hour-labelled: include the hour containing `start`
        r = await session.execute(
            text(
                f"UPDATE {table} SET scenario_id = :key "  # noqa: S608
                f"WHERE scenario_id IS NULL AND {col} >= :start AND {col} < :end"
            ),
            {
                "key": key,
                "start": start
                if table != "service_edges"
                else start.replace(minute=0, second=0, microsecond=0),
                "end": end,
            },
        )
        result.tagged[table] = int(getattr(r, "rowcount", 0) or 0)
    existing = await session.scalar(text("SELECT id FROM scenarios WHERE key = :key"), {"key": key})
    values: dict[str, Any] = {
        "title": title,
        "fault_type": fault_type,
        "expected_service": expected_service,
        "expected_category": expected_category,
        "expected_actions": expected_actions or [],
        "window_start": start,
        "window_end": end,
        "notes": notes,
    }
    if existing:
        row = await session.get(Scenario, int(existing))
        assert row is not None
        for k, v in values.items():
            setattr(row, k, v)
    else:
        session.add(Scenario(key=key, **values))
    await session.flush()
    return result
