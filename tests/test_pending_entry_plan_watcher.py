from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from trading_mvp.models import PendingEntryPlan, RiskCheck
from trading_mvp.schemas import MarketCandle, MarketSnapshotPayload, RiskCheckResult, TradeDecision
from trading_mvp.services.dashboard import get_overview
from trading_mvp.services.orchestrator import TradingOrchestrator
from trading_mvp.services.runtime_state import mark_sync_issue, mark_sync_success
from trading_mvp.services.secret_store import encrypt_secret
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive


def _mark_all_sync_fresh(settings_row) -> None:
    now = utcnow_naive()
    for scope in ("account", "positions", "open_orders", "protective_orders"):
        mark_sync_success(settings_row, scope=scope, synced_at=now)


def _enable_live_settings(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    _mark_all_sync_fresh(settings_row)
    db_session.add(settings_row)
    db_session.flush()


def _snapshot(
    *,
    timeframe: str,
    snapshot_time,
    latest_price: float,
    candles: list[tuple[float, float, float, float]],
) -> MarketSnapshotPayload:
    candle_rows = []
    for index, (open_price, high_price, low_price, close_price) in enumerate(candles):
        candle_rows.append(
            MarketCandle(
                timestamp=snapshot_time - timedelta(minutes=max(len(candles) - index - 1, 0)),
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                volume=1000.0 + index,
            )
        )
    return MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe=timeframe,
        snapshot_time=snapshot_time,
        latest_price=latest_price,
        latest_volume=1000.0,
        candle_count=len(candle_rows),
        is_stale=False,
        is_complete=True,
        candles=candle_rows,
    )


def _decision_snapshot(*, snapshot_time, latest_price: float = 70000.0) -> MarketSnapshotPayload:
    return _snapshot(
        timeframe="15m",
        snapshot_time=snapshot_time,
        latest_price=latest_price,
        candles=[(69900.0, 70100.0, 69800.0, latest_price)],
    )


def _watch_snapshot(*, snapshot_time, latest_price: float) -> MarketSnapshotPayload:
    return _snapshot(
        timeframe="1m",
        snapshot_time=snapshot_time,
        latest_price=latest_price,
        candles=[
            (69480.0, 69500.0, 69320.0, 69360.0),
            (69350.0, 69480.0, 69240.0, latest_price),
        ],
    )


def _watch_snapshot_weak_reclaim(*, snapshot_time, latest_price: float = 69305.0) -> MarketSnapshotPayload:
    return _snapshot(
        timeframe="1m",
        snapshot_time=snapshot_time,
        latest_price=latest_price,
        candles=[
            (69420.0, 69480.0, 69280.0, 69310.0),
            (69300.0, 69420.0, 69260.0, latest_price),
        ],
    )


def _watch_snapshot_late_chase(*, snapshot_time, latest_price: float = 70500.0) -> MarketSnapshotPayload:
    return _snapshot(
        timeframe="1m",
        snapshot_time=snapshot_time,
        latest_price=latest_price,
        candles=[
            (69450.0, 69520.0, 69290.0, 69340.0),
            (69340.0, 70520.0, 69280.0, latest_price),
        ],
    )


def _market_context(snapshot: MarketSnapshotPayload) -> dict[str, MarketSnapshotPayload]:
    return {
        "15m": snapshot,
        "1h": snapshot.model_copy(update={"timeframe": "1h"}),
        "4h": snapshot.model_copy(update={"timeframe": "4h"}),
    }


def _pullback_long_decision() -> TradeDecision:
    return TradeDecision(
        decision="long",
        confidence=0.72,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=69000.0,
        entry_zone_max=69300.0,
        entry_mode="pullback_confirm",
        invalidation_price=68500.0,
        max_chase_bps=25.0,
        idea_ttl_minutes=15,
        stop_loss=68500.0,
        take_profit=71000.0,
        max_holding_minutes=180,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["ALIGNED_PULLBACK", "TREND_UP"],
        explanation_short="상승 추세 눌림목 진입 계획입니다.",
        explanation_detailed="현재 가격은 zone 밖이라 즉시 진입보다 계획 arm 후 1분 확인을 기다립니다.",
    )


