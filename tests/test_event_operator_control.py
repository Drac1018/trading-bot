from __future__ import annotations

from datetime import timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from trading_mvp.main import app
from trading_mvp.models import AuditEvent, PnLSnapshot, Position
from trading_mvp.schemas import (
    AIEventViewPayload,
    ManualNoTradeWindowPayload,
    ManualNoTradeWindowRequest,
    ManualNoTradeWindowScopePayload,
    OperatorEventViewPayload,
    OperatorEventViewRequest,
    TradeDecision,
)
from trading_mvp.services.audit import compact_audit_payload
from trading_mvp.services.dashboard import get_operator_dashboard
from trading_mvp.services.event_context import normalize_operator_event_context
from trading_mvp.services.event_policy import derive_ai_event_view, evaluate_event_alignment, no_trade_window_is_active
from trading_mvp.services.market_data import build_market_snapshot
from trading_mvp.services.risk import evaluate_risk
from trading_mvp.services.settings import (
    build_event_operator_control_payload,
    clear_operator_event_view,
    create_manual_no_trade_window,
    end_manual_no_trade_window,
    get_or_create_settings,
    serialize_settings_view,
    update_manual_no_trade_window,
    upsert_operator_event_view,
)
from trading_mvp.time_utils import utcnow_aware, utcnow_naive


def _operator_event_view_request(**overrides: object) -> OperatorEventViewRequest:
    now = utcnow_aware()
    payload = {
        "operator_bias": "bearish",
        "operator_risk_state": "risk_off",
        "applies_to_symbols": ["BTCUSDT"],
        "horizon": "event-day",
        "valid_from": now.isoformat(),
        "valid_to": (now + timedelta(hours=2)).isoformat(),
        "enforcement_mode": "approval_required",
        "note": "Wait for event resolution.",
        "created_by": "operator-ui",
    }
    payload.update(overrides)
    return OperatorEventViewRequest(**payload)


def _manual_window_request(**overrides: object) -> ManualNoTradeWindowRequest:
    now = utcnow_aware()
    payload = {
        "scope": {"scope_type": "symbols", "symbols": ["BTCUSDT"]},
        "start_at": now.isoformat(),
        "end_at": (now + timedelta(hours=3)).isoformat(),
        "reason": "manual no-trade around macro event",
        "auto_resume": True,
        "require_manual_rearm": False,
        "created_by": "operator-ui",
    }
    payload.update(overrides)
    return ManualNoTradeWindowRequest(**payload)


def _risk_entry_decision(*, reference_price: float = 66547.8, **overrides: object) -> TradeDecision:
    payload = {
        "decision": "long",
        "confidence": 0.72,
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "entry_zone_min": round(reference_price * 0.9999, 6),
        "entry_zone_max": round(reference_price * 1.0001, 6),
        "entry_mode": "immediate",
        "invalidation_price": round(reference_price * 0.995, 6),
        "max_chase_bps": 100.0,
        "idea_ttl_minutes": 15,
        "stop_loss": round(reference_price * 0.995, 6),
        "take_profit": round(reference_price * 1.01, 6),
        "max_holding_minutes": 120,
        "risk_pct": 0.02,
        "leverage": 5.0,
        "rationale_codes": ["TEST", "PENDING_ENTRY_PLAN_TRIGGERED"],
        "scenario_note": None,
        "confidence_penalty_reason": None,
        "event_risk_acknowledgement": None,
        "explanation_short": "event policy risk test",
        "explanation_detailed": "Risk guard event policy regression test.",
    }
    payload.update(overrides)
    return TradeDecision(**payload)


def _seed_pnl_snapshot(db_session, *, equity: float = 20000.0) -> None:
    created_at = utcnow_naive() - timedelta(minutes=1)
    db_session.add(
        PnLSnapshot(
            snapshot_date=created_at.date(),
            equity=equity,
            cash_balance=equity,
            wallet_balance=equity,
            available_balance=equity,
            gross_realized_pnl=0.0,
            fee_total=0.0,
            funding_total=0.0,
            net_pnl=0.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            daily_pnl=0.0,
            cumulative_pnl=0.0,
            consecutive_losses=0,
            created_at=created_at,
            updated_at=created_at,
        )
    )


