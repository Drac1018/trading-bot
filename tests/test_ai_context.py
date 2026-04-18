from __future__ import annotations

from datetime import timedelta

from trading_mvp.schemas import DerivativesContextPayload, MarketCandle, MarketSnapshotPayload
from trading_mvp.services.ai_context import (
    build_ai_decision_context,
    build_composite_regime_packet,
    build_data_quality_packet,
)
from trading_mvp.services.features import compute_features
from trading_mvp.time_utils import utcnow_naive


def _snapshot(
    timeframe: str,
    closes: list[float],
    *,
    symbol: str = "BTCUSDT",
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
        symbol=symbol,
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


def _features():
    base = _snapshot(
        "15m",
        [100, 100.5, 101.0, 101.6, 102.2, 102.8, 103.6, 104.5, 105.3, 106.2, 107.0, 108.1],
        volumes=[900, 930, 960, 990, 1020, 1060, 1110, 1160, 1200, 1260, 1320, 1390],
    )
    features = compute_features(
        base,
        {
            "1h": _snapshot("1h", [96, 97.2, 98.5, 99.9, 101.4, 103.0, 104.7, 106.5, 108.4, 110.4, 112.5, 114.7]),
            "4h": _snapshot("4h", [90, 92.0, 94.3, 96.8, 99.5, 102.4, 105.5, 108.8, 112.3, 116.0, 119.9, 124.0]),
        },
    )
    return base, features.model_copy(
        update={
            "regime": features.regime.model_copy(
                update={
                    "primary_regime": "bullish",
                    "trend_alignment": "bullish_aligned",
                    "volatility_regime": "expanded",
                    "volume_regime": "strong",
                    "weak_volume": False,
                    "momentum_weakening": False,
                }
            ),
            "breakout": features.breakout.model_copy(update={"range_breakout_direction": "up"}),
            "candle_structure": features.candle_structure.model_copy(update={"bullish_streak": 5}),
            "volume_persistence": features.volume_persistence.model_copy(
                update={"sustained_high_volume": True, "persistence_ratio": 1.14}
            ),
            "derivatives": features.derivatives.model_copy(
                update={
                    "available": True,
                    "best_bid": 108.95,
                    "best_ask": 109.01,
                    "spread_bps": 5.5,
                    "spread_stress_score": 0.12,
                    "spread_stress": False,
                    "spread_headwind": False,
                    "long_alignment_score": 0.78,
                    "short_alignment_score": 0.24,
                    "funding_bias": "neutral",
                }
            ),
            "lead_lag": features.lead_lag.model_copy(
                update={"available": True, "strong_reference_confirmation": True}
            ),
            "data_quality_flags": [],
        }
    )


def test_composite_regime_packet_generation() -> None:
    snapshot, features = _features()

    packet = build_composite_regime_packet(
        market_snapshot=snapshot,
        features=features,
    )

    assert packet.structure_regime == "expansion"
    assert packet.direction_regime == "bullish"
    assert packet.volatility_regime == "fast"
    assert packet.participation_regime == "strong"
    assert packet.derivatives_regime == "tailwind"
    assert packet.execution_regime == "clean"
    assert packet.persistence_bars == 5
    assert packet.persistence_class == "established"
    assert packet.transition_risk == "low"
    assert "BREAKOUT_UP" in packet.regime_reason_codes


def test_data_quality_unavailable_and_degraded_classification() -> None:
    snapshot, features = _features()
    degraded_features = features.model_copy(
        update={
            "derivatives": features.derivatives.model_copy(
                update={
                    "available": False,
                    "best_bid": None,
                    "best_ask": None,
                    "spread_bps": None,
                    "spread_stress_score": None,
                }
            ),
            "data_quality_flags": ["MISSING_DERIVATIVES_CONTEXT"],
        }
    )
    degraded_packet = build_data_quality_packet(
        market_snapshot=snapshot,
        features=degraded_features,
        decision_reference={
            "sync_freshness_summary": {
                "account": {"stale": False, "incomplete": False},
                "positions": {"stale": False, "incomplete": False},
                "open_orders": {"stale": False, "incomplete": False},
                "protective_orders": {"stale": False, "incomplete": False},
            }
        },
    )

    assert degraded_packet.data_quality_grade == "degraded"
    assert degraded_packet.derivatives_available is False
    assert degraded_packet.orderbook_available is False
    assert degraded_packet.spread_quality_available is False
    assert "derivatives_context_unavailable" in degraded_packet.missing_context_flags

    unavailable_snapshot = snapshot.model_copy(update={"is_stale": True, "is_complete": False})
    unavailable_features = degraded_features.model_copy(
        update={"data_quality_flags": ["STALE_MARKET_DATA", "INCOMPLETE_MARKET_DATA"]}
    )
    unavailable_packet = build_data_quality_packet(
        market_snapshot=unavailable_snapshot,
        features=unavailable_features,
        decision_reference={
            "sync_freshness_summary": {
                "account": {"stale": True, "incomplete": False},
                "positions": {"stale": False, "incomplete": True},
            }
        },
    )

    assert unavailable_packet.data_quality_grade == "unavailable"
    assert unavailable_packet.market_state_trustworthy is False
    assert unavailable_packet.account_state_trustworthy is False
    assert "market_snapshot_stale" in unavailable_packet.stale_context_flags
    assert "positions_sync_incomplete" in unavailable_packet.missing_context_flags


def test_previous_thesis_delta_generation() -> None:
    snapshot, features = _features()
    previous_context = build_ai_decision_context(
        market_snapshot=snapshot,
        features=features,
        risk_context={},
        selection_context={
            "strategy_engine": "trend_pullback_engine",
            "holding_profile": "scalp",
            "holding_profile_reason": "scalp_default_intraday_bias",
            "assigned_slot": "slot_1",
            "candidate_weight": 0.64,
            "reason_codes": ["TREND_UP"],
            "holding_profile_context": {
                "holding_profile": "scalp",
                "holding_profile_reason": "scalp_default_intraday_bias",
                "hard_stop_active": True,
                "stop_widening_allowed": False,
                "initial_stop_type": "deterministic_hard_stop",
            },
        },
        review_trigger={
            "trigger_reason": "entry_candidate_event",
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "strategy_engine": "trend_pullback_engine",
            "holding_profile": "scalp",
            "assigned_slot": "slot_1",
            "candidate_weight": 0.64,
            "reason_codes": ["TREND_UP"],
            "trigger_fingerprint": "prev-fingerprint-123",
            "last_decision_at": None,
            "triggered_at": utcnow_naive(),
        },
        decision_reference={},
    )

    current_features = features.model_copy(
        update={
            "regime": features.regime.model_copy(
                update={
                    "primary_regime": "transition",
                    "trend_alignment": "mixed",
                    "momentum_weakening": True,
                    "volatility_regime": "expanded",
                }
            ),
            "derivatives": features.derivatives.model_copy(
                update={"available": False, "best_bid": None, "best_ask": None, "spread_bps": None}
            ),
            "data_quality_flags": ["STALE_MARKET_DATA"],
        }
    )
    context = build_ai_decision_context(
        market_snapshot=snapshot,
        features=current_features,
        risk_context={},
        selection_context={
            "strategy_engine": "breakout_exception_engine",
            "holding_profile": "swing",
            "holding_profile_reason": "intraday_alignment_supports_swing",
            "assigned_slot": "slot_2",
            "candidate_weight": 0.42,
            "reason_codes": ["TREND_UP", "BREAKOUT_UP", "DERIVATIVES_CONTEXT_LOST"],
            "holding_profile_context": {
                "holding_profile": "swing",
                "holding_profile_reason": "intraday_alignment_supports_swing",
                "hard_stop_active": True,
                "stop_widening_allowed": False,
                "initial_stop_type": "deterministic_hard_stop",
            },
        },
        review_trigger={
            "trigger_reason": "breakout_exception_event",
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "strategy_engine": "breakout_exception_engine",
            "holding_profile": "swing",
            "assigned_slot": "slot_2",
            "candidate_weight": 0.42,
            "reason_codes": ["BREAKOUT_UP", "DERIVATIVES_CONTEXT_LOST"],
            "trigger_fingerprint": "curr-fingerprint-123",
            "last_decision_at": None,
            "triggered_at": utcnow_naive(),
        },
        decision_reference={},
        previous_decision_output={
            "decision": "long",
            "holding_profile": "scalp",
            "rationale_codes": ["TREND_UP"],
            "no_trade_reason_codes": [],
            "invalidation_reason_codes": ["INVALIDATION_PRICE_BREACH"],
        },
        previous_decision_metadata={
            "ai_context": previous_context.model_dump(mode="json"),
            "strategy_engine": {"selected_engine": {"engine_name": "trend_pullback_engine"}},
            "holding_profile": "scalp",
        },
        previous_input_payload={"ai_context": previous_context.model_dump(mode="json")},
        previous_ai_invoked_at=utcnow_naive() - timedelta(minutes=30),
    )

    delta = context.previous_thesis
    assert delta.previous_decision == "long"
    assert delta.previous_strategy_engine == "trend_pullback_engine"
    assert delta.previous_holding_profile == "scalp"
    assert "strategy_engine" in delta.delta_changed_fields
    assert "holding_profile" in delta.delta_changed_fields
    assert "data_quality_grade" in delta.delta_changed_fields
    assert "BREAKOUT_UP" in delta.delta_reason_codes_added
    assert delta.thesis_degrade_detected is True
    assert delta.regime_transition_detected is True
    assert delta.data_quality_changed is True


def test_selection_context_hard_stop_and_holding_profile_included() -> None:
    snapshot, features = _features()

    context = build_ai_decision_context(
        market_snapshot=snapshot,
        features=features,
        risk_context={
            "position_management_context": {
                "holding_profile": "position",
                "holding_profile_reason": "strong_structural_regime_supports_position",
                "hard_stop_active": True,
                "stop_widening_allowed": False,
                "initial_stop_type": "deterministic_hard_stop",
            }
        },
        selection_context={
            "strategy_engine": "trend_continuation_engine",
            "strategy_engine_context": {"engine_name": "trend_continuation_engine"},
            "holding_profile": "position",
            "holding_profile_reason": "strong_structural_regime_supports_position",
            "assigned_slot": "slot_1",
            "candidate_weight": 0.78,
            "capacity_reason": "trend_expansion_priority_capacity",
            "reason_codes": ["TREND_UP", "LEAD_LAG_STRONG_CONFIRMATION"],
            "slot_allocation": {"assigned_slot": "slot_1", "candidate_weight": 0.78},
            "holding_profile_context": {
                "holding_profile": "position",
                "holding_profile_reason": "strong_structural_regime_supports_position",
                "hard_stop_active": True,
                "stop_widening_allowed": False,
                "initial_stop_type": "deterministic_hard_stop",
            },
        },
        review_trigger={
            "trigger_reason": "open_position_recheck_due",
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "strategy_engine": "trend_continuation_engine",
            "holding_profile": "position",
            "assigned_slot": "slot_1",
            "candidate_weight": 0.78,
            "reason_codes": ["TREND_UP"],
            "trigger_fingerprint": "context-fingerprint-123",
            "last_decision_at": None,
            "triggered_at": utcnow_naive(),
        },
        decision_reference={},
    )

    assert context.strategy_engine == "trend_continuation_engine"
    assert context.holding_profile == "position"
    assert context.holding_profile_reason == "strong_structural_regime_supports_position"
    assert context.assigned_slot == "slot_1"
    assert context.candidate_weight == 0.78
    assert context.capacity_reason == "trend_expansion_priority_capacity"
    assert context.hard_stop_active is True
    assert context.stop_widening_allowed is False
    assert context.initial_stop_type == "deterministic_hard_stop"
    assert context.selection_context_summary["slot_allocation"]["assigned_slot"] == "slot_1"
    assert context.prompt_family_hint == "open_position_recheck_due:trend_continuation_engine"
