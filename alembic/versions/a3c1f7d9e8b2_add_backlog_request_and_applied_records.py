"""add backlog request and applied record tables

Revision ID: a3c1f7d9e8b2
Revises: 8c0f7e21d55a
Create Date: 2026-04-09 15:10:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a3c1f7d9e8b2"
down_revision: Union[str, Sequence[str], None] = "8c0f7e21d55a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

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

    user_indexes = {index["name"] for index in inspector.get_indexes("user_change_requests")}
    if "ix_user_change_requests_status" not in user_indexes:
        op.create_index("ix_user_change_requests_status", "user_change_requests", ["status"], unique=False)
    if "ix_user_change_requests_linked_backlog_id" not in user_indexes:
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

    applied_indexes = {index["name"] for index in inspector.get_indexes("applied_change_records")}
    if "ix_applied_change_records_related_backlog_id" not in applied_indexes:
        op.create_index(
            "ix_applied_change_records_related_backlog_id",
            "applied_change_records",
            ["related_backlog_id"],
            unique=False,
        )
    if "ix_applied_change_records_source_type" not in applied_indexes:
        op.create_index(
            "ix_applied_change_records_source_type",
            "applied_change_records",
            ["source_type"],
            unique=False,
        )
    if "ix_applied_change_records_applied_at" not in applied_indexes:
        op.create_index(
            "ix_applied_change_records_applied_at",
            "applied_change_records",
            ["applied_at"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index("ix_applied_change_records_applied_at", table_name="applied_change_records")
    op.drop_index("ix_applied_change_records_source_type", table_name="applied_change_records")
    op.drop_index("ix_applied_change_records_related_backlog_id", table_name="applied_change_records")
    op.drop_table("applied_change_records")

    op.drop_index("ix_user_change_requests_linked_backlog_id", table_name="user_change_requests")
    op.drop_index("ix_user_change_requests_status", table_name="user_change_requests")
    op.drop_table("user_change_requests")
