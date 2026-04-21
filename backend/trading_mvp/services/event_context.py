from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

import httpx

from trading_mvp.schemas import (
    EventBias,
    EventContextPayload,
    EventSourceProvenance,
    EventSourceStatus,
    EventSourceVendor,
    MacroEventPayload,
    OperatorActiveRiskWindowPayload,
    OperatorEventContextPayload,
    OperatorEventImportance,
    OperatorEventItemPayload,
    OperatorEventSourceStatus,
)
from trading_mvp.services.event_context_adapters import (
    BEAActualReleaseEnrichmentAdapter,
    BLSActualReleaseEnrichmentAdapter,
    ExternalEventFetchPayload,
    ExternalEventFetcher,
    ExternalMacroEventAdapter,
    FredReleaseDatesAdapter,
    PostReleaseEventEnrichmentAdapter,
)
from trading_mvp.time_utils import ensure_utc_aware, parse_utc_datetime, utcnow_aware


def _normalized_assets(values: object) -> list[str]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    assets: list[str] = []
    for item in values:
        text = str(item or "").strip().upper()
        if text and text not in assets:
            assets.append(text)
    return assets


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return ensure_utc_aware(value).replace(tzinfo=None) if value.tzinfo is not None else value.replace(tzinfo=None)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        return ensure_utc_aware(parsed).replace(tzinfo=None) if parsed.tzinfo is not None else parsed.replace(tzinfo=None)
    return None


def _parse_aware_datetime(value: object) -> datetime | None:
    return parse_utc_datetime(value)


def _minutes_until(*, generated_at: datetime, event_at: datetime) -> int:
    return int((event_at - generated_at).total_seconds() // 60)


def _is_active_risk_window(
    *,
    generated_at: datetime,
    event_at: datetime,
    risk_window_before_minutes: int,
    risk_window_after_minutes: int,
) -> bool:
    window_start = event_at - timedelta(minutes=risk_window_before_minutes)
    window_end = event_at + timedelta(minutes=risk_window_after_minutes)
    return window_start <= generated_at <= window_end


def _coerce_source_status(value: object, *, default: EventSourceStatus = "error") -> EventSourceStatus:
    text = str(value or "").strip().lower()
    if text == "available":
        return "external_api"
    if text in {"fixture", "stub", "external_api", "stale", "incomplete", "unavailable", "error"}:
        return text  # type: ignore[return-value]
    return default


def _normalize_source_provenance(
    value: object,
    *,
    fallback_status: object = None,
) -> EventSourceProvenance | None:
    text = str(value or "").strip().lower()
    if text in {"fixture", "stub", "external_api"}:
        return text  # type: ignore[return-value]
    fallback_text = str(fallback_status or "").strip().lower()
    if fallback_text in {"fixture", "stub", "external_api"}:
        return fallback_text  # type: ignore[return-value]
    return None


def _normalize_source_vendor(value: object) -> EventSourceVendor | None:
    text = str(value or "").strip().lower()
    if text in {"fred", "bls", "bea"}:
        return text  # type: ignore[return-value]
    return None


def _normalize_vendor_list(values: object) -> list[EventSourceVendor]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    normalized: list[EventSourceVendor] = []
    for item in values:
        vendor_name = _normalize_source_vendor(item)
        if vendor_name is not None and vendor_name not in normalized:
            normalized.append(vendor_name)
    return normalized


def _normalize_release_enrichment(value: object) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping):
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for raw_vendor, raw_payload in value.items():
        vendor_name = _normalize_source_vendor(raw_vendor)
        if vendor_name is None or not isinstance(raw_payload, Mapping):
            continue
        payload: dict[str, Any] = {}
        for raw_key, raw_value in raw_payload.items():
            key = str(raw_key or "").strip()
            if not key or raw_value in {None, ""}:
                continue
            if isinstance(raw_value, datetime):
                normalized_datetime = _parse_datetime(raw_value)
                if normalized_datetime is not None:
                    payload[key] = normalized_datetime.isoformat()
                continue
            payload[key] = raw_value
        if payload:
            normalized[vendor_name] = payload
    return normalized


