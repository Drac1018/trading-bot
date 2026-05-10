from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock
from typing import Any

import websockets

from trading_mvp.schemas import MarketCandle
from trading_mvp.services.market_data_cache import (
    MARKET_DATA_CACHE_BACKEND_PROCESS_LOCAL,
    MARKET_DATA_CACHE_ENV_MAINNET,
    MARKET_DATA_CACHE_MARKET_TYPE_USDM_FUTURES,
    MARKET_DATA_CACHE_SCOPE_PROCESS_LOCAL,
)
from trading_mvp.time_utils import utcnow_naive

MARKET_STREAM_DETAIL_KEY = "market_stream"
MARKET_STREAM_STATUS_IDLE = "idle"
MARKET_STREAM_STATUS_CONNECTED = "connected"
MARKET_STREAM_STATUS_DEGRADED = "degraded"
MARKET_STREAM_STATUS_UNAVAILABLE = "unavailable"
MARKET_STREAM_SOURCE = "binance_futures_market_stream"
MARKET_STREAM_DATA_SOURCE = "binance_ws_final_kline"
MARKET_STREAM_REST_FALLBACK_SOURCE = "binance_rest"
MARKET_STREAM_PARTIAL_REASON_CODE = "PARTIAL_KLINE_IGNORED"
MARKET_STREAM_CLOSED_REASON_CODE = "KLINE_CLOSED"
MARKET_STREAM_INVALID_REASON_CODE = "MARKET_STREAM_EVENT_INVALID"
MARKET_STREAM_MAX_STREAMS = 1024
MARKET_STREAM_MIN_STALE_AFTER_SECONDS = 120
MARKET_STREAM_CACHE_BACKEND = MARKET_DATA_CACHE_BACKEND_PROCESS_LOCAL
MARKET_STREAM_CACHE_SCOPE = MARKET_DATA_CACHE_SCOPE_PROCESS_LOCAL
MARKET_STREAM_CACHE_SCOPE_DETAIL = (
    "Closed-kline cache is local to this backend process; REST remains the fallback when this process has no fresh cache."
)
MARKET_STREAM_SHARED_CACHE_SCOPE_DETAIL = (
    "Redis shared closed-kline cache is configured as an optional freshness accelerator; "
    "REST remains the fallback when Redis is unavailable or has no fresh final kline."
)


@dataclass(frozen=True, slots=True)
class ClosedKlineCacheEntry:
    symbol: str
    timeframe: str
    candle: MarketCandle
    event_time: datetime | None
    close_time: datetime | None
    received_at: datetime
    stream_name: str | None = None

    def freshness_seconds(self, now: datetime | None = None) -> int:
        resolved_now = now or utcnow_naive()
        return max(int((resolved_now - self.received_at).total_seconds()), 0)

    def as_source_detail(self, *, now: datetime | None = None, stale_after_seconds: int | None = None) -> dict[str, Any]:
        resolved_stale_after = stale_after_seconds or market_stream_stale_after_seconds(self.timeframe)
        freshness_seconds = self.freshness_seconds(now)
        return {
            "source": MARKET_STREAM_DATA_SOURCE,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "stream_name": self.stream_name,
            "event_time": self.event_time.isoformat() if self.event_time is not None else None,
            "close_time": self.close_time.isoformat() if self.close_time is not None else None,
            "received_at": self.received_at.isoformat(),
            "freshness_seconds": freshness_seconds,
            "stale_after_seconds": resolved_stale_after,
            "stale": freshness_seconds > resolved_stale_after,
            "partial_candle_used": False,
        }


_cache_lock = RLock()
_closed_kline_cache: dict[tuple[str, str], ClosedKlineCacheEntry] = {}
_market_stream_state: dict[str, Any] = {}


def _as_mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _coerce_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _datetime_from_ms(value: object) -> datetime | None:
    milliseconds = _coerce_int(value, 0)
    if milliseconds <= 0:
        return None
    return datetime.fromtimestamp(milliseconds / 1000.0, UTC).replace(tzinfo=None)


