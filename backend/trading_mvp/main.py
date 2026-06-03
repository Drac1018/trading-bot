from __future__ import annotations

import asyncio
import hashlib
import os
import re
import secrets
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager, suppress
from time import monotonic

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from trading_mvp.config import (
    app_env_is_production,
    get_settings,
    split_csv,
    validate_runtime_secret_seed,
)
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
from trading_mvp.services.ai_usage import warm_ai_usage_metrics_cache
from trading_mvp.services.audit import record_audit_event, record_health_event
from trading_mvp.services.binance_account import (
    get_binance_account_snapshot,
    get_cached_binance_account_snapshot,
    get_local_binance_account_snapshot,
    mark_binance_account_refresh_requested,
    mark_binance_account_refresh_started,
    store_binance_account_cache_failure,
    store_binance_account_cache_result,
)
from trading_mvp.services.binance_market_stream import (
    BinanceMarketStreamListener,
    build_market_stream_state,
)
from trading_mvp.services.connectivity import (
    check_binance_connection,
    check_openai_connection,
)
from trading_mvp.services.dashboard import (
    get_agent_runs,
    get_alerts,
    get_analytics_cost_breakdown,
    get_audit_event_detail,
    get_audit_timeline,
    get_decisions,
    get_execution_quality_report,
    get_executions,
    get_feature_snapshots,
    get_market_chart_markers,
    get_market_snapshots,
    get_operator_dashboard,
    get_orders,
    get_overview,
    get_positions,
    get_profitability_dashboard,
    get_risk_checks,
    get_scheduler_runs,
    warm_profitability_dashboard_cache,
)
from trading_mvp.services.execution import (
    poll_live_user_stream,
    run_live_test_order,
    sync_live_state,
)
from trading_mvp.services.market_data_cache import (
    MARKET_DATA_CACHE_BACKEND_PROCESS_LOCAL,
    MARKET_DATA_CACHE_BACKEND_REDIS,
    MARKET_DATA_CACHE_ENV_MAINNET,
    MARKET_DATA_CACHE_ENV_TESTNET,
    MARKET_DATA_CACHE_MARKET_TYPE_USDM_FUTURES,
    MARKET_DATA_CACHE_SCOPE_PROCESS_LOCAL,
    MARKET_DATA_CACHE_SCOPE_SHARED,
    write_closed_kline_event_to_redis,
)
from trading_mvp.services.orchestrator import TradingOrchestrator
from trading_mvp.services.pause_control import attempt_auto_resume
from trading_mvp.services.performance_reporting import (
    build_opportunity_attribution_report,
    build_opportunity_attribution_summary,
    build_signal_performance_report,
    warm_opportunity_attribution_summary_cache,
)
from trading_mvp.services.replay_validation import build_replay_validation_report
from trading_mvp.services.runtime_state import replace_market_stream_detail
from trading_mvp.services.safety_checks import get_safety_check_detail, get_safety_check_summaries
from trading_mvp.services.scheduler import (
    abandon_stale_scheduler_runs,
    maybe_refresh_exchange_sync_freshness,
    run_due_exchange_sync_cycle,
    run_due_operational_cycles,
    run_due_windows,
    run_window,
)
from trading_mvp.services.seed import seed_demo_data
from trading_mvp.services.service_gate import build_service_switch_gate_snapshot
from trading_mvp.services.settings import (
    LiveApprovalWindowError,
    arm_live_execution,
    clear_operator_event_view,
    create_manual_no_trade_window,
    disarm_live_execution,
    end_manual_no_trade_window,
    get_or_create_settings,
    get_rollout_mode,
    live_arm_blocking_reason_codes,
    resolve_live_approval_window_minutes,
    serialize_settings_ai_usage,
    serialize_settings_cadences,
    serialize_settings_view,
    set_trading_pause,
    update_manual_no_trade_window,
    update_settings,
    upsert_operator_event_view,
)
from trading_mvp.services.strategy_performance_report import build_short_side_drag_report

READ_REFRESH_DISPATCH_DEBOUNCE_SECONDS = 20.0
OPERATOR_HOME_CACHE_TTL_SECONDS = 2.0
LOCAL_DEV_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"
LOCAL_DEV_ALLOWED_ORIGINS = [
    "http://localhost",
    "http://127.0.0.1",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
OPERATOR_API_KEY_HEADER = "X-Operator-API-Key"
OPERATOR_INTENT_HEADER = "X-Operator-Intent"
OPERATOR_WRITE_ANY_INTENT = "operator.write"
OPERATOR_CORS_ALLOW_METHODS = ["GET", "POST", "PUT", "OPTIONS"]
OPERATOR_CORS_ALLOW_HEADERS = [
    "Authorization",
    "Content-Type",
    OPERATOR_API_KEY_HEADER,
    OPERATOR_INTENT_HEADER,
]
OPERATOR_READ_METHODS = {"GET", "HEAD"}
OPERATOR_ROLE_VIEWER = "viewer"
OPERATOR_ROLE_OPERATOR = "operator"
OPERATOR_ROLE_ADMIN = "admin"
OPERATOR_ROLE_ORDER = {
    OPERATOR_ROLE_VIEWER: 10,
    OPERATOR_ROLE_OPERATOR: 20,
    OPERATOR_ROLE_ADMIN: 30,
}
OPERATOR_WRITE_INTENT_MIN_ROLE = {
    "system.seed": OPERATOR_ROLE_ADMIN,
    "binance.account_refresh": OPERATOR_ROLE_OPERATOR,
    "settings.update": OPERATOR_ROLE_ADMIN,
    "settings.operator_event_view": OPERATOR_ROLE_OPERATOR,
    "settings.operator_event_view_clear": OPERATOR_ROLE_OPERATOR,
    "settings.manual_no_trade_window": OPERATOR_ROLE_OPERATOR,
    "settings.pause": OPERATOR_ROLE_OPERATOR,
    "settings.resume": OPERATOR_ROLE_OPERATOR,
    "settings.resume_attempt": OPERATOR_ROLE_OPERATOR,
    "settings.live_arm": OPERATOR_ROLE_ADMIN,
    "settings.live_disarm": OPERATOR_ROLE_OPERATOR,
    "settings.integration_test": OPERATOR_ROLE_OPERATOR,
    "settings.live_test_order": OPERATOR_ROLE_ADMIN,
    "cycle.run": OPERATOR_ROLE_OPERATOR,
    "review.run": OPERATOR_ROLE_OPERATOR,
    "replay.run": OPERATOR_ROLE_OPERATOR,
    "replay.validation": OPERATOR_ROLE_OPERATOR,
    "live.sync": OPERATOR_ROLE_OPERATOR,
}
MAX_LIST_LIMIT = 200
_sqlite_background_write_guard = threading.Lock()
_exchange_sync_read_refresh_guard = threading.Lock()
_binance_account_cache_refresh_guard = threading.Lock()
_operator_home_cache_guard = threading.Lock()
_operator_rate_limit_guard = threading.Lock()
_operator_home_cache_started = 0.0
_operator_home_cache_key: str | None = None
_operator_home_cache_payload: dict[str, object] | None = None
_operator_rate_limit_buckets: dict[str, list[float]] = {}
_exchange_sync_read_refresh_inflight = False
_exchange_sync_read_refresh_last_started = 0.0


def _bounded_limit(value: int, *, default: int = 50, maximum: int = MAX_LIST_LIMIT) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(normalized, maximum))


