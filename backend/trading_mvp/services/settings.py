from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from trading_mvp.config import Settings as AppConfig
from trading_mvp.config import get_settings
from trading_mvp.models import Setting
from trading_mvp.schemas import AppSettingsResponse, AppSettingsUpdateRequest
from trading_mvp.services.ai_usage import (
    AIUsageMetrics,
    build_ai_usage_metrics,
    get_openai_call_gate,
    manual_ai_guard_minutes,
)
from trading_mvp.services.secret_store import decrypt_secret, encrypt_secret
from trading_mvp.time_utils import utcnow_naive


@dataclass(slots=True)
class RuntimeCredentials:
    openai_api_key: str
    binance_api_key: str
    binance_api_secret: str


MINUTES_PER_30_DAY_MONTH = 30 * 24 * 60
AUTO_RESUME_REASON_CODES = {"EXCHANGE_ACCOUNT_STATE_UNAVAILABLE"}


def _default_windows(defaults: AppConfig) -> list[str]:
    return [item.strip() for item in defaults.schedule_windows.split(",") if item.strip()]


def _default_symbols(defaults: AppConfig) -> list[str]:
    values = [item.strip().upper() for item in defaults.tracked_symbols.split(",") if item.strip()]
    if not values:
        values = [defaults.default_symbol.upper()]
    return list(dict.fromkeys(values))


def normalize_symbols(symbols: list[str]) -> list[str]:
    cleaned = [item.strip().upper() for item in symbols if item and item.strip()]
    return list(dict.fromkeys(cleaned))


def get_effective_symbols(settings_row: Setting) -> list[str]:
    symbols = normalize_symbols(settings_row.tracked_symbols)
    if settings_row.default_symbol.upper() not in symbols:
        symbols.insert(0, settings_row.default_symbol.upper())
    return symbols


def get_or_create_settings(session: Session) -> Setting:
    row = session.scalar(select(Setting).limit(1))
    if row is not None:
        return row

    defaults = get_settings()
    tracked_symbols = _default_symbols(defaults)
    row = Setting(
        live_trading_enabled=defaults.live_trading_enabled,
        manual_live_approval=defaults.manual_live_approval,
        live_execution_armed=False,
        live_execution_armed_until=None,
        live_approval_window_minutes=15,
        trading_paused=defaults.trading_paused,
        default_symbol=tracked_symbols[0],
        tracked_symbols=tracked_symbols,
        default_timeframe=defaults.default_timeframe,
        schedule_windows=_default_windows(defaults),
        max_leverage=defaults.max_leverage,
        max_risk_per_trade=defaults.max_risk_per_trade,
        max_daily_loss=defaults.max_daily_loss,
        max_consecutive_losses=defaults.max_consecutive_losses,
        stale_market_seconds=defaults.stale_market_seconds,
        slippage_threshold_pct=defaults.slippage_threshold_pct,
        starting_equity=defaults.starting_equity,
        ai_enabled=defaults.ai_enabled,
        ai_provider=defaults.ai_provider,
        ai_model=defaults.openai_model,
        ai_call_interval_minutes=defaults.ai_call_interval_minutes,
        decision_cycle_interval_minutes=defaults.decision_cycle_interval_minutes,
        ai_max_input_candles=defaults.ai_max_input_candles,
        ai_temperature=defaults.ai_temperature,
        openai_api_key_encrypted=encrypt_secret(defaults.openai_api_key, defaults.app_secret_seed),
        binance_market_data_enabled=defaults.binance_market_data_enabled,
        binance_testnet_enabled=defaults.binance_testnet_enabled,
        binance_futures_enabled=defaults.binance_futures_enabled,
    )
    session.add(row)
    session.flush()
    return row


def get_runtime_credentials(settings_row: Setting, defaults: AppConfig | None = None) -> RuntimeCredentials:
    app_defaults = defaults or get_settings()
    openai_key = decrypt_secret(settings_row.openai_api_key_encrypted, app_defaults.app_secret_seed)
    if not openai_key:
        openai_key = app_defaults.openai_api_key
    return RuntimeCredentials(
        openai_api_key=openai_key,
        binance_api_key=decrypt_secret(settings_row.binance_api_key_encrypted, app_defaults.app_secret_seed),
        binance_api_secret=decrypt_secret(settings_row.binance_api_secret_encrypted, app_defaults.app_secret_seed),
    )


