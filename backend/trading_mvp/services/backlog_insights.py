from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from trading_mvp.models import AgentRun, CompetitorNote, Execution, Order, PnLSnapshot, Position, RiskCheck
from trading_mvp.schemas import (
    DecisionPerformanceEntry,
    FeatureFlagPerformanceEntry,
    PerformanceAggregateEntry,
    PerformanceWindowReport,
    PerformanceWindowSummary,
    SignalPerformanceEntry,
    SignalPerformanceReportResponse,
    StructuredCompetitorNote,
    StructuredCompetitorNotesResponse,
)
from trading_mvp.time_utils import utcnow_naive


@dataclass(slots=True)
class SignalBucket:
    key: str
    decisions: int = 0
    approvals: int = 0
    orders: int = 0
    fills: int = 0
    holds: int = 0
    longs: int = 0
    shorts: int = 0
    reduces: int = 0
    exits: int = 0
    wins: int = 0
    losses: int = 0
    realized_pnl_total: float = 0.0
    fee_total: float = 0.0
    net_realized_pnl_total: float = 0.0
    slippages: list[float] = field(default_factory=list)
    holding_minutes: list[float] = field(default_factory=list)
    holding_over_plan_count: int = 0
    open_positions: int = 0
    closed_positions: int = 0
    stop_loss_closes: int = 0
    take_profit_closes: int = 0
    manual_closes: int = 0
    unclassified_closes: int = 0
    latest_seen_at: datetime = field(default_factory=utcnow_naive)


@dataclass(slots=True)
class DecisionPerformanceSnapshot:
    decision_run_id: int
    created_at: datetime
    symbol: str
    timeframe: str
    decision: str
    regime: str
    trend_alignment: str
    weak_volume: bool
    volatility_expanded: bool
    momentum_weakening: bool
    rationale_codes: list[str]
    approved: bool
    approved_risk_pct: float
    approved_leverage: float
    orders: int
    fills: int
    wins: int
    losses: int
    realized_pnl_total: float
    fee_total: float
    net_realized_pnl_total: float
    average_slippage_pct: float
    max_holding_minutes_planned: int | None
    holding_minutes_observed: float
    holding_result_status: str
    stop_loss: float | None
    take_profit: float | None
    planned_risk_reward_ratio: float | None
    close_outcome: str
    stop_loss_closes: int
    take_profit_closes: int
    manual_closes: int
    unclassified_closes: int
    open_positions: int
    closed_positions: int
    holding_over_plan_count: int
    position_ids: list[int]


def _safe_float(value: object, default: float = 0.0) -> float:
    if value in {None, ""}:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return default


def _safe_int(value: object, default: int | None = None) -> int | None:
    if value in {None, ""}:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value))
    except ValueError:
        return default


def _safe_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return default


def _snapshot_net_pnl_estimate(session: Session, since: datetime) -> float:
    latest = session.scalar(select(PnLSnapshot).order_by(desc(PnLSnapshot.created_at)).limit(1))
    if latest is None:
        return 0.0
    baseline = session.scalar(
        select(PnLSnapshot)
        .where(PnLSnapshot.created_at < since)
        .order_by(desc(PnLSnapshot.created_at))
        .limit(1)
    )
    baseline_cumulative = baseline.cumulative_pnl if baseline is not None else 0.0
    return latest.cumulative_pnl - baseline_cumulative


