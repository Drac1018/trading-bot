from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from trading_mvp.database import Base, get_db
from trading_mvp.main import app
from trading_mvp.models import (
    AccountLedgerEntry,
    AgentRun,
    AuditEvent,
    DecisionPerformanceFact,
    Execution,
    MarketSnapshot,
    Order,
    PendingEntryPlan,
    PnLSnapshot,
    Position,
    RiskCheck,
)
from trading_mvp.services import performance_reporting
from trading_mvp.services.performance_reporting import (
    build_opportunity_attribution_report,
    build_signal_performance_report,
)
from trading_mvp.time_utils import utcnow_naive


def _feature_input(
    *,
    primary_regime: str,
    trend_alignment: str,
    volatility_regime: str,
    weak_volume: bool,
    momentum_weakening: bool,
) -> dict[str, object]:
    return {
        "features": {
            "regime": {
                "primary_regime": primary_regime,
                "trend_alignment": trend_alignment,
                "volatility_regime": volatility_regime,
                "weak_volume": weak_volume,
                "momentum_weakening": momentum_weakening,
            }
        }
    }


def _decision_agreement(*, baseline: str, final: str, ai_used: bool) -> dict[str, object]:
    return {
        "ai_used": ai_used,
        "comparison_source": (
            "deterministic_baseline_vs_ai_final"
            if ai_used
            else "deterministic_baseline_vs_deterministic_final"
        ),
        "level": "full_agreement" if baseline == final else "direction_disagreement",
        "baseline_decision": baseline,
        "final_decision": final,
    }


def _seed_performance_rows(db_session) -> None:
    now = utcnow_naive()
    recent_long = AgentRun(
        role="trading_decision",
        trigger_event="realtime_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="openai",
        summary="recent btc long",
        input_payload=_feature_input(
            primary_regime="bullish",
            trend_alignment="bullish_aligned",
            volatility_regime="normal",
            weak_volume=False,
            momentum_weakening=False,
        ),
        output_payload={
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "decision": "long",
            "rationale_codes": ["TREND_UP", "BREAKOUT"],
            "entry_zone_min": 69950.0,
            "entry_zone_max": 70050.0,
            "stop_loss": 69400.0,
            "take_profit": 70800.0,
            "max_holding_minutes": 60,
        },
        metadata_json={
            "source": "llm",
            "ai_model": "gpt-4.1-mini",
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        },
        schema_valid=True,
    )
    recent_hold = AgentRun(
        role="trading_decision",
        trigger_event="realtime_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="deterministic-mock",
        summary="recent eth hold",
        input_payload=_feature_input(
            primary_regime="range",
            trend_alignment="range",
            volatility_regime="normal",
            weak_volume=True,
            momentum_weakening=True,
        ),
        output_payload={
            "symbol": "ETHUSDT",
            "timeframe": "1h",
            "decision": "hold",
            "rationale_codes": ["RANGE_CHOP"],
            "max_holding_minutes": 120,
            "should_abstain": True,
        },
        metadata_json={
            "source": "deterministic",
            "last_ai_skip_reason": "ENTRY_CANDIDATE_WEAK_VOLUME_PREAI",
            "pre_ai_skip_reason": "ENTRY_CANDIDATE_WEAK_VOLUME_PREAI",
        },
        schema_valid=True,
    )
    older_short = AgentRun(
        role="trading_decision",
        trigger_event="realtime_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="openai",
        summary="older sol short",
        input_payload=_feature_input(
            primary_regime="bearish",
            trend_alignment="bearish_aligned",
            volatility_regime="expanded",
            weak_volume=False,
            momentum_weakening=False,
        ),
        output_payload={
            "symbol": "SOLUSDT",
            "timeframe": "5m",
            "decision": "short",
            "rationale_codes": ["BREAKDOWN"],
            "entry_zone_min": 180.0,
            "entry_zone_max": 182.0,
            "stop_loss": 186.0,
            "take_profit": 170.0,
            "max_holding_minutes": 90,
        },
        metadata_json={
            "source": "llm",
            "ai_model": "gpt-4.1-mini",
            "usage": {"prompt_tokens": 80, "completion_tokens": 10, "total_tokens": 90},
        },
        schema_valid=True,
    )
    db_session.add_all([recent_long, recent_hold, older_short])
    db_session.flush()
    recent_long.created_at = now - timedelta(hours=2)
    recent_hold.created_at = now - timedelta(hours=1)
    older_short.created_at = now - timedelta(days=3)

    db_session.add_all(
        [
            RiskCheck(
                symbol="BTCUSDT",
                decision_run_id=recent_long.id,
                allowed=True,
                decision="long",
                reason_codes=[],
                approved_risk_pct=0.01,
                approved_leverage=2.0,
                payload={},
            ),
            RiskCheck(
                symbol="ETHUSDT",
                decision_run_id=recent_hold.id,
                allowed=False,
                decision="hold",
                reason_codes=["HOLD_DECISION"],
                approved_risk_pct=0.0,
                approved_leverage=0.0,
                payload={},
            ),
            RiskCheck(
                symbol="SOLUSDT",
                decision_run_id=older_short.id,
                allowed=True,
                decision="short",
                reason_codes=[],
                approved_risk_pct=0.01,
                approved_leverage=2.0,
                payload={},
            ),
        ]
    )
    db_session.flush()

    btc_position = Position(
        symbol="BTCUSDT",
        mode="live",
        side="long",
        status="closed",
        quantity=0.01,
        entry_price=70000.0,
        mark_price=70800.0,
        leverage=2.0,
        stop_loss=69400.0,
        take_profit=70800.0,
        realized_pnl=12.0,
        unrealized_pnl=0.0,
        metadata_json={"replay": {"mfe_pct": 0.018, "mae_pct": 0.006, "mfe_pnl": 12.6, "mae_pnl": 4.2}},
    )
    sol_position = Position(
        symbol="SOLUSDT",
        mode="live",
        side="short",
        status="closed",
        quantity=1.0,
        entry_price=181.0,
        mark_price=186.0,
        leverage=2.0,
        stop_loss=186.0,
        take_profit=170.0,
        realized_pnl=-5.0,
        unrealized_pnl=0.0,
        metadata_json={"replay": {"mfe_pct": 0.022, "mae_pct": 0.031, "mfe_pnl": 3.98, "mae_pnl": 5.61}},
    )
    db_session.add_all([btc_position, sol_position])
    db_session.flush()
    btc_position.opened_at = now - timedelta(minutes=90)
    btc_position.closed_at = now - timedelta(minutes=15)
    sol_position.opened_at = now - timedelta(days=3, minutes=130)
    sol_position.closed_at = now - timedelta(days=3, minutes=20)

    btc_entry = Order(
        symbol="BTCUSDT",
        decision_run_id=recent_long.id,
        position_id=btc_position.id,
        side="buy",
        order_type="market",
        mode="live",
        status="filled",
        external_order_id="btc-entry",
        requested_quantity=0.01,
        requested_price=70000.0,
        filled_quantity=0.01,
        average_fill_price=70005.0,
        reason_codes=[],
        metadata_json={},
    )
    btc_take_profit = Order(
        symbol="BTCUSDT",
        decision_run_id=recent_long.id,
        position_id=btc_position.id,
        side="sell",
        order_type="TAKE_PROFIT_MARKET",
        mode="live",
        status="filled",
        external_order_id="btc-tp",
        reduce_only=True,
        close_only=True,
        requested_quantity=0.01,
        requested_price=70800.0,
        filled_quantity=0.01,
        average_fill_price=70800.0,
        reason_codes=[],
        metadata_json={},
    )
    sol_entry = Order(
        symbol="SOLUSDT",
        decision_run_id=older_short.id,
        position_id=sol_position.id,
        side="sell",
        order_type="market",
        mode="live",
        status="filled",
        external_order_id="sol-entry",
        requested_quantity=1.0,
        requested_price=181.0,
        filled_quantity=1.0,
        average_fill_price=181.0,
        reason_codes=[],
        metadata_json={},
    )
    sol_stop = Order(
        symbol="SOLUSDT",
        decision_run_id=older_short.id,
        position_id=sol_position.id,
        side="buy",
        order_type="STOP_MARKET",
        mode="live",
        status="filled",
        external_order_id="sol-stop",
        reduce_only=True,
        close_only=True,
        requested_quantity=1.0,
        requested_price=186.0,
        filled_quantity=1.0,
        average_fill_price=186.0,
        reason_codes=[],
        metadata_json={},
    )
    db_session.add_all([btc_entry, btc_take_profit, sol_entry, sol_stop])
    db_session.flush()
    btc_entry.created_at = now - timedelta(minutes=89)
    btc_take_profit.created_at = now - timedelta(minutes=16)
    sol_entry.created_at = now - timedelta(days=3, minutes=129)
    sol_stop.created_at = now - timedelta(days=3, minutes=21)

    db_session.add_all(
        [
            Execution(
                order_id=btc_entry.id,
                position_id=btc_position.id,
                symbol="BTCUSDT",
                status="filled",
                external_trade_id="btc-entry-fill",
                fill_price=70005.0,
                fill_quantity=0.01,
                fee_paid=0.0,
                commission_asset="USDT",
                slippage_pct=0.001,
                realized_pnl=0.0,
                payload={},
            ),
            Execution(
                order_id=btc_take_profit.id,
                position_id=btc_position.id,
                symbol="BTCUSDT",
                status="filled",
                external_trade_id="btc-tp-fill",
                fill_price=70800.0,
                fill_quantity=0.01,
                fee_paid=1.0,
                commission_asset="USDT",
                slippage_pct=0.0015,
                realized_pnl=12.0,
                payload={},
            ),
            Execution(
                order_id=sol_entry.id,
                position_id=sol_position.id,
                symbol="SOLUSDT",
                status="filled",
                external_trade_id="sol-entry-fill",
                fill_price=181.0,
                fill_quantity=1.0,
                fee_paid=0.0,
                commission_asset="USDT",
                slippage_pct=0.001,
                realized_pnl=0.0,
                payload={},
            ),
            Execution(
                order_id=sol_stop.id,
                position_id=sol_position.id,
                symbol="SOLUSDT",
                status="filled",
                external_trade_id="sol-stop-fill",
                fill_price=186.0,
                fill_quantity=1.0,
                fee_paid=0.5,
                commission_asset="USDT",
                slippage_pct=0.003,
                realized_pnl=-5.0,
                payload={},
            ),
        ]
    )
    db_session.flush()
    btc_entry_fill = db_session.query(Execution).filter_by(external_trade_id="btc-entry-fill").one()
    btc_tp_fill = db_session.query(Execution).filter_by(external_trade_id="btc-tp-fill").one()
    sol_entry_fill = db_session.query(Execution).filter_by(external_trade_id="sol-entry-fill").one()
    sol_stop_fill = db_session.query(Execution).filter_by(external_trade_id="sol-stop-fill").one()
    btc_entry_fill.created_at = now - timedelta(minutes=88, seconds=30)
    btc_tp_fill.created_at = now - timedelta(minutes=15)
    sol_entry_fill.created_at = now - timedelta(days=3, minutes=126)
    sol_stop_fill.created_at = now - timedelta(days=3, minutes=20)
    db_session.flush()

    db_session.add_all(
        [
            PnLSnapshot(
                snapshot_date=(now - timedelta(days=2)).date(),
                equity=100100.0,
                cash_balance=100100.0,
                realized_pnl=100.0,
                unrealized_pnl=0.0,
                daily_pnl=10.0,
                cumulative_pnl=100.0,
                consecutive_losses=0,
                created_at=now - timedelta(days=2),
            ),
            PnLSnapshot(
                snapshot_date=now.date(),
                equity=100120.0,
                cash_balance=100120.0,
                realized_pnl=120.0,
                unrealized_pnl=0.0,
                daily_pnl=20.0,
                cumulative_pnl=120.0,
                consecutive_losses=0,
                created_at=now,
            ),
            AuditEvent(
                event_type="decision_ai_deduped",
                entity_type="symbol",
                entity_id="BTCUSDT",
                severity="info",
                message="Repeated trigger fingerprint was deduplicated before AI review.",
                payload={"symbol": "BTCUSDT", "dedupe_reason": "TRIGGER_FINGERPRINT_UNCHANGED"},
                created_at=now - timedelta(minutes=30),
            ),
        ]
    )
    db_session.flush()


