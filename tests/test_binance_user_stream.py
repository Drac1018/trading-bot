from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from trading_mvp.services.binance import BinanceAPIError
from trading_mvp.services.binance_user_stream import (
    BinanceUserStreamListener,
    next_reconnect_backoff_seconds,
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

    def close_futures_listen_key(self, listen_key: str) -> dict[str, Any]:
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


class _RotateSuccessClient(_KeepaliveClient):
    def __init__(self) -> None:
        super().__init__()
        self.closed: list[str] = []

    def create_futures_listen_key(self) -> str:
        self.created += 1
        return "listen-key-new"

    def close_futures_listen_key(self, listen_key: str) -> dict[str, Any]:
        self.closed.append(listen_key)
        return {"listenKey": listen_key}

    async def stream_futures_user_events(self, listen_key: str, *, max_events: int | None = None, idle_timeout_seconds: float = 30.0):
        del max_events, idle_timeout_seconds
        assert listen_key == "listen-key-old"
        yield {
            "e": "listenKeyExpired",
            "E": 1_713_312_200_000,
            "reason": "listenKeyExpired",
        }


class _RotateFailClient(_RotateSuccessClient):
    def create_futures_listen_key(self) -> str:
        self.created += 1
        raise RuntimeError("rotate failed")


class _InvalidListenKeyRecoveryClient(_KeepaliveClient):
    def __init__(self) -> None:
        super().__init__()
        self.closed: list[str] = []
        self.keepalive_attempts: list[str] = []

    def keepalive_futures_listen_key(self, listen_key: str) -> dict[str, Any]:
        self.keepalive_attempts.append(listen_key)
        raise BinanceAPIError(-1125, "This listenKey does not exist.")

    def create_futures_listen_key(self) -> str:
        self.created += 1
        return "listen-key-recovered"

    def close_futures_listen_key(self, listen_key: str) -> dict[str, Any]:
        self.closed.append(listen_key)
        return {"listenKey": listen_key}


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


def test_next_reconnect_backoff_saturates_for_large_reconnect_count() -> None:
    assert next_reconnect_backoff_seconds(0) == 0.0
    assert next_reconnect_backoff_seconds(1) == 1.0
    assert next_reconnect_backoff_seconds(2) == 2.0
    assert next_reconnect_backoff_seconds(3071) == 30.0


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


def test_user_stream_listener_rotates_listen_key_when_expired_event_received() -> None:
    base = datetime(2026, 4, 17, 12, 0, 0)
    clock = _Clock(base)
    client = _RotateSuccessClient()
    listener = BinanceUserStreamListener(client, now_fn=clock.now, sleep_fn=clock.sleep)

    initial_state = {
        "status": "connected",
        "listen_key": "listen-key-old",
        "listen_key_refreshed_at": base.isoformat(),
        "stream_source": "user_stream",
    }
    result = asyncio.run(listener.collect_once(initial_state, max_events=1, idle_timeout_seconds=0.01))

    state = result["state"]
    issue_codes = [str(item.get("reason_code")) for item in result["issues"]]
    assert state["status"] == "degraded"
    assert state["stream_source"] == "rest_polling_fallback"
    assert state["listen_key"] == "listen-key-new"
    assert state["listen_key_expiry_reason"] == "listenKeyExpired"
    assert state["listen_key_rotate_status"] == "succeeded"
    assert state["listen_key_rotate_attempted_at"] is not None
    assert state["listen_key_rotate_completed_at"] is not None
    assert "USER_STREAM_LISTEN_KEY_ROTATED" in issue_codes
    assert client.closed == ["listen-key-old"]


def test_user_stream_listener_rotation_failure_keeps_fallback_and_entry_guard_state() -> None:
    base = datetime(2026, 4, 17, 12, 0, 0)
    clock = _Clock(base)
    client = _RotateFailClient()
    listener = BinanceUserStreamListener(client, now_fn=clock.now, sleep_fn=clock.sleep)

    initial_state = {
        "status": "connected",
        "listen_key": "listen-key-old",
        "listen_key_refreshed_at": base.isoformat(),
        "stream_source": "user_stream",
    }
    result = asyncio.run(listener.collect_once(initial_state, max_events=1, idle_timeout_seconds=0.01))

    state = result["state"]
    issue_codes = [str(item.get("reason_code")) for item in result["issues"]]
    assert state["status"] == "degraded"
    assert state["stream_source"] == "rest_polling_fallback"
    assert state["listen_key"] is None
    assert state["listen_key_rotate_status"] == "failed"
    assert state["listen_key_rotate_error"] == "rotate failed"
    assert state["next_retry_at"] is not None
    assert state["backoff_seconds"] > 0
    assert "USER_STREAM_LISTEN_KEY_ROTATE_FAILED" in issue_codes


def test_user_stream_listener_registration_recovers_connected_after_rotation_failure() -> None:
    base = datetime(2026, 4, 17, 12, 0, 0)
    times = iter([base, base + timedelta(seconds=2)])
    client = _KeepaliveClient()
    listener = BinanceUserStreamListener(client, now_fn=lambda: next(times))

    failed_state = {
        "status": "degraded",
        "stream_source": "rest_polling_fallback",
        "listen_key": None,
        "next_retry_at": (base - timedelta(seconds=1)).isoformat(),
        "backoff_seconds": 1.0,
        "listen_key_rotate_status": "failed",
        "listen_key_rotate_error": "rotate failed",
    }
    state, issues = listener.ensure_registration(failed_state)

    assert issues == []
    assert state["status"] == "connected"
    assert state["stream_source"] == "user_stream"
    assert state["listen_key"] == "listen-key-1"
    assert state["listen_key_rotate_status"] == "succeeded"
    assert state["listen_key_rotate_completed_at"] is not None
    assert state["listen_key_rotate_error"] is None


def test_user_stream_listener_recreates_invalid_listen_key_and_resets_reconnect_count() -> None:
    base = datetime(2026, 4, 17, 12, 0, 0)
    client = _InvalidListenKeyRecoveryClient()
    listener = BinanceUserStreamListener(client, now_fn=lambda: base)

    stale_state = {
        "status": "degraded",
        "listen_key": "listen-key-old",
        "listen_key_refreshed_at": (base - timedelta(minutes=30)).isoformat(),
        "stream_source": "rest_polling_fallback",
        "reconnect_count": 3070,
        "last_error": "old failure",
    }
    state, issues = listener.ensure_registration(stale_state)

    issue_codes = [str(item.get("reason_code")) for item in issues]
    assert state["status"] == "connected"
    assert state["stream_source"] == "user_stream"
    assert state["listen_key"] == "listen-key-recovered"
    assert state["reconnect_count"] == 0
    assert state["backoff_seconds"] == 0.0
    assert state["next_retry_at"] is None
    assert state["listen_key_rotate_status"] == "succeeded"
    assert "USER_STREAM_LISTEN_KEY_RECREATED" in issue_codes
    assert client.keepalive_attempts == ["listen-key-old"]
    assert client.closed == ["listen-key-old"]
