from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from trading_mvp.database import Base, get_db
from trading_mvp.main import app
from trading_mvp.models import AuditEvent, PnLSnapshot, Position, SchedulerRun
from trading_mvp.schemas import AppSettingsUpdateRequest
from trading_mvp.services.orchestrator import TradingOrchestrator
from trading_mvp.services.pause_control import attempt_auto_resume
from trading_mvp.services.scheduler import run_interval_decision_cycle
from trading_mvp.services.settings import (
    arm_live_execution,
    get_or_create_settings,
    set_trading_pause,
    update_settings,
)
from trading_mvp.time_utils import utcnow_naive


def _build_live_settings_payload() -> AppSettingsUpdateRequest:
    return AppSettingsUpdateRequest(
        live_trading_enabled=True,
        manual_live_approval=True,
        live_approval_window_minutes=15,
        default_symbol="BTCUSDT",
        tracked_symbols=["BTCUSDT"],
        default_timeframe="15m",
        schedule_windows=["1h", "4h", "12h", "24h"],
        max_leverage=3.0,
        max_risk_per_trade=0.01,
        max_daily_loss=0.02,
        max_consecutive_losses=3,
        stale_market_seconds=1800,
        slippage_threshold_pct=0.003,
        starting_equity=100000.0,
        ai_enabled=True,
        ai_provider="openai",
        ai_model="gpt-4.1-mini",
        ai_call_interval_minutes=30,
        decision_cycle_interval_minutes=15,
        ai_max_input_candles=16,
        ai_temperature=0.1,
        binance_market_data_enabled=True,
        binance_testnet_enabled=True,
        binance_futures_enabled=True,
        openai_api_key="sk-test-openai",
        binance_api_key="binance-key",
        binance_api_secret="binance-secret",
        clear_openai_api_key=False,
        clear_binance_api_key=False,
        clear_binance_api_secret=False,
    )


def _prime_live_ready(db_session, monkeypatch):
    settings_row = update_settings(db_session, _build_live_settings_payload())
    arm_live_execution(db_session, 15)
    monkeypatch.setattr(
        "trading_mvp.services.pause_control.get_settings",
        lambda: SimpleNamespace(
            live_trading_env_enabled=True,
            exchange_recv_window_ms=5000,
            app_secret_seed="change-me-local-dev-secret",
        ),
    )
    return settings_row


class _HealthyClient:
    def __init__(
        self,
        *,
        open_orders: list[dict[str, object]] | None = None,
        positions: list[dict[str, object]] | None = None,
        available_balance: str = "100.0",
    ) -> None:
        self._open_orders = open_orders or []
        self._positions = positions or []
        self._available_balance = available_balance

    def get_account_info(self) -> dict[str, object]:
        return {
            "availableBalance": self._available_balance,
            "totalWalletBalance": self._available_balance,
            "totalUnrealizedProfit": "0.0",
            "totalMarginBalance": self._available_balance,
        }

    def fetch_klines(self, symbol: str, interval: str, limit: int = 2):
        now = utcnow_naive()
        return [
            SimpleNamespace(timestamp=now - timedelta(minutes=15)),
            SimpleNamespace(timestamp=now),
        ]

    def get_open_orders(self, symbol: str) -> list[dict[str, object]]:
        return self._open_orders

    def get_position_information(self, symbol: str) -> list[dict[str, object]]:
        return self._positions


def test_auto_resume_succeeds_for_recoverable_pause_when_state_is_safe(db_session, monkeypatch) -> None:
    _prime_live_ready(db_session, monkeypatch)
    paused = set_trading_pause(
        db_session,
        True,
        reason_code="EXCHANGE_ACCOUNT_STATE_UNAVAILABLE",
        reason_detail={"source": "exchange"},
        pause_origin="system",
        auto_resume_after=utcnow_naive() - timedelta(minutes=1),
        preserve_live_arm=True,
    )
    monkeypatch.setattr("trading_mvp.services.pause_control._build_client", lambda settings: _HealthyClient())

    result = attempt_auto_resume(db_session, paused, trigger_source="test")

    refreshed = get_or_create_settings(db_session)
    assert result["status"] == "resumed"
    assert result["resumed"] is True
    assert refreshed.trading_paused is False


