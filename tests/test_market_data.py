from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trading_mvp.schemas import DerivativesContextPayload, EventContextPayload, MarketCandle
from trading_mvp.services import market_data
from trading_mvp.services.binance_market_stream import (
    clear_market_stream_cache,
    record_market_stream_event,
)
from trading_mvp.services.market_data_cache import (
    SharedClosedKlineCacheEntry,
    SharedMarketCacheRead,
    build_shared_market_cache_state,
)


def _ms(value: datetime) -> int:
    return int(value.replace(tzinfo=UTC).timestamp() * 1000)


def _closed_kline_event(symbol: str, timeframe: str, open_time: datetime, *, close: str = "999.0") -> dict[str, object]:
    interval_minutes = market_data.timeframe_to_minutes(timeframe)
    return {
        "stream": f"{symbol.lower()}@kline_{timeframe}",
        "data": {
            "e": "kline",
            "E": _ms(open_time + timedelta(minutes=interval_minutes)),
            "s": symbol.upper(),
            "k": {
                "t": _ms(open_time),
                "T": _ms(open_time + timedelta(minutes=interval_minutes) - timedelta(milliseconds=1)),
                "s": symbol.upper(),
                "i": timeframe,
                "o": "990.0",
                "c": close,
                "h": "1005.0",
                "l": "980.0",
                "v": "2500.0",
                "x": True,
            },
        },
    }


def _partial_kline_event(symbol: str, timeframe: str, open_time: datetime) -> dict[str, object]:
    event = _closed_kline_event(symbol, timeframe, open_time, close="888.0")
    assert isinstance(event["data"], dict)
    assert isinstance(event["data"]["k"], dict)
    event["data"]["k"]["x"] = False
    return event


def _shared_cache_miss(*_args, **_kwargs) -> SharedMarketCacheRead:
    return SharedMarketCacheRead(None, build_shared_market_cache_state({"cache_health": "miss"}))


def _shared_cache_entry(
    *,
    fixed_now: datetime,
    timeframe: str = "15m",
    close: float = 1001.0,
) -> SharedClosedKlineCacheEntry:
    stream_open_time = datetime(2026, 4, 27, 15, 0, 0)
    return SharedClosedKlineCacheEntry(
        symbol="BTCUSDT",
        timeframe=timeframe,
        candle=MarketCandle(
            timestamp=stream_open_time,
            open=990.0,
            high=1005.0,
            low=980.0,
            close=close,
            volume=2500.0,
        ),
        event_time=fixed_now,
        close_time=stream_open_time + timedelta(minutes=15) - timedelta(milliseconds=1),
        received_at=fixed_now,
        stream_name=f"btcusdt@kline_{timeframe}",
    )


def _install_binance_snapshot_fakes(monkeypatch, fixed_now: datetime) -> None:
    latest_age_by_interval = {
        "15m": timedelta(minutes=31),
        "1h": timedelta(minutes=55),
        "4h": timedelta(hours=3, minutes=14),
    }
    interval_minutes = {"15m": 15, "1h": 60, "4h": 240}

    class FakeBinanceClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def fetch_klines(self, *, symbol: str, interval: str, limit: int) -> list[MarketCandle]:
            latest_timestamp = fixed_now - latest_age_by_interval[interval]
            step = timedelta(minutes=interval_minutes[interval])
            candles: list[MarketCandle] = []
            for index in range(limit):
                timestamp = latest_timestamp - step * (limit - index - 1)
                close = 100.0 + index
                candles.append(
                    MarketCandle(
                        timestamp=timestamp,
                        open=close - 0.5,
                        high=close + 1.0,
                        low=close - 1.0,
                        close=close,
                        volume=1000.0 + index,
                    )
                )
            return candles

    monkeypatch.setattr(market_data, "utcnow_naive", lambda: fixed_now)
    monkeypatch.setattr(market_data, "BinanceClient", FakeBinanceClient)
    monkeypatch.setattr(market_data, "read_closed_kline_from_redis", _shared_cache_miss)
    monkeypatch.setattr(market_data, "_build_derivatives_context", lambda *_args, **_kwargs: DerivativesContextPayload())
    monkeypatch.setattr(
        market_data,
        "build_event_context",
        lambda **kwargs: EventContextPayload(generated_at=kwargs["generated_at"], is_complete=True),
    )


