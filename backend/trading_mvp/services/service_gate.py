from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import Select, func, select, text
from sqlalchemy.orm import Session

from trading_mvp.models import (
    AuditEvent,
    Order,
    PendingEntryPlan,
    Position,
    SchedulerRun,
    Setting,
    SystemHealthEvent,
)
from trading_mvp.services.runtime_state import (
    get_market_stream_detail,
    get_reconciliation_detail,
    list_unresolved_submission_guards,
)
from trading_mvp.time_utils import utcnow_naive

ACTIVE_PENDING_ENTRY_PLAN_STATUS = "armed"
TRIGGERED_PENDING_ENTRY_PLAN_STATUS = "triggered"
ACTIVE_LIVE_ORDER_STATUSES = frozenset(
    {
        "new",
        "pending",
        "submitted",
        "open",
        "partially_filled",
    }
)
TERMINAL_TRIGGERED_EXECUTION_STATUSES = frozenset(
    {
        "filled",
        "shadow",
        "blocked",
        "rejected",
        "error",
        "submission_unknown",
        "canceled",
        "cancelled",
        "expired",
        "skipped",
    }
)
HEALTH_BLOCKING_STATUSES = frozenset({"error", "degraded"})
SCHEDULER_NON_BLOCKING_STATUSES = frozenset({"success", "skipped"})
REDIS_CACHE_UNAVAILABLE_BLOCKER = "redis_cache_unavailable"
REDIS_CACHE_UNAVAILABLE_ROOT_CAUSE = "REDIS_CACHE_UNAVAILABLE"
EXCHANGE_AUTH_PERMISSION_REJECTED = "EXCHANGE_AUTH_PERMISSION_REJECTED"
RECENT_SCHEDULER_NON_SUCCESS_ROOT_CAUSE = "RECENT_SCHEDULER_NON_SUCCESS"
RECENT_HEALTH_ERRORS_ROOT_CAUSE = "RECENT_HEALTH_ERRORS"
DB_CONNECTION_LOST_ROOT_CAUSE = "DB_CONNECTION_LOST"
DB_IDLE_IN_TRANSACTION_TIMEOUT_ROOT_CAUSE = "DB_IDLE_IN_TRANSACTION_TIMEOUT"
_ROOT_CAUSE_BOOLEAN_FLAG_CODES = {
    "db_connection_lost": DB_CONNECTION_LOST_ROOT_CAUSE,
}
_ROOT_CAUSE_CODE_KEYS = (
    "root_cause_code",
    "reason_code",
    "blocked_reason_code",
    "error_category",
    "error_code",
    "code",
)
_ROOT_CAUSE_TEXT_KEYS = (
    "error",
    "message",
    "msg",
    "detail",
    "reason",
    "exception",
)


def active_pending_entry_plan_statement(*, symbols: list[str] | tuple[str, ...] | None = None) -> Select[tuple[PendingEntryPlan]]:
    statement = select(PendingEntryPlan).where(PendingEntryPlan.plan_status == ACTIVE_PENDING_ENTRY_PLAN_STATUS)
    if symbols:
        statement = statement.where(PendingEntryPlan.symbol.in_([symbol.upper() for symbol in symbols]))
    return statement


def pending_entry_plan_execution_status(row: PendingEntryPlan) -> str | None:
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    execution_result = metadata.get("execution_result")
    if not isinstance(execution_result, dict):
        return None
    status = str(execution_result.get("status") or "").strip().lower()
    return status or None


def pending_entry_plan_gate_state(
    row: PendingEntryPlan,
    *,
    now: datetime | None = None,
) -> str:
    status = str(row.plan_status or "").strip().lower()
    if status == ACTIVE_PENDING_ENTRY_PLAN_STATUS:
        return "active_waiting"
    if status != TRIGGERED_PENDING_ENTRY_PLAN_STATUS:
        return "inactive_history"
    execution_status = pending_entry_plan_execution_status(row)
    if execution_status in TERMINAL_TRIGGERED_EXECUTION_STATUSES:
        return "triggered_terminal_history"
    comparison_now = now or utcnow_naive()
    if row.expires_at is not None and row.expires_at <= comparison_now:
        return "triggered_stale_history"
    return "triggered_non_terminal"