def _add_readiness_decision(
    db_session,
    *,
    index: int,
    decision: str,
    realized_pnl: float = 0.0,
    fee_paid: float = 0.0,
) -> AgentRun:
    symbol = f"RDY{index}USDT"
    is_entry = decision in {"long", "short"}
    row = AgentRun(
        role="trading_decision",
        trigger_event="realtime_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="openai",
        summary=f"readiness {symbol} {decision}",
        input_payload=_feature_input(
            primary_regime="bullish",
            trend_alignment="bullish_aligned",
            volatility_regime="normal",
            weak_volume=False,
            momentum_weakening=False,
        ),
        output_payload={
            "symbol": symbol,
            "timeframe": "15m",
            "decision": decision,
            "rationale_codes": ["READINESS_SAMPLE"],
            "entry_zone_min": 100.0,
            "entry_zone_max": 101.0,
            "stop_loss": 98.0,
            "take_profit": 106.0,
            "max_holding_minutes": 90,
        },
        metadata_json={
            "source": "llm",
            "ai_model": "gpt-4.1-mini",
            "decision_agreement": _decision_agreement(
                baseline=decision,
                final=decision,
                ai_used=True,
            ),
        },
        schema_valid=True,
    )
    db_session.add(row)
    db_session.flush()

    db_session.add(
        RiskCheck(
            symbol=symbol,
            decision_run_id=row.id,
            allowed=is_entry,
            decision=decision,
            reason_codes=[] if is_entry else ["HOLD_DECISION"],
            approved_risk_pct=0.01 if is_entry else 0.0,
            approved_leverage=2.0 if is_entry else 0.0,
            payload={},
        )
    )
    db_session.flush()

    if is_entry:
        order = Order(
            symbol=symbol,
            decision_run_id=row.id,
            side="buy" if decision == "long" else "sell",
            order_type="market",
            mode="live",
            status="filled",
            external_order_id=f"{symbol.lower()}-entry",
            requested_quantity=1.0,
            requested_price=100.0,
            filled_quantity=1.0,
            average_fill_price=100.0,
            reason_codes=[],
            metadata_json={},
        )
        db_session.add(order)
        db_session.flush()
        db_session.add(
            Execution(
                order_id=order.id,
                symbol=symbol,
                status="filled",
                external_trade_id=f"{symbol.lower()}-fill",
                fill_price=101.0,
                fill_quantity=1.0,
                fee_paid=fee_paid,
                commission_asset="USDT",
                slippage_pct=0.001,
                realized_pnl=realized_pnl,
                payload={},
            )
        )
        db_session.flush()
    return row


def _seed_readiness_sample(
    db_session,
    *,
    entry_pnls: list[tuple[float, float]],
) -> None:
    for index, (realized_pnl, fee_paid) in enumerate(entry_pnls, start=1):
        _add_readiness_decision(
            db_session,
            index=index,
            decision="long",
            realized_pnl=realized_pnl,
            fee_paid=fee_paid,
        )
    for index in range(len(entry_pnls) + 1, 6):
        _add_readiness_decision(db_session, index=index, decision="hold")


