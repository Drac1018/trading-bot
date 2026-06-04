from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from trading_mvp.enums import AgentRole
from trading_mvp.models import (
    AgentRun,
    AuditEvent,
    DecisionPerformanceFact,
    Execution,
    Order,
    PnLSnapshot,
    Position,
    StrategyCooldownState,
)
from trading_mvp.schemas import DerivativesContextPayload, TradeDecision
from trading_mvp.services.adaptive_signal import ADAPTIVE_SETUP_DISABLE_REASON_CODE
from trading_mvp.services.cost_model import calculate_expected_trade_cost
from trading_mvp.services.market_data import build_market_snapshot
from trading_mvp.services.range_mr_cooldown import (
    RANGE_MEAN_REVERSION_STRATEGY_ID,
    RANGE_MR_BREAKOUT_COOLDOWN_REASON_CODE,
    RANGE_MR_CONSECUTIVE_FAILURES_REASON_CODE,
    RANGE_MR_COOLDOWN_BLOCK_REASON_CODE,
    record_range_mr_trade_result,
)
from trading_mvp.services.risk import (
    AI_DECISION_EXPIRED_REASON_CODE,
    AI_DECISION_PRICE_MOVE_INVALIDATED_REASON_CODE,
    AI_DECISION_RANGE_BREAK_INVALIDATED_REASON_CODE,
    AI_DECISION_REGIME_CHANGED_REASON_CODE,
    AI_DECISION_VOLATILITY_SPIKE_REASON_CODE,
    CORRELATED_EXPOSURE_LIMIT_REASON_CODE,
    EXECUTION_RISK_PROFILE_BLOCK_REASON_CODE,
    PROFILE_RELAXATION_CONSECUTIVE_REASON_CODE,
    evaluate_risk,
    is_survival_path_decision,
    select_safe_execution_risk_profile,
    validate_decision_schema,
)
from trading_mvp.services.runtime_state import mark_sync_issue, mark_sync_success
from trading_mvp.services.secret_store import encrypt_secret
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive


def _mock_expected_edge_gate_settings(
    monkeypatch,
    *,
    enabled: bool = True,
    shadow: bool = False,
    max_cost_to_edge_ratio: float = 0.50,
    min_net_expected_edge_bps: float = 15.0,
    max_same_direction_major_exposure_pct: float = 2.0,
) -> None:
    settings = SimpleNamespace(
        live_trading_env_enabled=True,
        cost_model_maker_fee_bps=5.0,
        cost_model_taker_fee_bps=5.0,
        cost_model_marketable_slippage_bps=3.0,
        cost_model_passive_slippage_bps=1.0,
        cost_model_unknown_slippage_bps=2.0,
        cost_model_recent_sample_limit=20,
        cost_model_min_required_net_bps=15.0,
        expected_edge_gate_enabled=enabled,
        expected_edge_gate_shadow=shadow,
        max_cost_to_edge_ratio=max_cost_to_edge_ratio,
        min_net_expected_edge_bps=min_net_expected_edge_bps,
        max_same_direction_major_exposure_pct=max_same_direction_major_exposure_pct,
        min_expected_gross_bps_default=40.0,
        max_fee_to_gross_ratio=0.30,
        min_confidence_for_entry=0.70,
        symbol_side_min_expected_gross_bps={
            "BTCUSDT:long": 40.0,
            "ETHUSDT:long": 0.0,
        },
    )
    monkeypatch.setattr("trading_mvp.services.risk.get_settings", lambda: settings)


def _mark_all_sync_scopes_fresh(settings_row) -> None:
    now = utcnow_naive()
    for scope in ("account", "positions", "open_orders", "protective_orders"):
        detail = (
            {
                "exchange_can_trade": True,
                "exchange_can_trade_known": True,
                "exchange_can_trade_source": "unit_test",
                "exchange_can_trade_checked_at": now.isoformat(),
            }
            if scope == "account"
            else None
        )
        mark_sync_success(settings_row, scope=scope, synced_at=now, detail=detail)


