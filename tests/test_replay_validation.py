from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from trading_mvp.database import Base, get_db
from trading_mvp.main import app
from trading_mvp.providers import DeterministicMockProvider
from trading_mvp.schemas import FeaturePayload, MarketCandle, MarketSnapshotPayload, ReplayValidationRequest
from trading_mvp.services.agents import TradingDecisionAgent
from trading_mvp.services.replay_validation import build_replay_validation_report
from trading_mvp.time_utils import utcnow_naive


def _sample_market_snapshot() -> MarketSnapshotPayload:
    now = utcnow_naive()
    candle = MarketCandle(
        timestamp=now - timedelta(minutes=15),
        open=65000.0,
        high=65150.0,
        low=64920.0,
        close=65080.0,
        volume=1800.0,
    )
    return MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=now,
        latest_price=candle.close,
        latest_volume=candle.volume,
        candle_count=1,
        is_stale=False,
        is_complete=True,
        candles=[candle],
    )


def _sample_features() -> FeaturePayload:
    return FeaturePayload.model_validate(
        {
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "trend_score": 0.24,
            "volatility_pct": 0.011,
            "volume_ratio": 1.18,
            "drawdown_pct": 0.012,
            "rsi": 60.0,
            "atr": 120.0,
            "atr_pct": 0.0018,
            "momentum_score": 0.14,
            "multi_timeframe": {},
            "regime": {
                "primary_regime": "bullish",
                "trend_alignment": "bullish_aligned",
                "volatility_regime": "normal",
                "volume_regime": "strong",
                "momentum_state": "stable",
                "weak_volume": False,
                "momentum_weakening": False,
            },
            "breakout": {
                "range_breakout_direction": "none",
            },
            "candle_structure": {
                "body_ratio": 0.62,
                "upper_wick_ratio": 0.12,
                "lower_wick_ratio": 0.18,
                "wick_to_body_ratio": 0.48,
                "bullish_streak": 2,
                "bearish_streak": 0,
                "bullish_streak_strength": 0.7,
                "bearish_streak_strength": 0.0,
            },
            "location": {
                "distance_from_recent_high_pct": -0.004,
                "distance_from_recent_low_pct": 0.02,
                "range_position_pct": 0.74,
                "vwap_distance_pct": 0.08,
            },
            "volume_persistence": {
                "recent_window": 5,
                "persistence_ratio": 1.05,
                "high_volume_bars": 3,
                "low_volume_bars": 0,
                "sustained_high_volume": True,
                "sustained_low_volume": False,
            },
            "pullback_context": {
                "higher_timeframe_bias": "bullish",
                "state": "bullish_pullback",
                "aligned_with_higher_timeframe": True,
            },
            "data_quality_flags": [],
        }
    )


def _binance_replay_candles(points: int = 220) -> list[MarketCandle]:
    start = utcnow_naive() - timedelta(minutes=15 * points)
    candles: list[MarketCandle] = []
    price = 65000.0
    for index in range(points):
        drift = 35.0 if index % 12 < 8 else -18.0
        open_price = price
        close_price = open_price + drift
        high_price = max(open_price, close_price) + 20.0
        low_price = min(open_price, close_price) - 18.0
        candles.append(
            MarketCandle(
                timestamp=start + timedelta(minutes=15 * index),
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                volume=1500.0 + index,
            )
        )
        price = close_price
    return candles


def test_trading_agent_supports_baseline_old_logic_variant() -> None:
    agent = TradingDecisionAgent(DeterministicMockProvider())
    market_snapshot = _sample_market_snapshot()
    features = _sample_features()
    risk_context = {
        "max_risk_per_trade": 0.02,
        "max_leverage": 5.0,
        "operating_state": "TRADABLE",
        "position_management_context": {},
        "adaptive_signal_context": {"enabled": False},
    }

    baseline_decision, _, baseline_metadata = agent.run(
        market_snapshot,
        features,
        [],
        risk_context,
        use_ai=False,
        max_input_candles=12,
        logic_variant="baseline_old",
    )
    improved_decision, _, improved_metadata = agent.run(
        market_snapshot,
        features,
        [],
        risk_context,
        use_ai=False,
        max_input_candles=12,
        logic_variant="improved",
    )

    assert baseline_decision.decision == "hold"
    assert improved_decision.decision == "long"
    assert "PROVIDER_DETERMINISTIC_BASELINE_OLD" in baseline_decision.rationale_codes
    assert "PROVIDER_DETERMINISTIC_MOCK" in improved_decision.rationale_codes
    assert baseline_metadata["logic_variant"] == "baseline_old"
    assert baseline_metadata["adaptive_signal_adjustment"]["status"] == "disabled_for_baseline_old"
    assert improved_metadata["logic_variant"] == "improved"


