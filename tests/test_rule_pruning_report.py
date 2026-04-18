from __future__ import annotations

from datetime import timedelta

from trading_mvp.models import AgentRun, Execution, Order
from trading_mvp.services.orchestrator import TradingOrchestrator
from trading_mvp.services.rule_pruning import build_keep_kill_report
from trading_mvp.time_utils import utcnow_naive


def _seed_pruning_decision(
    db_session,
    *,
    symbol: str,
    timeframe: str = "15m",
    decision: str = "long",
    entry_mode: str = "pullback_confirm",
    rationale_codes: list[str],
    primary_regime: str = "bullish",
    trend_alignment: str = "bullish_aligned",
    created_at,
    net_pnl_after_fees: float | None = None,
    signed_slippage_bps: float = 0.0,
    policy_profile: str = "UNSPECIFIED",
    trigger_details: dict[str, object] | None = None,
    setup_cluster_state: dict[str, object] | None = None,
    selection_context: dict[str, object] | None = None,
    ai_skipped_reason: str | None = None,
    decision_agreement: dict[str, object] | None = None,
) -> AgentRun:
    decision_row = AgentRun(
        role="trading_decision",
        trigger_event="interval_decision_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="deterministic-mock",
        summary="rule pruning seed",
        input_payload={
            "features": {
                "regime": {
                    "primary_regime": primary_regime,
                    "trend_alignment": trend_alignment,
                }
            }
        },
        output_payload={
            "symbol": symbol,
            "timeframe": timeframe,
            "decision": decision,
            "entry_mode": entry_mode,
            "rationale_codes": rationale_codes,
            "confidence": 0.64,
            "risk_pct": 0.01,
            "leverage": 2.0,
        },
        metadata_json={
            "trigger_details": trigger_details or {},
            "setup_cluster_state": setup_cluster_state or {},
            "selection_context": selection_context or {},
            "ai_skipped_reason": ai_skipped_reason,
            "decision_agreement": decision_agreement or {},
        },
        schema_valid=True,
        started_at=created_at,
        completed_at=created_at,
        created_at=created_at,
        updated_at=created_at,
    )
    db_session.add(decision_row)
    db_session.flush()

    if net_pnl_after_fees is not None:
        order_row = Order(
            symbol=symbol,
            decision_run_id=decision_row.id,
            risk_check_id=None,
            position_id=None,
            side="buy" if decision == "long" else "sell",
            order_type="limit",
            mode="live",
            status="filled",
            requested_quantity=1.0,
            requested_price=100.0,
            filled_quantity=1.0,
            average_fill_price=100.0,
            reason_codes=[],
            metadata_json={
                "execution_quality": {
                    "policy_profile": policy_profile,
                }
            },
            created_at=created_at,
            updated_at=created_at,
        )
        db_session.add(order_row)
        db_session.flush()
        execution_row = Execution(
            order_id=order_row.id,
            position_id=None,
            symbol=symbol,
            status="filled",
            external_trade_id=f"{symbol}-{decision_row.id}",
            fill_price=100.0,
            fill_quantity=1.0,
            fee_paid=max(abs(net_pnl_after_fees) * 0.1, 0.1),
            commission_asset="USDT",
            slippage_pct=abs(signed_slippage_bps) / 10_000.0,
            realized_pnl=net_pnl_after_fees + max(abs(net_pnl_after_fees) * 0.1, 0.1),
            payload={
                "signed_slippage_bps": signed_slippage_bps,
            },
            created_at=created_at,
            updated_at=created_at,
        )
        db_session.add(execution_row)
        db_session.flush()

    return decision_row


