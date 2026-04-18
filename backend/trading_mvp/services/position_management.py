from __future__ import annotations

from typing import Any

from trading_mvp.models import Position, Setting
from trading_mvp.schemas import FeaturePayload
from trading_mvp.services.holding_profile import (
    HOLDING_PROFILE_SCALP,
    deterministic_stop_management_payload,
    resolve_holding_profile_management_policy,
)
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
MFE_ROLLBACK_ACTIVATION_R = 1.25
MFE_ROLLBACK_BASE_THRESHOLD = 0.42
MFE_ROLLBACK_STRONG_TREND_THRESHOLD = 0.58
MFE_ROLLBACK_WEAK_TREND_THRESHOLD = 0.32
MFE_ROLLBACK_PARTIAL_TIGHTENING = 0.1
MFE_ROLLBACK_MIN_THRESHOLD = 0.2
MFE_ROLLBACK_STOP_BUFFER_BASE_R = 0.55
MFE_ROLLBACK_STOP_BUFFER_STRONG_R = 0.8
MFE_ROLLBACK_STOP_BUFFER_WEAK_R = 0.35
MFE_ROLLBACK_SEVERE_EXTRA_PCT = 0.18
MFE_ROLLBACK_EXIT_EXTRA_PCT = 0.3
MFE_ROLLBACK_EXIT_MIN_R = 2.0
MFE_ROLLBACK_EXIT_CURRENT_R_MAX = 0.2
BREAKOUT_TIME_PROFILE_NAME = "breakout_fast"
CONTINUATION_TIME_PROFILE_NAME = "continuation_balanced"
PULLBACK_TIME_PROFILE_NAME = "pullback_flexible"


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


def _set_management_stage(management: dict[str, Any], *, stage: str) -> None:
    previous_stage = str(management.get("management_stage") or "")
    management["management_stage"] = stage
    if previous_stage != stage:
        management["last_stage_transition"] = {
            "from": previous_stage or None,
            "to": stage,
            "at": utcnow_naive().isoformat(),
        }


def _infer_time_profile_name(planned_max_holding_minutes: int | None) -> str:
    if planned_max_holding_minutes is not None:
        if planned_max_holding_minutes <= 120:
            return BREAKOUT_TIME_PROFILE_NAME
        if planned_max_holding_minutes <= 180:
            return CONTINUATION_TIME_PROFILE_NAME
    return PULLBACK_TIME_PROFILE_NAME


def _resolve_time_profile(
    management: dict[str, Any],
    *,
    planned_max_holding_minutes: int | None,
) -> dict[str, Any]:
    explicit_profile = bool(
        planned_max_holding_minutes not in {None, 0}
        or management.get("entry_time_profile")
        or management.get("time_profile_name")
        or management.get("early_fail_minutes") not in {None, ""}
        or management.get("early_fail_r_floor") not in {None, ""}
    )
    if not explicit_profile:
        return {
            "profile_name": PULLBACK_TIME_PROFILE_NAME,
            "planned_max_holding_minutes": None,
            "early_fail_minutes": None,
            "early_fail_r_floor": None,
            "hold_extension_minutes": 0,
            "time_to_fail_basis": "legacy_time_stop_only",
        }
    profile_name = str(
        management.get("entry_time_profile")
        or management.get("time_profile_name")
        or _infer_time_profile_name(planned_max_holding_minutes)
    )
    if profile_name == BREAKOUT_TIME_PROFILE_NAME:
        resolved_planned = planned_max_holding_minutes or 120
        early_fail_r_floor = 0.1
        hold_extension_minutes = 15
        time_to_fail_basis = "breakout_confirm_requires_fast_follow_through"
    elif profile_name == CONTINUATION_TIME_PROFILE_NAME:
        resolved_planned = planned_max_holding_minutes or 180
        early_fail_r_floor = 0.0
        hold_extension_minutes = 25
        time_to_fail_basis = "continuation_requires_progress_without_stalling"
    else:
        profile_name = PULLBACK_TIME_PROFILE_NAME
        resolved_planned = planned_max_holding_minutes or 240
        early_fail_r_floor = -0.15
        hold_extension_minutes = 35
        time_to_fail_basis = "pullback_allows_more_time_before_capitulation"
    early_fail_minutes = min(
        max(int(round(resolved_planned * 0.25)), 18 if profile_name == BREAKOUT_TIME_PROFILE_NAME else 30),
        30 if profile_name == BREAKOUT_TIME_PROFILE_NAME else 45 if profile_name == CONTINUATION_TIME_PROFILE_NAME else 60,
    )
    return {
        "profile_name": profile_name,
        "planned_max_holding_minutes": resolved_planned,
        "early_fail_minutes": int(management.get("early_fail_minutes") or early_fail_minutes),
        "early_fail_r_floor": _coerce_float(management.get("early_fail_r_floor"))
        if management.get("early_fail_r_floor") not in {None, ""}
        else early_fail_r_floor,
        "hold_extension_minutes": int(management.get("hold_extension_minutes") or hold_extension_minutes),
        "time_to_fail_basis": str(management.get("time_to_fail_basis") or time_to_fail_basis),
    }


