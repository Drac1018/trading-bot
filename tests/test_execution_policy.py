from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from trading_mvp.database import Base, get_db
from trading_mvp.main import app
from trading_mvp.models import AgentRun, AuditEvent, Order
from trading_mvp.schemas import (
    ExecutionIntent,
    MarketCandle,
    MarketSnapshotPayload,
    RiskCheckResult,
    TradeDecision,
)
from trading_mvp.services.execution import execute_live_trade
from trading_mvp.services.execution_policy import select_execution_plan
from trading_mvp.services.dashboard import get_executions
from trading_mvp.services.runtime_state import PROTECTION_REQUIRED_STATE
from trading_mvp.services.secret_store import encrypt_secret
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive


def _snapshot(
    *,
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    latest_price: float = 70000.0,
    is_stale: bool = False,
    high_delta: float = 50.0,
    low_delta: float = 40.0,
) -> MarketSnapshotPayload:
    now = utcnow_naive()
    open_1 = max(latest_price - max(low_delta * 0.5, latest_price * 0.001), 0.0001)
    open_2 = max(latest_price - max(low_delta * 0.25, latest_price * 0.0005), 0.0001)
    open_3 = max(latest_price - max(low_delta * 0.1, latest_price * 0.0003), 0.0001)
    low_1 = max(latest_price - low_delta, 0.0001)
    low_2 = max(latest_price - low_delta * 0.8, 0.0001)
    low_3 = max(latest_price - low_delta * 0.6, 0.0001)
    close_1 = max(latest_price - max(low_delta * 0.2, latest_price * 0.0003), 0.0001)
    close_2 = max(latest_price - max(low_delta * 0.05, latest_price * 0.0001), 0.0001)
    return MarketSnapshotPayload(
        symbol=symbol,
        timeframe=timeframe,
        snapshot_time=now,
        latest_price=latest_price,
        latest_volume=1200.0,
        candle_count=3,
        is_stale=is_stale,
        is_complete=True,
        candles=[
            MarketCandle(timestamp=now, open=open_1, high=latest_price + high_delta * 0.6, low=low_1, close=close_1, volume=900.0),
            MarketCandle(timestamp=now, open=open_2, high=latest_price + high_delta * 0.8, low=low_2, close=close_2, volume=950.0),
            MarketCandle(timestamp=now, open=open_3, high=latest_price + high_delta, low=low_3, close=latest_price, volume=1000.0),
        ],
    )


def _intent(*, action: str, intent_type: str, symbol: str = "BTCUSDT", requested_price: float = 70000.0) -> ExecutionIntent:
    return ExecutionIntent(
        symbol=symbol,
        action=action,  # type: ignore[arg-type]
        intent_type=intent_type,  # type: ignore[arg-type]
        quantity=0.01,
        requested_price=requested_price,
        stop_loss=69000.0,
        take_profit=72000.0,
        leverage=2.0,
        mode="live",
        reduce_only=intent_type == "reduce_only",
        close_only=action == "exit",
    )


def _risk_result(action: str) -> RiskCheckResult:
    return RiskCheckResult(
        allowed=True,
        decision=action,  # type: ignore[arg-type]
        reason_codes=[],
        approved_risk_pct=0.01,
        approved_leverage=2.0,
        operating_mode="live",
        effective_leverage_cap=5.0,
        symbol_risk_tier="btc",
        exposure_metrics={},
    )


def _decision(action: str) -> TradeDecision:
    return TradeDecision(
        decision=action,  # type: ignore[arg-type]
        confidence=0.8,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=69990.0,
        entry_zone_max=70010.0,
        stop_loss=69000.0,
        take_profit=72000.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="execution policy test",
        explanation_detailed="execution policy path should choose the expected order type.",
    )


def _prime_live_settings(db_session):
    settings_row = get_or_create_settings(db_session)
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    settings_row.slippage_threshold_pct = 0.002
    db_session.add(settings_row)
    db_session.flush()
    return settings_row


