"""Captured scenarios (E1.6, PROJECT.md §6 `scenarios`): one row per replayable window.

Telemetry, change events, service edges and incidents inside [window_start, window_end)
carry `scenario_id = key`; retention never touches tagged rows. Expected values come
from bench/scenarios.yaml so the benchmark can score a replay.
"""

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from aegisops_api.models.base import Base, TimestampedRow


class Scenario(TimestampedRow, Base):
    __tablename__ = "scenarios"

    key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    fault_type: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_service: Mapped[str | None] = mapped_column(String(128))
    expected_category: Mapped[str | None] = mapped_column(String(32))
    expected_actions: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False, default=list
    )
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
