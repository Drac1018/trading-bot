from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, object_session

from trading_mvp.config import Settings as AppConfig
from trading_mvp.config import get_settings
from trading_mvp.models import AgentRun, FeatureSnapshot, PnLSnapshot, Position, RiskCheck, Setting, SystemHealthEvent
from trading_mvp.schemas import AppSettingsResponse, AppSettingsUpdateRequest
from trading_mvp.services.account import get_latest_pnl_snapshot
from trading_mvp.services.adaptive_signal import (
    build_adaptive_signal_context,
    summarize_adaptive_signal_state,
)
from trading_mvp.services.ai_usage import (
    AIUsageMetrics,
    build_ai_usage_metrics,
    get_openai_call_gate,
    manual_ai_guard_minutes,
)
from trading_mvp.services.execution_policy import summarize_execution_policy
from trading_mvp.services.pause_policy import (
    get_pause_reason_policy,
    pause_reason_allows_auto_resume,
    pause_reason_recovery_class,
    pause_reason_severity,
)
from trading_mvp.services.runtime_state import (
    DEGRADED_MANAGE_ONLY_STATE,
    EMERGENCY_EXIT_STATE,
    PROTECTION_REQUIRED_STATE,
    build_sync_freshness_summary,
    summarize_runtime_state,
)
from trading_mvp.services.secret_store import decrypt_secret, encrypt_secret
from trading_mvp.time_utils import utcnow_naive


@dataclass(slots=True)
class RuntimeCredentials:
    openai_api_key: str
    binance_api_key: str
    binance_api_secret: str


