from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from trading_mvp.models import AgentRun, Execution, Order, Position
from trading_mvp.schemas import StrategyEngineBucketEntry, StrategyEngineReportResponse
from trading_mvp.services.intent_semantics import infer_intent_semantics
from trading_mvp.services.performance_reporting import _extract_analysis_context
from trading_mvp.time_utils import utcnow_naive


@dataclass(slots=True)
class _EngineBucketAccumulator:
    decisions: int = 0
    traded_decisions: int = 0
    pnls: list[float] = field(default_factory=list)
    signed_slippages: list[float] = field(default_factory=list)
    time_to_profit_minutes: list[float] = field(default_factory=list)
    drawdown_impacts: list[float] = field(default_factory=list)
    latest_decision_at: datetime | None = None


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        if value in {None, ""}:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_dict(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _strategy_engine_payload(metadata: dict[str, object]) -> dict[str, object]:
    return _as_dict(metadata.get("strategy_engine"))


def _selected_engine_name(metadata: dict[str, object], output_payload: dict[str, object]) -> str:
    strategy_engine = _strategy_engine_payload(metadata)
    selected = _as_dict(strategy_engine.get("selected_engine"))
    engine_name = str(selected.get("engine_name") or "").strip()
    if engine_name:
        return engine_name
    entry_mode = str(output_payload.get("entry_mode") or "").lower()
    decision = str(output_payload.get("decision") or "").lower()
    rationale_codes = {str(code) for code in output_payload.get("rationale_codes") or [] if code}
    if rationale_codes & {"PROTECTION_REQUIRED", "PROTECTION_RECOVERY", "PROTECTION_RESTORE"}:
        return "protection_reduce_engine"
    if entry_mode == "breakout_confirm":
        return "breakout_exception_engine"
    if any("CONTINUATION" in code for code in rationale_codes):
        return "trend_continuation_engine"
    if entry_mode == "pullback_confirm":
        return "trend_pullback_engine"
    if decision in {"reduce", "exit"}:
        return "protection_reduce_engine"
    return "unspecified_engine"


def _session_context(metadata: dict[str, object]) -> tuple[str, str]:
    strategy_engine = _strategy_engine_payload(metadata)
    session_context = _as_dict(strategy_engine.get("session_context"))
    session_label = str(session_context.get("session_label") or "unknown")
    time_of_day_bucket = str(session_context.get("time_of_day_bucket") or "unknown")
    return session_label, time_of_day_bucket


def _scenario(output_payload: dict[str, object]) -> str:
    decision = str(output_payload.get("decision") or "").lower()
    if decision in {"hold", "reduce", "exit"}:
        return decision
    entry_mode = str(output_payload.get("entry_mode") or "").lower()
    rationale_codes = {str(code) for code in output_payload.get("rationale_codes") or [] if code}
    if rationale_codes & {"PROTECTION_REQUIRED", "PROTECTION_RECOVERY", "PROTECTION_RESTORE"}:
        return "protection_restore"
    if entry_mode == "pullback_confirm" or any("PULLBACK" in code for code in rationale_codes):
        return "pullback_entry"
    return "trend_follow"


def _execution_policy_profile(orders: list[Order], metadata: dict[str, object]) -> str:
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


def _position_payload(position: Position) -> dict[str, object]:
    metadata = _as_dict(position.metadata_json)
    for key in ("capital_efficiency", "position_management", "replay", "intratrade"):
        payload = _as_dict(metadata.get(key))
        if payload:
            return payload
    return {}


def _time_to_profit_minutes(positions: list[Position]) -> float | None:
    values: list[float] = []
    for position in positions:
        payload = _position_payload(position)
        value = _safe_float(payload.get("time_to_0_25r_minutes"), default=-1.0)
        if value < 0:
            value = _safe_float(payload.get("time_to_0_5r_minutes"), default=-1.0)
        if value >= 0:
            values.append(value)
    if not values:
        return None
    return min(values)


def _drawdown_impact(positions: list[Position]) -> float:
    values: list[float] = []
    for position in positions:
        payload = _position_payload(position)
        mae_r = _safe_float(payload.get("mae_r"), default=0.0)
        if mae_r != 0.0:
            values.append(abs(mae_r))
    if not values:
        return 0.0
    return sum(values) / max(len(values), 1)


def _classification(
    *,
    traded_decisions: int,
    expectancy: float,
    net_pnl_after_fees: float,
    avg_signed_slippage_bps: float,
    average_drawdown_impact: float,
) -> tuple[str, list[str], float]:
    if traded_decisions == 0:
        return "mixed", ["NO_TRADED_SAMPLE"], 0.5
    expectancy_score = max(0.0, min(1.0, 0.5 + (expectancy / 40.0)))
    net_score = max(0.0, min(1.0, 0.5 + (net_pnl_after_fees / 120.0)))
    slippage_score = max(0.0, min(1.0, 0.88 - (min(max(avg_signed_slippage_bps, 0.0), 18.0) / 18.0 * 0.5)))
    drawdown_score = max(0.0, min(1.0, 0.82 - (min(max(average_drawdown_impact, 0.0), 2.0) / 2.0 * 0.42)))
    efficiency_score = (expectancy_score * 0.38) + (net_score * 0.32) + (slippage_score * 0.18) + (drawdown_score * 0.12)
    reasons: list[str] = []
    if expectancy > 0:
        reasons.append("POSITIVE_EXPECTANCY")
    if net_pnl_after_fees > 0:
        reasons.append("POSITIVE_NET_PNL_AFTER_FEES")
    if avg_signed_slippage_bps >= 12.0:
        reasons.append("ADVERSE_SIGNED_SLIPPAGE")
    if average_drawdown_impact >= 0.8:
        reasons.append("ELEVATED_DRAWDOWN_IMPACT")
    if efficiency_score >= 0.64 and expectancy > 0 and net_pnl_after_fees > 0:
        return "strong", reasons or ["STRONG_ENGINE_BUCKET"], round(efficiency_score, 6)
    if efficiency_score <= 0.44 and expectancy <= 0 and net_pnl_after_fees <= 0:
        return "weak", reasons or ["WEAK_ENGINE_BUCKET"], round(efficiency_score, 6)
    return "mixed", reasons or ["MIXED_ENGINE_BUCKET"], round(efficiency_score, 6)


def build_strategy_engine_bucket_report(
    session: Session,
    *,
    lookback_days: int = 21,
    limit: int = 256,
) -> StrategyEngineReportResponse:
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
        return StrategyEngineReportResponse(
            generated_at=now,
            lookback_days=lookback_days,
            decisions_analyzed=0,
            traded_decisions=0,
            bucket_reports=[],
            strong_engine_bucket_keys=[],
            weak_engine_bucket_keys=[],
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

    accumulators: dict[str, _EngineBucketAccumulator] = defaultdict(_EngineBucketAccumulator)
    bucket_context: dict[str, dict[str, str]] = {}
    traded_decisions = 0

    for decision_row in decision_rows:
        output_payload = _as_dict(decision_row.output_payload)
        metadata = _as_dict(decision_row.metadata_json)
        symbol = str(output_payload.get("symbol") or "UNKNOWN").upper()
        timeframe = str(output_payload.get("timeframe") or "UNKNOWN")
        decision_code = str(output_payload.get("decision") or "hold").lower()
        strategy_engine = _selected_engine_name(metadata, output_payload)
        session_label, time_of_day_bucket = _session_context(metadata)
        scenario = _scenario(output_payload)
        intent_semantics = infer_intent_semantics(output_payload, metadata)
        regime, trend_alignment, *_rest = _extract_analysis_context(decision_row)
        linked_orders = orders_by_decision.get(decision_row.id, [])
        linked_executions = [
            execution
            for order in linked_orders
            for execution in executions_by_order.get(order.id, [])
        ]
        execution_policy_profile = _execution_policy_profile(linked_orders, metadata)
        bucket_key = "|".join(
            [
                strategy_engine,
                symbol,
                timeframe,
                scenario,
                regime,
                trend_alignment,
                str(output_payload.get("entry_mode") or "none").lower(),
                execution_policy_profile,
                session_label,
                time_of_day_bucket,
            ]
        )
        accumulator = accumulators[bucket_key]
        accumulator.decisions += 1
        if accumulator.latest_decision_at is None or decision_row.created_at > accumulator.latest_decision_at:
            accumulator.latest_decision_at = decision_row.created_at
        bucket_context[bucket_key] = {
            "strategy_engine": strategy_engine,
            "symbol": symbol,
            "timeframe": timeframe,
            "scenario": scenario,
            "regime": regime,
            "trend_alignment": trend_alignment,
            "entry_mode": str(output_payload.get("entry_mode") or "none").lower(),
            "execution_policy_profile": execution_policy_profile,
            "session_label": session_label,
            "time_of_day_bucket": time_of_day_bucket,
        }
        if (
            decision_code not in {"long", "short", "reduce", "exit"}
            or not linked_executions
            or bool(intent_semantics.get("analytics_excluded_from_entry_stats"))
        ):
            continue
        traded_decisions += 1
        accumulator.traded_decisions += 1
        net_pnl_after_fees = sum(
            _safe_float(execution.realized_pnl) - _safe_float(execution.fee_paid)
            for execution in linked_executions
        )
        avg_signed_slippage_bps = sum(
            _safe_float(_as_dict(execution.payload).get("signed_slippage_bps"))
            for execution in linked_executions
        ) / max(len(linked_executions), 1)
        positions = [
            positions_by_id[int(order.position_id)]
            for order in linked_orders
            if order.position_id is not None and int(order.position_id) in positions_by_id
        ]
        time_to_profit = _time_to_profit_minutes(positions)
        drawdown_impact = _drawdown_impact(positions)
        accumulator.pnls.append(net_pnl_after_fees)
        accumulator.signed_slippages.append(avg_signed_slippage_bps)
        if time_to_profit is not None:
            accumulator.time_to_profit_minutes.append(time_to_profit)
        accumulator.drawdown_impacts.append(drawdown_impact)

    bucket_reports: list[StrategyEngineBucketEntry] = []
    strong_bucket_keys: list[str] = []
    weak_bucket_keys: list[str] = []
    for bucket_key, accumulator in accumulators.items():
        pnls = accumulator.pnls
        wins = [pnl for pnl in pnls if pnl > 0]
        losses = [abs(pnl) for pnl in pnls if pnl < 0]
        hit_rate = len(wins) / max(accumulator.traded_decisions, 1)
        loss_rate = len(losses) / max(accumulator.traded_decisions, 1)
        avg_win = sum(wins) / max(len(wins), 1) if wins else 0.0
        avg_loss = sum(losses) / max(len(losses), 1) if losses else 0.0
        expectancy = (hit_rate * avg_win) - (loss_rate * avg_loss)
        net_pnl_after_fees = sum(pnls)
        avg_signed_slippage_bps = sum(accumulator.signed_slippages) / max(len(accumulator.signed_slippages), 1) if accumulator.signed_slippages else 0.0
        average_time_to_profit_minutes = (
            round(sum(accumulator.time_to_profit_minutes) / max(len(accumulator.time_to_profit_minutes), 1), 4)
            if accumulator.time_to_profit_minutes
            else None
        )
        average_drawdown_impact = (
            round(sum(accumulator.drawdown_impacts) / max(len(accumulator.drawdown_impacts), 1), 4)
            if accumulator.drawdown_impacts
            else 0.0
        )
        classification, reasons, efficiency_score = _classification(
            traded_decisions=accumulator.traded_decisions,
            expectancy=expectancy,
            net_pnl_after_fees=net_pnl_after_fees,
            avg_signed_slippage_bps=avg_signed_slippage_bps,
            average_drawdown_impact=average_drawdown_impact,
        )
        if classification == "strong":
            strong_bucket_keys.append(bucket_key)
        elif classification == "weak":
            weak_bucket_keys.append(bucket_key)
        context = bucket_context[bucket_key]
        bucket_reports.append(
            StrategyEngineBucketEntry(
                bucket_key=bucket_key,
                strategy_engine=context["strategy_engine"],
                symbol=context["symbol"],
                timeframe=context["timeframe"],
                scenario=context["scenario"],
                regime=context["regime"],
                trend_alignment=context["trend_alignment"],
                entry_mode=context["entry_mode"],
                execution_policy_profile=context["execution_policy_profile"],
                session_label=context["session_label"],
                time_of_day_bucket=context["time_of_day_bucket"],
                decisions=accumulator.decisions,
                traded_decisions=accumulator.traded_decisions,
                expectancy=round(expectancy, 4),
                net_pnl_after_fees=round(net_pnl_after_fees, 4),
                avg_signed_slippage_bps=round(avg_signed_slippage_bps, 4),
                average_time_to_profit_minutes=average_time_to_profit_minutes,
                average_drawdown_impact=average_drawdown_impact,
                efficiency_score=efficiency_score,
                classification=classification,
                reasons=reasons,
                latest_decision_at=accumulator.latest_decision_at,
            )
        )
    bucket_reports.sort(key=lambda item: (item.classification != "strong", -item.efficiency_score, -item.net_pnl_after_fees))
    return StrategyEngineReportResponse(
        generated_at=now,
        lookback_days=lookback_days,
        decisions_analyzed=len(decision_rows),
        traded_decisions=traded_decisions,
        bucket_reports=bucket_reports,
        strong_engine_bucket_keys=strong_bucket_keys,
        weak_engine_bucket_keys=weak_bucket_keys,
    )
