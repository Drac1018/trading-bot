from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, sessionmaker

from trading_mvp.models import MarketSnapshot, PendingEntryPlan, SchedulerRun
from trading_mvp.services.account import get_open_positions
from trading_mvp.services.audit import record_audit_event, record_health_event
from trading_mvp.services.orchestrator import TradingOrchestrator
from trading_mvp.services.pause_control import attempt_auto_resume
from trading_mvp.services.runtime_state import build_sync_freshness_summary
from trading_mvp.services.settings import (
    get_effective_symbol_schedule,
    get_or_create_settings,
)
from trading_mvp.time_utils import ensure_utc_aware, parse_utc_datetime, utcnow_naive

WINDOW_HOURS = {"1h": 1}

EXCHANGE_SYNC_WORKFLOW = "exchange_sync_cycle"
MARKET_REFRESH_WORKFLOW = "market_refresh_cycle"
POSITION_MANAGEMENT_WORKFLOW = "position_management_cycle"
INTERVAL_DECISION_WORKFLOW = "interval_decision_cycle"
ENTRY_PLAN_WATCHER_WORKFLOW = "entry_plan_watcher_cycle"
RELEASE_ENRICHMENT_WATCH_WORKFLOW = "release_enrichment_watch_cycle"
READ_REFRESH_SYNC_DEBOUNCE_SECONDS = 30
PRE_DECISION_SYNC_MIN_FRESH_SECONDS = 60
RELEASE_ENRICHMENT_RETRY_SECONDS = 15
RELEASE_ENRICHMENT_WATCH_WINDOW_SECONDS = 120
STALE_RUNNING_SCHEDULER_RUN_SECONDS = 30 * 60
BLS_RELEASE_WATCH_EVENT_NAMES = {
    "Consumer Price Index",
    "Producer Price Index",
    "Employment Situation",
}
BEA_RELEASE_WATCH_EVENT_NAMES = {
    "Gross Domestic Product",
    "Personal Consumption Expenditures",
}


def _next_window_run(window: str, from_time: datetime | None = None) -> datetime:
    base = from_time or utcnow_naive()
    return base + timedelta(hours=WINDOW_HOURS[window])


def _symbol_schedule_window(interval_seconds: int | None = None, interval_minutes: int | None = None) -> str:
    if interval_seconds is not None:
        return f"{int(interval_seconds)}s"
    if interval_minutes is not None:
        return f"{int(interval_minutes)}m"
    return "unknown"


def _active_position_suppression_payload_from_plan(plan: dict[str, Any]) -> dict[str, Any]:
    if not {
        "suppression_active",
        "suppression_reason_code",
        "allow_same_side_add_on",
        "allowed_add_on_side",
    }.intersection(plan.keys()):
        return {}
    allowed_add_on_side = str(plan.get("allowed_add_on_side") or "").lower()
    return {
        "suppression_active": bool(plan.get("suppression_active")),
        "suppression_reason_code": str(plan.get("suppression_reason_code") or "") or None,
        "allow_same_side_add_on": bool(plan.get("allow_same_side_add_on")),
        "allowed_add_on_side": (
            allowed_add_on_side if allowed_add_on_side in {"long", "short"} else None
        ),
    }


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
        normalized = ensure_utc_aware(value)
        return normalized.replace(tzinfo=None) if normalized is not None else None
    parsed = parse_utc_datetime(value)
    if parsed is not None:
        return parsed.replace(tzinfo=None)
    return None


def _latest_symbol_market_snapshot(
    session: Session,
    *,
    symbol: str,
    timeframe: str,
) -> MarketSnapshot | None:
    return session.scalar(
        select(MarketSnapshot)
        .where(MarketSnapshot.symbol == symbol.upper(), MarketSnapshot.timeframe == timeframe)
        .order_by(desc(MarketSnapshot.snapshot_time))
        .limit(1)
    )


