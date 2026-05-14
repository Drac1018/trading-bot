from __future__ import annotations

from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from trading_mvp.models import AuditEvent, RiskCheck


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _as_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    values: list[str] = []
    for item in value:
        normalized = _as_string(item)
        if normalized and normalized not in values:
            values.append(normalized)
    return values


def _merge_codes(*values: object) -> list[str]:
    merged: list[str] = []
    for value in values:
        for code in _as_string_list(value):
            if code not in merged:
                merged.append(code)
    return merged


def _serialize_risk_check(row: RiskCheck) -> dict[str, object]:
    return {
        "id": row.id,
        "symbol": row.symbol,
        "decision_run_id": row.decision_run_id,
        "market_snapshot_id": row.market_snapshot_id,
        "allowed": row.allowed,
        "decision": row.decision,
        "reason_codes": list(row.reason_codes or []),
        "approved_risk_pct": row.approved_risk_pct,
        "approved_leverage": row.approved_leverage,
        "payload": row.payload if isinstance(row.payload, dict) else {},
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _serialize_audit_event(row: AuditEvent | None) -> dict[str, object] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "event_type": row.event_type,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "severity": row.severity,
        "message": row.message,
        "payload": row.payload if isinstance(row.payload, dict) else {},
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _latest_risk_check_audit_events(session: Session, risk_check_ids: list[int]) -> dict[str, AuditEvent]:
    if not risk_check_ids:
        return {}
    risk_check_id_strings = [str(risk_check_id) for risk_check_id in risk_check_ids]
    rows = session.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.event_type == "risk_check",
            AuditEvent.entity_type == "risk_check",
            AuditEvent.entity_id.in_(risk_check_id_strings),
        )
        .order_by(desc(AuditEvent.created_at), desc(AuditEvent.id))
    )
    events: dict[str, AuditEvent] = {}
    for row in rows:
        events.setdefault(row.entity_id, row)
    return events


def get_safety_check_summaries(session: Session, limit: int = 20) -> list[dict[str, object]]:
    rows = list(session.scalars(select(RiskCheck).order_by(desc(RiskCheck.created_at)).limit(limit)))
    audit_events = _latest_risk_check_audit_events(session, [row.id for row in rows])
    summaries: list[dict[str, object]] = []
    for row in rows:
        payload = _as_dict(row.payload)
        risk_check_key = str(row.id)
        audit_event = audit_events.get(risk_check_key)
        blocked_reason_codes = _merge_codes(payload.get("blocked_reason_codes"))
        if row.allowed is False and not blocked_reason_codes:
            blocked_reason_codes = _merge_codes(row.reason_codes, payload.get("reason_codes"))
        reason_codes = _merge_codes(row.reason_codes, payload.get("reason_codes"), blocked_reason_codes)
        summaries.append(
            {
                "id": row.id,
                "risk_check_id": row.id,
                "symbol": row.symbol,
                "decision": row.decision,
                "intent": _as_string(payload.get("intent") or payload.get("intent_family") or payload.get("management_action")),
                "allowed": row.allowed,
                "reason_codes": reason_codes,
                "blocked_reason_codes": blocked_reason_codes,
                "blocked_reason": _as_string(payload.get("blocked_reason") or payload.get("degraded_reason")),
                "audit_event_id": audit_event.id if audit_event is not None else None,
                "has_audit_event": audit_event is not None,
                "created_at": row.created_at,
            }
        )
    return summaries


def get_safety_check_detail(session: Session, risk_check_id: int) -> dict[str, object] | None:
    row = session.get(RiskCheck, risk_check_id)
    if row is None:
        return None
    audit_event = _latest_risk_check_audit_events(session, [row.id]).get(str(row.id))
    return {
        "risk_check": _serialize_risk_check(row),
        "audit_event": _serialize_audit_event(audit_event),
    }
