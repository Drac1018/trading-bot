from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from trading_mvp.main import app
from trading_mvp.models import (
    AuditEvent,
    Order,
    PendingEntryPlan,
    Position,
    SchedulerRun,
    SystemHealthEvent,
)
from trading_mvp.services.runtime_state import (
    replace_market_stream_detail,
    set_reconciliation_detail,
)
from trading_mvp.services.service_gate import (
    REDIS_CACHE_UNAVAILABLE_BLOCKER,
    build_service_switch_gate_snapshot,
    normalize_stale_pending_entry_plan_history,
)
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive


def _synced_gate_settings(db_session):
    settings = get_or_create_settings(db_session)
    set_reconciliation_detail(
        settings,
        status="synced",
        unresolved_submission_badge=False,
        unresolved_submission_count=0,
        unresolved_submission_symbols=[],
        unresolved_submissions=[],
    )
    db_session.flush()
    return settings


def _pending_plan(
    *,
    status: str,
    source_decision_run_id: int,
    metadata_json: dict[str, object] | None = None,
    expires_delta: timedelta = timedelta(minutes=30),
) -> PendingEntryPlan:
    now = utcnow_naive()
    return PendingEntryPlan(
        symbol="BTCUSDT",
        side="short",
        plan_status=status,
        source_decision_run_id=source_decision_run_id,
        source_timeframe="15m",
        entry_mode="pullback_confirm",
        entry_zone_min=100.0,
        entry_zone_max=101.0,
        invalidation_price=102.0,
        max_chase_bps=10.0,
        idea_ttl_minutes=30,
        stop_loss=102.0,
        take_profit=98.0,
        risk_pct_cap=0.01,
        leverage_cap=1.0,
        expires_at=now + expires_delta,
        triggered_at=now if status == "triggered" else None,
        idempotency_key=f"pending-plan:BTCUSDT:short:{source_decision_run_id}:test",
        metadata_json=metadata_json or {},
    )


def test_service_gate_ignores_triggered_terminal_pending_plan_history(db_session) -> None:
    _synced_gate_settings(db_session)
    db_session.add(
        _pending_plan(
            status="triggered",
            source_decision_run_id=9001,
            metadata_json={"execution_result": {"status": "filled", "order_id": 1}},
        )
    )
    db_session.flush()

    snapshot = build_service_switch_gate_snapshot(db_session)

    assert snapshot["gate_clear"] is True
    assert snapshot["blockers"] == []
    assert snapshot["counts"]["active_pending_entry_plans"] == 0
    assert snapshot["counts"]["triggered_terminal_history_entry_plans"] == 1
    assert snapshot["triggered_terminal_history_entry_plans"][0]["gate_state"] == "triggered_terminal_history"


def test_runtime_service_gate_endpoint_publishes_empty_root_cause_codes_when_clear(testclient_db_factory) -> None:
    TestingSessionLocal = testclient_db_factory("service_gate_clear_root_cause_contract.db")

    with TestingSessionLocal() as session:
        _synced_gate_settings(session)
        session.commit()

    with TestClient(app) as client:
        response = client.get("/api/runtime/service-gate")

    assert response.status_code == 200
    payload = response.json()
    assert payload["gate_clear"] is True
    assert payload["blockers"] == []
    assert "root_cause_codes" in payload
    assert payload["root_cause_codes"] == []