MINUTES_PER_30_DAY_MONTH = 30 * 24 * 60
DISPLAY_MAX_LEVERAGE = 5.0
DISPLAY_MAX_RISK_PER_TRADE = 0.02
DISPLAY_MAX_DAILY_LOSS = 0.05
DISPLAY_MAX_GROSS_EXPOSURE_PCT = 3.0
DISPLAY_MAX_LARGEST_POSITION_PCT = 1.5
DISPLAY_MAX_DIRECTIONAL_BIAS_PCT = 2.0
DISPLAY_MAX_SAME_TIER_CONCENTRATION_PCT = 2.5
AUTO_RESUME_GRACE_MAX_MINUTES = 15
RUNTIME_STATE_DETAIL_KEYS = {"operating_state", "protection_recovery", "exchange_sync"}
"""
ACCOUNT_SYNC_WARNING_REASON_CODES = {
    "EXCHANGE_ACCOUNT_STATE_UNAVAILABLE",
    "TEMPORARY_SYNC_FAILURE",
    "EXCHANGE_POSITION_SYNC_FAILED",
    "EXCHANGE_OPEN_ORDERS_SYNC_FAILED",
    "EXCHANGE_CONNECTIVITY_TEMPORARY_FAILURE",
    "ACCOUNT_STATE_STALE": "거래소 계좌 상태 동기화가 오래되어 신규 진입을 차단했습니다.",
    "POSITION_STATE_STALE": "거래소 포지션 상태 동기화가 오래되어 신규 진입을 차단했습니다.",
    "OPEN_ORDERS_STATE_STALE": "거래소 오더 상태 동기화가 오래되어 신규 진입을 차단했습니다.",
    "PROTECTION_STATE_UNVERIFIED": "보호주문 상태를 확인할 수 없어 신규 진입을 차단했습니다.",
}

"""
ACCOUNT_SYNC_WARNING_REASON_CODES = {
    "EXCHANGE_ACCOUNT_STATE_UNAVAILABLE",
    "TEMPORARY_SYNC_FAILURE",
    "EXCHANGE_POSITION_SYNC_FAILED",
    "EXCHANGE_OPEN_ORDERS_SYNC_FAILED",
    "EXCHANGE_CONNECTIVITY_TEMPORARY_FAILURE",
}
GUARD_MODE_REASON_MESSAGES: dict[str, str] = {
    "TRADING_PAUSED": "거래가 일시 중지되어 가드 모드입니다.",
    "MANUAL_USER_REQUEST": "운영자가 수동으로 거래를 중지해 가드 모드입니다.",
    "EXCHANGE_ACCOUNT_STATE_UNAVAILABLE": "거래소 계좌 상태 동기화 실패로 시스템 pause 상태입니다.",
    "EXCHANGE_CONNECTIVITY_TEMPORARY_FAILURE": "거래소 연결 장애가 감지되어 가드 모드입니다.",
    "TEMPORARY_SYNC_FAILURE": "계좌 상태 동기화가 일시 실패해 가드 모드입니다.",
    "EXCHANGE_POSITION_SYNC_FAILED": "거래소 포지션 동기화 실패로 가드 모드입니다.",
    "EXCHANGE_OPEN_ORDERS_SYNC_FAILED": "거래소 주문 동기화 실패로 가드 모드입니다.",
    "PROTECTION_REQUIRED": "무보호 포지션이 감지되어 보호 복구 우선 상태입니다.",
    "DEGRADED_MANAGE_ONLY": "보호 복구가 반복 실패해 관리 전용 상태로 가드 모드입니다.",
    "EMERGENCY_EXIT": "비상 청산 상태가 진행 중이라 가드 모드입니다.",
    "LIVE_ENV_DISABLED": "실거래 환경이 비활성화되어 가드 모드입니다.",
    "LIVE_TRADING_DISABLED": "앱에서 실거래 사용이 꺼져 있어 가드 모드입니다.",
    "LIVE_APPROVAL_POLICY_DISABLED": "앱의 실거래 승인 정책이 비활성화되어 가드 모드입니다.",
    "LIVE_APPROVAL_REQUIRED": "실거래 승인 창이 닫혀 있어 가드 모드입니다.",
    "LIVE_CREDENTIALS_MISSING": "Binance API 키 또는 시크릿이 없어 가드 모드입니다.",
    "MISSING_PROTECTIVE_ORDERS": "무보호 포지션이 남아 있어 가드 모드입니다.",
    "PROTECTIVE_ORDER_FAILURE": "보호 주문 복구 실패로 가드 모드입니다.",
    "ACCOUNT_STATE_INCONSISTENT": "거래소와 로컬 계좌 상태가 불일치해 가드 모드입니다.",
    "PORTFOLIO_RISK_UNCERTAIN": "포트폴리오 위험을 신뢰할 수 없어 가드 모드입니다.",
    "STALE_MARKET_DATA": "시장 데이터가 오래되어 신규 진입이 차단된 상태입니다.",
    "INCOMPLETE_MARKET_DATA": "시장 데이터가 불완전해 신규 진입이 차단된 상태입니다.",
    "ACCOUNT_STATE_STALE": "거래소 계좌 상태 동기화가 오래되어 신규 진입을 차단했습니다.",
    "POSITION_STATE_STALE": "거래소 포지션 상태 동기화가 오래되어 신규 진입을 차단했습니다.",
    "OPEN_ORDERS_STATE_STALE": "거래소 오더 상태 동기화가 오래되어 신규 진입을 차단했습니다.",
    "PROTECTION_STATE_UNVERIFIED": "보호주문 상태를 확인할 수 없어 신규 진입을 차단했습니다.",
}


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
        live_approval_window_minutes=0,
        trading_paused=defaults.trading_paused,
        default_symbol=tracked_symbols[0],
        tracked_symbols=tracked_symbols,
        default_timeframe=defaults.default_timeframe,
        schedule_windows=_default_windows(defaults),
        max_leverage=defaults.max_leverage,
        max_risk_per_trade=defaults.max_risk_per_trade,
        max_daily_loss=defaults.max_daily_loss,
        max_consecutive_losses=defaults.max_consecutive_losses,
        max_gross_exposure_pct=defaults.max_gross_exposure_pct,
        max_largest_position_pct=defaults.max_largest_position_pct,
        max_directional_bias_pct=defaults.max_directional_bias_pct,
        max_same_tier_concentration_pct=defaults.max_same_tier_concentration_pct,
        stale_market_seconds=defaults.stale_market_seconds,
        slippage_threshold_pct=defaults.slippage_threshold_pct,
        adaptive_signal_enabled=defaults.adaptive_signal_enabled,
        position_management_enabled=defaults.position_management_enabled,
        break_even_enabled=defaults.break_even_enabled,
        atr_trailing_stop_enabled=defaults.atr_trailing_stop_enabled,
        partial_take_profit_enabled=defaults.partial_take_profit_enabled,
        holding_edge_decay_enabled=defaults.holding_edge_decay_enabled,
        reduce_on_regime_shift_enabled=defaults.reduce_on_regime_shift_enabled,
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