def _extract_analysis_context(decision_row: AgentRun) -> tuple[str, str, bool, bool, bool]:
    input_payload = decision_row.input_payload if isinstance(decision_row.input_payload, dict) else {}
    metadata = decision_row.metadata_json if isinstance(decision_row.metadata_json, dict) else {}
    features = input_payload.get("features") if isinstance(input_payload.get("features"), dict) else {}
    regime = features.get("regime") if isinstance(features.get("regime"), dict) else {}
    metadata_context = (
        metadata.get("analysis_context") if isinstance(metadata.get("analysis_context"), dict) else {}
    )
    metadata_regime = (
        metadata_context.get("regime") if isinstance(metadata_context.get("regime"), dict) else {}
    )
    metadata_flags = (
        metadata_context.get("flags") if isinstance(metadata_context.get("flags"), dict) else {}
    )

    primary_regime = str(
        regime.get("primary_regime")
        or metadata_regime.get("primary_regime")
        or "unknown"
    )
    trend_alignment = str(
        regime.get("trend_alignment")
        or metadata_regime.get("trend_alignment")
        or "unknown"
    )
    volatility_regime = str(
        regime.get("volatility_regime")
        or metadata_regime.get("volatility_regime")
        or "unknown"
    )
    weak_volume = _safe_bool(
        regime.get("weak_volume", metadata_flags.get("weak_volume", False))
    )
    momentum_weakening = _safe_bool(
        regime.get("momentum_weakening", metadata_flags.get("momentum_weakening", False))
    )
    volatility_expanded = _safe_bool(
        metadata_flags.get("volatility_expanded", volatility_regime == "expanded")
    )
    return primary_regime, trend_alignment, weak_volume, volatility_expanded, momentum_weakening


def _planned_risk_reward_ratio(
    *,
    decision: str,
    entry_zone_min: object,
    entry_zone_max: object,
    stop_loss: float | None,
    take_profit: float | None,
) -> float | None:
    if stop_loss is None or take_profit is None:
        return None
    entry_min = _safe_float(entry_zone_min, default=0.0)
    entry_max = _safe_float(entry_zone_max, default=0.0)
    entry_price = (entry_min + entry_max) / 2.0 if entry_min > 0 and entry_max > 0 else max(entry_min, entry_max)
    if entry_price <= 0:
        return None
    if decision == "long":
        risk = entry_price - stop_loss
        reward = take_profit - entry_price
    elif decision == "short":
        risk = stop_loss - entry_price
        reward = entry_price - take_profit
    else:
        return None
    if risk <= 0 or reward <= 0:
        return None
    return reward / risk


def _close_outcome_from_counts(
    *,
    stop_loss_closes: int,
    take_profit_closes: int,
    manual_closes: int,
    unclassified_closes: int,
    open_positions: int,
    closed_positions: int,
) -> str:
    if stop_loss_closes > 0:
        return "stop_loss"
    if take_profit_closes > 0:
        return "take_profit"
    if manual_closes > 0:
        return "manual_close"
    if unclassified_closes > 0:
        return "unclassified_close"
    if open_positions > 0:
        return "open"
    if closed_positions > 0:
        return "closed_without_fill_classification"
    return "not_closed"


def _hold_condition_key(
    *,
    regime: str,
    trend_alignment: str,
    weak_volume: bool,
    volatility_expanded: bool,
    momentum_weakening: bool,
) -> str:
    return (
        f"{regime} | trend={trend_alignment} | weak_volume={'on' if weak_volume else 'off'}"
        f" | volatility_expanded={'on' if volatility_expanded else 'off'}"
        f" | momentum_weakening={'on' if momentum_weakening else 'off'}"
    )


def _holding_snapshot(
    positions: list[Position],
    *,
    planned_max_holding_minutes: int | None,
    now: datetime,
) -> tuple[float, str, int, int, int]:
    if not positions:
        return 0.0, "unlinked", 0, 0, 0
    observed_values: list[float] = []
    open_positions = 0
    closed_positions = 0
    holding_over_plan_count = 0
    for position in positions:
        end_at = position.closed_at or now
        duration_minutes = max((end_at - position.opened_at).total_seconds() / 60.0, 0.0)
        observed_values.append(duration_minutes)
        if position.closed_at is None or position.status == "open":
            open_positions += 1
        else:
            closed_positions += 1
        if planned_max_holding_minutes is not None and duration_minutes > planned_max_holding_minutes:
            holding_over_plan_count += 1
    observed = max(observed_values) if observed_values else 0.0
    if open_positions > 0:
        status = "open_over_plan" if holding_over_plan_count > 0 else "open_within_plan"
    else:
        status = "closed_over_plan" if holding_over_plan_count > 0 else "closed_within_plan"
    return observed, status, open_positions, closed_positions, holding_over_plan_count


