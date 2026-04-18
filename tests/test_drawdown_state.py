from __future__ import annotations

from datetime import timedelta

from trading_mvp.models import PnLSnapshot
from trading_mvp.services.drawdown_state import (
    DRAWDOWN_STATE_CAUTION,
    DRAWDOWN_STATE_CONTAINMENT,
    DRAWDOWN_STATE_NORMAL,
    DRAWDOWN_STATE_RECOVERY,
    build_drawdown_state_snapshot,
)
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive


def _seed_pnl_snapshot(
    db_session,
    *,
    equity: float,
    net_pnl: float,
    daily_pnl: float,
    consecutive_losses: int,
    minutes_ago: int,
) -> None:
    created_at = utcnow_naive() - timedelta(minutes=minutes_ago)
    row = PnLSnapshot(
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
    db_session.add(row)


def test_drawdown_state_enters_caution_from_recent_losses(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _seed_pnl_snapshot(db_session, equity=100000.0, net_pnl=0.0, daily_pnl=0.0, consecutive_losses=0, minutes_ago=40)
    _seed_pnl_snapshot(db_session, equity=98200.0, net_pnl=-1800.0, daily_pnl=-1000.0, consecutive_losses=2, minutes_ago=5)
    db_session.flush()

    state = build_drawdown_state_snapshot(db_session, settings_row)

    assert state["current_drawdown_state"] == DRAWDOWN_STATE_CAUTION
    assert state["transition_reason"] in {"consecutive_losses_warning", "recent_net_pnl_warning", "drawdown_depth_warning"}
    assert state["policy_adjustments"]["risk_pct_multiplier"] == 0.75
    assert state["policy_adjustments"]["winner_only_pyramiding"] is True


def test_drawdown_state_enters_containment_on_deeper_drawdown(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _seed_pnl_snapshot(db_session, equity=100000.0, net_pnl=0.0, daily_pnl=0.0, consecutive_losses=0, minutes_ago=40)
    _seed_pnl_snapshot(db_session, equity=95800.0, net_pnl=-4200.0, daily_pnl=-1800.0, consecutive_losses=2, minutes_ago=5)
    db_session.flush()

    state = build_drawdown_state_snapshot(db_session, settings_row)

    assert state["current_drawdown_state"] == DRAWDOWN_STATE_CONTAINMENT
    assert state["policy_adjustments"]["risk_pct_multiplier"] == 0.5
    assert state["policy_adjustments"]["breakout_exception_allowed"] is False


def test_drawdown_state_moves_to_recovery_after_positive_progress(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _seed_pnl_snapshot(db_session, equity=100000.0, net_pnl=0.0, daily_pnl=0.0, consecutive_losses=0, minutes_ago=60)
    _seed_pnl_snapshot(db_session, equity=95200.0, net_pnl=-4800.0, daily_pnl=-2200.0, consecutive_losses=3, minutes_ago=30)
    _seed_pnl_snapshot(db_session, equity=97500.0, net_pnl=-2500.0, daily_pnl=300.0, consecutive_losses=1, minutes_ago=5)
    db_session.flush()

    containment_state = {
        "current_drawdown_state": DRAWDOWN_STATE_CONTAINMENT,
        "entered_at": (utcnow_naive() - timedelta(minutes=30)).isoformat(),
        "transition_reason": "drawdown_depth_threshold",
        "policy_adjustments": {},
        "peak_equity": 100000.0,
        "trough_equity": 95200.0,
    }

    state = build_drawdown_state_snapshot(db_session, settings_row, current_detail=containment_state)

    assert state["current_drawdown_state"] == DRAWDOWN_STATE_RECOVERY
    assert state["transition_reason"] == "recovery_progress_positive"
    assert state["recovery_progress"] >= 0.25


def test_drawdown_state_returns_to_normal_after_recovery_completes(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _seed_pnl_snapshot(db_session, equity=100000.0, net_pnl=0.0, daily_pnl=0.0, consecutive_losses=0, minutes_ago=60)
    _seed_pnl_snapshot(db_session, equity=99750.0, net_pnl=-250.0, daily_pnl=150.0, consecutive_losses=0, minutes_ago=5)
    db_session.flush()

    recovery_state = {
        "current_drawdown_state": DRAWDOWN_STATE_RECOVERY,
        "entered_at": (utcnow_naive() - timedelta(minutes=20)).isoformat(),
        "transition_reason": "recovery_progress_positive",
        "policy_adjustments": {},
        "peak_equity": 100000.0,
        "trough_equity": 95000.0,
    }

    state = build_drawdown_state_snapshot(db_session, settings_row, current_detail=recovery_state)

    assert state["current_drawdown_state"] == DRAWDOWN_STATE_NORMAL
    assert state["transition_reason"] == "recovered_to_normal"
    assert state["policy_adjustments"]["breakout_exception_allowed"] is True

