from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import Lock
from time import monotonic
from typing import Any

from sqlalchemy import Text, cast, desc, func, select
from sqlalchemy.orm import Session

from trading_mvp.models import (
    AgentRun,
    AuditEvent,
    CompetitorNote,
    DecisionPerformanceFact,
    Execution,
    Order,
    PnLSnapshot,
    Position,
    RiskCheck,
)
from trading_mvp.schemas import (
    AIBaselineComparisonBucket,
    AIBaselineComparisonSummary,
    AIUsageTelemetrySummary,
    DecisionPerformanceEntry,
    FeatureFlagPerformanceEntry,
    LimitedLiveReadinessReport,
    PerformanceAggregateEntry,
    PerformanceWindowReport,
    PerformanceWindowSummary,
    SignalPerformanceEntry,
    SignalPerformanceReportResponse,
    StructuredCompetitorNote,
    StructuredCompetitorNotesResponse,
)
from trading_mvp.services.ai_usage import build_ai_telemetry_summary, count_ai_deduped_events
from trading_mvp.time_utils import utcnow_naive

DEFAULT_SIGNAL_PERFORMANCE_WINDOW_SPECS: tuple[tuple[str, int], ...] = (
    ("24h", 24),
    ("7d", 24 * 7),
    ("30d", 24 * 30),
)
SIGNAL_PERFORMANCE_REPORT_CACHE_TTL_SECONDS = 15.0


@dataclass(slots=True)
class _CachedSignalPerformanceReport:
    stored_at: float
    source_key: tuple[object, ...]
    payload: SignalPerformanceReportResponse


_signal_performance_report_cache: dict[tuple[object, ...], _CachedSignalPerformanceReport] = {}
_signal_performance_report_cache_lock = Lock()


@dataclass(slots=True)
class SignalBucket:
    key: str
    decisions: int = 0
    approvals: int = 0
    orders: int = 0
    fills: int = 0
    holds: int = 0
    longs: int = 0
    shorts: int = 0
    reduces: int = 0
    exits: int = 0
    wins: int = 0
    losses: int = 0
    realized_pnl_total: float = 0.0
    fee_total: float = 0.0
    net_realized_pnl_total: float = 0.0
    slippages: list[float] = field(default_factory=list)
    holding_minutes: list[float] = field(default_factory=list)
    holding_over_plan_count: int = 0
    open_positions: int = 0
    closed_positions: int = 0
    stop_loss_closes: int = 0
    take_profit_closes: int = 0
    manual_closes: int = 0
    unclassified_closes: int = 0
    latest_seen_at: datetime = field(default_factory=utcnow_naive)


@dataclass(slots=True)
class DecisionPerformanceSnapshot:
    decision_run_id: int
    created_at: datetime
    symbol: str
    timeframe: str
    decision: str
    regime: str
    trend_alignment: str
    weak_volume: bool
    volatility_expanded: bool
    momentum_weakening: bool
    rationale_codes: list[str]
    approved: bool
    approved_risk_pct: float
    approved_leverage: float
    orders: int
    fills: int
    wins: int
    losses: int
    realized_pnl_total: float
    fee_total: float
    net_realized_pnl_total: float
    average_slippage_pct: float
    arrival_slippage_pct: float
    realized_slippage_pct: float
    first_fill_latency_seconds: float
    cancel_attempts: int
    cancel_successes: int
    max_holding_minutes_planned: int | None
    holding_minutes_observed: float
    holding_result_status: str
    stop_loss: float | None
    take_profit: float | None
    planned_risk_reward_ratio: float | None
    close_outcome: str
    stop_loss_closes: int
    take_profit_closes: int
    manual_closes: int
    unclassified_closes: int
    open_positions: int
    closed_positions: int
    holding_over_plan_count: int
    position_ids: list[int]
    mfe_pct: float
    mae_pct: float
    mfe_pnl: float
    mae_pnl: float
    baseline_decision: str | None
    ai_decision: str
    decision_agreement_level: str
    decision_agreement_source: str
    ai_used: bool
    comparison_bucket: str
    unobserved_reason: str | None
    pnl_per_exposure_hour: float | None


@dataclass(slots=True)
class DecisionPerformanceContext:
    id: int
    created_at: datetime
    provider_name: str
    symbol: str
    timeframe: str
    decision: str
    rationale_codes: list[str]
    regime: str
    trend_alignment: str
    weak_volume: bool
    volatility_expanded: bool
    momentum_weakening: bool
    entry_zone_min: float | None
    entry_zone_max: float | None
    stop_loss: float | None
    take_profit: float | None
    max_holding_minutes: int | None
    baseline_decision: str | None
    ai_used: bool
    comparison_bucket: str
    decision_agreement_level: str
    decision_agreement_source: str
    metadata_json: dict[str, Any]
    output_payload: dict[str, Any]
    role: str = "trading_decision"


@dataclass(slots=True)
class PnLSnapshotPoint:
    created_at: datetime
    equity: float
    cumulative_pnl: float
    consecutive_losses: int


@dataclass(slots=True)
class PnLSnapshotWindowCache:
    rows: list[PnLSnapshotPoint]
    latest: PnLSnapshotPoint | None
    baseline_before_max_since: PnLSnapshotPoint | None


@dataclass(slots=True)
class ReadinessSafetyEvent:
    created_at: datetime
    protection_failure: bool = False
    unknown_submission: bool = False
    stale_incomplete_data_block: bool = False


@dataclass(slots=True)
class ReadinessSafetyEventCache:
    events: list[ReadinessSafetyEvent]

    def counts_since(self, since: datetime) -> tuple[int, int, int]:
        protection_failure_count = 0
        unknown_submission_count = 0
        stale_incomplete_data_block_count = 0
        for event in self.events:
            if event.created_at < since:
                continue
            if event.protection_failure:
                protection_failure_count += 1
            if event.unknown_submission:
                unknown_submission_count += 1
            if event.stale_incomplete_data_block:
                stale_incomplete_data_block_count += 1
        return protection_failure_count, unknown_submission_count, stale_incomplete_data_block_count


CANCEL_ATTEMPT_ORDER_STATUSES = {"canceled", "cancelled", "expired"}
CANCEL_SUCCESS_ORDER_STATUSES = {"canceled", "cancelled"}
ENTRY_DECISIONS = {"long", "short"}
MANAGEMENT_DECISIONS = {"reduce", "exit"}
AI_BASELINE_BUCKET_ORDER = (
    "baseline_only_entry",
    "ai_approved_entry",
    "ai_rejected_baseline_entry",
    "ai_management_action",
    "ai_hold_no_trade",
    "provider_failed_fail_closed",
)
READINESS_MIN_CANDIDATE_EVENTS = 5
READINESS_MIN_ACTUAL_ENTRIES = 2
READINESS_SCALE_UP_MIN_CANDIDATE_EVENTS = 20
READINESS_SCALE_UP_MIN_ACTUAL_ENTRIES = 5
READINESS_MAX_DRAWDOWN = 1_000.0
READINESS_STALE_BLOCK_RATIO_HIGH = 0.25
READINESS_STALE_BLOCK_COUNT_HIGH = 3
READINESS_PROTECTION_REASON_CODES = {
    "PROTECTION_REQUIRED",
    "PROTECTIVE_ORDER_FAILURE",
    "MISSING_PROTECTIVE_ORDERS",
    "PROTECTION_STATE_UNVERIFIED",
    "PROTECTION_VERIFY_FAILED",
    "INVALID_PROTECTION_BRACKETS",
}
READINESS_PROTECTION_AUDIT_EVENTS = {
    "protective_order_failure",
    "protection_verify_failed",
    "unprotected_position_detected",
    "emergency_exit_failed",
}
READINESS_UNKNOWN_REASON_CODES = {
    "LIVE_ORDER_SUBMISSION_UNKNOWN",
    "UNRESOLVED_SUBMISSION_GUARD_ACTIVE",
    "UNRESOLVED_SUBMISSION_DEADLINE_EXCEEDED",
}
READINESS_UNKNOWN_AUDIT_EVENTS = {
    "live_order_submission_unknown",
}
READINESS_AUDIT_SAFETY_EVENT_TYPES = READINESS_PROTECTION_AUDIT_EVENTS | READINESS_UNKNOWN_AUDIT_EVENTS | {
    "live_execution_error",
    "live_execution_rejected",
    "live_execution_skipped",
    "pending_entry_plan_blocked",
    "pending_entry_plan_canceled",
    "pending_entry_plan_deferred",
    "protection_verification_failed",
    "risk_blocked",
    "risk_check",
}
READINESS_UNKNOWN_ORDER_STATUSES = {
    "submit_unknown",
    "submission_unknown",
    "unknown_submission",
    "unknown",
}
READINESS_STALE_INCOMPLETE_REASON_CODES = {
    "STALE_MARKET_DATA",
    "INCOMPLETE_MARKET_DATA",
    "ACCOUNT_STATE_STALE",
    "POSITION_STATE_STALE",
    "OPEN_ORDERS_STATE_STALE",
    "MARKET_SNAPSHOT_STALE",
    "MARKET_SNAPSHOT_INCOMPLETE",
    "FEATURE_INPUT_MISSING",
}
READINESS_AUDIT_REASON_PAYLOAD_KEYS = (
    "reason_codes",
    "blocked_reason_codes",
    "degraded_reason_codes",
    "protection_reason_codes",
    "data_quality_block_reason_codes",
    "blocked_reason",
    "degraded_reason",
    "approval_required_reason",
)
READINESS_AUDIT_REASON_PAYLOAD_KEY_MARKERS = tuple(f'"{key}"' for key in READINESS_AUDIT_REASON_PAYLOAD_KEYS)
READINESS_AUDIT_REASON_CODE_MARKERS = tuple(
    f'"{code}"'
    for code in sorted(
        READINESS_PROTECTION_REASON_CODES | READINESS_UNKNOWN_REASON_CODES | READINESS_STALE_INCOMPLETE_REASON_CODES
    )
)


def _safe_float(value: object, default: float = 0.0) -> float:
    if value in {None, ""}:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return default


def _safe_int(value: object, default: int | None = None) -> int | None:
    if value in {None, ""}:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value))
    except ValueError:
        return default


