from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from trading_mvp.schemas import PerformanceAggregateEntry, SignalPerformanceReportResponse
from trading_mvp.services.performance_reporting import build_signal_performance_report

ADAPTIVE_WINDOW_WEIGHTS: dict[str, float] = {"24h": 0.6, "7d": 0.4}
ADAPTIVE_MIN_SAMPLE_SIZE = 3
ADAPTIVE_SIGNAL_WEIGHT_MIN = 0.85
ADAPTIVE_SIGNAL_WEIGHT_MAX = 1.1
ADAPTIVE_CONFIDENCE_DISCOUNT_MAX = 0.18
ADAPTIVE_RISK_PCT_MULTIPLIER_MIN = 0.65
ADAPTIVE_RISK_PCT_MULTIPLIER_MAX = 1.0
ADAPTIVE_HOLD_BIAS_MAX = 0.22


def _safe_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _aggregate_lookup(items: list[PerformanceAggregateEntry], key: str) -> PerformanceAggregateEntry | None:
    for item in items:
        if item.key == key:
            return item
    return None


def _bucket_weight(bucket: PerformanceAggregateEntry | None) -> dict[str, Any]:
    if bucket is None:
        return {"weight": 1.0, "status": "missing", "sample_size": 0}

    sample_size = max(bucket.decisions, bucket.fills)
    if sample_size < ADAPTIVE_MIN_SAMPLE_SIZE:
        return {
            "weight": 1.0,
            "status": "insufficient_data",
            "sample_size": sample_size,
            "net_realized_pnl_total": bucket.net_realized_pnl_total,
            "wins": bucket.wins,
            "losses": bucket.losses,
        }

    pnl_per_decision = bucket.net_realized_pnl_total / max(bucket.decisions, 1)
    win_rate = bucket.wins / max(bucket.wins + bucket.losses, 1)
    edge = max(-0.15, min(0.1, (pnl_per_decision / 25.0) * 0.08 + ((win_rate - 0.5) * 2.0) * 0.07))
    return {
        "weight": round(max(ADAPTIVE_SIGNAL_WEIGHT_MIN, min(ADAPTIVE_SIGNAL_WEIGHT_MAX, 1.0 + edge)), 4),
        "status": "active",
        "sample_size": sample_size,
        "net_realized_pnl_total": round(bucket.net_realized_pnl_total, 4),
        "wins": bucket.wins,
        "losses": bucket.losses,
    }


def _window_payload(report: SignalPerformanceReportResponse, label: str, *, symbol: str, timeframe: str, regime: str) -> dict[str, Any]:
    window = next((item for item in report.windows if item.window_label == label), None)
    if window is None:
        return {
            "window_label": label,
            "symbol": {"weight": 1.0, "status": "missing", "sample_size": 0},
            "regime": {"weight": 1.0, "status": "missing", "sample_size": 0},
            "symbol_timeframe": {"weight": 1.0, "status": "missing", "sample_size": 0},
            "rationale_codes": {},
        }

    exact_matches = [item for item in window.decisions if item.symbol == symbol and item.timeframe == timeframe]
    exact_bucket = None
    if exact_matches:
        wins = sum(item.wins for item in exact_matches)
        losses = sum(item.losses for item in exact_matches)
        fills = sum(item.fills for item in exact_matches)
        decisions = len(exact_matches)
        exact_bucket = PerformanceAggregateEntry(
            key=f"{symbol}:{timeframe}",
            decisions=decisions,
            approvals=sum(1 for item in exact_matches if item.approved),
            orders=sum(item.orders for item in exact_matches),
            fills=fills,
            holds=sum(1 for item in exact_matches if item.decision == "hold"),
            longs=sum(1 for item in exact_matches if item.decision == "long"),
            shorts=sum(1 for item in exact_matches if item.decision == "short"),
            reduces=sum(1 for item in exact_matches if item.decision == "reduce"),
            exits=sum(1 for item in exact_matches if item.decision == "exit"),
            wins=wins,
            losses=losses,
            realized_pnl_total=sum(item.realized_pnl_total for item in exact_matches),
            fee_total=sum(item.fee_total for item in exact_matches),
            net_realized_pnl_total=sum(item.net_realized_pnl_total for item in exact_matches),
            average_slippage_pct=(sum(item.average_slippage_pct for item in exact_matches) / len(exact_matches)),
            average_holding_minutes=(sum(item.holding_minutes_observed for item in exact_matches) / len(exact_matches)),
            holding_over_plan_count=sum(
                1 for item in exact_matches if str(item.holding_result_status) == "over_plan"
            ),
            open_positions=sum(1 for item in exact_matches if item.holding_result_status == "open"),
            closed_positions=sum(1 for item in exact_matches if item.holding_result_status != "open"),
            stop_loss_closes=sum(1 for item in exact_matches if item.close_outcome == "stop_loss"),
            take_profit_closes=sum(1 for item in exact_matches if item.close_outcome == "take_profit"),
            manual_closes=sum(1 for item in exact_matches if item.close_outcome == "manual"),
            unclassified_closes=sum(1 for item in exact_matches if item.close_outcome == "unclassified"),
            latest_seen_at=max(item.created_at for item in exact_matches),
        )

    rationale_weights: dict[str, dict[str, Any]] = {}
    for item in window.rationale_codes:
        rationale_weights[item.key] = _bucket_weight(item)

    return {
        "window_label": label,
        "symbol": _bucket_weight(_aggregate_lookup(window.symbols, symbol)),
        "regime": _bucket_weight(_aggregate_lookup(window.regimes, regime)),
        "symbol_timeframe": _bucket_weight(exact_bucket),
        "rationale_codes": rationale_weights,
        "sample_size": window.summary.decisions,
    }


