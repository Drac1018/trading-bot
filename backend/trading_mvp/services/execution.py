from __future__ import annotations

import time
from datetime import timedelta
from math import floor
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_mvp.config import get_settings
from trading_mvp.models import Execution, Order, Position, RiskCheck, Setting
from trading_mvp.schemas import (
    ExecutionIntent,
    FeaturePayload,
    MarketCandle,
    MarketSnapshotPayload,
    RiskCheckResult,
    TradeDecision,
)
from trading_mvp.services.account import (
    create_exchange_pnl_snapshot,
    get_open_position,
    refresh_open_position_marks,
)
from trading_mvp.services.audit import (
    create_alert,
    record_audit_event,
    record_health_event,
    record_position_management_event,
)
from trading_mvp.services.binance import BinanceAPIError, BinanceClient
from trading_mvp.services.execution_policy import (
    ExecutionPlan,
    select_execution_plan,
    should_fallback_aggressively,
)
from trading_mvp.services.pause_control import (
    clear_symbol_protection_state,
    mark_manage_only_state,
    set_symbol_protection_state,
)
from trading_mvp.services.position_management import (
    PARTIAL_TAKE_PROFIT_FRACTION,
    build_position_management_context,
    mark_partial_take_profit_taken,
    mark_time_stop_action,
    seed_position_management_metadata,
    store_position_management_context,
)
from trading_mvp.services.risk import evaluate_risk, is_survival_path_decision
from trading_mvp.services.runtime_state import (
    DEGRADED_MANAGE_ONLY_STATE,
    EMERGENCY_EXIT_STATE,
    PROTECTION_RECOVERY_THRESHOLD,
    PROTECTION_REQUIRED_STATE,
    TRADABLE_STATE,
    build_sync_freshness_summary,
    get_operating_state,
    get_protection_recovery_detail,
    mark_sync_issue,
    mark_sync_success,
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


def _to_bool(value: object, default: bool = False) -> bool:
    if value in {None, ""}:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _exchange_order_requested_price(exchange_order: dict[str, object], fallback: float) -> float:
    price = _to_float(exchange_order.get("price"))
    if price > 0:
        return price
    stop_price = _to_float(exchange_order.get("stopPrice"))
    if stop_price > 0:
        return stop_price
    return fallback


def _apply_exchange_order_state(
    row: Order,
    exchange_order: dict[str, object],
    *,
    requested_quantity_fallback: float,
    requested_price_fallback: float,
    reduce_only_fallback: bool,
    close_only_fallback: bool,
) -> None:
    row.status = _map_exchange_status(str(exchange_order.get("status", "NEW")))
    row.exchange_status = str(exchange_order.get("status", "")) or None
    row.last_exchange_update_at = utcnow_naive()
    row.filled_quantity = abs(_to_float(exchange_order.get("executedQty"), row.filled_quantity))
    requested_quantity = abs(_to_float(exchange_order.get("origQty"), requested_quantity_fallback))
    if requested_quantity > 0:
        row.requested_quantity = requested_quantity
    requested_price = _exchange_order_requested_price(exchange_order, requested_price_fallback)
    if requested_price > 0:
        row.requested_price = requested_price
    avg_price = _to_float(exchange_order.get("avgPrice") or exchange_order.get("price"), row.average_fill_price)
    if avg_price > 0:
        row.average_fill_price = avg_price
    row.reduce_only = _to_bool(exchange_order.get("reduceOnly"), default=reduce_only_fallback)
    row.close_only = _to_bool(exchange_order.get("closePosition"), default=close_only_fallback)


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


def _quantity_for_notional(notional: float, price: float) -> float:
    if notional <= 0 or price <= 0:
        return 0.0
    return round(notional / price, 6)


def _cap_quantity_to_approved_notional(
    client: Any,
    *,
    symbol: str,
    quantity: float,
    reference_price: float,
    approved_notional: float,
) -> float:
    if approved_notional <= 0 or reference_price <= 0 or quantity <= 0:
        return quantity
    max_quantity = approved_notional / reference_price
    if quantity <= max_quantity + 1e-9:
        return quantity
    if not hasattr(client, "get_symbol_filters"):
        return round(max(max_quantity, 0.0), 6)
    filters = client.get_symbol_filters(symbol)
    step_size = _to_float(filters.get("step_size"))
    min_qty = _to_float(filters.get("min_qty"))
    capped = max_quantity
    if step_size > 0:
        capped = floor(max_quantity / step_size) * step_size
    if capped < min_qty:
        return 0.0
    return round(max(capped, 0.0), 6)


def _execution_minimum_notional_failure(
    client: Any,
    *,
    symbol: str,
    quantity: float,
    reference_price: float,
) -> str | None:
    if quantity <= 0 or reference_price <= 0 or not hasattr(client, "get_symbol_filters"):
        return "APPROVED_SIZE_BELOW_EXECUTION_MINIMUM" if quantity <= 0 else None
    filters = client.get_symbol_filters(symbol)
    min_qty = _to_float(filters.get("min_qty"))
    min_notional = _to_float(filters.get("min_notional"))
    if min_qty > 0 and quantity < min_qty:
        return "APPROVED_SIZE_BELOW_EXECUTION_MINIMUM"
    if min_notional > 0 and quantity * reference_price < min_notional:
        return "APPROVED_SIZE_BELOW_EXECUTION_MINIMUM"
    return None


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


def _reduce_fraction_for_decision(decision: TradeDecision, settings_row: Setting) -> float:
    rationale_codes = set(decision.rationale_codes)
    if "POSITION_MANAGEMENT_PARTIAL_TAKE_PROFIT" in rationale_codes:
        configured = _to_float(getattr(settings_row, "partial_tp_size_pct", PARTIAL_TAKE_PROFIT_FRACTION))
        return min(max(configured, 0.01), 1.0)
    if "POSITION_MANAGEMENT_TIME_STOP_REDUCE" in rationale_codes:
        return 0.5
    if {"POSITION_MANAGEMENT_EDGE_DECAY", "POSITION_MANAGEMENT_REGIME_SHIFT", "POSITION_MANAGEMENT_MOMENTUM_WEAKENING"} & rationale_codes:
        return 0.35
    return 0.5


def _is_effectively_zero(value: float | None) -> bool:
    return value is None or abs(value) <= 1e-9


def _build_market_snapshot_from_position(position: Position, feature_payload: FeaturePayload) -> MarketSnapshotPayload:
    snapshot_time = utcnow_naive()
    latest_price = position.mark_price if position.mark_price > 0 else position.entry_price
    return MarketSnapshotPayload(
        symbol=position.symbol,
        timeframe=feature_payload.timeframe,
        snapshot_time=snapshot_time,
        latest_price=latest_price,
        latest_volume=max(feature_payload.volume_ratio, 0.0),
        candle_count=1,
        is_stale=False,
        is_complete=True,
        candles=[
            MarketCandle(
                timestamp=snapshot_time,
                open=latest_price,
                high=latest_price,
                low=latest_price,
                close=latest_price,
                volume=max(feature_payload.volume_ratio, 0.0),
            )
        ],
    )


def _build_position_management_trade_decision(
    position: Position,
    *,
    feature_payload: FeaturePayload,
    context: dict[str, object],
    settings_row: Setting,
) -> TradeDecision | None:
    reason_codes = [str(item) for item in _get_string_list(context, "reduce_reason_codes")]
    if not reason_codes:
        return None

    if "POSITION_MANAGEMENT_TIME_STOP_EXIT" in reason_codes:
        decision_type = "exit"
        explanation_short = "time stop exit"
        explanation_detailed = "Time stop triggered a deterministic exit because the trade failed to show acceptable progress."
    else:
        decision_type = "reduce"
        if "POSITION_MANAGEMENT_PARTIAL_TAKE_PROFIT" in reason_codes:
            explanation_short = "partial take profit"
            explanation_detailed = (
                "Partial take profit triggered after the configured R threshold and keeps the position in reduce-only mode."
            )
        elif "POSITION_MANAGEMENT_TIME_STOP_REDUCE" in reason_codes:
            explanation_short = "time stop reduce"
            explanation_detailed = "Time stop triggered a deterministic size reduction because edge decayed before target follow-through."
        else:
            explanation_short = "position management reduce"
            explanation_detailed = "Deterministic position management requested a reduce-only adjustment."

    return TradeDecision(
        decision=decision_type,  # type: ignore[arg-type]
        confidence=0.9,
        symbol=position.symbol,
        timeframe=feature_payload.timeframe,
        entry_zone_min=position.mark_price if position.mark_price > 0 else position.entry_price,
        entry_zone_max=position.mark_price if position.mark_price > 0 else position.entry_price,
        stop_loss=position.stop_loss,
        take_profit=position.take_profit,
        max_holding_minutes=max(getattr(settings_row, "time_stop_minutes", 120), 1),
        risk_pct=min(settings_row.max_risk_per_trade, 0.01),
        leverage=max(min(position.leverage, settings_row.max_leverage), 1.0),
        rationale_codes=reason_codes,
        explanation_short=explanation_short,
        explanation_detailed=explanation_detailed,
    )


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


def _record_sync_success(
    session: Session,
    settings_row: Setting,
    *,
    scope: str,
    detail: dict[str, object] | None = None,
    status: str = "synced",
) -> None:
    mark_sync_success(settings_row, scope=scope, detail=detail, status=status)
    session.add(settings_row)
    session.flush()


def _record_sync_issue(
    session: Session,
    settings_row: Setting,
    *,
    scope: str,
    status: str,
    reason_code: str,
    detail: dict[str, object] | None = None,
) -> None:
    mark_sync_issue(settings_row, scope=scope, status=status, reason_code=reason_code, detail=detail)
    session.add(settings_row)
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


def _is_more_protective_stop(side: str, current_stop: float | None, candidate_stop: float | None) -> bool:
    if candidate_stop is None:
        return False
    if current_stop is None:
        return True
    if side == "long":
        return candidate_stop > current_stop + 1e-9
    return candidate_stop < current_stop - 1e-9


def _replace_stop_loss_order(
    session: Session,
    *,
    client: BinanceClient,
    position: Position,
    symbol: str,
    stop_loss: float,
    decision_run_id: int | None,
    risk_row: RiskCheck | None,
    trigger_source: str,
    open_orders: list[dict[str, object]],
) -> dict[str, object]:
    exit_side = "SELL" if position.side == "long" else "BUY"
    cancelled_order_ids: list[str] = []
    for item in open_orders:
        if _protective_bucket(item) != "stop_loss":
            continue
        remote_order_id = str(item.get("orderId", ""))
        remote_client_order_id = str(item.get("clientOrderId", ""))
        _cancel_exchange_order(
            client,
            symbol=symbol,
            order_payload=item,
            order_id=remote_order_id or None,
            client_order_id=remote_client_order_id or None,
        )
        if remote_order_id:
            cancelled_order_ids.append(remote_order_id)
        local_order = None
        if remote_order_id:
            local_order = session.scalar(select(Order).where(Order.external_order_id == remote_order_id).limit(1))
        if local_order is None and remote_client_order_id:
            local_order = session.scalar(select(Order).where(Order.client_order_id == remote_client_order_id).limit(1))
        if local_order is not None:
            local_order.status = "canceled"
            local_order.exchange_status = "CANCELED"
            local_order.last_exchange_update_at = utcnow_naive()
            session.add(local_order)

    normalized_stop = client.normalize_price(symbol, stop_loss) if hasattr(client, "normalize_price") else stop_loss
    client_order_id, exchange_order = _safe_submit_order(
        client,
        symbol=symbol,
        side=exit_side,
        order_type="STOP_MARKET",
        stop_price=normalized_stop,
        close_position=True,
        response_type="ACK",
    )
    order = _upsert_exchange_order_row(
        session,
        symbol=symbol,
        requested_price=normalized_stop,
        requested_quantity=position.quantity,
        order_type="STOP_MARKET",
        side=exit_side.lower(),
        exchange_order={**exchange_order, "clientOrderId": client_order_id},
        decision_run_id=decision_run_id,
        risk_row=risk_row,
        reduce_only=True,
        close_only=True,
        parent_order_id=None,
    )
    order.position_id = position.id
    order.metadata_json = {
        **(order.metadata_json or {}),
        "position_management": {
            "trigger_source": trigger_source,
            "applied_rule": "STOP_TIGHTENED",
            "tightened_stop_loss": normalized_stop,
        },
    }
    position.stop_loss = normalized_stop
    session.add(position)
    session.add(order)
    session.flush()
    record_position_management_event(
        session,
        event_type="position_management_stop_tightened",
        position_id=position.id,
        severity="info",
        message="Position management tightened the live stop loss.",
        payload={
            "symbol": symbol,
            "trigger_source": trigger_source,
            "cancelled_order_ids": cancelled_order_ids,
            "new_order_id": order.id,
            "tightened_stop_loss": normalized_stop,
        },
    )
    return {
        "status": "applied",
        "tightened_stop_loss": normalized_stop,
        "cancelled_order_ids": cancelled_order_ids,
        "order_id": order.id,
    }


def apply_position_management(
    session: Session,
    settings_row: Setting,
    *,
    symbol: str,
    feature_payload: FeaturePayload,
    decision_run_id: int | None = None,
    risk_row: RiskCheck | None = None,
    client: BinanceClient | None = None,
) -> dict[str, object]:
    position = get_open_position(session, symbol)
    context = build_position_management_context(position, feature_payload=feature_payload, settings_row=settings_row)
    if position is None:
        return {"status": "no_open_position", "position_management_context": context}

    store_position_management_context(position, context)
    session.add(position)
    session.flush()

    if not context.get("enabled"):
        return {"status": "disabled", "position_management_context": context}

    client = client or _build_client(settings_row)
    open_orders = client.get_open_orders(symbol)
    protection_state = _build_protection_state(position, open_orders)
    tightened_stop_loss = _to_float(context.get("tightened_stop_loss"))
    stop_can_tighten = (
        protection_state["status"] == "protected"
        and bool(protection_state.get("has_stop_loss"))
        and not _is_effectively_zero(tightened_stop_loss)
        and _is_more_protective_stop(position.side, position.stop_loss, tightened_stop_loss)
    )
    if stop_can_tighten:
        applied = _replace_stop_loss_order(
            session,
            client=client,
            position=position,
            symbol=symbol,
            stop_loss=float(tightened_stop_loss),
            decision_run_id=decision_run_id,
            risk_row=risk_row,
            trigger_source="position_management",
            open_orders=open_orders,
        )
        break_even_stop = _to_float(context.get("break_even_stop_loss"))
        if (
            "POSITION_MANAGEMENT_BREAK_EVEN" in set(_get_string_list(context, "applied_rule_candidates"))
            and break_even_stop is not None
            and abs(float(applied["tightened_stop_loss"]) - break_even_stop) <= 1e-9
        ):
            record_position_management_event(
                session,
                event_type="moved_stop_to_breakeven",
                position_id=position.id,
                severity="info",
                message="Position management moved the live stop to break-even.",
                payload={
                    "symbol": symbol,
                    "decision_run_id": decision_run_id,
                    "tightened_stop_loss": applied["tightened_stop_loss"],
                    "break_even_trigger_r": context.get("break_even_trigger_r"),
                },
            )
        refreshed_open_orders = client.get_open_orders(symbol)
        protection_result = _ensure_protected_position(
            session,
            settings_row,
            client,
            symbol=symbol,
            position=position,
            stop_loss=position.stop_loss,
            take_profit=position.take_profit,
            decision_run_id=decision_run_id,
            risk_row=risk_row,
            parent_order=None,
            trigger_source="position_management",
            pause_reason_code="MISSING_PROTECTIVE_ORDERS",
        )
        return {
            "status": "applied",
            "position_management_context": context,
            "position_management_action": applied,
            "protection_state": _build_protection_state(position, refreshed_open_orders),
            "protection_result": protection_result,
        }

    management_decision = _build_position_management_trade_decision(
        position,
        feature_payload=feature_payload,
        context=context,
        settings_row=settings_row,
    )
    if management_decision is None:
        return {
            "status": "monitoring",
            "position_management_context": context,
            "protection_state": protection_state,
        }
    if protection_state["status"] != "protected":
        return {
            "status": "monitoring",
            "position_management_context": context,
            "protection_state": protection_state,
            "position_management_action": {
                "status": "skipped_unverified_protection",
                "decision": management_decision.model_dump(mode="json"),
            },
        }

    market_snapshot = _build_market_snapshot_from_position(position, feature_payload)
    management_risk_result, management_risk_row = evaluate_risk(
        session,
        settings_row,
        management_decision,
        market_snapshot,
        decision_run_id=decision_run_id,
        execution_mode="live",
    )
    if not management_risk_result.allowed or not is_survival_path_decision(management_decision):
        return {
            "status": "blocked",
            "position_management_context": context,
            "protection_state": protection_state,
            "position_management_action": {
                "status": "risk_blocked",
                "decision": management_decision.model_dump(mode="json"),
                "risk_result": management_risk_result.model_dump(mode="json"),
            },
        }

    execution_result = execute_live_trade(
        session,
        settings_row,
        decision_run_id=decision_run_id,
        decision=management_decision,
        market_snapshot=market_snapshot,
        risk_result=management_risk_result,
        risk_row=management_risk_row,
    )

    fill_quantity = _to_float(execution_result.get("fill_quantity"))
    if fill_quantity > 0 and "POSITION_MANAGEMENT_PARTIAL_TAKE_PROFIT" in set(management_decision.rationale_codes):
        record_position_management_event(
            session,
            event_type="partial_tp_executed",
            position_id=position.id,
            severity="info",
            message="Position management executed a partial take profit in reduce-only mode.",
            payload={
                "symbol": symbol,
                "order_id": execution_result.get("order_id"),
                "fill_quantity": fill_quantity,
                "reduce_fraction": execution_result.get("position_management", {}).get("reduce_fraction"),
            },
        )
    if fill_quantity > 0 and "POSITION_MANAGEMENT_TIME_STOP_EXIT" in set(management_decision.rationale_codes):
        record_position_management_event(
            session,
            event_type="time_stop_exit",
            position_id=position.id,
            severity="info",
            message="Position management exited the position because time stop conditions were met.",
            payload={
                "symbol": symbol,
                "order_id": execution_result.get("order_id"),
                "time_stop_minutes": context.get("time_stop_minutes"),
                "time_stop_profit_floor": context.get("time_stop_profit_floor"),
            },
        )
        mark_time_stop_action(position, action="exit")
        session.add(position)
        session.flush()
    elif fill_quantity > 0 and "POSITION_MANAGEMENT_TIME_STOP_REDUCE" in set(management_decision.rationale_codes):
        record_position_management_event(
            session,
            event_type="time_stop_reduce",
            position_id=position.id,
            severity="info",
            message="Position management reduced the position because time stop conditions were met.",
            payload={
                "symbol": symbol,
                "order_id": execution_result.get("order_id"),
                "time_stop_minutes": context.get("time_stop_minutes"),
                "time_stop_profit_floor": context.get("time_stop_profit_floor"),
            },
        )
        mark_time_stop_action(position, action="reduce")
        session.add(position)
        session.flush()

    return {
        "status": "executed",
        "position_management_context": context,
        "position_management_action": execution_result,
        "protection_state": protection_state,
        "risk_result": management_risk_result.model_dump(mode="json"),
        "decision": management_decision.model_dump(mode="json"),
    }


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
    if intent_type == "reduce_only" and existing_position is not None:
        entry_price = existing_position.mark_price if existing_position.mark_price > 0 else market_snapshot.latest_price
    if intent_type == "protection" and existing_position is not None:
        quantity = max(existing_position.quantity, 0.0001)
        entry_price = existing_position.mark_price if existing_position.mark_price > 0 else existing_position.entry_price
        leverage = existing_position.leverage if existing_position.leverage > 0 else min(risk_result.approved_leverage, settings_row.max_leverage)
    else:
        approved_quantity = risk_result.approved_quantity if risk_result.approved_quantity is not None else 0.0
        if approved_quantity > 0:
            quantity = approved_quantity
        else:
            quantity = _calculate_quantity(
                entry_price=entry_price,
                stop_loss=decision.stop_loss,
                equity=equity,
                risk_pct=risk_result.approved_risk_pct,
                leverage=risk_result.approved_leverage,
            )
            if risk_result.approved_projected_notional > 0:
                quantity = min(
                    quantity,
                    _quantity_for_notional(risk_result.approved_projected_notional, entry_price),
                )
        leverage = min(risk_result.approved_leverage, settings_row.max_leverage)
    return ExecutionIntent(
        symbol=decision.symbol,
        action=decision.decision,  # type: ignore[arg-type]
        intent_type=intent_type,  # type: ignore[arg-type]
        quantity=max(quantity, 0.0001),
        requested_price=entry_price,
        entry_mode=decision.entry_mode,
        invalidation_price=decision.invalidation_price,
        max_chase_bps=decision.max_chase_bps,
        idea_ttl_minutes=decision.idea_ttl_minutes,
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
            payload={
                "trade": trade,
                "requested_price": order.requested_price,
                "requested_quantity": order.requested_quantity,
                "order_type": order.order_type,
                "execution_policy": (order.metadata_json or {}).get("execution_policy"),
            },
        )
        session.add(execution)
        fee_total += fee_paid
        realized_total += realized_pnl
    session.flush()
    return fee_total, realized_total


def _sum_trade_quantity(trades: list[dict[str, object]]) -> float:
    return sum(abs(_to_float(trade.get("qty"))) for trade in trades)


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
    _apply_exchange_order_state(
        row,
        exchange_order,
        requested_quantity_fallback=requested_quantity,
        requested_price_fallback=requested_price,
        reduce_only_fallback=reduce_only,
        close_only_fallback=close_only,
    )
    existing_metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    row.metadata_json = {
        **existing_metadata,
        "exchange_order": exchange_order,
    }
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
    price: float | None = None,
    stop_price: float | None = None,
    reduce_only: bool = False,
    close_position: bool = False,
    response_type: str = "RESULT",
    time_in_force: str | None = None,
) -> tuple[str, dict[str, object]]:
    client_order_id = f"mvp-{uuid4().hex[:24]}"
    try:
        if price is None and time_in_force is None:
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
        elif time_in_force is None:
            response = client.new_order(
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                stop_price=stop_price,
                reduce_only=reduce_only,
                close_position=close_position,
                client_order_id=client_order_id,
                response_type=response_type,
            )
        else:
            response = client.new_order(
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                stop_price=stop_price,
                reduce_only=reduce_only,
                close_position=close_position,
                client_order_id=client_order_id,
                response_type=response_type,
                time_in_force=time_in_force,
            )
    except (httpx.TimeoutException, httpx.TransportError):
        response = _fetch_exchange_order(
            client,
            symbol=symbol,
            order_type=order_type,
            client_order_id=client_order_id,
        )
    return client_order_id, response


