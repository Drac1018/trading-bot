"""add event source enrichment settings.

Revision ID: d5a8b2c3e4f1
Revises: c1e7b9a4d2f3
Create Date: 2026-04-21 15:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d5a8b2c3e4f1"
down_revision: str | Sequence[str] | None = "c1e7b9a4d2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    column_names = {column["name"] for column in inspector.get_columns("settings")}

    with op.batch_alter_table("settings") as batch_op:
        if "event_source_bls_enrichment_url" not in column_names:
            batch_op.add_column(
                sa.Column("event_source_bls_enrichment_url", sa.String(length=255), nullable=True)
            )
        if "event_source_bls_enrichment_static_params" not in column_names:
            batch_op.add_column(
                sa.Column(
                    "event_source_bls_enrichment_static_params",
                    sa.JSON(),
                    nullable=False,
                    server_default="{}",
                )
            )
        if "event_source_bea_enrichment_url" not in column_names:
            batch_op.add_column(
                sa.Column("event_source_bea_enrichment_url", sa.String(length=255), nullable=True)
            )
        if "event_source_bea_enrichment_static_params" not in column_names:
            batch_op.add_column(
                sa.Column(
                    "event_source_bea_enrichment_static_params",
                    sa.JSON(),
                    nullable=False,
                    server_default="{}",
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    column_names = {column["name"] for column in inspector.get_columns("settings")}

    with op.batch_alter_table("settings") as batch_op:
        if "event_source_bea_enrichment_static_params" in column_names:
            batch_op.drop_column("event_source_bea_enrichment_static_params")
        if "event_source_bea_enrichment_url" in column_names:
            batch_op.drop_column("event_source_bea_enrichment_url")
        if "event_source_bls_enrichment_static_params" in column_names:
            batch_op.drop_column("event_source_bls_enrichment_static_params")
        if "event_source_bls_enrichment_url" in column_names:
            batch_op.drop_column("event_source_bls_enrichment_url")