def test_runtime_service_gate_endpoint_publishes_db_connection_root_cause_codes(testclient_db_factory) -> None:
    TestingSessionLocal = testclient_db_factory("service_gate_db_root_cause_contract.db")

    with TestingSessionLocal() as session:
        _synced_gate_settings(session)
        now = utcnow_naive()
        failure_payload = {
            "status": "error",
            "error_category": "db_operational_error",
            "db_connection_lost": True,
            "session_recovered_before_logging": True,
            "scheduler_failure_persisted": True,
        }
        session.add(
            SchedulerRun(
                schedule_window="30s",
                workflow="market_refresh_cycle",
                status="failed",
                triggered_by="scheduler",
                created_at=now,
                outcome=failure_payload,
            )
        )
        session.add(
            SystemHealthEvent(
                component="scheduler",
                status="error",
                message="Market refresh cycle failed.",
                created_at=now,
                payload=failure_payload,
            )
        )
        session.commit()

    with TestClient(app) as client:
        response = client.get("/api/runtime/service-gate")

    assert response.status_code == 200
    payload = response.json()
    assert payload["gate_clear"] is False
    assert payload["blockers"] == ["recent_scheduler_non_success", "recent_health_errors"]
    assert payload["root_cause_codes"] == ["DB_CONNECTION_LOST", "DB_OPERATIONAL_ERROR"]
    scheduler_row = payload["recent_scheduler_non_success"][0]
    health_row = payload["recent_health_errors"][0]
    assert scheduler_row["root_cause_codes"] == ["DB_CONNECTION_LOST", "DB_OPERATIONAL_ERROR"]
    assert scheduler_row["reason_code"] == "DB_CONNECTION_LOST"
    assert health_row["root_cause_codes"] == ["DB_CONNECTION_LOST", "DB_OPERATIONAL_ERROR"]
    assert health_row["reason_code"] == "DB_CONNECTION_LOST"


def test_service_gate_blocks_armed_and_triggered_non_terminal_pending_plans(db_session) -> None:
    _synced_gate_settings(db_session)
    db_session.add_all(
        [
            _pending_plan(status="armed", source_decision_run_id=9002),
            _pending_plan(status="triggered", source_decision_run_id=9003),
        ]
    )
    db_session.flush()

    snapshot = build_service_switch_gate_snapshot(db_session)
    gate_states = {row["gate_state"] for row in snapshot["active_pending_entry_plans"]}

    assert snapshot["gate_clear"] is False
    assert snapshot["blockers"] == ["active_pending_entry_plans"]
    assert snapshot["counts"]["active_pending_entry_plans"] == 2
    assert snapshot["counts"]["armed_pending_entry_plans"] == 1
    assert snapshot["counts"]["triggered_non_terminal_pending_entry_plans"] == 1
    assert gate_states == {"active_waiting", "triggered_non_terminal"}


def test_service_gate_publishes_active_pending_plan_reason_codes(db_session) -> None:
    _synced_gate_settings(db_session)
    db_session.add(
        _pending_plan(
            status="armed",
            source_decision_run_id=9010,
            metadata_json={
                "last_watch_blocked_reason_codes": [" plan_late_chase_waiting_reentry "],
                "source_blocked_reason_codes": ["HOLD_DECISION"],
                "last_confirmation_tracking": {
                    "blocked_reason_codes": ["zone_not_entered"],
                    "confirmation_failed_reason": "confirmation_waiting",
                },
            },
        )
    )
    db_session.flush()

    snapshot = build_service_switch_gate_snapshot(db_session)

    assert snapshot["gate_clear"] is False
    assert snapshot["blockers"] == ["active_pending_entry_plans"]
    assert snapshot["root_cause_codes"] == [
        "PLAN_LATE_CHASE_WAITING_REENTRY",
        "HOLD_DECISION",
        "ZONE_NOT_ENTERED",
        "CONFIRMATION_WAITING",
    ]
    plan = snapshot["active_pending_entry_plans"][0]
    assert plan["blocked_reason_codes"] == [
        "plan_late_chase_waiting_reentry",
        "HOLD_DECISION",
        "zone_not_entered",
        "confirmation_waiting",
    ]
    assert plan["root_cause_codes"] == [
        "PLAN_LATE_CHASE_WAITING_REENTRY",
        "HOLD_DECISION",
        "ZONE_NOT_ENTERED",
        "CONFIRMATION_WAITING",
    ]


