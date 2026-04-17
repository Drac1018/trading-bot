from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select
from trading_mvp.models import AuditEvent, Execution, Order, Position, SystemHealthEvent
from trading_mvp.schemas import (
    FeaturePayload,
    MarketCandle,
    MarketSnapshotPayload,
    RegimeFeatureContext,
    RiskCheckResult,
    TradeDecision,
)
from trading_mvp.services.binance_user_stream import normalize_user_stream_event
from trading_mvp.services.execution import (
    _cap_quantity_to_approved_notional,
    _cancel_exit_orders,
    apply_normalized_user_stream_events,
    apply_position_management,
    build_execution_intent,
    execute_live_trade,
    poll_live_user_stream,
    sync_live_state,
)
from trading_mvp.services.binance import BinanceAPIError
from trading_mvp.services.risk import evaluate_risk
from trading_mvp.services.secret_store import encrypt_secret
from trading_mvp.services.settings import (
    get_or_create_settings,
    serialize_settings,
    set_trading_pause,
)
from trading_mvp.services.runtime_state import set_user_stream_detail
from trading_mvp.time_utils import utcnow_naive


def _market_snapshot() -> MarketSnapshotPayload:
    now = utcnow_naive()
    return MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=now,
        latest_price=70000.0,
        latest_volume=1000.0,
        candle_count=1,
        is_stale=False,
        is_complete=True,
        candles=[
            MarketCandle(
                timestamp=now,
                open=69900.0,
                high=70100.0,
                low=69850.0,
                close=70000.0,
                volume=1000.0,
            )
        ],
    )


def _feature_payload(*, atr: float = 200.0) -> FeaturePayload:
    return FeaturePayload(
        symbol="BTCUSDT",
        timeframe="15m",
        trend_score=1.0,
        volatility_pct=0.01,
        volume_ratio=1.1,
        drawdown_pct=0.0,
        rsi=58.0,
        atr=atr,
        atr_pct=0.003,
        momentum_score=0.6,
        regime=RegimeFeatureContext(
            primary_regime="bullish",
            trend_alignment="bullish_aligned",
            volatility_regime="normal",
            volume_regime="normal",
            momentum_state="stable",
            weak_volume=False,
            momentum_weakening=False,
        ),
    )


def _risk_result(decision: str) -> RiskCheckResult:
    return RiskCheckResult(
        allowed=True,
        decision=decision,  # type: ignore[arg-type]
        reason_codes=[],
        approved_risk_pct=0.01,
        approved_leverage=2.0,
        operating_mode="live",
        effective_leverage_cap=5.0,
        symbol_risk_tier="btc",
        exposure_metrics={},
    )


def _live_decision(decision: str) -> TradeDecision:
    return TradeDecision(
        decision=decision,  # type: ignore[arg-type]
        confidence=0.7,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=69950.0,
        entry_zone_max=70050.0,
        entry_mode="immediate" if decision in {"long", "short"} else "none",
        invalidation_price=69000.0 if decision in {"long", "short"} else None,
        max_chase_bps=15.0 if decision in {"long", "short"} else None,
        idea_ttl_minutes=15 if decision in {"long", "short"} else None,
        stop_loss=69000.0,
        take_profit=72000.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="safety test",
        explanation_detailed="execution safety regression test path.",
    )


