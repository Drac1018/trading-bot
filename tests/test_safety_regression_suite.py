from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from trading_mvp.models import AuditEvent, Order, Position
from trading_mvp.services.execution import (
    apply_position_management,
    execute_live_trade,
    sync_live_state,
)
from trading_mvp.services.market_data import build_market_snapshot
from trading_mvp.services.range_mr_cooldown import (
    RANGE_MR_CONSECUTIVE_FAILURES_REASON_CODE,
    RANGE_MR_COOLDOWN_BLOCK_REASON_CODE,
)
from trading_mvp.services.risk import (
    AI_DECISION_EXPIRED_REASON_CODE,
    AI_DECISION_PRICE_MOVE_INVALIDATED_REASON_CODE,
    CORRELATED_EXPOSURE_LIMIT_REASON_CODE,
    evaluate_risk,
)
from trading_mvp.services.runtime_state import mark_sync_issue
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive

from tests.test_execution_safety import (
    PositionManagementStopClient,
    ProtectiveHealthExitClient,
    ProtectiveHealthSyncClient,
    _add_breakeven_position,
    _breakeven_context,
    _connected_user_stream_payload,
    _feature_payload,
    _live_decision,
    _market_snapshot,
    _patch_breakeven_runtime,
    _prime_live_settings,
    _protective_health_orders,
    _risk_result,
)
from tests.test_risk_engine import (
    _add_ai_decision_run,
    _closed_range_mr_position,
    _entry_decision,
    _mark_all_sync_scopes_fresh,
    _mock_expected_edge_gate_settings,
    _range_mr_context,
    _seed_account_equity,
    _triggerable_decision,
)

FRESHNESS_BLOCKERS = {
    "ACCOUNT_STATE_STALE",
    "POSITION_STATE_STALE",
    "OPEN_ORDERS_STATE_STALE",
    "PROTECTION_STATE_UNVERIFIED",
    "MARKET_STATE_STALE",
    "MARKET_STATE_INCOMPLETE",
}


def test_regression_stale_market_data_blocks_new_entry(db_session) -> None:
    _prime_live_settings(db_session)
    settings_row = get_or_create_settings(db_session)
    _seed_account_equity(db_session)
    _mark_all_sync_scopes_fresh(settings_row)
    db_session.flush()
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140, force_stale=True)

    result, risk_row = evaluate_risk(
        db_session,
        settings_row,
        _triggerable_decision(snapshot),
        snapshot,
        execution_mode="live",
    )

    assert result.allowed is False
    assert "MARKET_STATE_STALE" in result.reason_codes
    assert "MARKET_STATE_STALE" in risk_row.reason_codes


def test_regression_incomplete_account_and_position_sync_blocks_new_entry(db_session) -> None:
    _prime_live_settings(db_session)
    settings_row = get_or_create_settings(db_session)
    _seed_account_equity(db_session)
    _mark_all_sync_scopes_fresh(settings_row)
    mark_sync_issue(settings_row, scope="account", status="incomplete", reason_code="ACCOUNT_STATE_STALE")
    db_session.flush()
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)

    account_result, account_risk_row = evaluate_risk(
        db_session,
        settings_row,
        _triggerable_decision(snapshot),
        snapshot,
        execution_mode="live",
    )

    assert account_result.allowed is False
    assert "ACCOUNT_STATE_STALE" in account_result.reason_codes
    assert "ACCOUNT_STATE_STALE" in account_risk_row.reason_codes

    _mark_all_sync_scopes_fresh(settings_row)
    mark_sync_issue(settings_row, scope="positions", status="incomplete", reason_code="POSITION_STATE_STALE")
    db_session.flush()

    position_result, position_risk_row = evaluate_risk(
        db_session,
        settings_row,
        _triggerable_decision(snapshot),
        snapshot,
        execution_mode="live",
    )

    assert position_result.allowed is False
    assert "POSITION_STATE_STALE" in position_result.reason_codes
    assert "POSITION_STATE_STALE" in position_risk_row.reason_codes


def test_regression_ai_decision_ttl_expiry_blocks_new_entry_and_audits(db_session) -> None:
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
        generated_at=utcnow_naive() - timedelta(minutes=30),
        ttl_seconds=60,
    )

    result, risk_row = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        decision_run_id=decision_run.id,
        market_snapshot_id=3001,
    )

    event = db_session.query(AuditEvent).filter_by(event_type="ai_decision_expired").one()
    assert result.allowed is False
    assert AI_DECISION_EXPIRED_REASON_CODE in result.reason_codes
    assert event.entity_id == str(decision_run.id)
    assert event.payload["risk_check_id"] == risk_row.id
    assert AI_DECISION_EXPIRED_REASON_CODE in event.payload["reason_codes"]


