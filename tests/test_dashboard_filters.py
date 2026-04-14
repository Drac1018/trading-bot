from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from trading_mvp.database import Base, get_db
from trading_mvp.main import app
from trading_mvp.models import AuditEvent, Execution, Order, Position, RiskCheck, SchedulerRun
from trading_mvp.services.dashboard import (
    classify_audit_event,
    get_audit_timeline,
    get_executions,
    get_operator_dashboard,
    get_orders,
    get_overview,
    get_positions,
    get_profitability_dashboard,
)
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive


def _seed_profitability_dashboard_rows(db_session) -> None:
    from trading_mvp.models import AgentRun

    now = utcnow_naive()
    settings = get_or_create_settings(db_session)
    settings.ai_enabled = True
    settings.adaptive_signal_enabled = True
    settings.trading_paused = True
    settings.pause_reason_code = "MANUAL_USER_REQUEST"
    settings.pause_origin = "manual"
    db_session.add(settings)
    db_session.flush()

    long_run = AgentRun(
        role="trading_decision",
        trigger_event="realtime_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="openai",
        summary="btc long",
        input_payload={
            "features": {
                "regime": {
                    "primary_regime": "bullish",
                    "trend_alignment": "bullish_aligned",
                    "volatility_regime": "normal",
                    "weak_volume": False,
                    "momentum_weakening": False,
                }
            }
        },
        output_payload={
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "decision": "long",
            "confidence": 0.72,
            "rationale_codes": ["TREND_UP", "BREAKOUT"],
            "explanation_short": "AI long proposal",
            "stop_loss": 69400.0,
            "take_profit": 70800.0,
            "max_holding_minutes": 60,
        },
        metadata_json={},
        schema_valid=True,
    )
    hold_run = AgentRun(
        role="trading_decision",
        trigger_event="realtime_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="deterministic-mock",
        summary="eth hold",
        input_payload={
            "features": {
                "regime": {
                    "primary_regime": "range",
                    "trend_alignment": "range",
                    "volatility_regime": "normal",
                    "weak_volume": True,
                    "momentum_weakening": True,
                }
            }
        },
        output_payload={
            "symbol": "ETHUSDT",
            "timeframe": "1h",
            "decision": "hold",
            "confidence": 0.34,
            "rationale_codes": ["RANGE_CHOP"],
            "explanation_short": "AI hold proposal",
            "max_holding_minutes": 120,
        },
        metadata_json={},
        schema_valid=True,
    )
    db_session.add_all([long_run, hold_run])
    db_session.flush()
    long_run.created_at = now - timedelta(hours=2)
    hold_run.created_at = now - timedelta(minutes=40)

    db_session.add_all(
        [
            RiskCheck(
                symbol="BTCUSDT",
                decision_run_id=long_run.id,
                allowed=True,
                decision="long",
                reason_codes=[],
                approved_risk_pct=0.01,
                approved_leverage=2.0,
                payload={"allowed": True, "decision": "long", "reason_codes": []},
            ),
            RiskCheck(
                symbol="ETHUSDT",
                decision_run_id=hold_run.id,
                allowed=False,
                decision="hold",
                reason_codes=["TRADING_PAUSED", "HOLD_DECISION"],
                approved_risk_pct=0.0,
                approved_leverage=0.0,
                payload={"allowed": False, "decision": "hold", "reason_codes": ["TRADING_PAUSED", "HOLD_DECISION"]},
            ),
        ]
    )
    db_session.flush()

    position = Position(
        symbol="BTCUSDT",
        mode="live",
        side="long",
        status="closed",
        quantity=0.01,
        entry_price=70000.0,
        mark_price=70800.0,
        leverage=2.0,
        stop_loss=69400.0,
        take_profit=70800.0,
        realized_pnl=8.0,
        unrealized_pnl=0.0,
        metadata_json={},
        opened_at=now - timedelta(minutes=90),
        closed_at=now - timedelta(minutes=20),
    )
    db_session.add(position)
    db_session.flush()

    entry_order = Order(
        symbol="BTCUSDT",
        decision_run_id=long_run.id,
        position_id=position.id,
        side="buy",
        order_type="limit",
        mode="live",
        status="filled",
        external_order_id="btc-entry",
        requested_quantity=0.01,
        requested_price=70000.0,
        filled_quantity=0.01,
        average_fill_price=70008.0,
        reason_codes=[],
        metadata_json={
            "execution_policy": {"policy_profile": "entry_btc_fast"},
            "execution_quality": {
                "partial_fill_attempts": 1,
                "repriced_attempts": 1,
                "aggressive_fallback_used": False,
                "realized_slippage_pct": 0.0012,
                "fees_total": 0.4,
                "realized_pnl_total": 8.0,
                "net_realized_pnl_total": 7.6,
                "decision_quality_status": "profit",
                "execution_quality_status": "repriced_limit_fill",
            },
        },
    )
    exit_order = Order(
        symbol="BTCUSDT",
        decision_run_id=long_run.id,
        position_id=position.id,
        side="sell",
        order_type="TAKE_PROFIT_MARKET",
        mode="live",
        status="filled",
        external_order_id="btc-tp",
        reduce_only=True,
        close_only=True,
        requested_quantity=0.01,
        requested_price=70800.0,
        filled_quantity=0.01,
        average_fill_price=70800.0,
        reason_codes=[],
        metadata_json={},
    )
    db_session.add_all([entry_order, exit_order])
    db_session.flush()

    db_session.add_all(
        [
            Execution(
                order_id=entry_order.id,
                position_id=position.id,
                symbol="BTCUSDT",
                status="filled",
                external_trade_id="btc-entry-fill",
                fill_price=70008.0,
                fill_quantity=0.01,
                fee_paid=0.2,
                commission_asset="USDT",
                slippage_pct=0.0012,
                realized_pnl=0.0,
                payload={},
            ),
            Execution(
                order_id=exit_order.id,
                position_id=position.id,
                symbol="BTCUSDT",
                status="filled",
                external_trade_id="btc-tp-fill",
                fill_price=70800.0,
                fill_quantity=0.01,
                fee_paid=0.2,
                commission_asset="USDT",
                slippage_pct=0.0,
                realized_pnl=8.0,
                payload={},
            ),
        ]
    )
    db_session.flush()

    db_session.add(
        SchedulerRun(
            schedule_window="15m",
            workflow="realtime_cycle",
            status="completed",
            triggered_by="system",
            next_run_at=now + timedelta(minutes=15),
            outcome={"decision_run_id": long_run.id},
        )
    )
    db_session.add_all(
        [
            AuditEvent(
                event_type="decision_cycle_completed",
                entity_type="agent_run",
                entity_id=str(long_run.id),
                severity="info",
                message="Latest decision cycle completed.",
                payload={"decision_run_id": long_run.id},
            ),
            AuditEvent(
                event_type="risk_blocked",
                entity_type="risk_check",
                entity_id=str(hold_run.id),
                severity="warning",
                message="Hold decision remained blocked.",
                payload={"reason_codes": ["TRADING_PAUSED", "HOLD_DECISION"]},
            ),
        ]
    )
    db_session.flush()


