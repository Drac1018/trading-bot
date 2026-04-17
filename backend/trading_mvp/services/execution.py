from __future__ import annotations

import asyncio
from copy import deepcopy
from hashlib import sha1
from threading import Lock
import time
from datetime import datetime, timedelta
from math import floor
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_mvp.config import get_settings
from trading_mvp.models import Execution, Order, PnLSnapshot, Position, RiskCheck, Setting
from trading_mvp.schemas import (
    ExecutionIntent,
    FeaturePayload,
    MarketCandle,
    MarketSnapshotPayload,
    ProtectionLifecycleSnapshot,
    ProtectionLifecycleState,
    ProtectionLifecycleTransition,
    RiskCheckResult,
    TradeDecision,
)
from trading_mvp.services.account import (
    create_exchange_pnl_snapshot,
    fetch_incremental_funding_entries,
    get_latest_pnl_snapshot,
    get_open_position,
    record_funding_ledger_entries,
    refresh_open_position_marks,
)
from trading_mvp.services.audit import (
    create_alert,
    normalize_correlation_ids,
    record_audit_event,
    record_health_event,
    record_position_management_event,
)
from trading_mvp.services.binance import BinanceAPIError, BinanceClient
from trading_mvp.services.binance_user_stream import (
    BinanceUserStreamListener,
    USER_STREAM_FALLBACK_SOURCE,
    build_user_stream_state,
    normalize_user_stream_event,
)
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
    build_execution_dedupe_key,
    build_sync_freshness_summary,
    clear_execution_lock,
    get_reconciliation_detail,
    get_user_stream_detail,
    get_execution_dedupe_record,
    get_operating_state,
    get_protection_recovery_detail,
    mark_execution_lock,
    mark_sync_issue,
    mark_sync_success,
    replace_user_stream_detail,
    set_reconciliation_detail,
    set_user_stream_detail,
    should_use_rest_order_reconciliation,
    store_execution_dedupe_record,
)
from trading_mvp.services.settings import (
    get_effective_symbol_schedule,
    get_limited_live_max_notional,
    get_rollout_mode,
    get_runtime_credentials,
    rollout_mode_allows_exchange_submit,
    set_trading_pause,
)
from trading_mvp.time_utils import utcnow_naive

FINAL_ORDER_STATUSES = {"filled", "canceled", "rejected", "expired"}
AUTO_RESUME_DELAY_MINUTES = 5
PROTECTIVE_ORDER_TYPES = ("STOP_MARKET", "TAKE_PROFIT_MARKET")
PROTECTION_RETRY_ATTEMPTS = 2
PROTECTION_VERIFY_FETCH_ATTEMPTS = 2
PROTECTION_VERIFY_FAILED_REASON_CODE = "PROTECTION_VERIFY_FAILED"
PROTECTION_VERIFY_BLOCKING_INTENT_TYPES = {"entry", "scale_in"}
INACTIVE_PROTECTIVE_ORDER_STATUSES = {"CANCELED", "CANCELLED", "REJECTED", "EXPIRED", "FILLED"}
DUPLICATE_EXECUTION_SUPPRESSED_REASON_CODE = "DUPLICATE_EXECUTION_SUPPRESSED"
UNKNOWN_SUBMISSION_REASON_CODE = "LIVE_ORDER_SUBMISSION_UNKNOWN"
POSITION_MODE_ONE_WAY = "one_way"
POSITION_MODE_HEDGE = "hedge"
POSITION_MODE_UNKNOWN = "unknown"
POSITION_MODE_UNCLEAR_REASON_CODE = "EXCHANGE_POSITION_MODE_UNCLEAR"
POSITION_MODE_MISMATCH_REASON_CODE = "EXCHANGE_POSITION_MODE_MISMATCH"
FUNDING_LEDGER_SYNC_REASON_CODE = "FUNDING_LEDGER_SYNC_FAILED"
ROLLOUT_MODE_SHADOW_REASON_CODE = "ROLLOUT_MODE_SHADOW"
ROLLOUT_MODE_LIVE_DRY_RUN_REASON_CODE = "ROLLOUT_MODE_LIVE_DRY_RUN"

_ACTIVE_SYMBOL_EXECUTION_LOCKS: dict[str, dict[str, object]] = {}
_ACTIVE_SYMBOL_EXECUTION_LOCKS_GUARD = Lock()
ORDER_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"pending", "partially_filled", "filled", "canceled", "rejected", "expired"},
    "partially_filled": {"partially_filled", "filled", "canceled", "rejected", "expired"},
    "filled": {"filled"},
    "canceled": {"canceled"},
    "rejected": {"rejected"},
    "expired": {"expired"},
}


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


def _as_object_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _to_bool(value: object, default: bool = False) -> bool:
    if value in {None, ""}:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _coerce_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000.0
        return datetime.fromtimestamp(timestamp)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _normalize_position_mode(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {POSITION_MODE_ONE_WAY, "oneway", "one-way", "single"}:
        return POSITION_MODE_ONE_WAY
    if normalized in {POSITION_MODE_HEDGE, "dual", "dual_side", "dual-side"}:
        return POSITION_MODE_HEDGE
    return POSITION_MODE_UNKNOWN


def _normalize_exchange_position_side(value: object) -> str | None:
    normalized = str(value or "").strip().upper()
    if normalized in {"LONG", "SHORT", "BOTH"}:
        return normalized
    return None


def _resolve_sync_symbols(settings_row: Setting, symbol: str | None) -> list[str]:
    if symbol:
        return [symbol.upper()]
    return [
        effective.symbol
        for effective in get_effective_symbol_schedule(settings_row)
        if effective.enabled
    ]


def _position_mode_guard_reason_code(position_mode: str) -> str | None:
    if position_mode == POSITION_MODE_UNKNOWN:
        return POSITION_MODE_UNCLEAR_REASON_CODE
    if position_mode == POSITION_MODE_HEDGE:
        return POSITION_MODE_MISMATCH_REASON_CODE
    return None


def _position_mode_guard_message(reason_code: str | None) -> str | None:
    if reason_code == POSITION_MODE_UNCLEAR_REASON_CODE:
        return "거래소 포지션 모드를 확인하지 못해 신규 진입을 차단합니다."
    if reason_code == POSITION_MODE_MISMATCH_REASON_CODE:
        return "거래소 Hedge mode가 현재 one-way 로컬 해석과 충돌해 신규 진입을 차단합니다."
    return None


def _exchange_order_position_side(payload: dict[str, object]) -> str | None:
    return _normalize_exchange_position_side(
        payload.get("positionSide")
        or payload.get("ps")
    )


def _position_metadata_side(position: Position | None) -> str | None:
    if position is None:
        return None
    metadata = position.metadata_json if isinstance(position.metadata_json, dict) else {}
    explicit = _normalize_exchange_position_side(metadata.get("exchange_position_side"))
    if explicit in {"LONG", "SHORT"}:
        return explicit
    if position.side == "long":
        return "LONG"
    if position.side == "short":
        return "SHORT"
    return None


def _filter_orders_for_position_context(
    open_orders: list[dict[str, object]],
    *,
    position_mode: str,
    exchange_position_side: str | None,
) -> list[dict[str, object]]:
    if position_mode == POSITION_MODE_HEDGE and exchange_position_side in {"LONG", "SHORT"}:
        filtered = [
            item
            for item in open_orders
            if _exchange_order_position_side(item) == exchange_position_side
        ]
        return filtered
    if position_mode == POSITION_MODE_ONE_WAY:
        return [
            item
            for item in open_orders
            if _exchange_order_position_side(item) in {None, "BOTH"}
        ]
    return list(open_orders)


def _record_position_mode_guard_transition(
    session: Session,
    settings_row: Setting,
    *,
    reason_code: str | None,
    guarded_symbols: list[str],
    position_mode: str,
    position_mode_source: str,
    detail: dict[str, object] | None = None,
) -> None:
    previous = get_reconciliation_detail(settings_row)
    previous_reason = str(previous.get("mode_guard_reason_code") or "") or None
    previous_symbols = [str(item).upper() for item in previous.get("guarded_symbols", []) if item]
    normalized_symbols = [str(item).upper() for item in guarded_symbols if item]
    if previous_reason == reason_code and previous_symbols == normalized_symbols:
        return
    payload = {
        "reason_code": reason_code,
        "guarded_symbols": normalized_symbols,
        "position_mode": position_mode,
        "position_mode_source": position_mode_source,
        **(detail or {}),
    }
    if reason_code:
        message = _position_mode_guard_message(reason_code) or "Exchange position mode guard is active."
        record_audit_event(
            session,
            event_type="exchange_position_mode_guard_enabled",
            entity_type="settings",
            entity_id=str(settings_row.id),
            severity="warning",
            message=message,
            payload=payload,
        )
        record_health_event(
            session,
            component="live_sync",
            status="degraded",
            message=message,
            payload=payload,
        )
        return
    if previous_reason:
        record_audit_event(
            session,
            event_type="exchange_position_mode_guard_cleared",
            entity_type="settings",
            entity_id=str(settings_row.id),
            severity="info",
            message="Exchange position mode guard cleared.",
            payload=payload,
        )
        record_health_event(
            session,
            component="live_sync",
            status="ok",
            message="Exchange position mode guard cleared.",
            payload=payload,
        )


class PreTradeExchangeFilterError(RuntimeError):
    def __init__(self, reason_code: str, *, detail: dict[str, Any] | None = None) -> None:
        self.reason_code = reason_code
        self.detail = detail or {}
        super().__init__(reason_code)


def _stringify_submit_error(exc: BaseException) -> str:
    message = str(exc).strip()
    if message:
        return f"{exc.__class__.__name__}: {message}"
    return exc.__class__.__name__


def _build_submission_tracking(
    *,
    submission_state: str,
    client_order_id: str | None,
    submit_attempt_count: int,
    last_submit_error: str | None = None,
    safe_retry_used: bool = False,
    recovered_via: str | None = None,
) -> dict[str, Any]:
    tracking: dict[str, Any] = {
        "submission_state": submission_state,
        "submit_attempt_count": max(int(submit_attempt_count), 0),
        "last_submit_error": last_submit_error or None,
    }
    if client_order_id:
        tracking["client_order_id"] = client_order_id
    if safe_retry_used:
        tracking["safe_retry_used"] = True
    if recovered_via:
        tracking["recovered_via"] = recovered_via
    tracking["updated_at"] = utcnow_naive().isoformat()
    return tracking


class OrderSubmissionUnknownError(RuntimeError):
    def __init__(
        self,
        *,
        client_order_id: str,
        submit_request: dict[str, Any],
        submit_attempt_count: int,
        last_submit_error: str | None,
        safe_retry_used: bool = False,
        message: str = "Live order submission timed out and could not be reconciled yet.",
    ) -> None:
        self.client_order_id = client_order_id
        self.submit_request = dict(submit_request)
        self.submission_tracking = _build_submission_tracking(
            submission_state="submit_unknown",
            client_order_id=client_order_id,
            submit_attempt_count=submit_attempt_count,
            last_submit_error=last_submit_error,
            safe_retry_used=safe_retry_used,
        )
        super().__init__(message)


def _normalize_submit_request(
    client: BinanceClient,
    *,
    symbol: str,
    quantity: float | None = None,
    price: float | None = None,
    stop_price: float | None = None,
    reference_price: float | None = None,
    approved_notional: float | None = None,
    enforce_min_notional: bool = True,
    close_position: bool = False,
) -> dict[str, Any]:
    if hasattr(client, "normalize_order_request"):
        normalized = client.normalize_order_request(
            symbol=symbol,
            quantity=quantity,
            price=price,
            stop_price=stop_price,
            reference_price=reference_price,
            approved_notional=approved_notional,
            enforce_min_notional=enforce_min_notional,
            close_position=close_position,
        )
    else:
        normalized_price = (
            client.normalize_price(symbol, price) if price is not None and hasattr(client, "normalize_price") else price
        )
        normalized_stop = (
            client.normalize_price(symbol, stop_price)
            if stop_price is not None and hasattr(client, "normalize_price")
            else stop_price
        )
        effective_reference = (
            normalized_price
            if normalized_price is not None and normalized_price > 0
            else normalized_stop
            if normalized_stop is not None and normalized_stop > 0
            else reference_price
        )
        normalized_quantity = quantity
        if quantity is not None and hasattr(client, "normalize_order_quantity"):
            normalized_quantity = client.normalize_order_quantity(
                symbol,
                quantity,
                reference_price=effective_reference,
                enforce_min_notional=enforce_min_notional and not close_position,
            )
        normalized = {
            "symbol": symbol.upper(),
            "quantity": normalized_quantity,
            "price": normalized_price,
            "stop_price": normalized_stop,
            "reference_price": effective_reference,
            "notional": (
                normalized_quantity * effective_reference
                if normalized_quantity is not None and effective_reference is not None
                else None
            ),
            "filters": client.get_symbol_filters(symbol) if hasattr(client, "get_symbol_filters") else {},
            "reason_code": None,
        }
    normalized = dict(normalized)
    filters = normalized.get("filters") if isinstance(normalized.get("filters"), dict) else {}
    effective_reference = _to_float(
        normalized.get("reference_price"),
        _to_float(normalized.get("price"), _to_float(normalized.get("stop_price"), _to_float(reference_price))),
    )
    normalized_quantity_value = normalized.get("quantity")
    if normalized_quantity_value is not None:
        normalized_quantity = abs(_to_float(normalized_quantity_value))
        if approved_notional is not None and approved_notional > 0 and effective_reference > 0:
            normalized_quantity = _cap_quantity_to_approved_notional(
                client,
                symbol=symbol,
                quantity=normalized_quantity,
                reference_price=effective_reference,
                approved_notional=approved_notional,
            )
        normalized["quantity"] = normalized_quantity
        normalized["reference_price"] = effective_reference if effective_reference > 0 else normalized.get("reference_price")
        normalized["notional"] = (
            normalized_quantity * effective_reference
            if normalized_quantity > 0 and effective_reference > 0
            else None
        )
        if not normalized.get("reason_code"):
            min_qty = _to_float(filters.get("min_qty"))
            min_notional = _to_float(filters.get("min_notional"))
            if normalized_quantity <= 0:
                normalized["reason_code"] = "ORDER_QTY_ZERO_AFTER_STEP_SIZE"
            elif min_qty > 0 and normalized_quantity < min_qty:
                normalized["reason_code"] = "ORDER_QTY_BELOW_MIN_QTY"
            elif (
                enforce_min_notional
                and not close_position
                and min_notional > 0
                and effective_reference > 0
                and normalized_quantity * effective_reference < min_notional
            ):
                normalized["reason_code"] = "ORDER_NOTIONAL_BELOW_MIN_NOTIONAL"
    reason_code = str(normalized.get("reason_code") or "") or None
    if reason_code is not None:
        raise PreTradeExchangeFilterError(reason_code, detail=dict(normalized))
    return dict(normalized)


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
    updated_at: datetime | None = None,
) -> None:
    incoming_status = _map_exchange_status(str(exchange_order.get("status", "NEW")))
    current_status = str(row.status or "pending")
    allowed_statuses = ORDER_STATUS_TRANSITIONS.get(current_status, {incoming_status})
    if current_status in FINAL_ORDER_STATUSES and incoming_status != current_status:
        resolved_status = current_status
    elif incoming_status in allowed_statuses:
        resolved_status = incoming_status
    else:
        resolved_status = current_status
    row.status = resolved_status
    row.exchange_status = str(exchange_order.get("status", "")) or None
    row.last_exchange_update_at = updated_at or utcnow_naive()
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