def test_event_operator_requests_require_timezone_aware_datetimes() -> None:
    with pytest.raises(ValidationError):
        OperatorEventViewRequest(
            operator_bias="neutral",
            operator_risk_state="neutral",
            applies_to_symbols=[],
            horizon="event-day",
            valid_from="2026-04-20T10:00:00",
            valid_to="2026-04-20T12:00:00",
            enforcement_mode="observe_only",
            note=None,
            created_by="operator-ui",
        )

    with pytest.raises(ValidationError):
        ManualNoTradeWindowRequest(
            scope={"scope_type": "symbols", "symbols": []},
            start_at="2026-04-20T10:00:00Z",
            end_at="2026-04-20T11:00:00Z",
            reason="invalid empty symbols scope",
            auto_resume=False,
            require_manual_rearm=False,
            created_by="operator-ui",
        )

    request = _operator_event_view_request()
    assert request.valid_from is not None
    assert request.valid_from.tzinfo is not None
    assert request.valid_to is not None
    assert request.valid_to.tzinfo is not None


@pytest.mark.parametrize(
    ("source_status", "is_stale", "is_complete"),
    [
        ("unavailable", False, False),
        ("stale", True, True),
        ("incomplete", False, False),
        ("error", False, False),
    ],
)
def test_normalize_operator_event_context_exposes_source_health(
    source_status: str,
    is_stale: bool,
    is_complete: bool,
) -> None:
    now = utcnow_aware()
    payload = normalize_operator_event_context(
        {
            "source_status": source_status,
            "generated_at": now.isoformat(),
            "is_stale": is_stale,
            "is_complete": is_complete,
            "next_event_name": "FOMC" if source_status != "unavailable" else None,
            "next_event_at": (now + timedelta(minutes=30)).isoformat() if source_status != "unavailable" else None,
            "next_event_importance": "critical" if source_status == "error" else "high",
            "minutes_to_next_event": 30 if source_status != "unavailable" else None,
            "active_risk_window": source_status == "stale",
            "affected_assets": ["BTCUSDT"],
        },
        generated_at=now,
        summary_note="fixture preview",
    )

    assert payload.source_status == source_status
    assert payload.is_stale is is_stale
    assert payload.is_complete is is_complete


def test_alignment_and_effective_policy_preview_rules() -> None:
    now = utcnow_aware()
    ai_view = AIEventViewPayload(
        ai_bias="bullish",
        ai_risk_state="risk_on",
        ai_confidence=0.72,
        scenario_note="event aware",
        confidence_penalty_reason="EVENT_WINDOW_PROXIMITY",
        source_state="available",
    )
    operator_view = OperatorEventViewPayload(
        operator_bias="bearish",
        operator_risk_state="risk_off",
        applies_to_symbols=["BTCUSDT"],
        horizon="event-day",
        valid_from=now - timedelta(minutes=5),
        valid_to=now + timedelta(minutes=30),
        enforcement_mode="block_on_conflict",
        note="Block until event resolves.",
        created_by="operator-ui",
        updated_at=now,
    )

    conflict = evaluate_event_alignment(
        symbol="BTCUSDT",
        ai_event_view=ai_view,
        operator_event_view=operator_view,
        manual_no_trade_windows=[],
        evaluated_at=now,
    )
    assert conflict.alignment_status == "conflict"
    assert conflict.effective_policy_preview == "block_new_entries"
    assert "bias_conflict" in conflict.reason_codes
    assert "block_on_conflict_preview" in conflict.reason_codes

    manual_window = ManualNoTradeWindowPayload(
        window_id="ntw_active",
        scope=ManualNoTradeWindowScopePayload(scope_type="symbols", symbols=["BTCUSDT"]),
        start_at=now - timedelta(minutes=1),
        end_at=now + timedelta(minutes=59),
        reason="manual no-trade",
        auto_resume=True,
        require_manual_rearm=False,
        created_by="operator-ui",
        updated_at=now,
        is_active=True,
    )
    forced = evaluate_event_alignment(
        symbol="BTCUSDT",
        ai_event_view=ai_view,
        operator_event_view=operator_view,
        manual_no_trade_windows=[manual_window],
        evaluated_at=now,
    )
    assert forced.effective_policy_preview == "force_no_trade_window"
    assert "manual_no_trade_active" in forced.reason_codes

    outside_window = evaluate_event_alignment(
        symbol="BTCUSDT",
        ai_event_view=ai_view,
        operator_event_view=operator_view.model_copy(
            update={
                "valid_from": now + timedelta(hours=1),
                "valid_to": now + timedelta(hours=2),
                "enforcement_mode": "approval_required",
            }
        ),
        manual_no_trade_windows=[],
        evaluated_at=now,
    )
    assert outside_window.alignment_status == "insufficient_data"
    assert outside_window.effective_policy_preview == "insufficient_data"
    assert "outside_valid_window" in outside_window.reason_codes


