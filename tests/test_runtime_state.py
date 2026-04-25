from __future__ import annotations

from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from trading_mvp.database import Base
from trading_mvp.services.runtime_state import (
    build_sync_freshness_summary,
    mark_sync_success,
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
