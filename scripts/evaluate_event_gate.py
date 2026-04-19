from __future__ import annotations
# ruff: noqa: E402, I001

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from trading_mvp.schemas import DerivativesContextPayload, MarketCandle, MarketSnapshotPayload
from trading_mvp.services.event_context import FixtureEventContextProvider, build_event_context
from trading_mvp.services.features import compute_features

DecisionMode = Literal["entry", "reduce_only", "exit_only"]
EntrySide = Literal["long", "short"]
EvaluationReason = Literal[
    "REGIME_LONG_ENTRY_ALLOWED",
    "REGIME_SHORT_ENTRY_ALLOWED",
    "REGIME_ENTRY_NOT_TRIGGERED",
    "SURVIVAL_PATH_ALLOWED",
    "HIGH_IMPACT_EVENT_WINDOW",
    "EVENT_SOURCE_UNAVAILABLE_ALLOWED",
    "EVENT_SOURCE_STALE_ALLOWED",
    "EVENT_SOURCE_INCOMPLETE_ALLOWED",
    "EVENT_CONTEXT_REQUIRED_BLOCKED",
]

ENTRY_DECISIONS = {"long", "short"}
SURVIVAL_DECISIONS = {"reduce", "exit"}
DEGRADED_EVENT_STATUSES = {"unavailable", "stale", "incomplete"}
UNAVAILABLE_OR_STALE_STATUSES = {"unavailable", "stale"}
DEFAULT_CANDLE_FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "event_gate" / "historical_candles.json"
DEFAULT_EVENT_FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "event_gate" / "macro_events.json"


@dataclass(frozen=True, slots=True)
class EvaluationConfig:
    event_guard_enabled: bool = True
    high_impact_block_before_minutes: int = 60
    high_impact_block_after_minutes: int = 30
    require_event_context_for_new_entries: bool = False


@dataclass(frozen=True, slots=True)
class ReplayCase:
    case_id: str
    symbol: str
    timeframe: str
    generated_at: datetime
    decision_mode: DecisionMode
    entry_side: EntrySide | None
    base_series: dict[str, Any]
    context_series: dict[str, dict[str, Any]]
    derivatives_context: dict[str, Any]


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if not isinstance(value, str):
        raise ValueError(f"datetime expected, got {value!r}")
    text = value.strip()
    if not text:
        raise ValueError("datetime string cannot be empty")
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC).replace(tzinfo=None)


def _timeframe_minutes(value: str) -> int:
    normalized = value.strip().lower()
    if normalized.endswith("m"):
        return int(normalized[:-1])
    if normalized.endswith("h"):
        return int(normalized[:-1]) * 60
    if normalized.endswith("d"):
        return int(normalized[:-1]) * 1440
    raise ValueError(f"unsupported timeframe {value!r}")


def _expand_series(
    *,
    generated_at: datetime,
    timeframe: str,
    spec: dict[str, Any],
) -> list[MarketCandle]:
    pattern = str(spec.get("pattern") or "trend_up").strip().lower()
    count = int(spec.get("count") or 60)
    start_price = float(spec.get("start_price") or 100.0)
    step = abs(float(spec.get("step") or 1.0))
    wick = float(spec.get("wick") or max(step * 0.35, 0.1))
    volume_start = float(spec.get("volume_start") or 1000.0)
    volume_step = float(spec.get("volume_step") or 12.0)
    amplitude = abs(float(spec.get("amplitude") or max(step * 2.0, 0.5)))
    interval_minutes = int(spec.get("interval_minutes") or _timeframe_minutes(timeframe))

    candles: list[MarketCandle] = []
    previous_close = start_price
    for index in range(count):
        timestamp = generated_at - timedelta(minutes=interval_minutes * (count - index - 1))
        if pattern == "trend_up":
            close = start_price + (index * step)
        elif pattern == "trend_down":
            close = max(start_price - (index * step), 0.1)
        elif pattern == "range":
            offset = amplitude if index % 2 == 0 else -amplitude
            close = max(start_price + offset, 0.1)
        else:
            raise ValueError(f"unsupported series pattern {pattern!r}")

        open_price = previous_close
        high = max(open_price, close) + wick
        low = max(min(open_price, close) - wick, 0.0001)
        volume = max(volume_start + (index * volume_step), 0.0)
        candles.append(
            MarketCandle(
                timestamp=timestamp,
                open=max(open_price, 0.0001),
                high=max(high, 0.0001),
                low=low,
                close=max(close, 0.0001),
                volume=volume,
            )
        )
        previous_close = close
    return candles


