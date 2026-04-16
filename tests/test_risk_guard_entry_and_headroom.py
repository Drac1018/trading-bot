from __future__ import annotations

from datetime import date, timedelta

import pytest

from trading_mvp.models import AgentRun, MarketSnapshot, Order, PnLSnapshot, Position, RiskCheck
from trading_mvp.schemas import TradeDecision
from trading_mvp.services.dashboard import get_operator_dashboard, get_overview
from trading_mvp.services.market_data import build_market_snapshot
from trading_mvp.services.risk import evaluate_risk
from trading_mvp.services.runtime_state import mark_sync_success
from trading_mvp.services.secret_store import encrypt_secret
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive


def _mark_all_sync_scopes_fresh(settings_row) -> None:
    now = utcnow_naive()
    for scope in ("account", "positions", "open_orders", "protective_orders"):
        mark_sync_success(settings_row, scope=scope, synced_at=now)


def _entry_decision(
    *,
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    decision: str = "long",
    entry_zone_min: float = 65000.0,
    entry_zone_max: float = 65100.0,
    stop_loss: float = 64000.0,
    take_profit: float = 66500.0,
    entry_mode: str = "immediate",
    invalidation_price: float | None = None,
    max_chase_bps: float | None = 25.0,
    risk_pct: float = 0.01,
    leverage: float = 2.0,
) -> TradeDecision:
    return TradeDecision(
        decision=decision,  # type: ignore[arg-type]
        confidence=0.7,
        symbol=symbol,
        timeframe=timeframe,
        entry_zone_min=entry_zone_min,
        entry_zone_max=entry_zone_max,
        entry_mode=entry_mode,  # type: ignore[arg-type]
        invalidation_price=stop_loss if invalidation_price is None else invalidation_price,
        max_chase_bps=max_chase_bps,
        idea_ttl_minutes=15,
        stop_loss=stop_loss,
        take_profit=take_profit,
        max_holding_minutes=120,
        risk_pct=risk_pct,
        leverage=leverage,
        rationale_codes=["TEST"],
        explanation_short="risk guard regression",
        explanation_detailed="Regression coverage for entry trigger and headroom debug behaviour.",
    )


def _seed_live_ready_settings(db_session):
    settings_row = get_or_create_settings(db_session)
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    _mark_all_sync_scopes_fresh(settings_row)
    db_session.add(settings_row)
    db_session.flush()
    return settings_row


class _RiskFilterClient:
    def __init__(self, *, tick_size: float = 0.1, step_size: float = 0.001, min_qty: float = 0.001, min_notional: float = 5.0) -> None:
        self.filters = {
            "tick_size": tick_size,
            "step_size": step_size,
            "min_qty": min_qty,
            "min_notional": min_notional,
        }

    def normalize_order_request(
        self,
        *,
        symbol: str,
        quantity: float | None = None,
        price: float | None = None,
        stop_price: float | None = None,
        reference_price: float | None = None,
        approved_notional: float | None = None,
        enforce_min_notional: bool = True,
        close_position: bool = False,
    ) -> dict[str, object]:
        del price, stop_price, close_position
        normalized_quantity = abs(float(quantity or 0.0))
        step_size = self.filters["step_size"]
        if step_size > 0:
            normalized_quantity = float(int(normalized_quantity / step_size)) * step_size
        safe_reference = max(float(reference_price or 0.0), 1.0)
        if approved_notional is not None and approved_notional > 0:
            max_quantity = approved_notional / safe_reference
            if step_size > 0:
                max_quantity = float(int(max_quantity / step_size)) * step_size
            normalized_quantity = min(normalized_quantity, max(max_quantity, 0.0))
        notional = normalized_quantity * safe_reference
        reason_code = None
        if normalized_quantity <= 0:
            reason_code = "ORDER_QTY_ZERO_AFTER_STEP_SIZE"
        elif normalized_quantity < self.filters["min_qty"]:
            reason_code = "ORDER_QTY_BELOW_MIN_QTY"
        elif enforce_min_notional and notional < self.filters["min_notional"]:
            reason_code = "ORDER_NOTIONAL_BELOW_MIN_NOTIONAL"
        return {
            "symbol": symbol.upper(),
            "quantity": round(normalized_quantity, 6),
            "reference_price": round(safe_reference, 6),
            "notional": round(max(notional, 0.0), 6),
            "filters": dict(self.filters),
            "reason_code": reason_code,
        }


