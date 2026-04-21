from __future__ import annotations

from datetime import timedelta
from time import perf_counter, sleep

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
    Position,
    RiskCheck,
    SystemHealthEvent,
)
from trading_mvp.schemas import (
    AppSettingsUpdateRequest,
    BinanceConnectionTestRequest,
    OpenAIConnectionTestRequest,
)
from trading_mvp.services.connectivity import (
    check_binance_connection,
    check_openai_connection,
)
from trading_mvp.services.runtime_state import mark_sync_success, set_drawdown_state_detail
from trading_mvp.services.settings import (
    get_or_create_settings,
    serialize_settings,
    serialize_settings_ai_usage,
    serialize_settings_cadences,
    serialize_settings_view,
    should_call_openai,
    update_settings,
)
from trading_mvp.time_utils import utcnow_naive


def build_settings_payload() -> AppSettingsUpdateRequest:
    return AppSettingsUpdateRequest(
        live_trading_enabled=True,
        rollout_mode="full_live",
        limited_live_max_notional=750.0,
        manual_live_approval=True,
        live_approval_window_minutes=15,
        default_symbol="BTCUSDT",
        tracked_symbols=["BTCUSDT", "ETHUSDT"],
        default_timeframe="15m",
        exchange_sync_interval_seconds=60,
        market_refresh_interval_minutes=1,
        position_management_interval_seconds=60,
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
        event_source_provider="fred",
        event_source_api_url="https://fred.settings/fred",
        event_source_timeout_seconds=12.0,
        event_source_default_assets=["BTCUSDT", "ETHUSDT"],
        event_source_fred_release_ids=[10, 101],
        event_source_bls_enrichment_url="https://bls.settings/releases",
        event_source_bls_enrichment_static_params={"series_id": "CUUR0000SA0"},
        event_source_bea_enrichment_url="https://bea.settings/releases",
        event_source_bea_enrichment_static_params={"dataset": "NIPA"},
        openai_api_key="sk-test-openai",
        binance_api_key="binance-key",
        binance_api_secret="binance-secret",
        event_source_api_key="fred-key",
        clear_openai_api_key=False,
        clear_binance_api_key=False,
        clear_binance_api_secret=False,
        clear_event_source_api_key=False,
    )


def test_settings_update_encrypts_and_masks_secrets(db_session) -> None:
    row = update_settings(db_session, build_settings_payload())
    serialized = serialize_settings(row)

    assert row.openai_api_key_encrypted
    assert row.openai_api_key_encrypted != "sk-test-openai"
    assert serialized["openai_api_key_configured"] is True
    assert serialized["binance_api_key_configured"] is True
    assert serialized["binance_api_secret_configured"] is True
    assert row.event_source_api_key_encrypted
    assert row.event_source_api_key_encrypted != "fred-key"
    assert serialized["event_source_provider"] == "fred"
    assert serialized["event_source_api_url"] == "https://fred.settings/fred"
    assert serialized["event_source_timeout_seconds"] == 12.0
    assert serialized["event_source_default_assets"] == ["BTCUSDT", "ETHUSDT"]
    assert serialized["event_source_fred_release_ids"] == [10, 101]
    assert serialized["event_source_bls_enrichment_url"] == "https://bls.settings/releases"
    assert serialized["event_source_bls_enrichment_static_params"] == {"series_id": "CUUR0000SA0"}
    assert serialized["event_source_bea_enrichment_url"] == "https://bea.settings/releases"
    assert serialized["event_source_bea_enrichment_static_params"] == {"dataset": "NIPA"}
    assert serialized["event_source_api_key_configured"] is True
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
    assert serialized["rollout_mode"] == "full_live"
    assert serialized["exchange_submit_allowed"] is True
    assert serialized["limited_live_max_notional"] == 750.0
    assert serialized["operational_status"]["live_execution_ready"] == serialized["live_execution_ready"]
    assert serialized["operational_status"]["rollout_mode"] == "full_live"
    assert serialized["operational_status"]["approval_armed"] == serialized["approval_armed"]
    assert serialized["symbol_effective_cadences"][0]["symbol"] == "BTCUSDT"
    assert "estimated_monthly_ai_calls" not in serialized
    assert "estimated_monthly_ai_calls" not in serialized["symbol_effective_cadences"][0]
    assert "starting_equity" not in serialized