def _hold_decision() -> TradeDecision:
    return TradeDecision(
        decision="hold",
        confidence=0.41,
        symbol="BTCUSDT",
        timeframe="15m",
        entry_zone_min=None,
        entry_zone_max=None,
        entry_mode="none",
        invalidation_price=None,
        max_chase_bps=None,
        idea_ttl_minutes=None,
        stop_loss=None,
        take_profit=None,
        max_holding_minutes=120,
        risk_pct=0.001,
        leverage=1.0,
        rationale_codes=["NO_EDGE"],
        explanation_short="현재는 hold가 우선입니다.",
        explanation_detailed="새 계획보다 기존 armed plan을 취소하고 관망하는 편이 안전합니다.",
    )


def _build_stubbed_risk_result(decision: TradeDecision) -> RiskCheckResult:
    trigger_waiting = decision.decision in {"long", "short"} and decision.entry_mode != "immediate"
    blocked_reason_codes = ["ENTRY_TRIGGER_NOT_MET"] if trigger_waiting else []
    approved_risk_pct = 0.0 if trigger_waiting else float(decision.risk_pct or 0.0)
    approved_leverage = 0.0 if trigger_waiting else float(decision.leverage or 0.0)
    return RiskCheckResult(
        allowed=not trigger_waiting,
        decision=decision.decision,  # type: ignore[arg-type]
        reason_codes=blocked_reason_codes,
        blocked_reason_codes=blocked_reason_codes,
        approved_risk_pct=approved_risk_pct,
        approved_leverage=approved_leverage,
        operating_mode="live",
        effective_leverage_cap=5.0,
        symbol_risk_tier="btc",
        exposure_metrics={},
    )


def _arm_plan(monkeypatch, db_session, *, snapshot_time=None) -> tuple[TradingOrchestrator, dict[str, object]]:
    _enable_live_settings(db_session)

    class EnabledSettings:
        live_trading_env_enabled = True

    monkeypatch.setattr("trading_mvp.services.risk.get_settings", lambda: EnabledSettings())
    snapshot_time = snapshot_time or utcnow_naive()
    decision_snapshot = _decision_snapshot(snapshot_time=snapshot_time)
    orchestrator = TradingOrchestrator(db_session)
    orchestrator.trading_agent.run = lambda *args, **kwargs: (
        _pullback_long_decision(),
        "deterministic-mock",
        {},
    )
    def fake_evaluate_risk(
        session,
        settings_row,
        decision,
        market_snapshot,
        decision_run_id=None,
        market_snapshot_id=None,
        execution_mode="live",
        **kwargs,
    ):
        risk_result = _build_stubbed_risk_result(decision)
        risk_row = RiskCheck(
            symbol=decision.symbol,
            decision_run_id=decision_run_id,
            market_snapshot_id=market_snapshot_id,
            allowed=risk_result.allowed,
            decision=decision.decision,
            reason_codes=list(risk_result.reason_codes),
            approved_risk_pct=risk_result.approved_risk_pct,
            approved_leverage=risk_result.approved_leverage,
            payload=risk_result.model_dump(mode="json"),
        )
        session.add(risk_row)
        session.flush()
        return risk_result, risk_row

    monkeypatch.setattr("trading_mvp.services.orchestrator.evaluate_risk", fake_evaluate_risk)
    monkeypatch.setattr(
        "trading_mvp.services.orchestrator.execute_live_trade",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("entry plans must not execute during decision cycle")),
    )
    result = orchestrator.run_decision_cycle(
        symbol="BTCUSDT",
        trigger_event="manual",
        market_snapshot_override=decision_snapshot,
        market_context_override=_market_context(decision_snapshot),
        exchange_sync_checked=True,
    )
    db_session.flush()
    return orchestrator, result


def test_decision_cycle_arms_pullback_entry_plan_without_immediate_order(monkeypatch, db_session) -> None:
    _, result = _arm_plan(monkeypatch, db_session)

    plan = db_session.scalar(select(PendingEntryPlan).where(PendingEntryPlan.plan_status == "armed"))

    assert result["decision"]["decision"] == "long"
    assert result["execution"] is None
    assert result["entry_plan"] is not None
    assert result["entry_plan"]["plan_status"] == "armed"
    assert "ENTRY_TRIGGER_NOT_MET" in result["risk_result"]["blocked_reason_codes"]
    assert plan is not None
    assert plan.plan_status == "armed"
    assert plan.idempotency_key.startswith("pending-plan:BTCUSDT:long:")


