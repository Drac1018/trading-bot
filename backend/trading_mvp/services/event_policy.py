from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from trading_mvp.schemas import (
    AIEventViewPayload,
    AlignmentDecisionPayload,
    EvaluatedOperatorPolicyPayload,
    EventPolicyEvaluationPayload,
    ManualNoTradeWindowPayload,
    OperatorEffectivePolicyPreview,
    OperatorEventBias,
    OperatorEventEnforcementMode,
    OperatorEventRiskState,
    OperatorEventSourceStatus,
    OperatorEventViewPayload,
)
from trading_mvp.time_utils import ensure_utc_aware, utcnow_aware

KNOWN_BIAS_VALUES = {"bullish", "bearish", "neutral", "no_trade"}
KNOWN_RISK_STATE_VALUES = {"risk_on", "risk_off", "neutral"}
KNOWN_SOURCE_STATUSES = {"available", "stale", "incomplete", "unavailable", "error"}
DEGRADED_REASON_PRIORITY = (
    "event_context_error",
    "event_context_stale",
    "event_context_incomplete",
    "event_context_unavailable",
    "ai_error",
    "ai_stale",
    "ai_incomplete",
    "ai_unavailable",
    "outside_valid_window",
    "operator_unavailable",
)


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _normalize_symbols(values: Sequence[object] | None) -> list[str]:
    normalized: list[str] = []
    for item in values or []:
        symbol = str(item or "").strip().upper()
        if symbol and symbol not in normalized:
            normalized.append(symbol)
    return normalized


def _normalize_bias(value: object) -> OperatorEventBias:
    text = str(value or "").strip().lower()
    if text in KNOWN_BIAS_VALUES or text == "unknown":
        return text  # type: ignore[return-value]
    return "unknown"


def _normalize_risk_state(value: object) -> OperatorEventRiskState:
    text = str(value or "").strip().lower()
    if text in KNOWN_RISK_STATE_VALUES or text == "unknown":
        return text  # type: ignore[return-value]
    return "unknown"


def _normalize_enforcement_mode(value: object) -> OperatorEventEnforcementMode:
    text = str(value or "").strip().lower()
    if text in {"observe_only", "approval_required", "block_on_conflict", "force_no_trade"}:
        return text  # type: ignore[return-value]
    return "observe_only"


def _normalize_source_status(value: object) -> OperatorEventSourceStatus:
    text = str(value or "").strip().lower()
    if text in KNOWN_SOURCE_STATUSES:
        return text  # type: ignore[return-value]
    return "unavailable"


def _decision_to_bias(value: object) -> OperatorEventBias:
    decision = str(value or "").strip().lower()
    if decision == "long":
        return "bullish"
    if decision == "short":
        return "bearish"
    if decision in {"hold", "reduce", "exit"}:
        return "no_trade"
    return "unknown"


def _decision_to_risk_state(value: object) -> OperatorEventRiskState:
    decision = str(value or "").strip().lower()
    if decision in {"long", "short"}:
        return "risk_on"
    if decision in {"hold", "reduce", "exit"}:
        return "neutral"
    return "unknown"


def _dedupe_reason_codes(values: Sequence[str]) -> list[str]:
    deduped: list[str] = []
    for item in values:
        code = str(item or "").strip()
        if code and code not in deduped:
            deduped.append(code)
    return deduped


def _event_source_reason_codes(
    *,
    source_status: OperatorEventSourceStatus,
    is_stale: bool,
) -> list[str]:
    reason_codes: list[str] = []
    if source_status == "error":
        reason_codes.append("event_context_error")
    elif source_status == "incomplete":
        reason_codes.append("event_context_incomplete")
    elif source_status == "unavailable":
        reason_codes.append("event_context_unavailable")
    if source_status == "stale" or is_stale:
        reason_codes.append("event_context_stale")
    return reason_codes


