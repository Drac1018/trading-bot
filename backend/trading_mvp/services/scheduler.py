from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from trading_mvp.models import SchedulerRun
from trading_mvp.services.account import get_open_positions
from trading_mvp.services.audit import record_audit_event, record_health_event
from trading_mvp.services.orchestrator import TradingOrchestrator
from trading_mvp.services.pause_control import attempt_auto_resume
from trading_mvp.services.settings import (
    get_effective_symbol_schedule,
    get_or_create_settings,
)
from trading_mvp.time_utils import utcnow_naive

WINDOW_HOURS = {"1h": 1, "4h": 4, "12h": 12, "24h": 24}

EXCHANGE_SYNC_WORKFLOW = "exchange_sync_cycle"
MARKET_REFRESH_WORKFLOW = "market_refresh_cycle"
POSITION_MANAGEMENT_WORKFLOW = "position_management_cycle"
INTERVAL_DECISION_WORKFLOW = "interval_decision_cycle"


def _next_window_run(window: str, from_time: datetime | None = None) -> datetime:
    base = from_time or utcnow_naive()
    return base + timedelta(hours=WINDOW_HOURS[window])


def _symbol_schedule_window(interval_seconds: int | None = None, interval_minutes: int | None = None) -> str:
    if interval_seconds is not None:
        return f"{int(interval_seconds)}s"
    if interval_minutes is not None:
        return f"{int(interval_minutes)}m"
    return "unknown"


def _latest_workflow_run(session: Session, workflow: str) -> SchedulerRun | None:
    return session.scalar(
        select(SchedulerRun)
        .where(SchedulerRun.workflow == workflow)
        .order_by(desc(SchedulerRun.created_at))
        .limit(1)
    )


def _latest_symbol_workflow_run(session: Session, workflow: str, symbol: str) -> SchedulerRun | None:
    rows = list(
        session.scalars(
            select(SchedulerRun)
            .where(SchedulerRun.workflow == workflow)
            .order_by(desc(SchedulerRun.created_at))
            .limit(200)
        )
    )
    symbol_upper = symbol.upper()
    for row in rows:
        outcome = row.outcome if isinstance(row.outcome, dict) else {}
        if str(outcome.get("symbol", "")).upper() == symbol_upper:
            return row
    return None


def _is_due(latest: SchedulerRun | None, delta: timedelta) -> bool:
    if latest is None:
        return True
    computed_due_at = latest.created_at + delta
    if latest.next_run_at is None:
        return computed_due_at <= utcnow_naive()
    return min(latest.next_run_at, computed_due_at) <= utcnow_naive()


def _start_scheduler_run(
    session: Session,
    *,
    workflow: str,
    schedule_window: str,
    triggered_by: str,
    symbol: str | None = None,
    next_run_at: datetime | None = None,
) -> SchedulerRun:
    outcome = {"symbol": symbol.upper()} if symbol else {}
    row = SchedulerRun(
        schedule_window=schedule_window,
        workflow=workflow,
        status="running",
        triggered_by=triggered_by,
        next_run_at=next_run_at,
        outcome=outcome,
    )
    session.add(row)
    session.flush()
    return row


def _finish_scheduler_run(
    session: Session,
    *,
    row: SchedulerRun,
    success: bool,
    message: str,
    payload: dict[str, object],
) -> dict[str, object]:
    row.status = "success" if success else "failed"
    row.outcome = payload
    session.add(row)
    event_type = "scheduler_run" if success else "scheduler_run_failed"
    severity = "info" if success else "error"
    record_audit_event(
        session,
        event_type=event_type,
        entity_type="scheduler_run",
        entity_id=str(row.id),
        severity=severity,
        message=message,
        payload=payload,
    )
    if not success:
        record_health_event(
            session,
            component="scheduler",
            status="error",
            message=message,
            payload=payload,
        )
    session.flush()
    return {
        "scheduler_run_id": row.id,
        "workflow": row.workflow,
        "status": row.status,
        "outcome": payload,
    }