def _prime_live_settings(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.live_trading_enabled = True
    settings_row.rollout_mode = "full_live"
    settings_row.limited_live_max_notional = 500.0
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    db_session.add(settings_row)
    db_session.flush()


def _connected_user_stream_payload() -> dict[str, object]:
    event_time = utcnow_naive().isoformat()
    return {
        "user_stream_summary": {
            "status": "connected",
            "stream_source": "user_stream",
            "heartbeat_ok": True,
            "last_event_at": event_time,
        },
        "stream_health": "connected",
        "stream_source": "user_stream",
        "last_stream_event_time": event_time,
        "stream_event_count": 0,
        "stream_events": [],
        "stream_issues": [],
    }


def test_execute_live_trade_returns_blocked_without_touching_exchange_when_risk_disallows(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    called = False

    def _unexpected_client(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("exchange client should not be built for blocked intents")

    monkeypatch.setattr("trading_mvp.services.execution._build_client", _unexpected_client)

    decision = _live_decision("long").model_copy(
        update={
            "entry_mode": "breakout_confirm",
            "max_chase_bps": 8.0,
        }
    )
    risk_result = RiskCheckResult(
        allowed=False,
        decision="long",
        reason_codes=["ENTRY_TRIGGER_NOT_MET"],
        approved_risk_pct=0.0,
        approved_leverage=0.0,
        operating_mode="hold",
        effective_leverage_cap=5.0,
        symbol_risk_tier="btc",
        exposure_metrics={},
    )

    result = execute_live_trade(
        db_session,
        settings_row,
        decision_run_id=123,
        decision=decision,
        market_snapshot=_market_snapshot(),
        risk_result=risk_result,
        risk_row=None,
    )

    assert result["status"] == "blocked"
    assert result["reason_codes"] == ["ENTRY_TRIGGER_NOT_MET"]
    assert called is False


def test_execute_live_trade_shadow_mode_records_intent_without_building_client(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.live_trading_enabled = True
    settings_row.rollout_mode = "shadow"
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    db_session.add(settings_row)
    db_session.flush()

    def _unexpected_client(*args, **kwargs):
        raise AssertionError("shadow mode should not build a Binance client")

    monkeypatch.setattr("trading_mvp.services.execution._build_client", _unexpected_client)

    risk_result = RiskCheckResult(
        allowed=True,
        decision="long",
        reason_codes=[],
        blocked_reason_codes=[],
        adjustment_reason_codes=[],
        approved_risk_pct=0.01,
        approved_leverage=2.0,
        approved_projected_notional=1400.0,
        approved_quantity=0.02,
        operating_mode="live",
        effective_leverage_cap=5.0,
        symbol_risk_tier="btc",
        exposure_metrics={},
    )

    result = execute_live_trade(
        db_session,
        settings_row,
        decision_run_id=321,
        decision=_live_decision("long"),
        market_snapshot=_market_snapshot(),
        risk_result=risk_result,
        risk_row=None,
    )

    assert result["status"] == "shadow"
    assert result["reason_codes"] == ["ROLLOUT_MODE_SHADOW"]
    assert result["submit_blocked"] is True
    assert result["rollout_mode"] == "shadow"


def test_build_execution_intent_uses_approved_quantity_from_risk_result(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    decision = _live_decision("long")
    risk_result = RiskCheckResult(
        allowed=True,
        decision="long",
        reason_codes=["ENTRY_AUTO_RESIZED", "ENTRY_CLAMPED_TO_SINGLE_POSITION_LIMIT"],
        approved_risk_pct=0.0075,
        approved_leverage=2.0,
        raw_projected_notional=158000.0,
        approved_projected_notional=150000.0,
        approved_quantity=2.142857,
        auto_resized_entry=True,
        size_adjustment_ratio=0.949367,
        exposure_headroom_snapshot={"limiting_headroom_notional": 150000.0},
        auto_resize_reason="CLAMPED_TO_SINGLE_POSITION_HEADROOM",
        operating_mode="live",
        effective_leverage_cap=5.0,
        symbol_risk_tier="btc",
        exposure_metrics={},
    )

    intent = build_execution_intent(
        decision,
        _market_snapshot(),
        risk_result,
        settings_row,
        equity=100000.0,
        existing_position=None,
    )

    assert intent.quantity == 2.142857
    assert intent.requested_price == 70000.0
    assert intent.entry_mode == "immediate"


def test_capped_quantity_never_overshoots_approved_notional_after_normalize() -> None:
    class FilterClient:
        @staticmethod
        def get_symbol_filters(symbol: str):
            return {"step_size": 0.001, "min_qty": 0.001, "min_notional": 5.0}

    capped = _cap_quantity_to_approved_notional(
        FilterClient(),
        symbol="BTCUSDT",
        quantity=2.143,
        reference_price=70000.0,
        approved_notional=150000.0,
    )

    assert capped * 70000.0 <= 150000.0
    assert capped == 2.142


def test_execute_live_trade_live_dry_run_runs_preflight_without_submit_and_skips_dedupe_cache(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "live_dry_run"
    db_session.add(settings_row)
    db_session.flush()

    client = DryRunCaptureClient()
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda *_args, **_kwargs: client)

    risk_result = RiskCheckResult(
        allowed=True,
        decision="long",
        reason_codes=[],
        blocked_reason_codes=[],
        adjustment_reason_codes=[],
        approved_risk_pct=0.01,
        approved_leverage=2.0,
        approved_projected_notional=1400.0,
        approved_quantity=0.02,
        operating_mode="live",
        effective_leverage_cap=5.0,
        symbol_risk_tier="btc",
        exposure_metrics={},
    )

    first = execute_live_trade(
        db_session,
        settings_row,
        decision_run_id=654,
        decision=_live_decision("long"),
        market_snapshot=_market_snapshot(),
        risk_result=risk_result,
        risk_row=None,
        idempotency_key="dry-run-plan-1",
    )
    second = execute_live_trade(
        db_session,
        settings_row,
        decision_run_id=654,
        decision=_live_decision("long"),
        market_snapshot=_market_snapshot(),
        risk_result=risk_result,
        risk_row=None,
        idempotency_key="dry-run-plan-1",
    )

    assert first["status"] == "dry_run"
    assert first["reason_codes"] == ["ROLLOUT_MODE_LIVE_DRY_RUN"]
    assert first["submit_blocked"] is True
    assert second["status"] == "dry_run"
    assert second.get("dedupe_suppressed") is None
    assert client.submit_calls == 0
    assert client.account_info_calls == 2


def test_execute_live_trade_limited_live_caps_entry_notional_before_submit(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "limited_live"
    settings_row.limited_live_max_notional = 700.0
    db_session.add(settings_row)
    db_session.flush()

    client = AutoResizeCaptureClient()
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda *_args, **_kwargs: client)

    risk_result = RiskCheckResult(
        allowed=True,
        decision="long",
        reason_codes=[],
        blocked_reason_codes=[],
        adjustment_reason_codes=[],
        approved_risk_pct=0.01,
        approved_leverage=2.0,
        approved_projected_notional=1400.0,
        approved_quantity=0.02,
        operating_mode="live",
        effective_leverage_cap=5.0,
        symbol_risk_tier="btc",
        exposure_metrics={},
    )

    result = execute_live_trade(
        db_session,
        settings_row,
        decision_run_id=655,
        decision=_live_decision("long"),
        market_snapshot=_market_snapshot(),
        risk_result=risk_result,
        risk_row=None,
    )

    assert result["status"] in {"filled", "partially_filled"}
    assert result["rollout_mode"] == "limited_live"
    assert result["rollout_notional_cap_applied"] is True
    assert client.entry_submitted_quantities[-1] * 70000.0 <= 700.0 + 1e-6


class ProtectionFailureClient:
    def __init__(self) -> None:
        self.entry_submitted = False
        self.emergency_submitted = False
        self.orders: list[dict[str, object]] = []

    def get_account_info(self):
        return {
            "availableBalance": "100.0",
            "totalWalletBalance": "100.0",
            "totalUnrealizedProfit": "0.0",
            "totalMarginBalance": "100.0",
        }

    def get_open_orders(self, symbol: str):
        return list(self.orders)

    def get_position_information(self, symbol: str):
        if self.emergency_submitted:
            return []
        if self.entry_submitted:
            return [{"positionAmt": "0.01", "entryPrice": "70000", "markPrice": "69900", "leverage": "2"}]
        return []

    def change_initial_leverage(self, symbol: str, leverage: int):
        return {"leverage": leverage}

    def normalize_order_quantity(self, symbol: str, quantity: float, *, reference_price: float | None = None, enforce_min_notional: bool = True):
        return quantity

    def normalize_price(self, symbol: str, price: float):
        return price

    def new_order(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float | None = None,
        price: float | None = None,
        stop_price: float | None = None,
        reduce_only: bool = False,
        close_position: bool = False,
        client_order_id: str | None = None,
        response_type: str = "RESULT",
        working_type: str = "MARK_PRICE",
        time_in_force: str | None = None,
    ):
        if order_type in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}:
            raise RuntimeError("protective create failed")
        if reduce_only:
            self.emergency_submitted = True
            return {"orderId": "202", "status": "FILLED", "executedQty": quantity or 0.01, "avgPrice": "69800"}
        self.entry_submitted = True
        return {"orderId": "101", "status": "FILLED", "executedQty": quantity or 0.01, "avgPrice": "70000"}

    def get_account_trades(self, *, symbol: str, order_id: str | None = None, limit: int = 50):
        if order_id == "202":
            return [{"id": "trade-202", "price": "69800", "qty": "0.01", "commission": "0.1", "commissionAsset": "USDT", "realizedPnl": "-2.0"}]
        return [{"id": "trade-101", "price": "70000", "qty": "0.01", "commission": "0.1", "commissionAsset": "USDT", "realizedPnl": "0.0"}]

    def cancel_order(self, *, symbol: str, order_id: str | None = None, client_order_id: str | None = None):
        self.orders = [item for item in self.orders if str(item.get("orderId", "")) != str(order_id or "")]
        return {"status": "CANCELED"}


class UnprotectedSyncClient:
    def __init__(self) -> None:
        self.orders: list[dict[str, object]] = []

    def get_account_info(self):
        return {
            "availableBalance": "100.0",
            "totalWalletBalance": "100.0",
            "totalUnrealizedProfit": "1.0",
            "totalMarginBalance": "101.0",
        }

    def get_open_orders(self, symbol: str):
        return list(self.orders)

    def get_position_information(self, symbol: str):
        return [{"positionAmt": "0.01", "entryPrice": "70000", "markPrice": "70100", "leverage": "2"}]

    def get_order(self, *, symbol: str, order_id: str | None = None, client_order_id: str | None = None):
        return {"orderId": order_id or "restored", "status": "NEW", "executedQty": "0.0", "avgPrice": "0"}

    def get_account_trades(self, *, symbol: str, order_id: str | None = None, limit: int = 50):
        return []

    def normalize_price(self, symbol: str, price: float):
        return price

    def new_order(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float | None = None,
        price: float | None = None,
        stop_price: float | None = None,
        reduce_only: bool = False,
        close_position: bool = False,
        client_order_id: str | None = None,
        response_type: str = "RESULT",
        working_type: str = "MARK_PRICE",
        time_in_force: str | None = None,
    ):
        order_id = "stop-1" if order_type == "STOP_MARKET" else "tp-1"
        self.orders.append(
            {
                "orderId": order_id,
                "clientOrderId": client_order_id or order_id,
                "type": order_type,
                "closePosition": "true",
                "reduceOnly": "true",
                "stopPrice": str(stop_price or 0),
                "status": "NEW",
            }
        )
        return {"orderId": order_id, "status": "NEW"}


class ExitWhilePausedClient:
    def __init__(self) -> None:
        self.exit_submitted = False
        self.orders = [
            {"orderId": "stop-1", "clientOrderId": "stop-1", "type": "STOP_MARKET", "closePosition": "true", "reduceOnly": "true", "stopPrice": "69000"},
            {"orderId": "tp-1", "clientOrderId": "tp-1", "type": "TAKE_PROFIT_MARKET", "closePosition": "true", "reduceOnly": "true", "stopPrice": "72000"},
        ]

    def get_account_info(self):
        return {
            "availableBalance": "100.0",
            "totalWalletBalance": "100.0",
            "totalUnrealizedProfit": "0.0",
            "totalMarginBalance": "100.0",
        }

    def get_open_orders(self, symbol: str):
        return list(self.orders)

    def get_position_information(self, symbol: str):
        if self.exit_submitted:
            return []
        return [{"positionAmt": "0.01", "entryPrice": "70000", "markPrice": "69950", "leverage": "2"}]

    def change_initial_leverage(self, symbol: str, leverage: int):
        return {"leverage": leverage}

    def normalize_order_quantity(self, symbol: str, quantity: float, *, reference_price: float | None = None, enforce_min_notional: bool = True):
        return quantity

    def normalize_price(self, symbol: str, price: float):
        return price

    def new_order(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float | None = None,
        price: float | None = None,
        stop_price: float | None = None,
        reduce_only: bool = False,
        close_position: bool = False,
        client_order_id: str | None = None,
        response_type: str = "RESULT",
        working_type: str = "MARK_PRICE",
        time_in_force: str | None = None,
    ):
        self.exit_submitted = True
        return {"orderId": "303", "status": "FILLED", "executedQty": quantity or 0.01, "avgPrice": "69950"}

    def get_account_trades(self, *, symbol: str, order_id: str | None = None, limit: int = 50):
        return [{"id": "trade-303", "price": "69950", "qty": "0.01", "commission": "0.05", "commissionAsset": "USDT", "realizedPnl": "-0.5"}]

    def cancel_order(self, *, symbol: str, order_id: str | None = None, client_order_id: str | None = None):
        self.orders = [item for item in self.orders if str(item.get("orderId", "")) != str(order_id or "")]
        return {"status": "CANCELED"}


class ScaleInClient(ExitWhilePausedClient):
    def __init__(self) -> None:
        super().__init__()
        self.scaled_in = False

    def get_position_information(self, symbol: str):
        if self.scaled_in:
            return [{"positionAmt": "0.02", "entryPrice": "70050", "markPrice": "70100", "leverage": "2"}]
        return [{"positionAmt": "0.01", "entryPrice": "70000", "markPrice": "70080", "leverage": "2"}]

    def new_order(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float | None = None,
        price: float | None = None,
        stop_price: float | None = None,
        reduce_only: bool = False,
        close_position: bool = False,
        client_order_id: str | None = None,
        response_type: str = "RESULT",
        working_type: str = "MARK_PRICE",
        time_in_force: str | None = None,
    ):
        self.scaled_in = True
        return {"orderId": "404", "status": "FILLED", "executedQty": quantity or 0.01, "avgPrice": "70080"}

    def get_account_trades(self, *, symbol: str, order_id: str | None = None, limit: int = 50):
        return [{"id": "trade-404", "price": "70080", "qty": "0.01", "commission": "0.05", "commissionAsset": "USDT", "realizedPnl": "0.0"}]


class TimeoutRecoveredExitClient(ExitWhilePausedClient):
    def __init__(self) -> None:
        super().__init__()
        self.submit_calls = 0
        self.lookup_calls = 0
        self.submitted_client_order_ids: list[str | None] = []

    def new_order(self, **kwargs):
        self.submit_calls += 1
        self.submitted_client_order_ids.append(kwargs.get("client_order_id"))
        raise httpx.ReadTimeout("submit timed out")

    def get_order(self, *, symbol: str, order_id: str | None = None, client_order_id: str | None = None):
        self.lookup_calls += 1
        self.exit_submitted = True
        return {
            "orderId": "timeout-restored-1",
            "clientOrderId": client_order_id or "timeout-restored-client-1",
            "status": "FILLED",
            "type": "MARKET",
            "origQty": "0.01",
            "executedQty": "0.01",
            "avgPrice": "69950",
            "reduceOnly": "true",
            "closePosition": "true",
        }

    def get_account_trades(self, *, symbol: str, order_id: str | None = None, limit: int = 50):
        if order_id == "timeout-restored-1":
            return [{"id": "trade-timeout-restored-1", "price": "69950", "qty": "0.01", "commission": "0.05", "commissionAsset": "USDT", "realizedPnl": "-0.5"}]
        return super().get_account_trades(symbol=symbol, order_id=order_id, limit=limit)


class TimeoutSafeRetryExitClient(ExitWhilePausedClient):
    def __init__(self) -> None:
        super().__init__()
        self.submit_calls = 0
        self.lookup_calls = 0
        self.submitted_client_order_ids: list[str | None] = []

    def new_order(self, **kwargs):
        self.submit_calls += 1
        self.submitted_client_order_ids.append(kwargs.get("client_order_id"))
        if self.submit_calls == 1:
            raise httpx.ReadTimeout("submit timed out")
        self.exit_submitted = True
        return {
            "orderId": "timeout-retry-1",
            "clientOrderId": kwargs.get("client_order_id") or "timeout-retry-client-1",
            "status": "FILLED",
            "type": "MARKET",
            "origQty": "0.01",
            "executedQty": "0.01",
            "avgPrice": "69950",
            "reduceOnly": "true",
            "closePosition": "true",
        }

    def get_order(self, *, symbol: str, order_id: str | None = None, client_order_id: str | None = None):
        self.lookup_calls += 1
        raise BinanceAPIError(-2013, "Order does not exist.")

    def get_account_trades(self, *, symbol: str, order_id: str | None = None, limit: int = 50):
        if order_id == "timeout-retry-1":
            return [{"id": "trade-timeout-retry-1", "price": "69950", "qty": "0.01", "commission": "0.05", "commissionAsset": "USDT", "realizedPnl": "-0.5"}]
        return super().get_account_trades(symbol=symbol, order_id=order_id, limit=limit)


class TimeoutDuplicateRetryExitClient(ExitWhilePausedClient):
    def __init__(self) -> None:
        super().__init__()
        self.submit_calls = 0
        self.lookup_calls = 0
        self.submitted_client_order_ids: list[str | None] = []

    def new_order(self, **kwargs):
        self.submit_calls += 1
        self.submitted_client_order_ids.append(kwargs.get("client_order_id"))
        if self.submit_calls == 1:
            raise httpx.ReadTimeout("submit timed out")
        raise BinanceAPIError(-2010, "Duplicate client order id.")

    def get_order(self, *, symbol: str, order_id: str | None = None, client_order_id: str | None = None):
        self.lookup_calls += 1
        if self.lookup_calls == 1:
            raise BinanceAPIError(-2013, "Order does not exist.")
        self.exit_submitted = True
        return {
            "orderId": "timeout-duplicate-restored-1",
            "clientOrderId": client_order_id or "timeout-duplicate-client-1",
            "status": "FILLED",
            "type": "MARKET",
            "origQty": "0.01",
            "executedQty": "0.01",
            "avgPrice": "69950",
            "reduceOnly": "true",
            "closePosition": "true",
        }

    def get_account_trades(self, *, symbol: str, order_id: str | None = None, limit: int = 50):
        if order_id == "timeout-duplicate-restored-1":
            return [{"id": "trade-timeout-duplicate-1", "price": "69950", "qty": "0.01", "commission": "0.05", "commissionAsset": "USDT", "realizedPnl": "-0.5"}]
        return super().get_account_trades(symbol=symbol, order_id=order_id, limit=limit)


class TimeoutUnknownExitClient(ExitWhilePausedClient):
    def __init__(self) -> None:
        super().__init__()
        self.submit_calls = 0
        self.lookup_calls = 0
        self.submitted_client_order_ids: list[str | None] = []

    def new_order(self, **kwargs):
        self.submit_calls += 1
        self.submitted_client_order_ids.append(kwargs.get("client_order_id"))
        raise httpx.ReadTimeout("submit timed out")

    def get_order(self, *, symbol: str, order_id: str | None = None, client_order_id: str | None = None):
        self.lookup_calls += 1
        raise BinanceAPIError(-2013, "Order does not exist.")


class ProtectionFailureManageOnlyClient(ProtectionFailureClient):
    def new_order(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float | None = None,
        price: float | None = None,
        stop_price: float | None = None,
        reduce_only: bool = False,
        close_position: bool = False,
        client_order_id: str | None = None,
        response_type: str = "RESULT",
        working_type: str = "MARK_PRICE",
        time_in_force: str | None = None,
    ):
        if order_type in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}:
            raise RuntimeError("protective create failed")
        if reduce_only:
            raise RuntimeError("emergency close failed")
        self.entry_submitted = True
        return {"orderId": "101", "status": "FILLED", "executedQty": quantity or 0.01, "avgPrice": "70000"}


class AlgoSyncLookupClient:
    def __init__(self) -> None:
        self.algo_order_calls = 0
        self.standard_order_calls = 0
        self.trade_lookup_calls = 0

    def get_account_info(self):
        return {
            "availableBalance": "100.0",
            "totalWalletBalance": "100.0",
            "totalUnrealizedProfit": "0.0",
            "totalMarginBalance": "100.0",
        }

    def get_algo_order(self, *, algo_id: str | None = None, client_algo_id: str | None = None):
        self.algo_order_calls += 1
        return {
            "orderId": algo_id or "algo-lookup-1",
            "clientOrderId": client_algo_id or "algo-client-lookup-1",
            "status": "NEW",
            "type": "STOP_MARKET",
            "executedQty": "0.0",
            "avgPrice": "0",
            "stopPrice": "69000",
        }

    def get_order(self, *, symbol: str, order_id: str | None = None, client_order_id: str | None = None):
        self.standard_order_calls += 1
        return {
            "orderId": order_id or "std-lookup-1",
            "clientOrderId": client_order_id or "std-client-lookup-1",
            "status": "NEW",
            "type": "LIMIT",
            "executedQty": "0.0",
            "avgPrice": "0",
        }

    def get_account_trades(self, *, symbol: str, order_id: str | None = None, limit: int = 50):
        self.trade_lookup_calls += 1
        return []

    def get_open_orders(self, symbol: str):
        return []

    def get_position_information(self, symbol: str):
        return []


class AlgoCancelClient:
    def __init__(self) -> None:
        self.algo_cancel_calls = 0
        self.standard_cancel_calls = 0
        self.orders = [
            {
                "orderId": "algo-stop-1",
                "clientOrderId": "algo-stop-client-1",
                "algoId": "algo-stop-1",
                "clientAlgoId": "algo-stop-client-1",
                "type": "STOP_MARKET",
                "closePosition": "true",
                "reduceOnly": "true",
                "status": "NEW",
            }
        ]

    def get_open_orders(self, symbol: str):
        return list(self.orders)

    def cancel_algo_order(self, *, algo_id: str | None = None, client_algo_id: str | None = None):
        self.algo_cancel_calls += 1
        self.orders = []
        return {"orderId": algo_id or "algo-stop-1", "status": "CANCELED"}

    def cancel_order(self, *, symbol: str, order_id: str | None = None, client_order_id: str | None = None):
        self.standard_cancel_calls += 1
        return {"orderId": order_id or "std-order-1", "status": "CANCELED"}


class PositionManagementStopClient:
    def __init__(self) -> None:
        self.orders = [
            {
                "orderId": "stop-old",
                "clientOrderId": "stop-old",
                "type": "STOP_MARKET",
                "closePosition": "true",
                "reduceOnly": "true",
                "stopPrice": "69000",
                "status": "NEW",
            },
            {
                "orderId": "tp-old",
                "clientOrderId": "tp-old",
                "type": "TAKE_PROFIT_MARKET",
                "closePosition": "true",
                "reduceOnly": "true",
                "stopPrice": "72000",
                "status": "NEW",
            },
        ]

    def get_open_orders(self, symbol: str):
        return list(self.orders)

    def cancel_order(self, *, symbol: str, order_id: str | None = None, client_order_id: str | None = None):
        target = str(order_id or client_order_id or "")
        self.orders = [
            item
            for item in self.orders
            if str(item.get("orderId", "")) != target and str(item.get("clientOrderId", "")) != target
        ]
        return {"orderId": target, "status": "CANCELED"}

    def normalize_price(self, symbol: str, price: float):
        return price

    def new_order(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float | None = None,
        price: float | None = None,
        stop_price: float | None = None,
        reduce_only: bool = False,
        close_position: bool = False,
        client_order_id: str | None = None,
        response_type: str = "RESULT",
        working_type: str = "MARK_PRICE",
        time_in_force: str | None = None,
    ):
        order_id = "stop-tightened"
        self.orders.append(
            {
                "orderId": order_id,
                "clientOrderId": client_order_id or order_id,
                "type": order_type,
                "closePosition": "true",
                "reduceOnly": "true",
                "stopPrice": str(stop_price or 0),
                "status": "NEW",
            }
        )
        return {"orderId": order_id, "status": "NEW"}


class EntrySuccessClient:
    def __init__(self) -> None:
        self.entry_submitted = False
        self.orders: list[dict[str, object]] = []
        self.account_info_calls = 0
        self.open_orders_calls = 0
        self.position_information_calls = 0

    def get_account_info(self):
        self.account_info_calls += 1
        return {
            "availableBalance": "100.0",
            "totalWalletBalance": "100.0",
            "totalUnrealizedProfit": "0.0",
            "totalMarginBalance": "100.0",
        }

    def get_open_orders(self, symbol: str):
        self.open_orders_calls += 1
        return list(self.orders)

    def get_position_information(self, symbol: str):
        self.position_information_calls += 1
        if not self.entry_submitted:
            return []
        return [{"positionAmt": "0.01", "entryPrice": "70000", "markPrice": "70120", "leverage": "2"}]

    def change_initial_leverage(self, symbol: str, leverage: int):
        return {"leverage": leverage}

    def normalize_order_quantity(self, symbol: str, quantity: float, *, reference_price: float | None = None, enforce_min_notional: bool = True):
        return quantity

    def normalize_price(self, symbol: str, price: float):
        return price

    def new_order(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float | None = None,
        price: float | None = None,
        stop_price: float | None = None,
        reduce_only: bool = False,
        close_position: bool = False,
        client_order_id: str | None = None,
        response_type: str = "RESULT",
        working_type: str = "MARK_PRICE",
        time_in_force: str | None = None,
    ):
        if order_type in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}:
            order_id = "stop-1" if order_type == "STOP_MARKET" else "tp-1"
            self.orders.append(
                {
                    "orderId": order_id,
                    "clientOrderId": client_order_id or order_id,
                    "type": order_type,
                    "closePosition": "true",
                    "reduceOnly": "true",
                    "stopPrice": str(stop_price or 0),
                    "status": "NEW",
                }
            )
            return {"orderId": order_id, "status": "NEW"}
        self.entry_submitted = True
        return {"orderId": "entry-1", "status": "FILLED", "executedQty": quantity or 0.01, "avgPrice": "70000"}

    def get_account_trades(self, *, symbol: str, order_id: str | None = None, limit: int = 50):
        return [{"id": "trade-entry-1", "price": "70000", "qty": "0.01", "commission": "0.1", "commissionAsset": "USDT", "realizedPnl": "0.0"}]

    def cancel_order(self, *, symbol: str, order_id: str | None = None, client_order_id: str | None = None):
        self.orders = [item for item in self.orders if str(item.get("orderId", "")) != str(order_id or "")]
        return {"status": "CANCELED"}


class AutoResizeCaptureClient(EntrySuccessClient):
    def __init__(self) -> None:
        super().__init__()
        self.normalized_requests: list[float] = []
        self.entry_submitted_quantities: list[float] = []

    def normalize_order_quantity(self, symbol: str, quantity: float, *, reference_price: float | None = None, enforce_min_notional: bool = True):
        normalized = quantity + 0.01
        self.normalized_requests.append(normalized)
        return normalized

    def new_order(self, **kwargs):
        if kwargs.get("order_type") not in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}:
            self.entry_submitted_quantities.append(float(kwargs.get("quantity") or 0.0))
        return super().new_order(**kwargs)


class DryRunCaptureClient(EntrySuccessClient):
    def __init__(self) -> None:
        super().__init__()
        self.submit_calls = 0

    def new_order(self, **kwargs):
        self.submit_calls += 1
        return super().new_order(**kwargs)


class StreamingCaptureClient:
    def create_futures_listen_key(self) -> str:
        return "listen-key-1"

    def keepalive_futures_listen_key(self, listen_key: str):
        return {"listenKey": listen_key}

    async def stream_futures_user_events(self, listen_key: str, *, max_events: int | None = None, idle_timeout_seconds: float = 30.0):
        yield {
            "e": "ORDER_TRADE_UPDATE",
            "E": 1_713_312_000_000,
            "o": {
                "s": "BTCUSDT",
                "i": "stream-order-1",
                "c": "stream-client-1",
                "X": "PARTIALLY_FILLED",
                "q": "0.02",
                "z": "0.01",
                "ap": "70010",
                "p": "70000",
                "sp": "0",
                "R": False,
                "cp": False,
                "o": "LIMIT",
                "S": "BUY",
                "t": "trade-stream-1",
                "l": "0.01",
                "L": "70010",
            },
        }
        yield {
            "e": "ACCOUNT_UPDATE",
            "E": 1_713_312_060_000,
            "a": {
                "B": [{"a": "USDT", "wb": "100.0", "cw": "99.0"}],
                "P": [{"s": "BTCUSDT", "pa": "0.01", "ep": "70010", "mp": "70100", "l": "2", "up": "0.9"}],
            },
        }


class ScriptedStreamingClient:
    def __init__(self, events: list[dict[str, object]]) -> None:
        self.events = [dict(item) for item in events]

    def create_futures_listen_key(self) -> str:
        return "listen-key-scripted"

    def keepalive_futures_listen_key(self, listen_key: str):
        return {"listenKey": listen_key}

    async def stream_futures_user_events(
        self,
        listen_key: str,
        *,
        max_events: int | None = None,
        idle_timeout_seconds: float = 30.0,
    ):
        del listen_key, idle_timeout_seconds
        emitted = 0
        for event in self.events:
            if max_events is not None and emitted >= max_events:
                break
            emitted += 1
            yield dict(event)


class StreamPrimarySyncClient:
    def __init__(self) -> None:
        self.order_lookup_calls = 0
        self.trade_lookup_calls = 0

    def get_order(self, *, symbol: str, order_id: str | None = None, client_order_id: str | None = None):
        del symbol, order_id, client_order_id
        self.order_lookup_calls += 1
        raise AssertionError("REST order lookup should not run while user stream is primary")

    def get_account_trades(self, *, symbol: str, order_id: str | None = None, limit: int = 50):
        del symbol, order_id, limit
        self.trade_lookup_calls += 1
        raise AssertionError("REST trade lookup should not run while user stream is primary")

    def get_open_orders(self, symbol: str):
        del symbol
        return []

    def get_position_information(self, symbol: str):
        del symbol
        return []

    def get_account_info(self):
        return {
            "availableBalance": "100.0",
            "totalWalletBalance": "100.0",
            "totalUnrealizedProfit": "0.0",
            "totalMarginBalance": "100.0",
        }


class RestFallbackSyncClient(StreamPrimarySyncClient):
    def get_order(self, *, symbol: str, order_id: str | None = None, client_order_id: str | None = None):
        del symbol, client_order_id
        self.order_lookup_calls += 1
        return {
            "orderId": order_id or "fallback-order-1",
            "clientOrderId": "fallback-client-1",
            "status": "FILLED",
            "type": "LIMIT",
            "origQty": "0.02",
            "executedQty": "0.02",
            "avgPrice": "70025",
            "price": "70000",
        }

    def get_account_trades(self, *, symbol: str, order_id: str | None = None, limit: int = 50):
        del symbol, order_id, limit
        self.trade_lookup_calls += 1
        return [
            {
                "id": "fallback-trade-1",
                "price": "70025",
                "qty": "0.02",
                "commission": "0.05",
                "commissionAsset": "USDT",
                "realizedPnl": "0.0",
            }
        ]


class MultiSymbolSyncClient(StreamPrimarySyncClient):
    def get_position_mode(self):
        return {"mode": "one_way", "dual_side_position": False}

    def get_open_orders(self, symbol: str):
        if symbol == "BTCUSDT":
            return [
                {
                    "orderId": "btc-stop-1",
                    "clientOrderId": "btc-stop-1",
                    "type": "STOP_MARKET",
                    "closePosition": "true",
                    "reduceOnly": "true",
                    "stopPrice": "69000",
                    "positionSide": "BOTH",
                    "status": "NEW",
                },
                {
                    "orderId": "btc-tp-1",
                    "clientOrderId": "btc-tp-1",
                    "type": "TAKE_PROFIT_MARKET",
                    "closePosition": "true",
                    "reduceOnly": "true",
                    "stopPrice": "72000",
                    "positionSide": "BOTH",
                    "status": "NEW",
                },
            ]
        if symbol == "XRPUSDT":
            return []
        raise AssertionError(f"unexpected symbol: {symbol}")

    def get_position_information(self, symbol: str):
        if symbol == "BTCUSDT":
            return [
                {
                    "positionAmt": "0.01",
                    "entryPrice": "70000",
                    "markPrice": "70100",
                    "leverage": "2",
                    "positionSide": "BOTH",
                }
            ]
        if symbol == "XRPUSDT":
            return []
        raise AssertionError(f"unexpected symbol: {symbol}")


class HedgeModeSyncClient(StreamPrimarySyncClient):
    def get_position_mode(self):
        return {"mode": "hedge", "dual_side_position": True}

    def get_open_orders(self, symbol: str):
        return [
            {
                "orderId": "hedge-stop-1",
                "clientOrderId": "hedge-stop-1",
                "type": "STOP_MARKET",
                "closePosition": "true",
                "reduceOnly": "true",
                "stopPrice": "69000",
                "positionSide": "LONG",
                "status": "NEW",
            }
        ]

    def get_position_information(self, symbol: str):
        return [
            {
                "positionAmt": "0.01",
                "entryPrice": "70000",
                "markPrice": "70150",
                "leverage": "2",
                "positionSide": "LONG",
            }
        ]


class UnknownPositionModeSyncClient(MultiSymbolSyncClient):
    def get_position_mode(self):
        raise BinanceAPIError(-1001, "position mode lookup failed")


class ReduceSuccessClient:
    def __init__(self) -> None:
        self.reduced = False
        self.orders = [
            {"orderId": "stop-1", "clientOrderId": "stop-1", "type": "STOP_MARKET", "closePosition": "true", "reduceOnly": "true", "stopPrice": "69000"},
            {"orderId": "tp-1", "clientOrderId": "tp-1", "type": "TAKE_PROFIT_MARKET", "closePosition": "true", "reduceOnly": "true", "stopPrice": "72000"},
        ]

    def get_account_info(self):
        return {
            "availableBalance": "100.0",
            "totalWalletBalance": "100.0",
            "totalUnrealizedProfit": "3.0",
            "totalMarginBalance": "103.0",
        }

    def get_open_orders(self, symbol: str):
        return list(self.orders)

    def get_position_information(self, symbol: str):
        if self.reduced:
            return [{"positionAmt": "0.015", "entryPrice": "70000", "markPrice": "70300", "leverage": "2"}]
        return [{"positionAmt": "0.02", "entryPrice": "70000", "markPrice": "70300", "leverage": "2"}]

    def change_initial_leverage(self, symbol: str, leverage: int):
        return {"leverage": leverage}

    def normalize_order_quantity(self, symbol: str, quantity: float, *, reference_price: float | None = None, enforce_min_notional: bool = True):
        return quantity

    def normalize_price(self, symbol: str, price: float):
        return price

    def new_order(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float | None = None,
        price: float | None = None,
        stop_price: float | None = None,
        reduce_only: bool = False,
        close_position: bool = False,
        client_order_id: str | None = None,
        response_type: str = "RESULT",
        working_type: str = "MARK_PRICE",
        time_in_force: str | None = None,
    ):
        self.reduced = True
        return {
            "orderId": "reduce-1",
            "status": "FILLED",
            "executedQty": quantity or 0.005,
            "avgPrice": "70300",
        }

    def get_account_trades(self, *, symbol: str, order_id: str | None = None, limit: int = 50):
        return [{"id": "trade-reduce-1", "price": "70300", "qty": "0.005", "commission": "0.03", "commissionAsset": "USDT", "realizedPnl": "1.0"}]

    def cancel_order(self, *, symbol: str, order_id: str | None = None, client_order_id: str | None = None):
        return {"status": "CANCELED"}


def test_apply_position_management_tightens_stop_and_records_audit(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    settings_row = get_or_create_settings(db_session)
    position = Position(
        symbol="BTCUSDT",
        mode="live",
        side="long",
        status="open",
        quantity=0.01,
        entry_price=70000.0,
        mark_price=70750.0,
        leverage=2.0,
        stop_loss=69000.0,
        take_profit=72000.0,
        realized_pnl=0.0,
        unrealized_pnl=7.5,
        metadata_json={"position_management": {"initial_stop_loss": 69000.0, "initial_risk_per_unit": 1000.0}},
    )
    db_session.add(position)
    db_session.flush()
    db_session.add(
        Order(
            symbol="BTCUSDT",
            decision_run_id=None,
            risk_check_id=None,
            position_id=position.id,
            side="sell",
            order_type="stop_market",
            mode="live",
            status="pending",
            external_order_id="stop-old",
            client_order_id="stop-old",
            reduce_only=True,
            close_only=True,
            parent_order_id=None,
            exchange_status="NEW",
            requested_quantity=0.01,
            requested_price=69000.0,
            filled_quantity=0.0,
            average_fill_price=0.0,
            reason_codes=[],
            metadata_json={},
        )
    )
    db_session.flush()
    client = PositionManagementStopClient()
    monkeypatch.setattr(
        "trading_mvp.services.execution.build_position_management_context",
        lambda position, *, feature_payload, settings_row: {
            "enabled": True,
            "status": "active",
            "tightened_stop_loss": 70125.0,
            "reduce_reason_codes": [],
            "applied_rule_candidates": ["POSITION_MANAGEMENT_BREAK_EVEN", "POSITION_MANAGEMENT_ATR_TRAIL"],
        },
    )

    result = apply_position_management(
        db_session,
        settings_row,
        symbol="BTCUSDT",
        feature_payload=None,  # type: ignore[arg-type]
        decision_run_id=11,
        client=client,
    )
    db_session.flush()

    refreshed = db_session.scalar(select(Position).where(Position.symbol == "BTCUSDT"))
    events = list(db_session.scalars(select(AuditEvent).order_by(AuditEvent.id)))
    orders = list(db_session.scalars(select(Order).order_by(Order.id)))

    assert result["status"] == "applied"
    assert result["position_management_action"]["tightened_stop_loss"] == 70125.0
    assert result["protection_state"]["status"] == "protected"
    assert refreshed is not None and refreshed.stop_loss == 70125.0
    assert any(event.event_type == "position_management_stop_tightened" for event in events)
    assert any(order.external_order_id == "stop-tightened" for order in orders)


def test_apply_position_management_never_widens_stop_for_break_even(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    settings_row = get_or_create_settings(db_session)
    position = Position(
        symbol="BTCUSDT",
        mode="live",
        side="long",
        status="open",
        quantity=0.01,
        entry_price=70000.0,
        mark_price=70400.0,
        leverage=2.0,
        stop_loss=69000.0,
        take_profit=72000.0,
        realized_pnl=0.0,
        unrealized_pnl=4.0,
        metadata_json={"position_management": {"initial_stop_loss": 69000.0, "initial_risk_per_unit": 1000.0}},
    )
    db_session.add(position)
    db_session.flush()

    client = PositionManagementStopClient()
    monkeypatch.setattr(
        "trading_mvp.services.execution.build_position_management_context",
        lambda position, *, feature_payload, settings_row: {
            "enabled": True,
            "status": "active",
            "tightened_stop_loss": 68950.0,
            "reduce_reason_codes": [],
            "applied_rule_candidates": ["POSITION_MANAGEMENT_BREAK_EVEN"],
        },
    )

    result = apply_position_management(
        db_session,
        settings_row,
        symbol="BTCUSDT",
        feature_payload=_feature_payload(),
        decision_run_id=21,
        client=client,
    )
    db_session.flush()

    refreshed = db_session.scalar(select(Position).where(Position.symbol == "BTCUSDT"))
    stop_orders = list(db_session.scalars(select(Order).where(Order.external_order_id == "stop-tightened")))

    assert result["status"] == "monitoring"
    assert refreshed is not None and refreshed.stop_loss == 69000.0
    assert stop_orders == []


def test_entry_protection_failure_triggers_emergency_close(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: ProtectionFailureClient())

    result = execute_live_trade(
        db_session,
        get_or_create_settings(db_session),
        decision_run_id=1,
        decision=_live_decision("long"),
        market_snapshot=_market_snapshot(),
        risk_result=_risk_result("long"),
    )
    db_session.flush()

    settings_row = get_or_create_settings(db_session)
    events = list(db_session.scalars(select(AuditEvent).order_by(AuditEvent.id)))
    serialized = serialize_settings(settings_row)

    assert result["status"] == "emergency_exit"
    assert settings_row.trading_paused is False
    assert serialized["operating_state"] == "TRADABLE"
    assert any(event.event_type == "emergency_exit_triggered" for event in events)
    assert any(event.event_type == "emergency_exit_completed" for event in events)


def test_entry_execution_seeds_position_management_metadata(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    client = EntrySuccessClient()
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: client)

    result = execute_live_trade(
        db_session,
        get_or_create_settings(db_session),
        decision_run_id=5,
        decision=_live_decision("long"),
        market_snapshot=_market_snapshot(),
        risk_result=_risk_result("long"),
    )
    db_session.flush()

    position = db_session.scalar(
        select(Position).where(Position.symbol == "BTCUSDT", Position.status == "open").order_by(Position.id.desc())
    )

    assert result["status"] == "filled"
    assert position is not None
    assert result["position_management"]["metadata"]["initial_stop_loss"] == 69000.0
    assert result["position_management"]["metadata"]["initial_take_profit"] == 72000.0
    assert result["position_management"]["metadata"]["planned_max_holding_minutes"] == 120
    assert result["position_management"]["metadata"]["partial_take_profit_taken"] is False
    assert client.account_info_calls >= 3
    assert client.open_orders_calls >= 3
    assert client.position_information_calls >= 3


def test_execute_live_trade_keeps_risk_approved_quantity_cap_for_auto_resized_entry(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    client = AutoResizeCaptureClient()
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: client)

    risk_result = RiskCheckResult(
        allowed=True,
        decision="long",
        reason_codes=["ENTRY_AUTO_RESIZED", "ENTRY_CLAMPED_TO_SINGLE_POSITION_LIMIT"],
        approved_risk_pct=0.0075,
        approved_leverage=2.0,
        raw_projected_notional=158000.0,
        approved_projected_notional=150000.0,
        approved_quantity=2.142857,
        auto_resized_entry=True,
        size_adjustment_ratio=0.949367,
        exposure_headroom_snapshot={"limiting_headroom_notional": 150000.0},
        auto_resize_reason="CLAMPED_TO_SINGLE_POSITION_HEADROOM",
        operating_mode="live",
        effective_leverage_cap=5.0,
        symbol_risk_tier="btc",
        exposure_metrics={},
    )

    result = execute_live_trade(
        db_session,
        get_or_create_settings(db_session),
        decision_run_id=777,
        decision=_live_decision("long"),
        market_snapshot=_market_snapshot(),
        risk_result=risk_result,
        risk_row=None,
    )
    db_session.flush()

    order = db_session.scalar(select(Order).where(Order.decision_run_id == 777))

    assert result["status"] in {"filled", "partially_filled"}
    assert client.normalized_requests[-1] > 2.142857
    assert client.entry_submitted_quantities[-1] == pytest.approx(2.142857, abs=1e-6)
    assert order is not None
    assert order.requested_quantity == pytest.approx(2.142857, abs=1e-6)
    assert order.requested_quantity * order.requested_price <= 150000.0 + 1e-6


def test_poll_live_user_stream_applies_events_and_updates_summary(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    db_session.add(
        Order(
            symbol="BTCUSDT",
            decision_run_id=None,
            risk_check_id=None,
            position_id=None,
            side="buy",
            order_type="limit",
            mode="live",
            status="pending",
            external_order_id="stream-order-1",
            client_order_id="stream-client-1",
            reduce_only=False,
            close_only=False,
            parent_order_id=None,
            exchange_status="NEW",
            requested_quantity=0.02,
            requested_price=70000.0,
            filled_quantity=0.0,
            average_fill_price=0.0,
            reason_codes=[],
            metadata_json={},
        )
    )
    db_session.flush()

    result = poll_live_user_stream(
        db_session,
        settings_row,
        client=StreamingCaptureClient(),
        max_events=2,
        idle_timeout_seconds=0.01,
    )
    db_session.flush()

    order = db_session.scalar(select(Order).where(Order.external_order_id == "stream-order-1"))
    position = db_session.scalar(select(Position).where(Position.symbol == "BTCUSDT", Position.status == "open"))
    execution = db_session.scalar(select(Execution).where(Execution.external_trade_id == "trade-stream-1"))

    assert result["stream_health"] == "connected"
    assert result["stream_source"] == "user_stream"
    assert result["stream_event_count"] == 2
    assert result["last_stream_event_time"] is not None
    assert result["user_stream_summary"]["last_event_type"] == "ACCOUNT_UPDATE"
    assert order is not None
    assert order.status == "partially_filled"
    assert order.filled_quantity == 0.01
    assert position is not None
    assert position.quantity == 0.01
    assert execution is not None


def test_apply_user_stream_events_accumulates_partial_fill_to_filled(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    db_session.add(
        Order(
            symbol="BTCUSDT",
            decision_run_id=None,
            risk_check_id=None,
            position_id=None,
            side="buy",
            order_type="limit",
            mode="live",
            status="pending",
            external_order_id="stream-order-2",
            client_order_id="stream-client-2",
            reduce_only=False,
            close_only=False,
            parent_order_id=None,
            exchange_status="NEW",
            requested_quantity=0.02,
            requested_price=70000.0,
            filled_quantity=0.0,
            average_fill_price=0.0,
            reason_codes=[],
            metadata_json={},
        )
    )
    db_session.flush()

    raw_events = [
        {
            "e": "ORDER_TRADE_UPDATE",
            "E": 1_713_312_000_000,
            "o": {
                "s": "BTCUSDT",
                "i": "stream-order-2",
                "c": "stream-client-2",
                "X": "PARTIALLY_FILLED",
                "q": "0.02",
                "z": "0.01",
                "ap": "70010",
                "p": "70000",
                "sp": "0",
                "R": False,
                "cp": False,
                "o": "LIMIT",
                "S": "BUY",
                "t": "trade-stream-2a",
                "l": "0.01",
                "L": "70010",
            },
        },
        {
            "e": "ORDER_TRADE_UPDATE",
            "E": 1_713_312_060_000,
            "o": {
                "s": "BTCUSDT",
                "i": "stream-order-2",
                "c": "stream-client-2",
                "X": "FILLED",
                "q": "0.02",
                "z": "0.02",
                "ap": "70020",
                "p": "70000",
                "sp": "0",
                "R": False,
                "cp": False,
                "o": "LIMIT",
                "S": "BUY",
                "t": "trade-stream-2b",
                "l": "0.01",
                "L": "70030",
            },
        },
    ]

    applied, issues = apply_normalized_user_stream_events(
        db_session,
        settings_row,
        normalized_events=[normalize_user_stream_event(item) for item in raw_events],
    )
    db_session.flush()

    order = db_session.scalar(select(Order).where(Order.external_order_id == "stream-order-2"))
    executions = list(
        db_session.scalars(
            select(Execution).where(Execution.order_id == order.id).order_by(Execution.external_trade_id)  # type: ignore[union-attr]
        )
    )

    assert issues == []
    assert len(applied) == 2
    assert order is not None
    assert order.status == "filled"
    assert order.filled_quantity == pytest.approx(0.02)
    assert [execution.external_trade_id for execution in executions] == ["trade-stream-2a", "trade-stream-2b"]


def test_apply_user_stream_events_reflects_cancel_and_reject(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    db_session.add_all(
        [
            Order(
                symbol="BTCUSDT",
                decision_run_id=None,
                risk_check_id=None,
                position_id=None,
                side="buy",
                order_type="limit",
                mode="live",
                status="pending",
                external_order_id="stream-order-cancel",
                client_order_id="stream-client-cancel",
                reduce_only=False,
                close_only=False,
                parent_order_id=None,
                exchange_status="NEW",
                requested_quantity=0.02,
                requested_price=70000.0,
                filled_quantity=0.0,
                average_fill_price=0.0,
                reason_codes=[],
                metadata_json={},
            ),
            Order(
                symbol="BTCUSDT",
                decision_run_id=None,
                risk_check_id=None,
                position_id=None,
                side="buy",
                order_type="limit",
                mode="live",
                status="pending",
                external_order_id="stream-order-reject",
                client_order_id="stream-client-reject",
                reduce_only=False,
                close_only=False,
                parent_order_id=None,
                exchange_status="NEW",
                requested_quantity=0.02,
                requested_price=70000.0,
                filled_quantity=0.0,
                average_fill_price=0.0,
                reason_codes=[],
                metadata_json={},
            ),
        ]
    )
    db_session.flush()

    raw_events = [
        {
            "e": "ORDER_TRADE_UPDATE",
            "E": 1_713_312_120_000,
            "o": {
                "s": "BTCUSDT",
                "i": "stream-order-cancel",
                "c": "stream-client-cancel",
                "X": "CANCELED",
                "q": "0.02",
                "z": "0.0",
                "ap": "0",
                "p": "70000",
                "sp": "0",
                "R": False,
                "cp": False,
                "o": "LIMIT",
                "S": "BUY",
                "t": "0",
                "l": "0.0",
                "L": "0",
            },
        },
        {
            "e": "ORDER_TRADE_UPDATE",
            "E": 1_713_312_180_000,
            "o": {
                "s": "BTCUSDT",
                "i": "stream-order-reject",
                "c": "stream-client-reject",
                "X": "REJECTED",
                "q": "0.02",
                "z": "0.0",
                "ap": "0",
                "p": "70000",
                "sp": "0",
                "R": False,
                "cp": False,
                "o": "LIMIT",
                "S": "BUY",
                "t": "0",
                "l": "0.0",
                "L": "0",
            },
        },
    ]

    _applied, issues = apply_normalized_user_stream_events(
        db_session,
        settings_row,
        normalized_events=[normalize_user_stream_event(item) for item in raw_events],
    )
    db_session.flush()

    canceled = db_session.scalar(select(Order).where(Order.external_order_id == "stream-order-cancel"))
    rejected = db_session.scalar(select(Order).where(Order.external_order_id == "stream-order-reject"))

    assert issues == []
    assert canceled is not None and canceled.status == "canceled"
    assert rejected is not None and rejected.status == "rejected"


def test_apply_user_stream_events_dedupes_duplicate_trade_ids(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    db_session.add(
        Order(
            symbol="BTCUSDT",
            decision_run_id=None,
            risk_check_id=None,
            position_id=None,
            side="buy",
            order_type="limit",
            mode="live",
            status="pending",
            external_order_id="stream-order-dup",
            client_order_id="stream-client-dup",
            reduce_only=False,
            close_only=False,
            parent_order_id=None,
            exchange_status="NEW",
            requested_quantity=0.02,
            requested_price=70000.0,
            filled_quantity=0.0,
            average_fill_price=0.0,
            reason_codes=[],
            metadata_json={},
        )
    )
    db_session.flush()

    duplicate_event = {
        "e": "ORDER_TRADE_UPDATE",
        "E": 1_713_312_240_000,
        "o": {
            "s": "BTCUSDT",
            "i": "stream-order-dup",
            "c": "stream-client-dup",
            "X": "PARTIALLY_FILLED",
            "q": "0.02",
            "z": "0.01",
            "ap": "70010",
            "p": "70000",
            "sp": "0",
            "R": False,
            "cp": False,
            "o": "LIMIT",
            "S": "BUY",
            "t": "trade-stream-dup",
            "l": "0.01",
            "L": "70010",
        },
    }

    _applied, issues = apply_normalized_user_stream_events(
        db_session,
        settings_row,
        normalized_events=[
            normalize_user_stream_event(duplicate_event),
            normalize_user_stream_event(duplicate_event),
        ],
    )
    db_session.flush()

    executions = list(
        db_session.scalars(select(Execution).where(Execution.external_trade_id == "trade-stream-dup"))
    )

    assert issues == []
    assert len(executions) == 1


def test_sync_live_state_skips_rest_order_lookup_when_user_stream_is_primary(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    client = StreamPrimarySyncClient()
    db_session.add(
        Order(
            symbol="BTCUSDT",
            decision_run_id=None,
            risk_check_id=None,
            position_id=None,
            side="buy",
            order_type="limit",
            mode="live",
            status="pending",
            external_order_id="stream-primary-order-1",
            client_order_id="stream-primary-client-1",
            reduce_only=False,
            close_only=False,
            parent_order_id=None,
            exchange_status="NEW",
            requested_quantity=0.02,
            requested_price=70000.0,
            filled_quantity=0.0,
            average_fill_price=0.0,
            reason_codes=[],
            metadata_json={},
        )
    )
    db_session.flush()
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: client)
    monkeypatch.setattr(
        "trading_mvp.services.execution.poll_live_user_stream",
        lambda *args, **kwargs: {
            "user_stream_summary": {
                "status": "connected",
                "stream_source": "user_stream",
                "heartbeat_ok": True,
                "last_event_at": utcnow_naive().isoformat(),
            },
            "stream_health": "connected",
            "stream_source": "user_stream",
            "last_stream_event_time": utcnow_naive().isoformat(),
            "stream_event_count": 1,
            "stream_events": [
                {
                    "event_category": "order",
                    "related_categories": ["execution"],
                    "symbol": "BTCUSDT",
                    "symbols": ["BTCUSDT"],
                    "order_id": "stream-primary-order-1",
                }
            ],
            "stream_issues": [],
        },
    )

    result = sync_live_state(db_session, get_or_create_settings(db_session), symbol="BTCUSDT")

    assert client.order_lookup_calls == 0
    assert client.trade_lookup_calls == 0
    assert result["reconcile_source"] == "user_stream_primary"
    assert result["reconciliation_summary"]["stream_fallback_active"] is False


def test_sync_live_state_uses_rest_order_fallback_when_user_stream_is_unavailable(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    client = RestFallbackSyncClient()
    db_session.add(
        Order(
            symbol="BTCUSDT",
            decision_run_id=None,
            risk_check_id=None,
            position_id=None,
            side="buy",
            order_type="limit",
            mode="live",
            status="pending",
            external_order_id="fallback-order-1",
            client_order_id="fallback-client-1",
            reduce_only=False,
            close_only=False,
            parent_order_id=None,
            exchange_status="NEW",
            requested_quantity=0.02,
            requested_price=70000.0,
            filled_quantity=0.0,
            average_fill_price=0.0,
            reason_codes=[],
            metadata_json={},
        )
    )
    db_session.flush()
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: client)
    monkeypatch.setattr(
        "trading_mvp.services.execution.poll_live_user_stream",
        lambda *args, **kwargs: {
            "user_stream_summary": {
                "status": "unavailable",
                "stream_source": "rest_polling_fallback",
                "heartbeat_ok": False,
                "last_event_at": None,
                "last_error": "USER_STREAM_UNAVAILABLE",
            },
            "stream_health": "unavailable",
            "stream_source": "rest_polling_fallback",
            "last_stream_event_time": None,
            "stream_event_count": 0,
            "stream_events": [],
            "stream_issues": [
                {
                    "severity": "warning",
                    "reason_code": "USER_STREAM_UNAVAILABLE",
                    "message": "User stream unavailable during live sync.",
                }
            ],
        },
    )

    result = sync_live_state(db_session, get_or_create_settings(db_session), symbol="BTCUSDT")
    db_session.flush()

    order = db_session.scalar(select(Order).where(Order.external_order_id == "fallback-order-1"))
    execution = db_session.scalar(select(Execution).where(Execution.external_trade_id == "fallback-trade-1"))
    audit_events = list(db_session.scalars(select(AuditEvent).where(AuditEvent.event_type == "user_stream_order_sync_fallback")))
    health_events = list(
        db_session.scalars(select(SystemHealthEvent).where(SystemHealthEvent.component == "user_stream").order_by(SystemHealthEvent.id))
    )

    assert client.order_lookup_calls == 1
    assert client.trade_lookup_calls == 1
    assert order is not None and order.status == "filled"
    assert execution is not None
    assert execution.fee_paid == pytest.approx(0.05)
    assert execution.realized_pnl == pytest.approx(0.0)
    assert result["reconcile_source"] == "rest_polling_fallback"
    assert result["reconciliation_summary"]["stream_fallback_active"] is True
    assert any(event.entity_id == "BTCUSDT" for event in audit_events)
    assert any(event.status == "degraded" for event in health_events)


def test_sync_live_state_reconciles_enabled_symbols_only_and_records_one_way_mapping(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    settings_row = get_or_create_settings(db_session)
    settings_row.tracked_symbols = ["BTCUSDT", "ETHUSDT", "XRPUSDT"]
    settings_row.symbol_cadence_overrides = [{"symbol": "ETHUSDT", "enabled": False}]
    db_session.flush()

    client = MultiSymbolSyncClient()
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: client)
    monkeypatch.setattr(
        "trading_mvp.services.execution.poll_live_user_stream",
        lambda *args, **kwargs: _connected_user_stream_payload(),
    )

    result = sync_live_state(db_session, settings_row)
    db_session.flush()

    reconciliation = result["reconciliation_summary"]
    position = db_session.scalar(
        select(Position).where(Position.symbol == "BTCUSDT", Position.status == "open")
    )

    assert result["symbols"] == ["BTCUSDT", "XRPUSDT"]
    assert reconciliation["enabled_symbols"] == ["BTCUSDT", "XRPUSDT"]
    assert reconciliation["position_mode"] == "one_way"
    assert reconciliation["mode_guard_active"] is False
    assert "ETHUSDT" not in result["symbol_reconciliation"]
    assert result["symbol_reconciliation"]["BTCUSDT"]["remote_position_sides"] == ["BOTH"]
    assert position is not None
    assert position.metadata_json["exchange_position_side"] == "BOTH"
    assert position.metadata_json["exchange_position_mode"] == "one_way"


def test_sync_live_state_marks_hedge_mode_guard_and_exposes_runtime_summary(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    settings_row = get_or_create_settings(db_session)
    client = HedgeModeSyncClient()
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: client)
    monkeypatch.setattr(
        "trading_mvp.services.execution.poll_live_user_stream",
        lambda *args, **kwargs: _connected_user_stream_payload(),
    )

    result = sync_live_state(db_session, settings_row, symbol="BTCUSDT")
    db_session.flush()
    serialized = serialize_settings(settings_row)
    audit_events = list(
        db_session.scalars(
            select(AuditEvent)
            .where(AuditEvent.event_type == "exchange_position_mode_guard_enabled")
            .order_by(AuditEvent.id)
        )
    )

    assert result["reconciliation_summary"]["position_mode"] == "hedge"
    assert result["reconciliation_summary"]["mode_guard_active"] is True
    assert result["reconciliation_summary"]["mode_guard_reason_code"] == "EXCHANGE_POSITION_MODE_MISMATCH"
    assert result["reconciliation_summary"]["guarded_symbols"] == ["BTCUSDT"]
    assert result["symbol_reconciliation"]["BTCUSDT"]["exchange_position_side"] == "LONG"
    assert serialized["can_enter_new_position"] is False
    assert serialized["reconciliation_summary"]["mode_guard_active"] is True
    assert serialized["reconciliation_summary"]["symbol_states"]["BTCUSDT"]["guard_active"] is True
    assert serialized["sync_freshness_summary"]["protective_orders"]["status"] == "incomplete"
    assert audit_events


def test_sync_live_state_marks_unknown_position_mode_guard_when_lookup_fails(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    settings_row = get_or_create_settings(db_session)
    client = UnknownPositionModeSyncClient()
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: client)
    monkeypatch.setattr(
        "trading_mvp.services.execution.poll_live_user_stream",
        lambda *args, **kwargs: _connected_user_stream_payload(),
    )

    result = sync_live_state(db_session, settings_row, symbol="BTCUSDT")
    db_session.flush()
    serialized = serialize_settings(settings_row)

    assert result["reconciliation_summary"]["position_mode"] == "unknown"
    assert result["reconciliation_summary"]["position_mode_source"] == "exchange_error"
    assert result["reconciliation_summary"]["mode_guard_active"] is True
    assert result["reconciliation_summary"]["mode_guard_reason_code"] == "EXCHANGE_POSITION_MODE_UNCLEAR"
    assert "lookup failed" in str(result["reconciliation_summary"]["last_error"])
    assert serialized["can_enter_new_position"] is False
    assert serialized["reconciliation_summary"]["mode_guard_message"]


def test_serialize_settings_blocks_new_entries_while_listen_key_rotation_pending(db_session) -> None:
    _prime_live_settings(db_session)
    settings_row = get_or_create_settings(db_session)
    now = utcnow_naive()
    set_user_stream_detail(
        settings_row,
        status="degraded",
        stream_source="rest_polling_fallback",
        heartbeat_ok=False,
        last_error="LISTEN_KEY_EXPIRED",
        last_disconnected_at=now,
        listen_key_expiry_reason="listenKeyExpired",
        last_listen_key_expired_at=now,
        listen_key_rotate_attempted_at=now,
        listen_key_rotate_status="pending",
    )
    db_session.add(settings_row)
    db_session.flush()

    serialized = serialize_settings(settings_row)

    assert serialized["can_enter_new_position"] is False
    assert "USER_STREAM_LISTEN_KEY_ROTATION_PENDING" in serialized["blocked_reasons"]
    assert serialized["guard_mode_reason_code"] == "USER_STREAM_LISTEN_KEY_ROTATION_PENDING"
    assert serialized["user_stream_summary"]["stream_source"] == "rest_polling_fallback"
    assert serialized["user_stream_summary"]["listen_key_rotate_status"] == "pending"


def test_evaluate_risk_blocks_new_entry_when_reconciliation_mode_guard_is_active(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    settings_row = get_or_create_settings(db_session)
    client = HedgeModeSyncClient()
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: client)
    monkeypatch.setattr(
        "trading_mvp.services.execution.poll_live_user_stream",
        lambda *args, **kwargs: _connected_user_stream_payload(),
    )

    sync_live_state(db_session, settings_row, symbol="BTCUSDT")
    db_session.flush()

    risk_result, _risk_row = evaluate_risk(
        db_session,
        settings_row,
        _live_decision("long"),
        _market_snapshot(),
        execution_mode="live",
    )

    assert risk_result.allowed is False
    assert "EXCHANGE_POSITION_MODE_MISMATCH" in risk_result.reason_codes
    assert "EXCHANGE_POSITION_MODE_MISMATCH" in risk_result.blocked_reason_codes
    assert risk_result.debug_payload["reconciliation_state"]["position_mode"] == "hedge"


def test_post_order_resync_updates_sync_freshness_summary(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    client = EntrySuccessClient()
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: client)

    execute_live_trade(
        db_session,
        get_or_create_settings(db_session),
        decision_run_id=55,
        decision=_live_decision("long"),
        market_snapshot=_market_snapshot(),
        risk_result=_risk_result("long"),
    )
    db_session.flush()

    serialized = serialize_settings(get_or_create_settings(db_session))

    assert serialized["sync_freshness_summary"]["account"]["stale"] is False
    assert serialized["sync_freshness_summary"]["positions"]["stale"] is False
    assert serialized["sync_freshness_summary"]["open_orders"]["stale"] is False
    assert serialized["sync_freshness_summary"]["protective_orders"]["stale"] is False
    assert serialized["sync_freshness_summary"]["protective_orders"]["last_sync_at"] is not None


def test_sync_live_state_recreates_missing_protection_and_logs(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.01,
            entry_price=70000.0,
            mark_price=70100.0,
            leverage=2.0,
            stop_loss=69000.0,
            take_profit=72000.0,
            realized_pnl=0.0,
            unrealized_pnl=1.0,
            metadata_json={},
        )
    )
    db_session.flush()
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: UnprotectedSyncClient())

    result = sync_live_state(db_session, get_or_create_settings(db_session), symbol="BTCUSDT")
    db_session.flush()
    events = list(db_session.scalars(select(AuditEvent).order_by(AuditEvent.id)))
    serialized = serialize_settings(get_or_create_settings(db_session))

    assert "BTCUSDT" in result["unprotected_positions"]
    assert result["symbol_protection_state"]["BTCUSDT"]["status"] == "protected"
    assert serialized["operating_state"] == "TRADABLE"
    assert serialized["sync_freshness_summary"]["account"]["stale"] is False
    assert serialized["sync_freshness_summary"]["protective_orders"]["stale"] is False
    assert any(event.event_type == "unprotected_position_detected" for event in events)
    assert any(event.event_type == "protection_recreate_attempted" for event in events)


def test_manual_pause_still_allows_exit_management_path(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.01,
            entry_price=70000.0,
            mark_price=69950.0,
            leverage=2.0,
            stop_loss=69000.0,
            take_profit=72000.0,
            realized_pnl=0.0,
            unrealized_pnl=-0.5,
            metadata_json={},
        )
    )
    db_session.flush()
    set_trading_pause(
        db_session,
        True,
        reason_code="MANUAL_USER_REQUEST",
        reason_detail={"source": "test"},
        pause_origin="manual",
    )
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: ExitWhilePausedClient())

    result = execute_live_trade(
        db_session,
        get_or_create_settings(db_session),
        decision_run_id=2,
        decision=_live_decision("exit"),
        market_snapshot=_market_snapshot(),
        risk_result=_risk_result("exit"),
    )

    assert result["status"] == "filled"


def test_execute_live_trade_recovers_timeout_submission_when_exchange_lookup_finds_order(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.01,
            entry_price=70000.0,
            mark_price=69950.0,
            leverage=2.0,
            stop_loss=69000.0,
            take_profit=72000.0,
            realized_pnl=0.0,
            unrealized_pnl=-0.5,
            metadata_json={},
        )
    )
    db_session.flush()
    client = TimeoutRecoveredExitClient()
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: client)

    result = execute_live_trade(
        db_session,
        get_or_create_settings(db_session),
        decision_run_id=52,
        decision=_live_decision("exit"),
        market_snapshot=_market_snapshot(),
        risk_result=_risk_result("exit"),
    )
    db_session.flush()

    order = db_session.scalar(select(Order).where(Order.external_order_id == "timeout-restored-1"))
    events = list(db_session.scalars(select(AuditEvent).where(AuditEvent.event_type == "live_order_submission_recovered")))

    assert result["status"] == "filled"
    assert client.submit_calls == 1
    assert client.lookup_calls == 1
    assert len({item for item in client.submitted_client_order_ids if item}) == 1
    assert order is not None
    assert order.metadata_json["submission_tracking"]["submission_state"] == "reconciled"
    assert order.metadata_json["submission_tracking"]["submit_attempt_count"] == 1
    assert order.metadata_json["submission_tracking"]["recovered_via"] == "client_order_id_lookup"
    assert any(event.entity_id == str(order.id) for event in events)


def test_execute_live_trade_safe_retries_after_timeout_when_order_is_absent(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.01,
            entry_price=70000.0,
            mark_price=69950.0,
            leverage=2.0,
            stop_loss=69000.0,
            take_profit=72000.0,
            realized_pnl=0.0,
            unrealized_pnl=-0.5,
            metadata_json={},
        )
    )
    db_session.flush()
    client = TimeoutSafeRetryExitClient()
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: client)

    result = execute_live_trade(
        db_session,
        get_or_create_settings(db_session),
        decision_run_id=53,
        decision=_live_decision("exit"),
        market_snapshot=_market_snapshot(),
        risk_result=_risk_result("exit"),
    )
    db_session.flush()

    order = db_session.scalar(select(Order).where(Order.external_order_id == "timeout-retry-1"))

    assert result["status"] == "filled"
    assert client.submit_calls == 2
    assert client.lookup_calls == 1
    assert len({item for item in client.submitted_client_order_ids if item}) == 1
    assert order is not None
    assert order.metadata_json["submission_tracking"]["submission_state"] == "reconciled"
    assert order.metadata_json["submission_tracking"]["submit_attempt_count"] == 2
    assert order.metadata_json["submission_tracking"]["safe_retry_used"] is True
    assert order.metadata_json["submission_tracking"]["recovered_via"] == "safe_retry_ack"


