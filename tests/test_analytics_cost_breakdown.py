from __future__ import annotations

from datetime import datetime, timedelta

import pytest
import trading_mvp.services.dashboard as dashboard_service
from fastapi.testclient import TestClient
from trading_mvp.main import app
from trading_mvp.models import AccountLedgerEntry, Execution, Order, Position
from trading_mvp.schemas import (
    AnalyticsCostBreakdownBucket,
    AnalyticsCostBreakdownDataQuality,
    AnalyticsCostBreakdownResponse,
    AnalyticsCostBreakdownSummary,
    DashboardProfitabilityCostBreakdown,
    LimitedLiveReadinessReport,
)
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


def test_cost_breakdown_schema_backfills_unknown_slippage_publication_reason() -> None:
    start_at = datetime(2026, 5, 26, 0, 0, 0)
    end_at = datetime(2026, 5, 27, 0, 0, 0)

    data_quality = AnalyticsCostBreakdownDataQuality(
        funding_sync_status="COMPLETE",
        slippage_data_status="UNKNOWN",
    )
    bucket = AnalyticsCostBreakdownBucket(label="2026-05-26", start_at=start_at, end_at=end_at)
    response = AnalyticsCostBreakdownResponse(
        period="today",
        timezone="Asia/Seoul",
        start_at=start_at,
        end_at=end_at,
        summary=AnalyticsCostBreakdownSummary(),
        buckets=[bucket],
        data_quality=data_quality,
        warnings=[],
    )
    profitability_cost = DashboardProfitabilityCostBreakdown(window_label="today")

    assert data_quality.slippage_data_status == "UNKNOWN"
    assert data_quality.slippage_data_reason == "slippage_status_unknown"
    assert data_quality.warning_codes == ["slippage_data_status:UNKNOWN"]
    assert bucket.slippage_data_status == "UNKNOWN"
    assert bucket.slippage_data_reason == "slippage_status_unknown"
    assert bucket.warning_codes == ["slippage_data_status:UNKNOWN"]
    assert response.warnings == ["slippage_data_status:UNKNOWN"]
    assert profitability_cost.slippage_data_status == "UNKNOWN"
    assert profitability_cost.slippage_data_reason == "slippage_status_unknown"
    assert profitability_cost.warning_codes == ["slippage_data_status:UNKNOWN"]


def test_cost_breakdown_response_promotes_nested_warning_codes() -> None:
    start_at = datetime(2026, 5, 26, 0, 0, 0)
    end_at = datetime(2026, 5, 27, 0, 0, 0)

    response = AnalyticsCostBreakdownResponse(
        period="today",
        timezone="Asia/Seoul",
        start_at=start_at,
        end_at=end_at,
        summary=AnalyticsCostBreakdownSummary(),
        buckets=[
            AnalyticsCostBreakdownBucket(
                label="2026-05-26",
                start_at=start_at,
                end_at=end_at,
                slippage_data_status="COMPLETE",
                warning_codes=["fee_asset_conversion_unavailable:BNB"],
            )
        ],
        data_quality=AnalyticsCostBreakdownDataQuality(
            funding_sync_status="stale",
            slippage_data_status="COMPLETE",
        ),
        warnings=[],
    )

    assert response.data_quality.funding_sync_status == "STALE"
    assert response.data_quality.funding_sync_reason == "funding_sync_stale"
    assert response.data_quality.warning_codes == ["funding_sync_status:STALE"]
    assert response.buckets[0].warning_codes == ["fee_asset_conversion_unavailable:BNB"]
    assert response.warnings == [
        "funding_sync_status:STALE",
        "fee_asset_conversion_unavailable:BNB",
    ]


