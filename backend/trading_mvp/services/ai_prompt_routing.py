from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from trading_mvp.schemas import AIDecisionContextPacket, TradeDecision
from trading_mvp.services.holding_profile import HOLDING_PROFILE_SCALP

DecisionAction = Literal["hold", "long", "short", "reduce", "exit"]

POSITION_REVIEW_TRIGGERS = {"open_position_recheck_due", "protection_review_event"}
NEW_ENTRY_ACTIONS = {"long", "short"}
LONG_HORIZON_PROFILES = {"swing", "position"}


@dataclass(frozen=True, slots=True)
class PromptRoutePolicy:
    trigger_type: str | None
    strategy_engine: str
    prompt_family: str
    allowed_actions: tuple[DecisionAction, ...]
    forbidden_actions: tuple[DecisionAction, ...]
    allow_new_entry: bool
    allow_reduce_exit: bool
    allowed_recommended_holding_profiles: tuple[str, ...]
    holding_profile_change_policy: str
    stop_management_mode: str
    fail_closed: bool
    safe_fallback_action: DecisionAction = "hold"
    allow_abstain: bool = True
    data_quality_hold_bias: bool = False
    family_instruction: str = ""
    engine_instruction: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "trigger_type": self.trigger_type,
            "strategy_engine": self.strategy_engine,
            "prompt_family": self.prompt_family,
            "allowed_actions": list(self.allowed_actions),
            "forbidden_actions": list(self.forbidden_actions),
            "allow_new_entry": self.allow_new_entry,
            "allow_reduce_exit": self.allow_reduce_exit,
            "allowed_recommended_holding_profiles": list(self.allowed_recommended_holding_profiles),
            "holding_profile_change_policy": self.holding_profile_change_policy,
            "stop_management_mode": self.stop_management_mode,
            "fail_closed": self.fail_closed,
            "safe_fallback_action": self.safe_fallback_action,
            "allow_abstain": self.allow_abstain,
            "data_quality_hold_bias": self.data_quality_hold_bias,
        }


@dataclass(frozen=True, slots=True)
class BoundedDecisionResult:
    decision: TradeDecision
    bounded_output_applied: bool
    fallback_reason_codes: tuple[str, ...]
    fail_closed_applied: bool
    abstain_reason_codes: tuple[str, ...]


def _normalized_engine(strategy_engine: str | None) -> str:
    engine = str(strategy_engine or "").strip() or "trend_pullback_engine"
    return engine


def _active_position_route_override(
    *,
    ai_context: AIDecisionContextPacket | None,
    has_open_position: bool,
) -> tuple[bool, str | None]:
    if not has_open_position or ai_context is None:
        return False, None
    strategy_engine_context = dict(ai_context.strategy_engine_context or {})
    management_only = bool(strategy_engine_context.get("management_only_open_position_route"))
    allowed_add_on_side = str(strategy_engine_context.get("allowed_add_on_side") or "").strip().lower()
    if bool(strategy_engine_context.get("allow_same_side_add_on")) and allowed_add_on_side in NEW_ENTRY_ACTIONS:
        return management_only, allowed_add_on_side
    return management_only, None


def _recommended_profiles_for_route(
    *,
    strategy_engine: str,
    trigger_type: str | None,
    has_open_position: bool,
    current_profile: str | None,
) -> tuple[str, ...]:
    normalized_profile = str(current_profile or HOLDING_PROFILE_SCALP).strip().lower() or HOLDING_PROFILE_SCALP
    if strategy_engine == "breakout_exception_engine":
        return ("hold_current", HOLDING_PROFILE_SCALP) if has_open_position or trigger_type in POSITION_REVIEW_TRIGGERS else (HOLDING_PROFILE_SCALP,)
    if has_open_position or trigger_type in POSITION_REVIEW_TRIGGERS:
        return ("hold_current", normalized_profile)
    return (HOLDING_PROFILE_SCALP, "swing", "position")


