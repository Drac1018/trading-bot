from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from trading_mvp.schemas import EventContextPayload, MacroEventPayload
from trading_mvp.services.event_context import (
    ExternalAPIEventContextProvider,
    ExternalEventFetchPayload,
    FixtureEventContextProvider,
    StubEventContextProvider,
    build_event_context,
    resolve_event_context_provider,
    resolve_event_context_provider_from_env,
)
from trading_mvp.services.event_context_adapters import FredReleaseDatesAdapter
from trading_mvp.services.event_context_adapters import (
    BEAActualReleaseEnrichmentAdapter,
    BLSActualReleaseEnrichmentAdapter,
)
from trading_mvp.services.features import compute_features
from trading_mvp.services.market_data import build_market_context, build_market_snapshot


def test_event_context_payload_serialization() -> None:
    generated_at = datetime(2026, 4, 20, 9, 0, 0)
    payload = EventContextPayload(
        source_status="fixture",
        source_provenance="fixture",
        generated_at=generated_at,
        is_stale=False,
        is_complete=True,
        next_event_at=generated_at + timedelta(minutes=45),
        next_event_name="US CPI",
        next_event_importance="high",
        minutes_to_next_event=45,
        active_risk_window=False,
        affected_assets=["BTCUSDT", "ETHUSDT"],
        event_bias="neutral",
        events=[
            MacroEventPayload(
                event_at=generated_at + timedelta(minutes=45),
                event_name="US CPI",
                importance="high",
                affected_assets=["BTCUSDT", "ETHUSDT"],
                event_bias="neutral",
                minutes_to_event=45,
                risk_window_before_minutes=60,
                risk_window_after_minutes=30,
                active_risk_window=False,
            )
        ],
    )

    dumped = payload.model_dump(mode="json")

    assert dumped["source_status"] == "fixture"
    assert dumped["source_provenance"] == "fixture"
    assert dumped["next_event_name"] == "US CPI"
    assert dumped["minutes_to_next_event"] == 45
    assert dumped["events"][0]["event_name"] == "US CPI"


def test_event_context_provider_unavailable_does_not_crash_snapshot() -> None:
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)

    assert snapshot.event_context.source_status == "unavailable"
    assert snapshot.event_context.source_provenance == "stub"
    assert snapshot.event_context.is_complete is False
    assert snapshot.event_context.events == []


def test_event_context_minutes_and_active_risk_window_are_calculated() -> None:
    generated_at = datetime(2026, 4, 20, 12, 0, 0)
    provider = FixtureEventContextProvider(
        fixtures={
            "BTCUSDT": [
                {
                    "event_name": "US CPI",
                    "event_at": generated_at + timedelta(minutes=25),
                    "importance": "high",
                    "affected_assets": ["BTCUSDT", "ETHUSDT"],
                    "event_bias": "neutral",
                    "risk_window_before_minutes": 30,
                    "risk_window_after_minutes": 45,
                }
            ]
        }
    )

    payload = build_event_context(
        symbol="BTCUSDT",
        timeframe="15m",
        generated_at=generated_at,
        provider=provider,
    )

    assert payload.source_status == "fixture"
    assert payload.source_provenance == "fixture"
    assert payload.minutes_to_next_event == 25
    assert payload.active_risk_window is True
    assert payload.next_event_name == "US CPI"
    assert payload.events[0].active_risk_window is True


