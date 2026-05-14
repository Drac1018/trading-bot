from __future__ import annotations

from datetime import timedelta

import pytest
from trading_mvp.schemas import (
    DerivativesContextPayload,
    EventContextPayload,
    MacroEventPayload,
    MarketCandle,
    MarketSnapshotPayload,
)
from trading_mvp.services import market_data
from trading_mvp.services.ai_context import (
    build_ai_decision_context,
    build_composite_regime_packet,
    build_data_quality_packet,
)
from trading_mvp.services.cost_model import calculate_expected_trade_cost
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


def _review_trigger(trigger_reason: str) -> dict[str, object]:
    return {
        "trigger_reason": trigger_reason,
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "strategy_engine": "trend_pullback_engine",
        "holding_profile": "scalp",
        "trigger_fingerprint": f"{trigger_reason}-macro-event-test",
        "last_decision_at": None,
        "triggered_at": utcnow_naive(),
    }


def _macro_event_context(
    snapshot: MarketSnapshotPayload,
    *,
    minutes_to_event: int = 10,
    active_risk_window: bool = True,
    source_status: str = "external_api",
    is_stale: bool = False,
    is_complete: bool = True,
    affected_assets: list[str] | None = None,
    enrichment_vendors: list[str] | None = None,
    events: list[MacroEventPayload] | None = None,
) -> EventContextPayload:
    return EventContextPayload(
        source_status=source_status,  # type: ignore[arg-type]
        source_provenance="external_api",
        source_vendor="fred",
        generated_at=snapshot.snapshot_time,
        is_stale=is_stale,
        is_complete=is_complete,
        next_event_at=snapshot.snapshot_time + timedelta(minutes=minutes_to_event),
        next_event_name="US CPI",
        next_event_importance="high",
        minutes_to_next_event=minutes_to_event,
        active_risk_window=active_risk_window,
        affected_assets=affected_assets or ["USD", "CRYPTO"],
        event_bias="bearish",
        enrichment_vendors=enrichment_vendors or ["bls"],
        events=events or [],
    )


def test_build_lead_market_contexts_records_runtime_failures(monkeypatch) -> None:
    def fail_build_market_context(**_kwargs):
        raise RuntimeError("lead market unavailable")

    monkeypatch.setattr(market_data, "build_market_context", fail_build_market_context)

    contexts = market_data.build_lead_market_contexts("15m")

    assert contexts == {}
    assert contexts.lead_context_status == "unavailable"
    assert contexts.missing_lead_symbols == ["BTCUSDT", "ETHUSDT"]
    assert "LEAD_CONTEXT_UNAVAILABLE" in contexts.reason_codes
    assert "LEAD_CONTEXT_BUILD_FAILED" in contexts.reason_codes


def test_ai_context_exposes_unavailable_lead_context_status() -> None:
    snapshot, features = _features()
    features = features.model_copy(
        update={
            "lead_lag": features.lead_lag.model_copy(
                update={
                    "available": False,
                    "reference_symbols": [],
                    "missing_reference_symbols": ["BTCUSDT", "ETHUSDT"],
                    "strong_reference_confirmation": False,
                    "weak_reference_confirmation": False,
                }
            )
        }
    )

    context = build_ai_decision_context(
        market_snapshot=snapshot,
        features=features,
        risk_context={},
        selection_context={"strategy_engine": "trend_pullback_engine", "holding_profile": "scalp"},
        decision_reference={},
    )

    assert context.lead_lag_summary.lead_context_status == "unavailable"
    assert context.lead_lag_summary.missing_reference_symbols == ["BTCUSDT", "ETHUSDT"]
    assert "LEAD_CONTEXT_UNAVAILABLE" in context.lead_lag_summary.reason_codes
    assert "lead_context_unavailable" in context.data_quality.missing_context_flags
    assert "LEAD_CONTEXT_UNAVAILABLE" in context.composite_regime.regime_reason_codes


