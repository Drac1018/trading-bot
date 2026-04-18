from __future__ import annotations

from trading_mvp.schemas import (
    AIDecisionContextPacket,
    CompositeRegimePacket,
    DataQualityPacket,
    PreviousThesisDeltaPacket,
    TradeDecision,
)
from trading_mvp.services.ai_prompt_routing import bound_trade_decision, resolve_prompt_route


def _ai_context(
    *,
    trigger_type: str,
    strategy_engine: str,
    holding_profile: str = "scalp",
    data_quality_grade: str = "complete",
) -> AIDecisionContextPacket:
    return AIDecisionContextPacket(
        symbol="BTCUSDT",
        timeframe="15m",
        trigger_type=trigger_type,  # type: ignore[arg-type]
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
            regime_reason_codes=["TEST_REGIME"],
        ),
        data_quality=DataQualityPacket(
            data_quality_grade=data_quality_grade,  # type: ignore[arg-type]
            missing_context_flags=[],
            stale_context_flags=[],
            derivatives_available=True,
            orderbook_available=True,
            spread_quality_available=True,
            account_state_trustworthy=True,
            market_state_trustworthy=True,
        ),
        previous_thesis=PreviousThesisDeltaPacket(),
        strategy_engine=strategy_engine,
        strategy_engine_context={"engine_name": strategy_engine},
        holding_profile=holding_profile,  # type: ignore[arg-type]
        hard_stop_active=True,
        stop_widening_allowed=False,
        initial_stop_type="deterministic_hard_stop",
    )


def _decision(
    *,
    action: str,
    holding_profile: str = "scalp",
    recommended_holding_profile: str | None = None,
    stop_loss: float | None = 99.0,
) -> TradeDecision:
    return TradeDecision(
        decision=action,  # type: ignore[arg-type]
        confidence=0.62,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=100.0 if action in {"long", "short"} else None,
        entry_zone_max=101.0 if action in {"long", "short"} else None,
        entry_mode="pullback_confirm" if action in {"long", "short"} else "none",
        holding_profile=holding_profile,  # type: ignore[arg-type]
        recommended_holding_profile=recommended_holding_profile,  # type: ignore[arg-type]
        invalidation_price=98.0 if action == "long" else 102.0 if action == "short" else None,
        max_chase_bps=12.0 if action in {"long", "short"} else None,
        idea_ttl_minutes=30 if action in {"long", "short"} else None,
        stop_loss=stop_loss,
        take_profit=105.0 if action == "long" else 95.0 if action == "short" else None,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=1.0,
        rationale_codes=["TEST_AI_OUTPUT"],
        explanation_short="routing test",
        explanation_detailed="Routing and bounding test payload.",
    )


def test_engine_trigger_matrix_routes_expected_prompt_families() -> None:
    entry_route = resolve_prompt_route(
        ai_context=_ai_context(trigger_type="entry_candidate_event", strategy_engine="trend_pullback_engine"),
        has_open_position=False,
    )
    assert entry_route.prompt_family == "entry_pullback_review"
    assert entry_route.allowed_actions == ("hold", "long", "short")
    assert entry_route.fail_closed is True

    breakout_route = resolve_prompt_route(
        ai_context=_ai_context(trigger_type="breakout_exception_event", strategy_engine="breakout_exception_engine"),
        has_open_position=False,
    )
    assert breakout_route.prompt_family == "breakout_exception_review"
    assert breakout_route.allowed_recommended_holding_profiles == ("scalp",)

    review_route = resolve_prompt_route(
        ai_context=_ai_context(trigger_type="open_position_recheck_due", strategy_engine="trend_continuation_engine"),
        has_open_position=True,
    )
    assert review_route.prompt_family == "open_position_thesis_review"
    assert review_route.allowed_actions == ("hold", "reduce", "exit")
    assert review_route.fail_closed is False

    protection_route = resolve_prompt_route(
        ai_context=_ai_context(trigger_type="manual_review_event", strategy_engine="protection_reduce_engine"),
        has_open_position=True,
    )
    assert protection_route.prompt_family == "protection_reduce_review"
    assert protection_route.allow_new_entry is False
    assert protection_route.safe_fallback_action == "reduce"


def test_invalid_output_bounding_on_protection_review_event() -> None:
    ai_context = _ai_context(trigger_type="protection_review_event", strategy_engine="trend_pullback_engine")
    route = resolve_prompt_route(ai_context=ai_context, has_open_position=True)
    bounded = bound_trade_decision(
        decision=_decision(action="long"),
        route=route,
        ai_context=ai_context,
        has_open_position=True,
        open_position_side="long",
        current_stop_loss=99.0,
        losing_position=False,
        provider_status="ok",
    )

    assert bounded.decision.decision == "reduce"
    assert bounded.bounded_output_applied is True
    assert "INVALID_ACTION_FOR_TRIGGER" in bounded.fallback_reason_codes
    assert bounded.fail_closed_applied is False


def test_breakout_exception_is_scalp_only() -> None:
    ai_context = _ai_context(
        trigger_type="breakout_exception_event",
        strategy_engine="breakout_exception_engine",
        data_quality_grade="complete",
    )
    route = resolve_prompt_route(ai_context=ai_context, has_open_position=False)
    bounded = bound_trade_decision(
        decision=_decision(
            action="long",
            holding_profile="position",
            recommended_holding_profile="position",
        ),
        route=route,
        ai_context=ai_context,
        has_open_position=False,
        open_position_side=None,
        current_stop_loss=None,
        losing_position=False,
        provider_status="ok",
    )

    assert bounded.decision.decision == "long"
    assert bounded.decision.holding_profile == "scalp"
    assert bounded.decision.recommended_holding_profile == "scalp"
    assert "INVALID_HOLDING_PROFILE_FOR_ENGINE" in bounded.fallback_reason_codes


def test_protection_reduce_engine_forbids_new_entry() -> None:
    ai_context = _ai_context(trigger_type="manual_review_event", strategy_engine="protection_reduce_engine")
    route = resolve_prompt_route(ai_context=ai_context, has_open_position=True)
    bounded = bound_trade_decision(
        decision=_decision(action="short"),
        route=route,
        ai_context=ai_context,
        has_open_position=True,
        open_position_side="long",
        current_stop_loss=99.0,
        losing_position=False,
        provider_status="ok",
    )

    assert bounded.decision.decision == "reduce"
    assert bounded.bounded_output_applied is True
    assert "ENGINE_FORBIDS_NEW_ENTRY" in bounded.fallback_reason_codes
