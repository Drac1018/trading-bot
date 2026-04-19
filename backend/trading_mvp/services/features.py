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
    DerivativesFeatureContext,
    FeaturePayload,
    LeadLagFeatureContext,
    LeadMarketReferencePayload,
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


def _ratio_to_bias(ratio: float | None) -> float | None:
    if ratio is None or ratio <= 0:
        return None
    return max(-1.0, min(1.0, (ratio - 1.0) / (ratio + 1.0)))


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


def _clamp_score(value: float, *, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _derivatives_context(
    snapshot: MarketSnapshotPayload,
    *,
    base_context: TimeframeFeatureContext,
    breakout: BreakoutFeatureContext,
) -> DerivativesFeatureContext:
    raw = snapshot.derivatives_context
    available = any(
        value is not None
        for value in (
            raw.open_interest,
            raw.open_interest_change_pct,
            raw.funding_rate,
            raw.taker_buy_sell_imbalance,
            raw.perp_basis_bps,
            raw.crowding_bias,
            raw.top_trader_long_short_ratio,
            raw.spread_bps,
            raw.spread_stress_score,
        )
    )
    if not available:
        return DerivativesFeatureContext(
            available=False,
            source=raw.source,
            fallback_used=raw.fallback_used,
            fetch_failed=raw.fetch_failed,
        )

    taker_flow_alignment: Literal["bullish", "bearish", "neutral", "unknown"] = "unknown"
    if raw.taker_buy_sell_imbalance is not None:
        if raw.taker_buy_sell_imbalance >= 0.12:
            taker_flow_alignment = "bullish"
        elif raw.taker_buy_sell_imbalance <= -0.12:
            taker_flow_alignment = "bearish"
        else:
            taker_flow_alignment = "neutral"

    funding_bias: Literal["long_headwind", "short_headwind", "neutral", "unknown"] = "unknown"
    if raw.funding_rate is not None:
        if raw.funding_rate >= 0.0005:
            funding_bias = "long_headwind"
        elif raw.funding_rate <= -0.0005:
            funding_bias = "short_headwind"
        else:
            funding_bias = "neutral"

    basis_bias: Literal["bullish", "bearish", "neutral", "unknown"] = "unknown"
    if raw.perp_basis_bps is not None:
        if raw.perp_basis_bps >= 4.0:
            basis_bias = "bullish"
        elif raw.perp_basis_bps <= -4.0:
            basis_bias = "bearish"
        else:
            basis_bias = "neutral"

    top_trader_crowding_bias = _ratio_to_bias(raw.top_trader_long_short_ratio)
    top_trader_long_crowded = bool(top_trader_crowding_bias is not None and top_trader_crowding_bias >= 0.16)
    top_trader_short_crowded = bool(top_trader_crowding_bias is not None and top_trader_crowding_bias <= -0.16)

    oi_expanding_with_price = (
        raw.open_interest_change_pct is not None
        and raw.open_interest_change_pct >= 0.8
        and (
            (base_context.trend_score > 0.15 and taker_flow_alignment == "bullish")
            or (base_context.trend_score < -0.15 and taker_flow_alignment == "bearish")
            or (breakout.range_breakout_direction == "up" and taker_flow_alignment == "bullish")
            or (breakout.range_breakout_direction == "down" and taker_flow_alignment == "bearish")
        )
    )
    oi_falling_on_breakout = (
        breakout.range_breakout_direction != "none"
        and raw.open_interest_change_pct is not None
        and raw.open_interest_change_pct <= -0.5
    )
    crowded_long_risk = bool(
        (raw.crowding_bias is not None and raw.crowding_bias >= 0.22)
        or (raw.funding_rate is not None and raw.funding_rate >= 0.0008)
        or top_trader_long_crowded
        or (
            raw.crowding_bias is not None
            and raw.crowding_bias >= 0.12
            and raw.perp_basis_bps is not None
            and raw.perp_basis_bps >= 7.0
        )
    )
    crowded_short_risk = bool(
        (raw.crowding_bias is not None and raw.crowding_bias <= -0.22)
        or (raw.funding_rate is not None and raw.funding_rate <= -0.0008)
        or top_trader_short_crowded
        or (
            raw.crowding_bias is not None
            and raw.crowding_bias <= -0.12
            and raw.perp_basis_bps is not None
            and raw.perp_basis_bps <= -7.0
        )
    )
    spread_stress_score = raw.spread_stress_score
    if spread_stress_score is None and raw.spread_bps is not None:
        spread_stress_score = raw.spread_bps / 4.0
    spread_stress = bool(
        spread_stress_score is not None
        and (
            spread_stress_score >= 1.45
            or (breakout.range_breakout_direction != "none" and spread_stress_score >= 1.15)
        )
    )
    spread_headwind = bool(
        spread_stress
        or (
            raw.spread_bps is not None
            and (
                raw.spread_bps >= 7.0
                or (breakout.range_breakout_direction != "none" and raw.spread_bps >= 4.5)
                or (abs(base_context.momentum_score) < 0.18 and raw.spread_bps >= 5.5)
            )
        )
    )
    breakout_spread_headwind = bool(
        breakout.range_breakout_direction != "none"
        and (
            spread_stress
            or (raw.spread_bps is not None and raw.spread_bps >= 4.5)
        )
    )

    long_alignment_score = 0.5
    short_alignment_score = 0.5
    if taker_flow_alignment == "bullish":
        long_alignment_score += 0.16
        short_alignment_score -= 0.16
    elif taker_flow_alignment == "bearish":
        long_alignment_score -= 0.16
        short_alignment_score += 0.16
    if basis_bias == "bullish":
        long_alignment_score += 0.08
        short_alignment_score -= 0.08
    elif basis_bias == "bearish":
        long_alignment_score -= 0.08
        short_alignment_score += 0.08
    if oi_expanding_with_price:
        if taker_flow_alignment == "bullish":
            long_alignment_score += 0.12
        elif taker_flow_alignment == "bearish":
            short_alignment_score += 0.12
    if oi_falling_on_breakout:
        long_alignment_score -= 0.12
        short_alignment_score -= 0.12
    if funding_bias == "long_headwind":
        long_alignment_score -= 0.12
        short_alignment_score += 0.05
    elif funding_bias == "short_headwind":
        long_alignment_score += 0.05
        short_alignment_score -= 0.12
    if crowded_long_risk:
        long_alignment_score -= 0.18
    if crowded_short_risk:
        short_alignment_score -= 0.18
    if top_trader_long_crowded:
        long_alignment_score -= 0.1
        short_alignment_score += 0.04
    if top_trader_short_crowded:
        short_alignment_score -= 0.1
        long_alignment_score += 0.04
    if spread_stress:
        long_alignment_score -= 0.12
        short_alignment_score -= 0.12
    if spread_headwind:
        long_alignment_score -= 0.14
        short_alignment_score -= 0.14
    if breakout_spread_headwind and not oi_expanding_with_price:
        long_alignment_score -= 0.08
        short_alignment_score -= 0.08

    entry_veto_reason_codes: list[str] = []
    if top_trader_long_crowded:
        entry_veto_reason_codes.append("TOP_TRADER_LONG_CROWDED")
    if top_trader_short_crowded:
        entry_veto_reason_codes.append("TOP_TRADER_SHORT_CROWDED")
    if funding_bias == "long_headwind":
        entry_veto_reason_codes.append("FUNDING_LONG_HEADWIND")
    elif funding_bias == "short_headwind":
        entry_veto_reason_codes.append("FUNDING_SHORT_HEADWIND")
    if spread_headwind:
        entry_veto_reason_codes.append("SPREAD_HEADWIND")
    if spread_stress:
        entry_veto_reason_codes.append("SPREAD_STRESS")

    breakout_veto_reason_codes: list[str] = []
    if breakout.range_breakout_direction != "none" and not oi_expanding_with_price:
        breakout_veto_reason_codes.append("BREAKOUT_OI_NOT_EXPANDING")
    if breakout_spread_headwind:
        breakout_veto_reason_codes.append("BREAKOUT_SPREAD_STRESS")
    if breakout.range_breakout_direction == "up" and taker_flow_alignment != "bullish":
        breakout_veto_reason_codes.append("BREAKOUT_TAKER_FLOW_NOT_ALIGNED")
    if breakout.range_breakout_direction == "down" and taker_flow_alignment != "bearish":
        breakout_veto_reason_codes.append("BREAKOUT_TAKER_FLOW_NOT_ALIGNED")

    long_discount_magnitude = 0.0
    short_discount_magnitude = 0.0
    if crowded_long_risk:
        long_discount_magnitude += 0.06
    if crowded_short_risk:
        short_discount_magnitude += 0.06
    if top_trader_long_crowded:
        long_discount_magnitude += 0.05
    if top_trader_short_crowded:
        short_discount_magnitude += 0.05
    if funding_bias == "long_headwind":
        long_discount_magnitude += 0.04
    elif funding_bias == "short_headwind":
        short_discount_magnitude += 0.04
    if taker_flow_alignment == "bearish":
        long_discount_magnitude += 0.05
    elif taker_flow_alignment == "bullish":
        short_discount_magnitude += 0.05
    if spread_headwind:
        long_discount_magnitude += 0.04
        short_discount_magnitude += 0.04
    if spread_stress:
        long_discount_magnitude += 0.05
        short_discount_magnitude += 0.05
    if breakout_spread_headwind and not oi_expanding_with_price:
        long_discount_magnitude += 0.06
        short_discount_magnitude += 0.06

    return DerivativesFeatureContext(
        available=available,
        source=raw.source,
        fallback_used=raw.fallback_used,
        fetch_failed=raw.fetch_failed,
        open_interest=raw.open_interest,
        open_interest_change_pct=raw.open_interest_change_pct,
        funding_rate=raw.funding_rate,
        taker_buy_sell_imbalance=raw.taker_buy_sell_imbalance,
        perp_basis_bps=raw.perp_basis_bps,
        crowding_bias=raw.crowding_bias,
        top_trader_long_short_ratio=raw.top_trader_long_short_ratio,
        top_trader_crowding_bias=top_trader_crowding_bias,
        best_bid=raw.best_bid,
        best_ask=raw.best_ask,
        spread_bps=raw.spread_bps,
        spread_stress_score=spread_stress_score,
        oi_expanding_with_price=oi_expanding_with_price,
        oi_falling_on_breakout=oi_falling_on_breakout,
        crowded_long_risk=crowded_long_risk,
        crowded_short_risk=crowded_short_risk,
        spread_headwind=spread_headwind,
        breakout_spread_headwind=breakout_spread_headwind,
        spread_stress=spread_stress,
        top_trader_long_crowded=top_trader_long_crowded,
        top_trader_short_crowded=top_trader_short_crowded,
        taker_flow_alignment=taker_flow_alignment,
        funding_bias=funding_bias,
        basis_bias=basis_bias,
        entry_veto_reason_codes=entry_veto_reason_codes,
        breakout_veto_reason_codes=breakout_veto_reason_codes,
        long_discount_magnitude=round(min(long_discount_magnitude, 0.35), 4),
        short_discount_magnitude=round(min(short_discount_magnitude, 0.35), 4),
        long_alignment_score=round(_clamp_score(long_alignment_score), 4),
        short_alignment_score=round(_clamp_score(short_alignment_score), 4),
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


def _lead_lag_context(
    *,
    symbol: str,
    timeframe: str,
    base_context: TimeframeFeatureContext,
    regime: RegimeFeatureContext,
    breakout: BreakoutFeatureContext,
    pullback_context: PullbackContinuationFeatureContext,
    lead_market_features: Mapping[str, FeaturePayload] | None,
) -> LeadLagFeatureContext:
    if not lead_market_features:
        return LeadLagFeatureContext()

    current_breakout_up = breakout.broke_swing_high or breakout.range_breakout_direction == "up"
    current_breakout_down = breakout.broke_swing_low or breakout.range_breakout_direction == "down"
    current_pullback_long = pullback_context.state == "bullish_pullback"
    current_pullback_short = pullback_context.state == "bearish_pullback"
    current_continuation_long = pullback_context.state == "bullish_continuation"
    current_continuation_short = pullback_context.state == "bearish_continuation"

    references: dict[str, LeadMarketReferencePayload] = {}
    bullish_reference_count = 0
    bearish_reference_count = 0
    bullish_scores: list[float] = []
    bearish_scores: list[float] = []
    bullish_breakout_confirmed = False
    bearish_breakout_confirmed = False
    bullish_pullback_supported = False
    bearish_pullback_supported = False
    bullish_continuation_supported = False
    bearish_continuation_supported = False
    reference_symbols: list[str] = []

    for lead_symbol, lead_features in lead_market_features.items():
        lead_symbol_key = str(lead_symbol or "").upper()
        if not lead_symbol_key or lead_symbol_key == symbol.upper():
            continue

        reference_symbols.append(lead_symbol_key)
        references[lead_symbol_key] = LeadMarketReferencePayload(
            symbol=lead_symbol_key,
            timeframe=timeframe,
            trend_score=round(float(lead_features.trend_score), 6),
            momentum_score=round(float(lead_features.momentum_score), 6),
            breakout_direction=lead_features.breakout.range_breakout_direction,
            pullback_state=lead_features.pullback_context.state,
            primary_regime=lead_features.regime.primary_regime,
            trend_alignment=lead_features.regime.trend_alignment,
            weak_volume=lead_features.regime.weak_volume,
            momentum_state=lead_features.regime.momentum_state,
            volume_ratio=round(float(lead_features.volume_ratio), 6),
        )

        bullish_reference = (
            lead_features.regime.trend_alignment == "bullish_aligned"
            and lead_features.regime.primary_regime != "range"
            and lead_features.trend_score >= 0.18
            and lead_features.momentum_score >= 0.05
            and not lead_features.regime.weak_volume
        )
        bearish_reference = (
            lead_features.regime.trend_alignment == "bearish_aligned"
            and lead_features.regime.primary_regime != "range"
            and lead_features.trend_score <= -0.18
            and lead_features.momentum_score <= -0.05
            and not lead_features.regime.weak_volume
        )
        bullish_breakout = bullish_reference and (
            lead_features.breakout.broke_swing_high or lead_features.breakout.range_breakout_direction == "up"
        )
        bearish_breakout = bearish_reference and (
            lead_features.breakout.broke_swing_low or lead_features.breakout.range_breakout_direction == "down"
        )
        bullish_pullback_support = bullish_reference and lead_features.pullback_context.state in {
            "bullish_pullback",
            "bullish_continuation",
        }
        bearish_pullback_support = bearish_reference and lead_features.pullback_context.state in {
            "bearish_pullback",
            "bearish_continuation",
        }
        bullish_continuation_support = bullish_reference and (
            lead_features.pullback_context.state == "bullish_continuation"
            or lead_features.regime.momentum_state in {"strengthening", "stable"}
        )
        bearish_continuation_support = bearish_reference and (
            lead_features.pullback_context.state == "bearish_continuation"
            or lead_features.regime.momentum_state in {"strengthening", "stable"}
        )

        if bullish_reference:
            bullish_reference_count += 1
        if bearish_reference:
            bearish_reference_count += 1
        bullish_breakout_confirmed = bullish_breakout_confirmed or (current_breakout_up and bullish_breakout)
        bearish_breakout_confirmed = bearish_breakout_confirmed or (current_breakout_down and bearish_breakout)
        bullish_pullback_supported = bullish_pullback_supported or (current_pullback_long and bullish_pullback_support)
        bearish_pullback_supported = bearish_pullback_supported or (current_pullback_short and bearish_pullback_support)
        bullish_continuation_supported = bullish_continuation_supported or (
            current_continuation_long and bullish_continuation_support
        )
        bearish_continuation_supported = bearish_continuation_supported or (
            current_continuation_short and bearish_continuation_support
        )

        bullish_score = 0.5
        bearish_score = 0.5
        if bullish_reference:
            bullish_score += 0.18
        if bearish_reference:
            bearish_score += 0.18
        if lead_features.regime.momentum_state in {"strengthening", "stable"}:
            if lead_features.momentum_score > 0.05:
                bullish_score += 0.08
            elif lead_features.momentum_score < -0.05:
                bearish_score += 0.08
        if not lead_features.regime.weak_volume:
            if bullish_reference:
                bullish_score += 0.08
            if bearish_reference:
                bearish_score += 0.08
        if current_breakout_up and bullish_breakout:
            bullish_score += 0.12
        elif current_breakout_up and not bullish_breakout:
            bullish_score -= 0.08
        if current_breakout_down and bearish_breakout:
            bearish_score += 0.12
        elif current_breakout_down and not bearish_breakout:
            bearish_score -= 0.08
        if current_pullback_long and bullish_pullback_support:
            bullish_score += 0.1
        if current_continuation_long and bullish_continuation_support:
            bullish_score += 0.08
        if current_pullback_short and bearish_pullback_support:
            bearish_score += 0.1
        if current_continuation_short and bearish_continuation_support:
            bearish_score += 0.08
        if bearish_reference and regime.trend_alignment == "bullish_aligned":
            bullish_score -= 0.14
        if bullish_reference and regime.trend_alignment == "bearish_aligned":
            bearish_score -= 0.14

        bullish_scores.append(_clamp_score(bullish_score))
        bearish_scores.append(_clamp_score(bearish_score))

    if not reference_symbols:
        return LeadLagFeatureContext(
            available=False,
            missing_reference_symbols=["BTCUSDT", "ETHUSDT"],
        )

    if bullish_reference_count > bearish_reference_count:
        leader_bias: Literal["bullish", "bearish", "mixed", "neutral", "unknown"] = "bullish"
    elif bearish_reference_count > bullish_reference_count:
        leader_bias = "bearish"
    elif bullish_reference_count == 0 and bearish_reference_count == 0:
        leader_bias = "neutral"
    else:
        leader_bias = "mixed"

    bullish_alignment_score = mean(bullish_scores) if bullish_scores else 0.5
    bearish_alignment_score = mean(bearish_scores) if bearish_scores else 0.5
    bullish_breakout_ahead = bool(current_breakout_up and not bullish_breakout_confirmed)
    bearish_breakout_ahead = bool(current_breakout_down and not bearish_breakout_confirmed)
    strong_reference_confirmation = any(
        (
            bullish_breakout_confirmed,
            bearish_breakout_confirmed,
            bullish_pullback_supported,
            bearish_pullback_supported,
            bullish_continuation_supported,
            bearish_continuation_supported,
        )
    )
    weak_reference_confirmation = (
        not strong_reference_confirmation
        and bool(reference_symbols)
        and (bullish_reference_count > 0 or bearish_reference_count > 0)
    )
    missing_reference_symbols = [
        lead_symbol
        for lead_symbol in ("BTCUSDT", "ETHUSDT")
        if lead_symbol != symbol.upper() and lead_symbol not in reference_symbols
    ]

    return LeadLagFeatureContext(
        available=True,
        leader_bias=leader_bias,
        reference_symbols=reference_symbols,
        missing_reference_symbols=missing_reference_symbols,
        bullish_alignment_score=round(_clamp_score(bullish_alignment_score), 6),
        bearish_alignment_score=round(_clamp_score(bearish_alignment_score), 6),
        bullish_breakout_confirmed=bullish_breakout_confirmed,
        bearish_breakout_confirmed=bearish_breakout_confirmed,
        bullish_breakout_ahead=bullish_breakout_ahead,
        bearish_breakout_ahead=bearish_breakout_ahead,
        bullish_pullback_supported=bullish_pullback_supported,
        bearish_pullback_supported=bearish_pullback_supported,
        bullish_continuation_supported=bullish_continuation_supported,
        bearish_continuation_supported=bearish_continuation_supported,
        strong_reference_confirmation=strong_reference_confirmation,
        weak_reference_confirmation=weak_reference_confirmation,
        references=references,
    )


def summarize_universe_breadth(
    items: list[FeaturePayload | Mapping[str, object] | object],
    *,
    decisions: Mapping[str, str] | None = None,
) -> dict[str, object]:
    total = 0
    bullish_aligned_count = 0
    bearish_aligned_count = 0
    weak_volume_count = 0
    transition_count = 0
    entry_candidates = 0

    for item in items:
        if item is None:
            continue
        symbol = ""
        regime_source: object | None = None
        if isinstance(item, FeaturePayload):
            symbol = item.symbol.upper()
            regime_source = item.regime
        elif isinstance(item, Mapping):
            symbol = str(item.get("symbol") or "").upper()
            regime_source = item
        else:
            continue

        if regime_source is None:
            continue

        total += 1
        if isinstance(regime_source, Mapping):
            trend_alignment = str(regime_source.get("trend_alignment") or "unknown")
            primary_regime = str(regime_source.get("primary_regime") or "unknown")
            weak_volume = bool(regime_source.get("weak_volume", False))
            momentum_weakening = bool(regime_source.get("momentum_weakening", False))
        else:
            trend_alignment = str(getattr(regime_source, "trend_alignment", "") or "unknown")
            primary_regime = str(getattr(regime_source, "primary_regime", "") or "unknown")
            weak_volume = bool(getattr(regime_source, "weak_volume", False))
            momentum_weakening = bool(getattr(regime_source, "momentum_weakening", False))

        inferred_entry_candidate = (
            trend_alignment in {"bullish_aligned", "bearish_aligned"}
            and not weak_volume
            and primary_regime not in {"range", "transition"}
            and not momentum_weakening
        )
        if decisions is not None:
            if str(decisions.get(symbol, "") or "") in {"long", "short"}:
                entry_candidates += 1
        elif inferred_entry_candidate:
            entry_candidates += 1
        if trend_alignment == "bullish_aligned":
            bullish_aligned_count += 1
        elif trend_alignment == "bearish_aligned":
            bearish_aligned_count += 1
        if weak_volume:
            weak_volume_count += 1
        if primary_regime in {"range", "transition"} or momentum_weakening:
            transition_count += 1

    denominator = max(total, 1)
    dominant_alignment_count = max(bullish_aligned_count, bearish_aligned_count)
    weak_volume_ratio = weak_volume_count / denominator
    transition_ratio = transition_count / denominator
    bullish_alignment_ratio = bullish_aligned_count / denominator
    bearish_alignment_ratio = bearish_aligned_count / denominator
    dominant_alignment_ratio = dominant_alignment_count / denominator

    if weak_volume_ratio >= 0.5 or entry_candidates <= 1:
        breadth_regime = "weak_breadth"
        entry_score_multiplier = 0.82
        hold_bias_multiplier = 1.18
    elif dominant_alignment_ratio >= 0.6 and weak_volume_ratio <= 0.34 and transition_ratio <= 0.34:
        breadth_regime = "trend_expansion"
        entry_score_multiplier = 1.04
        hold_bias_multiplier = 0.95
    elif transition_ratio >= 0.4:
        breadth_regime = "transition_fragile"
        entry_score_multiplier = 0.9
        hold_bias_multiplier = 1.08
    else:
        breadth_regime = "mixed"
        entry_score_multiplier = 0.96
        hold_bias_multiplier = 1.0

    if bullish_aligned_count > bearish_aligned_count:
        directional_bias = "bullish"
    elif bearish_aligned_count > bullish_aligned_count:
        directional_bias = "bearish"
    else:
        directional_bias = "balanced"

    return {
        "breadth_regime": breadth_regime,
        "directional_bias": directional_bias,
        "tracked_symbols": total,
        "entry_candidates": entry_candidates,
        "bullish_aligned_count": bullish_aligned_count,
        "bearish_aligned_count": bearish_aligned_count,
        "weak_volume_count": weak_volume_count,
        "transition_count": transition_count,
        "bullish_alignment_ratio": round(bullish_alignment_ratio, 4),
        "bearish_alignment_ratio": round(bearish_alignment_ratio, 4),
        "weak_volume_ratio": round(weak_volume_ratio, 4),
        "transition_ratio": round(transition_ratio, 4),
        "entry_score_multiplier": round(entry_score_multiplier, 4),
        "hold_bias_multiplier": round(hold_bias_multiplier, 4),
    }


def compute_features(
    snapshot: MarketSnapshotPayload,
    context_snapshots: Mapping[str, MarketSnapshotPayload] | None = None,
    lead_market_features: Mapping[str, FeaturePayload] | None = None,
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
    derivatives = _derivatives_context(
        snapshot,
        base_context=base_context,
        breakout=breakout,
    )
    lead_lag = _lead_lag_context(
        symbol=snapshot.symbol,
        timeframe=snapshot.timeframe,
        base_context=base_context,
        regime=regime,
        breakout=breakout,
        pullback_context=pullback_context,
        lead_market_features=lead_market_features,
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
    if not derivatives.available:
        flags.append("DERIVATIVES_CONTEXT_UNAVAILABLE")
    if not lead_lag.available:
        flags.append("LEAD_LAG_CONTEXT_UNAVAILABLE")
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
        derivatives=derivatives,
        lead_lag=lead_lag,
        event_context=snapshot.event_context.model_copy(deep=True),
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
