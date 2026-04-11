"""add ai and binance settings

Revision ID: b94b86b18709
Revises: 855703716928
Create Date: 2026-04-08 13:05:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b94b86b18709"
down_revision: Union[str, Sequence[str], None] = "855703716928"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("settings") as batch_op:
        batch_op.add_column(sa.Column("ai_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(
            sa.Column("ai_provider", sa.String(length=20), nullable=False, server_default="openai")
        )
        batch_op.add_column(
            sa.Column("ai_model", sa.String(length=80), nullable=False, server_default="gpt-4.1-mini")
        )
        batch_op.add_column(
            sa.Column("ai_call_interval_minutes", sa.Integer(), nullable=False, server_default="30")
        )
        batch_op.add_column(
            sa.Column("decision_cycle_interval_minutes", sa.Integer(), nullable=False, server_default="15")
        )
        batch_op.add_column(
            sa.Column("ai_max_input_candles", sa.Integer(), nullable=False, server_default="32")
        )
        batch_op.add_column(sa.Column("ai_temperature", sa.Float(), nullable=False, server_default="0.1"))
        batch_op.add_column(
            sa.Column("openai_api_key_encrypted", sa.Text(), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column(
                "binance_market_data_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column("binance_testnet_enabled", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(
            sa.Column("binance_futures_enabled", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch_op.add_column(
            sa.Column("binance_api_key_encrypted", sa.Text(), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("binance_api_secret_encrypted", sa.Text(), nullable=False, server_default="")
        )

    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "provider_name",
                sa.String(length=50),
                nullable=False,
                server_default="deterministic-mock",
            )
        )
        batch_op.add_column(sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.drop_column("metadata_json")
        batch_op.drop_column("provider_name")

    with op.batch_alter_table("settings") as batch_op:
        batch_op.drop_column("binance_api_secret_encrypted")
        batch_op.drop_column("binance_api_key_encrypted")
        batch_op.drop_column("binance_futures_enabled")
        batch_op.drop_column("binance_testnet_enabled")
        batch_op.drop_column("binance_market_data_enabled")
        batch_op.drop_column("openai_api_key_encrypted")
        batch_op.drop_column("ai_temperature")
        batch_op.drop_column("ai_max_input_candles")
        batch_op.drop_column("decision_cycle_interval_minutes")
        batch_op.drop_column("ai_call_interval_minutes")
        batch_op.drop_column("ai_model")
        batch_op.drop_column("ai_provider")
        batch_op.drop_column("ai_enabled")
