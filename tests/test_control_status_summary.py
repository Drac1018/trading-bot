from __future__ import annotations

from datetime import timedelta

from trading_mvp.models import MarketSnapshot, RiskCheck
from trading_mvp.services.dashboard import get_operator_dashboard, get_overview
from trading_mvp.services.runtime_state import (
    mark_sync_success,
    record_binance_rest_issue,
    set_reconciliation_detail,
    set_user_stream_detail,
)
from trading_mvp.services.settings import get_or_create_settings, serialize_settings
from trading_mvp.time_utils import utcnow_naive


def _mark_fresh_sync_state(db_session, settings_row, *, exchange_can_trade: bool | None = None) -> None:
    detail: dict[str, object] = {"symbol": settings_row.default_symbol}
    if exchange_can_trade is not None:
        detail["exchange_can_trade"] = exchange_can_trade
    mark_sync_success(settings_row, scope="account", detail=detail)
    mark_sync_success(settings_row, scope="positions", detail={"symbol": settings_row.default_symbol})
    mark_sync_success(settings_row, scope="open_orders", detail={"symbol": settings_row.default_symbol})
    mark_sync_success(settings_row, scope="protective_orders", detail={"symbol": settings_row.default_symbol})
    db_session.add(settings_row)


def _seed_market_snapshot(db_session, settings_row) -> None:
    now = utcnow_naive()
    db_session.add(
        MarketSnapshot(
            symbol=settings_row.default_symbol,
            timeframe=settings_row.default_timeframe,
            snapshot_time=now - timedelta(minutes=1),
            latest_price=70000.0,
            latest_volume=1200.0,
            candle_count=96,
            is_stale=False,
            is_complete=True,
            payload={},
        )
    )


def test_control_status_summary_separates_pause_arm_and_current_cycle_risk(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=10)
    settings_row.trading_paused = True
    settings_row.pause_reason_code = "MANUAL_USER_REQUEST"
    settings_row.pause_origin = "manual"
    _mark_fresh_sync_state(db_session, settings_row, exchange_can_trade=True)
    _seed_market_snapshot(db_session, settings_row)
    db_session.add(
        RiskCheck(
            symbol=settings_row.default_symbol,
            decision="long",
            allowed=False,
            reason_codes=["ENTRY_TRIGGER_NOT_MET"],
            approved_risk_pct=0.0,
            approved_leverage=0.0,
            payload={"allowed": False, "decision": "long", "reason_codes": ["ENTRY_TRIGGER_NOT_MET"]},
        )
    )
    db_session.flush()

    serialized = serialize_settings(settings_row)
    summary = serialized["operational_status"]["control_status_summary"]

    assert serialized["live_execution_ready"] is False
    assert summary["exchange_can_trade"] is True
    assert summary["exchange_connectivity_state"] == "tradable"
    assert summary["app_live_armed"] is True
    assert summary["approval_window_open"] is True
    assert summary["paused"] is True
    assert summary["degraded"] is False
    assert summary["risk_allowed"] is False
    assert summary["blocked_reasons_current_cycle"] == ["ENTRY_TRIGGER_NOT_MET"]
    assert summary["blocked_reason_codes"] == ["ENTRY_TRIGGER_NOT_MET"]
    assert summary["degraded_reason_codes"] == ["PAUSED", "MANUAL_USER_REQUEST"]
    assert summary["protection_reason_codes"] == []