def test_service_gate_treats_expired_triggered_non_terminal_plan_as_stale_history(db_session) -> None:
    _synced_gate_settings(db_session)
    db_session.add(
        _pending_plan(
            status="triggered",
            source_decision_run_id=9004,
            metadata_json={"execution_result": {"status": "partially_filled"}},
            expires_delta=timedelta(days=-1),
        )
    )
    db_session.flush()

    snapshot = build_service_switch_gate_snapshot(db_session)

    assert snapshot["gate_clear"] is True
    assert snapshot["blockers"] == []
    assert snapshot["counts"]["active_pending_entry_plans"] == 0
    assert snapshot["counts"]["triggered_stale_history_entry_plans"] == 1
    assert snapshot["triggered_stale_history_entry_plans"][0]["gate_state"] == "triggered_stale_history"


def test_service_gate_blocks_when_configured_redis_is_unavailable(db_session) -> None:
    settings = _synced_gate_settings(db_session)
    replace_market_stream_detail(
        settings,
        {
            "redis_configured": True,
            "redis_connected": False,
            "cache_health": "unavailable",
            "cache_reject_reason": "redis_unavailable",
            "last_shared_cache_error": "connection refused",
        },
    )
    db_session.flush()

    snapshot = build_service_switch_gate_snapshot(db_session)

    assert snapshot["gate_clear"] is False
    assert REDIS_CACHE_UNAVAILABLE_BLOCKER in snapshot["blockers"]
    assert snapshot["counts"][REDIS_CACHE_UNAVAILABLE_BLOCKER] == 1
    assert snapshot["root_cause_codes"] == ["REDIS_CACHE_UNAVAILABLE"]
    assert snapshot["redis_cache"]["blocking"] is True
    assert snapshot["redis_cache"]["reason_code"] == "REDIS_CACHE_UNAVAILABLE"
    assert snapshot["redis_cache"]["root_cause_code"] == "REDIS_CACHE_UNAVAILABLE"
    assert snapshot["redis_cache"]["redis_configured"] is True
    assert snapshot["redis_cache"]["redis_connected"] is False


def test_service_gate_recent_scheduler_and_health_rows_publish_root_cause_codes(db_session) -> None:
    _synced_gate_settings(db_session)
    now = utcnow_naive()
    db_session.add(
        SchedulerRun(
            schedule_window="30s",
            workflow="exchange_sync_cycle",
            status="failed",
            triggered_by="scheduler",
            created_at=now,
            outcome={
                "status": "error",
                "error": "Binance error -2015: Invalid API-key, IP, or permissions for action.",
            },
        )
    )
    db_session.add(
        SystemHealthEvent(
            component="live_sync",
            status="error",
            message="Binance error -2015: Invalid API-key, IP, or permissions for action.",
            created_at=now,
            payload={"status": "error"},
        )
    )
    db_session.flush()

    snapshot = build_service_switch_gate_snapshot(db_session)

    assert snapshot["gate_clear"] is False
    assert "recent_scheduler_non_success" in snapshot["blockers"]
    assert "recent_health_errors" in snapshot["blockers"]
    assert snapshot["root_cause_codes"] == ["EXCHANGE_AUTH_PERMISSION_REJECTED"]
    scheduler_row = snapshot["recent_scheduler_non_success"][0]
    health_row = snapshot["recent_health_errors"][0]
    assert scheduler_row["reason_code"] == "EXCHANGE_AUTH_PERMISSION_REJECTED"
    assert scheduler_row["root_cause_code"] == "EXCHANGE_AUTH_PERMISSION_REJECTED"
    assert health_row["reason_code"] == "EXCHANGE_AUTH_PERMISSION_REJECTED"
    assert health_row["root_cause_code"] == "EXCHANGE_AUTH_PERMISSION_REJECTED"


