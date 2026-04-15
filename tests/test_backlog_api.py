from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from trading_mvp.database import Base, get_db
from trading_mvp.main import app
from trading_mvp.models import (
    AgentRun,
    AppliedChangeRecord,
    CompetitorNote,
    Execution,
    Order,
    ProductBacklog,
    RiskCheck,
    UserChangeRequest,
)
from trading_mvp.services.backlog import get_backlog_board
from trading_mvp.time_utils import utcnow_naive


def _create_backlog(session) -> ProductBacklog:
    row = ProductBacklog(
        title="실시간 거래 상태 대시보드 개발",
        problem="핵심 운영 상태가 흩어져 있습니다.",
        proposal="실시간으로 거래 상태와 주요 지표를 보여주는 대시보드를 개발합니다.",
        severity="high",
        effort="medium",
        impact="high",
        priority="high",
        rationale="운영자가 거래 상황을 신속하게 파악하고 대응할 수 있습니다.",
        source="product_improvement_agent",
        status="open",
    )
    session.add(row)
    session.flush()
    return row


def test_backlog_board_groups_related_items(db_session) -> None:
    backlog = _create_backlog(db_session)
    db_session.add(
        UserChangeRequest(
            title="백로그에 사용자 요청 표시",
            detail="요청 사항을 AI 백로그 카드에서 바로 보고 싶습니다.",
            status="requested",
            linked_backlog_id=backlog.id,
        )
    )
    db_session.add(
        AppliedChangeRecord(
            title="백로그 카드 시안 적용",
            summary="카드형 UI를 추가했습니다.",
            detail="AI 제안과 사용자 요청을 한 카드에서 묶어 보여줍니다.",
            related_backlog_id=backlog.id,
            source_type="manual",
            files_changed=["frontend/app/dashboard/backlog/page.tsx"],
            verification_summary="화면에서 요청과 적용 내역이 함께 보입니다.",
        )
    )
    db_session.flush()

    board = get_backlog_board(db_session)

    assert len(board.ai_backlog) == 1
    assert board.ai_backlog[0].user_requests[0].title == "백로그에 사용자 요청 표시"
    assert board.ai_backlog[0].applied_records[0].title == "백로그 카드 시안 적용"
    assert "codex_prompt_draft" not in board.ai_backlog[0].model_dump()
    assert "auto_apply_supported" not in board.ai_backlog[0].model_dump()
    assert "auto_apply_label" not in board.ai_backlog[0].model_dump()


def test_backlog_board_sorts_by_latest_activity_and_includes_insights(db_session) -> None:
    older = ProductBacklog(
        title="오래된 high backlog",
        problem="오래된 backlog 입니다.",
        proposal="우선순위는 높지만 날짜는 오래됐습니다.",
        severity="high",
        effort="medium",
        impact="high",
        priority="high",
        rationale="오래된 데이터",
        source="product_improvement_agent",
        status="open",
    )
    newer = ProductBacklog(
        title="시그널 성과 분해 리포트 추가",
        problem="최근 항목입니다.",
        proposal="rationale code 기준 성과 분해 리포트를 24시간 리뷰에 포함합니다.",
        severity="medium",
        effort="medium",
        impact="high",
        priority="high",
        rationale="최신 데이터",
        source="product_improvement_agent",
        status="open",
    )
    db_session.add_all([older, newer])
    db_session.flush()
    now = utcnow_naive()
    older.created_at = now - timedelta(days=2)
    older.updated_at = now - timedelta(days=2)
    newer.created_at = now - timedelta(hours=1)
    newer.updated_at = now - timedelta(hours=1)

    linked_request = UserChangeRequest(
        title="최신 사용자 요청",
        detail="최신 활동 기준으로 위에 보여야 합니다.",
        status="requested",
        linked_backlog_id=newer.id,
    )
    db_session.add(linked_request)
    db_session.flush()
    linked_request.created_at = now
    linked_request.updated_at = now

    db_session.add(
        CompetitorNote(
            source="competitor-a",
            note="대시보드에서 알림과 실행 로그를 한 화면에 묶어 보여줍니다.",
            tags=["dashboard", "alert"],
        )
    )
    decision = AgentRun(
        role="trading_decision",
        trigger_event="realtime_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="openai",
        summary="signal report",
        input_payload={},
        output_payload={"rationale_codes": ["TREND_UP"], "decision": "long"},
        metadata_json={},
        schema_valid=True,
    )
    db_session.add(decision)
    db_session.flush()
    decision.created_at = now
    decision.updated_at = now
    db_session.add(
        RiskCheck(
            symbol="BTCUSDT",
            decision_run_id=decision.id,
            market_snapshot_id=None,
            allowed=True,
            decision="long",
            reason_codes=[],
            approved_risk_pct=0.01,
            approved_leverage=2.0,
            payload={},
        )
    )
    order = Order(
        symbol="BTCUSDT",
        decision_run_id=decision.id,
        risk_check_id=None,
        position_id=None,
        side="buy",
        order_type="market",
        mode="live",
        status="filled",
        requested_quantity=0.002,
        requested_price=70000.0,
        filled_quantity=0.002,
        average_fill_price=70010.0,
        reason_codes=[],
        metadata_json={},
    )
    db_session.add(order)
    db_session.flush()
    db_session.add(
        Execution(
            order_id=order.id,
            position_id=None,
            symbol="BTCUSDT",
            status="filled",
            external_trade_id="trade-1",
            fill_price=70010.0,
            fill_quantity=0.002,
            fee_paid=0.2,
            commission_asset="USDT",
            slippage_pct=0.001,
            realized_pnl=12.5,
            payload={},
        )
    )
    db_session.flush()

    board = get_backlog_board(db_session)

    assert board.ai_backlog[0].id == newer.id
    assert board.ai_backlog[0].user_requests[0].title == "최신 사용자 요청"
    assert board.signal_performance_report is not None
    assert board.signal_performance_report.items[0].rationale_code == "TREND_UP"
    assert board.structured_competitor_notes is not None
    assert board.structured_competitor_notes.items[0].category == "dashboard"
    assert "codex_prompt_draft" not in board.ai_backlog[0].model_dump()


