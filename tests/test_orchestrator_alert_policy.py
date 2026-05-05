from __future__ import annotations

from trading_mvp.services.orchestrator import _should_create_trade_blocked_alert


def test_trade_blocked_alert_suppresses_passive_hold_reasons() -> None:
    assert _should_create_trade_blocked_alert("hold", ["HOLD_DECISION"]) is False
    assert (
        _should_create_trade_blocked_alert(
            "hold",
            ["HOLD_DECISION", "DETERMINISTIC_BASELINE_DISAGREEMENT"],
        )
        is False
    )


def test_trade_blocked_alert_keeps_hard_blocker_reasons() -> None:
    assert (
        _should_create_trade_blocked_alert(
            "hold",
            ["HOLD_DECISION", "PROTECTION_STATE_UNVERIFIED"],
        )
        is True
    )