def test_order_and_execution_filters(db_session) -> None:
    primary_order = Order(
        symbol="BTCUSDT",
        side="buy",
        order_type="market",
        mode="live",
        status="filled",
        external_order_id="btc-order-1",
        requested_quantity=0.01,
        requested_price=65000.0,
    )
    secondary_order = Order(
        symbol="ETHUSDT",
        side="sell",
        order_type="limit",
        mode="live",
        status="rejected",
        external_order_id="eth-order-1",
        requested_quantity=0.2,
        requested_price=3200.0,
    )
    db_session.add_all([primary_order, secondary_order])
    db_session.flush()

    db_session.add_all(
        [
            Execution(
                order_id=primary_order.id,
                symbol="BTCUSDT",
                status="filled",
                external_trade_id="btc-trade-1",
                fill_price=65010.0,
                fill_quantity=0.01,
                payload={},
            ),
            Execution(
                order_id=secondary_order.id,
                symbol="ETHUSDT",
                status="rejected",
                external_trade_id="eth-trade-1",
                fill_price=3195.0,
                fill_quantity=0.2,
                payload={},
            ),
        ]
    )
    db_session.flush()

    filtered_orders = get_orders(db_session, symbol="BTCUSDT", status="filled", search="btc")
    filtered_executions = get_executions(db_session, symbol="BTCUSDT", status="filled", search="btc")

    assert len(filtered_orders) == 1
    assert filtered_orders[0]["symbol"] == "BTCUSDT"
    assert len(filtered_executions) == 1
    assert filtered_executions[0]["symbol"] == "BTCUSDT"


def test_audit_filters(db_session) -> None:
    db_session.add_all(
        [
            AuditEvent(
                event_type="live_sync",
                entity_type="binance",
                entity_id="BTCUSDT",
                severity="info",
                message="Live exchange state synchronized.",
                payload={},
            ),
            AuditEvent(
                event_type="backlog_auto_applied",
                entity_type="product_backlog",
                entity_id="1",
                severity="warning",
                message="Supported backlog item was auto-applied.",
                payload={},
            ),
        ]
    )
    db_session.flush()

    filtered = get_audit_timeline(db_session, event_type="backlog_auto_applied", severity="warning", search="auto")

    assert len(filtered) == 1
    assert filtered[0]["event_type"] == "backlog_auto_applied"
    assert filtered[0]["event_category"] == "health_system"


