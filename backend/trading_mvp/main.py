from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager, suppress
from time import monotonic

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session, sessionmaker

from trading_mvp.database import Base, engine, get_db
from trading_mvp.schemas import (
    AppSettingsUpdateRequest,
    BinanceAccountResponse,
    BinanceConnectionTestRequest,
    BinanceLiveTestOrderRequest,
    ManualLiveApprovalRequest,
    ManualNoTradeWindowEndRequest,
    ManualNoTradeWindowRequest,
    OpenAIConnectionTestRequest,
    OperatorEventViewClearRequest,
    OperatorEventViewRequest,
    ReplayValidationRequest,
)
from trading_mvp.services.audit import record_audit_event, record_health_event
from trading_mvp.services.binance_account import get_binance_account_snapshot
from trading_mvp.services.connectivity import (
    check_binance_connection,
    check_openai_connection,
)
from trading_mvp.services.dashboard import (
    get_agent_runs,
    get_alerts,
    get_audit_timeline,
    get_decisions,
    get_execution_quality_report,
    get_executions,
    get_feature_snapshots,
    get_market_snapshots,
    get_operator_dashboard,
    get_orders,
    get_overview,
    get_positions,
    get_profitability_dashboard,
    get_risk_checks,
    get_scheduler_runs,
)
from trading_mvp.services.execution import (
    poll_live_user_stream,
    run_live_test_order,
    sync_live_state,
)
from trading_mvp.services.orchestrator import TradingOrchestrator
from trading_mvp.services.pause_control import attempt_auto_resume
from trading_mvp.services.performance_reporting import build_signal_performance_report
from trading_mvp.services.replay_validation import build_replay_validation_report
from trading_mvp.services.scheduler import (
    maybe_refresh_exchange_sync_freshness,
    run_due_operational_cycles,
    run_due_windows,
    run_window,
)
from trading_mvp.services.seed import seed_demo_data
from trading_mvp.services.settings import (
    arm_live_execution,
    clear_operator_event_view,
    create_manual_no_trade_window,
    disarm_live_execution,
    end_manual_no_trade_window,
    get_or_create_settings,
    serialize_settings_ai_usage,
    serialize_settings_cadences,
    serialize_settings_view,
    set_trading_pause,
    update_manual_no_trade_window,
    update_settings,
    upsert_operator_event_view,
)

READ_REFRESH_DISPATCH_DEBOUNCE_SECONDS = 20.0
LOCAL_DEV_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"
_sqlite_background_write_guard = threading.Lock()
_exchange_sync_read_refresh_guard = threading.Lock()
_exchange_sync_read_refresh_inflight = False
_exchange_sync_read_refresh_last_started = 0.0


