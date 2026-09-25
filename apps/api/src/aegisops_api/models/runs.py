"""Runs, their event stream and remediation proposals (PROJECT.md §6, E5.2).

A run is one investigation of one incident under one LangGraph thread. `run_events` is
the append-only stream the UI replays over SSE; `remediations` holds the proposal the
`approval` interrupt paused on and the human (or policy) decision about it.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from aegisops_api.models.base import Base, TimestampedRow


class RunStatus(StrEnum):
    running = "running"
    awaiting_approval = "awaiting_approval"
    succeeded = "succeeded"
    failed = "failed"
    budget_exceeded = "budget_exceeded"


class ActionKind(StrEnum):
    toggle_flag = "toggle_flag"
    restart_service = "restart_service"
    scale_service = "scale_service"
    rollback_deployment = "rollback_deployment"
    none = "none"


class Risk(StrEnum):
    low = "low"
    medium = "medium"


class Decision(StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    auto = "auto"


class Run(TimestampedRow, Base):
    __tablename__ = "runs"

    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    model: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    variant: Mapped[str] = mapped_column(String(16), nullable=False, default="D")
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, native_enum=False, length=24), nullable=False, default=RunStatus.running
    )
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    langfuse_trace_id: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    root_cause: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_runs_incident", "incident_id"),)


class RunEvent(TimestampedRow, Base):
    __tablename__ = "run_events"

    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    node: Mapped[str] = mapped_column(String(32), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_run_events_run_seq", "run_id", "seq", unique=True),)


class Remediation(TimestampedRow, Base):
    __tablename__ = "remediations"

    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), nullable=False)
    action: Mapped[ActionKind] = mapped_column(
        Enum(ActionKind, native_enum=False, length=24), nullable=False
    )
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    risk: Mapped[Risk] = mapped_column(Enum(Risk, native_enum=False, length=8), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    decision: Mapped[Decision] = mapped_column(
        Enum(Decision, native_enum=False, length=16), nullable=False, default=Decision.pending
    )
    decided_by: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (Index("ix_remediations_run", "run_id"),)