def run_window(session: Session, window: str, triggered_by: str = "manual") -> dict[str, object]:
    orchestrator = TradingOrchestrator(session)
    auto_resume_result = attempt_auto_resume(
        session,
        orchestrator.settings_row,
        trigger_source=f"{triggered_by}:{window}",
    )
    if window == "1h":
        outcome = orchestrator.run_market_refresh_cycle(
            trigger_event=triggered_by,
            auto_resume_checked=True,
            include_exchange_sync=False,
        )
        scheduler_result = _finish_scheduler_run(
            session,
            row=_start_scheduler_run(
                session,
                workflow=MARKET_REFRESH_WORKFLOW,
                schedule_window=window,
                triggered_by=triggered_by,
                next_run_at=_next_window_run(window),
            ),
            success=True,
            message="1h market refresh completed.",
            payload={**outcome, "window": window, "auto_resume": auto_resume_result},
        )
        return {
            **scheduler_result,
            "window": window,
            "status": "market_data_only" if not orchestrator.settings_row.ai_enabled else "success",
            "outcome": outcome,
            "auto_resume": auto_resume_result,
        }

    if not orchestrator.settings_row.ai_enabled:
        return {
            "window": window,
            "status": "skipped",
            "reason": "AI_DISABLED",
            "auto_resume": auto_resume_result,
        }

    row = _start_scheduler_run(
        session,
        workflow="scheduled_review",
        schedule_window=window,
        triggered_by=triggered_by,
        next_run_at=_next_window_run(window),
    )
    try:
        if window == "4h":
            outcome = orchestrator.run_integration_review()
        elif window == "12h":
            outcome = orchestrator.run_ui_review()
        elif window == "24h":
            outcome = orchestrator.run_product_review()
        else:
            raise RuntimeError(f"Unsupported window {window}")
    except Exception as exc:
        return _finish_scheduler_run(
            session,
            row=row,
            success=False,
            message=f"{window} scheduled workflow failed.",
            payload={"window": window, "error": str(exc)},
        )
    return _finish_scheduler_run(
        session,
        row=row,
        success=True,
        message=f"{window} scheduled workflow completed.",
        payload={**outcome, "window": window, "auto_resume": auto_resume_result},
    )


def is_exchange_sync_due(session: Session) -> bool:
    settings_row = get_or_create_settings(session)
    latest = _latest_workflow_run(session, EXCHANGE_SYNC_WORKFLOW)
    return _is_due(latest, timedelta(seconds=settings_row.exchange_sync_interval_seconds))


def run_exchange_sync_cycle(session: Session, triggered_by: str = "scheduler") -> dict[str, object]:
    settings_row = get_or_create_settings(session)
    interval_seconds = int(settings_row.exchange_sync_interval_seconds)
    row = _start_scheduler_run(
        session,
        workflow=EXCHANGE_SYNC_WORKFLOW,
        schedule_window=_symbol_schedule_window(interval_seconds=interval_seconds),
        triggered_by=triggered_by,
        next_run_at=utcnow_naive() + timedelta(seconds=interval_seconds),
    )
    orchestrator = TradingOrchestrator(session)
    try:
        outcome = orchestrator.run_exchange_sync_cycle(trigger_event=triggered_by)
    except Exception as exc:
        return _finish_scheduler_run(
            session,
            row=row,
            success=False,
            message="Exchange sync cycle failed.",
            payload={"error": str(exc)},
        )
    success = str(outcome.get("status")) != "error"
    return _finish_scheduler_run(
        session,
        row=row,
        success=success,
        message="Exchange sync cycle completed." if success else "Exchange sync cycle failed.",
        payload=outcome,
    )


def get_due_market_refresh_symbols(session: Session) -> list[str]:
    settings_row = get_or_create_settings(session)
    due: list[str] = []
    for effective in get_effective_symbol_schedule(settings_row):
        if not effective.enabled:
            continue
        latest = _latest_symbol_workflow_run(session, MARKET_REFRESH_WORKFLOW, effective.symbol)
        if _is_due(latest, timedelta(minutes=effective.market_refresh_interval_minutes)):
            due.append(effective.symbol)
    return due