def _ensure_user_stream_registration(
    session: Session,
    settings_row: Setting,
    *,
    client: BinanceClient,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    listener = BinanceUserStreamListener(client)
    state, issues = listener.ensure_registration(get_user_stream_detail(settings_row))
    replace_user_stream_detail(settings_row, state)
    session.add(settings_row)
    session.flush()
    return get_user_stream_detail(settings_row), [dict(item) for item in issues if isinstance(item, dict)]


def _persist_user_stream_state(settings_row: Setting, state: dict[str, Any]) -> None:
    replace_user_stream_detail(settings_row, build_user_stream_state(state))


def _build_account_info_from_user_stream(account_update: dict[str, object]) -> dict[str, object]:
    balances = account_update.get("B") if isinstance(account_update.get("B"), list) else []
    positions = account_update.get("P") if isinstance(account_update.get("P"), list) else []
    wallet_balance = 0.0
    available_balance = 0.0
    if balances:
        preferred = next(
            (
                item
                for item in balances
                if isinstance(item, dict) and str(item.get("a") or "").upper() == "USDT"
            ),
            balances[0] if isinstance(balances[0], dict) else None,
        )
        if isinstance(preferred, dict):
            wallet_balance = _to_float(preferred.get("wb"))
            available_balance = _to_float(preferred.get("cw"), wallet_balance)
    total_unrealized = 0.0
    for item in positions:
        if isinstance(item, dict):
            total_unrealized += _to_float(item.get("up"))
    total_margin_balance = wallet_balance + total_unrealized
    return {
        "availableBalance": available_balance,
        "totalWalletBalance": wallet_balance,
        "totalUnrealizedProfit": total_unrealized,
        "totalMarginBalance": total_margin_balance,
        "assets": balances,
    }


def _apply_user_stream_position_payload(
    session: Session,
    *,
    symbol: str,
    position_payload: dict[str, object],
) -> Position | None:
    position_amt = _to_float(position_payload.get("pa") or position_payload.get("positionAmt"))
    local = get_open_position(session, symbol)
    if abs(position_amt) <= 1e-9:
        if local is not None:
            local.status = "closed"
            local.quantity = 0.0
            local.closed_at = utcnow_naive()
            session.add(local)
            session.flush()
        return None
    entry_price = _to_float(position_payload.get("ep") or position_payload.get("entryPrice"))
    mark_price = _to_float(position_payload.get("mp") or position_payload.get("markPrice"), entry_price)
    leverage = _to_float(position_payload.get("l") or position_payload.get("leverage"), 1.0)
    quantity = abs(position_amt)
    exchange_position_side = _normalize_exchange_position_side(
        position_payload.get("ps") or position_payload.get("positionSide")
    )
    if exchange_position_side == "LONG":
        side = "long"
    elif exchange_position_side == "SHORT":
        side = "short"
    else:
        side = "long" if position_amt > 0 else "short"
        exchange_position_side = "BOTH"
    exchange_position_mode = (
        POSITION_MODE_HEDGE if exchange_position_side in {"LONG", "SHORT"} else POSITION_MODE_ONE_WAY
    )
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
            stop_loss=entry_price if entry_price > 0 else mark_price,
            take_profit=entry_price if entry_price > 0 else mark_price,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            metadata_json={
                "origin": "binance_user_stream",
                "exchange_position_side": exchange_position_side,
                "exchange_position_mode": exchange_position_mode,
            },
        )
    else:
        metadata = local.metadata_json if isinstance(local.metadata_json, dict) else {}
        metadata["origin"] = "binance_user_stream"
        metadata["exchange_position_side"] = exchange_position_side
        metadata["exchange_position_mode"] = exchange_position_mode
        local.metadata_json = metadata
        local.side = side
        local.status = "open"
        local.quantity = quantity
        local.entry_price = entry_price or local.entry_price
        local.mark_price = mark_price or local.mark_price
        local.leverage = leverage or local.leverage
        local.closed_at = None
    local.unrealized_pnl = _to_float(position_payload.get("up"), (mark_price - entry_price) * quantity)
    session.add(local)
    session.flush()
    return local


def apply_user_stream_event(
    session: Session,
    settings_row: Setting,
    *,
    event_payload: dict[str, object],
    normalized_event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = (
        dict(normalized_event)
        if isinstance(normalized_event, dict)
        else normalize_user_stream_event(event_payload)
    )
    raw_payload = _as_object_dict(normalized.get("raw_payload")) or dict(event_payload)
    event_type = str(normalized.get("event_type") or raw_payload.get("e") or raw_payload.get("eventType") or "")
    event_time = _coerce_datetime(normalized.get("event_time")) or _coerce_datetime(raw_payload.get("E")) or utcnow_naive()
    set_user_stream_detail(
        settings_row,
        status="connected",
        source="binance_futures_user_stream",
        last_event_at=event_time,
        last_event_type=event_type,
        heartbeat_ok=True,
        stream_source="user_stream",
        last_error="",
    )
    applied_symbols: list[str] = []
    if event_type == "ACCOUNT_UPDATE":
        account_update = _as_object_dict(raw_payload.get("a"))
        positions = account_update.get("P") if isinstance(account_update.get("P"), list) else []
        for item in positions:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("s") or item.get("symbol") or "").upper()
            if not symbol:
                continue
            _apply_user_stream_position_payload(session, symbol=symbol, position_payload=item)
            applied_symbols.append(symbol)
        if account_update:
            pnl_snapshot = create_exchange_pnl_snapshot(
                session,
                settings_row,
                _build_account_info_from_user_stream(account_update),
            )
            mark_sync_success(
                settings_row,
                scope="account",
                synced_at=event_time,
                detail={"source": "user_stream", "equity": pnl_snapshot.equity},
            )
        mark_sync_success(
            settings_row,
            scope="positions",
            synced_at=event_time,
            detail={"source": "user_stream", "symbols": applied_symbols},
        )
    elif event_type == "ORDER_TRADE_UPDATE":
        order_update = _as_object_dict(raw_payload.get("o"))
        symbol = str(order_update.get("s") or "").upper()
        if symbol:
            exchange_order = {
                "orderId": order_update.get("i") or order_update.get("orderId"),
                "clientOrderId": order_update.get("c") or order_update.get("clientOrderId"),
                "status": order_update.get("X") or order_update.get("status"),
                "origQty": order_update.get("q") or order_update.get("origQty"),
                "executedQty": order_update.get("z") or order_update.get("executedQty"),
                "avgPrice": order_update.get("ap") or order_update.get("avgPrice"),
                "price": order_update.get("p") or order_update.get("price"),
                "stopPrice": order_update.get("sp") or order_update.get("stopPrice"),
                "reduceOnly": order_update.get("R") or order_update.get("reduceOnly"),
                "closePosition": order_update.get("cp") or order_update.get("closePosition"),
                "type": order_update.get("o") or order_update.get("type"),
                "side": order_update.get("S") or order_update.get("side"),
                "positionSide": order_update.get("ps") or order_update.get("positionSide"),
            }
            row = _upsert_exchange_order_row(
                session,
                symbol=symbol,
                requested_price=_exchange_order_requested_price(exchange_order, _to_float(exchange_order.get("price"))),
                requested_quantity=abs(_to_float(exchange_order.get("origQty"))),
                order_type=str(exchange_order.get("type") or "MARKET"),
                side=str(exchange_order.get("side") or "BUY").lower(),
                exchange_order=exchange_order,
                decision_run_id=None,
                risk_row=None,
                reduce_only=_to_bool(exchange_order.get("reduceOnly")),
                close_only=_to_bool(exchange_order.get("closePosition")),
                updated_at=event_time,
            )
            trade_id = str(order_update.get("t") or "")
            last_fill_quantity = abs(_to_float(order_update.get("l")))
            last_fill_price = _to_float(order_update.get("L") or order_update.get("ap"))
            if trade_id and trade_id != "0" and last_fill_quantity > 0:
                existing = session.scalar(select(Execution).where(Execution.external_trade_id == trade_id).limit(1))
                if existing is None:
                    session.add(
                        Execution(
                            order_id=row.id,
                            position_id=row.position_id,
                            symbol=symbol,
                            status="filled",
                            external_trade_id=trade_id,
                            fill_price=last_fill_price,
                            fill_quantity=last_fill_quantity,
                            fee_paid=abs(_to_float(order_update.get("n"))),
                            commission_asset=str(order_update.get("N") or "") or None,
                            slippage_pct=abs(last_fill_price - row.requested_price) / max(row.requested_price, 1.0)
                            if last_fill_price > 0 and row.requested_price > 0
                            else 0.0,
                            realized_pnl=_to_float(order_update.get("rp")),
                            payload={"user_stream": dict(order_update)},
                        )
                    )
            applied_symbols.append(symbol)
            mark_sync_success(
                settings_row,
                scope="open_orders",
                synced_at=event_time,
                detail={"symbol": symbol, "source": "user_stream", "event_type": event_type},
            )
    session.add(settings_row)
    session.flush()
    return {
        "event_type": event_type or "unknown",
        "event_time": event_time.isoformat(),
        "event_category": str(normalized.get("event_category") or "unknown"),
        "related_categories": list(normalized.get("related_categories") or []),
        "symbols": applied_symbols,
        "symbol": str(normalized.get("symbol") or "") or None,
        "order_id": str(normalized.get("order_id") or "") or None,
        "client_order_id": str(normalized.get("client_order_id") or "") or None,
        "order_status": str(normalized.get("order_status") or "") or None,
        "user_stream_summary": get_user_stream_detail(settings_row),
    }


