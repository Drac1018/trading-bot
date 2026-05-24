from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from trading_mvp.models import Alert, AuditEvent, SystemHealthEvent

SENSITIVE_REDACTED_VALUE = "[REDACTED]"
SENSITIVE_KEY_EXACT_MATCHES = frozenset(
    {
        "apikey",
        "apisecret",
        "api_key",
        "api_secret",
        "authorization",
        "accesstoken",
        "auth_token",
        "bearer_token",
        "clientsecret",
        "idtoken",
        "listenkey",
        "listen_key",
        "password",
        "refresh_token",
        "refreshtoken",
        "secret",
        "sessiontoken",
        "session_token",
        "token",
    }
)
SENSITIVE_KEY_SUFFIXES = (
    "_api_key",
    "_api_secret",
    "_auth_token",
    "_password",
    "_refresh_token",
    "_secret",
    "_session_token",
    "_token",
)
CORRELATION_ID_FIELDS = (
    "cycle_id",
    "snapshot_id",
    "decision_id",
    "risk_id",
    "execution_id",
)
APPROVAL_TIMELINE_KEYS = (
    "approval_state",
    "approval_armed",
    "approval_window_open",
    "approval_expires_at",
    "armed_until",
    "approval_window_minutes",
    "live_execution_ready",
    "can_enter_new_position",
    "trigger_source",
)
EVENT_OPERATOR_CONTROL_TIMELINE_KEYS = (
    "actor",
    "symbols",
    "window_id",
    "scope",
    "before",
    "after",
    "evaluations",
    "evaluated_at",
)
EVENT_POLICY_TIMELINE_KEYS = (
    "symbol",
    "decision",
    "blocked_reason",
    "approval_required_reason",
    "degraded_reason",
    "policy_source",
    "survival_path",
)
EXPECTED_EDGE_TIMELINE_KEYS = (
    "symbol",
    "decision",
    "timeframe",
    "mode",
    "gate_enabled",
    "gate_shadow",
    "blocking_active",
    "would_block",
    "would_block_reason_codes",
    "enforced_reason_codes",
    "expected_profit_bps",
    "expected_loss_bps",
    "expected_fee_bps",
    "expected_slippage_bps",
    "spread_cost_bps",
    "expected_total_cost_bps",
    "net_expected_edge_bps",
    "cost_to_edge_ratio",
    "rr_after_estimated_cost",
    "entry_execution_type",
    "required_order_policy",
    "risk_check_id",
)
PORTFOLIO_EXPOSURE_TIMELINE_KEYS = (
    "symbol",
    "decision",
    "timeframe",
    "status",
    "reason_code",
    "reason_codes",
    "blocked_reason_codes",
    "total_notional_exposure",
    "total_margin_exposure",
    "current_notional_exposure",
    "candidate_notional_exposure",
    "long_exposure",
    "short_exposure",
    "directional_bias",
    "directional_bias_pct",
    "max_single_position_exposure",
    "max_single_position_exposure_pct",
    "same_tier_concentration",
    "same_tier_concentration_notional",
    "correlated_symbol_exposure",
    "correlated_symbol_exposure_pct",
    "combined_BTC_ETH_directional_exposure",
    "combined_BTC_ETH_directional_exposure_pct",
    "candidate_direction",
    "candidate_symbol",
    "risk_check_id",
)
PLAN_CONFIRMATION_TIMELINE_KEYS = (
    "plan_id",
    "symbol",
    "strategy_id",
    "regime_id",
    "entry_zone_low",
    "entry_zone_high",
    "zone_touched",
    "m1_close_reclaim",
    "structure_break",
    "wick_body_condition",
    "chase_distance_bps",
    "RR_before_confirmation",
    "RR_after_confirmation",
    "confirmation_passed",
    "confirmation_failed_reason",
    "plan_cancel_reason",
    "plan_status",
    "watch_timeframe",
    "source_timeframe",
    "market_snapshot_id",
    "follow_up_snapshot",
)
EVENT_CONTEXT_TIMELINE_KEYS = (
    "source_status",
    "source_provenance",
    "is_stale",
    "is_complete",
    "next_event_name",
    "next_event_at",
    "minutes_to_next_event",
    "active_risk_window",
)
EVENT_CONTEXT_VISIBILITY_DEFAULTS = {
    "source_status": "unavailable",
    "is_stale": False,
    "next_event_name": None,
    "minutes_to_next_event": None,
    "active_risk_window": False,
}
PROTECTION_TIMELINE_KEYS = (
    "symbol",
    "trigger_source",
    "operating_state",
    "recovery_status",
    "status",
    "position_size",
    "missing_components",
    "created_order_ids",
    "from_state",
    "to_state",
    "transition_reason",
    "last_error",
)
BREAKEVEN_TIMELINE_KEYS = (
    "symbol",
    "status",
    "reason_code",
    "reason_codes",
    "risk_check_id",
    "position_id",
    "position_side",
    "entry_price",
    "mark_price",
    "current_stop_loss",
    "candidate_stop_loss",
    "new_stop_loss",
    "current_r_multiple",
    "trigger_r",
    "breakeven_lock_bps",
    "breakeven_min_hold_seconds",
    "shadow",
    "would_move",
    "approved",
    "cancelled_order_ids",
    "new_order_id",
    "error",
)
RANGE_MR_COOLDOWN_TIMELINE_KEYS = (
    "strategy_id",
    "symbol",
    "direction",
    "timeframe",
    "regime_id",
    "range_id",
    "status",
    "reason_code",
    "reason_codes",
    "blocked_reason_codes",
    "consecutive_failures",
    "max_consecutive_failures",
    "cooldown_minutes",
    "breakout_cooldown_minutes",
    "cooldown_until",
    "cooldown_reason",
    "last_failure_at",
    "last_breakout_at",
    "range_breakout_direction",
    "position_id",
    "net_r_multiple",
    "net_realized_pnl",
    "result",
    "risk_check_id",
)
EXECUTION_TIMELINE_KEYS = (
    "symbol",
    "status",
    "order_status",
    "order_type",
    "submission_state",
    "requested_quantity",
    "filled_quantity",
    "fill_price",
    "average_fill_price",
    "fees",
    "realized_pnl",
    "reason_codes",
)
AI_DECISION_TIMELINE_KEYS = (
    "symbol",
    "provider",
    "prompt_family",
    "suppression_active",
    "suppression_reason_code",
    "allow_same_side_add_on",
    "allowed_add_on_side",
    "bounded_output_applied",
    "fallback_reason_codes",
    "fail_closed_applied",
    "engine_prior_classification",
    "capital_efficiency_classification",
    "session_prior_classification",
    "time_of_day_prior_classification",
    "prior_penalty_level",
    "prior_reason_codes",
    "sample_threshold_satisfied",
    "confidence_adjustment_applied",
    "abstain_due_to_prior_and_quality",
    "expected_payoff_efficiency_hint_summary",
    "intent_family",
    "management_action",
    "legacy_semantics_preserved",
    "analytics_excluded_from_entry_stats",
)
AI_DECISION_VALIDITY_TIMELINE_KEYS = (
    "status",
    "symbol",
    "timeframe",
    "decision",
    "reason_code",
    "reason_codes",
    "blocked_reason_codes",
    "generated_at",
    "valid_until",
    "ttl_seconds",
    "market_snapshot_id",
    "current_market_snapshot_id",
    "snapshot_hash",
    "snapshot_time",
    "reference_price",
    "current_price",
    "price_move_pct",
    "price_move_threshold_pct",
    "reference_regime_id",
    "current_regime_id",
    "reference_regime_label",
    "current_regime_label",
    "reference_volatility_pct",
    "current_volatility_pct",
    "range_breakout_direction",
    "reference_range_breakout_direction",
    "sync_untrusted_scopes",
    "risk_check_id",
)
DECISION_FUNNEL_TIMELINE_KEYS = (
    "stage",
    "status",
    "symbol",
    "timeframe",
    "regime",
    "strategy_candidate",
    "ai_decision",
    "risk_decision",
    "entry_plan_status",
    "m1_confirmation_status",
    "final_risk_status",
    "order_status",
    "blocked_reason",
    "blocked_reason_codes",
    "hold_reason",
    "hold_reason_codes",
)
AI_MARKET_SETTINGS_ADVISOR_TIMELINE_KEYS = (
    "recommendation_id",
    "generated_at",
    "valid_until",
    "symbol_scope",
    "recommended_profile_id",
    "confidence",
    "reason_summary",
    "reason_codes",
    "observed_risk_flags",
    "suggested_new_entry_policy",
    "do_not_relax",
    "status",
    "ignored_reason",
    "ignored_reason_code",
    "ignored_reason_codes",
    "shadow",
    "would_apply",
    "min_confidence_to_apply",
    "provider",
    "agent_run_id",
)
SAFE_PROFILE_SELECTOR_TIMELINE_KEYS = (
    "previous_profile",
    "deterministic_profile",
    "ai_recommended_profile",
    "final_active_profile",
    "shadow_final_profile",
    "selection_mode",
    "selected_reason",
    "ai_recommendation_id",
    "confidence",
    "was_tightened_by_ai",
    "was_relaxation_blocked",
    "relaxation_block_reason_codes",
    "ignored_reason_codes",
    "hard_condition_reason_codes",
    "active_profile_blocks_new_entry",
    "blocking_active",
    "risk_check_id",
)


