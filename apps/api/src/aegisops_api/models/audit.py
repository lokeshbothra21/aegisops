"""Audit trail (E9.6, PROJECT.md §6/§11): every tool call, approval and action attempt,
with the actor (`agent`, `admin:<name>`, `policy:auto`). Append-only by convention."""

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from aegisops_api.models.base import Base, TimestampedRow


class AuditLog(TimestampedRow, Base):
    __tablename__ = "audit_log"

    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"))
    node: Mapped[str] = mapped_column(String(32), nullable=False)
    tool: Mapped[str] = mapped_column(String(64), nullable=False)
    args: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (Index("ix_audit_log_run", "run_id"),)
