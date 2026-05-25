from __future__ import annotations

from datetime import timedelta
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import desc, select
from trading_mvp.main import app
from trading_mvp.models import (
    AccountLedgerEntry,
    Alert,
    AuditEvent,
    DecisionPerformanceFact,
    Execution,
    FeatureSnapshot,
    MarketSnapshot,
    Order,
    PendingEntryPlan,
    PnLSnapshot,
    Position,
    RiskCheck,
    SchedulerRun,
)
from trading_mvp.services.audit import (
    SENSITIVE_REDACTED_VALUE,
    record_audit_event,
    redact_sensitive_payload,
)
from trading_mvp.services.dashboard import (
    OPERATOR_EXTRACTED_SYMBOL_FALLBACK_SCAN_LIMIT,
    OPERATOR_RECENT_ROW_SCAN_LIMIT,
    _latest_rows_by_extracted_symbol,
    _latest_rows_by_symbol,
    classify_audit_event,
    get_alerts,
    get_audit_event_detail,
    get_audit_timeline,
    get_decisions,
    get_executions,
    get_operator_dashboard,
    get_orders,
    get_overview,
    get_positions,
    get_profitability_dashboard,
    get_risk_checks,
)
from trading_mvp.services.runtime_state import (
    mark_sync_issue,
    mark_sync_success,
    replace_market_stream_detail,
    set_candidate_selection_detail,
    set_reconciliation_detail,
    set_user_stream_detail,
)
from trading_mvp.services.secret_store import encrypt_secret
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive


