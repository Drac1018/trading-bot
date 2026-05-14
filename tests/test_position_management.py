from __future__ import annotations

from datetime import timedelta

import pytest
from trading_mvp.models import Position, Setting
from trading_mvp.schemas import FeaturePayload, RegimeFeatureContext
from trading_mvp.services.position_management import (
    build_position_management_context,
    seed_position_management_metadata,
    store_position_management_context,
)
from trading_mvp.time_utils import utcnow_naive


def _settings() -> Setting:
    return Setting(
        position_management_enabled=True,
        break_even_enabled=False,
        atr_trailing_stop_enabled=False,
        partial_take_profit_enabled=False,
        time_stop_enabled=False,
        holding_edge_decay_enabled=False,
        reduce_on_regime_shift_enabled=False,
    )


def _feature_payload(*, atr: float = 0.0, weak: bool = False) -> FeaturePayload:
    return FeaturePayload(
        symbol="BTCUSDT",
        timeframe="15m",
        trend_score=0.4,
        volatility_pct=0.01,
        volume_ratio=1.0,
        drawdown_pct=0.0,
        rsi=52.0,
        atr=atr,
        atr_pct=0.003,
        momentum_score=0.2,
        regime=RegimeFeatureContext(
            primary_regime="transition" if weak else "bullish",
            trend_alignment="mixed" if weak else "bullish_aligned",
            volatility_regime="normal",
            volume_regime="weak" if weak else "normal",
            momentum_state="weakening" if weak else "stable",
            weak_volume=weak,
            momentum_weakening=weak,
        ),
    )


def _position(
    *,
    side: str = "short",
    entry_price: float = 100.0,
    mark_price: float = 99.98,
    stop_loss: float = 101.0,
    take_profit: float = 99.65,
    opened_minutes_ago: int = 16,
    management: dict[str, object] | None = None,
) -> Position:
    return Position(
        symbol="BTCUSDT",
        mode="live",
        side=side,
        status="open",
        quantity=0.01,
        entry_price=entry_price,
        mark_price=mark_price,
        leverage=2.0,
        stop_loss=stop_loss,
        take_profit=take_profit,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        opened_at=utcnow_naive() - timedelta(minutes=opened_minutes_ago),
        metadata_json={"position_management": management or {}},
    )


def test_short_tp_scalp_position_early_fails_when_profit_progress_is_missing() -> None:
    position = _position(
        management={
            "holding_profile": "scalp",
            "strategy_tag": "scalp",
            "initial_stop_loss": 101.0,
            "initial_take_profit": 99.65,
            "initial_risk_per_unit": 1.0,
            "expected_gross_bps": 35.0,
            "expected_holding_minutes": 45,
            "expected_time_to_profit_minutes": 10,
            "mfe_r": 0.0,
        }
    )

    context = build_position_management_context(
        position,
        feature_payload=_feature_payload(),
        settings_row=_settings(),
    )
    metadata = store_position_management_context(position, context)

    assert context["short_tp_intent"]["applies"] is True
    assert context["early_fail_minutes"] == 15
    assert context["scalp_early_fail_ready"] is True
    assert context["time_to_profit_missed"] is True
    assert context["time_to_fail_ready"] is True
    assert context["time_to_fail_action"] == "reduce"
    assert "scalp_early_fail" in context["early_fail_reason_codes"]
    assert "time_to_profit_missed" in context["early_fail_reason_codes"]
    assert "short_hold_reassessment_required" in context["reassessment_reason_codes"]
    assert "POSITION_MANAGEMENT_SCALP_EARLY_FAIL" in context["applied_rule_candidates"]
    assert "POSITION_MANAGEMENT_SCALP_EARLY_FAIL_REDUCE" in context["reduce_reason_codes"]
    assert metadata["scalp_early_fail_ready"] is True
    assert "scalp_early_fail" in metadata["early_fail_reason_codes"]


def test_general_position_does_not_apply_scalp_early_fail() -> None:
    position = _position(
        side="long",
        entry_price=100.0,
        mark_price=99.9,
        stop_loss=95.0,
        take_profit=106.0,
        opened_minutes_ago=30,
        management={
            "holding_profile": "swing",
            "initial_stop_loss": 95.0,
            "initial_take_profit": 106.0,
            "initial_risk_per_unit": 5.0,
            "planned_max_holding_minutes": 240,
            "expected_holding_minutes": 240,
        },
    )

    context = build_position_management_context(
        position,
        feature_payload=_feature_payload(),
        settings_row=_settings(),
    )

    assert context["short_tp_intent"]["applies"] is False
    assert context["scalp_early_fail_ready"] is False
    assert context["time_to_fail_ready"] is False
    assert "POSITION_MANAGEMENT_SCALP_EARLY_FAIL_REDUCE" not in context["reduce_reason_codes"]


def test_scalp_early_fail_does_not_override_profitable_protective_tightening() -> None:
    settings_row = _settings()
    settings_row.break_even_enabled = True
    position = _position(
        side="long",
        entry_price=100.0,
        mark_price=101.2,
        stop_loss=99.0,
        take_profit=100.35,
        opened_minutes_ago=20,
        management={
            "holding_profile": "scalp",
            "strategy_tag": "scalp",
            "initial_stop_loss": 99.0,
            "initial_take_profit": 100.35,
            "initial_risk_per_unit": 1.0,
            "expected_gross_bps": 35.0,
            "expected_holding_minutes": 45,
            "mfe_r": 1.2,
        },
    )

    context = build_position_management_context(
        position,
        feature_payload=_feature_payload(),
        settings_row=settings_row,
    )

    assert context["short_tp_intent"]["applies"] is True
    assert context["scalp_early_fail_ready"] is False
    assert context["time_to_fail_ready"] is False
    assert context["break_even_eligible"] is True
    assert context["tightened_stop_loss"] == pytest.approx(100.0)


def test_seed_position_management_metadata_marks_tight_tp_scalp_intent_inputs() -> None:
    position = _position(
        side="long",
        entry_price=100.0,
        mark_price=100.0,
        stop_loss=99.0,
        take_profit=100.35,
        opened_minutes_ago=1,
        management={},
    )

    metadata = seed_position_management_metadata(
        position,
        max_holding_minutes=45,
        timeframe="15m",
        stop_loss=99.0,
        take_profit=100.35,
        holding_profile="scalp",
    )

    assert metadata["holding_profile"] == "scalp"
    assert metadata["strategy_tag"] == "scalp"
    assert metadata["expected_gross_bps"] == pytest.approx(35.0)
    assert metadata["expected_holding_minutes"] == 45