def test_build_signal_performance_report_returns_regime_and_flag_breakdowns(db_session) -> None:
    _seed_performance_rows(db_session)

    report = build_signal_performance_report(db_session)

    assert report.window_hours == 24
    assert len(report.windows) == 3

    by_window = {item.window_label: item for item in report.windows}
    day = by_window["24h"]
    week = by_window["7d"]

    assert day.summary.decisions == 2
    assert day.summary.approvals == 1
    assert day.summary.orders == 2
    assert day.summary.fills == 2
    assert day.summary.holds == 1
    assert day.summary.longs == 1
    assert day.summary.shorts == 0
    assert day.summary.realized_pnl_total == 12.0
    assert day.summary.fee_total == 1.0
    assert day.summary.net_realized_pnl_total == 11.0
    assert day.summary.average_arrival_slippage_pct == pytest.approx(0.001, abs=1e-9)
    assert day.summary.average_realized_slippage_pct == pytest.approx(0.001, abs=1e-9)
    assert day.summary.average_first_fill_latency_seconds == pytest.approx(30.0, abs=1e-9)
    assert day.summary.cancel_attempts == 0
    assert day.summary.cancel_successes == 0
    assert day.summary.cancel_success_rate == 0.0
    assert day.summary.wins == 1
    assert day.summary.losses == 0
    assert day.summary.take_profit_closes == 1
    assert day.summary.stop_loss_closes == 0
    assert day.summary.snapshot_net_pnl_estimate == 20.0
    assert day.summary.average_mfe_pct == 0.018
    assert day.summary.average_mae_pct == 0.006
    assert day.summary.best_mfe_pct == 0.018
    assert day.summary.worst_mae_pct == 0.006
    assert day.entry_quality["entry_marketable"].trade_count == 1
    assert day.entry_quality["entry_marketable"].net_pnl == pytest.approx(11.0, abs=1e-9)
    assert day.entry_quality["entry_passive_limit"].trade_count == 0
    assert day.ai_telemetry.ai_calls_total == 3
    assert day.ai_telemetry.ai_calls_provider_invoked == 1
    assert day.ai_telemetry.ai_calls_skipped_preai == 1
    assert day.ai_telemetry.ai_calls_deduped == 1
    assert day.ai_telemetry.input_tokens == 100
    assert day.ai_telemetry.output_tokens == 20
    assert day.ai_telemetry.estimated_cost_usd == pytest.approx(0.000072, abs=1e-12)
    assert day.ai_telemetry.decision_counts["long"] == 1
    assert day.ai_telemetry.decision_counts["hold"] == 1
    assert day.ai_telemetry.should_abstain == 1
    assert day.ai_telemetry.preai_skip_reasons["ENTRY_CANDIDATE_WEAK_VOLUME_PREAI"] == 1
    assert day.ai_telemetry.downstream.agent_runs == 2
    assert day.ai_telemetry.downstream.risk_checks == 2
    assert day.ai_telemetry.downstream.risk_allowed == 1
    assert day.ai_telemetry.downstream.orders == 2
    assert day.ai_telemetry.downstream.fills == 2
    assert day.ai_telemetry.downstream.realized_pnl == 12.0
    assert day.ai_telemetry.downstream.fee == 1.0
    assert day.ai_telemetry.downstream.net_realized_pnl == 11.0
    assert day.ai_telemetry.downstream.average_slippage_pct == pytest.approx(0.00125, abs=1e-9)
    assert day.ai_telemetry.downstream_by_decision["long"].agent_runs == 1
    assert day.ai_telemetry.downstream_by_decision["long"].risk_allowed == 1
    assert day.ai_telemetry.downstream_by_decision["long"].fills == 2
    assert day.ai_telemetry.downstream_by_decision["long"].net_realized_pnl == 11.0
    assert day.ai_telemetry.downstream_by_decision["hold"].agent_runs == 1
    assert day.ai_telemetry.downstream_by_decision["hold"].risk_blocked == 1
    assert day.ai_telemetry.downstream_by_decision["hold"].fills == 0
    assert day.limited_live_readiness.read_only is True
    assert day.limited_live_readiness.status == "not_ready"
    assert "insufficient_sample" in day.limited_live_readiness.reason_codes
    assert day.limited_live_readiness.recent_candidate_events == 2
    assert day.limited_live_readiness.actual_entries == 1
    assert day.limited_live_readiness.ai_calls_provider_invoked == 1

    assert day.decisions[0].symbol == "ETHUSDT"
    assert day.decisions[0].decision == "hold"
    assert day.decisions[0].regime == "range"
    assert day.decisions[0].weak_volume is True
    assert day.decisions[1].symbol == "BTCUSDT"
    assert day.decisions[1].close_outcome == "take_profit"
    assert day.decisions[1].planned_risk_reward_ratio == 1.3333333333333333
    assert day.decisions[1].arrival_slippage_pct == pytest.approx(0.001, abs=1e-9)
    assert day.decisions[1].realized_slippage_pct == pytest.approx(0.001, abs=1e-9)
    assert day.decisions[1].first_fill_latency_seconds == pytest.approx(30.0, abs=1e-9)
    assert day.decisions[1].mfe_pct == 0.018
    assert day.decisions[1].mae_pct == 0.006

    assert {item.key for item in day.regimes} == {"bullish", "range"}
    assert {item.key for item in day.trend_alignments} == {"bullish_aligned", "range"}
    assert {item.key for item in day.directions} == {"hold", "long"}
    assert day.hold_conditions[0].holds == 1
    assert "range | trend=range" in day.hold_conditions[0].key
    assert day.feature_flags[0].flag_name in {"weak_volume", "volatility_expanded", "momentum_weakening"}
    weak_volume = next(item for item in day.feature_flags if item.flag_name == "weak_volume")
    assert weak_volume.enabled.decisions == 1
    assert weak_volume.disabled.decisions == 1
    assert weak_volume.disabled.net_realized_pnl_total == 11.0
    assert {item.key for item in day.close_outcomes} == {"not_closed", "take_profit"}

    assert week.summary.decisions == 3
    assert week.summary.shorts == 1
    assert week.summary.stop_loss_closes == 1
    assert week.summary.average_arrival_slippage_pct == pytest.approx(0.001, abs=1e-9)
    assert week.summary.average_realized_slippage_pct == pytest.approx(0.001, abs=1e-9)
    assert week.summary.average_first_fill_latency_seconds == pytest.approx(105.0, abs=1e-9)
    assert week.summary.average_mfe_pct == pytest.approx(0.02, abs=1e-9)
    assert week.summary.average_mae_pct == pytest.approx(0.0185, abs=1e-9)
    assert week.summary.best_mfe_pct == pytest.approx(0.022, abs=1e-9)
    assert week.summary.worst_mae_pct == pytest.approx(0.031, abs=1e-9)
    assert week.ai_telemetry.ai_calls_provider_invoked == 2
    assert week.ai_telemetry.ai_calls_skipped_preai == 1
    assert week.ai_telemetry.ai_calls_deduped == 1
    assert week.ai_telemetry.estimated_cost_usd == pytest.approx(0.00012, abs=1e-12)
    assert {item.key for item in week.regimes} == {"bullish", "bearish", "range"}
    assert {item.key for item in week.symbols} == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
    assert {item.key for item in week.timeframes} == {"15m", "1h", "5m"}
    assert {item.key for item in week.directions} == {"long", "hold", "short"}

    assert report.items
    assert report.items[0].fee_total >= 0.0
    assert report.items[0].net_realized_pnl_total >= report.items[0].realized_pnl_total - report.items[0].fee_total


def test_entry_quality_breakdown_separates_passive_limit_and_marketable_entries(db_session) -> None:
    _seed_performance_rows(db_session)
    btc_entry = db_session.query(Order).filter_by(external_order_id="btc-entry").one()
    btc_entry.order_type = "limit"
    btc_entry.metadata_json = {
        "execution_policy": {
            "policy_name": "entry_passive_limit",
            "marketable": False,
        }
    }
    sol_entry = db_session.query(Order).filter_by(external_order_id="sol-entry").one()
    sol_entry.metadata_json = {
        "execution_policy": {
            "policy_name": "entry_marketable",
            "marketable": True,
        }
    }
    sol_entry.average_fill_price = 180.0
    sol_entry_fill = db_session.query(Execution).filter_by(external_trade_id="sol-entry-fill").one()
    sol_entry_fill.fill_price = 180.0
    db_session.flush()

    report = build_signal_performance_report(db_session, window_specs=(("7d", 24 * 7),), limit=20)

    entry_quality = report.windows[0].entry_quality
    passive = entry_quality["entry_passive_limit"]
    marketable = entry_quality["entry_marketable"]
    assert passive.trade_count == 1
    assert passive.net_pnl == pytest.approx(11.0, abs=1e-9)
    assert passive.expectancy == pytest.approx(11.0, abs=1e-9)
    assert passive.avg_signed_slippage_bps > 0.0
    assert marketable.trade_count == 1
    assert marketable.net_pnl == pytest.approx(-5.5, abs=1e-9)
    assert marketable.win_rate == 0.0
    assert marketable.avg_adverse_slippage_bps > 0.0
    decisions_by_symbol = {item.symbol: item for item in report.windows[0].decisions}
    assert decisions_by_symbol["BTCUSDT"].entry_execution_type == "entry_passive_limit"
    assert decisions_by_symbol["SOLUSDT"].entry_execution_type == "entry_marketable"