def pending_entry_plan_blocks_service_switch(row: PendingEntryPlan) -> bool:
    return pending_entry_plan_gate_state(row) in {"active_waiting", "triggered_non_terminal"}


def _dt(value: object) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None


def _reason_code_list(value: object) -> list[str]:
    if isinstance(value, str):
        normalized = value.strip()
        return [normalized] if normalized else []
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for item in value:
        normalized = str(item or "").strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _pending_plan_reason_codes(row: PendingEntryPlan, metadata: dict[str, object]) -> list[str]:
    tracking = metadata.get("last_confirmation_tracking")
    tracking_payload = tracking if isinstance(tracking, dict) else {}
    reason_codes: list[str] = []
    for value in (
        metadata.get("last_watch_blocked_reason_codes"),
        metadata.get("source_blocked_reason_codes"),
        metadata.get("blocked_reason_codes"),
        metadata.get("reason_codes"),
        metadata.get("root_cause_codes"),
        tracking_payload.get("blocked_reason_codes"),
        tracking_payload.get("reason_codes"),
        tracking_payload.get("root_cause_codes"),
        tracking_payload.get("confirmation_failed_reason"),
        tracking_payload.get("plan_cancel_reason"),
        row.canceled_reason,
    ):
        for code in _reason_code_list(value):
            if code not in reason_codes:
                reason_codes.append(code)
    return reason_codes


def _normalize_root_cause_code(value: object) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return EXCHANGE_AUTH_PERMISSION_REJECTED if value == -2015 else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    upper = text.upper()
    if (
        EXCHANGE_AUTH_PERMISSION_REJECTED in upper
        or "INVALID API-KEY" in upper
        or "PERMISSIONS FOR ACTION" in upper
        or "BINANCE ERROR -2015" in upper
        or upper == "-2015"
    ):
        return EXCHANGE_AUTH_PERMISSION_REJECTED
    compact = upper.replace("_", "").replace("-", "").replace(" ", "")
    if (
        "IDLEINTRANSACTIONSESSIONTIMEOUT" in compact
        or "IDLEINTRANSACTIONTIMEOUT" in compact
    ):
        return DB_IDLE_IN_TRANSACTION_TIMEOUT_ROOT_CAUSE
    if upper.replace("_", "").replace("-", "").replace(":", "").isalnum() and " " not in upper:
        return upper
    return None


def _is_root_cause_container_key(key: object) -> bool:
    normalized = str(key or "").strip().lower()
    return any(token in normalized for token in ("error", "exception", "reason", "blocker", "issue", "failure"))


def _root_cause_code_from_sequence(values: object, *, scan_strings: bool) -> str | None:
    if not isinstance(values, (list, tuple, set)):
        return None
    for item in values:
        code = _root_cause_code_from_value(item, scan_strings=scan_strings)
        if code:
            return code
    return None


def _root_cause_code_from_value(value: object, *, scan_strings: bool) -> str | None:
    if isinstance(value, dict):
        return _root_cause_code(value)
    if isinstance(value, (list, tuple, set)):
        return _root_cause_code_from_sequence(value, scan_strings=scan_strings)
    if scan_strings:
        return _normalize_root_cause_code(value)
    return None


def _root_cause_code(payload: object, *extra_text: object) -> str | None:
    if isinstance(payload, dict):
        for key, code in _ROOT_CAUSE_BOOLEAN_FLAG_CODES.items():
            if payload.get(key) is True:
                return code
        for key in _ROOT_CAUSE_CODE_KEYS:
            code = _root_cause_code_from_value(payload.get(key), scan_strings=True)
            if code:
                return code
        for key in _ROOT_CAUSE_TEXT_KEYS:
            code = _root_cause_code_from_value(payload.get(key), scan_strings=True)
            if code:
                return code
        for key, value in payload.items():
            if isinstance(value, dict):
                code = _root_cause_code(value)
                if code:
                    return code
            elif isinstance(value, (list, tuple, set)):
                code = _root_cause_code_from_sequence(
                    value,
                    scan_strings=_is_root_cause_container_key(key),
                )
                if code:
                    return code
    for value in extra_text:
        code = _root_cause_code_from_value(value, scan_strings=True)
        if code:
            return code
    return None