def run_market_refresh_cycle(session: Session, triggered_by: str = "scheduler") -> dict[str, object]:
    orchestrator = TradingOrchestrator(session)
    results: list[dict[str, object]] = []
    for effective in get_effective_symbol_schedule(orchestrator.settings_row):
        if not effective.enabled:
            continue
        latest = _latest_symbol_workflow_run(session, MARKET_REFRESH_WORKFLOW, effective.symbol)
        if not _is_due(latest, timedelta(minutes=effective.market_refresh_interval_minutes)):
            continue
        row = _start_scheduler_run(
            session,
            workflow=MARKET_REFRESH_WORKFLOW,
            schedule_window=_symbol_schedule_window(interval_minutes=effective.market_refresh_interval_minutes),
            triggered_by=triggered_by,
            symbol=effective.symbol,
            next_run_at=utcnow_naive() + timedelta(minutes=effective.market_refresh_interval_minutes),
        )
        try:
            cycle = orchestrator.run_market_refresh_cycle(
                symbols=[effective.symbol],
                timeframe=effective.timeframe,
                trigger_event=triggered_by,
                include_exchange_sync=False,
                auto_resume_checked=True,
            )
            symbol_outcome = cycle["results"][0]
            results.append(
                _finish_scheduler_run(
                    session,
                    row=row,
                    success=True,
                    message="Market refresh cycle completed.",
                    payload=symbol_outcome,
                )
            )
        except Exception as exc:
            results.append(
                _finish_scheduler_run(
                    session,
                    row=row,
                    success=False,
                    message="Market refresh cycle failed.",
                    payload={"symbol": effective.symbol, "error": str(exc)},
                )
            )
    return {"workflow": MARKET_REFRESH_WORKFLOW, "results": results}


def get_due_position_management_symbols(session: Session) -> list[str]:
    settings_row = get_or_create_settings(session)
    due: list[str] = []
    for effective in get_effective_symbol_schedule(settings_row):
        if not effective.enabled:
            continue
        if not get_open_positions(session, effective.symbol):
            continue
        latest = _latest_symbol_workflow_run(session, POSITION_MANAGEMENT_WORKFLOW, effective.symbol)
        if _is_due(latest, timedelta(seconds=effective.position_management_interval_seconds)):
            due.append(effective.symbol)
    return due


def run_position_management_cycle(session: Session, triggered_by: str = "scheduler") -> dict[str, object]:
    orchestrator = TradingOrchestrator(session)
    results: list[dict[str, object]] = []
    for effective in get_effective_symbol_schedule(orchestrator.settings_row):
        if not effective.enabled:
            continue
        if not get_open_positions(session, effective.symbol):
            continue
        latest = _latest_symbol_workflow_run(session, POSITION_MANAGEMENT_WORKFLOW, effective.symbol)
        if not _is_due(latest, timedelta(seconds=effective.position_management_interval_seconds)):
            continue
        row = _start_scheduler_run(
            session,
            workflow=POSITION_MANAGEMENT_WORKFLOW,
            schedule_window=_symbol_schedule_window(
                interval_seconds=effective.position_management_interval_seconds
            ),
            triggered_by=triggered_by,
            symbol=effective.symbol,
            next_run_at=utcnow_naive() + timedelta(seconds=effective.position_management_interval_seconds),
        )
        try:
            outcome = orchestrator.run_position_management_cycle(
                symbol=effective.symbol,
                timeframe=effective.timeframe,
                trigger_event=triggered_by,
            )
            results.append(
                _finish_scheduler_run(
                    session,
                    row=row,
                    success=True,
                    message="Position management cycle completed.",
                    payload=outcome,
                )
            )
        except Exception as exc:
            results.append(
                _finish_scheduler_run(
                    session,
                    row=row,
                    success=False,
                    message="Position management cycle failed.",
                    payload={"symbol": effective.symbol, "error": str(exc)},
                )
            )
    return {"workflow": POSITION_MANAGEMENT_WORKFLOW, "results": results}


def is_interval_decision_due(session: Session, symbol: str | None = None) -> bool:
    settings_row = get_or_create_settings(session)
    if not settings_row.ai_enabled:
        return False
    if symbol is None:
        return len(get_due_interval_decision_symbols(session)) > 0
    effective = next(
        (item for item in get_effective_symbol_schedule(settings_row) if item.symbol == symbol.upper()),
        None,
    )
    if effective is None or not effective.enabled:
        return False
    latest = _latest_symbol_workflow_run(session, INTERVAL_DECISION_WORKFLOW, effective.symbol)
    return _is_due(latest, timedelta(minutes=effective.decision_cycle_interval_minutes))


