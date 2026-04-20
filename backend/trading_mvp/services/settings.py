from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, object_session

from trading_mvp.config import Settings as AppConfig
from trading_mvp.config import get_settings
from trading_mvp.models import (
    AgentRun,
    FeatureSnapshot,
    MarketSnapshot,
    PnLSnapshot,
    Position,
    RiskCheck,
    SchedulerRun,
    Setting,
    SystemHealthEvent,
)
from trading_mvp.schemas import (
    AIEventViewPayload,
    AppSettingsAIUsageResponse,
    AppSettingsCadenceResponse,
    AppSettingsResponse,
    AppSettingsUpdateRequest,
    AppSettingsViewResponse,
    ControlStatusSummary,
    EventOperatorControlPayload,
    ManualNoTradeWindowPayload,
    ManualNoTradeWindowRequest,
    OperatorEventContextPayload,
    OperatorEventViewPayload,
    OperatorEventViewRequest,
    OperationalStatusPayload,
    RolloutMode,
    SymbolCadenceOverride,
    SymbolEffectiveCadence,
)
from trading_mvp.services.account import get_latest_pnl_snapshot, is_placeholder_live_snapshot
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
from trading_mvp.services.drawdown_state import build_drawdown_state_snapshot
from trading_mvp.services.execution_policy import summarize_execution_policy
from trading_mvp.services.event_context import normalize_operator_event_context
from trading_mvp.services.event_policy import (
    build_default_operator_event_view,
    derive_ai_event_view,
    evaluate_event_policy,
    no_trade_window_is_active,
)
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
    get_drawdown_state_detail,
    get_sync_state_detail,
    summarize_runtime_state,
)
from trading_mvp.services.secret_store import decrypt_secret, encrypt_secret
from trading_mvp.services.audit import record_audit_event
from trading_mvp.time_utils import ensure_utc_aware, isoformat_utc, parse_utc_datetime, utcnow_aware, utcnow_naive


@dataclass(slots=True)
class RuntimeCredentials:
    openai_api_key: str
    binance_api_key: str
    binance_api_secret: str


@dataclass(slots=True)
class EffectiveSymbolSettings:
    symbol: str
    enabled: bool
    uses_global_defaults: bool
    timeframe: str
    market_refresh_interval_minutes: int
    position_management_interval_seconds: int
    decision_cycle_interval_minutes: int
    ai_call_interval_minutes: int
    ai_backstop_enabled: bool
    ai_backstop_interval_minutes: int


