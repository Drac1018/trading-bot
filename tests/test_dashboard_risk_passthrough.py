from __future__ import annotations

from datetime import timedelta

from trading_mvp.models import RiskCheck
from trading_mvp.services.dashboard import get_operator_dashboard, get_overview, get_risk_checks
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive


def test_overview_latest_risk_passthrough_adds_snapshot_cycle_and_as_of(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.tracked_symbols = ["BTCUSDT"]
    db_session.add(settings_row)
    db_session.flush()

    as_of = utcnow_naive() - timedelta(minutes=3)
    risk_payload = {
        "allowed": False,
        "decision": "long",
        "reason_codes": ["ENTRY_TRIGGER_NOT_MET", "SLIPPAGE_THRESHOLD_EXCEEDED"],
        "blocked_reason_codes": ["ENTRY_TRIGGER_NOT_MET", "SLIPPAGE_THRESHOLD_EXCEEDED"],
        "adjustment_reason_codes": [],
        "degraded_reason_codes": [],
        "protection_reason_codes": [],
        "approved_risk_pct": 0.0,
        "approved_leverage": 0.0,
        "raw_projected_notional": 32100.5,
        "approved_projected_notional": 0.0,
        "approved_quantity": None,
        "exposure_headroom_snapshot": {"limiting_headroom_notional": 1234.5},
        "debug_payload": {
            "headroom": {"limiting_headroom_notional": 1234.5},
            "requested_exposure_limit_codes": ["ENTRY_TRIGGER_NOT_MET"],
            "final_exposure_limit_codes": ["ENTRY_TRIGGER_NOT_MET"],
            "lead_market_context": {
                "lead_context_status": "partial",
                "missing_lead_symbols": ["ETHUSDT"],
                "reason_codes": ["LEAD_CONTEXT_PARTIAL", "LEAD_CONTEXT_MISSING_ETHUSDT"],
            },
            "binance_rest_summary": {
                "status": "unavailable",
                "circuit_state": "open",
                "entry_block_reason_code": "BINANCE_REST_CIRCUIT_OPEN",
            },
        },
    }
    risk_row = RiskCheck(
        symbol="BTCUSDT",
        decision_run_id=321,
        market_snapshot_id=654,
        allowed=False,
        decision="long",
        reason_codes=["ENTRY_TRIGGER_NOT_MET", "SLIPPAGE_THRESHOLD_EXCEEDED"],
        approved_risk_pct=0.0,
        approved_leverage=0.0,
        payload=risk_payload,
    )
    db_session.add(risk_row)
    db_session.flush()
    risk_row.created_at = as_of
    db_session.add(risk_row)
    db_session.flush()

    overview = get_overview(db_session)

    assert overview.latest_risk is not None
    assert overview.latest_risk["reason_codes"] == ["ENTRY_TRIGGER_NOT_MET", "SLIPPAGE_THRESHOLD_EXCEEDED"]
    assert overview.latest_risk["blocked_reason_codes"] == ["ENTRY_TRIGGER_NOT_MET", "SLIPPAGE_THRESHOLD_EXCEEDED"]
    assert overview.latest_risk["degraded_reason_codes"] == []
    assert overview.latest_risk["protection_reason_codes"] == []
    assert overview.latest_risk["approved_projected_notional"] == 0.0
    assert overview.latest_risk["approved_quantity"] is None
    assert overview.latest_risk["exposure_headroom_snapshot"] == {"limiting_headroom_notional": 1234.5}
    assert overview.latest_risk["debug_payload"] == risk_payload["debug_payload"]
    assert overview.latest_risk["debug_payload"]["lead_market_context"]["lead_context_status"] == "partial"
    assert overview.latest_risk["snapshot_id"] == 654
    assert overview.latest_risk["cycle_id"] == "321"
    assert overview.latest_risk["as_of"] == as_of


def test_dashboard_filters_requested_only_auto_resizable_exposure_reason(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.tracked_symbols = ["BTCUSDT"]
    db_session.add(settings_row)
    db_session.flush()

    as_of = utcnow_naive() - timedelta(minutes=1)
    risk_payload = {
        "allowed": False,
        "decision": "long",
        "reason_codes": ["LIVE_APPROVAL_REQUIRED", "LARGEST_POSITION_LIMIT_REACHED"],
        "blocked_reason_codes": ["LIVE_APPROVAL_REQUIRED", "LARGEST_POSITION_LIMIT_REACHED"],
        "adjustment_reason_codes": [],
        "degraded_reason_codes": [],
        "protection_reason_codes": [],
        "approved_risk_pct": 0.0,
        "approved_leverage": 0.0,
        "raw_projected_notional": 463.863014,
        "approved_projected_notional": 0.0,
        "approved_quantity": None,
        "debug_payload": {
            "requested_exposure_limit_codes": ["LARGEST_POSITION_LIMIT_REACHED"],
            "final_exposure_limit_codes": [],
            "headroom": {
                "limiting_headroom_notional": 347.897261,
                "minimum_actionable_notional": 77.4281,
            },
        },
        "snapshot_id": 2671,
        "cycle_id": "2671",
        "as_of": as_of.isoformat(),
    }
    risk_row = RiskCheck(
        symbol="BTCUSDT",
        decision_run_id=2671,
        market_snapshot_id=2671,
        allowed=False,
        decision="long",
        reason_codes=["LIVE_APPROVAL_REQUIRED", "LARGEST_POSITION_LIMIT_REACHED"],
        approved_risk_pct=0.0,
        approved_leverage=0.0,
        payload=risk_payload,
    )
    db_session.add(risk_row)
    db_session.flush()
    risk_row.created_at = as_of
    db_session.add(risk_row)
    db_session.flush()

    overview = get_overview(db_session)
    operator_payload = get_operator_dashboard(db_session)
    btc = next(item for item in operator_payload.symbols if item.symbol == "BTCUSDT")

    assert overview.latest_risk is not None
    assert overview.latest_risk["reason_codes"] == ["LIVE_APPROVAL_REQUIRED"]
    assert overview.latest_risk["blocked_reason_codes"] == ["LIVE_APPROVAL_REQUIRED"]
    assert overview.latest_risk["debug_payload"]["requested_exposure_limit_codes"] == [
        "LARGEST_POSITION_LIMIT_REACHED"
    ]
    assert btc.risk_guard.reason_codes == ["LIVE_APPROVAL_REQUIRED"]
    assert btc.risk_guard.blocked_reason_codes == ["LIVE_APPROVAL_REQUIRED"]
    assert btc.risk_guard.debug_payload["requested_exposure_limit_codes"] == ["LARGEST_POSITION_LIMIT_REACHED"]

    risk_checks = get_risk_checks(db_session, limit=1, compact=True)

    assert risk_checks[0]["reason_codes"] == ["LIVE_APPROVAL_REQUIRED"]
    assert risk_checks[0]["blocked_reason_codes"] == ["LIVE_APPROVAL_REQUIRED"]
    assert risk_checks[0]["payload"]["reason_codes"] == ["LIVE_APPROVAL_REQUIRED"]
    assert risk_checks[0]["payload"]["blocked_reason_codes"] == ["LIVE_APPROVAL_REQUIRED"]
    assert risk_checks[0]["risk_guard_result"]["reason_codes"] == ["LIVE_APPROVAL_REQUIRED"]
    assert risk_checks[0]["risk_guard_result"]["blocked_reason_codes"] == ["LIVE_APPROVAL_REQUIRED"]


def test_operator_dashboard_risk_guard_passthroughs_current_cycle_result_without_recalculation(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.tracked_symbols = ["BTCUSDT"]
    db_session.add(settings_row)
    db_session.flush()

    as_of = utcnow_naive() - timedelta(minutes=1)
    risk_payload = {
        "allowed": True,
        "decision": "long",
        "reason_codes": [],
        "blocked_reason_codes": [],
        "adjustment_reason_codes": ["ENTRY_AUTO_RESIZED", "ENTRY_CLAMPED_TO_DIRECTIONAL_LIMIT"],
        "degraded_reason_codes": ["PROTECTION_REQUIRED"],
        "protection_reason_codes": ["PROTECTION_REQUIRED", "MISSING_PROTECTIVE_ORDERS"],
        "survival_path": "protective_recovery",
        "approved_risk_pct": 0.004321,
        "approved_leverage": 1.25,
        "raw_projected_notional": 100000.123456,
        "approved_projected_notional": 40000.456789,
        "approved_quantity": 0.617283,
        "auto_resized_entry": True,
        "size_adjustment_ratio": 0.400004,
        "auto_resize_reason": "CLAMPED_TO_DIRECTIONAL_HEADROOM",
        "exposure_headroom_snapshot": {
            "directional_headroom_notional": 40000.456789,
            "limiting_headroom_notional": 40000.456789,
        },
        "debug_payload": {
            "requested_notional": 100000.123456,
            "resized_notional": 40000.456789,
            "headroom": {"directional_headroom_notional": 40000.456789},
            "requested_exposure_limit_codes": ["DIRECTIONAL_BIAS_LIMIT_REACHED"],
            "final_exposure_limit_codes": [],
            "lead_market_context": {
                "lead_context_status": "unavailable",
                "missing_lead_symbols": ["BTCUSDT", "ETHUSDT"],
                "reason_codes": ["LEAD_CONTEXT_UNAVAILABLE"],
            },
        },
        "snapshot_id": 777,
        "cycle_id": "cycle-777",
        "as_of": as_of.isoformat(),
    }
    risk_row = RiskCheck(
        symbol="BTCUSDT",
        decision_run_id=111,
        market_snapshot_id=222,
        allowed=True,
        decision="long",
        reason_codes=["POSITION_STATE_STALE"],
        approved_risk_pct=0.0,
        approved_leverage=0.0,
        payload=risk_payload,
    )
    db_session.add(risk_row)
    db_session.flush()

    payload = get_operator_dashboard(db_session)
    btc = next(item for item in payload.symbols if item.symbol == "BTCUSDT")

    assert btc.blocked_reasons == []
    assert btc.risk_guard.reason_codes == []
    assert btc.risk_guard.blocked_reason_codes == []
    assert btc.risk_guard.adjustment_reason_codes == ["ENTRY_AUTO_RESIZED", "ENTRY_CLAMPED_TO_DIRECTIONAL_LIMIT"]
    assert btc.risk_guard.degraded_reason_codes == ["PROTECTION_REQUIRED"]
    assert btc.risk_guard.protection_reason_codes == ["PROTECTION_REQUIRED", "MISSING_PROTECTIVE_ORDERS"]
    assert btc.risk_guard.survival_path == "protective_recovery"
    assert btc.risk_guard.approved_risk_pct == 0.004321
    assert btc.risk_guard.approved_leverage == 1.25
    assert btc.risk_guard.approved_projected_notional == 40000.456789
    assert btc.risk_guard.approved_quantity == 0.617283
    assert btc.risk_guard.exposure_headroom_snapshot == risk_payload["exposure_headroom_snapshot"]
    assert btc.risk_guard.debug_payload == risk_payload["debug_payload"]
    assert btc.risk_guard.debug_payload["lead_market_context"]["lead_context_status"] == "unavailable"
    assert btc.risk_guard.snapshot_id == 777
    assert btc.risk_guard.cycle_id == "cycle-777"
    assert btc.risk_guard.as_of == as_of
    assert btc.risk_guard.current_cycle_result["approved_projected_notional"] == 40000.456789
    assert btc.risk_guard.current_cycle_result["approved_quantity"] == 0.617283
    assert btc.risk_guard.current_cycle_result["snapshot_id"] == 777
    assert btc.risk_guard.current_cycle_result["cycle_id"] == "cycle-777"
    assert btc.risk_guard.current_cycle_result["as_of"] == as_of
    assert btc.risk_guard.current_cycle_result["survival_path"] == "protective_recovery"
