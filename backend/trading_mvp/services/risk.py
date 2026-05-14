from __future__ import annotations

import json
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from trading_mvp.config import get_settings
from trading_mvp.enums import AgentRole
from trading_mvp.models import AgentRun, Execution, Order, Position, RiskCheck, Setting
from trading_mvp.schemas import (
    EventOperatorControlPayload,
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
from trading_mvp.services.audit import record_audit_event
from trading_mvp.services.binance import BinanceClient
from trading_mvp.services.cost_model import (
    DEFAULT_MIN_REQUIRED_NET_BPS,
    ENTRY_EXECUTION_TYPE_MARKETABLE,
    ENTRY_EXECUTION_TYPE_PASSIVE_LIMIT,
    ENTRY_EXECUTION_TYPE_UNKNOWN,
    build_recent_execution_cost_estimate,
    calculate_expected_trade_cost,
    normalize_entry_execution_type,
    resolve_cost_model_config,
)
from trading_mvp.services.drawdown_state import (
    STATE_ADJUSTMENT_REASON_CODES,
    build_drawdown_state_snapshot,
)
from trading_mvp.services.event_policy import derive_ai_event_view
from trading_mvp.services.holding_profile import (
    HOLDING_PROFILE_POSITION,
    HOLDING_PROFILE_SCALP,
    HOLDING_PROFILE_SWING,
    deterministic_stop_management_payload,
    resolve_holding_profile_cadence_hint,
    resolve_holding_profile_management_policy,
    resolve_holding_profile_risk_policy,
)
from trading_mvp.services.intent_semantics import is_survival_path_intent
from trading_mvp.services.range_mr_cooldown import (
    RANGE_MR_COOLDOWN_BLOCK_REASON_CODE,
    evaluate_range_mr_cooldown_gate,
)
from trading_mvp.services.runtime_state import (
    DEGRADED_MANAGE_ONLY_STATE,
    EMERGENCY_EXIT_STATE,
    PROTECTION_REQUIRED_STATE,
    build_sync_freshness_summary,
    derive_degraded_reason_codes,
    derive_protection_reason_codes,
    get_binance_rest_detail,
    get_binance_rest_entry_block_reason_code,
    get_drawdown_state_detail,
    get_operating_state,
    get_reconciliation_blocking_reason_codes,
    get_reconciliation_detail,
    sync_scope_blocks_new_entry,
)
from trading_mvp.services.settings import (
    SAFE_PROFILE_SELECTOR_DETAIL_KEY,
    build_event_operator_control_payload,
    get_exposure_limits,
    get_execution_risk_profile_policy,
    get_limited_live_max_notional,
    get_rollout_mode,
    get_runtime_credentials,
    is_live_execution_armed,
    rollout_mode_allows_exchange_submit,
)
from trading_mvp.time_utils import utcnow_aware, utcnow_naive

HARD_MAX_GLOBAL_LEVERAGE = 5.0
HARD_MAX_RISK_PER_TRADE = 0.02
HARD_MAX_DAILY_LOSS = 0.05
BTC_SYMBOLS = {"BTCUSDT"}
LEAD_MARKET_SYMBOLS = {"BTCUSDT", "ETHUSDT"}
HIGH_CORRELATION_MAJOR_SYMBOLS = {"BTCUSDT", "ETHUSDT"}
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
EVENT_POLICY_BLOCK_REASON_CODES = {
    "manual_no_trade_active",
    "operator_force_no_trade",
    "operator_bias_no_trade",
    "alignment_conflict_block",
}
EVENT_POLICY_APPROVAL_REASON_CODES = {
    "alignment_not_aligned",
    "alignment_insufficient_data",
}
MACRO_EVENT_RESULT_CONFLICT_REASON_CODE = "MACRO_EVENT_RESULT_CONFLICT"
MACRO_EVENT_RESULT_MIN_CONFLICT_CONFIDENCE = 0.25
AI_DECISION_EXPIRED_REASON_CODE = "AI_DECISION_EXPIRED"
AI_DECISION_MARKET_STALE_REASON_CODE = "AI_DECISION_MARKET_STALE"
AI_DECISION_MARKET_INCOMPLETE_REASON_CODE = "AI_DECISION_MARKET_INCOMPLETE"
AI_DECISION_PRICE_MOVE_INVALIDATED_REASON_CODE = "AI_DECISION_PRICE_MOVE_INVALIDATED"
AI_DECISION_VOLATILITY_SPIKE_REASON_CODE = "AI_DECISION_VOLATILITY_SPIKE"
AI_DECISION_REGIME_CHANGED_REASON_CODE = "AI_DECISION_REGIME_CHANGED"
AI_DECISION_RANGE_BREAK_INVALIDATED_REASON_CODE = "AI_DECISION_RANGE_BREAK_INVALIDATED"
AI_DECISION_SYNC_STATE_UNTRUSTED_REASON_CODE = "AI_DECISION_SYNC_STATE_UNTRUSTED"
AI_DECISION_INVALIDATION_REASON_CODES = {
    AI_DECISION_EXPIRED_REASON_CODE,
    AI_DECISION_MARKET_STALE_REASON_CODE,
    AI_DECISION_MARKET_INCOMPLETE_REASON_CODE,
    AI_DECISION_PRICE_MOVE_INVALIDATED_REASON_CODE,
    AI_DECISION_VOLATILITY_SPIKE_REASON_CODE,
    AI_DECISION_REGIME_CHANGED_REASON_CODE,
    AI_DECISION_RANGE_BREAK_INVALIDATED_REASON_CODE,
    AI_DECISION_SYNC_STATE_UNTRUSTED_REASON_CODE,
}
EXECUTION_RISK_PROFILE_SEVERITY = {
    "NORMAL": 0,
    "CAUTION": 1,
    "HIGH_VOLATILITY": 2,
    "THIN_LIQUIDITY": 2,
    "STRESS": 3,
    "DEGRADED": 4,
}
EXECUTION_RISK_PROFILE_BLOCK_REASON_CODE = "EXECUTION_RISK_PROFILE_BLOCKED"
EXECUTION_RISK_PROFILE_BLOCKING_SEVERITY = 3
SAFE_PROFILE_AUTO_APPLY_MODES = {"off", "shadow", "conservative_only", "manual_approval"}
PROFILE_RELAXATION_CONSECUTIVE_REASON_CODE = "PROFILE_RELAXATION_REQUIRES_CONSECUTIVE_CONFIRMATIONS"
PROFILE_RELAXATION_DWELL_REASON_CODE = "PROFILE_RELAXATION_MIN_DWELL_NOT_MET"
PROFILE_RELAXATION_AI_VALIDITY_REASON_CODE = "PROFILE_RELAXATION_REQUIRES_VALID_AI_RECOMMENDATION"
PROFILE_RELAXATION_AI_CONFIDENCE_REASON_CODE = "PROFILE_RELAXATION_REQUIRES_AI_CONFIDENCE"
PROFILE_RELAXATION_HEALTH_REASON_CODE = "PROFILE_RELAXATION_REQUIRES_HEALTHY_STATE"
PROFILE_RELAXATION_DO_NOT_RELAX_REASON_CODE = "PROFILE_RELAXATION_DO_NOT_RELAX"
AI_MARKET_SETTINGS_RECOMMENDATION_EXPIRED_REASON_CODE = "AI_MARKET_SETTINGS_RECOMMENDATION_EXPIRED"
AI_MARKET_SETTINGS_RECOMMENDATION_LOW_CONFIDENCE_REASON_CODE = "AI_MARKET_SETTINGS_RECOMMENDATION_LOW_CONFIDENCE"
AI_MARKET_SETTINGS_RECOMMENDATION_UNKNOWN_PROFILE_REASON_CODE = "AI_MARKET_SETTINGS_RECOMMENDATION_UNKNOWN_PROFILE"
AI_MARKET_SETTINGS_RECOMMENDATION_SCOPE_MISMATCH_REASON_CODE = "AI_MARKET_SETTINGS_RECOMMENDATION_SCOPE_MISMATCH"
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
ALT_ENTRY_LEAD_CONTEXT_UNAVAILABLE_REASON_CODE = "ALT_ENTRY_LEAD_CONTEXT_UNAVAILABLE"
ADD_ON_RISK_DOWNSIZED_REASON_CODE = "ADD_ON_RISK_DOWNSIZED"
ADD_ON_SPREAD_HEADWIND_BPS = 7.0
ADD_ON_RISK_MULTIPLIER = 0.7
ADD_ON_LEVERAGE_MULTIPLIER = 0.9
ADD_ON_NOTIONAL_MULTIPLIER = 0.6
ADD_ON_HIGH_R_MULTIPLIER = 0.78
ADD_ON_HIGH_R_THRESHOLD = 1.0
EXPECTED_COST_EXCEEDS_EDGE_REASON_CODE = "EXPECTED_COST_EXCEEDS_EDGE"
EXPECTED_EDGE_MARGIN_TOO_THIN_REASON_CODE = "EXPECTED_EDGE_MARGIN_TOO_THIN"
EXPECTED_COST_UNAVAILABLE_REASON_CODE = "EXPECTED_COST_UNAVAILABLE"
EXPECTED_EDGE_MIN_NET_NOT_MET_REASON_CODE = "EXPECTED_EDGE_MIN_NET_NOT_MET"
EXPECTED_EDGE_COST_RATIO_TOO_HIGH_REASON_CODE = "EXPECTED_EDGE_COST_RATIO_TOO_HIGH"
ADVERSE_SLIPPAGE_TOO_HIGH_REASON_CODE = "ADVERSE_SLIPPAGE_TOO_HIGH"
FUNDING_HEADWIND_TOO_HIGH_REASON_CODE = "FUNDING_HEADWIND_TOO_HIGH"
MARKETABLE_ENTRY_COST_TOO_HIGH_REASON_CODE = "MARKETABLE_ENTRY_COST_TOO_HIGH"
EXPECTED_NET_BPS_TOO_LOW_REASON_CODE = "expected_net_bps_too_low"
FEE_TO_GROSS_RATIO_TOO_HIGH_REASON_CODE = "fee_to_gross_ratio_too_high"
EXPECTED_GROSS_BPS_TOO_TIGHT_REASON_CODE = "expected_gross_bps_too_tight"
BTC_LONG_TP_TOO_TIGHT_REASON_CODE = "btc_long_tp_too_tight"
MARKET_FALLBACK_NOT_ALLOWED_FOR_TIGHT_TP_REASON_CODE = "market_fallback_not_allowed_for_tight_tp"
MISSING_EXPECTED_PROFITABILITY_INPUTS_REASON_CODE = "missing_expected_profitability_inputs"
CONFIDENCE_BELOW_MIN_ENTRY_THRESHOLD_REASON_CODE = "confidence_below_min_entry_threshold"
PLANNED_RISK_REWARD_TOO_LOW_REASON_CODE = "PLANNED_RISK_REWARD_TOO_LOW"
SYMBOL_RECENT_PERFORMANCE_NEGATIVE_REASON_CODE = "SYMBOL_RECENT_PERFORMANCE_NEGATIVE"
EXPECTED_COST_ENTRY_MARKETABLE = ENTRY_EXECUTION_TYPE_MARKETABLE
EXPECTED_COST_ENTRY_PASSIVE_LIMIT = ENTRY_EXECUTION_TYPE_PASSIVE_LIMIT
EXPECTED_COST_ENTRY_UNKNOWN = ENTRY_EXECUTION_TYPE_UNKNOWN
EXPECTED_COST_MIN_EDGE_MARGIN_BPS = DEFAULT_MIN_REQUIRED_NET_BPS
EXPECTED_COST_ADVERSE_SLIPPAGE_ALERT_BPS = 12.0
EXPECTED_COST_FUNDING_HEADWIND_ALERT_BPS = 5.0
EXPECTED_PROFITABILITY_MIN_GROSS_BPS_DEFAULT = 40.0
EXPECTED_PROFITABILITY_MAX_FEE_TO_GROSS_RATIO = 0.30
EXPECTED_PROFITABILITY_MIN_CONFIDENCE_FOR_ENTRY = 0.70
EXPECTED_PROFITABILITY_TIGHT_TP_BPS = 40.0
EXPECTED_PROFITABILITY_REQUIRED_ORDER_POLICY = "limit_only_or_post_only"
EXPECTED_PROFITABILITY_SYMBOL_SIDE_MIN_GROSS_BPS = {
    "BTCUSDT:long": 40.0,
    "ETHUSDT:long": 0.0,
}
SAME_SYMBOL_SIDE_TP_COOLDOWN_REASON_CODE = "same_symbol_side_tp_cooldown"
RECENT_TP_REENTRY_EDGE_NOT_ENOUGH_REASON_CODE = "recent_tp_reentry_edge_not_enough"
RECENT_TP_REENTRY_REQUIRES_LIMIT_ONLY_REASON_CODE = "recent_tp_reentry_requires_limit_only"
SAME_SYMBOL_SIDE_TP_COOLDOWN_MINUTES_DEFAULT = 30
RECENT_TP_REENTRY_EDGE_UPLIFT_BPS_DEFAULT = 10.0
TAKE_PROFIT_CLOSE_REASON_MARKERS = frozenset({"tp", "take_profit", "take-profit", "take profit"})
MIN_PLANNED_RISK_REWARD_RATIO = 1.25
SYMBOL_RECENT_PERFORMANCE_LOOKBACK_DAYS = 30
SYMBOL_RECENT_PERFORMANCE_SAMPLE_LIMIT = 80
SYMBOL_RECENT_PERFORMANCE_MIN_EXECUTIONS = 4
DEFAULT_MAX_SAME_DIRECTION_MAJOR_EXPOSURE_PCT = 2.0
CORRELATED_EXPOSURE_LIMIT_REASON_CODE = "CORRELATED_EXPOSURE_LIMIT_REACHED"
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
BREAKEVEN_DISABLED_REASON_CODE = "BREAKEVEN_DISABLED"
BREAKEVEN_TRIGGER_NOT_MET_REASON_CODE = "BREAKEVEN_TRIGGER_NOT_MET"
BREAKEVEN_MIN_HOLD_NOT_MET_REASON_CODE = "BREAKEVEN_MIN_HOLD_NOT_MET"
BREAKEVEN_CANDIDATE_INVALID_REASON_CODE = "BREAKEVEN_CANDIDATE_INVALID"
BREAKEVEN_NOT_MORE_PROTECTIVE_REASON_CODE = "BREAKEVEN_NOT_MORE_PROTECTIVE"
BREAKEVEN_STOP_NOT_INSIDE_MARKET_REASON_CODE = "BREAKEVEN_STOP_NOT_INSIDE_MARKET"
BREAKEVEN_PROTECTION_UNHEALTHY_REASON_CODE = "BREAKEVEN_PROTECTION_UNHEALTHY"
BREAKEVEN_LIVE_CONTROL_BLOCKED_REASON_CODE = "BREAKEVEN_LIVE_CONTROL_BLOCKED"
FINAL_ORDER_STATUSES = frozenset({"filled", "canceled", "cancelled", "rejected", "expired", "finished"})
FINAL_EXCHANGE_ORDER_STATUSES = frozenset(
    {"FILLED", "CANCELED", "CANCELLED", "REJECTED", "EXPIRED", "EXPIRED_IN_MATCH", "FINISHED"}
)
PROTECTIVE_ORDER_TYPE_PREFIXES = ("stop", "take_profit", "trailing_stop")
EXPOSURE_LIMIT_REASON_SPECS = (
    ("gross_exposure_pct_equity", "gross_exposure_pct", "GROSS_EXPOSURE_LIMIT_REACHED"),
    ("decision_symbol_concentration_pct", "largest_position_pct", "LARGEST_POSITION_LIMIT_REACHED"),
    ("same_tier_concentration_pct", "same_tier_concentration_pct", "SAME_TIER_CONCENTRATION_LIMIT_REACHED"),
)


def validate_decision_schema(payload: dict[str, Any]) -> TradeDecision:
    return TradeDecision.model_validate(payload)


def is_survival_path_decision(decision: TradeDecision | str) -> bool:
    return is_survival_path_intent(decision)


def _survival_path_label(decision: TradeDecision, *, is_protection_recovery: bool) -> str | None:
    if is_protection_recovery:
        return "protective_recovery"
    if not is_survival_path_intent(decision):
        return None
    management_action = str(decision.management_action or "").strip().lower()
    if decision.decision == "exit" or management_action == "exit_only":
        return "exit"
    if decision.decision == "reduce" or management_action == "reduce_only":
        return "reduce_only"
    return None


def _has_operator_event_override(payload: EventOperatorControlPayload | None) -> bool:
    if payload is None:
        return False
    view = payload.operator_event_view
    return (
        bool(payload.manual_no_trade_windows)
        or view.operator_bias != "unknown"
        or view.operator_risk_state != "unknown"
        or bool(view.applies_to_symbols)
        or view.valid_from is not None
        or view.valid_to is not None
        or view.enforcement_mode != "observe_only"
        or bool(view.note)
    )


def _event_policy_audit_payload(
    *,
    decision: TradeDecision,
    event_control_payload: EventOperatorControlPayload,
    blocked_reason: str | None,
    approval_required_reason: str | None,
    degraded_reason: str | None,
    policy_source: str,
    survival_path: str | None = None,
) -> dict[str, Any]:
    evaluated_operator_policy = event_control_payload.evaluated_operator_policy
    return {
        "symbol": decision.symbol,
        "decision": decision.decision,
        "timeframe": decision.timeframe,
        "blocked_reason": blocked_reason,
        "approval_required_reason": approval_required_reason,
        "degraded_reason": degraded_reason,
        "policy_source": policy_source,
        "survival_path": survival_path,
        "event_context": event_control_payload.event_context.model_dump(mode="json"),
        "operator_event_view": event_control_payload.operator_event_view.model_dump(mode="json"),
        "manual_no_trade_windows": [window.model_dump(mode="json") for window in event_control_payload.manual_no_trade_windows],
        "alignment_decision": event_control_payload.alignment_decision.model_dump(mode="json"),
        "evaluated_operator_policy": (
            evaluated_operator_policy.model_dump(mode="json") if evaluated_operator_policy is not None else None
        ),
    }


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
        if sync_scope_blocks_new_entry(sync_freshness_summary, scope):
            reason_codes.append(reason_code)
    return reason_codes


def _parse_ai_decision_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _positive_float(value: object) -> float | None:
    parsed = _optional_float(value)
    if parsed is None or parsed <= 0.0:
        return None
    return parsed


def _ai_decision_payload_from_run(agent_run: AgentRun) -> dict[str, Any]:
    metadata = _as_dict(agent_run.metadata_json)
    validity = _as_dict(metadata.get("ai_decision_validity"))
    output_payload = _as_dict(agent_run.output_payload)
    input_payload = _as_dict(agent_run.input_payload)
    market_payload = _as_dict(input_payload.get("market_snapshot"))
    feature_payload = _as_dict(input_payload.get("features"))
    feature_regime = _as_dict(feature_payload.get("regime"))
    feature_breakout = _as_dict(feature_payload.get("breakout"))
    trade_tags = _as_dict(metadata.get("trade_performance_tags"))
    reference_regime_id = (
        str(validity.get("regime_id") or trade_tags.get("regime_id") or "").strip()
        or None
    )
    reference_regime_label = (
        str(
            validity.get("regime_label")
            or trade_tags.get("regime_label")
            or feature_regime.get("primary_regime")
            or ""
        ).strip()
        or None
    )
    return {
        "metadata": metadata,
        "validity": validity,
        "output": output_payload,
        "input": input_payload,
        "market": market_payload,
        "features": feature_payload,
        "reference_symbol": validity.get("symbol") or output_payload.get("symbol") or market_payload.get("symbol"),
        "reference_timeframe": (
            validity.get("timeframe") or output_payload.get("timeframe") or market_payload.get("timeframe")
        ),
        "reference_price": _positive_float(validity.get("reference_price"))
        or _positive_float(market_payload.get("latest_price")),
        "reference_regime_id": reference_regime_id,
        "reference_regime_label": reference_regime_label,
        "reference_volatility_pct": _optional_float(validity.get("volatility_pct"))
        if validity.get("volatility_pct") not in {None, ""}
        else _optional_float(feature_payload.get("volatility_pct")),
        "reference_range_breakout_direction": str(
            validity.get("range_breakout_direction")
            or feature_breakout.get("range_breakout_direction")
            or "none"
        ).strip().lower(),
        "market_snapshot_id": validity.get("market_snapshot_id"),
        "snapshot_hash": validity.get("snapshot_hash"),
        "snapshot_time": validity.get("snapshot_time") or market_payload.get("snapshot_time"),
    }


def _current_market_state(
    decision_context: dict[str, Any] | None,
    market_snapshot: MarketSnapshotPayload,
) -> dict[str, Any]:
    context = _as_dict(decision_context)
    current = _as_dict(context.get("current_market_state"))
    return {
        "regime_id": str(current.get("regime_id") or "").strip() or None,
        "regime_label": str(current.get("regime_label") or "").strip() or None,
        "volatility_pct": _optional_float(current.get("volatility_pct")),
        "range_breakout_direction": str(current.get("range_breakout_direction") or "none").strip().lower(),
        "price": market_snapshot.latest_price,
    }


def _range_break_direction_is_material(direction: object) -> bool:
    normalized = str(direction or "").strip().lower()
    if normalized in {"", "none", "neutral", "inside", "no_break", "no_breakout"}:
        return False
    return "break" in normalized or normalized in {"up", "down", "long", "short", "bullish", "bearish"}


def _ai_decision_sync_untrusted_scopes(sync_freshness_summary: dict[str, Any]) -> list[str]:
    scopes: list[str] = []
    for scope in SYNC_BLOCKING_REASON_CODES:
        if sync_scope_blocks_new_entry(sync_freshness_summary, scope):
            scopes.append(scope)
    return scopes


def _evaluate_ai_decision_validity(
    *,
    session: Session,
    defaults: object,
    decision: TradeDecision,
    market_snapshot: MarketSnapshotPayload,
    decision_run_id: int | None,
    market_snapshot_id: int | None,
    is_entry_decision: bool,
    execution_mode: str,
    decision_context: dict[str, Any] | None,
    sync_freshness_summary: dict[str, Any],
) -> dict[str, Any]:
    if not is_entry_decision:
        return {"status": "not_applicable", "reason_codes": [], "applied": False}
    if execution_mode == "historical_replay" or decision_run_id is None:
        return {"status": "not_applicable", "reason_codes": [], "applied": False}
    agent_run = session.get(AgentRun, decision_run_id)
    if agent_run is None or agent_run.role != AgentRole.TRADING_DECISION.value:
        return {"status": "not_applicable", "reason_codes": [], "applied": False}

    reference = _ai_decision_payload_from_run(agent_run)
    validity = _as_dict(reference.get("validity"))
    generated_at = (
        _parse_ai_decision_datetime(validity.get("generated_at"))
        or _parse_ai_decision_datetime(reference["metadata"].get("generated_at"))
        or _parse_ai_decision_datetime(agent_run.completed_at)
    )
    try:
        ttl_seconds = max(int(getattr(defaults, "ai_decision_ttl_seconds", 900) or 900), 1)
    except (TypeError, ValueError):
        ttl_seconds = 900
    if validity.get("ttl_seconds") not in {None, ""}:
        with suppress(TypeError, ValueError):
            ttl_seconds = max(int(validity.get("ttl_seconds")), 1)
    valid_until = _parse_ai_decision_datetime(validity.get("valid_until"))
    if valid_until is None and generated_at is not None:
        valid_until = generated_at + timedelta(seconds=ttl_seconds)

    now = utcnow_naive()
    reason_codes: list[str] = []
    if valid_until is not None and now > valid_until:
        reason_codes.append(AI_DECISION_EXPIRED_REASON_CODE)
    if market_snapshot.is_stale:
        reason_codes.append(AI_DECISION_MARKET_STALE_REASON_CODE)
    if not market_snapshot.is_complete:
        reason_codes.append(AI_DECISION_MARKET_INCOMPLETE_REASON_CODE)

    price_threshold = _optional_float(validity.get("price_move_invalidation_pct"))
    if price_threshold is None:
        price_threshold = _settings_float(
            defaults,
            "ai_decision_price_move_invalidation_pct",
            0.004,
            minimum=0.0,
        )
    reference_price = _positive_float(reference.get("reference_price"))
    price_move_pct: float | None = None
    if reference_price is not None:
        price_move_pct = abs(float(market_snapshot.latest_price) - reference_price) / reference_price
        if price_threshold > 0.0 and price_move_pct >= price_threshold:
            reason_codes.append(AI_DECISION_PRICE_MOVE_INVALIDATED_REASON_CODE)

    current = _current_market_state(decision_context, market_snapshot)
    reference_regime_id = reference.get("reference_regime_id")
    current_regime_id = current.get("regime_id")
    reference_regime_label = reference.get("reference_regime_label")
    current_regime_label = current.get("regime_label")
    if (
        reference_regime_id
        and current_regime_id
        and reference_regime_id != current_regime_id
        or reference_regime_label
        and current_regime_label
        and reference_regime_label != current_regime_label
    ):
        reason_codes.append(AI_DECISION_REGIME_CHANGED_REASON_CODE)

    reference_range_direction = str(reference.get("reference_range_breakout_direction") or "none").strip().lower()
    current_range_direction = str(current.get("range_breakout_direction") or "none").strip().lower()
    if (
        current_range_direction != reference_range_direction
        and _range_break_direction_is_material(current_range_direction)
    ):
        reason_codes.append(AI_DECISION_RANGE_BREAK_INVALIDATED_REASON_CODE)

    reference_volatility = _optional_float(reference.get("reference_volatility_pct"))
    current_volatility = _optional_float(current.get("volatility_pct"))
    volatility_multiplier = _settings_float(
        defaults,
        "ai_decision_volatility_spike_multiplier",
        1.5,
        minimum=1.0,
    )
    volatility_floor = _settings_float(
        defaults,
        "ai_decision_volatility_spike_min_pct",
        0.02,
        minimum=0.0,
    )
    if reference_volatility is not None and current_volatility is not None:
        spike_threshold = max(reference_volatility * volatility_multiplier, volatility_floor)
        if current_volatility >= spike_threshold and current_volatility > reference_volatility:
            reason_codes.append(AI_DECISION_VOLATILITY_SPIKE_REASON_CODE)

    sync_untrusted_scopes = _ai_decision_sync_untrusted_scopes(sync_freshness_summary)
    if sync_untrusted_scopes:
        reason_codes.append(AI_DECISION_SYNC_STATE_UNTRUSTED_REASON_CODE)

    reason_codes = list(dict.fromkeys(reason_codes))
    status = "valid"
    event_type = None
    if AI_DECISION_EXPIRED_REASON_CODE in reason_codes:
        status = "expired"
        event_type = "ai_decision_expired"
    elif reason_codes:
        status = "invalidated"
        event_type = "ai_decision_invalidated"

    payload = {
        "status": status,
        "reason_codes": reason_codes,
        "reason_code": reason_codes[0] if reason_codes else None,
        "symbol": decision.symbol,
        "timeframe": decision.timeframe,
        "decision": decision.decision,
        "generated_at": generated_at.isoformat() if generated_at is not None else None,
        "valid_until": valid_until.isoformat() if valid_until is not None else None,
        "ttl_seconds": ttl_seconds,
        "market_snapshot_id": reference.get("market_snapshot_id"),
        "current_market_snapshot_id": market_snapshot_id,
        "snapshot_hash": reference.get("snapshot_hash"),
        "snapshot_time": reference.get("snapshot_time"),
        "reference_price": _round_float(reference_price),
        "current_price": _round_float(market_snapshot.latest_price),
        "price_move_pct": _round_float(price_move_pct),
        "price_move_threshold_pct": _round_float(price_threshold),
        "reference_regime_id": reference_regime_id,
        "current_regime_id": current_regime_id,
        "reference_regime_label": reference_regime_label,
        "current_regime_label": current_regime_label,
        "reference_volatility_pct": _round_float(reference_volatility),
        "current_volatility_pct": _round_float(current_volatility),
        "range_breakout_direction": current_range_direction,
        "reference_range_breakout_direction": reference_range_direction,
        "sync_untrusted_scopes": sync_untrusted_scopes,
        "applied": True,
    }
    if reason_codes:
        metadata = dict(agent_run.metadata_json or {})
        updated_validity = dict(_as_dict(metadata.get("ai_decision_validity")))
        updated_validity.update(
            {
                "status": status,
                "invalidated_at": now.isoformat(),
                "last_checked_at": now.isoformat(),
                "reason_codes": reason_codes,
                "reason_code": reason_codes[0],
                "event_type": event_type,
            }
        )
        metadata["ai_decision_validity"] = updated_validity
        agent_run.metadata_json = metadata
        session.add(agent_run)
    return {
        **payload,
        "event_type": event_type,
        "applied": True,
    }


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


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item not in {None, ""}]


