from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from trading_mvp.models import (
    AgentRun,
    AuditEvent,
    Execution,
    FeatureSnapshot,
    MarketSnapshot,
    Order,
    PendingEntryPlan,
    PnLSnapshot,
    RiskCheck,
    SchedulerRun,
    User,
)
from trading_mvp.schemas import MarketCandle, MarketSnapshotPayload, RiskCheckResult, TradeDecision
from trading_mvp.services.binance import BinanceAPIError
from trading_mvp.services.execution import execute_live_trade, sync_live_state
from trading_mvp.services.market_data import build_market_snapshot
from trading_mvp.services.orchestrator import TradingOrchestrator
from trading_mvp.services.runtime_state import mark_sync_success
from trading_mvp.services.scheduler import (
    is_interval_decision_due,
    run_exchange_sync_cycle,
    run_interval_decision_cycle,
    run_market_refresh_cycle,
    run_position_management_cycle,
    run_window,
)
from trading_mvp.services.secret_store import encrypt_secret
from trading_mvp.services.seed import seed_demo_data
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive


def _mark_pipeline_sync_fresh(settings_row) -> None:
    now = utcnow_naive()
    for scope in ("account", "positions", "open_orders", "protective_orders"):
        mark_sync_success(settings_row, scope=scope, synced_at=now)


def test_seed_bootstraps_without_demo_trading_data(db_session) -> None:
    output = seed_demo_data(db_session)
    assert output["status"] == "bootstrapped"
    assert db_session.scalar(select(User).limit(1)) is not None
    assert db_session.scalar(select(AgentRun).limit(1)) is None


def test_ai_disabled_collects_market_data_only(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = False
    settings_row.binance_market_data_enabled = True
    db_session.add(settings_row)
    db_session.flush()

    snapshot = MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=utcnow_naive(),
        latest_price=70000.0,
        latest_volume=1000.0,
        candle_count=1,
        is_stale=False,
        is_complete=True,
        candles=[
            MarketCandle(
                timestamp=utcnow_naive(),
                open=69900.0,
                high=70100.0,
                low=69800.0,
                close=70000.0,
                volume=1000.0,
            )
        ],
    )

    monkeypatch.setattr("trading_mvp.services.orchestrator.build_market_snapshot", lambda **kwargs: snapshot)
    monkeypatch.setattr(
        "trading_mvp.services.orchestrator.build_market_context",
        lambda **kwargs: {"15m": snapshot, "1h": snapshot.model_copy(update={"timeframe": "1h"}), "4h": snapshot.model_copy(update={"timeframe": "4h"})},
    )

    result = TradingOrchestrator(db_session).run_selected_symbols_cycle(trigger_event="realtime_cycle")
    db_session.commit()

    assert result["mode"] == "market_data_only"
    assert result["results"][0]["status"] == "market_data_only"
    assert db_session.scalar(select(MarketSnapshot).limit(1)) is not None
    assert db_session.scalar(select(FeatureSnapshot).limit(1)) is None
    assert db_session.scalar(select(AgentRun).limit(1)) is None
    assert db_session.scalar(select(RiskCheck).limit(1)) is None
    latest_pnl = db_session.scalar(select(PnLSnapshot).limit(1))
    if latest_pnl is not None:
        assert latest_pnl.daily_pnl == 0.0
        assert latest_pnl.cumulative_pnl == 0.0
        assert latest_pnl.consecutive_losses == 0
    assert db_session.scalar(select(AuditEvent).limit(1)) is None


