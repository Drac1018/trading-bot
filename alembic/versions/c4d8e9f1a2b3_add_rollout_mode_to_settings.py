"""add rollout mode to settings

Revision ID: c4d8e9f1a2b3
Revises: b3e7a1c2d4f6
Create Date: 2026-04-17 17:40:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4d8e9f1a2b3"
down_revision: Union[str, Sequence[str], None] = "b3e7a1c2d4f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("settings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("rollout_mode", sa.String(length=20), nullable=False, server_default="paper"))
        batch_op.add_column(
            sa.Column("limited_live_max_notional", sa.Float(), nullable=False, server_default="500.0")
        )

    op.execute(
        "UPDATE settings "
        "SET rollout_mode = CASE WHEN live_trading_enabled = 1 THEN 'full_live' ELSE 'paper' END"
    )

    with op.batch_alter_table("settings", schema=None) as batch_op:
        batch_op.alter_column("rollout_mode", server_default=None)
        batch_op.alter_column("limited_live_max_notional", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("settings", schema=None) as batch_op:
        batch_op.drop_column("limited_live_max_notional")
        batch_op.drop_column("rollout_mode")
