from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from trading_mvp.models import AuditEvent, Order
from trading_mvp.schemas import MarketCandle, MarketSnapshotPayload, RiskCheckResult, TradeDecision
from trading_mvp.services.execution import execute_live_trade
from trading_mvp.services.secret_store import encrypt_secret
from trading_mvp.services.settings import get_or_create_settings
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
        explanation_short="protection lifecycle",
        explanation_detailed="execution protection lifecycle regression coverage.",
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


class ProtectionLifecycleSuccessClient:
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

    def normalize_order_quantity(
        self,
        symbol: str,
        quantity: float,
        *,
        reference_price: float | None = None,
        enforce_min_notional: bool = True,
    ):
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
        return [
            {
                "id": "trade-entry-1",
                "price": "70000",
                "qty": "0.01",
                "commission": "0.1",
                "commissionAsset": "USDT",
                "realizedPnl": "0.0",
            }
        ]

    def cancel_order(self, *, symbol: str, order_id: str | None = None, client_order_id: str | None = None):
        self.orders = [item for item in self.orders if str(item.get("orderId", "")) != str(order_id or "")]
        return {"status": "CANCELED"}


class ProtectionLifecycleVerifyFailedClient:
    def __init__(self) -> None:
        self.entry_submitted = False
        self.emergency_submitted = False

    def get_account_info(self):
        return {
            "availableBalance": "100.0",
            "totalWalletBalance": "100.0",
            "totalUnrealizedProfit": "0.0",
            "totalMarginBalance": "100.0",
        }

    def get_open_orders(self, symbol: str):
        return []

    def get_position_information(self, symbol: str):
        if self.emergency_submitted:
            return []
        if self.entry_submitted:
            return [{"positionAmt": "0.01", "entryPrice": "70000", "markPrice": "69900", "leverage": "2"}]
        return []

    def change_initial_leverage(self, symbol: str, leverage: int):
        return {"leverage": leverage}

    def normalize_order_quantity(
        self,
        symbol: str,
        quantity: float,
        *,
        reference_price: float | None = None,
        enforce_min_notional: bool = True,
    ):
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
            order_id = "ack-stop" if order_type == "STOP_MARKET" else "ack-tp"
            return {"orderId": order_id, "status": "NEW"}
        if reduce_only:
            self.emergency_submitted = True
            return {"orderId": "emergency-1", "status": "FILLED", "executedQty": quantity or 0.01, "avgPrice": "69800"}
        self.entry_submitted = True
        return {"orderId": "entry-1", "status": "FILLED", "executedQty": quantity or 0.01, "avgPrice": "70000"}

    def get_account_trades(self, *, symbol: str, order_id: str | None = None, limit: int = 50):
        if order_id == "emergency-1":
            return [
                {
                    "id": "trade-emergency-1",
                    "price": "69800",
                    "qty": "0.01",
                    "commission": "0.1",
                    "commissionAsset": "USDT",
                    "realizedPnl": "-2.0",
                }
            ]
        return [
            {
                "id": "trade-entry-1",
                "price": "70000",
                "qty": "0.01",
                "commission": "0.1",
                "commissionAsset": "USDT",
                "realizedPnl": "0.0",
            }
        ]

    def cancel_order(self, *, symbol: str, order_id: str | None = None, client_order_id: str | None = None):
        return {"status": "CANCELED"}


def test_entry_protection_lifecycle_reaches_verified(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    client = ProtectionLifecycleSuccessClient()
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: client)

    result = execute_live_trade(
        db_session,
        get_or_create_settings(db_session),
        decision_run_id=101,
        decision=_live_decision("long"),
        market_snapshot=_market_snapshot(),
        risk_result=_risk_result("long"),
    )
    db_session.flush()

    order = db_session.scalar(select(Order).where(Order.id == result["order_id"]))
    events = list(
        db_session.scalars(
            select(AuditEvent)
            .where(AuditEvent.event_type == "protection_lifecycle_transition")
            .order_by(AuditEvent.id)
        )
    )

    assert result["status"] == "filled"
    assert result["protective_state"]["status"] == "protected"
    assert result["protection_lifecycle"]["state"] == "verified"
    assert [item["to_state"] for item in result["protection_lifecycle"]["transitions"]] == [
        "requested",
        "placed",
        "verified",
    ]
    assert result["protection_lifecycle"]["requested_order_types"] == ["STOP_MARKET", "TAKE_PROFIT_MARKET"]
    assert len(result["protection_lifecycle"]["created_order_ids"]) == 2
    assert order is not None
    assert order.metadata_json["protection_lifecycle"]["state"] == "verified"
    assert [event.payload["to_state"] for event in events] == ["requested", "placed", "verified"]


def test_entry_protection_lifecycle_marks_verify_failed_when_exchange_never_confirms(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    client = ProtectionLifecycleVerifyFailedClient()
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: client)

    result = execute_live_trade(
        db_session,
        get_or_create_settings(db_session),
        decision_run_id=202,
        decision=_live_decision("long"),
        market_snapshot=_market_snapshot(),
        risk_result=_risk_result("long"),
    )
    db_session.flush()

    order = db_session.scalar(select(Order).where(Order.id == result["order_id"]))
    events = list(
        db_session.scalars(
            select(AuditEvent)
            .where(AuditEvent.event_type == "protection_lifecycle_transition")
            .order_by(AuditEvent.id)
        )
    )

    assert result["status"] == "emergency_exit"
    assert result["protection_lifecycle"]["state"] == "verify_failed"
    assert [item["to_state"] for item in result["protection_lifecycle"]["transitions"]] == [
        "requested",
        "placed",
        "verify_failed",
    ]
    assert result["protection_lifecycle"]["verification_detail"]["error"].startswith(
        "Protective order verify refetch failed:"
    )
    assert result["emergency_action"]["status"] == "completed"
    assert order is not None
    assert order.metadata_json["protection_lifecycle"]["state"] == "verify_failed"
    assert [event.payload["to_state"] for event in events] == ["requested", "placed", "verify_failed"]