def resolve_prompt_route(
    *,
    ai_context: AIDecisionContextPacket | None,
    strategy_engine: str | None = None,
    trigger_type: str | None = None,
    has_open_position: bool,
) -> PromptRoutePolicy:
    resolved_trigger = str(trigger_type or (ai_context.trigger_type if ai_context is not None else "") or "").strip() or None
    resolved_engine = _normalized_engine(strategy_engine or (ai_context.strategy_engine if ai_context is not None else None))
    current_profile = ai_context.holding_profile if ai_context is not None else None
    management_only_open_position_route, add_on_side = _active_position_route_override(
        ai_context=ai_context,
        has_open_position=has_open_position,
    )
    allowed_profiles = _recommended_profiles_for_route(
        strategy_engine=resolved_engine,
        trigger_type=resolved_trigger,
        has_open_position=has_open_position,
        current_profile=current_profile,
    )
    if resolved_engine == "protection_reduce_engine" or resolved_trigger == "protection_review_event":
        return PromptRoutePolicy(
            trigger_type=resolved_trigger,
            strategy_engine=resolved_engine,
            prompt_family="protection_reduce_review",
            allowed_actions=("hold", "reduce", "exit"),
            forbidden_actions=("long", "short"),
            allow_new_entry=False,
            allow_reduce_exit=True,
            allowed_recommended_holding_profiles=allowed_profiles,
            holding_profile_change_policy="preserve_or_de_risk_only",
            stop_management_mode="tighten_only",
            fail_closed=False,
            safe_fallback_action="reduce",
            family_instruction=(
                "This is a protection and reduction review. "
                "Do not suggest a fresh entry, opposite reversal, stop widening, or protection removal."
            ),
            engine_instruction="protection_reduce_engine is management-only and never authorizes a new entry.",
        )
    if resolved_trigger in POSITION_REVIEW_TRIGGERS or (resolved_trigger == "manual_review_event" and has_open_position):
        return PromptRoutePolicy(
            trigger_type=resolved_trigger,
            strategy_engine=resolved_engine,
            prompt_family="open_position_thesis_review",
            allowed_actions=("hold", "reduce", "exit"),
            forbidden_actions=("long", "short"),
            allow_new_entry=False,
            allow_reduce_exit=True,
            allowed_recommended_holding_profiles=allowed_profiles,
            holding_profile_change_policy="keep_current_or_de_risk_only",
            stop_management_mode="tighten_only",
            fail_closed=False,
            safe_fallback_action="hold",
            family_instruction=(
                "This is an open-position thesis review. "
                "Focus on hold, reduce, or exit. Do not propose a fresh entry or reverse into a new position."
            ),
            engine_instruction="A losing position cannot be promoted into a longer holding profile.",
        )
    if resolved_trigger == "periodic_backstop_due":
        return PromptRoutePolicy(
            trigger_type=resolved_trigger,
            strategy_engine=resolved_engine,
            prompt_family="periodic_backstop_review",
            allowed_actions=("hold", "reduce", "exit") if has_open_position else ("hold", "long", "short"),
            forbidden_actions=("long", "short") if has_open_position else ("reduce", "exit"),
            allow_new_entry=not has_open_position,
            allow_reduce_exit=has_open_position,
            allowed_recommended_holding_profiles=allowed_profiles,
            holding_profile_change_policy="stale_thesis_refresh_only",
            stop_management_mode="tighten_only",
            fail_closed=not has_open_position,
            safe_fallback_action="hold",
            family_instruction=(
                "This is a low-frequency stale-thesis backstop review. "
                "Do not manufacture activity. Prefer hold unless the packet clearly supports action."
            ),
            engine_instruction="Backstop review is a safety net, not a frequency expansion mechanism.",
        )
    if management_only_open_position_route and add_on_side in NEW_ENTRY_ACTIONS:
        forbidden_actions = tuple(
            action
            for action in ("long", "short")
            if action != add_on_side
        )
        return PromptRoutePolicy(
            trigger_type=resolved_trigger,
            strategy_engine=resolved_engine,
            prompt_family="open_position_add_on_review",
            allowed_actions=("hold", add_on_side, "reduce", "exit"),
            forbidden_actions=forbidden_actions,
            allow_new_entry=True,
            allow_reduce_exit=True,
            allowed_recommended_holding_profiles=allowed_profiles,
            holding_profile_change_policy="keep_current_or_de_risk_only",
            stop_management_mode="tighten_only",
            fail_closed=False,
            safe_fallback_action="hold",
            family_instruction=(
                "This is an open-position management review. "
                "Only a protected winner-only same-side add-on may use a fresh entry action."
            ),
            engine_instruction="Opposite reversal and unprotected add-on ideas remain forbidden while a position is open.",
        )
    if management_only_open_position_route:
        return PromptRoutePolicy(
            trigger_type=resolved_trigger,
            strategy_engine=resolved_engine,
            prompt_family="open_position_thesis_review",
            allowed_actions=("hold", "reduce", "exit"),
            forbidden_actions=("long", "short"),
            allow_new_entry=False,
            allow_reduce_exit=True,
            allowed_recommended_holding_profiles=allowed_profiles,
            holding_profile_change_policy="keep_current_or_de_risk_only",
            stop_management_mode="tighten_only",
            fail_closed=False,
            safe_fallback_action="hold",
            family_instruction=(
                "This is an open-position management review. "
                "Focus on hold, reduce, or exit until state changes materially."
            ),
            engine_instruction="Fresh entry ideas are suppressed while an active position remains open.",
        )
    if resolved_engine == "breakout_exception_engine" or resolved_trigger == "breakout_exception_event":
        return PromptRoutePolicy(
            trigger_type=resolved_trigger,
            strategy_engine=resolved_engine,
            prompt_family="breakout_exception_review",
            allowed_actions=("hold", "long", "short"),
            forbidden_actions=("reduce", "exit"),
            allow_new_entry=True,
            allow_reduce_exit=False,
            allowed_recommended_holding_profiles=(HOLDING_PROFILE_SCALP,),
            holding_profile_change_policy="scalp_only",
            stop_management_mode="tighten_only",
            fail_closed=True,
            safe_fallback_action="hold",
            data_quality_hold_bias=True,
            family_instruction=(
                "Breakout exception is rare and scalp-only. "
                "If data quality is degraded or unavailable, default to hold with abstain metadata."
            ),
            engine_instruction="Do not promote breakout_exception ideas into swing or position holding profiles.",
        )
    if resolved_engine == "trend_continuation_engine":
        return PromptRoutePolicy(
            trigger_type=resolved_trigger,
            strategy_engine=resolved_engine,
            prompt_family="entry_continuation_review",
            allowed_actions=("hold", "long", "short"),
            forbidden_actions=("reduce", "exit"),
            allow_new_entry=True,
            allow_reduce_exit=False,
            allowed_recommended_holding_profiles=allowed_profiles,
            holding_profile_change_policy="entry_profile_only",
            stop_management_mode="tighten_only",
            fail_closed=True,
            safe_fallback_action="hold",
            family_instruction="Review continuation quality. If the move looks late or extended, prefer hold or abstain.",
            engine_instruction="Continuation entries must stay conservative when extension risk is elevated.",
        )
    if resolved_engine == "range_mean_reversion_engine":
        return PromptRoutePolicy(
            trigger_type=resolved_trigger,
            strategy_engine=resolved_engine,
            prompt_family="range_mean_reversion_review",
            allowed_actions=("hold", "long", "short"),
            forbidden_actions=("reduce", "exit"),
            allow_new_entry=True,
            allow_reduce_exit=False,
            allowed_recommended_holding_profiles=allowed_profiles,
            holding_profile_change_policy="entry_profile_only",
            stop_management_mode="tighten_only",
            fail_closed=True,
            safe_fallback_action="hold",
            family_instruction="Treat this as a small fade / range mean reversion review.",
            engine_instruction="Do not tell a trend continuation story inside range_mean_reversion_engine.",
        )
    return PromptRoutePolicy(
        trigger_type=resolved_trigger,
        strategy_engine=resolved_engine,
        prompt_family="entry_pullback_review",
        allowed_actions=("hold", "long", "short"),
        forbidden_actions=("reduce", "exit"),
        allow_new_entry=True,
        allow_reduce_exit=False,
        allowed_recommended_holding_profiles=allowed_profiles,
        holding_profile_change_policy="entry_profile_only",
        stop_management_mode="tighten_only",
        fail_closed=True,
        safe_fallback_action="hold",
        family_instruction="Review only pullback-confirm entry quality. Prefer aligned pullback confirmation over breakout chasing.",
        engine_instruction="breakout_confirm requires explicit breakout_exception routing instead of generic pullback review.",
    )