def test_execute_live_trade_dedupes_duplicate_retry_with_same_client_order_id(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.01,
            entry_price=70000.0,
            mark_price=69950.0,
            leverage=2.0,
            stop_loss=69000.0,
            take_profit=72000.0,
            realized_pnl=0.0,
            unrealized_pnl=-0.5,
            metadata_json={},
        )
    )
    db_session.flush()
    client = TimeoutDuplicateRetryExitClient()
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: client)

    result = execute_live_trade(
        db_session,
        get_or_create_settings(db_session),
        decision_run_id=54,
        decision=_live_decision("exit"),
        market_snapshot=_market_snapshot(),
        risk_result=_risk_result("exit"),
    )
    db_session.flush()

    order = db_session.scalar(select(Order).where(Order.external_order_id == "timeout-duplicate-restored-1"))

    assert result["status"] == "filled"
    assert client.submit_calls == 2
    assert client.lookup_calls == 2
    assert len({item for item in client.submitted_client_order_ids if item}) == 1
    assert order is not None
    assert order.metadata_json["submission_tracking"]["submission_state"] == "reconciled"
    assert order.metadata_json["submission_tracking"]["submit_attempt_count"] == 2
    assert order.metadata_json["submission_tracking"]["safe_retry_used"] is True
    assert order.metadata_json["submission_tracking"]["recovered_via"] == "duplicate_client_order_id_lookup"


