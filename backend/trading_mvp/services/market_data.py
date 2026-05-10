from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import sin

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from trading_mvp.config import get_settings
from trading_mvp.models import MarketSnapshot
from trading_mvp.schemas import (
    DerivativesContextPayload,
    EventContextPayload,
    MarketCandle,
    MarketSnapshotPayload,
)
from trading_mvp.services.binance import BinanceClient
from trading_mvp.services.binance_market_stream import (
    MARKET_STREAM_DATA_SOURCE,
    MARKET_STREAM_REST_FALLBACK_SOURCE,
    get_cached_closed_kline,
    get_market_stream_state,
    market_stream_stale_after_seconds,
)
from trading_mvp.services.event_context import EventContextProvider, build_event_context
from trading_mvp.services.market_data_cache import (
    MARKET_DATA_CACHE_BACKEND_PROCESS_LOCAL,
    MARKET_DATA_CACHE_ENV_MAINNET,
    MARKET_DATA_CACHE_ENV_TESTNET,
    MARKET_DATA_CACHE_SCOPE_PROCESS_LOCAL,
    build_shared_market_cache_state,
    normalize_market_data_cache_environment,
    read_closed_kline_from_redis,
)
from trading_mvp.time_utils import utcnow_naive

DEFAULT_CONTEXT_TIMEFRAMES = ("1h", "4h")
DEFAULT_MARKET_SNAPSHOT_LOOKBACK = 120
CONTEXT_TIMEFRAME_STALE_GRACE_SECONDS = 300
LEAD_MARKET_SYMBOLS = ("BTCUSDT", "ETHUSDT")
LEAD_CONTEXT_OK = "ok"
LEAD_CONTEXT_PARTIAL = "partial"
LEAD_CONTEXT_UNAVAILABLE = "unavailable"
LEAD_CONTEXT_PARTIAL_REASON_CODE = "LEAD_CONTEXT_PARTIAL"
LEAD_CONTEXT_UNAVAILABLE_REASON_CODE = "LEAD_CONTEXT_UNAVAILABLE"
LEAD_CONTEXT_BUILD_FAILED_REASON_CODE = "LEAD_CONTEXT_BUILD_FAILED"

DERIVATIVES_CONTEXT_FIELDS = (
    "open_interest",
    "open_interest_change_pct",
    "funding_rate",
    "taker_buy_sell_imbalance",
    "perp_basis_bps",
    "crowding_bias",
    "top_trader_long_short_ratio",
    "spread_bps",
    "spread_stress_score",
)


class LeadMarketContexts(dict[str, dict[str, MarketSnapshotPayload]]):
    def __init__(
        self,
        values: dict[str, dict[str, MarketSnapshotPayload]] | None = None,
        *,
        metadata: dict[str, object] | None = None,
    ) -> None:
        super().__init__(values or {})
        self.metadata = metadata or {}

    @property
    def lead_context_status(self) -> str:
        return str(self.metadata.get("lead_context_status") or LEAD_CONTEXT_UNAVAILABLE)

    @property
    def missing_lead_symbols(self) -> list[str]:
        value = self.metadata.get("missing_lead_symbols")
        return [str(item) for item in value] if isinstance(value, list) else []

    @property
    def reason_codes(self) -> list[str]:
        value = self.metadata.get("reason_codes")
        return [str(item) for item in value] if isinstance(value, list) else []


