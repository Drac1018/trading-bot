from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from trading_mvp.models import Order
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
        explanation_short="protection verify",
        explanation_detailed="execution protective order verification regression coverage.",
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


class ProtectionVerifySuccessClient:
    def __init__(self) -> None:
        self.entry_submitted = False
        self.emergency_submitted = False
        self.orders_by_id: dict[str, dict[str, object]] = {}
        self.fetch_calls: list[tuple[str, str | None, str | None]] = []
        self.new_order_calls = 0

    def get_account_info(self):
        return {
            "availableBalance": "100.0",
            "totalWalletBalance": "100.0",
            "totalUnrealizedProfit": "0.0",
            "totalMarginBalance": "100.0",
        }

    def get_open_orders(self, symbol: str):
        return list(self.orders_by_id.values())

    def get_position_information(self, symbol: str):
        if self.emergency_submitted:
            return []
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
        self.new_order_calls += 1
        if order_type in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}:
            order_id = "algo-stop-1" if order_type == "STOP_MARKET" else "algo-tp-1"
            payload = {
                "orderId": order_id,
                "clientOrderId": client_order_id or order_id,
                "type": order_type,
                "closePosition": "true",
                "reduceOnly": "true",
                "status": "NEW",
                "stopPrice": str(stop_price or 0),
                "origQty": str(quantity or 0.01),
                "avgPrice": "0",
                "executedQty": "0.0",
            }
            self.orders_by_id[order_id] = payload
            return {"orderId": order_id, "status": "NEW"}
        if reduce_only:
            self.emergency_submitted = True
            return {"orderId": "emergency-1", "status": "FILLED", "executedQty": quantity or 0.01, "avgPrice": "69800"}
        self.entry_submitted = True
        return {"orderId": "entry-1", "status": "FILLED", "executedQty": quantity or 0.01, "avgPrice": "70000"}

    def fetch_order(
        self,
        *,
        symbol: str,
        order_type: str | None = None,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, object]:
        self.fetch_calls.append((str(order_type or ""), order_id, client_order_id))
        if order_id and order_id in self.orders_by_id:
            return dict(self.orders_by_id[order_id])
        raise RuntimeError("order not found")

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

    def cancel_exchange_order(
        self,
        *,
        symbol: str,
        order_type: str | None = None,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, object]:
        if order_id:
            self.orders_by_id.pop(order_id, None)
        return {"orderId": order_id or "canceled", "status": "CANCELED"}


class ProtectionVerifyRetryClient(ProtectionVerifySuccessClient):
    def __init__(self) -> None:
        super().__init__()
        self.stop_fetch_attempts = 0

    def fetch_order(
        self,
        *,
        symbol: str,
        order_type: str | None = None,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, object]:
        if order_type and order_type.upper() == "STOP_MARKET":
            self.stop_fetch_attempts += 1
            if self.stop_fetch_attempts == 1:
                raise RuntimeError("temporary verify miss")
        return super().fetch_order(
            symbol=symbol,
            order_type=order_type,
            order_id=order_id,
            client_order_id=client_order_id,
        )


class ProtectionVerifyFailureClient(ProtectionVerifySuccessClient):
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
        self.new_order_calls += 1
        if order_type in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}:
            order_id = "algo-stop-fail" if order_type == "STOP_MARKET" else "algo-tp-fail"
            return {"orderId": order_id, "status": "NEW"}
        if reduce_only:
            self.emergency_submitted = True
            return {"orderId": "emergency-1", "status": "FILLED", "executedQty": quantity or 0.01, "avgPrice": "69800"}
        self.entry_submitted = True
        return {"orderId": "entry-1", "status": "FILLED", "executedQty": quantity or 0.01, "avgPrice": "70000"}

    def get_open_orders(self, symbol: str):
        return []

    def fetch_order(
        self,
        *,
        symbol: str,
        order_type: str | None = None,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, object]:
        self.fetch_calls.append((str(order_type or ""), order_id, client_order_id))
        raise RuntimeError("verify lookup missing")


class EntryBlockedAfterVerifyFailureClient(ProtectionVerifySuccessClient):
    pass


def test_protection_verify_refetch_marks_verified_and_uses_same_lookup_path(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    client = ProtectionVerifySuccessClient()
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: client)

    result = execute_live_trade(
        db_session,
        get_or_create_settings(db_session),
        decision_run_id=1,
        decision=_live_decision("long"),
        market_snapshot=_market_snapshot(),
        risk_result=_risk_result("long"),
    )
    db_session.flush()

    order = db_session.scalar(select(Order).where(Order.id == result["order_id"]))

    assert result["status"] == "filled"
    assert result["protection_lifecycle"]["state"] == "verified"
    assert len(client.fetch_calls) == 2
    assert {item[0].upper() for item in client.fetch_calls} == {"STOP_MARKET", "TAKE_PROFIT_MARKET"}
    assert order is not None
    assert order.metadata_json["protection_lifecycle"]["state"] == "verified"


def test_protection_verify_allows_single_bounded_retry(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    client = ProtectionVerifyRetryClient()
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: client)

    result = execute_live_trade(
        db_session,
        get_or_create_settings(db_session),
        decision_run_id=2,
        decision=_live_decision("long"),
        market_snapshot=_market_snapshot(),
        risk_result=_risk_result("long"),
    )

    assert result["status"] == "filled"
    assert result["protection_lifecycle"]["state"] == "verified"
    assert client.stop_fetch_attempts == 2


def test_protection_verify_failed_blocks_followup_entry_for_same_symbol(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    failing_client = ProtectionVerifyFailureClient()
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: failing_client)

    first_result = execute_live_trade(
        db_session,
        get_or_create_settings(db_session),
        decision_run_id=3,
        decision=_live_decision("long"),
        market_snapshot=_market_snapshot(),
        risk_result=_risk_result("long"),
    )
    db_session.flush()

    settings_row = get_or_create_settings(db_session)
    verify_block = settings_row.pause_reason_detail["protection_recovery"]["verification_blocks"]["BTCUSDT"]

    blocked_client = EntryBlockedAfterVerifyFailureClient()
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: blocked_client)
    second_result = execute_live_trade(
        db_session,
        settings_row,
        decision_run_id=4,
        decision=_live_decision("long"),
        market_snapshot=_market_snapshot(),
        risk_result=_risk_result("long"),
    )

    assert first_result["status"] == "emergency_exit"
    assert first_result["protection_lifecycle"]["state"] == "verify_failed"
    assert verify_block["status"] == "verify_failed"
    assert second_result["status"] == "blocked"
    assert second_result["reason_codes"] == ["PROTECTION_VERIFY_FAILED"]
    assert second_result["protection_verify_block"]["status"] == "verify_failed"
    assert blocked_client.new_order_calls == 0