def test_manual_window_active_state_is_start_inclusive_end_exclusive() -> None:
    now = utcnow_aware()
    window = ManualNoTradeWindowPayload(
        window_id="ntw_bounds",
        scope=ManualNoTradeWindowScopePayload(scope_type="global", symbols=[]),
        start_at=now,
        end_at=now + timedelta(minutes=15),
        reason="bounds",
        auto_resume=False,
        require_manual_rearm=False,
        created_by="operator-ui",
        updated_at=now,
        is_active=False,
    )

    assert no_trade_window_is_active(window, symbol="BTCUSDT", now=now) is True
    assert no_trade_window_is_active(window, symbol="BTCUSDT", now=now + timedelta(minutes=14, seconds=59)) is True
    assert no_trade_window_is_active(window, symbol="BTCUSDT", now=window.end_at) is False


def test_event_operator_persistence_round_trip_and_audit(db_session) -> None:
    row = get_or_create_settings(db_session)

    operator_request = _operator_event_view_request()
    row, changed = upsert_operator_event_view(db_session, operator_request)
    assert changed is True
    row, changed = upsert_operator_event_view(db_session, operator_request)
    assert changed is False

    window_request = _manual_window_request()
    row, window = create_manual_no_trade_window(db_session, window_request)
    assert window.window_id.startswith("ntw_")

    row, updated_window, changed = update_manual_no_trade_window(
        db_session,
        window_id=window.window_id,
        payload=_manual_window_request(reason="updated no-trade reason"),
    )
    assert changed is True
    assert updated_window.reason == "updated no-trade reason"

    row, ended_window, changed = end_manual_no_trade_window(
        db_session,
        window_id=window.window_id,
        actor="operator-ui",
        end_at=utcnow_aware() + timedelta(hours=1),
    )
    assert changed is True
    assert ended_window.window_id == window.window_id

    row, changed = clear_operator_event_view(db_session, actor="operator-ui")
    assert changed is True

    db_session.commit()

    event_types = list(
        db_session.scalars(select(AuditEvent.event_type).order_by(AuditEvent.id.asc()))
    )
    assert "operator_event_view_created" in event_types
    assert "manual_no_trade_window_created" in event_types
    assert "manual_no_trade_window_updated" in event_types
    assert "manual_no_trade_window_ended" in event_types
    assert "operator_event_view_cleared" in event_types
    assert "alignment_evaluated" in event_types

    latest_alignment_event = db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.event_type == "alignment_evaluated")
        .order_by(AuditEvent.id.desc())
        .limit(1)
    )
    assert latest_alignment_event is not None
    compact = compact_audit_payload(
        latest_alignment_event.payload,
        event_type=latest_alignment_event.event_type,
        event_category="approval_control",
    )
    assert compact["actor"] == "operator-ui"
    assert "evaluations" in compact


