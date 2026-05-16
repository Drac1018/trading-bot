from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import String, cast, desc, func, or_, select
from sqlalchemy.orm import Session

from trading_mvp.config import get_settings
from trading_mvp.models import (
    AccountLedgerEntry,
    AgentRun,
    Alert,
    AuditEvent,
    DecisionPerformanceFact,
    Execution,
    FeatureSnapshot,
    MarketSnapshot,
    Order,
    PendingEntryPlan,
    PnLSnapshot,
    Position,
    RiskCheck,
    SchedulerRun,
    Setting,
)
from trading_mvp.schemas import (
    AnalyticsCostBreakdownBucket,
    AnalyticsCostBreakdownDataQuality,
    AnalyticsCostBreakdownResponse,
    AnalyticsCostBreakdownSummary,
    AuditTimelineEntry,
    DashboardExecutionProfileSummary,
    DashboardExecutionWindowSummary,
    DashboardHoldBlockedSummary,
    DashboardProfitabilityCostBreakdown,
    DashboardProfitabilityResponse,
    DashboardProfitabilityWindow,
    DecisionReferencePayload,
    LimitedLiveReadinessReport,
    OperatorAIReviewSnapshot,
    OperatorCandidateSelectionSnapshot,
    OperatorControlState,
    OperatorDashboardResponse,
    OperatorDecisionEventContextSnapshot,
    OperatorDecisionSnapshot,
    OperatorExecutionFillSummary,
    OperatorExecutionSnapshot,
    OperatorMarketSignalSnapshot,
    OperatorMarketSignalSummary,
    OperatorPositionSummary,
    OperatorProtectionSummary,
    OperatorRiskGuardResultSnapshot,
    OperatorRiskSnapshot,
    OperatorSymbolSummary,
    OverviewResponse,
    PendingEntryPlanSnapshot,
    PerformanceAggregateEntry,
    PerformanceWindowSummary,
)
from trading_mvp.services.audit import compact_audit_payload
from trading_mvp.services.intent_semantics import infer_intent_semantics
from trading_mvp.services.pending_entry_time import pending_entry_expiry_context
from trading_mvp.services.performance_reporting import build_signal_performance_report
from trading_mvp.services.runtime_state import (
    PROTECTION_REQUIRED_STATE,
    build_sync_freshness_summary,
    derive_degraded_reason_codes,
    derive_protection_reason_codes,
    summarize_runtime_state,
)
from trading_mvp.services.service_gate import active_pending_entry_plan_statement
from trading_mvp.services.settings import (
    AI_MARKET_SETTINGS_ADVISOR_DETAIL_KEY,
    SAFE_PROFILE_SELECTOR_DETAIL_KEY,
    build_event_operator_control_payload,
    build_operational_status_payload,
    extract_freshest_raw_event_context,
    get_effective_symbols,
    get_execution_risk_profile_policy,
    get_or_create_settings,
    serialize_settings_runtime_summary,
)
from trading_mvp.time_utils import utcnow_naive

FINAL_ORDER_STATUSES = {"filled", "canceled", "cancelled", "rejected", "expired", "finished"}
FINAL_EXCHANGE_ORDER_STATUSES = {"FILLED", "CANCELED", "CANCELLED", "REJECTED", "EXPIRED", "EXPIRED_IN_MATCH", "FINISHED"}
PROTECTIVE_ORDER_TYPES = {"stop_market", "take_profit_market"}
PROTECTIVE_CLOSE_FILL_BACKFILL_FAILED_REASON_CODE = "PROTECTIVE_CLOSE_FILL_BACKFILL_FAILED"
PROTECTIVE_CLOSE_FILL_BACKFILL_PENDING_REASON_CODE = "PROTECTIVE_CLOSE_FILL_BACKFILL_PENDING"
CLOSED_POSITION_PROTECTIVE_ORDER_RECONCILED_REASON_CODE = "POSITION_CLOSED_PROTECTIVE_ORDER_ORPHANED"
UNCONFIRMED_CLOSE_EXECUTION_STATUSES = {"MISSING", "PENDING", "FAILED"}
PROFITABILITY_COST_WINDOW_SPECS: tuple[tuple[str, int | None], ...] = (
    ("today", None),
    ("7d", 24 * 7),
    ("30d", 24 * 30),
    ("all_time", None),
)
ANALYTICS_COST_BREAKDOWN_TIMEZONE = "Asia/Seoul"
ANALYTICS_COST_BREAKDOWN_PERIODS = {"today", "month", "year"}
MARKETABLE_ENTRY_WARNING_RATIO = 0.7
AUDIT_CATEGORY_RISK = "risk"
AUDIT_CATEGORY_EXECUTION = "execution"
AUDIT_CATEGORY_APPROVAL_CONTROL = "approval_control"
AUDIT_CATEGORY_PROTECTION = "protection"
AUDIT_CATEGORY_HEALTH_SYSTEM = "health_system"
AUDIT_CATEGORY_AI_DECISION = "ai_decision"
OPERATOR_AUDIT_LIMIT = 4
SYMBOL_AUDIT_LIMIT = 3
OPERATOR_PERFORMANCE_WINDOW_LIMIT = 1
OPERATOR_PERFORMANCE_WINDOW_SPECS: tuple[tuple[str, int], ...] = (("24h", 24),)
OPERATOR_PROFITABILITY_COST_WINDOW_SPECS: tuple[tuple[str, int | None], ...] = (
    ("today", None),
)
OPERATOR_PERFORMANCE_ENTRY_LIMIT = 3
OPERATOR_EXECUTION_PROFILE_LIMIT = 2
OPERATOR_RECENT_ROW_SCAN_LIMIT = 100
OPERATOR_EXTRACTED_SYMBOL_FALLBACK_SCAN_LIMIT = 900
RECENT_FILL_LIMIT = 4
OPERATOR_COMPACT_VIEWS = {"market", "scheduler", "decision", "risk"}
AUTO_RESIZABLE_EXPOSURE_LIMIT_REASON_CODES = {
    "GROSS_EXPOSURE_LIMIT_REACHED",
    "DIRECTIONAL_BIAS_LIMIT_REACHED",
    "LARGEST_POSITION_LIMIT_REACHED",
    "SAME_TIER_CONCENTRATION_LIMIT_REACHED",
}
AI_REVIEW_TYPE_BY_TRIGGER_REASON = {
    "entry_candidate_event": "entry_candidate_review",
    "breakout_exception_event": "breakout_exception_review",
    "open_position_recheck_due": "open_position_review",
    "protection_review_event": "protection_review",
    "manual_review_event": "manual_review",
    "periodic_backstop_due": "periodic_backstop_review",
}

MARKET_CONTEXT_SUMMARY_KEYS = (
    "primary_regime",
    "trend_alignment",
    "volatility_regime",
    "volume_regime",
    "momentum_state",
    "weak_volume",
    "momentum_weakening",
)
DERIVATIVES_SUMMARY_KEYS = (
    "available",
    "source",
    "funding_bias",
    "basis_bias",
    "taker_flow_alignment",
    "long_alignment_score",
    "short_alignment_score",
    "crowded_long_risk",
    "crowded_short_risk",
    "spread_headwind",
    "spread_stress",
    "spread_bps",
)
EVENT_CONTEXT_SUMMARY_KEYS = (
    "source_status",
    "source_provenance",
    "next_event_name",
    "next_event_at",
    "next_event_importance",
    "minutes_to_next_event",
    "active_risk_window",
    "event_bias",
    "is_stale",
    "is_complete",
    "affected_assets",
    "enrichment_vendors",
    "failed_release_ids",
    "parse_failed_release_ids",
    "complete_reference",
)
EVENT_CONTEXT_VISIBILITY_DEFAULTS = {
    "source_status": "unavailable",
    "is_stale": False,
    "next_event_name": None,
    "minutes_to_next_event": None,
    "active_risk_window": False,
}
DECISION_COMPACT_OUTPUT_KEYS = (
    "symbol",
    "timeframe",
    "decision",
    "confidence",
    "confidence_band",
    "recommended_holding_profile",
    "intent_family",
    "management_action",
    "primary_reason_codes",
    "no_trade_reason_codes",
    "abstain_reason_codes",
    "fallback_reason_codes",
    "provider_status",
    "explanation_short",
)
DECISION_COMPACT_TRIGGER_KEYS = (
    "trigger_reason",
    "symbol",
    "timeframe",
    "strategy_engine",
    "holding_profile",
    "assigned_slot",
    "candidate_weight",
    "reason_codes",
    "dedupe_reason",
    "applied_review_cadence_minutes",
    "review_cadence_source",
    "triggered_at",
)
DECISION_COMPACT_REFERENCE_KEYS = (
    "market_snapshot_id",
    "feature_snapshot_id",
    "sync_freshness_summary",
)
DECISION_COMPACT_METADATA_KEYS = (
    "provider",
    "model",
    "duration_ms",
    "schema_valid",
    "last_ai_trigger_reason",
    "last_ai_invoked_at",
    "last_ai_skip_reason",
    "trigger_deduped",
    "trigger_fingerprint",
)
RISK_COMPACT_PAYLOAD_KEYS = (
    "allowed",
    "decision",
    "reason_codes",
    "blocked_reason",
    "blocked_reason_codes",
    "adjustment_reason_codes",
    "degraded_reason_codes",
    "protection_reason_codes",
    "reason_details",
    "approved_risk_pct",
    "approved_leverage",
    "approved_quantity",
    "approved_qty",
    "approved_notional",
    "approved_projected_notional",
    "auto_resized_entry",
    "auto_resize_reason",
    "approval_required_reason",
    "operating_state",
    "operating_mode",
    "degraded_reason",
    "survival_path",
    "policy_source",
    "exchange_connectivity_state",
    "evaluated_operator_policy",
    "sync_freshness_summary",
    "exposure_headroom_snapshot",
    "reason_evidence",
)
AGENT_COMPACT_INPUT_KEYS = (
    "symbol",
    "timeframe",
    "ai_trigger",
    "decision_reference",
    "event_context",
    "manual_review",
)
AGENT_COMPACT_OUTPUT_KEYS = (
    "symbol",
    "timeframe",
    "decision",
    "confidence",
    "confidence_band",
    "recommended_holding_profile",
    "intent_family",
    "management_action",
    "primary_reason_codes",
    "no_trade_reason_codes",
    "abstain_reason_codes",
    "rationale_codes",
    "provider_status",
    "explanation_short",
    "items",
)
AGENT_COMPACT_METADATA_KEYS = (
    "provider",
    "model",
    "duration_ms",
    "schema_valid",
    "last_ai_trigger_reason",
    "last_ai_invoked_at",
    "last_ai_skip_reason",
    "trigger_deduped",
    "trigger_fingerprint",
    "active_position_prompt_route_context",
)
ADAPTIVE_SIGNAL_SUMMARY_KEYS = (
    "status",
    "active_inputs",
    "signal_weight",
    "confidence_multiplier",
    "hold_bias",
    "risk_pct_multiplier",
)
EXECUTION_POLICY_SUMMARY_KEYS = (
    "policy_profile",
    "execution_style",
    "order_type",
    "time_in_force",
    "timeout_seconds",
)
EXECUTION_QUALITY_SUMMARY_KEYS = (
    "execution_quality_status",
    "decision_quality_status",
    "partial_fill_attempts",
    "repriced_attempts",
    "aggressive_fallback_used",
    "arrival_slippage_pct",
    "realized_slippage_pct",
    "first_fill_latency_seconds",
    "fees_total",
    "net_realized_pnl_total",
)
ORDER_HISTORY_COMPACT_KEYS = (
    "id",
    "symbol",
    "position_id",
    "parent_order_id",
    "decision_run_id",
    "risk_check_id",
    "side",
    "order_type",
    "status",
    "exchange_status",
    "mode",
    "reduce_only",
    "close_only",
    "requested_quantity",
    "requested_price",
    "filled_quantity",
    "average_fill_price",
    "external_order_id",
    "client_order_id",
    "reason_codes",
    "pnl_source",
    "close_execution_sync_status",
    "realized_pnl_confirmed",
    "missing_close_execution",
    "fee_source",
    "fee_confirmed",
    "warning_message",
    "blocked_reason",
    "fee_warning_message",
    "created_at",
    "updated_at",
    "last_exchange_update_at",
)
EXECUTION_HISTORY_COMPACT_KEYS = (
    "id",
    "order_id",
    "position_id",
    "symbol",
    "status",
    "fill_price",
    "fill_quantity",
    "fee_paid",
    "realized_pnl",
    "external_trade_id",
    "commission_asset",
    "created_at",
    "updated_at",
    "mode",
    "order_type",
    "order_status",
    "requested_quantity",
    "requested_price",
    "decision_run_id",
)
RISK_DEBUG_NUMERIC_KEYS = (
    "requested_notional",
    "resized_notional",
    "projected_symbol_notional",
    "projected_directional_notional",
    "current_symbol_notional",
    "current_directional_notional",
    "open_order_reserved_notional",
)

AUDIT_APPROVAL_CONTROL_EVENT_TYPES = {
    "settings_updated",
    "trading_paused",
    "trading_resumed",
    "live_approval_armed",
    "live_approval_disarmed",
    "operator_event_view_created",
    "operator_event_view_updated",
    "operator_event_view_cleared",
    "manual_no_trade_window_created",
    "manual_no_trade_window_updated",
    "manual_no_trade_window_ended",
    "alignment_evaluated",
    "operating_state_changed",
    "trading_auto_resume_skipped",
    "trading_auto_resume_attempted",
    "trading_auto_resume_blocked",
    "trading_auto_resumed",
}
AUDIT_PROTECTION_EVENT_TYPES = {
    "position_management_stop_tightened",
    "protection_manage_only_enabled",
    "unprotected_position_detected",
    "emergency_exit_triggered",
    "emergency_exit_completed",
    "emergency_exit_failed",
}
AUDIT_EXECUTION_EVENT_TYPES = {
    "live_execution_attempted",
    "live_execution",
    "live_execution_rejected",
    "live_execution_error",
    "live_execution_skipped",
    "live_order_submission_unknown",
    "live_order_submission_recovered",
    "live_test_order",
}
AUDIT_AI_EVENT_TYPES = {
    "agent_output",
    "decision_cycle_completed",
    "decision_cycle_failed",
}
AUDIT_HEALTH_SYSTEM_EVENT_TYPES = {
    "market_snapshot",
    "integration_test",
    "live_sync",
    "live_sync_failed",
    "scheduler_run",
    "scheduler_run_failed",
}
AUDIT_CATEGORY_VALUES = {
    AUDIT_CATEGORY_RISK,
    AUDIT_CATEGORY_EXECUTION,
    AUDIT_CATEGORY_APPROVAL_CONTROL,
    AUDIT_CATEGORY_PROTECTION,
    AUDIT_CATEGORY_HEALTH_SYSTEM,
    AUDIT_CATEGORY_AI_DECISION,
}
AUDIT_RELATED_ID_KEYS = (
    "risk_id",
    "risk_check_id",
    "execution_id",
    "order_id",
    "position_id",
    "decision_id",
    "decision_run_id",
    "agent_run_id",
    "scheduler_run_id",
    "snapshot_id",
    "cycle_id",
)
AUDIT_LEGACY_TRIGGER_REASONS = {"open_position_recheck_due", "periodic_backstop_due"}
AUDIT_COMPACT_ROW_KEYS = (
    "id",
    "event_type",
    "event_category",
    "entity_type",
    "entity_id",
    "severity",
    "message",
    "message_summary",
    "related_type",
    "related_id",
    "has_payload",
    "payload_keys",
    "suppression_active",
    "suppression_reason_code",
    "allow_same_side_add_on",
    "allowed_add_on_side",
    "legacy_review_trigger_reason",
    "created_at",
    "updated_at",
)


def _serialize_model_row(row: object) -> dict[str, object]:
    values: dict[str, object] = {}
    for key in row.__table__.columns:  # type: ignore[attr-defined]
        value = getattr(row, key.name)
        values[key.name] = value.isoformat() if hasattr(value, "isoformat") else value
    return values


def _serialize_model_list(rows: Sequence[object]) -> list[dict[str, object]]:
    return [_serialize_model_row(row) for row in rows]


def _serialize_mapping_row(row: Mapping[str, object]) -> dict[str, object]:
    values: dict[str, object] = {}
    for key, value in row.items():
        values[str(key)] = value.isoformat() if hasattr(value, "isoformat") else value
    return values


def _normalize_symbol_filter(symbol: str | None) -> str | None:
    if symbol is None:
        return None
    normalized = symbol.strip().upper()
    return normalized or None


def _normalize_timeframe_filter(timeframe: str | None) -> str | None:
    if timeframe is None:
        return None
    normalized = timeframe.strip()
    return normalized or None


def _build_position_protection_state(session: Session, position: Position) -> dict[str, object]:
    if position.status != "open" or position.quantity <= 0:
        return {
            "symbol": position.symbol,
            "side": position.side,
            "status": "flat",
            "protected": True,
            "protective_order_count": 0,
            "has_stop_loss": False,
            "has_take_profit": False,
            "missing_components": [],
            "order_ids": [],
            "position_size": position.quantity,
        }

    active_orders = list(
        session.scalars(
            select(Order).where(
                Order.mode == "live",
                Order.symbol == position.symbol,
                func.lower(func.coalesce(Order.status, "")).notin_(tuple(FINAL_ORDER_STATUSES)),
                func.upper(func.coalesce(Order.exchange_status, "")).notin_(tuple(FINAL_EXCHANGE_ORDER_STATUSES)),
            )
        )
    )
    protective_orders = [
        order
        for order in active_orders
        if order.order_type.lower() in PROTECTIVE_ORDER_TYPES
    ]
    has_stop = any(order.order_type.lower().startswith("stop") for order in protective_orders)
    has_take_profit = any(order.order_type.lower().startswith("take_profit") for order in protective_orders)
    missing_components: list[str] = []
    if not has_stop:
        missing_components.append("stop_loss")
    if not has_take_profit:
        missing_components.append("take_profit")
    return {
        "symbol": position.symbol,
        "side": position.side,
        "status": "protected" if not missing_components else "missing",
        "protected": not missing_components,
        "protective_order_count": len(protective_orders),
        "has_stop_loss": has_stop,
        "has_take_profit": has_take_profit,
        "missing_components": missing_components,
        "order_ids": [order.id for order in protective_orders],
        "position_size": position.quantity,
    }


def _as_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item not in {None, ""}]


def _build_pending_entry_plan_snapshot(row: PendingEntryPlan | None) -> PendingEntryPlanSnapshot:
    if row is None:
        return PendingEntryPlanSnapshot()
    metadata = dict(row.metadata_json) if isinstance(row.metadata_json, dict) else {}
    trigger_details = metadata.get("trigger_details")
    expiry_context = pending_entry_expiry_context(row.expires_at)
    return PendingEntryPlanSnapshot(
        plan_id=row.id,
        symbol=row.symbol,
        side=row.side if row.side in {"long", "short"} else None,
        plan_status=row.plan_status if row.plan_status in {"armed", "triggered", "expired", "canceled"} else None,
        source_decision_run_id=row.source_decision_run_id,
        source_risk_check_id=_as_int(metadata.get("source_risk_check_id"), default=0) or None,
        source_blocked_reason_codes=_as_string_list(metadata.get("source_blocked_reason_codes")),
        source_timeframe=row.source_timeframe,
        regime=row.regime,
        posture=row.posture,
        rationale_codes=_as_string_list(row.rationale_codes),
        entry_mode=row.entry_mode if row.entry_mode in {"breakout_confirm", "pullback_confirm", "immediate", "none"} else None,
        holding_profile=_as_holding_profile(metadata.get("holding_profile")) or "scalp",  # type: ignore[arg-type]
        holding_profile_reason=str(metadata.get("holding_profile_reason") or "") or None,
        entry_zone_min=row.entry_zone_min,
        entry_zone_max=row.entry_zone_max,
        invalidation_price=row.invalidation_price,
        max_chase_bps=row.max_chase_bps,
        idea_ttl_minutes=row.idea_ttl_minutes,
        stop_loss=row.stop_loss,
        take_profit=row.take_profit,
        risk_pct_cap=row.risk_pct_cap,
        leverage_cap=row.leverage_cap,
        created_at=row.created_at,
        expires_at=row.expires_at,
        expires_at_time_basis=str(expiry_context["expires_at_time_basis"]),
        app_utc_now=expiry_context["app_utc_now"],
        remaining_ttl_seconds=expiry_context["remaining_ttl_seconds"],
        expired_by_app_utc_now=bool(expiry_context["expired_by_app_utc_now"]),
        triggered_at=row.triggered_at,
        canceled_at=row.canceled_at,
        canceled_reason=row.canceled_reason,
        idempotency_key=row.idempotency_key,
        last_watch_at=_as_datetime(metadata.get("last_watch_at")),
        last_watch_snapshot_id=_as_int(metadata.get("last_watch_snapshot_id"), default=0) or None,
        trigger_details=dict(trigger_details) if isinstance(trigger_details, dict) else {},
    )


def _as_missing_items(value: object) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): _as_string_list(item)
        for key, item in value.items()
        if isinstance(item, list)
    }


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _as_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _adverse_slippage_pct(*, side: str, requested_price: float, fill_price: float) -> float:
    if requested_price <= 0 or fill_price <= 0:
        return 0.0
    side_key = side.lower()
    if side_key == "buy":
        return max((fill_price - requested_price) / requested_price, 0.0)
    if side_key == "sell":
        return max((requested_price - fill_price) / requested_price, 0.0)
    return abs(fill_price - requested_price) / requested_price


def _signed_slippage_bps_from_prices(*, side: str, requested_price: float, fill_price: float) -> float | None:
    if requested_price <= 0 or fill_price <= 0:
        return None
    side_key = side.lower()
    if side_key == "buy":
        return ((fill_price - requested_price) / requested_price) * 10000.0
    if side_key == "sell":
        return ((requested_price - fill_price) / requested_price) * 10000.0
    return None


def _first_numeric_value(source: dict[str, Any], keys: Sequence[str]) -> float | None:
    for key in keys:
        value = source.get(key)
        if value is None or value == "":
            continue
        return _as_float(value, default=0.0)
    return None


def _execution_signed_slippage_bps(execution_row: Execution, order_row: Order | None) -> float | None:
    payload = _as_dict(execution_row.payload)
    explicit = _first_numeric_value(payload, ("signed_slippage_bps", "fill_signed_slippage_bps"))
    if explicit is not None:
        return explicit
    quality: dict[str, Any] = {}
    if order_row is not None:
        metadata = _as_dict(order_row.metadata_json)
        quality = _as_dict(metadata.get("execution_quality"))
    explicit = _first_numeric_value(quality, ("signed_slippage_bps", "fill_signed_slippage_bps"))
    if explicit is not None:
        return explicit
    if order_row is None:
        return None
    return _signed_slippage_bps_from_prices(
        side=str(order_row.side or ""),
        requested_price=_as_float(order_row.requested_price, default=0.0),
        fill_price=_as_float(execution_row.fill_price, default=0.0),
    )


def _is_entry_order(order_row: Order) -> bool:
    order_type = str(order_row.order_type or "").lower()
    if bool(order_row.reduce_only) or bool(order_row.close_only):
        return False
    return order_type not in PROTECTIVE_ORDER_TYPES


def _is_close_order(order_row: Order) -> bool:
    return not _is_entry_order(order_row)


def _entry_order_style(order_row: Order) -> str:
    metadata = _as_dict(order_row.metadata_json)
    policy = _as_dict(metadata.get("execution_policy"))
    quality = _as_dict(metadata.get("execution_quality"))
    style = str(
        metadata.get("entry_execution_type")
        or quality.get("entry_execution_type")
        or policy.get("entry_execution_type")
        or policy.get("policy_name")
        or policy.get("execution_style")
        or policy.get("entry_style")
        or policy.get("order_style")
        or quality.get("execution_style")
        or ""
    ).lower()
    if style in {"entry_marketable", "marketable", "aggressive", "market"}:
        return "marketable"
    if style in {"entry_passive_limit", "passive", "passive_limit", "maker"}:
        return "passive"
    if bool(quality.get("aggressive_fallback_used")):
        return "marketable"
    if policy.get("marketable") is not None:
        return "marketable" if bool(policy.get("marketable")) else "passive"

    order_type = str(order_row.order_type or "").lower()
    if order_type == "limit":
        return "passive"
    if order_type == "market" or order_type.endswith("_market"):
        return "marketable"
    return "unknown"


def _profitability_window_since(window_label: str, window_hours: int | None, now: datetime) -> datetime | None:
    if window_label == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if window_hours is not None:
        return now - timedelta(hours=window_hours)
    return None


def _funding_total_for_window(session: Session, since: datetime | None, until: datetime | None = None) -> float:
    statement = select(func.coalesce(func.sum(AccountLedgerEntry.amount), 0.0)).where(
        AccountLedgerEntry.entry_type == "funding"
    )
    if since is not None:
        statement = statement.where(AccountLedgerEntry.occurred_at >= since)
    if until is not None:
        statement = statement.where(AccountLedgerEntry.occurred_at < until)
    return _as_float(session.scalar(statement), default=0.0)


