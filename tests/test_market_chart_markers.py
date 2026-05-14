from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from trading_mvp.main import app
from trading_mvp.models import (
    DecisionPerformanceFact,
    Execution,
    Order,
    PendingEntryPlan,
    RiskCheck,
)
from trading_mvp.services.dashboard import get_market_chart_markers, get_risk_checks
from trading_mvp.time_utils import utcnow_naive


def _seed_chart_marker_rows(session) -> None:
    now = utcnow_naive()
    session.add(
        DecisionPerformanceFact(
            decision_run_id=101,
            symbol="ETHUSDT",
            timeframe="15m",
            decision="long",
            rationale_codes=["TREND_UP"],
            entry_zone_min=2319.0,
            entry_zone_max=2321.0,
            telemetry_output={"confidence": 0.72},
            created_at=now - timedelta(minutes=4),
        )
    )
    session.add(
        RiskCheck(
            symbol="ETHUSDT",
            decision_run_id=101,
            market_snapshot_id=301,
            allowed=False,
            decision="long",
            reason_codes=["ENTRY_TRIGGER_NOT_MET"],
            approved_risk_pct=0.0,
            approved_leverage=0.0,
            payload={
                "blocked_reason_codes": ["ENTRY_TRIGGER_NOT_MET"],
                "debug_payload": {"entry_trigger": {"reference_price": 2320.5}},
                "large_unused_blob": "x" * 5000,
            },
            created_at=now - timedelta(minutes=3),
        )
    )
    session.add(
        PendingEntryPlan(
            symbol="ETHUSDT",
            side="long",
            plan_status="armed",
            source_decision_run_id=101,
            entry_zone_min=2319.0,
            entry_zone_max=2321.0,
            expires_at=now + timedelta(minutes=10),
            idempotency_key="chart-marker-plan",
        )
    )
    order = Order(
        symbol="ETHUSDT",
        decision_run_id=101,
        side="sell",
        order_type="take_profit_market",
        mode="live",
        status="filled",
        requested_quantity=0.1,
        requested_price=2330.0,
        filled_quantity=0.1,
        average_fill_price=2331.0,
        created_at=now - timedelta(minutes=2),
    )
    session.add(order)
    session.flush()
    session.add(
        Execution(
            order_id=order.id,
            symbol="ETHUSDT",
            status="filled",
            fill_price=2331.5,
            fill_quantity=0.1,
            payload={"trade": {"side": "SELL", "price": "2331.5"}},
            created_at=now - timedelta(minutes=1),
        )
    )
    session.commit()


def test_market_chart_markers_return_compact_event_rows(db_session) -> None:
    _seed_chart_marker_rows(db_session)

    markers = get_market_chart_markers(db_session, symbol="ETHUSDT", limit=80)

    assert [marker["kind"] for marker in markers] == ["ai", "risk_blocked", "execution"]
    assert markers[0]["sourceId"] == "ai:101"
    assert markers[0]["price"] == 2320.0
    assert markers[1]["sourceId"] == "risk:1"
    assert markers[1]["reasonCodes"] == ["ENTRY_TRIGGER_NOT_MET"]
    assert markers[1]["price"] == 2320.5
    assert markers[2]["sourceId"] == "execution:1"
    assert markers[2]["price"] == 2331.5
    assert all("payload" not in marker for marker in markers)


def test_market_chart_markers_api_uses_single_compact_payload(testclient_db_factory) -> None:
    session_factory = testclient_db_factory("market-chart-markers.db")
    with session_factory() as session:
        _seed_chart_marker_rows(session)

    with TestClient(app) as client:
        response = client.get("/api/market/chart-markers?symbol=ETHUSDT&limit=80")

    assert response.status_code == 200
    markers = response.json()
    assert [marker["kind"] for marker in markers] == ["ai", "risk_blocked", "execution"]
    assert all("payload" not in marker for marker in markers)


def test_get_risk_checks_limits_rows_before_decision_join(db_session) -> None:
    now = utcnow_naive()
    for index in range(3):
        db_session.add(
            RiskCheck(
                symbol="ETHUSDT",
                decision_run_id=None,
                market_snapshot_id=index,
                allowed=index == 2,
                decision="long",
                reason_codes=[f"REASON_{index}"],
                approved_risk_pct=0.0,
                approved_leverage=0.0,
                payload={"reason_codes": [f"REASON_{index}"]},
                created_at=now + timedelta(minutes=index),
            )
        )
    db_session.commit()

    rows = get_risk_checks(db_session, limit=2, compact=True)

    assert [row["reason_codes"] for row in rows] == [["REASON_2"], ["REASON_1"]]
