from __future__ import annotations

from datetime import datetime, timedelta

from trading_mvp.schemas import DerivativesContextPayload, EventContextPayload, MarketCandle
from trading_mvp.services import market_data


def _install_binance_snapshot_fakes(monkeypatch, fixed_now: datetime) -> None:
    latest_age_by_interval = {
        "15m": timedelta(minutes=31),
        "1h": timedelta(minutes=55),
        "4h": timedelta(hours=3, minutes=14),
    }
    interval_minutes = {"15m": 15, "1h": 60, "4h": 240}

    class FakeBinanceClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def fetch_klines(self, *, symbol: str, interval: str, limit: int) -> list[MarketCandle]:
            latest_timestamp = fixed_now - latest_age_by_interval[interval]
            step = timedelta(minutes=interval_minutes[interval])
            candles: list[MarketCandle] = []
            for index in range(limit):
                timestamp = latest_timestamp - step * (limit - index - 1)
                close = 100.0 + index
                candles.append(
                    MarketCandle(
                        timestamp=timestamp,
                        open=close - 0.5,
                        high=close + 1.0,
                        low=close - 1.0,
                        close=close,
                        volume=1000.0 + index,
                    )
                )
            return candles

    monkeypatch.setattr(market_data, "utcnow_naive", lambda: fixed_now)
    monkeypatch.setattr(market_data, "BinanceClient", FakeBinanceClient)
    monkeypatch.setattr(market_data, "_build_derivatives_context", lambda *_args, **_kwargs: DerivativesContextPayload())
    monkeypatch.setattr(
        market_data,
        "build_event_context",
        lambda **kwargs: EventContextPayload(generated_at=kwargs["generated_at"], is_complete=True),
    )


def test_market_context_relaxes_context_timeframe_stale_threshold_only(monkeypatch) -> None:
    fixed_now = datetime(2026, 4, 27, 15, 14, 44)
    _install_binance_snapshot_fakes(monkeypatch, fixed_now)

    context = market_data.build_market_context(
        symbol="BTCUSDT",
        base_timeframe="15m",
        context_timeframes=("1h", "4h"),
        use_binance=True,
        stale_threshold_seconds=1800,
    )

    assert context["15m"].is_stale is True
    assert context["1h"].is_stale is False
    assert context["4h"].is_stale is False


def test_market_snapshot_keeps_configured_stale_threshold_for_base_calls(monkeypatch) -> None:
    fixed_now = datetime(2026, 4, 27, 15, 14, 44)
    _install_binance_snapshot_fakes(monkeypatch, fixed_now)

    snapshot = market_data.build_market_snapshot(
        symbol="BTCUSDT",
        timeframe="4h",
        use_binance=True,
        stale_threshold_seconds=1800,
    )

    assert snapshot.is_stale is True