def test_audit_event_categories_are_deterministic() -> None:
    assert classify_audit_event("risk_check", "risk_check", {}) == "risk"
    assert classify_audit_event("live_limit_partial_fill", "order", {}) == "execution"
    assert classify_audit_event("trading_paused", "settings", {}) == "approval_control"
    assert classify_audit_event("protection_recreate_attempted", "position", {}) == "protection"
    assert classify_audit_event("live_sync_failed", "binance", {}) == "health_system"
    assert classify_audit_event("agent_output", "agent_run", {}) == "ai_decision"


def test_overview_and_positions_include_protection_status(db_session) -> None:
    settings = get_or_create_settings(db_session)
    settings.trading_paused = True
    settings.pause_reason_code = "PROTECTIVE_ORDER_FAILURE"
    settings.pause_origin = "system"
    settings.pause_reason_detail = {
        "detail": "protective verification failed",
        "auto_resume": {"status": "not_eligible", "blockers": ["MISSING_PROTECTIVE_ORDERS"]},
    }
    db_session.flush()

    position = Position(
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
    db_session.add(position)
    db_session.flush()

    db_session.add(
        Order(
            symbol="BTCUSDT",
            position_id=position.id,
            side="sell",
            order_type="stop_market",
            mode="live",
            status="pending",
            external_order_id="protect-stop-1",
            reduce_only=True,
            close_only=True,
            requested_quantity=0.01,
            requested_price=69000.0,
            filled_quantity=0.0,
            average_fill_price=0.0,
            metadata_json={},
        )
    )
    db_session.add(
        RiskCheck(
            symbol="BTCUSDT",
            decision="long",
            allowed=False,
            reason_codes=["TRADING_PAUSED", "LIVE_APPROVAL_REQUIRED"],
            approved_risk_pct=0.0,
            approved_leverage=0.0,
            payload={"reason_codes": ["TRADING_PAUSED", "LIVE_APPROVAL_REQUIRED"]},
        )
    )
    db_session.flush()

    overview = get_overview(db_session)
    positions = get_positions(db_session)

    assert overview.open_positions == 1
    assert overview.unprotected_positions == 1
    assert overview.operating_state == "PAUSED"
    assert overview.trading_paused is True
    assert overview.pause_reason_code == "PROTECTIVE_ORDER_FAILURE"
    assert overview.pause_origin == "system"
    assert overview.guard_mode_reason_category == "pause"
    assert overview.guard_mode_reason_code == "PROTECTIVE_ORDER_FAILURE"
    assert overview.guard_mode_reason_message == "보호 주문 복구 실패로 가드 모드입니다."
    assert overview.auto_resume_status == "not_eligible"
    assert overview.auto_resume_last_blockers == ["MISSING_PROTECTIVE_ORDERS"]
    assert overview.latest_blocked_reasons == ["TRADING_PAUSED", "LIVE_APPROVAL_REQUIRED"]
    assert overview.pause_severity == "critical"
    assert overview.pause_recovery_class == "portfolio_unsafe"
    assert overview.protection_recovery_status == "idle"
    assert overview.protection_recovery_active is False
    assert overview.missing_protection_symbols == ["BTCUSDT"]
    assert overview.missing_protection_items == {"BTCUSDT": ["take_profit"]}
    assert overview.pnl_summary["basis"] == "execution_ledger_truth"
    assert "status" in overview.account_sync_summary
    assert "headroom" in overview.exposure_summary
    assert "entry" in overview.execution_policy_summary
    assert "primary_regime" in overview.market_context_summary
    assert "mode" in overview.adaptive_protection_summary
    assert "status" in overview.adaptive_signal_summary
    assert overview.position_protection_summary[0]["symbol"] == "BTCUSDT"
    assert overview.position_protection_summary[0]["missing_components"] == ["take_profit"]
    assert overview.position_protection_summary[0]["status"] == "missing"
    assert positions[0]["status"] == "open"
    assert positions[0]["protection_status"] == "missing"
    assert positions[0]["protected"] is False
    assert positions[0]["protective_order_count"] == 1
    assert positions[0]["missing_components"] == ["take_profit"]


def test_positions_hide_closed_rows_and_do_not_mark_them_missing(db_session) -> None:
    closed_position = Position(
        symbol="BTCUSDT",
        mode="live",
        side="long",
        status="closed",
        quantity=0.0,
        entry_price=70000.0,
        mark_price=70050.0,
        leverage=2.0,
        stop_loss=69000.0,
        take_profit=72000.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        metadata_json={},
    )
    db_session.add(closed_position)
    db_session.flush()

    positions = get_positions(db_session)
    overview = get_overview(db_session)

    assert positions == []
    assert overview.open_positions == 0
    assert overview.position_protection_summary == []


def test_profitability_dashboard_groups_performance_execution_and_blocked_context(db_session) -> None:
    _seed_profitability_dashboard_rows(db_session)

    payload = get_profitability_dashboard(db_session)

    assert [item.window_label for item in payload.windows] == ["24h", "7d", "30d"]
    assert payload.windows[0].rationale_winners
    assert payload.windows[0].top_regimes
    assert payload.windows[0].top_symbols
    assert payload.execution_windows
    assert payload.execution_windows[0].worst_profiles
    assert payload.hold_blocked_summary.latest_blocked_reasons == ["TRADING_PAUSED", "HOLD_DECISION"]
    assert payload.adaptive_signal_summary["status"] in {"active", "neutral", "insufficient_data", "disabled"}
    assert payload.latest_decision is not None
    assert payload.latest_risk is not None


def test_operator_dashboard_groups_control_decision_risk_execution_and_audit(db_session) -> None:
    _seed_profitability_dashboard_rows(db_session)

    payload = get_operator_dashboard(db_session)

    assert payload.control.can_enter_new_position is False
    assert payload.control.trading_paused is True
    assert payload.control.scheduler_window == "15m"
    assert payload.market_signal.performance_windows[0].window_label == "24h"
    assert payload.ai_decision.decision in {"hold", "long"}
    assert payload.risk_guard.reason_codes == ["TRADING_PAUSED", "HOLD_DECISION"]
    assert payload.execution.decision_run_id == payload.ai_decision.decision_run_id
    assert payload.execution.order_id is None
    assert payload.audit_events[0].event_type in {"decision_cycle_completed", "risk_blocked"}


def test_profitability_dashboard_api_returns_windowed_snapshot(tmp_path, monkeypatch) -> None:
    test_engine = create_engine(f"sqlite:///{tmp_path / 'profitability_dashboard.db'}", future=True)
    TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=test_engine)
    monkeypatch.setattr("trading_mvp.main.engine", test_engine)

    def override_get_db():
        with TestingSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestingSessionLocal() as session:
            _seed_profitability_dashboard_rows(session)
            session.commit()

        with TestClient(app) as client:
            response = client.get("/api/dashboard/profitability")

        assert response.status_code == 200
        payload = response.json()
        assert payload["windows"][0]["window_label"] == "24h"
        assert "rationale_winners" in payload["windows"][0]
        assert "rationale_losers" in payload["windows"][0]
        assert "execution_windows" in payload
        assert payload["execution_windows"][0]["worst_profiles"]
        assert payload["hold_blocked_summary"]["latest_blocked_reasons"] == ["TRADING_PAUSED", "HOLD_DECISION"]
    finally:
        app.dependency_overrides.clear()