class PolicyCaptureClient:
    def __init__(self, *, initial_position_qty: float = 0.0, orders: list[dict[str, object]] | None = None) -> None:
        self.current_position_qty = initial_position_qty
        self.orders = list(orders or [])
        self.primary_order_calls: list[dict[str, object]] = []
        self.protective_order_calls: list[dict[str, object]] = []
        self.last_primary_order_id: str | None = None
        self.side = "long"

    def get_account_info(self):
        return {
            "availableBalance": "250.0",
            "totalWalletBalance": "250.0",
            "totalUnrealizedProfit": "0.0",
            "totalMarginBalance": "250.0",
        }

    def get_open_orders(self, symbol: str):
        return list(self.orders)

    def get_position_information(self, symbol: str):
        if self.current_position_qty <= 0:
            return []
        position_amt = self.current_position_qty if self.side == "long" else -self.current_position_qty
        return [{"positionAmt": str(position_amt), "entryPrice": "70000", "markPrice": "70000", "leverage": "2"}]

    def change_initial_leverage(self, symbol: str, leverage: int):
        return {"leverage": leverage}

    def normalize_order_quantity(self, symbol: str, quantity: float, *, reference_price: float | None = None, enforce_min_notional: bool = True):
        return quantity

    def normalize_price(self, symbol: str, price: float):
        return price

    def new_order(self, **kwargs):
        order_type = str(kwargs["order_type"])
        if order_type in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}:
            self.protective_order_calls.append(kwargs)
            order_id = f"{order_type.lower()}-{len(self.protective_order_calls)}"
            self.orders.append(
                {
                    "orderId": order_id,
                    "clientOrderId": kwargs.get("client_order_id", order_id),
                    "type": order_type,
                    "closePosition": "true",
                    "reduceOnly": "true",
                    "stopPrice": str(kwargs.get("stop_price") or 0),
                    "status": "NEW",
                }
            )
            return {"orderId": order_id, "status": "NEW"}

        self.primary_order_calls.append(kwargs)
        order_id = f"primary-{len(self.primary_order_calls)}"
        self.last_primary_order_id = order_id
        reduce_only = bool(kwargs.get("reduce_only"))
        close_position = bool(kwargs.get("close_position"))
        quantity = float(kwargs.get("quantity") or 0.0)
        if close_position:
            self.current_position_qty = 0.0
        elif reduce_only:
            self.current_position_qty = max(self.current_position_qty - quantity, 0.0)
        elif self.current_position_qty > 0:
            self.current_position_qty += quantity
        else:
            self.current_position_qty = quantity
        return {
            "orderId": order_id,
            "clientOrderId": kwargs.get("client_order_id", order_id),
            "status": "FILLED",
            "executedQty": str(quantity),
            "avgPrice": str(kwargs.get("price") or 70000.0),
        }

    def get_account_trades(self, *, symbol: str, order_id: str | None = None, limit: int = 50):
        if order_id and order_id == self.last_primary_order_id:
            return [
                {
                    "id": f"trade-{order_id}",
                    "price": "70000",
                    "qty": "0.01",
                    "commission": "0.1",
                    "commissionAsset": "USDT",
                    "realizedPnl": "0.0",
                }
            ]
        return []

    def cancel_order(self, *, symbol: str, order_id: str | None = None, client_order_id: str | None = None):
        self.orders = [item for item in self.orders if str(item.get("orderId", "")) != str(order_id or "")]
        return {"status": "CANCELED"}

    def get_order(self, *, symbol: str, order_id: str | None = None, client_order_id: str | None = None):
        return {"orderId": order_id or "lookup", "status": "NEW", "executedQty": "0.0", "avgPrice": "0"}