def test_auto_resume_waits_for_cooldown_when_delay_not_reached(db_session, monkeypatch) -> None:
    _prime_live_ready(db_session, monkeypatch)
    paused = set_trading_pause(
        db_session,
        True,
        reason_code="EXCHANGE_ACCOUNT_STATE_UNAVAILABLE",
        reason_detail={"source": "exchange"},
        pause_origin="system",
        auto_resume_after=utcnow_naive() + timedelta(minutes=3),
        preserve_live_arm=True,
    )

    result = attempt_auto_resume(db_session, paused, trigger_source="test")

    assert result["status"] == "waiting_cooldown"
    assert get_or_create_settings(db_session).trading_paused is True


def test_auto_resume_blocks_when_open_position_has_no_protective_orders(db_session, monkeypatch) -> None:
    _prime_live_ready(db_session, monkeypatch)
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
            realized_pnl=0.0,
            unrealized_pnl=1.0,
            metadata_json={},
        )
    )
    db_session.flush()
    paused = set_trading_pause(
        db_session,
        True,
        reason_code="EXCHANGE_POSITION_SYNC_FAILED",
        reason_detail={"source": "exchange"},
        pause_origin="system",
        auto_resume_after=utcnow_naive() - timedelta(minutes=1),
        preserve_live_arm=True,
    )
    monkeypatch.setattr(
        "trading_mvp.services.pause_control._build_client",
        lambda settings: _HealthyClient(
            positions=[
                {
                    "positionAmt": "0.01",
                    "entryPrice": "70000",
                    "markPrice": "70100",
                    "leverage": "2",
                }
            ],
            open_orders=[],
        ),
    )

    result = attempt_auto_resume(db_session, paused, trigger_source="test")

    assert result["status"] == "blocked"
    assert "MISSING_PROTECTIVE_ORDERS" in result["blockers"]
    assert result["symbol_blockers"]["BTCUSDT"] == ["MISSING_PROTECTIVE_ORDERS"]
    assert result["protective_orders"]["BTCUSDT"] == "missing"
    assert any(
        item["code"] == "MISSING_PROTECTIVE_ORDERS" and item.get("symbol") == "BTCUSDT"
        for item in result["blocker_details"]
    )
    assert get_or_create_settings(db_session).trading_paused is True


def test_auto_resume_blocks_when_daily_loss_limit_is_reached(db_session, monkeypatch) -> None:
    settings_row = _prime_live_ready(db_session, monkeypatch)
    db_session.add(
        PnLSnapshot(
            snapshot_date=utcnow_naive().date(),
            equity=100.0,
            cash_balance=100.0,
            realized_pnl=-20.0,
            unrealized_pnl=0.0,
            daily_pnl=-20.0,
            cumulative_pnl=-20.0,
            consecutive_losses=1,
        )
    )
    db_session.flush()
    paused = set_trading_pause(
        db_session,
        True,
        reason_code="TEMPORARY_SYNC_FAILURE",
        reason_detail={"source": "sync"},
        pause_origin="system",
        auto_resume_after=utcnow_naive() - timedelta(minutes=1),
        preserve_live_arm=True,
    )
    monkeypatch.setattr("trading_mvp.services.pause_control._build_client", lambda settings: _HealthyClient())

    result = attempt_auto_resume(db_session, paused, trigger_source="test")

    assert settings_row.id == paused.id
    assert result["status"] == "blocked"
    assert "DAILY_LOSS_LIMIT_REACHED" in result["blockers"]
    assert get_or_create_settings(db_session).trading_paused is True


def test_manual_pause_is_never_auto_resumed(db_session, monkeypatch) -> None:
    _prime_live_ready(db_session, monkeypatch)
    paused = set_trading_pause(
        db_session,
        True,
        reason_code="MANUAL_USER_REQUEST",
        reason_detail={"source": "api"},
        pause_origin="manual",
    )

    result = attempt_auto_resume(db_session, paused, trigger_source="test")

    assert result["status"] == "not_eligible"
    assert get_or_create_settings(db_session).trading_paused is True


