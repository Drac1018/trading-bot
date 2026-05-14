from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from trading_mvp.models import Execution, Order, Position
from trading_mvp.schemas import MarketCandle, MarketSnapshotPayload
from trading_mvp.services.ai_context import build_ai_decision_context
from trading_mvp.services.features import compute_features
from trading_mvp.services.orchestrator import TradingOrchestrator
from trading_mvp.time_utils import utcnow_naive


def _market_features(symbol: str = "BTCUSDT"):
    now = utcnow_naive()
    closes = [100.0, 100.4, 100.9, 101.3, 101.8, 102.2, 102.7, 103.0, 103.4, 103.8, 104.1, 104.5]
    candles = []
    for index, close in enumerate(closes):
        previous = closes[index - 1] if index else close
        candles.append(
            MarketCandle(
                timestamp=now - timedelta(minutes=15 * (len(closes) - index)),
                open=previous,
                high=max(previous, close) * 1.001,
                low=min(previous, close) * 0.999,
                close=close,
                volume=1000.0 + index,
            )
        )
    snapshot = MarketSnapshotPayload(
        symbol=symbol,
        timeframe="15m",
        snapshot_time=now,
        latest_price=closes[-1],
        latest_volume=1100.0,
        candle_count=len(candles),
        is_stale=False,
        is_complete=True,
        candles=candles,
    )
    return snapshot, compute_features(snapshot, {})


def _closed_position_with_executions(
    db_session,
    *,
    symbol: str = "BTCUSDT",
    side: str = "short",
    closed_at=None,
    close_order_type: str = "take_profit_market",
    protective_component: str | None = "take_profit",
    exchange_order_type: str = "TAKE_PROFIT_MARKET",
    close_order_side: str = "buy",
    realized_pnl: float = 1.2,
) -> Position:
    closed_at = closed_at or utcnow_naive()
    position = Position(
        symbol=symbol,
        side=side,
        status="closed",
        mode="live",
        quantity=0.01,
        entry_price=100.0,
        mark_price=98.8,
        leverage=3.0,
        stop_loss=101.0,
        take_profit=98.8,
        realized_pnl=realized_pnl,
        unrealized_pnl=0.0,
        opened_at=closed_at - timedelta(minutes=20),
        closed_at=closed_at,
        metadata_json={},
    )
    db_session.add(position)
    db_session.flush()

    entry_order = Order(
        symbol=symbol,
        position_id=position.id,
        side="sell" if side == "short" else "buy",
        order_type="limit",
        mode="live",
        status="filled",
        requested_quantity=0.01,
        requested_price=100.0,
        filled_quantity=0.01,
        average_fill_price=100.0,
        reduce_only=False,
        close_only=False,
        metadata_json={},
    )
    close_order = Order(
        symbol=symbol,
        position_id=position.id,
        side=close_order_side,
        order_type=close_order_type,
        mode="live",
        status="filled",
        requested_quantity=0.01,
        requested_price=98.8,
        filled_quantity=0.01,
        average_fill_price=98.8,
        reduce_only=True,
        close_only=True,
        metadata_json=(
            {"protective_component": protective_component}
            if protective_component is not None
            else {}
        ),
    )
    db_session.add_all([entry_order, close_order])
    db_session.flush()

    db_session.add_all(
        [
            Execution(
                order_id=entry_order.id,
                position_id=position.id,
                symbol=symbol,
                status="filled",
                fill_price=100.0,
                fill_quantity=0.01,
                fee_paid=0.05,
                slippage_pct=0.0,
                realized_pnl=0.0,
                payload={"exchange_order": {"type": "LIMIT"}},
                created_at=closed_at - timedelta(minutes=19),
            ),
            Execution(
                order_id=close_order.id,
                position_id=position.id,
                symbol=symbol,
                status="filled",
                fill_price=98.8,
                fill_quantity=0.01,
                fee_paid=0.1,
                slippage_pct=0.0,
                realized_pnl=realized_pnl,
                payload={"exchange_order": {"type": exchange_order_type}},
                created_at=closed_at,
            ),
        ]
    )
    db_session.flush()
    return position


