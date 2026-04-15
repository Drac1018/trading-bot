from __future__ import annotations

from typing import Any, Literal

from sqlalchemy.orm import Session

from trading_mvp.config import get_settings
from trading_mvp.models import RiskCheck, Setting
from trading_mvp.schemas import MarketSnapshotPayload, RiskCheckResult, TradeDecision
from trading_mvp.services.account import (
    get_latest_pnl_snapshot,
    get_open_position,
    get_open_positions,
)
from trading_mvp.services.runtime_state import (
    DEGRADED_MANAGE_ONLY_STATE,
    EMERGENCY_EXIT_STATE,
    PROTECTION_REQUIRED_STATE,
    build_sync_freshness_summary,
    get_operating_state,
)
from trading_mvp.services.settings import (
    get_exposure_limits,
    get_runtime_credentials,
    is_live_execution_armed,
)

HARD_MAX_GLOBAL_LEVERAGE = 5.0
HARD_MAX_RISK_PER_TRADE = 0.02
HARD_MAX_DAILY_LOSS = 0.05
BTC_SYMBOLS = {"BTCUSDT"}
MAJOR_ALT_SYMBOLS = {"ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT"}
SYNC_BLOCKING_REASON_CODES = {
    "account": "ACCOUNT_STATE_STALE",
    "positions": "POSITION_STATE_STALE",
    "open_orders": "OPEN_ORDERS_STATE_STALE",
    "protective_orders": "PROTECTION_STATE_UNVERIFIED",
}
SURVIVAL_PATH_DECISIONS = {"reduce", "exit"}
AUTO_RESIZE_REASON_CODE_MAP = {
    "gross_exposure_headroom_notional": "ENTRY_CLAMPED_TO_GROSS_EXPOSURE_LIMIT",
    "directional_headroom_notional": "ENTRY_CLAMPED_TO_DIRECTIONAL_LIMIT",
    "single_position_headroom_notional": "ENTRY_CLAMPED_TO_SINGLE_POSITION_LIMIT",
    "same_tier_headroom_notional": "ENTRY_CLAMPED_TO_SAME_TIER_LIMIT",
}


def validate_decision_schema(payload: dict[str, Any]) -> TradeDecision:
    return TradeDecision.model_validate(payload)


def is_survival_path_decision(decision: TradeDecision | str) -> bool:
    value = decision.decision if isinstance(decision, TradeDecision) else str(decision)
    return value in SURVIVAL_PATH_DECISIONS


def _entry_price(decision: TradeDecision, market_snapshot: MarketSnapshotPayload) -> float:
    if decision.entry_zone_min is not None and decision.entry_zone_max is not None:
        return (decision.entry_zone_min + decision.entry_zone_max) / 2
    return market_snapshot.latest_price


def _entry_zone_bounds(decision: TradeDecision, market_snapshot: MarketSnapshotPayload) -> tuple[float, float]:
    entry_min = decision.entry_zone_min if decision.entry_zone_min is not None else market_snapshot.latest_price
    entry_max = decision.entry_zone_max if decision.entry_zone_max is not None else market_snapshot.latest_price
    if entry_min > entry_max:
        return entry_max, entry_min
    return entry_min, entry_max