def test_event_context_provider_marks_stale_and_incomplete_explicitly() -> None:
    generated_at = datetime(2026, 4, 20, 12, 0, 0)
    stale_provider = FixtureEventContextProvider(
        fixtures={
            "BTCUSDT": [
                {
                    "event_name": "FOMC",
                    "event_at": generated_at + timedelta(hours=2),
                    "importance": "high",
                }
            ]
        },
        source_generated_at=generated_at - timedelta(hours=4),
        stale_after_minutes=60,
    )
    incomplete_provider = FixtureEventContextProvider(
        fixtures={"BTCUSDT": [{"event_name": "Broken event"}]},
    )

    stale_payload = build_event_context(
        symbol="BTCUSDT",
        timeframe="15m",
        generated_at=generated_at,
        provider=stale_provider,
    )
    incomplete_payload = build_event_context(
        symbol="BTCUSDT",
        timeframe="15m",
        generated_at=generated_at,
        provider=incomplete_provider,
    )

    assert stale_payload.source_status == "stale"
    assert stale_payload.source_provenance == "fixture"
    assert stale_payload.is_stale is True
    assert stale_payload.is_complete is True
    assert incomplete_payload.source_status == "incomplete"
    assert incomplete_payload.source_provenance == "fixture"
    assert incomplete_payload.is_complete is False


def test_external_event_context_provider_preserves_external_provenance() -> None:
    generated_at = datetime(2026, 4, 20, 12, 0, 0)

    class _ExternalAdapter:
        def fetch_event_context(
            self,
            *,
            symbol: str,
            timeframe: str,
            generated_at: datetime,
        ) -> ExternalEventFetchPayload:
            return ExternalEventFetchPayload(
                source_status="external_api",
                source_vendor="fred",
                enrichment_vendors=("bls",),
                is_complete=True,
                events=(
                    {
                        "event_name": "US CPI",
                        "event_at": generated_at + timedelta(minutes=35),
                        "importance": "high",
                        "affected_assets": [symbol],
                        "enrichment_vendors": ["bls"],
                        "release_enrichment": {"bls": {"actual": 3.1}},
                    },
                ),
            )

    payload = build_event_context(
        symbol="BTCUSDT",
        timeframe="15m",
        generated_at=generated_at,
        provider=ExternalAPIEventContextProvider(adapter=_ExternalAdapter()),
    )

    assert payload.source_status == "external_api"
    assert payload.source_provenance == "external_api"
    assert payload.source_vendor == "fred"
    assert payload.enrichment_vendors == ["bls"]
    assert payload.is_complete is True
    assert payload.next_event_name == "US CPI"
    assert payload.events[0].release_enrichment["bls"]["actual"] == 3.1


def test_fred_release_dates_adapter_translates_calendar_response() -> None:
    generated_at = datetime(2026, 1, 28, 18, 0, 0)

    def _fetcher(*, url: str, params: dict[str, str], headers: dict[str, str]) -> dict[str, object]:
        assert url.endswith("/release/dates")
        assert headers["Accept"] == "application/json"
        release_id = int(params["release_id"])
        payloads = {
            101: {"release_dates": [{"release_id": 101, "date": "2026-01-28"}]},
            10: {"release_dates": [{"release_id": 10, "date": "2026-02-10"}]},
        }
        return payloads[release_id]

    payload = build_event_context(
        symbol="BTCUSDT",
        timeframe="15m",
        generated_at=generated_at,
        provider=ExternalAPIEventContextProvider(
            adapter=FredReleaseDatesAdapter(
                api_key="fred-demo",
                fetcher=_fetcher,
                release_ids=(101, 10),
            )
        ),
    )

    assert payload.source_status == "external_api"
    assert payload.source_provenance == "external_api"
    assert payload.source_vendor == "fred"
    assert payload.is_complete is True
    assert payload.next_event_name == "FOMC Press Release"
    assert payload.minutes_to_next_event == 60
    assert [event.event_name for event in payload.events] == ["FOMC Press Release", "Consumer Price Index"]
    assert payload.enrichment_vendors == []


