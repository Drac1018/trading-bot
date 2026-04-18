from __future__ import annotations

from typing import Any, Literal

import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from trading_mvp.enums import AgentRole, OperatingMode, PriorityLevel
from trading_mvp.models import (
    AgentRun,
    Alert,
    Position,
    SystemHealthEvent,
)
from trading_mvp.providers import ProviderResult, StructuredModelProvider
from trading_mvp.schemas import (
    AgentRunRecord,
    AIDecisionContextPacket,
    ChiefReviewSummary,
    FeaturePayload,
    MarketSnapshotPayload,
    RiskCheckResult,
    TradeDecision,
)
from trading_mvp.services.adaptive_signal import (
    ADAPTIVE_SETUP_DISABLE_REASON_CODE,
    compute_adaptive_adjustment,
)
from trading_mvp.services.ai_prompt_routing import (
    bound_trade_decision,
    render_prompt_instructions,
    resolve_prompt_route,
)
from trading_mvp.services.holding_profile import (
    HOLDING_PROFILE_POSITION,
    HOLDING_PROFILE_SCALP,
    HOLDING_PROFILE_SWING,
    deterministic_stop_management_payload,
    evaluate_holding_profile,
)
from trading_mvp.services.strategy_engines import select_strategy_engine
from trading_mvp.time_utils import utcnow_naive

IMMEDIATE_ENTRY_ALLOWED_RATIONALE_CODES = {"PENDING_ENTRY_PLAN_TRIGGERED"}
SETUP_CLUSTER_DISABLED_REASON_CODE = "SETUP_CLUSTER_DISABLED"
WINNER_ONLY_ADD_ON_REASON_CODE = "WINNER_ONLY_ADD_ON"
ADD_ON_TREND_CONFIRMED_REASON_CODE = "ADD_ON_TREND_CONFIRMED"
ADD_ON_PROTECTED_STOP_REASON_CODE = "ADD_ON_PROTECTED_STOP"
ADD_ON_REQUIRES_WINNING_POSITION_REASON_CODE = "ADD_ON_REQUIRES_WINNING_POSITION"
ADD_ON_PROTECTIVE_STOP_REQUIRED_REASON_CODE = "ADD_ON_PROTECTIVE_STOP_REQUIRED"
ADD_ON_TREND_ALIGNMENT_REQUIRED_REASON_CODE = "ADD_ON_TREND_ALIGNMENT_REQUIRED"
ADD_ON_BREADTH_VETO_REASON_CODE = "ADD_ON_BREADTH_VETO"
ADD_ON_LEAD_LAG_VETO_REASON_CODE = "ADD_ON_LEAD_LAG_VETO"
ADD_ON_DERIVATIVES_VETO_REASON_CODE = "ADD_ON_DERIVATIVES_VETO"
SETUP_CLUSTER_EXEMPT_RATIONALE_CODES = {
    "PROTECTION_REQUIRED",
    "PROTECTION_RECOVERY",
    "PROTECTION_RESTORE",
}