def _seed_profitability_dashboard_rows(db_session) -> None:
    from trading_mvp.models import AgentRun

    now = utcnow_naive()
    settings = get_or_create_settings(db_session)
    settings.ai_enabled = True
    settings.adaptive_signal_enabled = True
    settings.trading_paused = True
    settings.pause_reason_code = "MANUAL_USER_REQUEST"
    settings.pause_origin = "manual"
    db_session.add(settings)
    db_session.flush()

    long_run = AgentRun(
        role="trading_decision",
        trigger_event="realtime_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="openai",
        summary="btc long",
        input_payload={
            "features": {
                "regime": {
                    "primary_regime": "bullish",
                    "trend_alignment": "bullish_aligned",
                    "volatility_regime": "normal",
                    "weak_volume": False,
                    "momentum_weakening": False,
                }
            }
        },
        output_payload={
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "decision": "long",
            "confidence": 0.72,
            "rationale_codes": ["TREND_UP", "BREAKOUT"],
            "explanation_short": "AI long proposal",
            "stop_loss": 69400.0,
            "take_profit": 70800.0,
            "max_holding_minutes": 60,
        },
        metadata_json={},
        schema_valid=True,
    )
    hold_run = AgentRun(
        role="trading_decision",
        trigger_event="realtime_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="deterministic-mock",
        summary="eth hold",
        input_payload={
            "features": {
                "regime": {
                    "primary_regime": "range",
                    "trend_alignment": "range",
                    "volatility_regime": "normal",
                    "weak_volume": True,
                    "momentum_weakening": True,
                }
            }
        },
        output_payload={
            "symbol": "ETHUSDT",
            "timeframe": "1h",
            "decision": "hold",
            "confidence": 0.34,
            "rationale_codes": ["RANGE_CHOP"],
            "explanation_short": "AI hold proposal",
            "max_holding_minutes": 120,
        },
        metadata_json={},
        schema_valid=True,
    )
    db_session.add_all([long_run, hold_run])
    db_session.flush()
    long_run.created_at = now - timedelta(hours=2)
    hold_run.created_at = now - timedelta(minutes=40)

    db_session.add_all(
        [
            RiskCheck(
                symbol="BTCUSDT",
                decision_run_id=long_run.id,
                allowed=True,
                decision="long",
                reason_codes=[],
                approved_risk_pct=0.01,
                approved_leverage=2.0,
                payload={"allowed": True, "decision": "long", "reason_codes": []},
            ),
            RiskCheck(
                symbol="ETHUSDT",
                decision_run_id=hold_run.id,
                allowed=False,
                decision="hold",
                reason_codes=["TRADING_PAUSED", "HOLD_DECISION"],
                approved_risk_pct=0.0,
                approved_leverage=0.0,
                payload={"allowed": False, "decision": "hold", "reason_codes": ["TRADING_PAUSED", "HOLD_DECISION"]},
            ),
        ]
    )
    db_session.flush()

    position = Position(
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
        realized_pnl=8.0,
        unrealized_pnl=0.0,
        metadata_json={},
        opened_at=now - timedelta(minutes=90),
        closed_at=now - timedelta(minutes=20),
    )
    db_session.add(position)
    db_session.flush()

    entry_order = Order(
        symbol="BTCUSDT",
        decision_run_id=long_run.id,
        position_id=position.id,
        side="buy",
        order_type="limit",
        mode="live",
        status="filled",
        external_order_id="btc-entry",
        requested_quantity=0.01,
        requested_price=70000.0,
        filled_quantity=0.01,
        average_fill_price=70008.0,
        reason_codes=[],
        metadata_json={
            "execution_policy": {"policy_profile": "entry_btc_fast"},
            "execution_quality": {
                "partial_fill_attempts": 1,
                "repriced_attempts": 1,
                "aggressive_fallback_used": False,
                "realized_slippage_pct": 0.0012,
                "fees_total": 0.4,
                "realized_pnl_total": 8.0,
                "net_realized_pnl_total": 7.6,
                "decision_quality_status": "profit",
                "execution_quality_status": "repriced_limit_fill",
            },
        },
    )
    exit_order = Order(
        symbol="BTCUSDT",
        decision_run_id=long_run.id,
        position_id=position.id,
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
    canceled_order = Order(
        symbol="BTCUSDT",
        decision_run_id=long_run.id,
        side="buy",
        order_type="limit",
        mode="live",
        status="canceled",
        external_order_id="btc-canceled",
        requested_quantity=0.01,
        requested_price=69980.0,
        filled_quantity=0.0,
        average_fill_price=0.0,
        reason_codes=[],
        metadata_json={"execution_policy": {"policy_profile": "entry_btc_fast"}},
    )
    expired_order = Order(
        symbol="BTCUSDT",
        decision_run_id=long_run.id,
        side="buy",
        order_type="limit",
        mode="live",
        status="expired",
        external_order_id="btc-expired",
        requested_quantity=0.01,
        requested_price=69970.0,
        filled_quantity=0.0,
        average_fill_price=0.0,
        reason_codes=[],
        metadata_json={"execution_policy": {"policy_profile": "entry_btc_fast"}},
    )
    db_session.add_all([entry_order, exit_order, canceled_order, expired_order])
    db_session.flush()
    entry_order.created_at = now - timedelta(minutes=89)
    exit_order.created_at = now - timedelta(minutes=16)
    canceled_order.created_at = now - timedelta(minutes=87)
    expired_order.created_at = now - timedelta(minutes=86)
    db_session.flush()

    db_session.add_all(
        [
            Execution(
                order_id=entry_order.id,
                position_id=position.id,
                symbol="BTCUSDT",
                status="filled",
                external_trade_id="btc-entry-fill",
                fill_price=70008.0,
                fill_quantity=0.01,
                fee_paid=0.2,
                commission_asset="USDT",
                slippage_pct=0.0012,
                realized_pnl=0.0,
                payload={"signed_slippage_bps": 1.1428571429},
            ),
            Execution(
                order_id=exit_order.id,
                position_id=position.id,
                symbol="BTCUSDT",
                status="filled",
                external_trade_id="btc-tp-fill",
                fill_price=70800.0,
                fill_quantity=0.01,
                fee_paid=0.2,
                commission_asset="USDT",
                slippage_pct=0.0,
                realized_pnl=8.0,
                payload={"signed_slippage_bps": 0.0},
            ),
        ]
    )
    db_session.flush()
    btc_entry_fill = db_session.query(Execution).filter_by(external_trade_id="btc-entry-fill").one()
    btc_tp_fill = db_session.query(Execution).filter_by(external_trade_id="btc-tp-fill").one()
    btc_entry_fill.created_at = now - timedelta(minutes=88, seconds=15)
    btc_tp_fill.created_at = now - timedelta(minutes=15)
    db_session.flush()

    db_session.add(
        AccountLedgerEntry(
            entry_type="funding",
            asset="USDT",
            symbol="BTCUSDT",
            amount=-0.25,
            external_ref_id="btc-funding-dashboard",
            occurred_at=now - timedelta(minutes=45),
            payload={"incomeType": "FUNDING_FEE"},
        )
    )
    db_session.flush()

    db_session.add(
        SchedulerRun(
            schedule_window="15m",
            workflow="realtime_cycle",
            status="completed",
            triggered_by="system",
            next_run_at=now + timedelta(minutes=15),
            outcome={"decision_run_id": long_run.id},
        )
    )
    db_session.add_all(
        [
            AuditEvent(
                event_type="decision_cycle_completed",
                entity_type="agent_run",
                entity_id=str(long_run.id),
                severity="info",
                message="Latest decision cycle completed.",
                payload={"decision_run_id": long_run.id},
            ),
            AuditEvent(
                event_type="risk_blocked",
                entity_type="risk_check",
                entity_id=str(hold_run.id),
                severity="warning",
                message="Hold decision remained blocked.",
                payload={"reason_codes": ["TRADING_PAUSED", "HOLD_DECISION"]},
            ),
        ]
    )
    db_session.flush()


def test_latest_rows_by_symbol_uses_recent_scan_with_offset_fallback(db_session) -> None:
    now = utcnow_naive()
    target = MarketSnapshot(
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=now - timedelta(minutes=OPERATOR_RECENT_ROW_SCAN_LIMIT + 5),
        latest_price=70000.0,
        latest_volume=1200.0,
        candle_count=60,
        is_stale=False,
        is_complete=True,
        payload={},
    )
    newer_noise = [
        MarketSnapshot(
            symbol=f"NOISE{i}USDT",
            timeframe="15m",
            snapshot_time=now - timedelta(seconds=i),
            latest_price=1.0,
            latest_volume=1.0,
            candle_count=60,
            is_stale=False,
            is_complete=True,
            payload={},
        )
        for i in range(OPERATOR_RECENT_ROW_SCAN_LIMIT + 1)
    ]
    db_session.add_all([target, *newer_noise])
    db_session.flush()

    rows = _latest_rows_by_symbol(
        db_session,
        MarketSnapshot,
        ["BTCUSDT"],
        MarketSnapshot.snapshot_time,
    )

    assert rows["BTCUSDT"].id == target.id


def test_latest_rows_by_extracted_symbol_bounds_offset_fallback(db_session) -> None:
    now = utcnow_naive()
    within_fallback_offset = OPERATOR_RECENT_ROW_SCAN_LIMIT + 5
    outside_fallback_offset = OPERATOR_RECENT_ROW_SCAN_LIMIT + OPERATOR_EXTRACTED_SYMBOL_FALLBACK_SCAN_LIMIT + 5
    noise_rows = [
        SchedulerRun(
            schedule_window="15m",
            workflow="interval_decision_cycle",
            status="success",
            triggered_by="scheduler",
            outcome={"symbol": f"NOISE{i}USDT"},
            created_at=now - timedelta(seconds=i),
        )
        for i in range(outside_fallback_offset + 3)
        if i not in {within_fallback_offset, outside_fallback_offset}
    ]
    within = SchedulerRun(
        schedule_window="15m",
        workflow="interval_decision_cycle",
        status="success",
        triggered_by="scheduler",
        outcome={"symbol": "BTCUSDT"},
        created_at=now - timedelta(seconds=within_fallback_offset),
    )
    outside = SchedulerRun(
        schedule_window="15m",
        workflow="interval_decision_cycle",
        status="success",
        triggered_by="scheduler",
        outcome={"symbol": "ETHUSDT"},
        created_at=now - timedelta(seconds=outside_fallback_offset),
    )
    db_session.add_all([*noise_rows, within, outside])
    db_session.flush()

    rows = _latest_rows_by_extracted_symbol(
        db_session,
        select(SchedulerRun)
        .where(SchedulerRun.workflow == "interval_decision_cycle")
        .order_by(desc(SchedulerRun.created_at)),
        ["BTCUSDT", "ETHUSDT"],
        lambda row: str((row.outcome if isinstance(row.outcome, dict) else {}).get("symbol") or "").upper(),
    )

    assert rows["BTCUSDT"].id == within.id
    assert "ETHUSDT" not in rows


def _seed_multi_symbol_operator_rows(db_session) -> None:
    from trading_mvp.models import AgentRun, DecisionPerformanceFact, MarketSnapshot

    now = utcnow_naive()
    settings = get_or_create_settings(db_session)
    settings.default_symbol = "BTCUSDT"
    settings.tracked_symbols = ["BTCUSDT", "ETHUSDT"]
    settings.default_timeframe = "15m"
    settings.live_trading_enabled = True
    settings.rollout_mode = "limited_live"
    settings.limited_live_max_notional = 600.0
    settings.manual_live_approval = True
    settings.live_execution_armed = True
    settings.live_execution_armed_until = now + timedelta(minutes=15)
    settings.trading_paused = False
    settings.pause_reason_detail = {
        "protection_recovery": {
            "status": "recovery_pending",
            "auto_recovery_active": True,
            "last_transition_at": now.isoformat(),
            "last_error": "Protective orders were not verified on the exchange.",
            "symbol_states": {
                "BTCUSDT": {
                    "state": "PROTECTION_REQUIRED",
                    "missing_components": ["take_profit"],
                    "failure_count": 2,
                    "auto_recovery_active": True,
                    "recovery_status": "recovery_pending",
                    "last_error": "Protective orders were not verified on the exchange.",
                    "last_transition_at": now.isoformat(),
                    "trigger_source": "protection_verify",
                }
            },
            "missing_symbols": ["BTCUSDT"],
            "missing_items": {"BTCUSDT": ["take_profit"]},
        }
    }
    db_session.add(settings)
    db_session.flush()

    btc_market = MarketSnapshot(
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=now - timedelta(minutes=1),
        latest_price=70500.0,
        latest_volume=1200.0,
        candle_count=200,
        is_stale=False,
        is_complete=True,
        payload={},
    )
    eth_market = MarketSnapshot(
        symbol="ETHUSDT",
        timeframe="15m",
        snapshot_time=now - timedelta(minutes=2),
        latest_price=3400.0,
        latest_volume=980.0,
        candle_count=200,
        is_stale=False,
        is_complete=True,
        payload={},
    )
    eth_prior_market = MarketSnapshot(
        symbol="ETHUSDT",
        timeframe="15m",
        snapshot_time=now - timedelta(minutes=5),
        latest_price=3392.0,
        latest_volume=910.0,
        candle_count=200,
        is_stale=False,
        is_complete=True,
        payload={},
    )
    db_session.add_all([btc_market, eth_market, eth_prior_market])
    db_session.flush()

    btc_run = AgentRun(
        role="trading_decision",
        trigger_event="realtime_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="openai",
        summary="btc blocked long",
        input_payload={
            "market_snapshot": {
                "symbol": "BTCUSDT",
                "timeframe": "15m",
                "snapshot_time": btc_market.snapshot_time.isoformat(),
                "latest_price": btc_market.latest_price,
                "is_stale": False,
                "is_complete": True,
            },
            "decision_reference": {
                "market_snapshot_id": btc_market.id,
                "market_snapshot_at": btc_market.snapshot_time.isoformat(),
                "market_snapshot_source": "refreshed",
                "market_snapshot_stale": False,
                "market_snapshot_incomplete": False,
                "account_sync_at": now.isoformat(),
                "positions_sync_at": now.isoformat(),
                "open_orders_sync_at": now.isoformat(),
                "protective_orders_sync_at": (now - timedelta(hours=2)).isoformat(),
                "account_sync_status": "fallback_reconciled",
                "sync_freshness_summary": {
                    "account": {"last_sync_at": now.isoformat(), "stale": False, "incomplete": False},
                    "positions": {"last_sync_at": now.isoformat(), "stale": False, "incomplete": False},
                    "open_orders": {"last_sync_at": now.isoformat(), "stale": False, "incomplete": False},
                    "protective_orders": {"last_sync_at": (now - timedelta(hours=2)).isoformat(), "stale": True, "incomplete": False},
                },
                "market_freshness_summary": {
                    "symbol": "BTCUSDT",
                    "timeframe": "15m",
                    "snapshot_at": btc_market.snapshot_time.isoformat(),
                    "stale": False,
                    "incomplete": False,
                },
                "freshness_blocking": True,
            },
            "features": {
                "trend_score": 1.48,
                "volume_ratio": 1.33,
                "momentum_score": 0.92,
                "breakout": {
                    "range_breakout_direction": "up",
                    "broke_swing_high": True,
                    "broke_swing_low": False,
                },
                "regime": {
                    "primary_regime": "bullish",
                    "trend_alignment": "bullish_aligned",
                    "volatility_regime": "normal",
                    "volume_regime": "strong",
                    "momentum_state": "stable",
                    "weak_volume": False,
                    "momentum_weakening": False,
                }
            }
        },
        output_payload={
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "decision": "long",
            "confidence": 0.78,
            "rationale_codes": ["TREND_UP"],
            "explanation_short": "BTC long candidate",
            "event_risk_acknowledgement": "High-impact macro event is approaching; event-aware caution applied.",
            "confidence_penalty_reason": "EVENT_WINDOW_PROXIMITY",
            "scenario_note": "Prefer confirmation after the event before fresh entry.",
        },
        metadata_json={
            "source": "llm",
            "holding_profile": "scalp",
            "holding_profile_reason": "scalp_default_intraday_bias",
            "active_position_prompt_route_context": {
                "management_only_open_position_route": True,
                "entry_proposal_suppression_active": True,
                "entry_proposal_suppressed_reason_code": "LARGEST_POSITION_LIMIT_REACHED",
                "allow_same_side_add_on": False,
                "allowed_add_on_side": None,
            },
            "active_position_entry_fingerprint_basis": {
                "position_state_bucket": "winning_protected",
                "regime_summary": {"primary_regime": "bullish"},
            },
            "selection_context": {
                "assigned_slot": "slot_1",
                "candidate_weight": 0.64,
                "capacity_reason": "mixed_breadth_moderate_capacity",
                "holding_profile": "scalp",
                "holding_profile_reason": "scalp_default_intraday_bias",
            },
            "slot_allocation": {
                "assigned_slot": "slot_1",
                "candidate_weight": 0.64,
                "capacity_reason": "mixed_breadth_moderate_capacity",
                "applies_soft_limit": False,
            },
            "ai_trigger": {
                "trigger_reason": "entry_candidate_event",
                "reason_codes": ["ENTRY_CANDIDATE_SELECTED"],
                "trigger_fingerprint": "btc-trigger-seed",
            },
            "last_ai_trigger_reason": "entry_candidate_event",
            "last_ai_invoked_at": (now - timedelta(minutes=6)).isoformat(),
            "next_ai_review_due_at": (now + timedelta(minutes=9)).isoformat(),
            "trigger_deduped": False,
            "trigger_fingerprint": "btc-trigger-seed",
            "last_ai_skip_reason": None,
        },
        schema_valid=True,
    )
    eth_run = AgentRun(
        role="trading_decision",
        trigger_event="realtime_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="openai",
        summary="eth tradable long",
        input_payload={
            "market_snapshot": {
                "symbol": "ETHUSDT",
                "timeframe": "15m",
                "snapshot_time": eth_prior_market.snapshot_time.isoformat(),
                "latest_price": eth_prior_market.latest_price,
                "is_stale": False,
                "is_complete": True,
            },
            "decision_reference": {
                "market_snapshot_id": eth_prior_market.id,
                "market_snapshot_at": eth_prior_market.snapshot_time.isoformat(),
                "market_snapshot_source": "refreshed",
                "market_snapshot_stale": False,
                "market_snapshot_incomplete": False,
                "account_sync_at": now.isoformat(),
                "positions_sync_at": now.isoformat(),
                "open_orders_sync_at": now.isoformat(),
                "protective_orders_sync_at": now.isoformat(),
                "account_sync_status": "fallback_reconciled",
                "sync_freshness_summary": {
                    "account": {"last_sync_at": now.isoformat(), "stale": False, "incomplete": False},
                    "positions": {"last_sync_at": now.isoformat(), "stale": False, "incomplete": False},
                    "open_orders": {"last_sync_at": now.isoformat(), "stale": False, "incomplete": False},
                    "protective_orders": {"last_sync_at": now.isoformat(), "stale": False, "incomplete": False},
                },
                "market_freshness_summary": {
                    "symbol": "ETHUSDT",
                    "timeframe": "15m",
                    "snapshot_at": eth_prior_market.snapshot_time.isoformat(),
                    "stale": False,
                    "incomplete": False,
                },
                "freshness_blocking": False,
            },
            "features": {
                "trend_score": 0.88,
                "volume_ratio": 1.21,
                "momentum_score": 0.46,
                "breakout": {
                    "range_breakout_direction": "none",
                    "broke_swing_high": False,
                    "broke_swing_low": False,
                },
                "regime": {
                    "primary_regime": "bullish",
                    "trend_alignment": "bullish_aligned",
                    "volatility_regime": "normal",
                    "volume_regime": "normal",
                    "momentum_state": "strengthening",
                    "weak_volume": False,
                    "momentum_weakening": False,
                }
            }
        },
        output_payload={
            "symbol": "ETHUSDT",
            "timeframe": "15m",
            "decision": "long",
            "confidence": 0.66,
            "rationale_codes": ["PULLBACK_ENTRY"],
            "explanation_short": "ETH long candidate",
        },
        metadata_json={
            "source": "llm",
            "holding_profile": "swing",
            "holding_profile_reason": "swing_intraday_trend_extension_allowed",
            "selection_context": {
                "assigned_slot": "slot_2",
                "candidate_weight": 0.42,
                "capacity_reason": "mixed_breadth_moderate_capacity",
                "holding_profile": "swing",
                "holding_profile_reason": "swing_intraday_trend_extension_allowed",
                "slot_applies_soft_cap": True,
            },
            "slot_allocation": {
                "assigned_slot": "slot_2",
                "candidate_weight": 0.42,
                "capacity_reason": "mixed_breadth_moderate_capacity",
                "applies_soft_limit": True,
            },
            "ai_trigger": {
                "trigger_reason": "entry_candidate_event",
                "reason_codes": ["ENTRY_CANDIDATE_SELECTED"],
                "trigger_fingerprint": "eth-trigger-seed",
            },
            "last_ai_trigger_reason": "entry_candidate_event",
            "last_ai_invoked_at": (now - timedelta(minutes=3)).isoformat(),
            "next_ai_review_due_at": (now + timedelta(minutes=12)).isoformat(),
            "trigger_deduped": False,
            "trigger_fingerprint": "eth-trigger-seed",
            "last_ai_skip_reason": None,
        },
        schema_valid=True,
    )
    db_session.add_all([btc_run, eth_run])
    db_session.flush()
    btc_run.created_at = now - timedelta(minutes=6)
    eth_run.created_at = now - timedelta(minutes=3)
    db_session.add_all(
        [
            DecisionPerformanceFact(
                decision_run_id=btc_run.id,
                provider_name=btc_run.provider_name,
                symbol="BTCUSDT",
                timeframe="15m",
                decision="long",
                rationale_codes=["TREND_UP"],
                regime="bullish",
                trend_alignment="bullish_aligned",
                telemetry_metadata={"source": "llm"},
                telemetry_output={"decision": "long"},
                created_at=btc_run.created_at,
                updated_at=btc_run.updated_at,
            ),
            DecisionPerformanceFact(
                decision_run_id=eth_run.id,
                provider_name=eth_run.provider_name,
                symbol="ETHUSDT",
                timeframe="15m",
                decision="long",
                rationale_codes=["PULLBACK_ENTRY"],
                regime="bullish",
                trend_alignment="bullish_aligned",
                telemetry_metadata={"source": "llm"},
                telemetry_output={"decision": "long"},
                created_at=eth_run.created_at,
                updated_at=eth_run.updated_at,
            ),
        ]
    )

    btc_risk = RiskCheck(
        symbol="BTCUSDT",
        decision_run_id=btc_run.id,
        allowed=False,
        decision="long",
        reason_codes=["POSITION_STATE_STALE"],
        approved_risk_pct=0.0,
        approved_leverage=0.0,
        payload={
            "allowed": False,
            "decision": "long",
            "operating_state": "TRADABLE",
            "blocked_reason_codes": ["POSITION_STATE_STALE"],
            "adjustment_reason_codes": [],
            "debug_payload": {
                "slot_allocation": {
                    "assigned_slot": "slot_1",
                    "candidate_weight": 0.64,
                    "capacity_reason": "mixed_breadth_moderate_capacity",
                    "applies_soft_limit": False,
                },
                "holding_profile": {
                    "holding_profile": "scalp",
                    "holding_profile_reason": "scalp_default_intraday_bias",
                    "hard_stop_active": True,
                    "stop_widening_allowed": False,
                },
            },
        },
    )
    eth_risk = RiskCheck(
        symbol="ETHUSDT",
        decision_run_id=eth_run.id,
        allowed=True,
        decision="long",
        reason_codes=[],
        approved_risk_pct=0.01,
        approved_leverage=2.0,
        payload={
            "allowed": True,
            "decision": "long",
            "operating_state": "TRADABLE",
            "raw_projected_notional": 158000.0,
            "approved_projected_notional": 150000.0,
            "approved_quantity": 44.117647,
            "auto_resized_entry": True,
            "size_adjustment_ratio": 0.949367,
            "auto_resize_reason": "CLAMPED_TO_SINGLE_POSITION_HEADROOM",
            "exposure_headroom_snapshot": {"limiting_headroom_notional": 150000.0},
            "adjustment_reason_codes": ["ENTRY_AUTO_RESIZED", "PORTFOLIO_SLOT_SOFT_CAP"],
            "debug_payload": {
                "slot_allocation": {
                    "assigned_slot": "slot_2",
                    "candidate_weight": 0.42,
                    "capacity_reason": "mixed_breadth_moderate_capacity",
                    "applies_soft_limit": True,
                },
                "holding_profile": {
                    "holding_profile": "swing",
                    "holding_profile_reason": "swing_intraday_trend_extension_allowed",
                    "hard_stop_active": True,
                    "stop_widening_allowed": False,
                },
            },
        },
    )
    db_session.add_all([btc_risk, eth_risk])
    db_session.flush()

    btc_position = Position(
        symbol="BTCUSDT",
        mode="live",
        side="long",
        status="open",
        quantity=0.02,
        entry_price=70000.0,
        mark_price=70500.0,
        leverage=2.0,
        stop_loss=69000.0,
        take_profit=71500.0,
        realized_pnl=0.0,
        unrealized_pnl=10.0,
        metadata_json={
            "position_management": {
                "holding_profile": "swing",
                "holding_profile_reason": "swing_intraday_trend_extension_allowed",
                "initial_stop_type": "deterministic_hard_stop",
                "ai_stop_management_allowed": True,
                "hard_stop_active": True,
                "stop_widening_allowed": False,
            },
            "position_exit_review": {
                "recommendation": "hold_runner",
                "confidence": 0.61,
                "profit_take_bias": "let_runner_work",
                "runner_state": "healthy",
                "exit_urgency": "watch",
                "summary": "Runner still has supportive trend context.",
                "reason_codes": ["RUNNER_TREND_SUPPORT"],
                "profit_protection_cues": ["current_r=1.2"],
                "runner_invalidation_cues": [],
                "data_quality_notes": [],
                "advisory_only": True,
                "execution_boundary": "metadata_only_no_order_authority",
                "agent_run_id": 9999,
            },
        },
    )
    db_session.add(btc_position)
    db_session.flush()

    btc_stop = Order(
        symbol="BTCUSDT",
        decision_run_id=btc_run.id,
        position_id=btc_position.id,
        side="sell",
        order_type="stop_market",
        mode="live",
        status="pending",
        external_order_id="btc-stop",
        reduce_only=True,
        close_only=True,
        requested_quantity=0.02,
        requested_price=69000.0,
        filled_quantity=0.0,
        average_fill_price=0.0,
        reason_codes=["POSITION_STATE_STALE"],
        metadata_json={"execution_quality": {"decision_quality_status": "pending"}},
    )
    eth_order = Order(
        symbol="ETHUSDT",
        decision_run_id=eth_run.id,
        side="buy",
        order_type="limit",
        mode="live",
        status="filled",
        external_order_id="eth-entry",
        requested_quantity=0.3,
        requested_price=3395.0,
        filled_quantity=0.3,
        average_fill_price=3396.0,
        reason_codes=[],
        metadata_json={
            "execution_quality": {
                "execution_quality_status": "limit_fill",
                "decision_quality_status": "pending",
            }
        },
    )
    db_session.add_all([btc_stop, eth_order])
    db_session.flush()

    eth_fill_1 = Execution(
        order_id=eth_order.id,
        symbol="ETHUSDT",
        status="filled",
        external_trade_id="eth-fill-1",
        fill_price=3395.5,
        fill_quantity=0.1,
        fee_paid=0.03,
        commission_asset="USDT",
        slippage_pct=0.0003,
        realized_pnl=0.0,
        payload={},
    )
    eth_fill_2 = Execution(
        order_id=eth_order.id,
        symbol="ETHUSDT",
        status="filled",
        external_trade_id="eth-fill-2",
        fill_price=3396.0,
        fill_quantity=0.2,
        fee_paid=0.07,
        commission_asset="USDT",
        slippage_pct=0.0004,
        realized_pnl=0.0,
        payload={},
    )
    db_session.add_all([eth_fill_1, eth_fill_2])
    db_session.flush()
    eth_fill_1.created_at = now - timedelta(seconds=20)
    eth_fill_2.created_at = now - timedelta(seconds=5)
    db_session.add_all(
        [
            SchedulerRun(
                schedule_window="15m",
                workflow="interval_decision_cycle",
                status="success",
                triggered_by="scheduler",
                next_run_at=now + timedelta(minutes=1),
                outcome={
                    "symbol": "BTCUSDT",
                    "last_ai_trigger_reason": "entry_candidate_event",
                    "last_ai_invoked_at": (now - timedelta(minutes=6)).isoformat(),
                    "next_ai_review_due_at": (now + timedelta(minutes=9)).isoformat(),
                    "trigger_deduped": True,
                    "trigger_fingerprint": "btc-trigger-seed",
                    "last_ai_skip_reason": "TRIGGER_DEDUPED",
                    "trigger": {
                        "trigger_reason": "entry_candidate_event",
                        "reason_codes": ["ENTRY_CANDIDATE_SELECTED"],
                        "trigger_fingerprint": "btc-trigger-seed",
                    },
                },
            ),
            SchedulerRun(
                schedule_window="15m",
                workflow="interval_decision_cycle",
                status="success",
                triggered_by="scheduler",
                next_run_at=now + timedelta(minutes=1),
                outcome={
                    "symbol": "ETHUSDT",
                    "last_ai_trigger_reason": "entry_candidate_event",
                    "last_ai_invoked_at": (now - timedelta(minutes=3)).isoformat(),
                    "next_ai_review_due_at": (now + timedelta(minutes=12)).isoformat(),
                    "trigger_deduped": False,
                    "trigger_fingerprint": "eth-trigger-seed",
                    "last_ai_skip_reason": None,
                    "trigger": {
                        "trigger_reason": "entry_candidate_event",
                        "reason_codes": ["ENTRY_CANDIDATE_SELECTED"],
                        "trigger_fingerprint": "eth-trigger-seed",
                    },
                },
            ),
            AuditEvent(
                event_type="live_approval_armed",
                entity_type="settings",
                entity_id=str(settings.id),
                severity="warning",
                message="Manual live execution window armed.",
                payload={
                    "approval_armed": True,
                    "approval_window_open": True,
                    "approval_state": "armed",
                    "approval_expires_at": settings.live_execution_armed_until.isoformat(),
                    "approval_window_minutes": 15,
                    "trigger_source": "manual_live_approval",
                },
            ),
            AuditEvent(
                event_type="risk_blocked",
                entity_type="risk_check",
                entity_id="BTCUSDT",
                severity="warning",
                message="BTC entry blocked because position state is stale.",
                payload={"symbol": "BTCUSDT", "reason_codes": ["POSITION_STATE_STALE"]},
            ),
            AuditEvent(
                event_type="live_execution",
                entity_type="order",
                entity_id="ETHUSDT",
                severity="info",
                message="ETH order filled.",
                payload={"symbol": "ETHUSDT", "order_status": "filled", "filled_quantity": 0.3},
            ),
            AuditEvent(
                event_type="protection_verification_failed",
                entity_type="position",
                entity_id="BTCUSDT",
                severity="warning",
                message="BTC protective orders were not verified.",
                payload={
                    "symbol": "BTCUSDT",
                    "trigger_source": "protection_verify",
                    "recovery_status": "recovery_pending",
                    "missing_components": ["take_profit"],
                    "last_error": "Protective orders were not verified on the exchange.",
                    "to_state": "verify_failed",
                    "verification_detail": {
                        "error": "Protective orders were not verified on the exchange.",
                        "expected_order_types": ["STOP_MARKET", "TAKE_PROFIT_MARKET"],
                    },
                },
            ),
        ]
    )
    mark_sync_success(settings, scope="account", synced_at=now)
    mark_sync_success(settings, scope="positions", synced_at=now)
    mark_sync_success(settings, scope="open_orders", synced_at=now)
    mark_sync_success(settings, scope="protective_orders", synced_at=now)
    set_candidate_selection_detail(
        settings,
        generated_at=now,
        mode="portfolio_rotation_top_n",
        max_selected=2,
        selected_symbols=["BTCUSDT", "ETHUSDT"],
        skipped_symbols=[],
        capacity_reason="mixed_breadth_moderate_capacity",
        rankings=[
            {
                "symbol": "BTCUSDT",
                "selected": True,
                "selection_reason": "ranked_portfolio_focus",
                "selected_reason": "ranked_portfolio_focus",
                "assigned_slot": "slot_1",
                "candidate_weight": 0.64,
                "capacity_reason": "mixed_breadth_moderate_capacity",
                "holding_profile": "scalp",
                "holding_profile_reason": "scalp_default_intraday_bias",
                "strategy_engine": "trend_pullback_engine",
                "slot_applies_soft_cap": False,
            },
            {
                "symbol": "ETHUSDT",
                "selected": True,
                "selection_reason": "ranked_portfolio_focus",
                "selected_reason": "ranked_portfolio_focus",
                "assigned_slot": "slot_2",
                "candidate_weight": 0.42,
                "capacity_reason": "mixed_breadth_moderate_capacity",
                "holding_profile": "swing",
                "holding_profile_reason": "swing_intraday_trend_extension_allowed",
                "strategy_engine": "trend_pullback_engine",
                "slot_applies_soft_cap": True,
            },
        ],
    )
    db_session.flush()


def test_order_and_execution_filters(db_session) -> None:
    primary_order = Order(
        symbol="BTCUSDT",
        side="buy",
        order_type="market",
        mode="live",
        status="filled",
        external_order_id="btc-order-1",
        requested_quantity=0.01,
        requested_price=65000.0,
    )
    secondary_order = Order(
        symbol="ETHUSDT",
        side="sell",
        order_type="limit",
        mode="live",
        status="rejected",
        external_order_id="eth-order-1",
        requested_quantity=0.2,
        requested_price=3200.0,
    )
    db_session.add_all([primary_order, secondary_order])
    db_session.flush()

    db_session.add_all(
        [
            Execution(
                order_id=primary_order.id,
                symbol="BTCUSDT",
                status="filled",
                external_trade_id="btc-trade-1",
                fill_price=65010.0,
                fill_quantity=0.01,
                payload={},
            ),
            Execution(
                order_id=secondary_order.id,
                symbol="ETHUSDT",
                status="rejected",
                external_trade_id="eth-trade-1",
                fill_price=3195.0,
                fill_quantity=0.2,
                payload={},
            ),
        ]
    )
    db_session.flush()

    filtered_orders = get_orders(db_session, symbol="BTCUSDT", status="filled", search="btc")
    filtered_executions = get_executions(db_session, symbol="BTCUSDT", status="filled", search="btc")

    assert len(filtered_orders) == 1
    assert filtered_orders[0]["symbol"] == "BTCUSDT"
    assert len(filtered_executions) == 1
    assert filtered_executions[0]["symbol"] == "BTCUSDT"


def test_order_and_execution_position_filter_compact_payload(db_session) -> None:
    primary_position = Position(
        symbol="BTCUSDT",
        mode="live",
        side="long",
        status="open",
        quantity=0.01,
        entry_price=65000.0,
        mark_price=65100.0,
        leverage=1.0,
        stop_loss=64500.0,
        take_profit=66000.0,
    )
    secondary_position = Position(
        symbol="ETHUSDT",
        mode="live",
        side="short",
        status="open",
        quantity=0.2,
        entry_price=3200.0,
        mark_price=3190.0,
        leverage=1.0,
        stop_loss=3250.0,
        take_profit=3100.0,
    )
    db_session.add_all([primary_position, secondary_position])
    db_session.flush()

    primary_order = Order(
        symbol="BTCUSDT",
        position_id=primary_position.id,
        side="buy",
        order_type="market",
        mode="live",
        status="filled",
        external_order_id="btc-position-order",
        requested_quantity=0.01,
        requested_price=65000.0,
        metadata_json={"raw_exchange_response": {"large": "payload"}},
    )
    secondary_order = Order(
        symbol="ETHUSDT",
        position_id=secondary_position.id,
        side="sell",
        order_type="market",
        mode="live",
        status="filled",
        external_order_id="eth-position-order",
        requested_quantity=0.2,
        requested_price=3200.0,
        metadata_json={"raw_exchange_response": {"large": "payload"}},
    )
    db_session.add_all([primary_order, secondary_order])
    db_session.flush()

    db_session.add_all(
        [
            Execution(
                order_id=primary_order.id,
                position_id=primary_position.id,
                symbol="BTCUSDT",
                status="filled",
                external_trade_id="btc-position-trade",
                fill_price=65005.0,
                fill_quantity=0.01,
                fee_paid=0.1,
                payload={"raw": {"large": "payload"}},
            ),
            Execution(
                order_id=secondary_order.id,
                position_id=secondary_position.id,
                symbol="ETHUSDT",
                status="filled",
                external_trade_id="eth-position-trade",
                fill_price=3195.0,
                fill_quantity=0.2,
                fee_paid=0.12,
                payload={"raw": {"large": "payload"}},
            ),
        ]
    )
    db_session.flush()

    order_rows = get_orders(db_session, position_id=primary_position.id, compact=True, limit=20)
    execution_rows = get_executions(db_session, position_id=primary_position.id, compact=True, limit=20)

    assert len(order_rows) == 1
    assert order_rows[0]["position_id"] == primary_position.id
    assert order_rows[0]["payload_mode"] == "compact"
    assert order_rows[0]["close_execution_sync_status"] == "UNKNOWN"
    assert "metadata_json" not in order_rows[0]
    assert "raw_exchange_response" not in order_rows[0]

    assert len(execution_rows) == 1
    assert execution_rows[0]["position_id"] == primary_position.id
    assert execution_rows[0]["payload_mode"] == "compact"
    assert execution_rows[0]["external_trade_id"] == "btc-position-trade"
    assert "payload" not in execution_rows[0]
    assert "execution_policy" not in execution_rows[0]
    assert "decision_summary" not in execution_rows[0]


def test_orders_mark_missing_close_execution_for_finished_protective_order(db_session) -> None:
    position = Position(
        symbol="BTCUSDT",
        mode="live",
        side="short",
        status="closed",
        quantity=0.0,
        entry_price=81536.6,
        mark_price=81089.0,
        leverage=1.0,
        stop_loss=82000.0,
        take_profit=81089.0,
    )
    db_session.add(position)
    db_session.flush()
    entry_order = Order(
        symbol="BTCUSDT",
        position_id=position.id,
        side="sell",
        order_type="limit",
        mode="live",
        status="filled",
        exchange_status="FILLED",
        external_order_id="1005147990724",
        requested_quantity=0.001,
        requested_price=81536.6,
        filled_quantity=0.001,
        average_fill_price=81536.6,
    )
    protective_order = Order(
        symbol="BTCUSDT",
        position_id=position.id,
        side="buy",
        order_type="take_profit_market",
        mode="live",
        status="expired",
        exchange_status="FINISHED",
        external_order_id="2000000895228311",
        client_order_id="protective-btc-1",
        reduce_only=True,
        close_only=True,
        requested_quantity=0.001,
        requested_price=81089.0,
        reason_codes=[],
    )
    db_session.add_all([entry_order, protective_order])
    db_session.flush()
    db_session.add(
        Execution(
            order_id=entry_order.id,
            position_id=position.id,
            symbol="BTCUSDT",
            status="filled",
            external_trade_id="7635750097",
            fill_price=81536.6,
            fill_quantity=0.001,
            fee_paid=0.0407683,
            commission_asset="USDT",
            realized_pnl=0.0,
            payload={},
        )
    )
    db_session.flush()

    rows = get_orders(db_session, symbol="BTCUSDT")
    by_order_id = {row["id"]: row for row in rows}

    assert by_order_id[entry_order.id]["missing_close_execution"] is True
    assert by_order_id[entry_order.id]["close_execution_sync_status"] == "MISSING"
    assert by_order_id[entry_order.id]["realized_pnl_confirmed"] is False
    assert by_order_id[entry_order.id]["pnl_source"] == "UNKNOWN"
    assert by_order_id[entry_order.id]["fee_source"] == "LOCAL_EXECUTIONS"
    assert by_order_id[entry_order.id]["fee_confirmed"] is False
    assert by_order_id[entry_order.id]["fee_warning_message"] == "청산 수수료 미반영"
    assert by_order_id[protective_order.id]["missing_close_execution"] is True


def test_orders_mark_realized_pnl_confirmed_after_close_execution_backfill(db_session) -> None:
    position = Position(
        symbol="BTCUSDT",
        mode="live",
        side="short",
        status="closed",
        quantity=0.0,
        entry_price=81536.6,
        mark_price=81089.0,
        leverage=1.0,
        stop_loss=82000.0,
        take_profit=81089.0,
    )
    db_session.add(position)
    db_session.flush()
    entry_order = Order(
        symbol="BTCUSDT",
        position_id=position.id,
        side="sell",
        order_type="limit",
        mode="live",
        status="filled",
        exchange_status="FILLED",
        external_order_id="1005147990724",
        requested_quantity=0.001,
        requested_price=81536.6,
        filled_quantity=0.001,
        average_fill_price=81536.6,
    )
    protective_order = Order(
        symbol="BTCUSDT",
        position_id=position.id,
        side="buy",
        order_type="take_profit_market",
        mode="live",
        status="filled",
        exchange_status="FINISHED",
        external_order_id="2000000895228311",
        client_order_id="protective-btc-1",
        reduce_only=True,
        close_only=True,
        requested_quantity=0.001,
        requested_price=81089.0,
        filled_quantity=0.001,
        average_fill_price=81089.0,
    )
    db_session.add_all([entry_order, protective_order])
    db_session.flush()
    db_session.add_all(
        [
            Execution(
                order_id=entry_order.id,
                position_id=position.id,
                symbol="BTCUSDT",
                status="filled",
                external_trade_id="7635750097",
                fill_price=81536.6,
                fill_quantity=0.001,
                fee_paid=0.0407683,
                commission_asset="USDT",
                realized_pnl=0.0,
                payload={},
            ),
            Execution(
                order_id=protective_order.id,
                position_id=position.id,
                symbol="BTCUSDT",
                status="filled",
                external_trade_id="7635814641",
                fill_price=81089.0,
                fill_quantity=0.001,
                fee_paid=0.04054449,
                commission_asset="USDT",
                realized_pnl=0.4476,
                payload={
                    "exchange": "BINANCE",
                    "source": "EXCHANGE_BACKFILL",
                    "trade": {"realizedPnl": "0.44760000"},
                },
            ),
        ]
    )
    db_session.flush()

    rows = get_orders(db_session, symbol="BTCUSDT")
    by_order_id = {row["id"]: row for row in rows}

    assert by_order_id[entry_order.id]["missing_close_execution"] is False
    assert by_order_id[entry_order.id]["close_execution_sync_status"] == "COMPLETE"
    assert by_order_id[entry_order.id]["realized_pnl_confirmed"] is True
    assert by_order_id[entry_order.id]["pnl_source"] == "EXCHANGE"
    assert by_order_id[protective_order.id]["close_execution_sync_status"] == "COMPLETE"


def test_audit_filters(db_session) -> None:
    db_session.add_all(
        [
            AuditEvent(
                event_type="live_sync",
                entity_type="binance",
                entity_id="BTCUSDT",
                severity="info",
                message="Live exchange state synchronized.",
                payload={},
            ),
            AuditEvent(
                event_type="scheduler_run_failed",
                entity_type="scheduler_run",
                entity_id="24h",
                severity="warning",
                message="Scheduled workflow failed.",
                payload={},
            ),
        ]
    )
    db_session.flush()

    filtered = get_audit_timeline(db_session, event_type="scheduler_run_failed", severity="warning", search="scheduled")

    assert len(filtered) == 1
    assert filtered[0]["event_type"] == "scheduler_run_failed"
    assert filtered[0]["event_category"] == "health_system"


def test_audit_compact_list_filters_and_detail_lazy_payload(db_session) -> None:
    risk_event = AuditEvent(
        event_type="risk_blocked",
        entity_type="risk_check",
        entity_id="123",
        severity="warning",
        message="Risk guard blocked the entry.",
        payload={
            "risk_id": 123,
            "symbol": "BTCUSDT",
            "blocked_reason": "POSITION_STATE_STALE",
            "raw_request": {"large": "payload"},
        },
    )
    execution_event = AuditEvent(
        event_type="live_execution",
        entity_type="order",
        entity_id="456",
        severity="info",
        message="Live order filled.",
        payload={"order_id": 456, "symbol": "ETHUSDT", "raw_response": {"large": "payload"}},
    )
    db_session.add_all([risk_event, execution_event])
    db_session.flush()

    rows = get_audit_timeline(
        db_session,
        event_category="risk",
        severity="warning",
        search="risk",
        sort="oldest",
        compact=True,
        limit=10,
    )

    assert len(rows) == 1
    assert rows[0]["id"] == risk_event.id
    assert rows[0]["event_category"] == "risk"
    assert rows[0]["related_type"] == "risk_id"
    assert rows[0]["related_id"] == 123
    assert rows[0]["has_payload"] is True
    assert "payload" not in rows[0]
    assert "raw_request" not in rows[0]

    detail = get_audit_event_detail(db_session, risk_event.id)

    assert detail is not None
    assert detail["payload"]["raw_request"]["large"] == "payload"
    assert detail["event_category"] == "risk"


def test_get_alerts_can_filter_acknowledged_rows(db_session) -> None:
    db_session.add_all(
        [
            Alert(
                category="execution",
                severity="error",
                title="Open orders sync failed",
                message="old permission error",
                acknowledged=True,
                payload={"reason_code": "EXCHANGE_AUTH_PERMISSION_REJECTED"},
            ),
            Alert(
                category="risk",
                severity="warning",
                title="Risk gate active",
                message="current unresolved alert",
                acknowledged=False,
                payload={"reason_code": "HOLD_DECISION"},
            ),
        ]
    )
    db_session.flush()

    rows = get_alerts(db_session, acknowledged=False)

    assert [row["title"] for row in rows] == ["Risk gate active"]


def test_audit_payload_redaction_masks_recursive_sensitive_keys() -> None:
    payload = {
        "user_stream_summary": {
            "listen_key": "listen-key-raw",
            "listenKey": "listen-key-camel",
            "listen_key_present": True,
            "listen_key_created_at": "2026-05-24T00:00:00",
        },
        "openai_api_key": "sk-raw",
        "binance_api_secret": "binance-secret-raw",
        "nested": [
            {"refresh_token": "refresh-raw"},
            {"token": "token-raw"},
            {"password": "password-raw"},
        ],
        "usage": {"ai_total_tokens": 1234},
    }

    redacted = redact_sensitive_payload(payload)

    assert redacted["user_stream_summary"]["listen_key"] == SENSITIVE_REDACTED_VALUE
    assert redacted["user_stream_summary"]["listenKey"] == SENSITIVE_REDACTED_VALUE
    assert redacted["user_stream_summary"]["listen_key_present"] is True
    assert redacted["user_stream_summary"]["listen_key_created_at"] == "2026-05-24T00:00:00"
    assert redacted["openai_api_key"] == SENSITIVE_REDACTED_VALUE
    assert redacted["binance_api_secret"] == SENSITIVE_REDACTED_VALUE
    assert redacted["nested"][0]["refresh_token"] == SENSITIVE_REDACTED_VALUE
    assert redacted["nested"][1]["token"] == SENSITIVE_REDACTED_VALUE
    assert redacted["nested"][2]["password"] == SENSITIVE_REDACTED_VALUE
    assert redacted["usage"]["ai_total_tokens"] == 1234


def test_record_audit_event_redacts_sensitive_payload_before_storage(db_session) -> None:
    event = record_audit_event(
        db_session,
        event_type="scheduler_live_poll_sync",
        entity_type="scheduler_run",
        entity_id="live",
        message="Live poll synchronized.",
        payload={
            "user_stream_summary": {
                "listen_key": "listen-key-raw",
                "status": "connected",
            },
            "api_key": "api-key-raw",
            "safe": "visible",
        },
    )
    db_session.flush()

    stored = db_session.get(AuditEvent, event.id)

    assert stored is not None
    assert stored.payload["user_stream_summary"]["listen_key"] == SENSITIVE_REDACTED_VALUE
    assert stored.payload["user_stream_summary"]["status"] == "connected"
    assert stored.payload["api_key"] == SENSITIVE_REDACTED_VALUE
    assert stored.payload["safe"] == "visible"


def test_audit_api_redacts_legacy_sensitive_payload_rows(testclient_db_factory) -> None:
    TestingSessionLocal = testclient_db_factory("audit_redaction.db")

    with TestingSessionLocal() as session:
        legacy = AuditEvent(
            event_type="scheduler_live_poll_sync",
            entity_type="scheduler_run",
            entity_id="live",
            severity="info",
            message="Live poll synchronized.",
            payload={
                "user_stream_summary": {
                    "listen_key": "legacy-listen-key",
                    "status": "connected",
                },
                "api_key": "legacy-api-key",
                "secret": "legacy-secret",
                "nested": {"token": "legacy-token", "safe": "visible"},
            },
        )
        session.add(legacy)
        session.commit()
        event_id = legacy.id

    with TestClient(app) as client:
        list_response = client.get("/api/audit", params={"event_type": "scheduler_live_poll_sync", "limit": 1})
        detail_response = client.get(f"/api/audit/{event_id}")

    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    list_payload = list_response.json()[0]["payload"]
    detail_payload = detail_response.json()["payload"]

    for payload in (list_payload, detail_payload):
        assert payload["user_stream_summary"]["listen_key"] == SENSITIVE_REDACTED_VALUE
        assert payload["user_stream_summary"]["status"] == "connected"
        assert payload["api_key"] == SENSITIVE_REDACTED_VALUE
        assert payload["secret"] == SENSITIVE_REDACTED_VALUE
        assert payload["nested"]["token"] == SENSITIVE_REDACTED_VALUE
        assert payload["nested"]["safe"] == "visible"


def test_audit_timeline_projects_active_position_suppression_without_fingerprint(db_session) -> None:
    from trading_mvp.models import AgentRun

    decision_run = AgentRun(
        role="trading_decision",
        trigger_event="realtime_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="openai",
        summary="btc management-only review",
        input_payload={},
        output_payload={
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "decision": "hold",
        },
        metadata_json={
            "source": "llm",
            "active_position_prompt_route_context": {
                "management_only_open_position_route": True,
                "entry_proposal_suppression_active": True,
                "entry_proposal_suppressed_reason_code": "DETERMINISTIC_BASELINE_DISAGREEMENT",
                "allow_same_side_add_on": True,
                "allowed_add_on_side": "long",
            },
            "active_position_entry_fingerprint_basis": {
                "position_state_bucket": "winning_protected",
                "regime_summary": {"primary_regime": "bullish"},
            },
        },
        schema_valid=True,
    )
    db_session.add(decision_run)
    db_session.flush()
    db_session.add(
        AuditEvent(
            event_type="decision_ai_skipped",
            entity_type="decision_run",
            entity_id=str(decision_run.id),
            severity="info",
            message="AI inference skipped.",
            payload={
                "symbol": "BTCUSDT",
                "ai_skipped_reason": "TRIGGER_DEDUPED",
            },
        )
    )
    db_session.commit()

    rows = get_audit_timeline(db_session, event_type="decision_ai_skipped", limit=1)

    assert len(rows) == 1
    row = rows[0]
    assert row["event_category"] == "ai_decision"
    assert row["suppression_active"] is True
    assert row["suppression_reason_code"] == "DETERMINISTIC_BASELINE_DISAGREEMENT"
    assert row["allow_same_side_add_on"] is True
    assert row["allowed_add_on_side"] == "long"
    assert "active_position_entry_fingerprint_basis" not in row
    assert row["payload"]["suppression_active"] is True
    assert row["payload"]["suppression_reason_code"] == "DETERMINISTIC_BASELINE_DISAGREEMENT"
    assert row["payload"]["allow_same_side_add_on"] is True
    assert row["payload"]["allowed_add_on_side"] == "long"
    assert "active_position_entry_fingerprint_basis" not in row["payload"]


def test_audit_timeline_prefers_payload_suppression_projection_over_join_fallback(db_session) -> None:
    from trading_mvp.models import AgentRun

    decision_run = AgentRun(
        role="trading_decision",
        trigger_event="realtime_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="openai",
        summary="btc management-only review",
        input_payload={},
        output_payload={
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "decision": "hold",
        },
        metadata_json={
            "source": "llm",
            "active_position_prompt_route_context": {
                "management_only_open_position_route": True,
                "entry_proposal_suppression_active": True,
                "entry_proposal_suppressed_reason_code": "LARGEST_POSITION_LIMIT_REACHED",
                "allow_same_side_add_on": True,
                "allowed_add_on_side": "long",
            },
        },
        schema_valid=True,
    )
    db_session.add(decision_run)
    db_session.flush()
    db_session.add(
        AuditEvent(
            event_type="agent_output",
            entity_type="agent_run",
            entity_id=str(decision_run.id),
            severity="info",
            message="Trading decision generated.",
            payload={
                "symbol": "BTCUSDT",
                "suppression_active": False,
                "suppression_reason_code": None,
                "allow_same_side_add_on": False,
                "allowed_add_on_side": None,
            },
        )
    )
    db_session.commit()

    rows = get_audit_timeline(db_session, event_type="agent_output", limit=1)

    assert len(rows) == 1
    row = rows[0]
    assert row["suppression_active"] is False
    assert row["suppression_reason_code"] is None
    assert row["allow_same_side_add_on"] is False
    assert row["allowed_add_on_side"] is None
    assert row["payload"]["suppression_active"] is False
    assert row["payload"]["suppression_reason_code"] is None
    assert row["payload"]["allow_same_side_add_on"] is False
    assert row["payload"]["allowed_add_on_side"] is None


def test_audit_event_categories_are_deterministic() -> None:
    assert classify_audit_event("risk_check", "risk_check", {}) == "risk"
    assert classify_audit_event("live_limit_partial_fill", "order", {}) == "execution"
    assert classify_audit_event("trading_paused", "settings", {}) == "approval_control"
    assert classify_audit_event("protection_recreate_attempted", "position", {}) == "protection"
    assert classify_audit_event("live_sync_failed", "binance", {}) == "health_system"
    assert classify_audit_event("agent_output", "agent_run", {}) == "ai_decision"


def test_overview_and_positions_include_protection_status(db_session) -> None:
    settings = get_or_create_settings(db_session)
    settings.trading_paused = True
    settings.pause_reason_code = "PROTECTIVE_ORDER_FAILURE"
    settings.pause_origin = "system"
    settings.pause_reason_detail = {
        "detail": "protective verification failed",
        "auto_resume": {"status": "not_eligible", "blockers": ["MISSING_PROTECTIVE_ORDERS"]},
    }
    now = utcnow_naive()
    mark_sync_success(settings, scope="account", synced_at=now)
    mark_sync_success(settings, scope="positions", synced_at=now)
    mark_sync_success(settings, scope="open_orders", synced_at=now - timedelta(hours=1), stale_after_seconds=60)
    mark_sync_success(settings, scope="protective_orders", synced_at=now, detail={"status": "synced"})
    db_session.flush()

    position = Position(
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
        realized_pnl=0.0,
        unrealized_pnl=1.0,
        metadata_json={},
    )
    db_session.add(position)
    db_session.flush()

    db_session.add(
        Order(
            symbol="BTCUSDT",
            position_id=position.id,
            side="sell",
            order_type="stop_market",
            mode="live",
            status="pending",
            external_order_id="protect-stop-1",
            reduce_only=True,
            close_only=True,
            requested_quantity=0.01,
            requested_price=69000.0,
            filled_quantity=0.0,
            average_fill_price=0.0,
            metadata_json={},
        )
    )
    db_session.add(
        RiskCheck(
            symbol="BTCUSDT",
            decision="long",
            allowed=False,
            reason_codes=["TRADING_PAUSED", "LIVE_APPROVAL_REQUIRED"],
            approved_risk_pct=0.0,
            approved_leverage=0.0,
            payload={"reason_codes": ["TRADING_PAUSED", "LIVE_APPROVAL_REQUIRED"]},
        )
    )
    db_session.flush()

    overview = get_overview(db_session)
    positions = get_positions(db_session)

    assert overview.open_positions == 1
    assert overview.unprotected_positions == 1
    assert overview.operating_state == "PAUSED"
    assert overview.trading_paused is True
    assert overview.operational_status.trading_paused is True
    assert overview.operational_status.operating_state == overview.operating_state
    assert overview.pause_reason_code == "PROTECTIVE_ORDER_FAILURE"
    assert overview.pause_origin == "system"
    assert overview.guard_mode_reason_category == "pause"
    assert overview.guard_mode_reason_code == "PROTECTIVE_ORDER_FAILURE"
    assert overview.guard_mode_reason_message == "보호 주문 복구 실패로 가드 모드입니다."
    assert overview.auto_resume_status == "not_eligible"
    assert overview.auto_resume_last_blockers == ["MISSING_PROTECTIVE_ORDERS"]
    assert overview.latest_blocked_reasons == ["TRADING_PAUSED", "LIVE_APPROVAL_REQUIRED"]
    assert overview.pause_severity == "critical"
    assert overview.pause_recovery_class == "portfolio_unsafe"
    assert overview.protection_recovery_status == "idle"
    assert overview.protection_recovery_active is False
    assert overview.missing_protection_symbols == ["BTCUSDT"]
    assert overview.missing_protection_items == {"BTCUSDT": ["take_profit"]}
    assert overview.pnl_summary["basis"] == "live_account_snapshot_unavailable"
    assert "status" in overview.account_sync_summary
    assert overview.operational_status.account_sync_summary["status"] == overview.account_sync_summary["status"]
    assert overview.sync_freshness_summary["account"]["stale"] is False
    assert overview.sync_freshness_summary["open_orders"]["stale"] is True
    assert overview.sync_freshness_summary["protective_orders"]["last_sync_at"] is not None
    assert overview.operational_status.sync_freshness_summary["open_orders"]["stale"] is True
    assert "headroom" in overview.exposure_summary
    assert "entry" in overview.execution_policy_summary
    assert "primary_regime" in overview.market_context_summary
    assert "mode" in overview.adaptive_protection_summary
    assert "status" in overview.adaptive_signal_summary
    assert overview.position_protection_summary[0]["symbol"] == "BTCUSDT"
    assert overview.position_protection_summary[0]["missing_components"] == ["take_profit"]
    assert overview.position_protection_summary[0]["status"] == "missing"
    assert positions[0]["status"] == "open"
    assert positions[0]["protection_status"] == "missing"
    assert positions[0]["protected"] is False
    assert positions[0]["protective_order_count"] == 1
    assert positions[0]["missing_components"] == ["take_profit"]


def test_operator_dashboard_exposes_sync_freshness_summary(db_session) -> None:
    settings = get_or_create_settings(db_session)
    now = utcnow_naive()
    mark_sync_success(settings, scope="account", synced_at=now)
    mark_sync_success(settings, scope="positions", synced_at=now)
    mark_sync_success(settings, scope="open_orders", synced_at=now)
    mark_sync_success(
        settings,
        scope="protective_orders",
        synced_at=now - timedelta(minutes=10),
        stale_after_seconds=60,
    )
    db_session.flush()

    payload = get_operator_dashboard(db_session)

    assert payload.control.operational_status.can_enter_new_position is False
    assert payload.control.sync_freshness_summary["account"]["stale"] is False
    assert payload.control.sync_freshness_summary["protective_orders"]["stale"] is True
    assert payload.control.can_enter_new_position is False


def test_operator_dashboard_exposes_scheduler_freshness_summary(db_session) -> None:
    now = utcnow_naive()
    db_session.add(
        SchedulerRun(
            schedule_window="60s",
            workflow="exchange_sync_cycle",
            status="success",
            triggered_by="scheduler",
            created_at=now - timedelta(minutes=20),
            next_run_at=now - timedelta(minutes=19),
            outcome={"status": "ok"},
        )
    )
    db_session.flush()

    payload = get_operator_dashboard(db_session, view="scheduler")
    summary = payload.control.scheduler_freshness_summary

    assert summary["status"] == "stale"
    assert summary["stale"] is True
    assert summary["reason_code"] == "SCHEDULER_NEXT_RUN_MISSED"
    assert summary["source"] == "scheduler_runs"
    assert payload.control.scheduler_last_run_at is not None


def test_operator_dashboard_exchange_sync_diagnostics_recovered_after_permission_failure(db_session) -> None:
    settings = get_or_create_settings(db_session)
    now = utcnow_naive()
    for scope in ("account", "positions", "open_orders", "protective_orders"):
        mark_sync_success(settings, scope=scope, synced_at=now)
    db_session.add_all(
        [
            SchedulerRun(
                schedule_window="30s",
                workflow="exchange_sync_cycle",
                status="failed",
                triggered_by="scheduler",
                created_at=now - timedelta(hours=2),
                outcome={
                    "status": "error",
                    "error": "Binance error -2015: Invalid API-key, IP, or permissions for action.",
                },
            ),
            SchedulerRun(
                schedule_window="30s",
                workflow="exchange_sync_cycle",
                status="success",
                triggered_by="scheduler",
                created_at=now - timedelta(hours=1),
                outcome={"status": "ok"},
            ),
        ]
    )
    db_session.flush()

    payload = get_operator_dashboard(db_session)
    diagnostics = payload.control.exchange_sync_diagnostics

    assert diagnostics["status"] == "recovered"
    assert diagnostics["current_block_state"] == "recovered"
    assert diagnostics["currently_blocking_new_entries"] is False
    assert diagnostics["permission_failure_count_24h"] == 1
    assert diagnostics["failure_count_24h"] == 1
    assert diagnostics["success_count_24h"] == 1
    assert diagnostics["latest_failure_reason_code"] == "EXCHANGE_AUTH_PERMISSION_REJECTED"
    assert diagnostics["latest_failure_at"] is not None
    assert diagnostics["latest_success_at"] is not None
    assert (
        payload.control.control_status_summary.exchange_sync_diagnostics["current_block_state"]
        == "recovered"
    )


def test_operator_dashboard_exchange_sync_diagnostics_currently_permission_blocked(db_session) -> None:
    settings = get_or_create_settings(db_session)
    now = utcnow_naive()
    mark_sync_success(settings, scope="account", synced_at=now)
    mark_sync_success(settings, scope="positions", synced_at=now)
    mark_sync_success(settings, scope="protective_orders", synced_at=now)
    mark_sync_issue(
        settings,
        scope="open_orders",
        status="failed",
        reason_code="EXCHANGE_AUTH_PERMISSION_REJECTED",
        observed_at=now,
    )
    db_session.add(
        SchedulerRun(
            schedule_window="30s",
            workflow="exchange_sync_cycle",
            status="failed",
            triggered_by="scheduler",
            created_at=now,
            outcome={
                "status": "error",
                "reason_code": "EXCHANGE_AUTH_PERMISSION_REJECTED",
                "error": "EXCHANGE_AUTH_PERMISSION_REJECTED",
            },
        )
    )
    db_session.flush()

    payload = get_operator_dashboard(db_session)
    diagnostics = payload.control.exchange_sync_diagnostics

    assert diagnostics["status"] == "blocked"
    assert diagnostics["current_block_state"] == "currently_blocked"
    assert diagnostics["currently_blocking_new_entries"] is True
    assert diagnostics["currently_permission_blocked"] is True
    assert diagnostics["active_reason_codes"] == ["EXCHANGE_AUTH_PERMISSION_REJECTED"]
    assert diagnostics["permission_failure_count_24h"] == 1


def test_operator_pending_plan_snapshot_uses_utc_naive_remaining_ttl(db_session) -> None:
    settings = get_or_create_settings(db_session)
    settings.tracked_symbols = ["BTCUSDT"]
    now = utcnow_naive()
    plan = PendingEntryPlan(
        symbol="BTCUSDT",
        side="short",
        plan_status="armed",
        source_decision_run_id=173,
        source_timeframe="15m",
        entry_mode="pullback_confirm",
        entry_zone_min=80505.6252,
        entry_zone_max=80344.7748,
        invalidation_price=80651.8214,
        max_chase_bps=4.0,
        idea_ttl_minutes=120,
        stop_loss=80651.8214,
        take_profit=80017.28148,
        risk_pct_cap=0.02,
        leverage_cap=3.0,
        expires_at=now + timedelta(minutes=90),
        idempotency_key="pending-plan:BTCUSDT:short:173:test",
        metadata_json={},
    )
    db_session.add_all([settings, plan])
    db_session.flush()

    payload = get_operator_dashboard(db_session)
    symbol = next(item for item in payload.symbols if item.symbol == "BTCUSDT")
    pending_plan = symbol.pending_entry_plan

    assert pending_plan is not None
    assert pending_plan.plan_id == plan.id
    assert pending_plan.expires_at_time_basis == "app_utc_naive"
    assert pending_plan.app_utc_now is not None
    assert pending_plan.remaining_ttl_seconds is not None
    assert 0 < pending_plan.remaining_ttl_seconds <= 90 * 60
    assert pending_plan.expired_by_app_utc_now is False
    assert db_session.get(PendingEntryPlan, plan.id).plan_status == "armed"


def test_overview_and_operator_expose_stream_reconcile_and_candidate_selection_summaries(db_session) -> None:
    settings = get_or_create_settings(db_session)
    now = utcnow_naive()
    set_user_stream_detail(
        settings,
        status="connected",
        source="binance_futures_user_stream",
        listen_key="listen-key-1",
        listen_key_created_at=now,
        listen_key_refreshed_at=now,
        last_event_at=now,
        last_event_type="ACCOUNT_UPDATE",
        heartbeat_ok=True,
        stream_source="user_stream",
    )
    set_reconciliation_detail(
        settings,
        status="completed",
        source="rest_polling_reconciliation",
        last_reconciled_at=now,
        last_success_at=now,
        last_symbol="BTCUSDT",
        stream_fallback_active=False,
        reconcile_source="rest_polling",
    )
    set_candidate_selection_detail(
        settings,
        generated_at=now,
        mode="correlation_aware_top_n",
        max_selected=2,
        selected_symbols=["BTCUSDT", "ETHUSDT"],
        skipped_symbols=["BNBUSDT"],
        rankings=[
            {"symbol": "BTCUSDT", "selected": True},
            {"symbol": "ETHUSDT", "selected": True},
            {"symbol": "BNBUSDT", "selected": False, "selection_reason": "correlation_limit"},
        ],
    )
    db_session.add(settings)
    db_session.flush()

    overview = get_overview(db_session)
    payload = get_operator_dashboard(db_session)

    assert overview.user_stream_summary["status"] == "connected"
    assert overview.user_stream_summary["stream_source"] == "user_stream"
    assert overview.reconciliation_summary["status"] == "completed"
    assert overview.candidate_selection_summary["mode"] == "correlation_aware_top_n"
    assert overview.candidate_selection_summary["selected_symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert payload.control.user_stream_summary["last_event_type"] == "ACCOUNT_UPDATE"
    assert payload.control.reconciliation_summary["last_symbol"] == "BTCUSDT"
    assert payload.control.candidate_selection_summary["rankings"][2]["selection_reason"] == "correlation_limit"


def test_positions_hide_closed_rows_and_do_not_mark_them_missing(db_session) -> None:
    closed_position = Position(
        symbol="BTCUSDT",
        mode="live",
        side="long",
        status="closed",
        quantity=0.0,
        entry_price=70000.0,
        mark_price=70050.0,
        leverage=2.0,
        stop_loss=69000.0,
        take_profit=72000.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        metadata_json={},
    )
    db_session.add(closed_position)
    db_session.flush()

    positions = get_positions(db_session)
    overview = get_overview(db_session)

    assert positions == []
    assert overview.open_positions == 0
    assert overview.position_protection_summary == []


def test_profitability_dashboard_groups_performance_execution_and_blocked_context(db_session) -> None:
    _seed_profitability_dashboard_rows(db_session)

    payload = get_profitability_dashboard(db_session)

    assert [item.window_label for item in payload.windows] == ["24h", "7d", "30d"]
    assert [item.window_label for item in payload.cost_breakdowns] == ["today", "7d", "30d", "all_time"]
    assert payload.windows[0].rationale_winners
    assert payload.windows[0].top_regimes
    assert payload.windows[0].top_symbols
    cost = payload.windows[0].cost_breakdown
    assert cost.gross_pnl == pytest.approx(8.0, abs=1e-9)
    assert cost.realized_pnl == pytest.approx(8.0, abs=1e-9)
    assert cost.fee == pytest.approx(0.4, abs=1e-9)
    assert cost.funding == pytest.approx(-0.25, abs=1e-9)
    assert cost.net_pnl_excluding_funding == pytest.approx(7.6, abs=1e-9)
    assert cost.net_pnl_including_funding == pytest.approx(7.35, abs=1e-9)
    assert cost.signed_slippage_bps_avg > 0.0
    assert cost.adverse_slippage_bps_avg > 0.0
    assert cost.passive_entry_count == 3
    assert cost.passive_entry_ratio == pytest.approx(1.0, abs=1e-9)
    assert payload.windows[0].entry_quality["entry_passive_limit"].trade_count == 1
    assert payload.windows[0].entry_quality["entry_passive_limit"].net_pnl == pytest.approx(7.35, abs=1e-9)
    assert payload.windows[0].entry_quality["entry_marketable"].trade_count == 0
    assert payload.execution_windows
    assert payload.execution_windows[0].worst_profiles
    assert payload.execution_windows[0].execution_quality_summary["cancel_attempts"] == 2
    assert payload.execution_windows[0].execution_quality_summary["cancel_successes"] == 1
    assert payload.execution_windows[0].execution_quality_summary["cancel_success_rate"] == 0.5
    assert payload.execution_windows[0].execution_quality_summary["average_arrival_slippage_pct"] > 0.0
    assert payload.execution_windows[0].execution_quality_summary["average_first_fill_latency_seconds"] > 0.0
    assert payload.execution_windows[0].worst_profiles[0].cancel_attempts == 2
    assert payload.execution_windows[0].worst_profiles[0].cancel_success_rate == 0.5
    assert payload.hold_blocked_summary.latest_blocked_reasons == ["TRADING_PAUSED", "HOLD_DECISION"]
    assert payload.adaptive_signal_summary["status"] in {"active", "neutral", "insufficient_data", "disabled"}
    assert payload.latest_decision is not None
    assert payload.latest_risk is not None


def test_profitability_dashboard_cost_breakdown_flags_cost_leakage(db_session) -> None:
    _seed_profitability_dashboard_rows(db_session)
    for execution in db_session.query(Execution).all():
        execution.fee_paid = 5.0
    for order in db_session.query(Order).filter(Order.reduce_only.is_(False), Order.close_only.is_(False)).all():
        order.order_type = "market"
        metadata = dict(order.metadata_json) if isinstance(order.metadata_json, dict) else {}
        execution_policy = dict(metadata.get("execution_policy") or {})
        execution_policy["execution_style"] = "marketable"
        metadata["execution_policy"] = execution_policy
        order.metadata_json = metadata
    db_session.flush()

    payload = get_profitability_dashboard(db_session)

    cost = payload.windows[0].cost_breakdown
    assert cost.marketable_entry_ratio == pytest.approx(1.0, abs=1e-9)
    assert {
        "fee_exceeds_gross_pnl",
        "cost_exceeds_gross_pnl",
        "positive_gross_negative_net",
        "adverse_slippage_positive",
        "high_marketable_ratio_low_net_pnl",
    }.issubset(set(cost.warning_codes))


def test_profitability_dashboard_reports_latest_pnl_snapshot_and_fact_gate_diagnostic(db_session) -> None:
    now = utcnow_naive().replace(second=0, microsecond=0)
    db_session.add_all(
        [
            PnLSnapshot(
                snapshot_date=(now - timedelta(days=1)).date(),
                equity=100000.0,
                cash_balance=100000.0,
                gross_realized_pnl=1.0,
                fee_total=0.1,
                funding_total=0.0,
                net_pnl=0.9,
                realized_pnl=1.0,
                daily_pnl=0.9,
                cumulative_pnl=0.9,
                created_at=now - timedelta(days=1),
            ),
            PnLSnapshot(
                snapshot_date=now.date(),
                equity=100008.0,
                cash_balance=100008.0,
                gross_realized_pnl=12.0,
                fee_total=3.0,
                funding_total=-1.0,
                net_pnl=8.0,
                realized_pnl=12.0,
                daily_pnl=8.0,
                cumulative_pnl=8.0,
                created_at=now,
            ),
        ]
    )
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
                decision_run_id=9001,
                provider_name="openai",
                symbol="BTCUSDT",
                timeframe="15m",
                decision="long",
                rationale_codes=["EXPECTED_EDGE_MARGIN_TOO_THIN"],
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

    payload = get_profitability_dashboard(db_session)

    snapshot = payload.latest_pnl_snapshot_breakdown
    assert snapshot.status == "ok"
    assert snapshot.gross_pnl == pytest.approx(12.0, abs=1e-9)
    assert snapshot.fee == pytest.approx(3.0, abs=1e-9)
    assert snapshot.funding == pytest.approx(-1.0, abs=1e-9)
    assert snapshot.net_pnl_excluding_funding == pytest.approx(9.0, abs=1e-9)
    assert snapshot.net_pnl_including_funding == pytest.approx(8.0, abs=1e-9)

    diagnostic = payload.candidate_gate_diagnostic
    assert diagnostic.recommendation == "diagnose_candidate_quality_and_cost_before_entry_relaxation"
    assert diagnostic.diagnostic_order[0] == "net_after_fees"
    assert diagnostic.source_counts["decision_performance_fact"] == 1
    assert diagnostic.evaluated_candidates == 1
    assert diagnostic.net_positive_blocked_or_canceled_candidates == 1
    missed_reason_codes = {item["reason_code"] for item in diagnostic.missed_opportunity_reason_codes}
    assert "EXPECTED_EDGE_MARGIN_TOO_THIN" in missed_reason_codes


def test_profitability_dashboard_cache_returns_snapshot_when_enabled(db_session) -> None:
    _seed_profitability_dashboard_rows(db_session)

    cached = get_profitability_dashboard(db_session, use_cache=True, allow_stale=False)
    for order in db_session.query(Order).filter(Order.reduce_only.is_(False), Order.close_only.is_(False)).all():
        order.order_type = "market"
        metadata = dict(order.metadata_json) if isinstance(order.metadata_json, dict) else {}
        execution_policy = dict(metadata.get("execution_policy") or {})
        execution_policy["execution_style"] = "marketable"
        metadata["execution_policy"] = execution_policy
        order.metadata_json = metadata
    db_session.flush()

    cached_again = get_profitability_dashboard(db_session, use_cache=True, allow_stale=False)
    fresh = get_profitability_dashboard(db_session)

    assert cached_again.windows[0].cost_breakdown.passive_entry_ratio == cached.windows[0].cost_breakdown.passive_entry_ratio
    assert cached_again.windows[0].cost_breakdown.marketable_entry_ratio == cached.windows[0].cost_breakdown.marketable_entry_ratio
    assert fresh.windows[0].cost_breakdown.marketable_entry_ratio == pytest.approx(1.0, abs=1e-9)


def test_operator_dashboard_groups_global_control_and_symbol_summaries(db_session) -> None:
    _seed_multi_symbol_operator_rows(db_session)

    overview = get_overview(db_session)
    payload = get_operator_dashboard(db_session)

    assert overview.last_decision_at is not None
    assert overview.last_decision_snapshot_at is not None
    assert overview.last_market_refresh_at is not None
    assert overview.last_market_refresh_at > overview.last_decision_snapshot_at
    assert overview.last_decision_reference.display_gap is True
    assert overview.last_decision_reference.display_gap_reason is not None
    assert overview.operational_status.rollout_mode == "limited_live"
    assert overview.operational_status.limited_live_max_notional == 600.0
    assert payload.control.default_symbol == "BTCUSDT"
    assert payload.control.tracked_symbol_count == 2
    assert payload.control.tracked_symbols == ["BTCUSDT", "ETHUSDT"]
    assert payload.control.rollout_mode == "limited_live"
    assert payload.control.exchange_submit_allowed is True
    assert payload.control.limited_live_max_notional == 600.0
    assert payload.control.operational_status.live_execution_ready == payload.control.live_execution_ready
    assert payload.control.last_decision_at is not None
    assert payload.control.last_decision_snapshot_at is not None
    assert payload.control.last_market_refresh_at is not None
    assert payload.control.last_market_refresh_at > payload.control.last_decision_snapshot_at
    assert payload.control.last_decision_reference.display_gap is True
    assert payload.control.last_decision_reference.display_gap_reason is not None
    assert "wallet_balance" in payload.control.pnl_summary
    assert "available_balance" in payload.control.pnl_summary
    assert "fee_total" in payload.control.pnl_summary
    assert "funding_total" in payload.control.pnl_summary
    assert payload.control.pnl_summary["account_snapshot_available"] is False
    assert payload.control.account_sync_summary["account_snapshot_available"] is False
    assert payload.control.control_status_summary.approval_state == "armed"
    assert payload.control.control_status_summary.approval_window_open is True
    assert payload.control.control_status_summary.rollout_mode == "limited_live"
    assert payload.control.control_status_summary.limited_live_max_notional == 600.0

    btc = next(item for item in payload.symbols if item.symbol == "BTCUSDT")
    eth = next(item for item in payload.symbols if item.symbol == "ETHUSDT")

    assert btc.latest_price == 70500.0
    assert btc.ai_decision.decision == "long"
    assert btc.ai_decision.raw_output == {}
    assert btc.ai_decision.last_ai_trigger_reason == "entry_candidate_event"
    assert btc.ai_decision.ai_review_type == "entry_candidate_review"
    assert btc.ai_decision.ai_trigger_reason_codes == ["ENTRY_CANDIDATE_SELECTED"]
    assert btc.ai_decision.trigger_deduped is True
    assert btc.ai_decision.last_ai_skip_reason == "TRIGGER_DEDUPED"
    assert btc.ai_decision.ai_skip_reason == "TRIGGER_DEDUPED"
    assert btc.ai_decision.ai_review.trigger_reason == "entry_candidate_event"
    assert btc.ai_decision.ai_review.trigger_reason_codes == ["ENTRY_CANDIDATE_SELECTED"]
    assert btc.ai_decision.ai_review.dedupe_reason == "TRIGGER_DEDUPED"
    assert btc.ai_decision.ai_review.provider_invoked is True
    assert btc.ai_decision.ai_review.provider_skipped is True
    assert btc.ai_decision.ai_review.provider_status == "deduped"
    assert btc.ai_decision.market_signal_summary is not None
    assert btc.ai_decision.ai_trigger_summary == btc.ai_decision.market_signal_summary
    assert btc.ai_decision.market_signal_context.summary == btc.ai_decision.market_signal_summary
    assert btc.ai_decision.market_signal_context.momentum_score == 0.92
    assert btc.ai_decision.market_signal_context.volume_ratio == 1.33
    assert btc.ai_decision.market_signal_context.trend_alignment == "bullish_aligned"
    assert btc.ai_decision.market_signal_context.breakout_direction == "up"
    assert btc.ai_decision.suppression_active is True
    assert btc.ai_decision.suppression_reason_code == "LARGEST_POSITION_LIMIT_REACHED"
    assert btc.ai_decision.allow_same_side_add_on is False
    assert btc.ai_decision.allowed_add_on_side is None
    assert btc.ai_decision.next_ai_review_due_at is None
    assert btc.ai_decision.assigned_slot == "slot_1"
    assert btc.ai_decision.candidate_weight == 0.64
    assert btc.ai_decision.capacity_reason == "mixed_breadth_moderate_capacity"
    assert "active_position_entry_fingerprint_basis" not in btc.ai_decision.model_dump(mode="json")
    assert btc.risk_guard.allowed is False
    assert btc.risk_guard.raw_payload == {}
    assert btc.risk_guard.assigned_slot == "slot_1"
    assert btc.risk_guard.candidate_weight == 0.64
    assert btc.risk_guard.capacity_reason == "mixed_breadth_moderate_capacity"
    assert btc.risk_guard.holding_profile == "scalp"
    assert btc.risk_guard.holding_profile_reason == "scalp_default_intraday_bias"
    assert btc.risk_guard.portfolio_slot_soft_cap_applied is False
    assert btc.risk_guard_result.allowed is False
    assert btc.risk_guard_result.blocked_reason_codes == ["POSITION_STATE_STALE"]
    assert btc.risk_guard_result.approved_risk_pct == 0.0
    assert btc.risk_guard_result.approved_leverage == 0.0
    assert btc.risk_guard_result.hold_decision is False
    assert btc.blocked_reasons == ["POSITION_STATE_STALE"]
    assert btc.candidate_selection.assigned_slot == "slot_1"
    assert btc.candidate_selection.candidate_weight == 0.64
    assert btc.candidate_selection.capacity_reason == "mixed_breadth_moderate_capacity"
    assert btc.candidate_selection.blocked_reason_codes == ["POSITION_STATE_STALE"]
    assert btc.candidate_selection.portfolio_slot_soft_cap_applied is False
    assert btc.open_position.is_open is True
    assert btc.open_position.holding_profile == "swing"
    assert btc.open_position.holding_profile_reason == "swing_intraday_trend_extension_allowed"
    assert btc.open_position.initial_stop_type == "deterministic_hard_stop"
    assert btc.open_position.hard_stop_active is True
    assert btc.open_position.ai_stop_management_allowed is True
    assert btc.open_position.stop_widening_allowed is False
    assert btc.open_position.position_exit_review is not None
    assert btc.open_position.position_exit_review.recommendation == "hold_runner"
    assert btc.open_position.position_exit_review.execution_boundary == "metadata_only_no_order_authority"
    assert btc.protection_status.status == "missing"
    assert btc.protection_status.recovery_status == "recovery_pending"
    assert btc.protection_status.auto_recovery_active is True
    assert btc.protection_status.failure_count == 2
    assert btc.protection_status.trigger_source == "protection_verify"
    assert btc.protection_status.verification_status == "verify_failed"
    assert btc.protection_status.last_event_type == "protection_verification_failed"
    assert btc.protection_status.last_error == "Protective orders were not verified on the exchange."
    assert btc.execution.order_id is not None
    assert btc.execution.symbol == "BTCUSDT"
    assert any(event.entity_id == "BTCUSDT" for event in btc.audit_events)
    assert any(
        event.event_type == "protection_verification_failed"
        and event.payload.get("recovery_status") == "recovery_pending"
        and event.payload.get("verification_detail", {}).get("error") == "Protective orders were not verified on the exchange."
        for event in btc.audit_events
    )

    assert eth.latest_price == 3400.0
    assert eth.ai_decision.decision == "long"
    assert eth.ai_decision.raw_output == {}
    assert eth.ai_decision.last_ai_trigger_reason == "entry_candidate_event"
    assert eth.ai_decision.ai_review_type == "entry_candidate_review"
    assert eth.ai_decision.ai_trigger_reason_codes == ["ENTRY_CANDIDATE_SELECTED"]
    assert eth.ai_decision.ai_review.provider_invoked is True
    assert eth.ai_decision.ai_review.provider_skipped is False
    assert eth.ai_decision.ai_review.provider_status == "invoked"
    assert eth.ai_decision.last_ai_invoked_at is not None
    assert eth.ai_decision.suppression_active is False
    assert eth.ai_decision.suppression_reason_code is None
    assert eth.ai_decision.allow_same_side_add_on is False
    assert eth.ai_decision.allowed_add_on_side is None
    assert eth.ai_decision.next_ai_review_due_at is None
    assert eth.ai_decision.assigned_slot == "slot_2"
    assert eth.ai_decision.candidate_weight == 0.42
    assert eth.ai_decision.capacity_reason == "mixed_breadth_moderate_capacity"
    assert eth.ai_decision.holding_profile == "swing"
    assert eth.ai_decision.holding_profile_reason == "swing_intraday_trend_extension_allowed"
    assert eth.ai_decision.portfolio_slot_soft_cap_applied is True
    assert eth.risk_guard.allowed is True
    assert eth.risk_guard.raw_payload == {}
    assert eth.risk_guard.auto_resized_entry is True
    assert eth.risk_guard.approved_projected_notional == 150000.0
    assert eth.risk_guard.approved_quantity == 44.117647
    assert eth.risk_guard.auto_resize_reason == "CLAMPED_TO_SINGLE_POSITION_HEADROOM"
    assert eth.risk_guard.assigned_slot == "slot_2"
    assert eth.risk_guard.candidate_weight == 0.42
    assert eth.risk_guard.capacity_reason == "mixed_breadth_moderate_capacity"
    assert eth.risk_guard.holding_profile == "swing"
    assert eth.risk_guard.holding_profile_reason == "swing_intraday_trend_extension_allowed"
    assert eth.risk_guard.portfolio_slot_soft_cap_applied is True
    assert eth.risk_guard_result.allowed is True
    assert eth.risk_guard_result.blocked_reason_codes == []
    assert eth.risk_guard_result.approved_risk_pct == 0.01
    assert eth.risk_guard_result.approved_leverage == 2.0
    assert eth.risk_guard_result.hold_decision is False
    assert eth.blocked_reasons == []
    assert eth.candidate_selection.assigned_slot == "slot_2"
    assert eth.candidate_selection.candidate_weight == 0.42
    assert eth.candidate_selection.capacity_reason == "mixed_breadth_moderate_capacity"
    assert eth.candidate_selection.portfolio_slot_soft_cap_applied is True
    assert eth.open_position.is_open is False
    assert eth.protection_status.status == "flat"
    assert eth.stale_flags == []
    assert eth.ai_decision.decision_reference.market_snapshot_at is not None
    assert eth.ai_decision.decision_reference.display_gap is True
    assert eth.ai_decision.decision_reference.display_gap_reason is not None
    assert eth.execution.order_status == "filled"
    assert eth.execution.symbol == "ETHUSDT"
    assert len(eth.execution.recent_fills) == 2
    assert eth.execution.recent_fills[0].external_trade_id == "eth-fill-2"
    assert eth.execution.recent_fills[1].external_trade_id == "eth-fill-1"
    assert any(event.entity_id == "ETHUSDT" for event in eth.audit_events)
    assert any(
        event.event_type == "live_execution"
        and event.payload.get("order_status") == "filled"
        and event.payload.get("filled_quantity") == 0.3
        for event in eth.audit_events
    )

    assert payload.market_signal.performance_windows[0].window_label == "24h"
    assert len(payload.market_signal.performance_windows) == 1
    assert len(payload.execution_windows) == 1
    assert payload.audit_events
    assert any(
        event.event_type == "live_approval_armed"
        and event.payload.get("approval_state") == "armed"
        and event.payload.get("approval_window_open") is True
        for event in payload.audit_events
    )


def test_operator_dashboard_exposes_weak_volume_preai_skip_reason(db_session) -> None:
    from trading_mvp.models import AgentRun

    now = utcnow_naive()
    settings = get_or_create_settings(db_session)
    settings.default_symbol = "BNBUSDT"
    settings.tracked_symbols = ["BNBUSDT"]
    db_session.add(settings)
    db_session.flush()

    db_session.add(
        AgentRun(
            role="trading_decision",
            trigger_event="realtime_cycle",
            schema_name="TradeDecision",
            status="completed",
            provider_name="deterministic",
            summary="bnb weak volume pre-ai skip",
            input_payload={
                "ai_trigger": {
                    "trigger_reason": "entry_candidate_event",
                    "reason_codes": ["ENTRY_CANDIDATE_SELECTED"],
                    "trigger_fingerprint": "bnb-trigger-seed",
                },
                "features": {
                    "trend_score": 0.32,
                    "momentum_score": 0.18,
                    "volume_ratio": 0.07,
                    "regime": {
                        "primary_regime": "bullish",
                        "trend_alignment": "bullish_aligned",
                        "volume_regime": "weak",
                        "weak_volume": True,
                    },
                },
            },
            output_payload={
                "symbol": "BNBUSDT",
                "timeframe": "15m",
                "decision": "hold",
                "confidence": 0.5,
                "rationale_codes": ["ENTRY_CANDIDATE_WEAK_VOLUME_PREAI"],
                "explanation_short": "Weak volume pre-AI skip",
            },
            metadata_json={
                "source": "deterministic",
                "ai_trigger": {
                    "trigger_reason": "entry_candidate_event",
                    "reason_codes": ["ENTRY_CANDIDATE_SELECTED"],
                    "trigger_fingerprint": "bnb-trigger-seed",
                },
                "last_ai_trigger_reason": "entry_candidate_event",
                "last_ai_skip_reason": "ENTRY_CANDIDATE_WEAK_VOLUME_PREAI",
                "pre_ai_skip_reason": "ENTRY_CANDIDATE_WEAK_VOLUME_PREAI",
                "trigger_fingerprint": "bnb-trigger-seed",
            },
            schema_valid=True,
            created_at=now,
        )
    )
    db_session.commit()

    payload = get_operator_dashboard(db_session)
    bnb = payload.symbols[0]

    assert bnb.ai_decision.last_ai_trigger_reason == "entry_candidate_event"
    assert bnb.ai_decision.ai_review_type == "entry_candidate_review"
    assert bnb.ai_decision.ai_trigger_reason_codes == ["ENTRY_CANDIDATE_SELECTED"]
    assert bnb.ai_decision.last_ai_skip_reason == "ENTRY_CANDIDATE_WEAK_VOLUME_PREAI"
    assert bnb.ai_decision.ai_skip_reason == "ENTRY_CANDIDATE_WEAK_VOLUME_PREAI"
    assert bnb.ai_decision.ai_review.skip_reason == "ENTRY_CANDIDATE_WEAK_VOLUME_PREAI"
    assert bnb.ai_decision.ai_review.provider_invoked is False
    assert bnb.ai_decision.ai_review.provider_skipped is True
    assert bnb.ai_decision.ai_review.provider_status == "skipped_pre_ai"
    assert bnb.ai_decision.market_signal_summary is not None
    assert bnb.ai_decision.ai_trigger_summary == bnb.ai_decision.market_signal_summary
    assert bnb.ai_decision.market_signal_context.weak_volume is True
    assert bnb.ai_decision.market_signal_context.volume_ratio == 0.07


def test_operator_dashboard_distinguishes_ai_invoked_hold_from_preai_skip(db_session) -> None:
    from trading_mvp.models import AgentRun

    now = utcnow_naive()
    settings = get_or_create_settings(db_session)
    settings.default_symbol = "HOLDUSDT"
    settings.tracked_symbols = ["HOLDUSDT", "SKIPUSDT"]
    db_session.add(settings)
    db_session.flush()

    hold_run = AgentRun(
        role="trading_decision",
        trigger_event="realtime_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="openai",
        summary="hold after ai",
        input_payload={
            "ai_trigger": {
                "trigger_reason": "entry_candidate_event",
                "reason_codes": ["ENTRY_CANDIDATE_SELECTED"],
                "trigger_fingerprint": "hold-trigger",
            },
            "market_snapshot": {
                "symbol": "HOLDUSDT",
                "timeframe": "15m",
                "snapshot_time": now.isoformat(),
            },
            "features": {
                "trend_score": 0.32,
                "momentum_score": 0.18,
                "volume_ratio": 1.08,
                "regime": {
                    "primary_regime": "bullish",
                    "trend_alignment": "bullish_aligned",
                    "volume_regime": "normal",
                    "weak_volume": False,
                },
            },
        },
        output_payload={
            "symbol": "HOLDUSDT",
            "timeframe": "15m",
            "decision": "hold",
            "confidence": 0.62,
            "rationale_codes": ["TEST_PROVIDER_CALLED"],
            "explanation_short": "AI reviewed and held",
        },
        metadata_json={
            "source": "llm",
            "ai_trigger": {
                "trigger_reason": "entry_candidate_event",
                "reason_codes": ["ENTRY_CANDIDATE_SELECTED"],
                "trigger_fingerprint": "hold-trigger",
            },
            "last_ai_trigger_reason": "entry_candidate_event",
            "last_ai_invoked_at": now.isoformat(),
            "last_ai_skip_reason": None,
            "trigger_fingerprint": "hold-trigger",
        },
        schema_valid=True,
        created_at=now,
    )
    skip_run = AgentRun(
        role="trading_decision",
        trigger_event="realtime_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="deterministic-mock",
        summary="weak volume pre-ai skip",
        input_payload={
            "ai_trigger": {
                "trigger_reason": "entry_candidate_event",
                "reason_codes": ["ENTRY_CANDIDATE_SELECTED"],
                "trigger_fingerprint": "skip-trigger",
            },
            "market_snapshot": {
                "symbol": "SKIPUSDT",
                "timeframe": "15m",
                "snapshot_time": now.isoformat(),
            },
            "features": {
                "trend_score": 0.32,
                "momentum_score": 0.18,
                "volume_ratio": 0.07,
                "regime": {
                    "primary_regime": "bullish",
                    "trend_alignment": "bullish_aligned",
                    "volume_regime": "weak",
                    "weak_volume": True,
                },
            },
        },
        output_payload={
            "symbol": "SKIPUSDT",
            "timeframe": "15m",
            "decision": "hold",
            "confidence": 0.5,
            "rationale_codes": ["ENTRY_CANDIDATE_WEAK_VOLUME_PREAI"],
            "explanation_short": "Weak volume pre-AI skip",
        },
        metadata_json={
            "source": "deterministic",
            "ai_trigger": {
                "trigger_reason": "entry_candidate_event",
                "reason_codes": ["ENTRY_CANDIDATE_SELECTED"],
                "trigger_fingerprint": "skip-trigger",
            },
            "last_ai_trigger_reason": "entry_candidate_event",
            "last_ai_skip_reason": "ENTRY_CANDIDATE_WEAK_VOLUME_PREAI",
            "pre_ai_skip_reason": "ENTRY_CANDIDATE_WEAK_VOLUME_PREAI",
            "trigger_fingerprint": "skip-trigger",
        },
        schema_valid=True,
        created_at=now - timedelta(seconds=1),
    )
    db_session.add_all([hold_run, skip_run])
    db_session.flush()
    for run in (hold_run, skip_run):
        db_session.add(
            RiskCheck(
                symbol=run.output_payload["symbol"],
                decision_run_id=run.id,
                allowed=False,
                decision="hold",
                reason_codes=["HOLD_DECISION"],
                approved_risk_pct=0.0,
                approved_leverage=0.0,
                payload={
                    "allowed": False,
                    "decision": "hold",
                    "reason_codes": ["HOLD_DECISION"],
                    "blocked_reason_codes": ["HOLD_DECISION"],
                },
            )
        )
    db_session.commit()

    payload = get_operator_dashboard(db_session)
    hold = next(item for item in payload.symbols if item.symbol == "HOLDUSDT")
    skip = next(item for item in payload.symbols if item.symbol == "SKIPUSDT")

    assert hold.ai_decision.decision == "hold"
    assert hold.ai_decision.last_ai_trigger_reason == "entry_candidate_event"
    assert hold.ai_decision.ai_review_type == "entry_candidate_review"
    assert hold.ai_decision.ai_trigger_reason_codes == ["ENTRY_CANDIDATE_SELECTED"]
    assert hold.ai_decision.last_ai_invoked_at is not None
    assert hold.ai_decision.last_ai_skip_reason is None
    assert hold.ai_decision.ai_skip_reason is None
    assert hold.ai_decision.ai_review.provider_invoked is True
    assert hold.ai_decision.ai_review.provider_skipped is False
    assert hold.ai_decision.ai_review.provider_status == "invoked"
    assert hold.risk_guard.decision == "hold"
    assert hold.risk_guard.blocked_reason_codes == ["HOLD_DECISION"]
    assert hold.risk_guard_result.hold_decision is True
    assert hold.ai_decision.market_signal_summary is not None
    assert "거래량" in hold.ai_decision.market_signal_summary
    assert hold.ai_decision.market_signal_context.volume_ratio == 1.08

    assert skip.ai_decision.decision == "hold"
    assert skip.ai_decision.last_ai_trigger_reason == "entry_candidate_event"
    assert skip.ai_decision.ai_review_type == "entry_candidate_review"
    assert skip.ai_decision.ai_trigger_reason_codes == ["ENTRY_CANDIDATE_SELECTED"]
    assert skip.ai_decision.last_ai_invoked_at is None
    assert skip.ai_decision.last_ai_skip_reason == "ENTRY_CANDIDATE_WEAK_VOLUME_PREAI"
    assert skip.ai_decision.ai_skip_reason == "ENTRY_CANDIDATE_WEAK_VOLUME_PREAI"
    assert skip.ai_decision.ai_review.provider_invoked is False
    assert skip.ai_decision.ai_review.provider_skipped is True
    assert skip.ai_decision.ai_review.provider_status == "skipped_pre_ai"
    assert skip.risk_guard.decision == "hold"
    assert skip.risk_guard.blocked_reason_codes == ["HOLD_DECISION"]
    assert skip.risk_guard_result.hold_decision is True
    assert skip.ai_decision.market_signal_summary is not None
    assert "거래량" in skip.ai_decision.market_signal_summary
    assert skip.ai_decision.market_signal_context.weak_volume is True


def test_operator_dashboard_current_interval_preai_skip_overrides_stale_provider_review(db_session) -> None:
    from trading_mvp.models import AgentRun

    now = utcnow_naive()
    old_ai_at = now - timedelta(hours=8)
    settings = get_or_create_settings(db_session)
    settings.default_symbol = "BTCUSDT"
    settings.tracked_symbols = ["BTCUSDT"]
    db_session.add(settings)
    db_session.flush()

    ai_run = AgentRun(
        role="trading_decision",
        trigger_event="realtime_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="openai",
        summary="old ai hold",
        input_payload={
            "ai_trigger": {
                "trigger_reason": "entry_candidate_event",
                "reason_codes": ["ENTRY_CANDIDATE_SELECTED"],
                "trigger_fingerprint": "old-provider-trigger",
            },
            "market_snapshot": {
                "symbol": "BTCUSDT",
                "timeframe": "15m",
                "snapshot_time": old_ai_at.isoformat(),
            },
        },
        output_payload={
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "decision": "hold",
            "confidence": 0.6,
            "rationale_codes": ["TEST_OLD_AI_HOLD"],
            "explanation_short": "Old provider review held",
        },
        metadata_json={
            "source": "llm",
            "ai_trigger": {
                "trigger_reason": "entry_candidate_event",
                "reason_codes": ["ENTRY_CANDIDATE_SELECTED"],
                "trigger_fingerprint": "old-provider-trigger",
            },
            "last_ai_trigger_reason": "entry_candidate_event",
            "last_ai_invoked_at": old_ai_at.isoformat(),
            "trigger_fingerprint": "old-provider-trigger",
        },
        schema_valid=True,
        created_at=old_ai_at,
    )
    db_session.add(ai_run)
    db_session.flush()
    db_session.add(
        RiskCheck(
            symbol="BTCUSDT",
            decision_run_id=ai_run.id,
            allowed=False,
            decision="hold",
            reason_codes=["HOLD_DECISION"],
            approved_risk_pct=0.0,
            approved_leverage=0.0,
            payload={
                "allowed": False,
                "decision": "hold",
                "reason_codes": ["HOLD_DECISION"],
                "blocked_reason_codes": ["HOLD_DECISION"],
            },
        )
    )
    db_session.add(
        SchedulerRun(
            schedule_window="15m",
            workflow="interval_decision_cycle",
            status="success",
            triggered_by="scheduler",
            next_run_at=now + timedelta(minutes=15),
            created_at=now,
            outcome={
                "symbol": "BTCUSDT",
                "status": "skipped",
                "ai_review_status": "skipped",
                "last_ai_trigger_reason": "entry_candidate_event",
                "last_ai_invoked_at": old_ai_at.isoformat(),
                "last_ai_skip_reason": "SOFT_SIGNAL_REVIEW_SUPPRESSED_WEAK_CANDIDATE",
                "trigger_fingerprint": "current-weak-soft-signal",
                "trigger": {
                    "trigger_reason": "entry_candidate_event",
                    "reason_codes": ["ENTRY_CANDIDATE_SELECTED"],
                    "trigger_fingerprint": "current-weak-soft-signal",
                },
            },
        )
    )
    db_session.commit()

    payload = get_operator_dashboard(db_session, view="decision")
    btc = next(item for item in payload.symbols if item.symbol == "BTCUSDT")

    assert btc.ai_decision.last_ai_invoked_at is not None
    assert btc.ai_decision.last_ai_skip_reason == "SOFT_SIGNAL_REVIEW_SUPPRESSED_WEAK_CANDIDATE"
    assert btc.ai_decision.ai_skip_reason == "SOFT_SIGNAL_REVIEW_SUPPRESSED_WEAK_CANDIDATE"
    assert btc.ai_decision.ai_review.skip_reason == "SOFT_SIGNAL_REVIEW_SUPPRESSED_WEAK_CANDIDATE"
    assert btc.ai_decision.ai_review.provider_invoked is False
    assert btc.ai_decision.ai_review.provider_skipped is True
    assert btc.ai_decision.ai_review.provider_status == "skipped_pre_ai"
    assert btc.ai_decision.ai_review.invoked_at is None
    assert btc.ai_decision.ai_review.provider_name is None
    assert btc.ai_decision.ai_review.provider_source is None


def test_operator_dashboard_exposes_stale_triggered_pending_plan_warning(db_session) -> None:
    now = utcnow_naive()
    settings = get_or_create_settings(db_session)
    settings.default_symbol = "BTCUSDT"
    settings.tracked_symbols = ["BTCUSDT"]
    db_session.add(settings)
    db_session.add(
        PendingEntryPlan(
            symbol="BTCUSDT",
            side="long",
            plan_status="triggered",
            source_decision_run_id=9101,
            source_timeframe="15m",
            entry_mode="pullback_confirm",
            entry_zone_min=100.0,
            entry_zone_max=101.0,
            invalidation_price=99.0,
            max_chase_bps=10.0,
            idea_ttl_minutes=15,
            stop_loss=99.0,
            take_profit=104.0,
            risk_pct_cap=0.01,
            leverage_cap=1.0,
            expires_at=now - timedelta(hours=2),
            triggered_at=now - timedelta(hours=3),
            idempotency_key="stale-triggered-plan-warning",
            metadata_json={"execution_result": {"status": "partially_filled"}},
        )
    )
    db_session.commit()

    payload = get_operator_dashboard(db_session)

    assert payload.control.stale_pending_entry_plan_count == 1
    assert payload.control.stale_pending_entry_plans
    assert payload.control.stale_pending_entry_plans[0]["gate_state"] == "triggered_stale_history"


def test_operator_dashboard_exposes_decision_macro_event_context_and_enrichment(db_session) -> None:
    from trading_mvp.models import AgentRun

    now = utcnow_naive()
    settings = get_or_create_settings(db_session)
    settings.default_symbol = "CPIUSDT"
    settings.tracked_symbols = ["CPIUSDT"]
    db_session.add(settings)
    db_session.flush()

    event_context = {
        "source_status": "external_api",
        "source_provenance": "external_api",
        "source_vendor": "fred",
        "generated_at": now.isoformat(),
        "is_stale": False,
        "is_complete": True,
        "next_event_at": (now + timedelta(minutes=10)).isoformat(),
        "next_event_name": "US CPI",
        "next_event_importance": "high",
        "minutes_to_next_event": 10,
        "active_risk_window": True,
        "affected_assets": ["USD", "CRYPTO"],
        "event_bias": "bearish",
        "enrichment_vendors": ["bls", "bea"],
        "events": [
            {
                "event_name": "US CPI",
                "event_at": (now + timedelta(minutes=10)).isoformat(),
                "importance": "high",
                "affected_assets": ["USD", "CRYPTO"],
                "release_enrichment": {
                    "bls": {"actual": 3.2, "prior": 3.1, "forecast": 3.0},
                    "bea": {"actual": 0.4, "prior": 0.3, "forecast": 0.2},
                },
            }
        ],
    }
    event_risk_context = {
        "event_risk_active": True,
        "reason_codes": [
            "MACRO_EVENT_RISK_WINDOW_ACTIVE",
            "MACRO_EVENT_IMMINENT",
            "MACRO_EVENT_ENRICHMENT_AVAILABLE",
        ],
        "risk_pct_multiplier": 0.5,
        "hold_bias": 0.25,
        "event_name": "US CPI",
        "event_importance": "high",
        "minutes_to_event": 10,
        "active_risk_window": True,
        "source_status": "external_api",
        "source_vendor": "fred",
        "affected_assets": ["USD", "CRYPTO"],
        "event_bias_observed": "bearish",
        "event_bias_used": "bearish",
        "enrichment_vendors": ["bls", "bea"],
    }
    run = AgentRun(
        role="trading_decision",
        trigger_event="realtime_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="openai",
        summary="macro risk hold",
        input_payload={
            "ai_trigger": {
                "trigger_reason": "entry_candidate_event",
                "reason_codes": ["ENTRY_CANDIDATE_SELECTED"],
                "trigger_fingerprint": "macro-event-trigger",
            },
            "market_snapshot": {
                "symbol": "CPIUSDT",
                "timeframe": "15m",
                "snapshot_time": now.isoformat(),
                "event_context": event_context,
            },
            "features": {
                "trend_score": 0.32,
                "momentum_score": 0.18,
                "volume_ratio": 1.08,
                "event_context": event_context,
                "regime": {
                    "primary_regime": "bullish",
                    "trend_alignment": "bullish_aligned",
                    "volume_regime": "normal",
                    "weak_volume": False,
                },
            },
            "ai_context": {
                "event_context_summary": {
                    "source_status": "external_api",
                    "source_provenance": "external_api",
                    "source_vendor": "fred",
                    "next_event_name": "US CPI",
                    "next_event_importance": "high",
                    "minutes_to_next_event": 10,
                    "active_risk_window": True,
                    "event_bias": "bearish",
                    "enrichment_vendors": ["bls", "bea"],
                },
                "event_risk_active": True,
                "event_risk_reason_codes": event_risk_context["reason_codes"],
                "event_risk_context": event_risk_context,
            },
        },
        output_payload={
            "symbol": "CPIUSDT",
            "timeframe": "15m",
            "decision": "hold",
            "confidence": 0.52,
            "rationale_codes": ["MACRO_EVENT_IMMINENT"],
            "explanation_short": "Macro event risk kept the entry conservative.",
        },
        metadata_json={
            "source": "llm",
            "last_ai_trigger_reason": "entry_candidate_event",
            "last_ai_invoked_at": now.isoformat(),
            "event_risk_active": True,
            "event_risk_reason_codes": event_risk_context["reason_codes"],
            "event_risk_context": event_risk_context,
        },
        schema_valid=True,
        created_at=now,
    )
    db_session.add(run)
    db_session.flush()
    db_session.add(
        RiskCheck(
            symbol="CPIUSDT",
            decision_run_id=run.id,
            allowed=False,
            decision="hold",
            reason_codes=["HOLD_DECISION"],
            approved_risk_pct=0.0,
            approved_leverage=0.0,
            payload={"allowed": False, "decision": "hold", "reason_codes": ["HOLD_DECISION"]},
        )
    )
    db_session.commit()

    payload = get_operator_dashboard(db_session)
    cpi = payload.symbols[0]
    macro = cpi.ai_decision.macro_event_context_summary
    macro_risk = cpi.ai_decision.macro_event_risk_summary
    risk_row = get_risk_checks(db_session)[0]
    risk_macro = risk_row["macro_event_context_summary"]

    assert macro.source_status == "external_api"
    assert macro.source_vendor == "fred"
    assert macro.next_event_name == "US CPI"
    assert macro.next_event_importance == "high"
    assert macro.minutes_to_next_event == 10
    assert macro.active_risk_window is True
    assert macro.affected_assets == ["USD", "CRYPTO"]
    assert macro.enrichment_vendors == ["bls", "bea"]
    assert macro.bls_actual_enriched is True
    assert macro.bea_actual_enriched is True
    assert macro.event_risk_active is True
    assert "MACRO_EVENT_IMMINENT" in macro.event_risk_reason_codes
    assert macro.event_bias_used == "bearish"
    assert macro_risk.next_event_name == "US CPI"
    assert macro_risk.event_risk_active is True
    assert macro_risk.bls_actual_enriched is True
    assert risk_row["macro_event_risk_summary"]["next_event_name"] == "US CPI"
    assert risk_macro["bls_actual_enriched"] is True
    assert risk_macro["bea_actual_enriched"] is True


def test_operator_dashboard_marks_stale_macro_event_context_without_directional_bias(db_session) -> None:
    from trading_mvp.models import AgentRun

    now = utcnow_naive()
    settings = get_or_create_settings(db_session)
    settings.default_symbol = "STALUSDT"
    settings.tracked_symbols = ["STALUSDT"]
    db_session.add(settings)
    db_session.flush()

    event_context = {
        "source_status": "stale",
        "source_provenance": "external_api",
        "source_vendor": "fred",
        "generated_at": (now - timedelta(hours=5)).isoformat(),
        "is_stale": True,
        "is_complete": False,
        "next_event_name": "US CPI",
        "next_event_importance": "high",
        "minutes_to_next_event": 10,
        "active_risk_window": True,
        "affected_assets": ["USD", "CRYPTO"],
        "event_bias": "bearish",
        "enrichment_vendors": [],
        "events": [],
    }
    event_risk_context = {
        "event_risk_active": False,
        "reason_codes": ["MACRO_EVENT_CONTEXT_STALE", "MACRO_EVENT_CONTEXT_INCOMPLETE"],
        "event_name": "US CPI",
        "event_importance": "high",
        "minutes_to_event": 10,
        "active_risk_window": True,
        "source_status": "stale",
        "source_vendor": "fred",
        "affected_assets": ["USD", "CRYPTO"],
        "event_bias_observed": "bearish",
        "event_bias_used": None,
        "enrichment_vendors": [],
    }
    db_session.add(
        AgentRun(
            role="trading_decision",
            trigger_event="realtime_cycle",
            schema_name="TradeDecision",
            status="completed",
            provider_name="openai",
            summary="stale macro context",
            input_payload={
                "ai_trigger": {
                    "trigger_reason": "entry_candidate_event",
                    "reason_codes": ["ENTRY_CANDIDATE_SELECTED"],
                    "trigger_fingerprint": "stale-macro-trigger",
                },
                "features": {
                    "event_context": event_context,
                    "trend_score": 0.32,
                    "momentum_score": 0.18,
                    "volume_ratio": 1.08,
                    "regime": {"trend_alignment": "bullish_aligned", "volume_regime": "normal"},
                },
                "ai_context": {
                    "event_context_summary": {
                        "source_status": "stale",
                        "source_provenance": "external_api",
                        "source_vendor": "fred",
                        "next_event_name": "US CPI",
                        "next_event_importance": "high",
                        "minutes_to_next_event": 10,
                        "active_risk_window": True,
                        "event_bias": "bearish",
                        "enrichment_vendors": [],
                    },
                    "event_risk_active": False,
                    "event_risk_reason_codes": event_risk_context["reason_codes"],
                    "event_risk_context": event_risk_context,
                },
            },
            output_payload={
                "symbol": "STALUSDT",
                "timeframe": "15m",
                "decision": "hold",
                "confidence": 0.52,
                "rationale_codes": ["MACRO_EVENT_CONTEXT_STALE"],
                "explanation_short": "Stale event context was not used as directional signal.",
            },
            metadata_json={
                "source": "llm",
                "last_ai_trigger_reason": "entry_candidate_event",
                "last_ai_invoked_at": now.isoformat(),
                "event_risk_active": False,
                "event_risk_reason_codes": event_risk_context["reason_codes"],
                "event_risk_context": event_risk_context,
            },
            schema_valid=True,
            created_at=now,
        )
    )
    db_session.commit()

    payload = get_operator_dashboard(db_session)
    stale = payload.symbols[0].ai_decision.macro_event_context_summary
    stale_risk = payload.symbols[0].ai_decision.macro_event_risk_summary

    assert stale.source_status == "stale"
    assert stale.is_stale is True
    assert stale.is_complete is False
    assert stale.is_incomplete is True
    assert stale.event_risk_active is False
    assert stale.event_bias_used is None
    assert "MACRO_EVENT_CONTEXT_STALE" in stale.event_risk_reason_codes
    assert "MACRO_EVENT_CONTEXT_INCOMPLETE" in stale.event_risk_reason_codes
    assert stale_risk.source_status == "stale"
    assert stale_risk.is_incomplete is True


def test_overview_prioritizes_stale_sync_reasons_in_operational_status(db_session) -> None:
    settings = get_or_create_settings(db_session)
    settings.live_trading_enabled = True
    settings.manual_live_approval = True
    settings.live_execution_armed = True
    settings.live_execution_armed_until = None
    settings.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    db_session.add(settings)
    db_session.flush()

    stale_at = utcnow_naive() - timedelta(hours=2)
    mark_sync_success(settings, scope="account", synced_at=utcnow_naive())
    mark_sync_success(settings, scope="positions", synced_at=stale_at, detail={"symbol": "BTCUSDT"})
    mark_sync_success(settings, scope="open_orders", synced_at=utcnow_naive())
    mark_sync_success(settings, scope="protective_orders", synced_at=utcnow_naive())
    db_session.add(settings)
    db_session.flush()

    db_session.add(
        RiskCheck(
            symbol="BTCUSDT",
            allowed=False,
            decision="long",
            reason_codes=["MAX_CONSECUTIVE_LOSSES_REACHED"],
            approved_risk_pct=0.0,
            approved_leverage=0.0,
            payload={"allowed": False, "decision": "long", "reason_codes": ["MAX_CONSECUTIVE_LOSSES_REACHED"]},
        )
    )
    db_session.flush()

    overview = get_overview(db_session)

    assert overview.sync_freshness_summary["positions"]["status"] == "stale"
    assert overview.sync_freshness_summary["positions"]["raw_status"] == "synced"
    assert overview.blocked_reasons[0] == "POSITION_STATE_STALE"
    assert "MAX_CONSECUTIVE_LOSSES_REACHED" in overview.blocked_reasons
    assert overview.guard_mode_reason_code == "POSITION_STATE_STALE"


def test_operator_dashboard_flags_missing_feature_input_and_uses_candle_timestamp(db_session) -> None:
    snapshot_time = utcnow_naive() - timedelta(minutes=5)
    candle_time = snapshot_time - timedelta(minutes=7, seconds=4)

    settings = get_or_create_settings(db_session)
    settings.default_symbol = "ADAUSDT"
    settings.tracked_symbols = ["ADAUSDT"]
    settings.default_timeframe = "15m"
    settings.live_trading_enabled = True
    settings.manual_live_approval = True
    settings.live_execution_armed = True
    settings.live_execution_armed_until = snapshot_time + timedelta(minutes=15)
    db_session.add(settings)
    db_session.flush()

    for scope in ("account", "positions", "open_orders", "protective_orders"):
        mark_sync_success(settings, scope=scope, synced_at=snapshot_time)
    db_session.add(settings)
    db_session.flush()

    db_session.add(
        MarketSnapshot(
            symbol="ADAUSDT",
            timeframe="15m",
            snapshot_time=snapshot_time,
            latest_price=0.2477,
            latest_volume=2321802.0,
            candle_count=60,
            is_stale=False,
            is_complete=True,
            payload={
                "symbol": "ADAUSDT",
                "timeframe": "15m",
                "snapshot_time": snapshot_time.isoformat(),
                "latest_price": 0.2477,
                "latest_volume": 2321802.0,
                "candle_count": 60,
                "is_stale": False,
                "is_complete": True,
                "candles": [
                    {
                        "timestamp": candle_time.isoformat(),
                        "open": 0.2481,
                        "high": 0.2482,
                        "low": 0.2476,
                        "close": 0.2477,
                        "volume": 2321802.0,
                    }
                ],
            },
        )
    )
    db_session.commit()

    payload = get_operator_dashboard(db_session)

    ada = payload.symbols[0]
    assert ada.symbol == "ADAUSDT"
    assert ada.market_snapshot_time == snapshot_time
    assert ada.market_candle_time == candle_time
    assert ada.market_context_summary == {}
    assert "feature_input_missing" in ada.stale_flags
    assert ada.feature_input_delay_minutes is not None
    assert ada.feature_input_delay_minutes >= 5
    assert ada.feature_input_delay_threshold_minutes == 30
    assert ada.feature_input_delayed is False
    assert ada.live_execution_ready is False


def test_operator_dashboard_uses_feature_snapshot_market_context_fallback(db_session) -> None:
    snapshot_time = utcnow_naive() - timedelta(minutes=5)
    candle_time = snapshot_time - timedelta(minutes=7, seconds=4)

    settings = get_or_create_settings(db_session)
    settings.default_symbol = "ADAUSDT"
    settings.tracked_symbols = ["ADAUSDT"]
    settings.default_timeframe = "15m"
    settings.live_trading_enabled = True
    settings.manual_live_approval = True
    settings.live_execution_armed = True
    settings.live_execution_armed_until = snapshot_time + timedelta(minutes=15)
    db_session.add(settings)
    db_session.flush()

    for scope in ("account", "positions", "open_orders", "protective_orders"):
        mark_sync_success(settings, scope=scope, synced_at=snapshot_time)
    db_session.add(settings)
    db_session.flush()

    market = MarketSnapshot(
        symbol="ADAUSDT",
        timeframe="15m",
        snapshot_time=snapshot_time,
        latest_price=0.2477,
        latest_volume=2321802.0,
        candle_count=60,
        is_stale=False,
        is_complete=True,
        payload={
            "symbol": "ADAUSDT",
            "timeframe": "15m",
            "snapshot_time": snapshot_time.isoformat(),
            "latest_price": 0.2477,
            "latest_volume": 2321802.0,
            "candle_count": 60,
            "is_stale": False,
            "is_complete": True,
            "candles": [
                {
                    "timestamp": candle_time.isoformat(),
                    "open": 0.2481,
                    "high": 0.2482,
                    "low": 0.2476,
                    "close": 0.2477,
                    "volume": 2321802.0,
                }
            ],
        },
    )
    db_session.add(market)
    db_session.flush()
    db_session.add(
        FeatureSnapshot(
            symbol="ADAUSDT",
            timeframe="15m",
            market_snapshot_id=market.id,
            feature_time=snapshot_time,
            trend_score=1.2,
            volatility_pct=0.02,
            volume_ratio=1.4,
            drawdown_pct=0.01,
            rsi=58.0,
            atr=0.004,
            payload={
                "regime": {
                    "primary_regime": "bullish",
                    "trend_alignment": "bullish_aligned",
                    "volatility_regime": "normal",
                    "volume_regime": "strong",
                    "momentum_state": "stable",
                    "weak_volume": False,
                    "momentum_weakening": False,
                }
            },
        )
    )
    db_session.commit()

    payload = get_operator_dashboard(db_session)

    ada = payload.symbols[0]
    assert ada.market_candle_time == candle_time
    assert ada.market_context_summary["primary_regime"] == "bullish"
    assert ada.market_context_summary["trend_alignment"] == "bullish_aligned"
    assert "feature_input_missing" not in ada.stale_flags
    assert ada.feature_input_delay_minutes is None
    assert ada.feature_input_delay_threshold_minutes is None
    assert ada.feature_input_delayed is False


def test_operator_dashboard_exposes_derivatives_and_event_context_summaries(db_session) -> None:
    snapshot_time = utcnow_naive() - timedelta(minutes=5)
    next_event_at = snapshot_time + timedelta(minutes=35)

    settings = get_or_create_settings(db_session)
    settings.default_symbol = "ADAUSDT"
    settings.tracked_symbols = ["ADAUSDT"]
    settings.default_timeframe = "15m"
    settings.live_trading_enabled = True
    settings.manual_live_approval = True
    settings.live_execution_armed = True
    settings.live_execution_armed_until = snapshot_time + timedelta(minutes=15)
    db_session.add(settings)
    db_session.flush()

    for scope in ("account", "positions", "open_orders", "protective_orders"):
        mark_sync_success(settings, scope=scope, synced_at=snapshot_time)
    db_session.add(settings)
    db_session.flush()

    market = MarketSnapshot(
        symbol="ADAUSDT",
        timeframe="15m",
        snapshot_time=snapshot_time,
        latest_price=0.2477,
        latest_volume=2321802.0,
        candle_count=60,
        is_stale=False,
        is_complete=True,
        payload={
            "symbol": "ADAUSDT",
            "timeframe": "15m",
            "snapshot_time": snapshot_time.isoformat(),
            "latest_price": 0.2477,
            "latest_volume": 2321802.0,
            "candle_count": 60,
            "is_stale": False,
            "is_complete": True,
            "candles": [],
            "derivatives_context": {
                "available": True,
                "source": "binance_public",
                "funding_bias": "neutral",
                "basis_bias": "bullish",
                "taker_flow_alignment": "bullish",
                "spread_bps": 4.2,
                "spread_stress": False,
            },
            "event_context": {
                "source_status": "stub",
                "generated_at": snapshot_time.isoformat(),
                "is_stale": False,
                "is_complete": True,
                "next_event_at": next_event_at.isoformat(),
                "next_event_name": "FOMC",
                "next_event_importance": "high",
                "minutes_to_next_event": 35,
                "active_risk_window": False,
                "affected_assets": ["ADA", "BTC"],
                "event_bias": "neutral",
                "events": [],
            },
        },
    )
    db_session.add(market)
    db_session.flush()
    db_session.add(
        FeatureSnapshot(
            symbol="ADAUSDT",
            timeframe="15m",
            market_snapshot_id=market.id,
            feature_time=snapshot_time,
            trend_score=1.2,
            volatility_pct=0.02,
            volume_ratio=1.4,
            drawdown_pct=0.01,
            rsi=58.0,
            atr=0.004,
            payload={
                "regime": {
                    "primary_regime": "bullish",
                    "trend_alignment": "bullish_aligned",
                    "volatility_regime": "normal",
                    "volume_regime": "strong",
                    "momentum_state": "stable",
                    "weak_volume": False,
                    "momentum_weakening": False,
                },
                "derivatives": {
                    "available": True,
                    "source": "binance_public",
                    "funding_bias": "neutral",
                    "basis_bias": "bullish",
                    "taker_flow_alignment": "bullish",
                    "spread_bps": 3.8,
                    "spread_stress": False,
                    "crowded_long_risk": False,
                    "crowded_short_risk": False,
                },
                "event_context": {
                    "source_status": "stub",
                    "generated_at": snapshot_time.isoformat(),
                    "is_stale": False,
                    "is_complete": True,
                    "next_event_at": next_event_at.isoformat(),
                    "next_event_name": "FOMC",
                    "next_event_importance": "high",
                    "minutes_to_next_event": 35,
                    "active_risk_window": False,
                    "affected_assets": ["ADA", "BTC"],
                    "event_bias": "neutral",
                    "events": [],
                },
            },
        )
    )
    db_session.commit()

    payload = get_operator_dashboard(db_session)

    ada = payload.symbols[0]
    assert ada.derivatives_summary["available"] is True
    assert ada.derivatives_summary["taker_flow_alignment"] == "bullish"
    assert ada.derivatives_summary["spread_bps"] == 3.8
    assert ada.event_context_summary["source_status"] == "stub"
    assert ada.event_context_summary["is_stale"] is False
    assert ada.event_context_summary["next_event_name"] == "FOMC"
    assert ada.event_context_summary["next_event_importance"] == "high"
    assert ada.event_context_summary["minutes_to_next_event"] == 35
    assert ada.event_context_summary["active_risk_window"] is False


def test_operator_dashboard_marks_feature_input_delay_after_threshold(db_session) -> None:
    snapshot_time = utcnow_naive() - timedelta(minutes=45)
    candle_time = snapshot_time - timedelta(minutes=15)

    settings = get_or_create_settings(db_session)
    settings.default_symbol = "ADAUSDT"
    settings.tracked_symbols = ["ADAUSDT"]
    settings.default_timeframe = "15m"
    settings.live_trading_enabled = True
    settings.manual_live_approval = True
    settings.live_execution_armed = True
    settings.live_execution_armed_until = snapshot_time + timedelta(minutes=15)
    db_session.add(settings)
    db_session.flush()

    for scope in ("account", "positions", "open_orders", "protective_orders"):
        mark_sync_success(settings, scope=scope, synced_at=snapshot_time)
    db_session.add(settings)
    db_session.flush()

    db_session.add(
        MarketSnapshot(
            symbol="ADAUSDT",
            timeframe="15m",
            snapshot_time=snapshot_time,
            latest_price=0.2477,
            latest_volume=2321802.0,
            candle_count=60,
            is_stale=False,
            is_complete=True,
            payload={
                "symbol": "ADAUSDT",
                "timeframe": "15m",
                "snapshot_time": snapshot_time.isoformat(),
                "latest_price": 0.2477,
                "latest_volume": 2321802.0,
                "candle_count": 60,
                "is_stale": False,
                "is_complete": True,
                "candles": [
                    {
                        "timestamp": candle_time.isoformat(),
                        "open": 0.2481,
                        "high": 0.2482,
                        "low": 0.2476,
                        "close": 0.2477,
                        "volume": 2321802.0,
                    }
                ],
            },
        )
    )
    db_session.commit()

    payload = get_operator_dashboard(db_session)

    ada = payload.symbols[0]
    assert "feature_input_missing" in ada.stale_flags
    assert ada.feature_input_delay_threshold_minutes == 30
    assert ada.feature_input_delay_minutes is not None
    assert ada.feature_input_delay_minutes >= 45
    assert ada.feature_input_delayed is True


def test_profitability_dashboard_api_returns_windowed_snapshot(testclient_db_factory) -> None:
    TestingSessionLocal = testclient_db_factory("profitability_dashboard.db")

    with TestingSessionLocal() as session:
        _seed_profitability_dashboard_rows(session)
        session.commit()

    with TestClient(app) as client:
        response = client.get("/api/dashboard/profitability")

    assert response.status_code == 200
    payload = response.json()
    assert payload["windows"][0]["window_label"] == "24h"
    assert payload["windows"][0]["cost_breakdown"]["net_pnl_including_funding"] == 7.35
    assert payload["windows"][0]["entry_quality"]["entry_passive_limit"]["trade_count"] == 1
    assert payload["entry_quality"]["entry_passive_limit"]["trade_count"] == 1
    assert payload["cost_breakdowns"][0]["window_label"] == "today"
    assert "rationale_winners" in payload["windows"][0]
    assert "rationale_losers" in payload["windows"][0]
    assert payload["windows"][0]["limited_live_readiness"]["read_only"] is True
    assert payload["limited_live_readiness"]["status"] == payload["windows"][0]["limited_live_readiness"]["status"]
    assert "execution_windows" in payload
    assert payload["execution_windows"][0]["worst_profiles"]
    assert payload["hold_blocked_summary"]["latest_blocked_reasons"] == ["TRADING_PAUSED", "HOLD_DECISION"]


def test_operator_dashboard_api_returns_operator_flow(testclient_db_factory) -> None:
    TestingSessionLocal = testclient_db_factory("operator_dashboard.db")

    with TestingSessionLocal() as session:
        _seed_multi_symbol_operator_rows(session)
        session.commit()

    with TestClient(app) as client:
        response = client.get("/api/dashboard/operator")

    assert response.status_code == 200
    payload = response.json()
    assert payload["control"]["default_symbol"] == "BTCUSDT"
    assert payload["control"]["tracked_symbol_count"] == 2
    assert "operational_status" not in payload["control"]
    assert "candidate_selection_summary" not in payload["control"]
    assert "reconciliation_summary" not in payload["control"]
    assert "wallet_balance" in payload["control"]["pnl_summary"]
    assert "available_balance" in payload["control"]["pnl_summary"]
    assert "net_pnl" in payload["control"]["pnl_summary"]
    assert payload["control"]["pnl_summary"]["account_snapshot_available"] is False
    assert payload["control"]["account_sync_summary"]["account_snapshot_available"] is False
    assert payload["control"]["limited_live_readiness"]["read_only"] is True
    assert payload["market_signal"]["performance_windows"][0]["limited_live_readiness"]["read_only"] is True
    assert "entry_quality" in payload["market_signal"]["performance_windows"][0]
    assert payload["market_signal"]["profitability_cost_breakdowns"][0]["window_label"] == "today"
    assert [item["window_label"] for item in payload["market_signal"]["profitability_cost_breakdowns"]] == ["today"]
    assert len(payload["symbols"]) == 2
    assert "ai_decision" not in payload
    assert "risk_guard" not in payload
    assert "execution" not in payload
    btc = next(item for item in payload["symbols"] if item["symbol"] == "BTCUSDT")
    eth = next(item for item in payload["symbols"] if item["symbol"] == "ETHUSDT")
    assert btc["latest_price"] == 70500.0
    assert btc["ai_decision"]["last_ai_trigger_reason"] == "entry_candidate_event"
    assert btc["ai_decision"]["ai_review_type"] == "entry_candidate_review"
    assert btc["ai_decision"]["ai_trigger_reason_codes"] == ["ENTRY_CANDIDATE_SELECTED"]
    assert btc["ai_decision"]["trigger_deduped"] is True
    assert btc["ai_decision"]["last_ai_skip_reason"] == "TRIGGER_DEDUPED"
    assert btc["ai_decision"]["ai_skip_reason"] == "TRIGGER_DEDUPED"
    assert btc["ai_decision"]["ai_review"]["dedupe_reason"] == "TRIGGER_DEDUPED"
    assert btc["ai_decision"]["ai_review"]["provider_status"] == "deduped"
    assert btc["ai_decision"]["market_signal_context"]["summary"] == btc["ai_decision"]["market_signal_summary"]
    assert btc["ai_decision"]["market_signal_context"]["breakout_direction"] == "up"
    assert btc["ai_decision"]["market_signal_summary"] == btc["ai_decision"]["ai_trigger_summary"]
    assert btc["ai_decision"]["event_risk_acknowledgement"] == "High-impact macro event is approaching; event-aware caution applied."
    assert btc["ai_decision"]["confidence_penalty_reason"] == "EVENT_WINDOW_PROXIMITY"
    assert btc["ai_decision"]["scenario_note"] == "Prefer confirmation after the event before fresh entry."
    assert btc["candidate_selection"]["assigned_slot"] == "slot_1"
    assert btc["candidate_selection"]["candidate_weight"] == 0.64
    assert btc["candidate_selection"]["capacity_reason"] == "mixed_breadth_moderate_capacity"
    assert btc["risk_guard"]["allowed"] is False
    assert btc["risk_guard"]["assigned_slot"] == "slot_1"
    assert btc["risk_guard"]["holding_profile"] == "scalp"
    assert "debug_payload" not in btc["risk_guard"]
    assert "current_cycle_result" not in btc["risk_guard"]
    assert "raw_payload" not in btc["risk_guard"]
    assert btc["risk_guard_result"]["allowed"] is False
    assert btc["risk_guard_result"]["blocked_reason_codes"] == ["POSITION_STATE_STALE"]
    assert btc["risk_guard_result"]["hold_decision"] is False
    assert btc["open_position"]["is_open"] is True
    assert btc["open_position"]["holding_profile"] == "swing"
    assert btc["open_position"]["hard_stop_active"] is True
    assert btc["open_position"]["stop_widening_allowed"] is False
    assert btc["open_position"]["position_exit_review"]["recommendation"] == "hold_runner"
    assert "agent_run_id" not in btc["open_position"]["position_exit_review"]
    assert eth["latest_price"] == 3400.0
    assert eth["ai_decision"]["next_ai_review_due_at"] is None
    assert eth["ai_decision"]["ai_review"]["provider_status"] == "invoked"
    assert eth["ai_decision"]["assigned_slot"] == "slot_2"
    assert eth["ai_decision"]["portfolio_slot_soft_cap_applied"] is True
    assert eth["ai_decision"]["event_risk_acknowledgement"] is None
    assert eth["ai_decision"]["confidence_penalty_reason"] is None
    assert eth["ai_decision"]["scenario_note"] is None
    assert eth["risk_guard"]["allowed"] is True
    assert eth["risk_guard"]["auto_resized_entry"] is True
    assert eth["risk_guard"]["approved_projected_notional"] == 150000.0
    assert eth["risk_guard"]["approved_quantity"] == 44.117647
    assert eth["risk_guard"]["auto_resize_reason"] == "CLAMPED_TO_SINGLE_POSITION_HEADROOM"
    assert eth["risk_guard"]["portfolio_slot_soft_cap_applied"] is True
    assert eth["risk_guard_result"]["allowed"] is True
    assert eth["risk_guard_result"]["approved_risk_pct"] == 0.01
    assert eth["risk_guard_result"]["approved_leverage"] == 2.0
    assert eth["candidate_selection"]["assigned_slot"] == "slot_2"
    assert eth["candidate_selection"]["candidate_weight"] == 0.42
    assert eth["execution"]["symbol"] == "ETHUSDT"
    assert len(payload["audit_events"]) >= 1


def test_operator_dashboard_home_exposes_readiness_reasons_without_full_profitability_payload(
    testclient_db_factory,
) -> None:
    TestingSessionLocal = testclient_db_factory("operator_home_readiness.db")

    with TestingSessionLocal() as session:
        _seed_profitability_dashboard_rows(session)
        session.commit()

    with TestClient(app) as client:
        response = client.get("/api/dashboard/operator?view=home")

    assert response.status_code == 200
    payload = response.json()
    readiness = payload["control"]["limited_live_readiness"]

    assert payload["symbols"] == []
    assert payload["market_signal"]["performance_windows"] == []
    assert payload["market_signal"]["profitability_cost_breakdowns"] == []
    assert readiness["status"] == "not_ready"
    assert "insufficient_sample" in readiness["reason_codes"]
    assert "productization_profitability_unverified" in readiness["reason_codes"]
    assert readiness["recent_candidate_events"] >= 2
    assert readiness["actual_entries"] == 1


def test_operator_dashboard_route_projection_skips_unused_sections(testclient_db_factory) -> None:
    TestingSessionLocal = testclient_db_factory("operator_projection.db")

    with TestingSessionLocal() as session:
        _seed_multi_symbol_operator_rows(session)
        session.commit()

    with TestClient(app) as client:
        market_response = client.get("/api/dashboard/operator?view=market")
        scheduler_response = client.get("/api/dashboard/operator?view=scheduler")
        decision_response = client.get("/api/dashboard/operator?view=decision")
        risk_response = client.get("/api/dashboard/operator?view=risk")

    assert market_response.status_code == 200
    assert scheduler_response.status_code == 200
    assert decision_response.status_code == 200
    assert risk_response.status_code == 200

    market_payload = market_response.json()
    market_btc = next(item for item in market_payload["symbols"] if item["symbol"] == "BTCUSDT")

    assert market_payload["market_signal"]["performance_windows"] == []
    assert market_payload["market_signal"]["profitability_cost_breakdowns"] == []
    assert market_payload["execution_windows"] == []
    assert market_payload["audit_events"] == []
    assert market_btc["latest_price"] == 70500.0
    assert market_btc["ai_decision"]["decision_run_id"] is None
    assert market_btc["risk_guard"]["risk_check_id"] is None
    assert market_btc["event_operator_control"] is None
    assert market_btc["audit_events"] == []

    scheduler_payload = scheduler_response.json()
    scheduler_btc = next(item for item in scheduler_payload["symbols"] if item["symbol"] == "BTCUSDT")

    assert scheduler_payload["market_signal"]["performance_windows"] == []
    assert scheduler_payload["market_signal"]["profitability_cost_breakdowns"] == []
    assert scheduler_payload["execution_windows"] == []
    assert scheduler_payload["audit_events"] == []
    assert scheduler_btc["ai_decision"]["last_ai_trigger_reason"] == "entry_candidate_event"
    assert scheduler_btc["risk_guard"]["blocked_reason_codes"] == ["POSITION_STATE_STALE"]
    assert scheduler_btc["candidate_selection"]["assigned_slot"] == "slot_1"
    assert scheduler_btc["event_operator_control"] is None
    assert scheduler_btc["execution"]["order_id"] is None
    assert scheduler_btc["protection_status"]["status"] == "unknown"
    assert scheduler_btc["audit_events"] == []

    decision_payload = decision_response.json()
    decision_btc = next(item for item in decision_payload["symbols"] if item["symbol"] == "BTCUSDT")

    assert decision_payload["market_signal"]["performance_windows"] == []
    assert decision_payload["market_signal"]["profitability_cost_breakdowns"] == []
    assert decision_payload["execution_windows"] == []
    assert decision_payload["audit_events"] == []
    assert decision_btc["ai_decision"]["decision"] == "long"
    assert decision_btc["risk_guard"]["blocked_reason_codes"] == ["POSITION_STATE_STALE"]
    assert decision_btc["execution"]["order_id"] is not None
    assert decision_btc["open_position"]["is_open"] is True
    assert decision_btc["event_operator_control"] is None
    assert decision_btc["audit_events"] == []

    risk_payload = risk_response.json()
    risk_btc = next(item for item in risk_payload["symbols"] if item["symbol"] == "BTCUSDT")

    assert risk_payload["market_signal"]["performance_windows"] == []
    assert risk_payload["market_signal"]["profitability_cost_breakdowns"] == []
    assert risk_payload["execution_windows"] == []
    assert risk_payload["audit_events"] == []
    assert risk_btc["ai_decision"]["decision_run_id"] is None
    assert risk_btc["risk_guard"]["blocked_reason_codes"] == ["POSITION_STATE_STALE"]
    assert risk_btc["execution"]["order_id"] is not None
    assert risk_btc["protection_status"]["status"] == "unknown"
    assert risk_btc["event_operator_control"] is None
    assert risk_btc["audit_events"] == []


def test_operator_dashboard_compact_view_skips_overview_decision_snapshot(db_session, monkeypatch) -> None:
    import trading_mvp.services.dashboard as dashboard_module
    from trading_mvp.models import AgentRun

    now = utcnow_naive()
    settings = get_or_create_settings(db_session)
    settings.default_symbol = "BTCUSDT"
    settings.tracked_symbols = ["BTCUSDT"]
    db_session.add(
        MarketSnapshot(
            symbol="BTCUSDT",
            timeframe="15m",
            snapshot_time=now,
            latest_price=70500.0,
            latest_volume=1000.0,
            candle_count=120,
            is_stale=False,
            is_complete=True,
            payload={},
        )
    )
    db_session.add(
        AgentRun(
            role="trading_decision",
            trigger_event="realtime_cycle",
            schema_name="TradeDecision",
            status="completed",
            provider_name="deterministic-mock",
            summary="heavy latest decision",
            input_payload={"large": ["value"] * 100},
            output_payload={"symbol": "BTCUSDT", "timeframe": "15m", "decision": "long"},
            metadata_json={},
            schema_valid=True,
            created_at=now,
            updated_at=now,
        )
    )
    db_session.commit()

    def fail_on_overview_decision_snapshot(*args, **kwargs):
        raise AssertionError("compact market view should not build decision snapshots")

    monkeypatch.setattr(dashboard_module, "_build_decision_snapshot", fail_on_overview_decision_snapshot)

    payload = dashboard_module.get_operator_dashboard(db_session, view="market")

    assert payload.control.last_decision_at == now
    assert payload.symbols[0].symbol == "BTCUSDT"
    assert payload.symbols[0].ai_decision.decision_run_id is None


def test_get_overview_uses_thin_runtime_summary(db_session, monkeypatch) -> None:
    import trading_mvp.services.dashboard as dashboard_module

    calls: list[dict[str, object]] = []
    original = dashboard_module.serialize_settings_runtime_summary

    def wrapped_runtime_summary(*args, **kwargs):
        calls.append(dict(kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(dashboard_module, "serialize_settings_runtime_summary", wrapped_runtime_summary)

    dashboard_module.get_overview(db_session)

    assert calls == [
        {
            "include_operational_status": False,
            "include_event_operator_control": False,
        }
    ]


def test_scheduler_operator_projection_uses_decision_fact_for_deep_history(db_session) -> None:
    from trading_mvp.models import AgentRun, DecisionPerformanceFact

    now = utcnow_naive()
    settings = get_or_create_settings(db_session)
    settings.default_symbol = "BTCUSDT"
    settings.tracked_symbols = ["BTCUSDT"]
    settings.default_timeframe = "15m"
    db_session.add(settings)

    target_run = AgentRun(
        role="trading_decision",
        trigger_event="realtime_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="openai",
        summary="deep btc long",
        input_payload={
            "features": {
                "regime": {
                    "primary_regime": "bullish",
                    "trend_alignment": "bullish_aligned",
                    "volatility_regime": "normal",
                }
            }
        },
        output_payload={
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "decision": "long",
            "confidence": 0.74,
            "rationale_codes": ["TREND_UP"],
            "explanation_short": "Deep history decision should come from fact lookup.",
        },
        metadata_json={
            "source": "llm",
            "last_ai_trigger_reason": "entry_candidate_event",
            "trigger_fingerprint": "btc-deep-history-fingerprint",
        },
        schema_valid=True,
        created_at=now - timedelta(hours=3),
        updated_at=now - timedelta(hours=3),
    )
    db_session.add(target_run)
    db_session.flush()

    db_session.add(
        DecisionPerformanceFact(
            decision_run_id=target_run.id,
            provider_name="openai",
            symbol="BTCUSDT",
            timeframe="15m",
            decision="long",
            rationale_codes=["TREND_UP"],
            regime="bullish",
            trend_alignment="bullish_aligned",
            telemetry_metadata={"source": "llm"},
            telemetry_output={"decision": "long"},
            created_at=target_run.created_at,
            updated_at=target_run.updated_at,
        )
    )
    db_session.add(
        MarketSnapshot(
            symbol="BTCUSDT",
            timeframe="15m",
            snapshot_time=now - timedelta(minutes=1),
            latest_price=70500.0,
            latest_volume=1200.0,
            candle_count=120,
            is_stale=False,
            is_complete=True,
            payload={},
        )
    )
    db_session.add(
        RiskCheck(
            symbol="BTCUSDT",
            decision_run_id=target_run.id,
            allowed=False,
            decision="long",
            reason_codes=["POSITION_STATE_STALE"],
            approved_risk_pct=0.0,
            approved_leverage=0.0,
            payload={"blocked_reason_codes": ["POSITION_STATE_STALE"]},
            created_at=now - timedelta(minutes=1),
        )
    )
    noise_count = OPERATOR_RECENT_ROW_SCAN_LIMIT + OPERATOR_EXTRACTED_SYMBOL_FALLBACK_SCAN_LIMIT + 5
    db_session.add_all(
        AgentRun(
            role="trading_decision",
            trigger_event="realtime_cycle",
            schema_name="TradeDecision",
            status="completed",
            provider_name="deterministic-mock",
            summary=f"noise {index}",
            input_payload={},
            output_payload={
                "symbol": f"NOISE{index}USDT",
                "timeframe": "15m",
                "decision": "hold",
                "rationale_codes": ["NOISE"],
            },
            metadata_json={},
            schema_valid=True,
            created_at=now - timedelta(seconds=index),
            updated_at=now - timedelta(seconds=index),
        )
        for index in range(noise_count)
    )
    db_session.flush()

    payload = get_operator_dashboard(db_session, view="scheduler")
    btc = next(item for item in payload.symbols if item.symbol == "BTCUSDT")

    assert btc.ai_decision.decision_run_id == target_run.id
    assert btc.ai_decision.last_ai_trigger_reason == "entry_candidate_event"
    assert btc.ai_decision.decision == "long"
    assert btc.risk_guard.blocked_reason_codes == ["POSITION_STATE_STALE"]
    assert btc.event_operator_control is None
    assert btc.execution.order_id is None
    assert btc.protection_status.status == "unknown"
    assert btc.audit_events == []

    decision_payload = get_operator_dashboard(db_session, view="decision")
    decision_btc = next(item for item in decision_payload.symbols if item.symbol == "BTCUSDT")

    assert decision_btc.ai_decision.decision_run_id == target_run.id
    assert decision_btc.risk_guard.blocked_reason_codes == ["POSITION_STATE_STALE"]
    assert decision_btc.audit_events == []

    full_payload = get_operator_dashboard(db_session)
    full_btc = next(item for item in full_payload.symbols if item.symbol == "BTCUSDT")

    assert full_btc.ai_decision.decision_run_id == target_run.id
    assert full_btc.risk_guard.blocked_reason_codes == ["POSITION_STATE_STALE"]


def test_scheduler_api_compact_omits_outcome(testclient_db_factory) -> None:
    TestingSessionLocal = testclient_db_factory("scheduler_compact.db")

    with TestingSessionLocal() as session:
        _seed_multi_symbol_operator_rows(session)
        session.commit()

    with TestClient(app) as client:
        compact_response = client.get("/api/scheduler?limit=20&compact=true")
        full_response = client.get("/api/scheduler?limit=20&include_payload=true")

    assert compact_response.status_code == 200
    assert full_response.status_code == 200
    compact_payload = compact_response.json()
    full_payload = full_response.json()

    assert compact_payload
    assert full_payload
    assert "outcome" not in compact_payload[0]
    assert "outcome" in full_payload[0]
    assert "ai_skip_reason" in compact_payload[0]
    assert compact_payload[0]["workflow"] == full_payload[0]["workflow"]


def test_market_inputs_api_compact_omits_payload_and_keeps_symbol_filter(testclient_db_factory) -> None:
    TestingSessionLocal = testclient_db_factory("market_inputs_compact.db")
    now = utcnow_naive()

    with TestingSessionLocal() as session:
        btc_market = MarketSnapshot(
            symbol="BTCUSDT",
            timeframe="15m",
            snapshot_time=now,
            latest_price=70000.0,
            latest_volume=1200.0,
            candle_count=1,
            is_stale=False,
            is_complete=True,
            payload={
                "candles": [
                    {
                        "timestamp": now.isoformat(),
                        "open": 69900.0,
                        "high": 70100.0,
                        "low": 69800.0,
                        "close": 70000.0,
                        "volume": 1200.0,
                    }
                ]
            },
        )
        eth_market = MarketSnapshot(
            symbol="ETHUSDT",
            timeframe="15m",
            snapshot_time=now - timedelta(minutes=1),
            latest_price=3400.0,
            latest_volume=900.0,
            candle_count=1,
            is_stale=False,
            is_complete=True,
            payload={"candles": []},
        )
        session.add_all([btc_market, eth_market])
        session.flush()
        session.add(
            FeatureSnapshot(
                symbol="BTCUSDT",
                timeframe="15m",
                market_snapshot_id=btc_market.id,
                feature_time=now,
                trend_score=1.2,
                volatility_pct=0.02,
                volume_ratio=1.4,
                drawdown_pct=0.01,
                rsi=58.0,
                atr=120.0,
                payload={
                    "multi_timeframe": {
                        "1h": {"trend_score": 1.1},
                        "4h": {"trend_score": 0.9},
                    }
                },
            )
        )
        session.commit()

    with TestClient(app) as client:
        compact_snapshots = client.get("/api/market/snapshots?limit=20&compact=true")
        full_btc_snapshots = client.get("/api/market/snapshots?limit=1&symbol=BTCUSDT&timeframe=15m")
        compact_features = client.get("/api/market/features?limit=20&compact=true")
        full_btc_features = client.get("/api/market/features?limit=1&symbol=BTCUSDT&timeframe=15m")

    assert compact_snapshots.status_code == 200
    assert full_btc_snapshots.status_code == 200
    assert compact_features.status_code == 200
    assert full_btc_features.status_code == 200

    compact_snapshot_payload = compact_snapshots.json()
    full_snapshot_payload = full_btc_snapshots.json()
    compact_feature_payload = compact_features.json()
    full_feature_payload = full_btc_features.json()

    assert {row["symbol"] for row in compact_snapshot_payload} == {"BTCUSDT", "ETHUSDT"}
    assert all("payload" not in row for row in compact_snapshot_payload)
    assert [row["symbol"] for row in full_snapshot_payload] == ["BTCUSDT"]
    assert full_snapshot_payload[0]["payload"]["candles"][0]["close"] == 70000.0
    assert all("payload" not in row for row in compact_feature_payload)
    assert compact_feature_payload[0]["available_timeframes"] == ["15m", "1h", "4h"]
    assert [row["symbol"] for row in full_feature_payload] == ["BTCUSDT"]
    assert full_feature_payload[0]["payload"]["multi_timeframe"]["1h"]["trend_score"] == 1.1


def test_decisions_api_compact_returns_operator_fields_without_raw_features(testclient_db_factory) -> None:
    TestingSessionLocal = testclient_db_factory("decisions_compact.db")

    with TestingSessionLocal() as session:
        _seed_multi_symbol_operator_rows(session)
        session.commit()

    with TestClient(app) as client:
        response = client.get("/api/decisions?limit=12&compact=true")

    assert response.status_code == 200
    payload = response.json()
    btc = next(item for item in payload if item["symbol"] == "BTCUSDT")

    assert btc["decision"] == "long"
    assert btc["timeframe"] == "15m"
    assert btc["ai_trigger_reason"] == "entry_candidate_event"
    assert "features" not in btc["input_payload"]
    assert "decision" in btc["output_payload"]
    assert "input_payload" in btc


def test_agents_api_compact_omits_raw_agent_payloads(testclient_db_factory) -> None:
    TestingSessionLocal = testclient_db_factory("agents_compact.db")

    with TestingSessionLocal() as session:
        _seed_multi_symbol_operator_rows(session)
        session.commit()

    with TestClient(app) as client:
        response = client.get("/api/agents?limit=12&compact=true")

    assert response.status_code == 200
    payload = response.json()
    btc = next(item for item in payload if item["summary"] == "btc blocked long")

    assert btc["payload_mode"] == "compact"
    assert btc["role"] == "trading_decision"
    assert btc["provider_name"] == "openai"
    assert btc["status"] == "completed"
    assert btc["output_payload"]["symbol"] == "BTCUSDT"
    assert btc["output_payload"]["decision"] == "long"
    assert "features" not in btc["input_payload"]
    assert "market_snapshot" not in btc["input_payload"]
    assert "event_risk_acknowledgement" not in btc["output_payload"]
    assert "active_position_prompt_route_context" in btc["metadata_json"]
    assert "active_position_entry_fingerprint_basis" not in btc["metadata_json"]


def test_risk_checks_api_compact_omits_debug_payload(testclient_db_factory) -> None:
    TestingSessionLocal = testclient_db_factory("risk_checks_compact.db")

    with TestingSessionLocal() as session:
        _seed_multi_symbol_operator_rows(session)
        session.commit()

    with TestClient(app) as client:
        response = client.get("/api/risk/checks?limit=12&compact=true")

    assert response.status_code == 200
    payload = response.json()
    btc = next(item for item in payload if item["symbol"] == "BTCUSDT")

    assert btc["payload_mode"] == "compact"
    assert btc["payload"]["allowed"] is False
    assert btc["payload"]["blocked_reason_codes"] == ["POSITION_STATE_STALE"]
    assert "debug_payload" not in btc["payload"]
    assert "exposure_metrics" not in btc["payload"]


def test_risk_checks_api_compact_includes_reason_evidence(testclient_db_factory) -> None:
    TestingSessionLocal = testclient_db_factory("risk_checks_reason_evidence_compact.db")

    with TestingSessionLocal() as session:
        session.add(
            RiskCheck(
                symbol="BTCUSDT",
                allowed=False,
                decision="short",
                reason_codes=["SYMBOL_RECENT_PERFORMANCE_NEGATIVE", "CORRELATED_EXPOSURE_LIMIT_REACHED"],
                approved_risk_pct=0.0,
                approved_leverage=0.0,
                payload={
                    "allowed": False,
                    "decision": "short",
                    "blocked_reason_codes": [
                        "SYMBOL_RECENT_PERFORMANCE_NEGATIVE",
                        "CORRELATED_EXPOSURE_LIMIT_REACHED",
                    ],
                    "debug_payload": {
                        "symbol_recent_performance_gate": {
                            "applied": True,
                            "status": "blocked",
                            "symbol": "BTCUSDT",
                            "lookback_days": 30,
                            "execution_count": 27,
                            "gross_realized_pnl": -2.843,
                            "fee_total": 3.373105,
                            "net_pnl_after_fees": -6.216105,
                            "raw_executions": [{"id": 1}],
                        },
                        "portfolio_exposure_gate": {
                            "status": "blocked",
                            "candidate_symbol": "BTCUSDT",
                            "candidate_direction": "short",
                            "combined_BTC_ETH_directional_exposure_pct": 2.993049,
                            "limits": {"max_same_direction_major_exposure_pct": 2.0},
                            "raw_positions": [{"symbol": "ETHUSDT"}],
                        },
                    },
                },
            )
        )
        session.commit()

    with TestClient(app) as client:
        response = client.get("/api/risk/checks?limit=1&compact=true")

    assert response.status_code == 200
    btc = response.json()[0]

    assert btc["payload_mode"] == "compact"
    assert "debug_payload" not in btc["payload"]
    evidence = btc["payload"]["reason_evidence"]
    symbol_gate = evidence["symbol_recent_performance_gate"]
    portfolio_gate = evidence["portfolio_exposure_gate"]
    assert symbol_gate["net_pnl_after_fees"] == -6.216105
    assert symbol_gate["execution_count"] == 27
    assert "raw_executions" not in symbol_gate
    assert portfolio_gate["combined_BTC_ETH_directional_exposure_pct"] == 2.993049
    assert portfolio_gate["limits"]["max_same_direction_major_exposure_pct"] == 2.0
    assert "raw_positions" not in portfolio_gate


def test_history_payload_endpoints_default_to_compact_with_raw_opt_in(testclient_db_factory) -> None:
    TestingSessionLocal = testclient_db_factory("history_payload_defaults_compact.db")

    with TestingSessionLocal() as session:
        _seed_multi_symbol_operator_rows(session)
        session.commit()

    with TestClient(app) as client:
        decisions_default = client.get("/api/decisions?limit=12")
        decisions_raw = client.get("/api/decisions?limit=12&include_payload=true")
        agents_default = client.get("/api/agents?limit=12")
        agents_raw = client.get("/api/agents?limit=12&include_payload=true")
        risk_default = client.get("/api/risk/checks?limit=12")
        risk_raw = client.get("/api/risk/checks?limit=12&include_payload=true")
        scheduler_default = client.get("/api/scheduler?limit=12")
        scheduler_raw = client.get("/api/scheduler?limit=12&compact=false")

    assert decisions_default.status_code == 200
    assert decisions_raw.status_code == 200
    assert agents_default.status_code == 200
    assert agents_raw.status_code == 200
    assert risk_default.status_code == 200
    assert risk_raw.status_code == 200
    assert scheduler_default.status_code == 200
    assert scheduler_raw.status_code == 200

    default_decision = next(item for item in decisions_default.json() if item["symbol"] == "BTCUSDT")
    raw_decision = next(item for item in decisions_raw.json() if item["symbol"] == "BTCUSDT")
    assert "features" not in default_decision["input_payload"]
    assert raw_decision["input_payload"]["features"]["trend_score"] == 1.48

    default_agent = next(item for item in agents_default.json() if item["summary"] == "btc blocked long")
    raw_agent = next(item for item in agents_raw.json() if item["summary"] == "btc blocked long")
    assert default_agent["payload_mode"] == "compact"
    assert "market_snapshot" not in default_agent["input_payload"]
    assert raw_agent["input_payload"]["market_snapshot"]["symbol"] == "BTCUSDT"

    default_risk = next(item for item in risk_default.json() if item["symbol"] == "BTCUSDT")
    raw_risk = next(item for item in risk_raw.json() if item["symbol"] == "BTCUSDT")
    assert default_risk["payload_mode"] == "compact"
    assert "debug_payload" not in default_risk["payload"]
    assert raw_risk["payload"]["debug_payload"]["slot_allocation"]["assigned_slot"] == "slot_1"

    default_scheduler = scheduler_default.json()[0]
    raw_scheduler = scheduler_raw.json()[0]
    assert "outcome" not in default_scheduler
    assert raw_scheduler["outcome"]["symbol"] in {"BTCUSDT", "ETHUSDT"}


def test_operator_snapshots_expose_psychology_scene_review(db_session) -> None:
    from trading_mvp.models import AgentRun
    from trading_mvp.services.dashboard import (
        _build_decision_snapshot,
        _build_pending_entry_plan_snapshot,
    )

    review = {
        "market_psychology": "Pullback buyers are waiting for confirmation.",
        "psychology_bias": "bullish",
        "scene_scenario": "Trend pullback needs a clean zone reclaim.",
        "scene_type": "trend_pullback",
        "entry_choreography": "Watch the zone and let the watcher require confirmation.",
        "preferred_entry_timing": "watch_zone",
        "confirmation_cues": ["zone_touch", "1m_reclaim"],
        "invalidation_cues": ["support_lost"],
        "reason_codes": ["SCENE_TREND_PULLBACK"],
        "summary": "Metadata-only scene review.",
        "execution_boundary": "metadata_only_no_order_authority",
        "future_extra_key": "ignored for strict snapshot compatibility",
    }
    decision_run = AgentRun(
        role="trading_decision",
        trigger_event="entry_candidate_event",
        schema_name="TradeDecision",
        status="completed",
        provider_name="openai",
        summary="scene review",
        input_payload={"ai_trigger": {"symbol": "BTCUSDT", "timeframe": "15m"}},
        output_payload={
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "decision": "hold",
            "confidence": 0.61,
            "rationale_codes": ["AI_WATCH_ENTRY_PLAN"],
            "psychology_scene_review": review,
        },
        metadata_json={},
        schema_valid=True,
    )
    db_session.add(decision_run)
    db_session.flush()
    plan = PendingEntryPlan(
        symbol="BTCUSDT",
        side="long",
        plan_status="armed",
        source_decision_run_id=decision_run.id,
        regime="bullish",
        posture="pullback_watch",
        rationale_codes=["AI_WATCH_ENTRY_PLAN"],
        source_timeframe="15m",
        entry_mode="pullback_confirm",
        entry_zone_min=69800.0,
        entry_zone_max=69950.0,
        invalidation_price=69400.0,
        max_chase_bps=12.0,
        idea_ttl_minutes=18,
        stop_loss=69400.0,
        take_profit=71000.0,
        risk_pct_cap=0.01,
        leverage_cap=1.0,
        expires_at=utcnow_naive() + timedelta(minutes=18),
        idempotency_key="scene-review-snapshot-test",
        metadata_json={"psychology_scene_review": review},
    )
    db_session.add(plan)
    db_session.flush()

    decision_snapshot = _build_decision_snapshot(decision_run)
    plan_snapshot = _build_pending_entry_plan_snapshot(plan)

    assert decision_snapshot.psychology_scene_review is not None
    assert decision_snapshot.psychology_scene_review.scene_type == "trend_pullback"
    assert decision_snapshot.psychology_scene_review.execution_boundary == "metadata_only_no_order_authority"
    assert plan_snapshot.psychology_scene_review is not None
    assert plan_snapshot.psychology_scene_review.preferred_entry_timing == "watch_zone"


def test_get_decisions_includes_decision_quality_payload(db_session) -> None:
    from trading_mvp.models import AgentRun, DecisionPerformanceFact

    decision_run = AgentRun(
        role="trading_decision",
        trigger_event="manual",
        schema_name="TradeDecision",
        status="completed",
        provider_name="openai",
        summary="quality row",
        input_payload={"ai_trigger": {"symbol": "BTCUSDT", "timeframe": "15m"}},
        output_payload={
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "decision": "long",
            "confidence": 0.82,
            "rationale_codes": ["TEST"],
        },
        metadata_json={"source": "llm"},
        schema_valid=True,
    )
    db_session.add(decision_run)
    db_session.flush()
    db_session.add(
        DecisionPerformanceFact(
            decision_run_id=decision_run.id,
            provider_name="openai",
            symbol="BTCUSDT",
            timeframe="15m",
            decision="long",
            rationale_codes=["TEST"],
            regime="bullish",
            trend_alignment="bullish_aligned",
            ai_used=True,
            ai_actionable=True,
            ai_blocked_by_risk=True,
            ai_usefulness_status="risk_blocked",
            ai_known_cost_usd=0.00042,
            ai_total_tokens=1234,
            expected_edge_bps=40.0,
            expected_total_cost_bps=12.0,
            net_expected_edge_bps=28.0,
            pnl_data_confidence="not_realized",
            telemetry_metadata={
                "scene_review": {
                    "has_review": True,
                    "scene_type": "trend_pullback",
                    "preferred_entry_timing": "watch_zone",
                },
                "scene_plan_outcome": {
                    "outcome_bucket": "waiting_confirm_quality_low",
                    "pending_plan_count": 1,
                    "plan_confirm_quality_low_count": 1,
                    "plan_invalidated_count": 0,
                    "order_observed": False,
                    "fill_observed": False,
                    "reason_codes": ["PLAN_CONFIRM_QUALITY_LOW"],
                },
            },
            telemetry_output={"decision": "long"},
        )
    )
    db_session.add(
        RiskCheck(
            symbol="BTCUSDT",
            decision_run_id=decision_run.id,
            allowed=False,
            decision="long",
            reason_codes=["EXPECTED_COST_EXCEEDS_EDGE"],
            approved_risk_pct=0.0,
            approved_leverage=0.0,
            payload={
                "debug_payload": {
                    "expected_cost_gate": {
                        "status": "blocked",
                        "expected_edge_bps": 40.0,
                        "expected_total_cost_bps": 12.0,
                        "net_expected_edge_bps": 28.0,
                    }
                }
            },
        )
    )
    db_session.flush()

    rows = get_decisions(db_session, limit=5, compact=True)
    row = next(item for item in rows if item["id"] == decision_run.id)
    quality = row["decision_quality"]

    assert quality["ai_usefulness_status"] == "risk_blocked"
    assert quality["ai_actionable"] is True
    assert quality["ai_blocked_by_risk"] is True
    assert quality["ai_known_cost_usd"] == pytest.approx(0.00042)
    assert quality["ai_total_tokens"] == 1234
    assert quality["net_expected_edge_bps"] == pytest.approx(28.0)
    assert quality["risk_allowed"] is False
    assert quality["risk_reason_codes"] == ["EXPECTED_COST_EXCEEDS_EDGE"]
    assert quality["scene_review"]["scene_type"] == "trend_pullback"
    assert quality["scene_plan_outcome"]["outcome_bucket"] == "waiting_confirm_quality_low"
    assert quality["scene_plan_outcome"]["plan_confirm_quality_low_count"] == 1


def test_risk_checks_api_includes_ai_trigger_summary(testclient_db_factory) -> None:
    TestingSessionLocal = testclient_db_factory("risk_checks_trigger_summary.db")

    with TestingSessionLocal() as session:
        _seed_multi_symbol_operator_rows(session)
        session.commit()

    with TestClient(app) as client:
        response = client.get("/api/risk/checks")

    assert response.status_code == 200
    payload = response.json()
    btc = next(item for item in payload if item["symbol"] == "BTCUSDT")
    eth = next(item for item in payload if item["symbol"] == "ETHUSDT")

    assert btc["ai_trigger_reason"] == "entry_candidate_event"
    assert btc["ai_review_type"] == "entry_candidate_review"
    assert btc["ai_trigger_reason_codes"] == ["ENTRY_CANDIDATE_SELECTED"]
    assert btc["ai_review"]["provider_status"] == "invoked"
    assert btc["market_signal_context"]["breakout_direction"] == "up"
    assert btc["risk_guard_result"]["allowed"] is False
    assert btc["market_signal_summary"] == btc["ai_trigger_summary"]
    assert "상단 돌파" in btc["ai_trigger_summary"]
    assert "거래량" in btc["ai_trigger_summary"]

    assert eth["ai_trigger_reason"] == "entry_candidate_event"
    assert eth["ai_review_type"] == "entry_candidate_review"
    assert eth["ai_trigger_reason_codes"] == ["ENTRY_CANDIDATE_SELECTED"]
    assert eth["ai_review"]["provider_status"] == "invoked"
    assert eth["risk_guard_result"]["allowed"] is True
    assert eth["market_signal_summary"] == eth["ai_trigger_summary"]
    assert "모멘텀" in eth["ai_trigger_summary"]
    assert "거래량" in eth["ai_trigger_summary"]


def test_overview_api_does_not_refresh_stale_exchange_sync_on_read_for_sqlite(
    testclient_db_factory,
    monkeypatch,
) -> None:
    TestingSessionLocal = testclient_db_factory("overview_sync_refresh.db")

    with TestingSessionLocal() as session:
        settings = get_or_create_settings(session)
        settings.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
        settings.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
        stale_at = utcnow_naive() - timedelta(hours=2)
        for scope in ("account", "positions", "open_orders", "protective_orders"):
            mark_sync_success(settings, scope=scope, synced_at=stale_at)
        session.commit()

    refresh_started = Event()
    def fake_refresh_exchange_sync(session, *, triggered_by: str):
        refresh_started.set()
        return {"workflow": "exchange_sync_cycle", "status": "success", "triggered_by": triggered_by}

    monkeypatch.setattr(
        "trading_mvp.main.maybe_refresh_exchange_sync_freshness",
        fake_refresh_exchange_sync,
    )
    monkeypatch.setattr("trading_mvp.main.READ_REFRESH_DISPATCH_DEBOUNCE_SECONDS", 0.0)
    monkeypatch.setattr("trading_mvp.main._exchange_sync_read_refresh_inflight", False)
    monkeypatch.setattr("trading_mvp.main._exchange_sync_read_refresh_last_started", 0.0)

    with TestClient(app) as client:
        response = client.get("/api/dashboard/overview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["sync_freshness_summary"]["account"]["stale"] is True
    assert refresh_started.wait(timeout=0.2) is False

    with TestingSessionLocal() as session:
        refreshed = get_overview(session)
        assert refreshed.sync_freshness_summary["account"]["stale"] is True
        assert refreshed.sync_freshness_summary["protective_orders"]["stale"] is True


def test_operator_api_does_not_refresh_stale_exchange_sync_on_read_for_sqlite(
    testclient_db_factory,
    monkeypatch,
) -> None:
    TestingSessionLocal = testclient_db_factory("operator_sync_refresh.db")

    with TestingSessionLocal() as session:
        _seed_multi_symbol_operator_rows(session)
        settings = get_or_create_settings(session)
        stale_at = utcnow_naive() - timedelta(hours=2)
        for scope in ("account", "positions", "open_orders", "protective_orders"):
            mark_sync_success(settings, scope=scope, synced_at=stale_at)
        session.commit()

    refresh_started = Event()

    def fake_refresh_exchange_sync(session, *, triggered_by: str):
        refresh_started.set()
        return {"workflow": "exchange_sync_cycle", "status": "success", "triggered_by": triggered_by}

    monkeypatch.setattr(
        "trading_mvp.main.maybe_refresh_exchange_sync_freshness",
        fake_refresh_exchange_sync,
    )
    monkeypatch.setattr("trading_mvp.main.READ_REFRESH_DISPATCH_DEBOUNCE_SECONDS", 0.0)
    monkeypatch.setattr("trading_mvp.main._exchange_sync_read_refresh_inflight", False)
    monkeypatch.setattr("trading_mvp.main._exchange_sync_read_refresh_last_started", 0.0)

    with TestClient(app) as client:
        response = client.get("/api/dashboard/operator")

    assert response.status_code == 200
    payload = response.json()
    assert payload["control"]["sync_freshness_summary"]["account"]["stale"] is True
    assert payload["control"]["sync_freshness_summary"]["protective_orders"]["stale"] is True
    assert refresh_started.wait(timeout=0.2) is False


def test_operator_dashboard_read_refresh_is_opt_in_for_postgres(monkeypatch) -> None:
    import trading_mvp.main as main_module

    started_threads: list[str] = []

    class _ThreadProbe:
        def __init__(self, *args, **kwargs) -> None:
            del args
            started_threads.append(str(kwargs.get("name") or "unnamed"))

        def start(self) -> None:
            started_threads.append("started")

    class _Payload:
        def model_dump(self, *, mode: str) -> dict[str, object]:
            assert mode == "json"
            return {"ok": True}

    monkeypatch.delenv("TRADING_MVP_ENABLE_READ_TRIGGER_EXCHANGE_SYNC", raising=False)
    monkeypatch.setattr(main_module, "engine", SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
    monkeypatch.setattr(main_module.threading, "Thread", _ThreadProbe)
    monkeypatch.setattr(main_module, "get_operator_dashboard", lambda db, view=None: _Payload())

    assert main_module.dashboard_operator(view="risk", db=object()) == {"ok": True}
    assert started_threads == []


def test_operator_dashboard_exposes_market_stream_source_truth(db_session) -> None:
    settings = get_or_create_settings(db_session)
    settings.default_symbol = "BTCUSDT"
    settings.default_timeframe = "15m"
    replace_market_stream_detail(
        settings,
        {
            "status": "degraded",
            "source": "binance_futures_market_stream",
            "stream_source": "binance_ws_final_kline",
            "subscribed_symbols": ["BTCUSDT"],
            "subscribed_timeframes": ["1m", "15m"],
            "stream_count": 2,
            "last_error": "socket dropped",
            "reason_code": "MARKET_STREAM_CONNECTION_DROPPED",
            "stream_enabled": True,
            "stream_running": False,
            "cache_backend": "redis",
            "configured_cache_backend": "redis",
            "cache_market_type": "usd_m_futures",
            "cache_environment": "mainnet",
            "cache_health": "unavailable",
            "cache_scope": "shared",
            "shared_cache_supported": True,
            "redis_required": False,
            "redis_configured": True,
            "redis_connected": False,
            "last_shared_cache_error": "redis down",
        },
    )
    db_session.add(
        MarketSnapshot(
            symbol="BTCUSDT",
            timeframe="15m",
            snapshot_time=utcnow_naive(),
            latest_price=70000.0,
            latest_volume=1200.0,
            candle_count=120,
            is_stale=False,
            is_complete=True,
            payload={
                "source": "binance_ws_final_kline",
                "source_status": "fresh",
                "source_detail": {
                    "active_snapshot_source": "redis",
                    "rest_bootstrap_used": True,
                    "partial_candle_used": False,
                    "used_fallback": False,
                    "fallback_active": False,
                    "fallback_reason": None,
                    "stale_reason": None,
                    "cache_backend": "redis",
                    "configured_cache_backend": "redis",
                    "cache_market_type": "usd_m_futures",
                    "cache_environment": "mainnet",
                    "cache_health": "ok",
                    "cache_scope": "shared",
                    "shared_cache_supported": True,
                    "redis_required": False,
                    "redis_configured": True,
                    "redis_connected": True,
                    "source_time": "2026-05-05T12:00:00",
                    "received_at": "2026-05-05T12:00:02",
                    "age_seconds": 2,
                },
            },
        )
    )
    db_session.commit()

    dashboard = get_operator_dashboard(db_session, view="market")
    summary = dashboard.control.market_freshness_summary

    assert summary["source"] == "binance_ws_final_kline"
    assert summary["active_snapshot_source"] == "redis"
    assert summary["source_status"] == "fresh"
    assert summary["source_detail"]["rest_bootstrap_used"] is True
    assert summary["stream"]["status"] == "degraded"
    assert summary["stream"]["reason_code"] == "MARKET_STREAM_CONNECTION_DROPPED"
    assert summary["stream_status"] == "degraded"
    assert summary["stream_enabled"] is True
    assert summary["stream_running"] is False
    assert summary["cache_backend"] == "redis"
    assert summary["configured_cache_backend"] == "redis"
    assert summary["cache_market_type"] == "usd_m_futures"
    assert summary["cache_environment"] == "mainnet"
    assert summary["cache_health"] == "ok"
    assert summary["cache_scope"] == "shared"
    assert summary["shared_cache_supported"] is True
    assert summary["redis_required"] is False
    assert summary["redis_configured"] is True
    assert summary["redis_connected"] is True
    assert summary["source_time"] == "2026-05-05T12:00:00"
    assert summary["received_at"] == "2026-05-05T12:00:02"
    assert summary["age_seconds"] == 2
    assert summary["used_fallback"] is False
    assert summary["fallback_active"] is False
    assert summary["fallback_reason"] is None
    assert summary["stale_reason"] is None


def test_operator_dashboard_distinguishes_configured_redis_from_rest_fallback_source(db_session) -> None:
    settings = get_or_create_settings(db_session)
    settings.default_symbol = "BTCUSDT"
    settings.default_timeframe = "15m"
    replace_market_stream_detail(
        settings,
        {
            "status": "degraded",
            "source": "binance_futures_market_stream",
            "stream_source": "binance_ws_final_kline",
            "reason_code": "MARKET_STREAM_STALE",
            "stream_enabled": True,
            "stream_running": False,
            "cache_backend": "redis",
            "configured_cache_backend": "redis",
            "cache_health": "unavailable",
            "cache_scope": "shared",
            "shared_cache_supported": True,
            "redis_required": False,
            "redis_configured": True,
            "redis_connected": False,
            "last_shared_cache_error": "Timeout connecting to server",
        },
    )
    db_session.add(
        MarketSnapshot(
            symbol="BTCUSDT",
            timeframe="15m",
            snapshot_time=utcnow_naive(),
            latest_price=70000.0,
            latest_volume=1200.0,
            candle_count=120,
            is_stale=False,
            is_complete=True,
            payload={
                "source": "binance_rest",
                "source_status": "rest_fallback",
                "source_detail": {
                    "source": "binance_rest",
                    "active_snapshot_source": "binance_rest",
                    "rest_bootstrap_used": True,
                    "used_fallback": True,
                    "fallback_active": True,
                    "fallback_reason": "market_stream_cache_unavailable",
                    "stale_reason": "market_stream_cache_unavailable",
                    "cache_backend": "redis",
                    "configured_cache_backend": "redis",
                    "cache_health": "unavailable",
                    "cache_scope": "shared",
                    "shared_cache_supported": True,
                    "redis_required": False,
                    "redis_configured": True,
                    "redis_connected": False,
                    "partial_candle_used": False,
                    "stream": {
                        "status": "degraded",
                        "reason_code": "MARKET_STREAM_STALE",
                        "cache_backend": "redis",
                        "configured_cache_backend": "redis",
                        "cache_health": "unavailable",
                        "cache_scope": "shared",
                        "redis_required": False,
                        "redis_configured": True,
                        "redis_connected": False,
                    },
                },
            },
        )
    )
    db_session.commit()

    dashboard = get_operator_dashboard(db_session, view="market")
    summary = dashboard.control.market_freshness_summary

    assert summary["source"] == "binance_rest"
    assert summary["active_snapshot_source"] == "binance_rest"
    assert summary["source_status"] == "rest_fallback"
    assert summary["status"] == "fresh"
    assert summary["stale"] is False
    assert summary["incomplete"] is False
    assert summary["configured_cache_backend"] == "redis"
    assert summary["cache_backend"] == "redis"
    assert summary["cache_health"] == "unavailable"
    assert summary["cache_scope"] == "shared"
    assert summary["redis_required"] is False
    assert summary["redis_configured"] is True
    assert summary["redis_connected"] is False
    assert summary["used_fallback"] is True
    assert summary["fallback_active"] is True
    assert summary["fallback_reason"] == "market_stream_cache_unavailable"
    assert summary["stale_reason"] == "market_stream_cache_unavailable"


def test_audit_api_returns_event_category(testclient_db_factory) -> None:
    TestingSessionLocal = testclient_db_factory("audit_categories.db")

    with TestingSessionLocal() as session:
        session.add(
            AuditEvent(
                event_type="live_execution_rejected",
                entity_type="order",
                entity_id="1",
                severity="warning",
                message="Execution rejected.",
                payload={},
            )
        )
        session.commit()

    with TestClient(app) as client:
        response = client.get("/api/audit?search=live_execution_rejected&limit=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["event_type"] == "live_execution_rejected"
    assert payload[0]["event_category"] == "execution"


def test_audit_api_compact_list_omits_payload_and_detail_returns_it(testclient_db_factory) -> None:
    TestingSessionLocal = testclient_db_factory("audit_compact_detail.db")

    with TestingSessionLocal() as session:
        event = AuditEvent(
            event_type="risk_blocked",
            entity_type="risk_check",
            entity_id="789",
            severity="warning",
            message="Risk guard blocked the entry.",
            payload={"risk_id": 789, "symbol": "BTCUSDT", "raw": {"large": "payload"}},
        )
        session.add(event)
        session.commit()
        event_id = event.id

    with TestClient(app) as client:
        list_response = client.get("/api/audit?tab=risk&severity=warning&q=risk&limit=5&compact=true")
        detail_response = client.get(f"/api/audit/{event_id}")

    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert len(list_payload) == 1
    assert list_payload[0]["id"] == event_id
    assert list_payload[0]["event_category"] == "risk"
    assert list_payload[0]["related_type"] == "risk_id"
    assert "payload" not in list_payload[0]

    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["payload"]["raw"]["large"] == "payload"
    assert detail_payload["event_category"] == "risk"
