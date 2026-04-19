from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from trading_mvp.models import AgentRun, Execution, Order, Position, RiskCheck
from trading_mvp.providers import ProviderResult
from trading_mvp.schemas import (
    AIDecisionContextPacket,
    AIPriorContextPacket,
    CompositeRegimePacket,
    DataQualityPacket,
    DerivativesContextPayload,
    DerivativesSummaryPayload,
    EventContextPayload,
    EventContextSummaryPayload,
    LeadLagSummaryPayload,
    MarketCandle,
    MarketSnapshotPayload,
    PreviousThesisDeltaPacket,
    RegimeSummaryPayload,
    TradeDecision,
)
from trading_mvp.services.adaptive_signal import (
    ADAPTIVE_SETUP_DISABLE_REASON_CODE,
    build_adaptive_signal_context,
    compute_adaptive_adjustment,
)
from trading_mvp.services.agents import TradingDecisionAgent, build_trading_decision_input_payload
from trading_mvp.services.features import compute_features, summarize_universe_breadth
from trading_mvp.services.intent_semantics import infer_intent_semantics
from trading_mvp.services.strategy_engines import select_strategy_engine
from trading_mvp.time_utils import utcnow_naive


def _snapshot(
    timeframe: str,
    closes: list[float],
    *,
    volumes: list[float] | None = None,
    stale: bool = False,
    complete: bool = True,
    derivatives_context: DerivativesContextPayload | None = None,
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
        derivatives_context=derivatives_context or DerivativesContextPayload(),
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


def _setup_cluster_context(
    *,
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    scenario: str = "pullback_entry",
    entry_mode: str = "pullback_confirm",
    regime: str = "bullish",
    trend_alignment: str = "bullish_aligned",
    active: bool = True,
) -> dict[str, object]:
    cluster_key = f"{symbol}|{timeframe}|{scenario}|{entry_mode}|{regime}|{trend_alignment}"
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "regime": regime,
        "trend_alignment": trend_alignment,
        "cluster_lookup": {
            cluster_key: {
                "cluster_key": cluster_key,
                "symbol": symbol,
                "timeframe": timeframe,
                "scenario": scenario,
                "entry_mode": entry_mode,
                "regime": regime,
                "trend_alignment": trend_alignment,
                "active": active,
                "disable_reason_codes": ["CLUSTER_NEGATIVE_EXPECTANCY", "CLUSTER_LOSS_STREAK"],
                "disabled_at": utcnow_naive().isoformat(),
                "cooldown_expires_at": (utcnow_naive() + timedelta(minutes=180)).isoformat(),
                "metrics": {
                    "expectancy": -12.0,
                    "net_pnl_after_fees": -48.0,
                    "avg_signed_slippage_bps": 15.0,
                    "loss_streak": 4,
                },
                "recovery_condition": {
                    "mode": "cooldown_or_positive_recent_metrics",
                    "cooldown_minutes": 180,
                },
            }
        },
        "active_cluster_keys": [cluster_key] if active else [],
    }


def test_summarize_universe_breadth_flags_weak_universe_without_directional_candidates() -> None:
    breadth = summarize_universe_breadth(
        [
            {
                "symbol": "BTCUSDT",
                "primary_regime": "range",
                "trend_alignment": "bullish_aligned",
                "weak_volume": True,
                "momentum_weakening": True,
            },
            {
                "symbol": "ETHUSDT",
                "primary_regime": "transition",
                "trend_alignment": "bearish_aligned",
                "weak_volume": True,
                "momentum_weakening": True,
            },
        ]
    )

    assert breadth["breadth_regime"] == "weak_breadth"
    assert breadth["entry_candidates"] == 0
    assert breadth["entry_score_multiplier"] < 1.0
    assert breadth["hold_bias_multiplier"] > 1.0


def test_summarize_universe_breadth_detects_trend_expansion_without_decision_map() -> None:
    breadth = summarize_universe_breadth(
        [
            {
                "symbol": "BTCUSDT",
                "primary_regime": "bullish",
                "trend_alignment": "bullish_aligned",
                "weak_volume": False,
                "momentum_weakening": False,
            },
            {
                "symbol": "ETHUSDT",
                "primary_regime": "bullish",
                "trend_alignment": "bullish_aligned",
                "weak_volume": False,
                "momentum_weakening": False,
            },
            {
                "symbol": "SOLUSDT",
                "primary_regime": "bullish",
                "trend_alignment": "bullish_aligned",
                "weak_volume": False,
                "momentum_weakening": False,
            },
        ]
    )

    assert breadth["breadth_regime"] == "trend_expansion"
    assert breadth["directional_bias"] == "bullish"
    assert breadth["entry_candidates"] == 3
    assert breadth["entry_score_multiplier"] > 1.0
    assert breadth["hold_bias_multiplier"] < 1.0


def _seed_setup_bucket_history(
    db_session,
    *,
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    regime: str = "bullish",
    trend_alignment: str = "bullish_aligned",
    entry_mode: str = "pullback_confirm",
    rationale_codes: list[str] | None = None,
    net_pnls: list[float],
    signed_slippage_bps: list[float] | None = None,
    start_offset_minutes: int = 5,
) -> None:
    now = utcnow_naive()
    reason_codes = rationale_codes or ["PULLBACK_ENTRY_BIAS", "BULLISH_CONTINUATION_PULLBACK"]
    slippage_values = signed_slippage_bps or [14.0 for _ in net_pnls]
    for index, net_pnl in enumerate(net_pnls):
        created_at = now - timedelta(minutes=start_offset_minutes + (index * 5))
        decision_row = AgentRun(
            role="trading_decision",
            trigger_event="interval_decision_cycle",
            schema_name="TradeDecision",
            status="completed",
            provider_name="deterministic-mock",
            summary="setup history",
            input_payload={
                "features": {
                    "regime": {
                        "primary_regime": regime,
                        "trend_alignment": trend_alignment,
                    }
                }
            },
            output_payload={
                "symbol": symbol,
                "timeframe": timeframe,
                "decision": "long",
                "entry_mode": entry_mode,
                "rationale_codes": reason_codes,
                "confidence": 0.62,
                "risk_pct": 0.01,
                "leverage": 2.0,
            },
            metadata_json={},
            schema_valid=True,
            started_at=created_at,
            completed_at=created_at,
            created_at=created_at,
            updated_at=created_at,
        )
        db_session.add(decision_row)
        db_session.flush()
        risk_row = RiskCheck(
            symbol=symbol,
            decision_run_id=decision_row.id,
            market_snapshot_id=None,
            allowed=True,
            decision="long",
            reason_codes=[],
            approved_risk_pct=0.01,
            approved_leverage=2.0,
            payload={"allowed": True},
            created_at=created_at,
            updated_at=created_at,
        )
        db_session.add(risk_row)
        db_session.flush()
        order_row = Order(
            symbol=symbol,
            decision_run_id=decision_row.id,
            risk_check_id=risk_row.id,
            position_id=None,
            side="buy",
            order_type="market",
            mode="live",
            status="filled",
            requested_quantity=0.01,
            requested_price=100.0,
            filled_quantity=0.01,
            average_fill_price=100.0,
            reason_codes=[],
            metadata_json={},
            created_at=created_at,
            updated_at=created_at,
        )
        db_session.add(order_row)
        db_session.flush()
        fee_paid = 1.0
        execution_row = Execution(
            order_id=order_row.id,
            position_id=None,
            symbol=symbol,
            status="filled",
            external_trade_id=f"trade-{decision_row.id}",
            fill_price=100.0,
            fill_quantity=0.01,
            fee_paid=fee_paid,
            commission_asset="USDT",
            slippage_pct=abs(float(slippage_values[index])) / 10000.0,
            realized_pnl=float(net_pnl) + fee_paid,
            payload={
                "signed_slippage_bps": float(slippage_values[index]),
                "signed_slippage_pct": float(slippage_values[index]) / 10000.0,
            },
            created_at=created_at,
            updated_at=created_at,
        )
        db_session.add(execution_row)
    db_session.flush()


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
    assert "DERIVATIVES_CONTEXT_UNAVAILABLE" in features.data_quality_flags
    assert features.derivatives.available is False
    assert features.derivatives.spread_bps is None
    assert features.derivatives.spread_headwind is False
    assert features.derivatives.top_trader_long_short_ratio is None
    assert features.derivatives.spread_stress_score is None
    assert features.derivatives.spread_stress is False
    assert features.derivatives.entry_veto_reason_codes == []


def test_compute_features_builds_derivatives_alignment_context() -> None:
    base = _snapshot(
        "15m",
        [100, 100.4, 100.6, 100.8, 100.7, 101.0, 101.2, 101.1, 101.4, 101.6, 101.8, 102.0, 102.2, 102.1, 102.4, 103.1],
        volumes=[900, 920, 940, 960, 955, 980, 1000, 1020, 1040, 1060, 1200, 1260, 1320, 1380, 1440, 1500],
        derivatives_context=DerivativesContextPayload(
            source="binance_public",
            open_interest=152300.0,
            open_interest_change_pct=2.4,
            funding_rate=0.0001,
            taker_buy_sell_imbalance=0.26,
            perp_basis_bps=6.2,
            crowding_bias=0.08,
            top_trader_long_short_ratio=1.08,
            best_bid=103.0,
            best_ask=103.03,
            spread_bps=2.9,
            spread_stress_score=0.72,
        ),
    )
    h1 = _snapshot("1h", [98, 98.7, 99.4, 100.1, 100.9, 101.8, 102.7, 103.6, 104.4, 105.2, 106.1, 107.0, 108.0, 109.1, 110.2, 111.4])
    h4 = _snapshot("4h", [92, 93.4, 94.9, 96.6, 98.4, 100.1, 102.0, 104.1, 106.4, 108.8, 111.3, 114.0, 116.8, 119.7, 122.7, 125.8])

    features = compute_features(base, {"1h": h1, "4h": h4})

    assert features.derivatives.available is True
    assert features.derivatives.oi_expanding_with_price is True
    assert features.derivatives.taker_flow_alignment == "bullish"
    assert features.derivatives.spread_bps == 2.9
    assert features.derivatives.spread_headwind is False
    assert features.derivatives.top_trader_long_crowded is False
    assert features.derivatives.spread_stress is False
    assert features.derivatives.long_discount_magnitude < 0.08
    assert features.derivatives.long_alignment_score > features.derivatives.short_alignment_score


