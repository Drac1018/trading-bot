from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

from trading_mvp import main
from trading_mvp.services.binance_market_stream import (
    MARKET_STREAM_DATA_SOURCE,
    MARKET_STREAM_PARTIAL_REASON_CODE,
    build_futures_market_stream_url,
    build_market_stream_state,
    clear_market_stream_cache,
    get_cached_closed_kline,
    normalize_market_stream_event,
    record_market_stream_event,
)
from trading_mvp.services.market_data_cache import (
    MARKET_DATA_CACHE_BACKEND_REDIS,
    MARKET_DATA_CACHE_PARTIAL_IGNORED,
    MARKET_DATA_CACHE_SCOPE_SHARED,
    read_closed_kline_from_redis,
    write_closed_kline_event_to_redis,
)


class FakeRedis:
    store: dict[str, object] = {}
    ttl_by_key: dict[str, int] = {}

    @classmethod
    def from_url(cls, *_args, **_kwargs):
        return cls()

    def set(self, key: str, value: object, *, ex: int | None = None) -> bool:
        self.store[key] = value
        if ex is not None:
            self.ttl_by_key[key] = ex
        return True

    def get(self, key: str) -> object | None:
        return self.store.get(key)


def _ms(value: datetime) -> int:
    return int(value.replace(tzinfo=UTC).timestamp() * 1000)


def _kline_event(*, closed: bool, open_time: datetime, close: str = "64100.0") -> dict[str, object]:
    return {
        "stream": "btcusdt@kline_1m",
        "data": {
            "e": "kline",
            "E": _ms(open_time + timedelta(minutes=1)),
            "s": "BTCUSDT",
            "k": {
                "t": _ms(open_time),
                "T": _ms(open_time + timedelta(minutes=1) - timedelta(milliseconds=1)),
                "s": "BTCUSDT",
                "i": "1m",
                "o": "64000.0",
                "c": close,
                "h": "64200.0",
                "l": "63900.0",
                "v": "1200.0",
                "x": closed,
            },
        },
    }


def test_market_stream_url_uses_public_market_route_and_lowercase_streams() -> None:
    url = build_futures_market_stream_url(["BTCUSDT"], ["1m", "15m"])

    assert url == "wss://fstream.binance.com/market/stream?streams=btcusdt@kline_1m/btcusdt@kline_15m"


def test_partial_kline_is_normalized_but_not_cached() -> None:
    clear_market_stream_cache()
    open_time = datetime(2026, 5, 5, 12, 0, 0)

    event = record_market_stream_event(
        _kline_event(closed=False, open_time=open_time),
        received_at=open_time + timedelta(seconds=20),
    )

    assert event["reason_code"] == MARKET_STREAM_PARTIAL_REASON_CODE
    assert event["partial_candle_used"] is False
    assert get_cached_closed_kline("BTCUSDT", "1m", now=open_time + timedelta(seconds=20)) is None


