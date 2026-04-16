"""add account snapshot balance fields and funding ledger

Revision ID: b3e7a1c2d4f6
Revises: f4b2c8d1e6a9
Create Date: 2026-04-17 20:10:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b3e7a1c2d4f6"
down_revision: Union[str, Sequence[str], None] = "f4b2c8d1e6a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("pnl_snapshots") as batch_op:
        batch_op.add_column(sa.Column("wallet_balance", sa.Float(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("available_balance", sa.Float(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("gross_realized_pnl", sa.Float(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("fee_total", sa.Float(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("funding_total", sa.Float(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("net_pnl", sa.Float(), nullable=False, server_default="0"))

    op.create_table(
        "account_ledger_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entry_type", sa.String(length=30), nullable=False, server_default="funding"),
        sa.Column("asset", sa.String(length=20), nullable=False, server_default="USDT"),
        sa.Column("symbol", sa.String(length=30), nullable=True),
        sa.Column("amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("external_ref_id", sa.String(length=80), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_account_ledger_entries_entry_type", "account_ledger_entries", ["entry_type"], unique=False)
    op.create_index("ix_account_ledger_entries_symbol", "account_ledger_entries", ["symbol"], unique=False)
    op.create_index("ix_account_ledger_entries_external_ref_id", "account_ledger_entries", ["external_ref_id"], unique=False)
    op.create_index("ix_account_ledger_entries_occurred_at", "account_ledger_entries", ["occurred_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_account_ledger_entries_occurred_at", table_name="account_ledger_entries")
    op.drop_index("ix_account_ledger_entries_external_ref_id", table_name="account_ledger_entries")
    op.drop_index("ix_account_ledger_entries_symbol", table_name="account_ledger_entries")
    op.drop_index("ix_account_ledger_entries_entry_type", table_name="account_ledger_entries")
    op.drop_table("account_ledger_entries")

    with op.batch_alter_table("pnl_snapshots") as batch_op:
        batch_op.drop_column("net_pnl")
        batch_op.drop_column("funding_total")
        batch_op.drop_column("fee_total")
        batch_op.drop_column("gross_realized_pnl")
        batch_op.drop_column("available_balance")
        batch_op.drop_column("wallet_balance")
