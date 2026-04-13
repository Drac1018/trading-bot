from __future__ import annotations

from collections.abc import Sequence
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
from trading_mvp.schemas import OverviewResponse
from trading_mvp.services.runtime_state import PROTECTION_REQUIRED_STATE, summarize_runtime_state
from trading_mvp.services.settings import (
    get_effective_symbols,
    get_or_create_settings,
    is_live_execution_ready,
    serialize_settings,
)

FINAL_ORDER_STATUSES = {"filled", "canceled", "cancelled", "rejected", "expired"}
PROTECTIVE_ORDER_TYPES = {"stop_market", "take_profit_market"}


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


def _as_dict(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


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
    serialized_missing_symbols = _as_string_list(settings_payload.get("missing_protection_symbols", []))
    runtime_missing_items = _as_missing_items(runtime_state["missing_protection_items"])
    pnl_summary = _as_dict(settings_payload.get("pnl_summary", {}))
    account_sync_summary = _as_dict(settings_payload.get("account_sync_summary", {}))
    exposure_summary = _as_dict(settings_payload.get("exposure_summary", {}))
    execution_policy_summary = _as_dict(settings_payload.get("execution_policy_summary", {}))
    market_context_summary = _as_dict(settings_payload.get("market_context_summary", {}))
    adaptive_protection_summary = _as_dict(settings_payload.get("adaptive_protection_summary", {}))
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
        select(Execution, Order.mode)
        .outerjoin(Order, Order.id == Execution.order_id)
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
    for execution, order_mode in rows:
        values = {}
        for key in execution.__table__.columns:  # type: ignore[attr-defined]
            value = getattr(execution, key.name)
            values[key.name] = value.isoformat() if hasattr(value, "isoformat") else value
        values["mode"] = order_mode or "unknown"
        payloads.append(values)
    return payloads


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
    return _serialize_model_list(list(session.scalars(statement)))


def get_backlog(session: Session, limit: int = 50) -> list[dict[str, object]]:
    return _serialize_model_list(list(session.scalars(select(ProductBacklog).order_by(desc(ProductBacklog.created_at)).limit(limit))))


def get_alerts(session: Session, limit: int = 50) -> list[dict[str, object]]:
    return _serialize_model_list(list(session.scalars(select(Alert).order_by(desc(Alert.created_at)).limit(limit))))