def test_compute_features_marks_spread_headwind_for_breakout_without_oi_expansion() -> None:
    base = _snapshot(
        "15m",
        [100.0, 100.3, 100.6, 100.9, 101.2, 101.6, 102.0, 102.4, 102.9, 103.5, 104.2, 104.9, 105.7, 106.6, 107.6, 109.2],
        volumes=[900, 930, 960, 990, 1030, 1060, 1090, 1120, 1160, 1200, 1250, 1310, 1380, 1460, 1550, 1650],
        derivatives_context=DerivativesContextPayload(
            source="binance_public",
            open_interest=175000.0,
            open_interest_change_pct=-0.4,
            funding_rate=0.0002,
            taker_buy_sell_imbalance=0.18,
            perp_basis_bps=5.4,
            crowding_bias=0.08,
            top_trader_long_short_ratio=1.24,
            best_bid=109.1,
            best_ask=109.16,
            spread_bps=5.5,
            spread_stress_score=1.34,
        ),
    )
    features = compute_features(
        base,
        {
            "1h": _snapshot("1h", [96.0, 96.7, 97.5, 98.3, 99.2, 100.1, 101.1, 102.2, 103.4, 104.7, 106.1, 107.6, 109.2, 110.9, 112.7, 114.6]),
            "4h": _snapshot("4h", [88.0, 89.6, 91.4, 93.3, 95.4, 97.7, 100.1, 102.8, 105.7, 108.8, 112.2, 115.8, 119.7, 123.9, 128.4, 133.2]),
        },
    )

    assert features.derivatives.available is True
    assert features.derivatives.spread_headwind is True
    assert features.derivatives.breakout_spread_headwind is True
    assert features.derivatives.oi_expanding_with_price is False
    assert features.derivatives.spread_stress is True
    assert "BREAKOUT_SPREAD_STRESS" in features.derivatives.breakout_veto_reason_codes
    assert features.derivatives.long_discount_magnitude > 0.0


def test_compute_features_marks_top_trader_crowding_and_spread_stress_as_entry_veto() -> None:
    base = _snapshot(
        "15m",
        [100, 100.4, 100.7, 101.0, 101.3, 101.7, 102.1, 102.6, 103.1, 103.7, 104.3, 104.9, 105.6, 106.4, 107.3, 108.3],
        volumes=[920, 950, 980, 1010, 1040, 1080, 1120, 1170, 1230, 1300, 1380, 1470, 1570, 1680, 1800, 1930],
        derivatives_context=DerivativesContextPayload(
            source="binance_public",
            open_interest=165000.0,
            open_interest_change_pct=0.3,
            funding_rate=0.0014,
            taker_buy_sell_imbalance=-0.08,
            perp_basis_bps=8.6,
            crowding_bias=0.26,
            top_trader_long_short_ratio=1.42,
            best_bid=108.2,
            best_ask=108.31,
            spread_bps=10.2,
            spread_stress_score=1.66,
        ),
    )
    features = compute_features(
        base,
        {
            "1h": _snapshot("1h", [96.0, 96.8, 97.7, 98.7, 99.8, 101.0, 102.3, 103.7, 105.2, 106.8, 108.5, 110.3, 112.2, 114.2, 116.3, 118.5]),
            "4h": _snapshot("4h", [88.0, 89.9, 92.0, 94.3, 96.8, 99.5, 102.4, 105.6, 109.0, 112.7, 116.7, 120.9, 125.4, 130.2, 135.3, 140.7]),
        },
    )

    assert features.derivatives.top_trader_long_crowded is True
    assert features.derivatives.spread_stress is True
    assert "TOP_TRADER_LONG_CROWDED" in features.derivatives.entry_veto_reason_codes
    assert "SPREAD_STRESS" in features.derivatives.entry_veto_reason_codes
    assert features.derivatives.long_discount_magnitude >= 0.18


def test_compute_features_builds_bullish_lead_lag_alignment_context() -> None:
    btc_base = _snapshot("15m", [100, 100.4, 100.7, 101.1, 101.6, 102.2, 102.9, 103.7, 104.6, 105.4, 106.3, 107.3, 108.4, 109.6, 110.9, 112.3]).model_copy(
        update={"symbol": "BTCUSDT"}
    )
    eth_base = _snapshot("15m", [80, 80.3, 80.7, 81.1, 81.6, 82.2, 82.9, 83.7, 84.6, 85.5, 86.4, 87.4, 88.5, 89.7, 91.0, 92.4]).model_copy(
        update={"symbol": "ETHUSDT"}
    )
    alt_base = _snapshot("15m", [50, 50.1, 50.3, 50.6, 50.8, 51.1, 51.4, 51.7, 52.0, 52.3, 52.6, 52.9, 53.2, 53.5, 53.9, 54.3]).model_copy(
        update={"symbol": "SOLUSDT"}
    )

    btc_features = compute_features(
        btc_base,
        {
            "1h": _snapshot("1h", [96, 96.8, 97.7, 98.7, 99.8, 101.0, 102.3, 103.7, 105.2, 106.8, 108.5, 110.3, 112.2, 114.2, 116.3, 118.5]).model_copy(update={"symbol": "BTCUSDT"}),
            "4h": _snapshot("4h", [88, 89.9, 92.0, 94.3, 96.8, 99.5, 102.4, 105.6, 109.0, 112.7, 116.7, 120.9, 125.4, 130.2, 135.3, 140.7]).model_copy(update={"symbol": "BTCUSDT"}),
        },
    )
    eth_features = compute_features(
        eth_base,
        {
            "1h": _snapshot("1h", [76, 76.7, 77.5, 78.4, 79.4, 80.5, 81.7, 83.0, 84.4, 85.9, 87.5, 89.2, 91.0, 92.9, 94.9, 97.0]).model_copy(update={"symbol": "ETHUSDT"}),
            "4h": _snapshot("4h", [70, 71.2, 72.6, 74.1, 75.8, 77.7, 79.8, 82.1, 84.6, 87.3, 90.2, 93.3, 96.7, 100.3, 104.1, 108.2]).model_copy(update={"symbol": "ETHUSDT"}),
        },
    )
    alt_features = compute_features(
        alt_base,
        {
            "1h": _snapshot("1h", [48.0, 48.2, 48.5, 48.8, 49.1, 49.5, 49.9, 50.4, 50.9, 51.4, 52.0, 52.6, 53.2, 53.9, 54.7, 55.5]).model_copy(update={"symbol": "SOLUSDT"}),
            "4h": _snapshot("4h", [44.0, 44.5, 45.1, 45.8, 46.6, 47.5, 48.5, 49.6, 50.8, 52.1, 53.5, 55.0, 56.6, 58.3, 60.1, 62.0]).model_copy(update={"symbol": "SOLUSDT"}),
        },
        lead_market_features={"BTCUSDT": btc_features, "ETHUSDT": eth_features},
    )

    assert alt_features.lead_lag.available is True
    assert alt_features.lead_lag.leader_bias == "bullish"
    assert alt_features.lead_lag.reference_symbols == ["BTCUSDT", "ETHUSDT"]
    assert alt_features.lead_lag.bullish_alignment_score > alt_features.lead_lag.bearish_alignment_score
    assert alt_features.lead_lag.bullish_continuation_supported or alt_features.lead_lag.bullish_pullback_supported


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
    bullish_features.breakout.broke_swing_high = False
    bullish_features.breakout.range_breakout_direction = "none"

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


def test_trading_agent_applies_setup_specific_time_profiles() -> None:
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
    continuation_decision, _, _ = _agent().run(
        bullish_base,
        bullish_features,
        [],
        _risk_context(),
        use_ai=False,
        max_input_candles=16,
    )
    continuation_timing = _agent()._build_entry_timing_profile(  # type: ignore[attr-defined]
        "long",
        market_snapshot=bullish_base,
        features=bullish_features,
        entry_mode="pullback_confirm",
    )

    pullback_features = bullish_features.model_copy(deep=True)
    pullback_features.pullback_context.state = "bullish_pullback"
    pullback_defaults = _agent()._build_entry_trigger_defaults(  # type: ignore[attr-defined]
        "long",
        market_snapshot=bullish_base,
        features=pullback_features,
        stop_loss=continuation_decision.stop_loss,
    )

    breakout_timing = _agent()._build_entry_timing_profile(  # type: ignore[attr-defined]
        "long",
        market_snapshot=bullish_base,
        features=pullback_features,
        entry_mode="breakout_confirm",
    )

    assert continuation_decision.entry_mode == "pullback_confirm"
    assert continuation_timing["max_holding_minutes"] > 90
    assert continuation_timing["idea_ttl_minutes"] >= 12
    assert "SETUP_TIME_PROFILE_CONTINUATION_BALANCED" in continuation_decision.rationale_codes
    assert pullback_defaults["max_holding_minutes"] > continuation_timing["max_holding_minutes"]
    assert pullback_defaults["idea_ttl_minutes"] > continuation_timing["idea_ttl_minutes"]
    assert breakout_timing["max_holding_minutes"] < continuation_timing["max_holding_minutes"]
    assert breakout_timing["idea_ttl_minutes"] < continuation_timing["idea_ttl_minutes"]
    assert breakout_timing["profile_rationale_code"] == "SETUP_TIME_PROFILE_BREAKOUT_FAST"


