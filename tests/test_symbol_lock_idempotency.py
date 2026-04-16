from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from trading_mvp.models import AuditEvent, Order, PendingEntryPlan
from trading_mvp.schemas import MarketCandle, MarketSnapshotPayload, RiskCheckResult, TradeDecision
from trading_mvp.services.execution import execute_live_trade
from trading_mvp.services.orchestrator import TradingOrchestrator
from trading_mvp.services.runtime_state import (
    build_execution_dedupe_key,
    get_execution_guard_detail,
    mark_sync_success,
)
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
        confidence=0.8,
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
        explanation_short="symbol lock regression",
        explanation_detailed="symbol-scoped execution lock and cycle dedupe regression coverage.",
    )


def _prime_live_settings(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    db_session.add(settings_row)
    db_session.flush()


def _mark_sync_fresh(settings_row) -> None:
    now = utcnow_naive()
    for scope in ("account", "positions", "open_orders", "protective_orders"):
        mark_sync_success(settings_row, scope=scope, synced_at=now)


class IdempotentExecutionClient:
    def __init__(self) -> None:
        self.entry_submitted = False
        self.new_order_calls: list[dict[str, object]] = []
        self.orders_by_id: dict[str, dict[str, object]] = {}

    def get_account_info(self):
        return {
            "availableBalance": "100.0",
            "totalWalletBalance": "100.0",
            "totalUnrealizedProfit": "0.0",
            "totalMarginBalance": "100.0",
        }

    def get_open_orders(self, symbol: str):
        return [dict(item) for item in self.orders_by_id.values()]

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
        self.new_order_calls.append(
            {
                "symbol": symbol,
                "side": side,
                "order_type": order_type,
                "client_order_id": client_order_id,
            }
        )
        if order_type in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}:
            order_id = "stop-1" if order_type == "STOP_MARKET" else "tp-1"
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
            return {"orderId": order_id, "clientOrderId": payload["clientOrderId"], "status": "NEW"}
        self.entry_submitted = True
        return {
            "orderId": "entry-1",
            "clientOrderId": client_order_id or "entry-1",
            "status": "FILLED",
            "executedQty": quantity or 0.01,
            "avgPrice": "70000",
        }

    def fetch_order(
        self,
        *,
        symbol: str,
        order_type: str | None = None,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, object]:
        if order_id and order_id in self.orders_by_id:
            return dict(self.orders_by_id[order_id])
        if client_order_id:
            for payload in self.orders_by_id.values():
                if str(payload.get("clientOrderId", "")) == client_order_id:
                    return dict(payload)
        raise RuntimeError("order not found")

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


def test_execute_live_trade_deduplicates_same_cycle_and_suppresses_duplicate_protection(
    monkeypatch,
    db_session,
) -> None:
    _prime_live_settings(db_session)
    settings_row = get_or_create_settings(db_session)
    client = IdempotentExecutionClient()
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: client)

    first = execute_live_trade(
        db_session,
        settings_row,
        decision_run_id=1,
        decision=_live_decision("long"),
        market_snapshot=_market_snapshot(),
        risk_result=_risk_result("long"),
        cycle_id="cycle-dedupe-1",
        snapshot_id=101,
    )
    second = execute_live_trade(
        db_session,
        settings_row,
        decision_run_id=1,
        decision=_live_decision("long"),
        market_snapshot=_market_snapshot(),
        risk_result=_risk_result("long"),
        cycle_id="cycle-dedupe-1",
        snapshot_id=101,
    )

    dedupe_key = build_execution_dedupe_key(
        cycle_id="cycle-dedupe-1",
        symbol="BTCUSDT",
        action="long",
    )
    guard_detail = get_execution_guard_detail(settings_row)
    events = list(
        db_session.scalars(
            select(AuditEvent)
            .where(AuditEvent.event_type == "live_execution_deduplicated")
            .order_by(AuditEvent.id)
        )
    )

    assert first["status"] == "filled"
    assert first["cycle_id"] == "cycle-dedupe-1"
    assert first["dedupe_key"] == dedupe_key
    assert second["status"] == "filled"
    assert second["order_id"] == first["order_id"]
    assert second["dedupe_suppressed"] is True
    assert second["dedupe_reason"] == "cycle_action_already_completed"
    assert len(client.new_order_calls) == 3
    assert len(list(db_session.scalars(select(Order).order_by(Order.id)))) == 3
    assert guard_detail["symbol_locks"] == {}
    assert guard_detail["dedupe_records"][dedupe_key]["status"] == "filled"
    assert events[-1].payload["dedupe_key"] == dedupe_key
    assert events[-1].payload["duplicate_reason"] == "cycle_action_already_completed"


