from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from trading_mvp.models import AgentRun, Execution, Order, RiskCheck, Setting
from trading_mvp.schemas import PerformanceAggregateEntry, SignalPerformanceReportResponse
from trading_mvp.services.performance_reporting import _extract_analysis_context, build_signal_performance_report
from trading_mvp.time_utils import utcnow_naive

ADAPTIVE_WINDOW_WEIGHTS: dict[str, float] = {"24h": 0.6, "7d": 0.4}
ADAPTIVE_MIN_SAMPLE_SIZE = 3
ADAPTIVE_SIGNAL_WEIGHT_MIN = 0.85
ADAPTIVE_SIGNAL_WEIGHT_MAX = 1.1
ADAPTIVE_CONFIDENCE_DISCOUNT_MAX = 0.18
ADAPTIVE_RISK_PCT_MULTIPLIER_MIN = 0.65
ADAPTIVE_RISK_PCT_MULTIPLIER_MAX = 1.0
ADAPTIVE_HOLD_BIAS_MAX = 0.22
ADAPTIVE_SETUP_DISABLE_REASON_CODE = "UNDERPERFORMING_SETUP_DISABLED"
ADAPTIVE_SETUP_DISABLE_LOOKBACK = 8
ADAPTIVE_SETUP_DISABLE_MIN_SAMPLE_SIZE = 4
ADAPTIVE_SETUP_DISABLE_EXPECTANCY_THRESHOLD = 0.0
ADAPTIVE_SETUP_DISABLE_LOSS_STREAK_THRESHOLD = 3
ADAPTIVE_SETUP_DISABLE_SIGNED_SLIPPAGE_BPS_THRESHOLD = 12.0
ADAPTIVE_SETUP_DISABLE_COOLDOWN_MINUTES = 180
ADAPTIVE_SETUP_DISABLE_HISTORY_LIMIT = 128
ADAPTIVE_SETUP_DISABLE_REASON_CODES = {
    "expectancy": "SETUP_NEGATIVE_EXPECTANCY",
    "loss_streak": "SETUP_LOSS_STREAK",
    "signed_slippage": "SETUP_ADVERSE_SIGNED_SLIPPAGE",
    "net_pnl": "SETUP_NET_PNL_AFTER_FEES_NEGATIVE",
}
ADAPTIVE_SETUP_DISABLE_EXEMPT_RATIONALE_CODES = {
    "PROTECTION_REQUIRED",
    "PROTECTION_RECOVERY",
    "PROTECTION_RESTORE",
}


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


def _safe_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item not in {None, ""}]


def _setup_scenario(decision: str, entry_mode: str | None, rationale_codes: list[str]) -> str:
    decision_code = str(decision or "").lower()
    if decision_code in {"reduce", "exit", "hold"}:
        return decision_code
    rationale_set = {str(code) for code in rationale_codes if code}
    if rationale_set & ADAPTIVE_SETUP_DISABLE_EXEMPT_RATIONALE_CODES:
        return "protection_restore"
    if str(entry_mode or "").lower() == "pullback_confirm" or any("PULLBACK" in code for code in rationale_set):
        return "pullback_entry"
    return "trend_follow"


def _setup_bucket_key(*, symbol: str, timeframe: str, scenario: str, regime: str, entry_mode: str) -> str:
    return f"{symbol.upper()}|{timeframe}|{scenario}|{regime}|{entry_mode}"


def _setup_disable_override_keys(settings_row: Setting | None) -> set[str]:
    if settings_row is None or not isinstance(settings_row.pause_reason_detail, dict):
        return set()
    overrides = settings_row.pause_reason_detail.get("setup_disable_overrides")
    if not isinstance(overrides, dict):
        return set()
    return {str(item) for item in _safe_str_list(overrides.get("force_enable_bucket_keys"))}


def _signed_slippage_bps(execution_row: Execution) -> float:
    payload = execution_row.payload if isinstance(execution_row.payload, dict) else {}
    if "signed_slippage_bps" in payload:
        return _safe_float(payload.get("signed_slippage_bps"), default=0.0)
    if "signed_slippage_pct" in payload:
        return _safe_float(payload.get("signed_slippage_pct"), default=0.0) * 10000.0
    return 0.0