def test_cost_breakdown_schema_normalizes_dynamic_warning_codes() -> None:
    start_at = datetime(2026, 5, 26, 0, 0, 0)
    end_at = datetime(2026, 5, 27, 0, 0, 0)

    profitability_cost = DashboardProfitabilityCostBreakdown(
        window_label="today",
        slippage_data_status="NO_SAMPLE",
        funding_sync_status="STALE",
        warning_codes=[
            "slippage_data_status:UNKNOWN",
            " slippage_data_status:no_sample ",
            "funding_sync_status:UNKNOWN",
            "funding_sync_status:stale",
            " Fee_Asset_Conversion_Unavailable: bnb ",
            "fee_asset_conversion_unavailable:BNB",
            "slippage_data_status:NO_SAMPLE",
        ],
    )
    data_quality = AnalyticsCostBreakdownDataQuality(
        funding_sync_status="stale",
        slippage_data_status="NOT_READY",
        warning_codes=[
            "execution_sync_status:UNKNOWN",
            " execution_sync_status:unknown ",
            "slippage_data_status:UNKNOWN",
            " slippage_data_status:not_ready ",
            "funding_sync_status:UNKNOWN",
            "funding_sync_status:stale",
        ],
    )
    bucket = AnalyticsCostBreakdownBucket(
        label="2026-05-26",
        start_at=start_at,
        end_at=end_at,
        slippage_data_status="BLOCKED",
        warning_codes=[
            "slippage_data_status:UNKNOWN",
            " slippage_data_status:blocked ",
            "slippage_data_status:BLOCKED",
            " Funding_Asset_Conversion_Unavailable: btc ",
            "funding_asset_conversion_unavailable:BTC",
        ],
    )
    response = AnalyticsCostBreakdownResponse(
        period="today",
        timezone="Asia/Seoul",
        start_at=start_at,
        end_at=end_at,
        summary=AnalyticsCostBreakdownSummary(),
        buckets=[bucket],
        data_quality=data_quality,
        warnings=[
            "slippage_data_status:UNKNOWN",
            " slippage_data_status:not_ready ",
            "funding_sync_status:UNKNOWN",
            " Fee_Asset_Conversion_Unavailable: bnb ",
        ],
    )

    assert profitability_cost.warning_codes == [
        "slippage_data_status:NO_SAMPLE",
        "funding_sync_status:STALE",
        "fee_asset_conversion_unavailable:BNB",
    ]
    assert data_quality.warning_codes == [
        "execution_sync_status:UNKNOWN",
        "slippage_data_status:NOT_READY",
        "funding_sync_status:STALE",
    ]
    assert bucket.warning_codes == [
        "slippage_data_status:BLOCKED",
        "funding_asset_conversion_unavailable:BTC",
    ]
    assert response.warnings == [
        "slippage_data_status:NOT_READY",
        "fee_asset_conversion_unavailable:BNB",
        "execution_sync_status:UNKNOWN",
        "funding_sync_status:STALE",
        "slippage_data_status:BLOCKED",
        "funding_asset_conversion_unavailable:BTC",
    ]


def test_profitability_cost_schema_backfills_execution_sync_publication() -> None:
    cost = DashboardProfitabilityCostBreakdown(
        window_label="today",
        slippage_data_status="COMPLETE",
        execution_sync_status=" complete ",
        missing_close_execution_count=2,
        warning_codes=[
            " execution_sync_status:unknown ",
            "execution_sync_status:INCOMPLETE",
        ],
    )

    assert cost.execution_sync_status == "INCOMPLETE"
    assert cost.missing_close_execution_count == 2
    assert cost.warning_codes == [
        "execution_sync_status:INCOMPLETE",
        "missing_close_execution_count:2",
    ]


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ("NOT_READY", "slippage_not_ready"),
        ("BLOCKED", "slippage_blocked"),
    ],
)
def test_cost_breakdown_schema_accepts_not_ready_and_blocked_slippage_publication(
    status: str,
    reason: str,
) -> None:
    start_at = datetime(2026, 5, 26, 0, 0, 0)
    end_at = datetime(2026, 5, 27, 0, 0, 0)

    data_quality = AnalyticsCostBreakdownDataQuality(
        funding_sync_status="COMPLETE",
        slippage_data_status=status,
    )
    bucket = AnalyticsCostBreakdownBucket(
        label="2026-05-26",
        start_at=start_at,
        end_at=end_at,
        slippage_data_status=status,
    )
    response = AnalyticsCostBreakdownResponse(
        period="today",
        timezone="Asia/Seoul",
        start_at=start_at,
        end_at=end_at,
        summary=AnalyticsCostBreakdownSummary(),
        buckets=[bucket],
        data_quality=data_quality,
        warnings=[],
    )
    profitability_cost = DashboardProfitabilityCostBreakdown(window_label="today", slippage_data_status=status)

    assert data_quality.slippage_data_reason == reason
    assert data_quality.warning_codes == [f"slippage_data_status:{status}"]
    assert bucket.slippage_data_reason == reason
    assert bucket.warning_codes == [f"slippage_data_status:{status}"]
    assert response.warnings == [f"slippage_data_status:{status}"]
    assert profitability_cost.slippage_data_reason == reason
    assert profitability_cost.warning_codes == [f"slippage_data_status:{status}"]