def test_fred_release_dates_adapter_marks_partial_parsing_as_incomplete() -> None:
    generated_at = datetime(2026, 4, 20, 12, 0, 0)

    def _fetcher(*, url: str, params: dict[str, str], headers: dict[str, str]) -> dict[str, object]:
        del url, headers
        release_id = int(params["release_id"])
        if release_id == 10:
            return {"release_dates": [{"release_id": 10, "date": "2026-04-21"}]}
        return {"release_dates": [{"release_id": 50, "date": "not-a-date"}]}

    payload = build_event_context(
        symbol="BTCUSDT",
        timeframe="15m",
        generated_at=generated_at,
        provider=ExternalAPIEventContextProvider(
            adapter=FredReleaseDatesAdapter(
                api_key="fred-demo",
                fetcher=_fetcher,
                release_ids=(10, 50),
            )
        ),
    )

    assert payload.source_status == "incomplete"
    assert payload.source_provenance == "external_api"
    assert payload.source_vendor == "fred"
    assert payload.is_complete is False
    assert payload.next_event_name == "Consumer Price Index"


def test_fred_release_dates_adapter_marks_missing_upcoming_dates_as_unavailable() -> None:
    generated_at = datetime(2026, 4, 20, 12, 0, 0)

    def _fetcher(*, url: str, params: dict[str, str], headers: dict[str, str]) -> dict[str, object]:
        del url, headers
        release_id = int(params["release_id"])
        return {"release_dates": [{"release_id": release_id, "date": "2026-04-19"}]}

    payload = build_event_context(
        symbol="BTCUSDT",
        timeframe="15m",
        generated_at=generated_at,
        provider=ExternalAPIEventContextProvider(
            adapter=FredReleaseDatesAdapter(
                api_key="fred-demo",
                fetcher=_fetcher,
                release_ids=(10,),
            )
        ),
    )

    assert payload.source_status == "unavailable"
    assert payload.source_provenance == "external_api"
    assert payload.source_vendor == "fred"
    assert payload.is_complete is False
    assert payload.events == []


def test_fred_release_dates_adapter_applies_bls_and_bea_post_release_enrichment() -> None:
    generated_at = datetime(2026, 2, 10, 14, 0, 0)

    def _fred_fetcher(*, url: str, params: dict[str, str], headers: dict[str, str]) -> dict[str, object]:
        del url, headers
        release_id = int(params["release_id"])
        payloads = {
            10: {"release_dates": [{"release_id": 10, "date": "2026-02-10"}]},
            53: {"release_dates": [{"release_id": 53, "date": "2026-02-10"}]},
        }
        return payloads[release_id]

    def _bls_fetcher(*, url: str, params: dict[str, str], headers: dict[str, str]) -> dict[str, object]:
        del url, headers
        event_key = params["event_key"]
        if event_key == "cpi":
            return {"actual": 3.1, "prior": 2.9, "reference_period": "2026-01"}
        return {}

    def _bea_fetcher(*, url: str, params: dict[str, str], headers: dict[str, str]) -> dict[str, object]:
        del url, headers
        event_key = params["event_key"]
        if event_key == "gdp":
            return {"actual": 2.4, "prior": 2.1, "reference_period": "2025-Q4"}
        return {}

    payload = build_event_context(
        symbol="BTCUSDT",
        timeframe="15m",
        generated_at=generated_at,
        provider=ExternalAPIEventContextProvider(
            adapter=FredReleaseDatesAdapter(
                api_key="fred-demo",
                fetcher=_fred_fetcher,
                release_ids=(10, 53),
                post_release_enrichers=(
                    BLSActualReleaseEnrichmentAdapter(fetcher=_bls_fetcher),
                    BEAActualReleaseEnrichmentAdapter(fetcher=_bea_fetcher),
                ),
            )
        ),
    )

    assert payload.source_status == "external_api"
    assert payload.source_vendor == "fred"
    assert payload.enrichment_vendors == ["bls", "bea"]
    assert payload.next_event_name == "Consumer Price Index"
    assert payload.active_risk_window is True
    assert payload.events[0].release_enrichment["bls"]["actual"] == 3.1
    assert payload.events[1].release_enrichment["bea"]["actual"] == 2.4


