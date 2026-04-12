from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from trading_mvp.models import Execution, Order, PnLSnapshot, Position, Setting


def _to_float(value: object, default: float = 0.0) -> float:
    if value in {None, ""}:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value))


@dataclass(slots=True)
class LivePnLComponents:
    snapshot_date: date
    has_live_executions: bool
    last_execution_at: datetime | None
    gross_realized_pnl: float
    fee_total: float
    net_realized_pnl: float
    daily_net_pnl: float
    consecutive_losses: int


@dataclass(slots=True)
class CloseOrderOutcome:
    order_id: int
    last_fill_at: datetime
    net_pnl: float = 0.0


def get_open_positions(session: Session, symbol: str | None = None) -> list[Position]:
    statement = (
        select(Position)
        .where(Position.status == "open", Position.mode == "live")
        .order_by(desc(Position.opened_at))
    )
    if symbol is not None:
        statement = statement.where(Position.symbol == symbol)
    return list(session.scalars(statement))


def get_open_position(session: Session, symbol: str) -> Position | None:
    statement = (
        select(Position)
        .where(Position.symbol == symbol, Position.status == "open", Position.mode == "live")
        .order_by(desc(Position.opened_at))
        .limit(1)
    )
    return session.scalar(statement)


def calculate_unrealized_pnl(position: Position, mark_price: float) -> float:
    if position.side == "long":
        return (mark_price - position.entry_price) * position.quantity
    return (position.entry_price - mark_price) * position.quantity


def refresh_open_position_marks(session: Session, symbol_prices: dict[str, float]) -> float:
    unrealized_total = 0.0
    for position in get_open_positions(session):
        mark_price = symbol_prices.get(position.symbol, position.mark_price)
        position.mark_price = mark_price
        position.unrealized_pnl = calculate_unrealized_pnl(position, mark_price)
        session.add(position)
        unrealized_total += position.unrealized_pnl
    session.flush()
    return unrealized_total


def _latest_snapshot_row(session: Session) -> PnLSnapshot | None:
    statement = select(PnLSnapshot).order_by(desc(PnLSnapshot.created_at)).limit(1)
    return session.scalar(statement)


def _fallback_balance_state(
    *,
    settings_row: Setting,
    previous: PnLSnapshot | None,
    cumulative_pnl: float,
    unrealized_pnl: float,
) -> tuple[float, float]:
    if previous is not None:
        cumulative_delta = cumulative_pnl - previous.cumulative_pnl
        cash_balance = previous.cash_balance + cumulative_delta
    else:
        cash_balance = settings_row.starting_equity + cumulative_pnl
    equity = cash_balance + unrealized_pnl
    return equity, cash_balance


def _current_unrealized_pnl(session: Session) -> float:
    return sum(position.unrealized_pnl for position in get_open_positions(session))


def _build_live_pnl_components(session: Session, *, snapshot_date: date | None = None) -> LivePnLComponents:
    effective_date = snapshot_date or date.today()
    gross_realized_pnl = 0.0
    fee_total = 0.0
    daily_net_pnl = 0.0
    last_execution_at: datetime | None = None
    has_live_executions = False
    close_order_outcomes: dict[int, CloseOrderOutcome] = {}

    statement = (
        select(Execution, Order)
        .join(Order, Execution.order_id == Order.id, isouter=True)
        .order_by(Execution.created_at.asc(), Execution.id.asc())
    )
    for execution_row, order_row in session.execute(statement):
        if order_row is not None and order_row.mode != "live":
            continue

        has_live_executions = True
        if last_execution_at is None or execution_row.created_at > last_execution_at:
            last_execution_at = execution_row.created_at

        gross_realized_pnl += execution_row.realized_pnl
        fee_total += execution_row.fee_paid
        net_realized = execution_row.realized_pnl - execution_row.fee_paid
        if execution_row.created_at.date() == effective_date:
            daily_net_pnl += net_realized

        if order_row is None or not (order_row.reduce_only or order_row.close_only):
            continue
        order_bucket = close_order_outcomes.setdefault(
            order_row.id,
            CloseOrderOutcome(order_id=order_row.id, last_fill_at=execution_row.created_at),
        )
        order_bucket.net_pnl += net_realized
        if execution_row.created_at > order_bucket.last_fill_at:
            order_bucket.last_fill_at = execution_row.created_at

    consecutive_losses = 0
    ordered_close_outcomes = sorted(
        close_order_outcomes.values(),
        key=lambda item: (item.last_fill_at, item.order_id),
    )
    for outcome in ordered_close_outcomes:
        net_pnl = outcome.net_pnl
        if net_pnl < 0:
            consecutive_losses += 1
        elif net_pnl > 0:
            consecutive_losses = 0

    net_realized_pnl = gross_realized_pnl - fee_total
    return LivePnLComponents(
        snapshot_date=effective_date,
        has_live_executions=has_live_executions,
        last_execution_at=last_execution_at,
        gross_realized_pnl=gross_realized_pnl,
        fee_total=fee_total,
        net_realized_pnl=net_realized_pnl,
        daily_net_pnl=daily_net_pnl,
        consecutive_losses=consecutive_losses,
    )