def test_build_signal_performance_report_attributes_funding_to_position_interval(db_session) -> None:
    _seed_performance_rows(db_session)
    btc_position = db_session.scalar(select(Position).where(Position.symbol == "BTCUSDT"))
    assert btc_position is not None
    db_session.add_all(
        [
            AccountLedgerEntry(
                entry_type="funding",
                asset="USDT",
                symbol="BTCUSDT",
                amount=-2.5,
                external_ref_id="funding-btc-match",
                occurred_at=btc_position.opened_at + timedelta(minutes=20),
                payload={"incomeType": "FUNDING_FEE"},
            ),
            AccountLedgerEntry(
                entry_type="funding",
                asset="USDT",
                symbol="BTCUSDT",
                amount=-9.0,
                external_ref_id="funding-btc-outside",
                occurred_at=btc_position.closed_at + timedelta(minutes=5),
                payload={"incomeType": "FUNDING_FEE"},
            ),
            AccountLedgerEntry(
                entry_type="funding",
                asset="USDT",
                symbol="ETHUSDT",
                amount=-7.0,
                external_ref_id="funding-other-symbol",
                occurred_at=btc_position.opened_at + timedelta(minutes=20),
                payload={"incomeType": "FUNDING_FEE"},
            ),
        ]
    )
    db_session.flush()

    report = build_signal_performance_report(db_session, window_specs=(("24h", 24),), limit=20)
    day = report.windows[0]
    btc_decision = next(item for item in day.decisions if item.symbol == "BTCUSDT")
    btc_bucket = next(item for item in day.symbols if item.key == "BTCUSDT")

    assert btc_decision.realized_pnl_total == 12.0
    assert btc_decision.fee_total == 1.0
    assert btc_decision.funding_total == pytest.approx(-2.5, abs=1e-9)
    assert btc_decision.net_pnl_excluding_funding == pytest.approx(11.0, abs=1e-9)
    assert btc_decision.net_pnl_including_funding == pytest.approx(8.5, abs=1e-9)
    assert btc_decision.funding_attribution_status == "matched"
    assert day.summary.funding_total == pytest.approx(-2.5, abs=1e-9)
    assert day.summary.net_pnl_excluding_funding == pytest.approx(11.0, abs=1e-9)
    assert day.summary.net_pnl_including_funding == pytest.approx(8.5, abs=1e-9)
    assert btc_bucket.funding_total == pytest.approx(-2.5, abs=1e-9)
    assert btc_bucket.net_pnl_including_funding == pytest.approx(8.5, abs=1e-9)


def test_build_signal_performance_report_handles_missing_funding_records(db_session) -> None:
    _seed_performance_rows(db_session)

    report = build_signal_performance_report(db_session, window_specs=(("24h", 24),), limit=20)
    btc_decision = next(item for item in report.windows[0].decisions if item.symbol == "BTCUSDT")

    assert btc_decision.funding_total == 0.0
    assert btc_decision.net_pnl_excluding_funding == pytest.approx(11.0, abs=1e-9)
    assert btc_decision.net_pnl_including_funding == pytest.approx(11.0, abs=1e-9)
    assert btc_decision.funding_attribution_status == "no_funding_records"


def test_build_signal_performance_report_uses_cached_decision_facts(db_session, monkeypatch) -> None:
    _seed_performance_rows(db_session)
    for row in db_session.query(AgentRun).filter_by(role="trading_decision"):
        performance_reporting.persist_decision_performance_fact(db_session, row)
    db_session.flush()

    def fail_on_agent_run_fallback(row):
        raise AssertionError(f"unexpected AgentRun JSON fallback for {row.id}")

    monkeypatch.setattr(performance_reporting, "_context_from_agent_run", fail_on_agent_run_fallback)

    report = build_signal_performance_report(db_session)

    day_symbols = {item.symbol for item in report.windows[0].decisions}
    assert {"BTCUSDT", "ETHUSDT"} <= day_symbols


def test_build_signal_performance_report_reuses_cached_subreport(db_session, monkeypatch) -> None:
    _seed_performance_rows(db_session)

    first = build_signal_performance_report(db_session)

    def fail_on_rebuild(*_args, **_kwargs):
        raise AssertionError("unexpected performance subreport rebuild")

    def fail_on_source_key(*_args, **_kwargs):
        raise AssertionError("unexpected performance source-key probe on fresh cache hit")

    monkeypatch.setattr(performance_reporting, "_load_pnl_snapshot_cache", fail_on_rebuild)
    monkeypatch.setattr(performance_reporting, "_signal_performance_source_key", fail_on_source_key)

    second = build_signal_performance_report(db_session)

    assert second.generated_at == first.generated_at
    assert second.windows[0].summary.snapshot_net_pnl_estimate == 20.0
    assert second.cache_status == "fresh"


def test_build_signal_performance_report_returns_stale_cache_while_revalidating(db_session, monkeypatch) -> None:
    _seed_performance_rows(db_session)

    first = build_signal_performance_report(db_session)
    refresh_calls: list[dict[str, object]] = []

    def start_refresh(*_args, **kwargs):
        refresh_calls.append(kwargs)
        return True

    def fail_on_rebuild(*_args, **_kwargs):
        raise AssertionError("stale cache response should not rebuild inline")

    monkeypatch.setattr(performance_reporting, "SIGNAL_PERFORMANCE_REPORT_CACHE_TTL_SECONDS", -1.0)
    monkeypatch.setattr(performance_reporting, "_start_signal_performance_background_refresh", start_refresh)
    monkeypatch.setattr(performance_reporting, "_load_pnl_snapshot_cache", fail_on_rebuild)

    second = build_signal_performance_report(db_session)

    assert second.generated_at == first.generated_at
    assert second.cache_status == "stale_revalidating"
    assert second.cache_rebuild_pending is True
    assert second.cache_age_seconds >= 0.0
    assert len(refresh_calls) == 1


def test_signal_performance_source_key_buckets_pnl_snapshot_timestamp(db_session) -> None:
    _seed_performance_rows(db_session)
    bucket_anchor = utcnow_naive().replace(second=10, microsecond=0)
    for row in db_session.query(PnLSnapshot):
        row.created_at = bucket_anchor
    db_session.flush()

    first_key = performance_reporting._signal_performance_source_key(db_session)

    latest = db_session.scalar(select(PnLSnapshot).order_by(PnLSnapshot.created_at.desc()).limit(1))
    assert latest is not None
    latest.created_at = bucket_anchor + timedelta(seconds=20)
    db_session.flush()
    same_bucket_key = performance_reporting._signal_performance_source_key(db_session)

    latest.created_at = bucket_anchor + timedelta(seconds=60)
    db_session.flush()
    next_bucket_key = performance_reporting._signal_performance_source_key(db_session)

    assert same_bucket_key == first_key
    assert next_bucket_key != first_key


def test_build_signal_performance_report_loads_pnl_snapshots_once(db_session, monkeypatch) -> None:
    _seed_performance_rows(db_session)
    cache_loads: list[object] = []
    original_loader = performance_reporting._load_pnl_snapshot_cache

    def load_spy(*args, **kwargs):
        cache_loads.append(args[1] if len(args) > 1 else kwargs.get("max_since"))
        return original_loader(*args, **kwargs)

    monkeypatch.setattr(performance_reporting, "_load_pnl_snapshot_cache", load_spy)

    report = build_signal_performance_report(
        db_session,
        window_specs=(("24h", 24), ("7d", 24 * 7), ("30d", 24 * 30)),
    )

    assert len(cache_loads) == 1
    assert report.windows[0].summary.snapshot_net_pnl_estimate == 20.0


