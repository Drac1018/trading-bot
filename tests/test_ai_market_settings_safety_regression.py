from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from trading_mvp.config import get_settings
from trading_mvp.models import AuditEvent, PnLSnapshot
from trading_mvp.providers import ProviderResult
from trading_mvp.schemas import AIMarketSettingsRecommendation, TradeDecision
from trading_mvp.services.agents import (
    MarketSettingsAdvisorAgent,
    build_market_settings_advisor_input_payload,
)
from trading_mvp.services.features import compute_features
from trading_mvp.services.market_data import build_market_snapshot
from trading_mvp.services.orchestrator import TradingOrchestrator
from trading_mvp.services.risk import (
    EXECUTION_RISK_PROFILE_BLOCK_REASON_CODE,
    PROFILE_RELAXATION_CONSECUTIVE_REASON_CODE,
    evaluate_risk,
    select_safe_execution_risk_profile,
)
from trading_mvp.services.runtime_state import mark_sync_success
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive


def _safe_profile_defaults(
    *,
    mode: str = "conservative_only",
    confidence: float = 0.70,
    dwell: int = 900,
    confirmations: int = 2,
):
    return get_settings().model_copy(
        update={
            "ai_market_settings_auto_apply_mode": mode,
            "ai_market_settings_advisor_min_confidence_to_apply": confidence,
            "ai_market_settings_min_profile_dwell_seconds": dwell,
            "ai_market_settings_relax_requires_consecutive_confirmations": confirmations,
        }
    )


def _mark_all_sync_scopes_fresh(settings_row) -> None:
    now = utcnow_naive()
    for scope in ("account", "positions", "open_orders", "protective_orders"):
        mark_sync_success(settings_row, scope=scope, synced_at=now)


def _seed_account_equity(db_session, equity: float = 100000.0) -> None:
    db_session.add(
        PnLSnapshot(
            snapshot_date=utcnow_naive().date(),
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
        )
    )
    db_session.flush()


def _store_ai_market_settings_recommendation(
    settings_row,
    *,
    profile: str,
    confidence: float = 0.80,
    valid_for_seconds: int = 900,
    recommendation_id: str = "advisor-safety-regression",
    symbol_scope: list[str] | None = None,
    do_not_relax: bool = True,
) -> dict[str, object]:
    now = utcnow_naive()
    payload: dict[str, object] = {
        "recommendation_id": recommendation_id,
        "generated_at": now.isoformat(),
        "valid_until": (now + timedelta(seconds=valid_for_seconds)).isoformat(),
        "symbol_scope": symbol_scope or ["BTCUSDT"],
        "recommended_profile_id": profile,
        "confidence": confidence,
        "reason_summary": "deterministic safety regression recommendation",
        "reason_codes": ["UNIT_TEST"],
        "observed_risk_flags": [],
        "suggested_new_entry_policy": "STRICT_CONFIRMATION_ONLY",
        "do_not_relax": do_not_relax,
        "status": "valid",
    }
    detail = dict(settings_row.pause_reason_detail or {})
    detail["ai_market_settings_advisor"] = {
        "status": "valid",
        "shadow": True,
        "updated_at": now.isoformat(),
        "latest": payload,
    }
    settings_row.pause_reason_detail = detail
    return payload


def _store_safe_profile_state(settings_row, *, active_profile: str, selected_seconds_ago: int = 1200) -> None:
    detail = dict(settings_row.pause_reason_detail or {})
    detail["safe_profile_selector"] = {
        "active_profile": active_profile,
        "active_profile_selected_at": (utcnow_naive() - timedelta(seconds=selected_seconds_ago)).isoformat(),
    }
    settings_row.pause_reason_detail = detail


def _store_execution_profile_policy(settings_row, **updates) -> None:
    detail = dict(settings_row.pause_reason_detail or {})
    detail["ai_market_settings_policy"] = {
        "advisor_enabled": True,
        "advisor_shadow_mode": True,
        "auto_apply_mode": "conservative_only",
        "normal_interval_seconds": 900,
        "elevated_interval_seconds": 300,
        "min_recheck_interval_seconds": 300,
        "recommendation_ttl_seconds": 900,
        "min_confidence_to_apply": 0.70,
        "relax_requires_consecutive_confirmations": 2,
        "min_profile_dwell_seconds": 900,
        **updates,
    }
    settings_row.pause_reason_detail = detail


