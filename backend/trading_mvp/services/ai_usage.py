from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import monotonic
from typing import Any, TypedDict

from sqlalchemy import desc, func, select, text
from sqlalchemy.orm import Session

from trading_mvp.config import get_settings
from trading_mvp.models import AgentRun, AuditEvent, Execution, Order, RiskCheck, Setting
from trading_mvp.time_utils import utcnow_naive

AI_ATTEMPT_SOURCES = {"llm", "llm_fallback"}
AI_DECISION_VALUES = ("hold", "long", "short", "reduce", "exit")
AI_DEDUPED_EVENT_TYPE = "decision_ai_deduped"
AI_COST_RATES_USD_PER_1M_TOKENS: dict[str, dict[str, float]] = {
    "gpt-4.1": {"input": 2.0, "output": 8.0},
    "gpt-4.1-mini": {"input": 0.4, "output": 1.6},
    "gpt-4.1-nano": {"input": 0.1, "output": 0.4},
}
AI_ROLE_HOURLY_CALL_BUDGETS = {
    "market_settings_advisor": 4,
}
AI_ROLE_DAILY_TOKEN_BUDGETS = {
    "market_settings_advisor": 250_000,
}
AI_ROLE_CONSECUTIVE_FAILURE_BUDGETS = {
    "market_settings_advisor": 3,
}
AI_USAGE_CACHE_TTL_SECONDS = 30.0
AI_USAGE_DETAIL_ROW_LIMIT = 1_000
AI_USAGE_DEFAULT_COST_MODEL = "gpt-4.1-mini"


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
    recent_ai_calls_24h: int
    recent_ai_calls_7d: int
    recent_ai_successes_24h: int
    recent_ai_successes_7d: int
    recent_ai_failures_24h: int
    recent_ai_failures_7d: int
    recent_ai_tokens_24h: TokenUsage
    recent_ai_tokens_7d: TokenUsage
    recent_ai_role_calls_24h: dict[str, int]
    recent_ai_role_calls_7d: dict[str, int]
    recent_ai_role_failures_24h: dict[str, int]
    recent_ai_role_failures_7d: dict[str, int]
    recent_ai_failure_reasons: list[str]
    observed_monthly_ai_calls_projection: int
    observed_monthly_ai_calls_projection_breakdown: dict[str, int]
    ai_protection_status: dict[str, Any]
    ai_usage_summary_24h: dict[str, Any]
    ai_usage_summary_7d: dict[str, Any]


_AI_USAGE_METRICS_CACHE: dict[
    tuple[str, str, str],
    tuple[float, tuple[object, ...], AIUsageMetrics],
] = {}


def clear_ai_usage_metrics_cache() -> None:
    _AI_USAGE_METRICS_CACHE.clear()


@dataclass(slots=True)
class OpenAICallGate:
    allowed: bool
    reason: str
    retry_after_seconds: int = 0
    backoff_minutes: int = 0
    manual_guard_minutes: int = 0
    last_attempt_at: datetime | None = None
    failure_reason: str | None = None

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
        return payload


@dataclass(slots=True)
class CostEstimate:
    estimated_cost_usd: float | None
    input_tokens: int
    output_tokens: int
    status: str
    model: str | None = None
    model_source: str = "unknown"


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


def _metadata_model(row: AgentRun) -> str | None:
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    for key in ("ai_model", "model", "openai_model"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raw_input_payload = getattr(row, "input_payload", None)
    raw_output_payload = getattr(row, "output_payload", None)
    input_payload = raw_input_payload if isinstance(raw_input_payload, dict) else {}
    output_payload = raw_output_payload if isinstance(raw_output_payload, dict) else {}
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
    return row.output_payload if isinstance(row.output_payload, dict) else {}


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
    }


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
        select(func.max(AgentRun.id), func.count(AgentRun.id)).where(AgentRun.created_at >= since)
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