def test_settings_view_and_operator_dashboard_expose_event_operator_control(db_session) -> None:
    row = get_or_create_settings(db_session)
    upsert_operator_event_view(db_session, _operator_event_view_request())
    create_manual_no_trade_window(db_session, _manual_window_request())
    db_session.commit()

    settings_payload = serialize_settings_view(row)
    assert settings_payload["default_symbol"] == row.default_symbol.upper()
    assert "event_operator_control" in settings_payload
    assert settings_payload["event_operator_control"]["operator_event_view"]["operator_bias"] == "bearish"

    dashboard = get_operator_dashboard(db_session)
    assert dashboard.symbols
    first_symbol = dashboard.symbols[0]
    assert first_symbol.event_operator_control is not None
    assert first_symbol.market_context_summary is not None


def test_event_operator_control_write_api_and_invalid_time_range(testclient_db_factory) -> None:
    testing_session = testclient_db_factory("event_operator_control_api.db")

    with TestClient(app) as client:
        operator_response = client.put(
            "/api/settings/operator-event-view",
            json={
                "operator_bias": "neutral",
                "operator_risk_state": "neutral",
                "applies_to_symbols": ["BTCUSDT"],
                "horizon": "event-day",
                "valid_from": "2026-04-20T10:00:00Z",
                "valid_to": "2026-04-20T12:00:00Z",
                "enforcement_mode": "observe_only",
                "note": "observe only",
                "created_by": "operator-ui",
            },
        )
        assert operator_response.status_code == 200
        assert operator_response.json()["event_operator_control"]["operator_event_view"]["operator_bias"] == "neutral"

        invalid_window_response = client.post(
            "/api/settings/manual-no-trade-windows",
            json={
                "scope": {"scope_type": "symbols", "symbols": ["BTCUSDT"]},
                "start_at": "2026-04-20T12:00:00Z",
                "end_at": "2026-04-20T11:00:00Z",
                "reason": "invalid range",
                "auto_resume": False,
                "require_manual_rearm": False,
                "created_by": "operator-ui",
            },
        )
        assert invalid_window_response.status_code == 422

    with testing_session() as session:
        audit_types = list(session.scalars(select(AuditEvent.event_type).order_by(AuditEvent.id.asc())))
        assert "operator_event_view_created" in audit_types


def test_risk_guard_blocks_entry_for_manual_no_trade_window_and_audits(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _seed_pnl_snapshot(db_session)
    create_manual_no_trade_window(db_session, _manual_window_request())
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        _risk_entry_decision(reference_price=snapshot.latest_price),
        snapshot,
        execution_mode="historical_replay",
    )
    db_session.flush()

    assert result.allowed is False
    assert result.blocked_reason == "manual_no_trade_active"
    assert result.policy_source == "manual_no_trade_window"
    assert result.evaluated_operator_policy is not None
    assert result.evaluated_operator_policy.matched_window_id is not None
    assert "manual_no_trade_active" in result.blocked_reason_codes

    audit_types = list(db_session.scalars(select(AuditEvent.event_type).order_by(AuditEvent.id.asc())))
    assert "event_policy_blocked_entry" in audit_types


@pytest.mark.parametrize(
    ("request_overrides", "expected_blocked_reason"),
    [
        (
            {
                "operator_bias": "neutral",
                "operator_risk_state": "neutral",
                "enforcement_mode": "force_no_trade",
                "note": "force no-trade window",
            },
            "operator_force_no_trade",
        ),
        (
            {
                "operator_bias": "no_trade",
                "operator_risk_state": "risk_off",
                "enforcement_mode": "observe_only",
                "note": "operator says no trade",
            },
            "operator_bias_no_trade",
        ),
    ],
)
def test_risk_guard_blocks_entry_for_operator_no_trade_controls(
    db_session,
    request_overrides: dict[str, object],
    expected_blocked_reason: str,
) -> None:
    settings_row = get_or_create_settings(db_session)
    _seed_pnl_snapshot(db_session)
    upsert_operator_event_view(db_session, _operator_event_view_request(**request_overrides))
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        _risk_entry_decision(reference_price=snapshot.latest_price, scenario_note="event aware"),
        snapshot,
        execution_mode="historical_replay",
    )

    assert result.allowed is False
    assert result.blocked_reason == expected_blocked_reason
    assert result.approval_required_reason is None
    assert result.evaluated_operator_policy is not None