def test_ai_disabled_hourly_window_creates_only_market_snapshots(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = False
    settings_row.binance_market_data_enabled = True
    db_session.add(settings_row)
    db_session.flush()

    snapshot = MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=utcnow_naive(),
        latest_price=70000.0,
        latest_volume=1000.0,
        candle_count=1,
        is_stale=False,
        is_complete=True,
        candles=[
            MarketCandle(
                timestamp=utcnow_naive(),
                open=69900.0,
                high=70100.0,
                low=69800.0,
                close=70000.0,
                volume=1000.0,
            )
        ],
    )

    monkeypatch.setattr("trading_mvp.services.orchestrator.build_market_snapshot", lambda **kwargs: snapshot)
    monkeypatch.setattr(
        "trading_mvp.services.orchestrator.build_market_context",
        lambda **kwargs: {"15m": snapshot, "1h": snapshot.model_copy(update={"timeframe": "1h"}), "4h": snapshot.model_copy(update={"timeframe": "4h"})},
    )

    result = run_window(db_session, "1h", triggered_by="scheduler")
    db_session.commit()

    assert result["status"] == "market_data_only"
    assert db_session.scalar(select(MarketSnapshot).limit(1)) is not None
    assert db_session.scalar(select(FeatureSnapshot).limit(1)) is None
    assert db_session.scalar(select(AgentRun).limit(1)) is None
    assert db_session.scalar(select(RiskCheck).limit(1)) is None


def test_ai_enabled_hourly_window_only_refreshes_market_data(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.binance_market_data_enabled = True
    db_session.add(settings_row)
    db_session.flush()

    snapshot = MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=utcnow_naive(),
        latest_price=70000.0,
        latest_volume=1000.0,
        candle_count=1,
        is_stale=False,
        is_complete=True,
        candles=[
            MarketCandle(
                timestamp=utcnow_naive(),
                open=69900.0,
                high=70100.0,
                low=69800.0,
                close=70000.0,
                volume=1000.0,
            )
        ],
    )

    monkeypatch.setattr("trading_mvp.services.orchestrator.build_market_snapshot", lambda **kwargs: snapshot)
    monkeypatch.setattr(
        "trading_mvp.services.orchestrator.build_market_context",
        lambda **kwargs: {"15m": snapshot, "1h": snapshot.model_copy(update={"timeframe": "1h"}), "4h": snapshot.model_copy(update={"timeframe": "4h"})},
    )

    result = run_window(db_session, "1h", triggered_by="scheduler")
    db_session.commit()

    scheduler_run = db_session.scalar(select(SchedulerRun).order_by(SchedulerRun.id.desc()).limit(1))
    assert result["window"] == "1h"
    assert result["outcome"]["mode"] == "market_refresh"
    assert scheduler_run is not None
    assert scheduler_run.workflow == "market_refresh_cycle"
    assert db_session.scalar(select(MarketSnapshot).limit(1)) is not None
    assert db_session.scalar(select(FeatureSnapshot).limit(1)) is None
    assert db_session.scalar(select(AgentRun).limit(1)) is None
    assert db_session.scalar(select(RiskCheck).limit(1)) is None