def _account_sync_stale_seconds(settings_row: Setting) -> int:
    return max(300, settings_row.decision_cycle_interval_minutes * 120)


def _build_pnl_summary(settings_row: Setting, latest_pnl: PnLSnapshot) -> dict[str, object]:
    return {
        "basis": "execution_ledger_truth",
        "basis_note": (
            "Net realized, daily, cumulative PnL and consecutive losses are derived from live executions first."
        ),
        "equity": latest_pnl.equity,
        "cash_balance": latest_pnl.cash_balance,
        "net_realized_pnl": latest_pnl.realized_pnl,
        "unrealized_pnl": latest_pnl.unrealized_pnl,
        "daily_pnl": latest_pnl.daily_pnl,
        "cumulative_pnl": latest_pnl.cumulative_pnl,
        "consecutive_losses": latest_pnl.consecutive_losses,
        "snapshot_time": latest_pnl.created_at,
    }


def _build_account_sync_summary(
    session: Session | None,
    settings_row: Setting,
    latest_pnl: PnLSnapshot,
) -> dict[str, object]:
    freshness_seconds = max(int((utcnow_naive() - latest_pnl.created_at).total_seconds()), 0)
    stale_after_seconds = _account_sync_stale_seconds(settings_row)
    latest_warning: SystemHealthEvent | None = None
    if session is not None:
        events = list(
            session.scalars(
                select(SystemHealthEvent)
                .where(SystemHealthEvent.component.in_(["live_execution", "live_sync"]))
                .order_by(SystemHealthEvent.created_at.desc())
                .limit(20)
            )
        )
        latest_warning = next(
            (
                event
                for event in events
                if str(event.payload.get("reason_code", "")) in ACCOUNT_SYNC_WARNING_REASON_CODES
            ),
            None,
        )

    status = "exchange_synced"
    note = "Cash/equity is currently aligned with the latest exchange account snapshot."
    reconciliation_mode = "exchange_confirmed"
    if freshness_seconds > stale_after_seconds:
        status = "stale"
        reconciliation_mode = "stale_snapshot"
        note = "The latest account snapshot is stale. Freshness should be confirmed before relying on cash/equity."
    elif latest_warning is not None and latest_warning.created_at >= latest_pnl.created_at:
        status = "fallback_reconciled"
        reconciliation_mode = "deterministic_delta_fallback"
        note = (
            "Recent account sync degraded. Cash/equity may be temporarily reconciled from the prior snapshot "
            "plus deterministic realized PnL delta until the next successful exchange sync."
        )

    return {
        "status": status,
        "reconciliation_mode": reconciliation_mode,
        "freshness_seconds": freshness_seconds,
        "stale_after_seconds": stale_after_seconds,
        "last_synced_at": latest_pnl.created_at,
        "last_warning_reason_code": (
            str(latest_warning.payload.get("reason_code"))
            if latest_warning is not None and latest_warning.payload.get("reason_code") not in {None, ""}
            else None
        ),
        "last_warning_message": latest_warning.message if latest_warning is not None else None,
        "note": note,
    }


def _build_market_context_summary(session: Session | None, settings_row: Setting) -> dict[str, object]:
    if session is None:
        return {
            "symbol": settings_row.default_symbol.upper(),
            "base_timeframe": settings_row.default_timeframe,
            "context_timeframes": [],
            "primary_regime": "unknown",
            "trend_alignment": "unknown",
            "volatility_regime": "unknown",
            "volume_regime": "unknown",
            "momentum_state": "unknown",
            "data_quality_flags": [],
        }

    latest_feature = session.scalar(
        select(FeatureSnapshot).order_by(FeatureSnapshot.feature_time.desc()).limit(1)
    )
    if latest_feature is None:
        return {
            "symbol": settings_row.default_symbol.upper(),
            "base_timeframe": settings_row.default_timeframe,
            "context_timeframes": [],
            "primary_regime": "unknown",
            "trend_alignment": "unknown",
            "volatility_regime": "unknown",
            "volume_regime": "unknown",
            "momentum_state": "unknown",
            "data_quality_flags": [],
        }

    payload = dict(latest_feature.payload or {})
    regime = dict(payload.get("regime", {})) if isinstance(payload.get("regime"), dict) else {}
    multi_timeframe = (
        dict(payload.get("multi_timeframe", {}))
        if isinstance(payload.get("multi_timeframe"), dict)
        else {}
    )
    return {
        "symbol": latest_feature.symbol,
        "base_timeframe": latest_feature.timeframe,
        "context_timeframes": sorted(str(item) for item in multi_timeframe),
        "primary_regime": str(regime.get("primary_regime", "unknown")),
        "trend_alignment": str(regime.get("trend_alignment", "unknown")),
        "volatility_regime": str(regime.get("volatility_regime", "unknown")),
        "volume_regime": str(regime.get("volume_regime", "unknown")),
        "momentum_state": str(regime.get("momentum_state", "unknown")),
        "data_quality_flags": [str(item) for item in payload.get("data_quality_flags", []) if item],
    }


