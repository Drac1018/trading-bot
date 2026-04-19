from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from trading_mvp.models import PendingEntryPlan, SchedulerRun
from trading_mvp.services.account import get_open_positions
from trading_mvp.services.audit import record_audit_event, record_health_event
from trading_mvp.services.orchestrator import TradingOrchestrator
from trading_mvp.services.pause_control import attempt_auto_resume
from trading_mvp.services.runtime_state import build_sync_freshness_summary
from trading_mvp.services.settings import (
    get_effective_symbol_schedule,
    get_or_create_settings,
)
from trading_mvp.time_utils import utcnow_naive

WINDOW_HOURS = {"1h": 1}

EXCHANGE_SYNC_WORKFLOW = "exchange_sync_cycle"
MARKET_REFRESH_WORKFLOW = "market_refresh_cycle"
POSITION_MANAGEMENT_WORKFLOW = "position_management_cycle"
INTERVAL_DECISION_WORKFLOW = "interval_decision_cycle"
ENTRY_PLAN_WATCHER_WORKFLOW = "entry_plan_watcher_cycle"
READ_REFRESH_SYNC_DEBOUNCE_SECONDS = 30


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


def _coerce_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _sync_summary_needs_refresh(sync_freshness_summary: dict[str, object]) -> bool:
    for payload in sync_freshness_summary.values():
        if not isinstance(payload, dict):
            continue
        if bool(payload.get("stale")) or bool(payload.get("incomplete")):
            return True
    return False


def _latest_sync_attempt_at(sync_freshness_summary: dict[str, object]) -> datetime | None:
    latest: datetime | None = None
    for payload in sync_freshness_summary.values():
        if not isinstance(payload, dict):
            continue
        attempted_at = _coerce_datetime(payload.get("last_attempt_at"))
        if attempted_at is None:
            continue
        if latest is None or attempted_at > latest:
            latest = attempted_at
    return latest


def _symbol_cadence_profile(
    orchestrator: TradingOrchestrator,
    *,
    symbol: str,
    timeframe: str,
) -> dict[str, object]:
    return orchestrator.get_symbol_cadence_profile(symbol=symbol, timeframe=timeframe)


def _cadence_minutes(profile: dict[str, object], key: str, fallback: int) -> int:
    cadence = profile.get("effective_cadence")
    if not isinstance(cadence, dict):
        return fallback
    value = cadence.get(key)
    return int(value) if isinstance(value, (int, float)) and int(value) > 0 else fallback


def _interval_decision_schedule_details(
    orchestrator: TradingOrchestrator,
    *,
    effective: object,
    cadence_profile: dict[str, object],
) -> dict[str, object]:
    return orchestrator.resolve_interval_decision_schedule_details(
        symbol=effective.symbol,
        timeframe=effective.timeframe,
        effective_settings=effective,
        cadence_profile=cadence_profile,
    )


def _cadence_seconds(profile: dict[str, object], key: str, fallback: int) -> int:
    cadence = profile.get("effective_cadence")
    if not isinstance(cadence, dict):
        return fallback
    value = cadence.get(key)
    return int(value) if isinstance(value, (int, float)) and int(value) > 0 else fallback


def _position_management_scheduler_seconds(profile: dict[str, object], fallback: int) -> int:
    cadence_seconds = _cadence_seconds(
        profile,
        "position_management_interval_seconds",
        fallback,
    )
    return max(15, min(int(cadence_seconds), int(fallback)))


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
    if window not in WINDOW_HOURS:
        return {
            "window": window,
            "status": "disabled",
            "reason": "REVIEW_WINDOW_DISABLED_OUT_OF_SCOPE",
            "auto_resume": auto_resume_result,
        }
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

    return {
        "window": window,
        "status": "skipped",
        "reason": "AI_DISABLED",
        "auto_resume": auto_resume_result,
    }


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
    try:
        orchestrator = TradingOrchestrator(session)
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


