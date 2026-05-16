from __future__ import annotations

from datetime import timedelta

from trading_mvp.models import PendingEntryPlan
from trading_mvp.services.runtime_state import set_reconciliation_detail
from trading_mvp.services.service_gate import build_service_switch_gate_snapshot
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
