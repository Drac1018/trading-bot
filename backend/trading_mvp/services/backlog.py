from __future__ import annotations

from datetime import datetime
from typing import Literal, cast

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from trading_mvp.models import AppliedChangeRecord, ProductBacklog, UserChangeRequest
from trading_mvp.schemas import (
    AppliedChangeRecordCreate,
    AppliedChangeRecordResponse,
    BacklogBoardResponse,
    ProductBacklogDetailResponse,
    ProductBacklogResponse,
    UserChangeRequestCreate,
    UserChangeRequestResponse,
)
from trading_mvp.services.backlog_auto_apply import describe_backlog_auto_apply
from trading_mvp.services.backlog_codex_drafts import build_codex_prompt_draft
from trading_mvp.services.backlog_insights import (
    build_signal_performance_report,
    build_structured_competitor_notes,
)
from trading_mvp.time_utils import utcnow_naive

BacklogSeverity = Literal["low", "medium", "high", "critical"]
BacklogEffort = Literal["small", "medium", "large"]
BacklogImpact = Literal["low", "medium", "high"]
RequestStatus = Literal["requested", "accepted", "applied", "verified"]
AppliedSource = Literal["ai", "user", "manual"]


def _latest_request_activity(row: UserChangeRequest) -> datetime:
    return max(row.created_at, row.updated_at)


def _latest_applied_activity(row: AppliedChangeRecord) -> datetime:
    return max(row.applied_at, row.created_at, row.updated_at)


def _latest_backlog_activity(
    row: ProductBacklog,
    request_rows: list[UserChangeRequest],
    applied_rows: list[AppliedChangeRecord],
) -> datetime:
    candidates = [row.created_at, row.updated_at]
    candidates.extend(_latest_request_activity(item) for item in request_rows)
    candidates.extend(_latest_applied_activity(item) for item in applied_rows)
    return max(candidates)


