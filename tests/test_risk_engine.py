from __future__ import annotations

from datetime import date, timedelta

import pytest
from pydantic import ValidationError
from trading_mvp.models import PnLSnapshot
from trading_mvp.schemas import TradeDecision
from trading_mvp.services.market_data import build_market_snapshot
from trading_mvp.services.risk import evaluate_risk, validate_decision_schema
from trading_mvp.services.secret_store import encrypt_secret
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive


def test_risk_blocks_invalid_long_brackets(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = TradeDecision(
        decision="long",
        confidence=0.6,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=65000.0,
        entry_zone_max=65100.0,
        stop_loss=65200.0,
        take_profit=64900.0,
        max_holding_minutes=120,
        risk_pct=0.005,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="invalid",
        explanation_detailed="invalid brackets",
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)
    assert result.allowed is False
    assert "INVALID_LONG_BRACKETS" in result.reason_codes


def test_risk_blocks_stale_market_data(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140, force_stale=True)
    decision = TradeDecision(
        decision="long",
        confidence=0.6,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=65000.0,
        entry_zone_max=65100.0,
        stop_loss=64000.0,
        take_profit=66500.0,
        max_holding_minutes=120,
        risk_pct=0.005,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="stale",
        explanation_detailed="stale market should block",
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)
    assert result.allowed is False
    assert "STALE_MARKET_DATA" in result.reason_codes


def test_risk_blocks_daily_loss_limit(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    db_session.add(
        PnLSnapshot(
            snapshot_date=date.today(),
            equity=97000.0,
            cash_balance=97000.0,
            realized_pnl=-3000.0,
            unrealized_pnl=0.0,
            daily_pnl=-3000.0,
            cumulative_pnl=-3000.0,
            consecutive_losses=1,
        )
    )
    db_session.flush()
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = TradeDecision(
        decision="long",
        confidence=0.6,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=65000.0,
        entry_zone_max=65100.0,
        stop_loss=64000.0,
        take_profit=66500.0,
        max_holding_minutes=120,
        risk_pct=0.005,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="loss limit",
        explanation_detailed="daily loss limit test",
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)
    assert result.allowed is False
    assert "DAILY_LOSS_LIMIT_REACHED" in result.reason_codes


def test_risk_blocks_consecutive_losses(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    db_session.add(
        PnLSnapshot(
            snapshot_date=date.today(),
            equity=99000.0,
            cash_balance=99000.0,
            realized_pnl=-1000.0,
            unrealized_pnl=0.0,
            daily_pnl=-1000.0,
            cumulative_pnl=-1000.0,
            consecutive_losses=3,
        )
    )
    db_session.flush()
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = TradeDecision(
        decision="short",
        confidence=0.6,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=65000.0,
        entry_zone_max=65100.0,
        stop_loss=66200.0,
        take_profit=64000.0,
        max_holding_minutes=120,
        risk_pct=0.005,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="consecutive losses",
        explanation_detailed="consecutive loss gate test",
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)
    assert result.allowed is False
    assert "MAX_CONSECUTIVE_LOSSES_REACHED" in result.reason_codes


def test_schema_validation_rejects_malformed_payload() -> None:
    with pytest.raises(ValidationError):
        validate_decision_schema(
            {
                "decision": "long",
                "confidence": "0.9",
                "symbol": "BTCUSDT"
            }
        )


def test_live_risk_blocks_when_env_gate_is_disabled(db_session) -> None:
    class DisabledLiveEnv:
        live_trading_env_enabled = False

    from trading_mvp.services import risk as risk_service

    original_get_settings = risk_service.get_settings
    risk_service.get_settings = lambda: DisabledLiveEnv()  # type: ignore[assignment]
    settings_row = get_or_create_settings(db_session)
    try:
        settings_row.live_trading_enabled = True
        settings_row.manual_live_approval = True
        settings_row.live_execution_armed = True
        settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
        settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
        settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
        db_session.add(settings_row)
        db_session.flush()

        snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
        decision = TradeDecision(
            decision="long",
            confidence=0.6,
            symbol="BTCUSDT",
            timeframe="15m",
            entry_zone_min=65000.0,
            entry_zone_max=65100.0,
            stop_loss=64000.0,
            take_profit=66500.0,
            max_holding_minutes=120,
            risk_pct=0.005,
            leverage=2.0,
            rationale_codes=["TEST"],
            explanation_short="live gate",
            explanation_detailed="live env gate should still block",
        )

        result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)
        assert result.allowed is False
        assert "LIVE_ENV_DISABLED" in result.reason_codes
    finally:
        risk_service.get_settings = original_get_settings  # type: ignore[assignment]
