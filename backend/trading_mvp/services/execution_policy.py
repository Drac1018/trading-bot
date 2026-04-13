from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from trading_mvp.models import Setting
from trading_mvp.schemas import ExecutionIntent, MarketSnapshotPayload


@dataclass(frozen=True)
class ExecutionPlan:
    intent_type: str
    action: str
    order_type: Literal["MARKET", "LIMIT"]
    price: float | None
    time_in_force: str | None
    policy_name: str
    marketable: bool
    estimated_slippage_pct: float
    volatility_pct: float
    timeout_seconds: int
    poll_interval_seconds: int
    max_requotes: int
    reprice_bps: float
    fallback_order_type: Literal["MARKET", "LIMIT", "NONE"]
    allow_partial_fill: bool
    reason: str

    def to_payload(self) -> dict[str, object]:
        return {
            "intent_type": self.intent_type,
            "action": self.action,
            "order_type": self.order_type,
            "price": self.price,
            "time_in_force": self.time_in_force,
            "policy_name": self.policy_name,
            "marketable": self.marketable,
            "estimated_slippage_pct": self.estimated_slippage_pct,
            "volatility_pct": self.volatility_pct,
            "timeout_seconds": self.timeout_seconds,
            "poll_interval_seconds": self.poll_interval_seconds,
            "max_requotes": self.max_requotes,
            "reprice_bps": self.reprice_bps,
            "fallback_order_type": self.fallback_order_type,
            "allow_partial_fill": self.allow_partial_fill,
            "reason": self.reason,
        }


def _estimate_slippage_pct(intent: ExecutionIntent, market_snapshot: MarketSnapshotPayload) -> float:
    latest_price = max(market_snapshot.latest_price, 1.0)
    reference_price = intent.requested_price if intent.requested_price > 0 else latest_price
    return abs(reference_price - latest_price) / latest_price


def _estimate_window_volatility_pct(market_snapshot: MarketSnapshotPayload, sample_size: int = 5) -> float:
    candles = market_snapshot.candles[-sample_size:] if market_snapshot.candles else []
    if not candles:
        return 0.0
    highest = max(candle.high for candle in candles)
    lowest = min(candle.low for candle in candles)
    latest_price = max(market_snapshot.latest_price, 1.0)
    return max(highest - lowest, 0.0) / latest_price


def should_fallback_aggressively(
    plan: ExecutionPlan,
    *,
    reprice_attempt: int,
    current_slippage_pct: float,
    slippage_threshold_pct: float,
    current_volatility_pct: float,
) -> bool:
    if plan.order_type != "LIMIT" or plan.fallback_order_type != "MARKET":
        return False
    if reprice_attempt >= plan.max_requotes:
        return True
    if current_slippage_pct >= max(slippage_threshold_pct * 1.5, plan.estimated_slippage_pct * 1.5):
        return True
    return current_volatility_pct >= max(slippage_threshold_pct * 6.0, plan.volatility_pct * 1.25)