def test_regression_ai_decision_invalidated_blocks_new_entry_and_audits(db_session) -> None:
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
        market_snapshot_id=3002,
    )

    event = db_session.query(AuditEvent).filter_by(event_type="ai_decision_invalidated").one()
    assert result.allowed is False
    assert AI_DECISION_PRICE_MOVE_INVALIDATED_REASON_CODE in result.reason_codes
    assert event.entity_id == str(decision_run.id)
    assert AI_DECISION_PRICE_MOVE_INVALIDATED_REASON_CODE in event.payload["reason_codes"]


@pytest.mark.parametrize(
    ("case_name", "expected_reason_code"),
    [
        ("sl_missing", "PROTECTIVE_STOP_LOSS_MISSING"),
        ("reduce_only_missing", "PROTECTIVE_ORDER_REDUCE_ONLY_MISSING"),
    ],
)
def test_regression_protective_order_health_blocks_entries_and_records_critical_audit(
    monkeypatch,
    db_session,
    case_name: str,
    expected_reason_code: str,
) -> None:
    _prime_live_settings(db_session)
    settings_row = get_or_create_settings(db_session)
    client = ProtectiveHealthSyncClient(_protective_health_orders(case_name))
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: client)
    monkeypatch.setattr(
        "trading_mvp.services.execution.poll_live_user_stream",
        lambda *args, **kwargs: _connected_user_stream_payload(),
    )

    sync_result = sync_live_state(
        db_session,
        settings_row,
        symbol="BTCUSDT",
        allow_protection_recovery=False,
    )
    risk_result, _ = evaluate_risk(
        db_session,
        settings_row,
        _live_decision("long"),
        _market_snapshot(),
        execution_mode="live",
    )
    db_session.flush()

    event = db_session.query(AuditEvent).filter_by(event_type="protective_order_health_check_failed").one()
    assert expected_reason_code in sync_result["symbol_protection_state"]["BTCUSDT"]["reason_codes"]
    assert risk_result.allowed is False
    assert "PROTECTION_STATE_UNVERIFIED" in risk_result.reason_codes
    assert event.severity == "critical"
    assert expected_reason_code in event.payload["reason_codes"]


def test_regression_btc_eth_same_direction_exposure_limit_blocks_new_entry(monkeypatch, db_session) -> None:
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

    result, risk_row = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
    )

    event = db_session.query(AuditEvent).filter_by(event_type="correlated_exposure_blocked").one()
    assert result.allowed is False
    assert CORRELATED_EXPOSURE_LIMIT_REASON_CODE in result.reason_codes
    assert event.entity_id == str(risk_row.id)
    assert event.payload["reason_code"] == CORRELATED_EXPOSURE_LIMIT_REASON_CODE