def _history_response_compact(compact: bool | None, *, include_payload: bool) -> bool:
    if include_payload:
        return False
    return True if compact is None else compact


def _cors_allowed_origins() -> list[str]:
    settings = get_settings()
    explicit_origins = split_csv(settings.cors_allowed_origins)
    if explicit_origins or app_env_is_production(settings.app_env):
        return explicit_origins
    return LOCAL_DEV_ALLOWED_ORIGINS if settings.cors_allow_local_dev_origins else []


def _cors_origin_regex() -> str | None:
    settings = get_settings()
    if split_csv(settings.cors_allowed_origins) or app_env_is_production(settings.app_env):
        return None
    return LOCAL_DEV_ORIGIN_REGEX if settings.cors_allow_local_dev_origins else None


def _operator_api_key_required(*, rollout_mode: object | None = None) -> bool:
    settings = get_settings()
    return (
        bool(settings.operator_api_key.strip())
        or bool(settings.operator_viewer_api_key.strip())
        or bool(settings.operator_trader_api_key.strip())
        or bool(settings.operator_admin_api_key.strip())
        or app_env_is_production(settings.app_env)
        or str(rollout_mode or "").strip().lower() == "full_live"
    )


def _operator_auth_unavailable_message() -> str:
    return "OPERATOR_API_KEY or role-specific operator API keys are required for write APIs in production/full_live runtime."


def _operator_read_auth_unavailable_message() -> str:
    return "OPERATOR_API_KEY or role-specific operator API keys are required for read APIs in production/full_live runtime."


def _operator_read_api_key_required() -> bool:
    settings = get_settings()
    return (
        bool(settings.operator_api_key.strip())
        or bool(settings.operator_viewer_api_key.strip())
        or bool(settings.operator_trader_api_key.strip())
        or bool(settings.operator_admin_api_key.strip())
        or app_env_is_production(settings.app_env)
    )


def _operator_configured_credentials() -> list[tuple[str, str]]:
    settings = get_settings()
    credentials: list[tuple[str, str]] = []
    # OPERATOR_API_KEY remains admin-equivalent for backward compatibility with deployed services.
    for key, role in (
        (settings.operator_api_key, OPERATOR_ROLE_ADMIN),
        (settings.operator_viewer_api_key, OPERATOR_ROLE_VIEWER),
        (settings.operator_trader_api_key, OPERATOR_ROLE_OPERATOR),
        (settings.operator_admin_api_key, OPERATOR_ROLE_ADMIN),
    ):
        normalized = key.strip()
        if normalized:
            credentials.append((normalized, role))
    return credentials


def _operator_credential_role(submitted: str | None) -> str | None:
    normalized = str(submitted or "").strip()
    if not normalized:
        return None
    for expected, role in _operator_configured_credentials():
        if secrets.compare_digest(normalized, expected):
            return role
    return None


def _operator_role_allowed(role: str | None, minimum_role: str) -> bool:
    return OPERATOR_ROLE_ORDER.get(str(role or ""), 0) >= OPERATOR_ROLE_ORDER[minimum_role]


def _operator_api_key_valid(submitted: str | None) -> bool:
    return _operator_credential_role(submitted) is not None


def _operator_rate_limit_required() -> bool:
    settings = get_settings()
    return bool(_operator_configured_credentials()) or app_env_is_production(settings.app_env)


def _raise_runtime_configuration_error(db: Session) -> None:
    settings = get_settings()
    settings_row = get_or_create_settings(db)
    rollout_mode = get_rollout_mode(settings_row)
    validate_runtime_secret_seed(settings, rollout_mode=rollout_mode)


def _runtime_configuration_error(db: Session) -> str | None:
    try:
        _raise_runtime_configuration_error(db)
    except RuntimeError as exc:
        return str(exc)
    return None


def _require_operator_key_for_rollout(rollout_mode: object | None) -> None:
    if _operator_api_key_required(rollout_mode=rollout_mode) and not _operator_configured_credentials():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_operator_auth_unavailable_message())


def require_operator_api_key(
    x_operator_api_key: str | None = Header(default=None, alias=OPERATOR_API_KEY_HEADER),
    db: Session = Depends(get_db),
) -> None:
    settings_row = get_or_create_settings(db)
    rollout_mode = get_rollout_mode(settings_row)
    if not _operator_api_key_required(rollout_mode=rollout_mode):
        return
    if not _operator_configured_credentials():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_operator_auth_unavailable_message())
    submitted_role = _operator_credential_role(x_operator_api_key)
    if submitted_role is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid operator API key.")
    if not _operator_role_allowed(submitted_role, OPERATOR_ROLE_OPERATOR):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operator role is not allowed for write APIs.")


def _request_origin_allowed(request: Request) -> bool:
    origin = str(request.headers.get("origin") or "").strip()
    if not origin:
        return True
    if origin in set(_cors_allowed_origins()):
        return True
    origin_regex = _cors_origin_regex()
    if origin_regex and re.fullmatch(origin_regex, origin):
        return True
    return origin == f"{request.url.scheme}://{request.headers.get('host')}"


def _operator_generic_intent_allowed(*, rollout_mode: object | None = None) -> bool:
    settings = get_settings()
    return not (
        app_env_is_production(settings.app_env)
        or str(rollout_mode or "").strip().lower() == "full_live"
    )


