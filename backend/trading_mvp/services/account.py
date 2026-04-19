from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from trading_mvp.models import AccountLedgerEntry, Execution, Order, PnLSnapshot, Position, Setting
from trading_mvp.services.binance import BinanceClient
from trading_mvp.time_utils import utcnow_naive


def _to_float(value: object, default: float = 0.0) -> float:
    if value in {None, ""}:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value))


def _to_datetime_ms(value: object) -> datetime | None:
    if value in {None, ""}:
        return None
    try:
        timestamp_ms = int(float(value))
    except (TypeError, ValueError):
        return None
    if timestamp_ms <= 0:
        return None
    return datetime.fromtimestamp(timestamp_ms / 1000.0)


@dataclass(slots=True)
class LivePnLComponents:
    snapshot_date: date
    has_live_activity: bool
    last_execution_at: datetime | None
    gross_realized_pnl: float
    fee_total: float
    funding_total: float
    net_pnl: float
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
    net_pnl: float,
    unrealized_pnl: float,
) -> dict[str, float]:
    if previous is not None:
        previous_net_pnl = float(previous.net_pnl or previous.cumulative_pnl)
        cumulative_delta = net_pnl - previous_net_pnl
        wallet_balance = (previous.wallet_balance or previous.cash_balance) + cumulative_delta
        available_balance = (previous.available_balance or previous.cash_balance) + cumulative_delta
    else:
        wallet_balance = 0.0
        available_balance = 0.0
    cash_balance = available_balance
    equity = wallet_balance + unrealized_pnl
    return {
        "equity": equity,
        "cash_balance": cash_balance,
        "wallet_balance": wallet_balance,
        "available_balance": available_balance,
    }


def _current_unrealized_pnl(session: Session) -> float:
    return sum(position.unrealized_pnl for position in get_open_positions(session))


def is_placeholder_live_snapshot(row: PnLSnapshot | None) -> bool:
    return row is not None and row.id is None


def _build_placeholder_live_snapshot(
    *,
    components: LivePnLComponents,
    unrealized_pnl: float,
) -> PnLSnapshot:
    now = utcnow_naive()
    return PnLSnapshot(
        snapshot_date=components.snapshot_date,
        equity=0.0,
        cash_balance=0.0,
        wallet_balance=0.0,
        available_balance=0.0,
        gross_realized_pnl=components.gross_realized_pnl,
        fee_total=components.fee_total,
        funding_total=components.funding_total,
        net_pnl=components.net_pnl,
        realized_pnl=components.net_pnl,
        unrealized_pnl=unrealized_pnl,
        daily_pnl=components.daily_net_pnl,
        cumulative_pnl=components.net_pnl,
        consecutive_losses=components.consecutive_losses,
        created_at=now,
        updated_at=now,
    )


def _normalize_funding_external_ref(entry: Mapping[str, object]) -> str | None:
    for key in ("tranId", "transactionId", "id", "tradeId"):
        value = entry.get(key)
        if value not in {None, ""}:
            return str(value)
    return None


def _funding_entry_exists(
    session: Session,
    *,
    external_ref_id: str | None,
    occurred_at: datetime,
    asset: str,
    symbol: str | None,
    amount: float,
) -> bool:
    statement = select(AccountLedgerEntry.id).where(AccountLedgerEntry.entry_type == "funding").limit(1)
    if external_ref_id:
        statement = statement.where(AccountLedgerEntry.external_ref_id == external_ref_id)
    else:
        statement = statement.where(
            AccountLedgerEntry.occurred_at == occurred_at,
            AccountLedgerEntry.asset == asset,
            AccountLedgerEntry.symbol == symbol,
            AccountLedgerEntry.amount == amount,
        )
    return session.scalar(statement) is not None


