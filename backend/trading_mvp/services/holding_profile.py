from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from trading_mvp.schemas import FeaturePayload

HOLDING_PROFILE_SCALP = "scalp"
HOLDING_PROFILE_SWING = "swing"
HOLDING_PROFILE_POSITION = "position"
HOLDING_PROFILES = {
    HOLDING_PROFILE_SCALP,
    HOLDING_PROFILE_SWING,
    HOLDING_PROFILE_POSITION,
}
DETERMINISTIC_INITIAL_STOP_TYPE = "deterministic_hard_stop"

HOLDING_PROFILE_RISK_POLICIES: dict[str, dict[str, Any]] = {
    HOLDING_PROFILE_SCALP: {
        "risk_pct_multiplier": 1.0,
        "leverage_multiplier": 1.0,
        "notional_multiplier": 1.0,
        "require_meta_gate_pass": False,
        "capital_bucket": "intraday_core",
        "max_turnover_bias": "normal",
        "breakout_exception_allowed": True,
    },
    HOLDING_PROFILE_SWING: {
        "risk_pct_multiplier": 0.82,
        "leverage_multiplier": 0.72,
        "notional_multiplier": 0.8,
        "require_meta_gate_pass": True,
        "capital_bucket": "trend_swing_bucket",
        "max_turnover_bias": "reduced",
        "breakout_exception_allowed": False,
    },
    HOLDING_PROFILE_POSITION: {
        "risk_pct_multiplier": 0.6,
        "leverage_multiplier": 0.45,
        "notional_multiplier": 0.58,
        "require_meta_gate_pass": True,
        "capital_bucket": "trend_position_bucket",
        "max_turnover_bias": "low",
        "breakout_exception_allowed": False,
    },
}

HOLDING_PROFILE_MANAGEMENT_POLICIES: dict[str, dict[str, Any]] = {
    HOLDING_PROFILE_SCALP: {
        "break_even_trigger_r": 0.7,
        "partial_take_profit_trigger_r": 1.1,
        "partial_take_profit_fraction": 0.35,
        "trailing_stop_atr_multiplier": 0.95,
    },
    HOLDING_PROFILE_SWING: {
        "break_even_trigger_r": 1.0,
        "partial_take_profit_trigger_r": 1.5,
        "partial_take_profit_fraction": 0.25,
        "trailing_stop_atr_multiplier": 1.2,
    },
    HOLDING_PROFILE_POSITION: {
        "break_even_trigger_r": 1.2,
        "partial_take_profit_trigger_r": 2.2,
        "partial_take_profit_fraction": 0.18,
        "trailing_stop_atr_multiplier": 1.45,
    },
}

HOLDING_PROFILE_CADENCE_HINTS: dict[str, dict[str, int | str]] = {
    HOLDING_PROFILE_SCALP: {
        "decision_interval_minutes": 15,
        "position_management_interval_seconds": 30,
        "entry_plan_watcher_interval_minutes": 1,
        "management_bias": "fast",
    },
    HOLDING_PROFILE_SWING: {
        "decision_interval_minutes": 20,
        "position_management_interval_seconds": 45,
        "entry_plan_watcher_interval_minutes": 1,
        "management_bias": "balanced",
    },
    HOLDING_PROFILE_POSITION: {
        "decision_interval_minutes": 30,
        "position_management_interval_seconds": 60,
        "entry_plan_watcher_interval_minutes": 1,
        "management_bias": "patient",
    },
}


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _timeframe_minutes(timeframe: str) -> int:
    normalized = str(timeframe or "").strip().lower()
    if normalized.endswith("m") and normalized[:-1].isdigit():
        return max(int(normalized[:-1]), 1)
    if normalized.endswith("h") and normalized[:-1].isdigit():
        return max(int(normalized[:-1]) * 60, 1)
    if normalized.endswith("d") and normalized[:-1].isdigit():
        return max(int(normalized[:-1]) * 1440, 1)
    return 15


def deterministic_stop_management_payload(*, hard_stop_active: bool = True) -> dict[str, Any]:
    return {
        "initial_stop_type": DETERMINISTIC_INITIAL_STOP_TYPE,
        "ai_stop_management_allowed": True,
        "hard_stop_active": bool(hard_stop_active),
        "stop_widening_allowed": False,
    }


def resolve_holding_profile_risk_policy(profile: str | None) -> dict[str, Any]:
    normalized = str(profile or HOLDING_PROFILE_SCALP).strip().lower()
    if normalized not in HOLDING_PROFILE_RISK_POLICIES:
        normalized = HOLDING_PROFILE_SCALP
    return {
        "holding_profile": normalized,
        **HOLDING_PROFILE_RISK_POLICIES[normalized],
    }


def resolve_holding_profile_management_policy(profile: str | None) -> dict[str, Any]:
    normalized = str(profile or HOLDING_PROFILE_SCALP).strip().lower()
    if normalized not in HOLDING_PROFILE_MANAGEMENT_POLICIES:
        normalized = HOLDING_PROFILE_SCALP
    return {
        "holding_profile": normalized,
        **HOLDING_PROFILE_MANAGEMENT_POLICIES[normalized],
    }


