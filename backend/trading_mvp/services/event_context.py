from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from trading_mvp.schemas import (
    EventBias,
    EventContextPayload,
    EventSourceStatus,
    MacroEventPayload,
    OperatorActiveRiskWindowPayload,
    OperatorEventContextPayload,
    OperatorEventImportance,
    OperatorEventItemPayload,
    OperatorEventSourceStatus,
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


def _normalize_operator_source_status(value: object) -> OperatorEventSourceStatus:
    text = str(value or "").strip().lower()
    if text in {"fixture", "stub", "available"}:
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
    if not source:
        return OperatorEventContextPayload(
            source_status="unavailable",
            generated_at=generated_at_value,
            is_stale=False,
            is_complete=False,
            active_risk_window=False,
            active_risk_window_detail=OperatorActiveRiskWindowPayload(
                is_active=False,
                summary_note=summary_note or "event context provider unavailable",
            ),
            next_event_at=None,
            next_event_name=None,
            next_event_importance="unknown",
            minutes_to_next_event=None,
            upcoming_events=[],
            affected_assets=[],
            summary_note=summary_note or "event context provider unavailable",
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
        summary_note=summary_note,
    )

    return OperatorEventContextPayload(
        source_status=_normalize_operator_source_status(source.get("source_status")),
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
        summary_note=summary_note,
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
        ).model_copy(update={"source_status": self.source_status})


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
