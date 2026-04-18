from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from trading_mvp.models import AgentRun, Execution, Order, PendingEntryPlan
from trading_mvp.schemas import (
    RulePruningBucketEntry,
    RulePruningCandidate,
    RulePruningReportResponse,
)
from trading_mvp.services.performance_reporting import _extract_analysis_context
from trading_mvp.time_utils import utcnow_naive

MIN_CLASSIFICATION_SAMPLE = 4
LATE_TRIGGER_KILL_THRESHOLD = 0.35
FAILURE_CLUSTER_KEEP_THRESHOLD = 0.35
ADVERSE_SIGNED_SLIPPAGE_THRESHOLD = 12.0
HIGH_HOLD_RATE_THRESHOLD = 0.75

PROTECTIVE_RULE_KEYS = {
    "setup_cluster_auto_disable",
    "idle_ai_skip",
    "confirm_quality_filter",
    "derivatives_entry_filter",
    "lead_lag_filter",
    "universe_breadth_filter",
    "decision_agreement_soft_limit",
}


@dataclass(slots=True)
class _MetricAccumulator:
    decisions: int = 0
    traded_decisions: int = 0
    holds: int = 0
    late_trigger_hits: int = 0
    failure_cluster_hits: int = 0
    trade_nets: list[float] = field(default_factory=list)
    signed_slippage_bps: list[float] = field(default_factory=list)


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        if value in {None, ""}:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_dict(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _signed_slippage_bps(execution_row: Execution) -> float:
    payload = _as_dict(execution_row.payload)
    if "signed_slippage_bps" in payload:
        return _safe_float(payload.get("signed_slippage_bps"))
    if "signed_slippage_pct" in payload:
        return _safe_float(payload.get("signed_slippage_pct")) * 10_000.0
    return 0.0


def _scenario_for_decision(decision: str, entry_mode: str, rationale_codes: list[str]) -> str:
    decision_code = str(decision or "").lower()
    if decision_code in {"reduce", "exit", "hold"}:
        return decision_code
    rationale_set = {str(code) for code in rationale_codes if code}
    if rationale_set & {"PROTECTION_REQUIRED", "PROTECTION_RECOVERY", "PROTECTION_RESTORE"}:
        return "protection_restore"
    if entry_mode == "pullback_confirm" or any("PULLBACK" in code for code in rationale_set):
        return "pullback_entry"
    return "trend_follow"


def _parse_cluster_key(cluster_key: str) -> dict[str, str]:
    parts = [part.strip() for part in str(cluster_key or "").split("|")]
    if len(parts) < 6:
        return {}
    return {
        "symbol": parts[0],
        "timeframe": parts[1],
        "scenario": parts[2],
        "entry_mode": parts[3],
        "regime": parts[4],
        "trend_alignment": parts[5],
    }


def _normalized_entry_mode(
    *,
    decision: str,
    entry_mode: str,
    rationale_codes: list[str],
    metadata: dict[str, object],
) -> str:
    normalized = str(entry_mode or "none").lower()
    if normalized not in {"", "none", "null"}:
        return normalized
    selection_context = _as_dict(metadata.get("selection_context"))
    for key in ("entry_mode", "candidate_entry_mode", "planned_entry_mode"):
        candidate_mode = str(selection_context.get(key) or "").lower()
        if candidate_mode and candidate_mode not in {"none", "null"}:
            return candidate_mode
    setup_cluster_state = _as_dict(metadata.get("setup_cluster_state"))
    cluster_entry_mode = _parse_cluster_key(str(setup_cluster_state.get("cluster_key") or "")).get("entry_mode")
    if cluster_entry_mode:
        return str(cluster_entry_mode).lower()
    rationale_set = {str(code) for code in rationale_codes if code}
    if any("PULLBACK" in code for code in rationale_set):
        return "pullback_confirm"
    if any("BREAKOUT" in code for code in rationale_set):
        return "breakout_confirm"
    if any("CONTINUATION" in code for code in rationale_set):
        return "continuation"
    if decision == "hold":
        scenario_hint = str(selection_context.get("expected_scenario") or selection_context.get("scenario") or "").lower()
        if scenario_hint == "pullback_entry":
            return "pullback_confirm"
    return "none"


def _normalized_scenario(
    *,
    decision: str,
    entry_mode: str,
    rationale_codes: list[str],
    metadata: dict[str, object],
) -> str:
    decision_code = str(decision or "").lower()
    if decision_code in {"reduce", "exit"}:
        return decision_code
    selection_context = _as_dict(metadata.get("selection_context"))
    setup_cluster_state = _as_dict(metadata.get("setup_cluster_state"))
    if decision_code == "hold":
        scenario_hint = str(selection_context.get("expected_scenario") or selection_context.get("scenario") or "").lower()
        if scenario_hint:
            return scenario_hint
        cluster_scenario = _parse_cluster_key(str(setup_cluster_state.get("cluster_key") or "")).get("scenario")
        if cluster_scenario:
            return str(cluster_scenario).lower()
        if entry_mode == "pullback_confirm":
            return "pullback_entry"
        if entry_mode in {"breakout_confirm", "continuation"}:
            return "trend_follow"
    return _scenario_for_decision(decision_code, entry_mode, rationale_codes)


def _execution_policy_profile(orders: list[Order]) -> str:
    for order in orders:
        metadata = _as_dict(order.metadata_json)
        execution_quality = _as_dict(metadata.get("execution_quality"))
        policy_profile = str(execution_quality.get("policy_profile") or "")
        if policy_profile:
            return policy_profile
        execution_policy = _as_dict(metadata.get("execution_policy"))
        policy_profile = str(execution_policy.get("policy_profile") or "")
        if policy_profile:
            return policy_profile
    return "UNSPECIFIED"


def _normalized_execution_policy_profile(
    *,
    orders: list[Order],
    metadata: dict[str, object],
) -> str:
    profile = _execution_policy_profile(orders)
    if profile != "UNSPECIFIED":
        return profile
    selection_context = _as_dict(metadata.get("selection_context"))
    for key in ("execution_policy_profile", "policy_profile", "candidate_policy_profile"):
        candidate_profile = str(selection_context.get(key) or "")
        if candidate_profile:
            return candidate_profile
    return "UNSPECIFIED"


def _decision_trigger_details(
    *,
    metadata: dict[str, object],
    linked_plans: list[PendingEntryPlan],
) -> dict[str, object]:
    trigger_details = _as_dict(metadata.get("trigger_details"))
    if trigger_details:
        return trigger_details
    for plan in sorted(linked_plans, key=lambda item: item.created_at, reverse=True):
        plan_metadata = _as_dict(plan.metadata_json)
        trigger_details = _as_dict(plan_metadata.get("trigger_details"))
        if trigger_details:
            return trigger_details
    return {}


def _failure_cluster_hit(setup_cluster_state: dict[str, object]) -> bool:
    if not setup_cluster_state:
        return False
    if bool(setup_cluster_state.get("active", False)):
        return True
    if bool(setup_cluster_state.get("underperforming", False)):
        return True
    disable_reason_codes = setup_cluster_state.get("disable_reason_codes")
    return isinstance(disable_reason_codes, list) and bool(disable_reason_codes)


def _rule_hits(
    *,
    rationale_codes: list[str],
    metadata: dict[str, object],
    trigger_details: dict[str, object],
    selection_context: dict[str, object],
) -> set[str]:
    rules: set[str] = set()
    rationale_set = set(rationale_codes)
    if rationale_set & {"STRUCTURE_BREAKOUT_UP_EXCEPTION", "STRUCTURE_BREAKOUT_DOWN_EXCEPTION"}:
        rules.add("breakout_exception")
    agreement = _as_dict(metadata.get("decision_agreement"))
    if str(agreement.get("level") or "") in {"partial_agreement", "disagreement"}:
        rules.add("decision_agreement_soft_limit")
    setup_cluster_state = _as_dict(metadata.get("setup_cluster_state"))
    if bool(setup_cluster_state.get("matched", False)) or "SETUP_CLUSTER_DISABLED" in rationale_set:
        rules.add("setup_cluster_auto_disable")
    ai_skipped_reason = str(metadata.get("ai_skipped_reason") or "")
    if ai_skipped_reason.startswith("CADENCE_IDLE_"):
        rules.add("idle_ai_skip")
    breadth_regime = str(selection_context.get("breadth_regime") or "")
    if breadth_regime in {"weak_breadth", "transition_fragile"}:
        rules.add("universe_breadth_filter")
    if rationale_set & {"LEAD_MARKET_DIVERGENCE", "ALT_BREAKOUT_AHEAD_OF_LEADS", "LEAD_MARKET_CONFIDENCE_DISCOUNT"}:
        rules.add("lead_lag_filter")
    if any(code.startswith("SETUP_TIME_PROFILE_") for code in rationale_codes):
        rules.add("setup_time_profile")
    if rationale_set & {"DERIVATIVES_ALIGNMENT_HEADWIND", "SPREAD_HEADWIND", "BREAKOUT_OI_SPREAD_FILTER"}:
        rules.add("derivatives_entry_filter")
    if bool(trigger_details.get("late_chase")) or str(trigger_details.get("quality_state") or "") in {"waiting", "cancel"}:
        rules.add("confirm_quality_filter")
    if str(selection_context.get("selected_reason") or "") == "ranked_portfolio_focus":
        rules.add("portfolio_rotation_ranking")
    return rules


def _expectancy(values: list[float]) -> float:
    if not values:
        return 0.0
    wins = [item for item in values if item > 0]
    losses = [abs(item) for item in values if item < 0]
    win_rate = len(wins) / len(values)
    loss_rate = len(losses) / len(values)
    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 0.0
    return (win_rate * avg_win) - (loss_rate * avg_loss)


def _classify_metrics(
    *,
    sample_size: int,
    traded_decisions: int,
    expectancy: float,
    net_pnl_after_fees: float,
    avg_signed_slippage_bps: float,
    hold_rate: float,
    late_trigger_ratio: float,
    failure_cluster_hit_rate: float,
    protective_rule: bool = False,
) -> tuple[str, list[str], str]:
    reasons: list[str] = []
    if sample_size < MIN_CLASSIFICATION_SAMPLE:
        return "simplify", ["INSUFFICIENT_SAMPLE"], "collect_more_data"
    if traded_decisions == 0 and hold_rate >= HIGH_HOLD_RATE_THRESHOLD:
        return "simplify", ["HIGH_HOLD_LOW_CONVERSION"], "ablation_review"
    if protective_rule and hold_rate >= 0.6 and failure_cluster_hit_rate >= FAILURE_CLUSTER_KEEP_THRESHOLD:
        reasons.extend(["PROTECTIVE_RULE", "FAILURE_CLUSTER_CAPTURE"])
        return "keep", reasons, "retain_and_monitor"
    if expectancy > 0 and net_pnl_after_fees > 0:
        reasons.extend(["POSITIVE_EXPECTANCY", "POSITIVE_NET_PNL"])
        if avg_signed_slippage_bps >= ADVERSE_SIGNED_SLIPPAGE_THRESHOLD:
            reasons.append("ADVERSE_SLIPPAGE_NEEDS_TUNING")
            return "simplify", reasons, "tighten_thresholds"
        if late_trigger_ratio > LATE_TRIGGER_KILL_THRESHOLD:
            reasons.append("LATE_TRIGGER_ELEVATED")
            return "simplify", reasons, "confirm_quality_revisit"
        return "keep", reasons, "retain_and_monitor"
    if (
        traded_decisions >= MIN_CLASSIFICATION_SAMPLE
        and expectancy <= 0
        and net_pnl_after_fees <= 0
        and (
            avg_signed_slippage_bps >= ADVERSE_SIGNED_SLIPPAGE_THRESHOLD
            or late_trigger_ratio >= LATE_TRIGGER_KILL_THRESHOLD
            or failure_cluster_hit_rate >= FAILURE_CLUSTER_KEEP_THRESHOLD
        )
    ):
        if avg_signed_slippage_bps >= ADVERSE_SIGNED_SLIPPAGE_THRESHOLD:
            reasons.append("ADVERSE_SIGNED_SLIPPAGE")
        if late_trigger_ratio >= LATE_TRIGGER_KILL_THRESHOLD:
            reasons.append("LATE_TRIGGER_HEAVY")
        if failure_cluster_hit_rate >= FAILURE_CLUSTER_KEEP_THRESHOLD:
            reasons.append("FAILURE_CLUSTER_HEAVY")
        reasons.extend(["NEGATIVE_EXPECTANCY", "NEGATIVE_NET_PNL"])
        return "kill", reasons, "ablation_candidate"
    reasons.append("MIXED_SIGNAL")
    return "simplify", reasons, "simplify_thresholds"


def _bucket_entry(
    *,
    bucket_key: str,
    symbol: str,
    timeframe: str,
    scenario: str,
    regime: str,
    entry_mode: str,
    execution_policy_profile: str,
    accumulator: _MetricAccumulator,
) -> RulePruningBucketEntry:
    expectancy = _expectancy(accumulator.trade_nets)
    net_pnl_after_fees = sum(accumulator.trade_nets)
    avg_signed_slippage_bps = (
        sum(accumulator.signed_slippage_bps) / len(accumulator.signed_slippage_bps)
        if accumulator.signed_slippage_bps
        else 0.0
    )
    hold_rate = accumulator.holds / max(accumulator.decisions, 1)
    late_trigger_ratio = accumulator.late_trigger_hits / max(accumulator.decisions, 1)
    failure_cluster_hit_rate = accumulator.failure_cluster_hits / max(accumulator.decisions, 1)
    classification, reasons, _recommendation = _classify_metrics(
        sample_size=accumulator.decisions,
        traded_decisions=accumulator.traded_decisions,
        expectancy=expectancy,
        net_pnl_after_fees=net_pnl_after_fees,
        avg_signed_slippage_bps=avg_signed_slippage_bps,
        hold_rate=hold_rate,
        late_trigger_ratio=late_trigger_ratio,
        failure_cluster_hit_rate=failure_cluster_hit_rate,
    )
    return RulePruningBucketEntry(
        bucket_key=bucket_key,
        symbol=symbol,
        timeframe=timeframe,
        scenario=scenario,
        regime=regime,
        entry_mode=entry_mode,
        execution_policy_profile=execution_policy_profile,
        decisions=accumulator.decisions,
        traded_decisions=accumulator.traded_decisions,
        expectancy=round(expectancy, 6),
        net_pnl_after_fees=round(net_pnl_after_fees, 6),
        avg_signed_slippage_bps=round(avg_signed_slippage_bps, 6),
        hold_rate=round(hold_rate, 6),
        late_trigger_ratio=round(late_trigger_ratio, 6),
        failure_cluster_hit_rate=round(failure_cluster_hit_rate, 6),
        classification=classification,  # type: ignore[arg-type]
        reasons=reasons,
    )


def _rule_entry(*, rule_key: str, accumulator: _MetricAccumulator) -> RulePruningCandidate:
    expectancy = _expectancy(accumulator.trade_nets)
    net_pnl_after_fees = sum(accumulator.trade_nets)
    avg_signed_slippage_bps = (
        sum(accumulator.signed_slippage_bps) / len(accumulator.signed_slippage_bps)
        if accumulator.signed_slippage_bps
        else 0.0
    )
    hold_rate = accumulator.holds / max(accumulator.decisions, 1)
    late_trigger_ratio = accumulator.late_trigger_hits / max(accumulator.decisions, 1)
    failure_cluster_hit_rate = accumulator.failure_cluster_hits / max(accumulator.decisions, 1)
    classification, reasons, recommendation = _classify_metrics(
        sample_size=accumulator.decisions,
        traded_decisions=accumulator.traded_decisions,
        expectancy=expectancy,
        net_pnl_after_fees=net_pnl_after_fees,
        avg_signed_slippage_bps=avg_signed_slippage_bps,
        hold_rate=hold_rate,
        late_trigger_ratio=late_trigger_ratio,
        failure_cluster_hit_rate=failure_cluster_hit_rate,
        protective_rule=rule_key in PROTECTIVE_RULE_KEYS,
    )
    return RulePruningCandidate(
        rule_key=rule_key,
        sample_size=accumulator.decisions,
        traded_decisions=accumulator.traded_decisions,
        expectancy=round(expectancy, 6),
        net_pnl_after_fees=round(net_pnl_after_fees, 6),
        avg_signed_slippage_bps=round(avg_signed_slippage_bps, 6),
        hold_rate=round(hold_rate, 6),
        late_trigger_ratio=round(late_trigger_ratio, 6),
        failure_cluster_hit_rate=round(failure_cluster_hit_rate, 6),
        classification=classification,  # type: ignore[arg-type]
        reasons=reasons,
        recommendation=recommendation,
    )


def build_keep_kill_report(
    session: Session,
    *,
    lookback_days: int = 21,
    limit: int = 256,
) -> RulePruningReportResponse:
    since = utcnow_naive() - timedelta(days=lookback_days)
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
        return RulePruningReportResponse(
            generated_at=utcnow_naive(),
            lookback_days=lookback_days,
            decisions_analyzed=0,
        )

    decision_ids = [row.id for row in decision_rows]
    orders = list(session.scalars(select(Order).where(Order.decision_run_id.in_(decision_ids))))
    orders_by_decision: dict[int, list[Order]] = defaultdict(list)
    for order in orders:
        if order.decision_run_id is not None:
            orders_by_decision[order.decision_run_id].append(order)
    order_ids = [row.id for row in orders]
    executions = list(session.scalars(select(Execution).where(Execution.order_id.in_(order_ids)))) if order_ids else []
    executions_by_order: dict[int, list[Execution]] = defaultdict(list)
    for execution in executions:
        if execution.order_id is not None:
            executions_by_order[execution.order_id].append(execution)
    plans = list(
        session.scalars(
            select(PendingEntryPlan)
            .where(PendingEntryPlan.source_decision_run_id.in_(decision_ids))
            .order_by(desc(PendingEntryPlan.created_at))
        )
    )
    plans_by_decision: dict[int, list[PendingEntryPlan]] = defaultdict(list)
    for plan in plans:
        if plan.source_decision_run_id is not None:
            plans_by_decision[plan.source_decision_run_id].append(plan)

    bucket_meta: dict[str, dict[str, str]] = {}
    bucket_accumulators: dict[str, _MetricAccumulator] = defaultdict(_MetricAccumulator)
    rule_accumulators: dict[str, _MetricAccumulator] = defaultdict(_MetricAccumulator)

    for decision_row in decision_rows:
        output_payload = _as_dict(decision_row.output_payload)
        metadata = _as_dict(decision_row.metadata_json)
        symbol = str(output_payload.get("symbol") or "").upper()
        timeframe = str(output_payload.get("timeframe") or "")
        decision = str(output_payload.get("decision") or "").lower()
        raw_entry_mode = str(output_payload.get("entry_mode") or "none").lower()
        rationale_codes = [str(code) for code in output_payload.get("rationale_codes", []) if code not in {None, ""}] if isinstance(output_payload.get("rationale_codes"), list) else []
        regime, trend_alignment, *_rest = _extract_analysis_context(decision_row)
        entry_mode = _normalized_entry_mode(
            decision=decision,
            entry_mode=raw_entry_mode,
            rationale_codes=rationale_codes,
            metadata=metadata,
        )
        scenario = _normalized_scenario(
            decision=decision,
            entry_mode=entry_mode,
            rationale_codes=rationale_codes,
            metadata=metadata,
        )
        linked_orders = orders_by_decision.get(decision_row.id, [])
        linked_executions = [
            execution
            for order in linked_orders
            for execution in executions_by_order.get(order.id, [])
        ]
        trigger_details = _decision_trigger_details(
            metadata=metadata,
            linked_plans=plans_by_decision.get(decision_row.id, []),
        )
        selection_context = _as_dict(metadata.get("selection_context"))
        setup_cluster_state = _as_dict(metadata.get("setup_cluster_state"))
        net_pnl_after_fees = sum(
            _safe_float(execution.realized_pnl) - _safe_float(execution.fee_paid)
            for execution in linked_executions
        )
        signed_slippage_values = [_signed_slippage_bps(execution) for execution in linked_executions]
        execution_policy_profile = _normalized_execution_policy_profile(
            orders=linked_orders,
            metadata=metadata,
        )
        bucket_key = "|".join(
            [
                symbol or "UNKNOWN",
                timeframe or "UNKNOWN",
                scenario or "UNKNOWN",
                regime or "unknown",
                entry_mode or "none",
                execution_policy_profile,
            ]
        )
        bucket_meta[bucket_key] = {
            "symbol": symbol or "UNKNOWN",
            "timeframe": timeframe or "UNKNOWN",
            "scenario": scenario or "UNKNOWN",
            "regime": regime or "unknown",
            "entry_mode": entry_mode or "none",
            "execution_policy_profile": execution_policy_profile,
        }
        late_trigger = bool(trigger_details.get("late_chase")) or str(trigger_details.get("reason") or "") == "QUALITY_REJECTED_LATE_CHASE"
        failure_cluster_hit = _failure_cluster_hit(setup_cluster_state)

        bucket_accumulator = bucket_accumulators[bucket_key]
        bucket_accumulator.decisions += 1
        bucket_accumulator.holds += int(decision == "hold")
        bucket_accumulator.late_trigger_hits += int(late_trigger)
        bucket_accumulator.failure_cluster_hits += int(failure_cluster_hit)
        if linked_executions:
            bucket_accumulator.traded_decisions += 1
            bucket_accumulator.trade_nets.append(net_pnl_after_fees)
            bucket_accumulator.signed_slippage_bps.extend(signed_slippage_values)

        for rule_key in _rule_hits(
            rationale_codes=rationale_codes,
            metadata=metadata,
            trigger_details=trigger_details,
            selection_context=selection_context,
        ):
            rule_accumulator = rule_accumulators[rule_key]
            rule_accumulator.decisions += 1
            rule_accumulator.holds += int(decision == "hold")
            rule_accumulator.late_trigger_hits += int(late_trigger)
            rule_accumulator.failure_cluster_hits += int(failure_cluster_hit)
            if linked_executions:
                rule_accumulator.traded_decisions += 1
                rule_accumulator.trade_nets.append(net_pnl_after_fees)
                rule_accumulator.signed_slippage_bps.extend(signed_slippage_values)

    bucket_reports = [
        _bucket_entry(
            bucket_key=bucket_key,
            symbol=meta["symbol"],
            timeframe=meta["timeframe"],
            scenario=meta["scenario"],
            regime=meta["regime"],
            entry_mode=meta["entry_mode"],
            execution_policy_profile=meta["execution_policy_profile"],
            accumulator=accumulator,
        )
        for bucket_key, accumulator in bucket_accumulators.items()
        for meta in [bucket_meta[bucket_key]]
    ]
    bucket_reports.sort(
        key=lambda item: (
            {"kill": 0, "simplify": 1, "keep": 2}[item.classification],
            item.expectancy,
            item.net_pnl_after_fees,
        )
    )

    rule_entries = [_rule_entry(rule_key=rule_key, accumulator=accumulator) for rule_key, accumulator in rule_accumulators.items()]
    rule_entries.sort(
        key=lambda item: (
            {"kill": 0, "simplify": 1, "keep": 2}[item.classification],
            item.expectancy,
            item.net_pnl_after_fees,
        )
    )

    keep_list = [item for item in rule_entries if item.classification == "keep"]
    kill_list = [item for item in rule_entries if item.classification == "kill"]
    simplify_list = [item for item in rule_entries if item.classification == "simplify"]
    next_cycle_candidates = sorted(
        [*kill_list, *simplify_list],
        key=lambda item: (
            0 if item.classification == "kill" else 1,
            -item.sample_size,
            item.expectancy,
            item.net_pnl_after_fees,
        ),
    )[:3]

    return RulePruningReportResponse(
        generated_at=utcnow_naive(),
        lookback_days=lookback_days,
        decisions_analyzed=len(decision_rows),
        bucket_reports=bucket_reports,
        keep_list=sorted(keep_list, key=lambda item: (-item.sample_size, -item.net_pnl_after_fees, -item.expectancy)),
        kill_list=sorted(kill_list, key=lambda item: (-item.sample_size, item.expectancy, item.net_pnl_after_fees)),
        simplify_list=sorted(simplify_list, key=lambda item: (-item.sample_size, item.expectancy, item.net_pnl_after_fees)),
        next_cycle_candidates=next_cycle_candidates,
    )