def _pending_plan_root_cause_codes(reason_codes: list[str]) -> list[str]:
    root_cause_codes: list[str] = []
    for code in reason_codes:
        normalized = _normalize_root_cause_code(code)
        if normalized and normalized not in root_cause_codes:
            root_cause_codes.append(normalized)
    return root_cause_codes


def _pending_plan_payload(row: PendingEntryPlan) -> dict[str, object]:
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    tracking = metadata.get("last_confirmation_tracking")
    tracking_payload = tracking if isinstance(tracking, dict) else {}
    reason_codes = _pending_plan_reason_codes(row, metadata)
    root_cause_codes = _pending_plan_root_cause_codes(reason_codes)
    return {
        "id": row.id,
        "symbol": row.symbol,
        "side": row.side,
        "plan_status": row.plan_status,
        "gate_state": pending_entry_plan_gate_state(row),
        "execution_status": pending_entry_plan_execution_status(row),
        "source_decision_run_id": row.source_decision_run_id,
        "entry_mode": row.entry_mode,
        "created_at": _dt(row.created_at),
        "expires_at": _dt(row.expires_at),
        "triggered_at": _dt(row.triggered_at),
        "canceled_at": _dt(row.canceled_at),
        "canceled_reason": row.canceled_reason,
        "blocked_reason_codes": reason_codes,
        "root_cause_codes": root_cause_codes,
        "confirmation_failed_reason": tracking_payload.get("confirmation_failed_reason"),
    }


def _stale_triggered_plan_cleanup_guard(session: Session, plan: PendingEntryPlan) -> dict[str, object]:
    symbol = str(plan.symbol or "").upper()
    source_decision_run_id = int(plan.source_decision_run_id) if plan.source_decision_run_id is not None else None
    open_position_exists = bool(
        session.scalar(
            select(Position.id)
            .where(
                Position.mode == "live",
                Position.status == "open",
                Position.quantity > 0,
                Position.symbol == symbol,
            )
            .limit(1)
        )
    )
    active_order_exists = bool(
        session.scalar(
            select(Order.id)
            .where(
                Order.mode == "live",
                Order.symbol == symbol,
                func.lower(Order.status).in_(ACTIVE_LIVE_ORDER_STATUSES),
            )
            .limit(1)
        )
    )
    linked_order_exists = False
    if source_decision_run_id is not None:
        linked_order_exists = bool(
            session.scalar(
                select(Order.id)
                .where(
                    Order.mode == "live",
                    Order.decision_run_id == source_decision_run_id,
                )
                .limit(1)
            )
        )
    cleanup_safe = not open_position_exists and not active_order_exists and not linked_order_exists
    return {
        "cleanup_safe": cleanup_safe,
        "open_position_exists": open_position_exists,
        "active_order_exists": active_order_exists,
        "linked_order_exists": linked_order_exists,
        "source_decision_run_id": source_decision_run_id,
    }


