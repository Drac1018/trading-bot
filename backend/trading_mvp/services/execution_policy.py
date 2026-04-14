from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from trading_mvp.models import Setting
from trading_mvp.schemas import ExecutionIntent, MarketSnapshotPayload

BTC_SYMBOLS = {"BTCUSDT"}
MAJOR_ALT_SYMBOLS = {"ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT"}


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
    policy_profile: str
    symbol_risk_tier: Literal["btc", "major_alt", "alt"]
    timeframe_bucket: Literal["fast", "medium", "slow"]
    volatility_regime: Literal["calm", "elevated", "stressed"]
    urgency: Literal["low", "medium", "high"]
    fallback_after_partial_fill_ratio: float
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
            "policy_profile": self.policy_profile,
            "symbol_risk_tier": self.symbol_risk_tier,
            "timeframe_bucket": self.timeframe_bucket,
            "volatility_regime": self.volatility_regime,
            "urgency": self.urgency,
            "fallback_after_partial_fill_ratio": self.fallback_after_partial_fill_ratio,
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


def _timeframe_bucket(timeframe: str) -> Literal["fast", "medium", "slow"]:
    normalized = timeframe.lower()
    if normalized in {"1m", "3m", "5m", "15m"}:
        return "fast"
    if normalized in {"30m", "1h"}:
        return "medium"
    return "slow"


def _symbol_risk_tier(symbol: str) -> Literal["btc", "major_alt", "alt"]:
    normalized = symbol.upper()
    if normalized in BTC_SYMBOLS:
        return "btc"
    if normalized in MAJOR_ALT_SYMBOLS:
        return "major_alt"
    return "alt"


def _volatility_regime(volatility_pct: float, slippage_threshold_pct: float) -> Literal["calm", "elevated", "stressed"]:
    if volatility_pct <= slippage_threshold_pct * 3.5:
        return "calm"
    if volatility_pct <= slippage_threshold_pct * 6.0:
        return "elevated"
    return "stressed"


def _urgency(intent: ExecutionIntent, timeframe_bucket: Literal["fast", "medium", "slow"], symbol_risk_tier: Literal["btc", "major_alt", "alt"]) -> Literal["low", "medium", "high"]:
    if intent.intent_type == "reduce_only" and intent.action == "exit":
        return "high"
    if timeframe_bucket == "fast" or symbol_risk_tier == "alt":
        return "high"
    if timeframe_bucket == "medium" or symbol_risk_tier == "major_alt":
        return "medium"
    return "low"


def _policy_profile(
    *,
    intent: ExecutionIntent,
    symbol_risk_tier: Literal["btc", "major_alt", "alt"],
    timeframe_bucket: Literal["fast", "medium", "slow"],
    volatility_regime: Literal["calm", "elevated", "stressed"],
) -> str:
    return f"{intent.intent_type}_{symbol_risk_tier}_{timeframe_bucket}_{volatility_regime}"


