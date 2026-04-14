from __future__ import annotations

from collections.abc import Mapping
from math import sqrt
from statistics import mean, pstdev
from typing import Literal

from sqlalchemy.orm import Session

from trading_mvp.models import FeatureSnapshot
from trading_mvp.schemas import (
    BreakoutFeatureContext,
    CandleStructureFeatureContext,
    FeaturePayload,
    LocationFeatureContext,
    MarketSnapshotPayload,
    PullbackContinuationFeatureContext,
    RegimeFeatureContext,
    TimeframeFeatureContext,
    VolumePersistenceFeatureContext,
)


def _simple_moving_average(values: list[float], period: int) -> float:
    window = values[-period:] if len(values) >= period else values
    return mean(window)


def _returns(values: list[float]) -> list[float]:
    output: list[float] = []
    for index in range(1, len(values)):
        previous = values[index - 1]
        if previous == 0:
            output.append(0.0)
        else:
            output.append((values[index] - previous) / previous)
    return output


def _rsi(values: list[float], period: int = 14) -> float:
    if len(values) < 2:
        return 50.0
    deltas = [values[index] - values[index - 1] for index in range(1, len(values))]
    window = deltas[-period:]
    gains = [delta for delta in window if delta > 0]
    losses = [-delta for delta in window if delta < 0]
    avg_gain = mean(gains) if gains else 0.0
    avg_loss = mean(losses) if losses else 0.0
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    if len(closes) < 2:
        return max(highs[-1] - lows[-1], 0.0)
    true_ranges: list[float] = []
    for index in range(1, len(closes)):
        true_ranges.append(
            max(
                highs[index] - lows[index],
                abs(highs[index] - closes[index - 1]),
                abs(lows[index] - closes[index - 1]),
            )
        )
    window = true_ranges[-period:] if len(true_ranges) >= period else true_ranges
    return mean(window) if window else 0.0


def _safe_volume_ratio(volumes: list[float]) -> float:
    if not volumes:
        return 1.0
    baseline = mean(volumes[-10:] if len(volumes) >= 10 else volumes)
    if baseline <= 0:
        return 1.0
    return volumes[-1] / baseline


