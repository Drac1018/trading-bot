"""add tracked symbols for multi-asset live trading

Revision ID: 7b2f4a9c1d11
Revises: 3f9f93d8a8e4
Create Date: 2026-04-08 23:45:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "7b2f4a9c1d11"
down_revision: Union[str, Sequence[str], None] = "3f9f93d8a8e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("settings") as batch_op:
        batch_op.add_column(
            sa.Column("tracked_symbols", sa.JSON(), nullable=False, server_default='["BTCUSDT"]')
        )


def downgrade() -> None:
    with op.batch_alter_table("settings") as batch_op:
        batch_op.drop_column("tracked_symbols")