def seed_position_management_metadata(
    position: Position,
    *,
    max_holding_minutes: int | None,
    timeframe: str | None,
    stop_loss: float | None,
    take_profit: float | None,
    reset_partial_take_profit: bool = False,
    holding_profile: str = HOLDING_PROFILE_SCALP,
    holding_profile_reason: str | None = None,
    initial_stop_type: str = "deterministic_hard_stop",
    ai_stop_management_allowed: bool = True,
    hard_stop_active: bool = True,
) -> dict[str, Any]:
    metadata = position.metadata_json if isinstance(position.metadata_json, dict) else {}
    management = _management_metadata(position)
    profile = str(holding_profile or HOLDING_PROFILE_SCALP).strip().lower()
    management_policy = resolve_holding_profile_management_policy(profile)
    baseline_stop = stop_loss if _valid_stop_for_side(position.side, position.entry_price, stop_loss) else None
    if baseline_stop is None and _valid_stop_for_side(position.side, position.entry_price, position.stop_loss):
        baseline_stop = position.stop_loss
    baseline_take_profit = take_profit if take_profit not in {None, 0} else position.take_profit
    initial_risk_per_unit = abs(position.entry_price - baseline_stop) if baseline_stop is not None else None
    stop_management = deterministic_stop_management_payload(
        hard_stop_active=bool(hard_stop_active and baseline_stop is not None)
    )
    if reset_partial_take_profit or "partial_take_profit_taken" not in management:
        management["partial_take_profit_taken"] = False
    if "mfe_r" not in management:
        management["mfe_r"] = 0.0
    if "mae_r" not in management:
        management["mae_r"] = 0.0
    if "mfe_rollback_pct" not in management:
        management["mfe_rollback_pct"] = 0.0
    if "mfe_protection_action" not in management:
        management["mfe_protection_action"] = "monitor"
    time_profile = _resolve_time_profile(
        management,
        planned_max_holding_minutes=max_holding_minutes or int(management.get("planned_max_holding_minutes") or 0) or None,
    )
    _set_management_stage(
        management,
        stage="partial_taken" if bool(management.get("partial_take_profit_taken")) else "initial",
    )
    management.update(
        {
            "initial_stop_loss": baseline_stop,
            "initial_take_profit": baseline_take_profit,
            "initial_risk_per_unit": initial_risk_per_unit,
            "planned_max_holding_minutes": time_profile["planned_max_holding_minutes"],
            "entry_timeframe": timeframe or management.get("entry_timeframe"),
            "entry_time_profile": time_profile["profile_name"],
            "holding_profile": profile,
            "holding_profile_reason": holding_profile_reason or management.get("holding_profile_reason"),
            "initial_stop_type": initial_stop_type or stop_management["initial_stop_type"],
            "ai_stop_management_allowed": bool(ai_stop_management_allowed),
            "hard_stop_active": bool(stop_management["hard_stop_active"]),
            "stop_widening_allowed": False,
            "break_even_trigger_r": _coerce_float(management_policy.get("break_even_trigger_r")),
            "partial_take_profit_trigger_r": _coerce_float(management_policy.get("partial_take_profit_trigger_r")),
            "partial_take_profit_fraction": _coerce_float(management_policy.get("partial_take_profit_fraction")),
            "trailing_stop_atr_multiplier": _coerce_float(management_policy.get("trailing_stop_atr_multiplier")),
            "early_fail_minutes": time_profile["early_fail_minutes"],
            "early_fail_r_floor": time_profile["early_fail_r_floor"],
            "hold_extension_minutes": time_profile["hold_extension_minutes"],
            "time_to_fail_basis": time_profile["time_to_fail_basis"],
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
    _set_management_stage(management, stage="partial_taken")
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


def record_add_on_metadata(
    position: Position,
    *,
    add_on_r_multiple: float | None,
    add_on_reason: str | None,
    risk_multiplier: float | None = None,
    leverage_multiplier: float | None = None,
    notional_multiplier: float | None = None,
) -> dict[str, Any]:
    metadata = position.metadata_json if isinstance(position.metadata_json, dict) else {}
    management = _management_metadata(position)
    add_on_count = int(management.get("add_on_count") or 0) + 1
    now = utcnow_naive().isoformat()
    management.update(
        {
            "add_on_count": add_on_count,
            "pyramiding_stage": add_on_count,
            "last_add_on_at": now,
            "add_on_reason": add_on_reason or "winner_only_add_on",
            "add_on_r_multiple": add_on_r_multiple,
            "last_add_on": {
                "at": now,
                "pyramiding_stage": add_on_count,
                "add_on_reason": add_on_reason or "winner_only_add_on",
                "add_on_r_multiple": add_on_r_multiple,
                "risk_multiplier": risk_multiplier,
                "leverage_multiplier": leverage_multiplier,
                "notional_multiplier": notional_multiplier,
            },
            "last_updated_at": now,
        }
    )
    metadata["position_management"] = management
    position.metadata_json = metadata
    return management


def store_position_management_context(position: Position, context: dict[str, Any]) -> dict[str, Any]:
    metadata = position.metadata_json if isinstance(position.metadata_json, dict) else {}
    management = _management_metadata(position)
    management["last_context"] = context
    for key in (
        "current_r_multiple",
        "mfe_r",
        "mae_r",
        "mfe_rollback_pct",
        "mfe_rollback_threshold",
        "mfe_protection_action",
        "entry_time_profile",
        "effective_max_holding_minutes",
        "early_fail_minutes",
        "early_fail_r_floor",
        "hold_extension_minutes",
        "time_to_fail_ready",
        "time_to_fail_action",
        "time_to_fail_reason",
        "time_to_fail_basis",
        "holding_profile",
        "holding_profile_reason",
        "initial_stop_type",
        "ai_stop_management_allowed",
        "hard_stop_active",
        "stop_widening_allowed",
        "break_even_trigger_r",
        "partial_take_profit_trigger_r",
        "partial_take_profit_fraction",
        "trailing_stop_atr_multiplier",
    ):
        if key in context:
            management[key] = context.get(key)
    stage = str(context.get("management_stage") or management.get("management_stage") or "initial")
    _set_management_stage(management, stage=stage)
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
    holding_profile = str(management.get("holding_profile") or HOLDING_PROFILE_SCALP)
    management_policy = resolve_holding_profile_management_policy(holding_profile)
    stop_management = deterministic_stop_management_payload(
        hard_stop_active=bool(management.get("hard_stop_active", initial_stop_loss is not None))
    )

    planned_max_holding_minutes = int(management.get("planned_max_holding_minutes") or 0) or None
    time_profile = _resolve_time_profile(
        management,
        planned_max_holding_minutes=planned_max_holding_minutes,
    )
    planned_max_holding_minutes = (
        int(time_profile["planned_max_holding_minutes"])
        if time_profile["planned_max_holding_minutes"] not in {None, 0}
        else None
    )
    entry_time_profile = str(time_profile["profile_name"])
    early_fail_minutes = (
        int(time_profile["early_fail_minutes"])
        if time_profile["early_fail_minutes"] not in {None, 0}
        else None
    )
    early_fail_r_floor = _coerce_float(time_profile["early_fail_r_floor"])
    hold_extension_minutes = int(time_profile["hold_extension_minutes"] or 0)
    time_to_fail_basis = str(time_profile["time_to_fail_basis"])
    time_in_trade_minutes = max(
        (utcnow_naive() - position.opened_at).total_seconds() / 60.0,
        0.0,
    )
    unrealized_pnl = float(position.unrealized_pnl)
    current_r_multiple = None
    if initial_risk_per_unit is not None and initial_risk_per_unit > 0:
        current_r_multiple = _signed_move(position.side, position.entry_price, position.mark_price) / initial_risk_per_unit

    regime = feature_payload.regime if feature_payload is not None else None
    partial_take_profit_taken = bool(management.get("partial_take_profit_taken"))
    strong_trend_regime = bool(
        regime is not None
        and regime.primary_regime not in {"range", "transition"}
        and regime.trend_alignment in {"bullish_aligned", "bearish_aligned"}
        and not regime.weak_volume
        and not regime.momentum_weakening
    )
    weak_trend_regime = bool(
        regime is not None
        and (
            regime.primary_regime in {"range", "transition"}
            or regime.weak_volume
            or regime.momentum_weakening
        )
    )
    previous_mfe_r = _coerce_float(management.get("mfe_r"))
    previous_mae_r = _coerce_float(management.get("mae_r"))
    mfe_r = None
    mae_r = None
    if current_r_multiple is not None:
        mfe_r = max(previous_mfe_r or 0.0, current_r_multiple, 0.0)
        mae_r = min(previous_mae_r or 0.0, current_r_multiple, 0.0)
    hold_extension_active = bool(
        planned_max_holding_minutes not in {None, 0}
        and hold_extension_minutes > 0
        and strong_trend_regime
        and current_r_multiple is not None
        and current_r_multiple >= 0.75
    )
    effective_max_holding_minutes = (
        planned_max_holding_minutes + hold_extension_minutes
        if hold_extension_active and planned_max_holding_minutes not in {None, 0}
        else planned_max_holding_minutes
    )
    holding_ratio = (
        time_in_trade_minutes / effective_max_holding_minutes
        if effective_max_holding_minutes not in {None, 0}
        else None
    )

    current_stop_loss = position.stop_loss if _valid_stop_for_side(position.side, position.entry_price, position.stop_loss) else None
    break_even_trigger_r = max(
        _coerce_float(management.get("break_even_trigger_r"))
        or _coerce_float(management_policy.get("break_even_trigger_r"))
        or _coerce_float(getattr(settings_row, "move_stop_to_be_rr", BREAK_EVEN_TRIGGER_R))
        or BREAK_EVEN_TRIGGER_R,
        0.0,
    )
    break_even_eligible = bool(
        settings_row.break_even_enabled
        and current_r_multiple is not None
        and current_r_multiple >= break_even_trigger_r
    )
    break_even_stop_loss = position.entry_price if break_even_eligible else None

    trailing_stop_loss = None
    trailing_stop_atr_multiplier = (
        _coerce_float(management.get("trailing_stop_atr_multiplier"))
        or _coerce_float(management_policy.get("trailing_stop_atr_multiplier"))
        or TRAILING_STOP_ATR_MULTIPLIER
    )
    if (
        feature_payload is not None
        and settings_row.atr_trailing_stop_enabled
        and current_r_multiple is not None
        and current_r_multiple >= TRAILING_STOP_ACTIVATION_R
        and feature_payload.atr > 0
    ):
        if position.side == "long":
            trailing_stop_loss = position.mark_price - (feature_payload.atr * trailing_stop_atr_multiplier)
        else:
            trailing_stop_loss = position.mark_price + (feature_payload.atr * trailing_stop_atr_multiplier)
        trailing_stop_loss = _cap_stop_inside_market(position.side, position.mark_price, trailing_stop_loss)

    mfe_rollback_threshold = None
    mfe_rollback_pct = None
    mfe_rollback_stop_loss = None
    mfe_rollback_triggered = False
    mfe_protection_action = "monitor"
    if (
        current_r_multiple is not None
        and mfe_r is not None
        and mfe_r >= MFE_ROLLBACK_ACTIVATION_R
        and current_r_multiple < mfe_r
        and initial_risk_per_unit is not None
        and initial_risk_per_unit > 0
    ):
        if strong_trend_regime:
            mfe_rollback_threshold = MFE_ROLLBACK_STRONG_TREND_THRESHOLD
            stop_buffer_r = MFE_ROLLBACK_STOP_BUFFER_STRONG_R
        elif weak_trend_regime:
            mfe_rollback_threshold = MFE_ROLLBACK_WEAK_TREND_THRESHOLD
            stop_buffer_r = MFE_ROLLBACK_STOP_BUFFER_WEAK_R
        else:
            mfe_rollback_threshold = MFE_ROLLBACK_BASE_THRESHOLD
            stop_buffer_r = MFE_ROLLBACK_STOP_BUFFER_BASE_R
        if partial_take_profit_taken:
            mfe_rollback_threshold = max(
                mfe_rollback_threshold - MFE_ROLLBACK_PARTIAL_TIGHTENING,
                MFE_ROLLBACK_MIN_THRESHOLD,
            )
            stop_buffer_r = max(stop_buffer_r - 0.1, 0.2)
        mfe_rollback_pct = min(
            max((mfe_r - max(current_r_multiple, 0.0)) / max(mfe_r, 1e-9), 0.0),
            1.0,
        )
        if mfe_rollback_pct >= mfe_rollback_threshold:
            mfe_rollback_triggered = True
            mfe_protection_action = "tighten_stop"
            severe_rollback = mfe_rollback_pct >= min(
                mfe_rollback_threshold + MFE_ROLLBACK_SEVERE_EXTRA_PCT,
                0.95,
            )
            if (
                not strong_trend_regime
                and mfe_r >= MFE_ROLLBACK_EXIT_MIN_R
                and current_r_multiple <= MFE_ROLLBACK_EXIT_CURRENT_R_MAX
                and mfe_rollback_pct >= min(mfe_rollback_threshold + MFE_ROLLBACK_EXIT_EXTRA_PCT, 0.98)
            ):
                mfe_protection_action = "exit"
            elif partial_take_profit_taken and (weak_trend_regime or severe_rollback):
                mfe_protection_action = "reduce"
            else:
                if position.side == "long":
                    mfe_rollback_stop_loss = position.mark_price - (initial_risk_per_unit * stop_buffer_r)
                else:
                    mfe_rollback_stop_loss = position.mark_price + (initial_risk_per_unit * stop_buffer_r)
                mfe_rollback_stop_loss = _cap_stop_inside_market(
                    position.side,
                    position.mark_price,
                    mfe_rollback_stop_loss,
                )
                if mfe_rollback_stop_loss is None:
                    mfe_protection_action = "monitor"

    tightened_stop_loss = current_stop_loss
    applied_rule_candidates: list[str] = []
    if _is_more_protective_stop(position.side, tightened_stop_loss, break_even_stop_loss):
        tightened_stop_loss = break_even_stop_loss
        applied_rule_candidates.append("POSITION_MANAGEMENT_BREAK_EVEN")
    if _is_more_protective_stop(position.side, tightened_stop_loss, trailing_stop_loss):
        tightened_stop_loss = trailing_stop_loss
        applied_rule_candidates.append("POSITION_MANAGEMENT_ATR_TRAIL")
    if mfe_rollback_triggered:
        applied_rule_candidates.append("POSITION_MANAGEMENT_MFE_ROLLBACK")
    if _is_more_protective_stop(position.side, tightened_stop_loss, mfe_rollback_stop_loss):
        tightened_stop_loss = mfe_rollback_stop_loss
        applied_rule_candidates.append("POSITION_MANAGEMENT_MFE_ROLLBACK_TIGHTEN")

    partial_take_profit_trigger_r = max(
        _coerce_float(management.get("partial_take_profit_trigger_r"))
        or _coerce_float(management_policy.get("partial_take_profit_trigger_r"))
        or _coerce_float(getattr(settings_row, "partial_tp_rr", PARTIAL_TAKE_PROFIT_TRIGGER_R))
        or PARTIAL_TAKE_PROFIT_TRIGGER_R,
        0.0,
    )
    partial_take_profit_fraction = min(
        max(
            _coerce_float(management.get("partial_take_profit_fraction"))
            or _coerce_float(management_policy.get("partial_take_profit_fraction"))
            or _coerce_float(getattr(settings_row, "partial_tp_size_pct", PARTIAL_TAKE_PROFIT_FRACTION))
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
    if mfe_protection_action == "exit":
        reduce_reason_codes.append("POSITION_MANAGEMENT_MFE_ROLLBACK_EXIT")
    elif mfe_protection_action == "reduce":
        reduce_reason_codes.extend(
            [
                "POSITION_MANAGEMENT_MFE_ROLLBACK_REDUCE",
                "POSITION_MANAGEMENT_RUNNER_PROTECT",
            ]
        )

    time_to_fail_ready = bool(
        current_r_multiple is not None
        and not partial_take_profit_taken
        and early_fail_minutes is not None
        and early_fail_r_floor is not None
        and time_in_trade_minutes >= early_fail_minutes
        and current_r_multiple <= early_fail_r_floor
    )
    time_to_fail_action: str | None = None
    time_to_fail_reason: str | None = None
    if time_to_fail_ready:
        applied_rule_candidates.append("POSITION_MANAGEMENT_TIME_TO_FAIL")
        if entry_time_profile == BREAKOUT_TIME_PROFILE_NAME:
            applied_rule_candidates.append("POSITION_MANAGEMENT_BREAKOUT_TIME_PROFILE")
            time_to_fail_reason = "breakout_follow_through_missing"
            if current_r_multiple is not None and current_r_multiple <= 0:
                time_to_fail_action = "exit"
                reduce_reason_codes.append("POSITION_MANAGEMENT_BREAKOUT_TIME_FAIL_EXIT")
            else:
                time_to_fail_action = "reduce"
                reduce_reason_codes.append("POSITION_MANAGEMENT_BREAKOUT_TIME_FAIL_REDUCE")
        elif entry_time_profile == CONTINUATION_TIME_PROFILE_NAME:
            applied_rule_candidates.append("POSITION_MANAGEMENT_CONTINUATION_TIME_PROFILE")
            time_to_fail_reason = "continuation_stalled"
            if current_r_multiple is not None and current_r_multiple <= -0.15 and weak_trend_regime:
                time_to_fail_action = "exit"
                reduce_reason_codes.append("POSITION_MANAGEMENT_CONTINUATION_TIME_FAIL_EXIT")
            else:
                time_to_fail_action = "reduce"
                reduce_reason_codes.append("POSITION_MANAGEMENT_CONTINUATION_TIME_FAIL_REDUCE")
        else:
            applied_rule_candidates.append("POSITION_MANAGEMENT_PULLBACK_TIME_PROFILE")
            time_to_fail_reason = "pullback_reclaim_delayed"
            if current_r_multiple is not None and current_r_multiple <= -0.35 and weak_trend_regime:
                time_to_fail_action = "exit"
                reduce_reason_codes.append("POSITION_MANAGEMENT_PULLBACK_TIME_FAIL_EXIT")
            else:
                time_to_fail_action = "reduce"
                reduce_reason_codes.append("POSITION_MANAGEMENT_PULLBACK_TIME_FAIL_REDUCE")

    configured_time_stop_minutes = max(
        int(getattr(settings_row, "time_stop_minutes", TIME_STOP_MINUTES) or TIME_STOP_MINUTES),
        1,
    )
    time_stop_minutes = max(
        int(effective_max_holding_minutes or configured_time_stop_minutes),
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

    management_stage = "partial_taken" if partial_take_profit_taken else "initial"
    if partial_take_profit_taken and mfe_protection_action == "tighten_stop":
        management_stage = "trailing_runner"
    elif mfe_protection_action in {"reduce", "exit"} or time_to_fail_action in {"reduce", "exit"} or time_stop_action in {"reduce", "exit"}:
        management_stage = "defensive_reduce"

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
        "holding_profile": holding_profile,
        "holding_profile_reason": management.get("holding_profile_reason"),
        "initial_stop_type": management.get("initial_stop_type") or stop_management["initial_stop_type"],
        "ai_stop_management_allowed": bool(management.get("ai_stop_management_allowed", stop_management["ai_stop_management_allowed"])),
        "hard_stop_active": bool(management.get("hard_stop_active", stop_management["hard_stop_active"])),
        "stop_widening_allowed": False,
        "planned_max_holding_minutes": planned_max_holding_minutes,
        "effective_max_holding_minutes": effective_max_holding_minutes,
        "entry_time_profile": entry_time_profile,
        "time_in_trade_minutes": round(time_in_trade_minutes, 2),
        "holding_ratio": round(holding_ratio, 4) if holding_ratio is not None else None,
        "unrealized_pnl": unrealized_pnl,
        "current_r_multiple": round(current_r_multiple, 4) if current_r_multiple is not None else None,
        "mfe_r": round(mfe_r, 4) if mfe_r is not None else None,
        "mae_r": round(mae_r, 4) if mae_r is not None else None,
        "mfe_rollback_pct": round(mfe_rollback_pct, 4) if mfe_rollback_pct is not None else None,
        "mfe_rollback_threshold": (
            round(mfe_rollback_threshold, 4) if mfe_rollback_threshold is not None else None
        ),
        "mfe_protection_action": mfe_protection_action,
        "mfe_rollback_triggered": mfe_rollback_triggered,
        "management_stage": management_stage,
        "break_even_eligible": break_even_eligible,
        "break_even_stop_loss": break_even_stop_loss,
        "break_even_trigger_r": break_even_trigger_r,
        "trailing_stop_atr_multiplier": trailing_stop_atr_multiplier,
        "trailing_stop_loss": trailing_stop_loss,
        "mfe_rollback_stop_loss": mfe_rollback_stop_loss,
        "tightened_stop_loss": tightened_stop_loss,
        "partial_take_profit_ready": partial_take_profit_ready,
        "partial_take_profit_taken": partial_take_profit_taken,
        "partial_take_profit_trigger_r": partial_take_profit_trigger_r,
        "partial_take_profit_fraction": partial_take_profit_fraction,
        "early_fail_minutes": early_fail_minutes,
        "early_fail_r_floor": early_fail_r_floor,
        "time_to_fail_basis": time_to_fail_basis,
        "time_to_fail_ready": time_to_fail_ready,
        "time_to_fail_action": time_to_fail_action,
        "time_to_fail_reason": time_to_fail_reason,
        "hold_extension_minutes": hold_extension_minutes,
        "hold_extension_active": hold_extension_active,
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
        "strong_trend_regime": strong_trend_regime,
        "weak_trend_regime": weak_trend_regime,
        "reduce_reason_codes": list(dict.fromkeys(reduce_reason_codes)),
        "applied_rule_candidates": list(dict.fromkeys(applied_rule_candidates)),
        "data_fallback_rule": (
            "Without initial stop metadata, stop tightening can still use the current stop, but partial take-profit "
            "and time-stop automation stay conservative."
        ),
    }
