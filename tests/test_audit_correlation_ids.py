from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from trading_mvp.models import AuditEvent, RiskCheck, SystemHealthEvent
from trading_mvp.schemas import MarketCandle, MarketSnapshotPayload, RiskCheckResult, TradeDecision
from trading_mvp.services.binance import BinanceAPIError
from trading_mvp.services.execution import execute_live_trade
from trading_mvp.services.orchestrator import TradingOrchestrator
from trading_mvp.services.runtime_state import mark_sync_success
from trading_mvp.services.secret_store import encrypt_secret
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive


def _mark_sync_fresh(settings_row) -> None:
    now = utcnow_naive()
    for scope in ("account", "positions", "open_orders", "protective_orders"):
        mark_sync_success(settings_row, scope=scope, synced_at=now)


def _prime_live_settings(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    _mark_sync_fresh(settings_row)
    db_session.add(settings_row)
    db_session.flush()


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


def _live_decision(decision: str = "long") -> TradeDecision:
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
        explanation_short="audit correlation",
        explanation_detailed="correlation id propagation regression coverage.",
    )


def _risk_result(decision: str, *, cycle_id: str, snapshot_id: int) -> RiskCheckResult:
    return RiskCheckResult(
        allowed=True,
        decision=decision,  # type: ignore[arg-type]
        reason_codes=[],
        blocked_reason_codes=[],
        adjustment_reason_codes=[],
        approved_risk_pct=0.01,
        approved_leverage=2.0,
        approved_notional=1400.0,
        approved_projected_notional=1400.0,
        approved_qty=0.02,
        approved_quantity=0.02,
        snapshot_id=snapshot_id,
        cycle_id=cycle_id,
        operating_mode="live",
        operating_state="TRADABLE",
        effective_leverage_cap=5.0,
        symbol_risk_tier="btc",
        exposure_metrics={},
    )


def _persist_risk_row(
    db_session,
    *,
    risk_result: RiskCheckResult,
    decision_run_id: int,
    market_snapshot_id: int,
) -> RiskCheck:
    row = RiskCheck(
        symbol="BTCUSDT",
        decision_run_id=decision_run_id,
        market_snapshot_id=market_snapshot_id,
        allowed=risk_result.allowed,
        decision=risk_result.decision,
        reason_codes=list(risk_result.reason_codes),
        approved_risk_pct=risk_result.approved_risk_pct,
        approved_leverage=risk_result.approved_leverage,
        payload=risk_result.model_dump(mode="json"),
    )
    db_session.add(row)
    db_session.flush()
    return row


class RejectedExecutionClient:
    def get_account_info(self):
        return {
            "availableBalance": "120.0",
            "totalWalletBalance": "120.0",
            "totalUnrealizedProfit": "0.0",
            "totalMarginBalance": "120.0",
        }

    def get_open_orders(self, symbol: str):
        return []

    def get_position_information(self, symbol: str):
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

    def new_order(self, **kwargs):
        raise BinanceAPIError(-2019, "Margin is insufficient.")


class ProtectionVerifyFailureClient:
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
        if self.emergency_submitted or not self.entry_submitted:
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
            order_id = "algo-stop-fail" if order_type == "STOP_MARKET" else "algo-tp-fail"
            return {"orderId": order_id, "status": "NEW"}
        if reduce_only:
            self.emergency_submitted = True
            return {
                "orderId": "emergency-1",
                "clientOrderId": client_order_id or "emergency-1",
                "status": "FILLED",
                "executedQty": quantity or 0.01,
                "avgPrice": "69800",
            }
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
        raise RuntimeError("verify lookup missing")

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


def test_orchestrator_records_decision_and_risk_correlation_ids(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)

    class EnabledSettings:
        live_trading_env_enabled = True

    monkeypatch.setattr("trading_mvp.services.risk.get_settings", lambda: EnabledSettings())
    monkeypatch.setattr(
        TradingOrchestrator,
        "run_exchange_sync_cycle",
        lambda self, **kwargs: {"status": "ok", "symbols": ["BTCUSDT"]},
    )
    monkeypatch.setattr(
        "trading_mvp.services.orchestrator.execute_live_trade",
        lambda *args, **kwargs: {"status": "filled"},
    )

    result = TradingOrchestrator(db_session).run_decision_cycle(trigger_event="manual", upto_index=140)

    decision_event = db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "agent_output").order_by(AuditEvent.id.desc()).limit(1)
    )
    risk_event = db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "risk_check").order_by(AuditEvent.id.desc()).limit(1)
    )

    assert decision_event is not None
    assert decision_event.payload["cycle_id"] == result["cycle_id"]
    assert decision_event.payload["snapshot_id"] == result["market_snapshot_id"]
    assert decision_event.payload["decision_id"] == result["decision_run_id"]
    assert "risk_id" not in decision_event.payload

    assert risk_event is not None
    assert risk_event.payload["cycle_id"] == result["cycle_id"]
    assert risk_event.payload["snapshot_id"] == result["market_snapshot_id"]
    assert risk_event.payload["decision_id"] == result["decision_run_id"]
    assert risk_event.payload["risk_id"] == result["risk_check_id"]