def select_execution_plan(
    intent: ExecutionIntent,
    market_snapshot: MarketSnapshotPayload,
    settings_row: Setting,
    *,
    pre_trade_protection: Mapping[str, object] | None = None,
) -> ExecutionPlan:
    estimated_slippage_pct = _estimate_slippage_pct(intent, market_snapshot)
    volatility_pct = _estimate_window_volatility_pct(market_snapshot)
    stale_or_incomplete = market_snapshot.is_stale or not market_snapshot.is_complete
    slippage_threshold = max(settings_row.slippage_threshold_pct, 0.0005)
    protected_position = bool((pre_trade_protection or {}).get("protected", False))

    if intent.intent_type == "entry":
        if stale_or_incomplete:
            return ExecutionPlan(
                intent_type=intent.intent_type,
                action=intent.action,
                order_type="MARKET",
                price=None,
                time_in_force=None,
                policy_name="entry_marketable",
                marketable=True,
                estimated_slippage_pct=estimated_slippage_pct,
                volatility_pct=volatility_pct,
                timeout_seconds=0,
                poll_interval_seconds=0,
                max_requotes=0,
                reprice_bps=0.0,
                fallback_order_type="NONE",
                allow_partial_fill=True,
                reason="market_data_not_reliable",
            )
        if estimated_slippage_pct <= slippage_threshold and volatility_pct <= slippage_threshold * 4:
            return ExecutionPlan(
                intent_type=intent.intent_type,
                action=intent.action,
                order_type="LIMIT",
                price=intent.requested_price,
                time_in_force="GTC",
                policy_name="entry_passive_limit",
                marketable=False,
                estimated_slippage_pct=estimated_slippage_pct,
                volatility_pct=volatility_pct,
                timeout_seconds=6,
                poll_interval_seconds=2,
                max_requotes=2,
                reprice_bps=4.0,
                fallback_order_type="MARKET",
                allow_partial_fill=True,
                reason="passive_entry_allowed",
            )
        return ExecutionPlan(
            intent_type=intent.intent_type,
            action=intent.action,
            order_type="MARKET",
            price=None,
            time_in_force=None,
            policy_name="entry_marketable",
            marketable=True,
            estimated_slippage_pct=estimated_slippage_pct,
            volatility_pct=volatility_pct,
            timeout_seconds=0,
            poll_interval_seconds=0,
            max_requotes=0,
            reprice_bps=0.0,
            fallback_order_type="NONE",
            allow_partial_fill=True,
            reason="slippage_or_volatility_above_threshold",
        )

    if intent.intent_type == "scale_in":
        if stale_or_incomplete:
            return ExecutionPlan(
                intent_type=intent.intent_type,
                action=intent.action,
                order_type="MARKET",
                price=None,
                time_in_force=None,
                policy_name="scale_in_marketable",
                marketable=True,
                estimated_slippage_pct=estimated_slippage_pct,
                volatility_pct=volatility_pct,
                timeout_seconds=0,
                poll_interval_seconds=0,
                max_requotes=0,
                reprice_bps=0.0,
                fallback_order_type="NONE",
                allow_partial_fill=True,
                reason="market_data_not_reliable",
            )
        if estimated_slippage_pct <= slippage_threshold * 1.25 and volatility_pct <= slippage_threshold * 5:
            return ExecutionPlan(
                intent_type=intent.intent_type,
                action=intent.action,
                order_type="LIMIT",
                price=intent.requested_price,
                time_in_force="GTC",
                policy_name="scale_in_passive_limit",
                marketable=False,
                estimated_slippage_pct=estimated_slippage_pct,
                volatility_pct=volatility_pct,
                timeout_seconds=5,
                poll_interval_seconds=2,
                max_requotes=2,
                reprice_bps=5.0,
                fallback_order_type="MARKET",
                allow_partial_fill=True,
                reason="passive_scale_in_allowed",
            )
        return ExecutionPlan(
            intent_type=intent.intent_type,
            action=intent.action,
            order_type="MARKET",
            price=None,
            time_in_force=None,
            policy_name="scale_in_marketable",
            marketable=True,
            estimated_slippage_pct=estimated_slippage_pct,
            volatility_pct=volatility_pct,
            timeout_seconds=0,
            poll_interval_seconds=0,
            max_requotes=0,
            reprice_bps=0.0,
            fallback_order_type="NONE",
            allow_partial_fill=True,
            reason="scale_in_needs_immediate_execution",
        )

    if intent.intent_type == "reduce_only":
        if intent.action == "exit":
            return ExecutionPlan(
                intent_type=intent.intent_type,
                action=intent.action,
                order_type="MARKET",
                price=None,
                time_in_force=None,
                policy_name="exit_marketable",
                marketable=True,
                estimated_slippage_pct=estimated_slippage_pct,
                volatility_pct=volatility_pct,
                timeout_seconds=0,
                poll_interval_seconds=0,
                max_requotes=0,
                reprice_bps=0.0,
                fallback_order_type="NONE",
                allow_partial_fill=True,
                reason="full_exit_prioritizes_certainty",
            )
        if not stale_or_incomplete and protected_position and estimated_slippage_pct <= slippage_threshold * 1.5:
            return ExecutionPlan(
                intent_type=intent.intent_type,
                action=intent.action,
                order_type="LIMIT",
                price=market_snapshot.latest_price,
                time_in_force="GTC",
                policy_name="reduce_passive_limit",
                marketable=False,
                estimated_slippage_pct=estimated_slippage_pct,
                volatility_pct=volatility_pct,
                timeout_seconds=4,
                poll_interval_seconds=2,
                max_requotes=1,
                reprice_bps=3.0,
                fallback_order_type="MARKET",
                allow_partial_fill=True,
                reason="protected_reduce_can_rest_passively",
            )
        return ExecutionPlan(
            intent_type=intent.intent_type,
            action=intent.action,
            order_type="MARKET",
            price=None,
            time_in_force=None,
            policy_name="reduce_marketable",
            marketable=True,
            estimated_slippage_pct=estimated_slippage_pct,
            volatility_pct=volatility_pct,
            timeout_seconds=0,
            poll_interval_seconds=0,
            max_requotes=0,
            reprice_bps=0.0,
            fallback_order_type="NONE",
            allow_partial_fill=True,
            reason="reduce_needs_immediate_execution",
        )

    return ExecutionPlan(
        intent_type=intent.intent_type,
        action=intent.action,
        order_type="MARKET",
        price=None,
        time_in_force=None,
        policy_name="managed_externally",
        marketable=True,
        estimated_slippage_pct=estimated_slippage_pct,
        volatility_pct=volatility_pct,
        timeout_seconds=0,
        poll_interval_seconds=0,
        max_requotes=0,
        reprice_bps=0.0,
        fallback_order_type="NONE",
        allow_partial_fill=True,
        reason="intent_managed_by_protection_or_emergency_path",
    )


def summarize_execution_policy(settings_row: Setting) -> dict[str, object]:
    return {
        "slippage_threshold_pct": settings_row.slippage_threshold_pct,
        "entry": {
            "preferred_order_type": "LIMIT",
            "fallback_order_type": "MARKET",
            "timeout_seconds": 6,
            "max_requotes": 2,
            "summary": "Passive entry limit with timeout, cancel/reprice, then aggressive market fallback when urgency dominates.",
        },
        "scale_in": {
            "preferred_order_type": "LIMIT",
            "fallback_order_type": "MARKET",
            "timeout_seconds": 5,
            "max_requotes": 2,
            "summary": "Scale-in prefers passive limit, reprices once or twice, then falls back market if fill risk grows.",
        },
        "reduce": {
            "preferred_order_type": "LIMIT",
            "fallback_order_type": "MARKET",
            "timeout_seconds": 4,
            "max_requotes": 1,
            "summary": "Protected reductions may rest passively briefly, then cancel/reprice or fall back market to finish.",
        },
        "exit": {
            "preferred_order_type": "MARKET",
            "fallback_order_type": "MARKET",
            "summary": "Full exit prioritizes certainty over maker preference.",
        },
        "protection": {
            "preferred_order_type": "ALGO",
            "fallback_order_type": "EMERGENCY_EXIT",
            "summary": "Exchange-resident stop/take-profit orders are required; failed protection recreates or exits.",
        },
    }
