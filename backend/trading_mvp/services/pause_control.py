from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session, object_session

from trading_mvp.config import get_settings
from trading_mvp.models import Setting
from trading_mvp.services.account import get_latest_pnl_snapshot, get_open_position
from trading_mvp.services.audit import record_audit_event, record_health_event
from trading_mvp.services.binance import BinanceClient
from trading_mvp.services.pause_policy import (
    get_pause_reason_policy,
    pause_reason_allows_auto_resume,
    pause_reason_recovery_class,
    pause_reason_severity,
)
from trading_mvp.services.risk import HARD_MAX_DAILY_LOSS
from trading_mvp.services.runtime_state import (
    DEGRADED_MANAGE_ONLY_STATE,
    EMERGENCY_EXIT_STATE,
    PROTECTION_REQUIRED_STATE,
    TRADABLE_STATE,
    get_protection_recovery_detail,
    summarize_runtime_state,
)
from trading_mvp.services.settings import (
    get_effective_symbols,
    get_runtime_credentials,
    is_live_execution_armed,
    set_trading_pause,
)
from trading_mvp.time_utils import utcnow_naive


def _recompute_operating_state(symbol_states: dict[str, dict[str, Any]]) -> str:
    states = {
        str(item.get("state", TRADABLE_STATE))
        for item in symbol_states.values()
        if item.get("state")
    }
    if EMERGENCY_EXIT_STATE in states:
        return EMERGENCY_EXIT_STATE
    if DEGRADED_MANAGE_ONLY_STATE in states:
        return DEGRADED_MANAGE_ONLY_STATE
    if PROTECTION_REQUIRED_STATE in states:
        return PROTECTION_REQUIRED_STATE
    return TRADABLE_STATE


def _write_runtime_state(
    session: Session,
    settings_row: Setting,
    *,
    operating_state: str,
    recovery_status: str,
    auto_recovery_active: bool,
    symbol_states: dict[str, dict[str, Any]],
    last_error: str | None = None,
) -> None:
    detail = dict(settings_row.pause_reason_detail or {})
    detail["operating_state"] = operating_state
    detail["protection_recovery"] = {
        "status": recovery_status,
        "auto_recovery_active": auto_recovery_active,
        "last_transition_at": utcnow_naive().isoformat(),
        "last_error": last_error,
        "symbol_states": symbol_states,
        "missing_symbols": [
            symbol
            for symbol, value in symbol_states.items()
            if bool(value.get("missing_components"))
        ],
        "missing_items": {
            symbol: [str(item) for item in value.get("missing_components", [])]
            for symbol, value in symbol_states.items()
            if bool(value.get("missing_components"))
        },
    }
    settings_row.pause_reason_detail = detail
    session.add(settings_row)
    session.flush()


