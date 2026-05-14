from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from math import isfinite
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_mvp.models import AuditEvent, Execution, Order, Position
from trading_mvp.time_utils import utcnow_naive

DEFAULT_GROUP_BY = (
    "strategy_id",
    "regime_id",
    "confirmation_type",
    "risk_mode",
    "symbol",
    "direction",
)
SUPPORTED_GROUP_BY = DEFAULT_GROUP_BY + ("mode",)
NO_DATA_MESSAGE = "No closed trade records matched the filters."


@dataclass(slots=True)
class StrategyPerformanceFilters:
    strategy_id: str | None = None
    regime_id: str | None = None
    confirmation_type: str | None = None
    direction: str | None = None
    symbol: str | None = None
    risk_mode: str | None = None
    mode: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    limit: int | None = None


@dataclass(slots=True)
class StrategyTradeRecord:
    position_id: int
    symbol: str
    direction: str
    mode: str
    strategy_id: str
    regime_id: str
    confirmation_type: str
    risk_mode: str
    opened_at: datetime | None
    closed_at: datetime | None
    gross_pnl: float
    fee_total: float
    fee_adjusted_pnl: float
    r_multiple: float | None
    hold_minutes: float | None
    avg_slippage_bps: float | None


@dataclass(slots=True)
class StrategyPerformanceBucket:
    group: dict[str, str]
    trade_count: int
    win_rate: float | None
    avg_win: float | None
    avg_loss: float | None
    avg_R: float | None
    profit_factor: float | None
    max_drawdown: float
    max_loss_streak: int
    avg_hold_time: float | None
    avg_slippage_bps: float | None
    fee_adjusted_pnl: float
    gross_pnl: float
    fee_total: float


@dataclass(slots=True)
class StrategyPerformanceReport:
    generated_at: datetime
    filters: dict[str, object]
    group_by: list[str]
    trade_count: int
    buckets: list[StrategyPerformanceBucket] = field(default_factory=list)
    message: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "filters": self.filters,
            "group_by": self.group_by,
            "trade_count": self.trade_count,
            "message": self.message,
            "buckets": [asdict(bucket) for bucket in self.buckets],
        }


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _is_present(value: object) -> bool:
    if value is None:
        return False
    return not (isinstance(value, str) and not value.strip())