def _ai_usage_source_revision(
    session: Session,
    *,
    cutoff_24h: datetime,
    cutoff_7d: datetime,
) -> tuple[object, ...]:
    return (
        *_agent_run_revision(session, cutoff_24h),
        *_agent_run_revision(session, cutoff_7d),
        *_deduped_event_revision(session, cutoff_24h),
        *_deduped_event_revision(session, cutoff_7d),
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


def _role_budget_status(rows: Sequence[AgentRun], role: str, *, now: datetime) -> dict[str, Any]:
    attempts = [row for row in rows if row.role == role and is_ai_attempt(row)]
    hourly_limit = AI_ROLE_HOURLY_CALL_BUDGETS.get(role)
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


def build_ai_protection_status(rows: Sequence[AgentRun], *, now: datetime) -> dict[str, Any]:
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
            role: _role_budget_status(rows, role, now=now)
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
    role_budget = _role_budget_status(recent_runs, role, now=now)
    if role_budget["status"] == "blocked":
        return OpenAICallGate(
            allowed=False,
            reason=str(role_budget["reason"]),
            retry_after_seconds=int(role_budget["retry_after_seconds"] or 0),
        )
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
) -> dict[str, Any]:
    decision_rows = [row for row in rows if row.role == "trading_decision"]
    provider_invoked_rows = [row for row in decision_rows if is_ai_attempt(row)]
    failed_rows = [row for row in provider_invoked_rows if is_ai_failure(row)]
    skipped_rows = [row for row in decision_rows if _is_preai_skipped(row)]
    telemetry_row_ids = {
        row.id
        for row in [*provider_invoked_rows, *skipped_rows]
        if row.id is not None
    }
    telemetry_rows = [row for row in decision_rows if row.id in telemetry_row_ids]

    decision_counts = {decision: 0 for decision in AI_DECISION_VALUES}
    decision_ids_by_decision: dict[str, list[int]] = {decision: [] for decision in AI_DECISION_VALUES}
    source_counts: Counter[str] = Counter()
    preai_skip_reasons: Counter[str] = Counter()
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

    return {
        "ai_calls_total": len(provider_invoked_rows) + len(skipped_rows) + int(deduped_count),
        "ai_calls_provider_invoked": len(provider_invoked_rows),
        "ai_calls_skipped_preai": len(skipped_rows),
        "ai_calls_deduped": int(deduped_count),
        "ai_calls_failed": len(failed_rows),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost_usd": estimated_cost_usd,
        "cost_estimate_status": cost_estimate_status,
        "missing_usage_rows": missing_usage_rows,
        "unknown_cost_rows": unknown_cost_rows,
        "decision_counts": decision_counts,
        "source_counts": {key: int(value) for key, value in sorted(source_counts.items())},
        "should_abstain": should_abstain,
        "fail_closed": fail_closed,
        "preai_skip_reasons": {key: int(value) for key, value in sorted(preai_skip_reasons.items())},
        "downstream": _downstream_summary(session, sorted(telemetry_row_ids)),
        "downstream_by_decision": {
            decision: _downstream_summary(session, decision_ids)
            for decision, decision_ids in decision_ids_by_decision.items()
        },
        "cost_basis": {
            "rate_unit": "usd_per_1m_tokens",
            "rates": AI_COST_RATES_USD_PER_1M_TOKENS,
            "note": "Uses prompt_tokens as input and completion_tokens as output; cached-token discounts are not assumed.",
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


def _summarize_attempt_window(session: Session, rows: list[AgentRun], since: datetime) -> AIWindowSummary:
    return _postgres_ai_attempt_summary(session, since) or _summarize_attempt_rows(rows)


def build_ai_usage_metrics(session: Session) -> AIUsageMetrics:
    now = utcnow_naive()
    cutoff_7d = now - timedelta(days=7)
    cutoff_24h = now - timedelta(hours=24)
    cutoff_24h_bucket = cutoff_24h.replace(second=0, microsecond=0).isoformat()
    cutoff_7d_bucket = cutoff_7d.replace(second=0, microsecond=0).isoformat()
    cache_key = (
        _session_cache_scope(session),
        cutoff_24h_bucket,
        cutoff_7d_bucket,
    )
    cached = _AI_USAGE_METRICS_CACHE.get(cache_key)
    monotonic_now = monotonic()
    if cached is not None and monotonic_now - cached[0] <= AI_USAGE_CACHE_TTL_SECONDS:
        return deepcopy(cached[2])

    source_revision = _ai_usage_source_revision(session, cutoff_24h=cutoff_24h, cutoff_7d=cutoff_7d)
    if cached is not None and cached[1] == source_revision:
        _AI_USAGE_METRICS_CACHE[cache_key] = (monotonic_now, cached[1], deepcopy(cached[2]))
        return deepcopy(cached[2])

    rows_7d = list(
        session.scalars(
            select(AgentRun)
            .where(AgentRun.created_at >= cutoff_7d)
            .order_by(desc(AgentRun.created_at))
            .limit(AI_USAGE_DETAIL_ROW_LIMIT)
        )
    )
    rows_24h = [row for row in rows_7d if row.created_at >= cutoff_24h]

    summary_24h = _summarize_attempt_window(session, rows_24h, cutoff_24h)
    summary_7d = _summarize_attempt_window(session, rows_7d, cutoff_7d)
    ai_usage_summary_24h = build_ai_telemetry_summary(
        session,
        rows_24h,
        deduped_count=count_ai_deduped_events(session, cutoff_24h),
    )
    ai_usage_summary_7d = build_ai_telemetry_summary(
        session,
        rows_7d,
        deduped_count=count_ai_deduped_events(session, cutoff_7d),
    )
    ai_protection_status = build_ai_protection_status(rows_7d, now=now)

    if summary_24h["calls"] > 0:
        projected_total = int(summary_24h["calls"] * 30)
        projected_breakdown = {
            role: int(count * 30) for role, count in summary_24h["role_calls"].items()
        }
    elif summary_7d["calls"] > 0:
        projected_total = int(round(summary_7d["calls"] / 7 * 30))
        projected_breakdown = {
            role: int(round(count / 7 * 30)) for role, count in summary_7d["role_calls"].items()
        }
    else:
        projected_total = 0
        projected_breakdown = {}

    metrics: AIUsageMetrics = {
        "recent_ai_calls_24h": int(summary_24h["calls"]),
        "recent_ai_calls_7d": int(summary_7d["calls"]),
        "recent_ai_successes_24h": int(summary_24h["successes"]),
        "recent_ai_successes_7d": int(summary_7d["successes"]),
        "recent_ai_failures_24h": int(summary_24h["failures"]),
        "recent_ai_failures_7d": int(summary_7d["failures"]),
        "recent_ai_tokens_24h": summary_24h["tokens"],
        "recent_ai_tokens_7d": summary_7d["tokens"],
        "recent_ai_role_calls_24h": summary_24h["role_calls"],
        "recent_ai_role_calls_7d": summary_7d["role_calls"],
        "recent_ai_role_failures_24h": summary_24h["role_failures"],
        "recent_ai_role_failures_7d": summary_7d["role_failures"],
        "recent_ai_failure_reasons": summary_7d["failure_reasons"],
        "observed_monthly_ai_calls_projection": projected_total,
        "observed_monthly_ai_calls_projection_breakdown": projected_breakdown,
        "ai_protection_status": ai_protection_status,
        "ai_usage_summary_24h": ai_usage_summary_24h,
        "ai_usage_summary_7d": ai_usage_summary_7d,
    }
    _AI_USAGE_METRICS_CACHE.clear()
    _AI_USAGE_METRICS_CACHE[cache_key] = (monotonic_now, source_revision, deepcopy(metrics))
    return metrics
