from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Any

try:
    from redis import Redis
except Exception:  # pragma: no cover - import failure is handled as cache unavailable.
    Redis = None  # type: ignore[assignment]

from trading_mvp.schemas import MarketCandle
from trading_mvp.time_utils import utcnow_naive

MARKET_DATA_CACHE_BACKEND_REDIS = "redis"
MARKET_DATA_CACHE_BACKEND_PROCESS_LOCAL = "process_local"
MARKET_DATA_CACHE_BACKEND_DISABLED = "disabled"
MARKET_DATA_CACHE_SCOPE_SHARED = "shared"
MARKET_DATA_CACHE_SCOPE_PROCESS_LOCAL = "process_local"
MARKET_DATA_CACHE_SCOPE_NONE = "none"
MARKET_DATA_CACHE_MARKET_TYPE_USDM_FUTURES = "usd_m_futures"
MARKET_DATA_CACHE_ENV_MAINNET = "mainnet"
MARKET_DATA_CACHE_ENV_TESTNET = "testnet"
MARKET_DATA_CACHE_KEY_PREFIX = "trading_mvp:public_market_data:binance"
MARKET_DATA_CACHE_MIN_TTL_SECONDS = 120
MARKET_DATA_CACHE_SOURCE = "binance_ws_final_kline"
MARKET_DATA_CACHE_PARTIAL_IGNORED = "partial_ignored"


@dataclass(frozen=True, slots=True)
class SharedClosedKlineCacheEntry:
    symbol: str
    timeframe: str
    candle: MarketCandle
    event_time: datetime | None
    close_time: datetime | None
    received_at: datetime
    stream_name: str | None = None
    market_type: str = MARKET_DATA_CACHE_MARKET_TYPE_USDM_FUTURES
    environment: str = MARKET_DATA_CACHE_ENV_MAINNET
    cache_backend: str = MARKET_DATA_CACHE_BACKEND_REDIS
    cache_scope: str = MARKET_DATA_CACHE_SCOPE_SHARED

    def freshness_seconds(self, now: datetime | None = None) -> int:
        resolved_now = now or utcnow_naive()
        return max(int((resolved_now - self.received_at).total_seconds()), 0)

    def as_source_detail(self, *, now: datetime | None = None, stale_after_seconds: int | None = None) -> dict[str, Any]:
        resolved_stale_after = stale_after_seconds or market_data_cache_ttl_seconds(self.timeframe)
        freshness_seconds = self.freshness_seconds(now)
        return {
            "source": MARKET_DATA_CACHE_SOURCE,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "cache_market_type": self.market_type,
            "cache_environment": self.environment,
            "stream_name": self.stream_name,
            "event_time": self.event_time.isoformat() if self.event_time is not None else None,
            "close_time": self.close_time.isoformat() if self.close_time is not None else None,
            "source_time": (self.close_time or self.event_time or self.received_at).isoformat(),
            "received_at": self.received_at.isoformat(),
            "freshness_seconds": freshness_seconds,
            "age_seconds": freshness_seconds,
            "stale_after_seconds": resolved_stale_after,
            "stale": freshness_seconds > resolved_stale_after,
            "partial_candle_used": False,
            "cache_backend": self.cache_backend,
            "cache_scope": self.cache_scope,
            "shared_cache_supported": self.cache_scope == MARKET_DATA_CACHE_SCOPE_SHARED,
        }


@dataclass(frozen=True, slots=True)
class SharedMarketCacheRead:
    entry: SharedClosedKlineCacheEntry | None
    state: dict[str, Any]


def _as_mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _coerce_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def market_data_cache_ttl_seconds(timeframe: str) -> int:
    value = str(timeframe or "").strip()
    try:
        if value.endswith("m"):
            return max(int(value[:-1]) * 60 * 2, MARKET_DATA_CACHE_MIN_TTL_SECONDS)
        if value.endswith("h"):
            return max(int(value[:-1]) * 60 * 60 * 2, MARKET_DATA_CACHE_MIN_TTL_SECONDS)
        if value.endswith("d"):
            return max(int(value[:-1]) * 24 * 60 * 60 * 2, MARKET_DATA_CACHE_MIN_TTL_SECONDS)
    except ValueError:
        return MARKET_DATA_CACHE_MIN_TTL_SECONDS
    return MARKET_DATA_CACHE_MIN_TTL_SECONDS


def normalize_market_data_cache_environment(environment: str | None = None, *, testnet_enabled: bool = False) -> str:
    value = str(environment or "").strip().lower()
    if value in {MARKET_DATA_CACHE_ENV_MAINNET, "live", "prod", "production"}:
        return MARKET_DATA_CACHE_ENV_MAINNET
    if value in {MARKET_DATA_CACHE_ENV_TESTNET, "test"}:
        return MARKET_DATA_CACHE_ENV_TESTNET
    return MARKET_DATA_CACHE_ENV_TESTNET if testnet_enabled else MARKET_DATA_CACHE_ENV_MAINNET