def _float(value: object, default: float = 0.0) -> float:
    if not _is_present(value):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_float(value: object) -> float | None:
    if not _is_present(value):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _text(value: object, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _normalize_filter(value: str | None, *, upper: bool = False) -> str | None:
    text = _text(value)
    if not text:
        return None
    return text.upper() if upper else text.lower()


def _metadata_tags(metadata: object) -> dict[str, Any]:
    payload = _as_dict(metadata)
    closed_pnl = _as_dict(payload.get("closed_position_pnl"))
    tags = {
        **_as_dict(payload.get("trade_performance_tags")),
        **_as_dict(closed_pnl.get("trade_performance_tags")),
    }
    direct_aliases = {
        "strategy_id": ("strategy_id", "strategy_engine"),
        "regime_id": ("regime_id",),
        "confirmation_type": ("confirmation_type", "entry_confirmation_type"),
        "risk_mode": ("risk_mode",),
        "symbol": ("symbol",),
        "direction": ("direction", "decision", "side"),
    }
    for canonical, aliases in direct_aliases.items():
        if _is_present(tags.get(canonical)):
            continue
        for alias in aliases:
            if _is_present(payload.get(alias)):
                tags[canonical] = payload[alias]
                break
            if _is_present(closed_pnl.get(alias)):
                tags[canonical] = closed_pnl[alias]
                break
    if not _is_present(tags.get("confirmation_type")) and _is_present(tags.get("entry_confirmation_type")):
        tags["confirmation_type"] = tags["entry_confirmation_type"]
    return tags


def _merge_tags(*payloads: object) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for payload in payloads:
        for key, value in _metadata_tags(payload).items():
            if _is_present(value) and not _is_present(merged.get(key)):
                merged[key] = value
    return merged


def _closed_pnl_payload(position: Position) -> dict[str, Any]:
    return _as_dict(_as_dict(position.metadata_json).get("closed_position_pnl"))


def _execution_slippage_bps(execution: Execution) -> float | None:
    payload = _as_dict(execution.payload)
    execution_quality = _as_dict(payload.get("execution_quality"))
    for source in (payload, execution_quality):
        for key in ("signed_slippage_bps", "slippage_bps", "adverse_slippage_bps", "realized_slippage_bps"):
            parsed = _optional_float(source.get(key))
            if parsed is not None:
                return parsed
    parsed_pct = _optional_float(execution.slippage_pct)
    return parsed_pct * 10_000 if parsed_pct is not None else None


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _profit_factor(wins: list[float], losses: list[float]) -> float | None:
    total_win = sum(wins)
    total_loss = abs(sum(losses))
    if total_loss <= 0.0:
        return None
    return total_win / total_loss


def _max_loss_streak(records: list[StrategyTradeRecord]) -> int:
    streak = 0
    maximum = 0
    for record in sorted(records, key=lambda item: item.closed_at or datetime.min):
        if record.fee_adjusted_pnl < 0:
            streak += 1
            maximum = max(maximum, streak)
        else:
            streak = 0
    return maximum


def _max_drawdown(records: list[StrategyTradeRecord]) -> float:
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for record in sorted(records, key=lambda item: item.closed_at or datetime.min):
        equity += record.fee_adjusted_pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return max_drawdown


def _filter_matches(record: StrategyTradeRecord, filters: StrategyPerformanceFilters) -> bool:
    comparisons = {
        "strategy_id": _normalize_filter(filters.strategy_id),
        "regime_id": _normalize_filter(filters.regime_id),
        "confirmation_type": _normalize_filter(filters.confirmation_type),
        "direction": _normalize_filter(filters.direction),
        "symbol": _normalize_filter(filters.symbol, upper=True),
        "risk_mode": _normalize_filter(filters.risk_mode),
        "mode": _normalize_filter(filters.mode),
    }
    for key, expected in comparisons.items():
        if expected is None:
            continue
        actual = getattr(record, key)
        normalized_actual = str(actual).upper() if key == "symbol" else str(actual).lower()
        if normalized_actual != expected:
            return False
    return True


def _audit_tags_by_position(session: Session, positions: list[Position], orders_by_position: dict[int, list[Order]]) -> dict[int, dict[str, Any]]:
    position_ids = {int(position.id) for position in positions if position.id is not None}
    order_to_position = {
        int(order.id): int(position_id)
        for position_id, orders in orders_by_position.items()
        for order in orders
        if order.id is not None
    }
    entity_ids = {str(position_id) for position_id in position_ids} | {str(order_id) for order_id in order_to_position}
    if not entity_ids:
        return {}
    rows = list(
        session.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_type.in_(("position", "order", "execution", "risk_check")),
                AuditEvent.entity_id.in_(entity_ids),
            )
        )
    )
    result: dict[int, dict[str, Any]] = defaultdict(dict)
    for row in rows:
        payload = _as_dict(row.payload)
        position_id = None
        parsed_entity_id = int(row.entity_id) if str(row.entity_id).isdigit() else None
        if row.entity_type == "position" and parsed_entity_id in position_ids:
            position_id = parsed_entity_id
        elif row.entity_type == "order" and parsed_entity_id in order_to_position:
            position_id = order_to_position[parsed_entity_id]
        else:
            raw_position_id = payload.get("position_id")
            if str(raw_position_id).isdigit() and int(raw_position_id) in position_ids:
                position_id = int(raw_position_id)
        if position_id is None:
            continue
        tags = _metadata_tags(payload)
        if tags:
            result[position_id] = {**tags, **result[position_id]}
    return dict(result)