def _passive_policy_params(
    *,
    intent: ExecutionIntent,
    symbol_risk_tier: Literal["btc", "major_alt", "alt"],
    timeframe_bucket: Literal["fast", "medium", "slow"],
    urgency: Literal["low", "medium", "high"],
) -> tuple[float, float, int, int, float, float]:
    slippage_multiplier = 1.0
    volatility_multiplier = 4.0
    timeout_seconds = 6 if intent.intent_type == "entry" else 5
    max_requotes = 2
    reprice_bps = 4.0 if intent.intent_type == "entry" else 5.0
    partial_fill_ratio = 0.45

    if symbol_risk_tier == "btc":
        slippage_multiplier += 0.2
        volatility_multiplier += 0.75
        reprice_bps -= 1.0
        partial_fill_ratio = 0.35
    elif symbol_risk_tier == "alt":
        slippage_multiplier -= 0.15
        volatility_multiplier -= 0.75
        reprice_bps += 1.5
        partial_fill_ratio = 0.55

    if timeframe_bucket == "slow":
        timeout_seconds += 1
        max_requotes += 1
        reprice_bps = max(reprice_bps - 0.5, 2.0)
        partial_fill_ratio = max(partial_fill_ratio - 0.1, 0.2)
    elif timeframe_bucket == "fast":
        timeout_seconds = max(timeout_seconds - 2, 2)
        max_requotes = max(max_requotes - 1, 1)
        reprice_bps += 1.0
        partial_fill_ratio = min(partial_fill_ratio + 0.1, 0.75)

    if urgency == "high":
        timeout_seconds = max(timeout_seconds - 1, 2)
        max_requotes = max(max_requotes - 1, 1)
        partial_fill_ratio = min(partial_fill_ratio + 0.1, 0.8)
    elif urgency == "low":
        timeout_seconds += 1
        max_requotes += 1
        partial_fill_ratio = max(partial_fill_ratio - 0.05, 0.2)

    return (
        slippage_multiplier,
        volatility_multiplier,
        timeout_seconds,
        max_requotes,
        reprice_bps,
        partial_fill_ratio,
    )


def _build_plan(
    *,
    intent: ExecutionIntent,
    order_type: Literal["MARKET", "LIMIT"],
    price: float | None,
    time_in_force: str | None,
    policy_name: str,
    marketable: bool,
    estimated_slippage_pct: float,
    volatility_pct: float,
    timeout_seconds: int,
    poll_interval_seconds: int,
    max_requotes: int,
    reprice_bps: float,
    fallback_order_type: Literal["MARKET", "LIMIT", "NONE"],
    allow_partial_fill: bool,
    policy_profile: str,
    symbol_risk_tier: Literal["btc", "major_alt", "alt"],
    timeframe_bucket: Literal["fast", "medium", "slow"],
    volatility_regime: Literal["calm", "elevated", "stressed"],
    urgency: Literal["low", "medium", "high"],
    fallback_after_partial_fill_ratio: float,
    reason: str,
) -> ExecutionPlan:
    return ExecutionPlan(
        intent_type=intent.intent_type,
        action=intent.action,
        order_type=order_type,
        price=price,
        time_in_force=time_in_force,
        policy_name=policy_name,
        marketable=marketable,
        estimated_slippage_pct=estimated_slippage_pct,
        volatility_pct=volatility_pct,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        max_requotes=max_requotes,
        reprice_bps=reprice_bps,
        fallback_order_type=fallback_order_type,
        allow_partial_fill=allow_partial_fill,
        policy_profile=policy_profile,
        symbol_risk_tier=symbol_risk_tier,
        timeframe_bucket=timeframe_bucket,
        volatility_regime=volatility_regime,
        urgency=urgency,
        fallback_after_partial_fill_ratio=fallback_after_partial_fill_ratio,
        reason=reason,
    )


