from __future__ import annotations

from datetime import date

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from trading_mvp.models import PnLSnapshot, Position, Setting


def _to_float(value: object, default: float = 0.0) -> float:
    if value in {None, ""}:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value))


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


def get_latest_pnl_snapshot(session: Session, settings_row: Setting) -> PnLSnapshot:
    statement = select(PnLSnapshot).order_by(desc(PnLSnapshot.created_at)).limit(1)
    row = session.scalar(statement)
    if row is not None:
        return row
    row = PnLSnapshot(
        snapshot_date=date.today(),
        equity=settings_row.starting_equity,
        cash_balance=settings_row.starting_equity,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        cumulative_pnl=0.0,
        consecutive_losses=0,
    )
    session.add(row)
    session.flush()
    return row


def create_pnl_snapshot(
    session: Session,
    settings_row: Setting,
    unrealized_pnl: float = 0.0,
    realized_delta: float = 0.0,
    trade_closed: bool = False,
) -> PnLSnapshot:
    previous = get_latest_pnl_snapshot(session, settings_row)
    previous_daily = previous.daily_pnl if previous.snapshot_date == date.today() else 0.0
    previous_cumulative = previous.cumulative_pnl
    consecutive_losses = previous.consecutive_losses
    if trade_closed:
        if realized_delta < 0:
            consecutive_losses += 1
        elif realized_delta > 0:
            consecutive_losses = 0

    cumulative_pnl = previous_cumulative + realized_delta
    daily_pnl = previous_daily + realized_delta
    equity = settings_row.starting_equity + cumulative_pnl + unrealized_pnl
    row = PnLSnapshot(
        snapshot_date=date.today(),
        equity=equity,
        cash_balance=settings_row.starting_equity + cumulative_pnl,
        realized_pnl=realized_delta,
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
    account_info: dict[str, object],
) -> PnLSnapshot:
    previous = get_latest_pnl_snapshot(session, settings_row)
    total_wallet_balance = _to_float(account_info.get("totalWalletBalance"))
    available_balance = _to_float(account_info.get("availableBalance"))
    unrealized_pnl = _to_float(account_info.get("totalUnrealizedProfit"))
    total_margin_balance = _to_float(account_info.get("totalMarginBalance"))

    equity = total_margin_balance if total_margin_balance > 0 else total_wallet_balance + unrealized_pnl
    if equity <= 0:
        equity = previous.equity
    cash_balance = available_balance if available_balance > 0 else total_wallet_balance
    if cash_balance <= 0:
        cash_balance = previous.cash_balance

    row = PnLSnapshot(
        snapshot_date=date.today(),
        equity=equity,
        cash_balance=cash_balance,
        realized_pnl=previous.realized_pnl if previous.snapshot_date == date.today() else 0.0,
        unrealized_pnl=unrealized_pnl,
        daily_pnl=previous.daily_pnl if previous.snapshot_date == date.today() else 0.0,
        cumulative_pnl=previous.cumulative_pnl,
        consecutive_losses=previous.consecutive_losses,
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