@pytest.mark.parametrize("status", ["UNKNOWN", "NOT_READY", "BLOCKED"])
def test_profitability_readiness_merges_schema_backfilled_slippage_warning(status: str) -> None:
    cost = DashboardProfitabilityCostBreakdown(
        window_label="today",
        slippage_data_status=status,
        warning_codes=[],
    )
    readiness = LimitedLiveReadinessReport(
        status="watch",
        reason_codes=["insufficient_sample"],
    )

    merged = dashboard_service._readiness_with_profitability_cost_reasons(readiness, [cost])

    expected_code = f"slippage_data_status:{status}"
    assert cost.warning_codes == [expected_code]
    assert merged.status == "not_ready"
    assert merged.reason_codes == ["insufficient_sample", expected_code]


def test_profitability_readiness_synthesizes_missing_slippage_warning_from_status() -> None:
    cost = DashboardProfitabilityCostBreakdown(
        window_label="today",
        slippage_data_status="NO_SAMPLE",
    ).model_copy(update={"warning_codes": []})
    readiness = LimitedLiveReadinessReport(
        status="watch",
        reason_codes=["insufficient_sample"],
    )

    merged = dashboard_service._readiness_with_profitability_cost_reasons(readiness, [cost])

    assert cost.warning_codes == []
    assert merged.status == "not_ready"
    assert merged.reason_codes == ["insufficient_sample", "slippage_data_status:NO_SAMPLE"]


def test_profitability_readiness_normalizes_stale_slippage_warning_code() -> None:
    cost = DashboardProfitabilityCostBreakdown(
        window_label="today",
        slippage_data_status="COMPLETE",
        warning_codes=[
            " Slippage_Data_Status:no_sample ",
            "SLIPPAGE_DATA_STATUS:NO_SAMPLE",
        ],
    )
    readiness = LimitedLiveReadinessReport(
        status="watch",
        reason_codes=["insufficient_sample"],
    )

    merged = dashboard_service._readiness_with_profitability_cost_reasons(readiness, [cost])

    assert merged.status == "not_ready"
    assert merged.reason_codes == ["insufficient_sample", "slippage_data_status:NO_SAMPLE"]


def test_profitability_readiness_merges_funding_sync_warning_code() -> None:
    cost = DashboardProfitabilityCostBreakdown(
        window_label="today",
        slippage_data_status="COMPLETE",
        funding_sync_status="COMPLETE",
        warning_codes=[
            " Funding_Sync_Status:stale ",
            "funding_sync_status:STALE",
        ],
    )
    readiness = LimitedLiveReadinessReport(
        status="watch",
        reason_codes=["insufficient_sample"],
    )

    merged = dashboard_service._readiness_with_profitability_cost_reasons(readiness, [cost])

    assert merged.status == "not_ready"
    assert merged.reason_codes == ["insufficient_sample", "funding_sync_status:STALE"]


def test_profitability_readiness_synthesizes_missing_funding_warning_from_status() -> None:
    cost = DashboardProfitabilityCostBreakdown(
        window_label="today",
        slippage_data_status="COMPLETE",
        funding_sync_status="STALE",
    ).model_copy(update={"warning_codes": []})
    readiness = LimitedLiveReadinessReport(
        status="watch",
        reason_codes=["insufficient_sample"],
    )

    merged = dashboard_service._readiness_with_profitability_cost_reasons(readiness, [cost])

    assert cost.warning_codes == []
    assert cost.funding_sync_reason == "funding_sync_stale"
    assert merged.status == "not_ready"
    assert merged.reason_codes == ["insufficient_sample", "funding_sync_status:STALE"]


def test_profitability_readiness_merges_execution_sync_warning_code() -> None:
    cost = DashboardProfitabilityCostBreakdown(
        window_label="today",
        slippage_data_status="COMPLETE",
        execution_sync_status="INCOMPLETE",
        missing_close_execution_count=1,
    )
    readiness = LimitedLiveReadinessReport(
        status="watch",
        reason_codes=["insufficient_sample"],
    )

    merged = dashboard_service._readiness_with_profitability_cost_reasons(readiness, [cost])

    assert cost.warning_codes == [
        "execution_sync_status:INCOMPLETE",
        "missing_close_execution_count:1",
    ]
    assert merged.status == "not_ready"
    assert merged.reason_codes == ["insufficient_sample", "execution_sync_status:INCOMPLETE"]