def test_limited_live_readiness_marks_positive_sample_as_candidate(db_session) -> None:
    _seed_readiness_sample(db_session, entry_pnls=[(5.0, 0.5), (6.0, 0.5)])

    report = build_signal_performance_report(db_session, window_specs=(("24h", 24),), limit=20)
    readiness = report.windows[0].limited_live_readiness

    assert readiness.status == "limited_live_candidate"
    assert readiness.reason_codes == []
    assert readiness.recent_candidate_events == 5
    assert readiness.actual_entries == 2
    assert readiness.fills == 2
    assert readiness.expectancy_after_fees == pytest.approx(5.0, abs=1e-9)
    assert readiness.net_pnl_after_fees == pytest.approx(10.0, abs=1e-9)


def test_limited_live_readiness_blocks_negative_expectancy(db_session) -> None:
    _seed_readiness_sample(db_session, entry_pnls=[(-3.0, 0.5), (-2.0, 0.5)])

    report = build_signal_performance_report(db_session, window_specs=(("24h", 24),), limit=20)
    readiness = report.windows[0].limited_live_readiness

    assert readiness.status == "blocked"
    assert "negative_expectancy" in readiness.reason_codes
    assert readiness.expectancy_after_fees == pytest.approx(-3.0, abs=1e-9)


def test_limited_live_readiness_blocks_protection_failures(db_session) -> None:
    _seed_readiness_sample(db_session, entry_pnls=[(5.0, 0.5), (6.0, 0.5)])
    db_session.add(
        AuditEvent(
            event_type="unprotected_position_detected",
            entity_type="position",
            entity_id="RDY1USDT",
            severity="warning",
            message="Protective order verification failed.",
            payload={"reason_codes": ["PROTECTION_REQUIRED"]},
        )
    )
    db_session.flush()

    report = build_signal_performance_report(db_session, window_specs=(("24h", 24),), limit=20)
    readiness = report.windows[0].limited_live_readiness

    assert readiness.status == "blocked"
    assert "protection_failures" in readiness.reason_codes
    assert readiness.protection_failure_count >= 1


def test_limited_live_readiness_reuses_safety_event_cache_across_windows(db_session, monkeypatch) -> None:
    now = utcnow_naive()
    _seed_readiness_sample(db_session, entry_pnls=[(5.0, 0.5), (6.0, 0.5)])
    db_session.add_all(
        [
            AuditEvent(
                event_type="unprotected_position_detected",
                entity_type="position",
                entity_id="RDY1USDT",
                severity="warning",
                message="Protective order verification failed.",
                payload={"reason_codes": ["PROTECTION_REQUIRED"]},
                created_at=now - timedelta(minutes=15),
            ),
            AuditEvent(
                event_type="live_order_submission_unknown",
                entity_type="order",
                entity_id="RDY2USDT",
                severity="warning",
                message="Live order submission outcome is unknown.",
                payload={},
                created_at=now - timedelta(days=2),
            ),
            AuditEvent(
                event_type="risk_blocked",
                entity_type="risk_check",
                entity_id="RDY3USDT",
                severity="warning",
                message="Risk blocked stale market data.",
                payload={"reason_codes": ["STALE_MARKET_DATA"]},
                created_at=now - timedelta(days=10),
            ),
        ]
    )
    db_session.flush()

    cache_loads: list[object] = []
    original_loader = performance_reporting._load_limited_live_safety_event_cache

    def load_spy(*args, **kwargs):
        cache_loads.append((args, kwargs))
        return original_loader(*args, **kwargs)

    monkeypatch.setattr(performance_reporting, "_load_limited_live_safety_event_cache", load_spy)

    report = performance_reporting.build_signal_performance_report(
        db_session,
        window_specs=(("24h", 24), ("7d", 24 * 7), ("30d", 24 * 30)),
        limit=20,
    )

    assert len(cache_loads) == 1
    windows = {window.window_label: window for window in report.windows}
    assert windows["24h"].limited_live_readiness.protection_failure_count == 1
    assert windows["24h"].limited_live_readiness.unknown_submission_count == 0
    assert windows["7d"].limited_live_readiness.unknown_submission_count == 1
    assert windows["30d"].limited_live_readiness.stale_incomplete_data_block_count == 1


def test_ai_telemetry_normalizes_preai_skip_reasons(db_session) -> None:
    now = utcnow_naive()
    rows: list[AgentRun] = []
    for index, reason in enumerate(
        [
            "macro_event_imminent",
            "macro_release_reaction_window",
            "stale_market_data",
            "low_score_gate",
        ],
        start=1,
    ):
        rows.append(
            AgentRun(
                role="trading_decision",
                trigger_event="realtime_cycle",
                schema_name="TradeDecision",
                status="completed",
                provider_name="deterministic-mock",
                summary=f"pre-ai skip {reason}",
                input_payload=_feature_input(
                    primary_regime="range",
                    trend_alignment="range",
                    volatility_regime="normal",
                    weak_volume=True,
                    momentum_weakening=True,
                ),
                output_payload={
                    "symbol": f"SKIP{index}USDT",
                    "timeframe": "15m",
                    "decision": "hold",
                    "rationale_codes": ["PRE_AI_SKIP_TEST"],
                    "max_holding_minutes": 60,
                },
                metadata_json={
                    "source": "deterministic",
                    "pre_ai_skip_reason": reason,
                },
                schema_valid=True,
            )
        )
    db_session.add_all(rows)
    db_session.flush()
    for offset, row in enumerate(rows, start=1):
        row.created_at = now - timedelta(minutes=offset)
    db_session.flush()

    report = build_signal_performance_report(db_session, window_specs=(("24h", 24),), limit=20)
    telemetry = report.windows[0].ai_telemetry

    assert telemetry.ai_calls_total == 4
    assert telemetry.ai_calls_provider_invoked == 0
    assert telemetry.ai_calls_skipped_preai == 4
    assert telemetry.estimated_cost_usd == 0.0
    assert telemetry.preai_skip_reasons["MACRO_EVENT_IMMINENT"] == 1
    assert telemetry.preai_skip_reasons["MACRO_EVENT_RISK_WINDOW_ACTIVE"] == 1
    assert telemetry.preai_skip_reasons["STALE_MARKET_DATA"] == 1
    assert telemetry.preai_skip_reasons["LOW_SCORE"] == 1


