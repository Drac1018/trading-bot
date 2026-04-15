"""add position management threshold fields

Revision ID: e3f7c1a9b2d4
Revises: c9d4e1b7f2a6
Create Date: 2026-04-15 23:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e3f7c1a9b2d4"
down_revision: Union[str, Sequence[str], None] = "c9d4e1b7f2a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SETTING_COLUMNS: dict[str, sa.Column] = {
    "partial_tp_rr": sa.Column("partial_tp_rr", sa.Float(), nullable=False, server_default="1.5"),
    "partial_tp_size_pct": sa.Column("partial_tp_size_pct", sa.Float(), nullable=False, server_default="0.25"),
    "move_stop_to_be_rr": sa.Column("move_stop_to_be_rr", sa.Float(), nullable=False, server_default="1.0"),
    "time_stop_enabled": sa.Column("time_stop_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    "time_stop_minutes": sa.Column("time_stop_minutes", sa.Integer(), nullable=False, server_default="120"),
    "time_stop_profit_floor": sa.Column("time_stop_profit_floor", sa.Float(), nullable=False, server_default="0.15"),
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("settings")}

    with op.batch_alter_table("settings") as batch_op:
        for name, column in SETTING_COLUMNS.items():
            if name not in columns:
                batch_op.add_column(column)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("settings")}

    with op.batch_alter_table("settings") as batch_op:
        for name in SETTING_COLUMNS:
            if name in columns:
                batch_op.drop_column(name)