def _bucket_from_snapshots(key: str, snapshots: list[DecisionPerformanceSnapshot]) -> PerformanceAggregateEntry:
    if not snapshots:
        return PerformanceAggregateEntry(
            key=key,
            decisions=0,
            approvals=0,
            orders=0,
            fills=0,
            holds=0,
            longs=0,
            shorts=0,
            reduces=0,
            exits=0,
            wins=0,
            losses=0,
            realized_pnl_total=0.0,
            fee_total=0.0,
            net_realized_pnl_total=0.0,
            average_slippage_pct=0.0,
            average_holding_minutes=0.0,
            holding_over_plan_count=0,
            open_positions=0,
            closed_positions=0,
            stop_loss_closes=0,
            take_profit_closes=0,
            manual_closes=0,
            unclassified_closes=0,
            latest_seen_at=utcnow_naive(),
        )
    slippages = [item.average_slippage_pct for item in snapshots if item.fills > 0]
    holdings = [item.holding_minutes_observed for item in snapshots if item.position_ids]
    return PerformanceAggregateEntry(
        key=key,
        decisions=len(snapshots),
        approvals=sum(1 for item in snapshots if item.approved),
        orders=sum(item.orders for item in snapshots),
        fills=sum(item.fills for item in snapshots),
        holds=sum(1 for item in snapshots if item.decision == "hold"),
        longs=sum(1 for item in snapshots if item.decision == "long"),
        shorts=sum(1 for item in snapshots if item.decision == "short"),
        reduces=sum(1 for item in snapshots if item.decision == "reduce"),
        exits=sum(1 for item in snapshots if item.decision == "exit"),
        wins=sum(item.wins for item in snapshots),
        losses=sum(item.losses for item in snapshots),
        realized_pnl_total=sum(item.realized_pnl_total for item in snapshots),
        fee_total=sum(item.fee_total for item in snapshots),
        net_realized_pnl_total=sum(item.net_realized_pnl_total for item in snapshots),
        average_slippage_pct=(sum(slippages) / len(slippages) if slippages else 0.0),
        average_holding_minutes=(sum(holdings) / len(holdings) if holdings else 0.0),
        holding_over_plan_count=sum(item.holding_over_plan_count for item in snapshots),
        open_positions=sum(item.open_positions for item in snapshots),
        closed_positions=sum(item.closed_positions for item in snapshots),
        stop_loss_closes=sum(item.stop_loss_closes for item in snapshots),
        take_profit_closes=sum(item.take_profit_closes for item in snapshots),
        manual_closes=sum(item.manual_closes for item in snapshots),
        unclassified_closes=sum(item.unclassified_closes for item in snapshots),
        latest_seen_at=max(item.created_at for item in snapshots),
    )


