from __future__ import annotations

from typing import Any

from trading_mvp.models import Position, Setting
from trading_mvp.schemas import FeaturePayload
from trading_mvp.time_utils import utcnow_naive

BREAK_EVEN_TRIGGER_R = 1.0
TRAILING_STOP_ACTIVATION_R = 1.0
TRAILING_STOP_ATR_MULTIPLIER = 1.2
PARTIAL_TAKE_PROFIT_TRIGGER_R = 1.5
PARTIAL_TAKE_PROFIT_FRACTION = 0.25
TIME_STOP_MINUTES = 120
TIME_STOP_PROFIT_FLOOR = 0.15
EDGE_DECAY_START_RATIO = 0.75
EDGE_DECAY_HARD_REDUCE_RATIO = 1.0


def _coerce_float(value: object) -> float | None:
    if value in {None, ""}:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return None


def _valid_stop_for_side(side: str, entry_price: float, stop_loss: float | None) -> bool:
    if stop_loss is None or entry_price <= 0:
        return False
    if side == "long":
        return stop_loss < entry_price
    return stop_loss > entry_price


def _is_more_protective_stop(side: str, current_stop: float | None, candidate_stop: float | None) -> bool:
    if candidate_stop is None:
        return False
    if current_stop is None:
        return True
    if side == "long":
        return candidate_stop > current_stop + 1e-9
    return candidate_stop < current_stop - 1e-9


def _cap_stop_inside_market(side: str, mark_price: float, candidate_stop: float | None) -> float | None:
    if candidate_stop is None or mark_price <= 0:
        return None
    if side == "long" and candidate_stop >= mark_price:
        return None
    if side == "short" and candidate_stop <= mark_price:
        return None
    return candidate_stop


def _signed_move(side: str, entry_price: float, mark_price: float) -> float:
    if side == "long":
        return mark_price - entry_price
    return entry_price - mark_price


def _management_metadata(position: Position) -> dict[str, Any]:
    metadata = position.metadata_json if isinstance(position.metadata_json, dict) else {}
    management = metadata.get("position_management")
    if isinstance(management, dict):
        return dict(management)
    return {}


def seed_position_management_metadata(
    position: Position,
    *,
    max_holding_minutes: int | None,
    timeframe: str | None,
    stop_loss: float | None,
    take_profit: float | None,
    reset_partial_take_profit: bool = False,
) -> dict[str, Any]:
    metadata = position.metadata_json if isinstance(position.metadata_json, dict) else {}
    management = _management_metadata(position)
    baseline_stop = stop_loss if _valid_stop_for_side(position.side, position.entry_price, stop_loss) else None
    if baseline_stop is None and _valid_stop_for_side(position.side, position.entry_price, position.stop_loss):
        baseline_stop = position.stop_loss
    baseline_take_profit = take_profit if take_profit not in {None, 0} else position.take_profit
    initial_risk_per_unit = abs(position.entry_price - baseline_stop) if baseline_stop is not None else None
    if reset_partial_take_profit or "partial_take_profit_taken" not in management:
        management["partial_take_profit_taken"] = False
    management.update(
        {
            "initial_stop_loss": baseline_stop,
            "initial_take_profit": baseline_take_profit,
            "initial_risk_per_unit": initial_risk_per_unit,
            "planned_max_holding_minutes": max_holding_minutes or management.get("planned_max_holding_minutes"),
            "entry_timeframe": timeframe or management.get("entry_timeframe"),
            "baseline_seeded_at": management.get("baseline_seeded_at") or utcnow_naive().isoformat(),
            "last_updated_at": utcnow_naive().isoformat(),
        }
    )
    metadata["position_management"] = management
    position.metadata_json = metadata
    return management


def mark_partial_take_profit_taken(position: Position) -> dict[str, Any]:
    metadata = position.metadata_json if isinstance(position.metadata_json, dict) else {}
    management = _management_metadata(position)
    management["partial_take_profit_taken"] = True
    management["partial_take_profit_taken_at"] = utcnow_naive().isoformat()
    management["last_updated_at"] = utcnow_naive().isoformat()
    metadata["position_management"] = management
    position.metadata_json = metadata
    return management


def mark_time_stop_action(position: Position, *, action: str) -> dict[str, Any]:
    metadata = position.metadata_json if isinstance(position.metadata_json, dict) else {}
    management = _management_metadata(position)
    management["time_stop_action_taken"] = action
    management["time_stop_action_taken_at"] = utcnow_naive().isoformat()
    management["last_updated_at"] = utcnow_naive().isoformat()
    metadata["position_management"] = management
    position.metadata_json = metadata
    return management


def store_position_management_context(position: Position, context: dict[str, Any]) -> dict[str, Any]:
    metadata = position.metadata_json if isinstance(position.metadata_json, dict) else {}
    management = _management_metadata(position)
    management["last_context"] = context
    management["last_updated_at"] = utcnow_naive().isoformat()
    metadata["position_management"] = management
    position.metadata_json = metadata
    return management


