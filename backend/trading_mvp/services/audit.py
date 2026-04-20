from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from trading_mvp.models import Alert, AuditEvent, SystemHealthEvent

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


def compact_audit_payload(
    payload: dict[str, Any] | None,
    *,
    event_type: str | None = None,
    event_category: str | None = None,
) -> dict[str, Any]:
    source = dict(payload or {})
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

    if category_key == "execution" or event_key.startswith("live_execution"):
        return _compact_payload_dict(source, allowed_keys=EXECUTION_TIMELINE_KEYS)

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
        event_context = _compact_payload_dict(
            source.get("event_context"),
            allowed_keys=(
                "source_status",
                "is_stale",
                "is_complete",
                "next_event_name",
                "next_event_at",
            ),
        )
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

    return {}


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
    return merged_payload


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
