"""add ai usefulness decision fact fields.

Revision ID: f9a0b1c2d3e4
Revises: e8b9c0d1a2f3
Create Date: 2026-05-17 20:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f9a0b1c2d3e4"
down_revision: str | Sequence[str] | None = "e8b9c0d1a2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "decision_performance_facts",
        sa.Column("ai_actionable", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "decision_performance_facts",
        sa.Column("ai_blocked_by_risk", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "decision_performance_facts",
        sa.Column("ai_led_to_order", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "decision_performance_facts",
        sa.Column("ai_led_to_fill", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "decision_performance_facts",
        sa.Column("ai_usefulness_status", sa.String(length=40), nullable=False, server_default="unknown"),
    )
    op.add_column("decision_performance_facts", sa.Column("ai_known_cost_usd", sa.Float(), nullable=True))
    op.add_column("decision_performance_facts", sa.Column("ai_total_tokens", sa.Integer(), nullable=True))
    op.add_column("decision_performance_facts", sa.Column("expected_edge_bps", sa.Float(), nullable=True))
    op.add_column("decision_performance_facts", sa.Column("expected_total_cost_bps", sa.Float(), nullable=True))
    op.add_column("decision_performance_facts", sa.Column("net_expected_edge_bps", sa.Float(), nullable=True))
    op.add_column(
        "decision_performance_facts",
        sa.Column("pnl_data_confidence", sa.String(length=40), nullable=False, server_default="unknown"),
    )
    op.create_index(
        "ix_decision_performance_ai_usefulness_status",
        "decision_performance_facts",
        ["ai_usefulness_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_decision_performance_ai_usefulness_status", table_name="decision_performance_facts")
    op.drop_column("decision_performance_facts", "pnl_data_confidence")
    op.drop_column("decision_performance_facts", "net_expected_edge_bps")
    op.drop_column("decision_performance_facts", "expected_total_cost_bps")
    op.drop_column("decision_performance_facts", "expected_edge_bps")
    op.drop_column("decision_performance_facts", "ai_total_tokens")
    op.drop_column("decision_performance_facts", "ai_known_cost_usd")
    op.drop_column("decision_performance_facts", "ai_usefulness_status")
    op.drop_column("decision_performance_facts", "ai_led_to_fill")
    op.drop_column("decision_performance_facts", "ai_led_to_order")
    op.drop_column("decision_performance_facts", "ai_blocked_by_risk")
    op.drop_column("decision_performance_facts", "ai_actionable")
