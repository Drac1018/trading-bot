from __future__ import annotations

from typing import Any, Literal

from trading_mvp.models import Setting

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


def normalize_operating_state(value: object) -> OperatingState:
    if isinstance(value, str) and value in OPERATING_STATES:
        return value  # type: ignore[return-value]
    return TRADABLE_STATE


def get_runtime_detail(settings_row: Setting) -> dict[str, Any]:
    return _as_dict(settings_row.pause_reason_detail)


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
    }
