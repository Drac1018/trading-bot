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
        reason="intent_managed_by_protection_or_emergency_path",
    )