def normalize_stale_pending_entry_plan_history(
    session: Session,
    *,
    now: datetime | None = None,
    reason: str = "PLAN_TRIGGERED_STALE_HISTORY_NORMALIZED",
) -> dict[str, object]:
    generated_at = now or utcnow_naive()
    candidates = list(
        session.scalars(
            select(PendingEntryPlan)
            .where(PendingEntryPlan.plan_status == TRIGGERED_PENDING_ENTRY_PLAN_STATUS)
            .order_by(PendingEntryPlan.expires_at.asc(), PendingEntryPlan.id.asc())
        )
    )
    normalized: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    for plan in candidates:
        gate_state = pending_entry_plan_gate_state(plan, now=generated_at)
        if gate_state != "triggered_stale_history":
            continue
        before_payload = _pending_plan_payload(plan)
        cleanup_guard = _stale_triggered_plan_cleanup_guard(session, plan)
        if not cleanup_guard["cleanup_safe"]:
            skipped.append({**before_payload, "cleanup_guard": cleanup_guard})
            continue
        metadata = plan.metadata_json if isinstance(plan.metadata_json, dict) else {}
        metadata = {
            **metadata,
            "normalized_from_gate_state": gate_state,
            "normalized_reason": reason,
            "normalized_at": generated_at.isoformat(),
            "cleanup_guard": cleanup_guard,
        }
        plan.plan_status = "expired"
        plan.canceled_at = generated_at
        plan.canceled_reason = reason
        plan.metadata_json = metadata
        session.add(plan)
        session.add(
            AuditEvent(
                event_type="pending_entry_plan_expired",
                entity_type="pending_entry_plan",
                entity_id=str(plan.id),
                severity="info",
                message="Stale triggered pending entry plan normalized to expired history.",
                payload={
                    "reason": reason,
                    "previous_gate_state": gate_state,
                    "previous_plan": before_payload,
                    "cleanup_guard": cleanup_guard,
                    "plan_status": "expired",
                    "normalized_at": generated_at.isoformat(),
                },
            )
        )
        normalized.append({**before_payload, "normalized_to_status": "expired", "normalized_reason": reason})
    session.flush()
    return {
        "normalized_count": len(normalized),
        "normalized_plans": normalized,
        "skipped_count": len(skipped),
        "skipped_plans": skipped,
    }


def _order_payload(row: Order) -> dict[str, object]:
    return {
        "id": row.id,
        "symbol": row.symbol,
        "side": row.side,
        "status": row.status,
        "mode": row.mode,
        "order_type": row.order_type,
        "created_at": _dt(row.created_at),
        "updated_at": _dt(row.updated_at),
    }


def _position_payload(row: Position) -> dict[str, object]:
    return {
        "id": row.id,
        "symbol": row.symbol,
        "side": row.side,
        "status": row.status,
        "mode": row.mode,
        "opened_at": _dt(row.opened_at),
        "updated_at": _dt(row.updated_at),
    }


def _scheduler_payload(row: SchedulerRun) -> dict[str, object]:
    outcome = row.outcome if isinstance(row.outcome, dict) else {}
    root_cause_codes = _root_cause_codes_from_evidence(outcome)
    if not root_cause_codes:
        root_cause_codes = [RECENT_SCHEDULER_NON_SUCCESS_ROOT_CAUSE]
    root_cause_code = root_cause_codes[0]
    return {
        "id": row.id,
        "workflow": row.workflow,
        "schedule_window": row.schedule_window,
        "status": row.status,
        "triggered_by": row.triggered_by,
        "created_at": _dt(row.created_at),
        "reason_code": root_cause_code,
        "root_cause_code": root_cause_code,
        "root_cause_codes": root_cause_codes,
        "outcome": outcome,
    }


def _health_payload(row: SystemHealthEvent) -> dict[str, object]:
    payload = row.payload if isinstance(row.payload, dict) else {}
    root_cause_codes = _root_cause_codes_from_evidence(payload, row.message)
    if not root_cause_codes:
        root_cause_codes = [RECENT_HEALTH_ERRORS_ROOT_CAUSE]
    root_cause_code = root_cause_codes[0]
    return {
        "id": row.id,
        "component": row.component,
        "status": row.status,
        "message": row.message,
        "created_at": _dt(row.created_at),
        "reason_code": root_cause_code,
        "root_cause_code": root_cause_code,
        "root_cause_codes": root_cause_codes,
        "payload": payload,
    }


def _append_root_cause_code(codes: list[str], value: object) -> None:
    if isinstance(value, (list, tuple, set)):
        for item in value:
            _append_root_cause_code(codes, item)
        return
    code = _normalize_root_cause_code(value)
    if code and code not in codes:
        codes.append(code)


