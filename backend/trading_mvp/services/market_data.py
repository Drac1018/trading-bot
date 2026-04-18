from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import sin

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from trading_mvp.models import MarketSnapshot
from trading_mvp.schemas import DerivativesContextPayload, MarketCandle, MarketSnapshotPayload
from trading_mvp.services.binance import BinanceClient
from trading_mvp.time_utils import utcnow_naive

DEFAULT_CONTEXT_TIMEFRAMES = ("1h", "4h")
LEAD_MARKET_SYMBOLS = ("BTCUSDT", "ETHUSDT")

DERIVATIVES_CONTEXT_FIELDS = (
    "open_interest",
    "open_interest_change_pct",
    "funding_rate",
    "taker_buy_sell_imbalance",
    "perp_basis_bps",
    "crowding_bias",
    "top_trader_long_short_ratio",
    "spread_bps",
    "spread_stress_score",
)


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


def _seed_derivatives_context() -> DerivativesContextPayload:
    return DerivativesContextPayload(source="seed_fallback", fallback_used=True, fetch_failed=False)


def _compute_spread_stress_score(
    *,
    spread_bps: object,
    top_bid_size: object,
    top_ask_size: object,
) -> float | None:
    try:
        normalized_spread_bps = float(spread_bps)
    except (TypeError, ValueError):
        return None
    if normalized_spread_bps < 0:
        return None
    stress_score = normalized_spread_bps / 4.0
    try:
        bid_size = float(top_bid_size) if top_bid_size not in {None, ""} else None
        ask_size = float(top_ask_size) if top_ask_size not in {None, ""} else None
    except (TypeError, ValueError):
        bid_size = None
        ask_size = None
    if bid_size is not None and ask_size is not None and bid_size > 0 and ask_size > 0:
        smaller = min(bid_size, ask_size)
        larger = max(bid_size, ask_size)
        imbalance = (larger - smaller) / larger if larger > 0 else 0.0
        stress_score *= 1.0 + min(imbalance, 1.0) * 0.25
        if smaller / larger <= 0.35:
            stress_score += 0.35
    return round(max(stress_score, 0.0), 4)


def _build_derivatives_context(client: BinanceClient, symbol: str) -> DerivativesContextPayload:
    payload: dict[str, object] = {
        "source": "binance_public",
        "fallback_used": False,
        "fetch_failed": False,
        "open_interest": None,
        "open_interest_change_pct": None,
        "funding_rate": None,
        "taker_buy_sell_imbalance": None,
        "perp_basis_bps": None,
        "crowding_bias": None,
        "top_trader_long_short_ratio": None,
        "best_bid": None,
        "best_ask": None,
        "spread_bps": None,
        "spread_stress_score": None,
    }
    fetch_failed = False
    try:
        payload["open_interest"] = client.get_open_interest(symbol)
    except Exception:
        fetch_failed = True
    try:
        payload["open_interest_change_pct"] = client.get_open_interest_change_pct(symbol, period="5m")
    except Exception:
        fetch_failed = True
    try:
        premium = client.get_premium_index(symbol)
        payload["funding_rate"] = premium.get("funding_rate")
        payload["perp_basis_bps"] = premium.get("perp_basis_bps")
    except Exception:
        fetch_failed = True
    try:
        payload["taker_buy_sell_imbalance"] = client.get_taker_buy_sell_imbalance(symbol, period="5m")
    except Exception:
        fetch_failed = True
    try:
        payload["crowding_bias"] = client.get_crowding_bias(symbol, period="5m")
    except Exception:
        fetch_failed = True
    try:
        payload["top_trader_long_short_ratio"] = client.get_top_trader_long_short_ratio(symbol, period="5m")
    except Exception:
        fetch_failed = True
    try:
        best_bid_ask = client.get_best_bid_ask(symbol, limit=5)
        payload["best_bid"] = best_bid_ask.get("best_bid")
        payload["best_ask"] = best_bid_ask.get("best_ask")
        payload["spread_bps"] = best_bid_ask.get("spread_bps")
        payload["spread_stress_score"] = _compute_spread_stress_score(
            spread_bps=best_bid_ask.get("spread_bps"),
            top_bid_size=best_bid_ask.get("top_bid_size"),
            top_ask_size=best_bid_ask.get("top_ask_size"),
        )
    except Exception:
        fetch_failed = True
    payload["fetch_failed"] = fetch_failed
    if not any(payload[field] is not None for field in DERIVATIVES_CONTEXT_FIELDS):
        return DerivativesContextPayload(
            source="unavailable",
            fallback_used=True,
            fetch_failed=fetch_failed,
        )
    return DerivativesContextPayload(**payload)


