from __future__ import annotations

from datetime import timedelta

import pytest
from trading_mvp.models import AgentRun, Position, SchedulerRun
from trading_mvp.services.orchestrator import TradingOrchestrator
from trading_mvp.services.runtime_state import mark_sync_success
from trading_mvp.services.scheduler import (
    get_due_interval_decision_symbols,
    get_due_position_management_symbols,
    run_interval_decision_cycle,
)
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive


def _mark_sync_fresh(settings_row) -> None:
    now = utcnow_naive()
    for scope in ("account", "positions", "open_orders", "protective_orders"):
        mark_sync_success(settings_row, scope=scope, synced_at=now)


def _seed_decision_run(
    db_session,
    *,
    symbol: str,
    created_at,
    trigger_reason: str = "manual_review_event",
    metadata_overrides: dict[str, object] | None = None,
) -> None:
    metadata = {
        "symbol": symbol,
        "timeframe": "15m",
        "source": "llm",
        "ai_trigger": {
            "trigger_reason": trigger_reason,
            "trigger_fingerprint": f"{symbol.lower()}-seed",
        },
    }
    if isinstance(metadata_overrides, dict):
        metadata.update(metadata_overrides)
    db_session.add(
        AgentRun(
            role="trading_decision",
            trigger_event="manual",
            schema_name="TradeDecision",
            status="completed",
            provider_name="openai",
            summary="seed decision",
            input_payload={
                "market_snapshot": {
                    "symbol": symbol,
                    "timeframe": "15m",
                    "snapshot_time": created_at.isoformat(),
                }
            },
            output_payload={"symbol": symbol, "timeframe": "15m", "decision": "hold"},
            metadata_json=metadata,
            schema_valid=True,
            started_at=created_at,
            completed_at=created_at,
            created_at=created_at,
        )
    )
    db_session.flush()


@pytest.mark.parametrize(
    ("holding_profile", "expected_minutes"),
    [
        ("scalp", 15),
        ("swing", 20),
        ("position", 30),
    ],
)
def test_scalp_swing_position_cadence_hint_consumption(db_session, holding_profile: str, expected_minutes: int) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_sync_fresh(settings_row)
    db_session.add(settings_row)
    db_session.flush()

    last_decision_at = utcnow_naive() - timedelta(minutes=expected_minutes + 1)
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
    )
    db_session.flush()
    _seed_decision_run(db_session, symbol="BTCUSDT", created_at=last_decision_at)

    plan = TradingOrchestrator(db_session).build_interval_decision_plan(symbols=["BTCUSDT"])
    symbol_plan = plan["plans"][0]

    assert symbol_plan["trigger"]["trigger_reason"] == "open_position_recheck_due"
    expected_due_at = last_decision_at + timedelta(minutes=expected_minutes)
    assert symbol_plan["next_ai_review_due_at"] == expected_due_at.isoformat()
    assert symbol_plan["applied_review_cadence_minutes"] == expected_minutes
    assert symbol_plan["review_cadence_source"] == "holding_profile_cadence_hint"
    assert symbol_plan["holding_profile_cadence_hint"]["holding_profile"] == holding_profile
    assert symbol_plan["max_review_age_minutes"] == expected_minutes * 3


def test_interval_scheduler_due_uses_latest_decision_holding_profile_hint(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_sync_fresh(settings_row)
    db_session.add(settings_row)
    db_session.flush()

    now = utcnow_naive()
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
            stop_loss=69400.0,
            take_profit=71200.0,
            metadata_json={
                "position_management": {
                    "holding_profile_reason": "missing_live_profile",
                    "hard_stop_active": True,
                    "stop_widening_allowed": False,
                }
            },
        )
    )
    db_session.add(
        SchedulerRun(
            schedule_window="20m",
            workflow="interval_decision_cycle",
            status="success",
            triggered_by="scheduler",
            created_at=now - timedelta(minutes=21),
            next_run_at=None,
            outcome={"symbol": "BTCUSDT"},
        )
    )
    db_session.flush()
    _seed_decision_run(
        db_session,
        symbol="BTCUSDT",
        created_at=now - timedelta(minutes=21),
        metadata_overrides={"holding_profile": "swing"},
    )

    assert get_due_interval_decision_symbols(db_session) == ["BTCUSDT"]


