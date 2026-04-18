from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from trading_mvp.models import AgentRun, Execution, Order, Position
from trading_mvp.schemas import (
    AIDecisionContextPacket,
    AIPriorContextPacket,
    CompositeRegimePacket,
    DataQualityPacket,
    TradeDecision,
)
from trading_mvp.services.agents import TradingDecisionAgent
from trading_mvp.services.ai_prior_context import build_ai_prior_context
from trading_mvp.services.ai_prompt_routing import resolve_prompt_route
from trading_mvp.time_utils import utcnow_naive


def _ai_context(
    *,
    strategy_engine: str = "trend_pullback_engine",
    data_quality_grade: str = "complete",
    holding_profile: str = "scalp",
    prior_context: AIPriorContextPacket | None = None,
) -> AIDecisionContextPacket:
    return AIDecisionContextPacket(
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
            transition_risk="low",
            regime_reason_codes=["TEST_REGIME"],
        ),
        data_quality=DataQualityPacket(
            data_quality_grade=data_quality_grade,  # type: ignore[arg-type]
            derivatives_available=data_quality_grade != "unavailable",
            orderbook_available=data_quality_grade == "complete",
            spread_quality_available=data_quality_grade in {"complete", "partial"},
            account_state_trustworthy=data_quality_grade != "unavailable",
            market_state_trustworthy=data_quality_grade != "unavailable",
            missing_context_flags=[] if data_quality_grade == "complete" else ["orderbook_context_unavailable"],
            stale_context_flags=[],
        ),
        prior_context=prior_context or AIPriorContextPacket(),
        strategy_engine=strategy_engine,
        strategy_engine_context={
            "session_context": {
                "session_label": "asia",
                "time_of_day_bucket": "utc_00_05",
            }
        },
        holding_profile=holding_profile,  # type: ignore[arg-type]
        selection_context_summary={
            "scenario": "pullback_entry",
            "entry_mode": "pullback_confirm",
            "execution_policy_profile": "entry_btc_fast_calm",
        },
    )


def _decision(
    *,
    decision_code: str = "long",
    confidence: float = 0.68,
    holding_profile: str = "scalp",
    recommended_holding_profile: str | None = None,
) -> TradeDecision:
    return TradeDecision(
        decision=decision_code,  # type: ignore[arg-type]
        confidence=confidence,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=69950.0 if decision_code in {"long", "short"} else None,
        entry_zone_max=69980.0 if decision_code in {"long", "short"} else None,
        entry_mode="pullback_confirm" if decision_code in {"long", "short"} else "none",
        holding_profile=holding_profile,  # type: ignore[arg-type]
        recommended_holding_profile=recommended_holding_profile,  # type: ignore[arg-type]
        invalidation_price=69880.0 if decision_code in {"long", "short"} else None,
        max_chase_bps=10.0 if decision_code in {"long", "short"} else None,
        idea_ttl_minutes=30 if decision_code in {"long", "short"} else None,
        stop_loss=69880.0 if decision_code in {"long", "short"} else None,
        take_profit=70120.0 if decision_code == "long" else 69820.0 if decision_code == "short" else None,
        max_holding_minutes=240,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST_DECISION"],
        explanation_short="prior test decision",
        explanation_detailed="Prior test decision for deterministic prior adjustment coverage.",
    )


def _agent() -> TradingDecisionAgent:
    return TradingDecisionAgent(SimpleNamespace(name="test-provider"))  # type: ignore[arg-type]