def test_replay_validation_report_compares_variants_without_live_execution(monkeypatch, db_session) -> None:
    def fail_execute(*args, **kwargs):
        raise AssertionError("historical replay validation must not place live orders")

    monkeypatch.setattr("trading_mvp.services.orchestrator.execute_live_trade", fail_execute)

    report = build_replay_validation_report(
        db_session,
        ReplayValidationRequest(
            cycles=24,
            start_index=90,
            timeframe="15m",
            symbols=["BTCUSDT"],
        ),
    )

    assert report.data_source_type == "synthetic_seed"
    assert "never submits live orders" in report.live_execution_guarantee
    assert {variant.logic_variant for variant in report.variants} == {"baseline_old", "improved"}
    assert all(variant.summary.decisions > 0 for variant in report.variants)
    assert all(variant.summary.average_arrival_slippage_pct >= 0.0 for variant in report.variants)
    assert all(variant.summary.average_realized_slippage_pct >= 0.0 for variant in report.variants)
    assert all(variant.summary.average_first_fill_latency_seconds >= 0.0 for variant in report.variants)
    assert all(variant.by_rationale_code for variant in report.variants)
    assert report.symbol_comparison and report.symbol_comparison[0].key == "BTCUSDT"
    assert report.timeframe_comparison and report.timeframe_comparison[0].key == "15m"
    assert report.regime_comparison
    assert report.rationale_comparison


def test_replay_validation_api_is_hard_disabled(tmp_path, monkeypatch) -> None:
    test_engine = create_engine(f"sqlite:///{tmp_path / 'replay_validation_api.db'}", future=True)
    TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=test_engine)
    monkeypatch.setattr("trading_mvp.main.engine", test_engine)

    def fail_execute(*args, **kwargs):
        raise AssertionError("historical replay validation must not place live orders")

    monkeypatch.setattr("trading_mvp.services.orchestrator.execute_live_trade", fail_execute)

    def override_get_db():
        with TestingSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/replay/validation",
                json={
                    "cycles": 20,
                    "start_index": 90,
                    "timeframe": "15m",
                    "symbols": ["BTCUSDT"],
                    "data_source_type": "synthetic_seed",
                },
            )

        assert response.status_code == 410
        payload = response.json()["detail"]
        assert payload["code"] == "NON_LIVE_SURFACE_DISABLED"
        assert "/api/replay/validation" in payload["message"]
    finally:
        app.dependency_overrides.clear()


def test_replay_validation_supports_binance_futures_klines_data_source(monkeypatch, db_session) -> None:
    def fail_execute(*args, **kwargs):
        raise AssertionError("historical replay validation must not place live orders")

    monkeypatch.setattr("trading_mvp.services.orchestrator.execute_live_trade", fail_execute)
    monkeypatch.setattr(
        "trading_mvp.services.replay_validation.BinanceClient.fetch_klines",
        lambda self, symbol, interval, limit=500: _binance_replay_candles(limit),
    )

    report = build_replay_validation_report(
        db_session,
        ReplayValidationRequest(
            cycles=18,
            start_index=90,
            timeframe="15m",
            symbols=["BTCUSDT"],
            data_source_type="binance_futures_klines",
        ),
    )

    assert report.data_source_type == "binance_futures_klines"
    assert "binance_futures_klines" in report.data_source_basis
    assert all(variant.data_source_type == "binance_futures_klines" for variant in report.variants)
    assert all(variant.summary.average_arrival_slippage_pct >= 0.0 for variant in report.variants)
    assert all(variant.summary.average_realized_slippage_pct >= 0.0 for variant in report.variants)
    assert all(variant.summary.average_first_fill_latency_seconds >= 0.0 for variant in report.variants)
    assert all(variant.summary.average_mfe_pct >= 0.0 for variant in report.variants)
    assert all(variant.summary.worst_mae_pct >= 0.0 for variant in report.variants)
    assert all(variant.by_rationale_code for variant in report.variants)
    assert report.live_execution_guarantee.endswith("never submits live orders.")
