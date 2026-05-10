from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from trading_mvp.services.binance_market_stream import normalize_market_stream_event
from trading_mvp.services.market_data_cache import (
    MARKET_DATA_CACHE_ENV_MAINNET,
    MARKET_DATA_CACHE_ENV_TESTNET,
    MARKET_DATA_CACHE_MARKET_TYPE_USDM_FUTURES,
    MARKET_DATA_CACHE_PARTIAL_IGNORED,
    market_data_cache_key,
    read_closed_kline_from_redis,
    write_closed_kline_event_to_redis,
)


class FakeRedis:
    store: dict[str, object] = {}
    ttl_by_key: dict[str, int] = {}
    fail: bool = False

    @classmethod
    def from_url(cls, *_args, **_kwargs):
        if cls.fail:
            raise TimeoutError("redis timeout")
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


def _kline_event(
    *,
    symbol: str = "BTCUSDT",
    timeframe: str = "1m",
    closed: bool = True,
    open_time: datetime,
    close: str = "64100.0",
) -> dict[str, object]:
    return {
        "stream": f"{symbol.lower()}@kline_{timeframe}",
        "data": {
            "e": "kline",
            "E": _ms(open_time + timedelta(minutes=1)),
            "s": symbol.upper(),
            "k": {
                "t": _ms(open_time),
                "T": _ms(open_time + timedelta(minutes=1) - timedelta(milliseconds=1)),
                "s": symbol.upper(),
                "i": timeframe,
                "o": "64000.0",
                "c": close,
                "h": "64200.0",
                "l": "63900.0",
                "v": "1200.0",
                "x": closed,
            },
        },
    }


def _install_fake_redis(monkeypatch) -> None:
    FakeRedis.store = {}
    FakeRedis.ttl_by_key = {}
    FakeRedis.fail = False
    monkeypatch.setattr("trading_mvp.services.market_data_cache.Redis", FakeRedis)


def test_market_data_cache_key_separates_market_environment_symbol_and_timeframe() -> None:
    mainnet_btc_1m = market_data_cache_key("BTCUSDT", "1m", environment=MARKET_DATA_CACHE_ENV_MAINNET)
    testnet_btc_1m = market_data_cache_key("BTCUSDT", "1m", environment=MARKET_DATA_CACHE_ENV_TESTNET)
    mainnet_eth_1m = market_data_cache_key("ETHUSDT", "1m", environment=MARKET_DATA_CACHE_ENV_MAINNET)
    mainnet_btc_15m = market_data_cache_key("BTCUSDT", "15m", environment=MARKET_DATA_CACHE_ENV_MAINNET)

    assert len({mainnet_btc_1m, testnet_btc_1m, mainnet_eth_1m, mainnet_btc_15m}) == 4
    assert "public_market_data" in mainnet_btc_1m
    assert MARKET_DATA_CACHE_MARKET_TYPE_USDM_FUTURES in mainnet_btc_1m
    assert MARKET_DATA_CACHE_ENV_MAINNET in mainnet_btc_1m
    assert "BTCUSDT" in mainnet_btc_1m
    assert mainnet_btc_1m.endswith(":1m")


def test_partial_kline_is_not_written_to_redis(monkeypatch) -> None:
    _install_fake_redis(monkeypatch)
    open_time = datetime(2026, 5, 5, 12, 0, 0)
    event = normalize_market_stream_event(_kline_event(open_time=open_time, closed=False), received_at=open_time)

    state = write_closed_kline_event_to_redis(event, redis_url="redis://test")

    assert state["cache_write_status"] == MARKET_DATA_CACHE_PARTIAL_IGNORED
    assert state["configured_cache_backend"] == "redis"
    assert state["redis_required"] is False
    assert state["redis_configured"] is True
    assert FakeRedis.store == {}


def test_final_closed_kline_is_written_with_ttl_and_read_back(monkeypatch) -> None:
    _install_fake_redis(monkeypatch)
    open_time = datetime(2026, 5, 5, 12, 0, 0)
    received_at = open_time + timedelta(minutes=1)
    event = normalize_market_stream_event(
        _kline_event(open_time=open_time, closed=True, close="64123.4"),
        received_at=received_at,
    )

    state = write_closed_kline_event_to_redis(
        event,
        redis_url="redis://test",
        environment=MARKET_DATA_CACHE_ENV_TESTNET,
    )
    read = read_closed_kline_from_redis(
        "BTCUSDT",
        "1m",
        redis_url="redis://test",
        environment=MARKET_DATA_CACHE_ENV_TESTNET,
        now=received_at + timedelta(seconds=10),
    )

    assert state["cache_write_status"] == "stored"
    assert state["cache_health"] == "ok"
    assert FakeRedis.ttl_by_key
    assert read.entry is not None
    assert read.entry.environment == MARKET_DATA_CACHE_ENV_TESTNET
    assert read.entry.candle.close == 64123.4
    assert read.state["cache_health"] == "ok"
    assert read.state["configured_cache_backend"] == "redis"
    assert read.state["redis_connected"] is True


