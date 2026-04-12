from __future__ import annotations

from datetime import date, timedelta

import pytest
from pydantic import ValidationError
from trading_mvp.models import PnLSnapshot, Position
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
            equity=94000.0,
            cash_balance=94000.0,
            realized_pnl=-6000.0,
            unrealized_pnl=0.0,
            daily_pnl=-6000.0,
            cumulative_pnl=-6000.0,
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


def test_reduce_is_allowed_while_trading_is_paused(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.trading_paused = True
    settings_row.live_trading_enabled = False
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    db_session.add(settings_row)
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = TradeDecision(
        decision="reduce",
        confidence=0.7,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=65000.0,
        entry_zone_max=65100.0,
        stop_loss=64000.0,
        take_profit=66500.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="paused reduce",
        explanation_detailed="paused mode should still allow reduce-only management for an existing position.",
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is True
    assert "TRADING_PAUSED" not in result.reason_codes
    assert "LIVE_TRADING_DISABLED" not in result.reason_codes


def test_btc_uses_five_x_hard_cap(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.max_leverage = 5.0
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = TradeDecision(
        decision="long",
        confidence=0.7,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=65000.0,
        entry_zone_max=65100.0,
        stop_loss=64000.0,
        take_profit=66500.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=5.0,
        rationale_codes=["TEST"],
        explanation_short="btc cap",
        explanation_detailed="btc should use the 5x hard cap without adding a leverage error.",
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.effective_leverage_cap == 5.0
    assert result.symbol_risk_tier == "btc"
    assert "LEVERAGE_EXCEEDS_LIMIT" not in result.reason_codes


def test_major_alt_blocks_leverage_above_three_x(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.max_leverage = 5.0
    snapshot = build_market_snapshot("ETHUSDT", "15m", upto_index=140)
    decision = TradeDecision(
        decision="long",
        confidence=0.7,
        symbol="ETHUSDT",
        timeframe="15m",
        entry_zone_min=3200.0,
        entry_zone_max=3210.0,
        stop_loss=3100.0,
        take_profit=3340.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=4.0,
        rationale_codes=["TEST"],
        explanation_short="major alt cap",
        explanation_detailed="major alts should be blocked above the 3x hard leverage cap.",
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.effective_leverage_cap == 3.0
    assert result.symbol_risk_tier == "major_alt"
    assert "LEVERAGE_EXCEEDS_LIMIT" in result.reason_codes


def test_general_alt_blocks_leverage_above_two_x(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.max_leverage = 5.0
    snapshot = build_market_snapshot("APTUSDT", "15m", upto_index=140)
    decision = TradeDecision(
        decision="long",
        confidence=0.7,
        symbol="APTUSDT",
        timeframe="15m",
        entry_zone_min=10.0,
        entry_zone_max=10.1,
        stop_loss=9.5,
        take_profit=10.8,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=3.0,
        rationale_codes=["TEST"],
        explanation_short="alt cap",
        explanation_detailed="general alts should be blocked above the 2x hard leverage cap.",
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.effective_leverage_cap == 2.0
    assert result.symbol_risk_tier == "alt"
    assert "LEVERAGE_EXCEEDS_LIMIT" in result.reason_codes


def test_risk_result_includes_exposure_metrics(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    db_session.add_all(
        [
            Position(
                symbol="BTCUSDT",
                mode="live",
                side="long",
                status="open",
                quantity=0.01,
                entry_price=65000.0,
                mark_price=66000.0,
                leverage=2.0,
                stop_loss=64000.0,
                take_profit=68000.0,
            ),
            Position(
                symbol="ETHUSDT",
                mode="live",
                side="short",
                status="open",
                quantity=0.5,
                entry_price=3200.0,
                mark_price=3150.0,
                leverage=2.0,
                stop_loss=3300.0,
                take_profit=3000.0,
            ),
        ]
    )
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = TradeDecision(
        decision="hold",
        confidence=0.55,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=65000.0,
        entry_zone_max=65100.0,
        stop_loss=64000.0,
        take_profit=66500.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="metrics",
        explanation_detailed="exposure metrics should be included in the risk payload for live monitoring.",
    )

    result, row = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.exposure_metrics["open_position_count"] == 2.0
    assert result.exposure_metrics["gross_exposure_pct_equity"] > 0
    assert row.payload["exposure_metrics"]["same_tier_concentration_pct"] >= 0.0


def test_unprotected_state_blocks_new_entry_but_allows_reduce(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    settings_row.pause_reason_detail = {
        "operating_state": "PROTECTION_REQUIRED",
        "protection_recovery": {
            "status": "recreating",
            "auto_recovery_active": True,
            "symbol_states": {
                "BTCUSDT": {
                    "state": "PROTECTION_REQUIRED",
                    "missing_components": ["take_profit"],
                    "failure_count": 1,
                }
            },
            "missing_symbols": ["BTCUSDT"],
            "missing_items": {"BTCUSDT": ["take_profit"]},
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
            mark_price=70100.0,
            leverage=2.0,
            stop_loss=69000.0,
            take_profit=72000.0,
        )
    )
    db_session.flush()

    entry_snapshot = build_market_snapshot("ETHUSDT", "15m", upto_index=140)
    entry_decision = TradeDecision(
        decision="long",
        confidence=0.7,
        symbol="ETHUSDT",
        timeframe="15m",
        entry_zone_min=3200.0,
        entry_zone_max=3210.0,
        stop_loss=3100.0,
        take_profit=3340.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="blocked entry",
        explanation_detailed="entry should be blocked while protection recovery is required.",
    )
    reduce_snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    reduce_decision = TradeDecision(
        decision="reduce",
        confidence=0.7,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=65000.0,
        entry_zone_max=65100.0,
        stop_loss=69000.0,
        take_profit=72000.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="reduce allowed",
        explanation_detailed="reduce-only management should remain allowed while entry is blocked.",
    )

    entry_result, _ = evaluate_risk(db_session, settings_row, entry_decision, entry_snapshot)
    reduce_result, _ = evaluate_risk(db_session, settings_row, reduce_decision, reduce_snapshot)

    assert entry_result.allowed is False
    assert "PROTECTION_REQUIRED" in entry_result.reason_codes
    assert reduce_result.allowed is True


def test_invalid_protection_recovery_output_is_blocked(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
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
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = TradeDecision(
        decision="long",
        confidence=0.65,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=69900.0,
        entry_zone_max=70050.0,
        stop_loss=70500.0,
        take_profit=69500.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="bad protection",
        explanation_detailed="protection recovery with invalid brackets should still be blocked by risk guard.",
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is False
    assert "INVALID_PROTECTION_BRACKETS" in result.reason_codes
