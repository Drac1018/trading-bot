from __future__ import annotations

from collections.abc import Mapping
from math import sqrt
from statistics import mean, pstdev
from typing import Literal

from sqlalchemy.orm import Session

from trading_mvp.models import FeatureSnapshot
from trading_mvp.schemas import (
    FeaturePayload,
    MarketSnapshotPayload,
    RegimeFeatureContext,
    TimeframeFeatureContext,
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
