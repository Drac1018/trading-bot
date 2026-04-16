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
    RiskCheck,
    SchedulerRun,
)
from trading_mvp.schemas import (
    AuditTimelineEntry,
    DecisionReferencePayload,
    DashboardExecutionProfileSummary,
    DashboardExecutionWindowSummary,
    DashboardHoldBlockedSummary,
    OperationalStatusPayload,
    OperatorControlState,
    OperatorDashboardResponse,
    OperatorDecisionSnapshot,
    OperatorExecutionSnapshot,
    OperatorMarketSignalSummary,
    OperatorPositionSummary,
    OperatorProtectionSummary,
    OperatorRiskSnapshot,
    OperatorSymbolSummary,
    DashboardProfitabilityResponse,
    DashboardProfitabilityWindow,
    OverviewResponse,
    PerformanceAggregateEntry,
)
from trading_mvp.services.backlog_insights import build_signal_performance_report
from trading_mvp.services.runtime_state import PROTECTION_REQUIRED_STATE, summarize_runtime_state
from trading_mvp.services.settings import (
    _prioritize_blocked_reasons,
    build_operational_status_payload,
    get_effective_symbols,
    get_or_create_settings,
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


def _as_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _as_dict(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


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
    settings_payload = serialize_settings(settings_row)
    runtime_state = summarize_runtime_state(settings_row)
    latest_market = session.scalar(select(MarketSnapshot).order_by(desc(MarketSnapshot.snapshot_time)).limit(1))
    latest_decision = session.scalar(
        select(AgentRun).where(AgentRun.role == "trading_decision").order_by(desc(AgentRun.created_at)).limit(1)
    )
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
    blocked_reasons = _risk_reason_codes_from_row(latest_risk) if latest_risk is not None and not latest_risk.allowed else []
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
        blocked_reasons=blocked_reasons,
        latest_blocked_reasons=blocked_reasons,
        account_sync_summary=_as_dict(settings_payload.get("account_sync_summary", {})),
        sync_freshness_summary=_as_dict(settings_payload.get("sync_freshness_summary", {})),
        market_freshness_summary=_as_dict(settings_payload.get("market_freshness_summary", {})),
    )
    pnl_summary = _as_dict(settings_payload.get("pnl_summary", {}))
    exposure_summary = _as_dict(settings_payload.get("exposure_summary", {}))
    execution_policy_summary = _as_dict(settings_payload.get("execution_policy_summary", {}))
    market_context_summary = _as_dict(settings_payload.get("market_context_summary", {}))
    adaptive_protection_summary = _as_dict(settings_payload.get("adaptive_protection_summary", {}))
    adaptive_signal_summary = _as_dict(settings_payload.get("adaptive_signal_summary", {}))
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
        latest_decision=latest_decision.output_payload if latest_decision is not None else None,
        latest_risk=latest_risk.payload if latest_risk is not None else None,
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
        daily_pnl=latest_pnl.daily_pnl if latest_pnl is not None else 0.0,
        cumulative_pnl=latest_pnl.cumulative_pnl if latest_pnl is not None else 0.0,
        blocked_reasons=operational_status.blocked_reasons,
        latest_blocked_reasons=operational_status.latest_blocked_reasons,
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
    decision_reference = _build_decision_reference(row)
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
        decision_reference=decision_reference,
        raw_output=payload,
    )


def _risk_reason_codes_from_row(row: RiskCheck | None) -> list[str]:
    if row is None:
        return []
    payload = row.payload if isinstance(row.payload, dict) else {}
    payload_reason_codes = _as_string_list(payload.get("reason_codes", []))
    if payload_reason_codes:
        return _prioritize_blocked_reasons(payload_reason_codes)
    return _prioritize_blocked_reasons(_as_string_list(row.reason_codes))


