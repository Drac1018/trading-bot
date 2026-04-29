"""add audit and scheduler created-at indexes.

Revision ID: a8d4e6f2b9c1
Revises: f1a2b3c4d5e6
Create Date: 2026-04-29 00:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a8d4e6f2b9c1"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_scheduler_runs_created_at", "scheduler_runs", ["created_at"], unique=False)
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"], unique=False)
    op.create_index(
        "ix_audit_events_event_type_created_at",
        "audit_events",
        ["event_type", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_audit_events_event_type_created_at", table_name="audit_events")
    op.drop_index("ix_audit_events_created_at", table_name="audit_events")
    op.drop_index("ix_scheduler_runs_created_at", table_name="scheduler_runs")