def test_recent_same_direction_tp_summary_uses_position_side_not_close_order_side(db_session) -> None:
    generated_at = utcnow_naive()
    _closed_position_with_executions(
        db_session,
        side="short",
        close_order_side="buy",
        closed_at=generated_at - timedelta(minutes=4),
    )

    summary = TradingOrchestrator._recent_same_direction_tp_close_summary(
        db_session,
        symbol="BTCUSDT",
        direction="short",
        generated_at=generated_at,
        lookback_minutes=15,
    )

    assert summary is not None
    assert summary["side"] == "short"
    assert summary["close_reason"] == "take_profit_market"
    assert summary["recent_tp_gross_pnl"] == 1.2
    assert summary["recent_tp_fee"] == 0.15
    assert summary["recent_tp_net_pnl"] == 1.05
    assert summary["recent_tp_fee_to_gross_ratio"] == 0.125


def test_recent_same_direction_tp_summary_filters_symbol_side_reason_and_lookback(db_session) -> None:
    generated_at = utcnow_naive()
    _closed_position_with_executions(
        db_session,
        symbol="BTCUSDT",
        side="short",
        closed_at=generated_at - timedelta(minutes=5),
    )
    _closed_position_with_executions(
        db_session,
        symbol="SOLUSDT",
        side="short",
        closed_at=generated_at - timedelta(minutes=2),
        close_order_type="market",
        protective_component=None,
        exchange_order_type="MARKET",
        realized_pnl=0.7,
    )

    assert (
        TradingOrchestrator._recent_same_direction_tp_close_summary(
            db_session,
            symbol="ETHUSDT",
            direction="short",
            generated_at=generated_at,
            lookback_minutes=15,
        )
        is None
    )
    assert (
        TradingOrchestrator._recent_same_direction_tp_close_summary(
            db_session,
            symbol="BTCUSDT",
            direction="long",
            generated_at=generated_at,
            lookback_minutes=15,
        )
        is None
    )
    assert (
        TradingOrchestrator._recent_same_direction_tp_close_summary(
            db_session,
            symbol="BTCUSDT",
            direction="short",
            generated_at=generated_at,
            lookback_minutes=1,
        )
        is None
    )
    assert (
        TradingOrchestrator._recent_same_direction_tp_close_summary(
            db_session,
            symbol="SOLUSDT",
            direction="short",
            generated_at=generated_at,
            lookback_minutes=15,
        )
        is None
    )


def test_recent_tp_risk_context_copy_feeds_ai_warning_without_mutating_source(db_session) -> None:
    generated_at = utcnow_naive()
    _closed_position_with_executions(
        db_session,
        symbol="BTCUSDT",
        side="short",
        closed_at=generated_at - timedelta(minutes=3),
    )
    orchestrator = TradingOrchestrator(db_session)
    original_risk_context = {"execution_constraints_summary": {"risk_guard_final_authority": True}}

    ai_risk_context = orchestrator._risk_context_with_recent_tp_close_summary(
        original_risk_context,
        symbol="BTCUSDT",
        direction="short",
        generated_at=generated_at,
        lookback_minutes=15,
    )

    assert "recent_closed_position_summary" not in original_risk_context
    assert ai_risk_context is not original_risk_context
    assert ai_risk_context["recent_closed_position_summary"]["side"] == "short"

    snapshot, features = _market_features("BTCUSDT")
    context = build_ai_decision_context(
        market_snapshot=snapshot,
        features=features,
        risk_context=ai_risk_context,
        selection_context={
            "strategy_engine": "trend_pullback_engine",
            "holding_profile": "scalp",
            "candidate": {"decision": "short"},
        },
        decision_reference={},
    )

    assert context.strategy_engine_context["same_direction_reentry_warning"][
        "recent_same_direction_tp_close"
    ] is True


def test_recent_tp_direction_prefers_pending_plan_side_and_lookback_uses_plan_ttl() -> None:
    selection_context = {"candidate": {"decision": "long"}, "decision_hint": "long"}
    pending_plan = SimpleNamespace(side="short", direction="long", idea_ttl_minutes=30)

    assert (
        TradingOrchestrator._recent_tp_direction_from_pending_entry_plan(
            pending_plan,  # type: ignore[arg-type]
            selection_context,
        )
        == "short"
    )
    assert (
        TradingOrchestrator._recent_tp_close_lookback_minutes(
            cadence_profile={
                "effective_cadence": {"decision_cycle_interval_minutes": 5},
                "holding_profile_cadence_hint": {"decision_interval_minutes": 15},
            },
            selection_context={"candidate": {"holding_profile": "scalp"}},
            ai_call_interval_minutes=60,
            pending_entry_plan=pending_plan,  # type: ignore[arg-type]
        )
        == 30
    )


