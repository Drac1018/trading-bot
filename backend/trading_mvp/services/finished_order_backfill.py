from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import Select, create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from trading_mvp.config import get_settings
from trading_mvp.models import Order
from trading_mvp.services.audit import record_audit_event

LEGACY_LOCAL_STATUS = "finished"
NORMALIZED_LOCAL_STATUS = "expired"
TARGET_EXCHANGE_STATUS = "FINISHED"
TARGET_PROTECTIVE_ORDER_TYPES = (
    "stop_market",
    "take_profit_market",
    "stop",
    "take_profit",
    "trailing_stop_market",
)
TARGET_MATCH_CONDITIONS = (
    "lower(status) = 'finished'",
    "upper(exchange_status) = 'FINISHED'",
    "lower(order_type) in protective algo order types",
    "parent_order_id is not null",
    "mode = 'live'",
    "reduce_only = true",
    "close_only = true",
    "filled_quantity = 0",
    "average_fill_price = 0",
)


@dataclass(frozen=True)
class FinishedOrderBackfillCandidate:
    order_id: int
    symbol: str
    order_type: str
    parent_order_id: int
    external_order_id: str | None
    requested_quantity: float
    filled_quantity: float
    average_fill_price: float


@dataclass(frozen=True)
class FinishedOrderBackfillPlan:
    candidates: tuple[FinishedOrderBackfillCandidate, ...]
    match_conditions: tuple[str, ...]
    update_sql: str
    rollback_sql: str

    @property
    def count(self) -> int:
        return len(self.candidates)

    @property
    def order_ids(self) -> tuple[int, ...]:
        return tuple(candidate.order_id for candidate in self.candidates)


@dataclass(frozen=True)
class FinishedOrderBackfillApplyResult:
    plan: FinishedOrderBackfillPlan
    updated_count: int
    batch_id: str | None
    audit_event_id: int | None


def _target_orders_query() -> Select[tuple[Order]]:
    return (
        select(Order)
        .where(
            func.lower(Order.status) == LEGACY_LOCAL_STATUS,
            func.upper(func.coalesce(Order.exchange_status, "")) == TARGET_EXCHANGE_STATUS,
            func.lower(Order.order_type).in_(TARGET_PROTECTIVE_ORDER_TYPES),
            Order.parent_order_id.is_not(None),
            Order.mode == "live",
            Order.reduce_only.is_(True),
            Order.close_only.is_(True),
            func.coalesce(Order.filled_quantity, 0.0) == 0.0,
            func.coalesce(Order.average_fill_price, 0.0) == 0.0,
        )
        .order_by(Order.id)
    )


def _candidate_from_row(row: Order) -> FinishedOrderBackfillCandidate:
    parent_order_id = row.parent_order_id
    if parent_order_id is None:
        raise ValueError("Backfill candidate requires parent_order_id.")
    return FinishedOrderBackfillCandidate(
        order_id=row.id,
        symbol=row.symbol,
        order_type=row.order_type,
        parent_order_id=parent_order_id,
        external_order_id=row.external_order_id,
        requested_quantity=row.requested_quantity,
        filled_quantity=row.filled_quantity,
        average_fill_price=row.average_fill_price,
    )


def _format_sql_id_list(order_ids: Sequence[int]) -> str:
    return ", ".join(str(order_id) for order_id in order_ids)


def _build_update_sql(order_ids: Sequence[int]) -> str:
    if not order_ids:
        return "-- no matching legacy protective orders"
    id_list = _format_sql_id_list(order_ids)
    return f"""BEGIN;
UPDATE orders
SET status = '{NORMALIZED_LOCAL_STATUS}'
WHERE id IN ({id_list})
  AND lower(status) = '{LEGACY_LOCAL_STATUS}'
  AND upper(coalesce(exchange_status, '')) = '{TARGET_EXCHANGE_STATUS}'
  AND lower(order_type) IN ('stop_market', 'take_profit_market', 'stop', 'take_profit', 'trailing_stop_market')
  AND parent_order_id IS NOT NULL
  AND mode = 'live'
  AND coalesce(reduce_only, false) = true
  AND coalesce(close_only, false) = true
  AND coalesce(filled_quantity, 0) = 0
  AND coalesce(average_fill_price, 0) = 0;
COMMIT;"""


