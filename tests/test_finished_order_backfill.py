from __future__ import annotations

from sqlalchemy import select
from trading_mvp.models import AuditEvent, Order
from trading_mvp.services.finished_order_backfill import (
    NORMALIZED_LOCAL_STATUS,
    apply_finished_order_backfill,
    build_finished_order_backfill_plan,
)


def _order(
    *,
    symbol: str = "BTCUSDT",
    order_type: str = "market",
    status: str = "pending",
    exchange_status: str | None = None,
    parent_order_id: int | None = None,
    reduce_only: bool = False,
    close_only: bool = False,
    filled_quantity: float = 0.0,
    average_fill_price: float = 0.0,
) -> Order:
    return Order(
        symbol=symbol,
        side="sell",
        order_type=order_type,
        mode="live",
        status=status,
        external_order_id=None,
        client_order_id=None,
        reduce_only=reduce_only,
        close_only=close_only,
        parent_order_id=parent_order_id,
        exchange_status=exchange_status,
        requested_quantity=1.0,
        requested_price=100.0,
        filled_quantity=filled_quantity,
        average_fill_price=average_fill_price,
        reason_codes=[],
        metadata_json={},
    )


def test_finished_order_backfill_plan_only_targets_legacy_protective_rows(db_session) -> None:
    parent = _order(symbol="SOLUSDT", order_type="limit", status="filled", exchange_status="FILLED")
    db_session.add(parent)
    db_session.flush()
    matching = _order(
        symbol="SOLUSDT",
        order_type="stop_market",
        status="finished",
        exchange_status="FINISHED",
        parent_order_id=parent.id,
        reduce_only=True,
        close_only=True,
    )
    wrong_status = _order(
        symbol="ETHUSDT",
        order_type="stop_market",
        status="pending",
        exchange_status="FINISHED",
        parent_order_id=parent.id,
        reduce_only=True,
        close_only=True,
    )
    wrong_flags = _order(
        symbol="XRPUSDT",
        order_type="stop_market",
        status="finished",
        exchange_status="FINISHED",
        parent_order_id=parent.id,
        reduce_only=True,
        close_only=False,
    )
    wrong_fill = _order(
        symbol="BNBUSDT",
        order_type="stop_market",
        status="finished",
        exchange_status="FINISHED",
        parent_order_id=parent.id,
        reduce_only=True,
        close_only=True,
        filled_quantity=1.0,
    )
    db_session.add_all([matching, wrong_status, wrong_flags, wrong_fill])
    db_session.flush()

    plan = build_finished_order_backfill_plan(db_session)

    assert plan.count == 1
    assert plan.order_ids == (matching.id,)
    assert "lower(status) = 'finished'" in plan.match_conditions
    assert f"WHERE id IN ({matching.id})" in plan.update_sql
    assert f"WHERE id IN ({matching.id})" in plan.rollback_sql


def test_finished_order_backfill_apply_updates_status_and_records_audit(db_session) -> None:
    parent = _order(symbol="ETHUSDT", order_type="limit", status="filled", exchange_status="FILLED")
    db_session.add(parent)
    db_session.flush()
    matching = _order(
        symbol="ETHUSDT",
        order_type="stop_market",
        status="finished",
        exchange_status="FINISHED",
        parent_order_id=parent.id,
        reduce_only=True,
        close_only=True,
    )
    db_session.add(matching)
    db_session.flush()

    result = apply_finished_order_backfill(db_session)

    refreshed = db_session.get(Order, matching.id)
    assert refreshed is not None
    assert result.updated_count == 1
    assert refreshed.status == NORMALIZED_LOCAL_STATUS
    assert result.batch_id is not None
    assert result.audit_event_id is not None

    audit_event = db_session.scalar(
        select(AuditEvent).where(AuditEvent.id == result.audit_event_id).limit(1)
    )
    assert audit_event is not None
    assert audit_event.event_type == "maintenance_finished_order_status_backfill_applied"
    assert audit_event.payload["updated_order_ids"] == [matching.id]
    assert audit_event.payload["after_status"] == NORMALIZED_LOCAL_STATUS

    second_plan = build_finished_order_backfill_plan(db_session)
    assert second_plan.count == 0
