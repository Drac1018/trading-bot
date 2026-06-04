from __future__ import annotations

from collections.abc import Mapping
from typing import Any

EXCHANGE_CAN_TRADE_UNKNOWN_REASON_CODE = "EXCHANGE_CAN_TRADE_UNKNOWN"
EXCHANGE_CAN_TRADE_DISABLED_REASON_CODE = "EXCHANGE_AUTH_PERMISSION_REJECTED"
RISK_ADDING_INTENT_TYPES = {"entry", "scale_in"}


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


def fetch_account_config_for_permission(client: object) -> tuple[Mapping[str, object] | None, str | None]:
    get_account_config = getattr(client, "get_account_config", None)
    if not callable(get_account_config):
        return None, None
    try:
        payload = get_account_config()
    except Exception as exc:  # pragma: no cover - exercised through callers with fakes.
        return None, str(exc)
    if not isinstance(payload, Mapping):
        return None, "Binance accountConfig response was not an object."
    return payload, None


def resolve_exchange_trade_permission(
    account_info: Mapping[str, object],
    *,
    account_config: Mapping[str, object] | None = None,
    account_config_error: str | None = None,
) -> dict[str, Any]:
    account_can_trade = _to_optional_bool(account_info.get("canTrade"))
    if account_can_trade is not None:
        return {
            "exchange_can_trade": account_can_trade,
            "exchange_can_trade_known": True,
            "exchange_can_trade_source": "binance_account_info",
            "exchange_can_trade_note": None,
        }

    if account_config is not None:
        config_can_trade = _to_optional_bool(account_config.get("canTrade"))
        if config_can_trade is not None:
            return {
                "exchange_can_trade": config_can_trade,
                "exchange_can_trade_known": True,
                "exchange_can_trade_source": "binance_account_config",
                "exchange_can_trade_note": None,
            }
        return {
            "exchange_can_trade": None,
            "exchange_can_trade_known": False,
            "exchange_can_trade_source": "binance_account_config_missing_canTrade",
            "exchange_can_trade_note": (
                "Binance account response omitted canTrade and accountConfig did not include canTrade; "
                "exchange trade permission is unknown and new entries must remain blocked."
            ),
        }

    if account_config_error:
        return {
            "exchange_can_trade": None,
            "exchange_can_trade_known": False,
            "exchange_can_trade_source": "binance_account_config_unavailable",
            "exchange_can_trade_note": (
                "Binance account response omitted canTrade and accountConfig permission check failed; "
                f"exchange trade permission is unknown and new entries must remain blocked. Error: {account_config_error}"
            ),
        }

    return {
        "exchange_can_trade": None,
        "exchange_can_trade_known": False,
        "exchange_can_trade_source": "binance_account_info_missing_canTrade",
        "exchange_can_trade_note": (
            "Binance account response omitted canTrade; exchange trade permission is unknown and "
            "new entries must remain blocked."
        ),
    }


def exchange_trade_permission_entry_blocker(
    permission: Mapping[str, object],
    *,
    rollout_mode: str,
    live_execution_armed: bool,
    intent_type: str | None = None,
) -> dict[str, object] | None:
    if rollout_mode != "full_live" or not live_execution_armed:
        return None
    if intent_type is not None and intent_type not in RISK_ADDING_INTENT_TYPES:
        return None

    exchange_can_trade = _to_optional_bool(permission.get("exchange_can_trade"))
    explicit_known = _to_optional_bool(permission.get("exchange_can_trade_known"))
    exchange_can_trade_known = (
        bool(explicit_known) if explicit_known is not None else exchange_can_trade is not None
    ) and exchange_can_trade is not None
    if exchange_can_trade_known and exchange_can_trade is True:
        return None

    source = str(permission.get("exchange_can_trade_source") or "unknown")
    checked_at = permission.get("exchange_can_trade_checked_at")
    if exchange_can_trade_known and exchange_can_trade is False:
        return {
            "reason_code": EXCHANGE_CAN_TRADE_DISABLED_REASON_CODE,
            "exchange_can_trade": False,
            "exchange_can_trade_known": True,
            "exchange_can_trade_source": source,
            "exchange_can_trade_checked_at": checked_at,
            "exchange_can_trade_note": "Binance canTrade is confirmed false; new entries must remain blocked.",
        }

    return {
        "reason_code": EXCHANGE_CAN_TRADE_UNKNOWN_REASON_CODE,
        "exchange_can_trade": None,
        "exchange_can_trade_known": False,
        "exchange_can_trade_source": source,
        "exchange_can_trade_checked_at": checked_at,
        "exchange_can_trade_note": str(
            permission.get("exchange_can_trade_note")
            or "Binance canTrade is unknown; new entries must remain blocked."
        ),
    }