def _normalize_execution_risk_profile(value: object) -> str | None:
    if value in {None, ""}:
        return None
    profile = str(value).strip().upper()
    return profile if profile in EXECUTION_RISK_PROFILE_SEVERITY else None


def _execution_risk_profile_severity(profile: object) -> int:
    normalized = _normalize_execution_risk_profile(profile)
    if normalized is None:
        return EXECUTION_RISK_PROFILE_SEVERITY["NORMAL"]
    return EXECUTION_RISK_PROFILE_SEVERITY[normalized]


def _more_conservative_profile(left: str, right: str) -> str:
    if _execution_risk_profile_severity(left) >= _execution_risk_profile_severity(right):
        return left
    return right


def _normalize_profile_auto_apply_mode(value: object) -> str:
    mode = str(value or "shadow").strip().lower()
    return mode if mode in SAFE_PROFILE_AUTO_APPLY_MODES else "shadow"


def _safe_profile_selector_state(settings_row: Setting) -> dict[str, Any]:
    detail = _as_dict(settings_row.pause_reason_detail)
    state = _as_dict(detail.get(SAFE_PROFILE_SELECTOR_DETAIL_KEY))
    return state


def _write_safe_profile_selector_state(settings_row: Setting, payload: dict[str, Any]) -> None:
    detail = _as_dict(settings_row.pause_reason_detail)
    current_state = _safe_profile_selector_state(settings_row)
    detail[SAFE_PROFILE_SELECTOR_DETAIL_KEY] = {
        **current_state,
        "active_profile": payload.get("final_active_profile"),
        "deterministic_profile": payload.get("deterministic_profile"),
        "ai_recommended_profile": payload.get("ai_recommended_profile"),
        "selection_mode": payload.get("selection_mode"),
        "selected_reason": payload.get("selected_reason"),
        "active_profile_selected_at": payload.get("active_profile_selected_at"),
        "updated_at": payload.get("selected_at"),
        "relaxation_candidate": payload.get("relaxation_candidate") or {},
        "last_selection": payload,
    }
    settings_row.pause_reason_detail = detail


def _profile_scope_matches(scope: object, symbol: str) -> bool:
    symbol_key = str(symbol or "").strip().upper()
    if not symbol_key:
        return True
    if scope is None or scope == "" or scope == []:
        return True
    if isinstance(scope, str):
        tokens = [item.strip().upper() for item in scope.replace(";", ",").split(",")]
    elif isinstance(scope, list):
        tokens = [str(item or "").strip().upper() for item in scope]
    else:
        return True
    filtered = [item for item in tokens if item]
    return not filtered or "ALL" in filtered or "*" in filtered or symbol_key in filtered


def _latest_ai_market_settings_recommendation(
    settings_row: Setting,
    *,
    symbol: str,
    policy: dict[str, object],
    now: datetime,
) -> dict[str, Any]:
    detail = _as_dict(settings_row.pause_reason_detail)
    advisor_state = _as_dict(detail.get("ai_market_settings_advisor"))
    payload = _as_dict(advisor_state.get("latest")) or advisor_state
    if not payload:
        return {
            "valid": False,
            "profile": None,
            "ignored_reason_codes": ["AI_MARKET_SETTINGS_RECOMMENDATION_MISSING"],
        }

    recommendation_id = str(payload.get("recommendation_id") or "").strip() or None
    profile = _normalize_execution_risk_profile(payload.get("recommended_profile_id"))
    confidence = _optional_float(payload.get("confidence"))
    valid_until = _parse_ai_decision_datetime(payload.get("valid_until"))
    generated_at = _parse_ai_decision_datetime(payload.get("generated_at"))
    ttl_seconds = int(policy.get("recommendation_ttl_seconds") or 900)
    if valid_until is None and generated_at is not None:
        valid_until = generated_at + timedelta(seconds=max(ttl_seconds, 1))
    min_confidence = float(policy.get("min_confidence_to_apply") or 0.70)

    ignored_reason_codes: list[str] = []
    if profile is None:
        ignored_reason_codes.append(AI_MARKET_SETTINGS_RECOMMENDATION_UNKNOWN_PROFILE_REASON_CODE)
    if valid_until is None or now > valid_until:
        ignored_reason_codes.append(AI_MARKET_SETTINGS_RECOMMENDATION_EXPIRED_REASON_CODE)
    if confidence is None or confidence < min_confidence:
        ignored_reason_codes.append(AI_MARKET_SETTINGS_RECOMMENDATION_LOW_CONFIDENCE_REASON_CODE)
    if not _profile_scope_matches(payload.get("symbol_scope"), symbol):
        ignored_reason_codes.append(AI_MARKET_SETTINGS_RECOMMENDATION_SCOPE_MISMATCH_REASON_CODE)
    if str(advisor_state.get("status") or payload.get("status") or "").strip().lower() == "ignored":
        ignored_reason_codes.extend(_as_string_list(payload.get("ignored_reason_codes")))

    return {
        "valid": not ignored_reason_codes,
        "profile": profile,
        "recommendation_id": recommendation_id,
        "confidence": confidence,
        "valid_until": valid_until.isoformat() if valid_until is not None else None,
        "generated_at": generated_at.isoformat() if generated_at is not None else None,
        "do_not_relax": bool(payload.get("do_not_relax", False)),
        "suggested_new_entry_policy": payload.get("suggested_new_entry_policy"),
        "ignored_reason_codes": list(dict.fromkeys(ignored_reason_codes)),
        "raw": payload,
    }