def _seed_account_equity(db_session, equity: float = 100000.0) -> PnLSnapshot:
    row = PnLSnapshot(
        snapshot_date=utcnow_naive().date(),
        equity=equity,
        cash_balance=equity,
        wallet_balance=equity,
        available_balance=equity,
        gross_realized_pnl=0.0,
        fee_total=0.0,
        funding_total=0.0,
        net_pnl=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        cumulative_pnl=0.0,
        consecutive_losses=0,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _entry_decision(
    *,
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    decision: str = "long",
    entry_zone_min: float = 65000.0,
    entry_zone_max: float = 65100.0,
    stop_loss: float = 64000.0,
    take_profit: float = 66500.0,
    entry_mode: str = "immediate",
    invalidation_price: float | None = None,
    max_chase_bps: float | None = 25.0,
    rationale_codes: list[str] | None = None,
) -> TradeDecision:
    return TradeDecision(
        decision=decision,  # type: ignore[arg-type]
        confidence=0.7,
        symbol=symbol,
        timeframe=timeframe,
        entry_zone_min=entry_zone_min,
        entry_zone_max=entry_zone_max,
        entry_mode=entry_mode,  # type: ignore[arg-type]
        invalidation_price=stop_loss if invalidation_price is None else invalidation_price,
        max_chase_bps=max_chase_bps,
        idea_ttl_minutes=15,
        stop_loss=stop_loss,
        take_profit=take_profit,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=rationale_codes or ["TEST", "PENDING_ENTRY_PLAN_TRIGGERED"],
        explanation_short="entry trigger test",
        explanation_detailed="Deterministic entry trigger regression test.",
    )


def _triggerable_decision(snapshot, *, decision: str = "long") -> TradeDecision:
    price = float(snapshot.latest_price)
    return _entry_decision(
        decision=decision,
        entry_zone_min=price * 0.999,
        entry_zone_max=price * 1.001,
        stop_loss=price * 0.99,
        take_profit=price * 1.02,
        invalidation_price=price * 0.985,
        max_chase_bps=100.0,
        entry_mode="pullback_confirm" if decision in {"long", "short"} else "none",
    )


def _add_ai_decision_run(
    db_session,
    decision: TradeDecision,
    snapshot,
    *,
    generated_at=None,
    ttl_seconds: int = 900,
    reference_price: float | None = None,
    regime_id: str = "range:neutral",
    regime_label: str = "range",
    volatility_pct: float = 0.01,
    range_breakout_direction: str = "none",
) -> AgentRun:
    generated_at = generated_at or utcnow_naive()
    valid_until = generated_at + timedelta(seconds=ttl_seconds)
    reference_price = float(reference_price or snapshot.latest_price)
    validity = {
        "status": "valid",
        "generated_at": generated_at.isoformat(),
        "valid_until": valid_until.isoformat(),
        "ttl_seconds": ttl_seconds,
        "symbol": decision.symbol,
        "timeframe": decision.timeframe,
        "market_snapshot_id": 1001,
        "snapshot_hash": "unit-test-snapshot",
        "snapshot_time": snapshot.snapshot_time.isoformat(),
        "reference_price": reference_price,
        "regime_id": regime_id,
        "regime_label": regime_label,
        "volatility_pct": volatility_pct,
        "range_breakout_direction": range_breakout_direction,
        "price_move_invalidation_pct": 0.004,
    }
    row = AgentRun(
        role=AgentRole.TRADING_DECISION.value,
        trigger_event="unit_test",
        schema_name="TradeDecision",
        status="completed",
        provider_name="deterministic-mock",
        summary="unit test decision",
        input_payload={
            "market_snapshot": snapshot.model_dump(mode="json"),
            "features": {
                "volatility_pct": volatility_pct,
                "regime": {"primary_regime": regime_label, "trend_alignment": "neutral"},
                "breakout": {"range_breakout_direction": range_breakout_direction},
            },
        },
        output_payload=decision.model_dump(mode="json"),
        metadata_json={
            "generated_at": generated_at.isoformat(),
            "ai_decision_validity": validity,
            "trade_performance_tags": {
                "strategy_id": "range_mean_reversion_engine",
                "regime_id": regime_id,
                "regime_label": regime_label,
                "entry_confirmation_type": "pullback_confirm",
                "risk_mode": "normal",
            },
        },
        schema_valid=True,
        started_at=generated_at,
        completed_at=generated_at,
        created_at=generated_at,
        updated_at=generated_at,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _closed_tp_position(
    db_session,
    *,
    symbol: str = "BTCUSDT",
    side: str = "long",
    closed_minutes_ago: float = 10.0,
    close_order_type: str = "take_profit_market",
) -> Position:
    closed_at = utcnow_naive() - timedelta(minutes=closed_minutes_ago)
    position = Position(
        symbol=symbol,
        mode="live",
        side=side,
        status="closed",
        quantity=0.01,
        entry_price=65000.0,
        mark_price=66500.0 if side == "long" else 63500.0,
        leverage=2.0,
        stop_loss=64000.0 if side == "long" else 66000.0,
        take_profit=66500.0 if side == "long" else 63500.0,
        realized_pnl=15.0,
        unrealized_pnl=0.0,
        opened_at=closed_at - timedelta(minutes=20),
        closed_at=closed_at,
        metadata_json={},
    )
    db_session.add(position)
    db_session.flush()

    entry_order = Order(
        symbol=symbol,
        position_id=position.id,
        side="buy" if side == "long" else "sell",
        order_type="limit",
        mode="live",
        status="filled",
        requested_quantity=0.01,
        requested_price=position.entry_price,
        filled_quantity=0.01,
        average_fill_price=position.entry_price,
        reduce_only=False,
        close_only=False,
        metadata_json={},
        created_at=closed_at - timedelta(minutes=20),
    )
    close_order = Order(
        symbol=symbol,
        position_id=position.id,
        side="sell" if side == "long" else "buy",
        order_type=close_order_type,
        mode="live",
        status="filled",
        requested_quantity=0.01,
        requested_price=position.take_profit,
        filled_quantity=0.01,
        average_fill_price=position.take_profit,
        reduce_only=True,
        close_only=True,
        metadata_json={"protective_component": "take_profit"},
        created_at=closed_at,
    )
    db_session.add_all([entry_order, close_order])
    db_session.flush()

    db_session.add(
        Execution(
            order_id=close_order.id,
            position_id=position.id,
            symbol=symbol,
            status="filled",
            fill_price=position.take_profit,
            fill_quantity=0.01,
            fee_paid=0.1,
            slippage_pct=0.0,
            realized_pnl=15.0,
            payload={"exchange_order": {"type": "TAKE_PROFIT_MARKET"}},
            created_at=closed_at,
        )
    )
    db_session.flush()
    return position


def _range_mr_context(
    *,
    direction: str = "long",
    regime_id: str = "range:range",
    range_id: str = "BTCUSDT:15m:range:range:range:64000.00-66000.00",
    strategy_id: str = RANGE_MEAN_REVERSION_STRATEGY_ID,
    range_breakout_direction: str = "none",
) -> dict[str, object]:
    return {
        "trade_performance_tags": {
            "strategy_id": strategy_id,
            "strategy_engine": strategy_id,
            "regime_id": regime_id,
            "regime_label": "range",
            "range_id": range_id,
            "range_low": 64000.0,
            "range_high": 66000.0,
            "range_width_pct": 3.0,
            "entry_confirmation_type": "range_edge_confirm",
            "risk_mode": "normal",
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "decision": direction,
        },
        "current_market_state": {
            "regime_id": regime_id,
            "regime_label": "range",
            "range_id": range_id,
            "range_low": 64000.0,
            "range_high": 66000.0,
            "range_width_pct": 3.0,
            "range_breakout_direction": range_breakout_direction,
        },
        "expected_cost_gate": {
            "expected_gross_bps": 80.0,
            "round_trip_fee_bps": 6.0,
            "expected_slippage_bps": 1.0,
            "spread_cost_bps": 0.0,
            "entry_execution_type": "entry_passive_limit",
        },
    }


def _closed_range_mr_position(
    db_session,
    *,
    side: str = "long",
    closed_minutes_ago: float = 10.0,
    net_r_multiple: float = -0.4,
    net_realized_pnl: float = -4.0,
    range_id: str = "BTCUSDT:15m:range:range:range:64000.00-66000.00",
) -> Position:
    closed_at = utcnow_naive() - timedelta(minutes=closed_minutes_ago)
    tags = _range_mr_context(direction=side, range_id=range_id)["trade_performance_tags"]
    position = Position(
        symbol="BTCUSDT",
        mode="live",
        side=side,
        status="closed",
        quantity=0.01,
        entry_price=65000.0,
        mark_price=64600.0 if side == "long" else 65400.0,
        leverage=2.0,
        stop_loss=64000.0 if side == "long" else 66000.0,
        take_profit=66500.0 if side == "long" else 63500.0,
        realized_pnl=net_realized_pnl,
        unrealized_pnl=0.0,
        opened_at=closed_at - timedelta(minutes=20),
        closed_at=closed_at,
        metadata_json={
            "trade_performance_tags": tags,
            "closed_position_pnl": {
                "net_r_multiple": net_r_multiple,
                "net_realized_pnl": net_realized_pnl,
                "source": "unit_test",
            },
        },
    )
    db_session.add(position)
    db_session.flush()
    record_range_mr_trade_result(db_session, position)
    return position


def test_survival_path_predicate_accepts_reduce_only_management_action() -> None:
    entry = _entry_decision().model_copy(
        update={"intent_family": "entry", "management_action": "none"}
    )
    reduce_only = _entry_decision(decision="reduce").model_copy(
        update={
            "intent_family": "management",
            "management_action": "reduce_only",
            "rationale_codes": ["POSITION_MANAGEMENT_EDGE_DECAY"],
        }
    )

    assert is_survival_path_decision(entry) is False
    assert is_survival_path_decision(reduce_only) is True


def _decision_context(
    level: str,
    *,
    ai_used: bool = True,
    baseline_decision: str = "long",
    baseline_entry_mode: str = "immediate",
    final_decision: str = "long",
    final_entry_mode: str = "immediate",
) -> dict[str, object]:
    return {
        "decision_agreement": {
            "ai_used": ai_used,
            "comparison_source": "deterministic_baseline_vs_ai_final",
            "level": level,
            "baseline_decision": baseline_decision,
            "baseline_entry_mode": baseline_entry_mode,
            "final_decision": final_decision,
            "final_entry_mode": final_entry_mode,
            "direction_match": baseline_decision == final_decision and baseline_decision in {"long", "short"},
            "entry_mode_match": (
                baseline_decision == final_decision
                and baseline_decision in {"long", "short"}
                and baseline_entry_mode == final_entry_mode
            ),
        }
    }


def _meta_gate_context(
    gate_decision: str,
    *,
    expected_hit_probability: float,
    risk_multiplier: float,
    leverage_multiplier: float,
    notional_multiplier: float,
    reject_reason_codes: list[str] | None = None,
) -> dict[str, object]:
    return {
        "meta_gate": {
            "gate_decision": gate_decision,
            "expected_hit_probability": expected_hit_probability,
            "expected_time_to_profit_minutes": 36,
            "reject_reason_codes": list(reject_reason_codes or []),
            "confidence_adjustment": -0.08 if gate_decision == "soft_pass" else 0.0,
            "risk_multiplier": risk_multiplier,
            "leverage_multiplier": leverage_multiplier,
            "notional_multiplier": notional_multiplier,
            "components": {
                "applicable": True,
                "test": True,
            },
        }
    }


def _add_on_context(
    *,
    current_r_multiple: float,
    protective_stop_ready: bool = True,
    trend_alignment_ok: bool = True,
    breadth_veto: bool = False,
    lead_lag_veto: bool = False,
    derivatives_veto: bool = False,
    spread_bps: float = 2.5,
    spread_headwind: bool = False,
) -> dict[str, object]:
    return {
        "add_on_context": {
            "current_r_multiple": current_r_multiple,
            "protective_stop_ready": protective_stop_ready,
            "trend_alignment_ok": trend_alignment_ok,
            "breadth_veto": breadth_veto,
            "lead_lag_veto": lead_lag_veto,
            "derivatives_veto": derivatives_veto,
            "spread_bps": spread_bps,
            "spread_headwind": spread_headwind,
        }
    }


def _slot_allocation_context(
    *,
    assigned_slot: str,
    candidate_weight: float,
    risk_pct_multiplier: float,
    leverage_multiplier: float,
    notional_multiplier: float,
    slot_conviction_score: float = 0.7,
    meta_gate_probability: float = 0.62,
    agreement_alignment_score: float = 0.64,
    agreement_level_hint: str = "full_agreement_likely",
    execution_quality_score: float = 0.66,
) -> dict[str, object]:
    return {
        "slot_allocation": {
            "assigned_slot": assigned_slot,
            "slot_label": "high_conviction" if assigned_slot == "slot_1" else "medium_conviction",
            "candidate_weight": candidate_weight,
            "portfolio_weight": candidate_weight,
            "slot_conviction_score": slot_conviction_score,
            "meta_gate_probability": meta_gate_probability,
            "agreement_alignment_score": agreement_alignment_score,
            "agreement_level_hint": agreement_level_hint,
            "execution_quality_score": execution_quality_score,
            "risk_pct_multiplier": risk_pct_multiplier,
            "leverage_multiplier": leverage_multiplier,
            "notional_multiplier": notional_multiplier,
            "capacity_reason": "trend_expansion_allow_rotation",
            "selected_reason": "ranked_portfolio_focus",
        }
    }


def _holding_profile_context(
    profile: str,
    *,
    structural_alignment_strong: bool = True,
    intraday_alignment_ok: bool = True,
    breadth_not_weak: bool = True,
    lead_lag_positive: bool = True,
    relative_strength_positive: bool = True,
    derivatives_headwind_severe: bool = False,
) -> dict[str, object]:
    return {
        "holding_profile_context": {
            "holding_profile": profile,
            "holding_profile_reason": f"{profile}_test",
            "structural_alignment_strong": structural_alignment_strong,
            "intraday_alignment_ok": intraday_alignment_ok,
            "breadth_not_weak": breadth_not_weak,
            "lead_lag_positive": lead_lag_positive,
            "relative_strength_positive": relative_strength_positive,
            "derivatives_headwind_severe": derivatives_headwind_severe,
        }
    }


def _seed_drawdown_pnl_snapshot(
    db_session,
    *,
    equity: float,
    net_pnl: float,
    daily_pnl: float,
    consecutive_losses: int,
    minutes_ago: int,
) -> None:
    created_at = utcnow_naive() - timedelta(minutes=minutes_ago)
    db_session.add(
        PnLSnapshot(
            snapshot_date=created_at.date(),
            equity=equity,
            cash_balance=equity,
            wallet_balance=equity,
            available_balance=equity,
            gross_realized_pnl=net_pnl,
            fee_total=0.0,
            funding_total=0.0,
            net_pnl=net_pnl,
            realized_pnl=net_pnl,
            unrealized_pnl=0.0,
            daily_pnl=daily_pnl,
            cumulative_pnl=net_pnl,
            consecutive_losses=consecutive_losses,
            created_at=created_at,
            updated_at=created_at,
        )
    )


def test_risk_blocks_invalid_long_brackets(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = TradeDecision(
        decision="long",
        confidence=0.6,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=65000.0,
        entry_zone_max=65100.0,
        stop_loss=65200.0,
        take_profit=64900.0,
        max_holding_minutes=120,
        risk_pct=0.005,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="invalid",
        explanation_detailed="invalid brackets",
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)
    assert result.allowed is False
    assert "INVALID_LONG_BRACKETS" in result.reason_codes


def test_risk_blocks_stale_market_data(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140, force_stale=True)
    decision = TradeDecision(
        decision="long",
        confidence=0.6,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=65000.0,
        entry_zone_max=65100.0,
        stop_loss=64000.0,
        take_profit=66500.0,
        max_holding_minutes=120,
        risk_pct=0.005,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="stale",
        explanation_detailed="stale market should block",
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)
    assert result.allowed is False
    assert "MARKET_STATE_STALE" in result.reason_codes


def test_risk_blocks_daily_loss_limit(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    db_session.add(
        PnLSnapshot(
            snapshot_date=utcnow_naive().date(),
            equity=94000.0,
            cash_balance=94000.0,
            realized_pnl=-6000.0,
            unrealized_pnl=0.0,
            daily_pnl=-6000.0,
            cumulative_pnl=-6000.0,
            consecutive_losses=1,
        )
    )
    db_session.flush()
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = TradeDecision(
        decision="long",
        confidence=0.6,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=65000.0,
        entry_zone_max=65100.0,
        stop_loss=64000.0,
        take_profit=66500.0,
        max_holding_minutes=120,
        risk_pct=0.005,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="loss limit",
        explanation_detailed="daily loss limit test",
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)
    assert result.allowed is False
    assert "DAILY_LOSS_LIMIT_REACHED" in result.reason_codes


def test_risk_blocks_consecutive_losses(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    db_session.add(
        PnLSnapshot(
            snapshot_date=utcnow_naive().date(),
            equity=99000.0,
            cash_balance=99000.0,
            realized_pnl=-1000.0,
            unrealized_pnl=0.0,
            daily_pnl=-1000.0,
            cumulative_pnl=-1000.0,
            consecutive_losses=3,
        )
    )
    db_session.flush()
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = TradeDecision(
        decision="short",
        confidence=0.6,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=65000.0,
        entry_zone_max=65100.0,
        stop_loss=66200.0,
        take_profit=64000.0,
        max_holding_minutes=120,
        risk_pct=0.005,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="consecutive losses",
        explanation_detailed="consecutive loss gate test",
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)
    assert result.allowed is False
    assert "MAX_CONSECUTIVE_LOSSES_REACHED" in result.reason_codes


def test_schema_validation_rejects_malformed_payload() -> None:
    with pytest.raises(ValidationError):
        validate_decision_schema(
            {
                "decision": "long",
                "confidence": "0.9",
                "symbol": "BTCUSDT"
            }
        )


def test_schema_validation_accepts_optional_entry_trigger_fields() -> None:
    decision = validate_decision_schema(
        {
            "decision": "long",
            "confidence": 0.9,
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "entry_zone_min": 69950.0,
            "entry_zone_max": 70050.0,
            "entry_mode": "breakout_confirm",
            "invalidation_price": 69000.0,
            "max_chase_bps": 12.0,
            "idea_ttl_minutes": 15,
            "stop_loss": 69000.0,
            "take_profit": 72000.0,
            "max_holding_minutes": 120,
            "risk_pct": 0.01,
            "leverage": 2.0,
            "rationale_codes": ["TEST"],
            "explanation_short": "schema ok",
            "explanation_detailed": "Optional trigger fields should remain backward-compatible for schema consumers.",
        }
    )

    assert decision.entry_mode == "breakout_confirm"
    assert decision.max_chase_bps == 12.0
    assert decision.idea_ttl_minutes == 15


def test_live_risk_blocks_when_env_gate_is_disabled(db_session) -> None:
    class DisabledLiveEnv:
        live_trading_env_enabled = False

    from trading_mvp.services import risk as risk_service

    original_get_settings = risk_service.get_settings
    risk_service.get_settings = lambda: DisabledLiveEnv()  # type: ignore[assignment]
    settings_row = get_or_create_settings(db_session)
    try:
        settings_row.live_trading_enabled = True
        settings_row.manual_live_approval = True
        settings_row.live_execution_armed = True
        settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
        settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
        settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
        db_session.add(settings_row)
        db_session.flush()

        snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
        decision = TradeDecision(
            decision="long",
            confidence=0.6,
            symbol="BTCUSDT",
            timeframe="15m",
            entry_zone_min=65000.0,
            entry_zone_max=65100.0,
            stop_loss=64000.0,
            take_profit=66500.0,
            max_holding_minutes=120,
            risk_pct=0.005,
            leverage=2.0,
            rationale_codes=["TEST"],
            explanation_short="live gate",
            explanation_detailed="live env gate should still block",
        )

        result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)
        assert result.allowed is False
        assert "LIVE_ENV_DISABLED" in result.reason_codes
    finally:
        risk_service.get_settings = original_get_settings  # type: ignore[assignment]


def test_reduce_is_allowed_while_trading_is_paused(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.trading_paused = True
    settings_row.live_trading_enabled = False
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    db_session.add(settings_row)
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = TradeDecision(
        decision="reduce",
        confidence=0.7,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=65000.0,
        entry_zone_max=65100.0,
        stop_loss=64000.0,
        take_profit=66500.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="paused reduce",
        explanation_detailed="paused mode should still allow reduce-only management for an existing position.",
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is True
    assert "TRADING_PAUSED" not in result.reason_codes
    assert "LIVE_TRADING_DISABLED" not in result.reason_codes


@pytest.mark.parametrize("side", ["long", "short"])
def test_expected_cost_gate_blocks_entry_when_cost_exceeds_edge(monkeypatch, db_session, side: str) -> None:
    _mock_expected_edge_gate_settings(monkeypatch)
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    take_profit = entry_price * (1.0005 if side == "long" else 0.9995)
    stop_loss = entry_price * (0.99 if side == "long" else 1.01)
    decision = _entry_decision(
        decision=side,
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        max_chase_bps=20.0,
    )

    result, row = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
    )

    assert result.allowed is False
    assert "EXPECTED_COST_EXCEEDS_EDGE" in result.reason_codes
    assert "MARKETABLE_ENTRY_COST_TOO_HIGH" in result.reason_codes
    expected_cost_gate = result.debug_payload["expected_cost_gate"]
    assert expected_cost_gate["status"] == "blocked"
    assert expected_cost_gate["expected_cost_bps"] >= expected_cost_gate["expected_edge_bps"]
    assert row.payload["debug_payload"]["expected_cost_gate"]["reason_codes"] == expected_cost_gate["reason_codes"]


def test_expected_cost_gate_blocks_entry_when_edge_margin_is_too_thin(monkeypatch, db_session) -> None:
    _mock_expected_edge_gate_settings(monkeypatch)
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    decision = _entry_decision(
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        stop_loss=entry_price * 0.99,
        take_profit=entry_price * 1.02,
        max_chase_bps=20.0,
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context={
            "expected_cost_gate": {
                "expected_edge_bps": 20.0,
                "expected_slippage_bps": 1.0,
                "entry_execution_type": "entry_passive_limit",
            }
        },
    )

    assert result.allowed is False
    assert "EXPECTED_EDGE_MARGIN_TOO_THIN" in result.reason_codes
    expected_cost_gate = result.debug_payload["expected_cost_gate"]
    assert expected_cost_gate["status"] == "blocked"
    assert expected_cost_gate["expected_cost_bps"] < expected_cost_gate["expected_edge_bps"]
    assert expected_cost_gate["edge_cost_margin_bps"] < 15.0
    assert expected_cost_gate["thresholds"]["minimum_edge_margin_bps"] == pytest.approx(15.0)


def test_planned_risk_reward_gate_blocks_low_reward_entry(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    decision = _entry_decision(
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        stop_loss=entry_price * 0.99,
        take_profit=entry_price * 1.01,
        max_chase_bps=20.0,
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context={
            "expected_cost_gate": {
                "expected_edge_bps": 200.0,
                "expected_slippage_bps": 1.0,
                "entry_execution_type": "entry_passive_limit",
            }
        },
    )

    assert result.allowed is False
    assert "PLANNED_RISK_REWARD_TOO_LOW" in result.reason_codes
    planned_gate = result.debug_payload["planned_risk_reward_gate"]
    assert planned_gate["status"] == "blocked"
    assert planned_gate["planned_risk_reward_ratio"] < 1.25


def test_recent_symbol_performance_gate_blocks_negative_symbol_entry(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    for index in range(4):
        order = Order(
            symbol="BTCUSDT",
            side="long",
            order_type="market",
            mode="live",
            status="filled",
            requested_quantity=0.01,
            requested_price=entry_price,
            filled_quantity=0.01,
            average_fill_price=entry_price,
            reason_codes=[],
            metadata_json={"entry_execution_type": "entry_marketable"},
        )
        db_session.add(order)
        db_session.flush()
        db_session.add(
            Execution(
                order_id=order.id,
                symbol="BTCUSDT",
                fill_price=entry_price,
                fill_quantity=0.01,
                fee_paid=0.2,
                slippage_pct=0.0,
                realized_pnl=-0.1 * (index + 1),
                payload={"signed_slippage_bps": 0.0},
            )
        )
    db_session.flush()
    decision = _entry_decision(
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        stop_loss=entry_price * 0.99,
        take_profit=entry_price * 1.03,
        max_chase_bps=20.0,
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context={
            "expected_cost_gate": {
                "expected_edge_bps": 200.0,
                "expected_slippage_bps": 1.0,
                "entry_execution_type": "entry_passive_limit",
            }
        },
    )

    assert result.allowed is False
    assert "SYMBOL_RECENT_PERFORMANCE_NEGATIVE" in result.reason_codes
    performance_gate = result.debug_payload["symbol_recent_performance_gate"]
    assert performance_gate["status"] == "blocked"
    assert performance_gate["execution_count"] == 4
    assert performance_gate["net_pnl_after_fees"] < 0


def test_recent_decision_bucket_performance_gate_blocks_negative_bucket_entry(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    for index in range(4):
        decision_run_id = 10_000 + index
        db_session.add(
            DecisionPerformanceFact(
                decision_run_id=decision_run_id,
                provider_name="openai",
                symbol="BTCUSDT",
                timeframe="15m",
                decision="long",
                rationale_codes=["TEST_BUCKET"],
                regime="bullish",
                trend_alignment="bullish_aligned",
                telemetry_metadata={},
                telemetry_output={"decision": "long"},
            )
        )
        order = Order(
            symbol="BTCUSDT",
            decision_run_id=decision_run_id,
            side="long",
            order_type="market",
            mode="live",
            status="filled",
            requested_quantity=0.01,
            requested_price=entry_price,
            filled_quantity=0.01,
            average_fill_price=entry_price,
            reason_codes=[],
            metadata_json={"entry_execution_type": "entry_marketable"},
        )
        db_session.add(order)
        db_session.flush()
        db_session.add(
            Execution(
                order_id=order.id,
                symbol="BTCUSDT",
                fill_price=entry_price,
                fill_quantity=0.01,
                fee_paid=0.2,
                slippage_pct=0.0,
                realized_pnl=-0.1 * (index + 1),
                payload={"signed_slippage_bps": 0.0},
            )
        )
    db_session.flush()
    decision = _entry_decision(
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        stop_loss=entry_price * 0.99,
        take_profit=entry_price * 1.03,
        max_chase_bps=20.0,
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context={
            "current_market_state": {"primary_regime": "bullish"},
            "expected_cost_gate": {
                "expected_edge_bps": 200.0,
                "expected_slippage_bps": 1.0,
                "entry_execution_type": "entry_passive_limit",
            },
        },
    )

    assert result.allowed is False
    assert "DECISION_BUCKET_RECENT_PERFORMANCE_NEGATIVE" in result.reason_codes
    performance_gate = result.debug_payload["decision_bucket_recent_performance_gate"]
    assert performance_gate["status"] == "blocked"
    assert performance_gate["bucket_key"] == "BTCUSDT:long:bullish"
    assert performance_gate["fill_count"] == 4
    assert performance_gate["expectancy_after_fees"] < 0


def test_expected_cost_gate_blocks_entry_when_edge_is_unavailable(monkeypatch, db_session) -> None:
    _mock_expected_edge_gate_settings(monkeypatch)
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    decision = _entry_decision(
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        stop_loss=entry_price * 0.99,
        take_profit=None,  # type: ignore[arg-type]
        max_chase_bps=20.0,
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
    )

    assert result.allowed is False
    assert "EXPECTED_COST_UNAVAILABLE" in result.reason_codes
    assert result.debug_payload["expected_cost_gate"]["expected_edge_source"] == "missing_target_or_entry"


def test_expected_cost_gate_uses_recent_adverse_signed_slippage(monkeypatch, db_session) -> None:
    _mock_expected_edge_gate_settings(monkeypatch)
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    order = Order(
        symbol="BTCUSDT",
        side="long",
        order_type="market",
        mode="live",
        status="filled",
        requested_quantity=0.01,
        requested_price=entry_price,
        filled_quantity=0.01,
        average_fill_price=entry_price * 1.004,
        reason_codes=[],
        metadata_json={"entry_execution_type": "entry_marketable"},
    )
    db_session.add(order)
    db_session.flush()
    db_session.add(
        Execution(
            order_id=order.id,
            symbol="BTCUSDT",
            fill_price=entry_price * 1.004,
            fill_quantity=0.01,
            fee_paid=0.0,
            slippage_pct=0.004,
            realized_pnl=0.0,
            payload={"signed_slippage_bps": 40.0},
        )
    )
    db_session.flush()
    decision = _entry_decision(
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        stop_loss=entry_price * 0.99,
        take_profit=entry_price * 1.003,
        max_chase_bps=20.0,
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
    )

    assert result.allowed is False
    assert "EXPECTED_COST_EXCEEDS_EDGE" in result.reason_codes
    assert "ADVERSE_SLIPPAGE_TOO_HIGH" in result.reason_codes
    components = result.debug_payload["expected_cost_gate"]["cost_components"]
    assert components["recent_adverse_slippage_bps"] == pytest.approx(40.0)
    assert components["recent_slippage_sample_count"] == 1


def test_expected_cost_gate_blocks_large_funding_headwind(monkeypatch, db_session) -> None:
    _mock_expected_edge_gate_settings(monkeypatch)
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    base_snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    snapshot = base_snapshot.model_copy(
        update={
            "derivatives_context": DerivativesContextPayload(
                source="binance_public",
                funding_rate=0.002,
            )
        }
    )
    entry_price = snapshot.latest_price
    decision = _entry_decision(
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        stop_loss=entry_price * 0.99,
        take_profit=entry_price * 1.002,
        max_chase_bps=20.0,
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
    )

    assert result.allowed is False
    assert "EXPECTED_COST_EXCEEDS_EDGE" in result.reason_codes
    assert "FUNDING_HEADWIND_TOO_HIGH" in result.reason_codes
    components = result.debug_payload["expected_cost_gate"]["cost_components"]
    assert components["funding_headwind_bps"] == pytest.approx(20.0)


@pytest.mark.parametrize("decision_name", ["reduce", "exit"])
def test_expected_cost_gate_does_not_block_survival_paths(db_session, decision_name: str) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        decision=decision_name,
        entry_mode="none",
        max_chase_bps=None,
        rationale_codes=["POSITION_MANAGEMENT_TEST"],
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context={"expected_cost_gate": {"expected_edge_bps": 0.1}},
    )

    assert result.allowed is True
    assert "EXPECTED_COST_EXCEEDS_EDGE" not in result.reason_codes
    assert "EXPECTED_COST_UNAVAILABLE" not in result.reason_codes
    assert "missing_expected_profitability_inputs" not in result.reason_codes
    assert "expected_net_bps_too_low" not in result.reason_codes
    assert result.debug_payload["expected_cost_gate"]["applied"] is False


def test_expected_edge_gate_shadow_records_would_block_without_blocking(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    decision = _entry_decision(
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        stop_loss=entry_price * 0.99,
        take_profit=entry_price * 1.02,
        max_chase_bps=20.0,
    )

    result, risk_row = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context={
            "expected_cost_gate": {
                "expected_gross_bps": 20.0,
                "round_trip_fee_bps": 10.0,
                "expected_slippage_bps": 1.0,
                "spread_cost_bps": 0.0,
                "entry_execution_type": "entry_passive_limit",
            }
        },
    )

    gate = result.debug_payload["expected_cost_gate"]
    assert result.allowed is True
    assert "EXPECTED_EDGE_MIN_NET_NOT_MET" not in result.reason_codes
    assert gate["mode"] == "shadow"
    assert gate["would_block"] is True
    assert "EXPECTED_EDGE_MIN_NET_NOT_MET" in gate["would_block_reason_codes"]
    audit_event = db_session.query(AuditEvent).filter_by(event_type="risk_expected_edge_gate").one()
    assert audit_event.entity_id == str(risk_row.id)
    assert audit_event.payload["would_block"] is True
    assert audit_event.payload["enforced_reason_codes"] == []


def test_expected_edge_gate_blocks_negative_net_edge_for_submit_modes(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "limited_live"
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    _mark_all_sync_scopes_fresh(settings_row)
    _seed_account_equity(db_session)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    decision = _entry_decision(
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        stop_loss=entry_price * 0.99,
        take_profit=entry_price * 1.002,
        max_chase_bps=20.0,
    )

    result, risk_row = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context={
            "expected_cost_gate": {
                "expected_gross_bps": 20.0,
                "round_trip_fee_bps": 18.0,
                "expected_slippage_bps": 5.0,
                "spread_cost_bps": 0.0,
                "entry_execution_type": "entry_passive_limit",
            }
        },
    )

    gate = result.debug_payload["expected_cost_gate"]
    assert result.allowed is False
    assert "EXPECTED_COST_EXCEEDS_EDGE" in result.reason_codes
    assert gate["mode"] == "live_submit_hard_block"
    assert gate["blocking_active"] is False
    assert gate["live_submit_hard_block"] is True
    assert gate["enforced_reason_codes"] == ["EXPECTED_COST_EXCEEDS_EDGE", "expected_net_bps_too_low"]
    audit_event = db_session.query(AuditEvent).filter_by(event_type="risk_expected_edge_gate").one()
    assert audit_event.entity_id == str(risk_row.id)
    assert audit_event.payload["enforced_reason_codes"] == [
        "EXPECTED_COST_EXCEEDS_EDGE",
        "expected_net_bps_too_low",
    ]


def test_expected_edge_gate_blocking_enforces_thresholds_and_audits(monkeypatch, db_session) -> None:
    _mock_expected_edge_gate_settings(monkeypatch)
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    decision = _entry_decision(
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        stop_loss=entry_price * 0.99,
        take_profit=entry_price * 1.02,
        max_chase_bps=20.0,
    )

    result, risk_row = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context={
            "expected_cost_gate": {
                "expected_gross_bps": 20.0,
                "round_trip_fee_bps": 10.0,
                "expected_slippage_bps": 1.0,
                "spread_cost_bps": 0.0,
                "entry_execution_type": "entry_passive_limit",
            }
        },
    )

    gate = result.debug_payload["expected_cost_gate"]
    assert result.allowed is False
    assert "EXPECTED_EDGE_MIN_NET_NOT_MET" in result.reason_codes
    assert "EXPECTED_EDGE_COST_RATIO_TOO_HIGH" in result.reason_codes
    assert gate["mode"] == "blocking"
    assert gate["net_expected_edge_bps"] == pytest.approx(9.0)
    assert gate["cost_to_edge_ratio"] == pytest.approx(0.55)
    audit_event = db_session.query(AuditEvent).filter_by(event_type="risk_expected_edge_gate").one()
    assert audit_event.entity_id == str(risk_row.id)
    assert audit_event.payload["would_block"] is True
    assert "EXPECTED_EDGE_MIN_NET_NOT_MET" in audit_event.payload["enforced_reason_codes"]


def test_expected_edge_gate_pass_records_cost_metrics(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    snapshot = build_market_snapshot("ETHUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    decision = _entry_decision(
        symbol="ETHUSDT",
        decision="long",
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        stop_loss=entry_price * 0.99,
        take_profit=entry_price * 1.02,
        max_chase_bps=20.0,
    ).model_copy(update={"confidence": 0.82})

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context={
            "expected_cost_gate": {
                "expected_gross_bps": 200.0,
                "round_trip_fee_bps": 10.0,
                "expected_slippage_bps": 1.0,
                "spread_cost_bps": 0.0,
                "entry_execution_type": "entry_passive_limit",
            }
        },
    )

    gate = result.debug_payload["expected_cost_gate"]
    audit_event = db_session.query(AuditEvent).filter_by(event_type="risk_expected_edge_gate").one()
    assert result.allowed is True
    assert gate["would_block"] is False
    assert gate["expected_profit_bps"] == pytest.approx(200.0)
    assert gate["expected_loss_bps"] == pytest.approx(100.0)
    assert gate["expected_fee_bps"] == pytest.approx(10.0)
    assert gate["expected_total_cost_bps"] == pytest.approx(11.0)
    assert gate["net_expected_edge_bps"] == pytest.approx(189.0)
    assert gate["rr_after_estimated_cost"] == pytest.approx(1.89)
    assert audit_event.payload["would_block"] is False


def test_expected_edge_gate_blocking_does_not_apply_to_reduce_path(monkeypatch, db_session) -> None:
    _mock_expected_edge_gate_settings(monkeypatch)
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        decision="reduce",
        entry_mode="none",
        max_chase_bps=None,
        rationale_codes=["POSITION_MANAGEMENT_TEST"],
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context={
            "expected_cost_gate": {
                "expected_gross_bps": 1.0,
                "round_trip_fee_bps": 10.0,
                "expected_slippage_bps": 5.0,
            }
        },
    )

    assert result.allowed is True
    assert result.debug_payload["expected_cost_gate"]["applied"] is False
    assert db_session.query(AuditEvent).filter_by(event_type="risk_expected_edge_gate").count() == 0


def test_expected_profitability_gate_blocks_btc_long_tight_tp_with_high_fee_ratio(monkeypatch, db_session) -> None:
    _mock_expected_edge_gate_settings(monkeypatch)
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    decision = _entry_decision(
        symbol="BTCUSDT",
        decision="long",
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        entry_mode="pullback_confirm",
        stop_loss=entry_price * 0.999,
        take_profit=entry_price * 1.003375,
        max_chase_bps=20.0,
    ).model_copy(update={"confidence": 0.82})

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context={
            "expected_cost_gate": {
                "expected_gross_bps": 33.75,
                "round_trip_fee_bps": 31.05,
                "expected_slippage_bps": 1.0,
                "spread_cost_bps": 0.0,
                "entry_execution_type": "entry_passive_limit",
                "allow_market_fallback": False,
            }
        },
    )

    gate = result.debug_payload["expected_cost_gate"]
    assert result.allowed is False
    assert "fee_to_gross_ratio_too_high" in result.reason_codes
    assert "expected_gross_bps_too_tight" in result.reason_codes
    assert "btc_long_tp_too_tight" in result.reason_codes
    assert gate["expected_gross_bps"] == pytest.approx(33.75)
    assert gate["fee_to_gross_ratio"] == pytest.approx(0.92)
    assert gate["expected_net_bps"] == pytest.approx(1.7)


def test_expected_profitability_gate_does_not_blanket_block_eth_long_35bps(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    snapshot = build_market_snapshot("ETHUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    decision = _entry_decision(
        symbol="ETHUSDT",
        decision="long",
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        entry_mode="pullback_confirm",
        stop_loss=entry_price * 0.999,
        take_profit=entry_price * 1.0035,
        max_chase_bps=20.0,
    ).model_copy(update={"confidence": 0.82})

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context={
            "expected_cost_gate": {
                "expected_gross_bps": 35.0,
                "round_trip_fee_bps": 6.0,
                "expected_slippage_bps": 1.0,
                "spread_cost_bps": 0.0,
                "entry_execution_type": "entry_passive_limit",
                "allow_market_fallback": False,
            }
        },
    )

    gate = result.debug_payload["expected_cost_gate"]
    assert result.allowed is True
    assert "expected_gross_bps_too_tight" not in result.reason_codes
    assert "btc_long_tp_too_tight" not in result.reason_codes
    assert gate["expected_gross_bps"] == pytest.approx(35.0)
    assert gate["expected_net_bps"] == pytest.approx(28.0)
    assert gate["thresholds"]["effective_min_expected_gross_bps"] == pytest.approx(0.0)


def test_expected_profitability_gate_uses_shared_cost_model_values(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    snapshot = build_market_snapshot("ETHUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    decision = _entry_decision(
        symbol="ETHUSDT",
        decision="long",
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        entry_mode="pullback_confirm",
        stop_loss=entry_price * 0.999,
        take_profit=entry_price * 1.0035,
        max_chase_bps=20.0,
    ).model_copy(update={"confidence": 0.82})

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context={
            "expected_cost_gate": {
                "expected_gross_bps": 35.0,
                "round_trip_fee_bps": 10.0,
                "expected_slippage_bps": 3.0,
                "spread_cost_bps": 2.0,
                "entry_execution_type": "entry_passive_limit",
                "allow_market_fallback": False,
            }
        },
    )

    shared = calculate_expected_trade_cost(
        entry_execution_type="entry_passive_limit",
        expected_gross_bps=35.0,
        explicit_round_trip_fee_bps=10.0,
        explicit_slippage_bps=3.0,
        spread_cost_bps=2.0,
    )
    gate = result.debug_payload["expected_cost_gate"]
    assert result.allowed is True
    assert gate["round_trip_fee_bps"] == pytest.approx(shared.round_trip_fee_bps)
    assert gate["expected_slippage_bps"] == pytest.approx(shared.expected_slippage_bps)
    assert gate["spread_cost_bps"] == pytest.approx(shared.spread_cost_bps)
    assert gate["expected_net_bps"] == pytest.approx(shared.expected_net_bps)
    assert gate["fee_to_gross_ratio"] == pytest.approx(shared.fee_to_gross_ratio)


def test_expected_profitability_gate_blocks_non_positive_net_bps(monkeypatch, db_session) -> None:
    _mock_expected_edge_gate_settings(monkeypatch)
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    snapshot = build_market_snapshot("ETHUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    decision = _entry_decision(
        symbol="ETHUSDT",
        decision="long",
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        entry_mode="pullback_confirm",
        stop_loss=entry_price * 0.999,
        take_profit=entry_price * 1.0035,
        max_chase_bps=20.0,
    ).model_copy(update={"confidence": 0.82})

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context={
            "expected_cost_gate": {
                "expected_gross_bps": 35.0,
                "round_trip_fee_bps": 28.0,
                "expected_slippage_bps": 7.0,
                "spread_cost_bps": 0.0,
                "entry_execution_type": "entry_passive_limit",
                "allow_market_fallback": False,
            }
        },
    )

    assert result.allowed is False
    assert "expected_net_bps_too_low" in result.reason_codes
    assert result.debug_payload["expected_cost_gate"]["expected_net_bps"] == pytest.approx(0.0)


def test_expected_profitability_gate_blocks_high_fee_to_gross_ratio(monkeypatch, db_session) -> None:
    _mock_expected_edge_gate_settings(monkeypatch)
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    snapshot = build_market_snapshot("ETHUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    decision = _entry_decision(
        symbol="ETHUSDT",
        decision="long",
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        entry_mode="pullback_confirm",
        stop_loss=entry_price * 0.999,
        take_profit=entry_price * 1.0035,
        max_chase_bps=20.0,
    ).model_copy(update={"confidence": 0.82})

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context={
            "expected_cost_gate": {
                "expected_gross_bps": 35.0,
                "round_trip_fee_bps": 11.0,
                "expected_slippage_bps": 1.0,
                "spread_cost_bps": 0.0,
                "entry_execution_type": "entry_passive_limit",
                "allow_market_fallback": False,
            }
        },
    )

    assert result.allowed is False
    assert "fee_to_gross_ratio_too_high" in result.reason_codes
    assert result.debug_payload["expected_cost_gate"]["fee_to_gross_ratio"] > 0.30


def test_expected_profitability_gate_blocks_market_fallback_for_tight_tp(monkeypatch, db_session) -> None:
    _mock_expected_edge_gate_settings(monkeypatch)
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    snapshot = build_market_snapshot("ETHUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    decision = _entry_decision(
        symbol="ETHUSDT",
        decision="long",
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        entry_mode="immediate",
        stop_loss=entry_price * 0.999,
        take_profit=entry_price * 1.0035,
        max_chase_bps=20.0,
    ).model_copy(update={"confidence": 0.82})

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context={
            "expected_cost_gate": {
                "expected_gross_bps": 35.0,
                "round_trip_fee_bps": 6.0,
                "expected_slippage_bps": 1.0,
                "spread_cost_bps": 0.0,
                "entry_execution_type": "entry_marketable",
            }
        },
    )

    gate = result.debug_payload["expected_cost_gate"]
    assert result.allowed is False
    assert "market_fallback_not_allowed_for_tight_tp" in result.reason_codes
    assert gate["required_order_policy"] == "limit_only_or_post_only"
    assert gate["market_fallback_allowed"] is False


def test_same_symbol_side_tp_cooldown_blocks_fast_reentry(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    _closed_tp_position(db_session, symbol="BTCUSDT", side="long", closed_minutes_ago=10)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    decision = _entry_decision(
        symbol="BTCUSDT",
        decision="long",
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        entry_mode="pullback_confirm",
        stop_loss=entry_price * 0.997,
        take_profit=entry_price * 1.0046,
        max_chase_bps=20.0,
    ).model_copy(update={"confidence": 0.82})

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context={
            "expected_cost_gate": {
                "expected_gross_bps": 46.0,
                "round_trip_fee_bps": 10.0,
                "expected_slippage_bps": 16.0,
                "spread_cost_bps": 0.0,
                "entry_execution_type": "entry_passive_limit",
            }
        },
    )

    gate = result.debug_payload["recent_tp_reentry_gate"]
    assert result.allowed is False
    assert "same_symbol_side_tp_cooldown" in result.reason_codes
    assert "recent_tp_reentry_edge_not_enough" in result.reason_codes
    assert gate["status"] == "blocked"
    assert gate["required_net_bps"] == pytest.approx(25.0)
    assert result.debug_payload["expected_cost_gate"]["required_order_policy"] == "block_or_pending"
    assert result.debug_payload["expected_cost_gate"]["allow_market_fallback"] is False


def test_same_symbol_side_tp_cooldown_allows_after_window(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    _closed_tp_position(db_session, symbol="BTCUSDT", side="long", closed_minutes_ago=31)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    decision = _entry_decision(
        symbol="BTCUSDT",
        decision="long",
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        entry_mode="pullback_confirm",
        stop_loss=entry_price * 0.997,
        take_profit=entry_price * 1.0046,
        max_chase_bps=20.0,
    ).model_copy(update={"confidence": 0.82})

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context={
            "expected_cost_gate": {
                "expected_gross_bps": 46.0,
                "round_trip_fee_bps": 10.0,
                "expected_slippage_bps": 16.0,
                "spread_cost_bps": 0.0,
                "entry_execution_type": "entry_passive_limit",
            }
        },
    )

    assert result.allowed is True
    assert "same_symbol_side_tp_cooldown" not in result.reason_codes
    assert result.debug_payload["recent_tp_reentry_gate"]["status"] == "clear"


def test_same_symbol_side_tp_cooldown_allows_only_when_net_edge_exceeds_uplift(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    _closed_tp_position(db_session, symbol="BTCUSDT", side="long", closed_minutes_ago=10)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    decision = _entry_decision(
        symbol="BTCUSDT",
        decision="long",
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        entry_mode="pullback_confirm",
        stop_loss=entry_price * 0.997,
        take_profit=entry_price * 1.006,
        max_chase_bps=20.0,
    ).model_copy(update={"confidence": 0.82})

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context={
            "expected_cost_gate": {
                "expected_gross_bps": 60.0,
                "round_trip_fee_bps": 10.0,
                "expected_slippage_bps": 1.0,
                "spread_cost_bps": 0.0,
                "entry_execution_type": "entry_passive_limit",
            }
        },
    )

    gate = result.debug_payload["recent_tp_reentry_gate"]
    assert result.allowed is True
    assert gate["status"] == "limited_pass"
    assert gate["expected_net_bps"] == pytest.approx(49.0)
    assert result.debug_payload["expected_cost_gate"]["required_order_policy"] == "limit_only_or_post_only"
    assert result.debug_payload["expected_cost_gate"]["allow_market_fallback"] is False
    assert "recent_tp_reentry_requires_limit_only" in result.adjustment_reason_codes


def test_same_symbol_side_tp_cooldown_does_not_block_opposite_side_entry(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    _closed_tp_position(db_session, symbol="BTCUSDT", side="long", closed_minutes_ago=10)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    decision = _entry_decision(
        symbol="BTCUSDT",
        decision="short",
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        entry_mode="pullback_confirm",
        stop_loss=entry_price * 1.003,
        take_profit=entry_price * 0.9954,
        max_chase_bps=20.0,
    ).model_copy(update={"confidence": 0.82})

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context={
            "expected_cost_gate": {
                "expected_gross_bps": 46.0,
                "round_trip_fee_bps": 10.0,
                "expected_slippage_bps": 16.0,
                "spread_cost_bps": 0.0,
                "entry_execution_type": "entry_passive_limit",
            }
        },
    )

    assert result.allowed is True
    assert "same_symbol_side_tp_cooldown" not in result.reason_codes
    assert result.debug_payload["recent_tp_reentry_gate"]["status"] == "clear"


def test_same_symbol_side_tp_cooldown_does_not_block_reduce_only_path(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    _closed_tp_position(db_session, symbol="BTCUSDT", side="long", closed_minutes_ago=10)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        symbol="BTCUSDT",
        decision="reduce",
        entry_mode="none",
        max_chase_bps=None,
        rationale_codes=["POSITION_MANAGEMENT_TEST"],
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
    )

    assert result.allowed is True
    assert "same_symbol_side_tp_cooldown" not in result.reason_codes
    assert result.debug_payload["recent_tp_reentry_gate"]["applied"] is False


def test_range_mean_reversion_cooldown_blocks_after_consecutive_long_losses(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    _closed_range_mr_position(db_session, side="long", closed_minutes_ago=20, net_r_multiple=-0.5)
    _closed_range_mr_position(db_session, side="long", closed_minutes_ago=5, net_r_multiple=-0.3)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    decision = _entry_decision(
        symbol="BTCUSDT",
        decision="long",
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        entry_mode="pullback_confirm",
        stop_loss=entry_price * 0.997,
        take_profit=entry_price * 1.008,
        max_chase_bps=20.0,
    ).model_copy(update={"confidence": 0.82})

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context=_range_mr_context(direction="long"),
    )

    gate = result.debug_payload["range_mr_cooldown_gate"]
    assert result.allowed is False
    assert RANGE_MR_COOLDOWN_BLOCK_REASON_CODE in result.reason_codes
    assert RANGE_MR_CONSECUTIVE_FAILURES_REASON_CODE in result.reason_codes
    assert gate["status"] == "blocked"
    assert gate["consecutive_failures"] == 2
    assert db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "range_mean_reversion_cooldown_blocked")
    )


