"""runs.requested_by for public admission limits (E9.4)

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-27 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("requested_by", sa.String(length=64), nullable=True))
    op.create_index("ix_runs_requested_started", "runs", ["requested_by", "started_at"])


def downgrade() -> None:
    op.drop_index("ix_runs_requested_started", table_name="runs")
    op.drop_column("runs", "requested_by")