def _unique_strings(values: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        ordered.append(item)
        seen.add(item)
    return ordered


def _lead_market_context_metadata(
    *,
    requested_symbols: list[str],
    available_symbols: list[str],
    failed_symbols: list[str],
) -> dict[str, object]:
    missing_symbols = [symbol for symbol in requested_symbols if symbol not in available_symbols]
    if not requested_symbols or not missing_symbols:
        status = LEAD_CONTEXT_OK
    elif not available_symbols:
        status = LEAD_CONTEXT_UNAVAILABLE
    else:
        status = LEAD_CONTEXT_PARTIAL

    reason_codes: list[str] = []
    if status == LEAD_CONTEXT_PARTIAL:
        reason_codes.append(LEAD_CONTEXT_PARTIAL_REASON_CODE)
    elif status == LEAD_CONTEXT_UNAVAILABLE:
        reason_codes.append(LEAD_CONTEXT_UNAVAILABLE_REASON_CODE)
    if failed_symbols:
        reason_codes.append(LEAD_CONTEXT_BUILD_FAILED_REASON_CODE)
    reason_codes.extend(f"LEAD_CONTEXT_MISSING_{symbol}" for symbol in missing_symbols)

    return {
        "lead_context_status": status,
        "missing_lead_symbols": missing_symbols,
        "available_lead_symbols": available_symbols,
        "failed_lead_symbols": failed_symbols,
        "reason_codes": _unique_strings(reason_codes),
    }


def timeframe_to_minutes(timeframe: str) -> int:
    if timeframe.endswith("m"):
        return int(timeframe[:-1])
    if timeframe.endswith("h"):
        return int(timeframe[:-1]) * 60
    raise ValueError(f"Unsupported timeframe: {timeframe}")


def _context_stale_threshold_seconds(timeframe: str, configured_threshold_seconds: int) -> int:
    try:
        timeframe_seconds = timeframe_to_minutes(timeframe) * 60
    except (AttributeError, TypeError, ValueError):
        return configured_threshold_seconds
    return max(configured_threshold_seconds, timeframe_seconds + CONTEXT_TIMEFRAME_STALE_GRACE_SECONDS)


def generate_seed_candles(symbol: str, timeframe: str, points: int = 160) -> list[MarketCandle]:
    interval_minutes = timeframe_to_minutes(timeframe)
    presets = {
        "BTCUSDT": (64000.0, 18.0, 420.0, 1500.0),
        "ETHUSDT": (3200.0, 1.6, 28.0, 9000.0),
        "SOLUSDT": (145.0, 0.15, 4.8, 42000.0),
        "XRPUSDT": (0.62, 0.0012, 0.025, 180000.0),
        "BNBUSDT": (580.0, 0.55, 12.0, 12000.0),
    }
    base_price, drift, volatility, base_volume = presets.get(symbol, (100.0, 0.08, 3.5, 25000.0))
    start = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=interval_minutes * points)

    candles: list[MarketCandle] = []
    previous_close = base_price
    for index in range(points):
        timestamp = start + timedelta(minutes=interval_minutes * index)
        wave = sin(index / 3.7) * volatility
        retrace = sin(index / 11.0) * (volatility * 0.45)
        close = max(1.0, base_price + (drift * index) + wave - retrace)
        open_price = previous_close
        high = max(open_price, close) * 1.0035
        low = min(open_price, close) * 0.9965
        volume = base_volume + ((index % 12) * 115.0) + abs(sin(index / 2.5)) * 500.0
        candles.append(
            MarketCandle(
                timestamp=timestamp.replace(tzinfo=None),
                open=round(open_price, 2),
                high=round(high, 2),
                low=round(low, 2),
                close=round(close, 2),
                volume=round(volume, 2),
            )
        )
        previous_close = close
    return candles


def _seed_derivatives_context() -> DerivativesContextPayload:
    return DerivativesContextPayload(source="seed_fallback", fallback_used=True, fetch_failed=False)


def _compute_spread_stress_score(
    *,
    spread_bps: object,
    top_bid_size: object,
    top_ask_size: object,
) -> float | None:
    try:
        normalized_spread_bps = float(spread_bps)
    except (TypeError, ValueError):
        return None
    if normalized_spread_bps < 0:
        return None
    stress_score = normalized_spread_bps / 4.0
    try:
        bid_size = float(top_bid_size) if top_bid_size not in {None, ""} else None
        ask_size = float(top_ask_size) if top_ask_size not in {None, ""} else None
    except (TypeError, ValueError):
        bid_size = None
        ask_size = None
    if bid_size is not None and ask_size is not None and bid_size > 0 and ask_size > 0:
        smaller = min(bid_size, ask_size)
        larger = max(bid_size, ask_size)
        imbalance = (larger - smaller) / larger if larger > 0 else 0.0
        stress_score *= 1.0 + min(imbalance, 1.0) * 0.25
        if smaller / larger <= 0.35:
            stress_score += 0.35
    return round(max(stress_score, 0.0), 4)


