from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from trading_mvp.schemas import EventSourceVendor
from trading_mvp.time_utils import ensure_utc_aware

ExternalEventFetcher = Callable[..., Mapping[str, object]]
ExternalReleaseEnrichmentFetcher = Callable[..., Mapping[str, object]]


@dataclass(slots=True)
class ExternalEventFetchPayload:
    source_status: str = "external_api"
    source_vendor: EventSourceVendor | None = None
    enrichment_vendors: tuple[EventSourceVendor, ...] = ()
    events: tuple[Mapping[str, object], ...] = ()
    source_generated_at: datetime | None = None
    is_stale: bool | None = None
    is_complete: bool | None = None


class ExternalMacroEventAdapter(Protocol):
    def fetch_event_context(
        self,
        *,
        symbol: str,
        timeframe: str,
        generated_at: datetime,
    ) -> ExternalEventFetchPayload: ...


class PostReleaseEventEnrichmentAdapter(Protocol):
    vendor_name: EventSourceVendor

    def enrich_events(
        self,
        *,
        symbol: str,
        timeframe: str,
        generated_at: datetime,
        events: Sequence[Mapping[str, object]],
    ) -> tuple[Mapping[str, object], ...]: ...


def _normalized_vendor_names(values: object) -> tuple[EventSourceVendor, ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return ()
    normalized: list[EventSourceVendor] = []
    for item in values:
        text = str(item or "").strip().lower()
        if text in {"fred", "bls", "bea"} and text not in normalized:
            normalized.append(text)  # type: ignore[arg-type]
    return tuple(normalized)


def _serialize_release_enrichment_value(value: object) -> object:
    if isinstance(value, datetime):
        return ensure_utc_aware(value).astimezone(UTC).isoformat().replace("+00:00", "Z")
    return value


def _coerce_numeric_value(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip().replace(",", "")
    if not text or text in {"--", "(NA)", "NA", "N/A"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _normalize_bls_reference_period(*, year: object, period: object) -> str | None:
    year_text = str(year or "").strip()
    period_text = str(period or "").strip().upper()
    if not year_text:
        return None
    if period_text.startswith("M") and len(period_text) == 3:
        try:
            month = int(period_text[1:])
        except ValueError:
            return None
        if 1 <= month <= 12:
            return f"{year_text}-{month:02d}"
        if month == 13:
            return year_text
    if period_text.startswith("Q"):
        quarter_text = period_text[1:].lstrip("0")
        if quarter_text in {"1", "2", "3", "4"}:
            return f"{year_text}-Q{quarter_text}"
    if period_text.startswith("A"):
        return year_text
    return None


def _normalize_bea_reference_period(value: object) -> str | None:
    text = str(value or "").strip().upper()
    if not text:
        return None
    if len(text) == 6 and text[:4].isdigit() and text[4] == "Q" and text[5] in {"1", "2", "3", "4"}:
        return f"{text[:4]}-Q{text[5]}"
    if len(text) == 7 and text[:4].isdigit() and text[4] == "M" and text[5:].isdigit():
        month = int(text[5:])
        if 1 <= month <= 12:
            return f"{text[:4]}-{month:02d}"
    if len(text) == 4 and text.isdigit():
        return text
    return None


def _normalize_bls_footnotes(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        text = str(item.get("text") or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _normalize_release_enrichment_payload(payload: Mapping[str, object]) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for raw_key, raw_value in payload.items():
        key = str(raw_key or "").strip()
        if not key or raw_value is None:
            continue
        if isinstance(raw_value, str) and not raw_value.strip():
            continue
        if isinstance(raw_value, (Mapping, Sequence)) and not isinstance(raw_value, (str, bytes)) and len(raw_value) == 0:
            continue
        normalized[key] = _serialize_release_enrichment_value(raw_value)
    return normalized


def _merge_release_enrichment(
    event: Mapping[str, object],
    *,
    vendor_name: EventSourceVendor,
    enrichment_payload: Mapping[str, object],
) -> Mapping[str, object]:
    merged_event = dict(event)
    current_release_enrichment = merged_event.get("release_enrichment")
    release_enrichment: dict[str, dict[str, object]] = {}
    if isinstance(current_release_enrichment, Mapping):
        for key, value in current_release_enrichment.items():
            if isinstance(value, Mapping):
                release_enrichment[str(key)] = dict(value)
    release_enrichment[vendor_name] = dict(enrichment_payload)
    merged_event["release_enrichment"] = release_enrichment
    enrichment_vendors = list(_normalized_vendor_names(merged_event.get("enrichment_vendors")))
    if vendor_name not in enrichment_vendors:
        enrichment_vendors.append(vendor_name)
    merged_event["enrichment_vendors"] = enrichment_vendors
    return merged_event


@dataclass(slots=True)
class NormalizedReleaseEnrichmentAdapter:
    vendor_name: EventSourceVendor
    base_url: str | None = None
    timeout_seconds: float = 10.0
    static_params: Mapping[str, str] = field(default_factory=dict)
    event_name_aliases: Mapping[str, str] = field(default_factory=dict)
    fetcher: ExternalReleaseEnrichmentFetcher | None = None

    def _headers(self) -> dict[str, str]:
        return {"Accept": "application/json"}

    def _params(
        self,
        *,
        symbol: str,
        timeframe: str,
        event_name: str,
        event_at: datetime,
    ) -> dict[str, str]:
        params = {str(key): str(value) for key, value in self.static_params.items()}
        params["symbol"] = symbol.upper()
        params["timeframe"] = timeframe
        params["event_name"] = event_name
        params["event_key"] = str(self.event_name_aliases.get(event_name, event_name))
        params["event_at"] = ensure_utc_aware(event_at).astimezone(UTC).isoformat().replace("+00:00", "Z")
        return params

    def _fetch_payload(
        self,
        *,
        symbol: str,
        timeframe: str,
        event_name: str,
        event_at: datetime,
    ) -> Mapping[str, object]:
        if self.fetcher is not None:
            payload = self.fetcher(
                url=self.base_url or "",
                params=self._params(
                    symbol=symbol,
                    timeframe=timeframe,
                    event_name=event_name,
                    event_at=event_at,
                ),
                headers=self._headers(),
            )
            return payload if isinstance(payload, Mapping) else {}
        if not self.base_url:
            return {}
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                self.base_url,
                params=self._params(
                    symbol=symbol,
                    timeframe=timeframe,
                    event_name=event_name,
                    event_at=event_at,
                ),
                headers=self._headers(),
            )
            response.raise_for_status()
            payload = response.json()
        return payload if isinstance(payload, Mapping) else {}

    def _parse_native_payload(
        self,
        payload: Mapping[str, object],
        *,
        event_name: str,
        event_at: datetime,
    ) -> tuple[dict[str, object], bool]:
        del payload, event_name, event_at
        return {}, False

    def _parse_enrichment_payload(
        self,
        payload: Mapping[str, object],
        *,
        event_name: str,
        event_at: datetime,
    ) -> dict[str, object]:
        native_payload, recognized = self._parse_native_payload(
            payload,
            event_name=event_name,
            event_at=event_at,
        )
        if native_payload:
            return native_payload
        if recognized:
            return {}
        return _normalize_release_enrichment_payload(payload)

    def enrich_events(
        self,
        *,
        symbol: str,
        timeframe: str,
        generated_at: datetime,
        events: Sequence[Mapping[str, object]],
    ) -> tuple[Mapping[str, object], ...]:
        normalized_generated_at = ensure_utc_aware(generated_at).astimezone(UTC).replace(tzinfo=None)
        enriched_events: list[Mapping[str, object]] = []
        for event in events:
            event_at = event.get("event_at")
            event_name = str(event.get("event_name") or "").strip()
            if not isinstance(event_at, datetime) or not event_name or event_at > normalized_generated_at:
                enriched_events.append(dict(event))
                continue
            try:
                payload = self._fetch_payload(
                    symbol=symbol,
                    timeframe=timeframe,
                    event_name=event_name,
                    event_at=event_at,
                )
            except Exception:
                enriched_events.append(dict(event))
                continue
            normalized_payload = self._parse_enrichment_payload(
                payload,
                event_name=event_name,
                event_at=event_at,
            )
            if not normalized_payload:
                enriched_events.append(dict(event))
                continue
            enriched_events.append(
                _merge_release_enrichment(
                    event,
                    vendor_name=self.vendor_name,
                    enrichment_payload=normalized_payload,
                )
            )
        return tuple(enriched_events)


@dataclass(slots=True)
class BLSActualReleaseEnrichmentAdapter(NormalizedReleaseEnrichmentAdapter):
    vendor_name: EventSourceVendor = "bls"
    event_name_aliases: Mapping[str, str] = field(
        default_factory=lambda: {
            "Consumer Price Index": "cpi",
            "Producer Price Index": "ppi",
            "Employment Situation": "employment_situation",
        }
    )

    def _parse_native_payload(
        self,
        payload: Mapping[str, object],
        *,
        event_name: str,
        event_at: datetime,
    ) -> tuple[dict[str, object], bool]:
        del event_name, event_at
        status = str(payload.get("status") or "").strip().upper()
        results = payload.get("Results")
        recognized = bool(status) or isinstance(results, (Mapping, Sequence))
        if not recognized:
            return {}, False
        if status and status != "REQUEST_SUCCEEDED":
            return {}, True

        series_items: list[Mapping[str, object]] = []
        if isinstance(results, Mapping):
            raw_series = results.get("series")
            if isinstance(raw_series, Sequence) and not isinstance(raw_series, (str, bytes)):
                series_items.extend(item for item in raw_series if isinstance(item, Mapping))
        elif isinstance(results, Sequence) and not isinstance(results, (str, bytes)):
            for item in results:
                if not isinstance(item, Mapping):
                    continue
                raw_series = item.get("series")
                if isinstance(raw_series, Sequence) and not isinstance(raw_series, (str, bytes)):
                    series_items.extend(series for series in raw_series if isinstance(series, Mapping))

        for series in series_items:
            raw_data = series.get("data")
            if not isinstance(raw_data, Sequence) or isinstance(raw_data, (str, bytes)):
                continue
            for item in raw_data:
                if not isinstance(item, Mapping):
                    continue
                parsed: dict[str, object] = {}
                actual_value = _coerce_numeric_value(item.get("value"))
                if actual_value is not None:
                    parsed["actual"] = actual_value
                reference_period = _normalize_bls_reference_period(
                    year=item.get("year"),
                    period=item.get("period"),
                )
                if reference_period is not None:
                    parsed["reference_period"] = reference_period
                series_id = str(series.get("seriesID") or "").strip()
                if series_id:
                    parsed["series_id"] = series_id
                period = str(item.get("period") or "").strip()
                if period:
                    parsed["period"] = period
                period_name = str(item.get("periodName") or "").strip()
                if period_name:
                    parsed["period_name"] = period_name
                if str(item.get("latest") or "").strip().lower() == "true":
                    parsed["latest"] = True
                footnotes = _normalize_bls_footnotes(item.get("footnotes"))
                if footnotes:
                    parsed["footnotes"] = footnotes
                catalog = series.get("catalog")
                if isinstance(catalog, Mapping):
                    series_title = str(catalog.get("series_title") or "").strip()
                    if series_title:
                        parsed["series_title"] = series_title
                if parsed:
                    return parsed, True
        return {}, True


@dataclass(slots=True)
class BEAActualReleaseEnrichmentAdapter(NormalizedReleaseEnrichmentAdapter):
    vendor_name: EventSourceVendor = "bea"
    event_name_aliases: Mapping[str, str] = field(
        default_factory=lambda: {
            "Gross Domestic Product": "gdp",
            "Personal Consumption Expenditures": "pce",
        }
    )

    def _parse_native_payload(
        self,
        payload: Mapping[str, object],
        *,
        event_name: str,
        event_at: datetime,
    ) -> tuple[dict[str, object], bool]:
        del event_at
        bea_api = payload.get("BEAAPI")
        if not isinstance(bea_api, Mapping):
            return {}, False
        results = bea_api.get("Results")
        if not isinstance(results, Mapping):
            return {}, True
        if isinstance(results.get("Error"), Mapping):
            return {}, True
        raw_data = results.get("Data")
        if not isinstance(raw_data, Sequence) or isinstance(raw_data, (str, bytes)):
            return {}, True

        target_description = str(event_name or "").strip().lower()
        scored_rows: list[tuple[int, Mapping[str, object]]] = []
        for item in raw_data:
            if not isinstance(item, Mapping):
                continue
            score = 0
            description = str(item.get("LineDescription") or item.get("TimeSeriesDescription") or "").strip().lower()
            if description:
                score += 1
                if target_description and target_description in description:
                    score += 4
            if _coerce_numeric_value(item.get("DataValue")) is not None:
                score += 2
            if _normalize_bea_reference_period(item.get("TimePeriod")) is not None:
                score += 1
            scored_rows.append((score, item))
        if not scored_rows:
            return {}, True

        scored_rows.sort(
            key=lambda item: (
                item[0],
                str(item[1].get("TimePeriod") or ""),
            ),
            reverse=True,
        )
        row = scored_rows[0][1]
        parsed: dict[str, object] = {}
        actual_value = _coerce_numeric_value(row.get("DataValue"))
        if actual_value is not None:
            parsed["actual"] = actual_value
        reference_period = _normalize_bea_reference_period(row.get("TimePeriod"))
        if reference_period is not None:
            parsed["reference_period"] = reference_period
        for source_key, target_key in (
            ("TimePeriod", "time_period"),
            ("LineDescription", "line_description"),
            ("TableName", "table_name"),
            ("SeriesCode", "series_code"),
            ("LineNumber", "line_number"),
            ("TimeSeriesId", "time_series_id"),
            ("TimeSeriesDescription", "time_series_description"),
            ("CL_UNIT", "unit"),
            ("UNIT_MULT", "unit_mult"),
            ("NoteRef", "note_ref"),
        ):
            value = str(row.get(source_key) or "").strip()
            if value:
                parsed[target_key] = value
        return parsed, True


@dataclass(frozen=True, slots=True)
class FredReleaseDefinition:
    release_id: int
    event_name: str
    importance: str = "high"
    affected_assets: tuple[str, ...] = ()
    event_bias: str | None = None
    risk_window_before_minutes: int = 60
    risk_window_after_minutes: int = 30
    release_hour: int = 8
    release_minute: int = 30
    release_timezone: str = "America/New_York"


DEFAULT_FRED_RELEASE_CATALOG: dict[int, FredReleaseDefinition] = {
    10: FredReleaseDefinition(release_id=10, event_name="Consumer Price Index", importance="high"),
    46: FredReleaseDefinition(release_id=46, event_name="Producer Price Index", importance="medium"),
    50: FredReleaseDefinition(release_id=50, event_name="Employment Situation", importance="high"),
    53: FredReleaseDefinition(release_id=53, event_name="Gross Domestic Product", importance="high"),
    101: FredReleaseDefinition(
        release_id=101,
        event_name="FOMC Press Release",
        importance="high",
        release_hour=14,
        release_minute=0,
        risk_window_before_minutes=120,
        risk_window_after_minutes=120,
    ),
}

DEFAULT_FRED_RELEASE_IDS: tuple[int, ...] = tuple(DEFAULT_FRED_RELEASE_CATALOG)


def _parse_release_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _combine_release_datetime(
    *,
    release_date: date,
    definition: FredReleaseDefinition,
) -> datetime | None:
    try:
        timezone = ZoneInfo(definition.release_timezone)
    except ZoneInfoNotFoundError:
        return None
    local_dt = datetime.combine(
        release_date,
        time(definition.release_hour, definition.release_minute),
        tzinfo=timezone,
    )
    return local_dt.astimezone(UTC).replace(tzinfo=None)


@dataclass(slots=True)
class FredReleaseDatesAdapter:
    api_key: str
    base_url: str = "https://api.stlouisfed.org/fred"
    timeout_seconds: float = 10.0
    default_assets: tuple[str, ...] = ()
    release_ids: tuple[int, ...] = ()
    post_release_retention_minutes: int = 180
    post_release_enrichers: tuple[PostReleaseEventEnrichmentAdapter, ...] = ()
    release_catalog: Mapping[int, FredReleaseDefinition] = field(
        default_factory=lambda: dict(DEFAULT_FRED_RELEASE_CATALOG)
    )
    fetcher: ExternalEventFetcher | None = None

    def _headers(self) -> dict[str, str]:
        return {"Accept": "application/json"}

    def _release_params(self, release_id: int) -> dict[str, str]:
        return {
            "api_key": self.api_key,
            "file_type": "json",
            "release_id": str(release_id),
            "sort_order": "asc",
            "limit": "10000",
            # Future dates are excluded unless this flag is true.
            "include_release_dates_with_no_data": "true",
        }

    def _fetch_release_payload(self, *, release_id: int) -> Mapping[str, object]:
        url = f"{self.base_url.rstrip('/')}/release/dates"
        params = self._release_params(release_id)
        headers = self._headers()
        if self.fetcher is not None:
            payload = self.fetcher(url=url, params=params, headers=headers)
            return payload if isinstance(payload, Mapping) else {}
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(url, params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()
        return payload if isinstance(payload, Mapping) else {}

    def _resolved_definitions(self) -> tuple[FredReleaseDefinition, ...]:
        release_ids = self.release_ids or DEFAULT_FRED_RELEASE_IDS
        definitions: list[FredReleaseDefinition] = []
        seen: set[int] = set()
        for raw_release_id in release_ids:
            try:
                release_id = int(raw_release_id)
            except (TypeError, ValueError):
                continue
            if release_id in seen:
                continue
            seen.add(release_id)
            definitions.append(
                self.release_catalog.get(
                    release_id,
                    FredReleaseDefinition(
                        release_id=release_id,
                        event_name=f"FRED release {release_id}",
                    ),
                )
            )
        return tuple(definitions)

    def _event_payload(
        self,
        *,
        definition: FredReleaseDefinition,
        event_at: datetime,
        symbol: str,
    ) -> Mapping[str, object]:
        affected_assets = list(definition.affected_assets or self.default_assets or (symbol.upper(),))
        return {
            "event_name": definition.event_name,
            "event_at": event_at,
            "importance": definition.importance,
            "affected_assets": affected_assets,
            "event_bias": definition.event_bias,
            "risk_window_before_minutes": definition.risk_window_before_minutes,
            "risk_window_after_minutes": definition.risk_window_after_minutes,
            "enrichment_vendors": [],
            "release_enrichment": {},
        }

    def _select_event_at(
        self,
        *,
        definition: FredReleaseDefinition,
        raw_release_dates: Sequence[object],
        generated_at: datetime,
    ) -> tuple[datetime | None, bool]:
        parse_incomplete = False
        latest_released_event_at: datetime | None = None
        for item in raw_release_dates:
            if not isinstance(item, Mapping):
                parse_incomplete = True
                continue
            release_date = _parse_release_date(item.get("date"))
            if release_date is None:
                parse_incomplete = True
                continue
            event_at = _combine_release_datetime(release_date=release_date, definition=definition)
            if event_at is None:
                parse_incomplete = True
                continue
            if event_at >= generated_at:
                return event_at, parse_incomplete
            if latest_released_event_at is None or event_at > latest_released_event_at:
                latest_released_event_at = event_at
        if latest_released_event_at is None:
            return None, parse_incomplete
        retention_minutes = max(definition.risk_window_after_minutes, self.post_release_retention_minutes)
        if (generated_at - latest_released_event_at).total_seconds() <= retention_minutes * 60:
            return latest_released_event_at, parse_incomplete
        return None, parse_incomplete

    def _apply_post_release_enrichers(
        self,
        *,
        symbol: str,
        timeframe: str,
        generated_at: datetime,
        events: Sequence[Mapping[str, object]],
    ) -> tuple[Mapping[str, object], ...]:
        enriched_events: tuple[Mapping[str, object], ...] = tuple(dict(event) for event in events)
        for enricher in self.post_release_enrichers:
            try:
                enriched_events = enricher.enrich_events(
                    symbol=symbol,
                    timeframe=timeframe,
                    generated_at=generated_at,
                    events=enriched_events,
                )
            except Exception:
                continue
        return enriched_events

    def _collect_enrichment_vendors(
        self,
        events: Sequence[Mapping[str, object]],
    ) -> tuple[EventSourceVendor, ...]:
        vendors: list[EventSourceVendor] = []
        for event in events:
            for vendor_name in _normalized_vendor_names(event.get("enrichment_vendors")):
                if vendor_name not in vendors:
                    vendors.append(vendor_name)
        return tuple(vendors)

    def fetch_event_context(
        self,
        *,
        symbol: str,
        timeframe: str,
        generated_at: datetime,
    ) -> ExternalEventFetchPayload:
        normalized_generated_at = ensure_utc_aware(generated_at).astimezone(UTC).replace(tzinfo=None)
        events: list[Mapping[str, object]] = []
        fetch_failed = False
        parse_incomplete = False
        saw_no_data = False

        for definition in self._resolved_definitions():
            try:
                payload = self._fetch_release_payload(release_id=definition.release_id)
            except Exception:
                fetch_failed = True
                continue

            if payload.get("error_code") or payload.get("error_message"):
                fetch_failed = True
                continue

            raw_release_dates = payload.get("release_dates")
            if not isinstance(raw_release_dates, Sequence) or isinstance(raw_release_dates, (str, bytes)):
                parse_incomplete = True
                continue

            next_event_at, release_parse_incomplete = self._select_event_at(
                definition=definition,
                raw_release_dates=raw_release_dates,
                generated_at=normalized_generated_at,
            )
            parse_incomplete = parse_incomplete or release_parse_incomplete

            if next_event_at is None:
                saw_no_data = True
                continue

            events.append(
                self._event_payload(
                    definition=definition,
                    event_at=next_event_at,
                    symbol=symbol,
                )
            )

        if events:
            events_with_enrichment = self._apply_post_release_enrichers(
                symbol=symbol,
                timeframe=timeframe,
                generated_at=normalized_generated_at,
                events=events,
            )
            status = "incomplete" if fetch_failed or parse_incomplete else "external_api"
            return ExternalEventFetchPayload(
                source_status=status,
                source_vendor="fred",
                enrichment_vendors=self._collect_enrichment_vendors(events_with_enrichment),
                events=events_with_enrichment,
                source_generated_at=normalized_generated_at,
                is_stale=False,
                is_complete=status == "external_api",
            )

        if fetch_failed:
            return ExternalEventFetchPayload(
                source_status="error",
                source_vendor="fred",
                source_generated_at=normalized_generated_at,
                is_stale=False,
                is_complete=False,
            )

        if parse_incomplete:
            return ExternalEventFetchPayload(
                source_status="incomplete",
                source_vendor="fred",
                source_generated_at=normalized_generated_at,
                is_stale=False,
                is_complete=False,
            )

        if saw_no_data:
            return ExternalEventFetchPayload(
                source_status="unavailable",
                source_vendor="fred",
                source_generated_at=normalized_generated_at,
                is_stale=False,
                is_complete=False,
            )

        return ExternalEventFetchPayload(
            source_status="unavailable",
            source_vendor="fred",
            source_generated_at=normalized_generated_at,
            is_stale=False,
            is_complete=False,
        )
