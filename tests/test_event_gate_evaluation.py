from __future__ import annotations

import json
from pathlib import Path

from scripts.evaluate_event_gate import (
    DEFAULT_CANDLE_FIXTURE_PATH,
    DEFAULT_EVENT_FIXTURE_PATH,
    EvaluationConfig,
    evaluate_replay,
)


def _case_map(result: dict[str, object]) -> dict[str, dict[str, object]]:
    cases = result.get("cases")
    assert isinstance(cases, list)
    return {
        str(item["case_id"]): item
        for item in cases
        if isinstance(item, dict) and "case_id" in item
    }


def test_event_gate_fixture_replay_produces_expected_breakdown() -> None:
    result = evaluate_replay(
        candle_fixture_path=DEFAULT_CANDLE_FIXTURE_PATH,
        event_fixture_path=DEFAULT_EVENT_FIXTURE_PATH,
    )

    assert result["case_count"] == 6
    assert result["baseline"] == {
        "entry_candidate_count": 5,
        "allowed_entry_count": 5,
        "blocked_entry_count": 0,
        "decision_reason_breakdown": {
            "REGIME_LONG_ENTRY_ALLOWED": 4,
            "REGIME_SHORT_ENTRY_ALLOWED": 1,
            "SURVIVAL_PATH_ALLOWED": 1,
        },
    }
    assert result["event_aware"] == {
        "entry_candidate_count": 5,
        "allowed_entry_count": 3,
        "blocked_entry_count": 2,
        "blocked_by_high_impact_window_count": 2,
        "reduced_paths_still_allowed_count": 1,
        "unavailable_stale_event_source_count": 2,
        "decision_reason_breakdown": {
            "EVENT_SOURCE_STALE_ALLOWED": 1,
            "EVENT_SOURCE_UNAVAILABLE_ALLOWED": 1,
            "HIGH_IMPACT_EVENT_WINDOW": 2,
            "REGIME_LONG_ENTRY_ALLOWED": 1,
            "SURVIVAL_PATH_ALLOWED": 1,
        },
    }
    assert result["delta"] == {
        "additional_blocked_entries": 2,
        "preserved_survival_paths": 1,
    }

    cases = _case_map(result)
    assert cases["btc_pre_high_event_long"]["event_aware_reason"] == "HIGH_IMPACT_EVENT_WINDOW"
    assert cases["eth_post_high_event_short"]["event_aware_reason"] == "HIGH_IMPACT_EVENT_WINDOW"
    assert cases["sol_reduce_during_high_event"]["event_aware_decision"] == "reduce"
    assert cases["ada_unavailable_source_long"]["event_aware_reason"] == "EVENT_SOURCE_UNAVAILABLE_ALLOWED"
    assert cases["doge_stale_source_long"]["event_aware_reason"] == "EVENT_SOURCE_STALE_ALLOWED"
    assert cases["xrp_clean_entry_long"]["event_aware_reason"] == "REGIME_LONG_ENTRY_ALLOWED"


def test_event_gate_evaluation_is_deterministic() -> None:
    first = evaluate_replay(
        candle_fixture_path=DEFAULT_CANDLE_FIXTURE_PATH,
        event_fixture_path=DEFAULT_EVENT_FIXTURE_PATH,
    )
    second = evaluate_replay(
        candle_fixture_path=DEFAULT_CANDLE_FIXTURE_PATH,
        event_fixture_path=DEFAULT_EVENT_FIXTURE_PATH,
    )

    assert first == second