def is_live_execution_armed(settings_row: Setting) -> bool:
    return bool(
        settings_row.live_execution_armed
        and settings_row.live_execution_armed_until is not None
        and settings_row.live_execution_armed_until > utcnow_naive()
    )


def is_live_execution_ready(settings_row: Setting, defaults: AppConfig | None = None) -> bool:
    app_defaults = defaults or get_settings()
    credentials = get_runtime_credentials(settings_row, defaults=app_defaults)
    return bool(
        app_defaults.live_trading_env_enabled
        and settings_row.live_trading_enabled
        and settings_row.manual_live_approval
        and is_live_execution_armed(settings_row)
        and credentials.binance_api_key
        and credentials.binance_api_secret
    )


def pause_reason_allows_auto_resume(reason_code: str | None) -> bool:
    return bool(reason_code and reason_code in AUTO_RESUME_REASON_CODES)


def arm_live_execution(session: Session, minutes: int | None = None) -> Setting:
    row = get_or_create_settings(session)
    row.live_execution_armed = True
    row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=minutes or row.live_approval_window_minutes)
    session.add(row)
    session.flush()
    return row


def disarm_live_execution(session: Session) -> Setting:
    row = get_or_create_settings(session)
    row.live_execution_armed = False
    row.live_execution_armed_until = None
    session.add(row)
    session.flush()
    return row