def test_daily_review_window_runs_without_backlog_workflow(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    db_session.add(settings_row)
    db_session.flush()

    result = run_window(db_session, "24h", triggered_by="scheduler")
    db_session.commit()

    scheduler_run = db_session.scalar(select(SchedulerRun).order_by(SchedulerRun.id.desc()).limit(1))
    assert scheduler_run is not None
    assert scheduler_run.workflow == "scheduled_review"
    assert result["workflow"] == "scheduled_review"
    assert result["status"] == "success"
    assert result["outcome"]["window"] == "24h"
    assert result["outcome"]["status"] == "skipped"
    assert result["outcome"]["reason"] == "DAILY_REVIEW_NO_ACTIVE_WORKFLOW"


def test_pipeline_creates_risk_and_execution_records(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    _mark_pipeline_sync_fresh(settings_row)
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

    def fake_execute_live_trade(
        session,
        settings_row,
        decision_run_id,
        decision,
        market_snapshot,
        risk_result,
        risk_row=None,
        cycle_id=None,
        snapshot_id=None,
        idempotency_key=None,
    ):
        raise AssertionError("new entry decision should arm a pending plan instead of executing immediately")

    monkeypatch.setattr("trading_mvp.services.orchestrator.execute_live_trade", fake_execute_live_trade)

    orchestrator = TradingOrchestrator(db_session)
    result = orchestrator.run_decision_cycle(trigger_event="manual", upto_index=140)
    db_session.commit()
    decision_run = db_session.get(AgentRun, result["decision_run_id"])

    assert result["decision"]["decision"] == "long"
    assert result["risk_result"]["allowed"] is False
    assert "ENTRY_TRIGGER_NOT_MET" in result["risk_result"]["blocked_reason_codes"]
    assert result["execution"] is None
    assert result["status"] == "entry_plan_armed"
    assert result["entry_plan"] is not None
    assert result["entry_plan"]["plan_status"] == "armed"
    assert result["decision_reference"]["market_snapshot_id"] == result["market_snapshot_id"]
    assert result["decision_reference"]["market_snapshot_source"] == "refreshed"
    assert result["decision_reference"]["market_snapshot_stale"] is False
    assert result["decision_reference"]["freshness_blocking"] is False
    assert result["decision_reference"]["account_sync_at"] is not None
    assert result["decision_reference"]["positions_sync_at"] is not None
    assert decision_run is not None
    assert decision_run.input_payload["decision_reference"]["market_snapshot_id"] == result["market_snapshot_id"]
    assert decision_run.input_payload["decision_reference"]["sync_freshness_summary"]["account"]["stale"] is False
    pending_plan = db_session.scalar(select(PendingEntryPlan).limit(1))
    assert pending_plan is not None
    assert pending_plan.plan_status == "armed"
    assert db_session.scalar(select(Order).limit(1)) is None
    assert db_session.scalar(select(Execution).limit(1)) is None
    assert db_session.scalar(select(RiskCheck).limit(1)) is not None
    assert db_session.scalar(select(AuditEvent).limit(1)) is not None


def test_historical_replay_never_executes_live(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    _mark_pipeline_sync_fresh(settings_row)
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

    def fail_execute(*args, **kwargs):
        raise AssertionError("historical replay must not place live orders")

    monkeypatch.setattr("trading_mvp.services.orchestrator.execute_live_trade", fail_execute)

    result = TradingOrchestrator(db_session).run_decision_cycle(trigger_event="historical_replay", upto_index=140)

    assert result["decision"]["decision"] == "long"
    assert "ENTRY_TRIGGER_NOT_MET" in result["risk_result"]["blocked_reason_codes"]
    assert result["execution"] is None
    assert result["entry_plan"] is None


def test_runtime_market_snapshot_requires_binance_data() -> None:
    try:
        build_market_snapshot(symbol="BTCUSDT", timeframe="15m", use_binance=False)
    except RuntimeError as exc:
        assert "Binance 실데이터" in str(exc)
    else:
        raise AssertionError("runtime snapshot should not fall back to synthetic data")


def test_live_sync_persists_partial_fill_and_position(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    order = Order(
        symbol="BTCUSDT",
        decision_run_id=None,
        risk_check_id=None,
        position_id=None,
        side="long",
        order_type="market",
        mode="live",
        status="pending",
        external_order_id="1001",
        client_order_id="mvp-test",
        reduce_only=False,
        close_only=False,
        parent_order_id=None,
        exchange_status="NEW",
        last_exchange_update_at=None,
        requested_quantity=0.5,
        requested_price=65000.0,
        filled_quantity=0.0,
        average_fill_price=0.0,
        reason_codes=[],
        metadata_json={},
    )
    db_session.add(order)
    db_session.flush()

    class FakeClient:
        def get_account_info(self):
            return {
                "availableBalance": "100.0",
                "totalWalletBalance": "100.0",
                "totalUnrealizedProfit": "2.5",
                "totalMarginBalance": "102.5",
            }

        def get_order(self, *, symbol, order_id=None, client_order_id=None):
            return {"orderId": "1001", "status": "PARTIALLY_FILLED", "executedQty": "0.25", "avgPrice": "65100"}

        def get_account_trades(self, *, symbol, order_id=None, limit=50):
            return [
                {
                    "id": 9001,
                    "price": "65100",
                    "qty": "0.25",
                    "commission": "1.2",
                    "commissionAsset": "USDT",
                    "realizedPnl": "0",
                }
            ]

        def get_open_orders(self, symbol=None):
            return [
                {"type": "STOP_MARKET", "stopPrice": "64000", "closePosition": True},
                {"type": "TAKE_PROFIT_MARKET", "stopPrice": "66500", "closePosition": True},
            ]

        def get_position_information(self, symbol=None):
            return [{"positionAmt": "0.25", "entryPrice": "65100", "markPrice": "65200", "leverage": "2"}]

    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings_row: FakeClient())

    result = sync_live_state(db_session, settings_row, symbol="BTCUSDT")
    db_session.commit()
    db_session.refresh(order)

    assert result["synced_orders"] == 1
    assert order.status == "partially_filled"
    assert order.filled_quantity == 0.25
    assert db_session.scalar(select(Execution).where(Execution.external_trade_id == "9001").limit(1)) is not None


def test_live_test_order_auto_adjusts_to_min_notional(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)

    class FakeClient:
        def get_symbol_filters(self, symbol):
            return {"tick_size": 0.1, "step_size": 0.001, "min_qty": 0.001, "min_notional": 100.0}

        def get_symbol_price(self, symbol):
            return 70000.0

        def normalize_order_quantity(self, symbol, quantity, *, reference_price=None, enforce_min_notional=True):
            assert symbol == "BTCUSDT"
            assert quantity == 0.001
            assert reference_price == 70000.0
            assert enforce_min_notional is True
            return 0.002

        def test_new_order(self, *, symbol, side, quantity):
            assert symbol == "BTCUSDT"
            assert side == "BUY"
            assert quantity == 0.002
            return {}

    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings_row: FakeClient())

    from trading_mvp.services.execution import run_live_test_order

    result = run_live_test_order(db_session, settings_row, symbol="BTCUSDT", side="BUY", quantity=0.001)

    assert result["requested_quantity"] == 0.001
    assert result["quantity"] == 0.002
    assert result["reference_price"] == 70000.0
    assert result["min_notional"] == 100.0


def test_execute_live_trade_uses_exchange_available_balance_for_sizing(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    captured: dict[str, float] = {}

    class FakeClient:
        def get_account_info(self):
            return {
                "availableBalance": "140.0",
                "totalWalletBalance": "140.0",
                "totalUnrealizedProfit": "0.0",
                "totalMarginBalance": "140.0",
            }

        def get_open_orders(self, symbol=None):
            return []

        def get_position_information(self, symbol=None):
            return []

        def change_initial_leverage(self, symbol, leverage):
            return {"leverage": leverage}

        def normalize_order_quantity(self, symbol, quantity, *, reference_price=None, enforce_min_notional=True):
            captured["requested_quantity"] = quantity
            return quantity

        def new_order(self, **kwargs):
            captured["submitted_quantity"] = kwargs["quantity"]
            return {
                "orderId": "3001",
                "clientOrderId": kwargs.get("client_order_id", "client"),
                "status": "FILLED",
                "executedQty": str(kwargs["quantity"]),
                "avgPrice": "70000",
            }

        def get_account_trades(self, *, symbol, order_id=None, limit=50):
            return []

    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda _: FakeClient())

    decision = TradeDecision(
        decision="long",
        confidence=0.8,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=70000.0,
        entry_zone_max=70000.0,
        stop_loss=None,
        take_profit=None,
        max_holding_minutes=60,
        risk_pct=0.01,
        leverage=1.0,
        rationale_codes=["TEST"],
        explanation_short="테스트 진입입니다.",
        explanation_detailed="실계좌 available balance 기준으로 sizing 되는지 확인합니다.",
    )
    snapshot = MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=utcnow_naive(),
        latest_price=70000.0,
        latest_volume=1000.0,
        candle_count=1,
        is_stale=False,
        is_complete=True,
        candles=[
            MarketCandle(
                timestamp=utcnow_naive(),
                open=69900.0,
                high=70100.0,
                low=69800.0,
                close=70000.0,
                volume=1000.0,
            )
        ],
    )
    risk_result = RiskCheckResult(
        allowed=True,
        decision="long",
        reason_codes=[],
        approved_risk_pct=0.01,
        approved_leverage=1.0,
        operating_mode="live",
        effective_leverage_cap=5.0,
        symbol_risk_tier="btc",
        exposure_metrics={},
    )

    result = execute_live_trade(
        db_session,
        settings_row,
        decision_run_id=1,
        decision=decision,
        market_snapshot=snapshot,
        risk_result=risk_result,
    )

    assert result["status"] == "filled"
    assert captured["requested_quantity"] == pytest.approx(0.002, rel=1e-6)
    assert captured["submitted_quantity"] == pytest.approx(0.002, rel=1e-6)


def test_execute_live_trade_rejects_insufficient_margin_without_raising(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)

    class FakeClient:
        def get_account_info(self):
            return {
                "availableBalance": "120.0",
                "totalWalletBalance": "120.0",
                "totalUnrealizedProfit": "0.0",
                "totalMarginBalance": "120.0",
            }

        def get_open_orders(self, symbol=None):
            return []

        def get_position_information(self, symbol=None):
            return []

        def change_initial_leverage(self, symbol, leverage):
            return {"leverage": leverage}

        def normalize_order_quantity(self, symbol, quantity, *, reference_price=None, enforce_min_notional=True):
            return quantity

        def new_order(self, **kwargs):
            raise BinanceAPIError(-2019, "Margin is insufficient.")

    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda _: FakeClient())

    decision = TradeDecision(
        decision="long",
        confidence=0.8,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=70000.0,
        entry_zone_max=70000.0,
        stop_loss=None,
        take_profit=None,
        max_holding_minutes=60,
        risk_pct=0.01,
        leverage=1.0,
        rationale_codes=["TEST"],
        explanation_short="테스트 진입입니다.",
        explanation_detailed="증거금 부족이 rejected 처리되는지 확인합니다.",
    )
    snapshot = MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=utcnow_naive(),
        latest_price=70000.0,
        latest_volume=1000.0,
        candle_count=1,
        is_stale=False,
        is_complete=True,
        candles=[
            MarketCandle(
                timestamp=utcnow_naive(),
                open=69900.0,
                high=70100.0,
                low=69800.0,
                close=70000.0,
                volume=1000.0,
            )
        ],
    )
    risk_result = RiskCheckResult(
        allowed=True,
        decision="long",
        reason_codes=[],
        approved_risk_pct=0.01,
        approved_leverage=1.0,
        operating_mode="live",
        effective_leverage_cap=5.0,
        symbol_risk_tier="btc",
        exposure_metrics={},
    )

    result = execute_live_trade(
        db_session,
        settings_row,
        decision_run_id=1,
        decision=decision,
        market_snapshot=snapshot,
        risk_result=risk_result,
    )

    order = db_session.scalar(select(Order).order_by(Order.id.desc()).limit(1))
    alert = db_session.scalar(select(AuditEvent).where(AuditEvent.event_type == "live_execution_rejected").limit(1))

    assert result["status"] == "rejected"
    assert "INSUFFICIENT_MARGIN" in result["reason_codes"]
    assert order is not None
    assert order.status == "rejected"
    assert alert is not None