def test_service_gate_preserves_multiple_published_root_cause_codes(db_session) -> None:
    _synced_gate_settings(db_session)
    now = utcnow_naive()
    db_session.add(
        SchedulerRun(
            schedule_window="30s",
            workflow="exchange_sync_cycle",
            status="failed",
            triggered_by="scheduler",
            created_at=now,
            outcome={
                "status": "error",
                "root_cause_codes": [
                    "EXCHANGE_AUTH_PERMISSION_REJECTED",
                    "REDIS_CACHE_UNAVAILABLE",
                ],
            },
        )
    )
    db_session.add(
        SystemHealthEvent(
            component="live_sync",
            status="error",
            message="sync failed",
            created_at=now,
            payload={
                "status": "error",
                "reason_codes": [
                    "RECENT_HEALTH_ERRORS",
                    "EXCHANGE_AUTH_PERMISSION_REJECTED",
                ],
            },
        )
    )
    db_session.flush()

    snapshot = build_service_switch_gate_snapshot(db_session)

    assert snapshot["root_cause_codes"] == [
        "EXCHANGE_AUTH_PERMISSION_REJECTED",
        "REDIS_CACHE_UNAVAILABLE",
        "RECENT_HEALTH_ERRORS",
    ]
    scheduler_row = snapshot["recent_scheduler_non_success"][0]
    health_row = snapshot["recent_health_errors"][0]
    assert scheduler_row["reason_code"] == "EXCHANGE_AUTH_PERMISSION_REJECTED"
    assert scheduler_row["root_cause_codes"] == [
        "EXCHANGE_AUTH_PERMISSION_REJECTED",
        "REDIS_CACHE_UNAVAILABLE",
    ]
    assert health_row["reason_code"] == "RECENT_HEALTH_ERRORS"
    assert health_row["root_cause_codes"] == [
        "RECENT_HEALTH_ERRORS",
        "EXCHANGE_AUTH_PERMISSION_REJECTED",
    ]


def test_service_gate_root_cause_scans_nested_error_lists(db_session) -> None:
    _synced_gate_settings(db_session)
    now = utcnow_naive()
    db_session.add(
        SchedulerRun(
            schedule_window="30s",
            workflow="exchange_sync_cycle",
            status="failed",
            triggered_by="scheduler",
            created_at=now,
            outcome={
                "status": "error",
                "errors": [
                    {
                        "message": "Binance error -2015: Invalid API-key, IP, or permissions for action.",
                    }
                ],
            },
        )
    )
    db_session.add(
        SystemHealthEvent(
            component="live_sync",
            status="error",
            message="sync failed",
            created_at=now,
            payload={
                "status": "error",
                "sync_errors": [
                    "Binance error -2015: Invalid API-key, IP, or permissions for action.",
                ],
            },
        )
    )
    db_session.flush()

    snapshot = build_service_switch_gate_snapshot(db_session)

    scheduler_row = snapshot["recent_scheduler_non_success"][0]
    health_row = snapshot["recent_health_errors"][0]
    assert snapshot["root_cause_codes"] == ["EXCHANGE_AUTH_PERMISSION_REJECTED"]
    assert scheduler_row["reason_code"] == "EXCHANGE_AUTH_PERMISSION_REJECTED"
    assert scheduler_row["root_cause_code"] == "EXCHANGE_AUTH_PERMISSION_REJECTED"
    assert health_row["reason_code"] == "EXCHANGE_AUTH_PERMISSION_REJECTED"
    assert health_row["root_cause_code"] == "EXCHANGE_AUTH_PERMISSION_REJECTED"