def _safe_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _build_event_context_payload(
    *,
    raw_events: Sequence[Mapping[str, object]] | None,
    generated_at: datetime,
    source_status: EventSourceStatus,
    source_provenance: EventSourceProvenance | None,
    source_vendor: EventSourceVendor | None = None,
    enrichment_vendors: Sequence[EventSourceVendor] | None = None,
    source_generated_at: datetime | None = None,
    stale_after_minutes: int = 180,
    is_stale: bool | None = None,
    is_complete: bool | None = None,
) -> EventContextPayload:
    normalized_generated_at = generated_at.replace(tzinfo=None) if generated_at.tzinfo is not None else generated_at
    normalized_source_generated_at = _parse_datetime(source_generated_at)

    events: list[MacroEventPayload] = []
    parsed_incomplete = False
    for raw in raw_events or []:
        event_at = _parse_datetime(raw.get("event_at"))
        event_name = str(raw.get("event_name") or raw.get("name") or "").strip()
        if event_at is None or not event_name:
            parsed_incomplete = True
            continue
        importance = raw.get("importance")
        if importance not in {"low", "medium", "high", None, ""}:
            parsed_incomplete = True
            importance = None
        bias = raw.get("event_bias")
        if bias not in {"bullish", "bearish", "neutral", None, ""}:
            parsed_incomplete = True
            bias = None
        risk_window_before_minutes = _safe_int(raw.get("risk_window_before_minutes"), 60)
        risk_window_after_minutes = _safe_int(raw.get("risk_window_after_minutes"), 30)
        event_enrichment_vendors = _normalize_vendor_list(raw.get("enrichment_vendors"))
        release_enrichment = _normalize_release_enrichment(raw.get("release_enrichment"))
        if release_enrichment:
            for vendor_name in release_enrichment:
                normalized_vendor = _normalize_source_vendor(vendor_name)
                if normalized_vendor is not None and normalized_vendor not in event_enrichment_vendors:
                    event_enrichment_vendors.append(normalized_vendor)
        active_risk_window = _is_active_risk_window(
            generated_at=normalized_generated_at,
            event_at=event_at,
            risk_window_before_minutes=risk_window_before_minutes,
            risk_window_after_minutes=risk_window_after_minutes,
        )
        events.append(
            MacroEventPayload(
                event_at=event_at,
                event_name=event_name,
                importance=importance if importance else None,
                affected_assets=_normalized_assets(raw.get("affected_assets")),
                event_bias=bias if bias else None,
                minutes_to_event=_minutes_until(generated_at=normalized_generated_at, event_at=event_at),
                risk_window_before_minutes=risk_window_before_minutes,
                risk_window_after_minutes=risk_window_after_minutes,
                active_risk_window=active_risk_window,
                enrichment_vendors=event_enrichment_vendors,
                release_enrichment=release_enrichment,
            )
        )

    events.sort(key=lambda item: item.event_at)
    upcoming = next((item for item in events if item.event_at >= normalized_generated_at), None)
    active_events = [item for item in events if item.active_risk_window]
    summary_event = upcoming if upcoming is not None else active_events[0] if active_events else None

    computed_stale = False
    if normalized_source_generated_at is not None:
        computed_stale = (
            normalized_generated_at - normalized_source_generated_at
        ).total_seconds() > stale_after_minutes * 60
    stale = bool(is_stale) if is_stale is not None else computed_stale

    computed_complete = not parsed_incomplete
    complete = bool(is_complete) if is_complete is not None else computed_complete

    final_status = _coerce_source_status(source_status)
    if final_status not in {"unavailable", "error", "stale", "incomplete"}:
        if stale:
            final_status = "stale"
        elif not complete or parsed_incomplete:
            final_status = "incomplete"

    summary_assets = (
        [asset for event in active_events for asset in event.affected_assets]
        if active_events
        else list(upcoming.affected_assets)
        if upcoming is not None
        else []
    )
    deduped_assets: list[str] = []
    for asset in summary_assets:
        if asset not in deduped_assets:
            deduped_assets.append(asset)

    summary_bias: EventBias | None = None
    if summary_event is not None:
        summary_bias = summary_event.event_bias

    summary_enrichment_vendors = _normalize_vendor_list(enrichment_vendors)
    for event in events:
        for vendor_name in event.enrichment_vendors:
            if vendor_name not in summary_enrichment_vendors:
                summary_enrichment_vendors.append(vendor_name)

    final_complete = complete and final_status not in {"unavailable", "error", "incomplete"}
    final_stale = stale or final_status == "stale"

    return EventContextPayload(
        source_status=final_status,
        source_provenance=_normalize_source_provenance(source_provenance, fallback_status=source_status),
        source_vendor=_normalize_source_vendor(source_vendor),
        generated_at=normalized_generated_at,
        is_stale=final_stale,
        is_complete=final_complete,
        next_event_at=summary_event.event_at if summary_event is not None else None,
        next_event_name=summary_event.event_name if summary_event is not None else None,
        next_event_importance=summary_event.importance if summary_event is not None else None,
        minutes_to_next_event=summary_event.minutes_to_event if summary_event is not None else None,
        active_risk_window=bool(active_events),
        affected_assets=deduped_assets,
        event_bias=summary_bias,
        enrichment_vendors=summary_enrichment_vendors,
        events=events,
    )