def estimate_monthly_ai_calls(settings_row: Setting, *, assume_enabled: bool = False) -> tuple[int, dict[str, int]]:
    breakdown = {
        "trading_decision": 0,
        "integration_planner": 0,
        "ui_ux": 0,
        "product_improvement": 0,
    }
    if settings_row.ai_provider != "openai" or not (assume_enabled or settings_row.ai_enabled):
        return 0, breakdown

    symbol_count = max(len(get_effective_symbols(settings_row)), 1)
    trading_interval = max(int(settings_row.decision_cycle_interval_minutes), int(settings_row.ai_call_interval_minutes))
    breakdown["trading_decision"] = (MINUTES_PER_30_DAY_MONTH // max(trading_interval, 1)) * symbol_count
    if "4h" in settings_row.schedule_windows:
        breakdown["integration_planner"] = 30 * 6
    if "12h" in settings_row.schedule_windows:
        breakdown["ui_ux"] = 30 * 2
    if "24h" in settings_row.schedule_windows:
        breakdown["product_improvement"] = 30
    return sum(breakdown.values()), breakdown


def serialize_settings(settings_row: Setting) -> dict[str, object]:
    defaults = get_settings()
    credentials = get_runtime_credentials(settings_row, defaults=defaults)
    estimated_total, estimated_breakdown = estimate_monthly_ai_calls(settings_row)
    projected_total, projected_breakdown = estimate_monthly_ai_calls(settings_row, assume_enabled=True)
    current_session = object_session(settings_row)
    usage_metrics: AIUsageMetrics = build_ai_usage_metrics(current_session) if current_session is not None else {
        "recent_ai_calls_24h": 0,
        "recent_ai_calls_7d": 0,
        "recent_ai_successes_24h": 0,
        "recent_ai_successes_7d": 0,
        "recent_ai_failures_24h": 0,
        "recent_ai_failures_7d": 0,
        "recent_ai_tokens_24h": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "recent_ai_tokens_7d": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "recent_ai_role_calls_24h": {},
        "recent_ai_role_calls_7d": {},
        "recent_ai_role_failures_24h": {},
        "recent_ai_role_failures_7d": {},
        "recent_ai_failure_reasons": [],
        "observed_monthly_ai_calls_projection": 0,
        "observed_monthly_ai_calls_projection_breakdown": {},
    }

    mode = "live_guarded"
    if settings_row.trading_paused:
        mode = "paused"
    elif is_live_execution_ready(settings_row, defaults=defaults):
        mode = "live_ready"

    payload = AppSettingsResponse(
        id=settings_row.id,
        live_trading_enabled=settings_row.live_trading_enabled,
        live_trading_env_enabled=defaults.live_trading_env_enabled,
        manual_live_approval=settings_row.manual_live_approval,
        live_execution_armed=is_live_execution_armed(settings_row),
        live_execution_armed_until=settings_row.live_execution_armed_until,
        live_approval_window_minutes=settings_row.live_approval_window_minutes,
        live_execution_ready=is_live_execution_ready(settings_row, defaults=defaults),
        trading_paused=settings_row.trading_paused,
        pause_reason_code=settings_row.pause_reason_code,
        pause_origin=settings_row.pause_origin,
        pause_reason_detail=settings_row.pause_reason_detail,
        pause_triggered_at=settings_row.pause_triggered_at,
        auto_resume_after=settings_row.auto_resume_after,
        auto_resume_whitelisted=pause_reason_allows_auto_resume(settings_row.pause_reason_code),
        default_symbol=settings_row.default_symbol.upper(),
        tracked_symbols=get_effective_symbols(settings_row),
        default_timeframe=settings_row.default_timeframe,
        schedule_windows=settings_row.schedule_windows,
        max_leverage=settings_row.max_leverage,
        max_risk_per_trade=settings_row.max_risk_per_trade,
        max_daily_loss=settings_row.max_daily_loss,
        max_consecutive_losses=settings_row.max_consecutive_losses,
        stale_market_seconds=settings_row.stale_market_seconds,
        slippage_threshold_pct=settings_row.slippage_threshold_pct,
        starting_equity=settings_row.starting_equity,
        ai_enabled=settings_row.ai_enabled,
        ai_provider=settings_row.ai_provider,
        ai_model=settings_row.ai_model,
        ai_call_interval_minutes=settings_row.ai_call_interval_minutes,
        decision_cycle_interval_minutes=settings_row.decision_cycle_interval_minutes,
        ai_max_input_candles=settings_row.ai_max_input_candles,
        ai_temperature=settings_row.ai_temperature,
        binance_market_data_enabled=settings_row.binance_market_data_enabled,
        binance_testnet_enabled=settings_row.binance_testnet_enabled,
        binance_futures_enabled=settings_row.binance_futures_enabled,
        mode=mode,
        openai_api_key_configured=bool(credentials.openai_api_key),
        binance_api_key_configured=bool(credentials.binance_api_key),
        binance_api_secret_configured=bool(credentials.binance_api_secret),
        estimated_monthly_ai_calls=estimated_total,
        estimated_monthly_ai_calls_breakdown=estimated_breakdown,
        projected_monthly_ai_calls_if_enabled=projected_total,
        projected_monthly_ai_calls_breakdown_if_enabled=projected_breakdown,
        recent_ai_calls_24h=usage_metrics["recent_ai_calls_24h"],
        recent_ai_calls_7d=usage_metrics["recent_ai_calls_7d"],
        recent_ai_successes_24h=usage_metrics["recent_ai_successes_24h"],
        recent_ai_successes_7d=usage_metrics["recent_ai_successes_7d"],
        recent_ai_failures_24h=usage_metrics["recent_ai_failures_24h"],
        recent_ai_failures_7d=usage_metrics["recent_ai_failures_7d"],
        recent_ai_tokens_24h={
            "prompt_tokens": usage_metrics["recent_ai_tokens_24h"]["prompt_tokens"],
            "completion_tokens": usage_metrics["recent_ai_tokens_24h"]["completion_tokens"],
            "total_tokens": usage_metrics["recent_ai_tokens_24h"]["total_tokens"],
        },
        recent_ai_tokens_7d={
            "prompt_tokens": usage_metrics["recent_ai_tokens_7d"]["prompt_tokens"],
            "completion_tokens": usage_metrics["recent_ai_tokens_7d"]["completion_tokens"],
            "total_tokens": usage_metrics["recent_ai_tokens_7d"]["total_tokens"],
        },
        recent_ai_role_calls_24h=usage_metrics["recent_ai_role_calls_24h"],
        recent_ai_role_calls_7d=usage_metrics["recent_ai_role_calls_7d"],
        recent_ai_role_failures_24h=usage_metrics["recent_ai_role_failures_24h"],
        recent_ai_role_failures_7d=usage_metrics["recent_ai_role_failures_7d"],
        recent_ai_failure_reasons=usage_metrics["recent_ai_failure_reasons"],
        observed_monthly_ai_calls_projection=usage_metrics["observed_monthly_ai_calls_projection"],
        observed_monthly_ai_calls_projection_breakdown=usage_metrics[
            "observed_monthly_ai_calls_projection_breakdown"
        ],
        manual_ai_guard_minutes=manual_ai_guard_minutes(settings_row),
    )
    return payload.model_dump(mode="json")


def update_settings(session: Session, payload: AppSettingsUpdateRequest) -> Setting:
    defaults = get_settings()
    row = get_or_create_settings(session)
    tracked_symbols = normalize_symbols(payload.tracked_symbols)
    if payload.default_symbol.upper() not in tracked_symbols:
        tracked_symbols.insert(0, payload.default_symbol.upper())

    row.live_trading_enabled = payload.live_trading_enabled
    row.manual_live_approval = payload.manual_live_approval
    row.live_approval_window_minutes = payload.live_approval_window_minutes
    row.default_symbol = payload.default_symbol.upper()
    row.tracked_symbols = tracked_symbols
    row.default_timeframe = payload.default_timeframe
    row.schedule_windows = payload.schedule_windows
    row.max_leverage = payload.max_leverage
    row.max_risk_per_trade = payload.max_risk_per_trade
    row.max_daily_loss = payload.max_daily_loss
    row.max_consecutive_losses = payload.max_consecutive_losses
    row.stale_market_seconds = payload.stale_market_seconds
    row.slippage_threshold_pct = payload.slippage_threshold_pct
    row.starting_equity = payload.starting_equity
    row.ai_enabled = payload.ai_enabled
    row.ai_provider = payload.ai_provider
    row.ai_model = payload.ai_model
    row.ai_call_interval_minutes = payload.ai_call_interval_minutes
    row.decision_cycle_interval_minutes = payload.decision_cycle_interval_minutes
    row.ai_max_input_candles = payload.ai_max_input_candles
    row.ai_temperature = payload.ai_temperature
    row.binance_market_data_enabled = payload.binance_market_data_enabled
    row.binance_testnet_enabled = payload.binance_testnet_enabled
    row.binance_futures_enabled = payload.binance_futures_enabled

    if payload.clear_openai_api_key:
        row.openai_api_key_encrypted = ""
    elif payload.openai_api_key:
        row.openai_api_key_encrypted = encrypt_secret(payload.openai_api_key, defaults.app_secret_seed)

    if payload.clear_binance_api_key:
        row.binance_api_key_encrypted = ""
    elif payload.binance_api_key:
        row.binance_api_key_encrypted = encrypt_secret(payload.binance_api_key, defaults.app_secret_seed)

    if payload.clear_binance_api_secret:
        row.binance_api_secret_encrypted = ""
    elif payload.binance_api_secret:
        row.binance_api_secret_encrypted = encrypt_secret(payload.binance_api_secret, defaults.app_secret_seed)

    if not row.live_trading_enabled or not row.manual_live_approval:
        row.live_execution_armed = False
        row.live_execution_armed_until = None

    session.add(row)
    session.flush()
    return row


def set_trading_pause(
    session: Session,
    paused: bool,
    *,
    reason_code: str | None = None,
    reason_detail: dict[str, object] | None = None,
    pause_origin: str | None = None,
    auto_resume_after: datetime | None = None,
    preserve_live_arm: bool = False,
) -> Setting:
    row = get_or_create_settings(session)
    row.trading_paused = paused
    if paused:
        row.pause_reason_code = reason_code
        row.pause_origin = pause_origin
        row.pause_reason_detail = reason_detail or {}
        row.pause_triggered_at = utcnow_naive()
        row.auto_resume_after = (
            auto_resume_after if pause_reason_allows_auto_resume(reason_code) else None
        )
        if not preserve_live_arm:
            row.live_execution_armed = False
            row.live_execution_armed_until = None
    else:
        row.pause_reason_code = None
        row.pause_origin = None
        row.pause_reason_detail = {}
        row.pause_triggered_at = None
        row.auto_resume_after = None
    session.add(row)
    session.flush()
    return row


def should_call_openai(session: Session, settings_row: Setting, role: str, trigger_event: str) -> bool:
    credentials = get_runtime_credentials(settings_row)
    gate = get_openai_call_gate(
        session,
        settings_row,
        role,
        trigger_event,
        has_openai_key=bool(credentials.openai_api_key),
    )
    return gate.allowed
