"""Change events, alert rules and incidents (PROJECT.md §6, E2.4).

Enums are stored as VARCHAR + CHECK (`native_enum=False`) so adding a value is an
ordinary column change, not a Postgres type migration.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from aegisops_api.models.base import Base, TimestampedRow


class ChangeType(StrEnum):
    deploy = "deploy"
    flag = "flag"
    scale = "scale"
    restart = "restart"
    commit = "commit"


class IncidentStatus(StrEnum):
    open = "open"
    investigating = "investigating"
    awaiting_approval = "awaiting_approval"
    remediating = "remediating"
    resolved = "resolved"
    failed = "failed"


class Comparator(StrEnum):
    gt = ">"
    gte = ">="
    lt = "<"
    lte = "<="


class ChangeEvent(TimestampedRow, Base):
    """Something changed in the target system: a flag flip, a deploy, a restart, a scale."""

    __tablename__ = "change_events"

    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    type: Mapped[ChangeType] = mapped_column(
        Enum(ChangeType, native_enum=False, length=16), nullable=False
    )
    service: Mapped[str | None] = mapped_column(String(128))
    before: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    after: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    scenario_id: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_change_events_ts", "ts"),
        Index("ix_change_events_scenario", "scenario_id"),
    )


class AlertRule(TimestampedRow, Base):
    """`metric comparator threshold` over `window_s`, for `for_windows` consecutive windows."""

    __tablename__ = "alert_rules"

    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    service: Mapped[str | None] = mapped_column(String(128))  # NULL = every service
    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    comparator: Mapped[Comparator] = mapped_column(
        # store the symbol (">=") not the member name ("gte")
        Enum(
            Comparator, native_enum=False, length=2, values_callable=lambda e: [m.value for m in e]
        ),
        nullable=False,
    )
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    window_s: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    for_windows: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Incident(TimestampedRow, Base):
    __tablename__ = "incidents"

    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    service: Mapped[str] = mapped_column(String(128), nullable=False)
    alert_rule_id: Mapped[int | None] = mapped_column(ForeignKey("alert_rules.id"))
    status: Mapped[IncidentStatus] = mapped_column(
        Enum(IncidentStatus, native_enum=False, length=24),
        nullable=False,
        default=IncidentStatus.open,
    )
    autonomy_level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    scenario_id: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_incidents_status", "status"),
        Index("ix_incidents_opened_at", "opened_at"),  # queried with ORDER BY opened_at DESC
    )