def _snapshot(*, stale: bool = False, incomplete: bool = False):
    snapshot = build_market_snapshot("BTCUSDT", "15m", upto_index=140, force_stale=stale)
    if incomplete:
        snapshot = snapshot.model_copy(update={"is_complete": False})
    return snapshot


def _entry_decision(snapshot, *, decision: str = "long") -> TradeDecision:
    price = float(snapshot.latest_price)
    return TradeDecision(
        decision=decision,  # type: ignore[arg-type]
        confidence=0.72,
        symbol=snapshot.symbol,
        timeframe=snapshot.timeframe,
        entry_zone_min=price * 0.999,
        entry_zone_max=price * 1.001,
        entry_mode="pullback_confirm" if decision in {"long", "short"} else "none",
        invalidation_price=price * 0.985,
        max_chase_bps=100.0,
        idea_ttl_minutes=15,
        stop_loss=price * 0.99,
        take_profit=price * 1.02,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST", "SAFE_PROFILE_REGRESSION"],
        explanation_short="safe profile regression",
        explanation_detailed="Deterministic safety regression test.",
    )


def _base_recommendation_payload(**updates: object) -> dict[str, object]:
    generated_at = datetime.now(UTC)
    payload: dict[str, object] = {
        "recommendation_id": "advisor-schema-001",
        "generated_at": generated_at.isoformat(),
        "valid_until": (generated_at + timedelta(minutes=15)).isoformat(),
        "symbol_scope": ["BTCUSDT"],
        "recommended_profile_id": "NORMAL",
        "confidence": 0.78,
        "reason_summary": "Schema validation regression payload.",
        "reason_codes": ["UNIT_TEST"],
        "observed_risk_flags": [],
        "suggested_new_entry_policy": "NORMAL_ALLOWED",
        "do_not_relax": True,
    }
    payload.update(updates)
    return payload


def _select_profile(settings_row, snapshot, *, deterministic_profile: str, mode: str = "conservative_only"):
    return select_safe_execution_risk_profile(
        settings_row,
        snapshot,
        defaults=_safe_profile_defaults(mode=mode),
        decision_context={"deterministic_market_condition_profile": deterministic_profile},
        live_requested=False,
    )


def test_ai_market_settings_recommendation_schema_accepts_profile_only_payload() -> None:
    recommendation = AIMarketSettingsRecommendation.model_validate(_base_recommendation_payload())

    assert recommendation.recommended_profile_id == "NORMAL"
    assert recommendation.suggested_new_entry_policy == "NORMAL_ALLOWED"
    assert recommendation.do_not_relax is True


def test_ai_market_settings_recommendation_schema_rejects_unknown_profile_and_raw_thresholds() -> None:
    with pytest.raises(ValidationError):
        AIMarketSettingsRecommendation.model_validate(
            _base_recommendation_payload(recommended_profile_id="UNKNOWN")
        )

    with pytest.raises(ValidationError):
        AIMarketSettingsRecommendation.model_validate(
            _base_recommendation_payload(max_slippage_bps=30, leverage=3)
        )


def test_market_settings_advisor_rejects_provider_output_with_raw_thresholds() -> None:
    class UnsafeProvider:
        name = "openai"

        def generate(self, role, payload, *, response_model, instructions):  # noqa: ANN001
            assert role == "market_settings_advisor"
            assert response_model is AIMarketSettingsRecommendation
            assert "Allowed profile_id values" in instructions
            return ProviderResult(
                provider="openai",
                output=_base_recommendation_payload(max_position_size_pct=0.5),
            )

    snapshot = _snapshot()
    recommendation, provider_name, metadata = MarketSettingsAdvisorAgent(UnsafeProvider()).run(
        market_snapshot=snapshot,
        features=compute_features(snapshot, {}),
        runtime_state={"operating_state": "TRADABLE"},
        settings_policy={"recommendation_ttl_seconds": 900, "symbol_scope": ["BTCUSDT"]},
        observed_risk_flags=[],
        use_ai=True,
    )

    assert recommendation is None
    assert provider_name == "openai"
    assert metadata["schema_status"] == "invalid"
    assert metadata["ignored_reason_code"] == "AI_MARKET_SETTINGS_SCHEMA_INVALID"