def test_backlog_endpoints_create_and_return_related_data(tmp_path, monkeypatch) -> None:
    test_engine = create_engine(f"sqlite:///{tmp_path / 'backlog_api.db'}", future=True)
    TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=test_engine)
    monkeypatch.setattr("trading_mvp.main.engine", test_engine)

    def override_get_db():
        with TestingSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    try:
        with TestingSessionLocal() as session:
            backlog = _create_backlog(session)
            backlog_id = backlog.id
            session.commit()

        with TestClient(app) as client:
            request_response = client.post(
                "/api/backlog/requests",
                json={
                    "title": "사용자 요청 등록 테스트",
                    "detail": "백로그 UI에서 직접 등록할 수 있어야 합니다.",
                    "status": "requested",
                    "linked_backlog_id": backlog_id,
                },
            )
            assert request_response.status_code == 200
            assert request_response.json()["linked_backlog_id"] == backlog_id

            applied_response = client.post(
                "/api/backlog/applied",
                json={
                    "title": "적용 내역 등록 테스트",
                    "summary": "백로그와 연결된 적용 내역을 저장했습니다.",
                    "detail": "UI에서 적용 내역 카드가 보이도록 했습니다.",
                    "related_backlog_id": backlog_id,
                    "source_type": "user",
                    "files_changed": ["frontend/components/backlog-board.tsx"],
                    "verification_summary": "백로그 상세 조회에서 함께 보이는지 확인했습니다.",
                },
            )
            assert applied_response.status_code == 200
            assert applied_response.json()["related_backlog_id"] == backlog_id

            board_response = client.get("/api/backlog")
            assert board_response.status_code == 200
            board = board_response.json()
            assert len(board["ai_backlog"]) == 1
            assert len(board["ai_backlog"][0]["user_requests"]) == 1
            assert len(board["ai_backlog"][0]["applied_records"]) == 1
            assert "codex_prompt_draft" not in board["ai_backlog"][0]
            assert "auto_apply_supported" not in board["ai_backlog"][0]
            assert "auto_apply_label" not in board["ai_backlog"][0]

            detail_response = client.get(f"/api/backlog/{backlog_id}")
            assert detail_response.status_code == 200
            detail = detail_response.json()
            assert detail["id"] == backlog_id
            assert detail["user_requests"][0]["title"] == "사용자 요청 등록 테스트"
            assert detail["applied_records"][0]["title"] == "적용 내역 등록 테스트"
            assert "codex_prompt_draft" not in detail
            assert "auto_apply_supported" not in detail
            assert "auto_apply_label" not in detail
            assert client.get(f"/api/backlog/{backlog_id}/codex-draft").status_code == 404
            assert client.post(f"/api/backlog/{backlog_id}/auto-apply").status_code == 404
            assert client.post("/api/backlog/auto-apply-supported").status_code == 405
            assert "signal_performance_report" in board
            assert "structured_competitor_notes" in board
    finally:
        app.dependency_overrides.clear()
