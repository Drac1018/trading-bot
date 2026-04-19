from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_mvp.models import AgentRun, Execution, Order, Position
from trading_mvp.schemas import (
    AIDecisionContextPacket,
    AIPriorContextPacket,
    CapitalEfficiencyBucketEntry,
    FeaturePayload,
    StrategyEngineBucketEntry,
)
from trading_mvp.services.capital_efficiency import build_capital_efficiency_report
from trading_mvp.services.holding_profile import HOLDING_PROFILE_POSITION, HOLDING_PROFILE_SWING
from trading_mvp.services.strategy_engine_analytics import build_strategy_engine_bucket_report
from trading_mvp.time_utils import utcnow_naive

PRIOR_LOOKBACK_DAYS = 21
PRIOR_REPORT_LIMIT = 256
ENGINE_PRIOR_MIN_SAMPLES = 3
CAPITAL_EFFICIENCY_PRIOR_MIN_SAMPLES = 3
SESSION_PRIOR_MIN_SAMPLES = 5
TIME_OF_DAY_PRIOR_MIN_SAMPLES = 6
SESSION_PRIOR_STRONG_SAMPLE_BUFFER = 2
TIME_OF_DAY_PRIOR_STRONG_SAMPLE_BUFFER = 3
SESSION_PRIOR_STRONG_MAX_RECENCY_MINUTES = 7 * 24 * 60
TIME_OF_DAY_PRIOR_STRONG_MAX_RECENCY_MINUTES = 5 * 24 * 60
SESSION_PRIOR_STRONG_MIN_CONCENTRATION = 0.55
TIME_OF_DAY_PRIOR_STRONG_MIN_CONCENTRATION = 0.65
PRIOR_REPORT_CACHE_TTL_SECONDS = 15
_PRIOR_REPORT_CACHE_SESSION_KEY = "ai_prior_context_report_cache_v1"

_ENGINE_CLASSIFICATION_SCORE = {
    "strong": 1.0,
    "neutral": 0.0,
    "weak": -1.0,
    "unavailable": 0.0,
}
_CAPITAL_CLASSIFICATION_SCORE = {
    "efficient": 1.0,
    "neutral": 0.0,
    "inefficient": -1.0,
    "unavailable": 0.0,
}
_DATA_QUALITY_SEVERITY = {
    "complete": 0,
    "partial": 1,
    "degraded": 2,
    "unavailable": 3,
}
_PRIOR_PENALTY_LEVELS = ("none", "light", "medium", "strong")


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _safe_float(value: object) -> float | None:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _weighted_mean(
    items: Iterable[object],
    *,
    value_getter,
    weight_getter,
) -> float | None:
    weighted_total = 0.0
    total_weight = 0.0
    for item in items:
        value = value_getter(item)
        weight = weight_getter(item)
        if value is None or weight <= 0:
            continue
        weighted_total += float(value) * float(weight)
        total_weight += float(weight)
    if total_weight <= 0:
        return None
    return weighted_total / total_weight


def _sum_values(items: Iterable[object], *, value_getter) -> float | None:
    total = 0.0
    seen = False
    for item in items:
        value = value_getter(item)
        if value is None:
            continue
        total += float(value)
        seen = True
    return total if seen else None


def _normalize_engine_classification(classification: str | None) -> str:
    normalized = str(classification or "").strip().lower()
    if normalized == "strong":
        return "strong"
    if normalized == "weak":
        return "weak"
    if normalized in {"mixed", "neutral"}:
        return "neutral"
    return "unavailable"


def _normalize_capital_classification(classification: str | None) -> str:
    normalized = str(classification or "").strip().lower()
    if normalized in {"efficient", "inefficient", "neutral"}:
        return normalized
    return "unavailable"


