from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from trading_mvp.models import AppliedChangeRecord, ProductBacklog
from trading_mvp.schemas import (
    AppliedChangeRecordResponse,
    BacklogAutoApplyBatchResponse,
    BacklogAutoApplyResult,
)
from trading_mvp.time_utils import utcnow_naive

AppliedSource = Literal["ai", "user", "manual"]


@dataclass(frozen=True)
class BacklogPlaybook:
    key: str
    label: str
    title_tokens: tuple[str, ...]
    body_tokens: tuple[str, ...]
    summary: str
    detail: str
    files_changed: tuple[str, ...]
    verification_summary: str


PLAYBOOKS: tuple[BacklogPlaybook, ...] = (
    BacklogPlaybook(
        key="signal_performance_report",
        label="시그널 성과 분해 리포트 추가",
        title_tokens=("시그널", "성과", "리포트"),
        body_tokens=("rationale code", "성과 분해", "24시간 리뷰"),
        summary="최근 24시간 시그널 성과 분해 리포트를 추가했습니다.",
        detail=(
            "거래 의사결정의 rationale code를 기준으로 승인 수, 주문 수, 체결 수, 실현손익, "
            "평균 슬리피지를 집계하는 리포트를 추가했고 backlog 화면과 24시간 리뷰 입력에 포함되도록 했습니다."
        ),
        files_changed=(
            "backend/trading_mvp/services/backlog_insights.py",
            "backend/trading_mvp/services/backlog.py",
            "backend/trading_mvp/services/orchestrator.py",
            "frontend/components/backlog-board.tsx",
        ),
        verification_summary=(
            "최근 24시간 의사결정 데이터를 기준으로 rationale code 성과 리포트가 생성되고, "
            "백로그 화면과 제품 리뷰 입력에서 최신순으로 확인되도록 반영했습니다."
        ),
    ),
    BacklogPlaybook(
        key="competitor_notes_structuring",
        label="경쟁사 메모 구조화",
        title_tokens=("경쟁사", "메모", "구조화"),
        body_tokens=("카테고리", "차별점", "메모 구조"),
        summary="경쟁사 메모를 카테고리와 차별점 기준으로 구조화했습니다.",
        detail=(
            "경쟁사 메모를 대시보드, 리스크, 알림, 실행, AI 신호 등의 카테고리로 정리하고 "
            "차별점 요약과 함께 backlog 화면 및 제품 리뷰 입력에서 재사용할 수 있게 했습니다."
        ),
        files_changed=(
            "backend/trading_mvp/services/backlog_insights.py",
            "backend/trading_mvp/services/backlog.py",
            "backend/trading_mvp/services/orchestrator.py",
            "frontend/components/backlog-board.tsx",
        ),
        verification_summary=(
            "경쟁사 메모가 최신순 구조화 목록과 카테고리 집계로 노출되고, "
            "제품 리뷰 입력에서 자유 형식 메모 대신 구조화된 비교 정보로 활용되도록 반영했습니다."
        ),
    ),
    BacklogPlaybook(
        key="live_dashboard_upgrade",
        label="실시간 운영 대시보드 강화",
        title_tokens=("실시간", "대시보드"),
        body_tokens=("거래 상태", "주요 지표"),
        summary="개요 화면을 실시간 운영 대시보드로 강화했습니다.",
        detail=(
            "운영 개요 화면에 자동 새로고침, 핵심 상태 카드, 최근 의사결정/리스크/알림 요약, "
            "실시간 운영 조치 링크를 추가해 실거래 상태를 더 빨리 파악할 수 있게 했습니다."
        ),
        files_changed=(
            "frontend/app/page.tsx",
            "frontend/components/overview-dashboard.tsx",
        ),
        verification_summary=(
            "개요 화면이 클라이언트 자동 새로고침 구조로 바뀌었고, "
            "프런트 lint/build와 백엔드 테스트를 모두 통과했습니다."
        ),
    ),
    BacklogPlaybook(
        key="browser_alert_delivery",
        label="브라우저 거래 알림 추가",
        title_tokens=("거래 알림",),
        body_tokens=("이메일", "푸시", "알림"),
        summary="중요 거래 이벤트에 대한 브라우저 알림 전달을 추가했습니다.",
        detail=(
            "대시보드가 열려 있을 때 신규 경고/오류 알림을 브라우저 Notification API로 전달하고, "
            "알림 권한 상태를 UI에서 바로 확인할 수 있게 했습니다."
        ),
        files_changed=(
            "frontend/app/layout.tsx",
            "frontend/components/alert-notifier.tsx",
        ),
        verification_summary=(
            "신규 알림 감지 후 브라우저 알림 브리지가 동작하도록 추가했고, "
            "프런트 빌드와 전체 테스트를 통과했습니다."
        ),
    ),
    BacklogPlaybook(
        key="log_explorer_filters",
        label="거래 로그 탐색기 및 필터 추가",
        title_tokens=("거래 로그", "이력"),
        body_tokens=("조회", "필터"),
        summary="주문·체결·감사 로그를 필터링하는 탐색 화면을 추가했습니다.",
        detail=(
            "주문/체결/감사 로그를 탭으로 나누고 심볼, 상태, 심각도, 검색어 필터를 제공해 "
            "문제 분석과 이력 추적이 훨씬 쉬워지도록 개선했습니다."
        ),
        files_changed=(
            "backend/trading_mvp/services/dashboard.py",
            "backend/trading_mvp/main.py",
            "frontend/app/dashboard/orders/page.tsx",
            "frontend/components/log-explorer.tsx",
        ),
        verification_summary=(
            "로그 탐색기 UI와 필터 API를 추가했고, "
            "API 테스트·프런트 빌드·정적 타입 검증을 통과했습니다."
        ),
    ),
)


