from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from trading_mvp.database import Base, get_db
from trading_mvp.main import app
from trading_mvp.providers import DeterministicMockProvider
from trading_mvp.schemas import (
    FeaturePayload,
    MarketCandle,
    MarketSnapshotPayload,
    ReplayBreakdownEntry,
    ReplayMetricSummary,
    ReplayValidationRequest,
)
from trading_mvp.services.agents import TradingDecisionAgent
from trading_mvp.services.replay_validation import (
    ReplayAccumulator,
    _build_parameter_recommendation,
    _summarize_metrics,
    build_replay_validation_report,
)
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
    assert all(
        variant.summary.closed_trades == 0
        or variant.summary.expectancy
        == pytest.approx(variant.summary.net_pnl_after_fees / variant.summary.closed_trades, abs=1e-8)
        for variant in report.variants
    )
    assert all(variant.recent_window_summary.closed_trades >= 0 for variant in report.variants)
    assert all(variant.by_scenario for variant in report.variants)
    assert all(variant.by_trend_alignment for variant in report.variants)
    assert all(variant.by_execution_policy_profile for variant in report.variants)
    assert all(variant.by_entry_mode for variant in report.variants)
    assert all(variant.walk_forward_recommendation is not None for variant in report.variants)
    assert report.recent_walk_forward_recommendation is not None
    assert report.recent_walk_forward_recommendation.risk_context_patch
    assert all(variant.by_rationale_code for variant in report.variants)
    assert report.symbol_comparison and report.symbol_comparison[0].key == "BTCUSDT"
    assert report.timeframe_comparison and report.timeframe_comparison[0].key == "15m"
    assert report.scenario_comparison
    assert report.regime_comparison
    assert report.trend_alignment_comparison
    assert report.execution_policy_profile_comparison
    assert report.entry_mode_comparison
    assert report.rationale_comparison


def test_replay_validation_api_returns_comparison_report(tmp_path, monkeypatch) -> None:
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

        assert response.status_code == 200
        payload = response.json()
        assert payload["data_source_type"] == "synthetic_seed"
        assert payload["execution_basis"] == "next_bar_open_entry_with_intrabar_stop_take_profit_and_synthetic_fees"
        assert len(payload["variants"]) == 2
        assert payload["variants"][0]["summary"]["gross_pnl"] is not None
        assert "average_arrival_slippage_pct" in payload["variants"][0]["summary"]
        assert "average_first_fill_latency_seconds" in payload["variants"][0]["summary"]
        assert "expectancy" in payload["variants"][0]["summary"]
        assert "recent_window_summary" in payload["variants"][0]
        assert "walk_forward_recommendation" in payload["variants"][0]
        assert "underperforming_buckets" in payload["variants"][0]
        assert "recent_walk_forward_recommendation" in payload
        assert payload["variants"][0]["by_rationale_code"]
        assert payload["symbol_comparison"][0]["key"] == "BTCUSDT"
        assert payload["timeframe_comparison"][0]["key"] == "15m"
        assert payload["scenario_comparison"]
        assert payload["rationale_comparison"]
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
    assert all(variant.summary.average_hold_time_minutes >= 0.0 for variant in report.variants)
    assert all(variant.summary.partial_tp_contribution >= 0.0 for variant in report.variants)
    assert all(variant.summary.runner_contribution >= 0.0 for variant in report.variants)
    assert all(variant.by_rationale_code for variant in report.variants)
    assert report.live_execution_guarantee.endswith("never submits live orders.")


def test_replay_metric_summary_calculates_expectancy_and_mfe_mae() -> None:
    accumulator = ReplayAccumulator(
        decisions=4,
        closed_trades=3,
        gross_pnl=21.0,
        net_pnl=18.0,
        fees=3.0,
        trade_nets=[12.0, -4.0, 10.0],
        holding_minutes_values=[30.0, 45.0, 75.0],
        stop_hits=1,
        tp_hits=1,
        partial_tp_contribution_total=5.0,
        runner_contribution_total=7.0,
        mfe_pct_values=[0.03, 0.05, 0.02],
        mae_pct_values=[0.01, 0.015, 0.025],
    )

    summary = _summarize_metrics(accumulator)

    assert summary.net_pnl_after_fees == 18.0
    assert summary.avg_win == 11.0
    assert summary.avg_loss == 4.0
    assert summary.expectancy == 6.0
    assert summary.average_hold_time_minutes == 50.0
    assert summary.stop_hit_rate == pytest.approx(1 / 3, abs=1e-8)
    assert summary.tp_hit_rate == pytest.approx(1 / 3, abs=1e-8)
    assert summary.partial_tp_contribution == 5.0
    assert summary.runner_contribution == 7.0
    assert summary.average_mfe_pct == pytest.approx(0.03333333, abs=1e-8)
    assert summary.average_mae_pct == pytest.approx(0.01666667, abs=1e-8)
    assert summary.best_mfe_pct == 0.05
    assert summary.worst_mae_pct == 0.025


def test_replay_metric_summary_uses_zero_baseline_for_equity_points() -> None:
    accumulator = ReplayAccumulator(
        decisions=3,
        closed_trades=2,
        trade_nets=[-2.0, 5.0],
    )

    summary = _summarize_metrics(accumulator, equity_points=[-2.0, 3.0, -1.0])

    assert summary.max_drawdown == 4.0


def test_replay_parameter_recommendation_returns_fallback_when_data_is_insufficient() -> None:
    recommendation = _build_parameter_recommendation(
        logic_variant="improved",
        recent_summary=ReplayMetricSummary(decisions=5, closed_trades=1),
        by_entry_mode=[ReplayBreakdownEntry(key="pullback_confirm", decisions=1, closed_trades=1)],
        by_scenario=[ReplayBreakdownEntry(key="pullback_entry", decisions=1, closed_trades=1)],
        by_execution_policy_profile=[ReplayBreakdownEntry(key="entry_btc_fast_calm", decisions=1, closed_trades=1)],
    )

    assert recommendation.status == "insufficient_data"
    assert recommendation.sample_size == 1
    assert recommendation.risk_pct_multiplier == 1.0
    assert recommendation.leverage_multiplier == 1.0
    assert recommendation.rationale == ["INSUFFICIENT_SAMPLE_SIZE"]
    assert recommendation.adaptive_signal_context_patch
    assert recommendation.risk_context_patch