def load_strategy_trade_records(
    session: Session,
    filters: StrategyPerformanceFilters | None = None,
) -> list[StrategyTradeRecord]:
    filters = filters or StrategyPerformanceFilters()
    query = select(Position).where(Position.status == "closed")
    if filters.symbol:
        query = query.where(Position.symbol == filters.symbol.upper())
    if filters.direction:
        query = query.where(Position.side == filters.direction.lower())
    if filters.mode:
        query = query.where(Position.mode == filters.mode)
    if filters.since is not None:
        query = query.where(Position.closed_at >= filters.since)
    if filters.until is not None:
        query = query.where(Position.closed_at <= filters.until)
    query = query.order_by(Position.closed_at.asc(), Position.id.asc())
    if filters.limit is not None and filters.limit > 0:
        query = query.limit(filters.limit)
    positions = list(session.scalars(query))
    if not positions:
        return []

    position_ids = [int(position.id) for position in positions if position.id is not None]
    orders = list(session.scalars(select(Order).where(Order.position_id.in_(position_ids)))) if position_ids else []
    orders_by_position: dict[int, list[Order]] = defaultdict(list)
    for order in orders:
        if order.position_id is not None:
            orders_by_position[int(order.position_id)].append(order)
    order_ids = [int(order.id) for order in orders if order.id is not None]
    executions = list(
        session.scalars(
            select(Execution).where(
                (Execution.position_id.in_(position_ids)) | (Execution.order_id.in_(order_ids))
            )
        )
    ) if position_ids or order_ids else []
    executions_by_position: dict[int, list[Execution]] = defaultdict(list)
    order_to_position = {
        int(order.id): int(order.position_id)
        for order in orders
        if order.id is not None and order.position_id is not None
    }
    seen_execution_ids: set[int] = set()
    for execution in executions:
        if execution.id is not None and int(execution.id) in seen_execution_ids:
            continue
        if execution.id is not None:
            seen_execution_ids.add(int(execution.id))
        position_id = execution.position_id
        if position_id is None and execution.order_id is not None:
            position_id = order_to_position.get(int(execution.order_id))
        if position_id is not None:
            executions_by_position[int(position_id)].append(execution)
    audit_tags = _audit_tags_by_position(session, positions, orders_by_position)

    records: list[StrategyTradeRecord] = []
    for position in positions:
        if position.id is None:
            continue
        position_orders = orders_by_position.get(int(position.id), [])
        position_executions = executions_by_position.get(int(position.id), [])
        tag_payloads: list[object] = [
            position.metadata_json,
            audit_tags.get(int(position.id), {}),
            *[order.metadata_json for order in position_orders],
            *[execution.payload for execution in position_executions],
        ]
        tags = _merge_tags(*tag_payloads)
        closed_pnl = _closed_pnl_payload(position)
        fee_total = _optional_float(closed_pnl.get("fee_total"))
        if fee_total is None:
            fee_total = sum(_float(execution.fee_paid) for execution in position_executions)
        gross_pnl = _optional_float(closed_pnl.get("gross_realized_pnl"))
        if gross_pnl is None:
            gross_pnl = _float(position.realized_pnl)
        net_pnl = _optional_float(closed_pnl.get("net_realized_pnl"))
        if net_pnl is None:
            net_pnl = gross_pnl - fee_total
        r_multiple = _optional_float(closed_pnl.get("net_r_multiple"))
        if r_multiple is None:
            risk_amount = _optional_float(closed_pnl.get("risk_amount_usdt")) or _optional_float(tags.get("initial_risk_usdt"))
            if risk_amount is not None and risk_amount > 0.0:
                r_multiple = net_pnl / risk_amount
        hold_minutes = None
        if position.opened_at is not None and position.closed_at is not None:
            hold_minutes = max((position.closed_at - position.opened_at).total_seconds() / 60.0, 0.0)
        slippage_values = [
            value
            for value in (_execution_slippage_bps(execution) for execution in position_executions)
            if value is not None
        ]
        record = StrategyTradeRecord(
            position_id=int(position.id),
            symbol=_text(tags.get("symbol"), position.symbol).upper(),
            direction=_text(tags.get("direction") or tags.get("decision"), position.side).lower(),
            mode=_text(position.mode, "unknown").lower(),
            strategy_id=_text(tags.get("strategy_id") or tags.get("strategy_engine"), "unknown"),
            regime_id=_text(tags.get("regime_id"), "unknown"),
            confirmation_type=_text(
                tags.get("confirmation_type") or tags.get("entry_confirmation_type"),
                "unknown",
            ),
            risk_mode=_text(tags.get("risk_mode"), "unknown"),
            opened_at=position.opened_at,
            closed_at=position.closed_at,
            gross_pnl=gross_pnl,
            fee_total=fee_total,
            fee_adjusted_pnl=net_pnl,
            r_multiple=r_multiple,
            hold_minutes=hold_minutes,
            avg_slippage_bps=_avg(slippage_values),
        )
        if _filter_matches(record, filters):
            records.append(record)
    return records


def _aggregate_bucket(records: list[StrategyTradeRecord], group: dict[str, str]) -> StrategyPerformanceBucket:
    wins = [record.fee_adjusted_pnl for record in records if record.fee_adjusted_pnl > 0]
    losses = [record.fee_adjusted_pnl for record in records if record.fee_adjusted_pnl < 0]
    r_values = [record.r_multiple for record in records if record.r_multiple is not None]
    hold_values = [record.hold_minutes for record in records if record.hold_minutes is not None]
    slippage_values = [record.avg_slippage_bps for record in records if record.avg_slippage_bps is not None]
    trade_count = len(records)
    return StrategyPerformanceBucket(
        group=group,
        trade_count=trade_count,
        win_rate=(len(wins) / trade_count) if trade_count else None,
        avg_win=_avg(wins),
        avg_loss=_avg(losses),
        avg_R=_avg(r_values),
        profit_factor=_profit_factor(wins, losses),
        max_drawdown=_max_drawdown(records),
        max_loss_streak=_max_loss_streak(records),
        avg_hold_time=_avg(hold_values),
        avg_slippage_bps=_avg(slippage_values),
        fee_adjusted_pnl=sum(record.fee_adjusted_pnl for record in records),
        gross_pnl=sum(record.gross_pnl for record in records),
        fee_total=sum(record.fee_total for record in records),
    )