def _extract_market_event_context(market_row: MarketSnapshot | None) -> dict[str, object]:
    if market_row is None or not isinstance(market_row.payload, dict):
        return {}
    payload = market_row.payload.get("event_context")
    return dict(payload) if isinstance(payload, dict) else {}


def _matching_release_event(
    raw_context: dict[str, object],
    *,
    event_name: str,
    event_at: datetime,
) -> dict[str, object]:
    raw_events = raw_context.get("events")
    if isinstance(raw_events, list):
        for raw_event in raw_events:
            if not isinstance(raw_event, dict):
                continue
            raw_event_name = str(raw_event.get("event_name") or "").strip()
            raw_event_at = _coerce_datetime(raw_event.get("event_at"))
            if raw_event_name == event_name and raw_event_at == event_at:
                return dict(raw_event)
    return {
        "event_name": event_name,
        "event_at": event_at.isoformat(),
        "release_enrichment": raw_context.get("release_enrichment") if isinstance(raw_context.get("release_enrichment"), dict) else {},
        "enrichment_vendors": raw_context.get("enrichment_vendors") if isinstance(raw_context.get("enrichment_vendors"), list) else [],
    }


def _event_has_vendor_enrichment(event_payload: dict[str, object], *, vendor_name: str) -> bool:
    release_enrichment = event_payload.get("release_enrichment")
    if not isinstance(release_enrichment, dict):
        return False
    vendor_payload = release_enrichment.get(vendor_name)
    return isinstance(vendor_payload, dict) and bool(vendor_payload)


def _supported_release_watch_event_names(settings_row: object) -> set[str]:
    names: set[str] = set()
    if getattr(settings_row, "event_source_bls_enrichment_url", None):
        names.update(BLS_RELEASE_WATCH_EVENT_NAMES)
    if getattr(settings_row, "event_source_bea_enrichment_url", None):
        names.update(BEA_RELEASE_WATCH_EVENT_NAMES)
    return names


def _release_watch_candidate(
    session: Session,
    *,
    settings_row: object,
    symbol: str,
    timeframe: str,
    now: datetime,
) -> dict[str, object] | None:
    supported_event_names = _supported_release_watch_event_names(settings_row)
    if not supported_event_names:
        return None

    market_row = _latest_symbol_market_snapshot(session, symbol=symbol, timeframe=timeframe)
    raw_context = _extract_market_event_context(market_row)
    event_name = str(raw_context.get("next_event_name") or "").strip()
    event_at = _coerce_datetime(raw_context.get("next_event_at"))
    if not event_name or event_at is None or event_name not in supported_event_names:
        return None

    seconds_since_release = (now - event_at).total_seconds()
    if seconds_since_release < 0 or seconds_since_release > RELEASE_ENRICHMENT_WATCH_WINDOW_SECONDS:
        return None

    event_payload = _matching_release_event(raw_context, event_name=event_name, event_at=event_at)
    if (
        event_name in BLS_RELEASE_WATCH_EVENT_NAMES
        and getattr(settings_row, "event_source_bls_enrichment_url", None)
        and _event_has_vendor_enrichment(event_payload, vendor_name="bls")
    ):
        return None
    if (
        event_name in BEA_RELEASE_WATCH_EVENT_NAMES
        and getattr(settings_row, "event_source_bea_enrichment_url", None)
        and _event_has_vendor_enrichment(event_payload, vendor_name="bea")
    ):
        return None

    latest_watch = _latest_symbol_workflow_run(session, RELEASE_ENRICHMENT_WATCH_WORKFLOW, symbol)
    latest_outcome = latest_watch.outcome if latest_watch is not None and isinstance(latest_watch.outcome, dict) else {}
    latest_event_name = str(latest_outcome.get("event_name") or "").strip()
    latest_event_at = _coerce_datetime(latest_outcome.get("event_at"))
    if (
        latest_watch is not None
        and latest_watch.next_run_at is not None
        and latest_watch.next_run_at > now
        and latest_event_name == event_name
        and latest_event_at == event_at
    ):
        return None

    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "event_name": event_name,
        "event_at": event_at,
        "seconds_since_release": int(seconds_since_release),
        "snapshot_time": market_row.snapshot_time.isoformat() if market_row is not None else None,
    }


