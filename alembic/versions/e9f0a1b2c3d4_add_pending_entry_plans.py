"""add pending entry plans.

Revision ID: e9f0a1b2c3d4
Revises: d5a8b2c3e4f1
Create Date: 2026-04-25 00:42:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e9f0a1b2c3d4"
down_revision: str | Sequence[str] | None = "d5a8b2c3e4f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "pending_entry_plans" in inspector.get_table_names():
        return

    op.create_table(
        "pending_entry_plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=30), nullable=False),
        sa.Column("side", sa.String(length=10), nullable=False),
        sa.Column("plan_status", sa.String(length=20), nullable=False),
        sa.Column("source_decision_run_id", sa.Integer(), nullable=True),
        sa.Column("regime", sa.String(length=40), nullable=True),
        sa.Column("posture", sa.String(length=80), nullable=True),
        sa.Column("rationale_codes", sa.JSON(), nullable=False),
        sa.Column("source_timeframe", sa.String(length=20), nullable=True),
        sa.Column("entry_mode", sa.String(length=30), nullable=False),
        sa.Column("entry_zone_min", sa.Float(), nullable=False),
        sa.Column("entry_zone_max", sa.Float(), nullable=False),
        sa.Column("invalidation_price", sa.Float(), nullable=True),
        sa.Column("max_chase_bps", sa.Float(), nullable=True),
        sa.Column("idea_ttl_minutes", sa.Integer(), nullable=True),
        sa.Column("stop_loss", sa.Float(), nullable=True),
        sa.Column("take_profit", sa.Float(), nullable=True),
        sa.Column("risk_pct_cap", sa.Float(), nullable=False),
        sa.Column("leverage_cap", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("canceled_reason", sa.String(length=120), nullable=True),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=False), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_pending_entry_plans_symbol"), "pending_entry_plans", ["symbol"], unique=False)
    op.create_index(op.f("ix_pending_entry_plans_side"), "pending_entry_plans", ["side"], unique=False)
    op.create_index(
        op.f("ix_pending_entry_plans_plan_status"),
        "pending_entry_plans",
        ["plan_status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_pending_entry_plans_source_decision_run_id"),
        "pending_entry_plans",
        ["source_decision_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_pending_entry_plans_expires_at"),
        "pending_entry_plans",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_pending_entry_plans_idempotency_key"),
        "pending_entry_plans",
        ["idempotency_key"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "pending_entry_plans" not in inspector.get_table_names():
        return

    op.drop_index(op.f("ix_pending_entry_plans_idempotency_key"), table_name="pending_entry_plans")
    op.drop_index(op.f("ix_pending_entry_plans_expires_at"), table_name="pending_entry_plans")
    op.drop_index(op.f("ix_pending_entry_plans_source_decision_run_id"), table_name="pending_entry_plans")
    op.drop_index(op.f("ix_pending_entry_plans_plan_status"), table_name="pending_entry_plans")
    op.drop_index(op.f("ix_pending_entry_plans_side"), table_name="pending_entry_plans")
    op.drop_index(op.f("ix_pending_entry_plans_symbol"), table_name="pending_entry_plans")
    op.drop_table("pending_entry_plans")
