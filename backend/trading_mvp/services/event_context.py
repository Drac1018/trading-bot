from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from trading_mvp.schemas import EventBias, EventContextPayload, EventSourceStatus, MacroEventPayload


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
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


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


class EventContextProvider(Protocol):
    def get_event_context(
        self,
        *,
        symbol: str,
        timeframe: str,
        generated_at: datetime,
    ) -> EventContextPayload: ...


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
            return EventContextPayload(
                source_status="unavailable",
                generated_at=generated_at,
                is_stale=False,
                is_complete=False,
                active_risk_window=False,
                affected_assets=[],
                event_bias=None,
                events=[],
            )

        events: list[MacroEventPayload] = []
        incomplete = False
        for raw in raw_events:
            event_at = _parse_datetime(raw.get("event_at"))
            event_name = str(raw.get("event_name") or raw.get("name") or "").strip()
            if event_at is None or not event_name:
                incomplete = True
                continue
            importance = raw.get("importance")
            if importance not in {"low", "medium", "high", None, ""}:
                incomplete = True
                importance = None
            bias = raw.get("event_bias")
            if bias not in {"bullish", "bearish", "neutral", None, ""}:
                incomplete = True
                bias = None
            risk_window_before_minutes = int(raw.get("risk_window_before_minutes") or 60)
            risk_window_after_minutes = int(raw.get("risk_window_after_minutes") or 30)
            active_risk_window = _is_active_risk_window(
                generated_at=generated_at,
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
                    minutes_to_event=_minutes_until(generated_at=generated_at, event_at=event_at),
                    risk_window_before_minutes=risk_window_before_minutes,
                    risk_window_after_minutes=risk_window_after_minutes,
                    active_risk_window=active_risk_window,
                )
            )

        events.sort(key=lambda item: item.event_at)
        upcoming = next((item for item in events if item.event_at >= generated_at), None)
        active_events = [item for item in events if item.active_risk_window]
        stale = False
        if self.source_generated_at is not None:
            stale = (generated_at - self.source_generated_at).total_seconds() > self.stale_after_minutes * 60

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
        if active_events:
            summary_bias = active_events[0].event_bias
        elif upcoming is not None:
            summary_bias = upcoming.event_bias

        source_status: EventSourceStatus = "fixture"
        if stale:
            source_status = "stale"
        elif incomplete:
            source_status = "incomplete"

        return EventContextPayload(
            source_status=source_status,
            generated_at=generated_at,
            is_stale=stale,
            is_complete=not incomplete,
            next_event_at=upcoming.event_at if upcoming is not None else None,
            next_event_name=upcoming.event_name if upcoming is not None else None,
            next_event_importance=upcoming.importance if upcoming is not None else None,
            minutes_to_next_event=upcoming.minutes_to_event if upcoming is not None else None,
            active_risk_window=bool(active_events),
            affected_assets=deduped_assets,
            event_bias=summary_bias,
            events=events,
        )


def build_event_context(
    *,
    symbol: str,
    timeframe: str,
    generated_at: datetime,
    provider: EventContextProvider | None = None,
) -> EventContextPayload:
    resolved_provider = provider or FixtureEventContextProvider()
    return resolved_provider.get_event_context(
        symbol=symbol,
        timeframe=timeframe,
        generated_at=generated_at,
    )
