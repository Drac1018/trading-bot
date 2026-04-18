from __future__ import annotations

from datetime import timedelta

from trading_mvp.models import AgentRun, Position
from trading_mvp.services.orchestrator import TradingOrchestrator
from trading_mvp.services.runtime_state import PROTECTION_REQUIRED_STATE, mark_sync_success
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive


def _mark_sync_fresh(settings_row) -> None:
    now = utcnow_naive()
    for scope in ("account", "positions", "open_orders", "protective_orders"):
        mark_sync_success(settings_row, scope=scope, synced_at=now)


def _seed_open_position(
    db_session,
    *,
    symbol: str = "BTCUSDT",
    holding_profile: str = "scalp",
    mark_price: float = 70100.0,
) -> Position:
    row = Position(
        symbol=symbol,
        mode="live",
        side="long",
        status="open",
        quantity=0.01,
        entry_price=70000.0,
        mark_price=mark_price,
        leverage=2.0,
        stop_loss=69400.0,
        take_profit=71200.0,
        metadata_json={
            "position_management": {
                "holding_profile": holding_profile,
                "holding_profile_reason": "test_profile",
                "hard_stop_active": True,
                "stop_widening_allowed": False,
            }
        },
    )
    db_session.add(row)
    db_session.flush()
    return row