def get_due_interval_decision_symbols(session: Session) -> list[str]:
    settings_row = get_or_create_settings(session)
    due: list[str] = []
    for effective in get_effective_symbol_schedule(settings_row):
        if not effective.enabled:
            continue
        latest = _latest_symbol_workflow_run(session, INTERVAL_DECISION_WORKFLOW, effective.symbol)
        if _is_due(latest, timedelta(minutes=effective.decision_cycle_interval_minutes)):
            due.append(effective.symbol)
    return due


def run_interval_decision_cycle(session: Session, triggered_by: str = "scheduler") -> dict[str, object]:
    settings_row = get_or_create_settings(session)
    auto_resume_result = attempt_auto_resume(
        session,
        settings_row,
        trigger_source=f"{triggered_by}:interval",
    )
    if not settings_row.ai_enabled:
        return {
            "workflow": INTERVAL_DECISION_WORKFLOW,
            "results": [],
            "status": "skipped",
            "reason": "AI_DISABLED",
            "auto_resume": auto_resume_result,
        }
    orchestrator = TradingOrchestrator(session)
    results: list[dict[str, object]] = []
    for effective in get_effective_symbol_schedule(settings_row):
        if not effective.enabled:
            continue
        latest = _latest_symbol_workflow_run(session, INTERVAL_DECISION_WORKFLOW, effective.symbol)
        if not _is_due(latest, timedelta(minutes=effective.decision_cycle_interval_minutes)):
            continue
        row = _start_scheduler_run(
            session,
            workflow=INTERVAL_DECISION_WORKFLOW,
            schedule_window=_symbol_schedule_window(
                interval_minutes=effective.decision_cycle_interval_minutes
            ),
            triggered_by=triggered_by,
            symbol=effective.symbol,
            next_run_at=utcnow_naive() + timedelta(minutes=effective.decision_cycle_interval_minutes),
        )
        try:
            outcome = orchestrator.run_decision_cycle(
                symbol=effective.symbol,
                timeframe=effective.timeframe,
                trigger_event="realtime_cycle",
                auto_resume_checked=True,
                exchange_sync_checked=True,
                include_inline_position_management=False,
            )
            results.append(
                _finish_scheduler_run(
                    session,
                    row=row,
                    success=True,
                    message="Interval decision cycle completed.",
                    payload={**outcome, "symbol": effective.symbol, "auto_resume": auto_resume_result},
                )
            )
        except Exception as exc:
            results.append(
                _finish_scheduler_run(
                    session,
                    row=row,
                    success=False,
                    message="Interval decision cycle failed.",
                    payload={"symbol": effective.symbol, "error": str(exc)},
                )
            )
    return {"workflow": INTERVAL_DECISION_WORKFLOW, "results": results, "auto_resume": auto_resume_result}


def run_due_exchange_sync_cycle(session: Session) -> dict[str, object] | None:
    if not is_exchange_sync_due(session):
        return None
    return run_exchange_sync_cycle(session, triggered_by="scheduler")


def run_due_interval_decision_cycle(session: Session) -> dict[str, object] | None:
    if not is_interval_decision_due(session):
        return None
    return run_interval_decision_cycle(session, triggered_by="scheduler")


def run_due_operational_cycles(session: Session) -> list[dict[str, object]]:
    outputs: list[dict[str, object]] = []
    exchange = run_due_exchange_sync_cycle(session)
    if exchange is not None:
        outputs.append(exchange)
    market = run_market_refresh_cycle(session, triggered_by="scheduler")
    if market["results"]:
        outputs.append(market)
    position_management = run_position_management_cycle(session, triggered_by="scheduler")
    if position_management["results"]:
        outputs.append(position_management)
    decisions = run_due_interval_decision_cycle(session)
    if decisions is not None and decisions.get("results"):
        outputs.append(decisions)
    return outputs


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
            .where(
                SchedulerRun.schedule_window == window,
                SchedulerRun.workflow == "scheduled_review",
            )
            .order_by(desc(SchedulerRun.created_at))
            .limit(1)
        )
        if latest is None or latest.next_run_at is None or latest.next_run_at <= utcnow_naive():
            outputs.append(run_window(session, window, triggered_by="scheduler"))
    return outputs