def _safe_pct(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return (numerator / denominator) * 100


def _vwap(candles) -> float | None:
    volume_sum = sum(float(candle.volume) for candle in candles)
    if volume_sum <= 0:
        return None
    weighted_sum = sum(float(candle.close) * float(candle.volume) for candle in candles)
    return weighted_sum / volume_sum


def _breakout_context(snapshot: MarketSnapshotPayload, *, lookback_bars: int = 8) -> BreakoutFeatureContext:
    candles = snapshot.candles
    effective_lookback = min(max(2, lookback_bars), max(len(candles) - 1, 2))
    prior_candles = candles[-(effective_lookback + 1):-1] if len(candles) > 1 else candles
    latest_candle = candles[-1]
    swing_high = max((float(candle.high) for candle in prior_candles), default=float(latest_candle.high))
    swing_low = min((float(candle.low) for candle in prior_candles), default=float(latest_candle.low))
    range_high = max((float(candle.close) for candle in prior_candles), default=float(latest_candle.close))
    range_low = min((float(candle.close) for candle in prior_candles), default=float(latest_candle.close))
    latest_close = float(latest_candle.close)
    range_breakout_direction: Literal["up", "down", "none"] = "none"
    if latest_close > range_high:
        range_breakout_direction = "up"
    elif latest_close < range_low:
        range_breakout_direction = "down"
    return BreakoutFeatureContext(
        lookback_bars=effective_lookback,
        swing_high=round(swing_high, 4),
        swing_low=round(swing_low, 4),
        range_high=round(range_high, 4),
        range_low=round(range_low, 4),
        range_width_pct=round(_safe_pct(range_high - range_low, max(latest_close, 1.0)), 4),
        broke_swing_high=latest_close > swing_high,
        broke_swing_low=latest_close < swing_low,
        range_breakout_direction=range_breakout_direction,
    )


def _candle_structure_context(snapshot: MarketSnapshotPayload) -> CandleStructureFeatureContext:
    latest_candle = snapshot.candles[-1]
    total_range = max(float(latest_candle.high) - float(latest_candle.low), 0.0)
    body = abs(float(latest_candle.close) - float(latest_candle.open))
    upper_wick = max(float(latest_candle.high) - max(float(latest_candle.open), float(latest_candle.close)), 0.0)
    lower_wick = max(min(float(latest_candle.open), float(latest_candle.close)) - float(latest_candle.low), 0.0)

    bullish_streak = 0
    bearish_streak = 0
    bullish_strength = 0.0
    bearish_strength = 0.0
    for candle in reversed(snapshot.candles):
        open_price = float(candle.open)
        close_price = float(candle.close)
        candle_body_strength = abs(_safe_pct(close_price - open_price, max(open_price, 1.0)))
        if close_price > open_price and bearish_streak == 0:
            bullish_streak += 1
            bullish_strength += candle_body_strength
            continue
        if close_price < open_price and bullish_streak == 0:
            bearish_streak += 1
            bearish_strength += candle_body_strength
            continue
        break

    ratio_denominator = total_range if total_range > 0 else max(float(latest_candle.close), 1.0)
    wick_to_body_ratio = (upper_wick + lower_wick) / body if body > 0 else (upper_wick + lower_wick) / ratio_denominator
    return CandleStructureFeatureContext(
        body_ratio=round(body / ratio_denominator, 4),
        upper_wick_ratio=round(upper_wick / ratio_denominator, 4),
        lower_wick_ratio=round(lower_wick / ratio_denominator, 4),
        wick_to_body_ratio=round(wick_to_body_ratio, 4),
        bullish_streak=bullish_streak,
        bearish_streak=bearish_streak,
        bullish_streak_strength=round(bullish_strength, 4),
        bearish_streak_strength=round(bearish_strength, 4),
    )


def _location_context(snapshot: MarketSnapshotPayload, breakout: BreakoutFeatureContext) -> LocationFeatureContext:
    latest_price = max(float(snapshot.latest_price), 1.0)
    recent_span = breakout.swing_high - breakout.swing_low
    range_position = 0.5
    if recent_span > 0:
        range_position = (latest_price - breakout.swing_low) / recent_span
    vwap = _vwap(snapshot.candles)
    vwap_distance_pct = _safe_pct(latest_price - vwap, max(vwap, 1.0)) if vwap is not None else 0.0
    return LocationFeatureContext(
        distance_from_recent_high_pct=round(_safe_pct(latest_price - breakout.swing_high, latest_price), 4),
        distance_from_recent_low_pct=round(_safe_pct(latest_price - breakout.swing_low, latest_price), 4),
        range_position_pct=round(range_position, 4),
        vwap_distance_pct=round(vwap_distance_pct, 4),
    )


def _volume_persistence_context(snapshot: MarketSnapshotPayload, *, recent_window: int = 5) -> VolumePersistenceFeatureContext:
    volumes = [float(candle.volume) for candle in snapshot.candles]
    effective_window = min(max(2, recent_window), len(volumes))
    recent_volumes = volumes[-effective_window:]
    baseline_volumes = volumes[-max(effective_window * 2, 10):]
    baseline = mean(baseline_volumes) if baseline_volumes else 0.0
    ratios = [volume / baseline for volume in recent_volumes] if baseline > 0 else [0.0 for _ in recent_volumes]
    high_volume_bars = sum(ratio >= 1.1 for ratio in ratios)
    low_volume_bars = sum(ratio <= 0.9 for ratio in ratios)
    persistence_ratio = mean(ratios) if ratios else 0.0
    return VolumePersistenceFeatureContext(
        recent_window=effective_window,
        persistence_ratio=round(persistence_ratio, 4),
        high_volume_bars=high_volume_bars,
        low_volume_bars=low_volume_bars,
        sustained_high_volume=high_volume_bars >= max(2, effective_window - 2),
        sustained_low_volume=low_volume_bars >= max(2, effective_window - 2),
    )


def _pullback_context(
    *,
    base_context: TimeframeFeatureContext,
    regime: RegimeFeatureContext,
    breakout: BreakoutFeatureContext,
    location: LocationFeatureContext,
    multi_timeframe: Mapping[str, TimeframeFeatureContext],
) -> PullbackContinuationFeatureContext:
    higher_contexts = [item for key, item in multi_timeframe.items() if key != base_context.timeframe]
    if not higher_contexts:
        return PullbackContinuationFeatureContext()

    avg_higher_trend = mean(item.trend_score for item in higher_contexts)
    positive_higher = sum(item.trend_score >= 0.2 for item in higher_contexts)
    negative_higher = sum(item.trend_score <= -0.2 for item in higher_contexts)
    if positive_higher == len(higher_contexts) and avg_higher_trend > 0:
        bias: Literal["bullish", "bearish", "range", "mixed", "unknown"] = "bullish"
    elif negative_higher == len(higher_contexts) and avg_higher_trend < 0:
        bias = "bearish"
    elif abs(avg_higher_trend) < 0.12:
        bias = "range"
    else:
        bias = "mixed"

    state: Literal[
        "bullish_continuation",
        "bearish_continuation",
        "bullish_pullback",
        "bearish_pullback",
        "countertrend",
        "range",
        "unclear",
    ] = "unclear"
    aligned = False
    if bias == "bullish":
        if breakout.range_breakout_direction == "up" or base_context.trend_score >= 0.18:
            state = "bullish_continuation"
            aligned = True
        elif base_context.trend_score > -0.12 and location.vwap_distance_pct <= 0.25:
            state = "bullish_pullback"
            aligned = True
        elif breakout.range_breakout_direction == "down" or base_context.trend_score < -0.18:
            state = "countertrend"
    elif bias == "bearish":
        if breakout.range_breakout_direction == "down" or base_context.trend_score <= -0.18:
            state = "bearish_continuation"
            aligned = True
        elif base_context.trend_score < 0.12 and location.vwap_distance_pct >= -0.25:
            state = "bearish_pullback"
            aligned = True
        elif breakout.range_breakout_direction == "up" or base_context.trend_score > 0.18:
            state = "countertrend"
    elif bias == "range" or regime.primary_regime == "range":
        state = "range"

    return PullbackContinuationFeatureContext(
        higher_timeframe_bias=bias,
        state=state,
        aligned_with_higher_timeframe=aligned,
    )


def _compute_timeframe_context(snapshot: MarketSnapshotPayload) -> TimeframeFeatureContext:
    closes = [candle.close for candle in snapshot.candles]
    highs = [candle.high for candle in snapshot.candles]
    lows = [candle.low for candle in snapshot.candles]
    volumes = [candle.volume for candle in snapshot.candles]
    returns = _returns(closes)
    sma_fast = _simple_moving_average(closes, 5)
    sma_slow = _simple_moving_average(closes, 20)
    volatility = (pstdev(returns) * sqrt(len(returns))) if len(returns) >= 2 else 0.0
    volume_ratio = _safe_volume_ratio(volumes)
    drawdown = (max(highs) - closes[-1]) / max(highs) if highs else 0.0
    trend_score = (((sma_fast - sma_slow) / sma_slow) * 100) if sma_slow else 0.0
    if len(closes) >= 5 and closes[-5] != 0:
        trend_score += ((closes[-1] - closes[-5]) / closes[-5]) * 100
    atr = _atr(highs, lows, closes)
    latest_price = max(closes[-1], 1.0)
    atr_pct = atr / latest_price
    momentum_score = 0.0
    if len(closes) >= 4 and closes[-4] != 0:
        momentum_score = ((closes[-1] - closes[-4]) / closes[-4]) * 100

    return TimeframeFeatureContext(
        timeframe=snapshot.timeframe,
        trend_score=round(trend_score, 4),
        volatility_pct=round(volatility * 100, 4),
        volume_ratio=round(volume_ratio, 4),
        drawdown_pct=round(drawdown * 100, 4),
        rsi=round(_rsi(closes), 4),
        atr=round(atr, 4),
        atr_pct=round(atr_pct * 100, 4),
        momentum_score=round(momentum_score, 4),
    )


def _build_regime_context(
    base_context: TimeframeFeatureContext,
    multi_timeframe: Mapping[str, TimeframeFeatureContext],
) -> RegimeFeatureContext:
    contexts = list(multi_timeframe.values())
    trend_scores = [item.trend_score for item in contexts]
    positive_trends = sum(score >= 0.35 for score in trend_scores)
    negative_trends = sum(score <= -0.35 for score in trend_scores)
    flat_trends = sum(abs(score) < 0.18 for score in trend_scores)

    if positive_trends >= 2 and base_context.trend_score >= 0.25:
        primary_regime: Literal["bullish", "bearish", "range", "transition"] = "bullish"
    elif negative_trends >= 2 and base_context.trend_score <= -0.25:
        primary_regime = "bearish"
    elif flat_trends >= 2 or (abs(base_context.trend_score) < 0.2 and 43.0 <= base_context.rsi <= 57.0):
        primary_regime = "range"
    else:
        primary_regime = "transition"

    if primary_regime == "range":
        trend_alignment: Literal["bullish_aligned", "bearish_aligned", "mixed", "range"] = "range"
    elif positive_trends >= 2 and negative_trends == 0:
        trend_alignment = "bullish_aligned"
    elif negative_trends >= 2 and positive_trends == 0:
        trend_alignment = "bearish_aligned"
    else:
        trend_alignment = "mixed"

    higher_timeframe_contexts = [item for key, item in multi_timeframe.items() if key != base_context.timeframe]
    baseline_atr_pct = mean([item.atr_pct for item in higher_timeframe_contexts]) if higher_timeframe_contexts else base_context.atr_pct
    if baseline_atr_pct <= 0:
        baseline_atr_pct = base_context.atr_pct
    if base_context.atr_pct >= baseline_atr_pct * 1.3:
        volatility_regime: Literal["compressed", "normal", "expanded"] = "expanded"
    elif base_context.atr_pct <= baseline_atr_pct * 0.75:
        volatility_regime = "compressed"
    else:
        volatility_regime = "normal"

    if base_context.volume_ratio < 0.75:
        volume_regime: Literal["weak", "normal", "strong"] = "weak"
    elif base_context.volume_ratio > 1.25:
        volume_regime = "strong"
    else:
        volume_regime = "normal"

    bullish_overextended = primary_regime == "bullish" and base_context.rsi >= 72.0
    bearish_overextended = primary_regime == "bearish" and base_context.rsi <= 28.0
    overextended = bullish_overextended or bearish_overextended
    momentum_weakening = (
        (primary_regime == "bullish" and base_context.momentum_score < 0.12)
        or (primary_regime == "bearish" and base_context.momentum_score > -0.12)
        or (trend_alignment == "mixed" and abs(base_context.momentum_score) < 0.1)
        or volume_regime == "weak"
    )
    if overextended:
        momentum_state: Literal["strengthening", "stable", "weakening", "overextended"] = "overextended"
    elif momentum_weakening:
        momentum_state = "weakening"
    elif abs(base_context.momentum_score) > max(abs(base_context.trend_score) * 0.5, 0.25):
        momentum_state = "strengthening"
    else:
        momentum_state = "stable"

    return RegimeFeatureContext(
        primary_regime=primary_regime,
        trend_alignment=trend_alignment,
        volatility_regime=volatility_regime,
        volume_regime=volume_regime,
        momentum_state=momentum_state,
        weak_volume=volume_regime == "weak",
        momentum_weakening=momentum_weakening,
    )


def compute_features(
    snapshot: MarketSnapshotPayload,
    context_snapshots: Mapping[str, MarketSnapshotPayload] | None = None,
) -> FeaturePayload:
    multi_timeframe: dict[str, TimeframeFeatureContext] = {
        snapshot.timeframe: _compute_timeframe_context(snapshot),
    }
    for timeframe, context_snapshot in (context_snapshots or {}).items():
        if timeframe == snapshot.timeframe:
            continue
        multi_timeframe[timeframe] = _compute_timeframe_context(context_snapshot)

    base_context = multi_timeframe[snapshot.timeframe]
    regime = _build_regime_context(base_context, multi_timeframe)
    breakout = _breakout_context(snapshot)
    candle_structure = _candle_structure_context(snapshot)
    location = _location_context(snapshot, breakout)
    volume_persistence = _volume_persistence_context(snapshot)
    pullback_context = _pullback_context(
        base_context=base_context,
        regime=regime,
        breakout=breakout,
        location=location,
        multi_timeframe=multi_timeframe,
    )

    flags: list[str] = []
    if snapshot.is_stale:
        flags.append("STALE_MARKET_DATA")
    if not snapshot.is_complete:
        flags.append("INCOMPLETE_MARKET_DATA")
    if regime.weak_volume:
        flags.append("WEAK_VOLUME")
    if regime.volatility_regime == "expanded":
        flags.append("VOLATILITY_EXPANDED")
    if regime.momentum_weakening:
        flags.append("MOMENTUM_WEAKENING")
    if len(snapshot.candles) < 9:
        flags.append("SWING_CONTEXT_PARTIAL")
    if len(snapshot.candles) < 5:
        flags.append("VOLUME_PERSISTENCE_PARTIAL")
    if _vwap(snapshot.candles) is None:
        flags.append("VWAP_CONTEXT_PARTIAL")
    if len(multi_timeframe) < 2:
        flags.append("PULLBACK_CONTEXT_PARTIAL")
    for timeframe, context_snapshot in (context_snapshots or {}).items():
        if context_snapshot.is_stale:
            flags.append(f"{timeframe.upper()}_STALE")
        if not context_snapshot.is_complete:
            flags.append(f"{timeframe.upper()}_INCOMPLETE")

    return FeaturePayload(
        symbol=snapshot.symbol,
        timeframe=snapshot.timeframe,
        trend_score=base_context.trend_score,
        volatility_pct=base_context.volatility_pct,
        volume_ratio=base_context.volume_ratio,
        drawdown_pct=base_context.drawdown_pct,
        rsi=base_context.rsi,
        atr=base_context.atr,
        atr_pct=base_context.atr_pct,
        momentum_score=base_context.momentum_score,
        multi_timeframe=multi_timeframe,
        regime=regime,
        breakout=breakout,
        candle_structure=candle_structure,
        location=location,
        volume_persistence=volume_persistence,
        pullback_context=pullback_context,
        data_quality_flags=flags,
    )


def persist_feature_snapshot(
    session: Session, market_snapshot_id: int, snapshot: MarketSnapshotPayload, features: FeaturePayload
) -> FeatureSnapshot:
    row = FeatureSnapshot(
        symbol=snapshot.symbol,
        timeframe=snapshot.timeframe,
        market_snapshot_id=market_snapshot_id,
        feature_time=snapshot.snapshot_time,
        trend_score=features.trend_score,
        volatility_pct=features.volatility_pct,
        volume_ratio=features.volume_ratio,
        drawdown_pct=features.drawdown_pct,
        rsi=features.rsi,
        atr=features.atr,
        payload=features.model_dump(mode="json"),
    )
    session.add(row)
    session.flush()
    return row