def test_orchestrator_advisor_status_ignores_expired_or_low_confidence_recommendations() -> None:
    now = datetime.now(UTC)
    expired = AIMarketSettingsRecommendation.model_validate(
        _base_recommendation_payload(generated_at=(now - timedelta(minutes=30)).isoformat(), valid_until=(now - timedelta(minutes=1)).isoformat())
    )
    low_confidence = AIMarketSettingsRecommendation.model_validate(
        _base_recommendation_payload(confidence=0.40)
    )

    expired_status, expired_reasons = TradingOrchestrator._market_settings_advisor_status(
        recommendation=expired,
        now=now,
        min_confidence_to_apply=0.70,
    )
    confidence_status, confidence_reasons = TradingOrchestrator._market_settings_advisor_status(
        recommendation=low_confidence,
        now=now,
        min_confidence_to_apply=0.70,
    )

    assert expired_status == "ignored"
    assert "AI_MARKET_SETTINGS_RECOMMENDATION_EXPIRED" in expired_reasons
    assert confidence_status == "ignored"
    assert "AI_MARKET_SETTINGS_LOW_CONFIDENCE" in confidence_reasons


def test_orchestrator_stale_incomplete_flags_drive_degraded_advisor_profile() -> None:
    snapshot = _snapshot(stale=True, incomplete=True)
    features = compute_features(snapshot, {})
    flags = TradingOrchestrator._market_settings_observed_risk_flags(
        market_snapshot=snapshot,
        feature_payload=features,
        runtime_state={"operating_state": "TRADABLE"},
        previous_state=None,
    )
    recommendation = MarketSettingsAdvisorAgent._deterministic_recommendation(
        symbol_scope=["BTCUSDT"],
        generated_at=datetime.now(UTC),
        ttl_seconds=900,
        observed_risk_flags=flags,
        market_snapshot=snapshot,
        features=features,
    )

    assert "STALE_MARKET_DATA" in flags
    assert "INCOMPLETE_MARKET_DATA" in flags
    assert recommendation.recommended_profile_id == "DEGRADED"
    assert recommendation.suggested_new_entry_policy == "NO_NEW_ENTRY"


def test_orchestrator_soft_signal_flags_do_not_drive_incomplete_market_data() -> None:
    snapshot = _snapshot()
    features = compute_features(snapshot, {}).model_copy(
        update={
            "data_quality_flags": ["WEAK_VOLUME", "MOMENTUM_WEAKENING"],
            "volatility_pct": 1.5,
        }
    )
    features.regime.volatility_regime = "normal"

    flags = TradingOrchestrator._market_settings_observed_risk_flags(
        market_snapshot=snapshot,
        feature_payload=features,
        runtime_state={"operating_state": "TRADABLE"},
        previous_state=None,
    )

    assert "INCOMPLETE_MARKET_DATA" not in flags
    assert "HIGH_VOLATILITY" not in flags


def test_market_settings_symbol_context_keeps_soft_signal_flags_complete() -> None:
    snapshot = _snapshot()
    features = compute_features(snapshot, {}).model_copy(
        update={
            "data_quality_flags": [
                "WEAK_VOLUME",
                "MOMENTUM_WEAKENING",
                "LEAD_LAG_CONTEXT_UNAVAILABLE",
                "DERIVATIVES_CONTEXT_UNAVAILABLE",
                "PULLBACK_CONTEXT_PARTIAL",
            ],
            "volatility_pct": 1.5,
        }
    )
    features.regime.volatility_regime = "normal"

    context = TradingOrchestrator._market_settings_symbol_context_from_payload(
        symbol=snapshot.symbol,
        timeframe=snapshot.timeframe,
        source="unit_test",
        feature_time=snapshot.snapshot_time,
        feature_payload=features.model_dump(mode="json"),
        market_payload=snapshot.model_dump(mode="json"),
        now=utcnow_naive(),
    )

    assert context["data_quality"]["is_complete"] is True
    assert "INCOMPLETE_MARKET_DATA" not in context["risk_flags"]
    assert "HIGH_VOLATILITY" not in context["risk_flags"]