def test_market_context_relaxes_context_timeframe_stale_threshold_only(monkeypatch) -> None:
    clear_market_stream_cache()
    fixed_now = datetime(2026, 4, 27, 15, 14, 44)
    _install_binance_snapshot_fakes(monkeypatch, fixed_now)

    context = market_data.build_market_context(
        symbol="BTCUSDT",
        base_timeframe="15m",
        context_timeframes=("1h", "4h"),
        use_binance=True,
        stale_threshold_seconds=1800,
    )

    assert context["15m"].is_stale is True
    assert context["1h"].is_stale is False
    assert context["4h"].is_stale is False


def test_market_snapshot_keeps_configured_stale_threshold_for_base_calls(monkeypatch) -> None:
    clear_market_stream_cache()
    fixed_now = datetime(2026, 4, 27, 15, 14, 44)
    _install_binance_snapshot_fakes(monkeypatch, fixed_now)

    snapshot = market_data.build_market_snapshot(
        symbol="BTCUSDT",
        timeframe="4h",
        use_binance=True,
        stale_threshold_seconds=1800,
    )

    assert snapshot.is_stale is True


def test_market_snapshot_prefers_fresh_closed_ws_kline_over_rest_tail(monkeypatch) -> None:
    clear_market_stream_cache()
    fixed_now = datetime(2026, 4, 27, 15, 14, 44)
    _install_binance_snapshot_fakes(monkeypatch, fixed_now)
    stream_open_time = datetime(2026, 4, 27, 15, 0, 0)
    record_market_stream_event(
        _closed_kline_event("BTCUSDT", "15m", stream_open_time, close="999.0"),
        received_at=fixed_now,
    )

    snapshot = market_data.build_market_snapshot(
        symbol="BTCUSDT",
        timeframe="15m",
        use_binance=True,
        stale_threshold_seconds=1900,
    )

    assert snapshot.source == "binance_ws_final_kline"
    assert snapshot.source_detail["rest_bootstrap_used"] is True
    assert snapshot.source_detail["used_fallback"] is False
    assert snapshot.source_detail["fallback_reason"] is None
    assert snapshot.latest_price == 999.0
    assert snapshot.candles[-1].timestamp == stream_open_time
    assert snapshot.source_detail["partial_candle_used"] is False
    assert snapshot.source_detail["cache_backend"] == "process_local"
    assert snapshot.source_detail["configured_cache_backend"] == "redis"
    assert snapshot.source_detail["active_snapshot_source"] == "binance_ws_final_kline"
    assert snapshot.source_detail["fallback_active"] is False
    assert snapshot.source_detail["cache_health"] == "ok"
    assert snapshot.source_detail["redis_required"] is False
    assert snapshot.source_detail["redis_configured"] is True
    assert snapshot.is_complete is True
    assert snapshot.is_stale is False


def test_market_snapshot_prefers_fresh_shared_redis_kline_over_rest_tail(monkeypatch) -> None:
    clear_market_stream_cache()
    fixed_now = datetime(2026, 4, 27, 15, 14, 44)
    _install_binance_snapshot_fakes(monkeypatch, fixed_now)
    shared_entry = _shared_cache_entry(fixed_now=fixed_now)
    monkeypatch.setattr(
        market_data,
        "read_closed_kline_from_redis",
        lambda *_args, **_kwargs: SharedMarketCacheRead(
            shared_entry,
            build_shared_market_cache_state({"cache_health": "ok", "shared_cache_key": "test-key"}),
        ),
    )

    snapshot = market_data.build_market_snapshot(
        symbol="BTCUSDT",
        timeframe="15m",
        use_binance=True,
        stale_threshold_seconds=1800,
    )

    assert snapshot.source == "binance_ws_final_kline"
    assert snapshot.latest_price == 1001.0
    assert snapshot.source_detail["cache_backend"] == "redis"
    assert snapshot.source_detail["configured_cache_backend"] == "redis"
    assert snapshot.source_detail["active_snapshot_source"] == "redis"
    assert snapshot.source_detail["cache_scope"] == "shared"
    assert snapshot.source_detail["cache_health"] == "ok"
    assert snapshot.source_detail["shared_cache_supported"] is True
    assert snapshot.source_detail["redis_required"] is False
    assert snapshot.source_detail["redis_configured"] is True
    assert snapshot.source_detail["redis_connected"] is True
    assert snapshot.source_detail["used_fallback"] is False
    assert snapshot.source_detail["fallback_active"] is False