def _seed_decision_run(
    db_session,
    *,
    symbol: str,
    created_at,
    trigger_reason: str = "open_position_recheck_due",
) -> AgentRun:
    row = AgentRun(
        role="trading_decision",
        trigger_event="realtime_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="openai",
        summary="seed review state",
        input_payload={
            "market_snapshot": {
                "symbol": symbol,
                "timeframe": "15m",
                "snapshot_time": created_at.isoformat(),
            }
        },
        output_payload={
            "symbol": symbol,
            "timeframe": "15m",
            "decision": "hold",
            "holding_profile": "scalp",
        },
        metadata_json={
            "symbol": symbol,
            "timeframe": "15m",
            "source": "llm",
            "holding_profile": "scalp",
            "ai_context": {
                "strategy_engine": "trend_pullback_engine",
                "holding_profile": "scalp",
                "hard_stop_active": True,
                "stop_widening_allowed": False,
                "composite_regime": {
                    "structure_regime": "trend",
                    "direction_regime": "bullish",
                    "volatility_regime": "normal",
                    "participation_regime": "strong",
                    "derivatives_regime": "neutral",
                    "execution_regime": "normal",
                    "transition_risk": "medium",
                },
                "data_quality": {"data_quality_grade": "complete"},
                "previous_thesis": {"thesis_degrade_detected": False},
            },
            "ai_trigger": {
                "trigger_reason": trigger_reason,
                "symbol": symbol,
                "timeframe": "15m",
                "strategy_engine": "trend_pullback_engine",
                "holding_profile": "scalp",
                "reason_codes": ["OPEN_POSITION_RECHECK_DUE"],
                "trigger_fingerprint": "seed-fingerprint",
                "fingerprint_basis": {},
                "fingerprint_changed_fields": [],
                "last_material_review_at": created_at.isoformat(),
                "triggered_at": created_at.isoformat(),
            },
            "last_material_review_at": created_at.isoformat(),
        },
        schema_valid=True,
        started_at=created_at,
        completed_at=created_at,
        created_at=created_at,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _prepare_same_fingerprint_state(
    db_session,
    *,
    created_at,
    monkeypatch=None,
    protection_runtime_state: dict[str, object] | None = None,
) -> tuple[AgentRun, Position]:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_sync_fresh(settings_row)
    db_session.add(settings_row)
    db_session.flush()

    position_row = _seed_open_position(db_session)
    decision_row = _seed_decision_run(db_session, symbol="BTCUSDT", created_at=created_at)

    if protection_runtime_state is not None and monkeypatch is not None:
        import trading_mvp.services.orchestrator as orchestrator_module

        monkeypatch.setattr(
            orchestrator_module,
            "summarize_runtime_state",
            lambda _settings_row: dict(protection_runtime_state),
        )

    plan = TradingOrchestrator(db_session).build_interval_decision_plan(symbols=["BTCUSDT"])
    trigger = plan["plans"][0]["trigger"]
    decision_row.metadata_json = {
        **decision_row.metadata_json,
        "ai_trigger": {
            **decision_row.metadata_json["ai_trigger"],
            **trigger,
        },
        "last_material_review_at": created_at.isoformat(),
    }
    db_session.add(decision_row)
    db_session.flush()
    return decision_row, position_row


def test_same_state_dedupes(db_session) -> None:
    created_at = utcnow_naive() - timedelta(minutes=16)
    _prepare_same_fingerprint_state(db_session, created_at=created_at)

    plan = TradingOrchestrator(db_session).build_interval_decision_plan(symbols=["BTCUSDT"])
    symbol_plan = plan["plans"][0]

    assert symbol_plan["trigger"]["trigger_reason"] == "open_position_recheck_due"
    assert symbol_plan["trigger_deduped"] is True
    assert symbol_plan["dedupe_reason"] == "OPEN_POSITION_FINGERPRINT_UNCHANGED"
    assert symbol_plan["fingerprint_changed_fields"] == []
    assert symbol_plan["trigger"]["fingerprint_changed_fields"] == []
    assert symbol_plan["forced_review_reason"] is None
    assert symbol_plan["last_material_review_at"] == created_at.isoformat()


def test_material_state_change_rechecks(db_session) -> None:
    created_at = utcnow_naive() - timedelta(minutes=16)
    _, position_row = _prepare_same_fingerprint_state(db_session, created_at=created_at)
    position_row.mark_price = 69320.0
    db_session.add(position_row)
    db_session.flush()

    plan = TradingOrchestrator(db_session).build_interval_decision_plan(symbols=["BTCUSDT"])
    symbol_plan = plan["plans"][0]

    assert symbol_plan["trigger"]["trigger_reason"] == "open_position_recheck_due"
    assert symbol_plan["trigger_deduped"] is False
    assert "position_state_bucket" in symbol_plan["fingerprint_changed_fields"]
    assert "position_state_bucket" in symbol_plan["trigger"]["fingerprint_changed_fields"]
    assert symbol_plan["forced_review_reason"] is None


def test_same_fingerprint_forces_review_after_max_age(db_session) -> None:
    created_at = utcnow_naive() - timedelta(minutes=50)
    _prepare_same_fingerprint_state(db_session, created_at=created_at)

    plan = TradingOrchestrator(db_session).build_interval_decision_plan(symbols=["BTCUSDT"])
    symbol_plan = plan["plans"][0]

    assert symbol_plan["trigger"]["trigger_reason"] == "open_position_recheck_due"
    assert symbol_plan["trigger_deduped"] is False
    assert symbol_plan["forced_review_reason"] == "OPEN_POSITION_MAX_REVIEW_AGE_EXCEEDED"
    assert symbol_plan["trigger"]["forced_review_reason"] == "OPEN_POSITION_MAX_REVIEW_AGE_EXCEEDED"
    assert symbol_plan["fingerprint_changed_fields"] == []


def test_protection_review_is_not_delayed_by_dedupe(monkeypatch, db_session) -> None:
    created_at = utcnow_naive() - timedelta(minutes=16)
    protection_runtime_state = {
        "operating_state": PROTECTION_REQUIRED_STATE,
        "protection_recovery_status": "active",
        "protection_recovery_active": True,
        "missing_protection_symbols": ["BTCUSDT"],
    }
    _prepare_same_fingerprint_state(
        db_session,
        created_at=created_at,
        monkeypatch=monkeypatch,
        protection_runtime_state=protection_runtime_state,
    )

    plan = TradingOrchestrator(db_session).build_interval_decision_plan(symbols=["BTCUSDT"])
    symbol_plan = plan["plans"][0]

    assert symbol_plan["trigger"]["trigger_reason"] == "protection_review_event"
    assert symbol_plan["trigger_deduped"] is False
    assert symbol_plan["dedupe_reason"] is None