def _seed_engine_trade(
    db_session,
    *,
    created_at,
    strategy_engine: str,
    session_label: str,
    time_of_day_bucket: str,
    net_pnl_after_fees: float,
    signed_slippage_bps: float,
    time_to_profit_minutes: float | None,
    drawdown_impact: float,
) -> None:
    decision_row = AgentRun(
        role="trading_decision",
        trigger_event="interval_decision_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="deterministic-mock",
        summary="prior engine seed",
        input_payload={
            "features": {
                "regime": {
                    "primary_regime": "bullish",
                    "trend_alignment": "bullish_aligned",
                }
            }
        },
        output_payload={
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "decision": "long",
            "entry_mode": "pullback_confirm",
            "rationale_codes": ["ENGINE_TEST"],
            "confidence": 0.7,
            "risk_pct": 0.01,
            "leverage": 2.0,
        },
        metadata_json={
            "strategy_engine": {
                "selected_engine": {
                    "engine_name": strategy_engine,
                    "scenario": "pullback_entry",
                    "decision_hint": "long",
                    "entry_mode": "pullback_confirm",
                    "eligible": True,
                },
                "session_context": {
                    "session_label": session_label,
                    "time_of_day_bucket": time_of_day_bucket,
                },
            },
            "selection_context": {
                "execution_policy_profile": "entry_btc_fast_calm",
            },
        },
        schema_valid=True,
        started_at=created_at,
        completed_at=created_at,
        created_at=created_at,
        updated_at=created_at,
    )
    db_session.add(decision_row)
    db_session.flush()

    position = Position(
        symbol="BTCUSDT",
        mode="live",
        side="long",
        status="closed",
        quantity=1.0,
        entry_price=100.0,
        mark_price=100.0,
        leverage=2.0,
        stop_loss=95.0,
        take_profit=110.0,
        realized_pnl=net_pnl_after_fees + 1.0,
        unrealized_pnl=0.0,
        metadata_json={
            "capital_efficiency": {
                "time_to_0_25r_minutes": time_to_profit_minutes,
                "mae_r": -abs(drawdown_impact),
            }
        },
        opened_at=created_at,
        closed_at=created_at + timedelta(minutes=60),
        created_at=created_at,
        updated_at=created_at,
    )
    db_session.add(position)
    db_session.flush()

    order = Order(
        symbol="BTCUSDT",
        decision_run_id=decision_row.id,
        position_id=position.id,
        side="buy",
        order_type="limit",
        mode="live",
        status="filled",
        requested_quantity=1.0,
        requested_price=100.0,
        filled_quantity=1.0,
        average_fill_price=100.0,
        reason_codes=[],
        metadata_json={"execution_quality": {"policy_profile": "entry_btc_fast_calm"}},
        created_at=created_at,
        updated_at=created_at,
    )
    db_session.add(order)
    db_session.flush()

    execution = Execution(
        order_id=order.id,
        position_id=position.id,
        symbol="BTCUSDT",
        status="filled",
        external_trade_id=f"engine-{decision_row.id}",
        fill_price=100.0,
        fill_quantity=1.0,
        fee_paid=1.0,
        commission_asset="USDT",
        slippage_pct=abs(signed_slippage_bps) / 10000.0,
        realized_pnl=net_pnl_after_fees + 1.0,
        payload={"signed_slippage_bps": signed_slippage_bps},
        created_at=created_at + timedelta(minutes=60),
        updated_at=created_at + timedelta(minutes=60),
    )
    db_session.add(execution)
    db_session.flush()