def _safe_profile_hard_condition_codes(
    settings_row: Setting,
    market_snapshot: MarketSnapshotPayload,
    *,
    sync_freshness_summary: dict[str, Any],
    operating_state: str,
    live_requested: bool,
) -> list[str]:
    reason_codes: list[str] = []
    if market_snapshot.is_stale:
        reason_codes.append(MARKET_BLOCKING_REASON_CODES["stale"])
    if not market_snapshot.is_complete:
        reason_codes.append(MARKET_BLOCKING_REASON_CODES["incomplete"])
    if settings_row.trading_paused:
        reason_codes.append("TRADING_PAUSED")
    if operating_state in {PROTECTION_REQUIRED_STATE, DEGRADED_MANAGE_ONLY_STATE, EMERGENCY_EXIT_STATE, "PAUSED"}:
        reason_codes.append(operating_state)
    for scope, code in SYNC_BLOCKING_REASON_CODES.items():
        if sync_scope_blocks_new_entry(sync_freshness_summary, scope):
            reason_codes.append(code)
    reconciliation_codes = get_reconciliation_blocking_reason_codes(settings_row)
    reason_codes.extend(reconciliation_codes)
    if live_requested:
        binance_rest_block_reason = get_binance_rest_entry_block_reason_code(
            settings_row,
            sync_freshness_summary=sync_freshness_summary,
        )
        if binance_rest_block_reason is not None:
            reason_codes.append(binance_rest_block_reason)
        if not is_live_execution_armed(settings_row):
            reason_codes.append("LIVE_APPROVAL_REQUIRED")
    return list(dict.fromkeys(reason_codes))


def _context_requested_market_condition_profile(decision_context: dict[str, Any] | None) -> str | None:
    context = _as_dict(decision_context)
    for key in (
        "deterministic_market_condition_profile",
        "market_condition_profile",
        "execution_risk_profile",
    ):
        profile = _normalize_execution_risk_profile(context.get(key))
        if profile is not None:
            return profile
    current_market_state = _as_dict(context.get("current_market_state"))
    for key in ("deterministic_market_condition_profile", "market_condition_profile", "execution_risk_profile"):
        profile = _normalize_execution_risk_profile(current_market_state.get(key))
        if profile is not None:
            return profile
    return None


def _deterministic_market_condition_profile(
    settings_row: Setting,
    market_snapshot: MarketSnapshotPayload,
    *,
    defaults: Any,
    sync_freshness_summary: dict[str, Any],
    operating_state: str,
    live_requested: bool,
    decision_context: dict[str, Any] | None,
) -> dict[str, Any]:
    hard_condition_reason_codes = _safe_profile_hard_condition_codes(
        settings_row,
        market_snapshot,
        sync_freshness_summary=sync_freshness_summary,
        operating_state=operating_state,
        live_requested=live_requested,
    )
    if hard_condition_reason_codes:
        return {
            "profile": "DEGRADED",
            "reason_codes": hard_condition_reason_codes,
            "hard_condition": True,
        }

    context_profile = _context_requested_market_condition_profile(decision_context)
    if context_profile is not None:
        return {"profile": context_profile, "reason_codes": ["DETERMINISTIC_CONTEXT_PROFILE"], "hard_condition": False}

    context = _as_dict(decision_context)
    current_market_state = _as_dict(context.get("current_market_state"))
    volatility_pct = _optional_float(current_market_state.get("volatility_pct"))
    volatility_min_pct = float(getattr(defaults, "ai_decision_volatility_spike_min_pct", 0.02) or 0.02)
    range_breakout_direction = str(current_market_state.get("range_breakout_direction") or "none").strip().lower()
    spread_bps = _optional_float(getattr(market_snapshot.derivatives_context, "spread_bps", None))
    spread_stress_score = _optional_float(getattr(market_snapshot.derivatives_context, "spread_stress_score", None))
    if volatility_pct is not None and volatility_pct >= volatility_min_pct:
        return {"profile": "HIGH_VOLATILITY", "reason_codes": ["VOLATILITY_ELEVATED"], "hard_condition": False}
    if range_breakout_direction not in {"", "none", "neutral"}:
        return {"profile": "HIGH_VOLATILITY", "reason_codes": ["RANGE_BREAK"], "hard_condition": False}
    if (spread_bps is not None and spread_bps >= 10.0) or (
        spread_stress_score is not None and spread_stress_score >= 0.75
    ):
        return {"profile": "THIN_LIQUIDITY", "reason_codes": ["SPREAD_STRESS"], "hard_condition": False}
    return {"profile": "NORMAL", "reason_codes": [], "hard_condition": False}


def select_safe_execution_risk_profile(
    settings_row: Setting,
    market_snapshot: MarketSnapshotPayload,
    *,
    defaults: Any | None = None,
    symbol: str | None = None,
    decision_context: dict[str, Any] | None = None,
    sync_freshness_summary: dict[str, Any] | None = None,
    operating_state: str | None = None,
    live_requested: bool = False,
    now: datetime | None = None,
    persist_state: bool = True,
) -> dict[str, Any]:
    defaults = defaults or get_settings()
    profile_policy = get_execution_risk_profile_policy(settings_row, defaults=defaults)
    current_time = now or utcnow_naive()
    current_symbol = symbol or market_snapshot.symbol
    sync_summary = sync_freshness_summary or build_sync_freshness_summary(settings_row, now=current_time)
    operating_state_value = operating_state or get_operating_state(settings_row)
    selection_mode = _normalize_profile_auto_apply_mode(profile_policy.get("auto_apply_mode"))
    deterministic = _deterministic_market_condition_profile(
        settings_row,
        market_snapshot,
        defaults=defaults,
        sync_freshness_summary=sync_summary,
        operating_state=operating_state_value,
        live_requested=live_requested,
        decision_context=decision_context,
    )
    deterministic_profile = str(deterministic["profile"])
    hard_condition_reason_codes = _as_string_list(deterministic.get("reason_codes")) if deterministic.get("hard_condition") else []
    advisor = _latest_ai_market_settings_recommendation(
        settings_row,
        symbol=current_symbol,
        policy=profile_policy,
        now=current_time,
    )
    ai_profile = advisor.get("profile") if advisor.get("valid") else None
    raw_ai_profile = advisor.get("profile")
    previous_state = _safe_profile_selector_state(settings_row)
    previous_profile = _normalize_execution_risk_profile(previous_state.get("active_profile"))
    previous_selected_at = _parse_ai_decision_datetime(previous_state.get("active_profile_selected_at"))

    candidate_profile = deterministic_profile
    selected_reason = "deterministic_profile_selected"
    was_tightened_by_ai = False
    would_tighten_by_ai = False
    was_relaxation_blocked = False
    relaxation_block_reason_codes: list[str] = []
    shadow_final_profile = deterministic_profile

    deterministic_severity = _execution_risk_profile_severity(deterministic_profile)
    if advisor.get("valid") and ai_profile is not None:
        ai_severity = _execution_risk_profile_severity(ai_profile)
        if bool(deterministic.get("hard_condition", False)):
            selected_reason = "hard_condition"
        elif selection_mode == "conservative_only":
            if ai_severity > deterministic_severity:
                candidate_profile = str(ai_profile)
                was_tightened_by_ai = True
                selected_reason = "ai_tightened_conservative_only"
            elif ai_severity < deterministic_severity:
                was_relaxation_blocked = True
                selected_reason = "ai_relaxation_blocked"
        elif selection_mode == "manual_approval":
            if ai_severity > deterministic_severity:
                would_tighten_by_ai = True
                selected_reason = "manual_approval_required"
            elif ai_severity < deterministic_severity:
                was_relaxation_blocked = True
                selected_reason = "ai_relaxation_blocked"
        elif selection_mode == "shadow":
            shadow_final_profile = _more_conservative_profile(deterministic_profile, str(ai_profile))
            selected_reason = "shadow_no_auto_apply"
        else:
            selected_reason = "off"
    elif bool(deterministic.get("hard_condition", False)):
        selected_reason = "hard_condition"
    elif advisor.get("ignored_reason_codes"):
        selected_reason = "ai_recommendation_ignored"

    relaxation_candidate: dict[str, Any] = {}
    final_profile = candidate_profile
    target_is_relaxation = (
        previous_profile is not None
        and _execution_risk_profile_severity(candidate_profile) < _execution_risk_profile_severity(previous_profile)
    )
    if target_is_relaxation:
        previous_candidate = _as_dict(previous_state.get("relaxation_candidate"))
        if _normalize_execution_risk_profile(previous_candidate.get("profile")) == candidate_profile:
            confirmation_count = int(previous_candidate.get("confirmation_count") or 0) + 1
            first_seen_at = previous_candidate.get("first_seen_at") or current_time.isoformat()
        else:
            confirmation_count = 1
            first_seen_at = current_time.isoformat()
        required_confirmations = max(
            int(profile_policy.get("relax_requires_consecutive_confirmations") or 2),
            1,
        )
        min_dwell_seconds = max(
            int(profile_policy.get("min_profile_dwell_seconds", 900)),
            0,
        )
        dwell_elapsed_seconds = (
            (current_time - previous_selected_at).total_seconds()
            if previous_selected_at is not None
            else min_dwell_seconds
        )
        ai_confidence_ok = bool(advisor.get("valid")) and advisor.get("confidence") is not None
        if bool(deterministic.get("hard_condition", False)):
            relaxation_block_reason_codes.append(PROFILE_RELAXATION_HEALTH_REASON_CODE)
        if not bool(advisor.get("valid")):
            relaxation_block_reason_codes.append(PROFILE_RELAXATION_AI_VALIDITY_REASON_CODE)
        if not ai_confidence_ok:
            relaxation_block_reason_codes.append(PROFILE_RELAXATION_AI_CONFIDENCE_REASON_CODE)
        if bool(advisor.get("do_not_relax", False)):
            relaxation_block_reason_codes.append(PROFILE_RELAXATION_DO_NOT_RELAX_REASON_CODE)
        if confirmation_count < required_confirmations:
            relaxation_block_reason_codes.append(PROFILE_RELAXATION_CONSECUTIVE_REASON_CODE)
        if dwell_elapsed_seconds < min_dwell_seconds:
            relaxation_block_reason_codes.append(PROFILE_RELAXATION_DWELL_REASON_CODE)
        if relaxation_block_reason_codes:
            final_profile = previous_profile
            was_relaxation_blocked = True
            selected_reason = "profile_relaxation_blocked"
            relaxation_candidate = {
                "profile": candidate_profile,
                "confirmation_count": confirmation_count,
                "required_confirmations": required_confirmations,
                "first_seen_at": first_seen_at,
                "dwell_elapsed_seconds": round(max(dwell_elapsed_seconds, 0.0), 3),
                "min_dwell_seconds": min_dwell_seconds,
                "reason_codes": list(dict.fromkeys(relaxation_block_reason_codes)),
            }
        else:
            selected_reason = "profile_relaxed_after_confirmations"

    active_profile_selected_at = (
        previous_state.get("active_profile_selected_at")
        if previous_profile == final_profile and previous_state.get("active_profile_selected_at")
        else current_time.isoformat()
    )
    blocking_active = selection_mode in {"conservative_only", "manual_approval"}
    active_profile_blocks_new_entry = bool(
        blocking_active
        and _execution_risk_profile_severity(final_profile) >= EXECUTION_RISK_PROFILE_BLOCKING_SEVERITY
    )
    result = {
        "previous_profile": previous_profile,
        "deterministic_profile": deterministic_profile,
        "deterministic_reason_codes": _as_string_list(deterministic.get("reason_codes")),
        "ai_recommended_profile": raw_ai_profile,
        "final_active_profile": final_profile,
        "shadow_final_profile": shadow_final_profile if selection_mode == "shadow" else None,
        "selection_mode": selection_mode,
        "selected_reason": selected_reason,
        "ai_recommendation_id": advisor.get("recommendation_id"),
        "confidence": advisor.get("confidence"),
        "valid_until": advisor.get("valid_until"),
        "was_tightened_by_ai": was_tightened_by_ai,
        "would_tighten_by_ai": would_tighten_by_ai,
        "was_relaxation_blocked": was_relaxation_blocked,
        "relaxation_block_reason_codes": list(dict.fromkeys(relaxation_block_reason_codes)),
        "ignored_reason_codes": _as_string_list(advisor.get("ignored_reason_codes")),
        "hard_condition_reason_codes": hard_condition_reason_codes,
        "active_profile_blocks_new_entry": active_profile_blocks_new_entry,
        "blocking_active": blocking_active,
        "active_profile_selected_at": active_profile_selected_at,
        "selected_at": current_time.isoformat(),
        "relaxation_candidate": relaxation_candidate,
    }
    if persist_state:
        _write_safe_profile_selector_state(settings_row, result)
    return result


