from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_mvp.config import get_settings
from trading_mvp.models import Position, StrategyCooldownState
from trading_mvp.schemas import TradeDecision
from trading_mvp.services.audit import record_audit_event
from trading_mvp.time_utils import utcnow_naive

RANGE_MEAN_REVERSION_STRATEGY_ID = "range_mean_reversion_engine"
RANGE_MR_COOLDOWN_BLOCK_REASON_CODE = "RANGE_MEAN_REVERSION_COOLDOWN_ACTIVE"
RANGE_MR_BREAKOUT_COOLDOWN_REASON_CODE = "RANGE_MEAN_REVERSION_RANGE_BREAK_COOLDOWN"
RANGE_MR_CONSECUTIVE_FAILURES_REASON_CODE = "RANGE_MEAN_REVERSION_CONSECUTIVE_FAILURES"


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _optional_float(value: object) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: object) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _settings_bool(name: str, fallback: bool) -> bool:
    value = getattr(get_settings(), name, None)
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return fallback


def _settings_int(name: str, fallback: int, *, minimum: int = 0) -> int:
    parsed = _optional_int(getattr(get_settings(), name, None))
    if parsed is None:
        parsed = fallback
    return max(parsed, minimum)


def _text(value: object) -> str:
    return str(value or "").strip()


def _normalized_strategy_id(value: object) -> str:
    return _text(value).lower()


def _normalized_side(value: object) -> str | None:
    side = _text(value).lower()
    if side in {"long", "buy"}:
        return "long"
    if side in {"short", "sell"}:
        return "short"
    return None


def _range_id_from_bounds(
    *,
    symbol: str,
    timeframe: str,
    regime_id: str,
    range_low: object,
    range_high: object,
) -> str | None:
    low = _optional_float(range_low)
    high = _optional_float(range_high)
    if low is None or high is None or low <= 0.0 or high <= low:
        return None
    return f"{symbol}:{timeframe}:{regime_id}:range:{low:.2f}-{high:.2f}"


def _trade_tags_from_context(decision_context: dict[str, Any] | None) -> dict[str, Any]:
    context = _as_dict(decision_context)
    tags = _as_dict(context.get("trade_performance_tags"))
    selection_context = _as_dict(context.get("selection_context"))
    tags.update(_as_dict(selection_context.get("trade_performance_tags")))
    return tags


def _strategy_id_from_context(decision_context: dict[str, Any] | None, trade_tags: dict[str, Any]) -> str:
    context = _as_dict(decision_context)
    selection_context = _as_dict(context.get("selection_context"))
    strategy_engine_context = _as_dict(selection_context.get("strategy_engine_context"))
    selected_engine = _as_dict(strategy_engine_context.get("selected_engine"))
    for value in (
        trade_tags.get("strategy_id"),
        trade_tags.get("strategy_engine"),
        context.get("strategy_id"),
        context.get("strategy_engine"),
        selection_context.get("strategy_engine"),
        strategy_engine_context.get("engine_name"),
        strategy_engine_context.get("strategy_engine"),
        selected_engine.get("engine_name"),
    ):
        text = _text(value)
        if text:
            return text
    return ""


def _range_breakout_direction(decision_context: dict[str, Any] | None) -> str:
    context = _as_dict(decision_context)
    current_market_state = _as_dict(context.get("current_market_state"))
    selection_context = _as_dict(context.get("selection_context"))
    strategy_engine_context = _as_dict(selection_context.get("strategy_engine_context"))
    for value in (
        current_market_state.get("range_breakout_direction"),
        context.get("range_breakout_direction"),
        selection_context.get("range_breakout_direction"),
        strategy_engine_context.get("range_breakout_direction"),
    ):
        normalized = _text(value).lower()
        if normalized:
            return normalized
    return "none"


def _adverse_breakout_for_side(side: str, breakout_direction: str) -> bool:
    if breakout_direction in {"", "none", "neutral", "inside", "no_break", "no_breakout"}:
        return False
    if side == "long":
        return breakout_direction in {"down", "short", "bearish", "lower", "break_down", "downside"}
    if side == "short":
        return breakout_direction in {"up", "long", "bullish", "upper", "break_up", "upside"}
    return False