def test_ai_baseline_comparison_separates_observed_and_unobserved_results(db_session) -> None:
    now = utcnow_naive()

    def make_decision(
        *,
        symbol: str,
        decision: str,
        baseline: str,
        ai_used: bool,
        source: str,
        fail_closed: bool = False,
    ) -> AgentRun:
        return AgentRun(
            role="trading_decision",
            trigger_event="realtime_cycle",
            schema_name="TradeDecision",
            status="completed",
            provider_name="openai" if source in {"llm", "llm_fallback"} else "deterministic-mock",
            summary=f"{symbol} {decision}",
            input_payload=_feature_input(
                primary_regime="bullish",
                trend_alignment="bullish_aligned",
                volatility_regime="normal",
                weak_volume=False,
                momentum_weakening=False,
            ),
            output_payload={
                "symbol": symbol,
                "timeframe": "15m",
                "decision": decision,
                "rationale_codes": ["TEST_BASELINE_AI"],
                "entry_zone_min": 100.0,
                "entry_zone_max": 101.0,
                "stop_loss": 98.0,
                "take_profit": 106.0,
                "max_holding_minutes": 90,
                "fail_closed_applied": fail_closed,
            },
            metadata_json={
                "source": source,
                "decision_agreement": _decision_agreement(
                    baseline=baseline,
                    final=decision,
                    ai_used=ai_used,
                ),
                "fail_closed_applied": fail_closed,
            },
            schema_valid=True,
        )

    ai_profit = make_decision(symbol="BTCUSDT", decision="long", baseline="long", ai_used=True, source="llm")
    ai_rejected = make_decision(symbol="ETHUSDT", decision="hold", baseline="long", ai_used=True, source="llm")
    ai_blocked = make_decision(symbol="SOLUSDT", decision="long", baseline="hold", ai_used=True, source="llm")
    ai_reduce = make_decision(symbol="BNBUSDT", decision="reduce", baseline="hold", ai_used=True, source="llm")
    fail_closed = make_decision(
        symbol="XRPUSDT",
        decision="hold",
        baseline="long",
        ai_used=False,
        source="llm_fallback",
        fail_closed=True,
    )
    baseline_entry = make_decision(
        symbol="ADAUSDT",
        decision="long",
        baseline="long",
        ai_used=False,
        source="deterministic",
    )
    db_session.add_all([ai_profit, ai_rejected, ai_blocked, ai_reduce, fail_closed, baseline_entry])
    db_session.flush()
    for offset, row in enumerate([ai_profit, ai_rejected, ai_blocked, ai_reduce, fail_closed, baseline_entry], start=1):
        row.created_at = now - timedelta(minutes=offset * 5)

    db_session.add_all(
        [
            RiskCheck(
                symbol="BTCUSDT",
                decision_run_id=ai_profit.id,
                allowed=True,
                decision="long",
                reason_codes=[],
                approved_risk_pct=0.01,
                approved_leverage=2.0,
                payload={},
            ),
            RiskCheck(
                symbol="ETHUSDT",
                decision_run_id=ai_rejected.id,
                allowed=False,
                decision="hold",
                reason_codes=["HOLD_DECISION"],
                approved_risk_pct=0.0,
                approved_leverage=0.0,
                payload={},
            ),
            RiskCheck(
                symbol="SOLUSDT",
                decision_run_id=ai_blocked.id,
                allowed=False,
                decision="long",
                reason_codes=["MAX_EXPOSURE"],
                approved_risk_pct=0.0,
                approved_leverage=0.0,
                payload={},
            ),
            RiskCheck(
                symbol="BNBUSDT",
                decision_run_id=ai_reduce.id,
                allowed=True,
                decision="reduce",
                reason_codes=[],
                approved_risk_pct=0.0,
                approved_leverage=1.0,
                payload={},
            ),
            RiskCheck(
                symbol="XRPUSDT",
                decision_run_id=fail_closed.id,
                allowed=False,
                decision="hold",
                reason_codes=["AI_UNAVAILABLE_FAIL_CLOSED"],
                approved_risk_pct=0.0,
                approved_leverage=0.0,
                payload={},
            ),
            RiskCheck(
                symbol="ADAUSDT",
                decision_run_id=baseline_entry.id,
                allowed=True,
                decision="long",
                reason_codes=[],
                approved_risk_pct=0.01,
                approved_leverage=2.0,
                payload={},
            ),
        ]
    )
    db_session.flush()

    position = Position(
        symbol="BTCUSDT",
        mode="live",
        side="long",
        status="closed",
        quantity=0.1,
        entry_price=100.0,
        mark_price=120.0,
        leverage=2.0,
        stop_loss=98.0,
        take_profit=120.0,
        realized_pnl=20.0,
        unrealized_pnl=0.0,
        metadata_json={},
    )
    db_session.add(position)
    db_session.flush()
    position.opened_at = now - timedelta(minutes=60)
    position.closed_at = now - timedelta(minutes=15)
    order = Order(
        symbol="BTCUSDT",
        decision_run_id=ai_profit.id,
        position_id=position.id,
        side="buy",
        order_type="market",
        mode="live",
        status="filled",
        external_order_id="btc-ai-profit",
        requested_quantity=0.1,
        requested_price=100.0,
        filled_quantity=0.1,
        average_fill_price=100.2,
        reason_codes=[],
        metadata_json={},
    )
    db_session.add(order)
    db_session.flush()
    order.created_at = now - timedelta(minutes=55)
    execution = Execution(
        order_id=order.id,
        position_id=position.id,
        symbol="BTCUSDT",
        status="filled",
        external_trade_id="btc-ai-profit-fill",
        fill_price=120.0,
        fill_quantity=0.1,
        fee_paid=2.0,
        commission_asset="USDT",
        slippage_pct=0.002,
        realized_pnl=20.0,
        payload={},
    )
    db_session.add(execution)
    db_session.flush()
    execution.created_at = now - timedelta(minutes=54)
    db_session.flush()

    report = build_signal_performance_report(db_session, window_specs=(("24h", 24),), limit=20)
    comparison = report.windows[0].ai_baseline_comparison
    buckets = {item.bucket: item for item in comparison.buckets}

    assert set(buckets) == {
        "baseline_only_entry",
        "ai_approved_entry",
        "ai_rejected_baseline_entry",
        "ai_management_action",
        "ai_hold_no_trade",
        "provider_failed_fail_closed",
    }
    approved = buckets["ai_approved_entry"]
    assert approved.decisions == 2
    assert approved.approvals == 1
    assert approved.fills == 1
    assert approved.wins == 1
    assert approved.net_pnl_after_fees == 18.0
    assert approved.expectancy == 18.0
    assert approved.avg_slippage == pytest.approx(0.002, abs=1e-9)
    assert approved.avg_holding_minutes == pytest.approx(45.0, abs=1e-9)
    assert approved.pnl_per_exposure_hour == pytest.approx(24.0, abs=1e-9)
    assert approved.unobserved_reason_counts["risk_blocked"] == 1

    rejected = buckets["ai_rejected_baseline_entry"]
    assert rejected.decisions == 1
    assert rejected.baseline_entries == 1
    assert rejected.ai_holds == 1
    assert rejected.fills == 0
    assert rejected.net_pnl_after_fees == 0.0
    assert rejected.observed_decisions == 0
    assert rejected.unobserved_decisions == 1
    assert rejected.unobserved_reason_counts["ai_filtered_no_order"] == 1

    assert buckets["ai_management_action"].decisions == 1
    assert buckets["provider_failed_fail_closed"].decisions == 1
    assert buckets["baseline_only_entry"].decisions == 1
    assert comparison.decisions == 6
    assert comparison.observed_decisions == 1
    assert comparison.unobserved_decisions == 5
    assert comparison.observed_net_pnl_after_fees == 18.0
    assert comparison.observed_rejected_baseline_entry_net_pnl_after_fees == 0.0
    assert comparison.ai_filter_observed_value_net_pnl_after_fees is None