def _seed_efficiency_trade(
    db_session,
    *,
    created_at,
    gross_pnl: float,
    fee_total: float,
    time_to_0_25r_minutes: float | None = None,
    time_to_0_5r_minutes: float | None = None,
    time_to_fail_minutes: float | None = None,
    reached_0_25r: bool | None = None,
    reached_0_5r: bool | None = None,
    failed_before_0_25r: bool | None = None,
) -> None:
    decision_row = AgentRun(
        role="trading_decision",
        trigger_event="interval_decision_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="deterministic-mock",
        summary="prior efficiency seed",
        input_payload={
            "features": {
                "regime": {
                    "primary_regime": "bullish",
                    "trend_alignment": "bullish_aligned",
                }
            }
        },
        output_payload={
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "decision": "long",
            "entry_mode": "pullback_confirm",
            "rationale_codes": ["EFFICIENCY_TEST"],
            "confidence": 0.7,
            "risk_pct": 0.01,
            "leverage": 2.0,
        },
        metadata_json={
            "selection_context": {
                "execution_policy_profile": "entry_btc_fast_calm",
            }
        },
        schema_valid=True,
        started_at=created_at,
        completed_at=created_at,
        created_at=created_at,
        updated_at=created_at,
    )
    db_session.add(decision_row)
    db_session.flush()

    position = Position(
        symbol="BTCUSDT",
        mode="live",
        side="long",
        status="closed",
        quantity=1.0,
        entry_price=100.0,
        mark_price=100.0,
        leverage=2.0,
        stop_loss=95.0,
        take_profit=110.0,
        realized_pnl=gross_pnl,
        unrealized_pnl=0.0,
        metadata_json={
            "capital_efficiency": {
                "time_to_0_25r_minutes": time_to_0_25r_minutes,
                "time_to_0_5r_minutes": time_to_0_5r_minutes,
                "time_to_fail_minutes": time_to_fail_minutes,
                "reached_0_25r": reached_0_25r,
                "reached_0_5r": reached_0_5r,
                "failed_before_0_25r": failed_before_0_25r,
            }
        },
        opened_at=created_at,
        closed_at=created_at + timedelta(minutes=90),
        created_at=created_at,
        updated_at=created_at,
    )
    db_session.add(position)
    db_session.flush()

    entry_order = Order(
        symbol="BTCUSDT",
        decision_run_id=decision_row.id,
        position_id=position.id,
        side="buy",
        order_type="limit",
        mode="live",
        status="filled",
        requested_quantity=1.0,
        requested_price=100.0,
        filled_quantity=1.0,
        average_fill_price=100.0,
        reason_codes=[],
        metadata_json={"execution_quality": {"policy_profile": "entry_btc_fast_calm"}},
        created_at=created_at,
        updated_at=created_at,
    )
    close_order = Order(
        symbol="BTCUSDT",
        decision_run_id=decision_row.id,
        position_id=position.id,
        side="sell",
        order_type="TAKE_PROFIT_MARKET" if gross_pnl >= 0 else "STOP_MARKET",
        mode="live",
        status="filled",
        requested_quantity=1.0,
        requested_price=108.0 if gross_pnl >= 0 else 96.0,
        filled_quantity=1.0,
        average_fill_price=108.0 if gross_pnl >= 0 else 96.0,
        reduce_only=True,
        close_only=True,
        reason_codes=[],
        metadata_json={"execution_quality": {"policy_profile": "entry_btc_fast_calm"}},
        created_at=created_at + timedelta(minutes=89),
        updated_at=created_at + timedelta(minutes=89),
    )
    db_session.add_all([entry_order, close_order])
    db_session.flush()

    execution = Execution(
        order_id=close_order.id,
        position_id=position.id,
        symbol="BTCUSDT",
        status="filled",
        external_trade_id=f"efficiency-{decision_row.id}",
        fill_price=close_order.average_fill_price or 0.0,
        fill_quantity=1.0,
        fee_paid=fee_total,
        commission_asset="USDT",
        slippage_pct=0.001,
        realized_pnl=gross_pnl,
        payload={},
        created_at=created_at + timedelta(minutes=90),
        updated_at=created_at + timedelta(minutes=90),
    )
    db_session.add(execution)
    db_session.flush()


def test_insufficient_samples_mark_prior_unavailable(db_session) -> None:
    now = utcnow_naive() - timedelta(hours=6)
    for offset_hours, pnl in ((0, 12.0), (2, 10.0)):
        _seed_engine_trade(
            db_session,
            created_at=now + timedelta(hours=offset_hours),
            strategy_engine="trend_pullback_engine",
            session_label="asia",
            time_of_day_bucket="utc_00_05",
            net_pnl_after_fees=pnl,
            signed_slippage_bps=4.0,
            time_to_profit_minutes=18.0,
            drawdown_impact=0.24,
        )

    prior_context = build_ai_prior_context(
        db_session,
        ai_context=_ai_context(),
        selection_context={
            "scenario": "pullback_entry",
            "entry_mode": "pullback_confirm",
            "execution_policy_profile": "entry_btc_fast_calm",
            "regime_summary": {
                "primary_regime": "bullish",
                "trend_alignment": "bullish_aligned",
            },
            "strategy_engine_context": {
                "session_context": {
                    "session_label": "asia",
                    "time_of_day_bucket": "utc_00_05",
                }
            },
        },
    )

    assert prior_context.engine_prior_available is False
    assert prior_context.engine_prior_classification == "unavailable"
    assert prior_context.engine_sample_threshold_satisfied is False
    assert prior_context.capital_efficiency_available is False
    assert prior_context.capital_efficiency_classification == "unavailable"
    assert prior_context.prior_penalty_level == "none"