def test_trading_agent_defaults_to_scalp_under_weak_regime() -> None:
    base = _snapshot(
        "15m",
        [100.0, 100.05, 99.98, 100.02, 100.01, 99.99, 100.0, 100.03, 99.97, 100.01, 100.0, 99.98, 100.02, 99.99, 100.01, 100.0],
        volumes=[420, 430, 410, 405, 400, 395, 390, 400, 410, 405, 398, 392, 388, 384, 380, 376],
    )
    features = compute_features(
        base,
        {
            "1h": _snapshot("1h", [100.0, 100.1, 100.0, 99.95, 100.0, 100.05, 100.0, 99.98, 100.02, 100.0, 99.99, 100.01, 100.0, 99.97, 100.01, 100.0]),
            "4h": _snapshot("4h", [100.0, 100.1, 100.0, 99.98, 100.01, 100.0, 99.99, 100.02, 100.01, 100.0, 99.98, 100.01, 100.0, 99.99, 100.0, 100.01]),
        },
    )
    features.regime.primary_regime = "range"
    features.regime.trend_alignment = "neutral"
    features.regime.weak_volume = True
    features.regime.momentum_weakening = True

    decision, _, metadata = _agent().run(
        base,
        features,
        [],
        _risk_context(),
        use_ai=False,
        max_input_candles=16,
    )

    assert decision.holding_profile == "scalp"
    assert decision.decision == "hold"
    assert metadata["holding_profile"] == "scalp"
    assert metadata["holding_profile_context"]["holding_profile"] == "scalp"
    assert "HOLDING_PROFILE_SCALP_DEFAULT" in decision.rationale_codes


def test_trading_agent_only_allows_position_profile_under_strong_structural_regime() -> None:
    bullish_base = _snapshot(
        "15m",
        [100, 100.6, 101.1, 101.7, 102.4, 103.0, 103.8, 104.5, 105.3, 106.0, 106.8, 107.6, 108.5, 109.3, 110.2, 111.2],
        volumes=[980, 1005, 1030, 1060, 1090, 1125, 1160, 1195, 1230, 1270, 1310, 1350, 1395, 1440, 1490, 1540],
    )
    bullish_features = compute_features(
        bullish_base,
        {
            "1h": _snapshot("1h", [96, 97.4, 98.9, 100.5, 102.2, 104.0, 106.1, 108.4, 110.9, 113.5, 116.3, 119.2, 122.2, 125.4, 128.7, 132.1]),
            "4h": _snapshot("4h", [88, 90.1, 92.5, 95.2, 98.3, 101.7, 105.5, 109.6, 114.1, 118.9, 124.1, 129.6, 135.4, 141.5, 147.9, 154.6]),
        },
    )
    bullish_features.lead_lag.available = True
    bullish_features.lead_lag.bullish_alignment_score = 0.84
    bullish_features.lead_lag.bullish_pullback_supported = True
    bullish_features.lead_lag.bullish_continuation_supported = True
    bullish_features.lead_lag.strong_reference_confirmation = True
    bullish_features.derivatives.available = True
    bullish_features.derivatives.long_alignment_score = 0.78
    bullish_features.derivatives.short_alignment_score = 0.22
    bullish_features.derivatives.funding_bias = "neutral"
    bullish_features.derivatives.spread_headwind = False
    bullish_features.derivatives.spread_stress = False
    bullish_features.derivatives.crowded_long_risk = False
    bullish_features.derivatives.top_trader_long_crowded = False

    agent = _agent()
    updated_decision, holding_profile_context = agent._apply_holding_profile_fields(  # type: ignore[attr-defined]
        TradeDecision(
            decision="long",
            confidence=0.82,
            symbol="BTCUSDT",
            timeframe="15m",
            entry_zone_min=bullish_base.latest_price - 25.0,
            entry_zone_max=bullish_base.latest_price - 5.0,
            entry_mode="pullback_confirm",
            invalidation_price=bullish_base.latest_price - 20.0,
            max_chase_bps=4.0,
            idea_ttl_minutes=15,
            stop_loss=bullish_base.latest_price - 80.0,
            take_profit=bullish_base.latest_price + 240.0,
            max_holding_minutes=180,
            risk_pct=0.01,
            leverage=2.0,
            rationale_codes=["TREND_UP", "ALIGNED_PULLBACK"],
            explanation_short="strong structural trend",
            explanation_detailed="강한 구조 정렬 안에서 눌림 진입을 테스트합니다.",
        ),
        market_snapshot=bullish_base,
        features=bullish_features,
        risk_context={
            **_risk_context(),
            "selection_context": {
                "universe_breadth": {
                    "breadth_regime": "trend_expansion",
                }
            },
        },
        strategy_engine_selection={"selected_engine": {"engine_name": "trend_pullback_engine"}},
    )

    assert updated_decision.decision == "long"
    assert updated_decision.holding_profile == "position"
    assert updated_decision.max_holding_minutes >= 200
    assert "HOLDING_PROFILE_POSITION_ALLOWED" in updated_decision.rationale_codes
    assert holding_profile_context["position_profile_eligible"] is True
    assert holding_profile_context["holding_profile"] == "position"


def test_trading_agent_holds_when_long_derivatives_headwind_is_strong() -> None:
    bullish_base = _snapshot(
        "15m",
        [100, 100.4, 100.6, 100.8, 100.7, 101.0, 101.2, 101.1, 101.4, 101.6, 101.8, 102.0, 102.2, 102.1, 102.4, 103.1],
        volumes=[900, 920, 940, 960, 955, 980, 1000, 1020, 1040, 1060, 1090, 1120, 1150, 1180, 1220, 1260],
        derivatives_context=DerivativesContextPayload(
            source="binance_public",
            open_interest=180000.0,
            open_interest_change_pct=0.6,
            funding_rate=0.0011,
            taker_buy_sell_imbalance=-0.24,
            perp_basis_bps=9.0,
            crowding_bias=0.34,
        ),
    )
    bullish_features = compute_features(
        bullish_base,
        {
            "1h": _snapshot("1h", [98, 98.7, 99.4, 100.1, 100.9, 101.8, 102.7, 103.6, 104.4, 105.2, 106.1, 107.0, 108.0, 109.1, 110.2, 111.4]),
            "4h": _snapshot("4h", [92, 93.4, 94.9, 96.6, 98.4, 100.1, 102.0, 104.1, 106.4, 108.8, 111.3, 114.0, 116.8, 119.7, 122.7, 125.8]),
        },
    )
    bullish_features.breakout.broke_swing_high = False
    bullish_features.breakout.range_breakout_direction = "none"

    decision, _, _ = _agent().run(
        bullish_base,
        bullish_features,
        [],
        _risk_context(),
        use_ai=False,
        max_input_candles=16,
    )

    assert decision.decision == "hold"
    assert "DERIVATIVES_ALIGNMENT_HEADWIND" in decision.rationale_codes
    assert "CROWDED_LONG_RISK" in decision.rationale_codes
    assert "TAKER_FLOW_DIVERGENCE" in decision.rationale_codes


def test_trading_agent_holds_when_funding_and_spread_headwind_align() -> None:
    bullish_base = _snapshot(
        "15m",
        [100, 100.4, 100.6, 100.8, 100.7, 101.0, 101.2, 101.1, 101.4, 101.6, 101.8, 102.0, 102.2, 102.1, 102.4, 103.1],
        volumes=[900, 920, 940, 960, 955, 980, 1000, 1020, 1040, 1060, 1090, 1120, 1150, 1180, 1220, 1260],
        derivatives_context=DerivativesContextPayload(
            source="binance_public",
            open_interest=180000.0,
            open_interest_change_pct=0.5,
            funding_rate=0.0012,
            taker_buy_sell_imbalance=0.06,
            perp_basis_bps=7.2,
            crowding_bias=0.08,
            top_trader_long_short_ratio=1.5,
            best_bid=103.0,
            best_ask=103.09,
            spread_bps=8.7,
            spread_stress_score=1.58,
        ),
    )
    bullish_features = compute_features(
        bullish_base,
        {
            "1h": _snapshot("1h", [98, 98.7, 99.4, 100.1, 100.9, 101.8, 102.7, 103.6, 104.4, 105.2, 106.1, 107.0, 108.0, 109.1, 110.2, 111.4]),
            "4h": _snapshot("4h", [92, 93.4, 94.9, 96.6, 98.4, 100.1, 102.0, 104.1, 106.4, 108.8, 111.3, 114.0, 116.8, 119.7, 122.7, 125.8]),
        },
    )
    bullish_features.breakout.broke_swing_high = False
    bullish_features.breakout.range_breakout_direction = "none"

    decision, _, _ = _agent().run(
        bullish_base,
        bullish_features,
        [],
        _risk_context(),
        use_ai=False,
        max_input_candles=16,
    )

    assert decision.decision == "hold"
    assert "DERIVATIVES_ALIGNMENT_HEADWIND" in decision.rationale_codes
    assert "FUNDING_HEADWIND" in decision.rationale_codes
    assert "SPREAD_HEADWIND" in decision.rationale_codes
    assert "TOP_TRADER_LONG_CROWDED" in decision.rationale_codes
    assert "SPREAD_STRESS" in decision.rationale_codes