_ROOT_CAUSE_COLLECTION_KEYS = (
    *_ROOT_CAUSE_CODE_KEYS,
    "root_cause_codes",
    "reason_codes",
    "blocked_reason_codes",
)


def _append_root_cause_codes_from_value(codes: list[str], value: object, *, scan_strings: bool) -> None:
    if isinstance(value, dict):
        visited_keys = {*_ROOT_CAUSE_COLLECTION_KEYS, *_ROOT_CAUSE_TEXT_KEYS}
        for key, code in _ROOT_CAUSE_BOOLEAN_FLAG_CODES.items():
            if value.get(key) is True:
                _append_root_cause_code(codes, code)
        for key in _ROOT_CAUSE_COLLECTION_KEYS:
            _append_root_cause_codes_from_value(codes, value.get(key), scan_strings=True)
        for key in _ROOT_CAUSE_TEXT_KEYS:
            _append_root_cause_codes_from_value(codes, value.get(key), scan_strings=True)
        for key, nested in value.items():
            if key in visited_keys:
                continue
            if isinstance(nested, dict):
                _append_root_cause_codes_from_value(codes, nested, scan_strings=False)
            elif isinstance(nested, (list, tuple, set)):
                _append_root_cause_codes_from_value(
                    codes,
                    nested,
                    scan_strings=_is_root_cause_container_key(key),
                )
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            _append_root_cause_codes_from_value(codes, item, scan_strings=scan_strings)
        return
    if scan_strings:
        _append_root_cause_code(codes, value)


def _root_cause_codes_from_evidence(payload: object, *extra_text: object) -> list[str]:
    codes: list[str] = []
    _append_root_cause_codes_from_value(codes, payload, scan_strings=False)
    for value in extra_text:
        _append_root_cause_codes_from_value(codes, value, scan_strings=True)
    if DB_IDLE_IN_TRANSACTION_TIMEOUT_ROOT_CAUSE in codes and "WORKFLOW_EXCEPTION" in codes:
        codes = [code for code in codes if code != "WORKFLOW_EXCEPTION"]
    return codes


def _root_cause_codes_from_payloads(*sections: list[dict[str, object]]) -> list[str]:
    codes: list[str] = []
    for section in sections:
        for item in section:
            if not isinstance(item, dict):
                continue
            for key in (
                "root_cause_code",
                "reason_code",
                "blocked_reason_code",
                "root_cause_codes",
                "reason_codes",
                "blocked_reason_codes",
            ):
                _append_root_cause_code(codes, item.get(key))
    return codes


def _setting_row(session: Session) -> Setting | None:
    return session.scalar(select(Setting).order_by(Setting.id.asc()).limit(1))


def _redis_cache_unavailable(settings_row: Setting | None) -> dict[str, object]:
    if settings_row is None:
        return {"blocking": False, "reason_code": None, "root_cause_code": None}
    state = get_market_stream_detail(settings_row)
    redis_configured = bool(state.get("redis_configured"))
    redis_connected = state.get("redis_connected")
    cache_health = str(state.get("cache_health") or "").strip().lower()
    blocking = redis_configured and (redis_connected is False or cache_health == "unavailable")
    root_cause_code = REDIS_CACHE_UNAVAILABLE_ROOT_CAUSE if blocking else None
    return {
        "blocking": blocking,
        "reason_code": root_cause_code,
        "root_cause_code": root_cause_code,
        "redis_configured": redis_configured,
        "redis_connected": redis_connected,
        "cache_health": cache_health or "unknown",
        "cache_reject_reason": state.get("cache_reject_reason"),
        "last_shared_cache_error": state.get("last_shared_cache_error"),
    }


