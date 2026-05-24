from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock, Thread
from time import monotonic
from typing import Any, TypedDict
from zoneinfo import ZoneInfo

from sqlalchemy import desc, func, inspect, or_, select, text
from sqlalchemy.orm import Session, load_only, sessionmaker

from trading_mvp.config import get_settings
from trading_mvp.models import (
    AgentRun,
    AuditEvent,
    Execution,
    Order,
    PendingEntryPlan,
    RiskCheck,
    SchedulerRun,
    Setting,
)
from trading_mvp.time_utils import parse_utc_datetime, utcnow_naive

AI_ATTEMPT_SOURCES = {"llm", "llm_fallback"}
AI_USAGE_SOURCE_FILTER_VALUES = AI_ATTEMPT_SOURCES | {"quality_fail_closed"}
AI_DECISION_VALUES = ("hold", "long", "short", "reduce", "exit")
AI_DEDUPED_EVENT_TYPE = "decision_ai_deduped"
AI_COST_RATES_USD_PER_1M_TOKENS: dict[str, dict[str, float]] = {
    "gpt-4.1": {"input": 2.0, "output": 8.0},
    "gpt-4.1-mini": {"input": 0.4, "output": 1.6},
    "gpt-4.1-nano": {"input": 0.1, "output": 0.4},
}
AI_ROLE_HOURLY_CALL_BUDGETS = {
    "market_settings_advisor": 4,
    "position_exit_review": 12,
    "trading_decision": 16,
}
AI_ROLE_DAILY_TOKEN_BUDGETS = {
    "market_settings_advisor": 250_000,
    "position_exit_review": 500_000,
    "trading_decision": 1_000_000,
}
AI_ROLE_CONSECUTIVE_FAILURE_BUDGETS = {
    "market_settings_advisor": 3,
    "position_exit_review": 5,
    "trading_decision": 5,
}
AI_USAGE_CACHE_TTL_SECONDS = 180.0
AI_USAGE_STALE_CACHE_TTL_SECONDS = 900.0
AI_USAGE_DETAIL_ROW_LIMIT = 10_000
AI_USAGE_DEFAULT_COST_MODEL = "gpt-4.1-mini"
AI_USAGE_REPORT_TIMEZONE_NAME = "Asia/Seoul"
AI_USAGE_REPORT_TIMEZONE = ZoneInfo(AI_USAGE_REPORT_TIMEZONE_NAME)
SOFT_SIGNAL_REVIEW_SUPPRESSED_SKIP_REASON = "SOFT_SIGNAL_REVIEW_SUPPRESSED_WEAK_CANDIDATE"
AI_TRADING_DECISION_WASTE_MIN_PROVIDER_CALLS_7D = 50
AI_TRADING_DECISION_WASTE_MAX_PROVIDER_TO_ORDER_RATE = 0.01
AI_TRADING_DECISION_WASTE_BACKOFF_MINUTES = 360
AI_TRADING_DECISION_WASTE_GUARD_REASON = "low_actionability_cost_guard_active"
AI_TRADING_DECISION_WASTE_GUARD_FALLBACK = (
    "skip provider call; deterministic HOLD/fail-closed path remains active"
)


class TokenUsage(TypedDict):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class AIWindowSummary(TypedDict):
    calls: int
    successes: int
    failures: int
    tokens: TokenUsage
    role_calls: dict[str, int]
    role_failures: dict[str, int]
    failure_reasons: list[str]


class AIUsageMetrics(TypedDict):
    recent_ai_calls_today_kst: int
    recent_ai_calls_24h: int
    recent_ai_calls_7d: int
    recent_ai_calls_30d: int
    recent_ai_successes_today_kst: int
    recent_ai_successes_24h: int
    recent_ai_successes_7d: int
    recent_ai_successes_30d: int
    recent_ai_failures_today_kst: int
    recent_ai_failures_24h: int
    recent_ai_failures_7d: int
    recent_ai_failures_30d: int
    recent_ai_tokens_today_kst: TokenUsage
    recent_ai_tokens_24h: TokenUsage
    recent_ai_tokens_7d: TokenUsage
    recent_ai_tokens_30d: TokenUsage
    recent_ai_role_calls_today_kst: dict[str, int]
    recent_ai_role_calls_24h: dict[str, int]
    recent_ai_role_calls_7d: dict[str, int]
    recent_ai_role_calls_30d: dict[str, int]
    recent_ai_role_failures_today_kst: dict[str, int]
    recent_ai_role_failures_24h: dict[str, int]
    recent_ai_role_failures_7d: dict[str, int]
    recent_ai_role_failures_30d: dict[str, int]
    recent_ai_failure_reasons: list[str]
    observed_monthly_ai_calls_projection: int
    observed_monthly_ai_calls_projection_breakdown: dict[str, int]
    observed_monthly_ai_cost_projection_usd: float | None
    observed_monthly_ai_net_projection_usd: float | None
    ai_protection_status: dict[str, Any]
    ai_cost_efficiency_summary: dict[str, Any]
    ai_usage_today_timezone: str
    ai_usage_today_start_at: str
    ai_usage_today_end_at: str
    ai_usage_summary_today_kst: dict[str, Any]
    ai_usage_summary_24h: dict[str, Any]
    ai_usage_summary_7d: dict[str, Any]
    ai_usage_summary_30d: dict[str, Any]


_AI_USAGE_METRICS_CACHE: dict[
    tuple[object, ...],
    tuple[float, tuple[object, ...], AIUsageMetrics],
] = {}
_AI_USAGE_METRICS_CACHE_LOCK = Lock()
_AI_USAGE_METRICS_REFRESHING: set[tuple[object, ...]] = set()


def clear_ai_usage_metrics_cache() -> None:
    with _AI_USAGE_METRICS_CACHE_LOCK:
        _AI_USAGE_METRICS_CACHE.clear()
        _AI_USAGE_METRICS_REFRESHING.clear()


