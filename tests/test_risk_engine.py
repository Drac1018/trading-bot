from __future__ import annotations

from datetime import date, timedelta

import pytest
from pydantic import ValidationError
from trading_mvp.models import PnLSnapshot, Position
from trading_mvp.schemas import TradeDecision
from trading_mvp.services.market_data import build_market_snapshot
from trading_mvp.services.risk import evaluate_risk, validate_decision_schema
from trading_mvp.services.runtime_state import mark_sync_issue, mark_sync_success
from trading_mvp.services.secret_store import encrypt_secret
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive


def _mark_all_sync_scopes_fresh(settings_row) -> None:
    now = utcnow_naive()
    for scope in ("account", "positions", "open_orders", "protective_orders"):
        mark_sync_success(settings_row, scope=scope, synced_at=now)


def _entry_decision(
    *,
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    decision: str = "long",
    entry_zone_min: float = 65000.0,
    entry_zone_max: float = 65100.0,
    stop_loss: float = 64000.0,
    take_profit: float = 66500.0,
    entry_mode: str = "immediate",
    invalidation_price: float | None = None,
    max_chase_bps: float | None = 25.0,
) -> TradeDecision:
    return TradeDecision(
        decision=decision,  # type: ignore[arg-type]
        confidence=0.7,
        symbol=symbol,
        timeframe=timeframe,
        entry_zone_min=entry_zone_min,
        entry_zone_max=entry_zone_max,
        entry_mode=entry_mode,  # type: ignore[arg-type]
        invalidation_price=stop_loss if invalidation_price is None else invalidation_price,
        max_chase_bps=max_chase_bps,
        idea_ttl_minutes=15,
        stop_loss=stop_loss,
        take_profit=take_profit,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="entry trigger test",
        explanation_detailed="Deterministic entry trigger regression test.",
    )


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


def test_schema_validation_accepts_optional_entry_trigger_fields() -> None:
    decision = validate_decision_schema(
        {
            "decision": "long",
            "confidence": 0.9,
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "entry_zone_min": 69950.0,
            "entry_zone_max": 70050.0,
            "entry_mode": "breakout_confirm",
            "invalidation_price": 69000.0,
            "max_chase_bps": 12.0,
            "idea_ttl_minutes": 15,
            "stop_loss": 69000.0,
            "take_profit": 72000.0,
            "max_holding_minutes": 120,
            "risk_pct": 0.01,
            "leverage": 2.0,
            "rationale_codes": ["TEST"],
            "explanation_short": "schema ok",
            "explanation_detailed": "Optional trigger fields should remain backward-compatible for schema consumers.",
        }
    )

    assert decision.entry_mode == "breakout_confirm"
    assert decision.max_chase_bps == 12.0
    assert decision.idea_ttl_minutes == 15


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


