from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

from trading_mvp.services.binance import BinanceClient
from trading_mvp.time_utils import utcnow_naive

USER_STREAM_STATUS_IDLE = "idle"
USER_STREAM_STATUS_CONNECTED = "connected"
USER_STREAM_STATUS_DEGRADED = "degraded"
USER_STREAM_STATUS_UNAVAILABLE = "unavailable"
USER_STREAM_SOURCE = "binance_futures_user_stream"
USER_STREAM_PRIMARY_SOURCE = "user_stream"
USER_STREAM_FALLBACK_SOURCE = "rest_polling_fallback"
DEFAULT_KEEPALIVE_INTERVAL_SECONDS = 25 * 60
DEFAULT_BACKOFF_BASE_SECONDS = 1.0
DEFAULT_BACKOFF_MAX_SECONDS = 30.0


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


def _serialize_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _as_object_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _coerce_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _coerce_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value:
        try:
            return int(value)
        except ValueError:
            return default
    return default


def build_user_stream_state(payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    data = dict(payload or {})
    return {
        "status": str(data.get("status") or USER_STREAM_STATUS_IDLE),
        "source": str(data.get("source") or USER_STREAM_SOURCE),
        "listen_key": str(data.get("listen_key") or "") or None,
        "listen_key_created_at": _serialize_datetime(_coerce_datetime(data.get("listen_key_created_at"))),
        "listen_key_refreshed_at": _serialize_datetime(_coerce_datetime(data.get("listen_key_refreshed_at"))),
        "last_keepalive_at": _serialize_datetime(_coerce_datetime(data.get("last_keepalive_at"))),
        "last_connected_at": _serialize_datetime(_coerce_datetime(data.get("last_connected_at"))),
        "last_disconnected_at": _serialize_datetime(_coerce_datetime(data.get("last_disconnected_at"))),
        "connection_attempted_at": _serialize_datetime(_coerce_datetime(data.get("connection_attempted_at"))),
        "last_event_at": _serialize_datetime(_coerce_datetime(data.get("last_event_at"))),
        "last_event_type": str(data.get("last_event_type") or "") or None,
        "last_error": str(data.get("last_error") or "") or None,
        "reconnect_count": _coerce_int(data.get("reconnect_count"), 0),
        "heartbeat_ok": bool(data.get("heartbeat_ok", False)),
        "stream_source": str(data.get("stream_source") or USER_STREAM_PRIMARY_SOURCE),
        "next_retry_at": _serialize_datetime(_coerce_datetime(data.get("next_retry_at"))),
        "backoff_seconds": round(_coerce_float(data.get("backoff_seconds"), 0.0), 4),
    }


def next_reconnect_backoff_seconds(
    reconnect_count: int,
    *,
    base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS,
    max_seconds: float = DEFAULT_BACKOFF_MAX_SECONDS,
) -> float:
    if reconnect_count <= 0:
        return 0.0
    exponent = max(reconnect_count - 1, 0)
    backoff = min(max_seconds, base_seconds * (2 ** exponent))
    return float(round(backoff, 4))


def normalize_user_stream_event(
    payload: Mapping[str, Any],
    *,
    received_at: datetime | None = None,
) -> dict[str, Any]:
    raw_payload = dict(payload)
    resolved_at = received_at or utcnow_naive()
    event_type = str(raw_payload.get("e") or raw_payload.get("eventType") or "unknown")
    event_time = _coerce_datetime(
        raw_payload.get("E")
        or raw_payload.get("eventTime")
        or raw_payload.get("T")
        or raw_payload.get("transactionTime")
    ) or resolved_at
    categories: list[str] = []
    symbol: str | None = None
    symbols: list[str] = []
    order_id: str | None = None
    client_order_id: str | None = None
    trade_id: str | None = None
    execution_type: str | None = None
    order_status: str | None = None

    if event_type == "ORDER_TRADE_UPDATE":
        order_payload = _as_object_dict(raw_payload.get("o"))
        symbol = str(order_payload.get("s") or order_payload.get("symbol") or "").upper() or None
        if symbol:
            symbols.append(symbol)
        order_id = str(order_payload.get("i") or order_payload.get("orderId") or "") or None
        client_order_id = str(order_payload.get("c") or order_payload.get("clientOrderId") or "") or None
        trade_id = str(order_payload.get("t") or "") or None
        execution_type = str(order_payload.get("x") or order_payload.get("executionType") or "") or None
        order_status = str(order_payload.get("X") or order_payload.get("status") or "") or None
        last_fill_quantity = _coerce_float(order_payload.get("l"), 0.0)
        if execution_type == "TRADE" or (trade_id not in {None, "", "0"} and last_fill_quantity > 0):
            categories = ["order", "execution"]
            primary_category = "execution"
        else:
            categories = ["order"]
            primary_category = "order"
    elif event_type == "ACCOUNT_UPDATE":
        account_payload = _as_object_dict(raw_payload.get("a"))
        balances = account_payload.get("B") if isinstance(account_payload.get("B"), list) else []
        positions = account_payload.get("P") if isinstance(account_payload.get("P"), list) else []
        if balances:
            categories.append("account")
        if positions:
            categories.append("position")
            for item in positions:
                if not isinstance(item, dict):
                    continue
                position_symbol = str(item.get("s") or item.get("symbol") or "").upper()
                if position_symbol and position_symbol not in symbols:
                    symbols.append(position_symbol)
        primary_category = "account" if "account" in categories else "position" if "position" in categories else "account"
    elif event_type == "MARGIN_CALL":
        categories = ["account", "position"]
        for item in raw_payload.get("p", []):
            if not isinstance(item, dict):
                continue
            position_symbol = str(item.get("s") or item.get("symbol") or "").upper()
            if position_symbol and position_symbol not in symbols:
                symbols.append(position_symbol)
        primary_category = "account"
    elif event_type == "ACCOUNT_CONFIG_UPDATE":
        categories = ["account"]
        primary_category = "account"
    elif event_type == "listenKeyExpired":
        categories = ["listen_key"]
        primary_category = "listen_key"
    else:
        categories = ["unknown"]
        primary_category = "unknown"

    if symbol is None and symbols:
        symbol = symbols[0]

    return {
        "event_type": event_type,
        "event_time": event_time.isoformat(),
        "event_category": primary_category,
        "related_categories": categories,
        "symbol": symbol,
        "symbols": symbols,
        "order_id": order_id,
        "client_order_id": client_order_id,
        "trade_id": trade_id,
        "execution_type": execution_type,
        "order_status": order_status,
        "listen_key_expired": event_type == "listenKeyExpired",
        "raw_payload": raw_payload,
    }


class BinanceUserStreamListener:
    def __init__(
        self,
        client: BinanceClient,
        *,
        now_fn: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
        keepalive_interval_seconds: float = DEFAULT_KEEPALIVE_INTERVAL_SECONDS,
        backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS,
        backoff_max_seconds: float = DEFAULT_BACKOFF_MAX_SECONDS,
    ) -> None:
        self._client = client
        self._now = now_fn or utcnow_naive
        self._sleep = sleep_fn or asyncio.sleep
        self._keepalive_interval_seconds = keepalive_interval_seconds
        self._backoff_base_seconds = backoff_base_seconds
        self._backoff_max_seconds = backoff_max_seconds

    def ensure_registration(self, state: Mapping[str, Any] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        current = build_user_stream_state(state)
        now = self._now()
        next_retry_at = _coerce_datetime(current.get("next_retry_at"))
        if (
            current["status"] == USER_STREAM_STATUS_DEGRADED
            and next_retry_at is not None
            and next_retry_at > now
        ):
            issue = {
                "severity": "warning",
                "reason_code": "USER_STREAM_BACKOFF_ACTIVE",
                "message": "User stream reconnect backoff is still active.",
                "payload": {"next_retry_at": current["next_retry_at"], "backoff_seconds": current["backoff_seconds"]},
            }
            return current, [issue]

        current["connection_attempted_at"] = now.isoformat()
        listen_key = str(current.get("listen_key") or "") or None
        refreshed_at = _coerce_datetime(current.get("listen_key_refreshed_at"))
        issues: list[dict[str, Any]] = []
        try:
            if listen_key is None:
                listen_key = self._client.create_futures_listen_key()
                current["listen_key"] = listen_key
                current["listen_key_created_at"] = now.isoformat()
                current["listen_key_refreshed_at"] = now.isoformat()
                current["last_keepalive_at"] = now.isoformat()
            elif refreshed_at is None or (now - refreshed_at).total_seconds() >= self._keepalive_interval_seconds:
                self._client.keepalive_futures_listen_key(listen_key)
                current["listen_key_refreshed_at"] = now.isoformat()
                current["last_keepalive_at"] = now.isoformat()
            current["status"] = USER_STREAM_STATUS_CONNECTED
            current["source"] = USER_STREAM_SOURCE
            current["heartbeat_ok"] = True
            current["stream_source"] = USER_STREAM_PRIMARY_SOURCE
            current["last_error"] = None
            current["next_retry_at"] = None
            current["backoff_seconds"] = 0.0
        except Exception as exc:
            reconnect_count = int(current.get("reconnect_count") or 0) + 1
            backoff_seconds = next_reconnect_backoff_seconds(
                reconnect_count,
                base_seconds=self._backoff_base_seconds,
                max_seconds=self._backoff_max_seconds,
            )
            current["status"] = USER_STREAM_STATUS_DEGRADED
            current["source"] = USER_STREAM_SOURCE
            current["listen_key"] = listen_key
            current["heartbeat_ok"] = False
            current["stream_source"] = USER_STREAM_FALLBACK_SOURCE
            current["reconnect_count"] = reconnect_count
            current["last_error"] = str(exc)
            current["last_disconnected_at"] = now.isoformat()
            current["next_retry_at"] = (now + timedelta(seconds=backoff_seconds)).isoformat()
            current["backoff_seconds"] = backoff_seconds
            issues.append(
                {
                    "severity": "warning",
                    "reason_code": "USER_STREAM_REGISTRATION_FAILED",
                    "message": "Failed to create or keep alive the Binance futures listen key.",
                    "payload": {
                        "error": str(exc),
                        "listen_key": listen_key,
                        "reconnect_count": reconnect_count,
                        "next_retry_at": current["next_retry_at"],
                    },
                }
            )
        return current, issues

    def close_registration(self, state: Mapping[str, Any] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        current = build_user_stream_state(state)
        listen_key = str(current.get("listen_key") or "") or None
        issues: list[dict[str, Any]] = []
        if listen_key is None:
            current["status"] = USER_STREAM_STATUS_IDLE
            current["heartbeat_ok"] = False
            return current, issues
        try:
            self._client.close_futures_listen_key(listen_key)
            current["status"] = USER_STREAM_STATUS_IDLE
            current["listen_key"] = None
            current["heartbeat_ok"] = False
            current["stream_source"] = USER_STREAM_FALLBACK_SOURCE
        except Exception as exc:
            issues.append(
                {
                    "severity": "warning",
                    "reason_code": "USER_STREAM_CLOSE_FAILED",
                    "message": "Failed to close the Binance futures listen key.",
                    "payload": {"error": str(exc), "listen_key": listen_key},
                }
            )
        return current, issues

    async def collect_once(
        self,
        state: Mapping[str, Any] | None,
        *,
        max_events: int | None = None,
        idle_timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        current, issues = self.ensure_registration(state)
        listen_key = str(current.get("listen_key") or "") or None
        if listen_key is None or current["status"] == USER_STREAM_STATUS_DEGRADED:
            return {"state": current, "events": [], "issues": issues}

        now = self._now()
        current["status"] = USER_STREAM_STATUS_CONNECTED
        current["source"] = USER_STREAM_SOURCE
        current["last_connected_at"] = now.isoformat()
        current["connection_attempted_at"] = now.isoformat()
        current["heartbeat_ok"] = True
        current["stream_source"] = USER_STREAM_PRIMARY_SOURCE
        current["last_error"] = None
        current["next_retry_at"] = None
        current["backoff_seconds"] = 0.0
        events: list[dict[str, Any]] = []

        try:
            async for raw_payload in self._client.stream_futures_user_events(
                listen_key,
                max_events=max_events,
                idle_timeout_seconds=idle_timeout_seconds,
            ):
                if not isinstance(raw_payload, Mapping):
                    continue
                normalized = normalize_user_stream_event(raw_payload, received_at=self._now())
                events.append(normalized)
                current["last_event_at"] = normalized["event_time"]
                current["last_event_type"] = normalized["event_type"]
                current["heartbeat_ok"] = True
                current["status"] = USER_STREAM_STATUS_CONNECTED
                current["stream_source"] = USER_STREAM_PRIMARY_SOURCE
                current["last_error"] = None
                current["next_retry_at"] = None
                current["backoff_seconds"] = 0.0
                if normalized["listen_key_expired"]:
                    expired_at = self._now()
                    current["status"] = USER_STREAM_STATUS_DEGRADED
                    current["listen_key"] = None
                    current["last_disconnected_at"] = expired_at.isoformat()
                    current["heartbeat_ok"] = False
                    current["stream_source"] = USER_STREAM_FALLBACK_SOURCE
                    current["last_error"] = "LISTEN_KEY_EXPIRED"
                    issues.append(
                        {
                            "severity": "warning",
                            "reason_code": "USER_STREAM_LISTEN_KEY_EXPIRED",
                            "message": "Binance futures listen key expired.",
                            "payload": {"event_time": normalized["event_time"]},
                        }
                    )
                    break
        except Exception as exc:
            disconnect_time = self._now()
            reconnect_count = int(current.get("reconnect_count") or 0) + 1
            backoff_seconds = next_reconnect_backoff_seconds(
                reconnect_count,
                base_seconds=self._backoff_base_seconds,
                max_seconds=self._backoff_max_seconds,
            )
            current["status"] = USER_STREAM_STATUS_DEGRADED
            current["heartbeat_ok"] = False
            current["stream_source"] = USER_STREAM_FALLBACK_SOURCE
            current["reconnect_count"] = reconnect_count
            current["last_error"] = str(exc)
            current["last_disconnected_at"] = disconnect_time.isoformat()
            current["next_retry_at"] = (disconnect_time + timedelta(seconds=backoff_seconds)).isoformat()
            current["backoff_seconds"] = backoff_seconds
            issues.append(
                {
                    "severity": "warning",
                    "reason_code": "USER_STREAM_CONNECTION_DROPPED",
                    "message": "Binance futures user stream connection dropped.",
                    "payload": {
                        "error": str(exc),
                        "listen_key": listen_key,
                        "reconnect_count": reconnect_count,
                        "next_retry_at": current["next_retry_at"],
                    },
                }
            )
        return {"state": current, "events": events, "issues": issues}

    async def listen_forever(
        self,
        state: Mapping[str, Any] | None,
        *,
        max_cycles: int | None = None,
        max_events_per_cycle: int | None = None,
        idle_timeout_seconds: float = 30.0,
    ) -> AsyncIterator[dict[str, Any]]:
        current = build_user_stream_state(state)
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            result = await self.collect_once(
                current,
                max_events=max_events_per_cycle,
                idle_timeout_seconds=idle_timeout_seconds,
            )
            current = build_user_stream_state(result.get("state"))
            yield {"state": current, "events": list(result.get("events") or []), "issues": list(result.get("issues") or [])}
            cycles += 1
            backoff_seconds = _coerce_float(current.get("backoff_seconds"), 0.0)
            if backoff_seconds > 0:
                await self._sleep(backoff_seconds)