def _build_seed_snapshot(
    symbol: str,
    timeframe: str,
    lookback: int,
    upto_index: int | None,
    force_stale: bool,
    *,
    derivatives_context: DerivativesContextPayload | None = None,
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
        derivatives_context=derivatives_context or _seed_derivatives_context(),
    )


def _build_binance_snapshot(
    symbol: str,
    timeframe: str,
    lookback: int,
    *,
    testnet_enabled: bool,
    stale_threshold_seconds: int,
    derivatives_context: DerivativesContextPayload | None = None,
) -> MarketSnapshotPayload:
    client = BinanceClient(testnet_enabled=testnet_enabled, futures_enabled=True)
    candles = client.fetch_klines(symbol=symbol, interval=timeframe, limit=lookback)
    latest = candles[-1]
    snapshot_time = utcnow_naive()
    staleness_seconds = (snapshot_time - latest.timestamp).total_seconds()
    snapshot_derivatives = derivatives_context or _build_derivatives_context(client, symbol)
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
        derivatives_context=snapshot_derivatives,
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
    derivatives_context_override: DerivativesContextPayload | None = None,
) -> MarketSnapshotPayload:
    if upto_index is not None or force_stale:
        return _build_seed_snapshot(
            symbol,
            timeframe,
            lookback,
            upto_index,
            force_stale,
            derivatives_context=derivatives_context_override,
        )

    if not use_binance:
        raise RuntimeError("실거래 모드에서는 Binance 실데이터가 꺼져 있으면 시장 스냅샷을 만들 수 없습니다.")

    return _build_binance_snapshot(
        symbol=symbol,
        timeframe=timeframe,
        lookback=lookback,
        testnet_enabled=binance_testnet_enabled,
        stale_threshold_seconds=stale_threshold_seconds,
        derivatives_context=derivatives_context_override,
    )


def build_market_context(
    symbol: str,
    base_timeframe: str,
    *,
    context_timeframes: tuple[str, ...] = DEFAULT_CONTEXT_TIMEFRAMES,
    lookback: int = 60,
    upto_index: int | None = None,
    force_stale: bool = False,
    use_binance: bool = False,
    binance_testnet_enabled: bool = False,
    stale_threshold_seconds: int = 1800,
) -> dict[str, MarketSnapshotPayload]:
    timeframes = [base_timeframe, *[item for item in context_timeframes if item != base_timeframe]]
    snapshots: dict[str, MarketSnapshotPayload] = {}
    derivatives_context: DerivativesContextPayload | None = None
    if upto_index is not None or force_stale:
        derivatives_context = _seed_derivatives_context()
    elif use_binance:
        derivatives_context = _build_derivatives_context(
            BinanceClient(testnet_enabled=binance_testnet_enabled, futures_enabled=True),
            symbol,
        )
    for timeframe in timeframes:
        snapshots[timeframe] = build_market_snapshot(
            symbol=symbol,
            timeframe=timeframe,
            lookback=lookback,
            upto_index=upto_index,
            force_stale=force_stale,
            use_binance=use_binance,
            binance_testnet_enabled=binance_testnet_enabled,
            stale_threshold_seconds=stale_threshold_seconds,
            derivatives_context_override=derivatives_context,
        )
    return snapshots


def build_lead_market_contexts(
    base_timeframe: str,
    *,
    lead_symbols: tuple[str, ...] = LEAD_MARKET_SYMBOLS,
    context_timeframes: tuple[str, ...] = DEFAULT_CONTEXT_TIMEFRAMES,
    lookback: int = 60,
    upto_index: int | None = None,
    force_stale: bool = False,
    use_binance: bool = False,
    binance_testnet_enabled: bool = False,
    stale_threshold_seconds: int = 1800,
) -> dict[str, dict[str, MarketSnapshotPayload]]:
    contexts: dict[str, dict[str, MarketSnapshotPayload]] = {}
    for symbol in lead_symbols:
        symbol_key = str(symbol or "").upper()
        if not symbol_key:
            continue
        try:
            contexts[symbol_key] = build_market_context(
                symbol=symbol_key,
                base_timeframe=base_timeframe,
                context_timeframes=context_timeframes,
                lookback=lookback,
                upto_index=upto_index,
                force_stale=force_stale,
                use_binance=use_binance,
                binance_testnet_enabled=binance_testnet_enabled,
                stale_threshold_seconds=stale_threshold_seconds,
            )
        except RuntimeError:
            continue
    return contexts


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
