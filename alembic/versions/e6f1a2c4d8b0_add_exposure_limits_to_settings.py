"""add exposure hard limits to settings

Revision ID: e6f1a2c4d8b0
Revises: d1b6c9e4a2f0
Create Date: 2026-04-12 23:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e6f1a2c4d8b0"
down_revision: Union[str, Sequence[str], None] = "d1b6c9e4a2f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("settings")}

    with op.batch_alter_table("settings") as batch_op:
        if "max_gross_exposure_pct" not in columns:
            batch_op.add_column(
                sa.Column("max_gross_exposure_pct", sa.Float(), nullable=False, server_default=sa.text("3.0"))
            )
        if "max_largest_position_pct" not in columns:
            batch_op.add_column(
                sa.Column("max_largest_position_pct", sa.Float(), nullable=False, server_default=sa.text("1.5"))
            )
        if "max_directional_bias_pct" not in columns:
            batch_op.add_column(
                sa.Column("max_directional_bias_pct", sa.Float(), nullable=False, server_default=sa.text("2.0"))
            )
        if "max_same_tier_concentration_pct" not in columns:
            batch_op.add_column(
                sa.Column(
                    "max_same_tier_concentration_pct",
                    sa.Float(),
                    nullable=False,
                    server_default=sa.text("2.5"),
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("settings")}

    with op.batch_alter_table("settings") as batch_op:
        if "max_same_tier_concentration_pct" in columns:
            batch_op.drop_column("max_same_tier_concentration_pct")
        if "max_directional_bias_pct" in columns:
            batch_op.drop_column("max_directional_bias_pct")
        if "max_largest_position_pct" in columns:
            batch_op.drop_column("max_largest_position_pct")
        if "max_gross_exposure_pct" in columns:
            batch_op.drop_column("max_gross_exposure_pct")
