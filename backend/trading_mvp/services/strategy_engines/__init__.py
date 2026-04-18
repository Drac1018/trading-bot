from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from trading_mvp.models import Position
    from trading_mvp.schemas import FeaturePayload, MarketSnapshotPayload


EngineDecision = Literal["hold", "long", "short", "reduce", "exit"]


@dataclass(slots=True)
class StrategyEngineCandidate:
    engine_name: str
    scenario: str
    decision_hint: EngineDecision
    entry_mode: str
    eligible: bool
    priority: float
    reasons: list[str]

    def to_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["priority"] = round(float(self.priority), 4)
        return payload


@dataclass(slots=True)
class StrategyEngineSelection:
    selected_engine: StrategyEngineCandidate
    candidates: list[StrategyEngineCandidate]
    session_context: dict[str, object]

    def to_payload(self) -> dict[str, object]:
        return {
            "selected_engine": self.selected_engine.to_payload(),
            "candidates": [candidate.to_payload() for candidate in self.candidates],
            "session_context": dict(self.session_context),
        }


def _session_context(snapshot_time: datetime | None) -> dict[str, object]:
    current = snapshot_time or datetime.utcnow()
    hour = int(current.hour)
    if 0 <= hour < 8:
        session_label = "asia"
    elif 8 <= hour < 13:
        session_label = "europe"
    elif 13 <= hour < 21:
        session_label = "us"
    else:
        session_label = "after_hours"
    bucket_start = (hour // 6) * 6
    bucket_end = min(bucket_start + 5, 23)
    return {
        "utc_hour": hour,
        "session_label": session_label,
        "time_of_day_bucket": f"utc_{bucket_start:02d}_{bucket_end:02d}",
    }


def _range_mean_reversion_candidate(*, features: FeaturePayload) -> StrategyEngineCandidate:
    regime = features.regime
    eligible = regime.primary_regime == "range" and not regime.weak_volume
    reasons = ["PRIMARY_REGIME_RANGE"]
    if regime.weak_volume:
        reasons.append("WEAK_VOLUME_RANGE")
    return StrategyEngineCandidate(
        engine_name="range_mean_reversion_engine",
        scenario="hold",
        decision_hint="hold",
        entry_mode="pullback_confirm",
        eligible=eligible,
        priority=0.42 if eligible else 0.22,
        reasons=reasons,
    )


def _trend_pullback_candidate(*, features: FeaturePayload) -> StrategyEngineCandidate:
    state = str(features.pullback_context.state or "")
    bullish = state == "bullish_pullback" and features.regime.trend_alignment == "bullish_aligned"
    bearish = state == "bearish_pullback" and features.regime.trend_alignment == "bearish_aligned"
    eligible = (bullish or bearish) and features.regime.primary_regime != "range" and not features.regime.weak_volume
    return StrategyEngineCandidate(
        engine_name="trend_pullback_engine",
        scenario="pullback_entry",
        decision_hint="long" if bullish else "short" if bearish else "hold",
        entry_mode="pullback_confirm",
        eligible=eligible,
        priority=0.84 if eligible else 0.24,
        reasons=[state.upper() or "NO_PULLBACK_STATE"],
    )


def _trend_continuation_candidate(*, features: FeaturePayload) -> StrategyEngineCandidate:
    state = str(features.pullback_context.state or "")
    bullish = state == "bullish_continuation" and features.regime.trend_alignment == "bullish_aligned"
    bearish = state == "bearish_continuation" and features.regime.trend_alignment == "bearish_aligned"
    eligible = (bullish or bearish) and features.regime.primary_regime != "range" and not features.regime.weak_volume
    return StrategyEngineCandidate(
        engine_name="trend_continuation_engine",
        scenario="trend_follow",
        decision_hint="long" if bullish else "short" if bearish else "hold",
        entry_mode="pullback_confirm",
        eligible=eligible,
        priority=0.78 if eligible else 0.2,
        reasons=[state.upper() or "NO_CONTINUATION_STATE"],
    )


def _breakout_exception_candidate(
    *,
    features: FeaturePayload,
    long_breakout_allowed: bool,
    short_breakout_allowed: bool,
) -> StrategyEngineCandidate:
    breakout_direction = str(features.breakout.range_breakout_direction or "none")
    eligible = bool(long_breakout_allowed or short_breakout_allowed)
    return StrategyEngineCandidate(
        engine_name="breakout_exception_engine",
        scenario="trend_follow",
        decision_hint="long" if long_breakout_allowed else "short" if short_breakout_allowed else "hold",
        entry_mode="breakout_confirm",
        eligible=eligible,
        priority=0.72 if eligible else 0.18,
        reasons=[
            "BREAKOUT_EXCEPTION_ALLOWED" if eligible else "BREAKOUT_EXCEPTION_NOT_ALLOWED",
            breakout_direction.upper(),
        ],
    )


def _protection_reduce_candidate(
    *,
    open_positions: list[Position],
    risk_context: dict[str, object],
) -> StrategyEngineCandidate:
    operating_state = str(risk_context.get("operating_state", "TRADABLE"))
    has_open_position = bool(open_positions)
    position_management_context = (
        dict(risk_context.get("position_management_context"))
        if isinstance(risk_context.get("position_management_context"), dict)
        else {}
    )
    reduce_reasons = position_management_context.get("reduce_reasons")
    protection_restore = operating_state == "PROTECTION_REQUIRED"
    eligible = has_open_position and (
        protection_restore
        or operating_state == "DEGRADED_MANAGE_ONLY"
        or bool(reduce_reasons)
    )
    return StrategyEngineCandidate(
        engine_name="protection_reduce_engine",
        scenario="protection_restore" if protection_restore else "reduce",
        decision_hint="reduce",
        entry_mode="none",
        eligible=eligible,
        priority=0.98 if protection_restore else 0.9 if eligible else 0.16,
        reasons=[
            operating_state,
            "OPEN_POSITION_PRESENT" if has_open_position else "NO_OPEN_POSITION",
        ],
    )


def select_strategy_engine(
    *,
    market_snapshot: MarketSnapshotPayload,
    features: FeaturePayload,
    open_positions: list[Position],
    risk_context: dict[str, object] | None = None,
    long_breakout_allowed: bool,
    short_breakout_allowed: bool,
) -> StrategyEngineSelection:
    resolved_risk_context = dict(risk_context or {})
    candidates = [
        _protection_reduce_candidate(open_positions=open_positions, risk_context=resolved_risk_context),
        _trend_pullback_candidate(features=features),
        _trend_continuation_candidate(features=features),
        _breakout_exception_candidate(
            features=features,
            long_breakout_allowed=long_breakout_allowed,
            short_breakout_allowed=short_breakout_allowed,
        ),
        _range_mean_reversion_candidate(features=features),
    ]
    ordered = sorted(
        candidates,
        key=lambda candidate: (candidate.eligible, candidate.priority),
        reverse=True,
    )
    return StrategyEngineSelection(
        selected_engine=ordered[0],
        candidates=ordered,
        session_context=_session_context(getattr(market_snapshot, "snapshot_time", None)),
    )
