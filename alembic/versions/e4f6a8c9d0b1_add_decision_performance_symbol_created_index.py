"""add decision performance symbol created index.

Revision ID: e4f6a8c9d0b1
Revises: d2e4f6a8b9c0
Create Date: 2026-05-15 00:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e4f6a8c9d0b1"
down_revision: str | Sequence[str] | None = "d2e4f6a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_decision_performance_facts_symbol_created_at "
        "ON decision_performance_facts (symbol, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_decision_performance_facts_symbol_created_at")
