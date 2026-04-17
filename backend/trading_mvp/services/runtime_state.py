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
EXECUTION_GUARD_DETAIL_KEY = "execution_guard"
USER_STREAM_DETAIL_KEY = "user_stream"
RECONCILIATION_DETAIL_KEY = "reconciliation"
CANDIDATE_SELECTION_DETAIL_KEY = "candidate_selection"
MAX_EXECUTION_DEDUPE_RECORDS = 128
SYNC_SCOPES: tuple[str, ...] = (
    "account",
    "positions",
    "open_orders",
    "protective_orders",
)
SYNC_SCOPE_STATUSES = {"unknown", "synced", "failed", "incomplete", "skipped"}


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


def _as_record_map(value: object) -> dict[str, dict[str, Any]]:
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


def _coerce_float(value: object, default: float) -> float:
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


def _prune_execution_dedupe_records(
    records: dict[str, dict[str, Any]],
    *,
    limit: int = MAX_EXECUTION_DEDUPE_RECORDS,
) -> dict[str, dict[str, Any]]:
    if len(records) <= limit:
        return records
    ordered = sorted(
        records.items(),
        key=lambda item: str(
            item[1].get("completed_at")
            or item[1].get("recorded_at")
            or item[1].get("locked_at")
            or ""
        ),
        reverse=True,
    )
    return dict(ordered[:limit])


def get_execution_guard_detail(settings_row: Setting) -> dict[str, dict[str, dict[str, Any]]]:
    detail = get_runtime_detail(settings_row)
    guard = _as_dict(detail.get(EXECUTION_GUARD_DETAIL_KEY))
    return {
        "symbol_locks": _as_symbol_map(guard.get("symbol_locks")),
        "dedupe_records": _as_record_map(guard.get("dedupe_records")),
        "unresolved_submission_guards": _as_record_map(guard.get("unresolved_submission_guards")),
    }


def get_user_stream_detail(settings_row: Setting) -> dict[str, Any]:
    detail = get_runtime_detail(settings_row)
    payload = _as_dict(detail.get(USER_STREAM_DETAIL_KEY))
    return {
        "status": str(payload.get("status") or "idle"),
        "source": str(payload.get("source") or "binance_futures_user_stream"),
        "listen_key": str(payload.get("listen_key") or "") or None,
        "listen_key_created_at": _serialize_datetime(_coerce_datetime(payload.get("listen_key_created_at"))),
        "listen_key_refreshed_at": _serialize_datetime(_coerce_datetime(payload.get("listen_key_refreshed_at"))),
        "last_keepalive_at": _serialize_datetime(_coerce_datetime(payload.get("last_keepalive_at"))),
        "last_connected_at": _serialize_datetime(_coerce_datetime(payload.get("last_connected_at"))),
        "last_disconnected_at": _serialize_datetime(_coerce_datetime(payload.get("last_disconnected_at"))),
        "connection_attempted_at": _serialize_datetime(_coerce_datetime(payload.get("connection_attempted_at"))),
        "last_event_at": _serialize_datetime(_coerce_datetime(payload.get("last_event_at"))),
        "last_event_type": str(payload.get("last_event_type") or "") or None,
        "last_error": str(payload.get("last_error") or "") or None,
        "reconnect_count": _coerce_int(payload.get("reconnect_count"), 0),
        "heartbeat_ok": bool(payload.get("heartbeat_ok", False)),
        "stream_source": str(payload.get("stream_source") or "user_stream"),
        "next_retry_at": _serialize_datetime(_coerce_datetime(payload.get("next_retry_at"))),
        "backoff_seconds": _coerce_float(payload.get("backoff_seconds"), 0.0),
    }


