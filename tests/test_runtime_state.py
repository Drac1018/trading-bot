from __future__ import annotations

from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from trading_mvp.database import Base
from trading_mvp.services.orchestrator import TradingOrchestrator
from trading_mvp.services.runtime_state import (
    build_sync_freshness_summary,
    get_binance_rest_detail,
    mark_sync_success,
    record_binance_rest_issue,
    record_binance_rest_success,
    set_user_stream_detail,
)
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive


def test_runtime_detail_top_level_write_preserves_fresh_exchange_sync(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'runtime_state.db'}", future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)

    stale_at = utcnow_naive() - timedelta(hours=1)
    fresh_at = utcnow_naive()

    with SessionLocal() as session:
        settings_row = get_or_create_settings(session)
        settings_row.decision_cycle_interval_minutes = 1
        for scope in ("account", "positions", "open_orders", "protective_orders"):
            mark_sync_success(settings_row, scope=scope, synced_at=stale_at)
        session.add(settings_row)
        session.commit()

    stale_session = SessionLocal()
    fresh_session = SessionLocal()
    try:
        stale_writer = get_or_create_settings(stale_session)
        fresh_writer = get_or_create_settings(fresh_session)

        for scope in ("account", "positions", "open_orders", "protective_orders"):
            mark_sync_success(fresh_writer, scope=scope, synced_at=fresh_at)
        fresh_session.add(fresh_writer)
        fresh_session.commit()

        set_user_stream_detail(stale_writer, status="connected", last_connected_at=fresh_at)
        stale_session.add(stale_writer)
        stale_session.commit()
    finally:
        stale_session.close()
        fresh_session.close()

    with SessionLocal() as session:
        settings_row = get_or_create_settings(session)
        summary = build_sync_freshness_summary(settings_row, now=fresh_at)

    assert summary["account"]["last_sync_at"] == fresh_at.isoformat()
    assert summary["positions"]["last_sync_at"] == fresh_at.isoformat()
    assert summary["open_orders"]["last_sync_at"] == fresh_at.isoformat()
    assert summary["protective_orders"]["last_sync_at"] == fresh_at.isoformat()
    assert settings_row.pause_reason_detail["user_stream"]["status"] == "connected"


def test_orchestrator_refreshes_runtime_sync_state_before_risk(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'risk_refresh.db'}", future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)

    fresh_at = utcnow_naive()
    stale_at = fresh_at - timedelta(minutes=2)

    with SessionLocal() as session:
        settings_row = get_or_create_settings(session)
        settings_row.decision_cycle_interval_minutes = 1
        for scope in ("account", "positions", "open_orders", "protective_orders"):
            mark_sync_success(settings_row, scope=scope, synced_at=stale_at, stale_after_seconds=60)
        session.add(settings_row)
        session.commit()

    stale_session = SessionLocal()
    fresh_session = SessionLocal()
    try:
        orchestrator = TradingOrchestrator(stale_session)
        cached_summary = build_sync_freshness_summary(orchestrator.settings_row, now=fresh_at)
        assert cached_summary["protective_orders"]["stale"] is True

        fresh_writer = get_or_create_settings(fresh_session)
        for scope in ("account", "positions", "open_orders", "protective_orders"):
            mark_sync_success(fresh_writer, scope=scope, synced_at=fresh_at, stale_after_seconds=60)
        fresh_session.add(fresh_writer)
        fresh_session.commit()

        cached_summary = build_sync_freshness_summary(orchestrator.settings_row, now=fresh_at)
        assert cached_summary["protective_orders"]["last_sync_at"] == stale_at.isoformat()

        orchestrator._refresh_runtime_state_before_risk()
        refreshed_summary = build_sync_freshness_summary(orchestrator.settings_row, now=fresh_at)

        assert refreshed_summary["protective_orders"]["last_sync_at"] == fresh_at.isoformat()
        assert refreshed_summary["protective_orders"]["stale"] is False
    finally:
        stale_session.close()
        fresh_session.close()


def test_binance_rest_success_can_clear_resolved_timestamp_failures(db_session) -> None:
    settings_row = get_or_create_settings(db_session)

    record_binance_rest_issue(
        settings_row,
        reason_code="BINANCE_REST_TIME_SYNC_REQUIRED",
        source="binance_client:account",
        failure_type="time_sync",
        http_status=400,
        api_code=-1021,
        error="HTTPStatusError",
    )
    assert get_binance_rest_detail(settings_row)["recent_failures"]

    record_binance_rest_success(
        settings_row,
        source="binance_client:account:time_sync",
        detail={"resolved_api_codes": [-1021]},
    )

    summary = get_binance_rest_detail(settings_row)
    assert summary["status"] == "ok"
    assert summary["consecutive_failures"] == 0
    assert summary["recent_failures"] == []


def test_binance_rest_auth_rejection_blocks_entries_with_specific_reason(db_session) -> None:
    settings_row = get_or_create_settings(db_session)

    record_binance_rest_issue(
        settings_row,
        reason_code="BINANCE_REST_AUTH_PERMISSION_REJECTED",
        source="binance_client:order_management",
        failure_type="auth_permission",
        http_status=401,
        api_code=-2015,
        error="Binance error -2015",
    )

    summary = get_binance_rest_detail(settings_row)
    assert summary["status"] == "unavailable"
    assert summary["circuit_state"] == "open"
    assert summary["new_entries_blocked"] is True
    assert summary["entry_block_reason_code"] == "BINANCE_REST_AUTH_PERMISSION_REJECTED"
