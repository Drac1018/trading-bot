from __future__ import annotations

from datetime import timedelta

import pytest
from trading_mvp.models import AgentRun, Execution, Order, Position
from trading_mvp.services.capital_efficiency import build_capital_efficiency_report
from trading_mvp.services.orchestrator import TradingOrchestrator
from trading_mvp.time_utils import utcnow_naive


def _seed_efficiency_trade(
    db_session,
    *,
    symbol: str,
    created_at,
    timeframe: str = "15m",
    decision: str = "long",
    entry_mode: str = "pullback_confirm",
    primary_regime: str = "bullish",
    trend_alignment: str = "bullish_aligned",
    execution_policy_profile: str = "entry_btc_fast_calm",
    exposure_minutes: int,
    gross_pnl: float,
    fee_total: float,
    time_to_0_25r_minutes: float | None = None,
    time_to_0_5r_minutes: float | None = None,
    time_to_fail_minutes: float | None = None,
    reached_0_25r: bool | None = None,
    reached_0_5r: bool | None = None,
    failed_before_0_25r: bool | None = None,
) -> AgentRun:
    decision_row = AgentRun(
        role="trading_decision",
        trigger_event="interval_decision_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="deterministic-mock",
        summary="capital efficiency seed",
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
            "rationale_codes": ["TEST_EFFICIENCY"],
            "confidence": 0.68,
            "risk_pct": 0.01,
            "leverage": 2.0,
        },
        metadata_json={
            "selection_context": {
                "execution_policy_profile": execution_policy_profile,
            }
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
        realized_pnl=gross_pnl,
        unrealized_pnl=0.0,
        metadata_json={
            "capital_efficiency": {
                "time_to_0_25r_minutes": time_to_0_25r_minutes,
                "time_to_0_5r_minutes": time_to_0_5r_minutes,
                "time_to_fail_minutes": time_to_fail_minutes,
                "reached_0_25r": reached_0_25r,
                "reached_0_5r": reached_0_5r,
                "failed_before_0_25r": failed_before_0_25r,
            }
        },
        opened_at=created_at,
        closed_at=created_at + timedelta(minutes=exposure_minutes),
        created_at=created_at,
        updated_at=created_at,
    )
    db_session.add(position)
    db_session.flush()

    entry_order = Order(
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
    close_order = Order(
        symbol=symbol,
        decision_run_id=decision_row.id,
        position_id=position.id,
        side="sell" if decision == "long" else "buy",
        order_type="TAKE_PROFIT_MARKET" if gross_pnl >= 0 else "STOP_MARKET",
        mode="live",
        status="filled",
        requested_quantity=1.0,
        requested_price=108.0 if gross_pnl >= 0 else 96.0,
        filled_quantity=1.0,
        average_fill_price=108.0 if gross_pnl >= 0 else 96.0,
        reduce_only=True,
        close_only=True,
        reason_codes=[],
        metadata_json={"execution_quality": {"policy_profile": execution_policy_profile}},
        created_at=created_at + timedelta(minutes=max(exposure_minutes - 1, 0)),
        updated_at=created_at + timedelta(minutes=max(exposure_minutes - 1, 0)),
    )
    db_session.add_all([entry_order, close_order])
    db_session.flush()

    exit_execution = Execution(
        order_id=close_order.id,
        position_id=position.id,
        symbol=symbol,
        status="filled",
        external_trade_id=f"{symbol}-{decision_row.id}",
        fill_price=close_order.average_fill_price or 0.0,
        fill_quantity=1.0,
        fee_paid=fee_total,
        commission_asset="USDT",
        slippage_pct=0.001,
        realized_pnl=gross_pnl,
        payload={},
        created_at=created_at + timedelta(minutes=exposure_minutes),
        updated_at=created_at + timedelta(minutes=exposure_minutes),
    )
    db_session.add(exit_execution)
    db_session.flush()
    return decision_row


def _seed_efficiency_dataset(db_session) -> None:
    now = utcnow_naive() - timedelta(hours=6)
    _seed_efficiency_trade(
        db_session,
        symbol="BTCUSDT",
        created_at=now,
        exposure_minutes=120,
        gross_pnl=24.0,
        fee_total=4.0,
        time_to_0_25r_minutes=20.0,
        time_to_0_5r_minutes=45.0,
        reached_0_25r=True,
        reached_0_5r=True,
        execution_policy_profile="entry_btc_fast_calm",
    )
    _seed_efficiency_trade(
        db_session,
        symbol="BTCUSDT",
        created_at=now + timedelta(hours=3),
        exposure_minutes=60,
        gross_pnl=9.0,
        fee_total=1.0,
        time_to_0_25r_minutes=15.0,
        time_to_0_5r_minutes=35.0,
        reached_0_25r=True,
        reached_0_5r=True,
        execution_policy_profile="entry_btc_fast_calm",
    )
    _seed_efficiency_trade(
        db_session,
        symbol="ETHUSDT",
        created_at=now + timedelta(hours=4),
        primary_regime="transition",
        trend_alignment="mixed",
        entry_mode="breakout_confirm",
        execution_policy_profile="entry_alt_fast_expanded",
        exposure_minutes=60,
        gross_pnl=-12.0,
        fee_total=1.0,
        time_to_fail_minutes=18.0,
        reached_0_25r=False,
        reached_0_5r=False,
        failed_before_0_25r=True,
    )


def test_build_capital_efficiency_report_computes_exposure_hour_metrics(db_session) -> None:
    _seed_efficiency_dataset(db_session)

    report = build_capital_efficiency_report(db_session, lookback_days=21, limit=64)

    btc_bucket = next(item for item in report.bucket_reports if item.symbol == "BTCUSDT")
    assert btc_bucket.decisions == 2
    assert btc_bucket.traded_decisions == 2
    assert btc_bucket.total_exposure_hours == pytest.approx(3.0, rel=1e-6)
    assert btc_bucket.gross_pnl == pytest.approx(33.0, rel=1e-6)
    assert btc_bucket.net_pnl_after_fees == pytest.approx(28.0, rel=1e-6)
    assert btc_bucket.pnl_per_exposure_hour == pytest.approx(11.0, rel=1e-6)
    assert btc_bucket.net_pnl_after_fees_per_hour == pytest.approx(28.0 / 3.0, rel=1e-6)
    assert btc_bucket.average_time_to_0_25r_minutes == pytest.approx(17.5, rel=1e-6)
    assert btc_bucket.average_time_to_0_5r_minutes == pytest.approx(40.0, rel=1e-6)
    assert btc_bucket.average_time_to_fail_minutes is None
    assert btc_bucket.capital_slot_occupancy_efficiency == pytest.approx(2.0 / 3.0, rel=1e-6)


def test_build_capital_efficiency_report_aggregates_buckets_and_classifies(db_session) -> None:
    _seed_efficiency_dataset(db_session)

    report = build_capital_efficiency_report(db_session, lookback_days=21, limit=64)

    assert report.decisions_analyzed == 3
    assert report.traded_decisions == 3
    assert report.total_exposure_hours == pytest.approx(4.0, rel=1e-6)
    assert len(report.bucket_reports) == 2

    btc_bucket = next(item for item in report.bucket_reports if item.symbol == "BTCUSDT")
    eth_bucket = next(item for item in report.bucket_reports if item.symbol == "ETHUSDT")
    assert btc_bucket.efficiency_classification == "efficient"
    assert "POSITIVE_NET_PER_HOUR" in btc_bucket.reasons
    assert eth_bucket.efficiency_classification == "inefficient"
    assert eth_bucket.average_time_to_fail_minutes == pytest.approx(18.0, rel=1e-6)
    assert eth_bucket.fail_before_0_25r_rate == 1.0
    assert btc_bucket.bucket_key in report.efficient_bucket_keys
    assert eth_bucket.bucket_key in report.inefficient_bucket_keys


def test_orchestrator_build_capital_efficiency_report_wrapper(db_session) -> None:
    _seed_efficiency_dataset(db_session)

    report = TradingOrchestrator(db_session).build_capital_efficiency_report(lookback_days=21, limit=64)

    assert report.decisions_analyzed == 3
    assert report.traded_decisions == 3
    assert report.total_exposure_hours == pytest.approx(4.0, rel=1e-6)
    assert report.bucket_reports[0].net_pnl_after_fees_per_hour >= report.bucket_reports[-1].net_pnl_after_fees_per_hour
