from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_mvp.config import get_settings
from trading_mvp.models import Execution, Order, Position, RiskCheck, Setting
from trading_mvp.schemas import (
    ExecutionIntent,
    MarketSnapshotPayload,
    RiskCheckResult,
    TradeDecision,
)
from trading_mvp.services.account import (
    create_exchange_pnl_snapshot,
    get_open_position,
    refresh_open_position_marks,
)
from trading_mvp.services.audit import create_alert, record_audit_event, record_health_event
from trading_mvp.services.binance import BinanceAPIError, BinanceClient
from trading_mvp.services.pause_control import (
    clear_symbol_protection_state,
    mark_manage_only_state,
    set_symbol_protection_state,
)
from trading_mvp.services.runtime_state import (
    DEGRADED_MANAGE_ONLY_STATE,
    EMERGENCY_EXIT_STATE,
    PROTECTION_RECOVERY_THRESHOLD,
    PROTECTION_REQUIRED_STATE,
    TRADABLE_STATE,
    get_operating_state,
    get_protection_recovery_detail,
)
from trading_mvp.services.settings import (
    get_effective_symbols,
    get_runtime_credentials,
    set_trading_pause,
)
from trading_mvp.time_utils import utcnow_naive

FINAL_ORDER_STATUSES = {"filled", "canceled", "rejected", "expired"}
AUTO_RESUME_DELAY_MINUTES = 5
PROTECTIVE_ORDER_TYPES = ("STOP_MARKET", "TAKE_PROFIT_MARKET")
PROTECTION_RETRY_ATTEMPTS = 2


def _entry_price(decision: TradeDecision, market_snapshot: MarketSnapshotPayload) -> float:
    if decision.entry_zone_min is not None and decision.entry_zone_max is not None:
        return (decision.entry_zone_min + decision.entry_zone_max) / 2
    return market_snapshot.latest_price


def _to_float(value: object, default: float = 0.0) -> float:
    if value in {None, ""}:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value))


def _build_client(settings_row: Setting) -> BinanceClient:
    credentials = get_runtime_credentials(settings_row)
    defaults = get_settings()
    return BinanceClient(
        api_key=credentials.binance_api_key,
        api_secret=credentials.binance_api_secret,
        testnet_enabled=settings_row.binance_testnet_enabled,
        futures_enabled=settings_row.binance_futures_enabled,
        recv_window_ms=defaults.exchange_recv_window_ms,
    )


def _classify_exchange_state_error(exc: Exception, default_reason: str) -> str:
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return "EXCHANGE_CONNECTIVITY_TEMPORARY_FAILURE"
    if isinstance(exc, BinanceAPIError) and exc.code in {-1021, -1001, -1007, -1003}:
        return "EXCHANGE_CONNECTIVITY_TEMPORARY_FAILURE"
    return default_reason


def _pause_for_system_issue(
    session: Session,
    settings_row: Setting,
    *,
    reason_code: str,
    symbol: str,
    error: str,
    event_type: str,
    component: str,
    alert_title: str,
    alert_message: str,
) -> None:
    set_trading_pause(
        session,
        True,
        reason_code=reason_code,
        reason_detail={"symbol": symbol, "error": error},
        pause_origin="system",
        auto_resume_after=utcnow_naive() + timedelta(minutes=AUTO_RESUME_DELAY_MINUTES),
        preserve_live_arm=True,
    )
    record_audit_event(
        session,
        event_type="trading_paused",
        entity_type="settings",
        entity_id=str(settings_row.id),
        severity="warning",
        message=alert_message,
        payload={
            "reason_code": reason_code,
            "pause_origin": "system",
            "symbol": symbol,
            "error": error,
        },
    )
    create_alert(
        session,
        category="execution",
        severity="error",
        title=alert_title,
        message=alert_message,
        payload={"reason_code": reason_code, "symbol": symbol, "error": error},
    )
    record_audit_event(
        session,
        event_type=event_type,
        entity_type=component,
        entity_id=symbol,
        severity="error",
        message=alert_message,
        payload={"reason_code": reason_code, "symbol": symbol, "error": error},
    )
    record_health_event(
        session,
        component=component,
        status="error",
        message=alert_message,
        payload={"reason_code": reason_code, "symbol": symbol, "error": error},
    )
    session.flush()


def _live_account_balances(account_info: dict[str, object]) -> dict[str, float]:
    available_balance = _to_float(account_info.get("availableBalance"))
    total_wallet_balance = _to_float(account_info.get("totalWalletBalance"))
    total_unrealized_profit = _to_float(account_info.get("totalUnrealizedProfit"))
    total_margin_balance = _to_float(account_info.get("totalMarginBalance"))

    equity = total_margin_balance if total_margin_balance > 0 else total_wallet_balance + total_unrealized_profit
    if equity <= 0:
        equity = total_wallet_balance
    sizing_equity = available_balance if available_balance > 0 else equity
    return {
        "available_balance": available_balance,
        "wallet_balance": total_wallet_balance,
        "unrealized_pnl": total_unrealized_profit,
        "equity": equity,
        "sizing_equity": max(sizing_equity, 0.0),
    }


def _calculate_quantity(entry_price: float, stop_loss: float | None, equity: float, risk_pct: float, leverage: float) -> float:
    if stop_loss is None:
        return max((equity * min(leverage, 1.0)) / max(entry_price, 1.0), 0.0001)
    per_unit_risk = abs(entry_price - stop_loss)
    if per_unit_risk == 0:
        return 0.0
    risk_budget = equity * risk_pct
    max_notional_quantity = (equity * leverage) / max(entry_price, 1.0)
    return round(min(risk_budget / per_unit_risk, max_notional_quantity), 6)


def _decision_matches_position_side(decision: TradeDecision, existing_position: Position | None) -> bool:
    if existing_position is None:
        return False
    target_side = "long" if decision.decision == "long" else "short"
    return existing_position.side == target_side


def _classify_execution_intent(
    decision: TradeDecision,
    existing_position: Position | None,
    *,
    operating_state: str,
) -> str:
    if decision.decision in {"reduce", "exit"}:
        return "reduce_only"
    if existing_position is not None and _decision_matches_position_side(decision, existing_position):
        if operating_state in {PROTECTION_REQUIRED_STATE, DEGRADED_MANAGE_ONLY_STATE}:
            return "protection"
        if existing_position.side in {"long", "short"}:
            return "scale_in"
    return "entry"


def _is_protective_order(order_payload: dict[str, object]) -> bool:
    order_type = str(order_payload.get("type", "")).upper()
    return order_type.startswith("STOP") or order_type.startswith("TAKE_PROFIT")


def _build_protection_state(position: Position | None, open_orders: list[dict[str, object]]) -> dict[str, object]:
    if position is None or position.status != "open" or position.quantity <= 0:
        return {
            "status": "flat",
            "protected": True,
            "has_stop_loss": False,
            "has_take_profit": False,
            "protective_order_count": 0,
            "protective_order_ids": [],
            "missing_components": [],
        }
    protective_orders = [item for item in open_orders if _is_protective_order(item)]
    has_stop_loss = any(str(item.get("type", "")).upper().startswith("STOP") for item in protective_orders)
    has_take_profit = any(str(item.get("type", "")).upper().startswith("TAKE_PROFIT") for item in protective_orders)
    missing_components: list[str] = []
    if not has_stop_loss:
        missing_components.append("stop_loss")
    if not has_take_profit:
        missing_components.append("take_profit")
    return {
        "status": "protected" if not missing_components else "missing",
        "protected": not missing_components,
        "has_stop_loss": has_stop_loss,
        "has_take_profit": has_take_profit,
        "protective_order_count": len(protective_orders),
        "protective_order_ids": [str(item.get("orderId", "")) for item in protective_orders if item.get("orderId")],
        "missing_components": missing_components,
    }


def _get_string_list(payload: dict[str, object], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item not in {None, ""}]


