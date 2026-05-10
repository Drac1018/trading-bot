from __future__ import annotations

from datetime import datetime, timedelta

import pytest
import trading_mvp.services.dashboard as dashboard_service
from fastapi.testclient import TestClient
from trading_mvp.main import app
from trading_mvp.models import AccountLedgerEntry, Execution, Order, Position
from trading_mvp.services.dashboard import get_analytics_cost_breakdown
from trading_mvp.services.runtime_state import mark_sync_success
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive


def _mark_account_sync_complete(db_session) -> None:
    settings = get_or_create_settings(db_session)
    mark_sync_success(settings, scope="account", synced_at=utcnow_naive(), stale_after_seconds=3600)
    db_session.add(settings)
    db_session.flush()


def _seed_live_execution(
    db_session,
    *,
    label: str,
    created_at: datetime,
    realized_pnl: float,
    fee_paid: float,
    funding: float = 0.0,
    signed_slippage_bps: float | None = 0.12,
    requested_price: float = 100.0,
    fill_price: float = 100.0,
    commission_asset: str = "USDT",
    mode: str = "live",
) -> None:
    order = Order(
        symbol="BTCUSDT",
        decision_run_id=None,
        risk_check_id=None,
        position_id=None,
        side="buy",
        order_type="market",
        mode=mode,
        status="filled",
        external_order_id=f"{label}-order",
        client_order_id=f"{label}-client",
        reduce_only=True,
        close_only=True,
        parent_order_id=None,
        exchange_status="FILLED",
        requested_quantity=1.0,
        requested_price=requested_price,
        filled_quantity=1.0,
        average_fill_price=fill_price,
        reason_codes=[],
        metadata_json={},
        created_at=created_at,
        updated_at=created_at,
    )
    db_session.add(order)
    db_session.flush()

    payload = {} if signed_slippage_bps is None else {"signed_slippage_bps": signed_slippage_bps}
    db_session.add(
        Execution(
            order_id=order.id,
            position_id=None,
            symbol="BTCUSDT",
            status="filled",
            external_trade_id=f"{label}-trade",
            fill_price=fill_price,
            fill_quantity=1.0,
            fee_paid=fee_paid,
            commission_asset=commission_asset,
            slippage_pct=0.0,
            realized_pnl=realized_pnl,
            payload=payload,
            created_at=created_at,
            updated_at=created_at,
        )
    )
    if funding:
        db_session.add(
            AccountLedgerEntry(
                entry_type="funding",
                asset="USDT",
                symbol="BTCUSDT",
                amount=funding,
                external_ref_id=f"{label}-funding",
                occurred_at=created_at + timedelta(minutes=5),
                payload={"incomeType": "FUNDING_FEE"},
                created_at=created_at,
                updated_at=created_at,
            )
        )
    db_session.flush()


def _seed_missing_close_execution(db_session, *, created_at: datetime) -> None:
    position = Position(
        symbol="BTCUSDT",
        mode="live",
        side="short",
        status="closed",
        quantity=0.0,
        entry_price=81536.6,
        mark_price=81089.0,
        leverage=1.0,
        stop_loss=82000.0,
        take_profit=81089.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        opened_at=created_at,
        closed_at=created_at + timedelta(minutes=20),
        metadata_json={},
    )
    db_session.add(position)
    db_session.flush()

    entry_order = Order(
        symbol="BTCUSDT",
        position_id=position.id,
        side="sell",
        order_type="limit",
        mode="live",
        status="filled",
        exchange_status="FILLED",
        external_order_id="missing-close-entry",
        requested_quantity=0.001,
        requested_price=0.0,
        filled_quantity=0.001,
        average_fill_price=0.0,
        reason_codes=[],
        metadata_json={},
        created_at=created_at,
        updated_at=created_at,
    )
    protective_order = Order(
        symbol="BTCUSDT",
        position_id=position.id,
        side="buy",
        order_type="take_profit_market",
        mode="live",
        status="expired",
        exchange_status="FINISHED",
        external_order_id="missing-close-protective",
        reduce_only=True,
        close_only=True,
        requested_quantity=0.001,
        requested_price=81089.0,
        filled_quantity=0.0,
        average_fill_price=0.0,
        reason_codes=[],
        metadata_json={},
        created_at=created_at + timedelta(minutes=20),
        updated_at=created_at + timedelta(minutes=20),
    )
    db_session.add_all([entry_order, protective_order])
    db_session.flush()
    db_session.add(
        Execution(
            order_id=entry_order.id,
            position_id=position.id,
            symbol="BTCUSDT",
            status="filled",
            external_trade_id="missing-close-entry-fill",
            fill_price=0.0,
            fill_quantity=0.001,
            fee_paid=0.04,
            commission_asset="USDT",
            slippage_pct=0.0,
            realized_pnl=0.0,
            payload={},
            created_at=created_at + timedelta(minutes=1),
            updated_at=created_at + timedelta(minutes=1),
        )
    )
    db_session.flush()


