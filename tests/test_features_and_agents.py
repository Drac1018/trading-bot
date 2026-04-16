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


def _risk_budget(
    *,
    long_notional: float = 5000.0,
    short_notional: float = 5000.0,
    symbol_notional: float = 2500.0,
    leverage: float = 5.0,
    directional_headroom: float = 5000.0,
    single_position_headroom: float = 2500.0,
    total_exposure_headroom: float = 8000.0,
) -> dict[str, float]:
    return {
        "max_additional_long_notional": long_notional,
        "max_additional_short_notional": short_notional,
        "max_new_position_notional_for_symbol": symbol_notional,
        "max_leverage_for_symbol": leverage,
        "directional_bias_headroom": directional_headroom,
        "single_position_headroom": single_position_headroom,
        "total_exposure_headroom": total_exposure_headroom,
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


def test_compute_features_adds_structure_location_volume_and_pullback_context() -> None:
    base = _snapshot(
        "15m",
        [100, 100.4, 100.6, 100.8, 100.7, 101.0, 101.2, 101.1, 101.4, 101.6, 101.8, 102.0, 102.2, 102.1, 102.4, 103.1],
        volumes=[900, 920, 940, 960, 955, 980, 1000, 1020, 1040, 1060, 1200, 1260, 1320, 1380, 1440, 1500],
    )
    h1 = _snapshot("1h", [98, 98.7, 99.4, 100.1, 100.9, 101.8, 102.7, 103.6, 104.4, 105.2, 106.1, 107.0, 108.0, 109.1, 110.2, 111.4])
    h4 = _snapshot("4h", [92, 93.4, 94.9, 96.6, 98.4, 100.1, 102.0, 104.1, 106.4, 108.8, 111.3, 114.0, 116.8, 119.7, 122.7, 125.8])

    features = compute_features(base, {"1h": h1, "4h": h4})

    assert features.breakout.broke_swing_high is True
    assert features.breakout.range_breakout_direction == "up"
    assert features.candle_structure.bullish_streak >= 1
    assert features.location.range_position_pct > 1.0
    assert features.location.vwap_distance_pct > 0.0
    assert features.volume_persistence.sustained_high_volume is True
    assert features.pullback_context.higher_timeframe_bias == "bullish"
    assert features.pullback_context.state == "bullish_continuation"


def test_compute_features_marks_partial_flags_when_context_is_insufficient() -> None:
    base = _snapshot("15m", [100, 100.3, 100.1, 100.4], volumes=[0, 0, 0, 0])

    features = compute_features(base, {})

    assert "SWING_CONTEXT_PARTIAL" in features.data_quality_flags
    assert "VOLUME_PERSISTENCE_PARTIAL" in features.data_quality_flags
    assert "VWAP_CONTEXT_PARTIAL" in features.data_quality_flags
    assert "PULLBACK_CONTEXT_PARTIAL" in features.data_quality_flags


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


def test_trading_agent_prefers_continuation_pullback_rationale_over_breakout_chasing() -> None:
    bullish_base = _snapshot(
        "15m",
        [100, 100.4, 100.6, 100.8, 100.7, 101.0, 101.2, 101.1, 101.4, 101.6, 101.8, 102.0, 102.2, 102.1, 102.4, 103.1],
        volumes=[900, 920, 940, 960, 955, 980, 1000, 1020, 1040, 1060, 1090, 1120, 1150, 1180, 1220, 1260],
    )
    bullish_features = compute_features(
        bullish_base,
        {
            "1h": _snapshot("1h", [98, 98.7, 99.4, 100.1, 100.9, 101.8, 102.7, 103.6, 104.4, 105.2, 106.1, 107.0, 108.0, 109.1, 110.2, 111.4]),
            "4h": _snapshot("4h", [92, 93.4, 94.9, 96.6, 98.4, 100.1, 102.0, 104.1, 106.4, 108.8, 111.3, 114.0, 116.8, 119.7, 122.7, 125.8]),
        },
    )

    decision, _, _ = _agent().run(
        bullish_base,
        bullish_features,
        [],
        _risk_context(),
        use_ai=False,
        max_input_candles=16,
    )

    assert decision.decision == "long"
    assert "PULLBACK_ENTRY_BIAS" in decision.rationale_codes
    assert "BULLISH_CONTINUATION_PULLBACK" in decision.rationale_codes


def test_trading_agent_prefers_hold_when_long_risk_budget_is_exhausted() -> None:
    bullish_base = _snapshot(
        "15m",
        [100, 100.4, 100.6, 100.8, 100.7, 101.0, 101.2, 101.1, 101.4, 101.6, 101.8, 102.0, 102.2, 102.1, 102.4, 103.1],
        volumes=[900, 920, 940, 960, 955, 980, 1000, 1020, 1040, 1060, 1090, 1120, 1150, 1180, 1220, 1260],
    )
    bullish_features = compute_features(
        bullish_base,
        {
            "1h": _snapshot("1h", [98, 98.7, 99.4, 100.1, 100.9, 101.8, 102.7, 103.6, 104.4, 105.2, 106.1, 107.0, 108.0, 109.1, 110.2, 111.4]),
            "4h": _snapshot("4h", [92, 93.4, 94.9, 96.6, 98.4, 100.1, 102.0, 104.1, 106.4, 108.8, 111.3, 114.0, 116.8, 119.7, 122.7, 125.8]),
        },
    )

    decision, _, _ = _agent().run(
        bullish_base,
        bullish_features,
        [],
        {
            **_risk_context(),
            "risk_budget": _risk_budget(long_notional=0.0, symbol_notional=0.0, total_exposure_headroom=0.0),
        },
        use_ai=False,
        max_input_candles=16,
    )

    assert decision.decision == "hold"
    assert "RISK_BUDGET_EXHAUSTED" in decision.rationale_codes
    assert "HOLD_ON_LONG_BUDGET_LIMIT" in decision.rationale_codes


def test_trading_agent_prefers_hold_when_short_risk_budget_is_too_small() -> None:
    bearish_base = _snapshot("15m", [109.4, 108.2, 108.5, 107.4, 107.7, 106.5, 106.8, 105.6, 105.2, 104.1, 104.3, 103.1, 102.8, 101.7, 101.3, 100.2])
    bearish_features = compute_features(
        bearish_base,
        {
            "1h": _snapshot("1h", [115.0, 113.6, 113.9, 112.4, 111.8, 110.3, 109.8, 108.4, 107.6, 106.1, 105.4, 104.0, 103.1, 101.8, 100.8, 99.7]),
            "4h": _snapshot("4h", [151.0, 144.8, 145.4, 138.7, 133.4, 127.8, 122.0, 116.9, 112.3, 107.2, 102.8, 98.4, 94.6, 91.1, 87.9, 85.0]),
        },
    )

    decision, _, _ = _agent().run(
        bearish_base,
        bearish_features,
        [],
        {
            **_risk_context(),
            "risk_budget": _risk_budget(short_notional=10.0, symbol_notional=10.0, total_exposure_headroom=10.0),
        },
        use_ai=False,
        max_input_candles=16,
    )

    assert decision.decision == "hold"
    assert "RISK_BUDGET_EXHAUSTED" in decision.rationale_codes
    assert "HOLD_ON_SHORT_BUDGET_LIMIT" in decision.rationale_codes


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


def test_trading_agent_reduces_when_partial_take_profit_is_ready() -> None:
    base = _snapshot(
        "15m",
        [100.0, 100.2, 100.35, 100.5, 100.65, 100.8, 100.95, 101.1, 101.25, 101.4, 101.55, 101.7, 101.85, 102.0, 102.15, 102.3],
        volumes=[1000.0, 1010.0, 1005.0, 1015.0, 1020.0, 1030.0, 1040.0, 1045.0, 1050.0, 1055.0, 1060.0, 1065.0, 1070.0, 1080.0, 1090.0, 1100.0],
    )
    features = compute_features(
        base,
        {
            "1h": _snapshot("1h", [98.0, 98.5, 99.0, 99.6, 100.1, 100.7, 101.2, 101.8, 102.3, 102.9, 103.4, 104.0, 104.6, 105.1, 105.7, 106.2]),
            "4h": _snapshot("4h", [94.0, 95.1, 96.2, 97.3, 98.4, 99.6, 100.8, 102.0, 103.2, 104.5, 105.8, 107.1, 108.5, 109.9, 111.4, 112.9]),
        },
    )
    features.rsi = 60.0
    features.trend_score = 0.0
    features.momentum_score = 0.0
    features.regime.primary_regime = "bullish"
    features.regime.trend_alignment = "mixed"
    features.regime.weak_volume = False
    features.regime.momentum_weakening = False
    open_position = Position(
        symbol="BTCUSDT",
        mode="live",
        side="long",
        status="open",
        quantity=0.01,
        entry_price=100.0,
        mark_price=102.3,
        leverage=2.0,
        stop_loss=98.5,
        take_profit=104.5,
    )

    decision, _, _ = _agent().run(
        base,
        features,
        [open_position],
        {
            **_risk_context(),
            "position_management_context": {
                "partial_take_profit_ready": True,
                "reduce_reason_codes": [],
                "current_r_multiple": 1.53,
            },
        },
        use_ai=False,
        max_input_candles=16,
    )

    assert decision.decision == "reduce"
    assert "POSITION_MANAGEMENT_PARTIAL_TAKE_PROFIT" in decision.rationale_codes
    assert "POSITION_MANAGEMENT_LOCK_PARTIAL_PROFIT" in decision.rationale_codes


def test_trading_agent_reduces_when_position_management_flags_edge_decay() -> None:
    base = _snapshot(
        "15m",
        [100.0, 100.1, 100.25, 100.4, 100.55, 100.7, 100.85, 101.0, 101.15, 101.3, 101.45, 101.6, 101.75, 101.9, 102.05, 102.2],
        volumes=[980.0, 990.0, 1000.0, 995.0, 1005.0, 1010.0, 1015.0, 1020.0, 1025.0, 1030.0, 1035.0, 1040.0, 1045.0, 1050.0, 1055.0, 1060.0],
    )
    features = compute_features(
        base,
        {
            "1h": _snapshot("1h", [98.0, 98.6, 99.1, 99.7, 100.2, 100.8, 101.3, 101.9, 102.4, 103.0, 103.5, 104.1, 104.7, 105.2, 105.8, 106.3]),
            "4h": _snapshot("4h", [94.0, 95.0, 96.1, 97.2, 98.3, 99.5, 100.7, 101.9, 103.1, 104.4, 105.7, 107.0, 108.4, 109.8, 111.2, 112.7]),
        },
    )
    features.rsi = 58.0
    features.trend_score = 0.0
    features.momentum_score = 0.0
    features.regime.primary_regime = "bullish"
    features.regime.trend_alignment = "mixed"
    features.regime.weak_volume = False
    features.regime.momentum_weakening = False
    open_position = Position(
        symbol="BTCUSDT",
        mode="live",
        side="long",
        status="open",
        quantity=0.01,
        entry_price=100.0,
        mark_price=102.2,
        leverage=2.0,
        stop_loss=98.6,
        take_profit=104.4,
    )

    decision, _, _ = _agent().run(
        base,
        features,
        [open_position],
        {
            **_risk_context(),
            "position_management_context": {
                "partial_take_profit_ready": False,
                "reduce_reason_codes": ["POSITION_MANAGEMENT_EDGE_DECAY", "POSITION_MANAGEMENT_MOMENTUM_WEAKENING"],
                "current_r_multiple": 1.1,
            },
        },
        use_ai=False,
        max_input_candles=16,
    )

    assert decision.decision == "reduce"
    assert "POSITION_MANAGEMENT_EDGE_DECAY" in decision.rationale_codes
    assert "POSITION_MANAGEMENT_MOMENTUM_WEAKENING" in decision.rationale_codes


def test_trading_agent_applies_adaptive_confidence_and_risk_discount() -> None:
    bullish_base = _snapshot(
        "15m",
        [100, 100.4, 100.6, 100.8, 100.7, 101.0, 101.2, 101.1, 101.4, 101.6, 101.8, 102.0, 102.2, 102.1, 102.4, 103.1],
        volumes=[900, 920, 940, 960, 955, 980, 1000, 1020, 1040, 1060, 1090, 1120, 1150, 1180, 1220, 1260],
    )
    bullish_features = compute_features(
        bullish_base,
        {
            "1h": _snapshot("1h", [98, 98.7, 99.4, 100.1, 100.9, 101.8, 102.7, 103.6, 104.4, 105.2, 106.1, 107.0, 108.0, 109.1, 110.2, 111.4]),
            "4h": _snapshot("4h", [92, 93.4, 94.9, 96.6, 98.4, 100.1, 102.0, 104.1, 106.4, 108.8, 111.3, 114.0, 116.8, 119.7, 122.7, 125.8]),
        },
    )
    baseline_decision, _, _ = _agent().run(
        bullish_base,
        bullish_features,
        [],
        _risk_context(),
        use_ai=False,
        max_input_candles=16,
    )

    adaptive_context = {
        "enabled": True,
        "windows": {
            "24h": {
                "symbol_timeframe": {"weight": 0.85},
                "symbol": {"weight": 0.85},
                "regime": {"weight": 0.86},
                "rationale_codes": {
                    "TREND_UP": {"weight": 0.85},
                    "PULLBACK_ENTRY_BIAS": {"weight": 0.85},
                    "BULLISH_CONTINUATION_PULLBACK": {"weight": 0.85},
                },
            },
            "7d": {
                "symbol_timeframe": {"weight": 0.86},
                "symbol": {"weight": 0.86},
                "regime": {"weight": 0.87},
                "rationale_codes": {
                    "TREND_UP": {"weight": 0.86},
                    "PULLBACK_ENTRY_BIAS": {"weight": 0.86},
                    "BULLISH_CONTINUATION_PULLBACK": {"weight": 0.86},
                },
            },
        },
    }
    adaptive_risk_context = {
        **_risk_context(),
        "adaptive_signal_context": adaptive_context,
    }

    adaptive_decision, _, metadata = _agent().run(
        bullish_base,
        bullish_features,
        [],
        adaptive_risk_context,
        use_ai=False,
        max_input_candles=16,
    )

    assert baseline_decision.decision == "long"
    assert adaptive_decision.decision == "long"
    assert adaptive_decision.confidence < baseline_decision.confidence
    assert adaptive_decision.risk_pct < baseline_decision.risk_pct
    assert "ADAPTIVE_CONFIDENCE_DISCOUNT" in adaptive_decision.rationale_codes
    assert "ADAPTIVE_RISK_REDUCED" in adaptive_decision.rationale_codes
    assert metadata["adaptive_signal_adjustment"]["status"] == "active"


def test_trading_agent_keeps_default_behavior_when_adaptive_data_is_missing() -> None:
    bearish_base = _snapshot("15m", [109.4, 108.2, 108.5, 107.4, 107.7, 106.5, 106.8, 105.6, 105.2, 104.1, 104.3, 103.1, 102.8, 101.7, 101.3, 100.2])
    bearish_features = compute_features(
        bearish_base,
        {
            "1h": _snapshot("1h", [115.0, 113.6, 113.9, 112.4, 111.8, 110.3, 109.8, 108.4, 107.6, 106.1, 105.4, 104.0, 103.1, 101.8, 100.8, 99.7]),
            "4h": _snapshot("4h", [151.0, 144.8, 145.4, 138.7, 133.4, 127.8, 122.0, 116.9, 112.3, 107.2, 102.8, 98.4, 94.6, 91.1, 87.9, 85.0]),
        },
    )
    baseline_decision, _, _ = _agent().run(
        bearish_base,
        bearish_features,
        [],
        _risk_context(),
        use_ai=False,
        max_input_candles=16,
    )
    neutral_decision, _, metadata = _agent().run(
        bearish_base,
        bearish_features,
        [],
        {
            **_risk_context(),
            "adaptive_signal_context": {"enabled": True, "windows": {}},
        },
        use_ai=False,
        max_input_candles=16,
    )

    assert neutral_decision.decision == baseline_decision.decision
    assert neutral_decision.confidence == baseline_decision.confidence
    assert neutral_decision.risk_pct == baseline_decision.risk_pct
    assert metadata["adaptive_signal_adjustment"]["status"] == "insufficient_data"