def test_redis_miss_down_corrupt_and_invalid_payload_do_not_return_entry(monkeypatch) -> None:
    _install_fake_redis(monkeypatch)
    key = market_data_cache_key("BTCUSDT", "1m")

    miss = read_closed_kline_from_redis("BTCUSDT", "1m", redis_url="redis://test")
    FakeRedis.fail = True
    down = read_closed_kline_from_redis("BTCUSDT", "1m", redis_url="redis://test")
    FakeRedis.fail = False
    FakeRedis.store[key] = "{not-json"
    corrupt = read_closed_kline_from_redis("BTCUSDT", "1m", redis_url="redis://test")
    FakeRedis.store[key] = json.dumps(
        {
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "closed": True,
            "received_at": datetime(2026, 5, 5, 12, 1, 0).isoformat(),
            "candle": {
                "timestamp": datetime(2026, 5, 5, 12, 0, 0).isoformat(),
                "open": "64000.0",
                "high": "64200.0",
                "low": "63900.0",
                "close": "nan",
                "volume": "1200.0",
            },
        }
    )
    invalid = read_closed_kline_from_redis("BTCUSDT", "1m", redis_url="redis://test")

    assert miss.entry is None
    assert miss.state["cache_health"] == "miss"
    assert miss.state["redis_connected"] is True
    assert down.entry is None
    assert down.state["cache_health"] == "unavailable"
    assert down.state["redis_connected"] is False
    assert corrupt.entry is None
    assert corrupt.state["cache_reject_reason"] == "cache_payload_corrupt"
    assert corrupt.state["redis_connected"] is True
    assert invalid.entry is None
    assert invalid.state["cache_reject_reason"] == "cache_payload_invalid"
    assert invalid.state["redis_connected"] is True


def test_redis_disabled_state_is_explicit_when_url_is_not_configured() -> None:
    read = read_closed_kline_from_redis("BTCUSDT", "1m", redis_url="")

    assert read.entry is None
    assert read.state["configured_cache_backend"] == "disabled"
    assert read.state["cache_health"] == "disabled"
    assert read.state["redis_required"] is False
    assert read.state["redis_configured"] is False
    assert read.state["redis_connected"] is False


def test_stale_and_metadata_mismatch_records_do_not_return_entry(monkeypatch) -> None:
    _install_fake_redis(monkeypatch)
    open_time = datetime(2026, 5, 5, 12, 0, 0)
    received_at = open_time + timedelta(minutes=1)
    event = normalize_market_stream_event(
        _kline_event(open_time=open_time, timeframe="1m", closed=True),
        received_at=received_at,
    )
    write_closed_kline_event_to_redis(event, redis_url="redis://test")

    stale = read_closed_kline_from_redis(
        "BTCUSDT",
        "1m",
        redis_url="redis://test",
        now=received_at + timedelta(minutes=5),
        stale_after_seconds=120,
    )
    wrong_key = market_data_cache_key("BTCUSDT", "15m")
    source_key = market_data_cache_key("BTCUSDT", "1m")
    FakeRedis.store[wrong_key] = FakeRedis.store[source_key]
    mismatch = read_closed_kline_from_redis("BTCUSDT", "15m", redis_url="redis://test", now=received_at)

    assert stale.entry is None
    assert stale.state["cache_health"] == "stale"
    assert stale.state["cache_reject_reason"] == "cache_stale"
    assert mismatch.entry is None
    assert mismatch.state["cache_health"] == "error"
    assert mismatch.state["cache_reject_reason"] == "cache_metadata_mismatch"
    assert "timeframe" in str(mismatch.state["last_shared_cache_error"])


def test_cache_modules_have_no_trade_authority_imports_or_calls() -> None:
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
    for filename in (
        "backend/trading_mvp/services/market_data_cache.py",
        "backend/trading_mvp/services/binance_market_stream.py",
        "backend/trading_mvp/services/market_data.py",
    ):
        tree = ast.parse(Path(filename).read_text(encoding="utf-8"))
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
