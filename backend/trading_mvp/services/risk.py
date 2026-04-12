from __future__ import annotations

from typing import Any, Literal

from sqlalchemy.orm import Session

from trading_mvp.config import get_settings
from trading_mvp.models import RiskCheck, Setting
from trading_mvp.schemas import MarketSnapshotPayload, RiskCheckResult, TradeDecision
from trading_mvp.services.account import get_latest_pnl_snapshot, get_open_positions
from trading_mvp.services.settings import get_runtime_credentials, is_live_execution_armed

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


def _build_exposure_metrics(session: Session, decision_symbol: str, equity: float) -> dict[str, float]:
    positions = get_open_positions(session)
    decision_tier = get_symbol_risk_tier(decision_symbol)
    total_notional = 0.0
    long_notional = 0.0
    short_notional = 0.0
    decision_symbol_notional = 0.0
    same_tier_notional = 0.0
    largest_symbol_notional = 0.0

    for position in positions:
        mark_price = position.mark_price if position.mark_price > 0 else position.entry_price
        notional = _position_notional(position.quantity, mark_price)
        total_notional += notional
        if position.side == "long":
            long_notional += notional
        else:
            short_notional += notional
        if position.symbol.upper() == decision_symbol.upper():
            decision_symbol_notional += notional
        if get_symbol_risk_tier(position.symbol) == decision_tier:
            same_tier_notional += notional
        largest_symbol_notional = max(largest_symbol_notional, notional)

    safe_equity = max(equity, 1.0)
    safe_total = max(total_notional, 1.0)
    directional_bias = max(long_notional, short_notional) / safe_total if total_notional > 0 else 0.0
    return {
        "gross_exposure_pct_equity": round(total_notional / safe_equity, 6),
        "long_exposure_pct_equity": round(long_notional / safe_equity, 6),
        "short_exposure_pct_equity": round(short_notional / safe_equity, 6),
        "directional_bias_pct": round(directional_bias, 6),
        "decision_symbol_concentration_pct": round(
            decision_symbol_notional / safe_total if total_notional > 0 else 0.0,
            6,
        ),
        "same_tier_concentration_pct": round(
            same_tier_notional / safe_total if total_notional > 0 else 0.0,
            6,
        ),
        "largest_position_pct_equity": round(largest_symbol_notional / safe_equity, 6),
        "open_position_count": float(len(positions)),
    }


def evaluate_risk(
    session: Session,
    settings_row: Setting,
    decision: TradeDecision,
    market_snapshot: MarketSnapshotPayload,
    decision_run_id: int | None = None,
    market_snapshot_id: int | None = None,
) -> tuple[RiskCheckResult, RiskCheck]:
    reason_codes: list[str] = []
    defaults = get_settings()
    live_requested = settings_row.live_trading_enabled
    operating_mode: Literal["live", "paused", "hold"] = "live"
    latest_pnl = get_latest_pnl_snapshot(session, settings_row)
    credentials = get_runtime_credentials(settings_row)
    symbol_risk_tier = get_symbol_risk_tier(decision.symbol)
    effective_leverage_cap = _effective_leverage_cap(settings_row, decision.symbol)
    effective_risk_cap = min(settings_row.max_risk_per_trade, HARD_MAX_RISK_PER_TRADE)
    effective_daily_loss_cap = min(settings_row.max_daily_loss, HARD_MAX_DAILY_LOSS)
    exposure_metrics = _build_exposure_metrics(session, decision.symbol, latest_pnl.equity)

    if settings_row.trading_paused:
        reason_codes.append("TRADING_PAUSED")
        operating_mode = "paused"
    if market_snapshot.is_stale:
        reason_codes.append("STALE_MARKET_DATA")
    if not market_snapshot.is_complete:
        reason_codes.append("INCOMPLETE_MARKET_DATA")
    if latest_pnl.daily_pnl < 0 and abs(latest_pnl.daily_pnl) / max(latest_pnl.equity, 1.0) >= effective_daily_loss_cap:
        reason_codes.append("DAILY_LOSS_LIMIT_REACHED")
    if latest_pnl.consecutive_losses >= settings_row.max_consecutive_losses and decision.decision in {"long", "short"}:
        reason_codes.append("MAX_CONSECUTIVE_LOSSES_REACHED")
    if decision.leverage > effective_leverage_cap:
        reason_codes.append("LEVERAGE_EXCEEDS_LIMIT")
    if decision.risk_pct > effective_risk_cap:
        reason_codes.append("RISK_PCT_EXCEEDS_LIMIT")
    if decision.decision in {"long", "short"} and (decision.stop_loss is None or decision.take_profit is None):
        reason_codes.append("MISSING_STOP_OR_TARGET")

    entry = _entry_price(decision, market_snapshot)
    if decision.decision == "long" and decision.stop_loss is not None and decision.take_profit is not None:
        if decision.stop_loss >= entry or decision.take_profit <= entry:
            reason_codes.append("INVALID_LONG_BRACKETS")
    if decision.decision == "short" and decision.stop_loss is not None and decision.take_profit is not None:
        if decision.stop_loss <= entry or decision.take_profit >= entry:
            reason_codes.append("INVALID_SHORT_BRACKETS")

    slippage = abs(entry - market_snapshot.latest_price) / max(market_snapshot.latest_price, 1.0)
    if slippage > settings_row.slippage_threshold_pct and decision.decision in {"long", "short", "reduce", "exit"}:
        reason_codes.append("SLIPPAGE_THRESHOLD_EXCEEDED")
    if decision.decision == "hold":
        reason_codes.append("HOLD_DECISION")
        operating_mode = "hold" if operating_mode != "paused" else operating_mode

    if live_requested:
        if not defaults.live_trading_env_enabled:
            reason_codes.append("LIVE_ENV_DISABLED")
        if not settings_row.manual_live_approval:
            reason_codes.append("LIVE_APPROVAL_POLICY_DISABLED")
        if not is_live_execution_armed(settings_row):
            reason_codes.append("LIVE_APPROVAL_REQUIRED")
        if not credentials.binance_api_key or not credentials.binance_api_secret:
            reason_codes.append("LIVE_CREDENTIALS_MISSING")
    elif decision.decision != "hold":
        reason_codes.append("LIVE_TRADING_DISABLED")
    allowed = len(reason_codes) == 0
    result = RiskCheckResult(
        allowed=allowed,
        decision=decision.decision,
        reason_codes=reason_codes,
        approved_risk_pct=decision.risk_pct if allowed else 0.0,
        approved_leverage=min(decision.leverage, effective_leverage_cap) if allowed else 0.0,
        operating_mode=operating_mode if not allowed else "live",
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
