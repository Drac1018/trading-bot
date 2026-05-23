from __future__ import annotations

import copy
import os
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from trading_mvp.models import Order, PnLSnapshot, Position, Setting
from trading_mvp.schemas import (
    BinanceAccountAsset,
    BinanceAccountPosition,
    BinanceAccountResponse,
    BinanceAccountSummary,
    BinanceOpenOrderSummary,
)
from trading_mvp.services.account import account_snapshot_to_dict, create_exchange_pnl_snapshot
from trading_mvp.services.binance import BinanceClient
from trading_mvp.services.runtime_state import get_sync_state_detail, mark_sync_success, write_runtime_detail_key
from trading_mvp.services.settings import (
    derive_guard_mode_reason,
    get_effective_symbols,
    get_latest_blocked_reasons,
    get_or_create_settings,
    get_runtime_credentials,
    is_live_execution_ready,
    serialize_settings_view,
)
from trading_mvp.time_utils import utcnow_naive

FINAL_ORDER_STATUSES = {"filled", "canceled", "cancelled", "rejected", "expired", "finished"}
FINAL_EXCHANGE_ORDER_STATUSES = {"FILLED", "CANCELED", "CANCELLED", "REJECTED", "EXPIRED", "EXPIRED_IN_MATCH", "FINISHED"}
ACCOUNT_CACHE_DETAIL_KEY = "binance_account_cache"
DEFAULT_ACCOUNT_READ_TIMEOUT_SECONDS = 5.0
DEFAULT_ACCOUNT_READ_MAX_GET_ATTEMPTS = 2
ACCOUNT_CACHE_PENDING_STATUSES = {"queued", "refreshing", "already_running"}
ACCOUNT_CACHE_PENDING_TIMEOUT_SECONDS = 5 * 60