def test_market_stream_module_has_no_trade_authority_imports_or_calls() -> None:
    source = Path("backend/trading_mvp/services/binance_market_stream.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_imports = (
        "trading_mvp.services.agents",
        "trading_mvp.services.execution",
        "trading_mvp.services.orchestrator",
        "trading_mvp.services.risk",
        "openai",
    )
    forbidden_calls = {
        "execute_live_trade",
        "evaluate_risk",
        "run_decision_cycle",
        "submit_order",
        "_safe_submit_order",
        "_submit_exchange_order",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported = [alias.name for alias in node.names]
            assert not any(name.startswith(forbidden_imports) for name in imported)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not module.startswith(forbidden_imports)
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                assert func.id not in forbidden_calls
            elif isinstance(func, ast.Attribute):
                assert func.attr not in forbidden_calls


def test_closed_kline_is_cached_as_final_market_source() -> None:
    clear_market_stream_cache()
    open_time = datetime(2026, 5, 5, 12, 0, 0)
    received_at = open_time + timedelta(minutes=1)

    event = normalize_market_stream_event(
        _kline_event(closed=True, open_time=open_time, close="64123.4"),
        received_at=received_at,
    )
    recorded = record_market_stream_event(
        _kline_event(closed=True, open_time=open_time, close="64123.4"),
        received_at=received_at,
    )
    cached = get_cached_closed_kline("BTCUSDT", "1m", now=received_at + timedelta(seconds=10))

    assert event["closed"] is True
    assert recorded["reason_code"] == "KLINE_CLOSED"
    assert cached is not None
    assert cached.candle.close == 64123.4
    assert cached.as_source_detail(now=received_at + timedelta(seconds=10))["source"] == MARKET_STREAM_DATA_SOURCE


def test_market_stream_state_declares_process_local_cache_scope() -> None:
    state = build_market_stream_state({"status": "idle"})

    assert state["cache_backend"] == "process_local"
    assert state["configured_cache_backend"] == "process_local"
    assert state["cache_health"] == "unknown"
    assert state["cache_scope"] == "process_local"
    assert state["shared_cache_supported"] is False
    assert state["redis_required"] is False
    assert state["redis_configured"] is False
    assert state["redis_connected"] is False
    assert state["stream_enabled"] is False
    assert state["stream_running"] is False
    assert state["sqlite_default_disabled"] is False


def test_market_stream_stale_reason_clears_when_state_is_fresh() -> None:
    now = datetime(2026, 5, 5, 12, 0, 0)

    state = build_market_stream_state(
        {
            "status": "connected",
            "last_event_at": (now - timedelta(seconds=10)).isoformat(),
            "stale_after_seconds": 120,
            "reason_code": "MARKET_STREAM_STALE",
        },
        now=now,
    )

    assert state["stale"] is False
    assert state["degraded"] is False
    assert state["reason_code"] is None


def test_market_stream_runtime_config_declares_redis_shared_cache() -> None:
    state = main._market_stream_runtime_config_fields({"enabled": True, "redis_url": "redis://test"})

    assert state["cache_backend"] == MARKET_DATA_CACHE_BACKEND_REDIS
    assert state["configured_cache_backend"] == MARKET_DATA_CACHE_BACKEND_REDIS
    assert state["cache_scope"] == MARKET_DATA_CACHE_SCOPE_SHARED
    assert state["shared_cache_supported"] is True
    assert state["redis_required"] is False
    assert state["redis_configured"] is True
    assert state["redis_connected"] is None
    assert state["stream_enabled"] is True


def test_partial_kline_is_not_written_to_shared_redis_cache(monkeypatch) -> None:
    FakeRedis.store = {}
    FakeRedis.ttl_by_key = {}
    monkeypatch.setattr("trading_mvp.services.market_data_cache.Redis", FakeRedis)
    open_time = datetime(2026, 5, 5, 12, 0, 0)
    normalized = normalize_market_stream_event(
        _kline_event(closed=False, open_time=open_time),
        received_at=open_time + timedelta(seconds=20),
    )

    state = write_closed_kline_event_to_redis(normalized, redis_url="redis://test")

    assert state["cache_backend"] == MARKET_DATA_CACHE_BACKEND_REDIS
    assert state["configured_cache_backend"] == MARKET_DATA_CACHE_BACKEND_REDIS
    assert state["cache_scope"] == MARKET_DATA_CACHE_SCOPE_SHARED
    assert state["cache_health"] == "ok"
    assert state["redis_connected"] is True
    assert state["cache_write_status"] == MARKET_DATA_CACHE_PARTIAL_IGNORED
    assert FakeRedis.store == {}


def test_final_closed_kline_is_written_to_shared_redis_cache(monkeypatch) -> None:
    FakeRedis.store = {}
    FakeRedis.ttl_by_key = {}
    monkeypatch.setattr("trading_mvp.services.market_data_cache.Redis", FakeRedis)
    open_time = datetime(2026, 5, 5, 12, 0, 0)
    received_at = open_time + timedelta(minutes=1)
    normalized = normalize_market_stream_event(
        _kline_event(closed=True, open_time=open_time, close="64123.4"),
        received_at=received_at,
    )

    write_state = write_closed_kline_event_to_redis(normalized, redis_url="redis://test")
    read = read_closed_kline_from_redis(
        "BTCUSDT",
        "1m",
        redis_url="redis://test",
        now=received_at + timedelta(seconds=10),
    )

    assert write_state["cache_write_status"] == "stored"
    assert write_state["cache_health"] == "ok"
    assert FakeRedis.store
    assert read.entry is not None
    assert read.entry.candle.close == 64123.4
    assert read.state["cache_backend"] == MARKET_DATA_CACHE_BACKEND_REDIS
    assert read.state["configured_cache_backend"] == MARKET_DATA_CACHE_BACKEND_REDIS
    assert read.state["redis_connected"] is True
    assert read.state["cache_scope"] == MARKET_DATA_CACHE_SCOPE_SHARED
    assert read.state["cache_health"] == "ok"


def test_background_market_stream_is_disabled_by_default_on_sqlite(monkeypatch) -> None:
    class FakeDialect:
        name = "sqlite"

    class FakeEngine:
        dialect = FakeDialect()

    monkeypatch.setattr(main, "engine", FakeEngine())
    monkeypatch.delenv("TRADING_MVP_ENABLE_BACKGROUND_MARKET_STREAM", raising=False)
    assert main._background_market_stream_enabled() is False

    monkeypatch.setenv("TRADING_MVP_ENABLE_BACKGROUND_MARKET_STREAM", "true")
    assert main._background_market_stream_enabled() is True