def _build_adaptive_protection_summary(
    runtime_state: dict[str, object],
    market_context_summary: dict[str, object],
) -> dict[str, object]:
    missing_symbols = [
        str(item)
        for item in cast(list[object], runtime_state.get("missing_protection_symbols", []))
    ]
    raw_missing_items = runtime_state.get("missing_protection_items", {})
    missing_items = cast(dict[object, object], raw_missing_items) if isinstance(raw_missing_items, dict) else {}
    return {
        "mode": "adaptive_atr_regime_aware",
        "status": str(runtime_state["protection_recovery_status"]),
        "active": bool(runtime_state["protection_recovery_active"]),
        "failure_count": _coerce_int(runtime_state.get("protection_recovery_failure_count")),
        "missing_symbols": missing_symbols,
        "missing_items": {
            str(key): [str(item) for item in cast(list[object], value)]
            for key, value in missing_items.items()
            if isinstance(value, list)
        },
        "primary_regime": str(market_context_summary.get("primary_regime", "unknown")),
        "volatility_regime": str(market_context_summary.get("volatility_regime", "unknown")),
        "summary": (
            "Protective brackets remain ATR-based but adapt to regime, volatility, volume, and momentum weakening."
        ),
    }


def _build_position_management_summary(
    session: Session | None,
    settings_row: Setting,
) -> dict[str, object]:
    active_positions = 0
    managed_positions = 0
    partial_take_profit_taken = 0
    if session is not None:
        positions = list(
            session.scalars(
                select(Position).where(
                    Position.mode == "live",
                    Position.status == "open",
                    Position.quantity > 0,
                )
            )
        )
        active_positions = len(positions)
        for position in positions:
            metadata = position.metadata_json if isinstance(position.metadata_json, dict) else {}
            management = metadata.get("position_management")
            if not isinstance(management, dict):
                continue
            managed_positions += 1
            if bool(management.get("partial_take_profit_taken")):
                partial_take_profit_taken += 1

    return {
        "mode": "conservative_dynamic_profit_protection",
        "enabled": settings_row.position_management_enabled,
        "protective_bias": "tighten_only",
        "rules_enabled": {
            "break_even": settings_row.position_management_enabled and settings_row.break_even_enabled,
            "atr_trailing_stop": settings_row.position_management_enabled and settings_row.atr_trailing_stop_enabled,
            "partial_take_profit": settings_row.position_management_enabled
            and settings_row.partial_take_profit_enabled,
            "holding_edge_decay": settings_row.position_management_enabled
            and settings_row.holding_edge_decay_enabled,
            "reduce_on_regime_shift": settings_row.position_management_enabled
            and settings_row.reduce_on_regime_shift_enabled,
        },
        "fixed_parameters": {
            "break_even_trigger_r": 1.0,
            "trailing_activation_r": 1.0,
            "trailing_atr_multiple": 1.2,
            "partial_take_profit_trigger_r": 1.5,
            "partial_take_profit_fraction": 0.25,
            "edge_decay_start_ratio": 0.75,
        },
        "active_positions": active_positions,
        "managed_positions_with_baseline": managed_positions,
        "partial_take_profit_taken_positions": partial_take_profit_taken,
        "data_fallback_rule": (
            "If initial stop or holding-plan metadata is missing, the layer keeps the current stop and skips "
            "time-decay or partial-take-profit automation."
        ),
        "summary": (
            "Break-even, ATR trailing, partial take-profit, holding-time decay, and regime weakening only tighten "
            "protection or reduce exposure. They never widen stop loss."
        ),
    }


