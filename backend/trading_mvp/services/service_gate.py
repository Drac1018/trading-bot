from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import Select, func, select, text
from sqlalchemy.orm import Session

from trading_mvp.models import (
    Order,
    PendingEntryPlan,
    Position,
    SchedulerRun,
    Setting,
    SystemHealthEvent,
)
from trading_mvp.services.runtime_state import (
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


def _pending_plan_payload(row: PendingEntryPlan) -> dict[str, object]:
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    tracking = metadata.get("last_confirmation_tracking")
    tracking_payload = tracking if isinstance(tracking, dict) else {}
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
        "blocked_reason_codes": tracking_payload.get("blocked_reason_codes") or [],
        "confirmation_failed_reason": tracking_payload.get("confirmation_failed_reason"),
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
    return {
        "id": row.id,
        "workflow": row.workflow,
        "schedule_window": row.schedule_window,
        "status": row.status,
        "triggered_by": row.triggered_by,
        "created_at": _dt(row.created_at),
        "outcome": row.outcome if isinstance(row.outcome, dict) else {},
    }


def _health_payload(row: SystemHealthEvent) -> dict[str, object]:
    return {
        "id": row.id,
        "component": row.component,
        "status": row.status,
        "message": row.message,
        "created_at": _dt(row.created_at),
        "payload": row.payload if isinstance(row.payload, dict) else {},
    }


def _setting_row(session: Session) -> Setting | None:
    return session.scalar(select(Setting).order_by(Setting.id.asc()).limit(1))


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

    return {
        "generated_at": now.isoformat(),
        "gate_clear": not blockers,
        "blockers": blockers,
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
        },
        "open_positions": [_position_payload(row) for row in open_positions],
        "active_orders": [_order_payload(row) for row in active_orders],
        "active_pending_entry_plans": [_pending_plan_payload(row) for row in blocking_pending],
        "triggered_terminal_history_entry_plans": [
            _pending_plan_payload(row) for row in terminal_triggered[:20]
        ],
        "triggered_stale_history_entry_plans": [
            _pending_plan_payload(row) for row in stale_triggered[:20]
        ],
        "reconciliation": reconciliation,
        "reconciliation_synced": reconciliation_synced,
        "unresolved_submissions": [dict(row) for row in unresolved_submission_guards],
        "recent_scheduler_non_success": [_scheduler_payload(row) for row in scheduler_non_success],
        "recent_health_errors": [_health_payload(row) for row in health_errors],
        "for_update_lock_waits": for_update_waits,
    }