def _build_derivatives_context(payload: dict[str, Any] | None) -> DerivativesContextPayload:
    return DerivativesContextPayload.model_validate(payload or {})


def _load_replay_cases(path: Path) -> list[ReplayCase]:
    payload = _load_json(path)
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError("candle fixture must contain a top-level 'cases' list")
    cases: list[ReplayCase] = []
    for item in raw_cases:
        if not isinstance(item, dict):
            continue
        decision_mode = str(item.get("decision_mode") or "entry").strip().lower()
        if decision_mode not in {"entry", "reduce_only", "exit_only"}:
            raise ValueError(f"unsupported decision_mode {decision_mode!r}")
        entry_side_value = item.get("entry_side")
        entry_side = str(entry_side_value).strip().lower() if entry_side_value is not None else None
        if decision_mode == "entry" and entry_side not in {"long", "short"}:
            raise ValueError(f"entry case requires entry_side, got {entry_side_value!r}")
        cases.append(
            ReplayCase(
                case_id=str(item["case_id"]),
                symbol=str(item["symbol"]).upper(),
                timeframe=str(item["timeframe"]),
                generated_at=_parse_datetime(item["generated_at"]),
                decision_mode=decision_mode,  # type: ignore[arg-type]
                entry_side=entry_side if entry_side in {"long", "short"} else None,  # type: ignore[arg-type]
                base_series=dict(item.get("base_series") or {}),
                context_series={
                    str(key): dict(value)
                    for key, value in (item.get("context_series") or {}).items()
                    if isinstance(value, dict)
                },
                derivatives_context=dict(item.get("derivatives_context") or {}),
            )
        )
    return cases


def _load_event_fixture_map(path: Path) -> dict[str, dict[str, Any]]:
    payload = _load_json(path)
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, dict):
        raise ValueError("event fixture must contain a top-level 'cases' object")
    return {
        str(key): dict(value)
        for key, value in raw_cases.items()
        if isinstance(value, dict)
    }


def _build_case_event_context(case: ReplayCase, event_fixture: dict[str, Any]) -> Any:
    if str(event_fixture.get("mode") or "").strip().lower() == "unavailable":
        return build_event_context(
            symbol=case.symbol,
            timeframe=case.timeframe,
            generated_at=case.generated_at,
            provider=FixtureEventContextProvider(fixtures={}),
        )

    events = event_fixture.get("events")
    fixtures = {case.symbol: events} if isinstance(events, list) else {}
    provider = FixtureEventContextProvider(
        fixtures=fixtures,
        source_generated_at=_parse_datetime(event_fixture["source_generated_at"])
        if event_fixture.get("source_generated_at")
        else None,
        stale_after_minutes=int(event_fixture.get("stale_after_minutes") or 180),
    )
    return build_event_context(
        symbol=case.symbol,
        timeframe=case.timeframe,
        generated_at=case.generated_at,
        provider=provider,
    )


def _build_snapshot(
    *,
    symbol: str,
    timeframe: str,
    generated_at: datetime,
    series_spec: dict[str, Any],
    event_context: Any,
    derivatives_context: DerivativesContextPayload,
) -> MarketSnapshotPayload:
    candles = _expand_series(
        generated_at=generated_at,
        timeframe=timeframe,
        spec=series_spec,
    )
    latest = candles[-1]
    return MarketSnapshotPayload(
        symbol=symbol,
        timeframe=timeframe,
        snapshot_time=generated_at,
        latest_price=latest.close,
        latest_volume=latest.volume,
        candle_count=len(candles),
        is_stale=False,
        is_complete=True,
        candles=candles,
        derivatives_context=derivatives_context,
        event_context=event_context,
    )


