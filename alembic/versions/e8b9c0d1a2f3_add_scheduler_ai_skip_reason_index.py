"""add scheduler ai skip reason index.

Revision ID: e8b9c0d1a2f3
Revises: e4f6a8c9d0b1
Create Date: 2026-05-17 19:12:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e8b9c0d1a2f3"
down_revision: str | Sequence[str] | None = "e4f6a8c9d0b1"
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
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_scheduler_runs_interval_ai_skip_reason_created_at "
        f"ON scheduler_runs (created_at, ({_AI_SKIP_REASON_EXPR})) "
        "WHERE workflow = 'interval_decision_cycle' "
        f"AND ({_AI_SKIP_REASON_EXPR}) <> ''"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_scheduler_runs_interval_ai_skip_reason_created_at")