def _entry_trigger_reason_codes(
    decision: TradeDecision,
    market_snapshot: MarketSnapshotPayload,
) -> list[str]:
    if decision.decision not in {"long", "short"}:
        return []

    latest_price = market_snapshot.latest_price
    entry_price = _entry_price(decision, market_snapshot)
    entry_min, entry_max = _entry_zone_bounds(decision, market_snapshot)
    invalidation_price = decision.invalidation_price
    mode = decision.entry_mode or "none"
    last_candle = market_snapshot.candles[-1] if market_snapshot.candles else None
    reason_codes: list[str] = []

    if invalidation_price is None or invalidation_price <= 0:
        reason_codes.append("INVALID_INVALIDATION_PRICE")
    elif decision.decision == "long":
        if invalidation_price >= min(entry_price, latest_price):
            reason_codes.append("INVALID_INVALIDATION_PRICE")
    elif invalidation_price <= max(entry_price, latest_price):
        reason_codes.append("INVALID_INVALIDATION_PRICE")

    if decision.max_chase_bps is not None:
        if decision.decision == "long":
            chase_anchor = max(entry_price, entry_max)
            chase_bps = max(((latest_price - chase_anchor) / max(chase_anchor, 1.0)) * 10_000, 0.0)
        else:
            chase_anchor = min(entry_price, entry_min)
            chase_bps = max(((chase_anchor - latest_price) / max(chase_anchor, 1.0)) * 10_000, 0.0)
        if chase_bps > decision.max_chase_bps:
            reason_codes.append("CHASE_LIMIT_EXCEEDED")

    if mode == "immediate":
        return list(dict.fromkeys(reason_codes))

    if mode == "breakout_confirm":
        if decision.decision == "long":
            breakout_confirmed = latest_price >= entry_max or (last_candle is not None and last_candle.high >= entry_max)
        else:
            breakout_confirmed = latest_price <= entry_min or (last_candle is not None and last_candle.low <= entry_min)
        if not breakout_confirmed:
            reason_codes.append("ENTRY_TRIGGER_NOT_MET")
    elif mode == "pullback_confirm":
        if not (entry_min <= latest_price <= entry_max):
            reason_codes.append("ENTRY_TRIGGER_NOT_MET")
    else:
        reason_codes.append("ENTRY_TRIGGER_NOT_MET")

    return list(dict.fromkeys(reason_codes))


def get_symbol_risk_tier(symbol: str) -> Literal["btc", "major_alt", "alt"]:
    normalized = symbol.upper()
    if normalized in BTC_SYMBOLS:
        return "btc"
    if normalized in MAJOR_ALT_SYMBOLS:
        return "major_alt"
    return "alt"


def get_symbol_leverage_cap(symbol: str) -> float:
    tier = get_symbol_risk_tier(symbol)
    if tier == "btc":
        return 5.0
    if tier == "major_alt":
        return 3.0
    return 2.0


def _effective_leverage_cap(settings_row: Setting, symbol: str) -> float:
    return min(HARD_MAX_GLOBAL_LEVERAGE, settings_row.max_leverage, get_symbol_leverage_cap(symbol))


def _position_notional(quantity: float, price: float) -> float:
    return abs(quantity) * max(price, 0.0)


def _decision_matches_position_side(position_side: str, decision: str) -> bool:
    return (position_side == "long" and decision == "long") or (position_side == "short" and decision == "short")


def _estimate_projected_notional(
    decision: TradeDecision,
    market_snapshot: MarketSnapshotPayload,
    *,
    equity: float,
    approved_risk_pct: float,
    approved_leverage: float,
) -> float:
    entry_price = _entry_price(decision, market_snapshot)
    safe_entry_price = max(entry_price, 1.0)
    if decision.stop_loss is None:
        quantity = max((equity * min(approved_leverage, 1.0)) / safe_entry_price, 0.0001)
        return _position_notional(quantity, safe_entry_price)
    per_unit_risk = abs(entry_price - decision.stop_loss)
    if per_unit_risk == 0:
        return 0.0
    risk_budget = max(equity, 0.0) * approved_risk_pct
    max_notional_quantity = (max(equity, 0.0) * approved_leverage) / safe_entry_price
    quantity = min(risk_budget / per_unit_risk, max_notional_quantity)
    return _position_notional(quantity, safe_entry_price)


def _estimate_projected_entry_size(
    decision: TradeDecision,
    market_snapshot: MarketSnapshotPayload,
    *,
    equity: float,
    approved_risk_pct: float,
    approved_leverage: float,
) -> dict[str, float]:
    entry_price = _entry_price(decision, market_snapshot)
    safe_entry_price = max(entry_price, 1.0)
    if decision.stop_loss is None:
        quantity = max((equity * min(approved_leverage, 1.0)) / safe_entry_price, 0.0001)
    else:
        per_unit_risk = abs(entry_price - decision.stop_loss)
        if per_unit_risk == 0:
            quantity = 0.0
        else:
            risk_budget = max(equity, 0.0) * approved_risk_pct
            max_notional_quantity = (max(equity, 0.0) * approved_leverage) / safe_entry_price
            quantity = min(risk_budget / per_unit_risk, max_notional_quantity)
    notional = _position_notional(quantity, safe_entry_price)
    return {
        "entry_price": round(safe_entry_price, 6),
        "quantity": round(max(quantity, 0.0), 6),
        "notional": round(max(notional, 0.0), 6),
    }


