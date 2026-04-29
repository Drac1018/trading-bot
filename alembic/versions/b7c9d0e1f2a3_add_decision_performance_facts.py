"""add decision performance facts.

Revision ID: b7c9d0e1f2a3
Revises: a8d4e6f2b9c1
Create Date: 2026-04-29 20:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7c9d0e1f2a3"
down_revision: str | Sequence[str] | None = "a8d4e6f2b9c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "decision_performance_facts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("decision_run_id", sa.Integer(), nullable=False),
        sa.Column("provider_name", sa.String(length=50), nullable=False, server_default="deterministic-mock"),
        sa.Column("symbol", sa.String(length=30), nullable=False, server_default="UNKNOWN"),
        sa.Column("timeframe", sa.String(length=20), nullable=False, server_default="UNKNOWN"),
        sa.Column("decision", sa.String(length=30), nullable=False, server_default="unknown"),
        sa.Column("rationale_codes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("regime", sa.String(length=40), nullable=False, server_default="unknown"),
        sa.Column("trend_alignment", sa.String(length=40), nullable=False, server_default="unknown"),
        sa.Column("weak_volume", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("volatility_expanded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("momentum_weakening", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("entry_zone_min", sa.Float(), nullable=True),
        sa.Column("entry_zone_max", sa.Float(), nullable=True),
        sa.Column("stop_loss", sa.Float(), nullable=True),
        sa.Column("take_profit", sa.Float(), nullable=True),
        sa.Column("max_holding_minutes", sa.Integer(), nullable=True),
        sa.Column("baseline_decision", sa.String(length=30), nullable=True),
        sa.Column("ai_used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("comparison_bucket", sa.String(length=60), nullable=False, server_default="ai_hold_no_trade"),
        sa.Column("decision_agreement_level", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("decision_agreement_source", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("telemetry_metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("telemetry_output", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=False), nullable=False),
    )
    op.create_index(
        "ix_decision_performance_facts_created_at",
        "decision_performance_facts",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_decision_performance_facts_decision_run_id",
        "decision_performance_facts",
        ["decision_run_id"],
        unique=True,
    )
    _backfill_postgresql()


def downgrade() -> None:
    op.drop_index("ix_decision_performance_facts_decision_run_id", table_name="decision_performance_facts")
    op.drop_index("ix_decision_performance_facts_created_at", table_name="decision_performance_facts")
    op.drop_table("decision_performance_facts")


def _backfill_postgresql() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    bind.execute(
        sa.text(
            """
            insert into decision_performance_facts (
                decision_run_id,
                provider_name,
                symbol,
                timeframe,
                decision,
                rationale_codes,
                regime,
                trend_alignment,
                weak_volume,
                volatility_expanded,
                momentum_weakening,
                entry_zone_min,
                entry_zone_max,
                stop_loss,
                take_profit,
                max_holding_minutes,
                baseline_decision,
                ai_used,
                comparison_bucket,
                decision_agreement_level,
                decision_agreement_source,
                telemetry_metadata,
                telemetry_output,
                created_at,
                updated_at
            )
            select
                id as decision_run_id,
                coalesce(nullif(provider_name, ''), 'deterministic-mock') as provider_name,
                coalesce(nullif(output_payload->>'symbol', ''), 'UNKNOWN') as symbol,
                coalesce(nullif(output_payload->>'timeframe', ''), 'UNKNOWN') as timeframe,
                coalesce(nullif(output_payload->>'decision', ''), 'unknown') as decision,
                case
                    when json_typeof(output_payload->'rationale_codes') = 'array'
                    then output_payload->'rationale_codes'
                    else '["UNSPECIFIED"]'::json
                end as rationale_codes,
                coalesce(
                    nullif(input_payload#>>'{features,regime,primary_regime}', ''),
                    nullif(metadata_json#>>'{analysis_context,regime,primary_regime}', ''),
                    'unknown'
                ) as regime,
                coalesce(
                    nullif(input_payload#>>'{features,regime,trend_alignment}', ''),
                    nullif(metadata_json#>>'{analysis_context,regime,trend_alignment}', ''),
                    'unknown'
                ) as trend_alignment,
                lower(coalesce(
                    nullif(input_payload#>>'{features,regime,weak_volume}', ''),
                    nullif(metadata_json#>>'{analysis_context,flags,weak_volume}', ''),
                    'false'
                )) in ('true', '1', 'yes', 'on') as weak_volume,
                (
                    lower(coalesce(nullif(metadata_json#>>'{analysis_context,flags,volatility_expanded}', ''), 'false'))
                    in ('true', '1', 'yes', 'on')
                    or coalesce(
                        coalesce(
                            nullif(input_payload#>>'{features,regime,volatility_regime}', ''),
                            nullif(metadata_json#>>'{analysis_context,regime,volatility_regime}', '')
                        ) = 'expanded',
                        false
                    )
                ) as volatility_expanded,
                lower(coalesce(
                    nullif(input_payload#>>'{features,regime,momentum_weakening}', ''),
                    nullif(metadata_json#>>'{analysis_context,flags,momentum_weakening}', ''),
                    'false'
                )) in ('true', '1', 'yes', 'on') as momentum_weakening,
                case when (output_payload->>'entry_zone_min') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                    then (output_payload->>'entry_zone_min')::double precision end as entry_zone_min,
                case when (output_payload->>'entry_zone_max') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                    then (output_payload->>'entry_zone_max')::double precision end as entry_zone_max,
                case when (output_payload->>'stop_loss') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                    then (output_payload->>'stop_loss')::double precision end as stop_loss,
                case when (output_payload->>'take_profit') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                    then (output_payload->>'take_profit')::double precision end as take_profit,
                case when (output_payload->>'max_holding_minutes') ~ '^[0-9]+$'
                    then (output_payload->>'max_holding_minutes')::integer end as max_holding_minutes,
                coalesce(
                    nullif(metadata_json#>>'{decision_agreement,baseline_decision}', ''),
                    nullif(metadata_json#>>'{decision_context,baseline_decision}', ''),
                    nullif(metadata_json->>'baseline_decision', '')
                ) as baseline_decision,
                (
                    lower(coalesce(
                        nullif(metadata_json#>>'{decision_agreement,ai_used}', ''),
                        nullif(metadata_json#>>'{decision_context,ai_used}', ''),
                        nullif(metadata_json->>'ai_used', ''),
                        'false'
                    )) in ('true', '1', 'yes', 'on')
                    or provider_name = 'openai'
                    or coalesce(metadata_json->>'source', '') in ('llm', 'llm_fallback')
                ) as ai_used,
                case
                    when coalesce(metadata_json->>'source', '') = 'llm_fallback'
                        or lower(coalesce(metadata_json->>'fail_closed_applied', 'false')) in ('true', '1', 'yes', 'on')
                        or lower(coalesce(metadata_json->>'data_quality_fail_closed_applied', 'false')) in ('true', '1', 'yes', 'on')
                        or lower(coalesce(output_payload->>'fail_closed_applied', 'false')) in ('true', '1', 'yes', 'on')
                    then 'provider_failed_fail_closed'
                    when coalesce(output_payload->>'decision', '') in ('reduce', 'exit') then 'ai_management_action'
                    when coalesce(output_payload->>'decision', '') in ('long', 'short') then 'ai_approved_entry'
                    when coalesce(
                        nullif(metadata_json#>>'{decision_agreement,baseline_decision}', ''),
                        nullif(metadata_json#>>'{decision_context,baseline_decision}', ''),
                        nullif(metadata_json->>'baseline_decision', '')
                    ) in ('long', 'short')
                        and coalesce(output_payload->>'decision', '') = 'hold'
                    then 'ai_rejected_baseline_entry'
                    else 'ai_hold_no_trade'
                end as comparison_bucket,
                coalesce(
                    nullif(metadata_json#>>'{decision_agreement,level}', ''),
                    nullif(metadata_json#>>'{decision_context,level}', ''),
                    ''
                ) as decision_agreement_level,
                coalesce(
                    nullif(metadata_json#>>'{decision_agreement,comparison_source}', ''),
                    nullif(metadata_json#>>'{decision_context,comparison_source}', ''),
                    ''
                ) as decision_agreement_source,
                json_build_object(
                    'source', metadata_json->>'source',
                    'error', metadata_json->>'error',
                    'usage', coalesce(metadata_json->'usage', '{}'::json),
                    'ai_model', metadata_json->>'ai_model',
                    'model', metadata_json->>'model',
                    'openai_model', metadata_json->>'openai_model',
                    'pre_ai_skip_reason', metadata_json->>'pre_ai_skip_reason',
                    'ai_skipped_reason', metadata_json->>'ai_skipped_reason',
                    'last_ai_skip_reason', metadata_json->>'last_ai_skip_reason',
                    'provider_not_called_due_to_quality', metadata_json->'provider_not_called_due_to_quality',
                    'provider_status', metadata_json->>'provider_status',
                    'data_quality_block_reason_codes', coalesce(metadata_json->'data_quality_block_reason_codes', '[]'::json),
                    'event_risk_reason_codes', coalesce(metadata_json->'event_risk_reason_codes', '[]'::json),
                    'should_abstain', metadata_json->'should_abstain',
                    'fail_closed_applied', metadata_json->'fail_closed_applied'
                ) as telemetry_metadata,
                json_build_object(
                    'decision', output_payload->>'decision',
                    'should_abstain', output_payload->'should_abstain',
                    'fail_closed_applied', output_payload->'fail_closed_applied',
                    'data_quality_flags', coalesce(output_payload->'data_quality_flags', '[]'::json)
                ) as telemetry_output,
                created_at,
                updated_at
            from agent_runs
            where role = 'trading_decision'
            on conflict (decision_run_id) do nothing
            """
        )
    )