def test_auto_resize_rechecks_directional_and_single_position_limits(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.max_largest_position_pct = 1.5
    settings_row.max_directional_bias_pct = 2.0
    db_session.add_all(
        [
            Position(
                symbol="BTCUSDT",
                mode="live",
                side="long",
                status="open",
                quantity=1.538462,
                entry_price=65000.0,
                mark_price=65000.0,
                leverage=2.0,
                stop_loss=63000.0,
                take_profit=68000.0,
            ),
            Position(
                symbol="ETHUSDT",
                mode="live",
                side="long",
                status="open",
                quantity=20.0,
                entry_price=3000.0,
                mark_price=3000.0,
                leverage=2.0,
                stop_loss=2900.0,
                take_profit=3300.0,
            ),
        ]
    )
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    decision = _entry_decision(
        symbol="BTCUSDT",
        entry_zone_min=entry_price - 25.0,
        entry_zone_max=entry_price + 25.0,
        stop_loss=entry_price - 10.0,
        take_profit=entry_price + 250.0,
        max_chase_bps=20.0,
        risk_pct=0.01,
        leverage=1.0,
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
    )
    overview = get_overview(db_session)
    payload = get_operator_dashboard(db_session)
    btc = next(item for item in payload.symbols if item.symbol == "BTCUSDT")

    assert result.allowed is True
    assert result.auto_resized_entry is True
    assert result.reason_codes == []
    assert result.blocked_reason_codes == []
    assert "ENTRY_AUTO_RESIZED" in result.adjustment_reason_codes
    assert "ENTRY_CLAMPED_TO_DIRECTIONAL_LIMIT" in result.adjustment_reason_codes
    assert "DIRECTIONAL_BIAS_LIMIT_REACHED" not in result.reason_codes
    assert "LARGEST_POSITION_LIMIT_REACHED" not in result.reason_codes
    assert result.approved_projected_notional == pytest.approx(40000.0, abs=5.0)
    assert overview.blocked_reasons == []
    assert overview.latest_blocked_reasons == []
    assert btc.blocked_reasons == []
    assert btc.risk_guard.reason_codes == []
    assert btc.risk_guard.blocked_reason_codes == []
    assert "ENTRY_AUTO_RESIZED" in btc.risk_guard.adjustment_reason_codes
    assert "ENTRY_CLAMPED_TO_DIRECTIONAL_LIMIT" in btc.risk_guard.adjustment_reason_codes
    assert result.debug_payload["current_symbol_notional"] == pytest.approx(100000.03, abs=5.0)
    assert result.debug_payload["current_directional_notional"] == pytest.approx(160000.03, abs=5.0)
    assert result.debug_payload["projected_symbol_notional"] == pytest.approx(140000.03, abs=5.0)
    assert result.debug_payload["projected_directional_notional"] == pytest.approx(200000.03, abs=5.0)
    assert set(result.debug_payload["requested_exposure_limit_codes"]) == {
        "DIRECTIONAL_BIAS_LIMIT_REACHED",
        "LARGEST_POSITION_LIMIT_REACHED",
    }
    assert result.debug_payload["final_exposure_limit_codes"] == []
    assert result.debug_payload["headroom"]["directional_headroom_notional"] == pytest.approx(40000.0, abs=5.0)
    assert result.debug_payload["headroom"]["single_position_headroom_notional"] == pytest.approx(50000.0, abs=5.0)


def test_headroom_auto_resize_blocks_when_exchange_min_notional_cannot_be_met(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.max_directional_bias_pct = 0.64
    db_session.add(
        Position(
            symbol="ETHUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=19.875,
            entry_price=3200.0,
            mark_price=3200.0,
            leverage=2.0,
            stop_loss=3100.0,
            take_profit=3400.0,
        )
    )
    db_session.flush()

    snapshot = build_market_snapshot("ETHUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        symbol="ETHUSDT",
        entry_zone_min=snapshot.latest_price - 5.0,
        entry_zone_max=snapshot.latest_price + 5.0,
        stop_loss=snapshot.latest_price - 1.0,
        take_profit=snapshot.latest_price + 80.0,
        max_chase_bps=20.0,
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        exchange_client=_RiskFilterClient(min_notional=5000.0),
    )

    assert result.allowed is False
    assert result.auto_resized_entry is False
    assert "ENTRY_SIZE_BELOW_MIN_NOTIONAL" in result.reason_codes
    assert result.approved_projected_notional == 0.0
    assert result.approved_quantity is None
    assert "DIRECTIONAL_BIAS_LIMIT_REACHED" in result.debug_payload["requested_exposure_limit_codes"]
    assert result.debug_payload["requested_exchange_reason_code"] is None
    assert result.debug_payload["resized_exchange_reason_code"] is None
    assert result.debug_payload["exchange_minimums"]["min_notional"] == 5000.0
    assert result.debug_payload["exchange_minimums"]["filter_source"] == "exchange_filters"
    assert result.debug_payload["headroom"]["limiting_headroom_notional"] < result.debug_payload["exchange_minimums"]["minimum_actionable_notional"]


def test_entry_is_blocked_when_exchange_min_qty_cannot_be_met(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        symbol="BTCUSDT",
        entry_zone_min=snapshot.latest_price - 25.0,
        entry_zone_max=snapshot.latest_price + 25.0,
        stop_loss=snapshot.latest_price - 1000.0,
        take_profit=snapshot.latest_price + 1500.0,
        max_chase_bps=20.0,
        risk_pct=0.00025,
        leverage=0.5,
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        exchange_client=_RiskFilterClient(step_size=0.01, min_qty=0.1, min_notional=10.0),
    )

    assert result.allowed is False
    assert result.auto_resized_entry is False
    assert result.reason_codes == ["ENTRY_SIZE_BELOW_MIN_NOTIONAL"]
    assert result.approved_projected_notional == 0.0
    assert result.approved_quantity is None
    assert result.debug_payload["requested_exchange_reason_code"] == "ORDER_QTY_BELOW_MIN_QTY"
    assert result.debug_payload["exchange_minimums"]["min_qty"] == 0.1
    assert result.debug_payload["exchange_minimums"]["minimum_actionable_quantity"] == pytest.approx(0.1, abs=1e-6)


def test_entry_trigger_failure_exposes_trigger_debug_payload(db_session) -> None:
    settings_row = _seed_live_ready_settings(db_session)
    settings_row.slippage_threshold_pct = 0.05
    db_session.add(settings_row)
    db_session.flush()
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    breakout_floor = max(
        snapshot.latest_price,
        snapshot.candles[-1].high if snapshot.candles else snapshot.latest_price,
    ) + 20.0
    decision = _entry_decision(
        symbol="BTCUSDT",
        entry_zone_min=breakout_floor,
        entry_zone_max=breakout_floor + 20.0,
        stop_loss=snapshot.latest_price - 1000.0,
        take_profit=snapshot.latest_price + 1500.0,
        entry_mode="breakout_confirm",
        max_chase_bps=30.0,
        leverage=1.0,
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    trigger_debug = result.debug_payload["entry_trigger"]
    assert result.allowed is False
    assert result.reason_codes == ["ENTRY_TRIGGER_NOT_MET"]
    assert result.blocked_reason_codes == ["ENTRY_TRIGGER_NOT_MET"]
    assert result.adjustment_reason_codes == []
    assert trigger_debug["trigger_met"] is False
    assert trigger_debug["breakout_confirmed"] is False
    assert trigger_debug["mode"] == "breakout_confirm"
    assert trigger_debug["latest_price"] == pytest.approx(snapshot.latest_price, abs=0.01)
    assert trigger_debug["entry_zone_min"] == pytest.approx(breakout_floor, abs=0.01)
    assert trigger_debug["entry_zone_max"] == pytest.approx(breakout_floor + 20.0, abs=0.01)
    assert trigger_debug["reason_codes"] == ["ENTRY_TRIGGER_NOT_MET"]


def test_open_order_reserved_notional_excludes_reduce_only_and_protective_orders(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    db_session.add_all(
        [
            Order(
                symbol="BTCUSDT",
                side="buy",
                order_type="limit",
                mode="live",
                status="pending",
                requested_quantity=0.1,
                requested_price=65000.0,
                filled_quantity=0.0,
                average_fill_price=0.0,
                reduce_only=False,
                close_only=False,
                reason_codes=[],
                metadata_json={},
            ),
            Order(
                symbol="BTCUSDT",
                side="sell",
                order_type="limit",
                mode="live",
                status="pending",
                requested_quantity=0.1,
                requested_price=65000.0,
                filled_quantity=0.0,
                average_fill_price=0.0,
                reduce_only=True,
                close_only=False,
                reason_codes=[],
                metadata_json={},
            ),
            Order(
                symbol="BTCUSDT",
                side="sell",
                order_type="STOP_MARKET",
                mode="live",
                status="pending",
                requested_quantity=0.1,
                requested_price=64000.0,
                filled_quantity=0.0,
                average_fill_price=0.0,
                reduce_only=False,
                close_only=True,
                reason_codes=[],
                metadata_json={},
            ),
        ]
    )
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        symbol="BTCUSDT",
        entry_zone_min=snapshot.latest_price - 25.0,
        entry_zone_max=snapshot.latest_price + 25.0,
        stop_loss=snapshot.latest_price - 1000.0,
        take_profit=snapshot.latest_price + 1500.0,
        max_chase_bps=20.0,
        risk_pct=0.001,
        leverage=0.5,
    )

    first_result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
    )

    entry_order = db_session.query(Order).filter(Order.side == "buy").one()
    entry_order.status = "canceled"
    db_session.add(entry_order)
    db_session.flush()

    second_result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
    )

    assert first_result.debug_payload["open_order_reserved_notional"] == pytest.approx(6500.0, abs=0.1)
    assert first_result.debug_payload["current_symbol_notional"] == pytest.approx(6500.0, abs=0.1)
    assert first_result.debug_payload["headroom"]["single_position_headroom_notional"] == pytest.approx(143500.0, abs=0.1)
    assert second_result.debug_payload["open_order_reserved_notional"] == 0.0
    assert second_result.debug_payload["current_symbol_notional"] == 0.0
    assert second_result.debug_payload["headroom"]["single_position_headroom_notional"] == pytest.approx(150000.0, abs=0.1)


def test_stale_sync_reason_keeps_sync_timestamps_in_debug_payload(db_session) -> None:
    settings_row = _seed_live_ready_settings(db_session)
    stale_at = utcnow_naive() - timedelta(hours=2)
    mark_sync_success(
        settings_row,
        scope="account",
        synced_at=stale_at,
        stale_after_seconds=60,
    )
    db_session.add(settings_row)
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        symbol="BTCUSDT",
        entry_zone_min=snapshot.latest_price - 50.0,
        entry_zone_max=snapshot.latest_price + 50.0,
        stop_loss=snapshot.latest_price - 500.0,
        take_profit=snapshot.latest_price + 800.0,
        max_chase_bps=20.0,
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is False
    assert "ACCOUNT_STATE_STALE" in result.reason_codes
    assert result.debug_payload["sync_timestamps"]["account_sync_at"] is not None
    assert result.debug_payload["sync_timestamps"]["positions_sync_at"] is not None
    assert result.debug_payload["sync_timestamps"]["open_orders_sync_at"] is not None
    assert result.debug_payload["sync_timestamps"]["protective_orders_sync_at"] is not None
    assert result.sync_freshness_summary["account"]["stale"] is True


def test_dashboard_prefers_blocked_reason_codes_from_latest_risk_payload(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    now = utcnow_naive()
    db_session.add(
        PnLSnapshot(
            snapshot_date=date.today(),
            equity=100000.0,
            cash_balance=100000.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            daily_pnl=0.0,
            cumulative_pnl=0.0,
            consecutive_losses=0,
        )
    )
    market_row = MarketSnapshot(
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=now,
        latest_price=65000.0,
        latest_volume=1000.0,
        candle_count=200,
        is_stale=False,
        is_complete=True,
        payload={},
    )
    db_session.add(market_row)
    db_session.flush()
    agent_run = AgentRun(
        role="trading_decision",
        trigger_event="realtime_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="deterministic-mock",
        summary="btc long",
        input_payload={},
        output_payload={
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "decision": "long",
            "confidence": 0.7,
            "rationale_codes": ["TEST"],
            "explanation_short": "dashboard risk payload source-of-truth",
        },
        metadata_json={},
        schema_valid=True,
    )
    db_session.add(agent_run)
    db_session.flush()
    debug_payload = {
        "requested_notional": 100000.0,
        "resized_notional": 50000.0,
        "headroom": {"directional_headroom_notional": 0.0},
        "entry_trigger": {"trigger_met": True},
    }
    db_session.add(
        RiskCheck(
            symbol="BTCUSDT",
            decision_run_id=agent_run.id,
            market_snapshot_id=market_row.id,
            allowed=False,
            decision="long",
            reason_codes=["ENTRY_TRIGGER_NOT_MET"],
            approved_risk_pct=0.0,
            approved_leverage=0.0,
            payload={
                "allowed": False,
                "decision": "long",
                "reason_codes": ["ENTRY_AUTO_RESIZED"],
                "blocked_reason_codes": ["DIRECTIONAL_BIAS_LIMIT_REACHED"],
                "adjustment_reason_codes": ["ENTRY_AUTO_RESIZED"],
                "approved_risk_pct": 0.0,
                "approved_leverage": 0.0,
                "debug_payload": debug_payload,
            },
        )
    )
    db_session.flush()

    overview = get_overview(db_session)
    payload = get_operator_dashboard(db_session)
    btc = next(item for item in payload.symbols if item.symbol == "BTCUSDT")

    assert overview.blocked_reasons == ["DIRECTIONAL_BIAS_LIMIT_REACHED"]
    assert btc.blocked_reasons == ["DIRECTIONAL_BIAS_LIMIT_REACHED"]
    assert btc.risk_guard.reason_codes == ["DIRECTIONAL_BIAS_LIMIT_REACHED"]
    assert btc.risk_guard.blocked_reason_codes == ["DIRECTIONAL_BIAS_LIMIT_REACHED"]
    assert btc.risk_guard.adjustment_reason_codes == ["ENTRY_AUTO_RESIZED"]
    assert btc.risk_guard.debug_payload == debug_payload
    assert overview.latest_risk is not None
    assert overview.latest_risk["reason_codes"] == ["DIRECTIONAL_BIAS_LIMIT_REACHED"]
    assert overview.latest_risk["blocked_reason_codes"] == ["DIRECTIONAL_BIAS_LIMIT_REACHED"]
    assert overview.latest_risk["adjustment_reason_codes"] == ["ENTRY_AUTO_RESIZED"]


def test_dashboard_falls_back_to_legacy_payload_reason_codes_when_blocked_field_missing(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    now = utcnow_naive()
    db_session.add(
        PnLSnapshot(
            snapshot_date=date.today(),
            equity=100000.0,
            cash_balance=100000.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            daily_pnl=0.0,
            cumulative_pnl=0.0,
            consecutive_losses=0,
        )
    )
    db_session.add(
        RiskCheck(
            symbol="BTCUSDT",
            decision_run_id=None,
            market_snapshot_id=None,
            allowed=False,
            decision="long",
            reason_codes=["ENTRY_TRIGGER_NOT_MET"],
            approved_risk_pct=0.0,
            approved_leverage=0.0,
            payload={
                "allowed": False,
                "decision": "long",
                "reason_codes": ["ENTRY_TRIGGER_NOT_MET"],
                "approved_risk_pct": 0.0,
                "approved_leverage": 0.0,
            },
        )
    )
    db_session.flush()

    overview = get_overview(db_session)
    payload = get_operator_dashboard(db_session)
    btc = next(item for item in payload.symbols if item.symbol == "BTCUSDT")

    assert overview.blocked_reasons == ["ENTRY_TRIGGER_NOT_MET"]
    assert overview.latest_risk is not None
    assert overview.latest_risk["reason_codes"] == ["ENTRY_TRIGGER_NOT_MET"]
    assert overview.latest_risk["blocked_reason_codes"] == ["ENTRY_TRIGGER_NOT_MET"]
    assert overview.latest_risk["adjustment_reason_codes"] == []
    assert btc.blocked_reasons == ["ENTRY_TRIGGER_NOT_MET"]
    assert btc.risk_guard.reason_codes == ["ENTRY_TRIGGER_NOT_MET"]
    assert btc.risk_guard.blocked_reason_codes == ["ENTRY_TRIGGER_NOT_MET"]
    assert btc.risk_guard.adjustment_reason_codes == []