def set_user_stream_detail(
    settings_row: Setting,
    *,
    status: str | None = None,
    source: str | None = None,
    listen_key: str | None = None,
    listen_key_created_at: datetime | None = None,
    listen_key_refreshed_at: datetime | None = None,
    last_keepalive_at: datetime | None = None,
    last_connected_at: datetime | None = None,
    last_disconnected_at: datetime | None = None,
    connection_attempted_at: datetime | None = None,
    last_event_at: datetime | None = None,
    last_event_type: str | None = None,
    last_error: str | None = None,
    reconnect_count: int | None = None,
    heartbeat_ok: bool | None = None,
    stream_source: str | None = None,
    next_retry_at: datetime | None = None,
    backoff_seconds: float | None = None,
) -> None:
    runtime_detail = get_runtime_detail(settings_row)
    payload = get_user_stream_detail(settings_row)
    if status is not None:
        payload["status"] = status
    if source is not None:
        payload["source"] = source
    if listen_key is not None:
        payload["listen_key"] = listen_key
    if listen_key_created_at is not None:
        payload["listen_key_created_at"] = listen_key_created_at.isoformat()
    if listen_key_refreshed_at is not None:
        payload["listen_key_refreshed_at"] = listen_key_refreshed_at.isoformat()
    if last_keepalive_at is not None:
        payload["last_keepalive_at"] = last_keepalive_at.isoformat()
    if last_connected_at is not None:
        payload["last_connected_at"] = last_connected_at.isoformat()
    if last_disconnected_at is not None:
        payload["last_disconnected_at"] = last_disconnected_at.isoformat()
    if connection_attempted_at is not None:
        payload["connection_attempted_at"] = connection_attempted_at.isoformat()
    if last_event_at is not None:
        payload["last_event_at"] = last_event_at.isoformat()
    if last_event_type is not None:
        payload["last_event_type"] = last_event_type
    if last_error is not None:
        payload["last_error"] = last_error
    if reconnect_count is not None:
        payload["reconnect_count"] = reconnect_count
    if heartbeat_ok is not None:
        payload["heartbeat_ok"] = heartbeat_ok
    if stream_source is not None:
        payload["stream_source"] = stream_source
    if next_retry_at is not None:
        payload["next_retry_at"] = next_retry_at.isoformat()
    if backoff_seconds is not None:
        payload["backoff_seconds"] = backoff_seconds
    runtime_detail[USER_STREAM_DETAIL_KEY] = payload
    settings_row.pause_reason_detail = runtime_detail


def replace_user_stream_detail(settings_row: Setting, payload: dict[str, Any]) -> None:
    runtime_detail = get_runtime_detail(settings_row)
    runtime_detail[USER_STREAM_DETAIL_KEY] = dict(payload)
    settings_row.pause_reason_detail = runtime_detail


def should_use_rest_order_reconciliation(
    settings_row: Setting,
    *,
    active_order_count: int,
    now: datetime | None = None,
) -> tuple[bool, str]:
    if active_order_count <= 0:
        return False, "NO_ACTIVE_LIVE_ORDERS"
    summary = get_user_stream_detail(settings_row)
    if str(summary.get("status") or "") != "connected":
        return True, "USER_STREAM_UNAVAILABLE"
    if not bool(summary.get("heartbeat_ok", False)):
        return True, "USER_STREAM_HEARTBEAT_UNHEALTHY"
    if str(summary.get("stream_source") or "") != "user_stream":
        return True, "USER_STREAM_FALLBACK_ACTIVE"
    reference_time = (
        _coerce_datetime(summary.get("last_event_at"))
        or _coerce_datetime(summary.get("last_connected_at"))
        or _coerce_datetime(summary.get("listen_key_refreshed_at"))
    )
    if reference_time is None:
        return True, "USER_STREAM_ACTIVITY_MISSING"
    resolved_now = now or utcnow_naive()
    freshness_seconds = max(int(settings_row.exchange_sync_interval_seconds or 0) * 2, 120)
    if (resolved_now - reference_time).total_seconds() > freshness_seconds:
        return True, "USER_STREAM_STALE"
    return False, "USER_STREAM_PRIMARY"