def test_entry_plan_watcher_executes_after_zone_entry_and_confirm_without_new_ai_call(monkeypatch, db_session) -> None:
    orchestrator, result = _arm_plan(monkeypatch, db_session)
    plan = db_session.scalar(select(PendingEntryPlan).where(PendingEntryPlan.plan_status == "armed"))
    assert plan is not None

    execution_calls: list[dict[str, object]] = []

    def fake_execute_live_trade(*args, **kwargs):
        execution_calls.append({"idempotency_key": kwargs.get("idempotency_key"), "decision": kwargs["decision"]})
        return {"order_id": 101, "status": "filled", "idempotency_key": kwargs.get("idempotency_key")}

    monkeypatch.setattr("trading_mvp.services.orchestrator.execute_live_trade", fake_execute_live_trade)

    watch_result = orchestrator.run_entry_plan_watcher_cycle(
        symbols=["BTCUSDT"],
        exchange_sync_checked=True,
        market_snapshot_override=_watch_snapshot(
            snapshot_time=utcnow_naive() + timedelta(minutes=1),
            latest_price=69420.0,
        ),
    )
    db_session.flush()
    refreshed = db_session.get(PendingEntryPlan, plan.id)

    assert result["entry_plan"]["plan_status"] == "armed"
    assert watch_result["results"][0]["plans"][0]["status"] == "triggered"
    assert len(execution_calls) == 1
    assert execution_calls[0]["idempotency_key"] == plan.idempotency_key
    assert execution_calls[0]["decision"].entry_mode == "immediate"
    assert refreshed is not None
    assert refreshed.plan_status == "triggered"
    trigger_details = watch_result["results"][0]["plans"][0]["plan"]["trigger_details"]
    assert trigger_details["quality_state"] == "trigger"
    assert trigger_details["quality_score"] >= trigger_details["quality_threshold"]
    assert trigger_details["quality_components"]["reclaim_signal_strength"] >= 0.55


def test_entry_plan_watcher_keeps_waiting_on_weak_reclaim_quality(monkeypatch, db_session) -> None:
    orchestrator, _ = _arm_plan(monkeypatch, db_session)
    plan = db_session.scalar(select(PendingEntryPlan).where(PendingEntryPlan.plan_status == "armed"))
    assert plan is not None

    execute_called = False

    def fake_execute_live_trade(*args, **kwargs):
        nonlocal execute_called
        execute_called = True
        return {"order_id": 201, "status": "filled"}

    monkeypatch.setattr("trading_mvp.services.orchestrator.execute_live_trade", fake_execute_live_trade)

    watch_result = orchestrator.run_entry_plan_watcher_cycle(
        symbols=["BTCUSDT"],
        exchange_sync_checked=True,
        market_snapshot_override=_watch_snapshot_weak_reclaim(snapshot_time=utcnow_naive() + timedelta(minutes=1)),
    )
    db_session.flush()
    refreshed = db_session.get(PendingEntryPlan, plan.id)

    assert watch_result["results"][0]["plans"][0]["status"] == "armed_waiting_confirmation"
    assert watch_result["results"][0]["plans"][0]["blocked_reasons"] == ["PLAN_CONFIRM_QUALITY_LOW"]
    trigger_details = watch_result["results"][0]["plans"][0]["plan"]["trigger_details"]
    assert trigger_details["quality_state"] == "waiting"
    assert trigger_details["quality_score"] < trigger_details["quality_threshold"]
    assert execute_called is False
    assert refreshed is not None
    assert refreshed.plan_status == "armed"


