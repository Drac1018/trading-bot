"""add event source settings.

Revision ID: c1e7b9a4d2f3
Revises: f7c1d2e3a4b5
Create Date: 2026-04-21 10:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c1e7b9a4d2f3"
down_revision: str | Sequence[str] | None = "f7c1d2e3a4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    column_names = {column["name"] for column in inspector.get_columns("settings")}

    with op.batch_alter_table("settings") as batch_op:
        if "event_source_provider" not in column_names:
            batch_op.add_column(sa.Column("event_source_provider", sa.String(length=20), nullable=True))
        if "event_source_api_url" not in column_names:
            batch_op.add_column(sa.Column("event_source_api_url", sa.String(length=255), nullable=True))
        if "event_source_timeout_seconds" not in column_names:
            batch_op.add_column(sa.Column("event_source_timeout_seconds", sa.Float(), nullable=True))
        if "event_source_default_assets" not in column_names:
            batch_op.add_column(
                sa.Column("event_source_default_assets", sa.JSON(), nullable=False, server_default="[]")
            )
        if "event_source_fred_release_ids" not in column_names:
            batch_op.add_column(
                sa.Column("event_source_fred_release_ids", sa.JSON(), nullable=False, server_default="[]")
            )
        if "event_source_api_key_encrypted" not in column_names:
            batch_op.add_column(
                sa.Column("event_source_api_key_encrypted", sa.Text(), nullable=False, server_default="")
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    column_names = {column["name"] for column in inspector.get_columns("settings")}

    with op.batch_alter_table("settings") as batch_op:
        if "event_source_api_key_encrypted" in column_names:
            batch_op.drop_column("event_source_api_key_encrypted")
        if "event_source_fred_release_ids" in column_names:
            batch_op.drop_column("event_source_fred_release_ids")
        if "event_source_default_assets" in column_names:
            batch_op.drop_column("event_source_default_assets")
        if "event_source_timeout_seconds" in column_names:
            batch_op.drop_column("event_source_timeout_seconds")
        if "event_source_api_url" in column_names:
            batch_op.drop_column("event_source_api_url")
        if "event_source_provider" in column_names:
            batch_op.drop_column("event_source_provider")