def get_reconciliation_detail(settings_row: Setting) -> dict[str, Any]:
    detail = get_runtime_detail(settings_row)
    payload = _as_dict(detail.get(RECONCILIATION_DETAIL_KEY))
    guarded_symbols = [str(item).upper() for item in payload.get("guarded_symbols", []) if item]
    return {
        "status": str(payload.get("status") or "idle"),
        "source": str(payload.get("source") or "rest_polling"),
        "last_reconciled_at": _serialize_datetime(_coerce_datetime(payload.get("last_reconciled_at"))),
        "last_success_at": _serialize_datetime(_coerce_datetime(payload.get("last_success_at"))),
        "last_error": str(payload.get("last_error") or "") or None,
        "last_symbol": str(payload.get("last_symbol") or "") or None,
        "stream_fallback_active": bool(payload.get("stream_fallback_active", False)),
        "reconcile_source": str(payload.get("reconcile_source") or "rest_polling"),
        "position_mode": str(payload.get("position_mode") or "unknown"),
        "position_mode_source": str(payload.get("position_mode_source") or "unknown"),
        "position_mode_checked_at": _serialize_datetime(_coerce_datetime(payload.get("position_mode_checked_at"))),
        "mode_guard_active": bool(payload.get("mode_guard_active", False)),
        "mode_guard_reason_code": str(payload.get("mode_guard_reason_code") or "") or None,
        "mode_guard_message": str(payload.get("mode_guard_message") or "") or None,
        "enabled_symbols": [str(item).upper() for item in payload.get("enabled_symbols", []) if item],
        "guarded_symbols": guarded_symbols,
        "guarded_symbols_count": len(guarded_symbols),
        "symbol_states": _as_symbol_map(payload.get("symbol_states")),
        "unresolved_submission_badge": bool(payload.get("unresolved_submission_badge", False)),
        "unresolved_submission_count": _coerce_int(payload.get("unresolved_submission_count"), 0),
        "unresolved_submission_symbols": [
            str(item).upper()
            for item in payload.get("unresolved_submission_symbols", [])
            if item
        ],
        "unresolved_submissions": [
            dict(item)
            for item in payload.get("unresolved_submissions", [])
            if isinstance(item, dict)
        ],
    }


def set_reconciliation_detail(
    settings_row: Setting,
    *,
    status: str | None = None,
    source: str | None = None,
    last_reconciled_at: datetime | None = None,
    last_success_at: datetime | None = None,
    last_error: str | None = None,
    last_symbol: str | None = None,
    stream_fallback_active: bool | None = None,
    reconcile_source: str | None = None,
    position_mode: str | None = None,
    position_mode_source: str | None = None,
    position_mode_checked_at: datetime | None = None,
    mode_guard_active: bool | None = None,
    mode_guard_reason_code: str | None = None,
    mode_guard_message: str | None = None,
    enabled_symbols: list[str] | None = None,
    guarded_symbols: list[str] | None = None,
    symbol_states: dict[str, dict[str, Any]] | None = None,
    unresolved_submission_badge: bool | None = None,
    unresolved_submission_count: int | None = None,
    unresolved_submission_symbols: list[str] | None = None,
    unresolved_submissions: list[dict[str, Any]] | None = None,
) -> None:
    runtime_detail = get_runtime_detail(settings_row)
    payload = get_reconciliation_detail(settings_row)
    if status is not None:
        payload["status"] = status
    if source is not None:
        payload["source"] = source
    if last_reconciled_at is not None:
        payload["last_reconciled_at"] = last_reconciled_at.isoformat()
    if last_success_at is not None:
        payload["last_success_at"] = last_success_at.isoformat()
    if last_error is not None:
        payload["last_error"] = last_error
    if last_symbol is not None:
        payload["last_symbol"] = last_symbol
    if stream_fallback_active is not None:
        payload["stream_fallback_active"] = stream_fallback_active
    if reconcile_source is not None:
        payload["reconcile_source"] = reconcile_source
    if position_mode is not None:
        payload["position_mode"] = position_mode
    if position_mode_source is not None:
        payload["position_mode_source"] = position_mode_source
    if position_mode_checked_at is not None:
        payload["position_mode_checked_at"] = position_mode_checked_at.isoformat()
    if mode_guard_active is not None:
        payload["mode_guard_active"] = mode_guard_active
    if mode_guard_reason_code is not None:
        payload["mode_guard_reason_code"] = mode_guard_reason_code
    if mode_guard_message is not None:
        payload["mode_guard_message"] = mode_guard_message
    if enabled_symbols is not None:
        payload["enabled_symbols"] = [str(item).upper() for item in enabled_symbols if item]
    if guarded_symbols is not None:
        payload["guarded_symbols"] = [str(item).upper() for item in guarded_symbols if item]
    if symbol_states is not None:
        payload["symbol_states"] = {
            str(key).upper(): dict(value)
            for key, value in symbol_states.items()
            if isinstance(value, dict)
        }
    if unresolved_submission_badge is not None:
        payload["unresolved_submission_badge"] = unresolved_submission_badge
    if unresolved_submission_count is not None:
        payload["unresolved_submission_count"] = max(int(unresolved_submission_count), 0)
    if unresolved_submission_symbols is not None:
        payload["unresolved_submission_symbols"] = [str(item).upper() for item in unresolved_submission_symbols if item]
    if unresolved_submissions is not None:
        payload["unresolved_submissions"] = [dict(item) for item in unresolved_submissions if isinstance(item, dict)]
    runtime_detail[RECONCILIATION_DETAIL_KEY] = payload
    settings_row.pause_reason_detail = runtime_detail