def _minimum_actionable_notional(entry_price: float) -> float:
    return round(max(25.0, max(entry_price, 1.0) * 0.0005), 6)


def _build_exposure_headroom_snapshot(
    *,
    exposure_metrics: dict[str, float],
    exposure_limits: dict[str, float],
    equity: float,
    decision_side: str,
) -> dict[str, float]:
    safe_equity = max(equity, 1.0)
    directional_metric_key = "long_exposure_pct_equity" if decision_side == "long" else "short_exposure_pct_equity"
    snapshot = {
        "gross_exposure_headroom_notional": max(
            exposure_limits["gross_exposure_pct"] - exposure_metrics["gross_exposure_pct_equity"],
            0.0,
        )
        * safe_equity,
        "directional_headroom_notional": max(
            exposure_limits["directional_bias_pct"] - exposure_metrics[directional_metric_key],
            0.0,
        )
        * safe_equity,
        "single_position_headroom_notional": max(
            exposure_limits["largest_position_pct"] - exposure_metrics["decision_symbol_concentration_pct"],
            0.0,
        )
        * safe_equity,
        "same_tier_headroom_notional": max(
            exposure_limits["same_tier_concentration_pct"] - exposure_metrics["same_tier_concentration_pct"],
            0.0,
        )
        * safe_equity,
    }
    limiting_key = min(snapshot, key=snapshot.get)
    snapshot["limiting_headroom_notional"] = round(snapshot[limiting_key], 6)
    return {key: round(value, 6) for key, value in snapshot.items()}


def _build_exposure_metrics(
    session: Session,
    decision_symbol: str,
    equity: float,
    *,
    projected_side: str | None = None,
    projected_notional: float = 0.0,
) -> dict[str, float]:
    positions = get_open_positions(session)
    decision_tier = get_symbol_risk_tier(decision_symbol)
    total_notional = 0.0
    long_notional = 0.0
    short_notional = 0.0
    decision_symbol_notional = 0.0
    same_tier_notional = 0.0
    symbol_notionals: dict[str, float] = {}

    for position in positions:
        mark_price = position.mark_price if position.mark_price > 0 else position.entry_price
        notional = _position_notional(position.quantity, mark_price)
        total_notional += notional
        symbol_key = position.symbol.upper()
        symbol_notionals[symbol_key] = symbol_notionals.get(symbol_key, 0.0) + notional
        if position.side == "long":
            long_notional += notional
        else:
            short_notional += notional
        if symbol_key == decision_symbol.upper():
            decision_symbol_notional += notional
        if get_symbol_risk_tier(position.symbol) == decision_tier:
            same_tier_notional += notional

    if projected_side in {"long", "short"} and projected_notional > 0:
        symbol_key = decision_symbol.upper()
        total_notional += projected_notional
        symbol_notionals[symbol_key] = symbol_notionals.get(symbol_key, 0.0) + projected_notional
        if projected_side == "long":
            long_notional += projected_notional
        else:
            short_notional += projected_notional
        decision_symbol_notional += projected_notional
        same_tier_notional += projected_notional

    safe_equity = max(equity, 1.0)
    dominant_side_notional = max(long_notional, short_notional)
    largest_symbol_notional = max(symbol_notionals.values(), default=0.0)
    return {
        "total_notional": round(total_notional, 6),
        "long_notional": round(long_notional, 6),
        "short_notional": round(short_notional, 6),
        "decision_symbol_notional": round(decision_symbol_notional, 6),
        "largest_symbol_notional": round(largest_symbol_notional, 6),
        "gross_exposure_pct_equity": round(total_notional / safe_equity, 6),
        "long_exposure_pct_equity": round(long_notional / safe_equity, 6),
        "short_exposure_pct_equity": round(short_notional / safe_equity, 6),
        "directional_bias_pct": round(dominant_side_notional / safe_equity, 6),
        "decision_symbol_concentration_pct": round(decision_symbol_notional / safe_equity, 6),
        "same_tier_concentration_pct": round(same_tier_notional / safe_equity, 6),
        "largest_position_pct_equity": round(largest_symbol_notional / safe_equity, 6),
        "projected_trade_notional_pct_equity": round(projected_notional / safe_equity, 6),
        "open_position_count": float(len(positions)),
    }