def test_month_cost_breakdown_reuses_today_formula_and_daily_buckets(db_session) -> None:
    _mark_account_sync_complete(db_session)
    _seed_live_execution(
        db_session,
        label="may-profit",
        created_at=datetime(2026, 5, 1, 1, 0, 0),
        realized_pnl=5.33,
        fee_paid=0.86,
        funding=0.02,
        signed_slippage_bps=0.12,
    )
    _seed_live_execution(
        db_session,
        label="outside-month",
        created_at=datetime(2026, 6, 1, 1, 0, 0),
        realized_pnl=99.0,
        fee_paid=1.0,
        funding=-1.0,
    )

    payload = get_analytics_cost_breakdown(db_session, period="month", year=2026, month=5)

    assert payload.period == "month"
    assert payload.timezone == "Asia/Seoul"
    assert payload.start_at.isoformat() == "2026-05-01T00:00:00+09:00"
    assert payload.end_at.isoformat() == "2026-06-01T00:00:00+09:00"
    assert payload.summary.gross_pnl_usdt == pytest.approx(5.33, abs=1e-9)
    assert payload.summary.fee_usdt == pytest.approx(0.86, abs=1e-9)
    assert payload.summary.funding_usdt == pytest.approx(0.02, abs=1e-9)
    assert payload.summary.net_pnl_usdt == pytest.approx(4.49, abs=1e-9)
    assert payload.summary.total_cost_usdt == pytest.approx(0.86, abs=1e-9)
    assert payload.summary.fee_ratio_pct == pytest.approx(16.2, abs=0.1)
    assert payload.summary.total_cost_ratio_pct == pytest.approx(16.2, abs=0.1)
    assert payload.summary.signed_slippage_bps == pytest.approx(0.12, abs=1e-9)
    assert payload.summary.adverse_slippage_bps == pytest.approx(0.12, abs=1e-9)
    assert payload.data_quality.realized_pnl_confirmed is True
    assert payload.data_quality.execution_sync_status == "COMPLETE"
    assert payload.data_quality.funding_sync_status == "COMPLETE"
    assert payload.data_quality.slippage_data_status == "COMPLETE"
    assert len(payload.buckets) == 31

    bucket_by_label = {bucket.label: bucket for bucket in payload.buckets}
    may_first = bucket_by_label["2026-05-01"]
    assert may_first.gross_pnl_usdt == pytest.approx(5.33, abs=1e-9)
    assert may_first.net_pnl_usdt == pytest.approx(4.49, abs=1e-9)


def test_year_cost_breakdown_returns_monthly_buckets(db_session) -> None:
    _mark_account_sync_complete(db_session)
    _seed_live_execution(
        db_session,
        label="may-year",
        created_at=datetime(2026, 5, 1, 1, 0, 0),
        realized_pnl=5.33,
        fee_paid=0.86,
        funding=0.02,
    )

    payload = get_analytics_cost_breakdown(db_session, period="year", year=2026)

    assert payload.start_at.isoformat() == "2026-01-01T00:00:00+09:00"
    assert payload.end_at.isoformat() == "2027-01-01T00:00:00+09:00"
    assert len(payload.buckets) == 12
    bucket_by_label = {bucket.label: bucket for bucket in payload.buckets}
    assert bucket_by_label["2026-05"].gross_pnl_usdt == pytest.approx(5.33, abs=1e-9)
    assert bucket_by_label["2026-05"].fee_usdt == pytest.approx(0.86, abs=1e-9)