def _env_float(name: str, *, default: float, minimum: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return max(minimum, parsed)


def _env_int(name: str, *, default: int, minimum: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(minimum, parsed)


def _account_read_timeout_seconds() -> float:
    return _env_float(
        "TRADING_MVP_BINANCE_ACCOUNT_READ_TIMEOUT_SECONDS",
        default=DEFAULT_ACCOUNT_READ_TIMEOUT_SECONDS,
        minimum=1.0,
    )


def _account_read_max_get_attempts() -> int:
    return _env_int(
        "TRADING_MVP_BINANCE_ACCOUNT_READ_MAX_GET_ATTEMPTS",
        default=DEFAULT_ACCOUNT_READ_MAX_GET_ATTEMPTS,
        minimum=1,
    )


def _iso_now() -> str:
    return utcnow_naive().isoformat()


def _settings_detail(settings_row: Setting) -> dict[str, Any]:
    detail = settings_row.pause_reason_detail
    return dict(detail) if isinstance(detail, dict) else {}


def _account_cache_detail(settings_row: Setting) -> dict[str, Any]:
    detail = _settings_detail(settings_row)
    cache = detail.get(ACCOUNT_CACHE_DETAIL_KEY)
    return dict(cache) if isinstance(cache, dict) else {}


def _coerce_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _normalize_account_cache_for_response(cache: Mapping[str, object]) -> dict[str, object]:
    normalized_cache = dict(cache)
    status = str(normalized_cache.get("status") or "")
    if status not in ACCOUNT_CACHE_PENDING_STATUSES:
        return normalized_cache
    pending_at = _coerce_iso_datetime(normalized_cache.get("started_at")) or _coerce_iso_datetime(
        normalized_cache.get("requested_at")
    )
    if pending_at is None:
        return normalized_cache
    pending_seconds = max((utcnow_naive() - pending_at).total_seconds(), 0.0)
    if pending_seconds <= ACCOUNT_CACHE_PENDING_TIMEOUT_SECONDS:
        return normalized_cache
    has_payload = isinstance(normalized_cache.get("payload"), dict)
    normalized_cache["status"] = "ready" if has_payload else "empty"
    normalized_cache["last_error"] = normalized_cache.get("last_error") or "ACCOUNT_CACHE_REFRESH_ABANDONED"
    normalized_cache["message"] = (
        "이전 Binance 원본 계정 캐시 갱신이 완료되지 않아 마지막 사용 가능한 계정 기준을 표시합니다. "
        "필요하면 다시 갱신 요청할 수 있습니다."
        if has_payload
        else "이전 Binance 원본 계정 캐시 갱신이 완료되지 않았고 사용 가능한 원본 캐시가 없습니다. "
        "최근 로컬 동기화 기준을 표시합니다."
    )
    normalized_cache["abandoned_refresh"] = {
        "previous_status": status,
        "pending_seconds": round(pending_seconds, 1),
    }
    return normalized_cache


def _write_account_cache_detail(settings_row: Setting, cache: Mapping[str, object]) -> dict[str, Any]:
    normalized_cache = dict(cache)
    write_runtime_detail_key(settings_row, ACCOUNT_CACHE_DETAIL_KEY, normalized_cache)
    return normalized_cache


def _response_to_cache_payload(payload: BinanceAccountResponse) -> dict[str, object]:
    payload_dict = payload.model_dump(mode="json")
    summary = payload_dict.get("summary")
    if isinstance(summary, dict):
        summary["message"] = (
            "연동된 Binance 계정 정보를 불러왔습니다."
            if payload.summary.connected
            else "Binance 원본 계정 정보를 불러오지 못했습니다."
        )
    return payload_dict


def _account_info_from_response(payload: BinanceAccountResponse) -> dict[str, object]:
    summary = payload.summary
    return {
        "totalWalletBalance": summary.total_wallet_balance,
        "availableBalance": summary.available_balance,
        "totalUnrealizedProfit": summary.total_unrealized_profit,
        "totalMarginBalance": summary.total_margin_balance,
    }


def _cache_status(cache: Mapping[str, object], *, has_payload: bool) -> str:
    status = str(cache.get("status") or "")
    if status:
        return status
    return "ready" if has_payload else "empty"


def _cache_response(
    session: Session,
    settings_row: Setting,
    *,
    cache: Mapping[str, object] | None = None,
) -> dict[str, object]:
    cache_payload = _normalize_account_cache_for_response(
        dict(cache) if cache is not None else _account_cache_detail(settings_row)
    )
    cached_account = cache_payload.get("payload")
    has_cached_account = isinstance(cached_account, dict)

    if has_cached_account:
        payload = copy.deepcopy(cached_account)
        source = "cached_live"
        default_message = "캐시된 Binance 원본 계정 정보를 표시합니다."
    else:
        payload = get_local_binance_account_snapshot(session).model_dump(mode="json")
        source = "local"
        default_message = "아직 캐시된 원본 응답이 없어 최근 로컬 동기화 기준으로 표시합니다."
        summary = payload.get("summary")
        if isinstance(summary, dict):
            summary["message"] = default_message

    return {
        "status": _cache_status(cache_payload, has_payload=has_cached_account),
        "source": source,
        "message": str(cache_payload.get("message") or default_message),
        "requested_at": cache_payload.get("requested_at"),
        "started_at": cache_payload.get("started_at"),
        "refreshed_at": cache_payload.get("refreshed_at"),
        "last_error": cache_payload.get("last_error"),
        "duration_ms": cache_payload.get("duration_ms"),
        "payload": payload,
    }


def _to_float(value: Any) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: Any) -> int:
    try:
        if value in (None, ""):
            return 0
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _to_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def _to_optional_bool(value: object) -> bool | None:
    if value in {None, ""}:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "y"}:
        return True
    if normalized in {"false", "0", "no", "n"}:
        return False
    return None