def _ai_source_reason_codes(ai_event_view: AIEventViewPayload) -> list[str]:
    source_state = str(ai_event_view.source_state or "unknown").strip().lower()
    reason_codes: list[str] = []
    if source_state == "error":
        reason_codes.append("ai_error")
    elif source_state == "stale":
        reason_codes.append("ai_stale")
    elif source_state == "incomplete":
        reason_codes.append("ai_incomplete")
    elif source_state in {"unavailable", "unknown"}:
        reason_codes.append("ai_unavailable")
    elif ai_event_view.ai_bias == "unknown" and ai_event_view.ai_risk_state == "unknown":
        reason_codes.append("ai_unavailable")
    return reason_codes


def _resolve_degraded_reason(reason_codes: Sequence[str]) -> str | None:
    deduped = _dedupe_reason_codes(reason_codes)
    for candidate in DEGRADED_REASON_PRIORITY:
        if candidate in deduped:
            return candidate
    return deduped[0] if deduped else None


def build_default_operator_event_view() -> OperatorEventViewPayload:
    return OperatorEventViewPayload()


def derive_ai_event_view(
    *,
    output_payload: Mapping[str, Any] | None = None,
    metadata_json: Mapping[str, Any] | None = None,
) -> AIEventViewPayload:
    output = _as_dict(output_payload)
    metadata = _as_dict(metadata_json)
    explicit = _as_dict(output.get("ai_event_view")) or _as_dict(metadata.get("ai_event_view"))

    scenario_note = (
        str(explicit.get("scenario_note") or output.get("scenario_note") or metadata.get("scenario_note") or "")
        or None
    )
    confidence_penalty_reason = (
        str(
            explicit.get("confidence_penalty_reason")
            or output.get("confidence_penalty_reason")
            or metadata.get("confidence_penalty_reason")
            or ""
        )
        or None
    )
    event_risk_ack = (
        str(
            explicit.get("event_risk_acknowledgement")
            or output.get("event_risk_acknowledgement")
            or metadata.get("event_risk_acknowledgement")
            or ""
        )
        or None
    )
    has_event_aware_signal = any(
        value is not None
        for value in (
            scenario_note,
            confidence_penalty_reason,
            event_risk_ack,
            explicit.get("ai_bias"),
            explicit.get("ai_risk_state"),
        )
    )
    if not has_event_aware_signal:
        return AIEventViewPayload(source_state="unavailable")

    confidence_value = explicit.get("ai_confidence", output.get("confidence"))
    confidence: float | None = None
    if isinstance(confidence_value, (int, float)) and 0.0 <= float(confidence_value) <= 1.0:
        confidence = float(confidence_value)

    source_state = str(explicit.get("source_state") or "").strip().lower()
    if source_state not in {"available", "stale", "incomplete", "unavailable", "error", "unknown"}:
        source_state = "available"

    decision_value = explicit.get("decision", output.get("decision"))
    return AIEventViewPayload(
        ai_bias=_normalize_bias(explicit.get("ai_bias")) if explicit.get("ai_bias") is not None else _decision_to_bias(decision_value),
        ai_risk_state=(
            _normalize_risk_state(explicit.get("ai_risk_state"))
            if explicit.get("ai_risk_state") is not None
            else _decision_to_risk_state(decision_value)
        ),
        ai_confidence=confidence,
        scenario_note=scenario_note or event_risk_ack,
        confidence_penalty_reason=confidence_penalty_reason,
        source_state=source_state,  # type: ignore[arg-type]
    )


def operator_view_applies_to_symbol(
    view: OperatorEventViewPayload | None,
    *,
    symbol: str,
) -> bool:
    if view is None:
        return False
    applies_to_symbols = _normalize_symbols(view.applies_to_symbols)
    return not applies_to_symbols or symbol.upper() in applies_to_symbols


