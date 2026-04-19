"""remove starting_equity from settings.

Revision ID: f7c1d2e3a4b5
Revises: d8e4b6c1f3a5
Create Date: 2026-04-19 20:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f7c1d2e3a4b5"
down_revision: str | Sequence[str] | None = "d8e4b6c1f3a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    column_names = {column["name"] for column in inspector.get_columns("settings")}

    if "starting_equity" in column_names:
        with op.batch_alter_table("settings") as batch_op:
            batch_op.drop_column("starting_equity")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    column_names = {column["name"] for column in inspector.get_columns("settings")}

    if "starting_equity" not in column_names:
        with op.batch_alter_table("settings") as batch_op:
            batch_op.add_column(
                sa.Column("starting_equity", sa.Float(), nullable=False, server_default="100000.0")
            )