def _operator_intent_matches(submitted: str | None, expected: str, *, allow_generic: bool = True) -> bool:
    normalized = str(submitted or "").strip()
    return normalized == expected or (allow_generic and normalized == OPERATOR_WRITE_ANY_INTENT)


def _record_operator_write_gate(
    db: Session,
    request: Request,
    *,
    allowed: bool,
    expected_intent: str,
    submitted_intent: str | None,
    operator_role: str | None = None,
    reason_code: str | None = None,
    commit: bool = False,
) -> None:
    record_audit_event(
        db,
        event_type="operator_write_authorized" if allowed else "operator_write_denied",
        entity_type="operator_write_gate",
        entity_id=f"{request.method} {request.url.path}",
        severity="info" if allowed else "warning",
        message="Operator write gate authorized request." if allowed else "Operator write gate denied request.",
        payload={
            "method": request.method,
            "path": request.url.path,
            "expected_intent": expected_intent,
            "submitted_intent": submitted_intent,
            "operator_role": operator_role,
            "origin": request.headers.get("origin"),
            "reason_code": reason_code,
        },
    )
    if commit:
        db.commit()


def require_operator_write_intent(expected_intent: str) -> Callable[..., None]:
    def dependency(
        request: Request,
        x_operator_api_key: str | None = Header(default=None, alias=OPERATOR_API_KEY_HEADER),
        x_operator_intent: str | None = Header(default=None, alias=OPERATOR_INTENT_HEADER),
        db: Session = Depends(get_db),
    ) -> None:
        settings_row = get_or_create_settings(db)
        rollout_mode = get_rollout_mode(settings_row)
        credentials = _operator_configured_credentials()
        if not credentials:
            _record_operator_write_gate(
                db,
                request,
                allowed=False,
                expected_intent=expected_intent,
                submitted_intent=x_operator_intent,
                operator_role=None,
                reason_code="operator_auth_unavailable",
                commit=True,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=_operator_auth_unavailable_message(),
            )
        submitted_role = _operator_credential_role(x_operator_api_key)
        if submitted_role is None:
            _record_operator_write_gate(
                db,
                request,
                allowed=False,
                expected_intent=expected_intent,
                submitted_intent=x_operator_intent,
                operator_role=None,
                reason_code="operator_auth_failed",
                commit=True,
            )
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid operator API key.")
        minimum_role = OPERATOR_WRITE_INTENT_MIN_ROLE.get(expected_intent, OPERATOR_ROLE_OPERATOR)
        if not _operator_role_allowed(submitted_role, minimum_role):
            _record_operator_write_gate(
                db,
                request,
                allowed=False,
                expected_intent=expected_intent,
                submitted_intent=x_operator_intent,
                operator_role=submitted_role,
                reason_code="operator_role_forbidden",
                commit=True,
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operator role is not allowed.")
        if not _request_origin_allowed(request):
            _record_operator_write_gate(
                db,
                request,
                allowed=False,
                expected_intent=expected_intent,
                submitted_intent=x_operator_intent,
                operator_role=submitted_role,
                reason_code="origin_not_allowed",
                commit=True,
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operator write origin is not allowed.")
        allow_generic_intent = _operator_generic_intent_allowed(rollout_mode=rollout_mode)
        if not _operator_intent_matches(x_operator_intent, expected_intent, allow_generic=allow_generic_intent):
            _record_operator_write_gate(
                db,
                request,
                allowed=False,
                expected_intent=expected_intent,
                submitted_intent=x_operator_intent,
                operator_role=submitted_role,
                reason_code="operator_intent_mismatch",
                commit=True,
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operator intent header is required.")
        _record_operator_write_gate(
            db,
            request,
            allowed=True,
            expected_intent=expected_intent,
            submitted_intent=x_operator_intent,
            operator_role=submitted_role,
            commit=True,
        )

    return dependency


def _operator_home_db_identity(db: Session) -> str:
    bind = db.get_bind()
    return str(bind.url) if bind is not None else "unknown"


def _get_operator_home_dashboard_payload(db: Session) -> dict[str, object]:
    global _operator_home_cache_key, _operator_home_cache_payload, _operator_home_cache_started

    now = monotonic()
    cache_key = _operator_home_db_identity(db)
    with _operator_home_cache_guard:
        if (
            _operator_home_cache_payload is not None
            and _operator_home_cache_key == cache_key
            and now - _operator_home_cache_started <= OPERATOR_HOME_CACHE_TTL_SECONDS
        ):
            return dict(_operator_home_cache_payload)

    payload = get_operator_dashboard(db, view="home").model_dump(mode="json")
    with _operator_home_cache_guard:
        _operator_home_cache_key = cache_key
        _operator_home_cache_started = monotonic()
        _operator_home_cache_payload = dict(payload)
    return payload


def _env_flag(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _background_loop_default_enabled() -> bool:
    return engine.dialect.name != "sqlite" and _env_flag("TRADING_MVP_SERVICE_RUNTIME", default=False)


def _background_scheduler_enabled() -> bool:
    default = _background_loop_default_enabled()
    return _env_flag("TRADING_MVP_ENABLE_BACKGROUND_SCHEDULER", default=default)


def _background_user_stream_enabled() -> bool:
    default = _background_loop_default_enabled()
    return _env_flag("TRADING_MVP_ENABLE_BACKGROUND_USER_STREAM", default=default)


def _background_market_stream_enabled() -> bool:
    default = _background_loop_default_enabled()
    return _env_flag("TRADING_MVP_ENABLE_BACKGROUND_MARKET_STREAM", default=default)


def _market_stream_runtime_config_fields(config: dict[str, object]) -> dict[str, object]:
    enabled = bool(config.get("enabled"))
    redis_url = str(config.get("redis_url") or "").strip()
    shared_cache_enabled = bool(redis_url)
    cache_environment = MARKET_DATA_CACHE_ENV_TESTNET if bool(config.get("testnet_enabled")) else MARKET_DATA_CACHE_ENV_MAINNET
    configured_cache_backend = (
        MARKET_DATA_CACHE_BACKEND_REDIS if shared_cache_enabled else MARKET_DATA_CACHE_BACKEND_PROCESS_LOCAL
    )
    return {
        "stream_enabled": enabled,
        "background_enabled": enabled,
        "database_dialect": engine.dialect.name,
        "cache_backend": configured_cache_backend,
        "configured_cache_backend": configured_cache_backend,
        "cache_market_type": MARKET_DATA_CACHE_MARKET_TYPE_USDM_FUTURES,
        "cache_environment": cache_environment,
        "cache_scope": MARKET_DATA_CACHE_SCOPE_SHARED if shared_cache_enabled else MARKET_DATA_CACHE_SCOPE_PROCESS_LOCAL,
        "shared_cache_supported": shared_cache_enabled,
        "redis_required": False,
        "redis_configured": shared_cache_enabled,
        "redis_connected": None if shared_cache_enabled else False,
        "sqlite_default_disabled": bool(config.get("sqlite_default_disabled")),
    }


def _manual_pause_active(settings_row) -> bool:
    return bool(settings_row.trading_paused) and settings_row.pause_origin == "manual"


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


async def _background_exchange_sync_loop() -> None:
    while True:
        interval_seconds = await asyncio.to_thread(
            _run_background_tick_with_sqlite_guard,
            _run_background_exchange_sync_tick,
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


async def _background_market_stream_loop() -> None:
    state: dict[str, object] = {}
    while True:
        config = await asyncio.to_thread(_load_background_market_stream_config)
        if not bool(config.get("enabled")):
            state = build_market_stream_state(
                {
                    **state,
                    **_market_stream_runtime_config_fields(config),
                    "status": "unavailable",
                    "stream_enabled": False,
                    "background_enabled": False,
                    "last_error": config.get("disabled_reason"),
                    "reason_code": config.get("disabled_reason"),
                    "subscribed_symbols": config.get("symbols", []),
                    "subscribed_timeframes": config.get("timeframes", []),
                    "stream_count": 0,
                }
            )
            await asyncio.to_thread(_persist_background_market_stream_state, state, [])
            await asyncio.sleep(15)
            continue
        listener = BinanceMarketStreamListener(
            symbols=[str(item) for item in config.get("symbols", [])],
            timeframes=[str(item) for item in config.get("timeframes", [])],
            testnet_enabled=bool(config.get("testnet_enabled")),
        )
        result = await listener.collect_once(
            max_events=64,
            idle_timeout_seconds=30.0,
            state=state,
        )
        shared_cache_state: dict[str, object] = {}
        redis_url = str(config.get("redis_url") or "").strip()
        cache_environment = (
            MARKET_DATA_CACHE_ENV_TESTNET if bool(config.get("testnet_enabled")) else MARKET_DATA_CACHE_ENV_MAINNET
        )
        for event in result.get("events", []):
            if not isinstance(event, dict) or not bool(event.get("closed")):
                continue
            shared_cache_state = write_closed_kline_event_to_redis(
                event,
                redis_url=redis_url,
                environment=cache_environment,
            )
        state = build_market_stream_state(
            {
                **dict(result.get("state") or {}),
                **shared_cache_state,
                **_market_stream_runtime_config_fields(config),
            }
        )
        await asyncio.to_thread(_persist_background_market_stream_state, state, result.get("issues", []))
        await asyncio.sleep(1 if state.get("status") == "connected" else 5)


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
    interval_seconds = 15
    with polling_session_factory() as session:
        try:
            settings_row = get_or_create_settings(session)
            interval_seconds = max(15, min(int(settings_row.exchange_sync_interval_seconds), 15))
            if _manual_pause_active(settings_row):
                session.rollback()
                return interval_seconds
            abandon_stale_scheduler_runs(session)
            session.commit()
            run_due_operational_cycles(
                session,
                include_exchange_sync=False,
                commit_between=True,
                continue_on_error=True,
                session_factory=polling_session_factory,
            )
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
                payload={
                    "error": str(exc),
                    "error_class": exc.__class__.__name__,
                    "session_rolled_back_before_logging": True,
                },
            )
        return interval_seconds


def _run_background_exchange_sync_tick() -> int:
    polling_session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    with polling_session_factory() as session:
        interval_seconds = 15
        try:
            settings_row = get_or_create_settings(session)
            interval_seconds = max(15, min(int(settings_row.exchange_sync_interval_seconds), 15))
            if _manual_pause_active(settings_row):
                session.rollback()
                return interval_seconds
            run_due_exchange_sync_cycle(session)
            session.commit()
        except Exception as exc:
            session.rollback()
            _record_background_loop_failure(
                polling_session_factory,
                event_type="background_exchange_sync_failed",
                entity_type="scheduler",
                entity_id="exchange_sync",
                severity="error",
                component="exchange_sync",
                message="Background exchange sync loop failed.",
                payload={
                    "error": str(exc),
                    "error_class": exc.__class__.__name__,
                    "session_rolled_back_before_logging": True,
                },
            )
        return interval_seconds


def _run_background_user_stream_tick() -> int:
    polling_session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    with polling_session_factory() as session:
        sleep_seconds = 5
        entity_id = "background"
        try:
            settings_row = get_or_create_settings(session)
            if _manual_pause_active(settings_row):
                session.rollback()
                return sleep_seconds
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


def _market_stream_configured_timeframes(settings_row) -> list[str]:
    candidates: list[str] = ["1m", str(settings_row.default_timeframe or "15m"), "1h", "4h"]
    for item in settings_row.symbol_cadence_overrides or []:
        if isinstance(item, dict):
            timeframe = str(item.get("timeframe_override") or "").strip()
            if timeframe:
                candidates.append(timeframe)
    ordered: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        ordered.append(value)
        seen.add(value)
    return ordered


def _load_background_market_stream_config() -> dict[str, object]:
    app_settings = get_settings()
    polling_session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    with polling_session_factory() as session:
        settings_row = get_or_create_settings(session)
        symbols = [
            str(symbol or "").strip().upper()
            for symbol in (settings_row.tracked_symbols or [settings_row.default_symbol])
            if str(symbol or "").strip()
        ]
        timeframes = _market_stream_configured_timeframes(settings_row)
        enabled = bool(settings_row.binance_market_data_enabled and settings_row.binance_futures_enabled and symbols and timeframes)
        disabled_reason = None
        if not settings_row.binance_market_data_enabled:
            disabled_reason = "BINANCE_MARKET_DATA_DISABLED"
        elif not settings_row.binance_futures_enabled:
            disabled_reason = "BINANCE_FUTURES_DISABLED"
        elif not symbols or not timeframes:
            disabled_reason = "NO_MARKET_STREAMS_CONFIGURED"
        return {
            "enabled": enabled,
            "disabled_reason": disabled_reason,
            "symbols": symbols,
            "timeframes": timeframes,
            "testnet_enabled": bool(settings_row.binance_testnet_enabled),
            "redis_url": app_settings.redis_url,
            "database_dialect": engine.dialect.name,
            "cache_backend": MARKET_DATA_CACHE_BACKEND_REDIS
            if app_settings.redis_url
            else MARKET_DATA_CACHE_BACKEND_PROCESS_LOCAL,
            "cache_market_type": MARKET_DATA_CACHE_MARKET_TYPE_USDM_FUTURES,
            "cache_environment": MARKET_DATA_CACHE_ENV_TESTNET
            if settings_row.binance_testnet_enabled
            else MARKET_DATA_CACHE_ENV_MAINNET,
            "cache_scope": MARKET_DATA_CACHE_SCOPE_SHARED
            if app_settings.redis_url
            else MARKET_DATA_CACHE_SCOPE_PROCESS_LOCAL,
            "shared_cache_supported": bool(app_settings.redis_url),
            "sqlite_default_disabled": engine.dialect.name == "sqlite"
            and os.getenv("TRADING_MVP_ENABLE_BACKGROUND_MARKET_STREAM") is None,
        }


def _persist_background_market_stream_state(state: dict[str, object], issues: object) -> None:
    polling_session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    with polling_session_factory() as session:
        try:
            settings_row = get_or_create_settings(session)
            replace_market_stream_detail(settings_row, state)
            for issue in list(issues or [])[:3]:
                if not isinstance(issue, dict):
                    continue
                record_audit_event(
                    session,
                    event_type="market_stream_issue",
                    entity_type="binance",
                    entity_id="market_stream",
                    severity=str(issue.get("severity") or "warning"),
                    message=str(issue.get("message") or "Binance futures market stream issue."),
                    payload={
                        "reason_code": issue.get("reason_code"),
                        "payload": issue.get("payload"),
                    },
                )
            session.commit()
        except Exception:
            session.rollback()
            raise


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)() as session:
        _raise_runtime_configuration_error(session)
    tasks: list[asyncio.Task[None]] = []
    if _background_scheduler_enabled():
        tasks.append(asyncio.create_task(_background_exchange_sync_loop()))
        tasks.append(asyncio.create_task(_background_scheduler_loop()))
    if _background_user_stream_enabled():
        tasks.append(asyncio.create_task(_background_user_stream_loop()))
    if _background_market_stream_enabled():
        tasks.append(asyncio.create_task(_background_market_stream_loop()))
    if engine.dialect.name != "sqlite":
        read_model_session_factory = sessionmaker(
            bind=engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
        warm_profitability_dashboard_cache(read_model_session_factory, synchronous=True)
        warm_ai_usage_metrics_cache(read_model_session_factory, synchronous=True)
        warm_opportunity_attribution_summary_cache(read_model_session_factory, synchronous=True)
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="Trading MVP API", version="0.2.0", lifespan=lifespan)
_app_cors_allowed_origins = _cors_allowed_origins()
_app_cors_origin_regex = _cors_origin_regex()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_app_cors_allowed_origins,
    allow_origin_regex=_app_cors_origin_regex,
    allow_credentials=bool(_app_cors_allowed_origins or _app_cors_origin_regex),
    allow_methods=OPERATOR_CORS_ALLOW_METHODS,
    allow_headers=OPERATOR_CORS_ALLOW_HEADERS,
)


def _operator_rate_limit_exceeded(request: Request) -> tuple[bool, int]:
    settings = get_settings()
    limit = (
        settings.operator_api_rate_limit_per_minute
        if request.method.upper() in OPERATOR_READ_METHODS
        else settings.operator_api_write_rate_limit_per_minute
    )
    submitted = str(request.headers.get(OPERATOR_API_KEY_HEADER) or "").strip()
    submitted_role = _operator_credential_role(submitted) if submitted else None
    client_host = request.client.host if request.client else "unknown"
    scope = "key" if submitted_role else "client"
    principal = submitted if submitted_role else client_host
    raw_bucket_key = f"{scope}:{principal}:{request.method.upper()}"
    bucket_key = hashlib.sha256(raw_bucket_key.encode("utf-8")).hexdigest()
    now = monotonic()
    cutoff = now - 60.0

    with _operator_rate_limit_guard:
        bucket = [item for item in _operator_rate_limit_buckets.get(bucket_key, []) if item >= cutoff]
        if len(bucket) >= limit:
            _operator_rate_limit_buckets[bucket_key] = bucket
            return True, 60
        bucket.append(now)
        _operator_rate_limit_buckets[bucket_key] = bucket
    return False, 0


def _operator_rate_limit_response(retry_after_seconds: int) -> JSONResponse:
    return JSONResponse(
        {"detail": "Operator API rate limit exceeded."},
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        headers={
            "Cache-Control": "no-store",
            "Retry-After": str(retry_after_seconds),
        },
    )


@app.middleware("http")
async def rate_limit_operator_api(request: Request, call_next):
    if request.url.path.startswith("/api/") and request.method.upper() != "OPTIONS" and _operator_rate_limit_required():
        exceeded, retry_after_seconds = _operator_rate_limit_exceeded(request)
        if exceeded:
            return _operator_rate_limit_response(retry_after_seconds)
    return await call_next(request)


@app.middleware("http")
async def require_operator_key_for_api_reads(request: Request, call_next):
    if (
        request.method.upper() in OPERATOR_READ_METHODS
        and request.url.path.startswith("/api/")
        and _operator_read_api_key_required()
    ):
        if not _operator_configured_credentials():
            return JSONResponse(
                {"detail": _operator_read_auth_unavailable_message()},
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                headers={"Cache-Control": "no-store"},
            )
        if not _operator_api_key_valid(request.headers.get(OPERATOR_API_KEY_HEADER)):
            if _operator_rate_limit_required():
                exceeded, retry_after_seconds = _operator_rate_limit_exceeded(request)
                if exceeded:
                    return _operator_rate_limit_response(retry_after_seconds)
            return JSONResponse(
                {"detail": "Invalid operator API key."},
                status_code=status.HTTP_401_UNAUTHORIZED,
                headers={"Cache-Control": "no-store"},
            )
    return await call_next(request)


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
                payload={
                    "error": str(exc),
                    "error_class": exc.__class__.__name__,
                    "triggered_by": triggered_by,
                    "session_recovered_before_logging": True,
                },
            )
            record_health_event(
                session,
                component="exchange_sync",
                status="error",
                message="Read-triggered exchange sync refresh failed.",
                payload={
                    "error": str(exc),
                    "error_class": exc.__class__.__name__,
                    "triggered_by": triggered_by,
                    "session_recovered_before_logging": True,
                },
            )
            session.commit()
    finally:
        with _exchange_sync_read_refresh_guard:
            _exchange_sync_read_refresh_inflight = False


def _read_trigger_refresh_enabled() -> bool:
    # Read-triggered exchange sync is optional. Scheduler cadence owns routine
    # sync, and dashboard GET paths should not enqueue settings-row writes by
    # default.
    enabled = os.getenv("TRADING_MVP_ENABLE_READ_TRIGGER_EXCHANGE_SYNC", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return False
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


def _run_binance_account_cache_refresh() -> None:
    started_at = monotonic()
    polling_session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    try:
        with polling_session_factory() as session, _sqlite_write_lock():
            mark_binance_account_refresh_started(session)
            session.commit()

        with polling_session_factory() as read_session:
            payload: BinanceAccountResponse = get_binance_account_snapshot(read_session)

        duration_ms = (monotonic() - started_at) * 1000
        with polling_session_factory() as session, _sqlite_write_lock():
            if payload.summary.connected:
                store_binance_account_cache_result(session, payload, duration_ms=duration_ms)
                record_audit_event(
                    session,
                    event_type="binance_account_cache_refreshed",
                    entity_type="binance",
                    entity_id="account",
                    message="Binance account cache refreshed.",
                    payload={
                        "duration_ms": round(duration_ms, 1),
                        "asset_count": payload.summary.asset_count,
                        "open_positions": payload.summary.open_positions,
                        "open_orders": payload.summary.open_orders,
                    },
                )
            else:
                failure_message = "Binance 원본 계정 캐시를 갱신하지 못했습니다."
                store_binance_account_cache_failure(
                    session,
                    failure_message,
                    duration_ms=duration_ms,
                )
                record_audit_event(
                    session,
                    event_type="binance_account_cache_refresh_failed",
                    entity_type="binance",
                    entity_id="account",
                    severity="warning",
                    message="Binance account cache refresh failed.",
                    payload={"duration_ms": round(duration_ms, 1), "error": failure_message},
                )
            session.commit()
    except Exception as exc:
        duration_ms = (monotonic() - started_at) * 1000
        try:
            with polling_session_factory() as session, _sqlite_write_lock():
                store_binance_account_cache_failure(session, str(exc), duration_ms=duration_ms)
                record_audit_event(
                    session,
                    event_type="binance_account_cache_refresh_failed",
                    entity_type="binance",
                    entity_id="account",
                    severity="warning",
                    message="Binance account cache refresh failed.",
                    payload={"duration_ms": round(duration_ms, 1), "error": str(exc)},
                )
                session.commit()
        except Exception:
            return
    finally:
        _binance_account_cache_refresh_guard.release()


@app.get("/health")
def health(db: Session = Depends(get_db)) -> dict[str, object]:
    config_error = _runtime_configuration_error(db)
    if config_error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "error",
                "mode": "configuration_error",
                "database": "ready",
                "reason": config_error,
            },
        )
    return {"status": "ok", "mode": "service_ready", "database": "ready"}


