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


def validate_decision_schema(payload: dict[str, Any]) -> TradeDecision:
    return TradeDecision.model_validate(payload)


def _entry_price(decision: TradeDecision, market_snapshot: MarketSnapshotPayload) -> float:
    if decision.entry_zone_min is not None and decision.entry_zone_max is not None:
        return (decision.entry_zone_min + decision.entry_zone_max) / 2
    return market_snapshot.latest_price


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
    projected_notional = 0.0
    if is_entry_decision:
        projected_notional = _estimate_projected_notional(
            decision,
            market_snapshot,
            equity=latest_pnl.equity,
            approved_risk_pct=min(decision.risk_pct, effective_risk_cap),
            approved_leverage=min(decision.leverage, effective_leverage_cap),
        )
    exposure_metrics = _build_exposure_metrics(
        session,
        decision.symbol,
        latest_pnl.equity,
        projected_side=decision.decision if is_entry_decision else None,
        projected_notional=projected_notional,
    )

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
    if is_entry_decision and exposure_metrics["gross_exposure_pct_equity"] > exposure_limits["gross_exposure_pct"]:
        reason_codes.append("GROSS_EXPOSURE_LIMIT_REACHED")
    if is_entry_decision and exposure_metrics["largest_position_pct_equity"] > exposure_limits["largest_position_pct"]:
        reason_codes.append("LARGEST_POSITION_LIMIT_REACHED")
    if is_entry_decision and exposure_metrics["directional_bias_pct"] > exposure_limits["directional_bias_pct"]:
        reason_codes.append("DIRECTIONAL_BIAS_LIMIT_REACHED")
    if (
        is_entry_decision
        and exposure_metrics["same_tier_concentration_pct"] > exposure_limits["same_tier_concentration_pct"]
    ):
        reason_codes.append("SAME_TIER_CONCENTRATION_LIMIT_REACHED")

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
    allowed = len(reason_codes) == 0
    result = RiskCheckResult(
        allowed=allowed,
        decision=decision.decision,
        reason_codes=reason_codes,
        approved_risk_pct=decision.risk_pct if allowed else 0.0,
        approved_leverage=min(decision.leverage, effective_leverage_cap) if allowed else 0.0,
        operating_mode=operating_mode if not allowed else "live",
        operating_state=operating_state,
        effective_leverage_cap=effective_leverage_cap,
        symbol_risk_tier=symbol_risk_tier,
        exposure_metrics=exposure_metrics,
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