def apply_normalized_user_stream_events(
    session: Session,
    settings_row: Setting,
    *,
    normalized_events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    applied_events: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for event in normalized_events:
        raw_payload = _as_object_dict(event.get("raw_payload"))
        try:
            applied_events.append(
                apply_user_stream_event(
                    session,
                    settings_row,
                    event_payload=raw_payload,
                    normalized_event=event,
                )
            )
        except Exception as exc:
            symbol = str(event.get("symbol") or settings_row.default_symbol or "").upper() or settings_row.default_symbol
            issue = {
                "severity": "warning",
                "reason_code": "USER_STREAM_EVENT_APPLY_FAILED",
                "message": "Failed to apply a Binance futures user stream event.",
                "payload": {
                    "error": str(exc),
                    "event_type": str(event.get("event_type") or "unknown"),
                    "symbol": symbol,
                    "normalized_event": dict(event),
                },
            }
            issues.append(issue)
            set_user_stream_detail(
                settings_row,
                status="degraded",
                heartbeat_ok=False,
                stream_source=USER_STREAM_FALLBACK_SOURCE,
                last_error=str(exc),
                last_disconnected_at=utcnow_naive(),
            )
            record_audit_event(
                session,
                event_type="user_stream_event_apply_failed",
                entity_type="binance",
                entity_id=symbol,
                severity="warning",
                message="Failed to apply a Binance futures user stream event.",
                payload=issue["payload"],
            )
            record_health_event(
                session,
                component="user_stream",
                status="error",
                message="Failed to apply a Binance futures user stream event.",
                payload=issue["payload"],
            )
            session.add(settings_row)
            session.flush()
    return applied_events, issues


def _drain_user_stream_events(
    session: Session,
    settings_row: Setting,
    *,
    client: BinanceClient,
    max_events: int = 8,
    idle_timeout_seconds: float = 0.15,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    listener = BinanceUserStreamListener(client)

    async def _collect() -> dict[str, Any]:
        return await listener.collect_once(
            get_user_stream_detail(settings_row),
            max_events=max_events,
            idle_timeout_seconds=idle_timeout_seconds,
        )

    loop = asyncio.new_event_loop()
    try:
        collected = loop.run_until_complete(_collect())
    finally:
        loop.close()

    state = build_user_stream_state(collected.get("state"))
    _persist_user_stream_state(settings_row, state)
    session.add(settings_row)
    session.flush()

    normalized_events = [
        dict(item)
        for item in collected.get("events", [])
        if isinstance(item, dict)
    ]
    applied_events, apply_issues = apply_normalized_user_stream_events(
        session,
        settings_row,
        normalized_events=normalized_events,
    )
    issues = [dict(item) for item in collected.get("issues", []) if isinstance(item, dict)]
    issues.extend(apply_issues)
    return applied_events, issues, get_user_stream_detail(settings_row)


def poll_live_user_stream(
    session: Session,
    settings_row: Setting,
    *,
    client: BinanceClient | None = None,
    max_events: int = 8,
    idle_timeout_seconds: float = 1.0,
) -> dict[str, object]:
    stream_client = client
    if stream_client is None:
        credentials = get_runtime_credentials(settings_row)
        if not credentials.binance_api_key or not credentials.binance_api_secret:
            set_user_stream_detail(
                settings_row,
                status="unavailable",
                source="binance_futures_user_stream",
                heartbeat_ok=False,
                last_error="LIVE_CREDENTIALS_MISSING",
                stream_source="rest_polling_fallback",
            )
            session.add(settings_row)
            session.flush()
            user_stream_summary = get_user_stream_detail(settings_row)
            return {
                "user_stream_summary": user_stream_summary,
                "stream_health": str(user_stream_summary.get("status") or "unavailable"),
                "last_stream_event_time": user_stream_summary.get("last_event_at"),
                "stream_source": str(user_stream_summary.get("stream_source") or "rest_polling_fallback"),
                "stream_event_count": 0,
                "stream_events": [],
            }
        stream_client = _build_client(settings_row)
    user_stream_summary, stream_issues = _ensure_user_stream_registration(session, settings_row, client=stream_client)
    stream_events: list[dict[str, Any]] = []
    if str(user_stream_summary.get("status") or "") != "degraded":
        try:
            stream_events, drain_issues, user_stream_summary = _drain_user_stream_events(
                session,
                settings_row,
                client=stream_client,
                max_events=max_events,
                idle_timeout_seconds=idle_timeout_seconds,
            )
            stream_issues.extend(drain_issues)
        except Exception as exc:
            reconnect_count = int(user_stream_summary.get("reconnect_count") or 0)
            set_user_stream_detail(
                settings_row,
                status="degraded",
                source="binance_futures_user_stream",
                listen_key=str(user_stream_summary.get("listen_key") or "") or None,
                reconnect_count=reconnect_count + 1,
                heartbeat_ok=False,
                last_error=str(exc),
                last_disconnected_at=utcnow_naive(),
                stream_source=USER_STREAM_FALLBACK_SOURCE,
            )
            session.add(settings_row)
            session.flush()
            stream_issues.append(
                {
                    "severity": "warning",
                    "reason_code": "USER_STREAM_POLL_FAILED",
                    "message": "Failed to poll the Binance futures user stream.",
                    "payload": {"error": str(exc)},
                }
            )
    user_stream_summary = get_user_stream_detail(settings_row)
    return {
        "user_stream_summary": user_stream_summary,
        "stream_health": str(user_stream_summary.get("status") or "idle"),
        "last_stream_event_time": user_stream_summary.get("last_event_at"),
        "stream_source": str(user_stream_summary.get("stream_source") or "rest_polling_fallback"),
        "stream_event_count": len(stream_events),
        "stream_events": stream_events,
        "stream_issues": stream_issues,
    }


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
    correlation_ids: dict[str, Any] | None = None,
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
        correlation_ids=correlation_ids,
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
        correlation_ids=correlation_ids,
    )
    record_health_event(
        session,
        component=component,
        status="error",
        message=alert_message,
        payload={"reason_code": reason_code, "symbol": symbol, "error": error},
        correlation_ids=correlation_ids,
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


def _create_live_account_snapshot(
    session: Session,
    settings_row: Setting,
    *,
    client: BinanceClient | None = None,
    account_info: dict[str, object] | None = None,
    component: str = "live_sync",
    event_type: str = "funding_ledger_sync_failed",
    symbol: str | None = None,
    correlation_ids: dict[str, object] | None = None,
) -> tuple[PnLSnapshot, dict[str, object]]:
    funding_summary: dict[str, object] = {
        "status": "not_requested" if client is None else "synced",
        "inserted_count": 0,
        "inserted_amount": 0.0,
        "last_occurred_at": None,
    }
    funding_entries: list[dict[str, object]] | None = None
    if client is not None and hasattr(client, "get_income_history"):
        try:
            funding_entries = fetch_incremental_funding_entries(session, client)
            ledger_summary = record_funding_ledger_entries(session, funding_entries)
            funding_summary = {
                "status": "synced",
                "fetched_count": len(funding_entries),
                "inserted_count": int(ledger_summary.get("inserted_count") or 0),
                "inserted_amount": _to_float(ledger_summary.get("inserted_amount")),
                "last_occurred_at": (
                    ledger_summary["last_occurred_at"].isoformat()
                    if isinstance(ledger_summary.get("last_occurred_at"), datetime)
                    else None
                ),
            }
        except Exception as exc:
            funding_summary = {
                "status": "warning",
                "reason_code": FUNDING_LEDGER_SYNC_REASON_CODE,
                "error": str(exc),
                "fetched_count": 0,
                "inserted_count": 0,
                "inserted_amount": 0.0,
                "last_occurred_at": None,
            }
            payload = {
                "reason_code": FUNDING_LEDGER_SYNC_REASON_CODE,
                "error": str(exc),
                **({"symbol": symbol} if symbol else {}),
            }
            record_health_event(
                session,
                component=component,
                status="warning",
                message="Funding ledger sync failed; keeping the prior funding total until the next successful sync.",
                payload=payload,
                correlation_ids=correlation_ids,
            )
            record_audit_event(
                session,
                event_type=event_type,
                entity_type="settings",
                entity_id=str(settings_row.id),
                severity="warning",
                message="Funding ledger sync failed; account snapshot used the existing funding ledger total.",
                payload=payload,
                correlation_ids=correlation_ids,
            )
    pnl_snapshot = create_exchange_pnl_snapshot(
        session,
        settings_row,
        account_info,
    )
    return pnl_snapshot, funding_summary


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


def _build_protection_state(
    position: Position | None,
    open_orders: list[dict[str, object]],
    *,
    position_mode: str = POSITION_MODE_ONE_WAY,
) -> dict[str, object]:
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
    relevant_orders = _filter_orders_for_position_context(
        open_orders,
        position_mode=position_mode,
        exchange_position_side=_position_metadata_side(position),
    )
    protective_orders = [item for item in relevant_orders if _is_protective_order(item)]
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
        "exchange_position_side": _position_metadata_side(position),
        "position_mode": position_mode,
    }


def _get_string_list(payload: dict[str, object], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item not in {None, ""}]


def _protection_lifecycle_payload(
    lifecycle: ProtectionLifecycleSnapshot | None,
) -> dict[str, object] | None:
    if lifecycle is None:
        return None
    return lifecycle.model_dump(mode="json")


def _initialize_protection_lifecycle(
    *,
    symbol: str,
    trigger_source: str,
    parent_order: Order | None,
) -> ProtectionLifecycleSnapshot:
    return ProtectionLifecycleSnapshot(
        symbol=symbol,
        trigger_source=trigger_source,
        parent_order_id=parent_order.id if parent_order is not None else None,
    )


def _sync_protection_lifecycle_snapshot(
    lifecycle: ProtectionLifecycleSnapshot | None,
    *,
    requested_components: list[str] | None = None,
    requested_order_types: list[str] | None = None,
    created_order_ids: list[int] | None = None,
    verification_detail: dict[str, object] | None = None,
) -> None:
    if lifecycle is None:
        return
    if requested_components is not None:
        lifecycle.requested_components = [str(item) for item in requested_components if item not in {None, ""}]
    if requested_order_types is not None:
        lifecycle.requested_order_types = [str(item) for item in requested_order_types if item not in {None, ""}]
    if created_order_ids is not None:
        merged_ids = [int(item) for item in lifecycle.created_order_ids]
        for item in created_order_ids:
            normalized = int(item)
            if normalized not in merged_ids:
                merged_ids.append(normalized)
        lifecycle.created_order_ids = merged_ids
    if verification_detail is not None:
        lifecycle.verification_detail = dict(verification_detail)


def _persist_protection_lifecycle(
    session: Session,
    parent_order: Order | None,
    lifecycle: ProtectionLifecycleSnapshot | None,
) -> None:
    if parent_order is None or lifecycle is None:
        return
    metadata = parent_order.metadata_json if isinstance(parent_order.metadata_json, dict) else {}
    parent_order.metadata_json = {
        **metadata,
        "protection_lifecycle": _protection_lifecycle_payload(lifecycle),
    }
    session.add(parent_order)
    session.flush()


def _transition_protection_lifecycle(
    session: Session,
    *,
    lifecycle: ProtectionLifecycleSnapshot | None,
    parent_order: Order | None,
    state: ProtectionLifecycleState,
    transition_reason: str,
    detail: dict[str, object] | None = None,
    requested_components: list[str] | None = None,
    requested_order_types: list[str] | None = None,
    created_order_ids: list[int] | None = None,
    verification_detail: dict[str, object] | None = None,
    correlation_ids: dict[str, Any] | None = None,
) -> None:
    if lifecycle is None:
        return
    transition_detail = dict(detail or {})
    _sync_protection_lifecycle_snapshot(
        lifecycle,
        requested_components=requested_components,
        requested_order_types=requested_order_types,
        created_order_ids=created_order_ids,
        verification_detail=verification_detail,
    )
    previous_state = lifecycle.state
    lifecycle.state = state
    lifecycle.transitions.append(
        ProtectionLifecycleTransition(
            from_state=previous_state,
            to_state=state,
            transition_reason=transition_reason,
            transitioned_at=utcnow_naive(),
            detail=transition_detail,
        )
    )
    _persist_protection_lifecycle(session, parent_order, lifecycle)
    record_audit_event(
        session,
        event_type="protection_lifecycle_transition",
        entity_type="order" if parent_order is not None else "position",
        entity_id=str(parent_order.id if parent_order is not None else lifecycle.symbol),
        severity="warning" if state == "verify_failed" else "info",
        message=f"Protection lifecycle transitioned to {state}.",
        payload={
            "symbol": lifecycle.symbol,
            "trigger_source": lifecycle.trigger_source,
            "from_state": previous_state,
            "to_state": state,
            "transition_reason": transition_reason,
            "detail": transition_detail,
            "protection_lifecycle": _protection_lifecycle_payload(lifecycle),
        },
        correlation_ids=correlation_ids,
    )


def _get_protection_verify_blocks(settings_row: Setting) -> dict[str, dict[str, object]]:
    recovery = get_protection_recovery_detail(settings_row)
    raw_blocks = recovery.get("verification_blocks")
    if not isinstance(raw_blocks, dict):
        return {}
    return {
        str(symbol): dict(detail)
        for symbol, detail in raw_blocks.items()
        if isinstance(detail, dict)
    }


def _get_symbol_protection_verify_block(settings_row: Setting, symbol: str) -> dict[str, object] | None:
    blocks = _get_protection_verify_blocks(settings_row)
    return blocks.get(symbol.upper()) or blocks.get(symbol)


def _write_protection_verify_blocks(
    session: Session,
    settings_row: Setting,
    *,
    blocks: dict[str, dict[str, object]],
) -> None:
    pause_detail = dict(settings_row.pause_reason_detail or {})
    recovery = dict(pause_detail.get("protection_recovery") or {})
    recovery["verification_blocks"] = blocks
    pause_detail["protection_recovery"] = recovery
    settings_row.pause_reason_detail = pause_detail
    session.add(settings_row)
    session.flush()


def _set_symbol_protection_verify_block(
    session: Session,
    settings_row: Setting,
    *,
    symbol: str,
    trigger_source: str,
    detail: str,
    protection_state: dict[str, object],
    created_order_ids: list[int],
    protection_lifecycle: ProtectionLifecycleSnapshot | None,
) -> None:
    normalized_symbol = symbol.upper()
    blocks = _get_protection_verify_blocks(settings_row)
    blocks[normalized_symbol] = {
        "status": "verify_failed",
        "blocked": True,
        "trigger_source": trigger_source,
        "blocked_at": utcnow_naive().isoformat(),
        "last_error": detail,
        "protection_state": dict(protection_state),
        "created_order_ids": [int(item) for item in created_order_ids],
        "protection_lifecycle": _protection_lifecycle_payload(protection_lifecycle),
    }
    _write_protection_verify_blocks(session, settings_row, blocks=blocks)


def _clear_symbol_protection_verify_block(
    session: Session,
    settings_row: Setting,
    *,
    symbol: str,
) -> None:
    normalized_symbol = symbol.upper()
    blocks = _get_protection_verify_blocks(settings_row)
    if normalized_symbol not in blocks and symbol not in blocks:
        return
    blocks.pop(normalized_symbol, None)
    blocks.pop(symbol, None)
    _write_protection_verify_blocks(session, settings_row, blocks=blocks)


def _merge_verified_protective_orders(
    open_orders: list[dict[str, object]],
    verified_orders: list[dict[str, object]],
) -> list[dict[str, object]]:
    merged = list(open_orders)
    existing_keys = {
        (str(item.get("orderId", "")), str(item.get("clientOrderId", "")))
        for item in merged
    }
    for payload in verified_orders:
        key = (str(payload.get("orderId", "")), str(payload.get("clientOrderId", "")))
        if key in existing_keys:
            continue
        merged.append(payload)
        existing_keys.add(key)
    return merged


def _protection_verify_status_is_active(status: object) -> bool:
    normalized = str(status or "").strip().upper()
    if not normalized:
        return True
    return normalized not in INACTIVE_PROTECTIVE_ORDER_STATUSES


def _verify_created_protective_orders(
    session: Session,
    client: BinanceClient,
    *,
    symbol: str,
    order_ids: list[int],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    verified_orders: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    for created_order_id in order_ids:
        row = session.get(Order, created_order_id)
        if row is None:
            failures.append(
                {
                    "order_id": created_order_id,
                    "error": "LOCAL_PROTECTIVE_ORDER_ROW_MISSING",
                }
            )
            continue
        verified_payload: dict[str, object] | None = None
        verification_error: str | None = None
        for attempt in range(1, PROTECTION_VERIFY_FETCH_ATTEMPTS + 1):
            try:
                payload = _fetch_exchange_order(
                    client,
                    symbol=symbol,
                    order_type=row.order_type,
                    order_id=row.external_order_id,
                    client_order_id=row.client_order_id,
                )
            except Exception as exc:
                verification_error = f"VERIFY_LOOKUP_FAILED:{exc}"
                continue

            if not _is_protective_order(payload):
                verification_error = "VERIFY_LOOKUP_RETURNED_NON_PROTECTIVE_ORDER"
                continue
            if str(payload.get("type", "")).upper() != row.order_type.upper():
                verification_error = (
                    f"VERIFY_LOOKUP_TYPE_MISMATCH:{row.order_type.upper()}:{str(payload.get('type', '')).upper()}"
                )
                continue
            if not _protection_verify_status_is_active(payload.get("status")):
                verification_error = f"VERIFY_LOOKUP_INACTIVE_STATUS:{str(payload.get('status', '')).upper()}"
                continue

            row = _upsert_exchange_order_row(
                session,
                symbol=symbol,
                requested_price=row.requested_price,
                requested_quantity=row.requested_quantity,
                order_type=row.order_type.upper(),
                side=row.side,
                exchange_order=payload,
                decision_run_id=row.decision_run_id,
                risk_row=session.get(RiskCheck, row.risk_check_id) if row.risk_check_id is not None else None,
                reduce_only=row.reduce_only,
                close_only=row.close_only,
                parent_order_id=row.parent_order_id,
            )
            verified_payload = payload
            break

        if verified_payload is None:
            failures.append(
                {
                    "order_id": created_order_id,
                    "external_order_id": row.external_order_id,
                    "client_order_id": row.client_order_id,
                    "order_type": row.order_type.upper(),
                    "error": verification_error or "VERIFY_LOOKUP_FAILED",
                }
            )
            continue
        verified_orders.append(verified_payload)
    return verified_orders, failures


def _format_protective_verification_failures(failures: list[dict[str, object]]) -> str:
    details = [
        f"{item.get('order_type', 'UNKNOWN')}:{item.get('error', 'VERIFY_LOOKUP_FAILED')}"
        for item in failures
    ]
    return "Protective order verify refetch failed: " + ", ".join(details)


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
    if hasattr(client, "fetch_order"):
        try:
            return client.fetch_order(  # type: ignore[attr-defined]
                symbol=symbol,
                order_type=order_type,
                order_id=order_id,
                client_order_id=client_order_id,
            )
        except TypeError:
            pass
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
    if hasattr(client, "cancel_exchange_order"):
        try:
            return client.cancel_exchange_order(  # type: ignore[attr-defined]
                symbol=symbol,
                order_type=effective_order_type,
                order_id=remote_order_id,
                client_order_id=remote_client_order_id,
            )
        except TypeError:
            pass
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


def _count_symbol_order_stream_events(stream_events: list[dict[str, Any]], *, symbol: str) -> int:
    order_keys: set[str] = set()
    fallback_count = 0
    for item in stream_events:
        related_categories = {
            str(category)
            for category in item.get("related_categories", [])
            if isinstance(category, str) and category
        }
        event_category = str(item.get("event_category") or "")
        if event_category not in {"order", "execution"} and not ({"order", "execution"} & related_categories):
            continue
        event_symbol = str(item.get("symbol") or "").upper()
        event_symbols = {
            str(value).upper()
            for value in item.get("symbols", [])
            if isinstance(value, str) and value
        }
        if symbol.upper() not in event_symbols and symbol.upper() != event_symbol:
            continue
        order_key = str(item.get("order_id") or item.get("client_order_id") or "")
        if order_key:
            order_keys.add(order_key)
        else:
            fallback_count += 1
    return len(order_keys) + fallback_count


def _record_user_stream_order_sync_fallback(
    session: Session,
    settings_row: Setting,
    *,
    symbol: str,
    reason_code: str,
) -> dict[str, Any]:
    user_stream_summary = get_user_stream_detail(settings_row)
    if str(user_stream_summary.get("status") or "") == "connected":
        set_user_stream_detail(
            settings_row,
            status="degraded",
            heartbeat_ok=False,
            stream_source=USER_STREAM_FALLBACK_SOURCE,
            last_error=reason_code,
            last_disconnected_at=utcnow_naive(),
        )
        session.add(settings_row)
        session.flush()
        user_stream_summary = get_user_stream_detail(settings_row)
    payload = {
        "symbol": symbol,
        "reason_code": reason_code,
        "user_stream_summary": user_stream_summary,
    }
    record_audit_event(
        session,
        event_type="user_stream_order_sync_fallback",
        entity_type="binance",
        entity_id=symbol,
        severity="warning",
        message="Live order synchronization fell back to REST reconciliation.",
        payload=payload,
    )
    record_health_event(
        session,
        component="user_stream",
        status="degraded",
        message="Live order synchronization fell back to REST reconciliation.",
        payload=payload,
    )
    return user_stream_summary


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

    client_order_id, exchange_order, submit_request, submission_tracking = _safe_submit_order(
        client,
        symbol=symbol,
        side=exit_side,
        order_type="STOP_MARKET",
        stop_price=stop_loss,
        close_position=True,
        response_type="ACK",
        reference_price=position.entry_price if position.entry_price > 0 else position.mark_price,
        enforce_min_notional=False,
    )
    normalized_stop = _to_float(submit_request.get("stop_price"), stop_loss)
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
    _apply_submission_tracking(
        order,
        client_order_id=client_order_id,
        submit_request=submit_request,
        submission_tracking=submission_tracking,
    )
    position.stop_loss = normalized_stop
    session.add(position)
    session.add(order)
    session.flush()
    _record_submission_recovery_event(
        session,
        order=order,
        symbol=symbol,
        submission_tracking=submission_tracking,
        context="position_management",
        requested_quantity=position.quantity,
        requested_price=normalized_stop,
    )
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
    updated_at: datetime | None = None,
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
        updated_at=updated_at,
    )
    existing_metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    submission_tracking = _as_object_dict(existing_metadata.get("submission_tracking"))
    if submission_tracking:
        recovered_via = str(submission_tracking.get("recovered_via") or "")
        submission_tracking = {
            **submission_tracking,
            "submission_state": "reconciled",
            "updated_at": (updated_at or utcnow_naive()).isoformat(),
        }
        if not recovered_via:
            submission_tracking["recovered_via"] = "exchange_sync"
        row.reason_codes = [code for code in row.reason_codes if code != UNKNOWN_SUBMISSION_REASON_CODE]
    row.metadata_json = {
        **existing_metadata,
        "exchange_order": exchange_order,
    }
    exchange_position_side = _exchange_order_position_side(exchange_order)
    if exchange_position_side is not None:
        row.metadata_json["exchange_position_side"] = exchange_position_side
        row.metadata_json["exchange_position_mode"] = (
            POSITION_MODE_HEDGE if exchange_position_side in {"LONG", "SHORT"} else POSITION_MODE_ONE_WAY
        )
    if submission_tracking:
        row.metadata_json["submission_tracking"] = submission_tracking
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
    client_order_id: str | None = None,
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
        client_order_id=client_order_id,
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


def _apply_submission_tracking(
    row: Order,
    *,
    client_order_id: str | None,
    submit_request: dict[str, Any],
    submission_tracking: dict[str, Any],
) -> None:
    metadata = _as_object_dict(row.metadata_json)
    row.client_order_id = client_order_id or row.client_order_id
    if str(submission_tracking.get("submission_state", "")).lower() != "submit_unknown":
        row.reason_codes = [code for code in row.reason_codes if code != UNKNOWN_SUBMISSION_REASON_CODE]
    row.metadata_json = {
        **metadata,
        "submit_request": dict(submit_request),
        "submission_tracking": dict(submission_tracking),
    }


def _create_submission_unknown_order_row(
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
    client_order_id: str,
    submit_request: dict[str, Any],
    submission_tracking: dict[str, Any],
    metadata_json: dict[str, object],
) -> Order:
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
            external_order_id=None,
            client_order_id=client_order_id,
            reduce_only=reduce_only,
            close_only=close_only,
            parent_order_id=None,
            exchange_status="SUBMIT_UNKNOWN",
            last_exchange_update_at=utcnow_naive(),
            requested_quantity=requested_quantity,
            requested_price=requested_price,
            filled_quantity=0.0,
            average_fill_price=0.0,
            reason_codes=[UNKNOWN_SUBMISSION_REASON_CODE],
            metadata_json={},
        )
    row.symbol = symbol
    row.decision_run_id = decision_run_id
    row.risk_check_id = risk_row.id if risk_row is not None else None
    row.side = side
    row.order_type = order_type.lower()
    row.mode = "live"
    row.status = "pending"
    row.exchange_status = "SUBMIT_UNKNOWN"
    row.last_exchange_update_at = utcnow_naive()
    row.requested_quantity = requested_quantity
    row.requested_price = requested_price
    row.reduce_only = reduce_only
    row.close_only = close_only
    row.reason_codes = [UNKNOWN_SUBMISSION_REASON_CODE]
    row.metadata_json = {
        **_as_object_dict(row.metadata_json),
        **metadata_json,
    }
    _apply_submission_tracking(
        row,
        client_order_id=client_order_id,
        submit_request=submit_request,
        submission_tracking=submission_tracking,
    )
    session.add(row)
    session.flush()
    return row


