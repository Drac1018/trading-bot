"""add risk check chart marker indexes.

Revision ID: c8f2a7d9e4b1
Revises: b4c6d8e0f2a1
Create Date: 2026-05-11 00:25:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c8f2a7d9e4b1"
down_revision: str | Sequence[str] | None = "b4c6d8e0f2a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_risk_checks_created_at ON risk_checks (created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_risk_checks_symbol_created_at ON risk_checks (symbol, created_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_risk_checks_symbol_created_at")
    op.execute("DROP INDEX IF EXISTS ix_risk_checks_created_at")