def test_execute_live_trade_symbol_lock_suppresses_reentrant_duplicate(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    nested_results: list[dict[str, object]] = []

    def fake_body(
        session,
        settings_row,
        decision_run_id,
        decision,
        market_snapshot,
        risk_result,
        risk_row=None,
        client_order_id_seed=None,
        correlation_ids=None,
    ):
        guard_detail = get_execution_guard_detail(settings_row)
        assert "BTCUSDT" in guard_detail["symbol_locks"]
        nested_results.append(
            execute_live_trade(
                session,
                settings_row,
                decision_run_id=decision_run_id,
                decision=decision,
                market_snapshot=market_snapshot,
                risk_result=risk_result,
                risk_row=risk_row,
                cycle_id="cycle-lock-1",
                snapshot_id=202,
            )
        )
        return {
            "status": "filled",
            "order_id": 321,
            "client_order_id_seed": client_order_id_seed,
        }

    monkeypatch.setattr("trading_mvp.services.execution._execute_live_trade_body", fake_body)

    result = execute_live_trade(
        db_session,
        settings_row,
        decision_run_id=2,
        decision=_live_decision("long"),
        market_snapshot=_market_snapshot(),
        risk_result=_risk_result("long"),
        cycle_id="cycle-lock-1",
        snapshot_id=202,
    )

    dedupe_key = build_execution_dedupe_key(
        cycle_id="cycle-lock-1",
        symbol="BTCUSDT",
        action="long",
    )
    guard_detail = get_execution_guard_detail(settings_row)
    events = list(
        db_session.scalars(
            select(AuditEvent)
            .where(AuditEvent.event_type == "live_execution_deduplicated")
            .order_by(AuditEvent.id)
        )
    )

    assert result["status"] == "filled"
    assert nested_results[0]["status"] == "deduplicated"
    assert nested_results[0]["dedupe_suppressed"] is True
    assert nested_results[0]["dedupe_reason"] == "cycle_action_in_progress"
    assert nested_results[0]["reason_codes"] == ["DUPLICATE_EXECUTION_SUPPRESSED"]
    assert guard_detail["symbol_locks"] == {}
    assert guard_detail["dedupe_records"][dedupe_key]["status"] == "filled"
    assert events[-1].payload["duplicate_reason"] == "cycle_action_in_progress"


def test_orchestrator_persists_cycle_id_and_snapshot_id_on_entry_plan(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    settings_row = get_or_create_settings(db_session)
    _mark_sync_fresh(settings_row)
    db_session.add(settings_row)
    db_session.flush()

    class EnabledSettings:
        live_trading_env_enabled = True

    monkeypatch.setattr("trading_mvp.services.risk.get_settings", lambda: EnabledSettings())
    monkeypatch.setattr(
        TradingOrchestrator,
        "run_exchange_sync_cycle",
        lambda self, **kwargs: {"status": "ok", "symbols": [settings_row.default_symbol]},
    )

    result = TradingOrchestrator(db_session).run_decision_cycle(trigger_event="manual", upto_index=140)
    plan = db_session.scalar(select(PendingEntryPlan).where(PendingEntryPlan.plan_status == "armed"))

    assert plan is not None
    assert plan.symbol == "BTCUSDT"
    assert isinstance(result["cycle_id"], str)
    assert result["cycle_id"]
    assert result["entry_plan"] is not None
    assert plan.metadata_json["cycle_id"] == result["cycle_id"]
    assert plan.metadata_json["snapshot_id"] == result["market_snapshot_id"]