def _summary_from_output(output: BaseModel | dict[str, Any]) -> str:
    payload = output.model_dump(mode="json") if isinstance(output, BaseModel) else output
    for key in ("summary", "explanation_short", "title", "recommended_mode"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    items = payload.get("items")
    if isinstance(items, list) and items:
        first = items[0]
        if isinstance(first, dict) and "title" in first:
            return str(first["title"])
    return "agent_run"


def persist_agent_run(
    session: Session,
    role: AgentRole,
    trigger_event: str,
    input_payload: dict[str, Any],
    output: BaseModel | dict[str, Any],
    *,
    provider_name: str = "deterministic-mock",
    metadata_json: dict[str, Any] | None = None,
    schema_valid: bool = True,
    status: str = "completed",
) -> AgentRun:
    now = utcnow_naive()
    metadata = metadata_json or {}
    derived_status = status
    source = metadata.get("source")
    gate = metadata.get("gate")
    if status == "completed":
        if source == "llm_fallback":
            derived_status = "fallback"
        elif isinstance(gate, dict) and gate.get("allowed") is False:
            derived_status = "skipped"
    output_payload = output.model_dump(mode="json") if isinstance(output, BaseModel) else output
    row = AgentRun(
        role=role.value,
        trigger_event=trigger_event,
        schema_name=output.__class__.__name__ if isinstance(output, BaseModel) else "dict",
        status=derived_status,
        provider_name=provider_name,
        summary=_summary_from_output(output),
        input_payload=input_payload,
        output_payload=output_payload,
        metadata_json=metadata,
        schema_valid=schema_valid,
        started_at=now,
        completed_at=now,
    )
    session.add(row)
    session.flush()
    return row


def serialize_agent_run(row: AgentRun) -> AgentRunRecord:
    return AgentRunRecord(
        role=row.role,
        trigger_event=row.trigger_event,
        schema_name=row.schema_name,
        status=row.status,
        provider_name=row.provider_name,
        summary=row.summary,
        input_payload=row.input_payload,
        output_payload=row.output_payload,
        metadata_json=row.metadata_json,
        schema_valid=row.schema_valid,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


def _provider_metadata(result: ProviderResult | None, *, source: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {"source": source}
    if result is None:
        return metadata
    if result.usage is not None:
        metadata["usage"] = result.usage
    if result.request_id:
        metadata["request_id"] = result.request_id
    return metadata


def _setup_cluster_scenario(decision: str, entry_mode: str | None, rationale_codes: list[str]) -> str:
    decision_code = str(decision or "").lower()
    if decision_code in {"reduce", "exit", "hold"}:
        return decision_code
    rationale_set = {str(code) for code in rationale_codes if code}
    if rationale_set & SETUP_CLUSTER_EXEMPT_RATIONALE_CODES:
        return "protection_restore"
    if str(entry_mode or "").lower() == "pullback_confirm" or any("PULLBACK" in code for code in rationale_set):
        return "pullback_entry"
    return "trend_follow"


def _setup_cluster_key(
    *,
    symbol: str,
    timeframe: str,
    scenario: str,
    entry_mode: str,
    regime: str,
    trend_alignment: str,
) -> str:
    return f"{symbol.upper()}|{timeframe}|{scenario}|{entry_mode}|{regime}|{trend_alignment}"


def build_trading_decision_input_payload(
    *,
    market_snapshot: MarketSnapshotPayload,
    higher_timeframe_context: dict[str, MarketSnapshotPayload],
    feature_payload: FeaturePayload,
    risk_context: dict[str, Any],
    decision_reference: dict[str, Any],
    ai_trigger: dict[str, Any] | None = None,
    ai_context: AIDecisionContextPacket | dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "market_snapshot": market_snapshot.model_dump(mode="json"),
        "market_context": {
            context_timeframe: snapshot.model_dump(mode="json")
            for context_timeframe, snapshot in higher_timeframe_context.items()
        },
        "features": feature_payload.model_dump(mode="json"),
        "risk_context": risk_context,
        "decision_reference": decision_reference,
    }
    if isinstance(ai_trigger, dict) and ai_trigger:
        payload["ai_trigger"] = dict(ai_trigger)
    if isinstance(ai_context, AIDecisionContextPacket):
        payload["ai_context"] = ai_context.model_dump(mode="json")
    elif isinstance(ai_context, dict) and ai_context:
        payload["ai_context"] = dict(ai_context)
    return payload


class TradingDecisionAgent:
    def __init__(self, provider: StructuredModelProvider) -> None:
        self.provider = provider

    @staticmethod
    def _coerce_ai_context(
        ai_context: AIDecisionContextPacket | dict[str, Any] | None,
    ) -> AIDecisionContextPacket | None:
        if isinstance(ai_context, AIDecisionContextPacket):
            return ai_context
        if isinstance(ai_context, dict) and ai_context:
            try:
                return AIDecisionContextPacket.model_validate(ai_context)
            except Exception:
                return None
        return None

    @staticmethod
    def _serialize_ai_context(
        ai_context: AIDecisionContextPacket | dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if isinstance(ai_context, AIDecisionContextPacket):
            return ai_context.model_dump(mode="json")
        if isinstance(ai_context, dict) and ai_context:
            return dict(ai_context)
        return None

    @staticmethod
    def _provider_status_from_exception(exc: Exception) -> str:
        if isinstance(exc, ValidationError):
            return "schema_invalid"
        if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
            return "timeout"
        if isinstance(exc, httpx.HTTPStatusError):
            status_code = exc.response.status_code if exc.response is not None else 0
            if status_code in {401, 403}:
                return "unavailable"
            if status_code in {429, 500, 502, 503, 504}:
                return "upstream_error"
            return "provider_error"
        return "provider_error"

    @staticmethod
    def _route_metadata(route, *, provider_status: str) -> dict[str, Any]:  # noqa: ANN001
        return {
            "trigger_type": route.trigger_type,
            "strategy_engine_name": route.strategy_engine,
            "prompt_family": route.prompt_family,
            "allowed_actions": list(route.allowed_actions),
            "forbidden_actions": list(route.forbidden_actions),
            "provider_status": provider_status,
        }

    @staticmethod
    def _open_position_side(open_positions: list[Position]) -> str | None:
        if not open_positions:
            return None
        side = str(getattr(open_positions[0], "side", "") or "").strip().lower()
        return side if side in {"long", "short"} else None

    @staticmethod
    def _current_stop_loss(open_positions: list[Position]) -> float | None:
        if not open_positions:
            return None
        stop_loss = getattr(open_positions[0], "stop_loss", None)
        try:
            return float(stop_loss) if stop_loss is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _has_losing_position(open_positions: list[Position]) -> bool:
        for position in open_positions:
            unrealized_pnl = getattr(position, "unrealized_pnl", None)
            try:
                if unrealized_pnl is not None and float(unrealized_pnl) < 0:
                    return True
            except (TypeError, ValueError):
                continue
        return False

    @staticmethod
    def _fail_closed_decision(
        baseline: TradeDecision,
        *,
        ai_context: AIDecisionContextPacket | None,
        provider_status: str,
        fail_reason_code: str,
    ) -> TradeDecision:
        current_profile = (
            str((ai_context.holding_profile if ai_context is not None else None) or baseline.holding_profile or HOLDING_PROFILE_SCALP)
            .strip()
            .lower()
            or HOLDING_PROFILE_SCALP
        )
        fail_codes = list(
            dict.fromkeys(
                [*baseline.rationale_codes, fail_reason_code, "AI_UNAVAILABLE_FAIL_CLOSED"]
            )
        )
        return baseline.model_copy(
            update={
                "decision": "hold",
                "entry_mode": "none",
                "entry_zone_min": None,
                "entry_zone_max": None,
                "invalidation_price": None,
                "max_chase_bps": None,
                "idea_ttl_minutes": None,
                "recommended_holding_profile": "hold_current",
                "holding_profile": current_profile,
                "primary_reason_codes": fail_codes,
                "rationale_codes": fail_codes,
                "no_trade_reason_codes": fail_codes,
                "abstain_reason_codes": list(dict.fromkeys([fail_reason_code, "AI_UNAVAILABLE_FAIL_CLOSED"])),
                "should_abstain": True,
                "bounded_output_applied": True,
                "fallback_reason_codes": list(dict.fromkeys([fail_reason_code, "AI_UNAVAILABLE_FAIL_CLOSED"])),
                "fail_closed_applied": True,
                "provider_status": provider_status,
                "explanation_short": "AI failure blocked a new entry.",
                "explanation_detailed": (
                    "The provider failed, timed out, or returned invalid output on a new-entry review. "
                    "The decision was normalized to hold before risk approval."
                ),
            }
        )

    @staticmethod
    def _adaptive_brackets(
        side: Literal["long", "short"],
        *,
        price: float,
        atr: float,
        features: FeaturePayload,
    ) -> tuple[float, float]:
        safe_price = max(price, 1.0)
        safe_atr = max(atr, safe_price * 0.002)
        stop_multiple = 1.1
        take_multiple = 2.0

        if features.regime.primary_regime == "range":
            stop_multiple *= 0.85
            take_multiple *= 0.75
        elif features.regime.trend_alignment in {"bullish_aligned", "bearish_aligned"}:
            take_multiple *= 1.25

        if features.regime.volatility_regime == "expanded":
            stop_multiple *= 1.15
            take_multiple *= 1.2
        elif features.regime.volatility_regime == "compressed":
            stop_multiple *= 0.9

        if features.regime.weak_volume:
            take_multiple *= 0.85
        if features.regime.momentum_state == "weakening":
            take_multiple *= 0.9
        if features.regime.momentum_state == "overextended":
            stop_multiple *= 0.95
            take_multiple *= 0.8

        if side == "long":
            return round(safe_price - safe_atr * stop_multiple, 2), round(safe_price + safe_atr * take_multiple, 2)
        return round(safe_price + safe_atr * stop_multiple, 2), round(safe_price - safe_atr * take_multiple, 2)

    @staticmethod
    def _confidence(features: FeaturePayload) -> float:
        confidence = 0.22 + min(abs(features.trend_score) / 2.5, 0.32)
        confidence += min(abs(features.momentum_score) / 3.0, 0.18)
        if features.regime.trend_alignment in {"bullish_aligned", "bearish_aligned"}:
            confidence += 0.12
        if features.breakout.range_breakout_direction != "none":
            confidence += 0.05
        if features.pullback_context.aligned_with_higher_timeframe:
            confidence += 0.05
        if features.regime.volume_regime == "strong":
            confidence += 0.05
        if features.regime.primary_regime == "range":
            confidence -= 0.1
        if features.regime.weak_volume:
            confidence -= 0.08
        if features.candle_structure.wick_to_body_ratio > 2.2:
            confidence -= 0.04
        if features.pullback_context.state == "countertrend":
            confidence -= 0.08
        if features.lead_lag.available:
            if features.regime.trend_alignment == "bullish_aligned":
                if features.lead_lag.bullish_alignment_score >= 0.68:
                    confidence += 0.05
                elif features.lead_lag.bullish_alignment_score <= 0.38:
                    confidence -= 0.08
            elif features.regime.trend_alignment == "bearish_aligned":
                if features.lead_lag.bearish_alignment_score >= 0.68:
                    confidence += 0.05
                elif features.lead_lag.bearish_alignment_score <= 0.38:
                    confidence -= 0.08
        if "STALE_MARKET_DATA" in features.data_quality_flags or "INCOMPLETE_MARKET_DATA" in features.data_quality_flags:
            confidence -= 0.2
        return round(min(0.96, max(0.18, confidence)), 4)

    @staticmethod
    def _derivatives_side_context(features: FeaturePayload, side: Literal["long", "short"]) -> dict[str, object]:
        derivatives = features.derivatives
        if not derivatives.available:
            return {
                "available": False,
                "alignment_score": 0.5,
                "crowding_risk": False,
                "top_trader_crowded": False,
                "taker_headwind": False,
                "funding_headwind": False,
                "spread_bps": None,
                "spread_headwind": False,
                "spread_stress": False,
                "breakout_filter_blocking": False,
                "entry_filter_blocking": False,
                "oi_supportive": True,
                "breakout_supportive": True,
                "entry_veto_reason_codes": [],
                "breakout_veto_reason_codes": [],
                "discount_magnitude": 0.0,
            }
        if side == "long":
            funding_headwind = derivatives.funding_bias == "long_headwind"
            crowding_risk = derivatives.crowded_long_risk
            top_trader_crowded = derivatives.top_trader_long_crowded
            taker_headwind = derivatives.taker_flow_alignment == "bearish"
            oi_supportive = derivatives.oi_expanding_with_price and not derivatives.oi_falling_on_breakout
            spread_headwind = derivatives.spread_headwind
            spread_stress = derivatives.spread_stress
            breakout_filter_blocking = (
                (derivatives.breakout_spread_headwind and not oi_supportive)
                or top_trader_crowded
                or spread_stress
                or derivatives.taker_flow_alignment != "bullish"
            )
            entry_filter_blocking = (
                (funding_headwind and spread_headwind)
                or (crowding_risk and spread_headwind)
                or (top_trader_crowded and (spread_stress or taker_headwind))
                or (funding_headwind and spread_stress)
            )
            breakout_supportive = (
                oi_supportive
                and derivatives.taker_flow_alignment == "bullish"
                and not crowding_risk
                and not top_trader_crowded
                and not funding_headwind
                and not spread_stress
                and not derivatives.breakout_spread_headwind
            )
            alignment_score = derivatives.long_alignment_score
            entry_veto_reason_codes = list(derivatives.entry_veto_reason_codes)
            breakout_veto_reason_codes = list(derivatives.breakout_veto_reason_codes)
            discount_magnitude = float(derivatives.long_discount_magnitude)
        else:
            funding_headwind = derivatives.funding_bias == "short_headwind"
            crowding_risk = derivatives.crowded_short_risk
            top_trader_crowded = derivatives.top_trader_short_crowded
            taker_headwind = derivatives.taker_flow_alignment == "bullish"
            oi_supportive = derivatives.oi_expanding_with_price and not derivatives.oi_falling_on_breakout
            spread_headwind = derivatives.spread_headwind
            spread_stress = derivatives.spread_stress
            breakout_filter_blocking = (
                (derivatives.breakout_spread_headwind and not oi_supportive)
                or top_trader_crowded
                or spread_stress
                or derivatives.taker_flow_alignment != "bearish"
            )
            entry_filter_blocking = (
                (funding_headwind and spread_headwind)
                or (crowding_risk and spread_headwind)
                or (top_trader_crowded and (spread_stress or taker_headwind))
                or (funding_headwind and spread_stress)
            )
            breakout_supportive = (
                oi_supportive
                and derivatives.taker_flow_alignment == "bearish"
                and not crowding_risk
                and not top_trader_crowded
                and not funding_headwind
                and not spread_stress
                and not derivatives.breakout_spread_headwind
            )
            alignment_score = derivatives.short_alignment_score
            entry_veto_reason_codes = list(derivatives.entry_veto_reason_codes)
            breakout_veto_reason_codes = list(derivatives.breakout_veto_reason_codes)
            discount_magnitude = float(derivatives.short_discount_magnitude)
        return {
            "available": True,
            "alignment_score": float(alignment_score),
            "crowding_risk": bool(crowding_risk),
            "top_trader_crowded": bool(top_trader_crowded),
            "taker_headwind": bool(taker_headwind),
            "funding_headwind": bool(funding_headwind),
            "spread_bps": derivatives.spread_bps,
            "spread_headwind": bool(spread_headwind),
            "spread_stress": bool(spread_stress),
            "breakout_filter_blocking": bool(breakout_filter_blocking),
            "entry_filter_blocking": bool(entry_filter_blocking),
            "oi_supportive": bool(oi_supportive),
            "breakout_supportive": bool(breakout_supportive),
            "entry_veto_reason_codes": entry_veto_reason_codes,
            "breakout_veto_reason_codes": breakout_veto_reason_codes,
            "discount_magnitude": round(discount_magnitude, 4),
        }

    @staticmethod
    def _lead_lag_side_context(features: FeaturePayload, side: Literal["long", "short"]) -> dict[str, object]:
        lead_lag = features.lead_lag
        if not lead_lag.available:
            return {
                "available": False,
                "alignment_score": 0.5,
                "leader_bias": "unknown",
                "breakout_confirmed": True,
                "breakout_ahead": False,
                "pullback_supported": True,
                "continuation_supported": True,
                "strong_reference_confirmation": False,
            }
        if side == "long":
            return {
                "available": True,
                "alignment_score": float(lead_lag.bullish_alignment_score),
                "leader_bias": lead_lag.leader_bias,
                "breakout_confirmed": bool(lead_lag.bullish_breakout_confirmed),
                "breakout_ahead": bool(lead_lag.bullish_breakout_ahead),
                "pullback_supported": bool(lead_lag.bullish_pullback_supported),
                "continuation_supported": bool(lead_lag.bullish_continuation_supported),
                "strong_reference_confirmation": bool(lead_lag.strong_reference_confirmation),
            }
        return {
            "available": True,
            "alignment_score": float(lead_lag.bearish_alignment_score),
            "leader_bias": lead_lag.leader_bias,
            "breakout_confirmed": bool(lead_lag.bearish_breakout_confirmed),
            "breakout_ahead": bool(lead_lag.bearish_breakout_ahead),
            "pullback_supported": bool(lead_lag.bearish_pullback_supported),
            "continuation_supported": bool(lead_lag.bearish_continuation_supported),
            "strong_reference_confirmation": bool(lead_lag.strong_reference_confirmation),
        }

    @staticmethod
    def _clamp(value: float, minimum: float, maximum: float) -> float:
        return min(maximum, max(minimum, value))

    @staticmethod
    def _risk_budget_context(risk_context: dict[str, Any]) -> dict[str, float]:
        payload = risk_context.get("risk_budget")
        if not isinstance(payload, dict):
            return {}
        return {
            key: float(value)
            for key, value in payload.items()
            if isinstance(value, (int, float))
        }

    @staticmethod
    def _optional_float(value: object) -> float | None:
        try:
            if value in {None, ""}:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _default_payoff_timing(
        *,
        holding_profile: str | None,
        volatility_regime: str | None,
    ) -> tuple[int | None, int | None]:
        profile = str(holding_profile or HOLDING_PROFILE_SCALP).strip().lower()
        base_timings = {
            HOLDING_PROFILE_SCALP: (15, 45),
            HOLDING_PROFILE_SWING: (90, 240),
            HOLDING_PROFILE_POSITION: (240, 720),
        }
        time_to_quarter_r, time_to_half_r = base_timings.get(profile, base_timings[HOLDING_PROFILE_SCALP])
        regime = str(volatility_regime or "normal")
        multiplier = 1.0
        if regime == "calm":
            multiplier = 1.35
        elif regime == "fast":
            multiplier = 0.7
        elif regime == "shock":
            multiplier = 0.5
        return max(int(round(time_to_quarter_r * multiplier)), 1), max(int(round(time_to_half_r * multiplier)), 1)

    @staticmethod
    def _default_expected_mae_r(*, volatility_regime: str | None) -> float:
        regime = str(volatility_regime or "normal")
        if regime == "calm":
            return 0.35
        if regime == "fast":
            return 0.7
        if regime == "shock":
            return 0.9
        return 0.5

    def _apply_ai_schema_fields(
        self,
        decision: TradeDecision,
        *,
        ai_context: AIDecisionContextPacket | None,
    ) -> TradeDecision:
        if ai_context is None:
            return decision
        update: dict[str, Any] = {}
        if decision.prompt_family_hint is None and ai_context.prompt_family_hint is not None:
            update["prompt_family_hint"] = ai_context.prompt_family_hint
        if decision.regime_transition_risk is None:
            update["regime_transition_risk"] = ai_context.composite_regime.transition_risk
        if not decision.data_quality_penalty_applied:
            update["data_quality_penalty_applied"] = ai_context.data_quality.data_quality_grade != "complete"
        if not decision.invalidation_reason_codes and decision.decision in {"long", "short"} and decision.invalidation_price is not None:
            update["invalidation_reason_codes"] = ["INVALIDATION_PRICE_BREACH"]
        if decision.decision in {"long", "short"}:
            payoff_profile = decision.recommended_holding_profile or decision.holding_profile
            quarter_r_minutes, half_r_minutes = self._default_payoff_timing(
                holding_profile=payoff_profile,
                volatility_regime=ai_context.composite_regime.volatility_regime,
            )
            if decision.expected_time_to_0_25r_minutes is None:
                update["expected_time_to_0_25r_minutes"] = quarter_r_minutes
            if decision.expected_time_to_0_5r_minutes is None:
                update["expected_time_to_0_5r_minutes"] = half_r_minutes
            if decision.expected_mae_r is None:
                update["expected_mae_r"] = self._default_expected_mae_r(
                    volatility_regime=ai_context.composite_regime.volatility_regime
                )
        elif decision.decision == "hold" and not decision.should_abstain:
            if ai_context.data_quality.data_quality_grade in {"degraded", "unavailable"} and decision.confidence <= 0.5:
                update["should_abstain"] = True
        if decision.should_abstain and not decision.abstain_reason_codes:
            update["abstain_reason_codes"] = list(decision.no_trade_reason_codes or decision.primary_reason_codes)
        return decision.model_copy(update=update) if update else decision

    @staticmethod
    def _prior_observability_fields(ai_context: AIDecisionContextPacket | None) -> dict[str, Any]:
        if ai_context is None:
            return {
                "engine_prior_classification": None,
                "capital_efficiency_classification": None,
                "session_prior_classification": None,
                "time_of_day_prior_classification": None,
                "prior_penalty_level": "none",
                "prior_reason_codes": [],
                "sample_threshold_satisfied": {},
                "confidence_adjustment_applied": False,
                "abstain_due_to_prior_and_quality": False,
                "expected_payoff_efficiency_hint_summary": {},
            }
        prior_context = ai_context.prior_context
        return {
            "engine_prior_classification": prior_context.engine_prior_classification,
            "capital_efficiency_classification": prior_context.capital_efficiency_classification,
            "session_prior_classification": prior_context.session_prior_classification,
            "time_of_day_prior_classification": prior_context.time_of_day_prior_classification,
            "prior_penalty_level": prior_context.prior_penalty_level,
            "prior_reason_codes": list(prior_context.prior_reason_codes),
            "sample_threshold_satisfied": {
                "engine": prior_context.engine_sample_threshold_satisfied,
                "capital_efficiency": prior_context.capital_efficiency_sample_threshold_satisfied,
                "session": prior_context.session_sample_threshold_satisfied,
                "time_of_day": prior_context.time_of_day_sample_threshold_satisfied,
            },
            "confidence_adjustment_applied": False,
            "abstain_due_to_prior_and_quality": False,
            "expected_payoff_efficiency_hint_summary": dict(prior_context.expected_payoff_efficiency_hint_summary),
        }

    def _apply_prior_soft_adjustments(
        self,
        decision: TradeDecision,
        *,
        ai_context: AIDecisionContextPacket | None,
        route,
    ) -> tuple[TradeDecision, dict[str, Any]]:  # noqa: ANN001
        metadata = self._prior_observability_fields(ai_context)
        if ai_context is None:
            return decision, metadata
        prior_context = ai_context.prior_context
        base_update: dict[str, Any] = {
            "engine_prior_classification": prior_context.engine_prior_classification,
            "capital_efficiency_classification": prior_context.capital_efficiency_classification,
            "session_prior_classification": prior_context.session_prior_classification,
            "time_of_day_prior_classification": prior_context.time_of_day_prior_classification,
            "prior_penalty_level": prior_context.prior_penalty_level,
            "prior_reason_codes": list(prior_context.prior_reason_codes),
            "sample_threshold_satisfied": dict(metadata["sample_threshold_satisfied"]),
            "expected_payoff_efficiency_hint_summary": dict(prior_context.expected_payoff_efficiency_hint_summary),
        }
        if (not route.allow_new_entry) or decision.decision in {"reduce", "exit"}:
            return decision.model_copy(update=base_update), metadata

        recommended_profile = str(decision.recommended_holding_profile or decision.holding_profile or HOLDING_PROFILE_SCALP)
        data_quality_grade = ai_context.data_quality.data_quality_grade
        confidence_delta = 0.0
        abstain_due_to_prior_and_quality = False
        adjustment_codes: list[str] = []
        update = dict(base_update)

        if (
            prior_context.engine_sample_threshold_satisfied
            and prior_context.engine_prior_classification == "strong"
            and data_quality_grade in {"complete", "partial"}
        ):
            confidence_delta += 0.04
            adjustment_codes.append("ENGINE_PRIOR_CONFIDENCE_BOOST")
        if prior_context.engine_sample_threshold_satisfied and prior_context.engine_prior_classification == "weak":
            confidence_delta -= 0.08
            adjustment_codes.append("ENGINE_PRIOR_CONFIDENCE_PENALTY")
        if (
            prior_context.capital_efficiency_sample_threshold_satisfied
            and prior_context.capital_efficiency_classification == "inefficient"
        ):
            confidence_delta -= 0.07 if recommended_profile in {HOLDING_PROFILE_SWING, HOLDING_PROFILE_POSITION} else 0.03
            adjustment_codes.append("CAPITAL_EFFICIENCY_CONSERVATISM")
        if prior_context.session_sample_threshold_satisfied and prior_context.session_prior_classification == "weak":
            confidence_delta -= 0.02
            adjustment_codes.append("SESSION_PRIOR_SOFT_PENALTY")
        if prior_context.time_of_day_sample_threshold_satisfied and prior_context.time_of_day_prior_classification == "weak":
            confidence_delta -= 0.02
            adjustment_codes.append("TIME_OF_DAY_PRIOR_SOFT_PENALTY")

        if (
            recommended_profile in {HOLDING_PROFILE_SWING, HOLDING_PROFILE_POSITION}
            and prior_context.capital_efficiency_sample_threshold_satisfied
            and prior_context.capital_efficiency_classification == "inefficient"
        ):
            update["holding_profile"] = HOLDING_PROFILE_SCALP
            update["recommended_holding_profile"] = HOLDING_PROFILE_SCALP
            adjustment_codes.append("INEFFICIENT_CAPITAL_PROFILE_DOWNGRADE")

        if (
            recommended_profile in {HOLDING_PROFILE_SWING, HOLDING_PROFILE_POSITION}
            and data_quality_grade in {"degraded", "unavailable"}
        ):
            update["holding_profile"] = HOLDING_PROFILE_SCALP
            update["recommended_holding_profile"] = HOLDING_PROFILE_SCALP if data_quality_grade == "degraded" else "hold_current"
            confidence_delta -= 0.1 if data_quality_grade == "degraded" else 0.14
            adjustment_codes.append("LONG_HOLDING_PROFILE_QUALITY_CONSERVATISM")
            if data_quality_grade == "unavailable" and decision.decision in {"long", "short"}:
                abstain_due_to_prior_and_quality = True

        if (
            ai_context.strategy_engine == "breakout_exception_engine"
            and data_quality_grade in {"degraded", "unavailable"}
            and decision.decision in {"long", "short"}
        ):
            update["decision"] = "hold"
            update["entry_mode"] = "none"
            update["entry_zone_min"] = None
            update["entry_zone_max"] = None
            update["recommended_holding_profile"] = "hold_current"
            confidence_delta = min(confidence_delta, -0.14)
            abstain_due_to_prior_and_quality = True
            adjustment_codes.append("BREAKOUT_EXCEPTION_QUALITY_ABSTAIN")

        confidence = self._clamp(decision.confidence + confidence_delta, 0.0, 1.0)
        if confidence != decision.confidence:
            update["confidence"] = round(confidence, 4)
            metadata["confidence_adjustment_applied"] = True
        if abstain_due_to_prior_and_quality:
            combined_no_trade_codes = list(
                dict.fromkeys(
                    [
                        *decision.no_trade_reason_codes,
                        *decision.abstain_reason_codes,
                        "PRIOR_QUALITY_CONSERVATISM",
                        *adjustment_codes,
                    ]
                )
            )
            update["should_abstain"] = True
            update["no_trade_reason_codes"] = combined_no_trade_codes
            update["abstain_reason_codes"] = combined_no_trade_codes
            metadata["abstain_due_to_prior_and_quality"] = True
        update["confidence_adjustment_applied"] = metadata["confidence_adjustment_applied"]
        update["abstain_due_to_prior_and_quality"] = metadata["abstain_due_to_prior_and_quality"]
        update["prior_reason_codes"] = list(dict.fromkeys([*prior_context.prior_reason_codes, *adjustment_codes]))
        metadata["prior_reason_codes"] = list(update["prior_reason_codes"])
        return decision.model_copy(update=update), metadata

    @classmethod
    def _protective_stop_ready_for_add_on(
        cls,
        position: Position,
        position_management_context: dict[str, Any],
    ) -> bool:
        if bool(position_management_context.get("break_even_eligible")):
            return True
        stop_loss = cls._optional_float(position_management_context.get("tightened_stop_loss"))
        if stop_loss is None:
            stop_loss = cls._optional_float(getattr(position, "stop_loss", None))
        entry_price = cls._optional_float(getattr(position, "entry_price", None))
        side = str(getattr(position, "side", "") or "").lower()
        if stop_loss is None or entry_price is None or side not in {"long", "short"}:
            return False
        if side == "long":
            return stop_loss >= entry_price
        return stop_loss <= entry_price

    def _add_on_candidate_context(
        self,
        *,
        decision_side: Literal["long", "short"],
        open_position: Position | None,
        features: FeaturePayload,
        risk_context: dict[str, Any],
        position_management_context: dict[str, Any],
        derivatives_blocking: bool,
        lead_lag_blocking: bool,
    ) -> dict[str, Any]:
        if open_position is None or open_position.side != decision_side:
            return {"candidate": False, "allowed": False, "blocked_reason_codes": [], "allowed_reason_codes": []}
        selection_context = (
            risk_context.get("selection_context")
            if isinstance(risk_context.get("selection_context"), dict)
            else {}
        )
        breadth_summary = (
            selection_context.get("universe_breadth")
            if isinstance(selection_context, dict) and isinstance(selection_context.get("universe_breadth"), dict)
            else {}
        )
        breadth_regime = str(breadth_summary.get("breadth_regime") or "mixed")
        hold_bias_multiplier = self._optional_float(breadth_summary.get("hold_bias_multiplier")) or 1.0
        breadth_veto = breadth_regime in {"weak_breadth", "transition_fragile"} and hold_bias_multiplier > 1.05
        current_r_multiple = self._optional_float(position_management_context.get("current_r_multiple"))
        unrealized_pnl = self._optional_float(getattr(open_position, "unrealized_pnl", None)) or 0.0
        protective_stop_ready = self._protective_stop_ready_for_add_on(open_position, position_management_context)
        target_alignment = "bullish_aligned" if decision_side == "long" else "bearish_aligned"
        trend_alignment_ok = features.regime.trend_alignment == target_alignment
        blocked_reason_codes: list[str] = []
        if unrealized_pnl <= 0 or current_r_multiple is None or current_r_multiple <= 0:
            blocked_reason_codes.append(ADD_ON_REQUIRES_WINNING_POSITION_REASON_CODE)
        if not protective_stop_ready:
            blocked_reason_codes.append(ADD_ON_PROTECTIVE_STOP_REQUIRED_REASON_CODE)
        if not trend_alignment_ok:
            blocked_reason_codes.append(ADD_ON_TREND_ALIGNMENT_REQUIRED_REASON_CODE)
        if breadth_veto:
            blocked_reason_codes.append(ADD_ON_BREADTH_VETO_REASON_CODE)
        if lead_lag_blocking:
            blocked_reason_codes.append(ADD_ON_LEAD_LAG_VETO_REASON_CODE)
        if derivatives_blocking or bool(features.derivatives.spread_headwind):
            blocked_reason_codes.append(ADD_ON_DERIVATIVES_VETO_REASON_CODE)
        return {
            "candidate": True,
            "allowed": len(blocked_reason_codes) == 0,
            "blocked_reason_codes": blocked_reason_codes,
            "allowed_reason_codes": [
                WINNER_ONLY_ADD_ON_REASON_CODE,
                ADD_ON_TREND_CONFIRMED_REASON_CODE,
                ADD_ON_PROTECTED_STOP_REASON_CODE,
            ],
            "current_r_multiple": current_r_multiple,
            "breadth_regime": breadth_regime,
        }

    @staticmethod
    def _minimum_actionable_notional(price: float) -> float:
        return max(25.0, price * 0.0005)

    def _breakout_exception_allowed(
        self,
        features: FeaturePayload,
        side: Literal["long", "short"],
    ) -> bool:
        breakout_up = features.breakout.broke_swing_high or features.breakout.range_breakout_direction == "up"
        breakout_down = features.breakout.broke_swing_low or features.breakout.range_breakout_direction == "down"
        bearish_rejection = (
            features.candle_structure.upper_wick_ratio > max(features.candle_structure.lower_wick_ratio + 0.08, 0.28)
            and features.candle_structure.body_ratio < 0.55
        )
        bullish_rejection = (
            features.candle_structure.lower_wick_ratio > max(features.candle_structure.upper_wick_ratio + 0.08, 0.28)
            and features.candle_structure.body_ratio < 0.55
        )
        if side == "long":
            derivatives = self._derivatives_side_context(features, "long")
            lead_lag = self._lead_lag_side_context(features, "long")
            return bool(
                breakout_up
                and features.regime.trend_alignment == "bullish_aligned"
                and features.regime.primary_regime != "range"
                and features.trend_score >= 0.42
                and features.momentum_score >= 0.26
                and not features.regime.weak_volume
                and not bearish_rejection
                and features.regime.momentum_state == "strengthening"
                and features.volume_persistence.persistence_ratio >= 1.05
                and bool(derivatives.get("breakout_supportive", True))
                and (
                    not bool(lead_lag.get("available"))
                    or (
                        not bool(lead_lag.get("breakout_ahead"))
                        and (
                            bool(lead_lag.get("breakout_confirmed"))
                            or bool(lead_lag.get("strong_reference_confirmation"))
                        )
                    )
                )
            )
        derivatives = self._derivatives_side_context(features, "short")
        lead_lag = self._lead_lag_side_context(features, "short")
        return bool(
            breakout_down
            and features.regime.trend_alignment == "bearish_aligned"
            and features.regime.primary_regime != "range"
            and features.trend_score <= -0.42
            and features.momentum_score <= -0.26
            and not features.regime.weak_volume
            and not bullish_rejection
            and features.regime.momentum_state == "strengthening"
            and features.volume_persistence.persistence_ratio >= 1.05
            and bool(derivatives.get("breakout_supportive", True))
            and (
                not bool(lead_lag.get("available"))
                or (
                    not bool(lead_lag.get("breakout_ahead"))
                    and (
                        bool(lead_lag.get("breakout_confirmed"))
                        or bool(lead_lag.get("strong_reference_confirmation"))
                    )
                )
            )
        )

    def _entry_budget_allows(
        self,
        risk_context: dict[str, Any],
        *,
        side: Literal["long", "short"],
        price: float,
    ) -> bool:
        budget = self._risk_budget_context(risk_context)
        if not budget:
            return True
        side_key = "max_additional_long_notional" if side == "long" else "max_additional_short_notional"
        side_budget = float(budget.get(side_key, 0.0))
        symbol_budget = float(budget.get("max_new_position_notional_for_symbol", 0.0))
        leverage_budget = float(budget.get("max_leverage_for_symbol", 0.0))
        actionable_threshold = self._minimum_actionable_notional(price)
        return side_budget >= actionable_threshold and symbol_budget >= actionable_threshold and leverage_budget >= 1.0

    @staticmethod
    def _timeframe_minutes(timeframe: str) -> int:
        value = timeframe.strip().lower()
        if value.endswith("m"):
            return max(int(value[:-1]), 1)
        if value.endswith("h"):
            return max(int(value[:-1]) * 60, 1)
        if value.endswith("d"):
            return max(int(value[:-1]) * 1440, 1)
        return 15

    def _build_entry_timing_profile(
        self,
        decision: Literal["hold", "long", "short", "reduce", "exit"],
        *,
        market_snapshot: MarketSnapshotPayload,
        features: FeaturePayload,
        entry_mode: str | None,
        holding_profile: str = HOLDING_PROFILE_SCALP,
    ) -> dict[str, Any]:
        if decision not in {"long", "short"}:
            return {
                "profile_name": "non_entry",
                "profile_rationale_code": None,
                "idea_ttl_minutes": None,
                "max_holding_minutes": 240,
                "early_fail_minutes": None,
                "early_fail_r_floor": None,
                "hold_extension_minutes": None,
            }

        timeframe_minutes = self._timeframe_minutes(market_snapshot.timeframe)
        pullback_state = str(features.pullback_context.state or "")

        if entry_mode == "breakout_confirm":
            profile_name = "breakout_fast"
            profile_rationale_code = "SETUP_TIME_PROFILE_BREAKOUT_FAST"
            idea_ttl_minutes = min(max(int(round(timeframe_minutes * 0.8)), 8), 12)
            max_holding_minutes = min(max(int(round(timeframe_minutes * 6.0)), 90), 120)
            early_fail_minutes = min(max(int(round(max_holding_minutes * 0.22)), 18), 30)
            early_fail_r_floor = 0.1
            hold_extension_minutes = min(max(int(round(timeframe_minutes * 0.75)), 8), 15)
        elif pullback_state in {"bullish_continuation", "bearish_continuation"}:
            profile_name = "continuation_balanced"
            profile_rationale_code = "SETUP_TIME_PROFILE_CONTINUATION_BALANCED"
            idea_ttl_minutes = min(max(int(round(timeframe_minutes * 1.0)), 12), 16)
            max_holding_minutes = min(max(int(round(timeframe_minutes * 10.0)), 150), 180)
            early_fail_minutes = min(max(int(round(max_holding_minutes * 0.25)), 30), 45)
            early_fail_r_floor = 0.0
            hold_extension_minutes = min(max(int(round(timeframe_minutes * 1.25)), 15), 25)
        else:
            profile_name = "pullback_flexible"
            profile_rationale_code = "SETUP_TIME_PROFILE_PULLBACK_FLEXIBLE"
            idea_ttl_minutes = min(max(int(round(timeframe_minutes * 1.25)), 15), 20)
            max_holding_minutes = min(max(int(round(timeframe_minutes * 14.0)), 180), 240)
            early_fail_minutes = min(max(int(round(max_holding_minutes * 0.25)), 40), 60)
            early_fail_r_floor = -0.15
            hold_extension_minutes = min(max(int(round(timeframe_minutes * 1.75)), 20), 35)

        if holding_profile == HOLDING_PROFILE_SCALP:
            idea_ttl_minutes = max(min(int(round(idea_ttl_minutes * 0.85)), idea_ttl_minutes), 6)
            max_holding_minutes = max(min(int(round(max_holding_minutes * 0.78)), max_holding_minutes), 60)
            early_fail_minutes = max(min(int(round(early_fail_minutes * 0.82)), early_fail_minutes), 15)
            hold_extension_minutes = max(min(int(round(hold_extension_minutes * 0.7)), hold_extension_minutes), 5)
            profile_name = f"{profile_name}_scalp"
        elif holding_profile == HOLDING_PROFILE_SWING:
            idea_ttl_minutes = max(int(round(idea_ttl_minutes * 1.15)), idea_ttl_minutes)
            max_holding_minutes = max(int(round(max_holding_minutes * 1.6)), max_holding_minutes)
            early_fail_minutes = max(int(round(early_fail_minutes * 1.2)), early_fail_minutes)
            hold_extension_minutes = max(int(round(hold_extension_minutes * 1.2)), hold_extension_minutes)
            profile_name = f"{profile_name}_swing"
        elif holding_profile == HOLDING_PROFILE_POSITION:
            idea_ttl_minutes = max(int(round(idea_ttl_minutes * 1.35)), idea_ttl_minutes)
            max_holding_minutes = max(int(round(max_holding_minutes * 3.2)), max_holding_minutes)
            early_fail_minutes = max(int(round(early_fail_minutes * 1.6)), early_fail_minutes)
            hold_extension_minutes = max(int(round(hold_extension_minutes * 1.6)), hold_extension_minutes)
            profile_name = f"{profile_name}_position"

        return {
            "profile_name": profile_name,
            "profile_rationale_code": profile_rationale_code,
            "idea_ttl_minutes": idea_ttl_minutes,
            "max_holding_minutes": max_holding_minutes,
            "early_fail_minutes": early_fail_minutes,
            "early_fail_r_floor": early_fail_r_floor,
            "hold_extension_minutes": hold_extension_minutes,
        }

    def _build_entry_trigger_defaults(
        self,
        decision: Literal["hold", "long", "short", "reduce", "exit"],
        *,
        market_snapshot: MarketSnapshotPayload,
        features: FeaturePayload,
        stop_loss: float | None,
    ) -> dict[str, Any]:
        if decision not in {"long", "short"}:
            return {
                "entry_mode": "none",
                "invalidation_price": None,
                "max_chase_bps": None,
                "idea_ttl_minutes": None,
            }

        strategy_engine_selection = self._build_strategy_engine_selection_payload(
            market_snapshot=market_snapshot,
            features=features,
            open_positions=[],
            risk_context={},
        )
        selected_engine = (
            dict(strategy_engine_selection.get("selected_engine"))
            if isinstance(strategy_engine_selection.get("selected_engine"), dict)
            else {}
        )
        selected_engine_name = str(selected_engine.get("engine_name") or "")
        entry_mode = "breakout_confirm" if selected_engine_name == "breakout_exception_engine" else "pullback_confirm"

        if entry_mode == "breakout_confirm":
            max_chase_bps = 6.0
        elif entry_mode == "pullback_confirm":
            max_chase_bps = 4.0
        else:
            max_chase_bps = 2.0
        timing_profile = self._build_entry_timing_profile(
            decision,
            market_snapshot=market_snapshot,
            features=features,
            entry_mode=entry_mode,
        )

        return {
            "entry_mode": entry_mode,
            "invalidation_price": stop_loss,
            "max_chase_bps": max_chase_bps,
            "idea_ttl_minutes": timing_profile["idea_ttl_minutes"],
            "max_holding_minutes": timing_profile["max_holding_minutes"],
            "time_profile_name": timing_profile["profile_name"],
            "time_profile_rationale_code": timing_profile["profile_rationale_code"],
        }

    def _build_strategy_engine_selection_payload(
        self,
        *,
        market_snapshot: MarketSnapshotPayload,
        features: FeaturePayload,
        open_positions: list[Position],
        risk_context: dict[str, Any],
    ) -> dict[str, Any]:
        return select_strategy_engine(
            market_snapshot=market_snapshot,
            features=features,
            open_positions=open_positions,
            risk_context=risk_context,
            long_breakout_allowed=self._breakout_exception_allowed(features, "long"),
            short_breakout_allowed=self._breakout_exception_allowed(features, "short"),
        ).to_payload()

    def _normalize_entry_trigger_fields(
        self,
        decision: TradeDecision,
        *,
        market_snapshot: MarketSnapshotPayload,
        features: FeaturePayload,
    ) -> TradeDecision:
        defaults = self._build_entry_trigger_defaults(
            decision.decision,
            market_snapshot=market_snapshot,
            features=features,
            stop_loss=decision.stop_loss,
        )
        if decision.decision not in {"long", "short"}:
            return decision.model_copy(update=defaults)
        normalized_entry_mode = decision.entry_mode or defaults["entry_mode"]
        preserve_immediate_entry = bool(
            normalized_entry_mode == "immediate"
            and set(decision.rationale_codes) & IMMEDIATE_ENTRY_ALLOWED_RATIONALE_CODES
        )
        if normalized_entry_mode == "immediate" and not preserve_immediate_entry:
            normalized_entry_mode = defaults["entry_mode"]
        normalized_timing_profile = self._build_entry_timing_profile(
            decision.decision,
            market_snapshot=market_snapshot,
            features=features,
            entry_mode=normalized_entry_mode,
        )
        normalized_default_max_chase_bps = 6.0 if normalized_entry_mode == "breakout_confirm" else 4.0 if normalized_entry_mode == "pullback_confirm" else 2.0
        normalized_max_chase_bps = (
            decision.max_chase_bps
            if decision.max_chase_bps is not None and preserve_immediate_entry
            else normalized_default_max_chase_bps if decision.entry_mode == "immediate" and not preserve_immediate_entry
            else decision.max_chase_bps if decision.max_chase_bps is not None
            else normalized_default_max_chase_bps
        )
        normalized_idea_ttl_minutes = (
            decision.idea_ttl_minutes
            if decision.idea_ttl_minutes is not None and preserve_immediate_entry
            else normalized_timing_profile["idea_ttl_minutes"] if decision.entry_mode == "immediate" and not preserve_immediate_entry
            else decision.idea_ttl_minutes if decision.idea_ttl_minutes is not None
            else normalized_timing_profile["idea_ttl_minutes"]
        )
        normalized_max_holding_minutes = (
            decision.max_holding_minutes
            if preserve_immediate_entry
            else int(normalized_timing_profile["max_holding_minutes"] or decision.max_holding_minutes)
        )
        normalized_rationale_codes = list(
            dict.fromkeys(decision.rationale_codes + [normalized_timing_profile["profile_rationale_code"]])
        )
        return decision.model_copy(
            update={
                "entry_mode": normalized_entry_mode,
                "invalidation_price": (
                    decision.invalidation_price
                    if decision.invalidation_price is not None
                    else defaults["invalidation_price"]
                ),
                "max_chase_bps": normalized_max_chase_bps,
                "idea_ttl_minutes": normalized_idea_ttl_minutes,
                "max_holding_minutes": normalized_max_holding_minutes,
                "rationale_codes": normalized_rationale_codes,
            }
        )

    def _apply_holding_profile_fields(
        self,
        decision: TradeDecision,
        *,
        market_snapshot: MarketSnapshotPayload,
        features: FeaturePayload,
        risk_context: dict[str, Any],
        strategy_engine_selection: dict[str, Any],
    ) -> tuple[TradeDecision, dict[str, Any]]:
        selection_context = (
            dict(risk_context.get("selection_context"))
            if isinstance(risk_context.get("selection_context"), dict)
            else {}
        )
        selected_engine = (
            dict(strategy_engine_selection.get("selected_engine"))
            if isinstance(strategy_engine_selection.get("selected_engine"), dict)
            else {}
        )
        holding_profile_context = evaluate_holding_profile(
            decision=decision.decision,
            features=features,
            selection_context=selection_context,
            strategy_engine=str(selected_engine.get("engine_name") or ""),
        )
        timing_profile = self._build_entry_timing_profile(
            decision.decision,
            market_snapshot=market_snapshot,
            features=features,
            entry_mode=decision.entry_mode,
            holding_profile=str(holding_profile_context["holding_profile"]),
        )
        normalized_rationale_codes = list(
            dict.fromkeys(
                list(decision.rationale_codes)
                + list(holding_profile_context.get("rationale_codes") or [])
                + [str(timing_profile["profile_rationale_code"] or "")]
            )
        )
        update_payload: dict[str, Any] = {
            "holding_profile": holding_profile_context["holding_profile"],
            "holding_profile_reason": holding_profile_context["holding_profile_reason"],
            "rationale_codes": [code for code in normalized_rationale_codes if code],
        }
        if decision.decision in {"long", "short"}:
            update_payload["idea_ttl_minutes"] = timing_profile["idea_ttl_minutes"]
            update_payload["max_holding_minutes"] = int(timing_profile["max_holding_minutes"] or decision.max_holding_minutes)
        updated_decision = decision.model_copy(update=update_payload)
        updated_decision = self._apply_deterministic_entry_brackets(
            updated_decision,
            market_snapshot=market_snapshot,
            features=features,
        )
        return updated_decision, {
            **holding_profile_context,
            **deterministic_stop_management_payload(hard_stop_active=updated_decision.stop_loss is not None),
        }

    @staticmethod
    def _deterministic_entry_reference_price(
        decision: TradeDecision,
        *,
        market_snapshot: MarketSnapshotPayload,
    ) -> float:
        if decision.entry_zone_min is not None and decision.entry_zone_max is not None:
            return (decision.entry_zone_min + decision.entry_zone_max) / 2
        return market_snapshot.latest_price

    def _apply_deterministic_entry_brackets(
        self,
        decision: TradeDecision,
        *,
        market_snapshot: MarketSnapshotPayload,
        features: FeaturePayload,
    ) -> TradeDecision:
        if decision.decision not in {"long", "short"}:
            return decision
        reference_price = self._deterministic_entry_reference_price(
            decision,
            market_snapshot=market_snapshot,
        )
        deterministic_stop_loss, deterministic_take_profit = self._adaptive_brackets(
            decision.decision,
            price=reference_price,
            atr=features.atr,
            features=features,
        )
        take_profit = decision.take_profit
        if take_profit is None or (
            decision.decision == "long" and take_profit <= reference_price
        ) or (
            decision.decision == "short" and take_profit >= reference_price
        ):
            take_profit = deterministic_take_profit
        normalized_rationale_codes = list(
            dict.fromkeys(list(decision.rationale_codes) + ["DETERMINISTIC_HARD_STOP_ACTIVE"])
        )
        return decision.model_copy(
            update={
                "stop_loss": deterministic_stop_loss,
                "take_profit": take_profit,
                "rationale_codes": normalized_rationale_codes,
            }
        )

    @staticmethod
    def _build_decision_agreement(
        baseline: TradeDecision,
        final_decision: TradeDecision,
        *,
        ai_used: bool,
    ) -> dict[str, Any]:
        baseline_direction = baseline.decision
        final_direction = final_decision.decision
        baseline_entry_mode = baseline.entry_mode or "none"
        final_entry_mode = final_decision.entry_mode or "none"
        direction_match = (
            baseline_direction in {"long", "short"}
            and final_direction in {"long", "short"}
            and baseline_direction == final_direction
        )
        entry_mode_match = direction_match and baseline_entry_mode == final_entry_mode
        if entry_mode_match:
            level = "full_agreement"
        elif direction_match:
            level = "partial_agreement"
        else:
            level = "disagreement"
        return {
            "ai_used": ai_used,
            "comparison_source": (
                "deterministic_baseline_vs_ai_final"
                if ai_used
                else "deterministic_baseline_vs_deterministic_final"
            ),
            "level": level,
            "baseline_decision": baseline_direction,
            "baseline_entry_mode": baseline_entry_mode,
            "final_decision": final_direction,
            "final_entry_mode": final_entry_mode,
            "direction_match": direction_match,
            "entry_mode_match": entry_mode_match,
            "baseline_is_hold": baseline_direction == "hold",
            "final_is_hold": final_direction == "hold",
        }

    @staticmethod
    def _resolve_setup_cluster_state(
        risk_context: dict[str, Any],
        decision: TradeDecision,
    ) -> dict[str, Any]:
        context = risk_context.get("setup_cluster_context")
        if not isinstance(context, dict):
            return {"matched": False, "active": False, "cooldown_active": False, "status": "unavailable", "recovery_trigger": None, "thresholds": {}}
        if decision.decision not in {"long", "short"}:
            return {"matched": False, "active": False, "cooldown_active": False, "status": "not_applicable", "recovery_trigger": None, "thresholds": {}}
        if set(decision.rationale_codes) & SETUP_CLUSTER_EXEMPT_RATIONALE_CODES:
            return {"matched": False, "active": False, "cooldown_active": False, "status": "exempt", "recovery_trigger": None, "thresholds": {}}
        cluster_lookup = context.get("cluster_lookup")
        if not isinstance(cluster_lookup, dict):
            return {"matched": False, "active": False, "cooldown_active": False, "status": "unavailable", "recovery_trigger": None, "thresholds": {}}
        cluster_key = _setup_cluster_key(
            symbol=decision.symbol,
            timeframe=decision.timeframe,
            scenario=_setup_cluster_scenario(
                decision.decision,
                decision.entry_mode,
                decision.rationale_codes,
            ),
            entry_mode=str(decision.entry_mode or "none").lower(),
            regime=str(context.get("regime") or "unknown"),
            trend_alignment=str(context.get("trend_alignment") or "unknown"),
        )
        cluster_state = cluster_lookup.get(cluster_key)
        if not isinstance(cluster_state, dict):
            return {
                "matched": False,
                "active": False,
                "cooldown_active": False,
                "status": "not_matched",
                "recovery_trigger": None,
                "thresholds": {},
                "cluster_key": cluster_key,
                "regime": context.get("regime"),
                "trend_alignment": context.get("trend_alignment"),
            }
        active = bool(cluster_state.get("active", False))
        cooldown_active = bool(cluster_state.get("cooldown_active", active))
        return {
            **cluster_state,
            "matched": True,
            "active": active,
            "cooldown_active": cooldown_active,
            "status": cluster_state.get("status") or ("active_disabled" if cooldown_active else "monitoring"),
            "recovery_trigger": cluster_state.get("recovery_trigger"),
            "thresholds": cluster_state.get("thresholds") if isinstance(cluster_state.get("thresholds"), dict) else {},
        }

    def _apply_adaptive_adjustment(
        self,
        decision: TradeDecision,
        *,
        risk_context: dict[str, Any],
        provider_code: str,
    ) -> tuple[TradeDecision, dict[str, Any]]:
        def _suppression_context(
            *,
            setup_disable: dict[str, Any],
            setup_disable_exempt: bool,
            setup_cluster_state: dict[str, Any],
            hold_bias: float,
            adjusted_confidence: float,
            adjusted_risk_pct: float,
        ) -> dict[str, Any]:
            sources: list[str] = []
            reason_codes: list[str] = []
            level = "none"
            if decision.decision not in {"long", "short"}:
                return {
                    "level": level,
                    "sources": sources,
                    "reason_codes": reason_codes,
                    "applies_hard_block": False,
                    "applies_risk_haircut": False,
                    "applies_soft_bias": False,
                    "hold_bias": round(hold_bias, 4),
                    "confidence_after_adjustment": round(adjusted_confidence, 4),
                    "risk_pct_after_adjustment": round(adjusted_risk_pct, 4),
                }

            if bool(setup_disable.get("active", False)) and not setup_disable_exempt:
                sources.append("adaptive_setup_disable")
                reason_codes.append(ADAPTIVE_SETUP_DISABLE_REASON_CODE)
                level = "hard_block"
            if bool(setup_cluster_state.get("active", False)):
                sources.append("setup_cluster_disable")
                reason_codes.append(SETUP_CLUSTER_DISABLED_REASON_CODE)
                level = "hard_block"
            if level == "none" and hold_bias >= 0.12 and adjusted_confidence <= 0.46:
                sources.append("adaptive_hold_bias")
                reason_codes.extend(["ADAPTIVE_HOLD_BIAS", "ADAPTIVE_SIGNAL_UNDERPERFORMING"])
                level = "soft_bias"
            elif level == "none" and (
                adjusted_confidence < decision.confidence or adjusted_risk_pct < decision.risk_pct
            ):
                sources.append("adaptive_risk_haircut")
                level = "risk_haircut"
            return {
                "level": level,
                "sources": list(dict.fromkeys(sources)),
                "reason_codes": list(dict.fromkeys(reason_codes)),
                "applies_hard_block": level == "hard_block",
                "applies_risk_haircut": level in {"hard_block", "risk_haircut"},
                "applies_soft_bias": level == "soft_bias",
                "hold_bias": round(hold_bias, 4),
                "confidence_after_adjustment": round(adjusted_confidence, 4),
                "risk_pct_after_adjustment": round(adjusted_risk_pct, 4),
            }

        adaptive_context = risk_context.get("adaptive_signal_context")
        adjustment = compute_adaptive_adjustment(
            adaptive_context if isinstance(adaptive_context, dict) else None,
            decision=decision.decision,
            rationale_codes=decision.rationale_codes,
            entry_mode=decision.entry_mode,
        )
        setup_cluster_state = self._resolve_setup_cluster_state(risk_context, decision)
        adjusted_confidence = round(max(decision.confidence, 0.18), 4)
        adjusted_risk_pct = round(max(decision.risk_pct, 0.001), 4)
        hold_bias = 0.0
        setup_disable: dict[str, Any] = {}
        setup_disable_exempt = False
        if not adjustment.get("enabled"):
            suppression_context = _suppression_context(
                setup_disable=setup_disable,
                setup_disable_exempt=setup_disable_exempt,
                setup_cluster_state=setup_cluster_state,
                hold_bias=hold_bias,
                adjusted_confidence=adjusted_confidence,
                adjusted_risk_pct=adjusted_risk_pct,
            )
            rationale_codes = list(dict.fromkeys(decision.rationale_codes + suppression_context["reason_codes"] + [provider_code]))
            return (
                decision.model_copy(
                    update={
                        "rationale_codes": rationale_codes,
                    }
                ),
                {
                    **adjustment,
                    "setup_cluster_state": setup_cluster_state,
                    "suppression_context": suppression_context,
                },
            )

        updated_decision = decision
        adjusted_confidence = round(
            self._clamp(
                decision.confidence * float(adjustment.get("confidence_multiplier", 1.0)),
                0.18,
                0.99,
            ),
            4,
        )
        adjusted_risk_pct = round(
            self._clamp(
                decision.risk_pct * float(adjustment.get("risk_pct_multiplier", 1.0)),
                0.001,
                float(risk_context.get("max_risk_per_trade", decision.risk_pct)),
            ),
            4,
        )
        hold_bias = float(adjustment.get("hold_bias", 0.0))
        rationale_codes = list(dict.fromkeys(decision.rationale_codes))
        setup_disable = adjustment.get("setup_disable") if isinstance(adjustment.get("setup_disable"), dict) else {}
        setup_disable_exempt = bool(set(rationale_codes) & {"PROTECTION_REQUIRED", "PROTECTION_RECOVERY", "PROTECTION_RESTORE"})
        suppression_context = _suppression_context(
            setup_disable=setup_disable,
            setup_disable_exempt=setup_disable_exempt,
            setup_cluster_state=setup_cluster_state,
            hold_bias=hold_bias,
            adjusted_confidence=adjusted_confidence,
            adjusted_risk_pct=adjusted_risk_pct,
        )

        if decision.decision in {"long", "short"} and suppression_context["applies_hard_block"]:
            rationale_codes.extend(suppression_context["reason_codes"])
            updated_decision = decision.model_copy(
                update={
                    "confidence": adjusted_confidence,
                    "risk_pct": adjusted_risk_pct,
                    "rationale_codes": rationale_codes,
                    "explanation_short": "최근 저성과 suppression 이 active여서 신규 진입은 risk gate에서 차단될 가능성이 높습니다.",
                    "explanation_detailed": (
                        f"Recent performance suppression is active with sources={suppression_context['sources']} "
                        f"and reasons={suppression_context['reason_codes']}. "
                        "의도는 유지하되 최종 허용/차단은 risk guard가 담당합니다."
                    ),
                }
            )
        elif decision.decision in {"long", "short"} and suppression_context["applies_soft_bias"]:
            rationale_codes.extend(suppression_context["reason_codes"])
            updated_decision = decision.model_copy(
                update={
                    "confidence": adjusted_confidence,
                    "risk_pct": adjusted_risk_pct,
                    "rationale_codes": rationale_codes,
                    "explanation_short": "최근 실거래 성과 약화로 신규 진입을 더 보수적으로 다룹니다.",
                    "explanation_detailed": (
                        "The adaptive layer detected recent underperformance for this setup. "
                        "신규 진입 의도는 유지하되 confidence와 risk_pct를 낮춰 보수적으로 넘깁니다."
                    ),
                }
            )
        else:
            if float(adjustment.get("signal_weight", 1.0)) < 1.0:
                rationale_codes.append("ADAPTIVE_SIGNAL_WEIGHT_REDUCED")
            if adjusted_confidence < decision.confidence:
                rationale_codes.append("ADAPTIVE_CONFIDENCE_DISCOUNT")
            if adjusted_risk_pct < decision.risk_pct:
                rationale_codes.append("ADAPTIVE_RISK_REDUCED")
            updated_decision = decision.model_copy(
                update={
                    "confidence": adjusted_confidence,
                    "risk_pct": adjusted_risk_pct,
                    "rationale_codes": rationale_codes,
                }
            )

        updated_decision = updated_decision.model_copy(
            update={
                "rationale_codes": list(dict.fromkeys(updated_decision.rationale_codes + [provider_code])),
            }
        )
        adjustment["setup_cluster_state"] = setup_cluster_state
        adjustment["suppression_context"] = suppression_context
        return updated_decision, adjustment

    def _deterministic_decision_improved(
        self,
        market_snapshot: MarketSnapshotPayload,
        features: FeaturePayload,
        open_positions: list[Position],
        risk_context: dict[str, Any],
    ) -> TradeDecision:
        price = market_snapshot.latest_price
        atr = max(features.atr, price * 0.0025)
        confidence = self._confidence(features)
        open_position = open_positions[0] if open_positions else None

        decision: Literal["hold", "long", "short", "reduce", "exit"] = "hold"
        rationale = ["NO_EDGE"]
        short_explanation = "추세 우위가 충분하지 않아 관망이 우선입니다."
        detailed_explanation = (
            "현재 신호는 중립에 가깝고 리스크 대비 기대수익이 제한적이라 신규 진입보다 "
            "다음 평가 사이클까지 모니터링이 안전합니다."
        )

        regime_name = features.regime.primary_regime
        trend_alignment = features.regime.trend_alignment
        weak_volume = features.regime.weak_volume
        momentum_weakening = features.regime.momentum_weakening
        bullish_pullback = features.pullback_context.state == "bullish_pullback"
        bearish_pullback = features.pullback_context.state == "bearish_pullback"
        bullish_continuation = features.pullback_context.state == "bullish_continuation"
        bearish_continuation = features.pullback_context.state == "bearish_continuation"
        long_derivatives = self._derivatives_side_context(features, "long")
        short_derivatives = self._derivatives_side_context(features, "short")
        long_lead_lag = self._lead_lag_side_context(features, "long")
        short_lead_lag = self._lead_lag_side_context(features, "short")
        countertrend = features.pullback_context.state == "countertrend"
        bearish_rejection = (
            features.candle_structure.upper_wick_ratio > max(features.candle_structure.lower_wick_ratio + 0.08, 0.28)
            and features.candle_structure.body_ratio < 0.55
        )
        bullish_rejection = (
            features.candle_structure.lower_wick_ratio > max(features.candle_structure.upper_wick_ratio + 0.08, 0.28)
            and features.candle_structure.body_ratio < 0.55
        )
        range_like_signal = regime_name == "range"
        strong_bullish_breakout_exception = self._breakout_exception_allowed(features, "long")
        strong_bearish_breakout_exception = self._breakout_exception_allowed(features, "short")
        pullback_long_signal = (
            trend_alignment == "bullish_aligned"
            and regime_name != "range"
            and features.trend_score >= 0.2
            and features.momentum_score >= 0.1
            and 42.0 <= features.rsi <= 78.0
            and not weak_volume
            and not bearish_rejection
            and bullish_pullback
            and features.location.vwap_distance_pct >= -0.65
            and features.location.vwap_distance_pct <= 0.15
            and not countertrend
        )
        continuation_long_signal = (
            trend_alignment == "bullish_aligned"
            and regime_name != "range"
            and features.trend_score >= 0.2
            and features.momentum_score >= 0.1
            and 48.0 <= features.rsi <= 88.0
            and not weak_volume
            and not bearish_rejection
            and bullish_continuation
            and features.location.vwap_distance_pct >= -0.85
            and features.location.vwap_distance_pct <= 6.0
            and features.regime.momentum_state in {"strengthening", "stable", "overextended"}
            and features.volume_persistence.persistence_ratio >= 0.95
            and not countertrend
        )
        pullback_short_signal = (
            trend_alignment == "bearish_aligned"
            and regime_name != "range"
            and features.trend_score <= -0.2
            and features.momentum_score <= -0.1
            and 22.0 <= features.rsi <= 58.0
            and not weak_volume
            and not bullish_rejection
            and bearish_pullback
            and features.location.vwap_distance_pct <= 0.65
            and features.location.vwap_distance_pct >= -0.15
            and not countertrend
        )
        continuation_short_signal = (
            trend_alignment == "bearish_aligned"
            and regime_name != "range"
            and features.trend_score <= -0.2
            and features.momentum_score <= -0.1
            and 12.0 <= features.rsi <= 52.0
            and not weak_volume
            and not bullish_rejection
            and bearish_continuation
            and features.location.vwap_distance_pct <= 0.85
            and features.location.vwap_distance_pct >= -6.0
            and features.regime.momentum_state in {"strengthening", "stable", "overextended"}
            and features.volume_persistence.persistence_ratio >= 0.95
            and not countertrend
        )
        long_signal = pullback_long_signal or continuation_long_signal or strong_bullish_breakout_exception
        short_signal = pullback_short_signal or continuation_short_signal or strong_bearish_breakout_exception
        long_breakout_like = strong_bullish_breakout_exception or (
            features.breakout.broke_swing_high or features.breakout.range_breakout_direction == "up"
        )
        short_breakout_like = strong_bearish_breakout_exception or (
            features.breakout.broke_swing_low or features.breakout.range_breakout_direction == "down"
        )
        long_lead_lag_blocking = bool(
            bool(long_lead_lag.get("available"))
            and (
                float(long_lead_lag.get("alignment_score", 0.5)) <= 0.32
                or (
                    long_breakout_like
                    and bool(long_lead_lag.get("breakout_ahead"))
                    and not bool(long_lead_lag.get("breakout_confirmed"))
                )
            )
        )
        short_lead_lag_blocking = bool(
            bool(short_lead_lag.get("available"))
            and (
                float(short_lead_lag.get("alignment_score", 0.5)) <= 0.32
                or (
                    short_breakout_like
                    and bool(short_lead_lag.get("breakout_ahead"))
                    and not bool(short_lead_lag.get("breakout_confirmed"))
                )
            )
        )
        long_derivatives_blocking = bool(
            bool(long_derivatives.get("entry_filter_blocking"))
            or (
                bool(long_derivatives.get("crowding_risk"))
                and bool(long_derivatives.get("taker_headwind"))
            )
            or (
                bool(long_derivatives.get("funding_headwind"))
                and bool(long_derivatives.get("taker_headwind"))
            )
            or (
                long_breakout_like
                and bool(long_derivatives.get("breakout_filter_blocking"))
            )
            or float(long_derivatives.get("alignment_score", 0.5)) <= 0.28
        )
        short_derivatives_blocking = bool(
            bool(short_derivatives.get("entry_filter_blocking"))
            or (
                bool(short_derivatives.get("crowding_risk"))
                and bool(short_derivatives.get("taker_headwind"))
            )
            or (
                bool(short_derivatives.get("funding_headwind"))
                and bool(short_derivatives.get("taker_headwind"))
            )
            or (
                short_breakout_like
                and bool(short_derivatives.get("breakout_filter_blocking"))
            )
            or float(short_derivatives.get("alignment_score", 0.5)) <= 0.28
        )
        weakening_signal = momentum_weakening or weak_volume or range_like_signal
        operating_state = str(risk_context.get("operating_state", "TRADABLE"))
        position_management_context = (
            risk_context.get("position_management_context")
            if isinstance(risk_context.get("position_management_context"), dict)
            else {}
        )
        partial_take_profit_ready = bool(position_management_context.get("partial_take_profit_ready"))
        management_reduce_reasons = [
            str(item)
            for item in position_management_context.get("reduce_reason_codes", [])
            if item not in {None, ""}
        ]
        current_r_multiple = position_management_context.get("current_r_multiple")
        long_budget_available = self._entry_budget_allows(risk_context, side="long", price=price)
        short_budget_available = self._entry_budget_allows(risk_context, side="short", price=price)
        long_add_on_context = self._add_on_candidate_context(
            decision_side="long",
            open_position=open_position,
            features=features,
            risk_context=risk_context,
            position_management_context=position_management_context,
            derivatives_blocking=long_derivatives_blocking,
            lead_lag_blocking=long_lead_lag_blocking,
        )
        short_add_on_context = self._add_on_candidate_context(
            decision_side="short",
            open_position=open_position,
            features=features,
            risk_context=risk_context,
            position_management_context=position_management_context,
            derivatives_blocking=short_derivatives_blocking,
            lead_lag_blocking=short_lead_lag_blocking,
        )

        if open_position is not None and operating_state == "PROTECTION_REQUIRED":
            decision = "long" if open_position.side == "long" else "short"
            rationale = ["PROTECTION_REQUIRED", "RESTORE_PROTECTION"]
            short_explanation = "누락된 보호 주문을 복구할 수 있도록 손절가와 익절가를 다시 제안합니다."
            detailed_explanation = (
                "현재는 신규 진입보다 기존 포지션 보호 복구가 우선입니다. "
                "기존 포지션 방향을 유지한 채 손절가와 익절가를 다시 설정하도록 보수적으로 판단합니다."
            )
        elif open_position is not None and operating_state == "DEGRADED_MANAGE_ONLY":
            decision = "reduce"
            rationale = ["MANAGE_ONLY_MODE", "REDUCE_EXPOSURE"]
            short_explanation = "관리 전용 상태이므로 기존 포지션을 일부 축소하는 판단을 우선합니다."
            detailed_explanation = (
                "보호 복구가 반복 실패해 신규 진입은 막힌 상태입니다. "
                "현재는 노출을 줄이고 남은 포지션만 보수적으로 관리하는 것이 우선입니다."
            )

        if decision == "hold" and open_position is not None and open_position.side == "long" and (short_signal or features.rsi > 73):
            decision = "exit"
            rationale = ["LONG_EXHAUSTION", "POSITION_RISK_RESET"]
            short_explanation = "기존 롱 포지션의 우위가 약해져 청산이 우선입니다."
            detailed_explanation = "과열 또는 반전 신호가 확인돼 기존 롱 포지션을 정리하는 편이 보수적입니다."
        elif decision == "hold" and open_position is not None and open_position.side == "short" and (long_signal or features.rsi < 28):
            decision = "exit"
            rationale = ["SHORT_EXHAUSTION", "POSITION_RISK_RESET"]
            short_explanation = "기존 숏 포지션의 우위가 약해져 청산이 우선입니다."
            detailed_explanation = "반등 또는 추세 전환 가능성이 커져 기존 숏 포지션을 정리하는 편이 보수적입니다."
        elif decision == "hold" and open_position is not None and partial_take_profit_ready:
            decision = "reduce"
            rationale = ["POSITION_MANAGEMENT_PARTIAL_TAKE_PROFIT", "POSITION_MANAGEMENT_LOCK_PARTIAL_PROFIT"]
            short_explanation = "수익 구간이 충분해 일부 익절로 이익 보호를 우선합니다."
            detailed_explanation = (
                "초기 위험 대비 충분한 수익 구간에 진입해 일부 익절로 변동성을 낮추고 남은 포지션만 관리합니다. "
                f"현재 추정 R 배수는 {current_r_multiple if current_r_multiple is not None else 'n/a'}입니다."
            )
        elif decision == "hold" and open_position is not None and management_reduce_reasons:
            decision = "reduce"
            rationale = management_reduce_reasons
            short_explanation = "보유 우위가 약해져 노출 축소가 신규 판단보다 우선입니다."
            detailed_explanation = (
                "보유 시간 경과, 레짐 전환, 또는 모멘텀 약화가 감지돼 남은 기대값이 낮아졌습니다. "
                "보호 방향 우선 원칙에 따라 포지션을 일부 줄여 수익 보호와 손실 제한을 강화합니다."
            )
        elif decision == "hold" and open_position is not None and weakening_signal:
            decision = "reduce"
            rationale = ["WEAKENING_SIGNAL", "PROTECT_OPEN_PNL"]
            short_explanation = "기존 포지션을 일부 축소해 리스크를 낮춥니다."
            detailed_explanation = "추세 강도와 거래량 우위가 약화돼 포지션 규모를 줄이는 편이 안전합니다."
        elif decision == "hold" and bool(long_add_on_context.get("candidate")) and not bool(long_add_on_context.get("allowed")):
            rationale = list(long_add_on_context.get("blocked_reason_codes") or [])
            short_explanation = "winner-only long add-on conditions are not met yet."
            detailed_explanation = (
                "The existing long is not protected well enough for a same-side add-on. "
                "The system keeps the add-on on hold until the position stays profitable and protected."
            )
        elif decision == "hold" and bool(short_add_on_context.get("candidate")) and not bool(short_add_on_context.get("allowed")):
            rationale = list(short_add_on_context.get("blocked_reason_codes") or [])
            short_explanation = "winner-only short add-on conditions are not met yet."
            detailed_explanation = (
                "The existing short is not protected well enough for a same-side add-on. "
                "The system keeps the add-on on hold until the position stays profitable and protected."
            )
        elif decision == "hold" and long_breakout_like and bool(long_derivatives.get("breakout_filter_blocking")):
            rationale = [
                "DERIVATIVES_ALIGNMENT_HEADWIND",
                "BREAKOUT_OI_SPREAD_FILTER",
                "BREAKOUT_OI_NOT_EXPANDING",
            ]
            if bool(long_derivatives.get("spread_headwind")):
                rationale.append("SPREAD_HEADWIND")
            if bool(long_derivatives.get("top_trader_crowded")):
                rationale.append("TOP_TRADER_LONG_CROWDED")
            if bool(long_derivatives.get("spread_stress")):
                rationale.append("SPREAD_STRESS")
            short_explanation = "濡?諛⑺뼢 ?뚯깮?쒖옣 而ㅼ꽌媛 ?섑솕?섏뼱 踰뚮젅?댄겕?꾩썐 ?덈즺瑜??좎낮??蹂대쪟?⑸땲??"
            detailed_explanation = (
                "遺덈젅?댄겕?꾩썐 紐⑥뼇? 蹂댁씠吏留??ㅻ컮?대줈 OI ?뺤옣???뺤씤?섏? 紐삵뻽怨??ㅽ봽?덈뱶瑜??뚯븳 議곌굔?덈룄 蹂댁닔?곸엯?덈떎. "
                "?뚯깮?쒖옣 ?뺥빀?깆씠 媛쒖꽑?섎뒗吏 ?붾㈃??吏꾩엯??蹂대쪟?⑸땲??"
            )
        elif decision == "hold" and long_signal and long_derivatives_blocking:
            rationale = ["DERIVATIVES_ALIGNMENT_HEADWIND"]
            if bool(long_derivatives.get("crowding_risk")):
                rationale.append("CROWDED_LONG_RISK")
            if bool(long_derivatives.get("top_trader_crowded")):
                rationale.append("TOP_TRADER_LONG_CROWDED")
            if bool(long_derivatives.get("taker_headwind")):
                rationale.append("TAKER_FLOW_DIVERGENCE")
            if bool(long_derivatives.get("funding_headwind")):
                rationale.append("FUNDING_HEADWIND")
            if bool(long_derivatives.get("spread_headwind")):
                rationale.append("SPREAD_HEADWIND")
            if bool(long_derivatives.get("spread_stress")):
                rationale.append("SPREAD_STRESS")
            if bool(long_derivatives.get("breakout_filter_blocking")):
                rationale.extend(["BREAKOUT_OI_SPREAD_FILTER", "BREAKOUT_OI_NOT_EXPANDING"])
            short_explanation = "롱 방향 차트 모양은 보이지만 파생시장 역풍이 커서 HOLD가 우선입니다."
            detailed_explanation = (
                "현재 롱 후보는 crowding, funding, taker flow 중 하나 이상이 역풍으로 작동하고 있습니다. "
                "차트만 보고 진입하기보다 파생시장 정합성이 회복될 때까지 대기합니다."
            )
        elif decision == "hold" and short_breakout_like and bool(short_derivatives.get("breakout_filter_blocking")):
            rationale = [
                "DERIVATIVES_ALIGNMENT_HEADWIND",
                "BREAKOUT_OI_SPREAD_FILTER",
                "BREAKOUT_OI_NOT_EXPANDING",
            ]
            if bool(short_derivatives.get("spread_headwind")):
                rationale.append("SPREAD_HEADWIND")
            if bool(short_derivatives.get("top_trader_crowded")):
                rationale.append("TOP_TRADER_SHORT_CROWDED")
            if bool(short_derivatives.get("spread_stress")):
                rationale.append("SPREAD_STRESS")
            short_explanation = "??諛⑺뼢 ?뚯깮?쒖옣 而ㅼ꽌媛 ?섑솕?섏뼱 踰뚮젅?댄겕?꾩썐 ?덈즺瑜??좎낮??蹂대쪟?⑸땲??"
            detailed_explanation = (
                "遺덈젅?댄겕?꾩썐 紐⑥뼇? 蹂댁씠吏留??ㅻ컮?대줈 OI ?뺤옣???뺤씤?섏? 紐삵뻽怨??ㅽ봽?덈뱶瑜??뚯븳 議곌굔?덈룄 蹂댁닔?곸엯?덈떎. "
                "?뚯깮?쒖옣 ?뺥빀?깆씠 媛쒖꽑?섎뒗吏 ?붾㈃??吏꾩엯??蹂대쪟?⑸땲??"
            )
        elif decision == "hold" and short_signal and short_derivatives_blocking:
            rationale = ["DERIVATIVES_ALIGNMENT_HEADWIND"]
            if bool(short_derivatives.get("crowding_risk")):
                rationale.append("CROWDED_SHORT_RISK")
            if bool(short_derivatives.get("top_trader_crowded")):
                rationale.append("TOP_TRADER_SHORT_CROWDED")
            if bool(short_derivatives.get("taker_headwind")):
                rationale.append("TAKER_FLOW_DIVERGENCE")
            if bool(short_derivatives.get("funding_headwind")):
                rationale.append("FUNDING_HEADWIND")
            if bool(short_derivatives.get("spread_headwind")):
                rationale.append("SPREAD_HEADWIND")
            if bool(short_derivatives.get("spread_stress")):
                rationale.append("SPREAD_STRESS")
            if bool(short_derivatives.get("breakout_filter_blocking")):
                rationale.extend(["BREAKOUT_OI_SPREAD_FILTER", "BREAKOUT_OI_NOT_EXPANDING"])
            short_explanation = "숏 방향 차트 모양은 보이지만 파생시장 역풍이 커서 HOLD가 우선입니다."
            detailed_explanation = (
                "현재 숏 후보는 crowding, funding, taker flow 중 하나 이상이 역풍으로 작동하고 있습니다. "
                "실제 밀릴 확률이 더 높아질 때까지 신규 진입을 보류합니다."
            )
        elif decision == "hold" and long_signal and long_lead_lag_blocking:
            rationale = ["LEAD_MARKET_DIVERGENCE"]
            if bool(long_lead_lag.get("breakout_ahead")):
                rationale.append("ALT_BREAKOUT_AHEAD_OF_LEADS")
            short_explanation = "BTC/ETH lead structure is not confirmed yet, so the alt long stays on hold."
            detailed_explanation = (
                "The alt setup looks constructive on its own, but BTC/ETH are not aligned strongly enough yet. "
                "The system keeps the long on hold until the lead market confirms the same structure."
            )
        elif decision == "hold" and short_signal and short_lead_lag_blocking:
            rationale = ["LEAD_MARKET_DIVERGENCE"]
            if bool(short_lead_lag.get("breakout_ahead")):
                rationale.append("ALT_BREAKOUT_AHEAD_OF_LEADS")
            short_explanation = "BTC/ETH lead structure is not confirmed yet, so the alt short stays on hold."
            detailed_explanation = (
                "The alt short setup is moving ahead of BTC/ETH confirmation. "
                "The system waits until the lead market aligns before allowing the trade idea."
            )
        elif decision == "hold" and long_signal and not long_budget_available:
            rationale = ["RISK_BUDGET_EXHAUSTED", "HOLD_ON_LONG_BUDGET_LIMIT"]
            short_explanation = "남은 롱 리스크 예산이 부족해 신규 진입보다 HOLD가 우선입니다."
            detailed_explanation = (
                "현재 계좌 노출과 심볼별 여유를 기준으로 보면 추가 롱 진입 예산이 거의 없습니다. "
                "허용 예산을 넘기지 않기 위해 이번 사이클은 HOLD로 유지합니다."
            )
        elif decision == "hold" and short_signal and not short_budget_available:
            rationale = ["RISK_BUDGET_EXHAUSTED", "HOLD_ON_SHORT_BUDGET_LIMIT"]
            short_explanation = "남은 숏 리스크 예산이 부족해 신규 진입보다 HOLD가 우선입니다."
            detailed_explanation = (
                "현재 계좌 노출과 심볼별 여유를 기준으로 보면 추가 숏 진입 예산이 거의 없습니다. "
                "허용 예산을 넘기지 않기 위해 이번 사이클은 HOLD로 유지합니다."
            )
        elif decision == "hold" and long_signal:
            decision = "long"
            rationale = ["TREND_UP", "PULLBACK_ENTRY_BIAS", "RSI_HEALTHY"]
            if bullish_pullback:
                rationale.append("ALIGNED_PULLBACK")
            elif bullish_continuation:
                rationale.append("BULLISH_CONTINUATION_PULLBACK")
            elif strong_bullish_breakout_exception:
                rationale.append("STRUCTURE_BREAKOUT_UP_EXCEPTION")
            short_explanation = "상승 추세와 거래량 지지가 확인돼 롱 진입을 제안합니다."
            detailed_explanation = (
                "상위 추세 정렬 안에서 눌림 매수 구간을 기다리는 편이 추격 진입보다 보수적입니다. "
                "현재는 즉시 추격보다 되돌림 확인 후 진입하는 시나리오가 우선입니다."
            )
        elif decision == "hold" and short_signal:
            decision = "short"
            rationale = ["TREND_DOWN", "PULLBACK_ENTRY_BIAS", "RSI_WEAK"]
            if bearish_pullback:
                rationale.append("ALIGNED_PULLBACK")
            elif bearish_continuation:
                rationale.append("BEARISH_CONTINUATION_REBOUND")
            elif strong_bearish_breakout_exception:
                rationale.append("STRUCTURE_BREAKOUT_DOWN_EXCEPTION")
            short_explanation = "하락 추세가 우세해 숏 진입을 제안합니다."
            detailed_explanation = (
                "하락 추세 안에서 반등 매도 구간을 기다리는 편이 추격 숏보다 안전합니다. "
                "현재는 1분 확인이 붙는 되돌림 진입 시나리오를 우선합니다."
            )

        if decision == "long" and bool(long_add_on_context.get("candidate")):
            rationale.extend([str(code) for code in long_add_on_context.get("allowed_reason_codes") or []])
            short_explanation = "winner-only add-on is allowed for the protected long."
            detailed_explanation = (
                "The existing long is already profitable and protected, so the system allows a same-side add-on "
                "instead of averaging down."
            )
        elif decision == "short" and bool(short_add_on_context.get("candidate")):
            rationale.extend([str(code) for code in short_add_on_context.get("allowed_reason_codes") or []])
            short_explanation = "winner-only add-on is allowed for the protected short."
            detailed_explanation = (
                "The existing short is already profitable and protected, so the system allows a same-side add-on "
                "instead of averaging down."
            )

        if decision == "long" and bool(long_derivatives.get("available")):
            derivatives_discount = float(long_derivatives.get("discount_magnitude", 0.0))
            if bool(long_derivatives.get("crowding_risk")):
                rationale.append("CROWDED_LONG_RISK")
            if bool(long_derivatives.get("top_trader_crowded")):
                rationale.append("TOP_TRADER_LONG_CROWDED")
            if bool(long_derivatives.get("taker_headwind")):
                rationale.append("TAKER_FLOW_DIVERGENCE")
            if bool(long_derivatives.get("funding_headwind")):
                rationale.append("FUNDING_HEADWIND")
            if bool(long_derivatives.get("spread_headwind")):
                rationale.append("SPREAD_HEADWIND")
            if bool(long_derivatives.get("spread_stress")):
                rationale.append("SPREAD_STRESS")
            if derivatives_discount > 0:
                confidence = self._clamp(confidence - derivatives_discount, 0.18, 0.96)
                rationale.append("DERIVATIVES_CONFIDENCE_DISCOUNT")
                short_explanation = "추세는 유효하지만 파생시장 역풍 때문에 보수적으로 롱 진입합니다."
                detailed_explanation = (
                    "차트 구조는 진입 조건을 만족했지만 crowding, funding, taker flow 중 일부가 역풍입니다. "
                    "진입 자체는 유지하되 confidence와 이후 risk sizing은 더 보수적으로 둡니다."
                )
        if decision == "long" and bool(long_lead_lag.get("available")):
            alignment_score = float(long_lead_lag.get("alignment_score", 0.5))
            if alignment_score >= 0.72:
                confidence = round(self._clamp(confidence * 1.05, 0.18, 0.99), 4)
                rationale.append("LEAD_MARKETS_ALIGNED")
            elif alignment_score <= 0.42:
                confidence = round(self._clamp(confidence * 0.88, 0.18, 0.99), 4)
                rationale.append("LEAD_MARKET_CONFIDENCE_DISCOUNT")
        elif decision == "short" and bool(short_derivatives.get("available")):
            derivatives_discount = float(short_derivatives.get("discount_magnitude", 0.0))
            if bool(short_derivatives.get("crowding_risk")):
                rationale.append("CROWDED_SHORT_RISK")
            if bool(short_derivatives.get("top_trader_crowded")):
                rationale.append("TOP_TRADER_SHORT_CROWDED")
            if bool(short_derivatives.get("taker_headwind")):
                rationale.append("TAKER_FLOW_DIVERGENCE")
            if bool(short_derivatives.get("funding_headwind")):
                rationale.append("FUNDING_HEADWIND")
            if bool(short_derivatives.get("spread_headwind")):
                rationale.append("SPREAD_HEADWIND")
            if bool(short_derivatives.get("spread_stress")):
                rationale.append("SPREAD_STRESS")
            if derivatives_discount > 0:
                confidence = self._clamp(confidence - derivatives_discount, 0.18, 0.96)
                rationale.append("DERIVATIVES_CONFIDENCE_DISCOUNT")
                short_explanation = "하락 구조는 유효하지만 파생시장 역풍 때문에 보수적으로 숏 진입합니다."
                detailed_explanation = (
                    "차트 구조는 진입 조건을 만족했지만 crowding, funding, taker flow 중 일부가 역풍입니다. "
                    "진입 자체는 유지하되 confidence와 이후 risk sizing은 더 보수적으로 둡니다."
                )

        if decision == "short" and bool(short_lead_lag.get("available")):
            alignment_score = float(short_lead_lag.get("alignment_score", 0.5))
            if alignment_score >= 0.72:
                confidence = round(self._clamp(confidence * 1.05, 0.18, 0.99), 4)
                rationale.append("LEAD_MARKETS_ALIGNED")
            elif alignment_score <= 0.42:
                confidence = round(self._clamp(confidence * 0.88, 0.18, 0.99), 4)
                rationale.append("LEAD_MARKET_CONFIDENCE_DISCOUNT")

        risk_pct = max(0.003, round(confidence * 0.008, 4))
        if features.regime.volatility_regime == "expanded":
            risk_pct *= 0.85
        if weak_volume or range_like_signal:
            risk_pct *= 0.85
        risk_pct = min(float(risk_context["max_risk_per_trade"]), round(risk_pct, 4))

        leverage = max(1.0, round(1.0 + (confidence * 1.6), 2))
        if features.regime.volatility_regime == "expanded":
            leverage *= 0.85
        if weak_volume or range_like_signal:
            leverage *= 0.9
        leverage = min(float(risk_context["max_leverage"]), round(leverage, 2))

        pullback_outer = atr * (0.3 if range_like_signal else 0.42)
        pullback_inner = atr * (0.1 if range_like_signal else 0.16)
        if decision == "long":
            entry_min = round(price - pullback_outer, 2)
            entry_max = round(price - pullback_inner, 2)
        elif decision == "short":
            entry_min = round(price + pullback_inner, 2)
            entry_max = round(price + pullback_outer, 2)
        else:
            entry_band = atr * (0.08 if range_like_signal else 0.12)
            entry_min = round(price - entry_band, 2)
            entry_max = round(price + entry_band, 2)
        stop_loss: float | None = None
        take_profit: float | None = None
        if decision == "long":
            stop_loss, take_profit = self._adaptive_brackets("long", price=price, atr=atr, features=features)
        elif decision == "short":
            stop_loss, take_profit = self._adaptive_brackets("short", price=price, atr=atr, features=features)
        elif decision in {"reduce", "exit"} and open_position is not None:
            stop_loss = open_position.stop_loss
            take_profit = open_position.take_profit

        return self._normalize_entry_trigger_fields(
            TradeDecision(
                decision=decision,
                confidence=round(confidence, 4),
                symbol=market_snapshot.symbol,
                timeframe=market_snapshot.timeframe,
                entry_zone_min=float(entry_min),
                entry_zone_max=float(entry_max),
                stop_loss=stop_loss,
                take_profit=take_profit,
                max_holding_minutes=240,
                risk_pct=float(risk_pct),
                leverage=float(leverage),
                rationale_codes=list(dict.fromkeys(rationale)),
                explanation_short=short_explanation,
                explanation_detailed=detailed_explanation,
            ),
            market_snapshot=market_snapshot,
            features=features,
        )

    def _deterministic_decision_baseline_old(
        self,
        market_snapshot: MarketSnapshotPayload,
        features: FeaturePayload,
        open_positions: list[Position],
        risk_context: dict[str, Any],
    ) -> TradeDecision:
        price = market_snapshot.latest_price
        atr = max(features.atr, price * 0.0025)
        confidence = self._clamp(self._confidence(features) - 0.06, 0.18, 0.9)
        open_position = open_positions[0] if open_positions else None

        decision: Literal["hold", "long", "short", "reduce", "exit"] = "hold"
        rationale = ["BASELINE_OLD_NO_EDGE"]
        short_explanation = "Old baseline keeps the setup on hold until trend and momentum align cleanly."
        detailed_explanation = (
            "The baseline-old replay path intentionally uses a narrower, simpler entry filter. "
            "If trend, momentum, and volume are not aligned enough, it prefers hold."
        )

        regime_name = features.regime.primary_regime
        trend_alignment = features.regime.trend_alignment
        weak_volume = features.regime.weak_volume
        momentum_weakening = features.regime.momentum_weakening
        operating_state = str(risk_context.get("operating_state", "TRADABLE"))
        long_budget_available = self._entry_budget_allows(risk_context, side="long", price=price)
        short_budget_available = self._entry_budget_allows(risk_context, side="short", price=price)

        long_signal = (
            trend_alignment == "bullish_aligned"
            and regime_name not in {"range", "transition"}
            and features.trend_score >= 0.28
            and features.momentum_score >= 0.18
            and 48.0 <= features.rsi <= 74.0
            and not weak_volume
            and not momentum_weakening
        )
        short_signal = (
            trend_alignment == "bearish_aligned"
            and regime_name not in {"range", "transition"}
            and features.trend_score <= -0.28
            and features.momentum_score <= -0.18
            and 26.0 <= features.rsi <= 52.0
            and not weak_volume
            and not momentum_weakening
        )
        weakening_signal = weak_volume or momentum_weakening or regime_name in {"range", "transition"}

        if open_position is not None and operating_state == "PROTECTION_REQUIRED":
            decision = "long" if open_position.side == "long" else "short"
            rationale = ["PROTECTION_REQUIRED", "RESTORE_PROTECTION", "BASELINE_OLD"]
            short_explanation = "Protection must be restored before any new replay action."
            detailed_explanation = (
                "The deterministic guard keeps the existing side so the protection recovery flow can be restored. "
                "This keeps the old baseline compatible with the current safety model."
            )
        elif open_position is not None and operating_state == "DEGRADED_MANAGE_ONLY":
            decision = "reduce"
            rationale = ["MANAGE_ONLY_MODE", "REDUCE_EXPOSURE", "BASELINE_OLD"]
            short_explanation = "Manage-only state reduces open exposure instead of adding risk."
            detailed_explanation = (
                "When the operating state is degraded, the baseline-old path only manages existing exposure. "
                "It does not add new risk in replay."
            )
        elif open_position is not None and open_position.side == "long" and (short_signal or features.rsi >= 72.0):
            decision = "exit"
            rationale = ["LONG_EXHAUSTION", "BASELINE_OLD"]
            short_explanation = "The long position is exited when the old baseline sees exhaustion."
            detailed_explanation = (
                "The old baseline exits long exposure on clear reversal pressure or stretched RSI. "
                "It favors flattening over staying aggressive."
            )
        elif open_position is not None and open_position.side == "short" and (long_signal or features.rsi <= 28.0):
            decision = "exit"
            rationale = ["SHORT_EXHAUSTION", "BASELINE_OLD"]
            short_explanation = "The short position is exited when the old baseline sees reversal risk."
            detailed_explanation = (
                "The old baseline exits short exposure on clear reversal pressure or compressed RSI. "
                "It favors flattening over forcing continuation."
            )
        elif open_position is not None and weakening_signal:
            decision = "reduce"
            rationale = ["WEAKENING_SIGNAL", "BASELINE_OLD"]
            short_explanation = "Weakening conditions cause the old baseline to reduce open exposure."
            detailed_explanation = (
                "If volume or momentum deteriorates after entry, the baseline-old path trims the position. "
                "This keeps the comparison path conservative."
            )
        elif long_signal and not long_budget_available:
            rationale = ["BASELINE_OLD_RISK_BUDGET_EXHAUSTED", "HOLD_ON_LONG_BUDGET_LIMIT"]
            short_explanation = "남은 롱 예산이 부족해 기존 방식에서도 HOLD가 우선입니다."
            detailed_explanation = (
                "리스크 예산 여유가 거의 없어 기존 baseline 로직 기준으로도 신규 롱 진입보다 HOLD가 더 보수적입니다."
            )
        elif short_signal and not short_budget_available:
            rationale = ["BASELINE_OLD_RISK_BUDGET_EXHAUSTED", "HOLD_ON_SHORT_BUDGET_LIMIT"]
            short_explanation = "남은 숏 예산이 부족해 기존 방식에서도 HOLD가 우선입니다."
            detailed_explanation = (
                "리스크 예산 여유가 거의 없어 기존 baseline 로직 기준으로도 신규 숏 진입보다 HOLD가 더 보수적입니다."
            )
        elif long_signal:
            decision = "long"
            rationale = ["TREND_UP", "RSI_HEALTHY", "BASELINE_OLD"]
            short_explanation = "The old baseline accepts a long only on clean aligned strength."
            detailed_explanation = (
                "Trend, momentum, and RSI are aligned enough for the baseline-old logic to allow a long entry. "
                "The filter is intentionally stricter than the improved path."
            )
        elif short_signal:
            decision = "short"
            rationale = ["TREND_DOWN", "RSI_WEAK", "BASELINE_OLD"]
            short_explanation = "The old baseline accepts a short only on clean aligned weakness."
            detailed_explanation = (
                "Trend, momentum, and RSI are aligned enough for the baseline-old logic to allow a short entry. "
                "The filter is intentionally stricter than the improved path."
            )

        risk_pct = max(0.003, round(confidence * 0.0075, 4))
        if features.regime.volatility_regime == "expanded":
            risk_pct *= 0.85
        if weak_volume or regime_name in {"range", "transition"}:
            risk_pct *= 0.85
        risk_pct = min(float(risk_context["max_risk_per_trade"]), round(risk_pct, 4))

        leverage = max(1.0, round(1.0 + (confidence * 1.4), 2))
        if features.regime.volatility_regime == "expanded":
            leverage *= 0.85
        if weak_volume or regime_name in {"range", "transition"}:
            leverage *= 0.9
        leverage = min(float(risk_context["max_leverage"]), round(leverage, 2))

        entry_band = atr * 0.18
        entry_min = round(price - entry_band, 2)
        entry_max = round(price + entry_band, 2)
        stop_loss: float | None = None
        take_profit: float | None = None
        if decision == "long":
            stop_loss, take_profit = self._adaptive_brackets("long", price=price, atr=atr, features=features)
        elif decision == "short":
            stop_loss, take_profit = self._adaptive_brackets("short", price=price, atr=atr, features=features)
        elif decision in {"reduce", "exit"} and open_position is not None:
            stop_loss = open_position.stop_loss
            take_profit = open_position.take_profit

        return self._normalize_entry_trigger_fields(
            TradeDecision(
                decision=decision,
                confidence=round(confidence, 4),
                symbol=market_snapshot.symbol,
                timeframe=market_snapshot.timeframe,
                entry_zone_min=float(entry_min),
                entry_zone_max=float(entry_max),
                stop_loss=stop_loss,
                take_profit=take_profit,
                max_holding_minutes=240,
                risk_pct=float(risk_pct),
                leverage=float(leverage),
                rationale_codes=rationale,
                explanation_short=short_explanation,
                explanation_detailed=detailed_explanation,
            ),
            market_snapshot=market_snapshot,
            features=features,
        )

    def _deterministic_decision(
        self,
        market_snapshot: MarketSnapshotPayload,
        features: FeaturePayload,
        open_positions: list[Position],
        risk_context: dict[str, Any],
        *,
        logic_variant: str = "improved",
    ) -> TradeDecision:
        if logic_variant == "baseline_old":
            return self._deterministic_decision_baseline_old(
                market_snapshot,
                features,
                open_positions,
                risk_context,
            )
        return self._deterministic_decision_improved(
            market_snapshot,
            features,
            open_positions,
            risk_context,
        )

    def run(
        self,
        market_snapshot: MarketSnapshotPayload,
        features: FeaturePayload,
        open_positions: list[Position],
        risk_context: dict[str, Any],
        *,
        use_ai: bool,
        max_input_candles: int,
        logic_variant: str = "improved",
        ai_context: AIDecisionContextPacket | dict[str, Any] | None = None,
    ) -> tuple[TradeDecision, str, dict[str, Any]]:
        resolved_ai_context = self._coerce_ai_context(ai_context)
        ai_context_payload = self._serialize_ai_context(ai_context)
        baseline = self._deterministic_decision(
            market_snapshot,
            features,
            open_positions,
            risk_context,
            logic_variant=logic_variant,
        )
        strategy_engine_selection = self._build_strategy_engine_selection_payload(
            market_snapshot=market_snapshot,
            features=features,
            open_positions=open_positions,
            risk_context=risk_context,
        )
        baseline, baseline_holding_profile = self._apply_holding_profile_fields(
            baseline,
            market_snapshot=market_snapshot,
            features=features,
            risk_context=risk_context,
            strategy_engine_selection=strategy_engine_selection,
        )
        selected_engine_payload = (
            strategy_engine_selection.get("selected_engine")
            if isinstance(strategy_engine_selection, dict)
            and isinstance(strategy_engine_selection.get("selected_engine"), dict)
            else {}
        )
        prompt_route = resolve_prompt_route(
            ai_context=resolved_ai_context,
            strategy_engine=str(
                (resolved_ai_context.strategy_engine if resolved_ai_context is not None else None)
                or selected_engine_payload.get("engine_name")
                or ""
            )
            or None,
            has_open_position=bool(open_positions),
        )
        prompt_route_payload = prompt_route.to_payload()
        prior_metadata = self._prior_observability_fields(resolved_ai_context)
        position_management_context = (
            risk_context.get("position_management_context")
            if isinstance(risk_context.get("position_management_context"), dict)
            else {}
        )
        if logic_variant == "baseline_old":
            provider_code = "PROVIDER_DETERMINISTIC_BASELINE_OLD"
            decision = baseline.model_copy(
                update={
                    "rationale_codes": list(dict.fromkeys(baseline.rationale_codes + [provider_code])),
                    "provider_status": "deterministic",
                }
            )
            decision = self._apply_ai_schema_fields(decision, ai_context=resolved_ai_context)
            decision = decision.model_copy(update={key: value for key, value in prior_metadata.items() if key in TradeDecision.model_fields})
            decision_agreement = self._build_decision_agreement(
                baseline,
                decision,
                ai_used=False,
            )
            return (
                decision,
                "deterministic-mock",
                {
                    "source": "deterministic",
                    "logic_variant": logic_variant,
                    "decision_agreement": decision_agreement,
                    "strategy_engine": strategy_engine_selection,
                    "holding_profile": decision.holding_profile,
                    "holding_profile_reason": decision.holding_profile_reason,
                    "holding_profile_context": baseline_holding_profile,
                    "ai_context": ai_context_payload,
                    "ai_context_version": (
                        resolved_ai_context.ai_context_version
                        if resolved_ai_context is not None
                        else decision.ai_context_version
                    ),
                    **self._route_metadata(prompt_route, provider_status="deterministic"),
                    "allowed_actions": list(prompt_route.allowed_actions),
                    "forbidden_actions": list(prompt_route.forbidden_actions),
                    "bounded_output_applied": decision.bounded_output_applied,
                    "fallback_reason_codes": list(decision.fallback_reason_codes),
                    "fail_closed_applied": decision.fail_closed_applied,
                    "should_abstain": decision.should_abstain,
                    "abstain_reason_codes": list(decision.abstain_reason_codes),
                    **deterministic_stop_management_payload(hard_stop_active=decision.stop_loss is not None),
                    "setup_cluster_state": {"matched": False, "active": False},
                    "suppression_context": {
                        "level": "none",
                        "sources": [],
                        "reason_codes": [],
                        "applies_hard_block": False,
                        "applies_risk_haircut": False,
                        "applies_soft_bias": False,
                    },
                    "adaptive_signal_adjustment": {
                        "enabled": False,
                        "status": "disabled_for_baseline_old",
                    },
                    **prior_metadata,
                },
            )
        if not use_ai:
            decision, adaptive_adjustment = self._apply_adaptive_adjustment(
                baseline,
                risk_context=risk_context,
                provider_code="PROVIDER_DETERMINISTIC_MOCK",
            )
            decision, holding_profile_context = self._apply_holding_profile_fields(
                decision,
                market_snapshot=market_snapshot,
                features=features,
                risk_context=risk_context,
                strategy_engine_selection=strategy_engine_selection,
            )
            decision = decision.model_copy(update={"provider_status": "deterministic"})
            decision = self._apply_ai_schema_fields(decision, ai_context=resolved_ai_context)
            decision = decision.model_copy(update={key: value for key, value in prior_metadata.items() if key in TradeDecision.model_fields})
            decision_agreement = self._build_decision_agreement(
                baseline,
                decision,
                ai_used=False,
            )
            return (
                decision,
                "deterministic-mock",
                {
                    "source": "deterministic",
                    "logic_variant": logic_variant,
                    "decision_agreement": decision_agreement,
                    "strategy_engine": strategy_engine_selection,
                    "holding_profile": decision.holding_profile,
                    "holding_profile_reason": decision.holding_profile_reason,
                    "holding_profile_context": holding_profile_context,
                    "ai_context": ai_context_payload,
                    "ai_context_version": (
                        resolved_ai_context.ai_context_version
                        if resolved_ai_context is not None
                        else decision.ai_context_version
                    ),
                    **self._route_metadata(prompt_route, provider_status="deterministic"),
                    "allowed_actions": list(prompt_route.allowed_actions),
                    "forbidden_actions": list(prompt_route.forbidden_actions),
                    "bounded_output_applied": decision.bounded_output_applied,
                    "fallback_reason_codes": list(decision.fallback_reason_codes),
                    "fail_closed_applied": decision.fail_closed_applied,
                    "should_abstain": decision.should_abstain,
                    "abstain_reason_codes": list(decision.abstain_reason_codes),
                    **deterministic_stop_management_payload(hard_stop_active=decision.stop_loss is not None),
                    "setup_cluster_state": adaptive_adjustment.get("setup_cluster_state"),
                    "suppression_context": adaptive_adjustment.get("suppression_context"),
                    "adaptive_signal_adjustment": adaptive_adjustment,
                    **prior_metadata,
                },
            )

        provider_result: ProviderResult | None = None
        try:
            candle_limit = max(8, min(max_input_candles, 16))
            compact_candles = [
                {
                    "t": candle.timestamp.isoformat(),
                    "o": round(candle.open, 2),
                    "h": round(candle.high, 2),
                    "l": round(candle.low, 2),
                    "c": round(candle.close, 2),
                    "v": round(candle.volume, 2),
                }
                for candle in market_snapshot.candles[-candle_limit:]
            ]
            compact_payload = {
                "market_snapshot": {
                    "symbol": market_snapshot.symbol,
                    "timeframe": market_snapshot.timeframe,
                    "latest_price": market_snapshot.latest_price,
                    "latest_volume": market_snapshot.latest_volume,
                    "is_stale": market_snapshot.is_stale,
                    "is_complete": market_snapshot.is_complete,
                    "derivatives_context": market_snapshot.derivatives_context.model_dump(mode="json"),
                    "candles": compact_candles,
                },
                "features": {
                    "trend_score": features.trend_score,
                    "volatility_pct": features.volatility_pct,
                    "volume_ratio": features.volume_ratio,
                    "drawdown_pct": features.drawdown_pct,
                    "rsi": features.rsi,
                    "atr": features.atr,
                    "atr_pct": features.atr_pct,
                    "momentum_score": features.momentum_score,
                    "regime": features.regime.model_dump(mode="json"),
                    "breakout": features.breakout.model_dump(mode="json"),
                    "candle_structure": features.candle_structure.model_dump(mode="json"),
                    "location": features.location.model_dump(mode="json"),
                    "volume_persistence": features.volume_persistence.model_dump(mode="json"),
                    "pullback_context": features.pullback_context.model_dump(mode="json"),
                    "derivatives": features.derivatives.model_dump(mode="json"),
                    "multi_timeframe": {
                        timeframe: context.model_dump(mode="json")
                        for timeframe, context in features.multi_timeframe.items()
                    },
                    "data_quality_flags": features.data_quality_flags,
                },
                "open_positions": [
                    {
                        "side": position.side,
                        "quantity": position.quantity,
                        "entry_price": position.entry_price,
                        "stop_loss": position.stop_loss,
                        "take_profit": position.take_profit,
                    }
                    for position in open_positions
                ],
                "risk_context": risk_context,
                "position_management_context": position_management_context,
                "deterministic_baseline": {
                    "decision": baseline.decision,
                    "confidence": baseline.confidence,
                    "entry_zone_min": baseline.entry_zone_min,
                    "entry_zone_max": baseline.entry_zone_max,
                    "entry_mode": baseline.entry_mode,
                    "holding_profile": baseline.holding_profile,
                    "holding_profile_reason": baseline.holding_profile_reason,
                    "invalidation_price": baseline.invalidation_price,
                    "max_chase_bps": baseline.max_chase_bps,
                    "idea_ttl_minutes": baseline.idea_ttl_minutes,
                    "stop_loss": baseline.stop_loss,
                    "take_profit": baseline.take_profit,
                    "risk_pct": baseline.risk_pct,
                    "leverage": baseline.leverage,
                    "rationale_codes": baseline.rationale_codes,
                    "explanation_short": baseline.explanation_short,
                },
                "strategy_engine_selection": strategy_engine_selection,
                "logic_variant": logic_variant,
            }
            if ai_context_payload is not None:
                compact_payload["ai_context"] = ai_context_payload
            compact_payload["ai_prompt_route"] = prompt_route_payload
            provider_result = self.provider.generate(
                AgentRole.TRADING_DECISION.value,
                compact_payload,
                response_model=TradeDecision,
                instructions=render_prompt_instructions(route=prompt_route),
            )
            decision = self._normalize_entry_trigger_fields(
                TradeDecision.model_validate(provider_result.output),
                market_snapshot=market_snapshot,
                features=features,
            )
            decision, adaptive_adjustment = self._apply_adaptive_adjustment(
                decision,
                risk_context=risk_context,
                provider_code=f"PROVIDER_{provider_result.provider.upper()}",
            )
            decision, holding_profile_context = self._apply_holding_profile_fields(
                decision,
                market_snapshot=market_snapshot,
                features=features,
                risk_context=risk_context,
                strategy_engine_selection=strategy_engine_selection,
            )
            decision = self._apply_ai_schema_fields(decision, ai_context=resolved_ai_context)
            decision, adjusted_prior_metadata = self._apply_prior_soft_adjustments(
                decision,
                ai_context=resolved_ai_context,
                route=prompt_route,
            )
            bounded_result = bound_trade_decision(
                decision=decision,
                route=prompt_route,
                ai_context=resolved_ai_context,
                has_open_position=bool(open_positions),
                open_position_side=self._open_position_side(open_positions),
                current_stop_loss=self._current_stop_loss(open_positions),
                losing_position=self._has_losing_position(open_positions),
                provider_status="ok",
            )
            decision = bounded_result.decision
            decision_agreement = self._build_decision_agreement(
                baseline,
                decision,
                ai_used=True,
            )
            metadata = _provider_metadata(provider_result, source="llm")
            metadata["logic_variant"] = logic_variant
            metadata["decision_agreement"] = decision_agreement
            metadata["strategy_engine"] = strategy_engine_selection
            metadata["holding_profile"] = decision.holding_profile
            metadata["holding_profile_reason"] = decision.holding_profile_reason
            metadata["holding_profile_context"] = holding_profile_context
            metadata["ai_context"] = ai_context_payload
            metadata["ai_context_version"] = (
                resolved_ai_context.ai_context_version
                if resolved_ai_context is not None
                else decision.ai_context_version
            )
            metadata.update(self._route_metadata(prompt_route, provider_status="ok"))
            metadata["allowed_actions"] = list(prompt_route.allowed_actions)
            metadata["forbidden_actions"] = list(prompt_route.forbidden_actions)
            metadata["bounded_output_applied"] = bounded_result.bounded_output_applied
            metadata["fallback_reason_codes"] = list(bounded_result.fallback_reason_codes)
            metadata["fail_closed_applied"] = bounded_result.fail_closed_applied
            metadata["should_abstain"] = decision.should_abstain
            metadata["abstain_reason_codes"] = list(bounded_result.abstain_reason_codes)
            metadata.update(deterministic_stop_management_payload(hard_stop_active=decision.stop_loss is not None))
            metadata["setup_cluster_state"] = adaptive_adjustment.get("setup_cluster_state")
            metadata["suppression_context"] = adaptive_adjustment.get("suppression_context")
            metadata["adaptive_signal_adjustment"] = adaptive_adjustment
            metadata.update(adjusted_prior_metadata)
            return decision, provider_result.provider, metadata
        except Exception as exc:
            provider_status = self._provider_status_from_exception(exc)
            if prompt_route.fail_closed:
                reason_code = (
                    "AI_SCHEMA_INVALID"
                    if provider_status == "schema_invalid"
                    else "AI_UNAVAILABLE_FAIL_CLOSED"
                )
                decision = self._fail_closed_decision(
                    baseline,
                    ai_context=resolved_ai_context,
                    provider_status=provider_status,
                    fail_reason_code=reason_code,
                )
                holding_profile_context = baseline_holding_profile
                adaptive_adjustment = {
                    "enabled": False,
                    "status": "fail_closed",
                    "reason_codes": [reason_code, "AI_UNAVAILABLE_FAIL_CLOSED"],
                }
            else:
                decision, adaptive_adjustment = self._apply_adaptive_adjustment(
                    baseline.model_copy(update={"rationale_codes": baseline.rationale_codes + ["LLM_FALLBACK"]}),
                    risk_context=risk_context,
                    provider_code=f"PROVIDER_{self.provider.name.upper()}",
                )
                decision, holding_profile_context = self._apply_holding_profile_fields(
                    decision,
                    market_snapshot=market_snapshot,
                    features=features,
                    risk_context=risk_context,
                    strategy_engine_selection=strategy_engine_selection,
                )
                decision = decision.model_copy(update={"provider_status": provider_status})
            decision = self._apply_ai_schema_fields(decision, ai_context=resolved_ai_context)
            decision = decision.model_copy(update={key: value for key, value in prior_metadata.items() if key in TradeDecision.model_fields})
            decision_agreement = self._build_decision_agreement(
                baseline,
                decision,
                ai_used=False,
            )
            metadata = _provider_metadata(provider_result, source="llm_fallback")
            metadata["error"] = str(exc)
            metadata["logic_variant"] = logic_variant
            metadata["decision_agreement"] = decision_agreement
            metadata["strategy_engine"] = strategy_engine_selection
            metadata["holding_profile"] = decision.holding_profile
            metadata["holding_profile_reason"] = decision.holding_profile_reason
            metadata["holding_profile_context"] = holding_profile_context
            metadata["ai_context"] = ai_context_payload
            metadata["ai_context_version"] = (
                resolved_ai_context.ai_context_version
                if resolved_ai_context is not None
                else decision.ai_context_version
            )
            metadata.update(self._route_metadata(prompt_route, provider_status=provider_status))
            metadata["allowed_actions"] = list(prompt_route.allowed_actions)
            metadata["forbidden_actions"] = list(prompt_route.forbidden_actions)
            metadata["bounded_output_applied"] = decision.bounded_output_applied
            metadata["fallback_reason_codes"] = list(decision.fallback_reason_codes)
            metadata["fail_closed_applied"] = decision.fail_closed_applied
            metadata["should_abstain"] = decision.should_abstain
            metadata["abstain_reason_codes"] = list(decision.abstain_reason_codes)
            metadata.update(deterministic_stop_management_payload(hard_stop_active=decision.stop_loss is not None))
            metadata["setup_cluster_state"] = adaptive_adjustment.get("setup_cluster_state")
            metadata["suppression_context"] = adaptive_adjustment.get("suppression_context")
            metadata["adaptive_signal_adjustment"] = adaptive_adjustment
            metadata.update(prior_metadata)
            return decision, "deterministic-mock", metadata


class ChiefReviewAgent:
    def __init__(self, provider: StructuredModelProvider | None = None) -> None:
        self.provider = provider

    def _deterministic_review(
        self,
        decision: TradeDecision,
        risk_result: RiskCheckResult,
        health_events: list[SystemHealthEvent],
        alerts: list[Alert],
    ) -> ChiefReviewSummary:
        blockers = list(risk_result.reason_codes)
        blockers.extend(alert.title for alert in alerts[:3])
        degraded = any(event.status not in {"ok", "healthy"} for event in health_events[:5])

        if not risk_result.allowed or degraded:
            mode = OperatingMode.HOLD
            priority = PriorityLevel.HIGH if degraded else PriorityLevel.MEDIUM
            summary = "리스크 또는 시스템 상태 때문에 실행보다 HOLD가 우선입니다."
            must_do = ["차단 사유 확인", "시스템 상태 점검", "다음 평가 전까지 모니터링"]
        elif decision.decision == "hold":
            mode = OperatingMode.MONITOR
            priority = PriorityLevel.MEDIUM
            summary = "시장 신호가 약해 모니터링 유지가 적절합니다."
            must_do = ["다음 평가 대기", "거래량과 추세 변화 확인"]
        else:
            mode = OperatingMode.ACT
            priority = PriorityLevel.MEDIUM
            summary = "리스크 검증을 통과해 종이매매 기준 실행 가능한 상태입니다."
            must_do = ["실행 결과 모니터링", "후속 알림 확인"]

        return ChiefReviewSummary(
            summary=summary,
            recommended_mode=mode.value,
            must_do_actions=must_do,
            blockers=blockers,
            priority=priority.value,
        )

    def run(
        self,
        decision: TradeDecision,
        risk_result: RiskCheckResult,
        health_events: list[SystemHealthEvent],
        alerts: list[Alert],
        *,
        use_ai: bool,
    ) -> tuple[ChiefReviewSummary, str, dict[str, Any]]:
        baseline = self._deterministic_review(decision, risk_result, health_events, alerts)
        if not use_ai or self.provider is None:
            return baseline, "deterministic-mock", {"source": "deterministic"}
        try:
            provider_result = self.provider.generate(
                AgentRole.CHIEF_REVIEW.value,
                {
                    "decision": decision.model_dump(mode="json"),
                    "risk_result": risk_result.model_dump(mode="json"),
                    "health_events": [
                        {"component": event.component, "status": event.status, "message": event.message}
                        for event in health_events[:8]
                    ],
                    "alerts": [{"title": alert.title, "message": alert.message} for alert in alerts[:5]],
                    "deterministic_baseline": baseline.model_dump(mode="json"),
                },
                response_model=ChiefReviewSummary,
                instructions=(
                    "Summarize the current operating posture. "
                    "If risk_result.allowed is false, recommended_mode should remain hold."
                ),
            )
            result = ChiefReviewSummary.model_validate(provider_result.output)
            return result, provider_result.provider, _provider_metadata(provider_result, source="llm")
        except Exception as exc:
            return baseline, "deterministic-mock", {"source": "llm_fallback", "error": str(exc)}