def test_strong_engine_prior_surfaces_in_context(db_session) -> None:
    now = utcnow_naive() - timedelta(hours=10)
    for offset_hours, pnl in ((0, 14.0), (2, 9.0), (4, 11.0)):
        _seed_engine_trade(
            db_session,
            created_at=now + timedelta(hours=offset_hours),
            strategy_engine="trend_pullback_engine",
            session_label="asia",
            time_of_day_bucket="utc_00_05",
            net_pnl_after_fees=pnl,
            signed_slippage_bps=4.0,
            time_to_profit_minutes=16.0 + offset_hours,
            drawdown_impact=0.22,
        )

    prior_context = build_ai_prior_context(
        db_session,
        ai_context=_ai_context(),
        selection_context={
            "scenario": "pullback_entry",
            "entry_mode": "pullback_confirm",
            "execution_policy_profile": "entry_btc_fast_calm",
            "regime_summary": {
                "primary_regime": "bullish",
                "trend_alignment": "bullish_aligned",
            },
            "strategy_engine_context": {
                "session_context": {
                    "session_label": "asia",
                    "time_of_day_bucket": "utc_00_05",
                }
            },
        },
    )

    assert prior_context.engine_prior_available is True
    assert prior_context.engine_prior_classification == "strong"
    assert prior_context.engine_prior_sample_count == 3
    assert prior_context.engine_expectancy_hint is not None
    assert prior_context.expected_payoff_efficiency_hint_summary["engine_time_to_profit_hint_minutes"] is not None


def test_inefficient_capital_efficiency_penalizes_long_holding_bias() -> None:
    prior_context = AIPriorContextPacket(
        engine_prior_available=True,
        engine_prior_sample_count=4,
        engine_sample_threshold_satisfied=True,
        engine_prior_classification="neutral",
        capital_efficiency_available=True,
        capital_efficiency_sample_count=4,
        capital_efficiency_sample_threshold_satisfied=True,
        capital_efficiency_classification="inefficient",
        prior_reason_codes=["CAPITAL_EFFICIENCY_INEFFICIENT"],
        prior_penalty_level="medium",
        expected_payoff_efficiency_hint_summary={"time_to_fail_hint_minutes": 24.0},
    )
    ai_context = _ai_context(
        prior_context=prior_context,
        holding_profile="position",
    )
    route = resolve_prompt_route(
        ai_context=ai_context,
        strategy_engine="trend_pullback_engine",
        has_open_position=False,
    )

    decision, metadata = _agent()._apply_prior_soft_adjustments(
        _decision(
            holding_profile="position",
            recommended_holding_profile="position",
        ),
        ai_context=ai_context,
        route=route,
    )

    assert decision.decision == "long"
    assert decision.holding_profile == "scalp"
    assert decision.recommended_holding_profile == "scalp"
    assert decision.confidence < 0.68
    assert metadata["confidence_adjustment_applied"] is True


def test_weak_session_and_time_prior_remain_soft_only() -> None:
    prior_context = AIPriorContextPacket(
        session_prior_available=True,
        session_prior_sample_count=6,
        session_sample_threshold_satisfied=True,
        session_prior_classification="weak",
        time_of_day_prior_available=True,
        time_of_day_prior_sample_count=7,
        time_of_day_sample_threshold_satisfied=True,
        time_of_day_prior_classification="weak",
        prior_reason_codes=["SESSION_PRIOR_WEAK", "TIME_OF_DAY_PRIOR_WEAK"],
        prior_penalty_level="light",
    )
    ai_context = _ai_context(prior_context=prior_context)
    route = resolve_prompt_route(
        ai_context=ai_context,
        strategy_engine="trend_pullback_engine",
        has_open_position=False,
    )

    decision, metadata = _agent()._apply_prior_soft_adjustments(
        _decision(),
        ai_context=ai_context,
        route=route,
    )

    assert decision.decision == "long"
    assert decision.should_abstain is False
    assert decision.confidence < 0.68
    assert metadata["confidence_adjustment_applied"] is True
    assert metadata["abstain_due_to_prior_and_quality"] is False


def test_degraded_quality_and_breakout_exception_bias_to_abstain() -> None:
    prior_context = AIPriorContextPacket(
        engine_prior_available=True,
        engine_prior_sample_count=4,
        engine_sample_threshold_satisfied=True,
        engine_prior_classification="weak",
        prior_reason_codes=["ENGINE_PRIOR_WEAK"],
        prior_penalty_level="strong",
    )
    ai_context = _ai_context(
        strategy_engine="breakout_exception_engine",
        data_quality_grade="degraded",
        prior_context=prior_context,
    )
    route = resolve_prompt_route(
        ai_context=ai_context,
        strategy_engine="breakout_exception_engine",
        has_open_position=False,
    )

    decision, metadata = _agent()._apply_prior_soft_adjustments(
        _decision(holding_profile="scalp", recommended_holding_profile="scalp"),
        ai_context=ai_context,
        route=route,
    )

    assert decision.decision == "hold"
    assert decision.should_abstain is True
    assert decision.recommended_holding_profile == "hold_current"
    assert metadata["abstain_due_to_prior_and_quality"] is True