def _to_datetime_ms(value: Any) -> datetime | None:
    try:
        timestamp_ms = int(float(value))
    except (TypeError, ValueError):
        return None
    if timestamp_ms <= 0:
        return None
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).replace(tzinfo=None)


def _has_meaningful_balance(*values: float) -> bool:
    return any(abs(value) > 1e-12 for value in values)


def _resolve_exchange_trade_permission(
    account_info: Mapping[str, object],
) -> tuple[bool | None, bool, str, str | None]:
    raw_can_trade = account_info.get("canTrade")
    if raw_can_trade is None:
        return (
            None,
            False,
            "binance_account_info_missing_canTrade",
            "Binance account response omitted canTrade; exchange trade permission is unknown.",
        )
    return _to_bool(raw_can_trade), True, "binance_account_info", None


def _exchange_permission_from_sync_state(settings_row: Setting) -> dict[str, object]:
    account_sync_detail = get_sync_state_detail(settings_row).get("account", {})
    exchange_can_trade = _to_optional_bool(account_sync_detail.get("exchange_can_trade"))
    exchange_can_trade_known = bool(account_sync_detail.get("exchange_can_trade_known")) and exchange_can_trade is not None
    if not exchange_can_trade_known:
        return {
            "can_trade": None,
            "exchange_can_trade": None,
            "exchange_can_trade_known": False,
            "exchange_can_trade_source": "local_snapshot_unchecked",
            "exchange_can_trade_checked_at": None,
            "exchange_can_trade_note": "Local account snapshot does not include Binance canTrade; original exchange permission is unknown.",
        }
    return {
        "can_trade": exchange_can_trade,
        "exchange_can_trade": exchange_can_trade,
        "exchange_can_trade_known": True,
        "exchange_can_trade_source": str(account_sync_detail.get("exchange_can_trade_source") or "account_sync"),
        "exchange_can_trade_checked_at": _coerce_iso_datetime(
            account_sync_detail.get("exchange_can_trade_checked_at") or account_sync_detail.get("last_sync_at")
        ),
        "exchange_can_trade_note": None,
    }


def _exchange_permission_sync_detail_from_summary(summary: BinanceAccountSummary) -> dict[str, object]:
    checked_at = summary.exchange_can_trade_checked_at or utcnow_naive()
    return {
        "exchange_can_trade": summary.exchange_can_trade,
        "exchange_can_trade_known": bool(summary.exchange_can_trade_known) and summary.exchange_can_trade is not None,
        "exchange_can_trade_source": summary.exchange_can_trade_source,
        "exchange_can_trade_checked_at": checked_at.isoformat()
        if isinstance(checked_at, datetime)
        else str(checked_at),
    }


def _build_client(
    settings_row: Setting,
    *,
    timeout_seconds: float | None = None,
    max_get_attempts: int | None = None,
) -> BinanceClient:
    credentials = get_runtime_credentials(settings_row)
    return BinanceClient(
        api_key=credentials.binance_api_key,
        api_secret=credentials.binance_api_secret,
        testnet_enabled=settings_row.binance_testnet_enabled,
        futures_enabled=settings_row.binance_futures_enabled,
        timeout_seconds=timeout_seconds if timeout_seconds is not None else 10.0,
        max_get_attempts=max_get_attempts,
    )