def maybe_refresh_exchange_sync_freshness(
    session: Session,
    *,
    triggered_by: str = "api_read",
) -> dict[str, Any] | None:
    settings_row = get_or_create_settings(session)
    if not settings_row.binance_api_key_encrypted or not settings_row.binance_api_secret_encrypted:
        return None
    sync_freshness_summary = build_sync_freshness_summary(settings_row)
    if not _sync_summary_needs_refresh(sync_freshness_summary):
        return None
    latest_attempt_at = _latest_sync_attempt_at(sync_freshness_summary)
    now = utcnow_naive()
    if latest_attempt_at is not None and (now - latest_attempt_at).total_seconds() < READ_REFRESH_SYNC_DEBOUNCE_SECONDS:
        return None
    return run_exchange_sync_cycle(session, triggered_by=triggered_by)


def get_due_market_refresh_symbols(session: Session) -> list[str]:
    settings_row = get_or_create_settings(session)
    orchestrator = TradingOrchestrator(session)
    due: list[str] = []
    for effective in get_effective_symbol_schedule(settings_row):
        if not effective.enabled:
            continue
        cadence_profile = _symbol_cadence_profile(
            orchestrator,
            symbol=effective.symbol,
            timeframe=effective.timeframe,
        )
        cadence_minutes = _cadence_minutes(
            cadence_profile,
            "market_refresh_interval_minutes",
            effective.market_refresh_interval_minutes,
        )
        latest = _latest_symbol_workflow_run(session, MARKET_REFRESH_WORKFLOW, effective.symbol)
        if _is_due(latest, timedelta(minutes=cadence_minutes)):
            due.append(effective.symbol)
    return due