def _execution_policy_sleep(seconds: int) -> None:
    if seconds > 0:
        time.sleep(seconds)


def _resolve_live_reference_price(
    client: BinanceClient,
    *,
    symbol: str,
    fallback_price: float,
) -> float:
    try:
        if hasattr(client, "get_symbol_price"):
            return max(float(client.get_symbol_price(symbol)), 0.0) or fallback_price
    except Exception:
        return fallback_price
    return fallback_price


def _compute_limit_reprice(
    client: BinanceClient,
    *,
    symbol: str,
    side: str,
    current_price: float,
    live_reference_price: float,
    reprice_bps: float,
) -> float:
    adjustment = max(reprice_bps, 0.0) / 10000.0
    if side.upper() == "BUY":
        candidate = min(live_reference_price, current_price * (1.0 + adjustment))
    else:
        candidate = max(live_reference_price, current_price * (1.0 - adjustment))
    if hasattr(client, "normalize_price"):
        return client.normalize_price(symbol, candidate)
    return candidate


def _normalize_remaining_quantity(
    client: BinanceClient,
    *,
    symbol: str,
    remaining_quantity: float,
    reference_price: float,
) -> float:
    if remaining_quantity <= 0:
        return 0.0
    normalized = client.normalize_order_quantity(
        symbol,
        remaining_quantity,
        reference_price=reference_price,
        enforce_min_notional=False,
    )
    if normalized > remaining_quantity:
        return 0.0
    return normalized


