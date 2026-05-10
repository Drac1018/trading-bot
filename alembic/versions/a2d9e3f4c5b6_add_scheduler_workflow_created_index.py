"""add scheduler workflow created index.

Revision ID: a2d9e3f4c5b6
Revises: c3d4e5f6a7b8
Create Date: 2026-05-07 00:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a2d9e3f4c5b6"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_scheduler_runs_workflow_created_at",
        "scheduler_runs",
        ["workflow", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_scheduler_runs_workflow_created_at", table_name="scheduler_runs")