def _build_deterministic_client_order_id(
    *,
    seed: str | None,
    suffix: str,
) -> str | None:
    if not seed:
        return None
    digest = sha1(f"{seed}:{suffix}".encode("utf-8")).hexdigest()[:24]
    return f"mvp-{digest}"


def _is_order_not_found_error(exc: Exception) -> bool:
    if not isinstance(exc, BinanceAPIError):
        return False
    message = exc.api_message.lower()
    return exc.code in {-2013, -2011} or "unknown order" in message or "does not exist" in message or "not found" in message


def _is_duplicate_client_order_id_error(exc: Exception) -> bool:
    if not isinstance(exc, BinanceAPIError):
        return False
    message = exc.api_message.lower()
    if exc.code == -2010 and "duplicate" in message:
        return True
    return "duplicate" in message and "client" in message


def _annotate_submission_exception(
    exc: Exception,
    *,
    client_order_id: str,
    submit_request: dict[str, Any],
    submission_tracking: dict[str, Any],
) -> Exception:
    setattr(exc, "client_order_id", client_order_id)
    setattr(exc, "submit_request", dict(submit_request))
    setattr(exc, "submission_tracking", dict(submission_tracking))
    return exc


def _submit_exchange_order(
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
    client_order_id: str | None = None,
) -> dict[str, object]:
    if price is None and time_in_force is None:
        return client.new_order(
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
    if time_in_force is None:
        return client.new_order(
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
    return client.new_order(
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


def _reconcile_unknown_submission(
    client: BinanceClient,
    *,
    symbol: str,
    order_type: str,
    client_order_id: str,
) -> dict[str, object] | None:
    try:
        return _fetch_exchange_order(
            client,
            symbol=symbol,
            order_type=order_type,
            client_order_id=client_order_id,
        )
    except BinanceAPIError as exc:
        if _is_order_not_found_error(exc):
            return None
        raise


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
    client_order_id: str | None = None,
    reference_price: float | None = None,
    approved_notional: float | None = None,
    enforce_min_notional: bool = True,
) -> tuple[str, dict[str, object], dict[str, Any], dict[str, Any]]:
    submit_request = _normalize_submit_request(
        client,
        symbol=symbol,
        quantity=quantity,
        price=price,
        stop_price=stop_price,
        reference_price=reference_price,
        approved_notional=approved_notional,
        enforce_min_notional=enforce_min_notional,
        close_position=close_position,
    )
    quantity = submit_request.get("quantity")
    price = submit_request.get("price")
    stop_price = submit_request.get("stop_price")
    client_order_id = client_order_id or f"mvp-{uuid4().hex[:24]}"
    submit_attempt_count = 1
    safe_retry_used = False
    last_submit_error: str | None = None
    try:
        response = _submit_exchange_order(
            client,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            stop_price=stop_price,
            reduce_only=reduce_only,
            close_position=close_position,
            response_type=response_type,
            time_in_force=time_in_force,
            client_order_id=client_order_id,
        )
        submission_tracking = _build_submission_tracking(
            submission_state="reconciled",
            client_order_id=client_order_id,
            submit_attempt_count=submit_attempt_count,
            recovered_via="submit_ack",
        )
        return client_order_id, response, submit_request, submission_tracking
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        last_submit_error = _stringify_submit_error(exc)

    try:
        reconciled_response = _reconcile_unknown_submission(
            client,
            symbol=symbol,
            order_type=order_type,
            client_order_id=client_order_id,
        )
    except Exception as exc:
        raise OrderSubmissionUnknownError(
            client_order_id=client_order_id,
            submit_request=submit_request,
            submit_attempt_count=submit_attempt_count,
            last_submit_error=last_submit_error or _stringify_submit_error(exc),
        ) from exc
    if reconciled_response is not None:
        submission_tracking = _build_submission_tracking(
            submission_state="reconciled",
            client_order_id=client_order_id,
            submit_attempt_count=submit_attempt_count,
            last_submit_error=last_submit_error,
            recovered_via="client_order_id_lookup",
        )
        return client_order_id, reconciled_response, submit_request, submission_tracking

    safe_retry_used = True
    submit_attempt_count += 1
    try:
        retry_response = _submit_exchange_order(
            client,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            stop_price=stop_price,
            reduce_only=reduce_only,
            close_position=close_position,
            response_type=response_type,
            time_in_force=time_in_force,
            client_order_id=client_order_id,
        )
        submission_tracking = _build_submission_tracking(
            submission_state="reconciled",
            client_order_id=client_order_id,
            submit_attempt_count=submit_attempt_count,
            last_submit_error=last_submit_error,
            safe_retry_used=True,
            recovered_via="safe_retry_ack",
        )
        return client_order_id, retry_response, submit_request, submission_tracking
    except BinanceAPIError as exc:
        if _is_duplicate_client_order_id_error(exc):
            try:
                reconciled_response = _reconcile_unknown_submission(
                    client,
                    symbol=symbol,
                    order_type=order_type,
                    client_order_id=client_order_id,
                )
            except Exception as lookup_exc:
                raise OrderSubmissionUnknownError(
                    client_order_id=client_order_id,
                    submit_request=submit_request,
                    submit_attempt_count=submit_attempt_count,
                    last_submit_error=last_submit_error or _stringify_submit_error(lookup_exc),
                    safe_retry_used=True,
                ) from lookup_exc
            if reconciled_response is not None:
                submission_tracking = _build_submission_tracking(
                    submission_state="reconciled",
                    client_order_id=client_order_id,
                    submit_attempt_count=submit_attempt_count,
                    last_submit_error=last_submit_error,
                    safe_retry_used=True,
                    recovered_via="duplicate_client_order_id_lookup",
                )
                return client_order_id, reconciled_response, submit_request, submission_tracking
            raise OrderSubmissionUnknownError(
                client_order_id=client_order_id,
                submit_request=submit_request,
                submit_attempt_count=submit_attempt_count,
                last_submit_error=last_submit_error or _stringify_submit_error(exc),
                safe_retry_used=True,
            ) from exc
        submission_tracking = _build_submission_tracking(
            submission_state="failed",
            client_order_id=client_order_id,
            submit_attempt_count=submit_attempt_count,
            last_submit_error=last_submit_error or _stringify_submit_error(exc),
            safe_retry_used=True,
        )
        raise _annotate_submission_exception(
            exc,
            client_order_id=client_order_id,
            submit_request=submit_request,
            submission_tracking=submission_tracking,
        )
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        last_submit_error = _stringify_submit_error(exc)
        try:
            reconciled_response = _reconcile_unknown_submission(
                client,
                symbol=symbol,
                order_type=order_type,
                client_order_id=client_order_id,
            )
        except Exception as lookup_exc:
            raise OrderSubmissionUnknownError(
                client_order_id=client_order_id,
                submit_request=submit_request,
                submit_attempt_count=submit_attempt_count,
                last_submit_error=last_submit_error or _stringify_submit_error(lookup_exc),
                safe_retry_used=True,
            ) from lookup_exc
        if reconciled_response is not None:
            submission_tracking = _build_submission_tracking(
                submission_state="reconciled",
                client_order_id=client_order_id,
                submit_attempt_count=submit_attempt_count,
                last_submit_error=last_submit_error,
                safe_retry_used=True,
                recovered_via="post_retry_lookup",
            )
            return client_order_id, reconciled_response, submit_request, submission_tracking
        raise OrderSubmissionUnknownError(
            client_order_id=client_order_id,
            submit_request=submit_request,
            submit_attempt_count=submit_attempt_count,
            last_submit_error=last_submit_error,
            safe_retry_used=True,
        ) from exc


def _record_submission_recovery_event(
    session: Session,
    *,
    order: Order,
    symbol: str,
    submission_tracking: dict[str, Any],
    context: str,
    requested_quantity: float,
    requested_price: float,
    correlation_ids: dict[str, Any] | None = None,
) -> None:
    recovered_via = str(submission_tracking.get("recovered_via") or "")
    if not recovered_via or recovered_via == "submit_ack":
        return
    payload = {
        "symbol": symbol,
        "order_id": order.id,
        "client_order_id": order.client_order_id,
        "context": context,
        "requested_quantity": requested_quantity,
        "requested_price": requested_price,
        "submission_tracking": submission_tracking,
    }
    record_audit_event(
        session,
        event_type="live_order_submission_recovered",
        entity_type="order",
        entity_id=str(order.id),
        severity="warning",
        message="Live order submission required reconcile recovery before confirmation.",
        payload=payload,
        correlation_ids=correlation_ids,
    )
    record_health_event(
        session,
        component="live_execution",
        status="warning",
        message="Live order submission recovered after timeout/transport failure.",
        payload=payload,
        correlation_ids=correlation_ids,
    )


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


def _protective_prices(
    open_orders: list[dict[str, object]],
    existing: Position | None,
    *,
    position_mode: str = POSITION_MODE_ONE_WAY,
) -> tuple[float | None, float | None]:
    stop_loss = existing.stop_loss if existing is not None else None
    take_profit = existing.take_profit if existing is not None else None
    relevant_orders = _filter_orders_for_position_context(
        open_orders,
        position_mode=position_mode,
        exchange_position_side=_position_metadata_side(existing),
    )
    for item in relevant_orders:
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


def _remote_position_side_snapshot(
    remote_positions: list[dict[str, object]],
) -> tuple[list[dict[str, object]], list[str]]:
    active_positions: list[dict[str, object]] = []
    active_sides: list[str] = []
    for item in remote_positions:
        position_amount = _to_float(item.get("positionAmt"))
        if abs(position_amount) <= 1e-9:
            continue
        active_positions.append(dict(item))
        position_side = _normalize_exchange_position_side(item.get("positionSide")) or "BOTH"
        if position_side not in active_sides:
            active_sides.append(position_side)
    return active_positions, active_sides


def _symbol_order_position_sides(open_orders: list[dict[str, object]]) -> list[str]:
    sides: list[str] = []
    for item in open_orders:
        position_side = _exchange_order_position_side(item) or "BOTH"
        if position_side not in sides:
            sides.append(position_side)
    return sides


def _resolve_remote_position_mapping(
    remote_positions: list[dict[str, object]],
) -> dict[str, object]:
    active_positions, remote_position_sides = _remote_position_side_snapshot(remote_positions)
    if not active_positions:
        return {
            "status": "flat",
            "active_remote_count": 0,
            "remote_position_sides": remote_position_sides,
            "ambiguous": False,
            "active_remote": None,
            "mapped_side": None,
            "exchange_position_side": None,
        }
    if len(active_positions) != 1:
        return {
            "status": "ambiguous",
            "active_remote_count": len(active_positions),
            "remote_position_sides": remote_position_sides,
            "ambiguous": True,
            "active_remote": None,
            "mapped_side": None,
            "exchange_position_side": None,
        }
    active_remote = dict(active_positions[0])
    position_amount = _to_float(active_remote.get("positionAmt"))
    exchange_position_side = _normalize_exchange_position_side(active_remote.get("positionSide")) or "BOTH"
    if exchange_position_side == "LONG":
        mapped_side = "long"
    elif exchange_position_side == "SHORT":
        mapped_side = "short"
    else:
        mapped_side = "long" if position_amount > 0 else "short"
    return {
        "status": "open",
        "active_remote_count": 1,
        "remote_position_sides": remote_position_sides,
        "ambiguous": False,
        "active_remote": active_remote,
        "mapped_side": mapped_side,
        "exchange_position_side": exchange_position_side,
    }


def sync_live_positions(
    session: Session,
    settings_row: Setting,
    *,
    symbol: str,
    client: BinanceClient | None = None,
    open_orders: list[dict[str, object]] | None = None,
    position_mode: str = POSITION_MODE_ONE_WAY,
) -> dict[str, object]:
    client = client or _build_client(settings_row)
    open_orders = open_orders if open_orders is not None else client.get_open_orders(symbol)
    remote_positions = client.get_position_information(symbol)
    mapping = _resolve_remote_position_mapping(remote_positions)
    local = get_open_position(session, symbol)
    order_position_sides = _symbol_order_position_sides(open_orders)
    position_side_conflict = False
    position_mode_reason_code = _position_mode_guard_reason_code(position_mode)
    if position_mode == POSITION_MODE_ONE_WAY:
        if any(side in {"LONG", "SHORT"} for side in mapping.get("remote_position_sides", [])):
            position_side_conflict = True
        if any(side in {"LONG", "SHORT"} for side in order_position_sides):
            position_side_conflict = True

    if mapping["status"] == "flat":
        if local is not None:
            local.status = "closed"
            local.quantity = 0.0
            local.closed_at = utcnow_naive()
            metadata = local.metadata_json if isinstance(local.metadata_json, dict) else {}
            metadata["exchange_position_mode"] = position_mode
            metadata["exchange_position_side"] = "BOTH" if position_mode == POSITION_MODE_ONE_WAY else None
            local.metadata_json = metadata
            session.add(local)
            session.flush()
        _record_sync_success(
            session,
            settings_row,
            scope="positions",
            detail={
                "symbol": symbol,
                "position_status": "flat",
                "position_mode": position_mode,
                "remote_position_sides": mapping.get("remote_position_sides", []),
                "open_order_position_sides": order_position_sides,
            },
        )
        return {
            "symbol": symbol,
            "status": "flat",
            "position_mode": position_mode,
            "remote_position_sides": mapping.get("remote_position_sides", []),
            "open_order_position_sides": order_position_sides,
            "position_side_conflict": position_side_conflict,
        }

    if bool(mapping.get("ambiguous")) or position_side_conflict:
        reason_code = POSITION_MODE_MISMATCH_REASON_CODE
        detail = {
            "symbol": symbol,
            "position_mode": position_mode,
            "remote_position_sides": mapping.get("remote_position_sides", []),
            "open_order_position_sides": order_position_sides,
            "active_remote_count": mapping.get("active_remote_count"),
        }
        _record_sync_issue(
            session,
            settings_row,
            scope="positions",
            status="incomplete",
            reason_code=reason_code,
            detail=detail,
        )
        return {
            "symbol": symbol,
            "status": "unmapped",
            "position_mode": position_mode,
            "guard_reason_code": reason_code,
            "remote_position_sides": mapping.get("remote_position_sides", []),
            "open_order_position_sides": order_position_sides,
            "position_side_conflict": True,
        }

    active_remote = dict(mapping.get("active_remote") or {})
    position_amount = _to_float(active_remote.get("positionAmt"))
    entry_price = _to_float(active_remote.get("entryPrice"))
    mark_price = _to_float(active_remote.get("markPrice"), entry_price)
    leverage = _to_float(active_remote.get("leverage"), 1.0)
    quantity = abs(position_amount)
    side = str(mapping.get("mapped_side") or ("long" if position_amount > 0 else "short"))
    exchange_position_side = str(mapping.get("exchange_position_side") or "BOTH")
    stop_loss, take_profit = _protective_prices(
        open_orders,
        local,
        position_mode=position_mode,
    )
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
            metadata_json={
                "origin": "binance_sync",
                "exchange_position_side": exchange_position_side,
                "exchange_position_mode": position_mode,
            },
        )
        session.add(local)
        session.flush()
    else:
        metadata = local.metadata_json if isinstance(local.metadata_json, dict) else {}
        if "origin" not in metadata:
            metadata["origin"] = "binance_sync"
        metadata["exchange_position_side"] = exchange_position_side
        metadata["exchange_position_mode"] = position_mode
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
        detail={
            "symbol": symbol,
            "position_status": "open",
            "side": side,
            "position_mode": position_mode,
            "exchange_position_side": exchange_position_side,
            "remote_position_sides": mapping.get("remote_position_sides", []),
            "open_order_position_sides": order_position_sides,
            "position_mode_guard_reason_code": position_mode_reason_code,
        },
    )
    return {
        "symbol": symbol,
        "status": "open",
        "position_id": local.id,
        "quantity": local.quantity,
        "side": local.side,
        "position_mode": position_mode,
        "exchange_position_side": exchange_position_side,
        "remote_position_sides": mapping.get("remote_position_sides", []),
        "open_order_position_sides": order_position_sides,
        "position_side_conflict": False,
        "guard_reason_code": position_mode_reason_code,
    }


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
    approved_notional_cap: float | None = None,
    client_order_id_seed: str | None = None,
    correlation_ids: dict[str, Any] | None = None,
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
        approved_notional_remaining = None
        if approved_notional_cap is not None and approved_notional_cap > 0:
            approved_notional_remaining = max(approved_notional_cap - total_fill_notional, 0.0)
        client_order_id, exchange_order, submit_request, submission_tracking = _safe_submit_order(
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
            client_order_id=_build_deterministic_client_order_id(
                seed=client_order_id_seed,
                suffix=f"primary-{attempt_index + 1}-{current_order_type.lower()}",
            ),
            reference_price=submit_price if submit_price is not None else requested_price,
            approved_notional=approved_notional_remaining,
            enforce_min_notional=not close_only,
        )
        submitted_quantity = _to_float(submit_request.get("quantity"), current_quantity)
        submitted_price = _to_float(
            submit_request.get("price"),
            submit_price if submit_price is not None else requested_price,
        )
        parent_order_id = root_order.id if root_order is not None else None
        order = _upsert_exchange_order_row(
            session,
            symbol=symbol,
            requested_price=submitted_price,
            requested_quantity=submitted_quantity,
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
        _apply_submission_tracking(
            order,
            client_order_id=client_order_id,
            submit_request=submit_request,
            submission_tracking=submission_tracking,
        )
        session.add(order)
        session.flush()
        _record_submission_recovery_event(
            session,
            order=order,
            symbol=symbol,
            submission_tracking=submission_tracking,
            context=intent_type,
            requested_quantity=submitted_quantity,
            requested_price=submitted_price,
            correlation_ids=normalize_correlation_ids(correlation_ids, execution_id=order.id),
        )
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
                    requested_price=submitted_price,
                    requested_quantity=submitted_quantity,
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
                        "requested_quantity": submitted_quantity,
                        "requested_price": submitted_price,
                        "execution_policy": execution_plan.to_payload(),
                    },
                )

        trades = client.get_account_trades(symbol=symbol, order_id=order.external_order_id)
        fee_paid, realized_pnl = _record_live_trades(session, order, trades)
        filled_quantity = min(_sum_trade_quantity(trades), submitted_quantity)
        average_fill_price = order.average_fill_price or submitted_price
        fill_slippage_pct = 0.0
        if filled_quantity > 0 and average_fill_price > 0:
            fill_slippage_pct = abs(average_fill_price - requested_price) / max(requested_price, 1.0)
        if filled_quantity > 0:
            total_fee_paid += fee_paid
            total_realized_pnl += realized_pnl
            total_filled_quantity += filled_quantity
            total_fill_notional += filled_quantity * max(average_fill_price, 0.0)
            if filled_quantity < submitted_quantity:
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
                        "remaining_quantity": max(submitted_quantity - filled_quantity, 0.0),
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
                "requested_quantity": submitted_quantity,
                "requested_price": submitted_price,
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
                requested_price=submitted_price,
                requested_quantity=submitted_quantity,
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
            fallback_price=submitted_price,
        )
        current_slippage_pct = abs(live_reference_price - max(submitted_price, 1.0)) / max(live_reference_price, 1.0)
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
            current_price=max(submitted_price, 1.0),
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
    protection_lifecycle: ProtectionLifecycleSnapshot | None = None,
    client_order_id_seed: str | None = None,
    correlation_ids: dict[str, Any] | None = None,
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
    requested_order_types = [order_type for order_type, _ in requested_orders]
    if requested_orders:
        if protection_lifecycle is not None and protection_lifecycle.state == "none":
            _transition_protection_lifecycle(
                session,
                lifecycle=protection_lifecycle,
                parent_order=parent_order,
                state="requested",
                transition_reason="protective_orders_requested",
                detail={
                    "missing_components": missing_components,
                    "requested_order_types": requested_order_types,
                },
                requested_components=missing_components,
                requested_order_types=requested_order_types,
                correlation_ids=correlation_ids,
            )
        else:
            _sync_protection_lifecycle_snapshot(
                protection_lifecycle,
                requested_components=missing_components,
                requested_order_types=requested_order_types,
            )
            _persist_protection_lifecycle(session, parent_order, protection_lifecycle)
    for order_type, stop_price in requested_orders:
        client_order_id, exchange_order, submit_request, submission_tracking = _safe_submit_order(
            client,
            symbol=symbol,
            side=exit_side,
            order_type=order_type,
            stop_price=stop_price,
            close_position=True,
            response_type="ACK",
            client_order_id=_build_deterministic_client_order_id(
                seed=client_order_id_seed,
                suffix=f"protective-{order_type.lower()}",
            ),
            reference_price=position.entry_price if position.entry_price > 0 else position.mark_price,
            enforce_min_notional=False,
        )
        normalized_stop_price = _to_float(submit_request.get("stop_price"), stop_price)
        row = _upsert_exchange_order_row(
            session,
            symbol=symbol,
            requested_price=normalized_stop_price,
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
        _apply_submission_tracking(
            row,
            client_order_id=client_order_id,
            submit_request=submit_request,
            submission_tracking=submission_tracking,
        )
        session.add(row)
        session.flush()
        _record_submission_recovery_event(
            session,
            order=row,
            symbol=symbol,
            submission_tracking=submission_tracking,
            context="protective_order",
            requested_quantity=position.quantity,
            requested_price=normalized_stop_price,
            correlation_ids=normalize_correlation_ids(correlation_ids, execution_id=row.id),
        )
        created_ids.append(row.id)
    if created_ids:
        if protection_lifecycle is not None and protection_lifecycle.state != "placed":
            _transition_protection_lifecycle(
                session,
                lifecycle=protection_lifecycle,
                parent_order=parent_order,
                state="placed",
                transition_reason="protective_orders_placed",
                detail={
                    "created_order_ids": created_ids,
                    "requested_order_types": requested_order_types,
                },
                created_order_ids=created_ids,
                correlation_ids=correlation_ids,
            )
        else:
            _sync_protection_lifecycle_snapshot(
                protection_lifecycle,
                created_order_ids=created_ids,
            )
            _persist_protection_lifecycle(session, parent_order, protection_lifecycle)
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
    correlation_ids: dict[str, Any] | None = None,
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
        correlation_ids=correlation_ids,
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
        correlation_ids=correlation_ids,
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
    correlation_ids: dict[str, Any] | None = None,
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
        correlation_ids=correlation_ids,
    )

    try:
        client_order_id, exchange_order, submit_request, submission_tracking = _safe_submit_order(
            client,
            symbol=symbol,
            side=side,
            order_type="MARKET",
            quantity=quantity,
            reduce_only=True,
            response_type="RESULT",
            reference_price=reference_price,
        )
        order = _upsert_exchange_order_row(
            session,
            symbol=symbol,
            requested_price=_to_float(submit_request.get("price"), reference_price),
            requested_quantity=_to_float(submit_request.get("quantity"), quantity),
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
        _apply_submission_tracking(
            order,
            client_order_id=client_order_id,
            submit_request=submit_request,
            submission_tracking=submission_tracking,
        )
        session.add(order)
        session.flush()
        _record_submission_recovery_event(
            session,
            order=order,
            symbol=symbol,
            submission_tracking=submission_tracking,
            context="emergency_exit",
            requested_quantity=_to_float(submit_request.get("quantity"), quantity),
            requested_price=_to_float(submit_request.get("price"), reference_price),
            correlation_ids=normalize_correlation_ids(correlation_ids, execution_id=order.id),
        )
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
            correlation_ids=correlation_ids,
        )
        return {
            "status": "completed",
            "order_id": order.id,
            "fill_quantity": order.filled_quantity,
            "fees": fee_paid,
            "realized_pnl": realized_pnl,
            "remaining_position": remaining_position.quantity if remaining_position is not None else 0.0,
        }
    except OrderSubmissionUnknownError as exc:
        order = _create_submission_unknown_order_row(
            session,
            symbol=symbol,
            side="exit",
            order_type="MARKET",
            requested_quantity=quantity,
            requested_price=reference_price,
            decision_run_id=None,
            risk_row=None,
            reduce_only=True,
            close_only=True,
            client_order_id=exc.client_order_id,
            submit_request=exc.submit_request,
            submission_tracking=exc.submission_tracking,
            metadata_json={
                "error": str(exc),
                "reason": reason,
                "protective_state": protection_state,
                "emergency_exit": True,
            },
        )
        payload = {
            **trigger_payload,
            "order_id": order.id,
            "client_order_id": exc.client_order_id,
            "submission_tracking": exc.submission_tracking,
        }
        record_audit_event(
            session,
            event_type="emergency_exit_submission_unknown",
            entity_type="order",
            entity_id=str(order.id),
            severity="critical",
            message="Emergency exit submission timed out and now requires reconciliation.",
            payload=payload,
            correlation_ids=normalize_correlation_ids(correlation_ids, execution_id=order.id),
        )
        create_alert(
            session,
            category="execution",
            severity="critical",
            title="Emergency exit submission unknown",
            message="Emergency exit timed out and must be reconciled before retry.",
            payload=payload,
        )
        record_health_event(
            session,
            component="live_execution",
            status="critical",
            message="Emergency exit submission is waiting for reconciliation after timeout/transport failure.",
            payload=payload,
            correlation_ids=normalize_correlation_ids(correlation_ids, execution_id=order.id),
        )
        session.flush()
        return {
            "status": "submission_unknown",
            "order_id": order.id,
            "client_order_id": exc.client_order_id,
            "submission_tracking": exc.submission_tracking,
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
            correlation_ids=correlation_ids,
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
            correlation_ids=correlation_ids,
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
    protection_lifecycle: ProtectionLifecycleSnapshot | None = None,
    client_order_id_seed: str | None = None,
    correlation_ids: dict[str, Any] | None = None,
) -> dict[str, object]:
    protection_correlation_ids = normalize_correlation_ids(
        correlation_ids,
        decision_id=decision_run_id,
        risk_id=risk_row.id if risk_row is not None else None,
        execution_id=parent_order.id if parent_order is not None else None,
    )
    open_orders = client.get_open_orders(symbol)
    protection_state = _build_protection_state(position, open_orders)
    if protection_state["status"] == "protected":
        _clear_symbol_protection_verify_block(
            session,
            settings_row,
            symbol=symbol,
        )
        _transition_protection_lifecycle(
            session,
            lifecycle=protection_lifecycle,
            parent_order=parent_order,
            state="verified",
            transition_reason="exchange_protection_verified",
            detail={"protection_state": protection_state},
            verification_detail={"protection_state": protection_state},
            correlation_ids=protection_correlation_ids,
        )
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
            correlation_ids=protection_correlation_ids,
        )
        return {
            "status": "protected",
            "protection_state": protection_state,
            "created_order_ids": [],
            "emergency_action": None,
            "protection_lifecycle": _protection_lifecycle_payload(protection_lifecycle),
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
        correlation_ids=protection_correlation_ids,
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
        correlation_ids=protection_correlation_ids,
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
                correlation_ids=protection_correlation_ids,
            )
            try:
                attempt_created_order_ids = _create_protective_orders(
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
                    protection_lifecycle=protection_lifecycle,
                    client_order_id_seed=client_order_id_seed,
                    correlation_ids=protection_correlation_ids,
                )
                created_order_ids.extend(attempt_created_order_ids)
                verified_orders, verification_failures = _verify_created_protective_orders(
                    session,
                    client,
                    symbol=symbol,
                    order_ids=attempt_created_order_ids,
                )
                open_orders = client.get_open_orders(symbol)
                open_orders = _merge_verified_protective_orders(open_orders, verified_orders)
                _cancel_duplicate_protective_orders(
                    session,
                    client,
                    symbol=symbol,
                    open_orders=open_orders,
                    preferred_order_ids=created_order_ids,
                )
                open_orders = client.get_open_orders(symbol)
                open_orders = _merge_verified_protective_orders(open_orders, verified_orders)
                protection_state = _build_protection_state(position, open_orders)
                if not verification_failures and protection_state["status"] == "protected":
                    _clear_symbol_protection_verify_block(
                        session,
                        settings_row,
                        symbol=symbol,
                    )
                    _transition_protection_lifecycle(
                        session,
                        lifecycle=protection_lifecycle,
                        parent_order=parent_order,
                        state="verified",
                        transition_reason="recreated_protection_verified",
                        detail={
                            "created_order_ids": created_order_ids,
                            "protection_state": protection_state,
                        },
                        created_order_ids=created_order_ids,
                        verification_detail={
                            "protection_state": protection_state,
                            "created_order_ids": created_order_ids,
                        },
                        correlation_ids=protection_correlation_ids,
                    )
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
                        correlation_ids=protection_correlation_ids,
                    )
                    return {
                        "status": "protected_recreated",
                        "protection_state": protection_state,
                        "created_order_ids": created_order_ids,
                        "emergency_action": None,
                        "protection_lifecycle": _protection_lifecycle_payload(protection_lifecycle),
                    }
                recreate_error = _format_protective_verification_failures(verification_failures)
            except Exception as exc:
                recreate_error = str(exc)
        if recreate_error is None:
            recreate_error = "Protective orders were not verified on the exchange."
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
            correlation_ids=protection_correlation_ids,
        )
    else:
        recreate_error = "Local stop loss / take profit template was unavailable."

    _transition_protection_lifecycle(
        session,
        lifecycle=protection_lifecycle,
        parent_order=parent_order,
        state="verify_failed",
        transition_reason="protective_verification_failed",
        detail={
            "error": recreate_error,
            "missing_components": protection_state.get("missing_components", []),
            "protection_state": protection_state,
        },
        created_order_ids=created_order_ids,
        verification_detail={
            "error": recreate_error,
            "protection_state": protection_state,
        },
        correlation_ids=protection_correlation_ids,
    )
    _set_symbol_protection_verify_block(
        session,
        settings_row,
        symbol=symbol,
        trigger_source=trigger_source,
        detail=recreate_error,
        protection_state=protection_state,
        created_order_ids=created_order_ids,
        protection_lifecycle=protection_lifecycle,
    )

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
        correlation_ids=protection_correlation_ids,
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
        correlation_ids=protection_correlation_ids,
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
        "protection_lifecycle": _protection_lifecycle_payload(protection_lifecycle),
    }