def _remaining_fill_ratio(*, requested_quantity: float, filled_quantity: float) -> float:
    if requested_quantity <= 0:
        return 0.0
    remaining = max(requested_quantity - filled_quantity, 0.0)
    return remaining / requested_quantity


def _classify_execution_quality(
    *,
    requested_quantity: float,
    filled_quantity: float,
    execution_attempts: list[dict[str, object]],
    aggressive_fallback_used: bool,
    slippage_pct: float,
    slippage_threshold_pct: float,
) -> tuple[str, str]:
    fill_ratio = 0.0 if requested_quantity <= 0 else min(filled_quantity / requested_quantity, 1.0)
    partial_fill_attempts = sum(
        1
        for attempt in execution_attempts
        if float(attempt.get("filled_quantity") or 0.0) > 0
        and float(attempt.get("filled_quantity") or 0.0) + 1e-9 < float(attempt.get("requested_quantity") or 0.0)
    )
    repriced_attempts = max(len(execution_attempts) - 1, 0)
    timed_out_attempts = sum(1 for attempt in execution_attempts if bool(attempt.get("timed_out")))

    if fill_ratio < 0.999:
        return "incomplete_fill", "signal_outcome_pending"
    if aggressive_fallback_used:
        return "aggressive_completion", "signal_outcome_pending"
    if timed_out_attempts > 0 or repriced_attempts > 0:
        return "repriced_completion", "signal_outcome_pending"
    if partial_fill_attempts > 0:
        return "partial_fill_recovered", "signal_outcome_pending"
    if slippage_pct > slippage_threshold_pct:
        return "high_slippage", "signal_outcome_pending"
    return "clean_fill", "signal_outcome_pending"