def _compact_payload_dict(
    payload: dict[str, Any] | None,
    *,
    allowed_keys: tuple[str, ...],
) -> dict[str, Any]:
    source = payload if isinstance(payload, dict) else {}
    compact: dict[str, Any] = {}
    for key in allowed_keys:
        value = source.get(key)
        if value is None or value == "" or value == [] or value == {}:
            continue
        compact[key] = value
    return compact


def _compact_event_context_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    source = payload if isinstance(payload, dict) else {}
    if not source:
        return {}
    compact = _compact_payload_dict(source, allowed_keys=EVENT_CONTEXT_TIMELINE_KEYS)
    for key, default in EVENT_CONTEXT_VISIBILITY_DEFAULTS.items():
        compact.setdefault(key, source.get(key, default))
    return compact


def compact_audit_payload(
    payload: dict[str, Any] | None,
    *,
    event_type: str | None = None,
    event_category: str | None = None,
) -> dict[str, Any]:
    source = redact_sensitive_payload(dict(payload or {}))
    if not source:
        return {}

    event_key = (event_type or "").strip().lower()
    category_key = (event_category or "").strip().lower()

    if category_key == "approval_control" or event_key.startswith("live_approval_"):
        compact = _compact_payload_dict(source, allowed_keys=APPROVAL_TIMELINE_KEYS)
        approval_detail = _compact_payload_dict(
            source.get("approval_detail"),
            allowed_keys=("approval_grace_until",),
        )
        if approval_detail:
            compact["approval_detail"] = approval_detail
        event_control_detail = _compact_payload_dict(source, allowed_keys=EVENT_OPERATOR_CONTROL_TIMELINE_KEYS)
        if event_control_detail:
            compact.update(event_control_detail)
        event_context = _compact_event_context_payload(source.get("event_context"))
        if event_context:
            compact["event_context"] = event_context
        return compact

    if category_key == "protection" or event_key.startswith("protection_") or "protective" in event_key:
        compact = _compact_payload_dict(source, allowed_keys=PROTECTION_TIMELINE_KEYS)
        protective_state = _compact_payload_dict(
            source.get("protective_state"),
            allowed_keys=(
                "status",
                "missing_components",
                "protective_order_count",
                "has_stop_loss",
                "has_take_profit",
            ),
        )
        if protective_state:
            compact["protective_state"] = protective_state
        protection_lifecycle = _compact_payload_dict(
            source.get("protection_lifecycle"),
            allowed_keys=(
                "state",
                "trigger_source",
                "requested_components",
                "requested_order_types",
                "created_order_ids",
            ),
        )
        if protection_lifecycle:
            compact["protection_lifecycle"] = protection_lifecycle
        verification_detail = _compact_payload_dict(
            source.get("verification_detail"),
            allowed_keys=("error", "verified_order_ids", "expected_order_types"),
        )
        if verification_detail:
            compact["verification_detail"] = verification_detail
        return compact

    if event_key.startswith("breakeven_") or event_key == "moved_stop_to_breakeven":
        compact = _compact_payload_dict(source, allowed_keys=BREAKEVEN_TIMELINE_KEYS)
        risk_guard = _compact_payload_dict(
            source.get("risk_guard"),
            allowed_keys=("allowed", "reason_codes", "blocked_reason_codes"),
        )
        if risk_guard:
            compact["risk_guard"] = risk_guard
        protective_state = _compact_payload_dict(
            source.get("protective_state"),
            allowed_keys=("status", "missing_components", "has_stop_loss", "has_take_profit"),
        )
        if protective_state:
            compact["protective_state"] = protective_state
        return compact

    if event_key.startswith("range_mean_reversion_cooldown_") or event_key == "range_mean_reversion_trade_result_recorded":
        return _compact_payload_dict(source, allowed_keys=RANGE_MR_COOLDOWN_TIMELINE_KEYS)

    if category_key == "execution" or event_key.startswith("live_execution"):
        return _compact_payload_dict(source, allowed_keys=EXECUTION_TIMELINE_KEYS)

    if event_key == "risk_expected_edge_gate":
        compact = _compact_payload_dict(source, allowed_keys=EXPECTED_EDGE_TIMELINE_KEYS)
        thresholds = _compact_payload_dict(
            source.get("thresholds"),
            allowed_keys=(
                "min_net_expected_edge_bps",
                "max_cost_to_edge_ratio",
                "settings_slippage_threshold_bps",
            ),
        )
        if thresholds:
            compact["thresholds"] = thresholds
        return compact

    if event_key in {
        "portfolio_exposure_evaluated",
        "portfolio_exposure_blocked",
        "correlated_exposure_blocked",
        "directional_bias_blocked",
    }:
        compact = _compact_payload_dict(source, allowed_keys=PORTFOLIO_EXPOSURE_TIMELINE_KEYS)
        limits = _compact_payload_dict(
            source.get("limits"),
            allowed_keys=(
                "gross_exposure_pct",
                "directional_bias_pct",
                "same_tier_concentration_pct",
                "max_same_direction_major_exposure_pct",
            ),
        )
        if limits:
            compact["limits"] = limits
        return compact

    if event_key == "pending_entry_plan_confirmation_evaluated":
        return _compact_payload_dict(source, allowed_keys=PLAN_CONFIRMATION_TIMELINE_KEYS)

    if category_key == "risk" or event_key.startswith("event_policy_"):
        compact = _compact_payload_dict(source, allowed_keys=EVENT_POLICY_TIMELINE_KEYS)
        evaluated_operator_policy = _compact_payload_dict(
            source.get("evaluated_operator_policy"),
            allowed_keys=(
                "operator_view_active",
                "matched_window_id",
                "alignment_status",
                "enforcement_mode",
                "reason_codes",
                "effective_policy_preview",
                "event_source_status",
                "event_source_stale",
                "evaluated_at",
            ),
        )
        if evaluated_operator_policy:
            compact["evaluated_operator_policy"] = evaluated_operator_policy
        event_context = _compact_event_context_payload(source.get("event_context"))
        if event_context:
            compact["event_context"] = event_context
        if isinstance(source.get("manual_no_trade_windows"), list):
            window_ids = [
                str(item.get("window_id"))
                for item in source.get("manual_no_trade_windows", [])
                if isinstance(item, dict) and item.get("window_id") not in {None, ""}
            ]
            if window_ids:
                compact["window_ids"] = window_ids
        return compact

    if category_key == "ai_decision":
        if event_key in {"ai_decision_expired", "ai_decision_invalidated"}:
            return _compact_payload_dict(source, allowed_keys=AI_DECISION_VALIDITY_TIMELINE_KEYS)

        if event_key == "decision_funnel_audit":
            return _compact_payload_dict(source, allowed_keys=DECISION_FUNNEL_TIMELINE_KEYS)

        compact = _compact_payload_dict(source, allowed_keys=AI_DECISION_TIMELINE_KEYS)
        decision_payload = _compact_payload_dict(
            source.get("decision"),
            allowed_keys=(
                "symbol",
                "timeframe",
                "decision",
                "confidence",
                "rationale_codes",
                "intent_family",
                "management_action",
                "legacy_semantics_preserved",
                "analytics_excluded_from_entry_stats",
            ),
        )
        if decision_payload:
            compact["decision"] = decision_payload
        trigger_payload = _compact_payload_dict(
            source.get("trigger"),
            allowed_keys=("trigger_reason", "symbol", "timeframe", "trigger_fingerprint"),
        )
        if trigger_payload:
            compact["trigger"] = trigger_payload
        return compact

    if category_key == "ai_market_settings_advisor" or event_key.startswith("ai_market_settings_"):
        return _compact_payload_dict(source, allowed_keys=AI_MARKET_SETTINGS_ADVISOR_TIMELINE_KEYS)

    if event_key == "execution_risk_profile_selected":
        return _compact_payload_dict(source, allowed_keys=SAFE_PROFILE_SELECTOR_TIMELINE_KEYS)

    return {}


