from __future__ import annotations

from trading_mvp.models import Setting
from trading_mvp.providers import OpenAIProvider
from trading_mvp.schemas import (
    BinanceConnectionTestRequest,
    ConnectionTestResponse,
    OpenAIConnectionTestRequest,
)
from trading_mvp.services.binance import BinanceClient
from trading_mvp.services.settings import get_runtime_credentials


def check_openai_connection(
    settings_row: Setting,
    request: OpenAIConnectionTestRequest,
) -> ConnectionTestResponse:
    credentials = get_runtime_credentials(settings_row)
    api_key = request.api_key or credentials.openai_api_key
    if not api_key:
        return ConnectionTestResponse(
            ok=False,
            provider="openai",
            message="OpenAI API 키가 없습니다.",
            details={},
        )

    try:
        details = OpenAIProvider(api_key=api_key, model=request.model).test_connection()
        return ConnectionTestResponse(
            ok=True,
            provider="openai",
            message="OpenAI 연결이 확인되었습니다.",
            details=details,
        )
    except Exception as exc:
        return ConnectionTestResponse(
            ok=False,
            provider="openai",
            message="OpenAI 연결 확인에 실패했습니다.",
            details={"error": str(exc)},
        )


def check_binance_connection(
    settings_row: Setting,
    request: BinanceConnectionTestRequest,
) -> ConnectionTestResponse:
    credentials = get_runtime_credentials(settings_row)
    api_key = request.api_key or credentials.binance_api_key
    api_secret = request.api_secret or credentials.binance_api_secret
    client = BinanceClient(
        api_key=api_key,
        api_secret=api_secret,
        testnet_enabled=request.testnet_enabled,
        futures_enabled=True,
    )
    try:
        details = client.test_connection(symbol=request.symbol, timeframe=request.timeframe)
        return ConnectionTestResponse(
            ok=True,
            provider="binance",
            message="Binance 연결이 확인되었습니다.",
            details=details,
        )
    except Exception as exc:
        return ConnectionTestResponse(
            ok=False,
            provider="binance",
            message="Binance 연결 확인에 실패했습니다.",
            details={"error": str(exc), "base_url": client.base_url},
        )
