from __future__ import annotations

from trading_mvp.schemas import FeaturePayload, MetaGateResult, TradeDecision

META_GATE_REJECT_THRESHOLD = 0.38
META_GATE_SOFT_PASS_THRESHOLD = 0.58
META_GATE_SOFT_MULTIPLIERS = {
    "risk_multiplier": 0.72,
    "leverage_multiplier": 0.85,
    "notional_multiplier": 0.65,
}


def _as_dict(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        if value in {None, ""}:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, *, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _side_alignment(features: FeaturePayload, side: str) -> tuple[float, float]:
    if side == "long":
        return (
            float(features.derivatives.long_alignment_score),
            float(features.lead_lag.bullish_alignment_score),
        )
    return (
        float(features.derivatives.short_alignment_score),
        float(features.lead_lag.bearish_alignment_score),
    )


def _breadth_score(selection_context: dict[str, object], side: str) -> tuple[float, list[str]]:
    breadth = _as_dict(selection_context.get("universe_breadth"))
    regime = str(breadth.get("breadth_regime") or "mixed")
    directional_bias = str(breadth.get("directional_bias") or "neutral")
    reasons: list[str] = []
    base = {
        "weak_breadth": 0.32,
        "transition_fragile": 0.42,
        "mixed": 0.56,
        "trend_expansion": 0.74,
    }.get(regime, 0.52)
    target_bias = "bullish" if side == "long" else "bearish"
    if directional_bias == target_bias:
        base += 0.04
    elif directional_bias not in {"neutral", "mixed", "unknown", ""}:
        base -= 0.08
        reasons.append("META_GATE_BREADTH_COUNTER_BIAS")
    if regime == "weak_breadth":
        reasons.append("META_GATE_WEAK_BREADTH")
    if regime == "transition_fragile":
        reasons.append("META_GATE_TRANSITIONAL_BREADTH")
    return _clamp(base), reasons


def _expected_time_to_profit(entry_mode: str, probability: float, avg_slippage_bps: float, breadth_score: float) -> int:
    baseline = {
        "breakout_confirm": 20,
        "continuation": 34,
        "pullback_confirm": 48,
    }.get(str(entry_mode or "none").lower(), 40)
    if probability < META_GATE_SOFT_PASS_THRESHOLD:
        baseline += 8
    if probability < META_GATE_REJECT_THRESHOLD:
        baseline += 12
    if avg_slippage_bps >= 8.0:
        baseline += 6
    if breadth_score <= 0.4:
        baseline += 8
    return max(5, min(int(round(baseline)), 240))


def evaluate_meta_gate(
    decision: TradeDecision,
    *,
    feature_payload: FeaturePayload,
    selection_context: dict[str, object] | None = None,
    decision_metadata: dict[str, object] | None = None,
) -> MetaGateResult:
    side = str(decision.decision or "").lower()
    if side not in {"long", "short"}:
        return MetaGateResult(
            gate_decision="pass",
            expected_hit_probability=0.5,
            components={"applicable": False, "reason": "SURVIVAL_OR_HOLD_PATH"},
        )

    selection = _as_dict(selection_context)
    metadata = _as_dict(decision_metadata)
    performance_summary = _as_dict(selection.get("performance_summary"))
    score_payload = _as_dict(selection.get("score"))
    decision_agreement = _as_dict(metadata.get("decision_agreement"))

    candidate_total_score = _safe_float(
        score_payload.get("total_score"),
        default=_safe_float(performance_summary.get("score"), default=0.55),
    )
    performance_score = _safe_float(performance_summary.get("score"), default=0.55)
    expectancy = _safe_float(performance_summary.get("expectancy"))
    net_pnl_after_fees = _safe_float(performance_summary.get("net_pnl_after_fees"))
    avg_signed_slippage_bps = _safe_float(performance_summary.get("avg_signed_slippage_bps"))
    underperforming = bool(performance_summary.get("underperforming", False))
    sample_size = int(performance_summary.get("sample_size", 0) or 0)
    loss_streak = int(performance_summary.get("loss_streak", 0) or 0)

    raw_derivatives_alignment, raw_lead_lag_alignment = _side_alignment(feature_payload, side)
    derivatives_alignment = _safe_float(
        score_payload.get("derivatives_alignment"),
        default=raw_derivatives_alignment,
    )
    lead_lag_alignment = _safe_float(
        score_payload.get("lead_lag_alignment"),
        default=raw_lead_lag_alignment,
    )
    derivatives_headwind = bool(
        feature_payload.derivatives.spread_headwind
        or (
            side == "long"
            and (feature_payload.derivatives.crowded_long_risk or feature_payload.derivatives.funding_bias == "long_headwind")
        )
        or (
            side == "short"
            and (feature_payload.derivatives.crowded_short_risk or feature_payload.derivatives.funding_bias == "short_headwind")
        )
    )
    breakout_filter_blocking = bool(feature_payload.derivatives.breakout_spread_headwind)
    if side == "long":
        lead_lag_divergence = bool(
            feature_payload.lead_lag.available
            and (
                feature_payload.lead_lag.bullish_alignment_score <= 0.32
                or (
                    decision.entry_mode == "breakout_confirm"
                    and feature_payload.lead_lag.bullish_breakout_ahead
                    and not feature_payload.lead_lag.bullish_breakout_confirmed
                )
            )
        )
    else:
        lead_lag_divergence = bool(
            feature_payload.lead_lag.available
            and (
                feature_payload.lead_lag.bearish_alignment_score <= 0.32
                or (
                    decision.entry_mode == "breakout_confirm"
                    and feature_payload.lead_lag.bearish_breakout_ahead
                    and not feature_payload.lead_lag.bearish_breakout_confirmed
                )
            )
        )

    breadth_score, breadth_reasons = _breadth_score(selection, side)
    slippage_score = _clamp(0.7 - (min(max(avg_signed_slippage_bps, 0.0), 16.0) / 16.0 * 0.5))
    agreement_level = str(decision_agreement.get("level") or "full_agreement")
    ai_used = bool(decision_agreement.get("ai_used", False))
    agreement_score = {
        "full_agreement": 0.72,
        "partial_agreement": 0.52,
        "disagreement": 0.28,
    }.get(agreement_level, 0.58)
    if not ai_used:
        agreement_score = 0.6

    probability = _clamp(
        (candidate_total_score * 0.24)
        + (performance_score * 0.28)
        + (breadth_score * 0.12)
        + (lead_lag_alignment * 0.12)
        + (derivatives_alignment * 0.12)
        + (slippage_score * 0.08)
        + (agreement_score * 0.04),
    )

    probability -= 0.08 if underperforming else 0.0
    probability -= 0.05 if derivatives_headwind else 0.0
    probability -= 0.05 if lead_lag_divergence else 0.0
    probability -= 0.04 if breakout_filter_blocking else 0.0
    probability -= 0.04 if feature_payload.regime.primary_regime == "range" and feature_payload.regime.weak_volume else 0.0
    probability = _clamp(probability)

    reject_reason_codes: list[str] = []
    if probability < META_GATE_REJECT_THRESHOLD:
        reject_reason_codes.append("META_GATE_LOW_HIT_PROBABILITY")
    if underperforming and expectancy < 0 and net_pnl_after_fees < 0:
        reject_reason_codes.append("META_GATE_NEGATIVE_EXPECTANCY")
    if avg_signed_slippage_bps >= 12.0:
        reject_reason_codes.append("META_GATE_ADVERSE_SIGNED_SLIPPAGE")
    if lead_lag_divergence:
        reject_reason_codes.append("META_GATE_LEAD_LAG_DIVERGENCE")
    if derivatives_headwind or breakout_filter_blocking:
        reject_reason_codes.append("META_GATE_DERIVATIVES_HEADWIND")
    reject_reason_codes.extend(breadth_reasons)
    reject_reason_codes = list(dict.fromkeys(reject_reason_codes))

    severe_negative_context = bool(
        underperforming
        and expectancy < 0
        and net_pnl_after_fees < 0
        and (avg_signed_slippage_bps >= 12.0 or loss_streak >= 3)
        and sample_size >= 4
    )
    moderate_headwind = bool(
        probability < META_GATE_SOFT_PASS_THRESHOLD
        or derivatives_headwind
        or lead_lag_divergence
        or avg_signed_slippage_bps >= 8.0
        or (ai_used and agreement_level == "partial_agreement")
    )

    gate_decision: str
    confidence_adjustment: float
    risk_multiplier: float
    leverage_multiplier: float
    notional_multiplier: float
    if severe_negative_context or probability < META_GATE_REJECT_THRESHOLD:
        gate_decision = "reject"
        confidence_adjustment = -0.18
        risk_multiplier = 0.0
        leverage_multiplier = 0.0
        notional_multiplier = 0.0
    elif moderate_headwind:
        gate_decision = "soft_pass"
        confidence_adjustment = -0.08
        risk_multiplier = META_GATE_SOFT_MULTIPLIERS["risk_multiplier"]
        leverage_multiplier = META_GATE_SOFT_MULTIPLIERS["leverage_multiplier"]
        notional_multiplier = META_GATE_SOFT_MULTIPLIERS["notional_multiplier"]
    else:
        gate_decision = "pass"
        confidence_adjustment = 0.0
        risk_multiplier = 1.0
        leverage_multiplier = 1.0
        notional_multiplier = 1.0
        reject_reason_codes = []

    return MetaGateResult(
        gate_decision=gate_decision,  # type: ignore[arg-type]
        expected_hit_probability=round(probability, 6),
        expected_time_to_profit_minutes=_expected_time_to_profit(
            str(decision.entry_mode or "none"),
            probability,
            avg_signed_slippage_bps,
            breadth_score,
        ),
        reject_reason_codes=reject_reason_codes,
        confidence_adjustment=round(confidence_adjustment, 6),
        risk_multiplier=round(risk_multiplier, 6),
        leverage_multiplier=round(leverage_multiplier, 6),
        notional_multiplier=round(notional_multiplier, 6),
        components={
            "applicable": True,
            "candidate_total_score": round(candidate_total_score, 6),
            "performance_score": round(performance_score, 6),
            "expectancy": round(expectancy, 6),
            "net_pnl_after_fees": round(net_pnl_after_fees, 6),
            "avg_signed_slippage_bps": round(avg_signed_slippage_bps, 6),
            "sample_size": sample_size,
            "loss_streak": loss_streak,
            "underperforming": underperforming,
            "breadth_regime": str(_as_dict(selection.get("universe_breadth")).get("breadth_regime") or "mixed"),
            "breadth_score": round(breadth_score, 6),
            "lead_lag_alignment": round(lead_lag_alignment, 6),
            "derivatives_alignment": round(derivatives_alignment, 6),
            "slippage_score": round(slippage_score, 6),
            "agreement_level": agreement_level,
            "ai_used": ai_used,
            "derivatives_headwind": derivatives_headwind,
            "lead_lag_divergence": lead_lag_divergence,
            "breakout_filter_blocking": breakout_filter_blocking,
            "breadth_reasons": breadth_reasons,
        },
    )