def test_risk_guard_blocks_conflict_under_block_on_conflict_and_matches_preview(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _seed_pnl_snapshot(db_session)
    now = utcnow_aware()
    upsert_operator_event_view(
        db_session,
        _operator_event_view_request(
            operator_bias="bearish",
            operator_risk_state="risk_off",
            enforcement_mode="block_on_conflict",
            valid_from=(now - timedelta(minutes=5)).isoformat(),
            valid_to=(now + timedelta(minutes=30)).isoformat(),
            note="block on conflict",
        ),
    )
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)
    decision = _risk_entry_decision(reference_price=snapshot.latest_price, scenario_note="event aware")
    preview = build_event_operator_control_payload(
        session=db_session,
        settings_row=settings_row,
        symbol=decision.symbol,
        timeframe=decision.timeframe,
        ai_event_view=derive_ai_event_view(output_payload=decision.model_dump(mode="json")),
        evaluated_at=now,
    )

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        decision,
        snapshot,
        execution_mode="historical_replay",
    )

    assert result.allowed is False
    assert result.blocked_reason == "alignment_conflict_block"
    assert result.policy_source == "alignment_policy"
    assert result.evaluated_operator_policy is not None
    assert preview.effective_policy_preview == result.evaluated_operator_policy.effective_policy_preview
    assert preview.alignment_decision.alignment_status == result.evaluated_operator_policy.alignment_status
    assert preview.policy_source == result.policy_source


def test_risk_guard_requires_approval_when_alignment_not_aligned(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _seed_pnl_snapshot(db_session)
    upsert_operator_event_view(
        db_session,
        _operator_event_view_request(
            operator_bias="bullish",
            operator_risk_state="risk_off",
            enforcement_mode="approval_required",
            note="approval required when not aligned",
        ),
    )
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        _risk_entry_decision(reference_price=snapshot.latest_price, scenario_note="event aware"),
        snapshot,
        execution_mode="historical_replay",
    )
    db_session.flush()

    assert result.allowed is False
    assert result.blocked_reason is None
    assert result.approval_required_reason == "alignment_not_aligned"
    assert result.policy_source == "alignment_policy"
    assert "alignment_not_aligned" in result.blocked_reason_codes

    audit_types = list(db_session.scalars(select(AuditEvent.event_type).order_by(AuditEvent.id.asc())))
    assert "event_policy_required_approval" in audit_types


def test_risk_guard_requires_approval_when_alignment_is_insufficient_data(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _seed_pnl_snapshot(db_session)
    upsert_operator_event_view(
        db_session,
        _operator_event_view_request(
            operator_bias="bullish",
            operator_risk_state="risk_on",
            enforcement_mode="approval_required",
            note="approval required with missing AI view",
        ),
    )
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        _risk_entry_decision(reference_price=snapshot.latest_price),
        snapshot,
        execution_mode="historical_replay",
    )

    assert result.allowed is False
    assert result.approval_required_reason == "alignment_insufficient_data"
    assert result.blocked_reason is None
    assert result.policy_source == "alignment_policy"


def test_risk_guard_skips_block_on_conflict_when_alignment_is_insufficient_data(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _seed_pnl_snapshot(db_session)
    upsert_operator_event_view(
        db_session,
        _operator_event_view_request(
            operator_bias="bearish",
            operator_risk_state="risk_off",
            enforcement_mode="block_on_conflict",
            note="do not auto-block on insufficient data",
        ),
    )
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        _risk_entry_decision(reference_price=snapshot.latest_price),
        snapshot,
        execution_mode="historical_replay",
    )
    db_session.flush()

    assert result.allowed is True
    assert result.blocked_reason is None
    assert result.approval_required_reason is None
    assert result.degraded_reason is not None
    assert result.evaluated_operator_policy is not None
    assert result.evaluated_operator_policy.alignment_status == "insufficient_data"

    audit_types = list(db_session.scalars(select(AuditEvent.event_type).order_by(AuditEvent.id.asc())))
    assert "event_policy_skipped_due_to_missing_data" in audit_types