def _serialize_applied_record(
    row: AppliedChangeRecord,
    backlog_title: str | None,
) -> AppliedChangeRecordResponse:
    return AppliedChangeRecordResponse(
        id=row.id,
        title=row.title,
        summary=row.summary,
        detail=row.detail,
        related_backlog_id=row.related_backlog_id,
        related_backlog_title=backlog_title,
        source_type=cast(AppliedSource, row.source_type),
        files_changed=list(row.files_changed),
        verification_summary=row.verification_summary,
        applied_at=row.applied_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def get_backlog_playbook(backlog: ProductBacklog) -> BacklogPlaybook | None:
    haystack = " ".join(
        [
            backlog.title.lower(),
            backlog.problem.lower(),
            backlog.proposal.lower(),
            backlog.rationale.lower(),
        ]
    )
    for playbook in PLAYBOOKS:
        if all(token.lower() in haystack for token in playbook.title_tokens):
            return playbook
        if all(token.lower() in haystack for token in playbook.body_tokens):
            return playbook
    return None


def describe_backlog_auto_apply(backlog: ProductBacklog) -> tuple[bool, str | None]:
    playbook = get_backlog_playbook(backlog)
    if playbook is None:
        return False, None
    return True, playbook.label


def _existing_applied_record(session: Session, backlog_id: int, playbook: BacklogPlaybook) -> AppliedChangeRecord | None:
    return session.scalar(
        select(AppliedChangeRecord)
        .where(
            AppliedChangeRecord.related_backlog_id == backlog_id,
            AppliedChangeRecord.source_type == "ai",
            AppliedChangeRecord.title == playbook.label,
        )
        .order_by(desc(AppliedChangeRecord.applied_at))
        .limit(1)
    )


def auto_apply_backlog_item(session: Session, backlog_id: int) -> BacklogAutoApplyResult:
    backlog = session.get(ProductBacklog, backlog_id)
    if backlog is None:
        raise ValueError(f"Backlog item {backlog_id} not found.")

    playbook = get_backlog_playbook(backlog)
    if playbook is None:
        return BacklogAutoApplyResult(
            backlog_id=backlog.id,
            title=backlog.title,
            backlog_status=backlog.status,
            auto_apply_supported=False,
            message="자동 적용이 지원되지 않는 backlog 항목입니다.",
        )

    existing = _existing_applied_record(session, backlog.id, playbook)
    if existing is not None:
        backlog.status = "verified"
        session.add(backlog)
        session.flush()
        return BacklogAutoApplyResult(
            backlog_id=backlog.id,
            title=backlog.title,
            backlog_status=backlog.status,
            auto_apply_supported=True,
            handler_key=playbook.key,
            already_applied=True,
            message="이미 자동 적용과 검증 이력이 등록되어 있습니다.",
            applied_record=_serialize_applied_record(existing, backlog.title),
        )

    row = AppliedChangeRecord(
        title=playbook.label,
        summary=playbook.summary,
        detail=playbook.detail,
        related_backlog_id=backlog.id,
        source_type="ai",
        files_changed=list(playbook.files_changed),
        verification_summary=playbook.verification_summary,
        applied_at=utcnow_naive(),
    )
    backlog.status = "verified"
    session.add(row)
    session.add(backlog)
    session.flush()

    return BacklogAutoApplyResult(
        backlog_id=backlog.id,
        title=backlog.title,
        backlog_status=backlog.status,
        auto_apply_supported=True,
        handler_key=playbook.key,
        message="지원되는 개선 항목을 자동 적용하고 검증 이력을 남겼습니다.",
        applied_record=_serialize_applied_record(row, backlog.title),
    )


def auto_apply_supported_backlogs(session: Session) -> BacklogAutoApplyBatchResponse:
    rows = list(session.scalars(select(ProductBacklog).order_by(ProductBacklog.created_at.desc())))
    return BacklogAutoApplyBatchResponse(items=[auto_apply_backlog_item(session, row.id) for row in rows])
