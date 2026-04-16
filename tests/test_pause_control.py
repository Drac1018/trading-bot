from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import select

from trading_mvp.models import AuditEvent
from trading_mvp.schemas import AppSettingsUpdateRequest
from trading_mvp.services.pause_control import attempt_auto_resume
from trading_mvp.services.settings import (
    arm_live_execution,
    get_or_create_settings,
    serialize_settings,
    set_trading_pause,
    update_settings,
)
from trading_mvp.time_utils import utcnow_naive


def _build_live_settings_payload() -> AppSettingsUpdateRequest:
    return AppSettingsUpdateRequest(
        live_trading_enabled=True,
        manual_live_approval=True,
        live_approval_window_minutes=15,
        default_symbol="BTCUSDT",
        tracked_symbols=["BTCUSDT"],
        default_timeframe="15m",
        schedule_windows=["1h", "4h", "12h", "24h"],
        max_leverage=3.0,
        max_risk_per_trade=0.01,
        max_daily_loss=0.02,
        max_consecutive_losses=3,
        stale_market_seconds=1800,
        slippage_threshold_pct=0.003,
        starting_equity=100000.0,
        ai_enabled=True,
        ai_provider="openai",
        ai_model="gpt-4.1-mini",
        ai_call_interval_minutes=30,
        decision_cycle_interval_minutes=15,
        ai_max_input_candles=16,
        ai_temperature=0.1,
        binance_market_data_enabled=True,
        binance_testnet_enabled=True,
        binance_futures_enabled=True,
        openai_api_key="sk-test-openai",
        binance_api_key="binance-key",
        binance_api_secret="binance-secret",
        clear_openai_api_key=False,
        clear_binance_api_key=False,
        clear_binance_api_secret=False,
    )


def test_pause_reason_metadata_and_auto_resume_whitelist(db_session, monkeypatch) -> None:
    update_settings(db_session, _build_live_settings_payload())
    arm_live_execution(db_session, 15)

    paused = set_trading_pause(
        db_session,
        True,
        reason_code="MANUAL_USER_REQUEST",
        reason_detail={"source": "test"},
        pause_origin="manual",
    )
    serialized = serialize_settings(paused)
    assert serialized["trading_paused"] is True
    assert serialized["pause_reason_code"] == "MANUAL_USER_REQUEST"
    assert serialized["pause_origin"] == "manual"
    assert serialized["auto_resume_whitelisted"] is False
    assert attempt_auto_resume(db_session, paused)["status"] == "not_eligible"
    arm_live_execution(db_session, 15)

    whitelisted = set_trading_pause(
        db_session,
        True,
        reason_code="EXCHANGE_ACCOUNT_STATE_UNAVAILABLE",
        reason_detail={"source": "exchange"},
        pause_origin="system",
        auto_resume_after=utcnow_naive() - timedelta(minutes=1),
        preserve_live_arm=True,
    )

    class FakeClient:
        def get_account_info(self) -> dict[str, object]:
            return {"availableBalance": "100.0"}

        def fetch_klines(self, symbol: str, interval: str, limit: int = 2):
            now = utcnow_naive()
            return [
                SimpleNamespace(timestamp=now - timedelta(minutes=15)),
                SimpleNamespace(timestamp=now),
            ]

        def get_open_orders(self, symbol: str) -> list[dict[str, object]]:
            return []

        def get_position_information(self, symbol: str) -> list[dict[str, object]]:
            return []

    monkeypatch.setattr(
        "trading_mvp.services.pause_control.get_settings",
        lambda: SimpleNamespace(
            live_trading_env_enabled=True,
            exchange_recv_window_ms=5000,
            app_secret_seed="change-me-local-dev-secret",
        ),
    )
    monkeypatch.setattr("trading_mvp.services.pause_control._build_client", lambda settings: FakeClient())

    result = attempt_auto_resume(db_session, whitelisted)

    assert result["status"] == "resumed"
    assert result["resumed"] is True

    refreshed = get_or_create_settings(db_session)
    refreshed_serialized = serialize_settings(refreshed)
    assert refreshed.trading_paused is False
    assert refreshed.pause_reason_code is None
    assert refreshed.auto_resume_after is None
    assert refreshed_serialized["sync_freshness_summary"]["account"]["last_sync_at"] is not None
    assert refreshed_serialized["sync_freshness_summary"]["open_orders"]["last_sync_at"] is not None
    assert refreshed_serialized["sync_freshness_summary"]["positions"]["last_sync_at"] is not None
    assert refreshed_serialized["sync_freshness_summary"]["protective_orders"]["last_sync_at"] is not None
    assert refreshed_serialized["sync_freshness_summary"]["protective_orders"]["status"] in {"synced", "stale"}

    audit_events = list(
        db_session.scalars(
            select(AuditEvent).where(AuditEvent.event_type == "trading_auto_resumed").order_by(AuditEvent.id.desc())
        )
    )
    assert audit_events
    assert audit_events[0].payload["reason_code"] == "EXCHANGE_ACCOUNT_STATE_UNAVAILABLE"


def test_non_whitelisted_system_pause_stays_paused(db_session) -> None:
    settings_row = update_settings(db_session, _build_live_settings_payload())
    paused = set_trading_pause(
        db_session,
        True,
        reason_code="PROTECTIVE_ORDER_FAILURE",
        reason_detail={"symbol": "BTCUSDT"},
        pause_origin="system",
    )

    serialized = serialize_settings(paused)
    assert serialized["pause_reason_code"] == "PROTECTIVE_ORDER_FAILURE"
    assert serialized["pause_origin"] == "system"
    assert serialized["auto_resume_whitelisted"] is False

    result = attempt_auto_resume(db_session, paused)
    assert result["status"] == "not_eligible"
    assert settings_row.id == paused.id
