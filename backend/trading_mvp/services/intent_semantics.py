from __future__ import annotations

from collections.abc import Mapping
from typing import Any

PROTECTION_REASON_CODES = {
    "PROTECTION_REQUIRED",
    "PROTECTION_RECOVERY",
    "PROTECTION_RESTORE",
}
TIGHTEN_MANAGEMENT_ACTIONS = {
    "tighten_stop",
    "trail_stop",
    "break_even",
    "move_stop_to_break_even",
}


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item not in {None, ""}]


def _explicit_value(
    output_payload: Mapping[str, Any] | None,
    metadata: Mapping[str, Any] | None,
    key: str,
) -> object:
    output = _as_dict(output_payload)
    meta = _as_dict(metadata)
    if key in output and output.get(key) not in {None, ""}:
        return output.get(key)
    if key in meta and meta.get(key) not in {None, ""}:
        return meta.get(key)
    return None


def _scenario(metadata: Mapping[str, Any] | None) -> str:
    meta = _as_dict(metadata)
    selection_context = _as_dict(meta.get("selection_context"))
    if selection_context:
        hinted = str(selection_context.get("expected_scenario") or selection_context.get("scenario") or "").strip().lower()
        if hinted:
            return hinted
    strategy_engine = _as_dict(meta.get("strategy_engine"))
    selected = _as_dict(strategy_engine.get("selected_engine"))
    hinted = str(selected.get("scenario") or "").strip().lower()
    if hinted:
        return hinted
    return ""


def _strategy_engine_name(metadata: Mapping[str, Any] | None) -> str:
    meta = _as_dict(metadata)
    strategy_engine = _as_dict(meta.get("strategy_engine"))
    selected = _as_dict(strategy_engine.get("selected_engine"))
    engine_name = str(selected.get("engine_name") or "").strip()
    if engine_name:
        return engine_name
    return str(meta.get("strategy_engine_name") or "").strip()


def _trigger_reason(metadata: Mapping[str, Any] | None) -> str:
    meta = _as_dict(metadata)
    ai_trigger = _as_dict(meta.get("ai_trigger"))
    return str(
        meta.get("last_ai_trigger_reason")
        or ai_trigger.get("trigger_reason")
        or meta.get("trigger_type")
        or ""
    ).strip().lower()


def _position_management_payload(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    meta = _as_dict(metadata)
    position_management = _as_dict(meta.get("position_management"))
    action_payload = _as_dict(position_management.get("position_management_action"))
    if action_payload:
        return action_payload
    if isinstance(meta.get("position_management_action"), dict):
        return _as_dict(meta.get("position_management_action"))
    return {}


def infer_intent_semantics(
    output_payload: Mapping[str, Any] | None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    valid_intent_families = {"entry", "management", "protection", "exit", "unknown"}
    valid_management_actions = {"restore_protection", "reduce_only", "exit_only", "tighten_management", "none"}
    explicit_intent_family_raw = str(_explicit_value(output_payload, metadata, "intent_family") or "").strip().lower()
    explicit_management_action_raw = str(_explicit_value(output_payload, metadata, "management_action") or "").strip().lower()
    explicit_intent_family = (
        explicit_intent_family_raw
        if explicit_intent_family_raw in valid_intent_families and explicit_intent_family_raw != "unknown"
        else ""
    )
    explicit_management_action = (
        explicit_management_action_raw
        if explicit_management_action_raw in valid_management_actions and explicit_management_action_raw != "none"
        else ""
    )

    output = _as_dict(output_payload)
    meta = _as_dict(metadata)
    decision = str(output.get("decision") or "").strip().lower()
    rationale_codes = {
        *{code for code in _as_list(output.get("rationale_codes"))},
        *{code for code in _as_list(output.get("primary_reason_codes"))},
    }
    scenario = _scenario(meta)
    strategy_engine_name = _strategy_engine_name(meta).lower()
    trigger_reason = _trigger_reason(meta)
    operating_state = str(meta.get("operating_state") or "").strip().upper()
    management_payload = _position_management_payload(meta)
    management_action_name = str(management_payload.get("action") or "").strip().lower()
    tighten_indicators = bool(
        management_action_name in TIGHTEN_MANAGEMENT_ACTIONS
        or (
            decision == "hold"
            and any(code.startswith("POSITION_MANAGEMENT_") for code in rationale_codes)
        )
    )
    protection_indicators = bool(
        rationale_codes & PROTECTION_REASON_CODES
        or scenario == "protection_restore"
        or strategy_engine_name == "protection_reduce_engine"
        or trigger_reason == "protection_review_event"
        or operating_state == "PROTECTION_REQUIRED"
    )

    if decision in {"long", "short"}:
        inferred_intent_family = "protection" if protection_indicators else "entry"
        inferred_management_action = "restore_protection" if protection_indicators else "none"
    elif decision == "reduce":
        inferred_intent_family = "protection" if protection_indicators else "management"
        inferred_management_action = "reduce_only"
    elif decision == "exit":
        inferred_intent_family = "exit"
        inferred_management_action = "exit_only"
    elif tighten_indicators:
        inferred_intent_family = "management"
        inferred_management_action = "tighten_management"
    elif protection_indicators:
        inferred_intent_family = "protection"
        inferred_management_action = "restore_protection" if scenario == "protection_restore" else "none"
    else:
        inferred_intent_family = "unknown"
        inferred_management_action = "none"

    intent_family = explicit_intent_family if explicit_intent_family in valid_intent_families else inferred_intent_family
    management_action = (
        explicit_management_action
        if explicit_management_action in valid_management_actions
        else inferred_management_action
    )
    legacy_semantics_preserved = bool(intent_family in {"management", "protection", "exit"} and decision in {"long", "short"})
    analytics_excluded_from_entry_stats = intent_family != "entry"
    return {
        "intent_family": intent_family,
        "management_action": management_action,
        "legacy_semantics_preserved": legacy_semantics_preserved,
        "analytics_excluded_from_entry_stats": analytics_excluded_from_entry_stats,
    }


def is_entry_intent(
    output_payload: Mapping[str, Any] | None,
    metadata: Mapping[str, Any] | None = None,
) -> bool:
    semantics = infer_intent_semantics(output_payload, metadata)
    return not bool(semantics.get("analytics_excluded_from_entry_stats"))