def _build_window_report(
    session: Session,
    *,
    window_label: str,
    window_hours: int,
    aggregate_limit: int,
    decision_limit: int,
) -> PerformanceWindowReport:
    since = utcnow_naive() - timedelta(hours=window_hours)
    now = utcnow_naive()
    decision_rows = list(
        session.scalars(
            select(AgentRun)
            .where(AgentRun.role == "trading_decision", AgentRun.created_at >= since)
            .order_by(AgentRun.created_at.desc())
        )
    )
    decision_ids = [row.id for row in decision_rows]

    risk_by_decision: dict[int, RiskCheck] = {}
    if decision_ids:
        for risk_row in session.scalars(
            select(RiskCheck)
            .where(RiskCheck.decision_run_id.in_(decision_ids))
            .order_by(RiskCheck.created_at.desc())
        ):
            if risk_row.decision_run_id is not None and risk_row.decision_run_id not in risk_by_decision:
                risk_by_decision[risk_row.decision_run_id] = risk_row

    orders_by_decision: dict[int, list[Order]] = defaultdict(list)
    order_ids: list[int] = []
    position_ids: set[int] = set()
    if decision_ids:
        for order_row in session.scalars(
            select(Order)
            .where(Order.decision_run_id.in_(decision_ids))
            .order_by(Order.created_at.desc())
        ):
            if order_row.decision_run_id is not None:
                orders_by_decision[order_row.decision_run_id].append(order_row)
            order_ids.append(order_row.id)
            if order_row.position_id is not None:
                position_ids.add(order_row.position_id)

    executions_by_order: dict[int, list[Execution]] = defaultdict(list)
    if order_ids:
        for execution_row in session.scalars(
            select(Execution)
            .where(Execution.order_id.in_(order_ids))
            .order_by(Execution.created_at.desc())
        ):
            if execution_row.order_id is not None:
                executions_by_order[execution_row.order_id].append(execution_row)

    positions_by_id: dict[int, Position] = {}
    if position_ids:
        for position_row in session.scalars(select(Position).where(Position.id.in_(position_ids))):
            positions_by_id[position_row.id] = position_row

    decision_items: list[DecisionPerformanceSnapshot] = []
    rationale_groups: dict[str, list[DecisionPerformanceSnapshot]] = defaultdict(list)
    symbol_groups: dict[str, list[DecisionPerformanceSnapshot]] = defaultdict(list)
    timeframe_groups: dict[str, list[DecisionPerformanceSnapshot]] = defaultdict(list)
    regime_groups: dict[str, list[DecisionPerformanceSnapshot]] = defaultdict(list)
    trend_groups: dict[str, list[DecisionPerformanceSnapshot]] = defaultdict(list)
    direction_groups: dict[str, list[DecisionPerformanceSnapshot]] = defaultdict(list)
    hold_condition_groups: dict[str, list[DecisionPerformanceSnapshot]] = defaultdict(list)
    close_outcome_groups: dict[str, list[DecisionPerformanceSnapshot]] = defaultdict(list)
    flag_groups: dict[str, dict[bool, list[DecisionPerformanceSnapshot]]] = {
        "weak_volume": defaultdict(list),
        "volatility_expanded": defaultdict(list),
        "momentum_weakening": defaultdict(list),
    }

    for decision_row in decision_rows:
        payload = decision_row.output_payload if isinstance(decision_row.output_payload, dict) else {}
        rationale_codes = (
            [str(item) for item in payload.get("rationale_codes", []) if item]
            if isinstance(payload.get("rationale_codes"), list)
            else []
        ) or ["UNSPECIFIED"]
        symbol = str(payload.get("symbol") or "UNKNOWN")
        timeframe = str(payload.get("timeframe") or "UNKNOWN")
        decision = str(payload.get("decision") or "unknown")
        regime, trend_alignment, weak_volume, volatility_expanded, momentum_weakening = _extract_analysis_context(
            decision_row
        )
        planned_holding_minutes = _safe_int(payload.get("max_holding_minutes"))
        stop_loss = _safe_float(payload.get("stop_loss"), default=0.0) or None
        take_profit = _safe_float(payload.get("take_profit"), default=0.0) or None

        linked_risk = risk_by_decision.get(decision_row.id)
        linked_orders = orders_by_decision.get(decision_row.id, [])
        linked_executions = [
            execution_row
            for order_row in linked_orders
            for execution_row in executions_by_order.get(order_row.id, [])
        ]
        linked_position_ids = sorted(
            {
                order_row.position_id
                for order_row in linked_orders
                if order_row.position_id is not None and order_row.position_id in positions_by_id
            }
        )
        linked_positions = [positions_by_id[position_id] for position_id in linked_position_ids]
        holding_minutes_observed, holding_result_status, open_positions, closed_positions, holding_over_plan_count = _holding_snapshot(
            linked_positions,
            planned_max_holding_minutes=planned_holding_minutes,
            now=now,
        )
        stop_loss_closes = 0
        take_profit_closes = 0
        manual_closes = 0
        unclassified_closes = 0
        for order_row in linked_orders:
            execution_count = len(executions_by_order.get(order_row.id, []))
            if execution_count == 0:
                continue
            order_type = str(order_row.order_type or "").upper()
            if order_type.startswith("STOP"):
                stop_loss_closes += execution_count
            elif order_type.startswith("TAKE_PROFIT"):
                take_profit_closes += execution_count
            elif order_row.reduce_only or order_row.close_only:
                manual_closes += execution_count
        if closed_positions > 0 and (stop_loss_closes + take_profit_closes + manual_closes) == 0:
            unclassified_closes = max(closed_positions, 1)
        realized_total = sum(_safe_float(execution_row.realized_pnl) for execution_row in linked_executions)
        fee_total = sum(_safe_float(execution_row.fee_paid) for execution_row in linked_executions)
        net_realized_total = realized_total - fee_total
        slippages = [_safe_float(execution_row.slippage_pct) for execution_row in linked_executions]
        wins = sum(1 for execution_row in linked_executions if (_safe_float(execution_row.realized_pnl) - _safe_float(execution_row.fee_paid)) > 0)
        losses = sum(1 for execution_row in linked_executions if (_safe_float(execution_row.realized_pnl) - _safe_float(execution_row.fee_paid)) < 0)
        snapshot = DecisionPerformanceSnapshot(
            decision_run_id=decision_row.id,
            created_at=decision_row.created_at,
            symbol=symbol,
            timeframe=timeframe,
            decision=decision,
            regime=regime,
            trend_alignment=trend_alignment,
            weak_volume=weak_volume,
            volatility_expanded=volatility_expanded,
            momentum_weakening=momentum_weakening,
            rationale_codes=rationale_codes,
            approved=bool(linked_risk.allowed) if linked_risk is not None else False,
            approved_risk_pct=_safe_float(linked_risk.approved_risk_pct) if linked_risk is not None else 0.0,
            approved_leverage=_safe_float(linked_risk.approved_leverage) if linked_risk is not None else 0.0,
            orders=len(linked_orders),
            fills=len(linked_executions),
            wins=wins,
            losses=losses,
            realized_pnl_total=realized_total,
            fee_total=fee_total,
            net_realized_pnl_total=net_realized_total,
            average_slippage_pct=(sum(slippages) / len(slippages) if slippages else 0.0),
            max_holding_minutes_planned=planned_holding_minutes,
            holding_minutes_observed=holding_minutes_observed,
            holding_result_status=holding_result_status,
            stop_loss=stop_loss,
            take_profit=take_profit,
            planned_risk_reward_ratio=_planned_risk_reward_ratio(
                decision=decision,
                entry_zone_min=payload.get("entry_zone_min"),
                entry_zone_max=payload.get("entry_zone_max"),
                stop_loss=stop_loss,
                take_profit=take_profit,
            ),
            close_outcome=_close_outcome_from_counts(
                stop_loss_closes=stop_loss_closes,
                take_profit_closes=take_profit_closes,
                manual_closes=manual_closes,
                unclassified_closes=unclassified_closes,
                open_positions=open_positions,
                closed_positions=closed_positions,
            ),
            stop_loss_closes=stop_loss_closes,
            take_profit_closes=take_profit_closes,
            manual_closes=manual_closes,
            unclassified_closes=unclassified_closes,
            open_positions=open_positions,
            closed_positions=closed_positions,
            holding_over_plan_count=holding_over_plan_count,
            position_ids=linked_position_ids,
        )
        decision_items.append(snapshot)
        for rationale_code in rationale_codes:
            rationale_groups[rationale_code].append(snapshot)
        symbol_groups[symbol].append(snapshot)
        timeframe_groups[timeframe].append(snapshot)
        regime_groups[regime].append(snapshot)
        trend_groups[trend_alignment].append(snapshot)
        direction_groups[decision].append(snapshot)
        close_outcome_groups[snapshot.close_outcome].append(snapshot)
        flag_groups["weak_volume"][weak_volume].append(snapshot)
        flag_groups["volatility_expanded"][volatility_expanded].append(snapshot)
        flag_groups["momentum_weakening"][momentum_weakening].append(snapshot)
        if decision == "hold":
            hold_condition_groups[
                _hold_condition_key(
                    regime=regime,
                    trend_alignment=trend_alignment,
                    weak_volume=weak_volume,
                    volatility_expanded=volatility_expanded,
                    momentum_weakening=momentum_weakening,
                )
            ].append(snapshot)

    rationale_items = [
        _bucket_from_snapshots(key, snapshots)
        for key, snapshots in rationale_groups.items()
    ]
    symbol_items = [
        _bucket_from_snapshots(key, snapshots)
        for key, snapshots in symbol_groups.items()
    ]
    timeframe_items = [
        _bucket_from_snapshots(key, snapshots)
        for key, snapshots in timeframe_groups.items()
    ]
    regime_items = [_bucket_from_snapshots(key, snapshots) for key, snapshots in regime_groups.items()]
    trend_items = [_bucket_from_snapshots(key, snapshots) for key, snapshots in trend_groups.items()]
    direction_items = [_bucket_from_snapshots(key, snapshots) for key, snapshots in direction_groups.items()]
    hold_condition_items = [
        _bucket_from_snapshots(key, snapshots) for key, snapshots in hold_condition_groups.items()
    ]
    close_outcome_items = [
        _bucket_from_snapshots(key, snapshots) for key, snapshots in close_outcome_groups.items()
    ]
    flag_items = [
        FeatureFlagPerformanceEntry(
            flag_name=flag_name,
            enabled=_bucket_from_snapshots(f"{flag_name}=on", grouped.get(True, [])),
            disabled=_bucket_from_snapshots(f"{flag_name}=off", grouped.get(False, [])),
        )
        for flag_name, grouped in flag_groups.items()
        if grouped.get(True) or grouped.get(False)
    ]
    rationale_items.sort(key=lambda item: (item.net_realized_pnl_total, item.fills, item.decisions), reverse=True)
    symbol_items.sort(key=lambda item: (item.net_realized_pnl_total, item.fills, item.decisions), reverse=True)
    timeframe_items.sort(key=lambda item: (item.net_realized_pnl_total, item.fills, item.decisions), reverse=True)
    regime_items.sort(key=lambda item: (item.net_realized_pnl_total, item.decisions, item.fills), reverse=True)
    trend_items.sort(key=lambda item: (item.net_realized_pnl_total, item.decisions, item.fills), reverse=True)
    direction_items.sort(key=lambda item: (item.net_realized_pnl_total, item.decisions, item.fills), reverse=True)
    hold_condition_items.sort(key=lambda item: (item.holds, item.decisions, item.latest_seen_at), reverse=True)
    close_outcome_items.sort(key=lambda item: (item.decisions, item.net_realized_pnl_total), reverse=True)
    decision_items.sort(key=lambda item: (item.created_at, item.net_realized_pnl_total), reverse=True)

    overall_slippages = [item.average_slippage_pct for item in decision_items if item.fills > 0]
    overall_holdings = [item.holding_minutes_observed for item in decision_items if item.position_ids]
    summary = PerformanceWindowSummary(
        decisions=len(decision_items),
        approvals=sum(1 for item in decision_items if item.approved),
        orders=sum(item.orders for item in decision_items),
        fills=sum(item.fills for item in decision_items),
        holds=sum(1 for item in decision_items if item.decision == "hold"),
        longs=sum(1 for item in decision_items if item.decision == "long"),
        shorts=sum(1 for item in decision_items if item.decision == "short"),
        reduces=sum(1 for item in decision_items if item.decision == "reduce"),
        exits=sum(1 for item in decision_items if item.decision == "exit"),
        wins=sum(item.wins for item in decision_items),
        losses=sum(item.losses for item in decision_items),
        realized_pnl_total=sum(item.realized_pnl_total for item in decision_items),
        fee_total=sum(item.fee_total for item in decision_items),
        net_realized_pnl_total=sum(item.net_realized_pnl_total for item in decision_items),
        average_slippage_pct=(sum(overall_slippages) / len(overall_slippages) if overall_slippages else 0.0),
        average_holding_minutes=(sum(overall_holdings) / len(overall_holdings) if overall_holdings else 0.0),
        holding_over_plan_count=sum(item.holding_over_plan_count for item in decision_items),
        open_positions=sum(item.open_positions for item in decision_items),
        closed_positions=sum(item.closed_positions for item in decision_items),
        stop_loss_closes=sum(item.stop_loss_closes for item in decision_items),
        take_profit_closes=sum(item.take_profit_closes for item in decision_items),
        manual_closes=sum(item.manual_closes for item in decision_items),
        unclassified_closes=sum(item.unclassified_closes for item in decision_items),
        snapshot_net_pnl_estimate=_snapshot_net_pnl_estimate(session, since),
    )

    return PerformanceWindowReport(
        window_label=window_label,
        window_hours=window_hours,
        summary=summary,
        decisions=[
            DecisionPerformanceEntry(
                decision_run_id=item.decision_run_id,
                created_at=item.created_at,
                symbol=item.symbol,
                timeframe=item.timeframe,
                decision=item.decision,
                regime=item.regime,
                trend_alignment=item.trend_alignment,
                weak_volume=item.weak_volume,
                volatility_expanded=item.volatility_expanded,
                momentum_weakening=item.momentum_weakening,
                rationale_codes=item.rationale_codes,
                approved=item.approved,
                approved_risk_pct=item.approved_risk_pct,
                approved_leverage=item.approved_leverage,
                orders=item.orders,
                fills=item.fills,
                wins=item.wins,
                losses=item.losses,
                realized_pnl_total=item.realized_pnl_total,
                fee_total=item.fee_total,
                net_realized_pnl_total=item.net_realized_pnl_total,
                average_slippage_pct=item.average_slippage_pct,
                max_holding_minutes_planned=item.max_holding_minutes_planned,
                holding_minutes_observed=item.holding_minutes_observed,
                holding_result_status=item.holding_result_status,
                stop_loss=item.stop_loss,
                take_profit=item.take_profit,
                planned_risk_reward_ratio=item.planned_risk_reward_ratio,
                close_outcome=item.close_outcome,
                position_ids=item.position_ids,
            )
            for item in decision_items[:decision_limit]
        ],
        rationale_codes=rationale_items[:aggregate_limit],
        symbols=symbol_items[:aggregate_limit],
        timeframes=timeframe_items[:aggregate_limit],
        regimes=regime_items[:aggregate_limit],
        trend_alignments=trend_items[:aggregate_limit],
        directions=direction_items[:aggregate_limit],
        hold_conditions=hold_condition_items[:aggregate_limit],
        close_outcomes=close_outcome_items[:aggregate_limit],
        feature_flags=flag_items,
    )