def test_execution_attempt_and_reject_events_share_correlation_ids(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    settings_row = get_or_create_settings(db_session)
    decision_run_id = 900
    snapshot_id = 501
    cycle_id = "cycle-exec-1"
    risk_result = _risk_result("long", cycle_id=cycle_id, snapshot_id=snapshot_id)
    risk_row = _persist_risk_row(
        db_session,
        risk_result=risk_result,
        decision_run_id=decision_run_id,
        market_snapshot_id=snapshot_id,
    )
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda _: RejectedExecutionClient())

    result = execute_live_trade(
        db_session,
        settings_row,
        decision_run_id=decision_run_id,
        decision=_live_decision("long"),
        market_snapshot=_market_snapshot(),
        risk_result=risk_result,
        risk_row=risk_row,
        cycle_id=cycle_id,
        snapshot_id=snapshot_id,
    )

    attempted_event = db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.event_type == "live_execution_attempted")
        .order_by(AuditEvent.id.desc())
        .limit(1)
    )
    rejected_event = db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.event_type == "live_execution_rejected")
        .order_by(AuditEvent.id.desc())
        .limit(1)
    )

    assert result["status"] == "rejected"
    assert attempted_event is not None
    assert attempted_event.payload["cycle_id"] == cycle_id
    assert attempted_event.payload["snapshot_id"] == snapshot_id
    assert attempted_event.payload["decision_id"] == decision_run_id
    assert attempted_event.payload["risk_id"] == risk_row.id

    assert rejected_event is not None
    assert rejected_event.payload["cycle_id"] == cycle_id
    assert rejected_event.payload["snapshot_id"] == snapshot_id
    assert rejected_event.payload["decision_id"] == decision_run_id
    assert rejected_event.payload["risk_id"] == risk_row.id
    assert rejected_event.payload["execution_id"] == result["order_id"]


def test_protection_verify_failed_and_health_events_keep_same_correlation_ids(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    settings_row = get_or_create_settings(db_session)
    decision_run_id = 901
    snapshot_id = 777
    cycle_id = "cycle-protect-1"
    risk_result = _risk_result("long", cycle_id=cycle_id, snapshot_id=snapshot_id)
    risk_row = _persist_risk_row(
        db_session,
        risk_result=risk_result,
        decision_run_id=decision_run_id,
        market_snapshot_id=snapshot_id,
    )
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda _: ProtectionVerifyFailureClient())

    result = execute_live_trade(
        db_session,
        settings_row,
        decision_run_id=decision_run_id,
        decision=_live_decision("long"),
        market_snapshot=_market_snapshot(),
        risk_result=risk_result,
        risk_row=risk_row,
        cycle_id=cycle_id,
        snapshot_id=snapshot_id,
    )

    verify_failed_event = db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.event_type == "protection_verification_failed")
        .order_by(AuditEvent.id.desc())
        .limit(1)
    )
    degraded_health_event = db_session.scalar(
        select(SystemHealthEvent)
        .where(SystemHealthEvent.component == "live_execution")
        .order_by(SystemHealthEvent.id.desc())
        .limit(1)
    )

    assert result["status"] == "emergency_exit"
    assert result["protection_lifecycle"]["state"] == "verify_failed"
    assert verify_failed_event is not None
    assert verify_failed_event.payload["cycle_id"] == cycle_id
    assert verify_failed_event.payload["snapshot_id"] == snapshot_id
    assert verify_failed_event.payload["decision_id"] == decision_run_id
    assert verify_failed_event.payload["risk_id"] == risk_row.id
    assert verify_failed_event.payload["execution_id"] == result["order_id"]

    assert degraded_health_event is not None
    assert degraded_health_event.payload["cycle_id"] == cycle_id
    assert degraded_health_event.payload["snapshot_id"] == snapshot_id
    assert degraded_health_event.payload["decision_id"] == decision_run_id
    assert degraded_health_event.payload["risk_id"] == risk_row.id
    assert degraded_health_event.payload["execution_id"] == result["order_id"]
