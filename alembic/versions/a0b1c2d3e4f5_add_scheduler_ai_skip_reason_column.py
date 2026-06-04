"""add scheduler ai skip reason column.

Revision ID: a0b1c2d3e4f5
Revises: f9a0b1c2d3e4
Create Date: 2026-05-18 00:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a0b1c2d3e4f5"
down_revision: str | Sequence[str] | None = "f9a0b1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AI_SKIP_REASON_EXPR = (
    "coalesce("
    "nullif(outcome->>'last_ai_skip_reason', ''), "
    "nullif(outcome->'ai_call_policy'->>'reason', ''), "
    "''"
    ")"
)


def upgrade() -> None:
    op.add_column("scheduler_runs", sa.Column("ai_skip_reason", sa.String(length=160), nullable=True))
    op.execute(
        "UPDATE scheduler_runs "
        f"SET ai_skip_reason = nullif({_AI_SKIP_REASON_EXPR}, '') "
        "WHERE workflow = 'interval_decision_cycle' "
        "AND ai_skip_reason IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_scheduler_runs_interval_ai_skip_reason_scalar_created_at "
        "ON scheduler_runs (created_at, ai_skip_reason) "
        "WHERE workflow = 'interval_decision_cycle' "
        "AND ai_skip_reason IS NOT NULL"
    )
    op.execute("ANALYZE scheduler_runs")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_scheduler_runs_interval_ai_skip_reason_scalar_created_at")
    op.drop_column("scheduler_runs", "ai_skip_reason")
