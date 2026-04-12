"""add pause reason metadata to settings

Revision ID: d1b6c9e4a2f0
Revises: a3c1f7d9e8b2
Create Date: 2026-04-12 17:40:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "d1b6c9e4a2f0"
down_revision: Union[str, Sequence[str], None] = "a3c1f7d9e8b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("settings")}

    with op.batch_alter_table("settings") as batch_op:
        if "pause_reason_code" not in columns:
            batch_op.add_column(sa.Column("pause_reason_code", sa.String(length=80), nullable=True))
        if "pause_origin" not in columns:
            batch_op.add_column(sa.Column("pause_origin", sa.String(length=30), nullable=True))
        if "pause_reason_detail" not in columns:
            batch_op.add_column(
                sa.Column("pause_reason_detail", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
            )
        if "pause_triggered_at" not in columns:
            batch_op.add_column(sa.Column("pause_triggered_at", sa.DateTime(), nullable=True))
        if "auto_resume_after" not in columns:
            batch_op.add_column(sa.Column("auto_resume_after", sa.DateTime(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("settings")}

    with op.batch_alter_table("settings") as batch_op:
        if "auto_resume_after" in columns:
            batch_op.drop_column("auto_resume_after")
        if "pause_triggered_at" in columns:
            batch_op.drop_column("pause_triggered_at")
        if "pause_reason_detail" in columns:
            batch_op.drop_column("pause_reason_detail")
        if "pause_origin" in columns:
            batch_op.drop_column("pause_origin")
        if "pause_reason_code" in columns:
            batch_op.drop_column("pause_reason_code")
