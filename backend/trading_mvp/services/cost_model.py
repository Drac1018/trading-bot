from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_mvp.config import get_settings
from trading_mvp.models import Execution, Order

ENTRY_EXECUTION_TYPE_MARKETABLE = "entry_marketable"
ENTRY_EXECUTION_TYPE_PASSIVE_LIMIT = "entry_passive_limit"
ENTRY_EXECUTION_TYPE_UNKNOWN = "entry_unknown"

DEFAULT_MAKER_FEE_BPS = 5.0
DEFAULT_TAKER_FEE_BPS = 5.0
DEFAULT_MARKETABLE_SLIPPAGE_BPS = 3.0
DEFAULT_PASSIVE_SLIPPAGE_BPS = 1.0
DEFAULT_UNKNOWN_SLIPPAGE_BPS = 2.0
DEFAULT_RECENT_SAMPLE_LIMIT = 20
DEFAULT_MIN_REQUIRED_NET_BPS = 15.0


@dataclass(frozen=True)
class CostModelConfig:
    maker_fee_bps: float = DEFAULT_MAKER_FEE_BPS
    taker_fee_bps: float = DEFAULT_TAKER_FEE_BPS
    marketable_slippage_bps: float = DEFAULT_MARKETABLE_SLIPPAGE_BPS
    passive_slippage_bps: float = DEFAULT_PASSIVE_SLIPPAGE_BPS
    unknown_slippage_bps: float = DEFAULT_UNKNOWN_SLIPPAGE_BPS
    recent_sample_limit: int = DEFAULT_RECENT_SAMPLE_LIMIT
    min_required_net_bps: float = DEFAULT_MIN_REQUIRED_NET_BPS


@dataclass(frozen=True)
class RecentExecutionCostEstimate:
    fee_bps_per_fill: float | None = None
    fee_sample_count: int = 0
    adverse_slippage_bps: float | None = None
    slippage_sample_count: int = 0
    source: str = "unavailable"

    def to_payload(self) -> dict[str, Any]:
        return {
            "fee_bps_per_fill": _round_float(self.fee_bps_per_fill),
            "fee_sample_count": self.fee_sample_count,
            "adverse_slippage_bps": _round_float(self.adverse_slippage_bps),
            "slippage_sample_count": self.slippage_sample_count,
            "source": self.source,
        }


@dataclass(frozen=True)
class CostEstimate:
    maker_fee_bps: float
    taker_fee_bps: float
    entry_fee_bps: float
    exit_fee_bps: float
    round_trip_fee_bps: float
    expected_slippage_bps: float
    spread_cost_bps: float
    expected_gross_bps: float | None
    expected_net_bps: float | None
    fee_to_gross_ratio: float | None
    min_required_net_bps: float
    entry_execution_type: str
    fee_source: str
    slippage_source: str
    recent_estimate: RecentExecutionCostEstimate

    @property
    def expected_cost_bps(self) -> float:
        return self.round_trip_fee_bps + self.expected_slippage_bps + self.spread_cost_bps

    def to_payload(self) -> dict[str, Any]:
        return {
            "maker_fee_bps": _round_float(self.maker_fee_bps),
            "taker_fee_bps": _round_float(self.taker_fee_bps),
            "entry_fee_bps": _round_float(self.entry_fee_bps),
            "exit_fee_bps": _round_float(self.exit_fee_bps),
            "round_trip_fee_bps": _round_float(self.round_trip_fee_bps),
            "expected_slippage_bps": _round_float(self.expected_slippage_bps),
            "spread_cost_bps": _round_float(self.spread_cost_bps),
            "expected_gross_bps": _round_float(self.expected_gross_bps),
            "expected_net_bps": _round_float(self.expected_net_bps),
            "fee_to_gross_ratio": _round_float(self.fee_to_gross_ratio),
            "min_required_net_bps": _round_float(self.min_required_net_bps),
            "expected_cost_bps": _round_float(self.expected_cost_bps),
            "entry_execution_type": self.entry_execution_type,
            "fee_source": self.fee_source,
            "slippage_source": self.slippage_source,
            "recent_estimate": self.recent_estimate.to_payload(),
        }