def _build_derivatives_context(client: BinanceClient, symbol: str) -> DerivativesContextPayload:
    payload: dict[str, object] = {
        "source": "binance_public",
        "fallback_used": False,
        "fetch_failed": False,
        "open_interest": None,
        "open_interest_change_pct": None,
        "funding_rate": None,
        "taker_buy_sell_imbalance": None,
        "perp_basis_bps": None,
        "crowding_bias": None,
        "top_trader_long_short_ratio": None,
        "best_bid": None,
        "best_ask": None,
        "spread_bps": None,
        "spread_stress_score": None,
    }
    fetch_failed = False
    try:
        payload["open_interest"] = client.get_open_interest(symbol)
    except Exception:
        fetch_failed = True
    try:
        payload["open_interest_change_pct"] = client.get_open_interest_change_pct(symbol, period="5m")
    except Exception:
        fetch_failed = True
    try:
        premium = client.get_premium_index(symbol)
        payload["funding_rate"] = premium.get("funding_rate")
        payload["perp_basis_bps"] = premium.get("perp_basis_bps")
    except Exception:
        fetch_failed = True
    try:
        payload["taker_buy_sell_imbalance"] = client.get_taker_buy_sell_imbalance(symbol, period="5m")
    except Exception:
        fetch_failed = True
    try:
        payload["crowding_bias"] = client.get_crowding_bias(symbol, period="5m")
    except Exception:
        fetch_failed = True
    try:
        payload["top_trader_long_short_ratio"] = client.get_top_trader_long_short_ratio(symbol, period="5m")
    except Exception:
        fetch_failed = True
    try:
        best_bid_ask = client.get_best_bid_ask(symbol, limit=5)
        payload["best_bid"] = best_bid_ask.get("best_bid")
        payload["best_ask"] = best_bid_ask.get("best_ask")
        payload["spread_bps"] = best_bid_ask.get("spread_bps")
        payload["spread_stress_score"] = _compute_spread_stress_score(
            spread_bps=best_bid_ask.get("spread_bps"),
            top_bid_size=best_bid_ask.get("top_bid_size"),
            top_ask_size=best_bid_ask.get("top_ask_size"),
        )
    except Exception:
        fetch_failed = True
    payload["fetch_failed"] = fetch_failed
    if not any(payload[field] is not None for field in DERIVATIVES_CONTEXT_FIELDS):
        return DerivativesContextPayload(
            source="unavailable",
            fallback_used=True,
            fetch_failed=fetch_failed,
        )
    return DerivativesContextPayload(**payload)


def _build_seed_snapshot(
    symbol: str,
    timeframe: str,
    lookback: int,
    upto_index: int | None,
    force_stale: bool,
    *,
    derivatives_context: DerivativesContextPayload | None = None,
) -> MarketSnapshotPayload:
    series = generate_seed_candles(symbol=symbol, timeframe=timeframe)
    if upto_index is None:
        upto_index = len(series) - 1
    upto_index = max(lookback - 1, min(upto_index, len(series) - 1))
    candles = series[max(0, upto_index - lookback + 1) : upto_index + 1]
    latest = candles[-1]
    snapshot_time = latest.timestamp
    if not force_stale:
        snapshot_time = utcnow_naive()
        delta = snapshot_time - latest.timestamp
        candles = [
            candle.model_copy(update={"timestamp": candle.timestamp + delta})  # type: ignore[operator]
            for candle in candles
        ]
        latest = candles[-1]
    return MarketSnapshotPayload(
        symbol=symbol,
        timeframe=timeframe,
        snapshot_time=snapshot_time,
        latest_price=latest.close,
        latest_volume=latest.volume,
        candle_count=len(candles),
        is_stale=force_stale,
        is_complete=len(candles) >= min(lookback, 20),
        source="seed_fallback",
        source_status="stale" if force_stale else "fresh",
        source_detail={"fallback_used": True, "partial_candle_used": False},
        candles=candles,
        derivatives_context=derivatives_context or _seed_derivatives_context(),
    )