def test_decision_performance_fact_refreshes_ai_usefulness_after_risk_and_fill(db_session) -> None:
    run = AgentRun(
        role="trading_decision",
        trigger_event="realtime_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="openai",
        summary="ai usefulness",
        input_payload=_feature_input(
            primary_regime="bullish",
            trend_alignment="bullish_aligned",
            volatility_regime="normal",
            weak_volume=False,
            momentum_weakening=False,
        ),
        output_payload={
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "decision": "long",
            "rationale_codes": ["TREND_UP"],
            "entry_zone_min": 69950.0,
            "entry_zone_max": 70050.0,
            "stop_loss": 69400.0,
            "take_profit": 70800.0,
            "max_holding_minutes": 60,
            "psychology_scene_review": {
                "market_psychology": "Pullback buyers are waiting for confirmation.",
                "psychology_bias": "bullish",
                "scene_scenario": "Trend pullback needs a clean reclaim.",
                "scene_type": "trend_pullback",
                "entry_choreography": "Wait for the zone and require confirmation.",
                "preferred_entry_timing": "watch_zone",
                "confirmation_cues": ["zone_touch"],
                "invalidation_cues": ["support_lost"],
                "reason_codes": ["SCENE_TREND_PULLBACK"],
                "summary": "Telemetry-only scene review.",
                "execution_boundary": "metadata_only_no_order_authority",
            },
        },
        metadata_json={
            "source": "llm",
            "model": "gpt-4.1-mini",
            "usage": {"prompt_tokens": 1000, "completion_tokens": 250, "total_tokens": 1250},
            "decision_agreement": _decision_agreement(baseline="long", final="long", ai_used=True),
        },
        schema_valid=True,
    )
    db_session.add(run)
    db_session.flush()

    fact = performance_reporting.persist_decision_performance_fact(db_session, run)
    assert fact is not None
    assert fact.ai_usefulness_status == "pending_risk"
    assert fact.ai_actionable is True
    assert fact.ai_total_tokens == 1250
    assert fact.ai_known_cost_usd is not None

    risk = RiskCheck(
        symbol="BTCUSDT",
        decision_run_id=run.id,
        allowed=True,
        decision="long",
        reason_codes=[],
        approved_risk_pct=0.01,
        approved_leverage=2.0,
        payload={
            "debug_payload": {
                "expected_cost_gate": {
                    "status": "pass",
                    "expected_edge_bps": 80.0,
                    "expected_total_cost_bps": 12.5,
                    "net_expected_edge_bps": 67.5,
                }
            }
        },
    )
    db_session.add(risk)
    db_session.flush()
    order = Order(
        symbol="BTCUSDT",
        decision_run_id=run.id,
        risk_check_id=risk.id,
        side="long",
        order_type="limit",
        mode="live",
        status="filled",
        external_order_id="ai-usefulness-order",
        requested_quantity=0.1,
        requested_price=70000.0,
        filled_quantity=0.1,
        average_fill_price=70000.0,
        reason_codes=[],
        metadata_json={},
    )
    db_session.add(order)
    db_session.flush()
    db_session.add(
        Execution(
            order_id=order.id,
            symbol="BTCUSDT",
            status="filled",
            external_trade_id="ai-usefulness-fill",
            fill_price=70000.0,
            fill_quantity=0.1,
            fee_paid=0.5,
            commission_asset="USDT",
            slippage_pct=0.0,
            realized_pnl=5.0,
            payload={},
        )
    )
    db_session.flush()
    db_session.add(
        PendingEntryPlan(
            symbol="BTCUSDT",
            side="long",
            plan_status="triggered",
            source_decision_run_id=run.id,
            regime="bullish",
            posture="pullback_watch",
            rationale_codes=["AI_WATCH_ENTRY_PLAN"],
            source_timeframe="15m",
            entry_mode="pullback_confirm",
            entry_zone_min=69950.0,
            entry_zone_max=70050.0,
            invalidation_price=69400.0,
            max_chase_bps=12.0,
            idea_ttl_minutes=18,
            stop_loss=69400.0,
            take_profit=70800.0,
            risk_pct_cap=0.01,
            leverage_cap=1.0,
            expires_at=utcnow_naive() + timedelta(minutes=18),
            triggered_at=utcnow_naive(),
            idempotency_key="scene-telemetry-triggered",
            metadata_json={
                "execution_result": {
                    "status": "filled",
                    "order_id": order.id,
                    "risk_check_id": risk.id,
                }
            },
        )
    )
    db_session.add(
        PendingEntryPlan(
            symbol="BTCUSDT",
            side="long",
            plan_status="canceled",
            source_decision_run_id=run.id,
            regime="bullish",
            posture="pullback_watch",
            rationale_codes=["AI_WATCH_ENTRY_PLAN"],
            source_timeframe="15m",
            entry_mode="pullback_confirm",
            entry_zone_min=69800.0,
            entry_zone_max=69900.0,
            invalidation_price=69400.0,
            max_chase_bps=12.0,
            idea_ttl_minutes=18,
            stop_loss=69400.0,
            take_profit=70800.0,
            risk_pct_cap=0.01,
            leverage_cap=1.0,
            expires_at=utcnow_naive() + timedelta(minutes=18),
            canceled_at=utcnow_naive(),
            canceled_reason="PLAN_INVALIDATED",
            idempotency_key="scene-telemetry-invalidated",
            metadata_json={
                "last_transition_reason": "PLAN_INVALIDATED",
                "last_confirmation_tracking": {
                    "zone_touched": True,
                    "quality_score": 0.31,
                    "quality_threshold": 0.62,
                    "blocked_reason_codes": ["PLAN_CONFIRM_QUALITY_LOW"],
                    "confirmation_failed_reason": "STRUCTURE_CONFIRMATION_FAILED",
                },
            },
        )
    )
    db_session.flush()

    refreshed = performance_reporting.refresh_decision_performance_fact_usefulness(db_session, run.id)
    assert refreshed is not None
    saved = db_session.scalar(
        select(DecisionPerformanceFact).where(DecisionPerformanceFact.decision_run_id == run.id)
    )
    assert saved is not None
    assert saved.ai_usefulness_status == "filled"
    assert saved.ai_led_to_order is True
    assert saved.ai_led_to_fill is True
    assert saved.expected_edge_bps == pytest.approx(80.0)
    assert saved.expected_total_cost_bps == pytest.approx(12.5)
    assert saved.net_expected_edge_bps == pytest.approx(67.5)
    assert saved.pnl_data_confidence == "exchange_trade_linked"
    assert saved.telemetry_metadata["decision_quality"]["execution_count"] == 1
    assert saved.telemetry_metadata["scene_review"]["scene_type"] == "trend_pullback"
    assert saved.telemetry_metadata["scene_review"]["preferred_entry_timing"] == "watch_zone"
    scene_outcome = saved.telemetry_metadata["scene_plan_outcome"]
    assert scene_outcome["triggered_count"] == 1
    assert scene_outcome["canceled_count"] == 1
    assert scene_outcome["plan_confirm_quality_low_count"] == 1
    assert scene_outcome["plan_invalidated_count"] == 1
    assert scene_outcome["order_observed"] is True
    assert scene_outcome["fill_observed"] is True
    assert scene_outcome["outcome_bucket"] == "triggered_with_fill"
    assert saved.telemetry_metadata["decision_quality"]["scene_plan_outcome"]["fill_count"] == 1


def test_performance_endpoint_returns_extended_report_payload(tmp_path, monkeypatch) -> None:
    test_engine = create_engine(f"sqlite:///{tmp_path / 'performance_api.db'}", future=True)
    TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=test_engine)
    monkeypatch.setattr("trading_mvp.main.engine", test_engine)

    def override_get_db():
        with TestingSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestingSessionLocal() as session:
            _seed_performance_rows(session)
            session.commit()

        with TestClient(app) as client:
            response = client.get("/api/performance")

        assert response.status_code == 200
        payload = response.json()
        assert payload["window_hours"] == 24
        assert len(payload["windows"]) == 3
        assert payload["windows"][0]["summary"]["execution_pnl_basis"] == "execution_ledger_truth"
        assert payload["windows"][0]["summary"]["decision_context_basis"] == "agent_run_input_features_regime"
        assert "average_arrival_slippage_pct" in payload["windows"][0]["summary"]
        assert "average_first_fill_latency_seconds" in payload["windows"][0]["summary"]
        assert payload["windows"][0]["ai_telemetry"]["ai_calls_provider_invoked"] == 1
        assert payload["windows"][0]["ai_telemetry"]["ai_calls_skipped_preai"] == 1
        assert payload["windows"][0]["ai_telemetry"]["downstream"]["net_realized_pnl"] == 11.0
        assert payload["windows"][0]["ai_telemetry"]["downstream_by_decision"]["long"]["net_realized_pnl"] == 11.0
        assert payload["windows"][0]["ai_telemetry"]["downstream_by_decision"]["hold"]["risk_blocked"] == 1
        assert "ai_baseline_comparison" in payload["windows"][0]
        assert "buckets" in payload["windows"][0]["ai_baseline_comparison"]
        assert payload["windows"][0]["entry_quality"]["entry_marketable"]["trade_count"] == 1
        assert payload["windows"][0]["limited_live_readiness"]["read_only"] is True
        assert payload["windows"][0]["limited_live_readiness"]["status"] == "not_ready"
        assert "insufficient_sample" in payload["windows"][0]["limited_live_readiness"]["reason_codes"]
        assert "regimes" in payload["windows"][0]
        assert "directions" in payload["windows"][0]
        assert "feature_flags" in payload["windows"][0]
    finally:
        app.dependency_overrides.clear()