def test_run_selected_symbols_cycle_isolates_symbol_failures(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT", "ETHUSDT"]
    db_session.add(settings_row)
    db_session.flush()

    def fake_run_decision_cycle(self, symbol=None, **kwargs):
        if symbol == "BTCUSDT":
            raise RuntimeError("boom")
        return {"symbol": symbol, "status": "ok"}

    monkeypatch.setattr(TradingOrchestrator, "run_decision_cycle", fake_run_decision_cycle)

    result = TradingOrchestrator(db_session).run_selected_symbols_cycle(trigger_event="realtime_cycle")

    assert result["failed_symbols"] == ["BTCUSDT"]
    assert result["results"][0]["status"] == "failed"
    assert result["results"][1]["status"] == "ok"


def test_run_selected_symbols_cycle_uses_candidate_selection_top_n(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
    db_session.add(settings_row)
    db_session.flush()
    executed_symbols: list[str] = []

    monkeypatch.setattr(
        TradingOrchestrator,
        "_rank_candidate_symbols",
        lambda self, **kwargs: {
            "mode": "correlation_aware_top_n",
            "max_selected": 2,
            "selected_symbols": ["BTCUSDT", "ETHUSDT"],
            "skipped_symbols": ["BNBUSDT"],
            "rankings": [
                {
                    "symbol": "BTCUSDT",
                    "selected": True,
                    "selection_reason": "ranked_top_n",
                    "score": {"total_score": 0.71, "correlation_penalty": 0.0},
                },
                {
                    "symbol": "ETHUSDT",
                    "selected": True,
                    "selection_reason": "ranked_top_n",
                    "score": {"total_score": 0.58, "correlation_penalty": 0.14},
                },
                {
                    "symbol": "BNBUSDT",
                    "selected": False,
                    "selection_reason": "correlation_limit",
                    "score": {"total_score": 0.22, "correlation_penalty": 0.36},
                },
            ],
        },
    )

    def fake_run_decision_cycle(self, symbol=None, **kwargs):
        executed_symbols.append(str(symbol))
        return {"symbol": symbol, "status": "ok"}

    monkeypatch.setattr(TradingOrchestrator, "run_decision_cycle", fake_run_decision_cycle)

    result = TradingOrchestrator(db_session).run_selected_symbols_cycle(trigger_event="realtime_cycle")

    assert executed_symbols == ["BTCUSDT", "ETHUSDT"]
    assert result["tracked_symbols"] == ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
    assert result["symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert result["candidate_selection"]["mode"] == "correlation_aware_top_n"
    assert result["candidate_selection"]["selected_symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert result["candidate_selection"]["skipped_symbols"] == ["BNBUSDT"]
    assert result["candidate_selection"]["rankings"][2]["score"]["correlation_penalty"] == 0.36


def test_run_exchange_sync_cycle_calls_live_sync_and_records_success(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    db_session.flush()
    calls: list[str | None] = []

    def fake_sync_live_state(session, settings_row, *, symbol=None):
        calls.append(symbol)
        return {"symbols": [symbol or settings_row.default_symbol], "synced_positions": 1, "synced_orders": 1}

    monkeypatch.setattr("trading_mvp.services.orchestrator.sync_live_state", fake_sync_live_state)

    result = TradingOrchestrator(db_session).run_exchange_sync_cycle(symbol="BTCUSDT", trigger_event="background_poll")

    assert result["status"] == "ok"
    assert calls == ["BTCUSDT"]


def test_run_exchange_sync_cycle_records_failure_without_raising(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    db_session.flush()

    def fake_sync_live_state(session, settings_row, *, symbol=None):
        raise RuntimeError("interval failure")

    monkeypatch.setattr("trading_mvp.services.orchestrator.sync_live_state", fake_sync_live_state)

    result = TradingOrchestrator(db_session).run_exchange_sync_cycle(symbol="BTCUSDT", trigger_event="background_poll")

    assert result["status"] == "error"
    assert result["symbol"] == "BTCUSDT"
    assert "interval failure" in result["error"]


def test_run_exchange_sync_cycle_marks_scopes_skipped_when_credentials_missing(db_session) -> None:
    settings_row = get_or_create_settings(db_session)

    result = TradingOrchestrator(db_session).run_exchange_sync_cycle(symbol="BTCUSDT", trigger_event="background_poll")

    assert result["status"] == "skipped"
    assert result["reason"] == "LIVE_CREDENTIALS_MISSING"
    sync_summary = result["sync_freshness_summary"]
    assert sync_summary["account"]["status"] == "skipped"
    assert sync_summary["positions"]["status"] == "skipped"
    assert sync_summary["open_orders"]["status"] == "skipped"
    assert sync_summary["protective_orders"]["status"] == "skipped"
    assert sync_summary["account"]["last_skip_reason"] == "LIVE_CREDENTIALS_MISSING"
    assert settings_row.pause_reason_detail["exchange_sync"]["account"]["last_skip_reason"] == "LIVE_CREDENTIALS_MISSING"


def test_scheduler_exchange_sync_cycle_records_workflow(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    db_session.flush()

    monkeypatch.setattr(
        "trading_mvp.services.orchestrator.sync_live_state",
        lambda session, settings_row, symbol=None: {"status": "ok", "symbols": ["BTCUSDT"]},
    )

    result = run_exchange_sync_cycle(db_session, triggered_by="scheduler")
    scheduler_run = db_session.scalar(select(SchedulerRun).order_by(SchedulerRun.id.desc()).limit(1))

    assert result["workflow"] == "exchange_sync_cycle"
    assert scheduler_run is not None
    assert scheduler_run.workflow == "exchange_sync_cycle"
    assert scheduler_run.status == "success"


def test_run_interval_decision_cycle_marks_failure_instead_of_raising(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    db_session.add(settings_row)
    db_session.flush()

    def fail_cycle(self, trigger_event="realtime_cycle", **kwargs):
        raise RuntimeError("interval failure")

    monkeypatch.setattr(TradingOrchestrator, "run_decision_cycle", fail_cycle)

    result = run_interval_decision_cycle(db_session, triggered_by="scheduler")
    scheduler_run = db_session.scalar(select(SchedulerRun).order_by(SchedulerRun.id.desc()).limit(1))

    assert result["results"][0]["outcome"]["error"] == "interval failure"
    assert scheduler_run is not None
    assert scheduler_run.status == "failed"


def test_scheduler_market_refresh_cycle_runs_without_ai_or_new_entry(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = False
    settings_row.tracked_symbols = ["BTCUSDT"]
    db_session.flush()

    snapshot = MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=utcnow_naive(),
        latest_price=70100.0,
        latest_volume=1000.0,
        candle_count=1,
        is_stale=False,
        is_complete=True,
        candles=[
            MarketCandle(
                timestamp=utcnow_naive(),
                open=70000.0,
                high=70200.0,
                low=69900.0,
                close=70100.0,
                volume=1000.0,
            )
        ],
    )
    monkeypatch.setattr("trading_mvp.services.orchestrator.build_market_snapshot", lambda **kwargs: snapshot)

    result = run_market_refresh_cycle(db_session, triggered_by="scheduler")

    assert result["workflow"] == "market_refresh_cycle"
    assert result["results"][0]["status"] == "success"
    assert db_session.scalar(select(MarketSnapshot).limit(1)) is not None
    assert db_session.scalar(select(AgentRun).limit(1)) is None
    assert db_session.scalar(select(Order).limit(1)) is None


def test_scheduler_position_management_cycle_does_not_create_new_entry(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.position_management_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_pipeline_sync_fresh(settings_row)
    db_session.flush()

    snapshot = MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=utcnow_naive(),
        latest_price=70050.0,
        latest_volume=1000.0,
        candle_count=1,
        is_stale=False,
        is_complete=True,
        candles=[
            MarketCandle(
                timestamp=utcnow_naive(),
                open=70000.0,
                high=70100.0,
                low=69900.0,
                close=70050.0,
                volume=1000.0,
            )
        ],
    )

    monkeypatch.setattr("trading_mvp.services.orchestrator.build_market_snapshot", lambda **kwargs: snapshot)
    monkeypatch.setattr(
        "trading_mvp.services.orchestrator.build_market_context",
        lambda **kwargs: {
            "15m": snapshot,
            "1h": snapshot.model_copy(update={"timeframe": "1h"}),
            "4h": snapshot.model_copy(update={"timeframe": "4h"}),
        },
    )
    monkeypatch.setattr(
        "trading_mvp.services.orchestrator.apply_position_management",
        lambda *args, **kwargs: {"status": "applied", "position_management_action": {"action": "tighten_stop"}},
    )

    from trading_mvp.models import Position

    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.01,
            entry_price=69500.0,
            mark_price=70050.0,
            leverage=2.0,
            stop_loss=69000.0,
            take_profit=71000.0,
            metadata_json={},
        )
    )
    db_session.flush()

    result = run_position_management_cycle(db_session, triggered_by="scheduler")

    assert result["workflow"] == "position_management_cycle"
    assert result["results"][0]["status"] == "success"
    assert db_session.scalar(select(AgentRun).limit(1)) is None
    assert db_session.scalar(select(RiskCheck).limit(1)) is None


def test_decision_cycle_skips_duplicate_same_candle_entry(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.live_trading_enabled = False
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_pipeline_sync_fresh(settings_row)
    db_session.flush()

    snapshot_time = utcnow_naive()
    snapshot = MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=snapshot_time,
        latest_price=70000.0,
        latest_volume=1000.0,
        candle_count=1,
        is_stale=False,
        is_complete=True,
        candles=[
            MarketCandle(
                timestamp=snapshot_time,
                open=69900.0,
                high=70100.0,
                low=69800.0,
                close=70000.0,
                volume=1000.0,
            )
        ],
    )
    monkeypatch.setattr("trading_mvp.services.orchestrator.build_market_snapshot", lambda **kwargs: snapshot)
    monkeypatch.setattr(
        "trading_mvp.services.orchestrator.build_market_context",
        lambda **kwargs: {
            "15m": snapshot,
            "1h": snapshot.model_copy(update={"timeframe": "1h"}),
            "4h": snapshot.model_copy(update={"timeframe": "4h"}),
        },
    )
    monkeypatch.setattr(
        TradingOrchestrator,
        "run_exchange_sync_cycle",
        lambda self, **kwargs: {"status": "ok", "symbols": ["BTCUSDT"]},
    )

    orchestrator = TradingOrchestrator(db_session)
    first = orchestrator.run_decision_cycle(trigger_event="manual", exchange_sync_checked=True)
    second = orchestrator.run_decision_cycle(trigger_event="manual", exchange_sync_checked=True)

    assert first["decision_run_id"] is not None
    assert second["status"] == "same_candle_skipped"
    assert second["decision_run_id"] is None


def test_symbol_due_uses_override_specific_interval(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT", "ETHUSDT", "XRPUSDT"]
    settings_row.decision_cycle_interval_minutes = 15
    settings_row.symbol_cadence_overrides = [
        {"symbol": "BTCUSDT", "decision_cycle_interval_minutes_override": 5, "ai_call_interval_minutes_override": 10},
        {"symbol": "ETHUSDT", "decision_cycle_interval_minutes_override": 10, "ai_call_interval_minutes_override": 15},
        {"symbol": "XRPUSDT", "decision_cycle_interval_minutes_override": 30, "ai_call_interval_minutes_override": 30},
    ]
    now = utcnow_naive()
    db_session.add_all(
        [
            SchedulerRun(
                schedule_window="5m",
                workflow="interval_decision_cycle",
                status="success",
                triggered_by="scheduler",
                next_run_at=now - timedelta(minutes=1),
                outcome={"symbol": "BTCUSDT"},
            ),
            SchedulerRun(
                schedule_window="10m",
                workflow="interval_decision_cycle",
                status="success",
                triggered_by="scheduler",
                next_run_at=now + timedelta(minutes=3),
                outcome={"symbol": "ETHUSDT"},
            ),
            SchedulerRun(
                schedule_window="30m",
                workflow="interval_decision_cycle",
                status="success",
                triggered_by="scheduler",
                next_run_at=now + timedelta(minutes=20),
                outcome={"symbol": "XRPUSDT"},
            ),
        ]
    )
    db_session.flush()

    assert is_interval_decision_due(db_session, "BTCUSDT") is True
    assert is_interval_decision_due(db_session, "ETHUSDT") is False
    assert is_interval_decision_due(db_session, "XRPUSDT") is False