def _normalized_payload_key(key: object) -> str:
    raw = str(key or "").strip().replace("-", "_").replace(" ", "_")
    return re.sub(r"(?<!^)(?=[A-Z])", "_", raw).lower()


def payload_key_is_sensitive(key: object) -> bool:
    normalized = _normalized_payload_key(key)
    compact = normalized.replace("_", "")
    return (
        normalized in SENSITIVE_KEY_EXACT_MATCHES
        or compact in SENSITIVE_KEY_EXACT_MATCHES
        or normalized.endswith(SENSITIVE_KEY_SUFFIXES)
    )


def redact_sensitive_payload(value: Any, *, _depth: int = 0) -> Any:
    if _depth > 24:
        return value
    if isinstance(value, dict):
        return {
            key: (
                SENSITIVE_REDACTED_VALUE
                if payload_key_is_sensitive(key)
                else redact_sensitive_payload(nested, _depth=_depth + 1)
            )
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_payload(item, _depth=_depth + 1) for item in value]
    if isinstance(value, tuple):
        return [redact_sensitive_payload(item, _depth=_depth + 1) for item in value]
    return value


def normalize_correlation_ids(
    correlation_ids: dict[str, Any] | None = None,
    *,
    cycle_id: str | None = None,
    snapshot_id: int | str | None = None,
    decision_id: int | str | None = None,
    risk_id: int | str | None = None,
    execution_id: int | str | None = None,
) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    base_payload = correlation_ids if isinstance(correlation_ids, dict) else {}
    for key in CORRELATION_ID_FIELDS:
        value = base_payload.get(key)
        if value is not None and value != "":
            normalized[key] = value
    overrides = {
        "cycle_id": cycle_id,
        "snapshot_id": snapshot_id,
        "decision_id": decision_id,
        "risk_id": risk_id,
        "execution_id": execution_id,
    }
    for key, value in overrides.items():
        if value is not None and value != "":
            normalized[key] = value
    return normalized


