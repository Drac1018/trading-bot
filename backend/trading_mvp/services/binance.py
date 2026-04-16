from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from typing import Any, cast
from urllib.parse import urlencode

import httpx
import websockets

from trading_mvp.schemas import MarketCandle

JsonDict = dict[str, Any]
ALGO_ORDER_TYPES = {"STOP_MARKET", "TAKE_PROFIT_MARKET", "STOP", "TAKE_PROFIT", "TRAILING_STOP_MARKET"}


class BinanceAPIError(RuntimeError):
    def __init__(self, code: object, message: object, *, status_code: int | None = None) -> None:
        self.code = int(code) if isinstance(code, (int, float)) else None
        self.status_code = status_code
        self.api_message = str(message or "Unknown Binance error")
        super().__init__(f"Binance error {self.code}: {self.api_message}" if self.code is not None else self.api_message)


class BinanceClient:
    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        *,
        testnet_enabled: bool = False,
        futures_enabled: bool = True,
        timeout_seconds: float = 10.0,
        recv_window_ms: int = 5000,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet_enabled = testnet_enabled
        self.futures_enabled = futures_enabled
        self.timeout_seconds = timeout_seconds
        self.recv_window_ms = recv_window_ms
        self._server_time_offset_ms: int | None = None
        self._timestamp_safety_margin_ms = 250
        if futures_enabled:
            self.base_url = "https://testnet.binancefuture.com" if testnet_enabled else "https://fapi.binance.com"
        else:
            self.base_url = "https://testnet.binance.vision" if testnet_enabled else "https://api.binance.com"

    def _sign(self, query: str) -> str:
        return hmac.new(
            self.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int | float | bool] | None = None,
        signed: bool = False,
        api_key_only: bool = False,
        retryable: bool | None = None,
    ) -> dict[str, object] | list[object]:
        attempts = 3 if (retryable if retryable is not None else method.upper() == "GET") else 1
        if signed:
            attempts = max(attempts, 2)
        last_error: Exception | None = None
        time_sync_attempted = False
        for attempt in range(attempts):
            query_params: dict[str, str | int | float | bool] = dict(params or {})
            headers: dict[str, str] = {}
            if signed:
                if not self.api_key or not self.api_secret:
                    raise RuntimeError("Binance signed endpoints require both API key and secret.")
                headers["X-MBX-APIKEY"] = self.api_key
            elif api_key_only:
                if not self.api_key:
                    raise RuntimeError("Binance API-key endpoints require an API key.")
                headers["X-MBX-APIKEY"] = self.api_key
            if signed:
                query_params["timestamp"] = self._signed_timestamp_ms()
                query_params["recvWindow"] = self.recv_window_ms
                query = urlencode(query_params)
                query_params["signature"] = self._sign(query)
            try:
                with httpx.Client(base_url=self.base_url, timeout=self.timeout_seconds) as client:
                    response = client.request(method, path, params=query_params, headers=headers)
                    response.raise_for_status()
                    payload = cast(dict[str, object] | list[object], response.json())
                    return payload
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_error = exc
                if isinstance(exc, httpx.HTTPStatusError):
                    try:
                        error_payload = exc.response.json()
                    except ValueError:
                        error_payload = {"error": exc.response.text}
                    if isinstance(error_payload, Mapping):
                        code = error_payload.get("code")
                        message = error_payload.get("msg") or error_payload.get("message") or error_payload.get("error")
                        if signed and code == -1021 and not time_sync_attempted:
                            self._refresh_server_time_offset_ms()
                            time_sync_attempted = True
                            continue
                        if code is not None or message:
                            raise BinanceAPIError(code, message, status_code=exc.response.status_code) from exc
                if attempt == attempts - 1:
                    raise
                time.sleep(0.35 * (attempt + 1))
        raise RuntimeError(str(last_error))

    def _server_time_path(self) -> str:
        return "/fapi/v1/time" if self.futures_enabled else "/api/v3/time"

    def _fetch_server_time_ms(self) -> int:
        with httpx.Client(base_url=self.base_url, timeout=self.timeout_seconds) as client:
            response = client.request("GET", self._server_time_path())
            response.raise_for_status()
            payload = cast(dict[str, object], response.json())
        server_time = payload.get("serverTime")
        if not isinstance(server_time, int | float):
            raise RuntimeError("Unexpected Binance server time response.")
        return int(server_time)

    def _refresh_server_time_offset_ms(self) -> int:
        local_now_ms = int(time.time() * 1000)
        server_now_ms = self._fetch_server_time_ms()
        self._server_time_offset_ms = local_now_ms - server_now_ms
        return self._server_time_offset_ms

    def _signed_timestamp_ms(self) -> int:
        local_now_ms = int(time.time() * 1000)
        if self._server_time_offset_ms is None:
            return local_now_ms - self._timestamp_safety_margin_ms
        return local_now_ms - self._server_time_offset_ms - self._timestamp_safety_margin_ms

    @staticmethod
    def _as_dict(payload: dict[str, object] | list[object], message: str) -> JsonDict:
        if not isinstance(payload, Mapping):
            raise RuntimeError(message)
        return dict(payload)

    @staticmethod
    def _is_algo_order_type(order_type: str) -> bool:
        return order_type.upper() in ALGO_ORDER_TYPES

    @staticmethod
    def _normalize_algo_order_payload(payload: Mapping[str, object]) -> JsonDict:
        result = dict(payload)
        if "algoId" in result and "orderId" not in result:
            result["orderId"] = result["algoId"]
        if "clientAlgoId" in result and "clientOrderId" not in result:
            result["clientOrderId"] = result["clientAlgoId"]
        if "algoStatus" in result and "status" not in result:
            result["status"] = result["algoStatus"]
        if "orderType" in result and "type" not in result:
            result["type"] = result["orderType"]
        if "triggerPrice" in result and "stopPrice" not in result:
            result["stopPrice"] = result["triggerPrice"]
        if "quantity" in result and "origQty" not in result:
            result["origQty"] = result["quantity"]
        return result

    def ping(self) -> dict[str, object]:
        payload = self._request("GET", "/fapi/v1/ping")
        return self._as_dict(payload, "Unexpected Binance ping response.")

    def fetch_klines(self, symbol: str, interval: str, limit: int = 60) -> list[MarketCandle]:
        raw = self._request(
            "GET",
            "/fapi/v1/klines",
            params={"symbol": symbol.upper(), "interval": interval, "limit": limit},
        )
        if not isinstance(raw, list):
            raise RuntimeError("Unexpected Binance kline response.")
        candles: list[MarketCandle] = []
        for item in raw:
            if not isinstance(item, list):
                raise RuntimeError("Unexpected Binance kline item.")
            row = cast(list[Any], item)
            candles.append(
                MarketCandle(
                    timestamp=datetime.fromtimestamp(float(row[0]) / 1000, UTC).replace(tzinfo=None),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
            )
        return candles

    def get_account_info(self) -> dict[str, object]:
        payload = self._request("GET", "/fapi/v3/account", signed=True)
        return self._as_dict(payload, "Unexpected Binance account response.")

    def get_exchange_info(self, symbol: str | None = None) -> dict[str, object]:
        payload = self._request("GET", "/fapi/v1/exchangeInfo")
        result = self._as_dict(payload, "Unexpected Binance exchange info response.")
        if symbol is None:
            return result
        symbols = result.get("symbols", [])
        if not isinstance(symbols, list):
            raise RuntimeError("Unexpected Binance exchange info symbols payload.")
        filtered = [item for item in symbols if isinstance(item, Mapping) and item.get("symbol") == symbol.upper()]
        return {**result, "symbols": filtered}

    def get_symbol_filters(self, symbol: str) -> dict[str, float]:
        info = self.get_exchange_info(symbol)
        symbols = info.get("symbols")
        if not isinstance(symbols, list) or not symbols:
            raise RuntimeError("No symbol metadata returned by Binance.")
        symbol_info = cast(dict[str, Any], symbols[0])
        filters = symbol_info.get("filters", [])
        if not isinstance(filters, list):
            raise RuntimeError("Unexpected Binance symbol filters.")

        output: dict[str, float] = {
            "tick_size": 0.0,
            "step_size": 0.0,
            "min_qty": 0.0,
            "min_notional": 0.0,
        }
        for item in filters:
            if not isinstance(item, Mapping):
                continue
            filter_type = str(item.get("filterType", ""))
            if filter_type == "PRICE_FILTER":
                output["tick_size"] = float(item.get("tickSize", 0.0))
            elif filter_type == "LOT_SIZE":
                output["step_size"] = float(item.get("stepSize", 0.0))
                output["min_qty"] = float(item.get("minQty", 0.0))
            elif filter_type == "MIN_NOTIONAL":
                output["min_notional"] = float(item.get("notional", item.get("minNotional", 0.0)))
        return output

    @staticmethod
    def _quantize(value: float, step: float) -> float:
        if step <= 0:
            return value
        decimal_value = Decimal(str(value))
        decimal_step = Decimal(str(step))
        return float((decimal_value // decimal_step) * decimal_step)

    @staticmethod
    def _quantize_up(value: float, step: float) -> float:
        if step <= 0:
            return value
        decimal_value = Decimal(str(value))
        decimal_step = Decimal(str(step))
        units = (decimal_value / decimal_step).to_integral_value(rounding=ROUND_CEILING)
        return float(units * decimal_step)

    def normalize_quantity(self, symbol: str, quantity: float) -> float:
        filters = self.get_symbol_filters(symbol)
        normalized = self._quantize(quantity, filters["step_size"])
        if normalized < filters["min_qty"]:
            normalized = filters["min_qty"]
        return normalized

    def get_symbol_price(self, symbol: str) -> float:
        payload = self._request("GET", "/fapi/v1/ticker/price", params={"symbol": symbol.upper()})
        result = self._as_dict(payload, "Unexpected Binance ticker price response.")
        return float(result.get("price", 0.0))

    def normalize_order_quantity(
        self,
        symbol: str,
        quantity: float,
        *,
        reference_price: float | None = None,
        enforce_min_notional: bool = True,
    ) -> float:
        filters = self.get_symbol_filters(symbol)
        step_size = filters["step_size"]
        normalized = self._quantize(quantity, step_size)
        if normalized < filters["min_qty"]:
            normalized = filters["min_qty"]
        if not enforce_min_notional:
            return normalized
        min_notional = filters["min_notional"]
        if min_notional <= 0:
            return normalized
        price = reference_price or self.get_symbol_price(symbol)
        if price <= 0:
            return normalized
        notional = normalized * price
        if notional >= min_notional:
            return normalized
        required_quantity = self._quantize_up(min_notional / price, step_size)
        if required_quantity < filters["min_qty"]:
            required_quantity = filters["min_qty"]
        return required_quantity

    def normalize_price(self, symbol: str, price: float) -> float:
        filters = self.get_symbol_filters(symbol)
        return self._quantize(price, filters["tick_size"])

    def normalize_order_request(
        self,
        *,
        symbol: str,
        quantity: float | None = None,
        price: float | None = None,
        stop_price: float | None = None,
        reference_price: float | None = None,
        approved_notional: float | None = None,
        enforce_min_notional: bool = True,
        close_position: bool = False,
    ) -> dict[str, object]:
        filters = self.get_symbol_filters(symbol)
        normalized_price = self._quantize(price, filters["tick_size"]) if price is not None else None
        normalized_stop_price = self._quantize(stop_price, filters["tick_size"]) if stop_price is not None else None
        effective_reference_price = (
            normalized_price
            if normalized_price is not None and normalized_price > 0
            else normalized_stop_price
            if normalized_stop_price is not None and normalized_stop_price > 0
            else reference_price
        )
        if effective_reference_price is None or effective_reference_price <= 0:
            effective_reference_price = reference_price if reference_price and reference_price > 0 else None
        normalized_quantity: float | None = None
        reason_code: str | None = None
        notional: float | None = None
        min_qty = float(filters.get("min_qty", 0.0))
        min_notional = float(filters.get("min_notional", 0.0))
        if quantity is not None:
            normalized_quantity = self._quantize(abs(quantity), filters["step_size"])
            if approved_notional is not None and approved_notional > 0 and effective_reference_price and effective_reference_price > 0:
                max_quantity = approved_notional / effective_reference_price
                normalized_quantity = min(normalized_quantity, self._quantize(max_quantity, filters["step_size"]))
            if normalized_quantity <= 0:
                reason_code = "ORDER_QTY_ZERO_AFTER_STEP_SIZE"
            elif normalized_quantity < min_qty:
                reason_code = "ORDER_QTY_BELOW_MIN_QTY"
            else:
                notional = normalized_quantity * (effective_reference_price or 0.0)
                if (
                    enforce_min_notional
                    and not close_position
                    and min_notional > 0
                    and effective_reference_price is not None
                    and effective_reference_price > 0
                    and notional < min_notional
                ):
                    reason_code = "ORDER_NOTIONAL_BELOW_MIN_NOTIONAL"
        return {
            "symbol": symbol.upper(),
            "quantity": normalized_quantity,
            "price": normalized_price,
            "stop_price": normalized_stop_price,
            "reference_price": effective_reference_price,
            "notional": notional,
            "filters": filters,
            "reason_code": reason_code,
        }

    def create_futures_listen_key(self) -> str:
        payload = self._request("POST", "/fapi/v1/listenKey", api_key_only=True, retryable=False)
        result = self._as_dict(payload, "Unexpected Binance futures listen key response.")
        listen_key = str(result.get("listenKey") or "")
        if not listen_key:
            raise RuntimeError("Binance futures listen key was not returned.")
        return listen_key

    def keepalive_futures_listen_key(self, listen_key: str) -> dict[str, object]:
        payload = self._request(
            "PUT",
            "/fapi/v1/listenKey",
            params={"listenKey": listen_key},
            api_key_only=True,
            retryable=False,
        )
        return self._as_dict(payload, "Unexpected Binance futures listen key keepalive response.")

    def close_futures_listen_key(self, listen_key: str) -> dict[str, object]:
        payload = self._request(
            "DELETE",
            "/fapi/v1/listenKey",
            params={"listenKey": listen_key},
            api_key_only=True,
            retryable=False,
        )
        return self._as_dict(payload, "Unexpected Binance futures listen key close response.")

    def build_futures_user_stream_url(self, listen_key: str) -> str:
        ws_base = "wss://stream.binancefuture.com" if self.testnet_enabled else "wss://fstream.binance.com"
        return f"{ws_base}/ws/{listen_key}"

    async def stream_futures_user_events(
        self,
        listen_key: str,
        *,
        max_events: int | None = None,
        idle_timeout_seconds: float = 30.0,
    ):
        url = self.build_futures_user_stream_url(listen_key)
        received = 0
        async with websockets.connect(url, ping_interval=15, ping_timeout=15, close_timeout=5) as websocket:
            while max_events is None or received < max_events:
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=max(idle_timeout_seconds, 0.01))
                except TimeoutError:
                    break
                if isinstance(message, bytes):
                    message = message.decode("utf-8")
                payload = json.loads(message)
                if isinstance(payload, dict):
                    yield payload
                    received += 1

    def change_initial_leverage(self, symbol: str, leverage: int) -> dict[str, object]:
        payload = self._request(
            "POST",
            "/fapi/v1/leverage",
            params={"symbol": symbol.upper(), "leverage": leverage},
            signed=True,
            retryable=False,
        )
        return self._as_dict(payload, "Unexpected Binance leverage response.")

    def new_order(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float | None = None,
        price: float | None = None,
        stop_price: float | None = None,
        reduce_only: bool = False,
        close_position: bool = False,
        client_order_id: str | None = None,
        response_type: str = "RESULT",
        working_type: str = "MARK_PRICE",
        time_in_force: str | None = None,
    ) -> dict[str, object]:
        if self.futures_enabled and self._is_algo_order_type(order_type):
            algo_params: dict[str, str | int | float | bool] = {
                "algoType": "CONDITIONAL",
                "symbol": symbol.upper(),
                "side": side,
                "type": order_type,
                "newOrderRespType": response_type,
                "workingType": working_type,
            }
            if quantity is not None and not close_position:
                algo_params["quantity"] = quantity
            if price is not None:
                algo_params["price"] = price
            if stop_price is not None:
                algo_params["triggerPrice"] = stop_price
            if reduce_only and not close_position:
                algo_params["reduceOnly"] = "true"
            if close_position:
                algo_params["closePosition"] = "true"
            if client_order_id:
                algo_params["clientAlgoId"] = client_order_id
            payload = self._request("POST", "/fapi/v1/algoOrder", params=algo_params, signed=True, retryable=False)
            return self._normalize_algo_order_payload(
                self._as_dict(payload, "Unexpected Binance new algo order response.")
            )

        params: dict[str, str | int | float | bool] = {
            "symbol": symbol.upper(),
            "side": side,
            "type": order_type,
            "newOrderRespType": response_type,
            "workingType": working_type,
        }
        if quantity is not None:
            params["quantity"] = quantity
        if price is not None:
            params["price"] = price
        if time_in_force is not None:
            params["timeInForce"] = time_in_force
        if stop_price is not None:
            params["stopPrice"] = stop_price
        if reduce_only:
            params["reduceOnly"] = "true"
        if close_position:
            params["closePosition"] = "true"
        if client_order_id:
            params["newClientOrderId"] = client_order_id
        payload = self._request("POST", "/fapi/v1/order", params=params, signed=True, retryable=False)
        return self._as_dict(payload, "Unexpected Binance new order response.")

    def get_algo_order(
        self,
        *,
        algo_id: str | None = None,
        client_algo_id: str | None = None,
    ) -> dict[str, object]:
        params: dict[str, str] = {}
        if algo_id:
            params["algoId"] = algo_id
        if client_algo_id:
            params["clientAlgoId"] = client_algo_id
        payload = self._request("GET", "/fapi/v1/algoOrder", params=params, signed=True)
        return self._normalize_algo_order_payload(
            self._as_dict(payload, "Unexpected Binance algo order response.")
        )

    def fetch_order(
        self,
        *,
        symbol: str,
        order_type: str | None = None,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, object]:
        if self.futures_enabled and order_type and self._is_algo_order_type(order_type):
            return self.get_algo_order(algo_id=order_id, client_algo_id=client_order_id)
        return self.get_order(symbol=symbol, order_id=order_id, client_order_id=client_order_id)

    def get_open_algo_orders(
        self,
        symbol: str | None = None,
    ) -> list[dict[str, object]]:
        params = {"symbol": symbol.upper()} if symbol else None
        payload = self._request("GET", "/fapi/v1/openAlgoOrders", params=params, signed=True)
        if not isinstance(payload, list):
            raise RuntimeError("Unexpected Binance open algo orders response.")
        return [
            self._normalize_algo_order_payload(cast(Mapping[str, object], item))
            for item in payload
            if isinstance(item, Mapping)
        ]

    def test_new_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
    ) -> dict[str, object]:
        params: dict[str, str | int | float] = {
            "symbol": symbol.upper(),
            "side": side,
            "type": "MARKET",
            "quantity": quantity,
        }
        payload = self._request("POST", "/fapi/v1/order/test", params=params, signed=True, retryable=False)
        return self._as_dict(payload, "Unexpected Binance test order response.")

    def cancel_order(
        self,
        *,
        symbol: str,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, object]:
        params: dict[str, str] = {"symbol": symbol.upper()}
        if order_id:
            params["orderId"] = order_id
        if client_order_id:
            params["origClientOrderId"] = client_order_id
        payload = self._request("DELETE", "/fapi/v1/order", params=params, signed=True, retryable=False)
        return self._as_dict(payload, "Unexpected Binance cancel order response.")

    def cancel_algo_order(
        self,
        *,
        algo_id: str | None = None,
        client_algo_id: str | None = None,
    ) -> dict[str, object]:
        params: dict[str, str] = {}
        if algo_id:
            params["algoId"] = algo_id
        if client_algo_id:
            params["clientAlgoId"] = client_algo_id
        payload = self._request("DELETE", "/fapi/v1/algoOrder", params=params, signed=True, retryable=False)
        return self._normalize_algo_order_payload(
            self._as_dict(payload, "Unexpected Binance cancel algo order response.")
        )

    def cancel_exchange_order(
        self,
        *,
        symbol: str,
        order_type: str | None = None,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, object]:
        if self.futures_enabled and order_type and self._is_algo_order_type(order_type):
            return self.cancel_algo_order(algo_id=order_id, client_algo_id=client_order_id)
        return self.cancel_order(symbol=symbol, order_id=order_id, client_order_id=client_order_id)

    def cancel_all_open_orders(self, symbol: str) -> dict[str, object]:
        payload = self._request(
            "DELETE",
            "/fapi/v1/allOpenOrders",
            params={"symbol": symbol.upper()},
            signed=True,
            retryable=False,
        )
        return self._as_dict(payload, "Unexpected Binance cancel all response.")

    def get_order(
        self,
        *,
        symbol: str,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, object]:
        params: dict[str, str] = {"symbol": symbol.upper()}
        if order_id:
            params["orderId"] = order_id
        if client_order_id:
            params["origClientOrderId"] = client_order_id
        payload = self._request("GET", "/fapi/v1/order", params=params, signed=True)
        return self._as_dict(payload, "Unexpected Binance order response.")

    def get_open_orders(self, symbol: str | None = None) -> list[dict[str, object]]:
        params = {"symbol": symbol.upper()} if symbol else None
        payload = self._request("GET", "/fapi/v1/openOrders", params=params, signed=True)
        if not isinstance(payload, list):
            raise RuntimeError("Unexpected Binance open orders response.")
        orders = [dict(cast(Mapping[str, object], item)) for item in payload if isinstance(item, Mapping)]
        if self.futures_enabled:
            orders.extend(self.get_open_algo_orders(symbol))
        return orders

    def get_account_trades(
        self,
        *,
        symbol: str,
        order_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        params: dict[str, str | int] = {"symbol": symbol.upper(), "limit": limit}
        if order_id:
            params["orderId"] = order_id
        payload = self._request("GET", "/fapi/v1/userTrades", params=params, signed=True)
        if not isinstance(payload, list):
            raise RuntimeError("Unexpected Binance trades response.")
        return [dict(cast(Mapping[str, object], item)) for item in payload if isinstance(item, Mapping)]

    def get_income_history(
        self,
        *,
        income_type: str | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 100,
        symbol: str | None = None,
    ) -> list[dict[str, object]]:
        params: dict[str, str | int] = {"limit": limit}
        if income_type:
            params["incomeType"] = income_type
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        if symbol:
            params["symbol"] = symbol.upper()
        payload = self._request("GET", "/fapi/v1/income", params=params, signed=True)
        if not isinstance(payload, list):
            raise RuntimeError("Unexpected Binance income history response.")
        return [dict(cast(Mapping[str, object], item)) for item in payload if isinstance(item, Mapping)]

    def get_position_information(self, symbol: str | None = None) -> list[dict[str, object]]:
        params = {"symbol": symbol.upper()} if symbol else None
        payload = self._request("GET", "/fapi/v3/positionRisk", params=params, signed=True)
        if not isinstance(payload, list):
            raise RuntimeError("Unexpected Binance position response.")
        return [dict(cast(Mapping[str, object], item)) for item in payload if isinstance(item, Mapping)]

    def get_position_mode(self) -> dict[str, object]:
        payload = self._request("GET", "/fapi/v1/positionSide/dual", signed=True)
        result = self._as_dict(payload, "Unexpected Binance position mode response.")
        dual_side_position = result.get("dualSidePosition")
        if isinstance(dual_side_position, bool):
            dual_side_enabled = dual_side_position
        elif isinstance(dual_side_position, str):
            dual_side_enabled = dual_side_position.strip().lower() == "true"
        else:
            dual_side_enabled = None
        mode = "unknown"
        if dual_side_enabled is True:
            mode = "hedge"
        elif dual_side_enabled is False:
            mode = "one_way"
        return {
            "mode": mode,
            "dual_side_position": dual_side_enabled,
            "raw_payload": result,
        }

    def test_connection(self, symbol: str, timeframe: str) -> dict[str, object]:
        self.ping()
        candles = self.fetch_klines(symbol=symbol, interval=timeframe, limit=2)
        result: dict[str, object] = {
            "market_data_ok": True,
            "latest_price": candles[-1].close,
            "latest_candle_time": candles[-1].timestamp.isoformat(),
            "base_url": self.base_url,
        }
        if self.api_key and self.api_secret:
            account = self.get_account_info()
            assets = account.get("assets", [])
            available_balance = account.get("availableBalance")
            result.update(
                {
                    "credentials_ok": True,
                    "asset_count": len(assets) if isinstance(assets, list) else 0,
                    "available_balance": available_balance,
                }
            )
        else:
            result["credentials_ok"] = False
        return result
