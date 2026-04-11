from __future__ import annotations

from typing import Any, Literal

from sqlalchemy.orm import Session

from trading_mvp.config import get_settings
from trading_mvp.models import RiskCheck, Setting
from trading_mvp.schemas import MarketSnapshotPayload, RiskCheckResult, TradeDecision
from trading_mvp.services.account import get_latest_pnl_snapshot
from trading_mvp.services.settings import get_runtime_credentials, is_live_execution_armed


def validate_decision_schema(payload: dict[str, Any]) -> TradeDecision:
    return TradeDecision.model_validate(payload)


def _entry_price(decision: TradeDecision, market_snapshot: MarketSnapshotPayload) -> float:
    if decision.entry_zone_min is not None and decision.entry_zone_max is not None:
        return (decision.entry_zone_min + decision.entry_zone_max) / 2
    return market_snapshot.latest_price


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

    if settings_row.trading_paused:
        reason_codes.append("TRADING_PAUSED")
        operating_mode = "paused"
    if market_snapshot.is_stale:
        reason_codes.append("STALE_MARKET_DATA")
    if not market_snapshot.is_complete:
        reason_codes.append("INCOMPLETE_MARKET_DATA")
    if latest_pnl.daily_pnl < 0 and abs(latest_pnl.daily_pnl) / max(latest_pnl.equity, 1.0) >= settings_row.max_daily_loss:
        reason_codes.append("DAILY_LOSS_LIMIT_REACHED")
    if latest_pnl.consecutive_losses >= settings_row.max_consecutive_losses and decision.decision in {"long", "short"}:
        reason_codes.append("MAX_CONSECUTIVE_LOSSES_REACHED")
    if decision.leverage > settings_row.max_leverage:
        reason_codes.append("LEVERAGE_EXCEEDS_LIMIT")
    if decision.risk_pct > settings_row.max_risk_per_trade:
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
        approved_leverage=decision.leverage if allowed else 0.0,
        operating_mode=operating_mode if not allowed else "live",
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