def _build_base_summary(
    session: Session,
    settings_row: Setting,
    *,
    connected: bool,
    message: str,
) -> BinanceAccountSummary:
    settings_payload = serialize_settings_view(settings_row)
    latest_blocked_reasons = get_latest_blocked_reasons(session)
    auto_resume_last_blockers = [str(item) for item in settings_payload.get("auto_resume_last_blockers", []) if item]
    guard_mode_reason = derive_guard_mode_reason(
        settings_row,
        latest_blocked_reasons=latest_blocked_reasons,
        auto_resume_last_blockers=auto_resume_last_blockers,
    )
    return BinanceAccountSummary(
        connected=connected,
        message=message,
        testnet_enabled=settings_row.binance_testnet_enabled,
        futures_enabled=settings_row.binance_futures_enabled,
        tracked_symbols=get_effective_symbols(settings_row),
        can_trade=None,
        exchange_can_trade=None,
        exchange_can_trade_known=False,
        exchange_can_trade_source="not_checked" if connected else "not_configured",
        exchange_can_trade_checked_at=None,
        exchange_can_trade_note="Binance canTrade was not checked for this local account snapshot.",
        app_live_execution_ready=is_live_execution_ready(settings_row),
        app_trading_paused=settings_row.trading_paused,
        app_operating_state=str(settings_payload.get("operating_state", "TRADABLE")),
        app_pause_reason_code=str(settings_payload.get("pause_reason_code") or "") or None,
        app_pause_origin=str(settings_payload.get("pause_origin") or "") or None,
        app_auto_resume_last_blockers=auto_resume_last_blockers,
        guard_mode_reason_category=guard_mode_reason["guard_mode_reason_category"],
        guard_mode_reason_code=guard_mode_reason["guard_mode_reason_code"],
        guard_mode_reason_message=guard_mode_reason["guard_mode_reason_message"],
        latest_blocked_reasons=latest_blocked_reasons,
        exchange_update_time=utcnow_naive(),
    )


def get_local_binance_account_snapshot(session: Session) -> BinanceAccountResponse:
    settings_row = get_or_create_settings(session)
    credentials = get_runtime_credentials(settings_row)
    snapshot_row = session.scalars(
        select(PnLSnapshot).order_by(desc(PnLSnapshot.snapshot_date), desc(PnLSnapshot.created_at)).limit(1)
    ).first()
    snapshot = account_snapshot_to_dict(snapshot_row) if snapshot_row is not None else {}
    snapshot_available = bool(snapshot.get("account_snapshot_available"))
    connected = bool(credentials.binance_api_key and credentials.binance_api_secret)

    summary = _build_base_summary(
        session,
        settings_row,
        connected=connected,
        message=(
            "최근 로컬 계정 동기화 기준입니다. Binance 원본 응답은 새로고침 버튼으로 별도 조회합니다."
            if snapshot_available
            else "아직 성공한 로컬 계정 동기화가 없습니다. Binance 원본 조회 또는 동기화 상태를 확인하세요."
        ),
    ).model_copy(
        update={
            **_exchange_permission_from_sync_state(settings_row),
            "total_wallet_balance": _to_float(snapshot.get("wallet_balance")),
            "available_balance": _to_float(snapshot.get("available_balance")),
            "total_unrealized_profit": _to_float(snapshot.get("unrealized_pnl")),
            "total_margin_balance": _to_float(snapshot.get("equity")),
            "asset_count": 1 if snapshot_available else 0,
            "exchange_update_time": utcnow_naive(),
        }
    )

    positions = [
        BinanceAccountPosition(
            symbol=row.symbol,
            position_side="short" if str(row.side).lower() == "short" else "long",
            position_amt=row.quantity,
            entry_price=row.entry_price,
            mark_price=row.mark_price,
            leverage=row.leverage,
            unrealized_profit=row.unrealized_pnl,
            notional=abs(row.mark_price * row.quantity),
            margin_type=str((row.metadata_json or {}).get("margin_type") or ""),
        )
        for row in session.scalars(
            select(Position)
            .where(Position.mode == "live", Position.status == "open", Position.quantity > 0)
            .order_by(desc(Position.updated_at), desc(Position.created_at))
            .limit(50)
        )
    ]

    open_orders = [
        BinanceOpenOrderSummary(
            symbol=row.symbol,
            side=row.side,
            type=row.order_type,
            status=row.status,
            price=row.requested_price,
            orig_qty=row.requested_quantity,
            executed_qty=row.filled_quantity,
            reduce_only=row.reduce_only,
            close_position=row.close_only,
            update_time=row.updated_at,
        )
        for row in session.scalars(
            select(Order)
            .where(
                Order.mode == "live",
                func.lower(func.coalesce(Order.status, "")).notin_(tuple(FINAL_ORDER_STATUSES)),
                func.upper(func.coalesce(Order.exchange_status, "")).notin_(tuple(FINAL_EXCHANGE_ORDER_STATUSES)),
            )
            .order_by(desc(Order.updated_at), desc(Order.created_at))
            .limit(50)
        )
    ]

    assets = []
    if snapshot_available:
        assets.append(
            BinanceAccountAsset(
                asset="USDT",
                wallet_balance=_to_float(snapshot.get("wallet_balance")),
                available_balance=_to_float(snapshot.get("available_balance")),
                margin_balance=_to_float(snapshot.get("equity")),
                unrealized_profit=_to_float(snapshot.get("unrealized_pnl")),
            )
        )

    summary = summary.model_copy(update={"open_positions": len(positions), "open_orders": len(open_orders)})
    return BinanceAccountResponse(summary=summary, assets=assets, positions=positions, open_orders=open_orders)