def _unique_codes(*groups: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for code in group:
            normalized = str(code or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            ordered.append(normalized)
    return ordered


def _selection_value(selection_context: Mapping[str, Any], *keys: str) -> str | None:
    selection = _as_dict(selection_context)
    for key in keys:
        value = selection.get(key)
        if value not in {None, ""}:
            return str(value)
    candidate_payload = _as_dict(selection.get("candidate"))
    for key in keys:
        value = candidate_payload.get(key)
        if value not in {None, ""}:
            return str(value)
    return None


def _selection_nested_value(selection_context: Mapping[str, Any], *path: str) -> str | None:
    current: object = _as_dict(selection_context)
    for key in path:
        current = _as_dict(current).get(key)
    if current in {None, ""}:
        return None
    return str(current)


def _prior_lookup_context(
    *,
    ai_context: AIDecisionContextPacket,
    selection_context: Mapping[str, Any] | None,
    feature_payload: FeaturePayload | None,
) -> dict[str, str | None]:
    selection = _as_dict(selection_context)
    selection_summary = _as_dict(ai_context.selection_context_summary)
    merged_selection = {
        **selection_summary,
        **selection,
    }
    if "strategy_engine_context" not in merged_selection and ai_context.strategy_engine_context:
        merged_selection["strategy_engine_context"] = dict(ai_context.strategy_engine_context)
    strategy_engine_context = _as_dict(merged_selection.get("strategy_engine_context"))
    session_context = _as_dict(strategy_engine_context.get("session_context"))
    selected_engine = _as_dict(strategy_engine_context.get("selected_engine"))

    scenario = (
        _selection_value(merged_selection, "scenario", "expected_scenario")
        or _selection_nested_value(merged_selection, "candidate", "scenario")
        or str(selected_engine.get("scenario") or "")
        or None
    )
    regime = (
        (feature_payload.regime.primary_regime if feature_payload is not None else None)
        or _selection_nested_value(merged_selection, "regime_summary", "primary_regime")
        or None
    )
    trend_alignment = (
        (feature_payload.regime.trend_alignment if feature_payload is not None else None)
        or _selection_nested_value(merged_selection, "regime_summary", "trend_alignment")
        or None
    )
    entry_mode = (
        _selection_value(merged_selection, "entry_mode", "candidate_entry_mode", "planned_entry_mode")
        or str(selected_engine.get("entry_mode") or "")
        or None
    )
    execution_policy_profile = (
        _selection_value(
            merged_selection,
            "execution_policy_profile",
            "policy_profile",
            "candidate_policy_profile",
        )
        or None
    )
    session_label = str(session_context.get("session_label") or "") or None
    time_of_day_bucket = str(session_context.get("time_of_day_bucket") or "") or None
    return {
        "strategy_engine": ai_context.strategy_engine,
        "symbol": ai_context.symbol.upper(),
        "timeframe": ai_context.timeframe,
        "scenario": str(scenario or "").lower() or None,
        "regime": str(regime or "").lower() or None,
        "trend_alignment": str(trend_alignment or "").lower() or None,
        "entry_mode": str(entry_mode or "").lower() or None,
        "execution_policy_profile": execution_policy_profile,
        "session_label": str(session_label or "").lower() or None,
        "time_of_day_bucket": str(time_of_day_bucket or "").lower() or None,
    }


def _matches_engine_bucket(bucket: StrategyEngineBucketEntry, lookup: Mapping[str, str | None]) -> bool:
    return all(
        (
            str(bucket.strategy_engine).lower() == str(lookup.get("strategy_engine") or "").lower(),
            str(bucket.symbol).upper() == str(lookup.get("symbol") or "").upper(),
            str(bucket.timeframe).lower() == str(lookup.get("timeframe") or "").lower(),
            str(bucket.scenario).lower() == str(lookup.get("scenario") or "").lower(),
            str(bucket.regime).lower() == str(lookup.get("regime") or "").lower(),
            str(bucket.trend_alignment).lower() == str(lookup.get("trend_alignment") or "").lower(),
            str(bucket.entry_mode).lower() == str(lookup.get("entry_mode") or "").lower(),
            str(bucket.execution_policy_profile) == str(lookup.get("execution_policy_profile") or ""),
        )
    )


def _matches_session_bucket(
    bucket: StrategyEngineBucketEntry,
    lookup: Mapping[str, str | None],
    *,
    field: str,
) -> bool:
    target = str(lookup.get(field) or "").lower()
    if not target:
        return False
    if not _matches_engine_bucket(bucket, lookup):
        return False
    return str(getattr(bucket, field)).lower() == target


def _matches_capital_bucket(bucket: CapitalEfficiencyBucketEntry, lookup: Mapping[str, str | None]) -> bool:
    return all(
        (
            str(bucket.symbol).upper() == str(lookup.get("symbol") or "").upper(),
            str(bucket.timeframe).lower() == str(lookup.get("timeframe") or "").lower(),
            str(bucket.scenario).lower() == str(lookup.get("scenario") or "").lower(),
            str(bucket.regime).lower() == str(lookup.get("regime") or "").lower(),
            str(bucket.entry_mode).lower() == str(lookup.get("entry_mode") or "").lower(),
            str(bucket.execution_policy_profile) == str(lookup.get("execution_policy_profile") or ""),
        )
    )


def _derive_engine_prior(
    rows: list[StrategyEngineBucketEntry],
    *,
    minimum_samples: int,
    unavailable_reason_code: str,
) -> dict[str, Any]:
    sample_count = sum(int(row.traded_decisions) for row in rows)
    threshold_satisfied = sample_count >= minimum_samples
    if not rows or not threshold_satisfied:
        return {
            "available": False,
            "sample_count": sample_count,
            "threshold_satisfied": threshold_satisfied,
            "classification": "unavailable",
            "expectancy_hint": None,
            "net_pnl_after_fees_hint": None,
            "avg_signed_slippage_bps_hint": None,
            "time_to_profit_hint_minutes": None,
            "drawdown_impact_hint": None,
            "reason_codes": [unavailable_reason_code],
        }

    expectancy = _weighted_mean(rows, value_getter=lambda item: item.expectancy, weight_getter=lambda item: item.traded_decisions)
    net_pnl_after_fees = _sum_values(rows, value_getter=lambda item: item.net_pnl_after_fees)
    avg_signed_slippage_bps = _weighted_mean(
        rows,
        value_getter=lambda item: item.avg_signed_slippage_bps,
        weight_getter=lambda item: item.traded_decisions,
    )
    time_to_profit = _weighted_mean(
        rows,
        value_getter=lambda item: item.average_time_to_profit_minutes,
        weight_getter=lambda item: item.traded_decisions,
    )
    drawdown_impact = _weighted_mean(
        rows,
        value_getter=lambda item: item.average_drawdown_impact,
        weight_getter=lambda item: item.traded_decisions,
    )
    score = _weighted_mean(
        rows,
        value_getter=lambda item: _ENGINE_CLASSIFICATION_SCORE[_normalize_engine_classification(item.classification)],
        weight_getter=lambda item: item.traded_decisions,
    ) or 0.0
    if score >= 0.45 and (expectancy or 0.0) > 0 and (net_pnl_after_fees or 0.0) > 0:
        classification = "strong"
    elif score <= -0.45 and (expectancy or 0.0) <= 0 and (net_pnl_after_fees or 0.0) <= 0:
        classification = "weak"
    else:
        classification = "neutral"
    reason_codes = [f"ENGINE_PRIOR_{classification.upper()}"]
    if (avg_signed_slippage_bps or 0.0) >= 12.0:
        reason_codes.append("ENGINE_PRIOR_SLIPPAGE_HEADWIND")
    if (drawdown_impact or 0.0) >= 0.8:
        reason_codes.append("ENGINE_PRIOR_DRAWDOWN_HEADWIND")
    return {
        "available": True,
        "sample_count": sample_count,
        "threshold_satisfied": True,
        "classification": classification,
        "expectancy_hint": round(expectancy, 6) if expectancy is not None else None,
        "net_pnl_after_fees_hint": round(net_pnl_after_fees, 6) if net_pnl_after_fees is not None else None,
        "avg_signed_slippage_bps_hint": round(avg_signed_slippage_bps, 6) if avg_signed_slippage_bps is not None else None,
        "time_to_profit_hint_minutes": round(time_to_profit, 6) if time_to_profit is not None else None,
        "drawdown_impact_hint": round(drawdown_impact, 6) if drawdown_impact is not None else None,
        "reason_codes": reason_codes,
    }


def _derive_capital_efficiency_prior(
    rows: list[CapitalEfficiencyBucketEntry],
    *,
    minimum_samples: int,
) -> dict[str, Any]:
    sample_count = sum(int(row.traded_decisions) for row in rows)
    threshold_satisfied = sample_count >= minimum_samples
    if not rows or not threshold_satisfied:
        return {
            "available": False,
            "sample_count": sample_count,
            "threshold_satisfied": threshold_satisfied,
            "classification": "unavailable",
            "pnl_per_exposure_hour_hint": None,
            "net_pnl_after_fees_per_hour_hint": None,
            "time_to_0_25r_hint_minutes": None,
            "time_to_0_5r_hint_minutes": None,
            "time_to_fail_hint_minutes": None,
            "capital_slot_occupancy_efficiency_hint": None,
            "reason_codes": ["CAPITAL_EFFICIENCY_PRIOR_UNAVAILABLE_INSUFFICIENT_SAMPLES"],
        }

    net_pnl_per_hour = _weighted_mean(
        rows,
        value_getter=lambda item: item.net_pnl_after_fees_per_hour,
        weight_getter=lambda item: item.traded_decisions,
    )
    pnl_per_hour = _weighted_mean(
        rows,
        value_getter=lambda item: item.pnl_per_exposure_hour,
        weight_getter=lambda item: item.traded_decisions,
    )
    time_to_0_25r = _weighted_mean(
        rows,
        value_getter=lambda item: item.average_time_to_0_25r_minutes,
        weight_getter=lambda item: item.traded_decisions,
    )
    time_to_0_5r = _weighted_mean(
        rows,
        value_getter=lambda item: item.average_time_to_0_5r_minutes,
        weight_getter=lambda item: item.traded_decisions,
    )
    time_to_fail = _weighted_mean(
        rows,
        value_getter=lambda item: item.average_time_to_fail_minutes,
        weight_getter=lambda item: item.traded_decisions,
    )
    capital_slot_occupancy_efficiency = _weighted_mean(
        rows,
        value_getter=lambda item: item.capital_slot_occupancy_efficiency,
        weight_getter=lambda item: item.traded_decisions,
    )
    score = _weighted_mean(
        rows,
        value_getter=lambda item: _CAPITAL_CLASSIFICATION_SCORE[_normalize_capital_classification(item.efficiency_classification)],
        weight_getter=lambda item: item.traded_decisions,
    ) or 0.0
    if score >= 0.45 and (net_pnl_per_hour or 0.0) > 0 and (capital_slot_occupancy_efficiency or 0.0) > 0:
        classification = "efficient"
    elif score <= -0.35 or (net_pnl_per_hour or 0.0) < 0:
        classification = "inefficient"
    else:
        classification = "neutral"
    reason_codes = [f"CAPITAL_EFFICIENCY_{classification.upper()}"]
    if classification == "inefficient" and time_to_fail is not None and time_to_fail <= 30.0:
        reason_codes.append("CAPITAL_EFFICIENCY_FAILS_FAST")
    return {
        "available": True,
        "sample_count": sample_count,
        "threshold_satisfied": True,
        "classification": classification,
        "pnl_per_exposure_hour_hint": round(pnl_per_hour, 6) if pnl_per_hour is not None else None,
        "net_pnl_after_fees_per_hour_hint": round(net_pnl_per_hour, 6) if net_pnl_per_hour is not None else None,
        "time_to_0_25r_hint_minutes": round(time_to_0_25r, 6) if time_to_0_25r is not None else None,
        "time_to_0_5r_hint_minutes": round(time_to_0_5r, 6) if time_to_0_5r is not None else None,
        "time_to_fail_hint_minutes": round(time_to_fail, 6) if time_to_fail is not None else None,
        "capital_slot_occupancy_efficiency_hint": (
            round(capital_slot_occupancy_efficiency, 6) if capital_slot_occupancy_efficiency is not None else None
        ),
        "reason_codes": reason_codes,
    }


def _latest_decision_at(rows: list[StrategyEngineBucketEntry]) -> datetime | None:
    latest: datetime | None = None
    for row in rows:
        row_latest = getattr(row, "latest_decision_at", None)
        if isinstance(row_latest, datetime) and (latest is None or row_latest > latest):
            latest = row_latest
    return latest


def _recency_minutes(
    *,
    latest_decision_at: datetime | None,
    reference_time: datetime,
) -> float | None:
    if latest_decision_at is None:
        return None
    return float(max(int((reference_time - latest_decision_at).total_seconds() // 60), 0))


def _calibrate_temporal_prior(
    prior: dict[str, Any],
    *,
    rows: list[StrategyEngineBucketEntry],
    engine_sample_count: int,
    reference_time: datetime,
    prefix: str,
    minimum_samples: int,
    strong_sample_buffer: int,
    strong_max_recency_minutes: int,
    strong_min_concentration: float,
) -> dict[str, Any]:
    sample_count = int(prior["sample_count"])
    latest_decision_at = _latest_decision_at(rows)
    recency_minutes = _recency_minutes(
        latest_decision_at=latest_decision_at,
        reference_time=reference_time,
    )
    concentration = (
        float(sample_count) / float(max(engine_sample_count, 1))
        if engine_sample_count > 0
        else None
    )
    classification = str(prior["classification"])
    calibration_reason_codes: list[str] = []

    if bool(prior["available"]) and classification == "strong":
        if sample_count < minimum_samples + strong_sample_buffer:
            calibration_reason_codes.append(f"{prefix}_PRIOR_STRONG_SAMPLE_EDGE")
        if recency_minutes is None or recency_minutes > strong_max_recency_minutes:
            calibration_reason_codes.append(f"{prefix}_PRIOR_STRONG_RECENCY_STALE")
        if concentration is not None and concentration < strong_min_concentration:
            calibration_reason_codes.append(f"{prefix}_PRIOR_STRONG_CONCENTRATION_DILUTED")
        if calibration_reason_codes:
            classification = "neutral"
            calibration_reason_codes.append(f"{prefix}_PRIOR_STRONG_DOWNGRADED_TO_NEUTRAL")

    return {
        **prior,
        "classification": classification,
        "recency_minutes": recency_minutes,
        "concentration": round(concentration, 6) if concentration is not None else None,
        "calibration_reason_codes": calibration_reason_codes,
    }


def _penalty_level(
    *,
    ai_context: AIDecisionContextPacket,
    engine_classification: str,
    capital_classification: str,
    session_classification: str,
    time_classification: str,
) -> str:
    penalty_score = 0
    if engine_classification == "weak":
        penalty_score = max(penalty_score, 2)
    if capital_classification == "inefficient":
        penalty_score = max(penalty_score, 1)
    session_time_weak = session_classification == "weak" or time_classification == "weak"
    if session_time_weak:
        penalty_score = max(penalty_score, 1)

    quality_severity = _DATA_QUALITY_SEVERITY.get(ai_context.data_quality.data_quality_grade, 0)
    aggressive_context = (
        ai_context.strategy_engine == "breakout_exception_engine"
        or ai_context.holding_profile in {HOLDING_PROFILE_SWING, HOLDING_PROFILE_POSITION}
    )
    if quality_severity >= 2 and (engine_classification == "weak" or capital_classification == "inefficient"):
        penalty_score = min(3, max(penalty_score + 1, 2))
    if quality_severity >= 2 and session_time_weak:
        penalty_score = min(3, max(penalty_score + 1, 2))
    if aggressive_context and quality_severity >= 2 and engine_classification != "strong":
        penalty_score = max(penalty_score, 3 if quality_severity >= 3 else 2)
    return _PRIOR_PENALTY_LEVELS[min(max(penalty_score, 0), 3)]


def _full_report_cache_key(
    session: Session,
    *,
    lookback_days: int,
    limit: int,
) -> tuple[str, int, int]:
    bind = session.get_bind()
    bind_key = str(getattr(bind, "url", "")) or repr(bind)
    return (bind_key, lookback_days, limit)


def _revision_component(
    session: Session,
    *,
    model,
    since: datetime,
    created_field: str | None = None,
) -> tuple[int, str | None]:
    updated_at_column = model.updated_at
    id_column = model.id
    if created_field is None:
        where_clause = updated_at_column >= since
    else:
        where_clause = getattr(model, created_field) >= since
    max_id, max_updated_at = session.execute(
        select(
            func.max(id_column),
            func.max(updated_at_column),
        ).where(where_clause)
    ).one()
    updated_iso = max_updated_at.isoformat() if isinstance(max_updated_at, datetime) else None
    return (int(max_id or 0), updated_iso)


def _prior_report_revision_signature(
    session: Session,
    *,
    lookback_days: int,
) -> tuple[tuple[int, str | None], ...]:
    since = utcnow_naive() - timedelta(days=lookback_days)
    return (
        _revision_component(session, model=AgentRun, since=since, created_field="created_at"),
        _revision_component(session, model=Order, since=since),
        _revision_component(session, model=Execution, since=since),
        _revision_component(session, model=Position, since=since),
    )


def _session_report_cache(session: Session) -> dict[tuple[str, int, int], dict[str, Any]]:
    cache = session.info.get(_PRIOR_REPORT_CACHE_SESSION_KEY)
    if cache is None:
        cache = {}
        session.info[_PRIOR_REPORT_CACHE_SESSION_KEY] = cache
    if not isinstance(cache, dict):
        raise TypeError("prior report cache must be a dict")
    return cache


def _build_full_prior_reports(
    session: Session,
    *,
    lookback_days: int,
    limit: int,
):
    engine_report = build_strategy_engine_bucket_report(
        session,
        lookback_days=lookback_days,
        limit=limit,
    )
    capital_efficiency_report = build_capital_efficiency_report(
        session,
        lookback_days=lookback_days,
        limit=limit,
    )
    return engine_report, capital_efficiency_report


def _load_prior_reports(
    session: Session,
    *,
    lookback_days: int,
    limit: int,
    use_cache: bool,
) -> tuple[object, object, dict[str, Any]]:
    if not use_cache:
        engine_report, capital_efficiency_report = _build_full_prior_reports(
            session,
            lookback_days=lookback_days,
            limit=limit,
        )
        return (
            engine_report,
            capital_efficiency_report,
            {
                "prior_read_path": "full_report",
                "cache_applied": False,
                "cache_fallback_used": False,
            },
        )
    try:
        now = utcnow_naive()
        cache_key = _full_report_cache_key(session, lookback_days=lookback_days, limit=limit)
        revision_signature = _prior_report_revision_signature(session, lookback_days=lookback_days)
        cache = _session_report_cache(session)
        cache_entry = cache.get(cache_key)
        if isinstance(cache_entry, dict):
            cached_at = cache_entry.get("cached_at")
            cached_signature = cache_entry.get("revision_signature")
            if (
                isinstance(cached_at, datetime)
                and (now - cached_at).total_seconds() <= PRIOR_REPORT_CACHE_TTL_SECONDS
                and cached_signature == revision_signature
                and cache_entry.get("engine_report") is not None
                and cache_entry.get("capital_efficiency_report") is not None
            ):
                return (
                    cache_entry["engine_report"],
                    cache_entry["capital_efficiency_report"],
                    {
                        "prior_read_path": "cache_hit",
                        "cache_applied": True,
                        "cache_fallback_used": False,
                    },
                )
        engine_report, capital_efficiency_report = _build_full_prior_reports(
            session,
            lookback_days=lookback_days,
            limit=limit,
        )
        cache[cache_key] = {
            "cached_at": now,
            "revision_signature": revision_signature,
            "engine_report": engine_report,
            "capital_efficiency_report": capital_efficiency_report,
        }
        return (
            engine_report,
            capital_efficiency_report,
            {
                "prior_read_path": "cache_miss",
                "cache_applied": True,
                "cache_fallback_used": False,
            },
        )
    except Exception:
        engine_report, capital_efficiency_report = _build_full_prior_reports(
            session,
            lookback_days=lookback_days,
            limit=limit,
        )
        return (
            engine_report,
            capital_efficiency_report,
            {
                "prior_read_path": "fallback_full_read",
                "cache_applied": False,
                "cache_fallback_used": True,
            },
        )


def build_ai_prior_context(
    session: Session,
    *,
    ai_context: AIDecisionContextPacket,
    selection_context: Mapping[str, Any] | None = None,
    feature_payload: FeaturePayload | None = None,
    lookback_days: int = PRIOR_LOOKBACK_DAYS,
    limit: int = PRIOR_REPORT_LIMIT,
    use_cache: bool = True,
    debug_collector: dict[str, Any] | None = None,
) -> AIPriorContextPacket:
    lookup = _prior_lookup_context(
        ai_context=ai_context,
        selection_context=selection_context,
        feature_payload=feature_payload,
    )
    required_lookup_fields = (
        "symbol",
        "timeframe",
        "scenario",
        "regime",
        "entry_mode",
        "execution_policy_profile",
    )
    if any(not lookup.get(field) for field in required_lookup_fields):
        if debug_collector is not None:
            debug_collector.update(
                {
                    "prior_read_path": "full_report",
                    "cache_applied": False,
                    "cache_fallback_used": False,
                }
            )
        return AIPriorContextPacket(
            prior_reason_codes=["PRIOR_CONTEXT_INCOMPLETE"],
            expected_payoff_efficiency_hint_summary={},
        )

    engine_report, capital_efficiency_report, read_debug = _load_prior_reports(
        session,
        lookback_days=lookback_days,
        limit=limit,
        use_cache=use_cache,
    )
    if debug_collector is not None:
        debug_collector.update(read_debug)

    matching_engine_rows = [
        bucket
        for bucket in engine_report.bucket_reports
        if _matches_engine_bucket(bucket, lookup)
    ]
    matching_session_rows = [
        bucket
        for bucket in engine_report.bucket_reports
        if _matches_session_bucket(bucket, lookup, field="session_label")
    ]
    matching_time_rows = [
        bucket
        for bucket in engine_report.bucket_reports
        if _matches_session_bucket(bucket, lookup, field="time_of_day_bucket")
    ]
    matching_capital_rows = [
        bucket
        for bucket in capital_efficiency_report.bucket_reports
        if _matches_capital_bucket(bucket, lookup)
    ]

    engine_prior = _derive_engine_prior(
        matching_engine_rows,
        minimum_samples=ENGINE_PRIOR_MIN_SAMPLES,
        unavailable_reason_code="ENGINE_PRIOR_UNAVAILABLE_INSUFFICIENT_SAMPLES",
    )
    session_prior = _derive_engine_prior(
        matching_session_rows,
        minimum_samples=SESSION_PRIOR_MIN_SAMPLES,
        unavailable_reason_code="SESSION_PRIOR_UNAVAILABLE_INSUFFICIENT_SAMPLES",
    )
    time_of_day_prior = _derive_engine_prior(
        matching_time_rows,
        minimum_samples=TIME_OF_DAY_PRIOR_MIN_SAMPLES,
        unavailable_reason_code="TIME_OF_DAY_PRIOR_UNAVAILABLE_INSUFFICIENT_SAMPLES",
    )
    session_prior = _calibrate_temporal_prior(
        session_prior,
        rows=matching_session_rows,
        engine_sample_count=int(engine_prior["sample_count"]),
        reference_time=engine_report.generated_at,
        prefix="SESSION",
        minimum_samples=SESSION_PRIOR_MIN_SAMPLES,
        strong_sample_buffer=SESSION_PRIOR_STRONG_SAMPLE_BUFFER,
        strong_max_recency_minutes=SESSION_PRIOR_STRONG_MAX_RECENCY_MINUTES,
        strong_min_concentration=SESSION_PRIOR_STRONG_MIN_CONCENTRATION,
    )
    time_of_day_prior = _calibrate_temporal_prior(
        time_of_day_prior,
        rows=matching_time_rows,
        engine_sample_count=int(engine_prior["sample_count"]),
        reference_time=engine_report.generated_at,
        prefix="TIME_OF_DAY",
        minimum_samples=TIME_OF_DAY_PRIOR_MIN_SAMPLES,
        strong_sample_buffer=TIME_OF_DAY_PRIOR_STRONG_SAMPLE_BUFFER,
        strong_max_recency_minutes=TIME_OF_DAY_PRIOR_STRONG_MAX_RECENCY_MINUTES,
        strong_min_concentration=TIME_OF_DAY_PRIOR_STRONG_MIN_CONCENTRATION,
    )
    capital_efficiency_prior = _derive_capital_efficiency_prior(
        matching_capital_rows,
        minimum_samples=CAPITAL_EFFICIENCY_PRIOR_MIN_SAMPLES,
    )

    prior_reason_codes = _unique_codes(
        list(engine_prior["reason_codes"]),
        list(capital_efficiency_prior["reason_codes"]),
        [code.replace("ENGINE_PRIOR_", "SESSION_PRIOR_") for code in session_prior["reason_codes"]],
        [code.replace("ENGINE_PRIOR_", "TIME_OF_DAY_PRIOR_") for code in time_of_day_prior["reason_codes"]],
        list(session_prior["calibration_reason_codes"]),
        list(time_of_day_prior["calibration_reason_codes"]),
    )
    prior_penalty_level = _penalty_level(
        ai_context=ai_context,
        engine_classification=str(engine_prior["classification"]),
        capital_classification=str(capital_efficiency_prior["classification"]),
        session_classification=str(session_prior["classification"]),
        time_classification=str(time_of_day_prior["classification"]),
    )
    payoff_efficiency_summary = {
        "engine_time_to_profit_hint_minutes": engine_prior["time_to_profit_hint_minutes"],
        "time_to_0_25r_hint_minutes": capital_efficiency_prior["time_to_0_25r_hint_minutes"],
        "time_to_0_5r_hint_minutes": capital_efficiency_prior["time_to_0_5r_hint_minutes"],
        "time_to_fail_hint_minutes": capital_efficiency_prior["time_to_fail_hint_minutes"],
        "net_pnl_after_fees_per_hour_hint": capital_efficiency_prior["net_pnl_after_fees_per_hour_hint"],
    }
    return AIPriorContextPacket(
        engine_prior_available=bool(engine_prior["available"]),
        engine_prior_sample_count=int(engine_prior["sample_count"]),
        engine_sample_threshold_satisfied=bool(engine_prior["threshold_satisfied"]),
        engine_prior_classification=str(engine_prior["classification"]),  # type: ignore[arg-type]
        engine_expectancy_hint=engine_prior["expectancy_hint"],
        engine_net_pnl_after_fees_hint=engine_prior["net_pnl_after_fees_hint"],
        engine_avg_signed_slippage_bps_hint=engine_prior["avg_signed_slippage_bps_hint"],
        engine_time_to_profit_hint_minutes=engine_prior["time_to_profit_hint_minutes"],
        engine_drawdown_impact_hint=engine_prior["drawdown_impact_hint"],
        capital_efficiency_available=bool(capital_efficiency_prior["available"]),
        capital_efficiency_sample_count=int(capital_efficiency_prior["sample_count"]),
        capital_efficiency_sample_threshold_satisfied=bool(capital_efficiency_prior["threshold_satisfied"]),
        capital_efficiency_classification=str(capital_efficiency_prior["classification"]),  # type: ignore[arg-type]
        pnl_per_exposure_hour_hint=capital_efficiency_prior["pnl_per_exposure_hour_hint"],
        net_pnl_after_fees_per_hour_hint=capital_efficiency_prior["net_pnl_after_fees_per_hour_hint"],
        time_to_0_25r_hint_minutes=capital_efficiency_prior["time_to_0_25r_hint_minutes"],
        time_to_0_5r_hint_minutes=capital_efficiency_prior["time_to_0_5r_hint_minutes"],
        time_to_fail_hint_minutes=capital_efficiency_prior["time_to_fail_hint_minutes"],
        capital_slot_occupancy_efficiency_hint=capital_efficiency_prior["capital_slot_occupancy_efficiency_hint"],
        session_prior_available=bool(session_prior["available"]),
        session_prior_sample_count=int(session_prior["sample_count"]),
        session_sample_threshold_satisfied=bool(session_prior["threshold_satisfied"]),
        session_prior_classification=str(session_prior["classification"]),  # type: ignore[arg-type]
        session_prior_recency_minutes=session_prior["recency_minutes"],
        time_of_day_prior_available=bool(time_of_day_prior["available"]),
        time_of_day_prior_sample_count=int(time_of_day_prior["sample_count"]),
        time_of_day_sample_threshold_satisfied=bool(time_of_day_prior["threshold_satisfied"]),
        time_of_day_prior_classification=str(time_of_day_prior["classification"]),  # type: ignore[arg-type]
        time_of_day_prior_recency_minutes=time_of_day_prior["recency_minutes"],
        session_time_calibration_reason_codes=_unique_codes(
            list(session_prior["calibration_reason_codes"]),
            list(time_of_day_prior["calibration_reason_codes"]),
        ),
        prior_reason_codes=prior_reason_codes,
        prior_penalty_level=prior_penalty_level,  # type: ignore[arg-type]
        expected_payoff_efficiency_hint_summary=payoff_efficiency_summary,
    )