def render_prompt_instructions(
    *,
    route: PromptRoutePolicy,
) -> str:
    contract = json.dumps(route.to_payload(), ensure_ascii=False, separators=(",", ":"))
    reviewer_contract = (
        "You are a Senior Quant Risk Reviewer for a Binance Futures short-term trading system. "
        "You may propose trade intent, but you have no authority to execute orders. "
        "Every live order must pass deterministic risk_guard validation after your decision; "
        "if your judgment conflicts with hard risk policy, hard risk policy wins. "
        "HOLD is the default when data is uncertain, stale, incomplete, contradictory, or operational state is not trustworthy. "
        "Prioritize entry-zone confirmation, pullback confirmation, 1m confirmation, and preserved risk/reward over chase entries. "
        "Before proposing any new long or short intent, explicitly review regime, volatility, liquidity, trend alignment, "
        "range structure, momentum, VWAP context, expected RR, estimated slippage, fees, and invalidation level. "
        "If protective orders are missing, mismatched, not reduce-only, or position/order sync is uncertain, "
        "prioritize position protection or review over fresh entry. "
    )
    schema_instruction = (
        "Populate the structured schema as completely as possible: decision, symbol, strategy_id, regime, confidence, "
        "reason_summary, entry_intent, entry_zone, invalidation_level, risk_notes, required_confirmations, and hard_blocks_observed. "
        "Use risk_notes for cost/RR/protection concerns, required_confirmations for zone/pullback/1m checks, "
        "and hard_blocks_observed for stale data, incomplete sync, protection uncertainty, policy blocks, or missing capacity. "
    )
    entry_plan_instruction = (
        "For new-entry routes, distinguish no-trade from a conditional entry plan. "
        "If a directional thesis is worth monitoring but price must first reach a specific zone, "
        "return decision='hold' with watch_entry_plan populated with side, entry zone, invalidation, stop, target, chase cap, TTL, and reason codes. "
        "A watch_entry_plan only arms a pending entry plan; execution still requires zone touch, AI recheck, and risk_guard approval. "
        "For same-direction re-entry after a recent take-profit close, only consider a new watch_entry_plan when fresh edge clearly exceeds estimated fees and slippage. "
        "Use decision='long' or decision='short' only when you are endorsing that side as the current trade intent. "
        "Use decision='hold' with watch_entry_plan=null when no side and zone should be monitored. "
    ) if route.allow_new_entry else (
        "This route must not create a watch_entry_plan; keep watch_entry_plan=null. "
    )
    return (
        reviewer_contract
        + "Return exactly one structured decision that fits the routing contract. "
        f"{route.family_instruction} "
        f"{route.engine_instruction} "
        f"{entry_plan_instruction}"
        f"{schema_instruction}"
        "Use regime_summary as the descriptive market-structure layer and event_context_summary as the forward-looking event-risk layer. "
        "Event context may justify lower confidence, a no-trade stance, event_risk_acknowledgement, confidence_penalty_reason, or scenario_note, "
        "but it never overrides the routing contract, risk_guard, or execution permissions. "
        "Use active_position_summary, pending_entry_plan_summary, and execution_constraints_summary to avoid duplicate plans, unsupported add-ons, "
        "or proposals that have no remaining actionable entry capacity. "
        "Never widen a stop, remove a deterministic hard stop, justify an unprotected position, average down a loser, "
        "or bypass the provided risk budget. Never propose size or leverage beyond the provided risk budget. "
        "If the remaining budget is small or zero, prefer hold. "
        "When the setup is not actionable, return decision='hold'. "
        "When you are abstaining rather than endorsing a trade, keep decision='hold', set should_abstain=true, "
        "and populate abstain_reason_codes. "
        f"Routing contract: {contract}."
    )