def test_settings_update_preserves_event_source_fields_when_older_payload_omits_them(db_session) -> None:
    row = update_settings(db_session, build_settings_payload())
    payload_data = build_settings_payload().model_dump()
    for key in {
        "event_source_provider",
        "event_source_api_url",
        "event_source_timeout_seconds",
        "event_source_default_assets",
        "event_source_fred_release_ids",
        "event_source_bls_enrichment_url",
        "event_source_bls_enrichment_static_params",
        "event_source_bea_enrichment_url",
        "event_source_bea_enrichment_static_params",
        "event_source_api_key",
        "clear_event_source_api_key",
    }:
        payload_data.pop(key, None)

    updated = update_settings(db_session, AppSettingsUpdateRequest(**payload_data))

    assert updated.event_source_provider == "fred"
    assert updated.event_source_api_url == "https://fred.settings/fred"
    assert updated.event_source_timeout_seconds == 12.0
    assert updated.event_source_default_assets == ["BTCUSDT", "ETHUSDT"]
    assert updated.event_source_fred_release_ids == [10, 101]
    assert updated.event_source_bls_enrichment_url == "https://bls.settings/releases"
    assert updated.event_source_bls_enrichment_static_params == {"series_id": "CUUR0000SA0"}
    assert updated.event_source_bea_enrichment_url == "https://bea.settings/releases"
    assert updated.event_source_bea_enrichment_static_params == {"dataset": "NIPA"}
    assert updated.event_source_api_key_encrypted == row.event_source_api_key_encrypted


def test_serialize_settings_view_removes_dead_and_heavy_fields(db_session) -> None:
    row = update_settings(db_session, build_settings_payload())

    serialized = serialize_settings_view(row)

    assert "schedule_windows" not in serialized
    assert "symbol_effective_cadences" not in serialized
    assert "recent_ai_calls_24h" not in serialized
    assert "pnl_summary" not in serialized
    assert serialized["default_symbol"] == "BTCUSDT"
    assert serialized["control_status_summary"]["rollout_mode"] == "full_live"
    assert serialized["event_source_provider"] == "fred"
    assert serialized["event_source_bls_enrichment_url"] == "https://bls.settings/releases"
    assert serialized["event_source_bea_enrichment_url"] == "https://bea.settings/releases"
    assert serialized["event_source_api_key_configured"] is True


def test_settings_auxiliary_serializers_expose_cadences_and_ai_usage(db_session) -> None:
    row = update_settings(db_session, build_settings_payload())
    db_session.add(
        AgentRun(
            role="trading_decision",
            trigger_event="entry_candidate_event",
            schema_name="TradeDecision",
            provider_name="openai",
            summary="decision",
            input_payload={"market_snapshot": {"symbol": "BTCUSDT"}},
            output_payload={"decision": "hold"},
            metadata_json={
                "source": "llm",
                "usage": {"prompt_tokens": 40, "completion_tokens": 10, "total_tokens": 50},
            },
        )
    )
    db_session.flush()

    cadences = serialize_settings_cadences(row)
    usage = serialize_settings_ai_usage(row)

    assert cadences["items"][0]["symbol"] == "BTCUSDT"
    assert usage["recent_ai_calls_24h"] == 1
    assert usage["manual_ai_guard_minutes"] == 5


def test_serialize_settings_reports_unknown_live_snapshot_without_synthetic_equity(db_session) -> None:
    row = update_settings(db_session, build_settings_payload())

    serialized = serialize_settings(row)

    assert "starting_equity" not in serialized
    assert serialized["pnl_summary"]["account_snapshot_available"] is False
    assert serialized["pnl_summary"]["basis"] == "live_account_snapshot_unavailable"
    assert serialized["pnl_summary"]["equity"] is None
    assert serialized["pnl_summary"]["wallet_balance"] is None
    assert serialized["account_sync_summary"]["account_snapshot_available"] is False
    assert serialized["account_sync_summary"]["status"] == "unknown"
    assert serialized["account_sync_summary"]["equity"] is None


