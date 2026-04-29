from __future__ import annotations

from typing import Any

import httpx
import pytest
from trading_mvp.services.binance import BinanceClient


def test_signed_requests_use_epoch_timestamp(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def request(self, method, path, params=None, headers=None):
            captured["method"] = method
            captured["path"] = path
            captured["params"] = dict(params or {})
            captured["headers"] = dict(headers or {})
            return FakeResponse()

    monkeypatch.setattr("trading_mvp.services.binance.time.time", lambda: 1_700_000_000.25)
    monkeypatch.setattr("trading_mvp.services.binance.httpx.Client", FakeClient)

    BinanceClient(api_key="key", api_secret="secret").get_account_info()

    assert captured["path"] == "/fapi/v3/account"
    assert captured["params"]["timestamp"] == 1_700_000_000_000
    assert captured["params"]["recvWindow"] == 5000
    assert "signature" in captured["params"]
    assert captured["headers"]["X-MBX-APIKEY"] == "key"


def test_http_error_exposes_binance_code_and_message(monkeypatch) -> None:
    events: list[dict[str, object]] = []

    class ErrorResponse:
        def __init__(self) -> None:
            self.text = '{"code":-1021,"msg":"Timestamp outside recvWindow"}'

        def raise_for_status(self) -> None:
            request = httpx.Request("GET", "https://fapi.binance.com/fapi/v3/account")
            response = httpx.Response(
                400,
                request=request,
                json={"code": -1021, "msg": "Timestamp outside recvWindow"},
            )
            raise httpx.HTTPStatusError("bad request", request=request, response=response)

        def json(self) -> dict[str, object]:
            return {"code": -1021, "msg": "Timestamp outside recvWindow"}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def request(self, method, path, params=None, headers=None):
            if path == "/fapi/v1/time":
                class TimeResponse:
                    def raise_for_status(self) -> None:
                        return None

                    def json(self) -> dict[str, object]:
                        return {"serverTime": 1_700_000_000_000}

                return TimeResponse()
            return ErrorResponse()

    monkeypatch.setattr("trading_mvp.services.binance.httpx.Client", FakeClient)

    with pytest.raises(RuntimeError, match="Binance error -1021: Timestamp outside recvWindow"):
        BinanceClient(api_key="key", api_secret="secret", request_error_hook=events.append).get_account_info()

    assert events[0]["endpoint_category"] == "account"
    assert events[0]["error_type"] == "status"
    assert events[0]["status_code"] == 400


def test_get_retry_attempts_can_be_capped(monkeypatch) -> None:
    calls = 0
    events: list[dict[str, object]] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def request(self, method, path, params=None, headers=None):
            nonlocal calls
            calls += 1
            raise httpx.TransportError("network timeout")

    monkeypatch.setattr("trading_mvp.services.binance.httpx.Client", FakeClient)

    with pytest.raises(httpx.TransportError, match="network timeout"):
        BinanceClient(max_get_attempts=1, request_error_hook=events.append).fetch_klines("BTCUSDT", "1m", limit=2)

    assert calls == 1
    assert len(events) == 1
    assert events[0]["event"] == "binance_request_error"
    assert events[0]["method"] == "GET"
    assert events[0]["path"] == "/fapi/v1/klines"
    assert events[0]["endpoint_category"] == "market_data"
    assert events[0]["error_type"] == "transport"
    assert events[0]["exception"] == "TransportError"
    assert events[0]["attempt"] == 1
    assert events[0]["attempts"] == 1


def test_injected_http_client_handles_requests_without_owning_lifecycle() -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        return httpx.Response(200, json={})

    http_client = httpx.Client(
        base_url="https://fapi.binance.com",
        transport=httpx.MockTransport(handler),
    )
    try:
        client = BinanceClient(http_client=http_client)
        assert client.ping() == {}
        client.close()
        assert http_client.is_closed is False
    finally:
        http_client.close()

    assert requests == [("GET", "/fapi/v1/ping")]


def test_default_http_client_is_lazy_reused_and_closeable(monkeypatch) -> None:
    instances: list[Any] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.closed = False
            self.requests: list[str] = []
            instances.append(self)

        def request(self, method, path, params=None, headers=None):
            self.requests.append(path)
            return FakeResponse()

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr("trading_mvp.services.binance.httpx.Client", FakeClient)

    client = BinanceClient()

    assert instances == []
    client.ping()
    client.ping()

    assert len(instances) == 1
    assert instances[0].requests == ["/fapi/v1/ping", "/fapi/v1/ping"]

    client.close()

    assert instances[0].closed is True


def test_context_manager_closes_owned_http_client(monkeypatch) -> None:
    instances: list[Any] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.closed = False
            instances.append(self)

        def request(self, method, path, params=None, headers=None):
            return FakeResponse()

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr("trading_mvp.services.binance.httpx.Client", FakeClient)

    with BinanceClient() as client:
        client.ping()

    assert instances[0].closed is True


def test_new_order_requires_client_order_id() -> None:
    with pytest.raises(ValueError, match="client_order_id"):
        BinanceClient(api_key="key", api_secret="secret").new_order(
            symbol="BTCUSDT",
            side="BUY",
            order_type="MARKET",
            quantity=0.002,
        )


@pytest.mark.parametrize(
    ("order_type", "expected_path", "order_kwargs"),
    [
        ("MARKET", "/fapi/v1/order", {"quantity": 0.002}),
        ("STOP_MARKET", "/fapi/v1/algoOrder", {"stop_price": 69000.0, "close_position": True}),
    ],
)
def test_order_submission_timeout_is_not_blind_retried(monkeypatch, order_type, expected_path, order_kwargs) -> None:
    requested_paths: list[str] = []
    events: list[dict[str, object]] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def request(self, method, path, params=None, headers=None):
            requested_paths.append(path)
            raise httpx.ReadTimeout("submit timed out")

    monkeypatch.setattr("trading_mvp.services.binance.httpx.Client", FakeClient)

    with pytest.raises(httpx.ReadTimeout, match="submit timed out"):
        BinanceClient(api_key="key", api_secret="secret", request_error_hook=events.append).new_order(
            symbol="BTCUSDT",
            side="BUY",
            order_type=order_type,
            client_order_id="mvp-client-1",
            **order_kwargs,
        )

    assert requested_paths == [expected_path]
    assert len(events) == 1
    assert events[0]["event"] == "binance_request_error"
    assert events[0]["method"] == "POST"
    assert events[0]["path"] == expected_path
    assert events[0]["endpoint_category"] == "order_submission"
    assert events[0]["error_type"] == "timeout"
    assert events[0]["exception"] == "ReadTimeout"
    assert events[0]["attempt"] == 1
    assert events[0]["attempts"] == 2
    assert events[0]["mutating_request"] is True


def test_timestamp_error_resyncs_with_server_time(monkeypatch) -> None:
    captured_account_timestamps: list[int] = []
    events: list[dict[str, object]] = []
    calls = {"account": 0}

    class FakeResponse:
        def __init__(self, path: str, params: dict[str, Any] | None = None) -> None:
            self.path = path
            self.params = params or {}

        def raise_for_status(self) -> None:
            if self.path == "/fapi/v3/account" and calls["account"] == 0:
                request = httpx.Request("GET", f"https://fapi.binance.com{self.path}")
                response = httpx.Response(
                    400,
                    request=request,
                    json={"code": -1021, "msg": "Timestamp for this request was 1000ms ahead of the server's time."},
                )
                calls["account"] += 1
                raise httpx.HTTPStatusError("bad request", request=request, response=response)

        def json(self) -> dict[str, object]:
            if self.path == "/fapi/v1/time":
                return {"serverTime": 1_700_000_000_000}
            return {"availableBalance": "100", "assets": []}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def request(self, method, path, params=None, headers=None):
            if path == "/fapi/v3/account":
                captured_account_timestamps.append(int((params or {})["timestamp"]))
            return FakeResponse(path, dict(params or {}))

    monkeypatch.setattr(
        "trading_mvp.services.binance.time.time",
        lambda: 1_700_000_001.0,
    )
    monkeypatch.setattr("trading_mvp.services.binance.httpx.Client", FakeClient)

    payload = BinanceClient(api_key="key", api_secret="secret", request_error_hook=events.append).get_account_info()

    assert payload["availableBalance"] == "100"
    assert len(captured_account_timestamps) == 2
    assert captured_account_timestamps[0] == 1_700_000_000_750
    assert captured_account_timestamps[1] == 1_699_999_999_750
    assert events[0]["event"] == "binance_request_error"
    assert events[0]["reason_code"] == "BINANCE_REST_TIME_SYNC_REQUIRED"
    assert events[1]["event"] == "binance_time_sync"
    assert events[1]["offset_ms"] == 1000


def test_normalize_order_quantity_meets_min_notional(monkeypatch) -> None:
    client = BinanceClient()
    monkeypatch.setattr(
        client,
        "get_symbol_filters",
        lambda symbol: {
            "tick_size": 0.1,
            "step_size": 0.001,
            "min_qty": 0.001,
            "min_notional": 100.0,
        },
    )

    adjusted = client.normalize_order_quantity(
        "BTCUSDT",
        0.001,
        reference_price=70000.0,
        enforce_min_notional=True,
    )

    assert adjusted == 0.002


def test_timestamp_error_resyncs_for_signed_post(monkeypatch) -> None:
    captured_order_timestamps: list[int] = []
    calls = {"order": 0}

    class FakeResponse:
        def __init__(self, path: str, params: dict[str, Any] | None = None) -> None:
            self.path = path
            self.params = params or {}

        def raise_for_status(self) -> None:
            if self.path == "/fapi/v1/order/test" and calls["order"] == 0:
                request = httpx.Request("POST", f"https://fapi.binance.com{self.path}")
                response = httpx.Response(
                    400,
                    request=request,
                    json={"code": -1021, "msg": "Timestamp for this request was 1000ms ahead of the server's time."},
                )
                calls["order"] += 1
                raise httpx.HTTPStatusError("bad request", request=request, response=response)

        def json(self) -> dict[str, object]:
            if self.path == "/fapi/v1/time":
                return {"serverTime": 1_700_000_000_000}
            return {"orderId": 0}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def request(self, method, path, params=None, headers=None):
            if path == "/fapi/v1/order/test":
                captured_order_timestamps.append(int((params or {})["timestamp"]))
            return FakeResponse(path, dict(params or {}))

    monkeypatch.setattr("trading_mvp.services.binance.time.time", lambda: 1_700_000_001.0)
    monkeypatch.setattr("trading_mvp.services.binance.httpx.Client", FakeClient)

    payload = BinanceClient(api_key="key", api_secret="secret").test_new_order(
        symbol="BTCUSDT",
        side="BUY",
        quantity=0.002,
    )

    assert payload["orderId"] == 0
    assert len(captured_order_timestamps) == 2


def test_new_order_routes_protective_types_to_algo_endpoint(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "algoId": "algo-1",
                "clientAlgoId": "algo-client-1",
                "algoStatus": "NEW",
                "orderType": "STOP_MARKET",
                "triggerPrice": "69000",
                "quantity": "0.01",
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def request(self, method, path, params=None, headers=None):
            captured["method"] = method
            captured["path"] = path
            captured["params"] = dict(params or {})
            captured["headers"] = dict(headers or {})
            return FakeResponse()

    monkeypatch.setattr("trading_mvp.services.binance.httpx.Client", FakeClient)

    payload = BinanceClient(api_key="key", api_secret="secret").new_order(
        symbol="BTCUSDT",
        side="SELL",
        order_type="STOP_MARKET",
        stop_price=69000.0,
        close_position=True,
        client_order_id="algo-client-1",
        response_type="ACK",
    )

    assert captured["path"] == "/fapi/v1/algoOrder"
    assert captured["params"]["algoType"] == "CONDITIONAL"
    assert captured["params"]["triggerPrice"] == 69000.0
    assert captured["params"]["closePosition"] == "true"
    assert "stopPrice" not in captured["params"]
    assert captured["params"]["clientAlgoId"] == "algo-client-1"
    assert payload["orderId"] == "algo-1"
    assert payload["clientOrderId"] == "algo-client-1"
    assert payload["status"] == "NEW"
    assert payload["type"] == "STOP_MARKET"
    assert payload["stopPrice"] == "69000"


def test_get_open_orders_includes_open_algo_orders(monkeypatch) -> None:
    requested_paths: list[str] = []

    class FakeResponse:
        def __init__(self, path: str) -> None:
            self.path = path

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            if self.path == "/fapi/v1/openOrders":
                return [
                    {
                        "orderId": "spot-1",
                        "clientOrderId": "spot-client-1",
                        "status": "NEW",
                        "type": "LIMIT",
                    }
                ]
            if self.path == "/fapi/v1/openAlgoOrders":
                return [
                    {
                        "algoId": "algo-2",
                        "clientAlgoId": "algo-client-2",
                        "algoStatus": "NEW",
                        "orderType": "TAKE_PROFIT_MARKET",
                        "triggerPrice": "72000",
                        "quantity": "0.01",
                    }
                ]
            return {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def request(self, method, path, params=None, headers=None):
            requested_paths.append(path)
            return FakeResponse(path)

    monkeypatch.setattr("trading_mvp.services.binance.httpx.Client", FakeClient)

    payload = BinanceClient(api_key="key", api_secret="secret").get_open_orders("BTCUSDT")

    assert requested_paths == ["/fapi/v1/openOrders", "/fapi/v1/openAlgoOrders"]
    assert len(payload) == 2
    assert payload[0]["orderId"] == "spot-1"
    assert payload[1]["orderId"] == "algo-2"
    assert payload[1]["clientOrderId"] == "algo-client-2"
    assert payload[1]["type"] == "TAKE_PROFIT_MARKET"
    assert payload[1]["status"] == "NEW"
    assert payload[1]["stopPrice"] == "72000"
