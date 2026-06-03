from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from trading_mvp.models import (
    AgentRun,
    AuditEvent,
    MarketSnapshot,
    PendingEntryPlan,
    Position,
    SchedulerRun,
    SystemHealthEvent,
)
from trading_mvp.services.orchestrator import TradingOrchestrator
from trading_mvp.services.runtime_state import build_sync_freshness_summary, mark_sync_success
from trading_mvp.services.scheduler import (
    abandon_stale_scheduler_runs,
    get_due_entry_plan_symbols,
    get_due_interval_decision_symbols,
    get_due_position_management_symbols,
    maybe_refresh_exchange_sync_freshness,
    run_due_operational_cycles,
    run_interval_decision_cycle,
    run_market_refresh_cycle,
    run_release_enrichment_watch_cycle,
)
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive


def _mark_sync_fresh(settings_row) -> None:
    now = utcnow_naive()
    for scope in ("account", "positions", "open_orders", "protective_orders"):
        mark_sync_success(settings_row, scope=scope, synced_at=now)


def test_due_entry_plan_symbols_keep_future_utc_naive_armed_plan(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.tracked_symbols = ["BTCUSDT"]
    now = utcnow_naive()
    plan = PendingEntryPlan(
        symbol="BTCUSDT",
        side="short",
        plan_status="armed",
        source_decision_run_id=173,
        source_timeframe="15m",
        entry_mode="pullback_confirm",
        entry_zone_min=80505.6252,
        entry_zone_max=80344.7748,
        invalidation_price=80651.8214,
        max_chase_bps=4.0,
        idea_ttl_minutes=120,
        stop_loss=80651.8214,
        take_profit=80017.28148,
        risk_pct_cap=0.02,
        leverage_cap=3.0,
        expires_at=now + timedelta(minutes=90),
        idempotency_key="pending-plan:BTCUSDT:short:173:scheduler-test",
        metadata_json={},
    )
    db_session.add_all([settings_row, plan])
    db_session.flush()

    assert get_due_entry_plan_symbols(db_session) == ["BTCUSDT"]
    assert db_session.get(PendingEntryPlan, plan.id).plan_status == "armed"


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


def _multi_symbol_entry_candidate_interval_plan(*, now, symbols: list[str]) -> dict[str, object]:
    plans = []
    for symbol in symbols:
        trigger = {
            "trigger_reason": "entry_candidate_event",
            "symbol": symbol,
            "timeframe": "15m",
            "strategy_engine": "trend_pullback_engine",
            "holding_profile": "scalp",
            "reason_codes": ["ENTRY_CANDIDATE_SELECTED"],
            "trigger_fingerprint": f"{symbol}:entry-candidate-fingerprint",
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
        plans.append(
            {
                "symbol": symbol,
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
                "last_ai_skip_reason": None,
            }
        )
    return {
        "generated_at": now.isoformat(),
        "candidate_selection": {"rankings": []},
        "plans": plans,
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
    observed_sessions: list[Session] = []
    SessionFactory = sessionmaker(
        bind=db_session.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    def fail_market_refresh(session, triggered_by="scheduler"):
        calls.append("market")
        observed_sessions.append(session)
        raise RuntimeError("market failed")

    def run_position_management(session, triggered_by="scheduler"):
        calls.append("position_management")
        observed_sessions.append(session)
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
    assert len({id(session) for session in observed_sessions}) == 2
    assert result == [{"workflow": "position_management_cycle", "results": [{"status": "ok"}]}]
    assert health_event is not None
    assert health_event.payload["workflow"] == "market_refresh_cycle"


def test_run_due_operational_cycles_closes_isolated_sessions_on_success_and_failure(
    monkeypatch,
    db_session,
) -> None:
    calls: list[str] = []
    opened_session_ids: list[int] = []
    closed_session_ids: list[int] = []

    class TrackingSession(Session):
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            super().__init__(*args, **kwargs)
            opened_session_ids.append(id(self))

        def close(self) -> None:
            closed_session_ids.append(id(self))
            super().close()

    SessionFactory = sessionmaker(
        bind=db_session.get_bind(),
        class_=TrackingSession,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    def fail_market_refresh(session, triggered_by="scheduler"):
        calls.append("market")
        raise RuntimeError("market failed")

    def run_release_watch(session, triggered_by="scheduler"):
        calls.append("release")
        return {"workflow": "release_enrichment_watch_cycle", "results": []}

    def run_position_management(session, triggered_by="scheduler"):
        calls.append("position_management")
        return {"workflow": "position_management_cycle", "results": [{"status": "ok"}]}

    def run_entry_plan_watcher(session):
        calls.append("entry_plan_watcher")
        return {"workflow": "entry_plan_watcher_cycle", "results": []}

    def run_decision(session):
        calls.append("decision")
        return {"workflow": "interval_decision_cycle", "results": []}

    monkeypatch.setattr("trading_mvp.services.scheduler.run_market_refresh_cycle", fail_market_refresh)
    monkeypatch.setattr("trading_mvp.services.scheduler.run_release_enrichment_watch_cycle", run_release_watch)
    monkeypatch.setattr("trading_mvp.services.scheduler.run_position_management_cycle", run_position_management)
    monkeypatch.setattr("trading_mvp.services.scheduler.run_due_entry_plan_watcher_cycle", run_entry_plan_watcher)
    monkeypatch.setattr("trading_mvp.services.scheduler.run_due_interval_decision_cycle", run_decision)

    result = run_due_operational_cycles(
        db_session,
        include_exchange_sync=False,
        continue_on_error=True,
        session_factory=SessionFactory,
    )

    assert calls == ["market", "release", "position_management", "entry_plan_watcher", "decision"]
    assert result == [{"workflow": "position_management_cycle", "results": [{"status": "ok"}]}]
    assert len(opened_session_ids) == 5
    assert sorted(closed_session_ids) == sorted(opened_session_ids)


def test_maybe_refresh_exchange_sync_commits_before_external_sync(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.binance_api_key_encrypted = "encrypted-key"
    settings_row.binance_api_secret_encrypted = "encrypted-secret"
    db_session.add(settings_row)
    db_session.flush()

    observed: dict[str, bool] = {}

    monkeypatch.setattr("trading_mvp.services.scheduler._sync_summary_needs_refresh", lambda *args, **kwargs: True)
    monkeypatch.setattr("trading_mvp.services.scheduler._latest_sync_attempt_at", lambda summary: None)

    def fake_run_exchange_sync(session, triggered_by="scheduler"):  # noqa: ANN001
        observed["in_transaction"] = session.in_transaction()
        return {"status": "ok", "triggered_by": triggered_by}

    monkeypatch.setattr("trading_mvp.services.scheduler.run_exchange_sync_cycle", fake_run_exchange_sync)

    result = maybe_refresh_exchange_sync_freshness(db_session, triggered_by="scheduler:pre_decision")

    assert result == {"status": "ok", "triggered_by": "scheduler:pre_decision"}
    assert observed == {"in_transaction": False}


def test_maybe_refresh_exchange_sync_rolls_back_and_reraises(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.binance_api_key_encrypted = "encrypted-key"
    settings_row.binance_api_secret_encrypted = "encrypted-secret"
    db_session.add(settings_row)
    db_session.flush()

    monkeypatch.setattr("trading_mvp.services.scheduler._sync_summary_needs_refresh", lambda *args, **kwargs: True)
    monkeypatch.setattr("trading_mvp.services.scheduler._latest_sync_attempt_at", lambda summary: None)

    def fail_exchange_sync(session, triggered_by="scheduler"):  # noqa: ANN001
        assert session.in_transaction() is False
        raise RuntimeError("exchange sync failed")

    monkeypatch.setattr("trading_mvp.services.scheduler.run_exchange_sync_cycle", fail_exchange_sync)

    with pytest.raises(RuntimeError, match="exchange sync failed"):
        maybe_refresh_exchange_sync_freshness(db_session, triggered_by="scheduler:pre_decision")

    assert db_session.in_transaction() is False


def test_interval_scheduler_records_cycle_pre_sync_failure_without_blocking_decision(
    monkeypatch,
    db_session,
) -> None:
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
        lambda self, **kwargs: {"generated_at": now.isoformat(), "candidate_selection": {}, "plans": []},
    )
    monkeypatch.setattr(
        "trading_mvp.services.scheduler.maybe_refresh_exchange_sync_freshness",
        lambda session, *, triggered_by: (_ for _ in ()).throw(RuntimeError("cycle sync failed")),
    )

    result = run_interval_decision_cycle(db_session, triggered_by="scheduler")
    outcome = result["results"][0]["outcome"]
    health_event = db_session.scalar(select(SystemHealthEvent).order_by(SystemHealthEvent.id.desc()).limit(1))

    assert result["pre_decision_exchange_sync_error"] == "cycle sync failed"
    assert outcome["ai_review_status"] == "no_event"
    assert outcome["pre_decision_exchange_sync_error"] == "cycle sync failed"
    assert health_event is not None
    assert health_event.payload["workflow"] == "interval_decision_cycle"
    assert health_event.payload["symbol"] is None
    assert health_event.payload["stage"] == "cycle_pre_decision_exchange_sync"


def test_interval_scheduler_records_plan_build_failure_without_poisoning_session(
    monkeypatch,
    db_session,
) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_sync_fresh(settings_row)
    db_session.add(settings_row)
    db_session.flush()

    monkeypatch.setattr(
        "trading_mvp.services.scheduler.maybe_refresh_exchange_sync_freshness",
        lambda session, *, triggered_by: None,
    )

    def fail_build_plan(self, **kwargs):  # noqa: ANN001, ARG001
        raise RuntimeError("decision plan build failed")

    monkeypatch.setattr(TradingOrchestrator, "build_interval_decision_plan", fail_build_plan)

    result = run_interval_decision_cycle(db_session, triggered_by="scheduler")
    scheduler_run = db_session.scalar(select(SchedulerRun).order_by(SchedulerRun.id.desc()).limit(1))
    health_event = db_session.scalar(select(SystemHealthEvent).order_by(SystemHealthEvent.id.desc()).limit(1))

    assert result["results"][0]["status"] == "failed"
    assert result["results"][0]["stage"] == "decision_plan_build"
    assert result["decision_plan_error"] == "decision plan build failed"
    assert scheduler_run is not None
    assert scheduler_run.status == "failed"
    assert scheduler_run.outcome["stage"] == "decision_plan_build"
    assert health_event is not None
    assert health_event.payload["workflow"] == "interval_decision_cycle"
    assert health_event.payload["stage"] == "decision_plan_build"
    assert db_session.in_transaction() is True
    db_session.rollback()
    assert db_session.in_transaction() is False


def test_interval_scheduler_plans_due_symbols_with_full_candidate_universe(
    monkeypatch,
    db_session,
) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    _mark_sync_fresh(settings_row)
    db_session.add(settings_row)
    now = utcnow_naive()
    for symbol in ["ETHUSDT", "SOLUSDT"]:
        db_session.add(
            SchedulerRun(
                schedule_window="15m",
                workflow="interval_decision_cycle",
                status="success",
                triggered_by="scheduler",
                created_at=now,
                next_run_at=now + timedelta(minutes=15),
                outcome={"symbol": symbol, "status": "completed"},
            )
        )
    db_session.flush()

    observed_kwargs: dict[str, object] = {}
    monkeypatch.setattr(
        "trading_mvp.services.scheduler.maybe_refresh_exchange_sync_freshness",
        lambda session, *, triggered_by: None,
    )

    def fake_build_interval_decision_plan(self, **kwargs):  # noqa: ANN001, ARG001
        observed_kwargs.update(kwargs)
        return {
            "generated_at": now.isoformat(),
            "candidate_selection": {
                "breadth_regime": "mixed",
                "breadth_summary": {"tracked_symbols": 3},
                "rankings": [],
            },
            "plans": [
                {
                    "symbol": "BTCUSDT",
                    "timeframe": "15m",
                    "cadence": {},
                    "selection_context": None,
                    "trigger": None,
                    "trigger_deduped": False,
                    "last_decision_at": None,
                    "last_ai_invoked_at": None,
                    "last_material_review_at": None,
                    "next_ai_review_due_at": None,
                    "applied_review_cadence_minutes": 15,
                    "review_cadence_source": "holding_profile_cadence_hint",
                    "holding_profile_cadence_hint": {},
                    "max_review_age_minutes": None,
                    "fingerprint_changed_fields": [],
                    "dedupe_reason": None,
                    "forced_review_reason": None,
                    "last_ai_skip_reason": "NO_EVENT",
                }
            ],
        }

    monkeypatch.setattr(TradingOrchestrator, "build_interval_decision_plan", fake_build_interval_decision_plan)

    result = run_interval_decision_cycle(db_session, triggered_by="scheduler")

    assert observed_kwargs["symbols"] == ["BTCUSDT"]
    assert observed_kwargs["candidate_universe_symbols"] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert [item["outcome"]["symbol"] for item in result["results"]] == ["BTCUSDT"]
    assert result["candidate_selection"]["breadth_summary"]["tracked_symbols"] == 3


def test_interval_decision_plan_ranks_full_candidate_universe_but_returns_due_plans(
    monkeypatch,
    db_session,
) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    _mark_sync_fresh(settings_row)
    db_session.add(settings_row)
    db_session.flush()

    observed_rank_kwargs: dict[str, object] = {}

    def fake_rank_candidate_symbols(self, **kwargs):  # noqa: ANN001, ARG001
        observed_rank_kwargs.update(kwargs)
        return {
            "mode": "portfolio_rotation_top_n",
            "breadth_regime": "mixed",
            "breadth_summary": {"tracked_symbols": 3},
            "selected_symbols": [],
            "skipped_symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            "rankings": [],
        }

    monkeypatch.setattr(TradingOrchestrator, "_rank_candidate_symbols", fake_rank_candidate_symbols)

    plan = TradingOrchestrator(db_session).build_interval_decision_plan(
        symbols=["BTCUSDT"],
        candidate_universe_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    )

    assert observed_rank_kwargs["decision_symbols"] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert [item["symbol"] for item in plan["plans"]] == ["BTCUSDT"]
    assert plan["candidate_selection"]["breadth_summary"]["tracked_symbols"] == 3


def test_interval_scheduler_commits_started_run_before_triggered_decision(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT"]
    _mark_sync_fresh(settings_row)
    db_session.add(settings_row)
    db_session.flush()

    now = utcnow_naive()
    observed: dict[str, bool] = {}
    monkeypatch.setattr(
        TradingOrchestrator,
        "build_interval_decision_plan",
        lambda self, **kwargs: _entry_candidate_interval_plan(now=now),
    )
    monkeypatch.setattr(
        "trading_mvp.services.scheduler.maybe_refresh_exchange_sync_freshness",
        lambda session, *, triggered_by: None,
    )

    def fake_run_decision_cycle(self, **kwargs):  # noqa: ANN001
        observed["in_transaction"] = self.session.in_transaction()
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

    assert result["results"][0]["outcome"]["decision"]["decision"] == "hold"
    assert observed == {"in_transaction": False}


def test_interval_scheduler_symbol_sync_failure_does_not_block_next_symbol_or_tick(
    monkeypatch,
    db_session,
) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT", "ETHUSDT"]
    _mark_sync_fresh(settings_row)
    db_session.add(settings_row)
    db_session.flush()
    db_session.commit()

    SessionFactory = sessionmaker(
        bind=db_session.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    now = utcnow_naive()
    refresh_failures = {"BTCUSDT": 1}
    decision_symbols: list[str] = []
    monkeypatch.setattr(
        TradingOrchestrator,
        "build_interval_decision_plan",
        lambda self, **kwargs: _multi_symbol_entry_candidate_interval_plan(
            now=now,
            symbols=["BTCUSDT", "ETHUSDT"],
        ),
    )
    monkeypatch.setattr(
        "trading_mvp.services.scheduler.run_market_refresh_cycle",
        lambda session, triggered_by="scheduler": {"workflow": "market_refresh_cycle", "results": []},
    )
    monkeypatch.setattr(
        "trading_mvp.services.scheduler.run_release_enrichment_watch_cycle",
        lambda session, triggered_by="scheduler": {"workflow": "release_enrichment_watch_cycle", "results": []},
    )
    monkeypatch.setattr(
        "trading_mvp.services.scheduler.run_position_management_cycle",
        lambda session, triggered_by="scheduler": {"workflow": "position_management_cycle", "results": []},
    )
    monkeypatch.setattr("trading_mvp.services.scheduler.run_due_entry_plan_watcher_cycle", lambda session: None)

    def fake_refresh(session, *, triggered_by: str):  # noqa: ANN001
        if triggered_by == "scheduler:pre_decision:BTCUSDT" and refresh_failures["BTCUSDT"] > 0:
            refresh_failures["BTCUSDT"] -= 1
            raise RuntimeError("BTCUSDT sync failed")
        return None

    def fake_run_decision_cycle(self, **kwargs):  # noqa: ANN001
        symbol = kwargs["symbol"]
        decision_symbols.append(symbol)
        return {
            "symbol": symbol,
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
            "trigger_fingerprint": f"{symbol}:entry-candidate-fingerprint",
        }

    monkeypatch.setattr("trading_mvp.services.scheduler.maybe_refresh_exchange_sync_freshness", fake_refresh)
    monkeypatch.setattr(TradingOrchestrator, "run_decision_cycle", fake_run_decision_cycle)

    first_result = run_due_operational_cycles(
        db_session,
        include_exchange_sync=False,
        continue_on_error=True,
        session_factory=SessionFactory,
    )
    second_result = run_due_operational_cycles(
        db_session,
        include_exchange_sync=False,
        continue_on_error=True,
        session_factory=SessionFactory,
    )

    scheduler_runs = list(
        db_session.scalars(
            select(SchedulerRun)
            .where(SchedulerRun.workflow == "interval_decision_cycle")
            .order_by(SchedulerRun.id)
        )
    )
    health_event = db_session.scalar(select(SystemHealthEvent).order_by(SystemHealthEvent.id.desc()).limit(1))

    assert decision_symbols == ["ETHUSDT", "BTCUSDT"]
    assert first_result[0]["results"][0]["status"] == "failed"
    assert first_result[0]["results"][0]["stage"] == "symbol_pre_decision_exchange_sync"
    assert first_result[0]["results"][1]["status"] == "success"
    assert second_result[0]["results"][0]["status"] == "success"
    assert [row.status for row in scheduler_runs] == ["failed", "success", "success"]
    assert health_event is not None
    assert health_event.payload["workflow"] == "interval_decision_cycle"
    assert health_event.payload["symbol"] == "BTCUSDT"
    assert health_event.payload["stage"] == "symbol_pre_decision_exchange_sync"


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


def test_market_refresh_cycle_skips_when_workflow_lease_is_held(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.tracked_symbols = ["BTCUSDT"]
    settings_row.default_timeframe = "15m"
    db_session.add(settings_row)
    db_session.flush()

    monkeypatch.setattr(
        "trading_mvp.services.scheduler._try_workflow_advisory_lock",
        lambda session, workflow: False,
    )
    monkeypatch.setattr(
        TradingOrchestrator,
        "run_market_refresh_cycle",
        lambda self, **kwargs: pytest.fail("market refresh should not run while lease is held"),
    )

    result = run_market_refresh_cycle(db_session)
    db_session.flush()

    assert result == {
        "workflow": "market_refresh_cycle",
        "status": "skipped",
        "reason": "WORKFLOW_LEASE_HELD",
        "results": [],
    }
    event = db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.event_type == "scheduler_workflow_skipped")
        .order_by(AuditEvent.id.desc())
        .limit(1)
    )
    assert event is not None
    assert event.entity_id == "market_refresh_cycle"
    assert event.payload["reason"] == "WORKFLOW_LEASE_HELD"


def test_market_refresh_cycle_recovers_session_before_persisting_failure(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.tracked_symbols = ["BTCUSDT"]
    settings_row.default_timeframe = "15m"
    db_session.add(settings_row)
    db_session.flush()

    def fail_with_poisoned_transaction(self, **kwargs):  # noqa: ANN001, ARG001
        self.session.add(
            SchedulerRun(
                schedule_window=None,
                workflow="poisoned_market_refresh",
                status="running",
                triggered_by="test",
                outcome={},
            )
        )
        self.session.flush()

    monkeypatch.setattr(TradingOrchestrator, "run_market_refresh_cycle", fail_with_poisoned_transaction)

    result = run_market_refresh_cycle(db_session, triggered_by="scheduler")
    scheduler_run = db_session.scalar(
        select(SchedulerRun)
        .where(SchedulerRun.workflow == "market_refresh_cycle")
        .order_by(SchedulerRun.id.desc())
        .limit(1)
    )

    assert result["results"]
    assert result["results"][0]["status"] == "failed"
    assert result["results"][0]["outcome"]["stage"] == "market_refresh"
    assert result["results"][0]["outcome"]["scheduler_failure_persisted"] is True
    assert scheduler_run is not None
    assert scheduler_run.status == "failed"
    assert scheduler_run.outcome["symbol"] == "BTCUSDT"
    assert scheduler_run.outcome["scheduler_failure_persisted"] is True


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