def test_regression_expected_edge_shadow_records_would_block_without_blocking(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    price = float(snapshot.latest_price)
    decision = _entry_decision(
        entry_zone_min=price,
        entry_zone_max=price,
        stop_loss=price * 0.99,
        take_profit=price * 1.02,
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
    event = db_session.query(AuditEvent).filter_by(event_type="risk_expected_edge_gate").one()
    assert result.allowed is True
    assert gate["mode"] == "shadow"
    assert gate["would_block"] is True
    assert "EXPECTED_EDGE_MIN_NET_NOT_MET" in gate["would_block_reason_codes"]
    assert event.entity_id == str(risk_row.id)
    assert event.payload["would_block"] is True
    assert event.payload["enforced_reason_codes"] == []


def test_regression_expected_edge_enabled_blocks_new_entry(monkeypatch, db_session) -> None:
    _mock_expected_edge_gate_settings(monkeypatch, enabled=True, shadow=False)
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    price = float(snapshot.latest_price)
    decision = _entry_decision(
        entry_zone_min=price,
        entry_zone_max=price,
        stop_loss=price * 0.99,
        take_profit=price * 1.02,
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
    assert gate["would_block"] is True


def test_regression_range_mean_reversion_cooldown_blocks_same_direction_entry(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    settings_row.live_trading_enabled = False
    _seed_account_equity(db_session)
    _closed_range_mr_position(db_session, side="long", closed_minutes_ago=20, net_r_multiple=-0.5)
    _closed_range_mr_position(db_session, side="long", closed_minutes_ago=5, net_r_multiple=-0.3)
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    price = float(snapshot.latest_price)
    decision = _entry_decision(
        symbol="BTCUSDT",
        decision="long",
        entry_zone_min=price,
        entry_zone_max=price,
        entry_mode="pullback_confirm",
        stop_loss=price * 0.997,
        take_profit=price * 1.008,
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

    assert result.allowed is False
    assert RANGE_MR_COOLDOWN_BLOCK_REASON_CODE in result.reason_codes
    assert RANGE_MR_CONSECUTIVE_FAILURES_REASON_CODE in result.reason_codes
    assert db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "range_mean_reversion_cooldown_blocked")
    )


def test_regression_breakeven_disabled_does_not_move_stop(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    settings_row = get_or_create_settings(db_session)
    settings_row.break_even_enabled = False
    _add_breakeven_position(db_session, side="long")
    client = PositionManagementStopClient()
    _patch_breakeven_runtime(monkeypatch, shadow=False)
    monkeypatch.setattr(
        "trading_mvp.services.execution.build_position_management_context",
        lambda position, *, feature_payload, settings_row: _breakeven_context(candidate_stop=70000.0),
    )

    result = apply_position_management(
        db_session,
        settings_row,
        symbol="BTCUSDT",
        feature_payload=_feature_payload(),
        decision_run_id=51,
        client=client,
    )
    db_session.flush()

    refreshed = db_session.scalar(select(Position).where(Position.symbol == "BTCUSDT"))
    assert result["status"] == "blocked"
    assert refreshed is not None and refreshed.stop_loss == 69000.0
    assert not any(call["order_type"] == "STOP_MARKET" for call in client.new_order_calls)
    assert db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "breakeven_stop_move_evaluated")
    )


def test_regression_breakeven_enabled_moves_stop_after_risk_approval(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    settings_row = get_or_create_settings(db_session)
    settings_row.break_even_enabled = True
    _add_breakeven_position(db_session, side="long")
    client = PositionManagementStopClient()
    _patch_breakeven_runtime(monkeypatch, shadow=False)
    monkeypatch.setattr(
        "trading_mvp.services.execution.build_position_management_context",
        lambda position, *, feature_payload, settings_row: _breakeven_context(candidate_stop=70000.0),
    )

    result = apply_position_management(
        db_session,
        settings_row,
        symbol="BTCUSDT",
        feature_payload=_feature_payload(),
        decision_run_id=53,
        client=client,
    )
    db_session.flush()

    refreshed = db_session.scalar(select(Position).where(Position.symbol == "BTCUSDT"))
    stop_order = db_session.scalar(select(Order).where(Order.external_order_id == "stop-tightened"))
    assert result["status"] == "applied"
    assert refreshed is not None and refreshed.stop_loss == 70000.0
    assert stop_order is not None and stop_order.reduce_only is True and stop_order.close_only is True
    assert client.new_order_calls[-1]["reduce_only"] is True
    assert client.new_order_calls[-1]["close_position"] is True
    assert db_session.scalar(select(AuditEvent).where(AuditEvent.event_type == "breakeven_stop_move_applied"))


@pytest.mark.parametrize("decision_name", ["reduce", "exit"])
def test_regression_survival_paths_ignore_new_entry_blockers(db_session, decision_name: str) -> None:
    _prime_live_settings(db_session)
    settings_row = get_or_create_settings(db_session)
    mark_sync_issue(settings_row, scope="account", status="failed", reason_code="ACCOUNT_STATE_STALE")
    mark_sync_issue(settings_row, scope="positions", status="failed", reason_code="POSITION_STATE_STALE")
    mark_sync_issue(
        settings_row,
        scope="protective_orders",
        status="incomplete",
        reason_code="PROTECTION_STATE_UNVERIFIED",
    )
    db_session.flush()
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140, force_stale=True).model_copy(
        update={"is_complete": False}
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        _entry_decision(decision=decision_name, entry_mode="none", max_chase_bps=None),
        snapshot,
        execution_mode="live",
    )

    assert result.allowed is True
    assert FRESHNESS_BLOCKERS.isdisjoint(result.reason_codes)


def test_regression_protective_health_block_does_not_block_reduce_only_execution(monkeypatch, db_session) -> None:
    _prime_live_settings(db_session)
    settings_row = get_or_create_settings(db_session)
    client = ProtectiveHealthExitClient(_protective_health_orders("reduce_only_missing"))
    monkeypatch.setattr("trading_mvp.services.execution._build_client", lambda settings: client)

    result = execute_live_trade(
        db_session,
        settings_row,
        decision_run_id=77,
        decision=_live_decision("exit"),
        market_snapshot=_market_snapshot(),
        risk_result=_risk_result("exit"),
    )
    db_session.flush()

    assert result["status"] == "filled"
    assert result["intent_type"] == "reduce_only"
    assert client.exit_submitted is True
