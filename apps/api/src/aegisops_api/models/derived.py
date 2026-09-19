"""Derived tables computed from raw telemetry by periodic jobs.

`service_edges` (PROJECT.md §6): one row per (hour, caller, callee). Built by
`jobs/service_edges.py` from parent→child span pairs that cross a service
boundary. The agent's `get_service_dependencies` tool reads this instead of
scanning spans, so "who calls whom, how often, how badly" is one indexed query.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from aegisops_api.models.base import Base, TimestampedRow


class ServiceEdge(TimestampedRow, Base):
    __tablename__ = "service_edges"

    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    caller: Mapped[str] = mapped_column(String(128), nullable=False)
    callee: Mapped[str] = mapped_column(String(128), nullable=False)
    call_count: Mapped[int] = mapped_column(Integer, nullable=False)
    err_count: Mapped[int] = mapped_column(Integer, nullable=False)
    p95_ms: Mapped[float] = mapped_column(Float, nullable=False)
    scenario_id: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_service_edges_window_caller", "window_start", "caller"),
        Index("ix_service_edges_scenario_callee", "scenario_id", "callee"),
    )