DISPLAY_MAX_LEVERAGE = 5.0
DISPLAY_MAX_RISK_PER_TRADE = 0.02
DISPLAY_MAX_DAILY_LOSS = 0.05
DISPLAY_MAX_GROSS_EXPOSURE_PCT = 3.0
DISPLAY_MAX_LARGEST_POSITION_PCT = 1.5
DISPLAY_MAX_DIRECTIONAL_BIAS_PCT = 2.0
DISPLAY_MAX_SAME_TIER_CONCENTRATION_PCT = 2.5
AUTO_RESUME_GRACE_MAX_MINUTES = 15
DEFAULT_LIMITED_LIVE_MAX_NOTIONAL = 500.0
DEFAULT_AI_BACKSTOP_ENABLED = True
DEFAULT_AI_BACKSTOP_INTERVAL_MINUTES = 180
ROLLOUT_MODE_SUBMIT_ENABLED = {"limited_live", "full_live"}
ROLLOUT_MODE_LIVE_PATH = {"shadow", "live_dry_run", "limited_live", "full_live"}
RUNTIME_STATE_DETAIL_KEYS = {
    "operating_state",
    "protection_recovery",
    "exchange_sync",
    "user_stream",
    "reconciliation",
    "candidate_selection",
}
EVENT_OPERATOR_CONTROL_DETAIL_KEY = "event_operator_control"
PRESERVED_SETTINGS_DETAIL_KEYS = {*RUNTIME_STATE_DETAIL_KEYS, EVENT_OPERATOR_CONTROL_DETAIL_KEY}
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
SYNC_SCOPE_GUARD_REASON_CODES = {
    "account": "ACCOUNT_STATE_STALE",
    "positions": "POSITION_STATE_STALE",
    "open_orders": "OPEN_ORDERS_STATE_STALE",
    "protective_orders": "PROTECTION_STATE_UNVERIFIED",
}
STALE_FIRST_REASON_PRIORITY = {
    "USER_STREAM_LISTEN_KEY_ROTATION_PENDING": -1,
    "ACCOUNT_STATE_STALE": 0,
    "POSITION_STATE_STALE": 1,
    "OPEN_ORDERS_STATE_STALE": 2,
    "PROTECTION_STATE_UNVERIFIED": 3,
    "EXCHANGE_ACCOUNT_STATE_UNAVAILABLE": 4,
    "EXCHANGE_POSITION_SYNC_FAILED": 5,
    "EXCHANGE_OPEN_ORDERS_SYNC_FAILED": 6,
    "TEMPORARY_SYNC_FAILURE": 7,
    "EXCHANGE_CONNECTIVITY_TEMPORARY_FAILURE": 8,
    "LIVE_CREDENTIALS_MISSING": 9,
    "STALE_MARKET_DATA": 10,
    "MARKET_STATE_STALE": 10,
    "INCOMPLETE_MARKET_DATA": 11,
    "MARKET_STATE_INCOMPLETE": 11,
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
    "ROLLOUT_MODE_SHADOW": "shadow rollout 모드라 실제 거래소 submit은 금지됩니다.",
    "ROLLOUT_MODE_LIVE_DRY_RUN": "live dry-run rollout 모드라 실제 거래소 submit은 금지됩니다.",
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
    "USER_STREAM_LISTEN_KEY_ROTATION_PENDING": "listen key 재등록이 완료되지 않아 신규 진입을 차단하고 REST fallback으로 운용 중입니다.",
    "EXCHANGE_POSITION_MODE_UNCLEAR": "거래소 포지션 모드를 확인하지 못해 신규 진입을 차단합니다.",
    "EXCHANGE_POSITION_MODE_MISMATCH": "거래소 Hedge mode가 현재 one-way 로컬 해석과 충돌해 신규 진입을 차단합니다.",
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


def normalize_rollout_mode(value: object) -> RolloutMode:
    raw = str(value or "paper").strip().lower()
    if raw in {"shadow", "live_dry_run", "limited_live", "full_live"}:
        return cast(RolloutMode, raw)
    return "paper"


def get_rollout_mode(settings_row: Setting) -> RolloutMode:
    rollout_mode = normalize_rollout_mode(getattr(settings_row, "rollout_mode", "paper"))
    if settings_row.live_trading_enabled and rollout_mode == "paper":
        return "full_live"
    if not settings_row.live_trading_enabled and rollout_mode != "paper":
        return "paper"
    return rollout_mode


def rollout_mode_uses_live_path(settings_row: Setting) -> bool:
    return get_rollout_mode(settings_row) in ROLLOUT_MODE_LIVE_PATH


def rollout_mode_allows_exchange_submit(settings_row: Setting) -> bool:
    return get_rollout_mode(settings_row) in ROLLOUT_MODE_SUBMIT_ENABLED


def get_limited_live_max_notional(settings_row: Setting) -> float:
    limit = float(getattr(settings_row, "limited_live_max_notional", DEFAULT_LIMITED_LIVE_MAX_NOTIONAL) or 0.0)
    if limit > 0:
        return limit
    return DEFAULT_LIMITED_LIVE_MAX_NOTIONAL


def normalize_symbol_cadence_overrides(
    overrides: list[dict[str, Any]] | list[SymbolCadenceOverride] | None,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in overrides or []:
        raw = item.model_dump(mode="json") if isinstance(item, SymbolCadenceOverride) else dict(item or {})
        symbol = str(raw.get("symbol", "")).strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        normalized.append(
            {
                "symbol": symbol,
                "enabled": bool(raw.get("enabled", True)),
                "timeframe_override": str(raw["timeframe_override"]).strip()
                if raw.get("timeframe_override")
                else None,
                "market_refresh_interval_minutes_override": (
                    int(raw["market_refresh_interval_minutes_override"])
                    if raw.get("market_refresh_interval_minutes_override") not in {None, ""}
                    else None
                ),
                "position_management_interval_seconds_override": (
                    int(raw["position_management_interval_seconds_override"])
                    if raw.get("position_management_interval_seconds_override") not in {None, ""}
                    else None
                ),
                "decision_cycle_interval_minutes_override": (
                    int(raw["decision_cycle_interval_minutes_override"])
                    if raw.get("decision_cycle_interval_minutes_override") not in {None, ""}
                    else None
                ),
                "ai_call_interval_minutes_override": (
                    int(raw["ai_call_interval_minutes_override"])
                    if raw.get("ai_call_interval_minutes_override") not in {None, ""}
                    else None
                ),
                "ai_backstop_enabled_override": (
                    bool(raw.get("ai_backstop_enabled_override"))
                    if raw.get("ai_backstop_enabled_override") is not None
                    else None
                ),
                "ai_backstop_interval_minutes_override": (
                    int(raw["ai_backstop_interval_minutes_override"])
                    if raw.get("ai_backstop_interval_minutes_override") not in {None, ""}
                    else None
                ),
            }
        )
    return normalized


def get_symbol_cadence_overrides(settings_row: Setting) -> list[dict[str, Any]]:
    raw = settings_row.symbol_cadence_overrides
    if not isinstance(raw, list):
        return []
    return normalize_symbol_cadence_overrides(raw)


def get_effective_symbols(settings_row: Setting) -> list[str]:
    symbols = normalize_symbols(settings_row.tracked_symbols)
    if settings_row.default_symbol.upper() not in symbols:
        symbols.insert(0, settings_row.default_symbol.upper())
    return symbols


def get_effective_symbol_settings(settings_row: Setting, symbol: str) -> EffectiveSymbolSettings:
    symbol = symbol.upper()
    raw_override = next(
        (item for item in get_symbol_cadence_overrides(settings_row) if item.get("symbol") == symbol),
        None,
    )
    override = raw_override or {}
    uses_global_defaults = True
    if raw_override is not None:
        uses_global_defaults = not any(
            override.get(key) not in {None, ""}
            for key in (
                "timeframe_override",
                "market_refresh_interval_minutes_override",
                "position_management_interval_seconds_override",
                "decision_cycle_interval_minutes_override",
                "ai_call_interval_minutes_override",
                "ai_backstop_enabled_override",
                "ai_backstop_interval_minutes_override",
            )
        )
    return EffectiveSymbolSettings(
        symbol=symbol,
        enabled=bool(override.get("enabled", True)),
        uses_global_defaults=uses_global_defaults,
        timeframe=str(override.get("timeframe_override") or settings_row.default_timeframe),
        market_refresh_interval_minutes=int(
            override.get("market_refresh_interval_minutes_override")
            or settings_row.market_refresh_interval_minutes
        ),
        position_management_interval_seconds=int(
            override.get("position_management_interval_seconds_override")
            or settings_row.position_management_interval_seconds
        ),
        decision_cycle_interval_minutes=int(
            override.get("decision_cycle_interval_minutes_override")
            or settings_row.decision_cycle_interval_minutes
        ),
        ai_call_interval_minutes=int(
            override.get("ai_call_interval_minutes_override")
            or settings_row.ai_call_interval_minutes
        ),
        ai_backstop_enabled=(
            bool(override.get("ai_backstop_enabled_override"))
            if override.get("ai_backstop_enabled_override") is not None
            else DEFAULT_AI_BACKSTOP_ENABLED
        ),
        ai_backstop_interval_minutes=int(
            override.get("ai_backstop_interval_minutes_override")
            or DEFAULT_AI_BACKSTOP_INTERVAL_MINUTES
        ),
    )


def get_effective_symbol_schedule(settings_row: Setting) -> list[EffectiveSymbolSettings]:
    return [get_effective_symbol_settings(settings_row, symbol) for symbol in get_effective_symbols(settings_row)]


def _event_operator_control_detail(settings_row: Setting) -> dict[str, Any]:
    detail = dict(settings_row.pause_reason_detail or {})
    payload = detail.get(EVENT_OPERATOR_CONTROL_DETAIL_KEY)
    return dict(payload) if isinstance(payload, dict) else {}


def _write_event_operator_control_detail(settings_row: Setting, payload: dict[str, Any]) -> None:
    detail = dict(settings_row.pause_reason_detail or {})
    detail[EVENT_OPERATOR_CONTROL_DETAIL_KEY] = payload
    settings_row.pause_reason_detail = detail


def _serialize_operator_event_view_payload(view: OperatorEventViewPayload | None) -> dict[str, Any] | None:
    if view is None:
        return None
    return view.model_dump(mode="json")


def _deserialize_operator_event_view_payload(settings_row: Setting) -> OperatorEventViewPayload | None:
    raw = _event_operator_control_detail(settings_row).get("operator_event_view")
    if not isinstance(raw, dict):
        return None
    try:
        return OperatorEventViewPayload.model_validate(raw)
    except Exception:
        return None


def _deserialize_manual_no_trade_windows(settings_row: Setting) -> list[ManualNoTradeWindowPayload]:
    raw = _event_operator_control_detail(settings_row).get("manual_no_trade_windows")
    if not isinstance(raw, list):
        return []
    windows: list[ManualNoTradeWindowPayload] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            windows.append(ManualNoTradeWindowPayload.model_validate(item))
        except Exception:
            continue
    windows.sort(
        key=lambda window: ensure_utc_aware(window.start_at) or utcnow_aware(),
        reverse=True,
    )
    return windows


def _serialize_manual_no_trade_windows(
    windows: list[ManualNoTradeWindowPayload],
) -> list[dict[str, Any]]:
    return [window.model_dump(mode="json") for window in windows]


def _operator_event_view_logical_payload(view: OperatorEventViewPayload | None) -> dict[str, Any] | None:
    if view is None:
        return None
    return {
        "operator_bias": view.operator_bias,
        "operator_risk_state": view.operator_risk_state,
        "applies_to_symbols": list(view.applies_to_symbols),
        "horizon": view.horizon,
        "valid_from": isoformat_utc(view.valid_from),
        "valid_to": isoformat_utc(view.valid_to),
        "enforcement_mode": view.enforcement_mode,
        "note": view.note,
    }


def _manual_window_logical_payload(window: ManualNoTradeWindowPayload) -> dict[str, Any]:
    return {
        "scope": window.scope.model_dump(mode="json"),
        "start_at": isoformat_utc(window.start_at),
        "end_at": isoformat_utc(window.end_at),
        "reason": window.reason,
        "auto_resume": window.auto_resume,
        "require_manual_rearm": window.require_manual_rearm,
    }


def _latest_symbol_decision(
    session: Session | None,
    *,
    symbol: str,
    timeframe: str | None = None,
) -> AgentRun | None:
    if session is None:
        return None
    symbol_key = symbol.upper()
    for row in session.scalars(
        select(AgentRun)
        .where(AgentRun.role == "trading_decision")
        .order_by(desc(AgentRun.created_at))
    ):
        output = row.output_payload if isinstance(row.output_payload, dict) else {}
        input_payload = row.input_payload if isinstance(row.input_payload, dict) else {}
        output_symbol = str(output.get("symbol") or "").upper()
        output_timeframe = str(output.get("timeframe") or "")
        input_market = input_payload.get("market_snapshot")
        input_market_timeframe = (
            str(input_market.get("timeframe") or "")
            if isinstance(input_market, dict)
            else ""
        )
        if output_symbol != symbol_key:
            continue
        if timeframe and output_timeframe not in {"", timeframe} and input_market_timeframe not in {"", timeframe}:
            continue
        return row
    return None


def _latest_symbol_feature(
    session: Session | None,
    *,
    symbol: str,
    timeframe: str | None = None,
) -> FeatureSnapshot | None:
    if session is None:
        return None
    query = select(FeatureSnapshot).where(FeatureSnapshot.symbol == symbol.upper())
    if timeframe:
        query = query.where(FeatureSnapshot.timeframe == timeframe)
    return session.scalar(query.order_by(desc(FeatureSnapshot.feature_time)).limit(1))


def _latest_symbol_market_snapshot(
    session: Session | None,
    *,
    symbol: str,
    timeframe: str | None = None,
) -> MarketSnapshot | None:
    if session is None:
        return None
    query = select(MarketSnapshot).where(MarketSnapshot.symbol == symbol.upper())
    if timeframe:
        query = query.where(MarketSnapshot.timeframe == timeframe)
    return session.scalar(query.order_by(desc(MarketSnapshot.snapshot_time)).limit(1))


def _extract_raw_event_context(
    *,
    decision_row: AgentRun | None,
    feature_row: FeatureSnapshot | None,
    market_row: MarketSnapshot | None,
) -> tuple[dict[str, Any], str | None]:
    if decision_row is not None and isinstance(decision_row.input_payload, dict):
        features = decision_row.input_payload.get("features")
        if isinstance(features, dict) and isinstance(features.get("event_context"), dict):
            return dict(features.get("event_context") or {}), "latest decision input"
        market_snapshot = decision_row.input_payload.get("market_snapshot")
        if isinstance(market_snapshot, dict) and isinstance(market_snapshot.get("event_context"), dict):
            return dict(market_snapshot.get("event_context") or {}), "decision market snapshot"
        ai_context = decision_row.input_payload.get("ai_context")
        if isinstance(ai_context, dict) and isinstance(ai_context.get("event_context_summary"), dict):
            return dict(ai_context.get("event_context_summary") or {}), "ai context summary"
        feature_layers = decision_row.input_payload.get("feature_layers")
        if isinstance(feature_layers, dict) and isinstance(feature_layers.get("event_context_summary"), dict):
            return dict(feature_layers.get("event_context_summary") or {}), "feature layer summary"
    if feature_row is not None and isinstance(feature_row.payload, dict) and isinstance(feature_row.payload.get("event_context"), dict):
        return dict(feature_row.payload.get("event_context") or {}), "feature snapshot"
    if market_row is not None and isinstance(market_row.payload, dict) and isinstance(market_row.payload.get("event_context"), dict):
        return dict(market_row.payload.get("event_context") or {}), "market snapshot"
    return {}, None


def _build_operator_event_context_payload(
    *,
    session: Session | None,
    settings_row: Setting,
    symbol: str,
    timeframe: str | None = None,
    decision_row: AgentRun | None = None,
    feature_row: FeatureSnapshot | None = None,
    market_row: MarketSnapshot | None = None,
) -> OperatorEventContextPayload:
    resolved_decision = decision_row or _latest_symbol_decision(session, symbol=symbol, timeframe=timeframe)
    resolved_feature = feature_row or _latest_symbol_feature(session, symbol=symbol, timeframe=timeframe)
    resolved_market = market_row or _latest_symbol_market_snapshot(session, symbol=symbol, timeframe=timeframe)
    raw_context, context_source = _extract_raw_event_context(
        decision_row=resolved_decision,
        feature_row=resolved_feature,
        market_row=resolved_market,
    )
    return normalize_operator_event_context(
        raw_context,
        generated_at=utcnow_aware(),
        summary_note=(
            f"derived from {context_source}"
            if context_source is not None
            else f"no event context available for {symbol.upper()} / {timeframe or settings_row.default_timeframe}"
        ),
    )


def _build_ai_event_view_payload(
    *,
    decision_row: AgentRun | None,
) -> AIEventViewPayload:
    if decision_row is None:
        return AIEventViewPayload(source_state="unavailable")
    return derive_ai_event_view(
        output_payload=decision_row.output_payload if isinstance(decision_row.output_payload, dict) else {},
        metadata_json=decision_row.metadata_json if isinstance(decision_row.metadata_json, dict) else {},
    )


def _windows_for_symbol(
    windows: list[ManualNoTradeWindowPayload],
    *,
    symbol: str,
    now: datetime,
) -> list[ManualNoTradeWindowPayload]:
    results: list[ManualNoTradeWindowPayload] = []
    for window in windows:
        results.append(
            window.model_copy(
                update={"is_active": no_trade_window_is_active(window, symbol=symbol, now=now)}
            )
        )
    return results


def build_event_operator_control_payload(
    *,
    session: Session | None,
    settings_row: Setting,
    symbol: str | None = None,
    timeframe: str | None = None,
    decision_row: AgentRun | None = None,
    feature_row: FeatureSnapshot | None = None,
    market_row: MarketSnapshot | None = None,
    ai_event_view: AIEventViewPayload | None = None,
    evaluated_at: datetime | None = None,
) -> EventOperatorControlPayload:
    resolved_symbol = (symbol or settings_row.default_symbol).upper()
    resolved_timeframe = timeframe or settings_row.default_timeframe
    evaluation_time = ensure_utc_aware(evaluated_at) or utcnow_aware()
    operator_event_view = _deserialize_operator_event_view_payload(settings_row) or build_default_operator_event_view()
    manual_windows = _deserialize_manual_no_trade_windows(settings_row)
    event_context = _build_operator_event_context_payload(
        session=session,
        settings_row=settings_row,
        symbol=resolved_symbol,
        timeframe=resolved_timeframe,
        decision_row=decision_row,
        feature_row=feature_row,
        market_row=market_row,
    )
    resolved_decision = decision_row or _latest_symbol_decision(
        session,
        symbol=resolved_symbol,
        timeframe=resolved_timeframe,
    )
    resolved_ai_event_view = ai_event_view or _build_ai_event_view_payload(decision_row=resolved_decision)
    symbol_windows = _windows_for_symbol(manual_windows, symbol=resolved_symbol, now=evaluation_time)
    policy_evaluation = evaluate_event_policy(
        symbol=resolved_symbol,
        ai_event_view=resolved_ai_event_view,
        operator_event_view=operator_event_view,
        manual_no_trade_windows=symbol_windows,
        event_source_status=event_context.source_status,
        event_source_is_stale=event_context.is_stale,
        evaluated_at=evaluation_time,
    )
    return EventOperatorControlPayload(
        event_context=event_context,
        ai_event_view=resolved_ai_event_view,
        operator_event_view=operator_event_view,
        alignment_decision=policy_evaluation.alignment_decision,
        evaluated_operator_policy=policy_evaluation.evaluated_operator_policy,
        blocked_reason=policy_evaluation.blocked_reason,
        degraded_reason=policy_evaluation.degraded_reason,
        approval_required_reason=policy_evaluation.approval_required_reason,
        policy_source=policy_evaluation.policy_source,
        manual_no_trade_windows=symbol_windows,
        effective_policy_preview=policy_evaluation.alignment_decision.effective_policy_preview,
    )


def _alignment_audit_payload(
    *,
    session: Session,
    settings_row: Setting,
    symbols: list[str],
) -> dict[str, Any]:
    evaluations: dict[str, Any] = {}
    for symbol in symbols:
        payload = build_event_operator_control_payload(
            session=session,
            settings_row=settings_row,
            symbol=symbol,
        )
        evaluations[symbol] = {
            "alignment_status": payload.alignment_decision.alignment_status,
            "effective_policy_preview": payload.alignment_decision.effective_policy_preview,
            "reason_codes": list(payload.alignment_decision.reason_codes),
        }
    return {
        "symbols": list(symbols),
        "evaluations": evaluations,
        "evaluated_at": isoformat_utc(utcnow_aware()),
    }


def _event_control_scope_symbols(
    settings_row: Setting,
    *,
    applies_to_symbols: list[str] | None = None,
) -> list[str]:
    normalized = normalize_symbols(applies_to_symbols or [])
    if normalized:
        return normalized
    return get_effective_symbols(settings_row)


def _persist_event_operator_control_state(
    settings_row: Setting,
    *,
    operator_event_view: OperatorEventViewPayload | None,
    manual_no_trade_windows: list[ManualNoTradeWindowPayload],
) -> None:
    payload: dict[str, Any] = {
        "manual_no_trade_windows": _serialize_manual_no_trade_windows(manual_no_trade_windows),
    }
    serialized_view = _serialize_operator_event_view_payload(operator_event_view)
    if serialized_view is not None:
        payload["operator_event_view"] = serialized_view
    _write_event_operator_control_detail(settings_row, payload)


def _record_alignment_evaluated(
    session: Session,
    settings_row: Setting,
    *,
    actor: str,
    symbols: list[str],
) -> None:
    record_audit_event(
        session,
        event_type="alignment_evaluated",
        entity_type="settings",
        entity_id=str(settings_row.id),
        message="Operator event alignment preview re-evaluated.",
        payload={
            "actor": actor,
            **_alignment_audit_payload(session=session, settings_row=settings_row, symbols=symbols),
        },
    )


def upsert_operator_event_view(
    session: Session,
    payload: OperatorEventViewRequest,
) -> tuple[Setting, bool]:
    row = get_or_create_settings(session)
    existing_view = _deserialize_operator_event_view_payload(row)
    logical_before = _operator_event_view_logical_payload(existing_view)
    next_view = OperatorEventViewPayload(
        operator_bias=payload.operator_bias,
        operator_risk_state=payload.operator_risk_state,
        applies_to_symbols=normalize_symbols(payload.applies_to_symbols),
        horizon=payload.horizon,
        valid_from=ensure_utc_aware(payload.valid_from),
        valid_to=ensure_utc_aware(payload.valid_to),
        enforcement_mode=payload.enforcement_mode,
        note=payload.note,
        created_by=existing_view.created_by if existing_view is not None else payload.created_by,
        updated_at=utcnow_aware(),
    )
    logical_after = _operator_event_view_logical_payload(next_view)
    if logical_before == logical_after:
        return row, False
    windows = _deserialize_manual_no_trade_windows(row)
    _persist_event_operator_control_state(
        row,
        operator_event_view=next_view,
        manual_no_trade_windows=windows,
    )
    session.add(row)
    session.flush()
    scope_symbols = _event_control_scope_symbols(row, applies_to_symbols=next_view.applies_to_symbols)
    record_audit_event(
        session,
        event_type="operator_event_view_created" if existing_view is None else "operator_event_view_updated",
        entity_type="settings",
        entity_id=str(row.id),
        message="Operator event view persisted.",
        payload={
            "actor": payload.created_by,
            "symbols": scope_symbols,
            "before": logical_before,
            "after": logical_after,
        },
    )
    _record_alignment_evaluated(
        session,
        row,
        actor=payload.created_by,
        symbols=scope_symbols,
    )
    return row, True


def clear_operator_event_view(
    session: Session,
    *,
    actor: str = "operator-ui",
) -> tuple[Setting, bool]:
    row = get_or_create_settings(session)
    existing_view = _deserialize_operator_event_view_payload(row)
    if existing_view is None:
        return row, False
    windows = _deserialize_manual_no_trade_windows(row)
    scope_symbols = _event_control_scope_symbols(row, applies_to_symbols=existing_view.applies_to_symbols)
    _persist_event_operator_control_state(
        row,
        operator_event_view=None,
        manual_no_trade_windows=windows,
    )
    session.add(row)
    session.flush()
    record_audit_event(
        session,
        event_type="operator_event_view_cleared",
        entity_type="settings",
        entity_id=str(row.id),
        message="Operator event view cleared.",
        payload={
            "actor": actor,
            "symbols": scope_symbols,
            "before": _operator_event_view_logical_payload(existing_view),
            "after": None,
        },
    )
    _record_alignment_evaluated(
        session,
        row,
        actor=actor,
        symbols=scope_symbols,
    )
    return row, True


def create_manual_no_trade_window(
    session: Session,
    payload: ManualNoTradeWindowRequest,
) -> tuple[Setting, ManualNoTradeWindowPayload]:
    row = get_or_create_settings(session)
    windows = _deserialize_manual_no_trade_windows(row)
    now = utcnow_aware()
    window = ManualNoTradeWindowPayload(
        window_id=f"ntw_{uuid4().hex[:16]}",
        scope=payload.scope,
        start_at=ensure_utc_aware(payload.start_at),
        end_at=ensure_utc_aware(payload.end_at),
        reason=payload.reason,
        auto_resume=payload.auto_resume,
        require_manual_rearm=payload.require_manual_rearm,
        created_by=payload.created_by,
        updated_at=now,
        is_active=False,
    )
    windows.append(window)
    windows.sort(key=lambda item: ensure_utc_aware(item.start_at) or now, reverse=True)
    _persist_event_operator_control_state(
        row,
        operator_event_view=_deserialize_operator_event_view_payload(row),
        manual_no_trade_windows=windows,
    )
    session.add(row)
    session.flush()
    scope_symbols = (
        window.scope.symbols
        if window.scope.scope_type == "symbols" and window.scope.symbols
        else get_effective_symbols(row)
    )
    record_audit_event(
        session,
        event_type="manual_no_trade_window_created",
        entity_type="settings",
        entity_id=str(row.id),
        message="Manual no-trade window created.",
        payload={
            "actor": payload.created_by,
            "window_id": window.window_id,
            "symbols": scope_symbols,
            "scope": window.scope.model_dump(mode="json"),
            "before": None,
            "after": _manual_window_logical_payload(window),
        },
    )
    _record_alignment_evaluated(
        session,
        row,
        actor=payload.created_by,
        symbols=scope_symbols,
    )
    return row, window


def update_manual_no_trade_window(
    session: Session,
    *,
    window_id: str,
    payload: ManualNoTradeWindowRequest,
) -> tuple[Setting, ManualNoTradeWindowPayload, bool]:
    row = get_or_create_settings(session)
    windows = _deserialize_manual_no_trade_windows(row)
    target_index = next((index for index, item in enumerate(windows) if item.window_id == window_id), None)
    if target_index is None:
        raise LookupError(f"manual no-trade window not found: {window_id}")
    existing = windows[target_index]
    logical_before = _manual_window_logical_payload(existing)
    updated = existing.model_copy(
        update={
            "scope": payload.scope,
            "start_at": ensure_utc_aware(payload.start_at),
            "end_at": ensure_utc_aware(payload.end_at),
            "reason": payload.reason,
            "auto_resume": payload.auto_resume,
            "require_manual_rearm": payload.require_manual_rearm,
            "updated_at": utcnow_aware(),
            "is_active": False,
        }
    )
    logical_after = _manual_window_logical_payload(updated)
    if logical_before == logical_after:
        return row, existing, False
    windows[target_index] = updated
    windows.sort(key=lambda item: ensure_utc_aware(item.start_at) or utcnow_aware(), reverse=True)
    _persist_event_operator_control_state(
        row,
        operator_event_view=_deserialize_operator_event_view_payload(row),
        manual_no_trade_windows=windows,
    )
    session.add(row)
    session.flush()
    scope_symbols = (
        updated.scope.symbols
        if updated.scope.scope_type == "symbols" and updated.scope.symbols
        else get_effective_symbols(row)
    )
    record_audit_event(
        session,
        event_type="manual_no_trade_window_updated",
        entity_type="settings",
        entity_id=str(row.id),
        message="Manual no-trade window updated.",
        payload={
            "actor": payload.created_by,
            "window_id": updated.window_id,
            "symbols": scope_symbols,
            "scope": updated.scope.model_dump(mode="json"),
            "before": logical_before,
            "after": logical_after,
        },
    )
    _record_alignment_evaluated(
        session,
        row,
        actor=payload.created_by,
        symbols=scope_symbols,
    )
    return row, updated, True


def end_manual_no_trade_window(
    session: Session,
    *,
    window_id: str,
    actor: str = "operator-ui",
    end_at: datetime | None = None,
) -> tuple[Setting, ManualNoTradeWindowPayload, bool]:
    row = get_or_create_settings(session)
    windows = _deserialize_manual_no_trade_windows(row)
    target_index = next((index for index, item in enumerate(windows) if item.window_id == window_id), None)
    if target_index is None:
        raise LookupError(f"manual no-trade window not found: {window_id}")
    existing = windows[target_index]
    resolved_end_at = ensure_utc_aware(end_at) or utcnow_aware()
    if resolved_end_at <= ensure_utc_aware(existing.start_at):
        raise ValueError("end_at must be later than start_at")
    if ensure_utc_aware(existing.end_at) == resolved_end_at:
        return row, existing, False
    updated = existing.model_copy(
        update={
            "end_at": resolved_end_at,
            "updated_at": utcnow_aware(),
            "is_active": False,
        }
    )
    windows[target_index] = updated
    windows.sort(key=lambda item: ensure_utc_aware(item.start_at) or utcnow_aware(), reverse=True)
    _persist_event_operator_control_state(
        row,
        operator_event_view=_deserialize_operator_event_view_payload(row),
        manual_no_trade_windows=windows,
    )
    session.add(row)
    session.flush()
    scope_symbols = (
        updated.scope.symbols
        if updated.scope.scope_type == "symbols" and updated.scope.symbols
        else get_effective_symbols(row)
    )
    record_audit_event(
        session,
        event_type="manual_no_trade_window_ended",
        entity_type="settings",
        entity_id=str(row.id),
        message="Manual no-trade window ended.",
        payload={
            "actor": actor,
            "window_id": updated.window_id,
            "symbols": scope_symbols,
            "scope": updated.scope.model_dump(mode="json"),
            "before": _manual_window_logical_payload(existing),
            "after": _manual_window_logical_payload(updated),
        },
    )
    _record_alignment_evaluated(
        session,
        row,
        actor=actor,
        symbols=scope_symbols,
    )
    return row, updated, True


def get_or_create_settings(session: Session) -> Setting:
    row = session.scalar(select(Setting).limit(1))
    if row is not None:
        return row

    defaults = get_settings()
    tracked_symbols = _default_symbols(defaults)
    row = Setting(
        live_trading_enabled=defaults.live_trading_enabled,
        rollout_mode="full_live" if defaults.live_trading_enabled else "paper",
        limited_live_max_notional=DEFAULT_LIMITED_LIVE_MAX_NOTIONAL,
        manual_live_approval=defaults.manual_live_approval,
        live_execution_armed=False,
        live_execution_armed_until=None,
        live_approval_window_minutes=0,
        trading_paused=defaults.trading_paused,
        default_symbol=tracked_symbols[0],
        tracked_symbols=tracked_symbols,
        default_timeframe=defaults.default_timeframe,
        exchange_sync_interval_seconds=defaults.exchange_sync_interval_seconds,
        market_refresh_interval_minutes=defaults.market_refresh_interval_minutes,
        position_management_interval_seconds=defaults.position_management_interval_seconds,
        schedule_windows=_default_windows(defaults),
        symbol_cadence_overrides=[],
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
        partial_tp_rr=defaults.partial_tp_rr,
        partial_tp_size_pct=defaults.partial_tp_size_pct,
        move_stop_to_be_rr=defaults.move_stop_to_be_rr,
        time_stop_enabled=defaults.time_stop_enabled,
        time_stop_minutes=defaults.time_stop_minutes,
        time_stop_profit_floor=defaults.time_stop_profit_floor,
        holding_edge_decay_enabled=defaults.holding_edge_decay_enabled,
        reduce_on_regime_shift_enabled=defaults.reduce_on_regime_shift_enabled,
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


def _build_unknown_pnl_summary(latest_pnl: PnLSnapshot | None = None) -> dict[str, object]:
    return {
        "account_snapshot_available": False,
        "basis": "live_account_snapshot_unavailable",
        "basis_note": (
            "Live account balance and equity stay unknown until the first successful Binance account sync. "
            "Realized, fee and funding totals still come from the execution ledger plus funding ledger."
        ),
        "equity": None,
        "wallet_balance": None,
        "available_balance": None,
        "cash_balance": None,
        "realized_pnl": latest_pnl.gross_realized_pnl if latest_pnl is not None else 0.0,
        "fee_total": latest_pnl.fee_total if latest_pnl is not None else 0.0,
        "funding_total": latest_pnl.funding_total if latest_pnl is not None else 0.0,
        "net_pnl": latest_pnl.net_pnl if latest_pnl is not None else 0.0,
        "net_realized_pnl": latest_pnl.net_pnl if latest_pnl is not None else 0.0,
        "unrealized_pnl": latest_pnl.unrealized_pnl if latest_pnl is not None else 0.0,
        "daily_pnl": latest_pnl.daily_pnl if latest_pnl is not None else 0.0,
        "cumulative_pnl": latest_pnl.cumulative_pnl if latest_pnl is not None else 0.0,
        "consecutive_losses": latest_pnl.consecutive_losses if latest_pnl is not None else 0,
        "snapshot_time": None,
        "note": "Live account snapshot is not available yet.",
    }


def _build_unknown_account_sync_summary(
    settings_row: Setting,
    latest_pnl: PnLSnapshot | None = None,
) -> dict[str, object]:
    return {
        "account_snapshot_available": False,
        "status": "unknown",
        "reconciliation_mode": "unknown",
        "freshness_seconds": None,
        "stale_after_seconds": _account_sync_stale_seconds(settings_row),
        "equity": None,
        "wallet_balance": None,
        "available_balance": None,
        "realized_pnl": latest_pnl.gross_realized_pnl if latest_pnl is not None else 0.0,
        "fee_total": latest_pnl.fee_total if latest_pnl is not None else 0.0,
        "funding_total": latest_pnl.funding_total if latest_pnl is not None else 0.0,
        "net_pnl": latest_pnl.net_pnl if latest_pnl is not None else 0.0,
        "unrealized_pnl": latest_pnl.unrealized_pnl if latest_pnl is not None else 0.0,
        "last_synced_at": None,
        "last_warning_reason_code": None,
        "last_warning_message": None,
        "note": "Account sync status is not available until the first successful live snapshot is created.",
    }


def _build_pnl_summary(settings_row: Setting, latest_pnl: PnLSnapshot) -> dict[str, object]:
    if is_placeholder_live_snapshot(latest_pnl):
        return _build_unknown_pnl_summary(latest_pnl)
    return {
        "account_snapshot_available": True,
        "basis": "live_account_snapshot_preferred",
        "basis_note": (
            "Wallet, available balance and equity prefer the latest Binance account snapshot. "
            "Realized, fee and funding totals are reconciled from the execution ledger plus funding ledger."
        ),
        "equity": latest_pnl.equity,
        "wallet_balance": latest_pnl.wallet_balance,
        "available_balance": latest_pnl.available_balance,
        "cash_balance": latest_pnl.cash_balance,
        "realized_pnl": latest_pnl.gross_realized_pnl,
        "fee_total": latest_pnl.fee_total,
        "funding_total": latest_pnl.funding_total,
        "net_pnl": latest_pnl.net_pnl,
        "net_realized_pnl": latest_pnl.net_pnl,
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
    if is_placeholder_live_snapshot(latest_pnl):
        return _build_unknown_account_sync_summary(settings_row, latest_pnl)
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
    note = "Wallet, available balance and equity are currently aligned with the latest exchange account snapshot."
    reconciliation_mode = "exchange_confirmed"
    if freshness_seconds > stale_after_seconds:
        status = "stale"
        reconciliation_mode = "stale_snapshot"
        note = (
            "The latest account snapshot is stale. Freshness should be confirmed before relying on wallet, "
            "available balance or equity."
        )
    elif latest_warning is not None and latest_warning.created_at >= latest_pnl.created_at:
        status = "fallback_reconciled"
        reconciliation_mode = "deterministic_delta_fallback"
        note = (
            "Recent account sync degraded. Wallet, available balance and equity may be temporarily reconciled "
            "from the prior snapshot plus deterministic PnL and funding deltas until the next successful exchange sync."
        )

    return {
        "account_snapshot_available": True,
        "status": status,
        "reconciliation_mode": reconciliation_mode,
        "freshness_seconds": freshness_seconds,
        "stale_after_seconds": stale_after_seconds,
        "equity": latest_pnl.equity,
        "wallet_balance": latest_pnl.wallet_balance,
        "available_balance": latest_pnl.available_balance,
        "realized_pnl": latest_pnl.gross_realized_pnl,
        "fee_total": latest_pnl.fee_total,
        "funding_total": latest_pnl.funding_total,
        "net_pnl": latest_pnl.net_pnl,
        "unrealized_pnl": latest_pnl.unrealized_pnl,
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
    active_holding_profiles = {"scalp": 0, "swing": 0, "position": 0}
    hard_stop_active_positions = 0
    deterministic_hard_stop_positions = 0
    stop_widening_forbidden_positions = 0
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
            holding_profile = str(management.get("holding_profile") or "scalp").strip().lower() or "scalp"
            if holding_profile not in active_holding_profiles:
                holding_profile = "scalp"
            active_holding_profiles[holding_profile] += 1
            if bool(management.get("partial_take_profit_taken")):
                partial_take_profit_taken += 1
            if bool(management.get("hard_stop_active")):
                hard_stop_active_positions += 1
            if str(management.get("initial_stop_type") or "") == "deterministic_hard_stop":
                deterministic_hard_stop_positions += 1
            if management.get("stop_widening_allowed") is False:
                stop_widening_forbidden_positions += 1

    return {
        "mode": "conservative_dynamic_profit_protection",
        "enabled": settings_row.position_management_enabled,
        "protective_bias": "tighten_only",
        "rules_enabled": {
            "break_even": settings_row.position_management_enabled and settings_row.break_even_enabled,
            "atr_trailing_stop": settings_row.position_management_enabled and settings_row.atr_trailing_stop_enabled,
            "partial_take_profit": settings_row.position_management_enabled
            and settings_row.partial_take_profit_enabled,
            "time_stop": settings_row.position_management_enabled and settings_row.time_stop_enabled,
            "holding_edge_decay": settings_row.position_management_enabled
            and settings_row.holding_edge_decay_enabled,
            "reduce_on_regime_shift": settings_row.position_management_enabled
            and settings_row.reduce_on_regime_shift_enabled,
        },
        "fixed_parameters": {
            "break_even_trigger_r": settings_row.move_stop_to_be_rr,
            "trailing_activation_r": 1.0,
            "trailing_atr_multiple": 1.2,
            "partial_take_profit_trigger_r": settings_row.partial_tp_rr,
            "partial_take_profit_fraction": settings_row.partial_tp_size_pct,
            "time_stop_minutes": settings_row.time_stop_minutes,
            "time_stop_profit_floor": settings_row.time_stop_profit_floor,
            "edge_decay_start_ratio": 0.75,
        },
        "active_positions": active_positions,
        "managed_positions_with_baseline": managed_positions,
        "partial_take_profit_taken_positions": partial_take_profit_taken,
        "active_holding_profiles": active_holding_profiles,
        "hard_stop_active_positions": hard_stop_active_positions,
        "deterministic_hard_stop_positions": deterministic_hard_stop_positions,
        "stop_widening_forbidden_positions": stop_widening_forbidden_positions,
        "data_fallback_rule": (
            "If initial stop or holding-plan metadata is missing, the layer keeps the current stop and skips "
            "time stop or partial-take-profit automation."
        ),
        "summary": (
            "Break-even, ATR trailing, partial take-profit, time stop, holding-time decay, and regime weakening "
            "only tighten protection or reduce exposure. They never widen stop loss."
        ),
    }


def get_latest_blocked_reasons(session: Session | None) -> list[str]:
    if session is None:
        return []
    latest_risk = session.scalar(select(RiskCheck).order_by(desc(RiskCheck.created_at)).limit(1))
    if latest_risk is None or latest_risk.allowed:
        return []
    return [str(item) for item in latest_risk.reason_codes if item not in {None, ""}]


def get_latest_risk_gate_status(session: Session | None) -> tuple[bool | None, list[str]]:
    if session is None:
        return None, []
    latest_risk = session.scalar(select(RiskCheck).order_by(desc(RiskCheck.created_at)).limit(1))
    if latest_risk is None:
        return None, []
    payload = latest_risk.payload if isinstance(latest_risk.payload, dict) else {}
    raw_reason_codes = payload.get("reason_codes", []) if isinstance(payload.get("reason_codes", []), list) else latest_risk.reason_codes
    reason_codes = [str(item) for item in raw_reason_codes if item not in {None, ""}]
    return bool(latest_risk.allowed), _prioritize_blocked_reasons(reason_codes)


def _agent_run_symbol(row: AgentRun) -> str | None:
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    symbol = metadata.get("symbol")
    if isinstance(symbol, str) and symbol:
        return symbol.upper()
    input_payload = row.input_payload if isinstance(row.input_payload, dict) else {}
    market_snapshot = input_payload.get("market_snapshot")
    if isinstance(market_snapshot, dict):
        market_symbol = market_snapshot.get("symbol")
        if isinstance(market_symbol, str) and market_symbol:
            return market_symbol.upper()
    output_payload = row.output_payload if isinstance(row.output_payload, dict) else {}
    output_symbol = output_payload.get("symbol")
    if isinstance(output_symbol, str) and output_symbol:
        return output_symbol.upper()
    return None


def _latest_scheduler_timestamps_by_symbol(
    session: Session | None,
    *,
    workflows: list[str],
    symbols: list[str],
    limit: int = 500,
) -> dict[tuple[str, str], tuple[datetime | None, datetime | None]]:
    if session is None or not workflows or not symbols:
        return {}
    symbol_set = {symbol.upper() for symbol in symbols}
    targets = {(workflow, symbol) for workflow in workflows for symbol in symbol_set}
    resolved: dict[tuple[str, str], tuple[datetime | None, datetime | None]] = {}
    rows = list(
        session.scalars(
            select(SchedulerRun)
            .where(SchedulerRun.workflow.in_(workflows))
            .order_by(desc(SchedulerRun.created_at))
            .limit(limit)
        )
    )
    for row in rows:
        outcome = row.outcome if isinstance(row.outcome, dict) else {}
        symbol = str(outcome.get("symbol", "")).upper()
        key = (row.workflow, symbol)
        if symbol not in symbol_set or key not in targets or key in resolved:
            continue
        resolved[key] = (row.created_at, row.next_run_at)
        if len(resolved) == len(targets):
            break
    return resolved


def _latest_decision_activity_by_symbol(
    session: Session | None,
    *,
    symbols: list[str],
    limit: int = 500,
) -> tuple[dict[str, AgentRun], dict[str, datetime]]:
    if session is None or not symbols:
        return {}, {}
    symbol_set = {symbol.upper() for symbol in symbols}
    latest_runs: dict[str, AgentRun] = {}
    latest_ai_attempts: dict[str, datetime] = {}
    rows = list(
        session.scalars(
            select(AgentRun)
            .where(AgentRun.role == "trading_decision")
            .order_by(desc(AgentRun.created_at))
            .limit(limit)
        )
    )
    for row in rows:
        symbol = _agent_run_symbol(row)
        if symbol is None or symbol not in symbol_set:
            continue
        if symbol not in latest_runs:
            latest_runs[symbol] = row
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        if symbol not in latest_ai_attempts and (
            row.provider_name == "openai" or metadata.get("source") in {"llm", "llm_fallback"}
        ):
            latest_ai_attempts[symbol] = row.created_at
        if len(latest_runs) == len(symbol_set) and len(latest_ai_attempts) == len(symbol_set):
            break
    return latest_runs, latest_ai_attempts


def _latest_market_snapshots_by_symbol(
    session: Session | None,
    *,
    effective_schedule: list[EffectiveSymbolSettings],
    limit: int = 500,
) -> dict[tuple[str, str], MarketSnapshot]:
    if session is None or not effective_schedule:
        return {}
    symbols = sorted({item.symbol.upper() for item in effective_schedule})
    timeframes = sorted({item.timeframe for item in effective_schedule})
    targets = {(item.symbol.upper(), item.timeframe) for item in effective_schedule}
    resolved: dict[tuple[str, str], MarketSnapshot] = {}
    rows = list(
        session.scalars(
            select(MarketSnapshot)
            .where(MarketSnapshot.symbol.in_(symbols), MarketSnapshot.timeframe.in_(timeframes))
            .order_by(desc(MarketSnapshot.snapshot_time))
            .limit(limit)
        )
    )
    for row in rows:
        key = (row.symbol.upper(), row.timeframe)
        if key not in targets or key in resolved:
            continue
        resolved[key] = row
        if len(resolved) == len(targets):
            break
    return resolved


def build_symbol_effective_cadences(
    session: Session | None,
    settings_row: Setting,
) -> list[SymbolEffectiveCadence]:
    items: list[SymbolEffectiveCadence] = []
    now = utcnow_naive()
    effective_schedule = get_effective_symbol_schedule(settings_row)
    symbols = [item.symbol for item in effective_schedule]
    scheduler_index = _latest_scheduler_timestamps_by_symbol(
        session,
        workflows=["market_refresh_cycle", "position_management_cycle", "interval_decision_cycle"],
        symbols=symbols,
    )
    latest_runs, latest_ai_attempts = _latest_decision_activity_by_symbol(session, symbols=symbols)
    latest_snapshots = _latest_market_snapshots_by_symbol(session, effective_schedule=effective_schedule)

    for effective in effective_schedule:
        latest_market_snapshot = latest_snapshots.get((effective.symbol.upper(), effective.timeframe))
        market_run_at, market_next_due = scheduler_index.get(
            ("market_refresh_cycle", effective.symbol.upper()),
            (None, None),
        )
        position_run_at, position_next_due = scheduler_index.get(
            ("position_management_cycle", effective.symbol.upper()),
            (None, None),
        )
        decision_run_at, decision_next_due = scheduler_index.get(
            ("interval_decision_cycle", effective.symbol.upper()),
            (None, None),
        )
        decision_agent_run = latest_runs.get(effective.symbol.upper())
        last_ai_decision_at = latest_ai_attempts.get(effective.symbol.upper())
        last_market_refresh_at = market_run_at or (
            latest_market_snapshot.snapshot_time if latest_market_snapshot is not None else None
        )
        if last_market_refresh_at is not None:
            market_next_due = last_market_refresh_at + timedelta(minutes=effective.market_refresh_interval_minutes)
        if position_run_at is not None:
            position_next_due = position_run_at + timedelta(seconds=effective.position_management_interval_seconds)
        if decision_run_at is not None:
            decision_next_due = decision_run_at + timedelta(minutes=effective.decision_cycle_interval_minutes)
        if last_ai_decision_at is not None:
            ai_next_due = last_ai_decision_at + timedelta(minutes=effective.ai_call_interval_minutes)
        else:
            ai_next_due = now
        items.append(
            SymbolEffectiveCadence(
                symbol=effective.symbol,
                enabled=effective.enabled,
                uses_global_defaults=effective.uses_global_defaults,
                timeframe=effective.timeframe,
                market_refresh_interval_minutes=effective.market_refresh_interval_minutes,
                position_management_interval_seconds=effective.position_management_interval_seconds,
                decision_cycle_interval_minutes=effective.decision_cycle_interval_minutes,
                ai_call_interval_minutes=effective.ai_call_interval_minutes,
                ai_backstop_enabled=effective.ai_backstop_enabled,
                ai_backstop_interval_minutes=effective.ai_backstop_interval_minutes,
                last_market_refresh_at=last_market_refresh_at,
                last_position_management_at=position_run_at,
                last_decision_at=decision_run_at or (decision_agent_run.created_at if decision_agent_run else None),
                last_ai_decision_at=last_ai_decision_at,
                next_market_refresh_due_at=market_next_due,
                next_position_management_due_at=position_next_due,
                next_decision_due_at=decision_next_due,
                next_ai_call_due_at=ai_next_due,
            )
        )
    return items


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


def _parse_runtime_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _coerce_optional_bool(value: object) -> bool | None:
    if value in {None, ""}:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
    return None


def get_live_approval_status(settings_row: Setting) -> tuple[bool, str, dict[str, object]]:
    if not settings_row.manual_live_approval:
        return False, "policy_disabled", {}
    if is_live_execution_armed(settings_row):
        return True, "armed", {}

    pause_detail = dict(settings_row.pause_reason_detail or {})
    resume_context = pause_detail.get("resume_context", {})
    grace_until = _parse_runtime_datetime(
        resume_context.get("approval_grace_until") if isinstance(resume_context, dict) else None
    )
    now = utcnow_naive()
    if (
        settings_row.pause_origin == "system"
        and pause_reason_allows_auto_resume(settings_row.pause_reason_code)
        and isinstance(resume_context, dict)
        and bool(resume_context.get("live_execution_ready_before_pause"))
        and grace_until is not None
        and grace_until > now
    ):
        return True, "grace", {"approval_grace_until": grace_until.isoformat()}

    return False, "required", {}


def is_live_execution_ready(settings_row: Setting, defaults: AppConfig | None = None) -> bool:
    app_defaults = defaults or get_settings()
    credentials = get_runtime_credentials(settings_row, defaults=app_defaults)
    return bool(
        app_defaults.live_trading_env_enabled
        and rollout_mode_uses_live_path(settings_row)
        and settings_row.manual_live_approval
        and is_live_execution_armed(settings_row)
        and credentials.binance_api_key
        and credentials.binance_api_secret
    )


def _humanize_guard_code(code: str) -> str:
    return code.replace("_", " ").strip().title()


def _guard_message_for_code(code: str, fallback_suffix: str = "가드 모드입니다.") -> str:
    if code == "MARKET_STATE_STALE":
        code = "STALE_MARKET_DATA"
    elif code == "MARKET_STATE_INCOMPLETE":
        code = "INCOMPLETE_MARKET_DATA"
    return GUARD_MODE_REASON_MESSAGES.get(code, f"{_humanize_guard_code(code)} 상태로 {fallback_suffix}")


def _derive_live_execution_guard_reason(
    settings_row: Setting,
    *,
    defaults: AppConfig | None = None,
) -> tuple[str | None, str | None, str | None]:
    app_defaults = defaults or get_settings()
    credentials = get_runtime_credentials(settings_row, defaults=app_defaults)
    rollout_mode = get_rollout_mode(settings_row)
    if not app_defaults.live_trading_env_enabled:
        code = "LIVE_ENV_DISABLED"
    elif not rollout_mode_uses_live_path(settings_row):
        code = "LIVE_TRADING_DISABLED"
    elif not credentials.binance_api_key or not credentials.binance_api_secret:
        code = "LIVE_CREDENTIALS_MISSING"
    elif not settings_row.manual_live_approval:
        code = "LIVE_APPROVAL_POLICY_DISABLED"
    elif not is_live_execution_armed(settings_row):
        code = "LIVE_APPROVAL_REQUIRED"
    elif rollout_mode == "shadow":
        code = "ROLLOUT_MODE_SHADOW"
    elif rollout_mode == "live_dry_run":
        code = "ROLLOUT_MODE_LIVE_DRY_RUN"
    else:
        return None, None, None
    return "readiness", code, _guard_message_for_code(code)


def _derive_sync_blocking_reasons(sync_freshness_summary: dict[str, object]) -> list[str]:
    reason_codes: list[str] = []
    for scope in ("account", "positions", "open_orders", "protective_orders"):
        scope_payload = sync_freshness_summary.get(scope)
        if not isinstance(scope_payload, dict):
            continue
        status = str(scope_payload.get("status") or scope_payload.get("raw_status") or "")
        has_observation = any(
            scope_payload.get(key) not in {None, ""}
            for key in ("last_sync_at", "last_attempt_at", "last_failure_at", "last_skip_at")
        )
        if status == "unknown" and not has_observation:
            continue
        if status in {"failed", "incomplete"}:
            code = str(scope_payload.get("last_failure_reason") or SYNC_SCOPE_GUARD_REASON_CODES[scope])
        elif status == "skipped":
            code = str(scope_payload.get("last_skip_reason") or SYNC_SCOPE_GUARD_REASON_CODES[scope])
        elif bool(scope_payload.get("stale")):
            code = SYNC_SCOPE_GUARD_REASON_CODES[scope]
        else:
            continue
        if code and code not in reason_codes:
            reason_codes.append(code)
    return reason_codes


def _derive_market_blocking_reasons(market_freshness_summary: dict[str, object]) -> list[str]:
    if not market_freshness_summary.get("snapshot_at"):
        return []
    if bool(market_freshness_summary.get("incomplete")):
        return ["MARKET_STATE_INCOMPLETE"]
    if bool(market_freshness_summary.get("stale")):
        return ["MARKET_STATE_STALE"]
    return []


def _derive_reconciliation_blocking_reasons(reconciliation_summary: dict[str, object]) -> list[str]:
    if not isinstance(reconciliation_summary, dict):
        return []
    reason_codes: list[str] = []
    if bool(reconciliation_summary.get("mode_guard_active")):
        reason_code = str(reconciliation_summary.get("mode_guard_reason_code") or "").strip()
        if reason_code:
            reason_codes.append(reason_code)
    if bool(reconciliation_summary.get("unresolved_submission_badge")):
        reason_codes.append("UNRESOLVED_SUBMISSION_GUARD_ACTIVE")
    return reason_codes


def _derive_user_stream_blocking_reasons(user_stream_summary: dict[str, object]) -> list[str]:
    if not isinstance(user_stream_summary, dict):
        return []
    stream_source = str(user_stream_summary.get("stream_source") or "")
    rotate_status = str(user_stream_summary.get("listen_key_rotate_status") or "")
    last_error = str(user_stream_summary.get("last_error") or "")
    if stream_source != "rest_polling_fallback":
        return []
    if rotate_status in {"pending", "failed"}:
        return ["USER_STREAM_LISTEN_KEY_ROTATION_PENDING"]
    if last_error in {"LISTEN_KEY_EXPIRED", "USER_STREAM_LISTEN_KEY_ROTATE_FAILED"}:
        return ["USER_STREAM_LISTEN_KEY_ROTATION_PENDING"]
    return []


def _user_stream_blocks_new_entries(user_stream_summary: dict[str, object]) -> bool:
    return bool(_derive_user_stream_blocking_reasons(user_stream_summary))


def _reconciliation_blocks_new_entries(reconciliation_summary: dict[str, object]) -> bool:
    return bool(_derive_reconciliation_blocking_reasons(reconciliation_summary))


def _is_one_way_requirement_block(reconciliation_summary: dict[str, object]) -> bool:
    if not isinstance(reconciliation_summary, dict):
        return False
    position_mode = str(reconciliation_summary.get("position_mode") or "").strip().lower()
    return position_mode in {"hedge", "unknown"}


def _one_way_requirement_reason_payload(
    reconciliation_summary: dict[str, object],
) -> tuple[str | None, str | None]:
    if not _is_one_way_requirement_block(reconciliation_summary):
        return None, None
    reason_code = str(reconciliation_summary.get("mode_guard_reason_code") or "").strip() or None
    return reason_code, "one-way required for current local position model"


def _prioritize_blocked_reasons(reason_codes: list[str]) -> list[str]:
    unique: list[str] = []
    for code in reason_codes:
        normalized = str(code or "").strip()
        if normalized and normalized not in unique:
            unique.append(normalized)
    return sorted(
        unique,
        key=lambda code: (STALE_FIRST_REASON_PRIORITY.get(code, 100), unique.index(code)),
    )


def derive_guard_mode_reason(
    settings_row: Setting,
    *,
    defaults: AppConfig | None = None,
    runtime_state: dict[str, object] | None = None,
    latest_blocked_reasons: list[str] | None = None,
    auto_resume_last_blockers: list[str] | None = None,
    sync_freshness_summary: dict[str, object] | None = None,
    market_freshness_summary: dict[str, object] | None = None,
) -> dict[str, str | None]:
    app_defaults = defaults or get_settings()
    runtime = runtime_state or summarize_runtime_state(settings_row)
    sync_blocked_reasons = _derive_sync_blocking_reasons(sync_freshness_summary or {})
    market_blocked_reasons = _derive_market_blocking_reasons(market_freshness_summary or {})
    user_stream_blocked_reasons = _derive_user_stream_blocking_reasons(dict(runtime.get("user_stream_summary") or {}))
    blocked_reasons = _prioritize_blocked_reasons(
        sync_blocked_reasons
        + market_blocked_reasons
        + user_stream_blocked_reasons
        + [str(item) for item in (latest_blocked_reasons or []) if item]
    )
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
    category, code, message = _derive_live_execution_guard_reason(settings_row, defaults=app_defaults)
    if code in {"ROLLOUT_MODE_SHADOW", "ROLLOUT_MODE_LIVE_DRY_RUN"}:
        return {
            "guard_mode_reason_category": category,
            "guard_mode_reason_code": code,
            "guard_mode_reason_message": message,
        }
    if not is_live_execution_ready(settings_row, defaults=app_defaults):
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


def _build_market_freshness_summary(
    session: Session | None,
    settings_row: Setting,
) -> dict[str, object]:
    symbol = settings_row.default_symbol.upper()
    timeframe = settings_row.default_timeframe
    if session is None:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "source": "snapshot",
            "status": "unknown",
            "snapshot_at": None,
            "stale": True,
            "incomplete": True,
            "latest_price": None,
        }

    latest_market = session.scalar(
        select(MarketSnapshot)
        .where(
            MarketSnapshot.symbol == symbol,
            MarketSnapshot.timeframe == timeframe,
        )
        .order_by(desc(MarketSnapshot.snapshot_time))
        .limit(1)
    )
    if latest_market is None:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "source": "snapshot",
            "status": "missing",
            "snapshot_at": None,
            "stale": True,
            "incomplete": True,
            "latest_price": None,
        }

    is_incomplete = not latest_market.is_complete
    is_stale = bool(latest_market.is_stale)
    status = "fresh"
    if is_incomplete:
        status = "incomplete"
    elif is_stale:
        status = "stale"
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "source": "snapshot",
        "status": status,
        "snapshot_at": latest_market.snapshot_time,
        "stale": is_stale,
        "incomplete": is_incomplete,
        "latest_price": latest_market.latest_price,
    }


def _sync_blocks_new_entries(sync_freshness_summary: dict[str, object]) -> bool:
    return any(
        isinstance(scope_payload, dict) and (bool(scope_payload.get("stale")) or bool(scope_payload.get("incomplete")))
        for scope_payload in sync_freshness_summary.values()
    )


def _market_blocks_new_entries(market_freshness_summary: dict[str, object]) -> bool:
    return bool(market_freshness_summary.get("stale")) or bool(market_freshness_summary.get("incomplete"))


def _build_control_status_summary(
    settings_row: Setting,
    *,
    operating_state: str,
    current_cycle_blocked_reasons: list[str],
    risk_allowed: bool | None,
    reconciliation_summary: dict[str, object],
    drawdown_state_summary: dict[str, object],
) -> ControlStatusSummary:
    approval_window_open, approval_state, approval_detail = get_live_approval_status(settings_row)
    account_sync_detail = get_sync_state_detail(settings_row).get("account", {})
    exchange_can_trade = _coerce_optional_bool(account_sync_detail.get("exchange_can_trade"))
    rollout_mode = get_rollout_mode(settings_row)
    resolved_risk_allowed = risk_allowed
    if resolved_risk_allowed is None and current_cycle_blocked_reasons:
        resolved_risk_allowed = False
    one_way_reason_code, one_way_reason_message = _one_way_requirement_reason_payload(reconciliation_summary)
    approval_control_blocked_reasons = _prioritize_blocked_reasons(
        list(current_cycle_blocked_reasons) + ([one_way_reason_code] if one_way_reason_code else [])
    )
    return ControlStatusSummary(
        exchange_can_trade=exchange_can_trade,
        rollout_mode=rollout_mode,
        exchange_submit_allowed=rollout_mode_allows_exchange_submit(settings_row),
        limited_live_max_notional=(
            get_limited_live_max_notional(settings_row) if rollout_mode == "limited_live" else None
        ),
        app_live_armed=is_live_execution_armed(settings_row),
        approval_window_open=approval_window_open,
        approval_state=approval_state,
        approval_detail=dict(approval_detail),
        paused=settings_row.trading_paused,
        degraded=operating_state in {
            PROTECTION_REQUIRED_STATE,
            DEGRADED_MANAGE_ONLY_STATE,
            EMERGENCY_EXIT_STATE,
        },
        risk_allowed=resolved_risk_allowed,
        blocked_reasons_current_cycle=_prioritize_blocked_reasons(current_cycle_blocked_reasons),
        approval_control_blocked_reasons=approval_control_blocked_reasons,
        live_arm_disabled=bool(one_way_reason_message),
        live_arm_disable_reason_code=one_way_reason_code,
        live_arm_disable_reason=one_way_reason_message,
        current_drawdown_state=str(drawdown_state_summary.get("current_drawdown_state") or "normal"),
        drawdown_state_entered_at=_parse_runtime_datetime(drawdown_state_summary.get("entered_at")),
        drawdown_transition_reason=str(drawdown_state_summary.get("transition_reason") or "") or None,
        drawdown_policy_adjustments=dict(drawdown_state_summary.get("policy_adjustments") or {}),
    )


def build_operational_status_payload(
    settings_row: Setting,
    *,
    session: Session | None = None,
    defaults: AppConfig | None = None,
    runtime_state: dict[str, object] | None = None,
    operating_state_override: str | None = None,
    missing_protection_symbols_override: list[str] | None = None,
    missing_protection_items_override: dict[str, list[str]] | None = None,
    blocked_reasons: list[str] | None = None,
    latest_blocked_reasons: list[str] | None = None,
    risk_allowed: bool | None = None,
    account_sync_summary: dict[str, object] | None = None,
    sync_freshness_summary: dict[str, object] | None = None,
    market_freshness_summary: dict[str, object] | None = None,
) -> OperationalStatusPayload:
    app_defaults = defaults or get_settings()
    current_session = session or object_session(settings_row)
    runtime = runtime_state or summarize_runtime_state(settings_row)
    live_execution_ready = is_live_execution_ready(settings_row, defaults=app_defaults)
    auto_resume_state = settings_row.pause_reason_detail.get("auto_resume", {}) if settings_row.trading_paused else {}
    auto_resume_last_blockers = [str(item) for item in auto_resume_state.get("blockers", [])]
    current_cycle_blocked_reasons = [str(item) for item in (blocked_reasons or []) if item not in {None, ""}]
    reconciliation_summary = dict(runtime.get("reconciliation_summary") or {})
    drawdown_state_summary = dict(runtime.get("drawdown_state_summary") or {})
    if not drawdown_state_summary and current_session is not None:
        drawdown_state_summary = build_drawdown_state_snapshot(
            current_session,
            settings_row,
            current_detail=get_drawdown_state_detail(settings_row),
        )
    if risk_allowed is None and current_session is not None:
        risk_allowed, latest_cycle_blocked_reasons = get_latest_risk_gate_status(current_session)
        if not current_cycle_blocked_reasons:
            current_cycle_blocked_reasons = latest_cycle_blocked_reasons
    sync_summary = dict(sync_freshness_summary or build_sync_freshness_summary(settings_row))
    market_summary = dict(market_freshness_summary or _build_market_freshness_summary(current_session, settings_row))
    user_stream_summary = dict(runtime.get("user_stream_summary") or {})
    inject_freshness_blockers = not settings_row.trading_paused
    sync_blocked_reasons = _derive_sync_blocking_reasons(sync_summary) if inject_freshness_blockers else []
    market_blocked_reasons = _derive_market_blocking_reasons(market_summary) if inject_freshness_blockers else []
    user_stream_blocked_reasons = _derive_user_stream_blocking_reasons(user_stream_summary) if inject_freshness_blockers else []
    reconciliation_blocked_reasons = (
        _derive_reconciliation_blocking_reasons(reconciliation_summary) if inject_freshness_blockers else []
    )
    recent_blocked_reasons = _prioritize_blocked_reasons(
        sync_blocked_reasons
        + market_blocked_reasons
        + user_stream_blocked_reasons
        + reconciliation_blocked_reasons
        + [
            str(item)
            for item in (
                latest_blocked_reasons
                if latest_blocked_reasons is not None
                else (current_cycle_blocked_reasons or get_latest_blocked_reasons(current_session))
            )
            if item not in {None, ""}
        ]
    )
    current_blocked_reasons = _prioritize_blocked_reasons(
        sync_blocked_reasons
        + market_blocked_reasons
        + user_stream_blocked_reasons
        + reconciliation_blocked_reasons
        + (current_cycle_blocked_reasons or recent_blocked_reasons)
    )
    latest_pnl = get_latest_pnl_snapshot(current_session, settings_row) if current_session is not None else None
    account_summary: dict[str, object]
    if account_sync_summary is not None:
        account_summary = dict(account_sync_summary)
    elif latest_pnl is not None:
        account_summary = _build_account_sync_summary(current_session, settings_row, latest_pnl)
    else:
        account_summary = _build_unknown_account_sync_summary(settings_row)
    missing_protection_symbols = (
        [str(item) for item in (missing_protection_symbols_override or []) if item not in {None, ""}]
        if missing_protection_symbols_override is not None
        else [str(item) for item in runtime["missing_protection_symbols"]]
    )
    missing_protection_items = (
        {
            str(key): [str(item) for item in value if item not in {None, ""}]
            for key, value in (missing_protection_items_override or {}).items()
        }
        if missing_protection_items_override is not None
        else {
            str(key): [str(item) for item in value]
            for key, value in runtime["missing_protection_items"].items()
        }
    )
    operating_state = operating_state_override or str(runtime.get("operating_state", "TRADABLE"))
    guard_runtime = {**runtime, "operating_state": operating_state}
    guard_mode_reason = derive_guard_mode_reason(
        settings_row,
        defaults=app_defaults,
        runtime_state=guard_runtime,
        latest_blocked_reasons=recent_blocked_reasons,
        auto_resume_last_blockers=auto_resume_last_blockers,
        sync_freshness_summary=sync_summary,
        market_freshness_summary=market_summary,
    )
    rollout_mode = get_rollout_mode(settings_row)
    exchange_submit_allowed = rollout_mode_allows_exchange_submit(settings_row)
    can_enter_new_position = (
        exchange_submit_allowed
        and
        live_execution_ready
        and not settings_row.trading_paused
        and operating_state == "TRADABLE"
        and not _sync_blocks_new_entries(sync_summary)
        and not _market_blocks_new_entries(market_summary)
        and not _user_stream_blocks_new_entries(user_stream_summary)
        and not _reconciliation_blocks_new_entries(reconciliation_summary)
    )
    pause_policy = get_pause_reason_policy(settings_row.pause_reason_code)
    one_way_reason_code, one_way_reason_message = _one_way_requirement_reason_payload(reconciliation_summary)
    control_status_summary = _build_control_status_summary(
        settings_row,
        operating_state=operating_state,
        current_cycle_blocked_reasons=current_cycle_blocked_reasons,
        risk_allowed=risk_allowed,
        reconciliation_summary=reconciliation_summary,
        drawdown_state_summary=drawdown_state_summary,
    )
    operator_alert: dict[str, object] = {}
    if one_way_reason_message:
        operator_alert = {
            "level": "critical",
            "source": "reconciliation_position_mode",
            "reason_code": one_way_reason_code,
            "message": one_way_reason_message,
            "position_mode": str(reconciliation_summary.get("position_mode") or "unknown"),
            "position_mode_checked_at": reconciliation_summary.get("position_mode_checked_at"),
            "guarded_symbols_count": int(reconciliation_summary.get("guarded_symbols_count") or 0),
        }
    return OperationalStatusPayload(
        live_trading_enabled=settings_row.live_trading_enabled,
        rollout_mode=rollout_mode,
        exchange_submit_allowed=exchange_submit_allowed,
        limited_live_max_notional=(
            get_limited_live_max_notional(settings_row) if rollout_mode == "limited_live" else None
        ),
        live_trading_env_enabled=app_defaults.live_trading_env_enabled,
        live_execution_ready=live_execution_ready,
        trading_paused=settings_row.trading_paused,
        approval_armed=is_live_execution_armed(settings_row),
        approval_expires_at=settings_row.live_execution_armed_until,
        approval_window_minutes=settings_row.live_approval_window_minutes,
        operating_state=operating_state,
        guard_mode_reason_category=guard_mode_reason["guard_mode_reason_category"],
        guard_mode_reason_code=guard_mode_reason["guard_mode_reason_code"],
        guard_mode_reason_message=guard_mode_reason["guard_mode_reason_message"],
        pause_reason_code=settings_row.pause_reason_code,
        pause_origin=settings_row.pause_origin,
        pause_triggered_at=settings_row.pause_triggered_at,
        auto_resume_after=settings_row.auto_resume_after,
        auto_resume_status=str(auto_resume_state.get("status", "not_paused" if not settings_row.trading_paused else "idle")),
        auto_resume_eligible=pause_policy.auto_resume_eligible if settings_row.trading_paused else False,
        auto_resume_last_blockers=auto_resume_last_blockers,
        pause_severity=pause_reason_severity(settings_row.pause_reason_code) if settings_row.trading_paused else None,
        pause_recovery_class=pause_reason_recovery_class(settings_row.pause_reason_code) if settings_row.trading_paused else None,
        blocked_reasons=current_blocked_reasons,
        latest_blocked_reasons=recent_blocked_reasons,
        account_sync_summary=account_summary,
        sync_freshness_summary=sync_summary,
        market_freshness_summary=market_summary,
        protection_recovery_status=str(runtime["protection_recovery_status"]),
        protection_recovery_active=bool(runtime["protection_recovery_active"]),
        protection_recovery_failure_count=int(runtime["protection_recovery_failure_count"]),
        missing_protection_symbols=missing_protection_symbols,
        missing_protection_items=missing_protection_items,
        current_drawdown_state=str(drawdown_state_summary.get("current_drawdown_state") or "normal"),
        drawdown_state_entered_at=_parse_runtime_datetime(drawdown_state_summary.get("entered_at")),
        drawdown_transition_reason=str(drawdown_state_summary.get("transition_reason") or "") or None,
        drawdown_policy_adjustments=dict(drawdown_state_summary.get("policy_adjustments") or {}),
        control_status_summary=control_status_summary,
        user_stream_summary=user_stream_summary,
        reconciliation_summary=reconciliation_summary,
        candidate_selection_summary=dict(runtime.get("candidate_selection_summary") or {}),
        operator_alert=operator_alert,
        can_enter_new_position=can_enter_new_position,
    )


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


def serialize_settings(settings_row: Setting) -> dict[str, object]:
    defaults = get_settings()
    credentials = get_runtime_credentials(settings_row, defaults=defaults)
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

    rollout_mode = get_rollout_mode(settings_row)
    exchange_submit_allowed = rollout_mode_allows_exchange_submit(settings_row)
    mode = "live_guarded"
    if settings_row.trading_paused:
        mode = "paused"
    elif is_live_execution_ready(settings_row, defaults=defaults) and exchange_submit_allowed:
        mode = "live_ready"

    effective_max_leverage = min(settings_row.max_leverage, DISPLAY_MAX_LEVERAGE)
    effective_max_risk_per_trade = min(settings_row.max_risk_per_trade, DISPLAY_MAX_RISK_PER_TRADE)
    effective_max_daily_loss = min(settings_row.max_daily_loss, DISPLAY_MAX_DAILY_LOSS)
    exposure_limits = get_exposure_limits(settings_row)
    runtime_state = summarize_runtime_state(settings_row)
    latest_pnl = get_latest_pnl_snapshot(current_session, settings_row) if current_session is not None else None
    pnl_summary = _build_pnl_summary(settings_row, latest_pnl) if latest_pnl is not None else _build_unknown_pnl_summary()
    account_sync_summary = (
        _build_account_sync_summary(current_session, settings_row, latest_pnl)
        if latest_pnl is not None
        else _build_unknown_account_sync_summary(settings_row)
    )
    sync_freshness_summary = build_sync_freshness_summary(settings_row)
    market_freshness_summary = _build_market_freshness_summary(current_session, settings_row)
    if current_session is not None and pnl_summary.get("equity") is not None:
        from trading_mvp.services.risk import build_current_exposure_summary

        exposure_summary = build_current_exposure_summary(
            current_session,
            settings_row,
            equity=_coerce_float(pnl_summary.get("equity"), 0.0),
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
        settings_row=settings_row,
    )
    latest_decision_rationale_codes: list[str] = []
    latest_decision_code: str | None = None
    latest_trading_run: AgentRun | None = None
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
    current_risk_allowed, current_cycle_blocked_reasons = get_latest_risk_gate_status(current_session)
    adaptive_signal_summary = summarize_adaptive_signal_state(
        adaptive_signal_context,
        latest_rationale_codes=latest_decision_rationale_codes,
        latest_decision=latest_decision_code,
        latest_entry_mode=(
            str(latest_trading_run.output_payload.get("entry_mode"))
            if current_session is not None
            and latest_trading_run is not None
            and isinstance(latest_trading_run.output_payload, dict)
            and latest_trading_run.output_payload.get("entry_mode") not in {None, ""}
            else None
        ),
    )
    execution_policy_summary = summarize_execution_policy(settings_row)
    position_management_summary = _build_position_management_summary(current_session, settings_row)
    event_operator_control = build_event_operator_control_payload(
        session=current_session,
        settings_row=settings_row,
        symbol=settings_row.default_symbol.upper(),
        timeframe=settings_row.default_timeframe,
    )
    operational_status = build_operational_status_payload(
        settings_row,
        session=current_session,
        defaults=defaults,
        runtime_state=runtime_state,
        blocked_reasons=current_cycle_blocked_reasons,
        latest_blocked_reasons=current_cycle_blocked_reasons,
        risk_allowed=current_risk_allowed,
        account_sync_summary=account_sync_summary,
        sync_freshness_summary=sync_freshness_summary,
        market_freshness_summary=market_freshness_summary,
    )
    symbol_cadence_overrides = [
        SymbolCadenceOverride(**item) for item in get_symbol_cadence_overrides(settings_row)
    ]
    symbol_effective_cadences = build_symbol_effective_cadences(current_session, settings_row)

    payload = AppSettingsResponse(
        id=settings_row.id,
        operational_status=operational_status,
        control_status_summary=operational_status.control_status_summary,
        live_trading_enabled=settings_row.live_trading_enabled,
        rollout_mode=rollout_mode,
        exchange_submit_allowed=exchange_submit_allowed,
        limited_live_max_notional=get_limited_live_max_notional(settings_row),
        live_trading_env_enabled=defaults.live_trading_env_enabled,
        manual_live_approval=settings_row.manual_live_approval,
        live_execution_armed=is_live_execution_armed(settings_row),
        live_execution_armed_until=settings_row.live_execution_armed_until,
        live_approval_window_minutes=settings_row.live_approval_window_minutes,
        live_execution_ready=operational_status.live_execution_ready,
        trading_paused=operational_status.trading_paused,
        approval_armed=operational_status.approval_armed,
        approval_expires_at=operational_status.approval_expires_at,
        can_enter_new_position=operational_status.can_enter_new_position,
        guard_mode_reason_category=operational_status.guard_mode_reason_category,
        guard_mode_reason_code=operational_status.guard_mode_reason_code,
        guard_mode_reason_message=operational_status.guard_mode_reason_message,
        pause_reason_code=operational_status.pause_reason_code,
        pause_origin=operational_status.pause_origin,
        pause_reason_detail=settings_row.pause_reason_detail,
        pause_triggered_at=operational_status.pause_triggered_at,
        auto_resume_after=operational_status.auto_resume_after,
        auto_resume_whitelisted=pause_reason_allows_auto_resume(settings_row.pause_reason_code),
        auto_resume_eligible=operational_status.auto_resume_eligible,
        auto_resume_status=operational_status.auto_resume_status,
        blocked_reasons=operational_status.blocked_reasons,
        auto_resume_last_blockers=operational_status.auto_resume_last_blockers,
        latest_blocked_reasons=operational_status.latest_blocked_reasons,
        pause_severity=operational_status.pause_severity,
        pause_recovery_class=operational_status.pause_recovery_class,
        operating_state=operational_status.operating_state,
        protection_recovery_status=operational_status.protection_recovery_status,
        protection_recovery_active=operational_status.protection_recovery_active,
        protection_recovery_failure_count=operational_status.protection_recovery_failure_count,
        missing_protection_symbols=operational_status.missing_protection_symbols,
        missing_protection_items=operational_status.missing_protection_items,
        pnl_summary=pnl_summary,
        account_sync_summary=operational_status.account_sync_summary,
        sync_freshness_summary=operational_status.sync_freshness_summary,
        market_freshness_summary=operational_status.market_freshness_summary,
        exposure_summary=exposure_summary,
        execution_policy_summary=execution_policy_summary,
        market_context_summary=market_context_summary,
        event_operator_control=event_operator_control,
        adaptive_protection_summary=adaptive_protection_summary,
        adaptive_signal_summary=adaptive_signal_summary,
        position_management_summary=position_management_summary,
        user_stream_summary=operational_status.user_stream_summary,
        reconciliation_summary=operational_status.reconciliation_summary,
        candidate_selection_summary=operational_status.candidate_selection_summary,
        operator_alert=operational_status.operator_alert,
        default_symbol=settings_row.default_symbol.upper(),
        tracked_symbols=get_effective_symbols(settings_row),
        default_timeframe=settings_row.default_timeframe,
        exchange_sync_interval_seconds=settings_row.exchange_sync_interval_seconds,
        market_refresh_interval_minutes=settings_row.market_refresh_interval_minutes,
        position_management_interval_seconds=settings_row.position_management_interval_seconds,
        symbol_cadence_overrides=symbol_cadence_overrides,
        symbol_effective_cadences=symbol_effective_cadences,
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
        partial_tp_rr=settings_row.partial_tp_rr,
        partial_tp_size_pct=settings_row.partial_tp_size_pct,
        move_stop_to_be_rr=settings_row.move_stop_to_be_rr,
        time_stop_enabled=settings_row.time_stop_enabled,
        time_stop_minutes=settings_row.time_stop_minutes,
        time_stop_profit_floor=settings_row.time_stop_profit_floor,
        holding_edge_decay_enabled=settings_row.holding_edge_decay_enabled,
        reduce_on_regime_shift_enabled=settings_row.reduce_on_regime_shift_enabled,
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


def serialize_settings_view(settings_row: Setting) -> dict[str, object]:
    defaults = get_settings()
    credentials = get_runtime_credentials(settings_row, defaults=defaults)
    current_session = object_session(settings_row)
    rollout_mode = get_rollout_mode(settings_row)
    exchange_submit_allowed = rollout_mode_allows_exchange_submit(settings_row)
    mode = "live_guarded"
    if settings_row.trading_paused:
        mode = "paused"
    elif is_live_execution_ready(settings_row, defaults=defaults) and exchange_submit_allowed:
        mode = "live_ready"

    effective_max_leverage = min(settings_row.max_leverage, DISPLAY_MAX_LEVERAGE)
    effective_max_risk_per_trade = min(settings_row.max_risk_per_trade, DISPLAY_MAX_RISK_PER_TRADE)
    effective_max_daily_loss = min(settings_row.max_daily_loss, DISPLAY_MAX_DAILY_LOSS)
    runtime_state = summarize_runtime_state(settings_row)
    latest_pnl = get_latest_pnl_snapshot(current_session, settings_row) if current_session is not None else None
    account_sync_summary = (
        _build_account_sync_summary(current_session, settings_row, latest_pnl)
        if latest_pnl is not None
        else _build_unknown_account_sync_summary(settings_row)
    )
    sync_freshness_summary = build_sync_freshness_summary(settings_row)
    market_freshness_summary = _build_market_freshness_summary(current_session, settings_row)
    market_context_summary = _build_market_context_summary(current_session, settings_row)
    adaptive_signal_context = build_adaptive_signal_context(
        current_session,
        enabled=settings_row.adaptive_signal_enabled,
        symbol=settings_row.default_symbol.upper(),
        timeframe=settings_row.default_timeframe,
        regime=str(market_context_summary.get("primary_regime", "unknown")),
        settings_row=settings_row,
    )
    latest_decision_rationale_codes: list[str] = []
    latest_decision_code: str | None = None
    latest_trading_run: AgentRun | None = None
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
    current_risk_allowed, current_cycle_blocked_reasons = get_latest_risk_gate_status(current_session)
    adaptive_signal_summary = summarize_adaptive_signal_state(
        adaptive_signal_context,
        latest_rationale_codes=latest_decision_rationale_codes,
        latest_decision=latest_decision_code,
        latest_entry_mode=(
            str(latest_trading_run.output_payload.get("entry_mode"))
            if current_session is not None
            and latest_trading_run is not None
            and isinstance(latest_trading_run.output_payload, dict)
            and latest_trading_run.output_payload.get("entry_mode") not in {None, ""}
            else None
        ),
    )
    position_management_summary = _build_position_management_summary(current_session, settings_row)
    event_operator_control = build_event_operator_control_payload(
        session=current_session,
        settings_row=settings_row,
        symbol=settings_row.default_symbol.upper(),
        timeframe=settings_row.default_timeframe,
    )
    operational_status = build_operational_status_payload(
        settings_row,
        session=current_session,
        defaults=defaults,
        runtime_state=runtime_state,
        blocked_reasons=current_cycle_blocked_reasons,
        latest_blocked_reasons=current_cycle_blocked_reasons,
        risk_allowed=current_risk_allowed,
        account_sync_summary=account_sync_summary,
        sync_freshness_summary=sync_freshness_summary,
        market_freshness_summary=market_freshness_summary,
    )
    symbol_cadence_overrides = [
        SymbolCadenceOverride(**item) for item in get_symbol_cadence_overrides(settings_row)
    ]
    payload = AppSettingsViewResponse(
        id=settings_row.id,
        can_enter_new_position=operational_status.can_enter_new_position,
        blocked_reasons=operational_status.blocked_reasons,
        live_trading_enabled=settings_row.live_trading_enabled,
        rollout_mode=rollout_mode,
        exchange_submit_allowed=exchange_submit_allowed,
        limited_live_max_notional=get_limited_live_max_notional(settings_row),
        live_trading_env_enabled=defaults.live_trading_env_enabled,
        manual_live_approval=settings_row.manual_live_approval,
        live_execution_armed=is_live_execution_armed(settings_row),
        live_execution_armed_until=settings_row.live_execution_armed_until,
        live_approval_window_minutes=settings_row.live_approval_window_minutes,
        live_execution_ready=operational_status.live_execution_ready,
        trading_paused=operational_status.trading_paused,
        guard_mode_reason_category=operational_status.guard_mode_reason_category,
        guard_mode_reason_code=operational_status.guard_mode_reason_code,
        guard_mode_reason_message=operational_status.guard_mode_reason_message,
        pause_reason_code=operational_status.pause_reason_code,
        pause_origin=operational_status.pause_origin,
        pause_reason_detail=settings_row.pause_reason_detail,
        pause_triggered_at=operational_status.pause_triggered_at,
        auto_resume_after=operational_status.auto_resume_after,
        auto_resume_whitelisted=pause_reason_allows_auto_resume(settings_row.pause_reason_code),
        auto_resume_eligible=operational_status.auto_resume_eligible,
        auto_resume_status=operational_status.auto_resume_status,
        auto_resume_last_blockers=operational_status.auto_resume_last_blockers,
        latest_blocked_reasons=operational_status.latest_blocked_reasons,
        control_status_summary=operational_status.control_status_summary,
        reconciliation_summary=operational_status.reconciliation_summary,
        operator_alert=operational_status.operator_alert,
        mode=mode,
        operating_state=operational_status.operating_state,
        protection_recovery_status=operational_status.protection_recovery_status,
        protection_recovery_active=operational_status.protection_recovery_active,
        protection_recovery_failure_count=operational_status.protection_recovery_failure_count,
        missing_protection_symbols=operational_status.missing_protection_symbols,
        missing_protection_items=operational_status.missing_protection_items,
        pause_severity=operational_status.pause_severity,
        pause_recovery_class=operational_status.pause_recovery_class,
        default_symbol=settings_row.default_symbol.upper(),
        tracked_symbols=get_effective_symbols(settings_row),
        default_timeframe=settings_row.default_timeframe,
        event_operator_control=event_operator_control,
        exchange_sync_interval_seconds=settings_row.exchange_sync_interval_seconds,
        market_refresh_interval_minutes=settings_row.market_refresh_interval_minutes,
        position_management_interval_seconds=settings_row.position_management_interval_seconds,
        symbol_cadence_overrides=symbol_cadence_overrides,
        max_leverage=effective_max_leverage,
        max_risk_per_trade=effective_max_risk_per_trade,
        max_daily_loss=effective_max_daily_loss,
        max_consecutive_losses=settings_row.max_consecutive_losses,
        stale_market_seconds=settings_row.stale_market_seconds,
        slippage_threshold_pct=settings_row.slippage_threshold_pct,
        adaptive_signal_enabled=settings_row.adaptive_signal_enabled,
        position_management_enabled=settings_row.position_management_enabled,
        break_even_enabled=settings_row.break_even_enabled,
        atr_trailing_stop_enabled=settings_row.atr_trailing_stop_enabled,
        partial_take_profit_enabled=settings_row.partial_take_profit_enabled,
        holding_edge_decay_enabled=settings_row.holding_edge_decay_enabled,
        reduce_on_regime_shift_enabled=settings_row.reduce_on_regime_shift_enabled,
        adaptive_signal_summary=adaptive_signal_summary,
        position_management_summary=position_management_summary,
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
        openai_api_key_configured=bool(credentials.openai_api_key),
        binance_api_key_configured=bool(credentials.binance_api_key),
        binance_api_secret_configured=bool(credentials.binance_api_secret),
    )
    return payload.model_dump(mode="json")


def serialize_settings_cadences(settings_row: Setting) -> dict[str, object]:
    current_session = object_session(settings_row)
    payload = AppSettingsCadenceResponse(
        items=build_symbol_effective_cadences(current_session, settings_row),
    )
    return payload.model_dump(mode="json")


def serialize_settings_ai_usage(settings_row: Setting) -> dict[str, object]:
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
    payload = AppSettingsAIUsageResponse(
        **usage_metrics,
        manual_ai_guard_minutes=manual_ai_guard_minutes(settings_row),
    )
    return payload.model_dump(mode="json")


def serialize_settings_runtime_summary(settings_row: Setting) -> dict[str, object]:
    defaults = get_settings()
    current_session = object_session(settings_row)

    exchange_submit_allowed = rollout_mode_allows_exchange_submit(settings_row)
    mode = "live_guarded"
    if settings_row.trading_paused:
        mode = "paused"
    elif is_live_execution_ready(settings_row, defaults=defaults) and exchange_submit_allowed:
        mode = "live_ready"

    exposure_limits = get_exposure_limits(settings_row)
    runtime_state = summarize_runtime_state(settings_row)
    latest_pnl = get_latest_pnl_snapshot(current_session, settings_row) if current_session is not None else None
    pnl_summary = _build_pnl_summary(settings_row, latest_pnl) if latest_pnl is not None else _build_unknown_pnl_summary()
    account_sync_summary = (
        _build_account_sync_summary(current_session, settings_row, latest_pnl)
        if latest_pnl is not None
        else _build_unknown_account_sync_summary(settings_row)
    )
    sync_freshness_summary = build_sync_freshness_summary(settings_row)
    market_freshness_summary = _build_market_freshness_summary(current_session, settings_row)
    if current_session is not None and pnl_summary.get("equity") is not None:
        from trading_mvp.services.risk import build_current_exposure_summary

        exposure_summary = build_current_exposure_summary(
            current_session,
            settings_row,
            equity=_coerce_float(pnl_summary.get("equity"), 0.0),
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
        settings_row=settings_row,
    )
    latest_decision_rationale_codes: list[str] = []
    latest_decision_code: str | None = None
    latest_trading_run: AgentRun | None = None
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
    current_risk_allowed, current_cycle_blocked_reasons = get_latest_risk_gate_status(current_session)
    adaptive_signal_summary = summarize_adaptive_signal_state(
        adaptive_signal_context,
        latest_rationale_codes=latest_decision_rationale_codes,
        latest_decision=latest_decision_code,
        latest_entry_mode=(
            str(latest_trading_run.output_payload.get("entry_mode"))
            if current_session is not None
            and latest_trading_run is not None
            and isinstance(latest_trading_run.output_payload, dict)
            and latest_trading_run.output_payload.get("entry_mode") not in {None, ""}
            else None
        ),
    )
    execution_policy_summary = summarize_execution_policy(settings_row)
    position_management_summary = _build_position_management_summary(current_session, settings_row)
    event_operator_control = build_event_operator_control_payload(
        session=current_session,
        settings_row=settings_row,
        symbol=settings_row.default_symbol.upper(),
        timeframe=settings_row.default_timeframe,
    )
    operational_status = build_operational_status_payload(
        settings_row,
        session=current_session,
        defaults=defaults,
        runtime_state=runtime_state,
        blocked_reasons=current_cycle_blocked_reasons,
        latest_blocked_reasons=current_cycle_blocked_reasons,
        risk_allowed=current_risk_allowed,
        account_sync_summary=account_sync_summary,
        sync_freshness_summary=sync_freshness_summary,
        market_freshness_summary=market_freshness_summary,
    )
    return {
        "mode": mode,
        "pnl_summary": pnl_summary,
        "account_sync_summary": account_sync_summary,
        "sync_freshness_summary": sync_freshness_summary,
        "market_freshness_summary": market_freshness_summary,
        "exposure_summary": exposure_summary,
        "execution_policy_summary": execution_policy_summary,
        "market_context_summary": market_context_summary,
        "adaptive_protection_summary": adaptive_protection_summary,
        "adaptive_signal_summary": adaptive_signal_summary,
        "position_management_summary": position_management_summary,
        "event_operator_control": event_operator_control.model_dump(mode="json"),
        "operational_status": operational_status.model_dump(mode="json"),
    }


def update_settings(session: Session, payload: AppSettingsUpdateRequest) -> Setting:
    defaults = get_settings()
    row = get_or_create_settings(session)
    tracked_symbols = normalize_symbols(payload.tracked_symbols)
    if payload.default_symbol.upper() not in tracked_symbols:
        tracked_symbols.insert(0, payload.default_symbol.upper())

    rollout_mode = (
        normalize_rollout_mode(payload.rollout_mode)
        if payload.rollout_mode is not None
        else ("full_live" if payload.live_trading_enabled else "paper")
    )
    row.rollout_mode = rollout_mode
    row.live_trading_enabled = rollout_mode != "paper"
    row.limited_live_max_notional = max(float(payload.limited_live_max_notional), 0.01)
    row.manual_live_approval = payload.manual_live_approval
    row.live_approval_window_minutes = payload.live_approval_window_minutes
    row.default_symbol = payload.default_symbol.upper()
    row.tracked_symbols = tracked_symbols
    row.default_timeframe = payload.default_timeframe
    row.exchange_sync_interval_seconds = payload.exchange_sync_interval_seconds
    row.market_refresh_interval_minutes = payload.market_refresh_interval_minutes
    row.position_management_interval_seconds = payload.position_management_interval_seconds
    row.symbol_cadence_overrides = normalize_symbol_cadence_overrides(payload.symbol_cadence_overrides)
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
    row.partial_tp_rr = payload.partial_tp_rr
    row.partial_tp_size_pct = payload.partial_tp_size_pct
    row.move_stop_to_be_rr = payload.move_stop_to_be_rr
    row.time_stop_enabled = payload.time_stop_enabled
    row.time_stop_minutes = payload.time_stop_minutes
    row.time_stop_profit_floor = payload.time_stop_profit_floor
    row.holding_edge_decay_enabled = payload.holding_edge_decay_enabled
    row.reduce_on_regime_shift_enabled = payload.reduce_on_regime_shift_enabled
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
        for key in PRESERVED_SETTINGS_DETAIL_KEYS
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
