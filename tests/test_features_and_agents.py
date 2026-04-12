from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from trading_mvp.models import Position
from trading_mvp.schemas import MarketCandle, MarketSnapshotPayload
from trading_mvp.services.agents import TradingDecisionAgent
from trading_mvp.services.features import compute_features
from trading_mvp.time_utils import utcnow_naive


def _snapshot(
    timeframe: str,
    closes: list[float],
    *,
    volumes: list[float] | None = None,
    stale: bool = False,
    complete: bool = True,
) -> MarketSnapshotPayload:
    now = utcnow_naive()
    interval_minutes = 15 if timeframe == "15m" else 60 if timeframe == "1h" else 240
    candles: list[MarketCandle] = []
    volumes = volumes or [1000.0 for _ in closes]
    for index, close in enumerate(closes):
        previous = closes[index - 1] if index > 0 else close
        timestamp = now - timedelta(minutes=interval_minutes * (len(closes) - index))
        candles.append(
            MarketCandle(
                timestamp=timestamp,
                open=previous,
                high=max(previous, close) * 1.002,
                low=min(previous, close) * 0.998,
                close=close,
                volume=volumes[index],
            )
        )
    return MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe=timeframe,
        snapshot_time=now,
        latest_price=closes[-1],
        latest_volume=volumes[-1],
        candle_count=len(candles),
        is_stale=stale,
        is_complete=complete,
        candles=candles,
    )


def _agent() -> TradingDecisionAgent:
    return TradingDecisionAgent(SimpleNamespace(name="test-provider"))  # type: ignore[arg-type]


def _risk_context(state: str = "TRADABLE") -> dict[str, object]:
    return {
        "max_risk_per_trade": 0.02,
        "max_leverage": 5.0,
        "symbol_risk_tier": "btc",
        "daily_pnl": 0.0,
        "consecutive_losses": 0,
        "operating_state": state,
        "protection_recovery_status": "idle",
        "missing_protection_symbols": [],
        "missing_protection_items": {},
    }


def test_compute_features_detects_bullish_multi_timeframe_regime() -> None:
    base = _snapshot("15m", [100, 101, 100.7, 101.8, 101.6, 102.7, 102.5, 103.7, 104.1, 105.2, 105.0, 106.4, 106.1, 107.5, 108.2, 109.4])
    h1 = _snapshot("1h", [98, 99.5, 99.2, 100.7, 101.4, 102.8, 103.3, 104.6, 105.4, 106.9, 107.8, 109.2, 110.5, 112.0, 113.4, 115.0])
    h4 = _snapshot("4h", [90, 92.2, 91.8, 94.6, 97.5, 100.1, 103.8, 107.2, 111.5, 116.0, 120.8, 126.5, 132.1, 138.0, 144.4, 151.0])

    features = compute_features(base, {"1h": h1, "4h": h4})

    assert features.regime.primary_regime == "bullish"
    assert features.regime.trend_alignment == "bullish_aligned"
    assert "1h" in features.multi_timeframe
    assert "4h" in features.multi_timeframe


def test_compute_features_detects_bearish_multi_timeframe_regime() -> None:
    base = _snapshot("15m", [109.4, 108.2, 108.5, 107.4, 107.7, 106.5, 106.8, 105.6, 105.2, 104.1, 104.3, 103.1, 102.8, 101.7, 101.3, 100.2])
    h1 = _snapshot("1h", [115.0, 113.6, 113.9, 112.4, 111.8, 110.3, 109.8, 108.4, 107.6, 106.1, 105.4, 104.0, 103.1, 101.8, 100.8, 99.7])
    h4 = _snapshot("4h", [151.0, 144.8, 145.4, 138.7, 133.4, 127.8, 122.0, 116.9, 112.3, 107.2, 102.8, 98.4, 94.6, 91.1, 87.9, 85.0])

    features = compute_features(base, {"1h": h1, "4h": h4})

    assert features.regime.primary_regime == "bearish"
    assert features.regime.trend_alignment == "bearish_aligned"


