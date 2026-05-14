from __future__ import annotations

from datetime import timedelta

import pytest
from trading_mvp.models import AuditEvent, Execution, Order, Position
from trading_mvp.services.strategy_performance_report import (
    NO_DATA_MESSAGE,
    StrategyPerformanceFilters,
    build_strategy_performance_report,
    format_console_report,
    format_csv_report,
    format_json_report,
)
from trading_mvp.time_utils import utcnow_naive


def _seed_closed_trade(
    db_session,
    *,
    symbol: str,
    side: str,
    strategy_id: str,
    regime_id: str,
    confirmation_type: str,
    risk_mode: str,
    net_pnl: float,
    gross_pnl: float,
    fee_total: float,
    net_r_multiple: float,
    hold_minutes: int,
    slippage_bps: float,
    mode: str = "live",
) -> Position:
    closed_at = utcnow_naive()
    opened_at = closed_at - timedelta(minutes=hold_minutes)
    tags = {
        "strategy_id": strategy_id,
        "strategy_engine": strategy_id,
        "regime_id": regime_id,
        "regime_label": regime_id.split(":", 1)[0],
        "entry_confirmation_type": confirmation_type,
        "risk_mode": risk_mode,
        "symbol": symbol,
        "timeframe": "15m",
        "decision": side,
    }
    position = Position(
        symbol=symbol,
        mode=mode,
        side=side,
        status="closed",
        quantity=1.0,
        entry_price=100.0,
        mark_price=100.0,
        leverage=2.0,
        stop_loss=95.0 if side == "long" else 105.0,
        take_profit=110.0 if side == "long" else 90.0,
        realized_pnl=gross_pnl,
        unrealized_pnl=0.0,
        opened_at=opened_at,
        closed_at=closed_at,
        metadata_json={
            "trade_performance_tags": tags,
            "closed_position_pnl": {
                "gross_realized_pnl": gross_pnl,
                "fee_total": fee_total,
                "net_realized_pnl": net_pnl,
                "net_r_multiple": net_r_multiple,
                "trade_performance_tags": tags,
            },
        },
        created_at=opened_at,
        updated_at=closed_at,
    )
    db_session.add(position)
    db_session.flush()
    order = Order(
        symbol=symbol,
        position_id=position.id,
        side="buy" if side == "long" else "sell",
        order_type="limit",
        mode=mode,
        status="filled",
        requested_quantity=1.0,
        requested_price=100.0,
        filled_quantity=1.0,
        average_fill_price=100.0,
        reduce_only=False,
        close_only=False,
        metadata_json={"trade_performance_tags": tags},
        created_at=opened_at,
        updated_at=opened_at,
    )
    db_session.add(order)
    db_session.flush()
    execution = Execution(
        order_id=order.id,
        position_id=position.id,
        symbol=symbol,
        status="filled",
        external_trade_id=f"{symbol}-{position.id}",
        fill_price=100.0,
        fill_quantity=1.0,
        fee_paid=fee_total,
        commission_asset="USDT",
        slippage_pct=slippage_bps / 10_000.0,
        realized_pnl=gross_pnl,
        payload={"signed_slippage_bps": slippage_bps, "trade_performance_tags": tags},
        created_at=closed_at,
        updated_at=closed_at,
    )
    db_session.add(execution)
    db_session.flush()
    return position


def _bucket_by_strategy(report, strategy_id: str):
    for bucket in report.buckets:
        if bucket.group["strategy_id"] == strategy_id:
            return bucket
    raise AssertionError(f"missing bucket for {strategy_id}")