def get_cached_binance_account_snapshot(session: Session) -> dict[str, object]:
    settings_row = get_or_create_settings(session)
    return _cache_response(session, settings_row)


def mark_binance_account_refresh_requested(
    session: Session,
    *,
    already_running: bool = False,
) -> dict[str, object]:
    settings_row = get_or_create_settings(session)
    cache = _account_cache_detail(settings_row)
    status = "already_running" if already_running else "queued"
    message = (
        "이미 Binance 원본 계정 캐시 갱신이 진행 중입니다."
        if already_running
        else "Binance 원본 계정 캐시 갱신을 요청했습니다."
    )
    request_detail = {
        "status": status,
        "source": cache.get("source") or "cached_live",
        "requested_at": _iso_now(),
        "last_error": None,
        "message": message,
    }
    if not already_running:
        request_detail["started_at"] = None
    cache.update(request_detail)
    normalized_cache = _write_account_cache_detail(settings_row, cache)
    session.add(settings_row)
    session.flush()
    return _cache_response(session, settings_row, cache=normalized_cache)


def mark_binance_account_refresh_started(session: Session) -> None:
    settings_row = get_or_create_settings(session)
    cache = _account_cache_detail(settings_row)
    cache.update(
        {
            "status": "refreshing",
            "source": cache.get("source") or "cached_live",
            "started_at": _iso_now(),
            "last_error": None,
            "message": "Binance 원본 계정 캐시를 갱신 중입니다.",
        }
    )
    _write_account_cache_detail(settings_row, cache)
    session.add(settings_row)
    session.flush()


def store_binance_account_cache_result(
    session: Session,
    payload: BinanceAccountResponse,
    *,
    duration_ms: float,
) -> dict[str, object]:
    settings_row = get_or_create_settings(session)
    cache = _account_cache_detail(settings_row)
    refreshed_at = _iso_now()
    cache.update(
        {
            "status": "ready",
            "source": "cached_live",
            "payload": _response_to_cache_payload(payload),
            "refreshed_at": refreshed_at,
            "duration_ms": round(duration_ms, 1),
            "last_error": None,
            "message": "Binance 원본 계정 캐시를 갱신했습니다.",
        }
    )
    normalized_cache = _write_account_cache_detail(settings_row, cache)
    if payload.summary.connected:
        create_exchange_pnl_snapshot(session, settings_row, _account_info_from_response(payload))
        mark_sync_success(
            settings_row,
            scope="account",
            detail={
                "source": "binance_account_cache",
                **_exchange_permission_sync_detail_from_summary(payload.summary),
            },
        )
    session.add(settings_row)
    session.flush()
    return _cache_response(session, settings_row, cache=normalized_cache)