def test_risk_guard_allows_reduce_path_under_manual_no_trade_window(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _seed_pnl_snapshot(db_session)
    create_manual_no_trade_window(db_session, _manual_window_request())
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        _risk_entry_decision(
            reference_price=snapshot.latest_price,
            decision="reduce",
            entry_mode="none",
            rationale_codes=["TEST"],
            scenario_note="event aware",
            explanation_short="reduce allowed",
            explanation_detailed="Reduce path should remain allowed under manual no-trade policy.",
        ),
        snapshot,
        execution_mode="historical_replay",
    )
    db_session.flush()

    assert result.allowed is True
    assert result.blocked_reason is None
    assert result.policy_source == "manual_no_trade_window"
    assert "manual_no_trade_active" not in result.blocked_reason_codes

    audit_types = list(db_session.scalars(select(AuditEvent.event_type).order_by(AuditEvent.id.asc())))
    assert "event_policy_allowed_survival_path" in audit_types


def test_risk_guard_allows_protective_recovery_under_force_no_trade(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _seed_pnl_snapshot(db_session)
    settings_row.pause_reason_detail = {
        "operating_state": "PROTECTION_REQUIRED",
        "protection_recovery": {
            "status": "recreating",
            "auto_recovery_active": True,
            "symbol_states": {
                "BTCUSDT": {
                    "state": "PROTECTION_REQUIRED",
                    "missing_components": ["take_profit"],
                    "failure_count": 1,
                }
            },
        },
    }
    db_session.add(settings_row)
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.01,
            entry_price=70000.0,
            mark_price=70100.0,
            leverage=2.0,
            stop_loss=69000.0,
            take_profit=72000.0,
        )
    )
    upsert_operator_event_view(
        db_session,
        _operator_event_view_request(
            operator_bias="neutral",
            operator_risk_state="risk_off",
            enforcement_mode="force_no_trade",
            note="force no-trade should not block protection recovery",
        ),
    )
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        _risk_entry_decision(
            reference_price=snapshot.latest_price,
            stop_loss=69000.0,
            take_profit=72000.0,
            scenario_note="event aware",
        ),
        snapshot,
        execution_mode="historical_replay",
    )

    assert result.allowed is True
    assert result.blocked_reason is None
    assert result.policy_source == "operator_enforcement_mode"


def test_risk_guard_honors_timezone_aware_operator_window_boundaries(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _seed_pnl_snapshot(db_session)
    now_kst = utcnow_aware().astimezone(timezone(timedelta(hours=9)))
    upsert_operator_event_view(
        db_session,
        _operator_event_view_request(
            operator_bias="no_trade",
            operator_risk_state="risk_off",
            enforcement_mode="observe_only",
            valid_from=now_kst.isoformat(),
            valid_to=(now_kst + timedelta(minutes=30)).isoformat(),
            note="active in KST window",
        ),
    )
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140)

    active_result, _ = evaluate_risk(
        db_session,
        settings_row,
        _risk_entry_decision(reference_price=snapshot.latest_price, scenario_note="event aware"),
        snapshot,
        execution_mode="historical_replay",
    )
    assert active_result.blocked_reason == "operator_bias_no_trade"

    upsert_operator_event_view(
        db_session,
        _operator_event_view_request(
            operator_bias="no_trade",
            operator_risk_state="risk_off",
            enforcement_mode="observe_only",
            valid_from=(now_kst - timedelta(minutes=30)).isoformat(),
            valid_to=now_kst.isoformat(),
            note="expired at boundary",
        ),
    )

    expired_result, _ = evaluate_risk(
        db_session,
        settings_row,
        _risk_entry_decision(reference_price=snapshot.latest_price, scenario_note="event aware"),
        snapshot,
        execution_mode="historical_replay",
    )
    assert expired_result.blocked_reason is None
    assert expired_result.allowed is True
