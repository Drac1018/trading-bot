"""add strategy cooldown states.

Revision ID: d2e4f6a8b9c0
Revises: c8f2a7d9e4b1
Create Date: 2026-05-13 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d2e4f6a8b9c0"
down_revision: str | Sequence[str] | None = "c8f2a7d9e4b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "strategy_cooldown_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("strategy_id", sa.String(length=80), nullable=False),
        sa.Column("symbol", sa.String(length=30), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("regime_id", sa.String(length=80), nullable=False),
        sa.Column("range_id", sa.String(length=180), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=False), nullable=True),
        sa.Column("cooldown_reason", sa.String(length=80), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("last_breakout_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("last_closed_position_id", sa.Integer(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=False), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_strategy_cooldown_states_strategy_id", "strategy_cooldown_states", ["strategy_id"])
    op.create_index("ix_strategy_cooldown_states_symbol", "strategy_cooldown_states", ["symbol"])
    op.create_index("ix_strategy_cooldown_states_direction", "strategy_cooldown_states", ["direction"])
    op.create_index(
        "ux_strategy_cooldown_identity",
        "strategy_cooldown_states",
        ["strategy_id", "symbol", "direction", "regime_id", "range_id"],
        unique=True,
    )
    op.create_index("ix_strategy_cooldown_until", "strategy_cooldown_states", ["cooldown_until"])
    op.create_index(
        "ix_strategy_cooldown_symbol_direction",
        "strategy_cooldown_states",
        ["symbol", "direction"],
    )
    op.create_index(
        "ix_strategy_cooldown_states_last_closed_position_id",
        "strategy_cooldown_states",
        ["last_closed_position_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_strategy_cooldown_states_last_closed_position_id", table_name="strategy_cooldown_states")
    op.drop_index("ix_strategy_cooldown_symbol_direction", table_name="strategy_cooldown_states")
    op.drop_index("ix_strategy_cooldown_until", table_name="strategy_cooldown_states")
    op.drop_index("ux_strategy_cooldown_identity", table_name="strategy_cooldown_states")
    op.drop_index("ix_strategy_cooldown_states_direction", table_name="strategy_cooldown_states")
    op.drop_index("ix_strategy_cooldown_states_symbol", table_name="strategy_cooldown_states")
    op.drop_index("ix_strategy_cooldown_states_strategy_id", table_name="strategy_cooldown_states")
    op.drop_table("strategy_cooldown_states")