def _build_profitability_cost_breakdown(
    session: Session,
    *,
    window_label: str,
    window_hours: int | None,
    since: datetime | None,
    until: datetime | None = None,
    summary: PerformanceWindowSummary | None = None,
) -> DashboardProfitabilityCostBreakdown:
    order_statement = select(Order).where(Order.mode == "live")
    if since is not None:
        order_statement = order_statement.where(Order.created_at >= since)
    if until is not None:
        order_statement = order_statement.where(Order.created_at < until)
    order_rows = list(session.scalars(order_statement))
    entry_rows = [row for row in order_rows if _is_entry_order(row)]
    marketable_entry_count = 0
    passive_entry_count = 0
    for row in entry_rows:
        style = _entry_order_style(row)
        if style == "marketable":
            marketable_entry_count += 1
        elif style == "passive":
            passive_entry_count += 1

    execution_statement = (
        select(Execution, Order)
        .join(Order, Order.id == Execution.order_id)
        .where(Order.mode == "live")
    )
    if since is not None:
        execution_statement = execution_statement.where(Execution.created_at >= since)
    if until is not None:
        execution_statement = execution_statement.where(Execution.created_at < until)
    execution_rows = list(session.execute(execution_statement))

    signed_weighted_sum = 0.0
    adverse_weighted_sum = 0.0
    signed_weight = 0.0
    for execution_row, order_row in execution_rows:
        signed_bps = _execution_signed_slippage_bps(execution_row, order_row)
        if signed_bps is None:
            continue
        weight = abs(_as_float(execution_row.fill_quantity, default=0.0)) or 1.0
        signed_weighted_sum += signed_bps * weight
        adverse_weighted_sum += max(signed_bps, 0.0) * weight
        signed_weight += weight

    signed_slippage_bps_avg = signed_weighted_sum / signed_weight if signed_weight > 0 else 0.0
    adverse_slippage_bps_avg = adverse_weighted_sum / signed_weight if signed_weight > 0 else 0.0

    if summary is not None:
        gross_pnl = _as_float(summary.gross_pnl_total, default=0.0)
        realized_pnl = _as_float(summary.realized_pnl_total, default=0.0)
        fee = _as_float(summary.fee_total, default=0.0)
        funding = _as_float(summary.funding_total, default=0.0)
        net_pnl_excluding_funding = _as_float(summary.net_pnl_excluding_funding, default=realized_pnl - fee)
        net_pnl_including_funding = _as_float(
            summary.net_pnl_including_funding,
            default=net_pnl_excluding_funding + funding,
        )
        data_count = summary.decisions + len(order_rows) + len(execution_rows)
        basis = "decision_performance_summary_plus_execution_ledger"
    else:
        realized_pnl = sum(_as_float(row.realized_pnl, default=0.0) for row, _order in execution_rows)
        gross_pnl = realized_pnl
        fee = sum(abs(_as_float(row.fee_paid, default=0.0)) for row, _order in execution_rows)
        funding = _funding_total_for_window(session, since, until)
        net_pnl_excluding_funding = realized_pnl - fee
        net_pnl_including_funding = net_pnl_excluding_funding + funding
        data_count = len(order_rows) + len(execution_rows)
        basis = "execution_ledger_plus_account_funding_ledger"

    entry_count = len(entry_rows)
    marketable_entry_ratio = marketable_entry_count / entry_count if entry_count else 0.0
    passive_entry_ratio = passive_entry_count / entry_count if entry_count else 0.0
    total_cost = fee + max(-funding, 0.0)
    fee_to_gross_pnl_ratio = fee / gross_pnl if gross_pnl > 0 else None
    cost_to_gross_pnl_ratio = total_cost / gross_pnl if gross_pnl > 0 else None

    warning_codes: list[str] = []
    if gross_pnl > 0 and fee > gross_pnl:
        warning_codes.append("fee_exceeds_gross_pnl")
    if gross_pnl > 0 and total_cost > gross_pnl:
        warning_codes.append("cost_exceeds_gross_pnl")
    if gross_pnl > 0 and net_pnl_including_funding < 0:
        warning_codes.append("positive_gross_negative_net")
    if adverse_slippage_bps_avg > 0:
        warning_codes.append("adverse_slippage_positive")
    if (
        marketable_entry_count > 0
        and marketable_entry_ratio >= MARKETABLE_ENTRY_WARNING_RATIO
        and net_pnl_including_funding <= 0
    ):
        warning_codes.append("high_marketable_ratio_low_net_pnl")

    return DashboardProfitabilityCostBreakdown(
        window_label=window_label,
        window_hours=window_hours,
        status="ok" if data_count > 0 else "no_data",
        gross_pnl=gross_pnl,
        realized_pnl=realized_pnl,
        fee=fee,
        funding=funding,
        net_pnl=net_pnl_including_funding,
        net_pnl_excluding_funding=net_pnl_excluding_funding,
        net_pnl_including_funding=net_pnl_including_funding,
        signed_slippage_bps_avg=signed_slippage_bps_avg,
        adverse_slippage_bps_avg=adverse_slippage_bps_avg,
        entry_count=entry_count,
        marketable_entry_count=marketable_entry_count,
        passive_entry_count=passive_entry_count,
        marketable_entry_ratio=marketable_entry_ratio,
        passive_entry_ratio=passive_entry_ratio,
        fee_to_gross_pnl_ratio=fee_to_gross_pnl_ratio,
        cost_to_gross_pnl_ratio=cost_to_gross_pnl_ratio,
        total_cost=total_cost,
        warning_codes=warning_codes,
        basis=basis,
    )


def _analytics_cost_timezone() -> ZoneInfo:
    return ZoneInfo(ANALYTICS_COST_BREAKDOWN_TIMEZONE)


def _next_month_start(value: datetime) -> datetime:
    if value.month == 12:
        return datetime(value.year + 1, 1, 1, tzinfo=value.tzinfo)
    return datetime(value.year, value.month + 1, 1, tzinfo=value.tzinfo)


def _normalize_cost_breakdown_period(period: str | None) -> str:
    normalized = str(period or "today").strip().lower()
    if normalized not in ANALYTICS_COST_BREAKDOWN_PERIODS:
        raise ValueError("period must be one of: today, month, year")
    return normalized


def _analytics_period_bounds(
    *,
    period: str,
    year: int | None,
    month: int | None,
) -> tuple[datetime, datetime]:
    timezone = _analytics_cost_timezone()
    now = datetime.now(timezone)
    if period == "today":
        start_at = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start_at, start_at + timedelta(days=1)

    resolved_year = year or now.year
    if resolved_year < 2000 or resolved_year > 2100:
        raise ValueError("year must be between 2000 and 2100")
    if period == "month":
        resolved_month = month or now.month
        if resolved_month < 1 or resolved_month > 12:
            raise ValueError("month must be between 1 and 12")
        start_at = datetime(resolved_year, resolved_month, 1, tzinfo=timezone)
        return start_at, _next_month_start(start_at)

    start_at = datetime(resolved_year, 1, 1, tzinfo=timezone)
    return start_at, datetime(resolved_year + 1, 1, 1, tzinfo=timezone)


def _analytics_bucket_bounds(
    *,
    period: str,
    start_at: datetime,
    end_at: datetime,
) -> list[tuple[str, datetime, datetime]]:
    if period == "year":
        buckets: list[tuple[str, datetime, datetime]] = []
        cursor = start_at
        while cursor < end_at:
            next_at = min(_next_month_start(cursor), end_at)
            buckets.append((cursor.strftime("%Y-%m"), cursor, next_at))
            cursor = next_at
        return buckets
    if period == "month":
        buckets = []
        cursor = start_at
        while cursor < end_at:
            next_at = min(cursor + timedelta(days=1), end_at)
            buckets.append((cursor.strftime("%Y-%m-%d"), cursor, next_at))
            cursor = next_at
        return buckets
    return [(start_at.strftime("%Y-%m-%d"), start_at, end_at)]