def test_market_settings_advisor_payload_keeps_context_flags_complete() -> None:
    snapshot = _snapshot()
    features = compute_features(snapshot, {}).model_copy(
        update={
            "data_quality_flags": [
                "WEAK_VOLUME",
                "MOMENTUM_WEAKENING",
                "LEAD_LAG_CONTEXT_UNAVAILABLE",
                "DERIVATIVES_CONTEXT_UNAVAILABLE",
                "PULLBACK_CONTEXT_PARTIAL",
            ],
            "volatility_pct": 1.5,
        }
    )

    payload = build_market_settings_advisor_input_payload(
        market_snapshot=snapshot,
        features=features,
        runtime_state={"operating_state": "TRADABLE"},
        settings_policy={"symbol_scope": ["BTCUSDT"]},
        observed_risk_flags=[],
    )
    symbol_context = payload["compact_market_context"]["symbols"][0]

    assert symbol_context["data_quality"]["is_complete"] is True


def test_market_settings_volatility_threshold_treats_ratio_default_as_percent(monkeypatch) -> None:
    defaults = get_settings().model_copy(update={"ai_decision_volatility_spike_min_pct": 0.02})
    monkeypatch.setattr("trading_mvp.services.orchestrator.get_settings", lambda: defaults)
    snapshot = _snapshot()
    features = compute_features(snapshot, {}).model_copy(
        update={"data_quality_flags": [], "volatility_pct": 1.5}
    )
    features.regime.volatility_regime = "normal"

    normal_flags = TradingOrchestrator._market_settings_observed_risk_flags(
        market_snapshot=snapshot,
        feature_payload=features,
        runtime_state={"operating_state": "TRADABLE"},
        previous_state=None,
    )
    elevated_flags = TradingOrchestrator._market_settings_observed_risk_flags(
        market_snapshot=snapshot,
        feature_payload=features.model_copy(update={"volatility_pct": 2.5}),
        runtime_state={"operating_state": "TRADABLE"},
        previous_state=None,
    )

    assert "HIGH_VOLATILITY" not in normal_flags
    assert "HIGH_VOLATILITY" in elevated_flags


def test_shadow_market_settings_advisor_is_not_injected_as_trading_constraint() -> None:
    risk_context = {
        "execution_constraints_summary": {
            "risk_guard_final_authority": True,
        }
    }
    advisor_result = {
        "status": "shadow_generated",
        "provider": "openai",
        "shadow": True,
        "risk_guard_unchanged": True,
        "observed_risk_flags": ["MARKET_BREADTH_WEAK"],
        "recommendation": {
            "recommended_profile_id": "DEGRADED",
            "confidence": 0.88,
            "suggested_new_entry_policy": "NO_NEW_ENTRY",
            "do_not_relax": True,
            "reason_codes": ["MARKET_BREADTH_WEAK"],
        },
    }

    updated, advisor_context = TradingOrchestrator._risk_context_with_market_settings_advisor(
        risk_context,
        advisor_result,
    )

    assert updated == risk_context
    assert advisor_context["shadow"] is True
    assert "suggested_new_entry_policy" not in advisor_context["recommendation"]
    assert "market_settings_advisor" not in updated
    assert "execution_risk_profile_advisor" not in updated["execution_constraints_summary"]


def test_skipped_market_settings_advisor_is_not_injected_as_trading_constraint() -> None:
    risk_context = {
        "execution_constraints_summary": {
            "risk_guard_final_authority": True,
        }
    }
    advisor_result = {
        "status": "skipped",
        "skip_reason": "min_recheck_interval_active",
        "risk_guard_unchanged": True,
        "observed_risk_flags": ["MARKET_BREADTH_WEAK"],
    }

    updated, advisor_context = TradingOrchestrator._risk_context_with_market_settings_advisor(
        risk_context,
        advisor_result,
    )

    assert updated == risk_context
    assert advisor_context["status"] == "skipped"
    assert "market_settings_advisor" not in updated
    assert "execution_risk_profile_advisor" not in updated["execution_constraints_summary"]


def test_non_shadow_market_settings_advisor_is_injected_as_trading_constraint() -> None:
    risk_context = {
        "execution_constraints_summary": {
            "risk_guard_final_authority": True,
        }
    }
    advisor_result = {
        "status": "generated",
        "provider": "openai",
        "shadow": False,
        "risk_guard_unchanged": True,
        "observed_risk_flags": ["HIGH_VOLATILITY"],
        "recommendation": {
            "recommended_profile_id": "HIGH_VOLATILITY",
            "confidence": 0.88,
            "suggested_new_entry_policy": "STRICT_CONFIRMATION_ONLY",
            "do_not_relax": True,
            "reason_codes": ["HIGH_VOLATILITY"],
        },
    }

    updated, advisor_context = TradingOrchestrator._risk_context_with_market_settings_advisor(
        risk_context,
        advisor_result,
    )

    assert advisor_context["shadow"] is False
    assert advisor_context["recommendation"]["suggested_new_entry_policy"] == "STRICT_CONFIRMATION_ONLY"
    assert updated["market_settings_advisor"] == advisor_context
    assert updated["execution_constraints_summary"]["execution_risk_profile_advisor"] == advisor_context


