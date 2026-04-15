from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from trading_mvp.models import Alert, AuditEvent, SystemHealthEvent


def record_audit_event(
    session: Session,
    event_type: str,
    entity_type: str,
    entity_id: str,
    message: str,
    severity: str = "info",
    payload: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        message=message,
        severity=severity,
        payload=payload or {},
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
) -> SystemHealthEvent:
    event = SystemHealthEvent(
        component=component,
        status=status,
        message=message,
        payload=payload or {},
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