def test_profitability_readiness_normalizes_existing_cost_reason_codes() -> None:
    cost = DashboardProfitabilityCostBreakdown(
        window_label="today",
        slippage_data_status="NO_SAMPLE",
        funding_sync_status="STALE",
    ).model_copy(update={"warning_codes": []})
    readiness = LimitedLiveReadinessReport(
        status="watch",
        reason_codes=[
            "insufficient_sample",
            " slippage_data_status:no_sample ",
            "Funding_Sync_Status:stale",
        ],
    )

    merged = dashboard_service._readiness_with_profitability_cost_reasons(readiness, [cost])

    assert cost.warning_codes == []
    assert merged.status == "not_ready"
    assert merged.reason_codes == [
        "insufficient_sample",
        "slippage_data_status:NO_SAMPLE",
        "funding_sync_status:STALE",
    ]


def test_profitability_today_cost_window_uses_cost_breakdown_kst_boundary(db_session, monkeypatch) -> None:
    fixed_now = datetime(2026, 5, 26, 15, 30)
    previous_kst_day_execution = datetime(2026, 5, 26, 10, 0)
    _seed_live_execution(
        db_session,
        label="previous-kst-day-execution",
        created_at=previous_kst_day_execution,
        realized_pnl=1.0,
        fee_paid=0.1,
        signed_slippage_bps=None,
    )
    db_session.commit()

    monkeypatch.setattr(dashboard_service, "utcnow_naive", lambda: fixed_now)

    payload = dashboard_service.get_profitability_dashboard(db_session)
    today = payload.cost_breakdowns[0]

    assert dashboard_service._profitability_window_since("today", None, fixed_now) == datetime(2026, 5, 26, 15, 0)
    assert today.window_label == "today"
    assert today.status == "no_data"
    assert today.slippage_data_status == "NO_SAMPLE"
    assert today.slippage_data_reason == "no_execution_slippage_sample"
    assert "slippage_data_status:NO_SAMPLE" in today.warning_codes
    assert "slippage_data_status:NO_SAMPLE" in payload.limited_live_readiness.reason_codes


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
    assert payload.data_quality.slippage_data_reason is None
    assert payload.data_quality.slippage_sample_count == 1
    assert payload.data_quality.missing_slippage_sample_count == 0
    assert len(payload.buckets) == 31

    bucket_by_label = {bucket.label: bucket for bucket in payload.buckets}
    may_first = bucket_by_label["2026-05-01"]
    assert may_first.gross_pnl_usdt == pytest.approx(5.33, abs=1e-9)
    assert may_first.net_pnl_usdt == pytest.approx(4.49, abs=1e-9)
    assert may_first.slippage_data_status == "COMPLETE"
    assert may_first.slippage_data_reason is None
    assert may_first.slippage_sample_count == 1
    assert may_first.missing_slippage_sample_count == 0

    may_second = bucket_by_label["2026-05-02"]
    assert may_second.signed_slippage_bps is None
    assert may_second.slippage_data_status == "NO_SAMPLE"
    assert may_second.slippage_data_reason == "no_execution_slippage_sample"
    assert may_second.slippage_sample_count == 0
    assert may_second.missing_slippage_sample_count == 0