def _safe_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return default


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _normalized_decision(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def _decision_agreement_payload(metadata: dict[str, object]) -> dict[str, object]:
    agreement = _as_dict(metadata.get("decision_agreement"))
    if agreement:
        return agreement
    decision_context = _as_dict(metadata.get("decision_context"))
    return _as_dict(decision_context.get("decision_agreement"))


def _decision_source(metadata: dict[str, object]) -> str:
    return str(metadata.get("source") or "").strip().lower()


def _ai_used_from_metadata(
    *,
    metadata: dict[str, object],
    agreement: dict[str, object],
    provider_name: str,
) -> bool:
    if "ai_used" in agreement:
        return _safe_bool(agreement.get("ai_used"), default=False)
    source = _decision_source(metadata)
    if source == "llm":
        return True
    if source == "llm_fallback":
        return False
    provider = provider_name.strip().lower()
    return provider not in {"", "deterministic", "deterministic-mock", "local"}


def _baseline_decision_from_metadata(
    *,
    metadata: dict[str, object],
    agreement: dict[str, object],
    final_decision: str,
    ai_used: bool,
) -> str | None:
    baseline = _as_dict(metadata.get("deterministic_baseline"))
    baseline_decision = (
        _normalized_decision(agreement.get("baseline_decision"))
        or _normalized_decision(baseline.get("decision"))
        or _normalized_decision(metadata.get("baseline_decision"))
    )
    if baseline_decision is None and not ai_used:
        return final_decision
    return baseline_decision


def _is_fail_closed(metadata: dict[str, object], payload: dict[str, object]) -> bool:
    return (
        _decision_source(metadata) == "llm_fallback"
        or _safe_bool(metadata.get("fail_closed_applied"), default=False)
        or _safe_bool(metadata.get("data_quality_fail_closed_applied"), default=False)
        or _safe_bool(payload.get("fail_closed_applied"), default=False)
        or _safe_bool(payload.get("data_quality_fail_closed_applied"), default=False)
    )


def _comparison_bucket(
    *,
    baseline_decision: str | None,
    ai_decision: str,
    ai_used: bool,
    fail_closed: bool,
) -> str:
    if fail_closed:
        return "provider_failed_fail_closed"
    baseline_is_entry = baseline_decision in ENTRY_DECISIONS
    if ai_used and ai_decision in MANAGEMENT_DECISIONS:
        return "ai_management_action"
    if ai_used and ai_decision in ENTRY_DECISIONS:
        return "ai_approved_entry"
    if ai_used and baseline_is_entry and ai_decision == "hold":
        return "ai_rejected_baseline_entry"
    if ai_used:
        return "ai_hold_no_trade"
    if ai_decision in ENTRY_DECISIONS:
        return "baseline_only_entry"
    if ai_decision in MANAGEMENT_DECISIONS:
        return "ai_management_action"
    return "ai_hold_no_trade"


def _unobserved_reason(
    *,
    snapshot_bucket: str,
    decision: str,
    fills: int,
    orders: int,
    linked_risk: RiskCheck | None,
) -> str | None:
    if fills > 0:
        return None
    if snapshot_bucket == "ai_rejected_baseline_entry":
        return "ai_filtered_no_order"
    if snapshot_bucket == "provider_failed_fail_closed":
        return "provider_failed_fail_closed"
    if linked_risk is not None and not linked_risk.allowed and decision in ENTRY_DECISIONS:
        return "risk_blocked"
    if orders > 0:
        return "order_not_filled"
    if decision in ENTRY_DECISIONS or decision in MANAGEMENT_DECISIONS:
        return "no_order_created"
    return "no_trade_intended"


def _pnl_per_exposure_hour(net_pnl_after_fees: float, holding_minutes: float, fills: int) -> float | None:
    if fills <= 0 or holding_minutes <= 0:
        return None
    return net_pnl_after_fees / (holding_minutes / 60.0)


def _pnl_point_from_row(row: PnLSnapshot | None) -> PnLSnapshotPoint | None:
    if row is None:
        return None
    return PnLSnapshotPoint(
        created_at=row.created_at,
        equity=_safe_float(row.equity, default=0.0),
        cumulative_pnl=_safe_float(row.cumulative_pnl, default=0.0),
        consecutive_losses=int(row.consecutive_losses),
    )


def _load_pnl_snapshot_cache(session: Session, max_since: datetime) -> PnLSnapshotWindowCache:
    latest = session.scalar(select(PnLSnapshot).order_by(desc(PnLSnapshot.created_at)).limit(1))
    baseline = session.scalar(
        select(PnLSnapshot)
        .where(PnLSnapshot.created_at < max_since)
        .order_by(desc(PnLSnapshot.created_at))
        .limit(1)
    )
    rows = [
        point
        for point in (
            _pnl_point_from_row(row)
            for row in session.scalars(
                select(PnLSnapshot)
                .where(PnLSnapshot.created_at >= max_since)
                .order_by(PnLSnapshot.created_at.asc())
            )
        )
        if point is not None
    ]
    return PnLSnapshotWindowCache(
        rows=rows,
        latest=_pnl_point_from_row(latest),
        baseline_before_max_since=_pnl_point_from_row(baseline),
    )


def _pnl_rows_since(cache: PnLSnapshotWindowCache, since: datetime) -> list[PnLSnapshotPoint]:
    return [row for row in cache.rows if row.created_at >= since]


def _pnl_baseline_before(cache: PnLSnapshotWindowCache, since: datetime) -> PnLSnapshotPoint | None:
    baseline = cache.baseline_before_max_since
    for row in cache.rows:
        if row.created_at >= since:
            break
        baseline = row
    return baseline


def _snapshot_net_pnl_estimate(
    session: Session,
    since: datetime,
    pnl_snapshot_cache: PnLSnapshotWindowCache | None = None,
) -> float:
    if pnl_snapshot_cache is not None:
        if pnl_snapshot_cache.latest is None:
            return 0.0
        baseline = _pnl_baseline_before(pnl_snapshot_cache, since)
        baseline_cumulative = baseline.cumulative_pnl if baseline is not None else 0.0
        return pnl_snapshot_cache.latest.cumulative_pnl - baseline_cumulative
    latest = session.scalar(select(PnLSnapshot).order_by(desc(PnLSnapshot.created_at)).limit(1))
    if latest is None:
        return 0.0
    baseline = session.scalar(
        select(PnLSnapshot)
        .where(PnLSnapshot.created_at < since)
        .order_by(desc(PnLSnapshot.created_at))
        .limit(1)
    )
    baseline_cumulative = baseline.cumulative_pnl if baseline is not None else 0.0
    return latest.cumulative_pnl - baseline_cumulative


def _max_drawdown_from_pnl_snapshots(
    session: Session,
    since: datetime,
    pnl_snapshot_cache: PnLSnapshotWindowCache | None = None,
) -> float:
    rows: Sequence[PnLSnapshot | PnLSnapshotPoint]
    if pnl_snapshot_cache is None:
        rows = list(
            session.scalars(
                select(PnLSnapshot)
                .where(PnLSnapshot.created_at >= since)
                .order_by(PnLSnapshot.created_at.asc())
            )
        )
    else:
        rows = _pnl_rows_since(pnl_snapshot_cache, since)
    if not rows:
        return 0.0
    values = [
        _safe_float(row.equity, default=0.0) if _safe_float(row.equity, default=0.0) > 0 else _safe_float(row.cumulative_pnl, default=0.0)
        for row in rows
    ]
    peak = values[0]
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        max_drawdown = max(max_drawdown, peak - value)
    return max_drawdown


def _max_consecutive_losses_from_pnl_snapshots(
    session: Session,
    since: datetime,
    pnl_snapshot_cache: PnLSnapshotWindowCache | None = None,
) -> int:
    rows: Sequence[PnLSnapshot | PnLSnapshotPoint]
    if pnl_snapshot_cache is None:
        rows = list(
            session.scalars(
                select(PnLSnapshot)
                .where(PnLSnapshot.created_at >= since)
                .order_by(PnLSnapshot.created_at.asc())
            )
        )
    else:
        rows = _pnl_rows_since(pnl_snapshot_cache, since)
    if not rows:
        if pnl_snapshot_cache is not None:
            latest = pnl_snapshot_cache.latest
            return int(latest.consecutive_losses) if latest is not None else 0
        latest = session.scalar(select(PnLSnapshot).order_by(desc(PnLSnapshot.created_at)).limit(1))
        return int(latest.consecutive_losses) if latest is not None else 0
    return max(int(row.consecutive_losses) for row in rows)


def _reason_codes_from_payload(value: object) -> list[str]:
    payload = _as_dict(value)
    codes: list[str] = []
    for key in (
        "reason_codes",
        "blocked_reason_codes",
        "degraded_reason_codes",
        "protection_reason_codes",
        "data_quality_block_reason_codes",
    ):
        raw = payload.get(key)
        if isinstance(raw, list):
            codes.extend(str(item) for item in raw if item not in {None, ""})
    for key in ("blocked_reason", "degraded_reason", "approval_required_reason"):
        raw_code = payload.get(key)
        if raw_code not in {None, ""}:
            codes.append(str(raw_code))
    return codes


def _risk_reason_codes(row: RiskCheck) -> list[str]:
    return _risk_reason_codes_from_values(row.reason_codes, row.payload)


def _order_reason_codes(row: Order) -> list[str]:
    return _order_reason_codes_from_values(row.reason_codes, row.metadata_json)


def _audit_reason_codes(row: AuditEvent) -> list[str]:
    return _reason_codes_from_payload(row.payload)


def _payload_text_may_have_readiness_reason_codes(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    return any(marker in value for marker in READINESS_AUDIT_REASON_PAYLOAD_KEY_MARKERS) and any(
        marker in value for marker in READINESS_AUDIT_REASON_CODE_MARKERS
    )


def _reason_codes_from_payload_text(value: object) -> list[str]:
    if not _payload_text_may_have_readiness_reason_codes(value):
        return []
    try:
        payload = json.loads(value) if isinstance(value, str) else {}
    except ValueError:
        return []
    return _reason_codes_from_payload(payload)


def _risk_reason_codes_from_values(reason_codes: object, payload: object) -> list[str]:
    codes = [str(item) for item in reason_codes if item not in {None, ""}] if isinstance(reason_codes, list) else []
    codes.extend(_reason_codes_from_payload(payload))
    return list(dict.fromkeys(codes))


def _order_reason_codes_from_values(reason_codes: object, metadata_json: object) -> list[str]:
    codes = [str(item) for item in reason_codes if item not in {None, ""}] if isinstance(reason_codes, list) else []
    codes.extend(_reason_codes_from_payload(metadata_json))
    return list(dict.fromkeys(codes))


def _append_safety_observation(
    events: list[ReadinessSafetyEvent],
    *,
    created_at: datetime,
    protection_failure: bool = False,
    unknown_submission: bool = False,
    stale_incomplete_data_block: bool = False,
) -> None:
    if protection_failure or unknown_submission or stale_incomplete_data_block:
        events.append(
            ReadinessSafetyEvent(
                created_at=created_at,
                protection_failure=protection_failure,
                unknown_submission=unknown_submission,
                stale_incomplete_data_block=stale_incomplete_data_block,
            )
        )


def _load_limited_live_safety_event_cache(session: Session, since: datetime) -> ReadinessSafetyEventCache:
    events: list[ReadinessSafetyEvent] = []

    for created_at, reason_codes, payload in session.execute(
        select(RiskCheck.created_at, RiskCheck.reason_codes, RiskCheck.payload).where(RiskCheck.created_at >= since)
    ):
        codes = set(_risk_reason_codes_from_values(reason_codes, payload))
        _append_safety_observation(
            events,
            created_at=created_at,
            protection_failure=bool(codes & READINESS_PROTECTION_REASON_CODES),
            unknown_submission=bool(codes & READINESS_UNKNOWN_REASON_CODES),
            stale_incomplete_data_block=bool(codes & READINESS_STALE_INCOMPLETE_REASON_CODES),
        )

    for created_at, reason_codes, metadata_json, status in session.execute(
        select(Order.created_at, Order.reason_codes, Order.metadata_json, Order.status).where(Order.created_at >= since)
    ):
        codes = set(_order_reason_codes_from_values(reason_codes, metadata_json))
        normalized_status = str(status or "").strip().lower()
        _append_safety_observation(
            events,
            created_at=created_at,
            unknown_submission=bool(codes & READINESS_UNKNOWN_REASON_CODES)
            or normalized_status in READINESS_UNKNOWN_ORDER_STATUSES,
        )

    for created_at, event_type, payload_text in session.execute(
        select(AuditEvent.created_at, AuditEvent.event_type, cast(AuditEvent.payload, Text)).where(
            AuditEvent.created_at >= since,
            AuditEvent.event_type.in_(tuple(READINESS_AUDIT_SAFETY_EVENT_TYPES)),
        )
    ):
        codes = set(_reason_codes_from_payload_text(payload_text))
        normalized_event_type = str(event_type or "").strip().lower()
        _append_safety_observation(
            events,
            created_at=created_at,
            protection_failure=normalized_event_type in READINESS_PROTECTION_AUDIT_EVENTS
            or bool(codes & READINESS_PROTECTION_REASON_CODES),
            unknown_submission=normalized_event_type in READINESS_UNKNOWN_AUDIT_EVENTS
            or bool(codes & READINESS_UNKNOWN_REASON_CODES),
            stale_incomplete_data_block=bool(codes & READINESS_STALE_INCOMPLETE_REASON_CODES),
        )

    return ReadinessSafetyEventCache(events=events)


def _count_limited_live_safety_events(
    session: Session,
    *,
    since: datetime,
    safety_event_cache: ReadinessSafetyEventCache | None = None,
) -> tuple[int, int, int]:
    cache = safety_event_cache or _load_limited_live_safety_event_cache(session, since)
    return cache.counts_since(since)


def _extract_analysis_context(decision_row: AgentRun) -> tuple[str, str, bool, bool, bool]:
    input_payload = decision_row.input_payload if isinstance(decision_row.input_payload, dict) else {}
    metadata = decision_row.metadata_json if isinstance(decision_row.metadata_json, dict) else {}
    features = input_payload.get("features") if isinstance(input_payload.get("features"), dict) else {}
    regime = features.get("regime") if isinstance(features.get("regime"), dict) else {}
    metadata_context = (
        metadata.get("analysis_context") if isinstance(metadata.get("analysis_context"), dict) else {}
    )
    metadata_regime = (
        metadata_context.get("regime") if isinstance(metadata_context.get("regime"), dict) else {}
    )
    metadata_flags = (
        metadata_context.get("flags") if isinstance(metadata_context.get("flags"), dict) else {}
    )

    primary_regime = str(
        regime.get("primary_regime")
        or metadata_regime.get("primary_regime")
        or "unknown"
    )
    trend_alignment = str(
        regime.get("trend_alignment")
        or metadata_regime.get("trend_alignment")
        or "unknown"
    )
    volatility_regime = str(
        regime.get("volatility_regime")
        or metadata_regime.get("volatility_regime")
        or "unknown"
    )
    weak_volume = _safe_bool(
        regime.get("weak_volume", metadata_flags.get("weak_volume", False))
    )
    momentum_weakening = _safe_bool(
        regime.get("momentum_weakening", metadata_flags.get("momentum_weakening", False))
    )
    volatility_expanded = _safe_bool(
        metadata_flags.get("volatility_expanded", volatility_regime == "expanded")
    )
    return primary_regime, trend_alignment, weak_volume, volatility_expanded, momentum_weakening


def _rationale_codes_from_payload(payload: Mapping[str, Any]) -> list[str]:
    raw_codes = payload.get("rationale_codes")
    codes = [str(item) for item in raw_codes if item] if isinstance(raw_codes, list) else []
    return codes or ["UNSPECIFIED"]


def _compact_telemetry_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in (
        "source",
        "error",
        "usage",
        "ai_model",
        "model",
        "openai_model",
        "pre_ai_skip_reason",
        "ai_skipped_reason",
        "last_ai_skip_reason",
        "provider_not_called_due_to_quality",
        "provider_status",
        "data_quality_block_reason_codes",
        "event_risk_reason_codes",
        "should_abstain",
        "fail_closed_applied",
    ):
        value = metadata.get(key)
        if value is not None:
            compact[key] = value
    return compact


def _compact_telemetry_output(payload: Mapping[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {"decision": payload.get("decision")}
    for key in ("should_abstain", "fail_closed_applied", "data_quality_flags"):
        value = payload.get(key)
        if value is not None:
            compact[key] = value
    return compact


def _decision_performance_fact_values(row: AgentRun) -> dict[str, Any]:
    payload = row.output_payload if isinstance(row.output_payload, dict) else {}
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    decision = str(payload.get("decision") or "unknown")
    agreement = _decision_agreement_payload(metadata)
    ai_used = _ai_used_from_metadata(
        metadata=metadata,
        agreement=agreement,
        provider_name=str(row.provider_name or ""),
    )
    baseline_decision = _baseline_decision_from_metadata(
        metadata=metadata,
        agreement=agreement,
        final_decision=decision,
        ai_used=ai_used,
    )
    comparison_bucket = _comparison_bucket(
        baseline_decision=baseline_decision,
        ai_decision=decision,
        ai_used=ai_used,
        fail_closed=_is_fail_closed(metadata, payload),
    )
    regime, trend_alignment, weak_volume, volatility_expanded, momentum_weakening = _extract_analysis_context(row)
    return {
        "decision_run_id": int(row.id),
        "provider_name": str(row.provider_name or "deterministic-mock"),
        "symbol": str(payload.get("symbol") or "UNKNOWN"),
        "timeframe": str(payload.get("timeframe") or "UNKNOWN"),
        "decision": decision,
        "rationale_codes": _rationale_codes_from_payload(payload),
        "regime": regime,
        "trend_alignment": trend_alignment,
        "weak_volume": weak_volume,
        "volatility_expanded": volatility_expanded,
        "momentum_weakening": momentum_weakening,
        "entry_zone_min": _safe_float(payload.get("entry_zone_min"), default=0.0) or None,
        "entry_zone_max": _safe_float(payload.get("entry_zone_max"), default=0.0) or None,
        "stop_loss": _safe_float(payload.get("stop_loss"), default=0.0) or None,
        "take_profit": _safe_float(payload.get("take_profit"), default=0.0) or None,
        "max_holding_minutes": _safe_int(payload.get("max_holding_minutes")),
        "baseline_decision": baseline_decision,
        "ai_used": ai_used,
        "comparison_bucket": comparison_bucket,
        "decision_agreement_level": str(agreement.get("level") or ""),
        "decision_agreement_source": str(agreement.get("comparison_source") or ""),
        "telemetry_metadata": _compact_telemetry_metadata(metadata),
        "telemetry_output": _compact_telemetry_output(payload),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def persist_decision_performance_fact(session: Session, row: AgentRun) -> DecisionPerformanceFact | None:
    if row.id is None or row.role != "trading_decision":
        return None
    values = _decision_performance_fact_values(row)
    fact = session.scalar(
        select(DecisionPerformanceFact).where(
            DecisionPerformanceFact.decision_run_id == int(row.id)
        )
    )
    if fact is None:
        fact = DecisionPerformanceFact(**values)
        session.add(fact)
    else:
        for key, value in values.items():
            setattr(fact, key, value)
    session.flush()
    return fact


def _context_from_fact(fact: DecisionPerformanceFact) -> DecisionPerformanceContext:
    metadata = fact.telemetry_metadata if isinstance(fact.telemetry_metadata, dict) else {}
    output = fact.telemetry_output if isinstance(fact.telemetry_output, dict) else {}
    output = {"decision": fact.decision, **output}
    return DecisionPerformanceContext(
        id=fact.decision_run_id,
        created_at=fact.created_at,
        provider_name=fact.provider_name,
        symbol=fact.symbol,
        timeframe=fact.timeframe,
        decision=fact.decision,
        rationale_codes=(
            [str(item) for item in fact.rationale_codes if item]
            if isinstance(fact.rationale_codes, list)
            else ["UNSPECIFIED"]
        ),
        regime=fact.regime,
        trend_alignment=fact.trend_alignment,
        weak_volume=bool(fact.weak_volume),
        volatility_expanded=bool(fact.volatility_expanded),
        momentum_weakening=bool(fact.momentum_weakening),
        entry_zone_min=fact.entry_zone_min,
        entry_zone_max=fact.entry_zone_max,
        stop_loss=fact.stop_loss,
        take_profit=fact.take_profit,
        max_holding_minutes=fact.max_holding_minutes,
        baseline_decision=fact.baseline_decision,
        ai_used=bool(fact.ai_used),
        comparison_bucket=fact.comparison_bucket,
        decision_agreement_level=fact.decision_agreement_level,
        decision_agreement_source=fact.decision_agreement_source,
        metadata_json=metadata,
        output_payload=output,
    )


def _context_from_agent_run(row: AgentRun) -> DecisionPerformanceContext:
    values = _decision_performance_fact_values(row)
    return DecisionPerformanceContext(
        id=values["decision_run_id"],
        created_at=values["created_at"],
        provider_name=values["provider_name"],
        symbol=values["symbol"],
        timeframe=values["timeframe"],
        decision=values["decision"],
        rationale_codes=values["rationale_codes"],
        regime=values["regime"],
        trend_alignment=values["trend_alignment"],
        weak_volume=values["weak_volume"],
        volatility_expanded=values["volatility_expanded"],
        momentum_weakening=values["momentum_weakening"],
        entry_zone_min=values["entry_zone_min"],
        entry_zone_max=values["entry_zone_max"],
        stop_loss=values["stop_loss"],
        take_profit=values["take_profit"],
        max_holding_minutes=values["max_holding_minutes"],
        baseline_decision=values["baseline_decision"],
        ai_used=values["ai_used"],
        comparison_bucket=values["comparison_bucket"],
        decision_agreement_level=values["decision_agreement_level"],
        decision_agreement_source=values["decision_agreement_source"],
        metadata_json=values["telemetry_metadata"],
        output_payload=values["telemetry_output"],
    )


def _load_decision_performance_contexts(
    session: Session,
    since: datetime,
) -> list[DecisionPerformanceContext]:
    decision_refs = list(
        session.execute(
            select(AgentRun.id, AgentRun.created_at)
            .where(AgentRun.role == "trading_decision", AgentRun.created_at >= since)
            .order_by(AgentRun.created_at.desc())
        )
    )
    decision_ids = [int(row_id) for row_id, _created_at in decision_refs if row_id is not None]
    if not decision_ids:
        return []

    facts = list(
        session.scalars(
            select(DecisionPerformanceFact).where(
                DecisionPerformanceFact.decision_run_id.in_(decision_ids)
            )
        )
    )
    contexts_by_id: dict[int, DecisionPerformanceContext] = {
        fact.decision_run_id: _context_from_fact(fact) for fact in facts
    }
    missing_ids = [row_id for row_id in decision_ids if row_id not in contexts_by_id]
    if missing_ids:
        missing_rows = list(
            session.scalars(select(AgentRun).where(AgentRun.id.in_(missing_ids)))
        )
        for row in missing_rows:
            contexts_by_id[int(row.id)] = _context_from_agent_run(row)
    return [contexts_by_id[row_id] for row_id in decision_ids if row_id in contexts_by_id]


def _planned_risk_reward_ratio(
    *,
    decision: str,
    entry_zone_min: object,
    entry_zone_max: object,
    stop_loss: float | None,
    take_profit: float | None,
) -> float | None:
    if stop_loss is None or take_profit is None:
        return None
    entry_min = _safe_float(entry_zone_min, default=0.0)
    entry_max = _safe_float(entry_zone_max, default=0.0)
    entry_price = (entry_min + entry_max) / 2.0 if entry_min > 0 and entry_max > 0 else max(entry_min, entry_max)
    if entry_price <= 0:
        return None
    if decision == "long":
        risk = entry_price - stop_loss
        reward = take_profit - entry_price
    elif decision == "short":
        risk = stop_loss - entry_price
        reward = entry_price - take_profit
    else:
        return None
    if risk <= 0 or reward <= 0:
        return None
    return reward / risk


def _close_outcome_from_counts(
    *,
    stop_loss_closes: int,
    take_profit_closes: int,
    manual_closes: int,
    unclassified_closes: int,
    open_positions: int,
    closed_positions: int,
) -> str:
    if stop_loss_closes > 0:
        return "stop_loss"
    if take_profit_closes > 0:
        return "take_profit"
    if manual_closes > 0:
        return "manual_close"
    if unclassified_closes > 0:
        return "unclassified_close"
    if open_positions > 0:
        return "open"
    if closed_positions > 0:
        return "closed_without_fill_classification"
    return "not_closed"


def _hold_condition_key(
    *,
    regime: str,
    trend_alignment: str,
    weak_volume: bool,
    volatility_expanded: bool,
    momentum_weakening: bool,
) -> str:
    return (
        f"{regime} | trend={trend_alignment} | weak_volume={'on' if weak_volume else 'off'}"
        f" | volatility_expanded={'on' if volatility_expanded else 'off'}"
        f" | momentum_weakening={'on' if momentum_weakening else 'off'}"
    )


def _holding_snapshot(
    positions: list[Position],
    *,
    planned_max_holding_minutes: int | None,
    now: datetime,
) -> tuple[float, str, int, int, int]:
    if not positions:
        return 0.0, "unlinked", 0, 0, 0
    observed_values: list[float] = []
    open_positions = 0
    closed_positions = 0
    holding_over_plan_count = 0
    for position in positions:
        end_at = position.closed_at or now
        duration_minutes = max((end_at - position.opened_at).total_seconds() / 60.0, 0.0)
        observed_values.append(duration_minutes)
        if position.closed_at is None or position.status == "open":
            open_positions += 1
        else:
            closed_positions += 1
        if planned_max_holding_minutes is not None and duration_minutes > planned_max_holding_minutes:
            holding_over_plan_count += 1
    observed = max(observed_values) if observed_values else 0.0
    if open_positions > 0:
        status = "open_over_plan" if holding_over_plan_count > 0 else "open_within_plan"
    else:
        status = "closed_over_plan" if holding_over_plan_count > 0 else "closed_within_plan"
    return observed, status, open_positions, closed_positions, holding_over_plan_count


def _position_excursion_snapshot(positions: list[Position]) -> tuple[float, float, float, float]:
    if not positions:
        return 0.0, 0.0, 0.0, 0.0
    mfe_pct_values: list[float] = []
    mae_pct_values: list[float] = []
    mfe_pnl_values: list[float] = []
    mae_pnl_values: list[float] = []
    for position in positions:
        metadata = position.metadata_json if isinstance(position.metadata_json, dict) else {}
        excursion = metadata.get("intratrade") if isinstance(metadata.get("intratrade"), dict) else {}
        if not excursion:
            excursion = metadata.get("replay") if isinstance(metadata.get("replay"), dict) else {}
        mfe_pct_values.append(_safe_float(excursion.get("mfe_pct"), default=0.0))
        mae_pct_values.append(_safe_float(excursion.get("mae_pct"), default=0.0))
        mfe_pnl_values.append(_safe_float(excursion.get("mfe_pnl"), default=0.0))
        mae_pnl_values.append(_safe_float(excursion.get("mae_pnl"), default=0.0))
    return (
        max(mfe_pct_values) if mfe_pct_values else 0.0,
        max(mae_pct_values) if mae_pct_values else 0.0,
        max(mfe_pnl_values) if mfe_pnl_values else 0.0,
        max(mae_pnl_values) if mae_pnl_values else 0.0,
    )


def _adverse_slippage_pct(*, side: str, requested_price: float, fill_price: float) -> float:
    if requested_price <= 0 or fill_price <= 0:
        return 0.0
    side_key = side.lower()
    if side_key == "buy":
        return max((fill_price - requested_price) / requested_price, 0.0)
    if side_key == "sell":
        return max((requested_price - fill_price) / requested_price, 0.0)
    return abs(fill_price - requested_price) / requested_price


def _execution_quality_snapshot(
    orders: list[Order],
    executions_by_order: dict[int, list[Execution]],
) -> tuple[float, float, float, int, int, float]:
    if not orders:
        return 0.0, 0.0, 0.0, 0, 0, 0.0

    primary_orders = [order for order in orders if not order.reduce_only and not order.close_only] or orders
    arrival_slippages: list[float] = []
    realized_slippages: list[float] = []
    first_fill_latencies: list[float] = []
    cancel_attempts = 0
    cancel_successes = 0

    for order_row in primary_orders:
        order_status = str(order_row.status or "").lower()
        if order_status in CANCEL_ATTEMPT_ORDER_STATUSES:
            cancel_attempts += 1
            if order_status in CANCEL_SUCCESS_ORDER_STATUSES:
                cancel_successes += 1

        metadata = order_row.metadata_json if isinstance(order_row.metadata_json, dict) else {}
        quality = metadata.get("execution_quality") if isinstance(metadata.get("execution_quality"), dict) else {}
        order_executions = sorted(
            executions_by_order.get(order_row.id, []),
            key=lambda item: (item.created_at, item.id),
        )

        if quality.get("arrival_slippage_pct") is not None:
            arrival_slippages.append(_safe_float(quality.get("arrival_slippage_pct"), default=0.0))
        elif order_executions:
            first_execution = order_executions[0]
            arrival_slippage = _safe_float(first_execution.slippage_pct, default=0.0)
            if arrival_slippage <= 0:
                arrival_slippage = _adverse_slippage_pct(
                    side=str(order_row.side or ""),
                    requested_price=_safe_float(order_row.requested_price, default=0.0),
                    fill_price=_safe_float(first_execution.fill_price, default=0.0),
                )
            arrival_slippages.append(arrival_slippage)

        if quality.get("realized_slippage_pct") is not None:
            realized_slippages.append(_safe_float(quality.get("realized_slippage_pct"), default=0.0))
        elif order_executions:
            weighted_quantity = sum(abs(_safe_float(item.fill_quantity, default=0.0)) for item in order_executions)
            if weighted_quantity > 0:
                realized_slippages.append(
                    sum(
                        abs(_safe_float(item.fill_quantity, default=0.0))
                        * _safe_float(item.slippage_pct, default=0.0)
                        for item in order_executions
                    )
                    / weighted_quantity
                )
            else:
                realized_slippages.append(
                    sum(_safe_float(item.slippage_pct, default=0.0) for item in order_executions)
                    / len(order_executions)
                )

        if quality.get("first_fill_latency_seconds") is not None:
            first_fill_latencies.append(max(_safe_float(quality.get("first_fill_latency_seconds"), default=0.0), 0.0))
        elif order_executions:
            first_fill_latencies.append(
                max((order_executions[0].created_at - order_row.created_at).total_seconds(), 0.0)
            )

    cancel_success_rate = (cancel_successes / cancel_attempts) if cancel_attempts else 0.0
    return (
        sum(arrival_slippages) / len(arrival_slippages) if arrival_slippages else 0.0,
        sum(realized_slippages) / len(realized_slippages) if realized_slippages else 0.0,
        sum(first_fill_latencies) / len(first_fill_latencies) if first_fill_latencies else 0.0,
        cancel_attempts,
        cancel_successes,
        cancel_success_rate,
    )


def _bucket_from_snapshots(key: str, snapshots: list[DecisionPerformanceSnapshot]) -> PerformanceAggregateEntry:
    if not snapshots:
        return PerformanceAggregateEntry(
            key=key,
            decisions=0,
            approvals=0,
            orders=0,
            fills=0,
            holds=0,
            longs=0,
            shorts=0,
            reduces=0,
            exits=0,
            wins=0,
            losses=0,
            realized_pnl_total=0.0,
            fee_total=0.0,
            net_realized_pnl_total=0.0,
            average_slippage_pct=0.0,
            average_arrival_slippage_pct=0.0,
            average_realized_slippage_pct=0.0,
            average_first_fill_latency_seconds=0.0,
            cancel_attempts=0,
            cancel_successes=0,
            cancel_success_rate=0.0,
            average_holding_minutes=0.0,
            holding_over_plan_count=0,
            open_positions=0,
            closed_positions=0,
            stop_loss_closes=0,
            take_profit_closes=0,
            manual_closes=0,
            unclassified_closes=0,
            latest_seen_at=utcnow_naive(),
        )
    slippages = [item.average_slippage_pct for item in snapshots if item.fills > 0]
    arrival_slippages = [item.arrival_slippage_pct for item in snapshots if item.orders > 0]
    realized_slippages = [item.realized_slippage_pct for item in snapshots if item.orders > 0]
    first_fill_latencies = [item.first_fill_latency_seconds for item in snapshots if item.first_fill_latency_seconds > 0]
    holdings = [item.holding_minutes_observed for item in snapshots if item.position_ids]
    cancel_attempts = sum(item.cancel_attempts for item in snapshots)
    cancel_successes = sum(item.cancel_successes for item in snapshots)
    return PerformanceAggregateEntry(
        key=key,
        decisions=len(snapshots),
        approvals=sum(1 for item in snapshots if item.approved),
        orders=sum(item.orders for item in snapshots),
        fills=sum(item.fills for item in snapshots),
        holds=sum(1 for item in snapshots if item.decision == "hold"),
        longs=sum(1 for item in snapshots if item.decision == "long"),
        shorts=sum(1 for item in snapshots if item.decision == "short"),
        reduces=sum(1 for item in snapshots if item.decision == "reduce"),
        exits=sum(1 for item in snapshots if item.decision == "exit"),
        wins=sum(item.wins for item in snapshots),
        losses=sum(item.losses for item in snapshots),
        realized_pnl_total=sum(item.realized_pnl_total for item in snapshots),
        fee_total=sum(item.fee_total for item in snapshots),
        net_realized_pnl_total=sum(item.net_realized_pnl_total for item in snapshots),
        average_slippage_pct=(sum(slippages) / len(slippages) if slippages else 0.0),
        average_arrival_slippage_pct=(sum(arrival_slippages) / len(arrival_slippages) if arrival_slippages else 0.0),
        average_realized_slippage_pct=(sum(realized_slippages) / len(realized_slippages) if realized_slippages else 0.0),
        average_first_fill_latency_seconds=(
            sum(first_fill_latencies) / len(first_fill_latencies) if first_fill_latencies else 0.0
        ),
        cancel_attempts=cancel_attempts,
        cancel_successes=cancel_successes,
        cancel_success_rate=(cancel_successes / cancel_attempts if cancel_attempts else 0.0),
        average_holding_minutes=(sum(holdings) / len(holdings) if holdings else 0.0),
        holding_over_plan_count=sum(item.holding_over_plan_count for item in snapshots),
        open_positions=sum(item.open_positions for item in snapshots),
        closed_positions=sum(item.closed_positions for item in snapshots),
        stop_loss_closes=sum(item.stop_loss_closes for item in snapshots),
        take_profit_closes=sum(item.take_profit_closes for item in snapshots),
        manual_closes=sum(item.manual_closes for item in snapshots),
        unclassified_closes=sum(item.unclassified_closes for item in snapshots),
        latest_seen_at=max(item.created_at for item in snapshots),
    )


def _decision_agrees_with_baseline(snapshot: DecisionPerformanceSnapshot) -> bool:
    if snapshot.baseline_decision is None:
        return False
    level = snapshot.decision_agreement_level.strip().lower()
    if level in {"full_agreement", "direction_match", "same_direction"}:
        return True
    if level:
        return snapshot.baseline_decision == snapshot.ai_decision
    return snapshot.baseline_decision == snapshot.ai_decision


def _ai_baseline_bucket_entry(
    bucket: str,
    snapshots: list[DecisionPerformanceSnapshot],
) -> AIBaselineComparisonBucket:
    observed = [item for item in snapshots if item.fills > 0]
    slippages = [item.average_slippage_pct for item in observed]
    holdings = [item.holding_minutes_observed for item in observed if item.holding_minutes_observed > 0]
    wins = sum(item.wins for item in snapshots)
    losses = sum(item.losses for item in snapshots)
    total_holding_hours = sum(item.holding_minutes_observed for item in observed if item.holding_minutes_observed > 0) / 60.0
    net_pnl_after_fees = sum(item.net_realized_pnl_total for item in snapshots)
    unobserved_reason_counts: dict[str, int] = defaultdict(int)
    for item in snapshots:
        if item.unobserved_reason:
            unobserved_reason_counts[item.unobserved_reason] += 1
    known_baseline = [item for item in snapshots if item.baseline_decision is not None]
    agreements = sum(1 for item in known_baseline if _decision_agrees_with_baseline(item))
    disagreements = len(known_baseline) - agreements
    return AIBaselineComparisonBucket(
        bucket=bucket,
        decisions=len(snapshots),
        approvals=sum(1 for item in snapshots if item.approved),
        orders=sum(item.orders for item in snapshots),
        fills=sum(item.fills for item in snapshots),
        wins=wins,
        losses=losses,
        win_rate=(wins / (wins + losses)) if (wins + losses) else 0.0,
        expectancy=(net_pnl_after_fees / len(observed)) if observed else 0.0,
        realized_pnl=sum(item.realized_pnl_total for item in snapshots),
        fee=sum(item.fee_total for item in snapshots),
        net_pnl_after_fees=net_pnl_after_fees,
        avg_slippage=(sum(slippages) / len(slippages) if slippages else 0.0),
        avg_holding_minutes=(sum(holdings) / len(holdings) if holdings else 0.0),
        pnl_per_exposure_hour=(net_pnl_after_fees / total_holding_hours if total_holding_hours > 0 else None),
        observed_decisions=len(observed),
        unobserved_decisions=len(snapshots) - len(observed),
        unobserved_reason_counts=dict(unobserved_reason_counts),
        baseline_entries=sum(1 for item in snapshots if item.baseline_decision in ENTRY_DECISIONS),
        baseline_holds=sum(1 for item in snapshots if item.baseline_decision == "hold"),
        ai_entries=sum(1 for item in snapshots if item.ai_used and item.ai_decision in ENTRY_DECISIONS),
        ai_holds=sum(1 for item in snapshots if item.ai_used and item.ai_decision == "hold"),
        ai_management_actions=sum(1 for item in snapshots if item.ai_used and item.ai_decision in MANAGEMENT_DECISIONS),
        agreements=agreements,
        disagreements=disagreements,
    )


def _build_ai_baseline_comparison(
    snapshots: list[DecisionPerformanceSnapshot],
) -> AIBaselineComparisonSummary:
    grouped: dict[str, list[DecisionPerformanceSnapshot]] = {bucket: [] for bucket in AI_BASELINE_BUCKET_ORDER}
    for snapshot in snapshots:
        grouped.setdefault(snapshot.comparison_bucket, []).append(snapshot)
    buckets = [
        _ai_baseline_bucket_entry(bucket, grouped.get(bucket, []))
        for bucket in AI_BASELINE_BUCKET_ORDER
    ]
    all_unobserved_reason_counts: dict[str, int] = defaultdict(int)
    for bucket in buckets:
        for reason, count in bucket.unobserved_reason_counts.items():
            all_unobserved_reason_counts[reason] += count
    rejected_bucket = next(
        (bucket for bucket in buckets if bucket.bucket == "ai_rejected_baseline_entry"),
        None,
    )
    rejected_observed_net = (
        rejected_bucket.net_pnl_after_fees
        if rejected_bucket is not None and rejected_bucket.observed_decisions > 0
        else 0.0
    )
    rejected_filter_value = (
        -rejected_observed_net
        if rejected_bucket is not None and rejected_bucket.observed_decisions > 0
        else None
    )
    return AIBaselineComparisonSummary(
        buckets=buckets,
        bucket_totals={bucket.bucket: bucket.decisions for bucket in buckets},
        decisions=sum(bucket.decisions for bucket in buckets),
        agreements=sum(bucket.agreements for bucket in buckets),
        disagreements=sum(bucket.disagreements for bucket in buckets),
        baseline_entries=sum(bucket.baseline_entries for bucket in buckets),
        ai_entries=sum(bucket.ai_entries for bucket in buckets),
        ai_holds=sum(bucket.ai_holds for bucket in buckets),
        ai_management_actions=sum(bucket.ai_management_actions for bucket in buckets),
        observed_decisions=sum(bucket.observed_decisions for bucket in buckets),
        unobserved_decisions=sum(bucket.unobserved_decisions for bucket in buckets),
        unobserved_reason_counts=dict(all_unobserved_reason_counts),
        observed_net_pnl_after_fees=sum(bucket.net_pnl_after_fees for bucket in buckets),
        observed_rejected_baseline_entry_net_pnl_after_fees=rejected_observed_net,
        ai_filter_observed_value_net_pnl_after_fees=rejected_filter_value,
    )


def _build_limited_live_readiness(
    session: Session,
    *,
    since: datetime,
    window_label: str,
    window_hours: int,
    decision_items: list[DecisionPerformanceSnapshot],
    summary: PerformanceWindowSummary,
    ai_telemetry: AIUsageTelemetrySummary | Mapping[str, object],
    ai_baseline_comparison: AIBaselineComparisonSummary,
    safety_event_cache: ReadinessSafetyEventCache | None = None,
    pnl_snapshot_cache: PnLSnapshotWindowCache | None = None,
) -> LimitedLiveReadinessReport:
    def telemetry_count(key: str) -> int:
        if isinstance(ai_telemetry, Mapping):
            return int(ai_telemetry.get(key, 0) or 0)
        return int(getattr(ai_telemetry, key, 0) or 0)

    actual_entries = sum(
        1
        for item in decision_items
        if item.decision in ENTRY_DECISIONS and item.orders > 0
    )
    observed_entry_decisions = [
        item
        for item in decision_items
        if item.decision in ENTRY_DECISIONS and item.fills > 0
    ]
    expectancy_after_fees = (
        sum(item.net_realized_pnl_total for item in observed_entry_decisions) / len(observed_entry_decisions)
        if observed_entry_decisions
        else 0.0
    )
    protection_failure_count, unknown_submission_count, stale_incomplete_data_block_count = (
        _count_limited_live_safety_events(session, since=since, safety_event_cache=safety_event_cache)
    )
    max_drawdown = _max_drawdown_from_pnl_snapshots(session, since, pnl_snapshot_cache)
    consecutive_losses = _max_consecutive_losses_from_pnl_snapshots(session, since, pnl_snapshot_cache)
    ai_filter_value = ai_baseline_comparison.ai_filter_observed_value_net_pnl_after_fees

    reason_codes: list[str] = []
    if len(decision_items) < READINESS_MIN_CANDIDATE_EVENTS or actual_entries < READINESS_MIN_ACTUAL_ENTRIES:
        reason_codes.append("insufficient_sample")
    if observed_entry_decisions and expectancy_after_fees < 0:
        reason_codes.append("negative_expectancy")
    if max_drawdown > READINESS_MAX_DRAWDOWN:
        reason_codes.append("excessive_drawdown")
    if protection_failure_count > 0:
        reason_codes.append("protection_failures")
    if unknown_submission_count > 0:
        reason_codes.append("execution_unknowns")
    stale_frequency_high = (
        stale_incomplete_data_block_count >= READINESS_STALE_BLOCK_COUNT_HIGH
        and len(decision_items) > 0
        and stale_incomplete_data_block_count / len(decision_items) >= READINESS_STALE_BLOCK_RATIO_HIGH
    )
    if stale_frequency_high:
        reason_codes.append("stale_data_frequency_high")
    if ai_filter_value is not None and ai_filter_value < 0:
        reason_codes.append("ai_filter_underperforming")

    hard_block_reasons = {
        "negative_expectancy",
        "excessive_drawdown",
        "protection_failures",
        "execution_unknowns",
        "ai_filter_underperforming",
    }
    if any(reason in hard_block_reasons for reason in reason_codes):
        status = "blocked"
    elif "insufficient_sample" in reason_codes:
        status = "not_ready"
    elif "stale_data_frequency_high" in reason_codes:
        status = "watch"
    elif (
        len(decision_items) >= READINESS_SCALE_UP_MIN_CANDIDATE_EVENTS
        and actual_entries >= READINESS_SCALE_UP_MIN_ACTUAL_ENTRIES
        and expectancy_after_fees > 0
    ):
        status = "scale_up_candidate"
    elif (
        len(decision_items) >= READINESS_MIN_CANDIDATE_EVENTS
        and actual_entries >= READINESS_MIN_ACTUAL_ENTRIES
        and expectancy_after_fees > 0
    ):
        status = "limited_live_candidate"
    else:
        status = "watch"

    return LimitedLiveReadinessReport(
        window_label=window_label,
        window_hours=window_hours,
        status=status,
        reason_codes=reason_codes,
        read_only=True,
        recent_candidate_events=len(decision_items),
        ai_calls_total=telemetry_count("ai_calls_total"),
        ai_calls_provider_invoked=telemetry_count("ai_calls_provider_invoked"),
        ai_calls_skipped_preai=telemetry_count("ai_calls_skipped_preai"),
        actual_entries=actual_entries,
        fills=summary.fills,
        expectancy_after_fees=expectancy_after_fees,
        net_pnl_after_fees=summary.net_realized_pnl_total,
        max_drawdown=max_drawdown,
        consecutive_losses=consecutive_losses,
        protection_failure_count=protection_failure_count,
        unknown_submission_count=unknown_submission_count,
        stale_incomplete_data_block_count=stale_incomplete_data_block_count,
        ai_filter_observed_value_net_pnl_after_fees=ai_filter_value,
        thresholds={
            "min_candidate_events": READINESS_MIN_CANDIDATE_EVENTS,
            "min_actual_entries": READINESS_MIN_ACTUAL_ENTRIES,
            "scale_up_min_candidate_events": READINESS_SCALE_UP_MIN_CANDIDATE_EVENTS,
            "scale_up_min_actual_entries": READINESS_SCALE_UP_MIN_ACTUAL_ENTRIES,
            "max_drawdown": READINESS_MAX_DRAWDOWN,
            "stale_block_ratio_high": READINESS_STALE_BLOCK_RATIO_HIGH,
            "stale_block_count_high": READINESS_STALE_BLOCK_COUNT_HIGH,
        },
    )


def _build_window_report(
    session: Session,
    *,
    window_label: str,
    window_hours: int,
    aggregate_limit: int,
    decision_limit: int,
    safety_event_cache: ReadinessSafetyEventCache | None = None,
    decision_contexts: Sequence[DecisionPerformanceContext] | None = None,
    pnl_snapshot_cache: PnLSnapshotWindowCache | None = None,
) -> PerformanceWindowReport:
    since = utcnow_naive() - timedelta(hours=window_hours)
    now = utcnow_naive()
    if decision_contexts is None:
        decision_rows = _load_decision_performance_contexts(session, since)
    else:
        decision_rows = [row for row in decision_contexts if row.created_at >= since]
    decision_ids = [row.id for row in decision_rows]

    risk_by_decision: dict[int, RiskCheck] = {}
    if decision_ids:
        for risk_row in session.scalars(
            select(RiskCheck)
            .where(RiskCheck.decision_run_id.in_(decision_ids))
            .order_by(RiskCheck.created_at.desc())
        ):
            if risk_row.decision_run_id is not None and risk_row.decision_run_id not in risk_by_decision:
                risk_by_decision[risk_row.decision_run_id] = risk_row

    orders_by_decision: dict[int, list[Order]] = defaultdict(list)
    order_ids: list[int] = []
    position_ids: set[int] = set()
    if decision_ids:
        for order_row in session.scalars(
            select(Order)
            .where(Order.decision_run_id.in_(decision_ids))
            .order_by(Order.created_at.desc())
        ):
            if order_row.decision_run_id is not None:
                orders_by_decision[order_row.decision_run_id].append(order_row)
            order_ids.append(order_row.id)
            if order_row.position_id is not None:
                position_ids.add(order_row.position_id)

    executions_by_order: dict[int, list[Execution]] = defaultdict(list)
    if order_ids:
        for execution_row in session.scalars(
            select(Execution)
            .where(Execution.order_id.in_(order_ids))
            .order_by(Execution.created_at.desc())
        ):
            if execution_row.order_id is not None:
                executions_by_order[execution_row.order_id].append(execution_row)

    positions_by_id: dict[int, Position] = {}
    if position_ids:
        for position_row in session.scalars(select(Position).where(Position.id.in_(position_ids))):
            positions_by_id[position_row.id] = position_row

    decision_items: list[DecisionPerformanceSnapshot] = []
    rationale_groups: dict[str, list[DecisionPerformanceSnapshot]] = defaultdict(list)
    symbol_groups: dict[str, list[DecisionPerformanceSnapshot]] = defaultdict(list)
    timeframe_groups: dict[str, list[DecisionPerformanceSnapshot]] = defaultdict(list)
    regime_groups: dict[str, list[DecisionPerformanceSnapshot]] = defaultdict(list)
    trend_groups: dict[str, list[DecisionPerformanceSnapshot]] = defaultdict(list)
    direction_groups: dict[str, list[DecisionPerformanceSnapshot]] = defaultdict(list)
    hold_condition_groups: dict[str, list[DecisionPerformanceSnapshot]] = defaultdict(list)
    close_outcome_groups: dict[str, list[DecisionPerformanceSnapshot]] = defaultdict(list)
    flag_groups: dict[str, dict[bool, list[DecisionPerformanceSnapshot]]] = {
        "weak_volume": defaultdict(list),
        "volatility_expanded": defaultdict(list),
        "momentum_weakening": defaultdict(list),
    }

    for decision_row in decision_rows:
        rationale_codes = decision_row.rationale_codes or ["UNSPECIFIED"]
        symbol = decision_row.symbol
        timeframe = decision_row.timeframe
        decision = decision_row.decision
        baseline_decision = decision_row.baseline_decision
        ai_used = decision_row.ai_used
        comparison_bucket = decision_row.comparison_bucket
        regime = decision_row.regime
        trend_alignment = decision_row.trend_alignment
        weak_volume = decision_row.weak_volume
        volatility_expanded = decision_row.volatility_expanded
        momentum_weakening = decision_row.momentum_weakening
        planned_holding_minutes = decision_row.max_holding_minutes
        stop_loss = decision_row.stop_loss
        take_profit = decision_row.take_profit

        linked_risk = risk_by_decision.get(decision_row.id)
        linked_orders = orders_by_decision.get(decision_row.id, [])
        linked_executions = [
            execution_row
            for order_row in linked_orders
            for execution_row in executions_by_order.get(order_row.id, [])
        ]
        linked_position_ids = sorted(
            {
                order_row.position_id
                for order_row in linked_orders
                if order_row.position_id is not None and order_row.position_id in positions_by_id
            }
        )
        linked_positions = [positions_by_id[position_id] for position_id in linked_position_ids]
        mfe_pct, mae_pct, mfe_pnl, mae_pnl = _position_excursion_snapshot(linked_positions)
        arrival_slippage_pct, realized_slippage_pct, first_fill_latency_seconds, cancel_attempts, cancel_successes, _cancel_success_rate = _execution_quality_snapshot(
            linked_orders,
            executions_by_order,
        )
        holding_minutes_observed, holding_result_status, open_positions, closed_positions, holding_over_plan_count = _holding_snapshot(
            linked_positions,
            planned_max_holding_minutes=planned_holding_minutes,
            now=now,
        )
        stop_loss_closes = 0
        take_profit_closes = 0
        manual_closes = 0
        unclassified_closes = 0
        for order_row in linked_orders:
            execution_count = len(executions_by_order.get(order_row.id, []))
            if execution_count == 0:
                continue
            order_type = str(order_row.order_type or "").upper()
            if order_type.startswith("STOP"):
                stop_loss_closes += execution_count
            elif order_type.startswith("TAKE_PROFIT"):
                take_profit_closes += execution_count
            elif order_row.reduce_only or order_row.close_only:
                manual_closes += execution_count
        if closed_positions > 0 and (stop_loss_closes + take_profit_closes + manual_closes) == 0:
            unclassified_closes = max(closed_positions, 1)
        realized_total = sum(_safe_float(execution_row.realized_pnl) for execution_row in linked_executions)
        fee_total = sum(_safe_float(execution_row.fee_paid) for execution_row in linked_executions)
        net_realized_total = realized_total - fee_total
        slippages = [_safe_float(execution_row.slippage_pct) for execution_row in linked_executions]
        fill_count = len(linked_executions)
        wins = sum(1 for execution_row in linked_executions if (_safe_float(execution_row.realized_pnl) - _safe_float(execution_row.fee_paid)) > 0)
        losses = sum(1 for execution_row in linked_executions if (_safe_float(execution_row.realized_pnl) - _safe_float(execution_row.fee_paid)) < 0)
        unobserved_reason = _unobserved_reason(
            snapshot_bucket=comparison_bucket,
            decision=decision,
            fills=fill_count,
            orders=len(linked_orders),
            linked_risk=linked_risk,
        )
        snapshot = DecisionPerformanceSnapshot(
            decision_run_id=decision_row.id,
            created_at=decision_row.created_at,
            symbol=symbol,
            timeframe=timeframe,
            decision=decision,
            regime=regime,
            trend_alignment=trend_alignment,
            weak_volume=weak_volume,
            volatility_expanded=volatility_expanded,
            momentum_weakening=momentum_weakening,
            rationale_codes=rationale_codes,
            approved=bool(linked_risk.allowed) if linked_risk is not None else False,
            approved_risk_pct=_safe_float(linked_risk.approved_risk_pct) if linked_risk is not None else 0.0,
            approved_leverage=_safe_float(linked_risk.approved_leverage) if linked_risk is not None else 0.0,
            orders=len(linked_orders),
            fills=fill_count,
            wins=wins,
            losses=losses,
            realized_pnl_total=realized_total,
            fee_total=fee_total,
            net_realized_pnl_total=net_realized_total,
            average_slippage_pct=(sum(slippages) / len(slippages) if slippages else 0.0),
            arrival_slippage_pct=arrival_slippage_pct,
            realized_slippage_pct=realized_slippage_pct,
            first_fill_latency_seconds=first_fill_latency_seconds,
            cancel_attempts=cancel_attempts,
            cancel_successes=cancel_successes,
            max_holding_minutes_planned=planned_holding_minutes,
            holding_minutes_observed=holding_minutes_observed,
            holding_result_status=holding_result_status,
            stop_loss=stop_loss,
            take_profit=take_profit,
            planned_risk_reward_ratio=_planned_risk_reward_ratio(
                decision=decision,
                entry_zone_min=decision_row.entry_zone_min,
                entry_zone_max=decision_row.entry_zone_max,
                stop_loss=stop_loss,
                take_profit=take_profit,
            ),
            close_outcome=_close_outcome_from_counts(
                stop_loss_closes=stop_loss_closes,
                take_profit_closes=take_profit_closes,
                manual_closes=manual_closes,
                unclassified_closes=unclassified_closes,
                open_positions=open_positions,
                closed_positions=closed_positions,
            ),
            stop_loss_closes=stop_loss_closes,
            take_profit_closes=take_profit_closes,
            manual_closes=manual_closes,
            unclassified_closes=unclassified_closes,
            open_positions=open_positions,
            closed_positions=closed_positions,
            holding_over_plan_count=holding_over_plan_count,
            position_ids=linked_position_ids,
            mfe_pct=mfe_pct,
            mae_pct=mae_pct,
            mfe_pnl=mfe_pnl,
            mae_pnl=mae_pnl,
            baseline_decision=baseline_decision,
            ai_decision=decision,
            decision_agreement_level=decision_row.decision_agreement_level,
            decision_agreement_source=decision_row.decision_agreement_source,
            ai_used=ai_used,
            comparison_bucket=comparison_bucket,
            unobserved_reason=unobserved_reason,
            pnl_per_exposure_hour=_pnl_per_exposure_hour(
                net_realized_total,
                holding_minutes_observed,
                fill_count,
            ),
        )
        decision_items.append(snapshot)
        for rationale_code in rationale_codes:
            rationale_groups[rationale_code].append(snapshot)
        symbol_groups[symbol].append(snapshot)
        timeframe_groups[timeframe].append(snapshot)
        regime_groups[regime].append(snapshot)
        trend_groups[trend_alignment].append(snapshot)
        direction_groups[decision].append(snapshot)
        close_outcome_groups[snapshot.close_outcome].append(snapshot)
        flag_groups["weak_volume"][weak_volume].append(snapshot)
        flag_groups["volatility_expanded"][volatility_expanded].append(snapshot)
        flag_groups["momentum_weakening"][momentum_weakening].append(snapshot)
        if decision == "hold":
            hold_condition_groups[
                _hold_condition_key(
                    regime=regime,
                    trend_alignment=trend_alignment,
                    weak_volume=weak_volume,
                    volatility_expanded=volatility_expanded,
                    momentum_weakening=momentum_weakening,
                )
            ].append(snapshot)

    rationale_items = [
        _bucket_from_snapshots(key, snapshots)
        for key, snapshots in rationale_groups.items()
    ]
    symbol_items = [
        _bucket_from_snapshots(key, snapshots)
        for key, snapshots in symbol_groups.items()
    ]
    timeframe_items = [
        _bucket_from_snapshots(key, snapshots)
        for key, snapshots in timeframe_groups.items()
    ]
    regime_items = [_bucket_from_snapshots(key, snapshots) for key, snapshots in regime_groups.items()]
    trend_items = [_bucket_from_snapshots(key, snapshots) for key, snapshots in trend_groups.items()]
    direction_items = [_bucket_from_snapshots(key, snapshots) for key, snapshots in direction_groups.items()]
    hold_condition_items = [
        _bucket_from_snapshots(key, snapshots) for key, snapshots in hold_condition_groups.items()
    ]
    close_outcome_items = [
        _bucket_from_snapshots(key, snapshots) for key, snapshots in close_outcome_groups.items()
    ]
    flag_items = [
        FeatureFlagPerformanceEntry(
            flag_name=flag_name,
            enabled=_bucket_from_snapshots(f"{flag_name}=on", grouped.get(True, [])),
            disabled=_bucket_from_snapshots(f"{flag_name}=off", grouped.get(False, [])),
        )
        for flag_name, grouped in flag_groups.items()
        if grouped.get(True) or grouped.get(False)
    ]
    rationale_items.sort(key=lambda item: (item.net_realized_pnl_total, item.fills, item.decisions), reverse=True)
    symbol_items.sort(key=lambda item: (item.net_realized_pnl_total, item.fills, item.decisions), reverse=True)
    timeframe_items.sort(key=lambda item: (item.net_realized_pnl_total, item.fills, item.decisions), reverse=True)
    regime_items.sort(key=lambda item: (item.net_realized_pnl_total, item.decisions, item.fills), reverse=True)
    trend_items.sort(key=lambda item: (item.net_realized_pnl_total, item.decisions, item.fills), reverse=True)
    direction_items.sort(key=lambda item: (item.net_realized_pnl_total, item.decisions, item.fills), reverse=True)
    hold_condition_items.sort(key=lambda item: (item.holds, item.decisions, item.latest_seen_at), reverse=True)
    close_outcome_items.sort(key=lambda item: (item.decisions, item.net_realized_pnl_total), reverse=True)
    decision_items.sort(key=lambda item: (item.created_at, item.net_realized_pnl_total), reverse=True)

    overall_slippages = [item.average_slippage_pct for item in decision_items if item.fills > 0]
    overall_arrival_slippages = [item.arrival_slippage_pct for item in decision_items if item.orders > 0]
    overall_realized_slippages = [item.realized_slippage_pct for item in decision_items if item.orders > 0]
    overall_first_fill_latencies = [item.first_fill_latency_seconds for item in decision_items if item.first_fill_latency_seconds > 0]
    overall_holdings = [item.holding_minutes_observed for item in decision_items if item.position_ids]
    overall_mfe = [item.mfe_pct for item in decision_items if item.position_ids]
    overall_mae = [item.mae_pct for item in decision_items if item.position_ids]
    overall_cancel_attempts = sum(item.cancel_attempts for item in decision_items)
    overall_cancel_successes = sum(item.cancel_successes for item in decision_items)
    summary = PerformanceWindowSummary(
        decisions=len(decision_items),
        approvals=sum(1 for item in decision_items if item.approved),
        orders=sum(item.orders for item in decision_items),
        fills=sum(item.fills for item in decision_items),
        holds=sum(1 for item in decision_items if item.decision == "hold"),
        longs=sum(1 for item in decision_items if item.decision == "long"),
        shorts=sum(1 for item in decision_items if item.decision == "short"),
        reduces=sum(1 for item in decision_items if item.decision == "reduce"),
        exits=sum(1 for item in decision_items if item.decision == "exit"),
        wins=sum(item.wins for item in decision_items),
        losses=sum(item.losses for item in decision_items),
        realized_pnl_total=sum(item.realized_pnl_total for item in decision_items),
        fee_total=sum(item.fee_total for item in decision_items),
        net_realized_pnl_total=sum(item.net_realized_pnl_total for item in decision_items),
        average_slippage_pct=(sum(overall_slippages) / len(overall_slippages) if overall_slippages else 0.0),
        average_arrival_slippage_pct=(
            sum(overall_arrival_slippages) / len(overall_arrival_slippages) if overall_arrival_slippages else 0.0
        ),
        average_realized_slippage_pct=(
            sum(overall_realized_slippages) / len(overall_realized_slippages) if overall_realized_slippages else 0.0
        ),
        average_first_fill_latency_seconds=(
            sum(overall_first_fill_latencies) / len(overall_first_fill_latencies)
            if overall_first_fill_latencies
            else 0.0
        ),
        cancel_attempts=overall_cancel_attempts,
        cancel_successes=overall_cancel_successes,
        cancel_success_rate=(
            overall_cancel_successes / overall_cancel_attempts if overall_cancel_attempts else 0.0
        ),
        average_holding_minutes=(sum(overall_holdings) / len(overall_holdings) if overall_holdings else 0.0),
        holding_over_plan_count=sum(item.holding_over_plan_count for item in decision_items),
        open_positions=sum(item.open_positions for item in decision_items),
        closed_positions=sum(item.closed_positions for item in decision_items),
        stop_loss_closes=sum(item.stop_loss_closes for item in decision_items),
        take_profit_closes=sum(item.take_profit_closes for item in decision_items),
        manual_closes=sum(item.manual_closes for item in decision_items),
        unclassified_closes=sum(item.unclassified_closes for item in decision_items),
        snapshot_net_pnl_estimate=_snapshot_net_pnl_estimate(session, since, pnl_snapshot_cache),
        average_mfe_pct=(sum(overall_mfe) / len(overall_mfe) if overall_mfe else 0.0),
        average_mae_pct=(sum(overall_mae) / len(overall_mae) if overall_mae else 0.0),
        best_mfe_pct=max(overall_mfe) if overall_mfe else 0.0,
        worst_mae_pct=max(overall_mae) if overall_mae else 0.0,
    )
    ai_telemetry = build_ai_telemetry_summary(
        session,
        decision_rows,
        deduped_count=count_ai_deduped_events(session, since),
    )
    ai_baseline_comparison = _build_ai_baseline_comparison(decision_items)
    limited_live_readiness = _build_limited_live_readiness(
        session,
        since=since,
        window_label=window_label,
        window_hours=window_hours,
        decision_items=decision_items,
        summary=summary,
        ai_telemetry=ai_telemetry,
        ai_baseline_comparison=ai_baseline_comparison,
        safety_event_cache=safety_event_cache,
        pnl_snapshot_cache=pnl_snapshot_cache,
    )

    return PerformanceWindowReport(
        window_label=window_label,
        window_hours=window_hours,
        summary=summary,
        ai_telemetry=ai_telemetry,
        ai_baseline_comparison=ai_baseline_comparison,
        limited_live_readiness=limited_live_readiness,
        decisions=[
            DecisionPerformanceEntry(
                decision_run_id=item.decision_run_id,
                created_at=item.created_at,
                symbol=item.symbol,
                timeframe=item.timeframe,
                decision=item.decision,
                regime=item.regime,
                trend_alignment=item.trend_alignment,
                weak_volume=item.weak_volume,
                volatility_expanded=item.volatility_expanded,
                momentum_weakening=item.momentum_weakening,
                rationale_codes=item.rationale_codes,
                approved=item.approved,
                approved_risk_pct=item.approved_risk_pct,
                approved_leverage=item.approved_leverage,
                orders=item.orders,
                fills=item.fills,
                wins=item.wins,
                losses=item.losses,
                realized_pnl_total=item.realized_pnl_total,
                fee_total=item.fee_total,
                net_realized_pnl_total=item.net_realized_pnl_total,
                average_slippage_pct=item.average_slippage_pct,
                arrival_slippage_pct=item.arrival_slippage_pct,
                realized_slippage_pct=item.realized_slippage_pct,
                first_fill_latency_seconds=item.first_fill_latency_seconds,
                cancel_attempts=item.cancel_attempts,
                cancel_successes=item.cancel_successes,
                cancel_success_rate=(
                    item.cancel_successes / item.cancel_attempts if item.cancel_attempts else 0.0
                ),
                max_holding_minutes_planned=item.max_holding_minutes_planned,
                holding_minutes_observed=item.holding_minutes_observed,
                holding_result_status=item.holding_result_status,
                stop_loss=item.stop_loss,
                take_profit=item.take_profit,
                planned_risk_reward_ratio=item.planned_risk_reward_ratio,
                close_outcome=item.close_outcome,
                position_ids=item.position_ids,
                mfe_pct=item.mfe_pct,
                mae_pct=item.mae_pct,
                mfe_pnl=item.mfe_pnl,
                mae_pnl=item.mae_pnl,
            )
            for item in decision_items[:decision_limit]
        ],
        rationale_codes=rationale_items[:aggregate_limit],
        symbols=symbol_items[:aggregate_limit],
        timeframes=timeframe_items[:aggregate_limit],
        regimes=regime_items[:aggregate_limit],
        trend_alignments=trend_items[:aggregate_limit],
        directions=direction_items[:aggregate_limit],
        hold_conditions=hold_condition_items[:aggregate_limit],
        close_outcomes=close_outcome_items[:aggregate_limit],
        feature_flags=flag_items,
    )


def _performance_report_db_identity(session: Session) -> str:
    bind = session.get_bind()
    return str(bind.url) if bind is not None else "unknown"


def _latest_scalar(session: Session, statement: Any) -> object:
    return session.scalar(statement)


def _signal_performance_source_key(session: Session) -> tuple[object, ...]:
    safety_event_types = tuple(READINESS_AUDIT_SAFETY_EVENT_TYPES)
    latest_safety_audit = None
    if safety_event_types:
        latest_safety_audit = _latest_scalar(
            session,
            select(func.max(AuditEvent.created_at)).where(AuditEvent.event_type.in_(safety_event_types)),
        )
    return (
        _latest_scalar(
            session,
            select(func.max(AgentRun.id)).where(AgentRun.role == "trading_decision"),
        ),
        _latest_scalar(session, select(func.max(DecisionPerformanceFact.id))),
        _latest_scalar(session, select(func.max(RiskCheck.id))),
        _latest_scalar(session, select(func.max(Order.id))),
        _latest_scalar(session, select(func.max(Execution.id))),
        _latest_scalar(session, select(func.max(PnLSnapshot.created_at))),
        latest_safety_audit,
    )


def build_signal_performance_report(
    session: Session,
    *,
    window_hours: int = 24,
    limit: int = 12,
    window_specs: Sequence[tuple[str, int]] | None = None,
) -> SignalPerformanceReportResponse:
    selected_window_specs = tuple(window_specs or DEFAULT_SIGNAL_PERFORMANCE_WINDOW_SPECS)
    cache_key = (
        _performance_report_db_identity(session),
        window_hours,
        limit,
        selected_window_specs,
    )
    source_key = _signal_performance_source_key(session)
    now_monotonic = monotonic()
    with _signal_performance_report_cache_lock:
        cached = _signal_performance_report_cache.get(cache_key)
        if (
            cached is not None
            and cached.source_key == source_key
            and now_monotonic - cached.stored_at <= SIGNAL_PERFORMANCE_REPORT_CACHE_TTL_SECONDS
        ):
            return cached.payload.model_copy(deep=True)

    max_window_hours = max((window_hour_count for _window_label, window_hour_count in selected_window_specs), default=window_hours)
    max_since = utcnow_naive() - timedelta(hours=max_window_hours)
    decision_contexts = _load_decision_performance_contexts(session, max_since)
    safety_event_cache = _load_limited_live_safety_event_cache(
        session,
        max_since,
    )
    pnl_snapshot_cache = _load_pnl_snapshot_cache(session, max_since)
    windows = [
        _build_window_report(
            session,
            window_label=window_label,
            window_hours=window_hour_count,
            aggregate_limit=limit,
            decision_limit=limit,
            safety_event_cache=safety_event_cache,
            decision_contexts=decision_contexts,
            pnl_snapshot_cache=pnl_snapshot_cache,
        )
        for window_label, window_hour_count in selected_window_specs
    ]
    primary_window = next((item for item in windows if item.window_hours == window_hours), windows[0])
    items = [
        SignalPerformanceEntry(
            rationale_code=item.key,
            decisions=item.decisions,
            approvals=item.approvals,
            orders=item.orders,
            fills=item.fills,
            holds=item.holds,
            longs=item.longs,
            shorts=item.shorts,
            reduces=item.reduces,
            exits=item.exits,
            wins=item.wins,
            losses=item.losses,
            realized_pnl_total=item.realized_pnl_total,
            fee_total=item.fee_total,
            net_realized_pnl_total=item.net_realized_pnl_total,
            average_slippage_pct=item.average_slippage_pct,
            average_arrival_slippage_pct=item.average_arrival_slippage_pct,
            average_realized_slippage_pct=item.average_realized_slippage_pct,
            average_first_fill_latency_seconds=item.average_first_fill_latency_seconds,
            cancel_attempts=item.cancel_attempts,
            cancel_successes=item.cancel_successes,
            cancel_success_rate=item.cancel_success_rate,
            average_holding_minutes=item.average_holding_minutes,
            holding_over_plan_count=item.holding_over_plan_count,
            open_positions=item.open_positions,
            closed_positions=item.closed_positions,
            latest_seen_at=item.latest_seen_at,
        )
        for item in primary_window.rationale_codes
    ]
    payload = SignalPerformanceReportResponse(
        generated_at=utcnow_naive(),
        window_hours=primary_window.window_hours,
        items=items[:limit],
        windows=windows,
    )
    with _signal_performance_report_cache_lock:
        if len(_signal_performance_report_cache) > 16:
            _signal_performance_report_cache.clear()
        _signal_performance_report_cache[cache_key] = _CachedSignalPerformanceReport(
            stored_at=monotonic(),
            source_key=source_key,
            payload=payload.model_copy(deep=True),
        )
    return payload


def _categorize_competitor_note(note: CompetitorNote) -> tuple[str, str]:
    haystack = " ".join([note.source, note.note, *note.tags]).lower()
    if any(token in haystack for token in ("dashboard", "ui", "ux", "layout", "화면", "대시보드")):
        return "dashboard", "운영 화면의 정보 구조와 모니터링 흐름을 강화하는 방향"
    if any(token in haystack for token in ("risk", "guard", "stop", "loss", "리스크", "보호")):
        return "risk", "리스크 통제와 보호 주문 체계를 강화하는 방향"
    if any(token in haystack for token in ("alert", "notification", "알림", "공지")):
        return "alerting", "중요 이벤트 감지와 운영 대응 속도를 높이는 방향"
    if any(token in haystack for token in ("execution", "order", "fill", "slippage", "체결", "주문")):
        return "execution", "주문 체결과 실행 품질 추적을 강화하는 방향"
    if any(token in haystack for token in ("ai", "signal", "model", "agent", "신호", "에이전트")):
        return "signal-ai", "AI 신호와 해석 가시성을 강화하는 방향"
    return "general", "제품 차별점과 운영 관찰 포인트를 보강하는 방향"


def _summarize_note(note: str, max_length: int = 120) -> str:
    compact = " ".join(note.split())
    if len(compact) <= max_length:
        return compact
    return compact[: max_length - 1].rstrip() + "…"


def build_structured_competitor_notes(
    session: Session,
    *,
    limit: int = 20,
) -> StructuredCompetitorNotesResponse:
    rows = list(session.scalars(select(CompetitorNote).order_by(CompetitorNote.created_at.desc()).limit(limit)))
    items: list[StructuredCompetitorNote] = []
    category_breakdown: dict[str, int] = defaultdict(int)

    for row in rows:
        category, differentiation = _categorize_competitor_note(row)
        category_breakdown[category] += 1
        items.append(
            StructuredCompetitorNote(
                id=row.id,
                source=row.source,
                category=category,
                differentiation=differentiation,
                summary=_summarize_note(row.note),
                tags=list(row.tags),
                created_at=row.created_at,
            )
        )

    items.sort(key=lambda item: item.created_at, reverse=True)
    return StructuredCompetitorNotesResponse(
        generated_at=utcnow_naive(),
        category_breakdown=dict(sorted(category_breakdown.items(), key=lambda item: item[0])),
        items=items,
    )