class EventContextProvider(Protocol):
    def get_event_context(
        self,
        *,
        symbol: str,
        timeframe: str,
        generated_at: datetime,
    ) -> EventContextPayload: ...


class OperatorEventContextProvider(Protocol):
    def get_event_context(
        self,
        *,
        symbol: str,
        timeframe: str,
        generated_at: datetime,
    ) -> OperatorEventContextPayload: ...


class EventSourceSettingsOverride(Protocol):
    event_source_provider: str | None
    event_source_api_url: str | None
    event_source_timeout_seconds: float | None
    event_source_default_assets: Sequence[str]
    event_source_fred_release_ids: Sequence[int]
    event_source_bls_enrichment_url: str | None
    event_source_bls_enrichment_static_params: Mapping[str, str]
    event_source_bea_enrichment_url: str | None
    event_source_bea_enrichment_static_params: Mapping[str, str]


class EventSourceRuntimeCredentials(Protocol):
    event_source_api_key: str


@dataclass(slots=True)
class StubEventContextProvider:
    source_status: EventSourceStatus = "unavailable"

    def get_event_context(
        self,
        *,
        symbol: str,
        timeframe: str,
        generated_at: datetime,
    ) -> EventContextPayload:
        return _build_event_context_payload(
            raw_events=[],
            generated_at=generated_at,
            source_status=self.source_status,
            source_provenance="stub",
            source_vendor=None,
            is_stale=False,
            is_complete=False,
        )


@dataclass(slots=True)
class ErrorEventContextProvider:
    source_provenance: EventSourceProvenance | None = "external_api"
    source_vendor: EventSourceVendor | None = None

    def get_event_context(
        self,
        *,
        symbol: str,
        timeframe: str,
        generated_at: datetime,
    ) -> EventContextPayload:
        return _build_event_context_payload(
            raw_events=[],
            generated_at=generated_at,
            source_status="error",
            source_provenance=self.source_provenance,
            source_vendor=self.source_vendor,
            is_stale=False,
            is_complete=False,
        )


@dataclass(slots=True)
class FixtureEventContextProvider:
    fixtures: Mapping[str, Sequence[Mapping[str, object]]] | None = None
    source_generated_at: datetime | None = None
    stale_after_minutes: int = 180

    def _symbol_events(self, symbol: str) -> Sequence[Mapping[str, object]] | None:
        fixtures = self.fixtures or {}
        symbol_key = str(symbol or "").upper()
        if symbol_key in fixtures:
            return fixtures[symbol_key]
        if "*" in fixtures:
            return fixtures["*"]
        return None

    def get_event_context(
        self,
        *,
        symbol: str,
        timeframe: str,
        generated_at: datetime,
    ) -> EventContextPayload:
        raw_events = self._symbol_events(symbol)
        if raw_events is None:
            return _build_event_context_payload(
                raw_events=[],
                generated_at=generated_at,
                source_status="unavailable",
                source_provenance="fixture",
                source_vendor=None,
                is_stale=False,
                is_complete=False,
            )
        return _build_event_context_payload(
            raw_events=raw_events,
            generated_at=generated_at,
            source_status="fixture",
            source_provenance="fixture",
            source_vendor=None,
            source_generated_at=self.source_generated_at,
            stale_after_minutes=self.stale_after_minutes,
        )