def set_symbol_protection_state(
    session: Session,
    settings_row: Setting,
    *,
    symbol: str,
    state: str,
    trigger_source: str,
    missing_components: list[str] | None = None,
    auto_recovery_active: bool = False,
    recovery_status: str | None = None,
    last_error: str | None = None,
    emergency_action: dict[str, object] | None = None,
    reset_failures: bool = False,
) -> dict[str, Any]:
    current_summary = summarize_runtime_state(settings_row)
    recovery = get_protection_recovery_detail(settings_row)
    symbol_states = dict(recovery.get("symbol_states", {}))
    current_symbol_state = dict(symbol_states.get(symbol, {}))
    failure_count = 0 if reset_failures else int(current_symbol_state.get("failure_count", 0) or 0)
    if state != TRADABLE_STATE and last_error:
        failure_count += 1
    updated_symbol_state = {
        **current_symbol_state,
        "state": state,
        "missing_components": [str(item) for item in (missing_components or [])],
        "failure_count": failure_count,
        "auto_recovery_active": auto_recovery_active,
        "recovery_status": recovery_status or state.lower(),
        "last_error": last_error,
        "last_transition_at": utcnow_naive().isoformat(),
        "trigger_source": trigger_source,
    }
    if emergency_action is not None:
        updated_symbol_state["emergency_action"] = emergency_action
    if state == TRADABLE_STATE:
        symbol_states.pop(symbol, None)
    else:
        symbol_states[symbol] = updated_symbol_state

    operating_state = _recompute_operating_state(symbol_states)
    resolved_status = recovery_status or (
        "restored"
        if operating_state == TRADABLE_STATE
        else "emergency_exit"
        if operating_state == EMERGENCY_EXIT_STATE
        else "manage_only"
        if operating_state == DEGRADED_MANAGE_ONLY_STATE
        else "protection_required"
    )
    _write_runtime_state(
        session,
        settings_row,
        operating_state=operating_state,
        recovery_status=resolved_status,
        auto_recovery_active=auto_recovery_active if operating_state != TRADABLE_STATE else False,
        symbol_states=symbol_states,
        last_error=last_error,
    )

    next_summary = summarize_runtime_state(settings_row)
    if current_summary["operating_state"] != next_summary["operating_state"]:
        record_audit_event(
            session,
            event_type="operating_state_changed",
            entity_type="settings",
            entity_id=str(settings_row.id),
            severity="warning" if next_summary["operating_state"] != TRADABLE_STATE else "info",
            message="Runtime trading operating state updated.",
            payload={
                "previous_state": current_summary["operating_state"],
                "operating_state": next_summary["operating_state"],
                "symbol": symbol,
                "trigger_source": trigger_source,
                "missing_components": missing_components or [],
                "failure_count": failure_count,
                "last_error": last_error,
            },
        )
    return next_summary


def clear_symbol_protection_state(
    session: Session,
    settings_row: Setting,
    *,
    symbol: str,
    trigger_source: str,
) -> dict[str, Any]:
    return set_symbol_protection_state(
        session,
        settings_row,
        symbol=symbol,
        state=TRADABLE_STATE,
        trigger_source=trigger_source,
        missing_components=[],
        auto_recovery_active=False,
        recovery_status="restored",
        last_error=None,
        reset_failures=True,
    )


def mark_manage_only_state(
    session: Session,
    settings_row: Setting,
    *,
    symbol: str,
    trigger_source: str,
    missing_components: list[str],
    last_error: str,
    emergency_action: dict[str, object] | None = None,
) -> dict[str, Any]:
    return set_symbol_protection_state(
        session,
        settings_row,
        symbol=symbol,
        state=DEGRADED_MANAGE_ONLY_STATE,
        trigger_source=trigger_source,
        missing_components=missing_components,
        auto_recovery_active=False,
        recovery_status="manage_only",
        last_error=last_error,
        emergency_action=emergency_action,
    )


def _to_float(value: object) -> float:
    if value in {None, ""}:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value))


