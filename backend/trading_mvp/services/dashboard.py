from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import String, cast, desc, func, or_, select
from sqlalchemy.orm import Session

from trading_mvp.models import (
    AgentRun,
    Alert,
    AuditEvent,
    Execution,
    FeatureSnapshot,
    MarketSnapshot,
    Order,
    PnLSnapshot,
    Position,
    ProductBacklog,
    RiskCheck,
    SchedulerRun,
)
from trading_mvp.schemas import (
    AuditTimelineEntry,
    DashboardExecutionProfileSummary,
    DashboardExecutionWindowSummary,
    DashboardHoldBlockedSummary,
    OperatorControlState,
    OperatorDashboardResponse,
    OperatorDecisionSnapshot,
    OperatorExecutionSnapshot,
    OperatorMarketSignalSummary,
    OperatorRiskSnapshot,
    DashboardProfitabilityResponse,
    DashboardProfitabilityWindow,
    OverviewResponse,
    PerformanceAggregateEntry,
)
from trading_mvp.services.backlog_insights import build_signal_performance_report
from trading_mvp.services.runtime_state import PROTECTION_REQUIRED_STATE, summarize_runtime_state
from trading_mvp.services.settings import (
    get_effective_symbols,
    get_or_create_settings,
    is_live_execution_ready,
    serialize_settings,
)
from trading_mvp.time_utils import utcnow_naive

FINAL_ORDER_STATUSES = {"filled", "canceled", "cancelled", "rejected", "expired"}
PROTECTIVE_ORDER_TYPES = {"stop_market", "take_profit_market"}
AUDIT_CATEGORY_RISK = "risk"
AUDIT_CATEGORY_EXECUTION = "execution"
AUDIT_CATEGORY_APPROVAL_CONTROL = "approval_control"
AUDIT_CATEGORY_PROTECTION = "protection"
AUDIT_CATEGORY_HEALTH_SYSTEM = "health_system"
AUDIT_CATEGORY_AI_DECISION = "ai_decision"

AUDIT_APPROVAL_CONTROL_EVENT_TYPES = {
    "settings_updated",
    "trading_paused",
    "trading_resumed",
    "live_approval_armed",
    "live_approval_disarmed",
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
    "live_execution",
    "live_execution_rejected",
    "live_execution_error",
    "live_execution_skipped",
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
    "user_change_request_created",
    "applied_change_record_created",
    "backlog_auto_applied",
    "backlog_auto_apply_batch",
}


def _serialize_model_list(rows: Sequence[object]) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for row in rows:
        values = {}
        for key in row.__table__.columns:  # type: ignore[attr-defined]
            value = getattr(row, key.name)
            values[key.name] = value.isoformat() if hasattr(value, "isoformat") else value
        payloads.append(values)
    return payloads


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
                Order.status.notin_(FINAL_ORDER_STATUSES),
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