def _build_execution_quality_summary(
    *,
    plan: ExecutionPlan,
    requested_quantity: float,
    requested_price: float,
    filled_quantity: float,
    average_fill_price: float,
    fee_paid: float,
    realized_pnl: float,
    execution_attempts: list[dict[str, object]],
    slippage_threshold_pct: float,
    aggressive_fallback_used: bool,
) -> dict[str, object]:
    slippage_pct = 0.0
    if filled_quantity > 0 and average_fill_price > 0:
        slippage_pct = abs(average_fill_price - requested_price) / max(requested_price, 1.0)
    fill_ratio = 0.0 if requested_quantity <= 0 else min(filled_quantity / requested_quantity, 1.0)
    timed_out_attempts = sum(1 for attempt in execution_attempts if bool(attempt.get("timed_out")))
    partial_fill_attempts = sum(
        1
        for attempt in execution_attempts
        if float(attempt.get("filled_quantity") or 0.0) > 0
        and float(attempt.get("filled_quantity") or 0.0) + 1e-9 < float(attempt.get("requested_quantity") or 0.0)
    )
    execution_quality_status, decision_quality_status = _classify_execution_quality(
        requested_quantity=requested_quantity,
        filled_quantity=filled_quantity,
        execution_attempts=execution_attempts,
        aggressive_fallback_used=aggressive_fallback_used,
        slippage_pct=slippage_pct,
        slippage_threshold_pct=slippage_threshold_pct,
    )
    return {
        "policy_profile": plan.policy_profile,
        "symbol_risk_tier": plan.symbol_risk_tier,
        "timeframe_bucket": plan.timeframe_bucket,
        "volatility_regime": plan.volatility_regime,
        "urgency": plan.urgency,
        "requested_quantity": requested_quantity,
        "filled_quantity": filled_quantity,
        "remaining_quantity": max(requested_quantity - filled_quantity, 0.0),
        "fill_ratio": fill_ratio,
        "attempt_count": len(execution_attempts),
        "repriced_attempts": max(len(execution_attempts) - 1, 0),
        "timed_out_attempts": timed_out_attempts,
        "partial_fill_attempts": partial_fill_attempts,
        "aggressive_fallback_used": aggressive_fallback_used,
        "realized_slippage_pct": slippage_pct,
        "slippage_threshold_pct": slippage_threshold_pct,
        "fees_total": fee_paid,
        "realized_pnl_total": realized_pnl,
        "net_realized_pnl_total": realized_pnl - fee_paid,
        "execution_quality_status": execution_quality_status,
        "decision_quality_status": decision_quality_status,
        "signal_vs_execution_note": (
            "Execution quality is measured separately from signal outcome; signal outcome stays pending until realized PnL closes."
        ),
    }


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
        _record_sync_success(
            session,
            settings_row,
            scope="positions",
            detail={"symbol": symbol, "position_status": "flat"},
        )
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
        metadata = local.metadata_json if isinstance(local.metadata_json, dict) else {}
        if "origin" not in metadata:
            metadata["origin"] = "binance_sync"
        local.metadata_json = metadata
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
    _record_sync_success(
        session,
        settings_row,
        scope="positions",
        detail={"symbol": symbol, "position_status": "open", "side": side},
    )
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