def test_bls_native_release_enrichment_adapter_parses_vendor_payload() -> None:
    generated_at = datetime(2026, 2, 10, 14, 0, 0)

    def _bls_fetcher(*, url: str, params: dict[str, str], headers: dict[str, str]) -> dict[str, object]:
        del url, params, headers
        return {
            "status": "REQUEST_SUCCEEDED",
            "Results": {
                "series": [
                    {
                        "seriesID": "CUUR0000SA0",
                        "catalog": {"series_title": "Consumer Price Index for All Urban Consumers"},
                        "data": [
                            {
                                "year": "2026",
                                "period": "M01",
                                "periodName": "January",
                                "value": "3.1",
                                "latest": "true",
                                "footnotes": [{"text": "Preliminary"}],
                            }
                        ],
                    }
                ]
            },
        }

    adapter = BLSActualReleaseEnrichmentAdapter(fetcher=_bls_fetcher)
    events = adapter.enrich_events(
        symbol="BTCUSDT",
        timeframe="15m",
        generated_at=generated_at,
        events=(
            {
                "event_name": "Consumer Price Index",
                "event_at": datetime(2026, 2, 10, 13, 30, 0),
            },
        ),
    )

    assert events[0]["enrichment_vendors"] == ["bls"]
    assert events[0]["release_enrichment"]["bls"]["actual"] == 3.1
    assert events[0]["release_enrichment"]["bls"]["reference_period"] == "2026-01"
    assert events[0]["release_enrichment"]["bls"]["series_id"] == "CUUR0000SA0"
    assert events[0]["release_enrichment"]["bls"]["period_name"] == "January"
    assert events[0]["release_enrichment"]["bls"]["latest"] is True


def test_bls_native_release_enrichment_adapter_keeps_partial_fields_when_actual_is_missing() -> None:
    generated_at = datetime(2026, 2, 10, 14, 0, 0)

    def _bls_fetcher(*, url: str, params: dict[str, str], headers: dict[str, str]) -> dict[str, object]:
        del url, params, headers
        return {
            "status": "REQUEST_SUCCEEDED",
            "Results": {
                "series": [
                    {
                        "seriesID": "PCU0000000000000",
                        "data": [
                            {
                                "year": "2026",
                                "period": "M01",
                                "periodName": "January",
                                "value": "--",
                            }
                        ],
                    }
                ]
            },
        }

    adapter = BLSActualReleaseEnrichmentAdapter(fetcher=_bls_fetcher)
    events = adapter.enrich_events(
        symbol="BTCUSDT",
        timeframe="15m",
        generated_at=generated_at,
        events=(
            {
                "event_name": "Producer Price Index",
                "event_at": datetime(2026, 2, 10, 13, 30, 0),
            },
        ),
    )

    assert events[0]["enrichment_vendors"] == ["bls"]
    assert "actual" not in events[0]["release_enrichment"]["bls"]
    assert events[0]["release_enrichment"]["bls"]["reference_period"] == "2026-01"
    assert events[0]["release_enrichment"]["bls"]["series_id"] == "PCU0000000000000"


def test_bea_native_release_enrichment_adapter_parses_vendor_payload() -> None:
    generated_at = datetime(2026, 2, 10, 14, 0, 0)

    def _bea_fetcher(*, url: str, params: dict[str, str], headers: dict[str, str]) -> dict[str, object]:
        del url, params, headers
        return {
            "BEAAPI": {
                "Results": {
                    "Data": [
                        {
                            "TableName": "T10101",
                            "LineDescription": "Gross domestic product",
                            "LineNumber": "1",
                            "SeriesCode": "A191RC",
                            "TimePeriod": "2025Q4",
                            "DataValue": "2.4",
                            "NoteRef": "A",
                        }
                    ]
                }
            }
        }

    adapter = BEAActualReleaseEnrichmentAdapter(fetcher=_bea_fetcher)
    events = adapter.enrich_events(
        symbol="BTCUSDT",
        timeframe="15m",
        generated_at=generated_at,
        events=(
            {
                "event_name": "Gross Domestic Product",
                "event_at": datetime(2026, 2, 10, 13, 30, 0),
            },
        ),
    )

    assert events[0]["enrichment_vendors"] == ["bea"]
    assert events[0]["release_enrichment"]["bea"]["actual"] == 2.4
    assert events[0]["release_enrichment"]["bea"]["reference_period"] == "2025-Q4"
    assert events[0]["release_enrichment"]["bea"]["table_name"] == "T10101"
    assert events[0]["release_enrichment"]["bea"]["series_code"] == "A191RC"


