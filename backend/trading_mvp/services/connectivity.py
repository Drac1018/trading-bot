from __future__ import annotations

from trading_mvp.models import Setting
from trading_mvp.providers import OpenAIProvider
from trading_mvp.schemas import (
    BinanceConnectionTestRequest,
    ConnectionTestResponse,
    OpenAIConnectionTestRequest,
)
from trading_mvp.services.binance import BinanceClient, classify_binance_rest_exception
from trading_mvp.services.runtime_state import (
    get_binance_rest_detail,
    record_binance_rest_issue,
    record_binance_rest_success,
)
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
        with client:
            details = client.test_connection(symbol=request.symbol, timeframe=request.timeframe)
        details["binance_rest_summary"] = record_binance_rest_success(
            settings_row,
            source="connection_test",
            detail={"base_url": client.base_url},
        )
        return ConnectionTestResponse(
            ok=True,
            provider="binance",
            message="Binance 연결이 확인되었습니다.",
            details=details,
        )
    except Exception as exc:
        classification = classify_binance_rest_exception(exc)
        rest_summary = record_binance_rest_issue(
            settings_row,
            reason_code=str(classification["reason_code"]),
            source="connection_test",
            error=str(exc),
            failure_type=str(classification.get("failure_type") or "request_error"),
            http_status=classification.get("http_status"),  # type: ignore[arg-type]
            api_code=classification.get("api_code"),  # type: ignore[arg-type]
            mutating_request=bool(classification.get("mutating_request", False)),
            transport_error=bool(classification.get("transport_error", False)),
            server_error=bool(classification.get("server_error", False)),
            rate_limited=bool(classification.get("rate_limited", False)),
            detail={"base_url": client.base_url},
        )
        return ConnectionTestResponse(
            ok=False,
            provider="binance",
            message="Binance 연결 확인에 실패했습니다.",
            details={
                "error": str(exc),
                "base_url": client.base_url,
                "binance_rest_summary": get_binance_rest_detail(settings_row),
                "binance_rest_failure": classification,
                "binance_rest_recorded": rest_summary,
            },
        )