def test_market_snapshot_rejects_shared_cache_timeframe_mismatch(monkeypatch) -> None:
    clear_market_stream_cache()
    fixed_now = datetime(2026, 4, 27, 15, 14, 44)
    _install_binance_snapshot_fakes(monkeypatch, fixed_now)
    monkeypatch.setattr(
        market_data,
        "read_closed_kline_from_redis",
        lambda *_args, **_kwargs: SharedMarketCacheRead(
            None,
            build_shared_market_cache_state(
                {
                    "cache_health": "error",
                    "cache_reject_reason": "cache_metadata_mismatch",
                    "last_shared_cache_error": "cache metadata mismatch: timeframe",
                    "shared_cache_key": "test-key",
                }
            ),
        ),
    )

    snapshot = market_data.build_market_snapshot(
        symbol="BTCUSDT",
        timeframe="15m",
        use_binance=True,
        stale_threshold_seconds=1800,
    )

    assert snapshot.source == "binance_rest"
    assert snapshot.source_detail["active_snapshot_source"] == "binance_rest"
    assert snapshot.source_detail["used_fallback"] is True
    assert snapshot.source_detail["fallback_active"] is True
    assert snapshot.source_detail["fallback_reason"] == "market_stream_cache_metadata_mismatch"
    assert snapshot.source_detail["stale_reason"] == "market_stream_cache_metadata_mismatch"
    assert snapshot.source_detail["cache_reject_reason"] == "cache_metadata_mismatch"
    assert "timeframe" in str(snapshot.source_detail["last_shared_cache_error"])


def test_market_snapshot_uses_rest_fallback_when_only_partial_ws_kline_exists(monkeypatch) -> None:
    clear_market_stream_cache()
    fixed_now = datetime(2026, 4, 27, 15, 14, 44)
    _install_binance_snapshot_fakes(monkeypatch, fixed_now)
    monkeypatch.setattr(market_data, "get_market_stream_state", lambda: {"status": "connected", "stale": False})
    record_market_stream_event(
        _partial_kline_event("BTCUSDT", "15m", datetime(2026, 4, 27, 15, 0, 0)),
        received_at=fixed_now,
    )

    snapshot = market_data.build_market_snapshot(
        symbol="BTCUSDT",
        timeframe="15m",
        use_binance=True,
        stale_threshold_seconds=1800,
    )

    assert snapshot.source == "binance_rest"
    assert snapshot.source_status == "rest_fallback"
    assert snapshot.latest_price != 888.0
    assert snapshot.source_detail["active_snapshot_source"] == "binance_rest"
    assert snapshot.source_detail["fallback_reason"] == "market_stream_missing_or_stale"
    assert snapshot.source_detail["used_fallback"] is True
    assert snapshot.source_detail["fallback_active"] is True
    assert snapshot.source_detail["stale_reason"] == "market_stream_missing_or_stale"


def test_market_snapshot_uses_rest_fallback_when_shared_cache_unavailable(monkeypatch) -> None:
    clear_market_stream_cache()
    fixed_now = datetime(2026, 4, 27, 15, 14, 44)
    _install_binance_snapshot_fakes(monkeypatch, fixed_now)
    monkeypatch.setattr(
        market_data,
        "read_closed_kline_from_redis",
        lambda *_args, **_kwargs: SharedMarketCacheRead(
            None,
            build_shared_market_cache_state(
                {
                    "cache_health": "unavailable",
                    "last_shared_cache_error": "redis down",
                    "shared_cache_key": "test-key",
                }
            ),
        ),
    )

    snapshot = market_data.build_market_snapshot(
        symbol="BTCUSDT",
        timeframe="15m",
        use_binance=True,
        stale_threshold_seconds=1900,
    )

    assert snapshot.source == "binance_rest"
    assert snapshot.source_status == "rest_fallback"
    assert snapshot.is_complete is True
    assert snapshot.is_stale is False
    assert snapshot.source_detail["active_snapshot_source"] == "binance_rest"
    assert snapshot.source_detail["used_fallback"] is True
    assert snapshot.source_detail["fallback_active"] is True
    assert snapshot.source_detail["fallback_reason"] == "market_stream_cache_unavailable"
    assert snapshot.source_detail["stale_reason"] == "market_stream_cache_unavailable"
    assert snapshot.source_detail["cache_backend"] == "redis"
    assert snapshot.source_detail["configured_cache_backend"] == "redis"
    assert snapshot.source_detail["cache_health"] == "unavailable"
    assert snapshot.source_detail["redis_required"] is False
    assert snapshot.source_detail["redis_configured"] is True
    assert snapshot.source_detail["redis_connected"] is False


