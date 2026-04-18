from __future__ import annotations

from datetime import timedelta

import pytest
from trading_mvp.models import AgentRun, Execution, Order, Position
from trading_mvp.services.orchestrator import TradingOrchestrator
from trading_mvp.services.strategy_engine_analytics import build_strategy_engine_bucket_report
from trading_mvp.time_utils import utcnow_naive


def _seed_strategy_engine_trade(
    db_session,
    *,
    symbol: str,
    created_at,
    strategy_engine: str,
    session_label: str,
    time_of_day_bucket: str,
    timeframe: str = "15m",
    scenario: str = "pullback_entry",
    decision: str = "long",
    entry_mode: str = "pullback_confirm",
    primary_regime: str = "bullish",
    trend_alignment: str = "bullish_aligned",
    execution_policy_profile: str = "entry_btc_fast_calm",
    net_pnl_after_fees: float,
    signed_slippage_bps: float,
    time_to_profit_minutes: float | None,
    drawdown_impact: float,
) -> AgentRun:
    decision_row = AgentRun(
        role="trading_decision",
        trigger_event="interval_decision_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="deterministic-mock",
        summary="strategy engine seed",
        input_payload={
            "features": {
                "regime": {
                    "primary_regime": primary_regime,
                    "trend_alignment": trend_alignment,
                }
            }
        },
        output_payload={
            "symbol": symbol,
            "timeframe": timeframe,
            "decision": decision,
            "entry_mode": entry_mode,
            "rationale_codes": [f"ENGINE_{strategy_engine.upper()}"],
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
                    "priority": 0.84,
                    "reasons": ["TEST_ENGINE"],
                },
                "candidates": [],
                "session_context": {
                    "session_label": session_label,
                    "time_of_day_bucket": time_of_day_bucket,
                },
            },
            "selection_context": {
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
        symbol=symbol,
        mode="live",
        side="long" if decision == "long" else "short",
        status="closed",
        quantity=1.0,
        entry_price=100.0,
        mark_price=100.0,
        leverage=2.0,
        stop_loss=95.0 if decision == "long" else 105.0,
        take_profit=110.0 if decision == "long" else 90.0,
        realized_pnl=net_pnl_after_fees + 1.0,
        unrealized_pnl=0.0,
        metadata_json={
            "capital_efficiency": {
                "time_to_0_25r_minutes": time_to_profit_minutes,
                "mae_r": -abs(drawdown_impact),
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
        symbol=symbol,
        decision_run_id=decision_row.id,
        position_id=position.id,
        side="buy" if decision == "long" else "sell",
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
        symbol=symbol,
        status="filled",
        external_trade_id=f"{symbol}-{decision_row.id}",
        fill_price=100.0,
        fill_quantity=1.0,
        fee_paid=1.0,
        commission_asset="USDT",
        slippage_pct=abs(signed_slippage_bps) / 10000.0,
        realized_pnl=net_pnl_after_fees + 1.0,
        payload={
            "signed_slippage_bps": signed_slippage_bps,
        },
        created_at=created_at + timedelta(minutes=60),
        updated_at=created_at + timedelta(minutes=60),
    )
    db_session.add(execution)
    db_session.flush()
    return decision_row


def _seed_strategy_engine_dataset(db_session) -> None:
    now = utcnow_naive() - timedelta(hours=8)
    _seed_strategy_engine_trade(
        db_session,
        symbol="BTCUSDT",
        created_at=now,
        strategy_engine="trend_pullback_engine",
        session_label="asia",
        time_of_day_bucket="utc_00_05",
        net_pnl_after_fees=14.0,
        signed_slippage_bps=3.5,
        time_to_profit_minutes=18.0,
        drawdown_impact=0.22,
    )
    _seed_strategy_engine_trade(
        db_session,
        symbol="BTCUSDT",
        created_at=now + timedelta(hours=2),
        strategy_engine="trend_pullback_engine",
        session_label="asia",
        time_of_day_bucket="utc_00_05",
        net_pnl_after_fees=8.0,
        signed_slippage_bps=4.5,
        time_to_profit_minutes=24.0,
        drawdown_impact=0.28,
    )
    _seed_strategy_engine_trade(
        db_session,
        symbol="ETHUSDT",
        created_at=now + timedelta(hours=3),
        strategy_engine="breakout_exception_engine",
        session_label="us",
        time_of_day_bucket="utc_12_17",
        scenario="trend_follow",
        entry_mode="breakout_confirm",
        execution_policy_profile="entry_alt_fast_expanded",
        net_pnl_after_fees=-10.0,
        signed_slippage_bps=14.0,
        time_to_profit_minutes=None,
        drawdown_impact=0.95,
    )


def test_build_strategy_engine_bucket_report_aggregates_engine_buckets(db_session) -> None:
    _seed_strategy_engine_dataset(db_session)

    report = build_strategy_engine_bucket_report(db_session, lookback_days=21, limit=64)

    pullback_bucket = next(item for item in report.bucket_reports if item.strategy_engine == "trend_pullback_engine")
    assert pullback_bucket.decisions == 2
    assert pullback_bucket.traded_decisions == 2
    assert pullback_bucket.expectancy == pytest.approx(11.0, rel=1e-6)
    assert pullback_bucket.net_pnl_after_fees == pytest.approx(22.0, rel=1e-6)
    assert pullback_bucket.avg_signed_slippage_bps == pytest.approx(4.0, rel=1e-6)
    assert pullback_bucket.average_time_to_profit_minutes == pytest.approx(21.0, rel=1e-6)
    assert pullback_bucket.average_drawdown_impact == pytest.approx(0.25, rel=1e-6)
    assert pullback_bucket.classification == "strong"


def test_build_strategy_engine_bucket_report_marks_weak_engine_buckets(db_session) -> None:
    _seed_strategy_engine_dataset(db_session)

    report = build_strategy_engine_bucket_report(db_session, lookback_days=21, limit=64)

    breakout_bucket = next(item for item in report.bucket_reports if item.strategy_engine == "breakout_exception_engine")
    assert breakout_bucket.decisions == 1
    assert breakout_bucket.traded_decisions == 1
    assert breakout_bucket.net_pnl_after_fees == pytest.approx(-10.0, rel=1e-6)
    assert breakout_bucket.avg_signed_slippage_bps == pytest.approx(14.0, rel=1e-6)
    assert breakout_bucket.average_drawdown_impact == pytest.approx(0.95, rel=1e-6)
    assert breakout_bucket.classification == "weak"
    assert breakout_bucket.bucket_key in report.weak_engine_bucket_keys


def test_orchestrator_build_strategy_engine_bucket_report_wrapper(db_session) -> None:
    _seed_strategy_engine_dataset(db_session)

    report = TradingOrchestrator(db_session).build_strategy_engine_bucket_report(lookback_days=21, limit=64)

    assert report.decisions_analyzed == 3
    assert report.traded_decisions == 3
    assert report.bucket_reports[0].efficiency_score >= report.bucket_reports[-1].efficiency_score
