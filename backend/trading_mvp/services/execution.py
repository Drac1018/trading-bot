from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha1
from math import floor
from threading import Lock
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from trading_mvp.config import get_settings
from trading_mvp.models import AgentRun, Execution, Order, PnLSnapshot, Position, RiskCheck, Setting
from trading_mvp.schemas import (
    ExecutionIntent,
    FeaturePayload,
    MarketCandle,
    MarketSnapshotPayload,
    ProtectionLifecycleSnapshot,
    ProtectionLifecycleState,
    ProtectionLifecycleTransition,
    RiskCheckResult,
    TradeDecision,
)
from trading_mvp.services.account import (
    create_exchange_pnl_snapshot,
    fetch_incremental_funding_entries,
    get_latest_pnl_snapshot,
    get_open_position,
    record_funding_ledger_entries,
    refresh_open_position_marks,
)
from trading_mvp.services.audit import (
    create_alert,
    normalize_correlation_ids,
    record_audit_event,
    record_health_event,
    record_position_management_event,
    record_protective_order_health_event,
)
from trading_mvp.services.binance import BinanceAPIError, BinanceClient
from trading_mvp.services.binance_user_stream import (
    USER_STREAM_FALLBACK_SOURCE,
    BinanceUserStreamListener,
    build_user_stream_state,
    normalize_user_stream_event,
)
from trading_mvp.services.execution_policy import (
    ExecutionPlan,
    select_execution_plan,
    should_fallback_aggressively,
)
from trading_mvp.services.holding_profile import (
    HOLDING_PROFILE_POSITION,
    HOLDING_PROFILE_SCALP,
    HOLDING_PROFILE_SWING,
    deterministic_stop_management_payload,
    resolve_holding_profile_management_policy,
)
from trading_mvp.services.pause_control import (
    clear_symbol_protection_state,
    mark_manage_only_state,
    set_symbol_protection_state,
)
from trading_mvp.services.position_management import (
    PARTIAL_TAKE_PROFIT_FRACTION,
    build_position_management_context,
    mark_partial_take_profit_taken,
    mark_time_stop_action,
    record_add_on_metadata,
    seed_position_management_metadata,
    store_position_management_context,
)
from trading_mvp.services.range_mr_cooldown import record_range_mr_trade_result
from trading_mvp.services.risk import (
    evaluate_breakeven_stop_move_risk_guard,
    evaluate_risk,
    is_survival_path_decision,
)
from trading_mvp.services.runtime_state import (
    DEGRADED_MANAGE_ONLY_STATE,
    EMERGENCY_EXIT_STATE,
    PROTECTION_RECOVERY_THRESHOLD,
    PROTECTION_REQUIRED_STATE,
    TRADABLE_STATE,
    build_execution_dedupe_key,
    build_sync_freshness_summary,
    clear_execution_lock,
    clear_unresolved_submission_guard,
    get_execution_dedupe_record,
    get_operating_state,
    get_protection_recovery_detail,
    get_reconciliation_detail,
    get_unresolved_submission_guard,
    get_user_stream_detail,
    list_unresolved_submission_guards,
    mark_execution_lock,
    mark_sync_issue,
    mark_sync_success,
    record_binance_rest_issue,
    record_binance_rest_success,
    replace_user_stream_detail,
    set_reconciliation_detail,
    set_unresolved_submission_guard,
    set_user_stream_detail,
    should_use_rest_order_reconciliation,
    store_execution_dedupe_record,
)
from trading_mvp.services.settings import (
    get_effective_symbol_schedule,
    get_limited_live_max_notional,
    get_rollout_mode,
    get_runtime_credentials,
    rollout_mode_allows_exchange_submit,
    set_trading_pause,
)
from trading_mvp.time_utils import utcnow_naive

FINAL_ORDER_STATUSES = {"filled", "canceled", "rejected", "expired"}
AUTO_RESUME_DELAY_MINUTES = 5
PROTECTIVE_ORDER_TYPES = ("STOP_MARKET", "TAKE_PROFIT_MARKET")
ENTRY_EXECUTION_TYPE_PASSIVE_LIMIT = "entry_passive_limit"
ENTRY_EXECUTION_TYPE_MARKETABLE = "entry_marketable"
ENTRY_EXECUTION_TYPE_UNKNOWN = "entry_unknown"
PROTECTION_RETRY_ATTEMPTS = 2
POSITION_EXIT_REVIEW_ALLOWED_RECOMMENDATIONS = {
    "full_take_profit",
    "partial_take_profit",
    "tighten_trailing",
    "move_to_breakeven",
    "reduce_risk_only",
}
POSITION_EXIT_REVIEW_RECOMMENDATION_ALIASES = {
    "take_profit_exit": "full_take_profit",
    "take_partial_profit": "partial_take_profit",
    "reduce_runner": "reduce_risk_only",
}
POSITION_EXIT_REVIEW_SYNC_SCOPES = ("account", "positions", "open_orders", "protective_orders")
POSITION_EXIT_REVIEW_FULL_TP_REASON_CODE = "POSITION_EXIT_REVIEW_FULL_TAKE_PROFIT"
POSITION_EXIT_REVIEW_PARTIAL_TP_REASON_CODE = "POSITION_EXIT_REVIEW_PARTIAL_TAKE_PROFIT"
POSITION_EXIT_REVIEW_REDUCE_REASON_CODE = "POSITION_EXIT_REVIEW_REDUCE_RISK_ONLY"
POSITION_EXIT_REVIEW_TIGHTEN_REASON_CODE = "POSITION_EXIT_REVIEW_TIGHTEN_TRAILING"
POSITION_EXIT_REVIEW_BREAKEVEN_REASON_CODE = "POSITION_EXIT_REVIEW_MOVE_TO_BREAKEVEN"
POSITION_EXIT_REVIEW_STALE_SYNC_REASON_CODE = "POSITION_EXIT_REVIEW_STALE_SYNC"
POSITION_EXIT_REVIEW_PROTECTION_UNVERIFIED_REASON_CODE = "POSITION_EXIT_REVIEW_PROTECTION_UNVERIFIED"
POSITION_EXIT_REVIEW_STOP_RELAXATION_IGNORED_REASON_CODE = "POSITION_EXIT_REVIEW_STOP_RELAXATION_IGNORED"
POSITION_EXIT_REVIEW_LOCAL_FILTER_BLOCKED_REASON_CODE = "POSITION_EXIT_REVIEW_LOCAL_FILTER_BLOCKED"
POSITION_EXIT_REVIEW_PARTIAL_NOT_READY_REASON_CODE = "POSITION_EXIT_REVIEW_PARTIAL_NOT_READY"
POSITION_EXIT_REVIEW_SCALP_RUNNER_BLOCKED_REASON_CODE = "POSITION_EXIT_REVIEW_SCALP_RUNNER_BLOCKED"
POSITION_EXIT_REVIEW_SWING_RUNNER_SIGNAL_REQUIRED_REASON_CODE = (
    "POSITION_EXIT_REVIEW_SWING_RUNNER_SIGNAL_REQUIRED"
)
POSITION_EXIT_REVIEW_POSITION_EXIT_TOO_EARLY_REASON_CODE = "POSITION_EXIT_REVIEW_POSITION_EXIT_TOO_EARLY"
PROTECTION_VERIFY_DEADLINE_SECONDS = 30
PROTECTION_VERIFY_FETCH_ATTEMPTS = 2
PROTECTION_VERIFY_FAILED_REASON_CODE = "PROTECTION_VERIFY_FAILED"
PROTECTION_VERIFY_BLOCKING_INTENT_TYPES = {"entry", "scale_in"}
PROTECTIVE_STOP_LOSS_MISSING_REASON_CODE = "PROTECTIVE_STOP_LOSS_MISSING"
PROTECTIVE_TAKE_PROFIT_MISSING_REASON_CODE = "PROTECTIVE_TAKE_PROFIT_MISSING"
PROTECTIVE_ORDER_QUANTITY_MISMATCH_REASON_CODE = "PROTECTIVE_ORDER_QUANTITY_MISMATCH"
PROTECTIVE_ORDER_REDUCE_ONLY_MISSING_REASON_CODE = "PROTECTIVE_ORDER_REDUCE_ONLY_MISSING"
PROTECTIVE_ORDER_SIDE_MISMATCH_REASON_CODE = "PROTECTIVE_ORDER_SIDE_MISMATCH"
PROTECTIVE_ORDER_HEALTH_REASON_CODES = {
    PROTECTIVE_STOP_LOSS_MISSING_REASON_CODE,
    PROTECTIVE_TAKE_PROFIT_MISSING_REASON_CODE,
    PROTECTIVE_ORDER_QUANTITY_MISMATCH_REASON_CODE,
    PROTECTIVE_ORDER_REDUCE_ONLY_MISSING_REASON_CODE,
    PROTECTIVE_ORDER_SIDE_MISMATCH_REASON_CODE,
}
INACTIVE_PROTECTIVE_ORDER_STATUSES = {"CANCELED", "CANCELLED", "REJECTED", "EXPIRED", "FILLED"}
DUPLICATE_EXECUTION_SUPPRESSED_REASON_CODE = "DUPLICATE_EXECUTION_SUPPRESSED"
UNKNOWN_SUBMISSION_REASON_CODE = "LIVE_ORDER_SUBMISSION_UNKNOWN"
UNRESOLVED_SUBMISSION_GUARD_REASON_CODE = "UNRESOLVED_SUBMISSION_GUARD_ACTIVE"
UNRESOLVED_SUBMISSION_DEADLINE_REASON_CODE = "UNRESOLVED_SUBMISSION_DEADLINE_EXCEEDED"
UNRESOLVED_SUBMISSION_MAX_RECONCILE_ATTEMPTS = 6
UNRESOLVED_SUBMISSION_RECONCILE_INTERVAL_SECONDS = 20
UNRESOLVED_SUBMISSION_RESOLUTION_DEADLINE_SECONDS = 300
POSITION_MODE_ONE_WAY = "one_way"
POSITION_MODE_HEDGE = "hedge"
POSITION_MODE_UNKNOWN = "unknown"
POSITION_MODE_UNCLEAR_REASON_CODE = "EXCHANGE_POSITION_MODE_UNCLEAR"
POSITION_MODE_MISMATCH_REASON_CODE = "EXCHANGE_POSITION_MODE_MISMATCH"
FUNDING_LEDGER_SYNC_REASON_CODE = "FUNDING_LEDGER_SYNC_FAILED"
ROLLOUT_MODE_SHADOW_REASON_CODE = "ROLLOUT_MODE_SHADOW"
ROLLOUT_MODE_LIVE_DRY_RUN_REASON_CODE = "ROLLOUT_MODE_LIVE_DRY_RUN"
CLOSED_POSITION_PROTECTIVE_ORDER_RECONCILED_REASON_CODE = "POSITION_CLOSED_PROTECTIVE_ORDER_ORPHANED"
PROTECTIVE_CLOSE_FILL_BACKFILL_SOURCE = "EXCHANGE_BACKFILL"
PROTECTIVE_CLOSE_FILL_BACKFILL_PENDING_REASON_CODE = "PROTECTIVE_CLOSE_FILL_BACKFILL_PENDING"
PROTECTIVE_CLOSE_FILL_BACKFILL_FAILED_REASON_CODE = "PROTECTIVE_CLOSE_FILL_BACKFILL_FAILED"
PROTECTIVE_CLOSE_FILL_BACKFILL_CLOSED_STATUSES = {"FINISHED", "FILLED", "CLOSED"}
TIGHT_TP_BPS_THRESHOLD = 40.0

_ACTIVE_SYMBOL_EXECUTION_LOCKS: dict[str, dict[str, object]] = {}
_ACTIVE_SYMBOL_EXECUTION_LOCKS_GUARD = Lock()
ORDER_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"pending", "partially_filled", "filled", "canceled", "rejected", "expired"},
    "partially_filled": {"partially_filled", "filled", "canceled", "rejected", "expired"},
    "filled": {"filled"},
    "canceled": {"canceled"},
    "rejected": {"rejected"},
    "expired": {"expired"},
}


def _entry_price(decision: TradeDecision, market_snapshot: MarketSnapshotPayload) -> float:
    if decision.entry_zone_min is not None and decision.entry_zone_max is not None:
        return (decision.entry_zone_min + decision.entry_zone_max) / 2
    return market_snapshot.latest_price


def _holding_profile_execution_payload(decision: TradeDecision) -> dict[str, object]:
    return {
        "holding_profile": decision.holding_profile,
        "holding_profile_reason": decision.holding_profile_reason,
        **deterministic_stop_management_payload(hard_stop_active=decision.stop_loss is not None),
    }


def _position_management_metadata_for_execution(position: Position | None) -> dict[str, Any]:
    if position is None or not isinstance(position.metadata_json, dict):
        return {}
    management = position.metadata_json.get("position_management")
    return dict(management) if isinstance(management, dict) else {}


def _take_profit_execution_policy(position: Position | None) -> dict[str, object]:
    management = _position_management_metadata_for_execution(position)
    profile = str(management.get("holding_profile") or HOLDING_PROFILE_SCALP).strip().lower()
    policy = resolve_holding_profile_management_policy(profile)
    order_mode = str(
        management.get("take_profit_order_mode")
        or policy.get("take_profit_order_mode")
        or "full_close"
    )
    partial_fraction = min(
        max(
            _to_float(
                management.get("partial_take_profit_fraction"),
                _to_float(policy.get("partial_take_profit_fraction"), PARTIAL_TAKE_PROFIT_FRACTION),
            ),
            0.01,
        ),
        1.0,
    )
    if profile != HOLDING_PROFILE_SWING or order_mode != "partial_reduce":
        order_mode = "full_close"
        partial_fraction = 1.0
    return {
        "holding_profile": profile,
        "mode": order_mode,
        "partial_take_profit_fraction": partial_fraction,
        "runner_after_partial_take_profit": bool(
            management.get("runner_after_partial_take_profit")
            if "runner_after_partial_take_profit" in management
            else policy.get("runner_after_partial_take_profit")
        ),
    }


def _execution_setting_bool(settings_row: Setting, name: str, fallback: bool) -> bool:
    app_settings = get_settings()
    configured = getattr(settings_row, name, None)
    if configured in {None, ""}:
        configured = getattr(app_settings, name, fallback)
    return _to_bool(configured, fallback)


def _execution_setting_float(settings_row: Setting, name: str, fallback: float, *, minimum: float = 0.0) -> float:
    app_settings = get_settings()
    configured = getattr(settings_row, name, None)
    if configured in {None, ""}:
        configured = getattr(app_settings, name, fallback)
    return max(_to_float(configured, fallback), minimum)


def _take_profit_distance_bps(position: Position | None, take_profit: float | None) -> float | None:
    if position is None or take_profit is None:
        return None
    reference_price = position.entry_price if position.entry_price > 0 else position.mark_price
    if reference_price <= 0:
        return None
    if position.side == "long" and take_profit > reference_price:
        return ((take_profit - reference_price) / reference_price) * 10_000
    if position.side == "short" and take_profit < reference_price:
        return ((reference_price - take_profit) / reference_price) * 10_000
    return None


def _take_profit_limit_policy(
    settings_row: Setting,
    position: Position,
    *,
    take_profit: float,
) -> dict[str, object]:
    tp_distance_bps = _take_profit_distance_bps(position, take_profit)
    threshold_bps = _execution_setting_float(
        settings_row,
        "tight_tp_bps_threshold",
        TIGHT_TP_BPS_THRESHOLD,
        minimum=0.0,
    )
    setting_enabled = _execution_setting_bool(settings_row, "use_limit_take_profit_for_tight_tp", True)
    post_only = _execution_setting_bool(settings_row, "tp_limit_post_only", True)
    limit_enabled = bool(
        setting_enabled
        and tp_distance_bps is not None
        and tp_distance_bps <= threshold_bps
        and position.quantity > 0
    )
    fallback_reason: str | None = None
    if not setting_enabled:
        fallback_reason = "limit_take_profit_setting_disabled"
    elif tp_distance_bps is None:
        fallback_reason = "take_profit_distance_unavailable"
    elif tp_distance_bps > threshold_bps:
        fallback_reason = "take_profit_not_tight"
    elif position.quantity <= 0:
        fallback_reason = "take_profit_quantity_unavailable"
    return {
        "tp_limit_enabled": limit_enabled,
        "tp_limit_post_only": bool(post_only and limit_enabled),
        "tight_tp_bps_threshold": threshold_bps,
        "take_profit_distance_bps": round(tp_distance_bps, 6) if tp_distance_bps is not None else None,
        "tp_market_fallback_reason": fallback_reason,
    }


def _partial_take_profit_taken(position: Position | None) -> bool:
    return bool(_position_management_metadata_for_execution(position).get("partial_take_profit_taken"))


def _take_profit_required_for_protection(position: Position | None) -> bool:
    policy = _take_profit_execution_policy(position)
    return not (
        policy.get("mode") == "partial_reduce"
        and bool(policy.get("runner_after_partial_take_profit"))
        and _partial_take_profit_taken(position)
    )


def _is_partial_take_profit_order(row: Order) -> bool:
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    policy = metadata.get("take_profit_execution_policy")
    if not isinstance(policy, dict):
        return False
    order_type = str(row.order_type or "").lower()
    protective_component = str(metadata.get("protective_component") or "")
    return (
        (order_type.startswith("take_profit") or (order_type == "limit" and protective_component == "take_profit"))
        and str(policy.get("mode") or "") == "partial_reduce"
    )


def _mark_partial_take_profit_order_filled(
    session: Session,
    row: Order,
    *,
    symbol: str,
    fill_quantity: float,
    fill_price: float,
) -> None:
    if row.position_id is None or row.status != "filled" or not _is_partial_take_profit_order(row):
        return
    position = session.get(Position, row.position_id)
    if position is None:
        return
    if _partial_take_profit_taken(position):
        return
    management = mark_partial_take_profit_taken(position)
    session.add(position)
    record_position_management_event(
        session,
        event_type="partial_tp_executed",
        position_id=position.id,
        severity="info",
        message="Exchange take-profit order executed a swing partial take profit.",
        payload={
            "symbol": symbol,
            "order_id": row.id,
            "external_order_id": row.external_order_id,
            "fill_quantity": fill_quantity,
            "fill_price": fill_price,
            "source": "exchange_take_profit_order",
            "metadata": management,
        },
    )


def _to_float(value: object, default: float = 0.0) -> float:
    if value in {None, ""}:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value))


def _as_object_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


TRADE_PERFORMANCE_TAG_KEYS = (
    "strategy_id",
    "strategy_engine",
    "regime_id",
    "regime_label",
    "range_id",
    "range_low",
    "range_high",
    "range_width_pct",
    "entry_confirmation_type",
    "risk_mode",
)


def _first_text(*values: object, default: str = "") -> str:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        text = str(value).strip()
        if text:
            return text
    return default


def _is_present_value(value: object) -> bool:
    if value is None:
        return False
    return not (isinstance(value, str) and not value.strip())


def _trade_performance_tags_from_metadata(metadata: object) -> dict[str, Any]:
    payload = _as_object_dict(metadata)
    tags = _as_object_dict(payload.get("trade_performance_tags"))
    if tags:
        return dict(tags)
    derived: dict[str, Any] = {}
    for key in TRADE_PERFORMANCE_TAG_KEYS:
        if _is_present_value(payload.get(key)):
            derived[key] = payload[key]
    return derived


def _trade_performance_payload(tags: dict[str, Any] | None) -> dict[str, Any]:
    if not tags:
        return {}
    cleaned = {key: value for key, value in tags.items() if _is_present_value(value)}
    payload: dict[str, Any] = {"trade_performance_tags": cleaned}
    for key in TRADE_PERFORMANCE_TAG_KEYS:
        if cleaned.get(key) not in {None, ""}:
            payload[key] = cleaned[key]
    return payload


def _merge_trade_performance_tags(
    metadata: object,
    tags: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = _as_object_dict(metadata)
    existing = _trade_performance_tags_from_metadata(payload)
    if not tags and not existing:
        return payload
    merged = {
        **existing,
        **{key: value for key, value in (tags or {}).items() if _is_present_value(value)},
    }
    return {
        **payload,
        **_trade_performance_payload(merged),
    }


def _risk_mode_from_drawdown_state(drawdown_state: dict[str, Any]) -> str:
    state = str(drawdown_state.get("current_drawdown_state") or drawdown_state.get("drawdown_state") or "normal").strip().lower()
    if state == "recovery":
        return "drawdown_recovery"
    return state or "normal"


def _strategy_engine_name_from_decision_payload(
    metadata: dict[str, Any],
    input_payload: dict[str, Any],
    output_payload: dict[str, Any],
) -> str:
    existing_tags = _trade_performance_tags_from_metadata(metadata)
    if existing_tags.get("strategy_id"):
        return str(existing_tags["strategy_id"])
    ai_context = _as_object_dict(input_payload.get("ai_context"))
    selection_context = _as_object_dict(metadata.get("selection_context"))
    for value in (
        metadata.get("strategy_id"),
        metadata.get("strategy_engine"),
        selection_context.get("strategy_engine"),
        ai_context.get("strategy_engine"),
        output_payload.get("strategy_id"),
        output_payload.get("strategy_engine"),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    for container in (
        metadata.get("strategy_engine"),
        selection_context.get("strategy_engine_context"),
        ai_context.get("strategy_engine_context"),
        output_payload.get("strategy_engine_context"),
    ):
        payload = _as_object_dict(container)
        selected = _as_object_dict(payload.get("selected_engine"))
        engine_name = _first_text(selected.get("engine_name"), payload.get("strategy_engine"))
        if engine_name:
            return engine_name
    entry_mode = str(output_payload.get("entry_mode") or "").lower()
    decision = str(output_payload.get("decision") or "").lower()
    rationale_codes = {str(code) for code in output_payload.get("rationale_codes") or [] if code}
    if rationale_codes & {"PROTECTION_REQUIRED", "PROTECTION_RECOVERY", "PROTECTION_RESTORE"}:
        return "protection_reduce_engine"
    if entry_mode == "breakout_confirm":
        return "breakout_exception_engine"
    if any("CONTINUATION" in code for code in rationale_codes):
        return "trend_continuation_engine"
    if entry_mode == "pullback_confirm":
        return "trend_pullback_engine"
    if decision in {"reduce", "exit"}:
        return "protection_reduce_engine"
    return "unspecified_engine"


def _regime_tags_from_decision_payload(
    metadata: dict[str, Any],
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    ai_context = _as_object_dict(input_payload.get("ai_context"))
    feature_layers = _as_object_dict(input_payload.get("feature_layers"))
    features = _as_object_dict(input_payload.get("features"))
    feature_regime = _as_object_dict(features.get("regime"))
    regime_summary = (
        _as_object_dict(ai_context.get("regime_summary"))
        or _as_object_dict(feature_layers.get("regime_summary"))
        or _as_object_dict(metadata.get("regime_summary"))
    )
    composite_regime = _as_object_dict(ai_context.get("composite_regime"))
    primary_regime = _first_text(
        regime_summary.get("primary_regime"),
        feature_regime.get("primary_regime"),
        composite_regime.get("structure_regime"),
        metadata.get("regime"),
        default="unknown",
    )
    direction_regime = _first_text(
        composite_regime.get("direction_regime"),
        regime_summary.get("trend_alignment"),
        feature_regime.get("trend_alignment"),
        default="unknown",
    )
    regime_id = primary_regime
    if direction_regime and direction_regime != "unknown":
        regime_id = f"{primary_regime}:{direction_regime}"
    return {
        "regime_id": regime_id,
        "regime_label": primary_regime,
        "regime_summary": regime_summary,
        "composite_regime": composite_regime,
    }


def _entry_confirmation_type(
    *,
    strategy_id: str,
    decision: TradeDecision,
    output_payload: dict[str, Any],
) -> str:
    existing = _trade_performance_tags_from_metadata(output_payload).get("entry_confirmation_type")
    if existing:
        return str(existing)
    if strategy_id == "range_mean_reversion_engine":
        return "range_edge_confirm"
    entry_mode = _first_text(output_payload.get("entry_mode"), decision.entry_mode, default="none").lower()
    if entry_mode in {"breakout_confirm", "pullback_confirm", "immediate", "none"}:
        return entry_mode
    return "none"


def _risk_tag_payload(
    *,
    decision: TradeDecision,
    requested_price: float,
    requested_quantity: float,
    risk_result: RiskCheckResult,
) -> dict[str, Any]:
    stop_loss = _to_float(decision.stop_loss)
    take_profit = _to_float(decision.take_profit)
    risk_per_unit = abs(requested_price - stop_loss) if requested_price > 0 and stop_loss > 0 else 0.0
    initial_risk_usdt = risk_per_unit * abs(requested_quantity) if risk_per_unit > 0 and requested_quantity > 0 else 0.0
    return {
        "entry_price": requested_price if requested_price > 0 else None,
        "stop_loss": stop_loss if stop_loss > 0 else None,
        "take_profit": take_profit if take_profit > 0 else None,
        "risk_per_unit": risk_per_unit if risk_per_unit > 0 else None,
        "initial_quantity": abs(requested_quantity) if requested_quantity > 0 else None,
        "initial_risk_usdt": initial_risk_usdt if initial_risk_usdt > 0 else None,
        "approved_risk_pct": risk_result.approved_risk_pct,
        "approved_leverage": risk_result.approved_leverage,
        "approved_projected_notional": risk_result.approved_projected_notional,
        "approved_quantity": risk_result.approved_quantity,
    }


def _build_trade_performance_tags(
    session: Session,
    *,
    decision_run_id: int | None,
    decision: TradeDecision,
    market_snapshot: MarketSnapshotPayload,
    risk_result: RiskCheckResult,
    risk_row: RiskCheck | None,
    intent_type: str,
    requested_price: float,
    requested_quantity: float,
    execution_plan: ExecutionPlan | None = None,
    existing_position: Position | None = None,
) -> dict[str, Any]:
    if decision.decision in {"reduce", "exit"} and existing_position is not None:
        position_tags = _trade_performance_tags_from_metadata(existing_position.metadata_json)
        if position_tags:
            return {
                **position_tags,
                "exit_decision_run_id": decision_run_id,
                "exit_decision": decision.decision,
                "exit_intent_type": intent_type,
            }

    decision_run = session.get(AgentRun, decision_run_id) if decision_run_id is not None else None
    metadata = _as_object_dict(decision_run.metadata_json if decision_run is not None else None)
    existing_tags = _trade_performance_tags_from_metadata(metadata)
    input_payload = _as_object_dict(decision_run.input_payload if decision_run is not None else None)
    output_payload = _as_object_dict(decision_run.output_payload if decision_run is not None else None)
    if not output_payload:
        output_payload = decision.model_dump(mode="json")
    strategy_id = _strategy_engine_name_from_decision_payload(metadata, input_payload, output_payload)
    regime_tags = _regime_tags_from_decision_payload(metadata, input_payload)
    risk_debug_payload = _as_object_dict(risk_result.debug_payload)
    if not risk_debug_payload and risk_row is not None:
        risk_debug_payload = _as_object_dict(_as_object_dict(risk_row.payload).get("debug_payload"))
    drawdown_state = _as_object_dict(risk_debug_payload.get("drawdown_state"))
    risk_payload = _risk_tag_payload(
        decision=decision,
        requested_price=requested_price if requested_price > 0 else market_snapshot.latest_price,
        requested_quantity=requested_quantity,
        risk_result=risk_result,
    )
    return {
        **existing_tags,
        "strategy_id": strategy_id,
        "strategy_engine": strategy_id,
        "regime_id": regime_tags["regime_id"],
        "regime_label": regime_tags["regime_label"],
        "entry_confirmation_type": _entry_confirmation_type(
            strategy_id=strategy_id,
            decision=decision,
            output_payload=output_payload,
        ),
        "risk_mode": _risk_mode_from_drawdown_state(drawdown_state),
        "current_drawdown_state": drawdown_state.get("current_drawdown_state") or drawdown_state.get("drawdown_state") or "normal",
        "drawdown_state": drawdown_state,
        "regime_summary": regime_tags["regime_summary"],
        "composite_regime": regime_tags["composite_regime"],
        "symbol": decision.symbol,
        "timeframe": decision.timeframe,
        "decision": decision.decision,
        "intent_type": intent_type,
        "source_decision_run_id": decision_run_id,
        "source_risk_check_id": risk_row.id if risk_row is not None else None,
        "execution_policy_name": execution_plan.policy_name if execution_plan is not None else None,
        "risk": risk_payload,
        "risk_per_unit": risk_payload.get("risk_per_unit"),
        "initial_risk_usdt": risk_payload.get("initial_risk_usdt"),
    }


def _execution_r_multiple_payload(
    *,
    tags: dict[str, Any],
    fill_quantity: float,
    realized_pnl: float,
    fee_paid: float,
) -> dict[str, float]:
    risk = _as_object_dict(tags.get("risk"))
    risk_per_unit = _to_float(tags.get("risk_per_unit") or risk.get("risk_per_unit"))
    risk_amount = risk_per_unit * abs(fill_quantity) if risk_per_unit > 0 and fill_quantity > 0 else 0.0
    if risk_amount <= 0:
        risk_amount = _to_float(tags.get("initial_risk_usdt") or risk.get("initial_risk_usdt"))
    if risk_amount <= 0:
        return {}
    return {
        "risk_amount_usdt": risk_amount,
        "gross_r_multiple": realized_pnl / risk_amount,
        "net_r_multiple": (realized_pnl - fee_paid) / risk_amount,
    }


def _to_bool(value: object, default: bool = False) -> bool:
    if value in {None, ""}:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _exchange_permission_sync_detail(account_info: dict[str, object]) -> dict[str, object]:
    raw_can_trade = account_info.get("canTrade")
    checked_at = utcnow_naive().isoformat()
    if raw_can_trade in {None, ""}:
        return {
            "exchange_can_trade": None,
            "exchange_can_trade_known": False,
            "exchange_can_trade_source": "binance_account_info_missing_canTrade",
            "exchange_can_trade_checked_at": checked_at,
        }
    return {
        "exchange_can_trade": _to_bool(raw_can_trade),
        "exchange_can_trade_known": True,
        "exchange_can_trade_source": "binance_account_info",
        "exchange_can_trade_checked_at": checked_at,
    }


def _coerce_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000.0
        return datetime.fromtimestamp(timestamp)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _normalize_position_mode(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {POSITION_MODE_ONE_WAY, "oneway", "one-way", "single"}:
        return POSITION_MODE_ONE_WAY
    if normalized in {POSITION_MODE_HEDGE, "dual", "dual_side", "dual-side"}:
        return POSITION_MODE_HEDGE
    return POSITION_MODE_UNKNOWN


def _normalize_exchange_position_side(value: object) -> str | None:
    normalized = str(value or "").strip().upper()
    if normalized in {"LONG", "SHORT", "BOTH"}:
        return normalized
    return None


def _resolve_sync_symbols(settings_row: Setting, symbol: str | None) -> list[str]:
    if symbol:
        return [symbol.upper()]
    return [
        effective.symbol
        for effective in get_effective_symbol_schedule(settings_row)
        if effective.enabled
    ]


def _symbol_payloads(payloads: list[dict[str, object]], symbol: str) -> list[dict[str, object]]:
    symbol_upper = symbol.upper()
    return [
        dict(item)
        for item in payloads
        if str(item.get("symbol") or "").upper() == symbol_upper
    ]


def _position_mode_guard_reason_code(position_mode: str) -> str | None:
    if position_mode == POSITION_MODE_UNKNOWN:
        return POSITION_MODE_UNCLEAR_REASON_CODE
    if position_mode == POSITION_MODE_HEDGE:
        return POSITION_MODE_MISMATCH_REASON_CODE
    return None


def _position_mode_guard_message(reason_code: str | None) -> str | None:
    if reason_code == POSITION_MODE_UNCLEAR_REASON_CODE:
        return "거래소 포지션 모드를 확인하지 못해 신규 진입을 차단합니다."
    if reason_code == POSITION_MODE_MISMATCH_REASON_CODE:
        return "거래소 Hedge mode가 현재 one-way 로컬 해석과 충돌해 신규 진입을 차단합니다."
    return None


def _exchange_order_position_side(payload: dict[str, object]) -> str | None:
    return _normalize_exchange_position_side(
        payload.get("positionSide")
        or payload.get("ps")
    )


def _position_metadata_side(position: Position | None) -> str | None:
    if position is None:
        return None
    metadata = position.metadata_json if isinstance(position.metadata_json, dict) else {}
    explicit = _normalize_exchange_position_side(metadata.get("exchange_position_side"))
    if explicit in {"LONG", "SHORT"}:
        return explicit
    if position.side == "long":
        return "LONG"
    if position.side == "short":
        return "SHORT"
    return None


def _filter_orders_for_position_context(
    open_orders: list[dict[str, object]],
    *,
    position_mode: str,
    exchange_position_side: str | None,
) -> list[dict[str, object]]:
    if position_mode == POSITION_MODE_HEDGE and exchange_position_side in {"LONG", "SHORT"}:
        filtered = [
            item
            for item in open_orders
            if _exchange_order_position_side(item) == exchange_position_side
        ]
        return filtered
    if position_mode == POSITION_MODE_ONE_WAY:
        return [
            item
            for item in open_orders
            if _exchange_order_position_side(item) in {None, "BOTH"}
        ]
    return list(open_orders)


def _record_position_mode_guard_transition(
    session: Session,
    settings_row: Setting,
    *,
    reason_code: str | None,
    guarded_symbols: list[str],
    position_mode: str,
    position_mode_source: str,
    detail: dict[str, object] | None = None,
) -> None:
    previous = get_reconciliation_detail(settings_row)
    previous_reason = str(previous.get("mode_guard_reason_code") or "") or None
    previous_symbols = [str(item).upper() for item in previous.get("guarded_symbols", []) if item]
    normalized_symbols = [str(item).upper() for item in guarded_symbols if item]
    if previous_reason == reason_code and previous_symbols == normalized_symbols:
        return
    payload = {
        "reason_code": reason_code,
        "guarded_symbols": normalized_symbols,
        "position_mode": position_mode,
        "position_mode_source": position_mode_source,
        **(detail or {}),
    }
    if reason_code:
        message = _position_mode_guard_message(reason_code) or "Exchange position mode guard is active."
        record_audit_event(
            session,
            event_type="exchange_position_mode_guard_enabled",
            entity_type="settings",
            entity_id=str(settings_row.id),
            severity="warning",
            message=message,
            payload=payload,
        )
        record_health_event(
            session,
            component="live_sync",
            status="degraded",
            message=message,
            payload=payload,
        )
        return
    if previous_reason:
        record_audit_event(
            session,
            event_type="exchange_position_mode_guard_cleared",
            entity_type="settings",
            entity_id=str(settings_row.id),
            severity="info",
            message="Exchange position mode guard cleared.",
            payload=payload,
        )
        record_health_event(
            session,
            component="live_sync",
            status="ok",
            message="Exchange position mode guard cleared.",
            payload=payload,
        )


class PreTradeExchangeFilterError(RuntimeError):
    def __init__(self, reason_code: str, *, detail: dict[str, Any] | None = None) -> None:
        self.reason_code = reason_code
        self.detail = detail or {}
        super().__init__(reason_code)


def _stringify_submit_error(exc: BaseException) -> str:
    message = str(exc).strip()
    if message:
        return f"{exc.__class__.__name__}: {message}"
    return exc.__class__.__name__


def _build_submission_tracking(
    *,
    submission_state: str,
    client_order_id: str | None,
    submit_attempt_count: int,
    last_submit_error: str | None = None,
    safe_retry_used: bool = False,
    recovered_via: str | None = None,
    reconcile_attempt_count: int | None = None,
    next_reconcile_at: datetime | None = None,
    final_resolution_deadline: datetime | None = None,
    unresolved_guard_active: bool | None = None,
) -> dict[str, Any]:
    tracking: dict[str, Any] = {
        "submission_state": submission_state,
        "submit_attempt_count": max(int(submit_attempt_count), 0),
        "last_submit_error": last_submit_error or None,
    }
    if client_order_id:
        tracking["client_order_id"] = client_order_id
    if safe_retry_used:
        tracking["safe_retry_used"] = True
    if recovered_via:
        tracking["recovered_via"] = recovered_via
    if reconcile_attempt_count is not None:
        tracking["reconcile_attempt_count"] = max(int(reconcile_attempt_count), 0)
    if next_reconcile_at is not None:
        tracking["next_reconcile_at"] = next_reconcile_at.isoformat()
    if final_resolution_deadline is not None:
        tracking["final_resolution_deadline"] = final_resolution_deadline.isoformat()
    if unresolved_guard_active is not None:
        tracking["unresolved_guard_active"] = bool(unresolved_guard_active)
    tracking["updated_at"] = utcnow_naive().isoformat()
    return tracking


def _entry_action_for_decision(decision: TradeDecision) -> str | None:
    if decision.decision == "long":
        return "long"
    if decision.decision == "short":
        return "short"
    return None


def _coerce_tracking_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


class OrderSubmissionUnknownError(RuntimeError):
    def __init__(
        self,
        *,
        client_order_id: str,
        submit_request: dict[str, Any],
        submit_attempt_count: int,
        last_submit_error: str | None,
        safe_retry_used: bool = False,
        message: str = "Live order submission timed out and could not be reconciled yet.",
    ) -> None:
        self.client_order_id = client_order_id
        self.submit_request = dict(submit_request)
        self.submission_tracking = _build_submission_tracking(
            submission_state="submit_unknown",
            client_order_id=client_order_id,
            submit_attempt_count=submit_attempt_count,
            last_submit_error=last_submit_error,
            safe_retry_used=safe_retry_used,
        )
        super().__init__(message)


def _normalize_submit_request(
    client: BinanceClient,
    *,
    symbol: str,
    quantity: float | None = None,
    price: float | None = None,
    stop_price: float | None = None,
    reference_price: float | None = None,
    approved_notional: float | None = None,
    enforce_min_notional: bool = True,
    close_position: bool = False,
) -> dict[str, Any]:
    if hasattr(client, "normalize_order_request"):
        normalized = client.normalize_order_request(
            symbol=symbol,
            quantity=quantity,
            price=price,
            stop_price=stop_price,
            reference_price=reference_price,
            approved_notional=approved_notional,
            enforce_min_notional=enforce_min_notional,
            close_position=close_position,
        )
    else:
        normalized_price = (
            client.normalize_price(symbol, price) if price is not None and hasattr(client, "normalize_price") else price
        )
        normalized_stop = (
            client.normalize_price(symbol, stop_price)
            if stop_price is not None and hasattr(client, "normalize_price")
            else stop_price
        )
        effective_reference = (
            normalized_price
            if normalized_price is not None and normalized_price > 0
            else normalized_stop
            if normalized_stop is not None and normalized_stop > 0
            else reference_price
        )
        normalized_quantity = quantity
        if quantity is not None and hasattr(client, "normalize_order_quantity"):
            normalized_quantity = client.normalize_order_quantity(
                symbol,
                quantity,
                reference_price=effective_reference,
                enforce_min_notional=enforce_min_notional and not close_position,
            )
        normalized = {
            "symbol": symbol.upper(),
            "quantity": normalized_quantity,
            "price": normalized_price,
            "stop_price": normalized_stop,
            "reference_price": effective_reference,
            "notional": (
                normalized_quantity * effective_reference
                if normalized_quantity is not None and effective_reference is not None
                else None
            ),
            "filters": client.get_symbol_filters(symbol) if hasattr(client, "get_symbol_filters") else {},
            "reason_code": None,
        }
    normalized = dict(normalized)
    filters = normalized.get("filters") if isinstance(normalized.get("filters"), dict) else {}
    effective_reference = _to_float(
        normalized.get("reference_price"),
        _to_float(normalized.get("price"), _to_float(normalized.get("stop_price"), _to_float(reference_price))),
    )
    normalized_quantity_value = normalized.get("quantity")
    if normalized_quantity_value is not None:
        normalized_quantity = abs(_to_float(normalized_quantity_value))
        if approved_notional is not None and approved_notional > 0 and effective_reference > 0:
            normalized_quantity = _cap_quantity_to_approved_notional(
                client,
                symbol=symbol,
                quantity=normalized_quantity,
                reference_price=effective_reference,
                approved_notional=approved_notional,
            )
        normalized["quantity"] = normalized_quantity
        normalized["reference_price"] = effective_reference if effective_reference > 0 else normalized.get("reference_price")
        normalized["notional"] = (
            normalized_quantity * effective_reference
            if normalized_quantity > 0 and effective_reference > 0
            else None
        )
        if not normalized.get("reason_code"):
            min_qty = _to_float(filters.get("min_qty"))
            min_notional = _to_float(filters.get("min_notional"))
            if normalized_quantity <= 0:
                normalized["reason_code"] = "ORDER_QTY_ZERO_AFTER_STEP_SIZE"
            elif min_qty > 0 and normalized_quantity < min_qty:
                normalized["reason_code"] = "ORDER_QTY_BELOW_MIN_QTY"
            elif (
                enforce_min_notional
                and not close_position
                and min_notional > 0
                and effective_reference > 0
                and normalized_quantity * effective_reference < min_notional
            ):
                normalized["reason_code"] = "ORDER_NOTIONAL_BELOW_MIN_NOTIONAL"
    reason_code = str(normalized.get("reason_code") or "") or None
    if reason_code is not None:
        raise PreTradeExchangeFilterError(reason_code, detail=dict(normalized))
    return dict(normalized)


def _exchange_order_requested_price(exchange_order: dict[str, object], fallback: float) -> float:
    price = _to_float(exchange_order.get("price"))
    if price > 0:
        return price
    stop_price = _to_float(exchange_order.get("stopPrice"))
    if stop_price > 0:
        return stop_price
    return fallback


def _apply_exchange_order_state(
    row: Order,
    exchange_order: dict[str, object],
    *,
    requested_quantity_fallback: float,
    requested_price_fallback: float,
    reduce_only_fallback: bool,
    close_only_fallback: bool,
    updated_at: datetime | None = None,
) -> None:
    incoming_status = _map_exchange_status(str(exchange_order.get("status", "NEW")))
    current_status = str(row.status or "pending")
    allowed_statuses = ORDER_STATUS_TRANSITIONS.get(current_status, {incoming_status})
    if current_status in FINAL_ORDER_STATUSES and incoming_status != current_status:
        resolved_status = current_status
    elif incoming_status in allowed_statuses:
        resolved_status = incoming_status
    else:
        resolved_status = current_status
    row.status = resolved_status
    row.exchange_status = str(exchange_order.get("status", "")) or None
    row.last_exchange_update_at = updated_at or utcnow_naive()
    row.filled_quantity = abs(_to_float(exchange_order.get("executedQty"), row.filled_quantity))
    requested_quantity = abs(_to_float(exchange_order.get("origQty"), requested_quantity_fallback))
    if requested_quantity > 0:
        row.requested_quantity = requested_quantity
    requested_price = _exchange_order_requested_price(exchange_order, requested_price_fallback)
    if requested_price > 0:
        row.requested_price = requested_price
    avg_price = _to_float(exchange_order.get("avgPrice") or exchange_order.get("price"), row.average_fill_price)
    if avg_price > 0:
        row.average_fill_price = avg_price
    row.reduce_only = _to_bool(exchange_order.get("reduceOnly"), default=reduce_only_fallback)
    row.close_only = _to_bool(exchange_order.get("closePosition"), default=close_only_fallback)


def _optional_int(value: object) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _build_binance_request_error_hook(settings_row: Setting) -> Callable[[dict[str, object]], None]:
    def _hook(event: dict[str, object]) -> None:
        if event.get("event") == "binance_time_sync":
            record_binance_rest_success(
                settings_row,
                source=f"binance_client:{event.get('endpoint_category') or 'unknown'}:time_sync",
                detail={
                    "time_sync": {
                        "method": event.get("method"),
                        "path": event.get("path"),
                        "offset_ms": event.get("offset_ms"),
                        "recv_window_ms": event.get("recv_window_ms"),
                    },
                    "resolved_api_codes": event.get("resolved_api_codes") or [-1021],
                },
            )
            return
        record_binance_rest_issue(
            settings_row,
            reason_code=str(event.get("reason_code") or "BINANCE_REST_REQUEST_FAILED"),
            source=f"binance_client:{event.get('endpoint_category') or 'unknown'}",
            error=str(event.get("exception") or event.get("failure_type") or "Binance request failed"),
            failure_type=str(event.get("failure_type") or event.get("error_type") or "request_error"),
            http_status=_optional_int(event.get("http_status") or event.get("status_code")),
            api_code=_optional_int(event.get("api_code")),
            mutating_request=bool(event.get("mutating_request", False)),
            transport_error=bool(event.get("transport_error", False)),
            server_error=bool(event.get("server_error", False)),
            rate_limited=bool(event.get("rate_limited", False)),
            detail={
                "method": event.get("method"),
                "path": event.get("path"),
                "endpoint_category": event.get("endpoint_category"),
                "attempt": event.get("attempt"),
                "attempts": event.get("attempts"),
            },
        )

    return _hook


def _build_client(settings_row: Setting) -> BinanceClient:
    credentials = get_runtime_credentials(settings_row)
    defaults = get_settings()
    return BinanceClient(
        api_key=credentials.binance_api_key,
        api_secret=credentials.binance_api_secret,
        testnet_enabled=settings_row.binance_testnet_enabled,
        futures_enabled=settings_row.binance_futures_enabled,
        recv_window_ms=defaults.exchange_recv_window_ms,
        request_error_hook=_build_binance_request_error_hook(settings_row),
    )


def _ensure_user_stream_registration(
    session: Session,
    settings_row: Setting,
    *,
    client: BinanceClient,
    flush_state: bool = True,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    listener = BinanceUserStreamListener(client)
    state, issues = listener.ensure_registration(get_user_stream_detail(settings_row))
    replace_user_stream_detail(settings_row, state)
    session.add(settings_row)
    if flush_state:
        session.flush()
    return get_user_stream_detail(settings_row), [dict(item) for item in issues if isinstance(item, dict)]


def _persist_user_stream_state(settings_row: Setting, state: dict[str, Any]) -> None:
    replace_user_stream_detail(settings_row, build_user_stream_state(state))


def _build_account_info_from_user_stream(account_update: dict[str, object]) -> dict[str, object]:
    balances = account_update.get("B") if isinstance(account_update.get("B"), list) else []
    positions = account_update.get("P") if isinstance(account_update.get("P"), list) else []
    wallet_balance = 0.0
    available_balance = 0.0
    if balances:
        preferred = next(
            (
                item
                for item in balances
                if isinstance(item, dict) and str(item.get("a") or "").upper() == "USDT"
            ),
            balances[0] if isinstance(balances[0], dict) else None,
        )
        if isinstance(preferred, dict):
            wallet_balance = _to_float(preferred.get("wb"))
            available_balance = _to_float(preferred.get("cw"), wallet_balance)
    total_unrealized = 0.0
    for item in positions:
        if isinstance(item, dict):
            total_unrealized += _to_float(item.get("up"))
    total_margin_balance = wallet_balance + total_unrealized
    return {
        "availableBalance": available_balance,
        "totalWalletBalance": wallet_balance,
        "totalUnrealizedProfit": total_unrealized,
        "totalMarginBalance": total_margin_balance,
        "assets": balances,
    }


def _apply_user_stream_position_payload(
    session: Session,
    *,
    symbol: str,
    position_payload: dict[str, object],
) -> Position | None:
    position_amt = _to_float(position_payload.get("pa") or position_payload.get("positionAmt"))
    local = get_open_position(session, symbol)
    if abs(position_amt) <= 1e-9:
        if local is not None:
            local.status = "closed"
            local.quantity = 0.0
            local.closed_at = utcnow_naive()
            local.metadata_json = _as_object_dict(local.metadata_json)
            session.add(local)
            session.flush()
        return None
    entry_price = _to_float(position_payload.get("ep") or position_payload.get("entryPrice"))
    mark_price = _to_float(position_payload.get("mp") or position_payload.get("markPrice"), entry_price)
    leverage = _to_float(position_payload.get("l") or position_payload.get("leverage"), 1.0)
    quantity = abs(position_amt)
    exchange_position_side = _normalize_exchange_position_side(
        position_payload.get("ps") or position_payload.get("positionSide")
    )
    if exchange_position_side == "LONG":
        side = "long"
    elif exchange_position_side == "SHORT":
        side = "short"
    else:
        side = "long" if position_amt > 0 else "short"
        exchange_position_side = "BOTH"
    exchange_position_mode = (
        POSITION_MODE_HEDGE if exchange_position_side in {"LONG", "SHORT"} else POSITION_MODE_ONE_WAY
    )
    if local is None:
        local = Position(
            symbol=symbol,
            mode="live",
            side=side,
            status="open",
            quantity=quantity,
            entry_price=entry_price,
            mark_price=mark_price,
            leverage=leverage,
            stop_loss=entry_price if entry_price > 0 else mark_price,
            take_profit=entry_price if entry_price > 0 else mark_price,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            metadata_json={
                "origin": "binance_user_stream",
                "exchange_position_side": exchange_position_side,
                "exchange_position_mode": exchange_position_mode,
            },
        )
    else:
        metadata = _as_object_dict(local.metadata_json)
        metadata["origin"] = "binance_user_stream"
        metadata["exchange_position_side"] = exchange_position_side
        metadata["exchange_position_mode"] = exchange_position_mode
        local.metadata_json = metadata
        local.side = side
        local.status = "open"
        local.quantity = quantity
        local.entry_price = entry_price or local.entry_price
        local.mark_price = mark_price or local.mark_price
        local.leverage = leverage or local.leverage
        local.closed_at = None
    local.unrealized_pnl = _to_float(position_payload.get("up"), (mark_price - entry_price) * quantity)
    session.add(local)
    session.flush()
    return local


def apply_user_stream_event(
    session: Session,
    settings_row: Setting,
    *,
    event_payload: dict[str, object],
    normalized_event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = (
        dict(normalized_event)
        if isinstance(normalized_event, dict)
        else normalize_user_stream_event(event_payload)
    )
    raw_payload = _as_object_dict(normalized.get("raw_payload")) or dict(event_payload)
    event_type = str(normalized.get("event_type") or raw_payload.get("e") or raw_payload.get("eventType") or "")
    event_time = _coerce_datetime(normalized.get("event_time")) or _coerce_datetime(raw_payload.get("E")) or utcnow_naive()
    set_user_stream_detail(
        settings_row,
        status="connected",
        source="binance_futures_user_stream",
        last_event_at=event_time,
        last_event_type=event_type,
        heartbeat_ok=True,
        stream_source="user_stream",
        last_error="",
    )
    applied_symbols: list[str] = []
    if event_type == "ACCOUNT_UPDATE":
        account_update = _as_object_dict(raw_payload.get("a"))
        positions = account_update.get("P") if isinstance(account_update.get("P"), list) else []
        for item in positions:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("s") or item.get("symbol") or "").upper()
            if not symbol:
                continue
            _apply_user_stream_position_payload(session, symbol=symbol, position_payload=item)
            applied_symbols.append(symbol)
        if account_update:
            pnl_snapshot = create_exchange_pnl_snapshot(
                session,
                settings_row,
                _build_account_info_from_user_stream(account_update),
            )
            mark_sync_success(
                settings_row,
                scope="account",
                synced_at=event_time,
                detail={"source": "user_stream", "equity": pnl_snapshot.equity},
            )
        mark_sync_success(
            settings_row,
            scope="positions",
            synced_at=event_time,
            detail={"source": "user_stream", "symbols": applied_symbols},
        )
    elif event_type == "ORDER_TRADE_UPDATE":
        order_update = _as_object_dict(raw_payload.get("o"))
        symbol = str(order_update.get("s") or "").upper()
        if symbol:
            exchange_order = {
                "orderId": order_update.get("i") or order_update.get("orderId"),
                "clientOrderId": order_update.get("c") or order_update.get("clientOrderId"),
                "status": order_update.get("X") or order_update.get("status"),
                "origQty": order_update.get("q") or order_update.get("origQty"),
                "executedQty": order_update.get("z") or order_update.get("executedQty"),
                "avgPrice": order_update.get("ap") or order_update.get("avgPrice"),
                "price": order_update.get("p") or order_update.get("price"),
                "stopPrice": order_update.get("sp") or order_update.get("stopPrice"),
                "reduceOnly": order_update.get("R") or order_update.get("reduceOnly"),
                "closePosition": order_update.get("cp") or order_update.get("closePosition"),
                "type": order_update.get("o") or order_update.get("type"),
                "side": order_update.get("S") or order_update.get("side"),
                "positionSide": order_update.get("ps") or order_update.get("positionSide"),
            }
            row = _upsert_exchange_order_row(
                session,
                symbol=symbol,
                requested_price=_exchange_order_requested_price(exchange_order, _to_float(exchange_order.get("price"))),
                requested_quantity=abs(_to_float(exchange_order.get("origQty"))),
                order_type=str(exchange_order.get("type") or "MARKET"),
                side=str(exchange_order.get("side") or "BUY").lower(),
                exchange_order=exchange_order,
                decision_run_id=None,
                risk_row=None,
                reduce_only=_to_bool(exchange_order.get("reduceOnly")),
                close_only=_to_bool(exchange_order.get("closePosition")),
                updated_at=event_time,
            )
            trade_id = str(order_update.get("t") or "")
            last_fill_quantity = abs(_to_float(order_update.get("l")))
            last_fill_price = _to_float(order_update.get("L") or order_update.get("ap"))
            if trade_id and trade_id != "0" and last_fill_quantity > 0:
                existing = session.scalar(select(Execution).where(Execution.external_trade_id == trade_id).limit(1))
                if existing is None:
                    signed_slippage_bps = _signed_slippage_bps(
                        side=row.side,
                        requested_price=row.requested_price,
                        fill_price=last_fill_price,
                    )
                    session.add(
                        Execution(
                            order_id=row.id,
                            position_id=row.position_id,
                            symbol=symbol,
                            status="filled",
                            external_trade_id=trade_id,
                            fill_price=last_fill_price,
                            fill_quantity=last_fill_quantity,
                            fee_paid=abs(_to_float(order_update.get("n"))),
                            commission_asset=str(order_update.get("N") or "") or None,
                            slippage_pct=abs(last_fill_price - row.requested_price) / max(row.requested_price, 1.0)
                            if last_fill_price > 0 and row.requested_price > 0
                            else 0.0,
                            realized_pnl=_to_float(order_update.get("rp")),
                            payload={
                                "user_stream": dict(order_update),
                                "requested_price": row.requested_price,
                                "requested_quantity": row.requested_quantity,
                                "signed_slippage_pct": signed_slippage_bps / 10000.0,
                                "signed_slippage_bps": signed_slippage_bps,
                            },
                        )
                    )
            _mark_partial_take_profit_order_filled(
                session,
                row,
                symbol=symbol,
                fill_quantity=last_fill_quantity,
                fill_price=last_fill_price,
            )
            applied_symbols.append(symbol)
            mark_sync_success(
                settings_row,
                scope="open_orders",
                synced_at=event_time,
                detail={"symbol": symbol, "source": "user_stream", "event_type": event_type},
            )
    session.add(settings_row)
    session.flush()
    return {
        "event_type": event_type or "unknown",
        "event_time": event_time.isoformat(),
        "event_category": str(normalized.get("event_category") or "unknown"),
        "related_categories": list(normalized.get("related_categories") or []),
        "symbols": applied_symbols,
        "symbol": str(normalized.get("symbol") or "") or None,
        "order_id": str(normalized.get("order_id") or "") or None,
        "client_order_id": str(normalized.get("client_order_id") or "") or None,
        "order_status": str(normalized.get("order_status") or "") or None,
        "user_stream_summary": get_user_stream_detail(settings_row),
    }


def apply_normalized_user_stream_events(
    session: Session,
    settings_row: Setting,
    *,
    normalized_events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    applied_events: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for event in normalized_events:
        raw_payload = _as_object_dict(event.get("raw_payload"))
        try:
            applied_events.append(
                apply_user_stream_event(
                    session,
                    settings_row,
                    event_payload=raw_payload,
                    normalized_event=event,
                )
            )
        except Exception as exc:
            symbol = str(event.get("symbol") or settings_row.default_symbol or "").upper() or settings_row.default_symbol
            issue = {
                "severity": "warning",
                "reason_code": "USER_STREAM_EVENT_APPLY_FAILED",
                "message": "Failed to apply a Binance futures user stream event.",
                "payload": {
                    "error": str(exc),
                    "event_type": str(event.get("event_type") or "unknown"),
                    "symbol": symbol,
                    "normalized_event": dict(event),
                },
            }
            issues.append(issue)
            set_user_stream_detail(
                settings_row,
                status="degraded",
                heartbeat_ok=False,
                stream_source=USER_STREAM_FALLBACK_SOURCE,
                last_error=str(exc),
                last_disconnected_at=utcnow_naive(),
            )
            record_audit_event(
                session,
                event_type="user_stream_event_apply_failed",
                entity_type="binance",
                entity_id=symbol,
                severity="warning",
                message="Failed to apply a Binance futures user stream event.",
                payload=issue["payload"],
            )
            record_health_event(
                session,
                component="user_stream",
                status="error",
                message="Failed to apply a Binance futures user stream event.",
                payload=issue["payload"],
            )
            session.add(settings_row)
            session.flush()
    return applied_events, issues


def _drain_user_stream_events(
    session: Session,
    settings_row: Setting,
    *,
    client: BinanceClient,
    max_events: int = 8,
    idle_timeout_seconds: float = 0.15,
    flush_state: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    listener = BinanceUserStreamListener(client)

    async def _collect() -> dict[str, Any]:
        return await listener.collect_once(
            get_user_stream_detail(settings_row),
            max_events=max_events,
            idle_timeout_seconds=idle_timeout_seconds,
        )

    loop = asyncio.new_event_loop()
    try:
        collected = loop.run_until_complete(_collect())
    finally:
        loop.close()

    state = build_user_stream_state(collected.get("state"))
    _persist_user_stream_state(settings_row, state)
    session.add(settings_row)
    if flush_state:
        session.flush()

    normalized_events = [
        dict(item)
        for item in collected.get("events", [])
        if isinstance(item, dict)
    ]
    applied_events, apply_issues = apply_normalized_user_stream_events(
        session,
        settings_row,
        normalized_events=normalized_events,
    )
    issues = [dict(item) for item in collected.get("issues", []) if isinstance(item, dict)]
    issues.extend(apply_issues)
    return applied_events, issues, get_user_stream_detail(settings_row)


def poll_live_user_stream(
    session: Session,
    settings_row: Setting,
    *,
    client: BinanceClient | None = None,
    max_events: int = 8,
    idle_timeout_seconds: float = 1.0,
    flush_state: bool = True,
) -> dict[str, object]:
    stream_client = client
    if stream_client is None:
        credentials = get_runtime_credentials(settings_row)
        if not credentials.binance_api_key or not credentials.binance_api_secret:
            set_user_stream_detail(
                settings_row,
                status="unavailable",
                source="binance_futures_user_stream",
                heartbeat_ok=False,
                last_error="LIVE_CREDENTIALS_MISSING",
                stream_source="rest_polling_fallback",
            )
            session.add(settings_row)
            if flush_state:
                session.flush()
            user_stream_summary = get_user_stream_detail(settings_row)
            return {
                "user_stream_summary": user_stream_summary,
                "stream_health": str(user_stream_summary.get("status") or "unavailable"),
                "last_stream_event_time": user_stream_summary.get("last_event_at"),
                "stream_source": str(user_stream_summary.get("stream_source") or "rest_polling_fallback"),
                "stream_event_count": 0,
                "stream_events": [],
            }
        stream_client = _build_client(settings_row)
    user_stream_summary, stream_issues = _ensure_user_stream_registration(
        session,
        settings_row,
        client=stream_client,
        flush_state=flush_state,
    )
    stream_events: list[dict[str, Any]] = []
    if str(user_stream_summary.get("status") or "") != "degraded":
        try:
            stream_events, drain_issues, user_stream_summary = _drain_user_stream_events(
                session,
                settings_row,
                client=stream_client,
                max_events=max_events,
                idle_timeout_seconds=idle_timeout_seconds,
                flush_state=flush_state,
            )
            stream_issues.extend(drain_issues)
        except Exception as exc:
            reconnect_count = int(user_stream_summary.get("reconnect_count") or 0)
            set_user_stream_detail(
                settings_row,
                status="degraded",
                source="binance_futures_user_stream",
                listen_key=str(user_stream_summary.get("listen_key") or "") or None,
                reconnect_count=reconnect_count + 1,
                heartbeat_ok=False,
                last_error=str(exc),
                last_disconnected_at=utcnow_naive(),
                stream_source=USER_STREAM_FALLBACK_SOURCE,
            )
            session.add(settings_row)
            if flush_state:
                session.flush()
            stream_issues.append(
                {
                    "severity": "warning",
                    "reason_code": "USER_STREAM_POLL_FAILED",
                    "message": "Failed to poll the Binance futures user stream.",
                    "payload": {"error": str(exc)},
                }
            )
    user_stream_summary = get_user_stream_detail(settings_row)
    return {
        "user_stream_summary": user_stream_summary,
        "stream_health": str(user_stream_summary.get("status") or "idle"),
        "last_stream_event_time": user_stream_summary.get("last_event_at"),
        "stream_source": str(user_stream_summary.get("stream_source") or "rest_polling_fallback"),
        "stream_event_count": len(stream_events),
        "stream_events": stream_events,
        "stream_issues": stream_issues,
    }


def _classify_exchange_state_error(exc: Exception, default_reason: str) -> str:
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return "EXCHANGE_CONNECTIVITY_TEMPORARY_FAILURE"
    if isinstance(exc, BinanceAPIError) and (exc.status_code == 401 or exc.code in {-2014, -2015}):
        return "EXCHANGE_AUTH_PERMISSION_REJECTED"
    if isinstance(exc, BinanceAPIError) and exc.code in {-1021, -1001, -1007, -1003}:
        return "EXCHANGE_CONNECTIVITY_TEMPORARY_FAILURE"
    return default_reason


def _pause_for_system_issue(
    session: Session,
    settings_row: Setting,
    *,
    reason_code: str,
    symbol: str,
    error: str,
    event_type: str,
    component: str,
    alert_title: str,
    alert_message: str,
    correlation_ids: dict[str, Any] | None = None,
) -> None:
    set_trading_pause(
        session,
        True,
        reason_code=reason_code,
        reason_detail={"symbol": symbol, "error": error},
        pause_origin="system",
        auto_resume_after=utcnow_naive() + timedelta(minutes=AUTO_RESUME_DELAY_MINUTES),
        preserve_live_arm=True,
    )
    record_audit_event(
        session,
        event_type="trading_paused",
        entity_type="settings",
        entity_id=str(settings_row.id),
        severity="warning",
        message=alert_message,
        payload={
            "reason_code": reason_code,
            "pause_origin": "system",
            "symbol": symbol,
            "error": error,
        },
        correlation_ids=correlation_ids,
    )
    create_alert(
        session,
        category="execution",
        severity="error",
        title=alert_title,
        message=alert_message,
        payload={"reason_code": reason_code, "symbol": symbol, "error": error},
    )
    record_audit_event(
        session,
        event_type=event_type,
        entity_type=component,
        entity_id=symbol,
        severity="error",
        message=alert_message,
        payload={"reason_code": reason_code, "symbol": symbol, "error": error},
        correlation_ids=correlation_ids,
    )
    record_health_event(
        session,
        component=component,
        status="error",
        message=alert_message,
        payload={"reason_code": reason_code, "symbol": symbol, "error": error},
        correlation_ids=correlation_ids,
    )
    session.flush()


def _live_account_balances(account_info: dict[str, object]) -> dict[str, float]:
    available_balance = _to_float(account_info.get("availableBalance"))
    total_wallet_balance = _to_float(account_info.get("totalWalletBalance"))
    total_unrealized_profit = _to_float(account_info.get("totalUnrealizedProfit"))
    total_margin_balance = _to_float(account_info.get("totalMarginBalance"))

    equity = total_margin_balance if total_margin_balance > 0 else total_wallet_balance + total_unrealized_profit
    if equity <= 0:
        equity = total_wallet_balance
    sizing_equity = available_balance if available_balance > 0 else equity
    return {
        "available_balance": available_balance,
        "wallet_balance": total_wallet_balance,
        "unrealized_pnl": total_unrealized_profit,
        "equity": equity,
        "sizing_equity": max(sizing_equity, 0.0),
    }


def _create_live_account_snapshot(
    session: Session,
    settings_row: Setting,
    *,
    client: BinanceClient | None = None,
    account_info: dict[str, object] | None = None,
    component: str = "live_sync",
    event_type: str = "funding_ledger_sync_failed",
    symbol: str | None = None,
    correlation_ids: dict[str, object] | None = None,
) -> tuple[PnLSnapshot, dict[str, object]]:
    funding_summary: dict[str, object] = {
        "status": "not_requested" if client is None else "synced",
        "inserted_count": 0,
        "inserted_amount": 0.0,
        "last_occurred_at": None,
    }
    funding_entries: list[dict[str, object]] | None = None
    if client is not None and hasattr(client, "get_income_history"):
        try:
            funding_entries = fetch_incremental_funding_entries(session, client)
            ledger_summary = record_funding_ledger_entries(session, funding_entries)
            funding_summary = {
                "status": "synced",
                "fetched_count": len(funding_entries),
                "inserted_count": int(ledger_summary.get("inserted_count") or 0),
                "inserted_amount": _to_float(ledger_summary.get("inserted_amount")),
                "last_occurred_at": (
                    ledger_summary["last_occurred_at"].isoformat()
                    if isinstance(ledger_summary.get("last_occurred_at"), datetime)
                    else None
                ),
            }
        except Exception as exc:
            funding_summary = {
                "status": "warning",
                "reason_code": FUNDING_LEDGER_SYNC_REASON_CODE,
                "error": str(exc),
                "fetched_count": 0,
                "inserted_count": 0,
                "inserted_amount": 0.0,
                "last_occurred_at": None,
            }
            payload = {
                "reason_code": FUNDING_LEDGER_SYNC_REASON_CODE,
                "error": str(exc),
                **({"symbol": symbol} if symbol else {}),
            }
            record_health_event(
                session,
                component=component,
                status="warning",
                message="Funding ledger sync failed; keeping the prior funding total until the next successful sync.",
                payload=payload,
                correlation_ids=correlation_ids,
            )
            record_audit_event(
                session,
                event_type=event_type,
                entity_type="settings",
                entity_id=str(settings_row.id),
                severity="warning",
                message="Funding ledger sync failed; account snapshot used the existing funding ledger total.",
                payload=payload,
                correlation_ids=correlation_ids,
            )
    pnl_snapshot = create_exchange_pnl_snapshot(
        session,
        settings_row,
        account_info,
    )
    return pnl_snapshot, funding_summary


def _calculate_quantity(entry_price: float, stop_loss: float | None, equity: float, risk_pct: float, leverage: float) -> float:
    if stop_loss is None:
        return max((equity * min(leverage, 1.0)) / max(entry_price, 1.0), 0.0001)
    per_unit_risk = abs(entry_price - stop_loss)
    if per_unit_risk == 0:
        return 0.0
    risk_budget = equity * risk_pct
    max_notional_quantity = (equity * leverage) / max(entry_price, 1.0)
    return round(min(risk_budget / per_unit_risk, max_notional_quantity), 6)


def _quantity_for_notional(notional: float, price: float) -> float:
    if notional <= 0 or price <= 0:
        return 0.0
    return round(notional / price, 6)


def _cap_quantity_to_approved_notional(
    client: Any,
    *,
    symbol: str,
    quantity: float,
    reference_price: float,
    approved_notional: float,
) -> float:
    if approved_notional <= 0 or reference_price <= 0 or quantity <= 0:
        return quantity
    max_quantity = approved_notional / reference_price
    if quantity <= max_quantity + 1e-9:
        return quantity
    if not hasattr(client, "get_symbol_filters"):
        return round(max(max_quantity, 0.0), 6)
    filters = client.get_symbol_filters(symbol)
    step_size = _to_float(filters.get("step_size"))
    min_qty = _to_float(filters.get("min_qty"))
    capped = max_quantity
    if step_size > 0:
        capped = floor(max_quantity / step_size) * step_size
    if capped < min_qty:
        return 0.0
    return round(max(capped, 0.0), 6)


def _execution_minimum_notional_failure(
    client: Any,
    *,
    symbol: str,
    quantity: float,
    reference_price: float,
) -> str | None:
    if quantity <= 0 or reference_price <= 0 or not hasattr(client, "get_symbol_filters"):
        return "APPROVED_SIZE_BELOW_EXECUTION_MINIMUM" if quantity <= 0 else None
    filters = client.get_symbol_filters(symbol)
    min_qty = _to_float(filters.get("min_qty"))
    min_notional = _to_float(filters.get("min_notional"))
    if min_qty > 0 and quantity < min_qty:
        return "APPROVED_SIZE_BELOW_EXECUTION_MINIMUM"
    if min_notional > 0 and quantity * reference_price < min_notional:
        return "APPROVED_SIZE_BELOW_EXECUTION_MINIMUM"
    return None


def _decision_matches_position_side(decision: TradeDecision, existing_position: Position | None) -> bool:
    if existing_position is None:
        return False
    target_side = "long" if decision.decision == "long" else "short"
    return existing_position.side == target_side


def _classify_execution_intent(
    decision: TradeDecision,
    existing_position: Position | None,
    *,
    operating_state: str,
) -> str:
    if decision.decision in {"reduce", "exit"}:
        return "reduce_only"
    if existing_position is not None and _decision_matches_position_side(decision, existing_position):
        if operating_state in {PROTECTION_REQUIRED_STATE, DEGRADED_MANAGE_ONLY_STATE}:
            return "protection"
        if existing_position.side in {"long", "short"}:
            return "scale_in"
    return "entry"


def _execution_order_policy_from_risk_result(
    risk_result: RiskCheckResult,
    *,
    intent_type: str,
) -> tuple[str, bool, str | None]:
    if intent_type != "entry":
        return "market_allowed", True, None
    debug_payload = _as_object_dict(risk_result.debug_payload)
    expected_cost_gate = _as_object_dict(debug_payload.get("expected_cost_gate"))
    required_order_policy = str(
        expected_cost_gate.get("required_order_policy")
        or debug_payload.get("required_order_policy")
        or "market_allowed"
    )
    if required_order_policy not in {
        "market_allowed",
        "limit_only",
        "limit_only_or_post_only",
        "block_or_pending",
    }:
        required_order_policy = "market_allowed"
    raw_allow_market_fallback = expected_cost_gate.get(
        "allow_market_fallback",
        expected_cost_gate.get("market_fallback_allowed", debug_payload.get("allow_market_fallback")),
    )
    allow_market_fallback = _to_bool(
        raw_allow_market_fallback,
        default=required_order_policy == "market_allowed",
    )
    if required_order_policy in {"limit_only", "limit_only_or_post_only", "block_or_pending"}:
        allow_market_fallback = False
    order_policy_reason = (
        expected_cost_gate.get("order_policy_reason")
        or debug_payload.get("order_policy_reason")
        or expected_cost_gate.get("market_fallback_violation_source")
    )
    order_policy_reason = None if order_policy_reason in {None, ""} else str(order_policy_reason)
    if required_order_policy == "market_allowed":
        required_order_policy = "limit_only_or_post_only"
        allow_market_fallback = False
        order_policy_reason = order_policy_reason or "entry_market_fallback_disabled_by_default"
    return required_order_policy, allow_market_fallback, order_policy_reason


def _reduce_fraction_for_decision(decision: TradeDecision, settings_row: Setting) -> float:
    rationale_codes = set(decision.rationale_codes)
    if "POSITION_MANAGEMENT_PARTIAL_TAKE_PROFIT" in rationale_codes:
        configured = _to_float(getattr(settings_row, "partial_tp_size_pct", PARTIAL_TAKE_PROFIT_FRACTION))
        return min(max(configured, 0.01), 1.0)
    if "POSITION_MANAGEMENT_TIME_STOP_REDUCE" in rationale_codes:
        return 0.5
    if {"POSITION_MANAGEMENT_EDGE_DECAY", "POSITION_MANAGEMENT_REGIME_SHIFT", "POSITION_MANAGEMENT_MOMENTUM_WEAKENING"} & rationale_codes:
        return 0.35
    return 0.5


def _is_effectively_zero(value: float | None) -> bool:
    return value is None or abs(value) <= 1e-9


def _build_market_snapshot_from_position(position: Position, feature_payload: FeaturePayload) -> MarketSnapshotPayload:
    snapshot_time = utcnow_naive()
    latest_price = position.mark_price if position.mark_price > 0 else position.entry_price
    return MarketSnapshotPayload(
        symbol=position.symbol,
        timeframe=feature_payload.timeframe,
        snapshot_time=snapshot_time,
        latest_price=latest_price,
        latest_volume=max(feature_payload.volume_ratio, 0.0),
        candle_count=1,
        is_stale=False,
        is_complete=True,
        candles=[
            MarketCandle(
                timestamp=snapshot_time,
                open=latest_price,
                high=latest_price,
                low=latest_price,
                close=latest_price,
                volume=max(feature_payload.volume_ratio, 0.0),
            )
        ],
    )


def _build_position_management_trade_decision(
    position: Position,
    *,
    feature_payload: FeaturePayload,
    context: dict[str, object],
    settings_row: Setting,
) -> TradeDecision | None:
    reason_codes = [str(item) for item in _get_string_list(context, "reduce_reason_codes")]
    if not reason_codes:
        return None

    if POSITION_EXIT_REVIEW_FULL_TP_REASON_CODE in reason_codes:
        decision_type = "exit"
        explanation_short = "ai exit review full take profit"
        explanation_detailed = (
            "Position exit review requested a full take-profit candidate, then deterministic risk and execution gates "
            "validated it as a close-only management action."
        )
    elif "POSITION_MANAGEMENT_MFE_ROLLBACK_EXIT" in reason_codes:
        decision_type = "exit"
        explanation_short = "mfe rollback exit"
        explanation_detailed = (
            "Position management exited because the trade gave back too much of its maximum favorable excursion."
        )
    elif "POSITION_MANAGEMENT_TIME_STOP_EXIT" in reason_codes:
        decision_type = "exit"
        explanation_short = "time stop exit"
        explanation_detailed = "Time stop triggered a deterministic exit because the trade failed to show acceptable progress."
    else:
        decision_type = "reduce"
        if "POSITION_MANAGEMENT_MFE_ROLLBACK_REDUCE" in reason_codes:
            explanation_short = "mfe rollback reduce"
            explanation_detailed = (
                "Position management reduced size because the runner gave back too much of its maximum favorable excursion."
            )
        elif "POSITION_MANAGEMENT_PARTIAL_TAKE_PROFIT" in reason_codes:
            explanation_short = "partial take profit"
            explanation_detailed = (
                "Partial take profit triggered after the configured R threshold and keeps the position in reduce-only mode."
            )
        elif POSITION_EXIT_REVIEW_REDUCE_REASON_CODE in reason_codes:
            explanation_short = "ai exit review reduce risk"
            explanation_detailed = (
                "Position exit review requested a risk-reduction candidate, then deterministic risk and execution gates "
                "validated it as a reduce-only management action."
            )
        elif "POSITION_MANAGEMENT_TIME_STOP_REDUCE" in reason_codes:
            explanation_short = "time stop reduce"
            explanation_detailed = "Time stop triggered a deterministic size reduction because edge decayed before target follow-through."
        else:
            explanation_short = "position management reduce"
            explanation_detailed = "Deterministic position management requested a reduce-only adjustment."

    return TradeDecision(
        decision=decision_type,  # type: ignore[arg-type]
        confidence=0.9,
        symbol=position.symbol,
        timeframe=feature_payload.timeframe,
        entry_zone_min=position.mark_price if position.mark_price > 0 else position.entry_price,
        entry_zone_max=position.mark_price if position.mark_price > 0 else position.entry_price,
        stop_loss=position.stop_loss,
        take_profit=position.take_profit,
        max_holding_minutes=max(getattr(settings_row, "time_stop_minutes", 120), 1),
        risk_pct=min(settings_row.max_risk_per_trade, 0.01),
        leverage=max(min(position.leverage, settings_row.max_leverage), 1.0),
        rationale_codes=reason_codes,
        explanation_short=explanation_short,
        explanation_detailed=explanation_detailed,
    )


def _position_exit_review_metadata(position: Position) -> tuple[dict[str, Any], dict[str, Any]]:
    metadata = _as_object_dict(position.metadata_json)
    return (
        _as_object_dict(metadata.get("position_exit_review")),
        _as_object_dict(metadata.get("position_exit_review_source")),
    )


def _normalize_position_exit_review_recommendation(value: object) -> str | None:
    recommendation = str(value or "").strip().lower()
    recommendation = POSITION_EXIT_REVIEW_RECOMMENDATION_ALIASES.get(recommendation, recommendation)
    if recommendation in POSITION_EXIT_REVIEW_ALLOWED_RECOMMENDATIONS or recommendation in {"no_action", "hold_runner"}:
        return recommendation
    return None


def _position_exit_review_stale_scopes(settings_row: Setting) -> tuple[list[str], dict[str, Any]]:
    summary = build_sync_freshness_summary(settings_row)
    stale_scopes: list[str] = []
    for scope in POSITION_EXIT_REVIEW_SYNC_SCOPES:
        payload = _as_object_dict(summary.get(scope))
        status = str(payload.get("status") or "unknown").lower()
        raw_status = str(payload.get("raw_status") or "").lower()
        if (
            bool(payload.get("stale"))
            or bool(payload.get("incomplete"))
            or status in {"unknown", "stale", "failed", "incomplete", "skipped"}
            or raw_status in {"unknown", "failed", "incomplete", "skipped"}
        ):
            stale_scopes.append(scope)
    return stale_scopes, summary


def _position_exit_review_stop_relaxation_details(
    position: Position,
    review: dict[str, Any],
) -> list[dict[str, object]]:
    details: list[dict[str, object]] = []
    current_stop = _to_float(position.stop_loss) if position.stop_loss not in {None, ""} else None
    for key in ("stop_loss", "proposed_stop_loss", "candidate_stop_loss", "new_stop_loss"):
        if key not in review:
            continue
        try:
            candidate = _to_float(review.get(key))
        except (TypeError, ValueError):
            details.append({"field": key, "value": str(review.get(key)), "reason": "invalid_stop_payload"})
            continue
        if candidate <= 0 or not _is_more_protective_stop(position.side, current_stop, candidate):
            details.append({"field": key, "value": candidate, "reason": "not_more_protective"})
    action_text = " ".join(
        str(review.get(key) or "").lower()
        for key in ("stop_action", "stop_loss_action", "protection_action", "rationale", "summary")
    )
    if any(token in action_text for token in ("widen", "relax", "loosen", "remove stop", "remove_stop")):
        details.append({"field": "stop_action", "value": action_text[:240], "reason": "relaxation_language"})
    return details


def _record_position_exit_review_event(
    session: Session,
    *,
    position: Position,
    event_type: str,
    status: str,
    recommendation: str | None,
    reason_codes: list[str],
    review: dict[str, Any],
    extra: dict[str, object] | None = None,
) -> None:
    record_position_management_event(
        session,
        event_type=event_type,
        position_id=position.id,
        severity="info" if status in {"candidate_applied", "no_action"} else "warning",
        message="Position exit review execution candidate was processed.",
        payload={
            "symbol": position.symbol,
            "status": status,
            "recommendation": recommendation,
            "reason_codes": list(dict.fromkeys(reason_codes)),
            "review_reason_codes": _get_string_list(review, "reason_codes"),
            "execution_boundary": review.get("execution_boundary"),
            **(extra or {}),
        },
    )


def _append_unique_reason_codes(context: dict[str, object], *reason_codes: str) -> None:
    existing = [str(item) for item in _get_string_list(context, "reduce_reason_codes")]
    context["reduce_reason_codes"] = list(dict.fromkeys([*existing, *reason_codes]))


def _append_unique_applied_candidates(context: dict[str, object], *candidates: str) -> None:
    existing = [str(item) for item in _get_string_list(context, "applied_rule_candidates")]
    context["applied_rule_candidates"] = list(dict.fromkeys([*existing, *candidates]))


def _position_exit_review_holding_profile(position: Position, context: dict[str, object]) -> str:
    management = _position_management_metadata_for_execution(position)
    profile = str(context.get("holding_profile") or management.get("holding_profile") or HOLDING_PROFILE_SCALP)
    profile = profile.strip().lower()
    if profile not in {HOLDING_PROFILE_SCALP, HOLDING_PROFILE_SWING, HOLDING_PROFILE_POSITION}:
        return HOLDING_PROFILE_SCALP
    return profile


def _has_position_exit_review_local_reduce_evidence(
    context: dict[str, object],
    reduce_reason_codes: list[str],
) -> bool:
    if reduce_reason_codes:
        return True
    return any(
        _to_bool(context.get(key))
        for key in (
            "mfe_rollback_triggered",
            "time_stop_ready",
            "time_to_fail_ready",
            "holding_edge_decay_active",
            "regime_transition_detected",
            "momentum_weakening",
            "countertrend_pressure",
        )
    )


def _has_scalp_exit_weakness(context: dict[str, object], reduce_reason_codes: list[str]) -> bool:
    if any(
        _to_bool(context.get(key))
        for key in (
            "scalp_early_fail_ready",
            "time_to_fail_ready",
            "time_stop_ready",
            "regime_transition_detected",
            "momentum_weakening",
            "countertrend_pressure",
        )
    ):
        return True
    weakness_tokens = (
        "SCALP",
        "TIME_TO_FAIL",
        "TIME_STOP",
        "BREAKOUT_TIME_FAIL",
        "CONTINUATION_TIME_FAIL",
        "PULLBACK_TIME_FAIL",
        "REGIME_SHIFT",
        "MOMENTUM",
        "COUNTERTREND",
        "MFE_ROLLBACK_EXIT",
    )
    return any(any(token in code.upper() for token in weakness_tokens) for code in reduce_reason_codes)


def _has_swing_runner_invalidation(context: dict[str, object], reduce_reason_codes: list[str]) -> bool:
    if reduce_reason_codes or _to_bool(context.get("mfe_rollback_triggered")):
        return True
    if not _to_bool(context.get("partial_take_profit_taken")):
        return False
    return any(
        _to_bool(context.get(key))
        for key in (
            "time_stop_ready",
            "time_to_fail_ready",
            "holding_edge_decay_active",
            "regime_transition_detected",
            "momentum_weakening",
            "countertrend_pressure",
        )
    )


def _position_exit_review_local_filter_block_reasons(
    position: Position,
    context: dict[str, object],
    recommendation: str,
) -> tuple[list[str], dict[str, object]]:
    profile = _position_exit_review_holding_profile(position, context)
    policy = resolve_holding_profile_management_policy(profile)
    reduce_reason_codes = [str(code) for code in _get_string_list(context, "reduce_reason_codes")]
    partial_ready = _to_bool(context.get("partial_take_profit_ready"))
    partial_taken = _to_bool(context.get("partial_take_profit_taken"))
    runner_after_partial = _to_bool(
        context.get("runner_after_partial_take_profit"),
        bool(policy.get("runner_after_partial_take_profit")),
    )
    local_reduce_evidence = _has_position_exit_review_local_reduce_evidence(context, reduce_reason_codes)
    payload = {
        "holding_profile": profile,
        "recommendation": recommendation,
        "partial_take_profit_ready": partial_ready,
        "partial_take_profit_taken": partial_taken,
        "runner_after_partial_take_profit": runner_after_partial,
        "local_reduce_evidence": local_reduce_evidence,
        "reduce_reason_codes": reduce_reason_codes,
    }
    reason_codes: list[str] = []

    if recommendation == "partial_take_profit":
        if not partial_ready:
            reason_codes.append(POSITION_EXIT_REVIEW_PARTIAL_NOT_READY_REASON_CODE)
        if partial_taken:
            reason_codes.append("POSITION_EXIT_REVIEW_PARTIAL_TP_ALREADY_TAKEN")
        return (
            [POSITION_EXIT_REVIEW_LOCAL_FILTER_BLOCKED_REASON_CODE, *reason_codes] if reason_codes else [],
            payload,
        )

    if recommendation in {"full_take_profit", "reduce_risk_only"}:
        if not local_reduce_evidence:
            reason_codes.append(POSITION_EXIT_REVIEW_LOCAL_FILTER_BLOCKED_REASON_CODE)

        if profile == HOLDING_PROFILE_SCALP and not _has_scalp_exit_weakness(context, reduce_reason_codes):
            reason_codes.append(POSITION_EXIT_REVIEW_SCALP_RUNNER_BLOCKED_REASON_CODE)
        elif profile == HOLDING_PROFILE_SWING and not _has_swing_runner_invalidation(context, reduce_reason_codes):
            reason_codes.append(POSITION_EXIT_REVIEW_SWING_RUNNER_SIGNAL_REQUIRED_REASON_CODE)
        elif profile == HOLDING_PROFILE_POSITION:
            has_exit_code = any("EXIT" in code.upper() for code in reduce_reason_codes)
            if recommendation == "reduce_risk_only" or not has_exit_code:
                reason_codes.append(POSITION_EXIT_REVIEW_POSITION_EXIT_TOO_EARLY_REASON_CODE)

    if reason_codes and POSITION_EXIT_REVIEW_LOCAL_FILTER_BLOCKED_REASON_CODE not in reason_codes:
        reason_codes.insert(0, POSITION_EXIT_REVIEW_LOCAL_FILTER_BLOCKED_REASON_CODE)
    return list(dict.fromkeys(reason_codes)), payload


def _apply_position_exit_review_to_context(
    session: Session,
    settings_row: Setting,
    position: Position,
    context: dict[str, object],
    protection_state: dict[str, object],
) -> dict[str, object]:
    review, source = _position_exit_review_metadata(position)
    if not review:
        return context

    recommendation = _normalize_position_exit_review_recommendation(review.get("recommendation"))
    if recommendation in {None, "no_action", "hold_runner"}:
        context["position_exit_review_execution"] = {
            "status": "no_action",
            "recommendation": recommendation,
        }
        return context

    relaxation_details = _position_exit_review_stop_relaxation_details(position, review)
    if relaxation_details:
        _record_position_exit_review_event(
            session,
            position=position,
            event_type="position_exit_review_stop_relaxation_ignored",
            status="stop_relaxation_ignored",
            recommendation=recommendation,
            reason_codes=[POSITION_EXIT_REVIEW_STOP_RELAXATION_IGNORED_REASON_CODE],
            review=review,
            extra={"relaxation_details": relaxation_details},
        )

    if recommendation not in POSITION_EXIT_REVIEW_ALLOWED_RECOMMENDATIONS:
        return context

    gate = _as_object_dict(source.get("gate"))
    if gate and gate.get("allowed") is False:
        reason = str(gate.get("reason") or "position_exit_review_gate_blocked")
        context["position_exit_review_execution"] = {
            "status": "blocked",
            "recommendation": recommendation,
            "reason_codes": [reason],
        }
        _record_position_exit_review_event(
            session,
            position=position,
            event_type="position_exit_review_execution_blocked",
            status="blocked",
            recommendation=recommendation,
            reason_codes=[reason],
            review=review,
            extra={"gate": gate},
        )
        return context

    stale_scopes, sync_summary = _position_exit_review_stale_scopes(settings_row)
    if stale_scopes:
        context["position_exit_review_execution"] = {
            "status": "blocked",
            "recommendation": recommendation,
            "reason_codes": [POSITION_EXIT_REVIEW_STALE_SYNC_REASON_CODE],
            "stale_scopes": stale_scopes,
        }
        _record_position_exit_review_event(
            session,
            position=position,
            event_type="position_exit_review_execution_blocked",
            status="blocked",
            recommendation=recommendation,
            reason_codes=[POSITION_EXIT_REVIEW_STALE_SYNC_REASON_CODE],
            review=review,
            extra={"stale_scopes": stale_scopes, "sync_freshness_summary": sync_summary},
        )
        return context

    if str(protection_state.get("status") or "").lower() != "protected":
        reason_codes = [
            POSITION_EXIT_REVIEW_PROTECTION_UNVERIFIED_REASON_CODE,
            *_protection_state_reason_codes(protection_state),
        ]
        context["position_exit_review_execution"] = {
            "status": "blocked",
            "recommendation": recommendation,
            "reason_codes": reason_codes,
            "protection_state": protection_state,
        }
        _record_position_exit_review_event(
            session,
            position=position,
            event_type="position_exit_review_execution_blocked",
            status="blocked",
            recommendation=recommendation,
            reason_codes=reason_codes,
            review=review,
            extra={"protection_state": protection_state},
        )
        return context

    context = dict(context)
    local_filter_reason_codes, local_filter_payload = _position_exit_review_local_filter_block_reasons(
        position,
        context,
        recommendation,
    )
    if local_filter_reason_codes:
        context["position_exit_review_execution"] = {
            "status": "blocked",
            "recommendation": recommendation,
            "reason_codes": local_filter_reason_codes,
            "source": "position_exit_review",
            "local_filter": local_filter_payload,
            "reduce_only_or_close_only_required": True,
        }
        _record_position_exit_review_event(
            session,
            position=position,
            event_type="position_exit_review_execution_blocked",
            status="blocked",
            recommendation=recommendation,
            reason_codes=local_filter_reason_codes,
            review=review,
            extra={"local_filter": local_filter_payload},
        )
        return context

    action_status = "candidate_applied"
    action_reason_codes: list[str] = []
    if recommendation == "partial_take_profit":
        if bool(context.get("partial_take_profit_taken")):
            action_status = "blocked"
            action_reason_codes = ["POSITION_EXIT_REVIEW_PARTIAL_TP_ALREADY_TAKEN"]
        else:
            _append_unique_reason_codes(
                context,
                POSITION_EXIT_REVIEW_PARTIAL_TP_REASON_CODE,
                "POSITION_MANAGEMENT_PARTIAL_TAKE_PROFIT",
                "POSITION_MANAGEMENT_LOCK_PARTIAL_PROFIT",
            )
            _append_unique_applied_candidates(
                context,
                POSITION_EXIT_REVIEW_PARTIAL_TP_REASON_CODE,
                "POSITION_MANAGEMENT_PARTIAL_TAKE_PROFIT",
            )
            action_reason_codes = [POSITION_EXIT_REVIEW_PARTIAL_TP_REASON_CODE]
    elif recommendation == "full_take_profit":
        _append_unique_reason_codes(context, POSITION_EXIT_REVIEW_FULL_TP_REASON_CODE)
        _append_unique_applied_candidates(context, POSITION_EXIT_REVIEW_FULL_TP_REASON_CODE)
        action_reason_codes = [POSITION_EXIT_REVIEW_FULL_TP_REASON_CODE]
    elif recommendation == "reduce_risk_only":
        _append_unique_reason_codes(context, POSITION_EXIT_REVIEW_REDUCE_REASON_CODE)
        _append_unique_applied_candidates(context, POSITION_EXIT_REVIEW_REDUCE_REASON_CODE)
        action_reason_codes = [POSITION_EXIT_REVIEW_REDUCE_REASON_CODE]
    elif recommendation == "move_to_breakeven":
        candidate_stop = _to_float(context.get("break_even_stop_loss"))
        if candidate_stop > 0 and _is_more_protective_stop(position.side, position.stop_loss, candidate_stop):
            context["tightened_stop_loss"] = candidate_stop
            _append_unique_applied_candidates(
                context,
                POSITION_EXIT_REVIEW_BREAKEVEN_REASON_CODE,
                "POSITION_MANAGEMENT_BREAK_EVEN",
            )
            action_reason_codes = [POSITION_EXIT_REVIEW_BREAKEVEN_REASON_CODE]
        else:
            action_status = "blocked"
            action_reason_codes = ["POSITION_EXIT_REVIEW_BREAKEVEN_NOT_MORE_PROTECTIVE"]
    elif recommendation == "tighten_trailing":
        candidate_stop = _to_float(context.get("tightened_stop_loss"))
        if candidate_stop > 0 and _is_more_protective_stop(position.side, position.stop_loss, candidate_stop):
            _append_unique_applied_candidates(context, POSITION_EXIT_REVIEW_TIGHTEN_REASON_CODE)
            action_reason_codes = [POSITION_EXIT_REVIEW_TIGHTEN_REASON_CODE]
        else:
            action_status = "blocked"
            action_reason_codes = ["POSITION_EXIT_REVIEW_TIGHTEN_NOT_MORE_PROTECTIVE"]

    context["position_exit_review_execution"] = {
        "status": action_status,
        "recommendation": recommendation,
        "reason_codes": action_reason_codes,
        "source": "position_exit_review",
        "reduce_only_or_close_only_required": True,
    }
    _record_position_exit_review_event(
        session,
        position=position,
        event_type="position_exit_review_execution_candidate",
        status=action_status,
        recommendation=recommendation,
        reason_codes=action_reason_codes,
        review=review,
        extra={"context": context["position_exit_review_execution"]},
    )
    return context


def _is_protective_order(order_payload: dict[str, object]) -> bool:
    order_type = str(order_payload.get("type", "")).upper()
    return order_type.startswith("STOP") or order_type.startswith("TAKE_PROFIT")


def _protective_order_identity(order_payload: dict[str, object]) -> dict[str, object]:
    return {
        "order_id": order_payload.get("orderId") or order_payload.get("order_id"),
        "client_order_id": order_payload.get("clientOrderId") or order_payload.get("client_order_id"),
        "type": str(order_payload.get("type") or "").upper(),
        "side": str(order_payload.get("side") or "").upper() or None,
        "position_side": _exchange_order_position_side(order_payload),
        "reduce_only": _to_bool(order_payload.get("reduceOnly") or order_payload.get("reduce_only")),
        "close_position": _to_bool(order_payload.get("closePosition") or order_payload.get("close_position")),
        "quantity": _protective_order_quantity(order_payload),
    }


def _expected_protective_order_side(position: Position) -> str | None:
    if position.side == "long":
        return "SELL"
    if position.side == "short":
        return "BUY"
    return None


def _protective_order_quantity(order_payload: dict[str, object]) -> float:
    for key in ("origQty", "quantity", "qty", "q"):
        quantity = _to_float(order_payload.get(key), 0.0)
        if quantity > 0:
            return quantity
    return 0.0


def _protective_quantity_matches_position(
    position: Position,
    order_payload: dict[str, object],
    *,
    bucket: str | None = None,
) -> bool:
    if _to_bool(order_payload.get("closePosition") or order_payload.get("close_position")):
        return True
    quantity = _protective_order_quantity(order_payload)
    if quantity <= 0:
        return False
    expected_quantity = abs(position.quantity)
    if bucket == "take_profit":
        take_profit_policy = _take_profit_execution_policy(position)
        if take_profit_policy.get("mode") == "partial_reduce":
            expected_quantity = abs(position.quantity) * max(
                min(_to_float(take_profit_policy.get("partial_take_profit_fraction"), PARTIAL_TAKE_PROFIT_FRACTION), 0.99),
                0.01,
            )
    tolerance = max(expected_quantity * 0.001, 1e-8)
    return abs(quantity - expected_quantity) <= tolerance


def _protective_order_has_reduce_only_guard(order_payload: dict[str, object]) -> bool:
    return any(
        _to_bool(order_payload.get(key))
        for key in ("reduceOnly", "reduce_only", "closePosition", "close_position", "closeOnly", "close_only")
    )


def _protective_order_side_matches_position(
    position: Position,
    order_payload: dict[str, object],
) -> bool:
    expected_side = _expected_protective_order_side(position)
    if expected_side is None:
        return True
    order_side = str(order_payload.get("side") or "").strip().upper()
    if not order_side:
        return True
    return order_side == expected_side


def _is_reduce_only_take_profit_limit(position: Position, order_payload: dict[str, object]) -> bool:
    order_type = str(order_payload.get("type", "")).upper()
    if order_type != "LIMIT" or not _to_bool(order_payload.get("reduceOnly")):
        return False
    price = _to_float(order_payload.get("price"))
    if price <= 0:
        return False
    configured_take_profit = _to_float(position.take_profit)
    if configured_take_profit > 0:
        distance_bps = abs(price - configured_take_profit) / configured_take_profit * 10_000
        if distance_bps > 2.0:
            return False
    reference_price = position.entry_price if position.entry_price > 0 else position.mark_price
    if reference_price <= 0:
        return False
    if position.side == "long":
        return price > reference_price
    if position.side == "short":
        return price < reference_price
    return False


def _protective_bucket_for_position(position: Position, order_payload: dict[str, object]) -> str | None:
    bucket = _protective_bucket(order_payload)
    if bucket is not None:
        return bucket
    if _is_reduce_only_take_profit_limit(position, order_payload):
        return "take_profit"
    return None


def _build_protection_state(
    position: Position | None,
    open_orders: list[dict[str, object]],
    *,
    position_mode: str = POSITION_MODE_ONE_WAY,
) -> dict[str, object]:
    if position is None or position.status != "open" or position.quantity <= 0:
        return {
            "status": "flat",
            "protected": True,
            "has_stop_loss": False,
            "has_take_profit": False,
            "protective_order_count": 0,
            "protective_order_ids": [],
            "missing_components": [],
        }
    relevant_orders = _filter_orders_for_position_context(
        open_orders,
        position_mode=position_mode,
        exchange_position_side=_position_metadata_side(position),
    )
    active_relevant_orders = [
        item
        for item in relevant_orders
        if str(item.get("status") or item.get("X") or "").upper() not in INACTIVE_PROTECTIVE_ORDER_STATUSES
    ]
    protective_orders = [
        item for item in active_relevant_orders if _protective_bucket_for_position(position, item) is not None
    ]
    has_stop_loss = any(_protective_bucket_for_position(position, item) == "stop_loss" for item in protective_orders)
    has_take_profit = any(_protective_bucket_for_position(position, item) == "take_profit" for item in protective_orders)
    take_profit_required = _take_profit_required_for_protection(position)
    take_profit_policy = _take_profit_execution_policy(position)
    missing_components: list[str] = []
    reason_codes: list[str] = []
    health_issues: list[dict[str, object]] = []
    if not has_stop_loss:
        missing_components.append("stop_loss")
        reason_codes.append(PROTECTIVE_STOP_LOSS_MISSING_REASON_CODE)
    if take_profit_required and not has_take_profit:
        missing_components.append("take_profit")
        reason_codes.append(PROTECTIVE_TAKE_PROFIT_MISSING_REASON_CODE)

    for item in protective_orders:
        bucket = _protective_bucket_for_position(position, item)
        order_identity = _protective_order_identity(item)
        if not _protective_order_has_reduce_only_guard(item):
            health_issues.append(
                {
                    "reason_code": PROTECTIVE_ORDER_REDUCE_ONLY_MISSING_REASON_CODE,
                    "component": bucket or "protective_order",
                    "order": order_identity,
                }
            )
            reason_codes.append(PROTECTIVE_ORDER_REDUCE_ONLY_MISSING_REASON_CODE)
        if not _protective_order_side_matches_position(position, item):
            health_issues.append(
                {
                    "reason_code": PROTECTIVE_ORDER_SIDE_MISMATCH_REASON_CODE,
                    "component": bucket or "protective_order",
                    "expected_side": _expected_protective_order_side(position),
                    "order": order_identity,
                }
            )
            reason_codes.append(PROTECTIVE_ORDER_SIDE_MISMATCH_REASON_CODE)
        if not _protective_quantity_matches_position(position, item, bucket=bucket):
            health_issues.append(
                {
                    "reason_code": PROTECTIVE_ORDER_QUANTITY_MISMATCH_REASON_CODE,
                    "component": bucket or "protective_order",
                    "expected_quantity": position.quantity,
                    "order": order_identity,
                }
            )
            reason_codes.append(PROTECTIVE_ORDER_QUANTITY_MISMATCH_REASON_CODE)

    reason_codes = list(dict.fromkeys(reason_codes))
    health_components = list(
        dict.fromkeys(
            [
                *missing_components,
                *[
                    str(item.get("component") or "protective_order")
                    for item in health_issues
                    if item.get("component") not in {None, ""}
                ],
            ]
        )
    )
    status = "protected"
    if missing_components:
        status = "missing"
    elif health_issues:
        status = "invalid"
    return {
        "status": status,
        "protected": status == "protected",
        "has_stop_loss": has_stop_loss,
        "has_take_profit": has_take_profit,
        "take_profit_required": take_profit_required,
        "take_profit_execution_policy": take_profit_policy,
        "protective_order_count": len(protective_orders),
        "protective_order_ids": [str(item.get("orderId", "")) for item in protective_orders if item.get("orderId")],
        "missing_components": missing_components,
        "health_components": health_components,
        "health_issues": health_issues,
        "reason_codes": reason_codes,
        "critical_reason_codes": reason_codes,
        "exchange_position_side": _position_metadata_side(position),
        "position_mode": position_mode,
    }


def _build_unverified_protection_state(
    position: Position | None,
    *,
    reason_code: str,
    detail: str,
    deadline_at: datetime | None = None,
) -> dict[str, object]:
    return {
        "status": "unverified",
        "protected": False,
        "has_stop_loss": False,
        "has_take_profit": False,
        "protective_order_count": 0,
        "protective_order_ids": [],
        "missing_components": ["protective_orders"] if position is not None and position.quantity > 0 else [],
        "verification_status": "unverified",
        "blocked_reason_code": reason_code,
        "blocked_reason": detail,
        "verification_deadline_at": deadline_at.isoformat() if deadline_at is not None else None,
        "exchange_position_side": _position_metadata_side(position),
    }


def _protection_state_blocks_entry(protection_state: dict[str, object]) -> bool:
    return str(protection_state.get("status") or "").lower() not in {"flat", "protected"}


def _protection_state_reason_codes(protection_state: dict[str, object]) -> list[str]:
    raw_codes = protection_state.get("reason_codes")
    reason_codes = [
        str(item)
        for item in (raw_codes if isinstance(raw_codes, list) else [])
        if str(item or "").strip()
    ]
    missing_components = {
        str(item)
        for item in _get_string_list(protection_state, "missing_components")
        if str(item or "").strip()
    }
    if "stop_loss" in missing_components:
        reason_codes.append(PROTECTIVE_STOP_LOSS_MISSING_REASON_CODE)
    if "take_profit" in missing_components:
        reason_codes.append(PROTECTIVE_TAKE_PROFIT_MISSING_REASON_CODE)
    return list(dict.fromkeys(reason_codes))


def _protection_state_blocking_components(protection_state: dict[str, object]) -> list[str]:
    components = _get_string_list(protection_state, "health_components")
    if not components:
        components = _get_string_list(protection_state, "missing_components")
    if not components:
        components = _protection_state_reason_codes(protection_state)
    return list(dict.fromkeys(components))


def _record_protection_health_failure(
    session: Session,
    settings_row: Setting,
    *,
    symbol: str,
    position: Position | None,
    protection_state: dict[str, object],
    trigger_source: str,
    correlation_ids: dict[str, Any] | None = None,
) -> None:
    if not _protection_state_blocks_entry(protection_state):
        return
    reason_codes = _protection_state_reason_codes(protection_state)
    payload = {
        "symbol": symbol,
        "trigger_source": trigger_source,
        "reason_code": "PROTECTION_STATE_UNVERIFIED",
        "reason_codes": reason_codes,
        "position_id": position.id if position is not None else None,
        "position_side": position.side if position is not None else None,
        "position_quantity": position.quantity if position is not None else 0.0,
        "protection_state": protection_state,
    }
    record_protective_order_health_event(
        session,
        symbol=symbol,
        entity_id=str(position.id if position is not None else symbol),
        severity="critical",
        message="Live position protective order health check failed.",
        payload=payload,
        correlation_ids=correlation_ids,
    )


def _get_string_list(payload: dict[str, object], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item not in {None, ""}]


def _protection_lifecycle_payload(
    lifecycle: ProtectionLifecycleSnapshot | None,
) -> dict[str, object] | None:
    if lifecycle is None:
        return None
    return lifecycle.model_dump(mode="json")


def _initialize_protection_lifecycle(
    *,
    symbol: str,
    trigger_source: str,
    parent_order: Order | None,
) -> ProtectionLifecycleSnapshot:
    return ProtectionLifecycleSnapshot(
        symbol=symbol,
        trigger_source=trigger_source,
        parent_order_id=parent_order.id if parent_order is not None else None,
    )


def _sync_protection_lifecycle_snapshot(
    lifecycle: ProtectionLifecycleSnapshot | None,
    *,
    requested_components: list[str] | None = None,
    requested_order_types: list[str] | None = None,
    created_order_ids: list[int] | None = None,
    verification_detail: dict[str, object] | None = None,
) -> None:
    if lifecycle is None:
        return
    if requested_components is not None:
        lifecycle.requested_components = [str(item) for item in requested_components if item not in {None, ""}]
    if requested_order_types is not None:
        lifecycle.requested_order_types = [str(item) for item in requested_order_types if item not in {None, ""}]
    if created_order_ids is not None:
        merged_ids = [int(item) for item in lifecycle.created_order_ids]
        for item in created_order_ids:
            normalized = int(item)
            if normalized not in merged_ids:
                merged_ids.append(normalized)
        lifecycle.created_order_ids = merged_ids
    if verification_detail is not None:
        lifecycle.verification_detail = dict(verification_detail)


def _persist_protection_lifecycle(
    session: Session,
    parent_order: Order | None,
    lifecycle: ProtectionLifecycleSnapshot | None,
) -> None:
    if parent_order is None or lifecycle is None:
        return
    metadata = parent_order.metadata_json if isinstance(parent_order.metadata_json, dict) else {}
    parent_order.metadata_json = {
        **metadata,
        "protection_lifecycle": _protection_lifecycle_payload(lifecycle),
    }
    session.add(parent_order)
    session.flush()


def _transition_protection_lifecycle(
    session: Session,
    *,
    lifecycle: ProtectionLifecycleSnapshot | None,
    parent_order: Order | None,
    state: ProtectionLifecycleState,
    transition_reason: str,
    detail: dict[str, object] | None = None,
    requested_components: list[str] | None = None,
    requested_order_types: list[str] | None = None,
    created_order_ids: list[int] | None = None,
    verification_detail: dict[str, object] | None = None,
    correlation_ids: dict[str, Any] | None = None,
) -> None:
    if lifecycle is None:
        return
    transition_detail = dict(detail or {})
    _sync_protection_lifecycle_snapshot(
        lifecycle,
        requested_components=requested_components,
        requested_order_types=requested_order_types,
        created_order_ids=created_order_ids,
        verification_detail=verification_detail,
    )
    previous_state = lifecycle.state
    lifecycle.state = state
    lifecycle.transitions.append(
        ProtectionLifecycleTransition(
            from_state=previous_state,
            to_state=state,
            transition_reason=transition_reason,
            transitioned_at=utcnow_naive(),
            detail=transition_detail,
        )
    )
    _persist_protection_lifecycle(session, parent_order, lifecycle)
    record_audit_event(
        session,
        event_type="protection_lifecycle_transition",
        entity_type="order" if parent_order is not None else "position",
        entity_id=str(parent_order.id if parent_order is not None else lifecycle.symbol),
        severity="warning" if state == "verify_failed" else "info",
        message=f"Protection lifecycle transitioned to {state}.",
        payload={
            "symbol": lifecycle.symbol,
            "trigger_source": lifecycle.trigger_source,
            "from_state": previous_state,
            "to_state": state,
            "transition_reason": transition_reason,
            "detail": transition_detail,
            "protection_lifecycle": _protection_lifecycle_payload(lifecycle),
        },
        correlation_ids=correlation_ids,
    )


def _get_protection_verify_blocks(settings_row: Setting) -> dict[str, dict[str, object]]:
    recovery = get_protection_recovery_detail(settings_row)
    raw_blocks = recovery.get("verification_blocks")
    if not isinstance(raw_blocks, dict):
        return {}
    return {
        str(symbol): dict(detail)
        for symbol, detail in raw_blocks.items()
        if isinstance(detail, dict)
    }


def _get_symbol_protection_verify_block(settings_row: Setting, symbol: str) -> dict[str, object] | None:
    blocks = _get_protection_verify_blocks(settings_row)
    return blocks.get(symbol.upper()) or blocks.get(symbol)


def _write_protection_verify_blocks(
    session: Session,
    settings_row: Setting,
    *,
    blocks: dict[str, dict[str, object]],
) -> None:
    pause_detail = dict(settings_row.pause_reason_detail or {})
    recovery = dict(pause_detail.get("protection_recovery") or {})
    recovery["verification_blocks"] = blocks
    pause_detail["protection_recovery"] = recovery
    settings_row.pause_reason_detail = pause_detail
    session.add(settings_row)
    session.flush()


def _set_symbol_protection_verify_block(
    session: Session,
    settings_row: Setting,
    *,
    symbol: str,
    trigger_source: str,
    detail: str,
    protection_state: dict[str, object],
    created_order_ids: list[int],
    protection_lifecycle: ProtectionLifecycleSnapshot | None,
) -> None:
    normalized_symbol = symbol.upper()
    blocks = _get_protection_verify_blocks(settings_row)
    reason_code = str(protection_state.get("blocked_reason_code") or PROTECTION_VERIFY_FAILED_REASON_CODE)
    blocks[normalized_symbol] = {
        "status": "verify_failed",
        "blocked": True,
        "reason_code": reason_code,
        "blocked_reason_code": reason_code,
        "blocked_reason": detail,
        "verification_status": str(protection_state.get("verification_status") or "verify_failed"),
        "verification_deadline_at": protection_state.get("verification_deadline_at"),
        "trigger_source": trigger_source,
        "blocked_at": utcnow_naive().isoformat(),
        "last_error": detail,
        "protection_state": dict(protection_state),
        "created_order_ids": [int(item) for item in created_order_ids],
        "protection_lifecycle": _protection_lifecycle_payload(protection_lifecycle),
    }
    _write_protection_verify_blocks(session, settings_row, blocks=blocks)


def _clear_symbol_protection_verify_block(
    session: Session,
    settings_row: Setting,
    *,
    symbol: str,
) -> None:
    normalized_symbol = symbol.upper()
    blocks = _get_protection_verify_blocks(settings_row)
    if normalized_symbol not in blocks and symbol not in blocks:
        return
    blocks.pop(normalized_symbol, None)
    blocks.pop(symbol, None)
    _write_protection_verify_blocks(session, settings_row, blocks=blocks)


def _merge_verified_protective_orders(
    open_orders: list[dict[str, object]],
    verified_orders: list[dict[str, object]],
) -> list[dict[str, object]]:
    merged = list(open_orders)
    existing_keys = {
        (str(item.get("orderId", "")), str(item.get("clientOrderId", "")))
        for item in merged
    }
    for payload in verified_orders:
        key = (str(payload.get("orderId", "")), str(payload.get("clientOrderId", "")))
        if key in existing_keys:
            continue
        merged.append(payload)
        existing_keys.add(key)
    return merged


def _protection_verify_status_is_active(status: object) -> bool:
    normalized = str(status or "").strip().upper()
    if not normalized:
        return True
    return normalized not in INACTIVE_PROTECTIVE_ORDER_STATUSES


def _verify_created_protective_orders(
    session: Session,
    client: BinanceClient,
    *,
    symbol: str,
    order_ids: list[int],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    verified_orders: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    for created_order_id in order_ids:
        row = session.get(Order, created_order_id)
        if row is None:
            failures.append(
                {
                    "order_id": created_order_id,
                    "error": "LOCAL_PROTECTIVE_ORDER_ROW_MISSING",
                }
            )
            continue
        verified_payload: dict[str, object] | None = None
        verification_error: str | None = None
        for _attempt in range(1, PROTECTION_VERIFY_FETCH_ATTEMPTS + 1):
            try:
                payload = _fetch_exchange_order(
                    client,
                    symbol=symbol,
                    order_type=row.order_type,
                    order_id=row.external_order_id,
                    client_order_id=row.client_order_id,
                )
            except Exception as exc:
                verification_error = f"VERIFY_LOOKUP_FAILED:{exc}"
                continue

            if not _is_protective_order(payload) and not _is_local_protective_order_row(row):
                verification_error = "VERIFY_LOOKUP_RETURNED_NON_PROTECTIVE_ORDER"
                continue
            if str(payload.get("type", "")).upper() != row.order_type.upper():
                verification_error = (
                    f"VERIFY_LOOKUP_TYPE_MISMATCH:{row.order_type.upper()}:{str(payload.get('type', '')).upper()}"
                )
                continue
            if not _protection_verify_status_is_active(payload.get("status")):
                verification_error = f"VERIFY_LOOKUP_INACTIVE_STATUS:{str(payload.get('status', '')).upper()}"
                continue

            row = _upsert_exchange_order_row(
                session,
                symbol=symbol,
                requested_price=row.requested_price,
                requested_quantity=row.requested_quantity,
                order_type=row.order_type.upper(),
                side=row.side,
                exchange_order=payload,
                decision_run_id=row.decision_run_id,
                risk_row=session.get(RiskCheck, row.risk_check_id) if row.risk_check_id is not None else None,
                reduce_only=row.reduce_only,
                close_only=row.close_only,
                parent_order_id=row.parent_order_id,
            )
            verified_payload = payload
            break

        if verified_payload is None:
            failures.append(
                {
                    "order_id": created_order_id,
                    "external_order_id": row.external_order_id,
                    "client_order_id": row.client_order_id,
                    "order_type": row.order_type.upper(),
                    "error": verification_error or "VERIFY_LOOKUP_FAILED",
                }
            )
            continue
        verified_orders.append(verified_payload)
    return verified_orders, failures


def _format_protective_verification_failures(failures: list[dict[str, object]]) -> str:
    details = [
        f"{item.get('order_type', 'UNKNOWN')}:{item.get('error', 'VERIFY_LOOKUP_FAILED')}"
        for item in failures
    ]
    return "Protective order verify refetch failed: " + ", ".join(details)


def _protective_bucket(order_payload: dict[str, object]) -> str | None:
    order_type = str(order_payload.get("type", "")).upper()
    if order_type.startswith("STOP"):
        return "stop_loss"
    if order_type.startswith("TAKE_PROFIT"):
        return "take_profit"
    return None


def _is_protective_order_type_name(order_type: str | None) -> bool:
    if not order_type:
        return False
    normalized = order_type.upper()
    return normalized.startswith("STOP") or normalized.startswith("TAKE_PROFIT")


def _is_local_protective_order_row(order: Order) -> bool:
    if _is_protective_order_type_name(order.order_type):
        return True
    metadata = order.metadata_json if isinstance(order.metadata_json, dict) else {}
    return str(metadata.get("protective_component") or "") in {"stop_loss", "take_profit"}


def _is_algo_order_payload(order_payload: dict[str, object]) -> bool:
    if _is_protective_order(order_payload):
        return True
    return any(key in order_payload for key in ("algoId", "clientAlgoId"))


def _open_order_identity_sets(open_orders: list[dict[str, object]]) -> tuple[set[str], set[str]]:
    remote_order_ids: set[str] = set()
    remote_client_order_ids: set[str] = set()
    for item in open_orders:
        for key in ("orderId", "algoId"):
            value = str(item.get(key) or "")
            if value:
                remote_order_ids.add(value)
        for key in ("clientOrderId", "clientAlgoId"):
            value = str(item.get(key) or "")
            if value:
                remote_client_order_ids.add(value)
    return remote_order_ids, remote_client_order_ids


def _local_order_is_present_remotely(
    order: Order,
    *,
    remote_order_ids: set[str],
    remote_client_order_ids: set[str],
) -> bool:
    external_order_id = str(order.external_order_id or "")
    client_order_id = str(order.client_order_id or "")
    return bool(
        (external_order_id and external_order_id in remote_order_ids)
        or (client_order_id and client_order_id in remote_client_order_ids)
    )


def reconcile_closed_position_protective_orders(
    session: Session,
    *,
    symbol: str,
    open_orders: list[dict[str, object]],
    observed_at: datetime | None = None,
    source: str = "exchange_sync",
) -> list[Order]:
    observed_at = observed_at or utcnow_naive()
    remote_order_ids, remote_client_order_ids = _open_order_identity_sets(open_orders)
    has_remote_open_orders = bool(open_orders)
    with session.no_autoflush:
        candidates = list(
            session.scalars(
                select(Order)
                .join(Position, Order.position_id == Position.id)
                .where(
                    Order.mode == "live",
                    Order.symbol == symbol.upper(),
                    Order.status.notin_(FINAL_ORDER_STATUSES),
                    or_(Order.reduce_only.is_(True), Order.close_only.is_(True)),
                    or_(Position.status != "open", Position.quantity <= 0),
                )
            )
    )
    reconciled: list[Order] = []
    for order in candidates:
        if not _is_local_protective_order_row(order):
            continue
        if _local_order_is_present_remotely(
            order,
            remote_order_ids=remote_order_ids,
            remote_client_order_ids=remote_client_order_ids,
        ):
            continue
        if has_remote_open_orders and not order.external_order_id and not order.client_order_id:
            continue
        previous_status = order.status
        previous_exchange_status = order.exchange_status
        order.status = "canceled"
        order.exchange_status = "CANCELED"
        order.last_exchange_update_at = observed_at
        reason_codes = [str(code) for code in (order.reason_codes or []) if code]
        if CLOSED_POSITION_PROTECTIVE_ORDER_RECONCILED_REASON_CODE not in reason_codes:
            reason_codes.append(CLOSED_POSITION_PROTECTIVE_ORDER_RECONCILED_REASON_CODE)
        order.reason_codes = reason_codes
        metadata = _as_object_dict(order.metadata_json)
        metadata["closed_position_protective_order_reconciliation"] = {
            "source": source,
            "reason_code": CLOSED_POSITION_PROTECTIVE_ORDER_RECONCILED_REASON_CODE,
            "reconciled_at": observed_at.isoformat(),
            "remote_open_order_absent": True,
            "previous_status": previous_status,
            "previous_exchange_status": previous_exchange_status,
            "position_id": order.position_id,
        }
        order.metadata_json = metadata
        session.add(order)
        reconciled.append(order)
        record_audit_event(
            session,
            event_type="protective_order_reconciled",
            entity_type="order",
            entity_id=str(order.id),
            severity="info",
            message="Closed-position protective order was reconciled from local pending state.",
            payload={
                "symbol": order.symbol,
                "position_id": order.position_id,
                "order_id": order.id,
                "external_order_id": order.external_order_id,
                "client_order_id": order.client_order_id,
                "reason_code": CLOSED_POSITION_PROTECTIVE_ORDER_RECONCILED_REASON_CODE,
                "source": source,
                "previous_status": previous_status,
                "previous_exchange_status": previous_exchange_status,
            },
        )
    if reconciled:
        session.flush()
    return reconciled


def _fetch_exchange_order(
    client: BinanceClient,
    *,
    symbol: str,
    order_type: str | None,
    order_id: str | None = None,
    client_order_id: str | None = None,
) -> dict[str, object]:
    if hasattr(client, "fetch_order"):
        try:
            return client.fetch_order(  # type: ignore[attr-defined]
                symbol=symbol,
                order_type=order_type,
                order_id=order_id,
                client_order_id=client_order_id,
            )
        except TypeError:
            pass
    if _is_protective_order_type_name(order_type) and hasattr(client, "get_algo_order"):
        try:
            return client.get_algo_order(algo_id=order_id, client_algo_id=client_order_id)  # type: ignore[attr-defined]
        except Exception:
            if not hasattr(client, "get_order"):
                raise
    return client.get_order(symbol=symbol, order_id=order_id, client_order_id=client_order_id)


def _cancel_exchange_order(
    client: BinanceClient,
    *,
    symbol: str,
    order_payload: dict[str, object] | None = None,
    order_type: str | None = None,
    order_id: str | None = None,
    client_order_id: str | None = None,
) -> dict[str, object]:
    payload = order_payload or {}
    remote_order_id = order_id or str(payload.get("orderId", "")) or None
    remote_client_order_id = client_order_id or str(payload.get("clientOrderId", "")) or None
    effective_order_type = order_type or str(payload.get("type", "") or "")
    if hasattr(client, "cancel_exchange_order"):
        try:
            return client.cancel_exchange_order(  # type: ignore[attr-defined]
                symbol=symbol,
                order_type=effective_order_type,
                order_id=remote_order_id,
                client_order_id=remote_client_order_id,
            )
        except TypeError:
            pass
    if (_is_algo_order_payload(payload) or _is_protective_order_type_name(effective_order_type)) and hasattr(
        client,
        "cancel_algo_order",
    ):
        try:
            return client.cancel_algo_order(  # type: ignore[attr-defined]
                algo_id=remote_order_id,
                client_algo_id=remote_client_order_id,
            )
        except Exception:
            if not hasattr(client, "cancel_order"):
                raise
    return client.cancel_order(
        symbol=symbol,
        order_id=remote_order_id,
        client_order_id=remote_client_order_id,
    )


def _cancel_duplicate_protective_orders(
    session: Session,
    client: BinanceClient,
    *,
    symbol: str,
    open_orders: list[dict[str, object]],
    position: Position | None = None,
    preferred_order_ids: list[int] | None = None,
) -> None:
    preferred = {str(item) for item in (preferred_order_ids or [])}
    orders_by_bucket: dict[str, list[dict[str, object]]] = {"stop_loss": [], "take_profit": []}
    for item in open_orders:
        bucket = _protective_bucket_for_position(position, item) if position is not None else _protective_bucket(item)
        if bucket is None:
            continue
        orders_by_bucket[bucket].append(item)

    for _bucket, items in orders_by_bucket.items():
        if len(items) <= 1:
            continue
        keep_item = next((item for item in items if str(item.get("orderId", "")) in preferred), items[0])
        keep_order_id = str(keep_item.get("orderId", ""))
        for item in items:
            order_id = str(item.get("orderId", ""))
            if order_id == keep_order_id:
                continue
            client_order_id = str(item.get("clientOrderId", ""))
            _cancel_exchange_order(
                client,
                symbol=symbol,
                order_payload=item,
                order_id=order_id or None,
                client_order_id=client_order_id or None,
            )
            local = None
            if order_id:
                local = session.scalar(select(Order).where(Order.external_order_id == order_id).limit(1))
            if local is None and client_order_id:
                local = session.scalar(select(Order).where(Order.client_order_id == client_order_id).limit(1))
            if local is not None:
                local.status = "canceled"
                local.exchange_status = "CANCELED"
                local.last_exchange_update_at = utcnow_naive()
                session.add(local)
    session.flush()


def _record_sync_success(
    session: Session,
    settings_row: Setting,
    *,
    scope: str,
    detail: dict[str, object] | None = None,
    status: str = "synced",
    flush_state: bool = True,
) -> None:
    mark_sync_success(settings_row, scope=scope, detail=detail, status=status)
    record_binance_rest_success(
        settings_row,
        source=f"sync:{scope}",
        detail={"scope": scope},
    )
    session.add(settings_row)
    if flush_state:
        session.flush()


def _record_sync_issue(
    session: Session,
    settings_row: Setting,
    *,
    scope: str,
    status: str,
    reason_code: str,
    detail: dict[str, object] | None = None,
) -> None:
    mark_sync_issue(settings_row, scope=scope, status=status, reason_code=reason_code, detail=detail)
    session.add(settings_row)
    session.flush()


def _count_symbol_order_stream_events(stream_events: list[dict[str, Any]], *, symbol: str) -> int:
    order_keys: set[str] = set()
    fallback_count = 0
    for item in stream_events:
        related_categories = {
            str(category)
            for category in item.get("related_categories", [])
            if isinstance(category, str) and category
        }
        event_category = str(item.get("event_category") or "")
        if event_category not in {"order", "execution"} and not ({"order", "execution"} & related_categories):
            continue
        event_symbol = str(item.get("symbol") or "").upper()
        event_symbols = {
            str(value).upper()
            for value in item.get("symbols", [])
            if isinstance(value, str) and value
        }
        if symbol.upper() not in event_symbols and symbol.upper() != event_symbol:
            continue
        order_key = str(item.get("order_id") or item.get("client_order_id") or "")
        if order_key:
            order_keys.add(order_key)
        else:
            fallback_count += 1
    return len(order_keys) + fallback_count


def _record_user_stream_order_sync_fallback(
    session: Session,
    settings_row: Setting,
    *,
    symbol: str,
    reason_code: str,
    user_stream_summary: dict[str, Any] | None = None,
    flush_state: bool = True,
) -> dict[str, Any]:
    user_stream_summary = dict(user_stream_summary) if user_stream_summary is not None else get_user_stream_detail(settings_row)
    if str(user_stream_summary.get("status") or "") == "connected":
        set_user_stream_detail(
            settings_row,
            status="degraded",
            heartbeat_ok=False,
            stream_source=USER_STREAM_FALLBACK_SOURCE,
            last_error=reason_code,
            last_disconnected_at=utcnow_naive(),
        )
        session.add(settings_row)
        if flush_state:
            session.flush()
        user_stream_summary = get_user_stream_detail(settings_row)
    payload = {
        "symbol": symbol,
        "reason_code": reason_code,
        "user_stream_summary": user_stream_summary,
    }
    record_audit_event(
        session,
        event_type="user_stream_order_sync_fallback",
        entity_type="binance",
        entity_id=symbol,
        severity="warning",
        message="Live order synchronization fell back to REST reconciliation.",
        payload=payload,
    )
    record_health_event(
        session,
        component="user_stream",
        status="degraded",
        message="Live order synchronization fell back to REST reconciliation.",
        payload=payload,
    )
    return user_stream_summary


def _has_valid_protection_template(position: Position | None, stop_loss: float | None, take_profit: float | None) -> bool:
    if position is None or stop_loss is None or take_profit is None:
        return False
    reference_price = position.entry_price if position.entry_price > 0 else position.mark_price
    if reference_price <= 0:
        return False
    if position.side == "long":
        return stop_loss < reference_price and take_profit > reference_price
    return stop_loss > reference_price and take_profit < reference_price


def _is_more_protective_stop(side: str, current_stop: float | None, candidate_stop: float | None) -> bool:
    if candidate_stop is None:
        return False
    if current_stop is None:
        return True
    if side == "long":
        return candidate_stop > current_stop + 1e-9
    return candidate_stop < current_stop - 1e-9


def _is_break_even_stop_tighten_candidate(context: dict[str, object], tightened_stop_loss: float | None) -> bool:
    break_even_stop = _to_float(context.get("break_even_stop_loss"))
    if _is_effectively_zero(break_even_stop) or _is_effectively_zero(tightened_stop_loss):
        return False
    candidates = set(_get_string_list(context, "applied_rule_candidates"))
    return (
        "POSITION_MANAGEMENT_BREAK_EVEN" in candidates
        and abs(float(tightened_stop_loss) - float(break_even_stop)) <= 1e-9
    )


def _breakeven_event_payload(
    position: Position,
    context: dict[str, object],
    *,
    status: str,
    candidate_stop_loss: float | None,
    reason_codes: list[str] | None = None,
    risk_result: RiskCheckResult | None = None,
    risk_row: RiskCheck | None = None,
    protection_state: dict[str, object] | None = None,
    shadow: bool | None = None,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "symbol": position.symbol,
        "status": status,
        "reason_code": reason_codes[0] if reason_codes else None,
        "reason_codes": list(reason_codes or []),
        "risk_check_id": risk_row.id if risk_row is not None else None,
        "position_id": position.id,
        "position_side": position.side,
        "entry_price": position.entry_price,
        "mark_price": position.mark_price,
        "current_stop_loss": position.stop_loss,
        "candidate_stop_loss": candidate_stop_loss,
        "current_r_multiple": context.get("current_r_multiple"),
        "trigger_r": context.get("break_even_trigger_r"),
        "breakeven_lock_bps": context.get("breakeven_lock_bps"),
        "breakeven_min_hold_seconds": context.get("breakeven_min_hold_seconds"),
        "shadow": shadow,
        "would_move": status == "shadow_would_move",
        "approved": bool(risk_result.allowed) if risk_result is not None else None,
    }
    if risk_result is not None:
        payload["risk_guard"] = {
            "allowed": risk_result.allowed,
            "reason_codes": list(risk_result.reason_codes),
            "blocked_reason_codes": list(risk_result.blocked_reason_codes),
        }
    if protection_state is not None:
        payload["protective_state"] = protection_state
    if extra:
        payload.update(extra)
    return payload


def _replace_stop_loss_order(
    session: Session,
    *,
    client: BinanceClient,
    position: Position,
    symbol: str,
    stop_loss: float,
    decision_run_id: int | None,
    risk_row: RiskCheck | None,
    trigger_source: str,
    open_orders: list[dict[str, object]],
) -> dict[str, object]:
    exit_side = "SELL" if position.side == "long" else "BUY"
    cancelled_order_ids: list[str] = []
    for item in open_orders:
        if _protective_bucket(item) != "stop_loss":
            continue
        remote_order_id = str(item.get("orderId", ""))
        remote_client_order_id = str(item.get("clientOrderId", ""))
        _cancel_exchange_order(
            client,
            symbol=symbol,
            order_payload=item,
            order_id=remote_order_id or None,
            client_order_id=remote_client_order_id or None,
        )
        if remote_order_id:
            cancelled_order_ids.append(remote_order_id)
        local_order = None
        if remote_order_id:
            local_order = session.scalar(select(Order).where(Order.external_order_id == remote_order_id).limit(1))
        if local_order is None and remote_client_order_id:
            local_order = session.scalar(select(Order).where(Order.client_order_id == remote_client_order_id).limit(1))
        if local_order is not None:
            local_order.status = "canceled"
            local_order.exchange_status = "CANCELED"
            local_order.last_exchange_update_at = utcnow_naive()
            session.add(local_order)

    client_order_id, exchange_order, submit_request, submission_tracking = _safe_submit_order(
        client,
        symbol=symbol,
        side=exit_side,
        order_type="STOP_MARKET",
        stop_price=stop_loss,
        reduce_only=True,
        close_position=True,
        response_type="ACK",
        reference_price=position.entry_price if position.entry_price > 0 else position.mark_price,
        enforce_min_notional=False,
    )
    normalized_stop = _to_float(submit_request.get("stop_price"), stop_loss)
    order = _upsert_exchange_order_row(
        session,
        symbol=symbol,
        requested_price=normalized_stop,
        requested_quantity=position.quantity,
        order_type="STOP_MARKET",
        side=exit_side.lower(),
        exchange_order={**exchange_order, "clientOrderId": client_order_id},
        decision_run_id=decision_run_id,
        risk_row=risk_row,
        reduce_only=True,
        close_only=True,
        parent_order_id=None,
    )
    order.position_id = position.id
    order.metadata_json = {
        **(order.metadata_json or {}),
        "position_management": {
            "trigger_source": trigger_source,
            "applied_rule": "STOP_TIGHTENED",
            "tightened_stop_loss": normalized_stop,
        },
    }
    _apply_submission_tracking(
        order,
        client_order_id=client_order_id,
        submit_request=submit_request,
        submission_tracking=submission_tracking,
    )
    position.stop_loss = normalized_stop
    session.add(position)
    session.add(order)
    session.flush()
    _record_submission_recovery_event(
        session,
        order=order,
        symbol=symbol,
        submission_tracking=submission_tracking,
        context="position_management",
        requested_quantity=position.quantity,
        requested_price=normalized_stop,
    )
    record_position_management_event(
        session,
        event_type="position_management_stop_tightened",
        position_id=position.id,
        severity="info",
        message="Position management tightened the live stop loss.",
        payload={
            "symbol": symbol,
            "trigger_source": trigger_source,
            "cancelled_order_ids": cancelled_order_ids,
            "new_order_id": order.id,
            "tightened_stop_loss": normalized_stop,
        },
    )
    return {
        "status": "applied",
        "tightened_stop_loss": normalized_stop,
        "cancelled_order_ids": cancelled_order_ids,
        "order_id": order.id,
    }


def apply_position_management(
    session: Session,
    settings_row: Setting,
    *,
    symbol: str,
    feature_payload: FeaturePayload,
    decision_run_id: int | None = None,
    risk_row: RiskCheck | None = None,
    client: BinanceClient | None = None,
) -> dict[str, object]:
    position = get_open_position(session, symbol)
    context = build_position_management_context(position, feature_payload=feature_payload, settings_row=settings_row)
    if position is None:
        return {"status": "no_open_position", "position_management_context": context}

    store_position_management_context(position, context)
    session.add(position)
    session.flush()

    if not context.get("enabled"):
        return {"status": "disabled", "position_management_context": context}

    client = client or _build_client(settings_row)
    open_orders = client.get_open_orders(symbol)
    protection_state = _build_protection_state(position, open_orders)
    context = _apply_position_exit_review_to_context(
        session,
        settings_row,
        position,
        context,
        protection_state,
    )
    store_position_management_context(position, context)
    session.add(position)
    session.flush()
    tightened_stop_loss = _to_float(context.get("tightened_stop_loss"))
    break_even_stop = _to_float(context.get("break_even_stop_loss"))
    break_even_stop_candidate = _is_break_even_stop_tighten_candidate(context, tightened_stop_loss)
    if break_even_stop_candidate and _protection_state_blocks_entry(protection_state):
        _record_protection_health_failure(
            session,
            settings_row,
            symbol=symbol,
            position=position,
            protection_state=protection_state,
            trigger_source="position_management:breakeven",
        )
        protection_result: dict[str, object] | None = None
        if str(protection_state.get("status") or "").lower() == "missing":
            protection_result = _ensure_protected_position(
                session,
                settings_row,
                client,
                symbol=symbol,
                position=position,
                stop_loss=position.stop_loss,
                take_profit=position.take_profit,
                decision_run_id=decision_run_id,
                risk_row=risk_row,
                parent_order=None,
                trigger_source="position_management:breakeven",
                pause_reason_code="MISSING_PROTECTIVE_ORDERS",
            )
            protection_state = dict(protection_result.get("protection_state") or protection_state)
        reason_codes = list(
            dict.fromkeys(
                [
                    "BREAKEVEN_PROTECTIVE_RECOVERY_REQUIRED",
                    *_protection_state_reason_codes(protection_state),
                ]
            )
        )
        record_position_management_event(
            session,
            event_type="breakeven_stop_move_cancelled",
            position_id=position.id,
            severity="critical",
            message="Break-even stop move was cancelled because protective order recovery has priority.",
            payload=_breakeven_event_payload(
                position,
                context,
                status="protective_recovery_required",
                candidate_stop_loss=break_even_stop,
                reason_codes=reason_codes,
                protection_state=protection_state,
                extra={"protection_result": protection_result or {}},
            ),
        )
        session.flush()
        return {
            "status": "protective_recovery_required",
            "position_management_context": context,
            "position_management_action": {
                "status": "protective_recovery_required",
                "reason_codes": reason_codes,
                "candidate_stop_loss": break_even_stop,
            },
            "protection_state": protection_state,
            "protection_result": protection_result,
        }
    stop_can_tighten = (
        protection_state["status"] == "protected"
        and bool(protection_state.get("has_stop_loss"))
        and not _is_effectively_zero(tightened_stop_loss)
        and _is_more_protective_stop(position.side, position.stop_loss, tightened_stop_loss)
    )
    if stop_can_tighten:
        if break_even_stop_candidate:
            breakeven_risk_result, breakeven_risk_row = evaluate_breakeven_stop_move_risk_guard(
                session,
                settings_row,
                position,
                candidate_stop_loss=float(break_even_stop),
                context=context,
                protection_state=protection_state,
                decision_run_id=decision_run_id,
            )
            record_position_management_event(
                session,
                event_type="breakeven_stop_move_evaluated",
                position_id=position.id,
                severity="info" if breakeven_risk_result.allowed else "warning",
                message="Risk guard evaluated a break-even stop move candidate.",
                payload=_breakeven_event_payload(
                    position,
                    context,
                    status="risk_approved" if breakeven_risk_result.allowed else "risk_blocked",
                    candidate_stop_loss=break_even_stop,
                    reason_codes=list(breakeven_risk_result.blocked_reason_codes),
                    risk_result=breakeven_risk_result,
                    risk_row=breakeven_risk_row,
                    protection_state=protection_state,
                ),
            )
            if not breakeven_risk_result.allowed:
                session.flush()
                return {
                    "status": "blocked",
                    "position_management_context": context,
                    "protection_state": protection_state,
                    "position_management_action": {
                        "status": "risk_blocked",
                        "reason_codes": list(breakeven_risk_result.blocked_reason_codes),
                        "risk_result": breakeven_risk_result.model_dump(mode="json"),
                    },
                    "risk_result": breakeven_risk_result.model_dump(mode="json"),
                }

            breakeven_shadow = _flag_enabled(getattr(get_settings(), "breakeven_shadow", True))
            if breakeven_shadow:
                record_position_management_event(
                    session,
                    event_type="breakeven_stop_move_would_move",
                    position_id=position.id,
                    severity="info",
                    message="Break-even stop move candidate would move the stop; shadow mode left orders unchanged.",
                    payload=_breakeven_event_payload(
                        position,
                        context,
                        status="shadow_would_move",
                        candidate_stop_loss=break_even_stop,
                        risk_result=breakeven_risk_result,
                        risk_row=breakeven_risk_row,
                        protection_state=protection_state,
                        shadow=True,
                    ),
                )
                session.flush()
                return {
                    "status": "monitoring",
                    "position_management_context": context,
                    "protection_state": protection_state,
                    "position_management_action": {
                        "status": "shadow_would_move",
                        "candidate_stop_loss": break_even_stop,
                        "risk_result": breakeven_risk_result.model_dump(mode="json"),
                    },
                    "risk_result": breakeven_risk_result.model_dump(mode="json"),
                }

            record_position_management_event(
                session,
                event_type="breakeven_stop_move_attempted",
                position_id=position.id,
                severity="info",
                message="Attempting to move the live stop to break-even after risk guard approval.",
                payload=_breakeven_event_payload(
                    position,
                    context,
                    status="attempted",
                    candidate_stop_loss=break_even_stop,
                    risk_result=breakeven_risk_result,
                    risk_row=breakeven_risk_row,
                    protection_state=protection_state,
                    shadow=False,
                ),
            )
            try:
                applied = _replace_stop_loss_order(
                    session,
                    client=client,
                    position=position,
                    symbol=symbol,
                    stop_loss=float(tightened_stop_loss),
                    decision_run_id=decision_run_id,
                    risk_row=breakeven_risk_row,
                    trigger_source="breakeven",
                    open_orders=open_orders,
                )
            except Exception as exc:
                failure_payload = _breakeven_event_payload(
                    position,
                    context,
                    status="failed",
                    candidate_stop_loss=break_even_stop,
                    reason_codes=["BREAKEVEN_STOP_MOVE_FAILED"],
                    risk_result=breakeven_risk_result,
                    risk_row=breakeven_risk_row,
                    protection_state=protection_state,
                    shadow=False,
                    extra={"error": str(exc)},
                )
                record_position_management_event(
                    session,
                    event_type="breakeven_stop_move_failed",
                    position_id=position.id,
                    severity="critical",
                    message="Break-even stop move failed after risk guard approval.",
                    payload=failure_payload,
                )
                record_health_event(
                    session,
                    component="position_management",
                    status="critical",
                    message="Break-even stop move failed after risk guard approval.",
                    payload=failure_payload,
                )
                create_alert(
                    session,
                    category="position_management",
                    severity="critical",
                    title="Break-even stop move failed",
                    message="Risk-approved break-even stop move failed; position protection requires operator review.",
                    payload=failure_payload,
                )
                session.flush()
                return {
                    "status": "failed",
                    "position_management_context": context,
                    "protection_state": protection_state,
                    "position_management_action": {
                        "status": "failed",
                        "reason_codes": ["BREAKEVEN_STOP_MOVE_FAILED"],
                        "error": str(exc),
                    },
                    "risk_result": breakeven_risk_result.model_dump(mode="json"),
                }
            record_position_management_event(
                session,
                event_type="breakeven_stop_move_applied",
                position_id=position.id,
                severity="info",
                message="Break-even stop move was applied after risk guard approval.",
                payload=_breakeven_event_payload(
                    position,
                    context,
                    status="applied",
                    candidate_stop_loss=break_even_stop,
                    risk_result=breakeven_risk_result,
                    risk_row=breakeven_risk_row,
                    protection_state=protection_state,
                    shadow=False,
                    extra={
                        "new_stop_loss": applied["tightened_stop_loss"],
                        "cancelled_order_ids": applied.get("cancelled_order_ids", []),
                        "new_order_id": applied.get("order_id"),
                    },
                ),
            )
            record_position_management_event(
                session,
                event_type="moved_stop_to_breakeven",
                position_id=position.id,
                severity="info",
                message="Position management moved the live stop to break-even.",
                payload={
                    "symbol": symbol,
                    "decision_run_id": decision_run_id,
                    "risk_check_id": breakeven_risk_row.id,
                    "tightened_stop_loss": applied["tightened_stop_loss"],
                    "break_even_trigger_r": context.get("break_even_trigger_r"),
                    "breakeven_lock_bps": context.get("breakeven_lock_bps"),
                },
            )
            refreshed_open_orders = client.get_open_orders(symbol)
            protection_result = _ensure_protected_position(
                session,
                settings_row,
                client,
                symbol=symbol,
                position=position,
                stop_loss=position.stop_loss,
                take_profit=position.take_profit,
                decision_run_id=decision_run_id,
                risk_row=breakeven_risk_row,
                parent_order=None,
                trigger_source="breakeven",
                pause_reason_code="MISSING_PROTECTIVE_ORDERS",
            )
            return {
                "status": "applied",
                "position_management_context": context,
                "position_management_action": applied,
                "protection_state": _build_protection_state(position, refreshed_open_orders),
                "protection_result": protection_result,
                "risk_result": breakeven_risk_result.model_dump(mode="json"),
            }
        applied = _replace_stop_loss_order(
            session,
            client=client,
            position=position,
            symbol=symbol,
            stop_loss=float(tightened_stop_loss),
            decision_run_id=decision_run_id,
            risk_row=risk_row,
            trigger_source="position_management",
            open_orders=open_orders,
        )
        break_even_stop = _to_float(context.get("break_even_stop_loss"))
        if (
            "POSITION_MANAGEMENT_BREAK_EVEN" in set(_get_string_list(context, "applied_rule_candidates"))
            and break_even_stop is not None
            and abs(float(applied["tightened_stop_loss"]) - break_even_stop) <= 1e-9
        ):
            record_position_management_event(
                session,
                event_type="moved_stop_to_breakeven",
                position_id=position.id,
                severity="info",
                message="Position management moved the live stop to break-even.",
                payload={
                    "symbol": symbol,
                    "decision_run_id": decision_run_id,
                    "tightened_stop_loss": applied["tightened_stop_loss"],
                    "break_even_trigger_r": context.get("break_even_trigger_r"),
                },
            )
        if "POSITION_MANAGEMENT_MFE_ROLLBACK" in set(_get_string_list(context, "applied_rule_candidates")):
            record_position_management_event(
                session,
                event_type="position_management_mfe_rollback_tighten",
                position_id=position.id,
                severity="info",
                message="Position management tightened protection after an MFE rollback trigger.",
                payload={
                    "symbol": symbol,
                    "decision_run_id": decision_run_id,
                    "tightened_stop_loss": applied["tightened_stop_loss"],
                    "mfe_r": context.get("mfe_r"),
                    "mae_r": context.get("mae_r"),
                    "mfe_rollback_pct": context.get("mfe_rollback_pct"),
                    "mfe_protection_action": context.get("mfe_protection_action"),
                    "management_stage": context.get("management_stage"),
                },
            )
        refreshed_open_orders = client.get_open_orders(symbol)
        protection_result = _ensure_protected_position(
            session,
            settings_row,
            client,
            symbol=symbol,
            position=position,
            stop_loss=position.stop_loss,
            take_profit=position.take_profit,
            decision_run_id=decision_run_id,
            risk_row=risk_row,
            parent_order=None,
            trigger_source="position_management",
            pause_reason_code="MISSING_PROTECTIVE_ORDERS",
        )
        return {
            "status": "applied",
            "position_management_context": context,
            "position_management_action": applied,
            "protection_state": _build_protection_state(position, refreshed_open_orders),
            "protection_result": protection_result,
        }

    management_decision = _build_position_management_trade_decision(
        position,
        feature_payload=feature_payload,
        context=context,
        settings_row=settings_row,
    )
    if management_decision is None:
        return {
            "status": "monitoring",
            "position_management_context": context,
            "protection_state": protection_state,
        }
    if protection_state["status"] != "protected":
        return {
            "status": "monitoring",
            "position_management_context": context,
            "protection_state": protection_state,
            "position_management_action": {
                "status": "skipped_unverified_protection",
                "decision": management_decision.model_dump(mode="json"),
            },
        }

    market_snapshot = _build_market_snapshot_from_position(position, feature_payload)
    management_risk_result, management_risk_row = evaluate_risk(
        session,
        settings_row,
        management_decision,
        market_snapshot,
        decision_run_id=decision_run_id,
        execution_mode="live",
    )
    if not management_risk_result.allowed or not is_survival_path_decision(management_decision):
        return {
            "status": "blocked",
            "position_management_context": context,
            "protection_state": protection_state,
            "position_management_action": {
                "status": "risk_blocked",
                "decision": management_decision.model_dump(mode="json"),
                "risk_result": management_risk_result.model_dump(mode="json"),
            },
        }

    execution_result = execute_live_trade(
        session,
        settings_row,
        decision_run_id=decision_run_id,
        decision=management_decision,
        market_snapshot=market_snapshot,
        risk_result=management_risk_result,
        risk_row=management_risk_row,
    )

    fill_quantity = _to_float(execution_result.get("fill_quantity"))
    if fill_quantity > 0 and "POSITION_MANAGEMENT_PARTIAL_TAKE_PROFIT" in set(management_decision.rationale_codes):
        record_position_management_event(
            session,
            event_type="partial_tp_executed",
            position_id=position.id,
            severity="info",
            message="Position management executed a partial take profit in reduce-only mode.",
            payload={
                "symbol": symbol,
                "order_id": execution_result.get("order_id"),
                "fill_quantity": fill_quantity,
                "reduce_fraction": execution_result.get("position_management", {}).get("reduce_fraction"),
            },
        )
    if fill_quantity > 0 and "POSITION_MANAGEMENT_TIME_STOP_EXIT" in set(management_decision.rationale_codes):
        record_position_management_event(
            session,
            event_type="time_stop_exit",
            position_id=position.id,
            severity="info",
            message="Position management exited the position because time stop conditions were met.",
            payload={
                "symbol": symbol,
                "order_id": execution_result.get("order_id"),
                "time_stop_minutes": context.get("time_stop_minutes"),
                "time_stop_profit_floor": context.get("time_stop_profit_floor"),
            },
        )
        mark_time_stop_action(position, action="exit")
        session.add(position)
        session.flush()
    elif fill_quantity > 0 and "POSITION_MANAGEMENT_TIME_STOP_REDUCE" in set(management_decision.rationale_codes):
        record_position_management_event(
            session,
            event_type="time_stop_reduce",
            position_id=position.id,
            severity="info",
            message="Position management reduced the position because time stop conditions were met.",
            payload={
                "symbol": symbol,
                "order_id": execution_result.get("order_id"),
                "time_stop_minutes": context.get("time_stop_minutes"),
                "time_stop_profit_floor": context.get("time_stop_profit_floor"),
            },
        )
        mark_time_stop_action(position, action="reduce")
        session.add(position)
        session.flush()
    elif fill_quantity > 0 and "POSITION_MANAGEMENT_MFE_ROLLBACK_REDUCE" in set(management_decision.rationale_codes):
        record_position_management_event(
            session,
            event_type="position_management_mfe_rollback_reduce",
            position_id=position.id,
            severity="info",
            message="Position management reduced the runner after a large MFE rollback.",
            payload={
                "symbol": symbol,
                "order_id": execution_result.get("order_id"),
                "mfe_r": context.get("mfe_r"),
                "mae_r": context.get("mae_r"),
                "mfe_rollback_pct": context.get("mfe_rollback_pct"),
                "mfe_protection_action": context.get("mfe_protection_action"),
                "management_stage": context.get("management_stage"),
            },
        )
    elif fill_quantity > 0 and "POSITION_MANAGEMENT_MFE_ROLLBACK_EXIT" in set(management_decision.rationale_codes):
        record_position_management_event(
            session,
            event_type="position_management_mfe_rollback_exit",
            position_id=position.id,
            severity="info",
            message="Position management exited after a severe MFE rollback.",
            payload={
                "symbol": symbol,
                "order_id": execution_result.get("order_id"),
                "mfe_r": context.get("mfe_r"),
                "mae_r": context.get("mae_r"),
                "mfe_rollback_pct": context.get("mfe_rollback_pct"),
                "mfe_protection_action": context.get("mfe_protection_action"),
                "management_stage": context.get("management_stage"),
            },
        )

    return {
        "status": "executed",
        "position_management_context": context,
        "position_management_action": execution_result,
        "protection_state": protection_state,
        "risk_result": management_risk_result.model_dump(mode="json"),
        "decision": management_decision.model_dump(mode="json"),
    }


def build_execution_intent(
    decision: TradeDecision,
    market_snapshot: MarketSnapshotPayload,
    risk_result: RiskCheckResult,
    settings_row: Setting,
    equity: float,
    existing_position: Position | None = None,
    operating_state: str = TRADABLE_STATE,
) -> ExecutionIntent:
    entry_price = _entry_price(decision, market_snapshot)
    intent_type = _classify_execution_intent(decision, existing_position, operating_state=operating_state)
    if intent_type == "reduce_only" and existing_position is not None:
        entry_price = existing_position.mark_price if existing_position.mark_price > 0 else market_snapshot.latest_price
    if intent_type == "protection" and existing_position is not None:
        quantity = max(existing_position.quantity, 0.0001)
        entry_price = existing_position.mark_price if existing_position.mark_price > 0 else existing_position.entry_price
        leverage = existing_position.leverage if existing_position.leverage > 0 else min(risk_result.approved_leverage, settings_row.max_leverage)
    else:
        approved_quantity = risk_result.approved_quantity if risk_result.approved_quantity is not None else 0.0
        if approved_quantity > 0:
            quantity = approved_quantity
        else:
            quantity = _calculate_quantity(
                entry_price=entry_price,
                stop_loss=decision.stop_loss,
                equity=equity,
                risk_pct=risk_result.approved_risk_pct,
                leverage=risk_result.approved_leverage,
            )
            if risk_result.approved_projected_notional > 0:
                quantity = min(
                    quantity,
                    _quantity_for_notional(risk_result.approved_projected_notional, entry_price),
                )
        leverage = min(risk_result.approved_leverage, settings_row.max_leverage)
    required_order_policy, allow_market_fallback, order_policy_reason = _execution_order_policy_from_risk_result(
        risk_result,
        intent_type=intent_type,
    )
    return ExecutionIntent(
        symbol=decision.symbol,
        action=decision.decision,  # type: ignore[arg-type]
        intent_type=intent_type,  # type: ignore[arg-type]
        quantity=max(quantity, 0.0001),
        requested_price=entry_price,
        entry_mode=decision.entry_mode,
        invalidation_price=decision.invalidation_price,
        max_chase_bps=decision.max_chase_bps,
        idea_ttl_minutes=decision.idea_ttl_minutes,
        stop_loss=decision.stop_loss,
        take_profit=decision.take_profit,
        leverage=leverage,
        mode="live",
        reduce_only=decision.decision in {"reduce", "exit"},
        close_only=decision.decision == "exit",
        holding_profile=decision.holding_profile,
        holding_profile_reason=decision.holding_profile_reason,
        required_order_policy=required_order_policy,  # type: ignore[arg-type]
        allow_market_fallback=allow_market_fallback,
        order_policy_reason=order_policy_reason,
    )


def _map_exchange_status(status: str) -> str:
    return {
        "NEW": "pending",
        "PARTIALLY_FILLED": "partially_filled",
        "FILLED": "filled",
        "CANCELED": "canceled",
        "REJECTED": "rejected",
        "EXPIRED": "expired",
        "EXPIRED_IN_MATCH": "expired",
        "FINISHED": "expired",
    }.get(status.upper(), status.lower())


def _flag_enabled(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() == "true"


def _signed_slippage_bps(*, side: str | None, requested_price: float | None, fill_price: float | None) -> float:
    requested = _to_float(requested_price)
    filled = _to_float(fill_price)
    if requested <= 0 or filled <= 0:
        return 0.0
    raw_bps = ((filled - requested) / requested) * 10000.0
    side_key = str(side or "").lower()
    if side_key == "buy":
        return raw_bps
    if side_key == "sell":
        return -raw_bps
    return 0.0


def _entry_execution_type_for_plan(
    plan: ExecutionPlan,
    *,
    order_type: str | None = None,
    execution_quality: dict[str, object] | None = None,
) -> str:
    if plan.intent_type not in {"entry", "scale_in"}:
        return ENTRY_EXECUTION_TYPE_UNKNOWN
    quality = execution_quality or {}
    if _flag_enabled(quality.get("aggressive_fallback_used")):
        return ENTRY_EXECUTION_TYPE_MARKETABLE
    policy_name = str(plan.policy_name or "").lower()
    if "marketable" in policy_name or "market" in policy_name or plan.marketable:
        return ENTRY_EXECUTION_TYPE_MARKETABLE
    if "passive" in policy_name or "maker" in policy_name:
        return ENTRY_EXECUTION_TYPE_PASSIVE_LIMIT
    normalized_order_type = str(order_type or plan.order_type or "").lower()
    if normalized_order_type == "limit":
        return ENTRY_EXECUTION_TYPE_PASSIVE_LIMIT
    if normalized_order_type == "market" or normalized_order_type.endswith("_market"):
        return ENTRY_EXECUTION_TYPE_MARKETABLE
    return ENTRY_EXECUTION_TYPE_UNKNOWN


def _record_live_trades(
    session: Session,
    order: Order,
    trades: list[dict[str, object]],
    *,
    source: str = "LIVE_ORDER_TRADE_LOOKUP",
    exchange_order: dict[str, object] | None = None,
    exchange_order_id: str | None = None,
    client_order_id: str | None = None,
    linked_protective_order_id: int | None = None,
) -> tuple[float, float]:
    fee_total = 0.0
    realized_total = 0.0
    for trade in trades:
        trade_id = str(trade.get("id", ""))
        if not trade_id:
            continue
        existing = session.scalar(select(Execution).where(Execution.external_trade_id == trade_id).limit(1))
        if existing is not None:
            continue
        fill_price = _to_float(trade.get("price"))
        fill_quantity = abs(_to_float(trade.get("qty")))
        fee_paid = abs(_to_float(trade.get("commission")))
        realized_pnl = _to_float(trade.get("realizedPnl"))
        metadata = _as_object_dict(order.metadata_json)
        performance_tags = _trade_performance_tags_from_metadata(metadata)
        signed_slippage_bps = _signed_slippage_bps(
            side=order.side,
            requested_price=order.requested_price,
            fill_price=fill_price,
        )
        trade_order_id = str(trade.get("orderId") or exchange_order_id or order.external_order_id or "") or None
        trade_client_order_id = (
            str(trade.get("clientOrderId") or client_order_id or order.client_order_id or "") or None
        )
        execution_payload = {
            "exchange": "BINANCE",
            "source": source,
            "trade": trade,
            "trade_id": trade_id,
            "exchange_trade_id": trade_id,
            "order_id": order.id,
            "local_order_id": order.id,
            "exchange_order_id": trade_order_id,
            "client_order_id": trade_client_order_id,
            "side": trade.get("side") or order.side,
            "position_side": trade.get("positionSide") or trade.get("position_side"),
            "trade_time": trade.get("time"),
            "is_maker": trade.get("maker"),
            "maker": trade.get("maker"),
            "buyer": trade.get("buyer"),
            "requested_price": order.requested_price,
            "requested_quantity": order.requested_quantity,
            "order_type": order.order_type,
            "execution_policy": metadata.get("execution_policy"),
            "entry_execution_type": metadata.get("entry_execution_type", ENTRY_EXECUTION_TYPE_UNKNOWN),
            "signed_slippage_pct": signed_slippage_bps / 10000.0,
            "signed_slippage_bps": signed_slippage_bps,
            "realized_pnl_source": "binance_user_trades" if "realizedPnl" in trade else "local_default",
            **_trade_performance_payload(performance_tags),
        }
        r_multiple_payload = _execution_r_multiple_payload(
            tags=performance_tags,
            fill_quantity=fill_quantity,
            realized_pnl=realized_pnl,
            fee_paid=fee_paid,
        )
        if r_multiple_payload:
            execution_payload["r_multiple"] = r_multiple_payload
            execution_payload["gross_r_multiple"] = r_multiple_payload["gross_r_multiple"]
            execution_payload["net_r_multiple"] = r_multiple_payload["net_r_multiple"]
            execution_payload["risk_amount_usdt"] = r_multiple_payload["risk_amount_usdt"]
        if exchange_order is not None:
            execution_payload["exchange_order"] = exchange_order
        if linked_protective_order_id is not None:
            execution_payload["linked_protective_order_id"] = linked_protective_order_id
        execution = Execution(
            order_id=order.id,
            position_id=order.position_id,
            symbol=order.symbol,
            status="filled",
            external_trade_id=trade_id,
            fill_price=fill_price,
            fill_quantity=fill_quantity,
            fee_paid=fee_paid,
            commission_asset=str(trade.get("commissionAsset", "")) or None,
            slippage_pct=abs(fill_price - order.requested_price) / max(order.requested_price, 1.0),
            realized_pnl=realized_pnl,
            payload=execution_payload,
        )
        session.add(execution)
        fee_total += fee_paid
        realized_total += realized_pnl
    session.flush()
    return fee_total, realized_total


def _backfill_missing_final_order_trades(
    session: Session,
    settings_row: Setting,
    client: BinanceClient,
    *,
    symbol: str,
) -> int:
    orders = session.scalars(
        select(Order)
        .where(
            Order.mode == "live",
            Order.symbol == symbol,
            Order.status.in_(("filled", "partially_filled")),
            Order.order_type.notin_(("stop_market", "take_profit_market")),
            ~Order.id.in_(select(Execution.order_id).where(Execution.order_id.is_not(None))),
        )
        .order_by(Order.id.asc())
    ).all()
    backfilled = 0
    for order in orders:
        if not order.external_order_id and not order.client_order_id:
            continue
        try:
            trades = client.get_account_trades(symbol=order.symbol, order_id=order.external_order_id)
        except Exception as exc:
            record_audit_event(
                session,
                event_type="live_trade_backfill_failed",
                entity_type="order",
                entity_id=str(order.id),
                severity="warning",
                message="Filled live order trade backfill failed.",
                payload={"symbol": order.symbol, "order_id": order.id, "error": str(exc)},
            )
            continue
        if not trades:
            record_audit_event(
                session,
                event_type="live_trade_backfill_empty",
                entity_type="order",
                entity_id=str(order.id),
                severity="warning",
                message="Filled live order had no account trades available during backfill.",
                payload={
                    "symbol": order.symbol,
                    "order_id": order.id,
                    "exchange_order_id": order.external_order_id,
                    "client_order_id": order.client_order_id,
                },
            )
            continue
        fee_paid, realized_pnl = _record_live_trades(session, order, trades)
        if fee_paid or realized_pnl or _sum_trade_quantity(trades) > 0:
            backfilled += 1
            record_audit_event(
                session,
                event_type="live_trade_backfilled",
                entity_type="order",
                entity_id=str(order.id),
                severity="info",
                message="Missing filled live order executions were backfilled from exchange trades.",
                payload={
                    "symbol": order.symbol,
                    "order_id": order.id,
                    "exchange_order_id": order.external_order_id,
                    "trade_count": len(trades),
                    "filled_quantity": _sum_trade_quantity(trades),
                    "fees": fee_paid,
                    "realized_pnl": realized_pnl,
                },
            )
    if backfilled:
        create_exchange_pnl_snapshot(session, settings_row)
    return backfilled


def _sum_trade_quantity(trades: list[dict[str, object]]) -> float:
    return sum(abs(_to_float(trade.get("qty"))) for trade in trades)


def _sync_closed_position_pnl(session: Session, position: Position) -> None:
    if position.id is None:
        return
    totals = session.execute(
        select(
            func.coalesce(func.sum(Execution.realized_pnl), 0.0),
            func.coalesce(func.sum(Execution.fee_paid), 0.0),
        )
        .join(Order, Order.id == Execution.order_id)
        .where(Order.position_id == position.id)
    ).one()
    gross_realized = float(totals[0] or 0.0)
    fee_total = float(totals[1] or 0.0)
    metadata = dict(position.metadata_json) if isinstance(position.metadata_json, dict) else {}
    performance_tags = _trade_performance_tags_from_metadata(metadata)
    closed_position_pnl: dict[str, object] = {
        "gross_realized_pnl": gross_realized,
        "fee_total": fee_total,
        "net_realized_pnl": gross_realized - fee_total,
        "source": "executions",
        "updated_at": utcnow_naive().isoformat(),
    }
    risk = _as_object_dict(performance_tags.get("risk"))
    risk_amount = _to_float(performance_tags.get("initial_risk_usdt") or risk.get("initial_risk_usdt"))
    if risk_amount <= 0:
        risk_per_unit = _to_float(performance_tags.get("risk_per_unit") or risk.get("risk_per_unit"))
        initial_quantity = _to_float(risk.get("initial_quantity"))
        if risk_per_unit > 0 and initial_quantity > 0:
            risk_amount = risk_per_unit * initial_quantity
    if risk_amount > 0:
        closed_position_pnl["risk_amount_usdt"] = risk_amount
        closed_position_pnl["gross_r_multiple"] = gross_realized / risk_amount
        closed_position_pnl["net_r_multiple"] = (gross_realized - fee_total) / risk_amount
    if performance_tags:
        closed_position_pnl.update(_trade_performance_payload(performance_tags))
    metadata["closed_position_pnl"] = closed_position_pnl
    position.realized_pnl = gross_realized
    position.unrealized_pnl = 0.0
    position.metadata_json = metadata
    record_range_mr_trade_result(session, position)


def _protective_close_fill_status(exchange_order: dict[str, object]) -> str | None:
    for key in ("algoStatus", "status"):
        status = str(exchange_order.get(key) or "").upper()
        if status in PROTECTIVE_CLOSE_FILL_BACKFILL_CLOSED_STATUSES:
            return status
    return None


def _protective_close_actual_order_id(exchange_order: dict[str, object]) -> str | None:
    for key in ("actualOrderId", "actual_order_id", "orderId"):
        value = str(exchange_order.get(key) or "")
        if value and value != "0":
            return value
    return None


def _protective_close_actual_quantity(order: Order, exchange_order: dict[str, object]) -> float:
    for key in ("actualQty", "executedQty", "quantity", "origQty"):
        quantity = _to_float(exchange_order.get(key))
        if quantity > 0:
            return abs(quantity)
    return abs(_to_float(order.requested_quantity))


def _protective_close_actual_price(exchange_order: dict[str, object], trades: list[dict[str, object]]) -> float:
    for key in ("actualPrice", "avgPrice", "price"):
        price = _to_float(exchange_order.get(key))
        if price > 0:
            return price
    quantity = _sum_trade_quantity(trades)
    if quantity <= 0:
        return 0.0
    notional = sum(abs(_to_float(trade.get("qty"))) * _to_float(trade.get("price")) for trade in trades)
    return notional / quantity if notional > 0 else 0.0


def _payload_time_ms(payload: dict[str, object]) -> int | None:
    for key in ("updateTime", "transactTime", "triggerTime", "workingTime", "time"):
        value = _optional_int(payload.get(key))
        if value:
            return value
    return None


def _matches_protective_close_trade(
    trade: dict[str, object],
    *,
    order: Order,
    exchange_order: dict[str, object],
    actual_order_id: str | None,
) -> bool:
    trade_order_id = str(trade.get("orderId") or "")
    if actual_order_id and trade_order_id and trade_order_id != actual_order_id:
        return False
    expected_side = str(exchange_order.get("side") or order.side or "").upper()
    trade_side = str(trade.get("side") or "").upper()
    if expected_side and trade_side and trade_side != expected_side:
        return False
    expected_quantity = _protective_close_actual_quantity(order, exchange_order)
    trade_quantity = abs(_to_float(trade.get("qty")))
    if expected_quantity > 0 and trade_quantity > 0:
        tolerance = max(1e-9, expected_quantity * 0.000001)
        if abs(trade_quantity - expected_quantity) > tolerance:
            return False
    reference_time_ms = _payload_time_ms(exchange_order)
    trade_time_ms = _optional_int(trade.get("time"))
    return not (
        reference_time_ms
        and trade_time_ms
        and abs(trade_time_ms - reference_time_ms) > 10 * 60 * 1000
    )


def _filter_protective_close_trades(
    trades: list[dict[str, object]],
    *,
    order: Order,
    exchange_order: dict[str, object],
    actual_order_id: str | None,
) -> list[dict[str, object]]:
    return [
        trade
        for trade in trades
        if _matches_protective_close_trade(
            trade,
            order=order,
            exchange_order=exchange_order,
            actual_order_id=actual_order_id,
        )
    ]


def _set_protective_close_fill_backfill_state(
    order: Order,
    *,
    status: str,
    source: str,
    reason_code: str | None = None,
    exchange_order: dict[str, object] | None = None,
    exchange_order_id: str | None = None,
    lookup_method: str | None = None,
    trade_count: int = 0,
    inserted_trade_count: int = 0,
    filled_quantity: float = 0.0,
    fees: float = 0.0,
    realized_pnl: float = 0.0,
    error: str | None = None,
) -> None:
    updated_at = utcnow_naive()
    metadata = _as_object_dict(order.metadata_json)
    detail: dict[str, object] = {
        "status": status,
        "source": source,
        "updated_at": updated_at.isoformat(),
        "exchange_order_id": exchange_order_id,
        "lookup_method": lookup_method,
        "trade_count": trade_count,
        "inserted_trade_count": inserted_trade_count,
        "filled_quantity": filled_quantity,
        "fees": fees,
        "realized_pnl": realized_pnl,
    }
    if reason_code:
        detail["reason_code"] = reason_code
    if error:
        detail["error"] = error
    if exchange_order is not None:
        detail["exchange_status"] = _protective_close_fill_status(exchange_order) or exchange_order.get("status")
        detail["exchange_order"] = exchange_order
    metadata["protective_close_fill_backfill"] = detail
    order.metadata_json = metadata
    remove_reason_codes = {
        PROTECTIVE_CLOSE_FILL_BACKFILL_PENDING_REASON_CODE,
        PROTECTIVE_CLOSE_FILL_BACKFILL_FAILED_REASON_CODE,
    }
    if status == "backfilled":
        remove_reason_codes.add(CLOSED_POSITION_PROTECTIVE_ORDER_RECONCILED_REASON_CODE)
    reason_codes = [
        str(code)
        for code in (order.reason_codes or [])
        if code and str(code) not in remove_reason_codes
    ]
    if reason_code and reason_code not in reason_codes:
        reason_codes.append(reason_code)
    order.reason_codes = reason_codes


def _fetch_protective_close_trades(
    client: BinanceClient,
    *,
    order: Order,
    exchange_order: dict[str, object],
    actual_order_id: str | None,
) -> tuple[list[dict[str, object]], str | None]:
    if actual_order_id:
        trades = client.get_account_trades(symbol=order.symbol, order_id=actual_order_id)
        if trades:
            return trades, "order_id"
    recent_trades = client.get_account_trades(symbol=order.symbol, limit=50)
    return (
        _filter_protective_close_trades(
            recent_trades,
            order=order,
            exchange_order=exchange_order,
            actual_order_id=actual_order_id,
        ),
        "symbol_recent",
    )


def _backfill_finished_protective_order_trades(
    session: Session,
    settings_row: Setting,
    client: BinanceClient,
    order: Order,
    exchange_order: dict[str, object],
    *,
    source: str = PROTECTIVE_CLOSE_FILL_BACKFILL_SOURCE,
) -> int:
    if not _is_local_protective_order_row(order):
        return 0
    closed_status = _protective_close_fill_status(exchange_order)
    if closed_status is None:
        return 0
    actual_order_id = _protective_close_actual_order_id(exchange_order)
    try:
        trades, lookup_method = _fetch_protective_close_trades(
            client,
            order=order,
            exchange_order=exchange_order,
            actual_order_id=actual_order_id,
        )
    except Exception as exc:
        _set_protective_close_fill_backfill_state(
            order,
            status="failed",
            source=source,
            reason_code=PROTECTIVE_CLOSE_FILL_BACKFILL_FAILED_REASON_CODE,
            exchange_order=exchange_order,
            exchange_order_id=actual_order_id,
            error=str(exc),
        )
        session.add(order)
        record_audit_event(
            session,
            event_type="protective_close_fill_backfill_failed",
            entity_type="order",
            entity_id=str(order.id),
            severity="warning",
            message="Finished protective order close-fill backfill failed.",
            payload={
                "symbol": order.symbol,
                "order_id": order.id,
                "position_id": order.position_id,
                "exchange_order_id": actual_order_id,
                "client_order_id": order.client_order_id,
                "exchange_status": closed_status,
                "source": source,
                "reason_code": PROTECTIVE_CLOSE_FILL_BACKFILL_FAILED_REASON_CODE,
                "error": str(exc),
            },
        )
        session.flush()
        return 0
    if not trades:
        _set_protective_close_fill_backfill_state(
            order,
            status="pending",
            source=source,
            reason_code=PROTECTIVE_CLOSE_FILL_BACKFILL_PENDING_REASON_CODE,
            exchange_order=exchange_order,
            exchange_order_id=actual_order_id,
            lookup_method=lookup_method,
        )
        session.add(order)
        record_audit_event(
            session,
            event_type="protective_close_fill_backfill_pending",
            entity_type="order",
            entity_id=str(order.id),
            severity="warning",
            message="Finished protective order had no close-fill trades available during backfill.",
            payload={
                "symbol": order.symbol,
                "order_id": order.id,
                "position_id": order.position_id,
                "exchange_order_id": actual_order_id,
                "client_order_id": order.client_order_id,
                "exchange_status": closed_status,
                "source": source,
                "reason_code": PROTECTIVE_CLOSE_FILL_BACKFILL_PENDING_REASON_CODE,
                "lookup_method": lookup_method,
            },
        )
        session.flush()
        return 0
    trade_ids = {str(trade.get("id") or "") for trade in trades if str(trade.get("id") or "")}
    existing_trade_ids = set(
        session.scalars(select(Execution.external_trade_id).where(Execution.external_trade_id.in_(trade_ids)))
    ) if trade_ids else set()
    inserted_fee_paid, inserted_realized_pnl = _record_live_trades(
        session,
        order,
        trades,
        source=PROTECTIVE_CLOSE_FILL_BACKFILL_SOURCE,
        exchange_order=exchange_order,
        exchange_order_id=actual_order_id,
        client_order_id=order.client_order_id,
        linked_protective_order_id=order.id,
    )
    inserted_trade_count = len(trade_ids - existing_trade_ids)
    filled_quantity = _sum_trade_quantity(trades)
    fee_paid = sum(abs(_to_float(trade.get("commission"))) for trade in trades)
    realized_pnl = sum(_to_float(trade.get("realizedPnl")) for trade in trades)
    average_fill_price = _protective_close_actual_price(exchange_order, trades)
    if filled_quantity > 0:
        order.filled_quantity = filled_quantity
    if average_fill_price > 0:
        order.average_fill_price = average_fill_price
    order.status = "filled"
    order.exchange_status = closed_status
    order.last_exchange_update_at = utcnow_naive()
    _set_protective_close_fill_backfill_state(
        order,
        status="backfilled",
        source=source,
        exchange_order=exchange_order,
        exchange_order_id=actual_order_id,
        lookup_method=lookup_method,
        trade_count=len(trades),
        inserted_trade_count=inserted_trade_count,
        filled_quantity=filled_quantity,
        fees=fee_paid,
        realized_pnl=realized_pnl,
    )
    session.add(order)
    if order.position_id is not None:
        position = session.get(Position, order.position_id)
        if position is not None:
            _sync_closed_position_pnl(session, position)
            session.add(position)
    if inserted_trade_count:
        create_exchange_pnl_snapshot(session, settings_row)
    record_audit_event(
        session,
        event_type="protective_close_fill_backfilled",
        entity_type="order",
        entity_id=str(order.id),
        severity="info",
        message="Finished protective order close-fill executions were backfilled from exchange trades.",
        payload={
            "symbol": order.symbol,
            "order_id": order.id,
            "position_id": order.position_id,
            "exchange_order_id": actual_order_id,
            "client_order_id": order.client_order_id,
            "exchange_status": closed_status,
            "source": source,
            "lookup_method": lookup_method,
            "trade_count": len(trades),
            "inserted_trade_count": inserted_trade_count,
            "filled_quantity": filled_quantity,
            "fees": fee_paid,
            "realized_pnl": realized_pnl,
            "inserted_fees": inserted_fee_paid,
            "inserted_realized_pnl": inserted_realized_pnl,
        },
    )
    session.flush()
    return inserted_trade_count


def _backfill_missing_finished_protective_order_trades(
    session: Session,
    settings_row: Setting,
    client: BinanceClient,
    *,
    symbol: str,
    observed_at: datetime | None = None,
    source: str = "sync_live_state",
) -> int:
    observed_at = observed_at or utcnow_naive()
    with session.no_autoflush:
        candidates = list(
            session.scalars(
                select(Order)
                .join(Position, Order.position_id == Position.id)
                .where(
                    Order.mode == "live",
                    Order.symbol == symbol.upper(),
                    or_(Order.reduce_only.is_(True), Order.close_only.is_(True)),
                    or_(Position.status != "open", Position.quantity <= 0),
                    ~Order.id.in_(select(Execution.order_id).where(Execution.order_id.is_not(None))),
                )
                .order_by(Order.updated_at.desc(), Order.id.desc())
                .limit(100)
            )
    )
    backfilled = 0
    for order in candidates:
        if not _is_local_protective_order_row(order):
            continue
        if not order.external_order_id and not order.client_order_id:
            continue
        try:
            exchange_order = _fetch_exchange_order(
                client,
                symbol=order.symbol,
                order_type=order.order_type,
                order_id=order.external_order_id,
                client_order_id=order.client_order_id,
            )
        except Exception as exc:
            _set_protective_close_fill_backfill_state(
                order,
                status="failed",
                source=source,
                reason_code=PROTECTIVE_CLOSE_FILL_BACKFILL_FAILED_REASON_CODE,
                exchange_order_id=order.external_order_id,
                error=str(exc),
            )
            order.last_exchange_update_at = observed_at
            session.add(order)
            record_audit_event(
                session,
                event_type="protective_close_fill_backfill_failed",
                entity_type="order",
                entity_id=str(order.id),
                severity="warning",
                message="Finished protective order lookup failed during close-fill backfill.",
                payload={
                    "symbol": order.symbol,
                    "order_id": order.id,
                    "position_id": order.position_id,
                    "exchange_order_id": order.external_order_id,
                    "client_order_id": order.client_order_id,
                    "source": source,
                    "reason_code": PROTECTIVE_CLOSE_FILL_BACKFILL_FAILED_REASON_CODE,
                    "error": str(exc),
                },
            )
            continue
        closed_status = _protective_close_fill_status(exchange_order)
        if closed_status is None:
            continue
        _apply_exchange_order_state(
            order,
            exchange_order,
            requested_quantity_fallback=order.requested_quantity,
            requested_price_fallback=order.requested_price,
            reduce_only_fallback=order.reduce_only,
            close_only_fallback=order.close_only,
            updated_at=observed_at,
        )
        session.add(order)
        backfilled += _backfill_finished_protective_order_trades(
            session,
            settings_row,
            client,
            order,
            exchange_order,
            source=source,
        )
    if candidates:
        session.flush()
    return backfilled


def _upsert_exchange_order_row(
    session: Session,
    *,
    symbol: str,
    requested_price: float,
    requested_quantity: float,
    order_type: str,
    side: str,
    exchange_order: dict[str, object],
    decision_run_id: int | None,
    risk_row: RiskCheck | None,
    reduce_only: bool,
    close_only: bool,
    parent_order_id: int | None = None,
    updated_at: datetime | None = None,
) -> Order:
    external_order_id = str(exchange_order.get("orderId", "")) or None
    client_order_id = str(exchange_order.get("clientOrderId", "")) or None
    row = None
    if external_order_id:
        row = session.scalar(select(Order).where(Order.external_order_id == external_order_id).limit(1))
    if row is None and client_order_id:
        row = session.scalar(select(Order).where(Order.client_order_id == client_order_id).limit(1))
    if row is None:
        row = Order(
            symbol=symbol,
            decision_run_id=decision_run_id,
            risk_check_id=risk_row.id if risk_row is not None else None,
            position_id=None,
            side=side,
            order_type=order_type.lower(),
            mode="live",
            status="pending",
            external_order_id=external_order_id,
            client_order_id=client_order_id,
            reduce_only=reduce_only,
            close_only=close_only,
            parent_order_id=parent_order_id,
            requested_quantity=requested_quantity,
            requested_price=requested_price,
            filled_quantity=0.0,
            average_fill_price=0.0,
            reason_codes=[],
            metadata_json={},
        )
    row.mode = "live"
    row.side = side
    row.order_type = order_type.lower()
    row.reduce_only = reduce_only
    row.close_only = close_only
    row.parent_order_id = parent_order_id
    row.requested_quantity = requested_quantity
    row.requested_price = requested_price
    _apply_exchange_order_state(
        row,
        exchange_order,
        requested_quantity_fallback=requested_quantity,
        requested_price_fallback=requested_price,
        reduce_only_fallback=reduce_only,
        close_only_fallback=close_only,
        updated_at=updated_at,
    )
    existing_metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    submission_tracking = _as_object_dict(existing_metadata.get("submission_tracking"))
    if submission_tracking:
        recovered_via = str(submission_tracking.get("recovered_via") or "")
        submission_tracking = {
            **submission_tracking,
            "submission_state": "reconciled",
            "updated_at": (updated_at or utcnow_naive()).isoformat(),
        }
        if not recovered_via:
            submission_tracking["recovered_via"] = "exchange_sync"
        row.reason_codes = [code for code in row.reason_codes if code != UNKNOWN_SUBMISSION_REASON_CODE]
    row.metadata_json = {
        **existing_metadata,
        "exchange_order": exchange_order,
    }
    exchange_position_side = _exchange_order_position_side(exchange_order)
    if exchange_position_side is not None:
        row.metadata_json["exchange_position_side"] = exchange_position_side
        row.metadata_json["exchange_position_mode"] = (
            POSITION_MODE_HEDGE if exchange_position_side in {"LONG", "SHORT"} else POSITION_MODE_ONE_WAY
        )
    if submission_tracking:
        row.metadata_json["submission_tracking"] = submission_tracking
    session.add(row)
    session.flush()
    return row


def _create_rejected_order_row(
    session: Session,
    *,
    symbol: str,
    side: str,
    order_type: str,
    requested_quantity: float,
    requested_price: float,
    decision_run_id: int | None,
    risk_row: RiskCheck | None,
    reduce_only: bool,
    close_only: bool,
    reason_codes: list[str],
    metadata_json: dict[str, object],
    client_order_id: str | None = None,
) -> Order:
    row = Order(
        symbol=symbol,
        decision_run_id=decision_run_id,
        risk_check_id=risk_row.id if risk_row is not None else None,
        position_id=None,
        side=side,
        order_type=order_type.lower(),
        mode="live",
        status="rejected",
        external_order_id=None,
        client_order_id=client_order_id,
        reduce_only=reduce_only,
        close_only=close_only,
        parent_order_id=None,
        exchange_status="REJECTED",
        last_exchange_update_at=utcnow_naive(),
        requested_quantity=requested_quantity,
        requested_price=requested_price,
        filled_quantity=0.0,
        average_fill_price=0.0,
        reason_codes=reason_codes,
        metadata_json=metadata_json,
    )
    session.add(row)
    session.flush()
    return row


def _apply_submission_tracking(
    row: Order,
    *,
    client_order_id: str | None,
    submit_request: dict[str, Any],
    submission_tracking: dict[str, Any],
) -> None:
    metadata = _as_object_dict(row.metadata_json)
    row.client_order_id = client_order_id or row.client_order_id
    if str(submission_tracking.get("submission_state", "")).lower() != "submit_unknown":
        row.reason_codes = [code for code in row.reason_codes if code != UNKNOWN_SUBMISSION_REASON_CODE]
    row.metadata_json = {
        **metadata,
        "submit_request": dict(submit_request),
        "submission_tracking": dict(submission_tracking),
    }


def _create_submission_unknown_order_row(
    session: Session,
    *,
    symbol: str,
    side: str,
    order_type: str,
    requested_quantity: float,
    requested_price: float,
    decision_run_id: int | None,
    risk_row: RiskCheck | None,
    reduce_only: bool,
    close_only: bool,
    client_order_id: str,
    submit_request: dict[str, Any],
    submission_tracking: dict[str, Any],
    metadata_json: dict[str, object],
) -> Order:
    row = session.scalar(select(Order).where(Order.client_order_id == client_order_id).limit(1))
    if row is None:
        row = Order(
            symbol=symbol,
            decision_run_id=decision_run_id,
            risk_check_id=risk_row.id if risk_row is not None else None,
            position_id=None,
            side=side,
            order_type=order_type.lower(),
            mode="live",
            status="pending",
            external_order_id=None,
            client_order_id=client_order_id,
            reduce_only=reduce_only,
            close_only=close_only,
            parent_order_id=None,
            exchange_status="SUBMIT_UNKNOWN",
            last_exchange_update_at=utcnow_naive(),
            requested_quantity=requested_quantity,
            requested_price=requested_price,
            filled_quantity=0.0,
            average_fill_price=0.0,
            reason_codes=[UNKNOWN_SUBMISSION_REASON_CODE],
            metadata_json={},
        )
    row.symbol = symbol
    row.decision_run_id = decision_run_id
    row.risk_check_id = risk_row.id if risk_row is not None else None
    row.side = side
    row.order_type = order_type.lower()
    row.mode = "live"
    row.status = "pending"
    row.exchange_status = "SUBMIT_UNKNOWN"
    row.last_exchange_update_at = utcnow_naive()
    row.requested_quantity = requested_quantity
    row.requested_price = requested_price
    row.reduce_only = reduce_only
    row.close_only = close_only
    row.reason_codes = [UNKNOWN_SUBMISSION_REASON_CODE]
    row.metadata_json = {
        **_as_object_dict(row.metadata_json),
        **metadata_json,
    }
    _apply_submission_tracking(
        row,
        client_order_id=client_order_id,
        submit_request=submit_request,
        submission_tracking=submission_tracking,
    )
    session.add(row)
    session.flush()
    return row


def _bounded_reconcile_submission_unknown_orders(
    session: Session,
    settings_row: Setting,
    *,
    client: BinanceClient,
    symbol: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    observed_at = now or utcnow_naive()
    query = select(Order).where(
        Order.mode == "live",
        Order.status == "pending",
    )
    if symbol:
        query = query.where(Order.symbol == symbol.upper())
    pending_rows = list(session.scalars(query))
    unresolved_payloads: list[dict[str, Any]] = []
    for row in pending_rows:
        metadata = _as_object_dict(row.metadata_json)
        tracking = _as_object_dict(metadata.get("submission_tracking"))
        if str(tracking.get("submission_state") or "").lower() != "submit_unknown":
            continue
        action = "long" if row.side.lower() == "buy" else "short" if row.side.lower() == "sell" else row.side.lower()
        if action not in {"long", "short"}:
            action = "long"
        attempts = int(tracking.get("reconcile_attempt_count") or 0)
        next_reconcile_at = _coerce_tracking_datetime(tracking.get("next_reconcile_at"))
        final_resolution_deadline = _coerce_tracking_datetime(tracking.get("final_resolution_deadline"))
        if final_resolution_deadline is None:
            final_resolution_deadline = observed_at + timedelta(seconds=UNRESOLVED_SUBMISSION_RESOLUTION_DEADLINE_SECONDS)
        deadline_exceeded = observed_at >= final_resolution_deadline
        attempt_budget_exhausted = attempts >= UNRESOLVED_SUBMISSION_MAX_RECONCILE_ATTEMPTS
        should_attempt_lookup = (
            not deadline_exceeded
            and not attempt_budget_exhausted
            and (
                attempts <= 0
                or next_reconcile_at is None
                or observed_at >= next_reconcile_at
            )
        )

        if should_attempt_lookup:
            attempts += 1
            exchange_order: dict[str, object] | None = None
            reconcile_lookup_error: str | None = None
            try:
                exchange_order = _reconcile_unknown_submission(
                    client,
                    symbol=row.symbol,
                    order_type=row.order_type,
                    client_order_id=row.client_order_id or "",
                )
            except Exception as exc:
                reconcile_lookup_error = _stringify_submit_error(exc)
            if exchange_order is not None:
                _upsert_exchange_order_row(
                    session,
                    symbol=row.symbol,
                    requested_price=row.requested_price,
                    requested_quantity=row.requested_quantity,
                    order_type=row.order_type,
                    side=row.side,
                    exchange_order=exchange_order,
                    decision_run_id=row.decision_run_id,
                    risk_row=session.get(RiskCheck, row.risk_check_id) if row.risk_check_id is not None else None,
                    reduce_only=row.reduce_only,
                    close_only=row.close_only,
                    parent_order_id=row.parent_order_id,
                    updated_at=observed_at,
                )
                updated_tracking = _build_submission_tracking(
                    submission_state="reconciled",
                    client_order_id=row.client_order_id,
                    submit_attempt_count=int(tracking.get("submit_attempt_count") or 0),
                    last_submit_error=str(tracking.get("last_submit_error") or "") or None,
                    safe_retry_used=bool(tracking.get("safe_retry_used", False)),
                    recovered_via="bounded_reconcile_loop",
                    reconcile_attempt_count=attempts,
                    unresolved_guard_active=False,
                )
                metadata["submission_tracking"] = updated_tracking
                row.metadata_json = metadata
                row.reason_codes = [code for code in row.reason_codes if code not in {UNKNOWN_SUBMISSION_REASON_CODE, UNRESOLVED_SUBMISSION_DEADLINE_REASON_CODE}]
                clear_unresolved_submission_guard(settings_row, symbol=row.symbol, action=action)
                record_audit_event(
                    session,
                    event_type="live_order_submission_recovered",
                    entity_type="order",
                    entity_id=str(row.id),
                    severity="info",
                    message="Submission-unknown order was recovered by bounded reconcile loop.",
                    payload={
                        "symbol": row.symbol,
                        "client_order_id": row.client_order_id,
                        "reconcile_attempt_count": attempts,
                        "recovered_via": "bounded_reconcile_loop",
                    },
                )
                continue
            next_reconcile_at = observed_at + timedelta(seconds=UNRESOLVED_SUBMISSION_RECONCILE_INTERVAL_SECONDS)
            if reconcile_lookup_error:
                tracking["last_submit_error"] = reconcile_lookup_error
        else:
            next_reconcile_at = next_reconcile_at or (observed_at + timedelta(seconds=UNRESOLVED_SUBMISSION_RECONCILE_INTERVAL_SECONDS))

        deadline_exceeded = observed_at >= final_resolution_deadline
        attempt_budget_exhausted = attempts >= UNRESOLVED_SUBMISSION_MAX_RECONCILE_ATTEMPTS
        guard_payload = {
            "guard_active": True,
            "guard_reason_code": (
                UNRESOLVED_SUBMISSION_DEADLINE_REASON_CODE
                if (deadline_exceeded or attempt_budget_exhausted)
                else UNRESOLVED_SUBMISSION_GUARD_REASON_CODE
            ),
            "order_id": row.id,
            "client_order_id": row.client_order_id,
            "final_resolution_deadline": final_resolution_deadline.isoformat(),
            "next_reconcile_at": next_reconcile_at.isoformat() if next_reconcile_at is not None else None,
            "reconcile_attempt_count": attempts,
            "updated_at": observed_at.isoformat(),
        }
        set_unresolved_submission_guard(
            settings_row,
            symbol=row.symbol,
            action=action,
            payload=guard_payload,
        )
        tracking.update(
            {
                "submission_state": "submit_unknown",
                "reconcile_attempt_count": attempts,
                "next_reconcile_at": next_reconcile_at.isoformat() if next_reconcile_at is not None else None,
                "final_resolution_deadline": final_resolution_deadline.isoformat(),
                "unresolved_guard_active": True,
                "updated_at": observed_at.isoformat(),
            }
        )
        metadata["submission_tracking"] = tracking
        row.metadata_json = metadata
        if UNKNOWN_SUBMISSION_REASON_CODE not in row.reason_codes:
            row.reason_codes = [*row.reason_codes, UNKNOWN_SUBMISSION_REASON_CODE]
        if deadline_exceeded or attempt_budget_exhausted:
            if UNRESOLVED_SUBMISSION_DEADLINE_REASON_CODE not in row.reason_codes:
                row.reason_codes = [*row.reason_codes, UNRESOLVED_SUBMISSION_DEADLINE_REASON_CODE]
            if not tracking.get("deadline_notified_at"):
                tracking["deadline_notified_at"] = observed_at.isoformat()
                record_audit_event(
                    session,
                    event_type="live_order_submission_unresolved_guarded",
                    entity_type="order",
                    entity_id=str(row.id),
                    severity="warning",
                    message="Submission-unknown order exceeded bounded reconcile window and entry guard remains active.",
                    payload={
                        "symbol": row.symbol,
                        "client_order_id": row.client_order_id,
                        "reconcile_attempt_count": attempts,
                        "final_resolution_deadline": final_resolution_deadline.isoformat(),
                    },
                )
                record_health_event(
                    session,
                    component="live_sync",
                    status="degraded",
                    message="Submission-unknown order remained unresolved after bounded reconcile attempts.",
                    payload={
                        "symbol": row.symbol,
                        "order_id": row.id,
                        "client_order_id": row.client_order_id,
                        "reconcile_attempt_count": attempts,
                    },
                )

        unresolved_payloads.append(
            {
                "order_id": row.id,
                "symbol": row.symbol,
                "action": action,
                "client_order_id": row.client_order_id,
                "reconcile_attempt_count": attempts,
                "next_reconcile_at": next_reconcile_at.isoformat() if next_reconcile_at is not None else None,
                "final_resolution_deadline": final_resolution_deadline.isoformat(),
                "deadline_exceeded": deadline_exceeded or attempt_budget_exhausted,
                "guard_reason_code": guard_payload["guard_reason_code"],
            }
        )
        session.add(row)

    guards = list_unresolved_submission_guards(settings_row, symbol=symbol)
    active_symbols = sorted(
        {
            str(item.get("symbol") or "").upper()
            for item in guards
            if bool(item.get("guard_active"))
        }
    )
    return {
        "unresolved_submission_badge": bool(guards),
        "unresolved_submission_count": len(guards),
        "unresolved_submission_symbols": active_symbols,
        "unresolved_submissions": unresolved_payloads or [dict(item) for item in guards],
    }


def _build_deterministic_client_order_id(
    *,
    seed: str | None,
    suffix: str,
) -> str | None:
    if not seed:
        return None
    digest = sha1(f"{seed}:{suffix}".encode()).hexdigest()[:24]
    return f"mvp-{digest}"


def _is_order_not_found_error(exc: Exception) -> bool:
    if not isinstance(exc, BinanceAPIError):
        return False
    message = exc.api_message.lower()
    return exc.code in {-2013, -2011} or "unknown order" in message or "does not exist" in message or "not found" in message


def _is_duplicate_client_order_id_error(exc: Exception) -> bool:
    if not isinstance(exc, BinanceAPIError):
        return False
    message = exc.api_message.lower()
    if exc.code == -2010 and "duplicate" in message:
        return True
    return "duplicate" in message and "client" in message


def _annotate_submission_exception(
    exc: Exception,
    *,
    client_order_id: str,
    submit_request: dict[str, Any],
    submission_tracking: dict[str, Any],
) -> Exception:
    exc.client_order_id = client_order_id
    exc.submit_request = dict(submit_request)
    exc.submission_tracking = dict(submission_tracking)
    return exc


def _submit_exchange_order(
    client: BinanceClient,
    *,
    symbol: str,
    side: str,
    order_type: str,
    quantity: float | None = None,
    price: float | None = None,
    stop_price: float | None = None,
    reduce_only: bool = False,
    close_position: bool = False,
    response_type: str = "RESULT",
    time_in_force: str | None = None,
    client_order_id: str | None = None,
) -> dict[str, object]:
    if not client_order_id:
        raise RuntimeError("Live order submissions require client_order_id for reconciliation.")
    if price is None and time_in_force is None:
        return client.new_order(
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            stop_price=stop_price,
            reduce_only=reduce_only,
            close_position=close_position,
            client_order_id=client_order_id,
            response_type=response_type,
        )
    if time_in_force is None:
        return client.new_order(
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            stop_price=stop_price,
            reduce_only=reduce_only,
            close_position=close_position,
            client_order_id=client_order_id,
            response_type=response_type,
        )
    return client.new_order(
        symbol=symbol,
        side=side,
        order_type=order_type,
        quantity=quantity,
        price=price,
        stop_price=stop_price,
        reduce_only=reduce_only,
        close_position=close_position,
        client_order_id=client_order_id,
        response_type=response_type,
        time_in_force=time_in_force,
    )


def _reconcile_unknown_submission(
    client: BinanceClient,
    *,
    symbol: str,
    order_type: str,
    client_order_id: str,
) -> dict[str, object] | None:
    try:
        return _fetch_exchange_order(
            client,
            symbol=symbol,
            order_type=order_type,
            client_order_id=client_order_id,
        )
    except BinanceAPIError as exc:
        if _is_order_not_found_error(exc):
            return None
        raise


def _safe_submit_order(
    client: BinanceClient,
    *,
    symbol: str,
    side: str,
    order_type: str,
    quantity: float | None = None,
    price: float | None = None,
    stop_price: float | None = None,
    reduce_only: bool = False,
    close_position: bool = False,
    response_type: str = "RESULT",
    time_in_force: str | None = None,
    client_order_id: str | None = None,
    reference_price: float | None = None,
    approved_notional: float | None = None,
    enforce_min_notional: bool = True,
) -> tuple[str, dict[str, object], dict[str, Any], dict[str, Any]]:
    submit_request = _normalize_submit_request(
        client,
        symbol=symbol,
        quantity=quantity,
        price=price,
        stop_price=stop_price,
        reference_price=reference_price,
        approved_notional=approved_notional,
        enforce_min_notional=enforce_min_notional,
        close_position=close_position,
    )
    quantity = submit_request.get("quantity")
    price = submit_request.get("price")
    stop_price = submit_request.get("stop_price")
    client_order_id = client_order_id or f"mvp-{uuid4().hex[:24]}"
    submit_attempt_count = 1
    last_submit_error: str | None = None
    try:
        response = _submit_exchange_order(
            client,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            stop_price=stop_price,
            reduce_only=reduce_only,
            close_position=close_position,
            response_type=response_type,
            time_in_force=time_in_force,
            client_order_id=client_order_id,
        )
        submission_tracking = _build_submission_tracking(
            submission_state="reconciled",
            client_order_id=client_order_id,
            submit_attempt_count=submit_attempt_count,
            recovered_via="submit_ack",
        )
        return client_order_id, response, submit_request, submission_tracking
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        last_submit_error = _stringify_submit_error(exc)

    try:
        reconciled_response = _reconcile_unknown_submission(
            client,
            symbol=symbol,
            order_type=order_type,
            client_order_id=client_order_id,
        )
    except Exception as exc:
        raise OrderSubmissionUnknownError(
            client_order_id=client_order_id,
            submit_request=submit_request,
            submit_attempt_count=submit_attempt_count,
            last_submit_error=last_submit_error or _stringify_submit_error(exc),
        ) from exc
    if reconciled_response is not None:
        submission_tracking = _build_submission_tracking(
            submission_state="reconciled",
            client_order_id=client_order_id,
            submit_attempt_count=submit_attempt_count,
            last_submit_error=last_submit_error,
            recovered_via="client_order_id_lookup",
        )
        return client_order_id, reconciled_response, submit_request, submission_tracking

    raise OrderSubmissionUnknownError(
        client_order_id=client_order_id,
        submit_request=submit_request,
        submit_attempt_count=submit_attempt_count,
        last_submit_error=last_submit_error,
    )


def _record_submission_recovery_event(
    session: Session,
    *,
    order: Order,
    symbol: str,
    submission_tracking: dict[str, Any],
    context: str,
    requested_quantity: float,
    requested_price: float,
    correlation_ids: dict[str, Any] | None = None,
) -> None:
    recovered_via = str(submission_tracking.get("recovered_via") or "")
    if not recovered_via or recovered_via == "submit_ack":
        return
    payload = {
        "symbol": symbol,
        "order_id": order.id,
        "client_order_id": order.client_order_id,
        "context": context,
        "requested_quantity": requested_quantity,
        "requested_price": requested_price,
        "submission_tracking": submission_tracking,
    }
    record_audit_event(
        session,
        event_type="live_order_submission_recovered",
        entity_type="order",
        entity_id=str(order.id),
        severity="warning",
        message="Live order submission required reconcile recovery before confirmation.",
        payload=payload,
        correlation_ids=correlation_ids,
    )
    record_health_event(
        session,
        component="live_execution",
        status="warning",
        message="Live order submission recovered after timeout/transport failure.",
        payload=payload,
        correlation_ids=correlation_ids,
    )


def _execution_policy_sleep(seconds: int) -> None:
    if seconds > 0:
        time.sleep(seconds)


def _resolve_live_reference_price(
    client: BinanceClient,
    *,
    symbol: str,
    fallback_price: float,
) -> float:
    try:
        if hasattr(client, "get_symbol_price"):
            return max(float(client.get_symbol_price(symbol)), 0.0) or fallback_price
    except Exception:
        return fallback_price
    return fallback_price


def _compute_limit_reprice(
    client: BinanceClient,
    *,
    symbol: str,
    side: str,
    current_price: float,
    live_reference_price: float,
    reprice_bps: float,
) -> float:
    adjustment = max(reprice_bps, 0.0) / 10000.0
    if side.upper() == "BUY":
        candidate = min(live_reference_price, current_price * (1.0 + adjustment))
    else:
        candidate = max(live_reference_price, current_price * (1.0 - adjustment))
    if hasattr(client, "normalize_price"):
        return client.normalize_price(symbol, candidate)
    return candidate


def _normalize_remaining_quantity(
    client: BinanceClient,
    *,
    symbol: str,
    remaining_quantity: float,
    reference_price: float,
) -> float:
    if remaining_quantity <= 0:
        return 0.0
    normalized = client.normalize_order_quantity(
        symbol,
        remaining_quantity,
        reference_price=reference_price,
        enforce_min_notional=False,
    )
    if normalized > remaining_quantity:
        return 0.0
    return normalized


def _remaining_fill_ratio(*, requested_quantity: float, filled_quantity: float) -> float:
    if requested_quantity <= 0:
        return 0.0
    remaining = max(requested_quantity - filled_quantity, 0.0)
    return remaining / requested_quantity


def _classify_execution_quality(
    *,
    requested_quantity: float,
    filled_quantity: float,
    execution_attempts: list[dict[str, object]],
    aggressive_fallback_used: bool,
    slippage_pct: float,
    slippage_threshold_pct: float,
) -> tuple[str, str]:
    fill_ratio = 0.0 if requested_quantity <= 0 else min(filled_quantity / requested_quantity, 1.0)
    partial_fill_attempts = sum(
        1
        for attempt in execution_attempts
        if float(attempt.get("filled_quantity") or 0.0) > 0
        and float(attempt.get("filled_quantity") or 0.0) + 1e-9 < float(attempt.get("requested_quantity") or 0.0)
    )
    repriced_attempts = max(len(execution_attempts) - 1, 0)
    timed_out_attempts = sum(1 for attempt in execution_attempts if bool(attempt.get("timed_out")))

    if fill_ratio < 0.999:
        return "incomplete_fill", "signal_outcome_pending"
    if aggressive_fallback_used:
        return "aggressive_completion", "signal_outcome_pending"
    if timed_out_attempts > 0 or repriced_attempts > 0:
        return "repriced_completion", "signal_outcome_pending"
    if partial_fill_attempts > 0:
        return "partial_fill_recovered", "signal_outcome_pending"
    if slippage_pct > slippage_threshold_pct:
        return "high_slippage", "signal_outcome_pending"
    return "clean_fill", "signal_outcome_pending"


def _build_execution_quality_summary(
    *,
    plan: ExecutionPlan,
    side: str,
    requested_quantity: float,
    requested_price: float,
    filled_quantity: float,
    average_fill_price: float,
    fee_paid: float,
    realized_pnl: float,
    execution_attempts: list[dict[str, object]],
    slippage_threshold_pct: float,
    aggressive_fallback_used: bool,
) -> dict[str, object]:
    slippage_pct = 0.0
    if filled_quantity > 0 and average_fill_price > 0:
        slippage_pct = abs(average_fill_price - requested_price) / max(requested_price, 1.0)
    signed_slippage_bps = _signed_slippage_bps(
        side=side,
        requested_price=requested_price,
        fill_price=average_fill_price if filled_quantity > 0 else 0.0,
    )
    fill_ratio = 0.0 if requested_quantity <= 0 else min(filled_quantity / requested_quantity, 1.0)
    timed_out_attempts = sum(1 for attempt in execution_attempts if bool(attempt.get("timed_out")))
    partial_fill_attempts = sum(
        1
        for attempt in execution_attempts
        if float(attempt.get("filled_quantity") or 0.0) > 0
        and float(attempt.get("filled_quantity") or 0.0) + 1e-9 < float(attempt.get("requested_quantity") or 0.0)
    )
    execution_quality_status, decision_quality_status = _classify_execution_quality(
        requested_quantity=requested_quantity,
        filled_quantity=filled_quantity,
        execution_attempts=execution_attempts,
        aggressive_fallback_used=aggressive_fallback_used,
        slippage_pct=slippage_pct,
        slippage_threshold_pct=slippage_threshold_pct,
    )
    entry_execution_type = _entry_execution_type_for_plan(
        plan,
        execution_quality={"aggressive_fallback_used": aggressive_fallback_used},
    )
    return {
        "policy_profile": plan.policy_profile,
        "symbol_risk_tier": plan.symbol_risk_tier,
        "timeframe_bucket": plan.timeframe_bucket,
        "volatility_regime": plan.volatility_regime,
        "urgency": plan.urgency,
        "requested_quantity": requested_quantity,
        "filled_quantity": filled_quantity,
        "remaining_quantity": max(requested_quantity - filled_quantity, 0.0),
        "fill_ratio": fill_ratio,
        "attempt_count": len(execution_attempts),
        "repriced_attempts": max(len(execution_attempts) - 1, 0),
        "timed_out_attempts": timed_out_attempts,
        "partial_fill_attempts": partial_fill_attempts,
        "aggressive_fallback_used": aggressive_fallback_used,
        "realized_slippage_pct": slippage_pct,
        "signed_slippage_pct": signed_slippage_bps / 10000.0,
        "signed_slippage_bps": signed_slippage_bps,
        "slippage_threshold_pct": slippage_threshold_pct,
        "fees_total": fee_paid,
        "realized_pnl_total": realized_pnl,
        "net_realized_pnl_total": realized_pnl - fee_paid,
        "execution_quality_status": execution_quality_status,
        "decision_quality_status": decision_quality_status,
        "entry_execution_type": entry_execution_type,
        "signal_vs_execution_note": (
            "Execution quality is measured separately from signal outcome; signal outcome stays pending until realized PnL closes."
        ),
    }


def _protective_prices(
    open_orders: list[dict[str, object]],
    existing: Position | None,
    *,
    position_mode: str = POSITION_MODE_ONE_WAY,
) -> tuple[float | None, float | None]:
    stop_loss = existing.stop_loss if existing is not None else None
    take_profit = existing.take_profit if existing is not None else None
    relevant_orders = _filter_orders_for_position_context(
        open_orders,
        position_mode=position_mode,
        exchange_position_side=_position_metadata_side(existing),
    )
    for item in relevant_orders:
        order_type = str(item.get("type", "")).upper()
        if existing is not None and _is_reduce_only_take_profit_limit(existing, item):
            price = _to_float(item.get("price"))
            if price > 0:
                take_profit = price
            continue
        stop_price_raw = item.get("stopPrice")
        if stop_price_raw in {None, "", "0", 0}:
            continue
        stop_price = _to_float(stop_price_raw)
        if order_type.startswith("STOP"):
            stop_loss = stop_price
        elif order_type.startswith("TAKE_PROFIT"):
            take_profit = stop_price
    return stop_loss, take_profit


def _remote_position_side_snapshot(
    remote_positions: list[dict[str, object]],
) -> tuple[list[dict[str, object]], list[str]]:
    active_positions: list[dict[str, object]] = []
    active_sides: list[str] = []
    for item in remote_positions:
        position_amount = _to_float(item.get("positionAmt"))
        if abs(position_amount) <= 1e-9:
            continue
        active_positions.append(dict(item))
        position_side = _normalize_exchange_position_side(item.get("positionSide")) or "BOTH"
        if position_side not in active_sides:
            active_sides.append(position_side)
    return active_positions, active_sides


def _symbol_order_position_sides(open_orders: list[dict[str, object]]) -> list[str]:
    sides: list[str] = []
    for item in open_orders:
        position_side = _exchange_order_position_side(item) or "BOTH"
        if position_side not in sides:
            sides.append(position_side)
    return sides


def _resolve_remote_position_mapping(
    remote_positions: list[dict[str, object]],
) -> dict[str, object]:
    active_positions, remote_position_sides = _remote_position_side_snapshot(remote_positions)
    if not active_positions:
        return {
            "status": "flat",
            "active_remote_count": 0,
            "remote_position_sides": remote_position_sides,
            "ambiguous": False,
            "active_remote": None,
            "mapped_side": None,
            "exchange_position_side": None,
        }
    if len(active_positions) != 1:
        return {
            "status": "ambiguous",
            "active_remote_count": len(active_positions),
            "remote_position_sides": remote_position_sides,
            "ambiguous": True,
            "active_remote": None,
            "mapped_side": None,
            "exchange_position_side": None,
        }
    active_remote = dict(active_positions[0])
    position_amount = _to_float(active_remote.get("positionAmt"))
    exchange_position_side = _normalize_exchange_position_side(active_remote.get("positionSide")) or "BOTH"
    if exchange_position_side == "LONG":
        mapped_side = "long"
    elif exchange_position_side == "SHORT":
        mapped_side = "short"
    else:
        mapped_side = "long" if position_amount > 0 else "short"
    return {
        "status": "open",
        "active_remote_count": 1,
        "remote_position_sides": remote_position_sides,
        "ambiguous": False,
        "active_remote": active_remote,
        "mapped_side": mapped_side,
        "exchange_position_side": exchange_position_side,
    }


def sync_live_positions(
    session: Session,
    settings_row: Setting,
    *,
    symbol: str,
    client: BinanceClient | None = None,
    open_orders: list[dict[str, object]] | None = None,
    remote_positions: list[dict[str, object]] | None = None,
    position_mode: str = POSITION_MODE_ONE_WAY,
    flush_state: bool = True,
) -> dict[str, object]:
    client = client or _build_client(settings_row)
    open_orders = open_orders if open_orders is not None else client.get_open_orders(symbol)
    remote_positions = remote_positions if remote_positions is not None else client.get_position_information(symbol)
    mapping = _resolve_remote_position_mapping(remote_positions)
    with session.no_autoflush:
        local = get_open_position(session, symbol)
    order_position_sides = _symbol_order_position_sides(open_orders)
    position_side_conflict = False
    position_mode_reason_code = _position_mode_guard_reason_code(position_mode)
    if position_mode == POSITION_MODE_ONE_WAY:
        if any(side in {"LONG", "SHORT"} for side in mapping.get("remote_position_sides", [])):
            position_side_conflict = True
        if any(side in {"LONG", "SHORT"} for side in order_position_sides):
            position_side_conflict = True

    if mapping["status"] == "flat":
        if local is not None:
            local.status = "closed"
            local.quantity = 0.0
            local.closed_at = utcnow_naive()
            metadata = _as_object_dict(local.metadata_json)
            metadata["exchange_position_mode"] = position_mode
            metadata["exchange_position_side"] = "BOTH" if position_mode == POSITION_MODE_ONE_WAY else None
            local.metadata_json = metadata
            _sync_closed_position_pnl(session, local)
            session.add(local)
            session.flush([local])
        _record_sync_success(
            session,
            settings_row,
            scope="positions",
            detail={
                "symbol": symbol,
                "position_status": "flat",
                "position_mode": position_mode,
                "remote_position_sides": mapping.get("remote_position_sides", []),
                "open_order_position_sides": order_position_sides,
            },
            flush_state=flush_state,
        )
        return {
            "symbol": symbol,
            "status": "flat",
            "position_mode": position_mode,
            "remote_position_sides": mapping.get("remote_position_sides", []),
            "open_order_position_sides": order_position_sides,
            "position_side_conflict": position_side_conflict,
        }

    if bool(mapping.get("ambiguous")) or position_side_conflict:
        reason_code = POSITION_MODE_MISMATCH_REASON_CODE
        detail = {
            "symbol": symbol,
            "position_mode": position_mode,
            "remote_position_sides": mapping.get("remote_position_sides", []),
            "open_order_position_sides": order_position_sides,
            "active_remote_count": mapping.get("active_remote_count"),
        }
        _record_sync_issue(
            session,
            settings_row,
            scope="positions",
            status="incomplete",
            reason_code=reason_code,
            detail=detail,
        )
        return {
            "symbol": symbol,
            "status": "unmapped",
            "position_mode": position_mode,
            "guard_reason_code": reason_code,
            "remote_position_sides": mapping.get("remote_position_sides", []),
            "open_order_position_sides": order_position_sides,
            "position_side_conflict": True,
        }

    active_remote = dict(mapping.get("active_remote") or {})
    position_amount = _to_float(active_remote.get("positionAmt"))
    entry_price = _to_float(active_remote.get("entryPrice"))
    mark_price = _to_float(active_remote.get("markPrice"), entry_price)
    leverage = _to_float(active_remote.get("leverage"), 1.0)
    quantity = abs(position_amount)
    side = str(mapping.get("mapped_side") or ("long" if position_amount > 0 else "short"))
    exchange_position_side = str(mapping.get("exchange_position_side") or "BOTH")
    stop_loss, take_profit = _protective_prices(
        open_orders,
        local,
        position_mode=position_mode,
    )
    if local is None:
        local = Position(
            symbol=symbol,
            mode="live",
            side=side,
            status="open",
            quantity=quantity,
            entry_price=entry_price,
            mark_price=mark_price,
            leverage=leverage,
            stop_loss=stop_loss or mark_price,
            take_profit=take_profit or mark_price,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            metadata_json={
                "origin": "binance_sync",
                "exchange_position_side": exchange_position_side,
                "exchange_position_mode": position_mode,
            },
        )
        session.add(local)
        session.flush([local])
    else:
        metadata = _as_object_dict(local.metadata_json)
        if "origin" not in metadata:
            metadata["origin"] = "binance_sync"
        metadata["exchange_position_side"] = exchange_position_side
        metadata["exchange_position_mode"] = position_mode
        local.metadata_json = metadata
        local.mode = "live"
        local.side = side
        local.status = "open"
        local.quantity = quantity
        local.entry_price = entry_price
        local.mark_price = mark_price
        local.leverage = leverage
        local.stop_loss = stop_loss or local.stop_loss or mark_price
        local.take_profit = take_profit or local.take_profit or mark_price
        local.closed_at = None
        session.add(local)
        session.flush([local])
    local.unrealized_pnl = (mark_price - entry_price) * quantity if side == "long" else (entry_price - mark_price) * quantity
    session.add(local)
    session.flush([local])
    _record_sync_success(
        session,
        settings_row,
        scope="positions",
        detail={
            "symbol": symbol,
            "position_status": "open",
            "side": side,
            "position_mode": position_mode,
            "exchange_position_side": exchange_position_side,
            "remote_position_sides": mapping.get("remote_position_sides", []),
            "open_order_position_sides": order_position_sides,
            "position_mode_guard_reason_code": position_mode_reason_code,
        },
        flush_state=flush_state,
    )
    return {
        "symbol": symbol,
        "status": "open",
        "position_id": local.id,
        "quantity": local.quantity,
        "side": local.side,
        "position_mode": position_mode,
        "exchange_position_side": exchange_position_side,
        "remote_position_sides": mapping.get("remote_position_sides", []),
        "open_order_position_sides": order_position_sides,
        "position_side_conflict": False,
        "guard_reason_code": position_mode_reason_code,
    }


def _cancel_exit_orders(session: Session, client: BinanceClient, symbol: str) -> None:
    for item in client.get_open_orders(symbol):
        if not _flag_enabled(item.get("closePosition")) and not _flag_enabled(item.get("reduceOnly")):
            continue
        external_order_id = str(item.get("orderId", ""))
        client_order_id = str(item.get("clientOrderId", ""))
        _cancel_exchange_order(
            client,
            symbol=symbol,
            order_payload=item,
            order_id=external_order_id or None,
            client_order_id=client_order_id or None,
        )
        local = None
        if external_order_id:
            local = session.scalar(select(Order).where(Order.external_order_id == external_order_id).limit(1))
        if local is None and client_order_id:
            local = session.scalar(select(Order).where(Order.client_order_id == client_order_id).limit(1))
        if local is not None:
            local.status = "canceled"
            local.exchange_status = "CANCELED"
            local.last_exchange_update_at = utcnow_naive()
            session.add(local)
    session.flush()


def _normalize_partial_take_profit_quantity(
    client: BinanceClient,
    *,
    symbol: str,
    position: Position,
    take_profit: float,
    fraction: float,
) -> float | None:
    raw_quantity = max(float(position.quantity) * min(max(float(fraction), 0.01), 0.99), 0.0)
    if raw_quantity <= 0:
        return None
    try:
        if hasattr(client, "normalize_order_quantity"):
            normalized = client.normalize_order_quantity(
                symbol,
                raw_quantity,
                reference_price=take_profit,
                enforce_min_notional=False,
            )
        else:
            normalized = raw_quantity
    except TypeError:
        normalized = client.normalize_order_quantity(symbol, raw_quantity)  # type: ignore[call-arg]
    quantity = min(abs(_to_float(normalized, raw_quantity)), float(position.quantity))
    if quantity <= 0 or quantity >= float(position.quantity) - 1e-12:
        return None
    return quantity


def _execute_primary_order_with_policy(
    session: Session,
    *,
    client: BinanceClient,
    settings_row: Setting,
    symbol: str,
    side: str,
    execution_plan: ExecutionPlan,
    requested_quantity: float,
    requested_price: float,
    decision_run_id: int,
    risk_row: RiskCheck | None,
    reduce_only: bool,
    close_only: bool,
    intent_type: str,
    approved_notional_cap: float | None = None,
    client_order_id_seed: str | None = None,
    correlation_ids: dict[str, Any] | None = None,
    trade_performance_tags: dict[str, Any] | None = None,
    position_id: int | None = None,
) -> dict[str, Any]:
    root_order: Order | None = None
    final_order: Order | None = None
    total_fee_paid = 0.0
    total_realized_pnl = 0.0
    total_filled_quantity = 0.0
    total_fill_notional = 0.0
    current_quantity = requested_quantity
    current_price = requested_price
    current_order_type: str = execution_plan.order_type
    attempt_index = 0
    execution_attempts: list[dict[str, object]] = []
    aggressive_fallback_used = False

    while current_quantity > 0:
        submit_price = current_price if current_order_type == "LIMIT" else None
        submit_tif = execution_plan.time_in_force if current_order_type == "LIMIT" else None
        approved_notional_remaining = None
        if approved_notional_cap is not None and approved_notional_cap > 0:
            approved_notional_remaining = max(approved_notional_cap - total_fill_notional, 0.0)
        client_order_id, exchange_order, submit_request, submission_tracking = _safe_submit_order(
            client,
            symbol=symbol,
            side=side,
            order_type=current_order_type,
            quantity=current_quantity,
            price=submit_price,
            reduce_only=reduce_only,
            close_position=close_only and current_order_type == "MARKET",
            response_type="RESULT",
            time_in_force=submit_tif,
            client_order_id=_build_deterministic_client_order_id(
                seed=client_order_id_seed,
                suffix=f"primary-{attempt_index + 1}-{current_order_type.lower()}",
            ),
            reference_price=submit_price if submit_price is not None else requested_price,
            approved_notional=approved_notional_remaining,
            enforce_min_notional=not close_only,
        )
        submitted_quantity = _to_float(submit_request.get("quantity"), current_quantity)
        submitted_price = _to_float(
            submit_request.get("price"),
            submit_price if submit_price is not None else requested_price,
        )
        parent_order_id = root_order.id if root_order is not None else None
        order = _upsert_exchange_order_row(
            session,
            symbol=symbol,
            requested_price=submitted_price,
            requested_quantity=submitted_quantity,
            order_type=current_order_type,
            side=side.lower(),
            exchange_order={**exchange_order, "clientOrderId": client_order_id},
            decision_run_id=decision_run_id,
            risk_row=risk_row,
            reduce_only=reduce_only,
            close_only=close_only,
            parent_order_id=parent_order_id,
        )
        if position_id is not None:
            order.position_id = position_id
        order.metadata_json = _merge_trade_performance_tags(
            {
                **(order.metadata_json or {}),
                "execution_policy": execution_plan.to_payload(),
                "entry_execution_type": _entry_execution_type_for_plan(
                    execution_plan,
                    order_type=current_order_type,
                ),
                "execution_attempt": attempt_index + 1,
            },
            trade_performance_tags,
        )
        _apply_submission_tracking(
            order,
            client_order_id=client_order_id,
            submit_request=submit_request,
            submission_tracking=submission_tracking,
        )
        session.add(order)
        session.flush()
        _record_submission_recovery_event(
            session,
            order=order,
            symbol=symbol,
            submission_tracking=submission_tracking,
            context=intent_type,
            requested_quantity=submitted_quantity,
            requested_price=submitted_price,
            correlation_ids=normalize_correlation_ids(correlation_ids, execution_id=order.id),
        )
        if root_order is None:
            root_order = order
        final_order = order

        latest_exchange_order = dict(exchange_order)
        timed_out = False
        if current_order_type == "LIMIT" and execution_plan.timeout_seconds > 0 and order.status in {"pending", "partially_filled"}:
            poll_cycles = max(
                int(max(execution_plan.timeout_seconds, execution_plan.poll_interval_seconds) / max(execution_plan.poll_interval_seconds, 1)),
                1,
            )
            for _ in range(poll_cycles):
                _execution_policy_sleep(execution_plan.poll_interval_seconds)
                latest_exchange_order = _fetch_exchange_order(
                    client,
                    symbol=symbol,
                    order_type=current_order_type,
                    order_id=order.external_order_id,
                    client_order_id=order.client_order_id,
                )
                order = _upsert_exchange_order_row(
                    session,
                    symbol=symbol,
                    requested_price=submitted_price,
                    requested_quantity=submitted_quantity,
                    order_type=current_order_type,
                    side=side.lower(),
                    exchange_order=latest_exchange_order,
                    decision_run_id=decision_run_id,
                    risk_row=risk_row,
                    reduce_only=reduce_only,
                    close_only=close_only,
                    parent_order_id=root_order.id if root_order is not None and root_order.id != order.id else parent_order_id,
                )
                final_order = order
                if order.status not in {"pending", "partially_filled"}:
                    break
            if order.status in {"pending", "partially_filled"}:
                timed_out = True
                record_audit_event(
                    session,
                    event_type="live_limit_timeout",
                    entity_type="order",
                    entity_id=str(order.id),
                    severity="warning",
                    message="Passive limit order timed out before full execution.",
                    payload={
                        "symbol": symbol,
                        "intent_type": intent_type,
                        "attempt": attempt_index + 1,
                        "order_type": current_order_type,
                        "requested_quantity": submitted_quantity,
                        "requested_price": submitted_price,
                        "execution_policy": execution_plan.to_payload(),
                    },
                )

        trades = client.get_account_trades(symbol=symbol, order_id=order.external_order_id)
        fee_paid, realized_pnl = _record_live_trades(session, order, trades)
        filled_quantity = min(_sum_trade_quantity(trades), submitted_quantity)
        average_fill_price = order.average_fill_price or submitted_price
        if filled_quantity <= 0 and order.status in {"filled", "partially_filled"} and order.filled_quantity > 0:
            filled_quantity = min(float(order.filled_quantity or 0.0), submitted_quantity)
            average_fill_price = float(order.average_fill_price or submitted_price)
            record_audit_event(
                session,
                event_type="live_execution_trade_lookup_empty",
                entity_type="order",
                entity_id=str(order.id),
                severity="warning",
                message="Exchange order was filled but account trades were unavailable for execution-quality calculation.",
                payload={
                    "symbol": symbol,
                    "order_id": order.id,
                    "exchange_order_id": order.external_order_id,
                    "client_order_id": order.client_order_id,
                    "fallback_filled_quantity": filled_quantity,
                    "fallback_average_fill_price": average_fill_price,
                },
                correlation_ids=normalize_correlation_ids(correlation_ids, execution_id=order.id),
            )
        fill_slippage_pct = 0.0
        if filled_quantity > 0 and average_fill_price > 0:
            fill_slippage_pct = abs(average_fill_price - requested_price) / max(requested_price, 1.0)
        fill_signed_slippage_bps = _signed_slippage_bps(
            side=side,
            requested_price=requested_price,
            fill_price=average_fill_price if filled_quantity > 0 else 0.0,
        )
        if filled_quantity > 0:
            total_fee_paid += fee_paid
            total_realized_pnl += realized_pnl
            total_filled_quantity += filled_quantity
            total_fill_notional += filled_quantity * max(average_fill_price, 0.0)
            if filled_quantity < submitted_quantity:
                record_audit_event(
                    session,
                    event_type="live_limit_partial_fill",
                    entity_type="order",
                    entity_id=str(order.id),
                    severity="info",
                    message="Limit order received a partial fill.",
                    payload={
                        "symbol": symbol,
                        "intent_type": intent_type,
                        "attempt": attempt_index + 1,
                        "filled_quantity": filled_quantity,
                        "remaining_quantity": max(submitted_quantity - filled_quantity, 0.0),
                        "fill_slippage_pct": fill_slippage_pct,
                        "fill_signed_slippage_bps": fill_signed_slippage_bps,
                        "execution_policy": execution_plan.to_payload(),
                    },
                )

        remaining_quantity = max(requested_quantity - total_filled_quantity, 0.0)
        remaining_ratio = _remaining_fill_ratio(
            requested_quantity=requested_quantity,
            filled_quantity=total_filled_quantity,
        )
        execution_attempts.append(
            {
                "order_id": order.id,
                "exchange_status": order.exchange_status,
                "status": order.status,
                "order_type": current_order_type,
                "requested_quantity": submitted_quantity,
                "requested_price": submitted_price,
                "filled_quantity": filled_quantity,
                "average_fill_price": average_fill_price if filled_quantity > 0 else None,
                "fill_slippage_pct": fill_slippage_pct,
                "fill_signed_slippage_bps": fill_signed_slippage_bps,
                "remaining_quantity": remaining_quantity,
                "remaining_ratio": remaining_ratio,
                "timed_out": timed_out,
            }
        )
        order.metadata_json = _merge_trade_performance_tags(
            {
                **(order.metadata_json or {}),
                "execution_policy": execution_plan.to_payload(),
                "entry_execution_type": _entry_execution_type_for_plan(
                    execution_plan,
                    order_type=current_order_type,
                ),
                "execution_attempt": attempt_index + 1,
                "execution_attempts": execution_attempts,
            },
            trade_performance_tags,
        )
        session.add(order)
        session.flush()

        if current_order_type != "LIMIT" or remaining_quantity <= 0.0:
            break
        if not timed_out and order.status == "filled":
            break

        if order.status not in FINAL_ORDER_STATUSES:
            _cancel_exchange_order(
                client,
                symbol=symbol,
                order_id=order.external_order_id,
                client_order_id=order.client_order_id,
                order_type=current_order_type,
            )
            latest_exchange_order = _fetch_exchange_order(
                client,
                symbol=symbol,
                order_type=current_order_type,
                order_id=order.external_order_id,
                client_order_id=order.client_order_id,
            )
            order = _upsert_exchange_order_row(
                session,
                symbol=symbol,
                requested_price=submitted_price,
                requested_quantity=submitted_quantity,
                order_type=current_order_type,
                side=side.lower(),
                exchange_order=latest_exchange_order,
                decision_run_id=decision_run_id,
                risk_row=risk_row,
                reduce_only=reduce_only,
                close_only=close_only,
                parent_order_id=root_order.id if root_order is not None and root_order.id != order.id else parent_order_id,
            )
            final_order = order

        live_reference_price = _resolve_live_reference_price(
            client,
            symbol=symbol,
            fallback_price=submitted_price,
        )
        current_slippage_pct = abs(live_reference_price - max(submitted_price, 1.0)) / max(live_reference_price, 1.0)
        fallback_skip_reason = None
        if current_order_type == "LIMIT" and execution_plan.fallback_order_type != "MARKET":
            if attempt_index >= execution_plan.max_requotes:
                fallback_skip_reason = "max_requotes_exhausted"
            elif (
                remaining_ratio is not None
                and filled_quantity > 0
                and remaining_ratio <= execution_plan.fallback_after_partial_fill_ratio
            ):
                fallback_skip_reason = "partial_fill_market_fallback_disallowed"
            elif current_slippage_pct >= max(
                settings_row.slippage_threshold_pct * 1.5,
                execution_plan.estimated_slippage_pct * 1.5,
            ):
                fallback_skip_reason = "slippage_market_fallback_disallowed"
            else:
                volatility_multiplier = 1.15 if execution_plan.urgency == "high" else 1.25
                if execution_plan.volatility_pct >= max(
                    settings_row.slippage_threshold_pct * 6.0,
                    execution_plan.volatility_pct * volatility_multiplier,
                ):
                    fallback_skip_reason = "volatility_market_fallback_disallowed"
        if fallback_skip_reason is not None:
            record_audit_event(
                session,
                event_type="live_limit_market_fallback_skipped",
                entity_type="order",
                entity_id=str(order.id),
                severity="info",
                message="Market fallback was skipped by execution policy.",
                payload={
                    "symbol": symbol,
                    "intent_type": intent_type,
                    "attempt": attempt_index + 1,
                    "reason_code": fallback_skip_reason,
                    "remaining_quantity": remaining_quantity,
                    "current_slippage_pct": current_slippage_pct,
                    "remaining_ratio": remaining_ratio,
                    "execution_policy": execution_plan.to_payload(),
                },
                correlation_ids=normalize_correlation_ids(correlation_ids, execution_id=order.id),
            )
            break
        if should_fallback_aggressively(
            execution_plan,
            reprice_attempt=attempt_index,
            current_slippage_pct=current_slippage_pct,
            slippage_threshold_pct=settings_row.slippage_threshold_pct,
            current_volatility_pct=execution_plan.volatility_pct,
            remaining_ratio=remaining_ratio if filled_quantity > 0 else None,
        ):
            aggressive_fallback_used = True
            record_audit_event(
                session,
                event_type="live_limit_aggressive_fallback",
                entity_type="order",
                entity_id=str(order.id),
                severity="warning",
                message="Limit order escalated to aggressive execution fallback.",
                payload={
                    "symbol": symbol,
                    "intent_type": intent_type,
                    "attempt": attempt_index + 1,
                    "remaining_quantity": remaining_quantity,
                    "current_slippage_pct": current_slippage_pct,
                    "remaining_ratio": remaining_ratio,
                    "execution_policy": execution_plan.to_payload(),
                },
            )
            current_order_type = execution_plan.fallback_order_type
            current_quantity = _normalize_remaining_quantity(
                client,
                symbol=symbol,
                remaining_quantity=remaining_quantity,
                reference_price=live_reference_price,
            )
            current_price = live_reference_price
            if current_quantity <= 0:
                break
            attempt_index += 1
            continue

        next_quantity = _normalize_remaining_quantity(
            client,
            symbol=symbol,
            remaining_quantity=remaining_quantity,
            reference_price=live_reference_price,
        )
        if next_quantity <= 0:
            break
        current_quantity = next_quantity
        current_price = _compute_limit_reprice(
            client,
            symbol=symbol,
            side=side,
            current_price=max(submitted_price, 1.0),
            live_reference_price=live_reference_price,
            reprice_bps=execution_plan.reprice_bps,
        )
        record_audit_event(
            session,
            event_type="live_limit_repriced",
            entity_type="order",
            entity_id=str(order.id),
            severity="info",
            message="Limit order was canceled and repriced for another passive attempt.",
            payload={
                "symbol": symbol,
                "intent_type": intent_type,
                "attempt": attempt_index + 2,
                "remaining_quantity": current_quantity,
                "repriced_limit": current_price,
                "execution_policy": execution_plan.to_payload(),
            },
        )
        attempt_index += 1
        if attempt_index > execution_plan.max_requotes and execution_plan.fallback_order_type == "NONE":
            break

    if final_order is None:
        raise RuntimeError("Execution policy did not produce an exchange order.")

    aggregate_avg_fill_price = (
        total_fill_notional / total_filled_quantity if total_filled_quantity > 0 else final_order.average_fill_price
    )
    final_status = final_order.status
    if total_filled_quantity > 0 and total_filled_quantity + 1e-9 < requested_quantity:
        final_status = "partially_filled"
    elif total_filled_quantity >= requested_quantity:
        final_status = "filled"
    execution_quality = _build_execution_quality_summary(
        plan=execution_plan,
        side=side,
        requested_quantity=requested_quantity,
        requested_price=requested_price,
        filled_quantity=total_filled_quantity,
        average_fill_price=float(aggregate_avg_fill_price or 0.0),
        fee_paid=total_fee_paid,
        realized_pnl=total_realized_pnl,
        execution_attempts=execution_attempts,
        slippage_threshold_pct=settings_row.slippage_threshold_pct,
        aggressive_fallback_used=aggressive_fallback_used,
    )
    final_order.metadata_json = _merge_trade_performance_tags(
        {
            **(final_order.metadata_json or {}),
            "execution_policy": execution_plan.to_payload(),
            "entry_execution_type": _entry_execution_type_for_plan(
                execution_plan,
                order_type=final_order.order_type,
                execution_quality=execution_quality,
            ),
            "execution_attempts": execution_attempts,
            "execution_quality": execution_quality,
        },
        trade_performance_tags,
    )
    session.add(final_order)
    session.flush()

    return {
        "order": final_order,
        "fees": total_fee_paid,
        "realized_pnl": total_realized_pnl,
        "filled_quantity": total_filled_quantity,
        "average_fill_price": aggregate_avg_fill_price,
        "status": final_status,
        "attempts": execution_attempts,
        "execution_quality": execution_quality,
    }


def _create_protective_orders(
    session: Session,
    client: BinanceClient,
    *,
    settings_row: Setting,
    decision_run_id: int | None,
    risk_row: RiskCheck | None,
    symbol: str,
    stop_loss: float | None,
    take_profit: float | None,
    parent_order: Order | None,
    position: Position | None,
    existing_open_orders: list[dict[str, object]] | None = None,
    protection_lifecycle: ProtectionLifecycleSnapshot | None = None,
    client_order_id_seed: str | None = None,
    correlation_ids: dict[str, Any] | None = None,
) -> list[int]:
    if position is None or stop_loss is None or take_profit is None:
        return []
    performance_tags = _trade_performance_tags_from_metadata(parent_order.metadata_json if parent_order is not None else {})
    if not performance_tags:
        performance_tags = _trade_performance_tags_from_metadata(position.metadata_json)
    exit_side = "SELL" if position.side == "long" else "BUY"
    created_ids: list[int] = []
    current_state = _build_protection_state(position, existing_open_orders or [])
    missing_components = _get_string_list(current_state, "missing_components")
    take_profit_policy = _take_profit_execution_policy(position)
    requested_orders: list[dict[str, object]] = []
    if "stop_loss" in missing_components:
        requested_orders.append(
            {
                "component": "stop_loss",
                "order_type": "STOP_MARKET",
                "stop_price": client.normalize_price(symbol, stop_loss),
                "quantity": None,
                "reduce_only": True,
                "close_position": True,
                "requested_quantity": position.quantity,
            }
        )
    if "take_profit" in missing_components:
        normalized_take_profit = client.normalize_price(symbol, take_profit)
        tp_limit_policy = _take_profit_limit_policy(
            settings_row,
            position,
            take_profit=normalized_take_profit,
        )
        partial_quantity = (
            _normalize_partial_take_profit_quantity(
                client,
                symbol=symbol,
                position=position,
                take_profit=normalized_take_profit,
                fraction=_to_float(
                    take_profit_policy.get("partial_take_profit_fraction"),
                    PARTIAL_TAKE_PROFIT_FRACTION,
                ),
            )
            if take_profit_policy.get("mode") == "partial_reduce"
            else None
        )
        limit_quantity = partial_quantity if partial_quantity is not None else position.quantity
        tp_limit_enabled = bool(tp_limit_policy.get("tp_limit_enabled")) and limit_quantity > 0
        tp_policy_payload = {
            **take_profit_policy,
            **tp_limit_policy,
            "mode": take_profit_policy.get("mode") if partial_quantity is not None else "full_close",
            "take_profit_order_type": "LIMIT" if tp_limit_enabled else "TAKE_PROFIT_MARKET",
        }
        if tp_limit_enabled:
            market_fallback_spec = {
                "component": "take_profit",
                "order_type": "TAKE_PROFIT_MARKET",
                "stop_price": normalized_take_profit,
                "price": None,
                "time_in_force": None,
                "quantity": partial_quantity,
                "reduce_only": True,
                "close_position": partial_quantity is None,
                "requested_quantity": partial_quantity if partial_quantity is not None else position.quantity,
                "take_profit_execution_policy": {
                    **tp_policy_payload,
                    "take_profit_order_type": "TAKE_PROFIT_MARKET",
                    "tp_limit_enabled": False,
                    "tp_market_fallback_reason": "tp_limit_submit_failed",
                },
            }
            requested_orders.append(
                {
                    "component": "take_profit",
                    "order_type": "LIMIT",
                    "stop_price": None,
                    "price": normalized_take_profit,
                    "time_in_force": "GTX" if bool(tp_limit_policy.get("tp_limit_post_only")) else "GTC",
                    "quantity": limit_quantity,
                    "reduce_only": True,
                    "close_position": False,
                    "requested_quantity": limit_quantity,
                    "take_profit_execution_policy": tp_policy_payload,
                    "market_fallback_spec": market_fallback_spec,
                }
            )
        elif partial_quantity is not None:
            requested_orders.append(
                {
                    "component": "take_profit",
                    "order_type": "TAKE_PROFIT_MARKET",
                    "stop_price": normalized_take_profit,
                    "price": None,
                    "time_in_force": None,
                    "quantity": partial_quantity,
                    "reduce_only": True,
                    "close_position": False,
                    "requested_quantity": partial_quantity,
                    "take_profit_execution_policy": tp_policy_payload,
                }
            )
        else:
            requested_orders.append(
                {
                    "component": "take_profit",
                    "order_type": "TAKE_PROFIT_MARKET",
                    "stop_price": normalized_take_profit,
                    "price": None,
                    "time_in_force": None,
                    "quantity": None,
                    "reduce_only": True,
                    "close_position": True,
                    "requested_quantity": position.quantity,
                    "take_profit_execution_policy": {
                        **tp_policy_payload,
                        "fallback_reason": (
                            "partial_take_profit_quantity_unavailable"
                            if take_profit_policy.get("mode") == "partial_reduce"
                            else tp_limit_policy.get("tp_market_fallback_reason")
                        ),
                    },
                }
            )
    requested_order_types = [str(item["order_type"]) for item in requested_orders]
    if requested_orders:
        if protection_lifecycle is not None and protection_lifecycle.state == "none":
            _transition_protection_lifecycle(
                session,
                lifecycle=protection_lifecycle,
                parent_order=parent_order,
                state="requested",
                transition_reason="protective_orders_requested",
                detail={
                    "missing_components": missing_components,
                    "requested_order_types": requested_order_types,
                },
                requested_components=missing_components,
                requested_order_types=requested_order_types,
                correlation_ids=correlation_ids,
            )
        else:
            _sync_protection_lifecycle_snapshot(
                protection_lifecycle,
                requested_components=missing_components,
                requested_order_types=requested_order_types,
            )
            _persist_protection_lifecycle(session, parent_order, protection_lifecycle)
    for order_spec in requested_orders:
        while True:
            order_type = str(order_spec["order_type"])
            price = _to_float(order_spec.get("price"))
            stop_price = _to_float(order_spec.get("stop_price"))
            close_position = bool(order_spec.get("close_position"))
            reduce_only = bool(order_spec.get("reduce_only"))
            quantity = order_spec.get("quantity")
            requested_quantity = _to_float(order_spec.get("requested_quantity"), position.quantity)
            reference_price = price or stop_price or position.entry_price or position.mark_price
            try:
                client_order_id, exchange_order, submit_request, submission_tracking = _safe_submit_order(
                    client,
                    symbol=symbol,
                    side=exit_side,
                    order_type=order_type,
                    quantity=float(quantity) if quantity not in {None, ""} else None,
                    price=price if price > 0 else None,
                    stop_price=stop_price if stop_price > 0 else None,
                    reduce_only=reduce_only,
                    close_position=close_position,
                    response_type="ACK",
                    time_in_force=str(order_spec.get("time_in_force") or "") or None,
                    client_order_id=_build_deterministic_client_order_id(
                        seed=client_order_id_seed,
                        suffix=f"protective-{order_type.lower()}-{order_spec.get('component')}",
                    ),
                    reference_price=reference_price,
                    enforce_min_notional=False,
                )
                break
            except Exception as exc:
                fallback_spec = order_spec.get("market_fallback_spec")
                if order_type != "LIMIT" or not isinstance(fallback_spec, dict):
                    raise
                record_audit_event(
                    session,
                    event_type="protective_take_profit_limit_fallback",
                    entity_type="position",
                    entity_id=str(position.id) if position.id is not None else symbol,
                    severity="warning",
                    message="Limit take-profit protective order failed; falling back to TAKE_PROFIT_MARKET.",
                    payload={
                        "symbol": symbol,
                        "take_profit_order_type": order_type,
                        "fallback_order_type": fallback_spec.get("order_type"),
                        "tp_limit_enabled": True,
                        "tp_market_fallback_reason": "tp_limit_submit_failed",
                        "error": str(exc),
                    },
                    correlation_ids=correlation_ids,
                )
                session.flush()
                order_spec = dict(fallback_spec)
        normalized_order_price = _to_float(
            submit_request.get("price"),
            _to_float(submit_request.get("stop_price"), price or stop_price),
        )
        if not close_position:
            requested_quantity = _to_float(submit_request.get("quantity"), requested_quantity)
        row = _upsert_exchange_order_row(
            session,
            symbol=symbol,
            requested_price=normalized_order_price,
            requested_quantity=requested_quantity,
            order_type=order_type,
            side=exit_side.lower(),
            exchange_order={**exchange_order, "clientOrderId": client_order_id},
            decision_run_id=decision_run_id,
            risk_row=risk_row,
            reduce_only=reduce_only,
            close_only=close_position,
            parent_order_id=parent_order.id if parent_order is not None else None,
        )
        row.position_id = position.id
        row_metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        row.metadata_json = _merge_trade_performance_tags(
            {
                **row_metadata,
                "protective_component": order_spec.get("component"),
            },
            performance_tags,
        )
        if order_spec.get("take_profit_execution_policy") is not None:
            row.metadata_json["take_profit_execution_policy"] = dict(
                order_spec["take_profit_execution_policy"]  # type: ignore[arg-type]
            )
        _apply_submission_tracking(
            row,
            client_order_id=client_order_id,
            submit_request=submit_request,
            submission_tracking=submission_tracking,
        )
        session.add(row)
        session.flush()
        _record_submission_recovery_event(
            session,
            order=row,
            symbol=symbol,
            submission_tracking=submission_tracking,
            context="protective_order",
            requested_quantity=requested_quantity,
            requested_price=normalized_order_price,
            correlation_ids=normalize_correlation_ids(correlation_ids, execution_id=row.id),
        )
        if order_spec.get("component") == "take_profit":
            tp_policy = (
                dict(order_spec["take_profit_execution_policy"])
                if isinstance(order_spec.get("take_profit_execution_policy"), dict)
                else {}
            )
            record_audit_event(
                session,
                event_type="protective_take_profit_order_created",
                entity_type="position",
                entity_id=str(position.id) if position.id is not None else symbol,
                severity="info",
                message="Protective take-profit order was created.",
                payload={
                    "symbol": symbol,
                    "order_id": row.id,
                    "take_profit_order_type": order_type,
                    "tp_limit_enabled": bool(tp_policy.get("tp_limit_enabled")),
                    "tp_limit_post_only": bool(tp_policy.get("tp_limit_post_only")),
                    "tp_market_fallback_reason": tp_policy.get("tp_market_fallback_reason"),
                    "reduce_only": reduce_only,
                    "close_position": close_position,
                    "requested_price": normalized_order_price,
                    "requested_quantity": requested_quantity,
                },
                correlation_ids=normalize_correlation_ids(correlation_ids, execution_id=row.id),
            )
            session.flush()
        created_ids.append(row.id)
    if created_ids:
        if protection_lifecycle is not None and protection_lifecycle.state != "placed":
            _transition_protection_lifecycle(
                session,
                lifecycle=protection_lifecycle,
                parent_order=parent_order,
                state="placed",
                transition_reason="protective_orders_placed",
                detail={
                    "created_order_ids": created_ids,
                    "requested_order_types": requested_order_types,
                },
                created_order_ids=created_ids,
                correlation_ids=correlation_ids,
            )
        else:
            _sync_protection_lifecycle_snapshot(
                protection_lifecycle,
                created_order_ids=created_ids,
            )
            _persist_protection_lifecycle(session, parent_order, protection_lifecycle)
    return created_ids


def _pause_for_protection_failure(
    session: Session,
    settings_row: Setting,
    *,
    reason_code: str,
    symbol: str,
    position: Position | None,
    protective_state: dict[str, object],
    detail: str,
    emergency_result: dict[str, object] | None = None,
    correlation_ids: dict[str, Any] | None = None,
) -> None:
    emergency_completed = bool(
        emergency_result is not None
        and emergency_result.get("status") == "completed"
        and _to_float(emergency_result.get("remaining_position"), 0.0) <= 0.0
    )
    if emergency_completed:
        clear_symbol_protection_state(
            session,
            settings_row,
            symbol=symbol,
            trigger_source=f"protection_failure:{reason_code}:recovered_flat",
        )
    else:
        mark_manage_only_state(
            session,
            settings_row,
            symbol=symbol,
            trigger_source=f"protection_failure:{reason_code}",
            missing_components=_get_string_list(protective_state, "missing_components"),
            last_error=detail,
            emergency_action=emergency_result or None,
        )
    payload = {
        "reason_code": reason_code,
        "operating_state": TRADABLE_STATE if emergency_completed else DEGRADED_MANAGE_ONLY_STATE,
        "symbol": symbol,
        "position_size": position.quantity if position is not None else 0.0,
        "protective_state": protective_state,
        "detail": detail,
        "emergency_result": emergency_result or {},
    }
    record_audit_event(
        session,
        event_type="protection_manage_only_enabled",
        entity_type="settings",
        entity_id=str(settings_row.id),
        severity="critical",
        message="Trading entry was blocked and management-only mode was enabled after a protection failure.",
        payload=payload,
        correlation_ids=correlation_ids,
    )
    create_alert(
        session,
        category="execution",
        severity="critical",
        title="Unprotected live position detected",
        message="포지션 보호 주문이 없거나 검증되지 않아 비상 청산 후 거래를 중지했습니다.",
        payload=payload,
    )
    record_health_event(
        session,
        component="live_execution",
        status="critical",
        message="Unprotected live position triggered emergency handling.",
        payload=payload,
        correlation_ids=correlation_ids,
    )
    session.flush()


def _emergency_close_position(
    session: Session,
    settings_row: Setting,
    client: BinanceClient,
    *,
    symbol: str,
    position: Position | None,
    reason: str,
    protection_state: dict[str, object],
    correlation_ids: dict[str, Any] | None = None,
) -> dict[str, object]:
    if position is None or position.status != "open" or position.quantity <= 0:
        return {"status": "skipped", "reason": "NO_OPEN_POSITION"}

    set_symbol_protection_state(
        session,
        settings_row,
        symbol=symbol,
        state=EMERGENCY_EXIT_STATE,
        trigger_source=reason,
        missing_components=_get_string_list(protection_state, "missing_components"),
        auto_recovery_active=False,
        recovery_status="emergency_exit",
        last_error=None,
    )
    side = "SELL" if position.side == "long" else "BUY"
    reference_price = position.mark_price if position.mark_price > 0 else position.entry_price
    quantity = client.normalize_order_quantity(
        symbol,
        position.quantity,
        reference_price=reference_price,
        enforce_min_notional=False,
    )
    trigger_payload = {
        "symbol": symbol,
        "position_size": position.quantity,
        "reason": reason,
        "protective_state": protection_state,
        "quantity": quantity,
    }
    record_audit_event(
        session,
        event_type="emergency_exit_triggered",
        entity_type="position",
        entity_id=str(position.id),
        severity="critical",
        message="Emergency exit triggered for unprotected live position.",
        payload=trigger_payload,
        correlation_ids=correlation_ids,
    )

    try:
        client_order_id, exchange_order, submit_request, submission_tracking = _safe_submit_order(
            client,
            symbol=symbol,
            side=side,
            order_type="MARKET",
            quantity=quantity,
            reduce_only=True,
            response_type="RESULT",
            reference_price=reference_price,
        )
        order = _upsert_exchange_order_row(
            session,
            symbol=symbol,
            requested_price=_to_float(submit_request.get("price"), reference_price),
            requested_quantity=_to_float(submit_request.get("quantity"), quantity),
            order_type="MARKET",
            side="exit",
            exchange_order={**exchange_order, "clientOrderId": client_order_id},
            decision_run_id=None,
            risk_row=None,
            reduce_only=True,
            close_only=True,
            parent_order_id=None,
        )
        order.position_id = position.id
        _apply_submission_tracking(
            order,
            client_order_id=client_order_id,
            submit_request=submit_request,
            submission_tracking=submission_tracking,
        )
        session.add(order)
        session.flush()
        _record_submission_recovery_event(
            session,
            order=order,
            symbol=symbol,
            submission_tracking=submission_tracking,
            context="emergency_exit",
            requested_quantity=_to_float(submit_request.get("quantity"), quantity),
            requested_price=_to_float(submit_request.get("price"), reference_price),
            correlation_ids=normalize_correlation_ids(correlation_ids, execution_id=order.id),
        )
        trades = client.get_account_trades(symbol=symbol, order_id=order.external_order_id)
        fee_paid, realized_pnl = _record_live_trades(session, order, trades)
        create_exchange_pnl_snapshot(session, settings_row)
        remaining_orders = client.get_open_orders(symbol)
        sync_live_positions(session, settings_row, symbol=symbol, client=client, open_orders=remaining_orders)
        remaining_position = get_open_position(session, symbol)
        if remaining_position is None:
            _cancel_exit_orders(session, client, symbol)
            clear_symbol_protection_state(
                session,
                settings_row,
                symbol=symbol,
                trigger_source=f"{reason}:flat_after_emergency_exit",
            )
        payload = {
            **trigger_payload,
            "order_id": order.id,
            "exchange_status": order.exchange_status,
            "fill_quantity": order.filled_quantity,
            "fees": fee_paid,
            "realized_pnl": realized_pnl,
            "remaining_position": remaining_position.quantity if remaining_position is not None else 0.0,
        }
        record_audit_event(
            session,
            event_type="emergency_exit_completed",
            entity_type="order",
            entity_id=str(order.id),
            severity="critical",
            message="Emergency exit completed for unprotected live position.",
            payload=payload,
            correlation_ids=correlation_ids,
        )
        return {
            "status": "completed",
            "order_id": order.id,
            "fill_quantity": order.filled_quantity,
            "fees": fee_paid,
            "realized_pnl": realized_pnl,
            "remaining_position": remaining_position.quantity if remaining_position is not None else 0.0,
        }
    except OrderSubmissionUnknownError as exc:
        order = _create_submission_unknown_order_row(
            session,
            symbol=symbol,
            side="exit",
            order_type="MARKET",
            requested_quantity=quantity,
            requested_price=reference_price,
            decision_run_id=None,
            risk_row=None,
            reduce_only=True,
            close_only=True,
            client_order_id=exc.client_order_id,
            submit_request=exc.submit_request,
            submission_tracking=exc.submission_tracking,
            metadata_json={
                "error": str(exc),
                "reason": reason,
                "protective_state": protection_state,
                "emergency_exit": True,
            },
        )
        payload = {
            **trigger_payload,
            "order_id": order.id,
            "client_order_id": exc.client_order_id,
            "submission_tracking": exc.submission_tracking,
        }
        record_audit_event(
            session,
            event_type="emergency_exit_submission_unknown",
            entity_type="order",
            entity_id=str(order.id),
            severity="critical",
            message="Emergency exit submission timed out and now requires reconciliation.",
            payload=payload,
            correlation_ids=normalize_correlation_ids(correlation_ids, execution_id=order.id),
        )
        create_alert(
            session,
            category="execution",
            severity="critical",
            title="Emergency exit submission unknown",
            message="Emergency exit timed out and must be reconciled before retry.",
            payload=payload,
        )
        record_health_event(
            session,
            component="live_execution",
            status="critical",
            message="Emergency exit submission is waiting for reconciliation after timeout/transport failure.",
            payload=payload,
            correlation_ids=normalize_correlation_ids(correlation_ids, execution_id=order.id),
        )
        session.flush()
        return {
            "status": "submission_unknown",
            "order_id": order.id,
            "client_order_id": exc.client_order_id,
            "submission_tracking": exc.submission_tracking,
        }
    except Exception as exc:
        payload = {**trigger_payload, "error": str(exc)}
        record_audit_event(
            session,
            event_type="emergency_exit_failed",
            entity_type="position",
            entity_id=str(position.id),
            severity="critical",
            message="Emergency exit failed for unprotected live position.",
            payload=payload,
            correlation_ids=correlation_ids,
        )
        create_alert(
            session,
            category="execution",
            severity="critical",
            title="Emergency exit failed",
            message="무보호 포지션 비상 청산에 실패했습니다.",
            payload=payload,
        )
        record_health_event(
            session,
            component="live_execution",
            status="critical",
            message="Emergency exit failed for unprotected live position.",
            payload=payload,
            correlation_ids=correlation_ids,
        )
        session.flush()
        return {"status": "failed", "error": str(exc)}


def _ensure_protected_position(
    session: Session,
    settings_row: Setting,
    client: BinanceClient,
    *,
    symbol: str,
    position: Position | None,
    stop_loss: float | None,
    take_profit: float | None,
    decision_run_id: int | None,
    risk_row: RiskCheck | None,
    parent_order: Order | None,
    trigger_source: str,
    pause_reason_code: str,
    protection_lifecycle: ProtectionLifecycleSnapshot | None = None,
    client_order_id_seed: str | None = None,
    correlation_ids: dict[str, Any] | None = None,
) -> dict[str, object]:
    protection_correlation_ids = normalize_correlation_ids(
        correlation_ids,
        decision_id=decision_run_id,
        risk_id=risk_row.id if risk_row is not None else None,
        execution_id=parent_order.id if parent_order is not None else None,
    )
    verification_deadline_at = utcnow_naive() + timedelta(seconds=PROTECTION_VERIFY_DEADLINE_SECONDS)
    try:
        open_orders = client.get_open_orders(symbol)
    except Exception as exc:
        exchange_reason_code = _classify_exchange_state_error(exc, "PROTECTION_STATE_UNVERIFIED")
        detail = f"Protective order verification unavailable: {exc}"
        protection_state = _build_unverified_protection_state(
            position,
            reason_code="PROTECTION_STATE_UNVERIFIED",
            detail=detail,
            deadline_at=verification_deadline_at,
        )
        _record_sync_issue(
            session,
            settings_row,
            scope="protective_orders",
            status="failed",
            reason_code="PROTECTION_STATE_UNVERIFIED",
            detail={
                "symbol": symbol,
                "exchange_reason_code": exchange_reason_code,
                "error": str(exc),
                "verification_deadline_at": verification_deadline_at.isoformat(),
            },
        )
        set_symbol_protection_state(
            session,
            settings_row,
            symbol=symbol,
            state=PROTECTION_REQUIRED_STATE,
            trigger_source=f"{trigger_source}:verification_unavailable",
            missing_components=_get_string_list(protection_state, "missing_components"),
            auto_recovery_active=True,
            recovery_status="verification_unavailable",
            last_error=detail,
        )
        _transition_protection_lifecycle(
            session,
            lifecycle=protection_lifecycle,
            parent_order=parent_order,
            state="verify_failed",
            transition_reason="protective_verification_unavailable",
            detail={
                "error": detail,
                "reason_code": "PROTECTION_STATE_UNVERIFIED",
                "exchange_reason_code": exchange_reason_code,
                "verification_deadline_at": verification_deadline_at.isoformat(),
                "protection_state": protection_state,
            },
            verification_detail={
                "error": detail,
                "reason_code": "PROTECTION_STATE_UNVERIFIED",
                "exchange_reason_code": exchange_reason_code,
                "verification_deadline_at": verification_deadline_at.isoformat(),
                "protection_state": protection_state,
            },
            correlation_ids=protection_correlation_ids,
        )
        _set_symbol_protection_verify_block(
            session,
            settings_row,
            symbol=symbol,
            trigger_source=trigger_source,
            detail=detail,
            protection_state=protection_state,
            created_order_ids=[],
            protection_lifecycle=protection_lifecycle,
        )
        emergency_result = _emergency_close_position(
            session,
            settings_row,
            client,
            symbol=symbol,
            position=position,
            reason=f"{trigger_source}:PROTECTION_STATE_UNVERIFIED",
            protection_state=protection_state,
            correlation_ids=protection_correlation_ids,
        )
        _pause_for_protection_failure(
            session,
            settings_row,
            reason_code="PROTECTION_STATE_UNVERIFIED",
            symbol=symbol,
            position=position,
            protective_state=protection_state,
            detail=detail,
            emergency_result=emergency_result,
            correlation_ids=protection_correlation_ids,
        )
        if emergency_result.get("status") != "completed":
            mark_manage_only_state(
                session,
                settings_row,
                symbol=symbol,
                trigger_source=f"{trigger_source}:verification_unavailable_emergency_failed",
                missing_components=_get_string_list(protection_state, "missing_components"),
                last_error=detail,
                emergency_action=emergency_result,
            )
        return {
            "status": "emergency_exit",
            "protection_state": protection_state,
            "created_order_ids": [],
            "emergency_action": emergency_result,
            "error": detail,
            "protection_lifecycle": _protection_lifecycle_payload(protection_lifecycle),
        }
    protection_state = _build_protection_state(position, open_orders)
    if protection_state["status"] == "protected":
        _clear_symbol_protection_verify_block(
            session,
            settings_row,
            symbol=symbol,
        )
        _transition_protection_lifecycle(
            session,
            lifecycle=protection_lifecycle,
            parent_order=parent_order,
            state="verified",
            transition_reason="exchange_protection_verified",
            detail={"protection_state": protection_state},
            verification_detail={"protection_state": protection_state},
            correlation_ids=protection_correlation_ids,
        )
        clear_symbol_protection_state(
            session,
            settings_row,
            symbol=symbol,
            trigger_source=f"{trigger_source}:already_protected",
        )
        record_audit_event(
            session,
            event_type="protection_recovery_succeeded",
            entity_type="position",
            entity_id=str(position.id if position is not None else symbol),
            severity="info",
            message="Protective order verification confirmed exchange-resident protection.",
            payload={"symbol": symbol, "trigger_source": trigger_source, "protective_state": protection_state},
            correlation_ids=protection_correlation_ids,
        )
        return {
            "status": "protected",
            "protection_state": protection_state,
            "created_order_ids": [],
            "emergency_action": None,
            "protection_lifecycle": _protection_lifecycle_payload(protection_lifecycle),
        }

    failure_detail = get_protection_recovery_detail(settings_row)
    failure_count = int(
        dict(failure_detail.get("symbol_states", {})).get(symbol, {}).get("failure_count", 0) or 0
    )
    set_symbol_protection_state(
        session,
        settings_row,
        symbol=symbol,
        state=PROTECTION_REQUIRED_STATE,
        trigger_source=trigger_source,
        missing_components=_get_string_list(protection_state, "missing_components"),
        auto_recovery_active=True,
        recovery_status="recreating",
        last_error=None,
    )
    record_audit_event(
        session,
        event_type="unprotected_position_detected",
        entity_type="position",
        entity_id=str(position.id if position is not None else symbol),
        severity="critical",
        message="Live position is missing exchange-resident protective orders.",
        payload={
            "symbol": symbol,
            "trigger_source": trigger_source,
            "position_size": position.quantity if position is not None else 0.0,
            "protective_state": protection_state,
        },
        correlation_ids=protection_correlation_ids,
    )
    record_audit_event(
        session,
        event_type="protection_verification_failed",
        entity_type="position",
        entity_id=str(position.id if position is not None else symbol),
        severity="critical",
        message="Protective order verification failed.",
        payload={
            "symbol": symbol,
            "trigger_source": trigger_source,
            "protective_state": protection_state,
        },
        correlation_ids=protection_correlation_ids,
    )

    recreate_error: str | None = None
    created_order_ids: list[int] = []
    position_entity_id = str(position.id) if position is not None else symbol
    if _has_valid_protection_template(position, stop_loss, take_profit):
        for attempt in range(1, PROTECTION_RETRY_ATTEMPTS + 1):
            record_audit_event(
                session,
                event_type="protection_recreate_attempted",
                entity_type="position",
                entity_id=position_entity_id,
                severity="warning",
                message="Attempting to recreate missing protective orders.",
                payload={
                    "symbol": symbol,
                    "attempt": attempt,
                    "trigger_source": trigger_source,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                },
                correlation_ids=protection_correlation_ids,
            )
            try:
                attempt_created_order_ids = _create_protective_orders(
                    session,
                    client,
                    settings_row=settings_row,
                    decision_run_id=decision_run_id,
                    risk_row=risk_row,
                    symbol=symbol,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    parent_order=parent_order,
                    position=position,
                    existing_open_orders=open_orders,
                    protection_lifecycle=protection_lifecycle,
                    client_order_id_seed=client_order_id_seed,
                    correlation_ids=protection_correlation_ids,
                )
                created_order_ids.extend(attempt_created_order_ids)
                verified_orders, verification_failures = _verify_created_protective_orders(
                    session,
                    client,
                    symbol=symbol,
                    order_ids=attempt_created_order_ids,
                )
                open_orders = client.get_open_orders(symbol)
                open_orders = _merge_verified_protective_orders(open_orders, verified_orders)
                _cancel_duplicate_protective_orders(
                    session,
                    client,
                    symbol=symbol,
                    open_orders=open_orders,
                    position=position,
                    preferred_order_ids=created_order_ids,
                )
                open_orders = client.get_open_orders(symbol)
                open_orders = _merge_verified_protective_orders(open_orders, verified_orders)
                protection_state = _build_protection_state(position, open_orders)
                if not verification_failures and protection_state["status"] == "protected":
                    _clear_symbol_protection_verify_block(
                        session,
                        settings_row,
                        symbol=symbol,
                    )
                    _transition_protection_lifecycle(
                        session,
                        lifecycle=protection_lifecycle,
                        parent_order=parent_order,
                        state="verified",
                        transition_reason="recreated_protection_verified",
                        detail={
                            "created_order_ids": created_order_ids,
                            "protection_state": protection_state,
                        },
                        created_order_ids=created_order_ids,
                        verification_detail={
                            "protection_state": protection_state,
                            "created_order_ids": created_order_ids,
                        },
                        correlation_ids=protection_correlation_ids,
                    )
                    clear_symbol_protection_state(
                        session,
                        settings_row,
                        symbol=symbol,
                        trigger_source=f"{trigger_source}:protected_recreated",
                    )
                    record_audit_event(
                        session,
                        event_type="protection_recovery_succeeded",
                        entity_type="position",
                        entity_id=position_entity_id,
                        severity="info",
                        message="Protective orders were recreated and verified on the exchange.",
                        payload={
                            "symbol": symbol,
                            "trigger_source": trigger_source,
                            "created_order_ids": created_order_ids,
                            "protective_state": protection_state,
                        },
                        correlation_ids=protection_correlation_ids,
                    )
                    return {
                        "status": "protected_recreated",
                        "protection_state": protection_state,
                        "created_order_ids": created_order_ids,
                        "emergency_action": None,
                        "protection_lifecycle": _protection_lifecycle_payload(protection_lifecycle),
                    }
                recreate_error = _format_protective_verification_failures(verification_failures)
            except Exception as exc:
                recreate_error = str(exc)
        if recreate_error is None:
            recreate_error = "Protective orders were not verified on the exchange."
        record_audit_event(
            session,
            event_type="protection_recreate_failed",
            entity_type="position",
            entity_id=position_entity_id,
            severity="critical",
            message="Protective order recreation failed.",
            payload={
                "symbol": symbol,
                "trigger_source": trigger_source,
                "error": recreate_error,
                "protective_state": protection_state,
            },
            correlation_ids=protection_correlation_ids,
        )
    else:
        recreate_error = "Local stop loss / take profit template was unavailable."

    _transition_protection_lifecycle(
        session,
        lifecycle=protection_lifecycle,
        parent_order=parent_order,
        state="verify_failed",
        transition_reason="protective_verification_failed",
        detail={
            "error": recreate_error,
            "missing_components": protection_state.get("missing_components", []),
            "protection_state": protection_state,
        },
        created_order_ids=created_order_ids,
        verification_detail={
            "error": recreate_error,
            "protection_state": protection_state,
        },
        correlation_ids=protection_correlation_ids,
    )
    _set_symbol_protection_verify_block(
        session,
        settings_row,
        symbol=symbol,
        trigger_source=trigger_source,
        detail=recreate_error,
        protection_state=protection_state,
        created_order_ids=created_order_ids,
        protection_lifecycle=protection_lifecycle,
    )

    next_failure_count = failure_count + 1
    if next_failure_count >= PROTECTION_RECOVERY_THRESHOLD:
        mark_manage_only_state(
            session,
            settings_row,
            symbol=symbol,
            trigger_source=f"{trigger_source}:recovery_failed",
            missing_components=_get_string_list(protection_state, "missing_components"),
            last_error=recreate_error or "Protective order recovery failed.",
        )

    emergency_result = _emergency_close_position(
        session,
        settings_row,
        client,
        symbol=symbol,
        position=position,
        reason=f"{trigger_source}:{pause_reason_code}",
        protection_state=protection_state,
        correlation_ids=protection_correlation_ids,
    )
    _pause_for_protection_failure(
        session,
        settings_row,
        reason_code=pause_reason_code,
        symbol=symbol,
        position=position,
        protective_state=protection_state,
        detail=recreate_error or "Protective orders were missing and emergency exit was triggered.",
        emergency_result=emergency_result,
        correlation_ids=protection_correlation_ids,
    )
    if emergency_result.get("status") != "completed":
        mark_manage_only_state(
            session,
            settings_row,
            symbol=symbol,
            trigger_source=f"{trigger_source}:emergency_failed",
            missing_components=_get_string_list(protection_state, "missing_components"),
            last_error=recreate_error or "Emergency exit failed after protective recovery failure.",
            emergency_action=emergency_result,
        )
    return {
        "status": "emergency_exit",
        "protection_state": protection_state,
        "created_order_ids": created_order_ids,
        "emergency_action": emergency_result,
        "error": recreate_error,
        "protection_lifecycle": _protection_lifecycle_payload(protection_lifecycle),
    }


def sync_live_state(
    session: Session,
    settings_row: Setting,
    *,
    symbol: str | None = None,
    allow_protection_recovery: bool = True,
) -> dict[str, object]:
    client = _build_client(settings_row)
    symbols = _resolve_sync_symbols(settings_row, symbol)
    stream_poll = poll_live_user_stream(
        session,
        settings_row,
        client=client,
        max_events=max(4, len(symbols) * 4),
        idle_timeout_seconds=0.1,
        flush_state=False,
    )
    stream_events = [dict(item) for item in stream_poll.get("stream_events", []) if isinstance(item, dict)]
    user_stream_summary = (
        dict(stream_poll.get("user_stream_summary"))
        if isinstance(stream_poll.get("user_stream_summary"), dict)
        else get_user_stream_detail(settings_row)
    )
    pending_user_stream_state = build_user_stream_state(user_stream_summary)
    user_stream_summary = dict(pending_user_stream_state)
    reconcile_started_at = utcnow_naive()
    position_mode = POSITION_MODE_ONE_WAY
    position_mode_source = "assumed_default"
    position_mode_lookup_error: str | None = None
    if hasattr(client, "get_position_mode"):
        try:
            position_mode_payload = client.get_position_mode()  # type: ignore[attr-defined]
            position_mode = _normalize_position_mode(position_mode_payload.get("mode"))
            position_mode_source = "exchange"
        except Exception as exc:
            position_mode = POSITION_MODE_UNKNOWN
            position_mode_source = "exchange_error"
            position_mode_lookup_error = str(exc)
    mode_guard_reason_code = _position_mode_guard_reason_code(position_mode)
    stream_fallback_active = str(user_stream_summary.get("status") or "") != "connected"
    reconcile_source = "rest_polling_fallback" if stream_fallback_active else "user_stream_primary"
    unresolved_submission_summary = _bounded_reconcile_submission_unknown_orders(
        session,
        settings_row,
        client=client,
        symbol=symbol,
        now=reconcile_started_at,
    )
    synced_orders = 0
    synced_positions = 0
    symbol_protection_state: dict[str, dict[str, object]] = {}
    symbol_states: dict[str, dict[str, object]] = {}
    unprotected_positions: list[str] = []
    emergency_actions_taken: list[dict[str, object]] = []
    reconciled_closed_position_protective_orders: list[dict[str, object]] = []
    guarded_symbols = list(symbols) if mode_guard_reason_code is not None else []
    bulk_open_orders: list[dict[str, object]] | None = None
    bulk_remote_positions: list[dict[str, object]] | None = None
    if len(symbols) > 1:
        try:
            bulk_open_orders = client.get_open_orders()
        except Exception:
            bulk_open_orders = None
        try:
            bulk_remote_positions = client.get_position_information()
        except Exception:
            bulk_remote_positions = None
    for item_symbol in symbols:
        live_orders = [
            order
            for order in session.scalars(
                select(Order)
                .where(Order.mode == "live", Order.symbol == item_symbol, Order.status.notin_(FINAL_ORDER_STATUSES))
            )
            if str(
                _as_object_dict(
                    _as_object_dict(order.metadata_json).get("submission_tracking")
                ).get("submission_state")
                or ""
            ).lower()
            != "submit_unknown"
        ]
        use_rest_order_fallback, fallback_reason = should_use_rest_order_reconciliation(
            settings_row,
            active_order_count=len(live_orders),
            now=reconcile_started_at,
            user_stream_summary=user_stream_summary,
        )
        if use_rest_order_fallback and live_orders:
            stream_fallback_active = True
            reconcile_source = "rest_polling_fallback"
            user_stream_summary = _record_user_stream_order_sync_fallback(
                session,
                settings_row,
                symbol=item_symbol,
                reason_code=fallback_reason,
                user_stream_summary=user_stream_summary,
                flush_state=False,
            )
            for order in live_orders:
                if not order.external_order_id and not order.client_order_id:
                    continue
                try:
                    exchange_order = _fetch_exchange_order(
                        symbol=order.symbol,
                        client=client,
                        order_type=order.order_type,
                        order_id=order.external_order_id,
                        client_order_id=order.client_order_id,
                    )
                except Exception as exc:
                    reason_code = _classify_exchange_state_error(exc, "TEMPORARY_SYNC_FAILURE")
                    _record_sync_issue(
                        session,
                        settings_row,
                        scope="open_orders",
                        status="failed",
                        reason_code=reason_code,
                        detail={"symbol": order.symbol, "stage": "trade_lookup"},
                    )
                    _pause_for_system_issue(
                        session,
                        settings_row,
                        reason_code=reason_code,
                        symbol=order.symbol,
                        error=str(exc),
                        event_type="live_order_sync_failed",
                        component="live_sync",
                        alert_title="Live order sync failed",
                    alert_message="거래소 주문 상태를 동기화하지 못해 거래를 일시 중지했습니다.",
                )
                    raise RuntimeError(f"{reason_code}: {exc}") from exc
                _apply_exchange_order_state(
                    order,
                    exchange_order,
                    requested_quantity_fallback=order.requested_quantity,
                    requested_price_fallback=order.requested_price,
                    reduce_only_fallback=order.reduce_only,
                    close_only_fallback=order.close_only,
                )
                session.add(order)
                if _is_local_protective_order_row(order):
                    _backfill_finished_protective_order_trades(
                        session,
                        settings_row,
                        client,
                        order,
                        exchange_order,
                        source=reconcile_source,
                    )
                    synced_orders += 1
                    continue
                try:
                    trades = client.get_account_trades(symbol=order.symbol, order_id=order.external_order_id)
                except Exception as exc:
                    reason_code = _classify_exchange_state_error(exc, "TEMPORARY_SYNC_FAILURE")
                    _record_sync_issue(
                        session,
                        settings_row,
                        scope="open_orders",
                        status="failed",
                        reason_code=reason_code,
                        detail={"symbol": order.symbol, "stage": "order_lookup"},
                    )
                    _pause_for_system_issue(
                        session,
                        settings_row,
                        reason_code=reason_code,
                        symbol=order.symbol,
                        error=str(exc),
                        event_type="live_trade_sync_failed",
                        component="live_sync",
                        alert_title="Live trade sync failed",
                    alert_message="거래소 체결 내역을 동기화하지 못해 거래를 일시 중지했습니다.",
                )
                    raise RuntimeError(f"{reason_code}: {exc}") from exc
                _record_live_trades(session, order, trades)
                create_exchange_pnl_snapshot(session, settings_row)
                synced_orders += 1
        else:
            synced_orders += _count_symbol_order_stream_events(stream_events, symbol=item_symbol)
        synced_orders += _backfill_missing_final_order_trades(
            session,
            settings_row,
            client,
            symbol=item_symbol,
        )
        try:
            open_orders = (
                _symbol_payloads(bulk_open_orders, item_symbol)
                if bulk_open_orders is not None
                else client.get_open_orders(item_symbol)
            )
            _record_sync_success(
                session,
                settings_row,
                scope="open_orders",
                detail={
                    "symbol": item_symbol,
                    "open_order_count": len(open_orders),
                    "position_mode": position_mode,
                },
                flush_state=False,
            )
        except Exception as exc:
            reason_code = _classify_exchange_state_error(exc, "EXCHANGE_OPEN_ORDERS_SYNC_FAILED")
            _record_sync_issue(
                session,
                settings_row,
                scope="open_orders",
                status="failed",
                reason_code=reason_code,
                detail={"symbol": item_symbol},
            )
            _pause_for_system_issue(
                session,
                settings_row,
                reason_code=reason_code,
                symbol=item_symbol,
                error=str(exc),
                event_type="live_open_orders_sync_failed",
                component="live_sync",
                alert_title="Open orders sync failed",
                alert_message="거래소 미체결 주문을 동기화하지 못해 거래를 일시 중지했습니다.",
            )
            raise RuntimeError(f"{reason_code}: {exc}") from exc
        try:
            synced_position = sync_live_positions(
                session,
                settings_row,
                symbol=item_symbol,
                client=client,
                open_orders=open_orders,
                remote_positions=(
                    _symbol_payloads(bulk_remote_positions, item_symbol)
                    if bulk_remote_positions is not None
                    else None
                ),
                position_mode=position_mode,
                flush_state=False,
            )
        except Exception as exc:
            reason_code = _classify_exchange_state_error(exc, "EXCHANGE_POSITION_SYNC_FAILED")
            _pause_for_system_issue(
                session,
                settings_row,
                reason_code=reason_code,
                symbol=item_symbol,
                error=str(exc),
                event_type="live_position_sync_failed",
                component="live_sync",
                alert_title="Position sync failed",
                alert_message="거래소 포지션 상태를 동기화하지 못해 거래를 일시 중지했습니다.",
            )
            raise RuntimeError(f"{reason_code}: {exc}") from exc
        synced_orders += _backfill_missing_finished_protective_order_trades(
            session,
            settings_row,
            client,
            symbol=item_symbol,
            observed_at=reconcile_started_at,
            source="sync_live_state",
        )
        stale_protective_orders = reconcile_closed_position_protective_orders(
            session,
            symbol=item_symbol,
            open_orders=open_orders,
            observed_at=reconcile_started_at,
            source="sync_live_state",
        )
        if stale_protective_orders:
            synced_orders += len(stale_protective_orders)
            reconciled_closed_position_protective_orders.extend(
                {
                    "symbol": order.symbol,
                    "order_id": order.id,
                    "position_id": order.position_id,
                    "reason_code": CLOSED_POSITION_PROTECTIVE_ORDER_RECONCILED_REASON_CODE,
                }
                for order in stale_protective_orders
            )
        with session.no_autoflush:
            position = get_open_position(session, item_symbol)
        symbol_guard_reason_code = str(synced_position.get("guard_reason_code") or "") or None
        symbol_guard_active = bool(mode_guard_reason_code or symbol_guard_reason_code)
        if symbol_guard_reason_code and item_symbol not in guarded_symbols:
            guarded_symbols.append(item_symbol)
        if symbol_guard_active and position is not None and position.status == "open":
            protection_state = {
                "status": "unverified",
                "protected": False,
                "has_stop_loss": False,
                "has_take_profit": False,
                "protective_order_count": 0,
                "protective_order_ids": [],
                "missing_components": ["stop_loss", "take_profit"],
                "position_mode": position_mode,
                "exchange_position_side": _position_metadata_side(position),
                "reason_code": mode_guard_reason_code or symbol_guard_reason_code,
            }
            _record_sync_issue(
                session,
                settings_row,
                scope="protective_orders",
                status="incomplete",
                reason_code="PROTECTION_STATE_UNVERIFIED",
                detail={
                    "symbol": item_symbol,
                    "position_mode": position_mode,
                    "guard_reason_code": mode_guard_reason_code or symbol_guard_reason_code,
                    "remote_position_sides": synced_position.get("remote_position_sides", []),
                    "open_order_position_sides": synced_position.get("open_order_position_sides", []),
                },
            )
        else:
            protection_state = _build_protection_state(
                position,
                open_orders,
                position_mode=position_mode,
            )
        symbol_protection_state[item_symbol] = protection_state
        if not symbol_guard_active and _protection_state_blocks_entry(protection_state):
            _record_protection_health_failure(
                session,
                settings_row,
                symbol=item_symbol,
                position=position,
                protection_state=protection_state,
                trigger_source="sync_live_state",
            )
            _record_sync_issue(
                session,
                settings_row,
                scope="protective_orders",
                status="incomplete",
                reason_code="PROTECTION_STATE_UNVERIFIED",
                detail={
                    "symbol": item_symbol,
                    "protection_status": protection_state.get("status"),
                    "missing_components": protection_state.get("missing_components", []),
                    "health_components": _protection_state_blocking_components(protection_state),
                    "reason_codes": _protection_state_reason_codes(protection_state),
                    "health_issues": protection_state.get("health_issues", []),
                },
            )
            unprotected_positions.append(item_symbol)
            if protection_state["status"] == "missing" and allow_protection_recovery:
                protection_result = _ensure_protected_position(
                    session,
                    settings_row,
                    client,
                    symbol=item_symbol,
                    position=position,
                    stop_loss=position.stop_loss if position is not None else None,
                    take_profit=position.take_profit if position is not None else None,
                    decision_run_id=None,
                    risk_row=None,
                    parent_order=None,
                    trigger_source="sync_live_state",
                    pause_reason_code="MISSING_PROTECTIVE_ORDERS",
                )
                symbol_protection_state[item_symbol] = protection_result["protection_state"]  # type: ignore[assignment]
                if protection_result.get("emergency_action") is not None:
                    emergency_actions_taken.append(
                        {
                            "symbol": item_symbol,
                            "action": protection_result.get("status"),
                            "result": protection_result["emergency_action"],
                        }
                    )
                else:
                    _record_sync_success(
                        session,
                        settings_row,
                        scope="protective_orders",
                        detail={
                            "symbol": item_symbol,
                            "status": protection_result["protection_state"].get("status", "protected"),
                        },
                        flush_state=False,
                    )
            elif protection_state["status"] != "missing":
                set_symbol_protection_state(
                    session,
                    settings_row,
                    symbol=item_symbol,
                    state=PROTECTION_REQUIRED_STATE,
                    trigger_source="sync_live_state:protection_health_failed",
                    missing_components=_protection_state_blocking_components(protection_state),
                    auto_recovery_active=False,
                    recovery_status="health_check_failed",
                    last_error="Protective order health check failed: "
                    + ", ".join(_protection_state_reason_codes(protection_state)),
                    flush_state=False,
                )
            else:
                record_audit_event(
                    session,
                    event_type="protection_recovery_deferred",
                    entity_type="position",
                    entity_id=str(position.id if position is not None else item_symbol),
                    severity="critical",
                    message="Protection recovery was deferred by verify-only live sync.",
                    payload={
                        "symbol": item_symbol,
                        "trigger_source": "sync_live_state",
                        "protective_state": protection_state,
                    },
                )
        elif not symbol_guard_active:
            _record_sync_success(
                session,
                settings_row,
                scope="protective_orders",
                detail={"symbol": item_symbol, "status": protection_state["status"]},
                flush_state=False,
            )
            if protection_state["status"] in {"flat", "protected"}:
                _clear_symbol_protection_verify_block(
                    session,
                    settings_row,
                    symbol=item_symbol,
                )
            clear_symbol_protection_state(
                session,
                settings_row,
                symbol=item_symbol,
                trigger_source="sync_live_state:protected_or_flat",
                flush_state=False,
            )
        symbol_states[item_symbol] = {
            "symbol": item_symbol,
            "position_mode": position_mode,
            "position_status": str(synced_position.get("status") or "unknown"),
            "exchange_position_side": synced_position.get("exchange_position_side"),
            "remote_position_sides": list(synced_position.get("remote_position_sides") or []),
            "open_order_position_sides": list(synced_position.get("open_order_position_sides") or []),
            "open_order_count": len(open_orders),
            "closed_position_protective_orders_reconciled": len(stale_protective_orders),
            "protection_status": str(symbol_protection_state[item_symbol].get("status") or "unknown"),
            "guard_active": symbol_guard_active,
            "guard_reason_code": mode_guard_reason_code or symbol_guard_reason_code,
            "position_side_conflict": bool(synced_position.get("position_side_conflict", False)),
        }
        synced_positions += 1
    with session.no_autoflush:
        latest_prices = {
            item_symbol: position.mark_price
            for item_symbol in symbols
            if (position := get_open_position(session, item_symbol)) is not None
        }
    if latest_prices:
        refresh_open_position_marks(session, latest_prices)
    account_symbol = symbols[0] if symbols else settings_row.default_symbol.upper()
    try:
        account_info = client.get_account_info()
    except Exception as exc:
        reason_code = _classify_exchange_state_error(exc, "EXCHANGE_ACCOUNT_STATE_UNAVAILABLE")
        _record_sync_issue(
            session,
            settings_row,
            scope="account",
            status="failed",
            reason_code=reason_code,
            detail={"symbol": account_symbol},
        )
        _pause_for_system_issue(
            session,
            settings_row,
            reason_code=reason_code,
            symbol=account_symbol,
            error=str(exc),
            event_type="live_account_sync_failed",
            component="live_sync",
            alert_title="Live account state unavailable",
            alert_message="거래소 계좌 상태를 읽지 못해 거래를 일시 중지했습니다.",
        )
        raise RuntimeError(f"{reason_code}: {exc}") from exc
    pnl_snapshot, funding_sync = _create_live_account_snapshot(
        session,
        settings_row,
        client=client,
        account_info=account_info,
        component="live_sync",
        event_type="live_account_funding_sync_failed",
        symbol=account_symbol,
    )
    _record_sync_success(
        session,
        settings_row,
        scope="account",
        detail={
            "symbol": account_symbol,
            "equity": pnl_snapshot.equity,
            "wallet_balance": pnl_snapshot.wallet_balance,
            "available_balance": pnl_snapshot.available_balance,
            "funding_sync": funding_sync,
            **_exchange_permission_sync_detail(account_info),
        },
        flush_state=False,
    )
    reconciled_at = utcnow_naive()
    if position_mode_lookup_error and mode_guard_reason_code is None:
        mode_guard_reason_code = POSITION_MODE_UNCLEAR_REASON_CODE
        guarded_symbols = list(dict.fromkeys(symbols))
    resolved_mode_guard_reason_code = (
        mode_guard_reason_code
        if mode_guard_reason_code is not None
        else POSITION_MODE_MISMATCH_REASON_CODE
        if guarded_symbols
        else None
    )
    _record_position_mode_guard_transition(
        session,
        settings_row,
        reason_code=resolved_mode_guard_reason_code,
        guarded_symbols=list(dict.fromkeys(guarded_symbols)),
        position_mode=position_mode,
        position_mode_source=position_mode_source,
        detail={
            "enabled_symbols": symbols,
            "position_mode_lookup_error": position_mode_lookup_error,
            "symbol_states": symbol_states,
        },
    )
    replace_user_stream_detail(settings_row, build_user_stream_state(user_stream_summary))
    set_reconciliation_detail(
        settings_row,
        status="synced",
        source="rest_polling_reconciliation",
        last_reconciled_at=reconciled_at,
        last_success_at=reconciled_at,
        last_error=position_mode_lookup_error or "",
        last_symbol=symbols[0] if len(symbols) == 1 else None,
        stream_fallback_active=stream_fallback_active,
        reconcile_source=reconcile_source,
        position_mode=position_mode,
        position_mode_source=position_mode_source,
        position_mode_checked_at=reconciled_at,
        mode_guard_active=bool(resolved_mode_guard_reason_code),
        mode_guard_reason_code=resolved_mode_guard_reason_code,
        mode_guard_message=_position_mode_guard_message(resolved_mode_guard_reason_code),
        enabled_symbols=symbols,
        guarded_symbols=list(dict.fromkeys(guarded_symbols)),
        symbol_states=symbol_states,
        unresolved_submission_badge=bool(unresolved_submission_summary.get("unresolved_submission_badge", False)),
        unresolved_submission_count=int(unresolved_submission_summary.get("unresolved_submission_count") or 0),
        unresolved_submission_symbols=[
            str(item)
            for item in unresolved_submission_summary.get("unresolved_submission_symbols", [])
            if item
        ],
        unresolved_submissions=[
            dict(item)
            for item in unresolved_submission_summary.get("unresolved_submissions", [])
            if isinstance(item, dict)
        ],
    )
    session.add(settings_row)
    session.flush()
    user_stream_summary = get_user_stream_detail(settings_row)
    reconciliation_summary = get_reconciliation_detail(settings_row)
    runtime_state = get_protection_recovery_detail(settings_row)
    return {
        "symbols": symbols,
        "synced_orders": synced_orders,
        "synced_positions": synced_positions,
        "equity": pnl_snapshot.equity,
        "sync_freshness_summary": build_sync_freshness_summary(settings_row),
        "symbol_protection_state": symbol_protection_state,
        "unprotected_positions": unprotected_positions,
        "emergency_actions_taken": emergency_actions_taken,
        "operating_state": get_operating_state(settings_row),
        "protection_recovery_status": str(runtime_state.get("status", "idle")),
        "protection_recovery_active": bool(runtime_state.get("auto_recovery_active", False)),
        "missing_protection_symbols": [str(item) for item in runtime_state.get("missing_symbols", []) if item],
        "missing_protection_items": {
            str(key): [str(item) for item in value]
            for key, value in runtime_state.get("missing_items", {}).items()
            if isinstance(value, list)
        },
        "user_stream_summary": user_stream_summary,
        "reconciliation_summary": reconciliation_summary,
        "stream_health": str(stream_poll.get("stream_health") or user_stream_summary.get("status") or "idle"),
        "last_stream_event_time": stream_poll.get("last_stream_event_time") or user_stream_summary.get("last_event_at"),
        "stream_source": str(stream_poll.get("stream_source") or user_stream_summary.get("stream_source") or "rest_polling_fallback"),
        "reconcile_source": str(reconciliation_summary.get("reconcile_source") or "rest_polling_fallback"),
        "stream_event_count": int(stream_poll.get("stream_event_count") or len(stream_events)),
        "stream_events": stream_events,
        "stream_issues": [dict(item) for item in stream_poll.get("stream_issues", []) if isinstance(item, dict)],
        "symbol_reconciliation": symbol_states,
        "reconciled_closed_position_protective_orders": reconciled_closed_position_protective_orders,
    }


def run_live_test_order(session: Session, settings_row: Setting, *, symbol: str, side: str, quantity: float | None = None) -> dict[str, object]:
    client = _build_client(settings_row)
    filters = client.get_symbol_filters(symbol)
    reference_price = client.get_symbol_price(symbol)
    requested_quantity = quantity or filters["min_qty"] or 0.001
    normalized_quantity = client.normalize_order_quantity(
        symbol,
        requested_quantity,
        reference_price=reference_price,
        enforce_min_notional=True,
    )
    client.test_new_order(symbol=symbol, side=side, quantity=normalized_quantity)
    record_audit_event(
        session,
        event_type="live_test_order",
        entity_type="binance",
        entity_id=symbol,
        severity="info",
        message="Binance live test order preflight succeeded.",
        payload={
            "symbol": symbol,
            "side": side,
            "requested_quantity": requested_quantity,
            "quantity": normalized_quantity,
            "reference_price": reference_price,
            "min_notional": filters["min_notional"],
        },
    )
    return {
        "ok": True,
        "symbol": symbol,
        "side": side,
        "requested_quantity": requested_quantity,
        "quantity": normalized_quantity,
        "reference_price": reference_price,
        "min_notional": filters["min_notional"],
    }


def _resync_exchange_state(
    session: Session,
    settings_row: Setting,
    *,
    client: BinanceClient,
    symbol: str,
    event_prefix: str,
    component: str,
    verify_protection: bool,
    correlation_ids: dict[str, Any] | None = None,
) -> dict[str, object]:
    try:
        open_orders = client.get_open_orders(symbol)
        _record_sync_success(
            session,
            settings_row,
            scope="open_orders",
            detail={"symbol": symbol, "open_order_count": len(open_orders)},
        )
    except Exception as exc:
        reason_code = _classify_exchange_state_error(exc, "EXCHANGE_OPEN_ORDERS_SYNC_FAILED")
        _record_sync_issue(
            session,
            settings_row,
            scope="open_orders",
            status="failed",
            reason_code=reason_code,
            detail={"symbol": symbol},
        )
        _pause_for_system_issue(
            session,
            settings_row,
            reason_code=reason_code,
            symbol=symbol,
            error=str(exc),
            event_type=f"{event_prefix}_open_orders_failed",
            component=component,
            alert_title="Open orders resync failed",
            alert_message="Exchange open orders resync failed after order processing.",
            correlation_ids=correlation_ids,
        )
        raise RuntimeError(f"{reason_code}: {exc}") from exc

    try:
        synced_position = sync_live_positions(
            session,
            settings_row,
            symbol=symbol,
            client=client,
            open_orders=open_orders,
        )
    except Exception as exc:
        reason_code = _classify_exchange_state_error(exc, "EXCHANGE_POSITION_SYNC_FAILED")
        _record_sync_issue(
            session,
            settings_row,
            scope="positions",
            status="failed",
            reason_code=reason_code,
            detail={"symbol": symbol},
        )
        _pause_for_system_issue(
            session,
            settings_row,
            reason_code=reason_code,
            symbol=symbol,
            error=str(exc),
            event_type=f"{event_prefix}_position_sync_failed",
            component=component,
            alert_title="Position resync failed",
            alert_message="Exchange position resync failed after order processing.",
            correlation_ids=correlation_ids,
        )
        raise RuntimeError(f"{reason_code}: {exc}") from exc

    position = get_open_position(session, symbol)
    protection_state = _build_protection_state(position, open_orders)
    if verify_protection:
        if position is not None and position.quantity > 0 and protection_state["status"] != "protected":
            _record_protection_health_failure(
                session,
                settings_row,
                symbol=symbol,
                position=position,
                protection_state=protection_state,
                trigger_source=f"{event_prefix}:resync",
                correlation_ids=correlation_ids,
            )
            _record_sync_issue(
                session,
                settings_row,
                scope="protective_orders",
                status="incomplete",
                reason_code="PROTECTION_STATE_UNVERIFIED",
                detail={
                    "symbol": symbol,
                    "protection_status": protection_state.get("status"),
                    "missing_components": protection_state.get("missing_components", []),
                    "health_components": _protection_state_blocking_components(protection_state),
                    "reason_codes": _protection_state_reason_codes(protection_state),
                    "health_issues": protection_state.get("health_issues", []),
                    "protective_order_count": protection_state.get("protective_order_count", 0),
                },
            )
            if protection_state["status"] != "missing":
                set_symbol_protection_state(
                    session,
                    settings_row,
                    symbol=symbol,
                    state=PROTECTION_REQUIRED_STATE,
                    trigger_source=f"{event_prefix}:protection_health_failed",
                    missing_components=_protection_state_blocking_components(protection_state),
                    auto_recovery_active=False,
                    recovery_status="health_check_failed",
                    last_error="Protective order health check failed: "
                    + ", ".join(_protection_state_reason_codes(protection_state)),
                )
        else:
            _record_sync_success(
                session,
                settings_row,
                scope="protective_orders",
                detail={
                    "symbol": symbol,
                    "status": protection_state["status"],
                    "protective_order_count": protection_state.get("protective_order_count", 0),
                },
            )

    try:
        account_info = client.get_account_info()
        pnl_snapshot, funding_sync = _create_live_account_snapshot(
            session,
            settings_row,
            client=client,
            account_info=account_info,
            component="live_sync",
            event_type=f"{event_prefix}_funding_sync_failed",
            symbol=symbol,
        )
        _record_sync_success(
            session,
            settings_row,
            scope="account",
            detail={
                "symbol": symbol,
                "equity": pnl_snapshot.equity,
                "wallet_balance": pnl_snapshot.wallet_balance,
                "available_balance": pnl_snapshot.available_balance,
                "funding_sync": funding_sync,
                **_exchange_permission_sync_detail(account_info),
            },
        )
    except Exception as exc:
        reason_code = _classify_exchange_state_error(exc, "EXCHANGE_ACCOUNT_STATE_UNAVAILABLE")
        _record_sync_issue(
            session,
            settings_row,
            scope="account",
            status="failed",
            reason_code=reason_code,
            detail={"symbol": symbol},
        )
        _pause_for_system_issue(
            session,
            settings_row,
            reason_code=reason_code,
            symbol=symbol,
            error=str(exc),
            event_type=f"{event_prefix}_account_sync_failed",
            component=component,
            alert_title="Account resync failed",
            alert_message="Exchange account resync failed after order processing.",
            correlation_ids=correlation_ids,
        )
        raise RuntimeError(f"{reason_code}: {exc}") from exc

    return {
        "open_orders": open_orders,
        "position": synced_position,
        "protection_state": protection_state,
        "account_info": account_info,
        "pnl_snapshot": pnl_snapshot,
    }


def execute_live_trade(
    session: Session,
    settings_row: Setting,
    decision_run_id: int | None,
    decision: TradeDecision,
    market_snapshot: MarketSnapshotPayload,
    risk_result: RiskCheckResult,
    risk_row: RiskCheck | None = None,
    cycle_id: str | None = None,
    snapshot_id: int | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    symbol = decision.symbol.upper()
    effective_cycle_id = cycle_id or risk_result.cycle_id or f"adhoc-{decision_run_id or 'manual'}-{uuid4().hex[:12]}"
    effective_snapshot_id = snapshot_id if snapshot_id is not None else risk_result.snapshot_id
    execution_correlation_ids = normalize_correlation_ids(
        cycle_id=effective_cycle_id,
        snapshot_id=effective_snapshot_id,
        decision_id=decision_run_id,
        risk_id=risk_row.id if risk_row is not None else None,
    )
    dedupe_key = idempotency_key or build_execution_dedupe_key(
        cycle_id=effective_cycle_id,
        symbol=symbol,
        action=decision.decision,
    )
    cached_record = get_execution_dedupe_record(settings_row, dedupe_key=dedupe_key)
    if cached_record is not None:
        cached_result = deepcopy(cached_record.get("result")) if isinstance(cached_record.get("result"), dict) else {}
        cached_response: dict[str, Any] = dict(cached_result)
        if "status" not in cached_response:
            cached_response["status"] = str(cached_record.get("status") or "deduplicated")
        cached_response.update(
            {
                "dedupe_suppressed": True,
                "dedupe_reason": "cycle_action_already_completed",
                "dedupe_key": dedupe_key,
                "cycle_id": effective_cycle_id,
                "snapshot_id": effective_snapshot_id,
            }
        )
        record_audit_event(
            session,
            event_type="live_execution_deduplicated",
            entity_type="decision_run",
            entity_id=str(decision_run_id or symbol),
            severity="info",
            message="Live execution duplicate was suppressed because the same cycle action already completed.",
            payload={
                "symbol": symbol,
                "action": decision.decision,
                "cycle_id": effective_cycle_id,
                "snapshot_id": effective_snapshot_id,
                "dedupe_key": dedupe_key,
                "duplicate_reason": "cycle_action_already_completed",
                "cached_status": cached_record.get("status"),
                "risk_check_id": risk_row.id if risk_row is not None else None,
            },
            correlation_ids=execution_correlation_ids,
        )
        session.flush()
        return cached_response

    active_lock: dict[str, object] | None = None
    lock_token: str | None = None
    with _ACTIVE_SYMBOL_EXECUTION_LOCKS_GUARD:
        active_lock = dict(_ACTIVE_SYMBOL_EXECUTION_LOCKS.get(symbol) or {})
        if not active_lock:
            lock_token = uuid4().hex
            active_lock = {
                "token": lock_token,
                "symbol": symbol,
                "dedupe_key": dedupe_key,
                "cycle_id": effective_cycle_id,
                "snapshot_id": effective_snapshot_id,
                "action": decision.decision,
                "locked_at": utcnow_naive().isoformat(),
            }
            _ACTIVE_SYMBOL_EXECUTION_LOCKS[symbol] = active_lock

    if lock_token is None:
        duplicate_reason = (
            "cycle_action_in_progress"
            if str(active_lock.get("dedupe_key") or "") == dedupe_key
            else "symbol_execution_in_progress"
        )
        record_audit_event(
            session,
            event_type="live_execution_deduplicated",
            entity_type="decision_run",
            entity_id=str(decision_run_id or symbol),
            severity="info",
            message="Live execution duplicate was suppressed because a symbol execution lock is already active.",
            payload={
                "symbol": symbol,
                "action": decision.decision,
                "cycle_id": effective_cycle_id,
                "snapshot_id": effective_snapshot_id,
                "dedupe_key": dedupe_key,
                "duplicate_reason": duplicate_reason,
                "active_lock": active_lock,
                "risk_check_id": risk_row.id if risk_row is not None else None,
            },
            correlation_ids=execution_correlation_ids,
        )
        session.flush()
        return {
            "status": "deduplicated",
            "reason_codes": [DUPLICATE_EXECUTION_SUPPRESSED_REASON_CODE],
            "dedupe_suppressed": True,
            "dedupe_reason": duplicate_reason,
            "dedupe_key": dedupe_key,
            "cycle_id": effective_cycle_id,
            "snapshot_id": effective_snapshot_id,
            "active_lock": active_lock,
        }

    mark_execution_lock(
        settings_row,
        symbol=symbol,
        lock_token=lock_token,
        dedupe_key=dedupe_key,
        cycle_id=effective_cycle_id,
        snapshot_id=effective_snapshot_id,
        action=decision.decision,
    )
    session.add(settings_row)
    session.flush()

    try:
        raw_result = _execute_live_trade_body(
            session,
            settings_row,
            decision_run_id=decision_run_id,
            decision=decision,
            market_snapshot=market_snapshot,
            risk_result=risk_result,
            risk_row=risk_row,
            client_order_id_seed=dedupe_key,
            correlation_ids=execution_correlation_ids,
        )
        cacheable_result = bool(raw_result.pop("_cache_dedupe", True))
        if cacheable_result:
            store_execution_dedupe_record(
                settings_row,
                dedupe_key=dedupe_key,
                symbol=symbol,
                cycle_id=effective_cycle_id,
                snapshot_id=effective_snapshot_id,
                action=decision.decision,
                status=str(raw_result.get("status") or "unknown"),
                result=deepcopy(raw_result),
            )
            session.add(settings_row)
            session.flush()
        response = dict(raw_result)
        response.update(
            {
                "dedupe_key": dedupe_key,
                "cycle_id": effective_cycle_id,
                "snapshot_id": effective_snapshot_id,
            }
        )
        return response
    finally:
        with _ACTIVE_SYMBOL_EXECUTION_LOCKS_GUARD:
            current_lock = _ACTIVE_SYMBOL_EXECUTION_LOCKS.get(symbol)
            if current_lock is not None and str(current_lock.get("token") or "") == lock_token:
                _ACTIVE_SYMBOL_EXECUTION_LOCKS.pop(symbol, None)
        clear_execution_lock(settings_row, symbol=symbol, lock_token=lock_token)
        session.add(settings_row)
        session.flush()


def _execute_live_trade_body(
    session: Session,
    settings_row: Setting,
    decision_run_id: int | None,
    decision: TradeDecision,
    market_snapshot: MarketSnapshotPayload,
    risk_result: RiskCheckResult,
    risk_row: RiskCheck | None = None,
    client_order_id_seed: str | None = None,
    correlation_ids: dict[str, Any] | None = None,
) -> dict[str, Any]:
    execution_correlation_ids = normalize_correlation_ids(
        correlation_ids,
        decision_id=decision_run_id,
        risk_id=risk_row.id if risk_row is not None else None,
    )
    meta_gate_payload = (
        dict(risk_result.debug_payload.get("meta_gate"))
        if isinstance(risk_result.debug_payload, dict) and isinstance(risk_result.debug_payload.get("meta_gate"), dict)
        else {}
    )
    holding_profile_payload = _holding_profile_execution_payload(decision)
    if not risk_result.allowed:
        record_audit_event(
            session,
            event_type="live_execution_blocked",
            entity_type="decision_run",
            entity_id=str(decision_run_id),
            severity="warning",
            message="Live execution skipped because risk_guard blocked the intent.",
            payload={
                "symbol": decision.symbol,
                "decision": decision.model_dump(mode="json"),
                "reason_codes": list(risk_result.reason_codes),
                "risk_check_id": risk_row.id if risk_row is not None else None,
                "risk_debug_payload": dict(risk_result.debug_payload),
                "meta_gate": meta_gate_payload,
                **holding_profile_payload,
            },
            correlation_ids=execution_correlation_ids,
        )
        session.flush()
        return {
            "status": "blocked",
            "reason_codes": list(risk_result.reason_codes),
            "decision": decision.decision,
            "meta_gate": meta_gate_payload,
            **holding_profile_payload,
        }

    rollout_mode = get_rollout_mode(settings_row)
    exchange_submit_allowed = rollout_mode_allows_exchange_submit(settings_row)
    limited_live_max_notional = (
        get_limited_live_max_notional(settings_row) if rollout_mode == "limited_live" else None
    )

    if decision.decision == "hold":
        return {
            "status": "skipped",
            "reason_codes": ["HOLD_DECISION"],
            "rollout_mode": rollout_mode,
            **holding_profile_payload,
        }

    entry_action = _entry_action_for_decision(decision)
    if entry_action is not None:
        unresolved_guard = get_unresolved_submission_guard(
            settings_row,
            symbol=decision.symbol,
            action=entry_action,
        )
        if unresolved_guard is not None and bool(unresolved_guard.get("guard_active", True)):
            reason_code = str(unresolved_guard.get("guard_reason_code") or UNRESOLVED_SUBMISSION_GUARD_REASON_CODE)
            record_audit_event(
                session,
                event_type="live_execution_blocked",
                entity_type="decision_run",
                entity_id=str(decision_run_id or decision.symbol),
                severity="warning",
                message="Live execution skipped because unresolved submission guard is active for this symbol/action.",
                payload={
                    "symbol": decision.symbol,
                    "decision": decision.decision,
                    "reason_code": reason_code,
                    "unresolved_submission_guard": unresolved_guard,
                },
                correlation_ids=execution_correlation_ids,
            )
            session.flush()
            return {
                "status": "blocked",
                "reason_codes": [reason_code],
                "decision": decision.decision,
                "unresolved_submission_guard": unresolved_guard,
            }

    if rollout_mode == "shadow":
        latest_pnl = get_latest_pnl_snapshot(session, settings_row)
        existing_position = get_open_position(session, decision.symbol)
        operating_state = get_operating_state(settings_row)
        intent = build_execution_intent(
            decision,
            market_snapshot,
            risk_result,
            settings_row,
            latest_pnl.equity,
            existing_position=existing_position,
            operating_state=operating_state,
        )
        intent_type = intent.intent_type
        protection_verify_block = _get_symbol_protection_verify_block(settings_row, decision.symbol)
        if intent_type in PROTECTION_VERIFY_BLOCKING_INTENT_TYPES and protection_verify_block is not None:
            record_audit_event(
                session,
                event_type="live_execution_blocked",
                entity_type="decision_run",
                entity_id=str(decision_run_id),
                severity="warning",
                message="Live execution skipped because protective order verification previously failed for this symbol.",
                payload={
                    "symbol": decision.symbol,
                    "intent_type": intent_type,
                    "reason_code": PROTECTION_VERIFY_FAILED_REASON_CODE,
                    "protection_verify_block": protection_verify_block,
                    "rollout_mode": rollout_mode,
                    **holding_profile_payload,
                },
                correlation_ids=execution_correlation_ids,
            )
            session.flush()
            return {
                "status": "blocked",
                "reason_codes": [PROTECTION_VERIFY_FAILED_REASON_CODE],
                "intent_type": intent_type,
                "protection_verify_block": protection_verify_block,
                "rollout_mode": rollout_mode,
                **holding_profile_payload,
            }
        execution_plan = select_execution_plan(
            intent,
            market_snapshot,
            settings_row,
            pre_trade_protection=_build_protection_state(existing_position, []),
        )
        if execution_plan.order_type == "NONE":
            record_audit_event(
                session,
                event_type="live_execution_blocked",
                entity_type="decision_run",
                entity_id=str(decision_run_id or decision.symbol),
                severity="warning",
                message="Live execution skipped because execution policy requires block or pending.",
                payload={
                    "symbol": decision.symbol,
                    "decision": decision.decision,
                    "intent_type": intent_type,
                    "reason_code": execution_plan.reason,
                    "execution_policy": execution_plan.to_payload(),
                    "rollout_mode": rollout_mode,
                    "exchange_submit_allowed": exchange_submit_allowed,
                    **holding_profile_payload,
                },
                correlation_ids=execution_correlation_ids,
            )
            session.flush()
            return {
                "status": "blocked",
                "reason_codes": [execution_plan.reason],
                "decision": decision.decision,
                "intent_type": intent_type,
                "execution_policy": execution_plan.to_payload(),
                "rollout_mode": rollout_mode,
                **holding_profile_payload,
            }
        record_audit_event(
            session,
            event_type="live_execution_attempted",
            entity_type="decision_run",
            entity_id=str(decision_run_id or decision.symbol),
            severity="info",
            message="Live execution attempt started.",
            payload={
                "symbol": decision.symbol,
                "decision": decision.decision,
                "intent_type": intent_type,
                "requested_quantity": intent.quantity,
                "requested_price": intent.requested_price,
                "execution_policy": execution_plan.to_payload(),
                "rollout_mode": rollout_mode,
                "exchange_submit_allowed": exchange_submit_allowed,
                **holding_profile_payload,
            },
            correlation_ids=execution_correlation_ids,
        )
        record_audit_event(
            session,
            event_type="live_execution_submit_skipped",
            entity_type="decision_run",
            entity_id=str(decision_run_id or decision.symbol),
            severity="info",
            message="Shadow rollout mode recorded the execution intent without submitting to Binance.",
            payload={
                "symbol": decision.symbol,
                "decision": decision.decision,
                "intent_type": intent_type,
                "reason_code": ROLLOUT_MODE_SHADOW_REASON_CODE,
                "requested_quantity": intent.quantity,
                "requested_price": intent.requested_price,
                "execution_policy": execution_plan.to_payload(),
                "rollout_mode": rollout_mode,
                **holding_profile_payload,
            },
            correlation_ids=execution_correlation_ids,
        )
        session.flush()
        return {
            "status": "shadow",
            "reason_codes": [ROLLOUT_MODE_SHADOW_REASON_CODE],
            "decision": decision.decision,
            "intent_type": intent_type,
            "rollout_mode": rollout_mode,
            "requested_quantity": intent.quantity,
            "requested_price": intent.requested_price,
            "execution_policy": execution_plan.to_payload(),
            "submit_blocked": True,
            "_cache_dedupe": False,
            **holding_profile_payload,
        }

    client = _build_client(settings_row)
    try:
        account_info = client.get_account_info()
    except Exception as exc:
        reason_code = _classify_exchange_state_error(exc, "EXCHANGE_ACCOUNT_STATE_UNAVAILABLE")
        _pause_for_system_issue(
            session,
            settings_row,
            reason_code=reason_code,
            symbol=decision.symbol,
            error=str(exc),
            event_type="live_account_state_unavailable",
            component="live_execution",
            alert_title="Live account state unavailable",
            alert_message="?? ??? ??? ? ?? ??? ?? ??????.",
            correlation_ids=execution_correlation_ids,
        )
        record_audit_event(
            session,
            event_type="live_execution_skipped",
            entity_type="decision_run",
            entity_id=str(decision_run_id),
            severity="error",
            message="Live execution skipped because exchange account state was unavailable.",
            payload={"symbol": decision.symbol, "error": str(exc), "reason_code": reason_code},
            correlation_ids=execution_correlation_ids,
        )
        session.flush()
        return {"status": "error", "reason_codes": [reason_code], "error": str(exc)}

    latest_pnl, funding_sync = _create_live_account_snapshot(
        session,
        settings_row,
        client=client,
        account_info=account_info,
        component="live_execution",
        event_type="live_execution_funding_sync_failed",
        symbol=decision.symbol,
        correlation_ids=execution_correlation_ids,
    )
    session.refresh(latest_pnl)
    _record_sync_success(
        session,
        settings_row,
        scope="account",
        detail={
            "symbol": decision.symbol,
            "equity": latest_pnl.equity,
            "wallet_balance": latest_pnl.wallet_balance,
            "available_balance": latest_pnl.available_balance,
            "funding_sync": funding_sync,
            **_exchange_permission_sync_detail(account_info),
        },
    )
    live_balances = _live_account_balances(account_info)

    try:
        open_orders = client.get_open_orders(decision.symbol)
        _record_sync_success(
            session,
            settings_row,
            scope="open_orders",
            detail={"symbol": decision.symbol, "open_order_count": len(open_orders)},
        )
    except Exception as exc:
        reason_code = _classify_exchange_state_error(exc, "EXCHANGE_OPEN_ORDERS_SYNC_FAILED")
        _record_sync_issue(
            session,
            settings_row,
            scope="open_orders",
            status="failed",
            reason_code=reason_code,
            detail={"symbol": decision.symbol},
        )
        _pause_for_system_issue(
            session,
            settings_row,
            reason_code=reason_code,
            symbol=decision.symbol,
            error=str(exc),
            event_type="live_preflight_open_orders_failed",
            component="live_execution",
            alert_title="Pre-trade open orders sync failed",
            alert_message="?? ?? ??? ???? ?? ??? ?? ??????.",
            correlation_ids=execution_correlation_ids,
        )
        return {"status": "error", "reason_codes": [reason_code], "error": str(exc)}

    try:
        sync_live_positions(session, settings_row, symbol=decision.symbol, client=client, open_orders=open_orders)
    except Exception as exc:
        reason_code = _classify_exchange_state_error(exc, "EXCHANGE_POSITION_SYNC_FAILED")
        _record_sync_issue(
            session,
            settings_row,
            scope="positions",
            status="failed",
            reason_code=reason_code,
            detail={"symbol": decision.symbol},
        )
        _pause_for_system_issue(
            session,
            settings_row,
            reason_code=reason_code,
            symbol=decision.symbol,
            error=str(exc),
            event_type="live_preflight_position_sync_failed",
            component="live_execution",
            alert_title="Pre-trade position sync failed",
            alert_message="?? ??? ??? ???? ?? ??? ?? ??????.",
            correlation_ids=execution_correlation_ids,
        )
        return {"status": "error", "reason_codes": [reason_code], "error": str(exc)}

    existing_position = get_open_position(session, decision.symbol)
    operating_state = get_operating_state(settings_row)
    intent = build_execution_intent(
        decision,
        market_snapshot,
        risk_result,
        settings_row,
        live_balances["sizing_equity"] if live_balances["sizing_equity"] > 0 else latest_pnl.equity,
        existing_position=existing_position,
        operating_state=operating_state,
    )
    intent_type = intent.intent_type
    protection_verify_block = _get_symbol_protection_verify_block(settings_row, decision.symbol)
    if intent_type in PROTECTION_VERIFY_BLOCKING_INTENT_TYPES and protection_verify_block is not None:
        record_audit_event(
            session,
            event_type="live_execution_blocked",
            entity_type="decision_run",
            entity_id=str(decision_run_id),
            severity="warning",
            message="Live execution skipped because protective order verification previously failed for this symbol.",
            payload={
                "symbol": decision.symbol,
                "intent_type": intent_type,
                "reason_code": PROTECTION_VERIFY_FAILED_REASON_CODE,
                "protection_verify_block": protection_verify_block,
            },
            correlation_ids=execution_correlation_ids,
        )
        session.flush()
        return {
            "status": "blocked",
            "reason_codes": [PROTECTION_VERIFY_FAILED_REASON_CODE],
            "intent_type": intent_type,
            "protection_verify_block": protection_verify_block,
        }
    pre_trade_protection = _build_protection_state(existing_position, open_orders)
    if (
        intent_type in PROTECTION_VERIFY_BLOCKING_INTENT_TYPES
        and existing_position is not None
        and _protection_state_blocks_entry(pre_trade_protection)
    ):
        reason_codes = ["PROTECTION_STATE_UNVERIFIED", *_protection_state_reason_codes(pre_trade_protection)]
        _record_protection_health_failure(
            session,
            settings_row,
            symbol=decision.symbol,
            position=existing_position,
            protection_state=pre_trade_protection,
            trigger_source="execute_live_trade:preflight",
            correlation_ids=execution_correlation_ids,
        )
        _record_sync_issue(
            session,
            settings_row,
            scope="protective_orders",
            status="incomplete",
            reason_code="PROTECTION_STATE_UNVERIFIED",
            detail={
                "symbol": decision.symbol,
                "protection_status": pre_trade_protection.get("status"),
                "health_components": _protection_state_blocking_components(pre_trade_protection),
                "reason_codes": _protection_state_reason_codes(pre_trade_protection),
                "health_issues": pre_trade_protection.get("health_issues", []),
            },
        )
        set_symbol_protection_state(
            session,
            settings_row,
            symbol=decision.symbol,
            state=PROTECTION_REQUIRED_STATE,
            trigger_source="execute_live_trade:preflight_protection_health_failed",
            missing_components=_protection_state_blocking_components(pre_trade_protection),
            auto_recovery_active=False,
            recovery_status="health_check_failed",
            last_error="Protective order health check failed: "
            + ", ".join(_protection_state_reason_codes(pre_trade_protection)),
        )
        record_audit_event(
            session,
            event_type="live_execution_blocked",
            entity_type="decision_run",
            entity_id=str(decision_run_id),
            severity="critical",
            message="Live execution skipped because protective order health check failed for the open position.",
            payload={
                "symbol": decision.symbol,
                "intent_type": intent_type,
                "reason_codes": reason_codes,
                "protection_state": pre_trade_protection,
            },
            correlation_ids=execution_correlation_ids,
        )
        session.flush()
        return {
            "status": "blocked",
            "reason_codes": reason_codes,
            "intent_type": intent_type,
            "protective_state": pre_trade_protection,
        }

    if decision.decision in {"long", "short"}:
        target_side = "long" if decision.decision == "long" else "short"
        if existing_position is not None and existing_position.side != target_side:
            create_alert(
                session,
                category="execution",
                severity="warning",
                title="Opposite live position open",
                message="?? ?? ???? ?? ?? ?? ??? ??????.",
                payload={"symbol": decision.symbol, "existing_side": existing_position.side, "target_side": target_side},
            )
            return {"status": "rejected", "reason_codes": ["OPPOSITE_LIVE_POSITION_OPEN"], "intent_type": intent_type}

    if intent_type == "protection":
        if existing_position is None:
            return {"status": "rejected", "reason_codes": ["NO_OPEN_POSITION"], "intent_type": intent_type}
        protection_recovery_result = _ensure_protected_position(
            session,
            settings_row,
            client,
            symbol=decision.symbol,
            position=existing_position,
            stop_loss=intent.stop_loss or existing_position.stop_loss,
            take_profit=intent.take_profit or existing_position.take_profit,
            decision_run_id=decision_run_id,
            risk_row=risk_row,
            parent_order=None,
            trigger_source="execute_live_trade:protection",
            pause_reason_code="MISSING_PROTECTIVE_ORDERS",
            client_order_id_seed=client_order_id_seed,
            correlation_ids=execution_correlation_ids,
        )
        record_audit_event(
            session,
            event_type="protection_recovery_processed",
            entity_type="position",
            entity_id=str(existing_position.id),
            severity="info" if protection_recovery_result["status"] != "emergency_exit" else "critical",
            message="Protection recovery intent processed.",
            payload={
                "symbol": decision.symbol,
                "intent_type": intent_type,
                "decision": decision.model_dump(mode="json"),
                "protection_result": protection_recovery_result,
            },
            correlation_ids=execution_correlation_ids,
        )
        return {
            "status": protection_recovery_result["status"],
            "intent_type": intent_type,
            "protective_state": protection_recovery_result["protection_state"],
            "protective_order_ids": protection_recovery_result.get("created_order_ids", []),
            "emergency_action": protection_recovery_result.get("emergency_action"),
        }

    if intent_type == "entry" and existing_position is None:
        _cancel_exit_orders(session, client, decision.symbol)
    client.change_initial_leverage(decision.symbol, max(1, int(round(intent.leverage))))

    side = "BUY" if decision.decision == "long" else "SELL"
    requested_quantity = intent.quantity
    reduce_only = False
    reduce_fraction = 1.0

    if decision.decision in {"reduce", "exit"}:
        if existing_position is None:
            return {"status": "rejected", "reason_codes": ["NO_OPEN_POSITION"], "intent_type": intent_type}
        side = "SELL" if existing_position.side == "long" else "BUY"
        reduce_fraction = 1.0 if decision.decision == "exit" else _reduce_fraction_for_decision(decision, settings_row)
        requested_quantity = existing_position.quantity * reduce_fraction
        reduce_only = True

    approved_notional_cap = (
        risk_result.approved_projected_notional
        if intent_type in {"entry", "scale_in"} and risk_result.approved_projected_notional > 0
        else None
    )
    rollout_notional_cap_applied = False
    if intent_type in {"entry", "scale_in"} and limited_live_max_notional is not None:
        current_cap = approved_notional_cap if approved_notional_cap is not None and approved_notional_cap > 0 else (
            requested_quantity * max(intent.requested_price, 0.0)
        )
        if current_cap > 0:
            adjusted_cap = min(current_cap, limited_live_max_notional)
            rollout_notional_cap_applied = adjusted_cap < current_cap - 1e-9
            approved_notional_cap = adjusted_cap
    try:
        preflight_request = _normalize_submit_request(
            client,
            symbol=decision.symbol,
            quantity=requested_quantity,
            reference_price=intent.requested_price,
            approved_notional=approved_notional_cap,
            enforce_min_notional=decision.decision != "exit",
            close_position=False,
        )
    except PreTradeExchangeFilterError as exc:
        record_audit_event(
            session,
            event_type="live_execution_blocked",
            entity_type="decision_run",
            entity_id=str(decision_run_id),
            severity="warning",
            message="Live execution skipped because the order failed exchange filters before submission.",
            payload={
                "symbol": decision.symbol,
                "reason_code": exc.reason_code,
                "intent_type": intent_type,
                "approved_projected_notional": risk_result.approved_projected_notional,
                "approved_quantity": risk_result.approved_quantity,
                "risk_check_id": risk_row.id if risk_row is not None else None,
                "risk_debug_payload": dict(risk_result.debug_payload),
                "meta_gate": meta_gate_payload,
                "exchange_filter_detail": exc.detail,
            },
            correlation_ids=execution_correlation_ids,
        )
        session.flush()
        return {
            "status": "blocked",
            "reason_codes": [exc.reason_code],
            "decision": decision.decision,
            "intent_type": intent_type,
            "exchange_filter_detail": exc.detail,
            "meta_gate": meta_gate_payload,
        }
    normalized_quantity = _to_float(preflight_request.get("quantity"), requested_quantity)
    execution_plan = select_execution_plan(
        intent,
        market_snapshot,
        settings_row,
        pre_trade_protection=pre_trade_protection,
    )
    if execution_plan.order_type == "NONE":
        record_audit_event(
            session,
            event_type="live_execution_blocked",
            entity_type="decision_run",
            entity_id=str(decision_run_id or decision.symbol),
            severity="warning",
            message="Live execution skipped because execution policy requires block or pending.",
            payload={
                "symbol": decision.symbol,
                "decision": decision.decision,
                "intent_type": intent_type,
                "reason_code": execution_plan.reason,
                "execution_policy": execution_plan.to_payload(),
                "rollout_mode": rollout_mode,
                "exchange_submit_allowed": exchange_submit_allowed,
                "approved_notional_cap": approved_notional_cap,
                "meta_gate": meta_gate_payload,
                **holding_profile_payload,
            },
            correlation_ids=execution_correlation_ids,
        )
        session.flush()
        return {
            "status": "blocked",
            "reason_codes": [execution_plan.reason],
            "decision": decision.decision,
            "intent_type": intent_type,
            "execution_policy": execution_plan.to_payload(),
            "meta_gate": meta_gate_payload,
            **holding_profile_payload,
        }
    execution_price = intent.requested_price
    if execution_plan.price is not None:
        if hasattr(client, "normalize_price"):
            execution_price = client.normalize_price(decision.symbol, execution_plan.price)
        else:
            execution_price = execution_plan.price
    planned_entry_execution_type = _entry_execution_type_for_plan(execution_plan)
    trade_performance_tags = _build_trade_performance_tags(
        session,
        decision_run_id=decision_run_id,
        decision=decision,
        market_snapshot=market_snapshot,
        risk_result=risk_result,
        risk_row=risk_row,
        intent_type=intent_type,
        requested_price=execution_price,
        requested_quantity=normalized_quantity,
        execution_plan=execution_plan,
        existing_position=existing_position,
    )
    record_audit_event(
        session,
        event_type="live_execution_attempted",
        entity_type="decision_run",
        entity_id=str(decision_run_id or decision.symbol),
        severity="info",
        message="Live execution attempt started.",
            payload={
                "symbol": decision.symbol,
                "decision": decision.decision,
                "intent_type": intent_type,
                "requested_quantity": normalized_quantity,
                "requested_price": execution_price,
                "entry_execution_type": planned_entry_execution_type,
                "execution_policy": execution_plan.to_payload(),
                "rollout_mode": rollout_mode,
                "exchange_submit_allowed": exchange_submit_allowed,
                "limited_live_max_notional": limited_live_max_notional,
                "rollout_notional_cap_applied": rollout_notional_cap_applied,
                "approved_notional_cap": approved_notional_cap,
                "meta_gate": meta_gate_payload,
                **_trade_performance_payload(trade_performance_tags),
            },
            correlation_ids=execution_correlation_ids,
        )
    if rollout_mode == "live_dry_run":
        record_audit_event(
            session,
            event_type="live_execution_submit_skipped",
            entity_type="decision_run",
            entity_id=str(decision_run_id or decision.symbol),
            severity="info",
            message="Live dry-run rollout mode completed exchange preflight without submitting to Binance.",
            payload={
                "symbol": decision.symbol,
                "decision": decision.decision,
                "intent_type": intent_type,
                "reason_code": ROLLOUT_MODE_LIVE_DRY_RUN_REASON_CODE,
                "requested_quantity": normalized_quantity,
                "requested_price": execution_price,
                "approved_notional_cap": approved_notional_cap,
                "entry_execution_type": planned_entry_execution_type,
                "execution_policy": execution_plan.to_payload(),
                "preflight_request": dict(preflight_request),
                "rollout_mode": rollout_mode,
                **_trade_performance_payload(trade_performance_tags),
                **holding_profile_payload,
            },
            correlation_ids=execution_correlation_ids,
        )
        session.flush()
        return {
            "status": "dry_run",
            "reason_codes": [ROLLOUT_MODE_LIVE_DRY_RUN_REASON_CODE],
            "decision": decision.decision,
            "intent_type": intent_type,
            "rollout_mode": rollout_mode,
            "requested_quantity": normalized_quantity,
            "requested_price": execution_price,
            "approved_notional_cap": approved_notional_cap,
            "entry_execution_type": planned_entry_execution_type,
            "execution_policy": execution_plan.to_payload(),
            **_trade_performance_payload(trade_performance_tags),
            "preflight_request": dict(preflight_request),
            "submit_blocked": True,
            "_cache_dedupe": False,
            **holding_profile_payload,
        }
    try:
        client.change_initial_leverage(decision.symbol, max(1, int(round(intent.leverage))))
        execution_result = _execute_primary_order_with_policy(
            session,
            client=client,
            settings_row=settings_row,
            symbol=decision.symbol,
            side=side,
            execution_plan=execution_plan,
            requested_quantity=normalized_quantity,
            requested_price=execution_price,
            decision_run_id=decision_run_id,
            risk_row=risk_row,
            reduce_only=reduce_only,
            close_only=decision.decision == "exit",
            intent_type=intent_type,
            approved_notional_cap=approved_notional_cap,
            client_order_id_seed=client_order_id_seed,
            correlation_ids=execution_correlation_ids,
            trade_performance_tags=trade_performance_tags,
            position_id=existing_position.id if existing_position is not None else None,
        )
    except PreTradeExchangeFilterError as exc:
        record_audit_event(
            session,
            event_type="live_execution_blocked",
            entity_type="decision_run",
            entity_id=str(decision_run_id or decision.symbol),
            severity="warning",
            message="Live execution skipped because the final exchange-normalized order failed pre-trade filters.",
            payload={
                "symbol": decision.symbol,
                "reason_code": exc.reason_code,
                "intent_type": intent_type,
                "requested_quantity": normalized_quantity,
                "requested_price": execution_price,
                "execution_policy": execution_plan.to_payload(),
                "exchange_filter_detail": exc.detail,
            },
            correlation_ids=execution_correlation_ids,
        )
        session.flush()
        return {
            "status": "blocked",
            "reason_codes": [exc.reason_code],
            "intent_type": intent_type,
            "execution_policy": execution_plan.to_payload(),
            "exchange_filter_detail": exc.detail,
        }
    except OrderSubmissionUnknownError as exc:
        submission_tracking = dict(exc.submission_tracking)
        now = utcnow_naive()
        submission_tracking["reconcile_attempt_count"] = 0
        submission_tracking["next_reconcile_at"] = now.isoformat()
        submission_tracking["final_resolution_deadline"] = (
            now + timedelta(seconds=UNRESOLVED_SUBMISSION_RESOLUTION_DEADLINE_SECONDS)
        ).isoformat()
        submission_tracking["unresolved_guard_active"] = True
        submission_tracking["updated_at"] = now.isoformat()
        order = _create_submission_unknown_order_row(
            session,
            symbol=decision.symbol,
            side=side.lower(),
            order_type=execution_plan.order_type,
            requested_quantity=normalized_quantity,
            requested_price=execution_price,
            decision_run_id=decision_run_id,
            risk_row=risk_row,
            reduce_only=reduce_only,
            close_only=decision.decision == "exit",
            client_order_id=exc.client_order_id,
            submit_request=exc.submit_request,
            submission_tracking=submission_tracking,
            metadata_json={
                "error": str(exc),
                "intent_type": intent_type,
                "requested_quantity": normalized_quantity,
                "requested_price": execution_price,
                "entry_execution_type": planned_entry_execution_type,
                "execution_policy": execution_plan.to_payload(),
                **_trade_performance_payload(trade_performance_tags),
            },
        )
        payload = {
            "symbol": decision.symbol,
            "client_order_id": exc.client_order_id,
            "submission_tracking": submission_tracking,
            "intent_type": intent_type,
            "requested_quantity": normalized_quantity,
            "requested_price": execution_price,
            "entry_execution_type": planned_entry_execution_type,
            "execution_policy": execution_plan.to_payload(),
            **_trade_performance_payload(trade_performance_tags),
        }
        guard_action = _entry_action_for_decision(decision)
        if guard_action is not None:
            set_unresolved_submission_guard(
                settings_row,
                symbol=decision.symbol,
                action=guard_action,
                payload={
                    "guard_active": True,
                    "guard_reason_code": UNRESOLVED_SUBMISSION_GUARD_REASON_CODE,
                    "order_id": order.id,
                    "client_order_id": exc.client_order_id,
                    "reconcile_attempt_count": 0,
                    "next_reconcile_at": submission_tracking.get("next_reconcile_at"),
                    "final_resolution_deadline": submission_tracking.get("final_resolution_deadline"),
                    "updated_at": submission_tracking.get("updated_at"),
                },
            )
        create_alert(
            session,
            category="execution",
            severity="warning",
            title="Live submission unknown",
            message="Live order submission timed out and now requires exchange reconciliation.",
            payload=payload,
        )
        record_audit_event(
            session,
            event_type="live_order_submission_unknown",
            entity_type="order",
            entity_id=str(order.id),
            severity="warning",
            message="Live order submission timed out and could not be confirmed yet.",
            payload=payload,
            correlation_ids=normalize_correlation_ids(execution_correlation_ids, execution_id=order.id),
        )
        record_health_event(
            session,
            component="live_execution",
            status="warning",
            message="Live order submission is waiting for reconciliation after timeout/transport failure.",
            payload=payload,
            correlation_ids=normalize_correlation_ids(execution_correlation_ids, execution_id=order.id),
        )
        session.flush()
        return {
            "order_id": order.id,
            "status": "submission_unknown",
            "reason_codes": [UNKNOWN_SUBMISSION_REASON_CODE],
            "client_order_id": exc.client_order_id,
            "submission_state": submission_tracking.get("submission_state"),
            "submit_attempt_count": submission_tracking.get("submit_attempt_count"),
            "last_submit_error": submission_tracking.get("last_submit_error"),
            "intent_type": intent_type,
            "entry_execution_type": planned_entry_execution_type,
            "execution_policy": execution_plan.to_payload(),
        }
    except BinanceAPIError as exc:
        reason_codes = ["BINANCE_ORDER_REJECTED"]
        if exc.code == -2019:
            reason_codes.append("INSUFFICIENT_MARGIN")
        submission_tracking = _as_object_dict(getattr(exc, "submission_tracking", None))
        client_order_id = getattr(exc, "client_order_id", None)
        submit_request = getattr(exc, "submit_request", None)
        order = _create_rejected_order_row(
            session,
            symbol=decision.symbol,
            side=decision.decision,
            order_type=execution_plan.order_type,
            requested_quantity=normalized_quantity,
            requested_price=execution_price,
            decision_run_id=decision_run_id,
            risk_row=risk_row,
            reduce_only=reduce_only,
            close_only=decision.decision == "exit",
            reason_codes=reason_codes,
            metadata_json={
                "error": str(exc),
                "exchange_code": exc.code,
                "available_balance": live_balances["available_balance"],
                "equity": live_balances["equity"],
                "intent_type": intent_type,
                "requested_quantity": normalized_quantity,
                "requested_price": execution_price,
                "entry_execution_type": planned_entry_execution_type,
                "execution_policy": execution_plan.to_payload(),
                **_trade_performance_payload(trade_performance_tags),
                **({"submit_request": dict(submit_request)} if isinstance(submit_request, dict) else {}),
                **({"submission_tracking": submission_tracking} if submission_tracking else {}),
            },
            client_order_id=str(client_order_id) if client_order_id else None,
        )
        create_alert(
            session,
            category="execution",
            severity="warning",
            title="Live order rejected",
            message="???? ???? ??????.",
            payload={
                "symbol": decision.symbol,
                "error": str(exc),
                "exchange_code": exc.code,
                "requested_quantity": normalized_quantity,
                "available_balance": live_balances["available_balance"],
                "intent_type": intent_type,
                "requested_price": execution_price,
                "entry_execution_type": planned_entry_execution_type,
                "execution_policy": execution_plan.to_payload(),
                **_trade_performance_payload(trade_performance_tags),
            },
        )
        record_audit_event(
            session,
            event_type="live_execution_rejected",
            entity_type="order",
            entity_id=str(order.id),
            severity="warning",
            message="Live execution was rejected by Binance.",
            payload={
                "symbol": decision.symbol,
                "error": str(exc),
                "exchange_code": exc.code,
                "requested_quantity": normalized_quantity,
                "available_balance": live_balances["available_balance"],
                "equity": live_balances["equity"],
                "intent_type": intent_type,
                "requested_price": execution_price,
                "entry_execution_type": planned_entry_execution_type,
                "execution_policy": execution_plan.to_payload(),
                **_trade_performance_payload(trade_performance_tags),
            },
            correlation_ids=normalize_correlation_ids(execution_correlation_ids, execution_id=order.id),
        )
        session.flush()
        return {
            "order_id": order.id,
            "status": "rejected",
            "reason_codes": reason_codes,
            "error": str(exc),
            "exchange_code": exc.code,
            "intent_type": intent_type,
            "entry_execution_type": planned_entry_execution_type,
            "execution_policy": execution_plan.to_payload(),
        }
    except Exception as exc:
        order = _create_rejected_order_row(
            session,
            symbol=decision.symbol,
            side=decision.decision,
            order_type=execution_plan.order_type,
            requested_quantity=normalized_quantity,
            requested_price=execution_price,
            decision_run_id=decision_run_id,
            risk_row=risk_row,
            reduce_only=reduce_only,
            close_only=decision.decision == "exit",
            reason_codes=["LIVE_EXECUTION_ERROR"],
            metadata_json={
                "error": str(exc),
                "intent_type": intent_type,
                "requested_quantity": normalized_quantity,
                "requested_price": execution_price,
                "entry_execution_type": planned_entry_execution_type,
                "execution_policy": execution_plan.to_payload(),
                **_trade_performance_payload(trade_performance_tags),
            },
        )
        create_alert(
            session,
            category="execution",
            severity="error",
            title="Live execution failed",
            message="??? ?? ? ??? ??????.",
            payload={
                "symbol": decision.symbol,
                "error": str(exc),
                "intent_type": intent_type,
                "requested_quantity": normalized_quantity,
                "requested_price": execution_price,
                "entry_execution_type": planned_entry_execution_type,
                "execution_policy": execution_plan.to_payload(),
                **_trade_performance_payload(trade_performance_tags),
            },
        )
        record_audit_event(
            session,
            event_type="live_execution_error",
            entity_type="order",
            entity_id=str(order.id),
            severity="error",
            message="Live execution failed before exchange acceptance.",
            payload={
                "symbol": decision.symbol,
                "error": str(exc),
                "intent_type": intent_type,
                "requested_quantity": normalized_quantity,
                "requested_price": execution_price,
                "entry_execution_type": planned_entry_execution_type,
                "execution_policy": execution_plan.to_payload(),
                **_trade_performance_payload(trade_performance_tags),
            },
            correlation_ids=normalize_correlation_ids(execution_correlation_ids, execution_id=order.id),
        )
        record_health_event(
            session,
            component="live_execution",
            status="error",
            message="Unexpected live execution error.",
            payload={
                "symbol": decision.symbol,
                "error": str(exc),
                "intent_type": intent_type,
                "entry_execution_type": planned_entry_execution_type,
                "execution_policy": execution_plan.to_payload(),
                **_trade_performance_payload(trade_performance_tags),
            },
            correlation_ids=normalize_correlation_ids(execution_correlation_ids, execution_id=order.id),
        )
        session.flush()
        return {
            "order_id": order.id,
            "status": "error",
            "reason_codes": ["LIVE_EXECUTION_ERROR"],
            "error": str(exc),
            "intent_type": intent_type,
            "entry_execution_type": planned_entry_execution_type,
            "execution_policy": execution_plan.to_payload(),
        }
    order = execution_result["order"]
    fee_paid = float(execution_result["fees"])
    realized_pnl = float(execution_result["realized_pnl"])
    aggregate_fill_price = float(execution_result["average_fill_price"])
    aggregate_filled_quantity = float(execution_result["filled_quantity"])
    final_execution_status = str(execution_result["status"])
    execution_quality = dict(execution_result.get("execution_quality") or {})
    entry_execution_type = _entry_execution_type_for_plan(
        execution_plan,
        order_type=order.order_type,
        execution_quality=execution_quality,
    )
    signed_slippage_bps = _signed_slippage_bps(
        side=side,
        requested_price=execution_price,
        fill_price=aggregate_fill_price if aggregate_filled_quantity > 0 else 0.0,
    )
    net_realized_pnl = realized_pnl - fee_paid
    if execution_quality:
        execution_quality.setdefault("signed_slippage_pct", signed_slippage_bps / 10000.0)
        execution_quality.setdefault("signed_slippage_bps", signed_slippage_bps)
        execution_quality.setdefault("entry_execution_type", entry_execution_type)
        if aggregate_filled_quantity > 0 and abs(net_realized_pnl) > 1e-9:
            execution_quality["decision_quality_status"] = "profit" if net_realized_pnl > 0 else "loss"
        elif aggregate_filled_quantity > 0 and abs(net_realized_pnl) <= 1e-9:
            execution_quality["decision_quality_status"] = "flat_or_pending"
        order.metadata_json = {
            **(order.metadata_json or {}),
            "entry_execution_type": entry_execution_type,
            "execution_quality": execution_quality,
        }
        session.add(order)
        session.flush()
    create_exchange_pnl_snapshot(session, settings_row)

    try:
        post_trade_sync = _resync_exchange_state(
            session,
            settings_row,
            client=client,
            symbol=decision.symbol,
            event_prefix="live_post_order",
            component="live_execution",
            verify_protection=False,
            correlation_ids=normalize_correlation_ids(execution_correlation_ids, execution_id=order.id),
        )
    except RuntimeError as exc:
        error_text = str(exc)
        reason_code = error_text.split(":", 1)[0]
        return {
            "order_id": order.id,
            "status": final_execution_status,
            "reason_codes": [reason_code],
            "error": error_text,
            "intent_type": intent_type,
        }

    synced_position = post_trade_sync["position"]

    position = get_open_position(session, decision.symbol)
    if position is not None:
        order.position_id = position.id
        session.add(order)
        session.flush()

    position_management_payload: dict[str, object] | None = None
    if position is not None:
        if decision.decision in {"long", "short"}:
            position_management_payload = seed_position_management_metadata(
                position,
                max_holding_minutes=decision.max_holding_minutes,
                timeframe=decision.timeframe,
                stop_loss=intent.stop_loss or position.stop_loss,
                take_profit=intent.take_profit or position.take_profit,
                reset_partial_take_profit=True,
                holding_profile=decision.holding_profile,
                holding_profile_reason=decision.holding_profile_reason,
                initial_stop_type=str(holding_profile_payload.get("initial_stop_type") or "deterministic_hard_stop"),
                ai_stop_management_allowed=bool(holding_profile_payload.get("ai_stop_management_allowed", True)),
                hard_stop_active=bool(holding_profile_payload.get("hard_stop_active", True)),
            )
            if intent_type == "scale_in":
                add_on_debug = (
                    risk_result.debug_payload.get("add_on")
                    if isinstance(risk_result.debug_payload, dict) and isinstance(risk_result.debug_payload.get("add_on"), dict)
                    else {}
                )
                position_management_payload = record_add_on_metadata(
                    position,
                    add_on_r_multiple=_to_float(add_on_debug.get("current_r_multiple")) if add_on_debug else None,
                    add_on_reason=str(add_on_debug.get("add_on_reason") or "winner_only_add_on") if add_on_debug else "winner_only_add_on",
                    risk_multiplier=_to_float(add_on_debug.get("risk_pct_multiplier")) if add_on_debug else None,
                    leverage_multiplier=_to_float(add_on_debug.get("leverage_multiplier")) if add_on_debug else None,
                    notional_multiplier=_to_float(add_on_debug.get("notional_multiplier")) if add_on_debug else None,
                )
        elif decision.decision in {"reduce", "exit"}:
            position_management_payload = (
                position.metadata_json.get("position_management")
                if isinstance(position.metadata_json, dict)
                and isinstance(position.metadata_json.get("position_management"), dict)
                else None
            )
        if position_management_payload is not None:
            session.add(position)
            session.flush()
        position.metadata_json = _merge_trade_performance_tags(position.metadata_json, trade_performance_tags)
        session.add(position)
        session.flush()

    protection_result: dict[str, object] | None = None
    protection_lifecycle: ProtectionLifecycleSnapshot | None = None
    protective_order_ids: list[int] = []
    if position is not None and position.quantity > 0:
        protection_lifecycle = _initialize_protection_lifecycle(
            symbol=decision.symbol,
            trigger_source=f"execute_live_trade:{intent_type}",
            parent_order=order,
        )
        _persist_protection_lifecycle(session, parent_order=order, lifecycle=protection_lifecycle)
        protection_result = _ensure_protected_position(
            session,
            settings_row,
            client,
            symbol=decision.symbol,
            position=position,
            stop_loss=intent.stop_loss or position.stop_loss,
            take_profit=intent.take_profit or position.take_profit,
            decision_run_id=decision_run_id,
            risk_row=risk_row,
            parent_order=order,
            trigger_source=f"execute_live_trade:{intent_type}",
            pause_reason_code="PROTECTIVE_ORDER_FAILURE" if intent_type in {"entry", "scale_in"} else "MISSING_PROTECTIVE_ORDERS",
            protection_lifecycle=protection_lifecycle,
            client_order_id_seed=client_order_id_seed,
            correlation_ids=normalize_correlation_ids(execution_correlation_ids, execution_id=order.id),
        )
        created_order_ids = protection_result.get("created_order_ids")
        if isinstance(created_order_ids, list):
            protective_order_ids = [int(item) for item in created_order_ids]
        else:
            protective_order_ids = []
        if protection_result["status"] == "emergency_exit":
            return {
                "order_id": order.id,
                "position_id": order.position_id,
                "status": "emergency_exit",
                "exchange_status": order.exchange_status,
                "fill_price": aggregate_fill_price,
                "fill_quantity": aggregate_filled_quantity,
                "realized_pnl": realized_pnl,
                "fees": fee_paid,
                "protective_order_ids": protective_order_ids,
                "protective_state": protection_result["protection_state"],
                "protection_lifecycle": protection_result.get("protection_lifecycle"),
                "emergency_action": protection_result["emergency_action"],
                "intent_type": intent_type,
                "execution_attempts": execution_result["attempts"],
                "execution_quality": execution_quality,
                "entry_execution_type": entry_execution_type,
                "signed_slippage_bps": signed_slippage_bps,
                **_trade_performance_payload(trade_performance_tags),
                **holding_profile_payload,
                "position_management": {
                    "reduce_fraction": reduce_fraction,
                    "rationale_codes": decision.rationale_codes,
                    "metadata": position_management_payload,
                },
            }
    else:
        _cancel_exit_orders(session, client, decision.symbol)

    try:
        final_resync = _resync_exchange_state(
            session,
            settings_row,
            client=client,
            symbol=decision.symbol,
            event_prefix="live_post_protection",
            component="live_execution",
            verify_protection=True,
            correlation_ids=normalize_correlation_ids(execution_correlation_ids, execution_id=order.id),
        )
    except RuntimeError as exc:
        error_text = str(exc)
        reason_code = error_text.split(":", 1)[0]
        return {
            "order_id": order.id,
            "status": final_execution_status,
            "reason_codes": [reason_code],
            "error": error_text,
            "intent_type": intent_type,
            "protection_lifecycle": _protection_lifecycle_payload(protection_lifecycle),
        }

    synced_position = final_resync["position"]
    if protection_result is None:
        final_protection_state = final_resync["protection_state"]
    else:
        final_protection_state = protection_result["protection_state"]
    if protection_result is None:
        final_protection_lifecycle = _protection_lifecycle_payload(protection_lifecycle)
    else:
        final_protection_lifecycle = protection_result.get("protection_lifecycle")

    slippage_pct = 0.0
    if aggregate_filled_quantity > 0 and aggregate_fill_price > 0:
        slippage_pct = abs(aggregate_fill_price - execution_price) / max(execution_price, 1.0)
    if aggregate_filled_quantity > 0 and slippage_pct > settings_row.slippage_threshold_pct:
        create_alert(
            session,
            category="execution",
            severity="warning",
            title="Slippage threshold exceeded",
            message="??? ????? ???? ??????.",
            payload={
                "order_id": order.id,
                "slippage_pct": slippage_pct,
                "signed_slippage_bps": signed_slippage_bps,
                "intent_type": intent_type,
                "entry_execution_type": entry_execution_type,
                "execution_policy": execution_plan.to_payload(),
                "execution_quality": execution_quality,
            },
        )

    latest_price = position.mark_price if position is not None else market_snapshot.latest_price
    refresh_open_position_marks(session, {decision.symbol: latest_price})
    if (
        position is not None
        and decision.decision == "reduce"
        and "POSITION_MANAGEMENT_PARTIAL_TAKE_PROFIT" in set(decision.rationale_codes)
        and aggregate_filled_quantity > 0
    ):
        position_management_payload = mark_partial_take_profit_taken(position)
        session.add(position)
        session.flush()
    pnl_snapshot = final_resync["pnl_snapshot"]
    record_audit_event(
        session,
        event_type="live_execution",
        entity_type="order",
        entity_id=str(order.id),
        severity="info",
        message="??? ??? Binance? ???????.",
        payload={
            "position": synced_position,
            "protective_order_ids": protective_order_ids,
            "protective_state": final_protection_state if protection_result is not None else final_protection_state,
            "protection_lifecycle": final_protection_lifecycle,
            "pre_trade_protection": pre_trade_protection,
            "slippage_pct": slippage_pct,
            "signed_slippage_bps": signed_slippage_bps,
            "intent_type": intent_type,
            "entry_execution_type": entry_execution_type,
            "execution_policy": execution_plan.to_payload(),
            "execution_attempts": execution_result["attempts"],
            "execution_quality": execution_quality,
            "meta_gate": meta_gate_payload,
            **_trade_performance_payload(trade_performance_tags),
            **holding_profile_payload,
            "position_management": {
                "reduce_fraction": reduce_fraction,
                "rationale_codes": decision.rationale_codes,
                "metadata": position_management_payload,
            },
        },
        correlation_ids=normalize_correlation_ids(execution_correlation_ids, execution_id=order.id),
    )
    order.metadata_json = {
        **(order.metadata_json or {}),
        **_trade_performance_payload(trade_performance_tags),
        **holding_profile_payload,
        "protection_lifecycle": final_protection_lifecycle,
        "position_management": {
            "reduce_fraction": reduce_fraction,
            "rationale_codes": decision.rationale_codes,
            "metadata": position_management_payload,
        },
        "holding_profile": decision.holding_profile,
        "holding_profile_reason": decision.holding_profile_reason,
        "initial_stop_type": holding_profile_payload.get("initial_stop_type"),
        "ai_stop_management_allowed": holding_profile_payload.get("ai_stop_management_allowed"),
        "hard_stop_active": holding_profile_payload.get("hard_stop_active"),
    }
    session.add(order)
    session.flush()
    return {
        "order_id": order.id,
        "position_id": order.position_id,
        "status": final_execution_status,
        "rollout_mode": rollout_mode,
        "approved_notional_cap": approved_notional_cap,
        "rollout_notional_cap_applied": rollout_notional_cap_applied,
        "exchange_status": order.exchange_status,
        "fill_price": aggregate_fill_price,
        "fill_quantity": aggregate_filled_quantity,
        "realized_pnl": realized_pnl,
        "fees": fee_paid,
        "signed_slippage_bps": signed_slippage_bps,
        "entry_execution_type": entry_execution_type,
        **_trade_performance_payload(trade_performance_tags),
        "equity": pnl_snapshot.equity,
        "funding_sync": funding_sync,
        "protective_order_ids": protective_order_ids,
        "protective_state": final_protection_state,
        "protection_lifecycle": final_protection_lifecycle,
        "intent_type": intent_type,
        "execution_policy": execution_plan.to_payload(),
        "execution_attempts": execution_result["attempts"],
        "execution_quality": execution_quality,
        "meta_gate": meta_gate_payload,
        **holding_profile_payload,
        "position_management": {
            "reduce_fraction": reduce_fraction,
            "rationale_codes": decision.rationale_codes,
            "metadata": position_management_payload,
        },
    }
