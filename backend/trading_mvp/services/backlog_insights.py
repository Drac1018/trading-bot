from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

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
            .where(
                AgentRun.role == "trading_decision",
                AgentRun.created_at >= since,
            )
            .order_by(AgentRun.created_at.desc())
        )
    )
    risk_by_decision: dict[int, RiskCheck] = {}
    for row in session.scalars(
        select(RiskCheck)
        .where(
            RiskCheck.decision_run_id.is_not(None),
            RiskCheck.created_at >= since,
        )
        .order_by(RiskCheck.created_at.desc())
    ):
        if row.decision_run_id is not None and row.decision_run_id not in risk_by_decision:
            risk_by_decision[row.decision_run_id] = row
    orders_by_decision: dict[int, list[Order]] = defaultdict(list)
    order_ids: list[int] = []
    for order in session.scalars(
        select(Order)
        .where(
            Order.decision_run_id.is_not(None),
            Order.created_at >= since,
        )
        .order_by(Order.created_at.desc())
    ):
        if order.decision_run_id is not None:
            orders_by_decision[order.decision_run_id].append(order)
        order_ids.append(order.id)
    executions_by_order: dict[int, list[Execution]] = defaultdict(list)
    if order_ids:
        for execution in session.scalars(
            select(Execution)
            .where(Execution.order_id.in_(order_ids), Execution.created_at >= since)
            .order_by(Execution.created_at.desc())
        ):
            if execution.order_id is not None:
                executions_by_order[execution.order_id].append(execution)

    aggregates: dict[str, dict[str, object]] = {}
    for row in decision_rows:
        payload = row.output_payload if isinstance(row.output_payload, dict) else {}
        rationale_codes = payload.get("rationale_codes")
        if not isinstance(rationale_codes, list) or not rationale_codes:
            rationale_codes = ["UNSPECIFIED"]
        risk_row = risk_by_decision.get(row.id)
        linked_orders = orders_by_decision.get(row.id, [])
        linked_executions = [execution for order in linked_orders for execution in executions_by_order.get(order.id, [])]
        realized_total = sum(execution.realized_pnl for execution in linked_executions)
        slippages = [execution.slippage_pct for execution in linked_executions]
        wins = sum(1 for execution in linked_executions if execution.realized_pnl > 0)
        losses = sum(1 for execution in linked_executions if execution.realized_pnl < 0)
        latest_seen_at = row.created_at
        approved = 1 if risk_row is not None and risk_row.allowed else 0

        for code in [str(item) for item in rationale_codes]:
            bucket = aggregates.setdefault(
                code,
                {
                    "rationale_code": code,
                    "decisions": 0,
                    "approvals": 0,
                    "orders": 0,
                    "fills": 0,
                    "wins": 0,
                    "losses": 0,
                    "realized_pnl_total": 0.0,
                    "slippages": [],
                    "latest_seen_at": latest_seen_at,
                },
            )
            bucket["decisions"] = int(bucket["decisions"]) + 1
            bucket["approvals"] = int(bucket["approvals"]) + approved
            bucket["orders"] = int(bucket["orders"]) + len(linked_orders)
            bucket["fills"] = int(bucket["fills"]) + len(linked_executions)
            bucket["wins"] = int(bucket["wins"]) + wins
            bucket["losses"] = int(bucket["losses"]) + losses
            bucket["realized_pnl_total"] = float(bucket["realized_pnl_total"]) + realized_total
            cast_slippages = bucket["slippages"]
            if isinstance(cast_slippages, list):
                cast_slippages.extend(slippages)
            if latest_seen_at > bucket["latest_seen_at"]:
                bucket["latest_seen_at"] = latest_seen_at

    items = [
        SignalPerformanceEntry(
            rationale_code=code,
            decisions=int(bucket["decisions"]),
            approvals=int(bucket["approvals"]),
            orders=int(bucket["orders"]),
            fills=int(bucket["fills"]),
            wins=int(bucket["wins"]),
            losses=int(bucket["losses"]),
            realized_pnl_total=float(bucket["realized_pnl_total"]),
            average_slippage_pct=(
                sum(bucket["slippages"]) / len(bucket["slippages"]) if bucket["slippages"] else 0.0
            ),
            latest_seen_at=bucket["latest_seen_at"],
        )
        for code, bucket in aggregates.items()
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
        return "dashboard", "운영자 시야와 모니터링 흐름을 강화하는 방향"
    if any(token in haystack for token in ("risk", "guard", "stop", "loss", "리스크", "손절", "보호")):
        return "risk", "리스크 통제와 보호 주문 중심 차별화"
    if any(token in haystack for token in ("alert", "notification", "알림", "푸시")):
        return "alerting", "이벤트 감지와 운영 대응 속도 강화"
    if any(token in haystack for token in ("execution", "order", "fill", "slippage", "체결", "주문")):
        return "execution", "주문 품질과 체결 추적 강화"
    if any(token in haystack for token in ("ai", "signal", "model", "agent", "신호", "에이전트")):
        return "signal-ai", "AI 신호와 해석 가능성 강화"
    return "general", "제품 차별점과 운영 포인트 비교"


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
