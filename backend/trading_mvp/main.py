from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session, sessionmaker

from trading_mvp.database import Base, engine, get_db
from trading_mvp.schemas import (
    AppliedChangeRecordCreate,
    AppSettingsUpdateRequest,
    BacklogAutoApplyBatchResponse,
    BacklogAutoApplyResult,
    BacklogBoardResponse,
    BacklogCodexDraftResponse,
    BinanceAccountResponse,
    BinanceConnectionTestRequest,
    BinanceLiveTestOrderRequest,
    ManualLiveApprovalRequest,
    OpenAIConnectionTestRequest,
    ProductBacklogDetailResponse,
    ReplayValidationRequest,
    UserChangeRequestCreate,
)
from trading_mvp.services.audit import record_audit_event, record_health_event
from trading_mvp.services.backlog import (
    create_applied_change_record,
    create_user_change_request,
    get_backlog_board,
    get_backlog_detail,
)
from trading_mvp.services.backlog_insights import build_signal_performance_report
from trading_mvp.services.backlog_auto_apply import (
    auto_apply_backlog_item,
    auto_apply_supported_backlogs,
)
from trading_mvp.services.binance_account import get_binance_account_snapshot
from trading_mvp.services.connectivity import check_binance_connection, check_openai_connection
from trading_mvp.services.dashboard import (
    get_agent_runs,
    get_alerts,
    get_audit_timeline,
    get_decisions,
    get_operator_dashboard,
    get_profitability_dashboard,
    get_execution_quality_report,
    get_executions,
    get_feature_snapshots,
    get_market_snapshots,
    get_orders,
    get_overview,
    get_positions,
    get_risk_checks,
    get_scheduler_runs,
)
from trading_mvp.services.execution import run_live_test_order, sync_live_state
from trading_mvp.services.orchestrator import TradingOrchestrator
from trading_mvp.services.pause_control import attempt_auto_resume
from trading_mvp.services.replay_validation import build_replay_validation_report
from trading_mvp.services.scheduler import run_due_operational_cycles, run_due_windows, run_window
from trading_mvp.services.seed import seed_demo_data
from trading_mvp.services.settings import (
    arm_live_execution,
    disarm_live_execution,
    get_or_create_settings,
    serialize_settings,
    set_trading_pause,
    update_settings,
)


async def _background_scheduler_loop() -> None:
    while True:
        polling_session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
        with polling_session_factory() as session:
            settings_row = get_or_create_settings(session)
            try:
                run_due_operational_cycles(session)
                run_due_windows(session)
                session.commit()
            except Exception as exc:
                record_audit_event(
                    session,
                    event_type="background_scheduler_failed",
                    entity_type="scheduler",
                    entity_id="background",
                    severity="error",
                    message="Background scheduler loop failed.",
                    payload={"error": str(exc)},
                )
                record_health_event(
                    session,
                    component="scheduler",
                    status="error",
                    message="Background scheduler loop failed.",
                    payload={"error": str(exc)},
                )
                session.commit()
            interval_seconds = max(15, min(int(settings_row.exchange_sync_interval_seconds), 30))
        await asyncio.sleep(interval_seconds)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    Base.metadata.create_all(bind=engine)
    poll_task = asyncio.create_task(_background_scheduler_loop())
    try:
        yield
    finally:
        poll_task.cancel()
        try:
            await poll_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Trading MVP API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health(db: Session = Depends(get_db)) -> dict[str, object]:
    settings_row = get_or_create_settings(db)
    return {"status": "ok", "mode": serialize_settings(settings_row)["mode"], "database": "ready"}


@app.post("/api/system/seed")
def seed_system(db: Session = Depends(get_db)) -> dict[str, object]:
    return seed_demo_data(db)


@app.get("/api/dashboard/overview")
def dashboard_overview(db: Session = Depends(get_db)) -> dict[str, object]:
    return get_overview(db).model_dump(mode="json")


@app.get("/api/dashboard/operator")
def dashboard_operator(db: Session = Depends(get_db)) -> dict[str, object]:
    return get_operator_dashboard(db).model_dump(mode="json")