def test_event_gate_boundary_windows_are_inclusive(tmp_path: Path) -> None:
    candle_fixture = {
        "cases": [
            {
                "case_id": "exact_pre_boundary",
                "symbol": "BTCUSDT",
                "timeframe": "15m",
                "generated_at": "2026-04-20T12:00:00+00:00",
                "decision_mode": "entry",
                "entry_side": "long",
                "base_series": {"pattern": "trend_up", "count": 60, "start_price": 100.0, "step": 1.1},
                "context_series": {
                    "1h": {"pattern": "trend_up", "count": 60, "start_price": 95.0, "step": 0.9},
                    "4h": {"pattern": "trend_up", "count": 60, "start_price": 90.0, "step": 1.3},
                },
            },
            {
                "case_id": "outside_pre_boundary",
                "symbol": "ETHUSDT",
                "timeframe": "15m",
                "generated_at": "2026-04-20T12:00:00+00:00",
                "decision_mode": "entry",
                "entry_side": "long",
                "base_series": {"pattern": "trend_up", "count": 60, "start_price": 100.0, "step": 1.1},
                "context_series": {
                    "1h": {"pattern": "trend_up", "count": 60, "start_price": 95.0, "step": 0.9},
                    "4h": {"pattern": "trend_up", "count": 60, "start_price": 90.0, "step": 1.3},
                },
            },
            {
                "case_id": "exact_post_boundary",
                "symbol": "SOLUSDT",
                "timeframe": "15m",
                "generated_at": "2026-04-20T12:00:00+00:00",
                "decision_mode": "entry",
                "entry_side": "long",
                "base_series": {"pattern": "trend_up", "count": 60, "start_price": 100.0, "step": 1.1},
                "context_series": {
                    "1h": {"pattern": "trend_up", "count": 60, "start_price": 95.0, "step": 0.9},
                    "4h": {"pattern": "trend_up", "count": 60, "start_price": 90.0, "step": 1.3},
                },
            },
            {
                "case_id": "outside_post_boundary",
                "symbol": "ADAUSDT",
                "timeframe": "15m",
                "generated_at": "2026-04-20T12:00:00+00:00",
                "decision_mode": "entry",
                "entry_side": "long",
                "base_series": {"pattern": "trend_up", "count": 60, "start_price": 100.0, "step": 1.1},
                "context_series": {
                    "1h": {"pattern": "trend_up", "count": 60, "start_price": 95.0, "step": 0.9},
                    "4h": {"pattern": "trend_up", "count": 60, "start_price": 90.0, "step": 1.3},
                },
            },
        ]
    }
    event_fixture = {
        "cases": {
            "exact_pre_boundary": {
                "source_generated_at": "2026-04-20T11:50:00+00:00",
                "events": [
                    {
                        "event_name": "FOMC",
                        "event_at": "2026-04-20T13:00:00+00:00",
                        "importance": "high",
                        "affected_assets": ["BTCUSDT"],
                    }
                ],
            },
            "outside_pre_boundary": {
                "source_generated_at": "2026-04-20T11:50:00+00:00",
                "events": [
                    {
                        "event_name": "CPI",
                        "event_at": "2026-04-20T13:01:00+00:00",
                        "importance": "high",
                        "affected_assets": ["ETHUSDT"],
                    }
                ],
            },
            "exact_post_boundary": {
                "source_generated_at": "2026-04-20T11:50:00+00:00",
                "events": [
                    {
                        "event_name": "Powell",
                        "event_at": "2026-04-20T11:30:00+00:00",
                        "importance": "high",
                        "affected_assets": ["SOLUSDT"],
                    }
                ],
            },
            "outside_post_boundary": {
                "source_generated_at": "2026-04-20T11:50:00+00:00",
                "events": [
                    {
                        "event_name": "Retail Sales",
                        "event_at": "2026-04-20T11:29:00+00:00",
                        "importance": "high",
                        "affected_assets": ["ADAUSDT"],
                    }
                ],
            },
        }
    }

    candle_path = tmp_path / "candles.json"
    event_path = tmp_path / "events.json"
    candle_path.write_text(json.dumps(candle_fixture, ensure_ascii=False, indent=2), encoding="utf-8")
    event_path.write_text(json.dumps(event_fixture, ensure_ascii=False, indent=2), encoding="utf-8")

    result = evaluate_replay(
        candle_fixture_path=candle_path,
        event_fixture_path=event_path,
        config=EvaluationConfig(
            event_guard_enabled=True,
            high_impact_block_before_minutes=60,
            high_impact_block_after_minutes=30,
            require_event_context_for_new_entries=False,
        ),
    )

    cases = _case_map(result)
    assert cases["exact_pre_boundary"]["event_aware_reason"] == "HIGH_IMPACT_EVENT_WINDOW"
    assert cases["outside_pre_boundary"]["event_aware_reason"] == "REGIME_LONG_ENTRY_ALLOWED"
    assert cases["exact_post_boundary"]["event_aware_reason"] == "HIGH_IMPACT_EVENT_WINDOW"
    assert cases["outside_post_boundary"]["event_aware_reason"] == "REGIME_LONG_ENTRY_ALLOWED"
    assert result["event_aware"]["blocked_entry_count"] == 2
