from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_mvp.models import AgentRun, CompetitorNote, Execution, Order, RiskCheck
from trading_mvp.schemas import (
    SignalPerformanceEntry,
    SignalPerformanceReportResponse,
    StructuredCompetitorNote,
    StructuredCompetitorNotesResponse,
)
from trading_mvp.time_utils import utcnow_naive


@dataclass(slots=True)
class SignalBucket:
    rationale_code: str
    decisions: int = 0
    approvals: int = 0
    orders: int = 0
    fills: int = 0
    wins: int = 0
    losses: int = 0
    realized_pnl_total: float = 0.0
    slippages: list[float] = field(default_factory=list)
    latest_seen_at: datetime = field(default_factory=utcnow_naive)


def _safe_float(value: object, default: float = 0.0) -> float:
    if value in {None, ""}:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return default


def build_signal_performance_report(
    session: Session,
    *,
    window_hours: int = 24,
    limit: int = 12,
) -> SignalPerformanceReportResponse:
    since = utcnow_naive() - timedelta(hours=window_hours)
    decision_rows = list(
        session.scalars(
            select(AgentRun)
            .where(AgentRun.role == "trading_decision", AgentRun.created_at >= since)
            .order_by(AgentRun.created_at.desc())
        )
    )

    risk_by_decision: dict[int, RiskCheck] = {}
    risk_rows = session.scalars(
        select(RiskCheck)
        .where(RiskCheck.decision_run_id.is_not(None), RiskCheck.created_at >= since)
        .order_by(RiskCheck.created_at.desc())
    )
    for risk_row in risk_rows:
        if risk_row.decision_run_id is not None and risk_row.decision_run_id not in risk_by_decision:
            risk_by_decision[risk_row.decision_run_id] = risk_row

    orders_by_decision: dict[int, list[Order]] = defaultdict(list)
    order_ids: list[int] = []
    order_rows = session.scalars(
        select(Order)
        .where(Order.decision_run_id.is_not(None), Order.created_at >= since)
        .order_by(Order.created_at.desc())
    )
    for order_row in order_rows:
        if order_row.decision_run_id is not None:
            orders_by_decision[order_row.decision_run_id].append(order_row)
        order_ids.append(order_row.id)

    executions_by_order: dict[int, list[Execution]] = defaultdict(list)
    if order_ids:
        execution_rows = session.scalars(
            select(Execution)
            .where(Execution.order_id.in_(order_ids), Execution.created_at >= since)
            .order_by(Execution.created_at.desc())
        )
        for execution_row in execution_rows:
            if execution_row.order_id is not None:
                executions_by_order[execution_row.order_id].append(execution_row)

    aggregates: dict[str, SignalBucket] = {}
    for decision_row in decision_rows:
        payload = decision_row.output_payload if isinstance(decision_row.output_payload, dict) else {}
        raw_rationale_codes = payload.get("rationale_codes")
        rationale_codes = (
            [str(item) for item in raw_rationale_codes]
            if isinstance(raw_rationale_codes, list) and raw_rationale_codes
            else ["UNSPECIFIED"]
        )

        linked_risk = risk_by_decision.get(decision_row.id)
        linked_orders = orders_by_decision.get(decision_row.id, [])
        linked_executions = [
            execution_row
            for order_row in linked_orders
            for execution_row in executions_by_order.get(order_row.id, [])
        ]
        realized_total = sum(execution_row.realized_pnl for execution_row in linked_executions)
        slippages = [execution_row.slippage_pct for execution_row in linked_executions]
        wins = sum(1 for execution_row in linked_executions if execution_row.realized_pnl > 0)
        losses = sum(1 for execution_row in linked_executions if execution_row.realized_pnl < 0)
        approved = 1 if linked_risk is not None and linked_risk.allowed else 0

        for rationale_code in rationale_codes:
            bucket = aggregates.setdefault(
                rationale_code,
                SignalBucket(rationale_code=rationale_code, latest_seen_at=decision_row.created_at),
            )
            bucket.decisions += 1
            bucket.approvals += approved
            bucket.orders += len(linked_orders)
            bucket.fills += len(linked_executions)
            bucket.wins += wins
            bucket.losses += losses
            bucket.realized_pnl_total += realized_total
            bucket.slippages.extend(slippages)
            if decision_row.created_at > bucket.latest_seen_at:
                bucket.latest_seen_at = decision_row.created_at

    items = [
        SignalPerformanceEntry(
            rationale_code=bucket.rationale_code,
            decisions=bucket.decisions,
            approvals=bucket.approvals,
            orders=bucket.orders,
            fills=bucket.fills,
            wins=bucket.wins,
            losses=bucket.losses,
            realized_pnl_total=bucket.realized_pnl_total,
            average_slippage_pct=(
                sum(bucket.slippages) / len(bucket.slippages) if bucket.slippages else 0.0
            ),
            latest_seen_at=bucket.latest_seen_at,
        )
        for bucket in aggregates.values()
    ]
    items.sort(key=lambda item: (item.latest_seen_at, item.decisions, item.fills), reverse=True)

    return SignalPerformanceReportResponse(
        generated_at=utcnow_naive(),
        window_hours=window_hours,
        items=items[:limit],
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
