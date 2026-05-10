from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from trading_mvp.models import AuditEvent, Execution, Order, Position
from trading_mvp.services.execution import (
    CLOSED_POSITION_PROTECTIVE_ORDER_RECONCILED_REASON_CODE,
    PROTECTIVE_CLOSE_FILL_BACKFILL_FAILED_REASON_CODE,
    PROTECTIVE_CLOSE_FILL_BACKFILL_PENDING_REASON_CODE,
    PROTECTIVE_CLOSE_FILL_BACKFILL_SOURCE,
    _apply_exchange_order_state,
    _backfill_finished_protective_order_trades,
    _backfill_missing_finished_protective_order_trades,
    reconcile_closed_position_protective_orders,
)
from trading_mvp.services.settings import get_or_create_settings


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
        side="buy",
        order_type="take_profit_market",
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


class ProtectiveCloseFillBackfillClient:
    def __init__(
        self,
        *,
        trades: list[dict[str, object]] | None = None,
        trade_error: Exception | None = None,
        exchange_order: dict[str, object] | None = None,
    ) -> None:
        self.trades = list(trades or [])
        self.trade_error = trade_error
        self.exchange_order = exchange_order
        self.trade_lookup_calls: list[dict[str, object]] = []
        self.order_lookup_calls: list[dict[str, object]] = []

    def get_account_trades(self, *, symbol: str, order_id: str | None = None, limit: int = 50):
        self.trade_lookup_calls.append({"symbol": symbol, "order_id": order_id, "limit": limit})
        if self.trade_error is not None:
            raise self.trade_error
        if order_id == "1005151959307":
            return list(self.trades)
        return []

    def get_algo_order(self, *, algo_id: str | None = None, client_algo_id: str | None = None):
        self.order_lookup_calls.append({"algo_id": algo_id, "client_algo_id": client_algo_id})
        if self.exchange_order is None:
            raise RuntimeError("missing scripted exchange order")
        return dict(self.exchange_order)


def _finished_take_profit_order_payload() -> dict[str, object]:
    return {
        "algoId": "2000000895228311",
        "clientAlgoId": "protective-btc-1",
        "orderId": "2000000895228311",
        "clientOrderId": "protective-btc-1",
        "actualOrderId": "1005151959307",
        "status": "FINISHED",
        "algoStatus": "FINISHED",
        "type": "TAKE_PROFIT_MARKET",
        "orderType": "TAKE_PROFIT_MARKET",
        "side": "BUY",
        "quantity": "0.001",
        "actualQty": "0.001",
        "actualPrice": "81089",
        "updateTime": 1_777_987_633_052,
    }


def _binance_close_trade() -> dict[str, object]:
    return {
        "id": "7635814641",
        "orderId": "1005151959307",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "positionSide": "BOTH",
        "price": "81089",
        "qty": "0.001",
        "commission": "0.04054449",
        "commissionAsset": "USDT",
        "realizedPnl": "0.44760000",
        "maker": False,
        "buyer": True,
        "time": 1_777_987_633_052,
    }


def test_finished_protective_order_backfills_close_trade_and_preserves_binance_fields(
    db_session: Session,
) -> None:
    settings_row = get_or_create_settings(db_session)
    position = _position()
    db_session.add(position)
    db_session.flush()
    order = _protective_order(position_id=position.id, external_order_id="2000000895228311")
    db_session.add(order)
    db_session.flush()
    client = ProtectiveCloseFillBackfillClient(trades=[_binance_close_trade()])
    exchange_order = _finished_take_profit_order_payload()

    inserted = _backfill_finished_protective_order_trades(
        db_session,
        settings_row,
        client,  # type: ignore[arg-type]
        order,
        exchange_order,
        source="test",
    )
    db_session.flush()

    execution = db_session.scalar(select(Execution).where(Execution.external_trade_id == "7635814641"))
    audit_event = db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "protective_close_fill_backfilled")
    )

    assert inserted == 1
    assert client.trade_lookup_calls[0]["order_id"] == "1005151959307"
    assert execution is not None
    assert execution.order_id == order.id
    assert execution.position_id == position.id
    assert execution.fill_price == pytest.approx(81089.0)
    assert execution.fill_quantity == pytest.approx(0.001)
    assert execution.fee_paid == pytest.approx(0.04054449)
    assert execution.commission_asset == "USDT"
    assert execution.realized_pnl == pytest.approx(0.4476)
    assert execution.payload["exchange"] == "BINANCE"
    assert execution.payload["source"] == PROTECTIVE_CLOSE_FILL_BACKFILL_SOURCE
    assert execution.payload["exchange_order_id"] == "1005151959307"
    assert execution.payload["exchange_trade_id"] == "7635814641"
    assert execution.payload["client_order_id"] == "protective-btc-1"
    assert execution.payload["linked_protective_order_id"] == order.id
    assert execution.payload["trade"]["realizedPnl"] == "0.44760000"
    assert order.status == "filled"
    assert order.exchange_status == "FINISHED"
    assert order.filled_quantity == pytest.approx(0.001)
    assert order.average_fill_price == pytest.approx(81089.0)
    assert order.metadata_json["protective_close_fill_backfill"]["status"] == "backfilled"
    assert position.realized_pnl == pytest.approx(0.4476)
    assert position.metadata_json["closed_position_pnl"]["gross_realized_pnl"] == pytest.approx(0.4476)
    assert audit_event is not None
    assert audit_event.payload["inserted_trade_count"] == 1


