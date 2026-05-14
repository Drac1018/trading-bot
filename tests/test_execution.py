from __future__ import annotations

from sqlalchemy import select
from trading_mvp.models import AuditEvent, Order, Position
from trading_mvp.services.execution import _build_protection_state, _create_protective_orders
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive


class ProtectiveOrderClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.orders: list[dict[str, object]] = []

    def normalize_price(self, symbol: str, price: float) -> float:
        return price

    def normalize_order_request(
        self,
        *,
        symbol: str,
        quantity: float | None = None,
        price: float | None = None,
        stop_price: float | None = None,
        reference_price: float | None = None,
        approved_notional: float | None = None,
        enforce_min_notional: bool = True,
        close_position: bool = False,
    ) -> dict[str, object]:
        effective_reference = price or stop_price or reference_price
        return {
            "symbol": symbol,
            "quantity": quantity,
            "price": price,
            "stop_price": stop_price,
            "reference_price": effective_reference,
            "notional": (quantity or 0.0) * (effective_reference or 0.0) if quantity is not None else None,
            "filters": {},
            "reason_code": None,
        }

    def new_order(self, **kwargs):
        self.calls.append(dict(kwargs))
        order_type = str(kwargs["order_type"])
        order_id = f"{order_type.lower()}-{len(self.calls)}"
        payload: dict[str, object] = {
            "orderId": order_id,
            "clientOrderId": kwargs.get("client_order_id") or order_id,
            "type": order_type,
            "side": kwargs.get("side"),
            "status": "NEW",
            "closePosition": "true" if kwargs.get("close_position") else "false",
            "reduceOnly": "true" if kwargs.get("reduce_only") else "false",
            "origQty": str(kwargs.get("quantity") or 0.0),
        }
        if kwargs.get("price") is not None:
            payload["price"] = str(kwargs["price"])
        if kwargs.get("stop_price") is not None:
            payload["stopPrice"] = str(kwargs["stop_price"])
        self.orders.append(payload)
        return payload


def _position(*, take_profit: float = 100.35) -> Position:
    return Position(
        symbol="BTCUSDT",
        mode="live",
        side="long",
        status="open",
        quantity=0.01,
        entry_price=100.0,
        mark_price=100.0,
        leverage=2.0,
        stop_loss=99.0,
        take_profit=take_profit,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        opened_at=utcnow_naive(),
        metadata_json={"position_management": {"holding_profile": "scalp"}},
    )


def test_tight_take_profit_uses_reduce_only_limit_when_enabled(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.use_limit_take_profit_for_tight_tp = True
    settings_row.tight_tp_bps_threshold = 40.0
    settings_row.tp_limit_post_only = True
    position = _position()
    db_session.add(position)
    db_session.flush()
    client = ProtectiveOrderClient()

    created_ids = _create_protective_orders(
        db_session,
        client,
        settings_row=settings_row,
        decision_run_id=None,
        risk_row=None,
        symbol="BTCUSDT",
        stop_loss=99.0,
        take_profit=100.35,
        parent_order=None,
        position=position,
        existing_open_orders=[],
    )

    assert len(created_ids) == 2
    stop_call = next(call for call in client.calls if call["order_type"] == "STOP_MARKET")
    tp_call = next(call for call in client.calls if call["order_type"] == "LIMIT")
    assert stop_call["close_position"] is True
    assert stop_call["reduce_only"] is True
    assert stop_call.get("price") is None
    assert tp_call["price"] == 100.35
    assert tp_call["time_in_force"] == "GTX"
    assert tp_call["reduce_only"] is True
    assert tp_call["close_position"] is False
    assert tp_call["quantity"] == 0.01

    protection_state = _build_protection_state(position, client.orders)
    assert protection_state["status"] == "protected"
    assert protection_state["has_stop_loss"] is True
    assert protection_state["has_take_profit"] is True

    tp_order = db_session.scalar(select(Order).where(Order.order_type == "limit"))
    assert tp_order is not None
    assert tp_order.reduce_only is True
    assert tp_order.close_only is False
    tp_policy = tp_order.metadata_json["take_profit_execution_policy"]
    assert tp_policy["take_profit_order_type"] == "LIMIT"
    assert tp_policy["tp_limit_enabled"] is True
    assert tp_policy["tp_limit_post_only"] is True

    audit_event = db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "protective_take_profit_order_created")
    )
    assert audit_event is not None
    assert audit_event.payload["take_profit_order_type"] == "LIMIT"
    assert audit_event.payload["tp_limit_enabled"] is True


def test_take_profit_market_remains_when_limit_setting_disabled(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.use_limit_take_profit_for_tight_tp = False
    settings_row.tight_tp_bps_threshold = 40.0
    position = _position()
    db_session.add(position)
    db_session.flush()
    client = ProtectiveOrderClient()

    _create_protective_orders(
        db_session,
        client,
        settings_row=settings_row,
        decision_run_id=None,
        risk_row=None,
        symbol="BTCUSDT",
        stop_loss=99.0,
        take_profit=100.35,
        parent_order=None,
        position=position,
        existing_open_orders=[],
    )

    assert {call["order_type"] for call in client.calls} == {"STOP_MARKET", "TAKE_PROFIT_MARKET"}
    stop_call = next(call for call in client.calls if call["order_type"] == "STOP_MARKET")
    tp_call = next(call for call in client.calls if call["order_type"] == "TAKE_PROFIT_MARKET")
    assert stop_call["close_position"] is True
    assert tp_call["close_position"] is True
    assert tp_call["reduce_only"] is True

    tp_order = db_session.scalar(select(Order).where(Order.order_type == "take_profit_market"))
    assert tp_order is not None
    tp_policy = tp_order.metadata_json["take_profit_execution_policy"]
    assert tp_policy["take_profit_order_type"] == "TAKE_PROFIT_MARKET"
    assert tp_policy["tp_limit_enabled"] is False
    assert tp_policy["tp_market_fallback_reason"] == "limit_take_profit_setting_disabled"