@app.get("/api/runtime/service-gate")
def runtime_service_gate(
    recent_minutes: int = 30,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    snapshot = build_service_switch_gate_snapshot(
        db,
        recent_minutes=_bounded_limit(recent_minutes, default=30, maximum=180),
    )
    snapshot.setdefault("root_cause_codes", [])
    return snapshot


@app.post("/api/system/seed")
def seed_system(
    _operator: None = Depends(require_operator_write_intent("system.seed")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    with _sqlite_write_lock():
        return seed_demo_data(db)


@app.get("/api/dashboard/overview")
def dashboard_overview(db: Session = Depends(get_db)) -> dict[str, object]:
    _refresh_exchange_sync_for_read(triggered_by="api_dashboard_overview")
    return get_overview(db).model_dump(mode="json")


@app.get("/api/dashboard/operator")
def dashboard_operator(view: str | None = None, db: Session = Depends(get_db)) -> dict[str, object]:
    _refresh_exchange_sync_for_read(triggered_by="api_dashboard_operator")
    if str(view or "").strip().lower() == "home":
        return _get_operator_home_dashboard_payload(db)
    return get_operator_dashboard(db, view=view).model_dump(mode="json")


@app.get("/api/dashboard/profitability")
def dashboard_profitability(db: Session = Depends(get_db)) -> dict[str, object]:
    return get_profitability_dashboard(db, use_cache=True, allow_stale=True).model_dump(mode="json")


@app.get("/api/analytics/cost-breakdown")
def analytics_cost_breakdown(
    period: str = "today",
    year: int | None = None,
    month: int | None = None,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        payload = get_analytics_cost_breakdown(db, period=period, year=year, month=month)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return payload.model_dump(mode="json")


@app.get("/api/analytics/short-drag")
def analytics_short_drag(
    days: int = 7,
    limit: int = 10,
    mode: str | None = "live",
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return build_short_side_drag_report(
        db,
        days=_bounded_limit(days, default=7, maximum=90),
        limit=_bounded_limit(limit, default=10, maximum=50),
        mode=mode,
    )


@app.get("/api/analytics/opportunity-attribution")
def analytics_opportunity_attribution(
    lookback_hours: int = 24,
    limit: int = 120,
    notional_usdt: float = 100.0,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return build_opportunity_attribution_report(
        db,
        lookback_hours=lookback_hours,
        limit=_bounded_limit(limit, default=120, maximum=500),
        notional_usdt=notional_usdt,
    )


@app.get("/api/analytics/opportunity-attribution/summary")
def analytics_opportunity_attribution_summary(
    limit: int = 120,
    notional_usdt: float = 100.0,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return build_opportunity_attribution_summary(
        db,
        limit=_bounded_limit(limit, default=120, maximum=500),
        notional_usdt=notional_usdt,
        use_cache=True,
        allow_stale=True,
    )


def _compact_opportunity_attribution_summary(window_label: str, report: dict[str, object]) -> dict[str, object]:
    return {
        "window_label": window_label,
        "generated_at": report.get("generated_at"),
        "lookback_hours": report.get("lookback_hours"),
        "since": report.get("since"),
        "virtual_notional_usdt": report.get("virtual_notional_usdt"),
        "basis": report.get("basis"),
        "overall": report.get("overall"),
        "missed_opportunity_reason_codes": list(report.get("missed_opportunity_reason_codes") or [])[:12],
        "loss_prevention_reason_codes": list(report.get("loss_prevention_reason_codes") or [])[:12],
        "pending_quality_summary": report.get("pending_quality_summary"),
        "pending_quality_threshold_review": report.get("pending_quality_threshold_review"),
        "ai_flow_summary": report.get("ai_flow_summary"),
    }


@app.get("/api/market/snapshots")
def market_snapshots(
    limit: int = 50,
    compact: bool = False,
    symbol: str | None = None,
    timeframe: str | None = None,
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return get_market_snapshots(db, limit=_bounded_limit(limit), compact=compact, symbol=symbol, timeframe=timeframe)


@app.get("/api/market/features")
def feature_snapshots(
    limit: int = 50,
    compact: bool = False,
    symbol: str | None = None,
    timeframe: str | None = None,
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return get_feature_snapshots(db, limit=_bounded_limit(limit), compact=compact, symbol=symbol, timeframe=timeframe)


@app.get("/api/market/chart-markers")
def market_chart_markers(
    symbol: str,
    limit: int = 80,
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return get_market_chart_markers(db, symbol=symbol, limit=_bounded_limit(limit, default=80, maximum=120))


@app.get("/api/decisions")
def decisions(
    limit: int = 50,
    compact: bool | None = None,
    include_payload: bool = False,
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return get_decisions(
        db,
        limit=_bounded_limit(limit),
        compact=_history_response_compact(compact, include_payload=include_payload),
    )


@app.get("/api/positions")
def positions(limit: int = 50, db: Session = Depends(get_db)) -> list[dict[str, object]]:
    return get_positions(db, limit=_bounded_limit(limit))


@app.get("/api/orders")
def orders(
    mode: str | None = None,
    symbol: str | None = None,
    status: str | None = None,
    search: str | None = None,
    position_id: int | None = None,
    compact: bool = False,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return get_orders(
        db,
        mode=mode,
        symbol=symbol,
        status=status,
        search=search,
        position_id=position_id,
        compact=compact,
        limit=_bounded_limit(limit),
    )


@app.get("/api/executions")
def executions(
    mode: str | None = None,
    symbol: str | None = None,
    status: str | None = None,
    search: str | None = None,
    position_id: int | None = None,
    compact: bool = False,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return get_executions(
        db,
        mode=mode,
        symbol=symbol,
        status=status,
        search=search,
        position_id=position_id,
        compact=compact,
        limit=_bounded_limit(limit),
    )


@app.get("/api/executions/report")
def execution_quality_report(db: Session = Depends(get_db)) -> dict[str, object]:
    return get_execution_quality_report(db)


@app.get("/api/risk/checks")
def risk_checks(
    limit: int = 50,
    compact: bool | None = None,
    include_payload: bool = False,
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return get_risk_checks(
        db,
        limit=_bounded_limit(limit),
        compact=_history_response_compact(compact, include_payload=include_payload),
    )


@app.get("/api/risk/checks/summary")
def risk_check_summaries(limit: int = 20, db: Session = Depends(get_db)) -> list[dict[str, object]]:
    return get_safety_check_summaries(db, limit=_bounded_limit(limit, default=20, maximum=20))


@app.get("/api/risk/checks/{risk_check_id}")
def risk_check_detail(risk_check_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    detail = get_safety_check_detail(db, risk_check_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Risk check not found")
    return detail


@app.get("/api/agents")
def agents(
    limit: int = 100,
    compact: bool | None = None,
    include_payload: bool = False,
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return get_agent_runs(
        db,
        limit=_bounded_limit(limit, default=100),
        compact=_history_response_compact(compact, include_payload=include_payload),
    )


@app.get("/api/scheduler")
def scheduler(
    limit: int = 50,
    compact: bool | None = None,
    include_payload: bool = False,
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return get_scheduler_runs(
        db,
        limit=_bounded_limit(limit),
        compact=_history_response_compact(compact, include_payload=include_payload),
    )


@app.get("/api/audit")
def audit(
    event_type: str | None = None,
    tab: str | None = None,
    category: str | None = None,
    severity: str | None = None,
    search: str | None = None,
    q: str | None = None,
    sort: str = "newest",
    compact: bool = False,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return get_audit_timeline(
        db,
        event_type=event_type,
        event_category=category or tab,
        severity=severity,
        search=search or q,
        sort=sort,
        compact=compact,
        limit=_bounded_limit(limit, default=100),
    )


@app.get("/api/audit/{audit_event_id}")
def audit_detail(audit_event_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    detail = get_audit_event_detail(db, audit_event_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Audit event not found")
    return detail


@app.get("/api/alerts")
def alerts(
    limit: int = 50,
    acknowledged: bool | None = None,
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return get_alerts(db, limit=_bounded_limit(limit), acknowledged=acknowledged)


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


@app.get("/api/binance/account/local")
def binance_account_local(db: Session = Depends(get_db)) -> dict[str, object]:
    payload: BinanceAccountResponse = get_local_binance_account_snapshot(db)
    return payload.model_dump(mode="json")


@app.get("/api/binance/account/cache")
def binance_account_cache(db: Session = Depends(get_db)) -> dict[str, object]:
    return get_cached_binance_account_snapshot(db)


@app.post("/api/binance/account/refresh")
def binance_account_refresh(
    _operator: None = Depends(require_operator_write_intent("binance.account_refresh")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    acquired = _binance_account_cache_refresh_guard.acquire(blocking=False)
    try:
        with _sqlite_write_lock():
            payload = mark_binance_account_refresh_requested(db, already_running=not acquired)
            db.commit()
    except Exception:
        if acquired:
            _binance_account_cache_refresh_guard.release()
        raise

    if acquired:
        try:
            threading.Thread(
                target=_run_binance_account_cache_refresh,
                daemon=True,
                name="binance-account-cache-refresh",
            ).start()
        except Exception:
            _binance_account_cache_refresh_guard.release()
            raise
    return payload


@app.put("/api/settings")
def settings_update(
    payload: AppSettingsUpdateRequest,
    _operator: None = Depends(require_operator_write_intent("settings.update")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _require_operator_key_for_rollout(payload.rollout_mode)
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
    _operator: None = Depends(require_operator_write_intent("settings.operator_event_view")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    with _sqlite_write_lock():
        row, _changed = upsert_operator_event_view(db, payload)
        db.commit()
        return serialize_settings_view(row)


@app.post("/api/settings/operator-event-view/clear")
def operator_event_view_clear(
    payload: OperatorEventViewClearRequest | None = None,
    _operator: None = Depends(require_operator_write_intent("settings.operator_event_view_clear")),
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
    _operator: None = Depends(require_operator_write_intent("settings.manual_no_trade_window")),
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
    _operator: None = Depends(require_operator_write_intent("settings.manual_no_trade_window")),
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
    _operator: None = Depends(require_operator_write_intent("settings.manual_no_trade_window")),
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
def pause_trading(
    _operator: None = Depends(require_operator_write_intent("settings.pause")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
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
def resume_trading(
    _operator: None = Depends(require_operator_write_intent("settings.resume")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
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


@app.post("/api/settings/resume/attempt")
def attempt_resume_trading(
    _operator: None = Depends(require_operator_write_intent("settings.resume_attempt")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    with _sqlite_write_lock():
        row = get_or_create_settings(db)
        result = attempt_auto_resume(db, row, trigger_source="operator_ui")
        db.commit()
        payload = serialize_settings_view(row)
        payload["auto_resume_attempt_result"] = result
        return payload


@app.post("/api/settings/live/arm")
def arm_live(
    payload: ManualLiveApprovalRequest | None = None,
    _operator: None = Depends(require_operator_write_intent("settings.live_arm")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    with _sqlite_write_lock():
        current_row = get_or_create_settings(db)
        sync_blockers = live_arm_blocking_reason_codes(current_row)
        if sync_blockers:
            detail = {
                "reason_code": "FULL_LIVE_SYNC_STALE",
                "blocked_reason_codes": sync_blockers,
                "message": "full_live 실거래 승인 전 거래소 동기화가 최신이어야 합니다.",
            }
            record_audit_event(
                db,
                event_type="live_approval_arm_denied",
                entity_type="settings",
                entity_id=str(current_row.id),
                severity="warning",
                message="Manual live execution window arm denied.",
                payload=detail,
            )
            db.commit()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
        requested_minutes = payload.minutes if payload is not None else None
        effective_minutes = resolve_live_approval_window_minutes(current_row, requested_minutes)
        try:
            row = arm_live_execution(db, requested_minutes)
        except LiveApprovalWindowError as exc:
            detail = {
                "reason_code": str(exc),
                "requested_minutes": requested_minutes,
                "effective_minutes": effective_minutes,
                "configured_window_minutes": current_row.live_approval_window_minutes,
                "message": "실거래 승인 창 시간이 1분 이상이어야 합니다.",
            }
            record_audit_event(
                db,
                event_type="live_approval_arm_denied",
                entity_type="settings",
                entity_id=str(current_row.id),
                severity="warning",
                message="Manual live execution window arm denied.",
                payload=detail,
            )
            db.commit()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from exc
        record_audit_event(
            db,
            event_type="live_approval_armed",
            entity_type="settings",
            entity_id=str(row.id),
            severity="warning",
            message="Manual live execution window armed.",
            payload={
                "armed_until": row.live_execution_armed_until.isoformat() if row.live_execution_armed_until else None,
                "requested_minutes": requested_minutes,
                "effective_minutes": effective_minutes,
                "configured_window_minutes": row.live_approval_window_minutes,
            },
        )
        db.commit()
        return serialize_settings_view(row)


@app.post("/api/settings/live/disarm")
def disarm_live(
    _operator: None = Depends(require_operator_write_intent("settings.live_disarm")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
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
    _operator: None = Depends(require_operator_write_intent("settings.integration_test")),
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
    _operator: None = Depends(require_operator_write_intent("settings.integration_test")),
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
    _operator: None = Depends(require_operator_write_intent("settings.live_test_order")),
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
def run_cycle(
    _operator: None = Depends(require_operator_write_intent("cycle.run")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    with _sqlite_write_lock():
        output = TradingOrchestrator(db).run_selected_symbols_cycle(trigger_event="manual")
        db.commit()
        return output


@app.post("/api/reviews/{window}")
def run_review(
    window: str,
    _operator: None = Depends(require_operator_write_intent("review.run")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    with _sqlite_write_lock():
        if window != "1h":
            raise HTTPException(status_code=400, detail="Only 1h review window is enabled in current live-core scope.")
        output = run_window(db, window, triggered_by="manual")
        db.commit()
        return output


@app.post("/api/replay/run")
def run_replay(
    cycles: int = 5,
    start_index: int = 120,
    _operator: None = Depends(require_operator_write_intent("replay.run")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
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
    _operator: None = Depends(require_operator_write_intent("replay.validation")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    report = build_replay_validation_report(db, payload)
    return report.model_dump(mode="json")


@app.post("/api/live/sync")
def live_sync(
    symbol: str | None = None,
    allow_protection_recovery: bool = True,
    _operator: None = Depends(require_operator_write_intent("live.sync")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    with _sqlite_write_lock():
        settings_row = get_or_create_settings(db)
        auto_resume_precheck: dict[str, object] | None = None
        auto_resume_postcheck: dict[str, object] | None = None

        if allow_protection_recovery and settings_row.trading_paused:
            auto_resume_precheck = attempt_auto_resume(
                db,
                settings_row,
                trigger_source="api_live_sync_precheck",
            )
            db.flush()
            settings_row = get_or_create_settings(db)
        try:
            if allow_protection_recovery:
                result = sync_live_state(db, settings_row, symbol=symbol)
            else:
                result = sync_live_state(
                    db,
                    settings_row,
                    symbol=symbol,
                    allow_protection_recovery=False,
                )
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
        if allow_protection_recovery and (auto_resume_precheck is not None or settings_row.trading_paused):
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
            "allow_protection_recovery": allow_protection_recovery,
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
