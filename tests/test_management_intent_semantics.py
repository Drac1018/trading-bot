from __future__ import annotations

from datetime import timedelta

from trading_mvp.models import AgentRun, Execution, Order, Position
from trading_mvp.services.capital_efficiency import build_capital_efficiency_report
from trading_mvp.services.intent_semantics import infer_intent_semantics
from trading_mvp.time_utils import utcnow_naive


def _seed_decision_trade(
    db_session,
    *,
    created_at,
    decision: str,
    scenario: str,
    strategy_engine: str,
    rationale_codes: list[str],
    entry_mode: str,
    execution_policy_profile: str = "entry_btc_fast_calm",
) -> AgentRun:
    decision_row = AgentRun(
        role="trading_decision",
        trigger_event="interval_decision_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="deterministic-mock",
        summary="management semantics seed",
        input_payload={
            "features": {
                "regime": {
                    "primary_regime": "bullish",
                    "trend_alignment": "bullish_aligned",
                }
            }
        },
        output_payload={
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "decision": decision,
            "entry_mode": entry_mode,
            "rationale_codes": rationale_codes,
            "confidence": 0.68,
            "risk_pct": 0.01,
            "leverage": 2.0,
        },
        metadata_json={
            "strategy_engine": {
                "selected_engine": {
                    "engine_name": strategy_engine,
                    "scenario": scenario,
                    "decision_hint": decision,
                    "entry_mode": entry_mode,
                    "eligible": True,
                }
            },
            "selection_context": {
                "scenario": scenario,
                "execution_policy_profile": execution_policy_profile,
            },
        },
        schema_valid=True,
        started_at=created_at,
        completed_at=created_at,
        created_at=created_at,
        updated_at=created_at,
    )
    db_session.add(decision_row)
    db_session.flush()

    position = Position(
        symbol="BTCUSDT",
        mode="live",
        side="long",
        status="closed",
        quantity=1.0,
        entry_price=100.0,
        mark_price=100.0,
        leverage=2.0,
        stop_loss=95.0,
        take_profit=110.0,
        realized_pnl=10.0,
        unrealized_pnl=0.0,
        metadata_json={
            "capital_efficiency": {
                "time_to_0_25r_minutes": 18.0,
                "time_to_0_5r_minutes": 40.0,
                "reached_0_25r": True,
                "reached_0_5r": True,
            }
        },
        opened_at=created_at,
        closed_at=created_at + timedelta(minutes=60),
        created_at=created_at,
        updated_at=created_at,
    )
    db_session.add(position)
    db_session.flush()

    order = Order(
        symbol="BTCUSDT",
        decision_run_id=decision_row.id,
        position_id=position.id,
        side="buy",
        order_type="limit",
        mode="live",
        status="filled",
        requested_quantity=1.0,
        requested_price=100.0,
        filled_quantity=1.0,
        average_fill_price=100.0,
        reason_codes=[],
        metadata_json={"execution_quality": {"policy_profile": execution_policy_profile}},
        created_at=created_at,
        updated_at=created_at,
    )
    db_session.add(order)
    db_session.flush()

    execution = Execution(
        order_id=order.id,
        position_id=position.id,
        symbol="BTCUSDT",
        status="filled",
        external_trade_id=f"mgmt-{decision_row.id}",
        fill_price=100.0,
        fill_quantity=1.0,
        fee_paid=1.0,
        commission_asset="USDT",
        slippage_pct=0.001,
        realized_pnl=10.0,
        payload={"signed_slippage_bps": 4.0},
        created_at=created_at + timedelta(minutes=60),
        updated_at=created_at + timedelta(minutes=60),
    )
    db_session.add(execution)
    db_session.flush()
    return decision_row


def test_protection_restore_is_classified_as_protection_management_action(db_session) -> None:
    row = _seed_decision_trade(
        db_session,
        created_at=utcnow_naive() - timedelta(hours=4),
        decision="long",
        scenario="protection_restore",
        strategy_engine="protection_reduce_engine",
        rationale_codes=["PROTECTION_REQUIRED", "PROTECTION_RESTORE"],
        entry_mode="immediate",
    )

    semantics = infer_intent_semantics(row.output_payload, row.metadata_json)

    assert row.output_payload["decision"] == "long"
    assert semantics["intent_family"] == "protection"
    assert semantics["management_action"] == "restore_protection"
    assert semantics["legacy_semantics_preserved"] is True
    assert semantics["analytics_excluded_from_entry_stats"] is True


def test_management_actions_are_excluded_from_entry_stats(db_session) -> None:
    now = utcnow_naive() - timedelta(hours=4)
    _seed_decision_trade(
        db_session,
        created_at=now,
        decision="long",
        scenario="pullback_entry",
        strategy_engine="trend_pullback_engine",
        rationale_codes=["PULLBACK_ENTRY_BIAS"],
        entry_mode="pullback_confirm",
    )
    _seed_decision_trade(
        db_session,
        created_at=now + timedelta(hours=1),
        decision="long",
        scenario="protection_restore",
        strategy_engine="protection_reduce_engine",
        rationale_codes=["PROTECTION_REQUIRED", "PROTECTION_RESTORE"],
        entry_mode="immediate",
    )

    report = build_capital_efficiency_report(db_session, lookback_days=21, limit=64)

    entry_bucket = next(item for item in report.bucket_reports if item.scenario == "pullback_entry")
    management_bucket = next(item for item in report.bucket_reports if item.scenario == "protection_restore")
    assert entry_bucket.traded_decisions == 1
    assert management_bucket.decisions == 1
    assert management_bucket.traded_decisions == 0


def test_reduce_and_exit_intents_keep_non_entry_management_semantics() -> None:
    reduce_semantics = infer_intent_semantics(
        {"decision": "reduce", "rationale_codes": ["POSITION_MANAGEMENT_EDGE_DECAY"]},
        {"selection_context": {"scenario": "reduce"}},
    )
    exit_semantics = infer_intent_semantics(
        {"decision": "exit", "rationale_codes": ["STOP_LOSS_TRIGGERED"]},
        {"selection_context": {"scenario": "exit"}},
    )

    assert reduce_semantics["intent_family"] == "management"
    assert reduce_semantics["management_action"] == "reduce_only"
    assert reduce_semantics["analytics_excluded_from_entry_stats"] is True
    assert exit_semantics["intent_family"] == "exit"
    assert exit_semantics["management_action"] == "exit_only"
    assert exit_semantics["analytics_excluded_from_entry_stats"] is True
