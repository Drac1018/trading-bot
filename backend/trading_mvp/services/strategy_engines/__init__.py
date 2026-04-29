from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from trading_mvp.models import Position
    from trading_mvp.schemas import FeaturePayload, MarketSnapshotPayload


EngineDecision = Literal["hold", "long", "short", "reduce", "exit"]

RANGE_REVERSION_MIN_WIDTH_PCT = 0.25
RANGE_REVERSION_MIN_VOLUME_PERSISTENCE = 0.6
RANGE_REVERSION_LOWER_EDGE_MAX = 0.32
RANGE_REVERSION_UPPER_EDGE_MIN = 0.68
QUIET_RANGE_MIN_VOLUME_RATIO = 0.45
QUIET_RANGE_MAX_SPREAD_BPS = 5.5


def _value(source: object, key: str, default: object = None) -> object:
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _context(source: object, key: str) -> object:
    return _value(source, key, {}) or {}


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _range_spread_ok(features: object) -> bool:
    derivatives = _context(features, "derivatives")
    if bool(_value(derivatives, "spread_stress", False)) or bool(_value(derivatives, "spread_headwind", False)):
        return False
    spread_bps = _optional_float(_value(derivatives, "spread_bps"))
    return spread_bps is None or spread_bps <= QUIET_RANGE_MAX_SPREAD_BPS


def quiet_range_mean_reversion_allowed(features: object) -> bool:
    regime = _context(features, "regime")
    if str(_value(regime, "primary_regime") or "") != "range":
        return False
    if not bool(_value(regime, "weak_volume", False)):
        return False

    breakout = _context(features, "breakout")
    location = _context(features, "location")
    volume_persistence = _context(features, "volume_persistence")
    range_position = _as_float(_value(location, "range_position_pct"), 0.5)
    at_range_edge = (
        range_position <= RANGE_REVERSION_LOWER_EDGE_MAX
        or range_position >= RANGE_REVERSION_UPPER_EDGE_MIN
    )

    return bool(
        _as_float(_value(features, "volume_ratio")) >= QUIET_RANGE_MIN_VOLUME_RATIO
        and _as_float(_value(breakout, "range_width_pct")) >= RANGE_REVERSION_MIN_WIDTH_PCT
        and str(_value(breakout, "range_breakout_direction") or "none") == "none"
        and _as_float(_value(volume_persistence, "persistence_ratio")) >= RANGE_REVERSION_MIN_VOLUME_PERSISTENCE
        and at_range_edge
        and _range_spread_ok(features)
    )


def range_mean_reversion_volume_ok(features: object) -> bool:
    regime = _context(features, "regime")
    return not bool(_value(regime, "weak_volume", False)) or quiet_range_mean_reversion_allowed(features)


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
    location = features.location
    breakout = features.breakout
    range_position = float(location.range_position_pct or 0.0)
    vwap_distance = float(location.vwap_distance_pct or 0.0)
    range_width = float(breakout.range_width_pct or 0.0)
    breakout_direction = str(breakout.range_breakout_direction or "none")
    volume_persistence = float(features.volume_persistence.persistence_ratio or 0.0)
    range_width_ok = range_width >= RANGE_REVERSION_MIN_WIDTH_PCT
    range_intact = breakout_direction == "none"
    volume_alive = volume_persistence >= RANGE_REVERSION_MIN_VOLUME_PERSISTENCE
    range_volume_ok = range_mean_reversion_volume_ok(features)
    long_edge = (
        range_position <= RANGE_REVERSION_LOWER_EDGE_MAX
        and 30.0 <= features.rsi <= 48.0
        and -2.5 <= vwap_distance <= 0.35
        and features.momentum_score >= -0.45
    )
    short_edge = (
        range_position >= RANGE_REVERSION_UPPER_EDGE_MIN
        and 52.0 <= features.rsi <= 70.0
        and -0.35 <= vwap_distance <= 2.5
        and features.momentum_score <= 0.45
    )
    decision_hint: EngineDecision = "long" if long_edge else "short" if short_edge else "hold"
    range_ready = (
        regime.primary_regime == "range"
        and range_volume_ok
        and range_width_ok
        and range_intact
        and volume_alive
    )
    eligible = range_ready and decision_hint in {"long", "short"}
    reasons = ["PRIMARY_REGIME_RANGE"]
    if regime.weak_volume and range_volume_ok:
        reasons.append("QUIET_RANGE_WEAK_VOLUME_ALLOWED")
    elif regime.weak_volume:
        reasons.append("WEAK_VOLUME_RANGE")
    if not range_width_ok:
        reasons.append("RANGE_TOO_NARROW")
    if not range_intact:
        reasons.append("RANGE_BREAKOUT_ACTIVE")
    if not volume_alive:
        reasons.append("RANGE_VOLUME_INSUFFICIENT")
    if long_edge:
        reasons.append("RANGE_LOWER_REVERSION_LONG")
    elif short_edge:
        reasons.append("RANGE_UPPER_REVERSION_SHORT")
    else:
        reasons.append("RANGE_MIDDLE_NO_EDGE")
    return StrategyEngineCandidate(
        engine_name="range_mean_reversion_engine",
        scenario="pullback_entry" if decision_hint in {"long", "short"} else "hold",
        decision_hint=decision_hint,
        entry_mode="pullback_confirm",
        eligible=eligible,
        priority=0.66 if eligible else 0.46 if regime.primary_regime == "range" else 0.22,
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
