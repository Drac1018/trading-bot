from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from trading_mvp.database import Base, get_db
from trading_mvp.main import app
from trading_mvp.models import (
    AgentRun,
    FeatureSnapshot,
    MarketSnapshot,
    PnLSnapshot,
    RiskCheck,
    SystemHealthEvent,
)
from trading_mvp.schemas import (
    AppSettingsUpdateRequest,
    BinanceConnectionTestRequest,
    OpenAIConnectionTestRequest,
)
from trading_mvp.services.connectivity import check_binance_connection, check_openai_connection
from trading_mvp.services.runtime_state import mark_sync_success
from trading_mvp.services.settings import (
    get_or_create_settings,
    serialize_settings,
    should_call_openai,
    update_settings,
)
from trading_mvp.time_utils import utcnow_naive


def build_settings_payload() -> AppSettingsUpdateRequest:
    return AppSettingsUpdateRequest(
        live_trading_enabled=True,
        manual_live_approval=True,
        live_approval_window_minutes=15,
        default_symbol="BTCUSDT",
        tracked_symbols=["BTCUSDT", "ETHUSDT"],
        default_timeframe="15m",
        exchange_sync_interval_seconds=60,
        market_refresh_interval_minutes=1,
        position_management_interval_seconds=60,
        schedule_windows=["1h", "4h", "12h", "24h"],
        symbol_cadence_overrides=[
            {
                "symbol": "BTCUSDT",
                "enabled": True,
                "decision_cycle_interval_minutes_override": 5,
                "ai_call_interval_minutes_override": 10,
            }
        ],
        max_leverage=3.0,
        max_risk_per_trade=0.01,
        max_daily_loss=0.02,
        max_consecutive_losses=3,
        max_gross_exposure_pct=3.0,
        max_largest_position_pct=1.5,
        max_directional_bias_pct=2.0,
        max_same_tier_concentration_pct=2.5,
        stale_market_seconds=1800,
        slippage_threshold_pct=0.003,
        adaptive_signal_enabled=True,
        position_management_enabled=True,
        break_even_enabled=True,
        atr_trailing_stop_enabled=True,
        partial_take_profit_enabled=True,
        partial_tp_rr=1.4,
        partial_tp_size_pct=0.3,
        move_stop_to_be_rr=0.9,
        time_stop_enabled=True,
        time_stop_minutes=90,
        time_stop_profit_floor=0.2,
        holding_edge_decay_enabled=True,
        reduce_on_regime_shift_enabled=True,
        starting_equity=100000.0,
        ai_enabled=True,
        ai_provider="openai",
        ai_model="gpt-4.1-mini",
        ai_call_interval_minutes=30,
        decision_cycle_interval_minutes=15,
        ai_max_input_candles=32,
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


def test_settings_update_encrypts_and_masks_secrets(db_session) -> None:
    row = update_settings(db_session, build_settings_payload())
    serialized = serialize_settings(row)

    assert row.openai_api_key_encrypted
    assert row.openai_api_key_encrypted != "sk-test-openai"
    assert serialized["openai_api_key_configured"] is True
    assert serialized["binance_api_key_configured"] is True
    assert serialized["binance_api_secret_configured"] is True
    assert serialized["tracked_symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert serialized["exchange_sync_interval_seconds"] == 60
    assert serialized["market_refresh_interval_minutes"] == 1
    assert serialized["position_management_interval_seconds"] == 60
    assert serialized["adaptive_signal_enabled"] is True
    assert serialized["position_management_enabled"] is True
    assert serialized["break_even_enabled"] is True
    assert serialized["atr_trailing_stop_enabled"] is True
    assert serialized["partial_take_profit_enabled"] is True
    assert serialized["partial_tp_rr"] == 1.4
    assert serialized["partial_tp_size_pct"] == 0.3
    assert serialized["move_stop_to_be_rr"] == 0.9
    assert serialized["time_stop_enabled"] is True
    assert serialized["time_stop_minutes"] == 90
    assert serialized["time_stop_profit_floor"] == 0.2
    assert serialized["holding_edge_decay_enabled"] is True
    assert serialized["reduce_on_regime_shift_enabled"] is True
    assert serialized["operational_status"]["live_execution_ready"] == serialized["live_execution_ready"]
    assert serialized["operational_status"]["approval_armed"] == serialized["approval_armed"]
    assert serialized["estimated_monthly_ai_calls_breakdown"]["trading_decision"] == 5760
    assert serialized["symbol_effective_cadences"][0]["symbol"] == "BTCUSDT"
    assert serialized["symbol_effective_cadences"][0]["estimated_monthly_ai_calls"] == 4320


def test_should_call_openai_respects_manual_and_replay(db_session) -> None:
    row = update_settings(db_session, build_settings_payload())

    assert should_call_openai(db_session, row, "trading_decision", "manual") is True
    assert should_call_openai(db_session, row, "trading_decision", "historical_replay") is False

    db_session.add(
        AgentRun(
            role="trading_decision",
            trigger_event="realtime_cycle",
            schema_name="TradeDecision",
            status="completed",
            provider_name="openai",
            summary="latest decision",
            input_payload={},
            output_payload={},
            metadata_json={"source": "llm"},
            schema_valid=True,
        )
    )
    db_session.flush()

    assert should_call_openai(db_session, row, "trading_decision", "realtime_cycle") is False


def test_should_call_openai_applies_failure_backoff(db_session) -> None:
    row = update_settings(db_session, build_settings_payload())

    db_session.add(
        AgentRun(
            role="trading_decision",
            trigger_event="realtime_cycle",
            schema_name="TradeDecision",
            status="fallback",
            provider_name="deterministic-mock",
            summary="fallback decision",
            input_payload={},
            output_payload={},
            metadata_json={
                "source": "llm_fallback",
                "error": "Client error 400 Bad Request",
            },
            schema_valid=True,
            created_at=utcnow_naive(),
        )
    )
    db_session.flush()

    assert should_call_openai(db_session, row, "trading_decision", "realtime_cycle") is False
    assert should_call_openai(db_session, row, "trading_decision", "manual") is False


def test_serialize_settings_marks_stale_sync_scope_instead_of_leaving_synced(db_session) -> None:
    row = update_settings(db_session, build_settings_payload())
    stale_at = utcnow_naive() - timedelta(hours=2)
    mark_sync_success(row, scope="positions", synced_at=stale_at, detail={"symbol": "BTCUSDT"})
    db_session.add(row)
    db_session.flush()

    serialized = serialize_settings(row)
    positions = serialized["sync_freshness_summary"]["positions"]

    assert positions["raw_status"] == "synced"
    assert positions["status"] == "stale"
    assert positions["last_sync_at"] == stale_at.isoformat()
    assert positions["last_attempt_at"] == stale_at.isoformat()
    assert positions["last_skip_reason"] is None


def test_serialize_settings_reports_recent_ai_usage_metrics(db_session) -> None:
    row = update_settings(db_session, build_settings_payload())
    now = utcnow_naive()
    db_session.add_all(
        [
            AgentRun(
                role="trading_decision",
                trigger_event="realtime_cycle",
                schema_name="TradeDecision",
                status="completed",
                provider_name="openai",
                summary="openai success",
                input_payload={},
                output_payload={},
                metadata_json={
                    "source": "llm",
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
                },
                schema_valid=True,
                created_at=now - timedelta(hours=2),
            ),
            AgentRun(
                role="trading_decision",
                trigger_event="realtime_cycle",
                schema_name="TradeDecision",
                status="fallback",
                provider_name="deterministic-mock",
                summary="openai fallback",
                input_payload={},
                output_payload={},
                metadata_json={
                    "source": "llm_fallback",
                    "error": "Client error 400 Bad Request",
                },
                schema_valid=True,
                created_at=now - timedelta(hours=1),
            ),
            AgentRun(
                role="integration_planner",
                trigger_event="scheduled_review",
                schema_name="IntegrationSuggestionBatch",
                status="completed",
                provider_name="openai",
                summary="planner success",
                input_payload={},
                output_payload={},
                metadata_json={
                    "source": "llm",
                    "usage": {"prompt_tokens": 40, "completion_tokens": 10, "total_tokens": 50},
                },
                schema_valid=True,
                created_at=now - timedelta(days=2),
            ),
        ]
    )
    db_session.flush()

    serialized = serialize_settings(row)

    assert serialized["recent_ai_calls_24h"] == 2
    assert serialized["recent_ai_successes_24h"] == 1
    assert serialized["recent_ai_failures_24h"] == 1
    assert serialized["recent_ai_tokens_24h"]["total_tokens"] == 120
    assert serialized["recent_ai_role_calls_7d"]["integration_planner"] == 1
    assert "BAD_REQUEST x1" in serialized["recent_ai_failure_reasons"]
    assert serialized["observed_monthly_ai_calls_projection"] == 60


def test_connection_services_return_success_with_patched_clients(db_session, monkeypatch) -> None:
    row = update_settings(db_session, build_settings_payload())

    monkeypatch.setattr(
        "trading_mvp.providers.OpenAIProvider.test_connection",
        lambda self: {"ok": True, "model": self.model},
    )
    monkeypatch.setattr(
        "trading_mvp.services.binance.BinanceClient.test_connection",
        lambda self, symbol, timeframe: {"market_data_ok": True, "symbol": symbol, "timeframe": timeframe},
    )

    openai_result = check_openai_connection(
        row,
        OpenAIConnectionTestRequest(api_key=None, model="gpt-4.1-mini"),
    )
    binance_result = check_binance_connection(
        row,
        BinanceConnectionTestRequest(
            api_key=None,
            api_secret=None,
            testnet_enabled=True,
            symbol="BTCUSDT",
            timeframe="15m",
        ),
    )

    assert openai_result.ok is True
    assert binance_result.ok is True
    assert binance_result.details["symbol"] == "BTCUSDT"


def test_get_or_create_settings_provides_new_defaults(db_session) -> None:
    row = get_or_create_settings(db_session)
    serialized = serialize_settings(row)

    assert serialized["exchange_sync_interval_seconds"] == 60
    assert serialized["market_refresh_interval_minutes"] == 1
    assert serialized["position_management_interval_seconds"] == 60
    assert serialized["ai_call_interval_minutes"] >= 5
    assert serialized["decision_cycle_interval_minutes"] >= 1
    assert serialized["tracked_symbols"]
    assert serialized["adaptive_signal_enabled"] is False


def test_serialize_settings_includes_adaptive_signal_summary(db_session) -> None:
    row = update_settings(db_session, build_settings_payload())

    serialized = serialize_settings(row)

    assert serialized["adaptive_signal_summary"]["enabled"] is True
    assert "bounds" in serialized["adaptive_signal_summary"]
    assert serialized["adaptive_signal_summary"]["data_fallback_rule"]


def test_serialize_settings_includes_position_management_summary(db_session) -> None:
    row = update_settings(db_session, build_settings_payload())

    serialized = serialize_settings(row)

    assert serialized["position_management_summary"]["enabled"] is True
    assert serialized["position_management_summary"]["protective_bias"] == "tighten_only"
    assert serialized["position_management_summary"]["rules_enabled"]["break_even"] is True
    assert serialized["position_management_summary"]["rules_enabled"]["time_stop"] is True
    assert serialized["position_management_summary"]["fixed_parameters"]["partial_take_profit_fraction"] == 0.3
    assert serialized["position_management_summary"]["fixed_parameters"]["break_even_trigger_r"] == 0.9
    assert serialized["position_management_summary"]["fixed_parameters"]["time_stop_minutes"] == 90
    assert serialized["position_management_summary"]["data_fallback_rule"]


def test_serialize_settings_merges_global_and_symbol_cadence_overrides(db_session) -> None:
    row = update_settings(db_session, build_settings_payload())

    serialized = serialize_settings(row)
    effective = {item["symbol"]: item for item in serialized["symbol_effective_cadences"]}

    assert effective["BTCUSDT"]["decision_cycle_interval_minutes"] == 5
    assert effective["BTCUSDT"]["ai_call_interval_minutes"] == 10
    assert effective["ETHUSDT"]["decision_cycle_interval_minutes"] == 15
    assert effective["ETHUSDT"]["uses_global_defaults"] is True


def test_serialize_settings_applies_hard_runtime_caps(db_session) -> None:
    row = get_or_create_settings(db_session)
    row.max_leverage = 9.0
    row.max_risk_per_trade = 0.2
    row.max_daily_loss = 0.5
    row.max_gross_exposure_pct = 9.0
    row.max_largest_position_pct = 4.0
    row.max_directional_bias_pct = 5.0
    row.max_same_tier_concentration_pct = 6.0
    db_session.flush()

    serialized = serialize_settings(row)

    assert serialized["max_leverage"] == 5.0
    assert serialized["max_risk_per_trade"] == 0.02
    assert serialized["max_daily_loss"] == 0.05
    assert serialized["max_gross_exposure_pct"] == 3.0
    assert serialized["max_largest_position_pct"] == 1.5
    assert serialized["max_directional_bias_pct"] == 2.0
    assert serialized["max_same_tier_concentration_pct"] == 2.5


def test_update_settings_does_not_change_trading_pause_state(db_session) -> None:
    row = get_or_create_settings(db_session)
    row.trading_paused = True
    db_session.flush()

    updated = update_settings(db_session, build_settings_payload())

    assert updated.trading_paused is True


def test_serialize_settings_includes_pause_and_auto_resume_state(db_session) -> None:
    row = get_or_create_settings(db_session)
    db_session.add(
        RiskCheck(
            symbol="BTCUSDT",
            decision="long",
            allowed=False,
            reason_codes=["TRADING_PAUSED", "LIVE_APPROVAL_REQUIRED"],
            approved_risk_pct=0.0,
            approved_leverage=0.0,
            payload={"reason_codes": ["TRADING_PAUSED", "LIVE_APPROVAL_REQUIRED"]},
        )
    )
    row.trading_paused = True
    row.pause_reason_code = "EXCHANGE_ACCOUNT_STATE_UNAVAILABLE"
    row.pause_origin = "system"
    row.pause_triggered_at = utcnow_naive()
    row.pause_reason_detail = {
        "detail": "account snapshot unavailable",
        "auto_resume": {
            "status": "blocked",
            "blockers": ["MISSING_PROTECTIVE_ORDERS"],
        },
    }
    db_session.flush()

    serialized = serialize_settings(row)

    assert serialized["trading_paused"] is True
    assert serialized["pause_reason_code"] == "EXCHANGE_ACCOUNT_STATE_UNAVAILABLE"
    assert serialized["pause_origin"] == "system"
    assert serialized["auto_resume_status"] == "blocked"
    assert serialized["auto_resume_last_blockers"] == ["MISSING_PROTECTIVE_ORDERS"]
    assert serialized["blocked_reasons"] == ["TRADING_PAUSED", "LIVE_APPROVAL_REQUIRED"]
    assert serialized["latest_blocked_reasons"] == ["TRADING_PAUSED", "LIVE_APPROVAL_REQUIRED"]
    assert serialized["operational_status"]["blocked_reasons"] == serialized["blocked_reasons"]
    assert serialized["operational_status"]["auto_resume_status"] == serialized["auto_resume_status"]
    assert serialized["pause_severity"] == "warning"
    assert serialized["pause_recovery_class"] == "recoverable_system"
    assert serialized["operating_state"] == "PAUSED"
    assert serialized["guard_mode_reason_category"] == "pause"
    assert serialized["guard_mode_reason_code"] == "EXCHANGE_ACCOUNT_STATE_UNAVAILABLE"
    assert serialized["guard_mode_reason_message"] == "거래소 계좌 상태 동기화 실패로 시스템 pause 상태입니다."
    assert serialized["protection_recovery_status"] == "idle"
    assert serialized["missing_protection_symbols"] == []
    assert serialized["missing_protection_items"] == {}


def test_serialize_settings_includes_operational_summary_sections(db_session) -> None:
    row = get_or_create_settings(db_session)
    now = utcnow_naive()
    db_session.add(
        PnLSnapshot(
            snapshot_date=date.today(),
            cash_balance=100250.0,
            equity=100125.0,
            unrealized_pnl=-125.0,
            realized_pnl=250.0,
            daily_pnl=120.0,
            cumulative_pnl=250.0,
            consecutive_losses=1,
            created_at=now - timedelta(minutes=5),
        )
    )
    db_session.add(
        MarketSnapshot(
            symbol="BTCUSDT",
            timeframe="15m",
            snapshot_time=now - timedelta(minutes=4),
            latest_price=71000.0,
            latest_volume=1250.0,
            candle_count=96,
            is_stale=False,
            is_complete=True,
            payload={},
        )
    )
    db_session.flush()
    market_snapshot = db_session.query(MarketSnapshot).order_by(MarketSnapshot.id.desc()).first()
    assert market_snapshot is not None

    db_session.add(
        FeatureSnapshot(
            symbol="BTCUSDT",
            timeframe="15m",
            market_snapshot_id=market_snapshot.id,
            feature_time=now - timedelta(minutes=3),
            trend_score=0.72,
            volatility_pct=0.018,
            volume_ratio=1.2,
            drawdown_pct=0.01,
            rsi=58.0,
            atr=210.0,
            payload={
                "multi_timeframe": {
                    "1h": {"timeframe": "1h"},
                    "4h": {"timeframe": "4h"},
                },
                "regime": {
                    "primary_regime": "bullish",
                    "trend_alignment": "bullish_aligned",
                    "volatility_regime": "normal",
                    "volume_regime": "strong",
                    "momentum_state": "stable",
                },
                "data_quality_flags": [],
            },
        )
    )
    db_session.add(
        SystemHealthEvent(
            component="live_sync",
            status="warning",
            message="Account sync degraded.",
            payload={"reason_code": "EXCHANGE_ACCOUNT_STATE_UNAVAILABLE"},
            created_at=now - timedelta(minutes=1),
        )
    )
    db_session.flush()

    serialized = serialize_settings(row)

    assert serialized["pnl_summary"]["basis"] == "execution_ledger_truth"
    assert serialized["account_sync_summary"]["status"] == "fallback_reconciled"
    assert serialized["operational_status"]["account_sync_summary"]["status"] == "fallback_reconciled"
    assert serialized["operational_status"]["market_freshness_summary"]["symbol"] == "BTCUSDT"
    assert serialized["operational_status"]["market_freshness_summary"]["stale"] is False
    assert serialized["exposure_summary"]["reference_symbol"] == "BTCUSDT"
    assert "entry" in serialized["execution_policy_summary"]
    assert serialized["market_context_summary"]["context_timeframes"] == ["1h", "4h"]
    assert serialized["adaptive_protection_summary"]["mode"] == "adaptive_atr_regime_aware"


def test_serialize_settings_reports_live_readiness_guard_reason(db_session) -> None:
    row = update_settings(db_session, build_settings_payload())
    row.live_execution_armed = False
    row.live_execution_armed_until = None
    db_session.flush()

    serialized = serialize_settings(row)

    assert serialized["guard_mode_reason_category"] == "readiness"
    assert serialized["guard_mode_reason_code"] == "LIVE_APPROVAL_REQUIRED"
    assert serialized["guard_mode_reason_message"] == "실거래 승인 창이 닫혀 있어 가드 모드입니다."


def test_serialize_settings_treats_manual_live_approval_as_indefinite_when_armed(db_session) -> None:
    row = update_settings(db_session, build_settings_payload())
    row.live_execution_armed = True
    row.live_execution_armed_until = None
    db_session.flush()

    serialized = serialize_settings(row)

    assert serialized["live_execution_ready"] is True
    assert serialized["guard_mode_reason_code"] is None


def test_serialize_settings_reports_operating_state_guard_reason(db_session) -> None:
    row = update_settings(db_session, build_settings_payload())
    row.trading_paused = False
    row.pause_reason_code = None
    row.pause_origin = None
    row.pause_triggered_at = None
    row.pause_reason_detail = {
        "operating_state": "PROTECTION_REQUIRED",
        "protection_recovery": {
            "status": "recreating",
            "missing_symbols": ["BTCUSDT"],
            "missing_items": {"BTCUSDT": ["stop_loss", "take_profit"]},
        },
    }
    db_session.flush()

    serialized = serialize_settings(row)

    assert serialized["guard_mode_reason_category"] == "operating_state"
    assert serialized["guard_mode_reason_code"] == "PROTECTION_REQUIRED"
    assert serialized["guard_mode_reason_message"] == "무보호 포지션이 감지되어 보호 복구 우선 상태입니다."


def test_serialize_settings_reports_degraded_manage_only_guard_reason(db_session) -> None:
    row = update_settings(db_session, build_settings_payload())
    row.trading_paused = False
    row.pause_reason_code = None
    row.pause_reason_detail = {
        "operating_state": "DEGRADED_MANAGE_ONLY",
        "protection_recovery": {
            "status": "manage_only",
            "missing_symbols": ["BTCUSDT"],
            "missing_items": {"BTCUSDT": ["take_profit"]},
        },
    }
    db_session.flush()

    serialized = serialize_settings(row)

    assert serialized["guard_mode_reason_category"] == "operating_state"
    assert serialized["guard_mode_reason_code"] == "DEGRADED_MANAGE_ONLY"
    assert serialized["guard_mode_reason_message"] == "보호 복구가 반복 실패해 관리 전용 상태로 가드 모드입니다."


def test_serialize_settings_reports_emergency_exit_guard_reason(db_session) -> None:
    row = update_settings(db_session, build_settings_payload())
    row.trading_paused = False
    row.pause_reason_code = None
    row.pause_reason_detail = {
        "operating_state": "EMERGENCY_EXIT",
        "protection_recovery": {
            "status": "emergency_exit",
            "missing_symbols": ["BTCUSDT"],
            "missing_items": {"BTCUSDT": ["stop_loss"]},
        },
    }
    db_session.flush()

    serialized = serialize_settings(row)

    assert serialized["guard_mode_reason_category"] == "operating_state"
    assert serialized["guard_mode_reason_code"] == "EMERGENCY_EXIT"
    assert serialized["guard_mode_reason_message"] == "비상 청산 상태가 진행 중이라 가드 모드입니다."


def test_pause_resume_endpoints_record_audit_events(tmp_path, monkeypatch) -> None:
    test_engine = create_engine(f"sqlite:///{tmp_path / 'settings_api.db'}", future=True)
    TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=test_engine)
    monkeypatch.setattr("trading_mvp.main.engine", test_engine)

    def override_get_db():
        with TestingSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    try:
        with TestClient(app) as client:
            pause_response = client.post("/api/settings/pause")
            assert pause_response.status_code == 200
            assert pause_response.json()["trading_paused"] is True

            resume_response = client.post("/api/settings/resume")
            assert resume_response.status_code == 200
            assert resume_response.json()["trading_paused"] is False

        with TestingSessionLocal() as session:
            events = session.execute(
                text(
                    """
                select event_type, message
                from audit_events
                order by id asc
                """
                )
            ).all()
            assert ("trading_paused", "Global trading pause enabled.") in events
            assert ("trading_resumed", "Global trading pause cleared.") in events
    finally:
        app.dependency_overrides.clear()


def test_health_endpoint_uses_lifespan_startup(tmp_path, monkeypatch) -> None:
    test_engine = create_engine(f"sqlite:///{tmp_path / 'lifespan_health.db'}", future=True)
    TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr("trading_mvp.main.engine", test_engine)

    def override_get_db():
        with TestingSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    try:
        with TestClient(app) as client:
            response = client.get("/health")
            assert response.status_code == 200
            payload = response.json()
            assert payload["status"] == "ok"
            assert payload["database"] == "ready"
    finally:
        app.dependency_overrides.clear()