def test_entry_plan_watcher_cancels_on_late_chase_and_rr_deterioration(monkeypatch, db_session) -> None:
    orchestrator, _ = _arm_plan(monkeypatch, db_session)
    plan = db_session.scalar(select(PendingEntryPlan).where(PendingEntryPlan.plan_status == "armed"))
    assert plan is not None

    execute_called = False

    def fake_execute_live_trade(*args, **kwargs):
        nonlocal execute_called
        execute_called = True
        return {"order_id": 301, "status": "filled"}

    monkeypatch.setattr("trading_mvp.services.orchestrator.execute_live_trade", fake_execute_live_trade)

    watch_result = orchestrator.run_entry_plan_watcher_cycle(
        symbols=["BTCUSDT"],
        exchange_sync_checked=True,
        market_snapshot_override=_watch_snapshot_late_chase(snapshot_time=utcnow_naive() + timedelta(minutes=1)),
    )
    db_session.flush()
    refreshed = db_session.get(PendingEntryPlan, plan.id)

    assert watch_result["results"][0]["plans"][0]["status"] == "canceled"
    assert watch_result["results"][0]["plans"][0]["blocked_reasons"] == ["PLAN_CONFIRM_QUALITY_REJECTED"]
    trigger_details = watch_result["results"][0]["plans"][0]["plan"]["trigger_details"]
    assert trigger_details["quality_state"] == "cancel"
    assert trigger_details["cancel_recommended"] is True
    assert trigger_details["late_chase"] is True
    assert trigger_details["current_expected_rr"] is not None and trigger_details["current_expected_rr"] < 0.85
    assert execute_called is False
    assert refreshed is not None
    assert refreshed.plan_status == "canceled"
    assert refreshed.canceled_reason == "PLAN_CONFIRM_QUALITY_REJECTED"


def test_entry_plan_watcher_expires_plan_without_execution(monkeypatch, db_session) -> None:
    orchestrator, _ = _arm_plan(monkeypatch, db_session)
    plan = db_session.scalar(select(PendingEntryPlan).where(PendingEntryPlan.plan_status == "armed"))
    assert plan is not None
    plan.expires_at = utcnow_naive() - timedelta(seconds=1)
    db_session.add(plan)
    db_session.flush()

    execute_called = False

    def fake_execute_live_trade(*args, **kwargs):
        nonlocal execute_called
        execute_called = True
        return {"order_id": 1, "status": "filled"}

    monkeypatch.setattr("trading_mvp.services.orchestrator.execute_live_trade", fake_execute_live_trade)

    watch_result = orchestrator.run_entry_plan_watcher_cycle(
        symbols=["BTCUSDT"],
        exchange_sync_checked=True,
        market_snapshot_override=_watch_snapshot(snapshot_time=utcnow_naive(), latest_price=69420.0),
    )
    db_session.flush()
    refreshed = db_session.get(PendingEntryPlan, plan.id)

    assert watch_result["results"][0]["plans"][0]["status"] == "expired"
    assert execute_called is False
    assert refreshed is not None
    assert refreshed.plan_status == "expired"


def test_new_hold_decision_cancels_existing_armed_plan(monkeypatch, db_session) -> None:
    orchestrator, _ = _arm_plan(monkeypatch, db_session, snapshot_time=utcnow_naive())
    existing_plan = db_session.scalar(select(PendingEntryPlan).where(PendingEntryPlan.plan_status == "armed"))
    assert existing_plan is not None

    later_snapshot = _decision_snapshot(snapshot_time=utcnow_naive() + timedelta(minutes=15), latest_price=70100.0)
    orchestrator.trading_agent.run = lambda *args, **kwargs: (
        _hold_decision(),
        "deterministic-mock",
        {},
    )
    result = orchestrator.run_decision_cycle(
        symbol="BTCUSDT",
        trigger_event="manual",
        market_snapshot_override=later_snapshot,
        market_context_override=_market_context(later_snapshot),
        exchange_sync_checked=True,
    )
    db_session.flush()
    refreshed = db_session.get(PendingEntryPlan, existing_plan.id)

    assert result["decision"]["decision"] == "hold"
    assert result["canceled_entry_plans"]
    assert refreshed is not None
    assert refreshed.plan_status == "canceled"
    assert refreshed.canceled_reason == "NEW_AI_HOLD_DECISION"