def test_range_mean_reversion_cooldown_does_not_block_short_or_trend_candidate(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    _closed_range_mr_position(db_session, side="long", closed_minutes_ago=20, net_r_multiple=-0.5)
    _closed_range_mr_position(db_session, side="long", closed_minutes_ago=5, net_r_multiple=-0.3)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    short_decision = _entry_decision(
        symbol="BTCUSDT",
        decision="short",
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        entry_mode="pullback_confirm",
        stop_loss=entry_price * 1.003,
        take_profit=entry_price * 0.992,
        max_chase_bps=20.0,
    ).model_copy(update={"confidence": 0.82})
    trend_decision = _entry_decision(
        symbol="BTCUSDT",
        decision="long",
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        entry_mode="pullback_confirm",
        stop_loss=entry_price * 0.997,
        take_profit=entry_price * 1.008,
        max_chase_bps=20.0,
    ).model_copy(update={"confidence": 0.82})

    short_result, _ = evaluate_risk(
        db_session,
        settings_row,
        short_decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context=_range_mr_context(direction="short"),
    )
    trend_result, _ = evaluate_risk(
        db_session,
        settings_row,
        trend_decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context=_range_mr_context(direction="long", strategy_id="trend_pullback_engine"),
    )

    assert RANGE_MR_COOLDOWN_BLOCK_REASON_CODE not in short_result.reason_codes
    assert short_result.debug_payload["range_mr_cooldown_gate"]["status"] == "clear"
    assert RANGE_MR_COOLDOWN_BLOCK_REASON_CODE not in trend_result.reason_codes
    assert trend_result.debug_payload["range_mr_cooldown_gate"]["status"] == "not_applicable"


def test_range_mean_reversion_cooldown_allows_after_elapsed_window(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    db_session.add(
        StrategyCooldownState(
            strategy_id=RANGE_MEAN_REVERSION_STRATEGY_ID,
            symbol="BTCUSDT",
            direction="long",
            regime_id="range:range",
            range_id="BTCUSDT:15m:range:range:range:64000.00-66000.00",
            consecutive_failures=2,
            cooldown_until=utcnow_naive() - timedelta(minutes=1),
            cooldown_reason=RANGE_MR_CONSECUTIVE_FAILURES_REASON_CODE,
            metadata_json={},
        )
    )
    db_session.flush()
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    decision = _entry_decision(
        symbol="BTCUSDT",
        decision="long",
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        entry_mode="pullback_confirm",
        stop_loss=entry_price * 0.997,
        take_profit=entry_price * 1.008,
        max_chase_bps=20.0,
    ).model_copy(update={"confidence": 0.82})

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context=_range_mr_context(direction="long"),
    )

    assert result.allowed is True
    assert RANGE_MR_COOLDOWN_BLOCK_REASON_CODE not in result.reason_codes
    assert result.debug_payload["range_mr_cooldown_gate"]["status"] == "clear"
    assert db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "range_mean_reversion_cooldown_released")
    )