def test_market_snapshot_uses_rest_fallback_when_shared_cache_stale(monkeypatch) -> None:
    clear_market_stream_cache()
    fixed_now = datetime(2026, 4, 27, 15, 14, 44)
    _install_binance_snapshot_fakes(monkeypatch, fixed_now)
    monkeypatch.setattr(
        market_data,
        "read_closed_kline_from_redis",
        lambda *_args, **_kwargs: SharedMarketCacheRead(
            None,
            build_shared_market_cache_state({"cache_health": "stale", "cache_reject_reason": "cache_stale"}),
        ),
    )

    snapshot = market_data.build_market_snapshot(
        symbol="BTCUSDT",
        timeframe="15m",
        use_binance=True,
        stale_threshold_seconds=1800,
    )

    assert snapshot.source == "binance_rest"
    assert snapshot.source_detail["active_snapshot_source"] == "binance_rest"
    assert snapshot.source_detail["fallback_reason"] == "market_stream_cache_stale"
    assert snapshot.source_detail["stale_reason"] == "market_stream_cache_stale"
    assert snapshot.source_detail["used_fallback"] is True
    assert snapshot.source_detail["fallback_active"] is True


def test_market_snapshot_uses_rest_fallback_when_shared_cache_corrupt(monkeypatch) -> None:
    clear_market_stream_cache()
    fixed_now = datetime(2026, 4, 27, 15, 14, 44)
    _install_binance_snapshot_fakes(monkeypatch, fixed_now)
    monkeypatch.setattr(
        market_data,
        "read_closed_kline_from_redis",
        lambda *_args, **_kwargs: SharedMarketCacheRead(
            None,
            build_shared_market_cache_state({"cache_health": "error", "cache_reject_reason": "cache_payload_corrupt"}),
        ),
    )

    snapshot = market_data.build_market_snapshot(
        symbol="BTCUSDT",
        timeframe="15m",
        use_binance=True,
        stale_threshold_seconds=1800,
    )

    assert snapshot.source == "binance_rest"
    assert snapshot.source_detail["active_snapshot_source"] == "binance_rest"
    assert snapshot.source_detail["fallback_reason"] == "market_stream_cache_corrupt"
    assert snapshot.source_detail["stale_reason"] == "market_stream_cache_corrupt"
    assert snapshot.source_detail["used_fallback"] is True
    assert snapshot.source_detail["fallback_active"] is True


def test_market_snapshot_returns_incomplete_ws_snapshot_when_rest_fails(monkeypatch) -> None:
    clear_market_stream_cache()
    fixed_now = datetime(2026, 4, 27, 15, 14, 44)
    stream_open_time = datetime(2026, 4, 27, 15, 0, 0)

    class FailingBinanceClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def fetch_klines(self, *, symbol: str, interval: str, limit: int) -> list[MarketCandle]:
            raise RuntimeError("rest down")

    monkeypatch.setattr(market_data, "utcnow_naive", lambda: fixed_now)
    monkeypatch.setattr(market_data, "BinanceClient", FailingBinanceClient)
    monkeypatch.setattr(market_data, "read_closed_kline_from_redis", _shared_cache_miss)
    monkeypatch.setattr(
        market_data,
        "build_event_context",
        lambda **kwargs: EventContextPayload(generated_at=kwargs["generated_at"], is_complete=True),
    )
    record_market_stream_event(
        _closed_kline_event("BTCUSDT", "15m", stream_open_time, close="999.0"),
        received_at=fixed_now,
    )

    snapshot = market_data.build_market_snapshot(
        symbol="BTCUSDT",
        timeframe="15m",
        use_binance=True,
        stale_threshold_seconds=1800,
    )

    assert snapshot.source == "binance_ws_final_kline"
    assert snapshot.source_status == "incomplete"
    assert snapshot.is_complete is False
    assert snapshot.source_detail["rest_fallback_failed"] is True
    assert snapshot.source_detail["stream_only_incomplete"] is True
    assert snapshot.source_detail["used_fallback"] is False
    assert snapshot.source_detail["fallback_active"] is False
    assert snapshot.source_detail["active_snapshot_source"] == "binance_ws_final_kline"
    assert snapshot.source_detail["stale_reason"] == "market_snapshot_incomplete"