def _as_dict(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


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
    settings_payload = serialize_settings(settings_row)
    runtime_state = summarize_runtime_state(settings_row)
    latest_market = session.scalar(select(MarketSnapshot).order_by(desc(MarketSnapshot.snapshot_time)).limit(1))
    latest_decision = session.scalar(select(AgentRun).where(AgentRun.role == "trading_decision").order_by(desc(AgentRun.created_at)).limit(1))
    latest_risk = session.scalar(select(RiskCheck).order_by(desc(RiskCheck.created_at)).limit(1))
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
    operating_state = str(settings_payload.get("operating_state") or runtime_state["operating_state"])
    if not settings_row.trading_paused and unprotected_positions > 0:
        operating_state = PROTECTION_REQUIRED_STATE
    blocked_reasons = latest_risk.reason_codes if latest_risk is not None and not latest_risk.allowed else []
    auto_resume_last_blockers = _as_string_list(settings_payload.get("auto_resume_last_blockers", []))
    latest_blocked_reasons = _as_string_list(settings_payload.get("latest_blocked_reasons", []))
    guard_mode_reason_category = str(settings_payload.get("guard_mode_reason_category") or "") or None
    guard_mode_reason_code = str(settings_payload.get("guard_mode_reason_code") or "") or None
    guard_mode_reason_message = str(settings_payload.get("guard_mode_reason_message") or "") or None
    serialized_missing_symbols = _as_string_list(settings_payload.get("missing_protection_symbols", []))
    runtime_missing_items = _as_missing_items(runtime_state["missing_protection_items"])
    pnl_summary = _as_dict(settings_payload.get("pnl_summary", {}))
    account_sync_summary = _as_dict(settings_payload.get("account_sync_summary", {}))
    exposure_summary = _as_dict(settings_payload.get("exposure_summary", {}))
    execution_policy_summary = _as_dict(settings_payload.get("execution_policy_summary", {}))
    market_context_summary = _as_dict(settings_payload.get("market_context_summary", {}))
    adaptive_protection_summary = _as_dict(settings_payload.get("adaptive_protection_summary", {}))
    adaptive_signal_summary = _as_dict(settings_payload.get("adaptive_signal_summary", {}))
    position_management_summary = _as_dict(settings_payload.get("position_management_summary", {}))
    return OverviewResponse(
        mode=str(settings_payload["mode"]),
        symbol=settings_row.default_symbol,
        tracked_symbols=get_effective_symbols(settings_row),
        timeframe=settings_row.default_timeframe,
        latest_price=latest_market.latest_price if latest_market is not None else 0.0,
        latest_decision=latest_decision.output_payload if latest_decision is not None else None,
        latest_risk=latest_risk.payload if latest_risk is not None else None,
        open_positions=len(open_positions),
        live_trading_enabled=settings_row.live_trading_enabled,
        live_execution_ready=is_live_execution_ready(settings_row),
        trading_paused=settings_row.trading_paused,
        guard_mode_reason_category=guard_mode_reason_category,
        guard_mode_reason_code=guard_mode_reason_code,
        guard_mode_reason_message=guard_mode_reason_message,
        pause_reason_code=str(settings_payload.get("pause_reason_code") or "") or None,
        pause_origin=str(settings_payload.get("pause_origin") or "") or None,
        pause_triggered_at=settings_row.pause_triggered_at,
        auto_resume_after=settings_row.auto_resume_after,
        auto_resume_status=str(settings_payload.get("auto_resume_status") or "not_paused"),
        auto_resume_eligible=bool(settings_payload.get("auto_resume_eligible", False)),
        auto_resume_last_blockers=auto_resume_last_blockers,
        pause_severity=str(settings_payload.get("pause_severity") or "") or None,
        pause_recovery_class=str(settings_payload.get("pause_recovery_class") or "") or None,
        operating_state=operating_state,
        protection_recovery_status=str(settings_payload.get("protection_recovery_status") or runtime_state["protection_recovery_status"]),
        protection_recovery_active=bool(settings_payload.get("protection_recovery_active", runtime_state["protection_recovery_active"])),
        protection_recovery_failure_count=_as_int(
            settings_payload.get(
                "protection_recovery_failure_count",
                runtime_state["protection_recovery_failure_count"],
            ),
            default=0,
        ),
        missing_protection_symbols=missing_protection_symbols
        or serialized_missing_symbols,
        missing_protection_items=missing_protection_items
        or runtime_missing_items,
        pnl_summary=pnl_summary,
        account_sync_summary=account_sync_summary,
        exposure_summary=exposure_summary,
        execution_policy_summary=execution_policy_summary,
        market_context_summary=market_context_summary,
        adaptive_protection_summary=adaptive_protection_summary,
        adaptive_signal_summary=adaptive_signal_summary,
        position_management_summary=position_management_summary,
        daily_pnl=latest_pnl.daily_pnl if latest_pnl is not None else 0.0,
        cumulative_pnl=latest_pnl.cumulative_pnl if latest_pnl is not None else 0.0,
        blocked_reasons=blocked_reasons,
        latest_blocked_reasons=latest_blocked_reasons or blocked_reasons,
        protected_positions=protected_positions,
        unprotected_positions=unprotected_positions,
        position_protection_summary=protection_summary,
    )


def get_market_snapshots(session: Session, limit: int = 50) -> list[dict[str, object]]:
    return _serialize_model_list(list(session.scalars(select(MarketSnapshot).order_by(desc(MarketSnapshot.snapshot_time)).limit(limit))))


def get_feature_snapshots(session: Session, limit: int = 50) -> list[dict[str, object]]:
    return _serialize_model_list(list(session.scalars(select(FeatureSnapshot).order_by(desc(FeatureSnapshot.feature_time)).limit(limit))))


def get_decisions(session: Session, limit: int = 50) -> list[dict[str, object]]:
    return _serialize_model_list(list(session.scalars(select(AgentRun).where(AgentRun.role == "trading_decision").order_by(desc(AgentRun.created_at)).limit(limit))))


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


def get_orders(
    session: Session,
    limit: int = 50,
    mode: str | None = None,
    symbol: str | None = None,
    status: str | None = None,
    search: str | None = None,
) -> list[dict[str, object]]:
    selected_mode = mode or "live"
    statement = select(Order).where(Order.mode == selected_mode)
    if symbol:
        statement = statement.where(Order.symbol == symbol.upper())
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
    return _serialize_model_list(list(session.scalars(statement)))


def get_executions(
    session: Session,
    limit: int = 50,
    mode: str | None = None,
    symbol: str | None = None,
    status: str | None = None,
    search: str | None = None,
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
                "avg_slippage_pct_sum": 0.0,
                "avg_slippage_pct_count": 0,
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
            "average_realized_slippage_pct": 0.0,
            "fee_total": 0.0,
            "realized_pnl_total": 0.0,
            "net_realized_pnl_total": 0.0,
        }
        slippage_sum = 0.0
        slippage_count = 0

        for order_row, decision_output in bucket_orders:
            metadata = order_row.metadata_json if isinstance(order_row.metadata_json, dict) else {}
            quality = metadata.get("execution_quality") if isinstance(metadata.get("execution_quality"), dict) else {}
            policy = metadata.get("execution_policy") if isinstance(metadata.get("execution_policy"), dict) else {}
            decision_payload = decision_output if isinstance(decision_output, dict) else {}
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

            summary["fee_total"] += _as_float(quality.get("fees_total"))
            summary["realized_pnl_total"] += _as_float(quality.get("realized_pnl_total"))
            summary["net_realized_pnl_total"] += _as_float(quality.get("net_realized_pnl_total"))
            if "realized_slippage_pct" in quality:
                slippage_sum += realized_slippage_pct
                slippage_count += 1
                profile_bucket["avg_slippage_pct_sum"] = _as_float(profile_bucket["avg_slippage_pct_sum"]) + realized_slippage_pct
                profile_bucket["avg_slippage_pct_count"] = _as_int(profile_bucket["avg_slippage_pct_count"]) + 1

            profile_bucket["orders"] = _as_int(profile_bucket["orders"]) + 1
            profile_bucket["symbol"] = order_row.symbol
            profile_bucket["timeframe"] = decision_payload.get("timeframe")

        summary["average_realized_slippage_pct"] = slippage_sum / slippage_count if slippage_count else 0.0
        profiles = sorted(
            [
                {
                    "policy_profile": str(item["policy_profile"]),
                    "symbol": item.get("symbol"),
                    "timeframe": item.get("timeframe"),
                    "orders": _as_int(item["orders"]),
                    "partial_fill_orders": _as_int(item["partial_fill_orders"]),
                    "aggressive_fallback_orders": _as_int(item["aggressive_fallback_orders"]),
                    "average_realized_slippage_pct": (
                        _as_float(item["avg_slippage_pct_sum"]) / _as_int(item["avg_slippage_pct_count"])
                        if _as_int(item["avg_slippage_pct_count"]) > 0
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
                    "average_realized_slippage_pct": summary["average_realized_slippage_pct"],
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
                average_realized_slippage_pct=_as_float(item.get("average_realized_slippage_pct"), default=0.0),
            )
        )
    return sorted(
        profiles,
        key=lambda item: (
            item.partial_fill_orders,
            item.aggressive_fallback_orders,
            item.average_realized_slippage_pct,
            item.orders,
        ),
        reverse=True,
    )[:limit]


def get_profitability_dashboard(session: Session) -> DashboardProfitabilityResponse:
    overview = get_overview(session)
    performance_report = build_signal_performance_report(session)
    execution_report = get_execution_quality_report(session)

    windows = [
        DashboardProfitabilityWindow(
            window_label=window.window_label,
            window_hours=window.window_hours,
            summary=window.summary,
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
        for window in performance_report.windows
    ]

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
        execution_windows=execution_windows,
        hold_blocked_summary=hold_blocked_summary,
    )


def _build_decision_snapshot(row: AgentRun | None) -> OperatorDecisionSnapshot:
    if row is None:
        return OperatorDecisionSnapshot()
    payload = row.output_payload if isinstance(row.output_payload, dict) else {}
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
        raw_output=payload,
    )


def _build_risk_snapshot(row: RiskCheck | None) -> OperatorRiskSnapshot:
    if row is None:
        return OperatorRiskSnapshot()
    payload = row.payload if isinstance(row.payload, dict) else {}
    return OperatorRiskSnapshot(
        risk_check_id=row.id,
        decision_run_id=row.decision_run_id,
        created_at=row.created_at,
        allowed=row.allowed,
        decision=row.decision,
        operating_state=str(payload.get("operating_state") or "") or None,
        reason_codes=_as_string_list(row.reason_codes),
        approved_risk_pct=row.approved_risk_pct,
        approved_leverage=row.approved_leverage,
        raw_payload=payload,
    )


def _build_execution_snapshot(session: Session, decision_run_id: int | None) -> OperatorExecutionSnapshot:
    order_row: Order | None = None
    if decision_run_id is not None:
        order_row = session.scalar(
            select(Order)
            .where(Order.mode == "live", Order.decision_run_id == decision_run_id)
            .order_by(desc(Order.created_at))
            .limit(1)
        )
        if order_row is None:
            return OperatorExecutionSnapshot(decision_run_id=decision_run_id)
    if order_row is None:
        order_row = session.scalar(select(Order).where(Order.mode == "live").order_by(desc(Order.created_at)).limit(1))
    if order_row is None:
        return OperatorExecutionSnapshot()

    execution_row = session.scalar(
        select(Execution)
        .where(Execution.order_id == order_row.id)
        .order_by(desc(Execution.created_at))
        .limit(1)
    )
    decision_output = {}
    if order_row.decision_run_id is not None:
        decision_output = (
            session.scalar(select(AgentRun.output_payload).where(AgentRun.id == order_row.decision_run_id).limit(1)) or {}
        )
    order_metadata = order_row.metadata_json if isinstance(order_row.metadata_json, dict) else {}
    decision_payload = decision_output if isinstance(decision_output, dict) else {}
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
        execution_policy=_as_dict(order_metadata.get("execution_policy", {})),
        execution_quality=_as_dict(order_metadata.get("execution_quality", {})),
        decision_summary={
            "decision": decision_payload.get("decision"),
            "timeframe": decision_payload.get("timeframe"),
            "confidence": decision_payload.get("confidence"),
            "rationale_codes": decision_payload.get("rationale_codes", []),
        },
    )


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


def get_operator_dashboard(session: Session) -> OperatorDashboardResponse:
    overview = get_overview(session)
    profitability = get_profitability_dashboard(session)
    settings_row = get_or_create_settings(session)
    latest_scheduler = session.scalar(select(SchedulerRun).order_by(desc(SchedulerRun.created_at)).limit(1))
    latest_decision_row = session.scalar(
        select(AgentRun)
        .where(AgentRun.role == "trading_decision")
        .order_by(desc(AgentRun.created_at))
        .limit(1)
    )
    latest_risk_row = None
    if latest_decision_row is not None:
        latest_risk_row = session.scalar(
            select(RiskCheck)
            .where(RiskCheck.decision_run_id == latest_decision_row.id)
            .order_by(desc(RiskCheck.created_at))
            .limit(1)
        )
    if latest_risk_row is None:
        latest_risk_row = session.scalar(select(RiskCheck).order_by(desc(RiskCheck.created_at)).limit(1))

    audit_rows = get_audit_timeline(session, limit=6)
    return OperatorDashboardResponse(
        generated_at=utcnow_naive(),
        control=OperatorControlState(
            generated_at=utcnow_naive(),
            can_enter_new_position=overview.live_execution_ready and not overview.trading_paused and overview.operating_state == "TRADABLE",
            mode=overview.mode,
            symbol=overview.symbol,
            timeframe=overview.timeframe,
            tracked_symbols=overview.tracked_symbols,
            latest_price=overview.latest_price,
            live_trading_enabled=overview.live_trading_enabled,
            live_execution_ready=overview.live_execution_ready,
            approval_armed=settings_row.live_execution_armed,
            approval_expires_at=settings_row.live_execution_armed_until,
            trading_paused=overview.trading_paused,
            operating_state=overview.operating_state,
            guard_mode_reason_category=overview.guard_mode_reason_category,
            guard_mode_reason_code=overview.guard_mode_reason_code,
            guard_mode_reason_message=overview.guard_mode_reason_message,
            pause_reason_code=overview.pause_reason_code,
            pause_origin=overview.pause_origin,
            pause_triggered_at=overview.pause_triggered_at,
            auto_resume_status=overview.auto_resume_status,
            auto_resume_eligible=overview.auto_resume_eligible,
            auto_resume_after=overview.auto_resume_after,
            auto_resume_last_blockers=overview.auto_resume_last_blockers,
            latest_blocked_reasons=overview.latest_blocked_reasons,
            protection_recovery_status=overview.protection_recovery_status,
            protected_positions=overview.protected_positions,
            unprotected_positions=overview.unprotected_positions,
            open_positions=overview.open_positions,
            scheduler_status=latest_scheduler.status if latest_scheduler is not None else None,
            scheduler_window=latest_scheduler.schedule_window if latest_scheduler is not None else None,
            scheduler_triggered_by=latest_scheduler.triggered_by if latest_scheduler is not None else None,
            scheduler_last_run_at=latest_scheduler.created_at if latest_scheduler is not None else None,
            scheduler_next_run_at=latest_scheduler.next_run_at if latest_scheduler is not None else None,
        ),
        market_signal=OperatorMarketSignalSummary(
            market_context_summary=overview.market_context_summary,
            performance_windows=profitability.windows,
            hold_blocked_summary=profitability.hold_blocked_summary,
            adaptive_signal_summary=profitability.adaptive_signal_summary,
        ),
        ai_decision=_build_decision_snapshot(latest_decision_row),
        risk_guard=_build_risk_snapshot(latest_risk_row),
        execution=_build_execution_snapshot(session, latest_decision_row.id if latest_decision_row is not None else None),
        execution_windows=profitability.execution_windows,
        audit_events=[_build_audit_entry(item) for item in audit_rows if isinstance(item, dict)],
    )


def get_risk_checks(session: Session, limit: int = 50) -> list[dict[str, object]]:
    return _serialize_model_list(list(session.scalars(select(RiskCheck).order_by(desc(RiskCheck.created_at)).limit(limit))))


def get_agent_runs(session: Session, limit: int = 100) -> list[dict[str, object]]:
    return _serialize_model_list(list(session.scalars(select(AgentRun).order_by(desc(AgentRun.created_at)).limit(limit))))


def get_scheduler_runs(session: Session, limit: int = 50) -> list[dict[str, object]]:
    return _serialize_model_list(list(session.scalars(select(SchedulerRun).order_by(desc(SchedulerRun.created_at)).limit(limit))))


def get_audit_timeline(
    session: Session,
    limit: int = 100,
    event_type: str | None = None,
    severity: str | None = None,
    search: str | None = None,
) -> list[dict[str, object]]:
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
    statement = statement.order_by(desc(AuditEvent.created_at)).limit(limit)
    rows = _serialize_model_list(list(session.scalars(statement)))
    for row in rows:
        if isinstance(row, dict):
            row["event_category"] = classify_audit_event(
                event_type=str(row.get("event_type") or "unknown"),
                entity_type=str(row.get("entity_type") or "unknown"),
                payload=_as_dict(row.get("payload", {})),
            )
    return rows


def get_backlog(session: Session, limit: int = 50) -> list[dict[str, object]]:
    return _serialize_model_list(list(session.scalars(select(ProductBacklog).order_by(desc(ProductBacklog.created_at)).limit(limit))))


def get_alerts(session: Session, limit: int = 50) -> list[dict[str, object]]:
    return _serialize_model_list(list(session.scalars(select(Alert).order_by(desc(Alert.created_at)).limit(limit))))
