from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import sin

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from trading_mvp.models import MarketSnapshot
from trading_mvp.schemas import MarketCandle, MarketSnapshotPayload
from trading_mvp.services.binance import BinanceClient
from trading_mvp.time_utils import utcnow_naive


def timeframe_to_minutes(timeframe: str) -> int:
    if timeframe.endswith("m"):
        return int(timeframe[:-1])
    if timeframe.endswith("h"):
        return int(timeframe[:-1]) * 60
    raise ValueError(f"Unsupported timeframe: {timeframe}")


def generate_seed_candles(symbol: str, timeframe: str, points: int = 160) -> list[MarketCandle]:
    interval_minutes = timeframe_to_minutes(timeframe)
    presets = {
        "BTCUSDT": (64000.0, 18.0, 420.0, 1500.0),
        "ETHUSDT": (3200.0, 1.6, 28.0, 9000.0),
        "SOLUSDT": (145.0, 0.15, 4.8, 42000.0),
        "XRPUSDT": (0.62, 0.0012, 0.025, 180000.0),
        "BNBUSDT": (580.0, 0.55, 12.0, 12000.0),
    }
    base_price, drift, volatility, base_volume = presets.get(symbol, (100.0, 0.08, 3.5, 25000.0))
    start = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=interval_minutes * points)

    candles: list[MarketCandle] = []
    previous_close = base_price
    for index in range(points):
        timestamp = start + timedelta(minutes=interval_minutes * index)
        wave = sin(index / 3.7) * volatility
        retrace = sin(index / 11.0) * (volatility * 0.45)
        close = max(1.0, base_price + (drift * index) + wave - retrace)
        open_price = previous_close
        high = max(open_price, close) * 1.0035
        low = min(open_price, close) * 0.9965
        volume = base_volume + ((index % 12) * 115.0) + abs(sin(index / 2.5)) * 500.0
        candles.append(
            MarketCandle(
                timestamp=timestamp.replace(tzinfo=None),
                open=round(open_price, 2),
                high=round(high, 2),
                low=round(low, 2),
                close=round(close, 2),
                volume=round(volume, 2),
            )
        )
        previous_close = close
    return candles


def _build_seed_snapshot(
    symbol: str,
    timeframe: str,
    lookback: int,
    upto_index: int | None,
    force_stale: bool,
) -> MarketSnapshotPayload:
    series = generate_seed_candles(symbol=symbol, timeframe=timeframe)
    if upto_index is None:
        upto_index = len(series) - 1
    upto_index = max(lookback - 1, min(upto_index, len(series) - 1))
    candles = series[max(0, upto_index - lookback + 1) : upto_index + 1]
    latest = candles[-1]
    snapshot_time = latest.timestamp
    if not force_stale:
        snapshot_time = utcnow_naive()
        delta = snapshot_time - latest.timestamp
        candles = [
            candle.model_copy(update={"timestamp": candle.timestamp + delta})  # type: ignore[operator]
            for candle in candles
        ]
        latest = candles[-1]
    return MarketSnapshotPayload(
        symbol=symbol,
        timeframe=timeframe,
        snapshot_time=snapshot_time,
        latest_price=latest.close,
        latest_volume=latest.volume,
        candle_count=len(candles),
        is_stale=force_stale,
        is_complete=len(candles) >= min(lookback, 20),
        candles=candles,
    )


def _build_binance_snapshot(
    symbol: str,
    timeframe: str,
    lookback: int,
    *,
    testnet_enabled: bool,
    stale_threshold_seconds: int,
) -> MarketSnapshotPayload:
    client = BinanceClient(testnet_enabled=testnet_enabled, futures_enabled=True)
    candles = client.fetch_klines(symbol=symbol, interval=timeframe, limit=lookback)
    latest = candles[-1]
    snapshot_time = utcnow_naive()
    staleness_seconds = (snapshot_time - latest.timestamp).total_seconds()
    return MarketSnapshotPayload(
        symbol=symbol,
        timeframe=timeframe,
        snapshot_time=snapshot_time,
        latest_price=latest.close,
        latest_volume=latest.volume,
        candle_count=len(candles),
        is_stale=staleness_seconds > stale_threshold_seconds,
        is_complete=len(candles) >= min(lookback, 20),
        candles=candles,
    )


def build_market_snapshot(
    symbol: str,
    timeframe: str,
    lookback: int = 60,
    upto_index: int | None = None,
    force_stale: bool = False,
    *,
    use_binance: bool = False,
    binance_testnet_enabled: bool = False,
    stale_threshold_seconds: int = 1800,
) -> MarketSnapshotPayload:
    if upto_index is not None or force_stale:
        return _build_seed_snapshot(symbol, timeframe, lookback, upto_index, force_stale)

    if not use_binance:
        raise RuntimeError("실거래 모드에서는 Binance 실데이터가 꺼져 있으면 시장 스냅샷을 만들 수 없습니다.")

    return _build_binance_snapshot(
        symbol=symbol,
        timeframe=timeframe,
        lookback=lookback,
        testnet_enabled=binance_testnet_enabled,
        stale_threshold_seconds=stale_threshold_seconds,
    )


def persist_market_snapshot(session: Session, snapshot: MarketSnapshotPayload) -> MarketSnapshot:
    row = MarketSnapshot(
        symbol=snapshot.symbol,
        timeframe=snapshot.timeframe,
        snapshot_time=snapshot.snapshot_time,
        latest_price=snapshot.latest_price,
        latest_volume=snapshot.latest_volume,
        candle_count=snapshot.candle_count,
        is_stale=snapshot.is_stale,
        is_complete=snapshot.is_complete,
        payload=snapshot.model_dump(mode="json"),
    )
    session.add(row)
    session.flush()
    return row


def get_latest_snapshots(session: Session, limit: int = 20) -> list[MarketSnapshot]:
    statement = select(MarketSnapshot).order_by(desc(MarketSnapshot.snapshot_time)).limit(limit)
    return list(session.scalars(statement))