def _round_float(value: float | None) -> float | None:
    return round(float(value), 6) if value is not None else None


def _optional_float(value: object) -> float | None:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _settings_float(defaults: object, name: str, fallback: float, *, minimum: float | None = None) -> float:
    parsed = _optional_float(getattr(defaults, name, None))
    if parsed is None:
        parsed = fallback
    if minimum is not None:
        parsed = max(parsed, minimum)
    return parsed


def resolve_cost_model_config(defaults: object | None = None) -> CostModelConfig:
    resolved = defaults if defaults is not None else get_settings()
    recent_sample_limit = int(
        max(
            _settings_float(
                resolved,
                "cost_model_recent_sample_limit",
                DEFAULT_RECENT_SAMPLE_LIMIT,
                minimum=1.0,
            ),
            1.0,
        )
    )
    return CostModelConfig(
        maker_fee_bps=_settings_float(resolved, "cost_model_maker_fee_bps", DEFAULT_MAKER_FEE_BPS, minimum=0.0),
        taker_fee_bps=_settings_float(resolved, "cost_model_taker_fee_bps", DEFAULT_TAKER_FEE_BPS, minimum=0.0),
        marketable_slippage_bps=_settings_float(
            resolved,
            "cost_model_marketable_slippage_bps",
            DEFAULT_MARKETABLE_SLIPPAGE_BPS,
            minimum=0.0,
        ),
        passive_slippage_bps=_settings_float(
            resolved,
            "cost_model_passive_slippage_bps",
            DEFAULT_PASSIVE_SLIPPAGE_BPS,
            minimum=0.0,
        ),
        unknown_slippage_bps=_settings_float(
            resolved,
            "cost_model_unknown_slippage_bps",
            DEFAULT_UNKNOWN_SLIPPAGE_BPS,
            minimum=0.0,
        ),
        recent_sample_limit=recent_sample_limit,
        min_required_net_bps=_settings_float(
            resolved,
            "cost_model_min_required_net_bps",
            DEFAULT_MIN_REQUIRED_NET_BPS,
            minimum=0.0,
        ),
    )


def normalize_entry_execution_type(value: object) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in {ENTRY_EXECUTION_TYPE_MARKETABLE, "marketable", "market", "aggressive", "immediate"}:
        return ENTRY_EXECUTION_TYPE_MARKETABLE
    if text in {
        ENTRY_EXECUTION_TYPE_PASSIVE_LIMIT,
        "passive",
        "passive_limit",
        "maker",
        "post_only",
        "post-only",
        "pullback_confirm",
    }:
        return ENTRY_EXECUTION_TYPE_PASSIVE_LIMIT
    if "marketable" in text or "market" in text or "aggressive" in text:
        return ENTRY_EXECUTION_TYPE_MARKETABLE
    if "passive" in text or "maker" in text or "post_only" in text or "post-only" in text:
        return ENTRY_EXECUTION_TYPE_PASSIVE_LIMIT
    return None


def _order_exposure_side(order: Order) -> str | None:
    side = str(order.side or "").strip().lower()
    if side in {"long", "buy"}:
        return "long"
    if side in {"short", "sell"}:
        return "short"
    return None


def _is_protective_order_type(order_type: object) -> bool:
    return str(order_type or "").strip().upper() in {"STOP_MARKET", "TAKE_PROFIT_MARKET", "TAKE_PROFIT"}