def _serialize_backlog_row(row: ProductBacklog) -> ProductBacklogResponse:
    auto_apply_supported, auto_apply_label = describe_backlog_auto_apply(row)
    return ProductBacklogResponse(
        id=row.id,
        title=row.title,
        problem=row.problem,
        proposal=row.proposal,
        severity=cast(BacklogSeverity, row.severity),
        effort=cast(BacklogEffort, row.effort),
        impact=cast(BacklogImpact, row.impact),
        priority=cast(BacklogSeverity, row.priority),
        rationale=row.rationale,
        source=row.source,
        status=row.status,
        auto_apply_supported=auto_apply_supported,
        auto_apply_label=auto_apply_label,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _serialize_user_request(
    row: UserChangeRequest,
    backlog_titles: dict[int, str],
) -> UserChangeRequestResponse:
    return UserChangeRequestResponse(
        id=row.id,
        title=row.title,
        detail=row.detail,
        status=cast(RequestStatus, row.status),
        linked_backlog_id=row.linked_backlog_id,
        linked_backlog_title=backlog_titles.get(row.linked_backlog_id) if row.linked_backlog_id is not None else None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _serialize_applied_record(
    row: AppliedChangeRecord,
    backlog_titles: dict[int, str],
) -> AppliedChangeRecordResponse:
    return AppliedChangeRecordResponse(
        id=row.id,
        title=row.title,
        summary=row.summary,
        detail=row.detail,
        related_backlog_id=row.related_backlog_id,
        related_backlog_title=backlog_titles.get(row.related_backlog_id) if row.related_backlog_id is not None else None,
        source_type=cast(AppliedSource, row.source_type),
        files_changed=list(row.files_changed),
        verification_summary=row.verification_summary,
        applied_at=row.applied_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _ensure_backlog_exists(session: Session, backlog_id: int | None) -> None:
    if backlog_id is None:
        return
    if session.get(ProductBacklog, backlog_id) is None:
        raise ValueError(f"Backlog item {backlog_id} does not exist.")


def get_backlog_board(session: Session) -> BacklogBoardResponse:
    request_rows: list[UserChangeRequest] = list(
        session.scalars(select(UserChangeRequest).order_by(desc(UserChangeRequest.created_at)))
    )
    applied_rows: list[AppliedChangeRecord] = list(
        session.scalars(select(AppliedChangeRecord).order_by(desc(AppliedChangeRecord.applied_at)))
    )
    raw_backlog_rows = list(session.scalars(select(ProductBacklog)))

    request_rows_by_backlog: dict[int, list[UserChangeRequest]] = {}
    for request_row in request_rows:
        if request_row.linked_backlog_id is None:
            continue
        request_rows_by_backlog.setdefault(request_row.linked_backlog_id, []).append(request_row)
    for request_items in request_rows_by_backlog.values():
        request_items.sort(key=_latest_request_activity, reverse=True)

    applied_rows_by_backlog: dict[int, list[AppliedChangeRecord]] = {}
    for applied_row in applied_rows:
        if applied_row.related_backlog_id is None:
            continue
        applied_rows_by_backlog.setdefault(applied_row.related_backlog_id, []).append(applied_row)
    for applied_items in applied_rows_by_backlog.values():
        applied_items.sort(key=_latest_applied_activity, reverse=True)

    backlog_rows = sorted(
        raw_backlog_rows,
        key=lambda row: _latest_backlog_activity(
            row,
            request_rows_by_backlog.get(row.id, []),
            applied_rows_by_backlog.get(row.id, []),
        ),
        reverse=True,
    )
    backlog_titles = {row.id: row.title for row in backlog_rows}

    requests_by_backlog: dict[int, list[UserChangeRequestResponse]] = {}
    for request_row in request_rows:
        serialized_request = _serialize_user_request(request_row, backlog_titles)
        if request_row.linked_backlog_id is None:
            continue
        requests_by_backlog.setdefault(request_row.linked_backlog_id, []).append(serialized_request)

    applied_by_backlog: dict[int, list[AppliedChangeRecordResponse]] = {}
    for applied_row in applied_rows:
        serialized_record = _serialize_applied_record(applied_row, backlog_titles)
        if applied_row.related_backlog_id is None:
            continue
        applied_by_backlog.setdefault(applied_row.related_backlog_id, []).append(serialized_record)

    ai_backlog = [
        ProductBacklogDetailResponse(
            **_serialize_backlog_row(row).model_dump(mode="python"),
            user_requests=requests_by_backlog.get(row.id, []),
            applied_records=applied_by_backlog.get(row.id, []),
            codex_prompt_draft=build_codex_prompt_draft(
                row,
                user_requests=requests_by_backlog.get(row.id, []),
                applied_records=applied_by_backlog.get(row.id, []),
            ),
        )
        for row in backlog_rows
    ]

    unlinked_user_requests: list[UserChangeRequestResponse] = []
    for request_row in request_rows:
        if request_row.linked_backlog_id is None:
            unlinked_user_requests.append(_serialize_user_request(request_row, backlog_titles))

    unlinked_applied_records: list[AppliedChangeRecordResponse] = []
    for applied_row in applied_rows:
        if applied_row.related_backlog_id is None:
            unlinked_applied_records.append(_serialize_applied_record(applied_row, backlog_titles))

    return BacklogBoardResponse(
        ai_backlog=ai_backlog,
        unlinked_user_requests=unlinked_user_requests,
        unlinked_applied_records=unlinked_applied_records,
        signal_performance_report=build_signal_performance_report(session),
        structured_competitor_notes=build_structured_competitor_notes(session),
    )


def get_backlog_detail(session: Session, backlog_id: int) -> ProductBacklogDetailResponse | None:
    board = get_backlog_board(session)
    for item in board.ai_backlog:
        if item.id == backlog_id:
            return item
    return None


def create_user_change_request(
    session: Session,
    payload: UserChangeRequestCreate,
) -> UserChangeRequestResponse:
    _ensure_backlog_exists(session, payload.linked_backlog_id)
    row = UserChangeRequest(
        title=payload.title,
        detail=payload.detail,
        status=payload.status,
        linked_backlog_id=payload.linked_backlog_id,
    )
    session.add(row)
    session.flush()
    backlog_titles = {
        backlog.id: backlog.title for backlog in session.scalars(select(ProductBacklog))
    }
    return _serialize_user_request(row, backlog_titles)


def create_applied_change_record(
    session: Session,
    payload: AppliedChangeRecordCreate,
) -> AppliedChangeRecordResponse:
    _ensure_backlog_exists(session, payload.related_backlog_id)
    row = AppliedChangeRecord(
        title=payload.title,
        summary=payload.summary,
        detail=payload.detail,
        related_backlog_id=payload.related_backlog_id,
        source_type=payload.source_type,
        files_changed=payload.files_changed,
        verification_summary=payload.verification_summary,
        applied_at=payload.applied_at or utcnow_naive(),
    )
    session.add(row)
    session.flush()
    backlog_titles = {
        backlog.id: backlog.title for backlog in session.scalars(select(ProductBacklog))
    }
    return _serialize_applied_record(row, backlog_titles)