def test_execute_live_trade_marks_timeout_submission_unknown_and_emits_audit(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.01,
            entry_price=70000.0,
            mark_price=69950.0,
            leverage=2.0,
            stop_loss=69000.0,
            take_profit=72000.0,
            realized_pnl=0.0,
            unrealized_pnl=-0.5,
            metadata_json={},
        )
    )
    db_session.flush()
    client = TimeoutUnknownExitClient()
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: client)

    result = execute_live_trade(
        db_session,
        get_or_create_settings(db_session),
        decision_run_id=55,
        decision=_live_decision("exit"),
        market_snapshot=_market_snapshot(),
        risk_result=_risk_result("exit"),
    )
    db_session.flush()

    order = db_session.scalar(select(Order).where(Order.client_order_id == client.submitted_client_order_ids[0]))
    audit_events = list(db_session.scalars(select(AuditEvent).where(AuditEvent.event_type == "live_order_submission_unknown")))
    health_events = list(db_session.scalars(select(SystemHealthEvent).where(SystemHealthEvent.component == "live_execution")))

    assert result["status"] == "submission_unknown"
    assert result["reason_codes"] == ["LIVE_ORDER_SUBMISSION_UNKNOWN"]
    assert client.submit_calls == 2
    assert client.lookup_calls == 2
    assert len({item for item in client.submitted_client_order_ids if item}) == 1
    assert order is not None
    assert order.status == "pending"
    assert order.exchange_status == "SUBMIT_UNKNOWN"
    assert order.metadata_json["submission_tracking"]["submission_state"] == "submit_unknown"
    assert order.metadata_json["submission_tracking"]["submit_attempt_count"] == 2
    assert order.metadata_json["submission_tracking"]["safe_retry_used"] is True
    assert any(event.entity_id == str(order.id) for event in audit_events)
    assert any(event.status == "warning" for event in health_events)