def build_position_management_context(
    position: Position | None,
    *,
    feature_payload: FeaturePayload | None,
    settings_row: Setting,
) -> dict[str, Any]:
    if not settings_row.position_management_enabled:
        return {
            "enabled": False,
            "status": "disabled",
            "applied_rule_candidates": [],
            "tightened_stop_loss": None,
            "reduce_reason_codes": [],
        }
    if position is None or position.status != "open" or position.quantity <= 0:
        return {
            "enabled": True,
            "status": "no_open_position",
            "applied_rule_candidates": [],
            "tightened_stop_loss": None,
            "reduce_reason_codes": [],
        }

    management = _management_metadata(position)
    initial_stop_loss = _coerce_float(management.get("initial_stop_loss"))
    if initial_stop_loss is None and _valid_stop_for_side(position.side, position.entry_price, position.stop_loss):
        initial_stop_loss = position.stop_loss
    initial_take_profit = _coerce_float(management.get("initial_take_profit"))
    initial_risk_per_unit = _coerce_float(management.get("initial_risk_per_unit"))
    if initial_risk_per_unit is None and initial_stop_loss is not None:
        initial_risk_per_unit = abs(position.entry_price - initial_stop_loss)

    planned_max_holding_minutes = int(management.get("planned_max_holding_minutes") or 0) or None
    time_in_trade_minutes = max(
        (utcnow_naive() - position.opened_at).total_seconds() / 60.0,
        0.0,
    )
    holding_ratio = (
        time_in_trade_minutes / planned_max_holding_minutes
        if planned_max_holding_minutes not in {None, 0}
        else None
    )
    unrealized_pnl = float(position.unrealized_pnl)
    current_r_multiple = None
    if initial_risk_per_unit is not None and initial_risk_per_unit > 0:
        current_r_multiple = _signed_move(position.side, position.entry_price, position.mark_price) / initial_risk_per_unit

    regime = feature_payload.regime if feature_payload is not None else None
    current_stop_loss = position.stop_loss if _valid_stop_for_side(position.side, position.entry_price, position.stop_loss) else None
    break_even_trigger_r = max(_coerce_float(getattr(settings_row, "move_stop_to_be_rr", BREAK_EVEN_TRIGGER_R)) or BREAK_EVEN_TRIGGER_R, 0.0)
    break_even_eligible = bool(
        settings_row.break_even_enabled
        and current_r_multiple is not None
        and current_r_multiple >= break_even_trigger_r
    )
    break_even_stop_loss = position.entry_price if break_even_eligible else None

    trailing_stop_loss = None
    if (
        feature_payload is not None
        and settings_row.atr_trailing_stop_enabled
        and current_r_multiple is not None
        and current_r_multiple >= TRAILING_STOP_ACTIVATION_R
        and feature_payload.atr > 0
    ):
        if position.side == "long":
            trailing_stop_loss = position.mark_price - (feature_payload.atr * TRAILING_STOP_ATR_MULTIPLIER)
        else:
            trailing_stop_loss = position.mark_price + (feature_payload.atr * TRAILING_STOP_ATR_MULTIPLIER)
        trailing_stop_loss = _cap_stop_inside_market(position.side, position.mark_price, trailing_stop_loss)

    tightened_stop_loss = current_stop_loss
    applied_rule_candidates: list[str] = []
    if _is_more_protective_stop(position.side, tightened_stop_loss, break_even_stop_loss):
        tightened_stop_loss = break_even_stop_loss
        applied_rule_candidates.append("POSITION_MANAGEMENT_BREAK_EVEN")
    if _is_more_protective_stop(position.side, tightened_stop_loss, trailing_stop_loss):
        tightened_stop_loss = trailing_stop_loss
        applied_rule_candidates.append("POSITION_MANAGEMENT_ATR_TRAIL")

    partial_take_profit_taken = bool(management.get("partial_take_profit_taken"))
    partial_take_profit_trigger_r = max(
        _coerce_float(getattr(settings_row, "partial_tp_rr", PARTIAL_TAKE_PROFIT_TRIGGER_R))
        or PARTIAL_TAKE_PROFIT_TRIGGER_R,
        0.0,
    )
    partial_take_profit_fraction = min(
        max(
            _coerce_float(getattr(settings_row, "partial_tp_size_pct", PARTIAL_TAKE_PROFIT_FRACTION))
            or PARTIAL_TAKE_PROFIT_FRACTION,
            0.01,
        ),
        1.0,
    )
    partial_take_profit_ready = bool(
        settings_row.partial_take_profit_enabled
        and not partial_take_profit_taken
        and current_r_multiple is not None
        and current_r_multiple >= partial_take_profit_trigger_r
    )
    if partial_take_profit_ready:
        applied_rule_candidates.append("POSITION_MANAGEMENT_PARTIAL_TAKE_PROFIT")

    reduce_reason_codes: list[str] = []

    time_stop_minutes = max(
        int(getattr(settings_row, "time_stop_minutes", TIME_STOP_MINUTES) or TIME_STOP_MINUTES),
        1,
    )
    time_stop_profit_floor = _coerce_float(getattr(settings_row, "time_stop_profit_floor", TIME_STOP_PROFIT_FLOOR))
    if time_stop_profit_floor is None:
        time_stop_profit_floor = TIME_STOP_PROFIT_FLOOR
    time_stop_action_taken = str(management.get("time_stop_action_taken", "") or "").lower() or None
    time_stop_elapsed = time_in_trade_minutes >= time_stop_minutes
    time_stop_ready = bool(
        settings_row.time_stop_enabled
        and time_stop_action_taken is None
        and current_r_multiple is not None
        and time_stop_elapsed
        and current_r_multiple <= time_stop_profit_floor
    )
    time_stop_action: str | None = None
    if time_stop_ready:
        if current_r_multiple is not None and current_r_multiple <= 0:
            time_stop_action = "exit"
            reduce_reason_codes.append("POSITION_MANAGEMENT_TIME_STOP_EXIT")
        else:
            time_stop_action = "reduce"
            reduce_reason_codes.append("POSITION_MANAGEMENT_TIME_STOP_REDUCE")
        applied_rule_candidates.append("POSITION_MANAGEMENT_TIME_STOP")

    regime_transition_detected = bool(regime is not None and regime.primary_regime == "transition")
    momentum_weakening = bool(regime is not None and regime.momentum_weakening)
    countertrend_pressure = bool(
        feature_payload is not None
        and feature_payload.pullback_context.state == "countertrend"
    )
    holding_edge_decay_active = bool(
        settings_row.holding_edge_decay_enabled
        and holding_ratio is not None
        and holding_ratio >= EDGE_DECAY_START_RATIO
    )
    if partial_take_profit_ready:
        reduce_reason_codes.extend(
            ["POSITION_MANAGEMENT_PARTIAL_TAKE_PROFIT", "POSITION_MANAGEMENT_LOCK_PARTIAL_PROFIT"]
        )
    if holding_edge_decay_active and (
        current_r_multiple is None
        or current_r_multiple <= 0.75
        or (holding_ratio is not None and holding_ratio >= EDGE_DECAY_HARD_REDUCE_RATIO)
    ):
        reduce_reason_codes.append("POSITION_MANAGEMENT_EDGE_DECAY")
    if settings_row.reduce_on_regime_shift_enabled and (
        regime_transition_detected or momentum_weakening or countertrend_pressure
    ):
        if current_r_multiple is None or current_r_multiple >= 0.15 or holding_edge_decay_active:
            if regime_transition_detected:
                reduce_reason_codes.append("POSITION_MANAGEMENT_REGIME_SHIFT")
            if momentum_weakening:
                reduce_reason_codes.append("POSITION_MANAGEMENT_MOMENTUM_WEAKENING")
            if countertrend_pressure:
                reduce_reason_codes.append("POSITION_MANAGEMENT_COUNTERTREND_PRESSURE")

    status = "active"
    if initial_stop_loss is None or initial_risk_per_unit in {None, 0}:
        status = "insufficient_baseline"

    return {
        "enabled": True,
        "status": status,
        "side": position.side,
        "symbol": position.symbol,
        "entry_price": position.entry_price,
        "mark_price": position.mark_price,
        "current_stop_loss": current_stop_loss,
        "initial_stop_loss": initial_stop_loss,
        "initial_take_profit": initial_take_profit,
        "initial_risk_per_unit": initial_risk_per_unit,
        "planned_max_holding_minutes": planned_max_holding_minutes,
        "time_in_trade_minutes": round(time_in_trade_minutes, 2),
        "holding_ratio": round(holding_ratio, 4) if holding_ratio is not None else None,
        "unrealized_pnl": unrealized_pnl,
        "current_r_multiple": round(current_r_multiple, 4) if current_r_multiple is not None else None,
        "break_even_eligible": break_even_eligible,
        "break_even_stop_loss": break_even_stop_loss,
        "break_even_trigger_r": break_even_trigger_r,
        "trailing_stop_loss": trailing_stop_loss,
        "tightened_stop_loss": tightened_stop_loss,
        "partial_take_profit_ready": partial_take_profit_ready,
        "partial_take_profit_taken": partial_take_profit_taken,
        "partial_take_profit_trigger_r": partial_take_profit_trigger_r,
        "partial_take_profit_fraction": partial_take_profit_fraction,
        "time_stop_enabled": settings_row.time_stop_enabled,
        "time_stop_minutes": time_stop_minutes,
        "time_stop_elapsed": time_stop_elapsed,
        "time_stop_profit_floor": time_stop_profit_floor,
        "time_stop_ready": time_stop_ready,
        "time_stop_action": time_stop_action,
        "time_stop_action_taken": time_stop_action_taken,
        "holding_edge_decay_active": holding_edge_decay_active,
        "regime_transition_detected": regime_transition_detected,
        "momentum_weakening": momentum_weakening,
        "countertrend_pressure": countertrend_pressure,
        "reduce_reason_codes": list(dict.fromkeys(reduce_reason_codes)),
        "applied_rule_candidates": list(dict.fromkeys(applied_rule_candidates)),
        "data_fallback_rule": (
            "Without initial stop metadata, stop tightening can still use the current stop, but partial take-profit "
            "and time-stop automation stay conservative."
        ),
    }