def build_strategy_performance_report(
    session: Session,
    *,
    filters: StrategyPerformanceFilters | None = None,
    group_by: list[str] | tuple[str, ...] = DEFAULT_GROUP_BY,
) -> StrategyPerformanceReport:
    filters = filters or StrategyPerformanceFilters()
    invalid_groups = [key for key in group_by if key not in SUPPORTED_GROUP_BY]
    if invalid_groups:
        raise ValueError(f"Unsupported group_by keys: {', '.join(invalid_groups)}")
    records = load_strategy_trade_records(session, filters)
    grouped: dict[tuple[str, ...], list[StrategyTradeRecord]] = defaultdict(list)
    for record in records:
        key = tuple(str(getattr(record, group_key)) for group_key in group_by)
        grouped[key].append(record)
    buckets = [
        _aggregate_bucket(bucket_records, dict(zip(group_by, bucket_key, strict=False)))
        for bucket_key, bucket_records in grouped.items()
    ]
    buckets.sort(key=lambda bucket: (bucket.group.get("strategy_id", ""), bucket.group.get("symbol", ""), bucket.group.get("direction", "")))
    return StrategyPerformanceReport(
        generated_at=utcnow_naive(),
        filters={key: value for key, value in asdict(filters).items() if _is_present(value)},
        group_by=list(group_by),
        trade_count=len(records),
        buckets=buckets,
        message=None if buckets else NO_DATA_MESSAGE,
    )


def _format_number(value: object, *, digits: int = 4) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def format_console_report(report: StrategyPerformanceReport) -> str:
    if not report.buckets:
        return NO_DATA_MESSAGE
    columns = [
        *report.group_by,
        "trade_count",
        "win_rate",
        "avg_win",
        "avg_loss",
        "avg_R",
        "profit_factor",
        "max_loss_streak",
        "avg_hold_time",
        "avg_slippage_bps",
        "fee_adjusted_pnl",
    ]
    rows: list[dict[str, str]] = []
    for bucket in report.buckets:
        row = {key: str(bucket.group.get(key, "")) for key in report.group_by}
        row.update(
            {
                "trade_count": str(bucket.trade_count),
                "win_rate": _format_number(bucket.win_rate, digits=4),
                "avg_win": _format_number(bucket.avg_win, digits=4),
                "avg_loss": _format_number(bucket.avg_loss, digits=4),
                "avg_R": _format_number(bucket.avg_R, digits=4),
                "profit_factor": _format_number(bucket.profit_factor, digits=4),
                "max_loss_streak": str(bucket.max_loss_streak),
                "avg_hold_time": _format_number(bucket.avg_hold_time, digits=2),
                "avg_slippage_bps": _format_number(bucket.avg_slippage_bps, digits=4),
                "fee_adjusted_pnl": _format_number(bucket.fee_adjusted_pnl, digits=4),
            }
        )
        rows.append(row)
    widths = {column: max(len(column), *(len(row[column]) for row in rows)) for column in columns}
    lines = [" | ".join(column.ljust(widths[column]) for column in columns)]
    lines.append("-+-".join("-" * widths[column] for column in columns))
    for row in rows:
        lines.append(" | ".join(row[column].ljust(widths[column]) for column in columns))
    return "\n".join(lines)


def format_csv_report(report: StrategyPerformanceReport) -> str:
    output = io.StringIO()
    fieldnames = [
        *report.group_by,
        "trade_count",
        "win_rate",
        "avg_win",
        "avg_loss",
        "avg_R",
        "profit_factor",
        "max_drawdown",
        "max_loss_streak",
        "avg_hold_time",
        "avg_slippage_bps",
        "fee_adjusted_pnl",
        "gross_pnl",
        "fee_total",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for bucket in report.buckets:
        writer.writerow(
            {
                **{key: bucket.group.get(key, "") for key in report.group_by},
                "trade_count": bucket.trade_count,
                "win_rate": bucket.win_rate,
                "avg_win": bucket.avg_win,
                "avg_loss": bucket.avg_loss,
                "avg_R": bucket.avg_R,
                "profit_factor": bucket.profit_factor,
                "max_drawdown": bucket.max_drawdown,
                "max_loss_streak": bucket.max_loss_streak,
                "avg_hold_time": bucket.avg_hold_time,
                "avg_slippage_bps": bucket.avg_slippage_bps,
                "fee_adjusted_pnl": bucket.fee_adjusted_pnl,
                "gross_pnl": bucket.gross_pnl,
                "fee_total": bucket.fee_total,
            }
        )
    return output.getvalue()


def format_json_report(report: StrategyPerformanceReport) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2, default=str)