def _protective_bucket(order_payload: dict[str, object]) -> str | None:
    order_type = str(order_payload.get("type", "")).upper()
    if order_type.startswith("STOP"):
        return "stop_loss"
    if order_type.startswith("TAKE_PROFIT"):
        return "take_profit"
    return None


def _is_protective_order_type_name(order_type: str | None) -> bool:
    if not order_type:
        return False
    normalized = order_type.upper()
    return normalized.startswith("STOP") or normalized.startswith("TAKE_PROFIT")


def _is_algo_order_payload(order_payload: dict[str, object]) -> bool:
    if _is_protective_order(order_payload):
        return True
    return any(key in order_payload for key in ("algoId", "clientAlgoId"))


def _fetch_exchange_order(
    client: BinanceClient,
    *,
    symbol: str,
    order_type: str | None,
    order_id: str | None = None,
    client_order_id: str | None = None,
) -> dict[str, object]:
    if _is_protective_order_type_name(order_type) and hasattr(client, "get_algo_order"):
        try:
            return client.get_algo_order(algo_id=order_id, client_algo_id=client_order_id)  # type: ignore[attr-defined]
        except Exception:
            if not hasattr(client, "get_order"):
                raise
    return client.get_order(symbol=symbol, order_id=order_id, client_order_id=client_order_id)


def _cancel_exchange_order(
    client: BinanceClient,
    *,
    symbol: str,
    order_payload: dict[str, object] | None = None,
    order_type: str | None = None,
    order_id: str | None = None,
    client_order_id: str | None = None,
) -> dict[str, object]:
    payload = order_payload or {}
    remote_order_id = order_id or str(payload.get("orderId", "")) or None
    remote_client_order_id = client_order_id or str(payload.get("clientOrderId", "")) or None
    effective_order_type = order_type or str(payload.get("type", "") or "")
    if (_is_algo_order_payload(payload) or _is_protective_order_type_name(effective_order_type)) and hasattr(
        client,
        "cancel_algo_order",
    ):
        try:
            return client.cancel_algo_order(  # type: ignore[attr-defined]
                algo_id=remote_order_id,
                client_algo_id=remote_client_order_id,
            )
        except Exception:
            if not hasattr(client, "cancel_order"):
                raise
    return client.cancel_order(
        symbol=symbol,
        order_id=remote_order_id,
        client_order_id=remote_client_order_id,
    )


def _cancel_duplicate_protective_orders(
    session: Session,
    client: BinanceClient,
    *,
    symbol: str,
    open_orders: list[dict[str, object]],
    preferred_order_ids: list[int] | None = None,
) -> None:
    preferred = {str(item) for item in (preferred_order_ids or [])}
    orders_by_bucket: dict[str, list[dict[str, object]]] = {"stop_loss": [], "take_profit": []}
    for item in open_orders:
        bucket = _protective_bucket(item)
        if bucket is None:
            continue
        orders_by_bucket[bucket].append(item)

    for _bucket, items in orders_by_bucket.items():
        if len(items) <= 1:
            continue
        keep_item = next((item for item in items if str(item.get("orderId", "")) in preferred), items[0])
        keep_order_id = str(keep_item.get("orderId", ""))
        for item in items:
            order_id = str(item.get("orderId", ""))
            if order_id == keep_order_id:
                continue
            client_order_id = str(item.get("clientOrderId", ""))
            _cancel_exchange_order(
                client,
                symbol=symbol,
                order_payload=item,
                order_id=order_id or None,
                client_order_id=client_order_id or None,
            )
            local = None
            if order_id:
                local = session.scalar(select(Order).where(Order.external_order_id == order_id).limit(1))
            if local is None and client_order_id:
                local = session.scalar(select(Order).where(Order.client_order_id == client_order_id).limit(1))
            if local is not None:
                local.status = "canceled"
                local.exchange_status = "CANCELED"
                local.last_exchange_update_at = utcnow_naive()
                session.add(local)
    session.flush()


def _has_valid_protection_template(position: Position | None, stop_loss: float | None, take_profit: float | None) -> bool:
    if position is None or stop_loss is None or take_profit is None:
        return False
    reference_price = position.entry_price if position.entry_price > 0 else position.mark_price
    if reference_price <= 0:
        return False
    if position.side == "long":
        return stop_loss < reference_price and take_profit > reference_price
    return stop_loss > reference_price and take_profit < reference_price


def build_execution_intent(
    decision: TradeDecision,
    market_snapshot: MarketSnapshotPayload,
    risk_result: RiskCheckResult,
    settings_row: Setting,
    equity: float,
    existing_position: Position | None = None,
    operating_state: str = TRADABLE_STATE,
) -> ExecutionIntent:
    entry_price = _entry_price(decision, market_snapshot)
    intent_type = _classify_execution_intent(decision, existing_position, operating_state=operating_state)
    if intent_type == "protection" and existing_position is not None:
        quantity = max(existing_position.quantity, 0.0001)
        entry_price = existing_position.mark_price if existing_position.mark_price > 0 else existing_position.entry_price
        leverage = existing_position.leverage if existing_position.leverage > 0 else min(risk_result.approved_leverage, settings_row.max_leverage)
    else:
        quantity = _calculate_quantity(
            entry_price=entry_price,
            stop_loss=decision.stop_loss,
            equity=equity,
            risk_pct=risk_result.approved_risk_pct,
            leverage=risk_result.approved_leverage,
        )
        leverage = min(risk_result.approved_leverage, settings_row.max_leverage)
    return ExecutionIntent(
        symbol=decision.symbol,
        action=decision.decision,  # type: ignore[arg-type]
        intent_type=intent_type,  # type: ignore[arg-type]
        quantity=max(quantity, 0.0001),
        requested_price=entry_price,
        stop_loss=decision.stop_loss,
        take_profit=decision.take_profit,
        leverage=leverage,
        mode="live",
        reduce_only=decision.decision in {"reduce", "exit"},
        close_only=decision.decision == "exit",
    )


def _map_exchange_status(status: str) -> str:
    return {
        "NEW": "pending",
        "PARTIALLY_FILLED": "partially_filled",
        "FILLED": "filled",
        "CANCELED": "canceled",
        "REJECTED": "rejected",
        "EXPIRED": "expired",
        "EXPIRED_IN_MATCH": "expired",
    }.get(status.upper(), status.lower())


