from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from trading_mvp.services.binance_user_stream import (
    BinanceUserStreamListener,
    normalize_user_stream_event,
)


class _Clock:
    def __init__(self, start: datetime) -> None:
        self.current = start
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += timedelta(seconds=seconds)


class _KeepaliveClient:
    def __init__(self) -> None:
        self.created = 0
        self.keepalives: list[str] = []

    def create_futures_listen_key(self) -> str:
        self.created += 1
        return "listen-key-1"

    def keepalive_futures_listen_key(self, listen_key: str) -> dict[str, Any]:
        self.keepalives.append(listen_key)
        return {"listenKey": listen_key}

    async def stream_futures_user_events(self, listen_key: str, *, max_events: int | None = None, idle_timeout_seconds: float = 30.0):
        if False:
            yield listen_key


class _DisconnectOnceClient(_KeepaliveClient):
    def __init__(self) -> None:
        super().__init__()
        self.stream_calls = 0

    async def stream_futures_user_events(self, listen_key: str, *, max_events: int | None = None, idle_timeout_seconds: float = 30.0):
        self.stream_calls += 1
        if self.stream_calls == 1:
            raise RuntimeError("socket dropped")
        yield {
            "e": "ACCOUNT_UPDATE",
            "E": 1_713_312_100_000,
            "a": {
                "B": [{"a": "USDT", "wb": "100.0", "cw": "95.0"}],
                "P": [{"s": "BTCUSDT", "pa": "0.010", "ep": "64000", "up": "5.0"}],
            },
        }


def test_normalize_user_stream_event_distinguishes_order_and_execution() -> None:
    order_event = normalize_user_stream_event(
        {
            "e": "ORDER_TRADE_UPDATE",
            "E": 1_713_312_000_000,
            "o": {
                "s": "BTCUSDT",
                "i": "order-1",
                "c": "client-1",
                "X": "NEW",
                "x": "NEW",
                "t": "0",
                "l": "0",
            },
        }
    )
    execution_event = normalize_user_stream_event(
        {
            "e": "ORDER_TRADE_UPDATE",
            "E": 1_713_312_010_000,
            "o": {
                "s": "BTCUSDT",
                "i": "order-1",
                "c": "client-1",
                "X": "PARTIALLY_FILLED",
                "x": "TRADE",
                "t": "trade-1",
                "l": "0.01",
            },
        }
    )

    assert order_event["event_category"] == "order"
    assert order_event["related_categories"] == ["order"]
    assert order_event["symbol"] == "BTCUSDT"
    assert order_event["raw_payload"]["o"]["X"] == "NEW"
    assert execution_event["event_category"] == "execution"
    assert execution_event["related_categories"] == ["order", "execution"]
    assert execution_event["trade_id"] == "trade-1"
    assert execution_event["execution_type"] == "TRADE"


def test_normalize_user_stream_event_extracts_account_and_position_categories() -> None:
    event = normalize_user_stream_event(
        {
            "e": "ACCOUNT_UPDATE",
            "E": 1_713_312_100_000,
            "a": {
                "B": [{"a": "USDT", "wb": "100.0", "cw": "95.0"}],
                "P": [
                    {"s": "BTCUSDT", "pa": "0.010", "ep": "64000", "up": "5.0"},
                    {"s": "ETHUSDT", "pa": "-0.500", "ep": "3200", "up": "-2.0"},
                ],
            },
        }
    )

    assert event["event_category"] == "account"
    assert event["related_categories"] == ["account", "position"]
    assert event["symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert event["raw_payload"]["a"]["B"][0]["a"] == "USDT"


def test_user_stream_listener_creates_and_keeps_alive_listen_key() -> None:
    base = datetime(2026, 4, 17, 12, 0, 0)
    times = iter([base, base + timedelta(minutes=26)])
    client = _KeepaliveClient()
    listener = BinanceUserStreamListener(client, now_fn=lambda: next(times))

    state, issues = listener.ensure_registration({})
    state, keepalive_issues = listener.ensure_registration(state)

    assert issues == []
    assert keepalive_issues == []
    assert client.created == 1
    assert client.keepalives == ["listen-key-1"]
    assert state["listen_key"] == "listen-key-1"
    assert state["last_keepalive_at"] is not None
    assert state["listen_key_refreshed_at"] is not None


def test_user_stream_listener_collect_once_marks_backoff_on_disconnect() -> None:
    base = datetime(2026, 4, 17, 12, 0, 0)
    clock = _Clock(base)
    client = _DisconnectOnceClient()
    listener = BinanceUserStreamListener(client, now_fn=clock.now, sleep_fn=clock.sleep)

    result = asyncio.run(listener.collect_once({}, max_events=1, idle_timeout_seconds=0.01))

    state = result["state"]
    issues = result["issues"]
    assert result["events"] == []
    assert state["status"] == "degraded"
    assert state["reconnect_count"] == 1
    assert state["backoff_seconds"] == 1.0
    assert state["next_retry_at"] is not None
    assert any(issue["reason_code"] == "USER_STREAM_CONNECTION_DROPPED" for issue in issues)


def test_user_stream_listener_loop_retries_after_backoff_and_normalizes_event() -> None:
    base = datetime(2026, 4, 17, 12, 0, 0)
    clock = _Clock(base)
    client = _DisconnectOnceClient()
    listener = BinanceUserStreamListener(client, now_fn=clock.now, sleep_fn=clock.sleep)

    async def _collect() -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        async for item in listener.listen_forever(
            {},
            max_cycles=2,
            max_events_per_cycle=1,
            idle_timeout_seconds=0.01,
        ):
            results.append(item)
        return results

    results = asyncio.run(_collect())

    assert len(results) == 2
    assert results[0]["state"]["status"] == "degraded"
    assert clock.sleeps == [1.0]
    assert results[1]["state"]["status"] == "connected"
    assert len(results[1]["events"]) == 1
    assert results[1]["events"][0]["event_category"] == "account"
    assert "position" in results[1]["events"][0]["related_categories"]