def test_build_keep_kill_report_classifies_keep_kill_and_simplify(db_session) -> None:
    now = utcnow_naive()
    for index, pnl in enumerate([18.0, 16.0, 14.0, 12.0]):
        _seed_pruning_decision(
            db_session,
            symbol="BTCUSDT",
            rationale_codes=["PULLBACK_ENTRY_BIAS", "SETUP_TIME_PROFILE_PULLBACK_FLEXIBLE"],
            primary_regime="bullish",
            trend_alignment="bullish_aligned",
            created_at=now - timedelta(hours=index),
            net_pnl_after_fees=pnl,
            signed_slippage_bps=2.0 + index,
            policy_profile="entry_btc_fast_calm",
            selection_context={"selected_reason": "ranked_portfolio_focus"},
        )
    for index, pnl in enumerate([-10.0, -12.0, -9.0, -11.0], start=4):
        _seed_pruning_decision(
            db_session,
            symbol="ETHUSDT",
            rationale_codes=[
                "STRUCTURE_BREAKOUT_UP_EXCEPTION",
                "SPREAD_HEADWIND",
                "BREAKOUT_OI_SPREAD_FILTER",
            ],
            primary_regime="bullish",
            trend_alignment="bullish_aligned",
            entry_mode="breakout_confirm",
            created_at=now - timedelta(hours=index),
            net_pnl_after_fees=pnl,
            signed_slippage_bps=14.0 + (index - 4),
            policy_profile="entry_alt_fast_expanded",
            trigger_details={"late_chase": True, "quality_state": "trigger"},
            setup_cluster_state={"matched": True, "underperforming": True, "disable_reason_codes": ["CLUSTER_NEGATIVE_EXPECTANCY"]},
        )
    for index in range(2):
        _seed_pruning_decision(
            db_session,
            symbol="SOLUSDT",
            rationale_codes=["LEAD_MARKET_DIVERGENCE"],
            primary_regime="transition",
            trend_alignment="mixed",
            created_at=now - timedelta(hours=12 + index),
            net_pnl_after_fees=None,
        )

    report = build_keep_kill_report(db_session, lookback_days=21, limit=64)

    assert report.decisions_analyzed == 10
    btc_bucket = next(item for item in report.bucket_reports if item.symbol == "BTCUSDT")
    eth_bucket = next(item for item in report.bucket_reports if item.symbol == "ETHUSDT")
    assert btc_bucket.classification == "keep"
    assert btc_bucket.expectancy > 0
    assert btc_bucket.net_pnl_after_fees > 0
    assert eth_bucket.classification == "kill"
    assert eth_bucket.late_trigger_ratio == 1.0
    assert eth_bucket.failure_cluster_hit_rate == 1.0
    assert any(item.rule_key == "setup_time_profile" for item in report.keep_list)
    assert any(item.rule_key == "breakout_exception" for item in report.kill_list)
    assert any(item.rule_key == "lead_lag_filter" for item in report.simplify_list)
    assert report.next_cycle_candidates


def test_build_keep_kill_report_aggregates_hold_late_trigger_and_failure_cluster_rates(db_session) -> None:
    now = utcnow_naive()
    _seed_pruning_decision(
        db_session,
        symbol="ADAUSDT",
        rationale_codes=["PULLBACK_ENTRY_BIAS"],
        created_at=now,
        net_pnl_after_fees=8.0,
        signed_slippage_bps=3.0,
        policy_profile="entry_alt_balanced",
    )
    _seed_pruning_decision(
        db_session,
        symbol="ADAUSDT",
        rationale_codes=["PULLBACK_ENTRY_BIAS"],
        created_at=now - timedelta(minutes=15),
        net_pnl_after_fees=6.0,
        signed_slippage_bps=4.0,
        policy_profile="entry_alt_balanced",
        trigger_details={"late_chase": True},
        setup_cluster_state={"matched": True, "underperforming": True},
    )
    _seed_pruning_decision(
        db_session,
        symbol="ADAUSDT",
        decision="hold",
        entry_mode="none",
        rationale_codes=["PULLBACK_ENTRY_BIAS", "NO_EDGE"],
        created_at=now - timedelta(minutes=30),
        net_pnl_after_fees=None,
        selection_context={"execution_policy_profile": "entry_alt_balanced"},
    )
    _seed_pruning_decision(
        db_session,
        symbol="ADAUSDT",
        decision="hold",
        entry_mode="none",
        rationale_codes=["PULLBACK_ENTRY_BIAS", "NO_EDGE"],
        created_at=now - timedelta(minutes=45),
        net_pnl_after_fees=None,
        selection_context={"execution_policy_profile": "entry_alt_balanced"},
    )

    report = TradingOrchestrator(db_session).build_keep_kill_report(lookback_days=21, limit=32)
    ada_bucket = next(item for item in report.bucket_reports if item.symbol == "ADAUSDT")

    assert ada_bucket.decisions == 4
    assert ada_bucket.traded_decisions == 2
    assert ada_bucket.hold_rate == 0.5
    assert ada_bucket.late_trigger_ratio == 0.25
    assert ada_bucket.failure_cluster_hit_rate == 0.25
