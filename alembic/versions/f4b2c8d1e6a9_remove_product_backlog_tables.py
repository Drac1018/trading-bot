"""remove product backlog tables

Revision ID: f4b2c8d1e6a9
Revises: e3f7c1a9b2d4
Create Date: 2026-04-16 16:40:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "f4b2c8d1e6a9"
down_revision: Union[str, Sequence[str], None] = "e3f7c1a9b2d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "applied_change_records" in existing_tables:
        indexes = {index["name"] for index in inspector.get_indexes("applied_change_records")}
        if "ix_applied_change_records_applied_at" in indexes:
            op.drop_index("ix_applied_change_records_applied_at", table_name="applied_change_records")
        if "ix_applied_change_records_source_type" in indexes:
            op.drop_index("ix_applied_change_records_source_type", table_name="applied_change_records")
        if "ix_applied_change_records_related_backlog_id" in indexes:
            op.drop_index("ix_applied_change_records_related_backlog_id", table_name="applied_change_records")
        op.drop_table("applied_change_records")

    if "user_change_requests" in existing_tables:
        indexes = {index["name"] for index in inspector.get_indexes("user_change_requests")}
        if "ix_user_change_requests_linked_backlog_id" in indexes:
            op.drop_index("ix_user_change_requests_linked_backlog_id", table_name="user_change_requests")
        if "ix_user_change_requests_status" in indexes:
            op.drop_index("ix_user_change_requests_status", table_name="user_change_requests")
        op.drop_table("user_change_requests")

    if "product_backlog" in existing_tables:
        op.drop_table("product_backlog")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "product_backlog" not in existing_tables:
        op.create_table(
            "product_backlog",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("problem", sa.Text(), nullable=False),
            sa.Column("proposal", sa.Text(), nullable=False),
            sa.Column("severity", sa.String(length=20), nullable=False),
            sa.Column("effort", sa.String(length=20), nullable=False),
            sa.Column("impact", sa.String(length=20), nullable=False),
            sa.Column("priority", sa.String(length=20), nullable=False),
            sa.Column("rationale", sa.Text(), nullable=False),
            sa.Column("source", sa.String(length=50), nullable=False, server_default="seed"),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="open"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )

    if "user_change_requests" not in existing_tables:
        op.create_table(
            "user_change_requests",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("detail", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="requested"),
            sa.Column("linked_backlog_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        op.create_index("ix_user_change_requests_status", "user_change_requests", ["status"], unique=False)
        op.create_index(
            "ix_user_change_requests_linked_backlog_id",
            "user_change_requests",
            ["linked_backlog_id"],
            unique=False,
        )

    if "applied_change_records" not in existing_tables:
        op.create_table(
            "applied_change_records",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("detail", sa.Text(), nullable=False),
            sa.Column("related_backlog_id", sa.Integer(), nullable=True),
            sa.Column("source_type", sa.String(length=20), nullable=False, server_default="manual"),
            sa.Column("files_changed", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("verification_summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("applied_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        op.create_index(
            "ix_applied_change_records_related_backlog_id",
            "applied_change_records",
            ["related_backlog_id"],
            unique=False,
        )
        op.create_index(
            "ix_applied_change_records_source_type",
            "applied_change_records",
            ["source_type"],
            unique=False,
        )
        op.create_index(
            "ix_applied_change_records_applied_at",
            "applied_change_records",
            ["applied_at"],
            unique=False,
        )