def test_protective_order_failure_is_never_auto_resumed(db_session, monkeypatch) -> None:
    _prime_live_ready(db_session, monkeypatch)
    paused = set_trading_pause(
        db_session,
        True,
        reason_code="PROTECTIVE_ORDER_FAILURE",
        reason_detail={"symbol": "BTCUSDT"},
        pause_origin="system",
    )

    result = attempt_auto_resume(db_session, paused, trigger_source="test")

    assert result["status"] == "not_eligible"
    assert get_or_create_settings(db_session).trading_paused is True


def test_scheduler_path_attempts_auto_resume_before_interval_cycle(db_session, monkeypatch) -> None:
    update_settings(db_session, _build_live_settings_payload())
    monkeypatch.setattr(
        "trading_mvp.services.scheduler.attempt_auto_resume",
        lambda session, settings_row, trigger_source="system": {
            "status": "resumed",
            "resumed": True,
            "allowed": True,
            "blockers": [],
            "trigger_source": trigger_source,
        },
    )
    monkeypatch.setattr(
        TradingOrchestrator,
        "run_selected_symbols_cycle",
        lambda self, **kwargs: {"status": "ok", "results": [], "mode": "ai_active"},
    )

    result = run_interval_decision_cycle(db_session, triggered_by="scheduler")
    scheduler_run = db_session.scalar(select(SchedulerRun).order_by(SchedulerRun.id.desc()).limit(1))

    assert result["auto_resume"]["status"] == "resumed"
    assert scheduler_run is not None
    assert scheduler_run.status == "success"


def test_manual_cycle_api_attempts_auto_resume_before_running(tmp_path, monkeypatch) -> None:
    test_engine = create_engine(f"sqlite:///{tmp_path / 'auto_resume_cycle.db'}", future=True)
    testing_session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=test_engine)
    monkeypatch.setattr("trading_mvp.main.engine", test_engine)

    def override_get_db():
        with testing_session() as session:
            yield session

    call_state = {"count": 0}

    def fake_attempt(session, settings_row, trigger_source="system"):
        call_state["count"] += 1
        return {
            "status": "resumed",
            "resumed": True,
            "allowed": True,
            "blockers": [],
            "trigger_source": trigger_source,
        }

    def fake_run_decision_cycle(self, symbol=None, **kwargs):
        return {"symbol": symbol, "status": "ok", "auto_resume": None}

    monkeypatch.setattr("trading_mvp.services.orchestrator.attempt_auto_resume", fake_attempt)
    monkeypatch.setattr(TradingOrchestrator, "run_decision_cycle", fake_run_decision_cycle)
    app.dependency_overrides[get_db] = override_get_db

    try:
        with testing_session() as session:
            update_settings(session, _build_live_settings_payload())
            session.commit()

        with TestClient(app) as client:
            response = client.post("/api/cycles/run")
            assert response.status_code == 200
            payload = response.json()
            assert payload["auto_resume"]["status"] == "resumed"
            assert call_state["count"] == 1
    finally:
        app.dependency_overrides.clear()


def test_recoverable_system_pause_can_resume_via_approval_grace_window(db_session, monkeypatch) -> None:
    _prime_live_ready(db_session, monkeypatch)
    paused = set_trading_pause(
        db_session,
        True,
        reason_code="EXCHANGE_CONNECTIVITY_TEMPORARY_FAILURE",
        reason_detail={"source": "network"},
        pause_origin="system",
        auto_resume_after=utcnow_naive() - timedelta(minutes=1),
        preserve_live_arm=False,
    )
    assert paused.live_execution_armed is False
    monkeypatch.setattr("trading_mvp.services.pause_control._build_client", lambda settings: _HealthyClient())

    result = attempt_auto_resume(db_session, paused, trigger_source="test")

    refreshed = get_or_create_settings(db_session)
    assert result["status"] == "resumed"
    assert refreshed.trading_paused is False
    assert refreshed.live_execution_armed is True
    assert refreshed.live_execution_armed_until is not None