def get_reconciliation_blocking_reason_codes(settings_row: Setting) -> list[str]:
    summary = get_reconciliation_detail(settings_row)
    reason_codes: list[str] = []
    if bool(summary.get("mode_guard_active")):
        code = str(summary.get("mode_guard_reason_code") or "").strip()
        if code:
            reason_codes.append(code)
    if bool(summary.get("unresolved_submission_badge")):
        reason_codes.append("UNRESOLVED_SUBMISSION_GUARD_ACTIVE")
    return reason_codes


def get_candidate_selection_detail(settings_row: Setting) -> dict[str, Any]:
    detail = get_runtime_detail(settings_row)
    payload = _as_dict(detail.get(CANDIDATE_SELECTION_DETAIL_KEY))
    return {
        "generated_at": _serialize_datetime(_coerce_datetime(payload.get("generated_at"))),
        "mode": str(payload.get("mode") or "unavailable"),
        "max_selected": _coerce_int(payload.get("max_selected"), 0),
        "selected_symbols": [str(item) for item in payload.get("selected_symbols", []) if item],
        "skipped_symbols": [str(item) for item in payload.get("skipped_symbols", []) if item],
        "rankings": [dict(item) for item in payload.get("rankings", []) if isinstance(item, dict)],
    }


def set_candidate_selection_detail(
    settings_row: Setting,
    *,
    generated_at: datetime | None = None,
    mode: str | None = None,
    max_selected: int | None = None,
    selected_symbols: list[str] | None = None,
    skipped_symbols: list[str] | None = None,
    rankings: list[dict[str, Any]] | None = None,
) -> None:
    runtime_detail = get_runtime_detail(settings_row)
    payload = get_candidate_selection_detail(settings_row)
    if generated_at is not None:
        payload["generated_at"] = generated_at.isoformat()
    if mode is not None:
        payload["mode"] = mode
    if max_selected is not None:
        payload["max_selected"] = max_selected
    if selected_symbols is not None:
        payload["selected_symbols"] = [str(item).upper() for item in selected_symbols if item]
    if skipped_symbols is not None:
        payload["skipped_symbols"] = [str(item).upper() for item in skipped_symbols if item]
    if rankings is not None:
        payload["rankings"] = [dict(item) for item in rankings if isinstance(item, dict)]
    runtime_detail[CANDIDATE_SELECTION_DETAIL_KEY] = payload
    settings_row.pause_reason_detail = runtime_detail


def _write_execution_guard_detail(
    settings_row: Setting,
    *,
    symbol_locks: dict[str, dict[str, Any]],
    dedupe_records: dict[str, dict[str, Any]],
    unresolved_submission_guards: dict[str, dict[str, Any]],
) -> None:
    runtime_detail = get_runtime_detail(settings_row)
    runtime_detail[EXECUTION_GUARD_DETAIL_KEY] = {
        "symbol_locks": symbol_locks,
        "dedupe_records": _prune_execution_dedupe_records(dedupe_records),
        "unresolved_submission_guards": unresolved_submission_guards,
    }
    settings_row.pause_reason_detail = runtime_detail


def build_execution_dedupe_key(*, cycle_id: str, symbol: str, action: str) -> str:
    return f"{cycle_id}:{symbol.upper()}:{action}"


def get_execution_dedupe_record(
    settings_row: Setting,
    *,
    dedupe_key: str,
) -> dict[str, Any] | None:
    detail = get_execution_guard_detail(settings_row)
    return detail["dedupe_records"].get(dedupe_key)


def mark_execution_lock(
    settings_row: Setting,
    *,
    symbol: str,
    lock_token: str,
    dedupe_key: str,
    cycle_id: str,
    snapshot_id: int | None,
    action: str,
    locked_at: datetime | None = None,
) -> None:
    detail = get_execution_guard_detail(settings_row)
    symbol_locks = dict(detail["symbol_locks"])
    symbol_locks[symbol.upper()] = {
        "token": lock_token,
        "symbol": symbol.upper(),
        "dedupe_key": dedupe_key,
        "cycle_id": cycle_id,
        "snapshot_id": snapshot_id,
        "action": action,
        "locked_at": _serialize_datetime(locked_at or utcnow_naive()),
    }
    _write_execution_guard_detail(
        settings_row,
        symbol_locks=symbol_locks,
        dedupe_records=detail["dedupe_records"],
        unresolved_submission_guards=detail["unresolved_submission_guards"],
    )


