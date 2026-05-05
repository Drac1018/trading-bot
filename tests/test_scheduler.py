from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from trading_mvp.models import AgentRun, MarketSnapshot, Position, SchedulerRun, SystemHealthEvent
from trading_mvp.services.orchestrator import TradingOrchestrator
from trading_mvp.services.runtime_state import build_sync_freshness_summary, mark_sync_success
from trading_mvp.services.scheduler import (
    abandon_stale_scheduler_runs,
    get_due_interval_decision_symbols,
    get_due_position_management_symbols,
    run_due_operational_cycles,
    run_interval_decision_cycle,
    run_release_enrichment_watch_cycle,
)
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive


def _mark_sync_fresh(settings_row) -> None:
    now = utcnow_naive()
    for scope in ("account", "positions", "open_orders", "protective_orders"):
        mark_sync_success(settings_row, scope=scope, synced_at=now)


def test_interval_plan_account_trust_allows_flat_protective_staleness(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    now = utcnow_naive()
    mark_sync_success(settings_row, scope="account", synced_at=now)
    mark_sync_success(settings_row, scope="positions", synced_at=now)
    mark_sync_success(settings_row, scope="open_orders", synced_at=now)
    mark_sync_success(
        settings_row,
        scope="protective_orders",
        synced_at=now - timedelta(seconds=120),
        stale_after_seconds=90,
        status="flat",
    )
    db_session.flush()

    summary = build_sync_freshness_summary(settings_row)

    assert TradingOrchestrator._account_untrusted_sync_reason_codes(summary) == []


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


def _seed_market_snapshot(
    db_session,
    *,
    symbol: str,
    timeframe: str,
    snapshot_time,
    event_name: str,
    event_at,
    release_enrichment: dict[str, dict[str, object]] | None = None,
) -> None:
    enrichment_payload = release_enrichment or {}
    enrichment_vendors = list(enrichment_payload.keys())
    db_session.add(
        MarketSnapshot(
            symbol=symbol,
            timeframe=timeframe,
            snapshot_time=snapshot_time,
            latest_price=70000.0,
            latest_volume=1.0,
            candle_count=60,
            is_stale=False,
            is_complete=True,
            payload={
                "event_context": {
                    "source_status": "external_api",
                    "source_provenance": "external_api",
                    "source_vendor": "fred",
                    "generated_at": snapshot_time.isoformat(),
                    "is_stale": False,
                    "is_complete": True,
                    "next_event_at": event_at.isoformat(),
                    "next_event_name": event_name,
                    "next_event_importance": "high",
                    "minutes_to_next_event": int((event_at - snapshot_time).total_seconds() // 60),
                    "enrichment_vendors": enrichment_vendors,
                    "events": [
                        {
                            "event_at": event_at.isoformat(),
                            "event_name": event_name,
                            "importance": "high",
                            "affected_assets": [symbol],
                            "minutes_to_event": int((event_at - snapshot_time).total_seconds() // 60),
                            "enrichment_vendors": enrichment_vendors,
                            "release_enrichment": enrichment_payload,
                        }
                    ],
                }
            },
        )
    )
    db_session.flush()


def _entry_candidate_interval_plan(
    *,
    now,
    last_ai_skip_reason: str | None = None,
) -> dict[str, object]:
    trigger = {
        "trigger_reason": "entry_candidate_event",
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "strategy_engine": "trend_pullback_engine",
        "holding_profile": "scalp",
        "reason_codes": ["ENTRY_CANDIDATE_SELECTED"],
        "trigger_fingerprint": "entry-candidate-fingerprint",
        "fingerprint_basis": {"position_state_bucket": "flat"},
        "fingerprint_changed_fields": [],
        "last_decision_at": None,
        "last_material_review_at": now.isoformat(),
        "forced_review_reason": None,
        "applied_review_cadence_minutes": 15,
        "review_cadence_source": "holding_profile_cadence_hint",
        "holding_profile_cadence_hint": {"holding_profile": "scalp", "decision_interval_minutes": 15},
        "max_review_age_minutes": 45,
        "triggered_at": now.isoformat(),
    }
    return {
        "generated_at": now.isoformat(),
        "candidate_selection": {"rankings": []},
        "plans": [
            {
                "symbol": "BTCUSDT",
                "timeframe": "15m",
                "cadence": {
                    "mode": "watch",
                    "effective_cadence": {
                        "decision_cycle_interval_minutes": 15,
                        "ai_call_interval_minutes": 15,
                    },
                },
                "selection_context": {"assigned_slot": "slot_1", "candidate_weight": 0.64},
                "trigger": trigger,
                "trigger_deduped": False,
                "last_decision_at": None,
                "last_ai_invoked_at": None,
                "last_material_review_at": now.isoformat(),
                "next_ai_review_due_at": (now + timedelta(minutes=15)).isoformat(),
                "applied_review_cadence_minutes": 15,
                "review_cadence_source": "holding_profile_cadence_hint",
                "holding_profile_cadence_hint": {"holding_profile": "scalp", "decision_interval_minutes": 15},
                "max_review_age_minutes": 45,
                "fingerprint_changed_fields": [],
                "dedupe_reason": None,
                "forced_review_reason": None,
                "last_ai_skip_reason": last_ai_skip_reason,
            }
        ],
    }


def test_run_due_operational_cycles_isolates_background_workflow_failure(monkeypatch, db_session) -> None:
    calls: list[str] = []

    def fail_market_refresh(session, triggered_by="scheduler"):
        calls.append("market")
        raise RuntimeError("market failed")

    def run_position_management(session, triggered_by="scheduler"):
        calls.append("position_management")
        return {"workflow": "position_management_cycle", "results": [{"status": "ok"}]}

    monkeypatch.setattr("trading_mvp.services.scheduler.run_market_refresh_cycle", fail_market_refresh)
    monkeypatch.setattr(
        "trading_mvp.services.scheduler.run_release_enrichment_watch_cycle",
        lambda session, triggered_by="scheduler": {"workflow": "release_enrichment_watch_cycle", "results": []},
    )
    monkeypatch.setattr("trading_mvp.services.scheduler.run_position_management_cycle", run_position_management)
    monkeypatch.setattr("trading_mvp.services.scheduler.run_due_entry_plan_watcher_cycle", lambda session: None)
    monkeypatch.setattr("trading_mvp.services.scheduler.run_due_interval_decision_cycle", lambda session: None)

    result = run_due_operational_cycles(
        db_session,
        include_exchange_sync=False,
        commit_between=True,
        continue_on_error=True,
    )

    health_event = db_session.scalar(select(SystemHealthEvent).order_by(SystemHealthEvent.id.desc()).limit(1))
    assert calls == ["market", "position_management"]
    assert result == [{"workflow": "position_management_cycle", "results": [{"status": "ok"}]}]
    assert health_event is not None
    assert health_event.payload["workflow"] == "market_refresh_cycle"


def test_run_due_operational_cycles_can_use_isolated_sessions(monkeypatch, db_session) -> None:
    calls: list[str] = []
    session_ids: list[int] = []
    SessionFactory = sessionmaker(
        bind=db_session.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    def fail_market_refresh(session, triggered_by="scheduler"):
        calls.append("market")
        session_ids.append(id(session))
        raise RuntimeError("market failed")

    def run_position_management(session, triggered_by="scheduler"):
        calls.append("position_management")
        session_ids.append(id(session))
        return {"workflow": "position_management_cycle", "results": [{"status": "ok"}]}

    monkeypatch.setattr("trading_mvp.services.scheduler.run_market_refresh_cycle", fail_market_refresh)
    monkeypatch.setattr(
        "trading_mvp.services.scheduler.run_release_enrichment_watch_cycle",
        lambda session, triggered_by="scheduler": {"workflow": "release_enrichment_watch_cycle", "results": []},
    )
    monkeypatch.setattr("trading_mvp.services.scheduler.run_position_management_cycle", run_position_management)
    monkeypatch.setattr("trading_mvp.services.scheduler.run_due_entry_plan_watcher_cycle", lambda session: None)
    monkeypatch.setattr("trading_mvp.services.scheduler.run_due_interval_decision_cycle", lambda session: None)

    result = run_due_operational_cycles(
        db_session,
        include_exchange_sync=False,
        continue_on_error=True,
        session_factory=SessionFactory,
    )

    health_event = db_session.scalar(select(SystemHealthEvent).order_by(SystemHealthEvent.id.desc()).limit(1))
    assert calls == ["market", "position_management"]
    assert len(set(session_ids)) == 2
    assert result == [{"workflow": "position_management_cycle", "results": [{"status": "ok"}]}]
    assert health_event is not None
    assert health_event.payload["workflow"] == "market_refresh_cycle"


def test_abandon_stale_scheduler_runs_marks_only_old_running_rows(db_session) -> None:
    now = utcnow_naive()
    stale = SchedulerRun(
        schedule_window="1m",
        workflow="interval_decision_cycle",
        status="running",
        triggered_by="scheduler",
        outcome={"symbol": "BTCUSDT"},
        created_at=now - timedelta(hours=2),
    )
    fresh = SchedulerRun(
        schedule_window="1m",
        workflow="interval_decision_cycle",
        status="running",
        triggered_by="scheduler",
        outcome={"symbol": "ETHUSDT"},
        created_at=now - timedelta(minutes=5),
    )
    db_session.add_all([stale, fresh])
    db_session.flush()

    count = abandon_stale_scheduler_runs(db_session, older_than_seconds=1800, now=now)
    db_session.flush()

    assert count == 1
    assert stale.status == "abandoned"
    assert stale.outcome["abandoned_reason"] == "STALE_RUNNING_SCHEDULER_RUN"
    assert fresh.status == "running"


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

    assert symbol_plan["trigger"] is None
    assert symbol_plan["last_ai_skip_reason"] == "NO_EVENT"
    assert symbol_plan["next_ai_review_due_at"] is None
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
    assert symbol_plan["trigger"] is None
    assert symbol_plan["last_ai_skip_reason"] == "NO_EVENT"
    assert symbol_plan["next_ai_review_due_at"] is None


def test_time_based_backstop_no_longer_triggers_review(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    settings_row.symbol_cadence_overrides = [
        {
            "symbol": "BTCUSDT",
            "ai_backstop_interval_minutes_override": 30,
        }
    ]
    _mark_sync_fresh(settings_row)
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
                    "selection_reason": "underperforming_expectancy_bucket",
                    "rejected_reason": "underperforming_expectancy_bucket",
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

    assert symbol_plan["trigger"] is None
    assert symbol_plan["last_ai_skip_reason"] == "NO_EVENT"
    assert symbol_plan["next_ai_review_due_at"] is None


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


def test_release_enrichment_watch_cycle_refreshes_supported_event(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.tracked_symbols = ["BTCUSDT"]
    settings_row.default_timeframe = "15m"
    settings_row.event_source_provider = "fred"
    settings_row.event_source_bls_enrichment_url = "http://127.0.0.1:8091/bls/releases"
    db_session.add(settings_row)
    db_session.flush()

    now = utcnow_naive()
    snapshot_time = now - timedelta(seconds=25)
    event_at = now - timedelta(seconds=5)
    _seed_market_snapshot(
        db_session,
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=snapshot_time,
        event_name="Consumer Price Index",
        event_at=event_at,
    )

    called: list[dict[str, object]] = []

    def _fake_run_market_refresh_cycle(self, **kwargs):  # noqa: ANN001
        called.append(dict(kwargs))
        return {
            "workflow": "market_refresh_cycle",
            "results": [
                {
                    "symbol": "BTCUSDT",
                    "timeframe": "15m",
                    "status": "market_refresh",
                    "trigger_event": kwargs.get("trigger_event"),
                }
            ],
        }

    monkeypatch.setattr(TradingOrchestrator, "run_market_refresh_cycle", _fake_run_market_refresh_cycle)

    result = run_release_enrichment_watch_cycle(db_session, triggered_by="scheduler")

    assert len(result["results"]) == 1
    assert called == [
        {
            "symbols": ["BTCUSDT"],
            "timeframe": "15m",
            "trigger_event": "release_watch",
            "include_exchange_sync": False,
            "auto_resume_checked": True,
        }
    ]
    latest = db_session.scalar(
        select(SchedulerRun)
        .where(SchedulerRun.workflow == "release_enrichment_watch_cycle")
        .order_by(SchedulerRun.created_at.desc())
        .limit(1)
    )
    assert latest is not None
    assert latest.status == "success"
    assert latest.outcome["event_name"] == "Consumer Price Index"


def test_release_enrichment_watch_cycle_skips_already_enriched_event(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.tracked_symbols = ["BTCUSDT"]
    settings_row.default_timeframe = "15m"
    settings_row.event_source_provider = "fred"
    settings_row.event_source_bls_enrichment_url = "http://127.0.0.1:8091/bls/releases"
    db_session.add(settings_row)
    db_session.flush()

    now = utcnow_naive()
    _seed_market_snapshot(
        db_session,
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=now - timedelta(seconds=25),
        event_name="Consumer Price Index",
        event_at=now - timedelta(seconds=5),
        release_enrichment={"bls": {"actual": 3.1, "prior": 2.9}},
    )

    monkeypatch.setattr(
        TradingOrchestrator,
        "run_market_refresh_cycle",
        lambda self, **kwargs: pytest.fail("already-enriched event should not trigger immediate refresh"),
    )

    result = run_release_enrichment_watch_cycle(db_session, triggered_by="scheduler")

    assert result["results"] == []


def test_deduped_entry_trigger_surfaces_reason_fields(monkeypatch, db_session) -> None:
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
                        "trigger_reason": "entry_candidate_event",
                        "symbol": "BTCUSDT",
                        "timeframe": "15m",
                        "strategy_engine": "trend_pullback_engine",
                        "holding_profile": "scalp",
                        "reason_codes": ["ENTRY_CANDIDATE_SELECTED"],
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
                    "next_ai_review_due_at": None,
                    "applied_review_cadence_minutes": 15,
                    "review_cadence_source": "holding_profile_cadence_hint",
                    "holding_profile_cadence_hint": {
                        "holding_profile": "scalp",
                        "decision_interval_minutes": 15,
                    },
                    "max_review_age_minutes": 45,
                    "fingerprint_changed_fields": [],
                    "dedupe_reason": "TRIGGER_FINGERPRINT_UNCHANGED",
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
    assert outcome["dedupe_reason"] == "TRIGGER_FINGERPRINT_UNCHANGED"
    assert outcome["fingerprint_changed_fields"] == []
    assert outcome["last_material_review_at"] == (now - timedelta(minutes=15)).isoformat()
    assert outcome["forced_review_reason"] is None
    assert outcome["applied_review_cadence_minutes"] == 15
    assert outcome["review_cadence_source"] == "holding_profile_cadence_hint"
    assert outcome["next_ai_review_due_at"] is None


def test_interval_scheduler_keeps_ai_invoked_hold_distinct_from_skip(monkeypatch, db_session) -> None:
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
        lambda self, **kwargs: _entry_candidate_interval_plan(now=now),
    )

    def fake_run_decision_cycle(self, **kwargs):  # noqa: ANN001
        assert kwargs["review_trigger"]["trigger_reason"] == "entry_candidate_event"
        return {
            "symbol": "BTCUSDT",
            "decision_run_id": 101,
            "risk_check_id": 202,
            "decision": {"decision": "hold"},
            "risk_result": {
                "allowed": False,
                "decision": "hold",
                "reason_codes": ["HOLD_DECISION"],
                "blocked_reason_codes": ["HOLD_DECISION"],
            },
            "execution": None,
            "last_ai_trigger_reason": "entry_candidate_event",
            "last_ai_invoked_at": now.isoformat(),
            "last_ai_skip_reason": None,
            "ai_skipped_reason": None,
            "trigger_deduped": False,
            "trigger_fingerprint": "entry-candidate-fingerprint",
        }

    monkeypatch.setattr(TradingOrchestrator, "run_decision_cycle", fake_run_decision_cycle)

    result = run_interval_decision_cycle(db_session, triggered_by="scheduler")
    outcome = result["results"][0]["outcome"]

    assert outcome.get("ai_review_status") is None
    assert outcome["trigger"]["trigger_reason"] == "entry_candidate_event"
    assert outcome["decision"]["decision"] == "hold"
    assert outcome["risk_result"]["reason_codes"] == ["HOLD_DECISION"]
    assert outcome["last_ai_invoked_at"] == now.isoformat()
    assert outcome["last_ai_skip_reason"] is None
    assert outcome["ai_skipped_reason"] is None
    assert outcome["execution"] is None


def test_interval_scheduler_rechecks_exchange_sync_before_triggered_decision(monkeypatch, db_session) -> None:
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
        lambda self, **kwargs: _entry_candidate_interval_plan(now=now),
    )

    refresh_calls: list[str] = []

    def fake_refresh(session, *, triggered_by: str):  # noqa: ANN001
        refresh_calls.append(triggered_by)
        if triggered_by == "scheduler:pre_decision:BTCUSDT":
            return {"status": "ok", "triggered_by": triggered_by}
        return None

    monkeypatch.setattr("trading_mvp.services.scheduler.maybe_refresh_exchange_sync_freshness", fake_refresh)

    def fake_run_decision_cycle(self, **kwargs):  # noqa: ANN001
        assert kwargs["exchange_sync_checked"] is True
        return {
            "symbol": "BTCUSDT",
            "decision_run_id": 101,
            "risk_check_id": 202,
            "decision": {"decision": "hold"},
            "risk_result": {
                "allowed": False,
                "decision": "hold",
                "reason_codes": ["HOLD_DECISION"],
                "blocked_reason_codes": ["HOLD_DECISION"],
            },
            "execution": None,
            "last_ai_trigger_reason": "entry_candidate_event",
            "last_ai_invoked_at": now.isoformat(),
            "last_ai_skip_reason": None,
            "ai_skipped_reason": None,
            "trigger_deduped": False,
            "trigger_fingerprint": "entry-candidate-fingerprint",
        }

    monkeypatch.setattr(TradingOrchestrator, "run_decision_cycle", fake_run_decision_cycle)

    result = run_interval_decision_cycle(db_session, triggered_by="scheduler")
    outcome = result["results"][0]["outcome"]

    assert refresh_calls == ["scheduler:pre_decision", "scheduler:pre_decision:BTCUSDT"]
    assert outcome["pre_decision_exchange_sync"] == {
        "status": "ok",
        "triggered_by": "scheduler:pre_decision:BTCUSDT",
    }
    assert outcome["last_ai_skip_reason"] is None


def test_interval_scheduler_surfaces_preai_weak_volume_skip(monkeypatch, db_session) -> None:
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
        lambda self, **kwargs: _entry_candidate_interval_plan(
            now=now,
            last_ai_skip_reason="ENTRY_CANDIDATE_WEAK_VOLUME_PREAI",
        ),
    )

    def fake_run_decision_cycle(self, **kwargs):  # noqa: ANN001
        assert kwargs["review_trigger"]["trigger_reason"] == "entry_candidate_event"
        return {
            "symbol": "BTCUSDT",
            "decision_run_id": 303,
            "risk_check_id": 404,
            "decision": {"decision": "hold"},
            "risk_result": {
                "allowed": False,
                "decision": "hold",
                "reason_codes": ["HOLD_DECISION"],
                "blocked_reason_codes": ["HOLD_DECISION"],
            },
            "execution": None,
            "last_ai_trigger_reason": "entry_candidate_event",
            "last_ai_invoked_at": None,
            "last_ai_skip_reason": "ENTRY_CANDIDATE_WEAK_VOLUME_PREAI",
            "ai_skipped_reason": "ENTRY_CANDIDATE_WEAK_VOLUME_PREAI",
            "trigger_deduped": False,
            "trigger_fingerprint": "entry-candidate-fingerprint",
        }

    monkeypatch.setattr(TradingOrchestrator, "run_decision_cycle", fake_run_decision_cycle)

    result = run_interval_decision_cycle(db_session, triggered_by="scheduler")
    outcome = result["results"][0]["outcome"]

    assert outcome.get("ai_review_status") is None
    assert outcome["trigger"]["trigger_reason"] == "entry_candidate_event"
    assert outcome["decision"]["decision"] == "hold"
    assert outcome["risk_result"]["reason_codes"] == ["HOLD_DECISION"]
    assert outcome["last_ai_invoked_at"] is None
    assert outcome["last_ai_skip_reason"] == "ENTRY_CANDIDATE_WEAK_VOLUME_PREAI"
    assert outcome["ai_skipped_reason"] == "ENTRY_CANDIDATE_WEAK_VOLUME_PREAI"
    assert outcome["execution"] is None


def test_time_based_open_position_review_no_longer_schedules_next_due(monkeypatch, db_session) -> None:
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

    assert symbol_plan["trigger"] is None
    assert symbol_plan["trigger_deduped"] is False
    assert symbol_plan["review_cadence_source"] == "holding_profile_cadence_hint"
    assert symbol_plan["applied_review_cadence_minutes"] == 20
    assert symbol_plan["last_ai_skip_reason"] == "NO_EVENT"
    assert symbol_plan["next_ai_review_due_at"] is None