def test_operator_dashboard_api_returns_operator_flow(tmp_path, monkeypatch) -> None:
    test_engine = create_engine(f"sqlite:///{tmp_path / 'operator_dashboard.db'}", future=True)
    TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=test_engine)
    monkeypatch.setattr("trading_mvp.main.engine", test_engine)

    def override_get_db():
        with TestingSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestingSessionLocal() as session:
            _seed_profitability_dashboard_rows(session)
            session.commit()

        with TestClient(app) as client:
            response = client.get("/api/dashboard/operator")

        assert response.status_code == 200
        payload = response.json()
        assert payload["control"]["trading_paused"] is True
        assert payload["market_signal"]["performance_windows"][0]["window_label"] == "24h"
        assert payload["risk_guard"]["reason_codes"] == ["TRADING_PAUSED", "HOLD_DECISION"]
        assert payload["execution"]["decision_run_id"] == payload["ai_decision"]["decision_run_id"]
        assert payload["execution"]["order_id"] is None
        assert len(payload["audit_events"]) >= 1
    finally:
        app.dependency_overrides.clear()


def test_audit_api_returns_event_category(tmp_path, monkeypatch) -> None:
    test_engine = create_engine(f"sqlite:///{tmp_path / 'audit_categories.db'}", future=True)
    TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=test_engine)
    monkeypatch.setattr("trading_mvp.main.engine", test_engine)

    def override_get_db():
        with TestingSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestingSessionLocal() as session:
            session.add(
                AuditEvent(
                    event_type="live_execution_rejected",
                    entity_type="order",
                    entity_id="1",
                    severity="warning",
                    message="Execution rejected.",
                    payload={},
                )
            )
            session.commit()

        with TestClient(app) as client:
            response = client.get("/api/audit")

        assert response.status_code == 200
        payload = response.json()
        assert payload[0]["event_type"] == "live_execution_rejected"
        assert payload[0]["event_category"] == "execution"
    finally:
        app.dependency_overrides.clear()
