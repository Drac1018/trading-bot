from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from trading_mvp.models import AuditEvent, Position
from trading_mvp.schemas import MarketCandle, MarketSnapshotPayload, RiskCheckResult, TradeDecision
from trading_mvp.services.execution import execute_live_trade, sync_live_state
from trading_mvp.services.secret_store import encrypt_secret
from trading_mvp.services.settings import get_or_create_settings, set_trading_pause
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
    ):
        self.scaled_in = True
        return {"orderId": "404", "status": "FILLED", "executedQty": quantity or 0.01, "avgPrice": "70080"}

    def get_account_trades(self, *, symbol: str, order_id: str | None = None, limit: int = 50):
        return [{"id": "trade-404", "price": "70080", "qty": "0.01", "commission": "0.05", "commissionAsset": "USDT", "realizedPnl": "0.0"}]


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

    assert result["status"] == "emergency_exit"
    assert settings_row.trading_paused is True
    assert settings_row.pause_reason_code == "PROTECTIVE_ORDER_FAILURE"
    assert any(event.event_type == "emergency_exit_triggered" for event in events)
    assert any(event.event_type == "emergency_exit_completed" for event in events)


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

    assert "BTCUSDT" in result["unprotected_positions"]
    assert result["symbol_protection_state"]["BTCUSDT"]["status"] == "protected"
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
