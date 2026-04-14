from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from trading_mvp.models import AuditEvent, Order, Position
from trading_mvp.schemas import MarketCandle, MarketSnapshotPayload, RiskCheckResult, TradeDecision
from trading_mvp.services.execution import (
    _cancel_exit_orders,
    apply_position_management,
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
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: EntrySuccessClient())

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