def clear_execution_lock(
    settings_row: Setting,
    *,
    symbol: str,
    lock_token: str | None = None,
) -> None:
    detail = get_execution_guard_detail(settings_row)
    symbol_locks = dict(detail["symbol_locks"])
    current = symbol_locks.get(symbol.upper())
    if current is None:
        return
    if lock_token is not None and str(current.get("token") or "") != lock_token:
        return
    symbol_locks.pop(symbol.upper(), None)
    _write_execution_guard_detail(
        settings_row,
        symbol_locks=symbol_locks,
        dedupe_records=detail["dedupe_records"],
        unresolved_submission_guards=detail["unresolved_submission_guards"],
    )


def store_execution_dedupe_record(
    settings_row: Setting,
    *,
    dedupe_key: str,
    symbol: str,
    cycle_id: str,
    snapshot_id: int | None,
    action: str,
    status: str,
    result: dict[str, Any] | None = None,
    recorded_at: datetime | None = None,
) -> None:
    detail = get_execution_guard_detail(settings_row)
    dedupe_records = dict(detail["dedupe_records"])
    dedupe_records[dedupe_key] = {
        "dedupe_key": dedupe_key,
        "symbol": symbol.upper(),
        "cycle_id": cycle_id,
        "snapshot_id": snapshot_id,
        "action": action,
        "status": status,
        "completed_at": _serialize_datetime(recorded_at or utcnow_naive()),
        "result": dict(result) if isinstance(result, dict) else None,
    }
    _write_execution_guard_detail(
        settings_row,
        symbol_locks=detail["symbol_locks"],
        dedupe_records=dedupe_records,
        unresolved_submission_guards=detail["unresolved_submission_guards"],
    )


def _build_unresolved_submission_guard_key(*, symbol: str, action: str) -> str:
    return f"{symbol.upper()}:{action.lower()}"


def get_unresolved_submission_guard(
    settings_row: Setting,
    *,
    symbol: str,
    action: str,
) -> dict[str, Any] | None:
    detail = get_execution_guard_detail(settings_row)
    key = _build_unresolved_submission_guard_key(symbol=symbol, action=action)
    guard = detail["unresolved_submission_guards"].get(key)
    if not isinstance(guard, dict):
        return None
    return dict(guard)


def list_unresolved_submission_guards(
    settings_row: Setting,
    *,
    symbol: str | None = None,
) -> list[dict[str, Any]]:
    detail = get_execution_guard_detail(settings_row)
    rows = list(detail["unresolved_submission_guards"].values())
    if symbol is not None:
        symbol_upper = symbol.upper()
        rows = [item for item in rows if str(item.get("symbol") or "").upper() == symbol_upper]
    return [dict(item) for item in rows if isinstance(item, dict)]


def set_unresolved_submission_guard(
    settings_row: Setting,
    *,
    symbol: str,
    action: str,
    payload: dict[str, Any],
) -> None:
    detail = get_execution_guard_detail(settings_row)
    guards = dict(detail["unresolved_submission_guards"])
    key = _build_unresolved_submission_guard_key(symbol=symbol, action=action)
    guards[key] = {
        **dict(payload),
        "symbol": symbol.upper(),
        "action": action.lower(),
    }
    _write_execution_guard_detail(
        settings_row,
        symbol_locks=detail["symbol_locks"],
        dedupe_records=detail["dedupe_records"],
        unresolved_submission_guards=guards,
    )