def test_service_gate_root_cause_scans_numeric_binance_error_code(db_session) -> None:
    _synced_gate_settings(db_session)
    now = utcnow_naive()
    db_session.add(
        SchedulerRun(
            schedule_window="30s",
            workflow="exchange_sync_cycle",
            status="failed",
            triggered_by="scheduler",
            created_at=now,
            outcome={
                "status": "error",
                "code": -2015,
                "msg": "Invalid API-key, IP, or permissions for action.",
            },
        )
    )
    db_session.add(
        SystemHealthEvent(
            component="live_sync",
            status="error",
            message="sync failed",
            created_at=now,
            payload={
                "status": "error",
                "error": {
                    "code": -2015,
                    "msg": "Invalid API-key, IP, or permissions for action.",
                },
            },
        )
    )
    db_session.flush()

    snapshot = build_service_switch_gate_snapshot(db_session)

    scheduler_row = snapshot["recent_scheduler_non_success"][0]
    health_row = snapshot["recent_health_errors"][0]
    assert snapshot["root_cause_codes"] == ["EXCHANGE_AUTH_PERMISSION_REJECTED"]
    assert scheduler_row["reason_code"] == "EXCHANGE_AUTH_PERMISSION_REJECTED"
    assert scheduler_row["root_cause_code"] == "EXCHANGE_AUTH_PERMISSION_REJECTED"
    assert health_row["reason_code"] == "EXCHANGE_AUTH_PERMISSION_REJECTED"
    assert health_row["root_cause_code"] == "EXCHANGE_AUTH_PERMISSION_REJECTED"


def test_service_gate_recent_scheduler_and_health_rows_publish_error_category_root_cause(db_session) -> None:
    _synced_gate_settings(db_session)
    now = utcnow_naive()
    failure_payload = {
        "status": "error",
        "error_category": "workflow_exception",
        "db_connection_lost": False,
        "scheduler_failure_persisted": True,
    }
    db_session.add(
        SchedulerRun(
            schedule_window="30s",
            workflow="market_refresh_cycle",
            status="failed",
            triggered_by="scheduler",
            created_at=now,
            outcome=failure_payload,
        )
    )
    db_session.add(
        SystemHealthEvent(
            component="scheduler",
            status="error",
            message="Market refresh cycle failed.",
            created_at=now,
            payload=failure_payload,
        )
    )
    db_session.flush()

    snapshot = build_service_switch_gate_snapshot(db_session)

    assert snapshot["gate_clear"] is False
    assert snapshot["root_cause_codes"] == ["WORKFLOW_EXCEPTION"]
    scheduler_row = snapshot["recent_scheduler_non_success"][0]
    health_row = snapshot["recent_health_errors"][0]
    assert scheduler_row["root_cause_codes"] == ["WORKFLOW_EXCEPTION"]
    assert scheduler_row["reason_code"] == "WORKFLOW_EXCEPTION"
    assert health_row["root_cause_codes"] == ["WORKFLOW_EXCEPTION"]
    assert health_row["reason_code"] == "WORKFLOW_EXCEPTION"


def test_service_gate_promotes_idle_transaction_timeout_root_cause(db_session) -> None:
    _synced_gate_settings(db_session)
    now = utcnow_naive()
    failure_payload = {
        "status": "error",
        "error_category": "workflow_exception",
        "error": (
            "(psycopg.errors.IdleInTransactionSessionTimeout) terminating connection "
            "due to idle-in-transaction timeout"
        ),
        "db_connection_lost": False,
        "session_recovered_before_logging": True,
        "scheduler_failure_persisted": True,
    }
    db_session.add(
        SchedulerRun(
            schedule_window="30s",
            workflow="market_refresh_cycle",
            status="failed",
            triggered_by="scheduler",
            created_at=now,
            outcome=failure_payload,
        )
    )
    db_session.add(
        SystemHealthEvent(
            component="scheduler",
            status="error",
            message="Market refresh cycle failed.",
            created_at=now,
            payload=failure_payload,
        )
    )
    db_session.flush()

    snapshot = build_service_switch_gate_snapshot(db_session)

    assert snapshot["gate_clear"] is False
    assert snapshot["root_cause_codes"] == ["DB_IDLE_IN_TRANSACTION_TIMEOUT"]
    scheduler_row = snapshot["recent_scheduler_non_success"][0]
    health_row = snapshot["recent_health_errors"][0]
    assert scheduler_row["root_cause_codes"] == ["DB_IDLE_IN_TRANSACTION_TIMEOUT"]
    assert scheduler_row["reason_code"] == "DB_IDLE_IN_TRANSACTION_TIMEOUT"
    assert health_row["root_cause_codes"] == ["DB_IDLE_IN_TRANSACTION_TIMEOUT"]
    assert health_row["reason_code"] == "DB_IDLE_IN_TRANSACTION_TIMEOUT"


