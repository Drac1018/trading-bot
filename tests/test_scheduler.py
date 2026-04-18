from __future__ import annotations

from datetime import timedelta

import pytest
from trading_mvp.models import AgentRun, Position, SchedulerRun
from trading_mvp.services.orchestrator import TradingOrchestrator
from trading_mvp.services.runtime_state import mark_sync_success
from trading_mvp.services.scheduler import get_due_position_management_symbols
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive


def _mark_sync_fresh(settings_row) -> None:
    now = utcnow_naive()
    for scope in ("account", "positions", "open_orders", "protective_orders"):
        mark_sync_success(settings_row, scope=scope, synced_at=now)


def _seed_decision_run(db_session, *, symbol: str, created_at, trigger_reason: str = "manual_review_event") -> None:
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
            metadata_json={
                "symbol": symbol,
                "timeframe": "15m",
                "source": "llm",
                "ai_trigger": {
                    "trigger_reason": trigger_reason,
                    "trigger_fingerprint": f"{symbol.lower()}-seed",
                },
            },
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
