"""add skipped trade events

Revision ID: d8e4b6c1f3a5
Revises: c4d8e9f1a2b3
Create Date: 2026-04-18 19:20:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d8e4b6c1f3a5"
down_revision: Union[str, Sequence[str], None] = "c4d8e9f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "skipped_trade_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=30), nullable=False),
        sa.Column("timeframe", sa.String(length=20), nullable=False),
        sa.Column("scenario", sa.String(length=40), nullable=False),
        sa.Column("regime", sa.String(length=40), nullable=False),
        sa.Column("trend_alignment", sa.String(length=40), nullable=False),
        sa.Column("entry_mode", sa.String(length=30), nullable=False),
        sa.Column("skip_reason", sa.String(length=80), nullable=False),
        sa.Column("skip_source", sa.String(length=30), nullable=False),
        sa.Column("market_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("decision_run_id", sa.Integer(), nullable=True),
        sa.Column("risk_check_id", sa.Integer(), nullable=True),
        sa.Column("expected_side", sa.String(length=10), nullable=True),
        sa.Column("rejected_side", sa.String(length=10), nullable=True),
        sa.Column("reference_price", sa.Float(), nullable=True),
        sa.Column("stop_loss", sa.Float(), nullable=True),
        sa.Column("take_profit", sa.Float(), nullable=True),
        sa.Column("horizon_minutes", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="pending_evaluation"),
        sa.Column("skipped_trade_followup_return", sa.Float(), nullable=True),
        sa.Column("would_have_hit_tp", sa.Boolean(), nullable=True),
        sa.Column("would_have_hit_sl", sa.Boolean(), nullable=True),
        sa.Column("would_have_reached_0_5r", sa.Boolean(), nullable=True),
        sa.Column("skip_quality_score", sa.Float(), nullable=True),
        sa.Column("skip_quality_label", sa.String(length=30), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=False), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_skipped_trade_events_symbol"), "skipped_trade_events", ["symbol"], unique=False)
    op.create_index(op.f("ix_skipped_trade_events_timeframe"), "skipped_trade_events", ["timeframe"], unique=False)
    op.create_index(op.f("ix_skipped_trade_events_scenario"), "skipped_trade_events", ["scenario"], unique=False)
    op.create_index(op.f("ix_skipped_trade_events_regime"), "skipped_trade_events", ["regime"], unique=False)
    op.create_index(
        op.f("ix_skipped_trade_events_trend_alignment"),
        "skipped_trade_events",
        ["trend_alignment"],
        unique=False,
    )
    op.create_index(op.f("ix_skipped_trade_events_entry_mode"), "skipped_trade_events", ["entry_mode"], unique=False)
    op.create_index(op.f("ix_skipped_trade_events_skip_reason"), "skipped_trade_events", ["skip_reason"], unique=False)
    op.create_index(op.f("ix_skipped_trade_events_skip_source"), "skipped_trade_events", ["skip_source"], unique=False)
    op.create_index(
        op.f("ix_skipped_trade_events_market_snapshot_id"),
        "skipped_trade_events",
        ["market_snapshot_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_skipped_trade_events_decision_run_id"),
        "skipped_trade_events",
        ["decision_run_id"],
        unique=False,
    )
    op.create_index(op.f("ix_skipped_trade_events_risk_check_id"), "skipped_trade_events", ["risk_check_id"], unique=False)
    op.create_index(op.f("ix_skipped_trade_events_status"), "skipped_trade_events", ["status"], unique=False)

    with op.batch_alter_table("skipped_trade_events", schema=None) as batch_op:
        batch_op.alter_column("horizon_minutes", server_default=None)
        batch_op.alter_column("status", server_default=None)
        batch_op.alter_column("payload", server_default=None)


def downgrade() -> None:
    op.drop_index(op.f("ix_skipped_trade_events_status"), table_name="skipped_trade_events")
    op.drop_index(op.f("ix_skipped_trade_events_risk_check_id"), table_name="skipped_trade_events")
    op.drop_index(op.f("ix_skipped_trade_events_decision_run_id"), table_name="skipped_trade_events")
    op.drop_index(op.f("ix_skipped_trade_events_market_snapshot_id"), table_name="skipped_trade_events")
    op.drop_index(op.f("ix_skipped_trade_events_skip_source"), table_name="skipped_trade_events")
    op.drop_index(op.f("ix_skipped_trade_events_skip_reason"), table_name="skipped_trade_events")
    op.drop_index(op.f("ix_skipped_trade_events_entry_mode"), table_name="skipped_trade_events")
    op.drop_index(op.f("ix_skipped_trade_events_trend_alignment"), table_name="skipped_trade_events")
    op.drop_index(op.f("ix_skipped_trade_events_regime"), table_name="skipped_trade_events")
    op.drop_index(op.f("ix_skipped_trade_events_scenario"), table_name="skipped_trade_events")
    op.drop_index(op.f("ix_skipped_trade_events_timeframe"), table_name="skipped_trade_events")
    op.drop_index(op.f("ix_skipped_trade_events_symbol"), table_name="skipped_trade_events")
    op.drop_table("skipped_trade_events")