def build_signal_performance_report(
    session: Session,
    *,
    window_hours: int = 24,
    limit: int = 12,
) -> SignalPerformanceReportResponse:
    windows = [
        _build_window_report(session, window_label="24h", window_hours=24, aggregate_limit=limit, decision_limit=limit),
        _build_window_report(session, window_label="7d", window_hours=24 * 7, aggregate_limit=limit, decision_limit=limit),
        _build_window_report(session, window_label="30d", window_hours=24 * 30, aggregate_limit=limit, decision_limit=limit),
    ]
    primary_window = next((item for item in windows if item.window_hours == window_hours), windows[0])
    items = [
        SignalPerformanceEntry(
            rationale_code=item.key,
            decisions=item.decisions,
            approvals=item.approvals,
            orders=item.orders,
            fills=item.fills,
            holds=item.holds,
            longs=item.longs,
            shorts=item.shorts,
            reduces=item.reduces,
            exits=item.exits,
            wins=item.wins,
            losses=item.losses,
            realized_pnl_total=item.realized_pnl_total,
            fee_total=item.fee_total,
            net_realized_pnl_total=item.net_realized_pnl_total,
            average_slippage_pct=item.average_slippage_pct,
            average_holding_minutes=item.average_holding_minutes,
            holding_over_plan_count=item.holding_over_plan_count,
            open_positions=item.open_positions,
            closed_positions=item.closed_positions,
            latest_seen_at=item.latest_seen_at,
        )
        for item in primary_window.rationale_codes
    ]
    return SignalPerformanceReportResponse(
        generated_at=utcnow_naive(),
        window_hours=primary_window.window_hours,
        items=items[:limit],
        windows=windows,
    )