def test_risk_blocks_gross_exposure_limit_for_new_entry(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.max_gross_exposure_pct = 0.5
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.8,
            entry_price=65000.0,
            mark_price=65000.0,
            leverage=2.0,
            stop_loss=63000.0,
            take_profit=68000.0,
        )
    )
    db_session.flush()

    snapshot = build_market_snapshot("ETHUSDT", "15m", upto_index=140)
    decision = TradeDecision(
        decision="long",
        confidence=0.7,
        symbol="ETHUSDT",
        timeframe="15m",
        entry_zone_min=3200.0,
        entry_zone_max=3210.0,
        stop_loss=3150.0,
        take_profit=3330.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="gross exposure gate",
        explanation_detailed="new entry should be blocked when projected gross exposure exceeds the deterministic cap.",
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is False
    assert "GROSS_EXPOSURE_LIMIT_REACHED" in result.reason_codes


def test_risk_blocks_directional_bias_limit_for_new_entry(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.max_directional_bias_pct = 0.7
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=1.0,
            entry_price=65000.0,
            mark_price=65000.0,
            leverage=2.0,
            stop_loss=63000.0,
            take_profit=68000.0,
        )
    )
    db_session.flush()

    snapshot = build_market_snapshot("ETHUSDT", "15m", upto_index=140)
    decision = TradeDecision(
        decision="long",
        confidence=0.7,
        symbol="ETHUSDT",
        timeframe="15m",
        entry_zone_min=3200.0,
        entry_zone_max=3210.0,
        stop_loss=3150.0,
        take_profit=3330.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="directional bias gate",
        explanation_detailed="new entry should be blocked when one-sided directional exposure exceeds the configured cap.",
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is False
    assert "DIRECTIONAL_BIAS_LIMIT_REACHED" in result.reason_codes


def test_risk_blocks_same_tier_concentration_limit_for_new_entry(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.max_same_tier_concentration_pct = 0.2
    db_session.add(
        Position(
            symbol="ETHUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=4.0,
            entry_price=3200.0,
            mark_price=3200.0,
            leverage=2.0,
            stop_loss=3100.0,
            take_profit=3400.0,
        )
    )
    db_session.flush()

    snapshot = build_market_snapshot("SOLUSDT", "15m", upto_index=140)
    decision = TradeDecision(
        decision="long",
        confidence=0.7,
        symbol="SOLUSDT",
        timeframe="15m",
        entry_zone_min=150.0,
        entry_zone_max=151.0,
        stop_loss=145.0,
        take_profit=160.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="tier concentration gate",
        explanation_detailed="new entry should be blocked when exposure inside the same risk tier exceeds the configured cap.",
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is False
    assert "SAME_TIER_CONCENTRATION_LIMIT_REACHED" in result.reason_codes


def test_entry_is_auto_resized_when_raw_size_slightly_exceeds_single_position_limit(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.max_largest_position_pct = 1.5
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    decision = _entry_decision(
        symbol="BTCUSDT",
        entry_zone_min=entry_price - 25.0,
        entry_zone_max=entry_price + 25.0,
        stop_loss=entry_price - 10.0,
        take_profit=entry_price + 250.0,
        max_chase_bps=20.0,
    ).model_copy(
        update={
            "leverage": 1.58,
            "risk_pct": 0.01,
        }
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
    )

    assert result.allowed is True
    assert result.auto_resized_entry is True
    assert "ENTRY_AUTO_RESIZED" in result.reason_codes
    assert "ENTRY_CLAMPED_TO_SINGLE_POSITION_LIMIT" in result.reason_codes
    assert result.raw_projected_notional > result.approved_projected_notional
    assert result.approved_projected_notional <= 150000.0
    assert result.approved_quantity is not None and result.approved_quantity > 0


def test_entry_is_clamped_to_directional_headroom_when_that_is_smallest(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.max_gross_exposure_pct = 1.5
    settings_row.max_directional_bias_pct = 0.6
    settings_row.max_largest_position_pct = 1.5
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.615385,
            entry_price=65000.0,
            mark_price=65000.0,
            leverage=2.0,
            stop_loss=63000.0,
            take_profit=68000.0,
        )
    )
    db_session.flush()

    snapshot = build_market_snapshot("ETHUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        symbol="ETHUSDT",
        entry_zone_min=snapshot.latest_price - 5.0,
        entry_zone_max=snapshot.latest_price + 5.0,
        stop_loss=snapshot.latest_price - 1.0,
        take_profit=snapshot.latest_price + 80.0,
        max_chase_bps=20.0,
    ).model_copy(
        update={
            "leverage": 1.0,
            "risk_pct": 0.01,
        }
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
    )

    assert result.allowed is True
    assert result.auto_resized_entry is True
    assert result.auto_resize_reason == "CLAMPED_TO_DIRECTIONAL_HEADROOM"
    assert result.approved_projected_notional == pytest.approx(20000.0, abs=5.0)
    assert result.exposure_headroom_snapshot["directional_headroom_notional"] == pytest.approx(20000.0, abs=5.0)


def test_entry_stays_blocked_when_remaining_headroom_is_below_minimum_order_size(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.max_directional_bias_pct = 0.6
    db_session.add(
        Position(
            symbol="ETHUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=18.746875,
            entry_price=3200.0,
            mark_price=3200.0,
            leverage=2.0,
            stop_loss=3100.0,
            take_profit=3400.0,
        )
    )
    db_session.flush()

    snapshot = build_market_snapshot("ETHUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        symbol="ETHUSDT",
        entry_zone_min=snapshot.latest_price - 5.0,
        entry_zone_max=snapshot.latest_price + 5.0,
        stop_loss=snapshot.latest_price - 1.0,
        take_profit=snapshot.latest_price + 80.0,
        max_chase_bps=20.0,
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
    )

    assert result.allowed is False
    assert result.auto_resized_entry is False
    assert "ENTRY_SIZE_BELOW_MIN_NOTIONAL" in result.reason_codes


def test_hard_blockers_keep_entry_blocked_even_when_exposure_could_be_resized(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = False
    settings_row.live_execution_armed = False
    settings_row.pause_reason_detail = {"operating_state": "PROTECTION_REQUIRED"}
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    _mark_all_sync_scopes_fresh(settings_row)
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140, force_stale=True)
    decision = _entry_decision(
        symbol="BTCUSDT",
        entry_zone_min=snapshot.latest_price - 25.0,
        entry_zone_max=snapshot.latest_price + 25.0,
        stop_loss=snapshot.latest_price - 10.0,
        take_profit=snapshot.latest_price + 250.0,
        max_chase_bps=20.0,
    ).model_copy(
        update={
            "leverage": 1.58,
            "risk_pct": 0.01,
        }
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is False
    assert result.auto_resized_entry is False
    assert "STALE_MARKET_DATA" in result.reason_codes
    assert "LIVE_APPROVAL_POLICY_DISABLED" in result.reason_codes
    assert "PROTECTION_REQUIRED" in result.reason_codes
    assert "ENTRY_AUTO_RESIZED" not in result.reason_codes


def test_reduce_and_exit_remain_allowed_under_exposure_limits(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    settings_row.max_gross_exposure_pct = 0.2
    settings_row.max_directional_bias_pct = 0.2
    settings_row.max_same_tier_concentration_pct = 0.2
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=1.0,
            entry_price=65000.0,
            mark_price=65000.0,
            leverage=2.0,
            stop_loss=63000.0,
            take_profit=68000.0,
        )
    )
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    reduce_decision = TradeDecision(
        decision="reduce",
        confidence=0.7,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=65000.0,
        entry_zone_max=65100.0,
        stop_loss=63000.0,
        take_profit=68000.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="reduce allowed",
        explanation_detailed="reduce-only management should stay allowed even when entry exposure caps are already exceeded.",
    )
    exit_decision = reduce_decision.model_copy(update={"decision": "exit", "explanation_short": "exit allowed"})

    reduce_result, _ = evaluate_risk(db_session, settings_row, reduce_decision, snapshot)
    exit_result, _ = evaluate_risk(db_session, settings_row, exit_decision, snapshot)

    assert reduce_result.allowed is True
    assert exit_result.allowed is True
    assert "GROSS_EXPOSURE_LIMIT_REACHED" not in reduce_result.reason_codes
    assert "DIRECTIONAL_BIAS_LIMIT_REACHED" not in exit_result.reason_codes


def test_live_entry_keeps_existing_path_when_sync_state_is_fresh(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    _mark_all_sync_scopes_fresh(settings_row)
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        entry_zone_min=snapshot.latest_price - 50.0,
        entry_zone_max=snapshot.latest_price + 50.0,
        stop_loss=snapshot.latest_price - 500.0,
        take_profit=snapshot.latest_price + 800.0,
        entry_mode="immediate",
        max_chase_bps=20.0,
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is True
    assert "ACCOUNT_STATE_STALE" not in result.reason_codes
    assert "POSITION_STATE_STALE" not in result.reason_codes
    assert "OPEN_ORDERS_STATE_STALE" not in result.reason_codes
    assert "PROTECTION_STATE_UNVERIFIED" not in result.reason_codes


def test_risk_blocks_entry_when_breakout_trigger_is_not_met(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    _mark_all_sync_scopes_fresh(settings_row)
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        entry_zone_min=snapshot.latest_price + 800.0,
        entry_zone_max=snapshot.latest_price + 1000.0,
        stop_loss=snapshot.latest_price - 400.0,
        take_profit=snapshot.latest_price + 1500.0,
        entry_mode="breakout_confirm",
        max_chase_bps=30.0,
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is False
    assert "ENTRY_TRIGGER_NOT_MET" in result.reason_codes


def test_risk_blocks_entry_when_chase_limit_is_exceeded(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    _mark_all_sync_scopes_fresh(settings_row)
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        entry_zone_min=snapshot.latest_price - 400.0,
        entry_zone_max=snapshot.latest_price - 300.0,
        stop_loss=snapshot.latest_price - 800.0,
        take_profit=snapshot.latest_price + 900.0,
        entry_mode="immediate",
        max_chase_bps=10.0,
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is False
    assert "CHASE_LIMIT_EXCEEDED" in result.reason_codes


def test_risk_blocks_entry_with_invalid_invalidation_price(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    _mark_all_sync_scopes_fresh(settings_row)
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        entry_zone_min=snapshot.latest_price - 50.0,
        entry_zone_max=snapshot.latest_price + 50.0,
        stop_loss=snapshot.latest_price - 500.0,
        take_profit=snapshot.latest_price + 900.0,
        entry_mode="immediate",
        invalidation_price=snapshot.latest_price + 25.0,
        max_chase_bps=25.0,
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is False
    assert "INVALID_INVALIDATION_PRICE" in result.reason_codes


def test_reduce_path_ignores_entry_trigger_requirements(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    mark_sync_issue(settings_row, scope="account", status="failed", reason_code="ACCOUNT_STATE_STALE")
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        decision="reduce",
        entry_mode="breakout_confirm",
        invalidation_price=snapshot.latest_price + 1000.0,
        max_chase_bps=1.0,
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is True
    assert "ENTRY_TRIGGER_NOT_MET" not in result.reason_codes
    assert "INVALID_INVALIDATION_PRICE" not in result.reason_codes


def test_live_entry_is_blocked_when_exchange_state_is_stale(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    stale_at = utcnow_naive() - timedelta(hours=2)
    mark_sync_success(settings_row, scope="account", synced_at=stale_at, stale_after_seconds=60)
    mark_sync_success(settings_row, scope="positions", synced_at=utcnow_naive())
    mark_sync_success(settings_row, scope="open_orders", synced_at=utcnow_naive())
    mark_sync_issue(
        settings_row,
        scope="protective_orders",
        status="incomplete",
        reason_code="PROTECTION_STATE_UNVERIFIED",
    )
    db_session.flush()

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
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="stale sync",
        explanation_detailed="Stale or incomplete exchange state should block new entries.",
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is False
    assert "ACCOUNT_STATE_STALE" in result.reason_codes
    assert "PROTECTION_STATE_UNVERIFIED" in result.reason_codes


def test_reduce_path_stays_open_when_exchange_state_is_stale(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    mark_sync_issue(settings_row, scope="account", status="failed", reason_code="ACCOUNT_STATE_STALE")
    mark_sync_issue(settings_row, scope="positions", status="failed", reason_code="POSITION_STATE_STALE")
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
        explanation_short="stale but reduce",
        explanation_detailed="Reduce-only path should remain available even when entry state freshness is degraded.",
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is True
    assert "ACCOUNT_STATE_STALE" not in result.reason_codes
    assert "POSITION_STATE_STALE" not in result.reason_codes


def test_time_stop_exit_path_stays_open_when_exchange_state_is_stale(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    mark_sync_issue(settings_row, scope="account", status="failed", reason_code="ACCOUNT_STATE_STALE")
    mark_sync_issue(settings_row, scope="positions", status="failed", reason_code="POSITION_STATE_STALE")
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = TradeDecision(
        decision="exit",
        confidence=0.8,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=65000.0,
        entry_zone_max=65100.0,
        stop_loss=64000.0,
        take_profit=66500.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["POSITION_MANAGEMENT_TIME_STOP_EXIT"],
        explanation_short="time stop exit",
        explanation_detailed="Time stop exit should remain available even when entry freshness checks are degraded.",
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is True
    assert "ACCOUNT_STATE_STALE" not in result.reason_codes
    assert "POSITION_STATE_STALE" not in result.reason_codes


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
