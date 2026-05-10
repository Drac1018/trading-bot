"""add cost analytics lookup indexes.

Revision ID: b4c6d8e0f2a1
Revises: a2d9e3f4c5b6
Create Date: 2026-05-10 14:35:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b4c6d8e0f2a1"
down_revision: str | Sequence[str] | None = "a2d9e3f4c5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_executions_created_at ON executions (created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_orders_mode_created_at ON orders (mode, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_positions_mode_closed_at ON positions (mode, closed_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_positions_mode_closed_at")
    op.execute("DROP INDEX IF EXISTS ix_orders_mode_created_at")
    op.execute("DROP INDEX IF EXISTS ix_executions_created_at")