def test_opportunity_attribution_summary_endpoint_returns_24h_and_7d(testclient_db_factory) -> None:
    testclient_db_factory("opportunity_attribution_summary.db")

    with TestClient(app) as client:
        response = client.get("/api/analytics/opportunity-attribution/summary")

    assert response.status_code == 200
    payload = response.json()
    assert [window["window_label"] for window in payload["windows"]] == ["24h", "7d"]


def _opportunity_snapshot(
    *,
    symbol: str,
    snapshot_time,
    latest_price: float,
) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        timeframe="15m",
        snapshot_time=snapshot_time,
        latest_price=latest_price,
        latest_volume=100.0,
        candle_count=1,
        is_stale=False,
        is_complete=True,
        payload={"derivatives_context": {"funding_rate": 0.0}},
    )


def test_opportunity_attribution_reports_reason_quality_and_ai_flow(db_session) -> None:
    now = utcnow_naive().replace(second=0, microsecond=0)
    snapshots = [
        _opportunity_snapshot(symbol="BTCUSDT", snapshot_time=now, latest_price=100.0),
        _opportunity_snapshot(symbol="BTCUSDT", snapshot_time=now + timedelta(minutes=15), latest_price=101.0),
        _opportunity_snapshot(symbol="BTCUSDT", snapshot_time=now + timedelta(minutes=30), latest_price=102.0),
        _opportunity_snapshot(symbol="BTCUSDT", snapshot_time=now + timedelta(minutes=60), latest_price=103.0),
    ]
    db_session.add_all(snapshots)
    db_session.flush()

    decision = AgentRun(
        role="trading_decision",
        trigger_event="realtime_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="deterministic-mock",
        summary="deterministic hold with long baseline",
        input_payload={},
        output_payload={
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "decision": "hold",
            "rationale_codes": ["HOLD_DECISION"],
        },
        metadata_json={
            "source": "deterministic",
            "decision_agreement": {"baseline_decision": "long"},
            "last_ai_skip_reason": "SOFT_SIGNAL_REVIEW_SUPPRESSED_WEAK_CANDIDATE",
        },
        schema_valid=True,
    )
    db_session.add(decision)
    db_session.flush()
    decision.created_at = now

    risk = RiskCheck(
        symbol="BTCUSDT",
        decision_run_id=decision.id,
        market_snapshot_id=snapshots[0].id,
        allowed=False,
        decision="hold",
        reason_codes=["HOLD_DECISION"],
        approved_risk_pct=0.0,
        approved_leverage=0.0,
        payload={"reason_codes": ["HOLD_DECISION"]},
    )
    db_session.add(risk)
    db_session.flush()

    plan = PendingEntryPlan(
        symbol="BTCUSDT",
        side="long",
        plan_status="armed",
        source_decision_run_id=decision.id,
        regime="bullish",
        posture="pullback",
        rationale_codes=["TEST_PLAN"],
        source_timeframe="15m",
        entry_mode="pullback_confirm",
        entry_zone_min=99.0,
        entry_zone_max=101.0,
        invalidation_price=98.0,
        max_chase_bps=20.0,
        idea_ttl_minutes=60,
        stop_loss=98.0,
        take_profit=104.0,
        risk_pct_cap=0.01,
        leverage_cap=2.0,
        expires_at=now + timedelta(hours=1),
        idempotency_key="test-opportunity-plan",
        metadata_json={
            "source_risk_check_id": risk.id,
            "last_confirmation_tracking": {
                "market_snapshot_id": snapshots[0].id,
                "market_snapshot_time": now.isoformat(),
                "latest_price": 100.0,
                "blocked_reason_codes": ["PLAN_CONFIRM_QUALITY_LOW"],
                "zone_touched": True,
                "quality_score": 0.55,
                "quality_threshold": 0.62,
                "quality_reason": "QUALITY_BELOW_THRESHOLD",
            },
        },
    )
    db_session.add(plan)
    db_session.flush()
    plan.created_at = now

    db_session.add(
        AuditEvent(
            event_type="decision_ai_skipped",
            entity_type="decision_run",
            entity_id=str(decision.id),
            severity="info",
            message="AI skipped in test",
            payload={
                "symbol": "BTCUSDT",
                "reason": "SOFT_SIGNAL_REVIEW_SUPPRESSED_WEAK_CANDIDATE",
                "snapshot_id": snapshots[0].id,
            },
        )
    )
    db_session.flush()

    report = build_opportunity_attribution_report(
        db_session,
        lookback_hours=3,
        limit=20,
        notional_usdt=100.0,
    )

    assert report["overall"]["evaluated_candidates"] == 3
    assert report["reason_code_summary"]["HOLD_DECISION"]["profitable_candidates"] == 1
    assert report["reason_code_summary"]["PLAN_CONFIRM_QUALITY_LOW"]["profitable_candidates"] == 1
    assert report["reason_code_summary"]["SOFT_SIGNAL_REVIEW_SUPPRESSED_WEAK_CANDIDATE"][
        "profitable_candidates"
    ] == 1
    assert report["pending_quality_summary"]["0.50_0.62"]["profitable_candidates"] == 1
    assert report["ai_flow_summary"]["ai_skipped"]["profitable_candidates"] == 3
    assert report["reason_code_summary"]["HOLD_DECISION"]["horizons"]["60m"][
        "avg_net_after_fees_usdt"
    ] > 0


def test_opportunity_attribution_uses_decision_performance_facts_for_blocked_net_positive_candidates(
    db_session,
) -> None:
    now = utcnow_naive().replace(second=0, microsecond=0)
    db_session.add_all(
        [
            MarketSnapshot(
                symbol="BTCUSDT",
                timeframe="15m",
                snapshot_time=now + timedelta(minutes=15),
                latest_price=101.0,
                latest_volume=100.0,
                candle_count=1,
                is_stale=False,
                is_complete=True,
                payload={},
            ),
            MarketSnapshot(
                symbol="BTCUSDT",
                timeframe="15m",
                snapshot_time=now + timedelta(minutes=30),
                latest_price=102.0,
                latest_volume=100.0,
                candle_count=1,
                is_stale=False,
                is_complete=True,
                payload={},
            ),
            MarketSnapshot(
                symbol="BTCUSDT",
                timeframe="15m",
                snapshot_time=now + timedelta(minutes=60),
                latest_price=104.0,
                latest_volume=100.0,
                candle_count=1,
                is_stale=False,
                is_complete=True,
                payload={},
            ),
            DecisionPerformanceFact(
                decision_run_id=9101,
                provider_name="openai",
                symbol="BTCUSDT",
                timeframe="15m",
                decision="long",
                rationale_codes=["EXPECTED_COST_EXCEEDS_EDGE"],
                regime="bullish",
                trend_alignment="bullish_aligned",
                entry_zone_min=99.0,
                entry_zone_max=101.0,
                stop_loss=98.0,
                take_profit=104.0,
                baseline_decision="long",
                ai_used=True,
                comparison_bucket="ai_rejected_baseline_entry",
                ai_actionable=True,
                ai_blocked_by_risk=True,
                ai_led_to_order=False,
                ai_led_to_fill=False,
                ai_usefulness_status="risk_blocked",
                expected_edge_bps=40.0,
                expected_total_cost_bps=12.0,
                net_expected_edge_bps=28.0,
                pnl_data_confidence="not_realized",
                telemetry_metadata={},
                telemetry_output={"decision": "long"},
                created_at=now,
            ),
        ]
    )
    db_session.flush()

    report = build_opportunity_attribution_report(
        db_session,
        lookback_hours=3,
        limit=20,
        notional_usdt=100.0,
    )

    assert report["overall"]["source_counts"]["decision_performance_fact"] == 1
    assert report["overall"]["evaluated_candidates"] == 1
    assert report["overall"]["profitable_candidates"] == 1
    assert report["reason_code_summary"]["EXPECTED_COST_EXCEEDS_EDGE"]["profitable_candidates"] == 1
    fact_candidate = report["candidates"][0]
    assert fact_candidate["source"] == "decision_performance_fact"
    assert fact_candidate["would_have_been_profitable"] is True
    assert fact_candidate["best_net_after_fees_usdt"] > 0