def test_reduce_execution_uses_partial_take_profit_fraction_and_marks_metadata(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.02,
            entry_price=70000.0,
            mark_price=70300.0,
            leverage=2.0,
            stop_loss=69000.0,
            take_profit=72000.0,
            realized_pnl=0.0,
            unrealized_pnl=6.0,
            metadata_json={"position_management": {"partial_take_profit_taken": False}},
        )
    )
    db_session.flush()
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: ReduceSuccessClient())

    decision = _live_decision("reduce")
    decision.rationale_codes = [
        "POSITION_MANAGEMENT_PARTIAL_TAKE_PROFIT",
        "POSITION_MANAGEMENT_LOCK_PARTIAL_PROFIT",
    ]

    result = execute_live_trade(
        db_session,
        get_or_create_settings(db_session),
        decision_run_id=6,
        decision=decision,
        market_snapshot=_market_snapshot(),
        risk_result=_risk_result("reduce"),
    )
    db_session.flush()

    order = db_session.scalar(select(Order).where(Order.external_order_id == "reduce-1"))
    position = db_session.scalar(
        select(Position).where(Position.symbol == "BTCUSDT", Position.status == "open").order_by(Position.id.desc())
    )

    assert result["status"] == "filled"
    assert result["position_management"]["reduce_fraction"] == 0.25
    assert result["position_management"]["metadata"]["partial_take_profit_taken"] is True
    assert order is not None
    assert order.requested_quantity == 0.005
    assert order.metadata_json["position_management"]["reduce_fraction"] == 0.25
    assert position is not None