class LimitRepriceFallbackClient(PolicyCaptureClient):
    def __init__(self, *, partial_fill_qty: float = 0.0, market_price: float = 70000.0) -> None:
        super().__init__(initial_position_qty=0.0)
        self.partial_fill_qty = partial_fill_qty
        self.market_price = market_price
        self.partial_fill_applied = False
        self.order_states: dict[str, str] = {}
        self.order_exec_qty: dict[str, float] = {}
        self.order_avg_price: dict[str, float] = {}
        self.order_trades: dict[str, list[dict[str, object]]] = {}

    def get_symbol_price(self, symbol: str) -> float:
        return self.market_price

    def new_order(self, **kwargs):
        order_type = str(kwargs["order_type"])
        if order_type in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}:
            return super().new_order(**kwargs)
        self.primary_order_calls.append(kwargs)
        order_id = f"primary-{len(self.primary_order_calls)}"
        client_order_id = kwargs.get("client_order_id", order_id)
        quantity = float(kwargs.get("quantity") or 0.0)
        price = float(kwargs.get("price") or self.market_price)
        if order_type == "LIMIT":
            fill_qty = self.partial_fill_qty if self.partial_fill_qty > 0 and not self.partial_fill_applied else 0.0
            self.partial_fill_applied = self.partial_fill_applied or fill_qty > 0
            self.order_states[order_id] = "PARTIALLY_FILLED" if fill_qty > 0 else "NEW"
            self.order_exec_qty[order_id] = fill_qty
            self.order_avg_price[order_id] = price
            if fill_qty > 0:
                self.current_position_qty += fill_qty
                self.order_trades[order_id] = [
                    {
                        "id": f"trade-{order_id}",
                        "price": str(price),
                        "qty": str(fill_qty),
                        "commission": "0.04",
                        "commissionAsset": "USDT",
                        "realizedPnl": "0.0",
                    }
                ]
            else:
                self.order_trades[order_id] = []
            return {
                "orderId": order_id,
                "clientOrderId": client_order_id,
                "status": self.order_states[order_id],
                "executedQty": str(self.order_exec_qty[order_id]),
                "avgPrice": str(self.order_avg_price[order_id]),
            }

        self.current_position_qty += quantity
        self.order_states[order_id] = "FILLED"
        self.order_exec_qty[order_id] = quantity
        self.order_avg_price[order_id] = self.market_price
        self.order_trades[order_id] = [
            {
                "id": f"trade-{order_id}",
                "price": str(self.market_price),
                "qty": str(quantity),
                "commission": "0.06",
                "commissionAsset": "USDT",
                "realizedPnl": "0.0",
            }
        ]
        return {
            "orderId": order_id,
            "clientOrderId": client_order_id,
            "status": "FILLED",
            "executedQty": str(quantity),
            "avgPrice": str(self.market_price),
        }

    def get_order(self, *, symbol: str, order_id: str | None = None, client_order_id: str | None = None):
        key = order_id or client_order_id or ""
        return {
            "orderId": key,
            "clientOrderId": client_order_id or key,
            "status": self.order_states.get(key, "NEW"),
            "executedQty": str(self.order_exec_qty.get(key, 0.0)),
            "avgPrice": str(self.order_avg_price.get(key, self.market_price)),
        }

    def get_account_trades(self, *, symbol: str, order_id: str | None = None, limit: int = 50):
        return list(self.order_trades.get(order_id or "", []))

    def cancel_order(self, *, symbol: str, order_id: str | None = None, client_order_id: str | None = None):
        key = order_id or client_order_id or ""
        self.order_states[key] = "CANCELED"
        return {"status": "CANCELED"}


def test_entry_policy_prefers_limit_under_passive_conditions() -> None:
    settings_row = SimpleNamespace(slippage_threshold_pct=0.002)
    plan = select_execution_plan(
        _intent(action="long", intent_type="entry"),
        _snapshot(),
        settings_row,  # type: ignore[arg-type]
        pre_trade_protection={},
    )

    assert plan.order_type == "LIMIT"
    assert plan.time_in_force == "GTC"
    assert plan.policy_name == "entry_passive_limit"


def test_execution_policy_profiles_by_symbol_timeframe_and_volatility() -> None:
    settings_row = SimpleNamespace(slippage_threshold_pct=0.002)

    btc_slow_plan = select_execution_plan(
        _intent(action="long", intent_type="entry", symbol="BTCUSDT"),
        _snapshot(symbol="BTCUSDT", timeframe="4h", high_delta=35.0, low_delta=30.0),
        settings_row,  # type: ignore[arg-type]
        pre_trade_protection={},
    )
    alt_fast_plan = select_execution_plan(
        _intent(action="long", intent_type="entry", symbol="LINKUSDT", requested_price=15.0),
        _snapshot(symbol="LINKUSDT", timeframe="15m", latest_price=15.0, high_delta=0.03, low_delta=0.025),
        settings_row,  # type: ignore[arg-type]
        pre_trade_protection={},
    )
    stressed_plan = select_execution_plan(
        _intent(action="long", intent_type="entry", symbol="LINKUSDT", requested_price=15.0),
        _snapshot(symbol="LINKUSDT", timeframe="15m", latest_price=15.0, high_delta=0.35, low_delta=0.35),
        settings_row,  # type: ignore[arg-type]
        pre_trade_protection={},
    )

    assert btc_slow_plan.order_type == "LIMIT"
    assert btc_slow_plan.timeframe_bucket == "slow"
    assert btc_slow_plan.symbol_risk_tier == "btc"
    assert btc_slow_plan.max_requotes >= 2
    assert alt_fast_plan.order_type == "LIMIT"
    assert alt_fast_plan.policy_profile.startswith("entry_alt_fast_")
    assert alt_fast_plan.max_requotes <= btc_slow_plan.max_requotes
    assert stressed_plan.order_type == "MARKET"
    assert stressed_plan.volatility_regime == "stressed"


