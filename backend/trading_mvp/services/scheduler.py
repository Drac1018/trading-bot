from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from trading_mvp.models import MarketSnapshot, SchedulerRun
from trading_mvp.services.audit import record_audit_event, record_health_event
from trading_mvp.services.orchestrator import TradingOrchestrator
from trading_mvp.services.pause_control import attempt_auto_resume
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive

WINDOW_HOURS = {"1h": 1, "4h": 4, "12h": 12, "24h": 24}


def _next_run(window: str, from_time: datetime | None = None) -> datetime:
    base = from_time or utcnow_naive()
    return base + timedelta(hours=WINDOW_HOURS[window])


def run_window(session: Session, window: str, triggered_by: str = "manual") -> dict[str, object]:
    orchestrator = TradingOrchestrator(session)
    auto_resume_result = attempt_auto_resume(session, orchestrator.settings_row)
    if window == "1h":
        outcome = orchestrator.run_market_refresh_cycle(
            status="market_data_only" if not orchestrator.settings_row.ai_enabled else "market_refresh"
        )
        if not orchestrator.settings_row.ai_enabled:
            return {
                "window": window,
                "status": "market_data_only",
                "outcome": outcome,
                "auto_resume": auto_resume_result,
            }
    elif not orchestrator.settings_row.ai_enabled:
        return {"window": window, "status": "skipped", "reason": "AI_DISABLED"}
    scheduler_run = SchedulerRun(
        schedule_window=window,
        workflow="market_refresh" if window == "1h" else "scheduled_review",
        status="running",
        triggered_by=triggered_by,
        next_run_at=_next_run(window),
        outcome={},
    )
    session.add(scheduler_run)
    session.flush()

    try:
        if window == "4h":
            outcome = orchestrator.run_integration_review()
        elif window == "12h":
            outcome = orchestrator.run_ui_review()
        elif window == "24h":
            outcome = orchestrator.run_product_review()
        elif window == "1h":
            pass
        else:
            scheduler_run.status = "failed"
            scheduler_run.outcome = {"error": f"Unsupported window {window}"}
            return scheduler_run.outcome
    except Exception as exc:
        scheduler_run.status = "failed"
        scheduler_run.outcome = {"error": str(exc)}
        scheduler_run.next_run_at = _next_run(window)
        session.add(scheduler_run)
        record_audit_event(
            session,
            event_type="scheduler_run_failed",
            entity_type="scheduler_run",
            entity_id=str(scheduler_run.id),
            severity="error",
            message=f"{window} scheduled workflow failed.",
            payload={"window": window, "error": str(exc)},
        )
        record_health_event(
            session,
            component="scheduler",
            status="error",
            message="Scheduled workflow failed.",
            payload={"window": window, "error": str(exc)},
        )
        session.flush()
        return {"scheduler_run_id": scheduler_run.id, "window": window, "outcome": scheduler_run.outcome}

    scheduler_run.status = "success"
    scheduler_run.outcome = outcome
    scheduler_run.next_run_at = _next_run(window)
    session.add(scheduler_run)
    record_audit_event(
        session,
        event_type="scheduler_run",
        entity_type="scheduler_run",
        entity_id=str(scheduler_run.id),
        message=f"{window} scheduled workflow completed.",
        payload={**outcome, "auto_resume": auto_resume_result},
    )
    session.flush()
    return {
        "scheduler_run_id": scheduler_run.id,
        "window": window,
        "outcome": outcome,
        "auto_resume": auto_resume_result,
    }


