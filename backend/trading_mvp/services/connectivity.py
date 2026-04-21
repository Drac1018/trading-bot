from __future__ import annotations

import os
from datetime import datetime

from trading_mvp.models import Setting
from trading_mvp.providers import OpenAIProvider
from trading_mvp.schemas import (
    BinanceConnectionTestRequest,
    ConnectionTestResponse,
    FredConnectionTestRequest,
    OpenAIConnectionTestRequest,
)
from trading_mvp.services.event_context_adapters import (
    DEFAULT_FRED_RELEASE_IDS,
    ExternalEventFetchPayload,
    FredReleaseDatesAdapter,
)
from trading_mvp.services.binance import BinanceClient
from trading_mvp.services.settings import get_runtime_credentials
from trading_mvp.time_utils import utcnow_naive


def _env_text(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    text = value.strip()
    return text or None


def _env_csv(name: str) -> tuple[str, ...]:
    value = _env_text(name)
    if value is None:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _env_int_csv(name: str) -> tuple[int, ...]:
    values: list[int] = []
    for item in _env_csv(name):
        try:
            parsed = int(item)
        except ValueError:
            continue
        if parsed > 0 and parsed not in values:
            values.append(parsed)
    return tuple(values)


def _normalize_timeout_seconds(value: object, *, default: float = 10.0) -> float:
    try:
        timeout_seconds = float(value) if value is not None else default
    except (TypeError, ValueError):
        timeout_seconds = default
    return max(timeout_seconds, 1.0)


def _normalize_assets(values: object) -> tuple[str, ...]:
    if not isinstance(values, list):
        return ()
    normalized: list[str] = []
    for item in values:
        text = str(item or "").strip().upper()
        if text and text not in normalized:
            normalized.append(text)
    return tuple(normalized)


def _normalize_release_ids(values: object) -> tuple[int, ...]:
    if not isinstance(values, list):
        return ()
    normalized: list[int] = []
    for item in values:
        try:
            parsed = int(item)
        except (TypeError, ValueError):
            continue
        if parsed > 0 and parsed not in normalized:
            normalized.append(parsed)
    return tuple(normalized)


def _next_event_details(result: ExternalEventFetchPayload) -> dict[str, object]:
    next_event_name: str | None = None
    next_event_at: str | None = None
    next_event_assets: list[str] = []
    next_event_time: datetime | None = None
    for item in result.events:
        event_at = item.get("event_at")
        if not isinstance(event_at, datetime):
            continue
        if next_event_time is not None and event_at >= next_event_time:
            continue
        next_event_time = event_at
        next_event_name = str(item.get("event_name") or "").strip() or None
        next_event_at = event_at.isoformat()
        raw_assets = item.get("affected_assets")
        if isinstance(raw_assets, list):
            next_event_assets = [str(asset) for asset in raw_assets if str(asset or "").strip()]
        else:
            next_event_assets = []
    return {
        "next_event_name": next_event_name,
        "next_event_at": next_event_at,
        "next_event_affected_assets": next_event_assets,
    }


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


def check_fred_connection(
    settings_row: Setting,
    request: FredConnectionTestRequest,
) -> ConnectionTestResponse:
    credentials = get_runtime_credentials(settings_row)
    api_key = request.api_key or credentials.event_source_api_key
    if not api_key:
        return ConnectionTestResponse(
            ok=False,
            provider="fred",
            message="FRED API 키가 없습니다.",
            details={},
        )

    release_ids = (
        _normalize_release_ids(request.release_ids)
        or _normalize_release_ids(settings_row.event_source_fred_release_ids)
        or _env_int_csv("TRADING_EVENT_SOURCE_FRED_RELEASE_IDS")
        or DEFAULT_FRED_RELEASE_IDS
    )
    default_assets = (
        _normalize_assets(request.default_assets)
        or _normalize_assets(settings_row.event_source_default_assets)
        or _env_csv("TRADING_EVENT_SOURCE_DEFAULT_ASSETS")
        or (request.symbol.upper(),)
    )
    timeout_seconds = _normalize_timeout_seconds(
        request.timeout_seconds
        if request.timeout_seconds is not None
        else settings_row.event_source_timeout_seconds
        if settings_row.event_source_timeout_seconds is not None
        else _env_text("TRADING_EVENT_SOURCE_TIMEOUT_SECONDS"),
    )
    base_url = (
        (request.api_url.strip() if request.api_url else "")
        or (settings_row.event_source_api_url or "").strip()
        or (_env_text("TRADING_EVENT_SOURCE_API_URL") or "")
        or "https://api.stlouisfed.org/fred"
    )
    adapter = FredReleaseDatesAdapter(
        api_key=api_key,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        default_assets=default_assets,
        release_ids=release_ids,
    )
    try:
        result = adapter.fetch_event_context(
            symbol=request.symbol.upper(),
            timeframe=request.timeframe,
            generated_at=utcnow_naive(),
        )
    except Exception as exc:
        return ConnectionTestResponse(
            ok=False,
            provider="fred",
            message="FRED 연결 확인에 실패했습니다.",
            details={"error": str(exc), "base_url": base_url, "release_ids": list(release_ids)},
        )

    details = {
        "source_status": result.source_status,
        "source_provenance": "external_api",
        "event_count": len(result.events),
        "symbol": request.symbol.upper(),
        "timeframe": request.timeframe,
        "base_url": base_url,
        "timeout_seconds": timeout_seconds,
        "release_ids": list(release_ids),
        "default_assets": list(default_assets),
        **_next_event_details(result),
    }
    if result.source_status == "external_api":
        return ConnectionTestResponse(
            ok=True,
            provider="fred",
            message="FRED 연결과 캘린더 응답이 확인되었습니다.",
            details=details,
        )
    if result.source_status == "unavailable":
        return ConnectionTestResponse(
            ok=True,
            provider="fred",
            message="FRED 연결은 확인됐지만 예정된 release를 찾지 못했습니다.",
            details=details,
        )
    if result.source_status == "incomplete":
        return ConnectionTestResponse(
            ok=False,
            provider="fred",
            message="FRED 응답은 도착했지만 일부 release 파싱이 불완전합니다.",
            details=details,
        )
    return ConnectionTestResponse(
        ok=False,
        provider="fred",
        message="FRED 연결 확인에 실패했습니다.",
        details=details,
    )