def test_scale_in_and_reduce_policy_split_from_exit() -> None:
    settings_row = SimpleNamespace(slippage_threshold_pct=0.002)
    scale_in_plan = select_execution_plan(
        _intent(action="long", intent_type="scale_in"),
        _snapshot(),
        settings_row,  # type: ignore[arg-type]
        pre_trade_protection={"protected": True},
    )
    reduce_plan = select_execution_plan(
        _intent(action="reduce", intent_type="reduce_only"),
        _snapshot(),
        settings_row,  # type: ignore[arg-type]
        pre_trade_protection={"protected": True},
    )
    exit_plan = select_execution_plan(
        _intent(action="exit", intent_type="reduce_only"),
        _snapshot(),
        settings_row,  # type: ignore[arg-type]
        pre_trade_protection={"protected": True},
    )

    assert scale_in_plan.order_type == "LIMIT"
    assert reduce_plan.order_type == "LIMIT"
    assert exit_plan.order_type == "MARKET"


def test_entry_policy_uses_market_when_snapshot_is_stale() -> None:
    settings_row = SimpleNamespace(slippage_threshold_pct=0.002)
    plan = select_execution_plan(
        _intent(action="long", intent_type="entry"),
        _snapshot(is_stale=True),
        settings_row,  # type: ignore[arg-type]
        pre_trade_protection={},
    )

    assert plan.order_type == "MARKET"
    assert plan.reason == "market_data_not_reliable"


def test_execute_live_trade_uses_policy_for_entry_and_scale_in(monkeypatch, db_session) -> None:
    settings_row = _prime_live_settings(db_session)
    entry_client = PolicyCaptureClient()
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda _: entry_client)

    entry_result = execute_live_trade(
        db_session,
        settings_row,
        decision_run_id=10,
        decision=_decision("long"),
        market_snapshot=_snapshot(),
        risk_result=_risk_result("long"),
    )

    assert entry_result["status"] == "filled"
    assert entry_result["execution_policy"]["order_type"] == "LIMIT"
    assert entry_client.primary_order_calls[0]["order_type"] == "LIMIT"
    assert entry_client.primary_order_calls[0]["time_in_force"] == "GTC"

    scale_in_client = PolicyCaptureClient(
        initial_position_qty=0.01,
        orders=[
            {"orderId": "stop-1", "clientOrderId": "stop-1", "type": "STOP_MARKET", "closePosition": "true", "reduceOnly": "true", "stopPrice": "69000", "status": "NEW"},
            {"orderId": "tp-1", "clientOrderId": "tp-1", "type": "TAKE_PROFIT_MARKET", "closePosition": "true", "reduceOnly": "true", "stopPrice": "72000", "status": "NEW"},
        ],
    )
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda _: scale_in_client)

    scale_in_result = execute_live_trade(
        db_session,
        settings_row,
        decision_run_id=11,
        decision=_decision("long"),
        market_snapshot=_snapshot(),
        risk_result=_risk_result("long"),
    )

    assert scale_in_result["intent_type"] == "scale_in"
    assert scale_in_result["execution_policy"]["order_type"] == "LIMIT"
    assert scale_in_client.primary_order_calls[0]["order_type"] == "LIMIT"


def test_execute_live_trade_uses_reduce_and_exit_policy(monkeypatch, db_session) -> None:
    settings_row = _prime_live_settings(db_session)
    reduce_client = PolicyCaptureClient(
        initial_position_qty=0.02,
        orders=[
            {"orderId": "stop-1", "clientOrderId": "stop-1", "type": "STOP_MARKET", "closePosition": "true", "reduceOnly": "true", "stopPrice": "69000", "status": "NEW"},
            {"orderId": "tp-1", "clientOrderId": "tp-1", "type": "TAKE_PROFIT_MARKET", "closePosition": "true", "reduceOnly": "true", "stopPrice": "72000", "status": "NEW"},
        ],
    )
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda _: reduce_client)

    reduce_result = execute_live_trade(
        db_session,
        settings_row,
        decision_run_id=12,
        decision=_decision("reduce"),
        market_snapshot=_snapshot(),
        risk_result=_risk_result("reduce"),
    )

    assert reduce_result["intent_type"] == "reduce_only"
    assert reduce_result["execution_policy"]["order_type"] == "LIMIT"
    assert reduce_client.primary_order_calls[0]["order_type"] == "LIMIT"

    exit_client = PolicyCaptureClient(
        initial_position_qty=0.01,
        orders=[
            {"orderId": "stop-1", "clientOrderId": "stop-1", "type": "STOP_MARKET", "closePosition": "true", "reduceOnly": "true", "stopPrice": "69000", "status": "NEW"},
            {"orderId": "tp-1", "clientOrderId": "tp-1", "type": "TAKE_PROFIT_MARKET", "closePosition": "true", "reduceOnly": "true", "stopPrice": "72000", "status": "NEW"},
        ],
    )
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda _: exit_client)

    exit_result = execute_live_trade(
        db_session,
        settings_row,
        decision_run_id=13,
        decision=_decision("exit"),
        market_snapshot=_snapshot(),
        risk_result=_risk_result("exit"),
    )

    assert exit_result["intent_type"] == "reduce_only"
    assert exit_result["execution_policy"]["order_type"] == "MARKET"
    assert exit_client.primary_order_calls[0]["order_type"] == "MARKET"