def record_funding_ledger_entries(
    session: Session,
    funding_entries: Sequence[Mapping[str, object] | dict[str, object]] | None,
) -> dict[str, object]:
    inserted = 0
    inserted_amount = 0.0
    last_occurred_at: datetime | None = None
    for raw_entry in funding_entries or []:
        if not isinstance(raw_entry, Mapping):
            continue
        amount = _to_float(raw_entry.get("income", raw_entry.get("amount")))
        asset = str(raw_entry.get("asset") or raw_entry.get("incomeAsset") or "USDT").upper()
        symbol_value = str(raw_entry.get("symbol") or "").upper()
        symbol = symbol_value or None
        occurred_at = _to_datetime_ms(raw_entry.get("time")) or utcnow_naive()
        external_ref_id = _normalize_funding_external_ref(raw_entry)
        if _funding_entry_exists(
            session,
            external_ref_id=external_ref_id,
            occurred_at=occurred_at,
            asset=asset,
            symbol=symbol,
            amount=amount,
        ):
            continue
        session.add(
            AccountLedgerEntry(
                entry_type="funding",
                asset=asset,
                symbol=symbol,
                amount=amount,
                external_ref_id=external_ref_id,
                occurred_at=occurred_at,
                payload=dict(raw_entry),
            )
        )
        inserted += 1
        inserted_amount += amount
        if last_occurred_at is None or occurred_at > last_occurred_at:
            last_occurred_at = occurred_at
    if inserted:
        session.flush()
    return {
        "inserted_count": inserted,
        "inserted_amount": inserted_amount,
        "last_occurred_at": last_occurred_at,
    }


def fetch_incremental_funding_entries(
    session: Session,
    client: BinanceClient,
    *,
    limit: int = 100,
) -> list[dict[str, object]]:
    latest_entry = session.scalar(
        select(AccountLedgerEntry)
        .where(AccountLedgerEntry.entry_type == "funding")
        .order_by(desc(AccountLedgerEntry.occurred_at))
        .limit(1)
    )
    start_time_ms: int | None = None
    if latest_entry is not None:
        start_time_ms = max(int(latest_entry.occurred_at.timestamp() * 1000) - 1000, 0)
    return client.get_income_history(income_type="FUNDING_FEE", start_time=start_time_ms, limit=limit)


def _build_live_pnl_components(session: Session, *, snapshot_date: date | None = None) -> LivePnLComponents:
    effective_date = snapshot_date or utcnow_naive().date()
    gross_realized_pnl = 0.0
    fee_total = 0.0
    daily_net_pnl = 0.0
    last_execution_at: datetime | None = None
    has_live_activity = False
    close_order_outcomes: dict[int, CloseOrderOutcome] = {}

    statement = (
        select(Execution, Order)
        .join(Order, Execution.order_id == Order.id, isouter=True)
        .order_by(Execution.created_at.asc(), Execution.id.asc())
    )
    for execution_row, order_row in session.execute(statement):
        if order_row is not None and order_row.mode != "live":
            continue

        has_live_activity = True
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

    funding_total = 0.0
    daily_funding_total = 0.0
    for ledger_entry in session.scalars(
        select(AccountLedgerEntry)
        .where(AccountLedgerEntry.entry_type == "funding")
        .order_by(AccountLedgerEntry.occurred_at.asc(), AccountLedgerEntry.id.asc())
    ):
        has_live_activity = True
        funding_total += ledger_entry.amount
        if ledger_entry.occurred_at.date() == effective_date:
            daily_funding_total += ledger_entry.amount

    net_pnl = gross_realized_pnl - fee_total + funding_total
    return LivePnLComponents(
        snapshot_date=effective_date,
        has_live_activity=has_live_activity,
        last_execution_at=last_execution_at,
        gross_realized_pnl=gross_realized_pnl,
        fee_total=fee_total,
        funding_total=funding_total,
        net_pnl=net_pnl,
        daily_net_pnl=daily_net_pnl + daily_funding_total,
        consecutive_losses=consecutive_losses,
    )


def _snapshot_matches_components(row: PnLSnapshot, components: LivePnLComponents, *, tolerance: float = 1e-9) -> bool:
    return (
        abs((row.gross_realized_pnl or 0.0) - components.gross_realized_pnl) <= tolerance
        and abs((row.fee_total or 0.0) - components.fee_total) <= tolerance
        and abs((row.funding_total or 0.0) - components.funding_total) <= tolerance
        and abs((row.net_pnl or row.cumulative_pnl) - components.net_pnl) <= tolerance
        and abs(row.realized_pnl - components.net_pnl) <= tolerance
        and abs(row.daily_pnl - components.daily_net_pnl) <= tolerance
        and abs(row.cumulative_pnl - components.net_pnl) <= tolerance
        and row.consecutive_losses == components.consecutive_losses
    )