def test_finished_protective_order_backfill_is_idempotent(db_session: Session) -> None:
    settings_row = get_or_create_settings(db_session)
    position = _position()
    db_session.add(position)
    db_session.flush()
    order = _protective_order(position_id=position.id, external_order_id="2000000895228311")
    db_session.add(order)
    db_session.flush()
    client = ProtectiveCloseFillBackfillClient(trades=[_binance_close_trade()])
    exchange_order = _finished_take_profit_order_payload()

    first_inserted = _backfill_finished_protective_order_trades(
        db_session,
        settings_row,
        client,  # type: ignore[arg-type]
        order,
        exchange_order,
        source="test",
    )
    second_inserted = _backfill_finished_protective_order_trades(
        db_session,
        settings_row,
        client,  # type: ignore[arg-type]
        order,
        exchange_order,
        source="test",
    )
    executions = list(db_session.scalars(select(Execution).where(Execution.external_trade_id == "7635814641")))

    assert first_inserted == 1
    assert second_inserted == 0
    assert len(executions) == 1
    assert order.metadata_json["protective_close_fill_backfill"]["inserted_trade_count"] == 0


def test_finished_protective_order_backfill_failure_marks_order_and_audit(db_session: Session) -> None:
    settings_row = get_or_create_settings(db_session)
    position = _position()
    db_session.add(position)
    db_session.flush()
    order = _protective_order(position_id=position.id, external_order_id="2000000895228311")
    db_session.add(order)
    db_session.flush()
    client = ProtectiveCloseFillBackfillClient(trade_error=RuntimeError("rate limit"))

    inserted = _backfill_finished_protective_order_trades(
        db_session,
        settings_row,
        client,  # type: ignore[arg-type]
        order,
        _finished_take_profit_order_payload(),
        source="test",
    )
    event = db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "protective_close_fill_backfill_failed")
    )

    assert inserted == 0
    assert db_session.scalar(select(Execution).where(Execution.order_id == order.id)) is None
    assert order.metadata_json["protective_close_fill_backfill"]["status"] == "failed"
    assert PROTECTIVE_CLOSE_FILL_BACKFILL_FAILED_REASON_CODE in order.reason_codes
    assert event is not None
    assert event.payload["reason_code"] == PROTECTIVE_CLOSE_FILL_BACKFILL_FAILED_REASON_CODE
    assert event.payload["error"] == "rate limit"


def test_missing_finished_protective_order_restart_path_backfills_orphaned_row(
    db_session: Session,
) -> None:
    settings_row = get_or_create_settings(db_session)
    position = _position()
    db_session.add(position)
    db_session.flush()
    order = _protective_order(position_id=position.id, external_order_id="2000000895228311")
    order.status = "canceled"
    order.exchange_status = "CANCELED"
    order.reason_codes = [CLOSED_POSITION_PROTECTIVE_ORDER_RECONCILED_REASON_CODE]
    db_session.add(order)
    db_session.flush()
    client = ProtectiveCloseFillBackfillClient(
        trades=[_binance_close_trade()],
        exchange_order=_finished_take_profit_order_payload(),
    )

    inserted = _backfill_missing_finished_protective_order_trades(
        db_session,
        settings_row,
        client,  # type: ignore[arg-type]
        symbol="BTCUSDT",
        observed_at=datetime(2026, 5, 6, 14, 8, 31),
        source="test_restart",
    )
    execution = db_session.scalar(select(Execution).where(Execution.external_trade_id == "7635814641"))

    assert inserted == 1
    assert client.order_lookup_calls == [{"algo_id": "2000000895228311", "client_algo_id": "protective-btc-1"}]
    assert execution is not None
    assert order.status == "filled"
    assert order.exchange_status == "FINISHED"
    assert CLOSED_POSITION_PROTECTIVE_ORDER_RECONCILED_REASON_CODE not in order.reason_codes


def test_finished_protective_order_without_trades_marks_backfill_pending(db_session: Session) -> None:
    settings_row = get_or_create_settings(db_session)
    position = _position()
    db_session.add(position)
    db_session.flush()
    order = _protective_order(position_id=position.id, external_order_id="2000000895228311")
    db_session.add(order)
    db_session.flush()
    client = ProtectiveCloseFillBackfillClient(trades=[])

    inserted = _backfill_finished_protective_order_trades(
        db_session,
        settings_row,
        client,  # type: ignore[arg-type]
        order,
        _finished_take_profit_order_payload(),
        source="test",
    )
    event = db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "protective_close_fill_backfill_pending")
    )

    assert inserted == 0
    assert order.metadata_json["protective_close_fill_backfill"]["status"] == "pending"
    assert PROTECTIVE_CLOSE_FILL_BACKFILL_PENDING_REASON_CODE in order.reason_codes
    assert event is not None
    assert event.payload["reason_code"] == PROTECTIVE_CLOSE_FILL_BACKFILL_PENDING_REASON_CODE


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