def test_trading_agent_requires_derivatives_confirmation_for_breakout_exception() -> None:
    breakout_base = _snapshot(
        "15m",
        [100.0, 100.3, 100.6, 100.9, 101.2, 101.6, 102.0, 102.4, 102.9, 103.5, 104.2, 104.9, 105.7, 106.6, 107.6, 109.2],
        volumes=[900, 930, 960, 990, 1030, 1060, 1090, 1120, 1160, 1200, 1250, 1310, 1380, 1460, 1550, 1650],
        derivatives_context=DerivativesContextPayload(
            source="binance_public",
            open_interest=175000.0,
            open_interest_change_pct=-1.1,
            funding_rate=0.0009,
            taker_buy_sell_imbalance=-0.18,
            perp_basis_bps=8.4,
            crowding_bias=0.28,
        ),
    )
    breakout_features = compute_features(
        breakout_base,
        {
            "1h": _snapshot("1h", [96.0, 96.7, 97.5, 98.3, 99.2, 100.1, 101.1, 102.2, 103.4, 104.7, 106.1, 107.6, 109.2, 110.9, 112.7, 114.6]),
            "4h": _snapshot("4h", [88.0, 89.6, 91.4, 93.3, 95.4, 97.7, 100.1, 102.8, 105.7, 108.8, 112.2, 115.8, 119.7, 123.9, 128.4, 133.2]),
        },
    )
    breakout_features.pullback_context.state = "unclear"
    breakout_features.breakout.broke_swing_high = True
    breakout_features.breakout.range_breakout_direction = "up"
    breakout_features.trend_score = 0.46
    breakout_features.momentum_score = 0.31
    breakout_features.regime.primary_regime = "bullish"
    breakout_features.regime.trend_alignment = "bullish_aligned"
    breakout_features.regime.momentum_state = "strengthening"
    breakout_features.regime.weak_volume = False
    breakout_features.volume_persistence.persistence_ratio = 1.08

    blocked_decision, _, _ = _agent().run(
        breakout_base,
        breakout_features,
        [],
        _risk_context(),
        use_ai=False,
        max_input_candles=16,
    )

    breakout_features.derivatives = breakout_features.derivatives.model_copy(
        update={
            "oi_expanding_with_price": True,
            "oi_falling_on_breakout": False,
            "taker_flow_alignment": "bullish",
            "funding_bias": "neutral",
            "basis_bias": "bullish",
            "crowded_long_risk": False,
            "top_trader_long_crowded": False,
            "long_alignment_score": 0.82,
            "spread_stress": False,
            "spread_stress_score": 0.8,
            "breakout_veto_reason_codes": [],
        }
    )

    confirmed_decision, _, _ = _agent().run(
        breakout_base,
        breakout_features,
        [],
        _risk_context(),
        use_ai=False,
        max_input_candles=16,
    )

    assert blocked_decision.decision == "hold"
    assert "DERIVATIVES_ALIGNMENT_HEADWIND" in blocked_decision.rationale_codes
    assert "BREAKOUT_OI_SPREAD_FILTER" in blocked_decision.rationale_codes
    assert "BREAKOUT_OI_NOT_EXPANDING" in blocked_decision.rationale_codes
    assert confirmed_decision.decision == "long"
    assert "STRUCTURE_BREAKOUT_UP_EXCEPTION" in confirmed_decision.rationale_codes


def test_trading_agent_holds_breakout_when_oi_is_not_expanding_and_spread_is_wide() -> None:
    breakout_base = _snapshot(
        "15m",
        [100.0, 100.3, 100.6, 100.9, 101.2, 101.6, 102.0, 102.4, 102.9, 103.5, 104.2, 104.9, 105.7, 106.6, 107.6, 109.2],
        volumes=[900, 930, 960, 990, 1030, 1060, 1090, 1120, 1160, 1200, 1250, 1310, 1380, 1460, 1550, 1650],
    )
    breakout_features = compute_features(
        breakout_base,
        {
            "1h": _snapshot("1h", [96.0, 96.7, 97.5, 98.3, 99.2, 100.1, 101.1, 102.2, 103.4, 104.7, 106.1, 107.6, 109.2, 110.9, 112.7, 114.6]),
            "4h": _snapshot("4h", [88.0, 89.6, 91.4, 93.3, 95.4, 97.7, 100.1, 102.8, 105.7, 108.8, 112.2, 115.8, 119.7, 123.9, 128.4, 133.2]),
        },
    )
    breakout_features.pullback_context.state = "unclear"
    breakout_features.breakout.broke_swing_high = True
    breakout_features.breakout.range_breakout_direction = "up"
    breakout_features.trend_score = 0.46
    breakout_features.momentum_score = 0.31
    breakout_features.regime.primary_regime = "bullish"
    breakout_features.regime.trend_alignment = "bullish_aligned"
    breakout_features.regime.momentum_state = "strengthening"
    breakout_features.regime.weak_volume = False
    breakout_features.volume_persistence.persistence_ratio = 1.08
    breakout_features.derivatives = breakout_features.derivatives.model_copy(
        update={
            "available": True,
            "oi_expanding_with_price": False,
            "oi_falling_on_breakout": True,
            "taker_flow_alignment": "bullish",
            "funding_bias": "neutral",
            "basis_bias": "bullish",
            "crowded_long_risk": False,
            "top_trader_long_crowded": True,
            "long_alignment_score": 0.76,
            "spread_bps": 6.2,
            "spread_headwind": True,
            "spread_stress": True,
            "spread_stress_score": 1.54,
            "breakout_spread_headwind": True,
            "breakout_veto_reason_codes": ["BREAKOUT_OI_NOT_EXPANDING", "BREAKOUT_SPREAD_STRESS"],
        }
    )

    decision, _, _ = _agent().run(
        breakout_base,
        breakout_features,
        [],
        _risk_context(),
        use_ai=False,
        max_input_candles=16,
    )

    assert decision.decision == "hold"
    assert "DERIVATIVES_ALIGNMENT_HEADWIND" in decision.rationale_codes
    assert "SPREAD_HEADWIND" in decision.rationale_codes
    assert "BREAKOUT_OI_SPREAD_FILTER" in decision.rationale_codes
    assert "TOP_TRADER_LONG_CROWDED" in decision.rationale_codes
    assert "SPREAD_STRESS" in decision.rationale_codes


def test_trading_agent_holds_when_alt_breakout_runs_ahead_of_btc_eth() -> None:
    btc_base = _snapshot("15m", [100.0, 100.3, 100.6, 100.9, 101.3, 101.7, 102.1, 102.5, 103.0, 103.6, 104.2, 104.9, 105.6, 106.4, 107.2, 108.1]).model_copy(
        update={"symbol": "BTCUSDT"}
    )
    eth_base = _snapshot("15m", [80.0, 80.2, 80.5, 80.8, 81.2, 81.6, 82.0, 82.5, 83.0, 83.6, 84.2, 84.9, 85.6, 86.4, 87.3, 88.2]).model_copy(
        update={"symbol": "ETHUSDT"}
    )
    lead_market_features = {
        "BTCUSDT": compute_features(
            btc_base,
            {
                "1h": _snapshot("1h", [96.0, 96.6, 97.3, 98.1, 99.0, 100.0, 101.1, 102.3, 103.6, 105.0, 106.5, 108.1, 109.8, 111.6, 113.5, 115.5]).model_copy(update={"symbol": "BTCUSDT"}),
                "4h": _snapshot("4h", [88.0, 89.5, 91.1, 92.9, 94.8, 96.9, 99.2, 101.7, 104.4, 107.3, 110.4, 113.7, 117.2, 121.0, 125.0, 129.2]).model_copy(update={"symbol": "BTCUSDT"}),
            },
        ),
        "ETHUSDT": compute_features(
            eth_base,
            {
                "1h": _snapshot("1h", [76.0, 76.5, 77.1, 77.8, 78.6, 79.5, 80.5, 81.6, 82.8, 84.1, 85.5, 87.0, 88.6, 90.3, 92.1, 94.0]).model_copy(update={"symbol": "ETHUSDT"}),
                "4h": _snapshot("4h", [70.0, 71.0, 72.2, 73.5, 74.9, 76.5, 78.2, 80.1, 82.2, 84.5, 87.0, 89.7, 92.6, 95.7, 99.0, 102.5]).model_copy(update={"symbol": "ETHUSDT"}),
            },
        ),
    }
    for symbol in ("BTCUSDT", "ETHUSDT"):
        lead_market_features[symbol].breakout.broke_swing_high = False
        lead_market_features[symbol].breakout.range_breakout_direction = "none"

    alt_base = _snapshot(
        "15m",
        [50.0, 50.3, 50.6, 51.0, 51.5, 52.1, 52.8, 53.6, 54.5, 55.5, 56.6, 57.8, 59.1, 60.5, 62.0, 63.8],
        volumes=[900, 930, 960, 990, 1030, 1070, 1110, 1160, 1220, 1290, 1370, 1460, 1560, 1670, 1790, 1920],
    ).model_copy(update={"symbol": "SOLUSDT"})
    alt_features = compute_features(
        alt_base,
        {
            "1h": _snapshot("1h", [47.5, 47.9, 48.4, 48.9, 49.5, 50.2, 51.0, 51.9, 52.9, 54.0, 55.2, 56.5, 57.9, 59.4, 61.0, 62.7]).model_copy(update={"symbol": "SOLUSDT"}),
            "4h": _snapshot("4h", [44.0, 44.7, 45.5, 46.4, 47.4, 48.5, 49.7, 51.0, 52.4, 53.9, 55.5, 57.2, 59.0, 60.9, 62.9, 65.0]).model_copy(update={"symbol": "SOLUSDT"}),
        },
        lead_market_features=lead_market_features,
    )
    alt_features.pullback_context.state = "bullish_continuation"
    alt_features.breakout.broke_swing_high = True
    alt_features.breakout.range_breakout_direction = "up"
    alt_features.trend_score = 0.48
    alt_features.momentum_score = 0.34
    alt_features.rsi = 64.0
    alt_features.regime.primary_regime = "bullish"
    alt_features.regime.trend_alignment = "bullish_aligned"
    alt_features.regime.momentum_state = "strengthening"
    alt_features.regime.weak_volume = False
    alt_features.volume_persistence.persistence_ratio = 1.1
    alt_features.location.vwap_distance_pct = 1.2
    alt_features.candle_structure.body_ratio = 0.72
    alt_features.candle_structure.upper_wick_ratio = 0.04
    alt_features.candle_structure.lower_wick_ratio = 0.12

    decision, _, _ = _agent().run(
        alt_base,
        alt_features,
        [],
        _risk_context(),
        use_ai=False,
        max_input_candles=16,
    )

    assert decision.decision == "hold"
    assert "LEAD_MARKET_DIVERGENCE" in decision.rationale_codes
    assert "ALT_BREAKOUT_AHEAD_OF_LEADS" in decision.rationale_codes