def _build_risk_snapshot(row: RiskCheck | None) -> OperatorRiskSnapshot:
    if row is None:
        return OperatorRiskSnapshot()
    payload = row.payload if isinstance(row.payload, dict) else {}
    reason_codes = _risk_reason_codes_from_row(row)
    return OperatorRiskSnapshot(
        risk_check_id=row.id,
        decision_run_id=row.decision_run_id,
        created_at=row.created_at,
        allowed=row.allowed,
        decision=row.decision,
        operating_state=str(payload.get("operating_state") or "") or None,
        reason_codes=reason_codes,
        approved_risk_pct=row.approved_risk_pct,
        approved_leverage=row.approved_leverage,
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
        exposure_headroom_snapshot={
            str(key): _as_float(value, default=0.0)
            for key, value in _as_dict(payload.get("exposure_headroom_snapshot")).items()
        },
        debug_payload=_as_dict(payload.get("debug_payload", {})),
        raw_payload=payload,
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


def _extract_symbol_market_context(row: AgentRun | None, market_row: MarketSnapshot | None) -> dict[str, Any]:
    if row is not None and isinstance(row.input_payload, dict):
        features = _as_dict(row.input_payload.get("features", {}))
        regime = _as_dict(features.get("regime", {}))
        if regime:
            return {
                "primary_regime": regime.get("primary_regime"),
                "trend_alignment": regime.get("trend_alignment"),
                "volatility_regime": regime.get("volatility_regime"),
                "volume_regime": regime.get("volume_regime"),
                "momentum_state": regime.get("momentum_state"),
                "weak_volume": regime.get("weak_volume"),
                "momentum_weakening": regime.get("momentum_weakening"),
            }
    if market_row is not None and isinstance(market_row.payload, dict):
        return _as_dict(market_row.payload.get("regime_summary", {}))
    return {}


def _build_execution_snapshot_from_rows(
    order_row: Order | None,
    execution_row: Execution | None,
    decision_row: AgentRun | None,
) -> OperatorExecutionSnapshot:
    if order_row is None:
        return OperatorExecutionSnapshot(
            decision_run_id=decision_row.id if decision_row is not None else None,
            symbol=_decision_symbol(decision_row),
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
        execution_policy=_as_dict(order_metadata.get("execution_policy", {})),
        execution_quality=_as_dict(order_metadata.get("execution_quality", {})),
        decision_summary={
            "decision": decision_payload.get("decision"),
            "timeframe": decision_payload.get("timeframe"),
            "confidence": decision_payload.get("confidence"),
            "rationale_codes": decision_payload.get("rationale_codes", []),
        },
    )


def _build_position_snapshot(position: Position | None) -> OperatorPositionSummary:
    if position is None:
        return OperatorPositionSummary()
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
    )


def _build_protection_snapshot(protection_state: dict[str, object]) -> OperatorProtectionSummary:
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
    )