def test_ai_context_exposes_operating_summaries() -> None:
    snapshot, features = _features()

    context = build_ai_decision_context(
        market_snapshot=snapshot,
        features=features,
        risk_context={
            "active_position_summary": {
                "has_open_position": True,
                "side": "long",
                "current_r_multiple": 0.42,
            },
            "pending_entry_plan_summary": {
                "active_plan_count": 1,
                "same_symbol_plan_count": 1,
                "plans": [{"plan_id": 7, "side": "long"}],
            },
            "execution_constraints_summary": {
                "minimum_actionable_notional": 25.0,
                "risk_guard_final_authority": True,
            },
        },
        selection_context={"strategy_engine": "trend_pullback_engine", "holding_profile": "scalp"},
        decision_reference={},
    )

    assert context.active_position_summary["has_open_position"] is True
    assert context.active_position_summary["current_r_multiple"] == 0.42
    assert context.pending_entry_plan_summary["active_plan_count"] == 1
    assert context.pending_entry_plan_summary["plans"][0]["plan_id"] == 7
    assert context.execution_constraints_summary["minimum_actionable_notional"] == 25.0
    assert context.execution_constraints_summary["risk_guard_final_authority"] is True
    assert "same_direction_reentry_warning" not in context.strategy_engine_context


def test_ai_context_expected_cost_context_uses_shared_cost_model() -> None:
    snapshot, features = _features()
    selection_context = {
        "decision": "long",
        "entry_mode": "pullback_confirm",
        "expected_gross_bps": 35.0,
        "expected_cost_gate": {
            "expected_slippage_bps": 3.0,
            "spread_cost_bps": 5.5,
        },
    }

    context = build_ai_decision_context(
        market_snapshot=snapshot,
        features=features,
        risk_context={},
        selection_context=selection_context,
        decision_reference={},
    )

    expected_cost = context.strategy_engine_context["expected_cost_context"]
    shared = calculate_expected_trade_cost(
        entry_execution_type="entry_passive_limit",
        expected_gross_bps=35.0,
        explicit_slippage_bps=3.0,
        spread_cost_bps=5.5,
    )

    assert expected_cost["expected_gross_bps"] == pytest.approx(shared.expected_gross_bps)
    assert expected_cost["round_trip_fee_bps"] == pytest.approx(shared.round_trip_fee_bps)
    assert expected_cost["expected_slippage_bps"] == pytest.approx(shared.expected_slippage_bps)
    assert expected_cost["spread_cost_bps"] == pytest.approx(shared.spread_cost_bps)
    assert expected_cost["expected_net_bps"] == pytest.approx(shared.expected_net_bps)
    assert expected_cost["fee_to_gross_ratio"] == pytest.approx(shared.fee_to_gross_ratio)
    assert expected_cost["min_required_net_bps"] == pytest.approx(shared.min_required_net_bps)


def test_ai_context_adds_same_direction_tp_reentry_warning_when_provided() -> None:
    snapshot, features = _features()

    context = build_ai_decision_context(
        market_snapshot=snapshot,
        features=features,
        risk_context={
            "recent_closed_position_summary": {
                "symbol": "BTCUSDT",
                "side": "short",
                "close_reason": "take_profit_market",
                "recent_same_direction_tp_close": True,
                "minutes_since_recent_same_direction_tp": 4.5,
                "recent_tp_gross_pnl": 1.2,
                "recent_tp_net_pnl": 0.82,
                "recent_tp_fee": 0.18,
            },
        },
        selection_context={
            "strategy_engine": "trend_pullback_engine",
            "holding_profile": "scalp",
            "candidate": {
                "decision": "short",
                "entry_mode": "pullback_confirm",
            },
        },
        decision_reference={},
    )

    warning = context.strategy_engine_context["same_direction_reentry_warning"]
    assert warning["recent_same_direction_tp_close"] is True
    assert warning["minutes_since_recent_same_direction_tp"] == 4.5
    assert warning["recent_tp_gross_pnl"] == 1.2
    assert warning["recent_tp_net_pnl"] == 0.82
    assert warning["recent_tp_fee"] == 0.18
    assert warning["recent_tp_fee_to_gross_ratio"] == 0.15
    assert "fresh edge" in warning["same_direction_reentry_note"]


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