def _build_rollback_sql(order_ids: Sequence[int]) -> str:
    if not order_ids:
        return "-- no matching legacy protective orders"
    id_list = _format_sql_id_list(order_ids)
    return f"""BEGIN;
UPDATE orders
SET status = '{LEGACY_LOCAL_STATUS}'
WHERE id IN ({id_list})
  AND status = '{NORMALIZED_LOCAL_STATUS}'
  AND upper(coalesce(exchange_status, '')) = '{TARGET_EXCHANGE_STATUS}';
COMMIT;"""


def build_finished_order_backfill_plan(session: Session) -> FinishedOrderBackfillPlan:
    rows = session.scalars(_target_orders_query()).all()
    candidates = tuple(_candidate_from_row(row) for row in rows)
    return FinishedOrderBackfillPlan(
        candidates=candidates,
        match_conditions=TARGET_MATCH_CONDITIONS,
        update_sql=_build_update_sql([candidate.order_id for candidate in candidates]),
        rollback_sql=_build_rollback_sql([candidate.order_id for candidate in candidates]),
    )


def apply_finished_order_backfill(session: Session) -> FinishedOrderBackfillApplyResult:
    rows = session.scalars(_target_orders_query()).all()
    plan = build_finished_order_backfill_plan(session)
    if not rows:
        return FinishedOrderBackfillApplyResult(plan=plan, updated_count=0, batch_id=None, audit_event_id=None)

    batch_id = f"finished-order-backfill-{uuid4().hex[:12]}"
    for row in rows:
        row.status = NORMALIZED_LOCAL_STATUS
        session.add(row)

    audit_event = record_audit_event(
        session,
        event_type="maintenance_finished_order_status_backfill_applied",
        entity_type="maintenance",
        entity_id=batch_id,
        message="Backfilled legacy protective order statuses from finished to expired.",
        payload={
            "operation": "legacy_protective_order_status_backfill",
            "before_status": LEGACY_LOCAL_STATUS,
            "after_status": NORMALIZED_LOCAL_STATUS,
            "exchange_status": TARGET_EXCHANGE_STATUS,
            "updated_count": plan.count,
            "updated_order_ids": list(plan.order_ids),
            "match_conditions": list(plan.match_conditions),
            "rollback_sql": plan.rollback_sql,
        },
    )
    session.flush()
    audit_event_id = audit_event.id
    session.commit()
    return FinishedOrderBackfillApplyResult(
        plan=plan,
        updated_count=plan.count,
        batch_id=batch_id,
        audit_event_id=audit_event_id,
    )


def _build_session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    resolved_database_url = database_url or get_settings().database_url
    engine_kwargs: dict[str, object] = {"future": True}
    if resolved_database_url.startswith("sqlite"):
        engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(resolved_database_url, **engine_kwargs)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _print_plan(plan: FinishedOrderBackfillPlan) -> None:
    print("Target conditions:")
    for condition in plan.match_conditions:
        print(f"  - {condition}")
    print(f"Dry-run count: {plan.count}")
    if not plan.candidates:
        print("Matched rows: none")
    else:
        print("Matched rows:")
        for candidate in plan.candidates:
            print(
                "  - "
                f"id={candidate.order_id} symbol={candidate.symbol} order_type={candidate.order_type} "
                f"parent_order_id={candidate.parent_order_id} external_order_id={candidate.external_order_id!r}"
            )
    print("Update SQL:")
    print(plan.update_sql)
    print("Rollback SQL:")
    print(plan.rollback_sql)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dry-run and apply backfill for legacy protective orders stuck at status='finished'."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the backfill. Without this flag the command runs in dry-run mode only.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Optional database URL override. Defaults to trading_mvp settings.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    session_factory = _build_session_factory(args.database_url)
    with session_factory() as session:
        plan = build_finished_order_backfill_plan(session)
        _print_plan(plan)
    if not args.apply:
        print("Dry-run only. Re-run with --apply to persist the backfill.")
        return 0
    with session_factory() as session:
        result = apply_finished_order_backfill(session)
    if result.updated_count == 0:
        print("Apply complete. No matching rows required changes.")
        return 0
    print(
        "Apply complete. "
        f"updated_count={result.updated_count} batch_id={result.batch_id} audit_event_id={result.audit_event_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