def test_serialize_settings_exposes_rollout_mode_and_submit_gate(db_session) -> None:
    row = update_settings(
        db_session,
        build_settings_payload().model_copy(
            update={
                "rollout_mode": "shadow",
                "limited_live_max_notional": 250.0,
            }
        ),
    )
    row.live_execution_armed = True
    row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    db_session.add(row)
    db_session.flush()

    serialized = serialize_settings(row)

    assert serialized["live_trading_enabled"] is True
    assert serialized["rollout_mode"] == "shadow"
    assert serialized["exchange_submit_allowed"] is False
    assert serialized["limited_live_max_notional"] == 250.0
    assert serialized["live_execution_ready"] is True
    assert serialized["can_enter_new_position"] is False
    assert serialized["guard_mode_reason_code"] == "ROLLOUT_MODE_SHADOW"
    assert serialized["operational_status"]["rollout_mode"] == "shadow"
    assert serialized["operational_status"]["exchange_submit_allowed"] is False
    assert serialized["operational_status"]["control_status_summary"]["rollout_mode"] == "shadow"


def test_serialize_settings_exposes_drawdown_operating_layer(db_session) -> None:
    row = update_settings(db_session, build_settings_payload())
    set_drawdown_state_detail(
        row,
        {
            "current_drawdown_state": "caution",
            "entered_at": utcnow_naive().isoformat(),
            "transition_reason": "consecutive_losses_warning",
            "policy_adjustments": {
                "risk_pct_multiplier": 0.75,
                "leverage_multiplier": 0.85,
                "notional_multiplier": 0.8,
                "max_non_priority_selected": 2,
                "entry_capacity_multiplier": 0.75,
                "winner_only_pyramiding": True,
                "breakout_exception_allowed": False,
            },
        },
    )
    db_session.add(row)
    db_session.flush()

    serialized = serialize_settings(row)

    assert serialized["operational_status"]["current_drawdown_state"] == "caution"
    assert serialized["operational_status"]["drawdown_transition_reason"] == "consecutive_losses_warning"
    assert serialized["operational_status"]["drawdown_policy_adjustments"]["risk_pct_multiplier"] == 0.75
    assert serialized["operational_status"]["control_status_summary"]["current_drawdown_state"] == "caution"
    assert serialized["operational_status"]["control_status_summary"]["drawdown_policy_adjustments"]["breakout_exception_allowed"] is False


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
                role="chief_review",
                trigger_event="realtime_cycle",
                schema_name="ChiefReviewSummary",
                status="completed",
                provider_name="openai",
                summary="chief success",
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
    assert serialized["recent_ai_role_calls_7d"]["chief_review"] == 1
    assert "estimated_monthly_ai_calls_breakdown" not in serialized
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