def _apply_closed_stream_kline(
    candles: list[MarketCandle],
    stream_candle: MarketCandle,
    *,
    lookback: int,
) -> list[MarketCandle]:
    if not candles:
        return [stream_candle]
    latest = candles[-1]
    if stream_candle.timestamp < latest.timestamp:
        return candles
    patched = list(candles)
    if stream_candle.timestamp == latest.timestamp:
        patched[-1] = stream_candle
    else:
        patched.append(stream_candle)
    return patched[-lookback:]


def _copy_cache_state_fields(target: dict[str, object], cache_state: dict[str, object]) -> dict[str, object]:
    for key in (
        "cache_backend",
        "configured_cache_backend",
        "cache_market_type",
        "cache_environment",
        "cache_health",
        "cache_reject_reason",
        "cache_scope",
        "shared_cache_supported",
        "redis_required",
        "redis_configured",
        "redis_connected",
        "cache_write_status",
        "last_shared_cache_read_at",
        "last_shared_cache_write_at",
        "last_shared_cache_error",
        "shared_cache_key",
    ):
        value = cache_state.get(key)
        if value not in {None, ""}:
            target[key] = value
    shared_cache_state = cache_state.get("shared_cache_state")
    if isinstance(shared_cache_state, dict):
        target["shared_cache_state"] = shared_cache_state
    return target


def _active_snapshot_source_for_stream_entry(cache_state: dict[str, object]) -> str:
    if (
        str(cache_state.get("cache_backend") or "") == "redis"
        and str(cache_state.get("cache_scope") or "") == "shared"
        and str(cache_state.get("cache_health") or "") == "ok"
    ):
        return "redis"
    return MARKET_STREAM_DATA_SOURCE


def _market_stream_fallback_reason(stream_state: dict[str, object]) -> str:
    cache_reject_reason = str(stream_state.get("cache_reject_reason") or "")
    if cache_reject_reason == "cache_metadata_mismatch":
        return "market_stream_cache_metadata_mismatch"
    if cache_reject_reason == "cache_payload_corrupt":
        return "market_stream_cache_corrupt"
    if cache_reject_reason == "cache_payload_invalid":
        return "market_stream_cache_invalid"
    if cache_reject_reason == "cache_disabled":
        return "market_stream_cache_disabled"
    cache_health = str(stream_state.get("cache_health") or "")
    if cache_health == "stale":
        return "market_stream_cache_stale"
    if cache_health == "unavailable":
        return "market_stream_cache_unavailable"
    if cache_health == "error":
        return "market_stream_cache_error"
    if bool(stream_state.get("stale")):
        return "market_stream_stale"
    return "market_stream_missing_or_stale"


def _resolve_closed_stream_entry(
    symbol: str,
    timeframe: str,
    *,
    snapshot_time: datetime,
    stale_after_seconds: int,
    testnet_enabled: bool,
) -> tuple[object | None, dict[str, object], dict[str, object]]:
    environment = normalize_market_data_cache_environment(
        MARKET_DATA_CACHE_ENV_TESTNET if testnet_enabled else MARKET_DATA_CACHE_ENV_MAINNET
    )
    redis_url = str(get_settings().redis_url or "").strip()
    shared_cache_read = read_closed_kline_from_redis(
        symbol,
        timeframe,
        redis_url=redis_url,
        environment=environment,
        now=snapshot_time,
        stale_after_seconds=stale_after_seconds,
    )
    cache_state = dict(shared_cache_read.state)
    stream_entry: object | None = shared_cache_read.entry
    if stream_entry is None:
        local_entry = get_cached_closed_kline(
            symbol,
            timeframe,
            now=snapshot_time,
            stale_after_seconds=stale_after_seconds,
        )
        if local_entry is not None:
            configured_cache_backend = (
                shared_cache_read.state.get("configured_cache_backend")
                or shared_cache_read.state.get("cache_backend")
                or MARKET_DATA_CACHE_BACKEND_PROCESS_LOCAL
            )
            cache_state = {
                **build_shared_market_cache_state(
                    {"cache_health": "ok"},
                    backend=MARKET_DATA_CACHE_BACKEND_PROCESS_LOCAL,
                ),
                "configured_cache_backend": configured_cache_backend,
                "redis_required": False,
                "redis_configured": bool(shared_cache_read.state.get("redis_configured", False)),
                "redis_connected": shared_cache_read.state.get("redis_connected"),
                "cache_scope": MARKET_DATA_CACHE_SCOPE_PROCESS_LOCAL,
                "shared_cache_state": shared_cache_read.state,
            }
            stream_entry = local_entry
    stream_state = {**get_market_stream_state(), **cache_state}
    return stream_entry, stream_state, cache_state


