from __future__ import annotations

from trading_mvp.models import AuditEvent, Execution, Order, Position
from trading_mvp.services.dashboard import (
    get_audit_timeline,
    get_executions,
    get_orders,
    get_overview,
    get_positions,
)
from trading_mvp.services.settings import get_or_create_settings


def test_order_and_execution_filters(db_session) -> None:
    primary_order = Order(
        symbol="BTCUSDT",
        side="buy",
        order_type="market",
        mode="live",
        status="filled",
        external_order_id="btc-order-1",
        requested_quantity=0.01,
        requested_price=65000.0,
    )
    secondary_order = Order(
        symbol="ETHUSDT",
        side="sell",
        order_type="limit",
        mode="live",
        status="rejected",
        external_order_id="eth-order-1",
        requested_quantity=0.2,
        requested_price=3200.0,
    )
    db_session.add_all([primary_order, secondary_order])
    db_session.flush()

    db_session.add_all(
        [
            Execution(
                order_id=primary_order.id,
                symbol="BTCUSDT",
                status="filled",
                external_trade_id="btc-trade-1",
                fill_price=65010.0,
                fill_quantity=0.01,
                payload={},
            ),
            Execution(
                order_id=secondary_order.id,
                symbol="ETHUSDT",
                status="rejected",
                external_trade_id="eth-trade-1",
                fill_price=3195.0,
                fill_quantity=0.2,
                payload={},
            ),
        ]
    )
    db_session.flush()

    filtered_orders = get_orders(db_session, symbol="BTCUSDT", status="filled", search="btc")
    filtered_executions = get_executions(db_session, symbol="BTCUSDT", status="filled", search="btc")

    assert len(filtered_orders) == 1
    assert filtered_orders[0]["symbol"] == "BTCUSDT"
    assert len(filtered_executions) == 1
    assert filtered_executions[0]["symbol"] == "BTCUSDT"


def test_audit_filters(db_session) -> None:
    db_session.add_all(
        [
            AuditEvent(
                event_type="live_sync",
                entity_type="binance",
                entity_id="BTCUSDT",
                severity="info",
                message="Live exchange state synchronized.",
                payload={},
            ),
            AuditEvent(
                event_type="backlog_auto_applied",
                entity_type="product_backlog",
                entity_id="1",
                severity="warning",
                message="Supported backlog item was auto-applied.",
                payload={},
            ),
        ]
    )
    db_session.flush()

    filtered = get_audit_timeline(db_session, event_type="backlog_auto_applied", severity="warning", search="auto")

    assert len(filtered) == 1
    assert filtered[0]["event_type"] == "backlog_auto_applied"


def test_overview_and_positions_include_protection_status(db_session) -> None:
    settings = get_or_create_settings(db_session)
    settings.trading_paused = True
    settings.pause_reason_code = "PROTECTIVE_ORDER_FAILURE"
    settings.pause_origin = "system"
    settings.pause_reason_detail = {
        "detail": "protective verification failed",
        "auto_resume": {"status": "not_eligible", "blockers": ["MISSING_PROTECTIVE_ORDERS"]},
    }
    db_session.flush()

    position = Position(
        symbol="BTCUSDT",
        mode="live",
        side="long",
        status="open",
        quantity=0.01,
        entry_price=70000.0,
        mark_price=70100.0,
        leverage=2.0,
        stop_loss=69000.0,
        take_profit=72000.0,
        realized_pnl=0.0,
        unrealized_pnl=1.0,
        metadata_json={},
    )
    db_session.add(position)
    db_session.flush()

    db_session.add(
        Order(
            symbol="BTCUSDT",
            position_id=position.id,
            side="sell",
            order_type="stop_market",
            mode="live",
            status="pending",
            external_order_id="protect-stop-1",
            reduce_only=True,
            close_only=True,
            requested_quantity=0.01,
            requested_price=69000.0,
            filled_quantity=0.0,
            average_fill_price=0.0,
            metadata_json={},
        )
    )
    db_session.flush()

    overview = get_overview(db_session)
    positions = get_positions(db_session)

    assert overview.open_positions == 1
    assert overview.unprotected_positions == 1
    assert overview.operating_state == "PAUSED"
    assert overview.trading_paused is True
    assert overview.pause_reason_code == "PROTECTIVE_ORDER_FAILURE"
    assert overview.pause_origin == "system"
    assert overview.auto_resume_status == "not_eligible"
    assert overview.auto_resume_last_blockers == ["MISSING_PROTECTIVE_ORDERS"]
    assert overview.pause_severity == "critical"
    assert overview.pause_recovery_class == "portfolio_unsafe"
    assert overview.protection_recovery_status == "idle"
    assert overview.protection_recovery_active is False
    assert overview.missing_protection_symbols == ["BTCUSDT"]
    assert overview.missing_protection_items == {"BTCUSDT": ["take_profit"]}
    assert overview.position_protection_summary[0]["symbol"] == "BTCUSDT"
    assert overview.position_protection_summary[0]["missing_components"] == ["take_profit"]
    assert positions[0]["protected"] is False
    assert positions[0]["protective_order_count"] == 1
    assert positions[0]["missing_components"] == ["take_profit"]