def test_cost_breakdown_marks_empty_period_slippage_as_no_sample(db_session) -> None:
    _mark_account_sync_complete(db_session)

    payload = get_analytics_cost_breakdown(db_session, period="month", year=2026, month=5)

    assert payload.summary.net_pnl_usdt == pytest.approx(0.0, abs=1e-9)
    assert payload.summary.signed_slippage_bps is None
    assert payload.summary.adverse_slippage_bps is None
    assert payload.data_quality.realized_pnl_confirmed is True
    assert payload.data_quality.execution_sync_status == "COMPLETE"
    assert payload.data_quality.funding_sync_status == "COMPLETE"
    assert payload.data_quality.slippage_data_status == "NO_SAMPLE"
    assert payload.data_quality.slippage_data_reason == "no_execution_slippage_sample"
    assert payload.data_quality.slippage_sample_count == 0
    assert payload.data_quality.missing_slippage_sample_count == 0
    assert payload.warnings == ["slippage_data_status:NO_SAMPLE"]


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
    assert payload.data_quality.slippage_data_reason == "missing_execution_slippage_sample"
    assert payload.data_quality.slippage_sample_count == 0
    assert payload.data_quality.missing_slippage_sample_count == 1
    assert payload.data_quality.warning_codes == [
        "execution_sync_status:INCOMPLETE",
        "missing_close_execution_count:1",
        "funding_sync_status:STALE",
        "slippage_data_status:INCOMPLETE",
    ]
    assert "execution_sync_status:INCOMPLETE" in payload.warnings
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
    assert payload["data_quality"]["slippage_data_status"] == "COMPLETE"
    assert payload["data_quality"]["slippage_data_reason"] is None
    assert payload["data_quality"]["slippage_sample_count"] == 1
    assert payload["data_quality"]["missing_slippage_sample_count"] == 0
    assert len(payload["buckets"]) == 31
    assert payload["buckets"][0]["label"] == "2026-05-01"
    assert payload["buckets"][0]["slippage_data_status"] == "COMPLETE"
    assert payload["buckets"][0]["slippage_data_reason"] is None
    assert payload["buckets"][0]["slippage_sample_count"] == 1
    assert payload["buckets"][0]["missing_slippage_sample_count"] == 0
    assert payload["buckets"][1]["label"] == "2026-05-02"
    assert payload["buckets"][1]["slippage_data_status"] == "NO_SAMPLE"
    assert payload["buckets"][1]["slippage_data_reason"] == "no_execution_slippage_sample"


def test_cost_breakdown_api_exposes_no_sample_slippage_reason(testclient_db_factory) -> None:
    testing_session = testclient_db_factory("analytics-cost-breakdown-no-sample.db")
    with testing_session() as session:
        _mark_account_sync_complete(session)
        session.commit()

    with TestClient(app) as client:
        response = client.get("/api/analytics/cost-breakdown?period=month&year=2026&month=5")

    assert response.status_code == 200
    payload = response.json()
    assert payload["period"] == "month"
    assert payload["summary"]["signed_slippage_bps"] is None
    assert payload["summary"]["adverse_slippage_bps"] is None
    assert payload["data_quality"]["funding_sync_status"] == "COMPLETE"
    assert payload["data_quality"]["slippage_data_status"] == "NO_SAMPLE"
    assert payload["data_quality"]["slippage_data_reason"] == "no_execution_slippage_sample"
    assert payload["data_quality"]["slippage_sample_count"] == 0
    assert payload["data_quality"]["missing_slippage_sample_count"] == 0
    assert "slippage_data_status:NO_SAMPLE" in payload["data_quality"]["warning_codes"]
    assert "slippage_data_status:NO_SAMPLE" in payload["warnings"]
    assert payload["buckets"][0]["label"] == "2026-05-01"
    assert payload["buckets"][0]["slippage_data_status"] == "NO_SAMPLE"
    assert payload["buckets"][0]["slippage_data_reason"] == "no_execution_slippage_sample"
    assert payload["buckets"][0]["slippage_sample_count"] == 0
    assert payload["buckets"][0]["missing_slippage_sample_count"] == 0
    assert "slippage_data_status:NO_SAMPLE" in payload["buckets"][0]["warning_codes"]


def test_cost_breakdown_api_exposes_incomplete_slippage_reason(testclient_db_factory) -> None:
    testing_session = testclient_db_factory("analytics-cost-breakdown-incomplete-slippage.db")
    with testing_session() as session:
        _mark_account_sync_complete(session)
        _seed_live_execution(
            session,
            label="api-incomplete-slippage",
            created_at=datetime(2026, 5, 26, 1, 0, 0),
            realized_pnl=1.0,
            fee_paid=0.1,
            signed_slippage_bps=None,
            requested_price=0.0,
            fill_price=0.0,
        )
        session.commit()

    with TestClient(app) as client:
        response = client.get("/api/analytics/cost-breakdown?period=month&year=2026&month=5")

    assert response.status_code == 200
    payload = response.json()
    assert payload["period"] == "month"
    assert payload["summary"]["signed_slippage_bps"] is None
    assert payload["summary"]["adverse_slippage_bps"] is None
    assert payload["data_quality"]["funding_sync_status"] == "COMPLETE"
    assert payload["data_quality"]["slippage_data_status"] == "INCOMPLETE"
    assert payload["data_quality"]["slippage_data_reason"] == "missing_execution_slippage_sample"
    assert payload["data_quality"]["slippage_sample_count"] == 0
    assert payload["data_quality"]["missing_slippage_sample_count"] == 1
    assert "slippage_data_status:INCOMPLETE" in payload["warnings"]

    bucket_by_label = {bucket["label"]: bucket for bucket in payload["buckets"]}
    may_26 = bucket_by_label["2026-05-26"]
    assert may_26["signed_slippage_bps"] is None
    assert may_26["adverse_slippage_bps"] is None
    assert may_26["slippage_data_status"] == "INCOMPLETE"
    assert may_26["slippage_data_reason"] == "missing_execution_slippage_sample"
    assert may_26["slippage_sample_count"] == 0
    assert may_26["missing_slippage_sample_count"] == 1