def _build_binance_snapshot(
    symbol: str,
    timeframe: str,
    lookback: int,
    *,
    testnet_enabled: bool,
    stale_threshold_seconds: int,
    derivatives_context: DerivativesContextPayload | None = None,
) -> MarketSnapshotPayload:
    client = BinanceClient(testnet_enabled=testnet_enabled, futures_enabled=True)
    snapshot_time = utcnow_naive()
    stream_stale_after_seconds = market_stream_stale_after_seconds(timeframe)
    stream_entry, stream_state, stream_cache_state = _resolve_closed_stream_entry(
        symbol,
        timeframe,
        snapshot_time=snapshot_time,
        stale_after_seconds=stream_stale_after_seconds,
        testnet_enabled=testnet_enabled,
    )
    try:
        candles = client.fetch_klines(symbol=symbol, interval=timeframe, limit=lookback)
    except Exception as exc:
        if stream_entry is None:
            raise
        latest = stream_entry.candle
        source_detail = stream_entry.as_source_detail(
            now=snapshot_time,
            stale_after_seconds=stream_stale_after_seconds,
        )
        source_detail.update(
            {
                "active_snapshot_source": _active_snapshot_source_for_stream_entry(stream_cache_state),
                "rest_bootstrap_used": False,
                "rest_fallback_failed": True,
                "rest_error": str(exc),
                "stream_only_incomplete": True,
                "used_fallback": False,
                "fallback_active": False,
                "fallback_reason": "rest_failed_stream_only_incomplete",
                "stale_reason": "market_snapshot_incomplete",
                "stream": stream_state,
            }
        )
        _copy_cache_state_fields(source_detail, stream_cache_state)
        staleness_seconds = (snapshot_time - latest.timestamp).total_seconds()
        return MarketSnapshotPayload(
            symbol=symbol,
            timeframe=timeframe,
            snapshot_time=snapshot_time,
            latest_price=latest.close,
            latest_volume=latest.volume,
            candle_count=1,
            is_stale=staleness_seconds > stale_threshold_seconds,
            is_complete=False,
            source=MARKET_STREAM_DATA_SOURCE,
            source_status="incomplete",
            source_detail=source_detail,
            candles=[latest],
            derivatives_context=DerivativesContextPayload(source="unavailable", fallback_used=True, fetch_failed=True),
        )
    rest_latest = candles[-1]
    rest_age_seconds = max(int((snapshot_time - rest_latest.timestamp).total_seconds()), 0)
    source = MARKET_STREAM_REST_FALLBACK_SOURCE
    source_status = "rest_fallback"
    fallback_reason = _market_stream_fallback_reason(stream_state)
    source_detail: dict[str, object] = {
        "source": MARKET_STREAM_REST_FALLBACK_SOURCE,
        "active_snapshot_source": MARKET_STREAM_REST_FALLBACK_SOURCE,
        "rest_bootstrap_used": True,
        "used_fallback": True,
        "fallback_active": True,
        "fallback_reason": fallback_reason,
        "stale_reason": fallback_reason,
        "rest_latest_candle_at": rest_latest.timestamp.isoformat(),
        "source_time": rest_latest.timestamp.isoformat(),
        "received_at": snapshot_time.isoformat(),
        "age_seconds": rest_age_seconds,
        "partial_candle_used": False,
        "stream": stream_state,
    }
    _copy_cache_state_fields(source_detail, stream_cache_state)
    if stream_entry is not None:
        stream_detail = stream_entry.as_source_detail(
            now=snapshot_time,
            stale_after_seconds=stream_stale_after_seconds,
        )
        _copy_cache_state_fields(stream_detail, stream_cache_state)
        if stream_entry.candle.timestamp >= rest_latest.timestamp:
            candles = _apply_closed_stream_kline(candles, stream_entry.candle, lookback=lookback)
            source = MARKET_STREAM_DATA_SOURCE
            source_status = "fresh"
            source_detail = {
                **stream_detail,
                "active_snapshot_source": _active_snapshot_source_for_stream_entry(stream_cache_state),
                "rest_bootstrap_used": True,
                "used_fallback": False,
                "fallback_active": False,
                "fallback_reason": None,
                "stale_reason": None,
                "rest_latest_candle_at": rest_latest.timestamp.isoformat(),
                "partial_candle_used": False,
                "stream": stream_state,
            }
        else:
            source_detail.update(
                {
                    "fallback_reason": "market_stream_older_than_rest",
                    "stale_reason": "market_stream_older_than_rest",
                    "stream": stream_detail,
                }
            )
    latest = candles[-1]
    staleness_seconds = (snapshot_time - latest.timestamp).total_seconds()
    snapshot_derivatives = derivatives_context or _build_derivatives_context(client, symbol)
    return MarketSnapshotPayload(
        symbol=symbol,
        timeframe=timeframe,
        snapshot_time=snapshot_time,
        latest_price=latest.close,
        latest_volume=latest.volume,
        candle_count=len(candles),
        is_stale=staleness_seconds > stale_threshold_seconds,
        is_complete=len(candles) >= min(lookback, 20),
        source=source,
        source_status=source_status,
        source_detail=source_detail,
        candles=candles,
        derivatives_context=snapshot_derivatives,
    )