def _signed_slippage_bps_from_execution(execution: Execution) -> float | None:
    payload = execution.payload if isinstance(execution.payload, dict) else {}
    for value in (
        payload.get("signed_slippage_bps"),
        _as_dict(payload.get("execution_quality")).get("signed_slippage_bps"),
    ):
        parsed = _optional_float(value)
        if parsed is not None:
            return parsed
    return None


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def build_recent_execution_cost_estimate(
    session: Session,
    *,
    symbol: str,
    side: str,
    sample_limit: int | None = None,
) -> RecentExecutionCostEstimate:
    limit = int(max(sample_limit or resolve_cost_model_config().recent_sample_limit, 1))
    rows = session.execute(
        select(Execution, Order)
        .join(Order, Execution.order_id == Order.id)
        .where(Execution.symbol == symbol)
        .order_by(Execution.created_at.desc())
        .limit(limit * 3)
    ).all()
    fee_samples: list[float] = []
    adverse_slippage_samples: list[float] = []
    target_side = str(side or "").strip().lower()
    for execution, order in rows:
        if order is None or bool(order.reduce_only) or bool(order.close_only):
            continue
        if _is_protective_order_type(order.order_type):
            continue
        if _order_exposure_side(order) != target_side:
            continue
        notional = float(execution.fill_price or 0.0) * float(execution.fill_quantity or 0.0)
        if notional > 0 and float(execution.fee_paid or 0.0) >= 0.0:
            fee_samples.append((float(execution.fee_paid or 0.0) / notional) * 10_000)
        signed_slippage_bps = _signed_slippage_bps_from_execution(execution)
        if signed_slippage_bps is not None:
            adverse_slippage_samples.append(max(float(signed_slippage_bps), 0.0))
        if len(fee_samples) >= limit and len(adverse_slippage_samples) >= limit:
            break
    return RecentExecutionCostEstimate(
        fee_bps_per_fill=(sum(fee_samples) / len(fee_samples)) if fee_samples else None,
        fee_sample_count=len(fee_samples),
        adverse_slippage_bps=(
            sum(adverse_slippage_samples) / len(adverse_slippage_samples)
            if adverse_slippage_samples
            else None
        ),
        slippage_sample_count=len(adverse_slippage_samples),
        source="recent_executions" if fee_samples or adverse_slippage_samples else "unavailable",
    )


def recent_execution_cost_estimate_from_context(context: Mapping[str, Any] | None) -> RecentExecutionCostEstimate:
    source = _as_dict(context)
    nested = _as_dict(
        source.get("recent_execution_cost_estimate")
        or source.get("cost_model_recent_estimate")
        or _as_dict(source.get("expected_cost_gate")).get("recent_estimate")
    )
    if not nested:
        nested = source
    fee_bps_per_fill = _optional_float(
        nested.get("fee_bps_per_fill")
        or nested.get("recent_fee_bps_per_fill")
        or nested.get("average_fee_bps_per_fill")
    )
    adverse_slippage_bps = _optional_float(
        nested.get("adverse_slippage_bps")
        or nested.get("recent_adverse_slippage_bps")
        or nested.get("average_adverse_slippage_bps")
    )
    has_recent_value = fee_bps_per_fill is not None or adverse_slippage_bps is not None
    source_label = str(nested.get("source") or "context") if has_recent_value else "unavailable"
    return RecentExecutionCostEstimate(
        fee_bps_per_fill=fee_bps_per_fill,
        fee_sample_count=int(_optional_float(nested.get("fee_sample_count")) or 0),
        adverse_slippage_bps=adverse_slippage_bps,
        slippage_sample_count=int(
            _optional_float(nested.get("slippage_sample_count") or nested.get("recent_slippage_sample_count")) or 0
        ),
        source=source_label,
    )


