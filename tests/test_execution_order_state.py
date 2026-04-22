from __future__ import annotations

from trading_mvp.models import Order
from trading_mvp.services.execution import _apply_exchange_order_state


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
