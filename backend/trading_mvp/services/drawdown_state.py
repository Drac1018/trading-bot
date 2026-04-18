from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from trading_mvp.models import PnLSnapshot, Setting
from trading_mvp.services.account import get_latest_pnl_snapshot
from trading_mvp.time_utils import utcnow_naive

DRAWDOWN_STATE_NORMAL = "normal"
DRAWDOWN_STATE_CAUTION = "caution"
DRAWDOWN_STATE_CONTAINMENT = "drawdown_containment"
DRAWDOWN_STATE_RECOVERY = "recovery"

DRAWDOWN_ACTIVE_STATES = {
    DRAWDOWN_STATE_CAUTION,
    DRAWDOWN_STATE_CONTAINMENT,
    DRAWDOWN_STATE_RECOVERY,
}

RECENT_SNAPSHOT_LIMIT = 21
RECENT_NET_PNL_LOOKBACK = 5
CAUTION_RECENT_NET_PNL_PCT_THRESHOLD = -0.01
CAUTION_DRAWDOWN_DEPTH_PCT_THRESHOLD = 0.02
CAUTION_CONSECUTIVE_LOSSES_THRESHOLD = 2
CONTAINMENT_RECENT_NET_PNL_PCT_THRESHOLD = -0.02
CONTAINMENT_DRAWDOWN_DEPTH_PCT_THRESHOLD = 0.04
CONTAINMENT_COMBINED_DRAWDOWN_DEPTH_PCT_THRESHOLD = 0.025
CONTAINMENT_CONSECUTIVE_LOSSES_THRESHOLD = 2
RECOVERY_PROGRESS_READY_THRESHOLD = 0.25
NORMAL_RECOVERY_PROGRESS_THRESHOLD = 0.75
NORMAL_DRAWDOWN_DEPTH_PCT_THRESHOLD = 0.01

DRAWDOWN_POLICY_MAP: dict[str, dict[str, Any]] = {
    DRAWDOWN_STATE_NORMAL: {
        "risk_pct_multiplier": 1.0,
        "leverage_multiplier": 1.0,
        "notional_multiplier": 1.0,
        "max_non_priority_selected": 3,
        "entry_capacity_multiplier": 1.0,
        "entry_score_threshold_uplift": 0.0,
        "winner_only_pyramiding": False,
        "breakout_exception_allowed": True,
    },
    DRAWDOWN_STATE_CAUTION: {
        "risk_pct_multiplier": 0.75,
        "leverage_multiplier": 0.85,
        "notional_multiplier": 0.8,
        "max_non_priority_selected": 2,
        "entry_capacity_multiplier": 0.75,
        "entry_score_threshold_uplift": 0.03,
        "winner_only_pyramiding": True,
        "breakout_exception_allowed": False,
    },
    DRAWDOWN_STATE_CONTAINMENT: {
        "risk_pct_multiplier": 0.5,
        "leverage_multiplier": 0.65,
        "notional_multiplier": 0.55,
        "max_non_priority_selected": 1,
        "entry_capacity_multiplier": 0.5,
        "entry_score_threshold_uplift": 0.08,
        "winner_only_pyramiding": True,
        "breakout_exception_allowed": False,
    },
    DRAWDOWN_STATE_RECOVERY: {
        "risk_pct_multiplier": 0.65,
        "leverage_multiplier": 0.8,
        "notional_multiplier": 0.7,
        "max_non_priority_selected": 1,
        "entry_capacity_multiplier": 0.6,
        "entry_score_threshold_uplift": 0.05,
        "winner_only_pyramiding": True,
        "breakout_exception_allowed": False,
    },
}

STATE_ADJUSTMENT_REASON_CODES = {
    DRAWDOWN_STATE_CAUTION: "DRAWDOWN_STATE_CAUTION",
    DRAWDOWN_STATE_CONTAINMENT: "DRAWDOWN_STATE_CONTAINMENT",
    DRAWDOWN_STATE_RECOVERY: "DRAWDOWN_STATE_RECOVERY",
}