def run_interval_decision_cycle(session: Session, triggered_by: str = "scheduler") -> dict[str, object]:
    settings_row = get_or_create_settings(session)
    auto_resume_result = attempt_auto_resume(session, settings_row)
    interval_minutes = settings_row.decision_cycle_interval_minutes
    orchestrator = TradingOrchestrator(session)
    if not settings_row.ai_enabled:
        return {
            **orchestrator.run_selected_symbols_cycle(trigger_event="realtime_cycle"),
            "auto_resume": auto_resume_result,
        }
    scheduler_run = SchedulerRun(
        schedule_window=f"{interval_minutes}m",
        workflow="interval_decision_cycle",
        status="running",
        triggered_by=triggered_by,
        next_run_at=utcnow_naive() + timedelta(minutes=interval_minutes),
        outcome={},
    )
    session.add(scheduler_run)
    session.flush()

    try:
        outcome = orchestrator.run_selected_symbols_cycle(trigger_event="realtime_cycle")
    except Exception as exc:
        scheduler_run.status = "failed"
        scheduler_run.outcome = {"error": str(exc)}
        scheduler_run.next_run_at = utcnow_naive() + timedelta(minutes=interval_minutes)
        session.add(scheduler_run)
        record_audit_event(
            session,
            event_type="scheduler_run_failed",
            entity_type="scheduler_run",
            entity_id=str(scheduler_run.id),
            severity="error",
            message="Interval decision cycle failed.",
            payload={"interval_minutes": interval_minutes, "error": str(exc)},
        )
        record_health_event(
            session,
            component="scheduler",
            status="error",
            message="Interval decision cycle failed.",
            payload={"interval_minutes": interval_minutes, "error": str(exc)},
        )
        session.flush()
        return {"scheduler_run_id": scheduler_run.id, "interval_minutes": interval_minutes, "outcome": scheduler_run.outcome}

    scheduler_run.status = "success"
    scheduler_run.outcome = outcome
    scheduler_run.next_run_at = utcnow_naive() + timedelta(minutes=interval_minutes)
    session.add(scheduler_run)
    record_audit_event(
        session,
        event_type="scheduler_run",
        entity_type="scheduler_run",
        entity_id=str(scheduler_run.id),
        message="Interval decision cycle completed.",
        payload={"interval_minutes": interval_minutes, **outcome, "auto_resume": auto_resume_result},
    )
    session.flush()
    return {
        "scheduler_run_id": scheduler_run.id,
        "interval_minutes": interval_minutes,
        "outcome": outcome,
        "auto_resume": auto_resume_result,
    }


def run_due_interval_decision_cycle(session: Session) -> dict[str, object] | None:
    if not is_interval_decision_due(session):
        return None
    return run_interval_decision_cycle(session, triggered_by="scheduler")


def is_interval_decision_due(session: Session) -> bool:
    settings_row = get_or_create_settings(session)
    if not settings_row.ai_enabled:
        latest_snapshot = session.scalar(select(MarketSnapshot).order_by(desc(MarketSnapshot.snapshot_time)).limit(1))
        if latest_snapshot is None:
            return True
        return latest_snapshot.snapshot_time <= utcnow_naive() - timedelta(minutes=settings_row.decision_cycle_interval_minutes)
    workflows = ["interval_decision_cycle"]
    latest = session.scalar(
        select(SchedulerRun)
        .where(SchedulerRun.workflow.in_(workflows))
        .order_by(desc(SchedulerRun.created_at))
        .limit(1)
    )
    return latest is None or latest.next_run_at is None or latest.next_run_at <= utcnow_naive()


def run_due_windows(session: Session) -> list[dict[str, object]]:
    settings_row = get_or_create_settings(session)
    outputs: list[dict[str, object]] = []
    if not settings_row.ai_enabled:
        return outputs
    for window in settings_row.schedule_windows:
        if window == "1h":
            continue
        latest = session.scalar(
            select(SchedulerRun)
            .where(SchedulerRun.schedule_window == window)
            .order_by(desc(SchedulerRun.created_at))
            .limit(1)
        )
        if latest is None or latest.next_run_at is None or latest.next_run_at <= utcnow_naive():
            outputs.append(run_window(session, window, triggered_by="scheduler"))
    return outputs