def get_latest_blocked_reasons(session: Session | None) -> list[str]:
    if session is None:
        return []
    latest_risk = session.scalar(select(RiskCheck).order_by(desc(RiskCheck.created_at)).limit(1))
    if latest_risk is None or latest_risk.allowed:
        return []
    return [str(item) for item in latest_risk.reason_codes if item not in {None, ""}]


def get_exposure_limits(settings_row: Setting) -> dict[str, float]:
    return {
        "gross_exposure_pct": min(settings_row.max_gross_exposure_pct, DISPLAY_MAX_GROSS_EXPOSURE_PCT),
        "largest_position_pct": min(settings_row.max_largest_position_pct, DISPLAY_MAX_LARGEST_POSITION_PCT),
        "directional_bias_pct": min(settings_row.max_directional_bias_pct, DISPLAY_MAX_DIRECTIONAL_BIAS_PCT),
        "same_tier_concentration_pct": min(
            settings_row.max_same_tier_concentration_pct,
            DISPLAY_MAX_SAME_TIER_CONCENTRATION_PCT,
        ),
    }


def _coerce_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _coerce_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def is_live_execution_armed(settings_row: Setting) -> bool:
    return bool(
        settings_row.live_execution_armed
        and (
            settings_row.live_execution_armed_until is None
            or settings_row.live_execution_armed_until > utcnow_naive()
        )
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


def _humanize_guard_code(code: str) -> str:
    return code.replace("_", " ").strip().title()


def _guard_message_for_code(code: str, fallback_suffix: str = "가드 모드입니다.") -> str:
    return GUARD_MODE_REASON_MESSAGES.get(code, f"{_humanize_guard_code(code)} 상태로 {fallback_suffix}")


def _derive_live_execution_guard_reason(
    settings_row: Setting,
    *,
    defaults: AppConfig | None = None,
) -> tuple[str | None, str | None, str | None]:
    app_defaults = defaults or get_settings()
    credentials = get_runtime_credentials(settings_row, defaults=app_defaults)
    if not app_defaults.live_trading_env_enabled:
        code = "LIVE_ENV_DISABLED"
    elif not settings_row.live_trading_enabled:
        code = "LIVE_TRADING_DISABLED"
    elif not credentials.binance_api_key or not credentials.binance_api_secret:
        code = "LIVE_CREDENTIALS_MISSING"
    elif not settings_row.manual_live_approval:
        code = "LIVE_APPROVAL_POLICY_DISABLED"
    elif not is_live_execution_armed(settings_row):
        code = "LIVE_APPROVAL_REQUIRED"
    else:
        return None, None, None
    return "readiness", code, _guard_message_for_code(code)


def derive_guard_mode_reason(
    settings_row: Setting,
    *,
    defaults: AppConfig | None = None,
    runtime_state: dict[str, object] | None = None,
    latest_blocked_reasons: list[str] | None = None,
    auto_resume_last_blockers: list[str] | None = None,
) -> dict[str, str | None]:
    app_defaults = defaults or get_settings()
    runtime = runtime_state or summarize_runtime_state(settings_row)
    blocked_reasons = [str(item) for item in (latest_blocked_reasons or []) if item]
    auto_resume_blockers = [str(item) for item in (auto_resume_last_blockers or []) if item]
    operating_state = str(runtime.get("operating_state", "TRADABLE"))

    if settings_row.trading_paused:
        code = str(settings_row.pause_reason_code or "TRADING_PAUSED")
        return {
            "guard_mode_reason_category": "pause",
            "guard_mode_reason_code": code,
            "guard_mode_reason_message": _guard_message_for_code(code),
        }
    if operating_state == EMERGENCY_EXIT_STATE:
        return {
            "guard_mode_reason_category": "operating_state",
            "guard_mode_reason_code": EMERGENCY_EXIT_STATE,
            "guard_mode_reason_message": _guard_message_for_code(EMERGENCY_EXIT_STATE),
        }
    if operating_state == DEGRADED_MANAGE_ONLY_STATE:
        return {
            "guard_mode_reason_category": "operating_state",
            "guard_mode_reason_code": DEGRADED_MANAGE_ONLY_STATE,
            "guard_mode_reason_message": _guard_message_for_code(DEGRADED_MANAGE_ONLY_STATE),
        }
    if operating_state == PROTECTION_REQUIRED_STATE:
        return {
            "guard_mode_reason_category": "operating_state",
            "guard_mode_reason_code": PROTECTION_REQUIRED_STATE,
            "guard_mode_reason_message": _guard_message_for_code(PROTECTION_REQUIRED_STATE),
        }
    if not is_live_execution_ready(settings_row, defaults=app_defaults):
        category, code, message = _derive_live_execution_guard_reason(settings_row, defaults=app_defaults)
        return {
            "guard_mode_reason_category": category,
            "guard_mode_reason_code": code,
            "guard_mode_reason_message": message,
        }
    if blocked_reasons:
        code = blocked_reasons[0]
        return {
            "guard_mode_reason_category": "risk_block",
            "guard_mode_reason_code": code,
            "guard_mode_reason_message": _guard_message_for_code(code),
        }
    if auto_resume_blockers:
        code = auto_resume_blockers[0]
        return {
            "guard_mode_reason_category": "auto_resume",
            "guard_mode_reason_code": code,
            "guard_mode_reason_message": _guard_message_for_code(code, fallback_suffix="자동 복구가 차단되어 가드 모드입니다."),
        }
    return {
        "guard_mode_reason_category": None,
        "guard_mode_reason_code": None,
        "guard_mode_reason_message": None,
    }


def arm_live_execution(session: Session, minutes: int | None = None) -> Setting:
    row = get_or_create_settings(session)
    row.live_execution_armed = True
    row.live_execution_armed_until = None
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

    effective_max_leverage = min(settings_row.max_leverage, DISPLAY_MAX_LEVERAGE)
    effective_max_risk_per_trade = min(settings_row.max_risk_per_trade, DISPLAY_MAX_RISK_PER_TRADE)
    effective_max_daily_loss = min(settings_row.max_daily_loss, DISPLAY_MAX_DAILY_LOSS)
    exposure_limits = get_exposure_limits(settings_row)
    pause_policy = get_pause_reason_policy(settings_row.pause_reason_code)
    auto_resume_state = settings_row.pause_reason_detail.get("auto_resume", {}) if settings_row.trading_paused else {}
    runtime_state = summarize_runtime_state(settings_row)
    latest_blocked_reasons = get_latest_blocked_reasons(current_session)
    auto_resume_last_blockers = [str(item) for item in auto_resume_state.get("blockers", [])]
    guard_mode_reason = derive_guard_mode_reason(
        settings_row,
        defaults=defaults,
        runtime_state=runtime_state,
        latest_blocked_reasons=latest_blocked_reasons,
        auto_resume_last_blockers=auto_resume_last_blockers,
    )
    latest_pnl = get_latest_pnl_snapshot(current_session, settings_row) if current_session is not None else None
    pnl_summary = (
        _build_pnl_summary(settings_row, latest_pnl)
        if latest_pnl is not None
        else {
            "basis": "execution_ledger_truth",
            "basis_note": (
                "Net realized, daily, cumulative PnL and consecutive losses are derived from live executions first."
            ),
            "equity": settings_row.starting_equity,
            "cash_balance": settings_row.starting_equity,
            "net_realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "daily_pnl": 0.0,
            "cumulative_pnl": 0.0,
            "consecutive_losses": 0,
            "snapshot_time": None,
        }
    )
    account_sync_summary = (
        _build_account_sync_summary(current_session, settings_row, latest_pnl)
        if latest_pnl is not None
        else {
            "status": "unknown",
            "reconciliation_mode": "unknown",
            "freshness_seconds": None,
            "stale_after_seconds": _account_sync_stale_seconds(settings_row),
            "last_synced_at": None,
            "last_warning_reason_code": None,
            "last_warning_message": None,
            "note": "Account sync status is not available until the first live snapshot is created.",
        }
    )
    sync_freshness_summary = build_sync_freshness_summary(settings_row)
    if current_session is not None:
        from trading_mvp.services.risk import build_current_exposure_summary

        exposure_summary = build_current_exposure_summary(
            current_session,
            settings_row,
            equity=_coerce_float(pnl_summary.get("equity"), settings_row.starting_equity),
            reference_symbol=settings_row.default_symbol,
        )
    else:
        exposure_summary = {
            "reference_symbol": settings_row.default_symbol.upper(),
            "reference_tier": "unknown",
            "metrics": {},
            "limits": exposure_limits,
            "headroom": {},
            "status": "unknown",
        }
    market_context_summary = _build_market_context_summary(current_session, settings_row)
    adaptive_protection_summary = _build_adaptive_protection_summary(
        runtime_state,
        market_context_summary,
    )
    adaptive_signal_context = build_adaptive_signal_context(
        current_session,
        enabled=settings_row.adaptive_signal_enabled,
        symbol=settings_row.default_symbol.upper(),
        timeframe=settings_row.default_timeframe,
        regime=str(market_context_summary.get("primary_regime", "unknown")),
    )
    latest_decision_rationale_codes: list[str] = []
    latest_decision_code: str | None = None
    if current_session is not None:
        latest_trading_run = current_session.scalar(
            select(AgentRun)
            .where(AgentRun.role == "trading_decision")
            .order_by(desc(AgentRun.created_at))
            .limit(1)
        )
        if latest_trading_run is not None:
            payload = latest_trading_run.output_payload or {}
            latest_decision_code = str(payload.get("decision") or "") or None
            raw_codes = payload.get("rationale_codes", [])
            if isinstance(raw_codes, list):
                latest_decision_rationale_codes = [str(item) for item in raw_codes if item not in {None, ""}]
    adaptive_signal_summary = summarize_adaptive_signal_state(
        adaptive_signal_context,
        latest_rationale_codes=latest_decision_rationale_codes,
        latest_decision=latest_decision_code,
    )
    execution_policy_summary = summarize_execution_policy(settings_row)
    position_management_summary = _build_position_management_summary(current_session, settings_row)

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
        guard_mode_reason_category=guard_mode_reason["guard_mode_reason_category"],
        guard_mode_reason_code=guard_mode_reason["guard_mode_reason_code"],
        guard_mode_reason_message=guard_mode_reason["guard_mode_reason_message"],
        pause_reason_code=settings_row.pause_reason_code,
        pause_origin=settings_row.pause_origin,
        pause_reason_detail=settings_row.pause_reason_detail,
        pause_triggered_at=settings_row.pause_triggered_at,
        auto_resume_after=settings_row.auto_resume_after,
        auto_resume_whitelisted=pause_reason_allows_auto_resume(settings_row.pause_reason_code),
        auto_resume_eligible=pause_policy.auto_resume_eligible if settings_row.trading_paused else False,
        auto_resume_status=str(auto_resume_state.get("status", "not_paused" if not settings_row.trading_paused else "idle")),
        auto_resume_last_blockers=auto_resume_last_blockers,
        latest_blocked_reasons=latest_blocked_reasons,
        pause_severity=pause_reason_severity(settings_row.pause_reason_code) if settings_row.trading_paused else None,
        pause_recovery_class=pause_reason_recovery_class(settings_row.pause_reason_code) if settings_row.trading_paused else None,
        operating_state=str(runtime_state["operating_state"]),
        protection_recovery_status=str(runtime_state["protection_recovery_status"]),
        protection_recovery_active=bool(runtime_state["protection_recovery_active"]),
        protection_recovery_failure_count=int(runtime_state["protection_recovery_failure_count"]),
        missing_protection_symbols=[str(item) for item in runtime_state["missing_protection_symbols"]],
        missing_protection_items={
            str(key): [str(item) for item in value]
            for key, value in runtime_state["missing_protection_items"].items()
        },
        pnl_summary=pnl_summary,
        account_sync_summary=account_sync_summary,
        sync_freshness_summary=sync_freshness_summary,
        exposure_summary=exposure_summary,
        execution_policy_summary=execution_policy_summary,
        market_context_summary=market_context_summary,
        adaptive_protection_summary=adaptive_protection_summary,
        adaptive_signal_summary=adaptive_signal_summary,
        position_management_summary=position_management_summary,
        default_symbol=settings_row.default_symbol.upper(),
        tracked_symbols=get_effective_symbols(settings_row),
        default_timeframe=settings_row.default_timeframe,
        schedule_windows=settings_row.schedule_windows,
        max_leverage=effective_max_leverage,
        max_risk_per_trade=effective_max_risk_per_trade,
        max_daily_loss=effective_max_daily_loss,
        max_consecutive_losses=settings_row.max_consecutive_losses,
        max_gross_exposure_pct=exposure_limits["gross_exposure_pct"],
        max_largest_position_pct=exposure_limits["largest_position_pct"],
        max_directional_bias_pct=exposure_limits["directional_bias_pct"],
        max_same_tier_concentration_pct=exposure_limits["same_tier_concentration_pct"],
        stale_market_seconds=settings_row.stale_market_seconds,
        slippage_threshold_pct=settings_row.slippage_threshold_pct,
        adaptive_signal_enabled=settings_row.adaptive_signal_enabled,
        position_management_enabled=settings_row.position_management_enabled,
        break_even_enabled=settings_row.break_even_enabled,
        atr_trailing_stop_enabled=settings_row.atr_trailing_stop_enabled,
        partial_take_profit_enabled=settings_row.partial_take_profit_enabled,
        holding_edge_decay_enabled=settings_row.holding_edge_decay_enabled,
        reduce_on_regime_shift_enabled=settings_row.reduce_on_regime_shift_enabled,
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
    row.max_gross_exposure_pct = payload.max_gross_exposure_pct
    row.max_largest_position_pct = payload.max_largest_position_pct
    row.max_directional_bias_pct = payload.max_directional_bias_pct
    row.max_same_tier_concentration_pct = payload.max_same_tier_concentration_pct
    row.stale_market_seconds = payload.stale_market_seconds
    row.slippage_threshold_pct = payload.slippage_threshold_pct
    row.adaptive_signal_enabled = payload.adaptive_signal_enabled
    row.position_management_enabled = payload.position_management_enabled
    row.break_even_enabled = payload.break_even_enabled
    row.atr_trailing_stop_enabled = payload.atr_trailing_stop_enabled
    row.partial_take_profit_enabled = payload.partial_take_profit_enabled
    row.holding_edge_decay_enabled = payload.holding_edge_decay_enabled
    row.reduce_on_regime_shift_enabled = payload.reduce_on_regime_shift_enabled
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
    existing_detail = dict(row.pause_reason_detail or {})
    preserved_runtime_detail = {
        key: existing_detail[key]
        for key in RUNTIME_STATE_DETAIL_KEYS
        if key in existing_detail
    }
    if paused:
        now = utcnow_naive()
        policy = get_pause_reason_policy(reason_code)
        current_detail = {**preserved_runtime_detail, **dict(reason_detail or {})}
        live_armed_before_pause = is_live_execution_armed(row)
        live_ready_before_pause = is_live_execution_ready(row)
        live_armed_until_before_pause = row.live_execution_armed_until
        latest_pnl = get_latest_pnl_snapshot(session, row)
        grace_until: datetime | None = None
        if (
            pause_origin == "system"
            and policy.auto_resume_eligible
            and live_ready_before_pause
        ):
            grace_minutes = max(5, min(row.live_approval_window_minutes, AUTO_RESUME_GRACE_MAX_MINUTES))
            grace_until = now + timedelta(minutes=grace_minutes)
            if live_armed_until_before_pause is not None and live_armed_until_before_pause > grace_until:
                grace_until = live_armed_until_before_pause
        current_detail["resume_context"] = {
            "live_execution_armed_before_pause": live_armed_before_pause,
            "live_execution_ready_before_pause": live_ready_before_pause,
            "live_execution_armed_until_before_pause": (
                live_armed_until_before_pause.isoformat() if live_armed_until_before_pause else None
            ),
            "approval_grace_until": grace_until.isoformat() if grace_until else None,
            "pause_severity": policy.severity,
            "pause_recovery_class": policy.recovery_class,
            "equity_before_pause": latest_pnl.equity,
            "daily_pnl_before_pause": latest_pnl.daily_pnl,
            "consecutive_losses_before_pause": latest_pnl.consecutive_losses,
        }
        current_detail["auto_resume"] = {
            "status": "idle" if policy.auto_resume_eligible else "not_eligible",
            "blockers": [],
            "last_checked_at": None,
        }
        row.pause_reason_code = reason_code
        row.pause_origin = pause_origin
        row.pause_reason_detail = current_detail
        row.pause_triggered_at = now
        row.auto_resume_after = (
            auto_resume_after if pause_reason_allows_auto_resume(reason_code) else None
        )
        if not preserve_live_arm:
            row.live_execution_armed = False
            row.live_execution_armed_until = None
    else:
        row.pause_reason_code = None
        row.pause_origin = None
        row.pause_reason_detail = preserved_runtime_detail
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