def build_adaptive_signal_context(
    session: Session | None,
    *,
    enabled: bool,
    symbol: str,
    timeframe: str,
    regime: str,
) -> dict[str, Any]:
    bounds = {
        "signal_weight_min": ADAPTIVE_SIGNAL_WEIGHT_MIN,
        "signal_weight_max": ADAPTIVE_SIGNAL_WEIGHT_MAX,
        "confidence_discount_max": ADAPTIVE_CONFIDENCE_DISCOUNT_MAX,
        "risk_pct_multiplier_min": ADAPTIVE_RISK_PCT_MULTIPLIER_MIN,
        "risk_pct_multiplier_max": ADAPTIVE_RISK_PCT_MULTIPLIER_MAX,
        "hold_bias_max": ADAPTIVE_HOLD_BIAS_MAX,
    }
    if not enabled:
        return {
            "enabled": False,
            "status": "disabled",
            "min_sample_size": ADAPTIVE_MIN_SAMPLE_SIZE,
            "window_weights": ADAPTIVE_WINDOW_WEIGHTS,
            "bounds": bounds,
            "windows": {},
            "symbol": symbol,
            "timeframe": timeframe,
            "regime": regime,
        }
    if session is None:
        return {
            "enabled": True,
            "status": "no_session",
            "min_sample_size": ADAPTIVE_MIN_SAMPLE_SIZE,
            "window_weights": ADAPTIVE_WINDOW_WEIGHTS,
            "bounds": bounds,
            "windows": {},
            "symbol": symbol,
            "timeframe": timeframe,
            "regime": regime,
        }

    report = build_signal_performance_report(session, limit=64)
    windows = {
        label: _window_payload(report, label, symbol=symbol, timeframe=timeframe, regime=regime)
        for label in ADAPTIVE_WINDOW_WEIGHTS
    }
    return {
        "enabled": True,
        "status": "ready",
        "min_sample_size": ADAPTIVE_MIN_SAMPLE_SIZE,
        "window_weights": ADAPTIVE_WINDOW_WEIGHTS,
        "bounds": bounds,
        "windows": windows,
        "symbol": symbol,
        "timeframe": timeframe,
        "regime": regime,
    }