def _build_symbol_stale_flags(
    sync_freshness_summary: dict[str, Any],
    market_row: MarketSnapshot | None,
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
    return flags


def _latest_timestamp(*timestamps: datetime | None) -> datetime | None:
    values = [item for item in timestamps if item is not None]
    return max(values) if values else None


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
    if isinstance(symbols, list) and symbol_key in {str(item).upper() for item in symbols}:
        return True
    return False


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


def _build_operator_symbol_summaries(
    session: Session,
    *,
    tracked_symbols: list[str],
    overview: OverviewResponse,
) -> list[OperatorSymbolSummary]:
    symbol_keys = [item.upper() for item in tracked_symbols if item]
    latest_markets: dict[str, MarketSnapshot] = {}
    for row in session.scalars(
        select(MarketSnapshot)
        .where(MarketSnapshot.symbol.in_(symbol_keys))
        .order_by(desc(MarketSnapshot.snapshot_time))
    ):
        symbol = row.symbol.upper()
        latest_markets.setdefault(symbol, row)

    latest_decisions: dict[str, AgentRun] = {}
    for row in session.scalars(
        select(AgentRun)
        .where(AgentRun.role == "trading_decision")
        .order_by(desc(AgentRun.created_at))
    ):
        symbol = _decision_symbol(row)
        if symbol in symbol_keys and symbol not in latest_decisions:
            latest_decisions[symbol] = row
        if len(latest_decisions) == len(symbol_keys):
            break

    latest_risks: dict[str, RiskCheck] = {}
    for row in session.scalars(
        select(RiskCheck)
        .where(RiskCheck.symbol.in_(symbol_keys))
        .order_by(desc(RiskCheck.created_at))
    ):
        symbol = row.symbol.upper()
        latest_risks.setdefault(symbol, row)

    latest_orders: dict[str, Order] = {}
    for row in session.scalars(
        select(Order)
        .where(Order.mode == "live", Order.symbol.in_(symbol_keys))
        .order_by(desc(Order.created_at))
    ):
        symbol = row.symbol.upper()
        latest_orders.setdefault(symbol, row)

    latest_executions_by_order_id: dict[int, Execution] = {}
    order_ids = [row.id for row in latest_orders.values()]
    if order_ids:
        for row in session.scalars(
            select(Execution)
            .where(Execution.order_id.in_(order_ids))
            .order_by(desc(Execution.created_at))
        ):
            if row.order_id is None:
                continue
            latest_executions_by_order_id.setdefault(row.order_id, row)

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

    audit_rows = get_audit_timeline(session, limit=max(20, len(symbol_keys) * 6))
    audit_entries_by_symbol: dict[str, list[AuditTimelineEntry]] = {symbol: [] for symbol in symbol_keys}
    for row in audit_rows:
        if not isinstance(row, dict):
            continue
        for symbol in symbol_keys:
            if len(audit_entries_by_symbol[symbol]) >= 4:
                continue
            if _audit_event_matches_symbol(row, symbol):
                audit_entries_by_symbol[symbol].append(_build_audit_entry(row))

    summaries: list[OperatorSymbolSummary] = []
    for symbol in tracked_symbols:
        symbol_key = symbol.upper()
        decision_row = latest_decisions.get(symbol_key)
        risk_row = latest_risks.get(symbol_key)
        order_row = latest_orders.get(symbol_key)
        execution_row = latest_executions_by_order_id.get(order_row.id) if order_row is not None else None
        position_row = open_positions.get(symbol_key)
        market_row = latest_markets.get(symbol_key)
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
        stale_flags = _build_symbol_stale_flags(overview.sync_freshness_summary, market_row)
        last_updated_at = _latest_timestamp(
            market_row.snapshot_time if market_row is not None else None,
            decision_row.created_at if decision_row is not None else None,
            risk_row.created_at if risk_row is not None else None,
            order_row.created_at if order_row is not None else None,
            execution_row.created_at if execution_row is not None else None,
            position_row.created_at if position_row is not None else None,
        )
        decision_snapshot = _build_decision_snapshot(decision_row)
        decision_snapshot = decision_snapshot.model_copy(
            update={
                "decision_reference": _annotate_decision_reference(
                    decision_snapshot.decision_reference,
                    current_market_refresh_at=market_row.snapshot_time if market_row is not None else None,
                    current_sync_freshness_summary=overview.sync_freshness_summary,
                )
            }
        )
        summaries.append(
            OperatorSymbolSummary(
                symbol=symbol_key,
                timeframe=_decision_timeframe(decision_row) or (market_row.timeframe if market_row is not None else None),
                latest_price=market_row.latest_price if market_row is not None else None,
                market_snapshot_time=market_row.snapshot_time if market_row is not None else None,
                market_context_summary=_extract_symbol_market_context(decision_row, market_row),
                ai_decision=decision_snapshot,
                risk_guard=_build_risk_snapshot(risk_row),
                execution=_build_execution_snapshot_from_rows(order_row, execution_row, decision_row),
                open_position=_build_position_snapshot(position_row),
                protection_status=_build_protection_snapshot(protection_state),
                blocked_reasons=_risk_reason_codes_from_row(risk_row),
                live_execution_ready=overview.live_execution_ready and len(stale_flags) == 0,
                stale_flags=stale_flags,
                last_updated_at=last_updated_at,
                audit_events=audit_entries_by_symbol.get(symbol_key, []),
            )
        )
    return summaries


def get_operator_dashboard(session: Session) -> OperatorDashboardResponse:
    overview = get_overview(session)
    profitability = get_profitability_dashboard(session)
    latest_scheduler = session.scalar(select(SchedulerRun).order_by(desc(SchedulerRun.created_at)).limit(1))
    symbol_summaries = _build_operator_symbol_summaries(
        session,
        tracked_symbols=overview.tracked_symbols,
        overview=overview,
    )
    audit_rows = get_audit_timeline(session, limit=6)
    return OperatorDashboardResponse(
        generated_at=utcnow_naive(),
        control=OperatorControlState(
            generated_at=utcnow_naive(),
            operational_status=overview.operational_status,
            can_enter_new_position=overview.operational_status.can_enter_new_position,
            mode=overview.mode,
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
            auto_resume_last_blockers=overview.operational_status.auto_resume_last_blockers,
            latest_blocked_reasons=overview.operational_status.latest_blocked_reasons,
            market_freshness_summary=overview.operational_status.market_freshness_summary,
            sync_freshness_summary=overview.operational_status.sync_freshness_summary,
            protection_recovery_status=overview.operational_status.protection_recovery_status,
            protected_positions=overview.protected_positions,
            unprotected_positions=overview.unprotected_positions,
            open_positions=overview.open_positions,
            daily_pnl=overview.daily_pnl,
            cumulative_pnl=overview.cumulative_pnl,
            account_sync_summary=overview.operational_status.account_sync_summary,
            exposure_summary=overview.exposure_summary,
            scheduler_status=latest_scheduler.status if latest_scheduler is not None else None,
            scheduler_window=latest_scheduler.schedule_window if latest_scheduler is not None else None,
            scheduler_triggered_by=latest_scheduler.triggered_by if latest_scheduler is not None else None,
            scheduler_last_run_at=latest_scheduler.created_at if latest_scheduler is not None else None,
            scheduler_next_run_at=latest_scheduler.next_run_at if latest_scheduler is not None else None,
            last_market_refresh_at=overview.last_market_refresh_at,
            last_decision_at=overview.last_decision_at,
            last_decision_snapshot_at=overview.last_decision_snapshot_at,
            last_decision_reference=overview.last_decision_reference,
        ),
        symbols=symbol_summaries,
        market_signal=OperatorMarketSignalSummary(
            market_context_summary=overview.market_context_summary,
            performance_windows=profitability.windows,
            hold_blocked_summary=profitability.hold_blocked_summary,
            adaptive_signal_summary=profitability.adaptive_signal_summary,
        ),
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


def get_alerts(session: Session, limit: int = 50) -> list[dict[str, object]]:
    return _serialize_model_list(list(session.scalars(select(Alert).order_by(desc(Alert.created_at)).limit(limit))))