def _for_update_lock_waits(session: Session) -> list[dict[str, object]]:
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return []
    rows = session.execute(
        text(
            """
            with waiting as (
                select
                    pid,
                    wait_event_type,
                    wait_event,
                    state,
                    left((now() - query_start)::text, 64) as query_age,
                    left(query, 240) as query,
                    pg_blocking_pids(pid) as blocking_pids
                from pg_stat_activity
                where wait_event_type = 'Lock'
                  and query ilike '%FOR UPDATE%'
            )
            select
                waiting.pid,
                waiting.wait_event_type,
                waiting.wait_event,
                waiting.state,
                waiting.query_age,
                waiting.query,
                waiting.blocking_pids,
                coalesce(
                    jsonb_agg(
                        jsonb_build_object(
                            'pid', blockers.pid,
                            'application_name', blockers.application_name,
                            'client_addr', blockers.client_addr::text,
                            'state', blockers.state,
                            'wait_event_type', blockers.wait_event_type,
                            'wait_event', blockers.wait_event,
                            'xact_age', left((now() - blockers.xact_start)::text, 64),
                            'query_age', left((now() - blockers.query_start)::text, 64),
                            'query', left(blockers.query, 240)
                        )
                    ) filter (where blockers.pid is not null),
                    '[]'::jsonb
                ) as blocking_sessions
            from waiting
            left join lateral unnest(waiting.blocking_pids) as blocker_pid(pid) on true
            left join pg_stat_activity blockers on blockers.pid = blocker_pid.pid
            group by
                waiting.pid,
                waiting.wait_event_type,
                waiting.wait_event,
                waiting.state,
                waiting.query_age,
                waiting.query,
                waiting.blocking_pids
            order by waiting.pid
            """
        )
    ).mappings()
    return [dict(row) for row in rows]