def test_protection_path_stays_separate_from_primary_execution(monkeypatch, db_session) -> None:
    settings_row = _prime_live_settings(db_session)
    protection_client = PolicyCaptureClient(initial_position_qty=0.01)
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda _: protection_client)
    monkeypatch.setattr(
        "trading_mvp.services.execution.get_operating_state",
        lambda settings_row: PROTECTION_REQUIRED_STATE,
    )

    result = execute_live_trade(
        db_session,
        settings_row,
        decision_run_id=14,
        decision=_decision("long"),
        market_snapshot=_snapshot(),
        risk_result=_risk_result("long"),
    )

    assert result["status"] in {"protected", "protected_recreated"}
    assert protection_client.primary_order_calls == []
    assert {call["order_type"] for call in protection_client.protective_order_calls} == {
        "STOP_MARKET",
        "TAKE_PROFIT_MARKET",
    }


def test_entry_limit_timeout_reprices_then_falls_back_to_market(monkeypatch, db_session) -> None:
    settings_row = _prime_live_settings(db_session)
    client = LimitRepriceFallbackClient()
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda _: client)
    monkeypatch.setattr("trading_mvp.services.execution._execution_policy_sleep", lambda _seconds: None)

    result = execute_live_trade(
        db_session,
        settings_row,
        decision_run_id=15,
        decision=_decision("long"),
        market_snapshot=_snapshot(),
        risk_result=_risk_result("long"),
    )

    order_types = [call["order_type"] for call in client.primary_order_calls]

    assert result["status"] == "filled"
    assert order_types[:2] == ["LIMIT", "LIMIT"]
    assert order_types[-1] == "MARKET"
    assert len(result["execution_attempts"]) >= 3
    audit_types = set(db_session.scalars(select(AuditEvent.event_type)))
    assert "live_limit_timeout" in audit_types
    assert "live_limit_repriced" in audit_types
    assert "live_limit_aggressive_fallback" in audit_types


def test_partial_fill_is_preserved_before_aggressive_fallback(monkeypatch, db_session) -> None:
    settings_row = _prime_live_settings(db_session)
    client = LimitRepriceFallbackClient(partial_fill_qty=0.001, market_price=71250.0)
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda _: client)
    monkeypatch.setattr("trading_mvp.services.execution._execution_policy_sleep", lambda _seconds: None)

    result = execute_live_trade(
        db_session,
        settings_row,
        decision_run_id=16,
        decision=_decision("long"),
        market_snapshot=_snapshot(),
        risk_result=_risk_result("long"),
    )

    assert result["status"] == "filled"
    assert result["fill_quantity"] >= 0.001
    assert result["execution_attempts"][0]["filled_quantity"] == 0.001
    assert result["execution_attempts"][-1]["order_type"] == "MARKET"
    assert result["fill_quantity"] > result["execution_attempts"][0]["filled_quantity"]
    audit_types = list(db_session.scalars(select(AuditEvent.event_type).order_by(AuditEvent.id.asc())))
    assert "live_limit_partial_fill" in audit_types
    assert "live_limit_aggressive_fallback" in audit_types