def _unique_codes(*groups: list[str] | tuple[str, ...]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for code in group:
            normalized = str(code or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            ordered.append(normalized)
    return ordered


def _decision_side(
    *,
    decision: TradeDecision,
    open_position_side: str | None,
) -> str | None:
    if decision.decision in {"long", "short"}:
        return decision.decision
    normalized = str(open_position_side or "").strip().lower()
    return normalized if normalized in {"long", "short"} else None


def _is_stop_widening(
    *,
    current_stop_loss: float | None,
    proposed_stop_loss: float | None,
    side: str | None,
) -> bool:
    if current_stop_loss is None or proposed_stop_loss is None or side not in {"long", "short"}:
        return False
    if side == "long":
        return proposed_stop_loss < current_stop_loss
    return proposed_stop_loss > current_stop_loss


def _fallback_decision(
    decision: TradeDecision,
    *,
    action: DecisionAction,
    fallback_reason_codes: list[str],
    abstain_reason_codes: list[str],
    current_profile: str,
    provider_status: str,
    fail_closed_applied: bool,
) -> TradeDecision:
    should_abstain = bool(abstain_reason_codes) or fail_closed_applied
    explanation_short = (
        "AI output was fail-closed into hold."
        if fail_closed_applied and action == "hold"
        else "AI output was bounded to a safe management action."
    )
    explanation_detailed = (
        "The AI response violated the route contract or fail-closed policy, so the decision was normalized before risk approval."
    )
    update: dict[str, Any] = {
        "decision": action,
        "entry_mode": "none" if action in {"hold", "reduce", "exit"} else decision.entry_mode,
        "entry_zone_min": None if action in {"hold", "reduce", "exit"} else decision.entry_zone_min,
        "entry_zone_max": None if action in {"hold", "reduce", "exit"} else decision.entry_zone_max,
        "watch_entry_plan": None,
        "invalidation_price": decision.invalidation_price if action in {"long", "short"} else None,
        "max_chase_bps": decision.max_chase_bps if action in {"long", "short"} else None,
        "idea_ttl_minutes": decision.idea_ttl_minutes if action in {"long", "short"} else None,
        "recommended_holding_profile": "hold_current" if action in {"hold", "reduce", "exit"} else decision.recommended_holding_profile,
        "holding_profile": current_profile,
        "should_abstain": should_abstain,
        "abstain_reason_codes": _unique_codes(abstain_reason_codes),
        "bounded_output_applied": True,
        "fallback_reason_codes": _unique_codes(fallback_reason_codes),
        "fail_closed_applied": fail_closed_applied,
        "provider_status": provider_status,
        "explanation_short": explanation_short,
        "explanation_detailed": explanation_detailed,
        "primary_reason_codes": _unique_codes(decision.primary_reason_codes, fallback_reason_codes),
        "rationale_codes": _unique_codes(decision.rationale_codes, fallback_reason_codes),
        "no_trade_reason_codes": _unique_codes(decision.no_trade_reason_codes, fallback_reason_codes)
        if action == "hold"
        else list(decision.no_trade_reason_codes),
        "invalidation_reason_codes": _unique_codes(
            decision.invalidation_reason_codes,
            ["STOP_WIDENING_FORBIDDEN"] if "STOP_WIDENING_FORBIDDEN" in fallback_reason_codes else [],
            ["HARD_STOP_REMOVAL_FORBIDDEN"] if "HARD_STOP_REMOVAL_FORBIDDEN" in fallback_reason_codes else [],
        ),
    }
    return decision.model_copy(update=update)


def bound_trade_decision(
    *,
    decision: TradeDecision,
    route: PromptRoutePolicy,
    ai_context: AIDecisionContextPacket | None,
    has_open_position: bool,
    open_position_side: str | None,
    current_stop_loss: float | None,
    losing_position: bool,
    provider_status: str,
) -> BoundedDecisionResult:
    bounded_output_applied = False
    fail_closed_applied = False
    fallback_reason_codes: list[str] = []
    abstain_reason_codes: list[str] = list(decision.abstain_reason_codes)
    current_profile = str(
        (ai_context.holding_profile if ai_context is not None else None) or decision.holding_profile or HOLDING_PROFILE_SCALP
    ).strip().lower() or HOLDING_PROFILE_SCALP
    normalized = decision.model_copy(update={"provider_status": provider_status})
    if (
        normalized.watch_entry_plan is not None
        and (not route.allow_new_entry or has_open_position or normalized.decision != "hold")
    ):
        bounded_output_applied = True
        fallback_reason_codes.append("WATCH_ENTRY_PLAN_NOT_ALLOWED_FOR_ROUTE")
        normalized = normalized.model_copy(update={"watch_entry_plan": None})

    if route.data_quality_hold_bias and ai_context is not None:
        if (
            ai_context.data_quality.data_quality_grade in {"degraded", "unavailable"}
            and normalized.decision in NEW_ENTRY_ACTIONS
        ):
            bounded_output_applied = True
            fallback_reason_codes.append("BREAKOUT_EXCEPTION_DATA_QUALITY_ABSTAIN")
            abstain_reason_codes.append("BREAKOUT_EXCEPTION_DATA_QUALITY_ABSTAIN")
            normalized = _fallback_decision(
                normalized,
                action="hold",
                fallback_reason_codes=fallback_reason_codes,
                abstain_reason_codes=abstain_reason_codes,
                current_profile=current_profile,
                provider_status=provider_status,
                fail_closed_applied=False,
            )

    if normalized.decision not in route.allowed_actions:
        bounded_output_applied = True
        if normalized.decision in NEW_ENTRY_ACTIONS and not route.allow_new_entry:
            fallback_reason_codes.append(
                "ENGINE_FORBIDS_NEW_ENTRY" if route.strategy_engine == "protection_reduce_engine" else "INVALID_ACTION_FOR_TRIGGER"
            )
        else:
            fallback_reason_codes.append("INVALID_ACTION_FOR_TRIGGER")
        if route.fail_closed and normalized.decision in NEW_ENTRY_ACTIONS:
            fail_closed_applied = True
            fallback_reason_codes.append("AI_BOUNDED_REJECTION")
            abstain_reason_codes.append("AI_BOUNDED_REJECTION")
        normalized = _fallback_decision(
            normalized,
            action=route.safe_fallback_action,
            fallback_reason_codes=fallback_reason_codes,
            abstain_reason_codes=abstain_reason_codes,
            current_profile=current_profile,
            provider_status=provider_status,
            fail_closed_applied=fail_closed_applied,
        )

    proposed_profile = str(normalized.recommended_holding_profile or normalized.holding_profile or "").strip().lower()
    allowed_profiles = set(route.allowed_recommended_holding_profiles)
    profile_allowed = proposed_profile in allowed_profiles or (
        normalized.decision == "hold" and proposed_profile == "hold_current"
    )
    if proposed_profile and not profile_allowed:
        bounded_output_applied = True
        fallback_reason_codes.append("INVALID_HOLDING_PROFILE_FOR_ENGINE")
        if route.strategy_engine == "breakout_exception_engine" and normalized.decision in NEW_ENTRY_ACTIONS:
            normalized = normalized.model_copy(
                update={
                    "holding_profile": HOLDING_PROFILE_SCALP,
                    "recommended_holding_profile": HOLDING_PROFILE_SCALP,
                    "bounded_output_applied": True,
                    "fallback_reason_codes": _unique_codes(
                        list(normalized.fallback_reason_codes),
                        fallback_reason_codes,
                    ),
                    "provider_status": provider_status,
                }
            )
        else:
            if route.fail_closed and normalized.decision in NEW_ENTRY_ACTIONS:
                fail_closed_applied = True
                fallback_reason_codes.append("AI_BOUNDED_REJECTION")
                abstain_reason_codes.append("AI_BOUNDED_REJECTION")
            normalized = _fallback_decision(
                normalized,
                action=route.safe_fallback_action,
                fallback_reason_codes=fallback_reason_codes,
                abstain_reason_codes=abstain_reason_codes,
                current_profile=current_profile,
                provider_status=provider_status,
                fail_closed_applied=fail_closed_applied,
            )

    proposed_profile = str(normalized.recommended_holding_profile or normalized.holding_profile or "").strip().lower()
    if has_open_position and losing_position and proposed_profile in LONG_HORIZON_PROFILES and current_profile != proposed_profile:
        bounded_output_applied = True
        fallback_reason_codes.append("LOSER_PROFILE_UPGRADE_FORBIDDEN")
        normalized = _fallback_decision(
            normalized,
            action=route.safe_fallback_action,
            fallback_reason_codes=fallback_reason_codes,
            abstain_reason_codes=abstain_reason_codes,
            current_profile=current_profile,
            provider_status=provider_status,
            fail_closed_applied=fail_closed_applied,
        )

    if _is_stop_widening(
        current_stop_loss=current_stop_loss,
        proposed_stop_loss=normalized.stop_loss,
        side=_decision_side(decision=normalized, open_position_side=open_position_side),
    ):
        bounded_output_applied = True
        fallback_reason_codes.append("STOP_WIDENING_FORBIDDEN")
        normalized = _fallback_decision(
            normalized,
            action=route.safe_fallback_action,
            fallback_reason_codes=fallback_reason_codes,
            abstain_reason_codes=abstain_reason_codes,
            current_profile=current_profile,
            provider_status=provider_status,
            fail_closed_applied=fail_closed_applied,
        )

    if bounded_output_applied and not normalized.bounded_output_applied:
        normalized = normalized.model_copy(
            update={
                "bounded_output_applied": True,
                "fallback_reason_codes": _unique_codes(list(normalized.fallback_reason_codes), fallback_reason_codes),
                "fail_closed_applied": fail_closed_applied,
                "provider_status": provider_status,
                "abstain_reason_codes": _unique_codes(list(normalized.abstain_reason_codes), abstain_reason_codes),
            }
        )

    return BoundedDecisionResult(
        decision=normalized,
        bounded_output_applied=bounded_output_applied or normalized.bounded_output_applied,
        fallback_reason_codes=tuple(_unique_codes(fallback_reason_codes, list(normalized.fallback_reason_codes))),
        fail_closed_applied=fail_closed_applied or normalized.fail_closed_applied,
        abstain_reason_codes=tuple(_unique_codes(abstain_reason_codes, list(normalized.abstain_reason_codes))),
    )