def build_service_switch_gate_snapshot(
    session: Session,
    *,
    recent_minutes: int = 30,
) -> dict[str, object]:
    now = utcnow_naive()
    since = now - timedelta(minutes=max(int(recent_minutes), 1))
    settings_row = _setting_row(session)
    reconciliation = get_reconciliation_detail(settings_row) if settings_row is not None else {}
    unresolved_submission_guards = (
        list_unresolved_submission_guards(settings_row) if settings_row is not None else []
    )
    open_positions = list(
        session.scalars(
            select(Position)
            .where(Position.mode == "live", Position.status == "open")
            .order_by(Position.updated_at.desc(), Position.id.desc())
        )
    )
    active_orders = list(
        session.scalars(
            select(Order)
            .where(Order.mode == "live", func.lower(Order.status).in_(ACTIVE_LIVE_ORDER_STATUSES))
            .order_by(Order.updated_at.desc(), Order.id.desc())
        )
    )
    pending_plans = list(
        session.scalars(
            select(PendingEntryPlan)
            .where(PendingEntryPlan.plan_status.in_([ACTIVE_PENDING_ENTRY_PLAN_STATUS, TRIGGERED_PENDING_ENTRY_PLAN_STATUS]))
            .order_by(PendingEntryPlan.expires_at.asc(), PendingEntryPlan.id.asc())
        )
    )
    gate_states = {int(row.id): pending_entry_plan_gate_state(row, now=now) for row in pending_plans if row.id is not None}
    blocking_pending = [
        row
        for row in pending_plans
        if gate_states.get(int(row.id), pending_entry_plan_gate_state(row, now=now))
        in {"active_waiting", "triggered_non_terminal"}
    ]
    terminal_triggered = [
        row
        for row in pending_plans
        if gate_states.get(int(row.id), pending_entry_plan_gate_state(row, now=now)) == "triggered_terminal_history"
    ]
    stale_triggered = [
        row
        for row in pending_plans
        if gate_states.get(int(row.id), pending_entry_plan_gate_state(row, now=now)) == "triggered_stale_history"
    ]
    scheduler_non_success = list(
        session.scalars(
            select(SchedulerRun)
            .where(
                SchedulerRun.created_at >= since,
                SchedulerRun.status.notin_(SCHEDULER_NON_BLOCKING_STATUSES),
            )
            .order_by(SchedulerRun.created_at.desc())
            .limit(20)
        )
    )
    health_errors = list(
        session.scalars(
            select(SystemHealthEvent)
            .where(
                SystemHealthEvent.created_at >= since,
                SystemHealthEvent.status.in_(HEALTH_BLOCKING_STATUSES),
            )
            .order_by(SystemHealthEvent.created_at.desc())
            .limit(20)
        )
    )
    for_update_waits = _for_update_lock_waits(session)
    redis_cache = _redis_cache_unavailable(settings_row)
    reconciliation_synced = str(reconciliation.get("status") or "").lower() == "synced"
    blockers: list[str] = []
    if open_positions:
        blockers.append("open_positions")
    if active_orders:
        blockers.append("active_orders")
    if blocking_pending:
        blockers.append("active_pending_entry_plans")
    if unresolved_submission_guards or int(reconciliation.get("unresolved_submission_count") or 0) > 0:
        blockers.append("unresolved_submission")
    if not reconciliation_synced:
        blockers.append("reconciliation_not_synced")
    if scheduler_non_success:
        blockers.append("recent_scheduler_non_success")
    if health_errors:
        blockers.append("recent_health_errors")
    if for_update_waits:
        blockers.append("for_update_lock_wait")
    if redis_cache.get("blocking"):
        blockers.append(REDIS_CACHE_UNAVAILABLE_BLOCKER)

    active_pending_payloads = [_pending_plan_payload(row) for row in blocking_pending]
    unresolved_submission_payloads = [dict(row) for row in unresolved_submission_guards]
    recent_scheduler_payloads = [_scheduler_payload(row) for row in scheduler_non_success]
    recent_health_payloads = [_health_payload(row) for row in health_errors]
    root_cause_codes = _root_cause_codes_from_payloads(
        active_pending_payloads,
        unresolved_submission_payloads,
        recent_scheduler_payloads,
        recent_health_payloads,
        for_update_waits,
        [redis_cache],
    )

    return {
        "generated_at": now.isoformat(),
        "gate_clear": not blockers,
        "blockers": blockers,
        "root_cause_codes": root_cause_codes,
        "counts": {
            "open_positions": len(open_positions),
            "active_orders": len(active_orders),
            "active_pending_entry_plans": len(blocking_pending),
            "armed_pending_entry_plans": sum(
                1
                for row in pending_plans
                if gate_states.get(int(row.id), pending_entry_plan_gate_state(row, now=now)) == "active_waiting"
            ),
            "triggered_non_terminal_pending_entry_plans": sum(
                1
                for row in pending_plans
                if gate_states.get(int(row.id), pending_entry_plan_gate_state(row, now=now)) == "triggered_non_terminal"
            ),
            "triggered_terminal_history_entry_plans": len(terminal_triggered),
            "triggered_stale_history_entry_plans": len(stale_triggered),
            "unresolved_submission_count": max(
                len(unresolved_submission_guards),
                int(reconciliation.get("unresolved_submission_count") or 0),
            ),
            "recent_scheduler_non_success": len(scheduler_non_success),
            "recent_health_errors": len(health_errors),
            "for_update_lock_waits": len(for_update_waits),
            REDIS_CACHE_UNAVAILABLE_BLOCKER: 1 if redis_cache.get("blocking") else 0,
        },
        "redis_cache": redis_cache,
        "open_positions": [_position_payload(row) for row in open_positions],
        "active_orders": [_order_payload(row) for row in active_orders],
        "active_pending_entry_plans": active_pending_payloads,
        "triggered_terminal_history_entry_plans": [
            _pending_plan_payload(row) for row in terminal_triggered[:20]
        ],
        "triggered_stale_history_entry_plans": [
            _pending_plan_payload(row) for row in stale_triggered[:20]
        ],
        "reconciliation": reconciliation,
        "reconciliation_synced": reconciliation_synced,
        "unresolved_submissions": unresolved_submission_payloads,
        "recent_scheduler_non_success": recent_scheduler_payloads,
        "recent_health_errors": recent_health_payloads,
        "for_update_lock_waits": for_update_waits,
    }