def test_service_gate_recent_scheduler_and_health_rows_without_known_root_cause_publish_generic_reasons(
    db_session,
) -> None:
    _synced_gate_settings(db_session)
    now = utcnow_naive()
    db_session.add(
        SchedulerRun(
            schedule_window="15m",
            workflow="interval_decision_cycle",
            status="running",
            triggered_by="scheduler",
            created_at=now,
            outcome={},
        )
    )
    db_session.add(
        SystemHealthEvent(
            component="worker",
            status="degraded",
            message="heartbeat stale",
            created_at=now,
            payload={},
        )
    )
    db_session.flush()

    snapshot = build_service_switch_gate_snapshot(db_session)

    assert snapshot["gate_clear"] is False
    assert "recent_scheduler_non_success" in snapshot["blockers"]
    assert "recent_health_errors" in snapshot["blockers"]
    assert snapshot["root_cause_codes"] == [
        "RECENT_SCHEDULER_NON_SUCCESS",
        "RECENT_HEALTH_ERRORS",
    ]
    scheduler_row = snapshot["recent_scheduler_non_success"][0]
    health_row = snapshot["recent_health_errors"][0]
    assert scheduler_row["reason_code"] == "RECENT_SCHEDULER_NON_SUCCESS"
    assert scheduler_row["root_cause_code"] == "RECENT_SCHEDULER_NON_SUCCESS"
    assert health_row["reason_code"] == "RECENT_HEALTH_ERRORS"
    assert health_row["root_cause_code"] == "RECENT_HEALTH_ERRORS"


def test_normalize_stale_pending_entry_plan_history_expires_only_stale_triggered(db_session) -> None:
    _synced_gate_settings(db_session)
    stale = _pending_plan(
        status="triggered",
        source_decision_run_id=9005,
        metadata_json={"execution_result": {"status": "partially_filled"}},
        expires_delta=timedelta(days=-1),
    )
    terminal = _pending_plan(
        status="triggered",
        source_decision_run_id=9006,
        metadata_json={"execution_result": {"status": "filled"}},
        expires_delta=timedelta(days=-1),
    )
    db_session.add_all([stale, terminal])
    db_session.flush()

    result = normalize_stale_pending_entry_plan_history(db_session)

    assert result["normalized_count"] == 1
    assert stale.plan_status == "expired"
    assert stale.canceled_reason == "PLAN_TRIGGERED_STALE_HISTORY_NORMALIZED"
    assert terminal.plan_status == "triggered"
    event = db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.event_type == "pending_entry_plan_expired",
            AuditEvent.entity_id == str(stale.id),
        )
    )
    assert event is not None
    assert event.payload["previous_gate_state"] == "triggered_stale_history"


def test_normalize_stale_pending_entry_plan_history_keeps_rows_with_live_position_or_order(db_session) -> None:
    _synced_gate_settings(db_session)
    stale = _pending_plan(
        status="triggered",
        source_decision_run_id=9010,
        metadata_json={"execution_result": {"status": "partially_filled"}},
        expires_delta=timedelta(days=-1),
    )
    db_session.add(stale)
    db_session.flush()
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.1,
            entry_price=100.0,
            mark_price=100.0,
            leverage=1.0,
            stop_loss=95.0,
            take_profit=110.0,
        )
    )
    db_session.add(
        Order(
            symbol="BTCUSDT",
            decision_run_id=9010,
            side="buy",
            order_type="limit",
            mode="live",
            status="open",
            requested_quantity=0.1,
            requested_price=100.0,
        )
    )
    db_session.flush()

    result = normalize_stale_pending_entry_plan_history(db_session)

    assert result["normalized_count"] == 0
    assert result["skipped_count"] == 1
    assert stale.plan_status == "triggered"
    assert result["skipped_plans"][0]["cleanup_guard"]["cleanup_safe"] is False