def test_range_mean_reversion_breakout_enters_cooldown_and_blocks_lower_long(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    decision = _entry_decision(
        symbol="BTCUSDT",
        decision="long",
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        entry_mode="pullback_confirm",
        stop_loss=entry_price * 0.997,
        take_profit=entry_price * 1.008,
        max_chase_bps=20.0,
    ).model_copy(update={"confidence": 0.82})

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context=_range_mr_context(direction="long", range_breakout_direction="down"),
    )

    assert result.allowed is False
    assert RANGE_MR_COOLDOWN_BLOCK_REASON_CODE in result.reason_codes
    assert RANGE_MR_BREAKOUT_COOLDOWN_REASON_CODE in result.reason_codes
    assert result.debug_payload["range_mr_cooldown_gate"]["range_breakout_direction"] == "down"
    assert db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "range_mean_reversion_cooldown_entered")
    )


def test_range_mean_reversion_cooldown_does_not_block_reduce_only_path(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    _closed_range_mr_position(db_session, side="long", closed_minutes_ago=20, net_r_multiple=-0.5)
    _closed_range_mr_position(db_session, side="long", closed_minutes_ago=5, net_r_multiple=-0.3)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        symbol="BTCUSDT",
        decision="reduce",
        entry_mode="none",
        max_chase_bps=None,
        rationale_codes=["POSITION_MANAGEMENT_TEST"],
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context=_range_mr_context(direction="long"),
    )

    assert result.allowed is True
    assert RANGE_MR_COOLDOWN_BLOCK_REASON_CODE not in result.reason_codes
    assert result.debug_payload["range_mr_cooldown_gate"]["applied"] is False


def test_expected_profitability_gate_blocks_missing_expected_gross_inputs(monkeypatch, db_session) -> None:
    _mock_expected_edge_gate_settings(monkeypatch)
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    decision = _entry_decision(
        symbol="BTCUSDT",
        decision="long",
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        stop_loss=entry_price * 0.999,
        take_profit=None,  # type: ignore[arg-type]
        max_chase_bps=20.0,
    ).model_copy(update={"confidence": 0.82})

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
    )

    assert result.allowed is False
    assert "missing_expected_profitability_inputs" in result.reason_codes


def test_expected_profitability_gate_blocks_low_confidence_entry(monkeypatch, db_session) -> None:
    _mock_expected_edge_gate_settings(monkeypatch)
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    snapshot = build_market_snapshot("ETHUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    decision = _entry_decision(
        symbol="ETHUSDT",
        decision="long",
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        entry_mode="pullback_confirm",
        stop_loss=entry_price * 0.99,
        take_profit=entry_price * 1.02,
        max_chase_bps=20.0,
    ).model_copy(update={"confidence": 0.69})

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context={
            "expected_cost_gate": {
                "expected_gross_bps": 200.0,
                "round_trip_fee_bps": 6.0,
                "expected_slippage_bps": 1.0,
                "entry_execution_type": "entry_passive_limit",
                "allow_market_fallback": False,
            }
        },
    )

    assert result.allowed is False
    assert "confidence_below_min_entry_threshold" in result.reason_codes


