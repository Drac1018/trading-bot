"""add live execution columns

Revision ID: 3f9f93d8a8e4
Revises: b94b86b18709
Create Date: 2026-04-08 22:15:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "3f9f93d8a8e4"
down_revision: Union[str, Sequence[str], None] = "b94b86b18709"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("settings") as batch_op:
        batch_op.add_column(sa.Column("live_execution_armed", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("live_execution_armed_until", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("live_approval_window_minutes", sa.Integer(), nullable=False, server_default="15"))

    with op.batch_alter_table("positions") as batch_op:
        batch_op.add_column(sa.Column("mode", sa.String(length=20), nullable=False, server_default="paper"))
        batch_op.create_index("ix_positions_mode", ["mode"], unique=False)

    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(sa.Column("external_order_id", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("client_order_id", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("reduce_only", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("close_only", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("parent_order_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("exchange_status", sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column("last_exchange_update_at", sa.DateTime(), nullable=True))
        batch_op.create_index("ix_orders_external_order_id", ["external_order_id"], unique=False)
        batch_op.create_index("ix_orders_client_order_id", ["client_order_id"], unique=False)
        batch_op.create_index("ix_orders_parent_order_id", ["parent_order_id"], unique=False)

    with op.batch_alter_table("executions") as batch_op:
        batch_op.add_column(sa.Column("external_trade_id", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("commission_asset", sa.String(length=20), nullable=True))
        batch_op.create_index("ix_executions_external_trade_id", ["external_trade_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("executions") as batch_op:
        batch_op.drop_index("ix_executions_external_trade_id")
        batch_op.drop_column("commission_asset")
        batch_op.drop_column("external_trade_id")

    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_index("ix_orders_parent_order_id")
        batch_op.drop_index("ix_orders_client_order_id")
        batch_op.drop_index("ix_orders_external_order_id")
        batch_op.drop_column("last_exchange_update_at")
        batch_op.drop_column("exchange_status")
        batch_op.drop_column("parent_order_id")
        batch_op.drop_column("close_only")
        batch_op.drop_column("reduce_only")
        batch_op.drop_column("client_order_id")
        batch_op.drop_column("external_order_id")

    with op.batch_alter_table("positions") as batch_op:
        batch_op.drop_index("ix_positions_mode")
        batch_op.drop_column("mode")

    with op.batch_alter_table("settings") as batch_op:
        batch_op.drop_column("live_approval_window_minutes")
        batch_op.drop_column("live_execution_armed_until")
        batch_op.drop_column("live_execution_armed")