def _flag_enabled(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() == "true"


def _build_client(settings_row: Setting) -> BinanceClient:
    credentials = get_runtime_credentials(settings_row)
    defaults = get_settings()
    return BinanceClient(
        api_key=credentials.binance_api_key,
        api_secret=credentials.binance_api_secret,
        testnet_enabled=settings_row.binance_testnet_enabled,
        futures_enabled=settings_row.binance_futures_enabled,
        recv_window_ms=defaults.exchange_recv_window_ms,
    )


def _read_resume_context(settings_row: Setting) -> dict[str, Any]:
    context = settings_row.pause_reason_detail.get("resume_context", {})
    return dict(context) if isinstance(context, dict) else {}


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _approval_state(settings_row: Setting) -> tuple[bool, str, dict[str, object]]:
    if is_live_execution_armed(settings_row):
        return True, "armed", {}

    resume_context = _read_resume_context(settings_row)
    grace_until = _parse_datetime(resume_context.get("approval_grace_until"))
    now = utcnow_naive()
    if (
        settings_row.pause_origin == "system"
        and pause_reason_allows_auto_resume(settings_row.pause_reason_code)
        and bool(resume_context.get("live_execution_ready_before_pause"))
        and grace_until is not None
        and grace_until > now
    ):
        return True, "grace", {"approval_grace_until": grace_until.isoformat()}
    return False, "required", {}


def _has_protective_orders(open_orders: list[dict[str, object]]) -> bool:
    return any(
        _flag_enabled(item.get("closePosition")) or _flag_enabled(item.get("reduceOnly"))
        for item in open_orders
    )


def _position_consistency_blocker(
    settings_row: Setting,
    symbol: str,
    remote_positions: list[dict[str, object]],
) -> str | None:
    local_position = get_open_position(settings_row_session(settings_row), symbol)
    active_remote = next((item for item in remote_positions if abs(_to_float(item.get("positionAmt"))) > 0), None)
    if local_position is None and active_remote is None:
        return None
    if local_position is None or active_remote is None:
        return "ACCOUNT_STATE_INCONSISTENT"

    remote_qty = abs(_to_float(active_remote.get("positionAmt")))
    remote_side = "long" if _to_float(active_remote.get("positionAmt")) > 0 else "short"
    if local_position.side != remote_side:
        return "ACCOUNT_STATE_INCONSISTENT"
    if abs(local_position.quantity - remote_qty) > max(0.001, remote_qty * 0.05):
        return "ACCOUNT_STATE_INCONSISTENT"
    return None


def settings_row_session(settings_row: Setting) -> Session:
    session = object_session(settings_row)
    if session is None:
        raise RuntimeError("Settings row is detached from session.")
    return session


def _write_auto_resume_state(
    session: Session,
    settings_row: Setting,
    *,
    status: str,
    blockers: list[str],
    symbol_blockers: dict[str, list[str]],
    blocker_details: list[dict[str, object]],
    evaluated_symbols: list[str],
    protective_orders: dict[str, str],
    market_data_status: dict[str, str],
    sync_status: dict[str, str],
    approval_state: str,
    approval_detail: dict[str, object],
) -> None:
    detail = dict(settings_row.pause_reason_detail or {})
    detail["auto_resume"] = {
        "status": status,
        "blockers": blockers,
        "symbol_blockers": symbol_blockers,
        "blocker_details": blocker_details,
        "last_checked_at": utcnow_naive().isoformat(),
        "evaluated_symbols": evaluated_symbols,
        "protective_orders": protective_orders,
        "market_data_status": market_data_status,
        "sync_status": sync_status,
        "approval_state": approval_state,
        "approval_detail": approval_detail,
    }
    settings_row.pause_reason_detail = detail
    session.add(settings_row)
    session.flush()


def evaluate_auto_resume_safety(
    session: Session,
    settings_row: Setting,
    *,
    trigger_source: str = "system",
) -> dict[str, Any]:
    reason_code = settings_row.pause_reason_code
    pause_policy = get_pause_reason_policy(reason_code)
    evaluated_symbols = get_effective_symbols(settings_row)
    resume_context = _read_resume_context(settings_row)
    result: dict[str, Any] = {
        "attempted": False,
        "resumed": False,
        "allowed": False,
        "status": "not_paused",
        "reason_code": reason_code,
        "pause_origin": settings_row.pause_origin,
        "auto_resume_after": settings_row.auto_resume_after.isoformat() if settings_row.auto_resume_after else None,
        "blockers": [],
        "symbol_blockers": {},
        "blocker_details": [],
        "evaluated_symbols": evaluated_symbols,
        "protective_orders": {},
        "market_data_status": {},
        "sync_status": {},
        "approval_state": "not_checked",
        "approval_detail": {},
        "pause_severity": pause_reason_severity(reason_code) if reason_code else None,
        "pause_recovery_class": pause_reason_recovery_class(reason_code) if reason_code else None,
        "trigger_source": trigger_source,
        "health_error": None,
        "market_errors": {},
        "sync_errors": {},
    }
    if not settings_row.trading_paused:
        return result

    if not pause_policy.auto_resume_eligible:
        result["status"] = "not_eligible"
        return result

    now = utcnow_naive()
    if settings_row.auto_resume_after is not None and settings_row.auto_resume_after > now:
        result["attempted"] = True
        result["status"] = "waiting_cooldown"
        return result

    result["attempted"] = True
    blockers: list[str] = []
    symbol_blockers: dict[str, list[str]] = {}
    blocker_details: list[dict[str, object]] = []
    market_data_status: dict[str, str] = {}
    sync_status: dict[str, str] = {}
    defaults = get_settings()
    credentials = get_runtime_credentials(settings_row, defaults=defaults)

    def add_blocker(
        code: str,
        *,
        symbol: str | None = None,
        detail: str | None = None,
        source: str | None = None,
    ) -> None:
        blockers.append(code)
        if symbol is not None:
            values = symbol_blockers.setdefault(symbol, [])
            if code not in values:
                values.append(code)
        blocker_detail: dict[str, object] = {"code": code}
        if symbol is not None:
            blocker_detail["symbol"] = symbol
        if detail is not None:
            blocker_detail["detail"] = detail
        if source is not None:
            blocker_detail["source"] = source
        blocker_details.append(blocker_detail)

    if not defaults.live_trading_env_enabled:
        add_blocker("LIVE_ENV_DISABLED", source="settings")
    if not settings_row.live_trading_enabled:
        add_blocker("LIVE_TRADING_DISABLED", source="settings")
    if not settings_row.manual_live_approval:
        add_blocker("LIVE_APPROVAL_POLICY_DISABLED", source="settings")
    if not credentials.binance_api_key or not credentials.binance_api_secret:
        add_blocker("LIVE_CREDENTIALS_MISSING", source="credentials")

    approval_allowed, approval_state, approval_detail = _approval_state(settings_row)
    result["approval_state"] = approval_state
    result["approval_detail"] = approval_detail
    if not approval_allowed:
        add_blocker("LIVE_APPROVAL_REQUIRED", source="approval")

    latest_pnl = get_latest_pnl_snapshot(session, settings_row)
    previous_daily_pnl = _to_float(resume_context.get("daily_pnl_before_pause"))
    previous_equity = _to_float(resume_context.get("equity_before_pause"))
    previous_consecutive_losses = int(resume_context.get("consecutive_losses_before_pause") or 0)
    if latest_pnl.daily_pnl < 0 and abs(latest_pnl.daily_pnl) / max(latest_pnl.equity, 1.0) >= min(
        settings_row.max_daily_loss,
        HARD_MAX_DAILY_LOSS,
    ):
        add_blocker("DAILY_LOSS_LIMIT_REACHED", detail="Daily loss threshold exceeded.", source="pnl")
    if latest_pnl.consecutive_losses >= settings_row.max_consecutive_losses:
        add_blocker("MAX_CONSECUTIVE_LOSSES_REACHED", detail="Consecutive loss threshold exceeded.", source="pnl")
    if latest_pnl.consecutive_losses > previous_consecutive_losses:
        add_blocker("PORTFOLIO_RISK_UNCERTAIN", detail="Consecutive losses increased while paused.", source="pnl")
    if previous_daily_pnl < 0 and latest_pnl.daily_pnl < previous_daily_pnl:
        add_blocker("PORTFOLIO_RISK_UNCERTAIN", detail="Daily PnL deteriorated while paused.", source="pnl")
    if previous_equity > 0 and latest_pnl.equity < previous_equity * 0.85:
        add_blocker("PORTFOLIO_RISK_UNCERTAIN", detail="Equity dropped materially while paused.", source="pnl")

    protective_summary: dict[str, str] = {}
    if not blockers:
        try:
            client = _build_client(settings_row)
        except Exception:
            add_blocker(
                "EXCHANGE_CONNECTIVITY_TEMPORARY_FAILURE",
                detail="Failed to initialize Binance client for auto-resume evaluation.",
                source="client",
            )
            client = None

        if client is not None:
            try:
                client.get_account_info()
            except Exception as exc:
                add_blocker(
                    "EXCHANGE_ACCOUNT_STATE_UNAVAILABLE",
                    detail=str(exc),
                    source="account",
                )
                result["health_error"] = str(exc)

            for symbol in evaluated_symbols:
                try:
                    candles = client.fetch_klines(symbol=symbol, interval=settings_row.default_timeframe, limit=2)
                    latest_candle = candles[-1]
                    staleness = (utcnow_naive() - latest_candle.timestamp).total_seconds()
                    if staleness > settings_row.stale_market_seconds:
                        add_blocker(
                            "TEMPORARY_MARKET_DATA_FAILURE",
                            symbol=symbol,
                            detail=f"Market data stale by {int(staleness)} seconds.",
                            source="market_data",
                        )
                        market_data_status[symbol] = "stale"
                        protective_summary.setdefault(symbol, "market_data_stale")
                        continue
                    market_data_status[symbol] = "ok"
                except Exception as exc:
                    add_blocker(
                        "TEMPORARY_MARKET_DATA_FAILURE",
                        symbol=symbol,
                        detail=str(exc),
                        source="market_data",
                    )
                    market_data_status[symbol] = "unavailable"
                    protective_summary.setdefault(symbol, "market_data_unavailable")
                    result["market_errors"][symbol] = str(exc)
                    continue

                try:
                    open_orders = client.get_open_orders(symbol)
                except Exception as exc:
                    add_blocker(
                        "EXCHANGE_OPEN_ORDERS_SYNC_FAILED",
                        symbol=symbol,
                        detail=str(exc),
                        source="open_orders",
                    )
                    sync_status[symbol] = "open_orders_failed"
                    result["sync_errors"][f"{symbol}:open_orders"] = str(exc)
                    protective_summary.setdefault(symbol, "open_orders_unavailable")
                    continue

                try:
                    remote_positions = client.get_position_information(symbol)
                except Exception as exc:
                    add_blocker(
                        "EXCHANGE_POSITION_SYNC_FAILED",
                        symbol=symbol,
                        detail=str(exc),
                        source="positions",
                    )
                    sync_status[symbol] = "positions_failed"
                    result["sync_errors"][f"{symbol}:positions"] = str(exc)
                    protective_summary.setdefault(symbol, "positions_unavailable")
                    continue

                consistency_blocker = _position_consistency_blocker(settings_row, symbol, remote_positions)
                if consistency_blocker:
                    add_blocker(
                        consistency_blocker,
                        symbol=symbol,
                        detail="Local and exchange position state do not match.",
                        source="reconciliation",
                    )
                    sync_status[symbol] = "state_inconsistent"
                    protective_summary.setdefault(symbol, "state_inconsistent")
                    continue

                has_open_position = any(abs(_to_float(item.get("positionAmt"))) > 0 for item in remote_positions)
                if has_open_position and not _has_protective_orders(open_orders):
                    add_blocker(
                        "MISSING_PROTECTIVE_ORDERS",
                        symbol=symbol,
                        detail="Open position exists without reduceOnly/closePosition protective orders.",
                        source="protective_orders",
                    )
                    sync_status[symbol] = "protective_orders_missing"
                    protective_summary[symbol] = "missing"
                elif has_open_position:
                    sync_status[symbol] = "position_ready"
                    protective_summary[symbol] = "ready"
                else:
                    sync_status[symbol] = "flat"
                    protective_summary[symbol] = "flat"

    deduped_blockers = list(dict.fromkeys(blockers))
    result["blockers"] = deduped_blockers
    result["symbol_blockers"] = symbol_blockers
    result["blocker_details"] = blocker_details
    result["protective_orders"] = protective_summary
    result["market_data_status"] = market_data_status
    result["sync_status"] = sync_status
    result["allowed"] = not deduped_blockers
    result["status"] = "ready" if result["allowed"] else "blocked"
    return result


def build_auto_resume_blockers(session: Session, settings_row: Setting) -> list[str]:
    return list(evaluate_auto_resume_safety(session, settings_row)["blockers"])


def check_resume_readiness(session: Session, settings_row: Setting) -> dict[str, Any]:
    return evaluate_auto_resume_safety(session, settings_row)


def attempt_auto_resume(
    session: Session,
    settings_row: Setting,
    *,
    trigger_source: str = "system",
) -> dict[str, Any]:
    evaluation = evaluate_auto_resume_safety(session, settings_row, trigger_source=trigger_source)
    if not settings_row.trading_paused:
        return evaluation

    if evaluation["status"] in {"not_eligible", "waiting_cooldown"}:
        _write_auto_resume_state(
            session,
            settings_row,
            status=str(evaluation["status"]),
            blockers=[str(item) for item in evaluation.get("blockers", [])],
            symbol_blockers={
                str(key): [str(item) for item in value]
                for key, value in evaluation.get("symbol_blockers", {}).items()
            },
            blocker_details=[
                {str(key): value for key, value in item.items()}
                for item in evaluation.get("blocker_details", [])
            ],
            evaluated_symbols=[str(item) for item in evaluation.get("evaluated_symbols", [])],
            protective_orders={str(key): str(value) for key, value in evaluation.get("protective_orders", {}).items()},
            market_data_status={str(key): str(value) for key, value in evaluation.get("market_data_status", {}).items()},
            sync_status={str(key): str(value) for key, value in evaluation.get("sync_status", {}).items()},
            approval_state=str(evaluation.get("approval_state", "not_checked")),
            approval_detail=dict(evaluation.get("approval_detail", {})),
        )
        record_audit_event(
            session,
            event_type="trading_auto_resume_skipped",
            entity_type="settings",
            entity_id=str(settings_row.id),
            severity="info",
            message="Trading auto resume was skipped.",
            payload=evaluation,
        )
        record_health_event(
            session,
            component="trading_pause",
            status="warning",
            message="Trading auto resume skipped.",
            payload=evaluation,
        )
        session.flush()
        return evaluation

    record_audit_event(
        session,
        event_type="trading_auto_resume_attempted",
        entity_type="settings",
        entity_id=str(settings_row.id),
        severity="info",
        message="Trading auto resume safety evaluation started.",
        payload=evaluation,
    )
    record_health_event(
        session,
        component="trading_pause",
        status="info",
        message="Trading auto resume safety evaluation started.",
        payload=evaluation,
    )
    if not evaluation["allowed"]:
        _write_auto_resume_state(
            session,
            settings_row,
            status="blocked",
            blockers=[str(item) for item in evaluation["blockers"]],
            symbol_blockers={str(key): [str(item) for item in value] for key, value in evaluation["symbol_blockers"].items()},
            blocker_details=[{str(key): value for key, value in item.items()} for item in evaluation["blocker_details"]],
            evaluated_symbols=[str(item) for item in evaluation["evaluated_symbols"]],
            protective_orders={str(key): str(value) for key, value in evaluation["protective_orders"].items()},
            market_data_status={str(key): str(value) for key, value in evaluation["market_data_status"].items()},
            sync_status={str(key): str(value) for key, value in evaluation["sync_status"].items()},
            approval_state=str(evaluation.get("approval_state", "not_checked")),
            approval_detail=dict(evaluation.get("approval_detail", {})),
        )
        record_audit_event(
            session,
            event_type="trading_auto_resume_blocked",
            entity_type="settings",
            entity_id=str(settings_row.id),
            severity="warning",
            message="Trading auto resume was blocked by current safety checks.",
            payload=evaluation,
        )
        record_health_event(
            session,
            component="trading_pause",
            status="warning",
            message="Trading auto resume blocked.",
            payload=evaluation,
        )
        session.flush()
        return evaluation

    previous_reason = settings_row.pause_reason_code
    previous_pause_at = settings_row.pause_triggered_at.isoformat() if settings_row.pause_triggered_at else None
    approval_state = str(evaluation.get("approval_state", "not_checked"))
    approval_detail = dict(evaluation.get("approval_detail", {}))
    if approval_state == "grace":
        grace_until = _parse_datetime(approval_detail.get("approval_grace_until"))
        if grace_until is not None:
            settings_row.live_execution_armed = True
            settings_row.live_execution_armed_until = grace_until
            session.add(settings_row)
            session.flush()
    set_trading_pause(session, False)
    evaluation["resumed"] = True
    evaluation["allowed"] = True
    evaluation["status"] = "resumed"
    record_audit_event(
        session,
        event_type="trading_auto_resumed",
        entity_type="settings",
        entity_id=str(settings_row.id),
        severity="info",
        message="Trading pause automatically cleared after safety checks passed.",
        payload={
            **evaluation,
            "reason_code": previous_reason,
            "previous_pause_at": previous_pause_at,
        },
    )
    record_health_event(
        session,
        component="trading_pause",
        status="ok",
        message="Trading pause automatically cleared.",
        payload=evaluation,
    )
    session.flush()
    return evaluation
