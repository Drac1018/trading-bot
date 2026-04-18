from __future__ import annotations

from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_mvp.config import get_settings
from trading_mvp.models import Order, RiskCheck, Setting
from trading_mvp.schemas import (
    MarketSnapshotPayload,
    MetaGateResult,
    RiskCheckResult,
    TradeDecision,
)
from trading_mvp.services.account import (
    get_latest_pnl_snapshot,
    get_open_position,
    get_open_positions,
)
from trading_mvp.services.adaptive_signal import ADAPTIVE_SETUP_DISABLE_REASON_CODE
from trading_mvp.services.binance import BinanceClient
from trading_mvp.services.drawdown_state import (
    STATE_ADJUSTMENT_REASON_CODES,
    build_drawdown_state_snapshot,
)
from trading_mvp.services.holding_profile import (
    HOLDING_PROFILE_POSITION,
    HOLDING_PROFILE_SCALP,
    HOLDING_PROFILE_SWING,
    deterministic_stop_management_payload,
    resolve_holding_profile_cadence_hint,
    resolve_holding_profile_management_policy,
    resolve_holding_profile_risk_policy,
)
from trading_mvp.services.runtime_state import (
    DEGRADED_MANAGE_ONLY_STATE,
    EMERGENCY_EXIT_STATE,
    PROTECTION_REQUIRED_STATE,
    build_sync_freshness_summary,
    get_drawdown_state_detail,
    get_operating_state,
    get_reconciliation_blocking_reason_codes,
    get_reconciliation_detail,
)
from trading_mvp.services.settings import (
    get_exposure_limits,
    get_limited_live_max_notional,
    get_rollout_mode,
    get_runtime_credentials,
    is_live_execution_armed,
    rollout_mode_allows_exchange_submit,
)

HARD_MAX_GLOBAL_LEVERAGE = 5.0
HARD_MAX_RISK_PER_TRADE = 0.02
HARD_MAX_DAILY_LOSS = 0.05
BTC_SYMBOLS = {"BTCUSDT"}
MAJOR_ALT_SYMBOLS = {"ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT"}
SYNC_BLOCKING_REASON_CODES = {
    "account": "ACCOUNT_STATE_STALE",
    "positions": "POSITION_STATE_STALE",
    "open_orders": "OPEN_ORDERS_STATE_STALE",
    "protective_orders": "PROTECTION_STATE_UNVERIFIED",
}
MARKET_BLOCKING_REASON_CODES = {
    "stale": "MARKET_STATE_STALE",
    "incomplete": "MARKET_STATE_INCOMPLETE",
}
SURVIVAL_PATH_DECISIONS = {"reduce", "exit"}
IMMEDIATE_ENTRY_ALLOWED_RATIONALE_CODES = frozenset({"PENDING_ENTRY_PLAN_TRIGGERED"})
AUTO_RESIZE_REASON_CODE_MAP = {
    "gross_exposure_headroom_notional": "ENTRY_CLAMPED_TO_GROSS_EXPOSURE_LIMIT",
    "directional_headroom_notional": "ENTRY_CLAMPED_TO_DIRECTIONAL_LIMIT",
    "single_position_headroom_notional": "ENTRY_CLAMPED_TO_SINGLE_POSITION_LIMIT",
    "same_tier_headroom_notional": "ENTRY_CLAMPED_TO_SAME_TIER_LIMIT",
}
AUTO_RESIZE_HEADROOM_REASON_MAP = {
    "gross_exposure_headroom_notional": "CLAMPED_TO_GROSS_EXPOSURE_HEADROOM",
    "directional_headroom_notional": "CLAMPED_TO_DIRECTIONAL_HEADROOM",
    "single_position_headroom_notional": "CLAMPED_TO_SINGLE_POSITION_HEADROOM",
    "same_tier_headroom_notional": "CLAMPED_TO_SAME_TIER_HEADROOM",
}
DECISION_AGREEMENT_DISAGREEMENT_REASON_CODE = "DETERMINISTIC_BASELINE_DISAGREEMENT"
SETUP_CLUSTER_DISABLED_REASON_CODE = "SETUP_CLUSTER_DISABLED"
META_GATE_SOFT_PASS_REASON_CODE = "META_GATE_SOFT_PASS"
DECISION_AGREEMENT_MULTIPLIERS = {
    "full_agreement": {
        "risk_pct_multiplier": 1.0,
        "leverage_multiplier": 1.0,
        "notional_multiplier": 1.0,
    },
    "partial_agreement": {
        "risk_pct_multiplier": 0.75,
        "leverage_multiplier": 0.85,
        "notional_multiplier": 0.7,
    },
    "disagreement": {
        "risk_pct_multiplier": 0.0,
        "leverage_multiplier": 0.0,
        "notional_multiplier": 0.0,
    },
}
META_GATE_REJECT_REASON_CODES = {
    "META_GATE_LOW_HIT_PROBABILITY",
    "META_GATE_NEGATIVE_EXPECTANCY",
    "META_GATE_ADVERSE_SIGNED_SLIPPAGE",
    "META_GATE_LEAD_LAG_DIVERGENCE",
    "META_GATE_DERIVATIVES_HEADWIND",
    "META_GATE_WEAK_BREADTH",
    "META_GATE_TRANSITIONAL_BREADTH",
    "META_GATE_BREADTH_COUNTER_BIAS",
}
DRAWDOWN_BREAKOUT_DISABLED_REASON_CODE = "DRAWDOWN_STATE_BREAKOUT_RESTRICTED"
DRAWDOWN_PYRAMIDING_REQUIRES_WINNER_REASON_CODE = "DRAWDOWN_STATE_PYRAMIDING_REQUIRES_WINNER"
ADD_ON_REQUIRES_WINNING_POSITION_REASON_CODE = "ADD_ON_REQUIRES_WINNING_POSITION"
ADD_ON_PROTECTIVE_STOP_REQUIRED_REASON_CODE = "ADD_ON_PROTECTIVE_STOP_REQUIRED"
ADD_ON_TREND_ALIGNMENT_REQUIRED_REASON_CODE = "ADD_ON_TREND_ALIGNMENT_REQUIRED"
ADD_ON_BREADTH_VETO_REASON_CODE = "ADD_ON_BREADTH_VETO"
ADD_ON_LEAD_LAG_VETO_REASON_CODE = "ADD_ON_LEAD_LAG_VETO"
ADD_ON_DERIVATIVES_VETO_REASON_CODE = "ADD_ON_DERIVATIVES_VETO"
ADD_ON_SPREAD_HEADWIND_REASON_CODE = "ADD_ON_SPREAD_HEADWIND"
ADD_ON_RISK_DOWNSIZED_REASON_CODE = "ADD_ON_RISK_DOWNSIZED"
ADD_ON_SPREAD_HEADWIND_BPS = 7.0
ADD_ON_RISK_MULTIPLIER = 0.7
ADD_ON_LEVERAGE_MULTIPLIER = 0.9
ADD_ON_NOTIONAL_MULTIPLIER = 0.6
ADD_ON_HIGH_R_MULTIPLIER = 0.78
ADD_ON_HIGH_R_THRESHOLD = 1.0
PORTFOLIO_SLOT_SOFT_CAP_REASON_CODE = "PORTFOLIO_SLOT_SOFT_CAP"
HOLDING_PROFILE_SWING_SOFT_CAP_REASON_CODE = "HOLDING_PROFILE_SWING_SOFT_CAP"
HOLDING_PROFILE_POSITION_SOFT_CAP_REASON_CODE = "HOLDING_PROFILE_POSITION_SOFT_CAP"
HOLDING_PROFILE_REQUIRES_META_GATE_PASS_REASON_CODE = "HOLDING_PROFILE_REQUIRES_META_GATE_PASS"
HOLDING_PROFILE_SWING_REQUIRES_INTRADAY_ALIGNMENT_REASON_CODE = "HOLDING_PROFILE_SWING_REQUIRES_INTRADAY_ALIGNMENT"
HOLDING_PROFILE_SWING_DERIVATIVES_HEADWIND_REASON_CODE = "HOLDING_PROFILE_SWING_DERIVATIVES_HEADWIND"
HOLDING_PROFILE_POSITION_REQUIRES_STRONG_REGIME_REASON_CODE = "HOLDING_PROFILE_POSITION_REQUIRES_STRONG_REGIME"
HOLDING_PROFILE_POSITION_BREADTH_WEAK_REASON_CODE = "HOLDING_PROFILE_POSITION_BREADTH_WEAK"
HOLDING_PROFILE_POSITION_LEAD_LAG_MISMATCH_REASON_CODE = "HOLDING_PROFILE_POSITION_LEAD_LAG_MISMATCH"
HOLDING_PROFILE_POSITION_RELATIVE_STRENGTH_WEAK_REASON_CODE = "HOLDING_PROFILE_POSITION_RELATIVE_STRENGTH_WEAK"
HOLDING_PROFILE_POSITION_DERIVATIVES_HEADWIND_REASON_CODE = "HOLDING_PROFILE_POSITION_DERIVATIVES_HEADWIND"
HOLDING_PROFILE_BREAKOUT_SCALP_ONLY_REASON_CODE = "HOLDING_PROFILE_BREAKOUT_SCALP_ONLY"
FINAL_ORDER_STATUSES = frozenset({"filled", "canceled", "cancelled", "rejected", "expired"})
PROTECTIVE_ORDER_TYPE_PREFIXES = ("stop", "take_profit", "trailing_stop")
EXPOSURE_LIMIT_REASON_SPECS = (
    ("gross_exposure_pct_equity", "gross_exposure_pct", "GROSS_EXPOSURE_LIMIT_REACHED"),
    ("decision_symbol_concentration_pct", "largest_position_pct", "LARGEST_POSITION_LIMIT_REACHED"),
    ("same_tier_concentration_pct", "same_tier_concentration_pct", "SAME_TIER_CONCENTRATION_LIMIT_REACHED"),
)


def validate_decision_schema(payload: dict[str, Any]) -> TradeDecision:
    return TradeDecision.model_validate(payload)


def is_survival_path_decision(decision: TradeDecision | str) -> bool:
    value = decision.decision if isinstance(decision, TradeDecision) else str(decision)
    return value in SURVIVAL_PATH_DECISIONS


def _market_freshness_reason_codes(market_snapshot: MarketSnapshotPayload) -> list[str]:
    reason_codes: list[str] = []
    if market_snapshot.is_stale:
        reason_codes.append(MARKET_BLOCKING_REASON_CODES["stale"])
    if not market_snapshot.is_complete:
        reason_codes.append(MARKET_BLOCKING_REASON_CODES["incomplete"])
    return reason_codes


def _sync_freshness_reason_codes(sync_freshness_summary: dict[str, Any]) -> list[str]:
    reason_codes: list[str] = []
    for scope, reason_code in SYNC_BLOCKING_REASON_CODES.items():
        scope_summary = sync_freshness_summary.get(scope)
        if not isinstance(scope_summary, dict):
            reason_codes.append(reason_code)
            continue
        if bool(scope_summary.get("stale")) or bool(scope_summary.get("incomplete")):
            reason_codes.append(reason_code)
    return reason_codes


def _entry_price(decision: TradeDecision, market_snapshot: MarketSnapshotPayload) -> float:
    if decision.entry_zone_min is not None and decision.entry_zone_max is not None:
        return (decision.entry_zone_min + decision.entry_zone_max) / 2
    return market_snapshot.latest_price