def build_ai_risk_budget_context(
    session: Session,
    settings_row: Setting,
    *,
    decision_symbol: str,
    equity: float,
) -> dict[str, float]:
    symbol = decision_symbol.upper()
    limits = get_exposure_limits(settings_row)
    metrics = _build_exposure_metrics(session, symbol, equity)
    safe_equity = max(equity, 1.0)
    effective_leverage_cap = _effective_leverage_cap(settings_row, symbol)

    total_exposure_headroom = max(
        limits["gross_exposure_pct"] - float(metrics["gross_exposure_pct_equity"]),
        0.0,
    ) * safe_equity
    directional_long_headroom = max(
        limits["directional_bias_pct"] - float(metrics["long_exposure_pct_equity"]),
        0.0,
    ) * safe_equity
    directional_short_headroom = max(
        limits["directional_bias_pct"] - float(metrics["short_exposure_pct_equity"]),
        0.0,
    ) * safe_equity
    single_position_headroom = max(
        limits["largest_position_pct"] - float(metrics["decision_symbol_concentration_pct"]),
        0.0,
    ) * safe_equity

    max_additional_long_notional = min(total_exposure_headroom, directional_long_headroom)
    max_additional_short_notional = min(total_exposure_headroom, directional_short_headroom)
    max_new_position_notional_for_symbol = min(
        total_exposure_headroom,
        single_position_headroom,
        max(max_additional_long_notional, max_additional_short_notional),
    )

    return {
        "max_additional_long_notional": round(max(max_additional_long_notional, 0.0), 4),
        "max_additional_short_notional": round(max(max_additional_short_notional, 0.0), 4),
        "max_new_position_notional_for_symbol": round(max(max_new_position_notional_for_symbol, 0.0), 4),
        "max_leverage_for_symbol": round(effective_leverage_cap, 4),
        "directional_bias_headroom": round(max(max(directional_long_headroom, directional_short_headroom), 0.0), 4),
        "single_position_headroom": round(max(single_position_headroom, 0.0), 4),
        "total_exposure_headroom": round(max(total_exposure_headroom, 0.0), 4),
    }


def build_current_exposure_summary(
    session: Session,
    settings_row: Setting,
    *,
    equity: float,
    reference_symbol: str | None = None,
) -> dict[str, object]:
    symbol = (reference_symbol or settings_row.default_symbol).upper()
    limits = get_exposure_limits(settings_row)
    metrics = _build_exposure_metrics(session, symbol, equity)
    headroom = {
        "gross_exposure_pct": round(
            max(limits["gross_exposure_pct"] - metrics["gross_exposure_pct_equity"], 0.0),
            6,
        ),
        "largest_position_pct": round(
            max(limits["largest_position_pct"] - metrics["largest_position_pct_equity"], 0.0),
            6,
        ),
        "directional_bias_pct": round(
            max(limits["directional_bias_pct"] - metrics["directional_bias_pct"], 0.0),
            6,
        ),
        "same_tier_concentration_pct": round(
            max(
                limits["same_tier_concentration_pct"]
                - metrics["same_tier_concentration_pct"],
                0.0,
            ),
            6,
        ),
    }
    blocked = [
        headroom["gross_exposure_pct"] <= 0.0,
        headroom["largest_position_pct"] <= 0.0,
        headroom["directional_bias_pct"] <= 0.0,
        headroom["same_tier_concentration_pct"] <= 0.0,
    ]
    near_limit = [
        headroom["gross_exposure_pct"] < 0.1,
        headroom["largest_position_pct"] < 0.05,
        headroom["directional_bias_pct"] < 0.1,
        headroom["same_tier_concentration_pct"] < 0.1,
    ]
    status = "ok"
    if any(blocked):
        status = "at_limit"
    elif any(near_limit):
        status = "near_limit"
    return {
        "reference_symbol": symbol,
        "reference_tier": get_symbol_risk_tier(symbol),
        "metrics": metrics,
        "limits": limits,
        "headroom": headroom,
        "status": status,
    }