def _build_setup_disable_buckets(
    session: Session,
    *,
    symbol: str,
    timeframe: str,
    settings_row: Setting | None,
) -> list[dict[str, Any]]:
    now = utcnow_naive()
    symbol_key = symbol.upper()
    decision_rows = list(
        session.scalars(
            select(AgentRun)
            .where(AgentRun.role == "trading_decision")
            .order_by(desc(AgentRun.created_at))
            .limit(ADAPTIVE_SETUP_DISABLE_HISTORY_LIMIT)
        )
    )
    exact_rows = [
        row
        for row in decision_rows
        if isinstance(row.output_payload, dict)
        and str(row.output_payload.get("symbol") or "").upper() == symbol_key
        and str(row.output_payload.get("timeframe") or "") == timeframe
        and str(row.output_payload.get("decision") or "").lower() in {"long", "short"}
    ]
    if not exact_rows:
        return []

    decision_ids = [row.id for row in exact_rows]
    risk_rows = list(
        session.scalars(
            select(RiskCheck)
            .where(RiskCheck.decision_run_id.in_(decision_ids))
            .order_by(desc(RiskCheck.created_at))
        )
    )
    risk_by_decision: dict[int, RiskCheck] = {}
    for row in risk_rows:
        if row.decision_run_id is not None and row.decision_run_id not in risk_by_decision:
            risk_by_decision[row.decision_run_id] = row

    orders = list(session.scalars(select(Order).where(Order.decision_run_id.in_(decision_ids))))
    orders_by_decision: dict[int, list[Order]] = defaultdict(list)
    for row in orders:
        if row.decision_run_id is not None:
            orders_by_decision[row.decision_run_id].append(row)
    order_ids = [row.id for row in orders]
    executions = (
        list(session.scalars(select(Execution).where(Execution.order_id.in_(order_ids)))) if order_ids else []
    )
    executions_by_order: dict[int, list[Execution]] = defaultdict(list)
    for row in executions:
        if row.order_id is not None:
            executions_by_order[row.order_id].append(row)

    bucket_samples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    override_keys = _setup_disable_override_keys(settings_row)
    for decision_row in exact_rows:
        linked_risk = risk_by_decision.get(decision_row.id)
        if linked_risk is not None and not bool(linked_risk.allowed):
            continue
        payload = decision_row.output_payload if isinstance(decision_row.output_payload, dict) else {}
        rationale_codes = _safe_str_list(payload.get("rationale_codes"))
        entry_mode = str(payload.get("entry_mode") or "none").lower()
        scenario = _setup_scenario(str(payload.get("decision") or ""), entry_mode, rationale_codes)
        primary_regime, _trend_alignment, _weak_volume, _volatility_expanded, _momentum_weakening = _extract_analysis_context(
            decision_row
        )
        linked_orders = orders_by_decision.get(decision_row.id, [])
        linked_executions = [
            execution_row
            for order_row in linked_orders
            for execution_row in executions_by_order.get(order_row.id, [])
        ]
        if not linked_executions:
            continue
        net_pnl_after_fees = sum(
            _safe_float(execution_row.realized_pnl) - _safe_float(execution_row.fee_paid)
            for execution_row in linked_executions
        )
        average_signed_slippage_bps = sum(_signed_slippage_bps(item) for item in linked_executions) / max(
            len(linked_executions),
            1,
        )
        bucket_key = _setup_bucket_key(
            symbol=symbol_key,
            timeframe=timeframe,
            scenario=scenario,
            regime=primary_regime,
            entry_mode=entry_mode,
        )
        bucket_samples[bucket_key].append(
            {
                "created_at": decision_row.created_at,
                "decision_run_id": decision_row.id,
                "symbol": symbol_key,
                "timeframe": timeframe,
                "scenario": scenario,
                "regime": primary_regime,
                "entry_mode": entry_mode,
                "net_pnl_after_fees": net_pnl_after_fees,
                "average_signed_slippage_bps": average_signed_slippage_bps,
            }
        )

    buckets: list[dict[str, Any]] = []
    for bucket_key, rows in bucket_samples.items():
        recent_rows = sorted(rows, key=lambda item: item["created_at"], reverse=True)[:ADAPTIVE_SETUP_DISABLE_LOOKBACK]
        sample_size = len(recent_rows)
        wins = [float(item["net_pnl_after_fees"]) for item in recent_rows if float(item["net_pnl_after_fees"]) > 0]
        losses = [abs(float(item["net_pnl_after_fees"])) for item in recent_rows if float(item["net_pnl_after_fees"]) < 0]
        win_rate = len(wins) / max(sample_size, 1)
        loss_rate = len(losses) / max(sample_size, 1)
        avg_win = sum(wins) / max(len(wins), 1) if wins else 0.0
        avg_loss = sum(losses) / max(len(losses), 1) if losses else 0.0
        expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)
        recent_net_pnl = sum(float(item["net_pnl_after_fees"]) for item in recent_rows)
        avg_signed_slippage_bps = sum(float(item["average_signed_slippage_bps"]) for item in recent_rows) / max(
            sample_size,
            1,
        )
        loss_streak = 0
        for item in recent_rows:
            if float(item["net_pnl_after_fees"]) < 0:
                loss_streak += 1
                continue
            break
        disable_reason_codes: list[str] = []
        if expectancy < ADAPTIVE_SETUP_DISABLE_EXPECTANCY_THRESHOLD:
            disable_reason_codes.append(ADAPTIVE_SETUP_DISABLE_REASON_CODES["expectancy"])
        if loss_streak >= ADAPTIVE_SETUP_DISABLE_LOSS_STREAK_THRESHOLD:
            disable_reason_codes.append(ADAPTIVE_SETUP_DISABLE_REASON_CODES["loss_streak"])
        if avg_signed_slippage_bps >= ADAPTIVE_SETUP_DISABLE_SIGNED_SLIPPAGE_BPS_THRESHOLD:
            disable_reason_codes.append(ADAPTIVE_SETUP_DISABLE_REASON_CODES["signed_slippage"])
        if recent_net_pnl < 0:
            disable_reason_codes.append(ADAPTIVE_SETUP_DISABLE_REASON_CODES["net_pnl"])
        underperforming = (
            sample_size >= ADAPTIVE_SETUP_DISABLE_MIN_SAMPLE_SIZE
            and expectancy < ADAPTIVE_SETUP_DISABLE_EXPECTANCY_THRESHOLD
            and recent_net_pnl < 0
            and (
                loss_streak >= ADAPTIVE_SETUP_DISABLE_LOSS_STREAK_THRESHOLD
                or avg_signed_slippage_bps >= ADAPTIVE_SETUP_DISABLE_SIGNED_SLIPPAGE_BPS_THRESHOLD
            )
        )
        latest_seen_at = recent_rows[0]["created_at"]
        disabled_at = latest_seen_at if underperforming else None
        cooldown_expires_at = (
            disabled_at + timedelta(minutes=ADAPTIVE_SETUP_DISABLE_COOLDOWN_MINUTES) if disabled_at is not None else None
        )
        manual_override = bucket_key in override_keys
        disabled = bool(
            underperforming
            and cooldown_expires_at is not None
            and cooldown_expires_at > now
            and not manual_override
        )
        if sample_size < ADAPTIVE_SETUP_DISABLE_MIN_SAMPLE_SIZE:
            status = "insufficient_data"
        elif manual_override and underperforming:
            status = "manual_override"
        elif disabled:
            status = "active_disabled"
        elif underperforming and cooldown_expires_at is not None and cooldown_expires_at <= now:
            status = "cooldown_elapsed"
        else:
            status = "healthy"
        buckets.append(
            {
                "bucket_key": bucket_key,
                "symbol": recent_rows[0]["symbol"],
                "timeframe": recent_rows[0]["timeframe"],
                "scenario": recent_rows[0]["scenario"],
                "regime": recent_rows[0]["regime"],
                "entry_mode": recent_rows[0]["entry_mode"],
                "sample_size": sample_size,
                "lookback": ADAPTIVE_SETUP_DISABLE_LOOKBACK,
                "status": status,
                "disabled": disabled,
                "underperforming": underperforming,
                "disable_reason_codes": disable_reason_codes,
                "disabled_at": disabled_at.isoformat() if disabled_at is not None else None,
                "cooldown_expires_at": cooldown_expires_at.isoformat() if cooldown_expires_at is not None else None,
                "manual_override": manual_override,
                "metrics": {
                    "win_rate": round(win_rate, 4),
                    "avg_win": round(avg_win, 4),
                    "avg_loss": round(avg_loss, 4),
                    "expectancy": round(expectancy, 4),
                    "net_pnl_after_fees": round(recent_net_pnl, 4),
                    "avg_signed_slippage_bps": round(avg_signed_slippage_bps, 4),
                    "loss_streak": loss_streak,
                },
                "recovery_condition": {
                    "mode": "cooldown_or_metrics_recovery_or_manual_override",
                    "cooldown_minutes": ADAPTIVE_SETUP_DISABLE_COOLDOWN_MINUTES,
                    "cooldown_expires_at": cooldown_expires_at.isoformat() if cooldown_expires_at is not None else None,
                    "manual_override_key": "pause_reason_detail.setup_disable_overrides.force_enable_bucket_keys[]",
                    "metrics_recovery_rule": (
                        "expectancy >= 0 and net_pnl_after_fees >= 0, or cooldown elapsed without new underperforming trades"
                    ),
                },
            }
        )
    buckets.sort(
        key=lambda item: (
            bool(item.get("disabled")),
            bool(item.get("underperforming")),
            int(item.get("sample_size", 0)),
            str(item.get("bucket_key") or ""),
        ),
        reverse=True,
    )
    return buckets