def _snapshot_matches_components(row: PnLSnapshot, components: LivePnLComponents, *, tolerance: float = 1e-9) -> bool:
    return (
        abs(row.realized_pnl - components.net_realized_pnl) <= tolerance
        and abs(row.daily_pnl - components.daily_net_pnl) <= tolerance
        and abs(row.cumulative_pnl - components.net_realized_pnl) <= tolerance
        and row.consecutive_losses == components.consecutive_losses
    )


def get_latest_pnl_snapshot(session: Session, settings_row: Setting) -> PnLSnapshot:
    row = _latest_snapshot_row(session)
    if row is None:
        return create_exchange_pnl_snapshot(session, settings_row)

    effective_date = date.today()
    if row.snapshot_date != effective_date:
        return create_exchange_pnl_snapshot(session, settings_row)

    components = _build_live_pnl_components(session, snapshot_date=effective_date)
    if not components.has_live_executions:
        return row

    if _snapshot_matches_components(row, components):
        return row

    return create_exchange_pnl_snapshot(session, settings_row)


def create_pnl_snapshot(
    session: Session,
    settings_row: Setting,
    unrealized_pnl: float = 0.0,
    realized_delta: float = 0.0,
    trade_closed: bool = False,
) -> PnLSnapshot:
    previous = _latest_snapshot_row(session)
    effective_date = date.today()
    previous_daily = previous.daily_pnl if previous is not None and previous.snapshot_date == effective_date else 0.0
    previous_cumulative = previous.cumulative_pnl if previous is not None else 0.0
    consecutive_losses = previous.consecutive_losses if previous is not None else 0
    if trade_closed:
        if realized_delta < 0:
            consecutive_losses += 1
        elif realized_delta > 0:
            consecutive_losses = 0

    cumulative_pnl = previous_cumulative + realized_delta
    daily_pnl = previous_daily + realized_delta
    if previous is not None:
        cash_balance = previous.cash_balance + realized_delta
    else:
        cash_balance = settings_row.starting_equity + cumulative_pnl
    equity = cash_balance + unrealized_pnl
    row = PnLSnapshot(
        snapshot_date=effective_date,
        equity=equity,
        cash_balance=cash_balance,
        realized_pnl=cumulative_pnl,
        unrealized_pnl=unrealized_pnl,
        daily_pnl=daily_pnl,
        cumulative_pnl=cumulative_pnl,
        consecutive_losses=consecutive_losses,
    )
    session.add(row)
    session.flush()
    return row


def create_exchange_pnl_snapshot(
    session: Session,
    settings_row: Setting,
    account_info: dict[str, object] | None = None,
) -> PnLSnapshot:
    previous = _latest_snapshot_row(session)
    components = _build_live_pnl_components(session, snapshot_date=date.today())
    if account_info is not None:
        total_wallet_balance = _to_float(account_info.get("totalWalletBalance"))
        available_balance = _to_float(account_info.get("availableBalance"))
        unrealized_pnl = _to_float(account_info.get("totalUnrealizedProfit"))
        total_margin_balance = _to_float(account_info.get("totalMarginBalance"))
    else:
        total_wallet_balance = 0.0
        available_balance = 0.0
        unrealized_pnl = _current_unrealized_pnl(session)
        total_margin_balance = 0.0

    fallback_equity, fallback_cash_balance = _fallback_balance_state(
        settings_row=settings_row,
        previous=previous,
        cumulative_pnl=components.net_realized_pnl,
        unrealized_pnl=unrealized_pnl,
    )

    equity = total_margin_balance if total_margin_balance > 0 else total_wallet_balance + unrealized_pnl
    if equity <= 0:
        equity = fallback_equity

    cash_balance = available_balance if available_balance > 0 else total_wallet_balance
    if cash_balance <= 0:
        cash_balance = fallback_cash_balance

    row = PnLSnapshot(
        snapshot_date=components.snapshot_date,
        equity=equity,
        cash_balance=cash_balance,
        realized_pnl=components.net_realized_pnl,
        unrealized_pnl=unrealized_pnl,
        daily_pnl=components.daily_net_pnl,
        cumulative_pnl=components.net_realized_pnl,
        consecutive_losses=components.consecutive_losses,
    )
    session.add(row)
    session.flush()
    return row


def account_snapshot_to_dict(pnl: PnLSnapshot) -> dict[str, float | int | str]:
    return {
        "snapshot_date": pnl.snapshot_date.isoformat(),
        "equity": pnl.equity,
        "cash_balance": pnl.cash_balance,
        "realized_pnl": pnl.realized_pnl,
        "unrealized_pnl": pnl.unrealized_pnl,
        "daily_pnl": pnl.daily_pnl,
        "cumulative_pnl": pnl.cumulative_pnl,
        "consecutive_losses": pnl.consecutive_losses,
    }
