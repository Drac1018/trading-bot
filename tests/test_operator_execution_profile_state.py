from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from trading_mvp.main import app
from trading_mvp.services.dashboard import get_operator_dashboard
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive


def _seed_profile_state(settings_row, *, now):
    settings_row.pause_reason_detail = {
        "ai_market_settings_advisor": {
            "status": "shadow_generated",
            "shadow": True,
            "updated_at": now.isoformat(),
            "latest": {
                "recommendation_id": "rec-profile-1",
                "recommended_profile_id": "CAUTION",
                "confidence": 0.82,
                "valid_until": (now + timedelta(minutes=15)).isoformat(),
                "reason_codes": ["VOLATILITY_ELEVATED"],
                "observed_risk_flags": [],
            },
        },
        "safe_profile_selector": {
            "active_profile": "CAUTION",
            "deterministic_profile": "NORMAL",
            "ai_recommended_profile": "CAUTION",
            "selection_mode": "conservative_only",
            "selected_reason": "ai_tightened_conservative_only",
            "last_selection": {
                "previous_profile": "NORMAL",
                "deterministic_profile": "NORMAL",
                "deterministic_reason_codes": [],
                "ai_recommended_profile": "CAUTION",
                "final_active_profile": "CAUTION",
                "selection_mode": "conservative_only",
                "selected_reason": "ai_tightened_conservative_only",
                "ai_recommendation_id": "rec-profile-1",
                "confidence": 0.82,
                "valid_until": (now + timedelta(minutes=15)).isoformat(),
                "was_tightened_by_ai": True,
                "was_relaxation_blocked": False,
                "relaxation_block_reason_codes": [],
                "ignored_reason_codes": [],
                "active_profile_blocks_new_entry": False,
                "blocking_active": True,
                "selected_at": now.isoformat(),
            },
        },
    }


def test_operator_dashboard_exposes_execution_profile_selector_state(db_session) -> None:
    now = utcnow_naive()
    settings_row = get_or_create_settings(db_session)
    _seed_profile_state(settings_row, now=now)
    db_session.add(settings_row)
    db_session.flush()

    payload = get_operator_dashboard(db_session)
    control = payload.control

    assert control.deterministic_market_profile == "NORMAL"
    assert control.ai_recommended_profile == "CAUTION"
    assert control.ai_recommendation_id == "rec-profile-1"
    assert control.ai_recommendation_confidence == 0.82
    assert control.ai_recommendation_reason_codes == ["VOLATILITY_ELEVATED"]
    assert control.ai_recommendation_status == "shadow_generated"
    assert control.final_active_execution_profile == "CAUTION"
    assert control.profile_selection_mode == "conservative_only"
    assert control.profile_selected_reason == "ai_tightened_conservative_only"
    assert control.was_tightened_by_ai is True
    assert control.was_relaxation_blocked is False
    assert control.profile_new_entry_blocked is False
    assert control.profile_survival_paths_allowed is True
    assert control.next_ai_settings_review_at is not None
    assert control.next_ai_settings_review_at > now


def test_operator_dashboard_next_ai_settings_review_uses_stored_policy(db_session) -> None:
    now = utcnow_naive()
    settings_row = get_or_create_settings(db_session)
    _seed_profile_state(settings_row, now=now)
    detail = dict(settings_row.pause_reason_detail or {})
    detail["ai_market_settings_policy"] = {
        "advisor_enabled": True,
        "advisor_shadow_mode": False,
        "auto_apply_mode": "manual_approval",
        "normal_interval_seconds": 120,
        "elevated_interval_seconds": 60,
        "min_recheck_interval_seconds": 60,
        "recommendation_ttl_seconds": 300,
        "min_confidence_to_apply": 0.75,
        "relax_requires_consecutive_confirmations": 2,
        "min_profile_dwell_seconds": 0,
    }
    settings_row.pause_reason_detail = detail
    db_session.add(settings_row)
    db_session.flush()

    payload = get_operator_dashboard(db_session)
    control = payload.control

    assert control.ai_settings_shadow_mode is False
    assert control.ai_settings_auto_apply_mode == "manual_approval"
    assert control.next_ai_settings_review_at is not None
    assert abs((control.next_ai_settings_review_at - now).total_seconds() - 120) < 1


def test_operator_dashboard_marks_ignored_recommendation_and_relaxation_block(db_session) -> None:
    now = utcnow_naive()
    settings_row = get_or_create_settings(db_session)
    settings_row.pause_reason_detail = {
        "ai_market_settings_advisor": {
            "status": "shadow_generated",
            "shadow": True,
            "updated_at": now.isoformat(),
            "latest": {
                "recommendation_id": "rec-profile-2",
                "recommended_profile_id": "NORMAL",
                "confidence": 0.41,
                "valid_until": (now - timedelta(minutes=1)).isoformat(),
                "reason_codes": ["VOLATILITY_NORMALIZED"],
                "ignored_reason_codes": ["AI_MARKET_SETTINGS_RECOMMENDATION_EXPIRED"],
            },
        },
        "safe_profile_selector": {
            "last_selection": {
                "deterministic_profile": "HIGH_VOLATILITY",
                "ai_recommended_profile": "NORMAL",
                "final_active_profile": "HIGH_VOLATILITY",
                "selection_mode": "conservative_only",
                "selected_reason": "ai_relaxation_blocked",
                "ai_recommendation_id": "rec-profile-2",
                "confidence": 0.41,
                "valid_until": (now - timedelta(minutes=1)).isoformat(),
                "was_tightened_by_ai": False,
                "was_relaxation_blocked": True,
                "relaxation_block_reason_codes": ["PROFILE_RELAXATION_AI_VALIDITY"],
                "ignored_reason_codes": [
                    "AI_MARKET_SETTINGS_RECOMMENDATION_EXPIRED",
                    "AI_MARKET_SETTINGS_RECOMMENDATION_LOW_CONFIDENCE",
                ],
                "active_profile_blocks_new_entry": False,
                "blocking_active": True,
            }
        },
    }
    db_session.add(settings_row)
    db_session.flush()

    payload = get_operator_dashboard(db_session)
    control = payload.control

    assert control.ai_recommendation_status == "ignored"
    assert control.was_relaxation_blocked is True
    assert control.relaxation_block_reason == "PROFILE_RELAXATION_AI_VALIDITY"
    assert control.ai_recommendation_ignored_reason_codes == [
        "AI_MARKET_SETTINGS_RECOMMENDATION_EXPIRED",
        "AI_MARKET_SETTINGS_RECOMMENDATION_LOW_CONFIDENCE",
    ]
    assert control.final_active_execution_profile == "HIGH_VOLATILITY"


def test_operator_dashboard_api_includes_execution_profile_fields(testclient_db_factory) -> None:
    testing_session = testclient_db_factory("operator_execution_profile.db")
    now = utcnow_naive()
    with testing_session() as session:
        settings_row = get_or_create_settings(session)
        _seed_profile_state(settings_row, now=now)
        session.add(settings_row)
        session.commit()

    with TestClient(app) as client:
        response = client.get("/api/dashboard/operator")

    assert response.status_code == 200
    control = response.json()["control"]
    assert control["deterministic_market_profile"] == "NORMAL"
    assert control["ai_recommended_profile"] == "CAUTION"
    assert control["ai_recommendation_id"] == "rec-profile-1"
    assert control["final_active_execution_profile"] == "CAUTION"
    assert control["profile_selection_mode"] == "conservative_only"
    assert control["was_tightened_by_ai"] is True
    assert control["profile_new_entry_blocked"] is False
    assert control["profile_survival_paths_allowed"] is True
