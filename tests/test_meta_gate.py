from __future__ import annotations

from datetime import timedelta

from trading_mvp.schemas import (
    DerivativesContextPayload,
    MarketCandle,
    MarketSnapshotPayload,
    TradeDecision,
)
from trading_mvp.services.features import compute_features
from trading_mvp.services.meta_gate import evaluate_meta_gate
from trading_mvp.time_utils import utcnow_naive


def _snapshot(
    timeframe: str,
    closes: list[float],
    *,
    derivatives_context: DerivativesContextPayload | None = None,
) -> MarketSnapshotPayload:
    now = utcnow_naive()
    interval_minutes = 15 if timeframe == "15m" else 60 if timeframe == "1h" else 240
    candles: list[MarketCandle] = []
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
                volume=1200.0,
            )
        )
    return MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe=timeframe,
        snapshot_time=now,
        latest_price=closes[-1],
        latest_volume=1200.0,
        candle_count=len(candles),
        is_stale=False,
        is_complete=True,
        candles=candles,
        derivatives_context=derivatives_context or DerivativesContextPayload(),
    )


def _decision(*, side: str = "long", entry_mode: str = "pullback_confirm") -> TradeDecision:
    return TradeDecision(
        decision=side,  # type: ignore[arg-type]
        confidence=0.68,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=100.0,
        entry_zone_max=101.0,
        entry_mode=entry_mode,  # type: ignore[arg-type]
        invalidation_price=98.0 if side == "long" else 103.0,
        max_chase_bps=15.0,
        idea_ttl_minutes=15,
        stop_loss=98.0 if side == "long" else 103.0,
        take_profit=105.0 if side == "long" else 96.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="meta gate",
        explanation_detailed="meta gate regression",
    )


def _selection_context(
    *,
    total_score: float,
    breadth_regime: str,
    directional_bias: str,
    expectancy: float,
    net_pnl_after_fees: float,
    avg_signed_slippage_bps: float,
    underperforming: bool,
    lead_lag_alignment: float,
    derivatives_alignment: float,
) -> dict[str, object]:
    return {
        "universe_breadth": {
            "breadth_regime": breadth_regime,
            "directional_bias": directional_bias,
        },
        "performance_summary": {
            "score": total_score,
            "sample_size": 6,
            "expectancy": expectancy,
            "net_pnl_after_fees": net_pnl_after_fees,
            "avg_signed_slippage_bps": avg_signed_slippage_bps,
            "loss_streak": 3 if underperforming else 0,
            "underperforming": underperforming,
        },
        "score": {
            "total_score": total_score,
            "lead_lag_alignment": lead_lag_alignment,
            "derivatives_alignment": derivatives_alignment,
        },
    }


def _decision_metadata(level: str) -> dict[str, object]:
    return {
        "decision_agreement": {
            "ai_used": True,
            "comparison_source": "deterministic_baseline_vs_ai_final",
            "level": level,
        }
    }


def test_meta_gate_passes_high_quality_aligned_entry() -> None:
    derivatives = DerivativesContextPayload(
        source="binance_public",
        spread_bps=2.0,
        taker_buy_sell_imbalance=0.18,
        funding_rate=0.00005,
        open_interest=10_000_000.0,
        open_interest_change_pct=2.5,
        perp_basis_bps=1.8,
        crowding_bias=0.08,
    )
    features = compute_features(
        _snapshot("15m", [100, 101, 102, 103, 104, 105, 106, 107], derivatives_context=derivatives),
        {
            "1h": _snapshot("1h", [98, 99, 100, 101, 102, 103, 104, 105], derivatives_context=derivatives),
            "4h": _snapshot("4h", [95, 97, 99, 101, 103, 105, 107, 109], derivatives_context=derivatives),
        },
    )

    result = evaluate_meta_gate(
        _decision(),
        feature_payload=features,
        selection_context=_selection_context(
            total_score=0.78,
            breadth_regime="trend_expansion",
            directional_bias="bullish",
            expectancy=18.0,
            net_pnl_after_fees=52.0,
            avg_signed_slippage_bps=3.5,
            underperforming=False,
            lead_lag_alignment=0.81,
            derivatives_alignment=0.79,
        ),
        decision_metadata=_decision_metadata("full_agreement"),
    )

    assert result.gate_decision == "pass"
    assert result.expected_hit_probability > 0.58
    assert result.risk_multiplier == 1.0
    assert result.reject_reason_codes == []