def test_cost_breakdown_groups_single_range_rows_without_bucket_summary_rescans(db_session, monkeypatch) -> None:
    _mark_account_sync_complete(db_session)
    _seed_live_execution(
        db_session,
        label="january",
        created_at=datetime(2026, 1, 3, 1, 0, 0),
        realized_pnl=3.0,
        fee_paid=0.3,
        funding=0.1,
    )
    _seed_live_execution(
        db_session,
        label="may",
        created_at=datetime(2026, 5, 1, 1, 0, 0),
        realized_pnl=5.0,
        fee_paid=0.5,
        funding=-0.2,
    )

    summary_calls = 0
    source_calls = 0
    original_source_rows_for_range = dashboard_service._analytics_cost_source_rows_for_range

    def fail_if_range_summary_is_used(*_args, **_kwargs):
        nonlocal summary_calls
        summary_calls += 1
        raise AssertionError("_analytics_cost_summary_for_range should not be used for bucket grouping")

    def spy_source_rows_for_range(*args, **kwargs):
        nonlocal source_calls
        source_calls += 1
        return original_source_rows_for_range(*args, **kwargs)

    monkeypatch.setattr(dashboard_service, "_analytics_cost_summary_for_range", fail_if_range_summary_is_used)
    monkeypatch.setattr(dashboard_service, "_analytics_cost_source_rows_for_range", spy_source_rows_for_range)

    payload = dashboard_service.get_analytics_cost_breakdown(db_session, period="year", year=2026)

    assert summary_calls == 0
    assert source_calls == 1
    assert len(payload.buckets) == 12
    assert payload.summary.gross_pnl_usdt == pytest.approx(8.0, abs=1e-9)
    bucket_by_label = {bucket.label: bucket for bucket in payload.buckets}
    assert bucket_by_label["2026-01"].gross_pnl_usdt == pytest.approx(3.0, abs=1e-9)
    assert bucket_by_label["2026-05"].gross_pnl_usdt == pytest.approx(5.0, abs=1e-9)


def test_month_cost_breakdown_keeps_utc_bucket_boundaries_and_live_mode_filter(db_session) -> None:
    _mark_account_sync_complete(db_session)
    _seed_live_execution(
        db_session,
        label="before-month",
        created_at=datetime(2026, 4, 30, 14, 59, 59),
        realized_pnl=99.0,
        fee_paid=9.0,
    )
    _seed_live_execution(
        db_session,
        label="month-start",
        created_at=datetime(2026, 4, 30, 15, 0, 0),
        realized_pnl=1.0,
        fee_paid=0.1,
    )
    _seed_live_execution(
        db_session,
        label="next-day-boundary",
        created_at=datetime(2026, 5, 1, 15, 0, 0),
        realized_pnl=2.0,
        fee_paid=0.2,
    )
    _seed_live_execution(
        db_session,
        label="last-in-month",
        created_at=datetime(2026, 5, 31, 14, 59, 59),
        realized_pnl=4.0,
        fee_paid=0.4,
    )
    _seed_live_execution(
        db_session,
        label="month-end",
        created_at=datetime(2026, 5, 31, 15, 0, 0),
        realized_pnl=88.0,
        fee_paid=8.0,
    )
    _seed_live_execution(
        db_session,
        label="paper-mode",
        created_at=datetime(2026, 5, 1, 1, 0, 0),
        realized_pnl=77.0,
        fee_paid=7.0,
        mode="paper",
    )

    payload = get_analytics_cost_breakdown(db_session, period="month", year=2026, month=5)

    assert payload.summary.gross_pnl_usdt == pytest.approx(7.0, abs=1e-9)
    assert payload.summary.fee_usdt == pytest.approx(0.7, abs=1e-9)
    bucket_by_label = {bucket.label: bucket for bucket in payload.buckets}
    assert bucket_by_label["2026-05-01"].gross_pnl_usdt == pytest.approx(1.0, abs=1e-9)
    assert bucket_by_label["2026-05-02"].gross_pnl_usdt == pytest.approx(2.0, abs=1e-9)
    assert bucket_by_label["2026-05-31"].gross_pnl_usdt == pytest.approx(4.0, abs=1e-9)