def test_cost_breakdown_today_api_exposes_no_sample_slippage_reason(testclient_db_factory) -> None:
    testing_session = testclient_db_factory("analytics-cost-breakdown-today-no-sample.db")
    with testing_session() as session:
        _mark_account_sync_complete(session)
        session.commit()

    with TestClient(app) as client:
        response = client.get("/api/analytics/cost-breakdown?period=today")

    assert response.status_code == 200
    payload = response.json()
    assert payload["period"] == "today"
    assert payload["summary"]["signed_slippage_bps"] is None
    assert payload["summary"]["adverse_slippage_bps"] is None
    assert payload["data_quality"]["funding_sync_status"] == "COMPLETE"
    assert payload["data_quality"]["slippage_data_status"] == "NO_SAMPLE"
    assert payload["data_quality"]["slippage_data_reason"] == "no_execution_slippage_sample"
    assert payload["data_quality"]["slippage_sample_count"] == 0
    assert payload["data_quality"]["missing_slippage_sample_count"] == 0
    assert "slippage_data_status:NO_SAMPLE" in payload["warnings"]
    assert payload["buckets"][0]["label"] == payload["start_at"][:10]
    assert payload["buckets"][0]["slippage_data_status"] == "NO_SAMPLE"
    assert payload["buckets"][0]["slippage_data_reason"] == "no_execution_slippage_sample"
    assert payload["buckets"][0]["slippage_sample_count"] == 0
    assert payload["buckets"][0]["missing_slippage_sample_count"] == 0


def test_cost_breakdown_no_sample_slippage_matches_profitability_api(testclient_db_factory) -> None:
    testing_session = testclient_db_factory("analytics-profitability-no-sample-match.db")
    with testing_session() as session:
        _mark_account_sync_complete(session)
        session.commit()

    with TestClient(app) as client:
        cost_response = client.get("/api/analytics/cost-breakdown?period=today")
        profitability_response = client.get("/api/dashboard/profitability")
        operator_response = client.get("/api/dashboard/operator")

    assert cost_response.status_code == 200
    assert profitability_response.status_code == 200
    assert operator_response.status_code == 200
    cost_payload = cost_response.json()
    profitability_payload = profitability_response.json()
    operator_payload = operator_response.json()
    cost_today = cost_payload["buckets"][0]
    profitability_today = profitability_payload["cost_breakdowns"][0]
    operator_today = operator_payload["market_signal"]["profitability_cost_breakdowns"][0]

    for published_today in (profitability_today, operator_today):
        assert published_today["window_label"] == "today"
        for key in (
            "slippage_data_status",
            "slippage_data_reason",
            "funding_sync_status",
            "funding_sync_reason",
            "slippage_sample_count",
            "missing_slippage_sample_count",
        ):
            assert published_today[key] == cost_payload["data_quality"][key]
            assert published_today[key] == cost_today[key]
        assert published_today["slippage_data_status"] == "NO_SAMPLE"
        assert published_today["slippage_data_reason"] == "no_execution_slippage_sample"
        assert published_today["funding_sync_status"] == cost_payload["data_quality"]["funding_sync_status"]
        assert published_today["funding_sync_status"] == "COMPLETE"
        assert published_today["funding_sync_reason"] is None
        assert "slippage_data_status:NO_SAMPLE" in published_today["warning_codes"]
    assert "slippage_data_status:NO_SAMPLE" in cost_today["warning_codes"]
    assert "slippage_data_status:NO_SAMPLE" in cost_payload["warnings"]
    assert "slippage_data_status:NO_SAMPLE" in profitability_payload["limited_live_readiness"]["reason_codes"]
    assert "slippage_data_status:NO_SAMPLE" in operator_payload["control"]["limited_live_readiness"]["reason_codes"]