def test_data_quality_treats_flat_protective_staleness_as_safe_when_position_scopes_are_fresh() -> None:
    snapshot, features = _features()
    packet = build_data_quality_packet(
        market_snapshot=snapshot,
        features=features,
        decision_reference={
            "sync_freshness_summary": {
                "account": {"status": "synced", "raw_status": "synced", "stale": False, "incomplete": False},
                "positions": {"status": "synced", "raw_status": "synced", "stale": False, "incomplete": False},
                "open_orders": {"status": "synced", "raw_status": "synced", "stale": False, "incomplete": False},
                "protective_orders": {
                    "status": "stale",
                    "raw_status": "synced",
                    "sync_detail_status": "flat",
                    "last_attempt_status": "success",
                    "last_failure_reason": None,
                    "stale": True,
                    "incomplete": False,
                },
            }
        },
    )

    assert packet.account_state_trustworthy is True
    assert packet.data_quality_grade == "complete"
    assert "protective_orders_sync_stale" not in packet.stale_context_flags


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


def test_build_ai_decision_context_includes_separated_feature_layer_summaries() -> None:
    snapshot, features = _features()
    event_context = EventContextPayload(
        source_status="fixture",
        source_provenance="fixture",
        generated_at=snapshot.snapshot_time,
        is_stale=False,
        is_complete=True,
        next_event_at=snapshot.snapshot_time + timedelta(minutes=25),
        next_event_name="US CPI",
        next_event_importance="high",
        minutes_to_next_event=25,
        active_risk_window=True,
        affected_assets=["BTC", "BTCUSDT"],
        event_bias="bearish",
        events=[],
    )
    features = features.model_copy(update={"event_context": event_context})

    context = build_ai_decision_context(
        market_snapshot=snapshot,
        features=features,
        risk_context={},
        selection_context={"strategy_engine": "trend_pullback_engine", "holding_profile": "scalp"},
        review_trigger={
            "trigger_reason": "entry_candidate_event",
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "strategy_engine": "trend_pullback_engine",
            "holding_profile": "scalp",
            "trigger_fingerprint": "event-layer-summary-test",
            "last_decision_at": None,
            "triggered_at": utcnow_naive(),
        },
        decision_reference={},
    )

    assert context.regime_summary.primary_regime == features.regime.primary_regime
    assert context.regime_summary.trend_alignment == features.regime.trend_alignment
    assert context.derivatives_summary.available == features.derivatives.available
    assert context.derivatives_summary.taker_flow_alignment == features.derivatives.taker_flow_alignment
    assert context.lead_lag_summary.leader_bias == features.lead_lag.leader_bias
    assert context.event_context_summary.next_event_name == "US CPI"
    assert context.event_context_summary.next_event_importance == "high"
    assert context.event_context_summary.minutes_to_next_event == 25
    assert context.event_context_summary.active_risk_window is True
    assert context.event_context_summary.source_status == "fixture"
    assert context.event_context_summary.source_provenance == "fixture"
    assert context.event_context_summary.source_vendor is None
    assert context.event_context_summary.enrichment_vendors == []
    assert context.event_context_summary.event_bias == "bearish"


def test_entry_candidate_ai_context_marks_imminent_high_impact_macro_event() -> None:
    snapshot, features = _features()
    features = features.model_copy(update={"event_context": _macro_event_context(snapshot)})

    context = build_ai_decision_context(
        market_snapshot=snapshot,
        features=features,
        risk_context={},
        selection_context={"strategy_engine": "trend_pullback_engine", "holding_profile": "scalp"},
        review_trigger=_review_trigger("entry_candidate_event"),
        decision_reference={},
    )

    assert context.event_risk_active is True
    assert "MACRO_EVENT_IMMINENT" in context.event_risk_reason_codes
    assert "MACRO_EVENT_RISK_WINDOW_ACTIVE" in context.event_risk_reason_codes
    assert "MACRO_EVENT_ENRICHMENT_AVAILABLE" in context.event_risk_reason_codes
    assert context.event_risk_context["risk_pct_multiplier"] == 0.5
    assert context.event_risk_context["hold_bias"] == 0.25
    assert context.event_risk_context["event_bias_used"] == "bearish"