def test_vendor_native_release_enrichment_adapter_falls_back_to_normalized_payload() -> None:
    generated_at = datetime(2026, 2, 10, 14, 0, 0)

    def _bea_fetcher(*, url: str, params: dict[str, str], headers: dict[str, str]) -> dict[str, object]:
        del url, params, headers
        return {"actual": 2.4, "prior": 2.1, "reference_period": "2025-Q4"}

    adapter = BEAActualReleaseEnrichmentAdapter(fetcher=_bea_fetcher)
    events = adapter.enrich_events(
        symbol="BTCUSDT",
        timeframe="15m",
        generated_at=generated_at,
        events=(
            {
                "event_name": "Gross Domestic Product",
                "event_at": datetime(2026, 2, 10, 13, 30, 0),
            },
        ),
    )

    assert events[0]["enrichment_vendors"] == ["bea"]
    assert events[0]["release_enrichment"]["bea"]["actual"] == 2.4
    assert events[0]["release_enrichment"]["bea"]["prior"] == 2.1
    assert events[0]["release_enrichment"]["bea"]["reference_period"] == "2025-Q4"


def test_resolve_event_context_provider_from_env_supports_fred(monkeypatch) -> None:
    monkeypatch.setenv("TRADING_EVENT_SOURCE_PROVIDER", "fred")
    monkeypatch.setenv("TRADING_EVENT_SOURCE_API_KEY", "fred-demo")
    monkeypatch.setenv("TRADING_EVENT_SOURCE_API_URL", "https://fred.example/fred")
    monkeypatch.setenv("TRADING_EVENT_SOURCE_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("TRADING_EVENT_SOURCE_DEFAULT_ASSETS", "BTCUSDT,ETHUSDT")
    monkeypatch.setenv("TRADING_EVENT_SOURCE_FRED_RELEASE_IDS", "10,101,invalid")

    provider = resolve_event_context_provider_from_env()

    assert isinstance(provider, ExternalAPIEventContextProvider)
    assert isinstance(provider.adapter, FredReleaseDatesAdapter)
    assert provider.adapter.api_key == "fred-demo"
    assert provider.adapter.base_url == "https://fred.example/fred"
    assert provider.adapter.timeout_seconds == 12.0
    assert provider.adapter.default_assets == ("BTCUSDT", "ETHUSDT")
    assert provider.adapter.release_ids == (10, 101)


def test_resolve_event_context_provider_from_env_attaches_post_release_enrichers(monkeypatch) -> None:
    monkeypatch.setenv("TRADING_EVENT_SOURCE_PROVIDER", "fred")
    monkeypatch.setenv("TRADING_EVENT_SOURCE_API_KEY", "fred-demo")
    monkeypatch.setenv("TRADING_EVENT_SOURCE_BLS_ENRICHMENT_URL", "https://bls.example/releases")
    monkeypatch.setenv("TRADING_EVENT_SOURCE_BLS_ENRICHMENT_STATIC_PARAMS", "series_id=CUUR0000SA0")
    monkeypatch.setenv("TRADING_EVENT_SOURCE_BEA_ENRICHMENT_URL", "https://bea.example/releases")
    monkeypatch.setenv("TRADING_EVENT_SOURCE_BEA_ENRICHMENT_STATIC_PARAMS", "dataset=NIPA")

    provider = resolve_event_context_provider_from_env()

    assert isinstance(provider, ExternalAPIEventContextProvider)
    assert isinstance(provider.adapter, FredReleaseDatesAdapter)
    assert len(provider.adapter.post_release_enrichers) == 2
    assert isinstance(provider.adapter.post_release_enrichers[0], BLSActualReleaseEnrichmentAdapter)
    assert isinstance(provider.adapter.post_release_enrichers[1], BEAActualReleaseEnrichmentAdapter)
    assert provider.adapter.post_release_enrichers[0].base_url == "https://bls.example/releases"
    assert provider.adapter.post_release_enrichers[0].static_params == {"series_id": "CUUR0000SA0"}
    assert provider.adapter.post_release_enrichers[1].base_url == "https://bea.example/releases"
    assert provider.adapter.post_release_enrichers[1].static_params == {"dataset": "NIPA"}


@dataclass
class _EventSourceSettingsRow:
    event_source_provider: str | None = None
    event_source_api_url: str | None = None
    event_source_timeout_seconds: float | None = None
    event_source_default_assets: list[str] | tuple[str, ...] = ()
    event_source_fred_release_ids: list[int] | tuple[int, ...] = ()
    event_source_bls_enrichment_url: str | None = None
    event_source_bls_enrichment_static_params: dict[str, str] | None = None
    event_source_bea_enrichment_url: str | None = None
    event_source_bea_enrichment_static_params: dict[str, str] | None = None


@dataclass
class _EventSourceCredentials:
    event_source_api_key: str = ""


def test_resolve_event_context_provider_prefers_settings_override_for_fred(monkeypatch) -> None:
    monkeypatch.setenv("TRADING_EVENT_SOURCE_PROVIDER", "stub")
    settings_row = _EventSourceSettingsRow(
        event_source_provider="fred",
        event_source_api_url="https://fred.settings/fred",
        event_source_timeout_seconds=18.0,
        event_source_default_assets=["BTCUSDT", "ETHUSDT"],
        event_source_fred_release_ids=[10, 101],
    )

    provider = resolve_event_context_provider(
        settings_row=settings_row,
        credentials=_EventSourceCredentials(event_source_api_key="fred-settings-key"),
    )

    assert isinstance(provider, ExternalAPIEventContextProvider)
    assert isinstance(provider.adapter, FredReleaseDatesAdapter)
    assert provider.adapter.api_key == "fred-settings-key"
    assert provider.adapter.base_url == "https://fred.settings/fred"
    assert provider.adapter.timeout_seconds == 18.0
    assert provider.adapter.default_assets == ("BTCUSDT", "ETHUSDT")
    assert provider.adapter.release_ids == (10, 101)


def test_resolve_event_context_provider_prefers_settings_enrichment_over_env(monkeypatch) -> None:
    monkeypatch.setenv("TRADING_EVENT_SOURCE_BLS_ENRICHMENT_URL", "https://bls.env/releases")
    monkeypatch.setenv("TRADING_EVENT_SOURCE_BEA_ENRICHMENT_URL", "https://bea.env/releases")
    settings_row = _EventSourceSettingsRow(
        event_source_provider="fred",
        event_source_api_url="https://fred.settings/fred",
        event_source_bls_enrichment_url="https://bls.settings/releases",
        event_source_bls_enrichment_static_params={"series_id": "CUUR0000SA0"},
        event_source_bea_enrichment_url="https://bea.settings/releases",
        event_source_bea_enrichment_static_params={"dataset": "NIPA"},
    )

    provider = resolve_event_context_provider(
        settings_row=settings_row,
        credentials=_EventSourceCredentials(event_source_api_key="fred-settings-key"),
    )

    assert isinstance(provider, ExternalAPIEventContextProvider)
    assert isinstance(provider.adapter, FredReleaseDatesAdapter)
    assert len(provider.adapter.post_release_enrichers) == 2
    assert provider.adapter.post_release_enrichers[0].base_url == "https://bls.settings/releases"
    assert provider.adapter.post_release_enrichers[0].static_params == {"series_id": "CUUR0000SA0"}
    assert provider.adapter.post_release_enrichers[1].base_url == "https://bea.settings/releases"
    assert provider.adapter.post_release_enrichers[1].static_params == {"dataset": "NIPA"}


def test_resolve_event_context_provider_falls_back_to_env_when_settings_override_is_empty(monkeypatch) -> None:
    monkeypatch.setenv("TRADING_EVENT_SOURCE_PROVIDER", "fred")
    monkeypatch.setenv("TRADING_EVENT_SOURCE_API_KEY", "fred-env-key")
    monkeypatch.setenv("TRADING_EVENT_SOURCE_API_URL", "https://fred.env/fred")
    monkeypatch.setenv("TRADING_EVENT_SOURCE_TIMEOUT_SECONDS", "14")
    monkeypatch.setenv("TRADING_EVENT_SOURCE_DEFAULT_ASSETS", "BTCUSDT,ETHUSDT")
    monkeypatch.setenv("TRADING_EVENT_SOURCE_FRED_RELEASE_IDS", "10,46")

    provider = resolve_event_context_provider(
        settings_row=_EventSourceSettingsRow(),
        credentials=_EventSourceCredentials(),
    )

    assert isinstance(provider, ExternalAPIEventContextProvider)
    assert isinstance(provider.adapter, FredReleaseDatesAdapter)
    assert provider.adapter.api_key == "fred-env-key"
    assert provider.adapter.base_url == "https://fred.env/fred"
    assert provider.adapter.timeout_seconds == 14.0
    assert provider.adapter.default_assets == ("BTCUSDT", "ETHUSDT")
    assert provider.adapter.release_ids == (10, 46)


def test_resolve_event_context_provider_allows_settings_stub_to_disable_env_provider(monkeypatch) -> None:
    monkeypatch.setenv("TRADING_EVENT_SOURCE_PROVIDER", "fred")
    monkeypatch.setenv("TRADING_EVENT_SOURCE_API_KEY", "fred-env-key")

    provider = resolve_event_context_provider(
        settings_row=_EventSourceSettingsRow(event_source_provider="stub"),
        credentials=_EventSourceCredentials(),
    )

    assert isinstance(provider, StubEventContextProvider)


def test_compute_features_preserves_regime_when_event_context_is_added() -> None:
    base = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    h1 = build_market_snapshot("BTCUSDT", "1h", upto_index=140)
    h4 = build_market_snapshot("BTCUSDT", "4h", upto_index=140)
    provider = FixtureEventContextProvider(
        fixtures={
            "BTCUSDT": [
                {
                    "event_name": "US CPI",
                    "event_at": base.snapshot_time + timedelta(minutes=90),
                    "importance": "high",
                    "affected_assets": ["BTCUSDT"],
                }
            ]
        }
    )
    event_context = build_event_context(
        symbol="BTCUSDT",
        timeframe="15m",
        generated_at=base.snapshot_time,
        provider=provider,
    )

    baseline_features = compute_features(base, {"1h": h1, "4h": h4})
    with_event_features = compute_features(
        base.model_copy(update={"event_context": event_context}),
        {
            "1h": h1.model_copy(update={"event_context": event_context}),
            "4h": h4.model_copy(update={"event_context": event_context}),
        },
    )

    assert with_event_features.regime == baseline_features.regime
    assert with_event_features.event_context.next_event_name == "US CPI"
    assert with_event_features.event_context.minutes_to_next_event == 90


def test_build_market_context_propagates_shared_event_context() -> None:
    provider = FixtureEventContextProvider(
        fixtures={
            "BTCUSDT": [
                {
                    "event_name": "Powell Speech",
                    "event_at": datetime(2099, 1, 1, 0, 0, 0),
                    "importance": "medium",
                    "affected_assets": ["BTCUSDT", "ETHUSDT"],
                }
            ]
        }
    )

    context = build_market_context(
        symbol="BTCUSDT",
        base_timeframe="15m",
        upto_index=140,
        event_context_provider=provider,
    )

    assert context["15m"].event_context.next_event_name == "Powell Speech"
    assert context["1h"].event_context.next_event_name == "Powell Speech"
    assert context["4h"].event_context.next_event_name == "Powell Speech"