def _categorize_competitor_note(note: CompetitorNote) -> tuple[str, str]:
    haystack = " ".join([note.source, note.note, *note.tags]).lower()
    if any(token in haystack for token in ("dashboard", "ui", "ux", "layout", "화면", "대시보드")):
        return "dashboard", "운영 화면의 정보 구조와 모니터링 흐름을 강화하는 방향"
    if any(token in haystack for token in ("risk", "guard", "stop", "loss", "리스크", "보호")):
        return "risk", "리스크 통제와 보호 주문 체계를 강화하는 방향"
    if any(token in haystack for token in ("alert", "notification", "알림", "공지")):
        return "alerting", "중요 이벤트 감지와 운영 대응 속도를 높이는 방향"
    if any(token in haystack for token in ("execution", "order", "fill", "slippage", "체결", "주문")):
        return "execution", "주문 체결과 실행 품질 추적을 강화하는 방향"
    if any(token in haystack for token in ("ai", "signal", "model", "agent", "신호", "에이전트")):
        return "signal-ai", "AI 신호와 해석 가시성을 강화하는 방향"
    return "general", "제품 차별점과 운영 관찰 포인트를 보강하는 방향"


def _summarize_note(note: str, max_length: int = 120) -> str:
    compact = " ".join(note.split())
    if len(compact) <= max_length:
        return compact
    return compact[: max_length - 1].rstrip() + "…"


def build_structured_competitor_notes(
    session: Session,
    *,
    limit: int = 20,
) -> StructuredCompetitorNotesResponse:
    rows = list(session.scalars(select(CompetitorNote).order_by(CompetitorNote.created_at.desc()).limit(limit)))
    items: list[StructuredCompetitorNote] = []
    category_breakdown: dict[str, int] = defaultdict(int)

    for row in rows:
        category, differentiation = _categorize_competitor_note(row)
        category_breakdown[category] += 1
        items.append(
            StructuredCompetitorNote(
                id=row.id,
                source=row.source,
                category=category,
                differentiation=differentiation,
                summary=_summarize_note(row.note),
                tags=list(row.tags),
                created_at=row.created_at,
            )
        )

    items.sort(key=lambda item: item.created_at, reverse=True)
    return StructuredCompetitorNotesResponse(
        generated_at=utcnow_naive(),
        category_breakdown=dict(sorted(category_breakdown.items(), key=lambda item: item[0])),
        items=items,
    )