@dataclass(slots=True)
class NormalizedHTTPEventAdapter:
    # Vendor-specific feeds should be translated into this normalized contract before core ingestion.
    url: str
    api_key: str | None = None
    api_key_header: str | None = None
    api_key_query_param: str | None = None
    timeout_seconds: float = 10.0
    static_params: Mapping[str, str] = field(default_factory=dict)
    default_assets: tuple[str, ...] = ()
    fetcher: ExternalEventFetcher | None = None

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.api_key and self.api_key_header:
            headers[self.api_key_header] = self.api_key
        return headers

    def _params(
        self,
        *,
        symbol: str,
        timeframe: str,
        generated_at: datetime,
    ) -> dict[str, str]:
        params = {str(key): str(value) for key, value in self.static_params.items()}
        params["symbol"] = symbol.upper()
        params["timeframe"] = timeframe
        params["generated_at"] = generated_at.isoformat()
        if self.api_key and self.api_key_query_param:
            params[self.api_key_query_param] = self.api_key
        return params

    def _fetch_payload(
        self,
        *,
        symbol: str,
        timeframe: str,
        generated_at: datetime,
    ) -> Mapping[str, object]:
        params = self._params(symbol=symbol, timeframe=timeframe, generated_at=generated_at)
        headers = self._headers()
        if self.fetcher is not None:
            payload = self.fetcher(url=self.url, params=params, headers=headers)
            return payload if isinstance(payload, Mapping) else {}
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(self.url, params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()
        return payload if isinstance(payload, Mapping) else {}

    def fetch_event_context(
        self,
        *,
        symbol: str,
        timeframe: str,
        generated_at: datetime,
    ) -> ExternalEventFetchPayload:
        try:
            payload = self._fetch_payload(
                symbol=symbol,
                timeframe=timeframe,
                generated_at=generated_at,
            )
        except Exception:
            return ExternalEventFetchPayload(source_status="error", is_complete=False)

        raw_events = payload.get("events") or payload.get("items")
        events: list[Mapping[str, object]] = []
        default_assets = self.default_assets or (symbol.upper(),)
        if isinstance(raw_events, Sequence) and not isinstance(raw_events, (str, bytes)):
            for item in raw_events:
                if not isinstance(item, Mapping):
                    continue
                event_payload = dict(item)
                if not event_payload.get("affected_assets"):
                    event_payload["affected_assets"] = list(default_assets)
                events.append(event_payload)

        raw_status = payload.get("source_status") or payload.get("status") or "external_api"
        status = _coerce_source_status(raw_status)
        return ExternalEventFetchPayload(
            source_status=status,
            source_vendor=_normalize_source_vendor(payload.get("source_vendor") or payload.get("provider")),
            enrichment_vendors=tuple(
                _normalize_vendor_list(payload.get("enrichment_vendors"))
            ),
            events=tuple(events),
            source_generated_at=_parse_datetime(payload.get("source_generated_at") or payload.get("generated_at")),
            is_stale=payload.get("is_stale") if isinstance(payload.get("is_stale"), bool) else None,
            is_complete=payload.get("is_complete") if isinstance(payload.get("is_complete"), bool) else None,
        )


@dataclass(slots=True)
class ExternalAPIEventContextProvider:
    adapter: ExternalMacroEventAdapter
    stale_after_minutes: int = 180

    def get_event_context(
        self,
        *,
        symbol: str,
        timeframe: str,
        generated_at: datetime,
    ) -> EventContextPayload:
        result = self.adapter.fetch_event_context(
            symbol=symbol,
            timeframe=timeframe,
            generated_at=generated_at,
        )
        return _build_event_context_payload(
            raw_events=result.events,
            generated_at=generated_at,
            source_status=result.source_status,
            source_provenance="external_api",
            source_vendor=result.source_vendor,
            enrichment_vendors=result.enrichment_vendors,
            source_generated_at=result.source_generated_at,
            stale_after_minutes=self.stale_after_minutes,
            is_stale=result.is_stale,
            is_complete=result.is_complete,
        )


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


def _env_pairs(name: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for item in _env_csv(name):
        key, separator, value = item.partition("=")
        if separator and key.strip():
            pairs[key.strip()] = value.strip()
    return pairs


def _env_int_csv(name: str) -> tuple[int, ...]:
    values: list[int] = []
    for item in _env_csv(name):
        try:
            parsed = int(item)
        except ValueError:
            continue
        if parsed not in values:
            values.append(parsed)
    return tuple(values)


def _coerce_timeout_seconds(value: object, *, default: float = 10.0) -> float:
    try:
        timeout_seconds = float(value) if value is not None else default
    except (TypeError, ValueError):
        timeout_seconds = default
    return max(timeout_seconds, 1.0)


def _settings_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _settings_provider_name(
    settings_row: EventSourceSettingsOverride | None,
) -> str | None:
    if settings_row is None:
        return None
    provider_name = str(getattr(settings_row, "event_source_provider", "") or "").strip().lower()
    if provider_name in {"stub", "fred"}:
        return provider_name
    return None


def _settings_default_assets(
    settings_row: EventSourceSettingsOverride | None,
) -> tuple[str, ...]:
    if settings_row is None:
        return ()
    return tuple(_normalized_assets(getattr(settings_row, "event_source_default_assets", None)))


def _settings_release_ids(
    settings_row: EventSourceSettingsOverride | None,
) -> tuple[int, ...]:
    if settings_row is None:
        return ()
    values = getattr(settings_row, "event_source_fred_release_ids", None)
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
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


def _settings_timeout_seconds(
    settings_row: EventSourceSettingsOverride | None,
) -> float | None:
    if settings_row is None:
        return None
    value = getattr(settings_row, "event_source_timeout_seconds", None)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _settings_api_key(credentials: EventSourceRuntimeCredentials | None) -> str | None:
    if credentials is None:
        return None
    return _settings_text(getattr(credentials, "event_source_api_key", None))


def _settings_static_params(
    settings_row: EventSourceSettingsOverride | None,
    attribute_name: str,
) -> dict[str, str]:
    if settings_row is None:
        return {}
    values = getattr(settings_row, attribute_name, None)
    if not isinstance(values, Mapping):
        return {}
    normalized: dict[str, str] = {}
    for raw_key, raw_value in values.items():
        key = str(raw_key or "").strip()
        value = str(raw_value or "").strip()
        if key and value:
            normalized[key] = value
    return normalized


def _build_post_release_enrichers(
    *,
    settings_row: EventSourceSettingsOverride | None,
    timeout_seconds: float,
) -> tuple[PostReleaseEventEnrichmentAdapter, ...]:
    enrichers: list[PostReleaseEventEnrichmentAdapter] = []

    bls_url = (
        _settings_text(getattr(settings_row, "event_source_bls_enrichment_url", None))
        if settings_row is not None
        else None
    ) or _env_text("TRADING_EVENT_SOURCE_BLS_ENRICHMENT_URL")
    if bls_url is not None:
        enrichers.append(
            BLSActualReleaseEnrichmentAdapter(
                base_url=bls_url,
                timeout_seconds=timeout_seconds,
                static_params=(
                    _settings_static_params(settings_row, "event_source_bls_enrichment_static_params")
                    or _env_pairs("TRADING_EVENT_SOURCE_BLS_ENRICHMENT_STATIC_PARAMS")
                ),
            )
        )

    bea_url = (
        _settings_text(getattr(settings_row, "event_source_bea_enrichment_url", None))
        if settings_row is not None
        else None
    ) or _env_text("TRADING_EVENT_SOURCE_BEA_ENRICHMENT_URL")
    if bea_url is not None:
        enrichers.append(
            BEAActualReleaseEnrichmentAdapter(
                base_url=bea_url,
                timeout_seconds=timeout_seconds,
                static_params=(
                    _settings_static_params(settings_row, "event_source_bea_enrichment_static_params")
                    or _env_pairs("TRADING_EVENT_SOURCE_BEA_ENRICHMENT_STATIC_PARAMS")
                ),
            )
        )

    return tuple(enrichers)


def _build_external_provider(
    provider_name: str,
    *,
    settings_row: EventSourceSettingsOverride | None = None,
    api_key: str | None,
    api_url: str | None,
    timeout_seconds: float,
    default_assets: tuple[str, ...],
    release_ids: tuple[int, ...],
) -> EventContextProvider:
    if provider_name == "fred":
        if api_key is None:
            return ErrorEventContextProvider(source_provenance="external_api", source_vendor="fred")
        return ExternalAPIEventContextProvider(
            adapter=FredReleaseDatesAdapter(
                api_key=api_key,
                base_url=api_url or "https://api.stlouisfed.org/fred",
                timeout_seconds=timeout_seconds,
                default_assets=default_assets,
                release_ids=release_ids,
                post_release_enrichers=_build_post_release_enrichers(
                    settings_row=settings_row,
                    timeout_seconds=timeout_seconds,
                ),
            )
        )

    if provider_name not in {"external_api", "normalized_json"}:
        return ErrorEventContextProvider(source_provenance="external_api")

    if api_url is None:
        return ErrorEventContextProvider(source_provenance="external_api")

    return ExternalAPIEventContextProvider(
        adapter=NormalizedHTTPEventAdapter(
            url=api_url,
            api_key=api_key,
            api_key_header=_env_text("TRADING_EVENT_SOURCE_API_KEY_HEADER"),
            api_key_query_param=_env_text("TRADING_EVENT_SOURCE_API_KEY_QUERY_PARAM"),
            timeout_seconds=timeout_seconds,
            static_params=_env_pairs("TRADING_EVENT_SOURCE_STATIC_PARAMS"),
            default_assets=default_assets,
        )
    )


def resolve_event_context_provider_from_env() -> EventContextProvider:
    provider_name = str(_env_text("TRADING_EVENT_SOURCE_PROVIDER") or "").lower()
    if not provider_name or provider_name in {"stub", "none", "disabled"}:
        return StubEventContextProvider()

    return _build_external_provider(
        provider_name,
        settings_row=None,
        api_key=_env_text("TRADING_EVENT_SOURCE_API_KEY"),
        api_url=_env_text("TRADING_EVENT_SOURCE_API_URL"),
        timeout_seconds=_coerce_timeout_seconds(_env_text("TRADING_EVENT_SOURCE_TIMEOUT_SECONDS")),
        default_assets=_env_csv("TRADING_EVENT_SOURCE_DEFAULT_ASSETS"),
        release_ids=_env_int_csv("TRADING_EVENT_SOURCE_FRED_RELEASE_IDS"),
    )


def resolve_event_context_provider(
    *,
    settings_row: EventSourceSettingsOverride | None = None,
    credentials: EventSourceRuntimeCredentials | None = None,
) -> EventContextProvider:
    provider_name = _settings_provider_name(settings_row)
    if provider_name is None:
        return resolve_event_context_provider_from_env()
    if provider_name == "stub":
        return StubEventContextProvider()
    configured_timeout_seconds = _settings_timeout_seconds(settings_row)
    return _build_external_provider(
        provider_name,
        settings_row=settings_row,
        api_key=_settings_api_key(credentials) or _env_text("TRADING_EVENT_SOURCE_API_KEY"),
        api_url=_settings_text(getattr(settings_row, "event_source_api_url", None))
        or _env_text("TRADING_EVENT_SOURCE_API_URL"),
        timeout_seconds=_coerce_timeout_seconds(
            configured_timeout_seconds
            if configured_timeout_seconds is not None
            else _env_text("TRADING_EVENT_SOURCE_TIMEOUT_SECONDS")
        ),
        default_assets=_settings_default_assets(settings_row) or _env_csv("TRADING_EVENT_SOURCE_DEFAULT_ASSETS"),
        release_ids=_settings_release_ids(settings_row) or _env_int_csv("TRADING_EVENT_SOURCE_FRED_RELEASE_IDS"),
    )


def build_event_context(
    *,
    symbol: str,
    timeframe: str,
    generated_at: datetime,
    provider: EventContextProvider | None = None,
) -> EventContextPayload:
    resolved_provider = provider or StubEventContextProvider()
    return resolved_provider.get_event_context(
        symbol=symbol,
        timeframe=timeframe,
        generated_at=generated_at,
    )


def _normalize_operator_source_status(value: object) -> OperatorEventSourceStatus:
    text = str(value or "").strip().lower()
    if text in {"fixture", "stub", "external_api", "available"}:
        return "available"
    if text in {"stale", "incomplete", "unavailable", "error"}:
        return text  # type: ignore[return-value]
    return "error"


def _normalize_operator_importance(value: object) -> OperatorEventImportance:
    text = str(value or "").strip().lower()
    if text in {"low", "medium", "high", "critical"}:
        return text  # type: ignore[return-value]
    return "unknown"


def _normalize_operator_event_items(
    raw_events: object,
    *,
    generated_at: datetime,
) -> list[OperatorEventItemPayload]:
    if not isinstance(raw_events, Sequence) or isinstance(raw_events, (str, bytes)):
        return []
    items: list[OperatorEventItemPayload] = []
    for raw in raw_events:
        payload = dict(raw) if isinstance(raw, Mapping) else {}
        event_at = _parse_aware_datetime(payload.get("event_at"))
        event_name = str(payload.get("event_name") or "").strip()
        if event_at is None or not event_name:
            continue
        minutes_to_event = payload.get("minutes_to_event")
        if not isinstance(minutes_to_event, int):
            minutes_to_event = _minutes_until(generated_at=generated_at, event_at=event_at)
        items.append(
            OperatorEventItemPayload(
                event_at=event_at,
                event_name=event_name,
                importance=_normalize_operator_importance(payload.get("importance")),
                affected_assets=_normalized_assets(payload.get("affected_assets")),
                minutes_to_event=minutes_to_event,
            )
        )
    items.sort(key=lambda item: item.event_at)
    return items


def normalize_operator_event_context(
    raw_context: Mapping[str, object] | EventContextPayload | None,
    *,
    generated_at: datetime | None = None,
    summary_note: str | None = None,
) -> OperatorEventContextPayload:
    source = (
        raw_context.model_dump(mode="json")
        if isinstance(raw_context, EventContextPayload)
        else dict(raw_context or {})
    )
    generated_at_value = (
        _parse_aware_datetime(source.get("generated_at"))
        or ensure_utc_aware(generated_at)
        or utcnow_aware()
    )
    resolved_summary_note = summary_note or (str(source.get("summary_note") or "").strip() or None)
    if not source:
        return OperatorEventContextPayload(
            source_status="unavailable",
            source_provenance=None,
            source_vendor=None,
            generated_at=generated_at_value,
            is_stale=False,
            is_complete=False,
            active_risk_window=False,
            active_risk_window_detail=OperatorActiveRiskWindowPayload(
                is_active=False,
                summary_note=resolved_summary_note or "event context provider unavailable",
            ),
            next_event_at=None,
            next_event_name=None,
            next_event_importance="unknown",
            minutes_to_next_event=None,
            upcoming_events=[],
            affected_assets=[],
            enrichment_vendors=[],
            summary_note=resolved_summary_note or "event context provider unavailable",
        )

    upcoming_events = _normalize_operator_event_items(
        source.get("events") or source.get("upcoming_events"),
        generated_at=generated_at_value,
    )
    next_event_at = _parse_aware_datetime(source.get("next_event_at"))
    next_event_name = str(source.get("next_event_name") or "").strip() or None
    next_event_importance = _normalize_operator_importance(source.get("next_event_importance"))
    minutes_to_next_event = source.get("minutes_to_next_event")
    if not isinstance(minutes_to_next_event, int):
        minutes_to_next_event = (
            _minutes_until(generated_at=generated_at_value, event_at=next_event_at)
            if next_event_at is not None
            else None
        )
    if not upcoming_events and next_event_at is not None and next_event_name is not None:
        upcoming_events = [
            OperatorEventItemPayload(
                event_at=next_event_at,
                event_name=next_event_name,
                importance=next_event_importance,
                affected_assets=_normalized_assets(source.get("affected_assets")),
                minutes_to_event=minutes_to_next_event,
            )
        ]

    active_event = next(
        (
            item
            for item in upcoming_events
            if item.minutes_to_event is not None and item.minutes_to_event <= 0
        ),
        None,
    )
    active_risk_window = bool(source.get("active_risk_window"))
    if not active_risk_window and active_event is not None:
        active_risk_window = True

    active_window_detail = OperatorActiveRiskWindowPayload(
        is_active=active_risk_window,
        event_name=active_event.event_name if active_event is not None else next_event_name,
        event_importance=active_event.importance if active_event is not None else next_event_importance,
        start_at=None,
        end_at=None,
        affected_assets=(
            list(active_event.affected_assets)
            if active_event is not None
            else _normalized_assets(source.get("affected_assets"))
        ),
        summary_note=resolved_summary_note,
    )

    return OperatorEventContextPayload(
        source_status=_normalize_operator_source_status(source.get("source_status")),
        source_provenance=_normalize_source_provenance(
            source.get("source_provenance"),
            fallback_status=source.get("source_status"),
        ),
        source_vendor=_normalize_source_vendor(source.get("source_vendor")),
        generated_at=generated_at_value,
        is_stale=bool(source.get("is_stale")),
        is_complete=bool(source.get("is_complete")),
        active_risk_window=active_risk_window,
        active_risk_window_detail=active_window_detail,
        next_event_at=next_event_at,
        next_event_name=next_event_name,
        next_event_importance=next_event_importance,
        minutes_to_next_event=minutes_to_next_event,
        upcoming_events=upcoming_events,
        affected_assets=_normalized_assets(source.get("affected_assets")),
        enrichment_vendors=_normalize_vendor_list(source.get("enrichment_vendors")),
        summary_note=resolved_summary_note,
    )


@dataclass(slots=True)
class StubOperatorEventContextProvider:
    source_status: OperatorEventSourceStatus = "unavailable"
    summary_note: str | None = "No operator event source configured."

    def get_event_context(
        self,
        *,
        symbol: str,
        timeframe: str,
        generated_at: datetime,
    ) -> OperatorEventContextPayload:
        return normalize_operator_event_context(
            {},
            generated_at=ensure_utc_aware(generated_at),
            summary_note=self.summary_note,
        ).model_copy(update={"source_status": self.source_status, "source_provenance": "stub"})


@dataclass(slots=True)
class FixtureOperatorEventContextProvider:
    fixtures: Mapping[str, Sequence[Mapping[str, object]]] | None = None
    source_generated_at: datetime | None = None
    stale_after_minutes: int = 180
    summary_note: str | None = "fixture-backed operator event context"

    def get_event_context(
        self,
        *,
        symbol: str,
        timeframe: str,
        generated_at: datetime,
    ) -> OperatorEventContextPayload:
        payload = FixtureEventContextProvider(
            fixtures=self.fixtures,
            source_generated_at=self.source_generated_at,
            stale_after_minutes=self.stale_after_minutes,
        ).get_event_context(
            symbol=symbol,
            timeframe=timeframe,
            generated_at=generated_at.replace(tzinfo=None) if generated_at.tzinfo is not None else generated_at,
        )
        return normalize_operator_event_context(
            payload,
            generated_at=ensure_utc_aware(generated_at),
            summary_note=self.summary_note,
        )