def run_market_refresh_cycle(session: Session, triggered_by: str = "scheduler") -> dict[str, object]:
    orchestrator = TradingOrchestrator(session)
    results: list[dict[str, object]] = []
    for effective in get_effective_symbol_schedule(orchestrator.settings_row):
        if not effective.enabled:
            continue
        cadence_profile = _symbol_cadence_profile(
            orchestrator,
            symbol=effective.symbol,
            timeframe=effective.timeframe,
        )
        cadence_minutes = _cadence_minutes(
            cadence_profile,
            "market_refresh_interval_minutes",
            effective.market_refresh_interval_minutes,
        )
        latest = _latest_symbol_workflow_run(session, MARKET_REFRESH_WORKFLOW, effective.symbol)
        if not _is_due(latest, timedelta(minutes=cadence_minutes)):
            continue
        row = _start_scheduler_run(
            session,
            workflow=MARKET_REFRESH_WORKFLOW,
            schedule_window=_symbol_schedule_window(interval_minutes=cadence_minutes),
            triggered_by=triggered_by,
            symbol=effective.symbol,
            next_run_at=utcnow_naive() + timedelta(minutes=cadence_minutes),
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
                    payload={**symbol_outcome, "cadence": cadence_profile},
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
    orchestrator = TradingOrchestrator(session)
    due: list[str] = []
    for effective in get_effective_symbol_schedule(settings_row):
        if not effective.enabled:
            continue
        if not get_open_positions(session, effective.symbol):
            continue
        cadence_profile = _symbol_cadence_profile(
            orchestrator,
            symbol=effective.symbol,
            timeframe=effective.timeframe,
        )
        cadence_seconds = _position_management_scheduler_seconds(
            cadence_profile,
            effective.position_management_interval_seconds,
        )
        latest = _latest_symbol_workflow_run(session, POSITION_MANAGEMENT_WORKFLOW, effective.symbol)
        if _is_due(latest, timedelta(seconds=cadence_seconds)):
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
        cadence_profile = _symbol_cadence_profile(
            orchestrator,
            symbol=effective.symbol,
            timeframe=effective.timeframe,
        )
        cadence_seconds = _position_management_scheduler_seconds(
            cadence_profile,
            effective.position_management_interval_seconds,
        )
        latest = _latest_symbol_workflow_run(session, POSITION_MANAGEMENT_WORKFLOW, effective.symbol)
        if not _is_due(latest, timedelta(seconds=cadence_seconds)):
            continue
        row = _start_scheduler_run(
            session,
            workflow=POSITION_MANAGEMENT_WORKFLOW,
            schedule_window=_symbol_schedule_window(
                interval_seconds=cadence_seconds
            ),
            triggered_by=triggered_by,
            symbol=effective.symbol,
            next_run_at=utcnow_naive() + timedelta(seconds=cadence_seconds),
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
                    payload={**outcome, "cadence": cadence_profile},
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


def get_due_entry_plan_symbols(session: Session) -> list[str]:
    orchestrator = TradingOrchestrator(session)
    active_symbols = sorted(
        {
            row.symbol.upper()
            for row in session.scalars(
                select(PendingEntryPlan).where(PendingEntryPlan.plan_status == "armed")
            )
        }
    )
    due: list[str] = []
    for symbol in active_symbols:
        effective = orchestrator._effective_symbol_settings(symbol)
        cadence_profile = _symbol_cadence_profile(
            orchestrator,
            symbol=symbol,
            timeframe=effective.timeframe,
        )
        cadence_minutes = _cadence_minutes(
            cadence_profile,
            "entry_plan_watcher_interval_minutes",
            1,
        )
        latest = _latest_symbol_workflow_run(session, ENTRY_PLAN_WATCHER_WORKFLOW, symbol)
        if _is_due(latest, timedelta(minutes=cadence_minutes)):
            due.append(symbol)
    return due


def run_entry_plan_watcher_cycle(session: Session, triggered_by: str = "scheduler") -> dict[str, object]:
    orchestrator = TradingOrchestrator(session)
    results: list[dict[str, object]] = []
    for symbol in get_due_entry_plan_symbols(session):
        effective = orchestrator._effective_symbol_settings(symbol)
        cadence_profile = _symbol_cadence_profile(
            orchestrator,
            symbol=symbol,
            timeframe=effective.timeframe,
        )
        cadence_minutes = _cadence_minutes(
            cadence_profile,
            "entry_plan_watcher_interval_minutes",
            1,
        )
        row = _start_scheduler_run(
            session,
            workflow=ENTRY_PLAN_WATCHER_WORKFLOW,
            schedule_window=_symbol_schedule_window(interval_minutes=cadence_minutes),
            triggered_by=triggered_by,
            symbol=symbol,
            next_run_at=utcnow_naive() + timedelta(minutes=cadence_minutes),
        )
        try:
            outcome = orchestrator.run_entry_plan_watcher_cycle(
                symbols=[symbol],
                trigger_event="entry_plan_watcher",
                auto_resume_checked=True,
            )
            symbol_outcome = outcome["results"][0] if outcome.get("results") else {"symbol": symbol, "plans": []}
            results.append(
                _finish_scheduler_run(
                    session,
                    row=row,
                    success=True,
                    message="Entry plan watcher cycle completed.",
                    payload={**symbol_outcome, "cadence": cadence_profile},
                )
            )
        except Exception as exc:
            results.append(
                _finish_scheduler_run(
                    session,
                    row=row,
                    success=False,
                    message="Entry plan watcher cycle failed.",
                    payload={"symbol": symbol, "error": str(exc)},
                )
            )
    return {"workflow": ENTRY_PLAN_WATCHER_WORKFLOW, "results": results}


def is_interval_decision_due(session: Session, symbol: str | None = None) -> bool:
    settings_row = get_or_create_settings(session)
    if not settings_row.ai_enabled:
        return False
    if symbol is None:
        return len(get_due_interval_decision_symbols(session)) > 0
    orchestrator = TradingOrchestrator(session)
    effective = next(
        (item for item in get_effective_symbol_schedule(settings_row) if item.symbol == symbol.upper()),
        None,
    )
    if effective is None or not effective.enabled:
        return False
    cadence_profile = _symbol_cadence_profile(
        orchestrator,
        symbol=effective.symbol,
        timeframe=effective.timeframe,
    )
    schedule_details = _interval_decision_schedule_details(
        orchestrator,
        effective=effective,
        cadence_profile=cadence_profile,
    )
    cadence_minutes = int(
        schedule_details.get("scheduler_interval_minutes")
        or _cadence_minutes(
            cadence_profile,
            "decision_cycle_interval_minutes",
            effective.decision_cycle_interval_minutes,
        )
    )
    latest = _latest_symbol_workflow_run(session, INTERVAL_DECISION_WORKFLOW, effective.symbol)
    return _is_due(latest, timedelta(minutes=cadence_minutes))


def get_due_interval_decision_symbols(session: Session) -> list[str]:
    settings_row = get_or_create_settings(session)
    orchestrator = TradingOrchestrator(session)
    due: list[str] = []
    for effective in get_effective_symbol_schedule(settings_row):
        if not effective.enabled:
            continue
        cadence_profile = _symbol_cadence_profile(
            orchestrator,
            symbol=effective.symbol,
            timeframe=effective.timeframe,
        )
        schedule_details = _interval_decision_schedule_details(
            orchestrator,
            effective=effective,
            cadence_profile=cadence_profile,
        )
        cadence_minutes = int(
            schedule_details.get("scheduler_interval_minutes")
            or _cadence_minutes(
                cadence_profile,
                "decision_cycle_interval_minutes",
                effective.decision_cycle_interval_minutes,
            )
        )
        latest = _latest_symbol_workflow_run(session, INTERVAL_DECISION_WORKFLOW, effective.symbol)
        if _is_due(latest, timedelta(minutes=cadence_minutes)):
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
    due_effective = []
    for effective in get_effective_symbol_schedule(settings_row):
        if not effective.enabled:
            continue
        cadence_profile = _symbol_cadence_profile(
            orchestrator,
            symbol=effective.symbol,
            timeframe=effective.timeframe,
        )
        schedule_details = _interval_decision_schedule_details(
            orchestrator,
            effective=effective,
            cadence_profile=cadence_profile,
        )
        cadence_minutes = int(
            schedule_details.get("scheduler_interval_minutes")
            or _cadence_minutes(
                cadence_profile,
                "decision_cycle_interval_minutes",
                effective.decision_cycle_interval_minutes,
            )
        )
        latest = _latest_symbol_workflow_run(session, INTERVAL_DECISION_WORKFLOW, effective.symbol)
        if not _is_due(latest, timedelta(minutes=cadence_minutes)):
            continue
        due_effective.append((effective, cadence_profile, cadence_minutes, schedule_details))
    decision_plan = orchestrator.build_interval_decision_plan(
        symbols=[effective.symbol for effective, _cadence, _minutes, _details in due_effective],
        triggered_at=utcnow_naive(),
    )
    plan_lookup = {
        str(item.get("symbol") or "").upper(): dict(item)
        for item in decision_plan.get("plans", [])
        if isinstance(item, dict) and item.get("symbol")
    }
    for effective, cadence_profile, cadence_minutes, schedule_details in due_effective:
        row = _start_scheduler_run(
            session,
            workflow=INTERVAL_DECISION_WORKFLOW,
            schedule_window=_symbol_schedule_window(
                interval_minutes=cadence_minutes
            ),
            triggered_by=triggered_by,
            symbol=effective.symbol,
            next_run_at=utcnow_naive() + timedelta(minutes=cadence_minutes),
        )
        plan = plan_lookup.get(effective.symbol, {})
        trigger_payload = plan.get("trigger") if isinstance(plan.get("trigger"), dict) else None
        next_ai_review_due_at = plan.get("next_ai_review_due_at")
        last_ai_invoked_at = plan.get("last_ai_invoked_at")
        last_material_review_at = plan.get("last_material_review_at")
        dedupe_reason = str(plan.get("dedupe_reason") or "") or None
        forced_review_reason = str(plan.get("forced_review_reason") or "") or None
        applied_review_cadence_minutes = plan.get("applied_review_cadence_minutes")
        review_cadence_source = str(plan.get("review_cadence_source") or "") or None
        holding_profile_cadence_hint = (
            dict(plan.get("holding_profile_cadence_hint"))
            if isinstance(plan.get("holding_profile_cadence_hint"), dict)
            else dict(schedule_details.get("holding_profile_cadence_hint") or {})
            if isinstance(schedule_details.get("holding_profile_cadence_hint"), dict)
            else {}
        )
        cadence_fallback_reason = (
            str(plan.get("cadence_fallback_reason") or "") or None
        )
        max_review_age_minutes = plan.get("max_review_age_minutes")
        cadence_profile_summary = (
            dict(plan.get("cadence_profile_summary"))
            if isinstance(plan.get("cadence_profile_summary"), dict)
            else dict(schedule_details.get("cadence_profile_summary") or {})
            if isinstance(schedule_details.get("cadence_profile_summary"), dict)
            else {}
        )
        fingerprint_changed_fields = (
            list(plan.get("fingerprint_changed_fields"))
            if isinstance(plan.get("fingerprint_changed_fields"), list)
            else list(trigger_payload.get("fingerprint_changed_fields") or [])
            if isinstance(trigger_payload, dict)
            else []
        )
        last_ai_skip_reason = str(plan.get("last_ai_skip_reason") or "") or None
        try:
            if trigger_payload is None:
                record_audit_event(
                    session,
                    event_type="decision_ai_no_event",
                    entity_type="symbol",
                    entity_id=effective.symbol,
                    severity="info",
                    message="No deterministic entry or review trigger was detected for this interval cycle.",
                    payload={
                        "symbol": effective.symbol,
                        "cadence": cadence_profile,
                        "next_ai_review_due_at": next_ai_review_due_at,
                        "last_material_review_at": last_material_review_at,
                        "applied_review_cadence_minutes": applied_review_cadence_minutes,
                        "review_cadence_source": review_cadence_source,
                        "cadence_fallback_reason": cadence_fallback_reason,
                        "max_review_age_minutes": max_review_age_minutes,
                    },
                )
                results.append(
                    _finish_scheduler_run(
                        session,
                        row=row,
                        success=True,
                        message="Interval decision cycle skipped because no trigger was detected.",
                        payload={
                            "symbol": effective.symbol,
                            "status": "skipped",
                            "ai_review_status": "no_event",
                            "trigger": None,
                            "last_ai_trigger_reason": None,
                            "last_ai_invoked_at": last_ai_invoked_at,
                            "next_ai_review_due_at": next_ai_review_due_at,
                            "trigger_deduped": False,
                            "trigger_fingerprint": None,
                            "fingerprint_changed_fields": [],
                            "dedupe_reason": None,
                            "last_material_review_at": last_material_review_at,
                            "forced_review_reason": None,
                            "last_ai_skip_reason": last_ai_skip_reason or "NO_EVENT",
                            "applied_review_cadence_minutes": applied_review_cadence_minutes,
                            "review_cadence_source": review_cadence_source,
                            "holding_profile_cadence_hint": holding_profile_cadence_hint,
                            "cadence_fallback_reason": cadence_fallback_reason,
                            "max_review_age_minutes": max_review_age_minutes,
                            "cadence_profile_summary": cadence_profile_summary,
                            "cadence": cadence_profile,
                            "auto_resume": auto_resume_result,
                        },
                    )
                )
                continue
            if bool(plan.get("trigger_deduped")):
                record_audit_event(
                    session,
                    event_type="decision_ai_deduped",
                    entity_type="symbol",
                    entity_id=effective.symbol,
                    severity="info",
                    message="Repeated trigger fingerprint was deduplicated before AI review.",
                    payload={
                        "symbol": effective.symbol,
                        "trigger": trigger_payload,
                        "next_ai_review_due_at": next_ai_review_due_at,
                        "dedupe_reason": dedupe_reason,
                        "last_material_review_at": last_material_review_at,
                        "applied_review_cadence_minutes": applied_review_cadence_minutes,
                        "review_cadence_source": review_cadence_source,
                        "cadence_fallback_reason": cadence_fallback_reason,
                        "max_review_age_minutes": max_review_age_minutes,
                    },
                )
                results.append(
                    _finish_scheduler_run(
                        session,
                        row=row,
                        success=True,
                        message="Interval decision cycle deduped an unchanged trigger.",
                        payload={
                            "symbol": effective.symbol,
                            "status": "skipped",
                            "ai_review_status": "deduped",
                            "trigger": trigger_payload,
                            "last_ai_trigger_reason": trigger_payload.get("trigger_reason"),
                            "last_ai_invoked_at": last_ai_invoked_at,
                            "next_ai_review_due_at": next_ai_review_due_at,
                            "trigger_deduped": True,
                            "trigger_fingerprint": trigger_payload.get("trigger_fingerprint"),
                            "fingerprint_changed_fields": fingerprint_changed_fields,
                            "dedupe_reason": dedupe_reason or "TRIGGER_FINGERPRINT_UNCHANGED",
                            "last_material_review_at": last_material_review_at,
                            "forced_review_reason": forced_review_reason,
                            "last_ai_skip_reason": last_ai_skip_reason or "TRIGGER_DEDUPED",
                            "applied_review_cadence_minutes": applied_review_cadence_minutes,
                            "review_cadence_source": review_cadence_source,
                            "holding_profile_cadence_hint": holding_profile_cadence_hint,
                            "cadence_fallback_reason": cadence_fallback_reason,
                            "max_review_age_minutes": max_review_age_minutes,
                            "cadence_profile_summary": cadence_profile_summary,
                            "cadence": cadence_profile,
                            "auto_resume": auto_resume_result,
                        },
                    )
                )
                continue
            if str(trigger_payload.get("trigger_reason") or "") == "periodic_backstop_due":
                record_audit_event(
                    session,
                    event_type="decision_ai_backstop_due",
                    entity_type="symbol",
                    entity_id=effective.symbol,
                    severity="info",
                    message="Periodic AI backstop review is due for this symbol.",
                    payload={
                        "symbol": effective.symbol,
                        "trigger": trigger_payload,
                        "next_ai_review_due_at": next_ai_review_due_at,
                        "forced_review_reason": forced_review_reason,
                        "last_material_review_at": last_material_review_at,
                        "applied_review_cadence_minutes": applied_review_cadence_minutes,
                        "review_cadence_source": review_cadence_source,
                        "cadence_fallback_reason": cadence_fallback_reason,
                        "max_review_age_minutes": max_review_age_minutes,
                    },
                )
            outcome = orchestrator.run_decision_cycle(
                symbol=effective.symbol,
                timeframe=effective.timeframe,
                trigger_event="realtime_cycle",
                auto_resume_checked=True,
                exchange_sync_checked=True,
                include_inline_position_management=False,
                selection_context=(
                    dict(plan.get("selection_context"))
                    if isinstance(plan.get("selection_context"), dict)
                    else None
                ),
                review_trigger=trigger_payload,
            )
            results.append(
                _finish_scheduler_run(
                    session,
                    row=row,
                    success=True,
                    message="Interval decision cycle completed.",
                    payload={
                        **outcome,
                        "symbol": effective.symbol,
                        "trigger": trigger_payload,
                        "cadence": cadence_profile,
                        "auto_resume": auto_resume_result,
                    },
                )
            )
        except Exception as exc:
            results.append(
                _finish_scheduler_run(
                    session,
                    row=row,
                    success=False,
                    message="Interval decision cycle failed.",
                    payload={
                        "symbol": effective.symbol,
                        "error": str(exc),
                        "trigger": trigger_payload,
                    },
                )
            )
    return {
        "workflow": INTERVAL_DECISION_WORKFLOW,
        "results": results,
        "auto_resume": auto_resume_result,
        "candidate_selection": decision_plan.get("candidate_selection", {}),
    }


def run_due_exchange_sync_cycle(session: Session) -> dict[str, object] | None:
    if not is_exchange_sync_due(session):
        return None
    return run_exchange_sync_cycle(session, triggered_by="scheduler")


def run_due_interval_decision_cycle(session: Session) -> dict[str, object] | None:
    if not is_interval_decision_due(session):
        return None
    return run_interval_decision_cycle(session, triggered_by="scheduler")


def run_due_entry_plan_watcher_cycle(session: Session) -> dict[str, object] | None:
    if not get_due_entry_plan_symbols(session):
        return None
    return run_entry_plan_watcher_cycle(session, triggered_by="scheduler")


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
    entry_plan_watcher = run_due_entry_plan_watcher_cycle(session)
    if entry_plan_watcher is not None and entry_plan_watcher.get("results"):
        outputs.append(entry_plan_watcher)
    decisions = run_due_interval_decision_cycle(session)
    if decisions is not None and decisions.get("results"):
        outputs.append(decisions)
    return outputs


def run_due_windows(session: Session) -> list[dict[str, object]]:
    # Out-of-scope auxiliary review workflows are disabled for current live-core scope.
    return []