def test_entry_plan_watcher_cancels_on_stale_sync(monkeypatch, db_session) -> None:
    orchestrator, _ = _arm_plan(monkeypatch, db_session)
    plan = db_session.scalar(select(PendingEntryPlan).where(PendingEntryPlan.plan_status == "armed"))
    settings_row = get_or_create_settings(db_session)
    mark_sync_issue(
        settings_row,
        scope="account",
        status="incomplete",
        reason_code="ACCOUNT_STATE_STALE",
        observed_at=utcnow_naive(),
    )
    db_session.add(settings_row)
    db_session.flush()

    execute_called = False

    def fake_execute_live_trade(*args, **kwargs):
        nonlocal execute_called
        execute_called = True
        return {"order_id": 1, "status": "filled"}

    monkeypatch.setattr("trading_mvp.services.orchestrator.execute_live_trade", fake_execute_live_trade)

    watch_result = orchestrator.run_entry_plan_watcher_cycle(
        symbols=["BTCUSDT"],
        exchange_sync_checked=True,
        market_snapshot_override=_watch_snapshot(snapshot_time=utcnow_naive(), latest_price=69420.0),
    )
    db_session.flush()
    refreshed = db_session.get(PendingEntryPlan, plan.id if plan is not None else 0)

    assert watch_result["results"][0]["plans"][0]["status"] == "canceled"
    assert execute_called is False
    assert refreshed is not None
    assert refreshed.plan_status == "canceled"
    assert refreshed.canceled_reason == "PLAN_CANCELED_STALE_SYNC"


def test_entry_plan_watcher_respects_approval_and_prevents_duplicate_execution(monkeypatch, db_session) -> None:
    orchestrator, _ = _arm_plan(monkeypatch, db_session)
    settings_row = get_or_create_settings(db_session)
    settings_row.live_execution_armed = False
    settings_row.live_execution_armed_until = None
    db_session.add(settings_row)
    db_session.flush()

    execution_calls = 0

    def fake_execute_live_trade(*args, **kwargs):
        nonlocal execution_calls
        execution_calls += 1
        return {"order_id": 77, "status": "filled"}

    monkeypatch.setattr("trading_mvp.services.orchestrator.execute_live_trade", fake_execute_live_trade)

    blocked_result = orchestrator.run_entry_plan_watcher_cycle(
        symbols=["BTCUSDT"],
        exchange_sync_checked=True,
        market_snapshot_override=_watch_snapshot(snapshot_time=utcnow_naive(), latest_price=69420.0),
    )
    db_session.flush()
    still_armed = db_session.scalar(select(PendingEntryPlan).where(PendingEntryPlan.plan_status == "armed"))
    assert blocked_result["results"][0]["plans"][0]["status"] == "control_blocked"
    assert still_armed is not None
    assert still_armed.plan_status == "armed"

    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    db_session.add(settings_row)
    db_session.flush()

    first_trigger = orchestrator.run_entry_plan_watcher_cycle(
        symbols=["BTCUSDT"],
        exchange_sync_checked=True,
        market_snapshot_override=_watch_snapshot(snapshot_time=utcnow_naive() + timedelta(minutes=1), latest_price=69420.0),
    )
    second_trigger = orchestrator.run_entry_plan_watcher_cycle(
        symbols=["BTCUSDT"],
        exchange_sync_checked=True,
        market_snapshot_override=_watch_snapshot(snapshot_time=utcnow_naive() + timedelta(minutes=2), latest_price=69420.0),
    )
    overview = get_overview(db_session)

    assert first_trigger["results"][0]["plans"][0]["status"] == "triggered"
    assert second_trigger["results"] == []
    assert execution_calls == 1
    assert len(overview.active_entry_plans) == 0


def test_entry_plan_watcher_reports_armed_entry_plan_cadence(monkeypatch, db_session) -> None:
    orchestrator, _ = _arm_plan(monkeypatch, db_session)

    watch_result = orchestrator.run_entry_plan_watcher_cycle(
        symbols=["BTCUSDT"],
        exchange_sync_checked=True,
        market_snapshot_override=_watch_snapshot(
            snapshot_time=utcnow_naive(),
            latest_price=69260.0,
        ),
    )

    assert watch_result["results"][0]["cadence"]["mode"] == "armed_entry_plan"
    assert (
        watch_result["results"][0]["cadence"]["effective_cadence"]["entry_plan_watcher_interval_minutes"]
        == 1
    )