def test_trading_agent_normalizes_ai_immediate_entry_into_pullback_confirm() -> None:
    class ImmediateProvider:
        name = "openai"

        def generate(self, role, payload, *, response_model, instructions):  # noqa: ANN001
            return ProviderResult(
                provider="openai",
                output={
                    "decision": "long",
                    "confidence": 0.68,
                    "symbol": payload["market_snapshot"]["symbol"],
                    "timeframe": payload["market_snapshot"]["timeframe"],
                    "entry_zone_min": payload["market_snapshot"]["latest_price"] - 80.0,
                    "entry_zone_max": payload["market_snapshot"]["latest_price"] - 20.0,
                    "entry_mode": "immediate",
                    "invalidation_price": payload["market_snapshot"]["latest_price"] - 4.0,
                    "max_chase_bps": 15.0,
                    "idea_ttl_minutes": 15,
                    "stop_loss": payload["market_snapshot"]["latest_price"] - 4.0,
                    "take_profit": payload["market_snapshot"]["latest_price"] + 7.0,
                    "max_holding_minutes": 180,
                    "risk_pct": 0.01,
                    "leverage": 2.0,
                    "rationale_codes": ["TREND_UP", "ALIGNED_PULLBACK"],
                    "explanation_short": "즉시 진입 대신 눌림 확인이 더 적절합니다.",
                    "explanation_detailed": "LLM이 immediate를 제안했더라도 신규 진입은 pullback_confirm 위주로 정규화되어야 합니다.",
                },
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            )

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

    decision, provider_name, _ = TradingDecisionAgent(ImmediateProvider()).run(
        bullish_base,
        bullish_features,
        [],
        _risk_context(),
        use_ai=True,
        max_input_candles=16,
    )

    assert provider_name == "openai"
    assert decision.decision == "long"
    assert decision.entry_mode == "pullback_confirm"
    assert decision.max_chase_bps == 4.0
    assert "PROVIDER_OPENAI" in decision.rationale_codes


def test_trading_agent_enforces_deterministic_hard_stop_over_ai_stop_loss() -> None:
    class ImmediateProvider:
        name = "openai"

        def generate(self, role, payload, *, response_model, instructions):  # noqa: ANN001
            latest_price = float(payload["market_snapshot"]["latest_price"])
            return ProviderResult(
                provider="openai",
                output={
                    "decision": "long",
                    "confidence": 0.69,
                    "symbol": payload["market_snapshot"]["symbol"],
                    "timeframe": payload["market_snapshot"]["timeframe"],
                    "entry_zone_min": latest_price - 80.0,
                    "entry_zone_max": latest_price - 20.0,
                    "entry_mode": "immediate",
                    "invalidation_price": latest_price - 4.0,
                    "max_chase_bps": 15.0,
                    "idea_ttl_minutes": 15,
                    "stop_loss": latest_price - 4.0,
                    "take_profit": latest_price + 7.0,
                    "max_holding_minutes": 180,
                    "risk_pct": 0.01,
                    "leverage": 2.0,
                    "rationale_codes": ["TREND_UP", "ALIGNED_PULLBACK"],
                    "explanation_short": "LLM stop override test",
                    "explanation_detailed": "AI가 제안한 stop_loss보다 deterministic hard stop이 우선되어야 합니다.",
                },
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            )

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
    agent = TradingDecisionAgent(ImmediateProvider())
    latest_price = float(bullish_base.latest_price)
    reference_price = ((latest_price - 80.0) + (latest_price - 20.0)) / 2
    expected_stop_loss, _ = agent._adaptive_brackets(  # type: ignore[attr-defined]
        "long",
        price=reference_price,
        atr=bullish_features.atr,
        features=bullish_features,
    )

    decision, _, metadata = agent.run(
        bullish_base,
        bullish_features,
        [],
        _risk_context(),
        use_ai=True,
        max_input_candles=16,
    )

    assert decision.entry_mode == "pullback_confirm"
    assert decision.stop_loss == pytest.approx(expected_stop_loss)
    assert decision.take_profit > reference_price
    assert decision.stop_loss != pytest.approx(latest_price - 4.0)
    assert "DETERMINISTIC_HARD_STOP_ACTIVE" in decision.rationale_codes
    assert metadata["initial_stop_type"] == "deterministic_hard_stop"
    assert metadata["hard_stop_active"] is True
    assert metadata["ai_stop_management_allowed"] is True
    assert metadata["stop_widening_allowed"] is False


def test_trading_agent_emits_decision_agreement_levels_for_ai_output() -> None:
    class FixedDecisionProvider:
        name = "openai"

        def __init__(self, *, decision: str, entry_mode: str) -> None:
            self.decision = decision
            self.entry_mode = entry_mode

        def generate(self, role, payload, *, response_model, instructions):  # noqa: ANN001
            latest_price = float(payload["market_snapshot"]["latest_price"])
            if self.decision == "hold":
                output = {
                    "decision": "hold",
                    "confidence": 0.52,
                    "symbol": payload["market_snapshot"]["symbol"],
                    "timeframe": payload["market_snapshot"]["timeframe"],
                    "entry_zone_min": None,
                    "entry_zone_max": None,
                    "entry_mode": "none",
                    "invalidation_price": None,
                    "max_chase_bps": None,
                    "idea_ttl_minutes": None,
                    "stop_loss": None,
                    "take_profit": None,
                    "max_holding_minutes": 120,
                    "risk_pct": 0.005,
                    "leverage": 1.0,
                    "rationale_codes": ["NO_EDGE"],
                    "explanation_short": "합의 불일치 테스트",
                    "explanation_detailed": "Deterministic baseline과 반대되는 hold 응답을 반환합니다.",
                }
            else:
                output = {
                    "decision": self.decision,
                    "confidence": 0.68,
                    "symbol": payload["market_snapshot"]["symbol"],
                    "timeframe": payload["market_snapshot"]["timeframe"],
                    "entry_zone_min": latest_price - 80.0,
                    "entry_zone_max": latest_price - 20.0,
                    "entry_mode": self.entry_mode,
                    "invalidation_price": latest_price - 4.0,
                    "max_chase_bps": 8.0,
                    "idea_ttl_minutes": 15,
                    "stop_loss": latest_price - 4.0,
                    "take_profit": latest_price + 7.0,
                    "max_holding_minutes": 180,
                    "risk_pct": 0.01,
                    "leverage": 2.0,
                    "rationale_codes": ["TREND_UP", "ALIGNED_PULLBACK"],
                    "explanation_short": "합의도 테스트",
                    "explanation_detailed": "Deterministic baseline과의 합의도를 검증하기 위한 AI 응답입니다.",
                }
            return ProviderResult(
                provider="openai",
                output=output,
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            )

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
    baseline = _agent()._deterministic_decision(  # type: ignore[attr-defined]
        bullish_base,
        bullish_features,
        [],
        _risk_context(),
        logic_variant="improved",
    )
    partial_entry_mode = "breakout_confirm" if (baseline.entry_mode or "none") != "breakout_confirm" else "pullback_confirm"

    _, _, full_metadata = TradingDecisionAgent(
        FixedDecisionProvider(decision=baseline.decision, entry_mode=baseline.entry_mode or "pullback_confirm")
    ).run(
        bullish_base,
        bullish_features,
        [],
        _risk_context(),
        use_ai=True,
        max_input_candles=16,
    )
    _, _, partial_metadata = TradingDecisionAgent(
        FixedDecisionProvider(decision=baseline.decision, entry_mode=partial_entry_mode)
    ).run(
        bullish_base,
        bullish_features,
        [],
        _risk_context(),
        use_ai=True,
        max_input_candles=16,
    )
    _, _, disagreement_metadata = TradingDecisionAgent(
        FixedDecisionProvider(decision="hold", entry_mode="none")
    ).run(
        bullish_base,
        bullish_features,
        [],
        _risk_context(),
        use_ai=True,
        max_input_candles=16,
    )

    assert full_metadata["decision_agreement"]["level"] == "full_agreement"
    assert full_metadata["decision_agreement"]["direction_match"] is True
    assert full_metadata["decision_agreement"]["entry_mode_match"] is True
    assert partial_metadata["decision_agreement"]["level"] == "partial_agreement"
    assert partial_metadata["decision_agreement"]["direction_match"] is True
    assert partial_metadata["decision_agreement"]["entry_mode_match"] is False
    assert disagreement_metadata["decision_agreement"]["level"] == "disagreement"
    assert disagreement_metadata["decision_agreement"]["direction_match"] is False