def test_apply_position_management_executes_partial_tp_once_and_stays_reduce_only(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    settings_row = get_or_create_settings(db_session)
    settings_row.partial_tp_size_pct = 0.25
    position = Position(
        symbol="BTCUSDT",
        mode="live",
        side="long",
        status="open",
        quantity=0.02,
        entry_price=70000.0,
        mark_price=70300.0,
        leverage=2.0,
        stop_loss=69000.0,
        take_profit=72000.0,
        realized_pnl=0.0,
        unrealized_pnl=6.0,
        metadata_json={"position_management": {"partial_take_profit_taken": False}},
    )
    db_session.add(position)
    db_session.flush()

    client = ReduceSuccessClient()
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: client)

    def _context(position, *, feature_payload, settings_row):
        management = position.metadata_json.get("position_management", {}) if isinstance(position.metadata_json, dict) else {}
        taken = bool(management.get("partial_take_profit_taken"))
        return {
            "enabled": True,
            "status": "active",
            "tightened_stop_loss": None,
            "reduce_reason_codes": []
            if taken
            else [
                "POSITION_MANAGEMENT_PARTIAL_TAKE_PROFIT",
                "POSITION_MANAGEMENT_LOCK_PARTIAL_PROFIT",
            ],
            "partial_take_profit_taken": taken,
            "partial_take_profit_fraction": 0.25,
            "applied_rule_candidates": ["POSITION_MANAGEMENT_PARTIAL_TAKE_PROFIT"] if not taken else [],
        }

    monkeypatch.setattr("trading_mvp.services.execution.build_position_management_context", _context)

    first = apply_position_management(
        db_session,
        settings_row,
        symbol="BTCUSDT",
        feature_payload=_feature_payload(),
        decision_run_id=31,
        client=client,
    )
    second = apply_position_management(
        db_session,
        settings_row,
        symbol="BTCUSDT",
        feature_payload=_feature_payload(),
        decision_run_id=32,
        client=client,
    )
    db_session.flush()

    events = list(db_session.scalars(select(AuditEvent).order_by(AuditEvent.id)))
    reduce_orders = list(db_session.scalars(select(Order).where(Order.external_order_id == "reduce-1")))
    refreshed = db_session.scalar(select(Position).where(Position.symbol == "BTCUSDT", Position.status == "open"))

    assert first["status"] == "executed"
    assert first["position_management_action"]["status"] == "filled"
    assert second["status"] == "monitoring"
    assert len(reduce_orders) == 1
    assert reduce_orders[0].reduce_only is True
    assert reduce_orders[0].requested_quantity == 0.005
    assert refreshed is not None
    assert refreshed.metadata_json["position_management"]["partial_take_profit_taken"] is True
    assert sum(1 for event in events if event.event_type == "partial_tp_executed") == 1