def test_large_partial_fill_finishes_remaining_quantity_with_market(monkeypatch, db_session) -> None:
    settings_row = _prime_live_settings(db_session)
    client = LimitRepriceFallbackClient(partial_fill_qty=0.002, market_price=70500.0)
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda _: client)
    monkeypatch.setattr("trading_mvp.services.execution._execution_policy_sleep", lambda _seconds: None)

    result = execute_live_trade(
        db_session,
        settings_row,
        decision_run_id=17,
        decision=_decision("long"),
        market_snapshot=_snapshot(),
        risk_result=_risk_result("long"),
    )

    assert result["status"] == "filled"
    assert len(result["execution_attempts"]) == 2
    assert result["execution_attempts"][0]["filled_quantity"] > 0.0
    assert result["execution_attempts"][-1]["order_type"] == "MARKET"
    assert result["execution_quality"]["aggressive_fallback_used"] is True
    assert result["execution_quality"]["execution_quality_status"] == "aggressive_completion"


def test_get_executions_includes_decision_and_execution_quality(monkeypatch, db_session) -> None:
    settings_row = _prime_live_settings(db_session)
    client = LimitRepriceFallbackClient(partial_fill_qty=0.001, market_price=71250.0)
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda _: client)
    monkeypatch.setattr("trading_mvp.services.execution._execution_policy_sleep", lambda _seconds: None)
    run = AgentRun(
        role="trading_decision",
        trigger_event="test",
        schema_name="TradeDecision",
        status="completed",
        provider_name="test",
        summary="execution quality test",
        input_payload={},
        output_payload={"decision": "long", "timeframe": "15m", "confidence": 0.8, "rationale_codes": ["TEST"]},
        metadata_json={},
    )
    db_session.add(run)
    db_session.flush()

    execute_live_trade(
        db_session,
        settings_row,
        decision_run_id=run.id,
        decision=_decision("long"),
        market_snapshot=_snapshot(),
        risk_result=_risk_result("long"),
    )

    payloads = get_executions(db_session, symbol="BTCUSDT", status="filled", limit=5)

    assert payloads
    assert payloads[0]["execution_policy"]["policy_profile"].startswith("entry_btc_fast_")
    assert payloads[0]["execution_quality"]["signal_vs_execution_note"]
    assert payloads[0]["decision_summary"]["decision"] == "long"
    assert payloads[0]["decision_summary"]["timeframe"] == "15m"


def test_execution_quality_report_api(monkeypatch, tmp_path) -> None:
    test_engine = create_engine(f"sqlite:///{tmp_path / 'execution_report.db'}", future=True)
    TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=test_engine)
    monkeypatch.setattr("trading_mvp.main.engine", test_engine)

    def override_get_db():
        with TestingSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestingSessionLocal() as session:
            settings_row = get_or_create_settings(session)
            session.add(settings_row)
            session.flush()

            run = AgentRun(
                role="trading_decision",
                trigger_event="test",
                schema_name="TradeDecision",
                status="completed",
                provider_name="test",
                summary="execution report test",
                input_payload={},
                output_payload={"decision": "long", "timeframe": "15m", "confidence": 0.81, "rationale_codes": ["TREND"]},
                metadata_json={},
            )
            session.add(run)
            session.flush()
            order = Order(
                symbol="BTCUSDT",
                decision_run_id=run.id,
                side="buy",
                order_type="limit",
                mode="live",
                status="filled",
                requested_quantity=0.01,
                requested_price=70000.0,
                filled_quantity=0.01,
                average_fill_price=70100.0,
                metadata_json={
                    "execution_policy": {
                        "policy_profile": "entry_btc_fast_elevated",
                        "symbol_risk_tier": "btc",
                        "timeframe_bucket": "fast",
                        "volatility_regime": "elevated",
                        "urgency": "high",
                    },
                    "execution_quality": {
                        "partial_fill_attempts": 1,
                        "repriced_attempts": 1,
                        "aggressive_fallback_used": True,
                        "realized_slippage_pct": 0.0014,
                        "fees_total": 0.15,
                        "realized_pnl_total": 2.0,
                        "net_realized_pnl_total": 1.85,
                        "execution_quality_status": "aggressive_completion",
                        "decision_quality_status": "profit",
                    },
                },
            )
            session.add(order)
            session.commit()

        with TestClient(app) as client:
            response = client.get("/api/executions/report")

        assert response.status_code == 200
        payload = response.json()
        assert payload["execution_quality_basis"] == "live_order_metadata_and_execution_ledger"
        assert len(payload["windows"]) == 2
        assert payload["windows"][0]["execution_quality_summary"]["aggressive_fallback_orders"] == 1
        assert payload["windows"][0]["decision_quality_summary"]["profitable_orders"] == 1
        assert payload["windows"][0]["profiles"][0]["policy_profile"] == "entry_btc_fast_elevated"
    finally:
        app.dependency_overrides.clear()