def _execute_primary_order_with_policy(
    session: Session,
    *,
    client: BinanceClient,
    settings_row: Setting,
    symbol: str,
    side: str,
    execution_plan: ExecutionPlan,
    requested_quantity: float,
    requested_price: float,
    decision_run_id: int,
    risk_row: RiskCheck | None,
    reduce_only: bool,
    close_only: bool,
    intent_type: str,
) -> dict[str, Any]:
    root_order: Order | None = None
    final_order: Order | None = None
    total_fee_paid = 0.0
    total_realized_pnl = 0.0
    total_filled_quantity = 0.0
    total_fill_notional = 0.0
    current_quantity = requested_quantity
    current_price = requested_price
    current_order_type: str = execution_plan.order_type
    attempt_index = 0
    execution_attempts: list[dict[str, object]] = []
    aggressive_fallback_used = False

    while current_quantity > 0:
        submit_price = current_price if current_order_type == "LIMIT" else None
        submit_tif = execution_plan.time_in_force if current_order_type == "LIMIT" else None
        client_order_id, exchange_order = _safe_submit_order(
            client,
            symbol=symbol,
            side=side,
            order_type=current_order_type,
            quantity=current_quantity,
            price=submit_price,
            reduce_only=reduce_only,
            close_position=close_only and current_order_type == "MARKET",
            response_type="RESULT",
            time_in_force=submit_tif,
        )
        parent_order_id = root_order.id if root_order is not None else None
        order = _upsert_exchange_order_row(
            session,
            symbol=symbol,
            requested_price=submit_price if submit_price is not None else requested_price,
            requested_quantity=current_quantity,
            order_type=current_order_type,
            side=side.lower(),
            exchange_order={**exchange_order, "clientOrderId": client_order_id},
            decision_run_id=decision_run_id,
            risk_row=risk_row,
            reduce_only=reduce_only,
            close_only=close_only,
            parent_order_id=parent_order_id,
        )
        order.metadata_json = {
            **(order.metadata_json or {}),
            "execution_policy": execution_plan.to_payload(),
            "execution_attempt": attempt_index + 1,
        }
        session.add(order)
        session.flush()
        if root_order is None:
            root_order = order
        final_order = order

        latest_exchange_order = dict(exchange_order)
        timed_out = False
        if current_order_type == "LIMIT" and execution_plan.timeout_seconds > 0 and order.status in {"pending", "partially_filled"}:
            poll_cycles = max(
                int(max(execution_plan.timeout_seconds, execution_plan.poll_interval_seconds) / max(execution_plan.poll_interval_seconds, 1)),
                1,
            )
            for _ in range(poll_cycles):
                _execution_policy_sleep(execution_plan.poll_interval_seconds)
                latest_exchange_order = _fetch_exchange_order(
                    client,
                    symbol=symbol,
                    order_type=current_order_type,
                    order_id=order.external_order_id,
                    client_order_id=order.client_order_id,
                )
                order = _upsert_exchange_order_row(
                    session,
                    symbol=symbol,
                    requested_price=submit_price if submit_price is not None else requested_price,
                    requested_quantity=current_quantity,
                    order_type=current_order_type,
                    side=side.lower(),
                    exchange_order=latest_exchange_order,
                    decision_run_id=decision_run_id,
                    risk_row=risk_row,
                    reduce_only=reduce_only,
                    close_only=close_only,
                    parent_order_id=root_order.id if root_order is not None and root_order.id != order.id else parent_order_id,
                )
                final_order = order
                if order.status not in {"pending", "partially_filled"}:
                    break
            if order.status in {"pending", "partially_filled"}:
                timed_out = True
                record_audit_event(
                    session,
                    event_type="live_limit_timeout",
                    entity_type="order",
                    entity_id=str(order.id),
                    severity="warning",
                    message="Passive limit order timed out before full execution.",
                    payload={
                        "symbol": symbol,
                        "intent_type": intent_type,
                        "attempt": attempt_index + 1,
                        "order_type": current_order_type,
                        "requested_quantity": current_quantity,
                        "requested_price": submit_price,
                        "execution_policy": execution_plan.to_payload(),
                    },
                )

        trades = client.get_account_trades(symbol=symbol, order_id=order.external_order_id)
        fee_paid, realized_pnl = _record_live_trades(session, order, trades)
        filled_quantity = min(_sum_trade_quantity(trades), current_quantity)
        average_fill_price = order.average_fill_price or submit_price or requested_price
        fill_slippage_pct = 0.0
        if filled_quantity > 0 and average_fill_price > 0:
            fill_slippage_pct = abs(average_fill_price - requested_price) / max(requested_price, 1.0)
        if filled_quantity > 0:
            total_fee_paid += fee_paid
            total_realized_pnl += realized_pnl
            total_filled_quantity += filled_quantity
            total_fill_notional += filled_quantity * max(average_fill_price, 0.0)
            if filled_quantity < current_quantity:
                record_audit_event(
                    session,
                    event_type="live_limit_partial_fill",
                    entity_type="order",
                    entity_id=str(order.id),
                    severity="info",
                    message="Limit order received a partial fill.",
                    payload={
                        "symbol": symbol,
                        "intent_type": intent_type,
                        "attempt": attempt_index + 1,
                        "filled_quantity": filled_quantity,
                        "remaining_quantity": max(current_quantity - filled_quantity, 0.0),
                        "fill_slippage_pct": fill_slippage_pct,
                        "execution_policy": execution_plan.to_payload(),
                    },
                )

        remaining_quantity = max(requested_quantity - total_filled_quantity, 0.0)
        remaining_ratio = _remaining_fill_ratio(
            requested_quantity=requested_quantity,
            filled_quantity=total_filled_quantity,
        )
        execution_attempts.append(
            {
                "order_id": order.id,
                "exchange_status": order.exchange_status,
                "status": order.status,
                "order_type": current_order_type,
                "requested_quantity": current_quantity,
                "requested_price": submit_price,
                "filled_quantity": filled_quantity,
                "average_fill_price": average_fill_price if filled_quantity > 0 else None,
                "fill_slippage_pct": fill_slippage_pct,
                "remaining_quantity": remaining_quantity,
                "remaining_ratio": remaining_ratio,
                "timed_out": timed_out,
            }
        )
        order.metadata_json = {
            **(order.metadata_json or {}),
            "execution_policy": execution_plan.to_payload(),
            "execution_attempt": attempt_index + 1,
            "execution_attempts": execution_attempts,
        }
        session.add(order)
        session.flush()

        if current_order_type != "LIMIT" or remaining_quantity <= 0.0:
            break
        if not timed_out and order.status == "filled":
            break

        if order.status not in FINAL_ORDER_STATUSES:
            _cancel_exchange_order(
                client,
                symbol=symbol,
                order_id=order.external_order_id,
                client_order_id=order.client_order_id,
                order_type=current_order_type,
            )
            latest_exchange_order = _fetch_exchange_order(
                client,
                symbol=symbol,
                order_type=current_order_type,
                order_id=order.external_order_id,
                client_order_id=order.client_order_id,
            )
            order = _upsert_exchange_order_row(
                session,
                symbol=symbol,
                requested_price=submit_price if submit_price is not None else requested_price,
                requested_quantity=current_quantity,
                order_type=current_order_type,
                side=side.lower(),
                exchange_order=latest_exchange_order,
                decision_run_id=decision_run_id,
                risk_row=risk_row,
                reduce_only=reduce_only,
                close_only=close_only,
                parent_order_id=root_order.id if root_order is not None and root_order.id != order.id else parent_order_id,
            )
            final_order = order

        live_reference_price = _resolve_live_reference_price(
            client,
            symbol=symbol,
            fallback_price=submit_price if submit_price is not None else requested_price,
        )
        current_slippage_pct = abs(live_reference_price - max(submit_price or requested_price, 1.0)) / max(live_reference_price, 1.0)
        if should_fallback_aggressively(
            execution_plan,
            reprice_attempt=attempt_index,
            current_slippage_pct=current_slippage_pct,
            slippage_threshold_pct=settings_row.slippage_threshold_pct,
            current_volatility_pct=execution_plan.volatility_pct,
            remaining_ratio=remaining_ratio if filled_quantity > 0 else None,
        ):
            aggressive_fallback_used = True
            record_audit_event(
                session,
                event_type="live_limit_aggressive_fallback",
                entity_type="order",
                entity_id=str(order.id),
                severity="warning",
                message="Limit order escalated to aggressive execution fallback.",
                payload={
                    "symbol": symbol,
                    "intent_type": intent_type,
                    "attempt": attempt_index + 1,
                    "remaining_quantity": remaining_quantity,
                    "current_slippage_pct": current_slippage_pct,
                    "remaining_ratio": remaining_ratio,
                    "execution_policy": execution_plan.to_payload(),
                },
            )
            current_order_type = execution_plan.fallback_order_type
            current_quantity = _normalize_remaining_quantity(
                client,
                symbol=symbol,
                remaining_quantity=remaining_quantity,
                reference_price=live_reference_price,
            )
            current_price = live_reference_price
            if current_quantity <= 0:
                break
            attempt_index += 1
            continue

        next_quantity = _normalize_remaining_quantity(
            client,
            symbol=symbol,
            remaining_quantity=remaining_quantity,
            reference_price=live_reference_price,
        )
        if next_quantity <= 0:
            break
        current_quantity = next_quantity
        current_price = _compute_limit_reprice(
            client,
            symbol=symbol,
            side=side,
            current_price=max(submit_price or requested_price, 1.0),
            live_reference_price=live_reference_price,
            reprice_bps=execution_plan.reprice_bps,
        )
        record_audit_event(
            session,
            event_type="live_limit_repriced",
            entity_type="order",
            entity_id=str(order.id),
            severity="info",
            message="Limit order was canceled and repriced for another passive attempt.",
            payload={
                "symbol": symbol,
                "intent_type": intent_type,
                "attempt": attempt_index + 2,
                "remaining_quantity": current_quantity,
                "repriced_limit": current_price,
                "execution_policy": execution_plan.to_payload(),
            },
        )
        attempt_index += 1
        if attempt_index > execution_plan.max_requotes and execution_plan.fallback_order_type == "NONE":
            break

    if final_order is None:
        raise RuntimeError("Execution policy did not produce an exchange order.")

    aggregate_avg_fill_price = (
        total_fill_notional / total_filled_quantity if total_filled_quantity > 0 else final_order.average_fill_price
    )
    final_status = final_order.status
    if total_filled_quantity > 0 and total_filled_quantity + 1e-9 < requested_quantity:
        final_status = "partially_filled"
    elif total_filled_quantity >= requested_quantity:
        final_status = "filled"
    execution_quality = _build_execution_quality_summary(
        plan=execution_plan,
        requested_quantity=requested_quantity,
        requested_price=requested_price,
        filled_quantity=total_filled_quantity,
        average_fill_price=float(aggregate_avg_fill_price or 0.0),
        fee_paid=total_fee_paid,
        realized_pnl=total_realized_pnl,
        execution_attempts=execution_attempts,
        slippage_threshold_pct=settings_row.slippage_threshold_pct,
        aggressive_fallback_used=aggressive_fallback_used,
    )
    final_order.metadata_json = {
        **(final_order.metadata_json or {}),
        "execution_policy": execution_plan.to_payload(),
        "execution_attempts": execution_attempts,
        "execution_quality": execution_quality,
    }
    session.add(final_order)
    session.flush()

    return {
        "order": final_order,
        "fees": total_fee_paid,
        "realized_pnl": total_realized_pnl,
        "filled_quantity": total_filled_quantity,
        "average_fill_price": aggregate_avg_fill_price,
        "status": final_status,
        "attempts": execution_attempts,
        "execution_quality": execution_quality,
    }


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
        create_exchange_pnl_snapshot(session, settings_row)
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
                _record_sync_issue(
                    session,
                    settings_row,
                    scope="open_orders",
                    status="failed",
                    reason_code=reason_code,
                    detail={"symbol": order.symbol, "stage": "trade_lookup"},
                )
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
            _apply_exchange_order_state(
                order,
                exchange_order,
                requested_quantity_fallback=order.requested_quantity,
                requested_price_fallback=order.requested_price,
                reduce_only_fallback=order.reduce_only,
                close_only_fallback=order.close_only,
            )
            session.add(order)
            if _is_protective_order_type_name(order.order_type):
                synced_orders += 1
                continue
            try:
                trades = client.get_account_trades(symbol=order.symbol, order_id=order.external_order_id)
            except Exception as exc:
                reason_code = _classify_exchange_state_error(exc, "TEMPORARY_SYNC_FAILURE")
                _record_sync_issue(
                    session,
                    settings_row,
                    scope="open_orders",
                    status="failed",
                    reason_code=reason_code,
                    detail={"symbol": order.symbol, "stage": "order_lookup"},
                )
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
            create_exchange_pnl_snapshot(session, settings_row)
            synced_orders += 1
        try:
            open_orders = client.get_open_orders(item_symbol)
            _record_sync_success(
                session,
                settings_row,
                scope="open_orders",
                detail={"symbol": item_symbol, "open_order_count": len(open_orders)},
            )
        except Exception as exc:
            reason_code = _classify_exchange_state_error(exc, "EXCHANGE_OPEN_ORDERS_SYNC_FAILED")
            _record_sync_issue(
                session,
                settings_row,
                scope="open_orders",
                status="failed",
                reason_code=reason_code,
                detail={"symbol": item_symbol},
            )
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
            _record_sync_issue(
                session,
                settings_row,
                scope="protective_orders",
                status="incomplete",
                reason_code="PROTECTION_STATE_UNVERIFIED",
                detail={"symbol": item_symbol, "missing_components": protection_state.get("missing_components", [])},
            )
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
                _record_sync_success(
                    session,
                    settings_row,
                    scope="protective_orders",
                    detail={
                        "symbol": item_symbol,
                        "status": protection_result["protection_state"].get("status", "protected"),
                    },
                )
        else:
            _record_sync_success(
                session,
                settings_row,
                scope="protective_orders",
                detail={"symbol": item_symbol, "status": protection_state["status"]},
            )
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
        _record_sync_issue(
            session,
            settings_row,
            scope="account",
            status="failed",
            reason_code=reason_code,
            detail={"symbol": symbols[0]},
        )
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
    _record_sync_success(
        session,
        settings_row,
        scope="account",
        detail={"symbol": symbols[0], "equity": pnl_snapshot.equity},
    )
    runtime_state = get_protection_recovery_detail(settings_row)
    return {
        "symbols": symbols,
        "synced_orders": synced_orders,
        "synced_positions": synced_positions,
        "equity": pnl_snapshot.equity,
        "sync_freshness_summary": build_sync_freshness_summary(settings_row),
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


def _resync_exchange_state(
    session: Session,
    settings_row: Setting,
    *,
    client: BinanceClient,
    symbol: str,
    event_prefix: str,
    component: str,
    verify_protection: bool,
) -> dict[str, object]:
    try:
        open_orders = client.get_open_orders(symbol)
        _record_sync_success(
            session,
            settings_row,
            scope="open_orders",
            detail={"symbol": symbol, "open_order_count": len(open_orders)},
        )
    except Exception as exc:
        reason_code = _classify_exchange_state_error(exc, "EXCHANGE_OPEN_ORDERS_SYNC_FAILED")
        _record_sync_issue(
            session,
            settings_row,
            scope="open_orders",
            status="failed",
            reason_code=reason_code,
            detail={"symbol": symbol},
        )
        _pause_for_system_issue(
            session,
            settings_row,
            reason_code=reason_code,
            symbol=symbol,
            error=str(exc),
            event_type=f"{event_prefix}_open_orders_failed",
            component=component,
            alert_title="Open orders resync failed",
            alert_message="Exchange open orders resync failed after order processing.",
        )
        raise RuntimeError(f"{reason_code}: {exc}") from exc

    try:
        synced_position = sync_live_positions(
            session,
            settings_row,
            symbol=symbol,
            client=client,
            open_orders=open_orders,
        )
    except Exception as exc:
        reason_code = _classify_exchange_state_error(exc, "EXCHANGE_POSITION_SYNC_FAILED")
        _record_sync_issue(
            session,
            settings_row,
            scope="positions",
            status="failed",
            reason_code=reason_code,
            detail={"symbol": symbol},
        )
        _pause_for_system_issue(
            session,
            settings_row,
            reason_code=reason_code,
            symbol=symbol,
            error=str(exc),
            event_type=f"{event_prefix}_position_sync_failed",
            component=component,
            alert_title="Position resync failed",
            alert_message="Exchange position resync failed after order processing.",
        )
        raise RuntimeError(f"{reason_code}: {exc}") from exc

    position = get_open_position(session, symbol)
    protection_state = _build_protection_state(position, open_orders)
    if verify_protection:
        if position is not None and position.quantity > 0 and protection_state["status"] != "protected":
            _record_sync_issue(
                session,
                settings_row,
                scope="protective_orders",
                status="incomplete",
                reason_code="PROTECTION_STATE_UNVERIFIED",
                detail={
                    "symbol": symbol,
                    "missing_components": protection_state.get("missing_components", []),
                    "protective_order_count": protection_state.get("protective_order_count", 0),
                },
            )
        else:
            _record_sync_success(
                session,
                settings_row,
                scope="protective_orders",
                detail={
                    "symbol": symbol,
                    "status": protection_state["status"],
                    "protective_order_count": protection_state.get("protective_order_count", 0),
                },
            )

    try:
        account_info = client.get_account_info()
        pnl_snapshot = create_exchange_pnl_snapshot(session, settings_row, account_info)
        _record_sync_success(
            session,
            settings_row,
            scope="account",
            detail={"symbol": symbol, "equity": pnl_snapshot.equity},
        )
    except Exception as exc:
        reason_code = _classify_exchange_state_error(exc, "EXCHANGE_ACCOUNT_STATE_UNAVAILABLE")
        _record_sync_issue(
            session,
            settings_row,
            scope="account",
            status="failed",
            reason_code=reason_code,
            detail={"symbol": symbol},
        )
        _pause_for_system_issue(
            session,
            settings_row,
            reason_code=reason_code,
            symbol=symbol,
            error=str(exc),
            event_type=f"{event_prefix}_account_sync_failed",
            component=component,
            alert_title="Account resync failed",
            alert_message="Exchange account resync failed after order processing.",
        )
        raise RuntimeError(f"{reason_code}: {exc}") from exc

    return {
        "open_orders": open_orders,
        "position": synced_position,
        "protection_state": protection_state,
        "account_info": account_info,
        "pnl_snapshot": pnl_snapshot,
    }


def execute_live_trade(
    session: Session,
    settings_row: Setting,
    decision_run_id: int | None,
    decision: TradeDecision,
    market_snapshot: MarketSnapshotPayload,
    risk_result: RiskCheckResult,
    risk_row: RiskCheck | None = None,
) -> dict[str, Any]:
    if not risk_result.allowed:
        record_audit_event(
            session,
            event_type="live_execution_blocked",
            entity_type="decision_run",
            entity_id=str(decision_run_id),
            severity="warning",
            message="Live execution skipped because risk_guard blocked the intent.",
            payload={
                "symbol": decision.symbol,
                "decision": decision.model_dump(mode="json"),
                "reason_codes": list(risk_result.reason_codes),
                "risk_check_id": risk_row.id if risk_row is not None else None,
                "risk_debug_payload": dict(risk_result.debug_payload),
            },
        )
        session.flush()
        return {
            "status": "blocked",
            "reason_codes": list(risk_result.reason_codes),
            "decision": decision.decision,
        }

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
        _record_sync_success(
            session,
            settings_row,
            scope="open_orders",
            detail={"symbol": decision.symbol, "open_order_count": len(open_orders)},
        )
    except Exception as exc:
        reason_code = _classify_exchange_state_error(exc, "EXCHANGE_OPEN_ORDERS_SYNC_FAILED")
        _record_sync_issue(
            session,
            settings_row,
            scope="open_orders",
            status="failed",
            reason_code=reason_code,
            detail={"symbol": decision.symbol},
        )
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
        _record_sync_issue(
            session,
            settings_row,
            scope="positions",
            status="failed",
            reason_code=reason_code,
            detail={"symbol": decision.symbol},
        )
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
    reduce_fraction = 1.0

    if decision.decision in {"reduce", "exit"}:
        if existing_position is None:
            return {"status": "rejected", "reason_codes": ["NO_OPEN_POSITION"], "intent_type": intent_type}
        side = "SELL" if existing_position.side == "long" else "BUY"
        reduce_fraction = 1.0 if decision.decision == "exit" else _reduce_fraction_for_decision(decision, settings_row)
        requested_quantity = existing_position.quantity * reduce_fraction
        reduce_only = True

    normalized_quantity = client.normalize_order_quantity(
        decision.symbol,
        requested_quantity,
        reference_price=intent.requested_price,
        enforce_min_notional=intent_type in {"entry", "scale_in"} and risk_result.approved_projected_notional <= 0,
    )
    if intent_type in {"entry", "scale_in"} and risk_result.approved_projected_notional > 0:
        normalized_quantity = _cap_quantity_to_approved_notional(
            client,
            symbol=decision.symbol,
            quantity=normalized_quantity,
            reference_price=intent.requested_price,
            approved_notional=risk_result.approved_projected_notional,
        )
        min_notional_failure = _execution_minimum_notional_failure(
            client,
            symbol=decision.symbol,
            quantity=normalized_quantity,
            reference_price=intent.requested_price,
        )
        if min_notional_failure is not None:
            record_audit_event(
                session,
                event_type="live_execution_blocked",
                entity_type="decision_run",
                entity_id=str(decision_run_id),
                severity="warning",
                message="Live execution skipped because the approved auto-resized size is below the executable minimum.",
                payload={
                    "symbol": decision.symbol,
                    "reason_code": min_notional_failure,
                    "approved_projected_notional": risk_result.approved_projected_notional,
                    "approved_quantity": risk_result.approved_quantity,
                    "risk_check_id": risk_row.id if risk_row is not None else None,
                    "risk_debug_payload": dict(risk_result.debug_payload),
                },
            )
            session.flush()
            return {
                "status": "blocked",
                "reason_codes": [min_notional_failure],
                "decision": decision.decision,
            }
    execution_plan = select_execution_plan(
        intent,
        market_snapshot,
        settings_row,
        pre_trade_protection=pre_trade_protection,
    )
    execution_price = intent.requested_price
    if execution_plan.price is not None:
        if hasattr(client, "normalize_price"):
            execution_price = client.normalize_price(decision.symbol, execution_plan.price)
        else:
            execution_price = execution_plan.price
    try:
        client.change_initial_leverage(decision.symbol, max(1, int(round(intent.leverage))))
        execution_result = _execute_primary_order_with_policy(
            session,
            client=client,
            settings_row=settings_row,
            symbol=decision.symbol,
            side=side,
            execution_plan=execution_plan,
            requested_quantity=normalized_quantity,
            requested_price=execution_price,
            decision_run_id=decision_run_id,
            risk_row=risk_row,
            reduce_only=reduce_only,
            close_only=decision.decision == "exit",
            intent_type=intent_type,
        )
    except BinanceAPIError as exc:
        reason_codes = ["BINANCE_ORDER_REJECTED"]
        if exc.code == -2019:
            reason_codes.append("INSUFFICIENT_MARGIN")
        order = _create_rejected_order_row(
            session,
            symbol=decision.symbol,
            side=decision.decision,
            order_type=execution_plan.order_type,
            requested_quantity=normalized_quantity,
            requested_price=execution_price,
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
                "requested_quantity": normalized_quantity,
                "requested_price": execution_price,
                "execution_policy": execution_plan.to_payload(),
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
                "requested_price": execution_price,
                "execution_policy": execution_plan.to_payload(),
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
                "requested_price": execution_price,
                "execution_policy": execution_plan.to_payload(),
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
            "execution_policy": execution_plan.to_payload(),
        }
    except Exception as exc:
        order = _create_rejected_order_row(
            session,
            symbol=decision.symbol,
            side=decision.decision,
            order_type=execution_plan.order_type,
            requested_quantity=normalized_quantity,
            requested_price=execution_price,
            decision_run_id=decision_run_id,
            risk_row=risk_row,
            reduce_only=reduce_only,
            close_only=decision.decision == "exit",
            reason_codes=["LIVE_EXECUTION_ERROR"],
            metadata_json={
                "error": str(exc),
                "intent_type": intent_type,
                "requested_quantity": normalized_quantity,
                "requested_price": execution_price,
                "execution_policy": execution_plan.to_payload(),
            },
        )
        create_alert(
            session,
            category="execution",
            severity="error",
            title="Live execution failed",
            message="??? ?? ? ??? ??????.",
            payload={
                "symbol": decision.symbol,
                "error": str(exc),
                "intent_type": intent_type,
                "requested_quantity": normalized_quantity,
                "requested_price": execution_price,
                "execution_policy": execution_plan.to_payload(),
            },
        )
        record_audit_event(
            session,
            event_type="live_execution_error",
            entity_type="order",
            entity_id=str(order.id),
            severity="error",
            message="Live execution failed before exchange acceptance.",
            payload={
                "symbol": decision.symbol,
                "error": str(exc),
                "intent_type": intent_type,
                "requested_quantity": normalized_quantity,
                "requested_price": execution_price,
                "execution_policy": execution_plan.to_payload(),
            },
        )
        record_health_event(
            session,
            component="live_execution",
            status="error",
            message="Unexpected live execution error.",
            payload={
                "symbol": decision.symbol,
                "error": str(exc),
                "intent_type": intent_type,
                "execution_policy": execution_plan.to_payload(),
            },
        )
        session.flush()
        return {
            "order_id": order.id,
            "status": "error",
            "reason_codes": ["LIVE_EXECUTION_ERROR"],
            "error": str(exc),
            "intent_type": intent_type,
            "execution_policy": execution_plan.to_payload(),
        }
    order = execution_result["order"]
    fee_paid = float(execution_result["fees"])
    realized_pnl = float(execution_result["realized_pnl"])
    aggregate_fill_price = float(execution_result["average_fill_price"])
    aggregate_filled_quantity = float(execution_result["filled_quantity"])
    final_execution_status = str(execution_result["status"])
    execution_quality = dict(execution_result.get("execution_quality") or {})
    net_realized_pnl = realized_pnl - fee_paid
    if execution_quality:
        if aggregate_filled_quantity > 0 and abs(net_realized_pnl) > 1e-9:
            execution_quality["decision_quality_status"] = "profit" if net_realized_pnl > 0 else "loss"
        elif aggregate_filled_quantity > 0 and abs(net_realized_pnl) <= 1e-9:
            execution_quality["decision_quality_status"] = "flat_or_pending"
        order.metadata_json = {
            **(order.metadata_json or {}),
            "execution_quality": execution_quality,
        }
        session.add(order)
        session.flush()
    create_exchange_pnl_snapshot(session, settings_row)

    try:
        post_trade_sync = _resync_exchange_state(
            session,
            settings_row,
            client=client,
            symbol=decision.symbol,
            event_prefix="live_post_order",
            component="live_execution",
            verify_protection=False,
        )
    except RuntimeError as exc:
        error_text = str(exc)
        reason_code = error_text.split(":", 1)[0]
        return {
            "order_id": order.id,
            "status": final_execution_status,
            "reason_codes": [reason_code],
            "error": error_text,
            "intent_type": intent_type,
        }

    synced_position = post_trade_sync["position"]

    position = get_open_position(session, decision.symbol)
    if position is not None:
        order.position_id = position.id
        session.add(order)
        session.flush()

    position_management_payload: dict[str, object] | None = None
    if position is not None:
        if decision.decision in {"long", "short"}:
            position_management_payload = seed_position_management_metadata(
                position,
                max_holding_minutes=decision.max_holding_minutes,
                timeframe=decision.timeframe,
                stop_loss=intent.stop_loss or position.stop_loss,
                take_profit=intent.take_profit or position.take_profit,
                reset_partial_take_profit=True,
            )
        elif decision.decision in {"reduce", "exit"}:
            position_management_payload = (
                position.metadata_json.get("position_management")
                if isinstance(position.metadata_json, dict)
                and isinstance(position.metadata_json.get("position_management"), dict)
                else None
            )
        if position_management_payload is not None:
            session.add(position)
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
                "fill_price": aggregate_fill_price,
                "fill_quantity": aggregate_filled_quantity,
                "realized_pnl": realized_pnl,
                "fees": fee_paid,
                "protective_order_ids": protective_order_ids,
                "protective_state": protection_result["protection_state"],
                "emergency_action": protection_result["emergency_action"],
                "intent_type": intent_type,
                "execution_attempts": execution_result["attempts"],
                "execution_quality": execution_quality,
                "position_management": {
                    "reduce_fraction": reduce_fraction,
                    "rationale_codes": decision.rationale_codes,
                    "metadata": position_management_payload,
                },
            }
    else:
        _cancel_exit_orders(session, client, decision.symbol)

    try:
        final_resync = _resync_exchange_state(
            session,
            settings_row,
            client=client,
            symbol=decision.symbol,
            event_prefix="live_post_protection",
            component="live_execution",
            verify_protection=True,
        )
    except RuntimeError as exc:
        error_text = str(exc)
        reason_code = error_text.split(":", 1)[0]
        return {
            "order_id": order.id,
            "status": final_execution_status,
            "reason_codes": [reason_code],
            "error": error_text,
            "intent_type": intent_type,
        }

    synced_position = final_resync["position"]
    if protection_result is None:
        final_protection_state = final_resync["protection_state"]
    else:
        final_protection_state = protection_result["protection_state"]

    slippage_pct = 0.0
    if aggregate_filled_quantity > 0 and aggregate_fill_price > 0:
        slippage_pct = abs(aggregate_fill_price - execution_price) / max(execution_price, 1.0)
    if aggregate_filled_quantity > 0 and slippage_pct > settings_row.slippage_threshold_pct:
        create_alert(
            session,
            category="execution",
            severity="warning",
            title="Slippage threshold exceeded",
            message="??? ????? ???? ??????.",
            payload={
                "order_id": order.id,
                "slippage_pct": slippage_pct,
                "intent_type": intent_type,
                "execution_policy": execution_plan.to_payload(),
                "execution_quality": execution_quality,
            },
        )

    latest_price = position.mark_price if position is not None else market_snapshot.latest_price
    refresh_open_position_marks(session, {decision.symbol: latest_price})
    if (
        position is not None
        and decision.decision == "reduce"
        and "POSITION_MANAGEMENT_PARTIAL_TAKE_PROFIT" in set(decision.rationale_codes)
        and aggregate_filled_quantity > 0
    ):
        position_management_payload = mark_partial_take_profit_taken(position)
        session.add(position)
        session.flush()
    pnl_snapshot = final_resync["pnl_snapshot"]
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
            "protective_state": final_protection_state if protection_result is not None else final_protection_state,
            "pre_trade_protection": pre_trade_protection,
            "slippage_pct": slippage_pct,
            "intent_type": intent_type,
            "execution_policy": execution_plan.to_payload(),
            "execution_attempts": execution_result["attempts"],
            "execution_quality": execution_quality,
            "position_management": {
                "reduce_fraction": reduce_fraction,
                "rationale_codes": decision.rationale_codes,
                "metadata": position_management_payload,
            },
        },
    )
    order.metadata_json = {
        **(order.metadata_json or {}),
        "position_management": {
            "reduce_fraction": reduce_fraction,
            "rationale_codes": decision.rationale_codes,
            "metadata": position_management_payload,
        },
    }
    session.add(order)
    session.flush()
    return {
        "order_id": order.id,
        "position_id": order.position_id,
        "status": final_execution_status,
        "exchange_status": order.exchange_status,
        "fill_price": aggregate_fill_price,
        "fill_quantity": aggregate_filled_quantity,
        "realized_pnl": realized_pnl,
        "fees": fee_paid,
        "equity": pnl_snapshot.equity,
        "protective_order_ids": protective_order_ids,
        "protective_state": final_protection_state,
        "intent_type": intent_type,
        "execution_policy": execution_plan.to_payload(),
        "execution_attempts": execution_result["attempts"],
        "execution_quality": execution_quality,
        "position_management": {
            "reduce_fraction": reduce_fraction,
            "rationale_codes": decision.rationale_codes,
            "metadata": position_management_payload,
        },
    }