def evaluate_breakeven_stop_move_risk_guard(
    session: Session,
    settings_row: Setting,
    position: Position,
    *,
    candidate_stop_loss: float | None,
    context: dict[str, Any],
    protection_state: dict[str, object],
    decision_run_id: int | None = None,
) -> tuple[RiskCheckResult, RiskCheck]:
    symbol = position.symbol
    side = "long" if position.side == "long" else "short" if position.side == "short" else "hold"
    operating_state = get_operating_state(settings_row)
    latest_pnl = get_latest_pnl_snapshot(session, settings_row)
    effective_leverage_cap = _effective_leverage_cap(settings_row, symbol)
    exposure_metrics = _build_exposure_metrics(session, symbol, latest_pnl.equity)
    sync_freshness_summary = build_sync_freshness_summary(settings_row)
    defaults = get_settings()
    credentials = get_runtime_credentials(settings_row)
    reason_codes: list[str] = []

    current_r_multiple = _optional_float(context.get("current_r_multiple"))
    trigger_r = _coerce_float(context.get("break_even_trigger_r"), 1.0)
    min_hold_seconds = max(int(_coerce_float(context.get("breakeven_min_hold_seconds"), 0.0)), 0)
    time_in_trade_seconds = max(_coerce_float(context.get("time_in_trade_minutes"), 0.0) * 60.0, 0.0)
    stop_loss = _optional_float(candidate_stop_loss)
    current_stop = _optional_float(position.stop_loss)
    mark_price = position.mark_price if position.mark_price > 0 else _coerce_float(context.get("mark_price"), 0.0)

    if not settings_row.position_management_enabled or not settings_row.break_even_enabled:
        reason_codes.append(BREAKEVEN_DISABLED_REASON_CODE)
    if side not in {"long", "short"} or position.status != "open" or position.quantity <= 0:
        reason_codes.append(BREAKEVEN_CANDIDATE_INVALID_REASON_CODE)
    if current_r_multiple is None or current_r_multiple < trigger_r:
        reason_codes.append(BREAKEVEN_TRIGGER_NOT_MET_REASON_CODE)
    if time_in_trade_seconds < min_hold_seconds:
        reason_codes.append(BREAKEVEN_MIN_HOLD_NOT_MET_REASON_CODE)
    if stop_loss is None or stop_loss <= 0:
        reason_codes.append(BREAKEVEN_CANDIDATE_INVALID_REASON_CODE)
    elif side == "long":
        if current_stop is not None and stop_loss <= current_stop + 1e-9:
            reason_codes.append(BREAKEVEN_NOT_MORE_PROTECTIVE_REASON_CODE)
        if mark_price > 0 and stop_loss >= mark_price:
            reason_codes.append(BREAKEVEN_STOP_NOT_INSIDE_MARKET_REASON_CODE)
    elif side == "short":
        if current_stop is not None and stop_loss >= current_stop - 1e-9:
            reason_codes.append(BREAKEVEN_NOT_MORE_PROTECTIVE_REASON_CODE)
        if mark_price > 0 and stop_loss <= mark_price:
            reason_codes.append(BREAKEVEN_STOP_NOT_INSIDE_MARKET_REASON_CODE)

    if str(protection_state.get("status") or "").lower() != "protected" or not bool(
        protection_state.get("has_stop_loss")
    ):
        reason_codes.append(BREAKEVEN_PROTECTION_UNHEALTHY_REASON_CODE)
        reason_codes.extend(_as_string_list(protection_state.get("reason_codes")))
    if settings_row.trading_paused:
        reason_codes.append("TRADING_PAUSED")
    if operating_state in {PROTECTION_REQUIRED_STATE, DEGRADED_MANAGE_ONLY_STATE, EMERGENCY_EXIT_STATE}:
        reason_codes.append(operating_state)
    if not rollout_mode_allows_exchange_submit(settings_row):
        reason_codes.append("LIVE_TRADING_DISABLED")
    if not bool(getattr(defaults, "live_trading_env_enabled", False)):
        reason_codes.append("LIVE_ENV_DISABLED")
    if not credentials.binance_api_key or not credentials.binance_api_secret:
        reason_codes.append("LIVE_CREDENTIALS_MISSING")
    if not settings_row.manual_live_approval:
        reason_codes.append("LIVE_APPROVAL_POLICY_DISABLED")
    if not is_live_execution_armed(settings_row):
        reason_codes.append("LIVE_APPROVAL_REQUIRED")

    blocked_reason_codes = list(dict.fromkeys(reason_codes))
    allowed = not blocked_reason_codes
    debug_payload = {
        "risk_guard": "breakeven_stop_move",
        "candidate": {
            "symbol": symbol,
            "side": side,
            "entry_price": _round_float(position.entry_price),
            "mark_price": _round_float(mark_price),
            "current_stop_loss": _round_float(position.stop_loss),
            "candidate_stop_loss": _round_float(stop_loss),
            "current_r_multiple": _round_float(current_r_multiple),
            "trigger_r": _round_float(trigger_r),
            "breakeven_lock_bps": _round_float(_optional_float(context.get("breakeven_lock_bps"))),
            "breakeven_min_hold_seconds": min_hold_seconds,
            "time_in_trade_seconds": _round_float(time_in_trade_seconds),
        },
        "protection_state": protection_state,
        "live_control": {
            "rollout_mode": get_rollout_mode(settings_row),
            "exchange_submit_allowed": rollout_mode_allows_exchange_submit(settings_row),
            "manual_live_approval": settings_row.manual_live_approval,
            "live_execution_armed": is_live_execution_armed(settings_row),
            "operating_state": operating_state,
        },
    }
    result = RiskCheckResult(
        allowed=allowed,
        decision=side,  # type: ignore[arg-type]
        reason_codes=blocked_reason_codes,
        blocked_reason_codes=blocked_reason_codes,
        adjustment_reason_codes=["BREAKEVEN_RISK_GUARD_APPROVED"] if allowed else [],
        degraded_reason_codes=derive_degraded_reason_codes(
            blocked_reason_codes,
            operating_state=operating_state,
            degraded_reason=None,
        ),
        protection_reason_codes=derive_protection_reason_codes(
            blocked_reason_codes,
            operating_state=operating_state,
        ),
        survival_path="protective_recovery",
        approved_risk_pct=0.0,
        approved_leverage=position.leverage if allowed and position.leverage > 0 else 0.0,
        operating_mode="paused" if settings_row.trading_paused else "live",
        operating_state=operating_state,
        effective_leverage_cap=effective_leverage_cap,
        symbol_risk_tier=get_symbol_risk_tier(symbol),
        exposure_metrics=exposure_metrics,
        sync_freshness_summary=sync_freshness_summary,
        debug_payload=debug_payload,
    )
    row = RiskCheck(
        symbol=symbol,
        decision_run_id=decision_run_id,
        market_snapshot_id=None,
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


def _symbol_side_key(symbol: str, side: str) -> str:
    return f"{str(symbol or '').strip().upper()}:{str(side or '').strip().lower()}"


def _normalize_symbol_side_key(value: object) -> str | None:
    text = str(value or "").strip()
    if ":" not in text:
        return None
    symbol, side = text.split(":", 1)
    symbol = symbol.strip().upper()
    side = side.strip().lower()
    if not symbol or side not in {"long", "short"}:
        return None
    return f"{symbol}:{side}"


def _parse_symbol_side_float_map(value: object) -> dict[str, float]:
    raw = value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            raw = {}
            for item in text.split(","):
                if "=" not in item:
                    continue
                key, raw_value = item.split("=", 1)
                normalized_key = _normalize_symbol_side_key(key)
                parsed = _optional_float(raw_value)
                if normalized_key is not None and parsed is not None and parsed >= 0.0:
                    raw[normalized_key] = parsed
    if not isinstance(raw, dict):
        return {}
    parsed_map: dict[str, float] = {}
    for key, raw_value in raw.items():
        normalized_key = _normalize_symbol_side_key(key)
        parsed = _optional_float(raw_value)
        if normalized_key is not None and parsed is not None and parsed >= 0.0:
            parsed_map[normalized_key] = parsed
    return parsed_map


def _settings_float(defaults: object, name: str, fallback: float, *, minimum: float | None = None) -> float:
    parsed = _optional_float(getattr(defaults, name, None))
    if parsed is None:
        parsed = fallback
    if minimum is not None:
        parsed = max(parsed, minimum)
    return parsed


def _settings_bool(defaults: object, name: str, fallback: bool) -> bool:
    value = getattr(defaults, name, None)
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return fallback


def _risk_setting_float(
    settings_row: Setting,
    name: str,
    fallback: float,
    *,
    minimum: float | None = None,
) -> float:
    runtime_defaults = get_settings()
    parsed = _optional_float(getattr(settings_row, name, None))
    if parsed is None:
        parsed = _optional_float(getattr(runtime_defaults, name, None))
    if parsed is None:
        parsed = fallback
    if minimum is not None:
        parsed = max(parsed, minimum)
    return parsed


def _risk_setting_int(settings_row: Setting, name: str, fallback: int, *, minimum: int | None = None) -> int:
    parsed = _risk_setting_float(settings_row, name, float(fallback), minimum=float(minimum or 0))
    return int(max(round(parsed), minimum if minimum is not None else parsed))


def _expected_profitability_thresholds(decision: TradeDecision) -> dict[str, Any]:
    defaults = get_settings()
    min_expected_gross_default = _settings_float(
        defaults,
        "min_expected_gross_bps_default",
        EXPECTED_PROFITABILITY_MIN_GROSS_BPS_DEFAULT,
        minimum=0.0,
    )
    symbol_side_thresholds = dict(EXPECTED_PROFITABILITY_SYMBOL_SIDE_MIN_GROSS_BPS)
    symbol_side_thresholds.update(
        _parse_symbol_side_float_map(getattr(defaults, "symbol_side_min_expected_gross_bps", None))
    )
    key = _symbol_side_key(decision.symbol, decision.decision)
    return {
        "min_expected_gross_bps_default": min_expected_gross_default,
        "symbol_side_key": key,
        "symbol_side_min_expected_gross_bps": symbol_side_thresholds,
        "effective_min_expected_gross_bps": symbol_side_thresholds.get(key, min_expected_gross_default),
        "max_fee_to_gross_ratio": _settings_float(
            defaults,
            "max_fee_to_gross_ratio",
            EXPECTED_PROFITABILITY_MAX_FEE_TO_GROSS_RATIO,
            minimum=0.0,
        ),
        "min_confidence_for_entry": _settings_float(
            defaults,
            "min_confidence_for_entry",
            EXPECTED_PROFITABILITY_MIN_CONFIDENCE_FOR_ENTRY,
            minimum=0.0,
        ),
        "tight_tp_bps": EXPECTED_PROFITABILITY_TIGHT_TP_BPS,
    }


def _macro_event_result_context(decision_context: dict[str, Any] | None) -> dict[str, Any]:
    context = _as_dict(decision_context)
    event_risk_context = _as_dict(context.get("event_risk_context"))
    event_result_context = _as_dict(event_risk_context.get("event_result_context"))
    if event_result_context:
        return event_result_context
    ai_context = _as_dict(context.get("ai_context"))
    event_risk_context = _as_dict(ai_context.get("event_risk_context"))
    return _as_dict(event_risk_context.get("event_result_context"))


def _macro_event_result_conflict_reason(
    *,
    decision: TradeDecision,
    event_result_context: dict[str, Any],
) -> str | None:
    if not bool(event_result_context.get("available", False)):
        return None
    if not bool(event_result_context.get("reaction_window_active", False)):
        return None
    bias = str(event_result_context.get("event_result_bias") or "").strip().lower()
    confidence = _optional_float(event_result_context.get("event_result_confidence")) or 0.0
    if confidence < MACRO_EVENT_RESULT_MIN_CONFLICT_CONFIDENCE:
        return None
    if decision.decision == "long" and bias == "bearish":
        return MACRO_EVENT_RESULT_CONFLICT_REASON_CODE
    if decision.decision == "short" and bias == "bullish":
        return MACRO_EVENT_RESULT_CONFLICT_REASON_CODE
    return None


def _lead_context_payload(decision_context: dict[str, Any] | None) -> dict[str, Any]:
    context = _as_dict(decision_context)
    for key in ("lead_market_context", "lead_context", "lead_lag_summary"):
        payload = _as_dict(context.get(key))
        if payload:
            return payload
    ai_context = _as_dict(context.get("ai_context"))
    payload = _as_dict(ai_context.get("lead_lag_summary"))
    if payload:
        return payload
    selection_context = _as_dict(context.get("selection_context"))
    candidate_payload = _as_dict(selection_context.get("candidate"))
    return _as_dict(candidate_payload.get("lead_lag_summary"))


def _lead_market_context_state(decision_context: dict[str, Any] | None) -> dict[str, Any]:
    payload = _lead_context_payload(decision_context)
    missing_symbols = _as_string_list(
        payload.get("missing_lead_symbols")
        if "missing_lead_symbols" in payload
        else payload.get("missing_reference_symbols")
    )
    status = str(payload.get("lead_context_status") or "").strip().lower()
    if status not in {"ok", "partial", "unavailable"}:
        if payload.get("available") is False:
            status = "unavailable"
        elif missing_symbols:
            status = "partial"
        else:
            status = "ok"
    reason_codes = _as_string_list(payload.get("reason_codes"))
    if status == "unavailable":
        reason_codes.append("LEAD_CONTEXT_UNAVAILABLE")
    elif status == "partial":
        reason_codes.append("LEAD_CONTEXT_PARTIAL")
    reason_codes.extend(f"LEAD_CONTEXT_MISSING_{symbol}" for symbol in missing_symbols)
    return {
        "lead_context_status": status,
        "missing_lead_symbols": list(dict.fromkeys(missing_symbols)),
        "reason_codes": list(dict.fromkeys(reason_codes)),
    }


def _is_non_lead_alt_symbol(symbol: str) -> bool:
    return str(symbol or "").upper() not in LEAD_MARKET_SYMBOLS


def _decision_agreement_context(decision_context: dict[str, Any] | None) -> dict[str, Any]:
    payload = (
        decision_context.get("decision_agreement")
        if isinstance(decision_context, dict) and isinstance(decision_context.get("decision_agreement"), dict)
        else {}
    )
    level = str(payload.get("level") or "full_agreement")
    baseline_decision = payload.get("baseline_decision")
    final_decision = payload.get("final_decision")
    same_non_entry_decision = (
        str(baseline_decision or "").strip().lower()
        == str(final_decision or "").strip().lower()
        and str(baseline_decision or "").strip().lower() in {"hold", "reduce", "exit"}
    )
    if level == "disagreement" and same_non_entry_decision:
        level = "full_agreement"
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
        "direction_match": bool(payload.get("direction_match", False)) or same_non_entry_decision,
        "entry_mode_match": bool(payload.get("entry_mode_match", False))
        or (
            same_non_entry_decision
            and str(payload.get("baseline_entry_mode") or "none").strip().lower()
            == str(payload.get("final_entry_mode") or "none").strip().lower()
        ),
        "baseline_decision": baseline_decision,
        "baseline_entry_mode": payload.get("baseline_entry_mode"),
        "final_decision": final_decision,
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


def _normalize_expected_entry_type(value: object) -> str | None:
    return normalize_entry_execution_type(value)


def _expected_entry_execution_type(decision: TradeDecision, decision_context: dict[str, Any] | None) -> str:
    context = _as_dict(decision_context)
    expected_cost_gate = _as_dict(context.get("expected_cost_gate"))
    execution_policy = _as_dict(context.get("execution_policy"))
    selection_context = _as_dict(context.get("selection_context"))
    selection_execution_policy = _as_dict(selection_context.get("execution_policy"))
    for value in (
        expected_cost_gate.get("entry_execution_type"),
        context.get("entry_execution_type"),
        execution_policy.get("entry_execution_type"),
        execution_policy.get("execution_style"),
        execution_policy.get("entry_style"),
        execution_policy.get("order_style"),
        selection_context.get("entry_execution_type"),
        selection_execution_policy.get("entry_execution_type"),
        selection_execution_policy.get("execution_style"),
        selection_execution_policy.get("entry_style"),
        selection_execution_policy.get("order_style"),
        selection_context.get("entry_mode"),
        decision.entry_mode,
    ):
        normalized = _normalize_expected_entry_type(value)
        if normalized is not None:
            return normalized
    if decision.entry_mode in {"immediate", "breakout_confirm"}:
        return EXPECTED_COST_ENTRY_MARKETABLE
    if decision.entry_mode == "pullback_confirm":
        return EXPECTED_COST_ENTRY_PASSIVE_LIMIT
    return EXPECTED_COST_ENTRY_UNKNOWN


def _first_positive_float(*values: object) -> float | None:
    for value in values:
        parsed = _optional_float(value)
        if parsed is not None and parsed > 0.0:
            return parsed
    return None


def _expected_edge_bps_from_context(decision_context: dict[str, Any] | None) -> tuple[float | None, str | None]:
    context = _as_dict(decision_context)
    expected_cost_gate = _as_dict(context.get("expected_cost_gate"))
    selection_context = _as_dict(context.get("selection_context"))
    candidate = _as_dict(selection_context.get("candidate"))
    strategy_engine_context = _as_dict(selection_context.get("strategy_engine_context"))
    meta_gate = _as_dict(context.get("meta_gate"))
    value = _first_positive_float(
        expected_cost_gate.get("expected_gross_bps"),
        expected_cost_gate.get("expected_edge_bps"),
        expected_cost_gate.get("take_profit_bps"),
        expected_cost_gate.get("target_profit_bps"),
        expected_cost_gate.get("tp_bps"),
        context.get("expected_gross_bps"),
        context.get("expected_edge_bps"),
        context.get("take_profit_bps"),
        context.get("target_profit_bps"),
        context.get("tp_bps"),
        selection_context.get("expected_gross_bps"),
        selection_context.get("expected_edge_bps"),
        selection_context.get("take_profit_bps"),
        selection_context.get("target_profit_bps"),
        selection_context.get("tp_bps"),
        candidate.get("expected_gross_bps"),
        candidate.get("expected_edge_bps"),
        candidate.get("take_profit_bps"),
        candidate.get("target_profit_bps"),
        candidate.get("tp_bps"),
        candidate.get("target_move_bps"),
        candidate.get("expected_move_bps"),
        strategy_engine_context.get("expected_gross_bps"),
        strategy_engine_context.get("expected_edge_bps"),
        strategy_engine_context.get("take_profit_bps"),
        strategy_engine_context.get("target_profit_bps"),
        strategy_engine_context.get("tp_bps"),
        strategy_engine_context.get("target_move_bps"),
        meta_gate.get("expected_gross_bps"),
        meta_gate.get("expected_edge_bps"),
    )
    if value is None:
        return None, None
    return value, "decision_context"


def _expected_edge_bps(
    decision: TradeDecision,
    market_snapshot: MarketSnapshotPayload,
    decision_context: dict[str, Any] | None,
) -> tuple[float | None, str]:
    context_edge, context_source = _expected_edge_bps_from_context(decision_context)
    if context_edge is not None:
        return context_edge, context_source or "decision_context"
    context = _as_dict(decision_context)
    expected_cost_gate = _as_dict(context.get("expected_cost_gate"))
    selection_context = _as_dict(context.get("selection_context"))
    candidate = _as_dict(selection_context.get("candidate"))
    entry_price = _first_positive_float(
        expected_cost_gate.get("entry_price"),
        context.get("entry_price"),
        selection_context.get("entry_price"),
        candidate.get("entry_price"),
    )
    if entry_price is None:
        entry_price = _entry_price(decision, market_snapshot)
    target_price = _first_positive_float(
        expected_cost_gate.get("target_price"),
        expected_cost_gate.get("take_profit"),
        context.get("target_price"),
        context.get("take_profit"),
        selection_context.get("target_price"),
        selection_context.get("take_profit"),
        candidate.get("target_price"),
        candidate.get("take_profit"),
        decision.take_profit,
    )
    if entry_price <= 0 or target_price is None:
        return None, "missing_target_or_entry"
    if decision.decision == "long" and target_price > entry_price:
        return ((target_price - entry_price) / entry_price) * 10_000, "take_profit_distance"
    if decision.decision == "short" and target_price < entry_price:
        return ((entry_price - target_price) / entry_price) * 10_000, "take_profit_distance"
    return None, "invalid_target_direction"


def _expected_loss_bps(decision: TradeDecision, market_snapshot: MarketSnapshotPayload) -> float | None:
    entry_price = _entry_price(decision, market_snapshot)
    if entry_price <= 0.0 or decision.stop_loss is None:
        return None
    if decision.decision == "long" and decision.stop_loss < entry_price:
        return ((entry_price - decision.stop_loss) / entry_price) * 10_000
    if decision.decision == "short" and decision.stop_loss > entry_price:
        return ((decision.stop_loss - entry_price) / entry_price) * 10_000
    return None


def _spread_bps_from_market(market_snapshot: MarketSnapshotPayload) -> tuple[float, str]:
    derivatives = market_snapshot.derivatives_context
    spread_bps = _optional_float(getattr(derivatives, "spread_bps", None))
    if spread_bps is not None and spread_bps >= 0.0:
        return spread_bps, "derivatives_context.spread_bps"
    best_bid = _optional_float(getattr(derivatives, "best_bid", None))
    best_ask = _optional_float(getattr(derivatives, "best_ask", None))
    if best_bid is not None and best_ask is not None and best_bid > 0.0 and best_ask > best_bid:
        midpoint = (best_bid + best_ask) / 2.0
        return ((best_ask - best_bid) / midpoint) * 10_000, "best_bid_ask"
    return 0.0, "unavailable"


def _funding_headwind_bps(decision: TradeDecision, market_snapshot: MarketSnapshotPayload) -> tuple[float, str]:
    funding_rate = _optional_float(getattr(market_snapshot.derivatives_context, "funding_rate", None))
    if funding_rate is None:
        return 0.0, "unavailable"
    if decision.decision == "long" and funding_rate > 0.0:
        return abs(funding_rate) * 10_000, "long_pays_positive_funding"
    if decision.decision == "short" and funding_rate < 0.0:
        return abs(funding_rate) * 10_000, "short_pays_negative_funding"
    return 0.0, "not_headwind"


def _observed_chase_bps(decision: TradeDecision, market_snapshot: MarketSnapshotPayload) -> float:
    entry_price = _entry_price(decision, market_snapshot)
    entry_min, entry_max = _entry_zone_bounds(decision, market_snapshot)
    latest_price = market_snapshot.latest_price
    if decision.decision == "long":
        chase_anchor = max(entry_price, entry_max)
        return max(((latest_price - chase_anchor) / max(chase_anchor, 1.0)) * 10_000, 0.0)
    chase_anchor = min(entry_price, entry_min)
    return max(((chase_anchor - latest_price) / max(chase_anchor, 1.0)) * 10_000, 0.0)


def _expected_holding_minutes(decision: TradeDecision, decision_context: dict[str, Any] | None) -> float | None:
    context = _as_dict(decision_context)
    expected_cost_gate = _as_dict(context.get("expected_cost_gate"))
    selection_context = _as_dict(context.get("selection_context"))
    candidate = _as_dict(selection_context.get("candidate"))
    meta_gate = _as_dict(context.get("meta_gate"))
    return _first_positive_float(
        expected_cost_gate.get("expected_holding_minutes"),
        context.get("expected_holding_minutes"),
        selection_context.get("expected_holding_minutes"),
        candidate.get("expected_holding_minutes"),
        candidate.get("max_holding_minutes"),
        meta_gate.get("expected_time_to_profit_minutes"),
        decision.max_holding_minutes,
    )


def _policy_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in {"1", "true", "yes", "on", "allow", "allowed", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "deny", "denied", "disabled", "none"}:
        return False
    return None


def _market_fallback_policy(decision_context: dict[str, Any] | None) -> tuple[bool | None, str]:
    context = _as_dict(decision_context)
    expected_cost_gate = _as_dict(context.get("expected_cost_gate"))
    execution_policy = _as_dict(context.get("execution_policy"))
    selection_context = _as_dict(context.get("selection_context"))
    selection_execution_policy = _as_dict(selection_context.get("execution_policy"))
    for source, value in (
        ("expected_cost_gate.market_fallback_allowed", expected_cost_gate.get("market_fallback_allowed")),
        ("expected_cost_gate.allow_market_fallback", expected_cost_gate.get("allow_market_fallback")),
        ("execution_policy.market_fallback_allowed", execution_policy.get("market_fallback_allowed")),
        ("execution_policy.allow_market_fallback", execution_policy.get("allow_market_fallback")),
        ("execution_policy.fallback_to_market", execution_policy.get("fallback_to_market")),
        (
            "selection_execution_policy.market_fallback_allowed",
            selection_execution_policy.get("market_fallback_allowed"),
        ),
        ("selection_execution_policy.allow_market_fallback", selection_execution_policy.get("allow_market_fallback")),
        ("selection_execution_policy.fallback_to_market", selection_execution_policy.get("fallback_to_market")),
    ):
        parsed = _policy_bool(value)
        if parsed is not None:
            return parsed, source
    for source, value in (
        ("execution_policy.fallback_order_type", execution_policy.get("fallback_order_type")),
        ("execution_policy.fallback_order_policy", execution_policy.get("fallback_order_policy")),
        ("execution_policy.fallback_type", execution_policy.get("fallback_type")),
        ("selection_execution_policy.fallback_order_type", selection_execution_policy.get("fallback_order_type")),
        ("selection_execution_policy.fallback_order_policy", selection_execution_policy.get("fallback_order_policy")),
        ("selection_execution_policy.fallback_type", selection_execution_policy.get("fallback_type")),
    ):
        normalized = _normalize_expected_entry_type(value)
        if normalized == EXPECTED_COST_ENTRY_MARKETABLE:
            return True, source
        if normalized == EXPECTED_COST_ENTRY_PASSIVE_LIMIT:
            return False, source
    return None, "unavailable"


def _has_take_profit_marker(value: object) -> bool:
    text = str(value or "").strip().lower()
    return bool(text and any(marker in text for marker in TAKE_PROFIT_CLOSE_REASON_MARKERS))


def _payload_take_profit_close_reason(payload: dict[str, Any]) -> str | None:
    for key in ("close_reason", "exit_reason", "close_component", "order_type", "protective_component"):
        value = payload.get(key)
        if _has_take_profit_marker(value):
            return str(value)
    exchange_order = _as_dict(payload.get("exchange_order"))
    for key in ("type", "order_type", "origType"):
        value = exchange_order.get(key)
        if _has_take_profit_marker(value):
            return str(value)
    execution_quality = _as_dict(payload.get("execution_quality"))
    for key in ("close_reason", "order_type", "protective_component"):
        value = execution_quality.get(key)
        if _has_take_profit_marker(value):
            return str(value)
    return None


def _position_take_profit_close_reason(position: Position) -> str | None:
    metadata = _as_dict(position.metadata_json)
    for key in ("close_reason", "exit_reason", "close_component", "order_type", "protective_component"):
        value = metadata.get(key)
        if _has_take_profit_marker(value):
            return str(value)
    return None


def _order_take_profit_close_reason(order: Order) -> str | None:
    if _has_take_profit_marker(order.order_type):
        return str(order.order_type)
    return _payload_take_profit_close_reason(_as_dict(order.metadata_json))


def _execution_take_profit_close_reason(execution: Execution) -> str | None:
    return _payload_take_profit_close_reason(_as_dict(execution.payload))


def _latest_same_symbol_side_tp_close(
    session: Session,
    *,
    symbol: str,
    side: str,
    cooldown_minutes: int,
) -> dict[str, Any] | None:
    if cooldown_minutes <= 0:
        return None
    normalized_symbol = str(symbol or "").strip().upper()
    normalized_side = str(side or "").strip().lower()
    if normalized_side not in {"long", "short"}:
        return None
    now = utcnow_naive()
    cutoff = now - timedelta(minutes=cooldown_minutes)
    positions = session.scalars(
        select(Position)
        .where(
            Position.symbol == normalized_symbol,
            Position.side == normalized_side,
            Position.status == "closed",
            Position.closed_at.is_not(None),
            Position.closed_at >= cutoff,
            Position.closed_at <= now,
        )
        .order_by(desc(Position.closed_at), desc(Position.id))
        .limit(10)
    ).all()
    for position in positions:
        close_reason = _position_take_profit_close_reason(position)
        if close_reason is None:
            orders = session.scalars(
                select(Order)
                .where(Order.position_id == position.id)
                .order_by(desc(Order.created_at), desc(Order.id))
                .limit(20)
            ).all()
            for order in orders:
                close_reason = _order_take_profit_close_reason(order)
                if close_reason is not None:
                    break
        if close_reason is None:
            executions = session.scalars(
                select(Execution)
                .where(Execution.position_id == position.id)
                .order_by(desc(Execution.created_at), desc(Execution.id))
                .limit(20)
            ).all()
            for execution in executions:
                close_reason = _execution_take_profit_close_reason(execution)
                if close_reason is not None:
                    break
        if close_reason is None or position.closed_at is None:
            continue
        minutes_since_close = max((now - position.closed_at).total_seconds() / 60.0, 0.0)
        return {
            "position_id": position.id,
            "symbol": position.symbol,
            "side": position.side,
            "closed_at": position.closed_at.isoformat(),
            "minutes_since_close": _round_float(minutes_since_close),
            "close_reason": close_reason,
            "realized_pnl": _round_float(position.realized_pnl),
            "cooldown_minutes": cooldown_minutes,
            "cooldown_remaining_minutes": _round_float(max(cooldown_minutes - minutes_since_close, 0.0)),
        }
    return None


def _force_recent_tp_reentry_limit_only(
    expected_cost_gate: dict[str, Any],
    *,
    block_or_pending: bool,
) -> None:
    existing_policy = str(expected_cost_gate.get("required_order_policy") or "").strip()
    expected_cost_gate["allow_market_fallback"] = False
    expected_cost_gate["market_fallback_allowed"] = False
    expected_cost_gate["required_order_policy"] = (
        "block_or_pending"
        if block_or_pending or existing_policy == "block_or_pending"
        else EXPECTED_PROFITABILITY_REQUIRED_ORDER_POLICY
    )
    expected_cost_gate["order_policy_reason"] = RECENT_TP_REENTRY_REQUIRES_LIMIT_ONLY_REASON_CODE


def _same_symbol_side_tp_reentry_gate(
    session: Session,
    *,
    settings_row: Setting,
    decision: TradeDecision,
    expected_cost_gate: dict[str, Any],
) -> tuple[list[str], list[str], dict[str, Any]]:
    cooldown_minutes = _risk_setting_int(
        settings_row,
        "same_symbol_side_tp_cooldown_minutes",
        SAME_SYMBOL_SIDE_TP_COOLDOWN_MINUTES_DEFAULT,
        minimum=0,
    )
    edge_uplift_bps = _risk_setting_float(
        settings_row,
        "recent_tp_reentry_edge_uplift_bps",
        RECENT_TP_REENTRY_EDGE_UPLIFT_BPS_DEFAULT,
        minimum=0.0,
    )
    gate = {
        "applied": False,
        "status": "disabled" if cooldown_minutes <= 0 else "clear",
        "reason_codes": [],
        "cooldown_minutes": cooldown_minutes,
        "recent_tp_reentry_edge_uplift_bps": _round_float(edge_uplift_bps),
        "recent_tp_close": None,
        "expected_net_bps": expected_cost_gate.get("expected_net_bps"),
        "min_required_net_bps": _as_dict(expected_cost_gate.get("thresholds")).get("min_required_net_bps"),
        "required_net_bps": None,
        "required_order_policy": expected_cost_gate.get("required_order_policy"),
        "allow_market_fallback": expected_cost_gate.get("allow_market_fallback"),
        "comparison": "block_same_symbol_side_tp_reentry_until_net_edge_exceeds_uplift",
    }
    if cooldown_minutes <= 0:
        return [], [], gate
    recent_tp_close = _latest_same_symbol_side_tp_close(
        session,
        symbol=decision.symbol,
        side=decision.decision,
        cooldown_minutes=cooldown_minutes,
    )
    if recent_tp_close is None:
        return [], [], gate

    min_required_net_bps = _optional_float(gate["min_required_net_bps"])
    if min_required_net_bps is None:
        min_required_net_bps = DEFAULT_MIN_REQUIRED_NET_BPS
    required_net_bps = min_required_net_bps + edge_uplift_bps
    expected_net_bps = _optional_float(expected_cost_gate.get("expected_net_bps"))
    gate.update(
        {
            "applied": True,
            "status": "blocked",
            "recent_tp_close": recent_tp_close,
            "expected_net_bps": _round_float(expected_net_bps),
            "min_required_net_bps": _round_float(min_required_net_bps),
            "required_net_bps": _round_float(required_net_bps),
            "reason_codes": [
                SAME_SYMBOL_SIDE_TP_COOLDOWN_REASON_CODE,
                RECENT_TP_REENTRY_EDGE_NOT_ENOUGH_REASON_CODE,
            ],
        }
    )
    if expected_net_bps is not None and expected_net_bps >= required_net_bps:
        _force_recent_tp_reentry_limit_only(expected_cost_gate, block_or_pending=False)
        gate.update(
            {
                "status": "limited_pass",
                "reason_codes": [
                    SAME_SYMBOL_SIDE_TP_COOLDOWN_REASON_CODE,
                    RECENT_TP_REENTRY_REQUIRES_LIMIT_ONLY_REASON_CODE,
                ],
                "required_order_policy": expected_cost_gate.get("required_order_policy"),
                "allow_market_fallback": expected_cost_gate.get("allow_market_fallback"),
            }
        )
        return [], [
            SAME_SYMBOL_SIDE_TP_COOLDOWN_REASON_CODE,
            RECENT_TP_REENTRY_REQUIRES_LIMIT_ONLY_REASON_CODE,
        ], gate

    _force_recent_tp_reentry_limit_only(expected_cost_gate, block_or_pending=True)
    gate.update(
        {
            "required_order_policy": expected_cost_gate.get("required_order_policy"),
            "allow_market_fallback": expected_cost_gate.get("allow_market_fallback"),
        }
    )
    return list(gate["reason_codes"]), [], gate


def _expected_cost_gate_evaluation(
    session: Session,
    *,
    settings_row: Setting,
    decision: TradeDecision,
    market_snapshot: MarketSnapshotPayload,
    decision_context: dict[str, Any] | None,
) -> tuple[list[str], dict[str, Any]]:
    defaults = get_settings()
    context = _as_dict(decision_context)
    expected_cost_gate_context = _as_dict(context.get("expected_cost_gate"))
    entry_execution_type = _expected_entry_execution_type(decision, decision_context)
    expected_edge_bps, expected_edge_source = _expected_edge_bps(decision, market_snapshot, decision_context)
    spread_bps, spread_source = _spread_bps_from_market(market_snapshot)
    explicit_spread_bps = _first_positive_float(
        expected_cost_gate_context.get("spread_cost_bps"),
        expected_cost_gate_context.get("spread_bps"),
        context.get("spread_cost_bps"),
        context.get("spread_bps"),
    )
    if explicit_spread_bps is not None:
        spread_bps = explicit_spread_bps
        spread_source = "decision_context"
    funding_headwind_bps, funding_source = _funding_headwind_bps(decision, market_snapshot)
    cost_model_config = resolve_cost_model_config(defaults)
    recent_cost_estimate = build_recent_execution_cost_estimate(
        session,
        symbol=decision.symbol,
        side=decision.decision,
        sample_limit=cost_model_config.recent_sample_limit,
    )
    explicit_round_trip_fee_bps = _first_positive_float(
        expected_cost_gate_context.get("round_trip_fee_bps"),
        expected_cost_gate_context.get("fee_bps"),
        context.get("round_trip_fee_bps"),
        context.get("fee_bps"),
    )
    explicit_entry_fee_bps = _first_positive_float(
        expected_cost_gate_context.get("entry_fee_bps"),
        context.get("entry_fee_bps"),
    )
    explicit_exit_fee_bps = _first_positive_float(
        expected_cost_gate_context.get("exit_fee_bps"),
        context.get("exit_fee_bps"),
    )
    explicit_slippage_bps = _first_positive_float(
        expected_cost_gate_context.get("expected_slippage_bps"),
        context.get("expected_slippage_bps"),
    )
    cost_estimate = calculate_expected_trade_cost(
        entry_execution_type=entry_execution_type,
        expected_gross_bps=expected_edge_bps,
        spread_cost_bps=spread_bps,
        observed_chase_bps=_observed_chase_bps(decision, market_snapshot),
        recent_estimate=recent_cost_estimate,
        explicit_round_trip_fee_bps=explicit_round_trip_fee_bps,
        explicit_entry_fee_bps=explicit_entry_fee_bps,
        explicit_exit_fee_bps=explicit_exit_fee_bps,
        explicit_slippage_bps=explicit_slippage_bps,
        config=cost_model_config,
    )
    round_trip_fee_bps = cost_estimate.round_trip_fee_bps
    expected_slippage_bps = cost_estimate.expected_slippage_bps
    expected_net_bps = cost_estimate.expected_net_bps
    fee_to_gross_ratio = cost_estimate.fee_to_gross_ratio
    recent_adverse_slippage_bps = recent_cost_estimate.adverse_slippage_bps or 0.0
    recent_slippage_samples = recent_cost_estimate.slippage_sample_count
    expected_holding_minutes = _expected_holding_minutes(decision, decision_context)
    profitability_thresholds = _expected_profitability_thresholds(decision)
    gate_enabled = _settings_bool(defaults, "expected_edge_gate_enabled", False)
    gate_shadow = _settings_bool(defaults, "expected_edge_gate_shadow", True)
    blocking_active = bool(gate_enabled and not gate_shadow)
    max_cost_to_edge_ratio = _settings_float(defaults, "max_cost_to_edge_ratio", 0.50, minimum=0.0)
    min_net_expected_edge_bps = _settings_float(
        defaults,
        "min_net_expected_edge_bps",
        cost_model_config.min_required_net_bps,
        minimum=0.0,
    )
    effective_min_gross_bps = float(profitability_thresholds["effective_min_expected_gross_bps"])
    max_fee_to_gross_ratio = float(profitability_thresholds["max_fee_to_gross_ratio"])
    min_confidence_for_entry = float(profitability_thresholds["min_confidence_for_entry"])
    tight_tp_bps = float(profitability_thresholds["tight_tp_bps"])
    symbol_side_key = str(profitability_thresholds["symbol_side_key"])
    market_fallback_policy_allowed, market_fallback_policy_source = _market_fallback_policy(decision_context)
    tight_tp_requires_limit_only = expected_edge_bps is not None and 0.0 < expected_edge_bps < tight_tp_bps
    market_data_reliable = not market_snapshot.is_stale and market_snapshot.is_complete
    required_order_policy = "market_allowed"
    allow_market_fallback = True
    order_policy_reason = "market_allowed"
    if not market_data_reliable:
        required_order_policy = "block_or_pending"
        allow_market_fallback = False
        order_policy_reason = "market_data_not_reliable"
    elif tight_tp_requires_limit_only:
        required_order_policy = EXPECTED_PROFITABILITY_REQUIRED_ORDER_POLICY
        allow_market_fallback = False
        order_policy_reason = "tight_tp_requires_limit_only"
    elif expected_net_bps is not None and expected_net_bps <= cost_estimate.min_required_net_bps:
        required_order_policy = "limit_only"
        allow_market_fallback = False
        order_policy_reason = "expected_net_bps_near_minimum"
    market_fallback_violation = False
    market_fallback_violation_source = "not_applicable"
    if tight_tp_requires_limit_only:
        if entry_execution_type != EXPECTED_COST_ENTRY_PASSIVE_LIMIT:
            market_fallback_violation = True
            market_fallback_violation_source = f"entry_execution_type:{entry_execution_type}"
        elif market_fallback_policy_allowed is True:
            market_fallback_violation = True
            market_fallback_violation_source = market_fallback_policy_source
    expected_cost_bps = (
        round_trip_fee_bps
        + expected_slippage_bps
        + spread_bps
        + funding_headwind_bps
    )
    edge_cost_margin_bps = (
        expected_edge_bps - expected_cost_bps
        if expected_edge_bps is not None
        else None
    )
    expected_loss_bps = _expected_loss_bps(decision, market_snapshot)
    cost_to_edge_ratio = (
        expected_cost_bps / expected_edge_bps
        if expected_edge_bps is not None and expected_edge_bps > 0.0
        else None
    )
    rr_after_estimated_cost = (
        edge_cost_margin_bps / expected_loss_bps
        if edge_cost_margin_bps is not None and expected_loss_bps is not None and expected_loss_bps > 0.0
        else None
    )
    reason_codes: list[str] = []
    if expected_edge_bps is None or expected_edge_bps <= 0.0:
        reason_codes.append(EXPECTED_COST_UNAVAILABLE_REASON_CODE)
        reason_codes.append(MISSING_EXPECTED_PROFITABILITY_INPUTS_REASON_CODE)
    elif expected_cost_bps >= expected_edge_bps:
        reason_codes.append(EXPECTED_COST_EXCEEDS_EDGE_REASON_CODE)
    elif edge_cost_margin_bps is not None and edge_cost_margin_bps < cost_estimate.min_required_net_bps:
        reason_codes.append(EXPECTED_EDGE_MARGIN_TOO_THIN_REASON_CODE)
    if expected_edge_bps is not None and expected_edge_bps > 0.0:
        if edge_cost_margin_bps is not None and edge_cost_margin_bps < min_net_expected_edge_bps:
            reason_codes.append(EXPECTED_EDGE_MIN_NET_NOT_MET_REASON_CODE)
        if cost_to_edge_ratio is not None and cost_to_edge_ratio > max_cost_to_edge_ratio:
            reason_codes.append(EXPECTED_EDGE_COST_RATIO_TOO_HIGH_REASON_CODE)
        if expected_net_bps is not None and expected_net_bps <= 0.0:
            reason_codes.append(EXPECTED_NET_BPS_TOO_LOW_REASON_CODE)
        if fee_to_gross_ratio is not None and fee_to_gross_ratio > max_fee_to_gross_ratio:
            reason_codes.append(FEE_TO_GROSS_RATIO_TOO_HIGH_REASON_CODE)
        if expected_edge_bps < effective_min_gross_bps:
            reason_codes.append(EXPECTED_GROSS_BPS_TOO_TIGHT_REASON_CODE)
            if symbol_side_key == "BTCUSDT:long":
                reason_codes.append(BTC_LONG_TP_TOO_TIGHT_REASON_CODE)
        if market_fallback_violation:
            reason_codes.append(MARKET_FALLBACK_NOT_ALLOWED_FOR_TIGHT_TP_REASON_CODE)
    if decision.confidence < min_confidence_for_entry:
        reason_codes.append(CONFIDENCE_BELOW_MIN_ENTRY_THRESHOLD_REASON_CODE)

    cost_related_reason_codes = {
        EXPECTED_COST_EXCEEDS_EDGE_REASON_CODE,
        EXPECTED_EDGE_MARGIN_TOO_THIN_REASON_CODE,
        EXPECTED_NET_BPS_TOO_LOW_REASON_CODE,
        FEE_TO_GROSS_RATIO_TOO_HIGH_REASON_CODE,
        EXPECTED_GROSS_BPS_TOO_TIGHT_REASON_CODE,
        BTC_LONG_TP_TOO_TIGHT_REASON_CODE,
        MARKET_FALLBACK_NOT_ALLOWED_FOR_TIGHT_TP_REASON_CODE,
        EXPECTED_EDGE_MIN_NET_NOT_MET_REASON_CODE,
        EXPECTED_EDGE_COST_RATIO_TOO_HIGH_REASON_CODE,
    }
    if (
        EXPECTED_COST_UNAVAILABLE_REASON_CODE not in reason_codes
        and any(code in reason_codes for code in cost_related_reason_codes)
    ):
        if entry_execution_type == EXPECTED_COST_ENTRY_MARKETABLE:
            reason_codes.append(MARKETABLE_ENTRY_COST_TOO_HIGH_REASON_CODE)
        if recent_adverse_slippage_bps >= min(expected_edge_bps, EXPECTED_COST_ADVERSE_SLIPPAGE_ALERT_BPS):
            reason_codes.append(ADVERSE_SLIPPAGE_TOO_HIGH_REASON_CODE)
        if funding_headwind_bps >= min(expected_edge_bps, EXPECTED_COST_FUNDING_HEADWIND_ALERT_BPS):
            reason_codes.append(FUNDING_HEADWIND_TOO_HIGH_REASON_CODE)

    cost_payload = cost_estimate.to_payload()
    would_block_reason_codes = list(dict.fromkeys(reason_codes))
    would_block = bool(would_block_reason_codes)
    enforced_reason_codes = would_block_reason_codes if blocking_active else []
    status = "pass"
    if would_block and blocking_active:
        status = "blocked"
    elif would_block:
        status = "would_block"
    debug_payload = {
        "applied": True,
        "status": status,
        "mode": "blocking" if blocking_active else "shadow",
        "gate_enabled": gate_enabled,
        "gate_shadow": gate_shadow,
        "blocking_active": blocking_active,
        "would_block": would_block,
        "would_block_reason_codes": would_block_reason_codes,
        "enforced_reason_codes": enforced_reason_codes,
        "reason_codes": would_block_reason_codes,
        "entry_execution_type": entry_execution_type,
        "expected_profit_bps": _round_float(expected_edge_bps),
        "expected_loss_bps": _round_float(expected_loss_bps),
        "expected_fee_bps": _round_float(round_trip_fee_bps),
        "expected_total_cost_bps": _round_float(expected_cost_bps),
        "net_expected_edge_bps": _round_float(edge_cost_margin_bps),
        "cost_to_edge_ratio": _round_float(cost_to_edge_ratio),
        "rr_after_estimated_cost": _round_float(rr_after_estimated_cost),
        "expected_edge_bps": _round_float(expected_edge_bps),
        "expected_edge_source": expected_edge_source,
        "expected_gross_bps": _round_float(expected_edge_bps),
        "round_trip_fee_bps": _round_float(round_trip_fee_bps),
        "expected_slippage_bps": _round_float(expected_slippage_bps),
        "spread_cost_bps": _round_float(spread_bps),
        "expected_net_bps": _round_float(expected_net_bps),
        "fee_to_gross_ratio": _round_float(fee_to_gross_ratio),
        "expected_holding_minutes": _round_float(expected_holding_minutes),
        "required_order_policy": required_order_policy,
        "allow_market_fallback": allow_market_fallback,
        "market_fallback_allowed": allow_market_fallback,
        "order_policy_reason": order_policy_reason,
        "market_fallback_policy_source": market_fallback_policy_source,
        "market_fallback_violation_source": market_fallback_violation_source,
        "expected_cost_bps": _round_float(expected_cost_bps),
        "edge_cost_margin_bps": _round_float(edge_cost_margin_bps),
        "cost_components": {
            "maker_fee_bps": cost_payload["maker_fee_bps"],
            "taker_fee_bps": cost_payload["taker_fee_bps"],
            "fee_bps": _round_float(round_trip_fee_bps),
            "round_trip_fee_bps": _round_float(round_trip_fee_bps),
            "entry_fee_bps": cost_payload["entry_fee_bps"],
            "exit_fee_bps": cost_payload["exit_fee_bps"],
            "fee_source": cost_payload["fee_source"],
            "slippage_bps": _round_float(expected_slippage_bps),
            "expected_slippage_bps": _round_float(expected_slippage_bps),
            "slippage_source": cost_payload["slippage_source"],
            "spread_bps": _round_float(spread_bps),
            "spread_cost_bps": _round_float(spread_bps),
            "spread_source": spread_source,
            "funding_headwind_bps": _round_float(funding_headwind_bps),
            "funding_source": funding_source,
            "recent_fee_bps_per_fill": cost_payload["recent_estimate"]["fee_bps_per_fill"],
            "recent_fee_sample_count": cost_payload["recent_estimate"]["fee_sample_count"],
            "recent_adverse_slippage_bps": _round_float(recent_adverse_slippage_bps),
            "recent_slippage_sample_count": recent_slippage_samples,
            "recent_estimate": cost_payload["recent_estimate"],
        },
        "thresholds": {
            "settings_slippage_threshold_bps": _round_float(settings_row.slippage_threshold_pct * 10_000),
            "adverse_slippage_alert_bps": EXPECTED_COST_ADVERSE_SLIPPAGE_ALERT_BPS,
            "funding_headwind_alert_bps": EXPECTED_COST_FUNDING_HEADWIND_ALERT_BPS,
            "minimum_edge_margin_bps": _round_float(cost_estimate.min_required_net_bps),
            "min_required_net_bps": _round_float(cost_estimate.min_required_net_bps),
            "min_net_expected_edge_bps": _round_float(min_net_expected_edge_bps),
            "max_cost_to_edge_ratio": _round_float(max_cost_to_edge_ratio),
            "min_expected_gross_bps_default": _round_float(
                profitability_thresholds["min_expected_gross_bps_default"]
            ),
            "effective_min_expected_gross_bps": _round_float(effective_min_gross_bps),
            "max_fee_to_gross_ratio": _round_float(max_fee_to_gross_ratio),
            "min_confidence_for_entry": _round_float(min_confidence_for_entry),
            "symbol_side_key": symbol_side_key,
            "symbol_side_min_expected_gross_bps": profitability_thresholds["symbol_side_min_expected_gross_bps"],
            "tight_tp_bps": _round_float(tight_tp_bps),
        },
        "comparison": "shadow_or_block_when_expected_edge_or_profitability_thresholds_fail",
    }
    return enforced_reason_codes, debug_payload


def _planned_risk_reward_ratio(
    decision: TradeDecision,
    market_snapshot: MarketSnapshotPayload,
) -> float | None:
    if decision.stop_loss is None or decision.take_profit is None:
        return None
    entry_price = _entry_price(decision, market_snapshot)
    if entry_price <= 0.0:
        return None
    if decision.decision == "long":
        risk = entry_price - decision.stop_loss
        reward = decision.take_profit - entry_price
    elif decision.decision == "short":
        risk = decision.stop_loss - entry_price
        reward = entry_price - decision.take_profit
    else:
        return None
    if risk <= 0.0 or reward <= 0.0:
        return None
    return reward / risk


def _planned_risk_reward_gate(
    decision: TradeDecision,
    market_snapshot: MarketSnapshotPayload,
) -> tuple[list[str], dict[str, Any]]:
    ratio = _planned_risk_reward_ratio(decision, market_snapshot)
    reason_codes: list[str] = []
    status = "unavailable"
    if ratio is not None:
        status = "pass"
        if ratio < MIN_PLANNED_RISK_REWARD_RATIO:
            status = "blocked"
            reason_codes.append(PLANNED_RISK_REWARD_TOO_LOW_REASON_CODE)
    return reason_codes, {
        "applied": True,
        "status": status,
        "reason_codes": reason_codes,
        "planned_risk_reward_ratio": _round_float(ratio),
        "minimum_planned_risk_reward_ratio": MIN_PLANNED_RISK_REWARD_RATIO,
        "entry_price": _round_float(_entry_price(decision, market_snapshot)),
        "stop_loss": _round_float(decision.stop_loss),
        "take_profit": _round_float(decision.take_profit),
        "comparison": "block_when_planned_risk_reward_lt_minimum",
    }


def _recent_symbol_performance_gate(
    session: Session,
    *,
    symbol: str,
) -> tuple[list[str], dict[str, Any]]:
    since = utcnow_naive() - timedelta(days=SYMBOL_RECENT_PERFORMANCE_LOOKBACK_DAYS)
    rows = list(
        session.scalars(
            select(Execution)
            .join(Order, Execution.order_id == Order.id)
            .where(
                Order.mode == "live",
                Execution.symbol == symbol,
                Execution.created_at >= since,
            )
            .order_by(Execution.created_at.desc())
            .limit(SYMBOL_RECENT_PERFORMANCE_SAMPLE_LIMIT)
        )
    )
    gross_realized_pnl = sum(_coerce_float(row.realized_pnl) for row in rows)
    fee_total = 0.0
    skipped_fee_assets: set[str] = set()
    for row in rows:
        asset = str(row.commission_asset or "USDT").upper()
        if asset != "USDT":
            skipped_fee_assets.add(asset)
            continue
        fee_total += abs(_coerce_float(row.fee_paid))
    net_pnl_after_fees = gross_realized_pnl - fee_total
    reason_codes: list[str] = []
    if len(rows) < SYMBOL_RECENT_PERFORMANCE_MIN_EXECUTIONS:
        status = "insufficient_sample"
    elif net_pnl_after_fees < 0.0:
        status = "blocked"
        reason_codes.append(SYMBOL_RECENT_PERFORMANCE_NEGATIVE_REASON_CODE)
    else:
        status = "pass"
    return reason_codes, {
        "applied": True,
        "status": status,
        "reason_codes": reason_codes,
        "symbol": symbol,
        "lookback_days": SYMBOL_RECENT_PERFORMANCE_LOOKBACK_DAYS,
        "sample_limit": SYMBOL_RECENT_PERFORMANCE_SAMPLE_LIMIT,
        "minimum_execution_count": SYMBOL_RECENT_PERFORMANCE_MIN_EXECUTIONS,
        "execution_count": len(rows),
        "gross_realized_pnl": _round_float(gross_realized_pnl),
        "fee_total": _round_float(fee_total),
        "net_pnl_after_fees": _round_float(net_pnl_after_fees),
        "skipped_fee_assets": sorted(skipped_fee_assets),
        "comparison": "block_when_recent_symbol_net_pnl_after_fees_lt_zero",
    }


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


def _exchange_leverage_for_risk(approved_leverage: float, effective_leverage_cap: float) -> float:
    leverage = min(max(approved_leverage, 0.0), max(effective_leverage_cap, 0.0))
    if leverage <= 0.0:
        return 0.0
    return float(max(1, int(leverage)))


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
    if str(order.exchange_status or "").strip().upper() in FINAL_EXCHANGE_ORDER_STATUSES:
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
    active_orders = list(session.scalars(select(Order).where(Order.mode == "live")))
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


def _normalize_portfolio_side(value: str | None) -> Literal["long", "short"] | None:
    normalized = str(value or "").strip().lower()
    if normalized in {"long", "buy"}:
        return "long"
    if normalized in {"short", "sell"}:
        return "short"
    return None


def _margin_exposure(notional: float, leverage: float) -> float:
    return notional / max(float(leverage or 0.0), 1.0)


def _max_same_direction_major_exposure_pct(defaults: Any | None = None) -> float:
    source = defaults if defaults is not None else get_settings()
    return max(
        _coerce_float(
            getattr(source, "max_same_direction_major_exposure_pct", None),
            DEFAULT_MAX_SAME_DIRECTION_MAJOR_EXPOSURE_PCT,
        ),
        0.0,
    )


def _build_portfolio_exposure_gate(
    session: Session,
    settings_row: Setting,
    decision: TradeDecision,
    *,
    equity: float,
    exposure_limits: dict[str, float],
    projected_notional: float,
    approved_leverage: float,
    defaults: Any | None = None,
) -> dict[str, Any]:
    safe_equity = max(float(equity or 0.0), 1.0)
    candidate_symbol = decision.symbol.upper()
    candidate_direction = _normalize_portfolio_side(decision.decision)
    max_same_direction_major_exposure_pct = _max_same_direction_major_exposure_pct(defaults)
    items: list[dict[str, Any]] = []

    def append_item(
        *,
        source: str,
        symbol: str,
        side: str | None,
        notional: float,
        leverage: float,
    ) -> None:
        normalized_side = _normalize_portfolio_side(side)
        if normalized_side is None or notional <= 0.0:
            return
        symbol_key = symbol.upper()
        items.append(
            {
                "source": source,
                "symbol": symbol_key,
                "side": normalized_side,
                "notional": round(float(notional), 6),
                "margin": round(_margin_exposure(float(notional), leverage), 6),
                "tier": get_symbol_risk_tier(symbol_key),
            }
        )

    for position in get_open_positions(session):
        mark_price = position.mark_price if position.mark_price > 0 else position.entry_price
        append_item(
            source="position",
            symbol=position.symbol,
            side=position.side,
            notional=_position_notional(position.quantity, mark_price),
            leverage=position.leverage,
        )

    for order in session.scalars(select(Order).where(Order.mode == "live")):
        if not _is_exposure_reserving_order(order):
            continue
        reference_price = _order_reference_price(order)
        append_item(
            source="open_order",
            symbol=order.symbol,
            side=_order_exposure_side(order),
            notional=_position_notional(_remaining_order_quantity(order), reference_price),
            leverage=_effective_leverage_cap(settings_row, order.symbol),
        )

    if candidate_direction is not None and projected_notional > 0.0:
        append_item(
            source="candidate",
            symbol=candidate_symbol,
            side=candidate_direction,
            notional=projected_notional,
            leverage=approved_leverage,
        )

    current_items = [item for item in items if item["source"] != "candidate"]
    total_notional = sum(float(item["notional"]) for item in items)
    total_margin = sum(float(item["margin"]) for item in items)
    current_notional = sum(float(item["notional"]) for item in current_items)
    candidate_notional = sum(float(item["notional"]) for item in items if item["source"] == "candidate")
    long_exposure = sum(float(item["notional"]) for item in items if item["side"] == "long")
    short_exposure = sum(float(item["notional"]) for item in items if item["side"] == "short")
    directional_bias = abs(long_exposure - short_exposure)
    symbol_notionals: dict[str, float] = {}
    for item in items:
        symbol_key = str(item["symbol"])
        symbol_notionals[symbol_key] = symbol_notionals.get(symbol_key, 0.0) + float(item["notional"])
    max_single_position_exposure = max(symbol_notionals.values(), default=0.0)
    candidate_tier = get_symbol_risk_tier(candidate_symbol)
    same_tier_concentration_notional = sum(
        float(item["notional"])
        for item in items
        if item["tier"] == candidate_tier
    )
    correlated_symbol_exposure = sum(
        float(item["notional"])
        for item in items
        if str(item["symbol"]) in HIGH_CORRELATION_MAJOR_SYMBOLS
    )
    major_directional_exposure_by_side = {
        "long": sum(
            float(item["notional"])
            for item in items
            if str(item["symbol"]) in HIGH_CORRELATION_MAJOR_SYMBOLS and item["side"] == "long"
        ),
        "short": sum(
            float(item["notional"])
            for item in items
            if str(item["symbol"]) in HIGH_CORRELATION_MAJOR_SYMBOLS and item["side"] == "short"
        ),
    }
    combined_major_directional_exposure = (
        major_directional_exposure_by_side.get(candidate_direction, 0.0)
        if candidate_symbol in HIGH_CORRELATION_MAJOR_SYMBOLS and candidate_direction is not None
        else 0.0
    )
    reason_codes: list[str] = []
    if total_notional / safe_equity > float(exposure_limits["gross_exposure_pct"]) + 1e-9:
        reason_codes.append("GROSS_EXPOSURE_LIMIT_REACHED")
    if max_single_position_exposure / safe_equity > float(exposure_limits["largest_position_pct"]) + 1e-9:
        reason_codes.append("LARGEST_POSITION_LIMIT_REACHED")
    if same_tier_concentration_notional / safe_equity > float(exposure_limits["same_tier_concentration_pct"]) + 1e-9:
        reason_codes.append("SAME_TIER_CONCENTRATION_LIMIT_REACHED")
    if directional_bias / safe_equity > float(exposure_limits["directional_bias_pct"]) + 1e-9:
        reason_codes.append("DIRECTIONAL_BIAS_LIMIT_REACHED")
    if (
        candidate_symbol in HIGH_CORRELATION_MAJOR_SYMBOLS
        and combined_major_directional_exposure / safe_equity > max_same_direction_major_exposure_pct + 1e-9
    ):
        reason_codes.append(CORRELATED_EXPOSURE_LIMIT_REASON_CODE)
    reason_codes = list(dict.fromkeys(reason_codes))

    return {
        "applied": True,
        "status": "blocked" if reason_codes else "passed",
        "reason_code": reason_codes[0] if reason_codes else None,
        "reason_codes": reason_codes,
        "blocked_reason_codes": reason_codes,
        "symbol": candidate_symbol,
        "decision": decision.decision,
        "timeframe": decision.timeframe,
        "candidate_symbol": candidate_symbol,
        "candidate_direction": candidate_direction,
        "total_notional_exposure": round(total_notional, 6),
        "total_margin_exposure": round(total_margin, 6),
        "current_notional_exposure": round(current_notional, 6),
        "candidate_notional_exposure": round(candidate_notional, 6),
        "long_exposure": round(long_exposure, 6),
        "short_exposure": round(short_exposure, 6),
        "directional_bias": round(directional_bias, 6),
        "directional_bias_pct": round(directional_bias / safe_equity, 6),
        "max_single_position_exposure": round(max_single_position_exposure, 6),
        "max_single_position_exposure_pct": round(max_single_position_exposure / safe_equity, 6),
        "same_tier_concentration": round(same_tier_concentration_notional / safe_equity, 6),
        "same_tier_concentration_notional": round(same_tier_concentration_notional, 6),
        "correlated_symbol_exposure": round(correlated_symbol_exposure, 6),
        "correlated_symbol_exposure_pct": round(correlated_symbol_exposure / safe_equity, 6),
        "combined_BTC_ETH_directional_exposure": round(combined_major_directional_exposure, 6),
        "combined_BTC_ETH_directional_exposure_pct": round(
            combined_major_directional_exposure / safe_equity,
            6,
        ),
        "combined_BTC_ETH_directional_exposure_by_side": {
            key: round(value, 6)
            for key, value in major_directional_exposure_by_side.items()
        },
        "open_position_count": float(sum(1 for item in items if item["source"] == "position")),
        "open_order_count": float(sum(1 for item in items if item["source"] == "open_order")),
        "limits": {
            **exposure_limits,
            "max_same_direction_major_exposure_pct": round(max_same_direction_major_exposure_pct, 6),
        },
        "high_correlation_major_symbols": sorted(HIGH_CORRELATION_MAJOR_SYMBOLS),
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
    blocked_reason: str | None = None
    degraded_reason: str | None = None
    approval_required_reason: str | None = None
    policy_source = "none"
    event_control_payload: EventOperatorControlPayload | None = None
    evaluated_operator_policy = None
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
        and is_survival_path_intent(decision)
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
    expected_cost_gate: dict[str, Any] = {"applied": False, "status": "not_entry_decision"}
    portfolio_exposure_gate: dict[str, Any] = {"applied": False, "status": "not_entry_decision"}
    planned_risk_reward_gate: dict[str, Any] = {"applied": False, "status": "not_entry_decision"}
    symbol_recent_performance_gate: dict[str, Any] = {"applied": False, "status": "not_entry_decision"}
    recent_tp_reentry_gate: dict[str, Any] = {"applied": False, "status": "not_entry_decision"}
    range_mr_cooldown_gate: dict[str, Any] = {"applied": False, "status": "not_entry_decision"}
    safe_profile_selection: dict[str, Any] = {"status": "not_evaluated"}
    decision_agreement = _decision_agreement_context(decision_context)
    setup_cluster_state = _setup_cluster_state_context(decision_context)
    suppression_context = _recent_performance_suppression_context(decision_context, decision)
    meta_gate = _meta_gate_context(decision_context)
    event_result_context = _macro_event_result_context(decision_context)
    macro_event_result_conflict_reason = _macro_event_result_conflict_reason(
        decision=decision,
        event_result_context=event_result_context,
    )
    holding_profile = _holding_profile_context(decision, decision_context)
    holding_profile_name = str(holding_profile["holding_profile"])
    holding_profile_policy = (
        dict(holding_profile.get("risk_policy"))
        if isinstance(holding_profile.get("risk_policy"), dict)
        else resolve_holding_profile_risk_policy(holding_profile_name)
    )
    slot_allocation = _slot_allocation_context(decision_context)
    lead_market_context = _lead_market_context_state(decision_context)
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
    survival_path_label = _survival_path_label(decision, is_protection_recovery=is_protection_recovery)
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
    binance_rest_summary = get_binance_rest_detail(
        settings_row,
        sync_freshness_summary=sync_freshness_summary,
    )
    safe_profile_selection = select_safe_execution_risk_profile(
        settings_row,
        market_snapshot,
        defaults=defaults,
        symbol=decision.symbol,
        decision_context=decision_context,
        sync_freshness_summary=sync_freshness_summary,
        operating_state=operating_state,
        live_requested=live_requested,
    )
    ai_decision_validity = _evaluate_ai_decision_validity(
        session=session,
        defaults=defaults,
        decision=decision,
        market_snapshot=market_snapshot,
        decision_run_id=decision_run_id,
        market_snapshot_id=market_snapshot_id,
        is_entry_decision=is_entry_decision,
        execution_mode=execution_mode,
        decision_context=decision_context,
        sync_freshness_summary=sync_freshness_summary,
    )
    if is_entry_decision:
        blocked_reason_codes.extend(_as_string_list(ai_decision_validity.get("reason_codes")))
    if is_entry_decision and bool(safe_profile_selection.get("active_profile_blocks_new_entry", False)):
        blocked_reason_codes.append(EXECUTION_RISK_PROFILE_BLOCK_REASON_CODE)

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
    if (
        is_entry_decision
        and _is_non_lead_alt_symbol(decision.symbol)
        and lead_market_context["lead_context_status"] == "unavailable"
    ):
        blocked_reason_codes.append(ALT_ENTRY_LEAD_CONTEXT_UNAVAILABLE_REASON_CODE)
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
    if is_entry_decision and macro_event_result_conflict_reason is not None:
        blocked_reason_codes.append(macro_event_result_conflict_reason)
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
        binance_rest_block_reason = get_binance_rest_entry_block_reason_code(
            settings_row,
            sync_freshness_summary=sync_freshness_summary,
        )
        if binance_rest_block_reason is not None:
            blocked_reason_codes.append(binance_rest_block_reason)
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
    if is_entry_decision:
        planned_risk_reward_reason_codes, planned_risk_reward_gate = _planned_risk_reward_gate(
            decision,
            market_snapshot,
        )
        blocked_reason_codes.extend(planned_risk_reward_reason_codes)
    if is_entry_decision:
        expected_cost_reason_codes, expected_cost_gate = _expected_cost_gate_evaluation(
            session,
            settings_row=settings_row,
            decision=decision,
            market_snapshot=market_snapshot,
            decision_context=decision_context,
        )
        blocked_reason_codes.extend(expected_cost_reason_codes)
        recent_tp_reentry_reason_codes, recent_tp_reentry_adjustment_codes, recent_tp_reentry_gate = (
            _same_symbol_side_tp_reentry_gate(
                session,
                settings_row=settings_row,
                decision=decision,
                expected_cost_gate=expected_cost_gate,
            )
        )
        blocked_reason_codes.extend(recent_tp_reentry_reason_codes)
        adjustment_reason_codes.extend(recent_tp_reentry_adjustment_codes)
        range_mr_cooldown_reason_codes, range_mr_cooldown_gate = evaluate_range_mr_cooldown_gate(
            session,
            decision,
            decision_context,
        )
        blocked_reason_codes.extend(range_mr_cooldown_reason_codes)
    if is_entry_decision:
        symbol_performance_reason_codes, symbol_recent_performance_gate = _recent_symbol_performance_gate(
            session,
            symbol=decision.symbol,
        )
        blocked_reason_codes.extend(symbol_performance_reason_codes)
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

    existing_entry_blockers_before_event_policy = len(blocked_reason_codes) > 0
    if decision.decision != "hold":
        event_control_payload = build_event_operator_control_payload(
            session=session,
            settings_row=settings_row,
            symbol=decision.symbol,
            timeframe=decision.timeframe,
            ai_event_view=derive_ai_event_view(output_payload=decision.model_dump(mode="json")),
            evaluated_at=utcnow_aware(),
        )
        evaluated_operator_policy = event_control_payload.evaluated_operator_policy
        policy_source = str(event_control_payload.policy_source or "none") or "none"
        if event_control_payload.degraded_reason is not None:
            degraded_reason = event_control_payload.degraded_reason
        if is_entry_decision and not existing_entry_blockers_before_event_policy:
            if event_control_payload.blocked_reason in EVENT_POLICY_BLOCK_REASON_CODES:
                blocked_reason = event_control_payload.blocked_reason
                blocked_reason_codes.append(blocked_reason)
                degraded_reason = None
            elif event_control_payload.approval_required_reason in EVENT_POLICY_APPROVAL_REASON_CODES:
                approval_required_reason = event_control_payload.approval_required_reason
                blocked_reason_codes.append(approval_required_reason)
                degraded_reason = None

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
                if max_additional_notional < minimum_actionable_notional:
                    blocked_reason_codes.extend(requested_exposure_limit_codes)
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
    raw_approved_leverage = 0.0
    combined_risk_multiplier = 1.0
    combined_leverage_multiplier = 1.0
    exchange_leverage_notional_cap = 0.0
    exchange_leverage_adjusted_notional = 0.0
    exchange_leverage_adjusted_quantity: float | None = None
    exchange_leverage_adjustment_reason_code: str | None = None
    exchange_leverage_reason_code: str | None = None
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
            raw_approved_leverage = round(
                min(
                    decision.leverage * combined_leverage_multiplier,
                    effective_leverage_cap,
                ),
                6,
            )
            if same_side_pyramiding and existing_position is not None and existing_position.leverage > 0:
                raw_approved_leverage = round(min(raw_approved_leverage, float(existing_position.leverage)), 6)
            approved_leverage = _exchange_leverage_for_risk(raw_approved_leverage, effective_leverage_cap)
        else:
            approved_risk_pct = decision.risk_pct
            raw_approved_leverage = min(decision.leverage, effective_leverage_cap)
            approved_leverage = raw_approved_leverage
    if allowed and is_entry_decision and approved_projected_notional > 0.0:
        exchange_leverage_notional_cap = round(max(latest_pnl.equity, 0.0) * approved_leverage, 6)
        if approved_leverage <= 0.0 or exchange_leverage_notional_cap <= 0.0:
            blocked_reason_codes.append("ENTRY_SIZE_BELOW_MIN_NOTIONAL")
            approved_projected_notional = 0.0
            approved_quantity = None
            approved_risk_pct = 0.0
            approved_leverage = 0.0
        elif approved_projected_notional > exchange_leverage_notional_cap + 1e-9:
            exchange_target_quantity = (
                min(
                    approved_quantity or raw_projected_quantity,
                    exchange_leverage_notional_cap / max(raw_size["entry_price"], 1.0),
                )
                if (approved_quantity or raw_projected_quantity) > 0
                else 0.0
            )
            exchange_leverage_payload = _normalize_entry_size_for_risk(
                settings_row,
                symbol=decision.symbol,
                quantity=exchange_target_quantity,
                reference_price=raw_size["entry_price"],
                approved_notional=exchange_leverage_notional_cap,
                exchange_client=exchange_client,
                enable_exchange_filters=execution_mode == "live" or exchange_client is not None,
            )
            exchange_leverage_adjusted_notional = _coerce_float(exchange_leverage_payload.get("notional"))
            exchange_leverage_adjusted_quantity = (
                _coerce_float(exchange_leverage_payload.get("quantity"))
                if _coerce_float(exchange_leverage_payload.get("quantity")) > 0
                else None
            )
            exchange_leverage_reason_code = (
                str(exchange_leverage_payload.get("reason_code") or "").strip() or None
            )
            if exchange_leverage_reason_code is not None:
                blocked_reason_codes.append("ENTRY_SIZE_BELOW_MIN_NOTIONAL")
                approved_projected_notional = 0.0
                approved_quantity = None
                approved_risk_pct = 0.0
                approved_leverage = 0.0
            else:
                approved_projected_notional = exchange_leverage_adjusted_notional
                approved_quantity = (
                    exchange_leverage_adjusted_quantity
                    if exchange_leverage_adjusted_quantity is not None and exchange_leverage_adjusted_quantity > 0
                    else None
                )
                resized_projected_notional = approved_projected_notional
                resized_projected_quantity = approved_quantity
                exposure_metrics = _build_exposure_metrics(
                    session,
                    decision.symbol,
                    latest_pnl.equity,
                    projected_side=decision.decision,
                    projected_notional=approved_projected_notional,
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
                auto_resized_entry = True
                size_adjustment_ratio = round(
                    approved_projected_notional / max(raw_projected_notional, 1e-9),
                    6,
                )
                auto_resize_reason = "CLAMPED_TO_EXCHANGE_LEVERAGE"
                exchange_leverage_adjustment_reason_code = "ENTRY_CLAMPED_TO_EXCHANGE_LEVERAGE"
                adjustment_reason_codes.extend(["ENTRY_AUTO_RESIZED", exchange_leverage_adjustment_reason_code])
    if is_entry_decision:
        portfolio_candidate_notional = approved_projected_notional
        if portfolio_candidate_notional <= 0.0 and requested_exchange_reason_code is None:
            portfolio_candidate_notional = requested_exchange_notional
        portfolio_exposure_gate = _build_portfolio_exposure_gate(
            session,
            settings_row,
            decision,
            equity=latest_pnl.equity,
            exposure_limits=exposure_limits,
            projected_notional=portfolio_candidate_notional,
            approved_leverage=(
                approved_leverage
                if approved_leverage > 0.0
                else min(decision.leverage, effective_leverage_cap)
            ),
            defaults=defaults,
        )
        portfolio_gate_reason_codes = _as_string_list(portfolio_exposure_gate.get("reason_codes"))
        if CORRELATED_EXPOSURE_LIMIT_REASON_CODE in portfolio_gate_reason_codes:
            blocked_reason_codes.append(CORRELATED_EXPOSURE_LIMIT_REASON_CODE)
    blocked_reason_codes = list(dict.fromkeys(blocked_reason_codes))
    adjustment_reason_codes = list(dict.fromkeys(adjustment_reason_codes))
    reason_codes = list(blocked_reason_codes)
    allowed = len(blocked_reason_codes) == 0
    if is_entry_decision and bool(portfolio_exposure_gate.get("applied", False)):
        portfolio_gate_would_block_reason_codes = _as_string_list(portfolio_exposure_gate.get("reason_codes"))
        portfolio_gate_enforced_reason_codes = [
            code
            for code in portfolio_gate_would_block_reason_codes
            if code in blocked_reason_codes
        ]
        portfolio_exposure_gate = {
            **portfolio_exposure_gate,
            "status": "blocked" if portfolio_gate_enforced_reason_codes else "passed",
            "reason_code": portfolio_gate_enforced_reason_codes[0] if portfolio_gate_enforced_reason_codes else None,
            "reason_codes": portfolio_gate_enforced_reason_codes,
            "blocked_reason_codes": portfolio_gate_enforced_reason_codes,
            "would_block_reason_codes": portfolio_gate_would_block_reason_codes,
            "enforced_reason_codes": portfolio_gate_enforced_reason_codes,
        }
    if not allowed:
        approved_risk_pct = 0.0
        approved_leverage = 0.0
        approved_projected_notional = 0.0
        approved_quantity = None
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
        "exchange_leverage": {
            "raw_approved_leverage": _round_float(raw_approved_leverage),
            "approved_exchange_leverage": _round_float(approved_leverage),
            "notional_cap": _round_float(exchange_leverage_notional_cap),
            "adjusted_notional": _round_float(
                exchange_leverage_adjusted_notional if exchange_leverage_adjusted_notional > 0 else None
            ),
            "adjusted_quantity": _round_float(exchange_leverage_adjusted_quantity),
            "adjustment_reason_code": exchange_leverage_adjustment_reason_code,
            "exchange_reason_code": exchange_leverage_reason_code,
            "comparison": "approved_notional_capped_to_integer_exchange_leverage",
        },
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
        "planned_risk_reward_gate": planned_risk_reward_gate,
        "expected_cost_gate": expected_cost_gate,
        "portfolio_exposure_gate": portfolio_exposure_gate,
        "required_order_policy": expected_cost_gate.get("required_order_policy"),
        "allow_market_fallback": expected_cost_gate.get("allow_market_fallback"),
        "order_policy_reason": expected_cost_gate.get("order_policy_reason"),
        "recent_tp_reentry_gate": recent_tp_reentry_gate,
        "range_mr_cooldown_gate": range_mr_cooldown_gate,
        "symbol_recent_performance_gate": symbol_recent_performance_gate,
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
        "lead_market_context": lead_market_context,
        "macro_event_result_policy": {
            "context": event_result_context,
            "conflict_reason_code": macro_event_result_conflict_reason,
            "applied": bool(is_entry_decision and macro_event_result_conflict_reason is not None),
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
        "binance_rest_summary": binance_rest_summary,
        "safe_profile_selector": safe_profile_selection,
        "ai_decision_validity": ai_decision_validity,
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
        "event_policy": {
            "blocked_reason": blocked_reason,
            "degraded_reason": degraded_reason,
            "approval_required_reason": approval_required_reason,
            "policy_source": policy_source,
            "evaluated_operator_policy": (
                evaluated_operator_policy.model_dump(mode="json") if evaluated_operator_policy is not None else None
            ),
            "event_context_source_status": (
                event_control_payload.event_context.source_status if event_control_payload is not None else None
            ),
            "event_context_is_stale": (
                bool(event_control_payload.event_context.is_stale) if event_control_payload is not None else False
            ),
        },
    }
    degraded_reason_codes = derive_degraded_reason_codes(
        blocked_reason_codes,
        operating_state=operating_state,
        degraded_reason=degraded_reason,
    )
    protection_reason_codes = derive_protection_reason_codes(
        blocked_reason_codes,
        operating_state=operating_state,
    )
    result = RiskCheckResult(
        allowed=allowed,
        decision=decision.decision,
        reason_codes=reason_codes,
        blocked_reason_codes=blocked_reason_codes,
        adjustment_reason_codes=adjustment_reason_codes,
        degraded_reason_codes=degraded_reason_codes,
        protection_reason_codes=protection_reason_codes,
        blocked_reason=blocked_reason,
        degraded_reason=degraded_reason,
        approval_required_reason=approval_required_reason,
        survival_path=survival_path_label,
        policy_source=policy_source,  # type: ignore[arg-type]
        evaluated_operator_policy=evaluated_operator_policy,
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
    profile_ignored_codes = _as_string_list(safe_profile_selection.get("ignored_reason_codes"))
    meaningful_profile_ignored_codes = [
        code for code in profile_ignored_codes if code != "AI_MARKET_SETTINGS_RECOMMENDATION_MISSING"
    ]
    profile_event_required = bool(
        is_entry_decision
        and (
            safe_profile_selection.get("previous_profile")
            != safe_profile_selection.get("final_active_profile")
            or safe_profile_selection.get("was_tightened_by_ai")
            or safe_profile_selection.get("was_relaxation_blocked")
            or safe_profile_selection.get("active_profile_blocks_new_entry")
            or safe_profile_selection.get("hard_condition_reason_codes")
            or meaningful_profile_ignored_codes
        )
    )
    if profile_event_required:
        record_audit_event(
            session,
            event_type="execution_risk_profile_selected",
            entity_type="risk_check",
            entity_id=str(row.id),
            message="Safe profile selector selected the active execution risk profile.",
            severity=(
                "warning"
                if safe_profile_selection.get("active_profile_blocks_new_entry")
                or safe_profile_selection.get("was_relaxation_blocked")
                or safe_profile_selection.get("hard_condition_reason_codes")
                else "info"
            ),
            payload={**safe_profile_selection, "risk_check_id": row.id},
            correlation_ids={
                "decision_id": decision_run_id,
                "snapshot_id": market_snapshot_id,
                "risk_id": row.id,
            },
        )
        session.flush()
    if is_entry_decision and bool(expected_cost_gate.get("applied", False)):
        expected_edge_audit_payload = {
            "symbol": decision.symbol,
            "decision": decision.decision,
            "timeframe": decision.timeframe,
            "mode": expected_cost_gate.get("mode"),
            "gate_enabled": expected_cost_gate.get("gate_enabled"),
            "gate_shadow": expected_cost_gate.get("gate_shadow"),
            "blocking_active": expected_cost_gate.get("blocking_active"),
            "would_block": expected_cost_gate.get("would_block"),
            "would_block_reason_codes": list(expected_cost_gate.get("would_block_reason_codes") or []),
            "enforced_reason_codes": list(expected_cost_gate.get("enforced_reason_codes") or []),
            "expected_profit_bps": expected_cost_gate.get("expected_profit_bps"),
            "expected_loss_bps": expected_cost_gate.get("expected_loss_bps"),
            "expected_fee_bps": expected_cost_gate.get("expected_fee_bps"),
            "expected_slippage_bps": expected_cost_gate.get("expected_slippage_bps"),
            "spread_cost_bps": expected_cost_gate.get("spread_cost_bps"),
            "expected_total_cost_bps": expected_cost_gate.get("expected_total_cost_bps"),
            "net_expected_edge_bps": expected_cost_gate.get("net_expected_edge_bps"),
            "cost_to_edge_ratio": expected_cost_gate.get("cost_to_edge_ratio"),
            "rr_after_estimated_cost": expected_cost_gate.get("rr_after_estimated_cost"),
            "entry_execution_type": expected_cost_gate.get("entry_execution_type"),
            "required_order_policy": expected_cost_gate.get("required_order_policy"),
            "thresholds": {
                "min_net_expected_edge_bps": _as_dict(expected_cost_gate.get("thresholds")).get(
                    "min_net_expected_edge_bps"
                ),
                "max_cost_to_edge_ratio": _as_dict(expected_cost_gate.get("thresholds")).get(
                    "max_cost_to_edge_ratio"
                ),
                "settings_slippage_threshold_bps": _as_dict(expected_cost_gate.get("thresholds")).get(
                    "settings_slippage_threshold_bps"
                ),
            },
            "risk_check_id": row.id,
        }
        record_audit_event(
            session,
            event_type="risk_expected_edge_gate",
            entity_type="risk_check",
            entity_id=str(row.id),
            message="Expected edge gate evaluated a new-entry candidate.",
            severity="warning" if bool(expected_cost_gate.get("would_block", False)) else "info",
            payload=expected_edge_audit_payload,
            correlation_ids={
                "decision_id": decision_run_id,
                "snapshot_id": market_snapshot_id,
                "risk_id": row.id,
            },
        )
        session.flush()
    if is_entry_decision and bool(portfolio_exposure_gate.get("applied", False)):
        portfolio_reason_codes = _as_string_list(portfolio_exposure_gate.get("reason_codes"))
        portfolio_event_specs: list[tuple[str, str, str | None]] = []
        if not portfolio_reason_codes:
            portfolio_event_specs.append(
                (
                    "portfolio_exposure_evaluated",
                    "Portfolio exposure gate evaluated a new-entry candidate.",
                    None,
                )
            )
        else:
            portfolio_general_codes = [
                code
                for code in portfolio_reason_codes
                if code
                in {
                    "GROSS_EXPOSURE_LIMIT_REACHED",
                    "LARGEST_POSITION_LIMIT_REACHED",
                    "SAME_TIER_CONCENTRATION_LIMIT_REACHED",
                }
            ]
            if CORRELATED_EXPOSURE_LIMIT_REASON_CODE in portfolio_reason_codes:
                portfolio_event_specs.append(
                    (
                        "correlated_exposure_blocked",
                        "Correlated BTC/ETH exposure gate blocked a new entry.",
                        CORRELATED_EXPOSURE_LIMIT_REASON_CODE,
                    )
                )
            if "DIRECTIONAL_BIAS_LIMIT_REACHED" in portfolio_reason_codes:
                portfolio_event_specs.append(
                    (
                        "directional_bias_blocked",
                        "Portfolio directional bias gate blocked a new entry.",
                        "DIRECTIONAL_BIAS_LIMIT_REACHED",
                    )
                )
            if portfolio_general_codes:
                portfolio_event_specs.append(
                    (
                        "portfolio_exposure_blocked",
                        "Portfolio exposure gate blocked a new entry.",
                        portfolio_general_codes[0],
                    )
                )
        for event_type, message, reason_code in portfolio_event_specs:
            record_audit_event(
                session,
                event_type=event_type,
                entity_type="risk_check",
                entity_id=str(row.id),
                message=message,
                severity="warning" if portfolio_reason_codes else "info",
                payload={
                    **portfolio_exposure_gate,
                    "reason_code": reason_code,
                    "risk_check_id": row.id,
                    "blocked_reason_codes": portfolio_reason_codes,
                },
                correlation_ids={
                    "decision_id": decision_run_id,
                    "snapshot_id": market_snapshot_id,
                    "risk_id": row.id,
                },
        )
        session.flush()
    if (
        is_entry_decision
        and bool(range_mr_cooldown_gate.get("applied", False))
        and RANGE_MR_COOLDOWN_BLOCK_REASON_CODE in blocked_reason_codes
    ):
        record_audit_event(
            session,
            event_type="range_mean_reversion_cooldown_blocked",
            entity_type="risk_check",
            entity_id=str(row.id),
            message="Range mean reversion cooldown blocked a new-entry candidate.",
            severity="warning",
            payload={
                **range_mr_cooldown_gate,
                "risk_check_id": row.id,
                "blocked_reason_codes": _as_string_list(range_mr_cooldown_gate.get("reason_codes")),
            },
            correlation_ids={
                "decision_id": decision_run_id,
                "snapshot_id": market_snapshot_id,
                "risk_id": row.id,
            },
        )
        session.flush()
    ai_invalidation_reason_codes = [
        code
        for code in _as_string_list(ai_decision_validity.get("reason_codes"))
        if code in AI_DECISION_INVALIDATION_REASON_CODES
    ]
    if ai_invalidation_reason_codes:
        event_type = str(ai_decision_validity.get("event_type") or "ai_decision_invalidated")
        record_audit_event(
            session,
            event_type=event_type,
            entity_type="agent_run",
            entity_id=str(decision_run_id),
            message=(
                "AI decision expired before deterministic risk approval."
                if event_type == "ai_decision_expired"
                else "AI decision was invalidated before deterministic risk approval."
            ),
            severity="warning",
            payload={
                **ai_decision_validity,
                "risk_check_id": row.id,
                "blocked_reason_codes": ai_invalidation_reason_codes,
            },
            correlation_ids={
                "decision_id": decision_run_id,
                "snapshot_id": market_snapshot_id,
                "risk_id": row.id,
            },
        )
        session.flush()
    if event_control_payload is not None and evaluated_operator_policy is not None:
        correlation_ids = {
            "decision_id": decision_run_id,
            "snapshot_id": market_snapshot_id,
            "risk_id": row.id,
        }
        audit_payload = _event_policy_audit_payload(
            decision=decision,
            event_control_payload=event_control_payload,
            blocked_reason=blocked_reason,
            approval_required_reason=approval_required_reason,
            degraded_reason=degraded_reason,
            policy_source=policy_source,
            survival_path=survival_path_label,
        )
        if is_entry_decision and blocked_reason in EVENT_POLICY_BLOCK_REASON_CODES:
            record_audit_event(
                session,
                event_type="event_policy_blocked_entry",
                entity_type="risk_check",
                entity_id=str(row.id),
                message="Operator event policy blocked a new entry.",
                severity="warning",
                payload=audit_payload,
                correlation_ids=correlation_ids,
            )
        elif is_entry_decision and approval_required_reason in EVENT_POLICY_APPROVAL_REASON_CODES:
            record_audit_event(
                session,
                event_type="event_policy_required_approval",
                entity_type="risk_check",
                entity_id=str(row.id),
                message="Operator event policy requires manual approval before a new entry.",
                severity="warning",
                payload=audit_payload,
                correlation_ids=correlation_ids,
            )
        elif (
            is_entry_decision
            and not existing_entry_blockers_before_event_policy
            and blocked_reason is None
            and approval_required_reason is None
            and event_control_payload.alignment_decision.alignment_status == "insufficient_data"
        ):
            record_audit_event(
                session,
                event_type="event_policy_skipped_due_to_missing_data",
                entity_type="risk_check",
                entity_id=str(row.id),
                message="Operator event policy did not block the entry because current data is insufficient.",
                severity="info",
                payload=audit_payload,
                correlation_ids=correlation_ids,
            )
        elif survival_path_label is not None and _has_operator_event_override(event_control_payload):
            record_audit_event(
                session,
                event_type="event_policy_allowed_survival_path",
                entity_type="risk_check",
                entity_id=str(row.id),
                message="Survival-path action bypassed operator event entry gating.",
                severity="info",
                payload=audit_payload,
                correlation_ids=correlation_ids,
            )
    return result, row