def test_cost_breakdown_handles_negative_funding_and_non_positive_gross(db_session) -> None:
    _mark_account_sync_complete(db_session)
    _seed_live_execution(
        db_session,
        label="negative-funding",
        created_at=datetime(2026, 3, 5, 1, 0, 0),
        realized_pnl=5.0,
        fee_paid=1.0,
        funding=-0.5,
    )
    _seed_live_execution(
        db_session,
        label="negative-gross",
        created_at=datetime(2026, 4, 5, 1, 0, 0),
        realized_pnl=-2.0,
        fee_paid=0.2,
        funding=0.0,
    )

    march = get_analytics_cost_breakdown(db_session, period="month", year=2026, month=3)
    april = get_analytics_cost_breakdown(db_session, period="month", year=2026, month=4)

    assert march.summary.net_pnl_usdt == pytest.approx(3.5, abs=1e-9)
    assert march.summary.total_cost_usdt == pytest.approx(1.5, abs=1e-9)
    assert march.summary.total_cost_ratio_pct == pytest.approx(30.0, abs=1e-9)
    assert april.summary.gross_pnl_usdt == pytest.approx(-2.0, abs=1e-9)
    assert april.summary.fee_ratio_pct is None
    assert april.summary.total_cost_ratio_pct is None


def test_cost_breakdown_data_quality_flags_missing_close_slippage_and_stale_funding(db_session) -> None:
    settings = get_or_create_settings(db_session)
    mark_sync_success(
        settings,
        scope="account",
        synced_at=datetime(2026, 4, 1, 0, 0, 0),
        stale_after_seconds=60,
    )
    db_session.add(settings)
    _seed_missing_close_execution(db_session, created_at=datetime(2026, 5, 3, 1, 0, 0))

    payload = get_analytics_cost_breakdown(db_session, period="month", year=2026, month=5)

    assert payload.data_quality.realized_pnl_confirmed is False
    assert payload.data_quality.execution_sync_status == "INCOMPLETE"
    assert payload.data_quality.missing_close_execution_count == 1
    assert payload.data_quality.funding_sync_status == "STALE"
    assert payload.data_quality.slippage_data_status == "INCOMPLETE"
    assert "missing_close_execution_count:1" in payload.warnings
    assert "funding_sync_status:STALE" in payload.warnings
    assert "slippage_data_status:INCOMPLETE" in payload.warnings
    assert payload.summary.signed_slippage_bps is None
    assert payload.summary.adverse_slippage_bps is None


def test_cost_breakdown_api_returns_month_payload(testclient_db_factory) -> None:
    testing_session = testclient_db_factory("analytics-cost-breakdown.db")
    with testing_session() as session:
        _mark_account_sync_complete(session)
        _seed_live_execution(
            session,
            label="api-month",
            created_at=datetime(2026, 5, 1, 1, 0, 0),
            realized_pnl=5.33,
            fee_paid=0.86,
            funding=0.02,
        )
        session.commit()

    with TestClient(app) as client:
        response = client.get("/api/analytics/cost-breakdown?period=month&year=2026&month=5")

    assert response.status_code == 200
    payload = response.json()
    assert payload["period"] == "month"
    assert payload["summary"]["net_pnl_usdt"] == pytest.approx(4.49, abs=1e-9)
    assert len(payload["buckets"]) == 31