def test_entry_candidate_ai_context_derives_post_release_macro_result_bias() -> None:
    snapshot, features = _features()
    event = MacroEventPayload(
        event_at=snapshot.snapshot_time - timedelta(minutes=10),
        event_name="US CPI",
        importance="high",
        affected_assets=["USD", "CRYPTO"],
        event_bias=None,
        minutes_to_event=-10,
        active_risk_window=True,
        enrichment_vendors=["bls"],
        release_enrichment={
            "bls": {
                "headline_metric": "cpi_yoy_pct",
                "actual": 3.4,
                "forecast": 3.0,
                "prior": 3.1,
            }
        },
    )
    features = features.model_copy(
        update={
            "event_context": _macro_event_context(
                snapshot,
                minutes_to_event=-10,
                events=[event],
            )
        }
    )

    context = build_ai_decision_context(
        market_snapshot=snapshot,
        features=features,
        risk_context={},
        selection_context={"strategy_engine": "trend_pullback_engine", "holding_profile": "scalp"},
        review_trigger=_review_trigger("entry_candidate_event"),
        decision_reference={},
    )

    result_context = context.event_risk_context["event_result_context"]
    assert result_context["available"] is True
    assert result_context["event_result_bias"] == "bearish"
    assert result_context["comparison"] == "forecast"
    assert result_context["reaction_window_active"] is True
    assert "MACRO_EVENT_RESULT_AVAILABLE" in context.event_risk_reason_codes
    assert "MACRO_EVENT_RESULT_BEARISH" in context.event_risk_reason_codes
    assert "MACRO_RELEASE_REACTION_WINDOW" in context.event_risk_reason_codes


def test_breakout_exception_ai_context_applies_stronger_macro_hold_bias() -> None:
    snapshot, features = _features()
    features = features.model_copy(update={"event_context": _macro_event_context(snapshot)})

    context = build_ai_decision_context(
        market_snapshot=snapshot,
        features=features,
        risk_context={},
        selection_context={"strategy_engine": "breakout_exception_engine", "holding_profile": "scalp"},
        review_trigger=_review_trigger("breakout_exception_event"),
        decision_reference={},
    )

    assert context.event_risk_active is True
    assert "MACRO_EVENT_RISK_WINDOW_ACTIVE" in context.event_risk_reason_codes
    assert context.event_risk_context["risk_pct_multiplier"] == 0.35
    assert context.event_risk_context["hold_bias"] == 0.45


def test_stale_event_context_keeps_uncertainty_without_directional_bias() -> None:
    snapshot, features = _features()
    features = features.model_copy(
        update={
            "event_context": _macro_event_context(
                snapshot,
                source_status="stale",
                is_stale=True,
                is_complete=False,
            )
        }
    )

    context = build_ai_decision_context(
        market_snapshot=snapshot,
        features=features,
        risk_context={},
        selection_context={"strategy_engine": "trend_pullback_engine", "holding_profile": "scalp"},
        review_trigger=_review_trigger("entry_candidate_event"),
        decision_reference={},
    )

    assert context.event_risk_active is False
    assert "MACRO_EVENT_CONTEXT_STALE" in context.event_risk_reason_codes
    assert "MACRO_EVENT_CONTEXT_INCOMPLETE" in context.event_risk_reason_codes
    assert "MACRO_EVENT_RISK_WINDOW_ACTIVE" not in context.event_risk_reason_codes
    assert context.event_risk_context["event_bias_observed"] == "bearish"
    assert context.event_risk_context["event_bias_used"] is None


def test_protection_review_ignores_macro_entry_conservatization() -> None:
    snapshot, features = _features()
    features = features.model_copy(update={"event_context": _macro_event_context(snapshot)})

    context = build_ai_decision_context(
        market_snapshot=snapshot,
        features=features,
        risk_context={},
        selection_context={"strategy_engine": "protection_review", "holding_profile": "scalp"},
        review_trigger=_review_trigger("protection_review_event"),
        decision_reference={},
    )

    assert context.event_risk_active is False
    assert context.event_risk_context["applies_to_new_entry"] is False
    assert "MACRO_EVENT_RISK_WINDOW_ACTIVE" not in context.event_risk_reason_codes
