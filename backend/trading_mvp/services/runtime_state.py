from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from trading_mvp.models import Setting
from trading_mvp.time_utils import utcnow_naive

OperatingState = Literal[
    "TRADABLE",
    "PROTECTION_REQUIRED",
    "DEGRADED_MANAGE_ONLY",
    "EMERGENCY_EXIT",
    "PAUSED",
]

TRADABLE_STATE: OperatingState = "TRADABLE"
PROTECTION_REQUIRED_STATE: OperatingState = "PROTECTION_REQUIRED"
DEGRADED_MANAGE_ONLY_STATE: OperatingState = "DEGRADED_MANAGE_ONLY"
EMERGENCY_EXIT_STATE: OperatingState = "EMERGENCY_EXIT"
PAUSED_STATE: OperatingState = "PAUSED"

OPERATING_STATES: set[str] = {
    TRADABLE_STATE,
    PROTECTION_REQUIRED_STATE,
    DEGRADED_MANAGE_ONLY_STATE,
    EMERGENCY_EXIT_STATE,
    PAUSED_STATE,
}
ENTRY_BLOCKING_OPERATING_STATES: set[str] = {
    PROTECTION_REQUIRED_STATE,
    DEGRADED_MANAGE_ONLY_STATE,
    EMERGENCY_EXIT_STATE,
    PAUSED_STATE,
}
PROTECTION_RECOVERY_THRESHOLD = 2
SYNC_STATE_DETAIL_KEY = "exchange_sync"
SYNC_SCOPES: tuple[str, ...] = (
    "account",
    "positions",
    "open_orders",
    "protective_orders",
)


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_symbol_map(value: object) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    items: dict[str, dict[str, Any]] = {}
    for key, item in value.items():
        if isinstance(item, dict):
            items[str(key)] = dict(item)
    return items


def _coerce_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _coerce_int(value: object, default: int) -> int:
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


def _serialize_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def normalize_operating_state(value: object) -> OperatingState:
    if isinstance(value, str) and value in OPERATING_STATES:
        return value  # type: ignore[return-value]
    return TRADABLE_STATE


def get_runtime_detail(settings_row: Setting) -> dict[str, Any]:
    return _as_dict(settings_row.pause_reason_detail)


def get_sync_state_detail(settings_row: Setting) -> dict[str, dict[str, Any]]:
    detail = get_runtime_detail(settings_row)
    sync_detail = _as_dict(detail.get(SYNC_STATE_DETAIL_KEY))
    normalized: dict[str, dict[str, Any]] = {}
    for scope in SYNC_SCOPES:
        scope_payload = _as_dict(sync_detail.get(scope))
        normalized[scope] = scope_payload
    return normalized


def _default_sync_stale_after_seconds(settings_row: Setting, scope: str) -> int:
    base_seconds = max(60, int(settings_row.decision_cycle_interval_minutes) * 120)
    if scope == "protective_orders":
        return max(60, int(settings_row.decision_cycle_interval_minutes) * 90)
    return base_seconds


def mark_sync_success(
    settings_row: Setting,
    *,
    scope: str,
    synced_at: datetime | None = None,
    detail: dict[str, Any] | None = None,
    stale_after_seconds: int | None = None,
    status: str = "synced",
) -> None:
    if scope not in SYNC_SCOPES:
        raise ValueError(f"Unsupported sync scope: {scope}")
    runtime_detail = get_runtime_detail(settings_row)
    sync_detail = get_sync_state_detail(settings_row)
    now = synced_at or utcnow_naive()
    scope_detail = {
        **sync_detail.get(scope, {}),
        "status": status,
        "last_sync_at": now.isoformat(),
        "last_failure_at": None,
        "last_failure_reason": None,
        "consecutive_failures": 0,
        "stale_after_seconds": stale_after_seconds
        if stale_after_seconds is not None
        else _default_sync_stale_after_seconds(settings_row, scope),
    }
    if detail:
        scope_detail.update(detail)
    sync_detail[scope] = scope_detail
    runtime_detail[SYNC_STATE_DETAIL_KEY] = sync_detail
    settings_row.pause_reason_detail = runtime_detail


def mark_sync_issue(
    settings_row: Setting,
    *,
    scope: str,
    status: Literal["failed", "incomplete"],
    reason_code: str,
    observed_at: datetime | None = None,
    detail: dict[str, Any] | None = None,
    stale_after_seconds: int | None = None,
) -> None:
    if scope not in SYNC_SCOPES:
        raise ValueError(f"Unsupported sync scope: {scope}")
    runtime_detail = get_runtime_detail(settings_row)
    sync_detail = get_sync_state_detail(settings_row)
    now = observed_at or utcnow_naive()
    scope_detail = {
        **sync_detail.get(scope, {}),
        "status": status,
        "last_failure_at": now.isoformat(),
        "last_failure_reason": reason_code,
        "consecutive_failures": _coerce_int(sync_detail.get(scope, {}).get("consecutive_failures"), 0) + 1,
        "stale_after_seconds": stale_after_seconds
        if stale_after_seconds is not None
        else _default_sync_stale_after_seconds(settings_row, scope),
    }
    if detail:
        scope_detail.update(detail)
    sync_detail[scope] = scope_detail
    runtime_detail[SYNC_STATE_DETAIL_KEY] = sync_detail
    settings_row.pause_reason_detail = runtime_detail