def compute_adaptive_adjustment(
    context: dict[str, Any] | None,
    *,
    decision: str,
    rationale_codes: list[str],
) -> dict[str, Any]:
    default_response = {
        "enabled": False,
        "status": "disabled",
        "signal_weight": 1.0,
        "confidence_multiplier": 1.0,
        "risk_pct_multiplier": 1.0,
        "hold_bias": 0.0,
        "active_inputs": [],
        "weak_inputs": [],
        "fallback_reason": "ADAPTIVE_DISABLED",
    }
    if not isinstance(context, dict) or not bool(context.get("enabled")):
        return default_response

    windows = context.get("windows", {})
    if not isinstance(windows, dict) or not windows:
        return {
            **default_response,
            "enabled": True,
            "status": "insufficient_data",
            "weak_inputs": [],
            "fallback_reason": "NO_PERFORMANCE_WINDOWS",
        }

    contributions: list[float] = []
    active_window_total = 0.0
    active_inputs: list[str] = []
    weak_inputs: list[str] = []
    for label, window_weight in ADAPTIVE_WINDOW_WEIGHTS.items():
        window = windows.get(label, {})
        if not isinstance(window, dict):
            continue
        dimension_weights: list[float] = []
        symbol_timeframe = window.get("symbol_timeframe", {})
        symbol_bucket = window.get("symbol", {})
        regime_bucket = window.get("regime", {})
        rationale_map = window.get("rationale_codes", {})
        if isinstance(symbol_timeframe, dict) and (
            str(symbol_timeframe.get("status", "")) == "active"
            or _safe_float(symbol_timeframe.get("weight"), 1.0) != 1.0
        ):
            dimension_weights.append(_safe_float(symbol_timeframe.get("weight"), 1.0))
            active_inputs.append(f"{label}:symbol_timeframe")
        elif isinstance(symbol_bucket, dict) and (
            str(symbol_bucket.get("status", "")) == "active"
            or _safe_float(symbol_bucket.get("weight"), 1.0) != 1.0
        ):
            dimension_weights.append(_safe_float(symbol_bucket.get("weight"), 1.0))
            active_inputs.append(f"{label}:symbol")
        if isinstance(regime_bucket, dict) and (
            str(regime_bucket.get("status", "")) == "active"
            or _safe_float(regime_bucket.get("weight"), 1.0) != 1.0
        ):
            dimension_weights.append(_safe_float(regime_bucket.get("weight"), 1.0))
            active_inputs.append(f"{label}:regime")
        if isinstance(rationale_map, dict):
            rationale_weights = [
                _safe_float(item.get("weight"), 1.0)
                for code in rationale_codes
                for item in [rationale_map.get(code)]
                if isinstance(item, dict)
                and (
                    str(item.get("status", "")) == "active"
                    or _safe_float(item.get("weight"), 1.0) != 1.0
                )
            ]
            if rationale_weights:
                dimension_weights.append(sum(rationale_weights) / len(rationale_weights))
                active_inputs.append(f"{label}:rationale")
        if not dimension_weights:
            continue
        window_signal = sum(dimension_weights) / len(dimension_weights)
        contributions.append(window_signal * window_weight)
        active_window_total += window_weight
        if window_signal < 1.0:
            weak_inputs.append(label)

    if not contributions:
        return {
            **default_response,
            "enabled": True,
            "status": "insufficient_data",
            "weak_inputs": [],
            "fallback_reason": "INSUFFICIENT_BUCKET_DATA",
        }

    signal_weight = sum(contributions) / max(active_window_total, 1.0)
    signal_weight = max(ADAPTIVE_SIGNAL_WEIGHT_MIN, min(ADAPTIVE_SIGNAL_WEIGHT_MAX, round(signal_weight, 4)))
    weakness = max(0.0, 1.0 - signal_weight)
    confidence_multiplier = max(1.0 - ADAPTIVE_CONFIDENCE_DISCOUNT_MAX, round(1.0 - weakness * 1.2, 4))
    risk_pct_multiplier = max(ADAPTIVE_RISK_PCT_MULTIPLIER_MIN, round(1.0 - weakness * 2.1, 4))
    hold_bias = min(ADAPTIVE_HOLD_BIAS_MAX, round(weakness * 1.5, 4))
    return {
        "enabled": True,
        "status": "active",
        "signal_weight": signal_weight,
        "confidence_multiplier": confidence_multiplier,
        "risk_pct_multiplier": min(risk_pct_multiplier, ADAPTIVE_RISK_PCT_MULTIPLIER_MAX),
        "hold_bias": hold_bias,
        "active_inputs": active_inputs,
        "weak_inputs": weak_inputs,
        "fallback_reason": None,
    }


def summarize_adaptive_signal_state(
    context: dict[str, Any] | None,
    *,
    latest_rationale_codes: list[str] | None = None,
    latest_decision: str | None = None,
) -> dict[str, Any]:
    rationale_codes = latest_rationale_codes or []
    adjustment = compute_adaptive_adjustment(
        context,
        decision=latest_decision or "hold",
        rationale_codes=rationale_codes,
    )
    bounds = context.get("bounds", {}) if isinstance(context, dict) else {}
    window_weights = context.get("window_weights", {}) if isinstance(context, dict) else {}
    return {
        "enabled": bool(context and context.get("enabled")),
        "status": adjustment["status"],
        "symbol": context.get("symbol") if isinstance(context, dict) else None,
        "timeframe": context.get("timeframe") if isinstance(context, dict) else None,
        "regime": context.get("regime") if isinstance(context, dict) else None,
        "signal_weight": adjustment["signal_weight"],
        "confidence_multiplier": adjustment["confidence_multiplier"],
        "risk_pct_multiplier": adjustment["risk_pct_multiplier"],
        "hold_bias": adjustment["hold_bias"],
        "active_inputs": adjustment["active_inputs"],
        "weak_inputs": adjustment["weak_inputs"],
        "fallback_reason": adjustment["fallback_reason"],
        "bounds": bounds,
        "window_weights": window_weights,
        "data_fallback_rule": (
            "If recent bucket samples are below the minimum threshold, all adaptive multipliers stay at the neutral default."
        ),
    }