def test_apply_position_management_does_nothing_when_time_stop_is_disabled(db_session) -> None:
    _prime_live_settings(db_session)
    settings_row = get_or_create_settings(db_session)
    settings_row.break_even_enabled = False
    settings_row.atr_trailing_stop_enabled = False
    settings_row.partial_take_profit_enabled = False
    settings_row.time_stop_enabled = False
    settings_row.holding_edge_decay_enabled = False
    settings_row.reduce_on_regime_shift_enabled = False
    position = Position(
        symbol="BTCUSDT",
        mode="live",
        side="long",
        status="open",
        quantity=0.01,
        entry_price=70000.0,
        mark_price=70020.0,
        leverage=2.0,
        stop_loss=69000.0,
        take_profit=72000.0,
        realized_pnl=0.0,
        unrealized_pnl=0.2,
        opened_at=utcnow_naive() - timedelta(hours=4),
        metadata_json={"position_management": {"initial_stop_loss": 69000.0, "initial_risk_per_unit": 1000.0}},
    )
    db_session.add(position)
    db_session.flush()

    result = apply_position_management(
        db_session,
        settings_row,
        symbol="BTCUSDT",
        feature_payload=_feature_payload(atr=0.0),
        decision_run_id=42,
        client=PositionManagementStopClient(),
    )

    assert result["status"] == "monitoring"
    assert result["position_management_context"]["time_stop_enabled"] is False
    assert result["position_management_context"]["time_stop_ready"] is False