def get_latest_pnl_snapshot(session: Session, settings_row: Setting) -> PnLSnapshot:
    row = _latest_snapshot_row(session)
    if row is None:
        return create_exchange_pnl_snapshot(session, settings_row)

    effective_date = utcnow_naive().date()
    if row.snapshot_date != effective_date:
        return create_exchange_pnl_snapshot(session, settings_row)

    components = _build_live_pnl_components(session, snapshot_date=effective_date)
    if not components.has_live_activity:
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
    effective_date = utcnow_naive().date()
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
        cash_balance = cumulative_pnl
    equity = cash_balance + unrealized_pnl
    row = PnLSnapshot(
        snapshot_date=effective_date,
        equity=equity,
        cash_balance=cash_balance,
        wallet_balance=cash_balance,
        available_balance=cash_balance,
        gross_realized_pnl=cumulative_pnl,
        fee_total=0.0,
        funding_total=0.0,
        net_pnl=cumulative_pnl,
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
    *,
    funding_entries: Sequence[Mapping[str, object] | dict[str, object]] | None = None,
) -> PnLSnapshot:
    if funding_entries:
        record_funding_ledger_entries(session, funding_entries)
    previous = _latest_snapshot_row(session)
    components = _build_live_pnl_components(session, snapshot_date=utcnow_naive().date())
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

    if previous is None and account_info is None:
        return _build_placeholder_live_snapshot(
            components=components,
            unrealized_pnl=unrealized_pnl,
        )

    fallback_balances = _fallback_balance_state(
        settings_row=settings_row,
        previous=previous,
        net_pnl=components.net_pnl,
        unrealized_pnl=unrealized_pnl,
    )

    equity = total_margin_balance if total_margin_balance > 0 else total_wallet_balance + unrealized_pnl
    if equity <= 0:
        equity = fallback_balances["equity"]

    wallet_balance = total_wallet_balance if total_wallet_balance > 0 else fallback_balances["wallet_balance"]
    normalized_available_balance = available_balance if available_balance > 0 else fallback_balances["available_balance"]
    cash_balance = normalized_available_balance if normalized_available_balance > 0 else total_wallet_balance
    if cash_balance <= 0:
        cash_balance = fallback_balances["cash_balance"]
    if wallet_balance <= 0:
        wallet_balance = fallback_balances["wallet_balance"]
    if normalized_available_balance <= 0:
        normalized_available_balance = fallback_balances["available_balance"]

    row = PnLSnapshot(
        snapshot_date=components.snapshot_date,
        equity=equity,
        cash_balance=cash_balance,
        wallet_balance=wallet_balance,
        available_balance=normalized_available_balance,
        gross_realized_pnl=components.gross_realized_pnl,
        fee_total=components.fee_total,
        funding_total=components.funding_total,
        net_pnl=components.net_pnl,
        realized_pnl=components.net_pnl,
        unrealized_pnl=unrealized_pnl,
        daily_pnl=components.daily_net_pnl,
        cumulative_pnl=components.net_pnl,
        consecutive_losses=components.consecutive_losses,
    )
    session.add(row)
    session.flush()
    return row


def account_snapshot_to_dict(pnl: PnLSnapshot) -> dict[str, float | int | str | None]:
    if is_placeholder_live_snapshot(pnl):
        return {
            "snapshot_date": pnl.snapshot_date.isoformat(),
            "account_snapshot_available": False,
            "basis": "live_account_snapshot_unavailable",
            "note": "Live account snapshot is unavailable until the first successful exchange account sync.",
            "equity": None,
            "cash_balance": None,
            "wallet_balance": None,
            "available_balance": None,
            "gross_realized_pnl": pnl.gross_realized_pnl,
            "fee_total": pnl.fee_total,
            "funding_total": pnl.funding_total,
            "net_pnl": pnl.net_pnl,
            "realized_pnl": pnl.realized_pnl,
            "unrealized_pnl": pnl.unrealized_pnl,
            "daily_pnl": pnl.daily_pnl,
            "cumulative_pnl": pnl.cumulative_pnl,
            "consecutive_losses": pnl.consecutive_losses,
        }
    return {
        "snapshot_date": pnl.snapshot_date.isoformat(),
        "account_snapshot_available": True,
        "equity": pnl.equity,
        "cash_balance": pnl.cash_balance,
        "wallet_balance": pnl.wallet_balance,
        "available_balance": pnl.available_balance,
        "gross_realized_pnl": pnl.gross_realized_pnl,
        "fee_total": pnl.fee_total,
        "funding_total": pnl.funding_total,
        "net_pnl": pnl.net_pnl,
        "realized_pnl": pnl.realized_pnl,
        "unrealized_pnl": pnl.unrealized_pnl,
        "daily_pnl": pnl.daily_pnl,
        "cumulative_pnl": pnl.cumulative_pnl,
        "consecutive_losses": pnl.consecutive_losses,
    }