def store_binance_account_cache_failure(
    session: Session,
    error: str,
    *,
    duration_ms: float | None = None,
) -> dict[str, object]:
    settings_row = get_or_create_settings(session)
    cache = _account_cache_detail(settings_row)
    cache.update(
        {
            "status": "failed",
            "source": cache.get("source") or "cached_live",
            "duration_ms": round(duration_ms, 1) if duration_ms is not None else cache.get("duration_ms"),
            "last_error": error,
            "message": "Binance 원본 계정 캐시 갱신에 실패했습니다. 이전 캐시나 로컬 동기화 기준을 계속 표시합니다.",
        }
    )
    normalized_cache = _write_account_cache_detail(settings_row, cache)
    session.add(settings_row)
    session.flush()
    return _cache_response(session, settings_row, cache=normalized_cache)


def get_binance_account_snapshot(session: Session) -> BinanceAccountResponse:
    settings_row = get_or_create_settings(session)
    credentials = get_runtime_credentials(settings_row)
    base_summary = _build_base_summary(
        session,
        settings_row,
        connected=False,
        message="바이낸스 API 키가 설정되지 않았습니다.",
    )

    base_summary = base_summary.model_copy(update={"message": "Binance API 키가 설정되지 않았습니다."})

    if not credentials.binance_api_key or not credentials.binance_api_secret:
        return BinanceAccountResponse(summary=base_summary)

    timeout_seconds = _account_read_timeout_seconds()
    max_get_attempts = _account_read_max_get_attempts()

    try:
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="binance-account-read") as executor:
            account_future = executor.submit(
                _build_client(
                    settings_row,
                    timeout_seconds=timeout_seconds,
                    max_get_attempts=max_get_attempts,
                ).get_account_info
            )
            positions_future = executor.submit(
                _build_client(
                    settings_row,
                    timeout_seconds=timeout_seconds,
                    max_get_attempts=max_get_attempts,
                ).get_position_information
            )
            open_orders_future = executor.submit(
                _build_client(
                    settings_row,
                    timeout_seconds=timeout_seconds,
                    max_get_attempts=max_get_attempts,
                ).get_open_orders
            )
            account_info = account_future.result()
            positions_raw = positions_future.result()
            open_orders_raw = open_orders_future.result()
    except Exception as exc:
        return BinanceAccountResponse(
            summary=base_summary.model_copy(
                update={
                    "message": f"바이낸스 계정 정보를 불러오지 못했습니다: {exc}",
                    "exchange_update_time": utcnow_naive(),
                }
            )
        )

    assets: list[BinanceAccountAsset] = []
    assets_payload = account_info.get("assets", [])
    if isinstance(assets_payload, list):
        for raw_asset in assets_payload:
            if not isinstance(raw_asset, Mapping):
                continue
            wallet_balance = _to_float(raw_asset.get("walletBalance"))
            available_balance = _to_float(raw_asset.get("availableBalance"))
            margin_balance = _to_float(raw_asset.get("marginBalance"))
            unrealized_profit = _to_float(raw_asset.get("unrealizedProfit"))
            max_withdraw_amount = _to_float(raw_asset.get("maxWithdrawAmount"))
            if not _has_meaningful_balance(
                wallet_balance,
                available_balance,
                margin_balance,
                unrealized_profit,
                max_withdraw_amount,
            ):
                continue
            assets.append(
                BinanceAccountAsset(
                    asset=str(raw_asset.get("asset", "")),
                    wallet_balance=wallet_balance,
                    available_balance=available_balance,
                    margin_balance=margin_balance,
                    unrealized_profit=unrealized_profit,
                    max_withdraw_amount=max_withdraw_amount,
                )
            )
    assets.sort(key=lambda item: abs(item.wallet_balance) + abs(item.unrealized_profit), reverse=True)

    positions: list[BinanceAccountPosition] = []
    for raw_position in positions_raw:
        if not isinstance(raw_position, Mapping):
            continue
        position_amt = _to_float(raw_position.get("positionAmt"))
        if abs(position_amt) <= 1e-12:
            continue
        positions.append(
            BinanceAccountPosition(
                symbol=str(raw_position.get("symbol", "")),
                position_side="long" if position_amt > 0 else "short",
                position_amt=position_amt,
                entry_price=_to_float(raw_position.get("entryPrice")),
                mark_price=_to_float(raw_position.get("markPrice")),
                liquidation_price=_to_float(raw_position.get("liquidationPrice")),
                leverage=_to_float(raw_position.get("leverage")),
                unrealized_profit=_to_float(raw_position.get("unRealizedProfit")),
                isolated_margin=_to_float(raw_position.get("isolatedMargin")),
                notional=_to_float(raw_position.get("notional")),
                margin_type=str(raw_position.get("marginType", "")),
            )
        )
    positions.sort(key=lambda item: abs(item.notional), reverse=True)

    open_orders: list[BinanceOpenOrderSummary] = []
    for raw_order in open_orders_raw:
        if not isinstance(raw_order, Mapping):
            continue
        open_orders.append(
            BinanceOpenOrderSummary(
                symbol=str(raw_order.get("symbol", "")),
                side=str(raw_order.get("side", "")),
                type=str(raw_order.get("type", "")),
                status=str(raw_order.get("status", "")),
                price=_to_float(raw_order.get("price")),
                stop_price=_to_float(raw_order.get("stopPrice")),
                orig_qty=_to_float(raw_order.get("origQty")),
                executed_qty=_to_float(raw_order.get("executedQty")),
                reduce_only=_to_bool(raw_order.get("reduceOnly")),
                close_position=_to_bool(raw_order.get("closePosition")),
                time_in_force=str(raw_order.get("timeInForce", "")),
                update_time=_to_datetime_ms(raw_order.get("updateTime")),
            )
        )
    open_orders.sort(key=lambda item: item.update_time or datetime.min, reverse=True)
    (
        exchange_can_trade,
        exchange_can_trade_known,
        exchange_can_trade_source,
        exchange_can_trade_note,
    ) = _resolve_exchange_trade_permission(account_info)
    exchange_can_trade_checked_at = utcnow_naive()

    summary = _build_base_summary(
        session,
        settings_row,
        connected=True,
        message="연동된 바이낸스 계정 정보를 불러왔습니다.",
    ).model_copy(
        update={
            "can_trade": exchange_can_trade,
            "exchange_can_trade": exchange_can_trade,
            "exchange_can_trade_known": exchange_can_trade_known,
            "exchange_can_trade_source": exchange_can_trade_source,
            "exchange_can_trade_checked_at": exchange_can_trade_checked_at,
            "exchange_can_trade_note": exchange_can_trade_note,
            "fee_tier": _to_int(account_info.get("feeTier")),
            "total_wallet_balance": _to_float(account_info.get("totalWalletBalance")),
            "available_balance": _to_float(account_info.get("availableBalance")),
            "total_unrealized_profit": _to_float(account_info.get("totalUnrealizedProfit")),
            "total_margin_balance": _to_float(account_info.get("totalMarginBalance")),
            "total_position_initial_margin": _to_float(account_info.get("totalPositionInitialMargin")),
            "total_open_order_initial_margin": _to_float(account_info.get("totalOpenOrderInitialMargin")),
            "total_maint_margin": _to_float(account_info.get("totalMaintMargin")),
            "asset_count": len(assets),
            "open_positions": len(positions),
            "open_orders": len(open_orders),
            "exchange_update_time": utcnow_naive(),
        }
    )
    if exchange_can_trade_note is not None:
        summary.message = f"{summary.message} {exchange_can_trade_note}"
    return BinanceAccountResponse(summary=summary, assets=assets, positions=positions, open_orders=open_orders)