def test_cost_breakdown_incomplete_slippage_matches_profitability_api(testclient_db_factory) -> None:
    testing_session = testclient_db_factory("analytics-profitability-incomplete-match.db")
    with testing_session() as session:
        _mark_account_sync_complete(session)
        _seed_live_execution(
            session,
            label="today-incomplete-slippage",
            created_at=utcnow_naive() - timedelta(minutes=5),
            realized_pnl=1.0,
            fee_paid=0.1,
            signed_slippage_bps=None,
            requested_price=0.0,
            fill_price=0.0,
        )
        session.commit()

    with TestClient(app) as client:
        cost_response = client.get("/api/analytics/cost-breakdown?period=today")
        profitability_response = client.get("/api/dashboard/profitability")
        operator_response = client.get("/api/dashboard/operator")

    assert cost_response.status_code == 200
    assert profitability_response.status_code == 200
    assert operator_response.status_code == 200
    cost_payload = cost_response.json()
    profitability_payload = profitability_response.json()
    operator_payload = operator_response.json()
    profitability_today = profitability_payload["cost_breakdowns"][0]
    operator_today = operator_payload["market_signal"]["profitability_cost_breakdowns"][0]
    cost_today = cost_payload["buckets"][0]

    for published_today in (profitability_today, operator_today):
        assert published_today["window_label"] == "today"
        for key in (
            "slippage_data_status",
            "slippage_data_reason",
            "slippage_sample_count",
            "missing_slippage_sample_count",
        ):
            assert published_today[key] == cost_payload["data_quality"][key]
            assert published_today[key] == cost_today[key]
        assert published_today["slippage_data_status"] == "INCOMPLETE"
        assert published_today["slippage_data_reason"] == "missing_execution_slippage_sample"
        assert "slippage_data_status:INCOMPLETE" in published_today["warning_codes"]
    assert "slippage_data_status:INCOMPLETE" in cost_today["warning_codes"]
    assert "slippage_data_status:INCOMPLETE" in cost_payload["warnings"]
    assert "slippage_data_status:INCOMPLETE" in profitability_payload["limited_live_readiness"]["reason_codes"]
    assert "slippage_data_status:INCOMPLETE" in operator_payload["control"]["limited_live_readiness"]["reason_codes"]


def test_cost_breakdown_missing_close_execution_matches_profitability_and_operator_api(
    testclient_db_factory,
) -> None:
    testing_session = testclient_db_factory("analytics-profitability-missing-close-match.db")
    with testing_session() as session:
        _mark_account_sync_complete(session)
        _seed_missing_close_execution(
            session,
            created_at=utcnow_naive() - timedelta(minutes=30),
        )
        session.commit()

    with TestClient(app) as client:
        cost_response = client.get("/api/analytics/cost-breakdown?period=today")
        profitability_response = client.get("/api/dashboard/profitability")
        operator_response = client.get("/api/dashboard/operator")

    assert cost_response.status_code == 200
    assert profitability_response.status_code == 200
    assert operator_response.status_code == 200
    cost_payload = cost_response.json()
    profitability_payload = profitability_response.json()
    operator_payload = operator_response.json()
    profitability_today = profitability_payload["cost_breakdowns"][0]
    operator_today = operator_payload["market_signal"]["profitability_cost_breakdowns"][0]

    assert cost_payload["data_quality"]["execution_sync_status"] == "INCOMPLETE"
    assert cost_payload["data_quality"]["missing_close_execution_count"] == 1
    assert "execution_sync_status:INCOMPLETE" in cost_payload["warnings"]
    assert "missing_close_execution_count:1" in cost_payload["warnings"]

    for published_today in (profitability_today, operator_today):
        assert published_today["window_label"] == "today"
        assert published_today["execution_sync_status"] == cost_payload["data_quality"]["execution_sync_status"]
        assert (
            published_today["missing_close_execution_count"]
            == cost_payload["data_quality"]["missing_close_execution_count"]
        )
        assert published_today["execution_sync_status"] == "INCOMPLETE"
        assert published_today["missing_close_execution_count"] == 1
        assert "execution_sync_status:INCOMPLETE" in published_today["warning_codes"]
        assert "missing_close_execution_count:1" in published_today["warning_codes"]

    assert "execution_sync_status:INCOMPLETE" in profitability_payload["limited_live_readiness"]["reason_codes"]
    assert "execution_sync_status:INCOMPLETE" in operator_payload["control"]["limited_live_readiness"]["reason_codes"]