def test_control_status_summary_uses_system_approval_grace_window(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = False
    settings_row.live_execution_armed_until = None
    settings_row.trading_paused = True
    settings_row.pause_reason_code = "EXCHANGE_CONNECTIVITY_TEMPORARY_FAILURE"
    settings_row.pause_origin = "system"
    settings_row.pause_reason_detail = {
        "resume_context": {
            "live_execution_ready_before_pause": True,
            "approval_grace_until": (utcnow_naive() + timedelta(minutes=5)).isoformat(),
        }
    }
    db_session.flush()

    serialized = serialize_settings(settings_row)
    summary = serialized["operational_status"]["control_status_summary"]

    assert summary["app_live_armed"] is False
    assert summary["approval_window_open"] is True
    assert summary["paused"] is True
    assert summary["exchange_connectivity_state"] == "degraded"


def test_dashboard_includes_control_status_summary_for_degraded_runtime(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = None
    settings_row.trading_paused = False
    settings_row.pause_reason_code = None
    settings_row.pause_origin = None
    settings_row.pause_reason_detail = {
        "operating_state": "DEGRADED_MANAGE_ONLY",
        "protection_recovery": {
            "status": "manage_only",
            "missing_symbols": [settings_row.default_symbol],
            "missing_items": {settings_row.default_symbol: ["take_profit"]},
        },
    }
    _mark_fresh_sync_state(db_session, settings_row, exchange_can_trade=False)
    _seed_market_snapshot(db_session, settings_row)
    db_session.add(
        RiskCheck(
            symbol=settings_row.default_symbol,
            decision="long",
            allowed=True,
            reason_codes=[],
            approved_risk_pct=0.01,
            approved_leverage=2.0,
            payload={"allowed": True, "decision": "long", "reason_codes": []},
        )
    )
    db_session.flush()

    overview = get_overview(db_session)
    dashboard = get_operator_dashboard(db_session)
    summary = overview.operational_status.control_status_summary

    assert summary.exchange_can_trade is False
    assert summary.exchange_connectivity_state == "blocked"
    assert summary.app_live_armed is True
    assert summary.approval_window_open is True
    assert summary.paused is False
    assert summary.degraded is True
    assert summary.risk_allowed is True
    assert summary.blocked_reasons_current_cycle == []
    assert summary.blocked_reason_codes == []
    assert summary.degraded_reason_codes == ["DEGRADED_MANAGE_ONLY"]
    assert summary.protection_reason_codes == ["DEGRADED_MANAGE_ONLY", "MISSING_PROTECTIVE_ORDERS"]
    assert dashboard.control.operational_status.control_status_summary == summary
    assert dashboard.control.exchange_connectivity_state == "blocked"
    assert dashboard.control.degraded_reason_codes == ["DEGRADED_MANAGE_ONLY"]


def test_control_status_summary_separates_user_stream_rest_and_unresolved_submission(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=10)
    _mark_fresh_sync_state(db_session, settings_row, exchange_can_trade=True)
    _seed_market_snapshot(db_session, settings_row)
    set_user_stream_detail(
        settings_row,
        status="degraded",
        stream_source="rest_polling_fallback",
        listen_key_rotate_status="pending",
        heartbeat_ok=False,
    )
    set_reconciliation_detail(
        settings_row,
        unresolved_submission_badge=True,
        unresolved_submission_count=1,
        unresolved_submission_symbols=[settings_row.default_symbol],
    )
    record_binance_rest_issue(
        settings_row,
        reason_code="BINANCE_REST_SERVER_ERROR",
        source="test",
        error="503",
        failure_type="server_error",
        server_error=True,
    )
    db_session.flush()

    overview = get_overview(db_session)
    dashboard = get_operator_dashboard(db_session)
    rest_summary = overview.reconciliation_summary["rest_connectivity"]

    assert overview.user_stream_summary["status"] == "degraded"
    assert overview.reconciliation_summary["unresolved_submission_badge"] is True
    assert rest_summary["reason_code"] == "BINANCE_REST_SERVER_ERROR"
    assert rest_summary["entry_block_reason_code"] == "BINANCE_REST_CIRCUIT_OPEN"
    assert "USER_STREAM_LISTEN_KEY_ROTATION_PENDING" in overview.operational_status.blocked_reasons
    assert "UNRESOLVED_SUBMISSION_GUARD_ACTIVE" in overview.operational_status.blocked_reasons
    assert "BINANCE_REST_CIRCUIT_OPEN" in overview.operational_status.blocked_reasons
    assert dashboard.control.reconciliation_summary["rest_connectivity"]["reason_code"] == "BINANCE_REST_SERVER_ERROR"
