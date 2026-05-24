from __future__ import annotations

from collections.abc import Mapping
from typing import Any


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