def _flag_enabled(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() == "true"


def _record_live_trades(session: Session, order: Order, trades: list[dict[str, object]]) -> tuple[float, float]:
    fee_total = 0.0
    realized_total = 0.0
    for trade in trades:
        trade_id = str(trade.get("id", ""))
        if not trade_id:
            continue
        existing = session.scalar(select(Execution).where(Execution.external_trade_id == trade_id).limit(1))
        if existing is not None:
            continue
        fill_price = _to_float(trade.get("price"))
        fill_quantity = abs(_to_float(trade.get("qty")))
        fee_paid = abs(_to_float(trade.get("commission")))
        realized_pnl = _to_float(trade.get("realizedPnl"))
        execution = Execution(
            order_id=order.id,
            position_id=order.position_id,
            symbol=order.symbol,
            status="filled",
            external_trade_id=trade_id,
            fill_price=fill_price,
            fill_quantity=fill_quantity,
            fee_paid=fee_paid,
            commission_asset=str(trade.get("commissionAsset", "")) or None,
            slippage_pct=abs(fill_price - order.requested_price) / max(order.requested_price, 1.0),
            realized_pnl=realized_pnl,
            payload={"trade": trade},
        )
        session.add(execution)
        fee_total += fee_paid
        realized_total += realized_pnl
    session.flush()
    return fee_total, realized_total


def _upsert_exchange_order_row(
    session: Session,
    *,
    symbol: str,
    requested_price: float,
    requested_quantity: float,
    order_type: str,
    side: str,
    exchange_order: dict[str, object],
    decision_run_id: int | None,
    risk_row: RiskCheck | None,
    reduce_only: bool,
    close_only: bool,
    parent_order_id: int | None = None,
) -> Order:
    external_order_id = str(exchange_order.get("orderId", "")) or None
    client_order_id = str(exchange_order.get("clientOrderId", "")) or None
    row = None
    if external_order_id:
        row = session.scalar(select(Order).where(Order.external_order_id == external_order_id).limit(1))
    if row is None and client_order_id:
        row = session.scalar(select(Order).where(Order.client_order_id == client_order_id).limit(1))
    if row is None:
        row = Order(
            symbol=symbol,
            decision_run_id=decision_run_id,
            risk_check_id=risk_row.id if risk_row is not None else None,
            position_id=None,
            side=side,
            order_type=order_type.lower(),
            mode="live",
            status="pending",
            external_order_id=external_order_id,
            client_order_id=client_order_id,
            reduce_only=reduce_only,
            close_only=close_only,
            parent_order_id=parent_order_id,
            requested_quantity=requested_quantity,
            requested_price=requested_price,
            filled_quantity=0.0,
            average_fill_price=0.0,
            reason_codes=[],
            metadata_json={},
        )
    row.mode = "live"
    row.side = side
    row.order_type = order_type.lower()
    row.reduce_only = reduce_only
    row.close_only = close_only
    row.parent_order_id = parent_order_id
    row.requested_quantity = requested_quantity
    row.requested_price = requested_price
    row.status = _map_exchange_status(str(exchange_order.get("status", "NEW")))
    row.exchange_status = str(exchange_order.get("status", "")) or None
    row.last_exchange_update_at = utcnow_naive()
    row.filled_quantity = abs(_to_float(exchange_order.get("executedQty"), row.filled_quantity))
    avg_price = _to_float(exchange_order.get("avgPrice") or exchange_order.get("price"), row.average_fill_price)
    if avg_price > 0:
        row.average_fill_price = avg_price
    row.metadata_json = {"exchange_order": exchange_order}
    session.add(row)
    session.flush()
    return row


def _create_rejected_order_row(
    session: Session,
    *,
    symbol: str,
    side: str,
    order_type: str,
    requested_quantity: float,
    requested_price: float,
    decision_run_id: int | None,
    risk_row: RiskCheck | None,
    reduce_only: bool,
    close_only: bool,
    reason_codes: list[str],
    metadata_json: dict[str, object],
) -> Order:
    row = Order(
        symbol=symbol,
        decision_run_id=decision_run_id,
        risk_check_id=risk_row.id if risk_row is not None else None,
        position_id=None,
        side=side,
        order_type=order_type.lower(),
        mode="live",
        status="rejected",
        external_order_id=None,
        client_order_id=None,
        reduce_only=reduce_only,
        close_only=close_only,
        parent_order_id=None,
        exchange_status="REJECTED",
        last_exchange_update_at=utcnow_naive(),
        requested_quantity=requested_quantity,
        requested_price=requested_price,
        filled_quantity=0.0,
        average_fill_price=0.0,
        reason_codes=reason_codes,
        metadata_json=metadata_json,
    )
    session.add(row)
    session.flush()
    return row


def _safe_submit_order(
    client: BinanceClient,
    *,
    symbol: str,
    side: str,
    order_type: str,
    quantity: float | None = None,
    stop_price: float | None = None,
    reduce_only: bool = False,
    close_position: bool = False,
    response_type: str = "RESULT",
) -> tuple[str, dict[str, object]]:
    client_order_id = f"mvp-{uuid4().hex[:24]}"
    try:
        response = client.new_order(
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            stop_price=stop_price,
            reduce_only=reduce_only,
            close_position=close_position,
            client_order_id=client_order_id,
            response_type=response_type,
        )
    except (httpx.TimeoutException, httpx.TransportError):
        response = _fetch_exchange_order(
            client,
            symbol=symbol,
            order_type=order_type,
            client_order_id=client_order_id,
        )
    return client_order_id, response


def _protective_prices(open_orders: list[dict[str, object]], existing: Position | None) -> tuple[float | None, float | None]:
    stop_loss = existing.stop_loss if existing is not None else None
    take_profit = existing.take_profit if existing is not None else None
    for item in open_orders:
        stop_price_raw = item.get("stopPrice")
        if stop_price_raw in {None, "", "0", 0}:
            continue
        stop_price = _to_float(stop_price_raw)
        order_type = str(item.get("type", "")).upper()
        if order_type.startswith("STOP"):
            stop_loss = stop_price
        elif order_type.startswith("TAKE_PROFIT"):
            take_profit = stop_price
    return stop_loss, take_profit


def sync_live_positions(
    session: Session,
    settings_row: Setting,
    *,
    symbol: str,
    client: BinanceClient | None = None,
    open_orders: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    client = client or _build_client(settings_row)
    open_orders = open_orders if open_orders is not None else client.get_open_orders(symbol)
    remote_positions = client.get_position_information(symbol)
    active_remote = next((item for item in remote_positions if abs(_to_float(item.get("positionAmt"))) > 0), None)
    local = get_open_position(session, symbol)
    if active_remote is None:
        if local is not None:
            local.status = "closed"
            local.quantity = 0.0
            local.closed_at = utcnow_naive()
            session.add(local)
            session.flush()
        return {"symbol": symbol, "status": "flat"}

    position_amount = _to_float(active_remote.get("positionAmt"))
    entry_price = _to_float(active_remote.get("entryPrice"))
    mark_price = _to_float(active_remote.get("markPrice"), entry_price)
    leverage = _to_float(active_remote.get("leverage"), 1.0)
    quantity = abs(position_amount)
    side = "long" if position_amount > 0 else "short"
    stop_loss, take_profit = _protective_prices(open_orders, local)
    if local is None:
        local = Position(
            symbol=symbol,
            mode="live",
            side=side,
            status="open",
            quantity=quantity,
            entry_price=entry_price,
            mark_price=mark_price,
            leverage=leverage,
            stop_loss=stop_loss or mark_price,
            take_profit=take_profit or mark_price,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            metadata_json={"origin": "binance_sync"},
        )
        session.add(local)
        session.flush()
    else:
        local.mode = "live"
        local.side = side
        local.status = "open"
        local.quantity = quantity
        local.entry_price = entry_price
        local.mark_price = mark_price
        local.leverage = leverage
        local.stop_loss = stop_loss or local.stop_loss or mark_price
        local.take_profit = take_profit or local.take_profit or mark_price
        local.closed_at = None
        session.add(local)
        session.flush()
    local.unrealized_pnl = (mark_price - entry_price) * quantity if side == "long" else (entry_price - mark_price) * quantity
    session.add(local)
    session.flush()
    return {"symbol": symbol, "status": "open", "position_id": local.id, "quantity": local.quantity, "side": local.side}


def _cancel_exit_orders(session: Session, client: BinanceClient, symbol: str) -> None:
    for item in client.get_open_orders(symbol):
        if not _flag_enabled(item.get("closePosition")) and not _flag_enabled(item.get("reduceOnly")):
            continue
        external_order_id = str(item.get("orderId", ""))
        client_order_id = str(item.get("clientOrderId", ""))
        _cancel_exchange_order(
            client,
            symbol=symbol,
            order_payload=item,
            order_id=external_order_id or None,
            client_order_id=client_order_id or None,
        )
        local = None
        if external_order_id:
            local = session.scalar(select(Order).where(Order.external_order_id == external_order_id).limit(1))
        if local is None and client_order_id:
            local = session.scalar(select(Order).where(Order.client_order_id == client_order_id).limit(1))
        if local is not None:
            local.status = "canceled"
            local.exchange_status = "CANCELED"
            local.last_exchange_update_at = utcnow_naive()
            session.add(local)
    session.flush()


def _create_protective_orders(
    session: Session,
    client: BinanceClient,
    *,
    decision_run_id: int | None,
    risk_row: RiskCheck | None,
    symbol: str,
    stop_loss: float | None,
    take_profit: float | None,
    parent_order: Order | None,
    position: Position | None,
    existing_open_orders: list[dict[str, object]] | None = None,
) -> list[int]:
    if position is None or stop_loss is None or take_profit is None:
        return []
    exit_side = "SELL" if position.side == "long" else "BUY"
    created_ids: list[int] = []
    current_state = _build_protection_state(position, existing_open_orders or [])
    missing_components = _get_string_list(current_state, "missing_components")
    requested_orders: list[tuple[str, float]] = []
    if "stop_loss" in missing_components:
        requested_orders.append(("STOP_MARKET", client.normalize_price(symbol, stop_loss)))
    if "take_profit" in missing_components:
        requested_orders.append(("TAKE_PROFIT_MARKET", client.normalize_price(symbol, take_profit)))
    for order_type, stop_price in requested_orders:
        client_order_id, exchange_order = _safe_submit_order(
            client,
            symbol=symbol,
            side=exit_side,
            order_type=order_type,
            stop_price=stop_price,
            close_position=True,
            response_type="ACK",
        )
        row = _upsert_exchange_order_row(
            session,
            symbol=symbol,
            requested_price=stop_price,
            requested_quantity=position.quantity,
            order_type=order_type,
            side=exit_side.lower(),
            exchange_order={**exchange_order, "clientOrderId": client_order_id},
            decision_run_id=decision_run_id,
            risk_row=risk_row,
            reduce_only=True,
            close_only=True,
            parent_order_id=parent_order.id if parent_order is not None else None,
        )
        row.position_id = position.id
        session.add(row)
        session.flush()
        created_ids.append(row.id)
    return created_ids


def _pause_for_protection_failure(
    session: Session,
    settings_row: Setting,
    *,
    reason_code: str,
    symbol: str,
    position: Position | None,
    protective_state: dict[str, object],
    detail: str,
    emergency_result: dict[str, object] | None = None,
) -> None:
    emergency_completed = bool(
        emergency_result is not None
        and emergency_result.get("status") == "completed"
        and _to_float(emergency_result.get("remaining_position"), 0.0) <= 0.0
    )
    if emergency_completed:
        clear_symbol_protection_state(
            session,
            settings_row,
            symbol=symbol,
            trigger_source=f"protection_failure:{reason_code}:recovered_flat",
        )
    else:
        mark_manage_only_state(
            session,
            settings_row,
            symbol=symbol,
            trigger_source=f"protection_failure:{reason_code}",
            missing_components=_get_string_list(protective_state, "missing_components"),
            last_error=detail,
            emergency_action=emergency_result or None,
        )
    payload = {
        "reason_code": reason_code,
        "operating_state": TRADABLE_STATE if emergency_completed else DEGRADED_MANAGE_ONLY_STATE,
        "symbol": symbol,
        "position_size": position.quantity if position is not None else 0.0,
        "protective_state": protective_state,
        "detail": detail,
        "emergency_result": emergency_result or {},
    }
    record_audit_event(
        session,
        event_type="protection_manage_only_enabled",
        entity_type="settings",
        entity_id=str(settings_row.id),
        severity="critical",
        message="Trading entry was blocked and management-only mode was enabled after a protection failure.",
        payload=payload,
    )
    create_alert(
        session,
        category="execution",
        severity="critical",
        title="Unprotected live position detected",
        message="포지션 보호 주문이 없거나 검증되지 않아 비상 청산 후 거래를 중지했습니다.",
        payload=payload,
    )
    record_health_event(
        session,
        component="live_execution",
        status="critical",
        message="Unprotected live position triggered emergency handling.",
        payload=payload,
    )
    session.flush()


def _emergency_close_position(
    session: Session,
    settings_row: Setting,
    client: BinanceClient,
    *,
    symbol: str,
    position: Position | None,
    reason: str,
    protection_state: dict[str, object],
) -> dict[str, object]:
    if position is None or position.status != "open" or position.quantity <= 0:
        return {"status": "skipped", "reason": "NO_OPEN_POSITION"}

    set_symbol_protection_state(
        session,
        settings_row,
        symbol=symbol,
        state=EMERGENCY_EXIT_STATE,
        trigger_source=reason,
        missing_components=_get_string_list(protection_state, "missing_components"),
        auto_recovery_active=False,
        recovery_status="emergency_exit",
        last_error=None,
    )
    side = "SELL" if position.side == "long" else "BUY"
    reference_price = position.mark_price if position.mark_price > 0 else position.entry_price
    quantity = client.normalize_order_quantity(
        symbol,
        position.quantity,
        reference_price=reference_price,
        enforce_min_notional=False,
    )
    trigger_payload = {
        "symbol": symbol,
        "position_size": position.quantity,
        "reason": reason,
        "protective_state": protection_state,
        "quantity": quantity,
    }
    record_audit_event(
        session,
        event_type="emergency_exit_triggered",
        entity_type="position",
        entity_id=str(position.id),
        severity="critical",
        message="Emergency exit triggered for unprotected live position.",
        payload=trigger_payload,
    )

    try:
        client_order_id, exchange_order = _safe_submit_order(
            client,
            symbol=symbol,
            side=side,
            order_type="MARKET",
            quantity=quantity,
            reduce_only=True,
            response_type="RESULT",
        )
        order = _upsert_exchange_order_row(
            session,
            symbol=symbol,
            requested_price=reference_price,
            requested_quantity=quantity,
            order_type="MARKET",
            side="exit",
            exchange_order={**exchange_order, "clientOrderId": client_order_id},
            decision_run_id=None,
            risk_row=None,
            reduce_only=True,
            close_only=True,
            parent_order_id=None,
        )
        order.position_id = position.id
        session.add(order)
        trades = client.get_account_trades(symbol=symbol, order_id=order.external_order_id)
        fee_paid, realized_pnl = _record_live_trades(session, order, trades)
        remaining_orders = client.get_open_orders(symbol)
        sync_live_positions(session, settings_row, symbol=symbol, client=client, open_orders=remaining_orders)
        remaining_position = get_open_position(session, symbol)
        if remaining_position is None:
            _cancel_exit_orders(session, client, symbol)
            clear_symbol_protection_state(
                session,
                settings_row,
                symbol=symbol,
                trigger_source=f"{reason}:flat_after_emergency_exit",
            )
        payload = {
            **trigger_payload,
            "order_id": order.id,
            "exchange_status": order.exchange_status,
            "fill_quantity": order.filled_quantity,
            "fees": fee_paid,
            "realized_pnl": realized_pnl,
            "remaining_position": remaining_position.quantity if remaining_position is not None else 0.0,
        }
        record_audit_event(
            session,
            event_type="emergency_exit_completed",
            entity_type="order",
            entity_id=str(order.id),
            severity="critical",
            message="Emergency exit completed for unprotected live position.",
            payload=payload,
        )
        return {
            "status": "completed",
            "order_id": order.id,
            "fill_quantity": order.filled_quantity,
            "fees": fee_paid,
            "realized_pnl": realized_pnl,
            "remaining_position": remaining_position.quantity if remaining_position is not None else 0.0,
        }
    except Exception as exc:
        payload = {**trigger_payload, "error": str(exc)}
        record_audit_event(
            session,
            event_type="emergency_exit_failed",
            entity_type="position",
            entity_id=str(position.id),
            severity="critical",
            message="Emergency exit failed for unprotected live position.",
            payload=payload,
        )
        create_alert(
            session,
            category="execution",
            severity="critical",
            title="Emergency exit failed",
            message="무보호 포지션 비상 청산에 실패했습니다.",
            payload=payload,
        )
        record_health_event(
            session,
            component="live_execution",
            status="critical",
            message="Emergency exit failed for unprotected live position.",
            payload=payload,
        )
        session.flush()
        return {"status": "failed", "error": str(exc)}


def _ensure_protected_position(
    session: Session,
    settings_row: Setting,
    client: BinanceClient,
    *,
    symbol: str,
    position: Position | None,
    stop_loss: float | None,
    take_profit: float | None,
    decision_run_id: int | None,
    risk_row: RiskCheck | None,
    parent_order: Order | None,
    trigger_source: str,
    pause_reason_code: str,
) -> dict[str, object]:
    open_orders = client.get_open_orders(symbol)
    protection_state = _build_protection_state(position, open_orders)
    if protection_state["status"] == "protected":
        clear_symbol_protection_state(
            session,
            settings_row,
            symbol=symbol,
            trigger_source=f"{trigger_source}:already_protected",
        )
        record_audit_event(
            session,
            event_type="protection_recovery_succeeded",
            entity_type="position",
            entity_id=str(position.id if position is not None else symbol),
            severity="info",
            message="Protective order verification confirmed exchange-resident protection.",
            payload={"symbol": symbol, "trigger_source": trigger_source, "protective_state": protection_state},
        )
        return {
            "status": "protected",
            "protection_state": protection_state,
            "created_order_ids": [],
            "emergency_action": None,
        }

    failure_detail = get_protection_recovery_detail(settings_row)
    failure_count = int(
        dict(failure_detail.get("symbol_states", {})).get(symbol, {}).get("failure_count", 0) or 0
    )
    set_symbol_protection_state(
        session,
        settings_row,
        symbol=symbol,
        state=PROTECTION_REQUIRED_STATE,
        trigger_source=trigger_source,
        missing_components=_get_string_list(protection_state, "missing_components"),
        auto_recovery_active=True,
        recovery_status="recreating",
        last_error=None,
    )
    record_audit_event(
        session,
        event_type="unprotected_position_detected",
        entity_type="position",
        entity_id=str(position.id if position is not None else symbol),
        severity="critical",
        message="Live position is missing exchange-resident protective orders.",
        payload={
            "symbol": symbol,
            "trigger_source": trigger_source,
            "position_size": position.quantity if position is not None else 0.0,
            "protective_state": protection_state,
        },
    )
    record_audit_event(
        session,
        event_type="protection_verification_failed",
        entity_type="position",
        entity_id=str(position.id if position is not None else symbol),
        severity="critical",
        message="Protective order verification failed.",
        payload={
            "symbol": symbol,
            "trigger_source": trigger_source,
            "protective_state": protection_state,
        },
    )

    recreate_error: str | None = None
    created_order_ids: list[int] = []
    position_entity_id = str(position.id) if position is not None else symbol
    if _has_valid_protection_template(position, stop_loss, take_profit):
        for attempt in range(1, PROTECTION_RETRY_ATTEMPTS + 1):
            record_audit_event(
                session,
                event_type="protection_recreate_attempted",
                entity_type="position",
                entity_id=position_entity_id,
                severity="warning",
                message="Attempting to recreate missing protective orders.",
                payload={
                    "symbol": symbol,
                    "attempt": attempt,
                    "trigger_source": trigger_source,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                },
            )
            try:
                created_order_ids.extend(
                    _create_protective_orders(
                        session,
                        client,
                        decision_run_id=decision_run_id,
                        risk_row=risk_row,
                        symbol=symbol,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        parent_order=parent_order,
                        position=position,
                        existing_open_orders=open_orders,
                    )
                )
                open_orders = client.get_open_orders(symbol)
                _cancel_duplicate_protective_orders(
                    session,
                    client,
                    symbol=symbol,
                    open_orders=open_orders,
                    preferred_order_ids=created_order_ids,
                )
                open_orders = client.get_open_orders(symbol)
                protection_state = _build_protection_state(position, open_orders)
                if protection_state["status"] == "protected":
                    clear_symbol_protection_state(
                        session,
                        settings_row,
                        symbol=symbol,
                        trigger_source=f"{trigger_source}:protected_recreated",
                    )
                    record_audit_event(
                        session,
                        event_type="protection_recovery_succeeded",
                        entity_type="position",
                        entity_id=position_entity_id,
                        severity="info",
                        message="Protective orders were recreated and verified on the exchange.",
                        payload={
                            "symbol": symbol,
                            "trigger_source": trigger_source,
                            "created_order_ids": created_order_ids,
                            "protective_state": protection_state,
                        },
                    )
                    return {
                        "status": "protected_recreated",
                        "protection_state": protection_state,
                        "created_order_ids": created_order_ids,
                        "emergency_action": None,
                    }
            except Exception as exc:
                recreate_error = str(exc)
        record_audit_event(
            session,
            event_type="protection_recreate_failed",
            entity_type="position",
            entity_id=position_entity_id,
            severity="critical",
            message="Protective order recreation failed.",
            payload={
                "symbol": symbol,
                "trigger_source": trigger_source,
                "error": recreate_error,
                "protective_state": protection_state,
            },
        )
    else:
        recreate_error = "Local stop loss / take profit template was unavailable."

    next_failure_count = failure_count + 1
    if next_failure_count >= PROTECTION_RECOVERY_THRESHOLD:
        mark_manage_only_state(
            session,
            settings_row,
            symbol=symbol,
            trigger_source=f"{trigger_source}:recovery_failed",
            missing_components=_get_string_list(protection_state, "missing_components"),
            last_error=recreate_error or "Protective order recovery failed.",
        )

    emergency_result = _emergency_close_position(
        session,
        settings_row,
        client,
        symbol=symbol,
        position=position,
        reason=f"{trigger_source}:{pause_reason_code}",
        protection_state=protection_state,
    )
    _pause_for_protection_failure(
        session,
        settings_row,
        reason_code=pause_reason_code,
        symbol=symbol,
        position=position,
        protective_state=protection_state,
        detail=recreate_error or "Protective orders were missing and emergency exit was triggered.",
        emergency_result=emergency_result,
    )
    if emergency_result.get("status") != "completed":
        mark_manage_only_state(
            session,
            settings_row,
            symbol=symbol,
            trigger_source=f"{trigger_source}:emergency_failed",
            missing_components=_get_string_list(protection_state, "missing_components"),
            last_error=recreate_error or "Emergency exit failed after protective recovery failure.",
            emergency_action=emergency_result,
        )
    return {
        "status": "emergency_exit",
        "protection_state": protection_state,
        "created_order_ids": created_order_ids,
        "emergency_action": emergency_result,
        "error": recreate_error,
    }


def sync_live_state(session: Session, settings_row: Setting, *, symbol: str | None = None) -> dict[str, object]:
    client = _build_client(settings_row)
    symbols = [symbol.upper()] if symbol else get_effective_symbols(settings_row)
    synced_orders = 0
    synced_positions = 0
    symbol_protection_state: dict[str, dict[str, object]] = {}
    unprotected_positions: list[str] = []
    emergency_actions_taken: list[dict[str, object]] = []
    for item_symbol in symbols:
        live_orders = list(
            session.scalars(
                select(Order)
                .where(Order.mode == "live", Order.symbol == item_symbol, Order.status.notin_(FINAL_ORDER_STATUSES))
            )
        )
        for order in live_orders:
            if not order.external_order_id and not order.client_order_id:
                continue
            try:
                exchange_order = _fetch_exchange_order(
                    symbol=order.symbol,
                    client=client,
                    order_type=order.order_type,
                    order_id=order.external_order_id,
                    client_order_id=order.client_order_id,
                )
            except Exception as exc:
                reason_code = _classify_exchange_state_error(exc, "TEMPORARY_SYNC_FAILURE")
                _pause_for_system_issue(
                    session,
                    settings_row,
                    reason_code=reason_code,
                    symbol=order.symbol,
                    error=str(exc),
                    event_type="live_order_sync_failed",
                    component="live_sync",
                    alert_title="Live order sync failed",
                    alert_message="거래소 주문 상태를 동기화하지 못해 거래를 일시 중지했습니다.",
                )
                raise RuntimeError(f"{reason_code}: {exc}") from exc
            order.status = _map_exchange_status(str(exchange_order.get("status", "NEW")))
            order.exchange_status = str(exchange_order.get("status", "")) or None
            order.last_exchange_update_at = utcnow_naive()
            order.filled_quantity = abs(_to_float(exchange_order.get("executedQty"), order.filled_quantity))
            avg_price = _to_float(exchange_order.get("avgPrice") or exchange_order.get("price"), order.average_fill_price)
            if avg_price > 0:
                order.average_fill_price = avg_price
            session.add(order)
            if _is_protective_order_type_name(order.order_type):
                synced_orders += 1
                continue
            try:
                trades = client.get_account_trades(symbol=order.symbol, order_id=order.external_order_id)
            except Exception as exc:
                reason_code = _classify_exchange_state_error(exc, "TEMPORARY_SYNC_FAILURE")
                _pause_for_system_issue(
                    session,
                    settings_row,
                    reason_code=reason_code,
                    symbol=order.symbol,
                    error=str(exc),
                    event_type="live_trade_sync_failed",
                    component="live_sync",
                    alert_title="Live trade sync failed",
                    alert_message="거래소 체결 내역을 동기화하지 못해 거래를 일시 중지했습니다.",
                )
                raise RuntimeError(f"{reason_code}: {exc}") from exc
            _record_live_trades(session, order, trades)
            synced_orders += 1
        try:
            open_orders = client.get_open_orders(item_symbol)
        except Exception as exc:
            reason_code = _classify_exchange_state_error(exc, "EXCHANGE_OPEN_ORDERS_SYNC_FAILED")
            _pause_for_system_issue(
                session,
                settings_row,
                reason_code=reason_code,
                symbol=item_symbol,
                error=str(exc),
                event_type="live_open_orders_sync_failed",
                component="live_sync",
                alert_title="Open orders sync failed",
                alert_message="거래소 미체결 주문을 동기화하지 못해 거래를 일시 중지했습니다.",
            )
            raise RuntimeError(f"{reason_code}: {exc}") from exc
        try:
            sync_live_positions(session, settings_row, symbol=item_symbol, client=client, open_orders=open_orders)
        except Exception as exc:
            reason_code = _classify_exchange_state_error(exc, "EXCHANGE_POSITION_SYNC_FAILED")
            _pause_for_system_issue(
                session,
                settings_row,
                reason_code=reason_code,
                symbol=item_symbol,
                error=str(exc),
                event_type="live_position_sync_failed",
                component="live_sync",
                alert_title="Position sync failed",
                alert_message="거래소 포지션 상태를 동기화하지 못해 거래를 일시 중지했습니다.",
            )
            raise RuntimeError(f"{reason_code}: {exc}") from exc
        position = get_open_position(session, item_symbol)
        protection_state = _build_protection_state(position, open_orders)
        symbol_protection_state[item_symbol] = protection_state
        if protection_state["status"] == "missing":
            unprotected_positions.append(item_symbol)
            protection_result = _ensure_protected_position(
                session,
                settings_row,
                client,
                symbol=item_symbol,
                position=position,
                stop_loss=position.stop_loss if position is not None else None,
                take_profit=position.take_profit if position is not None else None,
                decision_run_id=None,
                risk_row=None,
                parent_order=None,
                trigger_source="sync_live_state",
                pause_reason_code="MISSING_PROTECTIVE_ORDERS",
            )
            symbol_protection_state[item_symbol] = protection_result["protection_state"]  # type: ignore[assignment]
            if protection_result.get("emergency_action") is not None:
                emergency_actions_taken.append(
                    {
                        "symbol": item_symbol,
                        "action": protection_result.get("status"),
                        "result": protection_result["emergency_action"],
                    }
                )
        else:
            clear_symbol_protection_state(
                session,
                settings_row,
                symbol=item_symbol,
                trigger_source="sync_live_state:protected_or_flat",
            )
        synced_positions += 1
    latest_prices = {
        item_symbol: position.mark_price
        for item_symbol in symbols
        if (position := get_open_position(session, item_symbol)) is not None
    }
    if latest_prices:
        refresh_open_position_marks(session, latest_prices)
    try:
        account_info = client.get_account_info()
    except Exception as exc:
        reason_code = _classify_exchange_state_error(exc, "EXCHANGE_ACCOUNT_STATE_UNAVAILABLE")
        _pause_for_system_issue(
            session,
            settings_row,
            reason_code=reason_code,
            symbol=symbols[0],
            error=str(exc),
            event_type="live_account_sync_failed",
            component="live_sync",
            alert_title="Live account state unavailable",
            alert_message="거래소 계좌 상태를 읽지 못해 거래를 일시 중지했습니다.",
        )
        raise RuntimeError(f"{reason_code}: {exc}") from exc
    pnl_snapshot = create_exchange_pnl_snapshot(session, settings_row, account_info)
    runtime_state = get_protection_recovery_detail(settings_row)
    return {
        "symbols": symbols,
        "synced_orders": synced_orders,
        "synced_positions": synced_positions,
        "equity": pnl_snapshot.equity,
        "symbol_protection_state": symbol_protection_state,
        "unprotected_positions": unprotected_positions,
        "emergency_actions_taken": emergency_actions_taken,
        "operating_state": get_operating_state(settings_row),
        "protection_recovery_status": str(runtime_state.get("status", "idle")),
        "protection_recovery_active": bool(runtime_state.get("auto_recovery_active", False)),
        "missing_protection_symbols": [str(item) for item in runtime_state.get("missing_symbols", []) if item],
        "missing_protection_items": {
            str(key): [str(item) for item in value]
            for key, value in runtime_state.get("missing_items", {}).items()
            if isinstance(value, list)
        },
    }


def run_live_test_order(session: Session, settings_row: Setting, *, symbol: str, side: str, quantity: float | None = None) -> dict[str, object]:
    client = _build_client(settings_row)
    filters = client.get_symbol_filters(symbol)
    reference_price = client.get_symbol_price(symbol)
    requested_quantity = quantity or filters["min_qty"] or 0.001
    normalized_quantity = client.normalize_order_quantity(
        symbol,
        requested_quantity,
        reference_price=reference_price,
        enforce_min_notional=True,
    )
    client.test_new_order(symbol=symbol, side=side, quantity=normalized_quantity)
    record_audit_event(
        session,
        event_type="live_test_order",
        entity_type="binance",
        entity_id=symbol,
        severity="info",
        message="Binance live test order preflight succeeded.",
        payload={
            "symbol": symbol,
            "side": side,
            "requested_quantity": requested_quantity,
            "quantity": normalized_quantity,
            "reference_price": reference_price,
            "min_notional": filters["min_notional"],
        },
    )
    return {
        "ok": True,
        "symbol": symbol,
        "side": side,
        "requested_quantity": requested_quantity,
        "quantity": normalized_quantity,
        "reference_price": reference_price,
        "min_notional": filters["min_notional"],
    }


def execute_live_trade(
    session: Session,
    settings_row: Setting,
    decision_run_id: int,
    decision: TradeDecision,
    market_snapshot: MarketSnapshotPayload,
    risk_result: RiskCheckResult,
    risk_row: RiskCheck | None = None,
) -> dict[str, Any]:
    client = _build_client(settings_row)
    try:
        account_info = client.get_account_info()
    except Exception as exc:
        reason_code = _classify_exchange_state_error(exc, "EXCHANGE_ACCOUNT_STATE_UNAVAILABLE")
        _pause_for_system_issue(
            session,
            settings_row,
            reason_code=reason_code,
            symbol=decision.symbol,
            error=str(exc),
            event_type="live_account_state_unavailable",
            component="live_execution",
            alert_title="Live account state unavailable",
            alert_message="?? ??? ??? ? ?? ??? ?? ??????.",
        )
        record_audit_event(
            session,
            event_type="live_execution_skipped",
            entity_type="decision_run",
            entity_id=str(decision_run_id),
            severity="error",
            message="Live execution skipped because exchange account state was unavailable.",
            payload={"symbol": decision.symbol, "error": str(exc), "reason_code": reason_code},
        )
        session.flush()
        return {"status": "error", "reason_codes": [reason_code], "error": str(exc)}

    latest_pnl = create_exchange_pnl_snapshot(session, settings_row, account_info)
    session.refresh(latest_pnl)
    live_balances = _live_account_balances(account_info)

    if decision.decision == "hold":
        return {"status": "skipped", "reason_codes": ["HOLD_DECISION"]}

    try:
        open_orders = client.get_open_orders(decision.symbol)
    except Exception as exc:
        reason_code = _classify_exchange_state_error(exc, "EXCHANGE_OPEN_ORDERS_SYNC_FAILED")
        _pause_for_system_issue(
            session,
            settings_row,
            reason_code=reason_code,
            symbol=decision.symbol,
            error=str(exc),
            event_type="live_preflight_open_orders_failed",
            component="live_execution",
            alert_title="Pre-trade open orders sync failed",
            alert_message="?? ?? ??? ???? ?? ??? ?? ??????.",
        )
        return {"status": "error", "reason_codes": [reason_code], "error": str(exc)}

    try:
        sync_live_positions(session, settings_row, symbol=decision.symbol, client=client, open_orders=open_orders)
    except Exception as exc:
        reason_code = _classify_exchange_state_error(exc, "EXCHANGE_POSITION_SYNC_FAILED")
        _pause_for_system_issue(
            session,
            settings_row,
            reason_code=reason_code,
            symbol=decision.symbol,
            error=str(exc),
            event_type="live_preflight_position_sync_failed",
            component="live_execution",
            alert_title="Pre-trade position sync failed",
            alert_message="?? ??? ??? ???? ?? ??? ?? ??????.",
        )
        return {"status": "error", "reason_codes": [reason_code], "error": str(exc)}

    existing_position = get_open_position(session, decision.symbol)
    operating_state = get_operating_state(settings_row)
    intent = build_execution_intent(
        decision,
        market_snapshot,
        risk_result,
        settings_row,
        live_balances["sizing_equity"] if live_balances["sizing_equity"] > 0 else latest_pnl.equity,
        existing_position=existing_position,
        operating_state=operating_state,
    )
    intent_type = intent.intent_type
    pre_trade_protection = _build_protection_state(existing_position, open_orders)

    if decision.decision in {"long", "short"}:
        target_side = "long" if decision.decision == "long" else "short"
        if existing_position is not None and existing_position.side != target_side:
            create_alert(
                session,
                category="execution",
                severity="warning",
                title="Opposite live position open",
                message="?? ?? ???? ?? ?? ?? ??? ??????.",
                payload={"symbol": decision.symbol, "existing_side": existing_position.side, "target_side": target_side},
            )
            return {"status": "rejected", "reason_codes": ["OPPOSITE_LIVE_POSITION_OPEN"], "intent_type": intent_type}

    if intent_type == "protection":
        if existing_position is None:
            return {"status": "rejected", "reason_codes": ["NO_OPEN_POSITION"], "intent_type": intent_type}
        protection_recovery_result = _ensure_protected_position(
            session,
            settings_row,
            client,
            symbol=decision.symbol,
            position=existing_position,
            stop_loss=intent.stop_loss or existing_position.stop_loss,
            take_profit=intent.take_profit or existing_position.take_profit,
            decision_run_id=decision_run_id,
            risk_row=risk_row,
            parent_order=None,
            trigger_source="execute_live_trade:protection",
            pause_reason_code="MISSING_PROTECTIVE_ORDERS",
        )
        record_audit_event(
            session,
            event_type="protection_recovery_processed",
            entity_type="position",
            entity_id=str(existing_position.id),
            severity="info" if protection_recovery_result["status"] != "emergency_exit" else "critical",
            message="Protection recovery intent processed.",
            payload={
                "symbol": decision.symbol,
                "intent_type": intent_type,
                "decision": decision.model_dump(mode="json"),
                "protection_result": protection_recovery_result,
            },
        )
        return {
            "status": protection_recovery_result["status"],
            "intent_type": intent_type,
            "protective_state": protection_recovery_result["protection_state"],
            "protective_order_ids": protection_recovery_result.get("created_order_ids", []),
            "emergency_action": protection_recovery_result.get("emergency_action"),
        }

    if intent_type == "entry" and existing_position is None:
        _cancel_exit_orders(session, client, decision.symbol)
    client.change_initial_leverage(decision.symbol, max(1, int(round(intent.leverage))))

    side = "BUY" if decision.decision == "long" else "SELL"
    requested_quantity = intent.quantity
    reduce_only = False

    if decision.decision in {"reduce", "exit"}:
        if existing_position is None:
            return {"status": "rejected", "reason_codes": ["NO_OPEN_POSITION"], "intent_type": intent_type}
        side = "SELL" if existing_position.side == "long" else "BUY"
        requested_quantity = existing_position.quantity if decision.decision == "exit" else existing_position.quantity * 0.5
        reduce_only = True

    normalized_quantity = client.normalize_order_quantity(
        decision.symbol,
        requested_quantity,
        reference_price=intent.requested_price,
        enforce_min_notional=intent_type in {"entry", "scale_in"},
    )
    try:
        client.change_initial_leverage(decision.symbol, max(1, int(round(intent.leverage))))
        client_order_id, exchange_order = _safe_submit_order(
            client,
            symbol=decision.symbol,
            side=side,
            order_type="MARKET",
            quantity=normalized_quantity,
            reduce_only=reduce_only,
            response_type="RESULT",
        )
    except BinanceAPIError as exc:
        reason_codes = ["BINANCE_ORDER_REJECTED"]
        if exc.code == -2019:
            reason_codes.append("INSUFFICIENT_MARGIN")
        order = _create_rejected_order_row(
            session,
            symbol=decision.symbol,
            side=decision.decision,
            order_type="MARKET",
            requested_quantity=normalized_quantity,
            requested_price=intent.requested_price,
            decision_run_id=decision_run_id,
            risk_row=risk_row,
            reduce_only=reduce_only,
            close_only=decision.decision == "exit",
            reason_codes=reason_codes,
            metadata_json={
                "error": str(exc),
                "exchange_code": exc.code,
                "available_balance": live_balances["available_balance"],
                "equity": live_balances["equity"],
                "intent_type": intent_type,
            },
        )
        create_alert(
            session,
            category="execution",
            severity="warning",
            title="Live order rejected",
            message="???? ???? ??????.",
            payload={
                "symbol": decision.symbol,
                "error": str(exc),
                "exchange_code": exc.code,
                "requested_quantity": normalized_quantity,
                "available_balance": live_balances["available_balance"],
                "intent_type": intent_type,
            },
        )
        record_audit_event(
            session,
            event_type="live_execution_rejected",
            entity_type="order",
            entity_id=str(order.id),
            severity="warning",
            message="Live execution was rejected by Binance.",
            payload={
                "symbol": decision.symbol,
                "error": str(exc),
                "exchange_code": exc.code,
                "requested_quantity": normalized_quantity,
                "available_balance": live_balances["available_balance"],
                "equity": live_balances["equity"],
                "intent_type": intent_type,
            },
        )
        session.flush()
        return {
            "order_id": order.id,
            "status": "rejected",
            "reason_codes": reason_codes,
            "error": str(exc),
            "exchange_code": exc.code,
            "intent_type": intent_type,
        }
    except Exception as exc:
        order = _create_rejected_order_row(
            session,
            symbol=decision.symbol,
            side=decision.decision,
            order_type="MARKET",
            requested_quantity=normalized_quantity,
            requested_price=intent.requested_price,
            decision_run_id=decision_run_id,
            risk_row=risk_row,
            reduce_only=reduce_only,
            close_only=decision.decision == "exit",
            reason_codes=["LIVE_EXECUTION_ERROR"],
            metadata_json={"error": str(exc), "intent_type": intent_type},
        )
        create_alert(
            session,
            category="execution",
            severity="error",
            title="Live execution failed",
            message="??? ?? ? ??? ??????.",
            payload={"symbol": decision.symbol, "error": str(exc), "intent_type": intent_type},
        )
        record_audit_event(
            session,
            event_type="live_execution_error",
            entity_type="order",
            entity_id=str(order.id),
            severity="error",
            message="Live execution failed before exchange acceptance.",
            payload={"symbol": decision.symbol, "error": str(exc), "intent_type": intent_type},
        )
        record_health_event(
            session,
            component="live_execution",
            status="error",
            message="Unexpected live execution error.",
            payload={"symbol": decision.symbol, "error": str(exc), "intent_type": intent_type},
        )
        session.flush()
        return {"order_id": order.id, "status": "error", "reason_codes": ["LIVE_EXECUTION_ERROR"], "error": str(exc), "intent_type": intent_type}

    order = _upsert_exchange_order_row(
        session,
        symbol=decision.symbol,
        requested_price=intent.requested_price,
        requested_quantity=normalized_quantity,
        order_type="MARKET",
        side=decision.decision,
        exchange_order={**exchange_order, "clientOrderId": client_order_id},
        decision_run_id=decision_run_id,
        risk_row=risk_row,
        reduce_only=reduce_only,
        close_only=decision.decision == "exit",
    )
    try:
        trades = client.get_account_trades(symbol=decision.symbol, order_id=order.external_order_id)
    except Exception as exc:
        reason_code = _classify_exchange_state_error(exc, "TEMPORARY_SYNC_FAILURE")
        _pause_for_system_issue(
            session,
            settings_row,
            reason_code=reason_code,
            symbol=decision.symbol,
            error=str(exc),
            event_type="live_post_trade_sync_failed",
            component="live_execution",
            alert_title="Post-trade sync failed",
            alert_message="?? ?? ?? ???? ??? ?? ??? ?? ??????.",
        )
        return {"order_id": order.id, "status": order.status, "reason_codes": [reason_code], "error": str(exc), "intent_type": intent_type}
    fee_paid, realized_pnl = _record_live_trades(session, order, trades)

    try:
        post_trade_open_orders = client.get_open_orders(decision.symbol)
    except Exception as exc:
        reason_code = _classify_exchange_state_error(exc, "EXCHANGE_OPEN_ORDERS_SYNC_FAILED")
        _pause_for_system_issue(
            session,
            settings_row,
            reason_code=reason_code,
            symbol=decision.symbol,
            error=str(exc),
            event_type="live_post_order_open_orders_failed",
            component="live_execution",
            alert_title="Post-order open orders sync failed",
            alert_message="?? ?? ??? ?? ??? ???? ?? ??? ?? ??????.",
        )
        return {"order_id": order.id, "status": order.status, "reason_codes": [reason_code], "error": str(exc), "intent_type": intent_type}

    try:
        synced_position = sync_live_positions(
            session,
            settings_row,
            symbol=decision.symbol,
            client=client,
            open_orders=post_trade_open_orders,
        )
    except Exception as exc:
        reason_code = _classify_exchange_state_error(exc, "EXCHANGE_POSITION_SYNC_FAILED")
        _pause_for_system_issue(
            session,
            settings_row,
            reason_code=reason_code,
            symbol=decision.symbol,
            error=str(exc),
            event_type="live_post_order_position_sync_failed",
            component="live_execution",
            alert_title="Post-order position sync failed",
            alert_message="?? ?? ??? ??? ???? ?? ??? ?? ??????.",
        )
        return {"order_id": order.id, "status": order.status, "reason_codes": [reason_code], "error": str(exc), "intent_type": intent_type}

    position = get_open_position(session, decision.symbol)
    if position is not None:
        order.position_id = position.id
        session.add(order)
        session.flush()

    protection_result: dict[str, object] | None = None
    protective_order_ids: list[int] = []
    if position is not None and position.quantity > 0:
        protection_result = _ensure_protected_position(
            session,
            settings_row,
            client,
            symbol=decision.symbol,
            position=position,
            stop_loss=intent.stop_loss or position.stop_loss,
            take_profit=intent.take_profit or position.take_profit,
            decision_run_id=decision_run_id,
            risk_row=risk_row,
            parent_order=order,
            trigger_source=f"execute_live_trade:{intent_type}",
            pause_reason_code="PROTECTIVE_ORDER_FAILURE" if intent_type in {"entry", "scale_in"} else "MISSING_PROTECTIVE_ORDERS",
        )
        created_order_ids = protection_result.get("created_order_ids")
        if isinstance(created_order_ids, list):
            protective_order_ids = [int(item) for item in created_order_ids]
        else:
            protective_order_ids = []
        if protection_result["status"] == "emergency_exit":
            return {
                "order_id": order.id,
                "position_id": order.position_id,
                "status": "emergency_exit",
                "exchange_status": order.exchange_status,
                "fill_price": order.average_fill_price,
                "fill_quantity": order.filled_quantity,
                "realized_pnl": realized_pnl,
                "fees": fee_paid,
                "protective_order_ids": protective_order_ids,
                "protective_state": protection_result["protection_state"],
                "emergency_action": protection_result["emergency_action"],
                "intent_type": intent_type,
            }
    else:
        _cancel_exit_orders(session, client, decision.symbol)

    slippage_pct = abs(order.average_fill_price - intent.requested_price) / max(intent.requested_price, 1.0)
    if slippage_pct > settings_row.slippage_threshold_pct:
        create_alert(
            session,
            category="execution",
            severity="warning",
            title="Slippage threshold exceeded",
            message="??? ????? ???? ??????.",
            payload={"order_id": order.id, "slippage_pct": slippage_pct, "intent_type": intent_type},
        )

    latest_price = position.mark_price if position is not None else market_snapshot.latest_price
    refresh_open_position_marks(session, {decision.symbol: latest_price})
    try:
        refreshed_account_info = client.get_account_info()
    except Exception as exc:
        reason_code = _classify_exchange_state_error(exc, "EXCHANGE_ACCOUNT_STATE_UNAVAILABLE")
        _pause_for_system_issue(
            session,
            settings_row,
            reason_code=reason_code,
            symbol=decision.symbol,
            error=str(exc),
            event_type="live_post_order_account_sync_failed",
            component="live_execution",
            alert_title="Post-order account sync failed",
            alert_message="?? ?? ?? ??? ?? ???? ?? ??? ?? ??????.",
        )
        return {"order_id": order.id, "status": order.status, "reason_codes": [reason_code], "error": str(exc), "intent_type": intent_type}
    pnl_snapshot = create_exchange_pnl_snapshot(session, settings_row, refreshed_account_info)
    record_audit_event(
        session,
        event_type="live_execution",
        entity_type="order",
        entity_id=str(order.id),
        severity="info",
        message="??? ??? Binance? ???????.",
        payload={
            "position": synced_position,
            "protective_order_ids": protective_order_ids,
            "protective_state": protection_result["protection_state"] if protection_result is not None else pre_trade_protection,
            "pre_trade_protection": pre_trade_protection,
            "slippage_pct": slippage_pct,
            "intent_type": intent_type,
        },
    )
    return {
        "order_id": order.id,
        "position_id": order.position_id,
        "status": order.status,
        "exchange_status": order.exchange_status,
        "fill_price": order.average_fill_price,
        "fill_quantity": order.filled_quantity,
        "realized_pnl": realized_pnl,
        "fees": fee_paid,
        "equity": pnl_snapshot.equity,
        "protective_order_ids": protective_order_ids,
        "protective_state": protection_result["protection_state"] if protection_result is not None else pre_trade_protection,
        "intent_type": intent_type,
    }