def _lookup_setup_disable_bucket(
    context: dict[str, Any] | None,
    *,
    decision: str,
    rationale_codes: list[str],
    entry_mode: str | None,
) -> dict[str, Any]:
    if not isinstance(context, dict):
        return {"matched": False, "active": False}
    bucket_lookup = context.get("setup_disable_lookup")
    if not isinstance(bucket_lookup, dict):
        return {"matched": False, "active": False}
    if str(decision or "").lower() not in {"long", "short"}:
        return {"matched": False, "active": False}
    if set(rationale_codes) & ADAPTIVE_SETUP_DISABLE_EXEMPT_RATIONALE_CODES:
        return {"matched": False, "active": False}
    bucket_key = _setup_bucket_key(
        symbol=str(context.get("symbol") or "").upper(),
        timeframe=str(context.get("timeframe") or ""),
        scenario=_setup_scenario(decision, entry_mode, rationale_codes),
        regime=str(context.get("regime") or "unknown"),
        entry_mode=str(entry_mode or "none").lower(),
    )
    bucket = bucket_lookup.get(bucket_key)
    if not isinstance(bucket, dict):
        return {"matched": False, "active": False, "bucket_key": bucket_key}
    return {"matched": True, "active": bool(bucket.get("disabled", False)), **bucket}


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
    settings_row: Setting | None = None,
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
            "setup_disable_buckets": [],
            "setup_disable_lookup": {},
            "setup_disable_overrides": {"force_enable_bucket_keys": []},
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
            "setup_disable_buckets": [],
            "setup_disable_lookup": {},
            "setup_disable_overrides": {"force_enable_bucket_keys": []},
        }

    report = build_signal_performance_report(session, limit=64)
    windows = {
        label: _window_payload(report, label, symbol=symbol, timeframe=timeframe, regime=regime)
        for label in ADAPTIVE_WINDOW_WEIGHTS
    }
    setup_disable_buckets = _build_setup_disable_buckets(
        session,
        symbol=symbol,
        timeframe=timeframe,
        settings_row=settings_row,
    )
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
        "setup_disable_buckets": setup_disable_buckets,
        "setup_disable_lookup": {
            str(item["bucket_key"]): item
            for item in setup_disable_buckets
            if isinstance(item, dict) and item.get("bucket_key") not in {None, ""}
        },
        "setup_disable_overrides": {
            "force_enable_bucket_keys": sorted(_setup_disable_override_keys(settings_row)),
        },
    }