def operator_view_is_active(
    view: OperatorEventViewPayload | None,
    *,
    symbol: str,
    now: datetime,
) -> bool:
    if view is None or not operator_view_applies_to_symbol(view, symbol=symbol):
        return False
    valid_from = ensure_utc_aware(view.valid_from)
    valid_to = ensure_utc_aware(view.valid_to)
    if valid_from is not None and now < valid_from:
        return False
    if valid_to is not None and now >= valid_to:
        return False
    return True


def no_trade_window_is_active(
    window: ManualNoTradeWindowPayload,
    *,
    symbol: str,
    now: datetime,
) -> bool:
    scope = window.scope
    if scope.scope_type == "symbols" and symbol.upper() not in _normalize_symbols(scope.symbols):
        return False
    start_at = ensure_utc_aware(window.start_at)
    end_at = ensure_utc_aware(window.end_at)
    if start_at is None or end_at is None:
        return False
    return start_at <= now < end_at


def relevant_manual_no_trade_windows(
    windows: Sequence[ManualNoTradeWindowPayload],
    *,
    symbol: str,
    now: datetime,
) -> list[ManualNoTradeWindowPayload]:
    return [window for window in windows if no_trade_window_is_active(window, symbol=symbol, now=now)]


def evaluate_event_policy(
    *,
    symbol: str,
    ai_event_view: AIEventViewPayload,
    operator_event_view: OperatorEventViewPayload | None,
    manual_no_trade_windows: Sequence[ManualNoTradeWindowPayload],
    event_source_status: OperatorEventSourceStatus | str | None = None,
    event_source_is_stale: bool = False,
    evaluated_at: datetime | None = None,
) -> EventPolicyEvaluationPayload:
    now = ensure_utc_aware(evaluated_at) or utcnow_aware()
    operator_view = operator_event_view or build_default_operator_event_view()
    reason_codes: list[str] = []
    active_windows = relevant_manual_no_trade_windows(manual_no_trade_windows, symbol=symbol, now=now)
    matched_window_id = active_windows[0].window_id if active_windows else None
    operator_applicable = operator_view_applies_to_symbol(operator_view, symbol=symbol)
    operator_active = operator_view_is_active(operator_view, symbol=symbol, now=now)
    source_status = _normalize_source_status(event_source_status)

    effective_operator_bias = operator_view.operator_bias if operator_active else "unknown"
    effective_operator_risk_state = operator_view.operator_risk_state if operator_active else "unknown"
    reason_codes.extend(_event_source_reason_codes(source_status=source_status, is_stale=bool(event_source_is_stale)))

    if not operator_applicable or not operator_active:
        reason_codes.append("operator_unavailable")
        if operator_applicable and (operator_view.valid_from is not None or operator_view.valid_to is not None):
            reason_codes.append("outside_valid_window")

    reason_codes.extend(_ai_source_reason_codes(ai_event_view))

    if active_windows:
        reason_codes.append("manual_no_trade_active")

    if operator_active and operator_view.operator_bias == "no_trade":
        reason_codes.append("operator_no_trade")

    if ai_event_view.ai_bias == "unknown" or effective_operator_bias == "unknown":
        alignment_status = "insufficient_data"
    elif ai_event_view.ai_risk_state == "unknown" or effective_operator_risk_state == "unknown":
        alignment_status = "insufficient_data"
    elif ai_event_view.ai_bias == effective_operator_bias and ai_event_view.ai_risk_state == effective_operator_risk_state:
        alignment_status = "aligned"
    elif ai_event_view.ai_bias != effective_operator_bias:
        alignment_status = "conflict"
        reason_codes.append("bias_conflict")
        if ai_event_view.ai_risk_state != effective_operator_risk_state:
            reason_codes.append("risk_state_conflict")
    else:
        alignment_status = "partially_aligned"
        reason_codes.append("risk_state_conflict")

    effective_policy_preview: OperatorEffectivePolicyPreview = "allow_normal"
    if active_windows:
        effective_policy_preview = "force_no_trade_window"
    elif operator_active and operator_view.enforcement_mode == "force_no_trade":
        effective_policy_preview = "force_no_trade_window"
    elif operator_active and operator_view.operator_bias == "no_trade":
        effective_policy_preview = "force_no_trade_window"
    elif operator_active and operator_view.enforcement_mode == "block_on_conflict" and alignment_status == "conflict":
        effective_policy_preview = "block_new_entries"
        reason_codes.append("block_on_conflict_preview")
    elif operator_active and operator_view.enforcement_mode == "approval_required" and alignment_status != "aligned":
        effective_policy_preview = "allow_with_approval"
        reason_codes.append("approval_required_preview")
    elif alignment_status == "insufficient_data":
        effective_policy_preview = "insufficient_data"

    deduped_reason_codes = _dedupe_reason_codes(reason_codes)
    blocked_reason: str | None = None
    approval_required_reason: str | None = None
    degraded_reason: str | None = None
    policy_source = "none"

    if active_windows:
        blocked_reason = "manual_no_trade_active"
        policy_source = "manual_no_trade_window"
    elif operator_active and operator_view.enforcement_mode == "force_no_trade":
        blocked_reason = "operator_force_no_trade"
        policy_source = "operator_enforcement_mode"
    elif operator_active and operator_view.operator_bias == "no_trade":
        blocked_reason = "operator_bias_no_trade"
        policy_source = "operator_bias"
    elif operator_active and operator_view.enforcement_mode == "block_on_conflict" and alignment_status == "conflict":
        blocked_reason = "alignment_conflict_block"
        policy_source = "alignment_policy"
    elif operator_active and operator_view.enforcement_mode == "approval_required" and alignment_status != "aligned":
        approval_required_reason = (
            "alignment_insufficient_data" if alignment_status == "insufficient_data" else "alignment_not_aligned"
        )
        policy_source = "alignment_policy"
    else:
        degraded_reason = _resolve_degraded_reason(deduped_reason_codes)

    alignment_decision = AlignmentDecisionPayload(
        ai_bias=ai_event_view.ai_bias,
        operator_bias=effective_operator_bias,
        ai_risk_state=ai_event_view.ai_risk_state,
        operator_risk_state=effective_operator_risk_state,
        alignment_status=alignment_status,  # type: ignore[arg-type]
        reason_codes=deduped_reason_codes,
        effective_policy_preview=effective_policy_preview,
        evaluated_at=now,
    )
    evaluated_operator_policy = EvaluatedOperatorPolicyPayload(
        operator_view_active=operator_active,
        matched_window_id=matched_window_id,
        alignment_status=alignment_status,  # type: ignore[arg-type]
        enforcement_mode=operator_view.enforcement_mode,
        reason_codes=deduped_reason_codes,
        effective_policy_preview=effective_policy_preview,
        event_source_status=source_status,
        event_source_stale=bool(event_source_is_stale),
        evaluated_at=now,
    )
    return EventPolicyEvaluationPayload(
        alignment_decision=alignment_decision,
        evaluated_operator_policy=evaluated_operator_policy,
        blocked_reason=blocked_reason,
        degraded_reason=degraded_reason,
        approval_required_reason=approval_required_reason,
        policy_source=policy_source,  # type: ignore[arg-type]
    )


def evaluate_event_alignment(
    *,
    symbol: str,
    ai_event_view: AIEventViewPayload,
    operator_event_view: OperatorEventViewPayload | None,
    manual_no_trade_windows: Sequence[ManualNoTradeWindowPayload],
    event_source_status: OperatorEventSourceStatus | str | None = None,
    event_source_is_stale: bool = False,
    evaluated_at: datetime | None = None,
) -> AlignmentDecisionPayload:
    evaluation = evaluate_event_policy(
        symbol=symbol,
        ai_event_view=ai_event_view,
        operator_event_view=operator_event_view,
        manual_no_trade_windows=manual_no_trade_windows,
        event_source_status=event_source_status,
        event_source_is_stale=event_source_is_stale,
        evaluated_at=evaluated_at,
    )
    return evaluation.alignment_decision
