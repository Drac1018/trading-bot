from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from trading_mvp.models import AgentRun, Execution, Order, Position
from trading_mvp.schemas import CapitalEfficiencyBucketEntry, CapitalEfficiencyReportResponse
from trading_mvp.services.intent_semantics import infer_intent_semantics
from trading_mvp.services.performance_reporting import _extract_analysis_context
from trading_mvp.time_utils import utcnow_naive


@dataclass(slots=True)
class _BucketAccumulator:
    decisions: int = 0
    traded_decisions: int = 0
    total_exposure_hours: float = 0.0
    gross_pnl: float = 0.0
    net_pnl_after_fees: float = 0.0
    time_to_0_25r_minutes: list[float] = field(default_factory=list)
    time_to_0_5r_minutes: list[float] = field(default_factory=list)
    time_to_fail_minutes: list[float] = field(default_factory=list)
    reached_0_25r_count: int = 0
    reached_0_5r_count: int = 0
    fail_before_0_25r_count: int = 0


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        if value in {None, ""}:
            return default
        return float(value)
    except (TypeError, ValueError):
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


def _as_dict(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _normalized_entry_mode(*, decision: str, output_payload: dict[str, object], metadata: dict[str, object]) -> str:
    entry_mode = str(output_payload.get("entry_mode") or "").lower()
    if entry_mode and entry_mode not in {"none", "null"}:
        return entry_mode
    selection_context = _as_dict(metadata.get("selection_context"))
    for key in ("entry_mode", "candidate_entry_mode", "planned_entry_mode"):
        value = str(selection_context.get(key) or "").lower()
        if value and value not in {"none", "null"}:
            return value
    if decision == "hold":
        scenario = str(selection_context.get("expected_scenario") or selection_context.get("scenario") or "").lower()
        if scenario == "pullback_entry":
            return "pullback_confirm"
    rationale_codes = [str(code) for code in output_payload.get("rationale_codes") or [] if code]
    if any("PULLBACK" in code for code in rationale_codes):
        return "pullback_confirm"
    if any("BREAKOUT" in code for code in rationale_codes):
        return "breakout_confirm"
    if any("CONTINUATION" in code for code in rationale_codes):
        return "continuation"
    return "none"


def _normalized_scenario(*, decision: str, entry_mode: str, output_payload: dict[str, object], metadata: dict[str, object]) -> str:
    decision_code = str(decision or "").lower()
    if decision_code in {"reduce", "exit", "hold"}:
        selection_context = _as_dict(metadata.get("selection_context"))
        hinted = str(selection_context.get("expected_scenario") or selection_context.get("scenario") or "").lower()
        if hinted:
            return hinted
        return decision_code
    rationale_codes = {str(code) for code in output_payload.get("rationale_codes") or [] if code}
    if rationale_codes & {"PROTECTION_REQUIRED", "PROTECTION_RECOVERY", "PROTECTION_RESTORE"}:
        return "protection_restore"
    if entry_mode == "pullback_confirm" or any("PULLBACK" in code for code in rationale_codes):
        return "pullback_entry"
    return "trend_follow"


def _execution_policy_profile(*, orders: list[Order], metadata: dict[str, object]) -> str:
    for order in orders:
        order_metadata = _as_dict(order.metadata_json)
        for key in ("execution_quality", "execution_policy"):
            payload = _as_dict(order_metadata.get(key))
            profile = str(payload.get("policy_profile") or "")
            if profile:
                return profile
    selection_context = _as_dict(metadata.get("selection_context"))
    for key in ("execution_policy_profile", "policy_profile", "candidate_policy_profile"):
        profile = str(selection_context.get(key) or "")
        if profile:
            return profile
    return "UNSPECIFIED"


def _close_outcome(orders: list[Order]) -> str:
    order_types = {str(order.order_type or "").lower() for order in orders}
    if any("take_profit" in order_type for order_type in order_types):
        return "take_profit"
    if any("stop" in order_type for order_type in order_types):
        return "stop_loss"
    return "manual_close"


def _position_exposure_hours(position: Position, *, now) -> float:
    end_at = position.closed_at or now
    return max((end_at - position.opened_at).total_seconds() / 3600.0, 0.0)


def _position_efficiency_payload(position: Position) -> dict[str, object]:
    metadata = _as_dict(position.metadata_json)
    for key in ("capital_efficiency", "analytics", "position_management", "replay", "intratrade"):
        payload = _as_dict(metadata.get(key))
        if payload:
            return payload
    return {}


def _position_time_metric(position: Position, *, key: str, close_outcome: str) -> float | None:
    payload = _position_efficiency_payload(position)
    value = _safe_float(payload.get(key), default=-1.0)
    if value >= 0:
        return value
    exposure_minutes = _position_exposure_hours(position, now=utcnow_naive()) * 60.0
    if key == "time_to_fail_minutes" and close_outcome == "stop_loss":
        return exposure_minutes
    reached_key = "reached_0_25r" if key == "time_to_0_25r_minutes" else "reached_0_5r"
    if key in {"time_to_0_25r_minutes", "time_to_0_5r_minutes"} and _safe_bool(payload.get(reached_key)):
        return exposure_minutes
    return None


def _position_reached_threshold(position: Position, *, key: str) -> bool:
    payload = _position_efficiency_payload(position)
    if key in payload:
        return _safe_bool(payload.get(key))
    if key == "reached_0_25r":
        return _safe_float(payload.get("mfe_r")) >= 0.25
    if key == "reached_0_5r":
        return _safe_float(payload.get("mfe_r")) >= 0.5
    if key == "failed_before_0_25r":
        return _safe_bool(payload.get("failed_before_0_25r")) or (_safe_float(payload.get("mae_r")) <= -1.0)
    return False


def _aggregate_position_metric(positions: list[Position], *, key: str, close_outcome: str) -> float | None:
    values = [
        metric
        for position in positions
        if (metric := _position_time_metric(position, key=key, close_outcome=close_outcome)) is not None
    ]
    if not values:
        return None
    return min(values)


def _classification(
    *,
    traded_decisions: int,
    total_exposure_hours: float,
    net_pnl_after_fees_per_hour: float,
    fail_before_0_25r_rate: float,
    capital_slot_occupancy_efficiency: float,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if traded_decisions == 0 or total_exposure_hours <= 0:
        return "neutral", ["NO_TRADED_SAMPLE"]
    if net_pnl_after_fees_per_hour > 0 and capital_slot_occupancy_efficiency > 0:
        reasons.extend(["POSITIVE_NET_PER_HOUR", "EARLY_PROGRESS_CAPTURED"])
        return "efficient", reasons
    if net_pnl_after_fees_per_hour < 0:
        reasons.append("NEGATIVE_NET_PER_HOUR")
    if fail_before_0_25r_rate >= 0.5:
        reasons.append("FAILS_BEFORE_0_25R")
    if reasons:
        return "inefficient", reasons
    return "neutral", ["MIXED_EFFICIENCY"]


def build_capital_efficiency_report(
    session: Session,
    *,
    lookback_days: int = 21,
    limit: int = 256,
) -> CapitalEfficiencyReportResponse:
    now = utcnow_naive()
    since = now - timedelta(days=lookback_days)
    decision_rows = list(
        session.scalars(
            select(AgentRun)
            .where(
                AgentRun.role == "trading_decision",
                AgentRun.created_at >= since,
            )
            .order_by(desc(AgentRun.created_at))
            .limit(limit)
        )
    )
    if not decision_rows:
        return CapitalEfficiencyReportResponse(
            generated_at=now,
            lookback_days=lookback_days,
            decisions_analyzed=0,
            traded_decisions=0,
            total_exposure_hours=0.0,
            bucket_reports=[],
            efficient_bucket_keys=[],
            inefficient_bucket_keys=[],
        )

    decision_ids = [row.id for row in decision_rows]
    order_rows = list(session.scalars(select(Order).where(Order.decision_run_id.in_(decision_ids))))
    orders_by_decision: dict[int, list[Order]] = defaultdict(list)
    for order in order_rows:
        if order.decision_run_id is not None:
            orders_by_decision[int(order.decision_run_id)].append(order)

    order_ids = [order.id for order in order_rows]
    execution_rows = list(session.scalars(select(Execution).where(Execution.order_id.in_(order_ids)))) if order_ids else []
    executions_by_order: dict[int, list[Execution]] = defaultdict(list)
    for execution in execution_rows:
        if execution.order_id is not None:
            executions_by_order[int(execution.order_id)].append(execution)

    position_ids = sorted({int(order.position_id) for order in order_rows if order.position_id is not None})
    positions_by_id: dict[int, Position] = {}
    if position_ids:
        positions_by_id = {
            position.id: position
            for position in session.scalars(select(Position).where(Position.id.in_(position_ids)))
        }

    accumulators: dict[str, _BucketAccumulator] = defaultdict(_BucketAccumulator)
    bucket_context: dict[str, dict[str, str]] = {}
    traded_decisions = 0
    total_exposure_hours = 0.0

    for decision_row in decision_rows:
        output_payload = _as_dict(decision_row.output_payload)
        metadata = _as_dict(decision_row.metadata_json)
        symbol = str(output_payload.get("symbol") or "UNKNOWN").upper()
        timeframe = str(output_payload.get("timeframe") or "UNKNOWN")
        decision = str(output_payload.get("decision") or "hold").lower()
        intent_semantics = infer_intent_semantics(output_payload, metadata)
        entry_mode = _normalized_entry_mode(decision=decision, output_payload=output_payload, metadata=metadata)
        scenario = _normalized_scenario(
            decision=decision,
            entry_mode=entry_mode,
            output_payload=output_payload,
            metadata=metadata,
        )
        regime, trend_alignment, *_ = _extract_analysis_context(decision_row)
        orders = orders_by_decision.get(decision_row.id, [])
        execution_policy_profile = _execution_policy_profile(orders=orders, metadata=metadata)
        bucket_key = "|".join(
            [
                symbol,
                timeframe,
                scenario,
                regime,
                entry_mode,
                execution_policy_profile,
            ]
        )
        accumulator = accumulators[bucket_key]
        accumulator.decisions += 1
        bucket_context[bucket_key] = {
            "symbol": symbol,
            "timeframe": timeframe,
            "scenario": scenario,
            "regime": regime,
            "entry_mode": entry_mode,
            "execution_policy_profile": execution_policy_profile,
        }
        if not orders or bool(intent_semantics.get("analytics_excluded_from_entry_stats")):
            continue

        linked_positions = [
            positions_by_id[position_id]
            for position_id in {int(order.position_id) for order in orders if order.position_id is not None}
            if position_id in positions_by_id
        ]
        linked_executions = [
            execution
            for order in orders
            for execution in executions_by_order.get(order.id, [])
        ]
        if not linked_positions and not linked_executions:
            continue

        gross_pnl = sum(_safe_float(execution.realized_pnl) for execution in linked_executions)
        if not linked_executions and linked_positions:
            gross_pnl = sum(_safe_float(position.realized_pnl) for position in linked_positions)
        fee_total = sum(_safe_float(execution.fee_paid) for execution in linked_executions)
        net_pnl_after_fees = gross_pnl - fee_total
        exposure_hours = sum(_position_exposure_hours(position, now=now) for position in linked_positions)
        close_outcome = _close_outcome(orders)

        accumulator.traded_decisions += 1
        accumulator.gross_pnl += gross_pnl
        accumulator.net_pnl_after_fees += net_pnl_after_fees
        accumulator.total_exposure_hours += exposure_hours
        traded_decisions += 1
        total_exposure_hours += exposure_hours

        time_to_0_25r = _aggregate_position_metric(
            linked_positions,
            key="time_to_0_25r_minutes",
            close_outcome=close_outcome,
        )
        time_to_0_5r = _aggregate_position_metric(
            linked_positions,
            key="time_to_0_5r_minutes",
            close_outcome=close_outcome,
        )
        time_to_fail = _aggregate_position_metric(
            linked_positions,
            key="time_to_fail_minutes",
            close_outcome=close_outcome,
        )
        if time_to_0_25r is not None:
            accumulator.time_to_0_25r_minutes.append(time_to_0_25r)
        if time_to_0_5r is not None:
            accumulator.time_to_0_5r_minutes.append(time_to_0_5r)
        if time_to_fail is not None:
            accumulator.time_to_fail_minutes.append(time_to_fail)

        reached_0_25r = any(_position_reached_threshold(position, key="reached_0_25r") for position in linked_positions)
        reached_0_5r = any(_position_reached_threshold(position, key="reached_0_5r") for position in linked_positions)
        failed_before_0_25r = any(
            _position_reached_threshold(position, key="failed_before_0_25r") for position in linked_positions
        )
        if reached_0_25r:
            accumulator.reached_0_25r_count += 1
        if reached_0_5r:
            accumulator.reached_0_5r_count += 1
        if failed_before_0_25r:
            accumulator.fail_before_0_25r_count += 1

    bucket_reports: list[CapitalEfficiencyBucketEntry] = []
    efficient_bucket_keys: list[str] = []
    inefficient_bucket_keys: list[str] = []

    for bucket_key, accumulator in accumulators.items():
        context = bucket_context[bucket_key]
        total_hours = accumulator.total_exposure_hours
        traded = accumulator.traded_decisions
        pnl_per_exposure_hour = accumulator.gross_pnl / total_hours if total_hours > 0 else 0.0
        net_pnl_after_fees_per_hour = accumulator.net_pnl_after_fees / total_hours if total_hours > 0 else 0.0
        reached_0_25r_rate = accumulator.reached_0_25r_count / traded if traded else 0.0
        reached_0_5r_rate = accumulator.reached_0_5r_count / traded if traded else 0.0
        fail_before_0_25r_rate = accumulator.fail_before_0_25r_count / traded if traded else 0.0
        capital_slot_occupancy_efficiency = accumulator.reached_0_25r_count / total_hours if total_hours > 0 else 0.0
        classification, reasons = _classification(
            traded_decisions=traded,
            total_exposure_hours=total_hours,
            net_pnl_after_fees_per_hour=net_pnl_after_fees_per_hour,
            fail_before_0_25r_rate=fail_before_0_25r_rate,
            capital_slot_occupancy_efficiency=capital_slot_occupancy_efficiency,
        )
        entry = CapitalEfficiencyBucketEntry(
            bucket_key=bucket_key,
            symbol=context["symbol"],
            timeframe=context["timeframe"],
            scenario=context["scenario"],
            regime=context["regime"],
            entry_mode=context["entry_mode"],
            execution_policy_profile=context["execution_policy_profile"],
            decisions=accumulator.decisions,
            traded_decisions=traded,
            total_exposure_hours=round(total_hours, 6),
            gross_pnl=round(accumulator.gross_pnl, 6),
            net_pnl_after_fees=round(accumulator.net_pnl_after_fees, 6),
            pnl_per_exposure_hour=round(pnl_per_exposure_hour, 6),
            net_pnl_after_fees_per_hour=round(net_pnl_after_fees_per_hour, 6),
            average_time_to_0_25r_minutes=(
                round(sum(accumulator.time_to_0_25r_minutes) / len(accumulator.time_to_0_25r_minutes), 6)
                if accumulator.time_to_0_25r_minutes
                else None
            ),
            average_time_to_0_5r_minutes=(
                round(sum(accumulator.time_to_0_5r_minutes) / len(accumulator.time_to_0_5r_minutes), 6)
                if accumulator.time_to_0_5r_minutes
                else None
            ),
            average_time_to_fail_minutes=(
                round(sum(accumulator.time_to_fail_minutes) / len(accumulator.time_to_fail_minutes), 6)
                if accumulator.time_to_fail_minutes
                else None
            ),
            reached_0_25r_rate=round(reached_0_25r_rate, 6),
            reached_0_5r_rate=round(reached_0_5r_rate, 6),
            fail_before_0_25r_rate=round(fail_before_0_25r_rate, 6),
            capital_slot_occupancy_efficiency=round(capital_slot_occupancy_efficiency, 6),
            efficiency_classification=classification,  # type: ignore[arg-type]
            reasons=reasons,
        )
        bucket_reports.append(entry)
        if classification == "efficient":
            efficient_bucket_keys.append(bucket_key)
        elif classification == "inefficient":
            inefficient_bucket_keys.append(bucket_key)

    bucket_reports.sort(
        key=lambda item: (
            item.net_pnl_after_fees_per_hour,
            item.capital_slot_occupancy_efficiency,
            item.total_exposure_hours,
        ),
        reverse=True,
    )

    return CapitalEfficiencyReportResponse(
        generated_at=now,
        lookback_days=lookback_days,
        decisions_analyzed=len(decision_rows),
        traded_decisions=traded_decisions,
        total_exposure_hours=round(total_exposure_hours, 6),
        bucket_reports=bucket_reports,
        efficient_bucket_keys=efficient_bucket_keys,
        inefficient_bucket_keys=inefficient_bucket_keys,
    )