@app.get("/api/dashboard/profitability")
def dashboard_profitability(db: Session = Depends(get_db)) -> dict[str, object]:
    return get_profitability_dashboard(db).model_dump(mode="json")


@app.get("/api/market/snapshots")
def market_snapshots(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    return get_market_snapshots(db)


@app.get("/api/market/features")
def feature_snapshots(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    return get_feature_snapshots(db)


@app.get("/api/decisions")
def decisions(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    return get_decisions(db)


@app.get("/api/positions")
def positions(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    return get_positions(db)


@app.get("/api/orders")
def orders(
    mode: str | None = None,
    symbol: str | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return get_orders(db, mode=mode, symbol=symbol, status=status, search=search, limit=limit)


@app.get("/api/executions")
def executions(
    mode: str | None = None,
    symbol: str | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return get_executions(db, mode=mode, symbol=symbol, status=status, search=search, limit=limit)


@app.get("/api/executions/report")
def execution_quality_report(db: Session = Depends(get_db)) -> dict[str, object]:
    return get_execution_quality_report(db)


@app.get("/api/risk/checks")
def risk_checks(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    return get_risk_checks(db)


@app.get("/api/agents")
def agents(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    return get_agent_runs(db)


@app.get("/api/scheduler")
def scheduler(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    return get_scheduler_runs(db)


@app.get("/api/audit")
def audit(
    event_type: str | None = None,
    severity: str | None = None,
    search: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return get_audit_timeline(db, event_type=event_type, severity=severity, search=search, limit=limit)


@app.get("/api/alerts")
def alerts(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    return get_alerts(db)


@app.get("/api/settings")
def settings_view(db: Session = Depends(get_db)) -> dict[str, object]:
    return serialize_settings(get_or_create_settings(db))


@app.get("/api/binance/account")
def binance_account(db: Session = Depends(get_db)) -> dict[str, object]:
    payload: BinanceAccountResponse = get_binance_account_snapshot(db)
    return payload.model_dump(mode="json")


@app.put("/api/settings")
def settings_update(payload: AppSettingsUpdateRequest, db: Session = Depends(get_db)) -> dict[str, object]:
    row = update_settings(db, payload)
    record_audit_event(
        db,
        event_type="settings_updated",
        entity_type="settings",
        entity_id=str(row.id),
        message="Application settings updated.",
        payload={"ai_enabled": row.ai_enabled, "binance_market_data_enabled": row.binance_market_data_enabled},
    )
    db.commit()
    return serialize_settings(row)


@app.post("/api/settings/pause")
def pause_trading(db: Session = Depends(get_db)) -> dict[str, object]:
    row = set_trading_pause(
        db,
        True,
        reason_code="MANUAL_USER_REQUEST",
        reason_detail={"source": "api"},
        pause_origin="manual",
    )
    record_audit_event(
        db,
        event_type="trading_paused",
        entity_type="settings",
        entity_id=str(row.id),
        severity="warning",
        message="Global trading pause enabled.",
        payload={"trading_paused": True, "reason_code": row.pause_reason_code, "pause_origin": row.pause_origin},
    )
    db.commit()
    return serialize_settings(row)


@app.post("/api/settings/resume")
def resume_trading(db: Session = Depends(get_db)) -> dict[str, object]:
    row = set_trading_pause(db, False)
    record_audit_event(
        db,
        event_type="trading_resumed",
        entity_type="settings",
        entity_id=str(row.id),
        severity="info",
        message="Global trading pause cleared.",
        payload={"trading_paused": False},
    )
    db.commit()
    return serialize_settings(row)


@app.post("/api/settings/live/arm")
def arm_live(
    payload: ManualLiveApprovalRequest | None = None,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    row = arm_live_execution(db, payload.minutes if payload is not None else None)
    record_audit_event(
        db,
        event_type="live_approval_armed",
        entity_type="settings",
        entity_id=str(row.id),
        severity="warning",
        message="Manual live execution window armed.",
        payload={"armed_until": row.live_execution_armed_until.isoformat() if row.live_execution_armed_until else None},
    )
    db.commit()
    return serialize_settings(row)


@app.post("/api/settings/live/disarm")
def disarm_live(db: Session = Depends(get_db)) -> dict[str, object]:
    row = disarm_live_execution(db)
    record_audit_event(
        db,
        event_type="live_approval_disarmed",
        entity_type="settings",
        entity_id=str(row.id),
        severity="info",
        message="Manual live execution window disarmed.",
        payload={},
    )
    db.commit()
    return serialize_settings(row)


@app.post("/api/settings/test/openai")
def openai_connection_test(
    payload: OpenAIConnectionTestRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    settings_row = get_or_create_settings(db)
    result = check_openai_connection(settings_row, payload)
    record_health_event(
        db,
        component="openai",
        status="ok" if result.ok else "error",
        message=result.message,
        payload=result.details,
    )
    record_audit_event(
        db,
        event_type="integration_test",
        entity_type="openai",
        entity_id=str(settings_row.id),
        severity="info" if result.ok else "warning",
        message=result.message,
        payload=result.details,
    )
    db.commit()
    return result.model_dump(mode="json")


@app.post("/api/settings/test/binance")
def binance_connection_test(
    payload: BinanceConnectionTestRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    settings_row = get_or_create_settings(db)
    result = check_binance_connection(settings_row, payload)
    record_health_event(
        db,
        component="binance",
        status="ok" if result.ok else "error",
        message=result.message,
        payload=result.details,
    )
    record_audit_event(
        db,
        event_type="integration_test",
        entity_type="binance",
        entity_id=str(settings_row.id),
        severity="info" if result.ok else "warning",
        message=result.message,
        payload=result.details,
    )
    db.commit()
    return result.model_dump(mode="json")


@app.post("/api/settings/test/binance/live-order")
def binance_live_order_test(
    payload: BinanceLiveTestOrderRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    settings_row = get_or_create_settings(db)
    try:
        result = run_live_test_order(
            db,
            settings_row,
            symbol=payload.symbol,
            side=payload.side,
            quantity=payload.quantity,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return result


@app.post("/api/cycles/run")
def run_cycle(db: Session = Depends(get_db)) -> dict[str, object]:
    output = TradingOrchestrator(db).run_selected_symbols_cycle(trigger_event="manual")
    db.commit()
    return output


@app.post("/api/reviews/{window}")
def run_review(window: str, db: Session = Depends(get_db)) -> dict[str, object]:
    output = run_window(db, window, triggered_by="manual")
    db.commit()
    return output


@app.post("/api/replay/run")
def run_replay(cycles: int = 5, start_index: int = 120, db: Session = Depends(get_db)) -> dict[str, object]:
    orchestrator = TradingOrchestrator(db)
    results: list[dict[str, object]] = []
    for offset in range(cycles):
        results.append(orchestrator.run_selected_symbols_cycle(trigger_event="historical_replay", upto_index=start_index + offset))
    db.commit()
    return {"cycles": cycles, "results": results}


@app.post("/api/replay/validation")
def replay_validation(
    payload: ReplayValidationRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    report = build_replay_validation_report(db, payload)
    return report.model_dump(mode="json")


@app.post("/api/live/sync")
def live_sync(symbol: str | None = None, db: Session = Depends(get_db)) -> dict[str, object]:
    settings_row = get_or_create_settings(db)
    auto_resume_precheck: dict[str, object] | None = None
    auto_resume_postcheck: dict[str, object] | None = None

    if settings_row.trading_paused:
        auto_resume_precheck = attempt_auto_resume(
            db,
            settings_row,
            trigger_source="api_live_sync_precheck",
        )
        db.flush()
        settings_row = get_or_create_settings(db)
    try:
        result = sync_live_state(db, settings_row, symbol=symbol)
    except Exception as exc:
        error_payload = {
            "error": str(exc),
            "auto_resume_precheck": auto_resume_precheck,
            "auto_resume_postcheck": auto_resume_postcheck,
            "auto_resume": auto_resume_precheck,
        }
        record_audit_event(
            db,
            event_type="live_sync_failed",
            entity_type="binance",
            entity_id=symbol or settings_row.default_symbol,
            severity="warning",
            message="Live exchange state sync failed.",
            payload=error_payload,
        )
        record_health_event(
            db,
            component="live_sync",
            status="error",
            message="Live exchange state sync failed.",
            payload=error_payload,
        )
        db.commit()
        raise HTTPException(status_code=400, detail=error_payload) from exc

    settings_row = get_or_create_settings(db)
    if auto_resume_precheck is not None or settings_row.trading_paused:
        auto_resume_postcheck = attempt_auto_resume(
            db,
            settings_row,
            trigger_source="api_live_sync_postcheck",
        )
        db.flush()
        settings_row = get_or_create_settings(db)

    auto_resume = auto_resume_postcheck or auto_resume_precheck
    payload = {
        **result,
        "auto_resume_precheck": auto_resume_precheck,
        "auto_resume_postcheck": auto_resume_postcheck,
        "auto_resume": auto_resume,
    }
    record_audit_event(
        db,
        event_type="live_sync",
        entity_type="binance",
        entity_id=symbol or settings_row.default_symbol,
        severity="info",
        message="Live exchange state synchronized.",
        payload=payload,
    )
    db.commit()
    return payload


@app.get("/api/backlog")
def backlog(db: Session = Depends(get_db)) -> dict[str, object]:
    payload: BacklogBoardResponse = get_backlog_board(db)
    return payload.model_dump(mode="json")


@app.get("/api/performance")
def performance_report(db: Session = Depends(get_db)) -> dict[str, object]:
    payload = build_signal_performance_report(db)
    return payload.model_dump(mode="json")


@app.post("/api/backlog/requests")
def create_backlog_request(
    payload: UserChangeRequestCreate,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        result = create_user_change_request(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    record_audit_event(
        db,
        event_type="user_change_request_created",
        entity_type="user_change_request",
        entity_id=str(result.id),
        severity="info",
        message="User change request created.",
        payload=result.model_dump(mode="json"),
    )
    db.commit()
    return result.model_dump(mode="json")


@app.post("/api/backlog/applied")
def create_backlog_applied_record(
    payload: AppliedChangeRecordCreate,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        result = create_applied_change_record(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    record_audit_event(
        db,
        event_type="applied_change_record_created",
        entity_type="applied_change_record",
        entity_id=str(result.id),
        severity="info",
        message="Applied change record created.",
        payload=result.model_dump(mode="json"),
    )
    db.commit()
    return result.model_dump(mode="json")


@app.get("/api/backlog/{backlog_id}")
def backlog_detail(backlog_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    payload: ProductBacklogDetailResponse | None = get_backlog_detail(db, backlog_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"Backlog item {backlog_id} not found.")
    return payload.model_dump(mode="json")


@app.get("/api/backlog/{backlog_id}/codex-draft")
def backlog_codex_draft(backlog_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    payload = get_backlog_detail(db, backlog_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"Backlog item {backlog_id} not found.")
    if payload.codex_prompt_draft is None:
        draft = BacklogCodexDraftResponse(
            available=False,
            title=f"Codex 실행 초안 #{backlog_id}",
            prompt="",
            generated_at=payload.updated_at,
            note="이미 적용되었거나 별도 초안이 필요하지 않은 backlog 항목입니다.",
        )
    else:
        draft = payload.codex_prompt_draft
    return draft.model_dump(mode="json")


@app.post("/api/backlog/{backlog_id}/auto-apply")
def backlog_auto_apply(backlog_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    try:
        payload: BacklogAutoApplyResult = auto_apply_backlog_item(db, backlog_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    record_audit_event(
        db,
        event_type="backlog_auto_applied",
        entity_type="product_backlog",
        entity_id=str(backlog_id),
        severity="info" if payload.auto_apply_supported else "warning",
        message=payload.message,
        payload=payload.model_dump(mode="json"),
    )
    db.commit()
    return payload.model_dump(mode="json")


@app.post("/api/backlog/auto-apply-supported")
def backlog_auto_apply_supported(db: Session = Depends(get_db)) -> dict[str, object]:
    payload: BacklogAutoApplyBatchResponse = auto_apply_supported_backlogs(db)
    record_audit_event(
        db,
        event_type="backlog_auto_apply_batch",
        entity_type="product_backlog",
        entity_id="batch",
        severity="info",
        message="Supported backlog items were auto-applied.",
        payload=payload.model_dump(mode="json"),
    )
    db.commit()
    return payload.model_dump(mode="json")