def test_meta_gate_soft_passes_moderate_headwind_entry() -> None:
    derivatives = DerivativesContextPayload(
        source="binance_public",
        spread_bps=6.5,
        funding_rate=0.0002,
        open_interest=9_000_000.0,
        open_interest_change_pct=0.4,
        taker_buy_sell_imbalance=0.02,
        perp_basis_bps=5.0,
        crowding_bias=0.2,
    )
    features = compute_features(
        _snapshot("15m", [100, 101, 102, 102.5, 103, 103.2, 103.4, 103.7], derivatives_context=derivatives),
        {
            "1h": _snapshot("1h", [99, 100, 101, 101.5, 102, 102.5, 103, 103.5], derivatives_context=derivatives),
            "4h": _snapshot("4h", [97, 98, 99, 100, 101, 102, 103, 104], derivatives_context=derivatives),
        },
    )

    result = evaluate_meta_gate(
        _decision(),
        feature_payload=features,
        selection_context=_selection_context(
            total_score=0.54,
            breadth_regime="mixed",
            directional_bias="bullish",
            expectancy=4.0,
            net_pnl_after_fees=6.0,
            avg_signed_slippage_bps=9.0,
            underperforming=False,
            lead_lag_alignment=0.58,
            derivatives_alignment=0.47,
        ),
        decision_metadata=_decision_metadata("partial_agreement"),
    )

    assert result.gate_decision == "soft_pass"
    assert result.risk_multiplier < 1.0
    assert result.notional_multiplier < 1.0
    assert result.expected_hit_probability < 0.58


def test_meta_gate_rejects_underperforming_high_cost_entry() -> None:
    derivatives = DerivativesContextPayload(
        source="binance_public",
        spread_bps=9.5,
        funding_rate=0.00045,
        open_interest=8_500_000.0,
        open_interest_change_pct=-0.8,
        taker_buy_sell_imbalance=-0.12,
        perp_basis_bps=11.0,
        crowding_bias=0.78,
    )
    features = compute_features(
        _snapshot("15m", [100, 101, 102, 103, 103.5, 103.8, 104.0, 104.2], derivatives_context=derivatives),
        {
            "1h": _snapshot("1h", [101, 101.4, 101.8, 102.0, 102.1, 102.2, 102.2, 102.1], derivatives_context=derivatives),
            "4h": _snapshot("4h", [102, 102.1, 102.2, 102.2, 102.1, 102.0, 101.9, 101.8], derivatives_context=derivatives),
        },
    )

    result = evaluate_meta_gate(
        _decision(entry_mode="breakout_confirm"),
        feature_payload=features,
        selection_context=_selection_context(
            total_score=0.24,
            breadth_regime="weak_breadth",
            directional_bias="bearish",
            expectancy=-9.0,
            net_pnl_after_fees=-26.0,
            avg_signed_slippage_bps=14.5,
            underperforming=True,
            lead_lag_alignment=0.21,
            derivatives_alignment=0.19,
        ),
        decision_metadata=_decision_metadata("partial_agreement"),
    )

    assert result.gate_decision == "reject"
    assert result.risk_multiplier == 0.0
    assert "META_GATE_LOW_HIT_PROBABILITY" in result.reject_reason_codes
    assert "META_GATE_NEGATIVE_EXPECTANCY" in result.reject_reason_codes
    assert "META_GATE_ADVERSE_SIGNED_SLIPPAGE" in result.reject_reason_codes
