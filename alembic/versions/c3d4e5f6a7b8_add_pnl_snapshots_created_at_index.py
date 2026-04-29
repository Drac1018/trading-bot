"""add pnl snapshots created-at index.

Revision ID: c3d4e5f6a7b8
Revises: b7c9d0e1f2a3
Create Date: 2026-04-30 00:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b7c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_pnl_snapshots_created_at", "pnl_snapshots", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_pnl_snapshots_created_at", table_name="pnl_snapshots")
