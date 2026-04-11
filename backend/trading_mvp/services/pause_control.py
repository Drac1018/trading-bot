from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from trading_mvp.config import get_settings
from trading_mvp.models import Setting
from trading_mvp.services.audit import record_audit_event, record_health_event
from trading_mvp.services.binance import BinanceClient
from trading_mvp.services.settings import (
    get_effective_symbols,
    get_runtime_credentials,
    is_live_execution_armed,
    pause_reason_allows_auto_resume,
    set_trading_pause,
)
from trading_mvp.time_utils import utcnow_naive


def _to_float(value: object) -> float:
    if value in {None, ""}:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value))


def _flag_enabled(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() == "true"


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


def attempt_auto_resume(session: Session, settings_row: Setting) -> dict[str, Any]:
    if not settings_row.trading_paused:
        return {"attempted": False, "resumed": False, "status": "not_paused"}

    reason_code = settings_row.pause_reason_code
    if not pause_reason_allows_auto_resume(reason_code):
        return {
            "attempted": False,
            "resumed": False,
            "status": "not_whitelisted",
            "reason_code": reason_code,
        }

    now = utcnow_naive()
    if settings_row.auto_resume_after is not None and settings_row.auto_resume_after > now:
        return {
            "attempted": True,
            "resumed": False,
            "status": "waiting_cooldown",
            "reason_code": reason_code,
            "auto_resume_after": settings_row.auto_resume_after.isoformat(),
        }

    defaults = get_settings()
    credentials = get_runtime_credentials(settings_row, defaults=defaults)
    blockers: list[str] = []

    if not defaults.live_trading_env_enabled:
        blockers.append("LIVE_ENV_DISABLED")
    if not settings_row.live_trading_enabled:
        blockers.append("LIVE_TRADING_DISABLED")
    if not settings_row.manual_live_approval:
        blockers.append("LIVE_APPROVAL_POLICY_DISABLED")
    if not is_live_execution_armed(settings_row):
        blockers.append("LIVE_APPROVAL_REQUIRED")
    if not credentials.binance_api_key or not credentials.binance_api_secret:
        blockers.append("LIVE_CREDENTIALS_MISSING")

    if blockers:
        record_health_event(
            session,
            component="trading_pause",
            status="warning",
            message="Auto resume skipped because live prerequisites are not satisfied.",
            payload={"reason_code": reason_code, "blockers": blockers},
        )
        session.flush()
        return {
            "attempted": True,
            "resumed": False,
            "status": "blocked",
            "reason_code": reason_code,
            "blockers": blockers,
        }

    try:
        client = _build_client(settings_row)
        account_info = client.get_account_info()
        protective_summary: dict[str, str] = {}
        for symbol in get_effective_symbols(settings_row):
            open_orders = client.get_open_orders(symbol)
            positions = client.get_position_information(symbol)
            has_open_position = any(abs(_to_float(item.get("positionAmt"))) > 0 for item in positions)
            has_protection = any(
                _flag_enabled(item.get("closePosition")) or _flag_enabled(item.get("reduceOnly"))
                for item in open_orders
            )
            if has_open_position and not has_protection:
                blockers.append(f"MISSING_PROTECTIVE_ORDERS:{symbol}")
                protective_summary[symbol] = "missing"
            elif has_open_position:
                protective_summary[symbol] = "ready"
    except Exception as exc:
        record_health_event(
            session,
            component="trading_pause",
            status="error",
            message="Auto resume health check failed.",
            payload={"reason_code": reason_code, "error": str(exc)},
        )
        session.flush()
        return {
            "attempted": True,
            "resumed": False,
            "status": "health_check_failed",
            "reason_code": reason_code,
            "error": str(exc),
        }

    if blockers:
        record_health_event(
            session,
            component="trading_pause",
            status="warning",
            message="Auto resume blocked after exchange safety checks.",
            payload={"reason_code": reason_code, "blockers": blockers},
        )
        session.flush()
        return {
            "attempted": True,
            "resumed": False,
            "status": "blocked",
            "reason_code": reason_code,
            "blockers": blockers,
        }

    previous_reason = settings_row.pause_reason_code
    previous_pause_at = settings_row.pause_triggered_at.isoformat() if settings_row.pause_triggered_at else None
    set_trading_pause(session, False)
    record_audit_event(
        session,
        event_type="trading_auto_resumed",
        entity_type="settings",
        entity_id=str(settings_row.id),
        severity="info",
        message="Trading pause automatically cleared after recoverable checks passed.",
        payload={
            "reason_code": previous_reason,
            "previous_pause_at": previous_pause_at,
            "available_balance": account_info.get("availableBalance"),
        },
    )
    record_health_event(
        session,
        component="trading_pause",
        status="ok",
        message="Trading pause automatically cleared.",
        payload={"reason_code": previous_reason},
    )
    session.flush()
    return {
        "attempted": True,
        "resumed": True,
        "status": "resumed",
        "reason_code": previous_reason,
    }