def _warm_ai_usage_metrics_cache(bind: Any) -> None:
    try:
        refresh_session_factory = sessionmaker(
            bind=bind,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
        with refresh_session_factory() as session:
            build_ai_usage_metrics(session, allow_stale=False)
    except Exception:
        return


def _refresh_ai_usage_metrics_cache(bind: Any, cache_key: tuple[object, ...]) -> None:
    try:
        _warm_ai_usage_metrics_cache(bind)
    finally:
        with _AI_USAGE_METRICS_CACHE_LOCK:
            _AI_USAGE_METRICS_REFRESHING.discard(cache_key)


def _start_ai_usage_metrics_refresh(session: Session, cache_key: tuple[object, ...]) -> None:
    try:
        bind = session.get_bind()
    except Exception:
        return
    if bind is None:
        return
    with _AI_USAGE_METRICS_CACHE_LOCK:
        if cache_key in _AI_USAGE_METRICS_REFRESHING:
            return
        _AI_USAGE_METRICS_REFRESHING.add(cache_key)
    Thread(
        target=_refresh_ai_usage_metrics_cache,
        kwargs={"bind": bind, "cache_key": cache_key},
        daemon=True,
        name="ai-usage-metrics-refresh",
    ).start()


def _copy_cached_ai_usage_metrics(entry: tuple[float, tuple[object, ...], AIUsageMetrics]) -> AIUsageMetrics:
    return deepcopy(entry[2])


def _find_stale_ai_usage_metrics_cache(
    cache_key: tuple[object, ...],
    *,
    monotonic_now: float,
) -> tuple[float, tuple[object, ...], AIUsageMetrics] | None:
    with _AI_USAGE_METRICS_CACHE_LOCK:
        candidates = [
            entry
            for key, entry in _AI_USAGE_METRICS_CACHE.items()
            if key
            and key[0] == cache_key[0]
            and key[-1] == cache_key[-1]
            and monotonic_now - entry[0] <= AI_USAGE_STALE_CACHE_TTL_SECONDS
        ]
    if not candidates:
        return None
    return max(candidates, key=lambda entry: entry[0])


def warm_ai_usage_metrics_cache(
    session_factory: Callable[[], Session],
    *,
    synchronous: bool = False,
) -> bool:
    try:
        with session_factory() as session:
            bind = session.get_bind()
        if bind is None:
            return False
        if synchronous:
            _warm_ai_usage_metrics_cache(bind)
            return True
        Thread(
            target=_warm_ai_usage_metrics_cache,
            kwargs={"bind": bind},
            daemon=True,
            name="ai-usage-metrics-warmup",
        ).start()
        return True
    except Exception:
        return False


@dataclass(slots=True)
class OpenAICallGate:
    allowed: bool
    reason: str
    retry_after_seconds: int = 0
    backoff_minutes: int = 0
    manual_guard_minutes: int = 0
    last_attempt_at: datetime | None = None
    failure_reason: str | None = None
    evidence: dict[str, Any] | None = None

    def as_metadata(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "allowed": self.allowed,
            "reason": self.reason,
            "retry_after_seconds": self.retry_after_seconds,
            "backoff_minutes": self.backoff_minutes,
            "manual_guard_minutes": self.manual_guard_minutes,
        }
        if self.last_attempt_at is not None:
            payload["last_attempt_at"] = self.last_attempt_at.isoformat()
        if self.failure_reason is not None:
            payload["failure_reason"] = self.failure_reason
        if self.evidence is not None:
            payload["evidence"] = dict(self.evidence)
        return payload


@dataclass(slots=True)
class CostEstimate:
    estimated_cost_usd: float | None
    input_tokens: int
    output_tokens: int
    status: str
    model: str | None = None
    model_source: str = "unknown"


@dataclass(slots=True)
class _LoadedDownstreamRows:
    risk_rows: Sequence[RiskCheck]
    order_rows: Sequence[Order]
    execution_rows_by_order_id: Mapping[int, Sequence[Execution]]
    pending_plan_rows: Sequence[PendingEntryPlan]


def manual_ai_guard_minutes(settings_row: Setting) -> int:
    return max(2, min(int(settings_row.ai_call_interval_minutes), 5))


def classify_ai_failure(error: str | None) -> str | None:
    if not error:
        return None
    message = error.lower()
    if "insufficient_quota" in message or "quota" in message:
        return "QUOTA"
    if "429" in message or "rate limit" in message:
        return "RATE_LIMIT"
    if "401" in message or "403" in message or "unauthorized" in message or "forbidden" in message:
        return "AUTH"
    if "timeout" in message or "timed out" in message:
        return "TIMEOUT"
    if "400" in message or "bad request" in message or "invalid" in message or "schema" in message:
        return "BAD_REQUEST"
    if "500" in message or "502" in message or "503" in message or "504" in message:
        return "UPSTREAM"
    return "UNKNOWN"


def failure_backoff_minutes(settings_row: Setting, error: str | None) -> int:
    base = max(5, min(int(settings_row.ai_call_interval_minutes), 30))
    reason = classify_ai_failure(error)
    if reason in {"AUTH", "BAD_REQUEST"}:
        return max(base, 60)
    if reason == "QUOTA":
        return max(base, 60)
    if reason == "RATE_LIMIT":
        return max(base, 30)
    if reason in {"TIMEOUT", "UPSTREAM"}:
        return max(base, 15)
    return base


def _metadata_source(row: AgentRun) -> str:
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    value = metadata.get("source")
    return value if isinstance(value, str) else ""


def _metadata_error(row: AgentRun) -> str:
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    value = metadata.get("error")
    return value if isinstance(value, str) else ""


def _usage_from_mapping(raw: Mapping[str, Any] | None) -> TokenUsage:
    if not isinstance(raw, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    prompt_tokens = int(raw.get("prompt_tokens", raw.get("input_tokens", 0)) or 0)
    completion_tokens = int(raw.get("completion_tokens", raw.get("output_tokens", 0)) or 0)
    total_tokens = int(raw.get("total_tokens", prompt_tokens + completion_tokens) or 0)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _metadata_usage(row: AgentRun) -> TokenUsage:
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    return _usage_from_mapping(metadata.get("usage") if isinstance(metadata.get("usage"), dict) else None)


def _metadata_has_usage(row: AgentRun) -> bool:
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    return isinstance(metadata.get("usage"), dict)


def _mapping_if_loaded(row: AgentRun, field_name: str) -> dict[str, Any]:
    try:
        if field_name in inspect(row).unloaded:
            return {}
    except Exception:
        pass
    value = getattr(row, field_name, None)
    return value if isinstance(value, dict) else {}


def _metadata_model(row: AgentRun) -> str | None:
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    for key in ("ai_model", "model", "openai_model"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    input_payload = _mapping_if_loaded(row, "input_payload")
    output_payload = _mapping_if_loaded(row, "output_payload")
    for payload in (input_payload, output_payload):
        for key in ("ai_model", "model", "openai_model"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _row_cost_model(row: AgentRun) -> tuple[str | None, str]:
    metadata_model = _metadata_model(row)
    if metadata_model:
        return metadata_model, "metadata"
    if is_ai_attempt(row):
        try:
            configured_model = str(getattr(get_settings(), "openai_model", "") or "").strip()
        except Exception:
            configured_model = ""
        return configured_model or AI_USAGE_DEFAULT_COST_MODEL, "settings_fallback"
    return None, "unknown"


def _canonical_model_name(model: str | None) -> str | None:
    if not model:
        return None
    normalized = model.strip().lower()
    for known_model in AI_COST_RATES_USD_PER_1M_TOKENS:
        if normalized == known_model:
            return known_model
    for known_model in sorted(AI_COST_RATES_USD_PER_1M_TOKENS, key=len, reverse=True):
        if normalized.startswith(f"{known_model}-"):
            return known_model
    return normalized


def _rate_for_model(model: str | None) -> dict[str, float] | None:
    canonical = _canonical_model_name(model)
    if canonical is None:
        return None
    return AI_COST_RATES_USD_PER_1M_TOKENS.get(canonical)


def estimate_ai_usage_cost_usd(*, model: str | None, usage: Mapping[str, Any] | None) -> float | None:
    tokens = _usage_from_mapping(usage)
    if tokens["total_tokens"] <= 0 and tokens["prompt_tokens"] <= 0 and tokens["completion_tokens"] <= 0:
        return 0.0
    rate = _rate_for_model(model)
    if rate is None:
        return None
    estimated = (
        (tokens["prompt_tokens"] * rate["input"])
        + (tokens["completion_tokens"] * rate["output"])
    ) / 1_000_000
    return round(estimated, 8)


def _estimate_row_cost(row: AgentRun) -> CostEstimate:
    usage = _metadata_usage(row)
    model, model_source = _row_cost_model(row)
    if not _metadata_has_usage(row):
        return CostEstimate(
            estimated_cost_usd=None,
            input_tokens=usage["prompt_tokens"],
            output_tokens=usage["completion_tokens"],
            status="missing_usage",
            model=model,
            model_source=model_source,
        )
    cost = estimate_ai_usage_cost_usd(model=model, usage=usage)
    if cost is None:
        return CostEstimate(
            estimated_cost_usd=None,
            input_tokens=usage["prompt_tokens"],
            output_tokens=usage["completion_tokens"],
            status="unknown_model_rate",
            model=model,
            model_source=model_source,
        )
    return CostEstimate(
        estimated_cost_usd=cost,
        input_tokens=usage["prompt_tokens"],
        output_tokens=usage["completion_tokens"],
        status="estimated" if model_source == "metadata" else "estimated_model_fallback",
        model=model,
        model_source=model_source,
    )


def _output_payload(row: AgentRun) -> dict[str, Any]:
    return _mapping_if_loaded(row, "output_payload")


def _metadata_payload(row: AgentRun) -> dict[str, Any]:
    return row.metadata_json if isinstance(row.metadata_json, dict) else {}


def _output_decision(row: AgentRun) -> str | None:
    decision = str(_output_payload(row).get("decision") or "").strip().lower()
    return decision if decision in AI_DECISION_VALUES else None


def _truthy_payload_value(*values: Any) -> bool:
    return any(value is True for value in values)


def is_ai_attempt(row: AgentRun) -> bool:
    return row.provider_name == "openai" or _metadata_source(row) in AI_ATTEMPT_SOURCES


def is_ai_success(row: AgentRun) -> bool:
    return row.provider_name == "openai" and _metadata_source(row) == "llm"


def is_ai_failure(row: AgentRun) -> bool:
    return _metadata_source(row) == "llm_fallback" or bool(_metadata_error(row))


def _metadata_reason_list(row: AgentRun) -> list[str]:
    metadata = _metadata_payload(row)
    output = _output_payload(row)
    values: list[str] = []
    for key in ("pre_ai_skip_reason", "ai_skipped_reason", "last_ai_skip_reason"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    if str(metadata.get("source") or "") == "quality_fail_closed":
        values.append(str(metadata.get("provider_status") or "QUALITY_FAIL_CLOSED"))
    for key in ("data_quality_block_reason_codes", "event_risk_reason_codes"):
        raw_values = metadata.get(key)
        if isinstance(raw_values, list):
            values.extend(str(item) for item in raw_values if item)
    raw_flags = output.get("data_quality_flags")
    if isinstance(raw_flags, list):
        values.extend(str(item) for item in raw_flags if item)
    return values


def _normalize_skip_reason(reason: str) -> str:
    normalized = reason.strip().upper()
    if not normalized:
        return "UNKNOWN"
    if "STALE" in normalized or "INCOMPLETE_MARKET_DATA" in normalized:
        return "STALE_MARKET_DATA"
    if "MACRO_EVENT_IMMINENT" in normalized:
        return "MACRO_EVENT_IMMINENT"
    if "MACRO_EVENT_RISK_WINDOW_ACTIVE" in normalized or "MACRO_RELEASE_REACTION_WINDOW" in normalized:
        return "MACRO_EVENT_RISK_WINDOW_ACTIVE"
    if "SPREAD" in normalized and ("STRESS" in normalized or "WIDE" in normalized):
        return "SPREAD_STRESS"
    if "EXPOSURE" in normalized or "HEADROOM" in normalized or "POSITION_LIMIT" in normalized:
        return "EXPOSURE_LIMIT"
    if "LOW_SCORE" in normalized or ("LOW" in normalized and "SCORE" in normalized):
        return "LOW_SCORE"
    return normalized


def _skip_reason_labels(row: AgentRun) -> list[str]:
    labels = [_normalize_skip_reason(reason) for reason in _metadata_reason_list(row)]
    return list(dict.fromkeys(label for label in labels if label))


def _is_preai_skipped(row: AgentRun) -> bool:
    if is_ai_attempt(row):
        return False
    metadata = _metadata_payload(row)
    return bool(
        _skip_reason_labels(row)
        or metadata.get("provider_not_called_due_to_quality") is True
        or str(metadata.get("source") or "") == "quality_fail_closed"
    )


def _agent_run_ai_usage_filter():
    metadata_source = AgentRun.metadata_json["source"].as_string()
    return or_(
        AgentRun.provider_name == "openai",
        metadata_source.in_(sorted(AI_USAGE_SOURCE_FILTER_VALUES)),
        AgentRun.metadata_json["pre_ai_skip_reason"].as_string().is_not(None),
        AgentRun.metadata_json["ai_skipped_reason"].as_string().is_not(None),
        AgentRun.metadata_json["last_ai_skip_reason"].as_string().is_not(None),
        AgentRun.metadata_json["provider_not_called_due_to_quality"].as_boolean().is_(True),
    )


def _safe_float(value: object, default: float = 0.0) -> float:
    if value in {None, ""}:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return default


def _empty_downstream_summary() -> dict[str, Any]:
    return {
        "agent_runs": 0,
        "risk_checks": 0,
        "risk_allowed": 0,
        "risk_blocked": 0,
        "orders": 0,
        "executions": 0,
        "fills": 0,
        "realized_pnl": 0.0,
        "fee": 0.0,
        "net_realized_pnl": 0.0,
        "average_slippage_pct": 0.0,
        "risk_reason_counts": {},
        "pending_entry_plans": 0,
        "active_pending_entry_plans": 0,
        "canceled_pending_entry_plans": 0,
        "expired_pending_entry_plans": 0,
        "pending_plan_status_counts": {},
    }


def _counter_payload(counter: Mapping[str, int], *, limit: int | None = None) -> dict[str, int]:
    items = sorted(counter.items(), key=lambda item: (-int(item[1]), item[0]))
    if limit is not None:
        items = items[: max(int(limit), 0)]
    return {str(key): int(value) for key, value in items if int(value) > 0}


def _risk_reason_counts(rows: Sequence[RiskCheck]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        for reason in row.reason_codes or []:
            label = str(reason or "").strip()
            if label:
                counts[label] += 1
    return _counter_payload(counts)


def _pending_plan_summary(rows: Sequence[PendingEntryPlan]) -> dict[str, Any]:
    status_counts: Counter[str] = Counter()
    active_count = 0
    canceled_count = 0
    expired_count = 0
    for row in rows:
        status = str(row.plan_status or "unknown").strip().lower() or "unknown"
        status_counts[status] += 1
        if status in {"armed", "pending", "active", "watching"}:
            active_count += 1
        if status == "canceled" or row.canceled_at is not None:
            canceled_count += 1
        if status == "expired":
            expired_count += 1
    return {
        "pending_entry_plans": len(rows),
        "active_pending_entry_plans": active_count,
        "canceled_pending_entry_plans": canceled_count,
        "expired_pending_entry_plans": expired_count,
        "pending_plan_status_counts": _counter_payload(status_counts),
    }


def _downstream_summary(session: Session, decision_ids: Sequence[int]) -> dict[str, Any]:
    unique_decision_ids = sorted({int(item) for item in decision_ids if item is not None})
    if not unique_decision_ids:
        return _empty_downstream_summary()

    risk_rows = list(
        session.scalars(select(RiskCheck).where(RiskCheck.decision_run_id.in_(unique_decision_ids)))
    )
    order_rows = list(
        session.scalars(select(Order).where(Order.decision_run_id.in_(unique_decision_ids)))
    )
    order_ids = [row.id for row in order_rows]
    execution_rows = (
        list(session.scalars(select(Execution).where(Execution.order_id.in_(order_ids))))
        if order_ids
        else []
    )
    pending_plan_rows = list(
        session.scalars(
            select(PendingEntryPlan).where(PendingEntryPlan.source_decision_run_id.in_(unique_decision_ids))
        )
    )
    slippages = [_safe_float(row.slippage_pct) for row in execution_rows]
    realized_pnl = sum(_safe_float(row.realized_pnl) for row in execution_rows)
    fee = sum(_safe_float(row.fee_paid) for row in execution_rows)
    return {
        "agent_runs": len(unique_decision_ids),
        "risk_checks": len(risk_rows),
        "risk_allowed": sum(1 for row in risk_rows if bool(row.allowed)),
        "risk_blocked": sum(1 for row in risk_rows if not bool(row.allowed)),
        "orders": len(order_rows),
        "executions": len(execution_rows),
        "fills": len(execution_rows),
        "realized_pnl": realized_pnl,
        "fee": fee,
        "net_realized_pnl": realized_pnl - fee,
        "average_slippage_pct": sum(slippages) / len(slippages) if slippages else 0.0,
        "risk_reason_counts": _risk_reason_counts(risk_rows),
        **_pending_plan_summary(pending_plan_rows),
    }


def _summarize_loaded_downstream(
    decision_ids: Sequence[int],
    *,
    risk_rows: Sequence[RiskCheck],
    order_rows: Sequence[Order],
    execution_rows_by_order_id: Mapping[int, Sequence[Execution]],
    pending_plan_rows: Sequence[PendingEntryPlan],
) -> dict[str, Any]:
    unique_decision_ids = {int(item) for item in decision_ids if item is not None}
    if not unique_decision_ids:
        return _empty_downstream_summary()

    selected_risk_rows = [
        row for row in risk_rows if row.decision_run_id is not None and int(row.decision_run_id) in unique_decision_ids
    ]
    selected_order_rows = [
        row for row in order_rows if row.decision_run_id is not None and int(row.decision_run_id) in unique_decision_ids
    ]
    selected_order_ids = {int(row.id) for row in selected_order_rows if row.id is not None}
    selected_execution_rows = [
        execution
        for order_id in selected_order_ids
        for execution in execution_rows_by_order_id.get(order_id, [])
    ]
    selected_pending_plan_rows = [
        row
        for row in pending_plan_rows
        if row.source_decision_run_id is not None and int(row.source_decision_run_id) in unique_decision_ids
    ]
    slippages = [_safe_float(row.slippage_pct) for row in selected_execution_rows]
    realized_pnl = sum(_safe_float(row.realized_pnl) for row in selected_execution_rows)
    fee = sum(_safe_float(row.fee_paid) for row in selected_execution_rows)
    return {
        "agent_runs": len(unique_decision_ids),
        "risk_checks": len(selected_risk_rows),
        "risk_allowed": sum(1 for row in selected_risk_rows if bool(row.allowed)),
        "risk_blocked": sum(1 for row in selected_risk_rows if not bool(row.allowed)),
        "orders": len(selected_order_rows),
        "executions": len(selected_execution_rows),
        "fills": len(selected_execution_rows),
        "realized_pnl": realized_pnl,
        "fee": fee,
        "net_realized_pnl": realized_pnl - fee,
        "average_slippage_pct": sum(slippages) / len(slippages) if slippages else 0.0,
        "risk_reason_counts": _risk_reason_counts(selected_risk_rows),
        **_pending_plan_summary(selected_pending_plan_rows),
    }


def _load_downstream_rows(session: Session, decision_ids: Sequence[int]) -> _LoadedDownstreamRows:
    unique_decision_ids = sorted({int(item) for item in decision_ids if item is not None})
    if not unique_decision_ids:
        return _LoadedDownstreamRows(
            risk_rows=[],
            order_rows=[],
            execution_rows_by_order_id={},
            pending_plan_rows=[],
        )

    risk_rows = list(
        session.scalars(
            select(RiskCheck)
            .options(
                load_only(
                    RiskCheck.id,
                    RiskCheck.decision_run_id,
                    RiskCheck.allowed,
                    RiskCheck.reason_codes,
                )
            )
            .where(RiskCheck.decision_run_id.in_(unique_decision_ids))
        )
    )
    order_rows = list(
        session.scalars(
            select(Order)
            .options(load_only(Order.id, Order.decision_run_id))
            .where(Order.decision_run_id.in_(unique_decision_ids))
        )
    )
    pending_plan_rows = list(
        session.scalars(
            select(PendingEntryPlan)
            .options(
                load_only(
                    PendingEntryPlan.id,
                    PendingEntryPlan.source_decision_run_id,
                    PendingEntryPlan.plan_status,
                    PendingEntryPlan.canceled_at,
                )
            )
            .where(PendingEntryPlan.source_decision_run_id.in_(unique_decision_ids))
        )
    )
    order_ids = [int(row.id) for row in order_rows if row.id is not None]
    execution_rows_by_order_id: dict[int, list[Execution]] = {}
    if order_ids:
        for row in session.scalars(
            select(Execution)
            .options(
                load_only(
                    Execution.id,
                    Execution.order_id,
                    Execution.slippage_pct,
                    Execution.realized_pnl,
                    Execution.fee_paid,
                )
            )
            .where(Execution.order_id.in_(order_ids))
        ):
            if row.order_id is None:
                continue
            execution_rows_by_order_id.setdefault(int(row.order_id), []).append(row)

    return _LoadedDownstreamRows(
        risk_rows=risk_rows,
        order_rows=order_rows,
        execution_rows_by_order_id=execution_rows_by_order_id,
        pending_plan_rows=pending_plan_rows,
    )


def _downstream_summaries(
    session: Session,
    decision_ids: Sequence[int],
    decision_ids_by_decision: Mapping[str, Sequence[int]],
    *,
    loaded_downstream: _LoadedDownstreamRows | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    unique_decision_ids = sorted({int(item) for item in decision_ids if item is not None})
    if not unique_decision_ids:
        return (
            _empty_downstream_summary(),
            {decision: _empty_downstream_summary() for decision in decision_ids_by_decision},
        )

    downstream_rows = loaded_downstream or _load_downstream_rows(session, unique_decision_ids)

    downstream = _summarize_loaded_downstream(
        unique_decision_ids,
        risk_rows=downstream_rows.risk_rows,
        order_rows=downstream_rows.order_rows,
        execution_rows_by_order_id=downstream_rows.execution_rows_by_order_id,
        pending_plan_rows=downstream_rows.pending_plan_rows,
    )
    downstream_by_decision = {
        decision: _summarize_loaded_downstream(
            bucket_decision_ids,
            risk_rows=downstream_rows.risk_rows,
            order_rows=downstream_rows.order_rows,
            execution_rows_by_order_id=downstream_rows.execution_rows_by_order_id,
            pending_plan_rows=downstream_rows.pending_plan_rows,
        )
        for decision, bucket_decision_ids in decision_ids_by_decision.items()
    }
    return downstream, downstream_by_decision


def _safe_rate(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 8)


def _ai_roi_summary(
    downstream: Mapping[str, Any],
    *,
    known_ai_cost_usd: float,
    provider_calls: int,
    cost_complete: bool,
    missing_usage_rows: int,
) -> dict[str, Any]:
    trade_net_realized_pnl = _safe_float(downstream.get("net_realized_pnl"))
    fills = int(downstream.get("fills") or 0)
    orders = int(downstream.get("orders") or 0)
    risk_allowed = int(downstream.get("risk_allowed") or 0)
    return {
        "trade_net_realized_pnl": round(trade_net_realized_pnl, 8),
        "known_ai_cost_usd": round(known_ai_cost_usd, 8),
        "net_after_known_ai_cost_usd": round(trade_net_realized_pnl - known_ai_cost_usd, 8),
        "cost_complete": cost_complete,
        "missing_usage_rows": int(missing_usage_rows),
        "cost_per_provider_call_usd": _safe_rate(known_ai_cost_usd, provider_calls),
        "cost_per_risk_allowed_usd": _safe_rate(known_ai_cost_usd, risk_allowed),
        "cost_per_order_usd": _safe_rate(known_ai_cost_usd, orders),
        "cost_per_fill_usd": _safe_rate(known_ai_cost_usd, fills),
        "risk_allowed_to_order_rate": _safe_rate(float(orders), float(risk_allowed)),
        "note": (
            "ROI uses local estimated AI cost from stored usage metadata. "
            "Rows without provider usage are excluded from known_ai_cost_usd."
        ),
    }


def _ai_actionability_summary(
    downstream: Mapping[str, Any],
    *,
    provider_calls: int,
) -> dict[str, Any]:
    risk_checks = int(downstream.get("risk_checks") or 0)
    risk_allowed = int(downstream.get("risk_allowed") or 0)
    risk_blocked = int(downstream.get("risk_blocked") or 0)
    orders = int(downstream.get("orders") or 0)
    fills = int(downstream.get("fills") or 0)
    if fills > 0:
        usefulness_status = "filled"
    elif orders > 0:
        usefulness_status = "ordered"
    elif risk_allowed > 0:
        usefulness_status = "risk_allowed"
    elif risk_blocked > 0:
        usefulness_status = "risk_blocked"
    elif provider_calls > 0:
        usefulness_status = "not_actionable"
    else:
        usefulness_status = "no_provider_calls"
    warning_status = None
    warning_title = None
    warning_detail = None
    if provider_calls > 0 and risk_checks > 0 and risk_allowed == 0:
        warning_status = "provider_invoked_all_blocked"
        warning_title = "AI 호출은 있었지만 리스크 승인 0건"
        warning_detail = (
            f"provider 호출 {provider_calls}건 이후 risk check {risk_checks}건이 모두 미승인입니다. "
            "전역 장애라기보다 HOLD 또는 후보 품질 부족일 수 있으니 risk view에서 hold 이유를 함께 확인하세요."
        )
    return {
        "provider_calls": int(provider_calls),
        "risk_checks_after_provider": risk_checks,
        "risk_allowed_after_provider": risk_allowed,
        "risk_blocked_after_provider": risk_blocked,
        "orders_after_provider": orders,
        "fills_after_provider": fills,
        "provider_to_risk_allowed_rate": _safe_rate(float(risk_allowed), float(provider_calls)),
        "provider_to_risk_blocked_rate": _safe_rate(float(risk_blocked), float(provider_calls)),
        "provider_to_order_rate": _safe_rate(float(orders), float(provider_calls)),
        "provider_to_fill_rate": _safe_rate(float(fills), float(provider_calls)),
        "usefulness_status": usefulness_status,
        "warning_status": warning_status,
        "warning_title": warning_title,
        "warning_detail": warning_detail,
        "basis": "provider-invoked trading_decision AgentRun rows joined to downstream RiskCheck, Order, and Execution rows.",
    }


def _output_reason_codes(row: AgentRun) -> list[str]:
    output = _output_payload(row)
    metadata = _metadata_payload(row)
    values: list[str] = []
    for source in (output, metadata):
        for key in (
            "rationale_codes",
            "reason_codes",
            "blocked_reason_codes",
            "ai_trigger_reason_codes",
            "data_quality_block_reason_codes",
        ):
            raw_values = source.get(key)
            if isinstance(raw_values, list):
                values.extend(str(item).strip() for item in raw_values if str(item or "").strip())
            elif isinstance(raw_values, str) and raw_values.strip():
                values.append(raw_values.strip())
    return list(dict.fromkeys(values))


def _reason_bucket_counts(rows: Sequence[AgentRun], *, decision: str | None = None) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        if decision is not None and _output_decision(row) != decision:
            continue
        for reason in _output_reason_codes(row) or _skip_reason_labels(row):
            counts[reason] += 1
    return _counter_payload(counts, limit=12)


def _known_cost_for_rows(rows: Sequence[AgentRun]) -> tuple[float, int, int, int, int]:
    known_cost = 0.0
    input_tokens = 0
    output_tokens = 0
    missing_usage_rows = 0
    unknown_cost_rows = 0
    for row in rows:
        cost = _estimate_row_cost(row)
        input_tokens += cost.input_tokens
        output_tokens += cost.output_tokens
        if cost.status == "missing_usage":
            missing_usage_rows += 1
            unknown_cost_rows += 1
        elif cost.estimated_cost_usd is None:
            unknown_cost_rows += 1
        else:
            known_cost += cost.estimated_cost_usd
    return round(known_cost, 8), input_tokens, output_tokens, missing_usage_rows, unknown_cost_rows


def _advisor_profile(row: AgentRun) -> str:
    output = _output_payload(row)
    metadata = _metadata_payload(row)
    value = output.get("recommended_profile_id") or metadata.get("recommended_profile_id")
    return str(value or "unknown").strip() or "unknown"


def _advisor_status(row: AgentRun) -> str:
    metadata = _metadata_payload(row)
    return str(metadata.get("status") or row.status or "unknown").strip() or "unknown"


def _advisor_efficiency(rows: Sequence[AgentRun], *, now: datetime) -> dict[str, Any]:
    advisor_rows = [row for row in rows if row.role == "market_settings_advisor" and is_ai_attempt(row)]
    profiles = [_advisor_profile(row) for row in advisor_rows]
    profile_changes = 0
    same_profile_recommendations = 0
    previous_profile: str | None = None
    for profile in reversed(profiles):
        if previous_profile is None:
            previous_profile = profile
            continue
        if profile != previous_profile:
            profile_changes += 1
        else:
            same_profile_recommendations += 1
        previous_profile = profile

    expired_count = 0
    for row in advisor_rows:
        valid_until = _output_payload(row).get("valid_until")
        parsed = parse_utc_datetime(valid_until)
        if parsed is None:
            continue
        parsed_naive = parsed.astimezone(UTC).replace(tzinfo=None)
        if parsed_naive <= now:
            expired_count += 1

    status_counts = Counter(_advisor_status(row) for row in advisor_rows)
    ignored_count = int(status_counts.get("ignored", 0))
    reuse_signal = None
    if len(advisor_rows) >= 3 and profile_changes == 0:
        reuse_signal = "stable_profile_reuse_candidate"
    elif same_profile_recommendations > profile_changes:
        reuse_signal = "profile_repeated_more_than_changed"

    return {
        "calls": len(advisor_rows),
        "profile_counts": _counter_payload(Counter(profiles)),
        "status_counts": _counter_payload(status_counts),
        "profile_change_count": profile_changes,
        "same_profile_recommendation_count": same_profile_recommendations,
        "ignored_count": int(ignored_count),
        "expired_count": expired_count,
        "reuse_signal": reuse_signal,
        "basis": "market_settings_advisor AgentRun output profiles and metadata status.",
    }


def _role_efficiency_summary(
    session: Session,
    rows: Sequence[AgentRun],
    *,
    provider_invoked_rows: Sequence[AgentRun],
    loaded_downstream: _LoadedDownstreamRows | None = None,
) -> dict[str, Any]:
    roles = sorted({row.role for row in rows} | {row.role for row in provider_invoked_rows})
    summary: dict[str, Any] = {}
    for role in roles:
        role_rows = [row for row in rows if row.role == role]
        role_provider_rows = [row for row in provider_invoked_rows if row.role == role]
        role_skipped_rows = [row for row in role_rows if _is_preai_skipped(row)]
        known_cost, input_tokens, output_tokens, missing_usage_rows, unknown_cost_rows = _known_cost_for_rows(
            role_provider_rows
        )
        role_decision_ids = [
            int(row.id) for row in role_rows if role == "trading_decision" and row.id is not None
        ]
        downstream, downstream_by_decision = _downstream_summaries(
            session,
            role_decision_ids,
            {
                decision: [
                    int(row.id)
                    for row in role_rows
                    if row.id is not None and _output_decision(row) == decision
                ]
                for decision in AI_DECISION_VALUES
            }
            if role == "trading_decision"
            else {},
            loaded_downstream=loaded_downstream,
        )
        roi = _ai_roi_summary(
            downstream,
            known_ai_cost_usd=known_cost,
            provider_calls=len(role_provider_rows),
            cost_complete=unknown_cost_rows == 0,
            missing_usage_rows=missing_usage_rows,
        )
        role_payload = {
            "provider_calls": len(role_provider_rows),
            "skipped_preai": len(role_skipped_rows),
            "failed_calls": sum(1 for row in role_provider_rows if is_ai_failure(row)),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "known_ai_cost_usd": known_cost,
            "missing_usage_rows": missing_usage_rows,
            "unknown_cost_rows": unknown_cost_rows,
            "cost_per_provider_call_usd": _safe_rate(known_cost, len(role_provider_rows)),
            "decision_counts": _counter_payload(
                Counter(decision for row in role_rows if (decision := _output_decision(row)) is not None)
            ),
            "hold_reason_buckets": _reason_bucket_counts(role_rows, decision="hold"),
            "fail_closed_count": sum(
                1
                for row in role_rows
                if _truthy_payload_value(
                    _output_payload(row).get("fail_closed_applied"),
                    _metadata_payload(row).get("fail_closed_applied"),
                )
            ),
            "downstream": downstream,
            "downstream_by_decision": downstream_by_decision,
            "actionability": _ai_actionability_summary(
                downstream,
                provider_calls=len(role_provider_rows),
            ),
            "roi": roi,
            "net_contribution_estimate_usd": roi["net_after_known_ai_cost_usd"],
        }
        if role == "market_settings_advisor":
            role_payload["advisor"] = _advisor_efficiency(role_rows, now=utcnow_naive())
        summary[role] = role_payload
    return summary


def _preai_savings_summary(
    *,
    provider_calls: int,
    skipped_preai: int,
    soft_signal_suppressed: int,
    known_ai_cost_usd: float,
) -> dict[str, Any]:
    avg_provider_cost = _safe_rate(known_ai_cost_usd, provider_calls)
    estimated_saved_cost = (
        round(float(avg_provider_cost) * float(skipped_preai), 8)
        if avg_provider_cost is not None
        else None
    )
    return {
        "provider_calls": int(provider_calls),
        "skipped_preai": int(skipped_preai),
        "soft_signal_suppressed": int(soft_signal_suppressed),
        "preai_skip_ratio": _safe_rate(float(skipped_preai), float(provider_calls + skipped_preai)),
        "estimated_cost_saved_usd": estimated_saved_cost,
        "basis": "Estimated saved cost multiplies skipped pre-AI rows by observed average provider-call cost.",
    }


def _waste_signal_summary(
    *,
    summary_7d: Mapping[str, Any],
    summary_30d: Mapping[str, Any],
    failure_reasons: Sequence[str],
) -> dict[str, Any]:
    signals: list[dict[str, Any]] = []

    def add_signal(code: str, severity: str, message: str, evidence: Mapping[str, Any]) -> None:
        signals.append(
            {
                "code": code,
                "severity": severity,
                "message": message,
                "evidence": dict(evidence),
            }
        )

    actionability_7d = summary_7d.get("actionability") if isinstance(summary_7d.get("actionability"), dict) else {}
    roi_7d = summary_7d.get("roi") if isinstance(summary_7d.get("roi"), dict) else {}
    actionability_30d = summary_30d.get("actionability") if isinstance(summary_30d.get("actionability"), dict) else {}
    roi_30d = summary_30d.get("roi") if isinstance(summary_30d.get("roi"), dict) else {}
    provider_to_order_7d = actionability_7d.get("provider_to_order_rate")
    net_after_cost_7d = _safe_float(roi_7d.get("net_after_known_ai_cost_usd"), 0.0)
    provider_calls_7d = int(_safe_float(actionability_7d.get("provider_calls"), 0.0))
    provider_to_order_rate_7d = (
        _safe_float(provider_to_order_7d, 0.0) if provider_to_order_7d is not None else None
    )
    guard_active = (
        provider_calls_7d >= AI_TRADING_DECISION_WASTE_MIN_PROVIDER_CALLS_7D
        and provider_to_order_rate_7d is not None
        and provider_to_order_rate_7d < AI_TRADING_DECISION_WASTE_MAX_PROVIDER_TO_ORDER_RATE
        and net_after_cost_7d < 0
    )
    runtime_guard = {
        "status": "active" if guard_active else "inactive",
        "reason": AI_TRADING_DECISION_WASTE_GUARD_REASON if guard_active else None,
        "applies_to": "non_manual_trading_decision_new_entry_ai_calls",
        "backoff_minutes": AI_TRADING_DECISION_WASTE_BACKOFF_MINUTES,
        "min_provider_calls": AI_TRADING_DECISION_WASTE_MIN_PROVIDER_CALLS_7D,
        "max_provider_to_order_rate": AI_TRADING_DECISION_WASTE_MAX_PROVIDER_TO_ORDER_RATE,
        "provider_calls_7d": provider_calls_7d,
        "provider_to_order_rate_7d": provider_to_order_rate_7d,
        "net_after_known_ai_cost_usd_7d": round(net_after_cost_7d, 8),
        "fallback": AI_TRADING_DECISION_WASTE_GUARD_FALLBACK if guard_active else None,
    }
    if provider_to_order_7d is not None and float(provider_to_order_7d) < 0.01:
        add_signal(
            "LOW_7D_PROVIDER_TO_ORDER_RATE",
            "warning",
            "7일 기준 provider 호출 대비 주문 전환율이 낮습니다.",
            {"provider_to_order_rate": provider_to_order_7d},
        )
    if net_after_cost_7d < 0:
        add_signal(
            "NEGATIVE_7D_NET_AFTER_AI_COST",
            "warning",
            "7일 기준 AI 비용 반영 순효과가 음수입니다.",
            {"net_after_known_ai_cost_usd": round(net_after_cost_7d, 8)},
        )
    if (
        actionability_30d.get("provider_to_order_rate") is not None
        and float(actionability_30d["provider_to_order_rate"]) < 0.01
    ):
        add_signal(
            "LOW_30D_PROVIDER_TO_ORDER_RATE",
            "info",
            "30일 기준 provider 호출 대비 주문 전환율도 낮습니다.",
            {"provider_to_order_rate": actionability_30d["provider_to_order_rate"]},
        )
    if _safe_float(roi_30d.get("net_after_known_ai_cost_usd"), 0.0) < 0:
        add_signal(
            "NEGATIVE_30D_NET_AFTER_AI_COST",
            "info",
            "30일 기준 AI 비용 반영 순효과가 음수입니다.",
            {"net_after_known_ai_cost_usd": roi_30d.get("net_after_known_ai_cost_usd")},
        )
    if any("QUOTA" in reason or "ROLE_DAILY_TOKEN_BUDGET_EXHAUSTED" in reason for reason in failure_reasons):
        add_signal(
            "REPEATED_QUOTA_OR_BUDGET_PRESSURE",
            "warning",
            "QUOTA 또는 role token budget 관련 이력이 반복됩니다.",
            {"failure_reasons": list(failure_reasons)[:5]},
        )

    return {
        "status": "needs_review" if any(item["severity"] == "warning" for item in signals) else "ok",
        "primary_window": "7d",
        "signals": signals,
        "runtime_guard": runtime_guard,
        "note": "24시간 체결 없음은 단독 경고 조건으로 사용하지 않고, 7일/30일 전환율과 비용 순효과를 함께 봅니다.",
    }


def _cost_efficiency_rollup(
    *,
    summary_today_kst: Mapping[str, Any],
    summary_24h: Mapping[str, Any],
    summary_7d: Mapping[str, Any],
    summary_30d: Mapping[str, Any],
    summary_30d_attempts: AIWindowSummary,
    failure_reasons: Sequence[str],
) -> dict[str, Any]:
    roi_7d = summary_7d.get("roi") if isinstance(summary_7d.get("roi"), dict) else {}
    known_7d_cost = _safe_float(roi_7d.get("known_ai_cost_usd"), 0.0)
    net_7d = _safe_float(roi_7d.get("net_after_known_ai_cost_usd"), 0.0)
    monthly_cost_projection = round(known_7d_cost / 7 * 30, 8) if known_7d_cost else 0.0
    monthly_net_projection = round(net_7d / 7 * 30, 8) if net_7d else 0.0
    return {
        "primary_window": "7d",
        "secondary_window": "30d",
        "window_labels": {
            "today_kst": "KST today",
            "rolling_24h": "rolling 24h",
            "rolling_7d": "rolling 7d",
            "rolling_30d": "rolling 30d",
        },
        "monthly_projected_known_ai_cost_usd": monthly_cost_projection,
        "monthly_projected_net_after_ai_cost_usd": monthly_net_projection,
        "monthly_projected_calls_from_30d": int(summary_30d_attempts["calls"]),
        "waste_assessment": _waste_signal_summary(
            summary_7d=summary_7d,
            summary_30d=summary_30d,
            failure_reasons=failure_reasons,
        ),
        "focus_metrics": {
            "today_kst": {
                "provider_calls": summary_today_kst.get("ai_calls_provider_invoked"),
                "known_ai_cost_usd": summary_today_kst.get("known_estimated_cost_usd"),
            },
            "rolling_24h": {
                "provider_calls": summary_24h.get("ai_calls_provider_invoked"),
                "known_ai_cost_usd": summary_24h.get("known_estimated_cost_usd"),
            },
            "rolling_7d": {
                "provider_calls": summary_7d.get("ai_calls_provider_invoked"),
                "known_ai_cost_usd": summary_7d.get("known_estimated_cost_usd"),
                "provider_to_order_rate": (
                    summary_7d.get("actionability", {}).get("provider_to_order_rate")
                    if isinstance(summary_7d.get("actionability"), dict)
                    else None
                ),
                "net_after_ai_cost_usd": roi_7d.get("net_after_known_ai_cost_usd"),
            },
            "rolling_30d": {
                "provider_calls": summary_30d.get("ai_calls_provider_invoked"),
                "known_ai_cost_usd": summary_30d.get("known_estimated_cost_usd"),
                "provider_to_order_rate": (
                    summary_30d.get("actionability", {}).get("provider_to_order_rate")
                    if isinstance(summary_30d.get("actionability"), dict)
                    else None
                ),
            },
        },
    }


def _scheduler_ai_skip_reason_counts(session: Session, since: datetime) -> Counter[str]:
    postgres_counts = _postgres_scheduler_ai_skip_reason_counts(session, since)
    if postgres_counts is not None:
        return postgres_counts

    rows = list(
        session.scalars(
            select(SchedulerRun)
            .where(
                SchedulerRun.workflow == "interval_decision_cycle",
                SchedulerRun.created_at >= since,
            )
            .order_by(desc(SchedulerRun.created_at))
            .limit(AI_USAGE_DETAIL_ROW_LIMIT)
        )
    )
    counts: Counter[str] = Counter()
    for row in rows:
        reason = str(row.ai_skip_reason or "").strip()
        if not reason:
            outcome = row.outcome if isinstance(row.outcome, dict) else {}
            reason = str(outcome.get("last_ai_skip_reason") or "").strip()
            if not reason:
                policy = outcome.get("ai_call_policy") if isinstance(outcome.get("ai_call_policy"), dict) else {}
                reason = str(policy.get("reason") or "").strip()
        if reason:
            counts[_normalize_skip_reason(reason)] += 1
    return counts


def _postgres_scheduler_ai_skip_reason_counts(session: Session, since: datetime) -> Counter[str] | None:
    bind = session.get_bind()
    dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
    if dialect_name != "postgresql":
        return None
    try:
        rows = session.execute(
            text(
                """
                select
                    ai_skip_reason as reason,
                    count(*)::bigint as count
                from scheduler_runs
                where workflow = 'interval_decision_cycle'
                  and created_at >= :since
                  and ai_skip_reason is not null
                group by reason
                """
            ),
            {"since": since},
        ).mappings()
    except Exception:
        return None

    counts: Counter[str] = Counter()
    for row in rows:
        reason = str(row["reason"] or "").strip()
        if reason:
            counts[_normalize_skip_reason(reason)] += int(row["count"] or 0)
    return counts


def _postgres_scheduler_ai_skip_reason_counts_for_windows(
    session: Session,
    *,
    cutoff_today_kst: datetime,
    cutoff_24h: datetime,
    cutoff_7d: datetime,
    cutoff_30d: datetime,
) -> tuple[Counter[str], Counter[str], Counter[str], Counter[str]] | None:
    bind = session.get_bind()
    dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
    if dialect_name != "postgresql":
        return None
    try:
        rows = session.execute(
            text(
                """
                select
                    ai_skip_reason as reason,
                    count(*) filter (where created_at >= :cutoff_today_kst)::bigint as today_count,
                    count(*) filter (where created_at >= :cutoff_24h)::bigint as count_24h,
                    count(*) filter (where created_at >= :cutoff_7d)::bigint as count_7d,
                    count(*)::bigint as count_30d
                from scheduler_runs
                where workflow = 'interval_decision_cycle'
                  and created_at >= :cutoff_30d
                  and ai_skip_reason is not null
                group by ai_skip_reason
                """
            ),
            {
                "cutoff_today_kst": cutoff_today_kst,
                "cutoff_24h": cutoff_24h,
                "cutoff_7d": cutoff_7d,
                "cutoff_30d": cutoff_30d,
            },
        ).mappings()
    except Exception:
        return None

    today_counts: Counter[str] = Counter()
    counts_24h: Counter[str] = Counter()
    counts_7d: Counter[str] = Counter()
    counts_30d: Counter[str] = Counter()
    for row in rows:
        reason = _normalize_skip_reason(str(row["reason"] or "").strip())
        if not reason:
            continue
        today_counts[reason] += int(row["today_count"] or 0)
        counts_24h[reason] += int(row["count_24h"] or 0)
        counts_7d[reason] += int(row["count_7d"] or 0)
        counts_30d[reason] += int(row["count_30d"] or 0)
    return today_counts, counts_24h, counts_7d, counts_30d


def _scheduler_ai_skip_reason_counts_for_windows(
    session: Session,
    *,
    cutoff_today_kst: datetime,
    cutoff_24h: datetime,
    cutoff_7d: datetime,
    cutoff_30d: datetime,
) -> tuple[Counter[str], Counter[str], Counter[str], Counter[str]]:
    postgres_counts = _postgres_scheduler_ai_skip_reason_counts_for_windows(
        session,
        cutoff_today_kst=cutoff_today_kst,
        cutoff_24h=cutoff_24h,
        cutoff_7d=cutoff_7d,
        cutoff_30d=cutoff_30d,
    )
    if postgres_counts is not None:
        return postgres_counts
    return (
        _scheduler_ai_skip_reason_counts(session, cutoff_today_kst),
        _scheduler_ai_skip_reason_counts(session, cutoff_24h),
        _scheduler_ai_skip_reason_counts(session, cutoff_7d),
        _scheduler_ai_skip_reason_counts(session, cutoff_30d),
    )


def count_ai_deduped_events(session: Session, since: datetime) -> int:
    return int(
        session.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.event_type == AI_DEDUPED_EVENT_TYPE,
                AuditEvent.created_at >= since,
            )
        )
        or 0
    )


def _session_cache_scope(session: Session) -> str:
    bind = session.get_bind()
    url = getattr(bind, "url", None)
    return str(url) if url is not None else repr(bind)


def _agent_run_revision(session: Session, since: datetime) -> tuple[int, int]:
    max_id, row_count = session.execute(
        select(func.max(AgentRun.id), func.count(AgentRun.id)).where(
            AgentRun.created_at >= since,
            _agent_run_ai_usage_filter(),
        )
    ).one()
    return int(max_id or 0), int(row_count or 0)


def _deduped_event_revision(session: Session, since: datetime) -> tuple[int, int]:
    max_id, row_count = session.execute(
        select(func.max(AuditEvent.id), func.count(AuditEvent.id)).where(
            AuditEvent.event_type == AI_DEDUPED_EVENT_TYPE,
            AuditEvent.created_at >= since,
        )
    ).one()
    return int(max_id or 0), int(row_count or 0)


def _scheduler_run_revision(session: Session, since: datetime) -> tuple[int, int]:
    max_id, row_count = session.execute(
        select(func.max(SchedulerRun.id), func.count(SchedulerRun.id)).where(
            SchedulerRun.workflow == "interval_decision_cycle",
            SchedulerRun.created_at >= since,
        )
    ).one()
    return int(max_id or 0), int(row_count or 0)


def _kst_today_window(now: datetime) -> tuple[datetime, str, str]:
    utc_now = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    today_start = utc_now.astimezone(AI_USAGE_REPORT_TIMEZONE).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    today_end = today_start + timedelta(days=1)
    return (
        today_start.astimezone(UTC).replace(tzinfo=None),
        today_start.isoformat(),
        today_end.isoformat(),
    )


def _cache_bucket(value: datetime, *, minutes: int = 5) -> str:
    bucket_minutes = max(int(minutes), 1)
    bucket_minute = (value.minute // bucket_minutes) * bucket_minutes
    return value.replace(minute=bucket_minute, second=0, microsecond=0).isoformat()


def _ai_usage_source_revision(
    session: Session,
    *,
    cutoff_24h: datetime,
    cutoff_7d: datetime,
    cutoff_30d: datetime,
) -> tuple[object, ...]:
    return (
        *_agent_run_revision(session, cutoff_24h),
        *_agent_run_revision(session, cutoff_7d),
        *_agent_run_revision(session, cutoff_30d),
        *_deduped_event_revision(session, cutoff_24h),
        *_deduped_event_revision(session, cutoff_7d),
        *_deduped_event_revision(session, cutoff_30d),
        *_scheduler_run_revision(session, cutoff_24h),
        *_scheduler_run_revision(session, cutoff_7d),
        *_scheduler_run_revision(session, cutoff_30d),
    )


def _recent_role_runs(session: Session, role: str, *, limit: int = 25) -> list[AgentRun]:
    return list(
        session.scalars(
            select(AgentRun).where(AgentRun.role == role).order_by(desc(AgentRun.created_at)).limit(limit)
        )
    )


def _recent_ai_attempt_runs(session: Session, *, limit: int = 100) -> list[AgentRun]:
    rows = list(session.scalars(select(AgentRun).order_by(desc(AgentRun.created_at)).limit(limit)))
    return [row for row in rows if is_ai_attempt(row)]


def _retry_after_window_seconds(rows: Sequence[AgentRun], *, now: datetime, window: timedelta) -> int:
    if not rows:
        return 0
    oldest = min(row.created_at for row in rows)
    retry_at = oldest + window
    return max(int((retry_at - now).total_seconds()), 1) if retry_at > now else 0


def _coerce_daily_token_budget(value: object, default: int | None) -> int | None:
    if default is None:
        return None
    try:
        budget = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return budget if budget >= 10_000 else default


def _role_daily_token_budget(settings_row: Setting | None, role: str) -> int | None:
    default = AI_ROLE_DAILY_TOKEN_BUDGETS.get(role)
    if role != "trading_decision" or settings_row is None:
        return default
    return _coerce_daily_token_budget(
        getattr(settings_row, "ai_trading_decision_daily_token_budget", None),
        default,
    )


def _role_budget_status(
    rows: Sequence[AgentRun],
    role: str,
    *,
    now: datetime,
    daily_token_limit: int | None = None,
) -> dict[str, Any]:
    attempts = [row for row in rows if row.role == role and is_ai_attempt(row)]
    hourly_limit = AI_ROLE_HOURLY_CALL_BUDGETS.get(role)
    if daily_token_limit is None:
        daily_token_limit = AI_ROLE_DAILY_TOKEN_BUDGETS.get(role)
    consecutive_failure_limit = AI_ROLE_CONSECUTIVE_FAILURE_BUDGETS.get(role)

    one_hour_rows = [row for row in attempts if row.created_at >= now - timedelta(hours=1)]
    daily_rows = [row for row in attempts if row.created_at >= now - timedelta(hours=24)]
    tokens_24h = sum(_metadata_usage(row)["total_tokens"] for row in daily_rows)
    consecutive_failures = 0
    for row in attempts:
        if is_ai_success(row):
            break
        if is_ai_failure(row):
            consecutive_failures += 1
            continue
        break

    status = "ok"
    reason = "within_budget"
    retry_after_seconds = 0
    if hourly_limit is not None and len(one_hour_rows) >= hourly_limit:
        status = "blocked"
        reason = "role_hourly_call_budget_exhausted"
        retry_after_seconds = _retry_after_window_seconds(
            one_hour_rows,
            now=now,
            window=timedelta(hours=1),
        )
    if daily_token_limit is not None and tokens_24h >= daily_token_limit:
        status = "blocked"
        reason = "role_daily_token_budget_exhausted"
        retry_after_seconds = max(
            retry_after_seconds,
            _retry_after_window_seconds(daily_rows, now=now, window=timedelta(hours=24)),
        )
    if (
        consecutive_failure_limit is not None
        and consecutive_failures >= consecutive_failure_limit
        and status != "blocked"
    ):
        status = "limited"
        reason = "role_consecutive_failure_budget_reached"

    return {
        "role": role,
        "status": status,
        "reason": reason,
        "calls_1h": len(one_hour_rows),
        "max_calls_1h": hourly_limit,
        "tokens_24h": int(tokens_24h),
        "max_tokens_24h": daily_token_limit,
        "consecutive_failures": consecutive_failures,
        "max_consecutive_failures": consecutive_failure_limit,
        "retry_after_seconds": retry_after_seconds,
    }


def _advisor_runtime_status(rows: Sequence[AgentRun], *, now: datetime) -> dict[str, Any]:
    latest = next((row for row in rows if row.role == "market_settings_advisor"), None)
    if latest is None:
        return {
            "status": "never_run",
            "last_run_at": None,
            "next_due_at": None,
            "retry_after_seconds": 0,
        }
    metadata = _metadata_payload(latest)
    cost_estimate = _estimate_row_cost(latest)
    settings_policy = metadata.get("settings_policy") if isinstance(metadata.get("settings_policy"), dict) else {}
    observed_risk_flags = [
        str(item)
        for item in metadata.get("observed_risk_flags", [])
        if str(item or "").strip()
    ] if isinstance(metadata.get("observed_risk_flags"), list) else []
    try:
        interval_seconds = int(
            settings_policy.get(
                "elevated_interval_seconds" if observed_risk_flags else "normal_interval_seconds",
                0,
            )
            or 0
        )
    except (TypeError, ValueError):
        interval_seconds = 0
    try:
        min_recheck_seconds = int(settings_policy.get("min_recheck_interval_seconds", 0) or 0)
    except (TypeError, ValueError):
        min_recheck_seconds = 0
    due_seconds = max(interval_seconds, min_recheck_seconds)
    next_due_at = latest.created_at + timedelta(seconds=due_seconds) if due_seconds > 0 else None
    retry_after_seconds = (
        max(int((next_due_at - now).total_seconds()), 1)
        if next_due_at is not None and next_due_at > now
        else 0
    )
    return {
        "status": str(metadata.get("status") or latest.status or "unknown"),
        "last_run_at": latest.created_at.isoformat(),
        "next_due_at": next_due_at.isoformat() if next_due_at is not None else None,
        "retry_after_seconds": retry_after_seconds,
        "symbol_scope": metadata.get("symbol_scope") if isinstance(metadata.get("symbol_scope"), list) else [],
        "representative_symbol": metadata.get("representative_symbol") or metadata.get("symbol"),
        "observed_risk_flags": observed_risk_flags,
        "source": metadata.get("source"),
        "cost_estimate_status": metadata.get("cost_estimate_status") or cost_estimate.status,
        "estimated_cost_usd": (
            metadata.get("estimated_cost_usd")
            if metadata.get("estimated_cost_usd") is not None
            else cost_estimate.estimated_cost_usd
        ),
        "cost_estimate_model": metadata.get("cost_estimate_model") or cost_estimate.model,
        "cost_estimate_model_source": metadata.get("cost_estimate_model_source") or cost_estimate.model_source,
    }


def build_ai_protection_status(
    rows: Sequence[AgentRun],
    *,
    now: datetime,
    settings_row: Setting | None = None,
) -> dict[str, Any]:
    roles = sorted(
        {
            *AI_ROLE_HOURLY_CALL_BUDGETS.keys(),
            *AI_ROLE_DAILY_TOKEN_BUDGETS.keys(),
            *AI_ROLE_CONSECUTIVE_FAILURE_BUDGETS.keys(),
        }
    )
    return {
        "policy": {
            "quota_errors": "global_backoff",
            "auth_errors": "global_backoff",
            "rate_limit_errors": "role_backoff",
        },
        "role_budgets": {
            role: _role_budget_status(
                rows,
                role,
                now=now,
                daily_token_limit=_role_daily_token_budget(settings_row, role),
            )
            for role in roles
        },
        "advisor": _advisor_runtime_status(rows, now=now),
    }


def _agent_run_symbol(row: AgentRun) -> str | None:
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    if isinstance(metadata.get("symbol"), str) and metadata.get("symbol"):
        return str(metadata["symbol"]).upper()
    input_payload = row.input_payload if isinstance(row.input_payload, dict) else {}
    market_snapshot = input_payload.get("market_snapshot")
    if isinstance(market_snapshot, dict) and isinstance(market_snapshot.get("symbol"), str):
        return str(market_snapshot["symbol"]).upper()
    output_payload = row.output_payload if isinstance(row.output_payload, dict) else {}
    if isinstance(output_payload.get("symbol"), str) and output_payload.get("symbol"):
        return str(output_payload["symbol"]).upper()
    return None


def _trading_decision_waste_guard(
    session: Session,
    *,
    now: datetime,
) -> OpenAICallGate | None:
    since = now - timedelta(days=7)
    rows = list(
        session.scalars(
            select(AgentRun)
            .options(
                load_only(
                    AgentRun.id,
                    AgentRun.role,
                    AgentRun.status,
                    AgentRun.provider_name,
                    AgentRun.metadata_json,
                    AgentRun.created_at,
                )
            )
            .where(
                AgentRun.role == "trading_decision",
                AgentRun.created_at >= since,
                _agent_run_ai_usage_filter(),
            )
            .order_by(desc(AgentRun.created_at))
            .limit(AI_USAGE_DETAIL_ROW_LIMIT)
        )
    )
    provider_rows = [row for row in rows if is_ai_attempt(row)]
    provider_calls = len(provider_rows)
    if provider_calls < AI_TRADING_DECISION_WASTE_MIN_PROVIDER_CALLS_7D:
        return None

    decision_ids = [int(row.id) for row in provider_rows if row.id is not None]
    if not decision_ids:
        return None
    order_rows = list(
        session.scalars(
            select(Order)
            .options(load_only(Order.id, Order.decision_run_id))
            .where(Order.decision_run_id.in_(decision_ids))
        )
    )
    order_ids = [int(row.id) for row in order_rows if row.id is not None]
    execution_rows = (
        list(
            session.scalars(
                select(Execution)
                .options(
                    load_only(
                        Execution.id,
                        Execution.order_id,
                        Execution.realized_pnl,
                        Execution.fee_paid,
                    )
                )
                .where(Execution.order_id.in_(order_ids))
            )
        )
        if order_ids
        else []
    )
    known_cost, _input_tokens, _output_tokens, missing_usage_rows, unknown_cost_rows = _known_cost_for_rows(
        provider_rows
    )
    trade_net = round(
        sum(_safe_float(row.realized_pnl) - _safe_float(row.fee_paid) for row in execution_rows),
        8,
    )
    net_after_cost = round(trade_net - known_cost, 8)
    provider_to_order_rate = _safe_rate(float(len(order_rows)), float(provider_calls)) or 0.0
    if (
        provider_to_order_rate >= AI_TRADING_DECISION_WASTE_MAX_PROVIDER_TO_ORDER_RATE
        or net_after_cost >= 0
    ):
        return None

    return OpenAICallGate(
        allowed=False,
        reason=AI_TRADING_DECISION_WASTE_GUARD_REASON,
        retry_after_seconds=AI_TRADING_DECISION_WASTE_BACKOFF_MINUTES * 60,
        backoff_minutes=AI_TRADING_DECISION_WASTE_BACKOFF_MINUTES,
        evidence={
            "window": "7d",
            "provider_calls": provider_calls,
            "orders": len(order_rows),
            "fills": len(execution_rows),
            "provider_to_order_rate": provider_to_order_rate,
            "known_ai_cost_usd": known_cost,
            "trade_net_realized_pnl_usd": trade_net,
            "net_after_known_ai_cost_usd": net_after_cost,
            "missing_usage_rows": missing_usage_rows,
            "unknown_cost_rows": unknown_cost_rows,
            "min_provider_calls": AI_TRADING_DECISION_WASTE_MIN_PROVIDER_CALLS_7D,
            "max_provider_to_order_rate": AI_TRADING_DECISION_WASTE_MAX_PROVIDER_TO_ORDER_RATE,
            "fallback": AI_TRADING_DECISION_WASTE_GUARD_FALLBACK,
        },
    )


def get_openai_call_gate(
    session: Session,
    settings_row: Setting,
    role: str,
    trigger_event: str,
    *,
    has_openai_key: bool,
    symbol: str | None = None,
    cooldown_minutes_override: int | None = None,
    manual_guard_minutes_override: int | None = None,
    enforce_waste_guard: bool | None = None,
) -> OpenAICallGate:
    if not settings_row.ai_enabled:
        return OpenAICallGate(allowed=False, reason="ai_disabled")
    if settings_row.ai_provider != "openai":
        return OpenAICallGate(allowed=False, reason="provider_not_openai")
    if not has_openai_key:
        return OpenAICallGate(allowed=False, reason="missing_api_key")
    if trigger_event == "historical_replay":
        return OpenAICallGate(allowed=False, reason="historical_replay_disabled")

    now = utcnow_naive()
    for row in _recent_ai_attempt_runs(session, limit=100):
        error = _metadata_error(row)
        reason = classify_ai_failure(error)
        if reason not in {"AUTH", "QUOTA"}:
            continue
        backoff = failure_backoff_minutes(settings_row, error)
        retry_at = row.created_at + timedelta(minutes=backoff)
        if retry_at > now:
            return OpenAICallGate(
                allowed=False,
                reason="global_failure_backoff_active",
                retry_after_seconds=max(int((retry_at - now).total_seconds()), 1),
                backoff_minutes=backoff,
                last_attempt_at=row.created_at,
                failure_reason=reason,
            )
        break

    recent_runs = _recent_role_runs(session, role, limit=500)
    role_budget = _role_budget_status(
        recent_runs,
        role,
        now=now,
        daily_token_limit=_role_daily_token_budget(settings_row, role),
    )
    if role_budget["status"] == "blocked":
        return OpenAICallGate(
            allowed=False,
            reason=str(role_budget["reason"]),
            retry_after_seconds=int(role_budget["retry_after_seconds"] or 0),
        )
    effective_enforce_waste_guard = (
        enforce_waste_guard
        if enforce_waste_guard is not None
        else role == "trading_decision" and trigger_event != "manual"
    )
    if effective_enforce_waste_guard and role == "trading_decision" and trigger_event != "manual":
        waste_gate = _trading_decision_waste_guard(session, now=now)
        if waste_gate is not None:
            return waste_gate
    recent_runs = recent_runs[:100 if symbol else 25]
    if symbol is not None:
        symbol_upper = symbol.upper()
        recent_runs = [row for row in recent_runs if _agent_run_symbol(row) == symbol_upper]
    latest_attempt = next((row for row in recent_runs if is_ai_attempt(row)), None)
    latest_success = next((row for row in recent_runs if is_ai_success(row)), None)

    if latest_attempt is not None and is_ai_failure(latest_attempt):
        error = _metadata_error(latest_attempt)
        backoff = failure_backoff_minutes(settings_row, error)
        retry_at = latest_attempt.created_at + timedelta(minutes=backoff)
        if retry_at > now:
            return OpenAICallGate(
                allowed=False,
                reason="failure_backoff_active",
                retry_after_seconds=max(int((retry_at - now).total_seconds()), 1),
                backoff_minutes=backoff,
                last_attempt_at=latest_attempt.created_at,
                failure_reason=classify_ai_failure(error),
            )

    if role != "trading_decision":
        return OpenAICallGate(allowed=True, reason="allowed")

    manual_guard = (
        manual_guard_minutes_override
        if manual_guard_minutes_override is not None
        else manual_ai_guard_minutes(settings_row)
    )
    if trigger_event == "manual":
        if latest_attempt is not None:
            retry_at = latest_attempt.created_at + timedelta(minutes=manual_guard)
            if retry_at > now:
                return OpenAICallGate(
                    allowed=False,
                    reason="manual_guard_active",
                    retry_after_seconds=max(int((retry_at - now).total_seconds()), 1),
                    manual_guard_minutes=manual_guard,
                    last_attempt_at=latest_attempt.created_at,
                )
        return OpenAICallGate(allowed=True, reason="allowed", manual_guard_minutes=manual_guard)

    cooldown_minutes = (
        cooldown_minutes_override
        if cooldown_minutes_override is not None
        else settings_row.ai_call_interval_minutes
    )
    cooldown_cutoff = now - timedelta(minutes=cooldown_minutes)
    if latest_success is not None and latest_success.created_at > cooldown_cutoff:
        retry_at = latest_success.created_at + timedelta(minutes=cooldown_minutes)
        return OpenAICallGate(
            allowed=False,
            reason="success_cooldown_active",
            retry_after_seconds=max(int((retry_at - now).total_seconds()), 1),
            last_attempt_at=latest_success.created_at,
        )

    return OpenAICallGate(allowed=True, reason="allowed", manual_guard_minutes=manual_guard)


def build_ai_telemetry_summary(
    session: Session,
    rows: Sequence[AgentRun],
    *,
    deduped_count: int = 0,
    scheduler_skip_reasons: Mapping[str, int] | None = None,
    loaded_downstream: _LoadedDownstreamRows | None = None,
) -> dict[str, Any]:
    decision_rows = [row for row in rows if row.role == "trading_decision"]
    trading_provider_invoked_rows = [row for row in decision_rows if is_ai_attempt(row)]
    provider_invoked_rows = [row for row in rows if is_ai_attempt(row)]
    failed_rows = [row for row in provider_invoked_rows if is_ai_failure(row)]
    skipped_rows = [row for row in decision_rows if _is_preai_skipped(row)]
    telemetry_row_ids = {
        row.id
        for row in [*trading_provider_invoked_rows, *skipped_rows]
        if row.id is not None
    }
    telemetry_rows = [row for row in decision_rows if row.id in telemetry_row_ids]

    decision_counts = {decision: 0 for decision in AI_DECISION_VALUES}
    decision_ids_by_decision: dict[str, list[int]] = {decision: [] for decision in AI_DECISION_VALUES}
    source_counts: Counter[str] = Counter()
    preai_skip_reasons: Counter[str] = Counter()
    scheduler_skip_reason_counts = Counter(
        {str(key): int(value) for key, value in (scheduler_skip_reasons or {}).items()}
    )
    should_abstain = 0
    fail_closed = 0
    input_tokens = 0
    output_tokens = 0
    estimated_cost_total = 0.0
    unknown_cost_rows = 0
    missing_usage_rows = 0

    for row in telemetry_rows:
        decision = _output_decision(row)
        if decision is not None:
            decision_counts[decision] += 1
            if row.id is not None:
                decision_ids_by_decision[decision].append(int(row.id))
        metadata = _metadata_payload(row)
        output = _output_payload(row)
        source_counts[str(metadata.get("source") or row.provider_name or "unknown")] += 1
        if _truthy_payload_value(output.get("should_abstain"), metadata.get("should_abstain")):
            should_abstain += 1
        if _truthy_payload_value(output.get("fail_closed_applied"), metadata.get("fail_closed_applied")):
            fail_closed += 1

    for row in skipped_rows:
        labels = _skip_reason_labels(row) or ["UNKNOWN"]
        preai_skip_reasons.update(labels)
    preai_skip_reasons.update(scheduler_skip_reason_counts)

    for row in provider_invoked_rows:
        cost = _estimate_row_cost(row)
        input_tokens += cost.input_tokens
        output_tokens += cost.output_tokens
        if cost.status == "missing_usage":
            missing_usage_rows += 1
            unknown_cost_rows += 1
        elif cost.estimated_cost_usd is None:
            unknown_cost_rows += 1
        else:
            estimated_cost_total += cost.estimated_cost_usd

    if not provider_invoked_rows:
        estimated_cost_usd: float | None = 0.0
        cost_estimate_status = "no_provider_calls"
    elif unknown_cost_rows:
        estimated_cost_usd = None
        cost_estimate_status = "partial_unknown"
    else:
        estimated_cost_usd = round(estimated_cost_total, 8)
        cost_estimate_status = "estimated"

    known_estimated_cost_usd = round(estimated_cost_total, 8)
    scheduler_skipped_count = sum(scheduler_skip_reason_counts.values())
    soft_signal_suppressed_count = int(
        preai_skip_reasons.get(SOFT_SIGNAL_REVIEW_SUPPRESSED_SKIP_REASON, 0)
    )
    downstream, downstream_by_decision = _downstream_summaries(
        session,
        sorted(telemetry_row_ids),
        decision_ids_by_decision,
        loaded_downstream=loaded_downstream,
    )
    provider_downstream, _ = _downstream_summaries(
        session,
        sorted(int(row.id) for row in trading_provider_invoked_rows if row.id is not None),
        {},
        loaded_downstream=loaded_downstream,
    )
    role_efficiency = _role_efficiency_summary(
        session,
        rows,
        provider_invoked_rows=provider_invoked_rows,
        loaded_downstream=loaded_downstream,
    )

    return {
        "ai_calls_total": len(provider_invoked_rows) + len(skipped_rows) + scheduler_skipped_count + int(deduped_count),
        "ai_calls_provider_invoked": len(provider_invoked_rows),
        "ai_calls_skipped_preai": len(skipped_rows) + scheduler_skipped_count,
        "ai_calls_scheduler_skipped": scheduler_skipped_count,
        "ai_calls_suppressed_soft_signal": soft_signal_suppressed_count,
        "ai_calls_deduped": int(deduped_count),
        "ai_calls_failed": len(failed_rows),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost_usd": estimated_cost_usd,
        "known_estimated_cost_usd": known_estimated_cost_usd,
        "cost_estimate_status": cost_estimate_status,
        "missing_usage_rows": missing_usage_rows,
        "unknown_cost_rows": unknown_cost_rows,
        "decision_counts": decision_counts,
        "source_counts": {key: int(value) for key, value in sorted(source_counts.items())},
        "should_abstain": should_abstain,
        "fail_closed": fail_closed,
        "preai_skip_reasons": {key: int(value) for key, value in sorted(preai_skip_reasons.items())},
        "scheduler_skip_reasons": {
            key: int(value) for key, value in sorted(scheduler_skip_reason_counts.items())
        },
        "downstream": downstream,
        "downstream_by_decision": downstream_by_decision,
        "provider_downstream": provider_downstream,
        "role_efficiency": role_efficiency,
        "reason_buckets": {
            "hold": _reason_bucket_counts(telemetry_rows, decision="hold"),
            "fail_closed": _reason_bucket_counts(
                [
                    row
                    for row in telemetry_rows
                    if _truthy_payload_value(
                        _output_payload(row).get("fail_closed_applied"),
                        _metadata_payload(row).get("fail_closed_applied"),
                    )
                ]
            ),
            "preai_skip": _counter_payload(preai_skip_reasons, limit=12),
            "risk": downstream.get("risk_reason_counts", {}),
        },
        "actionability": _ai_actionability_summary(
            provider_downstream,
            provider_calls=len(trading_provider_invoked_rows),
        ),
        "roi": _ai_roi_summary(
            downstream,
            known_ai_cost_usd=known_estimated_cost_usd,
            provider_calls=len(provider_invoked_rows),
            cost_complete=unknown_cost_rows == 0,
            missing_usage_rows=missing_usage_rows,
        ),
        "preai_savings": _preai_savings_summary(
            provider_calls=len(provider_invoked_rows),
            skipped_preai=len(skipped_rows) + scheduler_skipped_count,
            soft_signal_suppressed=soft_signal_suppressed_count,
            known_ai_cost_usd=known_estimated_cost_usd,
        ),
        "cost_basis": {
            "rate_unit": "usd_per_1m_tokens",
            "rates": AI_COST_RATES_USD_PER_1M_TOKENS,
            "note": (
                "Uses prompt_tokens as input and completion_tokens as output; cached-token discounts are not assumed. "
                "Failed provider rows may not include usage, so known_estimated_cost_usd can understate billed cost."
            ),
        },
    }


def _summarize_attempt_rows(rows: list[AgentRun]) -> AIWindowSummary:
    attempts = [row for row in rows if is_ai_attempt(row)]
    successes = [row for row in attempts if is_ai_success(row)]
    failures = [row for row in attempts if is_ai_failure(row)]
    tokens = Counter({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
    role_calls: Counter[str] = Counter()
    role_failures: Counter[str] = Counter()
    failure_reasons: Counter[str] = Counter()

    for row in attempts:
        role_calls[row.role] += 1
        tokens.update(_metadata_usage(row))

    for row in failures:
        role_failures[row.role] += 1
        failure_reasons[classify_ai_failure(_metadata_error(row)) or "UNKNOWN"] += 1

    return {
        "calls": len(attempts),
        "successes": len(successes),
        "failures": len(failures),
        "tokens": {
            "prompt_tokens": int(tokens["prompt_tokens"]),
            "completion_tokens": int(tokens["completion_tokens"]),
            "total_tokens": int(tokens["total_tokens"]),
        },
        "role_calls": {key: int(value) for key, value in sorted(role_calls.items())},
        "role_failures": {key: int(value) for key, value in sorted(role_failures.items())},
        "failure_reasons": [
            f"{reason} x{count}" for reason, count in failure_reasons.most_common(5) if count > 0
        ],
    }


def _postgres_ai_attempt_summary(session: Session, since: datetime) -> AIWindowSummary | None:
    bind = session.get_bind()
    dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
    if dialect_name != "postgresql":
        return None
    try:
        summary = session.execute(
            text(
                """
                with attempts as (
                    select
                        role,
                        provider_name,
                        coalesce(metadata_json->>'source', '') as source,
                        coalesce(metadata_json->>'error', '') as error,
                        case
                            when coalesce(metadata_json->'usage'->>'prompt_tokens', '') ~ '^[0-9]+$'
                            then (metadata_json->'usage'->>'prompt_tokens')::bigint
                            else 0
                        end as prompt_tokens,
                        case
                            when coalesce(metadata_json->'usage'->>'completion_tokens', '') ~ '^[0-9]+$'
                            then (metadata_json->'usage'->>'completion_tokens')::bigint
                            else 0
                        end as completion_tokens,
                        case
                            when coalesce(metadata_json->'usage'->>'total_tokens', '') ~ '^[0-9]+$'
                            then (metadata_json->'usage'->>'total_tokens')::bigint
                            else 0
                        end as total_tokens
                    from agent_runs
                    where created_at >= :since
                      and (
                        provider_name = 'openai'
                        or coalesce(metadata_json->>'source', '') in ('llm', 'llm_fallback')
                      )
                )
                select
                    count(*)::bigint as calls,
                    count(*) filter (where provider_name = 'openai' and source = 'llm')::bigint as successes,
                    count(*) filter (where source = 'llm_fallback' or error <> '')::bigint as failures,
                    coalesce(sum(prompt_tokens), 0)::bigint as prompt_tokens,
                    coalesce(sum(completion_tokens), 0)::bigint as completion_tokens,
                    coalesce(sum(total_tokens), 0)::bigint as total_tokens
                from attempts
                """
            ),
            {"since": since},
        ).mappings().one()
        role_calls = {
            str(row["role"]): int(row["count"] or 0)
            for row in session.execute(
                text(
                    """
                    select role, count(*)::bigint as count
                    from agent_runs
                    where created_at >= :since
                      and (
                        provider_name = 'openai'
                        or coalesce(metadata_json->>'source', '') in ('llm', 'llm_fallback')
                      )
                    group by role
                    order by role
                    """
                ),
                {"since": since},
            ).mappings()
        }
        role_failures = {
            str(row["role"]): int(row["count"] or 0)
            for row in session.execute(
                text(
                    """
                    select role, count(*)::bigint as count
                    from agent_runs
                    where created_at >= :since
                      and (
                        provider_name = 'openai'
                        or coalesce(metadata_json->>'source', '') in ('llm', 'llm_fallback')
                      )
                      and (
                        coalesce(metadata_json->>'source', '') = 'llm_fallback'
                        or coalesce(metadata_json->>'error', '') <> ''
                      )
                    group by role
                    order by role
                    """
                ),
                {"since": since},
            ).mappings()
        }
        failure_reasons = Counter()
        for row in session.execute(
            text(
                """
                select coalesce(metadata_json->>'error', '') as error
                from agent_runs
                where created_at >= :since
                  and (
                    provider_name = 'openai'
                    or coalesce(metadata_json->>'source', '') in ('llm', 'llm_fallback')
                  )
                  and (
                    coalesce(metadata_json->>'source', '') = 'llm_fallback'
                    or coalesce(metadata_json->>'error', '') <> ''
                  )
                order by created_at desc
                limit 100
                """
            ),
            {"since": since},
        ).mappings():
            failure_reasons[classify_ai_failure(row["error"]) or "UNKNOWN"] += 1
    except Exception:
        return None

    return {
        "calls": int(summary["calls"] or 0),
        "successes": int(summary["successes"] or 0),
        "failures": int(summary["failures"] or 0),
        "tokens": {
            "prompt_tokens": int(summary["prompt_tokens"] or 0),
            "completion_tokens": int(summary["completion_tokens"] or 0),
            "total_tokens": int(summary["total_tokens"] or 0),
        },
        "role_calls": role_calls,
        "role_failures": role_failures,
        "failure_reasons": [
            f"{reason} x{count}" for reason, count in failure_reasons.most_common(5) if count > 0
        ],
    }


def _summarize_attempt_window(
    session: Session,
    rows: list[AgentRun],
    since: datetime,
    *,
    rows_complete: bool = False,
) -> AIWindowSummary:
    if rows_complete:
        return _summarize_attempt_rows(rows)
    return _postgres_ai_attempt_summary(session, since) or _summarize_attempt_rows(rows)


def _load_ai_usage_rows(
    session: Session,
    *,
    cutoff_30d: datetime,
    limit: int,
    include_output_payload: bool = True,
) -> list[AgentRun]:
    columns = [
        AgentRun.id,
        AgentRun.role,
        AgentRun.status,
        AgentRun.provider_name,
        AgentRun.metadata_json,
        AgentRun.created_at,
    ]
    if include_output_payload:
        columns.append(AgentRun.output_payload)
    return list(
        session.scalars(
            select(AgentRun)
            .options(
                load_only(*columns)
            )
            .where(
                AgentRun.created_at >= cutoff_30d,
                _agent_run_ai_usage_filter(),
            )
            .order_by(desc(AgentRun.created_at))
            .limit(limit)
        )
    )


def build_ai_usage_metrics(session: Session, *, allow_stale: bool = True) -> AIUsageMetrics:
    now = utcnow_naive()
    cutoff_30d = now - timedelta(days=30)
    cutoff_7d = now - timedelta(days=7)
    cutoff_24h = now - timedelta(hours=24)
    cutoff_today_kst, today_kst_start_at, today_kst_end_at = _kst_today_window(now)
    settings_row = session.scalar(select(Setting).limit(1))
    trading_decision_daily_token_budget = _role_daily_token_budget(settings_row, "trading_decision")
    cutoff_today_kst_bucket = cutoff_today_kst.replace(second=0, microsecond=0).isoformat()
    cutoff_24h_bucket = _cache_bucket(cutoff_24h)
    cutoff_7d_bucket = _cache_bucket(cutoff_7d)
    cutoff_30d_bucket = _cache_bucket(cutoff_30d)
    cache_key = (
        _session_cache_scope(session),
        cutoff_today_kst_bucket,
        cutoff_24h_bucket,
        cutoff_7d_bucket,
        cutoff_30d_bucket,
        trading_decision_daily_token_budget,
    )
    monotonic_now = monotonic()
    with _AI_USAGE_METRICS_CACHE_LOCK:
        cached = _AI_USAGE_METRICS_CACHE.get(cache_key)
    if cached is not None and monotonic_now - cached[0] <= AI_USAGE_CACHE_TTL_SECONDS:
        return _copy_cached_ai_usage_metrics(cached)
    if allow_stale:
        stale_cached = cached or _find_stale_ai_usage_metrics_cache(
            cache_key,
            monotonic_now=monotonic_now,
        )
        if (
            stale_cached is not None
            and monotonic_now - stale_cached[0] <= AI_USAGE_STALE_CACHE_TTL_SECONDS
        ):
            _start_ai_usage_metrics_refresh(session, cache_key)
            return _copy_cached_ai_usage_metrics(stale_cached)

    source_revision = (
        *_ai_usage_source_revision(
            session,
            cutoff_24h=cutoff_24h,
            cutoff_7d=cutoff_7d,
            cutoff_30d=cutoff_30d,
        ),
        trading_decision_daily_token_budget,
    )
    if cached is not None and cached[1] == source_revision:
        with _AI_USAGE_METRICS_CACHE_LOCK:
            _AI_USAGE_METRICS_CACHE[cache_key] = (monotonic_now, cached[1], deepcopy(cached[2]))
        return _copy_cached_ai_usage_metrics(cached)

    rows_7d = _load_ai_usage_rows(
        session,
        cutoff_30d=cutoff_7d,
        limit=AI_USAGE_DETAIL_ROW_LIMIT,
        include_output_payload=True,
    )
    rows_30d = _load_ai_usage_rows(
        session,
        cutoff_30d=cutoff_30d,
        limit=AI_USAGE_DETAIL_ROW_LIMIT,
        include_output_payload=False,
    )
    rows_today_kst = [row for row in rows_7d if row.created_at >= cutoff_today_kst]
    rows_24h = [row for row in rows_7d if row.created_at >= cutoff_24h]
    oldest_loaded_at = rows_30d[-1].created_at if rows_30d else None
    downstream_rows_30d = _load_downstream_rows(
        session,
        [int(row.id) for row in rows_30d if row.role == "trading_decision" and row.id is not None],
    )

    def loaded_rows_cover(cutoff: datetime) -> bool:
        return len(rows_30d) < AI_USAGE_DETAIL_ROW_LIMIT or (
            oldest_loaded_at is not None and oldest_loaded_at <= cutoff
        )

    summary_today_kst = _summarize_attempt_window(
        session,
        rows_today_kst,
        cutoff_today_kst,
        rows_complete=loaded_rows_cover(cutoff_today_kst),
    )
    summary_24h = _summarize_attempt_window(
        session,
        rows_24h,
        cutoff_24h,
        rows_complete=loaded_rows_cover(cutoff_24h),
    )
    summary_7d = _summarize_attempt_window(
        session,
        rows_7d,
        cutoff_7d,
        rows_complete=loaded_rows_cover(cutoff_7d),
    )
    summary_30d = _summarize_attempt_window(
        session,
        rows_30d,
        cutoff_30d,
        rows_complete=loaded_rows_cover(cutoff_30d),
    )
    (
        scheduler_skip_reasons_today_kst,
        scheduler_skip_reasons_24h,
        scheduler_skip_reasons_7d,
        scheduler_skip_reasons_30d,
    ) = (
        _scheduler_ai_skip_reason_counts_for_windows(
            session,
            cutoff_today_kst=cutoff_today_kst,
            cutoff_24h=cutoff_24h,
            cutoff_7d=cutoff_7d,
            cutoff_30d=cutoff_30d,
        )
    )
    ai_usage_summary_today_kst = build_ai_telemetry_summary(
        session,
        rows_today_kst,
        deduped_count=count_ai_deduped_events(session, cutoff_today_kst),
        scheduler_skip_reasons=scheduler_skip_reasons_today_kst,
        loaded_downstream=downstream_rows_30d,
    )
    ai_usage_summary_24h = build_ai_telemetry_summary(
        session,
        rows_24h,
        deduped_count=count_ai_deduped_events(session, cutoff_24h),
        scheduler_skip_reasons=scheduler_skip_reasons_24h,
        loaded_downstream=downstream_rows_30d,
    )
    ai_usage_summary_7d = build_ai_telemetry_summary(
        session,
        rows_7d,
        deduped_count=count_ai_deduped_events(session, cutoff_7d),
        scheduler_skip_reasons=scheduler_skip_reasons_7d,
        loaded_downstream=downstream_rows_30d,
    )
    ai_usage_summary_30d = build_ai_telemetry_summary(
        session,
        rows_30d,
        deduped_count=count_ai_deduped_events(session, cutoff_30d),
        scheduler_skip_reasons=scheduler_skip_reasons_30d,
        loaded_downstream=downstream_rows_30d,
    )
    ai_protection_status = build_ai_protection_status(rows_7d, now=now, settings_row=settings_row)

    if summary_30d["calls"] > 0:
        projected_total = int(summary_30d["calls"])
        projected_breakdown = dict(summary_30d["role_calls"])
    elif summary_7d["calls"] > 0:
        projected_total = int(round(summary_7d["calls"] / 7 * 30))
        projected_breakdown = {
            role: int(round(count / 7 * 30))
            for role, count in summary_7d["role_calls"].items()
        }
    elif summary_24h["calls"] > 0:
        projected_total = int(summary_24h["calls"] * 30)
        projected_breakdown = {
            role: int(count * 30) for role, count in summary_24h["role_calls"].items()
        }
    else:
        projected_total = 0
        projected_breakdown = {}
    ai_cost_efficiency_summary = _cost_efficiency_rollup(
        summary_today_kst=ai_usage_summary_today_kst,
        summary_24h=ai_usage_summary_24h,
        summary_7d=ai_usage_summary_7d,
        summary_30d=ai_usage_summary_30d,
        summary_30d_attempts=summary_30d,
        failure_reasons=summary_30d["failure_reasons"],
    )

    metrics: AIUsageMetrics = {
        "recent_ai_calls_today_kst": int(summary_today_kst["calls"]),
        "recent_ai_calls_24h": int(summary_24h["calls"]),
        "recent_ai_calls_7d": int(summary_7d["calls"]),
        "recent_ai_calls_30d": int(summary_30d["calls"]),
        "recent_ai_successes_today_kst": int(summary_today_kst["successes"]),
        "recent_ai_successes_24h": int(summary_24h["successes"]),
        "recent_ai_successes_7d": int(summary_7d["successes"]),
        "recent_ai_successes_30d": int(summary_30d["successes"]),
        "recent_ai_failures_today_kst": int(summary_today_kst["failures"]),
        "recent_ai_failures_24h": int(summary_24h["failures"]),
        "recent_ai_failures_7d": int(summary_7d["failures"]),
        "recent_ai_failures_30d": int(summary_30d["failures"]),
        "recent_ai_tokens_today_kst": summary_today_kst["tokens"],
        "recent_ai_tokens_24h": summary_24h["tokens"],
        "recent_ai_tokens_7d": summary_7d["tokens"],
        "recent_ai_tokens_30d": summary_30d["tokens"],
        "recent_ai_role_calls_today_kst": summary_today_kst["role_calls"],
        "recent_ai_role_calls_24h": summary_24h["role_calls"],
        "recent_ai_role_calls_7d": summary_7d["role_calls"],
        "recent_ai_role_calls_30d": summary_30d["role_calls"],
        "recent_ai_role_failures_today_kst": summary_today_kst["role_failures"],
        "recent_ai_role_failures_24h": summary_24h["role_failures"],
        "recent_ai_role_failures_7d": summary_7d["role_failures"],
        "recent_ai_role_failures_30d": summary_30d["role_failures"],
        "recent_ai_failure_reasons": summary_30d["failure_reasons"],
        "observed_monthly_ai_calls_projection": projected_total,
        "observed_monthly_ai_calls_projection_breakdown": projected_breakdown,
        "observed_monthly_ai_cost_projection_usd": ai_cost_efficiency_summary[
            "monthly_projected_known_ai_cost_usd"
        ],
        "observed_monthly_ai_net_projection_usd": ai_cost_efficiency_summary[
            "monthly_projected_net_after_ai_cost_usd"
        ],
        "ai_protection_status": ai_protection_status,
        "ai_cost_efficiency_summary": ai_cost_efficiency_summary,
        "ai_usage_today_timezone": AI_USAGE_REPORT_TIMEZONE_NAME,
        "ai_usage_today_start_at": today_kst_start_at,
        "ai_usage_today_end_at": today_kst_end_at,
        "ai_usage_summary_today_kst": ai_usage_summary_today_kst,
        "ai_usage_summary_24h": ai_usage_summary_24h,
        "ai_usage_summary_7d": ai_usage_summary_7d,
        "ai_usage_summary_30d": ai_usage_summary_30d,
    }
    with _AI_USAGE_METRICS_CACHE_LOCK:
        _AI_USAGE_METRICS_CACHE[cache_key] = (monotonic_now, source_revision, deepcopy(metrics))
        if len(_AI_USAGE_METRICS_CACHE) > 8:
            oldest_keys = sorted(
                _AI_USAGE_METRICS_CACHE,
                key=lambda key: _AI_USAGE_METRICS_CACHE[key][0],
            )[: len(_AI_USAGE_METRICS_CACHE) - 8]
            for old_key in oldest_keys:
                _AI_USAGE_METRICS_CACHE.pop(old_key, None)
    return metrics