def test_cost_breakdown_stale_funding_warning_matches_profitability_and_operator_api(testclient_db_factory) -> None:
    testing_session = testclient_db_factory("analytics-profitability-stale-funding-match.db")
    stale_synced_at = utcnow_naive() - timedelta(hours=2)
    with testing_session() as session:
        settings = get_or_create_settings(session)
        mark_sync_success(settings, scope="account", synced_at=stale_synced_at, stale_after_seconds=60)
        session.add(settings)
        session.commit()

    with TestClient(app) as client:
        cost_response = client.get("/api/analytics/cost-breakdown?period=today")
        profitability_response = client.get("/api/dashboard/profitability")
        operator_response = client.get("/api/dashboard/operator")

    assert cost_response.status_code == 200
    assert profitability_response.status_code == 200
    assert operator_response.status_code == 200
    cost_payload = cost_response.json()
    profitability_payload = profitability_response.json()
    operator_payload = operator_response.json()
    cost_today = cost_payload["buckets"][0]
    profitability_today = profitability_payload["cost_breakdowns"][0]
    operator_today = operator_payload["market_signal"]["profitability_cost_breakdowns"][0]

    assert cost_payload["data_quality"]["funding_sync_status"] == "STALE"
    assert cost_payload["data_quality"]["funding_sync_reason"] == "funding_sync_stale"
    assert "funding_sync_status:STALE" in cost_payload["data_quality"]["warning_codes"]
    assert cost_today["funding_sync_status"] == "STALE"
    assert cost_today["funding_sync_reason"] == cost_payload["data_quality"]["funding_sync_reason"]
    assert "funding_sync_status:STALE" in cost_today["warning_codes"]
    assert profitability_today["funding_sync_status"] == "STALE"
    assert operator_today["funding_sync_status"] == "STALE"
    assert profitability_today["funding_sync_reason"] == cost_payload["data_quality"]["funding_sync_reason"]
    assert operator_today["funding_sync_reason"] == cost_payload["data_quality"]["funding_sync_reason"]
    assert profitability_today["funding_sync_reason"] == "funding_sync_stale"
    assert operator_today["funding_sync_reason"] == "funding_sync_stale"
    assert "funding_sync_status:STALE" in cost_payload["warnings"]
    assert "funding_sync_status:STALE" in profitability_today["warning_codes"]
    assert "funding_sync_status:STALE" in operator_today["warning_codes"]
    assert "funding_sync_status:STALE" in profitability_payload["limited_live_readiness"]["reason_codes"]
    assert "funding_sync_status:STALE" in operator_payload["control"]["limited_live_readiness"]["reason_codes"]


def test_profitability_cache_tracks_funding_sync_status_changes(testclient_db_factory) -> None:
    testing_session = testclient_db_factory("profitability-funding-sync-cache-key.db")
    stale_synced_at = utcnow_naive() - timedelta(hours=2)
    with testing_session() as session:
        settings = get_or_create_settings(session)
        mark_sync_success(settings, scope="account", synced_at=stale_synced_at, stale_after_seconds=60)
        session.add(settings)
        session.commit()

    with TestClient(app) as client:
        stale_response = client.get("/api/dashboard/profitability")

    assert stale_response.status_code == 200
    assert stale_response.json()["cost_breakdowns"][0]["funding_sync_status"] == "STALE"

    with testing_session() as session:
        _mark_account_sync_complete(session)
        session.commit()

    with TestClient(app) as client:
        cost_response = client.get("/api/analytics/cost-breakdown?period=today")
        profitability_response = client.get("/api/dashboard/profitability")

    assert cost_response.status_code == 200
    assert profitability_response.status_code == 200
    cost_payload = cost_response.json()
    profitability_payload = profitability_response.json()
    profitability_today = profitability_payload["cost_breakdowns"][0]

    assert cost_payload["data_quality"]["funding_sync_status"] == "COMPLETE"
    assert profitability_today["funding_sync_status"] == "COMPLETE"
    assert "funding_sync_status:STALE" not in profitability_today["warning_codes"]
    assert "funding_sync_status:STALE" not in profitability_payload["limited_live_readiness"]["reason_codes"]