def _propose_baseline_decision(case: ReplayCase, features: Any) -> tuple[str, EvaluationReason]:
    if case.decision_mode == "reduce_only":
        return "reduce", "SURVIVAL_PATH_ALLOWED"
    if case.decision_mode == "exit_only":
        return "exit", "SURVIVAL_PATH_ALLOWED"

    regime = features.regime
    if case.entry_side == "long":
        if regime.primary_regime == "bullish" and regime.trend_alignment == "bullish_aligned":
            return "long", "REGIME_LONG_ENTRY_ALLOWED"
        return "hold", "REGIME_ENTRY_NOT_TRIGGERED"
    if case.entry_side == "short":
        if regime.primary_regime == "bearish" and regime.trend_alignment == "bearish_aligned":
            return "short", "REGIME_SHORT_ENTRY_ALLOWED"
        return "hold", "REGIME_ENTRY_NOT_TRIGGERED"
    return "hold", "REGIME_ENTRY_NOT_TRIGGERED"


def _minutes_to_event(*, event: Any, generated_at: datetime) -> int | None:
    minutes_to_event = getattr(event, "minutes_to_event", None)
    if minutes_to_event is not None:
        return int(minutes_to_event)
    event_at = getattr(event, "event_at", None)
    if not isinstance(event_at, datetime):
        return None
    return int((event_at - generated_at).total_seconds() // 60)


def _active_high_impact_event(event_context: Any, *, config: EvaluationConfig) -> bool:
    generated_at = getattr(event_context, "generated_at", None)
    if not isinstance(generated_at, datetime):
        generated_at = None
    for event in event_context.events:
        if event.importance != "high":
            continue
        minutes_to_event = _minutes_to_event(event=event, generated_at=generated_at or datetime.min)
        if minutes_to_event is None:
            if event.active_risk_window:
                return True
            continue
        if -config.high_impact_block_after_minutes <= minutes_to_event <= config.high_impact_block_before_minutes:
            return True
    return False


def _apply_event_guard(
    *,
    decision: str,
    baseline_reason: EvaluationReason,
    event_context: Any,
    config: EvaluationConfig,
) -> tuple[str, EvaluationReason, bool]:
    if decision not in ENTRY_DECISIONS:
        return decision, baseline_reason, False
    if not config.event_guard_enabled:
        return decision, baseline_reason, False
    if _active_high_impact_event(event_context, config=config):
        return "hold", "HIGH_IMPACT_EVENT_WINDOW", True

    source_status = str(event_context.source_status)
    if source_status in DEGRADED_EVENT_STATUSES:
        if config.require_event_context_for_new_entries:
            return "hold", "EVENT_CONTEXT_REQUIRED_BLOCKED", True
        degraded_reason_map: dict[str, EvaluationReason] = {
            "unavailable": "EVENT_SOURCE_UNAVAILABLE_ALLOWED",
            "stale": "EVENT_SOURCE_STALE_ALLOWED",
            "incomplete": "EVENT_SOURCE_INCOMPLETE_ALLOWED",
        }
        return decision, degraded_reason_map.get(source_status, baseline_reason), False

    return decision, baseline_reason, False


def _sorted_breakdown(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def evaluate_replay(
    *,
    candle_fixture_path: Path = DEFAULT_CANDLE_FIXTURE_PATH,
    event_fixture_path: Path = DEFAULT_EVENT_FIXTURE_PATH,
    config: EvaluationConfig | None = None,
) -> dict[str, Any]:
    resolved_config = config or EvaluationConfig()
    cases = sorted(_load_replay_cases(candle_fixture_path), key=lambda item: item.case_id)
    event_fixture_map = _load_event_fixture_map(event_fixture_path)

    baseline_reasons: Counter[str] = Counter()
    event_reasons: Counter[str] = Counter()
    baseline_entry_candidate_count = 0
    baseline_allowed_entry_count = 0
    event_blocked_entry_count = 0
    blocked_by_high_impact_window_count = 0
    reduced_paths_still_allowed_count = 0
    unavailable_stale_event_source_count = 0
    case_results: list[dict[str, Any]] = []

    for case in cases:
        event_context = _build_case_event_context(case, event_fixture_map.get(case.case_id, {"mode": "unavailable"}))
        derivatives_context = _build_derivatives_context(case.derivatives_context)
        base_snapshot = _build_snapshot(
            symbol=case.symbol,
            timeframe=case.timeframe,
            generated_at=case.generated_at,
            series_spec=case.base_series,
            event_context=event_context,
            derivatives_context=derivatives_context,
        )
        context_snapshots = {
            timeframe: _build_snapshot(
                symbol=case.symbol,
                timeframe=timeframe,
                generated_at=case.generated_at,
                series_spec=series_spec,
                event_context=event_context,
                derivatives_context=derivatives_context,
            )
            for timeframe, series_spec in sorted(case.context_series.items())
        }
        features = compute_features(base_snapshot, context_snapshots=context_snapshots)
        baseline_decision, baseline_reason = _propose_baseline_decision(case, features)
        baseline_reasons[baseline_reason] += 1
        if baseline_decision in ENTRY_DECISIONS:
            baseline_entry_candidate_count += 1
            baseline_allowed_entry_count += 1
        if baseline_decision in SURVIVAL_DECISIONS:
            reduced_paths_still_allowed_count += 1

        event_decision, event_reason, blocked = _apply_event_guard(
            decision=baseline_decision,
            baseline_reason=baseline_reason,
            event_context=event_context,
            config=resolved_config,
        )
        event_reasons[event_reason] += 1
        if blocked and baseline_decision in ENTRY_DECISIONS:
            event_blocked_entry_count += 1
        if event_reason == "HIGH_IMPACT_EVENT_WINDOW":
            blocked_by_high_impact_window_count += 1
        if str(event_context.source_status) in UNAVAILABLE_OR_STALE_STATUSES:
            unavailable_stale_event_source_count += 1

        case_results.append(
            {
                "case_id": case.case_id,
                "symbol": case.symbol,
                "timeframe": case.timeframe,
                "generated_at": case.generated_at.isoformat(),
                "baseline_decision": baseline_decision,
                "baseline_reason": baseline_reason,
                "event_aware_decision": event_decision,
                "event_aware_reason": event_reason,
                "blocked_by_event_guard": blocked,
                "event_source_status": str(event_context.source_status),
                "event_active_risk_window": bool(event_context.active_risk_window),
                "next_event_name": event_context.next_event_name,
                "minutes_to_next_event": event_context.minutes_to_next_event,
                "regime_summary": {
                    "primary_regime": features.regime.primary_regime,
                    "trend_alignment": features.regime.trend_alignment,
                    "volatility_regime": features.regime.volatility_regime,
                    "volume_regime": features.regime.volume_regime,
                },
            }
        )

    return {
        "config": {
            "event_guard_enabled": resolved_config.event_guard_enabled,
            "high_impact_block_before_minutes": resolved_config.high_impact_block_before_minutes,
            "high_impact_block_after_minutes": resolved_config.high_impact_block_after_minutes,
            "require_event_context_for_new_entries": resolved_config.require_event_context_for_new_entries,
        },
        "case_count": len(cases),
        "baseline": {
            "entry_candidate_count": baseline_entry_candidate_count,
            "allowed_entry_count": baseline_allowed_entry_count,
            "blocked_entry_count": 0,
            "decision_reason_breakdown": _sorted_breakdown(baseline_reasons),
        },
        "event_aware": {
            "entry_candidate_count": baseline_entry_candidate_count,
            "allowed_entry_count": baseline_allowed_entry_count - event_blocked_entry_count,
            "blocked_entry_count": event_blocked_entry_count,
            "blocked_by_high_impact_window_count": blocked_by_high_impact_window_count,
            "reduced_paths_still_allowed_count": reduced_paths_still_allowed_count,
            "unavailable_stale_event_source_count": unavailable_stale_event_source_count,
            "decision_reason_breakdown": _sorted_breakdown(event_reasons),
        },
        "delta": {
            "additional_blocked_entries": event_blocked_entry_count,
            "preserved_survival_paths": reduced_paths_still_allowed_count,
        },
        "cases": case_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare baseline regime-only decisions against an event-aware entry gate using offline fixtures.",
    )
    parser.add_argument("--candles", type=Path, default=DEFAULT_CANDLE_FIXTURE_PATH)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENT_FIXTURE_PATH)
    parser.add_argument("--strict-event-context", action="store_true")
    parser.add_argument("--before-minutes", type=int, default=60)
    parser.add_argument("--after-minutes", type=int, default=30)
    args = parser.parse_args()

    result = evaluate_replay(
        candle_fixture_path=args.candles,
        event_fixture_path=args.events,
        config=EvaluationConfig(
            high_impact_block_before_minutes=max(args.before_minutes, 0),
            high_impact_block_after_minutes=max(args.after_minutes, 0),
            require_event_context_for_new_entries=args.strict_event_context,
        ),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
