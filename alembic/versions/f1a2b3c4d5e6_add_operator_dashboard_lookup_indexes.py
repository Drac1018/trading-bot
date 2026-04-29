"""add operator dashboard lookup indexes.

Revision ID: f1a2b3c4d5e6
Revises: e9f0a1b2c3d4
Create Date: 2026-04-28 00:45:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "e9f0a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_market_snapshots_symbol_timeframe_snapshot_time",
        "market_snapshots",
        ["symbol", "timeframe", "snapshot_time"],
        unique=False,
    )
    op.create_index(
        "ix_feature_snapshots_symbol_timeframe_feature_time",
        "feature_snapshots",
        ["symbol", "timeframe", "feature_time"],
        unique=False,
    )
    op.create_index(
        "ix_agent_runs_role_created_at",
        "agent_runs",
        ["role", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_agent_runs_role_created_at", table_name="agent_runs")
    op.drop_index("ix_feature_snapshots_symbol_timeframe_feature_time", table_name="feature_snapshots")
    op.drop_index("ix_market_snapshots_symbol_timeframe_snapshot_time", table_name="market_snapshots")
