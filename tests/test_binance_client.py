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
        BinanceClient(api_key="key", api_secret="secret").get_account_info()


def test_timestamp_error_resyncs_with_server_time(monkeypatch) -> None:
    captured_account_timestamps: list[int] = []
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

    payload = BinanceClient(api_key="key", api_secret="secret").get_account_info()

    assert payload["availableBalance"] == "100"
    assert len(captured_account_timestamps) == 2
    assert captured_account_timestamps[0] == 1_700_000_000_750
    assert captured_account_timestamps[1] == 1_699_999_999_750


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