def test_trading_agent_holds_when_matching_setup_cluster_is_active() -> None:
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

    decision, _, metadata = _agent().run(
        bullish_base,
        bullish_features,
        [],
        {
            **_risk_context(),
            "setup_cluster_context": _setup_cluster_context(),
        },
        use_ai=False,
        max_input_candles=16,
    )

    assert decision.decision == "long"
    assert "SETUP_CLUSTER_DISABLED" in decision.rationale_codes
    assert metadata["setup_cluster_state"]["matched"] is True
    assert metadata["setup_cluster_state"]["active"] is True
    assert metadata["setup_cluster_state"]["status"] == "active_disabled"
    assert metadata["setup_cluster_state"]["cooldown_active"] is True
    assert metadata["suppression_context"]["level"] == "hard_block"
    assert "setup_cluster_disable" in metadata["suppression_context"]["sources"]


def test_trading_agent_ignores_non_matching_setup_cluster_context() -> None:
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

    cluster_context = _setup_cluster_context()
    cluster_payload = next(iter(cluster_context["cluster_lookup"].values()))
    mismatched_cluster_key = "BTCUSDT|15m|pullback_entry|pullback_confirm|bullish|bearish_aligned"
    cluster_payload["cluster_key"] = mismatched_cluster_key
    cluster_payload["trend_alignment"] = "bearish_aligned"
    cluster_context["cluster_lookup"] = {mismatched_cluster_key: cluster_payload}
    cluster_context["active_cluster_keys"] = [mismatched_cluster_key]

    decision, _, metadata = _agent().run(
        bullish_base,
        bullish_features,
        [],
        {
            **_risk_context(),
            "setup_cluster_context": cluster_context,
        },
        use_ai=False,
        max_input_candles=16,
    )

    assert decision.decision == "long"
    assert "SETUP_CLUSTER_DISABLED" not in decision.rationale_codes
    assert metadata["setup_cluster_state"]["matched"] is False
    assert metadata["setup_cluster_state"]["active"] is False
    assert metadata["setup_cluster_state"]["status"] == "not_matched"


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
    semantics = infer_intent_semantics(
        decision.model_dump(mode="json"),
        {"operating_state": "PROTECTION_REQUIRED"},
    )
    assert semantics["intent_family"] == "protection"
    assert semantics["management_action"] == "restore_protection"
    assert semantics["legacy_semantics_preserved"] is True


def test_trading_agent_allows_winner_only_add_on_for_protected_position() -> None:
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
    bullish_features.rsi = 60.0
    open_position = Position(
        symbol="BTCUSDT",
        mode="live",
        side="long",
        status="open",
        quantity=0.01,
        entry_price=100.0,
        mark_price=102.2,
        leverage=2.0,
        stop_loss=100.4,
        take_profit=106.0,
        realized_pnl=0.0,
        unrealized_pnl=2.2,
    )

    decision, _, _ = _agent().run(
        bullish_base,
        bullish_features,
        [open_position],
        {
            **_risk_context(),
            "position_management_context": {
                "current_r_multiple": 1.2,
                "break_even_eligible": True,
                "tightened_stop_loss": 100.4,
            },
            "selection_context": {
                "universe_breadth": {
                    "breadth_regime": "mixed",
                    "hold_bias_multiplier": 1.0,
                }
            },
        },
        use_ai=False,
        max_input_candles=16,
    )

    assert decision.decision == "long"
    assert "WINNER_ONLY_ADD_ON" in decision.rationale_codes
    assert "ADD_ON_TREND_CONFIRMED" in decision.rationale_codes
    assert "ADD_ON_PROTECTED_STOP" in decision.rationale_codes


def test_trading_agent_holds_add_on_when_position_is_not_a_winner() -> None:
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
    bullish_features.rsi = 60.0
    open_position = Position(
        symbol="BTCUSDT",
        mode="live",
        side="long",
        status="open",
        quantity=0.01,
        entry_price=100.0,
        mark_price=99.8,
        leverage=2.0,
        stop_loss=98.8,
        take_profit=106.0,
        realized_pnl=0.0,
        unrealized_pnl=-0.2,
    )

    decision, _, _ = _agent().run(
        bullish_base,
        bullish_features,
        [open_position],
        {
            **_risk_context(),
            "position_management_context": {
                "current_r_multiple": -0.2,
                "break_even_eligible": False,
                "tightened_stop_loss": 98.8,
            },
            "selection_context": {
                "universe_breadth": {
                    "breadth_regime": "mixed",
                    "hold_bias_multiplier": 1.0,
                }
            },
        },
        use_ai=False,
        max_input_candles=16,
    )

    assert decision.decision == "hold"
    assert "ADD_ON_REQUIRES_WINNING_POSITION" in decision.rationale_codes
    assert "ADD_ON_PROTECTIVE_STOP_REQUIRED" in decision.rationale_codes


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
    assert metadata["suppression_context"]["level"] == "risk_haircut"


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


def test_adaptive_signal_disables_underperforming_setup_bucket_and_marks_hard_block_for_risk(db_session) -> None:
    _seed_setup_bucket_history(
        db_session,
        net_pnls=[-28.0, -22.0, -18.0, -14.0],
        signed_slippage_bps=[16.0, 15.0, 14.0, 13.0],
    )
    adaptive_context = build_adaptive_signal_context(
        db_session,
        enabled=True,
        symbol="BTCUSDT",
        timeframe="15m",
        regime="bullish",
    )
    adjustment = compute_adaptive_adjustment(
        adaptive_context,
        decision="long",
        rationale_codes=["PULLBACK_ENTRY_BIAS", "BULLISH_CONTINUATION_PULLBACK"],
        entry_mode="pullback_confirm",
    )

    assert adjustment["status"] == "setup_disabled"
    assert adjustment["setup_disable"]["active"] is True
    assert adjustment["setup_disable"]["scenario"] == "pullback_entry"
    assert "SETUP_NEGATIVE_EXPECTANCY" in adjustment["setup_disable"]["disable_reason_codes"]
    assert "SETUP_LOSS_STREAK" in adjustment["setup_disable"]["disable_reason_codes"]
    assert "SETUP_NET_PNL_AFTER_FEES_NEGATIVE" in adjustment["setup_disable"]["disable_reason_codes"]

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
    decision, _, metadata = _agent().run(
        bullish_base,
        bullish_features,
        [],
        {
            **_risk_context(),
            "adaptive_signal_context": adaptive_context,
        },
        use_ai=False,
        max_input_candles=16,
    )

    assert decision.decision == "long"
    assert ADAPTIVE_SETUP_DISABLE_REASON_CODE in decision.rationale_codes
    assert metadata["adaptive_signal_adjustment"]["setup_disable"]["active"] is True
    assert metadata["suppression_context"]["level"] == "hard_block"
    assert "adaptive_setup_disable" in metadata["suppression_context"]["sources"]


def test_adaptive_signal_setup_disable_recovers_after_cooldown(db_session) -> None:
    _seed_setup_bucket_history(
        db_session,
        net_pnls=[-28.0, -22.0, -18.0, -14.0],
        signed_slippage_bps=[16.0, 15.0, 14.0, 13.0],
        start_offset_minutes=240,
    )
    adaptive_context = build_adaptive_signal_context(
        db_session,
        enabled=True,
        symbol="BTCUSDT",
        timeframe="15m",
        regime="bullish",
    )
    adjustment = compute_adaptive_adjustment(
        adaptive_context,
        decision="long",
        rationale_codes=["PULLBACK_ENTRY_BIAS", "BULLISH_CONTINUATION_PULLBACK"],
        entry_mode="pullback_confirm",
    )

    assert adjustment["setup_disable"]["matched"] is True
    assert adjustment["setup_disable"]["active"] is False
    assert adjustment["setup_disable"]["status"] == "cooldown_elapsed"
    assert adjustment["setup_disable"]["recovery_condition"]["mode"] == "cooldown_or_metrics_recovery_or_manual_override"