def test_recent_tp_direction_skips_hold_candidates_and_lookback_uses_existing_cadence() -> None:
    assert (
        TradingOrchestrator._recent_tp_direction_from_selection_context(
            {"candidate": {"decision": "hold"}}
        )
        is None
    )
    assert (
        TradingOrchestrator._recent_tp_close_lookback_minutes(
            cadence_profile={
                "effective_cadence": {
                    "entry_plan_watcher_interval_minutes": 1,
                    "decision_cycle_interval_minutes": 5,
                },
                "holding_profile_cadence_hint": {"decision_interval_minutes": 15},
            }
        )
        == 15
    )


def test_recent_tp_new_entry_lookback_includes_settings_ai_call_interval() -> None:
    assert (
        TradingOrchestrator._recent_tp_close_lookback_minutes(
            cadence_profile={
                "effective_cadence": {
                    "entry_plan_watcher_interval_minutes": None,
                    "decision_cycle_interval_minutes": 1,
                },
                "holding_profile_cadence_hint": {},
            },
            selection_context={},
            ai_call_interval_minutes=5,
        )
        == 5
    )


def test_recent_tp_new_entry_lookback_uses_candidate_holding_profile_hint(db_session) -> None:
    generated_at = utcnow_naive()
    _closed_position_with_executions(
        db_session,
        symbol="BTCUSDT",
        side="short",
        closed_at=generated_at - timedelta(minutes=6.0924),
    )
    lookback_minutes = TradingOrchestrator._recent_tp_close_lookback_minutes(
        cadence_profile={
            "effective_cadence": {
                "entry_plan_watcher_interval_minutes": None,
                "decision_cycle_interval_minutes": 1,
            },
            "holding_profile_cadence_hint": {},
        },
        selection_context={"candidate": {"decision": "short", "holding_profile": "scalp"}},
        ai_call_interval_minutes=5,
    )

    assert lookback_minutes == 15
    summary = TradingOrchestrator._recent_same_direction_tp_close_summary(
        db_session,
        symbol="BTCUSDT",
        direction="short",
        generated_at=generated_at,
        lookback_minutes=lookback_minutes,
    )

    assert summary is not None
    assert summary["recent_same_direction_tp_close"] is True
    assert summary["minutes_since_recent_same_direction_tp"] == 6.0924


def test_recent_tp_new_entry_settings_lookback_catches_sub_two_minute_cases(db_session) -> None:
    generated_at = utcnow_naive()
    _closed_position_with_executions(
        db_session,
        symbol="BTCUSDT",
        side="short",
        closed_at=generated_at - timedelta(minutes=1.3191),
    )
    lookback_minutes = TradingOrchestrator._recent_tp_close_lookback_minutes(
        cadence_profile={
            "effective_cadence": {
                "entry_plan_watcher_interval_minutes": None,
                "decision_cycle_interval_minutes": 1,
            },
            "holding_profile_cadence_hint": {},
        },
        selection_context={},
        ai_call_interval_minutes=5,
    )

    summary = TradingOrchestrator._recent_same_direction_tp_close_summary(
        db_session,
        symbol="BTCUSDT",
        direction="short",
        generated_at=generated_at,
        lookback_minutes=lookback_minutes,
    )

    assert lookback_minutes == 5
    assert summary is not None
    assert summary["minutes_since_recent_same_direction_tp"] == 1.3191


def test_recent_tp_new_entry_profile_lookback_still_excludes_old_tp(db_session) -> None:
    generated_at = utcnow_naive()
    _closed_position_with_executions(
        db_session,
        symbol="BTCUSDT",
        side="short",
        closed_at=generated_at - timedelta(minutes=16),
    )
    lookback_minutes = TradingOrchestrator._recent_tp_close_lookback_minutes(
        cadence_profile={
            "effective_cadence": {
                "entry_plan_watcher_interval_minutes": None,
                "decision_cycle_interval_minutes": 1,
            },
            "holding_profile_cadence_hint": {},
        },
        selection_context={"candidate": {"decision": "short", "holding_profile": "scalp"}},
        ai_call_interval_minutes=5,
    )

    assert lookback_minutes == 15
    assert (
        TradingOrchestrator._recent_same_direction_tp_close_summary(
            db_session,
            symbol="BTCUSDT",
            direction="short",
            generated_at=generated_at,
            lookback_minutes=lookback_minutes,
        )
        is None
    )
