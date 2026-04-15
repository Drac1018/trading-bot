"""add cycle cadence settings

Revision ID: c9d4e1b7f2a6
Revises: a7d2c6f4e9b1
Create Date: 2026-04-15 18:40:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9d4e1b7f2a6"
down_revision: Union[str, None] = "a7d2c6f4e9b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "settings",
        sa.Column("exchange_sync_interval_seconds", sa.Integer(), nullable=False, server_default="60"),
    )
    op.add_column(
        "settings",
        sa.Column("market_refresh_interval_minutes", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "settings",
        sa.Column("position_management_interval_seconds", sa.Integer(), nullable=False, server_default="60"),
    )
    op.add_column(
        "settings",
        sa.Column("symbol_cadence_overrides", sa.JSON(), nullable=False, server_default="[]"),
    )

    op.alter_column("settings", "exchange_sync_interval_seconds", server_default=None)
    op.alter_column("settings", "market_refresh_interval_minutes", server_default=None)
    op.alter_column("settings", "position_management_interval_seconds", server_default=None)
    op.alter_column("settings", "symbol_cadence_overrides", server_default=None)


def downgrade() -> None:
    op.drop_column("settings", "symbol_cadence_overrides")
    op.drop_column("settings", "position_management_interval_seconds")
    op.drop_column("settings", "market_refresh_interval_minutes")
    op.drop_column("settings", "exchange_sync_interval_seconds")