def _env_flag(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _background_scheduler_enabled() -> bool:
    default = engine.dialect.name != "sqlite"
    return _env_flag("TRADING_MVP_ENABLE_BACKGROUND_SCHEDULER", default=default)


def _background_user_stream_enabled() -> bool:
    default = engine.dialect.name != "sqlite"
    return _env_flag("TRADING_MVP_ENABLE_BACKGROUND_USER_STREAM", default=default)


@contextmanager
def _sqlite_write_lock() -> Iterator[None]:
    if engine.dialect.name != "sqlite":
        yield
        return
    _sqlite_background_write_guard.acquire()
    try:
        yield
    finally:
        _sqlite_background_write_guard.release()


async def _background_scheduler_loop() -> None:
    while True:
        interval_seconds = await asyncio.to_thread(
            _run_background_tick_with_sqlite_guard,
            _run_background_scheduler_tick,
            1,
        )
        await asyncio.sleep(interval_seconds)


async def _background_user_stream_loop() -> None:
    while True:
        sleep_seconds = await asyncio.to_thread(
            _run_background_tick_with_sqlite_guard,
            _run_background_user_stream_tick,
            1,
        )
        await asyncio.sleep(sleep_seconds)


def _run_background_tick_with_sqlite_guard(tick: Callable[[], int], blocked_sleep_seconds: int) -> int:
    if engine.dialect.name != "sqlite":
        return tick()
    # SQLite local/test environments cannot tolerate overlapping background writes.
    # Skip the overlapping tick and retry soon instead of queueing another writer.
    if not _sqlite_background_write_guard.acquire(blocking=False):
        return blocked_sleep_seconds
    try:
        return tick()
    finally:
        _sqlite_background_write_guard.release()


def _record_background_loop_failure(
    session_factory,
    *,
    event_type: str,
    entity_type: str,
    entity_id: str,
    severity: str,
    component: str,
    message: str,
    payload: dict[str, object],
) -> None:
    # Recovery logging is best-effort only. If SQLite is still locked, the loop
    # must survive and retry on the next tick rather than crash here.
    try:
        with session_factory() as recovery_session:
            record_audit_event(
                recovery_session,
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                severity=severity,
                message=message,
                payload=payload,
            )
            record_health_event(
                recovery_session,
                component=component,
                status="error",
                message=message,
                payload=payload,
            )
            recovery_session.commit()
    except Exception:
        return


def _run_background_scheduler_tick() -> int:
    polling_session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    with polling_session_factory() as session:
        interval_seconds = 15
        try:
            settings_row = get_or_create_settings(session)
            interval_seconds = max(15, min(int(settings_row.exchange_sync_interval_seconds), 15))
            run_due_operational_cycles(session)
            run_due_windows(session)
            session.commit()
        except Exception as exc:
            session.rollback()
            _record_background_loop_failure(
                polling_session_factory,
                event_type="background_scheduler_failed",
                entity_type="scheduler",
                entity_id="background",
                severity="error",
                component="scheduler",
                message="Background scheduler loop failed.",
                payload={"error": str(exc)},
            )
        return interval_seconds


def _run_background_user_stream_tick() -> int:
    polling_session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    with polling_session_factory() as session:
        sleep_seconds = 5
        entity_id = "background"
        try:
            settings_row = get_or_create_settings(session)
            entity_id = settings_row.default_symbol
            tracked_symbols = settings_row.tracked_symbols or [settings_row.default_symbol]
            stream_result = poll_live_user_stream(
                session,
                settings_row,
                max_events=max(8, len(tracked_symbols) * 4),
                idle_timeout_seconds=2.0,
            )
            session.commit()
            stream_health = str(stream_result.get("stream_health") or "idle")
            if stream_health == "connected":
                sleep_seconds = 1
            elif stream_health == "unavailable":
                sleep_seconds = 10
        except Exception as exc:
            session.rollback()
            _record_background_loop_failure(
                polling_session_factory,
                event_type="background_user_stream_failed",
                entity_type="binance",
                entity_id=entity_id,
                severity="warning",
                component="user_stream",
                message="Background Binance futures user stream loop failed.",
                payload={"error": str(exc)},
            )
        return sleep_seconds


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    Base.metadata.create_all(bind=engine)
    tasks: list[asyncio.Task[None]] = []
    if _background_scheduler_enabled():
        tasks.append(asyncio.create_task(_background_scheduler_loop()))
    if _background_user_stream_enabled():
        tasks.append(asyncio.create_task(_background_user_stream_loop()))
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="Trading MVP API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://127.0.0.1",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_origin_regex=LOCAL_DEV_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _run_exchange_sync_read_refresh(triggered_by: str) -> None:
    global _exchange_sync_read_refresh_inflight

    polling_session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    try:
        with polling_session_factory() as session:
            refresh_result = maybe_refresh_exchange_sync_freshness(session, triggered_by=triggered_by)
            if refresh_result is not None:
                session.commit()
    except Exception as exc:
        with polling_session_factory() as session:
            record_audit_event(
                session,
                event_type="exchange_sync_read_refresh_failed",
                entity_type="scheduler",
                entity_id="exchange_sync_cycle",
                severity="warning",
                message="Read-triggered exchange sync refresh failed.",
                payload={"error": str(exc), "triggered_by": triggered_by},
            )
            record_health_event(
                session,
                component="exchange_sync",
                status="error",
                message="Read-triggered exchange sync refresh failed.",
                payload={"error": str(exc), "triggered_by": triggered_by},
            )
            session.commit()
    finally:
        with _exchange_sync_read_refresh_guard:
            _exchange_sync_read_refresh_inflight = False


def _read_trigger_refresh_enabled() -> bool:
    # SQLite local/test environments are write-contention-prone, so read paths
    # must not enqueue background sync writes from GET requests.
    return engine.dialect.name != "sqlite"


def _refresh_exchange_sync_for_read(*, triggered_by: str) -> bool:
    global _exchange_sync_read_refresh_inflight, _exchange_sync_read_refresh_last_started

    if not _read_trigger_refresh_enabled():
        return False

    started_at = monotonic()
    with _exchange_sync_read_refresh_guard:
        if _exchange_sync_read_refresh_inflight:
            return False
        if started_at - _exchange_sync_read_refresh_last_started < READ_REFRESH_DISPATCH_DEBOUNCE_SECONDS:
            return False
        _exchange_sync_read_refresh_inflight = True
        _exchange_sync_read_refresh_last_started = started_at
    threading.Thread(
        target=_run_exchange_sync_read_refresh,
        args=(triggered_by,),
        daemon=True,
        name="exchange-sync-read-refresh",
    ).start()
    return True

@app.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok", "mode": "service_ready", "database": "ready"}


@app.post("/api/system/seed")
def seed_system(db: Session = Depends(get_db)) -> dict[str, object]:
    with _sqlite_write_lock():
        return seed_demo_data(db)


@app.get("/api/dashboard/overview")
def dashboard_overview(db: Session = Depends(get_db)) -> dict[str, object]:
    _refresh_exchange_sync_for_read(triggered_by="api_dashboard_overview")
    return get_overview(db).model_dump(mode="json")


@app.get("/api/dashboard/operator")
def dashboard_operator(db: Session = Depends(get_db)) -> dict[str, object]:
    _refresh_exchange_sync_for_read(triggered_by="api_dashboard_operator")
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
    return serialize_settings_view(get_or_create_settings(db))


@app.get("/api/settings/cadences")
def settings_cadences(db: Session = Depends(get_db)) -> dict[str, object]:
    return serialize_settings_cadences(get_or_create_settings(db))


@app.get("/api/settings/ai-usage")
def settings_ai_usage(db: Session = Depends(get_db)) -> dict[str, object]:
    return serialize_settings_ai_usage(get_or_create_settings(db))


@app.get("/api/binance/account")
def binance_account(db: Session = Depends(get_db)) -> dict[str, object]:
    payload: BinanceAccountResponse = get_binance_account_snapshot(db)
    return payload.model_dump(mode="json")


@app.put("/api/settings")
def settings_update(payload: AppSettingsUpdateRequest, db: Session = Depends(get_db)) -> dict[str, object]:
    with _sqlite_write_lock():
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
        return serialize_settings_view(row)


@app.put("/api/settings/operator-event-view")
def operator_event_view_upsert(
    payload: OperatorEventViewRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    with _sqlite_write_lock():
        row, _changed = upsert_operator_event_view(db, payload)
        db.commit()
        return serialize_settings_view(row)


@app.post("/api/settings/operator-event-view/clear")
def operator_event_view_clear(
    payload: OperatorEventViewClearRequest | None = None,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    with _sqlite_write_lock():
        actor = payload.created_by if payload is not None else "operator-ui"
        row, _changed = clear_operator_event_view(db, actor=actor)
        db.commit()
        return serialize_settings_view(row)


@app.post("/api/settings/manual-no-trade-windows")
def manual_no_trade_window_create(
    payload: ManualNoTradeWindowRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    with _sqlite_write_lock():
        row, _window = create_manual_no_trade_window(db, payload)
        db.commit()
        return serialize_settings_view(row)


@app.put("/api/settings/manual-no-trade-windows/{window_id}")
def manual_no_trade_window_update(
    window_id: str,
    payload: ManualNoTradeWindowRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    with _sqlite_write_lock():
        try:
            row, _window, _changed = update_manual_no_trade_window(db, window_id=window_id, payload=payload)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        db.commit()
        return serialize_settings_view(row)


@app.post("/api/settings/manual-no-trade-windows/{window_id}/end")
def manual_no_trade_window_end(
    window_id: str,
    payload: ManualNoTradeWindowEndRequest | None = None,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    with _sqlite_write_lock():
        try:
            row, _window, _changed = end_manual_no_trade_window(
                db,
                window_id=window_id,
                actor=payload.created_by if payload is not None else "operator-ui",
                end_at=payload.end_at if payload is not None else None,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        db.commit()
        return serialize_settings_view(row)


@app.post("/api/settings/pause")
def pause_trading(db: Session = Depends(get_db)) -> dict[str, object]:
    with _sqlite_write_lock():
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
        return serialize_settings_view(row)


@app.post("/api/settings/resume")
def resume_trading(db: Session = Depends(get_db)) -> dict[str, object]:
    with _sqlite_write_lock():
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
        return serialize_settings_view(row)


@app.post("/api/settings/live/arm")
def arm_live(
    payload: ManualLiveApprovalRequest | None = None,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    with _sqlite_write_lock():
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
        return serialize_settings_view(row)


@app.post("/api/settings/live/disarm")
def disarm_live(db: Session = Depends(get_db)) -> dict[str, object]:
    with _sqlite_write_lock():
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
        return serialize_settings_view(row)


@app.post("/api/settings/test/openai")
def openai_connection_test(
    payload: OpenAIConnectionTestRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    with _sqlite_write_lock():
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
    with _sqlite_write_lock():
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
    with _sqlite_write_lock():
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
    with _sqlite_write_lock():
        output = TradingOrchestrator(db).run_selected_symbols_cycle(trigger_event="manual")
        db.commit()
        return output


@app.post("/api/reviews/{window}")
def run_review(window: str, db: Session = Depends(get_db)) -> dict[str, object]:
    with _sqlite_write_lock():
        if window != "1h":
            raise HTTPException(status_code=400, detail="Only 1h review window is enabled in current live-core scope.")
        output = run_window(db, window, triggered_by="manual")
        db.commit()
        return output


@app.post("/api/replay/run")
def run_replay(cycles: int = 5, start_index: int = 120, db: Session = Depends(get_db)) -> dict[str, object]:
    with _sqlite_write_lock():
        orchestrator = TradingOrchestrator(db)
        results: list[dict[str, object]] = []
        for offset in range(cycles):
            results.append(
                orchestrator.run_selected_symbols_cycle(trigger_event="historical_replay", upto_index=start_index + offset)
            )
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
    with _sqlite_write_lock():
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


@app.get("/api/performance")
def performance_report(db: Session = Depends(get_db)) -> dict[str, object]:
    payload = build_signal_performance_report(db)
    return payload.model_dump(mode="json")
