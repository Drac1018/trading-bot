"""remove legacy paper trading setting flag

Revision ID: 8c0f7e21d55a
Revises: 7b2f4a9c1d11
Create Date: 2026-04-08 23:59:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "8c0f7e21d55a"
down_revision: Union[str, Sequence[str], None] = "7b2f4a9c1d11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("settings") as batch_op:
        batch_op.drop_column("paper_trading_enabled")


def downgrade() -> None:
    with op.batch_alter_table("settings") as batch_op:
        batch_op.add_column(
            sa.Column("paper_trading_enabled", sa.Boolean(), nullable=False, server_default=sa.false())
        )