def test_auto_resume_attempts_are_written_to_audit_log(db_session, monkeypatch) -> None:
    _prime_live_ready(db_session, monkeypatch)
    paused = set_trading_pause(
        db_session,
        True,
        reason_code="TEMPORARY_MARKET_DATA_FAILURE",
        reason_detail={"source": "market"},
        pause_origin="system",
        auto_resume_after=utcnow_naive() - timedelta(minutes=1),
        preserve_live_arm=True,
    )
    monkeypatch.setattr("trading_mvp.services.pause_control._build_client", lambda settings: _HealthyClient())

    attempt_auto_resume(db_session, paused, trigger_source="test")

    events = list(
        db_session.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.event_type.in_(
                    [
                        "trading_auto_resume_attempted",
                        "trading_auto_resumed",
                    ]
                )
            )
            .order_by(AuditEvent.id)
        )
    )
    assert [item.event_type for item in events] == [
        "trading_auto_resume_attempted",
        "trading_auto_resumed",
    ]


def test_auto_resume_reports_symbol_detail_for_market_data_failure(db_session, monkeypatch) -> None:
    _prime_live_ready(db_session, monkeypatch)
    paused = set_trading_pause(
        db_session,
        True,
        reason_code="TEMPORARY_MARKET_DATA_FAILURE",
        reason_detail={"source": "market"},
        pause_origin="system",
        auto_resume_after=utcnow_naive() - timedelta(minutes=1),
        preserve_live_arm=True,
    )

    class FailingMarketClient(_HealthyClient):
        def fetch_klines(self, symbol: str, interval: str, limit: int = 2):
            raise RuntimeError("market data unavailable")

    monkeypatch.setattr("trading_mvp.services.pause_control._build_client", lambda settings: FailingMarketClient())

    result = attempt_auto_resume(db_session, paused, trigger_source="test")

    assert result["status"] == "blocked"
    assert "TEMPORARY_MARKET_DATA_FAILURE" in result["blockers"]
    assert result["symbol_blockers"]["BTCUSDT"] == ["TEMPORARY_MARKET_DATA_FAILURE"]
    assert result["market_data_status"]["BTCUSDT"] == "unavailable"
    assert any(
        item["code"] == "TEMPORARY_MARKET_DATA_FAILURE" and item.get("symbol") == "BTCUSDT"
        for item in result["blocker_details"]
    )


def test_live_sync_runs_auto_resume_precheck_before_sync(tmp_path, monkeypatch) -> None:
    test_engine = create_engine(f"sqlite:///{tmp_path / 'live_sync_precheck.db'}", future=True)
    testing_session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=test_engine)
    monkeypatch.setattr("trading_mvp.main.engine", test_engine)

    def override_get_db():
        with testing_session() as session:
            yield session

    call_order: list[str] = []

    def fake_attempt(db, settings_row, trigger_source="system"):
        call_order.append(trigger_source)
        return {
            "attempted": True,
            "resumed": False,
            "allowed": False,
            "status": "blocked",
            "reason_code": settings_row.pause_reason_code,
            "pause_origin": settings_row.pause_origin,
            "pause_severity": "warning",
            "pause_recovery_class": "recoverable_system",
            "trigger_source": trigger_source,
            "blockers": ["LIVE_APPROVAL_REQUIRED"],
            "symbol_blockers": {},
            "blocker_details": [],
            "evaluated_symbols": ["BTCUSDT"],
            "protective_orders": {},
            "market_data_status": {},
            "sync_status": {},
            "approval_state": "required",
        }

    def fake_sync(db, settings_row, symbol=None):
        call_order.append("sync")
        return {
            "symbols": ["BTCUSDT"],
            "synced_orders": 0,
            "synced_positions": 0,
            "equity": 100.0,
            "operating_state": "PROTECTION_REQUIRED",
            "protection_recovery_status": "recreating",
            "protection_recovery_active": True,
            "missing_protection_symbols": ["BTCUSDT"],
            "missing_protection_items": {"BTCUSDT": ["stop_loss"]},
            "symbol_protection_state": {
                "BTCUSDT": {
                    "status": "missing",
                    "protected": False,
                    "protective_order_count": 0,
                    "has_stop_loss": False,
                    "has_take_profit": True,
                    "missing_components": ["stop_loss"],
                }
            },
            "unprotected_positions": ["BTCUSDT"],
            "emergency_actions_taken": [],
        }

    monkeypatch.setattr("trading_mvp.main.attempt_auto_resume", fake_attempt)
    monkeypatch.setattr("trading_mvp.main.sync_live_state", fake_sync)
    app.dependency_overrides[get_db] = override_get_db

    try:
        with testing_session() as session:
            update_settings(session, _build_live_settings_payload())
            set_trading_pause(
                session,
                True,
                reason_code="EXCHANGE_ACCOUNT_STATE_UNAVAILABLE",
                reason_detail={"source": "exchange"},
                pause_origin="system",
                auto_resume_after=utcnow_naive() - timedelta(minutes=1),
                preserve_live_arm=True,
            )
            session.commit()

        with TestClient(app) as client:
            response = client.post("/api/live/sync")
            assert response.status_code == 200
            payload = response.json()
            assert payload["auto_resume_precheck"]["trigger_source"] == "api_live_sync_precheck"
            assert payload["auto_resume_postcheck"]["trigger_source"] == "api_live_sync_postcheck"
            assert payload["operating_state"] == "PROTECTION_REQUIRED"
            assert payload["protection_recovery_status"] == "recreating"
            assert payload["missing_protection_items"] == {"BTCUSDT": ["stop_loss"]}
            assert call_order == ["api_live_sync_precheck", "sync", "api_live_sync_postcheck"]
    finally:
        app.dependency_overrides.clear()


