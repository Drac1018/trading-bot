from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from trading_mvp.models import AuditEvent, Order, Position
from trading_mvp.schemas import (
    FeaturePayload,
    MarketCandle,
    MarketSnapshotPayload,
    RegimeFeatureContext,
    RiskCheckResult,
    TradeDecision,
)
from trading_mvp.services.execution import (
    _cap_quantity_to_approved_notional,
    _cancel_exit_orders,
    apply_position_management,
    build_execution_intent,
    execute_live_trade,
    sync_live_state,
)
from trading_mvp.services.secret_store import encrypt_secret
from trading_mvp.services.settings import (
    get_or_create_settings,
    serialize_settings,
    set_trading_pause,
)
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
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    db_session.add(settings_row)
    db_session.flush()


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
