from __future__ import annotations

from datetime import datetime, timedelta

from trading_mvp.schemas import EventContextPayload, MacroEventPayload
from trading_mvp.services.event_context import FixtureEventContextProvider, build_event_context
from trading_mvp.services.features import compute_features
from trading_mvp.services.market_data import build_market_context, build_market_snapshot


def test_event_context_payload_serialization() -> None:
    generated_at = datetime(2026, 4, 20, 9, 0, 0)
    payload = EventContextPayload(
        source_status="fixture",
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
    assert dumped["next_event_name"] == "US CPI"
    assert dumped["minutes_to_next_event"] == 45
    assert dumped["events"][0]["event_name"] == "US CPI"


def test_event_context_provider_unavailable_does_not_crash_snapshot() -> None:
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)

    assert snapshot.event_context.source_status == "unavailable"
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
    assert stale_payload.is_stale is True
    assert stale_payload.is_complete is True
    assert incomplete_payload.source_status == "incomplete"
    assert incomplete_payload.is_complete is False


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