def sync_live_state(session: Session, settings_row: Setting, *, symbol: str | None = None) -> dict[str, object]:
    client = _build_client(settings_row)
    symbols = _resolve_sync_symbols(settings_row, symbol)
    stream_poll = poll_live_user_stream(
        session,
        settings_row,
        client=client,
        max_events=max(4, len(symbols) * 4),
        idle_timeout_seconds=0.1,
    )
    stream_events = [dict(item) for item in stream_poll.get("stream_events", []) if isinstance(item, dict)]
    user_stream_summary = (
        dict(stream_poll.get("user_stream_summary"))
        if isinstance(stream_poll.get("user_stream_summary"), dict)
        else get_user_stream_detail(settings_row)
    )
    replace_user_stream_detail(settings_row, build_user_stream_state(user_stream_summary))
    session.add(settings_row)
    session.flush()
    user_stream_summary = get_user_stream_detail(settings_row)
    reconcile_started_at = utcnow_naive()
    position_mode = POSITION_MODE_ONE_WAY
    position_mode_source = "assumed_default"
    position_mode_lookup_error: str | None = None
    if hasattr(client, "get_position_mode"):
        try:
            position_mode_payload = client.get_position_mode()  # type: ignore[attr-defined]
            position_mode = _normalize_position_mode(position_mode_payload.get("mode"))
            position_mode_source = "exchange"
        except Exception as exc:
            position_mode = POSITION_MODE_UNKNOWN
            position_mode_source = "exchange_error"
            position_mode_lookup_error = str(exc)
    mode_guard_reason_code = _position_mode_guard_reason_code(position_mode)
    stream_fallback_active = str(user_stream_summary.get("status") or "") != "connected"
    reconcile_source = "rest_polling_fallback" if stream_fallback_active else "user_stream_primary"
    set_reconciliation_detail(
        settings_row,
        status="running",
        source="rest_polling_reconciliation",
        last_reconciled_at=reconcile_started_at,
        last_symbol=symbols[0] if len(symbols) == 1 else None,
        stream_fallback_active=stream_fallback_active,
        reconcile_source=reconcile_source,
        position_mode=position_mode,
        position_mode_source=position_mode_source,
        position_mode_checked_at=reconcile_started_at,
        enabled_symbols=symbols,
    )
    session.add(settings_row)
    session.flush()
    synced_orders = 0
    synced_positions = 0
    symbol_protection_state: dict[str, dict[str, object]] = {}
    symbol_states: dict[str, dict[str, object]] = {}
    unprotected_positions: list[str] = []
    emergency_actions_taken: list[dict[str, object]] = []
    guarded_symbols = list(symbols) if mode_guard_reason_code is not None else []
    for item_symbol in symbols:
        live_orders = list(
            session.scalars(
                select(Order)
                .where(Order.mode == "live", Order.symbol == item_symbol, Order.status.notin_(FINAL_ORDER_STATUSES))
            )
        )
        use_rest_order_fallback, fallback_reason = should_use_rest_order_reconciliation(
            settings_row,
            active_order_count=len(live_orders),
            now=reconcile_started_at,
        )
        if use_rest_order_fallback and live_orders:
            stream_fallback_active = True
            reconcile_source = "rest_polling_fallback"
            user_stream_summary = _record_user_stream_order_sync_fallback(
                session,
                settings_row,
                symbol=item_symbol,
                reason_code=fallback_reason,
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
        else:
            synced_orders += _count_symbol_order_stream_events(stream_events, symbol=item_symbol)
        try:
            open_orders = client.get_open_orders(item_symbol)
            _record_sync_success(
                session,
                settings_row,
                scope="open_orders",
                detail={
                    "symbol": item_symbol,
                    "open_order_count": len(open_orders),
                    "position_mode": position_mode,
                },
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
            synced_position = sync_live_positions(
                session,
                settings_row,
                symbol=item_symbol,
                client=client,
                open_orders=open_orders,
                position_mode=position_mode,
            )
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
        symbol_guard_reason_code = str(synced_position.get("guard_reason_code") or "") or None
        symbol_guard_active = bool(mode_guard_reason_code or symbol_guard_reason_code)
        if symbol_guard_reason_code and item_symbol not in guarded_symbols:
            guarded_symbols.append(item_symbol)
        if symbol_guard_active and position is not None and position.status == "open":
            protection_state = {
                "status": "unverified",
                "protected": False,
                "has_stop_loss": False,
                "has_take_profit": False,
                "protective_order_count": 0,
                "protective_order_ids": [],
                "missing_components": ["stop_loss", "take_profit"],
                "position_mode": position_mode,
                "exchange_position_side": _position_metadata_side(position),
                "reason_code": mode_guard_reason_code or symbol_guard_reason_code,
            }
            _record_sync_issue(
                session,
                settings_row,
                scope="protective_orders",
                status="incomplete",
                reason_code="PROTECTION_STATE_UNVERIFIED",
                detail={
                    "symbol": item_symbol,
                    "position_mode": position_mode,
                    "guard_reason_code": mode_guard_reason_code or symbol_guard_reason_code,
                    "remote_position_sides": synced_position.get("remote_position_sides", []),
                    "open_order_position_sides": synced_position.get("open_order_position_sides", []),
                },
            )
        else:
            protection_state = _build_protection_state(
                position,
                open_orders,
                position_mode=position_mode,
            )
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
        elif not symbol_guard_active:
            _record_sync_success(
                session,
                settings_row,
                scope="protective_orders",
                detail={"symbol": item_symbol, "status": protection_state["status"]},
            )
            if protection_state["status"] == "protected":
                _clear_symbol_protection_verify_block(
                    session,
                    settings_row,
                    symbol=item_symbol,
                )
            clear_symbol_protection_state(
                session,
                settings_row,
                symbol=item_symbol,
                trigger_source="sync_live_state:protected_or_flat",
            )
        symbol_states[item_symbol] = {
            "symbol": item_symbol,
            "position_mode": position_mode,
            "position_status": str(synced_position.get("status") or "unknown"),
            "exchange_position_side": synced_position.get("exchange_position_side"),
            "remote_position_sides": list(synced_position.get("remote_position_sides") or []),
            "open_order_position_sides": list(synced_position.get("open_order_position_sides") or []),
            "open_order_count": len(open_orders),
            "protection_status": str(symbol_protection_state[item_symbol].get("status") or "unknown"),
            "guard_active": symbol_guard_active,
            "guard_reason_code": mode_guard_reason_code or symbol_guard_reason_code,
            "position_side_conflict": bool(synced_position.get("position_side_conflict", False)),
        }
        synced_positions += 1
    latest_prices = {
        item_symbol: position.mark_price
        for item_symbol in symbols
        if (position := get_open_position(session, item_symbol)) is not None
    }
    if latest_prices:
        refresh_open_position_marks(session, latest_prices)
    account_symbol = symbols[0] if symbols else settings_row.default_symbol.upper()
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
            detail={"symbol": account_symbol},
        )
        _pause_for_system_issue(
            session,
            settings_row,
            reason_code=reason_code,
            symbol=account_symbol,
            error=str(exc),
            event_type="live_account_sync_failed",
            component="live_sync",
            alert_title="Live account state unavailable",
            alert_message="거래소 계좌 상태를 읽지 못해 거래를 일시 중지했습니다.",
        )
        raise RuntimeError(f"{reason_code}: {exc}") from exc
    pnl_snapshot, funding_sync = _create_live_account_snapshot(
        session,
        settings_row,
        client=client,
        account_info=account_info,
        component="live_sync",
        event_type="live_account_funding_sync_failed",
        symbol=account_symbol,
    )
    _record_sync_success(
        session,
        settings_row,
        scope="account",
        detail={
            "symbol": account_symbol,
            "equity": pnl_snapshot.equity,
            "wallet_balance": pnl_snapshot.wallet_balance,
            "available_balance": pnl_snapshot.available_balance,
            "funding_sync": funding_sync,
        },
    )
    reconciled_at = utcnow_naive()
    if position_mode_lookup_error and mode_guard_reason_code is None:
        mode_guard_reason_code = POSITION_MODE_UNCLEAR_REASON_CODE
        guarded_symbols = list(dict.fromkeys(symbols))
    resolved_mode_guard_reason_code = (
        mode_guard_reason_code
        if mode_guard_reason_code is not None
        else POSITION_MODE_MISMATCH_REASON_CODE
        if guarded_symbols
        else None
    )
    _record_position_mode_guard_transition(
        session,
        settings_row,
        reason_code=resolved_mode_guard_reason_code,
        guarded_symbols=list(dict.fromkeys(guarded_symbols)),
        position_mode=position_mode,
        position_mode_source=position_mode_source,
        detail={
            "enabled_symbols": symbols,
            "position_mode_lookup_error": position_mode_lookup_error,
            "symbol_states": symbol_states,
        },
    )
    set_reconciliation_detail(
        settings_row,
        status="synced",
        source="rest_polling_reconciliation",
        last_reconciled_at=reconciled_at,
        last_success_at=reconciled_at,
        last_error=position_mode_lookup_error or "",
        last_symbol=symbols[0] if len(symbols) == 1 else None,
        stream_fallback_active=stream_fallback_active,
        reconcile_source=reconcile_source,
        position_mode=position_mode,
        position_mode_source=position_mode_source,
        position_mode_checked_at=reconciled_at,
        mode_guard_active=bool(resolved_mode_guard_reason_code),
        mode_guard_reason_code=resolved_mode_guard_reason_code,
        mode_guard_message=_position_mode_guard_message(resolved_mode_guard_reason_code),
        enabled_symbols=symbols,
        guarded_symbols=list(dict.fromkeys(guarded_symbols)),
        symbol_states=symbol_states,
    )
    session.add(settings_row)
    session.flush()
    user_stream_summary = get_user_stream_detail(settings_row)
    reconciliation_summary = get_reconciliation_detail(settings_row)
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
        "user_stream_summary": user_stream_summary,
        "reconciliation_summary": reconciliation_summary,
        "stream_health": str(stream_poll.get("stream_health") or user_stream_summary.get("status") or "idle"),
        "last_stream_event_time": stream_poll.get("last_stream_event_time") or user_stream_summary.get("last_event_at"),
        "stream_source": str(stream_poll.get("stream_source") or user_stream_summary.get("stream_source") or "rest_polling_fallback"),
        "reconcile_source": str(reconciliation_summary.get("reconcile_source") or "rest_polling_fallback"),
        "stream_event_count": int(stream_poll.get("stream_event_count") or len(stream_events)),
        "stream_events": stream_events,
        "stream_issues": [dict(item) for item in stream_poll.get("stream_issues", []) if isinstance(item, dict)],
        "symbol_reconciliation": symbol_states,
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
    correlation_ids: dict[str, Any] | None = None,
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
            correlation_ids=correlation_ids,
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
            correlation_ids=correlation_ids,
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
        pnl_snapshot, funding_sync = _create_live_account_snapshot(
            session,
            settings_row,
            client=client,
            account_info=account_info,
            component="live_sync",
            event_type=f"{event_prefix}_funding_sync_failed",
            symbol=symbol,
        )
        _record_sync_success(
            session,
            settings_row,
            scope="account",
            detail={
                "symbol": symbol,
                "equity": pnl_snapshot.equity,
                "wallet_balance": pnl_snapshot.wallet_balance,
                "available_balance": pnl_snapshot.available_balance,
                "funding_sync": funding_sync,
            },
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
            correlation_ids=correlation_ids,
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
    cycle_id: str | None = None,
    snapshot_id: int | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    symbol = decision.symbol.upper()
    effective_cycle_id = cycle_id or risk_result.cycle_id or f"adhoc-{decision_run_id or 'manual'}-{uuid4().hex[:12]}"
    effective_snapshot_id = snapshot_id if snapshot_id is not None else risk_result.snapshot_id
    execution_correlation_ids = normalize_correlation_ids(
        cycle_id=effective_cycle_id,
        snapshot_id=effective_snapshot_id,
        decision_id=decision_run_id,
        risk_id=risk_row.id if risk_row is not None else None,
    )
    dedupe_key = idempotency_key or build_execution_dedupe_key(
        cycle_id=effective_cycle_id,
        symbol=symbol,
        action=decision.decision,
    )
    cached_record = get_execution_dedupe_record(settings_row, dedupe_key=dedupe_key)
    if cached_record is not None:
        cached_result = deepcopy(cached_record.get("result")) if isinstance(cached_record.get("result"), dict) else {}
        cached_response: dict[str, Any] = dict(cached_result)
        if "status" not in cached_response:
            cached_response["status"] = str(cached_record.get("status") or "deduplicated")
        cached_response.update(
            {
                "dedupe_suppressed": True,
                "dedupe_reason": "cycle_action_already_completed",
                "dedupe_key": dedupe_key,
                "cycle_id": effective_cycle_id,
                "snapshot_id": effective_snapshot_id,
            }
        )
        record_audit_event(
            session,
            event_type="live_execution_deduplicated",
            entity_type="decision_run",
            entity_id=str(decision_run_id or symbol),
            severity="info",
            message="Live execution duplicate was suppressed because the same cycle action already completed.",
            payload={
                "symbol": symbol,
                "action": decision.decision,
                "cycle_id": effective_cycle_id,
                "snapshot_id": effective_snapshot_id,
                "dedupe_key": dedupe_key,
                "duplicate_reason": "cycle_action_already_completed",
                "cached_status": cached_record.get("status"),
                "risk_check_id": risk_row.id if risk_row is not None else None,
            },
            correlation_ids=execution_correlation_ids,
        )
        session.flush()
        return cached_response

    active_lock: dict[str, object] | None = None
    lock_token: str | None = None
    with _ACTIVE_SYMBOL_EXECUTION_LOCKS_GUARD:
        active_lock = dict(_ACTIVE_SYMBOL_EXECUTION_LOCKS.get(symbol) or {})
        if not active_lock:
            lock_token = uuid4().hex
            active_lock = {
                "token": lock_token,
                "symbol": symbol,
                "dedupe_key": dedupe_key,
                "cycle_id": effective_cycle_id,
                "snapshot_id": effective_snapshot_id,
                "action": decision.decision,
                "locked_at": utcnow_naive().isoformat(),
            }
            _ACTIVE_SYMBOL_EXECUTION_LOCKS[symbol] = active_lock

    if lock_token is None:
        duplicate_reason = (
            "cycle_action_in_progress"
            if str(active_lock.get("dedupe_key") or "") == dedupe_key
            else "symbol_execution_in_progress"
        )
        record_audit_event(
            session,
            event_type="live_execution_deduplicated",
            entity_type="decision_run",
            entity_id=str(decision_run_id or symbol),
            severity="info",
            message="Live execution duplicate was suppressed because a symbol execution lock is already active.",
            payload={
                "symbol": symbol,
                "action": decision.decision,
                "cycle_id": effective_cycle_id,
                "snapshot_id": effective_snapshot_id,
                "dedupe_key": dedupe_key,
                "duplicate_reason": duplicate_reason,
                "active_lock": active_lock,
                "risk_check_id": risk_row.id if risk_row is not None else None,
            },
            correlation_ids=execution_correlation_ids,
        )
        session.flush()
        return {
            "status": "deduplicated",
            "reason_codes": [DUPLICATE_EXECUTION_SUPPRESSED_REASON_CODE],
            "dedupe_suppressed": True,
            "dedupe_reason": duplicate_reason,
            "dedupe_key": dedupe_key,
            "cycle_id": effective_cycle_id,
            "snapshot_id": effective_snapshot_id,
            "active_lock": active_lock,
        }

    mark_execution_lock(
        settings_row,
        symbol=symbol,
        lock_token=lock_token,
        dedupe_key=dedupe_key,
        cycle_id=effective_cycle_id,
        snapshot_id=effective_snapshot_id,
        action=decision.decision,
    )
    session.add(settings_row)
    session.flush()

    try:
        raw_result = _execute_live_trade_body(
            session,
            settings_row,
            decision_run_id=decision_run_id,
            decision=decision,
            market_snapshot=market_snapshot,
            risk_result=risk_result,
            risk_row=risk_row,
            client_order_id_seed=dedupe_key,
            correlation_ids=execution_correlation_ids,
        )
        cacheable_result = bool(raw_result.pop("_cache_dedupe", True))
        if cacheable_result:
            store_execution_dedupe_record(
                settings_row,
                dedupe_key=dedupe_key,
                symbol=symbol,
                cycle_id=effective_cycle_id,
                snapshot_id=effective_snapshot_id,
                action=decision.decision,
                status=str(raw_result.get("status") or "unknown"),
                result=deepcopy(raw_result),
            )
            session.add(settings_row)
            session.flush()
        response = dict(raw_result)
        response.update(
            {
                "dedupe_key": dedupe_key,
                "cycle_id": effective_cycle_id,
                "snapshot_id": effective_snapshot_id,
            }
        )
        return response
    finally:
        with _ACTIVE_SYMBOL_EXECUTION_LOCKS_GUARD:
            current_lock = _ACTIVE_SYMBOL_EXECUTION_LOCKS.get(symbol)
            if current_lock is not None and str(current_lock.get("token") or "") == lock_token:
                _ACTIVE_SYMBOL_EXECUTION_LOCKS.pop(symbol, None)
        clear_execution_lock(settings_row, symbol=symbol, lock_token=lock_token)
        session.add(settings_row)
        session.flush()


def _execute_live_trade_body(
    session: Session,
    settings_row: Setting,
    decision_run_id: int | None,
    decision: TradeDecision,
    market_snapshot: MarketSnapshotPayload,
    risk_result: RiskCheckResult,
    risk_row: RiskCheck | None = None,
    client_order_id_seed: str | None = None,
    correlation_ids: dict[str, Any] | None = None,
) -> dict[str, Any]:
    execution_correlation_ids = normalize_correlation_ids(
        correlation_ids,
        decision_id=decision_run_id,
        risk_id=risk_row.id if risk_row is not None else None,
    )
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
            correlation_ids=execution_correlation_ids,
        )
        session.flush()
        return {
            "status": "blocked",
            "reason_codes": list(risk_result.reason_codes),
            "decision": decision.decision,
        }

    rollout_mode = get_rollout_mode(settings_row)
    exchange_submit_allowed = rollout_mode_allows_exchange_submit(settings_row)
    limited_live_max_notional = (
        get_limited_live_max_notional(settings_row) if rollout_mode == "limited_live" else None
    )

    if decision.decision == "hold":
        return {"status": "skipped", "reason_codes": ["HOLD_DECISION"], "rollout_mode": rollout_mode}

    if rollout_mode == "shadow":
        latest_pnl = get_latest_pnl_snapshot(session, settings_row)
        existing_position = get_open_position(session, decision.symbol)
        operating_state = get_operating_state(settings_row)
        intent = build_execution_intent(
            decision,
            market_snapshot,
            risk_result,
            settings_row,
            latest_pnl.equity,
            existing_position=existing_position,
            operating_state=operating_state,
        )
        intent_type = intent.intent_type
        protection_verify_block = _get_symbol_protection_verify_block(settings_row, decision.symbol)
        if intent_type in PROTECTION_VERIFY_BLOCKING_INTENT_TYPES and protection_verify_block is not None:
            record_audit_event(
                session,
                event_type="live_execution_blocked",
                entity_type="decision_run",
                entity_id=str(decision_run_id),
                severity="warning",
                message="Live execution skipped because protective order verification previously failed for this symbol.",
                payload={
                    "symbol": decision.symbol,
                    "intent_type": intent_type,
                    "reason_code": PROTECTION_VERIFY_FAILED_REASON_CODE,
                    "protection_verify_block": protection_verify_block,
                    "rollout_mode": rollout_mode,
                },
                correlation_ids=execution_correlation_ids,
            )
            session.flush()
            return {
                "status": "blocked",
                "reason_codes": [PROTECTION_VERIFY_FAILED_REASON_CODE],
                "intent_type": intent_type,
                "protection_verify_block": protection_verify_block,
                "rollout_mode": rollout_mode,
            }
        execution_plan = select_execution_plan(
            intent,
            market_snapshot,
            settings_row,
            pre_trade_protection=_build_protection_state(existing_position, []),
        )
        record_audit_event(
            session,
            event_type="live_execution_attempted",
            entity_type="decision_run",
            entity_id=str(decision_run_id or decision.symbol),
            severity="info",
            message="Live execution attempt started.",
            payload={
                "symbol": decision.symbol,
                "decision": decision.decision,
                "intent_type": intent_type,
                "requested_quantity": intent.quantity,
                "requested_price": intent.requested_price,
                "execution_policy": execution_plan.to_payload(),
                "rollout_mode": rollout_mode,
                "exchange_submit_allowed": exchange_submit_allowed,
            },
            correlation_ids=execution_correlation_ids,
        )
        record_audit_event(
            session,
            event_type="live_execution_submit_skipped",
            entity_type="decision_run",
            entity_id=str(decision_run_id or decision.symbol),
            severity="info",
            message="Shadow rollout mode recorded the execution intent without submitting to Binance.",
            payload={
                "symbol": decision.symbol,
                "decision": decision.decision,
                "intent_type": intent_type,
                "reason_code": ROLLOUT_MODE_SHADOW_REASON_CODE,
                "requested_quantity": intent.quantity,
                "requested_price": intent.requested_price,
                "execution_policy": execution_plan.to_payload(),
                "rollout_mode": rollout_mode,
            },
            correlation_ids=execution_correlation_ids,
        )
        session.flush()
        return {
            "status": "shadow",
            "reason_codes": [ROLLOUT_MODE_SHADOW_REASON_CODE],
            "decision": decision.decision,
            "intent_type": intent_type,
            "rollout_mode": rollout_mode,
            "requested_quantity": intent.quantity,
            "requested_price": intent.requested_price,
            "execution_policy": execution_plan.to_payload(),
            "submit_blocked": True,
            "_cache_dedupe": False,
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
            correlation_ids=execution_correlation_ids,
        )
        record_audit_event(
            session,
            event_type="live_execution_skipped",
            entity_type="decision_run",
            entity_id=str(decision_run_id),
            severity="error",
            message="Live execution skipped because exchange account state was unavailable.",
            payload={"symbol": decision.symbol, "error": str(exc), "reason_code": reason_code},
            correlation_ids=execution_correlation_ids,
        )
        session.flush()
        return {"status": "error", "reason_codes": [reason_code], "error": str(exc)}

    latest_pnl, funding_sync = _create_live_account_snapshot(
        session,
        settings_row,
        client=client,
        account_info=account_info,
        component="live_execution",
        event_type="live_execution_funding_sync_failed",
        symbol=decision.symbol,
        correlation_ids=execution_correlation_ids,
    )
    session.refresh(latest_pnl)
    live_balances = _live_account_balances(account_info)

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
            correlation_ids=execution_correlation_ids,
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
            correlation_ids=execution_correlation_ids,
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
    protection_verify_block = _get_symbol_protection_verify_block(settings_row, decision.symbol)
    if intent_type in PROTECTION_VERIFY_BLOCKING_INTENT_TYPES and protection_verify_block is not None:
        record_audit_event(
            session,
            event_type="live_execution_blocked",
            entity_type="decision_run",
            entity_id=str(decision_run_id),
            severity="warning",
            message="Live execution skipped because protective order verification previously failed for this symbol.",
            payload={
                "symbol": decision.symbol,
                "intent_type": intent_type,
                "reason_code": PROTECTION_VERIFY_FAILED_REASON_CODE,
                "protection_verify_block": protection_verify_block,
            },
            correlation_ids=execution_correlation_ids,
        )
        session.flush()
        return {
            "status": "blocked",
            "reason_codes": [PROTECTION_VERIFY_FAILED_REASON_CODE],
            "intent_type": intent_type,
            "protection_verify_block": protection_verify_block,
        }
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
            client_order_id_seed=client_order_id_seed,
            correlation_ids=execution_correlation_ids,
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
            correlation_ids=execution_correlation_ids,
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

    approved_notional_cap = (
        risk_result.approved_projected_notional
        if intent_type in {"entry", "scale_in"} and risk_result.approved_projected_notional > 0
        else None
    )
    rollout_notional_cap_applied = False
    if intent_type in {"entry", "scale_in"} and limited_live_max_notional is not None:
        current_cap = approved_notional_cap if approved_notional_cap is not None and approved_notional_cap > 0 else (
            requested_quantity * max(intent.requested_price, 0.0)
        )
        if current_cap > 0:
            adjusted_cap = min(current_cap, limited_live_max_notional)
            rollout_notional_cap_applied = adjusted_cap < current_cap - 1e-9
            approved_notional_cap = adjusted_cap
    try:
        preflight_request = _normalize_submit_request(
            client,
            symbol=decision.symbol,
            quantity=requested_quantity,
            reference_price=intent.requested_price,
            approved_notional=approved_notional_cap,
            enforce_min_notional=not (decision.decision == "exit"),
            close_position=False,
        )
    except PreTradeExchangeFilterError as exc:
        record_audit_event(
            session,
            event_type="live_execution_blocked",
            entity_type="decision_run",
            entity_id=str(decision_run_id),
            severity="warning",
            message="Live execution skipped because the order failed exchange filters before submission.",
            payload={
                "symbol": decision.symbol,
                "reason_code": exc.reason_code,
                "intent_type": intent_type,
                "approved_projected_notional": risk_result.approved_projected_notional,
                "approved_quantity": risk_result.approved_quantity,
                "risk_check_id": risk_row.id if risk_row is not None else None,
                "risk_debug_payload": dict(risk_result.debug_payload),
                "exchange_filter_detail": exc.detail,
            },
            correlation_ids=execution_correlation_ids,
        )
        session.flush()
        return {
            "status": "blocked",
            "reason_codes": [exc.reason_code],
            "decision": decision.decision,
            "intent_type": intent_type,
            "exchange_filter_detail": exc.detail,
        }
    normalized_quantity = _to_float(preflight_request.get("quantity"), requested_quantity)
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
    record_audit_event(
        session,
        event_type="live_execution_attempted",
        entity_type="decision_run",
        entity_id=str(decision_run_id or decision.symbol),
        severity="info",
        message="Live execution attempt started.",
            payload={
                "symbol": decision.symbol,
                "decision": decision.decision,
                "intent_type": intent_type,
                "requested_quantity": normalized_quantity,
                "requested_price": execution_price,
                "execution_policy": execution_plan.to_payload(),
                "rollout_mode": rollout_mode,
                "exchange_submit_allowed": exchange_submit_allowed,
                "limited_live_max_notional": limited_live_max_notional,
                "rollout_notional_cap_applied": rollout_notional_cap_applied,
                "approved_notional_cap": approved_notional_cap,
            },
            correlation_ids=execution_correlation_ids,
        )
    if rollout_mode == "live_dry_run":
        record_audit_event(
            session,
            event_type="live_execution_submit_skipped",
            entity_type="decision_run",
            entity_id=str(decision_run_id or decision.symbol),
            severity="info",
            message="Live dry-run rollout mode completed exchange preflight without submitting to Binance.",
            payload={
                "symbol": decision.symbol,
                "decision": decision.decision,
                "intent_type": intent_type,
                "reason_code": ROLLOUT_MODE_LIVE_DRY_RUN_REASON_CODE,
                "requested_quantity": normalized_quantity,
                "requested_price": execution_price,
                "approved_notional_cap": approved_notional_cap,
                "execution_policy": execution_plan.to_payload(),
                "preflight_request": dict(preflight_request),
                "rollout_mode": rollout_mode,
            },
            correlation_ids=execution_correlation_ids,
        )
        session.flush()
        return {
            "status": "dry_run",
            "reason_codes": [ROLLOUT_MODE_LIVE_DRY_RUN_REASON_CODE],
            "decision": decision.decision,
            "intent_type": intent_type,
            "rollout_mode": rollout_mode,
            "requested_quantity": normalized_quantity,
            "requested_price": execution_price,
            "approved_notional_cap": approved_notional_cap,
            "execution_policy": execution_plan.to_payload(),
            "preflight_request": dict(preflight_request),
            "submit_blocked": True,
            "_cache_dedupe": False,
        }
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
            approved_notional_cap=approved_notional_cap,
            client_order_id_seed=client_order_id_seed,
            correlation_ids=execution_correlation_ids,
        )
    except PreTradeExchangeFilterError as exc:
        record_audit_event(
            session,
            event_type="live_execution_blocked",
            entity_type="decision_run",
            entity_id=str(decision_run_id or decision.symbol),
            severity="warning",
            message="Live execution skipped because the final exchange-normalized order failed pre-trade filters.",
            payload={
                "symbol": decision.symbol,
                "reason_code": exc.reason_code,
                "intent_type": intent_type,
                "requested_quantity": normalized_quantity,
                "requested_price": execution_price,
                "execution_policy": execution_plan.to_payload(),
                "exchange_filter_detail": exc.detail,
            },
            correlation_ids=execution_correlation_ids,
        )
        session.flush()
        return {
            "status": "blocked",
            "reason_codes": [exc.reason_code],
            "intent_type": intent_type,
            "execution_policy": execution_plan.to_payload(),
            "exchange_filter_detail": exc.detail,
        }
    except OrderSubmissionUnknownError as exc:
        order = _create_submission_unknown_order_row(
            session,
            symbol=decision.symbol,
            side=side.lower(),
            order_type=execution_plan.order_type,
            requested_quantity=normalized_quantity,
            requested_price=execution_price,
            decision_run_id=decision_run_id,
            risk_row=risk_row,
            reduce_only=reduce_only,
            close_only=decision.decision == "exit",
            client_order_id=exc.client_order_id,
            submit_request=exc.submit_request,
            submission_tracking=exc.submission_tracking,
            metadata_json={
                "error": str(exc),
                "intent_type": intent_type,
                "requested_quantity": normalized_quantity,
                "requested_price": execution_price,
                "execution_policy": execution_plan.to_payload(),
            },
        )
        payload = {
            "symbol": decision.symbol,
            "client_order_id": exc.client_order_id,
            "submission_tracking": exc.submission_tracking,
            "intent_type": intent_type,
            "requested_quantity": normalized_quantity,
            "requested_price": execution_price,
            "execution_policy": execution_plan.to_payload(),
        }
        create_alert(
            session,
            category="execution",
            severity="warning",
            title="Live submission unknown",
            message="Live order submission timed out and now requires exchange reconciliation.",
            payload=payload,
        )
        record_audit_event(
            session,
            event_type="live_order_submission_unknown",
            entity_type="order",
            entity_id=str(order.id),
            severity="warning",
            message="Live order submission timed out and could not be confirmed yet.",
            payload=payload,
            correlation_ids=normalize_correlation_ids(execution_correlation_ids, execution_id=order.id),
        )
        record_health_event(
            session,
            component="live_execution",
            status="warning",
            message="Live order submission is waiting for reconciliation after timeout/transport failure.",
            payload=payload,
            correlation_ids=normalize_correlation_ids(execution_correlation_ids, execution_id=order.id),
        )
        session.flush()
        return {
            "order_id": order.id,
            "status": "submission_unknown",
            "reason_codes": [UNKNOWN_SUBMISSION_REASON_CODE],
            "client_order_id": exc.client_order_id,
            "submission_state": exc.submission_tracking.get("submission_state"),
            "submit_attempt_count": exc.submission_tracking.get("submit_attempt_count"),
            "last_submit_error": exc.submission_tracking.get("last_submit_error"),
            "intent_type": intent_type,
            "execution_policy": execution_plan.to_payload(),
        }
    except BinanceAPIError as exc:
        reason_codes = ["BINANCE_ORDER_REJECTED"]
        if exc.code == -2019:
            reason_codes.append("INSUFFICIENT_MARGIN")
        submission_tracking = _as_object_dict(getattr(exc, "submission_tracking", None))
        client_order_id = getattr(exc, "client_order_id", None)
        submit_request = getattr(exc, "submit_request", None)
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
                **({"submit_request": dict(submit_request)} if isinstance(submit_request, dict) else {}),
                **({"submission_tracking": submission_tracking} if submission_tracking else {}),
            },
            client_order_id=str(client_order_id) if client_order_id else None,
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
            correlation_ids=normalize_correlation_ids(execution_correlation_ids, execution_id=order.id),
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
            correlation_ids=normalize_correlation_ids(execution_correlation_ids, execution_id=order.id),
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
            correlation_ids=normalize_correlation_ids(execution_correlation_ids, execution_id=order.id),
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
            correlation_ids=normalize_correlation_ids(execution_correlation_ids, execution_id=order.id),
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
    protection_lifecycle: ProtectionLifecycleSnapshot | None = None
    protective_order_ids: list[int] = []
    if position is not None and position.quantity > 0:
        protection_lifecycle = _initialize_protection_lifecycle(
            symbol=decision.symbol,
            trigger_source=f"execute_live_trade:{intent_type}",
            parent_order=order,
        )
        _persist_protection_lifecycle(session, parent_order=order, lifecycle=protection_lifecycle)
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
            protection_lifecycle=protection_lifecycle,
            client_order_id_seed=client_order_id_seed,
            correlation_ids=normalize_correlation_ids(execution_correlation_ids, execution_id=order.id),
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
                "protection_lifecycle": protection_result.get("protection_lifecycle"),
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
            correlation_ids=normalize_correlation_ids(execution_correlation_ids, execution_id=order.id),
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
            "protection_lifecycle": _protection_lifecycle_payload(protection_lifecycle),
        }

    synced_position = final_resync["position"]
    if protection_result is None:
        final_protection_state = final_resync["protection_state"]
    else:
        final_protection_state = protection_result["protection_state"]
    if protection_result is None:
        final_protection_lifecycle = _protection_lifecycle_payload(protection_lifecycle)
    else:
        final_protection_lifecycle = protection_result.get("protection_lifecycle")

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
            "protection_lifecycle": final_protection_lifecycle,
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
        correlation_ids=normalize_correlation_ids(execution_correlation_ids, execution_id=order.id),
    )
    order.metadata_json = {
        **(order.metadata_json or {}),
        "protection_lifecycle": final_protection_lifecycle,
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
        "rollout_mode": rollout_mode,
        "approved_notional_cap": approved_notional_cap,
        "rollout_notional_cap_applied": rollout_notional_cap_applied,
        "exchange_status": order.exchange_status,
        "fill_price": aggregate_fill_price,
        "fill_quantity": aggregate_filled_quantity,
        "realized_pnl": realized_pnl,
        "fees": fee_paid,
        "equity": pnl_snapshot.equity,
        "funding_sync": funding_sync,
        "protective_order_ids": protective_order_ids,
        "protective_state": final_protection_state,
        "protection_lifecycle": final_protection_lifecycle,
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