def test_scale_in_does_not_cancel_existing_protection_before_fill(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.01,
            entry_price=70000.0,
            mark_price=70080.0,
            leverage=2.0,
            stop_loss=69000.0,
            take_profit=72000.0,
            realized_pnl=0.0,
            unrealized_pnl=0.8,
            metadata_json={},
        )
    )
    db_session.flush()
    cancel_calls: list[str] = []
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: ScaleInClient())
    monkeypatch.setattr("trading_mvp.services.execution._cancel_exit_orders", lambda session, client, symbol: cancel_calls.append(symbol))

    result = execute_live_trade(
        db_session,
        get_or_create_settings(db_session),
        decision_run_id=3,
        decision=_live_decision("long"),
        market_snapshot=_market_snapshot(),
        risk_result=_risk_result("long"),
    )

    assert result["status"] == "filled"
    assert result["intent_type"] == "scale_in"
    assert cancel_calls == []


def test_protection_failure_falls_back_to_manage_only_when_emergency_close_fails(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: ProtectionFailureManageOnlyClient())

    result = execute_live_trade(
        db_session,
        get_or_create_settings(db_session),
        decision_run_id=4,
        decision=_live_decision("long"),
        market_snapshot=_market_snapshot(),
        risk_result=_risk_result("long"),
    )
    db_session.flush()
    events = list(db_session.scalars(select(AuditEvent).order_by(AuditEvent.id)))
    serialized = serialize_settings(get_or_create_settings(db_session))

    assert result["status"] == "emergency_exit"
    assert serialized["operating_state"] == "DEGRADED_MANAGE_ONLY"
    assert serialized["protection_recovery_failure_count"] >= 1
    assert any(event.event_type == "protection_manage_only_enabled" for event in events)


def test_apply_position_management_executes_time_stop_exit(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    settings_row = get_or_create_settings(db_session)
    settings_row.time_stop_enabled = True
    position = Position(
        symbol="BTCUSDT",
        mode="live",
        side="long",
        status="open",
        quantity=0.01,
        entry_price=70000.0,
        mark_price=69950.0,
        leverage=2.0,
        stop_loss=69000.0,
        take_profit=72000.0,
        realized_pnl=0.0,
        unrealized_pnl=-0.5,
        opened_at=utcnow_naive() - timedelta(hours=3),
        metadata_json={"position_management": {"initial_stop_loss": 69000.0, "initial_risk_per_unit": 1000.0}},
    )
    db_session.add(position)
    db_session.flush()

    client = ExitWhilePausedClient()
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: client)
    monkeypatch.setattr(
        "trading_mvp.services.execution.build_position_management_context",
        lambda position, *, feature_payload, settings_row: {
            "enabled": True,
            "status": "active",
            "tightened_stop_loss": None,
            "reduce_reason_codes": ["POSITION_MANAGEMENT_TIME_STOP_EXIT"],
            "time_stop_minutes": 120,
            "time_stop_profit_floor": 0.15,
            "applied_rule_candidates": ["POSITION_MANAGEMENT_TIME_STOP"],
        },
    )

    result = apply_position_management(
        db_session,
        settings_row,
        symbol="BTCUSDT",
        feature_payload=_feature_payload(),
        decision_run_id=41,
        client=client,
    )
    db_session.flush()
    events = list(db_session.scalars(select(AuditEvent).order_by(AuditEvent.id)))

    assert result["status"] == "executed"
    assert result["position_management_action"]["status"] == "filled"
    assert any(event.event_type == "time_stop_exit" for event in events)


def test_apply_position_management_skips_aggressive_action_when_protection_is_unverified(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    settings_row = get_or_create_settings(db_session)
    position = Position(
        symbol="BTCUSDT",
        mode="live",
        side="long",
        status="open",
        quantity=0.01,
        entry_price=70000.0,
        mark_price=69950.0,
        leverage=2.0,
        stop_loss=69000.0,
        take_profit=72000.0,
        realized_pnl=0.0,
        unrealized_pnl=-0.5,
        metadata_json={"position_management": {"initial_stop_loss": 69000.0, "initial_risk_per_unit": 1000.0}},
    )
    db_session.add(position)
    db_session.flush()
    monkeypatch.setattr(
        "trading_mvp.services.execution.build_position_management_context",
        lambda position, *, feature_payload, settings_row: {
            "enabled": True,
            "status": "active",
            "tightened_stop_loss": None,
            "reduce_reason_codes": ["POSITION_MANAGEMENT_TIME_STOP_REDUCE"],
            "time_stop_minutes": 120,
            "time_stop_profit_floor": 0.15,
            "applied_rule_candidates": ["POSITION_MANAGEMENT_TIME_STOP"],
        },
    )

    result = apply_position_management(
        db_session,
        settings_row,
        symbol="BTCUSDT",
        feature_payload=_feature_payload(),
        decision_run_id=43,
        client=UnprotectedSyncClient(),
    )

    assert result["status"] == "monitoring"
    assert result["position_management_action"]["status"] == "skipped_unverified_protection"


def test_sync_live_state_uses_algo_lookup_for_protective_orders(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    client = AlgoSyncLookupClient()
    db_session.add(
        Order(
            symbol="BTCUSDT",
            decision_run_id=None,
            risk_check_id=None,
            position_id=None,
            side="sell",
            order_type="stop_market",
            mode="live",
            status="pending",
            external_order_id="algo-lookup-1",
            client_order_id="algo-client-lookup-1",
            reduce_only=True,
            close_only=True,
            parent_order_id=None,
            exchange_status="NEW",
            requested_quantity=0.01,
            requested_price=69000.0,
            filled_quantity=0.0,
            average_fill_price=0.0,
            reason_codes=[],
            metadata_json={},
        )
    )
    db_session.flush()
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: client)

    sync_live_state(db_session, get_or_create_settings(db_session), symbol="BTCUSDT")

    assert client.algo_order_calls == 1
    assert client.standard_order_calls == 0
    assert client.trade_lookup_calls == 0


def test_cancel_exit_orders_uses_algo_cancel_for_protective_orders(db_session) -> None:
    client = AlgoCancelClient()
    db_session.add(
        Order(
            symbol="BTCUSDT",
            decision_run_id=None,
            risk_check_id=None,
            position_id=None,
            side="sell",
            order_type="stop_market",
            mode="live",
            status="pending",
            external_order_id="algo-stop-1",
            client_order_id="algo-stop-client-1",
            reduce_only=True,
            close_only=True,
            parent_order_id=None,
            exchange_status="NEW",
            requested_quantity=0.01,
            requested_price=69000.0,
            filled_quantity=0.0,
            average_fill_price=0.0,
            reason_codes=[],
            metadata_json={},
        )
    )
    db_session.flush()

    _cancel_exit_orders(db_session, client, "BTCUSDT")

    assert client.algo_cancel_calls == 1
    assert client.standard_cancel_calls == 0