def resolve_holding_profile_cadence_hint(profile: str | None) -> dict[str, Any]:
    normalized = str(profile or HOLDING_PROFILE_SCALP).strip().lower()
    if normalized not in HOLDING_PROFILE_CADENCE_HINTS:
        normalized = HOLDING_PROFILE_SCALP
    return {
        "holding_profile": normalized,
        **HOLDING_PROFILE_CADENCE_HINTS[normalized],
    }


def evaluate_holding_profile(
    *,
    decision: str,
    features: FeaturePayload | None,
    selection_context: Mapping[str, Any] | None = None,
    strategy_engine: str | None = None,
) -> dict[str, Any]:
    decision_side = str(decision or "").lower()
    stop_policy = deterministic_stop_management_payload()
    if decision_side not in {"long", "short"} or features is None:
        return {
            "holding_profile": HOLDING_PROFILE_SCALP,
            "holding_profile_reason": "non_entry_or_missing_features_defaults_to_scalp",
            "rationale_codes": ["HOLDING_PROFILE_SCALP_DEFAULT"],
            "structural_alignment_strong": False,
            "intraday_alignment_ok": False,
            "breadth_not_weak": False,
            "lead_lag_positive": False,
            "relative_strength_positive": False,
            "derivatives_headwind_severe": False,
            "position_profile_eligible": False,
            "swing_profile_eligible": False,
            "cadence_hint": resolve_holding_profile_cadence_hint(HOLDING_PROFILE_SCALP),
            "risk_policy": resolve_holding_profile_risk_policy(HOLDING_PROFILE_SCALP),
            "management_policy": resolve_holding_profile_management_policy(HOLDING_PROFILE_SCALP),
            **stop_policy,
        }

    is_long = decision_side == "long"
    regime = features.regime
    breadth = _as_dict(_as_dict(selection_context).get("universe_breadth"))
    breadth_regime = str(breadth.get("breadth_regime") or "mixed")
    breadth_not_weak = breadth_regime not in {"weak_breadth", "transition_fragile"}
    breadth_supportive = breadth_regime == "trend_expansion"

    multi_timeframe = list(features.multi_timeframe.values())
    higher_timeframes = [context for context in multi_timeframe if _timeframe_minutes(context.timeframe) >= 60]
    strong_htf_confirmations = 0
    anchor_confirmations = 0
    htf_scores: list[float] = []
    for context in higher_timeframes:
        trend_score = float(context.trend_score)
        momentum_score = float(context.momentum_score)
        aligned = (trend_score >= 0.22 and momentum_score >= 0.0) if is_long else (trend_score <= -0.22 and momentum_score <= 0.0)
        if aligned:
            strong_htf_confirmations += 1
            timeframe_minutes = _timeframe_minutes(context.timeframe)
            if timeframe_minutes >= 240:
                anchor_confirmations += 1
        directional_score = min(
            max(
                ((trend_score + momentum_score) / 2.0) if is_long else ((-trend_score + -momentum_score) / 2.0),
                0.0,
            ),
            1.0,
        )
        htf_scores.append(directional_score)
    htf_alignment_score = sum(htf_scores) / len(htf_scores) if htf_scores else 0.0
    intraday_alignment_ok = bool(
        regime.primary_regime not in {"range", "transition"}
        and regime.trend_alignment == ("bullish_aligned" if is_long else "bearish_aligned")
        and not regime.weak_volume
        and not regime.momentum_weakening
        and ((features.trend_score >= 0.2 and features.momentum_score >= 0.05) if is_long else (features.trend_score <= -0.2 and features.momentum_score <= -0.05))
    )
    structural_alignment_strong = bool(
        intraday_alignment_ok
        and strong_htf_confirmations >= max(1, min(len(higher_timeframes), 2))
        and anchor_confirmations >= 1
        and htf_alignment_score >= 0.58
    )

    lead_lag = features.lead_lag
    lead_lag_alignment = float(lead_lag.bullish_alignment_score if is_long else lead_lag.bearish_alignment_score)
    breakout_ahead = bool(lead_lag.bullish_breakout_ahead if is_long else lead_lag.bearish_breakout_ahead)
    continuation_supported = bool(lead_lag.bullish_continuation_supported if is_long else lead_lag.bearish_continuation_supported)
    pullback_supported = bool(lead_lag.bullish_pullback_supported if is_long else lead_lag.bearish_pullback_supported)
    lead_lag_positive = bool(
        lead_lag.available
        and lead_lag_alignment >= 0.66
        and not breakout_ahead
        and (lead_lag.strong_reference_confirmation or pullback_supported or continuation_supported)
    )

    derivatives = features.derivatives
    funding_headwind = bool(
        derivatives.funding_bias == ("long_headwind" if is_long else "short_headwind")
    )
    crowding_risk = bool(
        derivatives.crowded_long_risk if is_long else derivatives.crowded_short_risk
    )
    top_trader_crowded = bool(
        derivatives.top_trader_long_crowded if is_long else derivatives.top_trader_short_crowded
    )
    derivatives_alignment = float(
        derivatives.long_alignment_score if is_long else derivatives.short_alignment_score
    )
    derivatives_headwind_severe = bool(
        crowding_risk
        or top_trader_crowded
        or derivatives.spread_stress
        or (funding_headwind and derivatives.spread_headwind)
        or derivatives_alignment <= 0.34
        or (
            str(strategy_engine or "") == "breakout_exception_engine"
            and (
                derivatives.breakout_spread_headwind
                or not derivatives.oi_expanding_with_price
            )
        )
    )

    directional_trend_score = float(features.trend_score if is_long else -features.trend_score)
    directional_momentum_score = float(features.momentum_score if is_long else -features.momentum_score)
    relative_strength_score = min(
        max(
            (
                max(directional_trend_score, 0.0) * 0.35
                + max(directional_momentum_score, 0.0) * 0.25
                + max(features.volume_ratio - 1.0, 0.0) * 0.15
                + max(features.volume_persistence.persistence_ratio - 1.0, 0.0) * 0.1
                + max(lead_lag_alignment - 0.5, 0.0) * 0.3
            ),
            0.0,
        ),
        1.0,
    )
    relative_strength_positive = bool(
        relative_strength_score >= 0.18
        and directional_trend_score >= 0.18
        and directional_momentum_score >= 0.04
        and not regime.weak_volume
    )

    swing_profile_eligible = bool(
        intraday_alignment_ok
        and breadth_regime != "weak_breadth"
        and not derivatives_headwind_severe
        and str(strategy_engine or "") != "range_mean_reversion_engine"
    )
    position_profile_eligible = bool(
        structural_alignment_strong
        and breadth_not_weak
        and lead_lag_positive
        and relative_strength_positive
        and not derivatives_headwind_severe
        and str(strategy_engine or "") not in {"breakout_exception_engine", "range_mean_reversion_engine"}
    )

    rationale_codes = ["HOLDING_PROFILE_SCALP_DEFAULT"]
    reason = "scalp_default_intraday_bias"
    holding_profile = HOLDING_PROFILE_SCALP
    if str(strategy_engine or "") == "breakout_exception_engine":
        rationale_codes.append("HOLDING_PROFILE_BREAKOUT_SCALP_ONLY")
        reason = "breakout_exception_kept_intraday"
    elif position_profile_eligible and breadth_supportive:
        holding_profile = HOLDING_PROFILE_POSITION
        rationale_codes = [
            "HOLDING_PROFILE_POSITION_ALLOWED",
            "HOLDING_PROFILE_STRONG_HTF_ALIGNMENT",
            "HOLDING_PROFILE_LEAD_LAG_CONFIRMED",
        ]
        reason = "strong_structural_regime_supports_position"
    elif swing_profile_eligible:
        holding_profile = HOLDING_PROFILE_SWING
        rationale_codes = [
            "HOLDING_PROFILE_SWING_ALLOWED",
            "HOLDING_PROFILE_INTRADAY_ALIGNMENT",
        ]
        reason = "intraday_alignment_supports_swing"
    elif regime.primary_regime in {"range", "transition"} or regime.weak_volume:
        rationale_codes = [
            "HOLDING_PROFILE_SCALP_DEFAULT",
            "HOLDING_PROFILE_WEAK_REGIME_SCALP_ONLY",
        ]
        reason = "weak_or_transitional_regime_kept_scalp"

    return {
        "holding_profile": holding_profile,
        "holding_profile_reason": reason,
        "rationale_codes": rationale_codes,
        "breadth_regime": breadth_regime,
        "breadth_not_weak": breadth_not_weak,
        "lead_lag_positive": lead_lag_positive,
        "lead_lag_alignment": round(lead_lag_alignment, 4),
        "relative_strength_positive": relative_strength_positive,
        "relative_strength_score": round(relative_strength_score, 4),
        "derivatives_headwind_severe": derivatives_headwind_severe,
        "structural_alignment_strong": structural_alignment_strong,
        "intraday_alignment_ok": intraday_alignment_ok,
        "position_profile_eligible": position_profile_eligible,
        "swing_profile_eligible": swing_profile_eligible,
        "htf_alignment_score": round(htf_alignment_score, 4),
        "strong_htf_confirmations": strong_htf_confirmations,
        "anchor_confirmations": anchor_confirmations,
        "strategy_engine": strategy_engine or "unspecified",
        "cadence_hint": resolve_holding_profile_cadence_hint(holding_profile),
        "risk_policy": resolve_holding_profile_risk_policy(holding_profile),
        "management_policy": resolve_holding_profile_management_policy(holding_profile),
        **stop_policy,
    }