def _entry_zone_bounds(decision: TradeDecision, market_snapshot: MarketSnapshotPayload) -> tuple[float, float]:
    entry_min = decision.entry_zone_min if decision.entry_zone_min is not None else market_snapshot.latest_price
    entry_max = decision.entry_zone_max if decision.entry_zone_max is not None else market_snapshot.latest_price
    if entry_min > entry_max:
        return entry_max, entry_min
    return entry_min, entry_max


def _round_float(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 6)


def _coerce_float(value: object, default: float = 0.0) -> float:
    if value in {None, ""}:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_float(value: object) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _decision_agreement_context(decision_context: dict[str, Any] | None) -> dict[str, Any]:
    payload = (
        decision_context.get("decision_agreement")
        if isinstance(decision_context, dict) and isinstance(decision_context.get("decision_agreement"), dict)
        else {}
    )
    level = str(payload.get("level") or "full_agreement")
    if level not in DECISION_AGREEMENT_MULTIPLIERS:
        level = "full_agreement"
    ai_used = bool(payload.get("ai_used", False))
    multiplier_profile = DECISION_AGREEMENT_MULTIPLIERS[level]
    if not ai_used:
        multiplier_profile = DECISION_AGREEMENT_MULTIPLIERS["full_agreement"]
    return {
        "ai_used": ai_used,
        "comparison_source": str(payload.get("comparison_source") or "unknown"),
        "level": level,
        "direction_match": bool(payload.get("direction_match", False)),
        "entry_mode_match": bool(payload.get("entry_mode_match", False)),
        "baseline_decision": payload.get("baseline_decision"),
        "baseline_entry_mode": payload.get("baseline_entry_mode"),
        "final_decision": payload.get("final_decision"),
        "final_entry_mode": payload.get("final_entry_mode"),
        "risk_pct_multiplier": float(multiplier_profile["risk_pct_multiplier"]),
        "leverage_multiplier": float(multiplier_profile["leverage_multiplier"]),
        "notional_multiplier": float(multiplier_profile["notional_multiplier"]),
        "applies_soft_limit": ai_used and level != "full_agreement",
    }


def _setup_cluster_state_context(decision_context: dict[str, Any] | None) -> dict[str, Any]:
    payload = (
        decision_context.get("setup_cluster_state")
        if isinstance(decision_context, dict) and isinstance(decision_context.get("setup_cluster_state"), dict)
        else {}
    )
    active = bool(payload.get("active", False))
    cooldown_active = bool(payload.get("cooldown_active", active))
    return {
        "matched": bool(payload.get("matched", False)),
        "active": active,
        "cooldown_active": cooldown_active,
        "status": payload.get("status") or ("active_disabled" if cooldown_active else "monitoring"),
        "recovery_trigger": payload.get("recovery_trigger"),
        "cluster_key": payload.get("cluster_key"),
        "disable_reason_codes": list(payload.get("disable_reason_codes", []))
        if isinstance(payload.get("disable_reason_codes"), list)
        else [],
        "disabled_at": payload.get("disabled_at"),
        "cooldown_expires_at": payload.get("cooldown_expires_at"),
        "recovery_condition": payload.get("recovery_condition")
        if isinstance(payload.get("recovery_condition"), dict)
        else {},
        "metrics": payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {},
        "thresholds": payload.get("thresholds") if isinstance(payload.get("thresholds"), dict) else {},
        "regime": payload.get("regime"),
        "trend_alignment": payload.get("trend_alignment"),
        "scenario": payload.get("scenario"),
        "entry_mode": payload.get("entry_mode"),
    }


def _recent_performance_suppression_context(
    decision_context: dict[str, Any] | None,
    decision: TradeDecision,
) -> dict[str, Any]:
    payload = (
        decision_context.get("suppression_context")
        if isinstance(decision_context, dict) and isinstance(decision_context.get("suppression_context"), dict)
        else {}
    )
    if payload:
        level = str(payload.get("level") or "none")
        reason_codes = [
            str(item)
            for item in payload.get("reason_codes", [])
            if item not in {None, ""}
        ] if isinstance(payload.get("reason_codes"), list) else []
        sources = [
            str(item)
            for item in payload.get("sources", [])
            if item not in {None, ""}
        ] if isinstance(payload.get("sources"), list) else []
        return {
            "level": level,
            "sources": sources,
            "reason_codes": list(dict.fromkeys(reason_codes)),
            "applies_hard_block": bool(payload.get("applies_hard_block", level == "hard_block")),
            "applies_risk_haircut": bool(payload.get("applies_risk_haircut", level in {"hard_block", "risk_haircut"})),
            "applies_soft_bias": bool(payload.get("applies_soft_bias", level == "soft_bias")),
            "hold_bias": float(payload.get("hold_bias", 0.0) or 0.0),
            "confidence_after_adjustment": float(
                payload.get("confidence_after_adjustment", getattr(decision, "confidence", 0.0)) or 0.0
            ),
            "risk_pct_after_adjustment": float(
                payload.get("risk_pct_after_adjustment", getattr(decision, "risk_pct", 0.0)) or 0.0
            ),
            "source": "decision_context",
        }

    setup_cluster_state = _setup_cluster_state_context(decision_context)
    fallback_reason_codes: list[str] = []
    fallback_sources: list[str] = []
    level = "none"
    if ADAPTIVE_SETUP_DISABLE_REASON_CODE in decision.rationale_codes:
        fallback_reason_codes.append(ADAPTIVE_SETUP_DISABLE_REASON_CODE)
        fallback_sources.append("adaptive_setup_disable")
        level = "hard_block"
    if setup_cluster_state["active"]:
        fallback_reason_codes.append(SETUP_CLUSTER_DISABLED_REASON_CODE)
        fallback_sources.append("setup_cluster_disable")
        level = "hard_block"
    if level == "none" and any(
        code in decision.rationale_codes for code in {"ADAPTIVE_HOLD_BIAS", "ADAPTIVE_SIGNAL_UNDERPERFORMING"}
    ):
        fallback_reason_codes.extend(["ADAPTIVE_HOLD_BIAS", "ADAPTIVE_SIGNAL_UNDERPERFORMING"])
        fallback_sources.append("adaptive_hold_bias")
        level = "soft_bias"
    return {
        "level": level,
        "sources": list(dict.fromkeys(fallback_sources)),
        "reason_codes": list(dict.fromkeys(fallback_reason_codes)),
        "applies_hard_block": level == "hard_block",
        "applies_risk_haircut": level in {"hard_block", "risk_haircut"},
        "applies_soft_bias": level == "soft_bias",
        "hold_bias": 0.0,
        "confidence_after_adjustment": float(getattr(decision, "confidence", 0.0) or 0.0),
        "risk_pct_after_adjustment": float(getattr(decision, "risk_pct", 0.0) or 0.0),
        "source": "fallback_from_decision",
    }


def _meta_gate_context(decision_context: dict[str, Any] | None) -> dict[str, Any]:
    payload = (
        decision_context.get("meta_gate")
        if isinstance(decision_context, dict) and isinstance(decision_context.get("meta_gate"), dict)
        else {}
    )
    try:
        meta_gate = MetaGateResult.model_validate(payload)
    except Exception:
        meta_gate = MetaGateResult()
    gate_decision = str(meta_gate.gate_decision or "pass")
    if gate_decision not in {"pass", "soft_pass", "reject"}:
        gate_decision = "pass"
    reject_reason_codes = [
        str(code)
        for code in meta_gate.reject_reason_codes
        if str(code or "") in META_GATE_REJECT_REASON_CODES
    ]
    if gate_decision == "pass":
        reject_reason_codes = []
    return {
        "gate_decision": gate_decision,
        "expected_hit_probability": float(meta_gate.expected_hit_probability),
        "expected_time_to_profit_minutes": meta_gate.expected_time_to_profit_minutes,
        "reject_reason_codes": reject_reason_codes,
        "confidence_adjustment": float(meta_gate.confidence_adjustment),
        "risk_multiplier": float(meta_gate.risk_multiplier),
        "leverage_multiplier": float(meta_gate.leverage_multiplier),
        "notional_multiplier": float(meta_gate.notional_multiplier),
        "components": dict(meta_gate.components),
        "applies_soft_limit": gate_decision == "soft_pass",
        "applies_block": gate_decision == "reject" and bool(reject_reason_codes),
    }


def _slot_allocation_context(decision_context: dict[str, Any] | None) -> dict[str, Any]:
    payload = (
        decision_context.get("slot_allocation")
        if isinstance(decision_context, dict) and isinstance(decision_context.get("slot_allocation"), dict)
        else {}
    )
    assigned_slot = str(payload.get("assigned_slot") or "")
    if assigned_slot not in {"slot_1", "slot_2", "slot_3"}:
        assigned_slot = ""
    slot_label = str(payload.get("slot_label") or "") or None
    candidate_weight = _optional_float(payload.get("candidate_weight"))
    if candidate_weight is None:
        candidate_weight = _optional_float(payload.get("portfolio_weight"))
    risk_pct_multiplier = _optional_float(payload.get("risk_pct_multiplier"))
    leverage_multiplier = _optional_float(payload.get("leverage_multiplier"))
    notional_multiplier = _optional_float(payload.get("notional_multiplier"))
    if not assigned_slot:
        risk_pct_multiplier = 1.0
        leverage_multiplier = 1.0
        notional_multiplier = 1.0
    return {
        "assigned_slot": assigned_slot or None,
        "slot_label": slot_label,
        "candidate_weight": candidate_weight if candidate_weight is not None else 0.0,
        "slot_conviction_score": _optional_float(payload.get("slot_conviction_score")),
        "meta_gate_probability": _optional_float(payload.get("meta_gate_probability")),
        "agreement_alignment_score": _optional_float(payload.get("agreement_alignment_score")),
        "agreement_level_hint": str(payload.get("agreement_level_hint") or "") or None,
        "execution_quality_score": _optional_float(payload.get("execution_quality_score")),
        "capacity_reason": str(payload.get("capacity_reason") or "") or None,
        "selected_reason": str(payload.get("selected_reason") or "") or None,
        "risk_pct_multiplier": max(risk_pct_multiplier if risk_pct_multiplier is not None else 1.0, 0.0),
        "leverage_multiplier": max(leverage_multiplier if leverage_multiplier is not None else 1.0, 0.0),
        "notional_multiplier": max(notional_multiplier if notional_multiplier is not None else 1.0, 0.0),
        "applies_soft_limit": bool(assigned_slot),
    }


