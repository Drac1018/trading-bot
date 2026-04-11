from __future__ import annotations

from math import sqrt
from statistics import mean, pstdev

from sqlalchemy.orm import Session

from trading_mvp.models import FeatureSnapshot
from trading_mvp.schemas import FeaturePayload, MarketSnapshotPayload


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


def compute_features(snapshot: MarketSnapshotPayload) -> FeaturePayload:
    closes = [candle.close for candle in snapshot.candles]
    highs = [candle.high for candle in snapshot.candles]
    lows = [candle.low for candle in snapshot.candles]
    volumes = [candle.volume for candle in snapshot.candles]
    returns = _returns(closes)
    sma_fast = _simple_moving_average(closes, 5)
    sma_slow = _simple_moving_average(closes, 20)
    volatility = (pstdev(returns) * sqrt(len(returns))) if len(returns) >= 2 else 0.0
    volume_ratio = volumes[-1] / mean(volumes[-10:] if len(volumes) >= 10 else volumes)
    drawdown = (max(highs) - closes[-1]) / max(highs) if highs else 0.0
    trend_score = (((sma_fast - sma_slow) / sma_slow) * 100) if sma_slow else 0.0
    if len(closes) >= 5 and closes[-5] != 0:
        trend_score += ((closes[-1] - closes[-5]) / closes[-5]) * 100

    flags: list[str] = []
    if snapshot.is_stale:
        flags.append("STALE_MARKET_DATA")
    if not snapshot.is_complete:
        flags.append("INCOMPLETE_MARKET_DATA")
    if volume_ratio < 0.6:
        flags.append("WEAK_VOLUME")

    return FeaturePayload(
        symbol=snapshot.symbol,
        timeframe=snapshot.timeframe,
        trend_score=round(trend_score, 4),
        volatility_pct=round(volatility * 100, 4),
        volume_ratio=round(volume_ratio, 4),
        drawdown_pct=round(drawdown * 100, 4),
        rsi=round(_rsi(closes), 4),
        atr=round(_atr(highs, lows, closes), 4),
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
