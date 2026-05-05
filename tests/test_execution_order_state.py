from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session
from trading_mvp.models import AuditEvent, Order, Position
from trading_mvp.services.execution import (
    CLOSED_POSITION_PROTECTIVE_ORDER_RECONCILED_REASON_CODE,
    _apply_exchange_order_state,
    reconcile_closed_position_protective_orders,
)


def _order(*, status: str = "pending") -> Order:
    return Order(
        symbol="BTCUSDT",
        side="sell",
        order_type="stop_market",
        mode="live",
        status=status,
        requested_quantity=0.01,
        requested_price=69000.0,
        filled_quantity=0.0,
        average_fill_price=0.0,
        reason_codes=[],
        metadata_json={},
    )


def test_apply_exchange_order_state_maps_finished_algo_status_to_expired() -> None:
    row = _order()

    _apply_exchange_order_state(
        row,
        {
            "status": "FINISHED",
            "origQty": "0.01",
            "executedQty": "0.0",
            "stopPrice": "69000",
            "reduceOnly": True,
            "closePosition": True,
        },
        requested_quantity_fallback=0.01,
        requested_price_fallback=69000.0,
        reduce_only_fallback=False,
        close_only_fallback=False,
    )

    assert row.status == "expired"
    assert row.exchange_status == "FINISHED"
    assert row.reduce_only is True
    assert row.close_only is True


def test_apply_exchange_order_state_normalizes_legacy_finished_local_status() -> None:
    row = _order(status="finished")

    _apply_exchange_order_state(
        row,
        {
            "status": "FINISHED",
            "origQty": "0.01",
            "executedQty": "0.0",
            "stopPrice": "69000",
        },
        requested_quantity_fallback=0.01,
        requested_price_fallback=69000.0,
        reduce_only_fallback=True,
        close_only_fallback=True,
    )

    assert row.status == "expired"
    assert row.exchange_status == "FINISHED"


def _position(*, status: str = "closed", quantity: float = 0.0) -> Position:
    return Position(
        symbol="BTCUSDT",
        mode="live",
        side="long",
        status=status,
        quantity=quantity,
        entry_price=70000.0,
        mark_price=70000.0,
        leverage=1.0,
        stop_loss=69000.0,
        take_profit=72000.0,
    )


def _protective_order(*, position_id: int, external_order_id: str = "12345") -> Order:
    return Order(
        symbol="BTCUSDT",
        side="sell",
        order_type="stop_market",
        mode="live",
        status="pending",
        external_order_id=external_order_id,
        client_order_id="protective-btc-1",
        position_id=position_id,
        reduce_only=True,
        close_only=True,
        requested_quantity=0.01,
        requested_price=69000.0,
        filled_quantity=0.0,
        average_fill_price=0.0,
        reason_codes=[],
        metadata_json={},
    )


def test_reconcile_closed_position_protective_orders_cancels_local_absent_order(db_session: Session) -> None:
    position = _position()
    db_session.add(position)
    db_session.flush()
    order = _protective_order(position_id=position.id)
    db_session.add(order)
    db_session.flush()

    reconciled = reconcile_closed_position_protective_orders(
        db_session,
        symbol="BTCUSDT",
        open_orders=[],
        observed_at=datetime(2026, 5, 3, 12, 0, 0),
        source="test",
    )

    assert [row.id for row in reconciled] == [order.id]
    assert order.status == "canceled"
    assert order.exchange_status == "CANCELED"
    assert CLOSED_POSITION_PROTECTIVE_ORDER_RECONCILED_REASON_CODE in order.reason_codes
    reconciliation = order.metadata_json["closed_position_protective_order_reconciliation"]
    assert reconciliation["source"] == "test"
    assert reconciliation["remote_open_order_absent"] is True
    event = db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.event_type == "protective_order_reconciled",
            AuditEvent.entity_id == str(order.id),
        )
    )
    assert event is not None
    assert event.payload["reason_code"] == CLOSED_POSITION_PROTECTIVE_ORDER_RECONCILED_REASON_CODE


def test_reconcile_closed_position_protective_orders_keeps_remote_open_order(db_session: Session) -> None:
    position = _position()
    db_session.add(position)
    db_session.flush()
    order = _protective_order(position_id=position.id)
    db_session.add(order)
    db_session.flush()

    reconciled = reconcile_closed_position_protective_orders(
        db_session,
        symbol="BTCUSDT",
        open_orders=[{"symbol": "BTCUSDT", "algoId": "12345", "clientAlgoId": "protective-btc-1"}],
        observed_at=datetime(2026, 5, 3, 12, 0, 0),
        source="test",
    )

    assert reconciled == []
    assert order.status == "pending"
    assert order.exchange_status is None


def test_reconcile_closed_position_protective_orders_ignores_open_position(db_session: Session) -> None:
    position = _position(status="open", quantity=0.01)
    db_session.add(position)
    db_session.flush()
    order = _protective_order(position_id=position.id)
    db_session.add(order)
    db_session.flush()

    reconciled = reconcile_closed_position_protective_orders(
        db_session,
        symbol="BTCUSDT",
        open_orders=[],
        observed_at=datetime(2026, 5, 3, 12, 0, 0),
        source="test",
    )

    assert reconciled == []
    assert order.status == "pending"


def test_reconcile_closed_position_protective_orders_ignores_non_protective_reduce_order(
    db_session: Session,
) -> None:
    position = _position()
    db_session.add(position)
    db_session.flush()
    order = _protective_order(position_id=position.id)
    order.order_type = "market"
    db_session.add(order)
    db_session.flush()

    reconciled = reconcile_closed_position_protective_orders(
        db_session,
        symbol="BTCUSDT",
        open_orders=[],
        observed_at=datetime(2026, 5, 3, 12, 0, 0),
        source="test",
    )

    assert reconciled == []
    assert order.status == "pending"