def build_range_mr_identity(
    decision: TradeDecision,
    decision_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    side = _normalized_side(decision.decision)
    if side is None:
        return None
    context = _as_dict(decision_context)
    current_market_state = _as_dict(context.get("current_market_state"))
    trade_tags = _trade_tags_from_context(decision_context)
    strategy_id = _strategy_id_from_context(decision_context, trade_tags)
    if _normalized_strategy_id(strategy_id) != RANGE_MEAN_REVERSION_STRATEGY_ID:
        return None

    symbol = _text(trade_tags.get("symbol")) or _text(decision.symbol).upper()
    timeframe = _text(trade_tags.get("timeframe")) or _text(decision.timeframe)
    regime_id = (
        _text(trade_tags.get("regime_id"))
        or _text(current_market_state.get("regime_id"))
        or _text(context.get("regime_id"))
        or "unknown"
    )
    range_id = (
        _text(trade_tags.get("range_id"))
        or _text(current_market_state.get("range_id"))
        or _text(context.get("range_id"))
        or _range_id_from_bounds(
            symbol=symbol,
            timeframe=timeframe,
            regime_id=regime_id,
            range_low=current_market_state.get("range_low") or trade_tags.get("range_low"),
            range_high=current_market_state.get("range_high") or trade_tags.get("range_high"),
        )
        or regime_id
    )
    return {
        "strategy_id": RANGE_MEAN_REVERSION_STRATEGY_ID,
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "direction": side,
        "regime_id": regime_id,
        "range_id": range_id,
        "trade_performance_tags": trade_tags,
        "current_market_state": current_market_state,
    }


def _state_for_identity(session: Session, identity: dict[str, Any]) -> StrategyCooldownState | None:
    return session.scalar(
        select(StrategyCooldownState)
        .where(
            StrategyCooldownState.strategy_id == identity["strategy_id"],
            StrategyCooldownState.symbol == identity["symbol"],
            StrategyCooldownState.direction == identity["direction"],
            StrategyCooldownState.regime_id == identity["regime_id"],
            StrategyCooldownState.range_id == identity["range_id"],
        )
        .limit(1)
    )


def _get_or_create_state(session: Session, identity: dict[str, Any]) -> StrategyCooldownState:
    state = _state_for_identity(session, identity)
    if state is not None:
        return state
    state = StrategyCooldownState(
        strategy_id=str(identity["strategy_id"]),
        symbol=str(identity["symbol"]),
        direction=str(identity["direction"]),
        regime_id=str(identity["regime_id"]),
        range_id=str(identity["range_id"]),
        metadata_json={},
    )
    session.add(state)
    session.flush()
    return state


def _cooldown_payload(state: StrategyCooldownState, identity: dict[str, Any]) -> dict[str, Any]:
    return {
        "strategy_id": state.strategy_id,
        "symbol": state.symbol,
        "direction": state.direction,
        "timeframe": identity.get("timeframe"),
        "regime_id": state.regime_id,
        "range_id": state.range_id,
        "consecutive_failures": state.consecutive_failures,
        "cooldown_until": state.cooldown_until.isoformat() if state.cooldown_until is not None else None,
        "cooldown_reason": state.cooldown_reason,
        "last_failure_at": state.last_failure_at.isoformat() if state.last_failure_at is not None else None,
        "last_breakout_at": state.last_breakout_at.isoformat() if state.last_breakout_at is not None else None,
    }


def _release_cooldown(
    session: Session,
    state: StrategyCooldownState,
    identity: dict[str, Any],
    *,
    reason: str,
    now: datetime,
) -> None:
    previous_until = state.cooldown_until
    previous_reason = state.cooldown_reason
    state.cooldown_until = None
    state.cooldown_reason = None
    state.metadata_json = {
        **_as_dict(state.metadata_json),
        "last_released_at": now.isoformat(),
        "last_release_reason": reason,
    }
    session.add(state)
    record_audit_event(
        session,
        event_type="range_mean_reversion_cooldown_released",
        entity_type="strategy_cooldown_state",
        entity_id=str(state.id),
        severity="info",
        message="Range mean reversion cooldown was released.",
        payload={
            **_cooldown_payload(state, identity),
            "release_reason": reason,
            "previous_cooldown_until": previous_until.isoformat() if previous_until is not None else None,
            "previous_cooldown_reason": previous_reason,
        },
    )


def _enter_cooldown(
    session: Session,
    state: StrategyCooldownState,
    identity: dict[str, Any],
    *,
    reason_code: str,
    cooldown_minutes: int,
    now: datetime,
    extra_payload: dict[str, Any] | None = None,
) -> None:
    if cooldown_minutes <= 0:
        return
    cooldown_until = now + timedelta(minutes=cooldown_minutes)
    previous_until = state.cooldown_until
    state.cooldown_until = max(previous_until, cooldown_until) if previous_until is not None else cooldown_until
    state.cooldown_reason = reason_code
    state.metadata_json = {
        **_as_dict(state.metadata_json),
        "last_entered_at": now.isoformat(),
        "last_enter_reason": reason_code,
        **_as_dict(extra_payload),
    }
    session.add(state)
    record_audit_event(
        session,
        event_type="range_mean_reversion_cooldown_entered",
        entity_type="strategy_cooldown_state",
        entity_id=str(state.id),
        severity="warning",
        message="Range mean reversion cooldown was entered.",
        payload={
            **_cooldown_payload(state, identity),
            "reason_code": reason_code,
            "cooldown_minutes": cooldown_minutes,
            **_as_dict(extra_payload),
        },
    )


def evaluate_range_mr_cooldown_gate(
    session: Session,
    decision: TradeDecision,
    decision_context: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> tuple[list[str], dict[str, Any]]:
    now = now or utcnow_naive()
    enabled = _settings_bool("range_mr_cooldown_enabled", True)
    max_failures = _settings_int("range_mr_max_consecutive_failures", 2, minimum=1)
    cooldown_minutes = _settings_int("range_mr_cooldown_minutes", 30, minimum=0)
    breakout_cooldown_minutes = _settings_int("range_mr_breakout_cooldown_minutes", 60, minimum=0)
    gate: dict[str, Any] = {
        "applied": False,
        "enabled": enabled,
        "status": "disabled" if not enabled else "not_applicable",
        "reason_codes": [],
        "max_consecutive_failures": max_failures,
        "cooldown_minutes": cooldown_minutes,
        "breakout_cooldown_minutes": breakout_cooldown_minutes,
    }
    if not enabled:
        return [], gate
    identity = build_range_mr_identity(decision, decision_context)
    if identity is None:
        return [], gate

    gate.update({"applied": True, **{key: identity[key] for key in ("strategy_id", "symbol", "direction", "regime_id", "range_id")}})
    state = _state_for_identity(session, identity)
    if state is not None and state.cooldown_until is not None and state.cooldown_until <= now:
        _release_cooldown(session, state, identity, reason="cooldown_elapsed", now=now)
        session.flush()

    breakout_direction = _range_breakout_direction(decision_context)
    if _adverse_breakout_for_side(str(identity["direction"]), breakout_direction):
        state = _get_or_create_state(session, identity)
        state.last_breakout_at = now
        _enter_cooldown(
            session,
            state,
            identity,
            reason_code=RANGE_MR_BREAKOUT_COOLDOWN_REASON_CODE,
            cooldown_minutes=breakout_cooldown_minutes,
            now=now,
            extra_payload={"range_breakout_direction": breakout_direction},
        )
        session.flush()

    state = _state_for_identity(session, identity)
    if state is None:
        gate["status"] = "clear"
        return [], gate
    active = state.cooldown_until is not None and state.cooldown_until > now
    gate.update(_cooldown_payload(state, identity))
    gate["range_breakout_direction"] = breakout_direction
    if not active:
        gate["status"] = "clear"
        return [], gate
    reason_code = str(state.cooldown_reason or RANGE_MR_COOLDOWN_BLOCK_REASON_CODE)
    gate["status"] = "blocked"
    gate["reason_code"] = reason_code
    gate["reason_codes"] = [RANGE_MR_COOLDOWN_BLOCK_REASON_CODE, reason_code]
    return list(dict.fromkeys([RANGE_MR_COOLDOWN_BLOCK_REASON_CODE, reason_code])), gate


def _position_identity(position: Position, tags: dict[str, Any]) -> dict[str, Any] | None:
    side = _normalized_side(tags.get("decision") or position.side)
    if side is None:
        return None
    strategy_id = _text(tags.get("strategy_id") or tags.get("strategy_engine"))
    if _normalized_strategy_id(strategy_id) != RANGE_MEAN_REVERSION_STRATEGY_ID:
        return None
    symbol = (_text(tags.get("symbol")) or _text(position.symbol)).upper()
    timeframe = _text(tags.get("timeframe")) or "15m"
    regime_id = _text(tags.get("regime_id")) or "unknown"
    range_id = (
        _text(tags.get("range_id"))
        or _range_id_from_bounds(
            symbol=symbol,
            timeframe=timeframe,
            regime_id=regime_id,
            range_low=tags.get("range_low"),
            range_high=tags.get("range_high"),
        )
        or regime_id
    )
    return {
        "strategy_id": RANGE_MEAN_REVERSION_STRATEGY_ID,
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": side,
        "regime_id": regime_id,
        "range_id": range_id,
        "trade_performance_tags": tags,
        "current_market_state": {},
    }


def _position_net_result(position: Position) -> tuple[float | None, float | None]:
    metadata = _as_dict(position.metadata_json)
    closed_pnl = _as_dict(metadata.get("closed_position_pnl"))
    net_r_multiple = _optional_float(closed_pnl.get("net_r_multiple"))
    net_realized_pnl = _optional_float(closed_pnl.get("net_realized_pnl"))
    if net_realized_pnl is None:
        net_realized_pnl = _optional_float(position.realized_pnl)
    return net_r_multiple, net_realized_pnl


def record_range_mr_trade_result(
    session: Session,
    position: Position,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    now = now or utcnow_naive()
    metadata = _as_dict(position.metadata_json)
    tags = _as_dict(metadata.get("trade_performance_tags"))
    identity = _position_identity(position, tags)
    if identity is None:
        return None

    max_failures = _settings_int("range_mr_max_consecutive_failures", 2, minimum=1)
    cooldown_minutes = _settings_int("range_mr_cooldown_minutes", 30, minimum=0)
    state = _get_or_create_state(session, identity)
    if position.id is not None and state.last_closed_position_id == position.id:
        return {
            **_cooldown_payload(state, identity),
            "status": "already_recorded",
            "position_id": position.id,
        }

    net_r_multiple, net_realized_pnl = _position_net_result(position)
    loss = net_r_multiple < 0 if net_r_multiple is not None else (net_realized_pnl or 0.0) < 0
    previous_failures = state.consecutive_failures
    state.last_closed_position_id = position.id
    if loss:
        state.consecutive_failures = previous_failures + 1
        state.last_failure_at = position.closed_at or now
        if state.consecutive_failures >= max_failures:
            _enter_cooldown(
                session,
                state,
                identity,
                reason_code=RANGE_MR_CONSECUTIVE_FAILURES_REASON_CODE,
                cooldown_minutes=cooldown_minutes,
                now=now,
                extra_payload={"position_id": position.id},
            )
    else:
        state.consecutive_failures = 0
        if state.cooldown_until is not None:
            _release_cooldown(session, state, identity, reason="profitable_or_flat_trade", now=now)

    state.metadata_json = {
        **_as_dict(state.metadata_json),
        "last_position_id": position.id,
        "last_position_closed_at": position.closed_at.isoformat() if position.closed_at is not None else None,
        "last_net_r_multiple": net_r_multiple,
        "last_net_realized_pnl": net_realized_pnl,
        "last_result": "loss" if loss else "non_loss",
        "previous_consecutive_failures": previous_failures,
        "max_consecutive_failures": max_failures,
    }
    session.add(state)
    payload = {
        **_cooldown_payload(state, identity),
        "position_id": position.id,
        "position_closed_at": position.closed_at.isoformat() if position.closed_at is not None else None,
        "net_r_multiple": net_r_multiple,
        "net_realized_pnl": net_realized_pnl,
        "result": "loss" if loss else "non_loss",
        "previous_consecutive_failures": previous_failures,
        "max_consecutive_failures": max_failures,
    }
    record_audit_event(
        session,
        event_type="range_mean_reversion_trade_result_recorded",
        entity_type="position",
        entity_id=str(position.id),
        severity="warning" if loss else "info",
        message="Range mean reversion trade result was recorded for cooldown tracking.",
        payload=payload,
    )
    session.flush()
    return payload