def get_sync_scope_status(
    settings_row: Setting,
    *,
    scope: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    if scope not in SYNC_SCOPES:
        raise ValueError(f"Unsupported sync scope: {scope}")
    scope_detail = get_sync_state_detail(settings_row).get(scope, {})
    current_time = now or utcnow_naive()
    last_sync_at = _coerce_datetime(scope_detail.get("last_sync_at"))
    last_failure_at = _coerce_datetime(scope_detail.get("last_failure_at"))
    stale_after_seconds = max(
        _coerce_int(scope_detail.get("stale_after_seconds"), _default_sync_stale_after_seconds(settings_row, scope)),
        1,
    )
    freshness_seconds = (
        max(int((current_time - last_sync_at).total_seconds()), 0)
        if last_sync_at is not None
        else None
    )
    status = str(scope_detail.get("status") or ("unknown" if last_sync_at is None else "synced"))
    stale = last_sync_at is None or (
        freshness_seconds is not None and freshness_seconds > stale_after_seconds
    )
    incomplete = status == "incomplete"
    return {
        "scope": scope,
        "status": status,
        "last_sync_at": _serialize_datetime(last_sync_at),
        "last_failure_at": _serialize_datetime(last_failure_at),
        "last_failure_reason": (
            str(scope_detail.get("last_failure_reason"))
            if scope_detail.get("last_failure_reason") not in {None, ""}
            else None
        ),
        "consecutive_failures": _coerce_int(scope_detail.get("consecutive_failures"), 0),
        "freshness_seconds": freshness_seconds,
        "stale_after_seconds": stale_after_seconds,
        "stale": stale,
        "incomplete": incomplete,
    }


def build_sync_freshness_summary(
    settings_row: Setting,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_time = now or utcnow_naive()
    scopes = {
        scope: get_sync_scope_status(settings_row, scope=scope, now=current_time)
        for scope in SYNC_SCOPES
    }
    return {
        "generated_at": current_time.isoformat(),
        "account": scopes["account"],
        "positions": scopes["positions"],
        "open_orders": scopes["open_orders"],
        "protective_orders": scopes["protective_orders"],
    }


def get_protection_recovery_detail(settings_row: Setting) -> dict[str, Any]:
    detail = get_runtime_detail(settings_row)
    recovery = _as_dict(detail.get("protection_recovery"))
    recovery["symbol_states"] = _as_symbol_map(recovery.get("symbol_states"))
    recovery["missing_symbols"] = [str(item) for item in recovery.get("missing_symbols", []) if item]
    recovery["missing_items"] = {
        str(key): [str(item) for item in value]
        for key, value in _as_dict(recovery.get("missing_items")).items()
        if isinstance(value, list)
    }
    return recovery


def get_operating_state(settings_row: Setting) -> OperatingState:
    if settings_row.trading_paused:
        return PAUSED_STATE
    detail = get_runtime_detail(settings_row)
    return normalize_operating_state(detail.get("operating_state"))


def summarize_runtime_state(settings_row: Setting) -> dict[str, Any]:
    recovery = get_protection_recovery_detail(settings_row)
    sync_freshness_summary = build_sync_freshness_summary(settings_row)
    symbol_states = _as_symbol_map(recovery.get("symbol_states"))
    failure_count = 0
    for item in symbol_states.values():
        try:
            failure_count = max(failure_count, int(item.get("failure_count", 0)))
        except (TypeError, ValueError):
            continue

    return {
        "operating_state": get_operating_state(settings_row),
        "protection_recovery_status": str(recovery.get("status", "idle")),
        "protection_recovery_active": bool(recovery.get("auto_recovery_active", False)),
        "protection_recovery_failure_count": failure_count,
        "missing_protection_symbols": [str(item) for item in recovery.get("missing_symbols", []) if item],
        "missing_protection_items": {
            str(key): [str(item) for item in value]
            for key, value in recovery.get("missing_items", {}).items()
            if isinstance(value, list)
        },
        "protection_recovery_symbols": symbol_states,
        "protection_recovery_last_error": (
            str(recovery.get("last_error"))
            if recovery.get("last_error") not in {None, ""}
            else None
        ),
        "protection_recovery_last_transition_at": (
            str(recovery.get("last_transition_at"))
            if recovery.get("last_transition_at") not in {None, ""}
            else None
        ),
        "last_account_sync_at": sync_freshness_summary["account"]["last_sync_at"],
        "last_positions_sync_at": sync_freshness_summary["positions"]["last_sync_at"],
        "last_open_orders_sync_at": sync_freshness_summary["open_orders"]["last_sync_at"],
        "last_protective_orders_sync_at": sync_freshness_summary["protective_orders"]["last_sync_at"],
        "sync_freshness_summary": sync_freshness_summary,
    }
