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
from trading_mvp.services.scheduler import run_interval_decision_cycle, run_window
from trading_mvp.services.secret_store import encrypt_secret
from trading_mvp.services.seed import seed_demo_data
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive


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

    result = TradingOrchestrator(db_session).run_selected_symbols_cycle(trigger_event="realtime_cycle")
    db_session.commit()

    assert result["mode"] == "market_data_only"
    assert result["results"][0]["status"] == "market_data_only"
    assert db_session.scalar(select(MarketSnapshot).limit(1)) is not None
    assert db_session.scalar(select(FeatureSnapshot).limit(1)) is None
    assert db_session.scalar(select(AgentRun).limit(1)) is None
    assert db_session.scalar(select(RiskCheck).limit(1)) is None
    assert db_session.scalar(select(PnLSnapshot).limit(1)) is None
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

    result = run_window(db_session, "1h", triggered_by="scheduler")
    db_session.commit()

    scheduler_run = db_session.scalar(select(SchedulerRun).order_by(SchedulerRun.id.desc()).limit(1))
    assert result["window"] == "1h"
    assert result["outcome"]["mode"] == "market_refresh"
    assert scheduler_run is not None
    assert scheduler_run.workflow == "market_refresh"
    assert db_session.scalar(select(MarketSnapshot).limit(1)) is not None
    assert db_session.scalar(select(FeatureSnapshot).limit(1)) is None
    assert db_session.scalar(select(AgentRun).limit(1)) is None
    assert db_session.scalar(select(RiskCheck).limit(1)) is None


def test_pipeline_creates_risk_and_execution_records(monkeypatch, db_session) -> None:
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

    class EnabledSettings:
        live_trading_env_enabled = True

    monkeypatch.setattr("trading_mvp.services.risk.get_settings", lambda: EnabledSettings())

    def fake_execute_live_trade(session, settings_row, decision_run_id, decision, market_snapshot, risk_result, risk_row=None):
        order = Order(
            symbol=decision.symbol,
            decision_run_id=decision_run_id,
            risk_check_id=risk_row.id if risk_row is not None else None,
            position_id=None,
            side=decision.decision,
            order_type="market",
            mode="live",
            status="filled",
            external_order_id="sim-order-1",
            client_order_id="sim-client-1",
            reduce_only=False,
            close_only=False,
            parent_order_id=None,
            exchange_status="FILLED",
            last_exchange_update_at=utcnow_naive(),
            requested_quantity=0.01,
            requested_price=market_snapshot.latest_price,
            filled_quantity=0.01,
            average_fill_price=market_snapshot.latest_price,
            reason_codes=[],
            metadata_json={},
        )
        session.add(order)
        session.flush()
        session.add(
            Execution(
                order_id=order.id,
                position_id=None,
                symbol=decision.symbol,
                status="filled",
                external_trade_id="sim-trade-1",
                fill_price=market_snapshot.latest_price,
                fill_quantity=0.01,
                fee_paid=0.1,
                commission_asset="USDT",
                slippage_pct=0.0,
                realized_pnl=0.0,
                payload={"test": True},
            )
        )
        session.flush()
        return {"order_id": order.id, "status": "filled"}

    monkeypatch.setattr("trading_mvp.services.orchestrator.execute_live_trade", fake_execute_live_trade)

    orchestrator = TradingOrchestrator(db_session)
    result = orchestrator.run_decision_cycle(trigger_event="manual", upto_index=140)
    db_session.commit()

    assert result["decision"]["decision"] == "long"
    assert result["risk_result"]["allowed"] is True
    assert result["execution"] is not None
    assert db_session.scalar(select(Order).limit(1)) is not None
    assert db_session.scalar(select(Execution).limit(1)) is not None
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
    db_session.add(settings_row)
    db_session.flush()

    class EnabledSettings:
        live_trading_env_enabled = True

    monkeypatch.setattr("trading_mvp.services.risk.get_settings", lambda: EnabledSettings())

    def fail_execute(*args, **kwargs):
        raise AssertionError("historical replay must not place live orders")

    monkeypatch.setattr("trading_mvp.services.orchestrator.execute_live_trade", fail_execute)

    result = TradingOrchestrator(db_session).run_decision_cycle(trigger_event="historical_replay", upto_index=140)

    assert result["decision"]["decision"] == "long"
    assert result["risk_result"]["allowed"] is True
    assert result["execution"] is None


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


def test_run_interval_decision_cycle_marks_failure_instead_of_raising(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    db_session.add(settings_row)
    db_session.flush()

    def fail_cycle(self, trigger_event="realtime_cycle", **kwargs):
        raise RuntimeError("interval failure")

    monkeypatch.setattr(TradingOrchestrator, "run_selected_symbols_cycle", fail_cycle)

    result = run_interval_decision_cycle(db_session, triggered_by="scheduler")
    scheduler_run = db_session.scalar(select(SchedulerRun).order_by(SchedulerRun.id.desc()).limit(1))

    assert result["outcome"]["error"] == "interval failure"
    assert scheduler_run is not None
    assert scheduler_run.status == "failed"
