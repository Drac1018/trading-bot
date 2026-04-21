from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from trading_mvp.schemas import (
    AIDecisionContextPacket,
    AIReviewTriggerPayload,
    CompositeRegimePacket,
    DataQualityGrade,
    DataQualityPacket,
    DerivativesSummaryPayload,
    EventContextSummaryPayload,
    FeaturePayload,
    LeadLagSummaryPayload,
    MarketSnapshotPayload,
    PreviousThesisDeltaPacket,
    RegimeSummaryPayload,
)
from trading_mvp.services.holding_profile import (
    HOLDING_PROFILE_SCALP,
    deterministic_stop_management_payload,
)

_DATA_QUALITY_ORDER = {
    "complete": 0,
    "partial": 1,
    "degraded": 2,
    "unavailable": 3,
}
_TRANSITION_RISK_ORDER = {
    "low": 0,
    "medium": 1,
    "high": 2,
}
_DERIVATIVES_REGIME_ORDER = {
    "tailwind": 0,
    "neutral": 1,
    "headwind": 2,
    "unavailable": 3,
}
_EXECUTION_REGIME_ORDER = {
    "clean": 0,
    "normal": 1,
    "stress": 2,
    "unavailable": 3,
}


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item not in {None, ""}]


def _safe_float(value: object, *, default: float | None = None) -> float | None:
    try:
        if value in {None, ""}:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _unique_codes(*groups: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            code = str(item or "").strip()
            if not code or code in seen:
                continue
            ordered.append(code)
            seen.add(code)
    return ordered


def _review_trigger_payload(
    review_trigger: AIReviewTriggerPayload | Mapping[str, Any] | None,
) -> AIReviewTriggerPayload | None:
    if isinstance(review_trigger, AIReviewTriggerPayload):
        return review_trigger
    if isinstance(review_trigger, Mapping):
        payload = dict(review_trigger)
        triggered_at = _coerce_datetime(payload.get("triggered_at"))
        last_decision_at = _coerce_datetime(payload.get("last_decision_at"))
        if triggered_at is None:
            return None
        try:
            return AIReviewTriggerPayload(
                trigger_reason=str(payload.get("trigger_reason") or "manual_review_event"),  # type: ignore[arg-type]
                symbol=str(payload.get("symbol") or ""),
                timeframe=str(payload.get("timeframe") or ""),
                strategy_engine=str(payload.get("strategy_engine") or "") or None,
                holding_profile=str(payload.get("holding_profile") or "") or None,  # type: ignore[arg-type]
                assigned_slot=str(payload.get("assigned_slot") or "") or None,
                candidate_weight=_safe_float(payload.get("candidate_weight")),
                reason_codes=_as_list(payload.get("reason_codes")),
                trigger_fingerprint=str(payload.get("trigger_fingerprint") or ""),
                last_decision_at=last_decision_at,
                triggered_at=triggered_at,
            )
        except Exception:
            return None
    return None


def _direction_regime(features: FeaturePayload) -> str:
    alignment = str(features.regime.trend_alignment or "")
    if alignment == "bullish_aligned" or features.trend_score >= 0.18:
        return "bullish"
    if alignment == "bearish_aligned" or features.trend_score <= -0.18:
        return "bearish"
    return "neutral"


def build_composite_regime_packet(
    *,
    market_snapshot: MarketSnapshotPayload,
    features: FeaturePayload,
) -> CompositeRegimePacket:
    direction_regime = _direction_regime(features)
    breakout_direction = str(features.breakout.range_breakout_direction or "none")
    primary_regime = str(features.regime.primary_regime or "transition")
    volatility_regime = str(features.regime.volatility_regime or "normal")
    derivatives = features.derivatives
    lead_lag = features.lead_lag

    if primary_regime == "range":
        structure_regime = (
            "squeeze"
            if volatility_regime == "compressed"
            or features.volume_persistence.sustained_low_volume
            or features.volume_ratio < 0.92
            else "range"
        )
    elif primary_regime == "transition" or features.regime.momentum_weakening:
        structure_regime = "transition"
    elif breakout_direction != "none" and volatility_regime == "expanded":
        structure_regime = "expansion"
    else:
        structure_regime = "trend"

    if volatility_regime == "compressed":
        normalized_volatility = "calm"
    elif volatility_regime == "normal":
        normalized_volatility = "normal"
    else:
        spread_stress_score = _safe_float(derivatives.spread_stress_score, default=0.0) or 0.0
        normalized_volatility = "shock" if derivatives.spread_stress or spread_stress_score >= 0.75 else "fast"

    if features.regime.weak_volume or features.regime.volume_regime == "weak" or features.volume_ratio < 0.92:
        participation_regime = "weak"
    elif (
        features.regime.volume_regime == "strong"
        and features.volume_ratio >= 1.08
        and (
            features.volume_persistence.sustained_high_volume
            or features.volume_persistence.persistence_ratio >= 1.05
        )
    ):
        participation_regime = "strong"
    else:
        participation_regime = "mixed"

    if not derivatives.available:
        derivatives_regime = "unavailable"
    else:
        if direction_regime == "bullish":
            alignment_score = float(derivatives.long_alignment_score)
            headwind = bool(
                derivatives.crowded_long_risk
                or derivatives.top_trader_long_crowded
                or derivatives.spread_stress
                or derivatives.funding_bias == "long_headwind"
            )
        elif direction_regime == "bearish":
            alignment_score = float(derivatives.short_alignment_score)
            headwind = bool(
                derivatives.crowded_short_risk
                or derivatives.top_trader_short_crowded
                or derivatives.spread_stress
                or derivatives.funding_bias == "short_headwind"
            )
        else:
            alignment_score = max(float(derivatives.long_alignment_score), float(derivatives.short_alignment_score))
            headwind = bool(derivatives.spread_stress or derivatives.spread_headwind)
        if headwind or alignment_score <= 0.38:
            derivatives_regime = "headwind"
        elif alignment_score >= 0.62:
            derivatives_regime = "tailwind"
        else:
            derivatives_regime = "neutral"

    spread_bps = _safe_float(derivatives.spread_bps)
    if spread_bps is None and market_snapshot.derivatives_context.spread_bps is not None:
        spread_bps = float(market_snapshot.derivatives_context.spread_bps)
    spread_available = spread_bps is not None or derivatives.best_bid is not None or derivatives.best_ask is not None
    if not spread_available:
        execution_regime = "unavailable"
    elif derivatives.spread_stress or (spread_bps is not None and spread_bps >= 18.0):
        execution_regime = "stress"
    elif spread_bps is not None and spread_bps <= 6.0 and not market_snapshot.is_stale:
        execution_regime = "clean"
    else:
        execution_regime = "normal"

    if direction_regime == "bullish":
        persistence_bars = int(features.candle_structure.bullish_streak)
    elif direction_regime == "bearish":
        persistence_bars = int(features.candle_structure.bearish_streak)
    elif structure_regime in {"range", "squeeze"}:
        persistence_bars = int(features.breakout.lookback_bars)
    else:
        persistence_bars = 0
    if persistence_bars < 3:
        persistence_class = "early"
    elif persistence_bars < 8:
        persistence_class = "established"
    else:
        persistence_class = "extended"

    if (
        structure_regime == "transition"
        or primary_regime == "transition"
        or lead_lag.weak_reference_confirmation
        or features.regime.momentum_weakening
    ):
        transition_risk = "high"
    elif (
        participation_regime == "strong"
        and persistence_class in {"established", "extended"}
        and direction_regime != "neutral"
        and derivatives_regime != "headwind"
    ):
        transition_risk = "low"
    else:
        transition_risk = "medium"

    reason_codes = [
        f"PRIMARY_REGIME_{primary_regime.upper()}",
        f"DIRECTION_{direction_regime.upper()}",
        f"VOLATILITY_{normalized_volatility.upper()}",
        f"PARTICIPATION_{participation_regime.upper()}",
    ]
    if breakout_direction != "none":
        reason_codes.append(f"BREAKOUT_{breakout_direction.upper()}")
    if derivatives_regime != "unavailable":
        reason_codes.append(f"DERIVATIVES_{derivatives_regime.upper()}")
    if execution_regime != "unavailable":
        reason_codes.append(f"EXECUTION_{execution_regime.upper()}")
    if lead_lag.strong_reference_confirmation:
        reason_codes.append("LEAD_LAG_STRONG_CONFIRMATION")
    if features.regime.weak_volume:
        reason_codes.append("WEAK_VOLUME")
    if features.regime.momentum_weakening:
        reason_codes.append("MOMENTUM_WEAKENING")

    return CompositeRegimePacket(
        structure_regime=structure_regime,  # type: ignore[arg-type]
        direction_regime=direction_regime,  # type: ignore[arg-type]
        volatility_regime=normalized_volatility,  # type: ignore[arg-type]
        participation_regime=participation_regime,  # type: ignore[arg-type]
        derivatives_regime=derivatives_regime,  # type: ignore[arg-type]
        execution_regime=execution_regime,  # type: ignore[arg-type]
        persistence_bars=max(persistence_bars, 0),
        persistence_class=persistence_class,  # type: ignore[arg-type]
        transition_risk=transition_risk,  # type: ignore[arg-type]
        regime_reason_codes=_unique_codes(reason_codes),
    )


def build_data_quality_packet(
    *,
    market_snapshot: MarketSnapshotPayload,
    features: FeaturePayload,
    decision_reference: Mapping[str, Any] | None = None,
) -> DataQualityPacket:
    reference = _as_dict(decision_reference)
    sync_freshness_summary = _as_dict(reference.get("sync_freshness_summary"))
    missing_flags: list[str] = []
    stale_flags: list[str] = []
    feature_flags = _as_list(features.data_quality_flags)

    derivatives_available = bool(features.derivatives.available)
    if not derivatives_available:
        missing_flags.append("derivatives_context_unavailable")
    orderbook_available = bool(
        features.derivatives.best_bid is not None and features.derivatives.best_ask is not None
    )
    if not orderbook_available:
        missing_flags.append("orderbook_context_unavailable")
    spread_quality_available = bool(
        features.derivatives.spread_bps is not None
        or market_snapshot.derivatives_context.spread_bps is not None
        or features.derivatives.spread_stress_score is not None
        or market_snapshot.derivatives_context.spread_stress_score is not None
    )
    if not spread_quality_available:
        missing_flags.append("spread_quality_unavailable")
    if not market_snapshot.is_complete:
        missing_flags.append("market_snapshot_incomplete")
    if market_snapshot.is_stale:
        stale_flags.append("market_snapshot_stale")

    account_state_trustworthy = True
    for scope in ("account", "positions", "open_orders", "protective_orders"):
        scope_payload = _as_dict(sync_freshness_summary.get(scope))
        if not scope_payload:
            continue
        if bool(scope_payload.get("stale")):
            stale_flags.append(f"{scope}_sync_stale")
            account_state_trustworthy = False
        if bool(scope_payload.get("incomplete")):
            missing_flags.append(f"{scope}_sync_incomplete")
            account_state_trustworthy = False

    market_state_trustworthy = not market_snapshot.is_stale and market_snapshot.is_complete
    for flag in feature_flags:
        normalized_flag = flag.lower()
        if "stale" in normalized_flag:
            stale_flags.append(normalized_flag)
            market_state_trustworthy = False
        elif "incomplete" in normalized_flag or "missing" in normalized_flag:
            missing_flags.append(normalized_flag)
            if "market" in normalized_flag:
                market_state_trustworthy = False

    stale_flags = _unique_codes(stale_flags)
    missing_flags = _unique_codes(missing_flags)
    all_microstructure_missing = (
        not derivatives_available
        and not orderbook_available
        and not spread_quality_available
    )
    if not market_state_trustworthy and (market_snapshot.is_stale or not market_snapshot.is_complete):
        data_quality_grade: DataQualityGrade = "unavailable"
    elif stale_flags or not account_state_trustworthy or not market_state_trustworthy or all_microstructure_missing:
        data_quality_grade = "degraded"
    elif missing_flags:
        data_quality_grade = "partial"
    else:
        data_quality_grade = "complete"

    return DataQualityPacket(
        data_quality_grade=data_quality_grade,
        missing_context_flags=missing_flags,
        stale_context_flags=stale_flags,
        derivatives_available=derivatives_available,
        orderbook_available=orderbook_available,
        spread_quality_available=spread_quality_available,
        account_state_trustworthy=account_state_trustworthy,
        market_state_trustworthy=market_state_trustworthy,
    )


def build_regime_summary(
    *,
    features: FeaturePayload,
) -> RegimeSummaryPayload:
    regime = features.regime
    return RegimeSummaryPayload(
        primary_regime=regime.primary_regime,
        trend_alignment=regime.trend_alignment,
        volatility_regime=regime.volatility_regime,
        volume_regime=regime.volume_regime,
        momentum_state=regime.momentum_state,
        weak_volume=regime.weak_volume,
        momentum_weakening=regime.momentum_weakening,
    )


def build_derivatives_summary(
    *,
    features: FeaturePayload,
) -> DerivativesSummaryPayload:
    derivatives = features.derivatives
    return DerivativesSummaryPayload(
        available=derivatives.available,
        source=derivatives.source,
        funding_bias=derivatives.funding_bias,
        basis_bias=derivatives.basis_bias,
        taker_flow_alignment=derivatives.taker_flow_alignment,
        long_alignment_score=derivatives.long_alignment_score,
        short_alignment_score=derivatives.short_alignment_score,
        crowded_long_risk=derivatives.crowded_long_risk,
        crowded_short_risk=derivatives.crowded_short_risk,
        spread_headwind=derivatives.spread_headwind,
        spread_stress=derivatives.spread_stress,
        oi_expanding_with_price=derivatives.oi_expanding_with_price,
        oi_falling_on_breakout=derivatives.oi_falling_on_breakout,
    )


def build_lead_lag_summary(
    *,
    features: FeaturePayload,
) -> LeadLagSummaryPayload:
    lead_lag = features.lead_lag
    return LeadLagSummaryPayload(
        available=lead_lag.available,
        leader_bias=lead_lag.leader_bias,
        reference_symbols=list(lead_lag.reference_symbols),
        bullish_alignment_score=lead_lag.bullish_alignment_score,
        bearish_alignment_score=lead_lag.bearish_alignment_score,
        bullish_breakout_confirmed=lead_lag.bullish_breakout_confirmed,
        bearish_breakout_confirmed=lead_lag.bearish_breakout_confirmed,
        bullish_pullback_supported=lead_lag.bullish_pullback_supported,
        bearish_pullback_supported=lead_lag.bearish_pullback_supported,
        bullish_continuation_supported=lead_lag.bullish_continuation_supported,
        bearish_continuation_supported=lead_lag.bearish_continuation_supported,
        strong_reference_confirmation=lead_lag.strong_reference_confirmation,
        weak_reference_confirmation=lead_lag.weak_reference_confirmation,
    )


def build_event_context_summary(
    *,
    features: FeaturePayload,
) -> EventContextSummaryPayload:
    event_context = features.event_context
    return EventContextSummaryPayload(
        source_status=event_context.source_status,
        source_provenance=event_context.source_provenance,
        source_vendor=event_context.source_vendor,
        next_event_name=event_context.next_event_name,
        next_event_importance=event_context.next_event_importance,
        minutes_to_next_event=event_context.minutes_to_next_event,
        active_risk_window=event_context.active_risk_window,
        event_bias=event_context.event_bias,
        enrichment_vendors=list(event_context.enrichment_vendors),
    )


def _previous_ai_context_payload(
    *,
    previous_input_payload: Mapping[str, Any] | None,
    previous_decision_metadata: Mapping[str, Any] | None,
) -> dict[str, Any]:
    previous_input = _as_dict(previous_input_payload)
    previous_metadata = _as_dict(previous_decision_metadata)
    for container in (previous_input, previous_metadata):
        ai_context = _as_dict(container.get("ai_context"))
        if ai_context:
            return ai_context
    return {}


def _previous_regime_summary(
    *,
    previous_ai_context: Mapping[str, Any],
    previous_input_payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    previous_context = _as_dict(previous_ai_context)
    composite_regime = _as_dict(previous_context.get("composite_regime"))
    if composite_regime:
        return {
            "structure_regime": composite_regime.get("structure_regime"),
            "direction_regime": composite_regime.get("direction_regime"),
            "volatility_regime": composite_regime.get("volatility_regime"),
            "participation_regime": composite_regime.get("participation_regime"),
            "derivatives_regime": composite_regime.get("derivatives_regime"),
            "execution_regime": composite_regime.get("execution_regime"),
            "transition_risk": composite_regime.get("transition_risk"),
        }
    previous_input = _as_dict(previous_input_payload)
    previous_features = _as_dict(previous_input.get("features"))
    previous_regime = _as_dict(previous_features.get("regime"))
    if not previous_regime:
        return {}
    primary_regime = str(previous_regime.get("primary_regime") or "transition")
    trend_alignment = str(previous_regime.get("trend_alignment") or "mixed")
    if primary_regime == "range":
        structure_regime = "range"
    elif primary_regime == "transition":
        structure_regime = "transition"
    else:
        structure_regime = "trend"
    direction_regime = (
        "bullish"
        if trend_alignment == "bullish_aligned"
        else "bearish"
        if trend_alignment == "bearish_aligned"
        else "neutral"
    )
    volatility_regime = str(previous_regime.get("volatility_regime") or "normal")
    return {
        "structure_regime": structure_regime,
        "direction_regime": direction_regime,
        "volatility_regime": "calm" if volatility_regime == "compressed" else "fast" if volatility_regime == "expanded" else "normal",
        "transition_risk": "high" if primary_regime == "transition" else "medium",
    }


def _current_regime_summary(packet: CompositeRegimePacket) -> dict[str, Any]:
    return {
        "structure_regime": packet.structure_regime,
        "direction_regime": packet.direction_regime,
        "volatility_regime": packet.volatility_regime,
        "participation_regime": packet.participation_regime,
        "derivatives_regime": packet.derivatives_regime,
        "execution_regime": packet.execution_regime,
        "transition_risk": packet.transition_risk,
    }


def _previous_data_quality_grade(
    *,
    previous_ai_context: Mapping[str, Any],
) -> DataQualityGrade | None:
    previous_context = _as_dict(previous_ai_context)
    previous_data_quality = _as_dict(previous_context.get("data_quality"))
    grade = str(previous_data_quality.get("data_quality_grade") or "")
    if grade in _DATA_QUALITY_ORDER:
        return grade  # type: ignore[return-value]
    return None


def build_previous_thesis_delta(
    *,
    composite_regime: CompositeRegimePacket,
    data_quality: DataQualityPacket,
    strategy_engine: str | None,
    holding_profile: str | None,
    current_reason_codes: list[str],
    assigned_slot: str | None,
    candidate_weight: float | None,
    previous_decision_output: Mapping[str, Any] | None = None,
    previous_decision_metadata: Mapping[str, Any] | None = None,
    previous_input_payload: Mapping[str, Any] | None = None,
    previous_ai_invoked_at: datetime | None = None,
) -> PreviousThesisDeltaPacket:
    previous_output = _as_dict(previous_decision_output)
    previous_metadata = _as_dict(previous_decision_metadata)
    previous_ai_context = _previous_ai_context_payload(
        previous_input_payload=previous_input_payload,
        previous_decision_metadata=previous_decision_metadata,
    )
    previous_regime_packet_summary = _previous_regime_summary(
        previous_ai_context=previous_ai_context,
        previous_input_payload=previous_input_payload,
    )
    previous_data_quality_grade = _previous_data_quality_grade(previous_ai_context=previous_ai_context)
    previous_strategy_engine = (
        str(_as_dict(previous_ai_context).get("strategy_engine") or "")
        or str(
            _as_dict(
                _as_dict(previous_metadata.get("strategy_engine")).get("selected_engine")
            ).get("engine_name")
            or ""
        )
        or None
    )
    previous_holding_profile = (
        str(_as_dict(previous_ai_context).get("holding_profile") or "")
        or str(previous_output.get("holding_profile") or "")
        or str(previous_metadata.get("holding_profile") or "")
        or None
    )
    previous_rationale_codes = _as_list(previous_output.get("rationale_codes"))
    previous_no_trade_reason_codes = _as_list(previous_output.get("no_trade_reason_codes"))
    previous_invalidation_reason_codes = _as_list(previous_output.get("invalidation_reason_codes"))

    if not previous_output and not previous_ai_context and previous_ai_invoked_at is None:
        return PreviousThesisDeltaPacket()

    current_regime_summary = _current_regime_summary(composite_regime)
    delta_changed_fields: list[str] = []
    if previous_strategy_engine != strategy_engine:
        delta_changed_fields.append("strategy_engine")
    if previous_holding_profile != holding_profile:
        delta_changed_fields.append("holding_profile")
    if previous_regime_packet_summary != current_regime_summary:
        delta_changed_fields.append("composite_regime")
    if previous_data_quality_grade != data_quality.data_quality_grade:
        delta_changed_fields.append("data_quality_grade")
    previous_assigned_slot = str(_as_dict(previous_ai_context).get("assigned_slot") or "") or None
    if previous_assigned_slot != assigned_slot:
        delta_changed_fields.append("assigned_slot")
    previous_candidate_weight = _safe_float(_as_dict(previous_ai_context).get("candidate_weight"))
    if previous_candidate_weight != candidate_weight:
        delta_changed_fields.append("candidate_weight")

    current_reason_set = set(current_reason_codes)
    previous_reason_set = set(previous_rationale_codes)
    delta_reason_codes_added = sorted(current_reason_set - previous_reason_set)
    delta_reason_codes_removed = sorted(previous_reason_set - current_reason_set)

    regime_transition_detected = (
        previous_regime_packet_summary.get("structure_regime") != composite_regime.structure_regime
        or previous_regime_packet_summary.get("direction_regime") != composite_regime.direction_regime
        or previous_regime_packet_summary.get("volatility_regime") != composite_regime.volatility_regime
        or composite_regime.transition_risk == "high"
    )
    data_quality_changed = previous_data_quality_grade != data_quality.data_quality_grade
    thesis_degrade_detected = bool(
        _DATA_QUALITY_ORDER.get(data_quality.data_quality_grade, 0)
        > _DATA_QUALITY_ORDER.get(previous_data_quality_grade or "complete", 0)
        or _TRANSITION_RISK_ORDER.get(composite_regime.transition_risk, 0)
        > _TRANSITION_RISK_ORDER.get(
            str(previous_regime_packet_summary.get("transition_risk") or "low"),
            0,
        )
        or _DERIVATIVES_REGIME_ORDER.get(composite_regime.derivatives_regime, 0)
        > _DERIVATIVES_REGIME_ORDER.get(
            str(previous_regime_packet_summary.get("derivatives_regime") or "tailwind"),
            0,
        )
        or _EXECUTION_REGIME_ORDER.get(composite_regime.execution_regime, 0)
        > _EXECUTION_REGIME_ORDER.get(
            str(previous_regime_packet_summary.get("execution_regime") or "clean"),
            0,
        )
        or bool(delta_reason_codes_added)
    )

    return PreviousThesisDeltaPacket(
        previous_decision=str(previous_output.get("decision") or "") or None,  # type: ignore[arg-type]
        previous_strategy_engine=previous_strategy_engine,
        previous_holding_profile=previous_holding_profile,  # type: ignore[arg-type]
        previous_rationale_codes=previous_rationale_codes,
        previous_no_trade_reason_codes=previous_no_trade_reason_codes,
        previous_invalidation_reason_codes=previous_invalidation_reason_codes,
        previous_regime_packet_summary=previous_regime_packet_summary,
        previous_data_quality_grade=previous_data_quality_grade,
        last_ai_invoked_at=previous_ai_invoked_at,
        delta_changed_fields=delta_changed_fields,
        delta_reason_codes_added=delta_reason_codes_added,
        delta_reason_codes_removed=delta_reason_codes_removed,
        thesis_degrade_detected=thesis_degrade_detected,
        regime_transition_detected=regime_transition_detected,
        data_quality_changed=data_quality_changed,
    )


def _strategy_engine_name(
    *,
    selection_context: Mapping[str, Any],
    previous_decision_metadata: Mapping[str, Any] | None,
    review_trigger: AIReviewTriggerPayload | None,
) -> str | None:
    selection = _as_dict(selection_context)
    if str(selection.get("strategy_engine") or ""):
        return str(selection.get("strategy_engine"))
    previous_metadata = _as_dict(previous_decision_metadata)
    strategy_engine_payload = _as_dict(previous_metadata.get("strategy_engine"))
    selected_engine = _as_dict(strategy_engine_payload.get("selected_engine"))
    if str(selected_engine.get("engine_name") or ""):
        return str(selected_engine.get("engine_name"))
    if review_trigger is not None and review_trigger.strategy_engine:
        return review_trigger.strategy_engine
    return None


def _strategy_engine_context(
    *,
    selection_context: Mapping[str, Any],
    previous_decision_metadata: Mapping[str, Any] | None,
) -> dict[str, Any]:
    selection = _as_dict(selection_context)
    selection_engine_context = _as_dict(selection.get("strategy_engine_context"))
    if selection_engine_context:
        return selection_engine_context
    previous_metadata = _as_dict(previous_decision_metadata)
    strategy_engine_payload = _as_dict(previous_metadata.get("strategy_engine"))
    if strategy_engine_payload:
        return strategy_engine_payload
    return {}


def _selection_context_summary(selection_context: Mapping[str, Any]) -> dict[str, Any]:
    selection = _as_dict(selection_context)
    summary: dict[str, Any] = {}
    for key in (
        "selected",
        "selection_reason",
        "selected_reason",
        "rejected_reason",
        "entry_mode",
        "scenario",
        "assigned_slot",
        "slot_label",
        "slot_reason",
        "candidate_weight",
        "portfolio_weight",
        "slot_conviction_score",
        "meta_gate_probability",
        "agreement_alignment_score",
        "agreement_level_hint",
        "execution_quality_score",
        "capacity_reason",
        "breadth_regime",
        "slot_applies_soft_cap",
    ):
        if key in selection:
            summary[key] = selection.get(key)
    slot_allocation = _as_dict(selection.get("slot_allocation"))
    if slot_allocation:
        summary["slot_allocation"] = slot_allocation
    reason_codes = _as_list(selection.get("reason_codes"))
    if reason_codes:
        summary["reason_codes"] = reason_codes
    universe_breadth = _as_dict(selection.get("universe_breadth"))
    if universe_breadth:
        summary["universe_breadth"] = {
            key: universe_breadth.get(key)
            for key in ("breadth_regime", "directional_bias", "bullish_aligned_count", "bearish_aligned_count")
            if key in universe_breadth
        }
    return summary


def build_ai_decision_context(
    *,
    market_snapshot: MarketSnapshotPayload,
    features: FeaturePayload,
    risk_context: Mapping[str, Any] | None = None,
    selection_context: Mapping[str, Any] | None = None,
    review_trigger: AIReviewTriggerPayload | Mapping[str, Any] | None = None,
    decision_reference: Mapping[str, Any] | None = None,
    previous_decision_output: Mapping[str, Any] | None = None,
    previous_decision_metadata: Mapping[str, Any] | None = None,
    previous_input_payload: Mapping[str, Any] | None = None,
    previous_ai_invoked_at: datetime | None = None,
) -> AIDecisionContextPacket:
    resolved_risk_context = _as_dict(risk_context)
    resolved_selection_context = _as_dict(selection_context)
    if not resolved_selection_context:
        resolved_selection_context = _as_dict(resolved_risk_context.get("selection_context"))
    resolved_review_trigger = _review_trigger_payload(review_trigger)
    position_management_context = _as_dict(resolved_risk_context.get("position_management_context"))
    selection_holding_profile_context = _as_dict(resolved_selection_context.get("holding_profile_context"))
    previous_metadata = _as_dict(previous_decision_metadata)

    composite_regime = build_composite_regime_packet(
        market_snapshot=market_snapshot,
        features=features,
    )
    regime_summary = build_regime_summary(features=features)
    derivatives_summary = build_derivatives_summary(features=features)
    lead_lag_summary = build_lead_lag_summary(features=features)
    event_context_summary = build_event_context_summary(features=features)
    data_quality = build_data_quality_packet(
        market_snapshot=market_snapshot,
        features=features,
        decision_reference=decision_reference,
    )

    strategy_engine = _strategy_engine_name(
        selection_context=resolved_selection_context,
        previous_decision_metadata=previous_decision_metadata,
        review_trigger=resolved_review_trigger,
    )
    holding_profile = (
        str(resolved_selection_context.get("holding_profile") or "")
        or str(position_management_context.get("holding_profile") or "")
        or str(previous_metadata.get("holding_profile") or "")
        or str(resolved_review_trigger.holding_profile if resolved_review_trigger is not None else "")
        or HOLDING_PROFILE_SCALP
    )
    holding_profile_reason = (
        str(resolved_selection_context.get("holding_profile_reason") or "")
        or str(position_management_context.get("holding_profile_reason") or "")
        or str(previous_metadata.get("holding_profile_reason") or "")
        or None
    )
    stop_management_context = (
        selection_holding_profile_context
        or position_management_context
        or _as_dict(previous_metadata.get("holding_profile_context"))
    )
    stop_management_defaults = deterministic_stop_management_payload(
        hard_stop_active=bool(stop_management_context.get("hard_stop_active", True))
    )
    assigned_slot = (
        str(resolved_selection_context.get("assigned_slot") or "")
        or str(_as_dict(previous_metadata.get("slot_allocation")).get("assigned_slot") or "")
        or str(resolved_review_trigger.assigned_slot if resolved_review_trigger is not None else "")
        or None
    )
    candidate_weight = _safe_float(
        resolved_selection_context.get("candidate_weight")
        if "candidate_weight" in resolved_selection_context
        else (
            _as_dict(previous_metadata.get("slot_allocation")).get("candidate_weight")
            if previous_metadata
            else (
                resolved_review_trigger.candidate_weight
                if resolved_review_trigger is not None
                else None
            )
        )
    )
    blocked_reason_codes = _unique_codes(
        _as_list(resolved_risk_context.get("blocked_reason_codes")),
        _as_list(resolved_selection_context.get("blocked_reason_codes")),
        [str(resolved_selection_context.get("rejected_reason") or "")]
        if resolved_selection_context.get("rejected_reason")
        else [],
        ["DECISION_REFERENCE_FRESHNESS_BLOCKING"]
        if bool(_as_dict(decision_reference).get("freshness_blocking"))
        else [],
    )
    current_reason_codes = _unique_codes(
        _as_list(resolved_selection_context.get("reason_codes")),
        _as_list(
            _as_dict(resolved_selection_context.get("candidate")).get("rationale_codes"),
        ),
        list(resolved_review_trigger.reason_codes) if resolved_review_trigger is not None else [],
        blocked_reason_codes,
    )
    prompt_family_hint = (
        f"{resolved_review_trigger.trigger_reason}:{strategy_engine}"
        if resolved_review_trigger is not None and strategy_engine
        else strategy_engine
        or (resolved_review_trigger.trigger_reason if resolved_review_trigger is not None else None)
    )
    previous_thesis = build_previous_thesis_delta(
        composite_regime=composite_regime,
        data_quality=data_quality,
        strategy_engine=strategy_engine,
        holding_profile=holding_profile,
        current_reason_codes=current_reason_codes,
        assigned_slot=assigned_slot,
        candidate_weight=candidate_weight,
        previous_decision_output=previous_decision_output,
        previous_decision_metadata=previous_decision_metadata,
        previous_input_payload=previous_input_payload,
        previous_ai_invoked_at=previous_ai_invoked_at,
    )
    return AIDecisionContextPacket(
        symbol=market_snapshot.symbol,
        timeframe=market_snapshot.timeframe,
        trigger_type=resolved_review_trigger.trigger_reason if resolved_review_trigger is not None else None,
        composite_regime=composite_regime,
        regime_summary=regime_summary,
        derivatives_summary=derivatives_summary,
        lead_lag_summary=lead_lag_summary,
        event_context_summary=event_context_summary,
        data_quality=data_quality,
        previous_thesis=previous_thesis,
        strategy_engine=strategy_engine,
        strategy_engine_context=_strategy_engine_context(
            selection_context=resolved_selection_context,
            previous_decision_metadata=previous_decision_metadata,
        ),
        holding_profile=holding_profile,  # type: ignore[arg-type]
        holding_profile_reason=holding_profile_reason,
        assigned_slot=assigned_slot,
        candidate_weight=candidate_weight,
        capacity_reason=(
            str(resolved_selection_context.get("capacity_reason") or "")
            or str(resolved_selection_context.get("slot_reason") or "")
            or None
        ),
        blocked_reason_codes=blocked_reason_codes,
        hard_stop_active=bool(
            stop_management_context.get("hard_stop_active", stop_management_defaults["hard_stop_active"])
        ),
        stop_widening_allowed=bool(
            stop_management_context.get(
                "stop_widening_allowed",
                stop_management_defaults["stop_widening_allowed"],
            )
        ),
        initial_stop_type=str(
            stop_management_context.get("initial_stop_type")
            or stop_management_defaults["initial_stop_type"]
        ),
        selection_context_summary=_selection_context_summary(resolved_selection_context),
        prompt_family_hint=prompt_family_hint,
    )