def compute_adaptive_adjustment(
    context: dict[str, Any] | None,
    *,
    decision: str,
    rationale_codes: list[str],
    entry_mode: str | None = None,
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
        "setup_disable": {"matched": False, "active": False},
    }
    if not isinstance(context, dict) or not bool(context.get("enabled")):
        return default_response

    setup_disable = _lookup_setup_disable_bucket(
        context,
        decision=decision,
        rationale_codes=rationale_codes,
        entry_mode=entry_mode,
    )

    windows = context.get("windows", {})
    if not isinstance(windows, dict) or not windows:
        if setup_disable.get("active"):
            return {
                **default_response,
                "enabled": True,
                "status": "setup_disabled",
                "signal_weight": ADAPTIVE_SIGNAL_WEIGHT_MIN,
                "confidence_multiplier": max(1.0 - ADAPTIVE_CONFIDENCE_DISCOUNT_MAX, 0.82),
                "risk_pct_multiplier": ADAPTIVE_RISK_PCT_MULTIPLIER_MIN,
                "hold_bias": ADAPTIVE_HOLD_BIAS_MAX,
                "active_inputs": [f"setup_disable:{setup_disable.get('bucket_key', 'unknown')}"],
                "weak_inputs": [],
                "fallback_reason": None,
                "setup_disable": setup_disable,
            }
        return {
            **default_response,
            "enabled": True,
            "status": "insufficient_data",
            "weak_inputs": [],
            "fallback_reason": "NO_PERFORMANCE_WINDOWS",
            "setup_disable": setup_disable,
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
        if setup_disable.get("active"):
            return {
                **default_response,
                "enabled": True,
                "status": "setup_disabled",
                "signal_weight": ADAPTIVE_SIGNAL_WEIGHT_MIN,
                "confidence_multiplier": max(1.0 - ADAPTIVE_CONFIDENCE_DISCOUNT_MAX, 0.82),
                "risk_pct_multiplier": ADAPTIVE_RISK_PCT_MULTIPLIER_MIN,
                "hold_bias": ADAPTIVE_HOLD_BIAS_MAX,
                "active_inputs": [f"setup_disable:{setup_disable.get('bucket_key', 'unknown')}"],
                "weak_inputs": [],
                "fallback_reason": None,
                "setup_disable": setup_disable,
            }
        return {
            **default_response,
            "enabled": True,
            "status": "insufficient_data",
            "weak_inputs": [],
            "fallback_reason": "INSUFFICIENT_BUCKET_DATA",
            "setup_disable": setup_disable,
        }

    signal_weight = sum(contributions) / max(active_window_total, 1.0)
    signal_weight = max(ADAPTIVE_SIGNAL_WEIGHT_MIN, min(ADAPTIVE_SIGNAL_WEIGHT_MAX, round(signal_weight, 4)))
    weakness = max(0.0, 1.0 - signal_weight)
    confidence_multiplier = max(1.0 - ADAPTIVE_CONFIDENCE_DISCOUNT_MAX, round(1.0 - weakness * 1.2, 4))
    risk_pct_multiplier = max(ADAPTIVE_RISK_PCT_MULTIPLIER_MIN, round(1.0 - weakness * 2.1, 4))
    hold_bias = min(ADAPTIVE_HOLD_BIAS_MAX, round(weakness * 1.5, 4))
    if setup_disable.get("active"):
        hold_bias = ADAPTIVE_HOLD_BIAS_MAX
        confidence_multiplier = max(1.0 - ADAPTIVE_CONFIDENCE_DISCOUNT_MAX, min(confidence_multiplier, 0.82))
        risk_pct_multiplier = ADAPTIVE_RISK_PCT_MULTIPLIER_MIN
        active_inputs = active_inputs + [f"setup_disable:{setup_disable.get('bucket_key', 'unknown')}"]
        status = "setup_disabled"
    else:
        status = "active"
    return {
        "enabled": True,
        "status": status,
        "signal_weight": signal_weight,
        "confidence_multiplier": confidence_multiplier,
        "risk_pct_multiplier": min(risk_pct_multiplier, ADAPTIVE_RISK_PCT_MULTIPLIER_MAX),
        "hold_bias": hold_bias,
        "active_inputs": active_inputs,
        "weak_inputs": weak_inputs,
        "fallback_reason": None,
        "setup_disable": setup_disable,
    }


def summarize_adaptive_signal_state(
    context: dict[str, Any] | None,
    *,
    latest_rationale_codes: list[str] | None = None,
    latest_decision: str | None = None,
    latest_entry_mode: str | None = None,
) -> dict[str, Any]:
    rationale_codes = latest_rationale_codes or []
    adjustment = compute_adaptive_adjustment(
        context,
        decision=latest_decision or "hold",
        rationale_codes=rationale_codes,
        entry_mode=latest_entry_mode,
    )
    bounds = context.get("bounds", {}) if isinstance(context, dict) else {}
    window_weights = context.get("window_weights", {}) if isinstance(context, dict) else {}
    setup_disable_buckets = (
        [item for item in context.get("setup_disable_buckets", []) if isinstance(item, dict)]
        if isinstance(context, dict)
        else []
    )
    active_setup_disable_buckets = [item for item in setup_disable_buckets if bool(item.get("disabled", False))]
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
        "setup_disable": adjustment.get("setup_disable", {"matched": False, "active": False}),
        "setup_disable_active": bool(active_setup_disable_buckets),
        "active_setup_disable_buckets": active_setup_disable_buckets,
        "bounds": bounds,
        "window_weights": window_weights,
        "data_fallback_rule": (
            "If recent bucket samples are below the minimum threshold, all adaptive multipliers stay at the neutral default."
        ),
    }