def test_trading_agent_setup_disable_does_not_override_protection_recovery(db_session) -> None:
    _seed_setup_bucket_history(
        db_session,
        net_pnls=[-28.0, -22.0, -18.0, -14.0],
        signed_slippage_bps=[16.0, 15.0, 14.0, 13.0],
    )
    adaptive_context = build_adaptive_signal_context(
        db_session,
        enabled=True,
        symbol="BTCUSDT",
        timeframe="15m",
        regime="bullish",
    )
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
        stop_loss=118.0,
        take_profit=130.0,
    )

    decision, _, metadata = _agent().run(
        base,
        features,
        [open_position],
        {
            **_risk_context("PROTECTION_REQUIRED"),
            "adaptive_signal_context": adaptive_context,
            "missing_protection_symbols": ["BTCUSDT"],
            "missing_protection_items": {"BTCUSDT": ["take_profit"]},
        },
        use_ai=False,
        max_input_candles=16,
    )

    assert decision.decision == "long"
    assert "PROTECTION_REQUIRED" in decision.rationale_codes
    assert ADAPTIVE_SETUP_DISABLE_REASON_CODE not in decision.rationale_codes
    assert metadata["adaptive_signal_adjustment"]["setup_disable"]["active"] is False


def test_strategy_engine_selector_prefers_continuation_engine_for_bullish_continuation() -> None:
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

    selection = select_strategy_engine(
        market_snapshot=bullish_base,
        features=bullish_features,
        open_positions=[],
        risk_context={},
        long_breakout_allowed=False,
        short_breakout_allowed=False,
    )

    assert selection.selected_engine.engine_name == "trend_continuation_engine"
    assert selection.selected_engine.decision_hint == "long"
    assert selection.selected_engine.entry_mode == "pullback_confirm"


def test_strategy_engine_selector_prefers_protection_reduce_engine_when_protection_required() -> None:
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
        stop_loss=118.0,
        take_profit=130.0,
    )

    selection = select_strategy_engine(
        market_snapshot=base,
        features=features,
        open_positions=[open_position],
        risk_context={"operating_state": "PROTECTION_REQUIRED"},
        long_breakout_allowed=False,
        short_breakout_allowed=False,
    )

    assert selection.selected_engine.engine_name == "protection_reduce_engine"
    assert selection.selected_engine.scenario == "protection_restore"
    assert selection.selected_engine.decision_hint == "reduce"


def test_trading_agent_metadata_includes_strategy_engine_selection() -> None:
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

    _, _, metadata = _agent().run(
        bullish_base,
        bullish_features,
        [],
        _risk_context(),
        use_ai=False,
        max_input_candles=16,
    )

    assert metadata["strategy_engine"]["selected_engine"]["engine_name"] == "trend_continuation_engine"
    assert metadata["strategy_engine"]["session_context"]["session_label"] in {"asia", "europe", "us", "after_hours"}