def test_serialize_settings_includes_holding_profile_and_hard_stop_position_summary(db_session) -> None:
    row = update_settings(db_session, build_settings_payload())
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.02,
            entry_price=70000.0,
            mark_price=70450.0,
            leverage=2.0,
            stop_loss=69200.0,
            take_profit=71500.0,
            metadata_json={
                "position_management": {
                    "holding_profile": "position",
                    "holding_profile_reason": "strong_structural_regime_position_allowed",
                    "initial_stop_type": "deterministic_hard_stop",
                    "hard_stop_active": True,
                    "stop_widening_allowed": False,
                }
            },
        )
    )
    db_session.flush()

    serialized = serialize_settings(row)

    assert serialized["position_management_summary"]["active_positions"] == 1
    assert serialized["position_management_summary"]["managed_positions_with_baseline"] == 1
    assert serialized["position_management_summary"]["active_holding_profiles"]["position"] == 1
    assert serialized["position_management_summary"]["hard_stop_active_positions"] == 1
    assert serialized["position_management_summary"]["deterministic_hard_stop_positions"] == 1
    assert serialized["position_management_summary"]["stop_widening_forbidden_positions"] == 1


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
            snapshot_date=utcnow_naive().date(),
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

    assert serialized["pnl_summary"]["basis"] == "live_account_snapshot_preferred"
    assert "wallet_balance" in serialized["pnl_summary"]
    assert "available_balance" in serialized["pnl_summary"]
    assert "fee_total" in serialized["pnl_summary"]
    assert "funding_total" in serialized["pnl_summary"]
    assert "net_pnl" in serialized["pnl_summary"]
    assert serialized["account_sync_summary"]["status"] in {"exchange_synced", "fallback_reconciled"}
    assert "wallet_balance" in serialized["account_sync_summary"]
    assert "available_balance" in serialized["account_sync_summary"]
    assert "fee_total" in serialized["account_sync_summary"]
    assert "funding_total" in serialized["account_sync_summary"]
    assert "net_pnl" in serialized["account_sync_summary"]
    assert serialized["operational_status"]["account_sync_summary"]["status"] in {
        "exchange_synced",
        "fallback_reconciled",
    }
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


def test_settings_api_splits_heavy_payloads(testclient_db_factory) -> None:
    TestingSessionLocal = testclient_db_factory("settings_view_split.db")

    with TestingSessionLocal() as session:
        update_settings(session, build_settings_payload())
        session.add(
            AgentRun(
                role="trading_decision",
                trigger_event="entry_candidate_event",
                schema_name="TradeDecision",
                provider_name="openai",
                summary="decision",
                input_payload={"market_snapshot": {"symbol": "BTCUSDT"}},
                output_payload={"decision": "hold"},
                metadata_json={
                    "source": "llm",
                    "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
                },
            )
        )
        session.commit()

    with TestClient(app) as client:
        view_response = client.get("/api/settings")
        cadence_response = client.get("/api/settings/cadences")
        usage_response = client.get("/api/settings/ai-usage")

    assert view_response.status_code == 200
    assert cadence_response.status_code == 200
    assert usage_response.status_code == 200
    assert "symbol_effective_cadences" not in view_response.json()
    assert "recent_ai_calls_24h" not in view_response.json()
    assert cadence_response.json()["items"][0]["symbol"] == "BTCUSDT"
    assert usage_response.json()["recent_ai_calls_24h"] == 1



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


def test_health_endpoint_is_not_blocked_by_slow_background_ticks(tmp_path, monkeypatch) -> None:
    test_engine = create_engine(f"sqlite:///{tmp_path / 'lifespan_background_ticks.db'}", future=True)
    TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr("trading_mvp.main.engine", test_engine)

    def override_get_db():
        with TestingSessionLocal() as session:
            yield session

    def slow_scheduler_tick() -> int:
        sleep(2.0)
        return 15

    def slow_user_stream_tick() -> int:
        sleep(2.0)
        return 5

    monkeypatch.setattr("trading_mvp.main._run_background_scheduler_tick", slow_scheduler_tick)
    monkeypatch.setattr("trading_mvp.main._run_background_user_stream_tick", slow_user_stream_tick)
    app.dependency_overrides[get_db] = override_get_db

    try:
        started_at = perf_counter()
        with TestClient(app) as client:
            response = client.get("/health")
        elapsed = perf_counter() - started_at

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        # Startup should not wait for both 2s background ticks to finish serially.
        assert elapsed < 3.2
    finally:
        app.dependency_overrides.clear()


def test_review_api_rejects_out_of_scope_windows(tmp_path, monkeypatch) -> None:
    test_engine = create_engine(f"sqlite:///{tmp_path / 'review_api.db'}", future=True)
    TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=test_engine)
    monkeypatch.setattr("trading_mvp.main.engine", test_engine)

    def override_get_db():
        with TestingSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    try:
        with TestClient(app) as client:
            response = client.post("/api/reviews/24h")
            assert response.status_code == 400
            assert "Only 1h review window is enabled" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()