def calculate_expected_trade_cost(
    *,
    entry_execution_type: str,
    expected_gross_bps: float | None,
    spread_cost_bps: float = 0.0,
    observed_chase_bps: float = 0.0,
    recent_estimate: RecentExecutionCostEstimate | None = None,
    explicit_round_trip_fee_bps: float | None = None,
    explicit_entry_fee_bps: float | None = None,
    explicit_exit_fee_bps: float | None = None,
    explicit_slippage_bps: float | None = None,
    config: CostModelConfig | None = None,
) -> CostEstimate:
    resolved_config = config or resolve_cost_model_config()
    normalized_entry_type = normalize_entry_execution_type(entry_execution_type) or ENTRY_EXECUTION_TYPE_UNKNOWN
    recent = recent_estimate or RecentExecutionCostEstimate()
    maker_fee_bps = resolved_config.maker_fee_bps
    taker_fee_bps = resolved_config.taker_fee_bps
    fee_source = "config_defaults"
    if recent.fee_bps_per_fill is not None and recent.fee_sample_count > 0:
        maker_fee_bps = max(maker_fee_bps, recent.fee_bps_per_fill)
        taker_fee_bps = max(taker_fee_bps, recent.fee_bps_per_fill)
        fee_source = "recent_executions_conservative_max"
    if explicit_round_trip_fee_bps is not None:
        entry_fee_bps = explicit_entry_fee_bps if explicit_entry_fee_bps is not None else explicit_round_trip_fee_bps / 2
        exit_fee_bps = explicit_exit_fee_bps if explicit_exit_fee_bps is not None else explicit_round_trip_fee_bps - entry_fee_bps
        fee_source = "explicit_round_trip"
    else:
        entry_fee_bps = (
            maker_fee_bps
            if normalized_entry_type == ENTRY_EXECUTION_TYPE_PASSIVE_LIMIT
            else taker_fee_bps
        )
        exit_fee_bps = taker_fee_bps
        if explicit_entry_fee_bps is not None:
            entry_fee_bps = explicit_entry_fee_bps
            fee_source = "explicit_components"
        if explicit_exit_fee_bps is not None:
            exit_fee_bps = explicit_exit_fee_bps
            fee_source = "explicit_components"
    round_trip_fee_bps = max(entry_fee_bps + exit_fee_bps, 0.0)

    if normalized_entry_type == ENTRY_EXECUTION_TYPE_MARKETABLE:
        fallback_slippage_bps = resolved_config.marketable_slippage_bps
    elif normalized_entry_type == ENTRY_EXECUTION_TYPE_PASSIVE_LIMIT:
        fallback_slippage_bps = resolved_config.passive_slippage_bps
    else:
        fallback_slippage_bps = resolved_config.unknown_slippage_bps
    recent_slippage_bps = recent.adverse_slippage_bps if recent.slippage_sample_count > 0 else None
    if explicit_slippage_bps is not None:
        expected_slippage_bps = max(explicit_slippage_bps, recent_slippage_bps or 0.0)
        slippage_source = (
            "explicit_plus_recent_conservative_max"
            if recent_slippage_bps is not None and recent_slippage_bps > explicit_slippage_bps
            else "explicit"
        )
    else:
        expected_slippage_bps = max(fallback_slippage_bps, observed_chase_bps, recent_slippage_bps or 0.0)
        if recent_slippage_bps is not None and expected_slippage_bps == recent_slippage_bps:
            slippage_source = "recent_executions_conservative_max"
        elif observed_chase_bps > fallback_slippage_bps:
            slippage_source = "observed_chase"
        else:
            slippage_source = "config_defaults"

    gross = expected_gross_bps if expected_gross_bps is not None else None
    expected_net_bps = (
        gross - round_trip_fee_bps - expected_slippage_bps - max(spread_cost_bps, 0.0)
        if gross is not None
        else None
    )
    fee_to_gross_ratio = round_trip_fee_bps / gross if gross is not None and gross > 0 else None
    return CostEstimate(
        maker_fee_bps=maker_fee_bps,
        taker_fee_bps=taker_fee_bps,
        entry_fee_bps=entry_fee_bps,
        exit_fee_bps=exit_fee_bps,
        round_trip_fee_bps=round_trip_fee_bps,
        expected_slippage_bps=expected_slippage_bps,
        spread_cost_bps=max(spread_cost_bps, 0.0),
        expected_gross_bps=gross,
        expected_net_bps=expected_net_bps,
        fee_to_gross_ratio=fee_to_gross_ratio,
        min_required_net_bps=resolved_config.min_required_net_bps,
        entry_execution_type=normalized_entry_type,
        fee_source=fee_source,
        slippage_source=slippage_source,
        recent_estimate=recent,
    )