def clear_unresolved_submission_guard(
    settings_row: Setting,
    *,
    symbol: str,
    action: str | None = None,
) -> None:
    detail = get_execution_guard_detail(settings_row)
    guards = dict(detail["unresolved_submission_guards"])
    symbol_upper = symbol.upper()
    if action is None:
        keys_to_delete = [
            key
            for key, value in guards.items()
            if isinstance(value, dict) and str(value.get("symbol") or "").upper() == symbol_upper
        ]
        for key in keys_to_delete:
            guards.pop(key, None)
    else:
        guards.pop(_build_unresolved_submission_guard_key(symbol=symbol_upper, action=action), None)
    _write_execution_guard_detail(
        settings_row,
        symbol_locks=detail["symbol_locks"],
        dedupe_records=detail["dedupe_records"],
        unresolved_submission_guards=guards,
    )


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
        "last_attempt_at": now.isoformat(),
        "last_attempt_status": "success",
        "last_sync_at": now.isoformat(),
        "last_failure_at": None,
        "last_failure_reason": None,
        "last_skip_at": None,
        "last_skip_reason": None,
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
        "last_attempt_at": now.isoformat(),
        "last_attempt_status": status,
        "last_failure_at": now.isoformat(),
        "last_failure_reason": reason_code,
        "last_skip_at": None,
        "last_skip_reason": None,
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


def mark_sync_skipped(
    settings_row: Setting,
    *,
    scope: str,
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
        "status": "skipped",
        "last_attempt_at": now.isoformat(),
        "last_attempt_status": "skipped",
        "last_skip_at": now.isoformat(),
        "last_skip_reason": reason_code,
        "stale_after_seconds": stale_after_seconds
        if stale_after_seconds is not None
        else _default_sync_stale_after_seconds(settings_row, scope),
    }
    if detail:
        scope_detail.update(detail)
    sync_detail[scope] = scope_detail
    runtime_detail[SYNC_STATE_DETAIL_KEY] = sync_detail
    settings_row.pause_reason_detail = runtime_detail


def _display_sync_status(raw_status: str, *, stale: bool) -> str:
    if raw_status == "unknown":
        return "unknown"
    if raw_status in {"failed", "incomplete", "skipped"}:
        return raw_status
    if stale:
        return "stale"
    return raw_status


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
    last_attempt_at = _coerce_datetime(scope_detail.get("last_attempt_at"))
    last_skip_at = _coerce_datetime(scope_detail.get("last_skip_at"))
    stale_after_seconds = max(
        _coerce_int(scope_detail.get("stale_after_seconds"), _default_sync_stale_after_seconds(settings_row, scope)),
        1,
    )
    freshness_seconds = (
        max(int((current_time - last_sync_at).total_seconds()), 0)
        if last_sync_at is not None
        else None
    )
    raw_status_value = str(scope_detail.get("status") or ("unknown" if last_sync_at is None else "synced"))
    raw_status = raw_status_value if raw_status_value in SYNC_SCOPE_STATUSES else ("unknown" if last_sync_at is None else "synced")
    stale = last_sync_at is None or (
        freshness_seconds is not None and freshness_seconds > stale_after_seconds
    )
    incomplete = raw_status == "incomplete"
    status = _display_sync_status(raw_status, stale=stale)
    return {
        "scope": scope,
        "status": status,
        "raw_status": raw_status,
        "sync_detail_status": raw_status_value if raw_status_value not in SYNC_SCOPE_STATUSES else None,
        "last_sync_at": _serialize_datetime(last_sync_at),
        "last_attempt_at": _serialize_datetime(last_attempt_at),
        "last_attempt_status": (
            str(scope_detail.get("last_attempt_status"))
            if scope_detail.get("last_attempt_status") not in {None, ""}
            else None
        ),
        "last_failure_at": _serialize_datetime(last_failure_at),
        "last_failure_reason": (
            str(scope_detail.get("last_failure_reason"))
            if scope_detail.get("last_failure_reason") not in {None, ""}
            else None
        ),
        "last_skip_at": _serialize_datetime(last_skip_at),
        "last_skip_reason": (
            str(scope_detail.get("last_skip_reason"))
            if scope_detail.get("last_skip_reason") not in {None, ""}
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
    user_stream_summary = get_user_stream_detail(settings_row)
    reconciliation_summary = get_reconciliation_detail(settings_row)
    candidate_selection_summary = get_candidate_selection_detail(settings_row)
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
        "user_stream_summary": user_stream_summary,
        "reconciliation_summary": reconciliation_summary,
        "candidate_selection_summary": candidate_selection_summary,
        "last_account_sync_at": sync_freshness_summary["account"]["last_sync_at"],
        "last_positions_sync_at": sync_freshness_summary["positions"]["last_sync_at"],
        "last_open_orders_sync_at": sync_freshness_summary["open_orders"]["last_sync_at"],
        "last_protective_orders_sync_at": sync_freshness_summary["protective_orders"]["last_sync_at"],
        "sync_freshness_summary": sync_freshness_summary,
    }
