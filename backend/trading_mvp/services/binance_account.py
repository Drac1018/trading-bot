from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from trading_mvp.models import Setting
from trading_mvp.schemas import (
    BinanceAccountAsset,
    BinanceAccountPosition,
    BinanceAccountResponse,
    BinanceAccountSummary,
    BinanceOpenOrderSummary,
)
from trading_mvp.services.binance import BinanceClient
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


def _resolve_exchange_trade_permission(account_info: Mapping[str, object]) -> tuple[bool, str | None]:
    raw_can_trade = account_info.get("canTrade")
    if raw_can_trade is None:
        return True, "Binance account response omitted canTrade; treated as not explicitly blocked."
    return _to_bool(raw_can_trade), None


def _build_client(settings_row: Setting) -> BinanceClient:
    credentials = get_runtime_credentials(settings_row)
    return BinanceClient(
        api_key=credentials.binance_api_key,
        api_secret=credentials.binance_api_secret,
        testnet_enabled=settings_row.binance_testnet_enabled,
        futures_enabled=settings_row.binance_futures_enabled,
    )


def get_binance_account_snapshot(session: Session) -> BinanceAccountResponse:
    settings_row = get_or_create_settings(session)
    credentials = get_runtime_credentials(settings_row)
    settings_payload = serialize_settings_view(settings_row)
    latest_blocked_reasons = get_latest_blocked_reasons(session)
    auto_resume_last_blockers = [str(item) for item in settings_payload.get("auto_resume_last_blockers", []) if item]
    guard_mode_reason = derive_guard_mode_reason(
        settings_row,
        latest_blocked_reasons=latest_blocked_reasons,
        auto_resume_last_blockers=auto_resume_last_blockers,
    )
    base_summary = BinanceAccountSummary(
        connected=False,
        message="바이낸스 API 키가 설정되지 않았습니다.",
        testnet_enabled=settings_row.binance_testnet_enabled,
        futures_enabled=settings_row.binance_futures_enabled,
        tracked_symbols=get_effective_symbols(settings_row),
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

    if not credentials.binance_api_key or not credentials.binance_api_secret:
        return BinanceAccountResponse(summary=base_summary)

    try:
        client = _build_client(settings_row)
        account_info = client.get_account_info()
        positions_raw = client.get_position_information()
        open_orders_raw = client.get_open_orders()
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
    exchange_can_trade, exchange_can_trade_note = _resolve_exchange_trade_permission(account_info)

    summary = BinanceAccountSummary(
        connected=True,
        message="연동된 바이낸스 계정 정보를 불러왔습니다.",
        testnet_enabled=settings_row.binance_testnet_enabled,
        futures_enabled=settings_row.binance_futures_enabled,
        tracked_symbols=get_effective_symbols(settings_row),
        can_trade=exchange_can_trade,
        exchange_can_trade=exchange_can_trade,
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
        fee_tier=_to_int(account_info.get("feeTier")),
        total_wallet_balance=_to_float(account_info.get("totalWalletBalance")),
        available_balance=_to_float(account_info.get("availableBalance")),
        total_unrealized_profit=_to_float(account_info.get("totalUnrealizedProfit")),
        total_margin_balance=_to_float(account_info.get("totalMarginBalance")),
        total_position_initial_margin=_to_float(account_info.get("totalPositionInitialMargin")),
        total_open_order_initial_margin=_to_float(account_info.get("totalOpenOrderInitialMargin")),
        total_maint_margin=_to_float(account_info.get("totalMaintMargin")),
        asset_count=len(assets),
        open_positions=len(positions),
        open_orders=len(open_orders),
        exchange_update_time=utcnow_naive(),
    )
    if exchange_can_trade_note is not None:
        summary.message = f"{summary.message} {exchange_can_trade_note}"
    return BinanceAccountResponse(summary=summary, assets=assets, positions=positions, open_orders=open_orders)