def test_macro_event_result_conflict_blocks_only_new_entries(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    event_result_context = {
        "event_risk_context": {
            "event_result_context": {
                "available": True,
                "event_result_bias": "bearish",
                "event_result_confidence": 0.8,
                "reaction_window_active": True,
                "comparison": "forecast",
                "event_name": "US CPI",
            },
            "reason_codes": [
                "MACRO_EVENT_RESULT_AVAILABLE",
                "MACRO_EVENT_RESULT_BEARISH",
                "MACRO_RELEASE_REACTION_WINDOW",
            ],
        },
        "expected_cost_gate": {
            "expected_edge_bps": 80.0,
            "entry_execution_type": "entry_passive_limit",
        },
    }
    entry_decision = _entry_decision(
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        stop_loss=entry_price * 0.99,
        take_profit=entry_price * 1.02,
        max_chase_bps=20.0,
    )
    reduce_decision = _entry_decision(
        decision="reduce",
        entry_mode="none",
        max_chase_bps=None,
        rationale_codes=["POSITION_MANAGEMENT_TEST"],
    )

    entry_result, _ = evaluate_risk(
        db_session,
        settings_row,
        entry_decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context=event_result_context,
    )
    reduce_result, _ = evaluate_risk(
        db_session,
        settings_row,
        reduce_decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context=event_result_context,
    )

    assert entry_result.allowed is False
    assert "MACRO_EVENT_RESULT_CONFLICT" in entry_result.reason_codes
    assert entry_result.debug_payload["macro_event_result_policy"]["applied"] is True
    assert reduce_result.allowed is True
    assert "MACRO_EVENT_RESULT_CONFLICT" not in reduce_result.reason_codes


def test_btc_uses_five_x_hard_cap(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.max_leverage = 5.0
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = TradeDecision(
        decision="long",
        confidence=0.7,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=65000.0,
        entry_zone_max=65100.0,
        stop_loss=64000.0,
        take_profit=66500.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=5.0,
        rationale_codes=["TEST"],
        explanation_short="btc cap",
        explanation_detailed="btc should use the 5x hard cap without adding a leverage error.",
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.effective_leverage_cap == 5.0
    assert result.symbol_risk_tier == "btc"
    assert "LEVERAGE_EXCEEDS_LIMIT" not in result.reason_codes


def test_major_alt_blocks_leverage_above_three_x(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.max_leverage = 5.0
    snapshot = build_market_snapshot("ETHUSDT", "15m", upto_index=140)
    decision = TradeDecision(
        decision="long",
        confidence=0.7,
        symbol="ETHUSDT",
        timeframe="15m",
        entry_zone_min=3200.0,
        entry_zone_max=3210.0,
        stop_loss=3100.0,
        take_profit=3340.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=4.0,
        rationale_codes=["TEST"],
        explanation_short="major alt cap",
        explanation_detailed="major alts should be blocked above the 3x hard leverage cap.",
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.effective_leverage_cap == 3.0
    assert result.symbol_risk_tier == "major_alt"
    assert "LEVERAGE_EXCEEDS_LIMIT" in result.reason_codes


def test_general_alt_blocks_leverage_above_two_x(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.max_leverage = 5.0
    snapshot = build_market_snapshot("APTUSDT", "15m", upto_index=140)
    decision = TradeDecision(
        decision="long",
        confidence=0.7,
        symbol="APTUSDT",
        timeframe="15m",
        entry_zone_min=10.0,
        entry_zone_max=10.1,
        stop_loss=9.5,
        take_profit=10.8,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=3.0,
        rationale_codes=["TEST"],
        explanation_short="alt cap",
        explanation_detailed="general alts should be blocked above the 2x hard leverage cap.",
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.effective_leverage_cap == 2.0
    assert result.symbol_risk_tier == "alt"
    assert "LEVERAGE_EXCEEDS_LIMIT" in result.reason_codes


def test_risk_result_includes_exposure_metrics(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    db_session.add_all(
        [
            Position(
                symbol="BTCUSDT",
                mode="live",
                side="long",
                status="open",
                quantity=0.01,
                entry_price=65000.0,
                mark_price=66000.0,
                leverage=2.0,
                stop_loss=64000.0,
                take_profit=68000.0,
            ),
            Position(
                symbol="ETHUSDT",
                mode="live",
                side="short",
                status="open",
                quantity=0.5,
                entry_price=3200.0,
                mark_price=3150.0,
                leverage=2.0,
                stop_loss=3300.0,
                take_profit=3000.0,
            ),
        ]
    )
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = TradeDecision(
        decision="hold",
        confidence=0.55,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=65000.0,
        entry_zone_max=65100.0,
        stop_loss=64000.0,
        take_profit=66500.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="metrics",
        explanation_detailed="exposure metrics should be included in the risk payload for live monitoring.",
    )

    result, row = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.exposure_metrics["open_position_count"] == 2.0
    assert result.exposure_metrics["gross_exposure_pct_equity"] > 0
    assert row.payload["exposure_metrics"]["same_tier_concentration_pct"] >= 0.0


def test_risk_blocks_gross_exposure_limit_for_new_entry(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.max_gross_exposure_pct = 0.5
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.8,
            entry_price=65000.0,
            mark_price=65000.0,
            leverage=2.0,
            stop_loss=63000.0,
            take_profit=68000.0,
        )
    )
    db_session.flush()

    snapshot = build_market_snapshot("ETHUSDT", "15m", upto_index=140)
    decision = TradeDecision(
        decision="long",
        confidence=0.7,
        symbol="ETHUSDT",
        timeframe="15m",
        entry_zone_min=3200.0,
        entry_zone_max=3210.0,
        stop_loss=3150.0,
        take_profit=3330.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="gross exposure gate",
        explanation_detailed="new entry should be blocked when projected gross exposure exceeds the deterministic cap.",
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is False
    assert "GROSS_EXPOSURE_LIMIT_REACHED" in result.reason_codes


def test_risk_blocks_directional_bias_limit_for_new_entry(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.max_directional_bias_pct = 0.7
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=1.0,
            entry_price=65000.0,
            mark_price=65000.0,
            leverage=2.0,
            stop_loss=63000.0,
            take_profit=68000.0,
        )
    )
    db_session.flush()

    snapshot = build_market_snapshot("ETHUSDT", "15m", upto_index=140)
    decision = TradeDecision(
        decision="long",
        confidence=0.7,
        symbol="ETHUSDT",
        timeframe="15m",
        entry_zone_min=3200.0,
        entry_zone_max=3210.0,
        stop_loss=3150.0,
        take_profit=3330.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="directional bias gate",
        explanation_detailed="new entry should be blocked when one-sided directional exposure exceeds the configured cap.",
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is False
    assert "DIRECTIONAL_BIAS_LIMIT_REACHED" in result.reason_codes


def test_risk_blocks_same_tier_concentration_limit_for_new_entry(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.max_same_tier_concentration_pct = 0.2
    db_session.add(
        Position(
            symbol="ETHUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=4.0,
            entry_price=3200.0,
            mark_price=3200.0,
            leverage=2.0,
            stop_loss=3100.0,
            take_profit=3400.0,
        )
    )
    db_session.flush()

    snapshot = build_market_snapshot("SOLUSDT", "15m", upto_index=140)
    decision = TradeDecision(
        decision="long",
        confidence=0.7,
        symbol="SOLUSDT",
        timeframe="15m",
        entry_zone_min=150.0,
        entry_zone_max=151.0,
        stop_loss=145.0,
        take_profit=160.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="tier concentration gate",
        explanation_detailed="new entry should be blocked when exposure inside the same risk tier exceeds the configured cap.",
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is False
    assert "SAME_TIER_CONCENTRATION_LIMIT_REACHED" in result.reason_codes


def test_portfolio_gate_blocks_eth_long_when_btc_long_major_limit_exceeded(monkeypatch, db_session) -> None:
    _mock_expected_edge_gate_settings(
        monkeypatch,
        enabled=False,
        shadow=True,
        max_same_direction_major_exposure_pct=0.75,
    )
    settings_row = get_or_create_settings(db_session)
    _seed_account_equity(db_session)
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=1.0,
            entry_price=65000.0,
            mark_price=65000.0,
            leverage=2.0,
            stop_loss=63000.0,
            take_profit=68000.0,
        )
    )
    db_session.flush()

    snapshot = build_market_snapshot("ETHUSDT", "15m", upto_index=140)
    price = float(snapshot.latest_price)
    decision = _entry_decision(
        symbol="ETHUSDT",
        entry_zone_min=price * 0.999,
        entry_zone_max=price * 1.001,
        stop_loss=price * 0.97,
        take_profit=price * 1.05,
        invalidation_price=price * 0.965,
        max_chase_bps=100.0,
    )

    result, row = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
    )

    assert result.allowed is False
    assert CORRELATED_EXPOSURE_LIMIT_REASON_CODE in result.reason_codes
    gate = result.debug_payload["portfolio_exposure_gate"]
    assert gate["combined_BTC_ETH_directional_exposure_pct"] > gate["limits"]["max_same_direction_major_exposure_pct"]
    event = db_session.query(AuditEvent).filter_by(event_type="correlated_exposure_blocked").one()
    assert event.entity_id == str(row.id)
    assert event.payload["reason_code"] == CORRELATED_EXPOSURE_LIMIT_REASON_CODE
    assert event.payload["combined_BTC_ETH_directional_exposure_pct"] == gate["combined_BTC_ETH_directional_exposure_pct"]


def test_portfolio_gate_handles_eth_short_as_directional_bias_not_same_direction(monkeypatch, db_session) -> None:
    _mock_expected_edge_gate_settings(
        monkeypatch,
        enabled=False,
        shadow=True,
        max_same_direction_major_exposure_pct=2.0,
    )
    settings_row = get_or_create_settings(db_session)
    settings_row.max_directional_bias_pct = 0.000001
    _seed_account_equity(db_session)
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=1.0,
            entry_price=65000.0,
            mark_price=65000.0,
            leverage=2.0,
            stop_loss=63000.0,
            take_profit=68000.0,
        )
    )
    db_session.flush()

    snapshot = build_market_snapshot("ETHUSDT", "15m", upto_index=140)
    price = float(snapshot.latest_price)
    decision = _entry_decision(
        symbol="ETHUSDT",
        decision="short",
        entry_zone_min=price * 0.999,
        entry_zone_max=price * 1.001,
        stop_loss=price * 1.03,
        take_profit=price * 0.95,
        invalidation_price=price * 1.035,
        max_chase_bps=100.0,
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
    )

    assert result.allowed is False
    assert "DIRECTIONAL_BIAS_LIMIT_REACHED" in result.reason_codes
    assert CORRELATED_EXPOSURE_LIMIT_REASON_CODE not in result.reason_codes
    gate = result.debug_payload["portfolio_exposure_gate"]
    assert gate["candidate_direction"] == "short"
    assert gate["combined_BTC_ETH_directional_exposure_pct"] < gate["limits"]["max_same_direction_major_exposure_pct"]
    assert db_session.query(AuditEvent).filter_by(event_type="directional_bias_blocked").one()
    assert db_session.query(AuditEvent).filter_by(event_type="correlated_exposure_blocked").count() == 0


def test_portfolio_gate_audits_candidate_exposure_when_no_positions(monkeypatch, db_session) -> None:
    _mock_expected_edge_gate_settings(
        monkeypatch,
        enabled=False,
        shadow=True,
        max_same_direction_major_exposure_pct=2.0,
    )
    settings_row = get_or_create_settings(db_session)
    _seed_account_equity(db_session)
    snapshot = build_market_snapshot("ETHUSDT", "15m", upto_index=140)
    price = float(snapshot.latest_price)
    decision = _entry_decision(
        symbol="ETHUSDT",
        entry_zone_min=price * 0.999,
        entry_zone_max=price * 1.001,
        stop_loss=price * 0.97,
        take_profit=price * 1.05,
        invalidation_price=price * 0.965,
        max_chase_bps=100.0,
    )

    result, row = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
    )

    assert result.allowed is True
    gate = result.debug_payload["portfolio_exposure_gate"]
    assert gate["current_notional_exposure"] == 0.0
    assert gate["candidate_notional_exposure"] > 0.0
    assert gate["total_notional_exposure"] == pytest.approx(gate["candidate_notional_exposure"])
    event = db_session.query(AuditEvent).filter_by(event_type="portfolio_exposure_evaluated").one()
    assert event.entity_id == str(row.id)
    assert event.payload["current_notional_exposure"] == 0.0
    assert event.payload["candidate_notional_exposure"] == gate["candidate_notional_exposure"]


def test_entry_is_auto_resized_when_raw_size_slightly_exceeds_single_position_limit(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _seed_account_equity(db_session)
    settings_row.max_largest_position_pct = 1.5
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    _mark_all_sync_scopes_fresh(settings_row)
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    decision = _entry_decision(
        symbol="BTCUSDT",
        entry_zone_min=entry_price - 25.0,
        entry_zone_max=entry_price + 25.0,
        stop_loss=entry_price - 10.0,
        take_profit=entry_price + 400.0,
        max_chase_bps=20.0,
    ).model_copy(
        update={
            "leverage": 1.58,
            "risk_pct": 0.01,
        }
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is True
    assert result.auto_resized_entry is True
    assert result.reason_codes == []
    assert result.blocked_reason_codes == []
    assert "ENTRY_AUTO_RESIZED" in result.adjustment_reason_codes
    assert "ENTRY_CLAMPED_TO_SINGLE_POSITION_LIMIT" in result.adjustment_reason_codes
    assert result.raw_projected_notional > result.approved_projected_notional
    assert result.approved_projected_notional <= 150000.0
    assert result.approved_quantity is not None and result.approved_quantity > 0


def test_fractional_approved_leverage_sizes_against_integer_exchange_leverage(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    equity = 233.34635614
    _seed_account_equity(db_session, equity=equity)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    settings_row.max_leverage = 2.0
    db_session.flush()

    snapshot = build_market_snapshot("ETHUSDT", "1m", upto_index=140)
    entry_price = snapshot.latest_price
    decision = _entry_decision(
        symbol="ETHUSDT",
        timeframe="1m",
        entry_zone_min=entry_price,
        entry_zone_max=entry_price,
        stop_loss=entry_price - 7.63,
        take_profit=entry_price + 20.0,
        max_chase_bps=20.0,
    ).model_copy(
        update={
            "risk_pct": 0.008919,
            "leverage": 1.445,
        }
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
    )

    assert result.allowed is True
    assert result.approved_leverage == 1.0
    assert result.approved_projected_notional <= equity + 1e-6
    assert result.approved_quantity is not None and result.approved_quantity > 0.0
    assert "ENTRY_CLAMPED_TO_EXCHANGE_LEVERAGE" in result.adjustment_reason_codes
    assert result.debug_payload["exchange_leverage"]["raw_approved_leverage"] == pytest.approx(1.445)
    assert result.debug_payload["exchange_leverage"]["approved_exchange_leverage"] == 1.0
    assert result.debug_payload["exchange_leverage"]["notional_cap"] == pytest.approx(equity)


def test_stale_sync_keeps_entry_blocked_without_auto_resize(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.max_largest_position_pct = 1.5
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    _mark_all_sync_scopes_fresh(settings_row)
    mark_sync_success(
        settings_row,
        scope="account",
        synced_at=utcnow_naive() - timedelta(hours=2),
        stale_after_seconds=60,
    )
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    entry_price = snapshot.latest_price
    decision = _entry_decision(
        symbol="BTCUSDT",
        entry_zone_min=entry_price - 25.0,
        entry_zone_max=entry_price + 25.0,
        stop_loss=entry_price - 10.0,
        take_profit=entry_price + 250.0,
        max_chase_bps=20.0,
    ).model_copy(
        update={
            "leverage": 1.58,
            "risk_pct": 0.01,
        }
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is False
    assert result.auto_resized_entry is False
    assert "ACCOUNT_STATE_STALE" in result.reason_codes
    assert "ENTRY_AUTO_RESIZED" not in result.reason_codes
    assert result.approved_projected_notional == 0.0
    assert result.approved_quantity is None


def test_entry_is_clamped_to_directional_headroom_when_that_is_smallest(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _seed_account_equity(db_session)
    settings_row.max_gross_exposure_pct = 1.5
    settings_row.max_directional_bias_pct = 0.6
    settings_row.max_largest_position_pct = 1.5
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.615385,
            entry_price=65000.0,
            mark_price=65000.0,
            leverage=2.0,
            stop_loss=63000.0,
            take_profit=68000.0,
        )
    )
    db_session.flush()

    snapshot = build_market_snapshot("ETHUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        symbol="ETHUSDT",
        entry_zone_min=snapshot.latest_price - 5.0,
        entry_zone_max=snapshot.latest_price + 5.0,
        stop_loss=snapshot.latest_price - 1.0,
        take_profit=snapshot.latest_price + 80.0,
        max_chase_bps=20.0,
    ).model_copy(
        update={
            "leverage": 1.0,
            "risk_pct": 0.01,
        }
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
    )

    assert result.allowed is True
    assert result.auto_resized_entry is True
    assert result.auto_resize_reason == "CLAMPED_TO_DIRECTIONAL_HEADROOM"
    assert result.approved_projected_notional == pytest.approx(20000.0, abs=5.0)
    assert result.exposure_headroom_snapshot["directional_headroom_notional"] == pytest.approx(20000.0, abs=5.0)


def test_entry_stays_blocked_when_remaining_headroom_is_below_minimum_order_size(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.max_directional_bias_pct = 0.6
    db_session.add(
        Position(
            symbol="ETHUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=18.746875,
            entry_price=3200.0,
            mark_price=3200.0,
            leverage=2.0,
            stop_loss=3100.0,
            take_profit=3400.0,
        )
    )
    db_session.flush()

    snapshot = build_market_snapshot("ETHUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        symbol="ETHUSDT",
        entry_zone_min=snapshot.latest_price - 5.0,
        entry_zone_max=snapshot.latest_price + 5.0,
        stop_loss=snapshot.latest_price - 1.0,
        take_profit=snapshot.latest_price + 80.0,
        max_chase_bps=20.0,
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
    )

    assert result.allowed is False
    assert result.auto_resized_entry is False
    assert "ENTRY_SIZE_BELOW_MIN_NOTIONAL" in result.reason_codes


def test_hard_blockers_keep_entry_blocked_even_when_exposure_could_be_resized(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = False
    settings_row.live_execution_armed = False
    settings_row.pause_reason_detail = {"operating_state": "PROTECTION_REQUIRED"}
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    _mark_all_sync_scopes_fresh(settings_row)
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140, force_stale=True)
    decision = _entry_decision(
        symbol="BTCUSDT",
        entry_zone_min=snapshot.latest_price - 25.0,
        entry_zone_max=snapshot.latest_price + 25.0,
        stop_loss=snapshot.latest_price - 10.0,
        take_profit=snapshot.latest_price + 250.0,
        max_chase_bps=20.0,
    ).model_copy(
        update={
            "leverage": 1.58,
            "risk_pct": 0.01,
        }
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is False
    assert result.auto_resized_entry is False
    assert "MARKET_STATE_STALE" in result.reason_codes
    assert "LIVE_APPROVAL_POLICY_DISABLED" in result.reason_codes
    assert "PROTECTION_REQUIRED" in result.reason_codes
    assert "ENTRY_AUTO_RESIZED" not in result.reason_codes


def test_protection_unverified_keeps_entry_blocked_without_auto_resize(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.max_largest_position_pct = 1.5
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    _mark_all_sync_scopes_fresh(settings_row)
    mark_sync_issue(
        settings_row,
        scope="protective_orders",
        status="incomplete",
        reason_code="PROTECTION_STATE_UNVERIFIED",
    )
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        symbol="BTCUSDT",
        entry_zone_min=snapshot.latest_price - 25.0,
        entry_zone_max=snapshot.latest_price + 25.0,
        stop_loss=snapshot.latest_price - 10.0,
        take_profit=snapshot.latest_price + 250.0,
        max_chase_bps=20.0,
    ).model_copy(update={"leverage": 1.58, "risk_pct": 0.01})

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is False
    assert result.auto_resized_entry is False
    assert "PROTECTION_STATE_UNVERIFIED" in result.reason_codes
    assert "ENTRY_AUTO_RESIZED" not in result.reason_codes
    assert result.approved_quantity is None


def test_flat_protective_order_staleness_does_not_block_when_position_scopes_are_fresh(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _seed_account_equity(db_session)
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    now = utcnow_naive()
    mark_sync_success(
        settings_row,
        scope="account",
        synced_at=now,
        detail={
            "exchange_can_trade": True,
            "exchange_can_trade_known": True,
            "exchange_can_trade_source": "unit_test",
            "exchange_can_trade_checked_at": now.isoformat(),
        },
    )
    mark_sync_success(settings_row, scope="positions", synced_at=now)
    mark_sync_success(settings_row, scope="open_orders", synced_at=now)
    mark_sync_success(
        settings_row,
        scope="protective_orders",
        synced_at=now - timedelta(seconds=120),
        stale_after_seconds=90,
        status="flat",
    )
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        symbol="BTCUSDT",
        entry_zone_min=snapshot.latest_price - 25.0,
        entry_zone_max=snapshot.latest_price + 25.0,
        stop_loss=snapshot.latest_price - 500.0,
        take_profit=snapshot.latest_price + 800.0,
        max_chase_bps=20.0,
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is True
    assert result.sync_freshness_summary["protective_orders"]["stale"] is True
    assert result.sync_freshness_summary["protective_orders"]["sync_detail_status"] == "flat"
    assert "PROTECTION_STATE_UNVERIFIED" not in result.reason_codes


def test_non_flat_protective_order_staleness_still_blocks_live_entries(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _seed_account_equity(db_session)
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    now = utcnow_naive()
    mark_sync_success(settings_row, scope="account", synced_at=now)
    mark_sync_success(settings_row, scope="positions", synced_at=now)
    mark_sync_success(settings_row, scope="open_orders", synced_at=now)
    mark_sync_success(
        settings_row,
        scope="protective_orders",
        synced_at=now - timedelta(seconds=120),
        stale_after_seconds=90,
    )
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        symbol="BTCUSDT",
        entry_zone_min=snapshot.latest_price - 25.0,
        entry_zone_max=snapshot.latest_price + 25.0,
        stop_loss=snapshot.latest_price - 500.0,
        take_profit=snapshot.latest_price + 800.0,
        max_chase_bps=20.0,
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is False
    assert "PROTECTION_STATE_UNVERIFIED" in result.reason_codes


def test_approval_closed_keeps_entry_blocked_without_auto_resize(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _seed_account_equity(db_session)
    settings_row.max_largest_position_pct = 1.5
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = None
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    _mark_all_sync_scopes_fresh(settings_row)
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        symbol="BTCUSDT",
        entry_zone_min=snapshot.latest_price - 25.0,
        entry_zone_max=snapshot.latest_price + 25.0,
        stop_loss=snapshot.latest_price - 10.0,
        take_profit=snapshot.latest_price + 250.0,
        max_chase_bps=20.0,
    ).model_copy(update={"leverage": 1.58, "risk_pct": 0.01})

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is False
    assert result.auto_resized_entry is False
    assert "LIVE_APPROVAL_REQUIRED" in result.reason_codes
    assert "LARGEST_POSITION_LIMIT_REACHED" not in result.reason_codes
    assert "LARGEST_POSITION_LIMIT_REACHED" in result.debug_payload["requested_exposure_limit_codes"]
    assert "ENTRY_AUTO_RESIZED" not in result.reason_codes
    assert result.approved_quantity is None


def test_trading_pause_keeps_entry_blocked_without_auto_resize(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _seed_account_equity(db_session)
    settings_row.max_largest_position_pct = 1.5
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.trading_paused = True
    settings_row.pause_reason_code = "MANUAL_USER_REQUEST"
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    _mark_all_sync_scopes_fresh(settings_row)
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        symbol="BTCUSDT",
        entry_zone_min=snapshot.latest_price - 25.0,
        entry_zone_max=snapshot.latest_price + 25.0,
        stop_loss=snapshot.latest_price - 10.0,
        take_profit=snapshot.latest_price + 250.0,
        max_chase_bps=20.0,
    ).model_copy(update={"leverage": 1.58, "risk_pct": 0.01})

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is False
    assert result.auto_resized_entry is False
    assert "TRADING_PAUSED" in result.reason_codes
    assert "LARGEST_POSITION_LIMIT_REACHED" not in result.reason_codes
    assert "LARGEST_POSITION_LIMIT_REACHED" in result.debug_payload["requested_exposure_limit_codes"]
    assert "ENTRY_AUTO_RESIZED" not in result.reason_codes
    assert result.approved_quantity is None


def test_reduce_and_exit_remain_allowed_under_exposure_limits(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    settings_row.max_gross_exposure_pct = 0.2
    settings_row.max_directional_bias_pct = 0.2
    settings_row.max_same_tier_concentration_pct = 0.2
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=1.0,
            entry_price=65000.0,
            mark_price=65000.0,
            leverage=2.0,
            stop_loss=63000.0,
            take_profit=68000.0,
        )
    )
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    reduce_decision = TradeDecision(
        decision="reduce",
        confidence=0.7,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=65000.0,
        entry_zone_max=65100.0,
        stop_loss=63000.0,
        take_profit=68000.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="reduce allowed",
        explanation_detailed="reduce-only management should stay allowed even when entry exposure caps are already exceeded.",
    )
    exit_decision = reduce_decision.model_copy(update={"decision": "exit", "explanation_short": "exit allowed"})

    reduce_result, _ = evaluate_risk(db_session, settings_row, reduce_decision, snapshot)
    exit_result, _ = evaluate_risk(db_session, settings_row, exit_decision, snapshot)

    assert reduce_result.allowed is True
    assert exit_result.allowed is True
    assert "GROSS_EXPOSURE_LIMIT_REACHED" not in reduce_result.reason_codes
    assert "DIRECTIONAL_BIAS_LIMIT_REACHED" not in exit_result.reason_codes


def test_reduce_remains_allowed_under_correlated_portfolio_limit(monkeypatch, db_session) -> None:
    _mock_expected_edge_gate_settings(
        monkeypatch,
        enabled=False,
        shadow=True,
        max_same_direction_major_exposure_pct=0.1,
    )
    settings_row = get_or_create_settings(db_session)
    settings_row.max_gross_exposure_pct = 0.1
    settings_row.max_directional_bias_pct = 0.1
    _seed_account_equity(db_session)
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=1.0,
            entry_price=65000.0,
            mark_price=65000.0,
            leverage=2.0,
            stop_loss=63000.0,
            take_profit=68000.0,
        )
    )
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    reduce_decision = TradeDecision(
        decision="reduce",
        confidence=0.7,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=65000.0,
        entry_zone_max=65100.0,
        stop_loss=63000.0,
        take_profit=68000.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="reduce allowed",
        explanation_detailed="reduce-only management must not be blocked by portfolio entry gates.",
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        reduce_decision,
        snapshot,
        execution_mode="historical_replay",
    )

    assert result.allowed is True
    assert result.debug_payload["portfolio_exposure_gate"]["applied"] is False
    assert CORRELATED_EXPOSURE_LIMIT_REASON_CODE not in result.reason_codes
    assert db_session.query(AuditEvent).filter_by(event_type="correlated_exposure_blocked").count() == 0


def test_alt_entry_blocks_when_lead_context_unavailable_but_reduce_survives(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    snapshot = build_market_snapshot("SOLUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        symbol="SOLUSDT",
        entry_zone_min=snapshot.latest_price * 0.999,
        entry_zone_max=snapshot.latest_price * 1.001,
        stop_loss=snapshot.latest_price * 0.97,
        take_profit=snapshot.latest_price * 1.04,
        max_chase_bps=100.0,
    )
    lead_context = {
        "lead_context_status": "unavailable",
        "missing_lead_symbols": ["BTCUSDT", "ETHUSDT"],
        "reason_codes": ["LEAD_CONTEXT_UNAVAILABLE"],
    }

    entry_result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context={"lead_market_context": lead_context},
    )
    reduce_result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision.model_copy(update={"decision": "reduce", "entry_mode": "none", "max_chase_bps": None}),
        snapshot,
        execution_mode="historical_replay",
        decision_context={"lead_market_context": lead_context},
    )

    assert entry_result.allowed is False
    assert "ALT_ENTRY_LEAD_CONTEXT_UNAVAILABLE" in entry_result.reason_codes
    assert entry_result.debug_payload["lead_market_context"]["lead_context_status"] == "unavailable"
    assert reduce_result.allowed is True
    assert "ALT_ENTRY_LEAD_CONTEXT_UNAVAILABLE" not in reduce_result.reason_codes


def test_live_entry_keeps_existing_path_when_sync_state_is_fresh(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _seed_account_equity(db_session)
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    _mark_all_sync_scopes_fresh(settings_row)
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        entry_zone_min=snapshot.latest_price - 50.0,
        entry_zone_max=snapshot.latest_price + 50.0,
        stop_loss=snapshot.latest_price - 500.0,
        take_profit=snapshot.latest_price + 800.0,
        entry_mode="immediate",
        max_chase_bps=20.0,
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is True
    assert "ACCOUNT_STATE_STALE" not in result.reason_codes
    assert "POSITION_STATE_STALE" not in result.reason_codes
    assert "OPEN_ORDERS_STATE_STALE" not in result.reason_codes
    assert "PROTECTION_STATE_UNVERIFIED" not in result.reason_codes


def test_full_live_armed_blocks_entry_and_scale_in_when_exchange_can_trade_unknown(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _seed_account_equity(db_session)
    settings_row.live_trading_enabled = True
    settings_row.rollout_mode = "full_live"
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    _mark_all_sync_scopes_fresh(settings_row)
    now = utcnow_naive()
    mark_sync_success(
        settings_row,
        scope="account",
        synced_at=now,
        detail={
            "exchange_can_trade": None,
            "exchange_can_trade_known": False,
            "exchange_can_trade_source": "binance_account_info_missing_canTrade",
            "exchange_can_trade_checked_at": now.isoformat(),
        },
    )
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        entry_zone_min=snapshot.latest_price - 50.0,
        entry_zone_max=snapshot.latest_price + 50.0,
        stop_loss=snapshot.latest_price - 500.0,
        take_profit=snapshot.latest_price + 800.0,
        entry_mode="immediate",
        max_chase_bps=20.0,
    )

    entry_result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.01,
            entry_price=snapshot.latest_price - 200.0,
            mark_price=snapshot.latest_price,
            leverage=2.0,
            stop_loss=snapshot.latest_price - 500.0,
            take_profit=snapshot.latest_price + 800.0,
            unrealized_pnl=2.0,
        )
    )
    db_session.flush()
    scale_in_result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert entry_result.allowed is False
    assert scale_in_result.allowed is False
    assert "EXCHANGE_CAN_TRADE_UNKNOWN" in entry_result.reason_codes
    assert "EXCHANGE_CAN_TRADE_UNKNOWN" in scale_in_result.reason_codes
    assert entry_result.debug_payload["exchange_permission_entry_block"]["exchange_can_trade_source"] == (
        "binance_account_info_missing_canTrade"
    )
    assert scale_in_result.debug_payload["exchange_permission_entry_block"]["reason_code"] == (
        "EXCHANGE_CAN_TRADE_UNKNOWN"
    )


def test_risk_blocks_plain_immediate_entry_without_confirmed_trigger_context(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    _mark_all_sync_scopes_fresh(settings_row)
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        entry_zone_min=snapshot.latest_price - 50.0,
        entry_zone_max=snapshot.latest_price + 50.0,
        stop_loss=snapshot.latest_price - 500.0,
        take_profit=snapshot.latest_price + 800.0,
        entry_mode="immediate",
        rationale_codes=["TEST"],
        max_chase_bps=20.0,
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is False
    assert "ENTRY_TRIGGER_NOT_MET" in result.reason_codes
    assert result.debug_payload["entry_trigger"]["immediate_allowed"] is False


def test_risk_blocks_entry_when_breakout_trigger_is_not_met(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    _mark_all_sync_scopes_fresh(settings_row)
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        entry_zone_min=snapshot.latest_price + 800.0,
        entry_zone_max=snapshot.latest_price + 1000.0,
        stop_loss=snapshot.latest_price - 400.0,
        take_profit=snapshot.latest_price + 1500.0,
        entry_mode="breakout_confirm",
        max_chase_bps=30.0,
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is False
    assert "ENTRY_TRIGGER_NOT_MET" in result.reason_codes


def test_risk_blocks_entry_when_chase_limit_is_exceeded(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    _mark_all_sync_scopes_fresh(settings_row)
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        entry_zone_min=snapshot.latest_price - 400.0,
        entry_zone_max=snapshot.latest_price - 300.0,
        stop_loss=snapshot.latest_price - 800.0,
        take_profit=snapshot.latest_price + 900.0,
        entry_mode="immediate",
        max_chase_bps=10.0,
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is False
    assert "CHASE_LIMIT_EXCEEDED" in result.reason_codes


def test_risk_blocks_entry_with_invalid_invalidation_price(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    _mark_all_sync_scopes_fresh(settings_row)
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        entry_zone_min=snapshot.latest_price - 50.0,
        entry_zone_max=snapshot.latest_price + 50.0,
        stop_loss=snapshot.latest_price - 500.0,
        take_profit=snapshot.latest_price + 900.0,
        entry_mode="immediate",
        invalidation_price=snapshot.latest_price + 25.0,
        max_chase_bps=25.0,
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is False
    assert "INVALID_INVALIDATION_PRICE" in result.reason_codes


def test_reduce_path_ignores_entry_trigger_requirements(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    mark_sync_issue(settings_row, scope="account", status="failed", reason_code="ACCOUNT_STATE_STALE")
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        decision="reduce",
        entry_mode="breakout_confirm",
        invalidation_price=snapshot.latest_price + 1000.0,
        max_chase_bps=1.0,
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is True
    assert "ENTRY_TRIGGER_NOT_MET" not in result.reason_codes
    assert "INVALID_INVALIDATION_PRICE" not in result.reason_codes


def test_live_entry_is_blocked_when_exchange_state_is_stale(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    stale_at = utcnow_naive() - timedelta(hours=2)
    mark_sync_success(settings_row, scope="account", synced_at=stale_at, stale_after_seconds=60)
    mark_sync_success(settings_row, scope="positions", synced_at=utcnow_naive())
    mark_sync_success(settings_row, scope="open_orders", synced_at=utcnow_naive())
    mark_sync_issue(
        settings_row,
        scope="protective_orders",
        status="incomplete",
        reason_code="PROTECTION_STATE_UNVERIFIED",
    )
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = TradeDecision(
        decision="long",
        confidence=0.7,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=65000.0,
        entry_zone_max=65100.0,
        stop_loss=64000.0,
        take_profit=66500.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="stale sync",
        explanation_detailed="Stale or incomplete exchange state should block new entries.",
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is False
    assert "ACCOUNT_STATE_STALE" in result.reason_codes
    assert "PROTECTION_STATE_UNVERIFIED" in result.reason_codes


def test_reduce_path_stays_open_when_exchange_state_is_stale(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    mark_sync_issue(settings_row, scope="account", status="failed", reason_code="ACCOUNT_STATE_STALE")
    mark_sync_issue(settings_row, scope="positions", status="failed", reason_code="POSITION_STATE_STALE")
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = TradeDecision(
        decision="reduce",
        confidence=0.7,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=65000.0,
        entry_zone_max=65100.0,
        stop_loss=64000.0,
        take_profit=66500.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="stale but reduce",
        explanation_detailed="Reduce-only path should remain available even when entry state freshness is degraded.",
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is True
    assert "ACCOUNT_STATE_STALE" not in result.reason_codes
    assert "POSITION_STATE_STALE" not in result.reason_codes


def test_time_stop_exit_path_stays_open_when_exchange_state_is_stale(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    mark_sync_issue(settings_row, scope="account", status="failed", reason_code="ACCOUNT_STATE_STALE")
    mark_sync_issue(settings_row, scope="positions", status="failed", reason_code="POSITION_STATE_STALE")
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = TradeDecision(
        decision="exit",
        confidence=0.8,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=65000.0,
        entry_zone_max=65100.0,
        stop_loss=64000.0,
        take_profit=66500.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["POSITION_MANAGEMENT_TIME_STOP_EXIT"],
        explanation_short="time stop exit",
        explanation_detailed="Time stop exit should remain available even when entry freshness checks are degraded.",
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is True
    assert "ACCOUNT_STATE_STALE" not in result.reason_codes
    assert "POSITION_STATE_STALE" not in result.reason_codes


def test_unprotected_state_blocks_new_entry_but_allows_reduce(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    settings_row.pause_reason_detail = {
        "operating_state": "PROTECTION_REQUIRED",
        "protection_recovery": {
            "status": "recreating",
            "auto_recovery_active": True,
            "symbol_states": {
                "BTCUSDT": {
                    "state": "PROTECTION_REQUIRED",
                    "missing_components": ["take_profit"],
                    "failure_count": 1,
                }
            },
            "missing_symbols": ["BTCUSDT"],
            "missing_items": {"BTCUSDT": ["take_profit"]},
        },
    }
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.01,
            entry_price=70000.0,
            mark_price=70100.0,
            leverage=2.0,
            stop_loss=69000.0,
            take_profit=72000.0,
        )
    )
    db_session.flush()

    entry_snapshot = build_market_snapshot("ETHUSDT", "15m", upto_index=140)
    entry_decision = TradeDecision(
        decision="long",
        confidence=0.7,
        symbol="ETHUSDT",
        timeframe="15m",
        entry_zone_min=3200.0,
        entry_zone_max=3210.0,
        stop_loss=3100.0,
        take_profit=3340.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="blocked entry",
        explanation_detailed="entry should be blocked while protection recovery is required.",
    )
    reduce_snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    reduce_decision = TradeDecision(
        decision="reduce",
        confidence=0.7,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=65000.0,
        entry_zone_max=65100.0,
        stop_loss=69000.0,
        take_profit=72000.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="reduce allowed",
        explanation_detailed="reduce-only management should remain allowed while entry is blocked.",
    )

    entry_result, _ = evaluate_risk(db_session, settings_row, entry_decision, entry_snapshot)
    reduce_result, _ = evaluate_risk(db_session, settings_row, reduce_decision, reduce_snapshot)

    assert entry_result.allowed is False
    assert "PROTECTION_REQUIRED" in entry_result.reason_codes
    assert reduce_result.allowed is True


def test_invalid_protection_recovery_output_is_blocked(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.pause_reason_detail = {
        "operating_state": "PROTECTION_REQUIRED",
        "protection_recovery": {
            "status": "recreating",
            "auto_recovery_active": True,
            "symbol_states": {
                "BTCUSDT": {
                    "state": "PROTECTION_REQUIRED",
                    "missing_components": ["stop_loss", "take_profit"],
                    "failure_count": 1,
                }
            },
        },
    }
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.01,
            entry_price=70000.0,
            mark_price=69950.0,
            leverage=2.0,
            stop_loss=69000.0,
            take_profit=72000.0,
        )
    )
    db_session.flush()

    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = TradeDecision(
        decision="long",
        confidence=0.65,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=69900.0,
        entry_zone_max=70050.0,
        stop_loss=70500.0,
        take_profit=69500.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST"],
        intent_family="protection",
        management_action="restore_protection",
        explanation_short="bad protection",
        explanation_detailed="protection recovery with invalid brackets should still be blocked by risk guard.",
    )

    result, _ = evaluate_risk(db_session, settings_row, decision, snapshot)

    assert result.allowed is False
    assert "INVALID_PROTECTION_BRACKETS" in result.reason_codes


def test_underperforming_setup_disable_blocks_new_entry_but_not_reduce_path(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    entry_decision = _entry_decision(rationale_codes=[ADAPTIVE_SETUP_DISABLE_REASON_CODE])
    reduce_decision = _entry_decision(
        decision="reduce",
        entry_mode="none",
        max_chase_bps=None,
        rationale_codes=[ADAPTIVE_SETUP_DISABLE_REASON_CODE],
    )

    entry_result, _ = evaluate_risk(db_session, settings_row, entry_decision, snapshot)
    reduce_result, _ = evaluate_risk(db_session, settings_row, reduce_decision, snapshot)

    assert entry_result.allowed is False
    assert ADAPTIVE_SETUP_DISABLE_REASON_CODE in entry_result.reason_codes
    assert ADAPTIVE_SETUP_DISABLE_REASON_CODE in entry_result.blocked_reason_codes
    assert entry_result.debug_payload["adaptive_setup_disable"]["active"] is True
    assert ADAPTIVE_SETUP_DISABLE_REASON_CODE not in reduce_result.reason_codes
    assert ADAPTIVE_SETUP_DISABLE_REASON_CODE not in reduce_result.blocked_reason_codes


def test_setup_cluster_disable_blocks_new_entry_but_not_reduce_path(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    entry_decision = _entry_decision(
        entry_mode="pullback_confirm",
        entry_zone_min=snapshot.latest_price - 20.0,
        entry_zone_max=snapshot.latest_price + 20.0,
        stop_loss=snapshot.latest_price - 400.0,
        take_profit=snapshot.latest_price + 700.0,
        max_chase_bps=100.0,
    )
    reduce_decision = _entry_decision(
        decision="reduce",
        entry_mode="none",
        max_chase_bps=None,
        rationale_codes=["TEST"],
    )
    cluster_state = {
        "matched": True,
        "active": True,
        "cluster_key": "BTCUSDT|15m|pullback_entry|pullback_confirm|bullish|bullish_aligned",
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
        "regime": "bullish",
        "trend_alignment": "bullish_aligned",
        "scenario": "pullback_entry",
        "entry_mode": "pullback_confirm",
    }

    entry_result, _ = evaluate_risk(
        db_session,
        settings_row,
        entry_decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context={"setup_cluster_state": cluster_state},
    )
    reduce_result, _ = evaluate_risk(
        db_session,
        settings_row,
        reduce_decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context={"setup_cluster_state": cluster_state},
    )

    assert entry_result.allowed is False
    assert "SETUP_CLUSTER_DISABLED" in entry_result.reason_codes
    assert entry_result.debug_payload["setup_cluster_state"]["active"] is True
    assert entry_result.debug_payload["setup_cluster_state"]["status"] == "active_disabled"
    assert entry_result.debug_payload["setup_cluster_state"]["cooldown_active"] is True
    assert reduce_result.allowed is True
    assert "SETUP_CLUSTER_DISABLED" not in reduce_result.reason_codes


def test_recent_performance_soft_bias_does_not_block_entry_but_is_exposed_in_debug_payload(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _seed_account_equity(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    entry_decision = _entry_decision(
        entry_mode="pullback_confirm",
        rationale_codes=["ADAPTIVE_HOLD_BIAS", "ADAPTIVE_SIGNAL_UNDERPERFORMING"],
        entry_zone_min=snapshot.latest_price - 20.0,
        entry_zone_max=snapshot.latest_price + 20.0,
        stop_loss=snapshot.latest_price - 400.0,
        take_profit=snapshot.latest_price + 700.0,
        max_chase_bps=100.0,
    )

    entry_result, _ = evaluate_risk(
        db_session,
        settings_row,
        entry_decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context={
            "suppression_context": {
                "level": "soft_bias",
                "sources": ["adaptive_hold_bias"],
                "reason_codes": ["ADAPTIVE_HOLD_BIAS", "ADAPTIVE_SIGNAL_UNDERPERFORMING"],
                "applies_hard_block": False,
                "applies_risk_haircut": False,
                "applies_soft_bias": True,
            }
        },
    )

    assert entry_result.allowed is True
    assert "ADAPTIVE_HOLD_BIAS" not in entry_result.blocked_reason_codes
    assert entry_result.debug_payload["suppression_context"]["level"] == "soft_bias"
    assert entry_result.debug_payload["suppression_context"]["applies_hard_block"] is False


def test_full_agreement_keeps_entry_size_and_leverage(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _seed_account_equity(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        entry_mode="pullback_confirm",
        entry_zone_min=snapshot.latest_price - 20.0,
        entry_zone_max=snapshot.latest_price + 20.0,
        stop_loss=snapshot.latest_price - 400.0,
        take_profit=snapshot.latest_price + 700.0,
        max_chase_bps=100.0,
    )

    baseline_result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
    )
    full_result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context=_decision_context(
            "full_agreement",
            baseline_entry_mode="pullback_confirm",
            final_entry_mode="pullback_confirm",
        ),
    )

    assert baseline_result.allowed is True
    assert full_result.allowed is True
    assert full_result.approved_projected_notional == pytest.approx(
        baseline_result.approved_projected_notional,
        rel=1e-6,
    )
    assert full_result.approved_risk_pct == pytest.approx(baseline_result.approved_risk_pct, rel=1e-6)
    assert full_result.approved_leverage == pytest.approx(baseline_result.approved_leverage, rel=1e-6)
    assert full_result.debug_payload["decision_agreement"]["level"] == "full_agreement"
    assert full_result.debug_payload["decision_agreement"]["applies_soft_limit"] is False


def test_partial_agreement_reduces_entry_size_and_risk_budget(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _seed_account_equity(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        entry_mode="pullback_confirm",
        entry_zone_min=snapshot.latest_price - 20.0,
        entry_zone_max=snapshot.latest_price + 20.0,
        stop_loss=snapshot.latest_price - 400.0,
        take_profit=snapshot.latest_price + 700.0,
        max_chase_bps=100.0,
    )

    baseline_result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
    )
    partial_result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context=_decision_context(
            "partial_agreement",
            baseline_entry_mode="breakout_confirm",
            final_entry_mode="pullback_confirm",
        ),
    )

    assert partial_result.allowed is True
    assert partial_result.approved_projected_notional < baseline_result.approved_projected_notional
    assert partial_result.approved_risk_pct < baseline_result.approved_risk_pct
    assert partial_result.approved_leverage < baseline_result.approved_leverage
    assert partial_result.debug_payload["decision_agreement"]["level"] == "partial_agreement"
    assert partial_result.debug_payload["decision_agreement"]["applies_soft_limit"] is True
    assert partial_result.debug_payload["decision_agreement"]["notional_multiplier"] == pytest.approx(0.7, rel=1e-6)


def test_disagreement_blocks_new_entry_but_not_survival_path(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    entry_decision = _entry_decision()
    reduce_decision = _entry_decision(
        decision="reduce",
        entry_mode="none",
        max_chase_bps=None,
        rationale_codes=["TEST"],
    )

    entry_result, _ = evaluate_risk(
        db_session,
        settings_row,
        entry_decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context=_decision_context(
            "disagreement",
            baseline_decision="hold",
            baseline_entry_mode="none",
            final_decision="long",
            final_entry_mode="immediate",
        ),
    )
    reduce_result, _ = evaluate_risk(
        db_session,
        settings_row,
        reduce_decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context=_decision_context(
            "disagreement",
            baseline_decision="hold",
            baseline_entry_mode="none",
            final_decision="reduce",
            final_entry_mode="none",
        ),
    )

    assert entry_result.allowed is False
    assert "DETERMINISTIC_BASELINE_DISAGREEMENT" in entry_result.reason_codes
    assert "DETERMINISTIC_BASELINE_DISAGREEMENT" in entry_result.blocked_reason_codes
    assert entry_result.debug_payload["decision_agreement"]["blocked_reason_code"] == "DETERMINISTIC_BASELINE_DISAGREEMENT"
    assert reduce_result.allowed is True
    assert "DETERMINISTIC_BASELINE_DISAGREEMENT" not in reduce_result.reason_codes


def test_same_hold_agreement_payload_is_not_treated_as_baseline_disagreement(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        _entry_decision(),
        snapshot,
        execution_mode="historical_replay",
        decision_context=_decision_context(
            "disagreement",
            baseline_decision="hold",
            baseline_entry_mode="none",
            final_decision="hold",
            final_entry_mode="none",
        ),
    )

    assert "DETERMINISTIC_BASELINE_DISAGREEMENT" not in result.reason_codes
    assert result.debug_payload["decision_agreement"]["level"] == "full_agreement"
    assert result.debug_payload["decision_agreement"]["direction_match"] is True


def test_meta_gate_soft_pass_downsizes_entry_without_blocking(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _seed_account_equity(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        entry_mode="pullback_confirm",
        entry_zone_min=snapshot.latest_price - 20.0,
        entry_zone_max=snapshot.latest_price + 20.0,
        stop_loss=snapshot.latest_price - 400.0,
        take_profit=snapshot.latest_price + 700.0,
        max_chase_bps=100.0,
    )

    baseline_result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
    )
    soft_result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context=_meta_gate_context(
            "soft_pass",
            expected_hit_probability=0.49,
            risk_multiplier=0.72,
            leverage_multiplier=0.85,
            notional_multiplier=0.65,
        ),
    )

    assert soft_result.allowed is True
    assert soft_result.approved_projected_notional < baseline_result.approved_projected_notional
    assert soft_result.approved_risk_pct < baseline_result.approved_risk_pct
    assert soft_result.approved_leverage < baseline_result.approved_leverage
    assert "META_GATE_SOFT_PASS" in soft_result.adjustment_reason_codes
    assert soft_result.debug_payload["meta_gate"]["gate_decision"] == "soft_pass"
    assert soft_result.debug_payload["meta_gate"]["applies_soft_limit"] is True


def test_meta_gate_reject_blocks_new_entry_but_not_survival_path(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    entry_decision = _entry_decision(entry_mode="pullback_confirm")
    reduce_decision = _entry_decision(
        decision="reduce",
        entry_mode="none",
        max_chase_bps=None,
        rationale_codes=["TEST"],
    )
    meta_gate_reject = _meta_gate_context(
        "reject",
        expected_hit_probability=0.24,
        risk_multiplier=0.0,
        leverage_multiplier=0.0,
        notional_multiplier=0.0,
        reject_reason_codes=["META_GATE_LOW_HIT_PROBABILITY", "META_GATE_NEGATIVE_EXPECTANCY"],
    )

    entry_result, _ = evaluate_risk(
        db_session,
        settings_row,
        entry_decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context=meta_gate_reject,
    )
    reduce_result, _ = evaluate_risk(
        db_session,
        settings_row,
        reduce_decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context=meta_gate_reject,
    )

    assert entry_result.allowed is False
    assert "META_GATE_LOW_HIT_PROBABILITY" in entry_result.reason_codes
    assert "META_GATE_NEGATIVE_EXPECTANCY" in entry_result.blocked_reason_codes
    assert entry_result.debug_payload["meta_gate"]["gate_decision"] == "reject"
    assert reduce_result.allowed is True
    assert "META_GATE_LOW_HIT_PROBABILITY" not in reduce_result.reason_codes


def test_holding_profile_swing_downsizes_entry_when_meta_gate_passes(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _seed_account_equity(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    reference_price = snapshot.latest_price
    decision = _entry_decision(
        entry_mode="pullback_confirm",
        entry_zone_min=reference_price * 0.999,
        entry_zone_max=reference_price * 1.001,
        stop_loss=reference_price * 0.985,
        take_profit=reference_price * 1.025,
        max_chase_bps=100.0,
    ).model_copy(
        update={
            "holding_profile": "swing",
            "holding_profile_reason": "swing_test",
        }
    )

    baseline_result, _ = evaluate_risk(
        db_session,
        settings_row,
        _entry_decision(
            entry_mode="pullback_confirm",
            entry_zone_min=reference_price * 0.999,
            entry_zone_max=reference_price * 1.001,
            stop_loss=reference_price * 0.985,
            take_profit=reference_price * 1.025,
            max_chase_bps=100.0,
        ),
        snapshot,
        execution_mode="historical_replay",
        decision_context={
            **_meta_gate_context(
                "pass",
                expected_hit_probability=0.72,
                risk_multiplier=1.0,
                leverage_multiplier=1.0,
                notional_multiplier=1.0,
            ),
            **_decision_context(
                "full_agreement",
                baseline_entry_mode="pullback_confirm",
                final_entry_mode="pullback_confirm",
            ),
        },
    )
    swing_result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context={
            **_holding_profile_context("swing"),
            **_meta_gate_context(
                "pass",
                expected_hit_probability=0.72,
                risk_multiplier=1.0,
                leverage_multiplier=1.0,
                notional_multiplier=1.0,
            ),
            **_decision_context(
                "full_agreement",
                baseline_entry_mode="pullback_confirm",
                final_entry_mode="pullback_confirm",
            ),
        },
    )

    assert baseline_result.allowed is True
    assert swing_result.allowed is True
    assert swing_result.approved_projected_notional < baseline_result.approved_projected_notional
    assert swing_result.approved_risk_pct < baseline_result.approved_risk_pct
    assert swing_result.approved_leverage < baseline_result.approved_leverage
    assert "HOLDING_PROFILE_SWING_SOFT_CAP" in swing_result.adjustment_reason_codes
    assert swing_result.debug_payload["holding_profile"]["holding_profile"] == "swing"


def test_holding_profile_position_blocks_when_structural_regime_is_not_strong(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    reference_price = snapshot.latest_price
    decision = _entry_decision(
        entry_mode="pullback_confirm",
        entry_zone_min=reference_price * 0.999,
        entry_zone_max=reference_price * 1.001,
        stop_loss=reference_price * 0.985,
        take_profit=reference_price * 1.018,
        max_chase_bps=100.0,
    ).model_copy(
        update={
            "holding_profile": "position",
            "holding_profile_reason": "position_test",
        }
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context={
            **_holding_profile_context("position", structural_alignment_strong=False),
            **_meta_gate_context(
                "pass",
                expected_hit_probability=0.76,
                risk_multiplier=1.0,
                leverage_multiplier=1.0,
                notional_multiplier=1.0,
            ),
            **_decision_context(
                "full_agreement",
                baseline_entry_mode="pullback_confirm",
                final_entry_mode="pullback_confirm",
            ),
        },
    )

    assert result.allowed is False
    assert "HOLDING_PROFILE_POSITION_REQUIRES_STRONG_REGIME" in result.reason_codes
    assert result.debug_payload["holding_profile"]["holding_profile"] == "position"


def test_holding_profile_swing_requires_meta_gate_pass_for_new_entry(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    reference_price = snapshot.latest_price
    decision = _entry_decision(
        entry_mode="pullback_confirm",
        entry_zone_min=reference_price * 0.999,
        entry_zone_max=reference_price * 1.001,
        stop_loss=reference_price * 0.985,
        take_profit=reference_price * 1.015,
        max_chase_bps=100.0,
    ).model_copy(
        update={
            "holding_profile": "swing",
            "holding_profile_reason": "swing_test",
        }
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context={
            **_holding_profile_context("swing"),
            **_meta_gate_context(
                "soft_pass",
                expected_hit_probability=0.52,
                risk_multiplier=0.72,
                leverage_multiplier=0.85,
                notional_multiplier=0.65,
            ),
            **_decision_context(
                "full_agreement",
                baseline_entry_mode="pullback_confirm",
                final_entry_mode="pullback_confirm",
            ),
        },
    )

    assert result.allowed is False
    assert "HOLDING_PROFILE_REQUIRES_META_GATE_PASS" in result.reason_codes
    assert result.debug_payload["holding_profile"]["meta_gate_decision"] == "soft_pass"


def test_holding_profile_blocks_do_not_apply_to_survival_paths(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        decision="reduce",
        entry_mode="none",
        max_chase_bps=None,
        rationale_codes=["TEST"],
    ).model_copy(
        update={
            "holding_profile": "position",
            "holding_profile_reason": "position_test",
        }
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context={
            **_holding_profile_context(
                "position",
                structural_alignment_strong=False,
                breadth_not_weak=False,
                lead_lag_positive=False,
                relative_strength_positive=False,
                derivatives_headwind_severe=True,
            ),
            **_meta_gate_context(
                "reject",
                expected_hit_probability=0.22,
                risk_multiplier=0.0,
                leverage_multiplier=0.0,
                notional_multiplier=0.0,
                reject_reason_codes=["META_GATE_LOW_HIT_PROBABILITY"],
            ),
        },
    )

    assert result.allowed is True
    assert "HOLDING_PROFILE_REQUIRES_META_GATE_PASS" not in result.reason_codes
    assert "HOLDING_PROFILE_POSITION_REQUIRES_STRONG_REGIME" not in result.reason_codes


def test_portfolio_slot_soft_cap_downsizes_medium_conviction_entry(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _seed_account_equity(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    reference_price = snapshot.latest_price
    decision = _entry_decision(
        entry_zone_min=reference_price * 0.999,
        entry_zone_max=reference_price * 1.001,
        stop_loss=reference_price * 0.985,
        take_profit=reference_price * 1.03,
        invalidation_price=reference_price * 0.985,
        max_chase_bps=120.0,
    )

    baseline_result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
    )
    slot_result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context=_slot_allocation_context(
            assigned_slot="slot_2",
            candidate_weight=0.38,
            risk_pct_multiplier=0.82,
            leverage_multiplier=0.9,
            notional_multiplier=0.78,
        ),
    )

    assert baseline_result.allowed is True
    assert slot_result.allowed is True
    assert slot_result.approved_projected_notional < baseline_result.approved_projected_notional
    assert slot_result.approved_risk_pct < baseline_result.approved_risk_pct
    assert slot_result.approved_leverage < baseline_result.approved_leverage
    assert "PORTFOLIO_SLOT_SOFT_CAP" in slot_result.adjustment_reason_codes
    assert slot_result.debug_payload["slot_allocation"]["assigned_slot"] == "slot_2"
    assert slot_result.debug_payload["slot_allocation"]["candidate_weight"] == pytest.approx(0.38, rel=1e-6)
    assert slot_result.debug_payload["slot_allocation"]["applies_soft_limit"] is True


def test_portfolio_slot_soft_cap_does_not_override_exposure_hard_block(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    settings_row.max_directional_bias_pct = 0.7
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=1.0,
            entry_price=65000.0,
            mark_price=65000.0,
            leverage=2.0,
            stop_loss=63000.0,
            take_profit=68000.0,
        )
    )
    db_session.flush()

    snapshot = build_market_snapshot("ETHUSDT", "15m", upto_index=140)
    decision = TradeDecision(
        decision="long",
        confidence=0.7,
        symbol="ETHUSDT",
        timeframe="15m",
        entry_zone_min=3200.0,
        entry_zone_max=3210.0,
        stop_loss=3150.0,
        take_profit=3330.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="slot soft cap must not override exposure hard block",
        explanation_detailed="Directional exposure blockers stay higher priority than slot-based soft downsize.",
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context=_slot_allocation_context(
            assigned_slot="slot_1",
            candidate_weight=0.64,
            risk_pct_multiplier=1.0,
            leverage_multiplier=1.0,
            notional_multiplier=1.0,
            slot_conviction_score=0.81,
            meta_gate_probability=0.74,
            agreement_alignment_score=0.78,
            execution_quality_score=0.72,
        ),
    )

    assert result.allowed is False
    assert "DIRECTIONAL_BIAS_LIMIT_REACHED" in result.reason_codes
    assert result.debug_payload["slot_allocation"]["assigned_slot"] == "slot_1"


def test_drawdown_caution_downsizes_entry_without_breaking_hard_gates(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_drawdown_pnl_snapshot(
        db_session,
        equity=100000.0,
        net_pnl=0.0,
        daily_pnl=0.0,
        consecutive_losses=0,
        minutes_ago=40,
    )
    _seed_drawdown_pnl_snapshot(
        db_session,
        equity=98200.0,
        net_pnl=-1800.0,
        daily_pnl=-900.0,
        consecutive_losses=2,
        minutes_ago=5,
    )
    db_session.flush()
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        entry_mode="pullback_confirm",
        entry_zone_min=snapshot.latest_price - 20.0,
        entry_zone_max=snapshot.latest_price + 20.0,
        stop_loss=snapshot.latest_price - 400.0,
        take_profit=snapshot.latest_price + 700.0,
        max_chase_bps=100.0,
    )

    caution_result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
    )

    assert caution_result.allowed is True
    assert caution_result.approved_projected_notional > 0.0
    assert caution_result.approved_risk_pct < decision.risk_pct
    assert caution_result.approved_leverage < decision.leverage
    assert "DRAWDOWN_STATE_CAUTION" in caution_result.adjustment_reason_codes
    assert caution_result.debug_payload["drawdown_state"]["current_drawdown_state"] == "caution"
    assert caution_result.debug_payload["drawdown_state"]["policy_adjustments"]["risk_pct_multiplier"] == 0.75


def test_drawdown_containment_blocks_breakout_and_losing_pyramiding_but_not_reduce(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_drawdown_pnl_snapshot(
        db_session,
        equity=100000.0,
        net_pnl=0.0,
        daily_pnl=0.0,
        consecutive_losses=0,
        minutes_ago=40,
    )
    _seed_drawdown_pnl_snapshot(
        db_session,
        equity=95800.0,
        net_pnl=-4200.0,
        daily_pnl=-1600.0,
        consecutive_losses=2,
        minutes_ago=5,
    )
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.25,
            entry_price=65000.0,
            mark_price=64850.0,
            leverage=2.0,
            unrealized_pnl=-37.5,
            stop_loss=64000.0,
            take_profit=66500.0,
        )
    )
    db_session.flush()
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    breakout_decision = _entry_decision(
        entry_mode="breakout_confirm",
        entry_zone_min=snapshot.latest_price - 20.0,
        entry_zone_max=snapshot.latest_price + 20.0,
        stop_loss=snapshot.latest_price - 400.0,
        take_profit=snapshot.latest_price + 700.0,
        max_chase_bps=100.0,
    )
    pullback_scale_in = _entry_decision(
        entry_mode="pullback_confirm",
        entry_zone_min=snapshot.latest_price - 20.0,
        entry_zone_max=snapshot.latest_price + 20.0,
        stop_loss=snapshot.latest_price - 400.0,
        take_profit=snapshot.latest_price + 700.0,
        max_chase_bps=100.0,
    )
    reduce_decision = _entry_decision(
        decision="reduce",
        entry_mode="none",
        max_chase_bps=None,
        rationale_codes=["TEST"],
    )

    breakout_result, _ = evaluate_risk(
        db_session,
        settings_row,
        breakout_decision,
        snapshot,
        execution_mode="historical_replay",
    )
    scale_in_result, _ = evaluate_risk(
        db_session,
        settings_row,
        pullback_scale_in,
        snapshot,
        execution_mode="historical_replay",
    )
    reduce_result, _ = evaluate_risk(
        db_session,
        settings_row,
        reduce_decision,
        snapshot,
        execution_mode="historical_replay",
    )

    assert breakout_result.allowed is False
    assert "DRAWDOWN_STATE_BREAKOUT_RESTRICTED" in breakout_result.reason_codes
    assert scale_in_result.allowed is False
    assert "DRAWDOWN_STATE_PYRAMIDING_REQUIRES_WINNER" in scale_in_result.reason_codes
    assert scale_in_result.debug_payload["drawdown_state"]["winner_only_pyramiding"] is True
    assert reduce_result.allowed is True
    assert "DRAWDOWN_STATE_BREAKOUT_RESTRICTED" not in reduce_result.reason_codes


def test_winner_only_add_on_allows_profitable_protected_scale_in_with_downsized_size(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_drawdown_pnl_snapshot(
        db_session,
        equity=100000.0,
        net_pnl=0.0,
        daily_pnl=0.0,
        consecutive_losses=0,
        minutes_ago=5,
    )
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.01,
            entry_price=70000.0,
            mark_price=70600.0,
            leverage=2.0,
            stop_loss=70100.0,
            take_profit=72000.0,
            realized_pnl=0.0,
            unrealized_pnl=6.0,
            metadata_json={
                "position_management": {
                    "initial_stop_loss": 69000.0,
                    "initial_risk_per_unit": 1000.0,
                    "current_r_multiple": 0.6,
                }
            },
        )
    )
    db_session.flush()

    decision = _entry_decision(
        entry_mode="pullback_confirm",
        entry_zone_min=snapshot.latest_price - 20.0,
        entry_zone_max=snapshot.latest_price + 20.0,
        stop_loss=snapshot.latest_price - 400.0,
        take_profit=snapshot.latest_price + 700.0,
        max_chase_bps=100.0,
        rationale_codes=["TEST", "TREND_UP", "ALIGNED_PULLBACK"],
    )
    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context=_add_on_context(current_r_multiple=0.6),
    )

    assert result.allowed is True
    assert result.debug_payload["add_on"]["same_side_pyramiding"] is True
    assert result.debug_payload["add_on"]["protective_stop_ready"] is True
    assert result.approved_projected_notional > 0.0
    assert result.approved_projected_notional < result.raw_projected_notional
    assert result.approved_projected_notional <= 706.0 + 1e-6
    assert result.approved_risk_pct < decision.risk_pct
    assert result.approved_leverage <= decision.leverage
    assert "ADD_ON_RISK_DOWNSIZED" in result.adjustment_reason_codes


def test_winner_only_add_on_blocks_losing_position_and_unprotected_stop(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_drawdown_pnl_snapshot(
        db_session,
        equity=100000.0,
        net_pnl=0.0,
        daily_pnl=0.0,
        consecutive_losses=0,
        minutes_ago=5,
    )
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.01,
            entry_price=70000.0,
            mark_price=69750.0,
            leverage=2.0,
            stop_loss=69000.0,
            take_profit=72000.0,
            realized_pnl=0.0,
            unrealized_pnl=-2.5,
            metadata_json={
                "position_management": {
                    "initial_stop_loss": 69000.0,
                    "initial_risk_per_unit": 1000.0,
                    "current_r_multiple": -0.25,
                }
            },
        )
    )
    db_session.flush()

    decision = _entry_decision(
        entry_mode="pullback_confirm",
        entry_zone_min=snapshot.latest_price - 20.0,
        entry_zone_max=snapshot.latest_price + 20.0,
        stop_loss=snapshot.latest_price - 400.0,
        take_profit=snapshot.latest_price + 700.0,
        max_chase_bps=100.0,
        rationale_codes=["TEST", "TREND_UP", "ALIGNED_PULLBACK"],
    )
    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context=_add_on_context(current_r_multiple=-0.25, protective_stop_ready=False),
    )

    assert result.allowed is False
    assert "ADD_ON_REQUIRES_WINNING_POSITION" in result.reason_codes
    assert "ADD_ON_PROTECTIVE_STOP_REQUIRED" in result.reason_codes
    assert result.debug_payload["add_on"]["same_side_pyramiding"] is True


def test_winner_only_add_on_block_does_not_apply_to_reduce_paths(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_drawdown_pnl_snapshot(
        db_session,
        equity=100000.0,
        net_pnl=0.0,
        daily_pnl=0.0,
        consecutive_losses=0,
        minutes_ago=5,
    )
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.01,
            entry_price=70000.0,
            mark_price=69750.0,
            leverage=2.0,
            stop_loss=69000.0,
            take_profit=72000.0,
            realized_pnl=0.0,
            unrealized_pnl=-2.5,
        )
    )
    db_session.flush()

    reduce_decision = _entry_decision(decision="reduce", entry_mode="none", max_chase_bps=None)
    reduce_result, _ = evaluate_risk(
        db_session,
        settings_row,
        reduce_decision,
        snapshot,
        execution_mode="historical_replay",
        decision_context=_add_on_context(current_r_multiple=-0.25, protective_stop_ready=False),
    )

    assert reduce_result.allowed is True
    assert "ADD_ON_REQUIRES_WINNING_POSITION" not in reduce_result.reason_codes
    assert "ADD_ON_PROTECTIVE_STOP_REQUIRED" not in reduce_result.reason_codes


def test_drawdown_soft_layer_does_not_override_daily_loss_hard_block(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    settings_row.max_daily_loss = 0.02
    _seed_drawdown_pnl_snapshot(
        db_session,
        equity=100000.0,
        net_pnl=0.0,
        daily_pnl=0.0,
        consecutive_losses=0,
        minutes_ago=40,
    )
    _seed_drawdown_pnl_snapshot(
        db_session,
        equity=94000.0,
        net_pnl=-6000.0,
        daily_pnl=-2500.0,
        consecutive_losses=2,
        minutes_ago=5,
    )
    db_session.flush()
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _entry_decision(
        entry_mode="pullback_confirm",
        entry_zone_min=snapshot.latest_price - 20.0,
        entry_zone_max=snapshot.latest_price + 20.0,
        stop_loss=snapshot.latest_price - 400.0,
        take_profit=snapshot.latest_price + 700.0,
        max_chase_bps=100.0,
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
    )

    assert result.allowed is False
    assert "DAILY_LOSS_LIMIT_REACHED" in result.reason_codes
    assert result.debug_payload["drawdown_state"]["current_drawdown_state"] == "drawdown_containment"


def test_ai_decision_ttl_expiry_blocks_new_entry_and_audits(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    _seed_account_equity(db_session)
    _mark_all_sync_scopes_fresh(settings_row)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _triggerable_decision(snapshot)
    decision_run = _add_ai_decision_run(
        db_session,
        decision,
        snapshot,
        generated_at=utcnow_naive() - timedelta(minutes=30),
        ttl_seconds=60,
    )

    result, risk_row = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        decision_run_id=decision_run.id,
        market_snapshot_id=2001,
    )

    assert result.allowed is False
    assert AI_DECISION_EXPIRED_REASON_CODE in result.reason_codes
    assert result.debug_payload["ai_decision_validity"]["status"] == "expired"
    event = db_session.query(AuditEvent).filter_by(event_type="ai_decision_expired").one()
    assert event.entity_id == str(decision_run.id)
    assert event.payload["risk_check_id"] == risk_row.id
    assert AI_DECISION_EXPIRED_REASON_CODE in event.payload["reason_codes"]
    db_session.refresh(decision_run)
    assert decision_run.metadata_json["ai_decision_validity"]["status"] == "expired"


def test_ai_decision_price_move_invalidates_new_entry_and_audits(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    _seed_account_equity(db_session)
    _mark_all_sync_scopes_fresh(settings_row)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _triggerable_decision(snapshot)
    decision_run = _add_ai_decision_run(
        db_session,
        decision,
        snapshot,
        reference_price=float(snapshot.latest_price) * 0.99,
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        decision_run_id=decision_run.id,
        market_snapshot_id=2002,
    )

    assert result.allowed is False
    assert AI_DECISION_PRICE_MOVE_INVALIDATED_REASON_CODE in result.reason_codes
    assert result.debug_payload["ai_decision_validity"]["status"] == "invalidated"
    event = db_session.query(AuditEvent).filter_by(event_type="ai_decision_invalidated").one()
    assert AI_DECISION_PRICE_MOVE_INVALIDATED_REASON_CODE in event.payload["reason_codes"]


def test_ai_decision_regime_change_invalidates_new_entry(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    _seed_account_equity(db_session)
    _mark_all_sync_scopes_fresh(settings_row)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _triggerable_decision(snapshot)
    decision_run = _add_ai_decision_run(
        db_session,
        decision,
        snapshot,
        regime_id="range:neutral",
        regime_label="range",
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        decision_run_id=decision_run.id,
        market_snapshot_id=2003,
        decision_context={
            "current_market_state": {
                "regime_id": "bullish:aligned",
                "regime_label": "bullish",
                "volatility_pct": 0.01,
                "range_breakout_direction": "none",
            }
        },
    )

    assert result.allowed is False
    assert AI_DECISION_REGIME_CHANGED_REASON_CODE in result.reason_codes
    assert result.debug_payload["ai_decision_validity"]["current_regime_id"] == "bullish:aligned"


def test_ai_decision_range_break_and_volatility_spike_invalidates_new_entry(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    _seed_account_equity(db_session)
    _mark_all_sync_scopes_fresh(settings_row)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _triggerable_decision(snapshot)
    decision_run = _add_ai_decision_run(
        db_session,
        decision,
        snapshot,
        volatility_pct=1.0,
        range_breakout_direction="none",
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        decision_run_id=decision_run.id,
        market_snapshot_id=2004,
        decision_context={
            "current_market_state": {
                "regime_id": "range:neutral",
                "regime_label": "range",
                "volatility_pct": 2.5,
                "range_breakout_direction": "up",
            }
        },
    )

    assert result.allowed is False
    assert AI_DECISION_RANGE_BREAK_INVALIDATED_REASON_CODE in result.reason_codes
    assert AI_DECISION_VOLATILITY_SPIKE_REASON_CODE in result.reason_codes


def test_expired_ai_decision_does_not_block_reduce_path(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    _seed_account_equity(db_session)
    _mark_all_sync_scopes_fresh(settings_row)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    reduce_decision = _triggerable_decision(snapshot, decision="reduce")
    decision_run = _add_ai_decision_run(
        db_session,
        reduce_decision,
        snapshot,
        generated_at=utcnow_naive() - timedelta(minutes=30),
        ttl_seconds=60,
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        reduce_decision,
        snapshot,
        decision_run_id=decision_run.id,
        market_snapshot_id=2005,
    )

    assert result.allowed is True
    assert AI_DECISION_EXPIRED_REASON_CODE not in result.reason_codes
    assert result.debug_payload["ai_decision_validity"]["status"] == "not_applicable"
    assert db_session.query(AuditEvent).filter_by(event_type="ai_decision_expired").count() == 0


def _safe_profile_defaults(
    *,
    mode: str = "conservative_only",
    confidence: float = 0.70,
    dwell: int = 900,
    confirmations: int = 2,
):
    from trading_mvp.services import risk as risk_service

    return risk_service.get_settings().model_copy(
        update={
            "ai_market_settings_auto_apply_mode": mode,
            "ai_market_settings_advisor_min_confidence_to_apply": confidence,
            "ai_market_settings_min_profile_dwell_seconds": dwell,
            "ai_market_settings_relax_requires_consecutive_confirmations": confirmations,
        }
    )


def _store_ai_market_settings_recommendation(
    settings_row,
    *,
    profile: str,
    confidence: float = 0.80,
    valid_for_seconds: int = 900,
    recommendation_id: str = "rec-test-1",
    symbol_scope: list[str] | None = None,
    do_not_relax: bool = False,
) -> dict[str, object]:
    now = utcnow_naive()
    payload: dict[str, object] = {
        "recommendation_id": recommendation_id,
        "generated_at": now.isoformat(),
        "valid_until": (now + timedelta(seconds=valid_for_seconds)).isoformat(),
        "symbol_scope": symbol_scope or ["BTCUSDT"],
        "recommended_profile_id": profile,
        "confidence": confidence,
        "reason_summary": "unit test recommendation",
        "reason_codes": ["UNIT_TEST"],
        "observed_risk_flags": [],
        "suggested_new_entry_policy": "STRICT_CONFIRMATION_ONLY",
        "do_not_relax": do_not_relax,
        "status": "valid",
    }
    detail = dict(settings_row.pause_reason_detail or {})
    detail["ai_market_settings_advisor"] = {
        "status": "valid",
        "shadow": True,
        "updated_at": now.isoformat(),
        "latest": payload,
    }
    settings_row.pause_reason_detail = detail
    return payload


def _store_safe_profile_state(settings_row, *, active_profile: str, selected_seconds_ago: int = 1200) -> None:
    detail = dict(settings_row.pause_reason_detail or {})
    detail["safe_profile_selector"] = {
        "active_profile": active_profile,
        "active_profile_selected_at": (utcnow_naive() - timedelta(seconds=selected_seconds_ago)).isoformat(),
    }
    settings_row.pause_reason_detail = detail


def test_safe_profile_selector_tightens_ai_profile_in_conservative_only(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _mark_all_sync_scopes_fresh(settings_row)
    _store_ai_market_settings_recommendation(settings_row, profile="CAUTION")
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)

    selection = select_safe_execution_risk_profile(
        settings_row,
        snapshot,
        defaults=_safe_profile_defaults(mode="conservative_only"),
        decision_context={"deterministic_market_condition_profile": "NORMAL"},
        live_requested=False,
    )

    assert selection["deterministic_profile"] == "NORMAL"
    assert selection["ai_recommended_profile"] == "CAUTION"
    assert selection["final_active_profile"] == "CAUTION"
    assert selection["was_tightened_by_ai"] is True


@pytest.mark.parametrize("deterministic_profile", ["HIGH_VOLATILITY", "STRESS"])
def test_safe_profile_selector_does_not_auto_relax_from_ai_normal(db_session, deterministic_profile: str) -> None:
    settings_row = get_or_create_settings(db_session)
    _mark_all_sync_scopes_fresh(settings_row)
    _store_ai_market_settings_recommendation(settings_row, profile="NORMAL")
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)

    selection = select_safe_execution_risk_profile(
        settings_row,
        snapshot,
        defaults=_safe_profile_defaults(mode="conservative_only"),
        decision_context={"deterministic_market_condition_profile": deterministic_profile},
        live_requested=False,
    )

    assert selection["final_active_profile"] == deterministic_profile
    assert selection["was_tightened_by_ai"] is False
    assert selection["was_relaxation_blocked"] is True


def test_safe_profile_selector_stale_data_forces_degraded_and_blocks_entry(monkeypatch, db_session) -> None:
    from trading_mvp.services import risk as risk_service

    defaults = _safe_profile_defaults(mode="conservative_only")
    monkeypatch.setattr(risk_service, "get_settings", lambda: defaults)
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    _seed_account_equity(db_session)
    _mark_all_sync_scopes_fresh(settings_row)
    _store_ai_market_settings_recommendation(settings_row, profile="NORMAL")
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140, force_stale=True)
    decision = _triggerable_decision(snapshot)

    result, risk_row = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
    )

    selector = result.debug_payload["safe_profile_selector"]
    assert result.allowed is False
    assert selector["final_active_profile"] == "DEGRADED"
    assert "MARKET_STATE_STALE" in selector["hard_condition_reason_codes"]
    assert EXECUTION_RISK_PROFILE_BLOCK_REASON_CODE in result.reason_codes
    event = db_session.query(AuditEvent).filter_by(event_type="execution_risk_profile_selected").one()
    assert event.entity_id == str(risk_row.id)
    assert event.payload["final_active_profile"] == "DEGRADED"


def test_safe_profile_selector_ignores_expired_ai_recommendation(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _mark_all_sync_scopes_fresh(settings_row)
    _store_ai_market_settings_recommendation(settings_row, profile="STRESS", valid_for_seconds=-60)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)

    selection = select_safe_execution_risk_profile(
        settings_row,
        snapshot,
        defaults=_safe_profile_defaults(mode="conservative_only"),
        decision_context={"deterministic_market_condition_profile": "NORMAL"},
        live_requested=False,
    )

    assert selection["final_active_profile"] == "NORMAL"
    assert "AI_MARKET_SETTINGS_RECOMMENDATION_EXPIRED" in selection["ignored_reason_codes"]


def test_safe_profile_selector_ignores_low_confidence_ai_recommendation(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _mark_all_sync_scopes_fresh(settings_row)
    _store_ai_market_settings_recommendation(settings_row, profile="STRESS", confidence=0.40)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)

    selection = select_safe_execution_risk_profile(
        settings_row,
        snapshot,
        defaults=_safe_profile_defaults(mode="conservative_only", confidence=0.70),
        decision_context={"deterministic_market_condition_profile": "NORMAL"},
        live_requested=False,
    )

    assert selection["final_active_profile"] == "NORMAL"
    assert "AI_MARKET_SETTINGS_RECOMMENDATION_LOW_CONFIDENCE" in selection["ignored_reason_codes"]


def test_safe_profile_selector_blocks_relaxation_until_consecutive_confirmations(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _mark_all_sync_scopes_fresh(settings_row)
    _store_safe_profile_state(settings_row, active_profile="STRESS", selected_seconds_ago=1200)
    _store_ai_market_settings_recommendation(settings_row, profile="NORMAL")
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)

    selection = select_safe_execution_risk_profile(
        settings_row,
        snapshot,
        defaults=_safe_profile_defaults(mode="conservative_only", dwell=0, confirmations=2),
        decision_context={"deterministic_market_condition_profile": "NORMAL"},
        live_requested=False,
    )

    assert selection["final_active_profile"] == "STRESS"
    assert selection["was_relaxation_blocked"] is True
    assert PROFILE_RELAXATION_CONSECUTIVE_REASON_CODE in selection["relaxation_block_reason_codes"]


def test_safe_profile_selector_does_not_block_reduce_only_path(monkeypatch, db_session) -> None:
    from trading_mvp.services import risk as risk_service

    defaults = _safe_profile_defaults(mode="conservative_only")
    monkeypatch.setattr(risk_service, "get_settings", lambda: defaults)
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    _seed_account_equity(db_session)
    _mark_all_sync_scopes_fresh(settings_row)
    _store_safe_profile_state(settings_row, active_profile="STRESS", selected_seconds_ago=0)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    reduce_decision = _triggerable_decision(snapshot, decision="reduce").model_copy(
        update={"intent_family": "management", "management_action": "reduce_only"}
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        reduce_decision,
        snapshot,
        execution_mode="historical_replay",
    )

    assert result.allowed is True
    assert result.debug_payload["safe_profile_selector"]["final_active_profile"] == "STRESS"
    assert EXECUTION_RISK_PROFILE_BLOCK_REASON_CODE not in result.reason_codes
