from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, TypedDict

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from trading_mvp.models import AgentRun, Setting
from trading_mvp.time_utils import utcnow_naive

AI_ATTEMPT_SOURCES = {"llm", "llm_fallback"}


class TokenUsage(TypedDict):
    estimated_prompt_tokens: int
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


def manual_ai_guard_minutes(settings_row: Setting) -> int:
    return max(2, min(int(settings_row.ai_call_interval_minutes), 5))


def classify_ai_failure(error: str | None) -> str | None:
    if not error:
        return None
    message = error.lower()
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
    if reason == "RATE_LIMIT":
        return max(base, 30)
    if reason in {"TIMEOUT", "UPSTREAM"}:
        return max(base, 15)
    return base


def _metadata_source(row: AgentRun) -> str:
    value = row.metadata_json.get("source")
    return value if isinstance(value, str) else ""


def _metadata_error(row: AgentRun) -> str:
    value = row.metadata_json.get("error")
    return value if isinstance(value, str) else ""


def _metadata_usage(row: AgentRun) -> TokenUsage:
    raw = row.metadata_json.get("usage")
    estimate_raw = row.metadata_json.get("input_token_estimate")
    estimated_prompt_tokens = int(estimate_raw) if isinstance(estimate_raw, (int, float)) else 0
    if not isinstance(raw, dict):
        return {
            "estimated_prompt_tokens": estimated_prompt_tokens,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
    return {
        "estimated_prompt_tokens": estimated_prompt_tokens,
        "prompt_tokens": int(raw.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(raw.get("completion_tokens", 0) or 0),
        "total_tokens": int(raw.get("total_tokens", 0) or 0),
    }


def is_ai_attempt(row: AgentRun) -> bool:
    return row.provider_name == "openai" or _metadata_source(row) in AI_ATTEMPT_SOURCES


def is_ai_success(row: AgentRun) -> bool:
    return row.provider_name == "openai" and _metadata_source(row) == "llm"


def is_ai_failure(row: AgentRun) -> bool:
    return _metadata_source(row) == "llm_fallback"


def _recent_role_runs(session: Session, role: str, *, limit: int = 25) -> list[AgentRun]:
    return list(
        session.scalars(
            select(AgentRun).where(AgentRun.role == role).order_by(desc(AgentRun.created_at)).limit(limit)
        )
    )


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
    recent_runs = _recent_role_runs(session, role, limit=100 if symbol else 25)
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


def _summarize_attempt_rows(rows: list[AgentRun]) -> AIWindowSummary:
    attempts = [row for row in rows if is_ai_attempt(row)]
    successes = [row for row in attempts if is_ai_success(row)]
    failures = [row for row in attempts if is_ai_failure(row)]
    tokens = Counter(
        {
            "estimated_prompt_tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
    )
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
            "estimated_prompt_tokens": int(tokens["estimated_prompt_tokens"]),
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


def build_ai_usage_metrics(session: Session) -> AIUsageMetrics:
    now = utcnow_naive()
    cutoff_7d = now - timedelta(days=7)
    cutoff_24h = now - timedelta(hours=24)
    rows_7d = list(
        session.scalars(select(AgentRun).where(AgentRun.created_at >= cutoff_7d).order_by(desc(AgentRun.created_at)))
    )
    rows_24h = [row for row in rows_7d if row.created_at >= cutoff_24h]

    summary_24h = _summarize_attempt_rows(rows_24h)
    summary_7d = _summarize_attempt_rows(rows_7d)

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

    return {
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
    }