def test_strategy_performance_report_aggregates_by_trade_tags(db_session) -> None:
    _seed_closed_trade(
        db_session,
        symbol="BTCUSDT",
        side="long",
        strategy_id="range_mean_reversion_engine",
        regime_id="range:range",
        confirmation_type="range_edge_confirm",
        risk_mode="normal",
        net_pnl=100.0,
        gross_pnl=112.0,
        fee_total=12.0,
        net_r_multiple=1.0,
        hold_minutes=60,
        slippage_bps=2.0,
    )
    _seed_closed_trade(
        db_session,
        symbol="BTCUSDT",
        side="long",
        strategy_id="range_mean_reversion_engine",
        regime_id="range:range",
        confirmation_type="range_edge_confirm",
        risk_mode="normal",
        net_pnl=-50.0,
        gross_pnl=-45.0,
        fee_total=5.0,
        net_r_multiple=-0.5,
        hold_minutes=30,
        slippage_bps=4.0,
    )
    _seed_closed_trade(
        db_session,
        symbol="ETHUSDT",
        side="short",
        strategy_id="trend_pullback_engine",
        regime_id="bearish:bearish_aligned",
        confirmation_type="pullback_confirm",
        risk_mode="drawdown_recovery",
        net_pnl=80.0,
        gross_pnl=86.0,
        fee_total=6.0,
        net_r_multiple=0.8,
        hold_minutes=45,
        slippage_bps=1.0,
    )

    report = build_strategy_performance_report(db_session)

    assert report.trade_count == 3
    assert len(report.buckets) == 2
    range_bucket = _bucket_by_strategy(report, "range_mean_reversion_engine")
    assert range_bucket.group["symbol"] == "BTCUSDT"
    assert range_bucket.group["direction"] == "long"
    assert range_bucket.trade_count == 2
    assert range_bucket.win_rate == pytest.approx(0.5)
    assert range_bucket.avg_win == pytest.approx(100.0)
    assert range_bucket.avg_loss == pytest.approx(-50.0)
    assert range_bucket.avg_R == pytest.approx(0.25)
    assert range_bucket.profit_factor == pytest.approx(2.0)
    assert range_bucket.max_loss_streak == 1
    assert range_bucket.avg_hold_time == pytest.approx(45.0)
    assert range_bucket.avg_slippage_bps == pytest.approx(3.0)
    assert range_bucket.fee_adjusted_pnl == pytest.approx(50.0)


def test_strategy_performance_report_filters_symbol_direction_and_risk_mode(db_session) -> None:
    _seed_closed_trade(
        db_session,
        symbol="BTCUSDT",
        side="long",
        strategy_id="range_mean_reversion_engine",
        regime_id="range:range",
        confirmation_type="range_edge_confirm",
        risk_mode="normal",
        net_pnl=20.0,
        gross_pnl=22.0,
        fee_total=2.0,
        net_r_multiple=0.2,
        hold_minutes=25,
        slippage_bps=2.0,
    )
    _seed_closed_trade(
        db_session,
        symbol="ETHUSDT",
        side="short",
        strategy_id="trend_pullback_engine",
        regime_id="bearish:bearish_aligned",
        confirmation_type="pullback_confirm",
        risk_mode="drawdown_recovery",
        net_pnl=80.0,
        gross_pnl=86.0,
        fee_total=6.0,
        net_r_multiple=0.8,
        hold_minutes=45,
        slippage_bps=1.0,
    )

    report = build_strategy_performance_report(
        db_session,
        filters=StrategyPerformanceFilters(
            symbol="ETHUSDT",
            direction="short",
            risk_mode="drawdown_recovery",
        ),
    )

    assert report.trade_count == 1
    assert report.buckets[0].group["strategy_id"] == "trend_pullback_engine"
    assert report.buckets[0].group["risk_mode"] == "drawdown_recovery"


def test_strategy_performance_report_empty_dataset_formats_no_data(db_session) -> None:
    report = build_strategy_performance_report(db_session)

    assert report.trade_count == 0
    assert report.message == NO_DATA_MESSAGE
    assert format_console_report(report) == NO_DATA_MESSAGE
    assert "trade_count" in format_csv_report(report)
    assert NO_DATA_MESSAGE in format_json_report(report)


def test_strategy_performance_report_tolerates_missing_optional_fields_and_uses_audit_tags(db_session) -> None:
    closed_at = utcnow_naive()
    position = Position(
        symbol="BTCUSDT",
        mode="paper",
        side="long",
        status="closed",
        quantity=1.0,
        entry_price=100.0,
        mark_price=100.0,
        leverage=1.0,
        stop_loss=95.0,
        take_profit=110.0,
        realized_pnl=7.5,
        unrealized_pnl=0.0,
        opened_at=closed_at - timedelta(minutes=12),
        closed_at=closed_at,
        metadata_json={},
    )
    db_session.add(position)
    db_session.flush()
    db_session.add(
        AuditEvent(
            event_type="live_execution",
            entity_type="position",
            entity_id=str(position.id),
            severity="info",
            message="mock audit trade tags",
            payload={
                "trade_performance_tags": {
                    "strategy_id": "range_mean_reversion_engine",
                    "regime_id": "range:range",
                    "entry_confirmation_type": "range_edge_confirm",
                    "risk_mode": "normal",
                    "symbol": "BTCUSDT",
                    "decision": "long",
                }
            },
        )
    )
    db_session.flush()

    report = build_strategy_performance_report(db_session, filters=StrategyPerformanceFilters(mode="paper"))

    assert report.trade_count == 1
    bucket = report.buckets[0]
    assert bucket.group["strategy_id"] == "range_mean_reversion_engine"
    assert bucket.group["confirmation_type"] == "range_edge_confirm"
    assert bucket.fee_adjusted_pnl == pytest.approx(7.5)
