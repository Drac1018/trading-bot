from __future__ import annotations

from collections.abc import Sequence

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
from trading_mvp.services.settings import (
    get_effective_symbols,
    get_or_create_settings,
    is_live_execution_ready,
    serialize_settings,
)


def _serialize_model_list(rows: Sequence[object]) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for row in rows:
        values = {}
        for key in row.__table__.columns:  # type: ignore[attr-defined]
            value = getattr(row, key.name)
            values[key.name] = value.isoformat() if hasattr(value, "isoformat") else value
        payloads.append(values)
    return payloads


def get_overview(session: Session) -> OverviewResponse:
    settings_row = get_or_create_settings(session)
    latest_market = session.scalar(select(MarketSnapshot).order_by(desc(MarketSnapshot.snapshot_time)).limit(1))
    latest_decision = session.scalar(select(AgentRun).where(AgentRun.role == "trading_decision").order_by(desc(AgentRun.created_at)).limit(1))
    latest_risk = session.scalar(select(RiskCheck).order_by(desc(RiskCheck.created_at)).limit(1))
    latest_pnl = session.scalar(select(PnLSnapshot).order_by(desc(PnLSnapshot.created_at)).limit(1))
    open_positions = list(session.scalars(select(Position).where(Position.status == "open", Position.mode == "live")))
    blocked_reasons = latest_risk.reason_codes if latest_risk is not None and not latest_risk.allowed else []
    return OverviewResponse(
        mode=serialize_settings(settings_row)["mode"],  # type: ignore[arg-type]
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
        daily_pnl=latest_pnl.daily_pnl if latest_pnl is not None else 0.0,
        cumulative_pnl=latest_pnl.cumulative_pnl if latest_pnl is not None else 0.0,
        blocked_reasons=blocked_reasons,
    )


def get_market_snapshots(session: Session, limit: int = 50) -> list[dict[str, object]]:
    return _serialize_model_list(list(session.scalars(select(MarketSnapshot).order_by(desc(MarketSnapshot.snapshot_time)).limit(limit))))


def get_feature_snapshots(session: Session, limit: int = 50) -> list[dict[str, object]]:
    return _serialize_model_list(list(session.scalars(select(FeatureSnapshot).order_by(desc(FeatureSnapshot.feature_time)).limit(limit))))


def get_decisions(session: Session, limit: int = 50) -> list[dict[str, object]]:
    return _serialize_model_list(list(session.scalars(select(AgentRun).where(AgentRun.role == "trading_decision").order_by(desc(AgentRun.created_at)).limit(limit))))


def get_positions(session: Session, limit: int = 50) -> list[dict[str, object]]:
    return _serialize_model_list(list(session.scalars(select(Position).where(Position.mode == "live").order_by(desc(Position.created_at)).limit(limit))))


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