def build_market_snapshot(
    symbol: str,
    timeframe: str,
    lookback: int = DEFAULT_MARKET_SNAPSHOT_LOOKBACK,
    upto_index: int | None = None,
    force_stale: bool = False,
    *,
    use_binance: bool = False,
    binance_testnet_enabled: bool = False,
    stale_threshold_seconds: int = 1800,
    derivatives_context_override: DerivativesContextPayload | None = None,
    event_context_override: EventContextPayload | None = None,
    event_context_provider: EventContextProvider | None = None,
) -> MarketSnapshotPayload:
    snapshot: MarketSnapshotPayload
    if upto_index is not None or force_stale:
        snapshot = _build_seed_snapshot(
            symbol,
            timeframe,
            lookback,
            upto_index,
            force_stale,
            derivatives_context=derivatives_context_override,
        )
    else:
        if not use_binance:
            raise RuntimeError("실거래 모드에서는 Binance 실데이터가 꺼져 있으면 시장 스냅샷을 만들 수 없습니다.")

        snapshot = _build_binance_snapshot(
            symbol=symbol,
            timeframe=timeframe,
            lookback=lookback,
            testnet_enabled=binance_testnet_enabled,
            stale_threshold_seconds=stale_threshold_seconds,
            derivatives_context=derivatives_context_override,
        )
    resolved_event_context = (
        event_context_override.model_copy(deep=True)
        if event_context_override is not None
        else build_event_context(
            symbol=snapshot.symbol,
            timeframe=snapshot.timeframe,
            generated_at=snapshot.snapshot_time,
            provider=event_context_provider,
        )
    )
    return snapshot.model_copy(update={"event_context": resolved_event_context})