def should_fallback_aggressively(
    plan: ExecutionPlan,
    *,
    reprice_attempt: int,
    current_slippage_pct: float,
    slippage_threshold_pct: float,
    current_volatility_pct: float,
    remaining_ratio: float | None = None,
) -> bool:
    if plan.order_type != "LIMIT" or plan.fallback_order_type != "MARKET":
        return False
    if reprice_attempt >= plan.max_requotes:
        return True
    if remaining_ratio is not None and remaining_ratio <= plan.fallback_after_partial_fill_ratio:
        return True
    if current_slippage_pct >= max(slippage_threshold_pct * 1.5, plan.estimated_slippage_pct * 1.5):
        return True
    volatility_multiplier = 1.15 if plan.urgency == "high" else 1.25
    return current_volatility_pct >= max(slippage_threshold_pct * 6.0, plan.volatility_pct * volatility_multiplier)


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
    symbol_risk_tier = _symbol_risk_tier(intent.symbol)
    timeframe_bucket = _timeframe_bucket(market_snapshot.timeframe)
    volatility_regime = _volatility_regime(volatility_pct, slippage_threshold)
    urgency = _urgency(intent, timeframe_bucket, symbol_risk_tier)
    profile = _policy_profile(
        intent=intent,
        symbol_risk_tier=symbol_risk_tier,
        timeframe_bucket=timeframe_bucket,
        volatility_regime=volatility_regime,
    )

    if intent.intent_type == "entry":
        if stale_or_incomplete:
            return _build_plan(
                intent=intent,
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
                policy_profile=profile,
                symbol_risk_tier=symbol_risk_tier,
                timeframe_bucket=timeframe_bucket,
                volatility_regime=volatility_regime,
                urgency=urgency,
                fallback_after_partial_fill_ratio=0.0,
                reason="market_data_not_reliable",
            )
        (
            slippage_multiplier,
            volatility_multiplier,
            timeout_seconds,
            max_requotes,
            reprice_bps,
            partial_fill_ratio,
        ) = _passive_policy_params(
            intent=intent,
            symbol_risk_tier=symbol_risk_tier,
            timeframe_bucket=timeframe_bucket,
            urgency=urgency,
        )
        if estimated_slippage_pct <= slippage_threshold * slippage_multiplier and volatility_pct <= slippage_threshold * volatility_multiplier:
            return _build_plan(
                intent=intent,
                order_type="LIMIT",
                price=intent.requested_price,
                time_in_force="GTC",
                policy_name="entry_passive_limit",
                marketable=False,
                estimated_slippage_pct=estimated_slippage_pct,
                volatility_pct=volatility_pct,
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=2,
                max_requotes=max_requotes,
                reprice_bps=reprice_bps,
                fallback_order_type="MARKET",
                allow_partial_fill=True,
                policy_profile=profile,
                symbol_risk_tier=symbol_risk_tier,
                timeframe_bucket=timeframe_bucket,
                volatility_regime=volatility_regime,
                urgency=urgency,
                fallback_after_partial_fill_ratio=partial_fill_ratio,
                reason="passive_entry_allowed",
            )
        return _build_plan(
            intent=intent,
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
            policy_profile=profile,
            symbol_risk_tier=symbol_risk_tier,
            timeframe_bucket=timeframe_bucket,
            volatility_regime=volatility_regime,
            urgency=urgency,
            fallback_after_partial_fill_ratio=0.0,
            reason="slippage_or_volatility_above_threshold",
        )

    if intent.intent_type == "scale_in":
        if stale_or_incomplete:
            return _build_plan(
                intent=intent,
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
                policy_profile=profile,
                symbol_risk_tier=symbol_risk_tier,
                timeframe_bucket=timeframe_bucket,
                volatility_regime=volatility_regime,
                urgency=urgency,
                fallback_after_partial_fill_ratio=0.0,
                reason="market_data_not_reliable",
            )
        (
            slippage_multiplier,
            volatility_multiplier,
            timeout_seconds,
            max_requotes,
            reprice_bps,
            partial_fill_ratio,
        ) = _passive_policy_params(
            intent=intent,
            symbol_risk_tier=symbol_risk_tier,
            timeframe_bucket=timeframe_bucket,
            urgency=urgency,
        )
        if estimated_slippage_pct <= slippage_threshold * max(slippage_multiplier, 0.85) and volatility_pct <= slippage_threshold * max(volatility_multiplier, 4.0):
            return _build_plan(
                intent=intent,
                order_type="LIMIT",
                price=intent.requested_price,
                time_in_force="GTC",
                policy_name="scale_in_passive_limit",
                marketable=False,
                estimated_slippage_pct=estimated_slippage_pct,
                volatility_pct=volatility_pct,
                timeout_seconds=max(timeout_seconds - 1, 2),
                poll_interval_seconds=2,
                max_requotes=max(max_requotes, 1),
                reprice_bps=max(reprice_bps, 4.0),
                fallback_order_type="MARKET",
                allow_partial_fill=True,
                policy_profile=profile,
                symbol_risk_tier=symbol_risk_tier,
                timeframe_bucket=timeframe_bucket,
                volatility_regime=volatility_regime,
                urgency=urgency,
                fallback_after_partial_fill_ratio=min(partial_fill_ratio + 0.05, 0.8),
                reason="passive_scale_in_allowed",
            )
        return _build_plan(
            intent=intent,
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
            policy_profile=profile,
            symbol_risk_tier=symbol_risk_tier,
            timeframe_bucket=timeframe_bucket,
            volatility_regime=volatility_regime,
            urgency=urgency,
            fallback_after_partial_fill_ratio=0.0,
            reason="scale_in_needs_immediate_execution",
        )

    if intent.intent_type == "reduce_only":
        if intent.action == "exit":
            return _build_plan(
                intent=intent,
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
                policy_profile=profile,
                symbol_risk_tier=symbol_risk_tier,
                timeframe_bucket=timeframe_bucket,
                volatility_regime=volatility_regime,
                urgency="high",
                fallback_after_partial_fill_ratio=0.0,
                reason="full_exit_prioritizes_certainty",
            )
        if not stale_or_incomplete and protected_position and estimated_slippage_pct <= slippage_threshold * 1.5:
            reduce_urgency: Literal["medium", "high"] = "high" if urgency == "high" else "medium"
            return _build_plan(
                intent=intent,
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
                policy_profile=profile,
                symbol_risk_tier=symbol_risk_tier,
                timeframe_bucket=timeframe_bucket,
                volatility_regime=volatility_regime,
                urgency=reduce_urgency,
                fallback_after_partial_fill_ratio=0.8,
                reason="protected_reduce_can_rest_passively",
            )
        return _build_plan(
            intent=intent,
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
            policy_profile=profile,
            symbol_risk_tier=symbol_risk_tier,
            timeframe_bucket=timeframe_bucket,
            volatility_regime=volatility_regime,
            urgency=urgency,
            fallback_after_partial_fill_ratio=0.0,
            reason="reduce_needs_immediate_execution",
        )

    return _build_plan(
        intent=intent,
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
        policy_profile=profile,
        symbol_risk_tier=symbol_risk_tier,
        timeframe_bucket=timeframe_bucket,
        volatility_regime=volatility_regime,
        urgency=urgency,
        fallback_after_partial_fill_ratio=0.0,
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
            "summary": "Entry uses passive LIMIT when market data is reliable and slippage/volatility stay within the tier and timeframe profile. MARKET is used when data is stale or urgency dominates.",
        },
        "scale_in": {
            "preferred_order_type": "LIMIT",
            "fallback_order_type": "MARKET",
            "timeout_seconds": 5,
            "max_requotes": 2,
            "summary": "Scale-in uses LIMIT under controlled volatility, but fast timeframes and alt symbols shorten patience and escalate faster.",
        },
        "reduce": {
            "preferred_order_type": "LIMIT",
            "fallback_order_type": "MARKET",
            "timeout_seconds": 4,
            "max_requotes": 1,
            "summary": "Protected reductions can rest briefly as LIMIT, then complete with MARKET once residual size or urgency makes waiting more expensive.",
        },
        "exit": {
            "preferred_order_type": "MARKET",
            "fallback_order_type": "MARKET",
            "summary": "Full exit always prioritizes certainty over maker preference.",
        },
        "protection": {
            "preferred_order_type": "ALGO",
            "fallback_order_type": "EMERGENCY_EXIT",
            "summary": "Exchange-resident stop/take-profit orders are required; failed protection recreates or exits.",
        },
        "profiles": {
            "btc_slow_calm": "Allows the most passive repricing tolerance.",
            "major_alt_medium": "Balanced between maker preference and fill certainty.",
            "alt_fast_or_stressed": "Uses shorter passive windows and faster aggressive fallback.",
        },
    }