def test_live_sync_failure_still_returns_precheck_result(tmp_path, monkeypatch) -> None:
    test_engine = create_engine(f"sqlite:///{tmp_path / 'live_sync_failure.db'}", future=True)
    testing_session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=test_engine)
    monkeypatch.setattr("trading_mvp.main.engine", test_engine)

    def override_get_db():
        with testing_session() as session:
            yield session

    def fake_attempt(db, settings_row, trigger_source="system"):
        return {
            "attempted": True,
            "resumed": False,
            "allowed": False,
            "status": "blocked",
            "reason_code": settings_row.pause_reason_code,
            "pause_origin": settings_row.pause_origin,
            "pause_severity": "warning",
            "pause_recovery_class": "recoverable_system",
            "trigger_source": trigger_source,
            "blockers": ["MISSING_PROTECTIVE_ORDERS"],
            "symbol_blockers": {"BTCUSDT": ["MISSING_PROTECTIVE_ORDERS"]},
            "blocker_details": [{"code": "MISSING_PROTECTIVE_ORDERS", "symbol": "BTCUSDT"}],
            "evaluated_symbols": ["BTCUSDT"],
            "protective_orders": {"BTCUSDT": "missing"},
            "market_data_status": {"BTCUSDT": "ok"},
            "sync_status": {"BTCUSDT": "protective_orders_missing"},
            "approval_state": "armed",
        }

    def fail_sync(db, settings_row, symbol=None):
        raise RuntimeError("sync exploded")

    monkeypatch.setattr("trading_mvp.main.attempt_auto_resume", fake_attempt)
    monkeypatch.setattr("trading_mvp.main.sync_live_state", fail_sync)
    app.dependency_overrides[get_db] = override_get_db

    try:
        with testing_session() as session:
            update_settings(session, _build_live_settings_payload())
            set_trading_pause(
                session,
                True,
                reason_code="EXCHANGE_ACCOUNT_STATE_UNAVAILABLE",
                reason_detail={"source": "exchange"},
                pause_origin="system",
                auto_resume_after=utcnow_naive() - timedelta(minutes=1),
                preserve_live_arm=True,
            )
            session.commit()

        with TestClient(app) as client:
            response = client.post("/api/live/sync")
            assert response.status_code == 400
            detail = response.json()["detail"]
            assert detail["auto_resume_precheck"]["symbol_blockers"]["BTCUSDT"] == ["MISSING_PROTECTIVE_ORDERS"]

        with testing_session() as session:
            event = session.scalar(
                select(AuditEvent)
                .where(AuditEvent.event_type == "live_sync_failed")
                .order_by(AuditEvent.id.desc())
                .limit(1)
            )
            assert event is not None
            assert event.payload["auto_resume_precheck"]["blockers"] == ["MISSING_PROTECTIVE_ORDERS"]
    finally:
        app.dependency_overrides.clear()