def _serialize_datetime(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and value:
        return value
    return None


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def timeframe_to_seconds(timeframe: str) -> int:
    value = str(timeframe or "").strip()
    if value.endswith("m"):
        return max(int(value[:-1]), 1) * 60
    if value.endswith("h"):
        return max(int(value[:-1]), 1) * 60 * 60
    if value.endswith("d"):
        return max(int(value[:-1]), 1) * 24 * 60 * 60
    raise ValueError(f"Unsupported market stream timeframe: {timeframe}")


def market_stream_stale_after_seconds(timeframe: str) -> int:
    try:
        timeframe_seconds = timeframe_to_seconds(timeframe)
    except (TypeError, ValueError):
        timeframe_seconds = MARKET_STREAM_MIN_STALE_AFTER_SECONDS
    return max(timeframe_seconds * 2, MARKET_STREAM_MIN_STALE_AFTER_SECONDS)


def build_market_stream_names(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    max_streams: int = MARKET_STREAM_MAX_STREAMS,
) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        symbol_key = str(symbol or "").strip().lower()
        if not symbol_key:
            continue
        for timeframe in timeframes:
            timeframe_key = str(timeframe or "").strip()
            if not timeframe_key:
                continue
            stream_name = f"{symbol_key}@kline_{timeframe_key}"
            if stream_name in seen:
                continue
            seen.add(stream_name)
            names.append(stream_name)
            if len(names) >= max_streams:
                return names
    return names


def build_futures_market_stream_url(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    testnet_enabled: bool = False,
) -> str:
    stream_names = build_market_stream_names(symbols, timeframes)
    if not stream_names:
        raise ValueError("At least one market stream symbol/timeframe is required.")
    ws_base = "wss://stream.binancefuture.com" if testnet_enabled else "wss://fstream.binance.com"
    return f"{ws_base}/market/stream?streams={'/'.join(stream_names)}"


def normalize_market_stream_event(
    payload: Mapping[str, Any],
    *,
    received_at: datetime | None = None,
) -> dict[str, Any]:
    received_time = received_at or utcnow_naive()
    stream_name = str(payload.get("stream") or "") or None
    raw_payload = _as_mapping(payload.get("data")) if "data" in payload else payload
    event_type = str(raw_payload.get("e") or "")
    kline = _as_mapping(raw_payload.get("k"))
    if event_type != "kline" or not kline:
        return {
            "event_category": "ignored",
            "reason_code": MARKET_STREAM_INVALID_REASON_CODE,
            "received_at": received_time.isoformat(),
            "stream_name": stream_name,
            "raw_event_type": event_type or None,
        }

    symbol = str(kline.get("s") or raw_payload.get("s") or "").upper()
    timeframe = str(kline.get("i") or "")
    open_time = _datetime_from_ms(kline.get("t"))
    close_time = _datetime_from_ms(kline.get("T"))
    event_time = _datetime_from_ms(raw_payload.get("E"))
    closed = bool(kline.get("x", False))
    open_price = _coerce_float(kline.get("o"))
    high = _coerce_float(kline.get("h"))
    low = _coerce_float(kline.get("l"))
    close = _coerce_float(kline.get("c"))
    volume = _coerce_float(kline.get("v"))
    valid_candle = (
        bool(symbol)
        and bool(timeframe)
        and open_time is not None
        and open_price is not None
        and high is not None
        and low is not None
        and close is not None
        and volume is not None
        and open_price > 0
        and high > 0
        and low > 0
        and close > 0
        and volume >= 0
    )
    if not valid_candle:
        return {
            "event_category": "ignored",
            "reason_code": MARKET_STREAM_INVALID_REASON_CODE,
            "symbol": symbol or None,
            "timeframe": timeframe or None,
            "closed": closed,
            "received_at": received_time.isoformat(),
            "stream_name": stream_name,
        }

    candle = MarketCandle(
        timestamp=open_time,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )
    return {
        "event_category": "market_kline",
        "reason_code": MARKET_STREAM_CLOSED_REASON_CODE if closed else MARKET_STREAM_PARTIAL_REASON_CODE,
        "symbol": symbol,
        "timeframe": timeframe,
        "closed": closed,
        "event_time": event_time.isoformat() if event_time is not None else None,
        "close_time": close_time.isoformat() if close_time is not None else None,
        "received_at": received_time.isoformat(),
        "stream_name": stream_name,
        "candle": candle.model_dump(mode="json"),
        "partial_candle_used": False,
    }


def _entry_from_normalized(event: Mapping[str, Any]) -> ClosedKlineCacheEntry | None:
    if event.get("event_category") != "market_kline" or not bool(event.get("closed")):
        return None
    candle_payload = _as_mapping(event.get("candle"))
    timestamp = _parse_datetime(candle_payload.get("timestamp"))
    open_price = _coerce_float(candle_payload.get("open"))
    high = _coerce_float(candle_payload.get("high"))
    low = _coerce_float(candle_payload.get("low"))
    close = _coerce_float(candle_payload.get("close"))
    volume = _coerce_float(candle_payload.get("volume"))
    if None in {timestamp, open_price, high, low, close, volume}:
        return None
    return ClosedKlineCacheEntry(
        symbol=str(event.get("symbol") or "").upper(),
        timeframe=str(event.get("timeframe") or ""),
        candle=MarketCandle(
            timestamp=timestamp,
            open=float(open_price),
            high=float(high),
            low=float(low),
            close=float(close),
            volume=float(volume),
        ),
        event_time=_parse_datetime(event.get("event_time")),
        close_time=_parse_datetime(event.get("close_time")),
        received_at=_parse_datetime(event.get("received_at")) or utcnow_naive(),
        stream_name=str(event.get("stream_name") or "") or None,
    )


def build_market_stream_state(payload: Mapping[str, Any] | None = None, *, now: datetime | None = None) -> dict[str, Any]:
    data = dict(payload or {})
    current_time = now or utcnow_naive()
    status = str(data.get("status") or MARKET_STREAM_STATUS_IDLE)
    last_event_at = _parse_datetime(data.get("last_event_at"))
    stale_after_seconds = max(_coerce_int(data.get("stale_after_seconds"), MARKET_STREAM_MIN_STALE_AFTER_SECONDS), 1)
    freshness_seconds = max(int((current_time - last_event_at).total_seconds()), 0) if last_event_at is not None else None
    stale = status == MARKET_STREAM_STATUS_CONNECTED and (
        freshness_seconds is None or freshness_seconds > stale_after_seconds
    )
    normalized_status = MARKET_STREAM_STATUS_DEGRADED if stale else status
    default_stream_enabled = normalized_status not in {MARKET_STREAM_STATUS_IDLE, MARKET_STREAM_STATUS_UNAVAILABLE}
    raw_reason_code = str(data.get("reason_code") or "") or None
    reason_code = "MARKET_STREAM_STALE" if stale else raw_reason_code
    if not stale and reason_code == "MARKET_STREAM_STALE":
        reason_code = None
    cache_backend = str(data.get("cache_backend") or MARKET_STREAM_CACHE_BACKEND)
    configured_cache_backend = str(data.get("configured_cache_backend") or cache_backend)
    cache_scope = str(data.get("cache_scope") or MARKET_STREAM_CACHE_SCOPE)
    cache_health = str(data.get("cache_health") or "unknown")
    cache_reject_reason = str(data.get("cache_reject_reason") or "") or None
    redis_configured = bool(
        data.get(
            "redis_configured",
            configured_cache_backend == "redis" or cache_backend == "redis",
        )
    )
    redis_required = bool(data.get("redis_required", False))
    raw_redis_connected = data.get("redis_connected")
    if isinstance(raw_redis_connected, bool):
        redis_connected: bool | None = raw_redis_connected
    elif not redis_configured:
        redis_connected = False
    elif cache_health in {"ok", "miss", "stale"} or (
        cache_health == "error"
        and cache_reject_reason
        in {"cache_payload_corrupt", "cache_payload_invalid", "cache_metadata_mismatch"}
    ):
        redis_connected = True
    elif cache_health in {"unavailable", "disabled"}:
        redis_connected = False
    else:
        redis_connected = None
    cache_scope_detail = str(data.get("cache_scope_detail") or "") or (
        MARKET_STREAM_SHARED_CACHE_SCOPE_DETAIL if cache_scope == "shared" else MARKET_STREAM_CACHE_SCOPE_DETAIL
    )
    return {
        "status": normalized_status,
        "source": str(data.get("source") or MARKET_STREAM_SOURCE),
        "stream_source": str(data.get("stream_source") or MARKET_STREAM_DATA_SOURCE),
        "stream_enabled": bool(data.get("stream_enabled", data.get("enabled", default_stream_enabled))),
        "stream_running": normalized_status == MARKET_STREAM_STATUS_CONNECTED,
        "background_enabled": bool(data.get("background_enabled", data.get("enabled", False))),
        "database_dialect": str(data.get("database_dialect") or "") or None,
        "cache_backend": cache_backend,
        "configured_cache_backend": configured_cache_backend,
        "cache_market_type": str(data.get("cache_market_type") or MARKET_DATA_CACHE_MARKET_TYPE_USDM_FUTURES),
        "cache_environment": str(data.get("cache_environment") or MARKET_DATA_CACHE_ENV_MAINNET),
        "cache_health": cache_health,
        "cache_reject_reason": cache_reject_reason,
        "cache_scope": cache_scope,
        "cache_scope_detail": cache_scope_detail,
        "shared_cache_supported": bool(data.get("shared_cache_supported", False)),
        "redis_required": redis_required,
        "redis_configured": redis_configured,
        "redis_connected": redis_connected,
        "cache_write_status": str(data.get("cache_write_status") or "") or None,
        "last_shared_cache_read_at": _serialize_datetime(data.get("last_shared_cache_read_at")),
        "last_shared_cache_write_at": _serialize_datetime(data.get("last_shared_cache_write_at")),
        "last_shared_cache_error": str(data.get("last_shared_cache_error") or "") or None,
        "shared_cache_key": str(data.get("shared_cache_key") or "") or None,
        "sqlite_default_disabled": bool(data.get("sqlite_default_disabled", False)),
        "subscribed_symbols": [str(item).upper() for item in data.get("subscribed_symbols", []) if item],
        "subscribed_timeframes": [str(item) for item in data.get("subscribed_timeframes", []) if item],
        "stream_count": _coerce_int(data.get("stream_count"), 0),
        "last_connected_at": _serialize_datetime(data.get("last_connected_at")),
        "last_disconnected_at": _serialize_datetime(data.get("last_disconnected_at")),
        "last_event_at": _serialize_datetime(last_event_at),
        "last_closed_kline_at": _serialize_datetime(data.get("last_closed_kline_at")),
        "last_closed_kline_symbol": str(data.get("last_closed_kline_symbol") or "") or None,
        "last_closed_kline_timeframe": str(data.get("last_closed_kline_timeframe") or "") or None,
        "last_error": str(data.get("last_error") or "") or None,
        "reconnect_count": _coerce_int(data.get("reconnect_count"), 0),
        "partial_ignored_count": _coerce_int(data.get("partial_ignored_count"), 0),
        "closed_kline_count": _coerce_int(data.get("closed_kline_count"), 0),
        "heartbeat_ok": bool(data.get("heartbeat_ok", False)),
        "freshness_seconds": freshness_seconds,
        "stale_after_seconds": stale_after_seconds,
        "stale": stale,
        "degraded": normalized_status in {MARKET_STREAM_STATUS_DEGRADED, MARKET_STREAM_STATUS_UNAVAILABLE},
        "reason_code": reason_code,
    }


def replace_market_stream_state(payload: Mapping[str, Any]) -> dict[str, Any]:
    state = build_market_stream_state(payload)
    with _cache_lock:
        _market_stream_state.clear()
        _market_stream_state.update(state)
    return state


def get_market_stream_state() -> dict[str, Any]:
    with _cache_lock:
        return build_market_stream_state(dict(_market_stream_state))


def record_market_stream_event(payload: Mapping[str, Any], *, received_at: datetime | None = None) -> dict[str, Any]:
    event = normalize_market_stream_event(payload, received_at=received_at)
    now = _parse_datetime(event.get("received_at")) or utcnow_naive()
    with _cache_lock:
        state = build_market_stream_state(_market_stream_state, now=now)
        state["status"] = MARKET_STREAM_STATUS_CONNECTED
        state["stream_enabled"] = True
        state["last_event_at"] = now.isoformat()
        state["last_error"] = None
        state["heartbeat_ok"] = True
        if event.get("reason_code") == MARKET_STREAM_PARTIAL_REASON_CODE:
            state["partial_ignored_count"] = int(state.get("partial_ignored_count") or 0) + 1
        entry = _entry_from_normalized(event)
        if entry is not None:
            _closed_kline_cache[(entry.symbol, entry.timeframe)] = entry
            state["last_closed_kline_at"] = entry.received_at.isoformat()
            state["last_closed_kline_symbol"] = entry.symbol
            state["last_closed_kline_timeframe"] = entry.timeframe
            state["closed_kline_count"] = int(state.get("closed_kline_count") or 0) + 1
        _market_stream_state.clear()
        _market_stream_state.update(build_market_stream_state(state, now=now))
    return event


def get_cached_closed_kline(
    symbol: str,
    timeframe: str,
    *,
    now: datetime | None = None,
    stale_after_seconds: int | None = None,
) -> ClosedKlineCacheEntry | None:
    symbol_key = str(symbol or "").upper()
    timeframe_key = str(timeframe or "")
    with _cache_lock:
        entry = _closed_kline_cache.get((symbol_key, timeframe_key))
    if entry is None:
        return None
    resolved_stale_after = stale_after_seconds or market_stream_stale_after_seconds(timeframe_key)
    if entry.freshness_seconds(now) > resolved_stale_after:
        return None
    return entry


def clear_market_stream_cache() -> None:
    with _cache_lock:
        _closed_kline_cache.clear()
        _market_stream_state.clear()


def _next_backoff_seconds(reconnect_count: int) -> float:
    if reconnect_count <= 0:
        return 0.0
    return min(float(2 ** (reconnect_count - 1)), 30.0)


class BinanceMarketStreamListener:
    def __init__(
        self,
        *,
        symbols: Sequence[str],
        timeframes: Sequence[str],
        testnet_enabled: bool = False,
        now_fn=utcnow_naive,
        sleep_fn=asyncio.sleep,
    ) -> None:
        self._symbols = [str(symbol or "").upper() for symbol in symbols if str(symbol or "").strip()]
        self._timeframes = [str(timeframe or "") for timeframe in timeframes if str(timeframe or "").strip()]
        self._testnet_enabled = testnet_enabled
        self._now = now_fn
        self._sleep = sleep_fn

    @property
    def stream_names(self) -> list[str]:
        return build_market_stream_names(self._symbols, self._timeframes)

    async def collect_once(
        self,
        *,
        max_events: int | None = None,
        idle_timeout_seconds: float = 30.0,
        state: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        stream_names = self.stream_names
        if not stream_names:
            unavailable = build_market_stream_state(
                {
                    **dict(state or {}),
                    "status": MARKET_STREAM_STATUS_UNAVAILABLE,
                    "stream_enabled": False,
                    "stream_running": False,
                    "last_error": "NO_MARKET_STREAMS_CONFIGURED",
                    "reason_code": "NO_MARKET_STREAMS_CONFIGURED",
                }
            )
            replace_market_stream_state(unavailable)
            return {"state": unavailable, "events": [], "issues": []}

        url = build_futures_market_stream_url(
            self._symbols,
            self._timeframes,
            testnet_enabled=self._testnet_enabled,
        )
        current_state = build_market_stream_state(
            {
                **dict(state or {}),
                "status": MARKET_STREAM_STATUS_CONNECTED,
                "source": MARKET_STREAM_SOURCE,
                "stream_source": MARKET_STREAM_DATA_SOURCE,
                "stream_enabled": True,
                "subscribed_symbols": self._symbols,
                "subscribed_timeframes": self._timeframes,
                "stream_count": len(stream_names),
                "stale_after_seconds": min(
                    market_stream_stale_after_seconds(timeframe) for timeframe in self._timeframes
                ),
                "last_connected_at": self._now().isoformat(),
                "last_error": None,
                "heartbeat_ok": True,
            }
        )
        replace_market_stream_state(current_state)
        events: list[dict[str, Any]] = []
        issues: list[dict[str, Any]] = []
        received = 0
        try:
            async with websockets.connect(url, ping_interval=180, ping_timeout=600, close_timeout=5) as websocket:
                while max_events is None or received < max_events:
                    try:
                        message = await asyncio.wait_for(
                            websocket.recv(),
                            timeout=max(idle_timeout_seconds, 0.01),
                        )
                    except TimeoutError:
                        break
                    try:
                        raw_payload = json.loads(message)
                    except json.JSONDecodeError:
                        issues.append(
                            {
                                "severity": "warning",
                                "reason_code": MARKET_STREAM_INVALID_REASON_CODE,
                                "message": "Binance futures market stream returned invalid JSON.",
                            }
                        )
                        continue
                    if not isinstance(raw_payload, Mapping):
                        continue
                    event = record_market_stream_event(raw_payload, received_at=self._now())
                    events.append(event)
                    received += 1
            return {"state": get_market_stream_state(), "events": events, "issues": issues}
        except Exception as exc:
            reconnect_count = int(current_state.get("reconnect_count") or 0) + 1
            failed_at = self._now()
            degraded = build_market_stream_state(
                {
                    **current_state,
                    "status": MARKET_STREAM_STATUS_DEGRADED,
                    "last_disconnected_at": failed_at.isoformat(),
                    "last_error": str(exc),
                    "reconnect_count": reconnect_count,
                    "heartbeat_ok": False,
                    "reason_code": "MARKET_STREAM_CONNECTION_DROPPED",
                },
                now=failed_at,
            )
            replace_market_stream_state(degraded)
            issues.append(
                {
                    "severity": "warning",
                    "reason_code": "MARKET_STREAM_CONNECTION_DROPPED",
                    "message": "Binance futures market stream connection dropped.",
                    "payload": {"error": str(exc), "stream_count": len(stream_names)},
                }
            )
            return {"state": degraded, "events": events, "issues": issues}

    async def listen_forever(
        self,
        *,
        max_cycles: int | None = None,
        max_events_per_cycle: int | None = 256,
        idle_timeout_seconds: float = 30.0,
        state: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        current = build_market_stream_state(state or {})
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            result = await self.collect_once(
                max_events=max_events_per_cycle,
                idle_timeout_seconds=idle_timeout_seconds,
                state=current,
            )
            current = build_market_stream_state(result.get("state"))
            yield result
            cycles += 1
            if current["status"] == MARKET_STREAM_STATUS_DEGRADED:
                await self._sleep(_next_backoff_seconds(int(current.get("reconnect_count") or 0)))