def test_invalid_missing_cadence_hint_falls_back_to_effective_ai_cadence(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_sync_fresh(settings_row)
    db_session.add(settings_row)
    db_session.flush()

    last_decision_at = utcnow_naive() - timedelta(minutes=10)
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.01,
            entry_price=70000.0,
            mark_price=69980.0,
            leverage=2.0,
            stop_loss=69400.0,
            take_profit=71200.0,
            metadata_json={"position_management": {}},
        )
    )
    db_session.flush()
    _seed_decision_run(db_session, symbol="BTCUSDT", created_at=last_decision_at)

    original_get_symbol_cadence_profile = TradingOrchestrator.get_symbol_cadence_profile

    def fake_get_symbol_cadence_profile(self, **kwargs):  # noqa: ANN001
        profile = original_get_symbol_cadence_profile(self, **kwargs)
        profile["active_holding_profile"] = None
        profile["holding_profile_cadence_hint"] = {}
        profile["effective_cadence"] = {
            **dict(profile.get("effective_cadence") or {}),
            "decision_cycle_interval_minutes": 9,
            "ai_call_interval_minutes": 9,
        }
        return profile

    monkeypatch.setattr(TradingOrchestrator, "get_symbol_cadence_profile", fake_get_symbol_cadence_profile)

    plan = TradingOrchestrator(db_session).build_interval_decision_plan(symbols=["BTCUSDT"])
    symbol_plan = plan["plans"][0]

    assert symbol_plan["applied_review_cadence_minutes"] == 9
    assert symbol_plan["review_cadence_source"] == "effective_ai_call_interval_minutes"
    assert symbol_plan["cadence_fallback_reason"] == "HOLDING_PROFILE_CADENCE_HINT_MISSING"
    assert symbol_plan["next_ai_review_due_at"] == (last_decision_at + timedelta(minutes=9)).isoformat()


def test_periodic_backstop_due(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    settings_row.symbol_cadence_overrides = [
        {
            "symbol": "BTCUSDT",
            "ai_backstop_interval_minutes_override": 30,
        }
    ]
    db_session.add(settings_row)
    db_session.flush()

    last_decision_at = utcnow_naive() - timedelta(minutes=31)
    _seed_decision_run(db_session, symbol="BTCUSDT", created_at=last_decision_at)
    monkeypatch.setattr(
        TradingOrchestrator,
        "_rank_candidate_symbols",
        lambda self, **kwargs: {
            "mode": "portfolio_rotation_top_n",
            "breadth_regime": "mixed",
            "breadth_summary": {"breadth_regime": "mixed"},
            "selected_symbols": [],
            "skipped_symbols": ["BTCUSDT"],
            "rankings": [
                {
                    "symbol": "BTCUSDT",
                    "selected": False,
                    "selection_reason": "score_below_threshold",
                    "rejected_reason": "score_below_threshold",
                    "entry_mode": "pullback_confirm",
                    "strategy_engine": "trend_pullback_engine",
                    "holding_profile": "scalp",
                    "holding_profile_reason": "test_holding_profile",
                    "holding_profile_context": {
                        "holding_profile": "scalp",
                        "holding_profile_reason": "test_holding_profile",
                        "cadence_hint": {"decision_interval_minutes": 15},
                    },
                    "assigned_slot": None,
                    "candidate_weight": 0.0,
                    "candidate": {
                        "symbol": "BTCUSDT",
                        "timeframe": "15m",
                        "decision": "hold",
                        "scenario": "hold",
                        "holding_profile": "scalp",
                        "strategy_engine": "trend_pullback_engine",
                        "rationale_codes": ["TEST_SELECTION"],
                    },
                    "score": {"total_score": 0.2},
                }
            ],
        },
    )

    plan = TradingOrchestrator(db_session).build_interval_decision_plan(symbols=["BTCUSDT"])
    symbol_plan = plan["plans"][0]

    assert symbol_plan["trigger"]["trigger_reason"] == "periodic_backstop_due"


def test_protection_path_not_delayed_by_cadence(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    settings_row.symbol_cadence_overrides = [
        {
            "symbol": "BTCUSDT",
            "position_management_interval_seconds_override": 30,
        }
    ]
    _mark_sync_fresh(settings_row)
    db_session.add(settings_row)
    db_session.flush()

    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.01,
            entry_price=70000.0,
            mark_price=70120.0,
            leverage=2.0,
            stop_loss=69400.0,
            take_profit=71200.0,
            metadata_json={
                "position_management": {
                    "holding_profile": "position",
                    "holding_profile_reason": "test_profile",
                    "hard_stop_active": True,
                    "stop_widening_allowed": False,
                }
            },
        )
    )
    db_session.add(
        SchedulerRun(
            schedule_window="30s",
            workflow="position_management_cycle",
            status="success",
            triggered_by="scheduler",
            created_at=utcnow_naive() - timedelta(seconds=40),
            next_run_at=None,
            outcome={"symbol": "BTCUSDT"},
        )
    )
    db_session.flush()

    assert get_due_position_management_symbols(db_session) == ["BTCUSDT"]