def _to_utc_naive(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def _live_execution_rows_for_range(
    session: Session,
    *,
    start_at: datetime,
    end_at: datetime,
) -> list[tuple[Execution, Order]]:
    statement = (
        select(Execution, Order)
        .join(Order, Order.id == Execution.order_id)
        .where(
            Order.mode == "live",
            Execution.created_at >= start_at,
            Execution.created_at < end_at,
        )
        .order_by(Execution.created_at.asc(), Execution.id.asc())
    )
    return [(execution_row, order_row) for execution_row, order_row in session.execute(statement)]


def _execution_commission_asset(execution_row: Execution) -> str:
    payload = _as_dict(execution_row.payload)
    asset = execution_row.commission_asset or payload.get("commissionAsset") or payload.get("commission_asset") or "USDT"
    return str(asset or "USDT").upper()


def _funding_rows_for_range(
    session: Session,
    *,
    start_at: datetime,
    end_at: datetime,
) -> list[AccountLedgerEntry]:
    return list(
        session.scalars(
            select(AccountLedgerEntry)
            .where(
                AccountLedgerEntry.entry_type == "funding",
                AccountLedgerEntry.occurred_at >= start_at,
                AccountLedgerEntry.occurred_at < end_at,
            )
            .order_by(AccountLedgerEntry.occurred_at.asc(), AccountLedgerEntry.id.asc())
        )
    )


def _analytics_slippage_metrics(
    execution_rows: Sequence[tuple[Execution, Order]],
) -> tuple[float | None, float | None, str, int]:
    if not execution_rows:
        return None, None, "UNKNOWN", 0

    signed_weighted_sum = 0.0
    adverse_weighted_sum = 0.0
    signed_weight = 0.0
    missing_count = 0
    for execution_row, order_row in execution_rows:
        signed_bps = _execution_signed_slippage_bps(execution_row, order_row)
        if signed_bps is None:
            missing_count += 1
            continue
        weight = abs(_as_float(execution_row.fill_quantity, default=0.0)) or 1.0
        signed_weighted_sum += signed_bps * weight
        adverse_weighted_sum += max(signed_bps, 0.0) * weight
        signed_weight += weight

    if signed_weight <= 0:
        return None, None, "INCOMPLETE", missing_count
    status = "COMPLETE" if missing_count == 0 else "INCOMPLETE"
    return signed_weighted_sum / signed_weight, adverse_weighted_sum / signed_weight, status, missing_count


def _analytics_cost_summary_for_range(
    session: Session,
    *,
    start_at: datetime,
    end_at: datetime,
) -> tuple[AnalyticsCostBreakdownSummary, str, list[str]]:
    execution_rows, funding_rows = _analytics_cost_source_rows_for_range(session, start_at=start_at, end_at=end_at)
    return _analytics_cost_summary_from_rows(execution_rows=execution_rows, funding_rows=funding_rows)


def _analytics_cost_source_rows_for_range(
    session: Session,
    *,
    start_at: datetime,
    end_at: datetime,
) -> tuple[list[tuple[Execution, Order]], list[AccountLedgerEntry]]:
    return (
        _live_execution_rows_for_range(session, start_at=start_at, end_at=end_at),
        _funding_rows_for_range(session, start_at=start_at, end_at=end_at),
    )


def _analytics_cost_summary_from_rows(
    *,
    execution_rows: Sequence[tuple[Execution, Order]],
    funding_rows: Sequence[AccountLedgerEntry],
) -> tuple[AnalyticsCostBreakdownSummary, str, list[str]]:
    warnings: list[str] = []

    gross_pnl = sum(_as_float(execution_row.realized_pnl, default=0.0) for execution_row, _order_row in execution_rows)
    fee = 0.0
    skipped_fee_assets: set[str] = set()
    for execution_row, _order_row in execution_rows:
        asset = _execution_commission_asset(execution_row)
        if asset != "USDT":
            skipped_fee_assets.add(asset)
            continue
        fee += abs(_as_float(execution_row.fee_paid, default=0.0))

    funding = 0.0
    skipped_funding_assets: set[str] = set()
    for funding_row in funding_rows:
        asset = str(funding_row.asset or "USDT").upper()
        if asset != "USDT":
            skipped_funding_assets.add(asset)
            continue
        funding += _as_float(funding_row.amount, default=0.0)

    signed_slippage_bps, adverse_slippage_bps, slippage_status, _missing_slippage_count = _analytics_slippage_metrics(
        execution_rows
    )
    total_cost = fee + max(-funding, 0.0)
    fee_ratio_pct = (fee / gross_pnl) * 100.0 if gross_pnl > 0 else None
    total_cost_ratio_pct = (total_cost / gross_pnl) * 100.0 if gross_pnl > 0 else None

    for asset in sorted(skipped_fee_assets):
        warnings.append(f"fee_asset_conversion_unavailable:{asset}")
    for asset in sorted(skipped_funding_assets):
        warnings.append(f"funding_asset_conversion_unavailable:{asset}")

    return (
        AnalyticsCostBreakdownSummary(
            net_pnl_usdt=gross_pnl - fee + funding,
            gross_pnl_usdt=gross_pnl,
            fee_usdt=fee,
            funding_usdt=funding,
            total_cost_usdt=total_cost,
            fee_ratio_pct=fee_ratio_pct,
            total_cost_ratio_pct=total_cost_ratio_pct,
            signed_slippage_bps=signed_slippage_bps,
            adverse_slippage_bps=adverse_slippage_bps,
        ),
        slippage_status,
        warnings,
    )


def _latest_settings_row(session: Session) -> Setting | None:
    return session.scalar(select(Setting).order_by(Setting.id.asc()).limit(1))


def _analytics_funding_sync_status(settings_row: Setting | None) -> str:
    if settings_row is None:
        return "UNKNOWN"
    account_sync = _as_dict(build_sync_freshness_summary(settings_row).get("account"))
    status = str(account_sync.get("status") or "unknown").lower()
    if status == "synced":
        return "COMPLETE"
    if status == "stale":
        return "STALE"
    if status == "unknown":
        return "UNKNOWN"
    if status in {"failed", "incomplete", "skipped"}:
        return "INCOMPLETE"
    return status.upper()


def _position_ids_for_cost_period(
    session: Session,
    *,
    start_at: datetime,
    end_at: datetime,
) -> list[int]:
    position_ids: set[int] = set(
        session.scalars(
            select(Position.id).where(
                Position.mode == "live",
                Position.closed_at >= start_at,
                Position.closed_at < end_at,
            )
        )
    )
    position_ids.update(
        row_id
        for row_id in session.scalars(
            select(Order.position_id).where(
                Order.mode == "live",
                Order.position_id.is_not(None),
                Order.created_at >= start_at,
                Order.created_at < end_at,
            )
        )
        if row_id is not None
    )
    position_ids.update(
        row_id
        for row_id in session.scalars(
            select(Execution.position_id).where(
                Execution.position_id.is_not(None),
                Execution.created_at >= start_at,
                Execution.created_at < end_at,
            )
        )
        if row_id is not None
    )
    return sorted(position_ids)


def _missing_close_execution_count_for_range(
    session: Session,
    *,
    start_at: datetime,
    end_at: datetime,
) -> int:
    position_ids = _position_ids_for_cost_period(session, start_at=start_at, end_at=end_at)
    if not position_ids:
        return 0

    positions_by_id = {
        row.id: row
        for row in session.scalars(select(Position).where(Position.id.in_(position_ids)))
    }
    position_orders = list(
        session.scalars(
            select(Order)
            .where(Order.position_id.in_(position_ids), Order.mode == "live")
            .order_by(Order.created_at.asc(), Order.id.asc())
        )
    )
    orders_by_position: dict[int, list[Order]] = defaultdict(list)
    order_ids: list[int] = []
    for order_row in position_orders:
        if order_row.position_id is not None:
            orders_by_position[order_row.position_id].append(order_row)
        if order_row.id is not None:
            order_ids.append(order_row.id)

    execution_filters = [Execution.position_id.in_(position_ids)]
    if order_ids:
        execution_filters.append(Execution.order_id.in_(order_ids))
    execution_statement = (
        select(Execution, Order)
        .outerjoin(Order, Order.id == Execution.order_id)
        .where(or_(*execution_filters))
        .order_by(Execution.created_at.asc(), Execution.id.asc())
    )
    executions_by_position: dict[int, list[tuple[Execution, Order | None]]] = defaultdict(list)
    for execution_row, order_row in session.execute(execution_statement):
        position_id = execution_row.position_id or (order_row.position_id if order_row is not None else None)
        if position_id is not None:
            executions_by_position[position_id].append((execution_row, order_row))

    missing_count = 0
    for position_id in position_ids:
        payload = _close_execution_sync_payload(
            position=positions_by_id.get(position_id),
            orders=orders_by_position.get(position_id, []),
            execution_rows=executions_by_position.get(position_id, []),
        )
        if bool(payload.get("missing_close_execution")):
            missing_count += 1
    return missing_count


def _analytics_data_quality(
    session: Session,
    *,
    start_at: datetime,
    end_at: datetime,
    slippage_status: str,
) -> AnalyticsCostBreakdownDataQuality:
    missing_close_execution_count = _missing_close_execution_count_for_range(session, start_at=start_at, end_at=end_at)
    return AnalyticsCostBreakdownDataQuality(
        realized_pnl_confirmed=missing_close_execution_count == 0,
        execution_sync_status="INCOMPLETE" if missing_close_execution_count > 0 else "COMPLETE",
        funding_sync_status=_analytics_funding_sync_status(_latest_settings_row(session)),
        slippage_data_status=slippage_status,
        missing_close_execution_count=missing_close_execution_count,
        slippage_weighting="quantity",
    )


def _analytics_cost_bucket_from_summary(
    *,
    label: str,
    start_at: datetime,
    end_at: datetime,
    summary: AnalyticsCostBreakdownSummary,
) -> AnalyticsCostBreakdownBucket:
    return (
        AnalyticsCostBreakdownBucket(
            label=label,
            start_at=start_at,
            end_at=end_at,
            net_pnl_usdt=summary.net_pnl_usdt,
            gross_pnl_usdt=summary.gross_pnl_usdt,
            fee_usdt=summary.fee_usdt,
            funding_usdt=summary.funding_usdt,
            total_cost_usdt=summary.total_cost_usdt,
            fee_ratio_pct=summary.fee_ratio_pct,
            total_cost_ratio_pct=summary.total_cost_ratio_pct,
            signed_slippage_bps=summary.signed_slippage_bps,
            adverse_slippage_bps=summary.adverse_slippage_bps,
        )
    )


def _append_unique_warnings(target: list[str], warnings: Sequence[str]) -> None:
    for warning in warnings:
        if warning not in target:
            target.append(warning)


def _normalize_analytics_row_timestamp(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return _to_utc_naive(value)
    return value


def _analytics_bucket_label_for_timestamp(
    value: datetime | None,
    bucket_bounds: Sequence[tuple[str, datetime, datetime]],
) -> str | None:
    timestamp = _normalize_analytics_row_timestamp(value)
    if timestamp is None:
        return None
    for label, start_at, end_at in bucket_bounds:
        if start_at <= timestamp < end_at:
            return label
    return None


def _analytics_cost_rows_by_bucket(
    *,
    execution_rows: Sequence[tuple[Execution, Order]],
    funding_rows: Sequence[AccountLedgerEntry],
    bucket_bounds: Sequence[tuple[str, datetime, datetime]],
) -> tuple[dict[str, list[tuple[Execution, Order]]], dict[str, list[AccountLedgerEntry]]]:
    execution_rows_by_bucket: dict[str, list[tuple[Execution, Order]]] = {
        label: [] for label, _start_at, _end_at in bucket_bounds
    }
    funding_rows_by_bucket: dict[str, list[AccountLedgerEntry]] = {
        label: [] for label, _start_at, _end_at in bucket_bounds
    }

    for execution_row, order_row in execution_rows:
        label = _analytics_bucket_label_for_timestamp(execution_row.created_at, bucket_bounds)
        if label is not None:
            execution_rows_by_bucket[label].append((execution_row, order_row))

    for funding_row in funding_rows:
        label = _analytics_bucket_label_for_timestamp(funding_row.occurred_at, bucket_bounds)
        if label is not None:
            funding_rows_by_bucket[label].append(funding_row)

    return execution_rows_by_bucket, funding_rows_by_bucket


def get_analytics_cost_breakdown(
    session: Session,
    *,
    period: str = "today",
    year: int | None = None,
    month: int | None = None,
) -> AnalyticsCostBreakdownResponse:
    normalized_period = _normalize_cost_breakdown_period(period)
    start_at, end_at = _analytics_period_bounds(period=normalized_period, year=year, month=month)
    start_at_utc = _to_utc_naive(start_at)
    end_at_utc = _to_utc_naive(end_at)
    execution_rows, funding_rows = _analytics_cost_source_rows_for_range(
        session, start_at=start_at_utc, end_at=end_at_utc
    )
    summary, slippage_status, warnings = _analytics_cost_summary_from_rows(
        execution_rows=execution_rows,
        funding_rows=funding_rows,
    )
    data_quality = _analytics_data_quality(
        session,
        start_at=start_at_utc,
        end_at=end_at_utc,
        slippage_status=slippage_status,
    )
    if data_quality.missing_close_execution_count > 0:
        warnings.append(f"missing_close_execution_count:{data_quality.missing_close_execution_count}")
    if data_quality.funding_sync_status in {"INCOMPLETE", "STALE", "UNKNOWN"}:
        warnings.append(f"funding_sync_status:{data_quality.funding_sync_status}")
    if data_quality.slippage_data_status in {"INCOMPLETE", "UNKNOWN"}:
        warnings.append(f"slippage_data_status:{data_quality.slippage_data_status}")

    bucket_bounds = _analytics_bucket_bounds(
        period=normalized_period,
        start_at=start_at,
        end_at=end_at,
    )
    bucket_bounds_utc = [
        (label, _to_utc_naive(bucket_start_at), _to_utc_naive(bucket_end_at))
        for label, bucket_start_at, bucket_end_at in bucket_bounds
    ]
    execution_rows_by_bucket, funding_rows_by_bucket = _analytics_cost_rows_by_bucket(
        execution_rows=execution_rows,
        funding_rows=funding_rows,
        bucket_bounds=bucket_bounds_utc,
    )

    buckets: list[AnalyticsCostBreakdownBucket] = []
    for label, bucket_start_at, bucket_end_at in bucket_bounds:
        bucket_summary, _bucket_slippage_status, bucket_warnings = _analytics_cost_summary_from_rows(
            execution_rows=execution_rows_by_bucket.get(label, []),
            funding_rows=funding_rows_by_bucket.get(label, []),
        )
        buckets.append(
            _analytics_cost_bucket_from_summary(
                label=label,
                start_at=bucket_start_at,
                end_at=bucket_end_at,
                summary=bucket_summary,
            )
        )
        _append_unique_warnings(warnings, bucket_warnings)

    deduped_warnings: list[str] = []
    _append_unique_warnings(deduped_warnings, warnings)
    return AnalyticsCostBreakdownResponse(
        period=normalized_period,  # type: ignore[arg-type]
        timezone=ANALYTICS_COST_BREAKDOWN_TIMEZONE,
        start_at=start_at,
        end_at=end_at,
        summary=summary,
        buckets=buckets,
        data_quality=data_quality,
        warnings=deduped_warnings,
    )


def _execution_quality_metrics_for_order(
    order_row: Order,
    executions: list[Execution],
) -> dict[str, float | int]:
    metadata = order_row.metadata_json if isinstance(order_row.metadata_json, dict) else {}
    quality = metadata.get("execution_quality") if isinstance(metadata.get("execution_quality"), dict) else {}
    sorted_executions = sorted(executions, key=lambda item: (item.created_at, item.id))

    arrival_slippage_pct = 0.0
    if quality.get("arrival_slippage_pct") is not None:
        arrival_slippage_pct = _as_float(quality.get("arrival_slippage_pct"), default=0.0)
    elif sorted_executions:
        first_execution = sorted_executions[0]
        arrival_slippage_pct = _as_float(first_execution.slippage_pct, default=0.0)
        if arrival_slippage_pct <= 0:
            arrival_slippage_pct = _adverse_slippage_pct(
                side=str(order_row.side or ""),
                requested_price=_as_float(order_row.requested_price, default=0.0),
                fill_price=_as_float(first_execution.fill_price, default=0.0),
            )

    realized_slippage_pct = 0.0
    if quality.get("realized_slippage_pct") is not None:
        realized_slippage_pct = _as_float(quality.get("realized_slippage_pct"), default=0.0)
    elif sorted_executions:
        weighted_quantity = sum(abs(_as_float(item.fill_quantity, default=0.0)) for item in sorted_executions)
        if weighted_quantity > 0:
            realized_slippage_pct = sum(
                abs(_as_float(item.fill_quantity, default=0.0))
                * _as_float(item.slippage_pct, default=0.0)
                for item in sorted_executions
            ) / weighted_quantity
        else:
            realized_slippage_pct = sum(_as_float(item.slippage_pct, default=0.0) for item in sorted_executions) / len(
                sorted_executions
            )

    first_fill_latency_seconds = 0.0
    if quality.get("first_fill_latency_seconds") is not None:
        first_fill_latency_seconds = max(_as_float(quality.get("first_fill_latency_seconds"), default=0.0), 0.0)
    elif sorted_executions:
        first_fill_latency_seconds = max((sorted_executions[0].created_at - order_row.created_at).total_seconds(), 0.0)

    order_status = str(order_row.status or "").lower()
    cancel_attempt = int(order_status in {"canceled", "cancelled", "expired"})
    cancel_success = int(order_status in {"canceled", "cancelled"})
    return {
        "arrival_slippage_pct": arrival_slippage_pct,
        "realized_slippage_pct": realized_slippage_pct,
        "first_fill_latency_seconds": first_fill_latency_seconds,
        "cancel_attempt": cancel_attempt,
        "cancel_success": cancel_success,
    }


def _as_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _as_dict(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _as_optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _string_or_none(value: object) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _bool_value(value: object, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if value is None or value == "":
        return default
    return bool(value)


def _first_string(values: Sequence[str]) -> str | None:
    return values[0] if values else None


def _active_position_suppression_projection(value: object) -> dict[str, object]:
    source = _as_dict(value)
    context = _as_dict(source.get("active_position_prompt_route_context")) if source else {}
    if not context:
        context = source
    allowed_add_on_side = str(context.get("allowed_add_on_side") or "").lower()
    return {
        "suppression_active": bool(
            context.get("entry_proposal_suppression_active")
            or context.get("suppression_active")
        ),
        "suppression_reason_code": str(
            context.get("entry_proposal_suppressed_reason_code")
            or context.get("suppression_reason_code")
            or ""
        )
        or None,
        "allow_same_side_add_on": bool(context.get("allow_same_side_add_on")),
        "allowed_add_on_side": (
            allowed_add_on_side if allowed_add_on_side in {"long", "short"} else None
        ),
    }


def _has_active_position_suppression_source(value: object) -> bool:
    source = _as_dict(value)
    if not source:
        return False
    return bool(
        "active_position_prompt_route_context" in source
        or {
            "suppression_active",
            "suppression_reason_code",
            "allow_same_side_add_on",
            "allowed_add_on_side",
        }.intersection(source.keys())
    )


def _decision_run_id_from_audit_row(row: dict[str, object]) -> int | None:
    entity_type = str(row.get("entity_type") or "").lower()
    entity_id = str(row.get("entity_id") or "").strip()
    if entity_type in {"agent_run", "decision_run"} and entity_id:
        try:
            return int(entity_id)
        except ValueError:
            return None
    payload = _as_dict(row.get("payload"))
    correlation_decision_id = payload.get("decision_id")
    if correlation_decision_id in {None, ""}:
        return None
    return _as_int(correlation_decision_id, default=0) or None


def _decision_run_suppression_context_by_id(
    session: Session,
    decision_run_ids: Sequence[int],
) -> dict[int, dict[str, object]]:
    if not decision_run_ids:
        return {}
    rows = list(
        session.scalars(
            select(AgentRun).where(AgentRun.id.in_(list(dict.fromkeys(decision_run_ids))))
        )
    )
    return {
        row.id: _as_dict(_as_dict(row.metadata_json).get("active_position_prompt_route_context"))
        for row in rows
    }


def _as_optional_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _as_holding_profile(value: object) -> str | None:
    profile = str(value or "").strip().lower()
    if profile in {"scalp", "swing", "position"}:
        return profile
    return None


def _compact_dict(
    value: object,
    *,
    allowed_keys: Sequence[str] | None = None,
    max_items: int | None = None,
) -> dict[str, Any]:
    source = _as_dict(value)
    if allowed_keys is not None:
        keys = [key for key in allowed_keys if key in source]
    else:
        keys = list(source.keys())
    if max_items is not None:
        keys = keys[:max_items]
    compact: dict[str, Any] = {}
    for key in keys:
        item = source.get(key)
        if item is None:
            continue
        if isinstance(item, str) and item == "":
            continue
        if isinstance(item, (list, dict)) and not item:
            continue
        compact[str(key)] = item
    return compact


def _compact_market_context_summary(value: object) -> dict[str, Any]:
    return _compact_dict(value, allowed_keys=MARKET_CONTEXT_SUMMARY_KEYS)


def _attach_decision_summary_fields(payload: dict[str, object]) -> dict[str, object]:
    output_payload = _as_dict(payload.get("output_payload"))
    input_payload = _as_dict(payload.get("input_payload"))
    ai_trigger = _as_dict(input_payload.get("ai_trigger"))

    payload["symbol"] = str(output_payload.get("symbol") or ai_trigger.get("symbol") or "") or None
    payload["timeframe"] = str(output_payload.get("timeframe") or ai_trigger.get("timeframe") or "") or None
    payload["decision"] = str(output_payload.get("decision") or "") or None
    payload["confidence"] = output_payload.get("confidence")
    payload["confidence_band"] = output_payload.get("confidence_band")
    payload["recommended_holding_profile"] = output_payload.get("recommended_holding_profile")
    return payload


def _compact_decision_row(row: AgentRun) -> dict[str, object]:
    payload = _serialize_model_row(row)
    input_payload = _as_dict(payload.get("input_payload"))
    compact_input: dict[str, Any] = {}

    ai_trigger = _compact_dict(input_payload.get("ai_trigger"), allowed_keys=DECISION_COMPACT_TRIGGER_KEYS)
    if ai_trigger:
        compact_input["ai_trigger"] = ai_trigger
    decision_reference = _compact_dict(
        input_payload.get("decision_reference"),
        allowed_keys=DECISION_COMPACT_REFERENCE_KEYS,
    )
    if decision_reference:
        compact_input["decision_reference"] = decision_reference

    payload["input_payload"] = compact_input
    payload["output_payload"] = _compact_dict(
        payload.get("output_payload"),
        allowed_keys=DECISION_COMPACT_OUTPUT_KEYS,
    )
    payload["metadata_json"] = _compact_dict(
        payload.get("metadata_json"),
        allowed_keys=DECISION_COMPACT_METADATA_KEYS,
    )
    return payload


def _compact_agent_run_row(row: AgentRun) -> dict[str, object]:
    payload = _serialize_model_row(row)
    payload["input_payload"] = _compact_dict(
        payload.get("input_payload"),
        allowed_keys=AGENT_COMPACT_INPUT_KEYS,
    )
    payload["output_payload"] = _compact_dict(
        payload.get("output_payload"),
        allowed_keys=AGENT_COMPACT_OUTPUT_KEYS,
    )
    payload["metadata_json"] = _compact_dict(
        payload.get("metadata_json"),
        allowed_keys=AGENT_COMPACT_METADATA_KEYS,
    )
    payload["payload_mode"] = "compact"
    return payload


def _compact_order_history_payload(payload: dict[str, object]) -> dict[str, object]:
    compact = _compact_dict(payload, allowed_keys=ORDER_HISTORY_COMPACT_KEYS)
    compact["payload_mode"] = "compact"
    return compact


def _compact_execution_history_payload(payload: dict[str, object]) -> dict[str, object]:
    compact = _compact_dict(payload, allowed_keys=EXECUTION_HISTORY_COMPACT_KEYS)
    compact["payload_mode"] = "compact"
    return compact


def _ai_trigger_reason_from_decision_row(row: AgentRun | None) -> str | None:
    if row is None:
        return None
    metadata = _as_dict(row.metadata_json)
    input_payload = _as_dict(row.input_payload)
    ai_trigger = _as_dict(metadata.get("ai_trigger"))
    if not ai_trigger:
        ai_trigger = _as_dict(input_payload.get("ai_trigger"))
    return str(
        metadata.get("last_ai_trigger_reason")
        or ai_trigger.get("trigger_reason")
        or ""
    ) or None


def _ai_review_type_from_trigger_reason(trigger_reason: str | None) -> str | None:
    if trigger_reason is None:
        return None
    normalized = str(trigger_reason or "").strip()
    return AI_REVIEW_TYPE_BY_TRIGGER_REASON.get(normalized, normalized or None)


def _ai_review_type_from_decision_row(row: AgentRun | None) -> str | None:
    return _ai_review_type_from_trigger_reason(_ai_trigger_reason_from_decision_row(row))


def _ai_trigger_reason_codes_from_decision_row(row: AgentRun | None) -> list[str]:
    if row is None:
        return []
    metadata = _as_dict(row.metadata_json)
    input_payload = _as_dict(row.input_payload)
    ai_trigger = _as_dict(metadata.get("ai_trigger"))
    if not ai_trigger:
        ai_trigger = _as_dict(input_payload.get("ai_trigger"))
    return _as_string_list(ai_trigger.get("reason_codes"))


def _last_ai_skip_reason_from_decision_row(row: AgentRun | None) -> str | None:
    if row is None:
        return None
    metadata = _as_dict(row.metadata_json)
    return str(
        metadata.get("last_ai_skip_reason")
        or metadata.get("ai_skipped_reason")
        or metadata.get("pre_ai_skip_reason")
        or ""
    ) or None


def _dedupe_reason_from_decision_row(row: AgentRun | None) -> str | None:
    if row is None:
        return None
    metadata = _as_dict(row.metadata_json)
    explicit_reason = str(
        metadata.get("dedupe_reason")
        or metadata.get("trigger_dedupe_reason")
        or metadata.get("ai_dedupe_reason")
        or ""
    ) or None
    if explicit_reason is not None:
        return explicit_reason
    skip_reason = _last_ai_skip_reason_from_decision_row(row)
    if skip_reason in {"TRIGGER_DEDUPED", "TRIGGER_FINGERPRINT_UNCHANGED"}:
        return skip_reason
    if bool(metadata.get("trigger_deduped", False)):
        return "TRIGGER_DEDUPED"
    return None


def _provider_invoked_from_decision_row(row: AgentRun | None) -> bool:
    if row is None:
        return False
    metadata = _as_dict(row.metadata_json)
    source = str(metadata.get("source") or "").strip().lower()
    if source in {"llm", "llm_fallback"}:
        return True
    if source in {"deterministic", "pre_ai_skip", "deterministic_skip"}:
        return False
    provider = str(row.provider_name or "").strip().lower()
    return provider not in {"", "deterministic", "deterministic-mock", "local"}


def _provider_status_from_review(*, provider_invoked: bool, provider_skipped: bool, deduped: bool) -> str:
    if deduped:
        return "deduped"
    if provider_skipped:
        return "skipped_pre_ai"
    if provider_invoked:
        return "invoked"
    return "not_invoked"


def _ai_review_snapshot_from_decision_row(row: AgentRun | None) -> OperatorAIReviewSnapshot:
    if row is None:
        return OperatorAIReviewSnapshot()
    metadata = _as_dict(row.metadata_json)
    input_payload = _as_dict(row.input_payload)
    ai_trigger = _as_dict(metadata.get("ai_trigger")) or _as_dict(input_payload.get("ai_trigger"))
    trigger_reason = _ai_trigger_reason_from_decision_row(row)
    skip_reason = _last_ai_skip_reason_from_decision_row(row)
    dedupe_reason = _dedupe_reason_from_decision_row(row)
    trigger_deduped = bool(metadata.get("trigger_deduped", False) or dedupe_reason is not None)
    provider_invoked = _provider_invoked_from_decision_row(row)
    provider_skipped = bool(skip_reason or trigger_deduped)
    return OperatorAIReviewSnapshot(
        trigger_reason=trigger_reason,
        trigger_reason_codes=_ai_trigger_reason_codes_from_decision_row(row),
        review_type=_ai_review_type_from_trigger_reason(trigger_reason),
        skip_reason=skip_reason,
        dedupe_reason=dedupe_reason,
        trigger_deduped=trigger_deduped,
        trigger_fingerprint=str(
            metadata.get("trigger_fingerprint")
            or ai_trigger.get("trigger_fingerprint")
            or ""
        )
        or None,
        provider_name=row.provider_name,
        provider_source=str(metadata.get("source") or "") or None,
        provider_invoked=provider_invoked,
        provider_skipped=provider_skipped,
        provider_status=_provider_status_from_review(
            provider_invoked=provider_invoked,
            provider_skipped=provider_skipped,
            deduped=trigger_deduped,
        ),
        invoked_at=(
            _as_datetime(metadata.get("last_ai_invoked_at"))
            or (row.created_at if str(metadata.get("source") or "") == "llm" else None)
        ),
        next_review_due_at=_as_datetime(metadata.get("next_ai_review_due_at")),
    )


def _fallback_ai_trigger_code_summary(codes: list[str]) -> str | None:
    if not codes:
        return None
    code_map = {
        "TREND_UP": "상승 추세",
        "TREND_DOWN": "하락 추세",
        "PULLBACK_ENTRY": "눌림 진입",
        "PULLBACK_ENTRY_BIAS": "눌림 진입 편향",
        "RSI_HEALTHY": "RSI 양호",
        "RSI_WEAK": "RSI 약세",
        "STRUCTURE_BREAKOUT_UP_EXCEPTION": "상단 돌파 예외",
        "STRUCTURE_BREAKOUT_DOWN_EXCEPTION": "하단 돌파 예외",
        "MISSING_PROTECTIVE_ORDERS": "보호 주문 누락",
        PROTECTION_REQUIRED_STATE: "보호 복구 필요",
    }
    summarized = [
        code_map.get(code, code)
        for code in codes
        if code
    ]
    unique = list(dict.fromkeys(summarized))
    return ", ".join(unique[:3]) if unique else None


def _entry_trigger_signal_summary(
    *,
    features: dict[str, Any],
    output_payload: dict[str, Any],
    trigger_reason: str,
) -> str | None:
    regime = _as_dict(features.get("regime"))
    breakout = _as_dict(features.get("breakout"))
    trend_score = _as_optional_float(features.get("trend_score"))
    momentum_score = _as_optional_float(features.get("momentum_score"))
    volume_ratio = _as_optional_float(features.get("volume_ratio"))
    decision = str(output_payload.get("decision") or "").lower()
    trend_alignment = str(regime.get("trend_alignment") or "").lower()
    breakout_up = bool(breakout.get("broke_swing_high")) or str(
        breakout.get("range_breakout_direction") or ""
    ).lower() == "up"
    breakout_down = bool(breakout.get("broke_swing_low")) or str(
        breakout.get("range_breakout_direction") or ""
    ).lower() == "down"

    parts: list[str] = []
    if trigger_reason == "breakout_exception_event" or breakout_up or breakout_down:
        if breakout_up:
            parts.append("상단 돌파")
        elif breakout_down:
            parts.append("하단 돌파")

    if momentum_score is not None and abs(momentum_score) >= 0.15:
        parts.append(f"모멘텀 {momentum_score:+.2f}%")
    elif trend_score is not None and abs(trend_score) >= 0.15:
        parts.append(f"추세 {trend_score:+.2f}%")

    if volume_ratio is not None:
        if volume_ratio >= 1.05:
            parts.append(f"거래량 {volume_ratio:.2f}배")
        elif volume_ratio <= 0.95:
            parts.append(f"거래량 {volume_ratio:.2f}배(약세)")

    if trend_alignment == "bullish_aligned":
        parts.append("상승 정렬")
    elif trend_alignment == "bearish_aligned":
        parts.append("하락 정렬")
    elif decision == "long":
        parts.append("롱 후보")
    elif decision == "short":
        parts.append("숏 후보")

    unique = list(dict.fromkeys(part for part in parts if part))
    return ", ".join(unique[:3]) if unique else None


def _ai_trigger_summary_from_decision_row(row: AgentRun | None) -> str | None:
    if row is None:
        return None
    metadata = _as_dict(row.metadata_json)
    input_payload = _as_dict(row.input_payload)
    output_payload = _as_dict(row.output_payload)
    ai_trigger = _as_dict(metadata.get("ai_trigger"))
    if not ai_trigger:
        ai_trigger = _as_dict(input_payload.get("ai_trigger"))
    trigger_reason = _ai_trigger_reason_from_decision_row(row)
    if trigger_reason is None:
        return None

    trigger_reason_codes = _as_string_list(ai_trigger.get("reason_codes"))
    output_reason_codes = _as_string_list(output_payload.get("rationale_codes"))

    if trigger_reason == "manual_review_event":
        return "운영자 수동 검토 요청"
    if trigger_reason == "protection_review_event":
        return (
            _fallback_ai_trigger_code_summary(trigger_reason_codes)
            or "보호 주문/포지션 상태 점검"
        )

    feature_summary = _entry_trigger_signal_summary(
        features=_as_dict(input_payload.get("features")),
        output_payload=output_payload,
        trigger_reason=trigger_reason,
    )
    if feature_summary:
        return feature_summary

    return _fallback_ai_trigger_code_summary(trigger_reason_codes or output_reason_codes)


def _market_signal_summary_from_decision_row(row: AgentRun | None) -> str | None:
    if row is None:
        return None
    input_payload = _as_dict(row.input_payload)
    output_payload = _as_dict(row.output_payload)
    trigger_reason = _ai_trigger_reason_from_decision_row(row)
    if trigger_reason is None:
        return None
    return _entry_trigger_signal_summary(
        features=_as_dict(input_payload.get("features")),
        output_payload=output_payload,
        trigger_reason=trigger_reason,
    )


def _market_signal_context_from_decision_row(row: AgentRun | None) -> OperatorMarketSignalSnapshot:
    if row is None:
        return OperatorMarketSignalSnapshot()
    input_payload = _as_dict(row.input_payload)
    features = _as_dict(input_payload.get("features"))
    regime = _as_dict(features.get("regime"))
    breakout = _as_dict(features.get("breakout"))
    breakout_direction = str(breakout.get("range_breakout_direction") or "") or None
    if breakout_direction in {None, "", "none"}:
        if bool(breakout.get("broke_swing_high")):
            breakout_direction = "up"
        elif bool(breakout.get("broke_swing_low")):
            breakout_direction = "down"
        else:
            breakout_direction = None
    return OperatorMarketSignalSnapshot(
        summary=_market_signal_summary_from_decision_row(row),
        trend_score=_as_optional_float(features.get("trend_score")),
        momentum_score=_as_optional_float(features.get("momentum_score")),
        volume_ratio=_as_optional_float(features.get("volume_ratio")),
        primary_regime=str(regime.get("primary_regime") or "") or None,
        trend_alignment=str(regime.get("trend_alignment") or "") or None,
        volatility_regime=str(regime.get("volatility_regime") or "") or None,
        volume_regime=str(regime.get("volume_regime") or "") or None,
        momentum_state=str(regime.get("momentum_state") or "") or None,
        weak_volume=bool(regime.get("weak_volume")) if regime.get("weak_volume") is not None else None,
        momentum_weakening=(
            bool(regime.get("momentum_weakening"))
            if regime.get("momentum_weakening") is not None
            else None
        ),
        breakout_direction=breakout_direction,
        broke_swing_high=bool(breakout.get("broke_swing_high"))
        if breakout.get("broke_swing_high") is not None
        else None,
        broke_swing_low=bool(breakout.get("broke_swing_low"))
        if breakout.get("broke_swing_low") is not None
        else None,
    )


def _first_non_empty_dict(*values: object) -> dict[str, Any]:
    for value in values:
        candidate = _as_dict(value)
        if candidate:
            return candidate
    return {}


def _first_present(*values: object) -> object:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and value == "":
            continue
        return value
    return None


def _as_optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _has_actual_release_enrichment(event_context: dict[str, Any], vendor: str) -> bool:
    events = event_context.get("events")
    if not isinstance(events, list):
        return False
    for event in events:
        event_payload = _as_dict(event)
        enrichment = _as_dict(event_payload.get("release_enrichment"))
        vendor_payload = _as_dict(enrichment.get(vendor))
        actual = vendor_payload.get("actual")
        if actual is not None and actual != "":
            return True
    return False


def _decision_macro_event_context_summary(row: AgentRun | None) -> OperatorDecisionEventContextSnapshot:
    if row is None:
        return OperatorDecisionEventContextSnapshot()
    metadata = _as_dict(row.metadata_json)
    input_payload = _as_dict(row.input_payload)
    metadata_ai_context = _as_dict(metadata.get("ai_context"))
    input_ai_context = _as_dict(input_payload.get("ai_context"))
    ai_context = metadata_ai_context or input_ai_context
    feature_layers = _as_dict(input_payload.get("feature_layers"))
    features = _as_dict(input_payload.get("features"))
    market_snapshot = _as_dict(input_payload.get("market_snapshot"))
    full_event_context = _first_non_empty_dict(
        features.get("event_context"),
        market_snapshot.get("event_context"),
    )
    event_summary = _first_non_empty_dict(
        ai_context.get("event_context_summary"),
        feature_layers.get("event_context_summary"),
        full_event_context,
    )
    event_risk_context = _first_non_empty_dict(
        metadata.get("event_risk_context"),
        ai_context.get("event_risk_context"),
    )
    event_risk_reason_codes = _as_string_list(metadata.get("event_risk_reason_codes"))
    if not event_risk_reason_codes:
        event_risk_reason_codes = _as_string_list(ai_context.get("event_risk_reason_codes"))
    if not event_risk_reason_codes:
        event_risk_reason_codes = _as_string_list(event_risk_context.get("reason_codes"))

    source_status = str(
        _first_present(
            event_summary.get("source_status"),
            full_event_context.get("source_status"),
            event_risk_context.get("source_status"),
        )
        or ""
    ) or None
    is_stale = bool(
        full_event_context.get("is_stale")
        if full_event_context.get("is_stale") is not None
        else source_status == "stale"
    )
    is_complete_raw = full_event_context.get("is_complete")
    is_complete = is_complete_raw if isinstance(is_complete_raw, bool) else None
    is_incomplete = bool(
        is_complete is False
        or source_status in {"incomplete", "unavailable", "error"}
        or "MACRO_EVENT_CONTEXT_INCOMPLETE" in event_risk_reason_codes
    )
    enrichment_vendors = list(
        dict.fromkeys(
            _as_string_list(event_summary.get("enrichment_vendors"))
            + _as_string_list(full_event_context.get("enrichment_vendors"))
            + _as_string_list(event_risk_context.get("enrichment_vendors"))
        )
    )
    bls_actual_enriched = _has_actual_release_enrichment(full_event_context, "bls")
    bea_actual_enriched = _has_actual_release_enrichment(full_event_context, "bea")
    if bls_actual_enriched and "bls" not in enrichment_vendors:
        enrichment_vendors.append("bls")
    if bea_actual_enriched and "bea" not in enrichment_vendors:
        enrichment_vendors.append("bea")

    return OperatorDecisionEventContextSnapshot(
        source_status=source_status,
        source_provenance=str(
            _first_present(event_summary.get("source_provenance"), full_event_context.get("source_provenance"))
            or ""
        )
        or None,
        source_vendor=str(
            _first_present(
                event_summary.get("source_vendor"),
                full_event_context.get("source_vendor"),
                event_risk_context.get("source_vendor"),
            )
            or ""
        )
        or None,
        next_event_name=str(
            _first_present(
                event_summary.get("next_event_name"),
                full_event_context.get("next_event_name"),
                event_risk_context.get("event_name"),
            )
            or ""
        )
        or None,
        next_event_importance=str(
            _first_present(
                event_summary.get("next_event_importance"),
                full_event_context.get("next_event_importance"),
                event_risk_context.get("event_importance"),
            )
            or ""
        )
        or None,
        minutes_to_next_event=_as_optional_int(
            _first_present(
                event_summary.get("minutes_to_next_event"),
                full_event_context.get("minutes_to_next_event"),
                event_risk_context.get("minutes_to_event"),
            )
        ),
        active_risk_window=bool(
            _first_present(
                event_summary.get("active_risk_window"),
                full_event_context.get("active_risk_window"),
                event_risk_context.get("active_risk_window"),
            )
        ),
        release_reaction_window="MACRO_RELEASE_REACTION_WINDOW" in event_risk_reason_codes,
        is_stale=is_stale,
        is_complete=is_complete,
        is_incomplete=is_incomplete,
        affected_assets=list(
            dict.fromkeys(
                _as_string_list(full_event_context.get("affected_assets"))
                + _as_string_list(event_risk_context.get("affected_assets"))
            )
        ),
        enrichment_vendors=enrichment_vendors,
        failed_release_ids=[
            int(item)
            for item in _as_string_list(full_event_context.get("failed_release_ids"))
            if str(item).isdigit()
        ],
        parse_failed_release_ids=[
            int(item)
            for item in _as_string_list(full_event_context.get("parse_failed_release_ids"))
            if str(item).isdigit()
        ],
        complete_reference=_as_dict(full_event_context.get("complete_reference")),
        bls_actual_enriched=bls_actual_enriched,
        bea_actual_enriched=bea_actual_enriched,
        event_risk_active=bool(
            metadata.get("event_risk_active")
            if metadata.get("event_risk_active") is not None
            else ai_context.get("event_risk_active")
            if ai_context.get("event_risk_active") is not None
            else event_risk_context.get("event_risk_active", False)
        ),
        event_risk_reason_codes=event_risk_reason_codes,
        event_bias_used=str(event_risk_context.get("event_bias_used") or "") or None,
    )


def _compact_derivatives_summary(value: object) -> dict[str, Any]:
    compact = _compact_dict(value, allowed_keys=DERIVATIVES_SUMMARY_KEYS)
    if compact:
        return compact
    return {
        "available": False,
        "source": "unavailable",
        "funding_bias": "unknown",
        "basis_bias": "unknown",
        "taker_flow_alignment": "unknown",
    }


def _compact_event_context_summary(value: object) -> dict[str, Any]:
    compact = _compact_dict(value, allowed_keys=EVENT_CONTEXT_SUMMARY_KEYS)
    if compact:
        for key, default in EVENT_CONTEXT_VISIBILITY_DEFAULTS.items():
            compact.setdefault(key, default)
        return compact
    return {
        **EVENT_CONTEXT_VISIBILITY_DEFAULTS,
        "is_complete": False,
    }


def _compact_adaptive_signal_summary(value: object) -> dict[str, Any]:
    return _compact_dict(value, allowed_keys=ADAPTIVE_SIGNAL_SUMMARY_KEYS)


def _compact_execution_policy(value: object) -> dict[str, Any]:
    return _compact_dict(value, allowed_keys=EXECUTION_POLICY_SUMMARY_KEYS)


def _compact_execution_quality(value: object) -> dict[str, Any]:
    return _compact_dict(value, allowed_keys=EXECUTION_QUALITY_SUMMARY_KEYS)


def _compact_risk_debug_payload(value: object) -> dict[str, Any]:
    source = _as_dict(value)
    compact: dict[str, Any] = {}
    for key in RISK_DEBUG_NUMERIC_KEYS:
        if source.get(key) is not None:
            compact[key] = _as_float(source.get(key), default=0.0)
    requested_codes = _as_string_list(source.get("requested_exposure_limit_codes"))
    if "requested_exposure_limit_codes" in source:
        compact["requested_exposure_limit_codes"] = requested_codes
    final_codes = _as_string_list(source.get("final_exposure_limit_codes"))
    if "final_exposure_limit_codes" in source:
        compact["final_exposure_limit_codes"] = final_codes
    headroom = _compact_dict(source.get("headroom"))
    if headroom:
        compact["headroom"] = headroom
    entry_trigger = _compact_dict(source.get("entry_trigger"))
    if entry_trigger:
        compact["entry_trigger"] = entry_trigger
    slot_allocation = _compact_dict(
        source.get("slot_allocation"),
        allowed_keys=(
            "assigned_slot",
            "candidate_weight",
            "capacity_reason",
            "selected_reason",
            "applies_soft_limit",
            "risk_pct_multiplier",
            "leverage_multiplier",
            "notional_multiplier",
        ),
    )
    if slot_allocation:
        compact["slot_allocation"] = slot_allocation
    holding_profile = _compact_dict(
        source.get("holding_profile"),
        allowed_keys=(
            "holding_profile",
            "holding_profile_reason",
            "hard_stop_active",
            "stop_widening_allowed",
        ),
    )
    if holding_profile:
        compact["holding_profile"] = holding_profile
    lead_market_context = _compact_dict(
        source.get("lead_market_context"),
        allowed_keys=(
            "lead_context_status",
            "missing_lead_symbols",
            "reason_codes",
        ),
    )
    if lead_market_context:
        compact["lead_market_context"] = lead_market_context
    binance_rest_summary = _as_dict(source.get("binance_rest_summary"))
    if binance_rest_summary:
        compact["binance_rest_summary"] = binance_rest_summary
    sync_timestamps = _compact_dict(source.get("sync_timestamps"))
    if sync_timestamps:
        compact["sync_timestamps"] = sync_timestamps
    return compact


def _compact_risk_reason_evidence(value: object) -> dict[str, Any]:
    source = _as_dict(value)
    compact: dict[str, Any] = {}
    symbol_performance = _compact_dict(
        source.get("symbol_recent_performance_gate"),
        allowed_keys=(
            "applied",
            "status",
            "reason_codes",
            "symbol",
            "lookback_days",
            "sample_limit",
            "minimum_execution_count",
            "execution_count",
            "gross_realized_pnl",
            "fee_total",
            "net_pnl_after_fees",
            "comparison",
        ),
    )
    if symbol_performance:
        compact["symbol_recent_performance_gate"] = symbol_performance
    portfolio_gate = _compact_dict(
        source.get("portfolio_exposure_gate"),
        allowed_keys=(
            "applied",
            "status",
            "reason_code",
            "reason_codes",
            "blocked_reason_codes",
            "would_block_reason_codes",
            "enforced_reason_codes",
            "candidate_symbol",
            "candidate_direction",
            "candidate_notional_exposure",
            "directional_bias_pct",
            "max_single_position_exposure_pct",
            "same_tier_concentration",
            "same_tier_concentration_pct",
            "correlated_symbol_exposure_pct",
            "combined_BTC_ETH_directional_exposure_pct",
            "limits",
        ),
    )
    if portfolio_gate:
        compact["portfolio_exposure_gate"] = portfolio_gate
    expected_cost_gate = _compact_dict(
        source.get("expected_cost_gate"),
        allowed_keys=(
            "applied",
            "status",
            "mode",
            "would_block",
            "would_block_reason_codes",
            "enforced_reason_codes",
            "reason_codes",
            "expected_profit_bps",
            "expected_total_cost_bps",
            "net_expected_edge_bps",
            "cost_to_edge_ratio",
            "rr_after_estimated_cost",
        ),
    )
    if expected_cost_gate:
        compact["expected_cost_gate"] = expected_cost_gate
    entry_trigger = _compact_dict(
        source.get("entry_trigger"),
        allowed_keys=(
            "decision_side",
            "mode",
            "latest_price",
            "entry_price",
            "entry_zone_min",
            "entry_zone_max",
            "max_chase_bps",
            "observed_chase_bps",
            "trigger_met",
            "reason_codes",
        ),
    )
    if entry_trigger:
        compact["entry_trigger"] = entry_trigger
    return compact


def _compact_decision_reference(reference: DecisionReferencePayload) -> DecisionReferencePayload:
    compact_market_freshness = _compact_dict(
        reference.market_freshness_summary,
        allowed_keys=("symbol", "timeframe", "source", "source_status", "status", "snapshot_at", "stale", "incomplete"),
    )
    return reference.model_copy(
        update={
            "sync_freshness_summary": {},
            "market_freshness_summary": compact_market_freshness,
        }
    )


def _sync_summary_blocks_freshness(sync_freshness_summary: dict[str, Any]) -> bool:
    for scope_payload in sync_freshness_summary.values():
        if not isinstance(scope_payload, dict):
            continue
        if bool(scope_payload.get("stale")) or bool(scope_payload.get("incomplete")):
            return True
    return False


def _build_decision_reference(row: AgentRun | None) -> DecisionReferencePayload:
    if row is None or not isinstance(row.input_payload, dict):
        return DecisionReferencePayload()
    reference_payload = _as_dict(row.input_payload.get("decision_reference", {}))
    market_snapshot = _as_dict(row.input_payload.get("market_snapshot", {}))
    sync_freshness_summary = {
        str(scope): dict(payload)
        for scope, payload in _as_dict(reference_payload.get("sync_freshness_summary", {})).items()
        if isinstance(payload, dict)
    }
    market_freshness_summary = _as_dict(reference_payload.get("market_freshness_summary", {}))
    market_snapshot_stale = bool(
        reference_payload.get(
            "market_snapshot_stale",
            market_freshness_summary.get("stale", market_snapshot.get("is_stale", False)),
        )
    )
    market_snapshot_incomplete = bool(
        reference_payload.get(
            "market_snapshot_incomplete",
            market_freshness_summary.get(
                "incomplete",
                not bool(market_snapshot.get("is_complete", True)),
            ),
        )
    )
    freshness_blocking = bool(reference_payload.get("freshness_blocking")) or market_snapshot_stale or market_snapshot_incomplete or _sync_summary_blocks_freshness(sync_freshness_summary)
    market_snapshot_id = reference_payload.get("market_snapshot_id")
    return DecisionReferencePayload(
        market_snapshot_id=_as_int(market_snapshot_id) if market_snapshot_id is not None else None,
        market_snapshot_at=_as_datetime(reference_payload.get("market_snapshot_at") or market_snapshot.get("snapshot_time")),
        market_snapshot_source=str(reference_payload.get("market_snapshot_source") or "unknown") or None,
        market_snapshot_stale=market_snapshot_stale,
        market_snapshot_incomplete=market_snapshot_incomplete,
        account_sync_at=_as_datetime(reference_payload.get("account_sync_at")),
        positions_sync_at=_as_datetime(reference_payload.get("positions_sync_at")),
        open_orders_sync_at=_as_datetime(reference_payload.get("open_orders_sync_at")),
        protective_orders_sync_at=_as_datetime(reference_payload.get("protective_orders_sync_at")),
        account_sync_status=str(reference_payload.get("account_sync_status") or "") or None,
        sync_freshness_summary=sync_freshness_summary,
        market_freshness_summary=market_freshness_summary,
        freshness_blocking=freshness_blocking,
        display_gap=bool(reference_payload.get("display_gap", False)),
        display_gap_reason=str(reference_payload.get("display_gap_reason") or "") or None,
    )


def _latest_market_refresh_at_for_decision(
    session: Session,
    decision_row: AgentRun | None,
    *,
    fallback_summary: dict[str, Any] | None = None,
) -> datetime | None:
    if decision_row is not None and isinstance(decision_row.output_payload, dict):
        symbol = str(decision_row.output_payload.get("symbol") or "").upper()
        timeframe = str(decision_row.output_payload.get("timeframe") or "")
        if symbol and timeframe:
            market_row = session.scalar(
                select(MarketSnapshot)
                .where(MarketSnapshot.symbol == symbol, MarketSnapshot.timeframe == timeframe)
                .order_by(desc(MarketSnapshot.snapshot_time))
                .limit(1)
            )
            if market_row is not None:
                return market_row.snapshot_time
    if isinstance(fallback_summary, dict):
        return _as_datetime(fallback_summary.get("snapshot_at"))
    return None


def _annotate_decision_reference(
    reference: DecisionReferencePayload,
    *,
    current_market_refresh_at: datetime | None,
    current_sync_freshness_summary: dict[str, Any],
) -> DecisionReferencePayload:
    if (
        reference.market_snapshot_at is None
        and reference.market_snapshot_id is None
        and not reference.sync_freshness_summary
        and not reference.market_freshness_summary
    ):
        return reference
    if reference.market_snapshot_at is not None and current_market_refresh_at is not None:
        if current_market_refresh_at > reference.market_snapshot_at:
            return reference.model_copy(
                update={
                    "display_gap": True,
                    "display_gap_reason": "The dashboard is showing a newer market refresh than the last AI decision snapshot.",
                }
            )
        if current_market_refresh_at < reference.market_snapshot_at:
            return reference.model_copy(
                update={
                    "display_gap": True,
                    "display_gap_reason": "The current overview payload is older than the snapshot used for the last AI decision.",
                }
            )
    if reference.freshness_blocking:
        return reference.model_copy(
            update={
                "display_gap": True,
                "display_gap_reason": "The last AI decision used stale or incomplete market/account/order state, so new entry should remain blocked.",
            }
        )
    if _sync_summary_blocks_freshness(current_sync_freshness_summary):
        return reference.model_copy(
            update={
                "display_gap": True,
                "display_gap_reason": "Current account or order sync is now stale even though the last AI decision used fresher sync data.",
            }
        )
    return reference


def classify_audit_event(
    event_type: str,
    entity_type: str | None = None,
    payload: dict[str, Any] | None = None,
) -> str:
    event_key = (event_type or "").strip().lower()
    entity_key = (entity_type or "").strip().lower()
    payload_dict = payload or {}

    if event_key == "risk_check" or event_key.startswith("risk_") or entity_key == "risk_check":
        return AUDIT_CATEGORY_RISK

    if (
        event_key in AUDIT_APPROVAL_CONTROL_EVENT_TYPES
        or event_key.startswith("trading_auto_resume")
        or event_key.startswith("live_approval_")
    ):
        return AUDIT_CATEGORY_APPROVAL_CONTROL

    if (
        event_key in AUDIT_PROTECTION_EVENT_TYPES
        or event_key.startswith("protection_")
        or event_key.startswith("emergency_exit")
        or "protective" in event_key
    ):
        return AUDIT_CATEGORY_PROTECTION

    if event_key in AUDIT_EXECUTION_EVENT_TYPES or event_key.startswith("live_execution") or event_key.startswith("live_limit_"):
        return AUDIT_CATEGORY_EXECUTION

    if event_key in AUDIT_AI_EVENT_TYPES or event_key.startswith("decision_") or entity_key == "agent_run":
        return AUDIT_CATEGORY_AI_DECISION

    if (
        event_key in AUDIT_HEALTH_SYSTEM_EVENT_TYPES
        or event_key.endswith("_sync")
        or event_key.endswith("_sync_failed")
        or entity_key in {"binance", "openai", "scheduler_run"}
        or bool(payload_dict.get("health_status"))
    ):
        return AUDIT_CATEGORY_HEALTH_SYSTEM

    return AUDIT_CATEGORY_HEALTH_SYSTEM


def get_overview(session: Session) -> OverviewResponse:
    settings_row = get_or_create_settings(session)
    settings_payload = serialize_settings_runtime_summary(settings_row)
    runtime_state = summarize_runtime_state(settings_row)
    latest_market = session.scalar(select(MarketSnapshot).order_by(desc(MarketSnapshot.snapshot_time)).limit(1))
    latest_decision = session.scalar(
        select(AgentRun).where(AgentRun.role == "trading_decision").order_by(desc(AgentRun.created_at)).limit(1)
    )
    latest_risk = session.scalar(select(RiskCheck).order_by(desc(RiskCheck.created_at)).limit(1))
    active_entry_plans = list(
        session.scalars(
            active_pending_entry_plan_statement()
            .order_by(desc(PendingEntryPlan.created_at))
            .limit(20)
        )
    )
    latest_pnl = session.scalar(select(PnLSnapshot).order_by(desc(PnLSnapshot.created_at)).limit(1))
    open_positions = list(session.scalars(select(Position).where(Position.status == "open", Position.mode == "live")))
    protection_summary = [_build_position_protection_state(session, position) for position in open_positions]
    protected_positions = sum(1 for item in protection_summary if bool(item["protected"]))
    unprotected_positions = len(protection_summary) - protected_positions
    missing_protection_items: dict[str, list[str]] = {
        str(item["symbol"]): _as_string_list(item["missing_components"])
        for item in protection_summary
        if _as_string_list(item["missing_components"])
    }
    missing_protection_symbols = list(missing_protection_items)
    current_cycle_blocked_reasons = _risk_reason_codes_from_row(latest_risk)
    current_cycle_risk_allowed = latest_risk.allowed if latest_risk is not None else None
    operational_status = build_operational_status_payload(
        settings_row,
        session=session,
        runtime_state=runtime_state,
        operating_state_override=(
            PROTECTION_REQUIRED_STATE
            if not settings_row.trading_paused and unprotected_positions > 0
            else None
        ),
        missing_protection_symbols_override=missing_protection_symbols or None,
        missing_protection_items_override=missing_protection_items or None,
        blocked_reasons=current_cycle_blocked_reasons,
        latest_blocked_reasons=current_cycle_blocked_reasons,
        risk_allowed=current_cycle_risk_allowed,
        account_sync_summary=_as_dict(settings_payload.get("account_sync_summary", {})),
        sync_freshness_summary=_as_dict(settings_payload.get("sync_freshness_summary", {})),
        market_freshness_summary=_as_dict(settings_payload.get("market_freshness_summary", {})),
    )
    pnl_summary = _as_dict(settings_payload.get("pnl_summary", {}))
    exposure_summary = _as_dict(settings_payload.get("exposure_summary", {}))
    execution_policy_summary = _as_dict(settings_payload.get("execution_policy_summary", {}))
    market_context_summary = _compact_market_context_summary(settings_payload.get("market_context_summary", {}))
    adaptive_protection_summary = _as_dict(settings_payload.get("adaptive_protection_summary", {}))
    adaptive_signal_summary = _compact_adaptive_signal_summary(settings_payload.get("adaptive_signal_summary", {}))
    position_management_summary = _as_dict(settings_payload.get("position_management_summary", {}))
    current_market_refresh_at = _latest_market_refresh_at_for_decision(
        session,
        latest_decision,
        fallback_summary=operational_status.market_freshness_summary,
    )
    last_decision_reference = _annotate_decision_reference(
        _build_decision_reference(latest_decision),
        current_market_refresh_at=current_market_refresh_at,
        current_sync_freshness_summary=operational_status.sync_freshness_summary,
    )
    return OverviewResponse(
        mode=str(settings_payload["mode"]),
        symbol=settings_row.default_symbol,
        tracked_symbols=get_effective_symbols(settings_row),
        timeframe=settings_row.default_timeframe,
        latest_price=latest_market.latest_price if latest_market is not None else 0.0,
        latest_decision=(
            _build_decision_snapshot(latest_decision).model_dump(mode="json", exclude={"raw_output"})
            if latest_decision is not None
            else None
        ),
        latest_risk=_dashboard_risk_payload_from_row(latest_risk) if latest_risk is not None else None,
        active_entry_plans=[
            _build_pending_entry_plan_snapshot(row)
            for row in active_entry_plans
        ],
        operational_status=operational_status,
        last_market_refresh_at=current_market_refresh_at,
        last_decision_at=latest_decision.created_at if latest_decision is not None else None,
        last_decision_snapshot_at=last_decision_reference.market_snapshot_at,
        last_decision_reference=last_decision_reference,
        open_positions=len(open_positions),
        live_trading_enabled=operational_status.live_trading_enabled,
        live_execution_ready=operational_status.live_execution_ready,
        trading_paused=operational_status.trading_paused,
        approval_armed=operational_status.approval_armed,
        approval_expires_at=operational_status.approval_expires_at,
        can_enter_new_position=operational_status.can_enter_new_position,
        guard_mode_reason_category=operational_status.guard_mode_reason_category,
        guard_mode_reason_code=operational_status.guard_mode_reason_code,
        guard_mode_reason_message=operational_status.guard_mode_reason_message,
        pause_reason_code=operational_status.pause_reason_code,
        pause_origin=operational_status.pause_origin,
        pause_triggered_at=operational_status.pause_triggered_at,
        auto_resume_after=operational_status.auto_resume_after,
        auto_resume_status=operational_status.auto_resume_status,
        auto_resume_eligible=operational_status.auto_resume_eligible,
        auto_resume_last_blockers=operational_status.auto_resume_last_blockers,
        pause_severity=operational_status.pause_severity,
        pause_recovery_class=operational_status.pause_recovery_class,
        operating_state=operational_status.operating_state,
        protection_recovery_status=operational_status.protection_recovery_status,
        protection_recovery_active=operational_status.protection_recovery_active,
        protection_recovery_failure_count=operational_status.protection_recovery_failure_count,
        missing_protection_symbols=operational_status.missing_protection_symbols,
        missing_protection_items=operational_status.missing_protection_items,
        pnl_summary=pnl_summary,
        account_sync_summary=operational_status.account_sync_summary,
        sync_freshness_summary=operational_status.sync_freshness_summary,
        market_freshness_summary=operational_status.market_freshness_summary,
        exposure_summary=exposure_summary,
        execution_policy_summary=execution_policy_summary,
        market_context_summary=market_context_summary,
        adaptive_protection_summary=adaptive_protection_summary,
        adaptive_signal_summary=adaptive_signal_summary,
        position_management_summary=position_management_summary,
        user_stream_summary=dict(operational_status.user_stream_summary),
        reconciliation_summary=dict(operational_status.reconciliation_summary),
        candidate_selection_summary=dict(operational_status.candidate_selection_summary),
        operator_alert=dict(operational_status.operator_alert),
        daily_pnl=latest_pnl.daily_pnl if latest_pnl is not None else 0.0,
        cumulative_pnl=latest_pnl.cumulative_pnl if latest_pnl is not None else 0.0,
        blocked_reasons=operational_status.blocked_reasons,
        latest_blocked_reasons=operational_status.latest_blocked_reasons,
        protected_positions=protected_positions,
        unprotected_positions=unprotected_positions,
        position_protection_summary=protection_summary,
    )


def get_market_snapshots(
    session: Session,
    limit: int = 50,
    *,
    compact: bool = False,
    symbol: str | None = None,
    timeframe: str | None = None,
) -> list[dict[str, object]]:
    symbol_filter = _normalize_symbol_filter(symbol)
    timeframe_filter = _normalize_timeframe_filter(timeframe)
    if compact:
        statement = select(
            MarketSnapshot.id,
            MarketSnapshot.symbol,
            MarketSnapshot.timeframe,
            MarketSnapshot.snapshot_time,
            MarketSnapshot.latest_price,
            MarketSnapshot.latest_volume,
            MarketSnapshot.candle_count,
            MarketSnapshot.is_stale,
            MarketSnapshot.is_complete,
            MarketSnapshot.created_at,
            MarketSnapshot.updated_at,
        )
        if symbol_filter:
            statement = statement.where(MarketSnapshot.symbol == symbol_filter)
        if timeframe_filter:
            statement = statement.where(MarketSnapshot.timeframe == timeframe_filter)
        rows = session.execute(statement.order_by(desc(MarketSnapshot.snapshot_time)).limit(limit)).mappings()
        return [_serialize_mapping_row(row) for row in rows]

    statement = select(MarketSnapshot)
    if symbol_filter:
        statement = statement.where(MarketSnapshot.symbol == symbol_filter)
    if timeframe_filter:
        statement = statement.where(MarketSnapshot.timeframe == timeframe_filter)
    return _serialize_model_list(list(session.scalars(statement.order_by(desc(MarketSnapshot.snapshot_time)).limit(limit))))


def _feature_available_timeframes(payload: object, direct_timeframe: object) -> list[str]:
    timeframes: list[str] = []
    direct = str(direct_timeframe or "").strip()
    if direct:
        timeframes.append(direct)
    multi_timeframe = _as_dict(_as_dict(payload).get("multi_timeframe"))
    for key in multi_timeframe:
        timeframe = str(key).strip()
        if timeframe and timeframe not in timeframes:
            timeframes.append(timeframe)
    return timeframes


def _compact_feature_snapshot_row(row: Mapping[str, object]) -> dict[str, object]:
    payload = _serialize_mapping_row(row)
    raw_feature_payload = payload.pop("payload", None)
    payload["available_timeframes"] = _feature_available_timeframes(
        raw_feature_payload,
        payload.get("timeframe"),
    )
    return payload


def get_feature_snapshots(
    session: Session,
    limit: int = 50,
    *,
    compact: bool = False,
    symbol: str | None = None,
    timeframe: str | None = None,
) -> list[dict[str, object]]:
    symbol_filter = _normalize_symbol_filter(symbol)
    timeframe_filter = _normalize_timeframe_filter(timeframe)
    if compact:
        statement = select(
            FeatureSnapshot.id,
            FeatureSnapshot.symbol,
            FeatureSnapshot.timeframe,
            FeatureSnapshot.market_snapshot_id,
            FeatureSnapshot.feature_time,
            FeatureSnapshot.trend_score,
            FeatureSnapshot.volatility_pct,
            FeatureSnapshot.volume_ratio,
            FeatureSnapshot.drawdown_pct,
            FeatureSnapshot.rsi,
            FeatureSnapshot.atr,
            FeatureSnapshot.payload,
            FeatureSnapshot.created_at,
            FeatureSnapshot.updated_at,
        )
        if symbol_filter:
            statement = statement.where(FeatureSnapshot.symbol == symbol_filter)
        if timeframe_filter:
            statement = statement.where(FeatureSnapshot.timeframe == timeframe_filter)
        rows = session.execute(statement.order_by(desc(FeatureSnapshot.feature_time)).limit(limit)).mappings()
        return [_compact_feature_snapshot_row(row) for row in rows]

    statement = select(FeatureSnapshot)
    if symbol_filter:
        statement = statement.where(FeatureSnapshot.symbol == symbol_filter)
    if timeframe_filter:
        statement = statement.where(FeatureSnapshot.timeframe == timeframe_filter)
    return _serialize_model_list(list(session.scalars(statement.order_by(desc(FeatureSnapshot.feature_time)).limit(limit))))


MARKET_CHART_TRADE_DECISIONS = {"long", "short", "enter_long", "enter_short", "reduce", "exit"}


def _market_chart_timestamp(value: object) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _market_chart_optional_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _market_chart_first_price(*values: object) -> float | None:
    for value in values:
        price = _market_chart_optional_float(value)
        if price is not None:
            return price
    return None


def _market_chart_zone_price(min_price: object, max_price: object) -> float | None:
    zone_min = _market_chart_optional_float(min_price)
    zone_max = _market_chart_optional_float(max_price)
    if zone_min is not None and zone_max is not None:
        return (zone_min + zone_max) / 2
    return zone_min or zone_max


def _market_chart_source_id(prefix: str, row_id: object) -> str | None:
    if row_id is None:
        return None
    return f"{prefix}:{row_id}"


def _market_chart_reason_codes(*values: object) -> list[str]:
    codes: list[str] = []
    for value in values:
        for code in _as_string_list(value):
            if code not in codes:
                codes.append(code)
    return codes


def _market_chart_marker(
    *,
    timestamp: object,
    kind: str,
    label: str,
    detail: str,
    symbol: str,
    action: str,
    price: float | None,
    status_label: str,
    reason_codes: Sequence[str] | None = None,
    source_id: str | None = None,
) -> dict[str, object] | None:
    normalized_timestamp = _market_chart_timestamp(timestamp)
    normalized_symbol = str(symbol or "").strip().upper()
    if not normalized_timestamp or not normalized_symbol:
        return None
    codes = list(reason_codes or [])
    reason_label = ", ".join(codes) if codes else None
    return {
        "timestamp": normalized_timestamp,
        "kind": kind,
        "label": label,
        "detail": detail,
        "symbol": normalized_symbol,
        "action": action,
        "price": price,
        "statusLabel": status_label,
        "reasonLabel": reason_label,
        "reasonCodes": codes,
        "sourceId": source_id,
    }


def _market_chart_decision_price(row: DecisionPerformanceFact) -> float | None:
    telemetry = _as_dict(row.telemetry_output)
    return _market_chart_first_price(
        telemetry.get("entry_price"),
        telemetry.get("recommended_entry_price"),
        telemetry.get("reference_price"),
        telemetry.get("latest_price"),
        _market_chart_zone_price(row.entry_zone_min, row.entry_zone_max),
        row.stop_loss,
        row.take_profit,
    )


def _market_chart_decision_markers(
    session: Session,
    *,
    symbol: str,
    limit: int,
) -> list[dict[str, object]]:
    rows = list(
        session.scalars(
            select(DecisionPerformanceFact)
            .where(DecisionPerformanceFact.symbol == symbol)
            .order_by(desc(DecisionPerformanceFact.created_at), desc(DecisionPerformanceFact.id))
            .limit(limit)
        )
    )
    markers: list[dict[str, object]] = []
    for row in rows:
        decision = str(row.decision or "").strip().lower()
        if decision not in MARKET_CHART_TRADE_DECISIONS:
            continue
        telemetry = _as_dict(row.telemetry_output)
        confidence = _market_chart_optional_float(telemetry.get("confidence"))
        detail = decision if confidence is None else f"{decision} / confidence {confidence:.2f}"
        marker = _market_chart_marker(
            timestamp=row.created_at,
            kind="ai",
            label="AI",
            detail=detail,
            symbol=row.symbol,
            action=decision,
            price=_market_chart_decision_price(row),
            status_label="AI recommendation",
            reason_codes=_market_chart_reason_codes(row.rationale_codes, telemetry.get("rationale_codes")),
            source_id=_market_chart_source_id("ai", row.decision_run_id),
        )
        if marker is not None:
            markers.append(marker)
    return markers


def _market_chart_pending_plans_by_decision(
    session: Session,
    decision_ids: Sequence[int | None],
) -> dict[int, PendingEntryPlan]:
    normalized_ids = [decision_id for decision_id in decision_ids if decision_id is not None]
    if not normalized_ids:
        return {}
    plans = session.scalars(
        select(PendingEntryPlan)
        .where(PendingEntryPlan.source_decision_run_id.in_(normalized_ids))
        .order_by(desc(PendingEntryPlan.updated_at), desc(PendingEntryPlan.created_at), desc(PendingEntryPlan.id))
    )
    by_decision: dict[int, PendingEntryPlan] = {}
    for plan in plans:
        if plan.source_decision_run_id is not None:
            by_decision.setdefault(plan.source_decision_run_id, plan)
    return by_decision


def _market_chart_risk_price(row: RiskCheck, plan: PendingEntryPlan | None) -> float | None:
    payload = _as_dict(row.payload)
    debug_payload = _as_dict(payload.get("debug_payload"))
    entry_trigger = _as_dict(debug_payload.get("entry_trigger"))
    price = _market_chart_first_price(
        payload.get("reference_price"),
        payload.get("latest_price"),
        payload.get("mark_price"),
        payload.get("entry_price"),
        debug_payload.get("reference_price"),
        entry_trigger.get("reference_price"),
        entry_trigger.get("latest_price"),
        payload.get("approved_entry_price"),
    )
    if price is not None:
        return price
    if plan is None:
        return None
    return _market_chart_zone_price(plan.entry_zone_min, plan.entry_zone_max)


def _market_chart_risk_markers(
    session: Session,
    *,
    symbol: str,
    limit: int,
) -> list[dict[str, object]]:
    rows = list(
        session.scalars(
            select(RiskCheck)
            .where(RiskCheck.symbol == symbol)
            .order_by(desc(RiskCheck.created_at), desc(RiskCheck.id))
            .limit(limit)
        )
    )
    plans_by_decision = _market_chart_pending_plans_by_decision(
        session,
        [row.decision_run_id for row in rows],
    )
    markers: list[dict[str, object]] = []
    for row in rows:
        payload = _as_dict(row.payload)
        decision = str(row.decision or payload.get("decision") or "").strip().lower()
        reason_codes = _market_chart_reason_codes(
            payload.get("blocked_reason_codes"),
            row.reason_codes,
            payload.get("reason_codes"),
        )
        if decision not in MARKET_CHART_TRADE_DECISIONS and all(code == "HOLD_DECISION" for code in reason_codes):
            continue
        blocked = row.allowed is not True
        marker = _market_chart_marker(
            timestamp=row.created_at,
            kind="risk_blocked" if blocked else "risk_approved",
            label="blocked" if blocked else "approved",
            detail=f"{decision or 'unknown'} risk {'blocked' if blocked else 'approved'}",
            symbol=row.symbol,
            action=decision or "unknown",
            price=_market_chart_risk_price(row, plans_by_decision.get(row.decision_run_id)),
            status_label="risk blocked" if blocked else "risk approved",
            reason_codes=reason_codes if blocked else [],
            source_id=_market_chart_source_id("risk", row.id),
        )
        if marker is not None:
            markers.append(marker)
    return markers


def _market_chart_order_price(row: Order) -> float | None:
    metadata = _as_dict(row.metadata_json)
    exchange_order = _as_dict(metadata.get("exchange_order"))
    submit_request = _as_dict(metadata.get("submit_request"))
    return _market_chart_first_price(
        row.average_fill_price,
        row.requested_price,
        exchange_order.get("actualPrice"),
        exchange_order.get("stopPrice"),
        submit_request.get("reference_price"),
    )


def _market_chart_execution_price(row: Execution) -> float | None:
    payload = _as_dict(row.payload)
    trade = _as_dict(payload.get("trade"))
    return _market_chart_first_price(
        row.fill_price,
        trade.get("price"),
        payload.get("requested_price"),
    )


def _market_chart_execution_markers(
    session: Session,
    *,
    symbol: str,
    limit: int,
) -> tuple[list[dict[str, object]], set[int]]:
    rows = session.execute(
        select(Execution, Order)
        .outerjoin(Order, Order.id == Execution.order_id)
        .where(Execution.symbol == symbol, Order.mode == "live")
        .order_by(desc(Execution.created_at), desc(Execution.id))
        .limit(limit)
    ).all()
    markers: list[dict[str, object]] = []
    order_ids: set[int] = set()
    for execution, order in rows:
        if execution.order_id is not None:
            order_ids.add(execution.order_id)
        payload = _as_dict(execution.payload)
        trade = _as_dict(payload.get("trade"))
        action_parts = [
            str(order.side if order is not None else payload.get("side") or trade.get("side") or "").strip(),
            str(order.order_type if order is not None else payload.get("order_type") or "").strip(),
        ]
        action = " ".join(part for part in action_parts if part) or "execution"
        marker = _market_chart_marker(
            timestamp=execution.created_at,
            kind="execution",
            label="fill",
            detail=f"execution {execution.status}",
            symbol=execution.symbol,
            action=action,
            price=_market_chart_execution_price(execution),
            status_label="execution",
            source_id=_market_chart_source_id("execution", execution.id),
        )
        if marker is not None:
            markers.append(marker)
    return markers, order_ids


def _market_chart_order_markers(
    session: Session,
    *,
    symbol: str,
    limit: int,
    execution_order_ids: set[int],
) -> list[dict[str, object]]:
    rows = list(
        session.scalars(
            select(Order)
            .where(Order.mode == "live", Order.symbol == symbol)
            .order_by(desc(Order.created_at), desc(Order.id))
            .limit(limit)
        )
    )
    markers: list[dict[str, object]] = []
    for row in rows:
        status = str(row.status or row.exchange_status or "").strip().lower()
        if row.id in execution_order_ids and status == "filled":
            continue
        price = _market_chart_order_price(row)
        if price is None:
            continue
        action = " ".join(part for part in (row.side, row.order_type) if str(part or "").strip()) or "order"
        marker = _market_chart_marker(
            timestamp=row.created_at,
            kind="execution",
            label="order",
            detail=f"order {status or 'unknown'}",
            symbol=row.symbol,
            action=action,
            price=price,
            status_label="execution",
            reason_codes=_market_chart_reason_codes(row.reason_codes),
            source_id=_market_chart_source_id("order", row.id),
        )
        if marker is not None:
            markers.append(marker)
    return markers


def get_market_chart_markers(
    session: Session,
    *,
    symbol: str,
    limit: int = 80,
) -> list[dict[str, object]]:
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        return []
    decision_markers = _market_chart_decision_markers(session, symbol=normalized_symbol, limit=limit)
    risk_markers = _market_chart_risk_markers(session, symbol=normalized_symbol, limit=limit)
    execution_markers, execution_order_ids = _market_chart_execution_markers(
        session,
        symbol=normalized_symbol,
        limit=limit,
    )
    order_markers = _market_chart_order_markers(
        session,
        symbol=normalized_symbol,
        limit=limit,
        execution_order_ids=execution_order_ids,
    )
    markers = decision_markers + risk_markers + execution_markers + order_markers
    seen: set[str] = set()
    deduped: list[dict[str, object]] = []
    for marker in sorted(markers, key=lambda item: str(item.get("timestamp") or "")):
        key = str(
            marker.get("sourceId")
            or f"{marker.get('kind')}:{marker.get('symbol')}:{marker.get('timestamp')}:{marker.get('action')}:{marker.get('price')}"
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(marker)
    return deduped


def get_decisions(session: Session, limit: int = 50, *, compact: bool = False) -> list[dict[str, object]]:
    rows = list(
        session.scalars(
            select(AgentRun)
            .where(AgentRun.role == "trading_decision")
            .order_by(desc(AgentRun.created_at))
            .limit(limit)
        )
    )
    payloads: list[dict[str, object]] = []
    for row in rows:
        payload = _compact_decision_row(row) if compact else _serialize_model_row(row)
        _attach_decision_summary_fields(payload)
        payload["ai_trigger_reason"] = _ai_trigger_reason_from_decision_row(row)
        payload["ai_review_type"] = _ai_review_type_from_decision_row(row)
        payload["ai_trigger_reason_codes"] = _ai_trigger_reason_codes_from_decision_row(row)
        payload["last_ai_skip_reason"] = _last_ai_skip_reason_from_decision_row(row)
        payload["ai_skip_reason"] = payload["last_ai_skip_reason"]
        payload["ai_trigger_summary"] = _ai_trigger_summary_from_decision_row(row)
        payload["market_signal_summary"] = _market_signal_summary_from_decision_row(row)
        payloads.append(payload)
    return payloads


def get_positions(session: Session, limit: int = 50) -> list[dict[str, object]]:
    rows = list(
        session.scalars(
            select(Position)
            .where(
                Position.mode == "live",
                Position.status == "open",
                Position.quantity > 0,
            )
            .order_by(desc(Position.created_at))
            .limit(limit)
        )
    )
    payloads = _serialize_model_list(rows)
    for payload, position in zip(payloads, rows, strict=False):
        protection_state = _build_position_protection_state(session, position)
        payload["protection_status"] = protection_state["status"]
        payload.update(
            {
                key: value
                for key, value in protection_state.items()
                if key != "status"
            }
        )
    return payloads


def _default_close_execution_sync_payload() -> dict[str, object]:
    return {
        "pnl_source": "LOCAL_EXECUTIONS",
        "close_execution_sync_status": "UNKNOWN",
        "realized_pnl_confirmed": True,
        "missing_close_execution": False,
        "fee_source": "LOCAL_EXECUTIONS",
        "fee_confirmed": True,
        "warning_message": None,
        "blocked_reason": None,
        "fee_warning_message": None,
    }


def _protective_backfill_status(order_row: Order) -> str | None:
    metadata = _as_dict(order_row.metadata_json)
    state = _as_dict(metadata.get("protective_close_fill_backfill"))
    status = str(state.get("status") or "").upper()
    return status or None


def _is_finished_protective_order(order_row: Order) -> bool:
    if str(order_row.order_type or "").lower() not in PROTECTIVE_ORDER_TYPES:
        return False
    exchange_status = str(order_row.exchange_status or "").upper()
    local_status = str(order_row.status or "").lower()
    return exchange_status == "FINISHED" or local_status == "filled"


def _execution_uses_exchange_pnl(execution_row: Execution) -> bool:
    payload = _as_dict(execution_row.payload)
    trade_payload = _as_dict(payload.get("trade"))
    return bool(
        str(payload.get("source") or "").upper() == "EXCHANGE_BACKFILL"
        or str(payload.get("exchange") or "").upper() == "BINANCE"
        or payload.get("realized_pnl_source") == "binance_user_trades"
        or "realizedPnl" in trade_payload
    )


def _close_execution_sync_payload(
    *,
    position: Position | None,
    orders: Sequence[Order],
    execution_rows: Sequence[tuple[Execution, Order | None]],
) -> dict[str, object]:
    payload = _default_close_execution_sync_payload()
    entry_execution_exists = any(order_row is not None and _is_entry_order(order_row) for _execution, order_row in execution_rows)
    close_executions = [
        execution_row
        for execution_row, order_row in execution_rows
        if order_row is not None and _is_close_order(order_row)
    ]
    close_execution_exists = bool(close_executions)
    position_closed = bool(position is not None and (position.status != "open" or _as_float(position.quantity) <= 0))
    protective_finished = any(_is_finished_protective_order(order_row) for order_row in orders)
    needs_close_execution = entry_execution_exists and (position_closed or protective_finished)
    if not entry_execution_exists:
        return payload
    if close_execution_exists:
        pnl_source = "EXCHANGE" if any(_execution_uses_exchange_pnl(row) for row in close_executions) else "LOCAL_EXECUTIONS"
        return {
            **payload,
            "pnl_source": pnl_source,
            "close_execution_sync_status": "COMPLETE",
            "realized_pnl_confirmed": True,
            "fee_confirmed": True,
        }
    if not needs_close_execution:
        return payload

    reason_codes = {
        code
        for order_row in orders
        for code in _as_string_list(order_row.reason_codes)
    }
    backfill_statuses = {_protective_backfill_status(order_row) for order_row in orders}
    if (
        PROTECTIVE_CLOSE_FILL_BACKFILL_FAILED_REASON_CODE in reason_codes
        or "FAILED" in backfill_statuses
    ):
        status = "FAILED"
        warning_message = "정산/청산 체결 동기화 실패"
    elif (
        PROTECTIVE_CLOSE_FILL_BACKFILL_PENDING_REASON_CODE in reason_codes
        or "PENDING" in backfill_statuses
    ):
        status = "PENDING"
        warning_message = "정산/청산 체결 동기화 미완료"
    else:
        status = "MISSING"
        warning_message = "청산 체결 누락: 거래소 손익 미반영"
    return {
        **payload,
        "pnl_source": "UNKNOWN",
        "close_execution_sync_status": status,
        "realized_pnl_confirmed": False,
        "missing_close_execution": True,
        "fee_confirmed": False,
        "warning_message": warning_message,
        "blocked_reason": warning_message,
        "fee_warning_message": "청산 수수료 미반영",
    }


def _close_execution_sync_payloads_by_position(
    session: Session,
    rows: Sequence[Order],
) -> dict[int, dict[str, object]]:
    position_ids = sorted({row.position_id for row in rows if row.position_id is not None})
    if not position_ids:
        return {}
    positions_by_id = {
        row.id: row
        for row in session.scalars(select(Position).where(Position.id.in_(position_ids)))
    }
    position_orders = list(
        session.scalars(
            select(Order)
            .where(Order.position_id.in_(position_ids), Order.mode == "live")
            .order_by(Order.created_at.asc(), Order.id.asc())
        )
    )
    orders_by_position: dict[int, list[Order]] = defaultdict(list)
    order_ids: list[int] = []
    for order_row in position_orders:
        if order_row.position_id is not None:
            orders_by_position[order_row.position_id].append(order_row)
        if order_row.id is not None:
            order_ids.append(order_row.id)
    execution_statement = (
        select(Execution, Order)
        .outerjoin(Order, Order.id == Execution.order_id)
        .where(
            or_(
                Execution.position_id.in_(position_ids),
                Execution.order_id.in_(order_ids),
            )
        )
        .order_by(Execution.created_at.asc(), Execution.id.asc())
    )
    executions_by_position: dict[int, list[tuple[Execution, Order | None]]] = defaultdict(list)
    for execution_row, order_row in session.execute(execution_statement):
        position_id = execution_row.position_id or (order_row.position_id if order_row is not None else None)
        if position_id is not None:
            executions_by_position[position_id].append((execution_row, order_row))
    return {
        position_id: _close_execution_sync_payload(
            position=positions_by_id.get(position_id),
            orders=orders_by_position.get(position_id, []),
            execution_rows=executions_by_position.get(position_id, []),
        )
        for position_id in position_ids
    }


def get_orders(
    session: Session,
    limit: int = 50,
    mode: str | None = None,
    symbol: str | None = None,
    status: str | None = None,
    search: str | None = None,
    position_id: int | None = None,
    compact: bool = False,
) -> list[dict[str, object]]:
    selected_mode = mode or "live"
    statement = select(Order).where(Order.mode == selected_mode)
    if symbol:
        statement = statement.where(Order.symbol == symbol.upper())
    if position_id is not None:
        statement = statement.where(Order.position_id == position_id)
    if status:
        statement = statement.where(Order.status == status)
    if search:
        token = f"%{search.lower()}%"
        statement = statement.where(
            or_(
                func.lower(Order.symbol).like(token),
                func.lower(Order.side).like(token),
                func.lower(Order.order_type).like(token),
                func.lower(Order.status).like(token),
                func.lower(cast(Order.external_order_id, String)).like(token),
                func.lower(cast(Order.client_order_id, String)).like(token),
            )
        )
    statement = statement.order_by(desc(Order.created_at)).limit(limit)
    rows = list(session.scalars(statement))
    close_sync_by_position = _close_execution_sync_payloads_by_position(session, rows)
    payloads: list[dict[str, object]] = []
    for row in rows:
        payload = _serialize_model_row(row)
        payload.update(
            close_sync_by_position.get(row.position_id, _default_close_execution_sync_payload())
        )
        if compact:
            payload = _compact_order_history_payload(payload)
        payloads.append(payload)
    return payloads


def get_executions(
    session: Session,
    limit: int = 50,
    mode: str | None = None,
    symbol: str | None = None,
    status: str | None = None,
    search: str | None = None,
    position_id: int | None = None,
    compact: bool = False,
) -> list[dict[str, object]]:
    selected_mode = mode or "live"
    statement = (
        select(Execution, Order, AgentRun.output_payload)
        .outerjoin(Order, Order.id == Execution.order_id)
        .outerjoin(AgentRun, AgentRun.id == Order.decision_run_id)
        .where(Order.mode == selected_mode)
    )
    if symbol:
        statement = statement.where(Execution.symbol == symbol.upper())
    if position_id is not None:
        statement = statement.where(
            or_(
                Execution.position_id == position_id,
                Order.position_id == position_id,
            )
        )
    if status:
        statement = statement.where(Execution.status == status)
    if search:
        token = f"%{search.lower()}%"
        statement = statement.where(
            or_(
                func.lower(Execution.symbol).like(token),
                func.lower(Execution.status).like(token),
                func.lower(cast(Execution.external_trade_id, String)).like(token),
                func.lower(cast(Execution.commission_asset, String)).like(token),
            )
        )
    statement = statement.order_by(desc(Execution.created_at)).limit(limit)
    rows = session.execute(statement).all()
    payloads: list[dict[str, object]] = []
    for execution, order_row, decision_output in rows:
        values = {}
        for key in execution.__table__.columns:  # type: ignore[attr-defined]
            value = getattr(execution, key.name)
            values[key.name] = value.isoformat() if hasattr(value, "isoformat") else value
        order_metadata = order_row.metadata_json if order_row is not None and isinstance(order_row.metadata_json, dict) else {}
        decision_payload = decision_output if isinstance(decision_output, dict) else {}
        values["mode"] = order_row.mode if order_row is not None else "unknown"
        values["order_type"] = order_row.order_type if order_row is not None else None
        values["order_status"] = order_row.status if order_row is not None else None
        values["requested_quantity"] = order_row.requested_quantity if order_row is not None else None
        values["requested_price"] = order_row.requested_price if order_row is not None else None
        values["decision_run_id"] = order_row.decision_run_id if order_row is not None else None
        values["execution_policy"] = order_metadata.get("execution_policy", {})
        values["execution_quality"] = order_metadata.get("execution_quality", {})
        values["decision_summary"] = {
            "decision": decision_payload.get("decision"),
            "timeframe": decision_payload.get("timeframe"),
            "confidence": decision_payload.get("confidence"),
            "rationale_codes": decision_payload.get("rationale_codes", []),
        }
        if compact:
            values = _compact_execution_history_payload(values)
        payloads.append(values)
    return payloads


def get_execution_quality_report(session: Session) -> dict[str, object]:
    now = utcnow_naive()
    windows = [
        ("24h", now - timedelta(hours=24)),
        ("7d", now - timedelta(days=7)),
    ]
    rows = session.execute(
        select(Order, AgentRun.output_payload)
        .outerjoin(AgentRun, AgentRun.id == Order.decision_run_id)
        .where(Order.mode == "live")
        .order_by(desc(Order.created_at))
    ).all()
    order_ids = [order_row.id for order_row, _ in rows]
    executions_by_order: dict[int, list[Execution]] = defaultdict(list)
    if order_ids:
        for execution_row in session.scalars(
            select(Execution)
            .where(Execution.order_id.in_(order_ids))
            .order_by(Execution.created_at.asc(), Execution.id.asc())
        ):
            if execution_row.order_id is not None:
                executions_by_order[execution_row.order_id].append(execution_row)
    report_windows: list[dict[str, object]] = []

    for label, cutoff in windows:
        bucket_orders = [
            (order_row, decision_output)
            for order_row, decision_output in rows
            if order_row.created_at >= cutoff
        ]
        by_profile: dict[str, dict[str, object]] = defaultdict(
            lambda: {
                "policy_profile": "unknown",
                "orders": 0,
                "partial_fill_orders": 0,
                "aggressive_fallback_orders": 0,
                "cancel_attempts": 0,
                "cancel_successes": 0,
                "avg_arrival_slippage_pct_sum": 0.0,
                "avg_arrival_slippage_pct_count": 0,
                "avg_slippage_pct_sum": 0.0,
                "avg_slippage_pct_count": 0,
                "avg_first_fill_latency_seconds_sum": 0.0,
                "avg_first_fill_latency_seconds_count": 0,
            }
        )
        summary = {
            "orders": 0,
            "filled_orders": 0,
            "partial_fill_orders": 0,
            "market_orders": 0,
            "limit_orders": 0,
            "repriced_orders": 0,
            "aggressive_fallback_orders": 0,
            "execution_degraded_orders": 0,
            "decision_profit_orders": 0,
            "decision_loss_orders": 0,
            "decision_pending_orders": 0,
            "average_arrival_slippage_pct": 0.0,
            "average_realized_slippage_pct": 0.0,
            "average_first_fill_latency_seconds": 0.0,
            "cancel_attempts": 0,
            "cancel_successes": 0,
            "cancel_success_rate": 0.0,
            "fee_total": 0.0,
            "realized_pnl_total": 0.0,
            "net_realized_pnl_total": 0.0,
        }
        arrival_slippage_sum = 0.0
        arrival_slippage_count = 0
        slippage_sum = 0.0
        slippage_count = 0
        first_fill_latency_sum = 0.0
        first_fill_latency_count = 0

        for order_row, decision_output in bucket_orders:
            metadata = order_row.metadata_json if isinstance(order_row.metadata_json, dict) else {}
            quality = metadata.get("execution_quality") if isinstance(metadata.get("execution_quality"), dict) else {}
            policy = metadata.get("execution_policy") if isinstance(metadata.get("execution_policy"), dict) else {}
            decision_payload = decision_output if isinstance(decision_output, dict) else {}
            order_executions = executions_by_order.get(order_row.id, [])
            order_metrics = _execution_quality_metrics_for_order(
                order_row,
                order_executions,
            )
            profile = str(policy.get("policy_profile") or "unknown")
            profile_bucket = by_profile[profile]
            profile_bucket["policy_profile"] = profile

            summary["orders"] += 1
            if order_row.status == "filled":
                summary["filled_orders"] += 1
            if order_row.order_type == "market":
                summary["market_orders"] += 1
            elif order_row.order_type == "limit":
                summary["limit_orders"] += 1

            partial_fill_attempts = _as_int(quality.get("partial_fill_attempts"))
            repriced_attempts = _as_int(quality.get("repriced_attempts"))
            aggressive_fallback_used = bool(quality.get("aggressive_fallback_used"))
            realized_slippage_pct = _as_float(quality.get("realized_slippage_pct"))
            decision_quality_status = str(quality.get("decision_quality_status") or "signal_outcome_pending")
            execution_quality_status = str(quality.get("execution_quality_status") or "unknown")

            if partial_fill_attempts > 0 or order_row.status == "partially_filled":
                summary["partial_fill_orders"] += 1
                profile_bucket["partial_fill_orders"] = _as_int(profile_bucket["partial_fill_orders"]) + 1
            if repriced_attempts > 0:
                summary["repriced_orders"] += 1
            if aggressive_fallback_used:
                summary["aggressive_fallback_orders"] += 1
                profile_bucket["aggressive_fallback_orders"] = _as_int(profile_bucket["aggressive_fallback_orders"]) + 1
            if execution_quality_status not in {"clean_fill", "unknown"}:
                summary["execution_degraded_orders"] += 1
            if decision_quality_status == "profit":
                summary["decision_profit_orders"] += 1
            elif decision_quality_status == "loss":
                summary["decision_loss_orders"] += 1
            else:
                summary["decision_pending_orders"] += 1
            summary["cancel_attempts"] += int(order_metrics["cancel_attempt"])
            summary["cancel_successes"] += int(order_metrics["cancel_success"])

            summary["fee_total"] += _as_float(quality.get("fees_total"))
            summary["realized_pnl_total"] += _as_float(quality.get("realized_pnl_total"))
            summary["net_realized_pnl_total"] += _as_float(quality.get("net_realized_pnl_total"))
            has_arrival_metric = quality.get("arrival_slippage_pct") is not None or bool(order_executions)
            has_realized_metric = quality.get("realized_slippage_pct") is not None or bool(order_executions)
            has_first_fill_latency_metric = quality.get("first_fill_latency_seconds") is not None or bool(order_executions)
            if has_arrival_metric:
                arrival_slippage_sum += _as_float(order_metrics["arrival_slippage_pct"], default=0.0)
                arrival_slippage_count += 1
                profile_bucket["avg_arrival_slippage_pct_sum"] = _as_float(
                    profile_bucket["avg_arrival_slippage_pct_sum"]
                ) + _as_float(order_metrics["arrival_slippage_pct"], default=0.0)
                profile_bucket["avg_arrival_slippage_pct_count"] = _as_int(
                    profile_bucket["avg_arrival_slippage_pct_count"]
                ) + 1
            if has_realized_metric:
                effective_realized_slippage = _as_float(order_metrics["realized_slippage_pct"], default=realized_slippage_pct)
                slippage_sum += effective_realized_slippage
                slippage_count += 1
                profile_bucket["avg_slippage_pct_sum"] = _as_float(
                    profile_bucket["avg_slippage_pct_sum"]
                ) + effective_realized_slippage
                profile_bucket["avg_slippage_pct_count"] = _as_int(profile_bucket["avg_slippage_pct_count"]) + 1
            if has_first_fill_latency_metric:
                first_fill_latency_sum += _as_float(order_metrics["first_fill_latency_seconds"], default=0.0)
                first_fill_latency_count += 1
                profile_bucket["avg_first_fill_latency_seconds_sum"] = _as_float(
                    profile_bucket["avg_first_fill_latency_seconds_sum"]
                ) + _as_float(order_metrics["first_fill_latency_seconds"], default=0.0)
                profile_bucket["avg_first_fill_latency_seconds_count"] = _as_int(
                    profile_bucket["avg_first_fill_latency_seconds_count"]
                ) + 1
            profile_bucket["cancel_attempts"] = _as_int(profile_bucket["cancel_attempts"]) + int(order_metrics["cancel_attempt"])
            profile_bucket["cancel_successes"] = _as_int(profile_bucket["cancel_successes"]) + int(order_metrics["cancel_success"])

            profile_bucket["orders"] = _as_int(profile_bucket["orders"]) + 1
            profile_bucket["symbol"] = order_row.symbol
            profile_bucket["timeframe"] = decision_payload.get("timeframe")

        summary["average_arrival_slippage_pct"] = (
            arrival_slippage_sum / arrival_slippage_count if arrival_slippage_count else 0.0
        )
        summary["average_realized_slippage_pct"] = slippage_sum / slippage_count if slippage_count else 0.0
        summary["average_first_fill_latency_seconds"] = (
            first_fill_latency_sum / first_fill_latency_count if first_fill_latency_count else 0.0
        )
        summary["cancel_success_rate"] = (
            summary["cancel_successes"] / summary["cancel_attempts"] if summary["cancel_attempts"] else 0.0
        )
        profiles = sorted(
            [
                {
                    "policy_profile": str(item["policy_profile"]),
                    "symbol": item.get("symbol"),
                    "timeframe": item.get("timeframe"),
                    "orders": _as_int(item["orders"]),
                    "partial_fill_orders": _as_int(item["partial_fill_orders"]),
                    "aggressive_fallback_orders": _as_int(item["aggressive_fallback_orders"]),
                    "cancel_attempts": _as_int(item["cancel_attempts"]),
                    "cancel_successes": _as_int(item["cancel_successes"]),
                    "cancel_success_rate": (
                        _as_int(item["cancel_successes"]) / _as_int(item["cancel_attempts"])
                        if _as_int(item["cancel_attempts"]) > 0
                        else 0.0
                    ),
                    "average_arrival_slippage_pct": (
                        _as_float(item["avg_arrival_slippage_pct_sum"]) / _as_int(item["avg_arrival_slippage_pct_count"])
                        if _as_int(item["avg_arrival_slippage_pct_count"]) > 0
                        else 0.0
                    ),
                    "average_realized_slippage_pct": (
                        _as_float(item["avg_slippage_pct_sum"]) / _as_int(item["avg_slippage_pct_count"])
                        if _as_int(item["avg_slippage_pct_count"]) > 0
                        else 0.0
                    ),
                    "average_first_fill_latency_seconds": (
                        _as_float(item["avg_first_fill_latency_seconds_sum"])
                        / _as_int(item["avg_first_fill_latency_seconds_count"])
                        if _as_int(item["avg_first_fill_latency_seconds_count"]) > 0
                        else 0.0
                    ),
                }
                for item in by_profile.values()
            ],
            key=lambda item: (-int(item["orders"]), str(item["policy_profile"])),
        )
        report_windows.append(
            {
                "window": label,
                "summary": summary,
                "decision_quality_summary": {
                    "profitable_orders": summary["decision_profit_orders"],
                    "loss_orders": summary["decision_loss_orders"],
                    "pending_or_flat_orders": summary["decision_pending_orders"],
                },
                "execution_quality_summary": {
                    "degraded_orders": summary["execution_degraded_orders"],
                    "partial_fill_orders": summary["partial_fill_orders"],
                    "repriced_orders": summary["repriced_orders"],
                    "aggressive_fallback_orders": summary["aggressive_fallback_orders"],
                    "average_arrival_slippage_pct": summary["average_arrival_slippage_pct"],
                    "average_realized_slippage_pct": summary["average_realized_slippage_pct"],
                    "average_first_fill_latency_seconds": summary["average_first_fill_latency_seconds"],
                    "cancel_attempts": summary["cancel_attempts"],
                    "cancel_successes": summary["cancel_successes"],
                    "cancel_success_rate": summary["cancel_success_rate"],
                },
                "profiles": profiles,
            }
        )

    return {
        "generated_at": now.isoformat(),
        "execution_quality_basis": "live_order_metadata_and_execution_ledger",
        "windows": report_windows,
    }


def _top_positive_entries(
    entries: list[PerformanceAggregateEntry],
    *,
    limit: int = 5,
) -> list[PerformanceAggregateEntry]:
    return sorted(
        entries,
        key=lambda item: (
            item.net_realized_pnl_total,
            item.wins - item.losses,
            item.decisions,
            item.key,
        ),
        reverse=True,
    )[:limit]


def _top_negative_entries(
    entries: list[PerformanceAggregateEntry],
    *,
    limit: int = 5,
) -> list[PerformanceAggregateEntry]:
    return sorted(
        entries,
        key=lambda item: (
            item.net_realized_pnl_total,
            item.losses - item.wins,
            -item.decisions,
            item.key,
        ),
    )[:limit]


def _top_execution_profiles(window_payload: dict[str, object], *, limit: int = 5) -> list[DashboardExecutionProfileSummary]:
    raw_profiles = window_payload.get("profiles")
    if not isinstance(raw_profiles, list):
        return []
    profiles: list[DashboardExecutionProfileSummary] = []
    for item in raw_profiles:
        if not isinstance(item, dict):
            continue
        profiles.append(
            DashboardExecutionProfileSummary(
                policy_profile=str(item.get("policy_profile") or "unknown"),
                symbol=str(item.get("symbol")) if item.get("symbol") is not None else None,
                timeframe=str(item.get("timeframe")) if item.get("timeframe") is not None else None,
                orders=_as_int(item.get("orders"), default=0),
                partial_fill_orders=_as_int(item.get("partial_fill_orders"), default=0),
                aggressive_fallback_orders=_as_int(item.get("aggressive_fallback_orders"), default=0),
                cancel_attempts=_as_int(item.get("cancel_attempts"), default=0),
                cancel_successes=_as_int(item.get("cancel_successes"), default=0),
                cancel_success_rate=_as_float(item.get("cancel_success_rate"), default=0.0),
                average_arrival_slippage_pct=_as_float(item.get("average_arrival_slippage_pct"), default=0.0),
                average_realized_slippage_pct=_as_float(item.get("average_realized_slippage_pct"), default=0.0),
                average_first_fill_latency_seconds=_as_float(
                    item.get("average_first_fill_latency_seconds"),
                    default=0.0,
                ),
            )
        )
    return sorted(
        profiles,
        key=lambda item: (
            item.average_realized_slippage_pct,
            item.average_arrival_slippage_pct,
            item.average_first_fill_latency_seconds,
            item.partial_fill_orders,
            item.aggressive_fallback_orders,
            item.orders,
        ),
        reverse=True,
    )[:limit]


def get_profitability_dashboard(
    session: Session,
    *,
    overview: OverviewResponse | None = None,
    performance_window_specs: Sequence[tuple[str, int]] | None = None,
    cost_window_specs: Sequence[tuple[str, int | None]] | None = None,
) -> DashboardProfitabilityResponse:
    overview = overview or get_overview(session)
    now = utcnow_naive()
    selected_cost_window_specs = tuple(cost_window_specs or PROFITABILITY_COST_WINDOW_SPECS)
    performance_report = build_signal_performance_report(
        session,
        window_specs=performance_window_specs,
    )
    execution_report = get_execution_quality_report(session)

    cost_breakdown_cache: dict[tuple[str, int | None, datetime | None], DashboardProfitabilityCostBreakdown] = {}

    def cost_breakdown_for(
        *,
        window_label: str,
        window_hours: int | None,
        summary: PerformanceWindowSummary | None,
    ) -> DashboardProfitabilityCostBreakdown:
        since = _profitability_window_since(window_label, window_hours, now)
        cache_key = (window_label, window_hours, since)
        cached = cost_breakdown_cache.get(cache_key)
        if cached is None:
            cached = _build_profitability_cost_breakdown(
                session,
                window_label=window_label,
                window_hours=window_hours,
                since=since,
                summary=summary,
            )
            cost_breakdown_cache[cache_key] = cached
        return cached

    cost_breakdown_by_label = {
        window.window_label: cost_breakdown_for(
            window_label=window.window_label,
            window_hours=window.window_hours,
            summary=window.summary,
        )
        for window in performance_report.windows
    }
    summary_by_label = {window.window_label: window.summary for window in performance_report.windows}
    cost_breakdowns = [
        cost_breakdown_for(
            window_label=window_label,
            window_hours=window_hours,
            summary=summary_by_label.get(window_label),
        )
        for window_label, window_hours in selected_cost_window_specs
    ]

    windows: list[DashboardProfitabilityWindow] = []
    for window in performance_report.windows:
        windows.append(
            DashboardProfitabilityWindow(
                window_label=window.window_label,
                window_hours=window.window_hours,
                summary=window.summary,
                cost_breakdown=cost_breakdown_by_label[window.window_label],
                entry_quality=window.entry_quality,
                ai_baseline_comparison=window.ai_baseline_comparison,
                limited_live_readiness=window.limited_live_readiness,
                rationale_winners=_top_positive_entries(window.rationale_codes),
                rationale_losers=_top_negative_entries(window.rationale_codes),
                top_regimes=_top_positive_entries(window.regimes, limit=4),
                top_symbols=_top_positive_entries(window.symbols, limit=4),
                top_timeframes=_top_positive_entries(window.timeframes, limit=4),
                top_hold_conditions=sorted(
                    window.hold_conditions,
                    key=lambda item: (item.holds, item.decisions, item.key),
                    reverse=True,
                )[:4],
            )
        )

    execution_windows: list[DashboardExecutionWindowSummary] = []
    raw_execution_windows = execution_report.get("windows")
    if isinstance(raw_execution_windows, list):
        for item in raw_execution_windows:
            if not isinstance(item, dict):
                continue
            decision_quality = item.get("decision_quality_summary")
            execution_quality = item.get("execution_quality_summary")
            execution_windows.append(
                DashboardExecutionWindowSummary(
                    window=str(item.get("window") or "unknown"),
                    decision_quality_summary={
                        str(key): _as_int(value, default=0)
                        for key, value in (decision_quality.items() if isinstance(decision_quality, dict) else [])
                    },
                    execution_quality_summary={
                        str(key): _as_float(value, default=0.0) if isinstance(value, float) else _as_int(value, default=0)
                        for key, value in (execution_quality.items() if isinstance(execution_quality, dict) else [])
                    },
                    worst_profiles=_top_execution_profiles(item),
                )
            )

    primary_window = performance_report.windows[0] if performance_report.windows else None
    limited_live_readiness = (
        primary_window.limited_live_readiness
        if primary_window is not None
        else LimitedLiveReadinessReport()
    )
    hold_blocked_summary = DashboardHoldBlockedSummary(
        hold_top_conditions=(
            sorted(
                primary_window.hold_conditions,
                key=lambda entry: (entry.holds, entry.decisions, entry.key),
                reverse=True,
            )[:5]
            if primary_window is not None
            else []
        ),
        latest_blocked_reasons=overview.latest_blocked_reasons,
        auto_resume_blockers=overview.auto_resume_last_blockers,
        guard_mode_reason_code=overview.guard_mode_reason_code,
        guard_mode_reason_message=overview.guard_mode_reason_message,
    )

    return DashboardProfitabilityResponse(
        generated_at=utcnow_naive(),
        operating_state=overview.operating_state,
        guard_mode_reason_code=overview.guard_mode_reason_code,
        guard_mode_reason_message=overview.guard_mode_reason_message,
        adaptive_signal_summary=overview.adaptive_signal_summary,
        latest_decision=overview.latest_decision,
        latest_risk=overview.latest_risk,
        windows=windows,
        entry_quality=primary_window.entry_quality if primary_window is not None else {},
        cost_breakdowns=cost_breakdowns,
        execution_windows=execution_windows,
        hold_blocked_summary=hold_blocked_summary,
        limited_live_readiness=limited_live_readiness,
    )


def _build_decision_snapshot(row: AgentRun | None) -> OperatorDecisionSnapshot:
    if row is None:
        return OperatorDecisionSnapshot()
    payload = row.output_payload if isinstance(row.output_payload, dict) else {}
    input_payload = row.input_payload if isinstance(row.input_payload, dict) else {}
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    ai_trigger = _as_dict(metadata.get("ai_trigger"))
    if not ai_trigger:
        ai_trigger = _as_dict(input_payload.get("ai_trigger"))
    selection_context = _as_dict(metadata.get("selection_context"))
    slot_allocation = _as_dict(metadata.get("slot_allocation"))
    if not slot_allocation:
        slot_allocation = _as_dict(selection_context.get("slot_allocation"))
    holding_profile_context = _as_dict(metadata.get("holding_profile_context"))
    if not holding_profile_context:
        holding_profile_context = _as_dict(selection_context.get("holding_profile_context"))
    decision_reference = _compact_decision_reference(_build_decision_reference(row))
    intent_semantics = infer_intent_semantics(payload, metadata)
    suppression_projection = _active_position_suppression_projection(
        metadata.get("active_position_prompt_route_context")
    )
    holding_profile = (
        _as_holding_profile(selection_context.get("holding_profile"))
        or _as_holding_profile(metadata.get("holding_profile"))
        or _as_holding_profile(holding_profile_context.get("holding_profile"))
        or _as_holding_profile(payload.get("holding_profile"))
    )
    holding_profile_reason = str(
        selection_context.get("holding_profile_reason")
        or metadata.get("holding_profile_reason")
        or holding_profile_context.get("holding_profile_reason")
        or payload.get("holding_profile_reason")
        or ""
    ) or None
    assigned_slot = str(
        selection_context.get("assigned_slot")
        or slot_allocation.get("assigned_slot")
        or ""
    ) or None
    candidate_weight_raw = (
        selection_context.get("candidate_weight")
        if selection_context.get("candidate_weight") not in {None, ""}
        else slot_allocation.get("candidate_weight")
    )
    capacity_reason = str(
        selection_context.get("capacity_reason")
        or slot_allocation.get("capacity_reason")
        or ""
    ) or None
    ai_trigger_reason = _ai_trigger_reason_from_decision_row(row)
    ai_skip_reason = _last_ai_skip_reason_from_decision_row(row)
    ai_review = _ai_review_snapshot_from_decision_row(row)
    market_signal_context = _market_signal_context_from_decision_row(row)
    macro_event_summary = _decision_macro_event_context_summary(row)
    return OperatorDecisionSnapshot(
        decision_run_id=row.id,
        created_at=row.created_at,
        provider_name=row.provider_name,
        trigger_event=row.trigger_event,
        status=row.status,
        summary=row.summary,
        symbol=str(payload.get("symbol") or "") or None,
        timeframe=str(payload.get("timeframe") or "") or None,
        decision=str(payload.get("decision") or "") or None,
        confidence=_as_float(payload.get("confidence"), default=0.0) if payload.get("confidence") is not None else None,
        rationale_codes=_as_string_list(payload.get("rationale_codes", [])),
        explanation_short=str(payload.get("explanation_short") or "") or None,
        holding_profile=holding_profile,  # type: ignore[arg-type]
        holding_profile_reason=holding_profile_reason,
        assigned_slot=assigned_slot,
        candidate_weight=_as_float(candidate_weight_raw, default=0.0)
        if candidate_weight_raw not in {None, ""}
        else None,
        capacity_reason=capacity_reason,
        portfolio_slot_soft_cap_applied=bool(
            slot_allocation.get("applies_soft_limit") or selection_context.get("slot_applies_soft_cap")
        ),
        intent_family=str(
            payload.get("intent_family")
            or metadata.get("intent_family")
            or intent_semantics.get("intent_family")
            or "unknown"
        ),
        management_action=str(
            payload.get("management_action")
            or metadata.get("management_action")
            or intent_semantics.get("management_action")
            or "none"
        ),
        legacy_semantics_preserved=bool(
            payload.get("legacy_semantics_preserved")
            if payload.get("legacy_semantics_preserved") is not None
            else metadata.get("legacy_semantics_preserved")
            if metadata.get("legacy_semantics_preserved") is not None
            else intent_semantics.get("legacy_semantics_preserved")
        ),
        analytics_excluded_from_entry_stats=bool(
            payload.get("analytics_excluded_from_entry_stats")
            if payload.get("analytics_excluded_from_entry_stats") is not None
            else metadata.get("analytics_excluded_from_entry_stats")
            if metadata.get("analytics_excluded_from_entry_stats") is not None
            else intent_semantics.get("analytics_excluded_from_entry_stats")
        ),
        last_ai_trigger_reason=ai_trigger_reason,
        ai_review_type=_ai_review_type_from_trigger_reason(ai_trigger_reason),
        ai_trigger_reason_codes=_ai_trigger_reason_codes_from_decision_row(row),
        last_ai_invoked_at=(
            _as_datetime(metadata.get("last_ai_invoked_at"))
            or (row.created_at if str(metadata.get("source") or "") == "llm" else None)
        ),
        next_ai_review_due_at=None,
        trigger_deduped=bool(metadata.get("trigger_deduped", False)),
        trigger_fingerprint=str(
            metadata.get("trigger_fingerprint")
            or ai_trigger.get("trigger_fingerprint")
            or ""
        )
        or None,
        last_ai_skip_reason=ai_skip_reason,
        ai_skip_reason=ai_skip_reason,
        ai_trigger_summary=_ai_trigger_summary_from_decision_row(row),
        market_signal_summary=_market_signal_summary_from_decision_row(row),
        ai_review=ai_review,
        market_signal_context=market_signal_context,
        macro_event_context_summary=macro_event_summary,
        macro_event_risk_summary=macro_event_summary,
        suppression_active=bool(suppression_projection.get("suppression_active")),
        suppression_reason_code=str(suppression_projection.get("suppression_reason_code") or "") or None,
        allow_same_side_add_on=bool(suppression_projection.get("allow_same_side_add_on")),
        allowed_add_on_side=str(suppression_projection.get("allowed_add_on_side") or "") or None,
        event_risk_acknowledgement=str(
            payload.get("event_risk_acknowledgement")
            or metadata.get("event_risk_acknowledgement")
            or ""
        )
        or None,
        confidence_penalty_reason=str(
            payload.get("confidence_penalty_reason")
            or metadata.get("confidence_penalty_reason")
            or ""
        )
        or None,
        scenario_note=str(payload.get("scenario_note") or metadata.get("scenario_note") or "") or None,
        decision_reference=decision_reference,
        raw_output={},
    )


def _interval_review_state_from_scheduler(row: SchedulerRun | None) -> dict[str, Any]:
    if row is None or not isinstance(row.outcome, dict):
        return {}
    outcome = row.outcome
    trigger = _as_dict(outcome.get("trigger"))
    trigger_reason = str(
        outcome.get("last_ai_trigger_reason")
        or trigger.get("trigger_reason")
        or ""
    ) or None
    last_ai_skip_reason = str(outcome.get("last_ai_skip_reason") or "") or None
    trigger_deduped = bool(outcome.get("trigger_deduped", False))
    dedupe_reason = str(
        outcome.get("dedupe_reason")
        or trigger.get("dedupe_reason")
        or (last_ai_skip_reason if trigger_deduped else "")
        or ""
    ) or None
    return {
        "last_ai_trigger_reason": trigger_reason,
        "ai_review_type": _ai_review_type_from_trigger_reason(trigger_reason),
        "ai_trigger_reason_codes": _as_string_list(trigger.get("reason_codes")),
        "last_ai_invoked_at": _as_datetime(outcome.get("last_ai_invoked_at")),
        "next_ai_review_due_at": None,
        "trigger_deduped": trigger_deduped,
        "trigger_fingerprint": str(
            outcome.get("trigger_fingerprint")
            or trigger.get("trigger_fingerprint")
            or ""
        )
        or None,
        "last_ai_skip_reason": last_ai_skip_reason,
        "ai_skip_reason": last_ai_skip_reason,
        "dedupe_reason": dedupe_reason,
        "provider_skipped": bool(last_ai_skip_reason or trigger_deduped),
    }


def _overlay_interval_review_state(
    snapshot: OperatorDecisionSnapshot,
    *,
    decision_row: AgentRun | None,
    interval_row: SchedulerRun | None,
) -> OperatorDecisionSnapshot:
    if interval_row is None:
        return snapshot
    interval_state = _interval_review_state_from_scheduler(interval_row)
    if not interval_state:
        return snapshot
    decision_created_at = decision_row.created_at if decision_row is not None else None
    update: dict[str, Any] = {}
    ai_review_update: dict[str, Any] = {}
    if interval_state.get("last_ai_skip_reason") is not None:
        update["last_ai_skip_reason"] = interval_state["last_ai_skip_reason"]
        update["ai_skip_reason"] = interval_state["ai_skip_reason"]
        ai_review_update["skip_reason"] = interval_state["ai_skip_reason"]
    if bool(interval_state.get("trigger_deduped")):
        update["trigger_deduped"] = True
        ai_review_update["trigger_deduped"] = True
        ai_review_update["dedupe_reason"] = interval_state.get("dedupe_reason") or "TRIGGER_DEDUPED"
    if interval_state.get("last_ai_trigger_reason") is not None and (
        decision_created_at is None or interval_row.created_at >= decision_created_at
    ):
        update["last_ai_trigger_reason"] = interval_state["last_ai_trigger_reason"]
        update["ai_review_type"] = interval_state["ai_review_type"]
        ai_review_update["trigger_reason"] = interval_state["last_ai_trigger_reason"]
        ai_review_update["review_type"] = interval_state["ai_review_type"]
    if interval_state.get("ai_trigger_reason_codes"):
        update["ai_trigger_reason_codes"] = interval_state["ai_trigger_reason_codes"]
        ai_review_update["trigger_reason_codes"] = interval_state["ai_trigger_reason_codes"]
    if interval_state.get("trigger_fingerprint") is not None and (
        decision_created_at is None or interval_row.created_at >= decision_created_at
    ):
        update["trigger_fingerprint"] = interval_state["trigger_fingerprint"]
        ai_review_update["trigger_fingerprint"] = interval_state["trigger_fingerprint"]
    if interval_state.get("last_ai_invoked_at") is not None:
        update["last_ai_invoked_at"] = interval_state["last_ai_invoked_at"]
        ai_review_update["invoked_at"] = interval_state["last_ai_invoked_at"]
    if interval_state.get("provider_skipped") is not None:
        ai_review_update["provider_skipped"] = bool(interval_state["provider_skipped"])
        ai_review_update["provider_status"] = _provider_status_from_review(
            provider_invoked=snapshot.ai_review.provider_invoked,
            provider_skipped=bool(interval_state["provider_skipped"]),
            deduped=bool(ai_review_update.get("trigger_deduped", snapshot.ai_review.trigger_deduped)),
        )
    if ai_review_update:
        update["ai_review"] = snapshot.ai_review.model_copy(update=ai_review_update)
    return snapshot.model_copy(update=update) if update else snapshot


def _risk_reason_codes_from_row(row: RiskCheck | None) -> list[str]:
    if row is None:
        return []
    payload = row.payload if isinstance(row.payload, dict) else {}
    if "blocked_reason_codes" in payload and isinstance(payload.get("blocked_reason_codes"), list):
        return _filter_requested_only_exposure_reason_codes(
            payload,
            _as_string_list(payload.get("blocked_reason_codes")),
        )
    if "reason_codes" in payload and isinstance(payload.get("reason_codes"), list):
        return _filter_requested_only_exposure_reason_codes(payload, _as_string_list(payload.get("reason_codes")))
    return _filter_requested_only_exposure_reason_codes(payload, _as_string_list(row.reason_codes))


def _filter_requested_only_exposure_reason_codes(payload: dict[str, Any], reason_codes: list[str]) -> list[str]:
    debug_payload = _as_dict(payload.get("debug_payload"))
    if "requested_exposure_limit_codes" not in debug_payload or "final_exposure_limit_codes" not in debug_payload:
        return reason_codes
    headroom = _as_dict(debug_payload.get("headroom"))
    minimum_actionable_notional = _as_float(headroom.get("minimum_actionable_notional"), default=0.0)
    limiting_headroom_notional = _as_float(headroom.get("limiting_headroom_notional"), default=-1.0)
    if minimum_actionable_notional <= 0.0 or limiting_headroom_notional < minimum_actionable_notional:
        return reason_codes
    requested_codes = set(_as_string_list(debug_payload.get("requested_exposure_limit_codes")))
    final_codes = set(_as_string_list(debug_payload.get("final_exposure_limit_codes")))
    requested_only_codes = (requested_codes - final_codes) & AUTO_RESIZABLE_EXPOSURE_LIMIT_REASON_CODES
    if not requested_only_codes:
        return reason_codes
    return [code for code in reason_codes if code not in requested_only_codes]


def _risk_adjustment_reason_codes_from_row(row: RiskCheck | None) -> list[str]:
    if row is None:
        return []
    payload = row.payload if isinstance(row.payload, dict) else {}
    if "adjustment_reason_codes" in payload and isinstance(payload.get("adjustment_reason_codes"), list):
        return _as_string_list(payload.get("adjustment_reason_codes"))
    return []


def _dashboard_risk_payload_from_row(row: RiskCheck | None) -> dict[str, Any]:
    if row is None:
        return {}
    payload = dict(row.payload) if isinstance(row.payload, dict) else {}
    blocked_reason_codes = _risk_reason_codes_from_row(row)
    adjustment_reason_codes = _risk_adjustment_reason_codes_from_row(row)
    operating_state = str(payload.get("operating_state") or "") or None
    degraded_reason = str(payload.get("degraded_reason") or "") or None
    degraded_reason_codes = (
        _as_string_list(payload.get("degraded_reason_codes"))
        if isinstance(payload.get("degraded_reason_codes"), list)
        else derive_degraded_reason_codes(
            blocked_reason_codes,
            operating_state=operating_state,
            degraded_reason=degraded_reason,
        )
    )
    protection_reason_codes = (
        _as_string_list(payload.get("protection_reason_codes"))
        if isinstance(payload.get("protection_reason_codes"), list)
        else derive_protection_reason_codes(
            blocked_reason_codes,
            operating_state=operating_state,
        )
    )
    approved_quantity_source = (
        payload.get("approved_quantity")
        if payload.get("approved_quantity") is not None
        else payload.get("approved_qty")
    )
    exposure_headroom_snapshot = {
        str(key): _as_float(value, default=0.0)
        for key, value in _as_dict(payload.get("exposure_headroom_snapshot")).items()
        if value is not None
    }
    normalized_payload: dict[str, Any] = {
        "allowed": payload.get("allowed", row.allowed),
        "decision": payload.get("decision", row.decision),
        "reason_codes": blocked_reason_codes,
        "blocked_reason_codes": blocked_reason_codes,
        "adjustment_reason_codes": adjustment_reason_codes,
        "blocked_reason": str(payload.get("blocked_reason") or "") or None,
        "degraded_reason": degraded_reason,
        "degraded_reason_codes": degraded_reason_codes,
        "protection_reason_codes": protection_reason_codes,
        "approval_required_reason": str(payload.get("approval_required_reason") or "") or None,
        "survival_path": str(payload.get("survival_path") or "") or None,
        "policy_source": str(payload.get("policy_source") or "none") or "none",
        "exchange_connectivity_state": str(payload.get("exchange_connectivity_state") or "") or None,
        "evaluated_operator_policy": (
            _as_dict(payload.get("evaluated_operator_policy"))
            if isinstance(payload.get("evaluated_operator_policy"), dict)
            else None
        ),
        "approved_risk_pct": payload.get("approved_risk_pct", row.approved_risk_pct),
        "approved_leverage": payload.get("approved_leverage", row.approved_leverage),
        "raw_projected_notional": payload.get("raw_projected_notional"),
        "approved_projected_notional": payload.get("approved_projected_notional"),
        "approved_quantity": approved_quantity_source,
        "auto_resized_entry": bool(payload.get("auto_resized_entry")),
        "size_adjustment_ratio": payload.get("size_adjustment_ratio"),
        "auto_resize_reason": payload.get("auto_resize_reason"),
        "snapshot_id": payload.get("snapshot_id", row.market_snapshot_id),
        "operating_state": operating_state,
        "exposure_headroom_snapshot": exposure_headroom_snapshot,
        "debug_payload": _compact_risk_debug_payload(payload.get("debug_payload", {})),
        "reason_evidence": _compact_risk_reason_evidence(payload.get("debug_payload", {})),
    }
    normalized_payload["cycle_id"] = (
        str(payload.get("cycle_id"))
        if payload.get("cycle_id") not in {None, ""}
        else (str(row.decision_run_id) if row.decision_run_id is not None else None)
    )
    normalized_payload["as_of"] = _as_datetime(payload.get("as_of")) or row.created_at
    return normalized_payload


def _build_risk_snapshot(row: RiskCheck | None) -> OperatorRiskSnapshot:
    if row is None:
        return OperatorRiskSnapshot()
    payload = _dashboard_risk_payload_from_row(row)
    reason_codes = _as_string_list(payload.get("reason_codes", []))
    blocked_reason_codes = _as_string_list(payload.get("blocked_reason_codes", []))
    adjustment_reason_codes = _as_string_list(payload.get("adjustment_reason_codes", []))
    degraded_reason_codes = _as_string_list(payload.get("degraded_reason_codes", []))
    protection_reason_codes = _as_string_list(payload.get("protection_reason_codes", []))
    debug_payload = _as_dict(payload.get("debug_payload", {}))
    slot_allocation = _as_dict(debug_payload.get("slot_allocation"))
    holding_profile = _as_dict(debug_payload.get("holding_profile"))
    candidate_weight_raw = slot_allocation.get("candidate_weight")
    return OperatorRiskSnapshot(
        risk_check_id=row.id,
        decision_run_id=row.decision_run_id,
        created_at=row.created_at,
        snapshot_id=_as_int(payload.get("snapshot_id")) if payload.get("snapshot_id") is not None else None,
        cycle_id=str(payload.get("cycle_id") or "") or None,
        as_of=_as_datetime(payload.get("as_of")),
        allowed=bool(payload.get("allowed")) if payload.get("allowed") is not None else row.allowed,
        decision=str(payload.get("decision") or row.decision or "") or None,
        operating_state=str(payload.get("operating_state") or "") or None,
        reason_codes=reason_codes,
        blocked_reason_codes=blocked_reason_codes,
        adjustment_reason_codes=adjustment_reason_codes,
        degraded_reason_codes=degraded_reason_codes,
        protection_reason_codes=protection_reason_codes,
        blocked_reason=str(payload.get("blocked_reason") or "") or None,
        degraded_reason=str(payload.get("degraded_reason") or "") or None,
        approval_required_reason=str(payload.get("approval_required_reason") or "") or None,
        survival_path=str(payload.get("survival_path") or "") or None,
        exchange_connectivity_state=str(payload.get("exchange_connectivity_state") or "") or None,
        policy_source=str(payload.get("policy_source") or "none") or "none",
        evaluated_operator_policy=(
            _as_dict(payload.get("evaluated_operator_policy"))
            if isinstance(payload.get("evaluated_operator_policy"), dict)
            else None
        ),
        approved_risk_pct=_as_float(payload.get("approved_risk_pct"), default=0.0)
        if payload.get("approved_risk_pct") is not None
        else None,
        approved_leverage=_as_float(payload.get("approved_leverage"), default=0.0)
        if payload.get("approved_leverage") is not None
        else None,
        raw_projected_notional=_as_float(payload.get("raw_projected_notional"), default=0.0)
        if payload.get("raw_projected_notional") is not None
        else None,
        approved_projected_notional=_as_float(payload.get("approved_projected_notional"), default=0.0)
        if payload.get("approved_projected_notional") is not None
        else None,
        approved_quantity=_as_float(payload.get("approved_quantity"), default=0.0)
        if payload.get("approved_quantity") is not None
        else None,
        auto_resized_entry=bool(payload.get("auto_resized_entry")),
        size_adjustment_ratio=_as_float(payload.get("size_adjustment_ratio"), default=0.0)
        if payload.get("size_adjustment_ratio") is not None
        else None,
        auto_resize_reason=str(payload.get("auto_resize_reason") or "") or None,
        holding_profile=_as_holding_profile(holding_profile.get("holding_profile")),  # type: ignore[arg-type]
        holding_profile_reason=str(holding_profile.get("holding_profile_reason") or "") or None,
        assigned_slot=str(slot_allocation.get("assigned_slot") or "") or None,
        candidate_weight=_as_float(candidate_weight_raw, default=0.0)
        if candidate_weight_raw not in {None, ""}
        else None,
        capacity_reason=str(slot_allocation.get("capacity_reason") or "") or None,
        portfolio_slot_soft_cap_applied=bool(
            slot_allocation.get("applies_soft_limit") or "PORTFOLIO_SLOT_SOFT_CAP" in adjustment_reason_codes
        ),
        exposure_headroom_snapshot={
            str(key): _as_float(value, default=0.0)
            for key, value in _as_dict(payload.get("exposure_headroom_snapshot")).items()
        },
        debug_payload=debug_payload,
        current_cycle_result=dict(payload),
        raw_payload={},
    )


def _risk_guard_result_from_snapshot(snapshot: OperatorRiskSnapshot) -> OperatorRiskGuardResultSnapshot:
    reason_codes = list(dict.fromkeys(snapshot.reason_codes + snapshot.blocked_reason_codes))
    return OperatorRiskGuardResultSnapshot(
        risk_check_id=snapshot.risk_check_id,
        decision_run_id=snapshot.decision_run_id,
        allowed=snapshot.allowed,
        decision=snapshot.decision,
        reason_codes=reason_codes,
        blocked_reason_codes=snapshot.blocked_reason_codes,
        approved_risk_pct=snapshot.approved_risk_pct,
        approved_leverage=snapshot.approved_leverage,
        hold_decision=(
            snapshot.decision == "hold"
            or "HOLD_DECISION" in reason_codes
            or "HOLD_DECISION" in snapshot.blocked_reason_codes
        ),
    )


def _decision_symbol(row: AgentRun | None) -> str | None:
    if row is None or not isinstance(row.output_payload, dict):
        return None
    symbol = str(row.output_payload.get("symbol") or "").upper()
    return symbol or None


def _decision_timeframe(row: AgentRun | None) -> str | None:
    if row is None or not isinstance(row.output_payload, dict):
        return None
    timeframe = str(row.output_payload.get("timeframe") or "")
    return timeframe or None


def _extract_symbol_market_context(
    row: AgentRun | None,
    market_row: MarketSnapshot | None,
    feature_row: FeatureSnapshot | None,
) -> dict[str, Any]:
    if row is not None and isinstance(row.input_payload, dict):
        features = _as_dict(row.input_payload.get("features", {}))
        regime = _as_dict(features.get("regime", {}))
        if regime:
            return _compact_market_context_summary(regime)
    if feature_row is not None and isinstance(feature_row.payload, dict):
        regime = _as_dict(feature_row.payload.get("regime", {}))
        if regime:
            return _compact_market_context_summary(regime)
    if market_row is not None and isinstance(market_row.payload, dict):
        return _compact_market_context_summary(market_row.payload.get("regime_summary", {}))
    return {}


def _extract_symbol_derivatives_summary(
    row: AgentRun | None,
    market_row: MarketSnapshot | None,
    feature_row: FeatureSnapshot | None,
) -> dict[str, Any]:
    if row is not None and isinstance(row.input_payload, dict):
        ai_context = _as_dict(row.input_payload.get("ai_context", {}))
        if ai_context:
            summary = _as_dict(ai_context.get("derivatives_summary", {}))
            if summary:
                return _compact_derivatives_summary(summary)
        feature_layers = _as_dict(row.input_payload.get("feature_layers", {}))
        if feature_layers:
            summary = _as_dict(feature_layers.get("derivatives_summary", {}))
            if summary:
                return _compact_derivatives_summary(summary)
        features = _as_dict(row.input_payload.get("features", {}))
        if features:
            derivatives = _as_dict(features.get("derivatives", {}))
            if derivatives:
                return _compact_derivatives_summary(derivatives)
    if feature_row is not None and isinstance(feature_row.payload, dict):
        derivatives = _as_dict(feature_row.payload.get("derivatives", {}))
        if derivatives:
            return _compact_derivatives_summary(derivatives)
    if market_row is not None and isinstance(market_row.payload, dict):
        return _compact_derivatives_summary(market_row.payload.get("derivatives_context", {}))
    return _compact_derivatives_summary({})


def _extract_symbol_event_context_summary(
    row: AgentRun | None,
    market_row: MarketSnapshot | None,
    feature_row: FeatureSnapshot | None,
) -> dict[str, Any]:
    event_context, _source = extract_freshest_raw_event_context(
        decision_row=row,
        feature_row=feature_row,
        market_row=market_row,
    )
    if event_context:
        return _compact_event_context_summary(event_context)
    return _compact_event_context_summary({})


def _extract_latest_candle_time(market_row: MarketSnapshot | None) -> datetime | None:
    if market_row is None or not isinstance(market_row.payload, dict):
        return None
    candles = market_row.payload.get("candles")
    if not isinstance(candles, list) or not candles:
        return None
    latest = candles[-1]
    if not isinstance(latest, dict):
        return None
    return _as_datetime(latest.get("timestamp"))


def _build_execution_snapshot_from_rows(
    order_row: Order | None,
    execution_row: Execution | None,
    decision_row: AgentRun | None,
    *,
    recent_fills: Sequence[Execution] = (),
) -> OperatorExecutionSnapshot:
    if order_row is None:
        return OperatorExecutionSnapshot(
            decision_run_id=decision_row.id if decision_row is not None else None,
            symbol=_decision_symbol(decision_row),
            recent_fills=[_build_execution_fill_summary(row) for row in recent_fills[:RECENT_FILL_LIMIT]],
        )
    decision_payload = decision_row.output_payload if decision_row is not None and isinstance(decision_row.output_payload, dict) else {}
    order_metadata = order_row.metadata_json if isinstance(order_row.metadata_json, dict) else {}
    return OperatorExecutionSnapshot(
        order_id=order_row.id,
        execution_id=execution_row.id if execution_row is not None else None,
        decision_run_id=order_row.decision_run_id,
        created_at=order_row.created_at,
        execution_created_at=execution_row.created_at if execution_row is not None else None,
        symbol=order_row.symbol,
        side=order_row.side,
        order_type=order_row.order_type,
        order_status=order_row.status,
        execution_status=execution_row.status if execution_row is not None else None,
        requested_quantity=order_row.requested_quantity,
        filled_quantity=order_row.filled_quantity,
        average_fill_price=order_row.average_fill_price,
        fill_price=execution_row.fill_price if execution_row is not None else None,
        reason_codes=_as_string_list(order_row.reason_codes),
        execution_policy=_compact_execution_policy(order_metadata.get("execution_policy", {})),
        execution_quality=_compact_execution_quality(order_metadata.get("execution_quality", {})),
        decision_summary={
            "decision": decision_payload.get("decision"),
            "timeframe": decision_payload.get("timeframe"),
            "confidence": decision_payload.get("confidence"),
            "rationale_codes": decision_payload.get("rationale_codes", []),
        },
        recent_fills=[_build_execution_fill_summary(row) for row in recent_fills[:RECENT_FILL_LIMIT]],
    )


def _build_execution_fill_summary(row: Execution) -> OperatorExecutionFillSummary:
    return OperatorExecutionFillSummary(
        execution_id=row.id,
        order_id=row.order_id,
        external_trade_id=row.external_trade_id,
        created_at=row.created_at,
        status=row.status,
        fill_price=row.fill_price,
        fill_quantity=row.fill_quantity,
        fee_paid=row.fee_paid,
        commission_asset=row.commission_asset,
        realized_pnl=row.realized_pnl,
    )


def _build_position_snapshot(position: Position | None) -> OperatorPositionSummary:
    if position is None:
        return OperatorPositionSummary()
    metadata = dict(position.metadata_json) if isinstance(position.metadata_json, dict) else {}
    management = dict(metadata.get("position_management") or {}) if isinstance(metadata.get("position_management"), dict) else {}
    return OperatorPositionSummary(
        is_open=position.status == "open" and position.quantity > 0,
        position_id=position.id,
        side=position.side,
        status=position.status,
        quantity=position.quantity,
        entry_price=position.entry_price,
        mark_price=position.mark_price,
        unrealized_pnl=position.unrealized_pnl,
        realized_pnl=position.realized_pnl,
        leverage=position.leverage,
        opened_at=position.opened_at,
        holding_profile=str(management.get("holding_profile") or "scalp"),
        holding_profile_reason=str(management.get("holding_profile_reason") or "") or None,
        initial_stop_type=str(management.get("initial_stop_type") or "") or None,
        ai_stop_management_allowed=(
            bool(management.get("ai_stop_management_allowed"))
            if "ai_stop_management_allowed" in management
            else None
        ),
        hard_stop_active=bool(management.get("hard_stop_active")) if "hard_stop_active" in management else None,
        stop_widening_allowed=(
            bool(management.get("stop_widening_allowed"))
            if "stop_widening_allowed" in management
            else None
        ),
    )


def _build_protection_snapshot(
    protection_state: dict[str, object],
    *,
    recovery_state: dict[str, object] | None = None,
    verification_block: dict[str, object] | None = None,
    latest_event: AuditTimelineEntry | None = None,
) -> OperatorProtectionSummary:
    recovery_payload = recovery_state if isinstance(recovery_state, dict) else {}
    verification_payload = verification_block if isinstance(verification_block, dict) else {}
    latest_event_payload = latest_event.payload if latest_event is not None else {}
    lifecycle_payload = _as_dict(verification_payload.get("protection_lifecycle"))
    lifecycle_state = str(
        lifecycle_payload.get("state")
        or latest_event_payload.get("to_state")
        or latest_event_payload.get("state")
        or latest_event_payload.get("status")
        or ""
    ) or None
    blocked_reason_code = str(
        verification_payload.get("blocked_reason_code")
        or verification_payload.get("reason_code")
        or protection_state.get("blocked_reason_code")
        or ""
    ) or None
    blocked_reason = str(
        verification_payload.get("blocked_reason")
        or verification_payload.get("last_error")
        or protection_state.get("blocked_reason")
        or ""
    ) or None
    verification_status = None
    if verification_payload:
        verification_status = str(
            verification_payload.get("verification_status")
            or verification_payload.get("status")
            or "verify_failed"
        )
    elif str(latest_event.event_type if latest_event is not None else "").lower() == "protection_verification_failed":
        verification_status = "verify_failed"
    elif lifecycle_state in {"verified", "placed", "requested", "verify_failed"}:
        verification_status = lifecycle_state
    elif bool(protection_state.get("protected", False)):
        verification_status = "verified"
    return OperatorProtectionSummary(
        status=str(protection_state.get("status") or "unknown"),
        protected=bool(protection_state.get("protected", False)),
        protective_order_count=_as_int(protection_state.get("protective_order_count"), default=0),
        has_stop_loss=bool(protection_state.get("has_stop_loss", False)),
        has_take_profit=bool(protection_state.get("has_take_profit", False)),
        missing_components=_as_string_list(protection_state.get("missing_components", [])),
        order_ids=[
            int(item)
            for item in protection_state.get("order_ids", [])
            if isinstance(item, int)
        ]
        if isinstance(protection_state.get("order_ids"), list)
        else [],
        recovery_status=str(recovery_payload.get("recovery_status") or "") or None,
        auto_recovery_active=bool(recovery_payload.get("auto_recovery_active", False)),
        failure_count=_as_int(recovery_payload.get("failure_count"), default=0),
        last_error=str(recovery_payload.get("last_error") or "") or None,
        last_transition_at=_as_datetime(recovery_payload.get("last_transition_at")),
        trigger_source=str(recovery_payload.get("trigger_source") or "") or None,
        lifecycle_state=lifecycle_state,
        verification_status=verification_status,
        blocked_reason_code=blocked_reason_code,
        blocked_reason=blocked_reason,
        verification_deadline_at=_as_datetime(
            verification_payload.get("verification_deadline_at")
            or protection_state.get("verification_deadline_at")
        ),
        last_event_type=latest_event.event_type if latest_event is not None else None,
        last_event_message=latest_event.message if latest_event is not None else None,
        last_event_at=latest_event.created_at if latest_event is not None else None,
    )


def _build_candidate_selection_snapshot(
    payload: dict[str, object] | None,
    *,
    blocked_reason_codes: list[str] | None = None,
) -> OperatorCandidateSelectionSnapshot:
    source = dict(payload) if isinstance(payload, dict) else {}
    candidate_weight_raw = source.get("candidate_weight")
    return OperatorCandidateSelectionSnapshot(
        symbol=str(source.get("symbol") or "") or None,
        selected=bool(source.get("selected")) if "selected" in source else None,
        selection_reason=str(source.get("selection_reason") or "") or None,
        selected_reason=str(source.get("selected_reason") or "") or None,
        rejected_reason=str(source.get("rejected_reason") or "") or None,
        strategy_engine=str(source.get("strategy_engine") or "") or None,
        holding_profile=_as_holding_profile(source.get("holding_profile")),  # type: ignore[arg-type]
        holding_profile_reason=str(source.get("holding_profile_reason") or "") or None,
        assigned_slot=str(source.get("assigned_slot") or "") or None,
        candidate_weight=_as_float(candidate_weight_raw, default=0.0)
        if candidate_weight_raw not in {None, ""}
        else None,
        capacity_reason=str(source.get("capacity_reason") or "") or None,
        blocked_reason_codes=list(blocked_reason_codes or []),
        portfolio_slot_soft_cap_applied=bool(source.get("slot_applies_soft_cap", False)),
    )


def _build_symbol_stale_flags(
    sync_freshness_summary: dict[str, Any],
    market_row: MarketSnapshot | None,
    market_context_summary: dict[str, Any],
) -> list[str]:
    flags: list[str] = []
    for scope, payload in sync_freshness_summary.items():
        if not isinstance(payload, dict):
            continue
        if bool(payload.get("stale")):
            flags.append(str(scope))
        elif bool(payload.get("incomplete")):
            flags.append(f"{scope}_incomplete")
    if market_row is not None:
        if market_row.is_stale:
            flags.append("market_snapshot")
        if not market_row.is_complete:
            flags.append("market_snapshot_incomplete")
    if not market_context_summary:
        flags.append("feature_input_missing")
    return flags


def _latest_timestamp(*timestamps: datetime | None) -> datetime | None:
    values = [item for item in timestamps if item is not None]
    return max(values) if values else None


def _latest_rows_by_symbol(
    session: Session,
    model: type[Any],
    symbols: Sequence[str],
    timestamp_column: Any,
    *conditions: Any,
) -> dict[str, Any]:
    symbol_set = {str(symbol or "").strip().upper() for symbol in symbols if str(symbol or "").strip()}
    if not symbol_set:
        return {}
    rows: dict[str, Any] = {}

    def consume(result: Sequence[Any]) -> bool:
        for row in result:
            symbol = str(getattr(row, "symbol", "") or "").upper()
            if symbol in symbol_set and symbol not in rows:
                rows[symbol] = row
            if len(rows) == len(symbol_set):
                return True
        return False

    statement = select(model).where(*conditions).order_by(desc(timestamp_column))
    scan_limit = max(OPERATOR_RECENT_ROW_SCAN_LIMIT, len(symbol_set))
    limited_rows = list(session.scalars(statement.limit(scan_limit)))
    if consume(limited_rows) or len(limited_rows) < scan_limit:
        return rows
    consume(list(session.scalars(statement.offset(scan_limit).limit(scan_limit))))
    return rows


def _latest_decision_rows_by_symbol_from_facts(
    session: Session,
    symbols: Sequence[str],
) -> dict[str, AgentRun]:
    symbol_keys = list(dict.fromkeys(str(symbol or "").strip().upper() for symbol in symbols if str(symbol or "").strip()))
    if not symbol_keys:
        return {}

    ranked_facts = (
        select(
            DecisionPerformanceFact.symbol.label("symbol"),
            DecisionPerformanceFact.decision_run_id.label("decision_run_id"),
            func.row_number()
            .over(
                partition_by=DecisionPerformanceFact.symbol,
                order_by=(desc(DecisionPerformanceFact.created_at), desc(DecisionPerformanceFact.id)),
            )
            .label("row_rank"),
        )
        .where(DecisionPerformanceFact.symbol.in_(symbol_keys))
        .subquery()
    )
    fact_refs = list(
        session.execute(
            select(ranked_facts.c.symbol, ranked_facts.c.decision_run_id).where(ranked_facts.c.row_rank == 1)
        )
    )
    decision_ids = [int(decision_run_id) for _symbol, decision_run_id in fact_refs if decision_run_id is not None]
    if not decision_ids:
        return {}

    rows_by_id = {
        int(row.id): row
        for row in session.scalars(select(AgentRun).where(AgentRun.id.in_(decision_ids)))
        if row.id is not None
    }
    return {
        str(symbol or "").upper(): rows_by_id[int(decision_run_id)]
        for symbol, decision_run_id in fact_refs
        if decision_run_id is not None and int(decision_run_id) in rows_by_id
    }


def _latest_decision_rows_by_symbol(
    session: Session,
    symbols: Sequence[str],
    *,
    prefer_fact_lookup: bool,
    fallback_scan_limit: int | None,
) -> dict[str, AgentRun]:
    symbol_keys = list(dict.fromkeys(str(symbol or "").strip().upper() for symbol in symbols if str(symbol or "").strip()))
    if not symbol_keys:
        return {}

    rows: dict[str, AgentRun] = {}
    if prefer_fact_lookup:
        rows.update(_latest_decision_rows_by_symbol_from_facts(session, symbol_keys))

    missing_symbols = [symbol for symbol in symbol_keys if symbol not in rows]
    if missing_symbols:
        rows.update(
            _latest_rows_by_extracted_symbol(
                session,
                select(AgentRun)
                .where(AgentRun.role == "trading_decision")
                .order_by(desc(AgentRun.created_at)),
                missing_symbols,
                _decision_symbol,
                fallback_scan_limit=fallback_scan_limit,
            )
        )
    return rows


def _latest_rows_by_extracted_symbol(
    session: Session,
    statement: Any,
    symbols: Sequence[str],
    symbol_for_row: Callable[[Any], str | None],
    *,
    fallback_scan_limit: int | None = OPERATOR_EXTRACTED_SYMBOL_FALLBACK_SCAN_LIMIT,
) -> dict[str, Any]:
    symbol_set = {symbol.upper() for symbol in symbols}
    rows: dict[str, Any] = {}

    def consume(result: Sequence[Any]) -> bool:
        for row in result:
            symbol = symbol_for_row(row)
            if symbol in symbol_set and symbol not in rows:
                rows[symbol] = row
            if len(rows) == len(symbol_set):
                return True
        return False

    limited_rows = list(session.scalars(statement.limit(OPERATOR_RECENT_ROW_SCAN_LIMIT)))
    if consume(limited_rows) or len(limited_rows) < OPERATOR_RECENT_ROW_SCAN_LIMIT:
        return rows
    if fallback_scan_limit is None or fallback_scan_limit <= 0:
        return rows
    fallback_limit = max(fallback_scan_limit, len(symbol_set))
    consume(list(session.scalars(statement.offset(OPERATOR_RECENT_ROW_SCAN_LIMIT).limit(fallback_limit))))
    return rows


def _parse_timeframe_minutes(value: str | None) -> int | None:
    if not value:
        return None
    normalized = value.strip().lower()
    if len(normalized) < 2:
        return None
    unit = normalized[-1]
    amount_raw = normalized[:-1]
    try:
        amount = int(amount_raw)
    except ValueError:
        return None
    if amount <= 0:
        return None
    if unit == "m":
        return amount
    if unit == "h":
        return amount * 60
    if unit == "d":
        return amount * 60 * 24
    return None


def _build_feature_input_delay_summary(
    *,
    now: datetime,
    timeframe: str | None,
    market_snapshot_time: datetime | None,
    market_candle_time: datetime | None,
    stale_flags: list[str],
) -> tuple[int | None, int | None, bool]:
    if "feature_input_missing" not in stale_flags:
        return None, None, False
    timeframe_minutes = _parse_timeframe_minutes(timeframe)
    threshold_minutes = max((timeframe_minutes or 15) * 2, 20)
    reference_time = market_snapshot_time or market_candle_time
    if reference_time is None:
        return None, threshold_minutes, False
    age_minutes = max(0, int((now - reference_time).total_seconds() // 60))
    return age_minutes, threshold_minutes, age_minutes >= threshold_minutes


def _audit_event_matches_symbol(row: dict[str, object], symbol: str) -> bool:
    symbol_key = symbol.upper()
    entity_id = str(row.get("entity_id") or "").upper()
    if entity_id == symbol_key:
        return True
    payload = _as_dict(row.get("payload", {}))
    for key in ("symbol", "tracked_symbol"):
        if str(payload.get(key) or "").upper() == symbol_key:
            return True
    symbols = payload.get("symbols")
    return isinstance(symbols, list) and symbol_key in {str(item).upper() for item in symbols}


def _build_audit_entry(payload: dict[str, object]) -> AuditTimelineEntry:
    created_at = payload.get("created_at")
    if isinstance(created_at, str):
        created_at_value = datetime.fromisoformat(created_at)
    elif isinstance(created_at, datetime):
        created_at_value = created_at
    else:
        created_at_value = utcnow_naive()
    return AuditTimelineEntry(
        event_category=str(payload.get("event_category") or classify_audit_event(
            event_type=str(payload.get("event_type") or "unknown"),
            entity_type=str(payload.get("entity_type") or "unknown"),
            payload=_as_dict(payload.get("payload", {})),
        )),
        event_type=str(payload.get("event_type") or "unknown"),
        entity_type=str(payload.get("entity_type") or "unknown"),
        entity_id=str(payload.get("entity_id") or "unknown"),
        severity=str(payload.get("severity") or "info"),
        message=str(payload.get("message") or ""),
        payload=_as_dict(payload.get("payload", {})),
        created_at=created_at_value,
    )


def _build_operator_audit_entry(payload: dict[str, object]) -> AuditTimelineEntry:
    entry = _build_audit_entry(payload)
    compact_payload = compact_audit_payload(
        entry.payload,
        event_type=entry.event_type,
        event_category=entry.event_category,
    )
    return entry.model_copy(update={"payload": compact_payload})


def _compact_profitability_window(window: DashboardProfitabilityWindow) -> DashboardProfitabilityWindow:
    return DashboardProfitabilityWindow(
        window_label=window.window_label,
        window_hours=window.window_hours,
        summary=window.summary,
        cost_breakdown=window.cost_breakdown,
        entry_quality=window.entry_quality,
        ai_baseline_comparison=window.ai_baseline_comparison,
        limited_live_readiness=window.limited_live_readiness,
        rationale_winners=window.rationale_winners[:OPERATOR_PERFORMANCE_ENTRY_LIMIT],
        rationale_losers=window.rationale_losers[:OPERATOR_PERFORMANCE_ENTRY_LIMIT],
        top_regimes=window.top_regimes[:OPERATOR_PERFORMANCE_ENTRY_LIMIT],
        top_symbols=window.top_symbols[:OPERATOR_PERFORMANCE_ENTRY_LIMIT],
        top_timeframes=[],
        top_hold_conditions=window.top_hold_conditions[:OPERATOR_PERFORMANCE_ENTRY_LIMIT],
    )


def _compact_execution_window(window: DashboardExecutionWindowSummary) -> DashboardExecutionWindowSummary:
    return DashboardExecutionWindowSummary(
        window=window.window,
        decision_quality_summary=dict(window.decision_quality_summary),
        execution_quality_summary=dict(window.execution_quality_summary),
        worst_profiles=window.worst_profiles[:OPERATOR_EXECUTION_PROFILE_LIMIT],
    )


def _normalize_operator_dashboard_view(view: str | None) -> str | None:
    normalized = (view or "").strip().lower()
    return normalized if normalized in OPERATOR_COMPACT_VIEWS else None


def _build_operator_symbol_summaries(
    session: Session,
    *,
    tracked_symbols: list[str],
    overview: OverviewResponse,
    include_decision_state: bool = True,
    include_risk_state: bool = True,
    include_execution_state: bool = True,
    include_protection_state: bool = True,
    include_event_operator_control: bool = True,
    include_audit_events: bool = True,
    prefer_fact_decision_lookup: bool = False,
    extracted_symbol_fallback_limit: int | None = OPERATOR_EXTRACTED_SYMBOL_FALLBACK_SCAN_LIMIT,
) -> list[OperatorSymbolSummary]:
    now = utcnow_naive()
    symbol_keys = list(dict.fromkeys(item.upper() for item in tracked_symbols if item))
    symbol_key_set = set(symbol_keys)
    settings_row = (
        get_or_create_settings(session)
        if include_event_operator_control or include_protection_state
        else None
    )
    runtime_summary = (
        summarize_runtime_state(settings_row)
        if include_protection_state and settings_row is not None
        else {}
    )
    protection_recovery_symbols = {
        str(key).upper(): dict(value)
        for key, value in (runtime_summary.get("protection_recovery_symbols") or {}).items()
        if isinstance(value, dict)
    }
    protection_verification_blocks = {
        str(key).upper(): dict(value)
        for key, value in (runtime_summary.get("protection_verification_blocks") or {}).items()
        if isinstance(value, dict)
    }
    latest_markets: dict[str, MarketSnapshot] = _latest_rows_by_symbol(
        session,
        MarketSnapshot,
        symbol_keys,
        MarketSnapshot.snapshot_time,
    )

    latest_features: dict[str, FeatureSnapshot] = _latest_rows_by_symbol(
        session,
        FeatureSnapshot,
        symbol_keys,
        FeatureSnapshot.feature_time,
    )

    latest_decisions: dict[str, AgentRun] = {}
    if include_decision_state:
        latest_decisions = _latest_decision_rows_by_symbol(
            session,
            symbol_keys,
            prefer_fact_lookup=prefer_fact_decision_lookup,
            fallback_scan_limit=extracted_symbol_fallback_limit,
        )

    latest_risks: dict[str, RiskCheck] = {}
    if include_risk_state:
        latest_risks = _latest_rows_by_symbol(
            session,
            RiskCheck,
            symbol_keys,
            RiskCheck.created_at,
        )

    active_entry_plans: dict[str, PendingEntryPlan] = {}
    if include_execution_state:
        for row in session.scalars(
            active_pending_entry_plan_statement(symbols=symbol_keys)
            .order_by(desc(PendingEntryPlan.created_at))
        ):
            symbol = row.symbol.upper()
            active_entry_plans.setdefault(symbol, row)

    latest_orders: dict[str, Order] = {}
    if include_execution_state:
        latest_orders = _latest_rows_by_symbol(
            session,
            Order,
            symbol_keys,
            Order.created_at,
            Order.mode == "live",
        )

    latest_interval_reviews: dict[str, SchedulerRun] = {}
    if include_decision_state:
        latest_interval_reviews = _latest_rows_by_extracted_symbol(
            session,
            select(SchedulerRun)
            .where(SchedulerRun.workflow == "interval_decision_cycle")
            .order_by(desc(SchedulerRun.created_at)),
            symbol_keys,
            lambda row: str((row.outcome if isinstance(row.outcome, dict) else {}).get("symbol") or "").upper(),
            fallback_scan_limit=extracted_symbol_fallback_limit,
        )

    latest_executions_by_order_id: dict[int, Execution] = {}
    recent_executions_by_symbol: dict[str, list[Execution]] = defaultdict(list)
    order_ids = [row.id for row in latest_orders.values()]
    if include_execution_state and order_ids:
        for row in session.scalars(
            select(Execution)
            .where(Execution.order_id.in_(order_ids))
            .order_by(desc(Execution.created_at))
        ):
            if row.order_id is None:
                continue
            latest_executions_by_order_id.setdefault(row.order_id, row)
            symbol_key = str(row.symbol or "").upper()
            if symbol_key in symbol_key_set and len(recent_executions_by_symbol[symbol_key]) < RECENT_FILL_LIMIT:
                recent_executions_by_symbol[symbol_key].append(row)

    open_positions: dict[str, Position] = {}
    if include_protection_state:
        open_positions = {
            row.symbol.upper(): row
            for row in session.scalars(
                select(Position).where(
                    Position.mode == "live",
                    Position.status == "open",
                    Position.quantity > 0,
                    Position.symbol.in_(symbol_keys),
                )
            )
        }

    audit_rows = (
        get_audit_timeline(session, limit=max(12, len(symbol_keys) * SYMBOL_AUDIT_LIMIT))
        if include_audit_events
        else []
    )
    audit_entries_by_symbol: dict[str, list[AuditTimelineEntry]] = {symbol: [] for symbol in symbol_keys}
    latest_protection_event_by_symbol: dict[str, AuditTimelineEntry] = {}
    for row in audit_rows:
        if not isinstance(row, dict):
            continue
        for symbol in symbol_keys:
            if not _audit_event_matches_symbol(row, symbol):
                continue
            entry = _build_operator_audit_entry(row)
            if len(audit_entries_by_symbol[symbol]) < SYMBOL_AUDIT_LIMIT:
                audit_entries_by_symbol[symbol].append(entry)
            if entry.event_category == AUDIT_CATEGORY_PROTECTION and symbol not in latest_protection_event_by_symbol:
                latest_protection_event_by_symbol[symbol] = entry

    summaries: list[OperatorSymbolSummary] = []
    candidate_selection_map = {
        str(item.get("symbol") or "").upper(): dict(item)
        for item in overview.candidate_selection_summary.get("rankings", [])
        if isinstance(item, dict) and item.get("symbol")
    }
    for symbol in tracked_symbols:
        symbol_key = symbol.upper()
        decision_row = latest_decisions.get(symbol_key)
        interval_review_row = latest_interval_reviews.get(symbol_key)
        risk_row = latest_risks.get(symbol_key)
        pending_entry_plan_row = active_entry_plans.get(symbol_key)
        order_row = latest_orders.get(symbol_key)
        execution_row = latest_executions_by_order_id.get(order_row.id) if order_row is not None else None
        position_row = open_positions.get(symbol_key)
        market_row = latest_markets.get(symbol_key)
        feature_row = latest_features.get(symbol_key)
        market_candle_time = _extract_latest_candle_time(market_row)
        market_context_summary = _extract_symbol_market_context(decision_row, market_row, feature_row)
        derivatives_summary = _extract_symbol_derivatives_summary(decision_row, market_row, feature_row)
        event_context_summary = _extract_symbol_event_context_summary(decision_row, market_row, feature_row)
        event_operator_control = (
            build_event_operator_control_payload(
                session=session,
                settings_row=settings_row,
                symbol=symbol_key,
                timeframe=_decision_timeframe(decision_row) or (market_row.timeframe if market_row is not None else None),
                decision_row=decision_row,
                feature_row=feature_row,
                market_row=market_row,
            )
            if include_event_operator_control and settings_row is not None
            else None
        )
        protection_state = {
            "status": "unknown",
            "protected": False,
            "protective_order_count": 0,
            "has_stop_loss": False,
            "has_take_profit": False,
            "missing_components": [],
            "order_ids": [],
        }
        if include_protection_state:
            protection_state = (
                _build_position_protection_state(session, position_row)
                if position_row is not None
                else {
                    "status": "flat",
                    "protected": True,
                    "protective_order_count": 0,
                    "has_stop_loss": False,
                    "has_take_profit": False,
                    "missing_components": [],
                    "order_ids": [],
                }
            )
        stale_flags = _build_symbol_stale_flags(overview.sync_freshness_summary, market_row, market_context_summary)
        feature_input_delay_minutes, feature_input_delay_threshold_minutes, feature_input_delayed = (
            _build_feature_input_delay_summary(
                now=now,
                timeframe=_decision_timeframe(decision_row) or (market_row.timeframe if market_row is not None else None),
                market_snapshot_time=market_row.snapshot_time if market_row is not None else None,
                market_candle_time=market_candle_time,
                stale_flags=stale_flags,
            )
        )
        last_updated_at = _latest_timestamp(
            market_row.snapshot_time if market_row is not None else None,
            feature_row.feature_time if feature_row is not None else None,
            decision_row.created_at if decision_row is not None else None,
            risk_row.created_at if risk_row is not None else None,
            pending_entry_plan_row.created_at if pending_entry_plan_row is not None else None,
            order_row.created_at if order_row is not None else None,
            execution_row.created_at if execution_row is not None else None,
            position_row.created_at if position_row is not None else None,
        )
        decision_snapshot = _build_decision_snapshot(decision_row)
        decision_snapshot = _overlay_interval_review_state(
            decision_snapshot,
            decision_row=decision_row,
            interval_row=interval_review_row,
        )
        decision_snapshot = decision_snapshot.model_copy(
            update={
                "decision_reference": _annotate_decision_reference(
                    decision_snapshot.decision_reference,
                    current_market_refresh_at=market_row.snapshot_time if market_row is not None else None,
                    current_sync_freshness_summary=overview.sync_freshness_summary,
                )
            }
        )
        risk_snapshot = _build_risk_snapshot(risk_row)
        summaries.append(
            OperatorSymbolSummary(
                symbol=symbol_key,
                timeframe=_decision_timeframe(decision_row) or (market_row.timeframe if market_row is not None else None),
                latest_price=market_row.latest_price if market_row is not None else None,
                market_snapshot_time=market_row.snapshot_time if market_row is not None else None,
                market_candle_time=market_candle_time,
                feature_input_delay_minutes=feature_input_delay_minutes,
                feature_input_delay_threshold_minutes=feature_input_delay_threshold_minutes,
                feature_input_delayed=feature_input_delayed,
                market_context_summary=market_context_summary,
                derivatives_summary=derivatives_summary,
                event_context_summary=event_context_summary,
                event_operator_control=event_operator_control,
                ai_decision=decision_snapshot,
                pending_entry_plan=_build_pending_entry_plan_snapshot(pending_entry_plan_row),
                risk_guard=risk_snapshot,
                risk_guard_result=_risk_guard_result_from_snapshot(risk_snapshot),
                execution=_build_execution_snapshot_from_rows(
                    order_row,
                    execution_row,
                    decision_row,
                    recent_fills=recent_executions_by_symbol.get(symbol_key, []),
                ),
                open_position=_build_position_snapshot(position_row),
                protection_status=(
                    _build_protection_snapshot(
                        protection_state,
                        recovery_state=protection_recovery_symbols.get(symbol_key),
                        verification_block=protection_verification_blocks.get(symbol_key),
                        latest_event=latest_protection_event_by_symbol.get(symbol_key),
                    )
                    if include_protection_state
                    else OperatorProtectionSummary()
                ),
                blocked_reasons=_risk_reason_codes_from_row(risk_row),
                candidate_selection=_build_candidate_selection_snapshot(
                    candidate_selection_map.get(symbol_key),
                    blocked_reason_codes=_risk_reason_codes_from_row(risk_row),
                ),
                live_execution_ready=overview.live_execution_ready and len(stale_flags) == 0,
                stale_flags=stale_flags,
                last_updated_at=last_updated_at,
                audit_events=audit_entries_by_symbol.get(symbol_key, []),
            )
        )
    return summaries


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _ai_settings_next_review_at(
    *,
    advisor_state: Mapping[str, Any],
    latest_recommendation: Mapping[str, Any],
    policy: Mapping[str, object],
) -> datetime | None:
    basis = _naive_utc(_as_datetime(advisor_state.get("updated_at"))) or _naive_utc(
        _as_datetime(latest_recommendation.get("generated_at"))
    )
    if basis is None:
        return None
    observed_risk_flags = _as_string_list(latest_recommendation.get("observed_risk_flags"))
    normal_interval = max(int(policy.get("normal_interval_seconds") or 900), 1)
    elevated_interval = max(int(policy.get("elevated_interval_seconds") or 900), 1)
    min_recheck = max(int(policy.get("min_recheck_interval_seconds") or 900), 1)
    interval = elevated_interval if observed_risk_flags else normal_interval
    return basis + timedelta(seconds=max(interval, min_recheck))


def _operator_execution_profile_state(settings_row: Setting) -> dict[str, Any]:
    defaults = get_settings()
    policy = get_execution_risk_profile_policy(settings_row, defaults=defaults)
    auto_apply_mode = str(policy.get("auto_apply_mode") or "shadow")
    detail = _as_dict(settings_row.pause_reason_detail)
    selector_state = _as_dict(detail.get(SAFE_PROFILE_SELECTOR_DETAIL_KEY))
    selection = {
        **selector_state,
        **_as_dict(selector_state.get("last_selection")),
    }
    advisor_state = _as_dict(detail.get(AI_MARKET_SETTINGS_ADVISOR_DETAIL_KEY))
    latest_recommendation = _as_dict(advisor_state.get("latest")) or advisor_state

    selection_ignored_codes = _as_string_list(selection.get("ignored_reason_codes"))
    latest_ignored_codes = _as_string_list(latest_recommendation.get("ignored_reason_codes"))
    ignored_reason_codes = list(dict.fromkeys([*selection_ignored_codes, *latest_ignored_codes]))
    relaxation_block_reason_codes = _as_string_list(selection.get("relaxation_block_reason_codes"))
    ai_status = _string_or_none(advisor_state.get("status") or latest_recommendation.get("status"))
    if ignored_reason_codes:
        ai_status = "ignored"

    final_profile = _string_or_none(selection.get("final_active_profile") or selector_state.get("active_profile"))
    blocking_active = _bool_value(
        selection.get("blocking_active"),
        default=auto_apply_mode in {"conservative_only", "manual_approval"},
    )
    profile_new_entry_blocked = _bool_value(selection.get("active_profile_blocks_new_entry"), default=False)
    if "active_profile_blocks_new_entry" not in selection and blocking_active and final_profile in {"STRESS", "DEGRADED"}:
        profile_new_entry_blocked = True

    confidence = _as_optional_float(selection.get("confidence"))
    if confidence is None:
        confidence = _as_optional_float(latest_recommendation.get("confidence"))

    valid_until = _naive_utc(_as_datetime(selection.get("valid_until"))) or _naive_utc(
        _as_datetime(latest_recommendation.get("valid_until"))
    )

    return {
        "deterministic_market_profile": _string_or_none(
            selection.get("deterministic_profile") or selector_state.get("deterministic_profile")
        ),
        "ai_recommended_profile": _string_or_none(
            selection.get("ai_recommended_profile") or latest_recommendation.get("recommended_profile_id")
        ),
        "ai_recommendation_id": _string_or_none(
            selection.get("ai_recommendation_id") or latest_recommendation.get("recommendation_id")
        ),
        "ai_recommendation_confidence": confidence,
        "ai_recommendation_valid_until": valid_until,
        "ai_recommendation_reason_codes": _as_string_list(latest_recommendation.get("reason_codes")),
        "ai_recommendation_status": ai_status or "unknown",
        "final_active_execution_profile": final_profile,
        "shadow_final_execution_profile": _string_or_none(selection.get("shadow_final_profile")),
        "profile_selection_mode": _string_or_none(selection.get("selection_mode")) or auto_apply_mode,
        "profile_selected_reason": _string_or_none(selection.get("selected_reason")),
        "was_tightened_by_ai": _bool_value(selection.get("was_tightened_by_ai"), default=False),
        "was_relaxation_blocked": _bool_value(selection.get("was_relaxation_blocked"), default=False),
        "relaxation_block_reason": _first_string(relaxation_block_reason_codes),
        "relaxation_block_reason_codes": relaxation_block_reason_codes,
        "ai_recommendation_ignored_reason_codes": ignored_reason_codes,
        "next_ai_settings_review_at": _ai_settings_next_review_at(
            advisor_state=advisor_state,
            latest_recommendation=latest_recommendation,
            policy=policy,
        ),
        "ai_settings_shadow_mode": bool(policy.get("advisor_shadow_mode", True)),
        "ai_settings_auto_apply_mode": auto_apply_mode,
        "profile_new_entry_blocked": profile_new_entry_blocked,
        "profile_survival_paths_allowed": True,
    }


def get_operator_dashboard(session: Session, *, view: str | None = None) -> OperatorDashboardResponse:
    operator_view = _normalize_operator_dashboard_view(view)
    fact_decision_projection = operator_view in {"decision", "scheduler"}
    overview = get_overview(session)
    settings_row = get_or_create_settings(session)
    execution_profile_state = _operator_execution_profile_state(settings_row)
    profitability = (
        None
        if operator_view is not None
        else get_profitability_dashboard(
            session,
            overview=overview,
            performance_window_specs=OPERATOR_PERFORMANCE_WINDOW_SPECS,
            cost_window_specs=OPERATOR_PROFITABILITY_COST_WINDOW_SPECS,
        )
    )
    latest_scheduler = (
        session.scalar(select(SchedulerRun).order_by(desc(SchedulerRun.created_at)).limit(1))
        if operator_view in {None, "scheduler"}
        else None
    )
    include_decision_state = operator_view not in {"market", "risk"}
    include_risk_state = operator_view != "market"
    include_execution_state = operator_view in {None, "decision", "risk"}
    include_protection_state = operator_view in {None, "decision"}
    include_event_operator_control = operator_view is None
    include_audit_events = operator_view is None
    symbol_summaries = _build_operator_symbol_summaries(
        session,
        tracked_symbols=overview.tracked_symbols,
        overview=overview,
        include_decision_state=include_decision_state,
        include_risk_state=include_risk_state,
        include_execution_state=include_execution_state,
        include_protection_state=include_protection_state,
        include_event_operator_control=include_event_operator_control,
        include_audit_events=include_audit_events,
        prefer_fact_decision_lookup=True,
        extracted_symbol_fallback_limit=0
        if fact_decision_projection
        else OPERATOR_EXTRACTED_SYMBOL_FALLBACK_SCAN_LIMIT,
    )
    compact_performance_windows = (
        []
        if profitability is None
        else [
            _compact_profitability_window(window)
            for window in profitability.windows[:OPERATOR_PERFORMANCE_WINDOW_LIMIT]
        ]
    )
    compact_execution_windows = (
        []
        if profitability is None
        else [
            _compact_execution_window(window)
            for window in profitability.execution_windows[:OPERATOR_PERFORMANCE_WINDOW_LIMIT]
        ]
    )
    limited_live_readiness = (
        LimitedLiveReadinessReport() if profitability is None else profitability.limited_live_readiness
    )
    audit_rows = get_audit_timeline(session, limit=OPERATOR_AUDIT_LIMIT) if include_audit_events else []
    return OperatorDashboardResponse(
        generated_at=utcnow_naive(),
        control=OperatorControlState(
            generated_at=utcnow_naive(),
            operational_status=overview.operational_status,
            control_status_summary=overview.operational_status.control_status_summary,
            can_enter_new_position=overview.operational_status.can_enter_new_position,
            mode=overview.mode,
            rollout_mode=overview.operational_status.rollout_mode,
            exchange_submit_allowed=overview.operational_status.exchange_submit_allowed,
            limited_live_max_notional=overview.operational_status.limited_live_max_notional,
            exchange_connectivity_state=overview.operational_status.exchange_connectivity_state,
            default_symbol=overview.symbol,
            default_timeframe=overview.timeframe,
            tracked_symbols=overview.tracked_symbols,
            tracked_symbol_count=len(overview.tracked_symbols),
            live_trading_enabled=overview.operational_status.live_trading_enabled,
            live_execution_ready=overview.operational_status.live_execution_ready,
            approval_armed=overview.operational_status.approval_armed,
            approval_expires_at=overview.operational_status.approval_expires_at,
            trading_paused=overview.operational_status.trading_paused,
            operating_state=overview.operational_status.operating_state,
            guard_mode_reason_category=overview.operational_status.guard_mode_reason_category,
            guard_mode_reason_code=overview.operational_status.guard_mode_reason_code,
            guard_mode_reason_message=overview.operational_status.guard_mode_reason_message,
            pause_reason_code=overview.operational_status.pause_reason_code,
            pause_origin=overview.operational_status.pause_origin,
            pause_triggered_at=overview.operational_status.pause_triggered_at,
            auto_resume_status=overview.operational_status.auto_resume_status,
            auto_resume_eligible=overview.operational_status.auto_resume_eligible,
            auto_resume_after=overview.operational_status.auto_resume_after,
            blocked_reasons=overview.operational_status.blocked_reasons,
            blocked_reason_codes=overview.operational_status.blocked_reason_codes,
            degraded_reason_codes=overview.operational_status.degraded_reason_codes,
            protection_reason_codes=overview.operational_status.protection_reason_codes,
            auto_resume_last_blockers=overview.operational_status.auto_resume_last_blockers,
            latest_blocked_reasons=overview.operational_status.latest_blocked_reasons,
            market_freshness_summary=overview.operational_status.market_freshness_summary,
            sync_freshness_summary=overview.operational_status.sync_freshness_summary,
            protection_recovery_status=overview.operational_status.protection_recovery_status,
            protected_positions=overview.protected_positions,
            unprotected_positions=overview.unprotected_positions,
            open_positions=overview.open_positions,
            pnl_summary=overview.pnl_summary,
            daily_pnl=overview.daily_pnl,
            cumulative_pnl=overview.cumulative_pnl,
            account_sync_summary=overview.operational_status.account_sync_summary,
            exposure_summary=overview.exposure_summary,
            user_stream_summary=overview.user_stream_summary,
            reconciliation_summary=overview.reconciliation_summary,
            candidate_selection_summary=overview.candidate_selection_summary,
            operator_alert=overview.operator_alert,
            limited_live_readiness=limited_live_readiness,
            scheduler_status=latest_scheduler.status if latest_scheduler is not None else None,
            scheduler_window=latest_scheduler.schedule_window if latest_scheduler is not None else None,
            scheduler_triggered_by=latest_scheduler.triggered_by if latest_scheduler is not None else None,
            scheduler_last_run_at=latest_scheduler.created_at if latest_scheduler is not None else None,
            scheduler_next_run_at=latest_scheduler.next_run_at if latest_scheduler is not None else None,
            **execution_profile_state,
            last_market_refresh_at=overview.last_market_refresh_at,
            last_decision_at=overview.last_decision_at,
            last_decision_snapshot_at=overview.last_decision_snapshot_at,
            last_decision_reference=overview.last_decision_reference,
        ),
        symbols=symbol_summaries,
        market_signal=OperatorMarketSignalSummary(
            market_context_summary=_compact_market_context_summary(overview.market_context_summary),
            performance_windows=compact_performance_windows,
            profitability_cost_breakdowns=[] if profitability is None else profitability.cost_breakdowns,
            hold_blocked_summary=(
                DashboardHoldBlockedSummary() if profitability is None else profitability.hold_blocked_summary
            ),
            adaptive_signal_summary=(
                {} if profitability is None else _compact_adaptive_signal_summary(profitability.adaptive_signal_summary)
            ),
        ),
        execution_windows=compact_execution_windows,
        audit_events=[_build_operator_audit_entry(item) for item in audit_rows if isinstance(item, dict)],
    )


def get_risk_checks(session: Session, limit: int = 50, *, compact: bool = False) -> list[dict[str, object]]:
    recent_risk_ids = (
        select(RiskCheck.id)
        .order_by(desc(RiskCheck.created_at), desc(RiskCheck.id))
        .limit(limit)
        .subquery()
    )
    rows = session.execute(
        select(RiskCheck, AgentRun)
        .join(recent_risk_ids, RiskCheck.id == recent_risk_ids.c.id)
        .outerjoin(AgentRun, AgentRun.id == RiskCheck.decision_run_id)
        .order_by(desc(RiskCheck.created_at), desc(RiskCheck.id))
    ).all()
    decision_ids = [
        risk_row.decision_run_id
        for risk_row, _decision_row in rows
        if risk_row.decision_run_id is not None
    ]
    pending_entry_plans_by_decision: dict[int, PendingEntryPlan] = {}
    if decision_ids:
        plan_rows = session.scalars(
            select(PendingEntryPlan)
            .where(PendingEntryPlan.source_decision_run_id.in_(decision_ids))
            .order_by(desc(PendingEntryPlan.updated_at), desc(PendingEntryPlan.created_at))
        )
        for plan_row in plan_rows:
            if plan_row.source_decision_run_id is None:
                continue
            pending_entry_plans_by_decision.setdefault(plan_row.source_decision_run_id, plan_row)
    payloads: list[dict[str, object]] = []
    for risk_row, decision_row in rows:
        payload = _serialize_model_row(risk_row)
        macro_event_summary = _decision_macro_event_context_summary(decision_row)
        risk_snapshot = _build_risk_snapshot(risk_row)
        risk_payload = _dashboard_risk_payload_from_row(risk_row)
        payload["reason_codes"] = risk_payload["reason_codes"]
        payload["blocked_reason_codes"] = risk_payload["blocked_reason_codes"]
        if isinstance(payload.get("payload"), dict):
            payload["payload"] = dict(payload["payload"])
            payload["payload"]["reason_codes"] = risk_payload["reason_codes"]
            payload["payload"]["blocked_reason_codes"] = risk_payload["blocked_reason_codes"]
            if risk_payload["reason_evidence"]:
                payload["payload"]["reason_evidence"] = risk_payload["reason_evidence"]
        payload["ai_trigger_reason"] = _ai_trigger_reason_from_decision_row(decision_row)
        payload["ai_review_type"] = _ai_review_type_from_decision_row(decision_row)
        payload["ai_trigger_reason_codes"] = _ai_trigger_reason_codes_from_decision_row(decision_row)
        payload["last_ai_skip_reason"] = _last_ai_skip_reason_from_decision_row(decision_row)
        payload["ai_skip_reason"] = payload["last_ai_skip_reason"]
        payload["ai_trigger_summary"] = _ai_trigger_summary_from_decision_row(decision_row)
        payload["market_signal_summary"] = _market_signal_summary_from_decision_row(decision_row)
        payload["ai_review"] = _ai_review_snapshot_from_decision_row(decision_row).model_dump(mode="json")
        payload["market_signal_context"] = _market_signal_context_from_decision_row(decision_row).model_dump(mode="json")
        payload["macro_event_context_summary"] = macro_event_summary.model_dump(mode="json")
        payload["macro_event_risk_summary"] = macro_event_summary.model_dump(mode="json")
        payload["risk_guard_result"] = _risk_guard_result_from_snapshot(risk_snapshot).model_dump(mode="json")
        payload["pending_entry_plan"] = _build_pending_entry_plan_snapshot(
            pending_entry_plans_by_decision.get(risk_row.decision_run_id)
        ).model_dump(mode="json")
        if compact:
            payload["payload"] = _compact_dict(payload.get("payload"), allowed_keys=RISK_COMPACT_PAYLOAD_KEYS)
            payload["payload_mode"] = "compact"
        payloads.append(payload)
    return payloads


def get_agent_runs(session: Session, limit: int = 100, *, compact: bool = False) -> list[dict[str, object]]:
    rows = list(session.scalars(select(AgentRun).order_by(desc(AgentRun.created_at)).limit(limit)))
    if compact:
        return [_compact_agent_run_row(row) for row in rows]
    return _serialize_model_list(rows)


def get_scheduler_runs(session: Session, limit: int = 50, *, compact: bool = False) -> list[dict[str, object]]:
    if compact:
        rows = session.execute(
            select(
                SchedulerRun.id,
                SchedulerRun.schedule_window,
                SchedulerRun.workflow,
                SchedulerRun.status,
                SchedulerRun.triggered_by,
                SchedulerRun.next_run_at,
                SchedulerRun.created_at,
                SchedulerRun.updated_at,
            )
            .order_by(desc(SchedulerRun.created_at))
            .limit(limit)
        ).mappings()
        return [_serialize_mapping_row(row) for row in rows]
    return _serialize_model_list(list(session.scalars(select(SchedulerRun).order_by(desc(SchedulerRun.created_at)).limit(limit))))


def _audit_related_id(payload: dict[str, Any]) -> tuple[str | None, object | None]:
    for key in AUDIT_RELATED_ID_KEYS:
        value = payload.get(key)
        if value not in {None, ""}:
            return key, value
    return None, None


def _audit_message_summary(value: object, *, limit: int = 160) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _audit_legacy_trigger_reason(value: object, *, depth: int = 0, seen: set[int] | None = None) -> str | None:
    if depth > 6 or not isinstance(value, (dict, list)):
        return None
    seen = seen or set()
    marker = id(value)
    if marker in seen:
        return None
    seen.add(marker)
    if isinstance(value, list):
        for item in value:
            nested = _audit_legacy_trigger_reason(item, depth=depth + 1, seen=seen)
            if nested:
                return nested
        return None
    for key, nested_value in value.items():
        if (
            key in {"trigger_reason", "last_ai_trigger_reason"}
            and isinstance(nested_value, str)
            and nested_value in AUDIT_LEGACY_TRIGGER_REASONS
        ):
            return nested_value
        nested = _audit_legacy_trigger_reason(nested_value, depth=depth + 1, seen=seen)
        if nested:
            return nested
    return None


def _audit_severity_rank(value: object) -> int:
    return {
        "critical": 0,
        "error": 1,
        "warning": 2,
        "info": 3,
    }.get(str(value or "").lower(), 99)


def _audit_sort_key(row: dict[str, object], sort: str) -> tuple[object, ...]:
    created_at = str(row.get("created_at") or "")
    if sort == "oldest":
        return (created_at, int(row.get("id") or 0))
    if sort == "severity":
        return (_audit_severity_rank(row.get("severity")), created_at)
    return (created_at, int(row.get("id") or 0))


def _audit_has_value(value: object) -> bool:
    if value is None or value == "":
        return False
    return not (isinstance(value, (list, dict)) and not value)


def _compact_audit_timeline_row(row: dict[str, object]) -> dict[str, object]:
    return {
        key: row[key]
        for key in AUDIT_COMPACT_ROW_KEYS
        if key in row and _audit_has_value(row[key])
    }


def _project_audit_rows(
    session: Session,
    rows: Sequence[AuditEvent],
    *,
    compact: bool = False,
) -> list[dict[str, object]]:
    payloads = _serialize_model_list(rows)
    suppression_context_by_decision_id = _decision_run_suppression_context_by_id(
        session,
        [
            decision_run_id
            for row in payloads
            if isinstance(row, dict)
            for decision_run_id in [_decision_run_id_from_audit_row(row)]
            if decision_run_id is not None
        ],
    )
    projected_rows: list[dict[str, object]] = []
    for row in payloads:
        if not isinstance(row, dict):
            continue
        payload = _as_dict(row.get("payload"))
        payload_has_suppression_source = _has_active_position_suppression_source(payload)
        decision_run_id = _decision_run_id_from_audit_row(row)
        fallback_context = suppression_context_by_decision_id.get(decision_run_id)
        suppression_projection = (
            _active_position_suppression_projection(payload)
            if payload_has_suppression_source
            else _active_position_suppression_projection(fallback_context)
        )
        if payload_has_suppression_source or fallback_context:
            row.update(suppression_projection)
            payload = {
                **payload,
                **suppression_projection,
            }
            row["payload"] = payload
        category = classify_audit_event(
            event_type=str(row.get("event_type") or "unknown"),
            entity_type=str(row.get("entity_type") or "unknown"),
            payload=payload,
        )
        related_type, related_id = _audit_related_id(payload)
        row["event_category"] = category
        row["message_summary"] = _audit_message_summary(row.get("message"))
        row["related_type"] = related_type
        row["related_id"] = related_id
        row["has_payload"] = bool(payload)
        row["payload_keys"] = list(payload.keys())[:12]
        legacy_trigger_reason = _audit_legacy_trigger_reason(payload)
        if legacy_trigger_reason:
            row["legacy_review_trigger_reason"] = legacy_trigger_reason
        projected_rows.append(_compact_audit_timeline_row(row) if compact else row)
    return projected_rows


def _normalized_audit_category(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    if normalized == "all":
        return None
    return normalized if normalized in AUDIT_CATEGORY_VALUES else None


def get_audit_timeline(
    session: Session,
    limit: int = 100,
    event_type: str | None = None,
    severity: str | None = None,
    search: str | None = None,
    event_category: str | None = None,
    sort: str = "newest",
    compact: bool = False,
) -> list[dict[str, object]]:
    category_filter = _normalized_audit_category(event_category)
    selected_sort = sort if sort in {"newest", "oldest", "severity"} else "newest"
    candidate_limit = min(max(limit * 5, limit), 500) if category_filter else limit
    statement = select(AuditEvent)
    if event_type:
        statement = statement.where(AuditEvent.event_type == event_type)
    if severity:
        statement = statement.where(AuditEvent.severity == severity)
    if search:
        token = f"%{search.lower()}%"
        statement = statement.where(
            or_(
                func.lower(AuditEvent.event_type).like(token),
                func.lower(AuditEvent.entity_type).like(token),
                func.lower(AuditEvent.entity_id).like(token),
                func.lower(AuditEvent.message).like(token),
                func.lower(AuditEvent.severity).like(token),
            )
        )
    if selected_sort == "oldest":
        statement = statement.order_by(AuditEvent.created_at.asc(), AuditEvent.id.asc())
    else:
        statement = statement.order_by(desc(AuditEvent.created_at), desc(AuditEvent.id))
    rows = _project_audit_rows(session, list(session.scalars(statement.limit(candidate_limit))), compact=compact)
    if category_filter:
        rows = [row for row in rows if row.get("event_category") == category_filter]
    rows = sorted(
        rows,
        key=lambda row: _audit_sort_key(row, selected_sort),
        reverse=selected_sort == "newest",
    )
    return rows[:limit]


def get_audit_event_detail(session: Session, audit_event_id: int) -> dict[str, object] | None:
    row = session.get(AuditEvent, audit_event_id)
    if row is None:
        return None
    rows = _project_audit_rows(session, [row], compact=False)
    return rows[0] if rows else None


def get_alerts(session: Session, limit: int = 50) -> list[dict[str, object]]:
    return _serialize_model_list(list(session.scalars(select(Alert).order_by(desc(Alert.created_at)).limit(limit))))