def merge_correlation_payload(
    payload: dict[str, Any] | None = None,
    *,
    correlation_ids: dict[str, Any] | None = None,
) -> dict[str, Any]:
    merged_payload = dict(payload or {})
    normalized = normalize_correlation_ids(correlation_ids)
    if normalized:
        merged_payload.update(normalized)
    return redact_sensitive_payload(merged_payload)


def record_audit_event(
    session: Session,
    event_type: str,
    entity_type: str,
    entity_id: str,
    message: str,
    severity: str = "info",
    payload: dict[str, Any] | None = None,
    correlation_ids: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        message=message,
        severity=severity,
        payload=merge_correlation_payload(payload, correlation_ids=correlation_ids),
    )
    session.add(event)
    return event


def _first_non_empty_string(values: list[Any]) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def record_decision_funnel_event(
    session: Session,
    *,
    cycle_id: str,
    symbol: str,
    stage: str,
    status: str,
    timeframe: str | None = None,
    regime: str | None = None,
    strategy_candidate: dict[str, Any] | None = None,
    ai_decision: dict[str, Any] | None = None,
    risk_decision: dict[str, Any] | None = None,
    entry_plan_status: str | None = None,
    m1_confirmation_status: str | None = None,
    final_risk_status: str | None = None,
    order_status: str | None = None,
    blocked_reason: str | None = None,
    blocked_reason_codes: list[str] | None = None,
    hold_reason: str | None = None,
    hold_reason_codes: list[str] | None = None,
    detail: dict[str, Any] | None = None,
    severity: str = "info",
    entity_type: str = "decision_cycle",
    entity_id: str | None = None,
    correlation_ids: dict[str, Any] | None = None,
) -> AuditEvent:
    normalized_blocked_reasons = [
        str(code)
        for code in blocked_reason_codes or []
        if str(code or "").strip()
    ]
    normalized_hold_reasons = [
        str(code)
        for code in hold_reason_codes or []
        if str(code or "").strip()
    ]
    payload = {
        "cycle_id": cycle_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "regime": regime,
        "stage": stage,
        "status": status,
        "strategy_candidate": strategy_candidate,
        "ai_decision": ai_decision,
        "risk_decision": risk_decision,
        "entry_plan_status": entry_plan_status or "not_checked",
        "m1_confirmation_status": m1_confirmation_status or "not_checked",
        "final_risk_status": final_risk_status or "not_checked",
        "order_status": order_status or "not_submitted",
        "blocked_reason": blocked_reason or _first_non_empty_string(normalized_blocked_reasons),
        "blocked_reason_codes": normalized_blocked_reasons,
        "hold_reason": hold_reason or _first_non_empty_string(normalized_hold_reasons),
        "hold_reason_codes": normalized_hold_reasons,
        "detail": dict(detail or {}),
    }
    return record_audit_event(
        session,
        event_type="decision_funnel_audit",
        entity_type=entity_type,
        entity_id=entity_id or symbol,
        severity=severity,
        message="Decision funnel audit checkpoint recorded.",
        payload=payload,
        correlation_ids=normalize_correlation_ids(
            correlation_ids,
            cycle_id=cycle_id,
        ),
    )


