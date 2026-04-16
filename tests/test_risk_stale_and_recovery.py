from __future__ import annotations

from datetime import timedelta

import pytest

from trading_mvp.models import Position
from trading_mvp.schemas import TradeDecision
from trading_mvp.services.market_data import build_market_snapshot
from trading_mvp.services.risk import evaluate_risk
from trading_mvp.services.runtime_state import mark_sync_issue, mark_sync_success
from trading_mvp.services.secret_store import encrypt_secret
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive

FRESHNESS_REASON_CODES = {
    "ACCOUNT_STATE_STALE",
    "POSITION_STATE_STALE",
    "OPEN_ORDERS_STATE_STALE",
    "PROTECTION_STATE_UNVERIFIED",
    "MARKET_STATE_STALE",
    "MARKET_STATE_INCOMPLETE",
}


def _prime_live_ready(settings_row) -> None:
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")


def _entry_decision(*, decision: str = "long", symbol: str = "BTCUSDT") -> TradeDecision:
    return TradeDecision(
        decision=decision,  # type: ignore[arg-type]
        confidence=0.7,
        symbol=symbol,
        timeframe="15m",
        entry_zone_min=65000.0,
        entry_zone_max=65100.0,
        entry_mode="immediate",
        invalidation_price=64000.0,
        max_chase_bps=25.0,
        idea_ttl_minutes=15,
        stop_loss=64000.0,
        take_profit=66500.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="freshness regression",
        explanation_detailed="Freshness gating should only block new entries.",
    )


def _mark_mixed_stale_and_incomplete_state(settings_row) -> None:
    stale_at = utcnow_naive() - timedelta(hours=2)
    mark_sync_success(settings_row, scope="account", synced_at=stale_at, stale_after_seconds=60)
    mark_sync_issue(settings_row, scope="positions", status="incomplete", reason_code="POSITION_STATE_STALE")
    mark_sync_success(settings_row, scope="open_orders", synced_at=stale_at, stale_after_seconds=60)
    mark_sync_issue(
        settings_row,
        scope="protective_orders",
        status="incomplete",
        reason_code="PROTECTION_STATE_UNVERIFIED",
    )


def _incomplete_snapshot():
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    return snapshot.model_copy(update={"is_complete": False})


def _stale_and_incomplete_snapshot():
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140, force_stale=True)
    return snapshot.model_copy(update={"is_complete": False})


def test_entry_returns_fixed_reason_codes_for_stale_and_incomplete_state(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _prime_live_ready(settings_row)
    _mark_mixed_stale_and_incomplete_state(settings_row)
    db_session.flush()

    result, _ = evaluate_risk(db_session, settings_row, _entry_decision(), _incomplete_snapshot())

    assert result.allowed is False
    assert "ACCOUNT_STATE_STALE" in result.reason_codes
    assert "POSITION_STATE_STALE" in result.reason_codes
    assert "OPEN_ORDERS_STATE_STALE" in result.reason_codes
    assert "PROTECTION_STATE_UNVERIFIED" in result.reason_codes
    assert "MARKET_STATE_INCOMPLETE" in result.reason_codes


def test_entry_blocks_stale_market_with_fixed_reason_code(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _prime_live_ready(settings_row)
    now = utcnow_naive()
    for scope in ("account", "positions", "open_orders", "protective_orders"):
        mark_sync_success(settings_row, scope=scope, synced_at=now)
    db_session.flush()

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        _entry_decision(),
        build_market_snapshot("BTCUSDT", "15m", upto_index=140, force_stale=True),
    )

    assert result.allowed is False
    assert "MARKET_STATE_STALE" in result.reason_codes


@pytest.mark.parametrize("decision_name", ["reduce", "exit"])
def test_survival_paths_ignore_freshness_blockers(db_session, decision_name: str) -> None:
    settings_row = get_or_create_settings(db_session)
    _prime_live_ready(settings_row)
    _mark_mixed_stale_and_incomplete_state(settings_row)
    db_session.flush()

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        _entry_decision(decision=decision_name),
        _stale_and_incomplete_snapshot(),
    )

    assert result.allowed is True
    assert FRESHNESS_REASON_CODES.isdisjoint(result.reason_codes)


def test_protective_recovery_ignores_freshness_blockers(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _prime_live_ready(settings_row)
    settings_row.pause_reason_detail = {
        "operating_state": "PROTECTION_REQUIRED",
        "protection_recovery": {
            "status": "recreating",
            "auto_recovery_active": True,
            "symbol_states": {
                "BTCUSDT": {
                    "state": "PROTECTION_REQUIRED",
                    "missing_components": ["stop_loss", "take_profit"],
                    "failure_count": 1,
                }
            },
        },
    }
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.01,
            entry_price=70000.0,
            mark_price=69950.0,
            leverage=2.0,
            stop_loss=69000.0,
            take_profit=72000.0,
        )
    )
    _mark_mixed_stale_and_incomplete_state(settings_row)
    db_session.flush()

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        _entry_decision(symbol="BTCUSDT").model_copy(
            update={
                "entry_zone_min": 69900.0,
                "entry_zone_max": 70050.0,
                "stop_loss": 69000.0,
                "take_profit": 72000.0,
            }
        ),
        _stale_and_incomplete_snapshot(),
    )

    assert result.allowed is True
    assert FRESHNESS_REASON_CODES.isdisjoint(result.reason_codes)