def test_redis_unavailable_with_rest_failure_uses_ws_only_incomplete_snapshot(monkeypatch) -> None:
    clear_market_stream_cache()
    fixed_now = datetime(2026, 4, 27, 15, 14, 44)
    stream_open_time = datetime(2026, 4, 27, 15, 0, 0)

    class FailingBinanceClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def fetch_klines(self, *, symbol: str, interval: str, limit: int) -> list[MarketCandle]:
            raise RuntimeError("rest down")

    monkeypatch.setattr(market_data, "utcnow_naive", lambda: fixed_now)
    monkeypatch.setattr(market_data, "BinanceClient", FailingBinanceClient)
    monkeypatch.setattr(
        market_data,
        "read_closed_kline_from_redis",
        lambda *_args, **_kwargs: SharedMarketCacheRead(
            None,
            build_shared_market_cache_state(
                {
                    "cache_health": "unavailable",
                    "last_shared_cache_error": "redis down",
                    "shared_cache_key": "test-key",
                }
            ),
        ),
    )
    monkeypatch.setattr(
        market_data,
        "build_event_context",
        lambda **kwargs: EventContextPayload(generated_at=kwargs["generated_at"], is_complete=True),
    )
    record_market_stream_event(
        _closed_kline_event("BTCUSDT", "15m", stream_open_time, close="999.0"),
        received_at=fixed_now,
    )

    snapshot = market_data.build_market_snapshot(
        symbol="BTCUSDT",
        timeframe="15m",
        use_binance=True,
        stale_threshold_seconds=1800,
    )

    assert snapshot.source == "binance_ws_final_kline"
    assert snapshot.source_status == "incomplete"
    assert snapshot.is_complete is False
    assert snapshot.source_detail["configured_cache_backend"] == "redis"
    assert snapshot.source_detail["cache_backend"] == "process_local"
    assert snapshot.source_detail["active_snapshot_source"] == "binance_ws_final_kline"
    assert snapshot.source_detail["redis_connected"] is False
    assert snapshot.source_detail["rest_fallback_failed"] is True
    assert snapshot.source_detail["stale_reason"] == "market_snapshot_incomplete"


def test_market_snapshot_returns_incomplete_shared_redis_snapshot_when_rest_fails(monkeypatch) -> None:
    clear_market_stream_cache()
    fixed_now = datetime(2026, 4, 27, 15, 14, 44)
    shared_entry = _shared_cache_entry(fixed_now=fixed_now, close=1001.0)

    class FailingBinanceClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def fetch_klines(self, *, symbol: str, interval: str, limit: int) -> list[MarketCandle]:
            raise RuntimeError("rest down")

    monkeypatch.setattr(market_data, "utcnow_naive", lambda: fixed_now)
    monkeypatch.setattr(market_data, "BinanceClient", FailingBinanceClient)
    monkeypatch.setattr(
        market_data,
        "read_closed_kline_from_redis",
        lambda *_args, **_kwargs: SharedMarketCacheRead(
            shared_entry,
            build_shared_market_cache_state({"cache_health": "ok", "shared_cache_key": "test-key"}),
        ),
    )
    monkeypatch.setattr(
        market_data,
        "build_event_context",
        lambda **kwargs: EventContextPayload(generated_at=kwargs["generated_at"], is_complete=True),
    )

    snapshot = market_data.build_market_snapshot(
        symbol="BTCUSDT",
        timeframe="15m",
        use_binance=True,
        stale_threshold_seconds=1800,
    )

    assert snapshot.source == "binance_ws_final_kline"
    assert snapshot.source_status == "incomplete"
    assert snapshot.is_complete is False
    assert snapshot.source_detail["cache_backend"] == "redis"
    assert snapshot.source_detail["configured_cache_backend"] == "redis"
    assert snapshot.source_detail["active_snapshot_source"] == "redis"
    assert snapshot.source_detail["cache_health"] == "ok"
    assert snapshot.source_detail["rest_fallback_failed"] is True