def market_data_cache_key(
    symbol: str,
    timeframe: str,
    *,
    market_type: str = MARKET_DATA_CACHE_MARKET_TYPE_USDM_FUTURES,
    environment: str = MARKET_DATA_CACHE_ENV_MAINNET,
) -> str:
    return ":".join(
        [
            MARKET_DATA_CACHE_KEY_PREFIX,
            str(market_type or MARKET_DATA_CACHE_MARKET_TYPE_USDM_FUTURES),
            normalize_market_data_cache_environment(environment),
            "kline",
            "closed",
            str(symbol or "").upper(),
            str(timeframe or ""),
        ]
    )


def build_shared_market_cache_state(
    payload: Mapping[str, Any] | None = None,
    *,
    backend: str = MARKET_DATA_CACHE_BACKEND_REDIS,
) -> dict[str, Any]:
    data = dict(payload or {})
    cache_backend = str(data.get("cache_backend") or backend)
    shared = cache_backend == MARKET_DATA_CACHE_BACKEND_REDIS
    cache_health = str(data.get("cache_health") or "unknown")
    cache_reject_reason = str(data.get("cache_reject_reason") or "") or None
    configured_cache_backend = str(data.get("configured_cache_backend") or cache_backend)
    if cache_health == "disabled" or cache_reject_reason == "cache_disabled":
        configured_cache_backend = str(data.get("configured_cache_backend") or MARKET_DATA_CACHE_BACKEND_DISABLED)
    redis_configured = bool(
        data.get(
            "redis_configured",
            configured_cache_backend == MARKET_DATA_CACHE_BACKEND_REDIS
            or cache_backend == MARKET_DATA_CACHE_BACKEND_REDIS,
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
    return {
        "cache_backend": cache_backend,
        "configured_cache_backend": configured_cache_backend,
        "cache_market_type": str(data.get("cache_market_type") or MARKET_DATA_CACHE_MARKET_TYPE_USDM_FUTURES),
        "cache_environment": normalize_market_data_cache_environment(
            str(data.get("cache_environment") or MARKET_DATA_CACHE_ENV_MAINNET)
        ),
        "cache_scope": str(
            data.get("cache_scope")
            or (MARKET_DATA_CACHE_SCOPE_SHARED if shared else MARKET_DATA_CACHE_SCOPE_PROCESS_LOCAL)
        ),
        "shared_cache_supported": bool(data.get("shared_cache_supported", shared)),
        "redis_required": redis_required,
        "redis_configured": redis_configured,
        "redis_connected": redis_connected,
        "cache_health": cache_health,
        "cache_reject_reason": cache_reject_reason,
        "cache_write_status": str(data.get("cache_write_status") or "") or None,
        "last_shared_cache_read_at": str(data.get("last_shared_cache_read_at") or "") or None,
        "last_shared_cache_write_at": str(data.get("last_shared_cache_write_at") or "") or None,
        "last_shared_cache_error": str(data.get("last_shared_cache_error") or "") or None,
        "shared_cache_key": str(data.get("shared_cache_key") or "") or None,
    }


def _redis_client(redis_url: str):
    if Redis is None:
        raise RuntimeError("redis package unavailable")
    return Redis.from_url(
        redis_url,
        socket_connect_timeout=0.25,
        socket_timeout=0.25,
        decode_responses=True,
    )


def _entry_from_payload(payload: Mapping[str, Any]) -> SharedClosedKlineCacheEntry | None:
    if payload.get("closed") is False:
        return None
    candle_payload = _as_mapping(payload.get("candle"))
    timestamp = _parse_datetime(candle_payload.get("timestamp"))
    open_price = _coerce_float(candle_payload.get("open"))
    high = _coerce_float(candle_payload.get("high"))
    low = _coerce_float(candle_payload.get("low"))
    close = _coerce_float(candle_payload.get("close"))
    volume = _coerce_float(candle_payload.get("volume"))
    if None in {timestamp, open_price, high, low, close, volume}:
        return None
    if not (
        float(open_price) > 0
        and float(high) > 0
        and float(low) > 0
        and float(close) > 0
        and float(volume) >= 0
    ):
        return None
    symbol = str(payload.get("symbol") or "").upper()
    timeframe = str(payload.get("timeframe") or "")
    if not symbol or not timeframe:
        return None
    environment = normalize_market_data_cache_environment(str(payload.get("cache_environment") or ""))
    return SharedClosedKlineCacheEntry(
        symbol=symbol,
        timeframe=timeframe,
        candle=MarketCandle(
            timestamp=timestamp,
            open=float(open_price),
            high=float(high),
            low=float(low),
            close=float(close),
            volume=float(volume),
        ),
        event_time=_parse_datetime(payload.get("event_time")),
        close_time=_parse_datetime(payload.get("close_time")),
        received_at=_parse_datetime(payload.get("received_at")) or utcnow_naive(),
        stream_name=str(payload.get("stream_name") or "") or None,
        market_type=str(payload.get("cache_market_type") or MARKET_DATA_CACHE_MARKET_TYPE_USDM_FUTURES),
        environment=environment,
        cache_backend=str(payload.get("cache_backend") or MARKET_DATA_CACHE_BACKEND_REDIS),
        cache_scope=str(payload.get("cache_scope") or MARKET_DATA_CACHE_SCOPE_SHARED),
    )


def _entry_from_normalized_event(event: Mapping[str, Any]) -> SharedClosedKlineCacheEntry | None:
    if event.get("event_category") != "market_kline" or not bool(event.get("closed")):
        return None
    return _entry_from_payload(
        {
            "symbol": event.get("symbol"),
            "timeframe": event.get("timeframe"),
            "stream_name": event.get("stream_name"),
            "event_time": event.get("event_time"),
            "close_time": event.get("close_time"),
            "received_at": event.get("received_at"),
            "candle": event.get("candle"),
            "closed": True,
            "cache_market_type": event.get("cache_market_type") or MARKET_DATA_CACHE_MARKET_TYPE_USDM_FUTURES,
            "cache_environment": event.get("cache_environment") or MARKET_DATA_CACHE_ENV_MAINNET,
            "cache_backend": MARKET_DATA_CACHE_BACKEND_REDIS,
            "cache_scope": MARKET_DATA_CACHE_SCOPE_SHARED,
        }
    )


def _serialize_entry(entry: SharedClosedKlineCacheEntry) -> str:
    return json.dumps(
        {
            "symbol": entry.symbol,
            "timeframe": entry.timeframe,
            "stream_name": entry.stream_name,
            "event_time": entry.event_time.isoformat() if entry.event_time is not None else None,
            "close_time": entry.close_time.isoformat() if entry.close_time is not None else None,
            "received_at": entry.received_at.isoformat(),
            "closed": True,
            "cache_market_type": entry.market_type,
            "cache_environment": entry.environment,
            "cache_backend": entry.cache_backend,
            "cache_scope": entry.cache_scope,
            "candle": entry.candle.model_dump(mode="json"),
        },
        sort_keys=True,
    )


def write_closed_kline_event_to_redis(
    event: Mapping[str, Any],
    *,
    redis_url: str,
    market_type: str = MARKET_DATA_CACHE_MARKET_TYPE_USDM_FUTURES,
    environment: str = MARKET_DATA_CACHE_ENV_MAINNET,
    stale_after_seconds: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_time = now or utcnow_naive()
    normalized_environment = normalize_market_data_cache_environment(environment)
    event_with_namespace = {
        **dict(event),
        "cache_market_type": market_type,
        "cache_environment": normalized_environment,
    }
    entry = _entry_from_normalized_event(event_with_namespace)
    if entry is None:
        return build_shared_market_cache_state(
            {
                "cache_health": "ok",
                "cache_market_type": market_type,
                "cache_environment": normalized_environment,
                "cache_write_status": MARKET_DATA_CACHE_PARTIAL_IGNORED,
                "last_shared_cache_write_at": current_time.isoformat(),
            }
        )
    key = market_data_cache_key(entry.symbol, entry.timeframe, market_type=market_type, environment=normalized_environment)
    ttl_seconds = max(int(stale_after_seconds or market_data_cache_ttl_seconds(entry.timeframe)), 1)
    if not redis_url:
        return build_shared_market_cache_state(
            {
                "cache_health": "disabled",
                "cache_reject_reason": "cache_disabled",
                "cache_market_type": market_type,
                "cache_environment": normalized_environment,
                "cache_write_status": "failed",
                "last_shared_cache_write_at": current_time.isoformat(),
                "last_shared_cache_error": "REDIS_URL not configured",
                "shared_cache_key": key,
            }
        )
    try:
        _redis_client(redis_url).set(key, _serialize_entry(entry), ex=ttl_seconds)
    except Exception as exc:
        return build_shared_market_cache_state(
            {
                "cache_health": "unavailable",
                "cache_reject_reason": "cache_unavailable",
                "cache_market_type": market_type,
                "cache_environment": normalized_environment,
                "cache_write_status": "failed",
                "last_shared_cache_write_at": current_time.isoformat(),
                "last_shared_cache_error": str(exc),
                "shared_cache_key": key,
            }
        )
    return build_shared_market_cache_state(
        {
            "cache_health": "ok",
            "cache_market_type": market_type,
            "cache_environment": normalized_environment,
            "cache_write_status": "stored",
            "last_shared_cache_write_at": current_time.isoformat(),
            "last_shared_cache_error": None,
            "shared_cache_key": key,
        }
    )


def read_closed_kline_from_redis(
    symbol: str,
    timeframe: str,
    *,
    redis_url: str,
    market_type: str = MARKET_DATA_CACHE_MARKET_TYPE_USDM_FUTURES,
    environment: str = MARKET_DATA_CACHE_ENV_MAINNET,
    now: datetime | None = None,
    stale_after_seconds: int | None = None,
) -> SharedMarketCacheRead:
    current_time = now or utcnow_naive()
    normalized_environment = normalize_market_data_cache_environment(environment)
    normalized_symbol = str(symbol or "").upper()
    normalized_timeframe = str(timeframe or "")
    key = market_data_cache_key(
        normalized_symbol,
        normalized_timeframe,
        market_type=market_type,
        environment=normalized_environment,
    )
    base_state = {
        "cache_market_type": market_type,
        "cache_environment": normalized_environment,
        "last_shared_cache_read_at": current_time.isoformat(),
        "shared_cache_key": key,
    }
    if not redis_url:
        return SharedMarketCacheRead(
            None,
            build_shared_market_cache_state(
                {
                    **base_state,
                    "cache_health": "disabled",
                    "cache_reject_reason": "cache_disabled",
                    "configured_cache_backend": MARKET_DATA_CACHE_BACKEND_DISABLED,
                    "redis_configured": False,
                    "redis_connected": False,
                    "last_shared_cache_error": "REDIS_URL not configured",
                }
            ),
        )
    try:
        raw_payload = _redis_client(redis_url).get(key)
    except Exception as exc:
        return SharedMarketCacheRead(
            None,
            build_shared_market_cache_state(
                {
                    **base_state,
                    "cache_health": "unavailable",
                    "cache_reject_reason": "cache_unavailable",
                    "last_shared_cache_error": str(exc),
                }
            ),
        )
    if not raw_payload:
        return SharedMarketCacheRead(
            None,
            build_shared_market_cache_state({**base_state, "cache_health": "miss", "cache_reject_reason": "cache_miss"}),
        )
    if isinstance(raw_payload, bytes):
        raw_payload = raw_payload.decode("utf-8")
    try:
        payload = json.loads(str(raw_payload))
    except json.JSONDecodeError as exc:
        return SharedMarketCacheRead(
            None,
            build_shared_market_cache_state(
                {
                    **base_state,
                    "cache_health": "error",
                    "cache_reject_reason": "cache_payload_corrupt",
                    "last_shared_cache_error": str(exc),
                }
            ),
        )
    entry = _entry_from_payload(_as_mapping(payload))
    if entry is None:
        return SharedMarketCacheRead(
            None,
            build_shared_market_cache_state(
                {
                    **base_state,
                    "cache_health": "error",
                    "cache_reject_reason": "cache_payload_invalid",
                    "last_shared_cache_error": "invalid closed kline payload",
                }
            ),
        )
    mismatches: list[str] = []
    if entry.symbol != normalized_symbol:
        mismatches.append("symbol")
    if entry.timeframe != normalized_timeframe:
        mismatches.append("timeframe")
    if entry.market_type != market_type:
        mismatches.append("market_type")
    if entry.environment != normalized_environment:
        mismatches.append("environment")
    if mismatches:
        return SharedMarketCacheRead(
            None,
            build_shared_market_cache_state(
                {
                    **base_state,
                    "cache_health": "error",
                    "cache_reject_reason": "cache_metadata_mismatch",
                    "last_shared_cache_error": f"cache metadata mismatch: {','.join(mismatches)}",
                }
            ),
        )
    resolved_stale_after = stale_after_seconds or market_data_cache_ttl_seconds(timeframe)
    if entry.freshness_seconds(current_time) > resolved_stale_after:
        return SharedMarketCacheRead(
            None,
            build_shared_market_cache_state(
                {
                    **base_state,
                    "cache_health": "stale",
                    "cache_reject_reason": "cache_stale",
                    "last_shared_cache_error": None,
                }
            ),
        )
    return SharedMarketCacheRead(
        entry,
        build_shared_market_cache_state(
            {
                **base_state,
                "cache_health": "ok",
                "last_shared_cache_error": None,
            }
        ),
    )