def test_trading_agent_propagates_ai_context_and_backfills_optional_schema_fields() -> None:
    captured_payloads: list[dict[str, object]] = []

    class ContextAwareProvider:
        name = "openai"

        def generate(self, role, payload, *, response_model, instructions):  # noqa: ANN001
            captured_payloads.append(dict(payload))
            latest_price = float(payload["market_snapshot"]["latest_price"])
            return ProviderResult(
                provider="openai",
                output={
                    "decision": "long",
                    "confidence": 0.67,
                    "symbol": payload["market_snapshot"]["symbol"],
                    "timeframe": payload["market_snapshot"]["timeframe"],
                    "entry_zone_min": latest_price - 80.0,
                    "entry_zone_max": latest_price - 20.0,
                    "entry_mode": "pullback_confirm",
                    "invalidation_price": latest_price - 6.0,
                    "max_chase_bps": 10.0,
                    "idea_ttl_minutes": 30,
                    "stop_loss": latest_price - 6.0,
                    "take_profit": latest_price + 9.0,
                    "max_holding_minutes": 180,
                    "risk_pct": 0.01,
                    "leverage": 2.0,
                    "rationale_codes": ["TREND_UP", "ALIGNED_PULLBACK"],
                    "explanation_short": "ai context propagation test",
                    "explanation_detailed": "The provider returns the legacy minimum shape while the agent backfills the new optional metadata from ai context.",
                },
                usage={"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18},
            )

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
    event_context = EventContextPayload(
        source_status="fixture",
        generated_at=bullish_base.snapshot_time,
        is_stale=False,
        is_complete=True,
        next_event_at=bullish_base.snapshot_time + timedelta(minutes=18),
        next_event_name="US CPI",
        next_event_importance="high",
        minutes_to_next_event=18,
        active_risk_window=True,
        affected_assets=["BTC", "BTCUSDT"],
        event_bias="bearish",
        events=[],
    )
    bullish_features = bullish_features.model_copy(
        update={
            "event_context": event_context,
            "lead_lag": bullish_features.lead_lag.model_copy(
                update={
                    "available": True,
                    "leader_bias": "bullish",
                    "reference_symbols": ["BTCUSDT", "ETHUSDT"],
                    "bullish_alignment_score": 0.74,
                    "bearish_alignment_score": 0.24,
                    "strong_reference_confirmation": True,
                }
            ),
        }
    )
    ai_context = AIDecisionContextPacket(
        symbol="BTCUSDT",
        timeframe="15m",
        trigger_type="entry_candidate_event",
        composite_regime=CompositeRegimePacket(
            structure_regime="trend",
            direction_regime="bullish",
            volatility_regime="fast",
            participation_regime="strong",
            derivatives_regime="tailwind",
            execution_regime="clean",
            persistence_bars=5,
            persistence_class="established",
            transition_risk="medium",
            regime_reason_codes=["TREND_UP"],
        ),
        regime_summary=RegimeSummaryPayload(
            primary_regime="bullish",
            trend_alignment="bullish_aligned",
            volatility_regime="expanded",
            volume_regime="strong",
            momentum_state="strengthening",
            weak_volume=False,
            momentum_weakening=False,
        ),
        derivatives_summary=DerivativesSummaryPayload(
            available=True,
            source="binance_public",
            funding_bias="neutral",
            basis_bias="neutral",
            taker_flow_alignment="unknown",
            long_alignment_score=0.5,
            short_alignment_score=0.5,
            crowded_long_risk=False,
            crowded_short_risk=False,
            spread_headwind=False,
            spread_stress=False,
            oi_expanding_with_price=False,
            oi_falling_on_breakout=False,
        ),
        lead_lag_summary=LeadLagSummaryPayload(
            available=True,
            leader_bias="bullish",
            reference_symbols=["BTCUSDT", "ETHUSDT"],
            bullish_alignment_score=0.74,
            bearish_alignment_score=0.24,
            bullish_breakout_confirmed=False,
            bearish_breakout_confirmed=False,
            bullish_pullback_supported=False,
            bearish_pullback_supported=False,
            bullish_continuation_supported=False,
            bearish_continuation_supported=False,
            strong_reference_confirmation=True,
            weak_reference_confirmation=False,
        ),
        event_context_summary=EventContextSummaryPayload(
            source_status="fixture",
            next_event_name="US CPI",
            next_event_importance="high",
            minutes_to_next_event=18,
            active_risk_window=True,
            event_bias="bearish",
        ),
        data_quality=DataQualityPacket(
            data_quality_grade="partial",
            missing_context_flags=["orderbook_context_unavailable"],
            stale_context_flags=[],
            derivatives_available=True,
            orderbook_available=False,
            spread_quality_available=True,
            account_state_trustworthy=True,
            market_state_trustworthy=True,
        ),
        previous_thesis=PreviousThesisDeltaPacket(),
        prior_context=AIPriorContextPacket(
            engine_prior_available=True,
            engine_prior_sample_count=4,
            engine_sample_threshold_satisfied=True,
            engine_prior_classification="strong",
            capital_efficiency_available=True,
            capital_efficiency_sample_count=4,
            capital_efficiency_sample_threshold_satisfied=True,
            capital_efficiency_classification="efficient",
            session_prior_available=True,
            session_prior_sample_count=6,
            session_sample_threshold_satisfied=True,
            session_prior_classification="neutral",
            session_prior_recency_minutes=75.0,
            time_of_day_prior_available=True,
            time_of_day_prior_sample_count=7,
            time_of_day_sample_threshold_satisfied=True,
            time_of_day_prior_classification="neutral",
            time_of_day_prior_recency_minutes=95.0,
            session_time_calibration_reason_codes=["SESSION_PRIOR_STRONG_SAMPLE_EDGE"],
            prior_reason_codes=["ENGINE_PRIOR_STRONG", "CAPITAL_EFFICIENCY_EFFICIENT"],
            prior_penalty_level="none",
            expected_payoff_efficiency_hint_summary={"time_to_0_25r_hint_minutes": 20.0},
        ),
        strategy_engine="trend_pullback_engine",
        strategy_engine_context={"engine_name": "trend_pullback_engine"},
        holding_profile="swing",
        holding_profile_reason="intraday_alignment_supports_swing",
        assigned_slot="slot_2",
        candidate_weight=0.36,
        capacity_reason="mixed_breadth_moderate_capacity",
        blocked_reason_codes=[],
        hard_stop_active=True,
        stop_widening_allowed=False,
        initial_stop_type="deterministic_hard_stop",
        selection_context_summary={"assigned_slot": "slot_2", "candidate_weight": 0.36},
        prompt_family_hint="entry_candidate_event:trend_pullback_engine",
    )

    decision, provider_name, metadata = TradingDecisionAgent(ContextAwareProvider()).run(
        bullish_base,
        bullish_features,
        [],
        _risk_context(),
        use_ai=True,
        max_input_candles=16,
        ai_context=ai_context,
    )

    assert provider_name == "openai"
    assert captured_payloads[0]["feature_layers"]["regime_summary"]["primary_regime"] == "bullish"
    assert captured_payloads[0]["feature_layers"]["lead_lag_summary"]["available"] is True
    assert captured_payloads[0]["feature_layers"]["event_context_summary"]["next_event_name"] == "US CPI"
    assert captured_payloads[0]["ai_context"]["strategy_engine"] == "trend_pullback_engine"
    assert captured_payloads[0]["ai_context"]["holding_profile"] == "swing"
    assert captured_payloads[0]["ai_context"]["event_context_summary"]["active_risk_window"] is True
    assert captured_payloads[0]["ai_context"]["prior_context"]["engine_prior_classification"] == "strong"
    assert decision.prompt_family_hint == "entry_candidate_event:trend_pullback_engine"
    assert decision.regime_transition_risk == "medium"
    assert decision.data_quality_penalty_applied is True
    assert decision.event_risk_acknowledgement is None
    assert decision.confidence_penalty_reason is None
    assert decision.scenario_note is None
    assert decision.expected_time_to_0_25r_minutes is not None
    assert decision.expected_time_to_0_5r_minutes is not None
    assert decision.expected_mae_r is not None
    assert decision.invalidation_reason_codes == ["INVALIDATION_PRICE_BREACH"]
    assert decision.provider_status == "ok"
    assert metadata["ai_context"]["assigned_slot"] == "slot_2"
    assert metadata["ai_context_version"] == decision.ai_context_version
    assert metadata["prompt_family"] == "entry_pullback_review"
    assert metadata["engine_prior_classification"] == "strong"
    assert metadata["capital_efficiency_classification"] == "efficient"
    assert metadata["session_prior_sample_count"] == 6
    assert metadata["time_of_day_prior_sample_count"] == 7
    assert metadata["session_prior_recency_minutes"] == 75.0
    assert metadata["time_of_day_prior_recency_minutes"] == 95.0
    assert metadata["session_time_calibration_reason_codes"] == ["SESSION_PRIOR_STRONG_SAMPLE_EDGE"]
    assert metadata["session_time_penalty_applied"] is False
    assert metadata["allowed_actions"] == ["hold", "long", "short"]
    assert metadata["bounded_output_applied"] is False


def test_trading_decision_input_payload_exposes_separated_feature_layers() -> None:
    base = _snapshot(
        "15m",
        [100, 100.4, 100.6, 100.8, 100.7, 101.0, 101.2, 101.1, 101.4, 101.6, 101.8, 102.0, 102.2, 102.1, 102.4, 103.1],
        volumes=[900, 920, 940, 960, 955, 980, 1000, 1020, 1040, 1060, 1090, 1120, 1150, 1180, 1220, 1260],
    )
    features = compute_features(
        base,
        {
            "1h": _snapshot("1h", [98, 98.7, 99.4, 100.1, 100.9, 101.8, 102.7, 103.6, 104.4, 105.2, 106.1, 107.0, 108.0, 109.1, 110.2, 111.4]),
            "4h": _snapshot("4h", [92, 93.4, 94.9, 96.6, 98.4, 100.1, 102.0, 104.1, 106.4, 108.8, 111.3, 114.0, 116.8, 119.7, 122.7, 125.8]),
        },
    )
    features = features.model_copy(
        update={
            "event_context": EventContextPayload(
                source_status="fixture",
                generated_at=base.snapshot_time,
                is_stale=False,
                is_complete=True,
                next_event_at=base.snapshot_time + timedelta(minutes=40),
                next_event_name="FOMC",
                next_event_importance="high",
                minutes_to_next_event=40,
                active_risk_window=True,
                affected_assets=["BTCUSDT"],
                event_bias="neutral",
                events=[],
            )
        }
    )

    payload = build_trading_decision_input_payload(
        market_snapshot=base,
        higher_timeframe_context={
            "1h": _snapshot("1h", [98, 98.7, 99.4, 100.1, 100.9, 101.8, 102.7, 103.6, 104.4, 105.2, 106.1, 107.0, 108.0, 109.1, 110.2, 111.4]),
            "4h": _snapshot("4h", [92, 93.4, 94.9, 96.6, 98.4, 100.1, 102.0, 104.1, 106.4, 108.8, 111.3, 114.0, 116.8, 119.7, 122.7, 125.8]),
        },
        feature_payload=features,
        risk_context=_risk_context(),
        decision_reference={"market_snapshot_id": 1},
    )

    assert payload["features"]["event_context"]["next_event_name"] == "FOMC"
    assert payload["feature_layers"]["regime_summary"]["primary_regime"] == features.regime.primary_regime
    assert payload["feature_layers"]["derivatives_summary"]["available"] == features.derivatives.available
    assert payload["feature_layers"]["lead_lag_summary"]["leader_bias"] == features.lead_lag.leader_bias
    assert payload["feature_layers"]["event_context_summary"]["next_event_name"] == "FOMC"
    assert payload["feature_layers"]["event_context_summary"]["active_risk_window"] is True


def test_trade_decision_schema_accepts_event_aware_optional_fields() -> None:
    decision = TradeDecision(
        decision="hold",
        confidence=0.41,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_mode="none",
        holding_profile="scalp",
        max_holding_minutes=60,
        risk_pct=0.01,
        leverage=1.0,
        rationale_codes=["HIGH_IMPACT_EVENT_NEARBY"],
        event_risk_acknowledgement="US CPI risk window active",
        confidence_penalty_reason="HIGH_IMPACT_EVENT_WINDOW_ACTIVE",
        scenario_note="Wait for post-event repricing before considering a new entry.",
        explanation_short="event-aware hold",
        explanation_detailed="The setup is valid structurally but a nearby high-impact event lowers confidence and supports a no-trade posture.",
    )

    assert decision.event_risk_acknowledgement == "US CPI risk window active"
    assert decision.confidence_penalty_reason == "HIGH_IMPACT_EVENT_WINDOW_ACTIVE"
    assert decision.scenario_note == "Wait for post-event repricing before considering a new entry."


def test_trading_agent_bounds_degraded_long_horizon_entry_to_hold() -> None:
    class LongHorizonProvider:
        name = "openai"

        def generate(self, role, payload, *, response_model, instructions):  # noqa: ANN001
            latest_price = float(payload["market_snapshot"]["latest_price"])
            return ProviderResult(
                provider="openai",
                output={
                    "decision": "long",
                    "confidence": 0.66,
                    "symbol": payload["market_snapshot"]["symbol"],
                    "timeframe": payload["market_snapshot"]["timeframe"],
                    "entry_zone_min": latest_price - 25.0,
                    "entry_zone_max": latest_price - 5.0,
                    "entry_mode": "pullback_confirm",
                    "holding_profile": "position",
                    "recommended_holding_profile": "position",
                    "invalidation_price": latest_price - 14.0,
                    "max_chase_bps": 8.0,
                    "idea_ttl_minutes": 20,
                    "stop_loss": latest_price - 14.0,
                    "take_profit": latest_price + 20.0,
                    "max_holding_minutes": 240,
                    "risk_pct": 0.01,
                    "leverage": 2.0,
                    "rationale_codes": ["LONG_HORIZON_PROVIDER_OUTPUT"],
                    "explanation_short": "provider suggests a long-horizon entry",
                    "explanation_detailed": "The provider proposes a position-style entry despite degraded market quality.",
                },
            )

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
    ai_context = AIDecisionContextPacket(
        symbol="BTCUSDT",
        timeframe="15m",
        trigger_type="entry_candidate_event",
        composite_regime=CompositeRegimePacket(
            structure_regime="trend",
            direction_regime="bullish",
            volatility_regime="normal",
            participation_regime="strong",
            derivatives_regime="tailwind",
            execution_regime="clean",
            persistence_bars=5,
            persistence_class="established",
            transition_risk="medium",
            regime_reason_codes=["TREND_UP"],
        ),
        data_quality=DataQualityPacket(
            data_quality_grade="degraded",
            missing_context_flags=["orderbook_context_unavailable"],
            stale_context_flags=["market_snapshot_stale"],
            derivatives_available=True,
            orderbook_available=False,
            spread_quality_available=False,
            account_state_trustworthy=True,
            market_state_trustworthy=False,
        ),
        previous_thesis=PreviousThesisDeltaPacket(),
        strategy_engine="trend_pullback_engine",
        strategy_engine_context={"engine_name": "trend_pullback_engine"},
        holding_profile="position",
        holding_profile_reason="provider_requests_position_style_review",
        hard_stop_active=True,
        stop_widening_allowed=False,
        initial_stop_type="deterministic_hard_stop",
    )

    decision, provider_name, metadata = TradingDecisionAgent(LongHorizonProvider()).run(
        bullish_base,
        bullish_features,
        [],
        _risk_context(),
        use_ai=True,
        max_input_candles=16,
        ai_context=ai_context,
    )

    assert provider_name == "openai"
    assert decision.decision == "hold"
    assert decision.should_abstain is True
    assert decision.bounded_output_applied is True
    assert decision.fail_closed_applied is False
    assert decision.abstain_due_to_data_quality is True
    assert decision.provider_not_called_due_to_quality is False
    assert decision.quality_penalty_level == "medium"
    assert "LONG_HOLDING_PROFILE_QUALITY_INSUFFICIENT" in decision.fallback_reason_codes
    assert "LONG_HOLDING_PROFILE_QUALITY_INSUFFICIENT" in decision.data_quality_block_reason_codes
    assert metadata["provider_status"] == "ok"
    assert metadata["provider_not_called_due_to_quality"] is False
    assert metadata["abstain_due_to_data_quality"] is True