def _holding_profile_context(
    decision: TradeDecision,
    decision_context: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = (
        decision_context.get("holding_profile_context")
        if isinstance(decision_context, dict) and isinstance(decision_context.get("holding_profile_context"), dict)
        else {}
    )
    profile = str(payload.get("holding_profile") or decision.holding_profile or HOLDING_PROFILE_SCALP).strip().lower()
    if profile not in {HOLDING_PROFILE_SCALP, HOLDING_PROFILE_SWING, HOLDING_PROFILE_POSITION}:
        profile = HOLDING_PROFILE_SCALP
    hard_stop_active = bool(payload.get("hard_stop_active", decision.stop_loss is not None))
    stop_management = deterministic_stop_management_payload(hard_stop_active=hard_stop_active)
    risk_policy = resolve_holding_profile_risk_policy(profile)
    cadence_hint = payload.get("cadence_hint") if isinstance(payload.get("cadence_hint"), dict) else resolve_holding_profile_cadence_hint(profile)
    management_policy = payload.get("management_policy") if isinstance(payload.get("management_policy"), dict) else resolve_holding_profile_management_policy(profile)
    return {
        "holding_profile": profile,
        "holding_profile_reason": str(
            payload.get("holding_profile_reason")
            or decision.holding_profile_reason
            or "scalp_default_intraday_bias"
        ),
        "structural_alignment_strong": bool(payload.get("structural_alignment_strong", False)),
        "intraday_alignment_ok": bool(payload.get("intraday_alignment_ok", profile == HOLDING_PROFILE_SCALP)),
        "breadth_not_weak": bool(payload.get("breadth_not_weak", profile == HOLDING_PROFILE_SCALP)),
        "lead_lag_positive": bool(payload.get("lead_lag_positive", profile == HOLDING_PROFILE_SCALP)),
        "relative_strength_positive": bool(payload.get("relative_strength_positive", profile == HOLDING_PROFILE_SCALP)),
        "derivatives_headwind_severe": bool(payload.get("derivatives_headwind_severe", False)),
        "position_profile_eligible": bool(payload.get("position_profile_eligible", profile == HOLDING_PROFILE_POSITION)),
        "swing_profile_eligible": bool(payload.get("swing_profile_eligible", profile in {HOLDING_PROFILE_SWING, HOLDING_PROFILE_POSITION})),
        "breadth_regime": str(payload.get("breadth_regime") or ""),
        "strategy_engine": str(payload.get("strategy_engine") or ""),
        "risk_policy": risk_policy,
        "cadence_hint": dict(cadence_hint),
        "management_policy": dict(management_policy),
        **stop_management,
    }


def _position_management_metadata(position: Any) -> dict[str, Any]:
    metadata = position.metadata_json if position is not None and isinstance(position.metadata_json, dict) else {}
    management = metadata.get("position_management")
    if isinstance(management, dict):
        return dict(management)
    return {}


def _position_initial_risk_per_unit(position: Any) -> float | None:
    management = _position_management_metadata(position)
    initial_risk = _optional_float(management.get("initial_risk_per_unit"))
    if initial_risk is not None and initial_risk > 0:
        return initial_risk
    entry_price = _optional_float(getattr(position, "entry_price", None))
    initial_stop = _optional_float(management.get("initial_stop_loss"))
    if entry_price is None or initial_stop is None:
        initial_stop = _optional_float(getattr(position, "stop_loss", None))
    if entry_price is None or initial_stop is None:
        return None
    initial_risk = abs(entry_price - initial_stop)
    return initial_risk if initial_risk > 0 else None


def _position_current_r_multiple(position: Any) -> float | None:
    management = _position_management_metadata(position)
    current_r_multiple = _optional_float(management.get("current_r_multiple"))
    if current_r_multiple is not None:
        return current_r_multiple
    initial_risk_per_unit = _position_initial_risk_per_unit(position)
    entry_price = _optional_float(getattr(position, "entry_price", None))
    mark_price = _optional_float(getattr(position, "mark_price", None))
    side = str(getattr(position, "side", "") or "").lower()
    if (
        initial_risk_per_unit is None
        or initial_risk_per_unit <= 0
        or entry_price is None
        or mark_price is None
        or side not in {"long", "short"}
    ):
        return None
    move = mark_price - entry_price if side == "long" else entry_price - mark_price
    return move / initial_risk_per_unit


def _protective_stop_progress_r(position: Any) -> float | None:
    initial_risk_per_unit = _position_initial_risk_per_unit(position)
    entry_price = _optional_float(getattr(position, "entry_price", None))
    stop_loss = _optional_float(getattr(position, "stop_loss", None))
    side = str(getattr(position, "side", "") or "").lower()
    if (
        initial_risk_per_unit is None
        or initial_risk_per_unit <= 0
        or entry_price is None
        or stop_loss is None
        or side not in {"long", "short"}
    ):
        return None
    if side == "long":
        return (stop_loss - entry_price) / initial_risk_per_unit
    return (entry_price - stop_loss) / initial_risk_per_unit


def _add_on_context(
    *,
    decision: TradeDecision,
    market_snapshot: MarketSnapshotPayload,
    existing_position: Any,
    decision_context: dict[str, Any] | None,
    meta_gate: dict[str, Any],
) -> dict[str, Any]:
    payload = (
        decision_context.get("add_on_context")
        if isinstance(decision_context, dict) and isinstance(decision_context.get("add_on_context"), dict)
        else {}
    )
    current_r_multiple = _optional_float(payload.get("current_r_multiple"))
    if current_r_multiple is None:
        current_r_multiple = _position_current_r_multiple(existing_position)
    existing_unrealized_pnl = _optional_float(getattr(existing_position, "unrealized_pnl", None))
    protected_r_multiple = _optional_float(payload.get("protected_r_multiple"))
    if protected_r_multiple is None:
        protected_r_multiple = _protective_stop_progress_r(existing_position)
    protective_stop_ready = bool(payload.get("protective_stop_ready", False))
    if not protective_stop_ready:
        protective_stop_ready = bool(
            protected_r_multiple is not None
            and (
                protected_r_multiple >= 0.0
                or (
                    current_r_multiple is not None
                    and current_r_multiple >= ADD_ON_HIGH_R_THRESHOLD
                    and protected_r_multiple >= -0.25
                )
            )
        )
    spread_bps = _optional_float(payload.get("spread_bps"))
    if spread_bps is None:
        spread_bps = _optional_float(getattr(market_snapshot.derivatives_context, "spread_bps", None))
    spread_headwind = bool(payload.get("spread_headwind", False))
    if not spread_headwind:
        spread_headwind = spread_bps is not None and spread_bps >= ADD_ON_SPREAD_HEADWIND_BPS
    breadth_veto = bool(payload.get("breadth_veto", False))
    lead_lag_veto = bool(payload.get("lead_lag_veto", False))
    derivatives_veto = bool(payload.get("derivatives_veto", False))
    meta_gate_reason_codes = {
        str(code)
        for code in meta_gate.get("reject_reason_codes", [])
        if code not in {None, ""}
    }
    if not breadth_veto:
        breadth_veto = any(
            code in {"META_GATE_WEAK_BREADTH", "META_GATE_TRANSITIONAL_BREADTH", "META_GATE_BREADTH_COUNTER_BIAS"}
            for code in meta_gate_reason_codes
        )
    if not lead_lag_veto:
        lead_lag_veto = "LEAD_MARKET_DIVERGENCE" in decision.rationale_codes or "META_GATE_LEAD_LAG_DIVERGENCE" in meta_gate_reason_codes
    if not derivatives_veto:
        derivatives_veto = bool(
            "DERIVATIVES_ALIGNMENT_HEADWIND" in decision.rationale_codes
            or "BREAKOUT_OI_SPREAD_FILTER" in decision.rationale_codes
            or "META_GATE_DERIVATIVES_HEADWIND" in meta_gate_reason_codes
        )
    trend_alignment_ok_payload = payload.get("trend_alignment_ok")
    if isinstance(trend_alignment_ok_payload, bool):
        trend_alignment_ok = trend_alignment_ok_payload
    elif decision.decision == "long":
        trend_alignment_ok = bool(
            {"TREND_UP", "ALIGNED_PULLBACK", "BULLISH_CONTINUATION_PULLBACK", "STRUCTURE_BREAKOUT_UP_EXCEPTION"}
            & set(decision.rationale_codes)
        ) or meta_gate.get("gate_decision") == "pass"
    else:
        trend_alignment_ok = bool(
            {"TREND_DOWN", "ALIGNED_PULLBACK", "BEARISH_CONTINUATION_REBOUND", "STRUCTURE_BREAKOUT_DOWN_EXCEPTION"}
            & set(decision.rationale_codes)
        ) or meta_gate.get("gate_decision") == "pass"
    current_position_notional = None
    if existing_position is not None:
        quantity = _optional_float(getattr(existing_position, "quantity", None))
        reference_price = _optional_float(getattr(existing_position, "mark_price", None)) or _optional_float(
            getattr(existing_position, "entry_price", None)
        )
        if quantity is not None and reference_price is not None and quantity > 0 and reference_price > 0:
            current_position_notional = quantity * reference_price
    strong_winner = bool(
        existing_unrealized_pnl is not None
        and existing_unrealized_pnl > 0
        and current_r_multiple is not None
        and current_r_multiple >= ADD_ON_HIGH_R_THRESHOLD
    )
    add_on_reason = "winner_only_add_on"
    if strong_winner and protective_stop_ready:
        add_on_reason = "winner_only_add_on_protected_runner"
    return {
        "current_r_multiple": current_r_multiple,
        "existing_unrealized_pnl": existing_unrealized_pnl,
        "protected_r_multiple": protected_r_multiple,
        "protective_stop_ready": protective_stop_ready,
        "trend_alignment_ok": trend_alignment_ok,
        "spread_bps": spread_bps,
        "spread_headwind": spread_headwind,
        "breadth_veto": breadth_veto,
        "lead_lag_veto": lead_lag_veto,
        "derivatives_veto": derivatives_veto,
        "current_position_notional": current_position_notional,
        "add_on_reason": add_on_reason,
        "risk_pct_multiplier": ADD_ON_HIGH_R_MULTIPLIER if strong_winner else ADD_ON_RISK_MULTIPLIER,
        "leverage_multiplier": ADD_ON_LEVERAGE_MULTIPLIER,
        "notional_multiplier": ADD_ON_HIGH_R_MULTIPLIER if strong_winner else ADD_ON_NOTIONAL_MULTIPLIER,
    }


def _entry_trigger_evaluation(
    decision: TradeDecision,
    market_snapshot: MarketSnapshotPayload,
) -> tuple[list[str], dict[str, Any]]:
    if decision.decision not in {"long", "short"}:
        return [], {}

    latest_price = market_snapshot.latest_price
    entry_price = _entry_price(decision, market_snapshot)
    entry_min, entry_max = _entry_zone_bounds(decision, market_snapshot)
    invalidation_price = decision.invalidation_price
    mode = decision.entry_mode or "none"
    last_candle = market_snapshot.candles[-1] if market_snapshot.candles else None
    reason_codes: list[str] = []
    invalidation_valid = True
    chase_bps: float | None = None
    chase_limit_exceeded = False
    breakout_confirmed: bool | None = None
    pullback_confirmed: bool | None = None
    immediate_allowed: bool | None = None

    if invalidation_price is None or invalidation_price <= 0:
        invalidation_valid = False
        reason_codes.append("INVALID_INVALIDATION_PRICE")
    elif decision.decision == "long":
        if invalidation_price >= min(entry_price, latest_price):
            invalidation_valid = False
            reason_codes.append("INVALID_INVALIDATION_PRICE")
    elif invalidation_price <= max(entry_price, latest_price):
        invalidation_valid = False
        reason_codes.append("INVALID_INVALIDATION_PRICE")

    if decision.max_chase_bps is not None:
        if decision.decision == "long":
            chase_anchor = max(entry_price, entry_max)
            chase_bps = max(((latest_price - chase_anchor) / max(chase_anchor, 1.0)) * 10_000, 0.0)
        else:
            chase_anchor = min(entry_price, entry_min)
            chase_bps = max(((chase_anchor - latest_price) / max(chase_anchor, 1.0)) * 10_000, 0.0)
        if chase_bps > decision.max_chase_bps:
            chase_limit_exceeded = True
            reason_codes.append("CHASE_LIMIT_EXCEEDED")

    trigger_met = True
    if mode == "immediate":
        immediate_allowed = bool(set(decision.rationale_codes) & IMMEDIATE_ENTRY_ALLOWED_RATIONALE_CODES)
        trigger_met = immediate_allowed
        if not trigger_met:
            reason_codes.append("ENTRY_TRIGGER_NOT_MET")
    elif mode == "breakout_confirm":
        if decision.decision == "long":
            breakout_confirmed = latest_price >= entry_max or (last_candle is not None and last_candle.high >= entry_max)
        else:
            breakout_confirmed = latest_price <= entry_min or (last_candle is not None and last_candle.low <= entry_min)
        trigger_met = bool(breakout_confirmed)
        if not trigger_met:
            reason_codes.append("ENTRY_TRIGGER_NOT_MET")
    elif mode == "pullback_confirm":
        pullback_confirmed = entry_min <= latest_price <= entry_max
        trigger_met = bool(pullback_confirmed)
        if not trigger_met:
            reason_codes.append("ENTRY_TRIGGER_NOT_MET")
    else:
        trigger_met = False
        reason_codes.append("ENTRY_TRIGGER_NOT_MET")

    detail = {
        "decision_side": decision.decision,
        "mode": mode,
        "latest_price": _round_float(latest_price),
        "entry_price": _round_float(entry_price),
        "entry_zone_min": _round_float(entry_min),
        "entry_zone_max": _round_float(entry_max),
        "invalidation_price": _round_float(invalidation_price),
        "invalidation_valid": invalidation_valid,
        "max_chase_bps": _round_float(decision.max_chase_bps),
        "observed_chase_bps": _round_float(chase_bps),
        "chase_limit_exceeded": chase_limit_exceeded,
        "breakout_confirmed": breakout_confirmed,
        "pullback_confirmed": pullback_confirmed,
        "immediate_allowed": immediate_allowed,
        "trigger_met": trigger_met,
        "last_candle_high": _round_float(last_candle.high if last_candle is not None else None),
        "last_candle_low": _round_float(last_candle.low if last_candle is not None else None),
        "reason_codes": list(dict.fromkeys(reason_codes)),
    }
    return detail["reason_codes"], detail


def get_symbol_risk_tier(symbol: str) -> Literal["btc", "major_alt", "alt"]:
    normalized = symbol.upper()
    if normalized in BTC_SYMBOLS:
        return "btc"
    if normalized in MAJOR_ALT_SYMBOLS:
        return "major_alt"
    return "alt"


def get_symbol_leverage_cap(symbol: str) -> float:
    tier = get_symbol_risk_tier(symbol)
    if tier == "btc":
        return 5.0
    if tier == "major_alt":
        return 3.0
    return 2.0


def _effective_leverage_cap(settings_row: Setting, symbol: str) -> float:
    return min(HARD_MAX_GLOBAL_LEVERAGE, settings_row.max_leverage, get_symbol_leverage_cap(symbol))


def _position_notional(quantity: float, price: float) -> float:
    return abs(quantity) * max(price, 0.0)


def _decision_matches_position_side(position_side: str, decision: str) -> bool:
    return (position_side == "long" and decision == "long") or (position_side == "short" and decision == "short")


def _estimate_projected_notional(
    decision: TradeDecision,
    market_snapshot: MarketSnapshotPayload,
    *,
    equity: float,
    approved_risk_pct: float,
    approved_leverage: float,
) -> float:
    entry_price = _entry_price(decision, market_snapshot)
    safe_entry_price = max(entry_price, 1.0)
    if decision.stop_loss is None:
        quantity = max((equity * min(approved_leverage, 1.0)) / safe_entry_price, 0.0001)
        return _position_notional(quantity, safe_entry_price)
    per_unit_risk = abs(entry_price - decision.stop_loss)
    if per_unit_risk == 0:
        return 0.0
    risk_budget = max(equity, 0.0) * approved_risk_pct
    max_notional_quantity = (max(equity, 0.0) * approved_leverage) / safe_entry_price
    quantity = min(risk_budget / per_unit_risk, max_notional_quantity)
    return _position_notional(quantity, safe_entry_price)


def _estimate_projected_entry_size(
    decision: TradeDecision,
    market_snapshot: MarketSnapshotPayload,
    *,
    equity: float,
    approved_risk_pct: float,
    approved_leverage: float,
) -> dict[str, float]:
    entry_price = _entry_price(decision, market_snapshot)
    safe_entry_price = max(entry_price, 1.0)
    if decision.stop_loss is None:
        quantity = max((equity * min(approved_leverage, 1.0)) / safe_entry_price, 0.0001)
    else:
        per_unit_risk = abs(entry_price - decision.stop_loss)
        if per_unit_risk == 0:
            quantity = 0.0
        else:
            risk_budget = max(equity, 0.0) * approved_risk_pct
            max_notional_quantity = (max(equity, 0.0) * approved_leverage) / safe_entry_price
            quantity = min(risk_budget / per_unit_risk, max_notional_quantity)
    notional = _position_notional(quantity, safe_entry_price)
    return {
        "entry_price": round(safe_entry_price, 6),
        "quantity": round(max(quantity, 0.0), 6),
        "notional": round(max(notional, 0.0), 6),
    }


def _minimum_actionable_notional(entry_price: float) -> float:
    return round(max(25.0, max(entry_price, 1.0) * 0.0005), 6)


def _build_risk_exchange_client(settings_row: Setting) -> BinanceClient:
    return BinanceClient(
        testnet_enabled=settings_row.binance_testnet_enabled,
        futures_enabled=settings_row.binance_futures_enabled,
    )


def _normalize_entry_size_for_risk(
    settings_row: Setting,
    *,
    symbol: str,
    quantity: float,
    reference_price: float,
    approved_notional: float | None = None,
    exchange_client: Any | None = None,
    enable_exchange_filters: bool = True,
) -> dict[str, Any]:
    safe_reference_price = max(reference_price, 1.0)
    fallback_min_notional = _minimum_actionable_notional(safe_reference_price)
    fallback_filters = {
        "tick_size": 0.0,
        "step_size": 0.0,
        "min_qty": 0.0,
        "min_notional": fallback_min_notional,
    }
    filter_source = "heuristic_fallback"
    filter_lookup_error: str | None = None
    client = exchange_client

    if client is None and enable_exchange_filters:
        try:
            client = _build_risk_exchange_client(settings_row)
        except Exception as exc:
            filter_lookup_error = str(exc)
            client = None

    if client is not None and hasattr(client, "normalize_order_request"):
        try:
            normalized = dict(
                client.normalize_order_request(
                    symbol=symbol,
                    quantity=quantity,
                    reference_price=safe_reference_price,
                    approved_notional=approved_notional,
                    enforce_min_notional=True,
                    close_position=False,
                )
            )
            normalized["filter_source"] = "exchange_filters"
            if filter_lookup_error:
                normalized["filter_lookup_error"] = filter_lookup_error
            return normalized
        except Exception as exc:
            filter_lookup_error = str(exc)

    filters = dict(fallback_filters)
    if client is not None and hasattr(client, "get_symbol_filters"):
        try:
            candidate_filters = client.get_symbol_filters(symbol)
            if isinstance(candidate_filters, dict):
                filters = {
                    "tick_size": _coerce_float(candidate_filters.get("tick_size")),
                    "step_size": _coerce_float(candidate_filters.get("step_size")),
                    "min_qty": _coerce_float(candidate_filters.get("min_qty")),
                    "min_notional": _coerce_float(candidate_filters.get("min_notional")),
                }
                filter_source = "exchange_filters"
        except Exception as exc:
            filter_lookup_error = str(exc)

    step_size = max(_coerce_float(filters.get("step_size")), 0.0)
    min_qty = max(_coerce_float(filters.get("min_qty")), 0.0)
    min_notional = max(_coerce_float(filters.get("min_notional")), 0.0)
    normalized_quantity = max(abs(quantity), 0.0)
    if step_size > 0:
        normalized_quantity = BinanceClient._quantize(normalized_quantity, step_size)
    if approved_notional is not None and approved_notional > 0 and safe_reference_price > 0:
        max_quantity = approved_notional / safe_reference_price
        if step_size > 0:
            max_quantity = BinanceClient._quantize(max_quantity, step_size)
        normalized_quantity = min(normalized_quantity, max(max_quantity, 0.0))
    notional = normalized_quantity * safe_reference_price if normalized_quantity > 0 else 0.0
    reason_code: str | None = None
    if normalized_quantity <= 0:
        reason_code = "ORDER_QTY_ZERO_AFTER_STEP_SIZE"
    elif min_qty > 0 and normalized_quantity < min_qty:
        reason_code = "ORDER_QTY_BELOW_MIN_QTY"
    elif min_notional > 0 and notional < min_notional:
        reason_code = "ORDER_NOTIONAL_BELOW_MIN_NOTIONAL"

    normalized = {
        "symbol": symbol.upper(),
        "quantity": round(normalized_quantity, 6),
        "reference_price": round(safe_reference_price, 6),
        "notional": round(max(notional, 0.0), 6),
        "filters": filters,
        "reason_code": reason_code,
        "filter_source": filter_source,
    }
    if filter_lookup_error:
        normalized["filter_lookup_error"] = filter_lookup_error
    return normalized


def _minimum_actionable_entry_size(
    *,
    entry_price: float,
    normalization_payload: dict[str, Any],
) -> dict[str, Any]:
    safe_entry_price = max(entry_price, 1.0)
    filters = normalization_payload.get("filters") if isinstance(normalization_payload.get("filters"), dict) else {}
    step_size = max(_coerce_float(filters.get("step_size")), 0.0)
    min_qty = max(_coerce_float(filters.get("min_qty")), 0.0)
    min_notional = max(_coerce_float(filters.get("min_notional")), 0.0)
    minimum_quantity = min_qty
    if min_notional > 0:
        quantity_from_notional = min_notional / safe_entry_price
        if step_size > 0:
            quantity_from_notional = BinanceClient._quantize_up(quantity_from_notional, step_size)
        minimum_quantity = max(minimum_quantity, quantity_from_notional)
    if minimum_quantity > 0 and step_size > 0:
        minimum_quantity = BinanceClient._quantize_up(minimum_quantity, step_size)
    minimum_notional = max(
        min_notional,
        minimum_quantity * safe_entry_price,
        _minimum_actionable_notional(safe_entry_price)
        if normalization_payload.get("filter_source") == "heuristic_fallback"
        else 0.0,
    )
    return {
        "quantity": round(max(minimum_quantity, 0.0), 6),
        "notional": round(max(minimum_notional, 0.0), 6),
        "filter_source": normalization_payload.get("filter_source"),
        "filter_lookup_error": normalization_payload.get("filter_lookup_error"),
        "filters": filters,
    }


def _has_non_resizable_entry_blockers(reason_codes: list[str]) -> bool:
    return any(str(code or "").strip() for code in reason_codes)


def _build_exposure_headroom_snapshot(
    *,
    exposure_metrics: dict[str, float],
    exposure_limits: dict[str, float],
    equity: float,
    decision_side: str,
) -> dict[str, float]:
    safe_equity = max(equity, 1.0)
    directional_metric_key = "long_exposure_pct_equity" if decision_side == "long" else "short_exposure_pct_equity"
    snapshot = {
        "gross_exposure_headroom_notional": max(
            exposure_limits["gross_exposure_pct"] - exposure_metrics["gross_exposure_pct_equity"],
            0.0,
        )
        * safe_equity,
        "directional_headroom_notional": max(
            exposure_limits["directional_bias_pct"] - exposure_metrics[directional_metric_key],
            0.0,
        )
        * safe_equity,
        "single_position_headroom_notional": max(
            exposure_limits["largest_position_pct"] - exposure_metrics["decision_symbol_concentration_pct"],
            0.0,
        )
        * safe_equity,
        "same_tier_headroom_notional": max(
            exposure_limits["same_tier_concentration_pct"] - exposure_metrics["same_tier_concentration_pct"],
            0.0,
        )
        * safe_equity,
    }
    limiting_key = min(snapshot, key=snapshot.get)
    snapshot["limiting_headroom_notional"] = round(snapshot[limiting_key], 6)
    return {key: round(value, 6) for key, value in snapshot.items()}


def _order_exposure_side(order: Order) -> Literal["long", "short"] | None:
    side = str(order.side or "").strip().lower()
    if side in {"buy", "long"}:
        return "long"
    if side in {"sell", "short"}:
        return "short"
    return None


def _is_protective_order_type(order_type: str | None) -> bool:
    normalized = str(order_type or "").strip().lower()
    return any(normalized.startswith(prefix) for prefix in PROTECTIVE_ORDER_TYPE_PREFIXES)


def _remaining_order_quantity(order: Order) -> float:
    return max(abs(order.requested_quantity) - abs(order.filled_quantity), 0.0)


def _order_reference_price(order: Order) -> float:
    if order.requested_price > 0:
        return order.requested_price
    if order.average_fill_price > 0:
        return order.average_fill_price
    return 0.0


def _is_exposure_reserving_order(order: Order) -> bool:
    if order.mode != "live":
        return False
    if str(order.status or "").strip().lower() in FINAL_ORDER_STATUSES:
        return False
    if order.reduce_only or order.close_only:
        return False
    if _is_protective_order_type(order.order_type):
        return False
    if _order_exposure_side(order) is None:
        return False
    return _remaining_order_quantity(order) > 0 and _order_reference_price(order) > 0


def _directional_metric_key(decision_side: str) -> str:
    return "long_exposure_pct_equity" if decision_side == "long" else "short_exposure_pct_equity"


def _current_directional_notional(exposure_metrics: dict[str, float], decision_side: str) -> float:
    metric_key = "long_notional" if decision_side == "long" else "short_notional"
    return float(exposure_metrics.get(metric_key, 0.0))


def _evaluate_exposure_limit_codes(
    *,
    exposure_metrics: dict[str, float],
    exposure_limits: dict[str, float],
    decision_side: str,
) -> list[str]:
    reason_codes: list[str] = []
    for metric_key, limit_key, reason_code in EXPOSURE_LIMIT_REASON_SPECS:
        if float(exposure_metrics.get(metric_key, 0.0)) > float(exposure_limits[limit_key]) + 1e-9:
            reason_codes.append(reason_code)
    directional_metric_key = _directional_metric_key(decision_side)
    if float(exposure_metrics.get(directional_metric_key, 0.0)) > float(exposure_limits["directional_bias_pct"]) + 1e-9:
        reason_codes.append("DIRECTIONAL_BIAS_LIMIT_REACHED")
    return list(dict.fromkeys(reason_codes))


def _build_exposure_metrics(
    session: Session,
    decision_symbol: str,
    equity: float,
    *,
    projected_side: str | None = None,
    projected_notional: float = 0.0,
) -> dict[str, float]:
    positions = get_open_positions(session)
    active_orders = list(
        session.scalars(
            select(Order).where(
                Order.mode == "live",
                Order.status.notin_(tuple(FINAL_ORDER_STATUSES)),
            )
        )
    )
    decision_tier = get_symbol_risk_tier(decision_symbol)
    total_notional = 0.0
    long_notional = 0.0
    short_notional = 0.0
    decision_symbol_notional = 0.0
    same_tier_notional = 0.0
    symbol_notionals: dict[str, float] = {}
    open_order_reserved_notional = 0.0
    open_order_long_reserved_notional = 0.0
    open_order_short_reserved_notional = 0.0
    open_order_symbol_reserved_notional = 0.0
    open_order_same_tier_reserved_notional = 0.0
    open_order_count = 0.0

    for position in positions:
        mark_price = position.mark_price if position.mark_price > 0 else position.entry_price
        notional = _position_notional(position.quantity, mark_price)
        total_notional += notional
        symbol_key = position.symbol.upper()
        symbol_notionals[symbol_key] = symbol_notionals.get(symbol_key, 0.0) + notional
        if position.side == "long":
            long_notional += notional
        else:
            short_notional += notional
        if symbol_key == decision_symbol.upper():
            decision_symbol_notional += notional
        if get_symbol_risk_tier(position.symbol) == decision_tier:
            same_tier_notional += notional

    for order in active_orders:
        if not _is_exposure_reserving_order(order):
            continue
        exposure_side = _order_exposure_side(order)
        if exposure_side is None:
            continue
        remaining_quantity = _remaining_order_quantity(order)
        reference_price = _order_reference_price(order)
        notional = _position_notional(remaining_quantity, reference_price)
        if notional <= 0:
            continue
        open_order_count += 1.0
        open_order_reserved_notional += notional
        total_notional += notional
        symbol_key = order.symbol.upper()
        symbol_notionals[symbol_key] = symbol_notionals.get(symbol_key, 0.0) + notional
        if exposure_side == "long":
            long_notional += notional
            open_order_long_reserved_notional += notional
        else:
            short_notional += notional
            open_order_short_reserved_notional += notional
        if symbol_key == decision_symbol.upper():
            decision_symbol_notional += notional
            open_order_symbol_reserved_notional += notional
        if get_symbol_risk_tier(order.symbol) == decision_tier:
            same_tier_notional += notional
            open_order_same_tier_reserved_notional += notional

    if projected_side in {"long", "short"} and projected_notional > 0:
        symbol_key = decision_symbol.upper()
        total_notional += projected_notional
        symbol_notionals[symbol_key] = symbol_notionals.get(symbol_key, 0.0) + projected_notional
        if projected_side == "long":
            long_notional += projected_notional
        else:
            short_notional += projected_notional
        decision_symbol_notional += projected_notional
        same_tier_notional += projected_notional

    safe_equity = max(equity, 1.0)
    dominant_side_notional = max(long_notional, short_notional)
    largest_symbol_notional = max(symbol_notionals.values(), default=0.0)
    return {
        "total_notional": round(total_notional, 6),
        "long_notional": round(long_notional, 6),
        "short_notional": round(short_notional, 6),
        "decision_symbol_notional": round(decision_symbol_notional, 6),
        "largest_symbol_notional": round(largest_symbol_notional, 6),
        "gross_exposure_pct_equity": round(total_notional / safe_equity, 6),
        "long_exposure_pct_equity": round(long_notional / safe_equity, 6),
        "short_exposure_pct_equity": round(short_notional / safe_equity, 6),
        "directional_bias_pct": round(dominant_side_notional / safe_equity, 6),
        "decision_symbol_concentration_pct": round(decision_symbol_notional / safe_equity, 6),
        "same_tier_concentration_pct": round(same_tier_notional / safe_equity, 6),
        "largest_position_pct_equity": round(largest_symbol_notional / safe_equity, 6),
        "projected_trade_notional_pct_equity": round(projected_notional / safe_equity, 6),
        "open_position_count": float(len(positions)),
        "open_order_reserved_notional": round(open_order_reserved_notional, 6),
        "open_order_long_reserved_notional": round(open_order_long_reserved_notional, 6),
        "open_order_short_reserved_notional": round(open_order_short_reserved_notional, 6),
        "decision_symbol_open_order_reserved_notional": round(open_order_symbol_reserved_notional, 6),
        "same_tier_open_order_reserved_notional": round(open_order_same_tier_reserved_notional, 6),
        "open_order_count": round(open_order_count, 6),
    }


def build_ai_risk_budget_context(
    session: Session,
    settings_row: Setting,
    *,
    decision_symbol: str,
    equity: float,
) -> dict[str, float]:
    symbol = decision_symbol.upper()
    limits = get_exposure_limits(settings_row)
    metrics = _build_exposure_metrics(session, symbol, equity)
    safe_equity = max(equity, 1.0)
    effective_leverage_cap = _effective_leverage_cap(settings_row, symbol)

    total_exposure_headroom = max(
        limits["gross_exposure_pct"] - float(metrics["gross_exposure_pct_equity"]),
        0.0,
    ) * safe_equity
    directional_long_headroom = max(
        limits["directional_bias_pct"] - float(metrics["long_exposure_pct_equity"]),
        0.0,
    ) * safe_equity
    directional_short_headroom = max(
        limits["directional_bias_pct"] - float(metrics["short_exposure_pct_equity"]),
        0.0,
    ) * safe_equity
    single_position_headroom = max(
        limits["largest_position_pct"] - float(metrics["decision_symbol_concentration_pct"]),
        0.0,
    ) * safe_equity

    max_additional_long_notional = min(total_exposure_headroom, directional_long_headroom)
    max_additional_short_notional = min(total_exposure_headroom, directional_short_headroom)
    max_new_position_notional_for_symbol = min(
        total_exposure_headroom,
        single_position_headroom,
        max(max_additional_long_notional, max_additional_short_notional),
    )

    return {
        "max_additional_long_notional": round(max(max_additional_long_notional, 0.0), 4),
        "max_additional_short_notional": round(max(max_additional_short_notional, 0.0), 4),
        "max_new_position_notional_for_symbol": round(max(max_new_position_notional_for_symbol, 0.0), 4),
        "max_leverage_for_symbol": round(effective_leverage_cap, 4),
        "directional_bias_headroom": round(max(max(directional_long_headroom, directional_short_headroom), 0.0), 4),
        "single_position_headroom": round(max(single_position_headroom, 0.0), 4),
        "total_exposure_headroom": round(max(total_exposure_headroom, 0.0), 4),
    }


def build_current_exposure_summary(
    session: Session,
    settings_row: Setting,
    *,
    equity: float,
    reference_symbol: str | None = None,
) -> dict[str, object]:
    symbol = (reference_symbol or settings_row.default_symbol).upper()
    limits = get_exposure_limits(settings_row)
    metrics = _build_exposure_metrics(session, symbol, equity)
    headroom = {
        "gross_exposure_pct": round(
            max(limits["gross_exposure_pct"] - metrics["gross_exposure_pct_equity"], 0.0),
            6,
        ),
        "largest_position_pct": round(
            max(limits["largest_position_pct"] - metrics["largest_position_pct_equity"], 0.0),
            6,
        ),
        "directional_bias_pct": round(
            max(limits["directional_bias_pct"] - metrics["directional_bias_pct"], 0.0),
            6,
        ),
        "same_tier_concentration_pct": round(
            max(
                limits["same_tier_concentration_pct"]
                - metrics["same_tier_concentration_pct"],
                0.0,
            ),
            6,
        ),
    }
    blocked = [
        headroom["gross_exposure_pct"] <= 0.0,
        headroom["largest_position_pct"] <= 0.0,
        headroom["directional_bias_pct"] <= 0.0,
        headroom["same_tier_concentration_pct"] <= 0.0,
    ]
    near_limit = [
        headroom["gross_exposure_pct"] < 0.1,
        headroom["largest_position_pct"] < 0.05,
        headroom["directional_bias_pct"] < 0.1,
        headroom["same_tier_concentration_pct"] < 0.1,
    ]
    status = "ok"
    if any(blocked):
        status = "at_limit"
    elif any(near_limit):
        status = "near_limit"
    return {
        "reference_symbol": symbol,
        "reference_tier": get_symbol_risk_tier(symbol),
        "metrics": metrics,
        "limits": limits,
        "headroom": headroom,
        "status": status,
    }


def evaluate_risk(
    session: Session,
    settings_row: Setting,
    decision: TradeDecision,
    market_snapshot: MarketSnapshotPayload,
    decision_run_id: int | None = None,
    market_snapshot_id: int | None = None,
    execution_mode: Literal["live", "historical_replay"] = "live",
    exchange_client: Any | None = None,
    decision_context: dict[str, Any] | None = None,
) -> tuple[RiskCheckResult, RiskCheck]:
    blocked_reason_codes: list[str] = []
    adjustment_reason_codes: list[str] = []
    defaults = get_settings()
    rollout_mode = get_rollout_mode(settings_row)
    live_requested = rollout_mode != "paper"
    operating_mode: Literal["live", "paused", "hold"] = "live"
    operating_state = get_operating_state(settings_row)
    existing_position = get_open_position(session, decision.symbol)
    is_protection_recovery = bool(
        existing_position is not None
        and operating_state in {PROTECTION_REQUIRED_STATE, DEGRADED_MANAGE_ONLY_STATE}
        and decision.decision in {"long", "short"}
        and _decision_matches_position_side(existing_position.side, decision.decision)
        and decision.stop_loss is not None
        and decision.take_profit is not None
    )
    is_entry_decision = decision.decision in {"long", "short"} and not is_protection_recovery
    latest_pnl = get_latest_pnl_snapshot(session, settings_row)
    drawdown_state = build_drawdown_state_snapshot(
        session,
        settings_row,
        current_detail=get_drawdown_state_detail(settings_row),
    )
    credentials = get_runtime_credentials(settings_row)
    symbol_risk_tier = get_symbol_risk_tier(decision.symbol)
    effective_leverage_cap = _effective_leverage_cap(settings_row, decision.symbol)
    effective_risk_cap = min(settings_row.max_risk_per_trade, HARD_MAX_RISK_PER_TRADE)
    effective_daily_loss_cap = min(settings_row.max_daily_loss, HARD_MAX_DAILY_LOSS)
    exposure_limits = get_exposure_limits(settings_row)
    raw_projected_notional = 0.0
    approved_projected_notional = 0.0
    approved_quantity: float | None = None
    auto_resized_entry = False
    size_adjustment_ratio = 0.0
    auto_resize_reason: str | None = None
    resized_projected_notional = 0.0
    resized_projected_quantity: float | None = None
    current_exposure_metrics = _build_exposure_metrics(
        session,
        decision.symbol,
        latest_pnl.equity,
    )
    exposure_metrics = current_exposure_metrics
    requested_exposure_metrics = current_exposure_metrics
    resized_exposure_metrics = current_exposure_metrics
    exposure_headroom_snapshot: dict[str, float] = {}
    raw_projected_quantity = 0.0
    requested_exchange_quantity: float | None = None
    requested_exchange_notional = 0.0
    requested_exchange_reason_code: str | None = None
    resized_exchange_reason_code: str | None = None
    exchange_minimums: dict[str, Any] = {}
    minimum_actionable_notional = 0.0
    minimum_actionable_quantity = 0.0
    max_additional_notional = 0.0
    requested_exposure_limit_codes: list[str] = []
    final_exposure_limit_codes: list[str] = []
    entry_trigger_debug: dict[str, Any] = {}
    decision_agreement = _decision_agreement_context(decision_context)
    setup_cluster_state = _setup_cluster_state_context(decision_context)
    suppression_context = _recent_performance_suppression_context(decision_context, decision)
    meta_gate = _meta_gate_context(decision_context)
    holding_profile = _holding_profile_context(decision, decision_context)
    holding_profile_name = str(holding_profile["holding_profile"])
    holding_profile_policy = (
        dict(holding_profile.get("risk_policy"))
        if isinstance(holding_profile.get("risk_policy"), dict)
        else resolve_holding_profile_risk_policy(holding_profile_name)
    )
    slot_allocation = _slot_allocation_context(decision_context)
    drawdown_policy = (
        dict(drawdown_state.get("policy_adjustments") or {})
        if isinstance(drawdown_state.get("policy_adjustments"), dict)
        else {}
    )
    drawdown_state_code = str(drawdown_state.get("current_drawdown_state") or "normal")
    same_side_pyramiding = bool(
        is_entry_decision
        and existing_position is not None
        and _decision_matches_position_side(existing_position.side, decision.decision)
    )
    add_on = _add_on_context(
        decision=decision,
        market_snapshot=market_snapshot,
        existing_position=existing_position,
        decision_context=decision_context,
        meta_gate=meta_gate,
    )
    agreement_adjusted_notional = 0.0
    agreement_adjusted_quantity: float | None = None
    agreement_block_reason_code: str | None = None
    reconciliation_summary = (
        get_reconciliation_detail(settings_row)
        if execution_mode != "historical_replay"
        else {}
    )
    if is_entry_decision:
        raw_size = _estimate_projected_entry_size(
            decision,
            market_snapshot,
            equity=latest_pnl.equity,
            approved_risk_pct=min(decision.risk_pct, effective_risk_cap),
            approved_leverage=min(decision.leverage, effective_leverage_cap),
        )
        raw_projected_notional = raw_size["notional"]
        raw_projected_quantity = raw_size["quantity"]
        requested_exchange_payload = _normalize_entry_size_for_risk(
            settings_row,
            symbol=decision.symbol,
            quantity=raw_projected_quantity,
            reference_price=raw_size["entry_price"],
            approved_notional=raw_projected_notional,
            exchange_client=exchange_client,
            enable_exchange_filters=execution_mode == "live" or exchange_client is not None,
        )
        requested_exchange_quantity = (
            _coerce_float(requested_exchange_payload.get("quantity"))
            if _coerce_float(requested_exchange_payload.get("quantity")) > 0
            else None
        )
        requested_exchange_notional = _coerce_float(requested_exchange_payload.get("notional"))
        requested_exchange_reason_code = (
            str(requested_exchange_payload.get("reason_code") or "").strip() or None
        )
        approved_projected_notional = requested_exchange_notional
        approved_quantity = requested_exchange_quantity
        resized_projected_notional = requested_exchange_notional
        resized_projected_quantity = approved_quantity
        minimum_actionable = _minimum_actionable_entry_size(
            entry_price=raw_size["entry_price"],
            normalization_payload=requested_exchange_payload,
        )
        minimum_actionable_notional = _coerce_float(minimum_actionable.get("notional"))
        minimum_actionable_quantity = _coerce_float(minimum_actionable.get("quantity"))
        exchange_minimums = {
            "filter_source": minimum_actionable.get("filter_source"),
            "filter_lookup_error": minimum_actionable.get("filter_lookup_error"),
            "tick_size": _round_float(_coerce_float(minimum_actionable.get("filters", {}).get("tick_size"))),
            "step_size": _round_float(_coerce_float(minimum_actionable.get("filters", {}).get("step_size"))),
            "min_qty": _round_float(_coerce_float(minimum_actionable.get("filters", {}).get("min_qty"))),
            "min_notional": _round_float(_coerce_float(minimum_actionable.get("filters", {}).get("min_notional"))),
            "minimum_actionable_quantity": _round_float(minimum_actionable_quantity),
            "minimum_actionable_notional": _round_float(minimum_actionable_notional),
            "requested_reason_code": requested_exchange_reason_code,
        }
        exposure_headroom_snapshot = _build_exposure_headroom_snapshot(
            exposure_metrics=current_exposure_metrics,
            exposure_limits=exposure_limits,
            equity=latest_pnl.equity,
            decision_side=decision.decision,
        )
        exposure_headroom_snapshot["minimum_actionable_notional"] = minimum_actionable_notional
        exposure_headroom_snapshot["minimum_actionable_quantity"] = minimum_actionable_quantity
        requested_exposure_metrics = _build_exposure_metrics(
            session,
            decision.symbol,
            latest_pnl.equity,
            projected_side=decision.decision,
            projected_notional=requested_exchange_notional if requested_exchange_reason_code is None else 0.0,
        )
        resized_exposure_metrics = requested_exposure_metrics
        exposure_metrics = requested_exposure_metrics
    sync_freshness_summary = build_sync_freshness_summary(settings_row)

    if settings_row.trading_paused and is_entry_decision:
        blocked_reason_codes.append("TRADING_PAUSED")
        operating_mode = "paused"
    if operating_state == PROTECTION_REQUIRED_STATE and is_entry_decision:
        blocked_reason_codes.append(PROTECTION_REQUIRED_STATE)
    if operating_state == DEGRADED_MANAGE_ONLY_STATE and is_entry_decision:
        blocked_reason_codes.append(DEGRADED_MANAGE_ONLY_STATE)
    if operating_state == EMERGENCY_EXIT_STATE and is_entry_decision:
        blocked_reason_codes.append(EMERGENCY_EXIT_STATE)
    if is_entry_decision:
        blocked_reason_codes.extend(_market_freshness_reason_codes(market_snapshot))
    if is_entry_decision and latest_pnl.daily_pnl < 0 and abs(latest_pnl.daily_pnl) / max(latest_pnl.equity, 1.0) >= effective_daily_loss_cap:
        blocked_reason_codes.append("DAILY_LOSS_LIMIT_REACHED")
    if latest_pnl.consecutive_losses >= settings_row.max_consecutive_losses and is_entry_decision:
        blocked_reason_codes.append("MAX_CONSECUTIVE_LOSSES_REACHED")
    if is_entry_decision and decision.leverage > effective_leverage_cap:
        blocked_reason_codes.append("LEVERAGE_EXCEEDS_LIMIT")
    if is_entry_decision and decision.risk_pct > effective_risk_cap:
        blocked_reason_codes.append("RISK_PCT_EXCEEDS_LIMIT")
    if is_entry_decision and (decision.stop_loss is None or decision.take_profit is None):
        blocked_reason_codes.append("MISSING_STOP_OR_TARGET")
    if is_entry_decision:
        entry_trigger_reason_codes, entry_trigger_debug = _entry_trigger_evaluation(decision, market_snapshot)
        blocked_reason_codes.extend(entry_trigger_reason_codes)
    if is_entry_decision and not is_protection_recovery and suppression_context["applies_hard_block"]:
        blocked_reason_codes.extend(list(suppression_context["reason_codes"]))
    if (
        is_entry_decision
        and decision_agreement["ai_used"]
        and decision_agreement["level"] == "disagreement"
    ):
        agreement_block_reason_code = DECISION_AGREEMENT_DISAGREEMENT_REASON_CODE
        blocked_reason_codes.append(agreement_block_reason_code)
    if is_entry_decision and meta_gate["applies_block"]:
        blocked_reason_codes.extend(list(meta_gate["reject_reason_codes"]))
    if is_entry_decision and holding_profile_name in {HOLDING_PROFILE_SWING, HOLDING_PROFILE_POSITION}:
        if bool(holding_profile_policy.get("require_meta_gate_pass", False)) and meta_gate["gate_decision"] != "pass":
            blocked_reason_codes.append(HOLDING_PROFILE_REQUIRES_META_GATE_PASS_REASON_CODE)
        if (
            not bool(holding_profile_policy.get("breakout_exception_allowed", True))
            and str(decision.entry_mode or "").lower() == "breakout_confirm"
        ):
            blocked_reason_codes.append(HOLDING_PROFILE_BREAKOUT_SCALP_ONLY_REASON_CODE)
        if holding_profile_name == HOLDING_PROFILE_SWING:
            if not bool(holding_profile.get("intraday_alignment_ok", False)):
                blocked_reason_codes.append(HOLDING_PROFILE_SWING_REQUIRES_INTRADAY_ALIGNMENT_REASON_CODE)
            if bool(holding_profile.get("derivatives_headwind_severe", False)):
                blocked_reason_codes.append(HOLDING_PROFILE_SWING_DERIVATIVES_HEADWIND_REASON_CODE)
        if holding_profile_name == HOLDING_PROFILE_POSITION:
            if not bool(holding_profile.get("structural_alignment_strong", False)):
                blocked_reason_codes.append(HOLDING_PROFILE_POSITION_REQUIRES_STRONG_REGIME_REASON_CODE)
            if not bool(holding_profile.get("breadth_not_weak", False)):
                blocked_reason_codes.append(HOLDING_PROFILE_POSITION_BREADTH_WEAK_REASON_CODE)
            if not bool(holding_profile.get("lead_lag_positive", False)):
                blocked_reason_codes.append(HOLDING_PROFILE_POSITION_LEAD_LAG_MISMATCH_REASON_CODE)
            if not bool(holding_profile.get("relative_strength_positive", False)):
                blocked_reason_codes.append(HOLDING_PROFILE_POSITION_RELATIVE_STRENGTH_WEAK_REASON_CODE)
            if bool(holding_profile.get("derivatives_headwind_severe", False)):
                blocked_reason_codes.append(HOLDING_PROFILE_POSITION_DERIVATIVES_HEADWIND_REASON_CODE)
    if (
        is_entry_decision
        and not bool(drawdown_policy.get("breakout_exception_allowed", True))
        and str(decision.entry_mode or "").lower() == "breakout_confirm"
    ):
        blocked_reason_codes.append(DRAWDOWN_BREAKOUT_DISABLED_REASON_CODE)
    if (
        is_entry_decision
        and same_side_pyramiding
        and bool(drawdown_policy.get("winner_only_pyramiding", False))
        and (existing_position.unrealized_pnl or 0.0) <= 0.0
    ):
        blocked_reason_codes.append(DRAWDOWN_PYRAMIDING_REQUIRES_WINNER_REASON_CODE)
    if is_entry_decision and same_side_pyramiding:
        if (
            add_on["existing_unrealized_pnl"] is None
            or add_on["existing_unrealized_pnl"] <= 0.0
            or add_on["current_r_multiple"] is None
            or add_on["current_r_multiple"] <= 0.0
        ):
            blocked_reason_codes.append(ADD_ON_REQUIRES_WINNING_POSITION_REASON_CODE)
        if not add_on["protective_stop_ready"]:
            blocked_reason_codes.append(ADD_ON_PROTECTIVE_STOP_REQUIRED_REASON_CODE)
        if not add_on["trend_alignment_ok"]:
            blocked_reason_codes.append(ADD_ON_TREND_ALIGNMENT_REQUIRED_REASON_CODE)
        if add_on["breadth_veto"]:
            blocked_reason_codes.append(ADD_ON_BREADTH_VETO_REASON_CODE)
        if add_on["lead_lag_veto"]:
            blocked_reason_codes.append(ADD_ON_LEAD_LAG_VETO_REASON_CODE)
        if add_on["derivatives_veto"]:
            blocked_reason_codes.append(ADD_ON_DERIVATIVES_VETO_REASON_CODE)
        if add_on["spread_headwind"]:
            blocked_reason_codes.append(ADD_ON_SPREAD_HEADWIND_REASON_CODE)
    if is_entry_decision and live_requested:
        blocked_reason_codes.extend(_sync_freshness_reason_codes(sync_freshness_summary))
    if is_entry_decision and live_requested:
        blocked_reason_codes.extend(get_reconciliation_blocking_reason_codes(settings_row))
    if is_entry_decision and requested_exchange_reason_code is not None:
        blocked_reason_codes.append("ENTRY_SIZE_BELOW_MIN_NOTIONAL")

    if is_protection_recovery and existing_position is not None:
        entry = existing_position.mark_price if existing_position.mark_price > 0 else existing_position.entry_price
    else:
        entry = _entry_price(decision, market_snapshot)
    if decision.decision == "long" and decision.stop_loss is not None and decision.take_profit is not None:
        if decision.stop_loss >= entry or decision.take_profit <= entry:
            blocked_reason_codes.append("INVALID_PROTECTION_BRACKETS" if is_protection_recovery else "INVALID_LONG_BRACKETS")
    if decision.decision == "short" and decision.stop_loss is not None and decision.take_profit is not None:
        if decision.stop_loss <= entry or decision.take_profit >= entry:
            blocked_reason_codes.append("INVALID_PROTECTION_BRACKETS" if is_protection_recovery else "INVALID_SHORT_BRACKETS")

    slippage = abs(entry - market_snapshot.latest_price) / max(market_snapshot.latest_price, 1.0)
    if slippage > settings_row.slippage_threshold_pct and is_entry_decision:
        blocked_reason_codes.append("SLIPPAGE_THRESHOLD_EXCEEDED")
    if decision.decision == "hold":
        blocked_reason_codes.append("HOLD_DECISION")
        operating_mode = "hold" if operating_mode != "paused" else operating_mode

    enforce_live_readiness = execution_mode != "historical_replay"
    if enforce_live_readiness and (not credentials.binance_api_key or not credentials.binance_api_secret):
        if decision.decision != "hold":
            blocked_reason_codes.append("LIVE_CREDENTIALS_MISSING")
    if enforce_live_readiness and is_entry_decision and live_requested:
        if not defaults.live_trading_env_enabled:
            blocked_reason_codes.append("LIVE_ENV_DISABLED")
        if not settings_row.manual_live_approval:
            blocked_reason_codes.append("LIVE_APPROVAL_POLICY_DISABLED")
        if not is_live_execution_armed(settings_row):
            blocked_reason_codes.append("LIVE_APPROVAL_REQUIRED")
    elif enforce_live_readiness and is_entry_decision:
        blocked_reason_codes.append("LIVE_TRADING_DISABLED")

    non_resizable_entry_blockers_present = _has_non_resizable_entry_blockers(blocked_reason_codes)
    if is_entry_decision:
        requested_exposure_limit_codes = _evaluate_exposure_limit_codes(
            exposure_metrics=requested_exposure_metrics,
            exposure_limits=exposure_limits,
            decision_side=decision.decision,
        )
        limiting_key = min(
            AUTO_RESIZE_REASON_CODE_MAP,
            key=lambda key: exposure_headroom_snapshot.get(key, 0.0),
        )
        max_additional_notional = max(exposure_headroom_snapshot.get(limiting_key, 0.0), 0.0)

        if requested_exposure_limit_codes:
            if not non_resizable_entry_blockers_present and max_additional_notional >= minimum_actionable_notional:
                resized_target_notional = min(requested_exchange_notional, max_additional_notional)
                resized_target_quantity = (
                    min(
                        requested_exchange_quantity or raw_projected_quantity,
                        resized_target_notional / max(_entry_price(decision, market_snapshot), 1.0),
                    )
                    if (requested_exchange_quantity or raw_projected_quantity) > 0
                    else 0.0
                )
                resized_exchange_payload = _normalize_entry_size_for_risk(
                    settings_row,
                    symbol=decision.symbol,
                    quantity=resized_target_quantity,
                    reference_price=raw_size["entry_price"],
                    approved_notional=resized_target_notional,
                    exchange_client=exchange_client,
                    enable_exchange_filters=execution_mode == "live" or exchange_client is not None,
                )
                resized_projected_notional = _coerce_float(resized_exchange_payload.get("notional"))
                resized_projected_quantity = (
                    _coerce_float(resized_exchange_payload.get("quantity"))
                    if _coerce_float(resized_exchange_payload.get("quantity")) > 0
                    else None
                )
                resized_exchange_reason_code = (
                    str(resized_exchange_payload.get("reason_code") or "").strip() or None
                )
                resized_exposure_metrics = _build_exposure_metrics(
                    session,
                    decision.symbol,
                    latest_pnl.equity,
                    projected_side=decision.decision,
                    projected_notional=resized_projected_notional,
                )
                exposure_metrics = resized_exposure_metrics
                if resized_exchange_reason_code is not None:
                    blocked_reason_codes.append("ENTRY_SIZE_BELOW_MIN_NOTIONAL")
                    approved_projected_notional = 0.0
                    approved_quantity = None
                else:
                    final_exposure_limit_codes = _evaluate_exposure_limit_codes(
                        exposure_metrics=resized_exposure_metrics,
                        exposure_limits=exposure_limits,
                        decision_side=decision.decision,
                    )
                    if final_exposure_limit_codes:
                        blocked_reason_codes.extend(final_exposure_limit_codes)
                        approved_projected_notional = 0.0
                        approved_quantity = None
                    else:
                        approved_projected_notional = resized_projected_notional
                        approved_quantity = (
                            resized_projected_quantity
                            if resized_projected_quantity is not None and resized_projected_quantity > 0
                            else None
                        )
                        if approved_projected_notional < requested_exchange_notional - 1e-9:
                            auto_resized_entry = True
                            size_adjustment_ratio = round(
                                approved_projected_notional / max(raw_projected_notional, 1e-9),
                                6,
                            )
                            auto_resize_reason = AUTO_RESIZE_HEADROOM_REASON_MAP[limiting_key]
                            adjustment_reason_codes.extend(["ENTRY_AUTO_RESIZED", AUTO_RESIZE_REASON_CODE_MAP[limiting_key]])
                        else:
                            size_adjustment_ratio = 1.0
            else:
                blocked_reason_codes.extend(requested_exposure_limit_codes)
                if max_additional_notional < minimum_actionable_notional:
                    blocked_reason_codes.append("ENTRY_SIZE_BELOW_MIN_NOTIONAL")
                approved_projected_notional = 0.0
                approved_quantity = None
                exposure_metrics = requested_exposure_metrics
        else:
            resized_exposure_metrics = requested_exposure_metrics
            exposure_metrics = requested_exposure_metrics

    combined_notional_multiplier = (
        float(decision_agreement["notional_multiplier"])
        * float(meta_gate["notional_multiplier"])
        * float(holding_profile_policy.get("notional_multiplier", 1.0))
        * float(drawdown_policy.get("notional_multiplier", 1.0))
        * (float(add_on["notional_multiplier"]) if same_side_pyramiding else 1.0)
        * (float(slot_allocation["notional_multiplier"]) if is_entry_decision else 1.0)
    )
    if (
        is_entry_decision
        and len(blocked_reason_codes) == 0
        and approved_projected_notional > 0
        and combined_notional_multiplier < 0.999999
    ):
        agreement_target_notional = approved_projected_notional * combined_notional_multiplier
        current_position_notional = _optional_float(add_on.get("current_position_notional"))
        if same_side_pyramiding and current_position_notional is not None and current_position_notional > 0:
            agreement_target_notional = min(agreement_target_notional, current_position_notional)
        agreement_target_quantity = (
            min(
                approved_quantity or raw_projected_quantity,
                agreement_target_notional / max(raw_size["entry_price"], 1.0),
            )
            if (approved_quantity or raw_projected_quantity) > 0
            else 0.0
        )
        agreement_payload = _normalize_entry_size_for_risk(
            settings_row,
            symbol=decision.symbol,
            quantity=agreement_target_quantity,
            reference_price=raw_size["entry_price"],
            approved_notional=agreement_target_notional,
            exchange_client=exchange_client,
            enable_exchange_filters=execution_mode == "live" or exchange_client is not None,
        )
        agreement_adjusted_notional = _coerce_float(agreement_payload.get("notional"))
        agreement_adjusted_quantity = (
            _coerce_float(agreement_payload.get("quantity"))
            if _coerce_float(agreement_payload.get("quantity")) > 0
            else None
        )
        agreement_reason_code = str(agreement_payload.get("reason_code") or "").strip() or None
        if agreement_reason_code is not None:
            blocked_reason_codes.append("ENTRY_SIZE_BELOW_MIN_NOTIONAL")
            approved_projected_notional = 0.0
            approved_quantity = None
        else:
            approved_projected_notional = agreement_adjusted_notional
            approved_quantity = agreement_adjusted_quantity
            exposure_metrics = _build_exposure_metrics(
                session,
                decision.symbol,
                latest_pnl.equity,
                projected_side=decision.decision,
                projected_notional=approved_projected_notional,
            )
            if meta_gate["applies_soft_limit"]:
                adjustment_reason_codes.append(META_GATE_SOFT_PASS_REASON_CODE)
            if holding_profile_name == HOLDING_PROFILE_SWING:
                adjustment_reason_codes.append(HOLDING_PROFILE_SWING_SOFT_CAP_REASON_CODE)
            elif holding_profile_name == HOLDING_PROFILE_POSITION:
                adjustment_reason_codes.append(HOLDING_PROFILE_POSITION_SOFT_CAP_REASON_CODE)
            if same_side_pyramiding:
                adjustment_reason_codes.append(ADD_ON_RISK_DOWNSIZED_REASON_CODE)
            if bool(slot_allocation.get("applies_soft_limit", False)):
                adjustment_reason_codes.append(PORTFOLIO_SLOT_SOFT_CAP_REASON_CODE)
            drawdown_adjustment_reason_code = STATE_ADJUSTMENT_REASON_CODES.get(drawdown_state_code)
            if drawdown_adjustment_reason_code is not None:
                adjustment_reason_codes.append(drawdown_adjustment_reason_code)

    blocked_reason_codes = list(dict.fromkeys(blocked_reason_codes))
    adjustment_reason_codes = list(dict.fromkeys(adjustment_reason_codes))
    reason_codes = list(blocked_reason_codes)
    resizable = bool(
        is_entry_decision
        and requested_exposure_limit_codes
        and not non_resizable_entry_blockers_present
        and max_additional_notional >= minimum_actionable_notional
    )
    allowed = len(blocked_reason_codes) == 0

    approved_risk_pct = 0.0
    approved_leverage = 0.0
    if allowed:
        if is_entry_decision and raw_projected_notional > 0:
            combined_risk_multiplier = (
                float(decision_agreement["risk_pct_multiplier"])
                * float(meta_gate["risk_multiplier"])
                * float(holding_profile_policy.get("risk_pct_multiplier", 1.0))
                * float(drawdown_policy.get("risk_pct_multiplier", 1.0))
                * (float(add_on["risk_pct_multiplier"]) if same_side_pyramiding else 1.0)
                * (float(slot_allocation["risk_pct_multiplier"]) if is_entry_decision else 1.0)
            )
            combined_leverage_multiplier = (
                float(decision_agreement["leverage_multiplier"])
                * float(meta_gate["leverage_multiplier"])
                * float(holding_profile_policy.get("leverage_multiplier", 1.0))
                * float(drawdown_policy.get("leverage_multiplier", 1.0))
                * (float(add_on["leverage_multiplier"]) if same_side_pyramiding else 1.0)
                * (float(slot_allocation["leverage_multiplier"]) if is_entry_decision else 1.0)
            )
            approved_risk_pct = round(
                min(
                    decision.risk_pct
                    * (approved_projected_notional / max(raw_projected_notional, 1e-9))
                    * combined_risk_multiplier,
                    effective_risk_cap,
                ),
                6,
            )
            approved_leverage = round(
                min(
                    decision.leverage * combined_leverage_multiplier,
                    effective_leverage_cap,
                ),
                6,
            )
            if same_side_pyramiding and existing_position is not None and existing_position.leverage > 0:
                approved_leverage = round(min(approved_leverage, float(existing_position.leverage)), 6)
        else:
            approved_risk_pct = decision.risk_pct
            approved_leverage = min(decision.leverage, effective_leverage_cap)
    sync_timestamp_debug = {
        "account_sync_at": (
            str(sync_freshness_summary.get("account", {}).get("last_sync_at"))
            if isinstance(sync_freshness_summary.get("account"), dict)
            and sync_freshness_summary.get("account", {}).get("last_sync_at") not in {None, ""}
            else None
        ),
        "positions_sync_at": (
            str(sync_freshness_summary.get("positions", {}).get("last_sync_at"))
            if isinstance(sync_freshness_summary.get("positions"), dict)
            and sync_freshness_summary.get("positions", {}).get("last_sync_at") not in {None, ""}
            else None
        ),
        "open_orders_sync_at": (
            str(sync_freshness_summary.get("open_orders", {}).get("last_sync_at"))
            if isinstance(sync_freshness_summary.get("open_orders"), dict)
            and sync_freshness_summary.get("open_orders", {}).get("last_sync_at") not in {None, ""}
            else None
        ),
        "protective_orders_sync_at": (
            str(sync_freshness_summary.get("protective_orders", {}).get("last_sync_at"))
            if isinstance(sync_freshness_summary.get("protective_orders"), dict)
            and sync_freshness_summary.get("protective_orders", {}).get("last_sync_at") not in {None, ""}
            else None
        ),
    }
    debug_payload = {
        "rollout_mode": rollout_mode,
        "exchange_submit_allowed": rollout_mode_allows_exchange_submit(settings_row),
        "limited_live_max_notional": (
            _round_float(get_limited_live_max_notional(settings_row)) if rollout_mode == "limited_live" else None
        ),
        "requested_notional": _round_float(raw_projected_notional),
        "requested_quantity": _round_float(raw_projected_quantity),
        "resized_notional": _round_float(resized_projected_notional),
        "resized_quantity": _round_float(resized_projected_quantity),
        "requested_exchange_notional": _round_float(requested_exchange_notional),
        "requested_exchange_quantity": _round_float(requested_exchange_quantity),
        "requested_exchange_reason_code": requested_exchange_reason_code,
        "resized_exchange_reason_code": resized_exchange_reason_code,
        "projected_symbol_notional": (
            _round_float(exposure_metrics.get("decision_symbol_notional", 0.0))
            if is_entry_decision
            else None
        ),
        "projected_directional_notional": (
            _round_float(_current_directional_notional(exposure_metrics, decision.decision))
            if is_entry_decision
            else None
        ),
        "current_symbol_notional": (
            _round_float(current_exposure_metrics.get("decision_symbol_notional", 0.0))
            if is_entry_decision
            else None
        ),
        "current_directional_notional": (
            _round_float(_current_directional_notional(current_exposure_metrics, decision.decision))
            if is_entry_decision
            else None
        ),
        "open_order_reserved_notional": _round_float(current_exposure_metrics.get("open_order_reserved_notional", 0.0)),
        "headroom": dict(exposure_headroom_snapshot),
        "requested_exposure_limit_codes": requested_exposure_limit_codes,
        "final_exposure_limit_codes": final_exposure_limit_codes,
        "exchange_minimums": exchange_minimums,
        "entry_trigger": entry_trigger_debug,
        "decision_agreement": {
            **decision_agreement,
            "agreement_adjusted_notional": _round_float(
                agreement_adjusted_notional if agreement_adjusted_notional > 0 else None
            ),
            "agreement_adjusted_quantity": _round_float(agreement_adjusted_quantity),
            "blocked_reason_code": agreement_block_reason_code,
        },
        "meta_gate": {
            **meta_gate,
            "soft_adjusted_notional": _round_float(
                agreement_adjusted_notional if meta_gate["applies_soft_limit"] and agreement_adjusted_notional > 0 else None
            ),
            "soft_adjusted_quantity": _round_float(
                agreement_adjusted_quantity if meta_gate["applies_soft_limit"] else None
            ),
        },
        "drawdown_state": {
            **drawdown_state,
            "same_side_pyramiding": same_side_pyramiding,
            "winner_only_pyramiding": bool(drawdown_policy.get("winner_only_pyramiding", False)),
            "breakout_exception_allowed": bool(drawdown_policy.get("breakout_exception_allowed", True)),
        },
        "holding_profile": {
            **holding_profile,
            "risk_policy": holding_profile_policy,
            "same_side_pyramiding": same_side_pyramiding,
            "meta_gate_decision": meta_gate["gate_decision"],
            "blocked_reason_codes": [
                code
                for code in blocked_reason_codes
                if code.startswith("HOLDING_PROFILE_")
            ],
        },
        "add_on": {
            **add_on,
            "same_side_pyramiding": same_side_pyramiding,
            "winner_only_required": same_side_pyramiding,
            "decision_agreement_level": decision_agreement["level"],
            "meta_gate_decision": meta_gate["gate_decision"],
            "drawdown_state": drawdown_state_code,
            "blocked_reason_codes": [
                code
                for code in blocked_reason_codes
                if code.startswith("ADD_ON_") or code == DRAWDOWN_PYRAMIDING_REQUIRES_WINNER_REASON_CODE
            ],
        },
        "slot_allocation": {
            **slot_allocation,
            "decision_agreement_level": decision_agreement["level"],
            "meta_gate_decision": meta_gate["gate_decision"],
            "drawdown_state": drawdown_state_code,
            "same_side_pyramiding": same_side_pyramiding,
        },
        "suppression_context": suppression_context,
        "setup_cluster_state": setup_cluster_state,
        "adaptive_setup_disable": {
            "active": ADAPTIVE_SETUP_DISABLE_REASON_CODE in suppression_context["reason_codes"],
            "reason_code": (
                ADAPTIVE_SETUP_DISABLE_REASON_CODE
                if ADAPTIVE_SETUP_DISABLE_REASON_CODE in suppression_context["reason_codes"]
                else None
            ),
        },
        "sync_timestamps": sync_timestamp_debug,
        "market_derivatives_context": market_snapshot.derivatives_context.model_dump(mode="json"),
        "reconciliation_state": {
            "position_mode": reconciliation_summary.get("position_mode"),
            "mode_guard_active": bool(reconciliation_summary.get("mode_guard_active", False)),
            "mode_guard_reason_code": reconciliation_summary.get("mode_guard_reason_code"),
            "guarded_symbols": [
                str(item)
                for item in reconciliation_summary.get("guarded_symbols", [])
                if item not in {None, ""}
            ]
            if isinstance(reconciliation_summary, dict)
            else [],
        },
    }
    result = RiskCheckResult(
        allowed=allowed,
        decision=decision.decision,
        reason_codes=reason_codes,
        blocked_reason_codes=blocked_reason_codes,
        adjustment_reason_codes=adjustment_reason_codes,
        approved_risk_pct=approved_risk_pct if allowed else 0.0,
        approved_leverage=approved_leverage if allowed else 0.0,
        raw_projected_notional=raw_projected_notional,
        approved_notional=approved_projected_notional if allowed else 0.0,
        approved_projected_notional=approved_projected_notional if allowed else 0.0,
        approved_qty=approved_quantity if allowed else None,
        approved_quantity=approved_quantity if allowed else None,
        resizable=resizable,
        auto_resized_entry=auto_resized_entry if allowed else False,
        size_adjustment_ratio=size_adjustment_ratio if allowed else 0.0,
        snapshot_id=market_snapshot_id,
        exposure_headroom_snapshot=exposure_headroom_snapshot,
        auto_resize_reason=auto_resize_reason if allowed else None,
        operating_mode=operating_mode if not allowed else "live",
        operating_state=operating_state,
        effective_leverage_cap=effective_leverage_cap,
        symbol_risk_tier=symbol_risk_tier,
        exposure_metrics=exposure_metrics,
        sync_freshness_summary=sync_freshness_summary,
        debug_payload=debug_payload,
    )
    row = RiskCheck(
        symbol=decision.symbol,
        decision_run_id=decision_run_id,
        market_snapshot_id=market_snapshot_id,
        allowed=result.allowed,
        decision=result.decision,
        reason_codes=result.blocked_reason_codes,
        approved_risk_pct=result.approved_risk_pct,
        approved_leverage=result.approved_leverage,
        payload=result.model_dump(mode="json"),
    )
    session.add(row)
    session.flush()
    return result, row
