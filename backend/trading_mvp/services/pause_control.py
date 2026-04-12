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
from trading_mvp.services.settings import (
    get_effective_symbols,
    get_runtime_credentials,
    is_live_execution_armed,
    set_trading_pause,
)
from trading_mvp.time_utils import utcnow_naive


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
    evaluated_symbols: list[str],
    protective_orders: dict[str, str],
    approval_state: str,
    approval_detail: dict[str, object],
) -> None:
    detail = dict(settings_row.pause_reason_detail or {})
    detail["auto_resume"] = {
        "status": status,
        "blockers": blockers,
        "last_checked_at": utcnow_naive().isoformat(),
        "evaluated_symbols": evaluated_symbols,
        "protective_orders": protective_orders,
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
        "evaluated_symbols": evaluated_symbols,
        "protective_orders": {},
        "approval_state": "not_checked",
        "pause_severity": pause_reason_severity(reason_code) if reason_code else None,
        "pause_recovery_class": pause_reason_recovery_class(reason_code) if reason_code else None,
        "trigger_source": trigger_source,
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
    defaults = get_settings()
    credentials = get_runtime_credentials(settings_row, defaults=defaults)

    if not defaults.live_trading_env_enabled:
        blockers.append("LIVE_ENV_DISABLED")
    if not settings_row.live_trading_enabled:
        blockers.append("LIVE_TRADING_DISABLED")
    if not settings_row.manual_live_approval:
        blockers.append("LIVE_APPROVAL_POLICY_DISABLED")
    if not credentials.binance_api_key or not credentials.binance_api_secret:
        blockers.append("LIVE_CREDENTIALS_MISSING")

    approval_allowed, approval_state, approval_detail = _approval_state(settings_row)
    result["approval_state"] = approval_state
    result["approval_detail"] = approval_detail
    if not approval_allowed:
        blockers.append("LIVE_APPROVAL_REQUIRED")

    latest_pnl = get_latest_pnl_snapshot(session, settings_row)
    previous_daily_pnl = _to_float(resume_context.get("daily_pnl_before_pause"))
    previous_equity = _to_float(resume_context.get("equity_before_pause"))
    previous_consecutive_losses = int(resume_context.get("consecutive_losses_before_pause") or 0)
    if latest_pnl.daily_pnl < 0 and abs(latest_pnl.daily_pnl) / max(latest_pnl.equity, 1.0) >= min(
        settings_row.max_daily_loss,
        HARD_MAX_DAILY_LOSS,
    ):
        blockers.append("DAILY_LOSS_LIMIT_REACHED")
    if latest_pnl.consecutive_losses >= settings_row.max_consecutive_losses:
        blockers.append("MAX_CONSECUTIVE_LOSSES_REACHED")
    if latest_pnl.consecutive_losses > previous_consecutive_losses:
        blockers.append("PORTFOLIO_RISK_UNCERTAIN")
    if previous_daily_pnl < 0 and latest_pnl.daily_pnl < previous_daily_pnl:
        blockers.append("PORTFOLIO_RISK_UNCERTAIN")
    if previous_equity > 0 and latest_pnl.equity < previous_equity * 0.85:
        blockers.append("PORTFOLIO_RISK_UNCERTAIN")

    protective_summary: dict[str, str] = {}
    if not blockers:
        try:
            client = _build_client(settings_row)
        except Exception:
            blockers.append("EXCHANGE_CONNECTIVITY_TEMPORARY_FAILURE")
            client = None

        if client is not None:
            try:
                client.get_account_info()
            except Exception as exc:
                blockers.append("EXCHANGE_ACCOUNT_STATE_UNAVAILABLE")
                result["health_error"] = str(exc)

            for symbol in evaluated_symbols:
                try:
                    candles = client.fetch_klines(symbol=symbol, interval=settings_row.default_timeframe, limit=2)
                    latest_candle = candles[-1]
                    staleness = (utcnow_naive() - latest_candle.timestamp).total_seconds()
                    if staleness > settings_row.stale_market_seconds:
                        blockers.append("TEMPORARY_MARKET_DATA_FAILURE")
                        protective_summary.setdefault(symbol, "market_data_stale")
                        continue
                except Exception as exc:
                    blockers.append("TEMPORARY_MARKET_DATA_FAILURE")
                    protective_summary.setdefault(symbol, "market_data_unavailable")
                    result.setdefault("market_errors", {})[symbol] = str(exc)
                    continue

                try:
                    open_orders = client.get_open_orders(symbol)
                except Exception as exc:
                    blockers.append("EXCHANGE_OPEN_ORDERS_SYNC_FAILED")
                    result.setdefault("sync_errors", {})[f"{symbol}:open_orders"] = str(exc)
                    protective_summary.setdefault(symbol, "open_orders_unavailable")
                    continue

                try:
                    remote_positions = client.get_position_information(symbol)
                except Exception as exc:
                    blockers.append("EXCHANGE_POSITION_SYNC_FAILED")
                    result.setdefault("sync_errors", {})[f"{symbol}:positions"] = str(exc)
                    protective_summary.setdefault(symbol, "positions_unavailable")
                    continue

                consistency_blocker = _position_consistency_blocker(settings_row, symbol, remote_positions)
                if consistency_blocker:
                    blockers.append(consistency_blocker)
                    protective_summary.setdefault(symbol, "state_inconsistent")
                    continue

                has_open_position = any(abs(_to_float(item.get("positionAmt"))) > 0 for item in remote_positions)
                if has_open_position and not _has_protective_orders(open_orders):
                    blockers.append("MISSING_PROTECTIVE_ORDERS")
                    protective_summary[symbol] = "missing"
                elif has_open_position:
                    protective_summary[symbol] = "ready"
                else:
                    protective_summary[symbol] = "flat"

    deduped_blockers = list(dict.fromkeys(blockers))
    result["blockers"] = deduped_blockers
    result["protective_orders"] = protective_summary
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
            evaluated_symbols=[str(item) for item in evaluation.get("evaluated_symbols", [])],
            protective_orders={str(key): str(value) for key, value in evaluation.get("protective_orders", {}).items()},
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
            evaluated_symbols=[str(item) for item in evaluation["evaluated_symbols"]],
            protective_orders={str(key): str(value) for key, value in evaluation["protective_orders"].items()},
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