def build_market_context(
    symbol: str,
    base_timeframe: str,
    *,
    context_timeframes: tuple[str, ...] = DEFAULT_CONTEXT_TIMEFRAMES,
    lookback: int = DEFAULT_MARKET_SNAPSHOT_LOOKBACK,
    upto_index: int | None = None,
    force_stale: bool = False,
    use_binance: bool = False,
    binance_testnet_enabled: bool = False,
    stale_threshold_seconds: int = 1800,
    event_context_provider: EventContextProvider | None = None,
) -> dict[str, MarketSnapshotPayload]:
    timeframes = [base_timeframe, *[item for item in context_timeframes if item != base_timeframe]]
    snapshots: dict[str, MarketSnapshotPayload] = {}
    derivatives_context: DerivativesContextPayload | None = None
    if upto_index is not None or force_stale:
        derivatives_context = _seed_derivatives_context()
    elif use_binance:
        derivatives_context = _build_derivatives_context(
            BinanceClient(testnet_enabled=binance_testnet_enabled, futures_enabled=True),
            symbol,
        )
    base_snapshot = build_market_snapshot(
        symbol=symbol,
        timeframe=base_timeframe,
        lookback=lookback,
        upto_index=upto_index,
        force_stale=force_stale,
        use_binance=use_binance,
        binance_testnet_enabled=binance_testnet_enabled,
        stale_threshold_seconds=stale_threshold_seconds,
        derivatives_context_override=derivatives_context,
        event_context_provider=event_context_provider,
    )
    snapshots[base_timeframe] = base_snapshot
    shared_event_context = base_snapshot.event_context.model_copy(deep=True)
    for timeframe in timeframes:
        if timeframe == base_timeframe:
            continue
        snapshots[timeframe] = build_market_snapshot(
            symbol=symbol,
            timeframe=timeframe,
            lookback=lookback,
            upto_index=upto_index,
            force_stale=force_stale,
            use_binance=use_binance,
            binance_testnet_enabled=binance_testnet_enabled,
            stale_threshold_seconds=_context_stale_threshold_seconds(timeframe, stale_threshold_seconds),
            derivatives_context_override=derivatives_context,
            event_context_override=shared_event_context,
        )
    return snapshots


def build_lead_market_contexts(
    base_timeframe: str,
    *,
    lead_symbols: tuple[str, ...] = LEAD_MARKET_SYMBOLS,
    context_timeframes: tuple[str, ...] = DEFAULT_CONTEXT_TIMEFRAMES,
    lookback: int = DEFAULT_MARKET_SNAPSHOT_LOOKBACK,
    upto_index: int | None = None,
    force_stale: bool = False,
    use_binance: bool = False,
    binance_testnet_enabled: bool = False,
    stale_threshold_seconds: int = 1800,
    event_context_provider: EventContextProvider | None = None,
) -> LeadMarketContexts:
    contexts: dict[str, dict[str, MarketSnapshotPayload]] = {}
    requested_symbols: list[str] = []
    failed_symbols: list[str] = []
    for symbol in lead_symbols:
        symbol_key = str(symbol or "").upper()
        if not symbol_key:
            continue
        if symbol_key not in requested_symbols:
            requested_symbols.append(symbol_key)
        try:
            contexts[symbol_key] = build_market_context(
                symbol=symbol_key,
                base_timeframe=base_timeframe,
                context_timeframes=context_timeframes,
                lookback=lookback,
                upto_index=upto_index,
                force_stale=force_stale,
                use_binance=use_binance,
                binance_testnet_enabled=binance_testnet_enabled,
                stale_threshold_seconds=stale_threshold_seconds,
                event_context_provider=event_context_provider,
            )
        except RuntimeError:
            failed_symbols.append(symbol_key)
            continue
    available_symbols = [symbol for symbol in requested_symbols if symbol in contexts]
    return LeadMarketContexts(
        contexts,
        metadata=_lead_market_context_metadata(
            requested_symbols=requested_symbols,
            available_symbols=available_symbols,
            failed_symbols=_unique_strings(failed_symbols),
        ),
    )


def persist_market_snapshot(session: Session, snapshot: MarketSnapshotPayload) -> MarketSnapshot:
    row = MarketSnapshot(
        symbol=snapshot.symbol,
        timeframe=snapshot.timeframe,
        snapshot_time=snapshot.snapshot_time,
        latest_price=snapshot.latest_price,
        latest_volume=snapshot.latest_volume,
        candle_count=snapshot.candle_count,
        is_stale=snapshot.is_stale,
        is_complete=snapshot.is_complete,
        payload=snapshot.model_dump(mode="json"),
    )
    session.add(row)
    session.flush()
    return row


def get_latest_snapshots(session: Session, limit: int = 20) -> list[MarketSnapshot]:
    statement = select(MarketSnapshot).order_by(desc(MarketSnapshot.snapshot_time)).limit(limit)
    return list(session.scalars(statement))