def _sync_summary_needs_refresh(
    sync_freshness_summary: dict[str, object],
    *,
    min_fresh_for_seconds: int = 0,
) -> bool:
    for payload in sync_freshness_summary.values():
        if not isinstance(payload, dict):
            continue
        if bool(payload.get("stale")) or bool(payload.get("incomplete")):
            return True
        if min_fresh_for_seconds <= 0:
            continue
        freshness_seconds = payload.get("freshness_seconds")
        stale_after_seconds = payload.get("stale_after_seconds")
        if not isinstance(freshness_seconds, (int, float)) or not isinstance(stale_after_seconds, (int, float)):
            continue
        if int(stale_after_seconds) - int(freshness_seconds) <= min_fresh_for_seconds:
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


def _commit_before_external_scheduler_work(session: Session) -> None:
    if session.in_transaction():
        session.commit()


def _rollback_scheduler_session(session: Session) -> None:
    try:
        session.rollback()
    except Exception:
        pass


def _record_interval_decision_sync_failure(
    session: Session,
    *,
    symbol: str | None,
    stage: str,
    error: Exception,
) -> None:
    payload = {
        "workflow": INTERVAL_DECISION_WORKFLOW,
        "symbol": symbol,
        "stage": stage,
        "error": str(error),
    }
    entity_type = "symbol" if symbol else "scheduler"
    entity_id = symbol or INTERVAL_DECISION_WORKFLOW
    for _attempt in range(2):
        _rollback_scheduler_session(session)
        try:
            record_audit_event(
                session,
                event_type="interval_decision_pre_sync_failed",
                entity_type=entity_type,
                entity_id=entity_id,
                severity="warning",
                message="Interval decision pre-decision exchange sync failed.",
                payload=payload,
            )
            record_health_event(
                session,
                component="scheduler",
                status="error",
                message="Interval decision pre-decision exchange sync failed.",
                payload=payload,
            )
            session.flush()
            return
        except Exception:
            continue
    _rollback_scheduler_session(session)


def _try_pre_decision_exchange_sync(
    session: Session,
    *,
    triggered_by: str,
    symbol: str | None,
    stage: str,
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return maybe_refresh_exchange_sync_freshness(session, triggered_by=triggered_by), None
    except Exception as exc:
        _record_interval_decision_sync_failure(
            session,
            symbol=symbol,
            stage=stage,
            error=exc,
        )
        return None, str(exc)


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
            payload={"workflow": row.workflow, **payload},
        )
    session.flush()
    result = {
        "scheduler_run_id": row.id,
        "workflow": row.workflow,
        "status": row.status,
        "outcome": payload,
    }
    for key, value in payload.items():
        result.setdefault(key, value)
    return result


