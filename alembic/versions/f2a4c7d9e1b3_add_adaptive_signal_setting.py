"""add adaptive signal setting

Revision ID: f2a4c7d9e1b3
Revises: e6f1a2c4d8b0
Create Date: 2026-04-14 12:10:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "f2a4c7d9e1b3"
down_revision: Union[str, Sequence[str], None] = "e6f1a2c4d8b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("settings")}

    with op.batch_alter_table("settings") as batch_op:
        if "adaptive_signal_enabled" not in columns:
            batch_op.add_column(
                sa.Column("adaptive_signal_enabled", sa.Boolean(), nullable=False, server_default=sa.false())
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("settings")}

    with op.batch_alter_table("settings") as batch_op:
        if "adaptive_signal_enabled" in columns:
            batch_op.drop_column("adaptive_signal_enabled")