def create_alert(
    session: Session,
    category: str,
    severity: str,
    title: str,
    message: str,
    payload: dict[str, Any] | None = None,
) -> Alert:
    alert = Alert(
        category=category,
        severity=severity,
        title=title,
        message=message,
        payload=payload or {},
    )
    session.add(alert)
    return alert


def record_health_event(
    session: Session,
    component: str,
    status: str,
    message: str,
    payload: dict[str, Any] | None = None,
    correlation_ids: dict[str, Any] | None = None,
) -> SystemHealthEvent:
    event = SystemHealthEvent(
        component=component,
        status=status,
        message=message,
        payload=merge_correlation_payload(payload, correlation_ids=correlation_ids),
    )
    session.add(event)
    return event


def record_protective_order_health_event(
    session: Session,
    *,
    symbol: str,
    severity: str,
    message: str,
    payload: dict[str, Any],
    entity_type: str = "position",
    entity_id: str | None = None,
    correlation_ids: dict[str, Any] | None = None,
) -> AuditEvent:
    event = record_audit_event(
        session,
        event_type="protective_order_health_check_failed",
        entity_type=entity_type,
        entity_id=entity_id or symbol,
        severity=severity,
        message=message,
        payload=payload,
        correlation_ids=correlation_ids,
    )
    record_health_event(
        session,
        component="protective_orders",
        status="critical" if severity == "critical" else "warning",
        message=message,
        payload=payload,
        correlation_ids=correlation_ids,
    )
    if severity == "critical":
        create_alert(
            session,
            category="protective_orders",
            severity="critical",
            title="Protective order health check failed",
            message=message,
            payload=merge_correlation_payload(payload, correlation_ids=correlation_ids),
        )
    return event


def record_position_management_event(
    session: Session,
    *,
    event_type: str,
    position_id: int | str,
    message: str,
    severity: str = "info",
    payload: dict[str, Any] | None = None,
) -> AuditEvent:
    return record_audit_event(
        session,
        event_type=event_type,
        entity_type="position",
        entity_id=str(position_id),
        message=message,
        severity=severity,
        payload=payload,
    )