def _coerce_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _serialize_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _recent_snapshots(session: Session) -> list[PnLSnapshot]:
    rows = list(
        session.scalars(
            select(PnLSnapshot)
            .order_by(desc(PnLSnapshot.created_at), desc(PnLSnapshot.id))
            .limit(RECENT_SNAPSHOT_LIMIT)
        )
    )
    rows.reverse()
    return rows


def _recent_net_pnl_delta(latest: PnLSnapshot, history: list[PnLSnapshot]) -> float:
    if not history:
        return float(latest.net_pnl or latest.cumulative_pnl or 0.0)
    if len(history) <= RECENT_NET_PNL_LOOKBACK:
        baseline = history[0]
    else:
        baseline = history[-(RECENT_NET_PNL_LOOKBACK + 1)]
    latest_net = float(latest.net_pnl or latest.cumulative_pnl or 0.0)
    baseline_net = float(baseline.net_pnl or baseline.cumulative_pnl or 0.0)
    return latest_net - baseline_net


def build_drawdown_state_snapshot(
    session: Session,
    settings_row: Setting,
    *,
    current_detail: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    observed_at = now or utcnow_naive()
    latest_pnl = get_latest_pnl_snapshot(session, settings_row)
    history = _recent_snapshots(session)
    previous_detail = dict(current_detail or {})
    previous_state = str(previous_detail.get("current_drawdown_state") or DRAWDOWN_STATE_NORMAL).strip().lower()
    if previous_state not in DRAWDOWN_POLICY_MAP:
        previous_state = DRAWDOWN_STATE_NORMAL

    equities = [float(row.equity or 0.0) for row in history if float(row.equity or 0.0) > 0.0]
    current_equity = max(float(latest_pnl.equity or 0.0), 1.0)
    peak_equity_recent = max(equities, default=current_equity)
    previous_peak_equity = _coerce_float(previous_detail.get("peak_equity"), peak_equity_recent)
    previous_trough_equity = _coerce_float(previous_detail.get("trough_equity"), current_equity)

    if previous_state in DRAWDOWN_ACTIVE_STATES:
        peak_equity = max(previous_peak_equity, peak_equity_recent, current_equity)
        trough_equity = min(
            value
            for value in (
                previous_trough_equity if previous_trough_equity > 0 else current_equity,
                current_equity,
                min(equities, default=current_equity),
            )
            if value > 0
        )
    else:
        peak_equity = max(peak_equity_recent, current_equity)
        trough_equity = current_equity

    drawdown_depth_pct = max(0.0, (peak_equity - current_equity) / max(peak_equity, 1.0))
    recent_net_pnl = _recent_net_pnl_delta(latest_pnl, history)
    recent_net_pnl_pct = recent_net_pnl / max(current_equity, 1.0)
    latest_daily_pnl = float(latest_pnl.daily_pnl or 0.0)
    consecutive_losses = int(latest_pnl.consecutive_losses or 0)
    if peak_equity - trough_equity > 1e-9:
        recovery_progress = max(0.0, min(1.0, (current_equity - trough_equity) / (peak_equity - trough_equity)))
    else:
        recovery_progress = 1.0 if drawdown_depth_pct <= 1e-9 else 0.0

    containment_recent_pnl_trigger = (
        recent_net_pnl_pct <= CONTAINMENT_RECENT_NET_PNL_PCT_THRESHOLD and latest_daily_pnl < 0.0
    )
    containment_trigger = (
        containment_recent_pnl_trigger
        or drawdown_depth_pct >= CONTAINMENT_DRAWDOWN_DEPTH_PCT_THRESHOLD
        or (
            consecutive_losses >= CONTAINMENT_CONSECUTIVE_LOSSES_THRESHOLD
            and drawdown_depth_pct >= CONTAINMENT_COMBINED_DRAWDOWN_DEPTH_PCT_THRESHOLD
        )
    )
    caution_trigger = (
        containment_trigger
        or recent_net_pnl_pct <= CAUTION_RECENT_NET_PNL_PCT_THRESHOLD
        or drawdown_depth_pct >= CAUTION_DRAWDOWN_DEPTH_PCT_THRESHOLD
        or consecutive_losses >= CAUTION_CONSECUTIVE_LOSSES_THRESHOLD
    )
    recovery_ready = (
        previous_state in DRAWDOWN_ACTIVE_STATES
        and not containment_trigger
        and (recent_net_pnl >= 0.0 or latest_daily_pnl >= 0.0)
        and consecutive_losses <= 1
        and (
            recovery_progress >= RECOVERY_PROGRESS_READY_THRESHOLD
            or drawdown_depth_pct <= CAUTION_DRAWDOWN_DEPTH_PCT_THRESHOLD
        )
    )
    normal_ready = (
        previous_state == DRAWDOWN_STATE_RECOVERY
        and not containment_trigger
        and consecutive_losses == 0
        and (recent_net_pnl >= 0.0 or latest_daily_pnl >= 0.0)
        and (
            recovery_progress >= NORMAL_RECOVERY_PROGRESS_THRESHOLD
            or drawdown_depth_pct <= NORMAL_DRAWDOWN_DEPTH_PCT_THRESHOLD
        )
    )

    if containment_trigger:
        current_state = DRAWDOWN_STATE_CONTAINMENT
    elif normal_ready:
        current_state = DRAWDOWN_STATE_NORMAL
    elif recovery_ready:
        current_state = DRAWDOWN_STATE_RECOVERY
    elif caution_trigger:
        current_state = DRAWDOWN_STATE_CAUTION
    else:
        current_state = DRAWDOWN_STATE_NORMAL

    if current_state == DRAWDOWN_STATE_NORMAL:
        peak_equity = max(peak_equity_recent, current_equity)
        trough_equity = current_equity
        drawdown_depth_pct = max(0.0, (peak_equity - current_equity) / max(peak_equity, 1.0))
        recovery_progress = 1.0 if drawdown_depth_pct <= 1e-9 else recovery_progress

    if current_state == previous_state:
        entered_at = _coerce_datetime(previous_detail.get("entered_at")) or latest_pnl.created_at or observed_at
    else:
        entered_at = observed_at

    if current_state == DRAWDOWN_STATE_CONTAINMENT:
        if drawdown_depth_pct >= CONTAINMENT_DRAWDOWN_DEPTH_PCT_THRESHOLD:
            transition_reason = "drawdown_depth_threshold"
        elif containment_recent_pnl_trigger:
            transition_reason = "recent_net_pnl_threshold"
        else:
            transition_reason = "consecutive_losses_and_drawdown"
    elif current_state == DRAWDOWN_STATE_CAUTION:
        if consecutive_losses >= CAUTION_CONSECUTIVE_LOSSES_THRESHOLD:
            transition_reason = "consecutive_losses_warning"
        elif drawdown_depth_pct >= CAUTION_DRAWDOWN_DEPTH_PCT_THRESHOLD:
            transition_reason = "drawdown_depth_warning"
        else:
            transition_reason = "recent_net_pnl_warning"
    elif current_state == DRAWDOWN_STATE_RECOVERY:
        transition_reason = "recovery_progress_positive"
    else:
        transition_reason = "recovered_to_normal" if previous_state in DRAWDOWN_ACTIVE_STATES else "stable_normal"

    return {
        "current_drawdown_state": current_state,
        "previous_drawdown_state": previous_state,
        "state_changed": current_state != previous_state,
        "entered_at": _serialize_datetime(entered_at),
        "transition_reason": transition_reason,
        "policy_adjustments": dict(DRAWDOWN_POLICY_MAP[current_state]),
        "peak_equity": round(peak_equity, 6),
        "trough_equity": round(trough_equity, 6),
        "drawdown_depth_pct": round(drawdown_depth_pct, 6),
        "recent_net_pnl": round(recent_net_pnl, 6),
        "recent_net_pnl_pct": round(recent_net_pnl_pct, 6),
        "consecutive_losses": consecutive_losses,
        "recovery_progress": round(recovery_progress, 6),
        "current_equity": round(current_equity, 6),
        "lookback_snapshots": len(history),
        "latest_pnl_snapshot_at": _serialize_datetime(latest_pnl.created_at),
    }