def evaluate_risk(
    session: Session,
    settings_row: Setting,
    decision: TradeDecision,
    market_snapshot: MarketSnapshotPayload,
    decision_run_id: int | None = None,
    market_snapshot_id: int | None = None,
    execution_mode: Literal["live", "historical_replay"] = "live",
) -> tuple[RiskCheckResult, RiskCheck]:
    reason_codes: list[str] = []
    defaults = get_settings()
    live_requested = settings_row.live_trading_enabled
    operating_mode: Literal["live", "paused", "hold"] = "live"
    operating_state = get_operating_state(settings_row)
    existing_position = get_open_position(session, decision.symbol)
    is_protection_recovery = bool(
        existing_position is not None
        and operating_state in {PROTECTION_REQUIRED_STATE, DEGRADED_MANAGE_ONLY_STATE}
        and decision.decision in {"long", "short"}
        and _decision_matches_position_side(existing_position.side, decision.decision)
        and decision.stop_loss is not None
        and decision.take_profit is not None
    )
    is_entry_decision = decision.decision in {"long", "short"} and not is_protection_recovery
    latest_pnl = get_latest_pnl_snapshot(session, settings_row)
    credentials = get_runtime_credentials(settings_row)
    symbol_risk_tier = get_symbol_risk_tier(decision.symbol)
    effective_leverage_cap = _effective_leverage_cap(settings_row, decision.symbol)
    effective_risk_cap = min(settings_row.max_risk_per_trade, HARD_MAX_RISK_PER_TRADE)
    effective_daily_loss_cap = min(settings_row.max_daily_loss, HARD_MAX_DAILY_LOSS)
    exposure_limits = get_exposure_limits(settings_row)
    raw_projected_notional = 0.0
    approved_projected_notional = 0.0
    approved_quantity: float | None = None
    auto_resized_entry = False
    size_adjustment_ratio = 0.0
    auto_resize_reason: str | None = None
    current_exposure_metrics = _build_exposure_metrics(
        session,
        decision.symbol,
        latest_pnl.equity,
    )
    exposure_metrics = current_exposure_metrics
    exposure_headroom_snapshot: dict[str, float] = {}
    raw_projected_quantity = 0.0
    if is_entry_decision:
        raw_size = _estimate_projected_entry_size(
            decision,
            market_snapshot,
            equity=latest_pnl.equity,
            approved_risk_pct=min(decision.risk_pct, effective_risk_cap),
            approved_leverage=min(decision.leverage, effective_leverage_cap),
        )
        raw_projected_notional = raw_size["notional"]
        raw_projected_quantity = raw_size["quantity"]
        approved_projected_notional = raw_projected_notional
        approved_quantity = raw_projected_quantity if raw_projected_quantity > 0 else None
        exposure_headroom_snapshot = _build_exposure_headroom_snapshot(
            exposure_metrics=current_exposure_metrics,
            exposure_limits=exposure_limits,
            equity=latest_pnl.equity,
            decision_side=decision.decision,
        )
    sync_freshness_summary = build_sync_freshness_summary(settings_row)

    if settings_row.trading_paused and is_entry_decision:
        reason_codes.append("TRADING_PAUSED")
        operating_mode = "paused"
    if operating_state == PROTECTION_REQUIRED_STATE and is_entry_decision:
        reason_codes.append(PROTECTION_REQUIRED_STATE)
    if operating_state == DEGRADED_MANAGE_ONLY_STATE and is_entry_decision:
        reason_codes.append(DEGRADED_MANAGE_ONLY_STATE)
    if operating_state == EMERGENCY_EXIT_STATE and is_entry_decision:
        reason_codes.append(EMERGENCY_EXIT_STATE)
    if market_snapshot.is_stale and is_entry_decision:
        reason_codes.append("STALE_MARKET_DATA")
    if not market_snapshot.is_complete and is_entry_decision:
        reason_codes.append("INCOMPLETE_MARKET_DATA")
    if is_entry_decision and latest_pnl.daily_pnl < 0 and abs(latest_pnl.daily_pnl) / max(latest_pnl.equity, 1.0) >= effective_daily_loss_cap:
        reason_codes.append("DAILY_LOSS_LIMIT_REACHED")
    if latest_pnl.consecutive_losses >= settings_row.max_consecutive_losses and is_entry_decision:
        reason_codes.append("MAX_CONSECUTIVE_LOSSES_REACHED")
    if is_entry_decision and decision.leverage > effective_leverage_cap:
        reason_codes.append("LEVERAGE_EXCEEDS_LIMIT")
    if is_entry_decision and decision.risk_pct > effective_risk_cap:
        reason_codes.append("RISK_PCT_EXCEEDS_LIMIT")
    if is_entry_decision and (decision.stop_loss is None or decision.take_profit is None):
        reason_codes.append("MISSING_STOP_OR_TARGET")
    if is_entry_decision:
        reason_codes.extend(_entry_trigger_reason_codes(decision, market_snapshot))
    if is_entry_decision and live_requested:
        for scope, reason_code in SYNC_BLOCKING_REASON_CODES.items():
            scope_summary = sync_freshness_summary.get(scope)
            if not isinstance(scope_summary, dict):
                reason_codes.append(reason_code)
                continue
            if bool(scope_summary.get("stale")) or bool(scope_summary.get("incomplete")):
                reason_codes.append(reason_code)

    if is_protection_recovery and existing_position is not None:
        entry = existing_position.mark_price if existing_position.mark_price > 0 else existing_position.entry_price
    else:
        entry = _entry_price(decision, market_snapshot)
    if decision.decision == "long" and decision.stop_loss is not None and decision.take_profit is not None:
        if decision.stop_loss >= entry or decision.take_profit <= entry:
            reason_codes.append("INVALID_PROTECTION_BRACKETS" if is_protection_recovery else "INVALID_LONG_BRACKETS")
    if decision.decision == "short" and decision.stop_loss is not None and decision.take_profit is not None:
        if decision.stop_loss <= entry or decision.take_profit >= entry:
            reason_codes.append("INVALID_PROTECTION_BRACKETS" if is_protection_recovery else "INVALID_SHORT_BRACKETS")

    slippage = abs(entry - market_snapshot.latest_price) / max(market_snapshot.latest_price, 1.0)
    if slippage > settings_row.slippage_threshold_pct and is_entry_decision:
        reason_codes.append("SLIPPAGE_THRESHOLD_EXCEEDED")
    if decision.decision == "hold":
        reason_codes.append("HOLD_DECISION")
        operating_mode = "hold" if operating_mode != "paused" else operating_mode

    enforce_live_readiness = execution_mode != "historical_replay"
    if enforce_live_readiness and (not credentials.binance_api_key or not credentials.binance_api_secret):
        if decision.decision != "hold":
            reason_codes.append("LIVE_CREDENTIALS_MISSING")
    if enforce_live_readiness and is_entry_decision and live_requested:
        if not defaults.live_trading_env_enabled:
            reason_codes.append("LIVE_ENV_DISABLED")
        if not settings_row.manual_live_approval:
            reason_codes.append("LIVE_APPROVAL_POLICY_DISABLED")
        if not is_live_execution_armed(settings_row):
            reason_codes.append("LIVE_APPROVAL_REQUIRED")
    elif enforce_live_readiness and is_entry_decision:
        reason_codes.append("LIVE_TRADING_DISABLED")

    hard_blockers_present = len(reason_codes) > 0
    exposure_limit_codes: list[str] = []
    if is_entry_decision:
        gross_headroom = exposure_headroom_snapshot.get("gross_exposure_headroom_notional", 0.0)
        directional_headroom = exposure_headroom_snapshot.get("directional_headroom_notional", 0.0)
        single_position_headroom = exposure_headroom_snapshot.get("single_position_headroom_notional", 0.0)
        same_tier_headroom = exposure_headroom_snapshot.get("same_tier_headroom_notional", 0.0)
        limiting_key = min(
            AUTO_RESIZE_REASON_CODE_MAP,
            key=lambda key: exposure_headroom_snapshot.get(key, 0.0),
        )
        max_additional_notional = max(exposure_headroom_snapshot.get(limiting_key, 0.0), 0.0)
        minimum_actionable_notional = _minimum_actionable_notional(_entry_price(decision, market_snapshot))
        exposure_headroom_snapshot["minimum_actionable_notional"] = minimum_actionable_notional

        if raw_projected_notional > gross_headroom:
            exposure_limit_codes.append("GROSS_EXPOSURE_LIMIT_REACHED")
        if raw_projected_notional > directional_headroom:
            exposure_limit_codes.append("DIRECTIONAL_BIAS_LIMIT_REACHED")
        if raw_projected_notional > single_position_headroom:
            exposure_limit_codes.append("LARGEST_POSITION_LIMIT_REACHED")
        if raw_projected_notional > same_tier_headroom:
            exposure_limit_codes.append("SAME_TIER_CONCENTRATION_LIMIT_REACHED")

        if exposure_limit_codes:
            if not hard_blockers_present and max_additional_notional >= minimum_actionable_notional:
                approved_projected_notional = min(raw_projected_notional, max_additional_notional)
                if approved_projected_notional < raw_projected_notional:
                    auto_resized_entry = True
                    size_adjustment_ratio = round(
                        approved_projected_notional / max(raw_projected_notional, 1e-9),
                        6,
                    )
                    approved_quantity = round(
                        min(
                            raw_projected_quantity,
                            approved_projected_notional / max(_entry_price(decision, market_snapshot), 1.0),
                        ),
                        6,
                    )
                    auto_resize_reason = {
                        "gross_exposure_headroom_notional": "CLAMPED_TO_GROSS_EXPOSURE_HEADROOM",
                        "directional_headroom_notional": "CLAMPED_TO_DIRECTIONAL_HEADROOM",
                        "single_position_headroom_notional": "CLAMPED_TO_SINGLE_POSITION_HEADROOM",
                        "same_tier_headroom_notional": "CLAMPED_TO_SAME_TIER_HEADROOM",
                    }[limiting_key]
                    reason_codes.extend(["ENTRY_AUTO_RESIZED", AUTO_RESIZE_REASON_CODE_MAP[limiting_key]])
                else:
                    size_adjustment_ratio = 1.0
            else:
                reason_codes.extend(exposure_limit_codes)
                if max_additional_notional < minimum_actionable_notional:
                    reason_codes.append("ENTRY_SIZE_BELOW_MIN_NOTIONAL")

        if approved_projected_notional > 0:
            exposure_metrics = _build_exposure_metrics(
                session,
                decision.symbol,
                latest_pnl.equity,
                projected_side=decision.decision,
                projected_notional=approved_projected_notional,
            )
            if size_adjustment_ratio == 0.0 and raw_projected_notional > 0:
                size_adjustment_ratio = round(
                    approved_projected_notional / max(raw_projected_notional, 1e-9),
                    6,
                )

    reason_codes = list(dict.fromkeys(reason_codes))
    allowed = len(reason_codes) == 0 or (
        is_entry_decision
        and auto_resized_entry
        and all(
            code in {"ENTRY_AUTO_RESIZED", *AUTO_RESIZE_REASON_CODE_MAP.values()}
            for code in reason_codes
        )
    )

    approved_risk_pct = 0.0
    if allowed:
        if is_entry_decision and raw_projected_notional > 0:
            approved_risk_pct = round(
                min(
                    decision.risk_pct * (approved_projected_notional / max(raw_projected_notional, 1e-9)),
                    effective_risk_cap,
                ),
                6,
            )
        else:
            approved_risk_pct = decision.risk_pct
    result = RiskCheckResult(
        allowed=allowed,
        decision=decision.decision,
        reason_codes=reason_codes,
        approved_risk_pct=approved_risk_pct if allowed else 0.0,
        approved_leverage=min(decision.leverage, effective_leverage_cap) if allowed else 0.0,
        raw_projected_notional=raw_projected_notional,
        approved_projected_notional=approved_projected_notional if allowed else 0.0,
        approved_quantity=approved_quantity if allowed else None,
        auto_resized_entry=auto_resized_entry if allowed else False,
        size_adjustment_ratio=size_adjustment_ratio if allowed else 0.0,
        exposure_headroom_snapshot=exposure_headroom_snapshot,
        auto_resize_reason=auto_resize_reason if allowed else None,
        operating_mode=operating_mode if not allowed else "live",
        operating_state=operating_state,
        effective_leverage_cap=effective_leverage_cap,
        symbol_risk_tier=symbol_risk_tier,
        exposure_metrics=exposure_metrics,
        sync_freshness_summary=sync_freshness_summary,
    )
    row = RiskCheck(
        symbol=decision.symbol,
        decision_run_id=decision_run_id,
        market_snapshot_id=market_snapshot_id,
        allowed=result.allowed,
        decision=result.decision,
        reason_codes=result.reason_codes,
        approved_risk_pct=result.approved_risk_pct,
        approved_leverage=result.approved_leverage,
        payload=result.model_dump(mode="json"),
    )
    session.add(row)
    session.flush()
    return result, row