def test_compute_features_detects_range_regime_and_weak_volume_flags() -> None:
    base = _snapshot(
        "15m",
        [100, 101, 100, 101, 100, 101, 100, 101, 100, 101, 100, 101, 100, 101, 100, 101],
        volumes=[1000, 1000, 980, 1020, 1000, 990, 1010, 995, 1005, 1000, 980, 1020, 995, 1005, 1000, 320],
        stale=True,
    )
    h1 = _snapshot("1h", [100, 100.5, 100, 100.5, 100, 100.5, 100, 100.5, 100, 100.5, 100, 100.5, 100, 100.5, 100, 100.5], stale=True)
    h4 = _snapshot("4h", [100, 100.3, 100, 100.3, 100, 100.3, 100, 100.3, 100, 100.3, 100, 100.3, 100, 100.3, 100, 100.3])

    features = compute_features(base, {"1h": h1, "4h": h4})

    assert features.regime.primary_regime == "range"
    assert features.regime.volume_regime == "weak"
    assert "WEAK_VOLUME" in features.data_quality_flags
    assert "STALE_MARKET_DATA" in features.data_quality_flags
    assert "1H_STALE" in features.data_quality_flags


def test_trading_agent_uses_regime_aware_baseline_for_long_and_short() -> None:
    bullish_base = _snapshot("15m", [100, 101, 100.7, 101.8, 101.6, 102.7, 102.5, 103.7, 104.1, 105.2, 105.0, 106.4, 106.1, 107.5, 108.2, 109.4])
    bullish_features = compute_features(
        bullish_base,
        {
            "1h": _snapshot("1h", [98, 99.5, 99.2, 100.7, 101.4, 102.8, 103.3, 104.6, 105.4, 106.9, 107.8, 109.2, 110.5, 112.0, 113.4, 115.0]),
            "4h": _snapshot("4h", [90, 92.2, 91.8, 94.6, 97.5, 100.1, 103.8, 107.2, 111.5, 116.0, 120.8, 126.5, 132.1, 138.0, 144.4, 151.0]),
        },
    )
    bullish_decision, _, _ = _agent().run(
        bullish_base,
        bullish_features,
        [],
        _risk_context(),
        use_ai=False,
        max_input_candles=16,
    )

    bearish_base = _snapshot("15m", [109.4, 108.2, 108.5, 107.4, 107.7, 106.5, 106.8, 105.6, 105.2, 104.1, 104.3, 103.1, 102.8, 101.7, 101.3, 100.2])
    bearish_features = compute_features(
        bearish_base,
        {
            "1h": _snapshot("1h", [115.0, 113.6, 113.9, 112.4, 111.8, 110.3, 109.8, 108.4, 107.6, 106.1, 105.4, 104.0, 103.1, 101.8, 100.8, 99.7]),
            "4h": _snapshot("4h", [151.0, 144.8, 145.4, 138.7, 133.4, 127.8, 122.0, 116.9, 112.3, 107.2, 102.8, 98.4, 94.6, 91.1, 87.9, 85.0]),
        },
    )
    bearish_decision, _, _ = _agent().run(
        bearish_base,
        bearish_features,
        [],
        _risk_context(),
        use_ai=False,
        max_input_candles=16,
    )

    assert bullish_decision.decision == "long"
    assert bullish_decision.stop_loss is not None and bullish_decision.take_profit is not None
    assert bearish_decision.decision == "short"
    assert bearish_decision.stop_loss is not None and bearish_decision.take_profit is not None


def test_trading_agent_prioritizes_protection_recovery_with_open_position() -> None:
    base = _snapshot("15m", [100, 101, 100.7, 101.8, 101.6, 102.7, 102.5, 103.7, 104.1, 105.2, 105.0, 106.4, 106.1, 107.5, 108.2, 109.4])
    features = compute_features(
        base,
        {
            "1h": _snapshot("1h", [98, 99.5, 99.2, 100.7, 101.4, 102.8, 103.3, 104.6, 105.4, 106.9, 107.8, 109.2, 110.5, 112.0, 113.4, 115.0]),
            "4h": _snapshot("4h", [90, 92.2, 91.8, 94.6, 97.5, 100.1, 103.8, 107.2, 111.5, 116.0, 120.8, 126.5, 132.1, 138.0, 144.4, 151.0]),
        },
    )
    open_position = Position(
        symbol="BTCUSDT",
        mode="live",
        side="long",
        status="open",
        quantity=0.01,
        entry_price=120.0,
        mark_price=126.0,
        leverage=2.0,
        stop_loss=116.0,
        take_profit=133.0,
    )

    decision, _, _ = _agent().run(
        base,
        features,
        [open_position],
        _risk_context("PROTECTION_REQUIRED"),
        use_ai=False,
        max_input_candles=16,
    )

    assert decision.decision == "long"
    assert "PROTECTION_REQUIRED" in decision.rationale_codes
    assert decision.stop_loss is not None
    assert decision.take_profit is not None