def abandon_stale_scheduler_runs(
    session: Session,
    *,
    older_than_seconds: int = STALE_RUNNING_SCHEDULER_RUN_SECONDS,
    now: datetime | None = None,
) -> int:
    cutoff = (now or utcnow_naive()) - timedelta(seconds=max(60, older_than_seconds))
    rows = list(
        session.scalars(
            select(SchedulerRun)
            .where(SchedulerRun.status == "running")
            .where(SchedulerRun.created_at < cutoff)
            .order_by(SchedulerRun.created_at)
        )
    )
    abandoned_at = now or utcnow_naive()
    for row in rows:
        previous_outcome = dict(row.outcome or {})
        row.status = "abandoned"
        row.outcome = {
            **previous_outcome,
            "abandoned_reason": "STALE_RUNNING_SCHEDULER_RUN",
            "abandoned_at": abandoned_at.isoformat(),
        }
        session.add(row)
    if rows:
        record_health_event(
            session,
            component="scheduler",
            status="warning",
            message="Stale running scheduler rows were marked abandoned.",
            payload={
                "abandoned_count": len(rows),
                "older_than_seconds": max(60, older_than_seconds),
                "scheduler_run_ids": [row.id for row in rows],
            },
        )
    return len(rows)


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
    schedule_window = _symbol_schedule_window(interval_seconds=interval_seconds)
    next_run_at = utcnow_naive() + timedelta(seconds=interval_seconds)
    try:
        orchestrator = TradingOrchestrator(session)
        outcome = orchestrator.run_exchange_sync_cycle(trigger_event=triggered_by)
    except Exception as exc:
        row = _start_scheduler_run(
            session,
            workflow=EXCHANGE_SYNC_WORKFLOW,
            schedule_window=schedule_window,
            triggered_by=triggered_by,
            next_run_at=next_run_at,
        )
        return _finish_scheduler_run(
            session,
            row=row,
            success=False,
            message="Exchange sync cycle failed.",
            payload={"error": str(exc)},
        )
    success = str(outcome.get("status")) != "error"
    row = _start_scheduler_run(
        session,
        workflow=EXCHANGE_SYNC_WORKFLOW,
        schedule_window=schedule_window,
        triggered_by=triggered_by,
        next_run_at=next_run_at,
    )
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
    if triggered_by.startswith("api_") and settings_row.trading_paused:
        return None
    if not settings_row.binance_api_key_encrypted or not settings_row.binance_api_secret_encrypted:
        return None
    sync_freshness_summary = build_sync_freshness_summary(settings_row)
    min_fresh_for_seconds = (
        PRE_DECISION_SYNC_MIN_FRESH_SECONDS
        if "pre_decision" in str(triggered_by or "")
        else 0
    )
    if not _sync_summary_needs_refresh(
        sync_freshness_summary,
        min_fresh_for_seconds=min_fresh_for_seconds,
    ):
        return None
    latest_attempt_at = _latest_sync_attempt_at(sync_freshness_summary)
    now = utcnow_naive()
    if latest_attempt_at is not None and (now - latest_attempt_at).total_seconds() < READ_REFRESH_SYNC_DEBOUNCE_SECONDS:
        return None
    _commit_before_external_scheduler_work(session)
    try:
        return run_exchange_sync_cycle(session, triggered_by=triggered_by)
    except Exception:
        session.rollback()
        raise


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
        schedule_window = _symbol_schedule_window(interval_minutes=cadence_minutes)
        next_run_at = utcnow_naive() + timedelta(minutes=cadence_minutes)
        try:
            cycle = orchestrator.run_market_refresh_cycle(
                symbols=[effective.symbol],
                timeframe=effective.timeframe,
                trigger_event=triggered_by,
                include_exchange_sync=False,
                auto_resume_checked=True,
            )
            symbol_outcome = cycle["results"][0]
            row = _start_scheduler_run(
                session,
                workflow=MARKET_REFRESH_WORKFLOW,
                schedule_window=schedule_window,
                triggered_by=triggered_by,
                symbol=effective.symbol,
                next_run_at=next_run_at,
            )
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
            row = _start_scheduler_run(
                session,
                workflow=MARKET_REFRESH_WORKFLOW,
                schedule_window=schedule_window,
                triggered_by=triggered_by,
                symbol=effective.symbol,
                next_run_at=next_run_at,
            )
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
    pre_decision_exchange_sync, pre_decision_exchange_sync_error = _try_pre_decision_exchange_sync(
        session,
        triggered_by=f"{triggered_by}:pre_decision",
        symbol=None,
        stage="cycle_pre_decision_exchange_sync",
    )
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
    plan_symbols = [effective.symbol for effective, _cadence, _minutes, _details in due_effective]
    try:
        _commit_before_external_scheduler_work(session)
        decision_plan = orchestrator.build_interval_decision_plan(
            symbols=plan_symbols,
            triggered_at=utcnow_naive(),
        )
    except Exception as exc:
        _rollback_scheduler_session(session)
        decision_plan_error = str(exc)
        for effective, _cadence_profile, cadence_minutes, _schedule_details in due_effective:
            row = _start_scheduler_run(
                session,
                workflow=INTERVAL_DECISION_WORKFLOW,
                schedule_window=_symbol_schedule_window(
                    interval_minutes=cadence_minutes
                ),
                triggered_by=triggered_by,
                symbol=effective.symbol,
                next_run_at=utcnow_naive(),
            )
            results.append(
                _finish_scheduler_run(
                    session,
                    row=row,
                    success=False,
                    message="Interval decision cycle failed while building decision plan.",
                    payload={
                        "symbol": effective.symbol,
                        "stage": "decision_plan_build",
                        "error": decision_plan_error,
                        "auto_resume": auto_resume_result,
                        "pre_decision_exchange_sync": pre_decision_exchange_sync,
                        "pre_decision_exchange_sync_error": pre_decision_exchange_sync_error,
                    },
                )
            )
        return {
            "workflow": INTERVAL_DECISION_WORKFLOW,
            "results": results,
            "auto_resume": auto_resume_result,
            "pre_decision_exchange_sync": pre_decision_exchange_sync,
            "pre_decision_exchange_sync_error": pre_decision_exchange_sync_error,
            "decision_plan_error": decision_plan_error,
            "candidate_selection": {},
        }
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
        next_ai_review_due_at = None
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
        plan_ai_call_policy = (
            dict(plan.get("ai_call_policy"))
            if isinstance(plan.get("ai_call_policy"), dict)
            else {}
        )
        active_position_suppression_payload = _active_position_suppression_payload_from_plan(plan)
        try:
            if trigger_payload is None:
                policy_reason = str(plan_ai_call_policy.get("reason") or "") or None
                audit_event_type = "decision_ai_skipped" if policy_reason else "decision_ai_no_event"
                record_audit_event(
                    session,
                    event_type=audit_event_type,
                    entity_type="symbol",
                    entity_id=effective.symbol,
                    severity="info",
                    message=(
                        "AI inference was skipped before decision-cycle execution."
                        if policy_reason
                        else "No deterministic entry or review trigger was detected for this interval cycle."
                    ),
                    payload={
                        "symbol": effective.symbol,
                        "ai_call_event": plan_ai_call_policy.get("ai_call_event"),
                        "reason": policy_reason,
                        "hard_skip_ai": plan_ai_call_policy.get("hard_skip_ai"),
                        "skip_category": plan_ai_call_policy.get("skip_category"),
                        "scope": plan_ai_call_policy.get("scope"),
                        "hard_skip_reason_codes": list(
                            plan_ai_call_policy.get("hard_skip_reason_codes") or []
                        ),
                        "last_ai_skip_reason": last_ai_skip_reason or "NO_EVENT",
                        "ai_call_policy": plan_ai_call_policy or None,
                        "cadence": cadence_profile,
                        "next_ai_review_due_at": next_ai_review_due_at,
                        "last_material_review_at": last_material_review_at,
                        "applied_review_cadence_minutes": applied_review_cadence_minutes,
                        "review_cadence_source": review_cadence_source,
                        "cadence_fallback_reason": cadence_fallback_reason,
                        "max_review_age_minutes": max_review_age_minutes,
                        **active_position_suppression_payload,
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
                            "ai_review_status": "skipped" if policy_reason else "no_event",
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
                            "pre_decision_exchange_sync": pre_decision_exchange_sync,
                            "pre_decision_exchange_sync_error": pre_decision_exchange_sync_error,
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
                        **active_position_suppression_payload,
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
                            "pre_decision_exchange_sync": pre_decision_exchange_sync,
                            "pre_decision_exchange_sync_error": pre_decision_exchange_sync_error,
                        },
                    )
                )
                continue
            _commit_before_external_scheduler_work(session)
            (
                decision_pre_decision_exchange_sync,
                decision_pre_decision_exchange_sync_error,
            ) = _try_pre_decision_exchange_sync(
                session,
                triggered_by=f"{triggered_by}:pre_decision:{effective.symbol}",
                symbol=effective.symbol,
                stage="symbol_pre_decision_exchange_sync",
            )
            if decision_pre_decision_exchange_sync_error is not None:
                row.next_run_at = utcnow_naive()
                results.append(
                    _finish_scheduler_run(
                        session,
                        row=row,
                        success=False,
                        message="Interval decision cycle failed during pre-decision exchange sync.",
                        payload={
                            "symbol": effective.symbol,
                            "stage": "symbol_pre_decision_exchange_sync",
                            "error": decision_pre_decision_exchange_sync_error,
                            "trigger": trigger_payload,
                            "pre_decision_exchange_sync": pre_decision_exchange_sync,
                            "pre_decision_exchange_sync_error": pre_decision_exchange_sync_error,
                        },
                    )
                )
                continue
            _commit_before_external_scheduler_work(session)
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
                        "pre_decision_exchange_sync": decision_pre_decision_exchange_sync
                        or pre_decision_exchange_sync,
                        "pre_decision_exchange_sync_error": pre_decision_exchange_sync_error,
                    },
                )
            )
        except Exception as exc:
            _rollback_scheduler_session(session)
            row.next_run_at = utcnow_naive()
            results.append(
                _finish_scheduler_run(
                    session,
                    row=row,
                    success=False,
                    message="Interval decision cycle failed.",
                    payload={
                        "symbol": effective.symbol,
                        "stage": "decision_cycle",
                        "error": str(exc),
                        "trigger": trigger_payload,
                        "pre_decision_exchange_sync": pre_decision_exchange_sync,
                        "pre_decision_exchange_sync_error": pre_decision_exchange_sync_error,
                    },
                )
            )
    return {
        "workflow": INTERVAL_DECISION_WORKFLOW,
        "results": results,
        "auto_resume": auto_resume_result,
        "pre_decision_exchange_sync": pre_decision_exchange_sync,
        "pre_decision_exchange_sync_error": pre_decision_exchange_sync_error,
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


def run_release_enrichment_watch_cycle(session: Session, triggered_by: str = "scheduler") -> dict[str, object]:
    settings_row = get_or_create_settings(session)
    supported_event_names = _supported_release_watch_event_names(settings_row)
    if not supported_event_names:
        return {"workflow": RELEASE_ENRICHMENT_WATCH_WORKFLOW, "results": []}

    orchestrator = TradingOrchestrator(session)
    now = utcnow_naive()
    results: list[dict[str, object]] = []
    for effective in get_effective_symbol_schedule(settings_row):
        if not effective.enabled:
            continue
        candidate = _release_watch_candidate(
            session,
            settings_row=settings_row,
            symbol=effective.symbol,
            timeframe=effective.timeframe,
            now=now,
        )
        if candidate is None:
            continue
        row = _start_scheduler_run(
            session,
            workflow=RELEASE_ENRICHMENT_WATCH_WORKFLOW,
            schedule_window=_symbol_schedule_window(interval_seconds=RELEASE_ENRICHMENT_RETRY_SECONDS),
            triggered_by=triggered_by,
            symbol=effective.symbol,
            next_run_at=utcnow_naive() + timedelta(seconds=RELEASE_ENRICHMENT_RETRY_SECONDS),
        )
        try:
            outcome = orchestrator.run_market_refresh_cycle(
                symbols=[effective.symbol],
                timeframe=effective.timeframe,
                trigger_event="release_watch",
                include_exchange_sync=False,
                auto_resume_checked=True,
            )
            symbol_outcome = outcome["results"][0] if outcome.get("results") else {
                "symbol": effective.symbol,
                "status": "no_result",
            }
            results.append(
                _finish_scheduler_run(
                    session,
                    row=row,
                    success=True,
                    message="Immediate release enrichment watch refreshed market snapshot.",
                    payload={
                        **candidate,
                        "event_at": candidate["event_at"].isoformat(),
                        "refresh_outcome": symbol_outcome,
                    },
                )
            )
        except Exception as exc:
            results.append(
                _finish_scheduler_run(
                    session,
                    row=row,
                    success=False,
                    message="Immediate release enrichment watch failed.",
                    payload={
                        **candidate,
                        "event_at": candidate["event_at"].isoformat(),
                        "error": str(exc),
                    },
                )
            )
    return {"workflow": RELEASE_ENRICHMENT_WATCH_WORKFLOW, "results": results}


def run_due_operational_cycles(
    session: Session,
    *,
    include_exchange_sync: bool = True,
    commit_between: bool = False,
    continue_on_error: bool = False,
    session_factory: Callable[[], Session] | sessionmaker[Session] | None = None,
) -> list[dict[str, object]]:
    outputs: list[dict[str, object]] = []

    def run_step(workflow: str, action: Callable[[Session], object | None]) -> object | None:
        step_session = session_factory() if session_factory is not None else session
        owns_session = step_session is not session
        try:
            result = action(step_session)
            if commit_between or owns_session:
                step_session.commit()
            return result
        except Exception as exc:
            step_session.rollback()
            if not continue_on_error:
                raise
            try:
                record_health_event(
                    step_session,
                    component="scheduler",
                    status="error",
                    message="Background scheduler workflow failed.",
                    payload={"workflow": workflow, "error": str(exc)},
                )
                step_session.commit()
            except Exception:
                step_session.rollback()
            return None
        finally:
            if owns_session:
                step_session.close()

    if include_exchange_sync:
        exchange = run_step(EXCHANGE_SYNC_WORKFLOW, lambda step_session: run_due_exchange_sync_cycle(step_session))
        if exchange is not None:
            outputs.append(exchange)  # type: ignore[arg-type]
    market = run_step(
        MARKET_REFRESH_WORKFLOW,
        lambda step_session: run_market_refresh_cycle(step_session, triggered_by="scheduler"),
    )
    if isinstance(market, dict) and market["results"]:
        outputs.append(market)
    release_watch = run_step(
        RELEASE_ENRICHMENT_WATCH_WORKFLOW,
        lambda step_session: run_release_enrichment_watch_cycle(step_session, triggered_by="scheduler"),
    )
    if isinstance(release_watch, dict) and release_watch["results"]:
        outputs.append(release_watch)
    position_management = run_step(
        POSITION_MANAGEMENT_WORKFLOW,
        lambda step_session: run_position_management_cycle(step_session, triggered_by="scheduler"),
    )
    if isinstance(position_management, dict) and position_management["results"]:
        outputs.append(position_management)
    entry_plan_watcher = run_step(
        ENTRY_PLAN_WATCHER_WORKFLOW,
        lambda step_session: run_due_entry_plan_watcher_cycle(step_session),
    )
    if isinstance(entry_plan_watcher, dict) and entry_plan_watcher.get("results"):
        outputs.append(entry_plan_watcher)
    decisions = run_step(INTERVAL_DECISION_WORKFLOW, lambda step_session: run_due_interval_decision_cycle(step_session))
    if isinstance(decisions, dict) and decisions.get("results"):
        outputs.append(decisions)
    return outputs


def run_due_windows(session: Session) -> list[dict[str, object]]:
    # Out-of-scope auxiliary review workflows are disabled for current live-core scope.
    return []
