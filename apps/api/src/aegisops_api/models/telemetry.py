"""Raw telemetry tables: one row per span, log record, or metric data point.

Design notes (PROJECT.md §6):
- `service` is denormalised onto every row so the hot queries ("errors for
  checkout in the last 5 minutes") never join.
- `attrs` keeps every OTLP attribute as JSONB so nothing is lost at ingest.
- `scenario_id` is NULL for live traffic and set when a window is captured for
  replay/benchmark; rows with NULL are deleted by the 24 h retention job.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, Index, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from aegisops_api.models.base import Base, TimestampedRow


class Span(TimestampedRow, Base):
    __tablename__ = "spans"

    trace_id: Mapped[str] = mapped_column(String(32), nullable=False)
    span_id: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_span_id: Mapped[str | None] = mapped_column(String(16))
    service: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="INTERNAL")
    start_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False)
    status_code: Mapped[str] = mapped_column(String(8), nullable=False, default="UNSET")
    status_message: Mapped[str | None] = mapped_column(Text)
    attrs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    scenario_id: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_spans_service_start", "service", "start_ts"),
        Index("ix_spans_trace", "trace_id"),
        Index("ix_spans_scenario_service_status", "scenario_id", "service", "status_code"),
    )


class Log(TimestampedRow, Base):
    __tablename__ = "logs"

    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    service: Mapped[str] = mapped_column(String(128), nullable=False)
    severity_num: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    severity_text: Mapped[str | None] = mapped_column(String(16))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(32))
    span_id: Mapped[str | None] = mapped_column(String(16))
    attrs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    scenario_id: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_logs_service_ts", "service", "ts"),
        Index("ix_logs_scenario_service_sev", "scenario_id", "service", "severity_num"),
        Index("ix_logs_trace", "trace_id"),
    )


class MetricPoint(TimestampedRow, Base):
    __tablename__ = "metric_points"

    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    service: Mapped[str] = mapped_column(String(128), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(256), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str | None] = mapped_column(String(32))
    attrs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    scenario_id: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (Index("ix_metrics_service_name_ts", "service", "metric_name", "ts"),)