def test_open_position_dedupe_surfaces_reason_fields(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_sync_fresh(settings_row)
    db_session.add(settings_row)
    db_session.flush()

    now = utcnow_naive()
    monkeypatch.setattr(
        TradingOrchestrator,
        "build_interval_decision_plan",
        lambda self, **kwargs: {
            "generated_at": now.isoformat(),
            "candidate_selection": {"rankings": []},
            "plans": [
                {
                    "symbol": "BTCUSDT",
                    "timeframe": "15m",
                    "cadence": {
                        "mode": "active_position",
                        "effective_cadence": {
                            "decision_cycle_interval_minutes": 1,
                            "ai_call_interval_minutes": 15,
                        },
                    },
                    "selection_context": None,
                    "trigger": {
                        "trigger_reason": "open_position_recheck_due",
                        "symbol": "BTCUSDT",
                        "timeframe": "15m",
                        "strategy_engine": "trend_pullback_engine",
                        "holding_profile": "scalp",
                        "reason_codes": ["OPEN_POSITION_RECHECK_DUE"],
                        "trigger_fingerprint": "same-fingerprint",
                        "fingerprint_basis": {"position_state_bucket": "flat"},
                        "fingerprint_changed_fields": [],
                    "last_decision_at": None,
                    "last_material_review_at": (now - timedelta(minutes=15)).isoformat(),
                    "forced_review_reason": None,
                    "applied_review_cadence_minutes": 15,
                    "review_cadence_source": "holding_profile_cadence_hint",
                    "holding_profile_cadence_hint": {
                        "holding_profile": "scalp",
                        "decision_interval_minutes": 15,
                    },
                    "max_review_age_minutes": 45,
                    "triggered_at": now.isoformat(),
                },
                    "trigger_deduped": True,
                    "last_decision_at": None,
                    "last_ai_invoked_at": (now - timedelta(minutes=15)).isoformat(),
                    "last_material_review_at": (now - timedelta(minutes=15)).isoformat(),
                    "next_ai_review_due_at": (now + timedelta(minutes=15)).isoformat(),
                    "applied_review_cadence_minutes": 15,
                    "review_cadence_source": "holding_profile_cadence_hint",
                    "holding_profile_cadence_hint": {
                        "holding_profile": "scalp",
                        "decision_interval_minutes": 15,
                    },
                    "max_review_age_minutes": 45,
                    "fingerprint_changed_fields": [],
                    "dedupe_reason": "OPEN_POSITION_FINGERPRINT_UNCHANGED",
                    "forced_review_reason": None,
                    "last_ai_skip_reason": "TRIGGER_DEDUPED",
                }
            ],
        },
    )
    monkeypatch.setattr(
        TradingOrchestrator,
        "run_decision_cycle",
        lambda self, **kwargs: pytest.fail("deduped open-position trigger must not invoke decision cycle"),
    )

    result = run_interval_decision_cycle(db_session, triggered_by="scheduler")
    outcome = result["results"][0]["outcome"]

    assert outcome["ai_review_status"] == "deduped"
    assert outcome["dedupe_reason"] == "OPEN_POSITION_FINGERPRINT_UNCHANGED"
    assert outcome["fingerprint_changed_fields"] == []
    assert outcome["last_material_review_at"] == (now - timedelta(minutes=15)).isoformat()
    assert outcome["forced_review_reason"] is None
    assert outcome["applied_review_cadence_minutes"] == 15
    assert outcome["review_cadence_source"] == "holding_profile_cadence_hint"


def test_same_fingerprint_dedupe_uses_review_cadence_for_next_due(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_sync_fresh(settings_row)
    db_session.add(settings_row)
    db_session.flush()

    now = utcnow_naive()
    last_decision_at = now - timedelta(minutes=21)
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
            stop_loss=69400.0,
            take_profit=71200.0,
            metadata_json={
                "position_management": {
                    "holding_profile": "swing",
                    "holding_profile_reason": "test_profile",
                    "hard_stop_active": True,
                    "stop_widening_allowed": False,
                }
            },
        )
    )
    db_session.flush()
    _seed_decision_run(
        db_session,
        symbol="BTCUSDT",
        created_at=last_decision_at,
        trigger_reason="open_position_recheck_due",
        metadata_overrides={
            "holding_profile": "swing",
            "last_material_review_at": last_decision_at.isoformat(),
            "ai_trigger": {
                "trigger_reason": "open_position_recheck_due",
                "trigger_fingerprint": "same-fingerprint",
                "last_material_review_at": last_decision_at.isoformat(),
            },
        },
    )
    monkeypatch.setattr(TradingOrchestrator, "_trigger_fingerprint", staticmethod(lambda payload: "same-fingerprint"))

    plan = TradingOrchestrator(db_session).build_interval_decision_plan(
        symbols=["BTCUSDT"],
        triggered_at=now,
    )
    symbol_plan = plan["plans"][0]

    assert symbol_plan["trigger_deduped"] is True
    assert symbol_plan["review_cadence_source"] == "holding_profile_cadence_hint"
    assert symbol_plan["applied_review_cadence_minutes"] == 20
    assert symbol_plan["next_ai_review_due_at"] == (now + timedelta(minutes=20)).isoformat()