def test_safe_profile_selector_tightens_normal_to_ai_caution(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _mark_all_sync_scopes_fresh(settings_row)
    _store_ai_market_settings_recommendation(settings_row, profile="CAUTION")

    selection = _select_profile(settings_row, _snapshot(), deterministic_profile="NORMAL")

    assert selection["deterministic_profile"] == "NORMAL"
    assert selection["ai_recommended_profile"] == "CAUTION"
    assert selection["final_active_profile"] == "CAUTION"
    assert selection["was_tightened_by_ai"] is True


def test_safe_profile_selector_uses_stored_auto_apply_policy(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _mark_all_sync_scopes_fresh(settings_row)
    _store_execution_profile_policy(settings_row, auto_apply_mode="conservative_only")
    _store_ai_market_settings_recommendation(settings_row, profile="CAUTION", confidence=0.82)

    selection = select_safe_execution_risk_profile(
        settings_row,
        _snapshot(),
        defaults=_safe_profile_defaults(mode="shadow"),
        decision_context={"deterministic_market_condition_profile": "NORMAL"},
        live_requested=False,
    )

    assert selection["selection_mode"] == "conservative_only"
    assert selection["final_active_profile"] == "CAUTION"
    assert selection["was_tightened_by_ai"] is True


def test_safe_profile_selector_uses_stored_confidence_and_ttl_policy(db_session) -> None:
    now = utcnow_naive()
    settings_row = get_or_create_settings(db_session)
    _mark_all_sync_scopes_fresh(settings_row)
    _store_execution_profile_policy(
        settings_row,
        auto_apply_mode="conservative_only",
        recommendation_ttl_seconds=60,
        min_confidence_to_apply=0.90,
    )
    detail = dict(settings_row.pause_reason_detail or {})
    detail["ai_market_settings_advisor"] = {
        "status": "valid",
        "shadow": True,
        "updated_at": (now - timedelta(seconds=120)).isoformat(),
        "latest": {
            "recommendation_id": "advisor-expired-by-stored-ttl",
            "generated_at": (now - timedelta(seconds=120)).isoformat(),
            "symbol_scope": ["BTCUSDT"],
            "recommended_profile_id": "STRESS",
            "confidence": 0.80,
            "reason_summary": "expired by stored ttl",
            "reason_codes": ["UNIT_TEST"],
            "observed_risk_flags": [],
            "suggested_new_entry_policy": "NO_NEW_ENTRY",
            "do_not_relax": True,
        },
    }
    settings_row.pause_reason_detail = detail

    selection = select_safe_execution_risk_profile(
        settings_row,
        _snapshot(),
        defaults=_safe_profile_defaults(mode="shadow", confidence=0.50),
        decision_context={"deterministic_market_condition_profile": "NORMAL"},
        live_requested=False,
        now=now,
    )

    assert selection["selection_mode"] == "conservative_only"
    assert selection["final_active_profile"] == "NORMAL"
    assert "AI_MARKET_SETTINGS_RECOMMENDATION_EXPIRED" in selection["ignored_reason_codes"]
    assert "AI_MARKET_SETTINGS_RECOMMENDATION_LOW_CONFIDENCE" in selection["ignored_reason_codes"]


def test_market_settings_advisor_defaults_use_stored_policy(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _store_execution_profile_policy(
        settings_row,
        advisor_enabled=False,
        advisor_shadow_mode=False,
        normal_interval_seconds=1200,
        elevated_interval_seconds=60,
        min_recheck_interval_seconds=120,
        recommendation_ttl_seconds=180,
        min_confidence_to_apply=0.85,
    )
    db_session.add(settings_row)
    db_session.flush()

    policy = TradingOrchestrator(db_session)._market_settings_advisor_defaults()

    assert policy["enabled"] is False
    assert policy["shadow"] is False
    assert policy["normal_interval_seconds"] == 1200
    assert policy["elevated_interval_seconds"] == 60
    assert policy["min_recheck_interval_seconds"] == 120
    assert policy["recommendation_ttl_seconds"] == 180
    assert policy["min_confidence_to_apply"] == 0.85


@pytest.mark.parametrize("deterministic_profile", ["HIGH_VOLATILITY", "STRESS"])
def test_safe_profile_selector_does_not_auto_relax_to_ai_normal(db_session, deterministic_profile: str) -> None:
    settings_row = get_or_create_settings(db_session)
    _mark_all_sync_scopes_fresh(settings_row)
    _store_ai_market_settings_recommendation(settings_row, profile="NORMAL")

    selection = _select_profile(settings_row, _snapshot(), deterministic_profile=deterministic_profile)

    assert selection["final_active_profile"] == deterministic_profile
    assert selection["was_tightened_by_ai"] is False
    assert selection["was_relaxation_blocked"] is True


@pytest.mark.parametrize(
    ("snapshot_kwargs", "operating_state", "expected_reason"),
    [
        ({"stale": True}, "TRADABLE", "MARKET_STATE_STALE"),
        ({"incomplete": True}, "TRADABLE", "MARKET_STATE_INCOMPLETE"),
        ({}, "DEGRADED_MANAGE_ONLY", "DEGRADED_MANAGE_ONLY"),
    ],
)
def test_safe_profile_selector_hard_conditions_override_ai_normal(
    db_session,
    snapshot_kwargs: dict[str, bool],
    operating_state: str,
    expected_reason: str,
) -> None:
    settings_row = get_or_create_settings(db_session)
    _mark_all_sync_scopes_fresh(settings_row)
    _store_ai_market_settings_recommendation(settings_row, profile="NORMAL")

    selection = select_safe_execution_risk_profile(
        settings_row,
        _snapshot(**snapshot_kwargs),
        defaults=_safe_profile_defaults(mode="conservative_only"),
        decision_context={"deterministic_market_condition_profile": "NORMAL"},
        operating_state=operating_state,
        live_requested=False,
    )

    assert selection["final_active_profile"] == "DEGRADED"
    assert selection["selected_reason"] == "hard_condition"
    assert expected_reason in selection["hard_condition_reason_codes"]
    assert selection["active_profile_blocks_new_entry"] is True


def test_safe_profile_selector_ignores_unknown_ai_profile_id(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    _mark_all_sync_scopes_fresh(settings_row)
    _store_ai_market_settings_recommendation(settings_row, profile="UNKNOWN")

    selection = _select_profile(settings_row, _snapshot(), deterministic_profile="NORMAL")

    assert selection["final_active_profile"] == "NORMAL"
    assert selection["ai_recommended_profile"] is None
    assert "AI_MARKET_SETTINGS_RECOMMENDATION_UNKNOWN_PROFILE" in selection["ignored_reason_codes"]


@pytest.mark.parametrize(
    ("confidence", "valid_for_seconds", "expected_reason"),
    [
        (0.40, 900, "AI_MARKET_SETTINGS_RECOMMENDATION_LOW_CONFIDENCE"),
        (0.80, -60, "AI_MARKET_SETTINGS_RECOMMENDATION_EXPIRED"),
    ],
)
def test_safe_profile_selector_ignores_low_confidence_or_expired_ai_recommendation(
    db_session,
    confidence: float,
    valid_for_seconds: int,
    expected_reason: str,
) -> None:
    settings_row = get_or_create_settings(db_session)
    _mark_all_sync_scopes_fresh(settings_row)
    _store_ai_market_settings_recommendation(
        settings_row,
        profile="STRESS",
        confidence=confidence,
        valid_for_seconds=valid_for_seconds,
    )

    selection = _select_profile(settings_row, _snapshot(), deterministic_profile="NORMAL")

    assert selection["final_active_profile"] == "NORMAL"
    assert expected_reason in selection["ignored_reason_codes"]


def test_shadow_mode_does_not_change_risk_guard_decision(monkeypatch, db_session) -> None:
    from trading_mvp.services import risk as risk_service

    defaults = _safe_profile_defaults(mode="shadow")
    monkeypatch.setattr(risk_service, "get_settings", lambda: defaults)
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    _seed_account_equity(db_session)
    _mark_all_sync_scopes_fresh(settings_row)
    _store_ai_market_settings_recommendation(settings_row, profile="STRESS")
    snapshot = _snapshot()

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        _entry_decision(snapshot),
        snapshot,
        execution_mode="historical_replay",
    )

    selector = result.debug_payload["safe_profile_selector"]
    assert result.allowed is True
    assert selector["selection_mode"] == "shadow"
    assert selector["final_active_profile"] == "NORMAL"
    assert selector["shadow_final_profile"] == "STRESS"
    assert selector["active_profile_blocks_new_entry"] is False
    assert EXECUTION_RISK_PROFILE_BLOCK_REASON_CODE not in result.reason_codes


def test_conservative_only_relaxation_block_is_persisted_and_audited(monkeypatch, db_session) -> None:
    from trading_mvp.services import risk as risk_service

    defaults = _safe_profile_defaults(mode="conservative_only", dwell=0, confirmations=2)
    monkeypatch.setattr(risk_service, "get_settings", lambda: defaults)
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    _seed_account_equity(db_session)
    _mark_all_sync_scopes_fresh(settings_row)
    _store_safe_profile_state(settings_row, active_profile="STRESS", selected_seconds_ago=1200)
    _store_ai_market_settings_recommendation(settings_row, profile="NORMAL")
    snapshot = _snapshot()

    result, risk_row = evaluate_risk(
        db_session,
        settings_row,
        _entry_decision(snapshot),
        snapshot,
        execution_mode="historical_replay",
    )

    selector = result.debug_payload["safe_profile_selector"]
    assert result.allowed is False
    assert selector["final_active_profile"] == "STRESS"
    assert selector["was_relaxation_blocked"] is True
    assert PROFILE_RELAXATION_CONSECUTIVE_REASON_CODE in selector["relaxation_block_reason_codes"]
    event = db_session.query(AuditEvent).filter_by(event_type="execution_risk_profile_selected").one()
    assert event.entity_id == str(risk_row.id)
    assert event.severity == "warning"
    assert event.payload["was_relaxation_blocked"] is True


@pytest.mark.parametrize(
    ("decision", "updates"),
    [
        ("reduce", {"intent_family": "management", "management_action": "reduce_only"}),
        ("exit", {"intent_family": "exit", "management_action": "exit_only"}),
    ],
)
def test_survival_paths_remain_allowed_when_active_profile_is_stress(
    monkeypatch,
    db_session,
    decision: str,
    updates: dict[str, str],
) -> None:
    from trading_mvp.services import risk as risk_service

    defaults = _safe_profile_defaults(mode="conservative_only")
    monkeypatch.setattr(risk_service, "get_settings", lambda: defaults)
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    _seed_account_equity(db_session)
    _mark_all_sync_scopes_fresh(settings_row)
    _store_safe_profile_state(settings_row, active_profile="STRESS", selected_seconds_ago=0)
    snapshot = _snapshot()
    survival_decision = _entry_decision(snapshot, decision=decision).model_copy(update=updates)

    result, _ = evaluate_risk(
        db_session,
        settings_row,
        survival_decision,
        snapshot,
        execution_mode="historical_replay",
    )

    assert result.allowed is True
    assert result.debug_payload["safe_profile_selector"]["final_active_profile"] == "STRESS"
    assert EXECUTION_RISK_PROFILE_BLOCK_REASON_CODE not in result.reason_codes


def test_profile_tightening_creates_audit_event(monkeypatch, db_session) -> None:
    from trading_mvp.services import risk as risk_service

    defaults = _safe_profile_defaults(mode="conservative_only")
    monkeypatch.setattr(risk_service, "get_settings", lambda: defaults)
    settings_row = get_or_create_settings(db_session)
    settings_row.rollout_mode = "paper"
    _seed_account_equity(db_session)
    _mark_all_sync_scopes_fresh(settings_row)
    _store_ai_market_settings_recommendation(settings_row, profile="CAUTION")
    snapshot = _snapshot()

    result, risk_row = evaluate_risk(
        db_session,
        settings_row,
        _entry_decision(snapshot),
        snapshot,
        execution_mode="historical_replay",
    )

    assert result.allowed is True
    event = db_session.query(AuditEvent).filter_by(event_type="execution_risk_profile_selected").one()
    assert event.entity_id == str(risk_row.id)
    assert event.severity == "info"
    assert event.payload["final_active_profile"] == "CAUTION"
    assert event.payload["was_tightened_by_ai"] is True
