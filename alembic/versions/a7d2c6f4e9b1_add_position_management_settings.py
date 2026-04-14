"""add position management settings

Revision ID: a7d2c6f4e9b1
Revises: f2a4c7d9e1b3
Create Date: 2026-04-14 14:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a7d2c6f4e9b1"
down_revision: Union[str, Sequence[str], None] = "f2a4c7d9e1b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SETTING_COLUMNS: dict[str, sa.Column] = {
    "position_management_enabled": sa.Column(
        "position_management_enabled",
        sa.Boolean(),
        nullable=False,
        server_default=sa.true(),
    ),
    "break_even_enabled": sa.Column(
        "break_even_enabled",
        sa.Boolean(),
        nullable=False,
        server_default=sa.true(),
    ),
    "atr_trailing_stop_enabled": sa.Column(
        "atr_trailing_stop_enabled",
        sa.Boolean(),
        nullable=False,
        server_default=sa.true(),
    ),
    "partial_take_profit_enabled": sa.Column(
        "partial_take_profit_enabled",
        sa.Boolean(),
        nullable=False,
        server_default=sa.true(),
    ),
    "holding_edge_decay_enabled": sa.Column(
        "holding_edge_decay_enabled",
        sa.Boolean(),
        nullable=False,
        server_default=sa.true(),
    ),
    "reduce_on_regime_shift_enabled": sa.Column(
        "reduce_on_regime_shift_enabled",
        sa.Boolean(),
        nullable=False,
        server_default=sa.true(),
    ),
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
