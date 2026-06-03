from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from math import sqrt
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from trading_mvp.config import get_settings
from trading_mvp.enums import AgentRole, TriggerEvent
from trading_mvp.models import (
    AgentRun,
    Alert,
    Execution,
    FeatureSnapshot,
    MarketSnapshot,
    Order,
    PendingEntryPlan,
    PnLSnapshot,
    Position,
    RiskCheck,
    SystemHealthEvent,
)
from trading_mvp.providers import build_model_provider
from trading_mvp.schemas import (
    AIMarketSettingsRecommendation,
    AIReviewTriggerPayload,
    FeaturePayload,
    MarketSnapshotPayload,
    PendingEntryPlanSnapshot,
    RiskCheckResult,
    TradeDecision,
    TradeDecisionCandidate,
    TradeDecisionCandidateScore,
    WatchEntryPlan,
)
from trading_mvp.services.account import (
    account_snapshot_to_dict,
    get_latest_pnl_snapshot,
    get_open_positions,
)
from trading_mvp.services.adaptive_signal import build_adaptive_signal_context
from trading_mvp.services.agents import (
    ChiefReviewAgent,
    MarketSettingsAdvisorAgent,
    PositionExitReviewAgent,
    TradingDecisionAgent,
    build_market_settings_advisor_input_payload,
    build_trading_decision_input_payload,
    persist_agent_run,
)
from trading_mvp.services.ai_context import build_ai_decision_context
from trading_mvp.services.ai_prior_context import build_ai_prior_context
from trading_mvp.services.ai_usage import estimate_ai_usage_cost_usd, get_openai_call_gate
from trading_mvp.services.audit import (
    create_alert,
    normalize_correlation_ids,
    record_audit_event,
    record_decision_funnel_event,
    record_health_event,
)
from trading_mvp.services.capital_efficiency import build_capital_efficiency_report
from trading_mvp.services.drawdown_state import (
    DRAWDOWN_STATE_CAUTION,
    DRAWDOWN_STATE_CONTAINMENT,
    build_drawdown_state_snapshot,
)
from trading_mvp.services.event_context import EventContextProvider, resolve_event_context_provider
from trading_mvp.services.exchange_permission import (
    EXCHANGE_CAN_TRADE_UNKNOWN_REASON_CODE,
    exchange_trade_permission_entry_blocker,
)
from trading_mvp.services.execution import (
    apply_position_management,
    execute_live_trade,
    sync_live_state,
)
from trading_mvp.services.features import (
    compute_features,
    persist_feature_snapshot,
    summarize_universe_breadth,
)
from trading_mvp.services.holding_profile import (
    evaluate_holding_profile,
    resolve_holding_profile_cadence_hint,
)
from trading_mvp.services.intent_semantics import infer_intent_semantics
from trading_mvp.services.market_data import (
    build_lead_market_contexts,
    build_market_context,
    build_market_snapshot,
    persist_market_snapshot,
)
from trading_mvp.services.meta_gate import evaluate_meta_gate
from trading_mvp.services.pause_control import attempt_auto_resume
from trading_mvp.services.pending_entry_time import (
    pending_entry_expires_at,
    pending_entry_expiry_context,
    pending_entry_plan_is_expired,
    pending_entry_remaining_ttl_seconds,
    pending_entry_utc_naive,
)
from trading_mvp.services.performance_reporting import (
    _extract_analysis_context,
    refresh_decision_performance_fact_usefulness,
)
from trading_mvp.services.position_management import build_position_management_context
from trading_mvp.services.risk import (
    HARD_MAX_DAILY_LOSS,
    HARD_MAX_GLOBAL_LEVERAGE,
    HARD_MAX_RISK_PER_TRADE,
    build_ai_risk_budget_context,
    evaluate_risk,
    get_symbol_leverage_cap,
    get_symbol_risk_tier,
)
from trading_mvp.services.rule_pruning import build_keep_kill_report
from trading_mvp.services.runtime_state import (
    EMERGENCY_EXIT_STATE,
    ENTRY_BLOCKING_OPERATING_STATES,
    PAUSED_STATE,
    PROTECTION_REQUIRED_STATE,
    build_sync_freshness_summary,
    get_drawdown_state_detail,
    get_sync_state_detail,
    get_unresolved_submission_guard,
    mark_sync_skipped,
    set_candidate_selection_detail,
    set_drawdown_state_detail,
    summarize_runtime_state,
    sync_scope_blocks_new_entry,
)
from trading_mvp.services.service_gate import active_pending_entry_plan_statement
from trading_mvp.services.settings import (
    build_operational_status_payload,
    get_effective_symbol_schedule,
    get_effective_symbol_settings,
    get_effective_symbols,
    get_execution_risk_profile_policy,
    get_or_create_settings,
    get_rollout_mode,
    get_runtime_credentials,
    is_live_execution_armed,
    rollout_mode_allows_exchange_submit,
    serialize_settings,
)
from trading_mvp.services.skip_quality import build_skip_quality_report, record_skip_event
from trading_mvp.services.strategy_engine_analytics import build_strategy_engine_bucket_report
from trading_mvp.services.strategy_engines import (
    quiet_range_mean_reversion_allowed,
    select_strategy_engine,
)
from trading_mvp.time_utils import utcnow_naive

ACTIVE_ENTRY_PLAN_STATUS = "armed"
ENTRY_CANDIDATE_WEAK_VOLUME_PREAI_REASON = "ENTRY_CANDIDATE_WEAK_VOLUME_PREAI"
ENTRY_CANDIDATE_WEAK_VOLUME_CONTEXT_REASON = "ENTRY_CANDIDATE_WEAK_VOLUME_CONTEXT"
ENTRY_CANDIDATE_WEAK_VOLUME_PREAI_POLICY_REASON = "entry_candidate_weak_volume_preai_skip"
ENTRY_CANDIDATE_EXTREME_LOW_VOLUME_RATIO = 0.10
ENTRY_CANDIDATE_WEAK_VOLUME_REGIMES = frozenset(
    {"weak", "low", "thin", "dry", "illiquid", "low_participation"}
)
ENTRY_CANDIDATE_NEUTRAL_CONTEXT_HOLD_BACKOFF_REASON = "neutral_entry_review_hold_backoff_active"
ENTRY_CANDIDATE_NEUTRAL_CONTEXT_HOLD_BACKOFF_SKIP_REASON = "ENTRY_CANDIDATE_NEUTRAL_CONTEXT_HOLD_BACKOFF"
ENTRY_CANDIDATE_NEUTRAL_CONTEXT_PREAI_REASON = "neutral_entry_context_preai_skip"
ENTRY_CANDIDATE_NEUTRAL_CONTEXT_PREAI_SKIP_REASON = "ENTRY_CANDIDATE_NEUTRAL_CONTEXT_PREAI"
ENTRY_CANDIDATE_NEUTRAL_CONTEXT_REQUIRED_REASONS = frozenset(
    {"EXPECTANCY_NEUTRAL", "DERIVATIVES_NEUTRAL", "LEAD_MARKETS_NEUTRAL"}
)
ENTRY_CANDIDATE_LOW_ACTIONABILITY_HOLD_BACKOFF_REASON = (
    "entry_candidate_low_actionability_hold_backoff_active"
)
ENTRY_CANDIDATE_LOW_ACTIONABILITY_HOLD_BACKOFF_SKIP_REASON = (
    "ENTRY_CANDIDATE_LOW_ACTIONABILITY_HOLD_BACKOFF"
)
ENTRY_CANDIDATE_LOW_ACTIONABILITY_REASON_CODES = frozenset(
    {
        "EXPECTANCY_NEUTRAL",
        "DERIVATIVES_NEUTRAL",
        "LEAD_MARKETS_NEUTRAL",
        "LEAD_MARKETS_DIVERGED",
        "DERIVATIVES_HEADWIND",
        "TOP_TRADER_LONG_CROWDED",
        "TREND_MIXED",
        "REGIME_TRANSITION",
        "WEAK_VOLUME",
        "MOMENTUM_WEAKENING",
        "RANGE_WEAK_VOLUME_NO_TRADE_ZONE",
        ENTRY_CANDIDATE_WEAK_VOLUME_CONTEXT_REASON,
    }
)
ENTRY_CANDIDATE_ORDER_PATH_NOT_ACTIONABLE_REASON = "entry_candidate_order_path_not_actionable"
ENTRY_CANDIDATE_ORDER_PATH_NOT_ACTIONABLE_SKIP_REASON = "ENTRY_CANDIDATE_ORDER_PATH_NOT_ACTIONABLE"
ENTRY_CANDIDATE_ACTIVE_PENDING_PLAN_REASON = "entry_candidate_active_pending_plan_preai_skip"
ENTRY_CANDIDATE_ACTIVE_PENDING_PLAN_SKIP_REASON = "ENTRY_CANDIDATE_ACTIVE_PENDING_PLAN_PREAI"
ENTRY_CANDIDATE_INCOMPLETE_TRADE_PLAN_REASON = "entry_candidate_incomplete_trade_plan_preai_skip"
ENTRY_CANDIDATE_INCOMPLETE_TRADE_PLAN_SKIP_REASON = "ENTRY_CANDIDATE_INCOMPLETE_TRADE_PLAN_PREAI"
ENTRY_CANDIDATE_AI_HOLD_FINGERPRINT_COOLDOWN_REASON = (
    "entry_candidate_ai_hold_fingerprint_cooldown_active"
)
ENTRY_CANDIDATE_AI_HOLD_FINGERPRINT_COOLDOWN_SKIP_REASON = (
    "ENTRY_CANDIDATE_AI_HOLD_FINGERPRINT_COOLDOWN"
)
ENTRY_CANDIDATE_AI_HOLD_FINGERPRINT_COOLDOWN_MINUTES = 60
ENTRY_CANDIDATE_AI_HOLD_FINGERPRINT_ROW_LIMIT = 40
AI_CALL_EVENT_ALLOWED = "AI_CALL_ALLOWED"
AI_CALL_EVENT_SKIPPED = "AI_CALL_SKIPPED"
SOFT_SIGNAL_AI_REVIEW_REASON_CODE = "SOFT_SIGNAL_AI_REVIEW"
SOFT_SIGNAL_TRANSITION_WATCH_REASON_CODE = "SOFT_SIGNAL_TRANSITION_WATCH"
SOFT_SIGNAL_TRANSITION_WATCH_FORCED_REASON = "soft_signal_transition_watch_due"
SOFT_SIGNAL_AI_REVIEW_SUPPRESSED_REASON = "soft_signal_review_suppressed_weak_candidate"
SOFT_SIGNAL_AI_REVIEW_SUPPRESSED_SKIP_REASON = "SOFT_SIGNAL_REVIEW_SUPPRESSED_WEAK_CANDIDATE"
SOFT_SIGNAL_TRANSITION_WATCH_COOLDOWN_SKIP_REASON = "SOFT_SIGNAL_REVIEW_COOLDOWN_ACTIVE"
SOFT_SIGNAL_TRANSITION_WATCH_NO_MATERIAL_CHANGE_REASON = "soft_signal_review_no_material_change"
SOFT_SIGNAL_TRANSITION_WATCH_NO_MATERIAL_CHANGE_SKIP_REASON = "SOFT_SIGNAL_REVIEW_NO_MATERIAL_CHANGE"
SOFT_SIGNAL_DIRECT_ENTRY_BOUNDED_TO_WATCH = "SOFT_SIGNAL_DIRECT_ENTRY_BOUNDED_TO_WATCH"
SOFT_SIGNAL_DIRECT_ENTRY_BOUNDED_TO_HOLD = "SOFT_SIGNAL_DIRECT_ENTRY_BOUNDED_TO_HOLD"
SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_REASON = "soft_signal_review_hold_backoff_active"
SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_SKIP_REASON = "SOFT_SIGNAL_REVIEW_HOLD_BACKOFF_ACTIVE"
SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_MIN_CALLS = 3
SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_LOOKBACK_MINUTES = 360
SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_ROW_LIMIT = 100
AI_REVIEW_SOFT_REJECTED_REASONS = frozenset(
    {
        "breadth_hold_bias",
        "low_conviction_slot_excluded",
        "low_edge_hold_candidate",
        "score_below_threshold",
    }
)
SOFT_SIGNAL_AI_REVIEW_MIN_INTERVAL_MINUTES = 15
SOFT_SIGNAL_TRANSITION_WATCH_MAX_SCORE_GAP = 0.02
SOFT_SIGNAL_TRANSITION_WATCH_COOLDOWN_MINUTES = 60
SOFT_SIGNAL_TRANSITION_WATCH_MATERIAL_SCORE_DELTA = 0.015
SOFT_SIGNAL_TRANSITION_WATCH_MATERIAL_PROBABILITY_DELTA = 0.04
SOFT_SIGNAL_TRANSITION_WATCH_MATERIAL_ALIGNMENT_DELTA = 0.08
SOFT_SIGNAL_LOW_EDGE_HOLD_MAX_SLOT_CONVICTION = 0.58
SOFT_SIGNAL_LOW_EDGE_HOLD_MAX_META_GATE_PROBABILITY = 0.55
SOFT_SIGNAL_ACTIONABLE_REVIEW_MIN_SLOT_CONVICTION = 0.62
SOFT_SIGNAL_ACTIONABLE_REVIEW_MIN_META_GATE_PROBABILITY = 0.58
ENTRY_CANDIDATE_LATE_LONG_SKIP_REASON = "late_long_no_pullback"
ENTRY_CANDIDATE_LATE_LONG_REASON_CODE = "LATE_LONG_NO_PULLBACK"
ENTRY_CANDIDATE_LONG_EXTENSION_DERIVATIVES_REASON_CODE = "LONG_EXTENSION_DERIVATIVES_HEADWIND"
ENTRY_CANDIDATE_LATE_LONG_UPPER_RANGE_POSITION = 0.86
ENTRY_CANDIDATE_LATE_LONG_RECENT_HIGH_DISTANCE_PCT = -0.35
ENTRY_CANDIDATE_LATE_LONG_MAX_DRAWDOWN_PCT = 0.45
ENTRY_CANDIDATE_LATE_LONG_RSI = 72.0
ENTRY_CANDIDATE_LATE_LONG_VWAP_DISTANCE_PCT = 0.35
ENTRY_PLAN_WATCH_TIMEFRAME = "1m"
ENTRY_PLAN_AI_RECHECK_TRIGGER_EVENT = "entry_plan_recheck"
ENTRY_PLAN_AI_RECHECK_COOLDOWN_SECONDS = 300
CADENCE_IDLE_MODE = "idle"
CADENCE_WATCH_MODE = "watch"
CADENCE_ACTIVE_POSITION_MODE = "active_position"
CADENCE_ARMED_ENTRY_PLAN_MODE = "armed_entry_plan"
CADENCE_HIGH_PRIORITY_RECOVERY_MODE = "high_priority_recovery"
FINAL_ORDER_STATUSES = frozenset({"filled", "canceled", "cancelled", "rejected", "expired"})
ACTIVE_POSITION_ENTRY_SUPPRESSION_REASON_CODES = frozenset(
    {"LARGEST_POSITION_LIMIT_REACHED", "DETERMINISTIC_BASELINE_DISAGREEMENT"}
)
ENTRY_PLAN_NON_STRUCTURAL_BLOCKERS = {
    "CHASE_LIMIT_EXCEEDED",
    "ENTRY_TRIGGER_NOT_MET",
    "EXCHANGE_CAN_TRADE_UNKNOWN",
    "SLIPPAGE_THRESHOLD_EXCEEDED",
}
ENTRY_PLAN_WATCH_STORAGE_BLOCKERS = {
    *ENTRY_PLAN_NON_STRUCTURAL_BLOCKERS,
    "DETERMINISTIC_BASELINE_DISAGREEMENT",
}
ENTRY_PLAN_AI_RECHECK_REASON_CODES = (
    "PENDING_ENTRY_PLAN_RECHECK",
    "ENTRY_PLAN_ZONE_TOUCHED",
)
ENTRY_PLAN_WATCH_REASON_CODE = "AI_WATCH_ENTRY_PLAN"
ENTRY_PLAN_NO_CAPACITY_CANCEL_REASON_CODE = "PLAN_CANCELED_NO_ENTRY_CAPACITY"
ENTRY_PLAN_MIN_ACTIONABLE_NOTIONAL_FLOOR = 25.0
ENTRY_PLAN_SIMULATION_ROLLOUT_MODES = frozenset({"shadow", "live_dry_run"})
ENTRY_PLAN_SIMULATION_GUARD_REASON_CODES = frozenset(
    {"ROLLOUT_MODE_SHADOW", "ROLLOUT_MODE_LIVE_DRY_RUN"}
)
ENTRY_PLAN_SIMULATED_EXECUTION_STATUSES = frozenset({"shadow", "dry_run"})
TAKE_PROFIT_CLOSE_ORDER_TYPES = frozenset({"take_profit_market"})
TAKE_PROFIT_CLOSE_PROTECTIVE_COMPONENTS = frozenset({"take_profit"})
ENTRY_PLAN_CONTROL_BLOCKERS_ALLOW_LOCAL_EXPIRY = frozenset(
    {
        "LIVE_APPROVAL_REQUIRED",
        "LIVE_EXECUTION_NOT_READY",
        "MANUAL_USER_REQUEST",
        "TRADING_PAUSED",
    }
)
TRADE_BLOCKED_ALERT_NON_ACTIONABLE_REASON_CODES = frozenset(
    {
        "HOLD_DECISION",
        "ENTRY_TRIGGER_NOT_MET",
        "NO_EDGE",
        "RANGE_CHOP",
        "WEAK_VOLUME",
        "MOMENTUM_WEAKENING",
        "DETERMINISTIC_BASELINE_DISAGREEMENT",
    }
)
ENTRY_PLAN_HOLD_CANCEL_REASON_CODES = frozenset(
    {
        "ENTRY_PLAN_INVALIDATED",
        "PLAN_INVALIDATED",
        "REGIME_INVALIDATED",
        "SETUP_INVALIDATED",
        "SIGNAL_INVALIDATED",
        "STRUCTURE_INVALIDATED",
        "TREND_INVALIDATED",
        "TREND_REVERSED",
        "TREND_REVERSAL",
        "OPPOSITE_SIGNAL",
        "LEAD_MARKET_DIVERGENCE",
        "META_GATE_LEAD_LAG_DIVERGENCE",
        "META_GATE_DERIVATIVES_HEADWIND",
        "NO_TRADE_ZONE_RANGE_WEAK_VOLUME",
        "MANUAL_NO_TRADE_ACTIVE",
        "OPERATOR_FORCE_NO_TRADE",
        "OPERATOR_BIAS_NO_TRADE",
    }
)
ENTRY_PLAN_LONG_INVALIDATED_BY_REASON_CODES = frozenset(
    {"TREND_DOWN", "BEARISH_CONTINUATION", "BEARISH_CONTINUATION_REBOUND"}
)
ENTRY_PLAN_SHORT_INVALIDATED_BY_REASON_CODES = frozenset(
    {"TREND_UP", "BULLISH_CONTINUATION", "BULLISH_CONTINUATION_PULLBACK"}
)
SETUP_CLUSTER_DISABLED_REASON_CODE = "SETUP_CLUSTER_DISABLED"
SETUP_CLUSTER_LOOKBACK = 8
SETUP_CLUSTER_MIN_SAMPLE_SIZE = 4
SETUP_CLUSTER_EXPECTANCY_THRESHOLD = 0.0
SETUP_CLUSTER_NET_PNL_THRESHOLD = 0.0
SETUP_CLUSTER_LOSS_STREAK_THRESHOLD = 3
SETUP_CLUSTER_SIGNED_SLIPPAGE_BPS_THRESHOLD = 12.0
SETUP_CLUSTER_COOLDOWN_MINUTES = 180
SETUP_CLUSTER_HISTORY_LIMIT = 128


def _should_create_trade_blocked_alert(decision: str | None, reason_codes: list[str]) -> bool:
    normalized = {str(code).strip().upper() for code in reason_codes if str(code).strip()}
    if not normalized:
        return True
    if normalized.issubset(TRADE_BLOCKED_ALERT_NON_ACTIONABLE_REASON_CODES):
        return False
    return (decision or "").strip().lower() != "hold" or not normalized.issubset(
        TRADE_BLOCKED_ALERT_NON_ACTIONABLE_REASON_CODES
    )
SETUP_CLUSTER_DISABLE_REASON_CODES = {
    "expectancy": "CLUSTER_NEGATIVE_EXPECTANCY",
    "loss_streak": "CLUSTER_LOSS_STREAK",
    "signed_slippage": "CLUSTER_ADVERSE_SIGNED_SLIPPAGE",
    "net_pnl": "CLUSTER_NET_PNL_AFTER_FEES_NEGATIVE",
}
SETUP_CLUSTER_EXEMPT_RATIONALE_CODES = {
    "PROTECTION_REQUIRED",
    "PROTECTION_RECOVERY",
    "PROTECTION_RESTORE",
}
PORTFOLIO_SLOT_ORDER = ("slot_1", "slot_2", "slot_3")
PORTFOLIO_SLOT_LABELS = {
    "slot_1": "high_conviction",
    "slot_2": "medium_conviction",
    "slot_3": "medium_conviction",
}
PORTFOLIO_SLOT_HIGH_CONVICTION_THRESHOLD = 0.68
PORTFOLIO_SLOT_MEDIUM_CONVICTION_THRESHOLD = 0.54
PORTFOLIO_SLOT_BASE_WEIGHTS = {
    "slot_1": 1.0,
    "slot_2": 0.72,
    "slot_3": 0.58,
}
PORTFOLIO_SLOT_POLICY_BASE = {
    "slot_1": {"risk_pct_multiplier": 1.0, "leverage_multiplier": 1.0, "notional_multiplier": 1.0},
    "slot_2": {"risk_pct_multiplier": 0.82, "leverage_multiplier": 0.9, "notional_multiplier": 0.78},
    "slot_3": {"risk_pct_multiplier": 0.68, "leverage_multiplier": 0.82, "notional_multiplier": 0.64},
}


def _decision_analysis_context(
    feature_payload,
    *,
    universe_breadth: dict[str, object] | None = None,
) -> dict[str, object]:
    regime = feature_payload.regime
    derivatives = feature_payload.derivatives
    volume_profile = getattr(feature_payload, "volume_profile", None)
    context = {
        "regime": {
            "primary_regime": regime.primary_regime,
            "trend_alignment": regime.trend_alignment,
            "volatility_regime": regime.volatility_regime,
        },
        "flags": {
            "weak_volume": regime.weak_volume,
            "volatility_expanded": regime.volatility_regime == "expanded",
            "momentum_weakening": regime.momentum_weakening,
        },
        "derivatives": {
            "available": derivatives.available,
            "source": derivatives.source,
            "top_trader_long_short_ratio": derivatives.top_trader_long_short_ratio,
            "top_trader_crowding_bias": derivatives.top_trader_crowding_bias,
            "taker_flow_alignment": derivatives.taker_flow_alignment,
            "funding_bias": derivatives.funding_bias,
            "spread_bps": derivatives.spread_bps,
            "spread_stress_score": derivatives.spread_stress_score,
            "spread_headwind": derivatives.spread_headwind,
            "spread_stress": derivatives.spread_stress,
            "breakout_spread_headwind": derivatives.breakout_spread_headwind,
            "oi_expanding_with_price": derivatives.oi_expanding_with_price,
            "oi_falling_on_breakout": derivatives.oi_falling_on_breakout,
            "crowded_long_risk": derivatives.crowded_long_risk,
            "crowded_short_risk": derivatives.crowded_short_risk,
            "top_trader_long_crowded": derivatives.top_trader_long_crowded,
            "top_trader_short_crowded": derivatives.top_trader_short_crowded,
            "entry_veto_reason_codes": list(derivatives.entry_veto_reason_codes),
            "breakout_veto_reason_codes": list(derivatives.breakout_veto_reason_codes),
            "long_discount_magnitude": derivatives.long_discount_magnitude,
            "short_discount_magnitude": derivatives.short_discount_magnitude,
            "long_alignment_score": derivatives.long_alignment_score,
            "short_alignment_score": derivatives.short_alignment_score,
        },
        "lead_lag": {
            "available": feature_payload.lead_lag.available,
            "leader_bias": feature_payload.lead_lag.leader_bias,
            "reference_symbols": list(feature_payload.lead_lag.reference_symbols),
            "bullish_alignment_score": feature_payload.lead_lag.bullish_alignment_score,
            "bearish_alignment_score": feature_payload.lead_lag.bearish_alignment_score,
            "bullish_breakout_confirmed": feature_payload.lead_lag.bullish_breakout_confirmed,
            "bearish_breakout_confirmed": feature_payload.lead_lag.bearish_breakout_confirmed,
            "bullish_breakout_ahead": feature_payload.lead_lag.bullish_breakout_ahead,
            "bearish_breakout_ahead": feature_payload.lead_lag.bearish_breakout_ahead,
            "bullish_pullback_supported": feature_payload.lead_lag.bullish_pullback_supported,
            "bearish_pullback_supported": feature_payload.lead_lag.bearish_pullback_supported,
            "bullish_continuation_supported": feature_payload.lead_lag.bullish_continuation_supported,
            "bearish_continuation_supported": feature_payload.lead_lag.bearish_continuation_supported,
        },
    }
    if volume_profile is not None:
        context["volume_profile"] = volume_profile.model_dump(mode="json")
    if isinstance(universe_breadth, dict) and universe_breadth:
        context["universe_breadth"] = dict(universe_breadth)
    return context


def _clamp_score(value: float, *, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _as_dict(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


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


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


_MARKET_SETTINGS_INCOMPLETE_DATA_FLAG_CODES = frozenset(
    {
        "INCOMPLETE_MARKET_DATA",
        "MARKET_DATA_INCOMPLETE",
        "MISSING_MARKET_DATA",
        "MISSING_CANDLES",
        "MISSING_CLOSE",
        "MISSING_PRICE",
    }
)


def _market_settings_has_incomplete_data_flags(flags: object) -> bool:
    if not isinstance(flags, (list, tuple, set)):
        return False
    for item in flags:
        code = str(item or "").strip().upper()
        if code in _MARKET_SETTINGS_INCOMPLETE_DATA_FLAG_CODES:
            return True
    return False


def _market_settings_volatility_spike_min_pct() -> float:
    raw_threshold = _safe_float(
        getattr(get_settings(), "ai_decision_volatility_spike_min_pct", 0.02),
        0.02,
    )
    if 0.0 < raw_threshold <= 0.1:
        return raw_threshold * 100.0
    return raw_threshold


def _strategy_engine_name_from_payload(
    metadata: dict[str, object],
    output_payload: dict[str, object],
) -> str:
    strategy_engine = _as_dict(metadata.get("strategy_engine"))
    selected_engine = _as_dict(strategy_engine.get("selected_engine"))
    engine_name = str(selected_engine.get("engine_name") or "").strip()
    if engine_name:
        return engine_name
    entry_mode = str(output_payload.get("entry_mode") or "").lower()
    decision = str(output_payload.get("decision") or "").lower()
    rationale_codes = {str(code) for code in output_payload.get("rationale_codes") or [] if code}
    if rationale_codes & {"PROTECTION_REQUIRED", "PROTECTION_RECOVERY", "PROTECTION_RESTORE"}:
        return "protection_reduce_engine"
    if entry_mode == "breakout_confirm":
        return "breakout_exception_engine"
    if any("CONTINUATION" in code for code in rationale_codes):
        return "trend_continuation_engine"
    if entry_mode == "pullback_confirm":
        return "trend_pullback_engine"
    if decision in {"reduce", "exit"}:
        return "protection_reduce_engine"
    return "unspecified_engine"


def _coerce_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _safe_str(value: object) -> str:
    if value in {None, ""}:
        return ""
    return str(value)


def _risk_mode_from_drawdown_payload(drawdown_state: dict[str, object]) -> str:
    state = str(drawdown_state.get("current_drawdown_state") or "normal").strip().lower()
    if state == "recovery":
        return "drawdown_recovery"
    return state or "normal"


def _entry_confirmation_type_for_tags(*, strategy_engine: str, entry_mode: str) -> str:
    if strategy_engine == "range_mean_reversion_engine":
        return "range_edge_confirm"
    if entry_mode in {"breakout_confirm", "pullback_confirm", "immediate", "none"}:
        return entry_mode
    return "none"


def _range_identity_from_feature_payload(
    *,
    symbol: str,
    timeframe: str,
    feature_payload: FeaturePayload | None,
    regime_id: str,
) -> dict[str, object]:
    if feature_payload is None:
        return {}
    range_low = float(feature_payload.breakout.range_low or 0.0)
    range_high = float(feature_payload.breakout.range_high or 0.0)
    payload: dict[str, object] = {
        "range_low": range_low if range_low > 0.0 else None,
        "range_high": range_high if range_high > 0.0 else None,
        "range_width_pct": feature_payload.breakout.range_width_pct,
        "range_breakout_direction": feature_payload.breakout.range_breakout_direction,
    }
    if range_low > 0.0 and range_high > range_low:
        payload["range_id"] = f"{symbol}:{timeframe}:{regime_id}:range:{range_low:.2f}-{range_high:.2f}"
    return {key: value for key, value in payload.items() if value not in {None, ""}}


def _decision_trade_performance_tags(
    decision: TradeDecision,
    metadata: dict[str, object],
    *,
    ai_context_payload: dict[str, object],
    drawdown_state: dict[str, object],
    feature_payload: FeaturePayload | None = None,
) -> dict[str, object]:
    output_payload = decision.model_dump(mode="json")
    selection_context = _as_dict(metadata.get("selection_context"))
    strategy_payload = _as_dict(metadata.get("strategy_engine"))
    selected_engine = _as_dict(strategy_payload.get("selected_engine"))
    metadata_strategy_engine = _safe_str(metadata.get("strategy_engine")) if isinstance(metadata.get("strategy_engine"), str) else ""
    strategy_engine = (
        _safe_str(_as_dict(metadata.get("trade_performance_tags")).get("strategy_id"))
        or _safe_str(selection_context.get("strategy_engine"))
        or _safe_str(ai_context_payload.get("strategy_engine"))
        or _safe_str(selected_engine.get("engine_name"))
        or metadata_strategy_engine
    )
    if not strategy_engine:
        strategy_engine = _strategy_engine_name_from_payload(metadata, output_payload)
    regime_summary = _as_dict(ai_context_payload.get("regime_summary")) or _as_dict(metadata.get("regime_summary"))
    composite_regime = _as_dict(ai_context_payload.get("composite_regime"))
    regime_label = (
        _safe_str(regime_summary.get("primary_regime"))
        or _safe_str(composite_regime.get("structure_regime"))
        or _safe_str(metadata.get("regime"))
        or "unknown"
    )
    direction_regime = (
        _safe_str(composite_regime.get("direction_regime"))
        or _safe_str(regime_summary.get("trend_alignment"))
        or "unknown"
    )
    regime_id = regime_label if direction_regime == "unknown" else f"{regime_label}:{direction_regime}"
    entry_mode = _safe_str(decision.entry_mode) or "none"
    return {
        "strategy_id": strategy_engine,
        "strategy_engine": strategy_engine,
        "regime_id": regime_id,
        "regime_label": regime_label,
        **_range_identity_from_feature_payload(
            symbol=decision.symbol,
            timeframe=decision.timeframe,
            feature_payload=feature_payload,
            regime_id=regime_id,
        ),
        "entry_confirmation_type": _entry_confirmation_type_for_tags(
            strategy_engine=strategy_engine,
            entry_mode=entry_mode,
        ),
        "risk_mode": _risk_mode_from_drawdown_payload(drawdown_state),
        "current_drawdown_state": drawdown_state.get("current_drawdown_state") or "normal",
        "drawdown_state": dict(drawdown_state),
        "regime_summary": regime_summary,
        "composite_regime": composite_regime,
        "symbol": decision.symbol,
        "timeframe": decision.timeframe,
        "decision": decision.decision,
        "entry_mode": entry_mode,
    }


def _decision_snapshot_hash(market_snapshot: MarketSnapshotPayload) -> str:
    payload = {
        "symbol": market_snapshot.symbol,
        "timeframe": market_snapshot.timeframe,
        "snapshot_time": market_snapshot.snapshot_time.isoformat(),
        "latest_price": market_snapshot.latest_price,
        "candle_count": market_snapshot.candle_count,
    }
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _ai_decision_validity_payload(
    *,
    decision: TradeDecision,
    generated_at: datetime,
    market_snapshot: MarketSnapshotPayload,
    market_snapshot_id: int | None,
    feature_payload: FeaturePayload,
    trade_performance_tags: dict[str, object],
) -> dict[str, object]:
    defaults = get_settings()
    ttl_seconds = max(int(getattr(defaults, "ai_decision_ttl_seconds", 900) or 900), 1)
    valid_until = generated_at + timedelta(seconds=ttl_seconds)
    breakout_payload = _as_dict(feature_payload.breakout.model_dump(mode="json"))
    return {
        "status": "valid",
        "generated_at": generated_at.isoformat(),
        "valid_until": valid_until.isoformat(),
        "ttl_seconds": ttl_seconds,
        "symbol": decision.symbol,
        "timeframe": decision.timeframe,
        "market_snapshot_id": market_snapshot_id,
        "snapshot_hash": _decision_snapshot_hash(market_snapshot),
        "snapshot_time": market_snapshot.snapshot_time.isoformat(),
        "reference_price": market_snapshot.latest_price,
        "regime_id": trade_performance_tags.get("regime_id"),
        "regime_label": trade_performance_tags.get("regime_label"),
        "volatility_pct": feature_payload.volatility_pct,
        "range_breakout_direction": breakout_payload.get("range_breakout_direction"),
        "price_move_invalidation_pct": float(
            getattr(defaults, "ai_decision_price_move_invalidation_pct", 0.004) or 0.004
        ),
    }


def _current_market_state_payload(
    *,
    feature_payload: FeaturePayload,
    ai_context_payload: dict[str, object],
) -> dict[str, object]:
    regime_summary = _as_dict(ai_context_payload.get("regime_summary"))
    composite_regime = _as_dict(ai_context_payload.get("composite_regime"))
    regime_label = (
        _safe_str(regime_summary.get("primary_regime"))
        or _safe_str(composite_regime.get("structure_regime"))
        or feature_payload.regime.primary_regime
        or "unknown"
    )
    direction_regime = (
        _safe_str(composite_regime.get("direction_regime"))
        or _safe_str(regime_summary.get("trend_alignment"))
        or feature_payload.regime.trend_alignment
        or "unknown"
    )
    regime_id = regime_label if direction_regime == "unknown" else f"{regime_label}:{direction_regime}"
    range_identity = _range_identity_from_feature_payload(
        symbol=feature_payload.symbol,
        timeframe=feature_payload.timeframe,
        feature_payload=feature_payload,
        regime_id=regime_id,
    )
    return {
        "regime_id": regime_id,
        "regime_label": regime_label,
        "volatility_pct": feature_payload.volatility_pct,
        "range_breakout_direction": feature_payload.breakout.range_breakout_direction,
        **range_identity,
    }


def _rolling_returns_from_snapshot(snapshot: MarketSnapshotPayload) -> list[float]:
    closes = [float(candle.close) for candle in snapshot.candles if candle.close > 0]
    if len(closes) < 3:
        return []
    returns: list[float] = []
    for previous, current in zip(closes, closes[1:], strict=False):
        if previous <= 0:
            continue
        returns.append((current - previous) / previous)
    return returns


def _pearson_correlation(left: list[float], right: list[float]) -> float:
    sample_size = min(len(left), len(right))
    if sample_size < 3:
        return 0.0
    lhs = left[-sample_size:]
    rhs = right[-sample_size:]
    lhs_mean = sum(lhs) / sample_size
    rhs_mean = sum(rhs) / sample_size
    covariance = sum((lhs_item - lhs_mean) * (rhs_item - rhs_mean) for lhs_item, rhs_item in zip(lhs, rhs, strict=False))
    lhs_variance = sum((item - lhs_mean) ** 2 for item in lhs)
    rhs_variance = sum((item - rhs_mean) ** 2 for item in rhs)
    denominator = sqrt(lhs_variance * rhs_variance)
    if denominator <= 0:
        return 0.0
    return covariance / denominator


class TradingOrchestrator:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.settings_row = get_or_create_settings(session)
        self.credentials = get_runtime_credentials(self.settings_row)
        self.event_context_provider: EventContextProvider = resolve_event_context_provider(
            settings_row=self.settings_row,
            credentials=self.credentials,
        )
        provider = build_model_provider(
            ai_provider=self.settings_row.ai_provider,
            ai_enabled=self.settings_row.ai_enabled,
            api_key=self.credentials.openai_api_key,
            model=self.settings_row.ai_model,
            temperature=self.settings_row.ai_temperature,
        )
        self.trading_agent = TradingDecisionAgent(provider)
        self.market_settings_advisor = MarketSettingsAdvisorAgent(provider)
        self.position_exit_review_agent = PositionExitReviewAgent(provider)
        self.chief_review_agent = ChiefReviewAgent()

    def build_keep_kill_report(
        self,
        *,
        lookback_days: int = 21,
        limit: int = 256,
    ):
        return build_keep_kill_report(
            self.session,
            lookback_days=lookback_days,
            limit=limit,
        )

    def build_skip_quality_report(
        self,
        *,
        lookback_days: int = 21,
        limit: int = 512,
    ):
        return build_skip_quality_report(
            self.session,
            lookback_days=lookback_days,
            limit=limit,
        )

    def build_capital_efficiency_report(
        self,
        *,
        lookback_days: int = 21,
        limit: int = 256,
    ):
        return build_capital_efficiency_report(
            self.session,
            lookback_days=lookback_days,
            limit=limit,
        )

    def build_strategy_engine_bucket_report(
        self,
        *,
        lookback_days: int = 21,
        limit: int = 256,
    ):
        return build_strategy_engine_bucket_report(
            self.session,
            lookback_days=lookback_days,
            limit=limit,
        )

    def _sync_drawdown_state(
        self,
        *,
        now: datetime | None = None,
        record_transition_audit: bool = True,
    ) -> dict[str, object]:
        observed_at = now or utcnow_naive()
        previous_detail = get_drawdown_state_detail(self.settings_row)
        previous_state = str(previous_detail.get("current_drawdown_state") or "normal")
        snapshot = build_drawdown_state_snapshot(
            self.session,
            self.settings_row,
            current_detail=previous_detail,
            now=observed_at,
        )
        if snapshot != previous_detail:
            set_drawdown_state_detail(self.settings_row, snapshot)
            self.session.add(self.settings_row)
            self.session.flush()
        current_state = str(snapshot.get("current_drawdown_state") or "normal")
        if record_transition_audit and previous_state != current_state:
            record_audit_event(
                self.session,
                event_type="drawdown_state_transition",
                entity_type="settings",
                entity_id=str(self.settings_row.id),
                severity="warning" if current_state in {DRAWDOWN_STATE_CAUTION, DRAWDOWN_STATE_CONTAINMENT} else "info",
                message="Account drawdown operating state updated.",
                payload={
                    "previous_drawdown_state": previous_state,
                    "current_drawdown_state": current_state,
                    "entered_at": snapshot.get("entered_at"),
                    "transition_reason": snapshot.get("transition_reason"),
                    "policy_adjustments": dict(snapshot.get("policy_adjustments") or {}),
                    "drawdown_depth_pct": snapshot.get("drawdown_depth_pct"),
                    "recent_net_pnl": snapshot.get("recent_net_pnl"),
                    "recent_net_pnl_pct": snapshot.get("recent_net_pnl_pct"),
                    "consecutive_losses": snapshot.get("consecutive_losses"),
                    "recovery_progress": snapshot.get("recovery_progress"),
                },
            )
        return snapshot

    def _market_settings_advisor_defaults(self) -> dict[str, object]:
        policy = get_execution_risk_profile_policy(self.settings_row, defaults=get_settings())
        return {
            "enabled": bool(policy["advisor_enabled"]),
            "shadow": bool(policy["advisor_shadow_mode"]),
            "normal_interval_seconds": int(policy["normal_interval_seconds"]),
            "elevated_interval_seconds": int(policy["elevated_interval_seconds"]),
            "min_recheck_interval_seconds": int(policy["min_recheck_interval_seconds"]),
            "recommendation_ttl_seconds": int(policy["recommendation_ttl_seconds"]),
            "min_confidence_to_apply": float(policy["min_confidence_to_apply"]),
        }

    def _latest_market_settings_advisor_run(self, *, symbol: str | None = None) -> AgentRun | None:
        rows = list(
            self.session.scalars(
                select(AgentRun)
                .where(AgentRun.role == AgentRole.MARKET_SETTINGS_ADVISOR.value)
                .order_by(desc(AgentRun.created_at))
                .limit(100)
            )
        )
        if symbol is None:
            return rows[0] if rows else None
        symbol_key = symbol.upper()
        for row in rows:
            metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
            output_payload = row.output_payload if isinstance(row.output_payload, dict) else {}
            symbol_scope = metadata.get("symbol_scope") or output_payload.get("symbol_scope") or []
            if isinstance(symbol_scope, list) and symbol_key in {str(item).upper() for item in symbol_scope}:
                return row
        return None

    @staticmethod
    def _market_settings_current_state(
        *,
        market_snapshot: MarketSnapshotPayload,
        feature_payload: FeaturePayload,
    ) -> dict[str, object]:
        return {
            "symbol": market_snapshot.symbol.upper(),
            "timeframe": market_snapshot.timeframe,
            "primary_regime": feature_payload.regime.primary_regime,
            "trend_alignment": feature_payload.regime.trend_alignment,
            "volatility_regime": feature_payload.regime.volatility_regime,
            "range_breakout_direction": feature_payload.breakout.range_breakout_direction,
            "spread_stress": feature_payload.derivatives.spread_stress,
            "snapshot_time": market_snapshot.snapshot_time.isoformat(),
        }

    @staticmethod
    def _market_settings_observed_risk_flags(
        *,
        market_snapshot: MarketSnapshotPayload,
        feature_payload: FeaturePayload,
        runtime_state: dict[str, object],
        previous_state: dict[str, object] | None,
    ) -> list[str]:
        flags: list[str] = []

        def add(code: str) -> None:
            if code not in flags:
                flags.append(code)

        if market_snapshot.is_stale or bool(getattr(feature_payload.event_context, "is_stale", False)):
            add("STALE_MARKET_DATA")
        if not market_snapshot.is_complete or _market_settings_has_incomplete_data_flags(
            feature_payload.data_quality_flags
        ):
            add("INCOMPLETE_MARKET_DATA")
        if (
            feature_payload.regime.volatility_regime == "expanded"
            or feature_payload.volatility_pct >= _market_settings_volatility_spike_min_pct()
        ):
            add("HIGH_VOLATILITY")
        if feature_payload.regime.volume_regime == "weak" or feature_payload.volume_ratio < 0.4:
            add("THIN_LIQUIDITY")
        if feature_payload.derivatives.spread_stress or feature_payload.derivatives.spread_headwind:
            add("SPREAD_STRESS")
        if feature_payload.breakout.range_breakout_direction in {"up", "down"}:
            add("RANGE_BREAK")
        if previous_state:
            previous_regime = str(previous_state.get("primary_regime") or "")
            if previous_regime and previous_regime != feature_payload.regime.primary_regime:
                add("REGIME_CHANGE")
        operating_state = str(runtime_state.get("operating_state") or "")
        if operating_state and operating_state not in {"TRADABLE", "tradable"}:
            add("SYNC_UNTRUSTED")
        if bool(runtime_state.get("protection_recovery_active")) or market_snapshot.symbol.upper() in {
            str(item).upper() for item in runtime_state.get("missing_protection_symbols", []) or []
        }:
            add("PROTECTIVE_ORDER_UNCERTAIN")
        return flags

    @staticmethod
    def _market_settings_symbol_context_from_payload(
        *,
        symbol: str,
        timeframe: str,
        source: str,
        feature_time: datetime,
        feature_payload: dict[str, object],
        market_payload: dict[str, object],
        now: datetime,
    ) -> dict[str, object]:
        regime = _as_dict(feature_payload.get("regime"))
        breakout = _as_dict(feature_payload.get("breakout"))
        derivatives = _as_dict(feature_payload.get("derivatives"))
        event_context = _as_dict(feature_payload.get("event_context"))
        flags = [
            str(item)
            for item in feature_payload.get("data_quality_flags", [])
            if str(item or "").strip()
        ] if isinstance(feature_payload.get("data_quality_flags"), list) else []
        feature_time_naive = feature_time.replace(tzinfo=None) if feature_time.tzinfo is not None else feature_time
        data_age_seconds = max(int((now - feature_time_naive).total_seconds()), 0)
        is_stale = bool(market_payload.get("is_stale", False)) or bool(event_context.get("is_stale", False))
        is_complete = bool(market_payload.get("is_complete", True)) and not _market_settings_has_incomplete_data_flags(
            flags
        )
        spread_stress = bool(derivatives.get("spread_stress", False))
        spread_headwind = bool(derivatives.get("spread_headwind", False))
        volatility_pct = _safe_float(feature_payload.get("volatility_pct"))
        volume_ratio = _safe_float(feature_payload.get("volume_ratio"), 1.0)
        range_breakout_direction = str(breakout.get("range_breakout_direction") or "none")
        risk_flags: list[str] = []
        if is_stale:
            risk_flags.append("STALE_MARKET_DATA")
        if not is_complete:
            risk_flags.append("INCOMPLETE_MARKET_DATA")
        if (
            str(regime.get("volatility_regime") or "") == "expanded"
            or volatility_pct >= _market_settings_volatility_spike_min_pct()
        ):
            risk_flags.append("HIGH_VOLATILITY")
        if str(regime.get("volume_regime") or "") == "weak" or volume_ratio < 0.4:
            risk_flags.append("THIN_LIQUIDITY")
        if spread_stress or spread_headwind:
            risk_flags.append("SPREAD_STRESS")
        if range_breakout_direction in {"up", "down"}:
            risk_flags.append("RANGE_BREAK")

        return {
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "source": source,
            "feature_time": feature_time.isoformat(),
            "data_age_seconds": data_age_seconds,
            "latest_price": market_payload.get("latest_price"),
            "data_quality": {
                "is_stale": is_stale,
                "is_complete": is_complete,
                "candle_count": market_payload.get("candle_count"),
                "flags": flags,
            },
            "regime": {
                "primary_regime": regime.get("primary_regime"),
                "trend_alignment": regime.get("trend_alignment"),
                "volatility_regime": regime.get("volatility_regime"),
                "volume_regime": regime.get("volume_regime"),
                "momentum_weakening": bool(regime.get("momentum_weakening", False)),
            },
            "market_metrics": {
                "trend_score": _safe_float(feature_payload.get("trend_score")),
                "momentum_score": _safe_float(feature_payload.get("momentum_score")),
                "volatility_pct": volatility_pct,
                "atr_pct": feature_payload.get("atr_pct"),
                "volume_ratio": volume_ratio,
                "rsi": feature_payload.get("rsi"),
                "range_breakout_direction": range_breakout_direction,
            },
            "liquidity_and_derivatives": {
                "available": bool(derivatives.get("available", False)),
                "spread_bps": derivatives.get("spread_bps"),
                "spread_stress": spread_stress,
                "spread_headwind": spread_headwind,
                "funding_bias": derivatives.get("funding_bias"),
                "taker_flow_alignment": derivatives.get("taker_flow_alignment"),
                "entry_veto_reason_codes": list(derivatives.get("entry_veto_reason_codes", []))
                if isinstance(derivatives.get("entry_veto_reason_codes"), list)
                else [],
                "breakout_veto_reason_codes": list(derivatives.get("breakout_veto_reason_codes", []))
                if isinstance(derivatives.get("breakout_veto_reason_codes"), list)
                else [],
            },
            "event_context": {
                "active_risk_window": bool(event_context.get("active_risk_window", False)),
                "next_event_name": event_context.get("next_event_name"),
                "minutes_to_next_event": event_context.get("minutes_to_next_event"),
                "severity": event_context.get("severity"),
            },
            "risk_flags": risk_flags,
        }

    def _market_settings_universe_context(
        self,
        *,
        symbol_scope: list[str],
        timeframe: str,
        market_snapshot: MarketSnapshotPayload,
        feature_payload: FeaturePayload,
    ) -> dict[str, object]:
        now = utcnow_naive()
        symbol_order = list(dict.fromkeys(item.upper() for item in symbol_scope if item))
        contexts: dict[str, dict[str, object]] = {
            market_snapshot.symbol.upper(): self._market_settings_symbol_context_from_payload(
                symbol=market_snapshot.symbol,
                timeframe=market_snapshot.timeframe,
                source="current_cycle",
                feature_time=market_snapshot.snapshot_time,
                feature_payload=feature_payload.model_dump(mode="json"),
                market_payload={
                    "latest_price": market_snapshot.latest_price,
                    "candle_count": market_snapshot.candle_count,
                    "is_stale": market_snapshot.is_stale,
                    "is_complete": market_snapshot.is_complete,
                },
                now=now,
            )
        }
        rows = list(
            self.session.scalars(
                select(FeatureSnapshot)
                .where(FeatureSnapshot.symbol.in_(symbol_order), FeatureSnapshot.timeframe == timeframe)
                .order_by(desc(FeatureSnapshot.feature_time))
                .limit(max(len(symbol_order) * 4, 20))
            )
        )
        market_ids = [row.market_snapshot_id for row in rows if row.market_snapshot_id is not None]
        market_rows = {
            row.id: row
            for row in self.session.scalars(select(MarketSnapshot).where(MarketSnapshot.id.in_(market_ids)))
        } if market_ids else {}
        for row in rows:
            symbol_key = row.symbol.upper()
            if symbol_key in contexts:
                continue
            market_row = market_rows.get(row.market_snapshot_id)
            contexts[symbol_key] = self._market_settings_symbol_context_from_payload(
                symbol=row.symbol,
                timeframe=row.timeframe,
                source="stored_latest",
                feature_time=row.feature_time,
                feature_payload=row.payload if isinstance(row.payload, dict) else {},
                market_payload={
                    "latest_price": market_row.latest_price if market_row is not None else None,
                    "candle_count": market_row.candle_count if market_row is not None else None,
                    "is_stale": bool(market_row.is_stale) if market_row is not None else False,
                    "is_complete": bool(market_row.is_complete) if market_row is not None else True,
                },
                now=now,
            )
            if len(contexts) >= len(symbol_order):
                break

        ordered_contexts = [contexts[symbol] for symbol in symbol_order if symbol in contexts]
        missing_symbols = [symbol for symbol in symbol_order if symbol not in contexts]
        breadth_items = []
        for item in ordered_contexts:
            regime = _as_dict(item.get("regime"))
            metrics = _as_dict(item.get("market_metrics"))
            breadth_items.append(
                {
                    "symbol": item.get("symbol"),
                    "primary_regime": regime.get("primary_regime"),
                    "trend_alignment": regime.get("trend_alignment"),
                    "weak_volume": _safe_float(metrics.get("volume_ratio"), 1.0) < 0.4
                    or str(regime.get("volume_regime") or "") == "weak",
                    "momentum_weakening": bool(regime.get("momentum_weakening", False)),
                }
            )
        breadth = summarize_universe_breadth(breadth_items)
        risk_flag_counts: dict[str, int] = defaultdict(int)
        stressed_symbols: list[str] = []
        for item in ordered_contexts:
            risk_flags = [
                str(flag)
                for flag in item.get("risk_flags", [])
                if str(flag or "").strip()
            ] if isinstance(item.get("risk_flags"), list) else []
            if risk_flags:
                stressed_symbols.append(str(item.get("symbol")))
            for flag in risk_flags:
                risk_flag_counts[flag] += 1
        return {
            "context_version": "market_settings_advisor_universe_v1",
            "market_breadth": {
                **breadth,
                "tracked_symbols": len(symbol_order),
                "context_symbols": len(ordered_contexts),
                "missing_symbols": missing_symbols,
                "stressed_symbols": stressed_symbols,
                "risk_flag_counts": dict(sorted(risk_flag_counts.items())),
            },
            "symbols": ordered_contexts,
        }

    @staticmethod
    def _market_settings_universe_risk_flags(universe_context: dict[str, object]) -> list[str]:
        breadth = _as_dict(universe_context.get("market_breadth"))
        counts = _as_dict(breadth.get("risk_flag_counts"))
        flags = [str(flag) for flag, count in counts.items() if _safe_float(count) > 0]
        if breadth.get("missing_symbols"):
            flags.append("MARKET_CONTEXT_PARTIAL")
        breadth_regime = str(breadth.get("breadth_regime") or "")
        if breadth_regime in {"weak_breadth", "transition_fragile"}:
            flags.append("MARKET_BREADTH_WEAK")
        return list(dict.fromkeys(flag for flag in flags if flag))

    @staticmethod
    def _market_settings_advisor_fingerprint_material(
        *,
        symbol_scope: list[str],
        market_state: dict[str, object],
        universe_context: dict[str, object],
        observed_risk_flags: list[str],
        runtime_state: dict[str, object],
    ) -> dict[str, object]:
        breadth = _as_dict(universe_context.get("market_breadth"))
        return {
            "symbol_scope": [str(symbol).upper() for symbol in symbol_scope],
            "operating_state": str(runtime_state.get("operating_state") or ""),
            "observed_risk_flags": sorted(dict.fromkeys(observed_risk_flags)),
            "market_state": {
                "primary_regime": market_state.get("primary_regime"),
                "trend_alignment": market_state.get("trend_alignment"),
                "volatility_regime": market_state.get("volatility_regime"),
                "range_breakout_direction": market_state.get("range_breakout_direction"),
                "spread_stress": market_state.get("spread_stress"),
            },
            "market_breadth": {
                "breadth_regime": breadth.get("breadth_regime"),
                "directional_bias": breadth.get("directional_bias"),
                "missing_symbols": sorted(str(symbol) for symbol in breadth.get("missing_symbols", []) or []),
                "stressed_symbols": sorted(str(symbol) for symbol in breadth.get("stressed_symbols", []) or []),
                "risk_flag_counts": _as_dict(breadth.get("risk_flag_counts")),
            },
        }

    @staticmethod
    def _market_settings_advisor_fingerprint(material: dict[str, object]) -> str:
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _record_market_settings_provider_skip(
        self,
        *,
        skip_reason: str,
        gate: dict[str, object] | None,
        observed_risk_flags: list[str],
        advisor_fingerprint: str,
        fingerprint_changed: bool,
        symbol_scope: list[str],
        cycle_id: str,
        snapshot_id: int,
        retry_after_seconds: int = 0,
    ) -> None:
        payload = {
            "status": "skipped",
            "skip_reason": skip_reason,
            "gate": gate,
            "observed_risk_flags": observed_risk_flags,
            "advisor_fingerprint": advisor_fingerprint,
            "fingerprint_changed": fingerprint_changed,
            "symbol_scope": symbol_scope,
            "retry_after_seconds": retry_after_seconds,
            "risk_guard_unchanged": True,
        }
        record_audit_event(
            self.session,
            event_type="ai_market_settings_provider_skipped",
            entity_type="settings",
            entity_id=str(self.settings_row.id),
            severity="warning" if gate is not None else "info",
            message="AI market settings advisor provider call skipped before invocation.",
            payload=payload,
            correlation_ids=normalize_correlation_ids(cycle_id=cycle_id, snapshot_id=snapshot_id),
        )
        self.session.flush()

    @staticmethod
    def _market_settings_advisor_status(
        *,
        recommendation: AIMarketSettingsRecommendation,
        now: datetime,
        min_confidence_to_apply: float,
    ) -> tuple[str, list[str]]:
        ignored_reason_codes: list[str] = []
        if recommendation.valid_until <= now:
            ignored_reason_codes.append("AI_MARKET_SETTINGS_RECOMMENDATION_EXPIRED")
        if recommendation.confidence < min_confidence_to_apply:
            ignored_reason_codes.append("AI_MARKET_SETTINGS_LOW_CONFIDENCE")
        if ignored_reason_codes:
            return "ignored", ignored_reason_codes
        return "shadow_generated", []

    @staticmethod
    def _market_settings_advisor_trading_context(
        advisor_result: dict[str, object] | None,
        *,
        include_shadow_policy: bool = False,
    ) -> dict[str, object]:
        if not isinstance(advisor_result, dict) or not advisor_result:
            return {}
        recommendation = _as_dict(advisor_result.get("recommendation"))
        status = str(advisor_result.get("status") or "").strip().lower()
        raw_shadow = advisor_result.get("shadow")
        shadow = bool(raw_shadow) if raw_shadow is not None else status == "shadow_generated"
        context: dict[str, object] = {
            "status": advisor_result.get("status"),
            "provider": advisor_result.get("provider"),
            "agent_run_id": advisor_result.get("agent_run_id"),
            "shadow": shadow,
            "risk_guard_unchanged": bool(advisor_result.get("risk_guard_unchanged", True)),
            "observed_risk_flags": list(advisor_result.get("observed_risk_flags") or []),
        }
        if advisor_result.get("skip_reason"):
            context["skip_reason"] = advisor_result.get("skip_reason")
        if advisor_result.get("retry_after_seconds") is not None:
            context["retry_after_seconds"] = advisor_result.get("retry_after_seconds")
        for key in (
            "reuse_age_seconds",
            "reuse_ttl_seconds",
            "reused_until",
            "advisor_fingerprint",
            "fingerprint_changed",
        ):
            if advisor_result.get(key) is not None:
                context[key] = advisor_result.get(key)
        if recommendation:
            context["recommendation"] = {
                "recommended_profile_id": recommendation.get("recommended_profile_id"),
                "confidence": recommendation.get("confidence"),
                "do_not_relax": recommendation.get("do_not_relax"),
                "reason_codes": list(recommendation.get("reason_codes") or []),
                "valid_until": recommendation.get("valid_until"),
            }
            if include_shadow_policy or not shadow:
                context["recommendation"]["suggested_new_entry_policy"] = recommendation.get(
                    "suggested_new_entry_policy"
                )
        return {
            key: value
            for key, value in context.items()
            if value is not None and value != "" and value != []
        }

    @classmethod
    def _risk_context_with_market_settings_advisor(
        cls,
        risk_context: dict[str, object],
        advisor_result: dict[str, object] | None,
    ) -> tuple[dict[str, object], dict[str, object]]:
        advisor_context = cls._market_settings_advisor_trading_context(advisor_result)
        if not advisor_context:
            return risk_context, {}
        status = str(advisor_context.get("status") or "").strip().lower()
        if status in {"skipped", "ignored"}:
            return risk_context, advisor_context
        if not _as_dict(advisor_context.get("recommendation")):
            return risk_context, advisor_context
        if bool(advisor_context.get("shadow", False)):
            return risk_context, advisor_context
        execution_constraints = dict(risk_context.get("execution_constraints_summary") or {})
        execution_constraints["execution_risk_profile_advisor"] = advisor_context
        return {
            **risk_context,
            "market_settings_advisor": advisor_context,
            "execution_risk_profile_advisor": advisor_context,
            "execution_constraints_summary": execution_constraints,
        }, advisor_context

    @staticmethod
    def _risk_context_with_advisor_prompt_context(
        risk_context: dict[str, object],
        advisor_context: dict[str, object],
    ) -> dict[str, object]:
        status = str(advisor_context.get("status") or "").strip().lower()
        if status in {"skipped", "ignored"} or not _as_dict(advisor_context.get("recommendation")):
            return risk_context
        execution_constraints = dict(risk_context.get("execution_constraints_summary") or {})
        execution_constraints["execution_risk_profile_advisor"] = advisor_context
        return {
            **risk_context,
            "market_settings_advisor": advisor_context,
            "execution_risk_profile_advisor": advisor_context,
            "execution_constraints_summary": execution_constraints,
        }

    @staticmethod
    def _advisor_datetime_as_utc(value: object) -> datetime | None:
        parsed = _coerce_datetime(value)
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    @classmethod
    def _cached_market_settings_advisor_result(
        cls,
        *,
        latest_run: AgentRun | None,
        defaults: dict[str, object],
        advisor_fingerprint: str,
        previous_fingerprint: str,
        observed_risk_flags: list[str],
        now_naive: datetime,
    ) -> dict[str, object] | None:
        if latest_run is None or previous_fingerprint != advisor_fingerprint:
            return None
        recommendation = _as_dict(latest_run.output_payload)
        if not recommendation or not recommendation.get("recommended_profile_id"):
            return None
        metadata = _as_dict(latest_run.metadata_json)
        if str(metadata.get("status") or latest_run.status or "").lower() == "ignored":
            return None
        created_at = latest_run.created_at
        if created_at is None:
            return None
        if created_at.tzinfo is not None:
            created_at = created_at.astimezone(UTC).replace(tzinfo=None)
        ttl_seconds = max(int(defaults["recommendation_ttl_seconds"]), 1)
        elapsed_seconds = max(int((now_naive - created_at).total_seconds()), 0)
        if elapsed_seconds > ttl_seconds:
            return None
        now_aware = now_naive.replace(tzinfo=UTC)
        valid_until = cls._advisor_datetime_as_utc(recommendation.get("valid_until"))
        if valid_until is not None and valid_until <= now_aware:
            return None
        reused_until = valid_until or (created_at.replace(tzinfo=UTC) + timedelta(seconds=ttl_seconds))
        return {
            "status": "reused",
            "skip_reason": "advisor_recommendation_reused",
            "agent_run_id": latest_run.id,
            "provider": latest_run.provider_name,
            "schema_valid": bool(latest_run.schema_valid),
            "recommendation": recommendation,
            "ignored_reason_codes": list(metadata.get("ignored_reason_codes") or []),
            "observed_risk_flags": observed_risk_flags,
            "shadow": bool(metadata.get("shadow", True)),
            "risk_guard_unchanged": True,
            "advisor_fingerprint": advisor_fingerprint,
            "fingerprint_changed": False,
            "reuse_age_seconds": elapsed_seconds,
            "reuse_ttl_seconds": ttl_seconds,
            "reused_until": reused_until.isoformat(),
        }

    @staticmethod
    def _should_run_market_settings_before_trading_ai(
        *,
        use_ai: bool,
        review_trigger_payload: AIReviewTriggerPayload | None,
    ) -> bool:
        if not use_ai or review_trigger_payload is None:
            return False
        return review_trigger_payload.trigger_reason != "protection_review_event"

    def _write_market_settings_advisor_state(
        self,
        *,
        status: str,
        payload: dict[str, object],
    ) -> None:
        detail = dict(self.settings_row.pause_reason_detail or {})
        detail["ai_market_settings_advisor"] = {
            "status": status,
            "shadow": bool(payload.get("shadow", True)),
            "updated_at": utcnow_naive().isoformat(),
            "latest": payload,
        }
        self.settings_row.pause_reason_detail = detail
        self.session.add(self.settings_row)
        self.session.flush()

    def _maybe_run_market_settings_advisor(
        self,
        *,
        symbol: str,
        trigger_event: str,
        market_snapshot: MarketSnapshotPayload,
        feature_payload: FeaturePayload,
        runtime_state: dict[str, object],
        cycle_id: str,
        snapshot_id: int,
        force: bool = False,
        reuse_cached: bool = False,
    ) -> dict[str, object] | None:
        defaults = self._market_settings_advisor_defaults()
        if not bool(defaults["enabled"]):
            return None

        latest_run = self._latest_market_settings_advisor_run()
        previous_metadata = latest_run.metadata_json if latest_run is not None and isinstance(latest_run.metadata_json, dict) else {}
        previous_state = _as_dict(previous_metadata.get("market_state"))
        symbol_scope = list(
            dict.fromkeys(
                effective.symbol
                for effective in get_effective_symbol_schedule(self.settings_row)
                if effective.enabled
            )
        ) or [symbol.upper()]
        universe_context = self._market_settings_universe_context(
            symbol_scope=symbol_scope,
            timeframe=market_snapshot.timeframe,
            market_snapshot=market_snapshot,
            feature_payload=feature_payload,
        )
        observed_risk_flags = self._market_settings_observed_risk_flags(
            market_snapshot=market_snapshot,
            feature_payload=feature_payload,
            runtime_state=runtime_state,
            previous_state=previous_state,
        )
        observed_risk_flags = list(
            dict.fromkeys(
                [
                    *observed_risk_flags,
                    *self._market_settings_universe_risk_flags(universe_context),
                ]
            )
        )
        market_state = self._market_settings_current_state(
            market_snapshot=market_snapshot,
            feature_payload=feature_payload,
        )
        fingerprint_material = self._market_settings_advisor_fingerprint_material(
            symbol_scope=symbol_scope,
            market_state=market_state,
            universe_context=universe_context,
            observed_risk_flags=observed_risk_flags,
            runtime_state=runtime_state,
        )
        advisor_fingerprint = self._market_settings_advisor_fingerprint(fingerprint_material)
        previous_fingerprint = str(previous_metadata.get("advisor_fingerprint") or "")
        elevated = bool(observed_risk_flags)
        interval_seconds = int(
            defaults["elevated_interval_seconds"] if elevated else defaults["normal_interval_seconds"]
        )
        min_recheck_seconds = int(defaults["min_recheck_interval_seconds"])
        now_naive = utcnow_naive()
        if latest_run is not None and reuse_cached and not force:
            cached_result = self._cached_market_settings_advisor_result(
                latest_run=latest_run,
                defaults=defaults,
                advisor_fingerprint=advisor_fingerprint,
                previous_fingerprint=previous_fingerprint,
                observed_risk_flags=observed_risk_flags,
                now_naive=now_naive,
            )
            if cached_result is not None:
                return cached_result
        if latest_run is not None and not force:
            elapsed_seconds = max(int((now_naive - latest_run.created_at).total_seconds()), 0)
            if elapsed_seconds < min_recheck_seconds:
                return {
                    "status": "skipped",
                    "skip_reason": "min_recheck_interval_active",
                    "retry_after_seconds": min_recheck_seconds - elapsed_seconds,
                    "observed_risk_flags": observed_risk_flags,
                }
            if elapsed_seconds < interval_seconds:
                return {
                    "status": "skipped",
                    "skip_reason": "advisor_interval_not_due",
                    "retry_after_seconds": interval_seconds - elapsed_seconds,
                    "observed_risk_flags": observed_risk_flags,
                }
            if previous_fingerprint == advisor_fingerprint:
                self._record_market_settings_provider_skip(
                    skip_reason="advisor_fingerprint_unchanged",
                    gate=None,
                    observed_risk_flags=observed_risk_flags,
                    advisor_fingerprint=advisor_fingerprint,
                    fingerprint_changed=False,
                    symbol_scope=symbol_scope,
                    cycle_id=cycle_id,
                    snapshot_id=snapshot_id,
                )
                return {
                    "status": "skipped",
                    "skip_reason": "advisor_fingerprint_unchanged",
                    "advisor_fingerprint": advisor_fingerprint,
                    "fingerprint_changed": False,
                    "observed_risk_flags": observed_risk_flags,
                    "risk_guard_unchanged": True,
                }

        gate = get_openai_call_gate(
            self.session,
            self.settings_row,
            AgentRole.MARKET_SETTINGS_ADVISOR.value,
            trigger_event,
            has_openai_key=bool(self.credentials.openai_api_key),
        )
        if not gate.allowed:
            gate_metadata = gate.as_metadata()
            self._record_market_settings_provider_skip(
                skip_reason=gate.reason,
                gate=gate_metadata,
                observed_risk_flags=observed_risk_flags,
                advisor_fingerprint=advisor_fingerprint,
                fingerprint_changed=previous_fingerprint != advisor_fingerprint,
                symbol_scope=symbol_scope,
                cycle_id=cycle_id,
                snapshot_id=snapshot_id,
                retry_after_seconds=gate.retry_after_seconds,
            )
            return {
                "status": "skipped",
                "skip_reason": gate.reason,
                "gate": gate_metadata,
                "advisor_fingerprint": advisor_fingerprint,
                "fingerprint_changed": previous_fingerprint != advisor_fingerprint,
                "observed_risk_flags": observed_risk_flags,
                "risk_guard_unchanged": True,
            }

        settings_policy = {
            **defaults,
            "symbol_scope": symbol_scope,
            "allowed_profiles": [
                "NORMAL",
                "CAUTION",
                "HIGH_VOLATILITY",
                "THIN_LIQUIDITY",
                "STRESS",
                "DEGRADED",
            ],
            "allowed_new_entry_policies": [
                "NORMAL_ALLOWED",
                "PULLBACK_ONLY",
                "STRICT_CONFIRMATION_ONLY",
                "NO_NEW_ENTRY",
            ],
        }
        input_payload = build_market_settings_advisor_input_payload(
            market_snapshot=market_snapshot,
            features=feature_payload,
            runtime_state=runtime_state,
            settings_policy=settings_policy,
            observed_risk_flags=observed_risk_flags,
            previous_recommendation=(
                latest_run.output_payload
                if latest_run is not None and isinstance(latest_run.output_payload, dict)
                else None
            ),
            universe_context=universe_context,
        )
        recommendation, provider_name, metadata = self.market_settings_advisor.run(
            market_snapshot=market_snapshot,
            features=feature_payload,
            runtime_state=runtime_state,
            settings_policy=settings_policy,
            observed_risk_flags=observed_risk_flags,
            previous_recommendation=input_payload.get("previous_recommendation")
            if isinstance(input_payload.get("previous_recommendation"), dict)
            else None,
            universe_context=universe_context,
            use_ai=True,
        )
        now_aware = datetime.now(UTC)
        status = "ignored"
        ignored_reason_codes: list[str] = []
        output_payload: dict[str, object]
        schema_valid = recommendation is not None
        if recommendation is None:
            ignored_reason_codes = [
                str(metadata.get("ignored_reason_code") or "AI_MARKET_SETTINGS_RECOMMENDATION_INVALID")
            ]
            output_payload = _as_dict(metadata.get("raw_output")) or {
                "status": "ignored",
                "reason_codes": ignored_reason_codes,
            }
        else:
            status, ignored_reason_codes = self._market_settings_advisor_status(
                recommendation=recommendation,
                now=now_aware,
                min_confidence_to_apply=float(defaults["min_confidence_to_apply"]),
            )
            output_payload = recommendation.model_dump(mode="json")

        metadata = {
            **metadata,
            "status": status,
            "ignored_reason_codes": ignored_reason_codes,
            "symbol": symbol.upper(),
            "representative_symbol": symbol.upper(),
            "symbol_scope": symbol_scope,
            "observed_risk_flags": observed_risk_flags,
            "settings_policy": settings_policy,
            "market_state": market_state,
            "market_breadth": universe_context.get("market_breadth"),
            "advisor_fingerprint": advisor_fingerprint,
            "advisor_fingerprint_material": fingerprint_material,
            "fingerprint_changed": previous_fingerprint != advisor_fingerprint,
            "gate": gate.as_metadata(),
            "cycle_id": cycle_id,
            "snapshot_id": snapshot_id,
            "source": "llm_ignored" if status == "ignored" else metadata.get("source", "llm"),
        }
        metadata.setdefault("model", self.settings_row.ai_model)
        metadata.setdefault("ai_model", self.settings_row.ai_model)
        usage_payload = metadata.get("usage") if isinstance(metadata.get("usage"), dict) else None
        if usage_payload is not None:
            estimated_cost_usd = estimate_ai_usage_cost_usd(
                model=str(metadata.get("ai_model") or self.settings_row.ai_model),
                usage=usage_payload,
            )
            metadata["estimated_cost_usd"] = estimated_cost_usd
            metadata["cost_estimate_status"] = (
                "unknown_model_rate" if estimated_cost_usd is None else "estimated"
            )
        else:
            metadata["cost_estimate_status"] = "missing_usage"
        advisor_run = persist_agent_run(
            self.session,
            AgentRole.MARKET_SETTINGS_ADVISOR,
            trigger_event,
            input_payload,
            output_payload,
            provider_name=provider_name,
            metadata_json=metadata,
            schema_valid=schema_valid,
            status="ignored" if status == "ignored" else "completed",
        )
        audit_payload: dict[str, object] = {
            **output_payload,
            "status": status,
            "provider": provider_name,
            "agent_run_id": advisor_run.id,
            "shadow": bool(defaults["shadow"]),
            "would_apply": bool(status != "ignored"),
            "min_confidence_to_apply": defaults["min_confidence_to_apply"],
            "observed_risk_flags": observed_risk_flags,
            "advisor_fingerprint": advisor_fingerprint,
            "fingerprint_changed": previous_fingerprint != advisor_fingerprint,
        }
        if ignored_reason_codes:
            audit_payload["ignored_reason_codes"] = ignored_reason_codes
            audit_payload["ignored_reason_code"] = ignored_reason_codes[0]
            audit_payload["ignored_reason"] = ignored_reason_codes[0]
        self._write_market_settings_advisor_state(status=status, payload=audit_payload)
        record_audit_event(
            self.session,
            event_type=(
                "ai_market_settings_recommendation_ignored"
                if status == "ignored"
                else "ai_market_settings_recommendation_generated"
            ),
            entity_type="agent_run",
            entity_id=str(advisor_run.id),
            severity="warning" if status == "ignored" else "info",
            message=(
                "AI market settings recommendation ignored."
                if status == "ignored"
                else "AI market settings recommendation generated in shadow mode."
            ),
            payload=audit_payload,
            correlation_ids=normalize_correlation_ids(cycle_id=cycle_id, snapshot_id=snapshot_id),
        )
        self.session.flush()
        return {
            "status": status,
            "agent_run_id": advisor_run.id,
            "provider": provider_name,
            "schema_valid": schema_valid,
            "recommendation": output_payload,
            "ignored_reason_codes": ignored_reason_codes,
            "observed_risk_flags": observed_risk_flags,
            "shadow": bool(defaults["shadow"]),
            "risk_guard_unchanged": True,
        }

    @staticmethod
    def _should_execute_live(trigger_event: str) -> bool:
        return trigger_event in {
            TriggerEvent.MANUAL.value,
            TriggerEvent.REALTIME.value,
            TriggerEvent.SCHEDULED.value,
            "test",
        }

    @staticmethod
    def _should_poll_exchange_state(trigger_event: str) -> bool:
        return trigger_event in {
            TriggerEvent.MANUAL.value,
            TriggerEvent.REALTIME.value,
            TriggerEvent.SCHEDULED.value,
            "test",
            "background_poll",
        }

    @staticmethod
    def _build_cycle_id(*, trigger_event: str, symbol: str, snapshot_id: int) -> str:
        return f"{trigger_event}:{symbol.upper()}:{snapshot_id}:{uuid4().hex[:8]}"

    @staticmethod
    def _compact_funnel_payload(payload: dict[str, object]) -> dict[str, object]:
        return {
            key: value
            for key, value in payload.items()
            if value is not None and value != "" and value != [] and value != {}
        }

    @staticmethod
    def _funnel_reason_codes(*values: object) -> list[str]:
        reason_codes: list[str] = []
        for value in values:
            items = value if isinstance(value, (list, tuple, set)) else [value]
            for item in items:
                code = str(item or "").strip()
                if code and code not in reason_codes:
                    reason_codes.append(code)
        return reason_codes

    @staticmethod
    def _first_funnel_reason(reason_codes: list[str]) -> str | None:
        for code in reason_codes:
            normalized = str(code or "").strip()
            if normalized:
                return normalized
        return None

    @classmethod
    def _strategy_candidate_funnel_payload(
        cls,
        selection_context: dict[str, object] | None,
    ) -> dict[str, object] | None:
        selection = _as_dict(selection_context)
        candidate = _as_dict(selection.get("candidate"))
        score = _as_dict(candidate.get("score")) or _as_dict(selection.get("score"))
        candidate_id = candidate.get("candidate_id") or selection.get("candidate_id")
        payload = {
            "candidate_id": candidate_id,
            "symbol": candidate.get("symbol") or selection.get("symbol"),
            "decision": candidate.get("decision") or selection.get("decision_hint"),
            "scenario": candidate.get("scenario"),
            "strategy_engine": (
                candidate.get("strategy_engine")
                or selection.get("strategy_engine")
                or _as_dict(selection.get("strategy_engine_context")).get("strategy_engine")
            ),
            "entry_mode": candidate.get("entry_mode") or selection.get("entry_mode"),
            "selection_reason": selection.get("selection_reason") or selection.get("rejected_reason"),
            "assigned_slot": selection.get("assigned_slot") or _as_dict(selection.get("slot_allocation")).get("assigned_slot"),
            "candidate_weight": cls._summary_float(selection.get("candidate_weight")),
            "rationale_codes": cls._funnel_reason_codes(candidate.get("rationale_codes")),
            "score_total": cls._summary_float(score.get("total_score")),
        }
        compact = cls._compact_funnel_payload(payload)
        return compact or None

    @classmethod
    def _decision_hold_reason_codes(
        cls,
        decision: TradeDecision | None,
        *,
        risk_result: RiskCheckResult | None = None,
        decision_metadata: dict[str, object] | None = None,
        ai_skipped_reason: str | None = None,
    ) -> list[str]:
        metadata = _as_dict(decision_metadata)
        reason_codes = cls._funnel_reason_codes(
            getattr(decision, "no_trade_reason_codes", []) if decision is not None else [],
            getattr(decision, "abstain_reason_codes", []) if decision is not None else [],
            getattr(decision, "primary_reason_codes", []) if decision is not None else [],
            getattr(decision, "rationale_codes", []) if decision is not None else [],
            getattr(decision, "invalidation_reason_codes", []) if decision is not None else [],
            getattr(decision, "fallback_reason_codes", []) if decision is not None else [],
            getattr(decision, "data_quality_block_reason_codes", []) if decision is not None else [],
            metadata.get("fallback_reason_codes"),
            metadata.get("data_quality_block_reason_codes"),
            metadata.get("abstain_reason_codes"),
            metadata.get("prior_reason_codes"),
            ai_skipped_reason,
            getattr(risk_result, "blocked_reason_codes", []) if risk_result is not None else [],
            getattr(risk_result, "reason_codes", []) if risk_result is not None else [],
        )
        if "HOLD_DECISION" not in reason_codes:
            reason_codes.append("HOLD_DECISION")
        return reason_codes or ["HOLD_DECISION"]

    @classmethod
    def _ai_decision_funnel_payload(
        cls,
        decision: TradeDecision | None,
        *,
        decision_run_id: int | None = None,
        provider_name: str | None = None,
        decision_metadata: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        if decision is None:
            return None
        metadata = _as_dict(decision_metadata)
        payload = {
            "decision_run_id": decision_run_id,
            "provider": provider_name,
            "source": metadata.get("source"),
            "decision": decision.decision,
            "confidence": cls._summary_float(decision.confidence),
            "intent_family": getattr(decision, "intent_family", None),
            "management_action": getattr(decision, "management_action", None),
            "holding_profile": getattr(decision, "holding_profile", None),
            "entry_mode": getattr(decision, "entry_mode", None),
            "primary_reason_codes": cls._funnel_reason_codes(getattr(decision, "primary_reason_codes", [])),
            "no_trade_reason_codes": cls._funnel_reason_codes(getattr(decision, "no_trade_reason_codes", [])),
            "abstain_reason_codes": cls._funnel_reason_codes(getattr(decision, "abstain_reason_codes", [])),
            "explanation_short": getattr(decision, "explanation_short", None),
        }
        return cls._compact_funnel_payload(payload)

    @classmethod
    def _risk_decision_funnel_payload(
        cls,
        risk_result: RiskCheckResult | None,
        *,
        risk_row_id: int | None = None,
    ) -> dict[str, object] | None:
        if risk_result is None:
            return None
        blocked_reason_codes = cls._funnel_reason_codes(
            getattr(risk_result, "blocked_reason_codes", []),
            getattr(risk_result, "reason_codes", []),
        )
        payload = {
            "risk_check_id": risk_row_id,
            "allowed": risk_result.allowed,
            "decision": risk_result.decision,
            "blocked_reason": getattr(risk_result, "blocked_reason", None)
            or cls._first_funnel_reason(blocked_reason_codes),
            "blocked_reason_codes": blocked_reason_codes,
            "approved_risk_pct": cls._summary_float(risk_result.approved_risk_pct),
            "approved_leverage": cls._summary_float(risk_result.approved_leverage),
            "operating_mode": risk_result.operating_mode,
            "operating_state": risk_result.operating_state,
            "execution_risk_profile": _as_dict(risk_result.debug_payload).get("safe_profile_selector"),
        }
        return cls._compact_funnel_payload(payload)

    @staticmethod
    def _pending_plan_funnel_candidate(plan: PendingEntryPlan) -> dict[str, object]:
        metadata = _as_dict(getattr(plan, "metadata_json", None))
        trade_performance_tags = _as_dict(metadata.get("trade_performance_tags"))
        return {
            "plan_id": plan.id,
            "symbol": plan.symbol,
            "decision": plan.side,
            "strategy_id": trade_performance_tags.get("strategy_id") or metadata.get("strategy_id"),
            "regime_id": trade_performance_tags.get("regime_id") or metadata.get("regime_id") or plan.regime,
            "scenario": plan.posture,
            "entry_mode": plan.entry_mode,
            "source_decision_run_id": plan.source_decision_run_id,
            "source_risk_check_id": metadata.get("source_risk_check_id"),
            "rationale_codes": list(plan.rationale_codes or []),
        }

    @staticmethod
    def _plan_tracking_tags(plan: PendingEntryPlan) -> dict[str, object]:
        metadata = _as_dict(getattr(plan, "metadata_json", None))
        trade_performance_tags = _as_dict(metadata.get("trade_performance_tags"))
        strategy_id = _safe_str(trade_performance_tags.get("strategy_id")) or _safe_str(metadata.get("strategy_id"))
        if strategy_id in {"unspecified", "unspecified_engine"}:
            strategy_id = ""
        if not strategy_id:
            strategy_id = "unspecified_engine"
        regime_id = (
            _safe_str(trade_performance_tags.get("regime_id"))
            or _safe_str(metadata.get("regime_id"))
            or _safe_str(plan.regime)
            or "unknown"
        )
        return {
            "strategy_id": strategy_id,
            "regime_id": regime_id,
            "entry_confirmation_type": (
                _safe_str(trade_performance_tags.get("entry_confirmation_type"))
                or _safe_str(metadata.get("entry_confirmation_type"))
                or _safe_str(plan.entry_mode)
                or "none"
            ),
        }

    @classmethod
    def _confirmation_failed_reason(
        cls,
        confirm_detail: dict[str, object],
        blocked_reason_codes: list[str],
    ) -> str | None:
        if bool(confirm_detail.get("confirm_met")):
            return None
        reason = _safe_str(confirm_detail.get("reason"))
        if reason == "ZONE_NOT_ENTERED":
            return "ZONE_NOT_ENTERED"
        if (
            bool(confirm_detail.get("rr_collapse"))
            or bool(confirm_detail.get("late_chase_rr_failure"))
            or reason in {"QUALITY_REJECTED_RR_DETERIORATED", "QUALITY_REJECTED_LATE_CHASE"}
        ):
            return "RR_DETERIORATED"
        if "PLAN_MAX_CHASE_EXCEEDED" in blocked_reason_codes:
            return "CHASE_LIMIT_EXCEEDED"
        if "PLAN_LATE_CHASE_WAITING_REENTRY" in blocked_reason_codes:
            return "LATE_CHASE_WAITING_REENTRY"
        if reason == "NO_1M_CANDLE":
            return "NO_1M_CANDLE"
        if bool(confirm_detail.get("zone_entered")) and (
            not bool(confirm_detail.get("close_reclaimed"))
            or not bool(confirm_detail.get("structure_break"))
        ):
            return "STRUCTURE_CONFIRMATION_FAILED"
        return reason or "QUALITY_BELOW_THRESHOLD"

    @classmethod
    def _plan_confirmation_tracking_payload(
        cls,
        *,
        plan: PendingEntryPlan,
        confirm_detail: dict[str, object],
        trigger_details: dict[str, object],
        market_snapshot: MarketSnapshotPayload,
        market_snapshot_id: int | None,
        plan_status: str,
        blocked_reason_codes: list[str] | None = None,
        plan_cancel_reason: str | None = None,
    ) -> dict[str, object]:
        blocked_codes = [
            str(code)
            for code in blocked_reason_codes or []
            if str(code or "").strip()
        ]
        failed_reason = cls._confirmation_failed_reason(confirm_detail, blocked_codes)
        tracking_tags = cls._plan_tracking_tags(plan)
        follow_up_snapshot: dict[str, object] = {}
        if failed_reason is not None or plan_cancel_reason is not None:
            watch_until = market_snapshot.snapshot_time + timedelta(minutes=15)
            follow_up_snapshot = {
                "status": "pending_observation",
                "horizon_minutes": 15,
                "reference_snapshot_id": market_snapshot_id,
                "reference_time": market_snapshot.snapshot_time.isoformat(),
                "reference_price": market_snapshot.latest_price,
                "watch_until": watch_until.isoformat(),
            }
        return {
            "plan_id": plan.id,
            "symbol": plan.symbol,
            "side": plan.side,
            "strategy_id": tracking_tags["strategy_id"],
            "regime_id": tracking_tags["regime_id"],
            "entry_confirmation_type": tracking_tags["entry_confirmation_type"],
            "source_timeframe": plan.source_timeframe,
            "watch_timeframe": ENTRY_PLAN_WATCH_TIMEFRAME,
            "entry_zone_low": min(plan.entry_zone_min, plan.entry_zone_max),
            "entry_zone_high": max(plan.entry_zone_min, plan.entry_zone_max),
            "zone_touched": bool(confirm_detail.get("zone_entered")),
            "m1_close_reclaim": bool(confirm_detail.get("close_reclaimed")),
            "structure_break": bool(confirm_detail.get("structure_break")),
            "wick_body_condition": {
                "wick_reclaim": bool(confirm_detail.get("wick_reclaim")),
                "candle_body_ratio": confirm_detail.get("candle_body_ratio"),
                "wick_imbalance": confirm_detail.get("wick_imbalance"),
                "lower_wick_ratio": confirm_detail.get("lower_wick_ratio"),
                "upper_wick_ratio": confirm_detail.get("upper_wick_ratio"),
                "candle_body_quality": _as_dict(confirm_detail.get("quality_components")).get("candle_body_quality"),
                "wick_imbalance_quality": _as_dict(confirm_detail.get("quality_components")).get(
                    "wick_imbalance_quality"
                ),
            },
            "chase_distance_bps": trigger_details.get("observed_chase_bps"),
            "RR_before_confirmation": confirm_detail.get("baseline_expected_rr"),
            "RR_after_confirmation": confirm_detail.get("current_expected_rr"),
            "confirmation_passed": bool(confirm_detail.get("confirm_met")),
            "confirmation_failed_reason": failed_reason,
            "plan_cancel_reason": plan_cancel_reason,
            "blocked_reason_codes": blocked_codes,
            "plan_status": plan_status,
            "quality_score": confirm_detail.get("quality_score"),
            "quality_threshold": confirm_detail.get("quality_threshold"),
            "quality_state": confirm_detail.get("quality_state"),
            "quality_reason": confirm_detail.get("reason"),
            "market_snapshot_id": market_snapshot_id,
            "market_snapshot_time": market_snapshot.snapshot_time.isoformat(),
            "latest_price": market_snapshot.latest_price,
            "follow_up_snapshot": follow_up_snapshot,
        }

    def _record_plan_confirmation_tracking(
        self,
        *,
        plan: PendingEntryPlan,
        confirm_detail: dict[str, object],
        trigger_details: dict[str, object],
        market_snapshot: MarketSnapshotPayload,
        market_snapshot_id: int | None,
        watch_cycle_id: str,
        plan_status: str,
        blocked_reason_codes: list[str] | None = None,
        plan_cancel_reason: str | None = None,
        severity: str = "info",
    ) -> dict[str, object]:
        payload = self._plan_confirmation_tracking_payload(
            plan=plan,
            confirm_detail=confirm_detail,
            trigger_details=trigger_details,
            market_snapshot=market_snapshot,
            market_snapshot_id=market_snapshot_id,
            plan_status=plan_status,
            blocked_reason_codes=blocked_reason_codes,
            plan_cancel_reason=plan_cancel_reason,
        )
        metadata = self._pending_entry_plan_metadata(plan)
        metadata["last_confirmation_tracking"] = payload
        if payload.get("follow_up_snapshot"):
            metadata["confirmation_follow_up_snapshot"] = payload["follow_up_snapshot"]
        raw_history = metadata.get("confirmation_tracking_history")
        history_source = raw_history if isinstance(raw_history, list) else []
        history = [item for item in history_source if isinstance(item, dict)]
        history.append(payload)
        metadata["confirmation_tracking_history"] = history[-20:]
        plan.metadata_json = metadata
        self.session.add(plan)
        record_audit_event(
            self.session,
            event_type="pending_entry_plan_confirmation_evaluated",
            entity_type="pending_entry_plan",
            entity_id=str(plan.id),
            severity=severity,
            message="Pending entry plan 1m confirmation was evaluated.",
            payload=payload,
            correlation_ids=normalize_correlation_ids(
                cycle_id=watch_cycle_id,
                snapshot_id=market_snapshot_id,
                decision_id=plan.source_decision_run_id,
            ),
        )
        self.session.flush()
        refresh_decision_performance_fact_usefulness(self.session, plan.source_decision_run_id)
        return payload

    @classmethod
    def _execution_order_status(cls, execution_result: dict[str, object] | None) -> str:
        if not execution_result:
            return "not_submitted"
        return str(
            execution_result.get("order_status")
            or execution_result.get("exchange_status")
            or execution_result.get("status")
            or "unknown"
        )

    def _record_decision_funnel_audit(
        self,
        *,
        cycle_id: str,
        symbol: str,
        stage: str,
        status: str,
        timeframe: str | None = None,
        regime: str | None = None,
        strategy_candidate: dict[str, object] | None = None,
        decision: TradeDecision | None = None,
        decision_run_id: int | None = None,
        provider_name: str | None = None,
        decision_metadata: dict[str, object] | None = None,
        risk_result: RiskCheckResult | None = None,
        risk_row_id: int | None = None,
        entry_plan_status: str | None = None,
        m1_confirmation_status: str | None = None,
        final_risk_status: str | None = None,
        execution_result: dict[str, object] | None = None,
        blocked_reason_codes: list[str] | None = None,
        hold_reason_codes: list[str] | None = None,
        detail: dict[str, object] | None = None,
        severity: str = "info",
        entity_type: str | None = None,
        entity_id: str | None = None,
        correlation_ids: dict[str, object] | None = None,
        ai_skipped_reason: str | None = None,
    ):
        risk_payload = self._risk_decision_funnel_payload(risk_result, risk_row_id=risk_row_id)
        normalized_blocked_reason_codes = self._funnel_reason_codes(
            blocked_reason_codes,
            _as_dict(risk_payload).get("blocked_reason_codes"),
            (execution_result or {}).get("reason_codes") if execution_result else [],
        )
        normalized_hold_reason_codes = self._funnel_reason_codes(hold_reason_codes)
        if decision is not None and decision.decision == "hold":
            normalized_hold_reason_codes = self._decision_hold_reason_codes(
                decision,
                risk_result=risk_result,
                decision_metadata=decision_metadata,
                ai_skipped_reason=ai_skipped_reason,
            )
        return record_decision_funnel_event(
            self.session,
            cycle_id=cycle_id,
            symbol=symbol,
            timeframe=timeframe,
            regime=regime,
            stage=stage,
            status=status,
            strategy_candidate=strategy_candidate,
            ai_decision=self._ai_decision_funnel_payload(
                decision,
                decision_run_id=decision_run_id,
                provider_name=provider_name,
                decision_metadata=decision_metadata,
            ),
            risk_decision=risk_payload,
            entry_plan_status=entry_plan_status,
            m1_confirmation_status=m1_confirmation_status,
            final_risk_status=final_risk_status,
            order_status=self._execution_order_status(execution_result),
            blocked_reason=self._first_funnel_reason(normalized_blocked_reason_codes),
            blocked_reason_codes=normalized_blocked_reason_codes,
            hold_reason=self._first_funnel_reason(normalized_hold_reason_codes),
            hold_reason_codes=normalized_hold_reason_codes,
            detail=detail,
            severity=severity,
            entity_type=entity_type or ("decision_run" if decision_run_id is not None else "decision_cycle"),
            entity_id=entity_id or (str(decision_run_id) if decision_run_id is not None else symbol),
            correlation_ids=correlation_ids,
        )

    def _event_context_cycle_active(self) -> bool:
        cycle_active = getattr(self.event_context_provider, "event_context_cycle_active", None)
        return bool(cycle_active()) if callable(cycle_active) else False

    def _begin_event_context_cycle(self, cycle_key: object) -> bool:
        if self._event_context_cycle_active():
            return False
        begin_cycle = getattr(self.event_context_provider, "begin_event_context_cycle", None)
        if not callable(begin_cycle):
            return False
        begin_cycle(cycle_key)
        return True

    def _end_event_context_cycle(self, owns_cycle: bool) -> None:
        if not owns_cycle:
            return
        end_cycle = getattr(self.event_context_provider, "end_event_context_cycle", None)
        if callable(end_cycle):
            end_cycle()

    def _refresh_runtime_state_before_risk(self) -> None:
        if self.settings_row.id is None:
            return
        if self.session.is_modified(self.settings_row, include_collections=True):
            self.session.flush()
        # AI review can outlive exchange sync cadence; risk gates must use the latest sync state.
        self.session.refresh(self.settings_row, attribute_names=["pause_reason_detail"])

    def _effective_symbol_settings(self, symbol: str):
        return get_effective_symbol_settings(self.settings_row, symbol.upper())

    def _latest_decision_snapshot_time(self, symbol: str, timeframe: str) -> str | None:
        rows = list(
            self.session.scalars(
                select(AgentRun)
                .where(AgentRun.role == AgentRole.TRADING_DECISION.value)
                .order_by(desc(AgentRun.created_at))
                .limit(100)
            )
        )
        symbol_upper = symbol.upper()
        for row in rows:
            input_payload = row.input_payload if isinstance(row.input_payload, dict) else {}
            market_snapshot = input_payload.get("market_snapshot")
            if not isinstance(market_snapshot, dict):
                continue
            if str(market_snapshot.get("symbol", "")).upper() != symbol_upper:
                continue
            if str(market_snapshot.get("timeframe", "")) != timeframe:
                continue
            snapshot_time = market_snapshot.get("snapshot_time")
            if isinstance(snapshot_time, str) and snapshot_time:
                return snapshot_time
        return None

    @staticmethod
    def _agent_run_matches_symbol(
        row: AgentRun,
        *,
        symbol: str,
        timeframe: str | None = None,
    ) -> bool:
        symbol_upper = symbol.upper()
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        input_payload = row.input_payload if isinstance(row.input_payload, dict) else {}
        output_payload = row.output_payload if isinstance(row.output_payload, dict) else {}
        market_snapshot = input_payload.get("market_snapshot")
        candidates = [
            (
                str(metadata.get("symbol") or "").upper(),
                str(metadata.get("timeframe") or ""),
            ),
            (
                str(output_payload.get("symbol") or "").upper(),
                str(output_payload.get("timeframe") or ""),
            ),
            (
                str(market_snapshot.get("symbol") or "").upper(),
                str(market_snapshot.get("timeframe") or ""),
            )
            if isinstance(market_snapshot, dict)
            else ("", ""),
        ]
        for candidate_symbol, candidate_timeframe in candidates:
            if candidate_symbol != symbol_upper:
                continue
            if timeframe is None or not timeframe or candidate_timeframe in {"", timeframe}:
                return True
        return False

    def _latest_symbol_decision_run(
        self,
        *,
        symbol: str,
        timeframe: str | None = None,
    ) -> AgentRun | None:
        rows = list(
            self.session.scalars(
                select(AgentRun)
                .where(AgentRun.role == AgentRole.TRADING_DECISION.value)
                .order_by(desc(AgentRun.created_at))
                .limit(100)
            )
        )
        for row in rows:
            if self._agent_run_matches_symbol(row, symbol=symbol, timeframe=timeframe):
                return row
        return None

    def _latest_symbol_ai_invoked_at(
        self,
        *,
        symbol: str,
        timeframe: str | None = None,
    ) -> datetime | None:
        rows = list(
            self.session.scalars(
                select(AgentRun)
                .where(AgentRun.role == AgentRole.TRADING_DECISION.value)
                .order_by(desc(AgentRun.created_at))
                .limit(100)
            )
        )
        for row in rows:
            if not self._agent_run_matches_symbol(row, symbol=symbol, timeframe=timeframe):
                continue
            metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
            if row.provider_name == "openai" or str(metadata.get("source") or "") == "llm":
                return row.created_at
        return None

    @staticmethod
    def _selection_ranking_lookup(candidate_selection: dict[str, object]) -> dict[str, dict[str, object]]:
        return {
            str(item.get("symbol") or "").upper(): dict(item)
            for item in candidate_selection.get("rankings", [])
            if isinstance(item, dict) and item.get("symbol")
        }

    def _close_idle_transaction_before_candidate_selection_write(self) -> None:
        if not self.session.in_transaction():
            return
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()

    def _selection_context_from_candidate_selection(
        self,
        *,
        symbol: str,
        candidate_selection: dict[str, object],
    ) -> dict[str, object]:
        ranking_lookup = self._selection_ranking_lookup(candidate_selection)
        ranking_payload = ranking_lookup.get(symbol.upper(), {})
        breadth_summary = (
            dict(candidate_selection.get("breadth_summary") or {})
            if isinstance(candidate_selection.get("breadth_summary"), dict)
            else {}
        )
        candidate_payload = _as_dict(ranking_payload.get("candidate"))
        slot_allocation = {
            "assigned_slot": ranking_payload.get("assigned_slot"),
            "slot_label": ranking_payload.get("slot_label"),
            "candidate_weight": ranking_payload.get("candidate_weight"),
            "portfolio_weight": ranking_payload.get("portfolio_weight"),
            "slot_conviction_score": ranking_payload.get("slot_conviction_score"),
            "meta_gate_probability": ranking_payload.get("meta_gate_probability"),
            "agreement_alignment_score": ranking_payload.get("agreement_alignment_score"),
            "agreement_level_hint": ranking_payload.get("agreement_level_hint"),
            "execution_quality_score": ranking_payload.get("execution_quality_score"),
            "risk_pct_multiplier": ranking_payload.get("slot_risk_pct_multiplier"),
            "leverage_multiplier": ranking_payload.get("slot_leverage_multiplier"),
            "notional_multiplier": ranking_payload.get("slot_notional_multiplier"),
            "applies_soft_limit": ranking_payload.get("slot_applies_soft_cap"),
        }
        return {
            "universe_breadth": breadth_summary,
            "breadth_regime": candidate_selection.get("breadth_regime"),
            "capacity_reason": candidate_selection.get("capacity_reason"),
            "drawdown_capacity_reason": candidate_selection.get("drawdown_capacity_reason"),
            "drawdown_state": dict(candidate_selection.get("drawdown_state") or {}),
            "regime_summary": _as_dict(ranking_payload.get("regime_summary")),
            "portfolio_weight": ranking_payload.get("portfolio_weight"),
            "candidate_weight": ranking_payload.get("candidate_weight"),
            "holding_profile": ranking_payload.get("holding_profile") or candidate_payload.get("holding_profile"),
            "holding_profile_reason": ranking_payload.get("holding_profile_reason")
            or candidate_payload.get("holding_profile_reason"),
            "holding_profile_context": _as_dict(ranking_payload.get("holding_profile_context")),
            "strategy_engine": ranking_payload.get("strategy_engine") or candidate_payload.get("strategy_engine"),
            "strategy_engine_context": _as_dict(ranking_payload.get("strategy_engine_context")),
            "assigned_slot": ranking_payload.get("assigned_slot"),
            "slot_label": ranking_payload.get("slot_label"),
            "slot_reason": ranking_payload.get("slot_reason"),
            "slot_conviction_score": ranking_payload.get("slot_conviction_score"),
            "meta_gate_probability": ranking_payload.get("meta_gate_probability"),
            "agreement_alignment_score": ranking_payload.get("agreement_alignment_score"),
            "agreement_level_hint": ranking_payload.get("agreement_level_hint"),
            "execution_quality_score": ranking_payload.get("execution_quality_score"),
            "slot_risk_pct_multiplier": ranking_payload.get("slot_risk_pct_multiplier"),
            "slot_leverage_multiplier": ranking_payload.get("slot_leverage_multiplier"),
            "slot_notional_multiplier": ranking_payload.get("slot_notional_multiplier"),
            "slot_applies_soft_cap": ranking_payload.get("slot_applies_soft_cap"),
            "slot_allocation": slot_allocation,
            "entry_score_threshold": ranking_payload.get("entry_score_threshold"),
            "breadth_score_multiplier": ranking_payload.get("breadth_score_multiplier"),
            "breadth_score_adjustment": ranking_payload.get("breadth_score_adjustment"),
            "breadth_hold_bias": ranking_payload.get("breadth_hold_bias"),
            "breadth_adjustment_reasons": ranking_payload.get("breadth_adjustment_reasons"),
            "selection_reason": ranking_payload.get("selection_reason"),
            "selected_reason": ranking_payload.get("selected_reason"),
            "rejected_reason": ranking_payload.get("rejected_reason"),
            "selected": ranking_payload.get("selected"),
            "entry_mode": ranking_payload.get("entry_mode"),
            "candidate_entry_mode": ranking_payload.get("entry_mode"),
            "scenario": candidate_payload.get("scenario"),
            "expected_scenario": candidate_payload.get("scenario"),
            "candidate": candidate_payload,
            "score": _as_dict(ranking_payload.get("score")),
            "performance_summary": _as_dict(ranking_payload.get("performance_summary")),
            "reason_codes": list(candidate_payload.get("rationale_codes") or []),
        }

    def _default_selection_context_for_symbol(
        self,
        *,
        symbol: str,
        timeframe: str,
        upto_index: int | None,
        force_stale: bool,
    ) -> dict[str, object]:
        candidate_selection = self._rank_candidate_symbols(
            decision_symbols=[symbol.upper()],
            timeframe=timeframe,
            upto_index=upto_index,
            force_stale=force_stale,
        )
        return self._selection_context_from_candidate_selection(
            symbol=symbol,
            candidate_selection=candidate_selection,
        )

    def _latest_feature_snapshot_payload(self, *, symbol: str, timeframe: str) -> dict[str, object]:
        row = self.session.scalar(
            select(FeatureSnapshot)
            .where(
                FeatureSnapshot.symbol == symbol.upper(),
                FeatureSnapshot.timeframe == timeframe,
            )
            .order_by(desc(FeatureSnapshot.feature_time), desc(FeatureSnapshot.id))
            .limit(1)
        )
        if row is None or not isinstance(row.payload, dict):
            return {}
        return dict(row.payload)

    @staticmethod
    def _reference_price_from_zone(
        *,
        entry_zone_min: object,
        entry_zone_max: object,
        fallback_price: float,
    ) -> float:
        zone_min = _safe_float(entry_zone_min, default=0.0)
        zone_max = _safe_float(entry_zone_max, default=0.0)
        if zone_min > 0 and zone_max > 0:
            return (zone_min + zone_max) / 2.0
        if zone_max > 0:
            return zone_max
        if zone_min > 0:
            return zone_min
        return fallback_price

    @staticmethod
    def _selection_skip_reason(
        *,
        rejected_reason: str,
        capacity_reason: str,
    ) -> str | None:
        reason = rejected_reason.strip().lower()
        if not reason:
            return None
        if reason == "breadth_hold_bias":
            return "breadth_veto"
        if reason == "capacity_reached" and capacity_reason in {
            "breadth_weak_reduce_capacity",
            "transition_fragile_reduce_capacity",
        }:
            return "breadth_veto"
        if reason in {"underperforming_expectancy_bucket", "expectancy_below_threshold"}:
            return "expectancy_veto"
        if reason == "adverse_signed_slippage":
            return "slippage_veto"
        if reason in {"duplicate_scenario_exposure", "duplicate_directional_exposure", "correlation_limit"}:
            return "correlation_veto"
        if reason == "score_below_threshold":
            return "score_veto"
        if reason == "low_edge_hold_candidate":
            return "low_edge_candidate"
        return reason

    @staticmethod
    def _decision_skip_reason(
        *,
        decision: TradeDecision,
        decision_metadata: dict[str, object],
        ai_skipped_reason: str | None,
    ) -> str | None:
        rationale_codes = {str(code) for code in decision.rationale_codes if code}
        if "NO_TRADE_ZONE_RANGE_WEAK_VOLUME" in rationale_codes or ai_skipped_reason == "CADENCE_IDLE_NO_TRADE_ZONE":
            return "no_trade_zone"
        if "UNDERPERFORMING_SETUP_DISABLED" in rationale_codes:
            return "disable_bucket"
        if SETUP_CLUSTER_DISABLED_REASON_CODE in rationale_codes:
            return "setup_cluster_disable"
        if rationale_codes & {"DERIVATIVES_ALIGNMENT_HEADWIND", "SPREAD_HEADWIND", "BREAKOUT_OI_SPREAD_FILTER"}:
            return "derivatives_filter_veto"
        if rationale_codes & {"LEAD_MARKET_DIVERGENCE", "ALT_BREAKOUT_AHEAD_OF_LEADS"}:
            return "lead_lag_veto"
        if "NEGATIVE_EXPECTANCY_BUCKET" in rationale_codes:
            return "expectancy_veto"
        setup_cluster_state = _as_dict(decision_metadata.get("setup_cluster_state"))
        if bool(setup_cluster_state.get("active")):
            return "setup_cluster_disable"
        return None

    @staticmethod
    def _decision_expected_side(
        *,
        decision: TradeDecision,
        decision_metadata: dict[str, object],
    ) -> str | None:
        if decision.decision in {"long", "short"}:
            return decision.decision
        baseline = _as_dict(decision_metadata.get("deterministic_baseline"))
        baseline_decision = _safe_str(baseline.get("decision")).lower()
        if baseline_decision in {"long", "short"}:
            return baseline_decision
        agreement = _as_dict(decision_metadata.get("decision_agreement"))
        baseline_decision = _safe_str(agreement.get("baseline_decision")).lower()
        if baseline_decision in {"long", "short"}:
            return baseline_decision
        return None

    def _record_selection_skip_event(
        self,
        *,
        symbol: str,
        timeframe: str,
        item: dict[str, object],
        ranking_payload: dict[str, object],
    ) -> None:
        skip_reason = self._selection_skip_reason(
            rejected_reason=_safe_str(ranking_payload.get("rejected_reason")),
            capacity_reason=_safe_str(ranking_payload.get("capacity_reason")),
        )
        if skip_reason is None:
            return
        candidate = item.get("candidate")
        if candidate is None or str(getattr(candidate, "decision", "") or "") not in {"long", "short"}:
            return
        regime_summary = item.get("regime_summary") if isinstance(item.get("regime_summary"), dict) else {}
        performance_summary = _as_dict(item.get("performance_summary"))
        market_snapshot = item.get("market_snapshot")
        market_snapshot_id: int | None = None
        if isinstance(market_snapshot, MarketSnapshotPayload):
            market_snapshot_id = persist_market_snapshot(self.session, market_snapshot).id
        latest_price = _safe_float(getattr(market_snapshot, "latest_price", None))
        reference_price = self._reference_price_from_zone(
            entry_zone_min=getattr(candidate, "entry_zone_min", None),
            entry_zone_max=getattr(candidate, "entry_zone_max", None),
            fallback_price=latest_price,
        )
        record_skip_event(
            self.session,
            symbol=symbol,
            timeframe=timeframe,
            scenario=_safe_str(getattr(candidate, "scenario", "unspecified")) or "unspecified",
            regime=_safe_str(regime_summary.get("primary_regime")) or "unknown",
            trend_alignment=_safe_str(regime_summary.get("trend_alignment")) or "unknown",
            entry_mode=_safe_str(item.get("entry_mode")) or "none",
            skip_reason=skip_reason,
            skip_source="selection",
            market_snapshot_id=market_snapshot_id,
            decision_run_id=None,
            expected_side=_safe_str(getattr(candidate, "decision", None)).lower() or None,
            rejected_side=_safe_str(getattr(candidate, "decision", None)).lower() or None,
            reference_price=reference_price if reference_price > 0 else None,
            stop_loss=_safe_float(getattr(candidate, "stop_loss", None), default=0.0) or None,
            take_profit=_safe_float(getattr(candidate, "take_profit", None), default=0.0) or None,
            payload={
                "candidate_id": _safe_str(getattr(candidate, "candidate_id", "")),
                "selection_reason": ranking_payload.get("selection_reason"),
                "rejected_reason": ranking_payload.get("rejected_reason"),
                "selected_reason": ranking_payload.get("selected_reason"),
                "score": ranking_payload.get("score"),
                "performance_summary": performance_summary,
                "breadth_regime": ranking_payload.get("breadth_regime"),
                "capacity_reason": ranking_payload.get("capacity_reason"),
                "entry_score_threshold": ranking_payload.get("entry_score_threshold"),
                "breadth_adjustment_reasons": ranking_payload.get("breadth_adjustment_reasons"),
                "late_long_filter": ranking_payload.get("late_long_filter"),
                "snapshot_time": getattr(market_snapshot, "snapshot_time", None).isoformat()
                if getattr(market_snapshot, "snapshot_time", None) is not None
                else None,
            },
        )

    def _record_decision_skip_event(
        self,
        *,
        symbol: str,
        timeframe: str,
        market_row: MarketSnapshot,
        market_snapshot: MarketSnapshotPayload,
        decision_run: AgentRun,
        decision: TradeDecision,
        decision_metadata: dict[str, object],
        ai_skipped_reason: str | None,
        selection_context: dict[str, object] | None,
    ) -> None:
        skip_reason = self._decision_skip_reason(
            decision=decision,
            decision_metadata=decision_metadata,
            ai_skipped_reason=ai_skipped_reason,
        )
        if skip_reason is None:
            return
        expected_side = self._decision_expected_side(decision=decision, decision_metadata=decision_metadata)
        if expected_side not in {"long", "short"}:
            return
        baseline = _as_dict(decision_metadata.get("deterministic_baseline"))
        entry_mode = (
            _safe_str(decision.entry_mode)
            or _safe_str(baseline.get("entry_mode"))
            or _safe_str(_as_dict(selection_context).get("entry_mode"))
            or _safe_str(_as_dict(selection_context).get("candidate_entry_mode"))
            or "none"
        )
        scenario = (
            _safe_str(_as_dict(selection_context).get("expected_scenario"))
            or _safe_str(_as_dict(selection_context).get("scenario"))
            or _setup_cluster_scenario(expected_side, entry_mode, decision.rationale_codes)
        )
        reference_price = self._reference_price_from_zone(
            entry_zone_min=baseline.get("entry_zone_min") if baseline else decision.entry_zone_min,
            entry_zone_max=baseline.get("entry_zone_max") if baseline else decision.entry_zone_max,
            fallback_price=market_snapshot.latest_price,
        )
        analysis_context = _as_dict(decision_metadata.get("analysis_context"))
        regime_context = _as_dict(analysis_context.get("regime"))
        record_skip_event(
            self.session,
            symbol=symbol,
            timeframe=timeframe,
            scenario=scenario or "unspecified",
            regime=_safe_str(regime_context.get("primary_regime")) or "unknown",
            trend_alignment=_safe_str(regime_context.get("trend_alignment")) or "unknown",
            entry_mode=entry_mode or "none",
            skip_reason=skip_reason,
            skip_source="decision",
            market_snapshot_id=market_row.id,
            decision_run_id=decision_run.id,
            expected_side=expected_side,
            rejected_side=expected_side,
            reference_price=reference_price if reference_price > 0 else None,
            stop_loss=_safe_float(baseline.get("stop_loss") if baseline else decision.stop_loss, default=0.0) or None,
            take_profit=_safe_float(baseline.get("take_profit") if baseline else decision.take_profit, default=0.0)
            or None,
            payload={
                "decision_rationale_codes": list(decision.rationale_codes),
                "ai_skipped_reason": ai_skipped_reason,
                "selection_context": dict(selection_context) if isinstance(selection_context, dict) else {},
                "decision_agreement": decision_metadata.get("decision_agreement"),
                "setup_cluster_state": decision_metadata.get("setup_cluster_state"),
                "meta_gate": decision_metadata.get("meta_gate"),
            },
        )

    def _record_risk_skip_event(
        self,
        *,
        symbol: str,
        timeframe: str,
        market_row: MarketSnapshot,
        market_snapshot: MarketSnapshotPayload,
        decision_run: AgentRun,
        decision: TradeDecision,
        risk_row: RiskCheck,
        risk_result: RiskCheckResult,
        selection_context: dict[str, object] | None,
    ) -> None:
        if decision.decision not in {"long", "short"}:
            return
        reason_codes = {str(code) for code in risk_result.reason_codes if code}
        if any(code.startswith("META_GATE_") for code in reason_codes):
            skip_reason = "meta_gate_reject"
        elif "UNDERPERFORMING_SETUP_DISABLED" in reason_codes:
            skip_reason = "disable_bucket"
        elif SETUP_CLUSTER_DISABLED_REASON_CODE in reason_codes:
            skip_reason = "setup_cluster_disable"
        else:
            return
        analysis_context = _as_dict(decision_run.metadata_json).get("analysis_context")
        regime_context = _as_dict(_as_dict(analysis_context).get("regime"))
        reference_price = self._reference_price_from_zone(
            entry_zone_min=decision.entry_zone_min,
            entry_zone_max=decision.entry_zone_max,
            fallback_price=market_snapshot.latest_price,
        )
        record_skip_event(
            self.session,
            symbol=symbol,
            timeframe=timeframe,
            scenario=_setup_cluster_scenario(decision.decision, decision.entry_mode, decision.rationale_codes),
            regime=_safe_str(regime_context.get("primary_regime")) or "unknown",
            trend_alignment=_safe_str(regime_context.get("trend_alignment")) or "unknown",
            entry_mode=_safe_str(decision.entry_mode) or "none",
            skip_reason=skip_reason,
            skip_source="risk",
            market_snapshot_id=market_row.id,
            decision_run_id=decision_run.id,
            risk_check_id=risk_row.id,
            expected_side=decision.decision,
            rejected_side=decision.decision,
            reference_price=reference_price if reference_price > 0 else None,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
            payload={
                "reason_codes": list(risk_result.reason_codes),
                "blocked_reason_codes": list(risk_result.blocked_reason_codes),
                "selection_context": dict(selection_context) if isinstance(selection_context, dict) else {},
                "meta_gate": _as_dict(risk_result.debug_payload).get("meta_gate"),
            },
        )

    @staticmethod
    def _cadence_feature_flags(feature_payload: object) -> dict[str, object]:
        if hasattr(feature_payload, "model_dump"):
            payload = feature_payload.model_dump(mode="json")  # type: ignore[assignment]
        else:
            payload = _as_dict(feature_payload)
        regime = _as_dict(payload.get("regime"))
        primary_regime = str(regime.get("primary_regime") or "unknown")
        weak_volume = bool(regime.get("weak_volume", False))
        momentum_weakening = bool(regime.get("momentum_weakening", False))
        quiet_range_allowed = quiet_range_mean_reversion_allowed(payload)
        return {
            "primary_regime": primary_regime,
            "weak_volume": weak_volume,
            "momentum_weakening": momentum_weakening,
            "quiet_range_mean_reversion_allowed": quiet_range_allowed,
            "no_trade_zone": primary_regime == "range" and weak_volume and momentum_weakening and not quiet_range_allowed,
        }

    @staticmethod
    def _adaptive_bucket_underperformance(context: object) -> tuple[bool, list[str]]:
        if not isinstance(context, dict):
            return False, []
        windows = context.get("windows")
        if not isinstance(windows, dict):
            return False, []
        reasons: list[str] = []
        for label, payload in windows.items():
            if not isinstance(payload, dict):
                continue
            for dimension in ("symbol_timeframe", "symbol", "regime"):
                bucket = payload.get(dimension)
                if not isinstance(bucket, dict):
                    continue
                status = str(bucket.get("status") or "")
                try:
                    weight = float(bucket.get("weight", 1.0))
                except (TypeError, ValueError):
                    weight = 1.0
                if status == "active" and weight < 0.95:
                    reasons.append(f"{label}:{dimension}")
        return bool(reasons), reasons

    @staticmethod
    def _adaptive_setup_disable_idle_state(context: object) -> tuple[bool, list[str]]:
        if not isinstance(context, dict):
            return False, []
        buckets = context.get("setup_disable_buckets")
        if not isinstance(buckets, list):
            return False, []
        current_regime = str(context.get("regime") or "")
        active_buckets = [
            item
            for item in buckets
            if isinstance(item, dict)
            and bool(item.get("disabled"))
            and str(item.get("status") or "") == "active_disabled"
            and (not current_regime or str(item.get("regime") or "") == current_regime)
        ]
        return bool(active_buckets), (["SETUP_DISABLE_COOLDOWN_ACTIVE"] if active_buckets else [])

    @staticmethod
    def _setup_cluster_idle_state(context: object) -> tuple[bool, list[str]]:
        if not isinstance(context, dict):
            return False, []
        active_cluster_keys = context.get("active_cluster_keys")
        cluster_lookup = context.get("cluster_lookup")
        if not isinstance(active_cluster_keys, list) or not isinstance(cluster_lookup, dict):
            return False, []
        current_symbol = str(context.get("symbol") or "").upper()
        current_timeframe = str(context.get("timeframe") or "")
        current_regime = str(context.get("regime") or "")
        current_trend_alignment = str(context.get("trend_alignment") or "")
        active_clusters = [
            cluster_lookup.get(str(key))
            for key in active_cluster_keys
            if isinstance(cluster_lookup.get(str(key)), dict)
        ]
        matching_clusters = [
            item
            for item in active_clusters
            if str(item.get("symbol") or "").upper() == current_symbol
            and str(item.get("timeframe") or "") == current_timeframe
            and str(item.get("regime") or "") == current_regime
            and str(item.get("trend_alignment") or "") == current_trend_alignment
        ]
        return bool(matching_clusters), (["SETUP_CLUSTER_COOLDOWN_ACTIVE"] if matching_clusters else [])

    @staticmethod
    def _signed_slippage_bps(execution_row: Execution) -> float:
        payload = execution_row.payload if isinstance(execution_row.payload, dict) else {}
        if "signed_slippage_bps" in payload:
            return _safe_float(payload.get("signed_slippage_bps"))
        if "signed_slippage_pct" in payload:
            return _safe_float(payload.get("signed_slippage_pct")) * 10_000.0
        return 0.0

    def _build_setup_cluster_context(
        self,
        *,
        symbol: str,
        timeframe: str,
        regime: str,
        trend_alignment: str,
    ) -> dict[str, object]:
        symbol_key = symbol.upper()
        decision_rows = list(
            self.session.scalars(
                select(AgentRun)
                .where(AgentRun.role == "trading_decision")
                .order_by(desc(AgentRun.created_at))
                .limit(SETUP_CLUSTER_HISTORY_LIMIT)
            )
        )
        exact_rows = [
            row
            for row in decision_rows
            if isinstance(row.output_payload, dict)
            and str(row.output_payload.get("symbol") or "").upper() == symbol_key
            and str(row.output_payload.get("timeframe") or "") == timeframe
            and str(row.output_payload.get("decision") or "").lower() in {"long", "short"}
        ]
        if not exact_rows:
            return {
                "symbol": symbol_key,
                "timeframe": timeframe,
                "regime": regime,
                "trend_alignment": trend_alignment,
                "lookback": SETUP_CLUSTER_LOOKBACK,
                "cluster_lookup": {},
                "active_cluster_keys": [],
                "active_cluster_count": 0,
            }

        decision_ids = [row.id for row in exact_rows]
        risk_rows = list(
            self.session.scalars(
                select(RiskCheck)
                .where(RiskCheck.decision_run_id.in_(decision_ids))
                .order_by(desc(RiskCheck.created_at))
            )
        )
        risk_by_decision: dict[int, RiskCheck] = {}
        for row in risk_rows:
            if row.decision_run_id is not None and row.decision_run_id not in risk_by_decision:
                risk_by_decision[row.decision_run_id] = row

        orders = list(self.session.scalars(select(Order).where(Order.decision_run_id.in_(decision_ids))))
        orders_by_decision: dict[int, list[Order]] = defaultdict(list)
        for row in orders:
            if row.decision_run_id is not None:
                orders_by_decision[row.decision_run_id].append(row)
        order_ids = [row.id for row in orders]
        executions = (
            list(self.session.scalars(select(Execution).where(Execution.order_id.in_(order_ids))))
            if order_ids
            else []
        )
        executions_by_order: dict[int, list[Execution]] = defaultdict(list)
        for row in executions:
            if row.order_id is not None:
                executions_by_order[row.order_id].append(row)

        cluster_samples: dict[str, list[dict[str, object]]] = defaultdict(list)
        for decision_row in exact_rows:
            linked_risk = risk_by_decision.get(decision_row.id)
            if linked_risk is not None and not bool(linked_risk.allowed):
                continue
            output_payload = decision_row.output_payload if isinstance(decision_row.output_payload, dict) else {}
            rationale_codes = (
                [str(code) for code in output_payload.get("rationale_codes", []) if code not in {None, ""}]
                if isinstance(output_payload.get("rationale_codes"), list)
                else []
            )
            entry_mode = str(output_payload.get("entry_mode") or "none").lower()
            scenario = _setup_cluster_scenario(
                str(output_payload.get("decision") or ""),
                entry_mode,
                rationale_codes,
            )
            primary_regime, row_trend_alignment, *_rest = _extract_analysis_context(decision_row)
            linked_orders = orders_by_decision.get(decision_row.id, [])
            linked_executions = [
                execution_row
                for order_row in linked_orders
                for execution_row in executions_by_order.get(order_row.id, [])
            ]
            if not linked_executions:
                continue
            net_pnl_after_fees = sum(
                _safe_float(execution_row.realized_pnl) - _safe_float(execution_row.fee_paid)
                for execution_row in linked_executions
            )
            avg_signed_slippage_bps = sum(
                self._signed_slippage_bps(execution_row)
                for execution_row in linked_executions
            ) / max(len(linked_executions), 1)
            cluster_key = _setup_cluster_key(
                symbol=symbol_key,
                timeframe=timeframe,
                scenario=scenario,
                entry_mode=entry_mode,
                regime=primary_regime,
                trend_alignment=row_trend_alignment,
            )
            cluster_samples[cluster_key].append(
                {
                    "created_at": decision_row.created_at,
                    "symbol": symbol_key,
                    "timeframe": timeframe,
                    "scenario": scenario,
                    "entry_mode": entry_mode,
                    "regime": primary_regime,
                    "trend_alignment": row_trend_alignment,
                    "net_pnl_after_fees": net_pnl_after_fees,
                    "avg_signed_slippage_bps": avg_signed_slippage_bps,
                }
            )

        now = utcnow_naive()
        cluster_lookup: dict[str, dict[str, object]] = {}
        active_cluster_keys: list[str] = []
        for cluster_key, sample_rows in cluster_samples.items():
            recent_rows = sorted(sample_rows, key=lambda item: item["created_at"], reverse=True)[:SETUP_CLUSTER_LOOKBACK]
            sample_size = len(recent_rows)
            wins = [float(item["net_pnl_after_fees"]) for item in recent_rows if float(item["net_pnl_after_fees"]) > 0]
            losses = [abs(float(item["net_pnl_after_fees"])) for item in recent_rows if float(item["net_pnl_after_fees"]) < 0]
            win_rate = len(wins) / max(sample_size, 1)
            loss_rate = len(losses) / max(sample_size, 1)
            avg_win = sum(wins) / max(len(wins), 1) if wins else 0.0
            avg_loss = sum(losses) / max(len(losses), 1) if losses else 0.0
            expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)
            net_pnl_after_fees = sum(float(item["net_pnl_after_fees"]) for item in recent_rows)
            avg_signed_slippage_bps = sum(
                float(item["avg_signed_slippage_bps"])
                for item in recent_rows
            ) / max(sample_size, 1)
            loss_streak = 0
            for row in recent_rows:
                if float(row["net_pnl_after_fees"]) < 0:
                    loss_streak += 1
                    continue
                break
            disable_reason_codes: list[str] = []
            if expectancy < SETUP_CLUSTER_EXPECTANCY_THRESHOLD:
                disable_reason_codes.append(SETUP_CLUSTER_DISABLE_REASON_CODES["expectancy"])
            if loss_streak >= SETUP_CLUSTER_LOSS_STREAK_THRESHOLD:
                disable_reason_codes.append(SETUP_CLUSTER_DISABLE_REASON_CODES["loss_streak"])
            if avg_signed_slippage_bps >= SETUP_CLUSTER_SIGNED_SLIPPAGE_BPS_THRESHOLD:
                disable_reason_codes.append(SETUP_CLUSTER_DISABLE_REASON_CODES["signed_slippage"])
            if net_pnl_after_fees < SETUP_CLUSTER_NET_PNL_THRESHOLD:
                disable_reason_codes.append(SETUP_CLUSTER_DISABLE_REASON_CODES["net_pnl"])
            underperforming = (
                sample_size >= SETUP_CLUSTER_MIN_SAMPLE_SIZE
                and expectancy < SETUP_CLUSTER_EXPECTANCY_THRESHOLD
                and net_pnl_after_fees < SETUP_CLUSTER_NET_PNL_THRESHOLD
                and (
                    loss_streak >= SETUP_CLUSTER_LOSS_STREAK_THRESHOLD
                    or avg_signed_slippage_bps >= SETUP_CLUSTER_SIGNED_SLIPPAGE_BPS_THRESHOLD
                )
            )
            latest_seen_at = recent_rows[0]["created_at"]
            cooldown_expires_at = (
                latest_seen_at + timedelta(minutes=SETUP_CLUSTER_COOLDOWN_MINUTES)
                if underperforming
                else None
            )
            cooldown_active = bool(
                underperforming
                and cooldown_expires_at is not None
                and cooldown_expires_at > now
            )
            metrics_recovered = bool(
                sample_size >= SETUP_CLUSTER_MIN_SAMPLE_SIZE
                and expectancy >= SETUP_CLUSTER_EXPECTANCY_THRESHOLD
                and net_pnl_after_fees >= SETUP_CLUSTER_NET_PNL_THRESHOLD
            )
            recovery_trigger: str | None = None
            status = "monitoring"
            if sample_size < SETUP_CLUSTER_MIN_SAMPLE_SIZE:
                status = "insufficient_data"
            elif cooldown_active:
                status = "active_disabled"
            elif underperforming and cooldown_expires_at is not None and cooldown_expires_at <= now:
                status = "cooldown_elapsed"
                recovery_trigger = "cooldown_elapsed"
            elif metrics_recovered:
                status = "metrics_recovered"
                recovery_trigger = "positive_recent_metrics"
            if cooldown_active:
                active_cluster_keys.append(cluster_key)
            cluster_lookup[cluster_key] = {
                "cluster_key": cluster_key,
                "symbol": recent_rows[0]["symbol"],
                "timeframe": recent_rows[0]["timeframe"],
                "scenario": recent_rows[0]["scenario"],
                "entry_mode": recent_rows[0]["entry_mode"],
                "regime": recent_rows[0]["regime"],
                "trend_alignment": recent_rows[0]["trend_alignment"],
                "sample_size": sample_size,
                "lookback": SETUP_CLUSTER_LOOKBACK,
                "active": cooldown_active,
                "cooldown_active": cooldown_active,
                "underperforming": underperforming,
                "status": status,
                "recovery_trigger": recovery_trigger,
                "disable_reason_codes": disable_reason_codes,
                "disabled_at": latest_seen_at.isoformat() if underperforming else None,
                "cooldown_expires_at": cooldown_expires_at.isoformat() if cooldown_expires_at is not None else None,
                "metrics": {
                    "win_rate": round(win_rate, 4),
                    "avg_win": round(avg_win, 4),
                    "avg_loss": round(avg_loss, 4),
                    "expectancy": round(expectancy, 4),
                    "net_pnl_after_fees": round(net_pnl_after_fees, 4),
                    "avg_signed_slippage_bps": round(avg_signed_slippage_bps, 4),
                    "loss_streak": loss_streak,
                },
                "thresholds": {
                    "expectancy": SETUP_CLUSTER_EXPECTANCY_THRESHOLD,
                    "net_pnl_after_fees": SETUP_CLUSTER_NET_PNL_THRESHOLD,
                    "loss_streak": SETUP_CLUSTER_LOSS_STREAK_THRESHOLD,
                    "avg_signed_slippage_bps": SETUP_CLUSTER_SIGNED_SLIPPAGE_BPS_THRESHOLD,
                },
                "recovery_condition": {
                    "mode": "cooldown_or_positive_recent_metrics",
                    "cooldown_minutes": SETUP_CLUSTER_COOLDOWN_MINUTES,
                    "cooldown_expires_at": cooldown_expires_at.isoformat() if cooldown_expires_at is not None else None,
                    "metrics_recovery_rule": "expectancy >= 0 and net_pnl_after_fees >= 0, or cooldown elapsed",
                },
            }
        return {
            "symbol": symbol_key,
            "timeframe": timeframe,
            "regime": regime,
            "trend_alignment": trend_alignment,
            "lookback": SETUP_CLUSTER_LOOKBACK,
            "cluster_lookup": cluster_lookup,
            "active_cluster_keys": active_cluster_keys,
            "active_cluster_count": len(active_cluster_keys),
        }

    @staticmethod
    def _performance_summary_bucket(
        *,
        label: str,
        sample_rows: list[dict[str, object]],
    ) -> dict[str, object]:
        recent_rows = sorted(
            sample_rows,
            key=lambda item: item.get("created_at") if isinstance(item.get("created_at"), datetime) else datetime.min,
            reverse=True,
        )[:SETUP_CLUSTER_LOOKBACK]
        sample_size = len(recent_rows)
        if sample_size == 0:
            return {
                "label": label,
                "sample_size": 0,
                "hit_rate": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "expectancy": 0.0,
                "net_pnl_after_fees": 0.0,
                "avg_signed_slippage_bps": 0.0,
                "avg_time_to_profit_minutes": 0.0,
                "avg_drawdown_impact": 0.0,
                "loss_streak": 0,
                "score": 0.55,
                "underperforming": False,
                "insufficient_data": True,
            }
        pnls = [float(item["net_pnl_after_fees"]) for item in recent_rows]
        wins = [pnl for pnl in pnls if pnl > 0]
        losses = [abs(pnl) for pnl in pnls if pnl < 0]
        hit_rate = len(wins) / max(sample_size, 1)
        loss_rate = len(losses) / max(sample_size, 1)
        avg_win = sum(wins) / max(len(wins), 1) if wins else 0.0
        avg_loss = sum(losses) / max(len(losses), 1) if losses else 0.0
        expectancy = (hit_rate * avg_win) - (loss_rate * avg_loss)
        net_pnl_after_fees = sum(pnls)
        avg_signed_slippage_bps = sum(float(item["avg_signed_slippage_bps"]) for item in recent_rows) / max(sample_size, 1)
        avg_time_to_profit_minutes = sum(float(item.get("time_to_profit_minutes", 0.0) or 0.0) for item in recent_rows) / max(sample_size, 1)
        avg_drawdown_impact = sum(float(item.get("drawdown_impact", 0.0) or 0.0) for item in recent_rows) / max(sample_size, 1)
        loss_streak = 0
        for pnl in pnls:
            if pnl < 0:
                loss_streak += 1
                continue
            break
        pnl_scale = max(sum(abs(pnl) for pnl in pnls) / max(sample_size, 1), 5.0)
        expectancy_score = _clamp_score(0.5 + ((expectancy / pnl_scale) * 0.28))
        net_pnl_score = _clamp_score(0.5 + ((net_pnl_after_fees / max(pnl_scale * sample_size, 10.0)) * 0.3))
        slippage_score = _clamp_score(
            0.68
            - (min(max(avg_signed_slippage_bps, 0.0), 18.0) / 18.0 * 0.38)
            + (min(max(-avg_signed_slippage_bps, 0.0), 12.0) / 12.0 * 0.08)
        )
        hit_rate_score = _clamp_score(0.35 + (hit_rate * 0.65))
        composite_score = _clamp_score(
            (expectancy_score * 0.45)
            + (net_pnl_score * 0.3)
            + (hit_rate_score * 0.15)
            + (slippage_score * 0.1)
        )
        underperforming = (
            sample_size >= SETUP_CLUSTER_MIN_SAMPLE_SIZE
            and expectancy < 0
            and net_pnl_after_fees < 0
            and (
                loss_streak >= SETUP_CLUSTER_LOSS_STREAK_THRESHOLD
                or avg_signed_slippage_bps >= SETUP_CLUSTER_SIGNED_SLIPPAGE_BPS_THRESHOLD
            )
        )
        return {
            "label": label,
            "sample_size": sample_size,
            "hit_rate": round(hit_rate, 4),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "expectancy": round(expectancy, 4),
            "net_pnl_after_fees": round(net_pnl_after_fees, 4),
            "avg_signed_slippage_bps": round(avg_signed_slippage_bps, 4),
            "avg_time_to_profit_minutes": round(avg_time_to_profit_minutes, 4),
            "avg_drawdown_impact": round(avg_drawdown_impact, 4),
            "loss_streak": loss_streak,
            "score": round(composite_score, 6),
            "underperforming": underperforming,
            "insufficient_data": sample_size < SETUP_CLUSTER_MIN_SAMPLE_SIZE,
        }

    def _recent_signal_performance_summary(
        self,
        *,
        symbol: str,
        timeframe: str,
        scenario: str,
        regime: str,
        trend_alignment: str,
        strategy_engine: str = "",
    ) -> dict[str, object]:
        symbol_key = symbol.upper()
        decision_rows = list(
            self.session.scalars(
                select(AgentRun)
                .where(AgentRun.role == AgentRole.TRADING_DECISION.value)
                .order_by(desc(AgentRun.created_at))
                .limit(SETUP_CLUSTER_HISTORY_LIMIT)
            )
        )
        exact_rows = []
        for row in decision_rows:
            output_payload = row.output_payload if isinstance(row.output_payload, dict) else {}
            if str(output_payload.get("symbol") or "").upper() != symbol_key:
                continue
            if timeframe and str(output_payload.get("timeframe") or "") != timeframe:
                continue
            if str(output_payload.get("decision") or "").lower() not in {"long", "short"}:
                continue
            exact_rows.append(row)
        if not exact_rows:
            neutral_bucket = self._performance_summary_bucket(label="symbol", sample_rows=[])
            return {
                "score": neutral_bucket["score"],
                "sample_size": neutral_bucket["sample_size"],
                "hit_rate": neutral_bucket["hit_rate"],
                "expectancy": neutral_bucket["expectancy"],
                "net_pnl_after_fees": neutral_bucket["net_pnl_after_fees"],
                "avg_signed_slippage_bps": neutral_bucket["avg_signed_slippage_bps"],
                "avg_time_to_profit_minutes": neutral_bucket["avg_time_to_profit_minutes"],
                "avg_drawdown_impact": neutral_bucket["avg_drawdown_impact"],
                "loss_streak": neutral_bucket["loss_streak"],
                "underperforming": False,
                "components": {
                    "symbol": neutral_bucket,
                    "engine": self._performance_summary_bucket(label="engine", sample_rows=[]),
                    "scenario": self._performance_summary_bucket(label="scenario", sample_rows=[]),
                    "regime": self._performance_summary_bucket(label="regime", sample_rows=[]),
                    "bucket": self._performance_summary_bucket(label="bucket", sample_rows=[]),
                },
            }

        decision_ids = [row.id for row in exact_rows]
        risk_rows = list(
            self.session.scalars(
                select(RiskCheck)
                .where(RiskCheck.decision_run_id.in_(decision_ids))
                .order_by(desc(RiskCheck.created_at))
            )
        )
        risk_by_decision: dict[int, RiskCheck] = {}
        for row in risk_rows:
            if row.decision_run_id is not None and row.decision_run_id not in risk_by_decision:
                risk_by_decision[row.decision_run_id] = row
        orders = list(self.session.scalars(select(Order).where(Order.decision_run_id.in_(decision_ids))))
        orders_by_decision: dict[int, list[Order]] = defaultdict(list)
        for row in orders:
            if row.decision_run_id is not None:
                orders_by_decision[row.decision_run_id].append(row)
        order_ids = [row.id for row in orders]
        executions = (
            list(self.session.scalars(select(Execution).where(Execution.order_id.in_(order_ids))))
            if order_ids
            else []
        )
        executions_by_order: dict[int, list[Execution]] = defaultdict(list)
        for row in executions:
            if row.order_id is not None:
                executions_by_order[row.order_id].append(row)
        position_ids = sorted({int(row.position_id) for row in orders if row.position_id is not None})
        positions_by_id: dict[int, Position] = {}
        if position_ids:
            positions_by_id = {
                position.id: position
                for position in self.session.scalars(select(Position).where(Position.id.in_(position_ids)))
            }

        samples: list[dict[str, object]] = []
        for decision_row in exact_rows:
            linked_risk = risk_by_decision.get(decision_row.id)
            if linked_risk is not None and not bool(linked_risk.allowed):
                continue
            output_payload = decision_row.output_payload if isinstance(decision_row.output_payload, dict) else {}
            metadata_payload = decision_row.metadata_json if isinstance(decision_row.metadata_json, dict) else {}
            rationale_codes = (
                [str(code) for code in output_payload.get("rationale_codes", []) if code not in {None, ""}]
                if isinstance(output_payload.get("rationale_codes"), list)
                else []
            )
            row_entry_mode = str(output_payload.get("entry_mode") or "none").lower()
            row_scenario = _setup_cluster_scenario(
                str(output_payload.get("decision") or ""),
                row_entry_mode,
                rationale_codes,
            )
            row_regime, row_trend_alignment, *_rest = _extract_analysis_context(decision_row)
            linked_orders = orders_by_decision.get(decision_row.id, [])
            linked_executions = [
                execution_row
                for order_row in linked_orders
                for execution_row in executions_by_order.get(order_row.id, [])
            ]
            if not linked_executions:
                continue
            net_pnl_after_fees = sum(
                _safe_float(execution_row.realized_pnl) - _safe_float(execution_row.fee_paid)
                for execution_row in linked_executions
            )
            avg_signed_slippage_bps = sum(
                self._signed_slippage_bps(execution_row)
                for execution_row in linked_executions
            ) / max(len(linked_executions), 1)
            positions = [
                positions_by_id[int(order_row.position_id)]
                for order_row in linked_orders
                if order_row.position_id is not None and int(order_row.position_id) in positions_by_id
            ]
            time_to_profit_minutes = 0.0
            drawdown_impact = 0.0
            if positions:
                profit_hints: list[float] = []
                drawdown_hints: list[float] = []
                for position in positions:
                    position_metadata = _as_dict(position.metadata_json)
                    capital_efficiency = _as_dict(position_metadata.get("capital_efficiency"))
                    position_management = _as_dict(position_metadata.get("position_management"))
                    time_to_profit = _safe_float(capital_efficiency.get("time_to_0_25r_minutes"), default=-1.0)
                    if time_to_profit < 0:
                        time_to_profit = _safe_float(capital_efficiency.get("time_to_0_5r_minutes"), default=-1.0)
                    if time_to_profit < 0:
                        time_to_profit = _safe_float(position_management.get("time_to_profit_minutes"), default=-1.0)
                    if time_to_profit >= 0:
                        profit_hints.append(time_to_profit)
                    drawdown_hint = _safe_float(capital_efficiency.get("mae_r"), default=0.0)
                    if drawdown_hint == 0.0:
                        drawdown_hint = _safe_float(position_management.get("mae_r"), default=0.0)
                    if drawdown_hint != 0.0:
                        drawdown_hints.append(abs(drawdown_hint))
                if profit_hints:
                    time_to_profit_minutes = min(profit_hints)
                if drawdown_hints:
                    drawdown_impact = sum(drawdown_hints) / max(len(drawdown_hints), 1)
            samples.append(
                {
                    "created_at": decision_row.created_at,
                    "scenario": row_scenario,
                    "regime": row_regime,
                    "trend_alignment": row_trend_alignment,
                    "strategy_engine": _strategy_engine_name_from_payload(metadata_payload, output_payload),
                    "net_pnl_after_fees": net_pnl_after_fees,
                    "avg_signed_slippage_bps": avg_signed_slippage_bps,
                    "time_to_profit_minutes": time_to_profit_minutes,
                    "drawdown_impact": drawdown_impact,
                }
            )

        if not samples:
            return self._recent_signal_performance_summary(
                symbol=symbol,
                timeframe="",
                scenario="",
                regime="",
                trend_alignment="",
            ) if timeframe else {
                "score": 0.55,
                "sample_size": 0,
                "hit_rate": 0.0,
                "expectancy": 0.0,
                "net_pnl_after_fees": 0.0,
                "avg_signed_slippage_bps": 0.0,
                "avg_time_to_profit_minutes": 0.0,
                "avg_drawdown_impact": 0.0,
                "loss_streak": 0,
                "underperforming": False,
                "components": {
                    "symbol": self._performance_summary_bucket(label="symbol", sample_rows=[]),
                    "engine": self._performance_summary_bucket(label="engine", sample_rows=[]),
                    "scenario": self._performance_summary_bucket(label="scenario", sample_rows=[]),
                    "regime": self._performance_summary_bucket(label="regime", sample_rows=[]),
                    "bucket": self._performance_summary_bucket(label="bucket", sample_rows=[]),
                },
            }

        symbol_summary = self._performance_summary_bucket(label="symbol", sample_rows=samples)
        engine_rows = [row for row in samples if not strategy_engine or str(row["strategy_engine"]) == strategy_engine]
        scenario_rows = [row for row in samples if not scenario or str(row["scenario"]) == scenario]
        regime_rows = [
            row
            for row in samples
            if (not regime or str(row["regime"]) == regime)
            and (not trend_alignment or str(row["trend_alignment"]) == trend_alignment)
        ]
        bucket_rows = [
            row
            for row in samples
            if (not scenario or str(row["scenario"]) == scenario)
            and (not regime or str(row["regime"]) == regime)
            and (not trend_alignment or str(row["trend_alignment"]) == trend_alignment)
        ]
        if strategy_engine:
            bucket_rows = [row for row in bucket_rows if str(row["strategy_engine"]) == strategy_engine]
        scenario_summary = self._performance_summary_bucket(label="scenario", sample_rows=scenario_rows)
        regime_summary = self._performance_summary_bucket(label="regime", sample_rows=regime_rows)
        bucket_summary = self._performance_summary_bucket(label="bucket", sample_rows=bucket_rows)
        engine_summary = self._performance_summary_bucket(label="engine", sample_rows=engine_rows)
        composite_score = _clamp_score(
            (float(bucket_summary["score"]) * 0.34)
            + (float(engine_summary["score"]) * 0.22)
            + (float(scenario_summary["score"]) * 0.18)
            + (float(regime_summary["score"]) * 0.16)
            + (float(symbol_summary["score"]) * 0.1)
        )
        primary_summary = next(
            (
                component
                for component in (bucket_summary, engine_summary, scenario_summary, regime_summary, symbol_summary)
                if int(component["sample_size"]) > 0
            ),
            symbol_summary,
        )
        underperforming = bool(bucket_summary["underperforming"]) or (
            bool(engine_summary["underperforming"])
            or (bool(scenario_summary["underperforming"]) and bool(regime_summary["underperforming"]))
        )
        return {
            "score": round(composite_score, 6),
            "sample_size": int(primary_summary["sample_size"]),
            "hit_rate": float(primary_summary["hit_rate"]),
            "expectancy": float(primary_summary["expectancy"]),
            "net_pnl_after_fees": float(primary_summary["net_pnl_after_fees"]),
            "avg_signed_slippage_bps": float(primary_summary["avg_signed_slippage_bps"]),
            "avg_time_to_profit_minutes": float(primary_summary["avg_time_to_profit_minutes"]),
            "avg_drawdown_impact": float(primary_summary["avg_drawdown_impact"]),
            "loss_streak": int(primary_summary["loss_streak"]),
            "underperforming": underperforming,
            "components": {
                "symbol": symbol_summary,
                "engine": engine_summary,
                "scenario": scenario_summary,
                "regime": regime_summary,
                "bucket": bucket_summary,
            },
        }

    @staticmethod
    def _market_refresh_cadence_minutes(base: int, *, mode: str) -> int:
        if mode == CADENCE_IDLE_MODE:
            return max(base * 2, 2)
        if mode == CADENCE_ARMED_ENTRY_PLAN_MODE:
            return 1
        if mode in {CADENCE_ACTIVE_POSITION_MODE, CADENCE_HIGH_PRIORITY_RECOVERY_MODE}:
            return max(1, min(base, 2))
        return max(base, 1)

    @staticmethod
    def _position_management_cadence_seconds(base: int, *, mode: str) -> int:
        if mode == CADENCE_IDLE_MODE:
            return max(base * 2, 60)
        if mode in {CADENCE_ACTIVE_POSITION_MODE, CADENCE_HIGH_PRIORITY_RECOVERY_MODE}:
            return max(15, min(base, 30))
        return max(base, 15)

    @staticmethod
    def _decision_cadence_minutes(base: int, *, mode: str) -> int:
        if mode in {
            CADENCE_IDLE_MODE,
            CADENCE_ACTIVE_POSITION_MODE,
            CADENCE_HIGH_PRIORITY_RECOVERY_MODE,
        }:
            return max(base * 2, 2)
        return max(base, 1)

    @staticmethod
    def _ai_cadence_minutes(base: int, *, mode: str, decision_cadence_minutes: int) -> int:
        if mode in {
            CADENCE_IDLE_MODE,
            CADENCE_ACTIVE_POSITION_MODE,
            CADENCE_HIGH_PRIORITY_RECOVERY_MODE,
        }:
            return max(base * 2, decision_cadence_minutes)
        return max(base, 1)

    @staticmethod
    def _cadence_holding_profile_context(
        *,
        open_positions: list[object],
        armed_plans: list[PendingEntryPlan],
    ) -> dict[str, object]:
        for position in open_positions:
            metadata = getattr(position, "metadata_json", None)
            metadata_dict = dict(metadata) if isinstance(metadata, dict) else {}
            management = metadata_dict.get("position_management")
            management_dict = dict(management) if isinstance(management, dict) else {}
            profile = str(management_dict.get("holding_profile") or "scalp").strip().lower() or "scalp"
            return {
                "active_holding_profile": profile,
                "active_holding_profile_reason": str(management_dict.get("holding_profile_reason") or "") or None,
                "holding_profile_cadence_source": "open_position",
                "holding_profile_cadence_hint": resolve_holding_profile_cadence_hint(profile),
            }
        for plan in armed_plans:
            metadata = getattr(plan, "metadata_json", None)
            metadata_dict = dict(metadata) if isinstance(metadata, dict) else {}
            profile = str(metadata_dict.get("holding_profile") or "scalp").strip().lower() or "scalp"
            return {
                "active_holding_profile": profile,
                "active_holding_profile_reason": str(metadata_dict.get("holding_profile_reason") or "") or None,
                "holding_profile_cadence_source": "armed_entry_plan",
                "holding_profile_cadence_hint": resolve_holding_profile_cadence_hint(profile),
            }
        return {
            "active_holding_profile": None,
            "active_holding_profile_reason": None,
            "holding_profile_cadence_source": None,
            "holding_profile_cadence_hint": {},
        }

    def get_symbol_cadence_profile(
        self,
        *,
        symbol: str,
        timeframe: str | None = None,
        runtime_state: dict[str, object] | None = None,
        open_positions: list[object] | None = None,
        feature_payload: object | None = None,
        adaptive_signal_context: dict[str, object] | None = None,
        setup_cluster_context: dict[str, object] | None = None,
        armed_plans: list[PendingEntryPlan] | None = None,
        include_adaptive_underperformance: bool = False,
    ) -> dict[str, object]:
        symbol = symbol.upper()
        effective_settings = self._effective_symbol_settings(symbol)
        timeframe = timeframe or effective_settings.timeframe
        runtime_state = runtime_state or summarize_runtime_state(self.settings_row)
        open_positions = list(open_positions) if open_positions is not None else list(get_open_positions(self.session, symbol))
        armed_plans = list(armed_plans) if armed_plans is not None else self._active_pending_entry_plans(symbol=symbol)
        feature_source = "state_only"
        if feature_payload is None:
            feature_payload = self._latest_feature_snapshot_payload(symbol=symbol, timeframe=timeframe)
            if feature_payload:
                feature_source = "latest_feature_snapshot"
        else:
            feature_source = "current_feature_payload"
        feature_flags = self._cadence_feature_flags(feature_payload)
        holding_profile_context = self._cadence_holding_profile_context(
            open_positions=open_positions,
            armed_plans=armed_plans,
        )
        missing_protection_symbols = {
            str(item).upper()
            for item in runtime_state.get("missing_protection_symbols", [])
            if item
        }
        recovery_active = (
            str(runtime_state.get("operating_state") or "") in {
                PROTECTION_REQUIRED_STATE,
                "DEGRADED_MANAGE_ONLY",
                "EMERGENCY_EXIT",
            }
            or symbol in missing_protection_symbols
        )

        mode = CADENCE_WATCH_MODE
        reasons: list[str] = []
        skip_reason: str | None = None
        ai_skipped_reason: str | None = None
        underperforming = False
        underperforming_reasons: list[str] = []
        setup_disable_active = False
        setup_disable_reasons: list[str] = []
        setup_cluster_active = False
        setup_cluster_reasons: list[str] = []
        if recovery_active:
            mode = CADENCE_HIGH_PRIORITY_RECOVERY_MODE
            reasons = ["HIGH_PRIORITY_RECOVERY_ACTIVE"]
            skip_reason = "HIGH_PRIORITY_RECOVERY_ACTIVE"
        elif armed_plans:
            mode = CADENCE_ARMED_ENTRY_PLAN_MODE
            reasons = ["ARMED_ENTRY_PLAN_ACTIVE"]
            skip_reason = "ARMED_ENTRY_PLAN_ACTIVE"
        elif open_positions:
            mode = CADENCE_ACTIVE_POSITION_MODE
            reasons = ["ACTIVE_POSITION_PRIORITY"]
            skip_reason = "ACTIVE_POSITION_PRIORITY"
        else:
            regime_context = getattr(feature_payload, "regime", None)
            primary_regime = str(getattr(regime_context, "primary_regime", "") or "")
            trend_alignment = str(getattr(regime_context, "trend_alignment", "") or "")
            resolved_adaptive_context = adaptive_signal_context
            if (
                resolved_adaptive_context is None
                and self.settings_row.adaptive_signal_enabled
                and primary_regime
            ):
                resolved_adaptive_context = build_adaptive_signal_context(
                    self.session,
                    enabled=self.settings_row.adaptive_signal_enabled,
                    symbol=symbol,
                    timeframe=timeframe,
                    regime=primary_regime,
                    settings_row=self.settings_row,
                )
            underperforming, underperforming_reasons = self._adaptive_bucket_underperformance(
                resolved_adaptive_context,
            )
            setup_disable_active, setup_disable_reasons = self._adaptive_setup_disable_idle_state(
                resolved_adaptive_context,
            )

            resolved_setup_cluster_context = setup_cluster_context
            if resolved_setup_cluster_context is None and primary_regime and trend_alignment:
                resolved_setup_cluster_context = self._build_setup_cluster_context(
                    symbol=symbol,
                    timeframe=timeframe,
                    regime=primary_regime,
                    trend_alignment=trend_alignment,
                )
            setup_cluster_active, setup_cluster_reasons = self._setup_cluster_idle_state(
                resolved_setup_cluster_context,
            )

            idle_reasons: list[str] = []
            if bool(feature_flags.get("no_trade_zone")):
                idle_reasons.append("RANGE_WEAK_VOLUME_NO_TRADE_ZONE")
            if include_adaptive_underperformance and underperforming:
                idle_reasons.extend(["ADAPTIVE_BUCKET_UNDERPERFORMING", *underperforming_reasons])
                ai_skipped_reason = ai_skipped_reason or "CADENCE_IDLE_UNDERPERFORMING_BUCKET"
            if setup_disable_active:
                idle_reasons.extend(setup_disable_reasons)
                ai_skipped_reason = ai_skipped_reason or "CADENCE_IDLE_SETUP_DISABLE_ACTIVE"
            if setup_cluster_active:
                idle_reasons.extend(setup_cluster_reasons)
                ai_skipped_reason = ai_skipped_reason or "CADENCE_IDLE_SETUP_CLUSTER_ACTIVE"
            if idle_reasons:
                mode = CADENCE_IDLE_MODE
                reasons = list(dict.fromkeys(idle_reasons))
                skip_reason = reasons[0]

        decision_cadence = self._decision_cadence_minutes(
            effective_settings.decision_cycle_interval_minutes,
            mode=mode,
        )
        position_management_cadence = self._position_management_cadence_seconds(
            effective_settings.position_management_interval_seconds,
            mode=mode,
        )
        watcher_cadence = 1 if armed_plans else None
        cadence_hint = holding_profile_context.get("holding_profile_cadence_hint")
        if isinstance(cadence_hint, dict):
            hint_decision = cadence_hint.get("decision_interval_minutes")
            hint_position_management = cadence_hint.get("position_management_interval_seconds")
            hint_watcher = cadence_hint.get("entry_plan_watcher_interval_minutes")
            if mode == CADENCE_ACTIVE_POSITION_MODE:
                if isinstance(hint_decision, int) and hint_decision > 0:
                    decision_cadence = max(decision_cadence, hint_decision)
                if isinstance(hint_position_management, int) and hint_position_management > 0:
                    position_management_cadence = max(position_management_cadence, hint_position_management)
            elif mode == CADENCE_ARMED_ENTRY_PLAN_MODE and isinstance(hint_decision, int) and hint_decision > 0:
                decision_cadence = max(decision_cadence, hint_decision)
            if armed_plans and isinstance(hint_watcher, int) and hint_watcher > 0:
                watcher_cadence = max(watcher_cadence or hint_watcher, hint_watcher)
        ai_cadence = self._ai_cadence_minutes(
            effective_settings.ai_call_interval_minutes,
            mode=mode,
            decision_cadence_minutes=decision_cadence,
        )
        ai_cadence = max(ai_cadence, decision_cadence)
        return {
            "mode": mode,
            "reasons": reasons,
            "skip_reason": skip_reason,
            "ai_skipped_reason": ai_skipped_reason,
            "watcher_enabled": bool(armed_plans),
            "feature_source": feature_source,
            "feature_flags": feature_flags,
            "bucket_underperforming": include_adaptive_underperformance and underperforming,
            "bucket_underperforming_reasons": underperforming_reasons if include_adaptive_underperformance else [],
            "setup_disable_active": setup_disable_active,
            "setup_disable_reasons": setup_disable_reasons,
            "setup_cluster_active": setup_cluster_active,
            "setup_cluster_reasons": setup_cluster_reasons,
            "active_holding_profile": holding_profile_context.get("active_holding_profile"),
            "active_holding_profile_reason": holding_profile_context.get("active_holding_profile_reason"),
            "holding_profile_cadence_source": holding_profile_context.get("holding_profile_cadence_source"),
            "holding_profile_cadence_hint": dict(holding_profile_context.get("holding_profile_cadence_hint") or {}),
            "effective_cadence": {
                "market_refresh_interval_minutes": self._market_refresh_cadence_minutes(
                    effective_settings.market_refresh_interval_minutes,
                    mode=mode,
                ),
                "position_management_interval_seconds": position_management_cadence,
                "decision_cycle_interval_minutes": decision_cadence,
                "ai_call_interval_minutes": ai_cadence,
                "entry_plan_watcher_interval_minutes": watcher_cadence,
            },
        }

    def _should_skip_same_candle_entry(
        self,
        *,
        symbol: str,
        timeframe: str,
        market_snapshot: MarketSnapshotPayload,
        has_open_position: bool,
    ) -> bool:
        if has_open_position:
            return False
        latest_snapshot_time = self._latest_decision_snapshot_time(symbol, timeframe)
        if latest_snapshot_time is None:
            return False
        return latest_snapshot_time == market_snapshot.snapshot_time.isoformat()

    def _latest_alerts(self, limit: int = 5) -> list[Alert]:
        return list(self.session.scalars(select(Alert).order_by(desc(Alert.created_at)).limit(limit)))


    def _latest_health_events(self, limit: int = 10) -> list[SystemHealthEvent]:
        return list(self.session.scalars(select(SystemHealthEvent).order_by(desc(SystemHealthEvent.created_at)).limit(limit)))

    @staticmethod
    def _pending_entry_plan_idempotency_key(
        *,
        symbol: str,
        side: str,
        source_decision_run_id: int,
        expires_at: datetime,
    ) -> str:
        return f"pending-plan:{symbol.upper()}:{side}:{source_decision_run_id}:{expires_at.isoformat()}"

    @staticmethod
    def _pending_entry_plan_metadata(plan: PendingEntryPlan) -> dict[str, object]:
        return dict(plan.metadata_json) if isinstance(plan.metadata_json, dict) else {}

    @staticmethod
    def _pending_entry_plan_snapshot(plan: PendingEntryPlan | None) -> PendingEntryPlanSnapshot:
        if plan is None:
            return PendingEntryPlanSnapshot()
        metadata = (
            dict(plan.metadata_json)
            if isinstance(plan.metadata_json, dict)
            else {}
        )
        trigger_details = metadata.get("trigger_details")
        confirmation_tracking = metadata.get("last_confirmation_tracking")
        confirmation_follow_up_snapshot = metadata.get("confirmation_follow_up_snapshot")
        expiry_context = pending_entry_expiry_context(plan.expires_at)
        return PendingEntryPlanSnapshot(
            plan_id=plan.id,
            symbol=plan.symbol,
            side=plan.side if plan.side in {"long", "short"} else None,
            plan_status=plan.plan_status if plan.plan_status in {"armed", "triggered", "expired", "canceled"} else None,
            source_decision_run_id=plan.source_decision_run_id,
            source_risk_check_id=(
                int(metadata["source_risk_check_id"])
                if str(metadata.get("source_risk_check_id") or "").isdigit()
                else None
            ),
            source_blocked_reason_codes=[
                str(code)
                for code in metadata.get("source_blocked_reason_codes", [])
                if str(code or "").strip()
            ]
            if isinstance(metadata.get("source_blocked_reason_codes"), list)
            else [],
            source_timeframe=plan.source_timeframe,
            regime=plan.regime,
            posture=plan.posture,
            rationale_codes=list(plan.rationale_codes or []),
            entry_mode=plan.entry_mode if plan.entry_mode in {"breakout_confirm", "pullback_confirm", "immediate", "none"} else None,
            holding_profile=str(metadata.get("holding_profile") or "scalp"),
            holding_profile_reason=str(metadata.get("holding_profile_reason") or "") or None,
            entry_zone_min=plan.entry_zone_min,
            entry_zone_max=plan.entry_zone_max,
            invalidation_price=plan.invalidation_price,
            max_chase_bps=plan.max_chase_bps,
            idea_ttl_minutes=plan.idea_ttl_minutes,
            stop_loss=plan.stop_loss,
            take_profit=plan.take_profit,
            risk_pct_cap=plan.risk_pct_cap,
            leverage_cap=plan.leverage_cap,
            created_at=plan.created_at,
            expires_at=plan.expires_at,
            expires_at_time_basis=str(expiry_context["expires_at_time_basis"]),
            app_utc_now=expiry_context["app_utc_now"],
            remaining_ttl_seconds=expiry_context["remaining_ttl_seconds"],
            expired_by_app_utc_now=bool(expiry_context["expired_by_app_utc_now"]),
            triggered_at=plan.triggered_at,
            canceled_at=plan.canceled_at,
            canceled_reason=plan.canceled_reason,
            idempotency_key=plan.idempotency_key,
            last_watch_at=_coerce_datetime(metadata.get("last_watch_at")),
            last_watch_snapshot_id=(
                int(metadata["last_watch_snapshot_id"])
                if str(metadata.get("last_watch_snapshot_id") or "").isdigit()
                else None
            ),
            psychology_scene_review=_as_dict(metadata.get("psychology_scene_review")) or None,
            trigger_details=dict(trigger_details) if isinstance(trigger_details, dict) else {},
            confirmation_tracking=(
                dict(confirmation_tracking)
                if isinstance(confirmation_tracking, dict)
                else {}
            ),
            confirmation_follow_up_snapshot=(
                dict(confirmation_follow_up_snapshot)
                if isinstance(confirmation_follow_up_snapshot, dict)
                else {}
            ),
        )

    @staticmethod
    def _summary_float(value: object, *, digits: int = 6) -> float | None:
        if value is None or value == "":
            return None
        try:
            return round(float(value), digits)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _distance_bps(cls, *, reference_price: float | None, target_price: float | None) -> float | None:
        if reference_price is None or target_price is None or reference_price <= 0:
            return None
        return cls._summary_float(((target_price - reference_price) / reference_price) * 10000.0)

    @classmethod
    def _zone_distance_summary(
        cls,
        *,
        latest_price: float | None,
        zone_min: float | None,
        zone_max: float | None,
    ) -> dict[str, object]:
        if latest_price is None or latest_price <= 0 or zone_min is None or zone_max is None:
            return {"zone_relation": "unknown", "distance_to_zone_bps": None}
        lower = min(zone_min, zone_max)
        upper = max(zone_min, zone_max)
        if lower <= latest_price <= upper:
            return {"zone_relation": "inside_zone", "distance_to_zone_bps": 0.0}
        if latest_price < lower:
            return {
                "zone_relation": "below_zone",
                "distance_to_zone_bps": cls._distance_bps(
                    reference_price=latest_price,
                    target_price=lower,
                ),
            }
        return {
            "zone_relation": "above_zone",
            "distance_to_zone_bps": cls._distance_bps(
                reference_price=latest_price,
                target_price=upper,
            ),
        }

    @classmethod
    def _minimum_actionable_notional_hint(cls, reference_price: float | None) -> float:
        safe_reference = max(reference_price or 0.0, 1.0)
        return round(max(ENTRY_PLAN_MIN_ACTIONABLE_NOTIONAL_FLOOR, safe_reference * 0.0005), 6)

    @classmethod
    def _active_position_ai_summary(
        cls,
        *,
        open_positions: list[Position],
        position_management_context: dict[str, object],
    ) -> dict[str, object]:
        summary: dict[str, object] = {
            "has_open_position": bool(open_positions),
            "open_position_count": len(open_positions),
        }
        if not open_positions:
            return summary
        position = open_positions[0]
        quantity = cls._summary_float(getattr(position, "quantity", None)) or 0.0
        entry_price = cls._summary_float(getattr(position, "entry_price", None))
        mark_price = cls._summary_float(getattr(position, "mark_price", None))
        stop_loss = cls._summary_float(getattr(position, "stop_loss", None))
        take_profit = cls._summary_float(getattr(position, "take_profit", None))
        side = str(getattr(position, "side", "") or "").lower()
        current_r_multiple = cls._summary_float(position_management_context.get("current_r_multiple"), digits=4)
        if current_r_multiple is None and entry_price is not None and mark_price is not None and stop_loss is not None:
            initial_risk = abs(entry_price - stop_loss)
            if initial_risk > 0:
                signed_move = mark_price - entry_price if side == "long" else entry_price - mark_price
                current_r_multiple = cls._summary_float(signed_move / initial_risk, digits=4)
        metadata = _as_dict(getattr(position, "metadata_json", None))
        liquidation_price = cls._summary_float(
            metadata.get("liquidation_price")
            or metadata.get("exchange_liquidation_price")
            or metadata.get("liq_price")
        )
        summary.update(
            {
                "symbol": getattr(position, "symbol", None),
                "side": side or None,
                "quantity": quantity,
                "entry_price": entry_price,
                "mark_price": mark_price,
                "position_notional": cls._summary_float(abs(quantity) * (mark_price or entry_price or 0.0)),
                "leverage": cls._summary_float(getattr(position, "leverage", None), digits=4),
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "unrealized_pnl": cls._summary_float(getattr(position, "unrealized_pnl", None)),
                "realized_pnl": cls._summary_float(getattr(position, "realized_pnl", None)),
                "current_r_multiple": current_r_multiple,
                "stop_distance_bps": cls._distance_bps(reference_price=mark_price, target_price=stop_loss),
                "take_profit_distance_bps": cls._distance_bps(reference_price=mark_price, target_price=take_profit),
                "liquidation_price": liquidation_price,
                "liquidation_distance_bps": cls._distance_bps(
                    reference_price=mark_price,
                    target_price=liquidation_price,
                ),
                "position_management_enabled": bool(position_management_context.get("enabled", False)),
                "position_management_status": str(position_management_context.get("status") or "") or None,
                "holding_profile": str(position_management_context.get("holding_profile") or "") or None,
                "management_stage": str(position_management_context.get("management_stage") or "") or None,
                "hard_stop_active": bool(position_management_context.get("hard_stop_active", stop_loss is not None)),
                "tightened_stop_loss": cls._summary_float(position_management_context.get("tightened_stop_loss")),
                "partial_take_profit_ready": bool(position_management_context.get("partial_take_profit_ready", False)),
                "partial_take_profit_taken": bool(position_management_context.get("partial_take_profit_taken", False)),
                "take_profit_order_mode": str(position_management_context.get("take_profit_order_mode") or "") or None,
                "reduce_reason_codes": list(position_management_context.get("reduce_reason_codes") or []),
                "applied_rule_candidates": list(position_management_context.get("applied_rule_candidates") or []),
            }
        )
        return summary

    @classmethod
    def _pending_entry_plan_ai_summary(
        cls,
        *,
        symbol: str,
        active_plans: list[PendingEntryPlan],
        latest_price: float | None,
        risk_budget: dict[str, object],
        open_positions: list[Position],
        now: datetime,
        include_plan_limit: int = 3,
    ) -> dict[str, object]:
        symbol = symbol.upper()
        same_symbol_plans = [plan for plan in active_plans if str(plan.symbol).upper() == symbol]
        max_new_position_notional = cls._summary_float(risk_budget.get("max_new_position_notional_for_symbol")) or 0.0
        minimum_actionable_notional = cls._minimum_actionable_notional_hint(latest_price)
        side_capacity = {
            "long": cls._summary_float(risk_budget.get("max_additional_long_notional")) or 0.0,
            "short": cls._summary_float(risk_budget.get("max_additional_short_notional")) or 0.0,
        }
        plans: list[dict[str, object]] = []
        for plan in same_symbol_plans[:include_plan_limit]:
            zone = cls._zone_distance_summary(
                latest_price=latest_price,
                zone_min=cls._summary_float(plan.entry_zone_min),
                zone_max=cls._summary_float(plan.entry_zone_max),
            )
            side = str(plan.side or "").lower()
            side_notional = side_capacity.get(side, 0.0)
            plans.append(
                {
                    "plan_id": plan.id,
                    "symbol": plan.symbol,
                    "side": side if side in {"long", "short"} else None,
                    "status": plan.plan_status,
                    "source_decision_run_id": plan.source_decision_run_id,
                    "entry_mode": plan.entry_mode,
                    "entry_zone_min": cls._summary_float(plan.entry_zone_min),
                    "entry_zone_max": cls._summary_float(plan.entry_zone_max),
                    "invalidation_price": cls._summary_float(plan.invalidation_price),
                    "max_chase_bps": cls._summary_float(plan.max_chase_bps),
                    "stop_loss": cls._summary_float(plan.stop_loss),
                    "take_profit": cls._summary_float(plan.take_profit),
                    "risk_pct_cap": cls._summary_float(plan.risk_pct_cap),
                    "leverage_cap": cls._summary_float(plan.leverage_cap, digits=4),
                    "rationale_codes": list(plan.rationale_codes or []),
                    "created_at": plan.created_at.isoformat() if plan.created_at is not None else None,
                    "expires_at": plan.expires_at.isoformat() if plan.expires_at is not None else None,
                    "minutes_until_expiry": cls._summary_float(
                        (
                            pending_entry_remaining_ttl_seconds(plan.expires_at, now=now) / 60.0
                            if plan.expires_at is not None
                            else None
                        ),
                        digits=2,
                    ),
                    "zone_relation": zone["zone_relation"],
                    "distance_to_zone_bps": zone["distance_to_zone_bps"],
                    "side_capacity_notional": side_notional,
                    "side_capacity_available": side_notional >= minimum_actionable_notional,
                }
            )
        capacity_available = max_new_position_notional >= minimum_actionable_notional
        return {
            "active_plan_count": len(active_plans),
            "same_symbol_plan_count": len(same_symbol_plans),
            "has_same_symbol_plan": bool(same_symbol_plans),
            "open_position_count": len(open_positions),
            "new_entry_capacity_available": capacity_available,
            "minimum_actionable_notional": minimum_actionable_notional,
            "max_new_position_notional_for_symbol": max_new_position_notional,
            "side_capacity_notional": side_capacity,
            "monitoring_action": (
                "capacity_blocked_no_new_plan_monitoring"
                if same_symbol_plans and not capacity_available and not open_positions
                else "watch_existing_plan"
                if same_symbol_plans
                else "no_active_plan"
            ),
            "plans": plans,
        }

    @classmethod
    def _execution_constraints_ai_summary(
        cls,
        *,
        symbol: str,
        market_snapshot: MarketSnapshotPayload,
        risk_context: dict[str, object],
        settings_row: object,
        open_positions: list[Position],
    ) -> dict[str, object]:
        risk_budget = _as_dict(risk_context.get("risk_budget"))
        latest_price = cls._summary_float(getattr(market_snapshot, "latest_price", None))
        minimum_actionable_notional = cls._minimum_actionable_notional_hint(latest_price)
        max_new_position_notional = cls._summary_float(risk_budget.get("max_new_position_notional_for_symbol")) or 0.0
        return {
            "symbol": symbol.upper(),
            "reference_price": latest_price,
            "minimum_actionable_notional": minimum_actionable_notional,
            "minimum_source": "heuristic_hint_only_risk_guard_recomputes_exchange_filters",
            "max_risk_per_trade": cls._summary_float(risk_context.get("max_risk_per_trade")),
            "max_leverage": cls._summary_float(risk_context.get("max_leverage"), digits=4),
            "max_new_position_notional_for_symbol": max_new_position_notional,
            "max_additional_long_notional": cls._summary_float(risk_budget.get("max_additional_long_notional")),
            "max_additional_short_notional": cls._summary_float(risk_budget.get("max_additional_short_notional")),
            "new_entry_capacity_status": (
                "available"
                if max_new_position_notional >= minimum_actionable_notional
                else "below_minimum_actionable_notional"
            ),
            "has_open_position": bool(open_positions),
            "reduce_only_required_for_management": bool(open_positions),
            "live_trading_enabled": bool(getattr(settings_row, "live_trading_enabled", False)),
            "rollout_mode": str(getattr(settings_row, "rollout_mode", "") or ""),
            "live_execution_armed": bool(getattr(settings_row, "live_execution_armed", False)),
            "limited_live_max_notional": cls._summary_float(getattr(settings_row, "limited_live_max_notional", None)),
            "risk_guard_final_authority": True,
            "ai_output_executes_directly": False,
        }

    @staticmethod
    def _recent_tp_direction_from_selection_context(selection_context: dict[str, object] | None) -> str | None:
        selection = _as_dict(selection_context)
        candidate = _as_dict(selection.get("candidate"))
        for value in (candidate.get("decision"), selection.get("decision_hint")):
            direction = str(value or "").strip().lower()
            if direction in {"long", "short"}:
                return direction
        return None

    @classmethod
    def _recent_tp_direction_from_pending_entry_plan(
        cls,
        plan: PendingEntryPlan,
        selection_context: dict[str, object] | None,
    ) -> str | None:
        for value in (getattr(plan, "side", None), getattr(plan, "direction", None)):
            direction = str(value or "").strip().lower()
            if direction in {"long", "short"}:
                return direction
        return cls._recent_tp_direction_from_selection_context(selection_context)

    @staticmethod
    def _recent_tp_selection_cadence_hint(selection_context: dict[str, object] | None) -> dict[str, object]:
        selection = _as_dict(selection_context)
        candidate = _as_dict(selection.get("candidate"))
        sources = (
            candidate,
            selection,
            _as_dict(candidate.get("holding_profile_context")),
            _as_dict(selection.get("holding_profile_context")),
        )
        for source in sources:
            for key in ("holding_profile", "profile", "profile_key", "strategy_profile"):
                profile = str(source.get(key) or "").strip().lower()
                if profile:
                    return dict(resolve_holding_profile_cadence_hint(profile))
        return {}

    @classmethod
    def _recent_tp_close_lookback_minutes(
        cls,
        *,
        cadence_profile: dict[str, object],
        pending_entry_plan: PendingEntryPlan | None = None,
        selection_context: dict[str, object] | None = None,
        ai_call_interval_minutes: int | float | None = None,
    ) -> int | None:
        if pending_entry_plan is not None:
            plan_ttl = cls._summary_float(getattr(pending_entry_plan, "idea_ttl_minutes", None), digits=0)
            if plan_ttl is not None and plan_ttl > 0:
                return int(plan_ttl)

        cadence = _as_dict(cadence_profile.get("effective_cadence"))
        cadence_hint = _as_dict(cadence_profile.get("holding_profile_cadence_hint"))
        selection_cadence_hint = cls._recent_tp_selection_cadence_hint(selection_context)
        candidates = [
            cls._summary_float(selection_cadence_hint.get("decision_interval_minutes"), digits=0),
            cls._summary_float(cadence_hint.get("decision_interval_minutes"), digits=0),
            cls._summary_float(ai_call_interval_minutes, digits=0),
            cls._summary_float(cadence.get("entry_plan_watcher_interval_minutes"), digits=0),
            cls._summary_float(cadence.get("decision_cycle_interval_minutes"), digits=0),
        ]
        positive_candidates = [int(value) for value in candidates if value is not None and value > 0]
        if not positive_candidates:
            return None
        return max(positive_candidates)

    @staticmethod
    def _execution_payload_exchange_order_type(execution: Execution) -> str:
        payload = _as_dict(getattr(execution, "payload", None))
        exchange_order = _as_dict(payload.get("exchange_order"))
        return str(
            exchange_order.get("type")
            or exchange_order.get("orderType")
            or exchange_order.get("origType")
            or ""
        ).strip().lower()

    @classmethod
    def _tp_close_marker_for_execution(
        cls,
        *,
        direction: str,
        execution: Execution,
        order: Order | None,
    ) -> str | None:
        closes_position = False
        if order is not None:
            closes_position = bool(order.reduce_only or order.close_only)
            order_side = str(order.side or "").strip().lower()
            if (direction == "short" and order_side == "buy") or (
                direction == "long" and order_side == "sell"
            ):
                closes_position = True
        if _safe_float(getattr(execution, "realized_pnl", None), default=0.0) != 0.0:
            closes_position = True
        if not closes_position:
            return None

        if order is not None:
            order_type = str(order.order_type or "").strip().lower()
            if order_type in TAKE_PROFIT_CLOSE_ORDER_TYPES:
                return order_type
            order_metadata = _as_dict(order.metadata_json)
            protective_component = str(order_metadata.get("protective_component") or "").strip().lower()
            if protective_component in TAKE_PROFIT_CLOSE_PROTECTIVE_COMPONENTS:
                return protective_component

        exchange_order_type = cls._execution_payload_exchange_order_type(execution)
        if exchange_order_type in TAKE_PROFIT_CLOSE_ORDER_TYPES:
            return exchange_order_type
        return None

    @classmethod
    def _recent_same_direction_tp_close_summary(
        cls,
        session: Session,
        *,
        symbol: str,
        direction: str | None,
        generated_at: datetime,
        lookback_minutes: int | None,
    ) -> dict[str, object] | None:
        direction = str(direction or "").strip().lower()
        symbol = str(symbol or "").strip().upper()
        if direction not in {"long", "short"} or not symbol or lookback_minutes is None or lookback_minutes <= 0:
            return None

        normalized_now = pending_entry_utc_naive(generated_at)
        cutoff = normalized_now - timedelta(minutes=int(lookback_minutes))
        positions = session.scalars(
            select(Position)
            .where(
                Position.symbol == symbol,
                Position.side == direction,
                Position.status == "closed",
                Position.mode == "live",
                Position.closed_at.is_not(None),
                Position.closed_at >= cutoff,
                Position.closed_at <= normalized_now,
            )
            .order_by(desc(Position.closed_at), desc(Position.id))
            .limit(5)
        ).all()
        for position in positions:
            execution_rows = list(
                session.execute(
                    select(Execution, Order)
                    .join(Order, Execution.order_id == Order.id, isouter=True)
                    .where(Execution.position_id == position.id)
                    .order_by(desc(Execution.created_at), desc(Execution.id))
                )
            )
            close_reason = None
            for execution, order in execution_rows:
                close_reason = cls._tp_close_marker_for_execution(
                    direction=direction,
                    execution=execution,
                    order=order,
                )
                if close_reason is not None:
                    break
            if close_reason is None:
                continue

            gross_pnl_value = _safe_float(getattr(position, "realized_pnl", None), default=0.0)
            fee_value = sum(abs(_safe_float(execution.fee_paid, default=0.0)) for execution, _order in execution_rows)
            fee_to_gross_ratio = fee_value / gross_pnl_value if gross_pnl_value > 0.0 else None
            closed_at = pending_entry_utc_naive(position.closed_at)
            return {
                "symbol": symbol,
                "side": direction,
                "close_reason": close_reason,
                "recent_same_direction_tp_close": True,
                "minutes_since_recent_same_direction_tp": cls._summary_float(
                    (normalized_now - closed_at).total_seconds() / 60.0,
                    digits=4,
                ),
                "recent_tp_gross_pnl": cls._summary_float(gross_pnl_value),
                "recent_tp_net_pnl": cls._summary_float(gross_pnl_value - fee_value),
                "recent_tp_fee": cls._summary_float(fee_value),
                "recent_tp_fee_to_gross_ratio": cls._summary_float(fee_to_gross_ratio),
            }
        return None

    def _risk_context_with_recent_tp_close_summary(
        self,
        risk_context: dict[str, object],
        *,
        symbol: str,
        direction: str | None,
        generated_at: datetime,
        lookback_minutes: int | None,
    ) -> dict[str, object]:
        summary = self._recent_same_direction_tp_close_summary(
            self.session,
            symbol=symbol,
            direction=direction,
            generated_at=generated_at,
            lookback_minutes=lookback_minutes,
        )
        if summary is None:
            return risk_context
        return {
            **risk_context,
            "recent_closed_position_summary": summary,
        }

    @staticmethod
    def _pending_plan_volume_profile_details(feature_payload, decision: TradeDecision) -> dict[str, object]:
        volume_profile = getattr(feature_payload, "volume_profile", None)
        if volume_profile is None or not bool(getattr(volume_profile, "available", False)):
            return {"available": False}

        side = decision.decision if decision.decision in {"long", "short"} else None
        nearest_support = getattr(volume_profile, "nearest_support", None)
        nearest_resistance = getattr(volume_profile, "nearest_resistance", None)
        if side == "long":
            entry_reference = nearest_support
            take_profit_reference = nearest_resistance
            stop_reference = nearest_support or getattr(volume_profile, "value_area_low", None)
        elif side == "short":
            entry_reference = nearest_resistance
            take_profit_reference = nearest_support
            stop_reference = nearest_resistance or getattr(volume_profile, "value_area_high", None)
        else:
            entry_reference = None
            take_profit_reference = None
            stop_reference = None

        return {
            "available": True,
            "timeframe": getattr(volume_profile, "timeframe", None),
            "lookback_candles": getattr(volume_profile, "lookback_candles", 0),
            "current_position": getattr(volume_profile, "current_position", "unknown"),
            "poc_price": getattr(volume_profile, "poc_price", None),
            "value_area_low": getattr(volume_profile, "value_area_low", None),
            "value_area_high": getattr(volume_profile, "value_area_high", None),
            "nearest_support": nearest_support,
            "nearest_resistance": nearest_resistance,
            "support_distance_pct": getattr(volume_profile, "support_distance_pct", None),
            "resistance_distance_pct": getattr(volume_profile, "resistance_distance_pct", None),
            "entry_reference": entry_reference,
            "take_profit_reference": take_profit_reference,
            "stop_reference": stop_reference,
            "entry_zone_min": decision.entry_zone_min,
            "entry_zone_max": decision.entry_zone_max,
            "stop_loss": decision.stop_loss,
            "take_profit": decision.take_profit,
        }

    def _active_pending_entry_plans(
        self,
        *,
        symbol: str | None = None,
    ) -> list[PendingEntryPlan]:
        query = active_pending_entry_plan_statement()
        if symbol is not None:
            query = query.where(PendingEntryPlan.symbol == symbol.upper())
        return list(self.session.scalars(query.order_by(PendingEntryPlan.created_at.desc())))

    def _entry_plan_posture(self, *, decision_side: str, feature_payload) -> str:
        state = str(feature_payload.pullback_context.state or "unclear")
        if decision_side == "long":
            if state == "bullish_pullback":
                return "bullish_pullback"
            if state == "bullish_continuation":
                return "bullish_continuation"
            if feature_payload.breakout.range_breakout_direction == "up" or feature_payload.breakout.broke_swing_high:
                return "breakout_exception"
        if decision_side == "short":
            if state == "bearish_pullback":
                return "bearish_pullback"
            if state == "bearish_continuation":
                return "bearish_continuation"
            if feature_payload.breakout.range_breakout_direction == "down" or feature_payload.breakout.broke_swing_low:
                return "breakout_exception"
        return state

    def _cancel_pending_entry_plan(
        self,
        plan: PendingEntryPlan,
        *,
        reason: str,
        cancel_status: str = "canceled",
        detail: dict[str, object] | None = None,
        correlation_ids: dict[str, object] | None = None,
    ) -> PendingEntryPlan:
        if plan.plan_status != ACTIVE_ENTRY_PLAN_STATUS:
            return plan
        now = utcnow_naive()
        metadata = self._pending_entry_plan_metadata(plan)
        metadata["last_transition_at"] = now.isoformat()
        metadata["last_transition_reason"] = reason
        if detail:
            metadata["last_transition_detail"] = dict(detail)
        plan.plan_status = "expired" if cancel_status == "expired" else "canceled"
        plan.canceled_at = now
        plan.canceled_reason = reason
        plan.metadata_json = metadata
        self.session.add(plan)
        self.session.flush()
        record_audit_event(
            self.session,
            event_type="pending_entry_plan_expired" if cancel_status == "expired" else "pending_entry_plan_canceled",
            entity_type="pending_entry_plan",
            entity_id=str(plan.id),
            severity="info" if cancel_status == "expired" else "warning",
            message="Pending entry plan transitioned out of armed status.",
            payload={
                "symbol": plan.symbol,
                "side": plan.side,
                "plan_status": plan.plan_status,
                "reason": reason,
                "source_decision_run_id": plan.source_decision_run_id,
                "detail": dict(detail or {}),
            },
            correlation_ids=correlation_ids,
        )
        correlation_payload = _as_dict(correlation_ids)
        funnel_cycle_id = str(
            correlation_payload.get("cycle_id")
            or metadata.get("last_watch_cycle_id")
            or metadata.get("cycle_id")
            or f"pending-entry-plan:{plan.id}"
        )
        m1_status = (
            "failed"
            if reason.startswith("PLAN_CONFIRM") or reason in {"PLAN_MAX_CHASE_EXCEEDED", "PLAN_LATE_CHASE_WAITING_REENTRY"}
            else "not_checked"
        )
        self._record_decision_funnel_audit(
            cycle_id=funnel_cycle_id,
            symbol=plan.symbol,
            timeframe=plan.source_timeframe,
            regime=plan.regime,
            stage="entry_plan_expired" if cancel_status == "expired" else "entry_plan_canceled",
            status=plan.plan_status,
            strategy_candidate=self._pending_plan_funnel_candidate(plan),
            entry_plan_status=plan.plan_status,
            m1_confirmation_status=m1_status,
            final_risk_status="not_checked",
            blocked_reason_codes=[reason],
            hold_reason_codes=[reason] if reason == "NEW_AI_HOLD_DECISION" else [],
            detail={"plan_id": plan.id, "reason": reason, **dict(detail or {})},
            severity="info" if cancel_status == "expired" else "warning",
            entity_type="pending_entry_plan",
            entity_id=str(plan.id),
            correlation_ids=normalize_correlation_ids(
                correlation_ids,
                cycle_id=funnel_cycle_id,
                snapshot_id=correlation_payload.get("snapshot_id") or metadata.get("last_watch_snapshot_id") or metadata.get("snapshot_id"),
                decision_id=correlation_payload.get("decision_id") or plan.source_decision_run_id,
            ),
        )
        refresh_decision_performance_fact_usefulness(self.session, plan.source_decision_run_id)
        return plan

    def _defer_pending_entry_plan(
        self,
        plan: PendingEntryPlan,
        *,
        reason: str,
        generated_at: datetime,
        market_row_id: int | None = None,
        detail: dict[str, object] | None = None,
    ) -> PendingEntryPlan:
        if plan.plan_status != ACTIVE_ENTRY_PLAN_STATUS:
            return plan
        metadata = self._pending_entry_plan_metadata(plan)
        max_extension_seconds = max(int(plan.idea_ttl_minutes or 15), 1) * 60
        try:
            current_extension_seconds = max(int(metadata.get("external_wait_extension_seconds") or 0), 0)
        except (TypeError, ValueError):
            current_extension_seconds = 0
        last_watch_at = _coerce_datetime(metadata.get("last_watch_at"))
        elapsed_since_last_watch_seconds = 60
        if last_watch_at is not None:
            if last_watch_at.tzinfo is not None:
                last_watch_at = last_watch_at.replace(tzinfo=None)
            elapsed_since_last_watch_seconds = max(int((generated_at - last_watch_at).total_seconds()), 0)
        remaining_extension_seconds = max(max_extension_seconds - current_extension_seconds, 0)
        cadence_extension_seconds = min(60, elapsed_since_last_watch_seconds)
        catch_up_seconds = 0
        if (
            pending_entry_plan_is_expired(plan.expires_at, now=generated_at)
            and elapsed_since_last_watch_seconds > 0
        ):
            minimum_wait_until = generated_at + timedelta(seconds=60)
            normalized_expires_at = pending_entry_utc_naive(plan.expires_at)
            catch_up_needed_seconds = max(int((minimum_wait_until - normalized_expires_at).total_seconds()), 0)
            catch_up_budget_seconds = (
                catch_up_needed_seconds
                if last_watch_at is None
                else elapsed_since_last_watch_seconds + 60
            )
            catch_up_seconds = min(catch_up_needed_seconds, catch_up_budget_seconds)
        extension_seconds = min(
            max(cadence_extension_seconds, catch_up_seconds),
            remaining_extension_seconds,
        )
        if extension_seconds > 0:
            plan.expires_at = pending_entry_utc_naive(plan.expires_at) + timedelta(seconds=extension_seconds)
            current_extension_seconds += extension_seconds
        wait_detail = dict(detail or {})
        if extension_seconds > 0:
            wait_detail["extended_ttl_seconds"] = extension_seconds
            wait_detail["external_wait_extension_seconds"] = current_extension_seconds
            wait_detail["external_wait_extension_cap_seconds"] = max_extension_seconds
        metadata["last_watch_at"] = generated_at.isoformat()
        metadata["last_watch_blocked_reason_codes"] = [reason]
        metadata["external_wait_extension_seconds"] = current_extension_seconds
        if market_row_id is not None:
            metadata["last_watch_snapshot_id"] = market_row_id
            metadata["last_watch_cycle_id"] = f"entry-plan-watch:{plan.id}:{market_row_id}"
        if isinstance(wait_detail.get("stale_scopes"), list):
            metadata["last_watch_stale_scopes"] = list(wait_detail["stale_scopes"])
        if wait_detail:
            metadata["last_watch_block_detail"] = wait_detail
        plan.metadata_json = metadata
        self.session.add(plan)
        self.session.flush()
        funnel_cycle_id = str(
            metadata.get("last_watch_cycle_id")
            or metadata.get("cycle_id")
            or f"pending-entry-plan:{plan.id}"
        )
        self._record_decision_funnel_audit(
            cycle_id=funnel_cycle_id,
            symbol=plan.symbol,
            timeframe=plan.source_timeframe,
            regime=plan.regime,
            stage="entry_plan_waiting",
            status="waiting",
            strategy_candidate=self._pending_plan_funnel_candidate(plan),
            entry_plan_status="waiting",
            m1_confirmation_status="not_checked",
            final_risk_status="not_checked",
            blocked_reason_codes=[reason],
            detail={"plan_id": plan.id, "reason": reason, **wait_detail},
            severity="info",
            entity_type="pending_entry_plan",
            entity_id=str(plan.id),
            correlation_ids=normalize_correlation_ids(
                cycle_id=funnel_cycle_id,
                snapshot_id=market_row_id or metadata.get("snapshot_id"),
                decision_id=plan.source_decision_run_id,
            ),
        )
        return plan

    @staticmethod
    def _decision_reason_code_set(decision: object) -> set[str]:
        reason_codes: set[str] = set()
        for attr_name in (
            "rationale_codes",
            "primary_reason_codes",
            "no_trade_reason_codes",
            "abstain_reason_codes",
            "invalidation_reason_codes",
        ):
            values = getattr(decision, attr_name, []) or []
            if not isinstance(values, list):
                continue
            reason_codes.update(str(code).strip().upper() for code in values if str(code or "").strip())
        return reason_codes

    @classmethod
    def _hold_decision_invalidates_plan(cls, decision: object, plan: PendingEntryPlan) -> bool:
        if str(getattr(decision, "decision", "") or "") != "hold":
            return False
        invalidation_reason_codes = getattr(decision, "invalidation_reason_codes", []) or []
        if isinstance(invalidation_reason_codes, list) and {
            str(code).strip().upper()
            for code in invalidation_reason_codes
            if str(code or "").strip()
        } & ENTRY_PLAN_HOLD_CANCEL_REASON_CODES:
            return True
        reason_codes = cls._decision_reason_code_set(decision)
        if reason_codes & ENTRY_PLAN_HOLD_CANCEL_REASON_CODES:
            return True
        if plan.side == "long" and reason_codes & ENTRY_PLAN_LONG_INVALIDATED_BY_REASON_CODES:
            return True
        return bool(plan.side == "short" and reason_codes & ENTRY_PLAN_SHORT_INVALIDATED_BY_REASON_CODES)

    def _plan_entry_allowed_without_trigger(
        self,
        decision: object,
        risk_result,
        *,
        watch_entry_plan: bool = False,
    ) -> bool:
        decision_side = str(getattr(decision, "decision", "") or "")
        if decision_side not in {"long", "short"}:
            return False
        blockers = set(getattr(risk_result, "blocked_reason_codes", []) or getattr(risk_result, "reason_codes", []))
        if (
            EXCHANGE_CAN_TRADE_UNKNOWN_REASON_CODE in blockers
            and get_rollout_mode(self.settings_row) == "full_live"
            and rollout_mode_allows_exchange_submit(self.settings_row)
        ):
            return False
        storage_blockers = ENTRY_PLAN_WATCH_STORAGE_BLOCKERS if watch_entry_plan else ENTRY_PLAN_NON_STRUCTURAL_BLOCKERS
        return len(blockers - storage_blockers) == 0

    @staticmethod
    def _watch_entry_plan_side_is_consistent(
        *,
        side: str,
        reference_price: float,
        stop_loss: float | None,
        take_profit: float | None,
    ) -> bool:
        if stop_loss is None or take_profit is None:
            return False
        if side == "long":
            return stop_loss < reference_price < take_profit
        return take_profit < reference_price < stop_loss

    def _watch_entry_plan_decision(self, decision: TradeDecision) -> TradeDecision | None:
        watch_plan = getattr(decision, "watch_entry_plan", None)
        if decision.decision != "hold" or watch_plan is None:
            return None
        side = str(getattr(watch_plan, "side", "") or "").lower()
        if side not in {"long", "short"}:
            return None
        entry_zone_min = _safe_float(getattr(watch_plan, "entry_zone_min", None), default=0.0)
        entry_zone_max = _safe_float(getattr(watch_plan, "entry_zone_max", None), default=0.0)
        if entry_zone_min <= 0.0 or entry_zone_max <= 0.0:
            return None
        stop_loss = getattr(watch_plan, "stop_loss", None)
        take_profit = getattr(watch_plan, "take_profit", None)
        reference_price = self._reference_price_from_zone(
            entry_zone_min=entry_zone_min,
            entry_zone_max=entry_zone_max,
            fallback_price=entry_zone_min,
        )
        if not self._watch_entry_plan_side_is_consistent(
            side=side,
            reference_price=reference_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        ):
            return None
        watch_reason_codes = [
            str(code)
            for code in getattr(watch_plan, "reason_codes", []) or []
            if str(code or "").strip()
        ]
        rationale_codes = self._unique_reason_codes(
            [
                *list(decision.rationale_codes or []),
                *watch_reason_codes,
                ENTRY_PLAN_WATCH_REASON_CODE,
            ]
        )
        source_abstain_reason_codes = [
            str(code)
            for code in getattr(decision, "abstain_reason_codes", []) or []
            if str(code or "").strip()
        ]
        return decision.model_copy(
            update={
                "decision": side,
                "entry_zone_min": entry_zone_min,
                "entry_zone_max": entry_zone_max,
                "entry_mode": getattr(watch_plan, "entry_mode", None) or "pullback_confirm",
                "watch_entry_plan": None,
                "invalidation_price": (
                    getattr(watch_plan, "invalidation_price", None)
                    or stop_loss
                    or decision.invalidation_price
                ),
                "max_chase_bps": getattr(watch_plan, "max_chase_bps", None) or decision.max_chase_bps or 4.0,
                "idea_ttl_minutes": getattr(watch_plan, "idea_ttl_minutes", None) or decision.idea_ttl_minutes or 15,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "rationale_codes": rationale_codes,
                "primary_reason_codes": self._unique_reason_codes(
                    [
                        *list(decision.primary_reason_codes or []),
                        *watch_reason_codes,
                        *source_abstain_reason_codes,
                        ENTRY_PLAN_WATCH_REASON_CODE,
                    ]
                ),
                "no_trade_reason_codes": self._unique_reason_codes(
                    [*list(decision.no_trade_reason_codes or []), *source_abstain_reason_codes]
                ),
                "abstain_reason_codes": [],
                "should_abstain": False,
                "explanation_short": f"Watch {side} entry plan armed for recheck at the zone.",
            }
        )

    def _bound_transition_watch_direct_entry(
        self,
        decision: TradeDecision,
    ) -> tuple[TradeDecision, dict[str, object]]:
        if decision.decision not in {"long", "short"}:
            return decision, {}
        side = decision.decision
        entry_zone_min = _safe_float(decision.entry_zone_min, default=0.0)
        entry_zone_max = _safe_float(decision.entry_zone_max, default=0.0)
        reference_price = self._reference_price_from_zone(
            entry_zone_min=entry_zone_min,
            entry_zone_max=entry_zone_max,
            fallback_price=entry_zone_min,
        )
        if (
            entry_zone_min > 0.0
            and entry_zone_max > 0.0
            and self._watch_entry_plan_side_is_consistent(
                side=side,
                reference_price=reference_price,
                stop_loss=decision.stop_loss,
                take_profit=decision.take_profit,
            )
        ):
            reason_codes = self._unique_reason_codes(
                [
                    *list(decision.rationale_codes or []),
                    SOFT_SIGNAL_DIRECT_ENTRY_BOUNDED_TO_WATCH,
                    ENTRY_PLAN_WATCH_REASON_CODE,
                ]
            )
            watch_plan = WatchEntryPlan(
                side=side,
                entry_zone_min=entry_zone_min,
                entry_zone_max=entry_zone_max,
                entry_mode=decision.entry_mode or "pullback_confirm",
                invalidation_price=decision.invalidation_price or decision.stop_loss,
                max_chase_bps=decision.max_chase_bps,
                idea_ttl_minutes=decision.idea_ttl_minutes or 15,
                stop_loss=decision.stop_loss,
                take_profit=decision.take_profit,
                reason_codes=[SOFT_SIGNAL_DIRECT_ENTRY_BOUNDED_TO_WATCH, ENTRY_PLAN_WATCH_REASON_CODE],
            )
            return decision.model_copy(
                update={
                    "decision": "hold",
                    "entry_zone_min": None,
                    "entry_zone_max": None,
                    "entry_mode": "none",
                    "watch_entry_plan": watch_plan,
                    "invalidation_price": None,
                    "max_chase_bps": None,
                    "idea_ttl_minutes": None,
                    "stop_loss": None,
                    "take_profit": None,
                    "rationale_codes": reason_codes,
                    "primary_reason_codes": self._unique_reason_codes(
                        [
                            *list(decision.primary_reason_codes or []),
                            SOFT_SIGNAL_DIRECT_ENTRY_BOUNDED_TO_WATCH,
                            ENTRY_PLAN_WATCH_REASON_CODE,
                        ]
                    ),
                    "no_trade_reason_codes": self._unique_reason_codes(
                        [
                            *list(decision.no_trade_reason_codes or []),
                            SOFT_SIGNAL_DIRECT_ENTRY_BOUNDED_TO_WATCH,
                        ]
                    ),
                    "bounded_output_applied": True,
                    "explanation_short": "Soft signal review can only arm a watch entry plan.",
                }
            ), {
                "watch_only_bounded": True,
                "soft_signal_original_decision": side,
                "soft_signal_bounded_action": "watch_entry_plan",
                "bounded_output_applied": True,
                "fallback_reason_codes": [SOFT_SIGNAL_DIRECT_ENTRY_BOUNDED_TO_WATCH],
            }

        hold_codes = self._unique_reason_codes(
            [
                *list(decision.no_trade_reason_codes or decision.rationale_codes or []),
                SOFT_SIGNAL_DIRECT_ENTRY_BOUNDED_TO_HOLD,
            ]
        )
        return decision.model_copy(
            update={
                "decision": "hold",
                "entry_zone_min": None,
                "entry_zone_max": None,
                "entry_mode": "none",
                "watch_entry_plan": None,
                "invalidation_price": None,
                "max_chase_bps": None,
                "idea_ttl_minutes": None,
                "stop_loss": None,
                "take_profit": None,
                "rationale_codes": self._unique_reason_codes(
                    [*list(decision.rationale_codes or []), SOFT_SIGNAL_DIRECT_ENTRY_BOUNDED_TO_HOLD]
                ),
                "primary_reason_codes": self._unique_reason_codes(
                    [*list(decision.primary_reason_codes or []), SOFT_SIGNAL_DIRECT_ENTRY_BOUNDED_TO_HOLD]
                ),
                "no_trade_reason_codes": hold_codes,
                "bounded_output_applied": True,
                "explanation_short": "Soft signal review cannot execute a direct entry.",
            }
        ), {
            "watch_only_bounded": True,
            "soft_signal_original_decision": side,
            "soft_signal_bounded_action": "hold",
            "bounded_output_applied": True,
            "fallback_reason_codes": [SOFT_SIGNAL_DIRECT_ENTRY_BOUNDED_TO_HOLD],
        }

    @staticmethod
    def _unique_reason_codes(values: list[object]) -> list[str]:
        reason_codes: list[str] = []
        for value in values:
            code = str(value or "").strip()
            if code and code not in reason_codes:
                reason_codes.append(code)
        return reason_codes

    @classmethod
    def _entry_plan_control_block(
        cls,
        operational_status,
    ) -> tuple[bool, list[str]]:
        raw_reason_codes = cls._unique_reason_codes(
            [
                *list(getattr(operational_status, "blocked_reasons", []) or []),
                *list(getattr(operational_status, "blocked_reason_codes", []) or []),
            ]
        )
        reason_codes = list(raw_reason_codes)
        reason_codes = [
            code
            for code in reason_codes
            if code not in ENTRY_PLAN_NON_STRUCTURAL_BLOCKERS
        ]
        guard_reason_code = str(getattr(operational_status, "guard_mode_reason_code", "") or "").strip()
        if (
            guard_reason_code
            and guard_reason_code not in ENTRY_PLAN_SIMULATION_GUARD_REASON_CODES
            and guard_reason_code not in ENTRY_PLAN_NON_STRUCTURAL_BLOCKERS
        ):
            reason_codes = cls._unique_reason_codes([*reason_codes, guard_reason_code])
        rollout_mode = str(getattr(operational_status, "rollout_mode", "") or "")
        if (
            EXCHANGE_CAN_TRADE_UNKNOWN_REASON_CODE in raw_reason_codes
            and rollout_mode == "full_live"
            and bool(getattr(operational_status, "exchange_submit_allowed", False))
        ):
            reason_codes = cls._unique_reason_codes(
                [*reason_codes, EXCHANGE_CAN_TRADE_UNKNOWN_REASON_CODE]
            )
        if bool(getattr(operational_status, "can_enter_new_position", False)):
            return False, reason_codes

        exchange_unknown_only = (
            EXCHANGE_CAN_TRADE_UNKNOWN_REASON_CODE in raw_reason_codes
            and not reason_codes
            and rollout_mode in ENTRY_PLAN_SIMULATION_ROLLOUT_MODES
            and bool(getattr(operational_status, "live_execution_ready", False))
            and not bool(getattr(operational_status, "trading_paused", False))
            and str(getattr(operational_status, "operating_state", "") or "") == "TRADABLE"
            and not bool(getattr(operational_status, "exchange_submit_allowed", False))
        )
        if exchange_unknown_only:
            return False, []

        simulation_without_submit = (
            rollout_mode in ENTRY_PLAN_SIMULATION_ROLLOUT_MODES
            and not bool(getattr(operational_status, "exchange_submit_allowed", False))
            and bool(getattr(operational_status, "live_execution_ready", False))
            and not bool(getattr(operational_status, "trading_paused", False))
            and str(getattr(operational_status, "operating_state", "") or "") == "TRADABLE"
            and not reason_codes
        )
        if simulation_without_submit:
            return False, []

        if not reason_codes:
            operating_state = str(getattr(operational_status, "operating_state", "") or "")
            if bool(getattr(operational_status, "trading_paused", False)):
                reason_codes.append("TRADING_PAUSED")
            elif not bool(getattr(operational_status, "live_execution_ready", False)):
                reason_codes.append(guard_reason_code or "LIVE_EXECUTION_NOT_READY")
            elif operating_state and operating_state != "TRADABLE":
                reason_codes.append(operating_state)
            elif not bool(getattr(operational_status, "exchange_submit_allowed", False)):
                reason_codes.append(guard_reason_code or "EXCHANGE_SUBMIT_DISABLED")
            else:
                reason_codes.append("ENTRY_CONTROL_BLOCKED")
        return True, reason_codes

    def _arm_pending_entry_plan(
        self,
        *,
        decision,
        decision_run: AgentRun,
        risk_result,
        risk_row_id: int | None,
        feature_payload,
        cycle_id: str,
        snapshot_id: int,
        replace_reason: str = "REPLACED_BY_NEW_APPROVED_PLAN",
    ) -> PendingEntryPlan:
        symbol = decision.symbol.upper()
        side = str(decision.decision)
        expires_at = pending_entry_expires_at(decision_run.created_at, decision.idea_ttl_minutes)
        idempotency_key = self._pending_entry_plan_idempotency_key(
            symbol=symbol,
            side=side,
            source_decision_run_id=decision_run.id,
            expires_at=expires_at,
        )
        decision_correlation_ids = normalize_correlation_ids(
            cycle_id=cycle_id,
            snapshot_id=snapshot_id,
            decision_id=decision_run.id,
        )
        for existing in self._active_pending_entry_plans(symbol=symbol):
            cancel_reason = "NEW_AI_HOLD_DECISION"
            if existing.side == side:
                cancel_reason = replace_reason
            elif existing.side != side:
                cancel_reason = "OPPOSITE_AI_PLAN_REPLACED"
            self._cancel_pending_entry_plan(
                existing,
                reason=cancel_reason,
                detail={"replacement_decision_run_id": decision_run.id, "replacement_side": side},
                correlation_ids=decision_correlation_ids,
            )
        source_decision_metadata = _as_dict(getattr(decision_run, "metadata_json", None))
        source_decision_output = _as_dict(getattr(decision_run, "output_payload", None))
        decision_scene_review = getattr(decision, "psychology_scene_review", None)
        psychology_scene_review = (
            dict(decision_scene_review.model_dump(mode="json"))
            if hasattr(decision_scene_review, "model_dump")
            else (
                _as_dict(source_decision_metadata.get("psychology_scene_review"))
                or _as_dict(source_decision_output.get("psychology_scene_review"))
                or None
            )
        )
        trade_performance_tags = _as_dict(source_decision_metadata.get("trade_performance_tags"))
        strategy_id = _safe_str(trade_performance_tags.get("strategy_id"))
        if strategy_id in {"unspecified", "unspecified_engine"}:
            strategy_id = ""
        if not strategy_id:
            strategy_id = _strategy_engine_name_from_payload(source_decision_metadata, decision.model_dump(mode="json"))
        regime_id = (
            _safe_str(trade_performance_tags.get("regime_id"))
            or _safe_str(getattr(feature_payload.regime, "primary_regime", None))
            or "unknown"
        )
        entry_confirmation_type = (
            _safe_str(trade_performance_tags.get("entry_confirmation_type"))
            or _entry_confirmation_type_for_tags(
                strategy_engine=strategy_id,
                entry_mode=str(decision.entry_mode or "none"),
            )
        )
        plan = PendingEntryPlan(
            symbol=symbol,
            side=side,
            plan_status=ACTIVE_ENTRY_PLAN_STATUS,
            source_decision_run_id=decision_run.id,
            regime=feature_payload.regime.primary_regime,
            posture=self._entry_plan_posture(decision_side=side, feature_payload=feature_payload),
            rationale_codes=list(dict.fromkeys(decision.rationale_codes)),
            source_timeframe=decision.timeframe,
            entry_mode=decision.entry_mode or "pullback_confirm",
            entry_zone_min=float(decision.entry_zone_min or 0.0),
            entry_zone_max=float(decision.entry_zone_max or 0.0),
            invalidation_price=decision.invalidation_price,
            max_chase_bps=decision.max_chase_bps,
            idea_ttl_minutes=decision.idea_ttl_minutes,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
            risk_pct_cap=(
                float(risk_result.approved_risk_pct)
                if float(risk_result.approved_risk_pct or 0.0) > 0.0
                else float(decision.risk_pct)
            ),
            leverage_cap=(
                float(risk_result.approved_leverage)
                if float(risk_result.approved_leverage or 0.0) > 0.0
                else float(decision.leverage)
            ),
            expires_at=expires_at,
            idempotency_key=idempotency_key,
            metadata_json={
                "cycle_id": cycle_id,
                "snapshot_id": snapshot_id,
                "source_risk_check_id": risk_row_id,
                "source_blocked_reason_codes": list(getattr(risk_result, "blocked_reason_codes", [])),
                "source_adjustment_reason_codes": list(getattr(risk_result, "adjustment_reason_codes", [])),
                "source_no_trade_reason_codes": list(getattr(decision, "no_trade_reason_codes", []) or []),
                "source_primary_reason_codes": list(getattr(decision, "primary_reason_codes", []) or []),
                "strategy_id": strategy_id,
                "regime_id": regime_id,
                "entry_confirmation_type": entry_confirmation_type,
                "trade_performance_tags": {
                    **trade_performance_tags,
                    "strategy_id": strategy_id,
                    "regime_id": regime_id,
                    "entry_confirmation_type": entry_confirmation_type,
                },
                "holding_profile": str(getattr(decision, "holding_profile", "scalp") or "scalp"),
                "holding_profile_reason": str(getattr(decision, "holding_profile_reason", "") or "") or None,
                "psychology_scene_review": psychology_scene_review,
                "trigger_details": {
                    "volume_profile": self._pending_plan_volume_profile_details(feature_payload, decision),
                },
            },
        )
        self.session.add(plan)
        self.session.flush()
        metadata = self._pending_entry_plan_metadata(plan)
        metadata["plan_id"] = plan.id
        plan.metadata_json = metadata
        self.session.add(plan)
        self.session.flush()
        record_audit_event(
            self.session,
            event_type="pending_entry_plan_armed",
            entity_type="pending_entry_plan",
            entity_id=str(plan.id),
            severity="info",
            message="A pending entry plan was armed from the latest AI decision.",
            payload={
                "plan_id": plan.id,
                "symbol": plan.symbol,
                "side": plan.side,
                "strategy_id": strategy_id,
                "regime_id": regime_id,
                "entry_confirmation_type": entry_confirmation_type,
                "source_decision_run_id": plan.source_decision_run_id,
                "entry_mode": plan.entry_mode,
                "holding_profile": plan.metadata_json.get("holding_profile") if isinstance(plan.metadata_json, dict) else "scalp",
                "entry_zone_min": plan.entry_zone_min,
                "entry_zone_max": plan.entry_zone_max,
                "expires_at": plan.expires_at.isoformat(),
                "idempotency_key": plan.idempotency_key,
                **({"psychology_scene_review": psychology_scene_review} if psychology_scene_review else {}),
            },
            correlation_ids=decision_correlation_ids,
        )
        self._record_decision_funnel_audit(
            cycle_id=cycle_id,
            symbol=plan.symbol,
            timeframe=plan.source_timeframe,
            regime=plan.regime,
            stage="entry_plan_armed",
            status="waiting",
            strategy_candidate=self._pending_plan_funnel_candidate(plan),
            entry_plan_status=plan.plan_status,
            m1_confirmation_status="waiting",
            final_risk_status="not_checked",
            blocked_reason_codes=list(getattr(risk_result, "blocked_reason_codes", []) or []),
            detail={
                "plan_id": plan.id,
                "source_decision_run_id": plan.source_decision_run_id,
                "source_risk_check_id": risk_row_id,
                "expires_at": plan.expires_at.isoformat(),
            },
            severity="info",
            entity_type="pending_entry_plan",
            entity_id=str(plan.id),
            correlation_ids=decision_correlation_ids,
        )
        refresh_decision_performance_fact_usefulness(self.session, decision_run.id)
        return plan

    def _cancel_symbol_entry_plans_from_decision(
        self,
        *,
        symbol: str,
        decision: TradeDecision,
        decision_run_id: int | None,
        cycle_id: str,
        snapshot_id: int,
    ) -> list[PendingEntryPlanSnapshot]:
        decision_side = str(decision.decision)
        decision_correlation_ids = normalize_correlation_ids(
            cycle_id=cycle_id,
            snapshot_id=snapshot_id,
            decision_id=decision_run_id,
        )
        canceled: list[PendingEntryPlanSnapshot] = []
        for plan in self._active_pending_entry_plans(symbol=symbol):
            should_cancel = plan.side != decision_side
            if decision_side == "hold":
                should_cancel = self._hold_decision_invalidates_plan(decision, plan)
            if not should_cancel:
                metadata = self._pending_entry_plan_metadata(plan)
                metadata["last_ai_hold_review_at"] = utcnow_naive().isoformat()
                metadata["last_ai_hold_decision_run_id"] = decision_run_id
                metadata["last_ai_hold_reason_codes"] = sorted(self._decision_reason_code_set(decision))
                plan.metadata_json = metadata
                self.session.add(plan)
                self.session.flush()
                continue
            reason = "NEW_AI_HOLD_DECISION" if decision_side == "hold" else "OPPOSITE_AI_PLAN_REPLACED"
            canceled.append(
                self._pending_entry_plan_snapshot(
                    self._cancel_pending_entry_plan(
                        plan,
                        reason=reason,
                        detail={"decision_side": decision_side},
                        correlation_ids=decision_correlation_ids,
                    )
                )
            )
        return canceled

    @staticmethod
    def _plan_zone_interacted(plan: PendingEntryPlan, candle) -> bool:
        return candle.low <= plan.entry_zone_max and candle.high >= plan.entry_zone_min

    @staticmethod
    def _plan_chase_bps(plan: PendingEntryPlan, latest_price: float) -> float:
        if plan.side == "long":
            anchor = max(plan.entry_zone_max, plan.entry_zone_min, 1.0)
            return max(((latest_price - anchor) / anchor) * 10_000, 0.0)
        anchor = max(min(plan.entry_zone_min, plan.entry_zone_max), 1.0)
        return max(((anchor - latest_price) / anchor) * 10_000, 0.0)

    @staticmethod
    def _build_plan_confirm_detail(plan: PendingEntryPlan, market_snapshot: MarketSnapshotPayload) -> dict[str, object]:
        candles = market_snapshot.candles
        last_candle = candles[-1] if candles else None
        previous_candle = candles[-2] if len(candles) >= 2 else None
        quality_threshold = 0.72 if plan.entry_mode == "breakout_confirm" else 0.66 if "continuation" in str(plan.posture or "") else 0.62
        if last_candle is None:
            return {
                "zone_entered": False,
                "confirm_met": False,
                "reason": "NO_1M_CANDLE",
                "quality_score": 0.0,
                "quality_threshold": quality_threshold,
                "quality_state": "waiting",
                "cancel_recommended": False,
                "quality_components": {},
            }
        zone_entered = TradingOrchestrator._plan_zone_interacted(plan, last_candle)
        candle_range = max(last_candle.high - last_candle.low, 1e-9)
        candle_body_ratio = abs(last_candle.close - last_candle.open) / candle_range
        lower_wick_ratio = max(min(last_candle.open, last_candle.close) - last_candle.low, 0.0) / candle_range
        upper_wick_ratio = max(last_candle.high - max(last_candle.open, last_candle.close), 0.0) / candle_range
        if plan.side == "long":
            close_reclaimed = last_candle.close >= plan.entry_zone_max
            structure_break = previous_candle is not None and last_candle.close > previous_candle.high
            wick_reclaim = lower_wick_ratio >= 0.35 and last_candle.close >= (last_candle.low + candle_range * 0.6)
            wick_imbalance = lower_wick_ratio - upper_wick_ratio
            entry_anchor = max(plan.entry_zone_min, plan.entry_zone_max)
        else:
            close_reclaimed = last_candle.close <= plan.entry_zone_min
            structure_break = previous_candle is not None and last_candle.close < previous_candle.low
            wick_reclaim = upper_wick_ratio >= 0.35 and last_candle.close <= (last_candle.high - candle_range * 0.6)
            wick_imbalance = upper_wick_ratio - lower_wick_ratio
            entry_anchor = min(plan.entry_zone_min, plan.entry_zone_max)
        reclaim_signal_score = min(
            (0.55 if close_reclaimed else 0.0)
            + (0.3 if structure_break else 0.0)
            + (0.2 if wick_reclaim else 0.0),
            1.0,
        )
        body_quality = min(max((candle_body_ratio - 0.18) / 0.42, 0.0), 1.0)
        wick_quality = min(max((wick_imbalance + 0.05) / 0.45, 0.0), 1.0)
        observed_chase_bps = TradingOrchestrator._plan_chase_bps(plan, market_snapshot.latest_price)
        if plan.max_chase_bps is not None and plan.max_chase_bps > 0:
            chase_ratio = observed_chase_bps / plan.max_chase_bps
            late_chase = chase_ratio >= 0.8
            severe_late_chase = chase_ratio >= 1.35
            if chase_ratio <= 0.5:
                chase_quality = 1.0
            elif chase_ratio <= 0.8:
                chase_quality = 0.85
            elif chase_ratio <= 1.0:
                chase_quality = 0.55
            elif chase_ratio <= 1.2:
                chase_quality = 0.25
            else:
                chase_quality = 0.0
        else:
            chase_ratio = None
            late_chase = False
            severe_late_chase = False
            chase_quality = 1.0
        baseline_expected_rr = None
        current_expected_rr = None
        expected_rr_deterioration_pct = None
        if plan.invalidation_price is not None and plan.take_profit is not None:
            if plan.side == "long":
                baseline_risk = entry_anchor - plan.invalidation_price
                baseline_reward = plan.take_profit - entry_anchor
                current_risk = market_snapshot.latest_price - plan.invalidation_price
                current_reward = plan.take_profit - market_snapshot.latest_price
            else:
                baseline_risk = plan.invalidation_price - entry_anchor
                baseline_reward = entry_anchor - plan.take_profit
                current_risk = plan.invalidation_price - market_snapshot.latest_price
                current_reward = market_snapshot.latest_price - plan.take_profit
            if baseline_risk > 0 and baseline_reward > 0 and current_risk > 0 and current_reward > 0:
                baseline_expected_rr = baseline_reward / baseline_risk
                current_expected_rr = current_reward / current_risk
                expected_rr_deterioration_pct = min(
                    max((baseline_expected_rr - current_expected_rr) / max(baseline_expected_rr, 1e-9), 0.0),
                    1.0,
                )
        if current_expected_rr is None:
            rr_quality = 0.5
        elif current_expected_rr >= 1.8:
            rr_quality = 1.0
        elif current_expected_rr >= 1.4:
            rr_quality = 0.8
        elif current_expected_rr >= 1.1:
            rr_quality = 0.6
        elif current_expected_rr >= 0.9:
            rr_quality = 0.35
        else:
            rr_quality = 0.1
        if expected_rr_deterioration_pct is not None:
            rr_quality *= max(0.0, 1.0 - expected_rr_deterioration_pct * 0.7)
        quality_components = {
            "zone_entered": 1.0 if zone_entered else 0.0,
            "reclaim_signal_strength": round(reclaim_signal_score, 4),
            "candle_body_quality": round(body_quality, 4),
            "wick_imbalance_quality": round(wick_quality, 4),
            "late_chase_quality": round(chase_quality, 4),
            "expected_rr_quality": round(rr_quality, 4),
        }
        quality_score = 0.0
        if zone_entered:
            quality_score = (
                reclaim_signal_score * 0.34
                + body_quality * 0.18
                + wick_quality * 0.16
                + chase_quality * 0.16
                + rr_quality * 0.16
            )
            if close_reclaimed and structure_break:
                quality_score += 0.04
            quality_score = min(max(quality_score, 0.0), 1.0)
        rr_collapse = bool(
            current_expected_rr is not None
            and current_expected_rr < 0.85
            and (expected_rr_deterioration_pct or 0.0) >= 0.5
        )
        late_chase_rr_failure = bool(
            late_chase
            and current_expected_rr is not None
            and current_expected_rr < 0.85
        )
        cancel_recommended = bool(
            zone_entered
            and not (
                reclaim_signal_score >= 0.55
                and quality_score >= quality_threshold
            )
            and (rr_collapse or late_chase_rr_failure)
        )
        confirm_met = bool(
            zone_entered
            and reclaim_signal_score >= 0.55
            and quality_score >= quality_threshold
        )
        if confirm_met:
            quality_state = "trigger"
            reason = "QUALITY_CONFIRMED"
        elif cancel_recommended:
            quality_state = "cancel"
            reason = (
                "QUALITY_REJECTED_LATE_CHASE"
                if late_chase_rr_failure
                else "QUALITY_REJECTED_RR_DETERIORATED"
            )
        elif not zone_entered:
            quality_state = "waiting"
            reason = "ZONE_NOT_ENTERED"
        else:
            quality_state = "waiting"
            reason = "QUALITY_BELOW_THRESHOLD"
        return {
            "zone_entered": zone_entered,
            "confirm_met": confirm_met,
            "quality_score": round(quality_score, 4),
            "quality_threshold": quality_threshold,
            "quality_state": quality_state,
            "cancel_recommended": cancel_recommended,
            "quality_components": quality_components,
            "reason": reason,
            "close_reclaimed": close_reclaimed,
            "structure_break": structure_break,
            "wick_reclaim": wick_reclaim,
            "candle_body_ratio": round(candle_body_ratio, 4),
            "wick_imbalance": round(wick_imbalance, 4),
            "lower_wick_ratio": round(lower_wick_ratio, 4),
            "upper_wick_ratio": round(upper_wick_ratio, 4),
            "late_chase": late_chase,
            "severe_late_chase": severe_late_chase,
            "rr_collapse": rr_collapse,
            "late_chase_rr_failure": late_chase_rr_failure,
            "late_chase_ratio": round(chase_ratio, 4) if chase_ratio is not None else None,
            "observed_chase_bps": round(observed_chase_bps, 6),
            "baseline_expected_rr": round(baseline_expected_rr, 4) if baseline_expected_rr is not None else None,
            "current_expected_rr": round(current_expected_rr, 4) if current_expected_rr is not None else None,
            "expected_rr_deterioration_pct": (
                round(expected_rr_deterioration_pct, 4) if expected_rr_deterioration_pct is not None else None
            ),
            "last_candle": {
                "timestamp": last_candle.timestamp.isoformat(),
                "open": last_candle.open,
                "high": last_candle.high,
                "low": last_candle.low,
                "close": last_candle.close,
            },
            "previous_candle": (
                {
                    "timestamp": previous_candle.timestamp.isoformat(),
                    "open": previous_candle.open,
                    "high": previous_candle.high,
                    "low": previous_candle.low,
                    "close": previous_candle.close,
                }
                if previous_candle is not None
                else None
            ),
        }

    @staticmethod
    def _plan_invalidation_broken(plan: PendingEntryPlan, market_snapshot: MarketSnapshotPayload) -> bool:
        if plan.invalidation_price is None or not market_snapshot.candles:
            return False
        last_candle = market_snapshot.candles[-1]
        if plan.side == "long":
            return market_snapshot.latest_price <= plan.invalidation_price or last_candle.low <= plan.invalidation_price
        return market_snapshot.latest_price >= plan.invalidation_price or last_candle.high >= plan.invalidation_price

    @staticmethod
    def _trigger_execution_decision_from_plan(
        plan: PendingEntryPlan,
        market_snapshot: MarketSnapshotPayload,
        source_decision: TradeDecision,
    ) -> TradeDecision:
        latest_price = market_snapshot.latest_price
        return source_decision.model_copy(
            update={
                "entry_mode": "immediate",
                "entry_zone_min": latest_price,
                "entry_zone_max": latest_price,
                "risk_pct": plan.risk_pct_cap if plan.risk_pct_cap > 0 else source_decision.risk_pct,
                "leverage": plan.leverage_cap if plan.leverage_cap > 0 else source_decision.leverage,
                "rationale_codes": list(dict.fromkeys([*source_decision.rationale_codes, "PENDING_ENTRY_PLAN_TRIGGERED"])),
            }
        )

    @staticmethod
    def _entry_plan_ai_recheck_cooldown_remaining_seconds(
        *,
        metadata: dict[str, object],
        generated_at: datetime,
    ) -> int:
        last_recheck_at = _coerce_datetime(metadata.get("last_ai_recheck_at"))
        if last_recheck_at is None:
            return 0
        elapsed_seconds = int((generated_at - last_recheck_at).total_seconds())
        return max(ENTRY_PLAN_AI_RECHECK_COOLDOWN_SECONDS - elapsed_seconds, 0)

    def _record_entry_plan_ai_recheck_skip(
        self,
        *,
        plan: PendingEntryPlan,
        reason: str,
        generated_at: datetime,
        retry_after_seconds: int = 0,
        gate_payload: dict[str, object] | None = None,
        hard_skip_ai: bool = False,
        skip_category: str | None = None,
        hard_skip_reason_codes: list[str] | None = None,
        correlation_ids: dict[str, object] | None = None,
    ) -> dict[str, object]:
        metadata = self._pending_entry_plan_metadata(plan)
        metadata["last_ai_recheck_skip_at"] = generated_at.isoformat()
        metadata["last_ai_recheck_skip_reason"] = reason
        if retry_after_seconds > 0:
            metadata["last_ai_recheck_retry_after_seconds"] = retry_after_seconds
        plan.metadata_json = metadata
        self.session.add(plan)
        self.session.flush()
        payload = {
            "ai_call_event": AI_CALL_EVENT_SKIPPED,
            "symbol": plan.symbol,
            "scope": "entry_plan_recheck",
            "reason": reason,
            "hard_skip_ai": hard_skip_ai,
            "skip_category": skip_category,
            "hard_skip_reason_codes": list(
                hard_skip_reason_codes
                if hard_skip_reason_codes is not None
                else [reason] if hard_skip_ai else []
            ),
            "plan_id": plan.id,
            "source_decision_run_id": plan.source_decision_run_id,
            "retry_after_seconds": retry_after_seconds,
            "gate": dict(gate_payload or {}),
        }
        record_audit_event(
            self.session,
            event_type="decision_ai_skipped",
            entity_type="pending_entry_plan",
            entity_id=str(plan.id),
            severity="info",
            message="Pending entry plan AI recheck was skipped before execution.",
            payload=payload,
            correlation_ids=correlation_ids,
        )
        return {
            "status": "skipped",
            "skip_reason": reason,
            "retry_after_seconds": retry_after_seconds,
            "ai_call_policy": payload,
        }

    @staticmethod
    def _entry_plan_min_actionable_notional(reference_price: float | None) -> float:
        return round(
            max(
                ENTRY_PLAN_MIN_ACTIONABLE_NOTIONAL_FLOOR,
                max(_safe_float(reference_price, default=0.0) or 0.0, 1.0) * 0.0005,
            ),
            6,
        )

    @classmethod
    def _entry_plan_no_additional_capacity_detail(
        cls,
        *,
        plan: PendingEntryPlan,
        open_positions: list[Position],
        risk_budget: dict[str, object],
        latest_price: float | None,
    ) -> dict[str, object] | None:
        if not open_positions:
            return None
        side = str(plan.side or "").strip().lower()
        if side not in {"long", "short"}:
            return None
        side_headroom = max(
            _safe_float(risk_budget.get(f"max_additional_{side}_notional"), default=0.0) or 0.0,
            0.0,
        )
        single_position_headroom = max(
            _safe_float(risk_budget.get("single_position_headroom"), default=0.0) or 0.0,
            0.0,
        )
        available_notional = min(side_headroom, single_position_headroom)
        minimum_actionable_notional = cls._entry_plan_min_actionable_notional(latest_price)
        if available_notional >= minimum_actionable_notional:
            return None
        return {
            "reason": ENTRY_PLAN_NO_CAPACITY_CANCEL_REASON_CODE,
            "plan_side": side,
            "available_additional_notional": round(available_notional, 6),
            "side_headroom": round(side_headroom, 6),
            "single_position_headroom": round(single_position_headroom, 6),
            "minimum_actionable_notional": minimum_actionable_notional,
            "risk_budget": dict(risk_budget),
            "open_position_ids": [
                position.id for position in open_positions if getattr(position, "id", None) is not None
            ],
            "open_position_sides": [
                str(getattr(position, "side", "") or "").lower()
                for position in open_positions
                if str(getattr(position, "side", "") or "").lower()
            ],
        }

    def _run_entry_plan_ai_recheck(
        self,
        *,
        plan: PendingEntryPlan,
        source_decision_run: AgentRun,
        market_snapshot: MarketSnapshotPayload,
        market_row: MarketSnapshot,
        runtime_state: dict[str, object],
        open_positions: list[Position],
        cadence_profile: dict[str, object],
        confirm_detail: dict[str, object],
        trigger_details: dict[str, object],
        generated_at: datetime,
    ) -> dict[str, object]:
        metadata = self._pending_entry_plan_metadata(plan)
        recheck_cycle_id = f"entry-plan-recheck:{plan.id}:{market_row.id}"
        correlation_ids = normalize_correlation_ids(
            cycle_id=recheck_cycle_id,
            snapshot_id=market_row.id,
            decision_id=plan.source_decision_run_id,
        )
        cooldown_remaining = self._entry_plan_ai_recheck_cooldown_remaining_seconds(
            metadata=metadata,
            generated_at=generated_at,
        )
        if cooldown_remaining > 0:
            return self._record_entry_plan_ai_recheck_skip(
                plan=plan,
                reason="PLAN_AI_RECHECK_COOLDOWN",
                generated_at=generated_at,
                retry_after_seconds=cooldown_remaining,
                correlation_ids=correlation_ids,
            )

        openai_gate = get_openai_call_gate(
            self.session,
            self.settings_row,
            AgentRole.TRADING_DECISION.value,
            ENTRY_PLAN_AI_RECHECK_TRIGGER_EVENT,
            has_openai_key=bool(self.credentials.openai_api_key),
            symbol=plan.symbol,
            cooldown_minutes_override=0,
            manual_guard_minutes_override=0,
            enforce_waste_guard=True,
        )
        gate_payload = openai_gate.as_metadata()
        if not openai_gate.allowed:
            return self._record_entry_plan_ai_recheck_skip(
                plan=plan,
                reason=str(openai_gate.reason or "openai_gate_blocked"),
                generated_at=generated_at,
                retry_after_seconds=int(openai_gate.retry_after_seconds or 0),
                gate_payload=gate_payload,
                correlation_ids=correlation_ids,
            )

        source_output = (
            dict(source_decision_run.output_payload)
            if isinstance(source_decision_run.output_payload, dict)
            else {}
        )
        source_metadata = (
            dict(source_decision_run.metadata_json)
            if isinstance(source_decision_run.metadata_json, dict)
            else {}
        )
        source_input = (
            dict(source_decision_run.input_payload)
            if isinstance(source_decision_run.input_payload, dict)
            else {}
        )
        strategy_engine_name = _strategy_engine_name_from_payload(source_metadata, source_output)
        holding_profile = (
            str(source_metadata.get("holding_profile") or "")
            or str(source_output.get("holding_profile") or "")
            or "scalp"
        )
        plan_snapshot = self._pending_entry_plan_snapshot(plan).model_dump(mode="json")
        recheck_context = {
            "plan_id": plan.id,
            "source_decision_run_id": plan.source_decision_run_id,
            "source_decision_created_at": source_decision_run.created_at.isoformat(),
            "plan_side": plan.side,
            "plan_status": plan.plan_status,
            "entry_zone_min": plan.entry_zone_min,
            "entry_zone_max": plan.entry_zone_max,
            "entry_mode": plan.entry_mode,
            "latest_price": market_snapshot.latest_price,
            "market_snapshot_id": market_row.id,
            "market_snapshot_time": market_snapshot.snapshot_time.isoformat(),
            "confirm_detail": dict(confirm_detail),
            "trigger_details": dict(trigger_details),
        }
        selection_context = {
            "strategy_engine": strategy_engine_name,
            "holding_profile": holding_profile,
            "selection_reason": "pending_entry_plan_zone_touch",
            "candidate_weight": 1.0,
            "entry_mode": plan.entry_mode,
            "candidate": {
                "decision": plan.side,
                "symbol": plan.symbol,
                "timeframe": plan.source_timeframe or market_snapshot.timeframe,
                "scenario": "pending_entry_plan_recheck",
                "entry_zone_min": plan.entry_zone_min,
                "entry_zone_max": plan.entry_zone_max,
                "stop_loss": plan.stop_loss,
                "take_profit": plan.take_profit,
                "risk_pct": plan.risk_pct_cap,
                "leverage": plan.leverage_cap,
                "rationale_codes": list(
                    dict.fromkeys([*list(plan.rationale_codes or []), *ENTRY_PLAN_AI_RECHECK_REASON_CODES])
                ),
            },
            "score": {
                "total_score": _safe_float(confirm_detail.get("quality_score"), default=0.0),
                "entry_plan_confirm_quality": _safe_float(confirm_detail.get("quality_score"), default=0.0),
            },
            "pending_entry_plan": plan_snapshot,
            "entry_plan_recheck": recheck_context,
        }
        reason_codes = list(
            dict.fromkeys([*list(plan.rationale_codes or []), *ENTRY_PLAN_AI_RECHECK_REASON_CODES])
        )
        review_trigger_payload = self._build_review_trigger_payload(
            trigger_reason="entry_candidate_event",
            symbol=plan.symbol,
            timeframe=market_snapshot.timeframe,
            strategy_engine=strategy_engine_name or None,
            holding_profile=holding_profile,
            assigned_slot=None,
            candidate_weight=1.0,
            reason_codes=reason_codes,
            last_decision_at=source_decision_run.created_at,
            last_material_review_at=_coerce_datetime(source_metadata.get("last_material_review_at")),
            triggered_at=generated_at,
            forced_review_reason="entry_plan_zone_touch",
            fingerprint_basis={
                "plan_id": plan.id,
                "source_decision_run_id": plan.source_decision_run_id,
                "market_snapshot_id": market_row.id,
                "latest_price": round(_safe_float(market_snapshot.latest_price), 6),
                "confirm_reason": confirm_detail.get("reason"),
            },
            fingerprint_changed_fields=["entry_plan_zone_touched"],
            fingerprint_material={
                "trigger_reason": "entry_candidate_event",
                "forced_review_reason": "entry_plan_zone_touch",
                "plan_id": plan.id,
                "source_decision_run_id": plan.source_decision_run_id,
                "symbol": plan.symbol,
                "timeframe": market_snapshot.timeframe,
                "side": plan.side,
                "market_snapshot_id": market_row.id,
                "latest_price": round(_safe_float(market_snapshot.latest_price), 6),
                "quality_score": round(_safe_float(confirm_detail.get("quality_score"), default=0.0), 6),
                "reason_codes": sorted(reason_codes),
            },
        )
        feature_payload = compute_features(market_snapshot, {})
        feature_row = persist_feature_snapshot(self.session, market_row.id, market_snapshot, feature_payload)
        latest_pnl = get_latest_pnl_snapshot(self.session, self.settings_row)
        effective_leverage_cap = min(
            self.settings_row.max_leverage,
            HARD_MAX_GLOBAL_LEVERAGE,
            get_symbol_leverage_cap(plan.symbol),
        )
        drawdown_state = self._sync_drawdown_state(now=generated_at)
        position_management_context = build_position_management_context(
            open_positions[0] if open_positions else None,
            feature_payload=feature_payload,
            settings_row=self.settings_row,
        )
        risk_budget_context = build_ai_risk_budget_context(
            self.session,
            self.settings_row,
            decision_symbol=plan.symbol,
            equity=latest_pnl.equity,
        )
        active_entry_plans = self._active_pending_entry_plans(symbol=plan.symbol)
        risk_context = {
            "max_risk_per_trade": min(self.settings_row.max_risk_per_trade, HARD_MAX_RISK_PER_TRADE),
            "max_leverage": effective_leverage_cap,
            "symbol_risk_tier": get_symbol_risk_tier(plan.symbol),
            "daily_pnl": latest_pnl.daily_pnl,
            "consecutive_losses": latest_pnl.consecutive_losses,
            "drawdown_state": drawdown_state,
            "operating_state": runtime_state["operating_state"],
            "protection_recovery_status": runtime_state["protection_recovery_status"],
            "missing_protection_symbols": runtime_state["missing_protection_symbols"],
            "missing_protection_items": runtime_state["missing_protection_items"],
            "risk_budget": risk_budget_context,
            "position_management_context": position_management_context,
            "selection_context": selection_context,
        }
        risk_context["active_position_summary"] = self._active_position_ai_summary(
            open_positions=open_positions,
            position_management_context=position_management_context,
        )
        risk_context["pending_entry_plan_summary"] = self._pending_entry_plan_ai_summary(
            symbol=plan.symbol,
            active_plans=active_entry_plans,
            latest_price=self._summary_float(market_snapshot.latest_price),
            risk_budget=risk_budget_context,
            open_positions=open_positions,
            now=generated_at,
        )
        risk_context["execution_constraints_summary"] = self._execution_constraints_ai_summary(
            symbol=plan.symbol,
            market_snapshot=market_snapshot,
            risk_context=risk_context,
            settings_row=self.settings_row,
            open_positions=open_positions,
        )
        decision_reference = self._build_decision_reference_payload(
            symbol=plan.symbol,
            timeframe=market_snapshot.timeframe,
            market_snapshot=market_snapshot,
            market_row=market_row,
            runtime_state=runtime_state,
        )
        recent_tp_direction = self._recent_tp_direction_from_pending_entry_plan(
            plan,
            selection_context,
        )
        ai_risk_context = self._risk_context_with_recent_tp_close_summary(
            risk_context,
            symbol=plan.symbol,
            direction=recent_tp_direction,
            generated_at=generated_at,
            lookback_minutes=self._recent_tp_close_lookback_minutes(
                cadence_profile=cadence_profile,
                pending_entry_plan=plan,
            ),
        )
        ai_context = build_ai_decision_context(
            market_snapshot=market_snapshot,
            features=feature_payload,
            risk_context=ai_risk_context,
            selection_context=selection_context,
            review_trigger=review_trigger_payload,
            decision_reference=decision_reference,
            previous_decision_output=source_output,
            previous_decision_metadata=source_metadata,
            previous_input_payload=source_input,
            previous_ai_invoked_at=_coerce_datetime(source_metadata.get("last_ai_invoked_at")),
        )
        prior_read_debug: dict[str, object] = {}
        ai_prior_context = build_ai_prior_context(
            self.session,
            ai_context=ai_context,
            selection_context=selection_context,
            feature_payload=feature_payload,
            debug_collector=prior_read_debug,
        )
        ai_context = ai_context.model_copy(update={"prior_context": ai_prior_context})
        ai_context_payload = ai_context.model_dump(mode="json")
        ai_call_policy = {
            "ai_call_event": AI_CALL_EVENT_ALLOWED,
            "ai_call_allowed": True,
            "skip_ai": False,
            "reason": None,
            "scope": "entry_plan_recheck",
            "hard_skip_ai": False,
            "skip_category": None,
            "hard_skip_reason_codes": [],
            "allow_ai_but_later_risk_check": ["PENDING_ENTRY_PLAN_FINAL_RISK_CHECK"],
        }
        decision, provider_name, decision_metadata = self.trading_agent.run(
            market_snapshot,
            feature_payload,
            open_positions,
            ai_risk_context,
            use_ai=True,
            max_input_candles=self.settings_row.ai_max_input_candles,
            logic_variant="improved",
            ai_context=ai_context,
        )
        decision_generated_at = utcnow_naive()
        resolved_last_ai_invoked_at = (
            decision_generated_at
            if str(decision_metadata.get("source") or "") == "llm"
            else _coerce_datetime(source_metadata.get("last_ai_invoked_at"))
        )
        decision_metadata = {
            **decision_metadata,
            "gate": gate_payload,
            "logic_variant": "entry_plan_recheck",
            "symbol": plan.symbol,
            "timeframe": market_snapshot.timeframe,
            "ai_provider": self.settings_row.ai_provider,
            "ai_model": self.settings_row.ai_model,
            "holding_profile": getattr(decision, "holding_profile", "scalp"),
            "holding_profile_reason": getattr(decision, "holding_profile_reason", None),
            "cadence": cadence_profile,
            "ai_skipped_reason": None,
            "pre_ai_skip_reason": None,
            "ai_call_policy": ai_call_policy,
            "ai_call_event": AI_CALL_EVENT_ALLOWED,
            "hard_skip_ai": False,
            "hard_skip_ai_reason": None,
            "allow_ai_but_later_risk_check": list(
                ai_call_policy["allow_ai_but_later_risk_check"]
            ),
            "analysis_context": _decision_analysis_context(feature_payload),
            "selection_context": selection_context,
            "ai_context": ai_context_payload,
            "ai_context_version": ai_context.ai_context_version,
            "event_risk_active": ai_context.event_risk_active,
            "event_risk_reason_codes": list(ai_context.event_risk_reason_codes),
            "event_risk_context": dict(ai_context.event_risk_context),
            "ai_trigger": review_trigger_payload.model_dump(mode="json"),
            "last_ai_trigger_reason": review_trigger_payload.trigger_reason,
            "last_ai_invoked_at": (
                resolved_last_ai_invoked_at.isoformat()
                if resolved_last_ai_invoked_at is not None
                else None
            ),
            "next_ai_review_due_at": None,
            "trigger_deduped": False,
            "trigger_fingerprint": review_trigger_payload.trigger_fingerprint,
            "fingerprint_changed_fields": list(review_trigger_payload.fingerprint_changed_fields),
            "dedupe_reason": None,
            "last_material_review_at": decision_generated_at.isoformat(),
            "forced_review_reason": review_trigger_payload.forced_review_reason,
            "last_ai_skip_reason": None,
            "pending_entry_plan_recheck": recheck_context,
            "source_decision_run_id": plan.source_decision_run_id,
            "source_plan_id": plan.id,
            "feature_snapshot_id": feature_row.id,
            "prior_read_path": prior_read_debug.get("prior_read_path"),
            "cache_applied": bool(prior_read_debug.get("cache_applied", False)),
            "cache_fallback_used": bool(prior_read_debug.get("cache_fallback_used", False)),
            "drawdown_state": drawdown_state,
            "position_management": {"position_management_context": position_management_context},
            "cycle_id": recheck_cycle_id,
            "snapshot_id": market_row.id,
        }
        usage_payload = (
            decision_metadata.get("usage")
            if isinstance(decision_metadata.get("usage"), dict)
            else None
        )
        provider_attempted = provider_name == "openai" or str(decision_metadata.get("source") or "") in {
            "llm",
            "llm_fallback",
        }
        if provider_attempted:
            estimated_cost_usd = estimate_ai_usage_cost_usd(
                model=self.settings_row.ai_model,
                usage=usage_payload,
            )
            decision_metadata["estimated_cost_usd"] = estimated_cost_usd
            if usage_payload is None:
                decision_metadata["cost_estimate_status"] = "missing_usage"
            elif estimated_cost_usd is None:
                decision_metadata["cost_estimate_status"] = "unknown_model_rate"
            else:
                decision_metadata["cost_estimate_status"] = "estimated"
        intent_semantics = infer_intent_semantics(
            decision.model_dump(mode="json"),
            decision_metadata,
        )
        decision = decision.model_copy(update=intent_semantics)
        decision_metadata = {**decision_metadata, **intent_semantics}
        trade_performance_tags = _decision_trade_performance_tags(
            decision,
            decision_metadata,
            ai_context_payload=ai_context_payload,
            drawdown_state=drawdown_state,
            feature_payload=feature_payload,
        )
        decision_metadata["trade_performance_tags"] = trade_performance_tags
        decision_metadata["generated_at"] = decision_generated_at.isoformat()
        decision_metadata["ai_decision_validity"] = _ai_decision_validity_payload(
            decision=decision,
            generated_at=decision_generated_at,
            market_snapshot=market_snapshot,
            market_snapshot_id=market_row.id,
            feature_payload=feature_payload,
            trade_performance_tags=trade_performance_tags,
        )
        decision_run = persist_agent_run(
            self.session,
            AgentRole.TRADING_DECISION,
            ENTRY_PLAN_AI_RECHECK_TRIGGER_EVENT,
            build_trading_decision_input_payload(
                market_snapshot=market_snapshot,
                higher_timeframe_context={},
                feature_payload=feature_payload,
                risk_context=ai_risk_context,
                decision_reference=decision_reference,
                ai_trigger=review_trigger_payload.model_dump(mode="json"),
                ai_context=ai_context,
            ),
            decision,
            provider_name=provider_name,
            metadata_json=decision_metadata,
        )
        recheck_correlation_ids = normalize_correlation_ids(
            correlation_ids,
            decision_id=decision_run.id,
        )
        record_audit_event(
            self.session,
            event_type="agent_output",
            entity_type="agent_run",
            entity_id=str(decision_run.id),
            severity="info",
            message="Pending entry plan AI recheck generated a decision.",
            payload={
                "provider": provider_name,
                "decision": decision.model_dump(mode="json"),
                "trade_performance_tags": decision_metadata.get("trade_performance_tags"),
                "ai_call_event": decision_metadata.get("ai_call_event"),
                "ai_trigger": decision_metadata.get("ai_trigger"),
                "pending_entry_plan_recheck": recheck_context,
                "source_decision_run_id": plan.source_decision_run_id,
                "plan_id": plan.id,
            },
            correlation_ids=recheck_correlation_ids,
        )
        if str(decision_metadata.get("source") or "") == "llm":
            record_audit_event(
                self.session,
                event_type="decision_ai_invoked",
                entity_type="decision_run",
                entity_id=str(decision_run.id),
                severity="info",
                message="AI inference was invoked for a pending entry plan recheck.",
                payload={
                    "ai_call_event": AI_CALL_EVENT_ALLOWED,
                    "symbol": plan.symbol,
                    "provider": provider_name,
                    "scope": "entry_plan_recheck",
                    "hard_skip_ai": False,
                    "snapshot_id": market_row.id,
                    "trigger": decision_metadata.get("ai_trigger"),
                    "source_decision_run_id": plan.source_decision_run_id,
                    "plan_id": plan.id,
                },
                correlation_ids=recheck_correlation_ids,
            )
            record_audit_event(
                self.session,
                event_type="decision_ai_received",
                entity_type="decision_run",
                entity_id=str(decision_run.id),
                severity="info",
                message="AI decision output was received for pending entry plan recheck.",
                payload={
                    "ai_call_event": "AI_DECISION_RECEIVED",
                    "symbol": plan.symbol,
                    "provider": provider_name,
                    "decision_type": decision.decision,
                    "intent": decision_metadata.get("intent_family"),
                    "confidence": decision.confidence,
                    "scope": "entry_plan_recheck",
                    "snapshot_id": market_row.id,
                    "trigger": decision_metadata.get("ai_trigger"),
                    "source_decision_run_id": plan.source_decision_run_id,
                    "plan_id": plan.id,
                },
                correlation_ids=recheck_correlation_ids,
            )

        metadata = self._pending_entry_plan_metadata(plan)
        metadata["last_ai_recheck_at"] = generated_at.isoformat()
        metadata["last_ai_recheck_decision_run_id"] = decision_run.id
        metadata["last_ai_recheck_decision"] = decision.decision
        metadata["last_ai_recheck_provider"] = provider_name
        metadata["last_ai_recheck_source"] = str(decision_metadata.get("source") or "")
        metadata["last_ai_recheck_fingerprint"] = review_trigger_payload.trigger_fingerprint
        plan.metadata_json = metadata
        self.session.add(plan)
        self.session.flush()
        return {
            "status": "received" if str(decision_metadata.get("source") or "") == "llm" else "unavailable",
            "decision": decision,
            "decision_run": decision_run,
            "decision_metadata": decision_metadata,
            "feature_snapshot_id": feature_row.id,
            "ai_call_policy": ai_call_policy,
            "trigger": review_trigger_payload.model_dump(mode="json"),
        }

    def _mark_pending_entry_plan_triggered(
        self,
        plan: PendingEntryPlan,
        *,
        execution_result: dict[str, object],
        correlation_ids: dict[str, object] | None = None,
    ) -> PendingEntryPlan:
        now = utcnow_naive()
        metadata = self._pending_entry_plan_metadata(plan)
        metadata["last_transition_at"] = now.isoformat()
        metadata["last_transition_reason"] = "PLAN_EXECUTED"
        metadata["execution_result"] = dict(execution_result)
        plan.plan_status = "triggered"
        plan.triggered_at = now
        plan.metadata_json = metadata
        self.session.add(plan)
        self.session.flush()
        record_audit_event(
            self.session,
            event_type="pending_entry_plan_triggered",
            entity_type="pending_entry_plan",
            entity_id=str(plan.id),
            severity="info",
            message="Pending entry plan triggered into live execution.",
            payload={
                "symbol": plan.symbol,
                "side": plan.side,
                "execution_result": dict(execution_result),
            },
            correlation_ids=correlation_ids,
        )
        correlation_payload = _as_dict(correlation_ids)
        funnel_cycle_id = str(
            correlation_payload.get("cycle_id")
            or metadata.get("last_watch_cycle_id")
            or metadata.get("cycle_id")
            or f"pending-entry-plan:{plan.id}"
        )
        self._record_decision_funnel_audit(
            cycle_id=funnel_cycle_id,
            symbol=plan.symbol,
            timeframe=plan.source_timeframe,
            regime=plan.regime,
            stage="entry_plan_triggered",
            status="order_submitted",
            strategy_candidate=self._pending_plan_funnel_candidate(plan),
            entry_plan_status=plan.plan_status,
            m1_confirmation_status="passed",
            final_risk_status="approved",
            execution_result=dict(execution_result),
            blocked_reason_codes=(
                list(dict(execution_result).get("reason_codes") or [])
                if self._execution_order_status(dict(execution_result)) in {"blocked", "rejected", "error", "submission_unknown"}
                else []
            ),
            detail={"plan_id": plan.id, "execution_result": dict(execution_result)},
            severity="info",
            entity_type="pending_entry_plan",
            entity_id=str(plan.id),
            correlation_ids=normalize_correlation_ids(
                correlation_ids,
                cycle_id=funnel_cycle_id,
                snapshot_id=correlation_payload.get("snapshot_id") or metadata.get("last_watch_snapshot_id") or metadata.get("snapshot_id"),
                decision_id=correlation_payload.get("decision_id") or plan.source_decision_run_id,
                risk_id=correlation_payload.get("risk_id") or dict(execution_result).get("risk_check_id"),
                execution_id=correlation_payload.get("execution_id") or dict(execution_result).get("order_id"),
            ),
        )
        refresh_decision_performance_fact_usefulness(self.session, plan.source_decision_run_id)
        return plan

    def _latest_decision_run(self, *, decision_run_id: int | None) -> AgentRun | None:
        if decision_run_id is None:
            return None
        return self.session.get(AgentRun, decision_run_id)

    def run_exchange_sync_cycle(
        self,
        *,
        symbol: str | None = None,
        trigger_event: str = "background_poll",
    ) -> dict[str, object]:
        if not self.credentials.binance_api_key or not self.credentials.binance_api_secret:
            skipped_at = utcnow_naive()
            effective_symbol = symbol or self.settings_row.default_symbol
            for scope in ("account", "positions", "open_orders", "protective_orders"):
                mark_sync_skipped(
                    self.settings_row,
                    scope=scope,
                    reason_code="LIVE_CREDENTIALS_MISSING",
                    observed_at=skipped_at,
                    detail={"symbol": effective_symbol, "trigger_event": trigger_event},
                )
            self.session.add(self.settings_row)
            self.session.flush()
            return {
                "status": "skipped",
                "reason": "LIVE_CREDENTIALS_MISSING",
                "symbol": effective_symbol,
                "sync_freshness_summary": build_sync_freshness_summary(self.settings_row),
            }
        try:
            result = sync_live_state(self.session, self.settings_row, symbol=symbol)
        except Exception as exc:
            record_audit_event(
                self.session,
                event_type="live_poll_sync_failed",
                entity_type="binance",
                entity_id=symbol or self.settings_row.default_symbol,
                severity="warning",
                message="Background exchange polling sync failed.",
                payload={"trigger_event": trigger_event, "error": str(exc)},
            )
            record_health_event(
                self.session,
                component="live_sync",
                status="error",
                message="Background exchange polling sync failed.",
                payload={"trigger_event": trigger_event, "error": str(exc)},
            )
            return {
                "status": "error",
                "symbol": symbol or self.settings_row.default_symbol,
                "trigger_event": trigger_event,
                "error": str(exc),
                "sync_freshness_summary": build_sync_freshness_summary(self.settings_row),
            }
        record_audit_event(
            self.session,
            event_type="live_poll_sync",
            entity_type="binance",
            entity_id=symbol or self.settings_row.default_symbol,
            severity="info",
            message="Background exchange polling sync completed.",
            payload={"trigger_event": trigger_event, **result},
        )
        return {"status": "ok", "trigger_event": trigger_event, **result}

    def _account_snapshot_preview(self) -> dict[str, object]:
        latest = self.session.scalar(select(PnLSnapshot).order_by(desc(PnLSnapshot.created_at)).limit(1))
        if latest is not None:
            return account_snapshot_to_dict(latest)
        return account_snapshot_to_dict(get_latest_pnl_snapshot(self.session, self.settings_row))

    @staticmethod
    def _should_attempt_auto_resume(trigger_event: str) -> bool:
        return trigger_event != "historical_replay"

    def _ensure_auto_resume(
        self,
        *,
        trigger_event: str,
        auto_resume_checked: bool,
    ) -> dict[str, object] | None:
        if auto_resume_checked or not self._should_attempt_auto_resume(trigger_event):
            return None
        return attempt_auto_resume(
            self.session,
            self.settings_row,
            trigger_source=trigger_event,
        )

    @staticmethod
    def _decision_reference_sync_at(sync_freshness_summary: dict[str, object], scope: str) -> str | None:
        scope_payload = sync_freshness_summary.get(scope)
        if not isinstance(scope_payload, dict):
            return None
        last_sync_at = scope_payload.get("last_sync_at")
        if isinstance(last_sync_at, datetime):
            return last_sync_at.isoformat()
        if isinstance(last_sync_at, str) and last_sync_at:
            return last_sync_at
        return None

    @staticmethod
    def _decision_reference_has_blocking_freshness(
        *,
        market_snapshot: MarketSnapshotPayload,
        sync_freshness_summary: dict[str, object],
    ) -> bool:
        if market_snapshot.is_stale or not market_snapshot.is_complete:
            return True
        for scope_payload in sync_freshness_summary.values():
            if not isinstance(scope_payload, dict):
                continue
            if bool(scope_payload.get("stale")) or bool(scope_payload.get("incomplete")):
                return True
        return False

    def _build_decision_reference_payload(
        self,
        *,
        symbol: str,
        timeframe: str,
        market_snapshot: MarketSnapshotPayload,
        market_row: MarketSnapshot,
        runtime_state: dict[str, object] | None = None,
    ) -> dict[str, object]:
        operational_status = build_operational_status_payload(
            self.settings_row,
            session=self.session,
            runtime_state=runtime_state,
        )
        market_freshness_summary = {
            "symbol": symbol,
            "timeframe": timeframe,
            "source": market_snapshot.source,
            "source_status": market_snapshot.source_status,
            "status": "fresh"
            if not market_snapshot.is_stale and market_snapshot.is_complete
            else ("stale" if market_snapshot.is_stale else "incomplete"),
            "snapshot_at": market_snapshot.snapshot_time.isoformat(),
            "stale": market_snapshot.is_stale,
            "incomplete": not market_snapshot.is_complete,
            "latest_price": market_snapshot.latest_price,
            "snapshot_id": market_row.id,
            "source_detail": dict(market_snapshot.source_detail),
        }
        sync_freshness_summary = {
            str(scope): dict(payload)
            for scope, payload in operational_status.sync_freshness_summary.items()
            if isinstance(payload, dict)
        }
        freshness_blocking = self._decision_reference_has_blocking_freshness(
            market_snapshot=market_snapshot,
            sync_freshness_summary=sync_freshness_summary,
        )
        return {
            "market_snapshot_id": market_row.id,
            "market_snapshot_at": market_snapshot.snapshot_time.isoformat(),
            "market_snapshot_source": market_snapshot.source,
            "market_snapshot_stale": market_snapshot.is_stale,
            "market_snapshot_incomplete": not market_snapshot.is_complete,
            "account_sync_at": (
                str(operational_status.account_sync_summary.get("last_synced_at") or "") or None
            ),
            "positions_sync_at": self._decision_reference_sync_at(sync_freshness_summary, "positions"),
            "open_orders_sync_at": self._decision_reference_sync_at(sync_freshness_summary, "open_orders"),
            "protective_orders_sync_at": self._decision_reference_sync_at(sync_freshness_summary, "protective_orders"),
            "account_sync_status": str(operational_status.account_sync_summary.get("status") or "") or None,
            "sync_freshness_summary": sync_freshness_summary,
            "market_freshness_summary": market_freshness_summary,
            "freshness_blocking": freshness_blocking,
            "display_gap": False,
            "display_gap_reason": (
                "The decision used stale or incomplete market/account/order state, so new entry should remain blocked."
                if freshness_blocking
                else None
            ),
        }

    def _collect_market_snapshot(
        self,
        *,
        symbol: str,
        timeframe: str,
        upto_index: int | None,
        force_stale: bool,
    ) -> tuple[MarketSnapshotPayload, MarketSnapshot]:
        market_snapshot = build_market_snapshot(
            symbol=symbol,
            timeframe=timeframe,
            upto_index=upto_index,
            force_stale=force_stale,
            use_binance=self.settings_row.binance_market_data_enabled,
            binance_testnet_enabled=self.settings_row.binance_testnet_enabled,
            stale_threshold_seconds=self.settings_row.stale_market_seconds,
            event_context_provider=self.event_context_provider,
        )
        market_row = persist_market_snapshot(self.session, market_snapshot)
        if self.settings_row.ai_enabled:
            record_audit_event(
                self.session,
                event_type="market_snapshot",
                entity_type="market_snapshot",
                entity_id=str(market_row.id),
                message="Market snapshot collected.",
                payload={
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "source": market_snapshot.source,
                    "source_status": market_snapshot.source_status,
                    "is_stale": market_snapshot.is_stale,
                    "is_complete": market_snapshot.is_complete,
                },
            )
        # Market snapshots are followed by additional context/exchange reads; commit
        # the observed fact first so safety-control writes are not blocked.
        self.session.commit()
        return market_snapshot, market_row

    def _recent_symbol_decisions(self, symbol: str, *, limit: int = 8) -> list[AgentRun]:
        rows = list(
            self.session.scalars(
                select(AgentRun)
                .where(AgentRun.role == AgentRole.TRADING_DECISION.value)
                .order_by(desc(AgentRun.created_at))
                .limit(max(limit * 6, 24))
            )
        )
        symbol_key = symbol.upper()
        return [
            row
            for row in rows
            if isinstance(row.output_payload, dict)
            and str(row.output_payload.get("symbol") or "").upper() == symbol_key
        ][:limit]

    def _recent_signal_performance_score(self, symbol: str) -> float:
        return _safe_float(
            self._recent_signal_performance_summary(
                symbol=symbol,
                timeframe="",
                scenario="",
                regime="",
                trend_alignment="",
            ).get("score"),
            default=0.55,
        )

    def _slippage_sensitivity_score(self, symbol: str) -> float:
        executions = list(
            self.session.scalars(
                select(Execution)
                .where(Execution.symbol == symbol.upper())
                .order_by(desc(Execution.created_at))
                .limit(8)
            )
        )
        if not executions:
            return 0.65
        avg_adverse_signed_slippage_bps = sum(
            max(self._signed_slippage_bps(row), 0.0)
            for row in executions
        ) / max(len(executions), 1)
        threshold_bps = max(float(self.settings_row.slippage_threshold_pct or 0.0) * 10_000.0, 10.0)
        return _clamp_score(0.92 - (min(avg_adverse_signed_slippage_bps / threshold_bps, 1.0) * 0.55))

    def _confidence_consistency_score(self, symbol: str, *, decision: str) -> float:
        decision_rows = self._recent_symbol_decisions(symbol, limit=6)
        if not decision_rows:
            return 0.55
        confidences: list[float] = []
        same_direction = 0
        directional_rows = 0
        for row in decision_rows:
            payload = row.output_payload if isinstance(row.output_payload, dict) else {}
            recent_decision = str(payload.get("decision") or "")
            confidence = float(payload.get("confidence") or 0.0)
            confidences.append(confidence)
            if recent_decision in {"long", "short"}:
                directional_rows += 1
                if recent_decision == decision:
                    same_direction += 1
        avg_confidence = sum(confidences) / max(len(confidences), 1)
        direction_ratio = same_direction / max(directional_rows, 1) if decision in {"long", "short"} else 0.5
        return _clamp_score((avg_confidence * 0.6) + (direction_ratio * 0.4))

    def _candidate_exposure_impact_score(self, *, symbol: str, priority: bool, total_open_positions: int) -> float:
        if priority:
            return 1.0
        tracked_count = max(len(get_effective_symbols(self.settings_row)), 1)
        crowding_ratio = total_open_positions / tracked_count
        symbol_is_open = bool(get_open_positions(self.session, symbol))
        base = 0.9 if not symbol_is_open else 0.75
        return _clamp_score(base - (crowding_ratio * 0.25), lower=0.2, upper=1.0)

    @staticmethod
    def _derivatives_summary(feature_payload) -> dict[str, object]:
        derivatives = feature_payload.derivatives
        return {
            "available": derivatives.available,
            "source": derivatives.source,
            "fallback_used": derivatives.fallback_used,
            "open_interest_change_pct": derivatives.open_interest_change_pct,
            "funding_rate": derivatives.funding_rate,
            "top_trader_long_short_ratio": derivatives.top_trader_long_short_ratio,
            "top_trader_crowding_bias": derivatives.top_trader_crowding_bias,
            "best_bid": derivatives.best_bid,
            "best_ask": derivatives.best_ask,
            "spread_bps": derivatives.spread_bps,
            "spread_stress_score": derivatives.spread_stress_score,
            "taker_buy_sell_imbalance": derivatives.taker_buy_sell_imbalance,
            "perp_basis_bps": derivatives.perp_basis_bps,
            "crowding_bias": derivatives.crowding_bias,
            "taker_flow_alignment": derivatives.taker_flow_alignment,
            "funding_bias": derivatives.funding_bias,
            "oi_expanding_with_price": derivatives.oi_expanding_with_price,
            "oi_falling_on_breakout": derivatives.oi_falling_on_breakout,
            "crowded_long_risk": derivatives.crowded_long_risk,
            "crowded_short_risk": derivatives.crowded_short_risk,
            "top_trader_long_crowded": derivatives.top_trader_long_crowded,
            "top_trader_short_crowded": derivatives.top_trader_short_crowded,
            "spread_headwind": derivatives.spread_headwind,
            "spread_stress": derivatives.spread_stress,
            "breakout_spread_headwind": derivatives.breakout_spread_headwind,
            "entry_veto_reason_codes": list(derivatives.entry_veto_reason_codes),
            "breakout_veto_reason_codes": list(derivatives.breakout_veto_reason_codes),
            "long_discount_magnitude": derivatives.long_discount_magnitude,
            "short_discount_magnitude": derivatives.short_discount_magnitude,
            "long_alignment_score": derivatives.long_alignment_score,
            "short_alignment_score": derivatives.short_alignment_score,
        }

    @staticmethod
    def _lead_lag_summary(feature_payload) -> dict[str, object]:
        lead_lag = feature_payload.lead_lag
        return {
            "available": lead_lag.available,
            "leader_bias": lead_lag.leader_bias,
            "reference_symbols": list(lead_lag.reference_symbols),
            "missing_reference_symbols": list(lead_lag.missing_reference_symbols),
            "bullish_alignment_score": lead_lag.bullish_alignment_score,
            "bearish_alignment_score": lead_lag.bearish_alignment_score,
            "bullish_breakout_confirmed": lead_lag.bullish_breakout_confirmed,
            "bearish_breakout_confirmed": lead_lag.bearish_breakout_confirmed,
            "bullish_breakout_ahead": lead_lag.bullish_breakout_ahead,
            "bearish_breakout_ahead": lead_lag.bearish_breakout_ahead,
            "bullish_pullback_supported": lead_lag.bullish_pullback_supported,
            "bearish_pullback_supported": lead_lag.bearish_pullback_supported,
            "bullish_continuation_supported": lead_lag.bullish_continuation_supported,
            "bearish_continuation_supported": lead_lag.bearish_continuation_supported,
            "strong_reference_confirmation": lead_lag.strong_reference_confirmation,
            "weak_reference_confirmation": lead_lag.weak_reference_confirmation,
        }

    def _build_lead_market_features(
        self,
        *,
        base_timeframe: str,
        upto_index: int | None,
        force_stale: bool,
    ) -> dict[str, FeaturePayload]:
        lead_contexts = build_lead_market_contexts(
            base_timeframe=base_timeframe,
            upto_index=upto_index,
            force_stale=force_stale,
            use_binance=self.settings_row.binance_market_data_enabled,
            binance_testnet_enabled=self.settings_row.binance_testnet_enabled,
            stale_threshold_seconds=self.settings_row.stale_market_seconds,
            event_context_provider=self.event_context_provider,
        )
        lead_features: dict[str, FeaturePayload] = {}
        for lead_symbol, context in lead_contexts.items():
            snapshot = context.get(base_timeframe)
            if snapshot is None:
                continue
            higher_timeframe_context = {
                timeframe: payload
                for timeframe, payload in context.items()
                if timeframe != base_timeframe
            }
            lead_features[lead_symbol] = compute_features(snapshot, higher_timeframe_context)
        return lead_features

    def _candidate_derivatives_alignment_score(self, feature_payload, *, decision: str, priority: bool) -> float:
        if priority:
            return 1.0
        derivatives = feature_payload.derivatives
        if not derivatives.available:
            return 0.55
        breakout_up = feature_payload.breakout.broke_swing_high or feature_payload.breakout.range_breakout_direction == "up"
        breakout_down = feature_payload.breakout.broke_swing_low or feature_payload.breakout.range_breakout_direction == "down"
        if decision == "long":
            score = float(derivatives.long_alignment_score)
            if derivatives.spread_headwind:
                score -= 0.12
            if derivatives.spread_stress:
                score -= 0.1
            if derivatives.top_trader_long_crowded:
                score -= 0.08
            if derivatives.funding_bias == "long_headwind" and derivatives.spread_headwind:
                score -= 0.06
            if breakout_up and derivatives.breakout_spread_headwind and not derivatives.oi_expanding_with_price:
                score -= 0.16
            if breakout_up and derivatives.breakout_veto_reason_codes:
                score -= 0.05
            return _clamp_score(score)
        if decision == "short":
            score = float(derivatives.short_alignment_score)
            if derivatives.spread_headwind:
                score -= 0.12
            if derivatives.spread_stress:
                score -= 0.1
            if derivatives.top_trader_short_crowded:
                score -= 0.08
            if derivatives.funding_bias == "short_headwind" and derivatives.spread_headwind:
                score -= 0.06
            if breakout_down and derivatives.breakout_spread_headwind and not derivatives.oi_expanding_with_price:
                score -= 0.16
            if breakout_down and derivatives.breakout_veto_reason_codes:
                score -= 0.05
            return _clamp_score(score)
        return 0.55

    @staticmethod
    def _candidate_lead_lag_alignment_score(feature_payload, *, decision: str, priority: bool) -> float:
        if priority:
            return 1.0
        lead_lag = feature_payload.lead_lag
        if not lead_lag.available:
            return 0.55
        breakout_up = feature_payload.breakout.broke_swing_high or feature_payload.breakout.range_breakout_direction == "up"
        breakout_down = feature_payload.breakout.broke_swing_low or feature_payload.breakout.range_breakout_direction == "down"
        if decision == "long":
            score = float(lead_lag.bullish_alignment_score)
            if feature_payload.pullback_context.state == "bullish_pullback" and lead_lag.bullish_pullback_supported:
                score += 0.08
            if feature_payload.pullback_context.state == "bullish_continuation" and lead_lag.bullish_continuation_supported:
                score += 0.06
            if breakout_up and lead_lag.bullish_breakout_confirmed:
                score += 0.06
            elif breakout_up and lead_lag.bullish_breakout_ahead:
                score *= 0.72
            return _clamp_score(score)
        if decision == "short":
            score = float(lead_lag.bearish_alignment_score)
            if feature_payload.pullback_context.state == "bearish_pullback" and lead_lag.bearish_pullback_supported:
                score += 0.08
            if feature_payload.pullback_context.state == "bearish_continuation" and lead_lag.bearish_continuation_supported:
                score += 0.06
            if breakout_down and lead_lag.bearish_breakout_confirmed:
                score += 0.06
            elif breakout_down and lead_lag.bearish_breakout_ahead:
                score *= 0.72
            return _clamp_score(score)
        return 0.55

    @staticmethod
    def _entry_candidate_late_long_filter(
        *,
        decision: str,
        entry_mode: str,
        strategy_engine: str,
        feature_payload: object,
    ) -> dict[str, object]:
        def _read(source: object, key: str, default: object = None) -> object:
            if isinstance(source, dict):
                return source.get(key, default)
            return getattr(source, key, default)

        def _inactive(details: dict[str, object] | None = None) -> dict[str, object]:
            return {"active": False, "reason_codes": [], "details": details or {}}

        if str(decision).lower() != "long":
            return _inactive()
        if str(entry_mode).lower() != "pullback_confirm":
            return _inactive()
        if str(strategy_engine) != "trend_continuation_engine":
            return _inactive()
        if feature_payload is None:
            return _inactive()

        location = _read(feature_payload, "location")
        regime = _read(feature_payload, "regime")
        pullback_context = _read(feature_payload, "pullback_context")
        derivatives = _read(feature_payload, "derivatives")
        multi_timeframe = _read(feature_payload, "multi_timeframe", {})

        range_position_pct = _safe_float(_read(location, "range_position_pct"))
        distance_from_recent_high_pct = _safe_float(_read(location, "distance_from_recent_high_pct"), default=-100.0)
        vwap_distance_pct = _safe_float(_read(location, "vwap_distance_pct"))
        drawdown_pct = _safe_float(_read(feature_payload, "drawdown_pct"), default=100.0)
        rsi = _safe_float(_read(feature_payload, "rsi"), default=50.0)
        momentum_state = str(_read(regime, "momentum_state", "") or "").lower()
        pullback_state = str(_read(pullback_context, "state", "") or "").lower()

        higher_timeframe_upper_hits: list[dict[str, object]] = []
        if isinstance(multi_timeframe, dict):
            for timeframe in ("4h", "24h", "1d"):
                context = multi_timeframe.get(timeframe)
                if context is None:
                    continue
                timeframe_drawdown_pct = _safe_float(_read(context, "drawdown_pct"), default=100.0)
                if timeframe_drawdown_pct <= ENTRY_CANDIDATE_LATE_LONG_MAX_DRAWDOWN_PCT:
                    higher_timeframe_upper_hits.append(
                        {
                            "timeframe": timeframe,
                            "drawdown_pct": round(timeframe_drawdown_pct, 6),
                        }
                    )

        upper_location = (
            range_position_pct >= ENTRY_CANDIDATE_LATE_LONG_UPPER_RANGE_POSITION
            or distance_from_recent_high_pct >= ENTRY_CANDIDATE_LATE_LONG_RECENT_HIGH_DISTANCE_PCT
            or drawdown_pct <= ENTRY_CANDIDATE_LATE_LONG_MAX_DRAWDOWN_PCT
            or bool(higher_timeframe_upper_hits)
        )
        no_pullback_confirm = pullback_state != "bullish_pullback"
        overheated = (
            momentum_state == "overextended"
            or rsi >= ENTRY_CANDIDATE_LATE_LONG_RSI
            or (
                range_position_pct >= ENTRY_CANDIDATE_LATE_LONG_UPPER_RANGE_POSITION
                and vwap_distance_pct >= ENTRY_CANDIDATE_LATE_LONG_VWAP_DISTANCE_PCT
            )
        )

        derivatives_available = bool(_read(derivatives, "available", False))
        entry_veto_codes = [str(code) for code in (_read(derivatives, "entry_veto_reason_codes", []) or []) if code]
        breakout_veto_codes = [
            str(code) for code in (_read(derivatives, "breakout_veto_reason_codes", []) or []) if code
        ]
        derivatives_headwind = derivatives_available and (
            not bool(_read(derivatives, "oi_expanding_with_price", False))
            or str(_read(derivatives, "taker_flow_alignment", "") or "").lower() != "bullish"
            or bool(_read(derivatives, "top_trader_long_crowded", False))
            or bool(_read(derivatives, "crowded_long_risk", False))
            or "TOP_TRADER_LONG_CROWDED" in entry_veto_codes
            or "BREAKOUT_OI_NOT_EXPANDING" in breakout_veto_codes
        )
        details = {
            "range_position_pct": round(range_position_pct, 6),
            "distance_from_recent_high_pct": round(distance_from_recent_high_pct, 6),
            "drawdown_pct": round(drawdown_pct, 6),
            "higher_timeframe_upper_hits": higher_timeframe_upper_hits,
            "rsi": round(rsi, 6),
            "vwap_distance_pct": round(vwap_distance_pct, 6),
            "momentum_state": momentum_state or None,
            "pullback_state": pullback_state or None,
            "derivatives_available": derivatives_available,
            "oi_expanding_with_price": bool(_read(derivatives, "oi_expanding_with_price", False)),
            "taker_flow_alignment": str(_read(derivatives, "taker_flow_alignment", "") or "") or None,
            "top_trader_long_crowded": bool(_read(derivatives, "top_trader_long_crowded", False)),
            "crowded_long_risk": bool(_read(derivatives, "crowded_long_risk", False)),
            "entry_veto_reason_codes": entry_veto_codes,
            "breakout_veto_reason_codes": breakout_veto_codes,
        }
        if not (upper_location and no_pullback_confirm and overheated and derivatives_headwind):
            return _inactive(details)
        return {
            "active": True,
            "reason_codes": [
                ENTRY_CANDIDATE_LATE_LONG_REASON_CODE,
                ENTRY_CANDIDATE_LONG_EXTENSION_DERIVATIVES_REASON_CODE,
            ],
            "details": details,
        }

    def _build_selection_candidate(
        self,
        *,
        symbol: str,
        timeframe: str,
        upto_index: int | None,
        force_stale: bool,
        missing_protection_symbols: set[str],
        total_open_positions: int,
        lead_market_features: dict[str, FeaturePayload] | None = None,
    ) -> dict[str, object]:
        market_context = build_market_context(
            symbol=symbol,
            base_timeframe=timeframe,
            upto_index=upto_index,
            force_stale=force_stale,
            use_binance=self.settings_row.binance_market_data_enabled,
            binance_testnet_enabled=self.settings_row.binance_testnet_enabled,
            stale_threshold_seconds=self.settings_row.stale_market_seconds,
            event_context_provider=self.event_context_provider,
        )
        market_snapshot = market_context[timeframe]
        higher_timeframe_context = {key: value for key, value in market_context.items() if key != timeframe}
        feature_payload = compute_features(
            market_snapshot,
            higher_timeframe_context,
            lead_market_features=lead_market_features,
        )
        open_positions = get_open_positions(self.session, symbol)
        priority = bool(open_positions) or symbol in missing_protection_symbols
        long_breakout_allowed = bool(
            (
                feature_payload.breakout.broke_swing_high
                or feature_payload.breakout.range_breakout_direction == "up"
            )
            and feature_payload.regime.trend_alignment == "bullish_aligned"
            and feature_payload.regime.primary_regime != "range"
            and not feature_payload.regime.weak_volume
            and feature_payload.regime.momentum_state == "strengthening"
            and feature_payload.volume_persistence.persistence_ratio >= 1.05
        )
        short_breakout_allowed = bool(
            (
                feature_payload.breakout.broke_swing_low
                or feature_payload.breakout.range_breakout_direction == "down"
            )
            and feature_payload.regime.trend_alignment == "bearish_aligned"
            and feature_payload.regime.primary_regime != "range"
            and not feature_payload.regime.weak_volume
            and feature_payload.regime.momentum_state == "strengthening"
            and feature_payload.volume_persistence.persistence_ratio >= 1.05
        )
        strategy_engine_selection = select_strategy_engine(
            market_snapshot=market_snapshot,
            features=feature_payload,
            open_positions=open_positions,
            risk_context={},
            long_breakout_allowed=long_breakout_allowed,
            short_breakout_allowed=short_breakout_allowed,
        )
        selected_engine_payload = strategy_engine_selection.selected_engine.to_payload()
        selected_engine_name = str(selected_engine_payload.get("engine_name") or "unspecified_engine")

        decision = "hold"
        scenario = "hold"
        explanation_short = "중립 심볼"
        if priority and symbol in missing_protection_symbols:
            decision = "reduce"
            scenario = "protection_restore"
            explanation_short = "보호주문 복구 우선 심볼"
        elif priority:
            decision = "reduce"
            scenario = "reduce"
            explanation_short = "오픈 포지션 관리 우선 심볼"
        elif feature_payload.regime.weak_volume:
            decision = "hold"
            scenario = "hold"
            explanation_short = "유동성 약화 관찰 심볼"
        elif feature_payload.trend_score >= 0.15:
            decision = "long"
            scenario = "trend_follow"
            explanation_short = "상승 정렬 진입 후보"
        elif feature_payload.trend_score <= -0.15:
            decision = "short"
            scenario = "trend_follow"
            explanation_short = "하락 정렬 진입 후보"
        elif feature_payload.momentum_score > 0:
            decision = "long"
            scenario = "pullback_entry"
            explanation_short = "당김목 진입 후보"

        if not priority and strategy_engine_selection.selected_engine.eligible:
            decision = str(strategy_engine_selection.selected_engine.decision_hint)
            scenario = str(strategy_engine_selection.selected_engine.scenario)
            explanation_short = {
                "trend_pullback_engine": "trend_pullback_candidate",
                "trend_continuation_engine": "trend_continuation_candidate",
                "breakout_exception_engine": "breakout_exception_candidate",
                "range_mean_reversion_engine": "range_mean_reversion_candidate",
                "protection_reduce_engine": "protection_reduce_priority",
            }.get(selected_engine_name, "strategy_engine_candidate")

        entry_price = float(market_snapshot.latest_price)
        atr = max(float(feature_payload.atr or 0.0), entry_price * 0.0025, 1e-6)
        if decision == "long":
            stop_loss = entry_price - atr
            take_profit = entry_price + (atr * 1.8)
        elif decision == "short":
            stop_loss = entry_price + atr
            take_profit = entry_price - (atr * 1.8)
        else:
            stop_loss = None
            take_profit = None
        expected_rr_ratio = 0.0
        if stop_loss is not None and take_profit is not None:
            risk_distance = abs(entry_price - stop_loss)
            reward_distance = abs(take_profit - entry_price)
            if risk_distance > 0:
                expected_rr_ratio = reward_distance / risk_distance

        regime = feature_payload.regime
        if priority:
            regime_fit = 1.0
        elif selected_engine_name == "range_mean_reversion_engine":
            regime_fit = 0.9 if regime.primary_regime == "range" else 0.35
        elif decision == "long":
            regime_fit = 1.0 if regime.trend_alignment == "bullish_aligned" else 0.45
        elif decision == "short":
            regime_fit = 1.0 if regime.trend_alignment == "bearish_aligned" else 0.45
        else:
            regime_fit = 0.4
        expected_rr = _clamp_score(expected_rr_ratio / 3.0)
        candidate_entry_mode = (
            "manage_only"
            if priority
            else str(selected_engine_payload.get("entry_mode") or "pullback_confirm")
            if decision in {"long", "short"} and strategy_engine_selection.selected_engine.eligible
            else "pullback_confirm"
            if scenario == "pullback_entry"
            else "breakout_confirm"
            if decision in {"long", "short"} and selected_engine_name == "breakout_exception_engine"
            else "none"
        )
        late_long_filter = self._entry_candidate_late_long_filter(
            decision=decision,
            entry_mode=candidate_entry_mode,
            strategy_engine=selected_engine_name,
            feature_payload=feature_payload,
        )
        performance_summary = self._recent_signal_performance_summary(
            symbol=symbol,
            timeframe=timeframe,
            scenario=scenario,
            regime=regime.primary_regime,
            trend_alignment=regime.trend_alignment,
            strategy_engine=selected_engine_name,
        )
        holding_profile_context = evaluate_holding_profile(
            decision=decision,
            features=feature_payload,
            selection_context={},
            strategy_engine=selected_engine_name,
        )
        recent_signal_performance = _safe_float(performance_summary.get("score"), default=0.55)
        derivatives_alignment = self._candidate_derivatives_alignment_score(
            feature_payload,
            decision=decision,
            priority=priority,
        )
        lead_lag_alignment = self._candidate_lead_lag_alignment_score(
            feature_payload,
            decision=decision,
            priority=priority,
        )
        derivatives_summary = self._derivatives_summary(feature_payload)
        if decision == "long":
            side_discount_magnitude = float(feature_payload.derivatives.long_discount_magnitude)
        elif decision == "short":
            side_discount_magnitude = float(feature_payload.derivatives.short_discount_magnitude)
        else:
            side_discount_magnitude = 0.0
        side_veto_reason_codes = (
            list(feature_payload.derivatives.breakout_veto_reason_codes)
            if candidate_entry_mode == "breakout_confirm"
            else list(feature_payload.derivatives.entry_veto_reason_codes)
        )
        derivatives_summary["discount_magnitude"] = round(side_discount_magnitude, 6)
        derivatives_summary["veto_reason_codes"] = side_veto_reason_codes
        lead_lag_summary = self._lead_lag_summary(feature_payload)
        slippage_sensitivity = self._slippage_sensitivity_score(symbol)
        confidence_consistency = self._confidence_consistency_score(symbol, decision=decision)
        exposure_impact = self._candidate_exposure_impact_score(
            symbol=symbol,
            priority=priority,
            total_open_positions=total_open_positions,
        )
        base_total = (
            (recent_signal_performance * 0.34)
            + (expected_rr * 0.13)
            + (regime_fit * 0.11)
            + (derivatives_alignment * 0.11)
            + (lead_lag_alignment * 0.09)
            + (slippage_sensitivity * 0.1)
            + (confidence_consistency * 0.07)
            + (exposure_impact * 0.05)
        )

        candidate = TradeDecisionCandidate(
            candidate_id=f"{symbol}:{timeframe}:{scenario}:{selected_engine_name}",
            scenario=scenario,  # type: ignore[arg-type]
            decision=decision,  # type: ignore[arg-type]
            symbol=symbol,
            timeframe=timeframe,
            confidence=round(_clamp_score((feature_payload.momentum_score + 1.0) / 2.0), 6),
            entry_zone_min=entry_price * (0.999 if decision == "long" else 1.001) if decision in {"long", "short"} else None,
            entry_zone_max=entry_price * (1.001 if decision == "long" else 0.999) if decision in {"long", "short"} else None,
            stop_loss=stop_loss,
            take_profit=take_profit,
            max_holding_minutes=max(30, min(int(timeframe.rstrip("mh")) * 8 if timeframe[:-1].isdigit() else 120, 720)),
            risk_pct=min(self.settings_row.max_risk_per_trade, HARD_MAX_RISK_PER_TRADE),
            leverage=min(self.settings_row.max_leverage, get_symbol_leverage_cap(symbol)),
            rationale_codes=[
                f"ENGINE_{selected_engine_name.upper()}",
                f"REGIME_{regime.primary_regime.upper()}",
                f"TREND_{regime.trend_alignment.upper()}",
                (
                    "EXPECTANCY_UNDERPERFORMING"
                    if bool(performance_summary.get("underperforming"))
                    else "EXPECTANCY_ALIGNED"
                    if recent_signal_performance >= 0.62
                    else "EXPECTANCY_NEUTRAL"
                ),
                "DERIVATIVES_ALIGNED" if derivatives_alignment >= 0.65 else "DERIVATIVES_NEUTRAL",
                "LEAD_MARKETS_ALIGNED" if lead_lag_alignment >= 0.68 else "LEAD_MARKETS_NEUTRAL",
            ]
            + (["DERIVATIVES_HEADWIND"] if derivatives_alignment <= 0.35 else [])
            + (["SPREAD_HEADWIND"] if feature_payload.derivatives.spread_headwind else [])
            + (
                ["SPREAD_STRESS"]
                if feature_payload.derivatives.spread_stress
                else []
            )
            + (
                ["TOP_TRADER_LONG_CROWDED"]
                if decision == "long" and feature_payload.derivatives.top_trader_long_crowded
                else ["TOP_TRADER_SHORT_CROWDED"]
                if decision == "short" and feature_payload.derivatives.top_trader_short_crowded
                else []
            )
            + (
                ["BREAKOUT_OI_SPREAD_FILTER"]
                if (
                    feature_payload.derivatives.breakout_spread_headwind
                    and not feature_payload.derivatives.oi_expanding_with_price
                )
                else []
            )
            + (
                ["ADVERSE_SIGNED_SLIPPAGE"]
                if _safe_float(performance_summary.get("avg_signed_slippage_bps")) >= SETUP_CLUSTER_SIGNED_SLIPPAGE_BPS_THRESHOLD
                else []
            )
            + list(late_long_filter.get("reason_codes") or [])
            + side_veto_reason_codes,
            holding_profile=str(holding_profile_context["holding_profile"]),
            holding_profile_reason=str(holding_profile_context["holding_profile_reason"]),
            strategy_engine=selected_engine_name,
            strategy_engine_context=strategy_engine_selection.to_payload(),
            lead_lag_summary=lead_lag_summary,
            derivatives_summary=derivatives_summary,
            explanation_short=explanation_short,
            explanation_detailed=(
                f"{symbol} {timeframe} candidate selected from market snapshot context. "
                f"strategy_engine={selected_engine_name}, "
                f"priority={priority}, regime_fit={regime_fit:.3f}, expected_rr={expected_rr_ratio:.3f}, "
                f"expectancy={_safe_float(performance_summary.get('expectancy')):.3f}, "
                f"net_pnl_after_fees={_safe_float(performance_summary.get('net_pnl_after_fees')):.3f}, "
                f"avg_signed_slippage_bps={_safe_float(performance_summary.get('avg_signed_slippage_bps')):.3f}, "
                f"derivatives_alignment={derivatives_alignment:.3f}, lead_lag_alignment={lead_lag_alignment:.3f}, "
                f"derivatives_discount={side_discount_magnitude:.3f}."
            ),
        )
        score = TradeDecisionCandidateScore(
            regime_fit=round(regime_fit, 6),
            expected_rr=round(expected_rr, 6),
            recent_signal_performance=round(recent_signal_performance, 6),
            derivatives_alignment=round(derivatives_alignment, 6),
            lead_lag_alignment=round(lead_lag_alignment, 6),
            slippage_sensitivity=round(slippage_sensitivity, 6),
            exposure_impact=round(exposure_impact, 6),
            confidence_consistency=round(confidence_consistency, 6),
            correlation_penalty=0.0,
            total_score=round(base_total, 6),
        )
        return {
            "symbol": symbol,
            "priority": priority,
            "candidate": candidate,
            "score": score,
            "feature_payload": feature_payload,
            "regime_summary": {
                "primary_regime": regime.primary_regime,
                "trend_alignment": regime.trend_alignment,
                "weak_volume": regime.weak_volume,
                "momentum_weakening": regime.momentum_weakening,
            },
            "performance_summary": performance_summary,
            "strategy_engine": selected_engine_name,
            "strategy_engine_context": strategy_engine_selection.to_payload(),
            "holding_profile": holding_profile_context["holding_profile"],
            "holding_profile_reason": holding_profile_context["holding_profile_reason"],
            "holding_profile_context": holding_profile_context,
            "entry_mode": candidate_entry_mode,
            "late_long_filter": late_long_filter,
            "scenario_signature": f"{decision}:{scenario}:{regime.primary_regime}:{regime.trend_alignment}",
            "returns": _rolling_returns_from_snapshot(market_snapshot),
            "market_snapshot": market_snapshot,
        }

    @staticmethod
    def _selection_breadth_summary(candidate_rows: list[dict[str, object]]) -> dict[str, object]:
        breadth_inputs: list[object] = []
        decisions: dict[str, str] = {}
        for item in candidate_rows:
            symbol = str(item.get("symbol") or "").upper()
            candidate = item.get("candidate")
            decisions[symbol] = str(getattr(candidate, "decision", "") or "")
            feature_payload = item.get("feature_payload")
            if feature_payload is not None:
                breadth_inputs.append(feature_payload)
                continue
            regime_summary = item.get("regime_summary") if isinstance(item.get("regime_summary"), dict) else {}
            breadth_inputs.append({"symbol": symbol, **regime_summary})
        return summarize_universe_breadth(breadth_inputs, decisions=decisions)

    @staticmethod
    def _selection_capacity_plan(
        *,
        breadth_summary: dict[str, object],
        priority_count: int,
        candidate_count: int,
        drawdown_state: dict[str, object] | None = None,
    ) -> tuple[int, float, str, str | None]:
        breadth_regime = str(breadth_summary.get("breadth_regime") or "mixed")
        if breadth_regime == "weak_breadth":
            non_priority_capacity = 1
            entry_score_threshold = 0.48
            capacity_reason = "breadth_weak_reduce_capacity"
        elif breadth_regime == "transition_fragile":
            non_priority_capacity = 1
            entry_score_threshold = 0.45
            capacity_reason = "transition_fragile_reduce_capacity"
        elif breadth_regime == "trend_expansion":
            non_priority_capacity = 3
            entry_score_threshold = 0.36
            capacity_reason = "trend_expansion_allow_rotation"
        else:
            non_priority_capacity = 2
            entry_score_threshold = 0.4
            capacity_reason = "mixed_breadth_moderate_capacity"
        drawdown_capacity_reason: str | None = None
        drawdown_policy = (
            dict(drawdown_state.get("policy_adjustments") or {})
            if isinstance(drawdown_state, dict)
            else {}
        )
        drawdown_state_code = (
            str(drawdown_state.get("current_drawdown_state") or "normal")
            if isinstance(drawdown_state, dict)
            else "normal"
        )
        max_non_priority_selected = int(drawdown_policy.get("max_non_priority_selected") or non_priority_capacity)
        entry_score_threshold_uplift = _safe_float(drawdown_policy.get("entry_score_threshold_uplift"))
        if drawdown_state_code != "normal":
            adjusted_non_priority_capacity = min(non_priority_capacity, max_non_priority_selected)
            if adjusted_non_priority_capacity < non_priority_capacity:
                non_priority_capacity = adjusted_non_priority_capacity
                drawdown_capacity_reason = f"{drawdown_state_code}_reduce_capacity"
            if entry_score_threshold_uplift > 0:
                entry_score_threshold = round(min(entry_score_threshold + entry_score_threshold_uplift, 0.95), 6)
        max_selected = min(priority_count + non_priority_capacity, candidate_count)
        return max_selected, entry_score_threshold, capacity_reason, drawdown_capacity_reason

    @staticmethod
    def _candidate_overlap_reason(
        *,
        item: dict[str, object],
        selected_rows: list[dict[str, object]],
        max_abs_correlation: float,
        breadth_regime: str,
    ) -> str | None:
        candidate = item.get("candidate")
        decision = str(getattr(candidate, "decision", "") or "")
        scenario_signature = str(item.get("scenario_signature") or "")
        regime_summary = item.get("regime_summary") if isinstance(item.get("regime_summary"), dict) else {}
        trend_alignment = str(regime_summary.get("trend_alignment") or "unknown")
        scenario_duplicate = any(str(selected.get("scenario_signature") or "") == scenario_signature for selected in selected_rows)
        directional_duplicate = any(
            str(getattr(selected.get("candidate"), "decision", "") or "") == decision
            and str((selected.get("regime_summary") or {}).get("trend_alignment") or "unknown") == trend_alignment
            for selected in selected_rows
        )
        if max_abs_correlation >= 0.92 and selected_rows:
            return "correlation_limit"
        if scenario_duplicate and max_abs_correlation >= 0.68:
            return "duplicate_scenario_exposure"
        if breadth_regime in {"weak_breadth", "transition_fragile"} and directional_duplicate and max_abs_correlation >= 0.55:
            return "duplicate_directional_exposure"
        return None

    @staticmethod
    def _candidate_breadth_adjustment(
        *,
        item: dict[str, object],
        breadth_summary: dict[str, object],
    ) -> dict[str, object]:
        candidate = item.get("candidate")
        decision = str(getattr(candidate, "decision", "") or "")
        if bool(item.get("priority")) or decision not in {"long", "short"}:
            return {
                "score_multiplier": 1.0,
                "score_adjustment": 0.0,
                "hold_bias": 1.0,
                "reasons": [],
            }
        breadth_regime = str(breadth_summary.get("breadth_regime") or "mixed")
        directional_bias = str(breadth_summary.get("directional_bias") or "balanced")
        base_multiplier = float(breadth_summary.get("entry_score_multiplier") or 1.0)
        hold_bias = float(breadth_summary.get("hold_bias_multiplier") or 1.0)
        regime_summary = item.get("regime_summary") if isinstance(item.get("regime_summary"), dict) else {}
        primary_regime = str(regime_summary.get("primary_regime") or "unknown")
        trend_alignment = str(regime_summary.get("trend_alignment") or "unknown")
        weak_volume = bool(regime_summary.get("weak_volume", False))
        momentum_weakening = bool(regime_summary.get("momentum_weakening", False))
        aligned_with_bias = (
            (decision == "long" and directional_bias == "bullish")
            or (decision == "short" and directional_bias == "bearish")
        )

        score_multiplier = base_multiplier
        score_adjustment = 0.0
        reasons: list[str] = []
        if breadth_regime == "weak_breadth":
            if weak_volume or primary_regime in {"range", "transition"} or momentum_weakening:
                score_multiplier *= 0.86
                hold_bias = max(hold_bias, 1.25)
                reasons.append("weak_breadth_structure_penalty")
            if directional_bias != "balanced" and not aligned_with_bias:
                score_multiplier *= 0.88
                reasons.append("breadth_direction_mismatch")
        elif breadth_regime == "transition_fragile":
            if primary_regime in {"range", "transition"} or momentum_weakening:
                score_multiplier *= 0.92
                hold_bias = max(hold_bias, 1.12)
                reasons.append("transition_fragile_penalty")
        elif breadth_regime == "trend_expansion":
            if aligned_with_bias and not weak_volume and trend_alignment in {"bullish_aligned", "bearish_aligned"}:
                score_adjustment += 0.04
                hold_bias = min(hold_bias, 0.95)
                reasons.append("breadth_trend_alignment_boost")
            elif directional_bias != "balanced" and not aligned_with_bias:
                score_multiplier *= 0.95
                reasons.append("breadth_secondary_rotation_discount")

        return {
            "score_multiplier": round(score_multiplier, 6),
            "score_adjustment": round(score_adjustment, 6),
            "hold_bias": round(hold_bias, 6),
            "reasons": reasons,
        }

    @staticmethod
    def _breadth_alignment_score(
        *,
        breadth_summary: dict[str, object],
        breadth_adjustment: dict[str, object],
        decision: str,
    ) -> float:
        breadth_regime = str(breadth_summary.get("breadth_regime") or "mixed")
        directional_bias = str(breadth_summary.get("directional_bias") or "neutral")
        base = {
            "weak_breadth": 0.34,
            "transition_fragile": 0.42,
            "mixed": 0.56,
            "trend_expansion": 0.74,
        }.get(breadth_regime, 0.52)
        target_bias = "bullish" if decision == "long" else "bearish"
        if directional_bias == target_bias:
            base += 0.05
        elif directional_bias not in {"neutral", "mixed", "unknown", ""}:
            base -= 0.08
        base += float(breadth_adjustment.get("score_adjustment", 0.0)) * 2.5
        base *= float(breadth_adjustment.get("score_multiplier", 1.0))
        base -= max(float(breadth_adjustment.get("hold_bias", 1.0)) - 1.0, 0.0) * 0.18
        return _clamp_score(base)

    @staticmethod
    def _agreement_alignment_score(
        *,
        confidence_consistency: float,
        recent_signal_performance: float,
    ) -> tuple[float, str]:
        score = _clamp_score((confidence_consistency * 0.62) + (recent_signal_performance * 0.38))
        if score >= 0.7:
            return score, "full_agreement_likely"
        if score >= 0.56:
            return score, "partial_agreement_likely"
        return score, "disagreement_risk"

    @staticmethod
    def _execution_quality_score(
        *,
        slippage_sensitivity: float,
        performance_summary: dict[str, object],
    ) -> float:
        avg_signed_slippage_bps = max(_safe_float(performance_summary.get("avg_signed_slippage_bps")), 0.0)
        slippage_quality = _clamp_score(0.9 - (min(avg_signed_slippage_bps, 18.0) / 18.0 * 0.55))
        score = (slippage_sensitivity * 0.62) + (slippage_quality * 0.38)
        if bool(performance_summary.get("underperforming")) and avg_signed_slippage_bps >= 10.0:
            score -= 0.08
        return _clamp_score(score)

    def _candidate_meta_gate_probability(
        self,
        *,
        candidate: TradeDecisionCandidate,
        entry_mode: str,
        feature_payload: FeaturePayload | None,
        score_payload: TradeDecisionCandidateScore,
        performance_summary: dict[str, object],
        breadth_summary: dict[str, object],
    ) -> float:
        if feature_payload is None:
            fallback = (
                float(score_payload.total_score) * 0.42
                + _safe_float(performance_summary.get("score"), default=float(score_payload.total_score)) * 0.34
                + float(score_payload.lead_lag_alignment) * 0.12
                + float(score_payload.derivatives_alignment) * 0.12
            )
            return _clamp_score(fallback)

        candidate_decision = TradeDecision(
            decision=candidate.decision,
            confidence=candidate.confidence,
            symbol=candidate.symbol,
            timeframe=candidate.timeframe,
            entry_zone_min=candidate.entry_zone_min,
            entry_zone_max=candidate.entry_zone_max,
            entry_mode=entry_mode if entry_mode in {"breakout_confirm", "pullback_confirm", "immediate", "none"} else "none",
            invalidation_price=None,
            max_chase_bps=None,
            idea_ttl_minutes=None,
            stop_loss=candidate.stop_loss,
            take_profit=candidate.take_profit,
            max_holding_minutes=candidate.max_holding_minutes,
            risk_pct=candidate.risk_pct,
            leverage=candidate.leverage,
            rationale_codes=list(candidate.rationale_codes),
            explanation_short=candidate.explanation_short,
            explanation_detailed=candidate.explanation_detailed,
        )
        meta_gate_result = evaluate_meta_gate(
            candidate_decision,
            feature_payload=feature_payload,
            selection_context={
                "score": score_payload.model_dump(mode="json"),
                "performance_summary": performance_summary,
                "universe_breadth": breadth_summary,
            },
            decision_metadata={},
        )
        return float(meta_gate_result.expected_hit_probability)

    @staticmethod
    def _slot_conviction_score(
        *,
        expectancy_score: float,
        meta_gate_probability: float,
        agreement_alignment: float,
        breadth_alignment: float,
        lead_lag_alignment: float,
        execution_quality: float,
        correlation_penalty: float,
    ) -> float:
        score = (
            (expectancy_score * 0.32)
            + (meta_gate_probability * 0.24)
            + (agreement_alignment * 0.12)
            + (breadth_alignment * 0.12)
            + (lead_lag_alignment * 0.1)
            + (execution_quality * 0.1)
        ) - (correlation_penalty * 0.18)
        return _clamp_score(score)

    @staticmethod
    def _available_portfolio_slots(non_priority_capacity: int) -> list[str]:
        if non_priority_capacity <= 0:
            return []
        return list(PORTFOLIO_SLOT_ORDER[: min(non_priority_capacity, len(PORTFOLIO_SLOT_ORDER))])

    @staticmethod
    def _assign_portfolio_slot(
        *,
        slot_conviction_score: float,
        used_slots: set[str],
        available_slots: list[str],
    ) -> dict[str, object]:
        if not available_slots:
            return {
                "assigned_slot": None,
                "slot_label": "unassigned",
                "rejected_reason": "capacity_reached",
                "slot_reason": "slot_capacity_reached",
            }
        if (
            slot_conviction_score >= PORTFOLIO_SLOT_HIGH_CONVICTION_THRESHOLD
            and "slot_1" in available_slots
            and "slot_1" not in used_slots
        ):
            return {
                "assigned_slot": "slot_1",
                "slot_label": PORTFOLIO_SLOT_LABELS["slot_1"],
                "rejected_reason": None,
                "slot_reason": "high_conviction_slot",
            }
        if slot_conviction_score < PORTFOLIO_SLOT_MEDIUM_CONVICTION_THRESHOLD:
            return {
                "assigned_slot": None,
                "slot_label": "unassigned",
                "rejected_reason": "low_conviction_slot_excluded",
                "slot_reason": "slot_conviction_below_threshold",
            }
        for slot_name in available_slots:
            if slot_name == "slot_1":
                continue
            if slot_name not in used_slots:
                return {
                    "assigned_slot": slot_name,
                    "slot_label": PORTFOLIO_SLOT_LABELS[slot_name],
                    "rejected_reason": None,
                    "slot_reason": "medium_conviction_slot",
                }
        return {
            "assigned_slot": None,
            "slot_label": "unassigned",
            "rejected_reason": "capacity_reached",
            "slot_reason": "slot_capacity_reached",
        }

    @staticmethod
    def _slot_policy_profile(*, assigned_slot: str | None, portfolio_weight: float) -> dict[str, object]:
        if assigned_slot not in PORTFOLIO_SLOT_POLICY_BASE:
            return {
                "assigned_slot": assigned_slot,
                "slot_label": "unassigned",
                "candidate_weight": 0.0,
                "risk_pct_multiplier": 1.0,
                "leverage_multiplier": 1.0,
                "notional_multiplier": 1.0,
                "applies_soft_cap": False,
            }
        base_profile = PORTFOLIO_SLOT_POLICY_BASE[assigned_slot]
        weight_factor = _clamp_score(0.6 + portfolio_weight, lower=0.6, upper=1.0)
        return {
            "assigned_slot": assigned_slot,
            "slot_label": PORTFOLIO_SLOT_LABELS[assigned_slot],
            "candidate_weight": round(portfolio_weight, 6),
            "risk_pct_multiplier": round(float(base_profile["risk_pct_multiplier"]) * weight_factor, 6),
            "leverage_multiplier": round(float(base_profile["leverage_multiplier"]) * weight_factor, 6),
            "notional_multiplier": round(float(base_profile["notional_multiplier"]) * weight_factor, 6),
            "applies_soft_cap": True,
        }

    @staticmethod
    def _portfolio_weight_map(selected_rows: list[dict[str, object]]) -> dict[str, float]:
        weighted_rows = [
            row
            for row in selected_rows
            if not bool(row.get("priority"))
            and str(getattr(row.get("candidate"), "decision", "") or "") in {"long", "short"}
            and isinstance(row.get("slot_allocation"), dict)
            and str((row.get("slot_allocation") or {}).get("assigned_slot") or "") in PORTFOLIO_SLOT_BASE_WEIGHTS
        ]
        if not weighted_rows:
            return {}
        weighted_scores = {
            str(row.get("symbol")): (
                PORTFOLIO_SLOT_BASE_WEIGHTS[
                    str((row.get("slot_allocation") or {}).get("assigned_slot") or "slot_3")
                ]
                * max(_safe_float((row.get("slot_allocation") or {}).get("slot_conviction_score")), 0.0) ** 2
                * max(_safe_float((row.get("slot_allocation") or {}).get("meta_gate_probability"), default=0.25), 0.25)
            )
            for row in weighted_rows
        }
        denominator = sum(weighted_scores.values())
        if denominator <= 0:
            equal_weight = round(1.0 / len(weighted_rows), 6)
            return {str(row.get("symbol")): equal_weight for row in weighted_rows}
        return {
            symbol: round(score / denominator, 6)
            for symbol, score in weighted_scores.items()
        }

    def _rank_candidate_symbols(
        self,
        *,
        decision_symbols: list[str],
        timeframe: str | None,
        upto_index: int | None,
        force_stale: bool,
    ) -> dict[str, object]:
        generated_at = utcnow_naive()
        drawdown_state = self._sync_drawdown_state(now=generated_at)
        if not self.settings_row.ai_enabled:
            set_candidate_selection_detail(
                self.settings_row,
                generated_at=generated_at,
                mode="disabled_ai_off",
                max_selected=len(decision_symbols),
                current_drawdown_state=str(drawdown_state.get("current_drawdown_state") or "normal"),
                drawdown_entered_at=_coerce_datetime(drawdown_state.get("entered_at")),
                drawdown_transition_reason=str(drawdown_state.get("transition_reason") or "") or None,
                drawdown_policy_adjustments=dict(drawdown_state.get("policy_adjustments") or {}),
                selected_symbols=decision_symbols,
                skipped_symbols=[],
                rankings=[],
            )
            self.session.add(self.settings_row)
            self.session.flush()
            return {
                "mode": "disabled_ai_off",
                "drawdown_state": drawdown_state,
                "selected_symbols": decision_symbols,
                "skipped_symbols": [],
                "rankings": [],
            }

        runtime_state = summarize_runtime_state(self.settings_row)
        missing_protection_symbols = {
            str(item).upper()
            for item in runtime_state.get("missing_protection_symbols", [])
            if item
        }
        total_open_positions = len(get_open_positions(self.session))
        lead_market_features = self._build_lead_market_features(
            base_timeframe=timeframe or self.settings_row.default_timeframe,
            upto_index=upto_index,
            force_stale=force_stale,
        )
        candidate_rows: list[dict[str, object]] = []
        for symbol in decision_symbols:
            effective_timeframe = timeframe or self._effective_symbol_settings(symbol).timeframe
            try:
                candidate_rows.append(
                    self._build_selection_candidate(
                        symbol=symbol,
                        timeframe=effective_timeframe,
                        upto_index=upto_index,
                        force_stale=force_stale,
                        missing_protection_symbols=missing_protection_symbols,
                        total_open_positions=total_open_positions,
                        lead_market_features=lead_market_features,
                    )
                )
            except Exception as exc:
                fallback_reason = " ".join(str(exc).split())
                if len(fallback_reason) > 420:
                    fallback_reason = f"{fallback_reason[:417]}..."
                fallback_candidate = TradeDecisionCandidate(
                    candidate_id=f"{symbol}:{effective_timeframe}:fallback",
                    scenario="hold",
                    decision="hold",
                    symbol=symbol,
                    timeframe=effective_timeframe,
                    confidence=0.0,
                    entry_zone_min=None,
                    entry_zone_max=None,
                    stop_loss=None,
                    take_profit=None,
                    max_holding_minutes=60,
                    risk_pct=min(self.settings_row.max_risk_per_trade, HARD_MAX_RISK_PER_TRADE),
                    leverage=1.0,
                    rationale_codes=["CANDIDATE_SELECTION_FALLBACK"],
                    explanation_short="후보 선별 fallback",
                    explanation_detailed=(
                        "Candidate selection fell back because market context collection failed: "
                        f"{fallback_reason}"
                    ),
                )
                candidate_rows.append(
                    {
                        "symbol": symbol,
                        "priority": False,
                        "candidate": fallback_candidate,
                        "score": TradeDecisionCandidateScore(total_score=0.15),
                        "regime_summary": {
                            "primary_regime": "unknown",
                            "trend_alignment": "unknown",
                            "weak_volume": False,
                            "momentum_weakening": False,
                        },
                        "performance_summary": {
                            "score": 0.35,
                            "sample_size": 0,
                            "hit_rate": 0.0,
                            "expectancy": 0.0,
                            "net_pnl_after_fees": 0.0,
                            "avg_signed_slippage_bps": 0.0,
                            "loss_streak": 0,
                            "underperforming": False,
                            "components": {},
                        },
                        "scenario_signature": "hold:hold:unknown:unknown",
                        "returns": [],
                        "market_snapshot": None,
                    }
                )

        candidate_rows.sort(
            key=lambda item: (
                bool(item.get("priority")),
                float(getattr(item.get("score"), "total_score", 0.0)),
            ),
            reverse=True,
        )

        breadth_summary = self._selection_breadth_summary(candidate_rows)
        priority_count = len([item for item in candidate_rows if bool(item.get("priority"))])
        max_selected, entry_score_threshold, capacity_reason, drawdown_capacity_reason = self._selection_capacity_plan(
            breadth_summary=breadth_summary,
            priority_count=priority_count,
            candidate_count=len(candidate_rows),
            drawdown_state=drawdown_state,
        )
        non_priority_capacity = max(max_selected - priority_count, 0)
        available_slots = self._available_portfolio_slots(non_priority_capacity)
        breadth_regime = str(breadth_summary.get("breadth_regime") or "mixed")
        selected_symbols: list[str] = []
        selected_rows: list[dict[str, object]] = []
        ranking_payloads: list[dict[str, object]] = []
        skipped_symbols: list[str] = []
        used_slots: set[str] = set()

        for item in candidate_rows:
            candidate = item["candidate"]
            score = item["score"]
            symbol = str(item["symbol"])
            returns = item["returns"]
            priority = bool(item.get("priority"))
            performance_summary = _as_dict(item.get("performance_summary"))
            late_long_filter = _as_dict(item.get("late_long_filter"))
            breadth_adjustment = self._candidate_breadth_adjustment(
                item=item,
                breadth_summary=breadth_summary,
            )
            adjusted_total = (
                float(score.total_score) * float(breadth_adjustment.get("score_multiplier", 1.0))
                + float(breadth_adjustment.get("score_adjustment", 0.0))
            )
            max_abs_correlation = 0.0
            for selected in selected_rows:
                correlation = abs(_pearson_correlation(returns, selected["returns"])) if returns and selected["returns"] else 0.0
                max_abs_correlation = max(max_abs_correlation, correlation)
            correlation_penalty = round(max(0.0, max_abs_correlation - 0.55) * 0.9, 6)
            selected_flag = False
            selection_reason = "capacity_reached"
            rejected_reason: str | None = None
            performance_score = _safe_float(performance_summary.get("score"), default=float(score.total_score))
            performance_sample_size = int(performance_summary.get("sample_size", 0) or 0)
            avg_signed_slippage_bps = _safe_float(performance_summary.get("avg_signed_slippage_bps"))
            underperforming_expectancy = bool(performance_summary.get("underperforming"))
            duplicate_exposure_reason = self._candidate_overlap_reason(
                item=item,
                selected_rows=selected_rows,
                max_abs_correlation=max_abs_correlation,
                breadth_regime=breadth_regime,
            )
            breadth_alignment = self._breadth_alignment_score(
                breadth_summary=breadth_summary,
                breadth_adjustment=breadth_adjustment,
                decision=str(candidate.decision),
            )
            agreement_alignment, agreement_level_hint = self._agreement_alignment_score(
                confidence_consistency=float(score.confidence_consistency),
                recent_signal_performance=float(score.recent_signal_performance),
            )
            execution_quality = self._execution_quality_score(
                slippage_sensitivity=float(score.slippage_sensitivity),
                performance_summary=performance_summary,
            )
            slot_score_payload = score.model_copy(update={"total_score": round(max(adjusted_total, 0.0), 6)})
            meta_gate_probability = self._candidate_meta_gate_probability(
                candidate=candidate,
                entry_mode=str(item.get("entry_mode") or "none"),
                feature_payload=item.get("feature_payload") if isinstance(item.get("feature_payload"), FeaturePayload) else None,
                score_payload=slot_score_payload,
                performance_summary=performance_summary,
                breadth_summary=breadth_summary,
            )
            slot_conviction_score = self._slot_conviction_score(
                expectancy_score=float(score.recent_signal_performance),
                meta_gate_probability=meta_gate_probability,
                agreement_alignment=agreement_alignment,
                breadth_alignment=breadth_alignment,
                lead_lag_alignment=float(score.lead_lag_alignment),
                execution_quality=execution_quality,
                correlation_penalty=correlation_penalty,
            )
            score.meta_gate_probability = round(meta_gate_probability, 6)
            score.agreement_alignment = round(agreement_alignment, 6)
            score.execution_quality = round(execution_quality, 6)
            score.slot_conviction = round(slot_conviction_score, 6)
            if priority:
                selected_flag = True
                selection_reason = "priority_position_or_protection"
            elif str(candidate.decision) not in {"long", "short"}:
                rejected_reason = "low_edge_hold_candidate"
            elif bool(late_long_filter.get("active")):
                rejected_reason = ENTRY_CANDIDATE_LATE_LONG_SKIP_REASON
            elif underperforming_expectancy:
                rejected_reason = "underperforming_expectancy_bucket"
            elif performance_sample_size >= SETUP_CLUSTER_MIN_SAMPLE_SIZE and performance_score < 0.42:
                rejected_reason = "expectancy_below_threshold"
            elif (
                performance_sample_size >= SETUP_CLUSTER_MIN_SAMPLE_SIZE
                and avg_signed_slippage_bps >= SETUP_CLUSTER_SIGNED_SLIPPAGE_BPS_THRESHOLD
                and performance_score < 0.5
            ):
                rejected_reason = "adverse_signed_slippage"
            elif duplicate_exposure_reason is not None:
                rejected_reason = duplicate_exposure_reason
            elif len(selected_rows) >= max_selected:
                rejected_reason = "capacity_reached"
            else:
                adjusted_total = adjusted_total - correlation_penalty
                if (
                    float(breadth_adjustment.get("hold_bias", 1.0)) >= 1.15
                    and adjusted_total < (entry_score_threshold + 0.03)
                ):
                    rejected_reason = "breadth_hold_bias"
                elif adjusted_total >= entry_score_threshold:
                    slot_allocation = self._assign_portfolio_slot(
                        slot_conviction_score=slot_conviction_score,
                        used_slots=used_slots,
                        available_slots=available_slots,
                    )
                    assigned_slot = str(slot_allocation.get("assigned_slot") or "")
                    if assigned_slot:
                        selected_flag = True
                        selection_reason = "ranked_portfolio_focus"
                        used_slots.add(assigned_slot)
                        item["slot_allocation"] = {
                            **slot_allocation,
                            "slot_conviction_score": round(slot_conviction_score, 6),
                            "meta_gate_probability": round(meta_gate_probability, 6),
                            "agreement_alignment_score": round(agreement_alignment, 6),
                            "agreement_level_hint": agreement_level_hint,
                            "execution_quality_score": round(execution_quality, 6),
                            "breadth_alignment_score": round(breadth_alignment, 6),
                        }
                    else:
                        rejected_reason = str(slot_allocation.get("rejected_reason") or "capacity_reached")
                else:
                    rejected_reason = "score_below_threshold"
            score.correlation_penalty = correlation_penalty
            score.total_score = round(adjusted_total, 6)
            slot_allocation_payload = (
                dict(item.get("slot_allocation"))
                if isinstance(item.get("slot_allocation"), dict)
                else {
                    "assigned_slot": "priority_management" if priority and selected_flag else None,
                    "slot_label": "priority_management" if priority and selected_flag else "unassigned",
                    "slot_reason": "priority_position_or_protection" if priority and selected_flag else None,
                    "slot_conviction_score": round(slot_conviction_score, 6),
                    "meta_gate_probability": round(meta_gate_probability, 6),
                    "agreement_alignment_score": round(agreement_alignment, 6),
                    "agreement_level_hint": agreement_level_hint,
                    "execution_quality_score": round(execution_quality, 6),
                    "breadth_alignment_score": round(breadth_alignment, 6),
                }
            )
            ranking_payload = {
                "symbol": symbol,
                "priority": priority,
                "selected": selected_flag,
                "selected_reason": selection_reason if selected_flag else None,
                "selection_reason": selection_reason if selected_flag else (rejected_reason or selection_reason),
                "rejected_reason": rejected_reason if not selected_flag else None,
                "max_abs_correlation": round(max_abs_correlation, 6),
                "breadth_regime": breadth_regime,
                "capacity_reason": capacity_reason,
                "drawdown_capacity_reason": drawdown_capacity_reason,
                "current_drawdown_state": str(drawdown_state.get("current_drawdown_state") or "normal"),
                "entry_score_threshold": round(entry_score_threshold, 6),
                "breadth_score_multiplier": breadth_adjustment.get("score_multiplier"),
                "breadth_score_adjustment": breadth_adjustment.get("score_adjustment"),
                "breadth_hold_bias": breadth_adjustment.get("hold_bias"),
                "breadth_adjustment_reasons": breadth_adjustment.get("reasons"),
                "assigned_slot": slot_allocation_payload.get("assigned_slot"),
                "slot_label": slot_allocation_payload.get("slot_label"),
                "slot_reason": slot_allocation_payload.get("slot_reason"),
                "slot_conviction_score": slot_allocation_payload.get("slot_conviction_score"),
                "meta_gate_probability": slot_allocation_payload.get("meta_gate_probability"),
                "agreement_alignment_score": slot_allocation_payload.get("agreement_alignment_score"),
                "agreement_level_hint": slot_allocation_payload.get("agreement_level_hint"),
                "execution_quality_score": slot_allocation_payload.get("execution_quality_score"),
                "breadth_alignment_score": slot_allocation_payload.get("breadth_alignment_score"),
                "performance_summary": performance_summary,
                "regime_summary": dict(item.get("regime_summary") or {}),
                "scenario_signature": item.get("scenario_signature"),
                "entry_mode": item.get("entry_mode"),
                "strategy_engine": item.get("strategy_engine"),
                "strategy_engine_context": item.get("strategy_engine_context"),
                "holding_profile": item.get("holding_profile"),
                "holding_profile_reason": item.get("holding_profile_reason"),
                "holding_profile_context": item.get("holding_profile_context"),
                "late_long_filter": late_long_filter if late_long_filter.get("active") else None,
                "candidate": candidate.model_dump(mode="json"),
                "score": score.model_dump(mode="json"),
            }
            ranking_payloads.append(ranking_payload)
            if selected_flag:
                selected_symbols.append(symbol)
                selected_rows.append(item)
            else:
                skipped_symbols.append(symbol)
                self._record_selection_skip_event(
                    symbol=symbol,
                    timeframe=str(getattr(candidate, "timeframe", "") or ""),
                    item=item,
                    ranking_payload=ranking_payload,
                )

        portfolio_weights = self._portfolio_weight_map(selected_rows)
        slot_assignments: dict[str, dict[str, object]] = {}
        allocator_payload = {
            "allocator_mode": "slot_weighted_rotation",
            "slot_mode": "conviction_slots",
            "slot_plan": {
                "available_slots": available_slots,
                "high_conviction_threshold": PORTFOLIO_SLOT_HIGH_CONVICTION_THRESHOLD,
                "medium_conviction_threshold": PORTFOLIO_SLOT_MEDIUM_CONVICTION_THRESHOLD,
                "low_conviction_action": "exclude",
            },
            "selected_entry_symbols": [symbol for symbol, weight in portfolio_weights.items() if weight > 0.0],
            "weights": portfolio_weights,
        }
        for ranking_payload in ranking_payloads:
            symbol = str(ranking_payload.get("symbol") or "")
            priority = bool(ranking_payload.get("priority"))
            portfolio_weight = portfolio_weights.get(symbol, 0.0)
            ranking_payload["portfolio_weight"] = portfolio_weight
            ranking_payload["candidate_weight"] = portfolio_weight
            slot_policy = self._slot_policy_profile(
                assigned_slot=str(ranking_payload.get("assigned_slot") or "") or None,
                portfolio_weight=portfolio_weight,
            )
            ranking_payload["slot_risk_pct_multiplier"] = slot_policy.get("risk_pct_multiplier")
            ranking_payload["slot_leverage_multiplier"] = slot_policy.get("leverage_multiplier")
            ranking_payload["slot_notional_multiplier"] = slot_policy.get("notional_multiplier")
            ranking_payload["slot_applies_soft_cap"] = slot_policy.get("applies_soft_cap")
            ranking_payload["weight_reason"] = (
                "management_priority_unweighted"
                if priority and ranking_payload.get("selected")
                else "slot_weighted_rotation"
                if ranking_payload.get("selected")
                else "not_selected"
            )
            assigned_slot = str(ranking_payload.get("assigned_slot") or "")
            if assigned_slot in PORTFOLIO_SLOT_LABELS and ranking_payload.get("selected"):
                slot_assignments[assigned_slot] = {
                    "label": PORTFOLIO_SLOT_LABELS[assigned_slot],
                    "symbol": symbol,
                    "candidate_weight": portfolio_weight,
                    "slot_conviction_score": ranking_payload.get("slot_conviction_score"),
                    "meta_gate_probability": ranking_payload.get("meta_gate_probability"),
                }
        allocator_payload["slot_assignments"] = slot_assignments

        self._close_idle_transaction_before_candidate_selection_write()
        set_candidate_selection_detail(
            self.settings_row,
            generated_at=generated_at,
            mode="portfolio_rotation_top_n",
            max_selected=max_selected,
            breadth_regime=breadth_regime,
            breadth_summary=breadth_summary,
            capacity_reason=capacity_reason,
            entry_score_threshold=entry_score_threshold,
            portfolio_allocator=allocator_payload,
            current_drawdown_state=str(drawdown_state.get("current_drawdown_state") or "normal"),
            drawdown_entered_at=_coerce_datetime(drawdown_state.get("entered_at")),
            drawdown_transition_reason=str(drawdown_state.get("transition_reason") or "") or None,
            drawdown_policy_adjustments=dict(drawdown_state.get("policy_adjustments") or {}),
            drawdown_capacity_reason=drawdown_capacity_reason,
            selected_symbols=selected_symbols,
            skipped_symbols=skipped_symbols,
            rankings=ranking_payloads,
        )
        self.session.add(self.settings_row)
        self.session.flush()
        record_audit_event(
            self.session,
            event_type="candidate_selection_ranked",
            entity_type="symbol_batch",
            entity_id="tracked_symbols",
            severity="info",
            message="Portfolio rotation candidate ranking completed for tracked symbols.",
            payload={
                "mode": "portfolio_rotation_top_n",
                "max_selected": max_selected,
                "breadth_regime": breadth_regime,
                "breadth_summary": breadth_summary,
                "capacity_reason": capacity_reason,
                "drawdown_capacity_reason": drawdown_capacity_reason,
                "entry_score_threshold": entry_score_threshold,
                "drawdown_state": drawdown_state,
                "portfolio_allocator": allocator_payload,
                "selected_symbols": selected_symbols,
                "skipped_symbols": skipped_symbols,
                "rankings": ranking_payloads,
            },
        )
        return {
            "mode": "portfolio_rotation_top_n",
            "max_selected": max_selected,
            "breadth_regime": breadth_regime,
            "breadth_summary": breadth_summary,
            "capacity_reason": capacity_reason,
            "drawdown_capacity_reason": drawdown_capacity_reason,
            "entry_score_threshold": entry_score_threshold,
            "drawdown_state": drawdown_state,
            "portfolio_allocator": allocator_payload,
            "selected_symbols": selected_symbols,
            "skipped_symbols": skipped_symbols,
            "rankings": ranking_payloads,
        }

    def run_market_refresh_cycle(
        self,
        *,
        symbols: list[str] | None = None,
        timeframe: str | None = None,
        upto_index: int | None = None,
        force_stale: bool = False,
        status: str = "market_refresh",
        trigger_event: str = TriggerEvent.MANUAL.value,
        auto_resume_checked: bool = False,
        include_exchange_sync: bool = False,
    ) -> dict[str, object]:
        auto_resume_result = self._ensure_auto_resume(
            trigger_event=trigger_event,
            auto_resume_checked=auto_resume_checked,
        )
        exchange_sync_result: dict[str, object] | None = None
        if include_exchange_sync and self._should_poll_exchange_state(trigger_event):
            exchange_sync_result = self.run_exchange_sync_cycle(trigger_event=trigger_event)
        selected_symbols = [item.upper() for item in symbols] if symbols else get_effective_symbols(self.settings_row)
        results: list[dict[str, object]] = []
        event_context_cycle_owner = self._begin_event_context_cycle(
            f"market-refresh:{trigger_event}:{timeframe or 'effective'}:{utcnow_naive().isoformat()}"
        )
        try:
            for symbol in selected_symbols:
                effective_settings = self._effective_symbol_settings(symbol)
                effective_timeframe = timeframe or effective_settings.timeframe
                market_snapshot, market_row = self._collect_market_snapshot(
                    symbol=symbol,
                    timeframe=effective_timeframe,
                    upto_index=upto_index,
                    force_stale=force_stale,
                )
                feature_row: FeatureSnapshot | None = None
                feature_generation_error: str | None = None
                try:
                    market_context = build_market_context(
                        symbol=symbol,
                        base_timeframe=effective_timeframe,
                        upto_index=upto_index,
                        force_stale=force_stale,
                        use_binance=self.settings_row.binance_market_data_enabled,
                        binance_testnet_enabled=self.settings_row.binance_testnet_enabled,
                        stale_threshold_seconds=self.settings_row.stale_market_seconds,
                        event_context_provider=self.event_context_provider,
                    )
                    higher_timeframe_context = {
                        tf: payload for tf, payload in market_context.items() if tf != effective_timeframe
                    }
                    feature_payload = compute_features(market_snapshot, higher_timeframe_context)
                    feature_row = persist_feature_snapshot(self.session, market_row.id, market_snapshot, feature_payload)
                except Exception as exc:
                    feature_generation_error = str(exc)
                results.append(
                    {
                        "symbol": symbol,
                        "timeframe": effective_timeframe,
                        "market_snapshot_id": market_row.id,
                        "feature_snapshot_id": feature_row.id if feature_row is not None else None,
                        "feature_generation_error": feature_generation_error,
                        "snapshot_time": market_snapshot.snapshot_time.isoformat(),
                        "latest_price": market_snapshot.latest_price,
                        "status": status,
                    }
                )
        finally:
            self._end_event_context_cycle(event_context_cycle_owner)
        return {
            "symbols": selected_symbols,
            "cycles": len(results),
            "mode": status,
            "results": results,
            "account": self._account_snapshot_preview(),
            "settings": serialize_settings(self.settings_row),
            "auto_resume": auto_resume_result,
            "exchange_sync": exchange_sync_result,
        }

    def _record_position_exit_review(
        self,
        *,
        position: Position,
        market_snapshot: MarketSnapshotPayload,
        feature_payload: FeaturePayload,
        position_management_context: dict[str, object],
        trigger_event: str,
    ) -> dict[str, object] | None:
        if not self.settings_row.ai_enabled:
            return None
        if position.status != "open" or (position.quantity or 0) <= 0:
            return None

        provider_name = str(getattr(self.position_exit_review_agent.provider, "name", "deterministic-mock"))
        gate_metadata: dict[str, object] = {
            "allowed": provider_name != "openai",
            "reason": "provider_not_openai_or_mock" if provider_name != "openai" else "not_checked",
        }
        use_ai = provider_name == "openai"
        if provider_name == "openai":
            gate = get_openai_call_gate(
                self.session,
                self.settings_row,
                AgentRole.POSITION_EXIT_REVIEW.value,
                trigger_event,
                has_openai_key=bool(self.credentials.openai_api_key),
                symbol=position.symbol,
            )
            gate_metadata = gate.as_metadata()
            use_ai = gate.allowed

        sync_freshness_summary = build_sync_freshness_summary(self.settings_row)
        review, provider_label, metadata, input_payload = self.position_exit_review_agent.run(
            market_snapshot=market_snapshot,
            features=feature_payload,
            position=position,
            position_management_context=dict(position_management_context),
            sync_freshness_summary=sync_freshness_summary,
            use_ai=use_ai,
        )
        metadata.update(
            {
                "role": AgentRole.POSITION_EXIT_REVIEW.value,
                "symbol": position.symbol,
                "position_id": position.id,
                "trigger_event": trigger_event,
                "gate": gate_metadata,
                "advisory_only": True,
                "execution_boundary": "metadata_only_no_order_authority",
                "order_execution_connected": False,
                "stop_loss_changed": False,
                "protective_order_changed": False,
                "new_entry_mixed": False,
            }
        )
        review_payload = review.model_dump(mode="json")
        metadata.setdefault("position_exit_review", review_payload)
        run_row = persist_agent_run(
            self.session,
            AgentRole.POSITION_EXIT_REVIEW,
            trigger_event,
            input_payload,
            review,
            provider_name=provider_label,
            metadata_json=metadata,
            status="completed" if bool(gate_metadata.get("allowed", True)) else "skipped",
        )
        review_payload_with_source = {
            **review_payload,
            "agent_run_id": run_row.id,
            "provider_name": provider_label,
            "generated_at": run_row.created_at.isoformat() if run_row.created_at is not None else None,
        }
        position_metadata = dict(position.metadata_json) if isinstance(position.metadata_json, dict) else {}
        position_metadata["position_exit_review"] = review_payload_with_source
        position_metadata["position_exit_review_source"] = {
            "agent_run_id": run_row.id,
            "provider_name": provider_label,
            "trigger_event": trigger_event,
            "gate": gate_metadata,
            "advisory_only": True,
            "execution_boundary": "metadata_only_no_order_authority",
        }
        position.metadata_json = position_metadata
        self.session.add(position)
        self.session.flush()
        return review_payload_with_source

    def run_position_management_cycle(
        self,
        *,
        symbol: str,
        timeframe: str | None = None,
        upto_index: int | None = None,
        force_stale: bool = False,
        trigger_event: str = TriggerEvent.MANUAL.value,
    ) -> dict[str, object]:
        symbol = symbol.upper()
        effective_settings = self._effective_symbol_settings(symbol)
        effective_timeframe = timeframe or effective_settings.timeframe
        open_positions = get_open_positions(self.session, symbol)
        if not open_positions:
            return {
                "symbol": symbol,
                "timeframe": effective_timeframe,
                "status": "no_open_position",
                "new_entries_allowed": False,
                "execution": None,
            }
        market_snapshot, market_row = self._collect_market_snapshot(
            symbol=symbol,
            timeframe=effective_timeframe,
            upto_index=upto_index,
            force_stale=force_stale,
        )
        market_context = build_market_context(
            symbol=symbol,
            base_timeframe=effective_timeframe,
            upto_index=upto_index,
            force_stale=force_stale,
            use_binance=self.settings_row.binance_market_data_enabled,
            binance_testnet_enabled=self.settings_row.binance_testnet_enabled,
            stale_threshold_seconds=self.settings_row.stale_market_seconds,
            event_context_provider=self.event_context_provider,
        )
        higher_timeframe_context = {
            tf: payload for tf, payload in market_context.items() if tf != effective_timeframe
        }
        feature_payload = compute_features(market_snapshot, higher_timeframe_context)
        feature_row = persist_feature_snapshot(self.session, market_row.id, market_snapshot, feature_payload)
        managed_position = open_positions[0]
        position_management_context = build_position_management_context(
            managed_position,
            feature_payload=feature_payload,
            settings_row=self.settings_row,
        )
        position_exit_review = self._record_position_exit_review(
            position=managed_position,
            market_snapshot=market_snapshot,
            feature_payload=feature_payload,
            position_management_context=position_management_context,
            trigger_event=trigger_event,
        )
        result = apply_position_management(
            self.session,
            self.settings_row,
            symbol=symbol,
            feature_payload=feature_payload,
        )
        return {
            "symbol": symbol,
            "timeframe": effective_timeframe,
            "market_snapshot_id": market_row.id,
            "feature_snapshot_id": feature_row.id,
            "status": str(result.get("status", "monitoring")),
            "new_entries_allowed": False,
            "execution": result.get("position_management_action"),
            "position_management": result,
            "position_exit_review": position_exit_review,
            "trigger_event": trigger_event,
        }

    def run_entry_plan_watcher_cycle(
        self,
        *,
        symbols: list[str] | None = None,
        trigger_event: str = "entry_plan_watcher",
        upto_index: int | None = None,
        force_stale: bool = False,
        auto_resume_checked: bool = False,
        exchange_sync_checked: bool = False,
        market_snapshot_override: MarketSnapshotPayload | None = None,
    ) -> dict[str, object]:
        auto_resume_result = self._ensure_auto_resume(
            trigger_event=trigger_event,
            auto_resume_checked=auto_resume_checked,
        )
        watched_symbols = (
            [item.upper() for item in symbols if item]
            if symbols
            else sorted({plan.symbol for plan in self._active_pending_entry_plans()})
        )
        results: list[dict[str, object]] = []
        generated_at = utcnow_naive()
        for symbol in watched_symbols:
            active_plans = self._active_pending_entry_plans(symbol=symbol)
            if not active_plans:
                continue
            exchange_sync_result: dict[str, object] | None = None
            if self._should_poll_exchange_state(trigger_event) and not exchange_sync_checked:
                exchange_sync_result = self.run_exchange_sync_cycle(symbol=symbol, trigger_event=trigger_event)
            market_snapshot, market_row = (
                (market_snapshot_override, persist_market_snapshot(self.session, market_snapshot_override))
                if market_snapshot_override is not None
                else self._collect_market_snapshot(
                    symbol=symbol,
                    timeframe=ENTRY_PLAN_WATCH_TIMEFRAME,
                    upto_index=upto_index,
                    force_stale=force_stale,
                )
            )
            runtime_state = summarize_runtime_state(self.settings_row)
            open_positions = get_open_positions(self.session, symbol)
            cadence_profile = self.get_symbol_cadence_profile(
                symbol=symbol,
                timeframe=ENTRY_PLAN_WATCH_TIMEFRAME,
                runtime_state=runtime_state,
                open_positions=open_positions,
                armed_plans=active_plans,
            )
            operational_status = build_operational_status_payload(
                self.settings_row,
                session=self.session,
                runtime_state=runtime_state,
            )
            stale_scopes = [
                scope
                for scope in ("account", "positions", "open_orders", "protective_orders")
                if isinstance(operational_status.sync_freshness_summary.get(scope), dict)
                and (
                    bool(operational_status.sync_freshness_summary[scope].get("stale"))
                    or bool(operational_status.sync_freshness_summary[scope].get("incomplete"))
                )
            ]
            market_stale_reason_codes: list[str] = []
            if market_snapshot.is_stale:
                market_stale_reason_codes.append("MARKET_STATE_STALE")
            if not market_snapshot.is_complete:
                market_stale_reason_codes.append("MARKET_STATE_INCOMPLETE")
            symbol_missing_protection = symbol in runtime_state["missing_protection_symbols"]
            protection_issue = (
                operational_status.operating_state == PROTECTION_REQUIRED_STATE
                or symbol_missing_protection
            )
            symbol_verification_blocked = symbol in runtime_state.get("protection_verification_blocked_symbols", [])
            entry_control_blocked, entry_control_blocked_reasons = self._entry_plan_control_block(
                operational_status
            )
            entry_exchange_permission_pre_ai_block = self._risk_adding_exchange_permission_pre_ai_block(
                intent_type="entry"
            )
            entry_capacity_risk_budget: dict[str, object] | None = None
            symbol_results: list[dict[str, object]] = []
            for plan in active_plans:
                metadata = self._pending_entry_plan_metadata(plan)
                result_item: dict[str, object] = {
                    "plan": self._pending_entry_plan_snapshot(plan).model_dump(mode="json"),
                    "status": "armed",
                    "execution": None,
                    "risk_result": None,
                }
                if symbol_missing_protection or symbol_verification_blocked:
                    self._cancel_pending_entry_plan(
                        plan,
                        reason="PLAN_CANCELED_PROTECTION_BLOCK",
                        detail={
                            "operating_state": operational_status.operating_state,
                            "symbol_missing_protection": symbol_missing_protection,
                            "symbol_verification_blocked": symbol_verification_blocked,
                        },
                    )
                    result_item["status"] = "canceled"
                    symbol_results.append(result_item)
                    continue
                if protection_issue:
                    self._defer_pending_entry_plan(
                        plan,
                        reason="PLAN_WAITING_FOR_PROTECTION_STATE",
                        generated_at=generated_at,
                        market_row_id=market_row.id,
                        detail={
                            "operating_state": operational_status.operating_state,
                            "missing_protection_symbols": list(runtime_state["missing_protection_symbols"]),
                        },
                    )
                    result_item["plan"] = self._pending_entry_plan_snapshot(plan).model_dump(mode="json")
                    result_item["status"] = "armed_waiting_protection_state"
                    result_item["blocked_reasons"] = ["PLAN_WAITING_FOR_PROTECTION_STATE"]
                    symbol_results.append(result_item)
                    continue
                if stale_scopes:
                    if (
                        pending_entry_plan_is_expired(plan.expires_at, now=generated_at)
                        and "PLAN_WAITING_FOR_FRESH_SYNC"
                        in (metadata.get("last_watch_blocked_reason_codes") or [])
                    ):
                        self._cancel_pending_entry_plan(
                            plan,
                            reason="PLAN_TTL_EXPIRED",
                            cancel_status="expired",
                            detail={
                                "observed_at": generated_at.isoformat(),
                                "expired_while_waiting_for_fresh_sync": True,
                                "stale_scopes": list(stale_scopes),
                            },
                        )
                        result_item["status"] = "expired"
                        symbol_results.append(result_item)
                        continue
                    self._defer_pending_entry_plan(
                        plan,
                        reason="PLAN_WAITING_FOR_FRESH_SYNC",
                        generated_at=generated_at,
                        market_row_id=market_row.id,
                        detail={"stale_scopes": list(stale_scopes)},
                    )
                    result_item["plan"] = self._pending_entry_plan_snapshot(plan).model_dump(mode="json")
                    result_item["status"] = "armed_waiting_sync"
                    result_item["blocked_reasons"] = ["PLAN_WAITING_FOR_FRESH_SYNC"]
                    result_item["stale_scopes"] = list(stale_scopes)
                    symbol_results.append(result_item)
                    continue
                if market_stale_reason_codes:
                    self._defer_pending_entry_plan(
                        plan,
                        reason="PLAN_WAITING_FOR_FRESH_MARKET",
                        generated_at=generated_at,
                        market_row_id=market_row.id,
                        detail={
                            "market_reason_codes": list(market_stale_reason_codes),
                            "market_snapshot_time": market_snapshot.snapshot_time.isoformat(),
                            "market_snapshot_stale": market_snapshot.is_stale,
                            "market_snapshot_incomplete": not market_snapshot.is_complete,
                        },
                    )
                    result_item["plan"] = self._pending_entry_plan_snapshot(plan).model_dump(mode="json")
                    result_item["status"] = "armed_waiting_market"
                    result_item["blocked_reasons"] = ["PLAN_WAITING_FOR_FRESH_MARKET"]
                    result_item["market_reason_codes"] = list(market_stale_reason_codes)
                    symbol_results.append(result_item)
                    continue
                if entry_capacity_risk_budget is None:
                    latest_pnl = get_latest_pnl_snapshot(self.session, self.settings_row)
                    entry_capacity_risk_budget = build_ai_risk_budget_context(
                        self.session,
                        self.settings_row,
                        decision_symbol=plan.symbol,
                        equity=latest_pnl.equity,
                    )
                no_capacity_detail = self._entry_plan_no_additional_capacity_detail(
                    plan=plan,
                    open_positions=open_positions,
                    risk_budget=entry_capacity_risk_budget,
                    latest_price=market_snapshot.latest_price,
                )
                if no_capacity_detail is not None:
                    self._cancel_pending_entry_plan(
                        plan,
                        reason=ENTRY_PLAN_NO_CAPACITY_CANCEL_REASON_CODE,
                        detail=no_capacity_detail,
                    )
                    record_audit_event(
                        self.session,
                        event_type="decision_ai_skipped",
                        entity_type="pending_entry_plan",
                        entity_id=str(plan.id),
                        severity="info",
                        message="Pending entry plan AI recheck was skipped because no additional entry capacity remained.",
                        payload={
                            "ai_call_event": AI_CALL_EVENT_SKIPPED,
                            "symbol": plan.symbol,
                            "scope": "entry_plan_recheck",
                            "reason": ENTRY_PLAN_NO_CAPACITY_CANCEL_REASON_CODE,
                            "hard_skip_ai": True,
                            "skip_category": "entry_capacity",
                            "plan_id": plan.id,
                            "source_decision_run_id": plan.source_decision_run_id,
                            **no_capacity_detail,
                        },
                    )
                    result_item["plan"] = self._pending_entry_plan_snapshot(plan).model_dump(mode="json")
                    result_item["status"] = "canceled"
                    result_item["blocked_reasons"] = [ENTRY_PLAN_NO_CAPACITY_CANCEL_REASON_CODE]
                    symbol_results.append(result_item)
                    continue
                expiry_takes_precedence = (
                    not entry_control_blocked
                    or any(
                        reason in ENTRY_PLAN_CONTROL_BLOCKERS_ALLOW_LOCAL_EXPIRY
                        for reason in entry_control_blocked_reasons
                    )
                )
                if pending_entry_plan_is_expired(plan.expires_at, now=generated_at) and expiry_takes_precedence:
                    self._cancel_pending_entry_plan(
                        plan,
                        reason="PLAN_TTL_EXPIRED",
                        cancel_status="expired",
                        detail={"observed_at": generated_at.isoformat()},
                    )
                    result_item["status"] = "expired"
                    symbol_results.append(result_item)
                    continue
                if entry_control_blocked or entry_exchange_permission_pre_ai_block is not None:
                    blocked_reasons = list(entry_control_blocked_reasons)
                    exchange_permission_payload: dict[str, object] | None = None
                    if entry_exchange_permission_pre_ai_block is not None:
                        reason_code = str(entry_exchange_permission_pre_ai_block["reason_code"])
                        blocked_reasons = self._unique_reason_codes([*blocked_reasons, reason_code])
                        exchange_permission_payload = dict(entry_exchange_permission_pre_ai_block)
                        ai_recheck = self._record_entry_plan_ai_recheck_skip(
                            plan=plan,
                            reason=reason_code,
                            generated_at=generated_at,
                            gate_payload={"exchange_permission": exchange_permission_payload},
                            hard_skip_ai=True,
                            skip_category="hard_skip_ai",
                            hard_skip_reason_codes=[reason_code],
                            correlation_ids=normalize_correlation_ids(
                                cycle_id=f"entry-plan-watch:{plan.id}:{market_row.id}",
                                snapshot_id=market_row.id,
                                decision_id=plan.source_decision_run_id,
                            ),
                        )
                        result_item["ai_recheck"] = {
                            "status": ai_recheck.get("status"),
                            "skip_reason": ai_recheck.get("skip_reason"),
                            "retry_after_seconds": ai_recheck.get("retry_after_seconds"),
                            "decision_run_id": None,
                            "decision": None,
                            "ai_call_policy": ai_recheck.get("ai_call_policy"),
                            "trigger": None,
                        }
                    self._defer_pending_entry_plan(
                        plan,
                        reason="PLAN_WAITING_FOR_ENTRY_CONTROL",
                        generated_at=generated_at,
                        market_row_id=market_row.id,
                        detail={
                            "blocked_reasons": blocked_reasons,
                            "exchange_permission": exchange_permission_payload,
                        },
                    )
                    result_item["plan"] = self._pending_entry_plan_snapshot(plan).model_dump(mode="json")
                    result_item["status"] = "control_blocked"
                    result_item["blocked_reasons"] = blocked_reasons
                    symbol_results.append(result_item)
                    continue
                if self._plan_invalidation_broken(plan, market_snapshot):
                    self._cancel_pending_entry_plan(
                        plan,
                        reason="PLAN_INVALIDATED",
                        detail={
                            "latest_price": market_snapshot.latest_price,
                            "invalidation_price": plan.invalidation_price,
                        },
                    )
                    result_item["status"] = "canceled"
                    symbol_results.append(result_item)
                    continue

                confirm_detail = self._build_plan_confirm_detail(plan, market_snapshot)
                observed_chase_bps = self._plan_chase_bps(plan, market_snapshot.latest_price)
                trigger_details = {
                    **confirm_detail,
                    "observed_chase_bps": round(observed_chase_bps, 6),
                    "max_chase_bps": plan.max_chase_bps,
                    "latest_price": market_snapshot.latest_price,
                    "market_snapshot_id": market_row.id,
                    "market_snapshot_time": market_snapshot.snapshot_time.isoformat(),
                }
                metadata["trigger_details"] = trigger_details
                metadata["last_watch_at"] = generated_at.isoformat()
                metadata["last_watch_snapshot_id"] = market_row.id
                metadata["last_watch_cycle_id"] = f"entry-plan-watch:{plan.id}:{market_row.id}"
                plan.metadata_json = metadata
                self.session.add(plan)
                self.session.flush()
                result_item["plan"] = self._pending_entry_plan_snapshot(plan).model_dump(mode="json")
                watch_cycle_id = str(metadata.get("last_watch_cycle_id") or f"entry-plan-watch:{plan.id}:{market_row.id}")

                if bool(confirm_detail.get("cancel_recommended")):
                    self._record_plan_confirmation_tracking(
                        plan=plan,
                        confirm_detail=confirm_detail,
                        trigger_details=trigger_details,
                        market_snapshot=market_snapshot,
                        market_snapshot_id=market_row.id,
                        watch_cycle_id=watch_cycle_id,
                        plan_status="canceled",
                        blocked_reason_codes=["PLAN_CONFIRM_QUALITY_REJECTED"],
                        plan_cancel_reason="PLAN_CONFIRM_QUALITY_REJECTED",
                        severity="warning",
                    )
                    self._cancel_pending_entry_plan(
                        plan,
                        reason="PLAN_CONFIRM_QUALITY_REJECTED",
                        detail={
                            "quality_score": confirm_detail.get("quality_score"),
                            "quality_threshold": confirm_detail.get("quality_threshold"),
                            "quality_state": confirm_detail.get("quality_state"),
                            "quality_components": confirm_detail.get("quality_components"),
                            "reason": confirm_detail.get("reason"),
                        },
                    )
                    result_item["status"] = "canceled"
                    result_item["blocked_reasons"] = ["PLAN_CONFIRM_QUALITY_REJECTED"]
                    result_item["plan"] = self._pending_entry_plan_snapshot(plan).model_dump(mode="json")
                    symbol_results.append(result_item)
                    continue
                if plan.max_chase_bps is not None and observed_chase_bps > plan.max_chase_bps:
                    self._record_plan_confirmation_tracking(
                        plan=plan,
                        confirm_detail=confirm_detail,
                        trigger_details=trigger_details,
                        market_snapshot=market_snapshot,
                        market_snapshot_id=market_row.id,
                        watch_cycle_id=watch_cycle_id,
                        plan_status="waiting",
                        blocked_reason_codes=["PLAN_MAX_CHASE_EXCEEDED"],
                    )
                    result_item["status"] = "armed_waiting_reentry"
                    result_item["blocked_reasons"] = ["PLAN_MAX_CHASE_EXCEEDED"]
                    result_item["plan"] = self._pending_entry_plan_snapshot(plan).model_dump(mode="json")
                    self._record_decision_funnel_audit(
                        cycle_id=watch_cycle_id,
                        symbol=plan.symbol,
                        timeframe=plan.source_timeframe,
                        regime=plan.regime,
                        stage="m1_confirmation_failed",
                        status="waiting",
                        strategy_candidate=self._pending_plan_funnel_candidate(plan),
                        entry_plan_status="waiting",
                        m1_confirmation_status="failed",
                        final_risk_status="not_checked",
                        blocked_reason_codes=["PLAN_MAX_CHASE_EXCEEDED"],
                        detail={"plan_id": plan.id, "trigger_details": trigger_details},
                        severity="info",
                        entity_type="pending_entry_plan",
                        entity_id=str(plan.id),
                        correlation_ids=normalize_correlation_ids(
                            cycle_id=watch_cycle_id,
                            snapshot_id=market_row.id,
                            decision_id=plan.source_decision_run_id,
                        ),
                    )
                    symbol_results.append(result_item)
                    continue
                if bool(confirm_detail.get("late_chase")) and not bool(confirm_detail.get("confirm_met")):
                    self._record_plan_confirmation_tracking(
                        plan=plan,
                        confirm_detail=confirm_detail,
                        trigger_details=trigger_details,
                        market_snapshot=market_snapshot,
                        market_snapshot_id=market_row.id,
                        watch_cycle_id=watch_cycle_id,
                        plan_status="waiting",
                        blocked_reason_codes=["PLAN_LATE_CHASE_WAITING_REENTRY"],
                    )
                    result_item["status"] = "armed_waiting_reentry"
                    result_item["blocked_reasons"] = ["PLAN_LATE_CHASE_WAITING_REENTRY"]
                    result_item["plan"] = self._pending_entry_plan_snapshot(plan).model_dump(mode="json")
                    self._record_decision_funnel_audit(
                        cycle_id=watch_cycle_id,
                        symbol=plan.symbol,
                        timeframe=plan.source_timeframe,
                        regime=plan.regime,
                        stage="m1_confirmation_failed",
                        status="waiting",
                        strategy_candidate=self._pending_plan_funnel_candidate(plan),
                        entry_plan_status="waiting",
                        m1_confirmation_status="failed",
                        final_risk_status="not_checked",
                        blocked_reason_codes=["PLAN_LATE_CHASE_WAITING_REENTRY"],
                        detail={"plan_id": plan.id, "trigger_details": trigger_details},
                        severity="info",
                        entity_type="pending_entry_plan",
                        entity_id=str(plan.id),
                        correlation_ids=normalize_correlation_ids(
                            cycle_id=watch_cycle_id,
                            snapshot_id=market_row.id,
                            decision_id=plan.source_decision_run_id,
                        ),
                    )
                    symbol_results.append(result_item)
                    continue
                if not bool(confirm_detail.get("confirm_met")):
                    self._record_plan_confirmation_tracking(
                        plan=plan,
                        confirm_detail=confirm_detail,
                        trigger_details=trigger_details,
                        market_snapshot=market_snapshot,
                        market_snapshot_id=market_row.id,
                        watch_cycle_id=watch_cycle_id,
                        plan_status="waiting",
                        blocked_reason_codes=["PLAN_CONFIRM_QUALITY_LOW"],
                    )
                    result_item["status"] = "armed_waiting_confirmation"
                    result_item["blocked_reasons"] = ["PLAN_CONFIRM_QUALITY_LOW"]
                    result_item["plan"] = self._pending_entry_plan_snapshot(plan).model_dump(mode="json")
                    self._record_decision_funnel_audit(
                        cycle_id=watch_cycle_id,
                        symbol=plan.symbol,
                        timeframe=plan.source_timeframe,
                        regime=plan.regime,
                        stage="m1_confirmation_failed",
                        status="waiting",
                        strategy_candidate=self._pending_plan_funnel_candidate(plan),
                        entry_plan_status="waiting",
                        m1_confirmation_status="failed",
                        final_risk_status="not_checked",
                        blocked_reason_codes=["PLAN_CONFIRM_QUALITY_LOW"],
                        detail={"plan_id": plan.id, "trigger_details": trigger_details},
                        severity="info",
                        entity_type="pending_entry_plan",
                        entity_id=str(plan.id),
                        correlation_ids=normalize_correlation_ids(
                            cycle_id=watch_cycle_id,
                            snapshot_id=market_row.id,
                            decision_id=plan.source_decision_run_id,
                        ),
                    )
                    symbol_results.append(result_item)
                    continue
                self._record_plan_confirmation_tracking(
                    plan=plan,
                    confirm_detail=confirm_detail,
                    trigger_details=trigger_details,
                    market_snapshot=market_snapshot,
                    market_snapshot_id=market_row.id,
                    watch_cycle_id=watch_cycle_id,
                    plan_status="passed",
                    blocked_reason_codes=[],
                )
                result_item["plan"] = self._pending_entry_plan_snapshot(plan).model_dump(mode="json")

                source_decision_run = self._latest_decision_run(decision_run_id=plan.source_decision_run_id)
                if source_decision_run is None or not isinstance(source_decision_run.output_payload, dict):
                    self._cancel_pending_entry_plan(
                        plan,
                        reason="SOURCE_DECISION_MISSING",
                    )
                    result_item["status"] = "canceled"
                    symbol_results.append(result_item)
                    continue
                ai_recheck = self._run_entry_plan_ai_recheck(
                    plan=plan,
                    source_decision_run=source_decision_run,
                    market_snapshot=market_snapshot,
                    market_row=market_row,
                    runtime_state=runtime_state,
                    open_positions=open_positions,
                    cadence_profile=cadence_profile,
                    confirm_detail=confirm_detail,
                    trigger_details=trigger_details,
                    generated_at=generated_at,
                )
                result_item["ai_recheck"] = {
                    "status": ai_recheck.get("status"),
                    "skip_reason": ai_recheck.get("skip_reason"),
                    "retry_after_seconds": ai_recheck.get("retry_after_seconds"),
                    "decision_run_id": getattr(ai_recheck.get("decision_run"), "id", None),
                    "decision": (
                        ai_recheck["decision"].model_dump(mode="json")
                        if isinstance(ai_recheck.get("decision"), TradeDecision)
                        else None
                    ),
                    "ai_call_policy": ai_recheck.get("ai_call_policy"),
                    "trigger": ai_recheck.get("trigger"),
                }
                if ai_recheck.get("status") != "received":
                    result_item["status"] = "armed_waiting_ai_recheck"
                    result_item["blocked_reasons"] = ["PLAN_AI_RECHECK_PENDING"]
                    symbol_results.append(result_item)
                    continue
                recheck_decision = ai_recheck.get("decision")
                recheck_decision_run = ai_recheck.get("decision_run")
                if not isinstance(recheck_decision, TradeDecision) or not isinstance(recheck_decision_run, AgentRun):
                    result_item["status"] = "armed_waiting_ai_recheck"
                    result_item["blocked_reasons"] = ["PLAN_AI_RECHECK_RESULT_MISSING"]
                    symbol_results.append(result_item)
                    continue
                if recheck_decision.decision != plan.side:
                    cancel_reason = (
                        "PLAN_RECHECK_OPPOSITE_SIDE"
                        if recheck_decision.decision in {"long", "short"}
                        else "PLAN_RECHECK_REJECTED"
                    )
                    recheck_rejection_correlation_ids = normalize_correlation_ids(
                        cycle_id=f"entry-plan-recheck:{plan.id}:{market_row.id}",
                        snapshot_id=market_row.id,
                        decision_id=recheck_decision_run.id,
                    )
                    self._cancel_pending_entry_plan(
                        plan,
                        reason=cancel_reason,
                        detail={
                            "recheck_decision": recheck_decision.decision,
                            "recheck_decision_run_id": recheck_decision_run.id,
                            "confidence": recheck_decision.confidence,
                        },
                        correlation_ids=recheck_rejection_correlation_ids,
                    )
                    record_audit_event(
                        self.session,
                        event_type="pending_entry_plan_recheck_rejected",
                        entity_type="pending_entry_plan",
                        entity_id=str(plan.id),
                        severity="info",
                        message="Pending entry plan was rejected by AI recheck at the entry zone.",
                        payload={
                            "ai_call_event": "AI_DECISION_RECEIVED",
                            "symbol": plan.symbol,
                            "plan_id": plan.id,
                            "plan_side": plan.side,
                            "decision": recheck_decision.decision,
                            "decision_run_id": recheck_decision_run.id,
                            "confidence": recheck_decision.confidence,
                            "cancel_reason": cancel_reason,
                        },
                        correlation_ids=recheck_rejection_correlation_ids,
                    )
                    result_item["status"] = "canceled"
                    result_item["blocked_reasons"] = [cancel_reason]
                    symbol_results.append(result_item)
                    continue
                risk_pct_candidates = [
                    value
                    for value in (
                        _safe_float(plan.risk_pct_cap, default=0.0),
                        _safe_float(recheck_decision.risk_pct, default=0.0),
                    )
                    if value > 0
                ]
                leverage_candidates = [
                    value
                    for value in (
                        _safe_float(plan.leverage_cap, default=0.0),
                        _safe_float(recheck_decision.leverage, default=0.0),
                    )
                    if value > 0
                ]
                final_risk_pct = min(risk_pct_candidates) if risk_pct_candidates else 0.0
                final_leverage = min(leverage_candidates) if leverage_candidates else 0.0
                source_decision = self._trigger_execution_decision_from_plan(
                    plan,
                    market_snapshot,
                    source_decision=recheck_decision,
                ).model_copy(
                    update={
                        "risk_pct": final_risk_pct,
                        "leverage": final_leverage,
                        "rationale_codes": list(
                            dict.fromkeys(
                                [
                                    *recheck_decision.rationale_codes,
                                    "PENDING_ENTRY_PLAN_RECHECK_CONFIRMED",
                                    "PENDING_ENTRY_PLAN_TRIGGERED",
                                ]
                            )
                        )
                    }
                )
                trigger_cycle_id = f"entry-plan-trigger:{plan.id}:{market_row.id}"
                correlation_ids = normalize_correlation_ids(
                    cycle_id=trigger_cycle_id,
                    snapshot_id=market_row.id,
                    decision_id=recheck_decision_run.id,
                )
                source_decision_metadata = _as_dict(ai_recheck.get("decision_metadata"))
                source_ai_context = _as_dict(source_decision_metadata.get("ai_context"))
                source_trade_performance_tags = _as_dict(source_decision_metadata.get("trade_performance_tags"))
                source_lead_market_context = _as_dict(source_ai_context.get("lead_lag_summary"))
                source_event_risk_context = _as_dict(source_ai_context.get("event_risk_context"))
                self._refresh_runtime_state_before_risk()
                risk_result, risk_row = evaluate_risk(
                    self.session,
                    self.settings_row,
                    source_decision,
                    market_snapshot,
                    decision_run_id=recheck_decision_run.id,
                    market_snapshot_id=market_row.id,
                    execution_mode="live",
                    decision_context={
                        "trade_performance_tags": source_trade_performance_tags,
                        "current_market_state": {
                            "regime_id": source_trade_performance_tags.get("regime_id"),
                            "regime_label": source_trade_performance_tags.get("regime_label"),
                            "range_id": source_trade_performance_tags.get("range_id"),
                            "range_low": source_trade_performance_tags.get("range_low"),
                            "range_high": source_trade_performance_tags.get("range_high"),
                            "range_width_pct": source_trade_performance_tags.get("range_width_pct"),
                            "range_breakout_direction": source_trade_performance_tags.get(
                                "range_breakout_direction"
                            ),
                        },
                        "lead_market_context": source_lead_market_context,
                        "event_risk_context": source_event_risk_context,
                    },
                )
                risk_correlation_ids = normalize_correlation_ids(correlation_ids, risk_id=risk_row.id)
                record_audit_event(
                    self.session,
                    event_type="risk_check",
                    entity_type="risk_check",
                    entity_id=str(risk_row.id),
                    severity="warning" if not risk_result.allowed else "info",
                    message="Pending entry plan trigger risk check completed.",
                    payload=risk_result.model_dump(mode="json"),
                    correlation_ids=risk_correlation_ids,
                )
                result_item["risk_result"] = risk_result.model_dump(mode="json")
                if not risk_result.allowed:
                    metadata = self._pending_entry_plan_metadata(plan)
                    metadata["last_blocked_reason_codes"] = list(risk_result.blocked_reason_codes)
                    metadata["last_risk_check_id"] = risk_row.id
                    metadata["last_ai_recheck_risk_blocked_decision_run_id"] = recheck_decision_run.id
                    plan.metadata_json = metadata
                    self.session.add(plan)
                    self.session.flush()
                    record_audit_event(
                        self.session,
                        event_type="decision_risk_blocked",
                        entity_type="decision_run",
                        entity_id=str(recheck_decision_run.id),
                        severity="warning",
                        message="Pending entry plan AI recheck was blocked by deterministic risk policy.",
                        payload={
                            "ai_call_event": "AI_DECISION_BLOCKED_BY_RISK",
                            "symbol": plan.symbol,
                            "decision": source_decision.decision,
                            "scope": "entry_plan_recheck",
                            "risk_allowed": False,
                            "risk_check_id": risk_row.id,
                            "reason_codes": list(risk_result.reason_codes or []),
                            "blocked_reason_codes": list(
                                getattr(risk_result, "blocked_reason_codes", []) or []
                            ),
                            "plan_id": plan.id,
                            "source_decision_run_id": plan.source_decision_run_id,
                        },
                        correlation_ids=risk_correlation_ids,
                    )
                    self._record_decision_funnel_audit(
                        cycle_id=trigger_cycle_id,
                        symbol=plan.symbol,
                        timeframe=plan.source_timeframe,
                        regime=plan.regime,
                        stage="final_risk_guard",
                        status="final_risk_blocked",
                        strategy_candidate=self._pending_plan_funnel_candidate(plan),
                        decision=source_decision,
                        decision_run_id=recheck_decision_run.id,
                        provider_name=str(source_decision_metadata.get("ai_provider") or source_decision_metadata.get("provider") or "") or None,
                        decision_metadata=source_decision_metadata,
                        risk_result=risk_result,
                        risk_row_id=risk_row.id,
                        entry_plan_status=plan.plan_status,
                        m1_confirmation_status="passed",
                        final_risk_status="blocked",
                        blocked_reason_codes=list(getattr(risk_result, "blocked_reason_codes", []) or []),
                        detail={"plan_id": plan.id, "source_decision_run_id": plan.source_decision_run_id},
                        severity="warning",
                        entity_type="pending_entry_plan",
                        entity_id=str(plan.id),
                        correlation_ids=risk_correlation_ids,
                    )
                    result_item["status"] = "risk_blocked"
                    symbol_results.append(result_item)
                    continue
                record_audit_event(
                    self.session,
                    event_type="decision_risk_approved",
                    entity_type="decision_run",
                    entity_id=str(recheck_decision_run.id),
                    severity="info",
                    message="Pending entry plan AI recheck was approved by deterministic risk policy.",
                    payload={
                        "ai_call_event": "AI_DECISION_APPROVED_BY_RISK",
                        "symbol": plan.symbol,
                        "decision": source_decision.decision,
                        "scope": "entry_plan_recheck",
                        "risk_allowed": True,
                        "risk_check_id": risk_row.id,
                        "approved_risk_pct": risk_result.approved_risk_pct,
                        "approved_leverage": risk_result.approved_leverage,
                        "plan_id": plan.id,
                        "source_decision_run_id": plan.source_decision_run_id,
                    },
                    correlation_ids=risk_correlation_ids,
                )
                execution_result = execute_live_trade(
                    self.session,
                    self.settings_row,
                    decision_run_id=recheck_decision_run.id,
                    decision=source_decision,
                    market_snapshot=market_snapshot,
                    risk_result=risk_result,
                    risk_row=risk_row,
                    cycle_id=trigger_cycle_id,
                    snapshot_id=market_row.id,
                    idempotency_key=plan.idempotency_key,
                )
                refresh_decision_performance_fact_usefulness(self.session, recheck_decision_run.id)
                result_item["execution"] = execution_result
                success_status = str(execution_result.get("status") or "")
                if success_status in {
                    "filled",
                    "partially_filled",
                    "emergency_exit",
                    *ENTRY_PLAN_SIMULATED_EXECUTION_STATUSES,
                } or (
                    success_status == "deduplicated"
                    and str(execution_result.get("dedupe_reason") or "") == "cycle_action_already_completed"
                ):
                    self._mark_pending_entry_plan_triggered(
                        plan,
                        execution_result={
                            "risk_check_id": risk_row.id,
                            **dict(execution_result),
                        },
                        correlation_ids=normalize_correlation_ids(
                            risk_correlation_ids,
                            execution_id=execution_result.get("order_id"),
                        ),
                    )
                    result_item["status"] = "triggered"
                else:
                    result_item["status"] = success_status or "execution_pending"
                    self._record_decision_funnel_audit(
                        cycle_id=trigger_cycle_id,
                        symbol=plan.symbol,
                        timeframe=plan.source_timeframe,
                        regime=plan.regime,
                        stage="order_execution",
                        status="order_blocked" if success_status in {"blocked", "rejected", "error", "submission_unknown"} else "execution_pending",
                        strategy_candidate=self._pending_plan_funnel_candidate(plan),
                        decision=source_decision,
                        decision_run_id=recheck_decision_run.id,
                        provider_name=str(source_decision_metadata.get("ai_provider") or source_decision_metadata.get("provider") or "") or None,
                        decision_metadata=source_decision_metadata,
                        risk_result=risk_result,
                        risk_row_id=risk_row.id,
                        entry_plan_status=plan.plan_status,
                        m1_confirmation_status="passed",
                        final_risk_status="approved",
                        execution_result=execution_result,
                        blocked_reason_codes=list(execution_result.get("reason_codes") or []),
                        detail={"plan_id": plan.id},
                        severity="warning" if success_status in {"blocked", "rejected", "error", "submission_unknown"} else "info",
                        entity_type="pending_entry_plan",
                        entity_id=str(plan.id),
                        correlation_ids=risk_correlation_ids,
                    )
                symbol_results.append(result_item)
            results.append(
                {
                    "symbol": symbol,
                    "watch_timeframe": ENTRY_PLAN_WATCH_TIMEFRAME,
                    "cadence": cadence_profile,
                    "market_snapshot_id": market_row.id,
                    "market_snapshot_time": market_snapshot.snapshot_time.isoformat(),
                    "latest_price": market_snapshot.latest_price,
                    "exchange_sync": exchange_sync_result,
                    "plans": symbol_results,
                }
            )
        return {
            "workflow": "entry_plan_watcher_cycle",
            "generated_at": generated_at.isoformat(),
            "symbols": watched_symbols,
            "results": results,
            "auto_resume": auto_resume_result,
        }

    @staticmethod
    def _position_review_interval_minutes(
        cadence_profile: dict[str, object],
        *,
        effective_settings: object,
    ) -> int:
        cadence_hint = _as_dict(cadence_profile.get("holding_profile_cadence_hint"))
        hint_minutes = cadence_hint.get("decision_interval_minutes")
        if isinstance(hint_minutes, (int, float)) and int(hint_minutes) > 0:
            return int(hint_minutes)
        effective_cadence = _as_dict(cadence_profile.get("effective_cadence"))
        cadence_minutes = effective_cadence.get("ai_call_interval_minutes")
        if isinstance(cadence_minutes, (int, float)) and int(cadence_minutes) > 0:
            return int(cadence_minutes)
        return max(int(getattr(effective_settings, "ai_call_interval_minutes", 30)), 1)

    @staticmethod
    def _validated_holding_profile(profile: object) -> str | None:
        normalized = str(profile or "").strip().lower()
        if not normalized:
            return None
        resolved = resolve_holding_profile_cadence_hint(normalized)
        resolved_profile = str(resolved.get("holding_profile") or "").strip().lower()
        return resolved_profile if resolved_profile == normalized else None

    @classmethod
    def _resolve_position_review_holding_profile(
        cls,
        *,
        review_trigger: AIReviewTriggerPayload | None = None,
        position_management: dict[str, object] | None = None,
        latest_metadata: dict[str, object] | None = None,
        cadence_profile: dict[str, object] | None = None,
    ) -> str | None:
        candidates = [
            review_trigger.holding_profile if review_trigger is not None else None,
            _as_dict(position_management or {}).get("holding_profile"),
            _as_dict(latest_metadata or {}).get("holding_profile"),
            _as_dict(cadence_profile or {}).get("active_holding_profile"),
        ]
        for candidate in candidates:
            validated = cls._validated_holding_profile(candidate)
            if validated is not None:
                return validated
        return None

    @classmethod
    def _position_review_cadence_details(
        cls,
        cadence_profile: dict[str, object],
        *,
        effective_settings: object,
        holding_profile: str | None,
    ) -> dict[str, object]:
        effective_cadence = _as_dict(cadence_profile.get("effective_cadence"))
        cadence_profile_summary = {
            "mode": str(cadence_profile.get("mode") or "") or None,
            "active_holding_profile": str(cadence_profile.get("active_holding_profile") or "") or None,
            "holding_profile_cadence_source": (
                str(cadence_profile.get("holding_profile_cadence_source") or "") or None
            ),
            "effective_cadence": {
                "decision_cycle_interval_minutes": effective_cadence.get("decision_cycle_interval_minutes"),
                "ai_call_interval_minutes": effective_cadence.get("ai_call_interval_minutes"),
                "position_management_interval_seconds": effective_cadence.get("position_management_interval_seconds"),
            },
        }
        validated_profile = cls._validated_holding_profile(holding_profile)
        if validated_profile is not None:
            cadence_hint = resolve_holding_profile_cadence_hint(validated_profile)
            hint_minutes = cadence_hint.get("decision_interval_minutes")
            if isinstance(hint_minutes, (int, float)) and int(hint_minutes) > 0:
                return {
                    "applied_review_cadence_minutes": int(hint_minutes),
                    "review_cadence_source": "holding_profile_cadence_hint",
                    "holding_profile_cadence_hint": dict(cadence_hint),
                    "cadence_fallback_reason": None,
                    "cadence_profile_summary": cadence_profile_summary,
                }

        effective_minutes = effective_cadence.get("ai_call_interval_minutes")
        if isinstance(effective_minutes, (int, float)) and int(effective_minutes) > 0:
            return {
                "applied_review_cadence_minutes": int(effective_minutes),
                "review_cadence_source": "effective_ai_call_interval_minutes",
                "holding_profile_cadence_hint": {},
                "cadence_fallback_reason": "HOLDING_PROFILE_CADENCE_HINT_MISSING",
                "cadence_profile_summary": cadence_profile_summary,
            }

        return {
            "applied_review_cadence_minutes": max(
                int(getattr(effective_settings, "ai_call_interval_minutes", 30)),
                1,
            ),
            "review_cadence_source": "settings_ai_call_interval_minutes",
            "holding_profile_cadence_hint": {},
            "cadence_fallback_reason": "EFFECTIVE_AI_CADENCE_UNAVAILABLE",
            "cadence_profile_summary": cadence_profile_summary,
        }

    def resolve_interval_decision_schedule_details(
        self,
        *,
        symbol: str,
        timeframe: str,
        effective_settings: object,
        cadence_profile: dict[str, object] | None = None,
        open_positions: list[Position] | None = None,
        latest_decision_run: AgentRun | None = None,
        review_trigger: AIReviewTriggerPayload | None = None,
    ) -> dict[str, object]:
        cadence_profile = (
            cadence_profile
            if isinstance(cadence_profile, dict)
            else self.get_symbol_cadence_profile(
                symbol=symbol,
                timeframe=timeframe,
                open_positions=open_positions,
            )
        )
        effective_cadence = _as_dict(cadence_profile.get("effective_cadence"))
        scheduler_interval_minutes = (
            int(effective_cadence.get("decision_cycle_interval_minutes"))
            if isinstance(effective_cadence.get("decision_cycle_interval_minutes"), (int, float))
            and int(effective_cadence.get("decision_cycle_interval_minutes")) > 0
            else max(int(getattr(effective_settings, "decision_cycle_interval_minutes", 1)), 1)
        )
        open_positions = (
            open_positions
            if open_positions is not None
            else get_open_positions(self.session, symbol)
        )
        latest_decision_run = (
            latest_decision_run
            if latest_decision_run is not None
            else self._latest_symbol_decision_run(symbol=symbol, timeframe=timeframe)
        )
        latest_metadata = (
            latest_decision_run.metadata_json
            if latest_decision_run is not None and isinstance(latest_decision_run.metadata_json, dict)
            else {}
        )
        cadence_profile_summary = {
            "mode": str(cadence_profile.get("mode") or "") or None,
            "active_holding_profile": str(cadence_profile.get("active_holding_profile") or "") or None,
            "holding_profile_cadence_source": (
                str(cadence_profile.get("holding_profile_cadence_source") or "") or None
            ),
            "effective_cadence": dict(effective_cadence),
        }
        if not open_positions:
            return {
                "scheduler_interval_minutes": scheduler_interval_minutes,
                "applied_review_cadence_minutes": None,
                "review_cadence_source": None,
                "holding_profile_cadence_hint": {},
                "cadence_fallback_reason": None,
                "cadence_profile_summary": cadence_profile_summary,
                "holding_profile": None,
            }

        position_row = open_positions[0]
        position_metadata = _as_dict(getattr(position_row, "metadata_json", {}))
        position_management = _as_dict(position_metadata.get("position_management"))
        holding_profile = self._resolve_position_review_holding_profile(
            review_trigger=review_trigger,
            position_management=position_management,
            latest_metadata=latest_metadata,
            cadence_profile=cadence_profile,
        )
        cadence_details = self._position_review_cadence_details(
            cadence_profile,
            effective_settings=effective_settings,
            holding_profile=holding_profile,
        )
        return {
            **cadence_details,
            "scheduler_interval_minutes": int(cadence_details["applied_review_cadence_minutes"]),
            "holding_profile": holding_profile or "scalp",
        }

    @staticmethod
    def _open_position_max_review_age_minutes(
        review_interval_minutes: int,
        *,
        backstop_interval_minutes: int | None,
    ) -> int:
        base_minutes = max(int(review_interval_minutes), 1)
        max_review_age_minutes = max(base_minutes * 3, base_minutes)
        if isinstance(backstop_interval_minutes, int) and backstop_interval_minutes > 0:
            max_review_age_minutes = min(max_review_age_minutes, backstop_interval_minutes)
        return max(max_review_age_minutes, base_minutes)

    @staticmethod
    def _regime_summary_from_ai_context(ai_context_payload: dict[str, object]) -> dict[str, object]:
        composite_regime = _as_dict(ai_context_payload.get("composite_regime"))
        if not composite_regime:
            return {}
        return {
            "structure_regime": str(composite_regime.get("structure_regime") or "transition"),
            "direction_regime": str(composite_regime.get("direction_regime") or "neutral"),
            "volatility_regime": str(composite_regime.get("volatility_regime") or "normal"),
            "participation_regime": str(composite_regime.get("participation_regime") or "mixed"),
            "derivatives_regime": str(composite_regime.get("derivatives_regime") or "unavailable"),
            "execution_regime": str(composite_regime.get("execution_regime") or "unavailable"),
            "transition_risk": str(composite_regime.get("transition_risk") or "medium"),
        }

    @staticmethod
    def _regime_summary_from_feature_snapshot(
        feature_snapshot_payload: dict[str, object],
        *,
        fallback_summary: dict[str, object],
    ) -> dict[str, object]:
        payload = _as_dict(feature_snapshot_payload)
        regime = _as_dict(payload.get("regime"))
        if not regime:
            return dict(fallback_summary)
        derivatives = _as_dict(payload.get("derivatives"))
        breakout = _as_dict(payload.get("breakout"))
        lead_lag = _as_dict(payload.get("lead_lag"))
        primary_regime = str(regime.get("primary_regime") or "transition")
        trend_alignment = str(regime.get("trend_alignment") or "mixed")
        volatility_regime = str(regime.get("volatility_regime") or "normal")
        weak_volume = bool(regime.get("weak_volume", False))
        volume_regime = str(regime.get("volume_regime") or "mixed")
        momentum_weakening = bool(regime.get("momentum_weakening", False))
        volume_ratio = _safe_float(payload.get("volume_ratio"), default=1.0)
        breakout_direction = str(breakout.get("range_breakout_direction") or "none")

        if primary_regime == "range":
            structure_regime = (
                "squeeze"
                if volatility_regime == "compressed" or weak_volume or volume_ratio < 0.92
                else "range"
            )
        elif primary_regime == "transition" or momentum_weakening:
            structure_regime = "transition"
        elif breakout_direction != "none" and volatility_regime == "expanded":
            structure_regime = "expansion"
        else:
            structure_regime = "trend"

        direction_regime = (
            "bullish"
            if trend_alignment == "bullish_aligned"
            else "bearish"
            if trend_alignment == "bearish_aligned"
            else "neutral"
        )
        normalized_volatility = (
            "calm"
            if volatility_regime == "compressed"
            else "fast"
            if volatility_regime == "expanded"
            else "normal"
        )
        participation_regime = (
            "weak"
            if weak_volume or volume_regime == "weak" or volume_ratio < 0.92
            else "strong"
            if volume_regime == "strong" and volume_ratio >= 1.08
            else "mixed"
        )
        derivatives_regime = (
            "unavailable"
            if not bool(derivatives.get("available", False))
            else "headwind"
            if bool(
                derivatives.get("spread_stress", False)
                or derivatives.get("spread_headwind", False)
                or derivatives.get("crowded_long_risk", False)
                or derivatives.get("crowded_short_risk", False)
            )
            else "tailwind"
            if max(
                _safe_float(derivatives.get("long_alignment_score"), default=0.0),
                _safe_float(derivatives.get("short_alignment_score"), default=0.0),
            ) >= 0.62
            else "neutral"
        )
        spread_bps = _safe_float(derivatives.get("spread_bps"), default=0.0)
        execution_regime = (
            "unavailable"
            if not bool(derivatives.get("available", False)) and spread_bps <= 0
            else "stress"
            if bool(derivatives.get("spread_stress", False)) or spread_bps >= 18.0
            else "clean"
            if spread_bps > 0 and spread_bps <= 6.0
            else "normal"
        )
        transition_risk = (
            "high"
            if primary_regime == "transition"
            or momentum_weakening
            or bool(lead_lag.get("weak_reference_confirmation", False))
            else "low"
            if participation_regime == "strong" and direction_regime != "neutral"
            else "medium"
        )
        return {
            "structure_regime": structure_regime,
            "direction_regime": direction_regime,
            "volatility_regime": normalized_volatility,
            "participation_regime": participation_regime,
            "derivatives_regime": derivatives_regime,
            "execution_regime": execution_regime,
            "transition_risk": transition_risk,
        }

    @staticmethod
    def _data_quality_grade_for_review(
        sync_freshness_summary: dict[str, object],
        *,
        fallback_grade: str | None,
    ) -> str:
        scope_statuses = [
            str(_as_dict(sync_freshness_summary.get(scope)).get("status") or "")
            for scope in ("account", "positions", "open_orders", "protective_orders")
        ]
        if any(status in {"unknown", "failed", "incomplete"} for status in scope_statuses):
            return "unavailable"
        if any(status in {"stale", "skipped"} for status in scope_statuses):
            return "degraded"
        if fallback_grade in {"complete", "partial", "degraded", "unavailable"}:
            return fallback_grade
        return "complete"

    @staticmethod
    def _position_state_bucket(position_row: Position) -> str:
        side = str(getattr(position_row, "side", "long") or "long").lower()
        entry_price = _safe_float(getattr(position_row, "entry_price", None), default=0.0)
        mark_price = _safe_float(getattr(position_row, "mark_price", None), default=entry_price)
        stop_loss = _safe_float(getattr(position_row, "stop_loss", None), default=0.0)
        if entry_price <= 0 or mark_price <= 0:
            return "unknown"
        direction = -1.0 if side == "short" else 1.0
        pnl_distance = (mark_price - entry_price) * direction
        risk_distance = abs(entry_price - stop_loss) if stop_loss > 0 else 0.0
        if risk_distance > 0:
            pnl_r = pnl_distance / risk_distance
            if pnl_r <= -0.5:
                return "deep_loss"
            if pnl_r < 0:
                return "loss"
            if pnl_r < 0.5:
                return "flat"
            if pnl_r < 1.5:
                return "profit"
            return "extended_profit"
        pnl_pct = pnl_distance / entry_price
        if pnl_pct <= -0.01:
            return "deep_loss"
        if pnl_pct < -0.0025:
            return "loss"
        if pnl_pct < 0.003:
            return "flat"
        if pnl_pct < 0.015:
            return "profit"
        return "extended_profit"

    @staticmethod
    def _protection_health_summary(
        *,
        symbol: str,
        position_row: Position,
        position_management: dict[str, object],
        runtime_state: dict[str, object],
        missing_protection_symbols: set[str],
    ) -> str:
        stop_loss = getattr(position_row, "stop_loss", None)
        hard_stop_value = position_management.get("hard_stop_active")
        if hard_stop_value in {None, ""}:
            hard_stop_value = stop_loss not in {None, ""}
        stop_widening_value = position_management.get("stop_widening_allowed")
        if stop_widening_value in {None, ""}:
            stop_widening_value = False
        if symbol in missing_protection_symbols:
            return "missing_protection"
        if str(runtime_state.get("operating_state") or "") == PROTECTION_REQUIRED_STATE:
            return "protection_required"
        if not bool(hard_stop_value) or stop_loss in {None, ""}:
            return "hard_stop_inactive"
        if bool(runtime_state.get("protection_recovery_active", False)):
            return "recovery_active"
        if bool(stop_widening_value):
            return "protected_widening_allowed"
        return "protected"

    def _latest_risk_check_for_decision_run(
        self,
        *,
        decision_run_id: int | None,
    ) -> RiskCheck | None:
        if decision_run_id is None:
            return None
        return self.session.scalar(
            select(RiskCheck)
            .where(RiskCheck.decision_run_id == decision_run_id)
            .order_by(desc(RiskCheck.created_at))
            .limit(1)
        )

    def _active_symbol_entry_orders(
        self,
        *,
        symbol: str,
    ) -> list[Order]:
        return list(
            self.session.scalars(
                select(Order)
                .where(
                    Order.symbol == symbol.upper(),
                    Order.mode == "live",
                    Order.status.notin_(tuple(FINAL_ORDER_STATUSES)),
                    Order.reduce_only.is_(False),
                    Order.close_only.is_(False),
                )
                .order_by(desc(Order.created_at))
            )
        )

    def _build_active_position_entry_fingerprint_basis(
        self,
        *,
        symbol: str,
        timeframe: str,
        position_row: Position,
        feature_payload: FeaturePayload,
        position_management: dict[str, object],
        strategy_engine_name: str,
        holding_profile: str,
        runtime_state: dict[str, object],
        sync_freshness_summary: dict[str, object],
        missing_protection_symbols: set[str],
        allow_same_side_add_on: bool,
        allowed_add_on_side: str | None,
    ) -> dict[str, object]:
        feature_snapshot_payload = feature_payload.model_dump(mode="json")
        hard_stop_active = position_management.get("hard_stop_active")
        if hard_stop_active in {None, ""}:
            hard_stop_active = getattr(position_row, "stop_loss", None) not in {None, ""}
        stop_widening_allowed = position_management.get("stop_widening_allowed")
        if stop_widening_allowed in {None, ""}:
            stop_widening_allowed = False
        current_r_multiple = _safe_float(position_management.get("current_r_multiple"), default=None)
        active_entry_orders = self._active_symbol_entry_orders(symbol=symbol)
        requested_notional = 0.0
        for row in active_entry_orders:
            requested_notional += max(_safe_float(row.requested_quantity, default=0.0) or 0.0, 0.0) * max(
                _safe_float(row.requested_price, default=0.0) or 0.0,
                0.0,
            )
        return {
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "strategy_engine": strategy_engine_name or "unspecified_engine",
            "holding_profile": holding_profile or "scalp",
            "position_side": str(getattr(position_row, "side", "") or "").lower() or None,
            "position_quantity": round(_safe_float(getattr(position_row, "quantity", None), default=0.0) or 0.0, 8),
            "position_state_bucket": self._position_state_bucket(position_row),
            "regime_summary": self._regime_summary_from_feature_snapshot(
                feature_snapshot_payload,
                fallback_summary={},
            ),
            "data_quality_grade": self._data_quality_grade_for_review(
                sync_freshness_summary,
                fallback_grade="complete",
            ),
            "hard_stop_active": bool(hard_stop_active),
            "stop_widening_allowed": bool(stop_widening_allowed),
            "current_r_multiple": round(current_r_multiple, 6) if current_r_multiple is not None else None,
            "protection_health_summary": self._protection_health_summary(
                symbol=symbol,
                position_row=position_row,
                position_management=position_management,
                runtime_state=runtime_state,
                missing_protection_symbols=missing_protection_symbols,
            ),
            "allow_same_side_add_on": bool(allow_same_side_add_on),
            "allowed_add_on_side": allowed_add_on_side if allowed_add_on_side in {"long", "short"} else None,
            "active_entry_order_count": len(active_entry_orders),
            "active_entry_order_sides": sorted(
                {
                    str(getattr(row, "side", "") or "").lower()
                    for row in active_entry_orders
                    if str(getattr(row, "side", "") or "").lower()
                }
            ),
            "active_entry_order_statuses": sorted(
                {
                    str(getattr(row, "status", "") or "").lower()
                    for row in active_entry_orders
                    if str(getattr(row, "status", "") or "").lower()
                }
            ),
            "active_entry_order_requested_notional": round(requested_notional, 6),
        }

    @staticmethod
    def _active_position_entry_suppression_reason_code(
        *,
        latest_metadata: dict[str, object],
        latest_risk_row: RiskCheck | None,
        current_fingerprint_basis: dict[str, object],
    ) -> str | None:
        if latest_risk_row is None or bool(latest_risk_row.allowed):
            return None
        previous_fingerprint_basis = _as_dict(
            latest_metadata.get("active_position_entry_fingerprint_basis")
        )
        if not previous_fingerprint_basis:
            return None
        if TradingOrchestrator._fingerprint_changed_fields(
            current_fingerprint_basis,
            previous_fingerprint_basis,
        ):
            return None
        reason_codes = {
            str(code)
            for code in list(latest_risk_row.reason_codes or [])
            if code in ACTIVE_POSITION_ENTRY_SUPPRESSION_REASON_CODES
        }
        if "LARGEST_POSITION_LIMIT_REACHED" in reason_codes:
            return "LARGEST_POSITION_LIMIT_REACHED"
        if "DETERMINISTIC_BASELINE_DISAGREEMENT" in reason_codes:
            return "DETERMINISTIC_BASELINE_DISAGREEMENT"
        return None

    def _build_active_position_prompt_route_context(
        self,
        *,
        symbol: str,
        timeframe: str,
        open_positions: list[Position],
        feature_payload: FeaturePayload,
        position_management_context: dict[str, object],
        risk_context: dict[str, object],
        selection_context: dict[str, object],
        runtime_state: dict[str, object],
        latest_decision_run: AgentRun | None,
        latest_decision_metadata: dict[str, object],
        latest_decision_output: dict[str, object],
        review_trigger_payload: AIReviewTriggerPayload | None,
        decision_reference: dict[str, object],
    ) -> tuple[dict[str, object], dict[str, object] | None]:
        if not open_positions:
            return {}, None
        missing_protection_symbols = {
            str(item).upper()
            for item in runtime_state.get("missing_protection_symbols", [])
            if item
        }
        recovery_active = (
            str(runtime_state.get("operating_state") or "") in {
                PROTECTION_REQUIRED_STATE,
                "DEGRADED_MANAGE_ONLY",
                "EMERGENCY_EXIT",
            }
            or symbol.upper() in missing_protection_symbols
        )
        position_row = open_positions[0]
        strategy_engine_name = (
            str(selection_context.get("strategy_engine") or "")
            or _strategy_engine_name_from_payload(latest_decision_metadata, latest_decision_output)
            or "trend_pullback_engine"
        )
        holding_profile = (
            str(selection_context.get("holding_profile") or "")
            or str(position_management_context.get("holding_profile") or "")
            or str(latest_decision_metadata.get("holding_profile") or "")
            or "scalp"
        )
        route_context: dict[str, object] = {
            "management_only_open_position_route": True,
            "allow_same_side_add_on": False,
            "allowed_add_on_side": None,
            "entry_proposal_suppression_active": False,
            "entry_proposal_suppressed_reason_code": None,
            "risk_adding_add_on_candidate": False,
            "risk_adding_add_on_side": None,
            "risk_adding_add_on_pre_ai_blocked": False,
            "risk_adding_add_on_pre_ai_block_reason": None,
            "risk_adding_add_on_pre_ai_reason_codes": [],
        }
        if recovery_active:
            return route_context, None
        evaluation_risk_context = dict(risk_context)
        if selection_context:
            evaluation_risk_context["selection_context"] = dict(selection_context)
        long_derivatives = TradingDecisionAgent._derivatives_side_context(feature_payload, side="long")
        short_derivatives = TradingDecisionAgent._derivatives_side_context(feature_payload, side="short")
        long_lead_lag = TradingDecisionAgent._lead_lag_side_context(feature_payload, side="long")
        short_lead_lag = TradingDecisionAgent._lead_lag_side_context(feature_payload, side="short")
        long_breakout_like = bool(
            feature_payload.breakout.broke_swing_high
            or feature_payload.breakout.range_breakout_direction == "up"
        )
        short_breakout_like = bool(
            feature_payload.breakout.broke_swing_low
            or feature_payload.breakout.range_breakout_direction == "down"
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
        long_add_on_context = self.trading_agent._add_on_candidate_context(
            decision_side="long",
            open_position=position_row,
            features=feature_payload,
            risk_context=evaluation_risk_context,
            position_management_context=position_management_context,
            derivatives_blocking=long_derivatives_blocking,
            lead_lag_blocking=long_lead_lag_blocking,
        )
        short_add_on_context = self.trading_agent._add_on_candidate_context(
            decision_side="short",
            open_position=position_row,
            features=feature_payload,
            risk_context=evaluation_risk_context,
            position_management_context=position_management_context,
            derivatives_blocking=short_derivatives_blocking,
            lead_lag_blocking=short_lead_lag_blocking,
        )
        allowed_add_on_side: str | None = None
        if bool(long_add_on_context.get("candidate")) and bool(long_add_on_context.get("allowed")):
            allowed_add_on_side = "long"
        elif bool(short_add_on_context.get("candidate")) and bool(short_add_on_context.get("allowed")):
            allowed_add_on_side = "short"
        route_context["risk_adding_add_on_candidate"] = bool(allowed_add_on_side)
        route_context["risk_adding_add_on_side"] = allowed_add_on_side
        route_context["allow_same_side_add_on"] = bool(allowed_add_on_side)
        route_context["allowed_add_on_side"] = allowed_add_on_side
        sync_freshness_summary = _as_dict(decision_reference.get("sync_freshness_summary"))
        fingerprint_basis = self._build_active_position_entry_fingerprint_basis(
            symbol=symbol,
            timeframe=timeframe,
            position_row=position_row,
            feature_payload=feature_payload,
            position_management=position_management_context,
            strategy_engine_name=strategy_engine_name,
            holding_profile=holding_profile,
            runtime_state=runtime_state,
            sync_freshness_summary=sync_freshness_summary,
            missing_protection_symbols=missing_protection_symbols,
            allow_same_side_add_on=bool(allowed_add_on_side),
            allowed_add_on_side=allowed_add_on_side,
        )
        if review_trigger_payload is None:
            suppression_reason_code = self._active_position_entry_suppression_reason_code(
                latest_metadata=latest_decision_metadata,
                latest_risk_row=self._latest_risk_check_for_decision_run(
                    decision_run_id=latest_decision_run.id if latest_decision_run is not None else None,
                ),
                current_fingerprint_basis=fingerprint_basis,
            )
            if suppression_reason_code is not None:
                route_context["allow_same_side_add_on"] = False
                route_context["allowed_add_on_side"] = None
                route_context["entry_proposal_suppression_active"] = True
                route_context["entry_proposal_suppressed_reason_code"] = suppression_reason_code
        return route_context, fingerprint_basis

    @staticmethod
    def _mark_risk_adding_add_on_pre_ai_blocked(
        route_context: dict[str, object] | None,
        reason_code: str | None,
    ) -> bool:
        if not route_context or not reason_code:
            return False
        if not bool(
            route_context.get("risk_adding_add_on_candidate")
            or route_context.get("allow_same_side_add_on")
        ):
            return False
        route_context["allow_same_side_add_on"] = False
        route_context["allowed_add_on_side"] = None
        route_context["risk_adding_add_on_pre_ai_blocked"] = True
        route_context["risk_adding_add_on_pre_ai_block_reason"] = reason_code
        route_context["risk_adding_add_on_pre_ai_reason_codes"] = [reason_code]
        return True

    @staticmethod
    def _active_position_suppression_audit_payload(route_context: dict[str, object] | None) -> dict[str, object]:
        context = _as_dict(route_context)
        if not context:
            return {}
        allowed_add_on_side = str(context.get("allowed_add_on_side") or "").lower()
        risk_adding_add_on_side = str(context.get("risk_adding_add_on_side") or "").lower()
        return {
            "suppression_active": bool(context.get("entry_proposal_suppression_active")),
            "suppression_reason_code": str(context.get("entry_proposal_suppressed_reason_code") or "") or None,
            "allow_same_side_add_on": bool(context.get("allow_same_side_add_on")),
            "allowed_add_on_side": (
                allowed_add_on_side if allowed_add_on_side in {"long", "short"} else None
            ),
            "risk_adding_add_on_candidate": bool(context.get("risk_adding_add_on_candidate")),
            "risk_adding_add_on_side": (
                risk_adding_add_on_side if risk_adding_add_on_side in {"long", "short"} else None
            ),
            "risk_adding_add_on_pre_ai_blocked": bool(
                context.get("risk_adding_add_on_pre_ai_blocked")
            ),
            "risk_adding_add_on_pre_ai_block_reason": (
                str(context.get("risk_adding_add_on_pre_ai_block_reason") or "") or None
            ),
            "risk_adding_add_on_pre_ai_reason_codes": list(
                context.get("risk_adding_add_on_pre_ai_reason_codes") or []
            ),
        }

    def _interval_plan_active_position_suppression_payload(
        self,
        *,
        symbol: str,
        timeframe: str,
        open_positions: list[Position],
        latest_decision_run: AgentRun | None,
        latest_decision_metadata: dict[str, object],
        latest_decision_output: dict[str, object],
        runtime_state: dict[str, object],
        sync_freshness_summary: dict[str, object],
        upto_index: int | None,
        force_stale: bool,
    ) -> dict[str, object]:
        if not open_positions:
            return {}
        fallback_payload = self._active_position_suppression_audit_payload(
            _as_dict(latest_decision_metadata.get("active_position_prompt_route_context"))
        )
        if fallback_payload:
            return fallback_payload
        latest_feature_snapshot = self._latest_feature_snapshot_payload(
            symbol=symbol,
            timeframe=timeframe,
        )
        if not latest_feature_snapshot:
            return fallback_payload
        try:
            feature_payload = FeaturePayload.model_validate(latest_feature_snapshot)
        except ValidationError:
            return fallback_payload
        selection_context = self._default_selection_context_for_symbol(
            symbol=symbol,
            timeframe=timeframe,
            upto_index=upto_index,
            force_stale=force_stale,
        )
        if not isinstance(selection_context, dict):
            selection_context = {}
        position_management_context = build_position_management_context(
            open_positions[0],
            feature_payload=feature_payload,
            settings_row=self.settings_row,
        )
        route_context, _fingerprint_basis = self._build_active_position_prompt_route_context(
            symbol=symbol,
            timeframe=timeframe,
            open_positions=open_positions,
            feature_payload=feature_payload,
            position_management_context=position_management_context,
            risk_context=(
                {"selection_context": dict(selection_context)}
                if selection_context
                else {}
            ),
            selection_context=dict(selection_context),
            runtime_state=runtime_state,
            latest_decision_run=latest_decision_run,
            latest_decision_metadata=latest_decision_metadata,
            latest_decision_output=latest_decision_output,
            review_trigger_payload=None,
            decision_reference={
                "sync_freshness_summary": dict(sync_freshness_summary or {}),
            },
        )
        return self._active_position_suppression_audit_payload(route_context)

    @staticmethod
    def _previous_open_position_fingerprint_basis(
        *,
        latest_trigger_payload: dict[str, object],
        latest_metadata: dict[str, object],
        latest_output_payload: dict[str, object],
    ) -> dict[str, object]:
        fingerprint_basis = _as_dict(latest_trigger_payload.get("fingerprint_basis"))
        if fingerprint_basis:
            return fingerprint_basis
        ai_context = _as_dict(latest_metadata.get("ai_context"))
        previous_thesis = _as_dict(ai_context.get("previous_thesis"))
        strategy_engine_name = (
            str(latest_trigger_payload.get("strategy_engine") or "")
            or _strategy_engine_name_from_payload(latest_metadata, latest_output_payload)
        )
        holding_profile = (
            str(latest_trigger_payload.get("holding_profile") or "")
            or str(_as_dict(latest_output_payload).get("holding_profile") or "")
            or str(latest_metadata.get("holding_profile") or "")
            or "scalp"
        )
        hard_stop_active = ai_context.get("hard_stop_active")
        if hard_stop_active in {None, ""}:
            hard_stop_active = latest_metadata.get("hard_stop_active")
        if hard_stop_active in {None, ""}:
            hard_stop_active = True
        stop_widening_allowed = ai_context.get("stop_widening_allowed")
        if stop_widening_allowed in {None, ""}:
            stop_widening_allowed = latest_metadata.get("stop_widening_allowed")
        if stop_widening_allowed in {None, ""}:
            stop_widening_allowed = False
        data_quality_grade = str(
            _as_dict(ai_context.get("data_quality")).get("data_quality_grade")
            or latest_metadata.get("data_quality_grade")
            or "complete"
        )
        protection_health_summary = (
            str(latest_trigger_payload.get("protection_health_summary") or "")
            or str(latest_metadata.get("protection_health_summary") or "")
            or "unknown"
        )
        position_state_bucket = (
            str(latest_trigger_payload.get("position_state_bucket") or "")
            or str(latest_metadata.get("position_state_bucket") or "")
            or "unknown"
        )
        return {
            "strategy_engine": strategy_engine_name or "unspecified_engine",
            "holding_profile": holding_profile,
            "hard_stop_active": bool(hard_stop_active),
            "stop_widening_allowed": bool(stop_widening_allowed),
            "regime_summary": TradingOrchestrator._regime_summary_from_ai_context(ai_context),
            "data_quality_grade": data_quality_grade,
            "thesis_degrade_detected": bool(previous_thesis.get("thesis_degrade_detected", False)),
            "position_state_bucket": position_state_bucket,
            "protection_health_summary": protection_health_summary,
        }

    @staticmethod
    def _fingerprint_changed_fields(
        current_basis: dict[str, object],
        previous_basis: dict[str, object],
    ) -> list[str]:
        if not previous_basis:
            return []
        changed_fields: list[str] = []
        for key in current_basis:
            if current_basis.get(key) != previous_basis.get(key):
                changed_fields.append(key)
        for key in previous_basis:
            if key not in current_basis:
                changed_fields.append(key)
        return list(dict.fromkeys(changed_fields))

    def _build_open_position_recheck_fingerprint_basis(
        self,
        *,
        symbol: str,
        timeframe: str,
        position_row: Position,
        position_management: dict[str, object],
        latest_metadata: dict[str, object],
        latest_output_payload: dict[str, object],
        strategy_engine_name: str,
        holding_profile: str,
        runtime_state: dict[str, object],
        missing_protection_symbols: set[str],
        sync_freshness_summary: dict[str, object],
    ) -> dict[str, object]:
        latest_ai_context = _as_dict(latest_metadata.get("ai_context"))
        latest_feature_snapshot = self._latest_feature_snapshot_payload(
            symbol=symbol,
            timeframe=timeframe,
        )
        regime_summary = self._regime_summary_from_feature_snapshot(
            latest_feature_snapshot,
            fallback_summary=self._regime_summary_from_ai_context(latest_ai_context),
        )
        fallback_data_quality_grade = str(
            _as_dict(latest_ai_context.get("data_quality")).get("data_quality_grade") or ""
        ) or None
        hard_stop_active = position_management.get("hard_stop_active")
        if hard_stop_active in {None, ""}:
            hard_stop_active = latest_ai_context.get("hard_stop_active")
        if hard_stop_active in {None, ""}:
            hard_stop_active = getattr(position_row, "stop_loss", None) not in {None, ""}
        stop_widening_allowed = position_management.get("stop_widening_allowed")
        if stop_widening_allowed in {None, ""}:
            stop_widening_allowed = latest_ai_context.get("stop_widening_allowed")
        if stop_widening_allowed in {None, ""}:
            stop_widening_allowed = False
        previous_thesis = _as_dict(latest_ai_context.get("previous_thesis"))
        return {
            "strategy_engine": strategy_engine_name or "unspecified_engine",
            "holding_profile": holding_profile,
            "hard_stop_active": bool(hard_stop_active),
            "stop_widening_allowed": bool(stop_widening_allowed),
            "regime_summary": regime_summary,
            "data_quality_grade": self._data_quality_grade_for_review(
                sync_freshness_summary,
                fallback_grade=fallback_data_quality_grade,
            ),
            "thesis_degrade_detected": bool(previous_thesis.get("thesis_degrade_detected", False)),
            "position_state_bucket": self._position_state_bucket(position_row),
            "protection_health_summary": self._protection_health_summary(
                symbol=symbol,
                position_row=position_row,
                position_management=position_management,
                runtime_state=runtime_state,
                missing_protection_symbols=missing_protection_symbols,
            ),
        }

    @staticmethod
    def _next_due_at(*values: datetime | None) -> datetime | None:
        due_values = [value for value in values if isinstance(value, datetime)]
        return min(due_values) if due_values else None

    @staticmethod
    def _trigger_fingerprint(payload: dict[str, object]) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]

    def _build_review_trigger_payload(
        self,
        *,
        trigger_reason: str,
        symbol: str,
        timeframe: str,
        strategy_engine: str | None,
        holding_profile: str | None,
        assigned_slot: str | None,
        candidate_weight: object,
        reason_codes: list[str],
        last_decision_at: datetime | None,
        last_material_review_at: datetime | None = None,
        triggered_at: datetime,
        fingerprint_material: dict[str, object],
        fingerprint_basis: dict[str, object] | None = None,
        fingerprint_changed_fields: list[str] | None = None,
        dedupe_reason: str | None = None,
        forced_review_reason: str | None = None,
        applied_review_cadence_minutes: int | None = None,
        review_cadence_source: str | None = None,
        holding_profile_cadence_hint: dict[str, object] | None = None,
        cadence_fallback_reason: str | None = None,
        max_review_age_minutes: int | None = None,
        cadence_profile_summary: dict[str, object] | None = None,
    ) -> AIReviewTriggerPayload:
        return AIReviewTriggerPayload(
            trigger_reason=trigger_reason,  # type: ignore[arg-type]
            symbol=symbol,
            timeframe=timeframe,
            strategy_engine=strategy_engine or None,
            holding_profile=(holding_profile or "scalp"),  # type: ignore[arg-type]
            assigned_slot=assigned_slot or None,
            candidate_weight=_safe_float(candidate_weight, default=0.0)
            if candidate_weight not in {None, ""}
            else None,
            reason_codes=list(dict.fromkeys([str(code) for code in reason_codes if code])),
            trigger_fingerprint=self._trigger_fingerprint(fingerprint_material),
            fingerprint_basis=dict(fingerprint_basis or {}),
            fingerprint_changed_fields=list(fingerprint_changed_fields or []),
            dedupe_reason=dedupe_reason,
            last_decision_at=last_decision_at,
            last_material_review_at=last_material_review_at,
            forced_review_reason=forced_review_reason,
            applied_review_cadence_minutes=applied_review_cadence_minutes,
            review_cadence_source=review_cadence_source,
            holding_profile_cadence_hint=dict(holding_profile_cadence_hint or {}),
            cadence_fallback_reason=cadence_fallback_reason,
            max_review_age_minutes=max_review_age_minutes,
            cadence_profile_summary=dict(cadence_profile_summary or {}),
            triggered_at=triggered_at,
        )

    @staticmethod
    def _review_trigger_payload_from_input(
        review_trigger: AIReviewTriggerPayload | dict[str, object] | None,
    ) -> AIReviewTriggerPayload | None:
        if isinstance(review_trigger, AIReviewTriggerPayload):
            return review_trigger
        if isinstance(review_trigger, dict) and review_trigger:
            triggered_at = _coerce_datetime(review_trigger.get("triggered_at"))
            if triggered_at is None:
                return None
            try:
                return AIReviewTriggerPayload(
                    trigger_reason=str(review_trigger.get("trigger_reason") or "manual_review_event"),  # type: ignore[arg-type]
                    symbol=str(review_trigger.get("symbol") or ""),
                    timeframe=str(review_trigger.get("timeframe") or ""),
                    strategy_engine=str(review_trigger.get("strategy_engine") or "") or None,
                    holding_profile=str(review_trigger.get("holding_profile") or "") or None,  # type: ignore[arg-type]
                    assigned_slot=str(review_trigger.get("assigned_slot") or "") or None,
                    candidate_weight=_safe_float(review_trigger.get("candidate_weight"), default=0.0)
                    if review_trigger.get("candidate_weight") not in {None, ""}
                    else None,
                    reason_codes=[
                        str(code)
                        for code in review_trigger.get("reason_codes", [])
                        if code not in {None, ""}
                    ]
                    if isinstance(review_trigger.get("reason_codes"), list)
                    else [],
                    trigger_fingerprint=str(review_trigger.get("trigger_fingerprint") or ""),
                    fingerprint_basis=_as_dict(review_trigger.get("fingerprint_basis")),
                    fingerprint_changed_fields=[
                        str(field)
                        for field in review_trigger.get("fingerprint_changed_fields", [])
                        if field not in {None, ""}
                    ]
                    if isinstance(review_trigger.get("fingerprint_changed_fields"), list)
                    else [],
                    dedupe_reason=str(review_trigger.get("dedupe_reason") or "") or None,
                    last_decision_at=_coerce_datetime(review_trigger.get("last_decision_at")),
                    last_material_review_at=_coerce_datetime(review_trigger.get("last_material_review_at")),
                    forced_review_reason=str(review_trigger.get("forced_review_reason") or "") or None,
                    applied_review_cadence_minutes=(
                        int(review_trigger.get("applied_review_cadence_minutes"))
                        if isinstance(review_trigger.get("applied_review_cadence_minutes"), (int, float))
                        and int(review_trigger.get("applied_review_cadence_minutes")) > 0
                        else None
                    ),
                    review_cadence_source=str(review_trigger.get("review_cadence_source") or "") or None,
                    holding_profile_cadence_hint=_as_dict(review_trigger.get("holding_profile_cadence_hint")),
                    cadence_fallback_reason=str(review_trigger.get("cadence_fallback_reason") or "") or None,
                    max_review_age_minutes=(
                        int(review_trigger.get("max_review_age_minutes"))
                        if isinstance(review_trigger.get("max_review_age_minutes"), (int, float))
                        and int(review_trigger.get("max_review_age_minutes")) > 0
                        else None
                    ),
                    cadence_profile_summary=_as_dict(review_trigger.get("cadence_profile_summary")),
                    triggered_at=triggered_at,
                )
            except Exception:
                return None
        return None

    @staticmethod
    def _entry_candidate_weak_volume_preai_skip_reason(
        *,
        review_trigger_payload: AIReviewTriggerPayload | None,
        feature_payload: FeaturePayload,
        open_positions: list[Position],
    ) -> str | None:
        if review_trigger_payload is None:
            return None
        if review_trigger_payload.trigger_reason != "entry_candidate_event":
            return None
        if open_positions:
            return None

        regime = feature_payload.regime
        volume_ratio: float | None = None
        if feature_payload.volume_ratio is not None:
            try:
                volume_ratio = float(feature_payload.volume_ratio)
            except (TypeError, ValueError):
                volume_ratio = None
        volume_regime = str(getattr(regime, "volume_regime", "") or "").strip().lower()
        weak_volume_signal = (
            bool(getattr(regime, "weak_volume", False))
            or volume_regime in ENTRY_CANDIDATE_WEAK_VOLUME_REGIMES
        )
        if volume_ratio is not None and volume_ratio <= ENTRY_CANDIDATE_EXTREME_LOW_VOLUME_RATIO:
            return ENTRY_CANDIDATE_WEAK_VOLUME_PREAI_REASON
        if weak_volume_signal:
            return ENTRY_CANDIDATE_WEAK_VOLUME_PREAI_REASON
        return None

    @staticmethod
    def _entry_candidate_soft_ai_context_reason_codes(
        *,
        review_trigger_payload: AIReviewTriggerPayload | None,
        feature_payload: FeaturePayload,
        open_positions: list[Position],
        cadence_profile: dict[str, object],
        selection_context: dict[str, object],
    ) -> list[str]:
        reason_codes: list[str] = []
        if (
            TradingOrchestrator._entry_candidate_weak_volume_preai_skip_reason(
                review_trigger_payload=review_trigger_payload,
                feature_payload=feature_payload,
                open_positions=open_positions,
            )
            == ENTRY_CANDIDATE_WEAK_VOLUME_PREAI_REASON
        ):
            reason_codes.append(ENTRY_CANDIDATE_WEAK_VOLUME_CONTEXT_REASON)
        cadence_reasons = {str(item) for item in cadence_profile.get("reasons", []) if item}
        cadence_skip_reason = str(cadence_profile.get("skip_reason") or "")
        if "RANGE_WEAK_VOLUME_NO_TRADE_ZONE" in cadence_reasons or cadence_skip_reason == "RANGE_WEAK_VOLUME_NO_TRADE_ZONE":
            reason_codes.append("RANGE_WEAK_VOLUME_NO_TRADE_ZONE")
        rejected_reason = str(selection_context.get("rejected_reason") or "").strip().lower()
        if rejected_reason in AI_REVIEW_SOFT_REJECTED_REASONS:
            reason_codes.append(SOFT_SIGNAL_AI_REVIEW_REASON_CODE)
        selection_ai_policy = _as_dict(selection_context.get("ai_call_policy"))
        if str(selection_ai_policy.get("soft_signal_review_mode") or "") == "transition_watch":
            reason_codes.append(SOFT_SIGNAL_TRANSITION_WATCH_REASON_CODE)
        return list(dict.fromkeys(reason_codes))

    @staticmethod
    def _soft_ai_review_symbol(candidate_selection: dict[str, object]) -> str | None:
        selected_symbols = {
            str(item).upper()
            for item in candidate_selection.get("selected_symbols", [])
            if item
        }
        if selected_symbols:
            return None
        rankings = candidate_selection.get("rankings")
        if not isinstance(rankings, list):
            return None
        for item in rankings:
            ranking_payload = _as_dict(item)
            rejected_reason = str(ranking_payload.get("rejected_reason") or "").strip().lower()
            if rejected_reason not in AI_REVIEW_SOFT_REJECTED_REASONS:
                continue
            candidate_payload = _as_dict(ranking_payload.get("candidate"))
            candidate_decision = str(candidate_payload.get("decision") or "").lower()
            if candidate_decision not in {"hold", "long", "short"}:
                continue
            symbol = str(ranking_payload.get("symbol") or candidate_payload.get("symbol") or "").upper()
            if symbol:
                return symbol
        return None

    @staticmethod
    def _soft_signal_ai_review_cooldown_minutes(effective_settings: object) -> int:
        del effective_settings
        return SOFT_SIGNAL_TRANSITION_WATCH_COOLDOWN_MINUTES

    @staticmethod
    def _soft_signal_review_metrics(selection_context: dict[str, object]) -> dict[str, object]:
        score_payload = _as_dict(selection_context.get("score"))
        score_total = _safe_float(score_payload.get("total_score"), default=0.0)
        entry_score_threshold = _safe_float(selection_context.get("entry_score_threshold"), default=0.0)
        threshold_gap = (
            round(entry_score_threshold - score_total, 6)
            if entry_score_threshold > 0.0 and score_total > 0.0
            else None
        )
        return {
            "score_total": score_total,
            "entry_score_threshold": entry_score_threshold if entry_score_threshold > 0.0 else None,
            "threshold_gap": threshold_gap,
            "slot_conviction_score": _safe_float(
                selection_context.get("slot_conviction_score"),
                default=0.0,
            ),
            "meta_gate_probability": _safe_float(
                selection_context.get("meta_gate_probability"),
                default=0.0,
            ),
        }

    @staticmethod
    def _optional_summary_float(value: object) -> float | None:
        if value is None or value == "":
            return None
        try:
            return round(float(value), 6)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _reason_suffix(reason_codes: list[str], prefix: str) -> str | None:
        for code in reason_codes:
            if code.startswith(prefix):
                return code.removeprefix(prefix).lower()
        return None

    @classmethod
    def _soft_signal_review_snapshot(
        cls,
        selection_context: dict[str, object],
    ) -> dict[str, object]:
        selection = _as_dict(selection_context)
        candidate = _as_dict(selection.get("candidate"))
        score_payload = _as_dict(selection.get("score"))
        metrics = cls._soft_signal_review_metrics(selection)
        regime_summary = _as_dict(selection.get("regime_summary"))
        reason_codes = [
            str(code)
            for code in (
                candidate.get("rationale_codes")
                or selection.get("reason_codes")
                or []
            )
            if code
        ]
        primary_regime = str(regime_summary.get("primary_regime") or "").lower()
        if not primary_regime:
            primary_regime = cls._reason_suffix(reason_codes, "REGIME_") or ""
        trend_alignment = str(regime_summary.get("trend_alignment") or "").lower()
        if not trend_alignment:
            trend_alignment = cls._reason_suffix(reason_codes, "TREND_") or ""
        lead_lag_summary = _as_dict(candidate.get("lead_lag_summary"))
        derivatives_summary = _as_dict(candidate.get("derivatives_summary"))
        return {
            "decision": str(candidate.get("decision") or "").lower(),
            "scenario": str(candidate.get("scenario") or selection.get("scenario") or "").lower(),
            "strategy_engine": str(
                selection.get("strategy_engine") or candidate.get("strategy_engine") or ""
            ),
            "holding_profile": str(
                selection.get("holding_profile") or candidate.get("holding_profile") or ""
            ),
            "entry_mode": str(selection.get("entry_mode") or "").lower(),
            "breadth_regime": str(selection.get("breadth_regime") or "").lower(),
            "capacity_reason": str(selection.get("capacity_reason") or "").lower(),
            "primary_regime": primary_regime,
            "trend_alignment": trend_alignment,
            "volume_regime": str(regime_summary.get("volume_regime") or "").lower(),
            "weak_volume": bool(regime_summary.get("weak_volume", False)),
            "momentum_weakening": bool(regime_summary.get("momentum_weakening", False)),
            "score_total": cls._optional_summary_float(metrics.get("score_total")),
            "entry_score_threshold": cls._optional_summary_float(
                metrics.get("entry_score_threshold")
            ),
            "threshold_gap": cls._optional_summary_float(metrics.get("threshold_gap")),
            "slot_conviction_score": cls._optional_summary_float(
                metrics.get("slot_conviction_score")
            ),
            "meta_gate_probability": cls._optional_summary_float(
                metrics.get("meta_gate_probability")
            ),
            "lead_lag_alignment": cls._optional_summary_float(
                score_payload.get("lead_lag_alignment")
            ),
            "derivatives_alignment": cls._optional_summary_float(
                score_payload.get("derivatives_alignment")
            ),
            "lead_lag_state": {
                "leader_bias": str(lead_lag_summary.get("leader_bias") or "").lower(),
                "strong_reference_confirmation": bool(
                    lead_lag_summary.get("strong_reference_confirmation", False)
                ),
                "weak_reference_confirmation": bool(
                    lead_lag_summary.get("weak_reference_confirmation", False)
                ),
                "bullish_breakout_confirmed": bool(
                    lead_lag_summary.get("bullish_breakout_confirmed", False)
                ),
                "bearish_breakout_confirmed": bool(
                    lead_lag_summary.get("bearish_breakout_confirmed", False)
                ),
                "bullish_breakout_ahead": bool(
                    lead_lag_summary.get("bullish_breakout_ahead", False)
                ),
                "bearish_breakout_ahead": bool(
                    lead_lag_summary.get("bearish_breakout_ahead", False)
                ),
            },
            "derivatives_state": {
                "taker_flow_alignment": str(
                    derivatives_summary.get("taker_flow_alignment") or ""
                ).lower(),
                "funding_bias": str(derivatives_summary.get("funding_bias") or "").lower(),
                "crowding_bias": str(derivatives_summary.get("crowding_bias") or "").lower(),
                "spread_headwind": bool(derivatives_summary.get("spread_headwind", False)),
                "spread_stress": bool(derivatives_summary.get("spread_stress", False)),
                "veto_reason_codes": sorted(
                    str(code)
                    for code in derivatives_summary.get("veto_reason_codes", [])
                    if code
                ),
            },
        }

    @classmethod
    def _previous_soft_signal_review_snapshot(
        cls,
        latest_decision_metadata: dict[str, object],
    ) -> dict[str, object]:
        snapshot = _as_dict(latest_decision_metadata.get("soft_signal_review_snapshot"))
        if snapshot:
            return snapshot
        selection_context = _as_dict(latest_decision_metadata.get("selection_context"))
        if selection_context:
            return cls._soft_signal_review_snapshot(selection_context)
        return {}

    @classmethod
    def _soft_signal_review_material_change(
        cls,
        *,
        current_snapshot: dict[str, object],
        previous_snapshot: dict[str, object],
    ) -> dict[str, object]:
        if not previous_snapshot:
            return {
                "material_changed": True,
                "reason": "missing_previous_snapshot",
                "changed_fields": ["previous_snapshot"],
                "current_snapshot": current_snapshot,
                "previous_snapshot": {},
            }
        categorical_keys = [
            "decision",
            "scenario",
            "strategy_engine",
            "holding_profile",
            "entry_mode",
            "breadth_regime",
            "capacity_reason",
            "primary_regime",
            "trend_alignment",
            "volume_regime",
            "weak_volume",
            "momentum_weakening",
            "lead_lag_state",
            "derivatives_state",
        ]
        changed_fields = [
            key
            for key in categorical_keys
            if current_snapshot.get(key) != previous_snapshot.get(key)
        ]
        numeric_thresholds = {
            "score_total": SOFT_SIGNAL_TRANSITION_WATCH_MATERIAL_SCORE_DELTA,
            "threshold_gap": SOFT_SIGNAL_TRANSITION_WATCH_MATERIAL_SCORE_DELTA,
            "slot_conviction_score": SOFT_SIGNAL_TRANSITION_WATCH_MATERIAL_PROBABILITY_DELTA,
            "meta_gate_probability": SOFT_SIGNAL_TRANSITION_WATCH_MATERIAL_PROBABILITY_DELTA,
            "lead_lag_alignment": SOFT_SIGNAL_TRANSITION_WATCH_MATERIAL_ALIGNMENT_DELTA,
            "derivatives_alignment": SOFT_SIGNAL_TRANSITION_WATCH_MATERIAL_ALIGNMENT_DELTA,
        }
        deltas: dict[str, float] = {}
        for key, threshold in numeric_thresholds.items():
            current_value = cls._optional_summary_float(current_snapshot.get(key))
            previous_value = cls._optional_summary_float(previous_snapshot.get(key))
            if current_value is None or previous_value is None:
                continue
            delta = round(current_value - previous_value, 6)
            deltas[key] = delta
            if abs(delta) >= threshold:
                changed_fields.append(key)
        material_changed = bool(changed_fields)
        return {
            "material_changed": material_changed,
            "reason": "material_change_detected" if material_changed else "no_material_change",
            "changed_fields": list(dict.fromkeys(changed_fields)),
            "deltas": deltas,
            "thresholds": numeric_thresholds,
            "current_snapshot": current_snapshot,
            "previous_snapshot": previous_snapshot,
        }

    @classmethod
    def _soft_signal_actionable_review_context(
        cls,
        *,
        selection_context: dict[str, object],
        current_snapshot: dict[str, object],
        material_change: dict[str, object] | None,
    ) -> dict[str, object]:
        slot_conviction_score = _safe_float(
            selection_context.get("slot_conviction_score"),
            default=0.0,
        )
        meta_gate_probability = _safe_float(
            selection_context.get("meta_gate_probability"),
            default=0.0,
        )
        lead_lag_state = _as_dict(current_snapshot.get("lead_lag_state"))
        derivatives_state = _as_dict(current_snapshot.get("derivatives_state"))
        strong_lead_lag = any(
            bool(lead_lag_state.get(key))
            for key in (
                "strong_reference_confirmation",
                "bullish_breakout_confirmed",
                "bearish_breakout_confirmed",
                "bullish_breakout_ahead",
                "bearish_breakout_ahead",
            )
        )
        directional_derivatives = any(
            str(derivatives_state.get(key) or "").lower()
            not in {"", "neutral", "unknown", "mixed", "none"}
            for key in ("taker_flow_alignment", "funding_bias", "crowding_bias")
        )
        changed_fields = {
            str(field)
            for field in _as_dict(material_change).get("changed_fields", [])
            if field
        }
        material_changed = bool(
            material_change
            and material_change.get("material_changed")
            and material_change.get("reason") != "missing_previous_snapshot"
        )
        structural_change = bool(
            material_changed
            and changed_fields.intersection(
                {
                    "decision",
                    "scenario",
                    "strategy_engine",
                    "holding_profile",
                    "primary_regime",
                    "trend_alignment",
                    "volume_regime",
                    "lead_lag_state",
                    "derivatives_state",
                }
            )
        )
        quality_exception = (
            slot_conviction_score >= SOFT_SIGNAL_ACTIONABLE_REVIEW_MIN_SLOT_CONVICTION
            or meta_gate_probability >= SOFT_SIGNAL_ACTIONABLE_REVIEW_MIN_META_GATE_PROBABILITY
            or strong_lead_lag
            or directional_derivatives
        )
        return {
            "actionable_review": bool(quality_exception or structural_change),
            "slot_conviction_score": round(slot_conviction_score, 6),
            "meta_gate_probability": round(meta_gate_probability, 6),
            "quality_exception": bool(quality_exception),
            "structural_change": bool(structural_change),
            "strong_lead_lag": bool(strong_lead_lag),
            "directional_derivatives": bool(directional_derivatives),
            "changed_fields": sorted(changed_fields),
        }

    @classmethod
    def _soft_signal_low_edge_hold_suppression_context(
        cls,
        *,
        selection_context: dict[str, object],
        current_snapshot: dict[str, object],
        soft_rejected_reason: str,
        material_change: dict[str, object] | None,
    ) -> dict[str, object]:
        candidate = _as_dict(selection_context.get("candidate"))
        candidate_decision = str(candidate.get("decision") or "").lower()
        if candidate_decision != "hold" or soft_rejected_reason != "low_edge_hold_candidate":
            return {"active": False, "candidate_decision": candidate_decision}

        slot_conviction_score = _safe_float(
            selection_context.get("slot_conviction_score"),
            default=0.0,
        )
        meta_gate_probability = _safe_float(
            selection_context.get("meta_gate_probability"),
            default=0.0,
        )
        weak_quality = (
            slot_conviction_score <= SOFT_SIGNAL_LOW_EDGE_HOLD_MAX_SLOT_CONVICTION
            and meta_gate_probability <= SOFT_SIGNAL_LOW_EDGE_HOLD_MAX_META_GATE_PROBABILITY
        )
        actionable_context = cls._soft_signal_actionable_review_context(
            selection_context=selection_context,
            current_snapshot=current_snapshot,
            material_change=material_change,
        )
        return {
            "active": bool(weak_quality and not actionable_context["actionable_review"]),
            "candidate_decision": candidate_decision,
            "weak_quality": bool(weak_quality),
            "soft_rejected_reason": soft_rejected_reason,
            "slot_conviction_score": round(slot_conviction_score, 6),
            "meta_gate_probability": round(meta_gate_probability, 6),
            "max_slot_conviction": SOFT_SIGNAL_LOW_EDGE_HOLD_MAX_SLOT_CONVICTION,
            "max_meta_gate_probability": SOFT_SIGNAL_LOW_EDGE_HOLD_MAX_META_GATE_PROBABILITY,
            "agreement_level_hint": str(selection_context.get("agreement_level_hint") or "") or None,
            "agreement_alignment_score": cls._optional_summary_float(
                selection_context.get("agreement_alignment_score")
            ),
            "actionable_context": actionable_context,
        }

    @staticmethod
    def _agent_run_is_provider_attempt(row: AgentRun) -> bool:
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        return row.provider_name == "openai" or str(metadata.get("source") or "") in {
            "llm",
            "llm_fallback",
        }

    @staticmethod
    def _agent_run_has_soft_signal_context(row: AgentRun) -> bool:
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        ai_call_policy = _as_dict(metadata.get("ai_call_policy"))
        ai_trigger = _as_dict(metadata.get("ai_trigger"))
        raw_reason_values: list[object] = []
        for raw_values in (
            metadata.get("allow_ai_but_later_risk_check"),
            ai_call_policy.get("allow_ai_but_later_risk_check"),
            ai_trigger.get("reason_codes"),
        ):
            if isinstance(raw_values, list):
                raw_reason_values.extend(raw_values)
            elif isinstance(raw_values, str):
                raw_reason_values.append(raw_values)
        reason_codes = {
            str(code)
            for code in raw_reason_values
            if code
        }
        return (
            SOFT_SIGNAL_AI_REVIEW_REASON_CODE in reason_codes
            or SOFT_SIGNAL_TRANSITION_WATCH_REASON_CODE in reason_codes
            or str(metadata.get("soft_signal_review_mode") or "")
            in {"transition_watch", "transition_watch_cooldown", "transition_watch_no_material_change"}
        )

    @staticmethod
    def _reason_codes_from_payload(payload: dict[str, object]) -> set[str]:
        values: list[object] = []
        for key in ("rationale_codes", "reason_codes", "no_trade_reason_codes"):
            raw_values = payload.get(key)
            if isinstance(raw_values, list):
                values.extend(raw_values)
            elif isinstance(raw_values, str):
                values.append(raw_values)
        return {str(code) for code in values if code}

    @classmethod
    def _entry_candidate_reason_codes(
        cls,
        selection_context: dict[str, object],
        *,
        extra_reason_codes: list[str] | None = None,
    ) -> set[str]:
        candidate = _as_dict(selection_context.get("candidate"))
        reason_codes = {
            *cls._reason_codes_from_payload(candidate),
            *cls._reason_codes_from_payload(selection_context),
        }
        for code in extra_reason_codes or []:
            if code:
                reason_codes.add(str(code))
        return reason_codes

    @staticmethod
    def _entry_candidate_decision(selection_context: dict[str, object]) -> str:
        candidate = _as_dict(selection_context.get("candidate"))
        return str(candidate.get("decision") or "").lower()

    @classmethod
    def _selection_has_neutral_entry_context(cls, selection_context: dict[str, object]) -> bool:
        decision = cls._entry_candidate_decision(selection_context)
        if decision not in {"long", "short"}:
            return False
        reason_codes = cls._entry_candidate_reason_codes(selection_context)
        return ENTRY_CANDIDATE_NEUTRAL_CONTEXT_REQUIRED_REASONS.issubset(reason_codes)

    @classmethod
    def _agent_run_has_neutral_entry_context(cls, row: AgentRun) -> bool:
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        output = row.output_payload if isinstance(row.output_payload, dict) else {}
        selection_context = _as_dict(metadata.get("selection_context"))
        selection_candidate = _as_dict(selection_context.get("candidate"))
        reason_codes = {
            *cls._reason_codes_from_payload(output),
            *cls._reason_codes_from_payload(selection_context),
            *cls._reason_codes_from_payload(selection_candidate),
        }
        return ENTRY_CANDIDATE_NEUTRAL_CONTEXT_REQUIRED_REASONS.issubset(reason_codes)

    @classmethod
    def _entry_candidate_weak_volume_context(
        cls,
        selection_context: dict[str, object],
        *,
        allow_ai_but_later_risk_check: list[str] | None = None,
    ) -> dict[str, object]:
        reason_codes = cls._entry_candidate_reason_codes(
            selection_context,
            extra_reason_codes=allow_ai_but_later_risk_check,
        )
        snapshot = cls._soft_signal_review_snapshot(selection_context)
        volume_regime = str(snapshot.get("volume_regime") or "").lower()
        weak_volume = bool(snapshot.get("weak_volume", False))
        momentum_weakening = bool(snapshot.get("momentum_weakening", False))
        active = (
            ENTRY_CANDIDATE_WEAK_VOLUME_CONTEXT_REASON in reason_codes
            or ENTRY_CANDIDATE_WEAK_VOLUME_PREAI_REASON in reason_codes
            or "WEAK_VOLUME" in reason_codes
            or volume_regime in ENTRY_CANDIDATE_WEAK_VOLUME_REGIMES
            or weak_volume
        )
        return {
            "active": bool(active),
            "reason_codes": sorted(reason_codes),
            "volume_regime": volume_regime or None,
            "weak_volume": weak_volume,
            "momentum_weakening": momentum_weakening,
        }

    @classmethod
    def _entry_candidate_low_actionability_signature(
        cls,
        selection_context: dict[str, object],
        *,
        allow_ai_but_later_risk_check: list[str] | None = None,
    ) -> dict[str, object] | None:
        decision = cls._entry_candidate_decision(selection_context)
        if decision not in {"long", "short"}:
            return None
        reason_codes = cls._entry_candidate_reason_codes(
            selection_context,
            extra_reason_codes=allow_ai_but_later_risk_check,
        )
        snapshot = cls._soft_signal_review_snapshot(selection_context)
        weak_context = cls._entry_candidate_weak_volume_context(
            selection_context,
            allow_ai_but_later_risk_check=allow_ai_but_later_risk_check,
        )
        low_reason_codes = {
            code
            for code in reason_codes
            if code in ENTRY_CANDIDATE_LOW_ACTIONABILITY_REASON_CODES
        }
        if bool(weak_context.get("active")):
            low_reason_codes.add(ENTRY_CANDIDATE_WEAK_VOLUME_CONTEXT_REASON)
        if bool(snapshot.get("momentum_weakening", False)):
            low_reason_codes.add("MOMENTUM_WEAKENING")
        if not low_reason_codes:
            return None
        return {
            "decision": decision,
            "reason_codes": sorted(low_reason_codes),
            "strategy_engine": str(
                selection_context.get("strategy_engine")
                or _as_dict(selection_context.get("candidate")).get("strategy_engine")
                or ""
            )
            or None,
        }

    @classmethod
    def _agent_run_low_actionability_signature(cls, row: AgentRun) -> dict[str, object] | None:
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        selection_context = _as_dict(metadata.get("selection_context"))
        if selection_context:
            allow_reasons = [
                str(code)
                for code in metadata.get("allow_ai_but_later_risk_check", [])
                if code
            ] if isinstance(metadata.get("allow_ai_but_later_risk_check"), list) else []
            ai_call_policy = _as_dict(metadata.get("ai_call_policy"))
            if isinstance(ai_call_policy.get("allow_ai_but_later_risk_check"), list):
                allow_reasons.extend(
                    str(code)
                    for code in ai_call_policy.get("allow_ai_but_later_risk_check", [])
                    if code
                )
            return cls._entry_candidate_low_actionability_signature(
                selection_context,
                allow_ai_but_later_risk_check=list(dict.fromkeys(allow_reasons)),
            )
        return None

    @staticmethod
    def _entry_candidate_score_bucket(value: object, *, bucket_size: float = 0.05) -> float | None:
        numeric_value = _safe_float(value, default=float("nan"))
        if numeric_value != numeric_value:
            return None
        bucket = int(max(min(numeric_value, 1.0), -1.0) / bucket_size) * bucket_size
        return round(bucket, 4)

    @classmethod
    def _entry_candidate_ai_hold_signature(
        cls,
        *,
        symbol: str,
        timeframe: str | None,
        selection_context: dict[str, object],
        allow_ai_but_later_risk_check: list[str] | None = None,
    ) -> dict[str, object] | None:
        decision = cls._entry_candidate_decision(selection_context)
        if decision not in {"long", "short"}:
            return None
        snapshot = cls._soft_signal_review_snapshot(selection_context)
        reason_codes = cls._entry_candidate_reason_codes(
            selection_context,
            extra_reason_codes=allow_ai_but_later_risk_check,
        )
        return {
            "symbol": symbol.upper(),
            "timeframe": timeframe or snapshot.get("timeframe") or None,
            "decision": decision,
            "scenario": snapshot.get("scenario") or None,
            "strategy_engine": snapshot.get("strategy_engine") or None,
            "holding_profile": snapshot.get("holding_profile") or None,
            "entry_mode": snapshot.get("entry_mode") or None,
            "reason_codes": sorted(reason_codes),
            "primary_regime": snapshot.get("primary_regime") or None,
            "trend_alignment": snapshot.get("trend_alignment") or None,
            "volume_regime": snapshot.get("volume_regime") or None,
            "weak_volume": bool(snapshot.get("weak_volume", False)),
            "momentum_weakening": bool(snapshot.get("momentum_weakening", False)),
            "score_bucket": cls._entry_candidate_score_bucket(snapshot.get("score_total")),
            "lead_lag_alignment_bucket": cls._entry_candidate_score_bucket(
                snapshot.get("lead_lag_alignment")
            ),
            "derivatives_alignment_bucket": cls._entry_candidate_score_bucket(
                snapshot.get("derivatives_alignment")
            ),
            "lead_lag_state": snapshot.get("lead_lag_state") or {},
            "derivatives_state": snapshot.get("derivatives_state") or {},
        }

    @classmethod
    def _agent_run_entry_candidate_ai_hold_signature(cls, row: AgentRun) -> dict[str, object] | None:
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        stored_signature = _as_dict(metadata.get("entry_candidate_ai_hold_fingerprint"))
        if stored_signature:
            return stored_signature
        selection_context = _as_dict(metadata.get("selection_context"))
        if not selection_context:
            return None
        allow_reasons = [
            str(code)
            for code in metadata.get("allow_ai_but_later_risk_check", [])
            if code
        ] if isinstance(metadata.get("allow_ai_but_later_risk_check"), list) else []
        ai_call_policy = _as_dict(metadata.get("ai_call_policy"))
        if isinstance(ai_call_policy.get("allow_ai_but_later_risk_check"), list):
            allow_reasons.extend(
                str(code)
                for code in ai_call_policy.get("allow_ai_but_later_risk_check", [])
                if code
            )
        output = row.output_payload if isinstance(row.output_payload, dict) else {}
        symbol = str(
            metadata.get("symbol")
            or output.get("symbol")
            or _as_dict(selection_context.get("candidate")).get("symbol")
            or ""
        )
        timeframe = str(
            metadata.get("timeframe")
            or output.get("timeframe")
            or _as_dict(selection_context.get("candidate")).get("timeframe")
            or ""
        ) or None
        if not symbol:
            return None
        return cls._entry_candidate_ai_hold_signature(
            symbol=symbol,
            timeframe=timeframe,
            selection_context=selection_context,
            allow_ai_but_later_risk_check=list(dict.fromkeys(allow_reasons)),
        )

    def _entry_candidate_ai_hold_fingerprint_cooldown_status(
        self,
        *,
        symbol: str,
        timeframe: str | None,
        selection_context: dict[str, object],
        generated_at: datetime,
        allow_ai_but_later_risk_check: list[str] | None = None,
    ) -> dict[str, object]:
        current_signature = self._entry_candidate_ai_hold_signature(
            symbol=symbol,
            timeframe=timeframe,
            selection_context=selection_context,
            allow_ai_but_later_risk_check=allow_ai_but_later_risk_check,
        )
        if current_signature is None:
            return {
                "active": False,
                "reason": "context_not_directional_entry_candidate",
            }
        cutoff = generated_at - timedelta(
            minutes=ENTRY_CANDIDATE_AI_HOLD_FINGERPRINT_COOLDOWN_MINUTES
        )
        rows = list(
            self.session.scalars(
                select(AgentRun)
                .where(
                    AgentRun.role == AgentRole.TRADING_DECISION.value,
                    AgentRun.trigger_event == TriggerEvent.REALTIME.value,
                    AgentRun.created_at >= cutoff,
                )
                .order_by(desc(AgentRun.created_at))
                .limit(ENTRY_CANDIDATE_AI_HOLD_FINGERPRINT_ROW_LIMIT)
            )
        )
        matching_rows: list[AgentRun] = []
        for row in rows:
            if not self._agent_run_is_provider_attempt(row):
                continue
            if not self._agent_run_matches_symbol(row, symbol=symbol.upper(), timeframe=timeframe):
                continue
            if self._agent_run_entry_candidate_ai_hold_signature(row) != current_signature:
                continue
            output = row.output_payload if isinstance(row.output_payload, dict) else {}
            if str(output.get("decision") or "").lower() != "hold":
                continue
            matching_rows.append(row)

        if not matching_rows:
            return {
                "active": False,
                "sample_count": 0,
                "cooldown_minutes": ENTRY_CANDIDATE_AI_HOLD_FINGERPRINT_COOLDOWN_MINUTES,
                "signature": current_signature,
            }

        run_ids = [int(row.id) for row in matching_rows if row.id is not None]
        order_run_ids = {
            int(run_id)
            for run_id in self.session.scalars(
                select(Order.decision_run_id).where(Order.decision_run_id.in_(run_ids))
            )
            if run_id is not None
        } if run_ids else set()
        plan_run_ids = {
            int(run_id)
            for run_id in self.session.scalars(
                select(PendingEntryPlan.source_decision_run_id).where(
                    PendingEntryPlan.source_decision_run_id.in_(run_ids)
                )
            )
            if run_id is not None
        } if run_ids else set()
        reusable_rows = [
            row
            for row in matching_rows
            if row.id is not None and int(row.id) not in order_run_ids and int(row.id) not in plan_run_ids
        ]
        latest_reusable = reusable_rows[0] if reusable_rows else None
        return {
            "active": latest_reusable is not None,
            "sample_count": len(matching_rows),
            "reusable_hold_count": len(reusable_rows),
            "cooldown_minutes": ENTRY_CANDIDATE_AI_HOLD_FINGERPRINT_COOLDOWN_MINUTES,
            "signature": current_signature,
            "decision_run_ids": run_ids,
            "order_decision_run_ids": sorted(order_run_ids),
            "pending_plan_decision_run_ids": sorted(plan_run_ids),
            "reused_decision_run_id": latest_reusable.id if latest_reusable is not None else None,
            "last_provider_call_at": (
                latest_reusable.created_at.isoformat()
                if latest_reusable is not None and latest_reusable.created_at is not None
                else None
            ),
        }

    @staticmethod
    def _risk_check_hold_blocked(row: RiskCheck) -> bool:
        payload = row.payload if isinstance(row.payload, dict) else {}
        reason_codes = {
            str(code)
            for code in [
                *list(row.reason_codes or []),
                *list(payload.get("reason_codes") or []),
                *list(payload.get("blocked_reason_codes") or []),
            ]
            if code
        }
        return bool(row.allowed is False and row.decision == "hold" and "HOLD_DECISION" in reason_codes)

    def _neutral_entry_hold_backoff_status(
        self,
        *,
        symbol: str,
        timeframe: str | None,
        selection_context: dict[str, object],
        generated_at: datetime,
    ) -> dict[str, object]:
        symbol_upper = symbol.upper()
        if not symbol_upper or not self._selection_has_neutral_entry_context(selection_context):
            return {"active": False, "sample_count": 0, "reason": "context_not_neutral_entry"}
        cutoff = generated_at - timedelta(minutes=SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_LOOKBACK_MINUTES)
        rows = list(
            self.session.scalars(
                select(AgentRun)
                .where(
                    AgentRun.role == AgentRole.TRADING_DECISION.value,
                    AgentRun.trigger_event == TriggerEvent.REALTIME.value,
                    AgentRun.created_at >= cutoff,
                )
                .order_by(desc(AgentRun.created_at))
                .limit(SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_ROW_LIMIT)
            )
        )
        recent_rows: list[AgentRun] = []
        for row in rows:
            if not self._agent_run_is_provider_attempt(row):
                continue
            if not self._agent_run_matches_symbol(row, symbol=symbol_upper, timeframe=timeframe):
                continue
            if not self._agent_run_has_neutral_entry_context(row):
                continue
            recent_rows.append(row)
            if len(recent_rows) >= SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_MIN_CALLS:
                break
        if len(recent_rows) < SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_MIN_CALLS:
            return {
                "active": False,
                "sample_count": len(recent_rows),
                "min_provider_calls": SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_MIN_CALLS,
                "lookback_minutes": SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_LOOKBACK_MINUTES,
            }

        run_ids = [int(row.id) for row in recent_rows if row.id is not None]
        risk_rows = list(
            self.session.scalars(select(RiskCheck).where(RiskCheck.decision_run_id.in_(run_ids)))
        )
        risk_by_run_id: dict[int, list[RiskCheck]] = defaultdict(list)
        for risk_row in risk_rows:
            if risk_row.decision_run_id is not None:
                risk_by_run_id[int(risk_row.decision_run_id)].append(risk_row)

        outcomes: list[dict[str, object]] = []
        for row in recent_rows:
            output = row.output_payload if isinstance(row.output_payload, dict) else {}
            decision_run_id = int(row.id) if row.id is not None else None
            risk_hold_blocked = any(
                self._risk_check_hold_blocked(risk_row)
                for risk_row in risk_by_run_id.get(decision_run_id, [])
                if decision_run_id is not None
            )
            outcomes.append(
                {
                    "decision_run_id": row.id,
                    "decision": str(output.get("decision") or "").lower() or None,
                    "risk_hold_blocked": risk_hold_blocked,
                }
            )

        active = all(bool(item["decision"] == "hold" and item["risk_hold_blocked"]) for item in outcomes)
        return {
            "active": active,
            "sample_count": len(recent_rows),
            "min_provider_calls": SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_MIN_CALLS,
            "lookback_minutes": SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_LOOKBACK_MINUTES,
            "decision_run_ids": run_ids,
            "outcomes": outcomes,
            "required_reason_codes": sorted(ENTRY_CANDIDATE_NEUTRAL_CONTEXT_REQUIRED_REASONS),
            "last_provider_call_at": recent_rows[0].created_at.isoformat() if recent_rows else None,
            "oldest_provider_call_at": recent_rows[-1].created_at.isoformat() if recent_rows else None,
        }

    def _entry_candidate_low_actionability_hold_backoff_status(
        self,
        *,
        symbol: str,
        timeframe: str | None,
        selection_context: dict[str, object],
        generated_at: datetime,
        allow_ai_but_later_risk_check: list[str] | None = None,
    ) -> dict[str, object]:
        symbol_upper = symbol.upper()
        current_signature = self._entry_candidate_low_actionability_signature(
            selection_context,
            allow_ai_but_later_risk_check=allow_ai_but_later_risk_check,
        )
        if not symbol_upper or current_signature is None:
            return {
                "active": False,
                "sample_count": 0,
                "reason": "context_not_low_actionability_entry",
            }
        cutoff = generated_at - timedelta(minutes=SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_LOOKBACK_MINUTES)
        rows = list(
            self.session.scalars(
                select(AgentRun)
                .where(
                    AgentRun.role == AgentRole.TRADING_DECISION.value,
                    AgentRun.trigger_event == TriggerEvent.REALTIME.value,
                    AgentRun.created_at >= cutoff,
                )
                .order_by(desc(AgentRun.created_at))
                .limit(SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_ROW_LIMIT)
            )
        )
        recent_rows: list[AgentRun] = []
        for row in rows:
            if not self._agent_run_is_provider_attempt(row):
                continue
            if not self._agent_run_matches_symbol(row, symbol=symbol_upper, timeframe=timeframe):
                continue
            if self._agent_run_low_actionability_signature(row) != current_signature:
                continue
            recent_rows.append(row)
            if len(recent_rows) >= SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_MIN_CALLS:
                break
        if len(recent_rows) < SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_MIN_CALLS:
            return {
                "active": False,
                "sample_count": len(recent_rows),
                "min_provider_calls": SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_MIN_CALLS,
                "lookback_minutes": SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_LOOKBACK_MINUTES,
                "signature": current_signature,
            }

        run_ids = [int(row.id) for row in recent_rows if row.id is not None]
        risk_rows = list(
            self.session.scalars(select(RiskCheck).where(RiskCheck.decision_run_id.in_(run_ids)))
        )
        risk_by_run_id: dict[int, list[RiskCheck]] = defaultdict(list)
        for risk_row in risk_rows:
            if risk_row.decision_run_id is not None:
                risk_by_run_id[int(risk_row.decision_run_id)].append(risk_row)

        outcomes: list[dict[str, object]] = []
        for row in recent_rows:
            output = row.output_payload if isinstance(row.output_payload, dict) else {}
            decision_run_id = int(row.id) if row.id is not None else None
            risk_hold_blocked = any(
                self._risk_check_hold_blocked(risk_row)
                for risk_row in risk_by_run_id.get(decision_run_id, [])
                if decision_run_id is not None
            )
            outcomes.append(
                {
                    "decision_run_id": row.id,
                    "decision": str(output.get("decision") or "").lower() or None,
                    "risk_hold_blocked": risk_hold_blocked,
                }
            )

        active = all(bool(item["decision"] == "hold" and item["risk_hold_blocked"]) for item in outcomes)
        return {
            "active": active,
            "sample_count": len(recent_rows),
            "min_provider_calls": SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_MIN_CALLS,
            "lookback_minutes": SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_LOOKBACK_MINUTES,
            "decision_run_ids": run_ids,
            "outcomes": outcomes,
            "signature": current_signature,
            "last_provider_call_at": recent_rows[0].created_at.isoformat() if recent_rows else None,
            "oldest_provider_call_at": recent_rows[-1].created_at.isoformat() if recent_rows else None,
        }

    @staticmethod
    def _entry_candidate_order_path_block_context(runtime_state: dict[str, object]) -> dict[str, object] | None:
        reason_codes: list[str] = []
        market_stream = _as_dict(runtime_state.get("market_stream_summary"))
        redis_configured = bool(market_stream.get("redis_configured"))
        redis_connected = market_stream.get("redis_connected")
        cache_health = str(market_stream.get("cache_health") or "").strip().lower()
        if redis_configured and (redis_connected is False or cache_health == "unavailable"):
            reason_codes.append("REDIS_CACHE_UNAVAILABLE")

        reconciliation = _as_dict(runtime_state.get("reconciliation_summary"))
        if str(reconciliation.get("status") or "").strip().lower() not in {"", "synced"}:
            reason_codes.append("RECONCILIATION_NOT_SYNCED")
        if (
            bool(reconciliation.get("unresolved_submission_badge"))
            or int(_safe_float(reconciliation.get("unresolved_submission_count"), default=0.0)) > 0
        ):
            reason_codes.append("UNRESOLVED_SUBMISSION")

        binance_rest = _as_dict(runtime_state.get("binance_rest_summary"))
        rest_status = str(binance_rest.get("status") or "ok").strip().lower()
        circuit_state = str(binance_rest.get("circuit_state") or "closed").strip().lower()
        if rest_status not in {"", "ok"}:
            reason_codes.append(str(binance_rest.get("reason_code") or "BINANCE_REST_NOT_READY"))
        if circuit_state not in {"", "closed"}:
            reason_codes.append("BINANCE_REST_CIRCUIT_OPEN")

        unique_codes = list(dict.fromkeys(reason_codes))
        if not unique_codes:
            return None
        return {
            "active": True,
            "reason_codes": unique_codes,
            "market_stream": {
                "redis_configured": redis_configured,
                "redis_connected": redis_connected,
                "cache_health": cache_health or None,
                "cache_reject_reason": market_stream.get("cache_reject_reason"),
            },
            "reconciliation": {
                "status": reconciliation.get("status"),
                "unresolved_submission_badge": bool(reconciliation.get("unresolved_submission_badge")),
                "unresolved_submission_count": int(
                    _safe_float(reconciliation.get("unresolved_submission_count"), default=0.0)
                ),
            },
            "binance_rest": {
                "status": rest_status or None,
                "circuit_state": circuit_state or None,
                "reason_code": binance_rest.get("reason_code"),
            },
        }

    def _entry_candidate_active_pending_plan_context(self, *, symbol: str) -> dict[str, object] | None:
        plans = self._active_pending_entry_plans(symbol=symbol)
        if not plans:
            return None
        return {
            "active": True,
            "active_plan_count": len(plans),
            "plan_ids": [plan.id for plan in plans if plan.id is not None],
            "statuses": list(dict.fromkeys(str(plan.plan_status or "") for plan in plans)),
        }

    @staticmethod
    def _entry_candidate_incomplete_trade_plan_context(
        selection_context: dict[str, object],
    ) -> dict[str, object] | None:
        candidate = _as_dict(selection_context.get("candidate"))
        if not candidate:
            return None
        decision = str(candidate.get("decision") or "").lower()
        if decision not in {"long", "short"}:
            return None
        required_fields = ("entry_zone_min", "entry_zone_max", "stop_loss", "take_profit")
        missing_fields = [
            field
            for field in required_fields
            if candidate.get(field) in {None, ""}
        ]
        if not missing_fields:
            return None
        return {
            "active": True,
            "missing_fields": missing_fields,
            "decision": decision,
        }

    def _soft_signal_hold_backoff_status(
        self,
        *,
        symbol: str,
        timeframe: str | None,
        generated_at: datetime,
    ) -> dict[str, object]:
        symbol_upper = symbol.upper()
        if not symbol_upper:
            return {"active": False, "sample_count": 0}
        cutoff = generated_at - timedelta(minutes=SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_LOOKBACK_MINUTES)
        rows = list(
            self.session.scalars(
                select(AgentRun)
                .where(
                    AgentRun.role == AgentRole.TRADING_DECISION.value,
                    AgentRun.created_at >= cutoff,
                )
                .order_by(desc(AgentRun.created_at))
                .limit(SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_ROW_LIMIT)
            )
        )
        recent_rows: list[AgentRun] = []
        for row in rows:
            if not self._agent_run_is_provider_attempt(row):
                continue
            if not self._agent_run_matches_symbol(row, symbol=symbol_upper, timeframe=timeframe):
                continue
            if not self._agent_run_has_soft_signal_context(row):
                continue
            recent_rows.append(row)
            if len(recent_rows) >= SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_MIN_CALLS:
                break
        if len(recent_rows) < SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_MIN_CALLS:
            return {
                "active": False,
                "sample_count": len(recent_rows),
                "min_provider_calls": SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_MIN_CALLS,
                "lookback_minutes": SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_LOOKBACK_MINUTES,
            }

        run_ids = [int(row.id) for row in recent_rows if row.id is not None]
        risk_rows = list(
            self.session.scalars(select(RiskCheck).where(RiskCheck.decision_run_id.in_(run_ids)))
        )
        risk_by_run_id: dict[int, list[RiskCheck]] = defaultdict(list)
        for risk_row in risk_rows:
            if risk_row.decision_run_id is not None:
                risk_by_run_id[int(risk_row.decision_run_id)].append(risk_row)

        outcomes: list[dict[str, object]] = []
        for row in recent_rows:
            output = row.output_payload if isinstance(row.output_payload, dict) else {}
            decision_run_id = int(row.id) if row.id is not None else None
            risk_hold_blocked = any(
                self._risk_check_hold_blocked(risk_row)
                for risk_row in risk_by_run_id.get(decision_run_id, [])
                if decision_run_id is not None
            )
            outcomes.append(
                {
                    "decision_run_id": row.id,
                    "decision": str(output.get("decision") or "").lower() or None,
                    "risk_hold_blocked": risk_hold_blocked,
                }
            )

        active = all(bool(item["decision"] == "hold" and item["risk_hold_blocked"]) for item in outcomes)
        return {
            "active": active,
            "sample_count": len(recent_rows),
            "min_provider_calls": SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_MIN_CALLS,
            "lookback_minutes": SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_LOOKBACK_MINUTES,
            "decision_run_ids": run_ids,
            "outcomes": outcomes,
            "last_provider_call_at": recent_rows[0].created_at.isoformat() if recent_rows else None,
            "oldest_provider_call_at": recent_rows[-1].created_at.isoformat() if recent_rows else None,
        }

    def _soft_signal_review_policy(
        self,
        *,
        symbol: str,
        timeframe: str | None,
        selection_context: dict[str, object],
        soft_rejected_reason: str,
        last_ai_invoked_at: datetime | None,
        latest_decision_metadata: dict[str, object],
        generated_at: datetime,
        effective_settings: object,
    ) -> dict[str, object]:
        metrics = self._soft_signal_review_metrics(selection_context)
        current_snapshot = self._soft_signal_review_snapshot(selection_context)
        previous_snapshot = self._previous_soft_signal_review_snapshot(latest_decision_metadata)
        previous_soft_mode = str(latest_decision_metadata.get("soft_signal_review_mode") or "")
        threshold_gap = metrics.get("threshold_gap")
        transition_watch_candidate = (
            isinstance(threshold_gap, (int, float))
            and float(threshold_gap) <= SOFT_SIGNAL_TRANSITION_WATCH_MAX_SCORE_GAP
        )
        common_payload = {
            "scope": "new_entry",
            "hard_skip_ai": False,
            "hard_skip_reason_codes": [],
            "allow_ai_but_later_risk_check": [SOFT_SIGNAL_AI_REVIEW_REASON_CODE],
            "soft_rejected_reason": soft_rejected_reason,
            "soft_signal_review_metrics": metrics,
            "slot_conviction_score": metrics["slot_conviction_score"],
            "score_total": metrics["score_total"],
            "entry_score_threshold": metrics["entry_score_threshold"],
            "threshold_gap": metrics["threshold_gap"],
            "soft_signal_review_snapshot": current_snapshot,
        }
        if not transition_watch_candidate:
            return {
                "action": "suppress",
                "last_ai_skip_reason": SOFT_SIGNAL_AI_REVIEW_SUPPRESSED_SKIP_REASON,
                "ai_call_policy": {
                    "ai_call_event": AI_CALL_EVENT_SKIPPED,
                    "ai_call_allowed": False,
                    "skip_ai": True,
                    "reason": SOFT_SIGNAL_AI_REVIEW_SUPPRESSED_REASON,
                    "skip_category": "cost_saved_weak_candidate",
                    "soft_signal_review_mode": "cost_saved_weak_candidate",
                    **common_payload,
                },
            }

        material_change = None
        if previous_soft_mode == "transition_watch":
            material_change = self._soft_signal_review_material_change(
                current_snapshot=current_snapshot,
                previous_snapshot=previous_snapshot,
            )
        actionable_review_context = self._soft_signal_actionable_review_context(
            selection_context=selection_context,
            current_snapshot=current_snapshot,
            material_change=material_change,
        )

        low_edge_hold_suppression = self._soft_signal_low_edge_hold_suppression_context(
            selection_context=selection_context,
            current_snapshot=current_snapshot,
            soft_rejected_reason=soft_rejected_reason,
            material_change=material_change,
        )
        if bool(low_edge_hold_suppression.get("active")):
            return {
                "action": "low_edge_hold_suppressed",
                "last_ai_skip_reason": SOFT_SIGNAL_AI_REVIEW_SUPPRESSED_SKIP_REASON,
                "ai_call_policy": {
                    "ai_call_event": AI_CALL_EVENT_SKIPPED,
                    "ai_call_allowed": False,
                    "skip_ai": True,
                    "reason": SOFT_SIGNAL_AI_REVIEW_SUPPRESSED_REASON,
                    "skip_category": "cost_saved_low_edge_hold_candidate",
                    "soft_signal_review_mode": "cost_saved_low_edge_hold_candidate",
                    "low_edge_hold_suppression": low_edge_hold_suppression,
                    "soft_signal_review_material_change": material_change,
                    **common_payload,
                },
            }

        adaptive_backoff = self._soft_signal_hold_backoff_status(
            symbol=symbol,
            timeframe=timeframe,
            generated_at=generated_at,
        )
        adaptive_backoff_override = None
        if bool(adaptive_backoff.get("active")) and isinstance(material_change, dict):
            if (
                bool(material_change.get("material_changed"))
                and bool(material_change.get("previous_snapshot"))
                and bool(actionable_review_context.get("actionable_review"))
            ):
                adaptive_backoff_override = {
                    "active": True,
                    "reason": "material_actionable_change",
                    "adaptive_hold_backoff": adaptive_backoff,
                    "actionable_context": actionable_review_context,
                }
        if bool(adaptive_backoff.get("active")) and adaptive_backoff_override is None:
            return {
                "action": "hold_backoff",
                "last_ai_skip_reason": SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_SKIP_REASON,
                "ai_call_policy": {
                    "ai_call_event": AI_CALL_EVENT_SKIPPED,
                    "ai_call_allowed": False,
                    "skip_ai": True,
                    "reason": SOFT_SIGNAL_ADAPTIVE_HOLD_BACKOFF_REASON,
                    "skip_category": "transition_watch_hold_backoff",
                    "soft_signal_review_mode": "transition_watch_hold_backoff",
                    "adaptive_hold_backoff": adaptive_backoff,
                    **common_payload,
                },
            }

        soft_cooldown_minutes = self._soft_signal_ai_review_cooldown_minutes(effective_settings)
        if last_ai_invoked_at is not None:
            retry_at = last_ai_invoked_at + timedelta(minutes=soft_cooldown_minutes)
            if retry_at > generated_at:
                retry_after_seconds = max(int((retry_at - generated_at).total_seconds()), 1)
                return {
                    "action": "cooldown",
                    "last_ai_skip_reason": SOFT_SIGNAL_TRANSITION_WATCH_COOLDOWN_SKIP_REASON,
                    "ai_call_policy": {
                        "ai_call_event": AI_CALL_EVENT_SKIPPED,
                        "ai_call_allowed": False,
                        "skip_ai": True,
                        "reason": "soft_signal_review_cooldown_active",
                        "skip_category": "transition_watch_cooldown",
                        "retry_after_seconds": retry_after_seconds,
                        "cooldown_minutes": soft_cooldown_minutes,
                        "last_ai_invoked_at": last_ai_invoked_at.isoformat(),
                        "soft_signal_review_mode": "transition_watch_cooldown",
                        **common_payload,
                    },
                }

        if previous_soft_mode == "transition_watch":
            if not bool(material_change.get("material_changed", True)):
                return {
                    "action": "no_material_change",
                    "last_ai_skip_reason": (
                        SOFT_SIGNAL_TRANSITION_WATCH_NO_MATERIAL_CHANGE_SKIP_REASON
                    ),
                    "ai_call_policy": {
                        "ai_call_event": AI_CALL_EVENT_SKIPPED,
                        "ai_call_allowed": False,
                        "skip_ai": True,
                        "reason": SOFT_SIGNAL_TRANSITION_WATCH_NO_MATERIAL_CHANGE_REASON,
                        "skip_category": "transition_watch_no_material_change",
                        "last_ai_invoked_at": (
                            last_ai_invoked_at.isoformat()
                            if last_ai_invoked_at is not None
                            else None
                        ),
                        "soft_signal_review_mode": "transition_watch_no_material_change",
                        "soft_signal_review_material_change": material_change,
                        **common_payload,
                    },
                }

        return {
            "action": "allow_transition_watch",
            "last_ai_skip_reason": None,
            "ai_call_policy": {
                "ai_call_event": AI_CALL_EVENT_ALLOWED,
                "ai_call_allowed": True,
                "skip_ai": False,
                "reason": None,
                "skip_category": None,
                "soft_signal_review_mode": "transition_watch",
                "transition_watch_ai_invoked": True,
                "cooldown_minutes": soft_cooldown_minutes,
                "soft_signal_review_material_change": (
                    material_change
                    or {
                        "material_changed": True,
                        "reason": "no_previous_transition_watch",
                        "current_snapshot": current_snapshot,
                        "previous_snapshot": previous_snapshot,
                    }
                ),
                "adaptive_hold_backoff_override": adaptive_backoff_override,
                **{
                    **common_payload,
                    "allow_ai_but_later_risk_check": [
                        SOFT_SIGNAL_AI_REVIEW_REASON_CODE,
                        SOFT_SIGNAL_TRANSITION_WATCH_REASON_CODE,
                    ],
                },
            },
        }

    def _neutral_entry_hold_backoff_policy(
        self,
        *,
        symbol: str,
        timeframe: str | None,
        selection_context: dict[str, object],
        generated_at: datetime,
    ) -> dict[str, object]:
        backoff = self._neutral_entry_hold_backoff_status(
            symbol=symbol,
            timeframe=timeframe,
            selection_context=selection_context,
            generated_at=generated_at,
        )
        if not bool(backoff.get("active")):
            if backoff.get("reason") == "context_not_neutral_entry":
                return {
                    "action": "allow",
                    "last_ai_skip_reason": None,
                    "neutral_entry_hold_backoff": backoff,
                }
            neutral_context = {
                **backoff,
                "active": True,
                "reason": ENTRY_CANDIDATE_NEUTRAL_CONTEXT_PREAI_REASON,
                "required_reason_codes": sorted(ENTRY_CANDIDATE_NEUTRAL_CONTEXT_REQUIRED_REASONS),
            }
            return {
                "action": "neutral_context_preai_skip",
                "last_ai_skip_reason": ENTRY_CANDIDATE_NEUTRAL_CONTEXT_PREAI_SKIP_REASON,
                "ai_call_policy": {
                    "ai_call_event": AI_CALL_EVENT_SKIPPED,
                    "ai_call_allowed": False,
                    "skip_ai": True,
                    "reason": ENTRY_CANDIDATE_NEUTRAL_CONTEXT_PREAI_REASON,
                    "pre_ai_skip_reason": ENTRY_CANDIDATE_NEUTRAL_CONTEXT_PREAI_SKIP_REASON,
                    "skip_category": "neutral_entry_context_preai",
                    "scope": "new_entry",
                    "hard_skip_ai": False,
                    "hard_skip_reason_codes": [],
                    "allow_ai_but_later_risk_check": [],
                    "neutral_entry_context": neutral_context,
                },
            }
        return {
            "action": "hold_backoff",
            "last_ai_skip_reason": ENTRY_CANDIDATE_NEUTRAL_CONTEXT_HOLD_BACKOFF_SKIP_REASON,
            "ai_call_policy": {
                "ai_call_event": AI_CALL_EVENT_SKIPPED,
                "ai_call_allowed": False,
                "skip_ai": True,
                "reason": ENTRY_CANDIDATE_NEUTRAL_CONTEXT_HOLD_BACKOFF_REASON,
                "pre_ai_skip_reason": ENTRY_CANDIDATE_NEUTRAL_CONTEXT_HOLD_BACKOFF_SKIP_REASON,
                "skip_category": "neutral_entry_hold_backoff",
                "scope": "new_entry",
                "hard_skip_ai": False,
                "hard_skip_reason_codes": [],
                "allow_ai_but_later_risk_check": [],
                "neutral_entry_hold_backoff": backoff,
            },
        }

    def _entry_candidate_pre_ai_eligibility_policy(
        self,
        *,
        symbol: str,
        timeframe: str | None,
        selection_context: dict[str, object],
        generated_at: datetime,
        runtime_state: dict[str, object],
        allow_ai_but_later_risk_check: list[str] | None = None,
    ) -> dict[str, object]:
        policy_reason_codes = {str(code) for code in allow_ai_but_later_risk_check or [] if code}
        if SOFT_SIGNAL_AI_REVIEW_REASON_CODE in policy_reason_codes:
            ai_hold_cooldown = self._entry_candidate_ai_hold_fingerprint_cooldown_status(
                symbol=symbol,
                timeframe=timeframe,
                selection_context=selection_context,
                generated_at=generated_at,
                allow_ai_but_later_risk_check=allow_ai_but_later_risk_check,
            )
            return {
                "action": "allow",
                "last_ai_skip_reason": None,
                "reason": "soft_signal_review_policy_owner",
                "entry_candidate_ai_hold_fingerprint": ai_hold_cooldown.get("signature"),
            }

        active_pending_plan = self._entry_candidate_active_pending_plan_context(symbol=symbol)
        if active_pending_plan is not None:
            return {
                "action": "active_pending_plan",
                "last_ai_skip_reason": ENTRY_CANDIDATE_ACTIVE_PENDING_PLAN_SKIP_REASON,
                "ai_call_policy": {
                    "ai_call_event": AI_CALL_EVENT_SKIPPED,
                    "ai_call_allowed": False,
                    "skip_ai": True,
                    "reason": ENTRY_CANDIDATE_ACTIVE_PENDING_PLAN_REASON,
                    "pre_ai_skip_reason": ENTRY_CANDIDATE_ACTIVE_PENDING_PLAN_SKIP_REASON,
                    "skip_category": "entry_candidate_active_pending_plan",
                    "scope": "new_entry",
                    "hard_skip_ai": False,
                    "hard_skip_reason_codes": [],
                    "allow_ai_but_later_risk_check": [],
                    "active_pending_plan": active_pending_plan,
                },
            }

        neutral_entry_policy = self._neutral_entry_hold_backoff_policy(
            symbol=symbol,
            timeframe=timeframe,
            selection_context=selection_context,
            generated_at=generated_at,
        )
        if neutral_entry_policy.get("action") != "allow":
            return neutral_entry_policy

        weak_volume_context = self._entry_candidate_weak_volume_context(
            selection_context,
            allow_ai_but_later_risk_check=allow_ai_but_later_risk_check,
        )
        if bool(weak_volume_context.get("active")):
            return {
                "action": "weak_volume_preai_skip",
                "last_ai_skip_reason": ENTRY_CANDIDATE_WEAK_VOLUME_PREAI_REASON,
                "ai_call_policy": {
                    "ai_call_event": AI_CALL_EVENT_SKIPPED,
                    "ai_call_allowed": False,
                    "skip_ai": True,
                    "reason": ENTRY_CANDIDATE_WEAK_VOLUME_PREAI_POLICY_REASON,
                    "pre_ai_skip_reason": ENTRY_CANDIDATE_WEAK_VOLUME_PREAI_REASON,
                    "skip_category": "entry_candidate_weak_volume_preai",
                    "scope": "new_entry",
                    "hard_skip_ai": False,
                    "hard_skip_reason_codes": [],
                    "allow_ai_but_later_risk_check": list(
                        dict.fromkeys(allow_ai_but_later_risk_check or [])
                    ),
                    "weak_volume_context": weak_volume_context,
                },
            }

        low_actionability_backoff = self._entry_candidate_low_actionability_hold_backoff_status(
            symbol=symbol,
            timeframe=timeframe,
            selection_context=selection_context,
            generated_at=generated_at,
            allow_ai_but_later_risk_check=allow_ai_but_later_risk_check,
        )
        if bool(low_actionability_backoff.get("active")):
            return {
                "action": "low_actionability_hold_backoff",
                "last_ai_skip_reason": ENTRY_CANDIDATE_LOW_ACTIONABILITY_HOLD_BACKOFF_SKIP_REASON,
                "ai_call_policy": {
                    "ai_call_event": AI_CALL_EVENT_SKIPPED,
                    "ai_call_allowed": False,
                    "skip_ai": True,
                    "reason": ENTRY_CANDIDATE_LOW_ACTIONABILITY_HOLD_BACKOFF_REASON,
                    "pre_ai_skip_reason": ENTRY_CANDIDATE_LOW_ACTIONABILITY_HOLD_BACKOFF_SKIP_REASON,
                    "skip_category": "entry_candidate_low_actionability_hold_backoff",
                    "scope": "new_entry",
                    "hard_skip_ai": False,
                    "hard_skip_reason_codes": [],
                    "allow_ai_but_later_risk_check": list(
                        dict.fromkeys(allow_ai_but_later_risk_check or [])
                    ),
                    "low_actionability_hold_backoff": low_actionability_backoff,
                },
            }

        incomplete_trade_plan = self._entry_candidate_incomplete_trade_plan_context(selection_context)
        if incomplete_trade_plan is not None:
            return {
                "action": "incomplete_trade_plan",
                "last_ai_skip_reason": ENTRY_CANDIDATE_INCOMPLETE_TRADE_PLAN_SKIP_REASON,
                "ai_call_policy": {
                    "ai_call_event": AI_CALL_EVENT_SKIPPED,
                    "ai_call_allowed": False,
                    "skip_ai": True,
                    "reason": ENTRY_CANDIDATE_INCOMPLETE_TRADE_PLAN_REASON,
                    "pre_ai_skip_reason": ENTRY_CANDIDATE_INCOMPLETE_TRADE_PLAN_SKIP_REASON,
                    "skip_category": "entry_candidate_incomplete_trade_plan",
                    "scope": "new_entry",
                    "hard_skip_ai": False,
                    "hard_skip_reason_codes": [],
                    "allow_ai_but_later_risk_check": [],
                    "incomplete_trade_plan": incomplete_trade_plan,
                },
            }

        ai_hold_cooldown = self._entry_candidate_ai_hold_fingerprint_cooldown_status(
            symbol=symbol,
            timeframe=timeframe,
            selection_context=selection_context,
            generated_at=generated_at,
            allow_ai_but_later_risk_check=allow_ai_but_later_risk_check,
        )
        if bool(ai_hold_cooldown.get("active")):
            return {
                "action": "ai_hold_fingerprint_cooldown",
                "last_ai_skip_reason": ENTRY_CANDIDATE_AI_HOLD_FINGERPRINT_COOLDOWN_SKIP_REASON,
                "entry_candidate_ai_hold_fingerprint": ai_hold_cooldown.get("signature"),
                "ai_call_policy": {
                    "ai_call_event": AI_CALL_EVENT_SKIPPED,
                    "ai_call_allowed": False,
                    "skip_ai": True,
                    "reason": ENTRY_CANDIDATE_AI_HOLD_FINGERPRINT_COOLDOWN_REASON,
                    "pre_ai_skip_reason": ENTRY_CANDIDATE_AI_HOLD_FINGERPRINT_COOLDOWN_SKIP_REASON,
                    "skip_category": "entry_candidate_ai_hold_fingerprint_cooldown",
                    "scope": "new_entry",
                    "hard_skip_ai": False,
                    "hard_skip_reason_codes": [],
                    "allow_ai_but_later_risk_check": [],
                    "entry_candidate_ai_hold_cooldown": ai_hold_cooldown,
                    "entry_candidate_ai_hold_fingerprint": ai_hold_cooldown.get("signature"),
                },
            }

        order_path_block = self._entry_candidate_order_path_block_context(runtime_state)
        if order_path_block is not None:
            return {
                "action": "order_path_not_actionable",
                "last_ai_skip_reason": ENTRY_CANDIDATE_ORDER_PATH_NOT_ACTIONABLE_SKIP_REASON,
                "ai_call_policy": {
                    "ai_call_event": AI_CALL_EVENT_SKIPPED,
                    "ai_call_allowed": False,
                    "skip_ai": True,
                    "reason": ENTRY_CANDIDATE_ORDER_PATH_NOT_ACTIONABLE_REASON,
                    "pre_ai_skip_reason": ENTRY_CANDIDATE_ORDER_PATH_NOT_ACTIONABLE_SKIP_REASON,
                    "skip_category": "entry_candidate_order_path_not_actionable",
                    "scope": "new_entry",
                    "hard_skip_ai": True,
                    "hard_skip_reason_codes": list(order_path_block["reason_codes"]),
                    "allow_ai_but_later_risk_check": [],
                    "order_path_block": order_path_block,
                },
            }
        return {
            "action": "allow",
            "last_ai_skip_reason": None,
            "low_actionability_hold_backoff": low_actionability_backoff,
            "entry_candidate_ai_hold_fingerprint": ai_hold_cooldown.get("signature"),
        }

    @staticmethod
    def _account_untrusted_sync_reason_codes(sync_freshness_summary: dict[str, object]) -> list[str]:
        reason_codes: list[str] = []
        for scope in ("account", "positions", "open_orders", "protective_orders"):
            scope_payload = _as_dict(sync_freshness_summary.get(scope))
            if not scope_payload:
                reason_codes.append(f"{scope}_sync_missing")
                continue
            if not sync_scope_blocks_new_entry(sync_freshness_summary, scope):
                continue
            if bool(scope_payload.get("stale")):
                reason_codes.append(f"{scope}_sync_stale")
            if bool(scope_payload.get("incomplete")):
                reason_codes.append(f"{scope}_sync_incomplete")
        return reason_codes

    def _risk_adding_exchange_permission_pre_ai_block(
        self,
        *,
        intent_type: str,
    ) -> dict[str, object] | None:
        return exchange_trade_permission_entry_blocker(
            get_sync_state_detail(self.settings_row).get("account", {}),
            rollout_mode=get_rollout_mode(self.settings_row),
            live_execution_armed=is_live_execution_armed(self.settings_row),
            intent_type=intent_type,
        )

    @staticmethod
    def _ai_review_scope(
        *,
        review_trigger_payload: AIReviewTriggerPayload | None,
        open_positions: list[Position],
        risk_adding_add_on: bool = False,
    ) -> str:
        if review_trigger_payload is not None and review_trigger_payload.trigger_reason == "protection_review_event":
            return "protective_recovery"
        if risk_adding_add_on:
            return "risk_adding_add_on"
        if open_positions:
            return "position_management"
        return "new_entry"

    def _new_entry_pre_ai_hard_block_reason(self, *, intent_type: str = "entry") -> str | None:
        latest_pnl = get_latest_pnl_snapshot(self.session, self.settings_row)
        daily_equity = max(_safe_float(getattr(latest_pnl, "equity", None), default=0.0), 1.0)
        daily_loss_cap = min(
            _safe_float(getattr(self.settings_row, "max_daily_loss", None), default=HARD_MAX_DAILY_LOSS),
            HARD_MAX_DAILY_LOSS,
        )
        if (
            _safe_float(getattr(latest_pnl, "daily_pnl", None), default=0.0) < 0.0
            and abs(_safe_float(getattr(latest_pnl, "daily_pnl", None), default=0.0)) / daily_equity
            >= daily_loss_cap
        ):
            return "DAILY_LOSS_LIMIT_REACHED"
        max_consecutive_losses = int(getattr(self.settings_row, "max_consecutive_losses", 0) or 0)
        if (
            max_consecutive_losses > 0
            and int(getattr(latest_pnl, "consecutive_losses", 0) or 0) >= max_consecutive_losses
        ):
            return "MAX_CONSECUTIVE_LOSSES_REACHED"
        if rollout_mode_allows_exchange_submit(self.settings_row):
            defaults = get_settings()
            if not defaults.live_trading_env_enabled:
                return "LIVE_ENV_DISABLED"
            if not bool(getattr(self.settings_row, "manual_live_approval", False)):
                return "LIVE_APPROVAL_POLICY_DISABLED"
            if not is_live_execution_armed(self.settings_row):
                return "LIVE_APPROVAL_REQUIRED"
        exchange_permission_entry_block = self._risk_adding_exchange_permission_pre_ai_block(
            intent_type=intent_type
        )
        if exchange_permission_entry_block is not None:
            return str(exchange_permission_entry_block["reason_code"])
        return None

    def _risk_adding_add_on_pre_ai_hard_block_reason(
        self,
        *,
        active_position_prompt_route_context: dict[str, object] | None,
        effective_settings: object,
        runtime_state: dict[str, object],
        openai_gate: object,
    ) -> str | None:
        route_context = _as_dict(active_position_prompt_route_context)
        if not bool(
            route_context.get("risk_adding_add_on_candidate")
            or route_context.get("allow_same_side_add_on")
        ):
            return None
        if not bool(getattr(effective_settings, "enabled", True)):
            return "symbol_disabled"
        operating_state = str(runtime_state.get("operating_state") or "")
        if operating_state in ENTRY_BLOCKING_OPERATING_STATES:
            return f"new_entry_blocked_{operating_state.lower()}"
        if pre_ai_hard_block_reason := self._new_entry_pre_ai_hard_block_reason(intent_type="scale_in"):
            return pre_ai_hard_block_reason
        openai_gate_reason = str(getattr(openai_gate, "reason", "") or "").upper()
        if (
            not bool(getattr(openai_gate, "allowed", False))
            and openai_gate_reason == "LOW_ACTIONABILITY_COST_GUARD_ACTIVE"
        ):
            return openai_gate_reason
        return None

    def _ai_call_policy(
        self,
        *,
        review_trigger_payload: AIReviewTriggerPayload | None,
        market_snapshot: MarketSnapshotPayload,
        effective_settings: object,
        runtime_state: dict[str, object],
        ai_context: object,
        openai_gate: object,
        cadence_profile: dict[str, object],
        open_positions: list[Position],
        allow_ai_but_later_risk_check: list[str],
        selection_context: dict[str, object] | None = None,
        generated_at: datetime | None = None,
        active_position_prompt_route_context: dict[str, object] | None = None,
    ) -> dict[str, object]:
        route_context = _as_dict(active_position_prompt_route_context)
        risk_adding_add_on = bool(
            route_context.get("risk_adding_add_on_candidate")
            or route_context.get("allow_same_side_add_on")
        )
        risk_adding_add_on_pre_ai_block_reason = (
            str(route_context.get("risk_adding_add_on_pre_ai_block_reason") or "") or None
        )
        risk_adding_add_on_pre_ai_reason_codes = list(
            route_context.get("risk_adding_add_on_pre_ai_reason_codes") or []
        )
        scope = self._ai_review_scope(
            review_trigger_payload=review_trigger_payload,
            open_positions=open_positions,
            risk_adding_add_on=risk_adding_add_on,
        )
        entry_like_scope = scope in {"new_entry", "risk_adding_add_on"}
        operating_state = str(runtime_state.get("operating_state") or "")
        data_quality = getattr(ai_context, "data_quality", None)
        account_state_trustworthy = bool(getattr(data_quality, "account_state_trustworthy", True))
        data_quality_reason_codes: list[str] = []
        if data_quality is not None:
            data_quality_reason_codes = self._unique_reason_codes(
                [
                    *list(getattr(data_quality, "missing_context_flags", []) or []),
                    *list(getattr(data_quality, "stale_context_flags", []) or []),
                    "account_state_untrustworthy" if not account_state_trustworthy else "",
                    (
                        "market_state_untrustworthy"
                        if not bool(getattr(data_quality, "market_state_trustworthy", True))
                        else ""
                    ),
                ]
            )
        hard_skip_ai = False
        skip_category: str | None = None
        reason: str | None = None
        pre_ai_skip_reason: str | None = None
        hard_skip_reason_codes: list[str] = []
        extra_policy: dict[str, object] = {}

        if review_trigger_payload is not None and review_trigger_payload.trigger_reason == "protection_review_event":
            reason = "PROTECTION_REVIEW_DETERMINISTIC_ONLY"
            hard_skip_ai = True
            skip_category = "protective_recovery_deterministic"
        elif operating_state == PAUSED_STATE:
            reason = "pause"
            hard_skip_ai = True
            skip_category = "hard_skip_ai"
        elif operating_state == EMERGENCY_EXIT_STATE:
            reason = "emergency"
            hard_skip_ai = True
            skip_category = "hard_skip_ai"
        elif bool(getattr(market_snapshot, "is_stale", False)):
            reason = "stale_market_data"
            hard_skip_ai = True
            skip_category = "hard_skip_ai"
        elif not bool(getattr(market_snapshot, "is_complete", True)):
            reason = "incomplete_market_data"
            hard_skip_ai = True
            skip_category = "hard_skip_ai"
        elif not account_state_trustworthy:
            reason = "account_untrusted"
            hard_skip_ai = True
            skip_category = "hard_skip_ai"
        elif risk_adding_add_on_pre_ai_block_reason is not None:
            reason = risk_adding_add_on_pre_ai_block_reason
            hard_skip_ai = True
            skip_category = (
                "cost_governance"
                if reason == "LOW_ACTIONABILITY_COST_GUARD_ACTIVE"
                else "hard_skip_ai"
            )
        elif entry_like_scope and not bool(getattr(effective_settings, "enabled", True)):
            reason = "symbol_disabled"
            hard_skip_ai = True
            skip_category = "hard_skip_ai"
        elif entry_like_scope and operating_state in ENTRY_BLOCKING_OPERATING_STATES:
            reason = f"new_entry_blocked_{operating_state.lower()}"
            hard_skip_ai = True
            skip_category = "hard_skip_ai"
        elif entry_like_scope and (pre_ai_hard_block_reason := self._new_entry_pre_ai_hard_block_reason()):
            reason = pre_ai_hard_block_reason
            hard_skip_ai = True
            skip_category = "hard_skip_ai"
        elif (
            scope == "new_entry"
            and review_trigger_payload is not None
            and review_trigger_payload.trigger_reason == "entry_candidate_event"
            and isinstance(selection_context, dict)
        ):
            entry_pre_ai_policy = self._entry_candidate_pre_ai_eligibility_policy(
                symbol=review_trigger_payload.symbol,
                timeframe=review_trigger_payload.timeframe,
                selection_context=selection_context,
                generated_at=generated_at or utcnow_naive(),
                runtime_state=runtime_state,
                allow_ai_but_later_risk_check=allow_ai_but_later_risk_check,
            )
            entry_candidate_ai_hold_fingerprint = _as_dict(
                entry_pre_ai_policy.get("entry_candidate_ai_hold_fingerprint")
            )
            if entry_candidate_ai_hold_fingerprint:
                extra_policy["entry_candidate_ai_hold_fingerprint"] = entry_candidate_ai_hold_fingerprint
            if entry_pre_ai_policy.get("action") != "allow":
                entry_ai_policy = _as_dict(entry_pre_ai_policy.get("ai_call_policy"))
                reason = str(entry_ai_policy.get("reason") or "") or None
                pre_ai_skip_reason = str(
                    entry_ai_policy.get("pre_ai_skip_reason")
                    or entry_pre_ai_policy.get("last_ai_skip_reason")
                    or ""
                ) or None
                hard_skip_ai = bool(entry_ai_policy.get("hard_skip_ai", False))
                skip_category = str(entry_ai_policy.get("skip_category") or "") or None
                hard_skip_reason_codes = list(entry_ai_policy.get("hard_skip_reason_codes") or [])
                extra_policy = {
                    key: value
                    for key, value in entry_ai_policy.items()
                    if key
                    not in {
                        "ai_call_event",
                        "ai_call_allowed",
                        "skip_ai",
                        "reason",
                        "pre_ai_skip_reason",
                        "scope",
                        "hard_skip_ai",
                        "skip_category",
                        "hard_skip_reason_codes",
                        "allow_ai_but_later_risk_check",
                    }
                }
        elif str(cadence_profile.get("ai_skipped_reason") or ""):
            reason = str(cadence_profile.get("ai_skipped_reason") or "")
            skip_category = "cadence_policy"
        elif not bool(getattr(openai_gate, "allowed", False)):
            reason = str(getattr(openai_gate, "reason", "") or "openai_gate_blocked").upper()
            if reason == "LOW_ACTIONABILITY_COST_GUARD_ACTIVE" and entry_like_scope:
                hard_skip_ai = True
                skip_category = "cost_governance"
            else:
                skip_category = "budget_cooldown_dedup"

        if reason is not None:
            hard_skip_reason_codes.append(reason)
            if hard_skip_ai:
                hard_skip_reason_codes = self._unique_reason_codes(
                    [*hard_skip_reason_codes, *data_quality_reason_codes]
                )
        if risk_adding_add_on_pre_ai_reason_codes:
            hard_skip_reason_codes = self._unique_reason_codes(
                [*hard_skip_reason_codes, *risk_adding_add_on_pre_ai_reason_codes]
            )
        return {
            "ai_call_event": AI_CALL_EVENT_SKIPPED if reason is not None else AI_CALL_EVENT_ALLOWED,
            "ai_call_allowed": reason is None,
            "skip_ai": reason is not None,
            "reason": reason,
            "pre_ai_skip_reason": pre_ai_skip_reason,
            "scope": scope,
            "hard_skip_ai": hard_skip_ai,
            "skip_category": skip_category,
            "hard_skip_reason_codes": hard_skip_reason_codes,
            "allow_ai_but_later_risk_check": list(dict.fromkeys(allow_ai_but_later_risk_check)),
            "risk_adding_add_on_candidate": risk_adding_add_on,
            "risk_adding_add_on_pre_ai_blocked": bool(risk_adding_add_on_pre_ai_block_reason),
            "risk_adding_add_on_pre_ai_block_reason": risk_adding_add_on_pre_ai_block_reason,
            "risk_adding_add_on_pre_ai_reason_codes": risk_adding_add_on_pre_ai_reason_codes,
            "soft_signal_review_mode": (
                "transition_watch"
                if SOFT_SIGNAL_TRANSITION_WATCH_REASON_CODE in set(allow_ai_but_later_risk_check)
                else None
            ),
            **extra_policy,
        }

    def build_interval_decision_plan(
        self,
        *,
        symbols: list[str],
        candidate_universe_symbols: list[str] | None = None,
        timeframe: str | None = None,
        upto_index: int | None = None,
        force_stale: bool = False,
        triggered_at: datetime | None = None,
    ) -> dict[str, object]:
        generated_at = triggered_at or utcnow_naive()
        tracked_symbols = [item.upper() for item in symbols if item]
        selection_symbols = [
            item.upper()
            for item in (candidate_universe_symbols if candidate_universe_symbols is not None else symbols)
            if item
        ]
        runtime_state = summarize_runtime_state(self.settings_row)
        missing_protection_symbols = {
            str(item).upper()
            for item in runtime_state.get("missing_protection_symbols", [])
            if item
        }
        effective_symbols = [item for item in get_effective_symbol_schedule(self.settings_row) if item.enabled]
        effective_lookup = {item.symbol: item for item in effective_symbols if item.symbol in tracked_symbols}
        selection_effective_lookup = {
            item.symbol: item for item in effective_symbols if item.symbol in selection_symbols
        }
        if tracked_symbols and not effective_lookup:
            no_candidate_cycle_id = f"decision-plan:all:{uuid4().hex[:8]}"
            record_audit_event(
                self.session,
                event_type="decision_ai_skipped",
                entity_type="decision_plan",
                entity_id="all",
                severity="info",
                message="AI inference was skipped because no eligible symbol is tradable.",
                payload={
                    "ai_call_event": AI_CALL_EVENT_SKIPPED,
                    "reason": "no_eligible_symbol",
                    "hard_skip_ai": True,
                    "skip_category": "hard_skip_ai",
                    "scope": "symbol_selection",
                    "symbols": tracked_symbols,
                },
            )
            self._record_decision_funnel_audit(
                cycle_id=no_candidate_cycle_id,
                symbol="all",
                timeframe=timeframe,
                stage="candidate_selection",
                status="no_candidate",
                entry_plan_status="not_created",
                m1_confirmation_status="not_checked",
                final_risk_status="not_checked",
                blocked_reason_codes=["no_eligible_symbol"],
                detail={"symbols": tracked_symbols},
                severity="info",
                entity_type="decision_plan",
                entity_id="all",
            )
        open_position_symbols = list(dict.fromkeys([*tracked_symbols, *selection_symbols]))
        open_positions_by_symbol = {
            symbol: list(get_open_positions(self.session, symbol))
            for symbol in open_position_symbols
            if symbol in effective_lookup or symbol in selection_effective_lookup
        }
        selection_flat_symbols = [
            symbol
            for symbol in selection_symbols
            if symbol in selection_effective_lookup and not open_positions_by_symbol.get(symbol)
        ]
        candidate_selection = (
            self._rank_candidate_symbols(
                decision_symbols=selection_flat_symbols,
                timeframe=timeframe,
                upto_index=upto_index,
                force_stale=force_stale,
            )
            if selection_flat_symbols
            else {
                "mode": "no_flat_symbols",
                "breadth_summary": {},
                "breadth_regime": "mixed",
                "selected_symbols": [],
                "skipped_symbols": [],
                "rankings": [],
            }
        )
        ranking_lookup = self._selection_ranking_lookup(candidate_selection)
        soft_ai_review_symbol = self._soft_ai_review_symbol(candidate_selection)
        sync_freshness_summary = build_sync_freshness_summary(
            self.settings_row,
            now=generated_at,
        )
        account_untrusted_reason_codes = self._account_untrusted_sync_reason_codes(sync_freshness_summary)
        plans: list[dict[str, object]] = []
        for symbol in tracked_symbols:
            effective = effective_lookup.get(symbol)
            if effective is None:
                continue
            effective_timeframe = timeframe or effective.timeframe
            open_positions = list(open_positions_by_symbol.get(symbol) or [])
            latest_decision_run = self._latest_symbol_decision_run(
                symbol=symbol,
                timeframe=effective_timeframe,
            )
            last_decision_at = latest_decision_run.created_at if latest_decision_run is not None else None
            last_ai_invoked_at = self._latest_symbol_ai_invoked_at(
                symbol=symbol,
                timeframe=effective_timeframe,
            )
            cadence_profile = self.get_symbol_cadence_profile(
                symbol=symbol,
                timeframe=effective_timeframe,
                runtime_state=runtime_state,
                open_positions=open_positions,
            )
            schedule_details = self.resolve_interval_decision_schedule_details(
                symbol=symbol,
                timeframe=effective_timeframe,
                effective_settings=effective,
                cadence_profile=cadence_profile,
                open_positions=open_positions,
                latest_decision_run=latest_decision_run,
            )
            review_interval_minutes = int(
                schedule_details.get("applied_review_cadence_minutes")
                or self._position_review_interval_minutes(
                    cadence_profile,
                    effective_settings=effective,
                )
            )
            next_ai_review_due_at = None
            trigger_payload: AIReviewTriggerPayload | None = None
            selection_context: dict[str, object] | None = None
            trigger_deduped = False
            last_ai_skip_reason: str | None = None
            plan_ai_call_policy: dict[str, object] | None = None
            dedupe_reason: str | None = None
            latest_metadata = (
                latest_decision_run.metadata_json
                if latest_decision_run is not None and isinstance(latest_decision_run.metadata_json, dict)
                else {}
            )
            last_material_review_at = (
                _coerce_datetime(latest_metadata.get("last_material_review_at"))
                or last_ai_invoked_at
                or last_decision_at
            )
            latest_trigger_payload = _as_dict(latest_metadata.get("ai_trigger"))
            latest_output_payload = (
                latest_decision_run.output_payload
                if latest_decision_run is not None and isinstance(latest_decision_run.output_payload, dict)
                else {}
            )
            active_position_suppression_payload = self._interval_plan_active_position_suppression_payload(
                symbol=symbol,
                timeframe=effective_timeframe,
                open_positions=open_positions,
                latest_decision_run=latest_decision_run,
                latest_decision_metadata=latest_metadata,
                latest_decision_output=latest_output_payload,
                runtime_state=runtime_state,
                sync_freshness_summary=sync_freshness_summary,
                upto_index=upto_index,
                force_stale=force_stale,
            )
            max_review_age_minutes = (
                self._open_position_max_review_age_minutes(
                    review_interval_minutes,
                    backstop_interval_minutes=(
                        effective.ai_backstop_interval_minutes
                        if effective.ai_backstop_enabled
                        else None
                    ),
                )
                if open_positions
                else None
            )

            if open_positions:
                position_row = open_positions[0]
                position_metadata = _as_dict(getattr(position_row, "metadata_json", {}))
                position_management = _as_dict(position_metadata.get("position_management"))
                trigger_reason: str | None = None
                reason_codes: list[str] = []
                if symbol in missing_protection_symbols:
                    trigger_reason = "protection_review_event"
                    reason_codes.append("MISSING_PROTECTIVE_ORDERS")
                elif str(runtime_state.get("operating_state") or "") == PROTECTION_REQUIRED_STATE:
                    trigger_reason = "protection_review_event"
                    reason_codes.append(PROTECTION_REQUIRED_STATE)

                if trigger_reason is not None:
                    strategy_engine_name = _strategy_engine_name_from_payload(
                        latest_metadata,
                        latest_output_payload,
                    )
                    slot_allocation = _as_dict(latest_metadata.get("slot_allocation"))
                    holding_profile = str(schedule_details.get("holding_profile") or "scalp")
                    fingerprint_basis = self._build_open_position_recheck_fingerprint_basis(
                        symbol=symbol,
                        timeframe=effective_timeframe,
                        position_row=position_row,
                        position_management=position_management,
                        latest_metadata=latest_metadata,
                        latest_output_payload=latest_output_payload,
                        strategy_engine_name=strategy_engine_name,
                        holding_profile=holding_profile,
                        runtime_state=runtime_state,
                        missing_protection_symbols=missing_protection_symbols,
                        sync_freshness_summary=sync_freshness_summary,
                    )
                    previous_fingerprint_basis = self._previous_open_position_fingerprint_basis(
                        latest_trigger_payload=latest_trigger_payload,
                        latest_metadata=latest_metadata,
                        latest_output_payload=latest_output_payload,
                    )
                    fingerprint_changed_fields = self._fingerprint_changed_fields(
                        fingerprint_basis,
                        previous_fingerprint_basis,
                    )
                    fingerprint_reason_codes = list(reason_codes)
                    forced_review_reason: str | None = None
                    trigger_payload = self._build_review_trigger_payload(
                        trigger_reason=trigger_reason,
                        symbol=symbol,
                        timeframe=effective_timeframe,
                        strategy_engine=strategy_engine_name or None,
                        holding_profile=holding_profile,
                        assigned_slot=str(slot_allocation.get("assigned_slot") or "") or None,
                        candidate_weight=slot_allocation.get("candidate_weight"),
                        reason_codes=reason_codes,
                        last_decision_at=last_decision_at,
                        last_material_review_at=last_material_review_at,
                        triggered_at=generated_at,
                        fingerprint_basis=fingerprint_basis,
                        fingerprint_changed_fields=fingerprint_changed_fields,
                        forced_review_reason=forced_review_reason,
                        applied_review_cadence_minutes=int(
                            schedule_details.get("applied_review_cadence_minutes") or review_interval_minutes
                        ),
                        review_cadence_source=str(
                            schedule_details.get("review_cadence_source") or ""
                        )
                        or None,
                        holding_profile_cadence_hint=_as_dict(
                            schedule_details.get("holding_profile_cadence_hint")
                        ),
                        cadence_fallback_reason=str(
                            schedule_details.get("cadence_fallback_reason") or ""
                        )
                        or None,
                        max_review_age_minutes=max_review_age_minutes,
                        cadence_profile_summary=_as_dict(
                            schedule_details.get("cadence_profile_summary")
                        ),
                        fingerprint_material={
                            "trigger_reason": trigger_reason,
                            "symbol": symbol,
                            "timeframe": effective_timeframe,
                            "holding_profile": holding_profile,
                            "strategy_engine": strategy_engine_name,
                            "assigned_slot": slot_allocation.get("assigned_slot"),
                            "candidate_weight": round(
                                _safe_float(slot_allocation.get("candidate_weight"), default=0.0),
                                6,
                            ),
                            "reason_codes": sorted(fingerprint_reason_codes),
                            "position_side": getattr(position_row, "side", None),
                            "position_status": getattr(position_row, "status", None),
                            "position_quantity": round(
                                _safe_float(getattr(position_row, "quantity", None), default=0.0),
                                8,
                            ),
                            "fingerprint_basis": fingerprint_basis,
                        },
                    )
            else:
                selection_context = self._selection_context_from_candidate_selection(
                    symbol=symbol,
                    candidate_selection=candidate_selection,
                )
                ranking_payload = ranking_lookup.get(symbol, {})
                candidate_payload = _as_dict(selection_context.get("candidate"))
                candidate_decision = str(candidate_payload.get("decision") or "").lower()
                strategy_engine_name = str(
                    selection_context.get("strategy_engine")
                    or candidate_payload.get("strategy_engine")
                    or "unspecified"
                )
                trigger_reason: str | None = None
                reason_codes = [
                    str(code)
                    for code in candidate_payload.get("rationale_codes", [])
                    if code not in {None, ""}
                ] if isinstance(candidate_payload.get("rationale_codes"), list) else []
                if bool(ranking_payload.get("selected")) and candidate_decision in {"long", "short"}:
                    trigger_reason = (
                        "breakout_exception_event"
                        if strategy_engine_name == "breakout_exception_engine"
                        or str(selection_context.get("entry_mode") or "") == "breakout_confirm"
                        else "entry_candidate_event"
                    )
                elif soft_ai_review_symbol == symbol and candidate_decision in {"hold", "long", "short"}:
                    trigger_reason = (
                        "breakout_exception_event"
                        if strategy_engine_name == "breakout_exception_engine"
                        or str(selection_context.get("entry_mode") or "") == "breakout_confirm"
                        else "entry_candidate_event"
                    )
                    reason_codes = list(
                        dict.fromkeys([*reason_codes, SOFT_SIGNAL_AI_REVIEW_REASON_CODE])
                    )

                if trigger_reason is not None:
                    trigger_payload = self._build_review_trigger_payload(
                        trigger_reason=trigger_reason,
                        symbol=symbol,
                        timeframe=effective_timeframe,
                        strategy_engine=strategy_engine_name or None,
                        holding_profile=str(
                            selection_context.get("holding_profile")
                            or candidate_payload.get("holding_profile")
                            or "scalp"
                        ),
                        assigned_slot=str(selection_context.get("assigned_slot") or "") or None,
                        candidate_weight=selection_context.get("candidate_weight"),
                        reason_codes=reason_codes,
                        last_decision_at=last_decision_at,
                        triggered_at=generated_at,
                        fingerprint_material={
                            "trigger_reason": trigger_reason,
                            "symbol": symbol,
                            "timeframe": effective_timeframe,
                            "strategy_engine": strategy_engine_name,
                            "holding_profile": selection_context.get("holding_profile")
                            or candidate_payload.get("holding_profile"),
                            "assigned_slot": selection_context.get("assigned_slot"),
                            "candidate_weight": round(
                                _safe_float(selection_context.get("candidate_weight"), default=0.0),
                                6,
                            ),
                            "reason_codes": sorted(reason_codes),
                            "decision": candidate_payload.get("decision"),
                            "scenario": candidate_payload.get("scenario"),
                            "selection_reason": selection_context.get("selection_reason"),
                            "slot_conviction_score": round(
                                _safe_float(selection_context.get("slot_conviction_score"), default=0.0),
                                6,
                            ),
                            "meta_gate_probability": round(
                                _safe_float(selection_context.get("meta_gate_probability"), default=0.0),
                                6,
                            ),
                            "score_total": round(
                                _safe_float(_as_dict(selection_context.get("score")).get("total_score"), default=0.0),
                                6,
                            ),
                        },
                    )

            if trigger_payload is not None and not open_positions and account_untrusted_reason_codes:
                plan_ai_call_policy = {
                    "ai_call_event": AI_CALL_EVENT_SKIPPED,
                    "ai_call_allowed": False,
                    "skip_ai": True,
                    "reason": "account_untrusted",
                    "scope": "new_entry",
                    "hard_skip_ai": True,
                    "skip_category": "hard_skip_ai",
                    "hard_skip_reason_codes": ["account_untrusted", *account_untrusted_reason_codes],
                    "allow_ai_but_later_risk_check": [],
                }
                if isinstance(selection_context, dict):
                    selection_context = {
                        **selection_context,
                        "ai_call_policy": plan_ai_call_policy,
                    }
                trigger_payload = None
                last_ai_skip_reason = "account_untrusted"

            exchange_permission_entry_block = (
                self._risk_adding_exchange_permission_pre_ai_block(intent_type="entry")
                if trigger_payload is not None and not open_positions
                else None
            )
            if exchange_permission_entry_block is not None:
                reason_code = str(exchange_permission_entry_block["reason_code"])
                plan_ai_call_policy = {
                    "ai_call_event": AI_CALL_EVENT_SKIPPED,
                    "ai_call_allowed": False,
                    "skip_ai": True,
                    "reason": reason_code,
                    "scope": "new_entry",
                    "hard_skip_ai": True,
                    "skip_category": "hard_skip_ai",
                    "hard_skip_reason_codes": [reason_code],
                    "allow_ai_but_later_risk_check": [],
                    "exchange_permission": dict(exchange_permission_entry_block),
                }
                if isinstance(selection_context, dict):
                    selection_context = {
                        **selection_context,
                        "ai_call_policy": plan_ai_call_policy,
                    }
                trigger_payload = None
                last_ai_skip_reason = reason_code

            if (
                trigger_payload is not None
                and not open_positions
                and isinstance(selection_context, dict)
                and trigger_payload.trigger_reason == "entry_candidate_event"
            ):
                entry_pre_ai_policy = self._entry_candidate_pre_ai_eligibility_policy(
                    symbol=symbol,
                    timeframe=effective_timeframe,
                    selection_context=selection_context,
                    generated_at=generated_at,
                    runtime_state=runtime_state,
                    allow_ai_but_later_risk_check=list(trigger_payload.reason_codes),
                )
                if entry_pre_ai_policy.get("action") != "allow":
                    plan_ai_call_policy = _as_dict(entry_pre_ai_policy.get("ai_call_policy"))
                    selection_context = {
                        **selection_context,
                        "ai_call_policy": plan_ai_call_policy,
                    }
                    trigger_payload = None
                    last_ai_skip_reason = str(
                        entry_pre_ai_policy.get("last_ai_skip_reason") or ""
                    ) or None

            if trigger_payload is not None and not open_positions and isinstance(selection_context, dict):
                soft_rejected_reason = str(
                    selection_context.get("rejected_reason")
                    or selection_context.get("selection_reason")
                    or ""
                ).strip().lower()
                weak_soft_review = (
                    SOFT_SIGNAL_AI_REVIEW_REASON_CODE in set(trigger_payload.reason_codes)
                    and soft_rejected_reason in AI_REVIEW_SOFT_REJECTED_REASONS
                    and not bool(selection_context.get("selected"))
                )
                if weak_soft_review:
                    soft_policy = self._soft_signal_review_policy(
                        symbol=symbol,
                        timeframe=effective_timeframe,
                        selection_context=selection_context,
                        soft_rejected_reason=soft_rejected_reason,
                        last_ai_invoked_at=last_ai_invoked_at,
                        latest_decision_metadata=latest_metadata,
                        generated_at=generated_at,
                        effective_settings=effective,
                    )
                    plan_ai_call_policy = _as_dict(soft_policy.get("ai_call_policy"))
                    selection_context = {
                        **selection_context,
                        "ai_call_policy": plan_ai_call_policy,
                    }
                    if soft_policy.get("action") in {
                        "suppress",
                        "low_edge_hold_suppressed",
                        "cooldown",
                        "no_material_change",
                        "hold_backoff",
                    }:
                        trigger_payload = None
                        last_ai_skip_reason = str(soft_policy.get("last_ai_skip_reason") or "") or None
                    elif soft_policy.get("action") == "allow_transition_watch":
                        trigger_payload = trigger_payload.model_copy(
                            update={
                                "reason_codes": list(
                                    dict.fromkeys(
                                        [
                                            *trigger_payload.reason_codes,
                                            SOFT_SIGNAL_TRANSITION_WATCH_REASON_CODE,
                                        ]
                                    )
                                ),
                                "forced_review_reason": SOFT_SIGNAL_TRANSITION_WATCH_FORCED_REASON,
                                "fingerprint_changed_fields": list(
                                    dict.fromkeys(
                                        [
                                            *trigger_payload.fingerprint_changed_fields,
                                            "soft_signal_transition_watch_due",
                                        ]
                                    )
                                ),
                            }
                        )
                        last_ai_skip_reason = None

            if (
                trigger_payload is not None
                and trigger_payload.trigger_reason
                not in {"manual_review_event", "protection_review_event"}
                and trigger_payload.forced_review_reason is None
                and str(latest_trigger_payload.get("trigger_reason") or "") == trigger_payload.trigger_reason
                and str(latest_trigger_payload.get("trigger_fingerprint") or "") == trigger_payload.trigger_fingerprint
            ):
                trigger_deduped = True
                dedupe_reason = "TRIGGER_FINGERPRINT_UNCHANGED"
                trigger_payload = trigger_payload.model_copy(update={"dedupe_reason": dedupe_reason})
                last_ai_skip_reason = "TRIGGER_DEDUPED"

            if trigger_payload is None and last_ai_skip_reason is None:
                last_ai_skip_reason = "NO_EVENT"

            plans.append(
                {
                    "symbol": symbol,
                    "timeframe": effective_timeframe,
                    "cadence": cadence_profile,
                    "selection_context": selection_context,
                    "trigger": trigger_payload.model_dump(mode="json") if trigger_payload is not None else None,
                    "trigger_deduped": trigger_deduped,
                    "last_decision_at": last_decision_at.isoformat() if last_decision_at is not None else None,
                    "last_ai_invoked_at": last_ai_invoked_at.isoformat() if last_ai_invoked_at is not None else None,
                    "last_material_review_at": (
                        last_material_review_at.isoformat()
                        if last_material_review_at is not None
                        else None
                    ),
                    "next_ai_review_due_at": (
                        next_ai_review_due_at.isoformat()
                        if next_ai_review_due_at is not None
                        else None
                    ),
                    "applied_review_cadence_minutes": schedule_details.get("applied_review_cadence_minutes"),
                    "review_cadence_source": schedule_details.get("review_cadence_source"),
                    "holding_profile_cadence_hint": _as_dict(
                        schedule_details.get("holding_profile_cadence_hint")
                    ),
                    "cadence_fallback_reason": schedule_details.get("cadence_fallback_reason"),
                    "max_review_age_minutes": max_review_age_minutes,
                    "cadence_profile_summary": _as_dict(
                        schedule_details.get("cadence_profile_summary")
                    ),
                    "fingerprint_changed_fields": (
                        list(trigger_payload.fingerprint_changed_fields)
                        if trigger_payload is not None
                        else []
                    ),
                    "dedupe_reason": dedupe_reason,
                    "forced_review_reason": (
                        trigger_payload.forced_review_reason
                        if trigger_payload is not None
                        else None
                    ),
                    "last_ai_skip_reason": last_ai_skip_reason,
                    "ai_call_policy": plan_ai_call_policy,
                    **active_position_suppression_payload,
                }
            )
        return {
            "generated_at": generated_at.isoformat(),
            "candidate_selection": candidate_selection,
            "plans": plans,
        }


    def run_decision_cycle(
        self,
        symbol: str | None = None,
        timeframe: str | None = None,
        trigger_event: str = TriggerEvent.MANUAL.value,
        upto_index: int | None = None,
        force_stale: bool = False,
        auto_resume_checked: bool = False,
        logic_variant: str = "improved",
        exchange_sync_checked: bool = False,
        include_inline_position_management: bool = False,
        market_snapshot_override: MarketSnapshotPayload | None = None,
        market_context_override: dict[str, MarketSnapshotPayload] | None = None,
        selection_context: dict[str, object] | None = None,
        review_trigger: AIReviewTriggerPayload | dict[str, object] | None = None,
    ) -> dict[str, object]:
        auto_resume_result = self._ensure_auto_resume(
            trigger_event=trigger_event,
            auto_resume_checked=auto_resume_checked,
        )
        symbol = (symbol or self.settings_row.default_symbol).upper()
        effective_settings = self._effective_symbol_settings(symbol)
        timeframe = timeframe or effective_settings.timeframe
        exchange_sync_result: dict[str, object] | None = None
        event_context_cycle_owner = self._begin_event_context_cycle(
            f"decision:{trigger_event}:{symbol}:{timeframe}:{utcnow_naive().isoformat()}"
        )
        try:
            if self._should_poll_exchange_state(trigger_event) and not exchange_sync_checked:
                exchange_sync_result = self.run_exchange_sync_cycle(symbol=symbol, trigger_event=trigger_event)
            if market_snapshot_override is None:
                market_snapshot, market_row = self._collect_market_snapshot(
                    symbol=symbol,
                    timeframe=timeframe,
                    upto_index=upto_index,
                    force_stale=force_stale,
                )
            else:
                market_snapshot = market_snapshot_override
                market_row = persist_market_snapshot(self.session, market_snapshot)
            cycle_id = self._build_cycle_id(
                trigger_event=trigger_event,
                symbol=symbol,
                snapshot_id=market_row.id,
            )
            market_context = (
                dict(market_context_override)
                if market_context_override is not None
                else build_market_context(
                    symbol=symbol,
                    base_timeframe=timeframe,
                    upto_index=upto_index,
                    force_stale=force_stale,
                    use_binance=self.settings_row.binance_market_data_enabled,
                    binance_testnet_enabled=self.settings_row.binance_testnet_enabled,
                    stale_threshold_seconds=self.settings_row.stale_market_seconds,
                    event_context_provider=self.event_context_provider,
                )
            )
            higher_timeframe_context = {
                tf: payload for tf, payload in market_context.items() if tf != timeframe
            }
            lead_market_features = self._build_lead_market_features(
                base_timeframe=timeframe,
                upto_index=upto_index,
                force_stale=force_stale,
            )
        finally:
            self._end_event_context_cycle(event_context_cycle_owner)
        if not self.settings_row.ai_enabled:
            cadence_profile = self.get_symbol_cadence_profile(
                symbol=symbol,
                timeframe=timeframe,
            )
            self._record_decision_funnel_audit(
                cycle_id=cycle_id,
                symbol=symbol,
                timeframe=timeframe,
                stage="ai_disabled",
                status="market_data_only",
                entry_plan_status="not_created",
                m1_confirmation_status="not_checked",
                final_risk_status="not_checked",
                blocked_reason_codes=["AI_DISABLED"],
                detail={"trigger_event": trigger_event},
                severity="info",
                entity_id=symbol,
                correlation_ids=normalize_correlation_ids(
                    cycle_id=cycle_id,
                    snapshot_id=market_row.id,
                ),
            )
            return {
                "symbol": symbol,
                "cycle_id": cycle_id,
                "market_snapshot_id": market_row.id,
                "feature_snapshot_id": None,
                "decision_run_id": None,
                "risk_check_id": None,
                "chief_review_run_id": None,
                "decision": None,
                "risk_result": None,
                "execution": None,
                "status": "market_data_only",
                "cadence": cadence_profile,
                "skip_reason": "AI_DISABLED",
                "ai_skipped_reason": "AI_DISABLED",
                "account": self._account_snapshot_preview(),
                "settings": serialize_settings(self.settings_row),
                "auto_resume": auto_resume_result,
                "exchange_sync": exchange_sync_result,
            }
        feature_payload = compute_features(
            market_snapshot,
            higher_timeframe_context,
            lead_market_features=lead_market_features,
        )
        feature_row = persist_feature_snapshot(self.session, market_row.id, market_snapshot, feature_payload)
        open_positions = get_open_positions(self.session, symbol)
        if (
            not open_positions
            and not isinstance(selection_context, dict)
            and trigger_event != TriggerEvent.REPLAY.value
        ):
            selection_context = self._default_selection_context_for_symbol(
                symbol=symbol,
                timeframe=timeframe,
                upto_index=upto_index,
                force_stale=force_stale,
            )
        effective_selection_context = dict(selection_context) if isinstance(selection_context, dict) else {}
        if not effective_selection_context and trigger_event != TriggerEvent.REPLAY.value:
            effective_selection_context = self._default_selection_context_for_symbol(
                symbol=symbol,
                timeframe=timeframe,
                upto_index=upto_index,
                force_stale=force_stale,
            )
        position_management_context = build_position_management_context(
            open_positions[0] if open_positions else None,
            feature_payload=feature_payload,
            settings_row=self.settings_row,
        )
        position_management_result: dict[str, object] | None = None
        if include_inline_position_management and open_positions and self._should_execute_live(trigger_event):
            position_management_result = apply_position_management(
                self.session,
                self.settings_row,
                symbol=symbol,
                feature_payload=feature_payload,
            )
            open_positions = get_open_positions(self.session, symbol)
            position_management_context = dict(
                position_management_result.get("position_management_context") or position_management_context
            )
        latest_pnl = get_latest_pnl_snapshot(self.session, self.settings_row)
        runtime_state = summarize_runtime_state(self.settings_row)
        market_settings_advisor_result: dict[str, object] | None = None
        market_settings_advisor_context: dict[str, object] = {}
        latest_decision_run = self._latest_symbol_decision_run(
            symbol=symbol,
            timeframe=timeframe,
        )
        last_decision_at = latest_decision_run.created_at if latest_decision_run is not None else None
        previous_ai_invoked_at = self._latest_symbol_ai_invoked_at(
            symbol=symbol,
            timeframe=timeframe,
        )
        decision_reference = self._build_decision_reference_payload(
            symbol=symbol,
            timeframe=timeframe,
            market_snapshot=market_snapshot,
            market_row=market_row,
            runtime_state=runtime_state,
        )
        if self._should_skip_same_candle_entry(
            symbol=symbol,
            timeframe=timeframe,
            market_snapshot=market_snapshot,
            has_open_position=bool(open_positions),
        ):
            self._record_decision_funnel_audit(
                cycle_id=cycle_id,
                symbol=symbol,
                timeframe=timeframe,
                regime=feature_payload.regime.primary_regime,
                stage="same_candle_skipped",
                status="blocked",
                strategy_candidate=self._strategy_candidate_funnel_payload(effective_selection_context),
                entry_plan_status="not_created",
                m1_confirmation_status="not_checked",
                final_risk_status="not_checked",
                blocked_reason_codes=["SAME_CANDLE_ENTRY_GUARD"],
                detail={"trigger_event": trigger_event},
                severity="info",
                entity_id=symbol,
                correlation_ids=normalize_correlation_ids(
                    cycle_id=cycle_id,
                    snapshot_id=market_row.id,
                ),
            )
            return {
                "symbol": symbol,
                "cycle_id": cycle_id,
                "market_snapshot_id": market_row.id,
                "feature_snapshot_id": feature_row.id,
                "decision_run_id": None,
                "risk_check_id": None,
                "chief_review_run_id": None,
                "decision": None,
                "risk_result": None,
                "execution": None,
                "status": "same_candle_skipped",
                "cadence": self.get_symbol_cadence_profile(
                    symbol=symbol,
                    timeframe=timeframe,
                    runtime_state=runtime_state,
                    open_positions=open_positions,
                ),
                "skip_reason": "SAME_CANDLE_ENTRY_GUARD",
                "ai_skipped_reason": None,
                "decision_reference": decision_reference,
                "market_settings_advisor": market_settings_advisor_result,
                "account": account_snapshot_to_dict(latest_pnl),
                "settings": serialize_settings(self.settings_row),
                "auto_resume": auto_resume_result,
                "exchange_sync": exchange_sync_result,
            }
        effective_leverage_cap = min(
            self.settings_row.max_leverage,
            HARD_MAX_GLOBAL_LEVERAGE,
            get_symbol_leverage_cap(symbol),
        )
        drawdown_state = self._sync_drawdown_state()
        risk_budget_context = build_ai_risk_budget_context(
            self.session,
            self.settings_row,
            decision_symbol=symbol,
            equity=latest_pnl.equity,
        )
        active_entry_plans = self._active_pending_entry_plans(symbol=symbol)
        risk_context = {
            "max_risk_per_trade": min(self.settings_row.max_risk_per_trade, HARD_MAX_RISK_PER_TRADE),
            "max_leverage": effective_leverage_cap,
            "symbol_risk_tier": get_symbol_risk_tier(symbol),
            "daily_pnl": latest_pnl.daily_pnl,
            "consecutive_losses": latest_pnl.consecutive_losses,
            "drawdown_state": drawdown_state,
            "operating_state": runtime_state["operating_state"],
            "protection_recovery_status": runtime_state["protection_recovery_status"],
            "missing_protection_symbols": runtime_state["missing_protection_symbols"],
            "missing_protection_items": runtime_state["missing_protection_items"],
            "risk_budget": risk_budget_context,
            "position_management_context": position_management_context,
        }
        risk_context["active_position_summary"] = self._active_position_ai_summary(
            open_positions=open_positions,
            position_management_context=position_management_context,
        )
        risk_context["pending_entry_plan_summary"] = self._pending_entry_plan_ai_summary(
            symbol=symbol,
            active_plans=active_entry_plans,
            latest_price=self._summary_float(market_snapshot.latest_price),
            risk_budget=risk_budget_context,
            open_positions=open_positions,
            now=utcnow_naive(),
        )
        risk_context["execution_constraints_summary"] = self._execution_constraints_ai_summary(
            symbol=symbol,
            market_snapshot=market_snapshot,
            risk_context=risk_context,
            settings_row=self.settings_row,
            open_positions=open_positions,
        )
        if isinstance(selection_context, dict) and selection_context:
            risk_context["universe_breadth"] = selection_context.get("universe_breadth") or {}
            risk_context["selection_context"] = dict(selection_context)
        adaptive_signal_context = build_adaptive_signal_context(
            self.session,
            enabled=self.settings_row.adaptive_signal_enabled,
            symbol=symbol,
            timeframe=timeframe,
            regime=feature_payload.regime.primary_regime,
            settings_row=self.settings_row,
        )
        risk_context["adaptive_signal_context"] = adaptive_signal_context
        setup_cluster_context = self._build_setup_cluster_context(
            symbol=symbol,
            timeframe=timeframe,
            regime=feature_payload.regime.primary_regime,
            trend_alignment=feature_payload.regime.trend_alignment,
        )
        risk_context["setup_cluster_context"] = setup_cluster_context
        cadence_profile = self.get_symbol_cadence_profile(
            symbol=symbol,
            timeframe=timeframe,
            runtime_state=runtime_state,
            open_positions=open_positions,
            feature_payload=feature_payload,
            adaptive_signal_context=adaptive_signal_context,
            setup_cluster_context=setup_cluster_context,
            include_adaptive_underperformance=True,
        )
        review_trigger_payload = self._review_trigger_payload_from_input(review_trigger)
        latest_decision_metadata = (
            latest_decision_run.metadata_json
            if latest_decision_run is not None and isinstance(latest_decision_run.metadata_json, dict)
            else {}
        )
        latest_decision_output = (
            latest_decision_run.output_payload
            if latest_decision_run is not None and isinstance(latest_decision_run.output_payload, dict)
            else {}
        )
        latest_decision_input = (
            latest_decision_run.input_payload
            if latest_decision_run is not None and isinstance(latest_decision_run.input_payload, dict)
            else {}
        )
        if review_trigger_payload is None and trigger_event == TriggerEvent.MANUAL.value:
            candidate_payload = _as_dict(effective_selection_context.get("candidate"))
            latest_slot_allocation = _as_dict(latest_decision_metadata.get("slot_allocation"))
            strategy_engine_name = (
                str(effective_selection_context.get("strategy_engine") or "")
            ) or _strategy_engine_name_from_payload(
                latest_decision_metadata,
                latest_decision_output,
            )
            holding_profile = (
                str(effective_selection_context.get("holding_profile") or "")
            ) or str(latest_decision_metadata.get("holding_profile") or "") or str(
                cadence_profile.get("active_holding_profile") or "scalp"
            )
            reason_codes = (
                [str(code) for code in candidate_payload.get("rationale_codes", []) if code not in {None, ""}]
                if isinstance(candidate_payload.get("rationale_codes"), list)
                else []
            )
            review_trigger_payload = self._build_review_trigger_payload(
                trigger_reason="manual_review_event",
                symbol=symbol,
                timeframe=timeframe,
                strategy_engine=strategy_engine_name or None,
                holding_profile=holding_profile or "scalp",
                assigned_slot=(
                    str(effective_selection_context.get("assigned_slot") or "")
                )
                or str(latest_slot_allocation.get("assigned_slot") or "")
                or None,
                candidate_weight=(
                    effective_selection_context.get("candidate_weight")
                    if effective_selection_context
                    else latest_slot_allocation.get("candidate_weight")
                ),
                reason_codes=reason_codes,
                last_decision_at=last_decision_at,
                triggered_at=utcnow_naive(),
                fingerprint_material={
                    "trigger_reason": "manual_review_event",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "strategy_engine": strategy_engine_name,
                    "holding_profile": holding_profile or "scalp",
                    "assigned_slot": (
                        effective_selection_context.get("assigned_slot")
                        if effective_selection_context
                        else latest_slot_allocation.get("assigned_slot")
                    ),
                    "candidate_weight": round(
                        _safe_float(
                            effective_selection_context.get("candidate_weight")
                            if effective_selection_context
                            else latest_slot_allocation.get("candidate_weight"),
                            default=0.0,
                        ),
                        6,
                    ),
                    "reason_codes": sorted(reason_codes),
                    "open_position": bool(open_positions),
                },
            )
        active_position_prompt_route_context: dict[str, object] = {}
        active_position_entry_fingerprint_basis: dict[str, object] | None = None
        if open_positions:
            active_position_prompt_route_context, active_position_entry_fingerprint_basis = (
                self._build_active_position_prompt_route_context(
                    symbol=symbol,
                    timeframe=timeframe,
                    open_positions=open_positions,
                    feature_payload=feature_payload,
                    position_management_context=position_management_context,
                    risk_context=risk_context,
                    selection_context=effective_selection_context,
                    runtime_state=runtime_state,
                    latest_decision_run=latest_decision_run,
                    latest_decision_metadata=latest_decision_metadata,
                    latest_decision_output=latest_decision_output,
                    review_trigger_payload=review_trigger_payload,
                    decision_reference=decision_reference,
                )
            )
            if active_position_prompt_route_context:
                merged_strategy_engine_context = {
                    **_as_dict(effective_selection_context.get("strategy_engine_context")),
                    **active_position_prompt_route_context,
                }
                effective_selection_context = {
                    **effective_selection_context,
                    "strategy_engine_context": merged_strategy_engine_context,
                }
                risk_context["selection_context"] = dict(effective_selection_context)
        schedule_details = self.resolve_interval_decision_schedule_details(
            symbol=symbol,
            timeframe=timeframe,
            effective_settings=effective_settings,
            cadence_profile=cadence_profile,
            open_positions=open_positions,
            latest_decision_run=latest_decision_run,
            review_trigger=review_trigger_payload,
        )
        review_interval_minutes = int(
            schedule_details.get("applied_review_cadence_minutes")
            or self._position_review_interval_minutes(
                cadence_profile,
                effective_settings=effective_settings,
            )
        )
        max_review_age_minutes = (
            self._open_position_max_review_age_minutes(
                review_interval_minutes,
                backstop_interval_minutes=(
                    effective_settings.ai_backstop_interval_minutes
                    if effective_settings.ai_backstop_enabled
                    else None
                ),
            )
            if open_positions
            else None
        )
        next_ai_review_due_at = None
        if review_trigger_payload is not None and open_positions:
            review_trigger_payload = review_trigger_payload.model_copy(
                update={
                    "applied_review_cadence_minutes": int(
                        schedule_details.get("applied_review_cadence_minutes")
                        or review_interval_minutes
                    ),
                    "review_cadence_source": (
                        str(schedule_details.get("review_cadence_source") or "") or None
                    ),
                    "holding_profile_cadence_hint": _as_dict(
                        schedule_details.get("holding_profile_cadence_hint")
                    ),
                    "cadence_fallback_reason": (
                        str(schedule_details.get("cadence_fallback_reason") or "") or None
                    ),
                    "max_review_age_minutes": max_review_age_minutes,
                    "cadence_profile_summary": _as_dict(
                        schedule_details.get("cadence_profile_summary")
                    ),
                }
            )
        openai_gate = get_openai_call_gate(
            self.session,
            self.settings_row,
            AgentRole.TRADING_DECISION.value,
            trigger_event,
            has_openai_key=bool(self.credentials.openai_api_key),
            symbol=symbol,
            cooldown_minutes_override=(
                0
                if review_trigger_payload is not None
                and review_trigger_payload.trigger_reason != "manual_review_event"
                else int(cadence_profile["effective_cadence"]["ai_call_interval_minutes"])
            ),
            manual_guard_minutes_override=max(
                2,
                min(int(cadence_profile["effective_cadence"]["ai_call_interval_minutes"]), 5),
            ),
            enforce_waste_guard=(
                not bool(open_positions)
                or bool(
                    active_position_prompt_route_context.get("risk_adding_add_on_candidate")
                    or active_position_prompt_route_context.get("allow_same_side_add_on")
                )
            ),
        )
        risk_adding_add_on_pre_ai_block_reason = self._risk_adding_add_on_pre_ai_hard_block_reason(
            active_position_prompt_route_context=active_position_prompt_route_context,
            effective_settings=effective_settings,
            runtime_state=runtime_state,
            openai_gate=openai_gate,
        )
        if self._mark_risk_adding_add_on_pre_ai_blocked(
            active_position_prompt_route_context,
            risk_adding_add_on_pre_ai_block_reason,
        ):
            merged_strategy_engine_context = {
                **_as_dict(effective_selection_context.get("strategy_engine_context")),
                **active_position_prompt_route_context,
            }
            effective_selection_context = {
                **effective_selection_context,
                "strategy_engine_context": merged_strategy_engine_context,
            }
            risk_context["selection_context"] = dict(effective_selection_context)
        recent_tp_direction = (
            self._recent_tp_direction_from_selection_context(effective_selection_context)
            if not open_positions
            else None
        )
        recent_tp_lookback_minutes = self._recent_tp_close_lookback_minutes(
            cadence_profile=cadence_profile,
            selection_context=effective_selection_context,
            ai_call_interval_minutes=self.settings_row.ai_call_interval_minutes,
        )
        ai_risk_context = self._risk_context_with_recent_tp_close_summary(
            risk_context,
            symbol=symbol,
            direction=recent_tp_direction,
            generated_at=utcnow_naive(),
            lookback_minutes=recent_tp_lookback_minutes,
        )
        ai_context = build_ai_decision_context(
            market_snapshot=market_snapshot,
            features=feature_payload,
            risk_context=ai_risk_context,
            selection_context=effective_selection_context,
            review_trigger=review_trigger_payload,
            decision_reference=decision_reference,
            previous_decision_output=latest_decision_output,
            previous_decision_metadata=latest_decision_metadata,
            previous_input_payload=latest_decision_input,
            previous_ai_invoked_at=previous_ai_invoked_at,
        )
        allow_ai_but_later_risk_check = self._entry_candidate_soft_ai_context_reason_codes(
            review_trigger_payload=review_trigger_payload,
            feature_payload=feature_payload,
            open_positions=open_positions,
            cadence_profile=cadence_profile,
            selection_context=effective_selection_context,
        )
        ai_call_policy = self._ai_call_policy(
            review_trigger_payload=review_trigger_payload,
            market_snapshot=market_snapshot,
            effective_settings=effective_settings,
            runtime_state=runtime_state,
            ai_context=ai_context,
            openai_gate=openai_gate,
            cadence_profile=cadence_profile,
            open_positions=open_positions,
            allow_ai_but_later_risk_check=allow_ai_but_later_risk_check,
            selection_context=effective_selection_context,
            generated_at=utcnow_naive(),
            active_position_prompt_route_context=active_position_prompt_route_context,
        )
        pre_ai_skip_reason = str(ai_call_policy.get("pre_ai_skip_reason") or "") or None
        ai_skipped_reason = pre_ai_skip_reason or str(ai_call_policy.get("reason") or "") or None
        use_ai = bool(ai_call_policy.get("ai_call_allowed", False)) and bool(openai_gate.allowed)
        if self._should_run_market_settings_before_trading_ai(
            use_ai=use_ai,
            review_trigger_payload=review_trigger_payload,
        ):
            market_settings_advisor_result = self._maybe_run_market_settings_advisor(
                symbol=symbol,
                trigger_event=trigger_event,
                market_snapshot=market_snapshot,
                feature_payload=feature_payload,
                runtime_state=runtime_state,
                cycle_id=cycle_id,
                snapshot_id=market_row.id,
                reuse_cached=True,
            )
            risk_context, market_settings_advisor_context = (
                self._risk_context_with_market_settings_advisor(
                    risk_context,
                    market_settings_advisor_result,
                )
            )
            ai_market_settings_advisor_context = market_settings_advisor_context
            if bool(market_settings_advisor_context.get("shadow", False)):
                ai_market_settings_advisor_context = self._market_settings_advisor_trading_context(
                    market_settings_advisor_result,
                    include_shadow_policy=True,
                )
            ai_risk_context = self._risk_context_with_recent_tp_close_summary(
                risk_context,
                symbol=symbol,
                direction=recent_tp_direction,
                generated_at=utcnow_naive(),
                lookback_minutes=recent_tp_lookback_minutes,
            )
            if ai_market_settings_advisor_context:
                ai_risk_context = self._risk_context_with_advisor_prompt_context(
                    ai_risk_context,
                    ai_market_settings_advisor_context,
                )
                market_settings_advisor_context = ai_market_settings_advisor_context
            ai_context = build_ai_decision_context(
                market_snapshot=market_snapshot,
                features=feature_payload,
                risk_context=ai_risk_context,
                selection_context=effective_selection_context,
                review_trigger=review_trigger_payload,
                decision_reference=decision_reference,
                previous_decision_output=latest_decision_output,
                previous_decision_metadata=latest_decision_metadata,
                previous_input_payload=latest_decision_input,
                previous_ai_invoked_at=previous_ai_invoked_at,
            )
        prior_read_debug: dict[str, object] = {}
        ai_prior_context = build_ai_prior_context(
            self.session,
            ai_context=ai_context,
            selection_context=effective_selection_context,
            feature_payload=feature_payload,
            debug_collector=prior_read_debug,
        )
        ai_context = ai_context.model_copy(update={"prior_context": ai_prior_context})
        ai_context_payload = ai_context.model_dump(mode="json")
        decision, provider_name, decision_metadata = self.trading_agent.run(
            market_snapshot,
            feature_payload,
            open_positions,
            ai_risk_context,
            use_ai=use_ai,
            max_input_candles=self.settings_row.ai_max_input_candles,
            logic_variant=logic_variant,
            ai_context=ai_context,
        )
        transition_watch_review = (
            SOFT_SIGNAL_TRANSITION_WATCH_REASON_CODE
            in set(ai_call_policy.get("allow_ai_but_later_risk_check") or [])
        )
        transition_watch_bound_metadata: dict[str, object] = {}
        if transition_watch_review:
            decision, transition_watch_bound_metadata = self._bound_transition_watch_direct_entry(decision)
            if transition_watch_bound_metadata:
                existing_fallback_codes = list(decision_metadata.get("fallback_reason_codes") or [])
                decision_metadata = {
                    **decision_metadata,
                    **transition_watch_bound_metadata,
                    "fallback_reason_codes": self._unique_reason_codes(
                        [
                            *existing_fallback_codes,
                            *list(transition_watch_bound_metadata.get("fallback_reason_codes") or []),
                        ]
                    ),
                }
        meta_gate_result = evaluate_meta_gate(
            decision,
            feature_payload=feature_payload,
            selection_context=dict(selection_context) if isinstance(selection_context, dict) else {},
            decision_metadata=decision_metadata,
        )
        decision_generated_at = utcnow_naive()
        resolved_last_ai_invoked_at = (
            decision_generated_at
            if str(decision_metadata.get("source") or "") == "llm"
            else previous_ai_invoked_at
        )
        decision_metadata = {
            **decision_metadata,
            "gate": openai_gate.as_metadata(),
            "logic_variant": logic_variant,
            "symbol": symbol,
            "timeframe": timeframe,
            "ai_provider": self.settings_row.ai_provider,
            "ai_model": self.settings_row.ai_model,
            "holding_profile": getattr(decision, "holding_profile", "scalp"),
            "holding_profile_reason": getattr(decision, "holding_profile_reason", None),
            "cadence": cadence_profile,
            "ai_skipped_reason": ai_skipped_reason,
            "pre_ai_skip_reason": pre_ai_skip_reason,
            "ai_call_policy": ai_call_policy,
            "ai_call_event": ai_call_policy.get("ai_call_event"),
            "entry_candidate_ai_hold_fingerprint": ai_call_policy.get(
                "entry_candidate_ai_hold_fingerprint"
            ),
            "entry_candidate_ai_hold_cooldown": ai_call_policy.get(
                "entry_candidate_ai_hold_cooldown"
            ),
            "hard_skip_ai": bool(ai_call_policy.get("hard_skip_ai", False)),
            "hard_skip_ai_reason": ai_call_policy.get("reason") if ai_call_policy.get("hard_skip_ai") else None,
            "soft_signal_review_mode": ai_call_policy.get("soft_signal_review_mode"),
            "soft_signal_review_snapshot": ai_call_policy.get("soft_signal_review_snapshot"),
            "soft_signal_review_material_change": ai_call_policy.get(
                "soft_signal_review_material_change"
            ),
            "transition_watch_ai_invoked": bool(transition_watch_review),
            "watch_only_bounded": bool(decision_metadata.get("watch_only_bounded", False)),
            "soft_signal_bounded_action": decision_metadata.get("soft_signal_bounded_action"),
            "soft_signal_original_decision": decision_metadata.get("soft_signal_original_decision"),
            "allow_ai_but_later_risk_check": list(
                ai_call_policy.get("allow_ai_but_later_risk_check") or []
            ),
            "effective_cadence": dict(cadence_profile.get("effective_cadence") or {}),
            "analysis_context": _decision_analysis_context(
                feature_payload,
                universe_breadth=effective_selection_context.get("universe_breadth") if effective_selection_context else None,
            ),
            "selection_context": effective_selection_context or None,
            "market_settings_advisor": market_settings_advisor_result,
            "market_settings_advisor_context": market_settings_advisor_context or None,
            "market_settings_advisor_pre_trade_refresh": bool(
                market_settings_advisor_result is not None
                and use_ai
                and market_settings_advisor_result.get("status") not in {"reused", "skipped"}
            ),
            "market_settings_advisor_pre_trade_status": (
                market_settings_advisor_result.get("status")
                if market_settings_advisor_result is not None
                else None
            ),
            "active_position_prompt_route_context": active_position_prompt_route_context or None,
            "active_position_entry_fingerprint_basis": active_position_entry_fingerprint_basis,
            "slot_allocation": (
                dict(effective_selection_context.get("slot_allocation"))
                if isinstance(effective_selection_context.get("slot_allocation"), dict)
                else None
            ),
            "ai_context": ai_context_payload,
            "ai_context_version": ai_context.ai_context_version,
            "event_risk_active": ai_context.event_risk_active,
            "event_risk_reason_codes": list(ai_context.event_risk_reason_codes),
            "event_risk_context": dict(ai_context.event_risk_context),
            "ai_trigger": review_trigger_payload.model_dump(mode="json") if review_trigger_payload is not None else None,
            "last_ai_trigger_reason": review_trigger_payload.trigger_reason if review_trigger_payload is not None else None,
            "last_ai_invoked_at": (
                resolved_last_ai_invoked_at.isoformat()
                if resolved_last_ai_invoked_at is not None
                else None
            ),
            "next_ai_review_due_at": (
                next_ai_review_due_at.isoformat()
                if next_ai_review_due_at is not None
                else None
            ),
            "trigger_deduped": False,
            "trigger_fingerprint": (
                review_trigger_payload.trigger_fingerprint
                if review_trigger_payload is not None
                else None
            ),
            "fingerprint_changed_fields": (
                list(review_trigger_payload.fingerprint_changed_fields)
                if review_trigger_payload is not None
                else []
            ),
            "dedupe_reason": (
                review_trigger_payload.dedupe_reason
                if review_trigger_payload is not None
                else None
            ),
            "last_material_review_at": decision_generated_at.isoformat(),
            "forced_review_reason": (
                review_trigger_payload.forced_review_reason
                if review_trigger_payload is not None
                else None
            ),
            "last_ai_skip_reason": ai_skipped_reason,
            "applied_review_cadence_minutes": schedule_details.get("applied_review_cadence_minutes"),
            "review_cadence_source": schedule_details.get("review_cadence_source"),
            "holding_profile_cadence_hint": _as_dict(
                schedule_details.get("holding_profile_cadence_hint")
            ),
            "cadence_fallback_reason": schedule_details.get("cadence_fallback_reason"),
            "max_review_age_minutes": max_review_age_minutes,
            "cadence_profile_summary": _as_dict(
                schedule_details.get("cadence_profile_summary")
            ),
            "engine_prior_classification": decision_metadata.get("engine_prior_classification"),
            "capital_efficiency_classification": decision_metadata.get("capital_efficiency_classification"),
            "session_prior_classification": decision_metadata.get("session_prior_classification"),
            "time_of_day_prior_classification": decision_metadata.get("time_of_day_prior_classification"),
            "session_prior_sample_count": decision_metadata.get("session_prior_sample_count"),
            "time_of_day_prior_sample_count": decision_metadata.get("time_of_day_prior_sample_count"),
            "session_prior_recency_minutes": decision_metadata.get("session_prior_recency_minutes"),
            "time_of_day_prior_recency_minutes": decision_metadata.get("time_of_day_prior_recency_minutes"),
            "session_time_calibration_reason_codes": decision_metadata.get("session_time_calibration_reason_codes"),
            "session_time_penalty_applied": decision_metadata.get("session_time_penalty_applied"),
            "prior_penalty_level": decision_metadata.get("prior_penalty_level"),
            "prior_reason_codes": decision_metadata.get("prior_reason_codes"),
            "sample_threshold_satisfied": decision_metadata.get("sample_threshold_satisfied"),
            "confidence_adjustment_applied": decision_metadata.get("confidence_adjustment_applied"),
            "abstain_due_to_prior_and_quality": decision_metadata.get("abstain_due_to_prior_and_quality"),
            "expected_payoff_efficiency_hint_summary": decision_metadata.get("expected_payoff_efficiency_hint_summary"),
            "prior_read_path": prior_read_debug.get("prior_read_path"),
            "cache_applied": bool(prior_read_debug.get("cache_applied", False)),
            "cache_fallback_used": bool(prior_read_debug.get("cache_fallback_used", False)),
            "drawdown_state": drawdown_state,
            "position_management": position_management_result or {"position_management_context": position_management_context},
            "holding_profile_context": decision_metadata.get("holding_profile_context"),
            "setup_cluster_state": decision_metadata.get("setup_cluster_state"),
            "meta_gate": meta_gate_result.model_dump(mode="json"),
            "cycle_id": cycle_id,
            "snapshot_id": market_row.id,
        }
        usage_payload = (
            decision_metadata.get("usage") if isinstance(decision_metadata.get("usage"), dict) else None
        )
        provider_attempted = provider_name == "openai" or str(decision_metadata.get("source") or "") in {
            "llm",
            "llm_fallback",
        }
        if provider_attempted:
            estimated_cost_usd = estimate_ai_usage_cost_usd(
                model=self.settings_row.ai_model,
                usage=usage_payload,
            )
            decision_metadata["estimated_cost_usd"] = estimated_cost_usd
            if usage_payload is None:
                decision_metadata["cost_estimate_status"] = "missing_usage"
            elif estimated_cost_usd is None:
                decision_metadata["cost_estimate_status"] = "unknown_model_rate"
            else:
                decision_metadata["cost_estimate_status"] = "estimated"
        intent_semantics = infer_intent_semantics(
            decision.model_dump(mode="json"),
            decision_metadata,
        )
        decision = decision.model_copy(update=intent_semantics)
        decision_metadata = {
            **decision_metadata,
            **intent_semantics,
        }
        trade_performance_tags = _decision_trade_performance_tags(
            decision,
            decision_metadata,
            ai_context_payload=ai_context_payload,
            drawdown_state=drawdown_state,
            feature_payload=feature_payload,
        )
        decision_metadata["trade_performance_tags"] = trade_performance_tags
        decision_metadata["generated_at"] = decision_generated_at.isoformat()
        decision_metadata["ai_decision_validity"] = _ai_decision_validity_payload(
            decision=decision,
            generated_at=decision_generated_at,
            market_snapshot=market_snapshot,
            market_snapshot_id=market_row.id,
            feature_payload=feature_payload,
            trade_performance_tags=trade_performance_tags,
        )
        decision_run = persist_agent_run(
            self.session,
            AgentRole.TRADING_DECISION,
            trigger_event,
            build_trading_decision_input_payload(
                market_snapshot=market_snapshot,
                higher_timeframe_context=higher_timeframe_context,
                feature_payload=feature_payload,
                risk_context=ai_risk_context,
                decision_reference=decision_reference,
                ai_trigger=review_trigger_payload.model_dump(mode="json") if review_trigger_payload is not None else None,
                ai_context=ai_context,
            ),
            decision,
            provider_name=provider_name,
            metadata_json=decision_metadata,
        )
        decision_correlation_ids = normalize_correlation_ids(
            cycle_id=cycle_id,
            snapshot_id=market_row.id,
            decision_id=decision_run.id,
        )
        active_position_suppression_payload = self._active_position_suppression_audit_payload(
            active_position_prompt_route_context
        )
        record_audit_event(
            self.session,
            event_type="agent_output",
            entity_type="agent_run",
            entity_id=str(decision_run.id),
            message="Trading decision generated.",
            payload={
                "provider": provider_name,
                "decision": decision.model_dump(mode="json"),
                "trade_performance_tags": decision_metadata.get("trade_performance_tags"),
                "analysis_context": _decision_analysis_context(
                    feature_payload,
                    universe_breadth=effective_selection_context.get("universe_breadth") if effective_selection_context else None,
                ),
                "selection_context": effective_selection_context or None,
                "slot_allocation": (
                    dict(effective_selection_context.get("slot_allocation"))
                    if isinstance(effective_selection_context.get("slot_allocation"), dict)
                    else None
                ),
                "holding_profile_context": decision_metadata.get("holding_profile_context"),
                "drawdown_state": drawdown_state,
                "setup_cluster_state": decision_metadata.get("setup_cluster_state"),
                "meta_gate": meta_gate_result.model_dump(mode="json"),
                "ai_trigger": decision_metadata.get("ai_trigger"),
                "pre_ai_skip_reason": decision_metadata.get("pre_ai_skip_reason"),
                "ai_call_event": decision_metadata.get("ai_call_event"),
                "hard_skip_ai": decision_metadata.get("hard_skip_ai"),
                "hard_skip_ai_reason": decision_metadata.get("hard_skip_ai_reason"),
                "soft_signal_review_mode": decision_metadata.get("soft_signal_review_mode"),
                "transition_watch_ai_invoked": decision_metadata.get("transition_watch_ai_invoked"),
                "watch_only_bounded": decision_metadata.get("watch_only_bounded"),
                "soft_signal_bounded_action": decision_metadata.get("soft_signal_bounded_action"),
                "allow_ai_but_later_risk_check": decision_metadata.get("allow_ai_but_later_risk_check"),
                "prompt_family": decision_metadata.get("prompt_family"),
                "bounded_output_applied": decision_metadata.get("bounded_output_applied"),
                "fallback_reason_codes": decision_metadata.get("fallback_reason_codes"),
                "fail_closed_applied": decision_metadata.get("fail_closed_applied"),
                "data_quality_fail_closed_applied": decision_metadata.get("data_quality_fail_closed_applied"),
                "data_quality_block_reason_codes": decision_metadata.get("data_quality_block_reason_codes"),
                "minimum_quality_required": decision_metadata.get("minimum_quality_required"),
                "abstain_due_to_data_quality": decision_metadata.get("abstain_due_to_data_quality"),
                "quality_penalty_level": decision_metadata.get("quality_penalty_level"),
                "provider_not_called_due_to_quality": decision_metadata.get("provider_not_called_due_to_quality"),
                "fingerprint_changed_fields": decision_metadata.get("fingerprint_changed_fields"),
                "dedupe_reason": decision_metadata.get("dedupe_reason"),
                "last_material_review_at": decision_metadata.get("last_material_review_at"),
                "forced_review_reason": decision_metadata.get("forced_review_reason"),
                "engine_prior_classification": decision_metadata.get("engine_prior_classification"),
                "capital_efficiency_classification": decision_metadata.get("capital_efficiency_classification"),
                "session_prior_classification": decision_metadata.get("session_prior_classification"),
                "time_of_day_prior_classification": decision_metadata.get("time_of_day_prior_classification"),
                "session_prior_sample_count": decision_metadata.get("session_prior_sample_count"),
                "time_of_day_prior_sample_count": decision_metadata.get("time_of_day_prior_sample_count"),
                "session_prior_recency_minutes": decision_metadata.get("session_prior_recency_minutes"),
                "time_of_day_prior_recency_minutes": decision_metadata.get("time_of_day_prior_recency_minutes"),
                "session_time_calibration_reason_codes": decision_metadata.get("session_time_calibration_reason_codes"),
                "session_time_penalty_applied": decision_metadata.get("session_time_penalty_applied"),
                "prior_penalty_level": decision_metadata.get("prior_penalty_level"),
                "prior_reason_codes": decision_metadata.get("prior_reason_codes"),
                "sample_threshold_satisfied": decision_metadata.get("sample_threshold_satisfied"),
                "confidence_adjustment_applied": decision_metadata.get("confidence_adjustment_applied"),
                "abstain_due_to_prior_and_quality": decision_metadata.get("abstain_due_to_prior_and_quality"),
                "expected_payoff_efficiency_hint_summary": decision_metadata.get("expected_payoff_efficiency_hint_summary"),
                "prior_read_path": decision_metadata.get("prior_read_path"),
                "cache_applied": decision_metadata.get("cache_applied"),
                "cache_fallback_used": decision_metadata.get("cache_fallback_used"),
                "intent_family": decision_metadata.get("intent_family"),
                "management_action": decision_metadata.get("management_action"),
                "legacy_semantics_preserved": decision_metadata.get("legacy_semantics_preserved"),
                "analytics_excluded_from_entry_stats": decision_metadata.get("analytics_excluded_from_entry_stats"),
                **active_position_suppression_payload,
            },
            correlation_ids=decision_correlation_ids,
        )
        if str(decision_metadata.get("source") or "") == "llm":
            record_audit_event(
                self.session,
                event_type="decision_ai_invoked",
                entity_type="decision_run",
                entity_id=str(decision_run.id),
                severity="info",
                message="AI inference was invoked for the current decision review.",
                payload={
                    "ai_call_event": AI_CALL_EVENT_ALLOWED,
                    "symbol": symbol,
                    "provider": provider_name,
                    "scope": ai_call_policy.get("scope"),
                    "hard_skip_ai": False,
                    "soft_signal_review_mode": ai_call_policy.get("soft_signal_review_mode"),
                    "transition_watch_ai_invoked": bool(transition_watch_review),
                    "watch_only_bounded": decision_metadata.get("watch_only_bounded"),
                    "soft_signal_bounded_action": decision_metadata.get("soft_signal_bounded_action"),
                    "allow_ai_but_later_risk_check": list(
                        ai_call_policy.get("allow_ai_but_later_risk_check") or []
                    ),
                    "snapshot_id": market_row.id,
                    "trigger": decision_metadata.get("ai_trigger"),
                    "next_ai_review_due_at": decision_metadata.get("next_ai_review_due_at"),
                    "fingerprint_changed_fields": decision_metadata.get("fingerprint_changed_fields"),
                    "last_material_review_at": decision_metadata.get("last_material_review_at"),
                    "forced_review_reason": decision_metadata.get("forced_review_reason"),
                    "applied_review_cadence_minutes": decision_metadata.get("applied_review_cadence_minutes"),
                    "review_cadence_source": decision_metadata.get("review_cadence_source"),
                    "cadence_fallback_reason": decision_metadata.get("cadence_fallback_reason"),
                    "max_review_age_minutes": decision_metadata.get("max_review_age_minutes"),
                    **active_position_suppression_payload,
                },
                correlation_ids=decision_correlation_ids,
            )
            record_audit_event(
                self.session,
                event_type="decision_ai_received",
                entity_type="decision_run",
                entity_id=str(decision_run.id),
                severity="info",
                message="AI decision output was received for risk evaluation.",
                payload={
                    "ai_call_event": "AI_DECISION_RECEIVED",
                    "symbol": symbol,
                    "provider": provider_name,
                    "decision_type": decision.decision,
                    "intent": decision_metadata.get("intent_family"),
                    "confidence": decision.confidence,
                    "scope": ai_call_policy.get("scope"),
                    "trigger": decision_metadata.get("ai_trigger"),
                    "allow_ai_but_later_risk_check": list(
                        ai_call_policy.get("allow_ai_but_later_risk_check") or []
                    ),
                    "snapshot_id": market_row.id,
                },
                correlation_ids=decision_correlation_ids,
            )
        if ai_skipped_reason is not None:
            record_audit_event(
                self.session,
                event_type="decision_ai_skipped",
                entity_type="decision_run",
                entity_id=str(decision_run.id),
                severity="info",
                message="AI inference was skipped and deterministic decision logic was used.",
                payload={
                    "ai_call_event": AI_CALL_EVENT_SKIPPED,
                    "symbol": symbol,
                    "reason": ai_skipped_reason,
                    "scope": ai_call_policy.get("scope"),
                    "hard_skip_ai": bool(ai_call_policy.get("hard_skip_ai", False)),
                    "skip_category": ai_call_policy.get("skip_category"),
                    "soft_signal_review_mode": ai_call_policy.get("soft_signal_review_mode"),
                    "hard_skip_reason_codes": list(ai_call_policy.get("hard_skip_reason_codes") or []),
                    "entry_candidate_ai_hold_cooldown": ai_call_policy.get(
                        "entry_candidate_ai_hold_cooldown"
                    ),
                    "entry_candidate_ai_hold_fingerprint": ai_call_policy.get(
                        "entry_candidate_ai_hold_fingerprint"
                    ),
                    "allow_ai_but_later_risk_check": list(
                        ai_call_policy.get("allow_ai_but_later_risk_check") or []
                    ),
                    "snapshot_id": market_row.id,
                    "cadence_mode": cadence_profile.get("mode"),
                    "cadence_reasons": list(cadence_profile.get("reasons") or []),
                    "ai_skipped_reason": ai_skipped_reason,
                    "pre_ai_skip_reason": decision_metadata.get("pre_ai_skip_reason"),
                    "gate": openai_gate.as_metadata(),
                    "trigger": decision_metadata.get("ai_trigger"),
                    "fingerprint_changed_fields": decision_metadata.get("fingerprint_changed_fields"),
                    "dedupe_reason": decision_metadata.get("dedupe_reason"),
                    "last_material_review_at": decision_metadata.get("last_material_review_at"),
                    "forced_review_reason": decision_metadata.get("forced_review_reason"),
                    "applied_review_cadence_minutes": decision_metadata.get("applied_review_cadence_minutes"),
                    "review_cadence_source": decision_metadata.get("review_cadence_source"),
                    "cadence_fallback_reason": decision_metadata.get("cadence_fallback_reason"),
                    "max_review_age_minutes": decision_metadata.get("max_review_age_minutes"),
                    "intent_family": decision_metadata.get("intent_family"),
                    "management_action": decision_metadata.get("management_action"),
                    **active_position_suppression_payload,
                },
                correlation_ids=decision_correlation_ids,
            )
        if bool(decision_metadata.get("bounded_output_applied")) or bool(decision_metadata.get("fail_closed_applied")):
            record_audit_event(
                self.session,
                event_type="decision_ai_bounded",
                entity_type="decision_run",
                entity_id=str(decision_run.id),
                severity="warning" if decision_metadata.get("fail_closed_applied") else "info",
                message="AI output was bounded or fail-closed before risk approval.",
                payload={
                    "symbol": symbol,
                    "provider": provider_name,
                    "prompt_family": decision_metadata.get("prompt_family"),
                    "trigger_type": decision_metadata.get("trigger_type"),
                    "provider_status": decision_metadata.get("provider_status"),
                    "fallback_reason_codes": decision_metadata.get("fallback_reason_codes"),
                    "fail_closed_applied": decision_metadata.get("fail_closed_applied"),
                    "data_quality_fail_closed_applied": decision_metadata.get("data_quality_fail_closed_applied"),
                    "data_quality_block_reason_codes": decision_metadata.get("data_quality_block_reason_codes"),
                    "minimum_quality_required": decision_metadata.get("minimum_quality_required"),
                    "abstain_due_to_data_quality": decision_metadata.get("abstain_due_to_data_quality"),
                    "quality_penalty_level": decision_metadata.get("quality_penalty_level"),
                    "provider_not_called_due_to_quality": decision_metadata.get("provider_not_called_due_to_quality"),
                    "should_abstain": decision_metadata.get("should_abstain"),
                    "abstain_reason_codes": decision_metadata.get("abstain_reason_codes"),
                    **active_position_suppression_payload,
                },
                correlation_ids=decision_correlation_ids,
            )
        watch_entry_plan_decision = (
            self._watch_entry_plan_decision(decision)
            if trigger_event != "historical_replay" and not open_positions
            else None
        )
        if not open_positions and decision.decision == "hold" and watch_entry_plan_decision is None:
            self._record_decision_skip_event(
                symbol=symbol,
                timeframe=timeframe,
                market_row=market_row,
                market_snapshot=market_snapshot,
                decision_run=decision_run,
                decision=decision,
                decision_metadata=decision_metadata,
                ai_skipped_reason=ai_skipped_reason,
                selection_context=selection_context,
            )
        if decision.decision in {"long", "short"}:
            unresolved_guard = get_unresolved_submission_guard(
                self.settings_row,
                symbol=symbol,
                action=decision.decision,
            )
            if unresolved_guard is not None and bool(unresolved_guard.get("guard_active", True)):
                reason_code = str(unresolved_guard.get("guard_reason_code") or "UNRESOLVED_SUBMISSION_GUARD_ACTIVE")
                blocked_risk_result = RiskCheckResult(
                    allowed=False,
                    decision=decision.decision,
                    reason_codes=[reason_code],
                    blocked_reason_codes=[reason_code],
                    approved_risk_pct=0.0,
                    approved_leverage=0.0,
                    operating_mode="hold",
                    effective_leverage_cap=get_symbol_leverage_cap(symbol),
                    symbol_risk_tier=get_symbol_risk_tier(symbol),
                    exposure_metrics={},
                    cycle_id=cycle_id,
                    snapshot_id=market_row.id,
                )
                record_audit_event(
                    self.session,
                    event_type="risk_check_skipped",
                    entity_type="decision_run",
                    entity_id=str(decision_run.id),
                    severity="warning",
                    message="Risk and execution were skipped because unresolved submission guard is active.",
                    payload={
                        "symbol": symbol,
                        "decision": decision.decision,
                        "reason_code": reason_code,
                        "unresolved_submission_guard": unresolved_guard,
                    },
                    correlation_ids=decision_correlation_ids,
                )
                execution_result = {
                    "status": "blocked",
                    "reason_codes": [reason_code],
                    "decision": decision.decision,
                    "unresolved_submission_guard": unresolved_guard,
                }
                chief_review, chief_provider_name, chief_metadata = self.chief_review_agent.run(
                    decision=decision,
                    risk_result=blocked_risk_result,
                    health_events=self._latest_health_events(),
                    alerts=self._latest_alerts(),
                    use_ai=False,
                )
                chief_run = persist_agent_run(
                    self.session,
                    AgentRole.CHIEF_REVIEW,
                    TriggerEvent.POST_DECISION.value,
                    {
                        "decision": decision.model_dump(mode="json"),
                        "risk_result": blocked_risk_result.model_dump(mode="json"),
                        "alerts": [alert.payload for alert in self._latest_alerts()],
                    },
                    chief_review,
                    provider_name=chief_provider_name,
                    metadata_json=chief_metadata,
                )
                self._record_decision_funnel_audit(
                    cycle_id=cycle_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    regime=feature_payload.regime.primary_regime,
                    stage="pre_risk_guard",
                    status="blocked",
                    strategy_candidate=self._strategy_candidate_funnel_payload(effective_selection_context),
                    decision=decision,
                    decision_run_id=decision_run.id,
                    provider_name=provider_name,
                    decision_metadata=decision_metadata,
                    risk_result=blocked_risk_result,
                    entry_plan_status="not_created",
                    m1_confirmation_status="not_checked",
                    final_risk_status="blocked",
                    blocked_reason_codes=[reason_code],
                    detail={"unresolved_submission_guard": unresolved_guard},
                    severity="warning",
                    correlation_ids=decision_correlation_ids,
                )
                return {
                    "symbol": symbol,
                    "cycle_id": cycle_id,
                    "market_snapshot_id": market_row.id,
                    "feature_snapshot_id": feature_row.id,
                    "decision_run_id": decision_run.id,
                    "risk_check_id": None,
                    "chief_review_run_id": chief_run.id,
                    "decision": decision.model_dump(mode="json"),
                    "risk_result": blocked_risk_result.model_dump(mode="json"),
                    "execution": execution_result,
                    "entry_plan": None,
                    "canceled_entry_plans": [],
                    "status": "blocked_pre_risk",
                    "cadence": cadence_profile,
                    "skip_reason": str(cadence_profile.get("skip_reason") or reason_code),
                    "ai_skipped_reason": ai_skipped_reason,
                    "last_ai_trigger_reason": decision_metadata.get("last_ai_trigger_reason"),
                    "last_ai_invoked_at": decision_metadata.get("last_ai_invoked_at"),
                    "next_ai_review_due_at": decision_metadata.get("next_ai_review_due_at"),
                    "trigger_deduped": bool(decision_metadata.get("trigger_deduped", False)),
                    "trigger_fingerprint": decision_metadata.get("trigger_fingerprint"),
                    "fingerprint_changed_fields": decision_metadata.get("fingerprint_changed_fields"),
                    "dedupe_reason": decision_metadata.get("dedupe_reason"),
                    "last_material_review_at": decision_metadata.get("last_material_review_at"),
                    "forced_review_reason": decision_metadata.get("forced_review_reason"),
                    "last_ai_skip_reason": decision_metadata.get("last_ai_skip_reason"),
                    "applied_review_cadence_minutes": decision_metadata.get("applied_review_cadence_minutes"),
                    "review_cadence_source": decision_metadata.get("review_cadence_source"),
                    "holding_profile_cadence_hint": decision_metadata.get("holding_profile_cadence_hint"),
                    "cadence_fallback_reason": decision_metadata.get("cadence_fallback_reason"),
                    "max_review_age_minutes": decision_metadata.get("max_review_age_minutes"),
                    "cadence_profile_summary": decision_metadata.get("cadence_profile_summary"),
                    "decision_reference": decision_reference,
                    "logic_variant": logic_variant,
                    "account": account_snapshot_to_dict(get_latest_pnl_snapshot(self.session, self.settings_row)),
                    "settings": serialize_settings(self.settings_row),
                    "auto_resume": auto_resume_result,
                    "exchange_sync": exchange_sync_result,
                }
        self._refresh_runtime_state_before_risk()
        risk_decision_context = {
            "trade_performance_tags": decision_metadata.get("trade_performance_tags"),
            "decision_agreement": decision_metadata.get("decision_agreement"),
            "suppression_context": decision_metadata.get("suppression_context"),
            "setup_cluster_state": decision_metadata.get("setup_cluster_state"),
            "meta_gate": decision_metadata.get("meta_gate"),
            "selection_context": effective_selection_context or None,
            "slot_allocation": decision_metadata.get("slot_allocation"),
            "holding_profile_context": decision_metadata.get("holding_profile_context"),
            "current_market_state": _current_market_state_payload(
                feature_payload=feature_payload,
                ai_context_payload=ai_context_payload,
            ),
            "lead_market_context": _as_dict(ai_context_payload.get("lead_lag_summary")),
            "event_risk_context": _as_dict(ai_context_payload.get("event_risk_context")),
        }
        risk_result, risk_row = evaluate_risk(
            self.session,
            self.settings_row,
            decision,
            market_snapshot,
            decision_run_id=decision_run.id,
            market_snapshot_id=market_row.id,
            execution_mode="historical_replay" if trigger_event == "historical_replay" else "live",
            decision_context=risk_decision_context,
        )
        risk_correlation_ids = normalize_correlation_ids(
            decision_correlation_ids,
            risk_id=risk_row.id,
        )
        record_audit_event(
            self.session,
            event_type="risk_check",
            entity_type="risk_check",
            entity_id=str(risk_row.id),
            severity="warning" if not risk_result.allowed else "info",
            message="Risk check completed.",
            payload=risk_result.model_dump(mode="json"),
            correlation_ids=risk_correlation_ids,
        )
        refresh_decision_performance_fact_usefulness(self.session, decision_run.id)
        entry_plan_decision = decision
        entry_plan_risk_result = risk_result
        entry_plan_risk_row = risk_row
        if watch_entry_plan_decision is not None:
            self._refresh_runtime_state_before_risk()
            watch_risk_context = {
                **risk_decision_context,
                "watch_entry_plan": decision.watch_entry_plan.model_dump(mode="json")
                if decision.watch_entry_plan is not None
                else None,
                "watch_entry_plan_source_decision": decision.decision,
            }
            watch_risk_result, watch_risk_row = evaluate_risk(
                self.session,
                self.settings_row,
                watch_entry_plan_decision,
                market_snapshot,
                decision_run_id=decision_run.id,
                market_snapshot_id=market_row.id,
                execution_mode="live",
                decision_context=watch_risk_context,
            )
            watch_risk_correlation_ids = normalize_correlation_ids(
                decision_correlation_ids,
                risk_id=watch_risk_row.id,
            )
            record_audit_event(
                self.session,
                event_type="risk_check",
                entity_type="risk_check",
                entity_id=str(watch_risk_row.id),
                severity="warning" if not watch_risk_result.allowed else "info",
                message="Risk check completed for AI watch entry plan.",
                payload={
                    **watch_risk_result.model_dump(mode="json"),
                    "watch_entry_plan": True,
                    "source_decision": decision.decision,
                },
                correlation_ids=watch_risk_correlation_ids,
            )
            refresh_decision_performance_fact_usefulness(self.session, decision_run.id)
            if str(decision_metadata.get("source") or "") == "llm":
                watch_risk_event = (
                    "AI_DECISION_APPROVED_BY_RISK"
                    if watch_risk_result.allowed
                    else "AI_DECISION_BLOCKED_BY_RISK"
                )
                record_audit_event(
                    self.session,
                    event_type=(
                        "decision_risk_approved"
                        if watch_risk_result.allowed
                        else "decision_risk_blocked"
                    ),
                    entity_type="decision_run",
                    entity_id=str(decision_run.id),
                    severity="info" if watch_risk_result.allowed else "warning",
                    message="AI watch entry plan was evaluated by deterministic risk policy.",
                    payload={
                        "ai_call_event": watch_risk_event,
                        "symbol": symbol,
                        "decision": watch_entry_plan_decision.decision,
                        "intent": "watch_entry_plan",
                        "risk_allowed": watch_risk_result.allowed,
                        "risk_check_id": watch_risk_row.id,
                        "reason_codes": list(watch_risk_result.reason_codes or []),
                        "blocked_reason_codes": list(
                            getattr(watch_risk_result, "blocked_reason_codes", []) or []
                        ),
                        "approved_risk_pct": watch_risk_result.approved_risk_pct,
                        "approved_leverage": watch_risk_result.approved_leverage,
                        "scope": ai_call_policy.get("scope"),
                        "snapshot_id": market_row.id,
                    },
                    correlation_ids=watch_risk_correlation_ids,
                )
            entry_plan_decision = watch_entry_plan_decision
            entry_plan_risk_result = watch_risk_result
            entry_plan_risk_row = watch_risk_row
        if str(decision_metadata.get("source") or "") == "llm" and decision.decision != "hold":
            risk_event = (
                "AI_DECISION_APPROVED_BY_RISK"
                if risk_result.allowed
                else "AI_DECISION_BLOCKED_BY_RISK"
            )
            record_audit_event(
                self.session,
                event_type=(
                    "decision_risk_approved"
                    if risk_result.allowed
                    else "decision_risk_blocked"
                ),
                entity_type="decision_run",
                entity_id=str(decision_run.id),
                severity="info" if risk_result.allowed else "warning",
                message="AI trade intent was evaluated by deterministic risk policy.",
                payload={
                    "ai_call_event": risk_event,
                    "symbol": symbol,
                    "decision": decision.decision,
                    "risk_allowed": risk_result.allowed,
                    "risk_check_id": risk_row.id,
                    "reason_codes": list(risk_result.reason_codes or []),
                    "blocked_reason_codes": list(getattr(risk_result, "blocked_reason_codes", []) or []),
                    "approved_risk_pct": risk_result.approved_risk_pct,
                    "approved_leverage": risk_result.approved_leverage,
                    "scope": ai_call_policy.get("scope"),
                    "snapshot_id": market_row.id,
                },
                correlation_ids=risk_correlation_ids,
            )
        if not open_positions and not risk_result.allowed:
            self._record_risk_skip_event(
                symbol=symbol,
                timeframe=timeframe,
                market_row=market_row,
                market_snapshot=market_snapshot,
                decision_run=decision_run,
                decision=decision,
                risk_row=risk_row,
                risk_result=risk_result,
                selection_context=selection_context,
            )

        canceled_entry_plans: list[dict[str, object]] = []
        armed_entry_plan: PendingEntryPlanSnapshot | None = None
        if trigger_event != "historical_replay" and decision.decision in {"hold", "long", "short"}:
            canceled_entry_plans = [
                snapshot.model_dump(mode="json")
                for snapshot in self._cancel_symbol_entry_plans_from_decision(
                    symbol=symbol,
                    decision=decision,
                    decision_run_id=decision_run.id,
                    cycle_id=cycle_id,
                    snapshot_id=market_row.id,
                )
            ]
        if (
            trigger_event != "historical_replay"
            and not open_positions
            and self._plan_entry_allowed_without_trigger(
                entry_plan_decision,
                entry_plan_risk_result,
                watch_entry_plan=watch_entry_plan_decision is not None,
            )
        ):
            armed_entry_plan = self._pending_entry_plan_snapshot(
                self._arm_pending_entry_plan(
                    decision=entry_plan_decision,
                    decision_run=decision_run,
                    risk_result=entry_plan_risk_result,
                    risk_row_id=entry_plan_risk_row.id,
                    feature_payload=feature_payload,
                    cycle_id=cycle_id,
                    snapshot_id=market_row.id,
                )
            )

        execution_result: dict[str, object] | None = None
        if (
            armed_entry_plan is None
            and risk_result.allowed
            and decision.decision != "hold"
            and not transition_watch_review
            and self._should_execute_live(trigger_event)
        ):
            execution_result = execute_live_trade(
                self.session,
                self.settings_row,
                decision_run_id=decision_run.id,
                decision=decision,
                market_snapshot=market_snapshot,
                risk_result=risk_result,
                risk_row=risk_row,
                cycle_id=cycle_id,
                snapshot_id=market_row.id,
            )
            refresh_decision_performance_fact_usefulness(self.session, decision_run.id)
        elif (
            armed_entry_plan is None
            and risk_result.allowed
            and decision.decision != "hold"
            and not transition_watch_review
        ):
            record_audit_event(
                self.session,
                event_type="live_execution_skipped",
                entity_type="decision_run",
                entity_id=str(decision_run.id),
                severity="info",
                message="Live execution skipped for non-live trigger.",
                payload={"trigger_event": trigger_event, "symbol": symbol},
            )
        elif armed_entry_plan is None and not risk_result.allowed and _should_create_trade_blocked_alert(
            decision.decision,
            risk_result.reason_codes,
        ):
            create_alert(
                self.session,
                category="risk",
                severity="warning",
                title="Trade blocked",
                message="Deterministic risk policy blocked the execution.",
                payload={"reason_codes": risk_result.reason_codes, "decision": decision.decision, "symbol": symbol},
            )

        execution_order_status = self._execution_order_status(execution_result)
        execution_terminal_blocked = execution_order_status in {
            "blocked",
            "rejected",
            "error",
            "submission_unknown",
        }
        if armed_entry_plan is not None:
            funnel_status = "entry_plan_armed"
            funnel_stage = "entry_plan_waiting"
            funnel_entry_plan_status = str(armed_entry_plan.plan_status or "armed")
            funnel_final_risk_status = "not_checked"
            funnel_blocked_reason_codes = self._funnel_reason_codes(
                getattr(entry_plan_risk_result, "blocked_reason_codes", []),
                getattr(entry_plan_risk_result, "reason_codes", []),
            )
            funnel_severity = "info"
        elif decision.decision == "hold":
            funnel_status = "hold"
            funnel_stage = "ai_hold" if risk_result.allowed is False else "decision_hold"
            funnel_entry_plan_status = "not_created"
            funnel_final_risk_status = "blocked" if not risk_result.allowed else "not_applicable"
            funnel_blocked_reason_codes = self._funnel_reason_codes(
                risk_result.blocked_reason_codes,
                risk_result.reason_codes,
            )
            funnel_severity = "info"
        elif not risk_result.allowed:
            funnel_status = "risk_blocked"
            funnel_stage = "risk_guard"
            funnel_entry_plan_status = "not_created"
            funnel_final_risk_status = "blocked"
            funnel_blocked_reason_codes = self._funnel_reason_codes(
                risk_result.blocked_reason_codes,
                risk_result.reason_codes,
            )
            funnel_severity = "warning"
        elif execution_result is not None:
            funnel_status = "order_blocked" if execution_terminal_blocked else "order_submitted"
            funnel_stage = "order_execution"
            funnel_entry_plan_status = "not_applicable"
            funnel_final_risk_status = "approved"
            funnel_blocked_reason_codes = self._funnel_reason_codes(execution_result.get("reason_codes"))
            funnel_severity = "warning" if execution_terminal_blocked else "info"
        else:
            funnel_status = "execution_skipped"
            funnel_stage = "execution_policy"
            funnel_entry_plan_status = "not_applicable"
            funnel_final_risk_status = "approved" if risk_result.allowed else "not_checked"
            funnel_blocked_reason_codes = []
            funnel_severity = "info"
        self._record_decision_funnel_audit(
            cycle_id=cycle_id,
            symbol=symbol,
            timeframe=timeframe,
            regime=feature_payload.regime.primary_regime,
            stage=funnel_stage,
            status=funnel_status,
            strategy_candidate=self._strategy_candidate_funnel_payload(effective_selection_context),
            decision=decision,
            decision_run_id=decision_run.id,
            provider_name=provider_name,
            decision_metadata=decision_metadata,
            risk_result=risk_result,
            risk_row_id=risk_row.id,
            entry_plan_status=funnel_entry_plan_status,
            m1_confirmation_status="waiting" if armed_entry_plan is not None else "not_checked",
            final_risk_status=funnel_final_risk_status,
            execution_result=execution_result,
            blocked_reason_codes=funnel_blocked_reason_codes,
            detail={
                "trigger_event": trigger_event,
                "entry_plan_id": armed_entry_plan.plan_id if armed_entry_plan is not None else None,
                "canceled_entry_plan_count": len(canceled_entry_plans),
            },
            severity=funnel_severity,
            correlation_ids=risk_correlation_ids,
            ai_skipped_reason=ai_skipped_reason,
        )

        chief_review, chief_provider_name, chief_metadata = self.chief_review_agent.run(
            decision=decision,
            risk_result=risk_result,
            health_events=self._latest_health_events(),
            alerts=self._latest_alerts(),
            use_ai=False,
        )
        chief_run = persist_agent_run(
            self.session,
            AgentRole.CHIEF_REVIEW,
            TriggerEvent.POST_DECISION.value,
            {"decision": decision.model_dump(mode="json"), "risk_result": risk_result.model_dump(mode="json"), "alerts": [alert.payload for alert in self._latest_alerts()]},
            chief_review,
            provider_name=chief_provider_name,
            metadata_json=chief_metadata,
        )
        record_audit_event(
            self.session,
            event_type="decision_cycle_completed",
            entity_type="decision_run",
            entity_id=str(decision_run.id),
            severity="info",
            message="Decision cycle completed.",
            payload={
                "symbol": symbol,
                "trigger_event": trigger_event,
                "decision": decision.decision,
                "status": "entry_plan_armed" if armed_entry_plan is not None else "completed",
                "risk_allowed": risk_result.allowed,
                "risk_reason_codes": list(risk_result.reason_codes or []),
                "ai_skipped_reason": ai_skipped_reason,
                **active_position_suppression_payload,
            },
            correlation_ids=decision_correlation_ids,
        )
        self.session.flush()
        return {
            "symbol": symbol,
            "cycle_id": cycle_id,
            "market_snapshot_id": market_row.id,
            "feature_snapshot_id": feature_row.id,
            "decision_run_id": decision_run.id,
            "risk_check_id": risk_row.id,
            "chief_review_run_id": chief_run.id,
            "decision": decision.model_dump(mode="json"),
            "risk_result": risk_result.model_dump(mode="json"),
            "execution": execution_result,
            "entry_plan": armed_entry_plan.model_dump(mode="json") if armed_entry_plan is not None else None,
            "canceled_entry_plans": canceled_entry_plans,
            "status": "entry_plan_armed" if armed_entry_plan is not None else "completed",
            "cadence": cadence_profile,
            "skip_reason": str(cadence_profile.get("skip_reason") or "") or None,
            "ai_skipped_reason": ai_skipped_reason,
            "last_ai_trigger_reason": decision_metadata.get("last_ai_trigger_reason"),
            "last_ai_invoked_at": decision_metadata.get("last_ai_invoked_at"),
            "next_ai_review_due_at": decision_metadata.get("next_ai_review_due_at"),
            "trigger_deduped": bool(decision_metadata.get("trigger_deduped", False)),
            "trigger_fingerprint": decision_metadata.get("trigger_fingerprint"),
            "fingerprint_changed_fields": decision_metadata.get("fingerprint_changed_fields"),
            "dedupe_reason": decision_metadata.get("dedupe_reason"),
            "last_material_review_at": decision_metadata.get("last_material_review_at"),
            "forced_review_reason": decision_metadata.get("forced_review_reason"),
            "last_ai_skip_reason": decision_metadata.get("last_ai_skip_reason"),
            "applied_review_cadence_minutes": decision_metadata.get("applied_review_cadence_minutes"),
            "review_cadence_source": decision_metadata.get("review_cadence_source"),
            "holding_profile_cadence_hint": decision_metadata.get("holding_profile_cadence_hint"),
            "cadence_fallback_reason": decision_metadata.get("cadence_fallback_reason"),
            "max_review_age_minutes": decision_metadata.get("max_review_age_minutes"),
            "cadence_profile_summary": decision_metadata.get("cadence_profile_summary"),
            "decision_reference": decision_reference,
            "market_settings_advisor": market_settings_advisor_result,
            "logic_variant": logic_variant,
            "account": account_snapshot_to_dict(get_latest_pnl_snapshot(self.session, self.settings_row)),
            "settings": serialize_settings(self.settings_row),
            "auto_resume": auto_resume_result,
            "exchange_sync": exchange_sync_result,
        }


    def run_selected_symbols_cycle(
        self,
        *,
        symbols: list[str] | None = None,
        trigger_event: str = TriggerEvent.MANUAL.value,
        timeframe: str | None = None,
        upto_index: int | None = None,
        force_stale: bool = False,
        auto_resume_checked: bool = False,
        logic_variant: str = "improved",
    ) -> dict[str, object]:
        auto_resume_result = self._ensure_auto_resume(
            trigger_event=trigger_event,
            auto_resume_checked=auto_resume_checked,
        )
        selected_symbols = [item.upper() for item in symbols] if symbols else get_effective_symbols(self.settings_row)
        decision_symbols = [
            effective.symbol
            for effective in get_effective_symbol_schedule(self.settings_row)
            if effective.enabled and effective.symbol in selected_symbols
        ]
        results: list[dict[str, object]] = []
        failed_symbols: list[str] = []
        event_context_cycle_owner = self._begin_event_context_cycle(
            f"selected-symbols:{trigger_event}:{timeframe or 'effective'}:{utcnow_naive().isoformat()}"
        )
        try:
            candidate_selection = self._rank_candidate_symbols(
                decision_symbols=decision_symbols,
                timeframe=timeframe,
                upto_index=upto_index,
                force_stale=force_stale,
            )
            selected_cycle_symbols = [
                str(item).upper()
                for item in candidate_selection.get("selected_symbols", decision_symbols)
                if item
            ] or decision_symbols
            for symbol in selected_cycle_symbols:
                try:
                    results.append(
                        self.run_decision_cycle(
                            symbol=symbol,
                            timeframe=timeframe,
                            trigger_event=trigger_event,
                            upto_index=upto_index,
                            force_stale=force_stale,
                            auto_resume_checked=True,
                            logic_variant=logic_variant,
                            exchange_sync_checked=True,
                            selection_context=self._selection_context_from_candidate_selection(
                                symbol=symbol,
                                candidate_selection=candidate_selection,
                            ),
                        )
                    )
                except Exception as exc:
                    failed_symbols.append(symbol)
                    record_audit_event(
                        self.session,
                        event_type="decision_cycle_failed",
                        entity_type="symbol",
                        entity_id=symbol,
                        severity="error",
                        message="Decision cycle failed for tracked symbol.",
                        payload={"trigger_event": trigger_event, "error": str(exc)},
                    )
                    record_health_event(
                        self.session,
                        component="decision_cycle",
                        status="error",
                        message="Tracked symbol decision cycle failed.",
                        payload={"symbol": symbol, "trigger_event": trigger_event, "error": str(exc)},
                    )
                    results.append(
                        {
                            "symbol": symbol,
                            "status": "failed",
                            "error": str(exc),
                        }
                    )
        finally:
            self._end_event_context_cycle(event_context_cycle_owner)
        return {
            "symbols": selected_cycle_symbols,
            "tracked_symbols": decision_symbols,
            "cycles": len(results),
            "mode": "market_data_only" if not self.settings_row.ai_enabled else "ai_active",
            "failed_symbols": failed_symbols,
            "logic_variant": logic_variant,
            "candidate_selection": candidate_selection,
            "results": results,
            "account": self._account_snapshot_preview() if not self.settings_row.ai_enabled else account_snapshot_to_dict(get_latest_pnl_snapshot(self.session, self.settings_row)),
            "settings": serialize_settings(self.settings_row),
            "auto_resume": auto_resume_result,
            "exchange_sync": None,
        }
