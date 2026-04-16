from __future__ import annotations

from datetime import date, timedelta

import pytest
from trading_mvp.models import Execution, Order, PnLSnapshot
from trading_mvp.schemas import TradeDecision
from trading_mvp.services.account import (
    create_exchange_pnl_snapshot,
    get_latest_pnl_snapshot,
)
from trading_mvp.services.market_data import build_market_snapshot
from trading_mvp.services.risk import evaluate_risk
from trading_mvp.services.secret_store import encrypt_secret
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive


def _seed_live_close_order(
    db_session,
    *,
    label: str,
    symbol: str = "BTCUSDT",
    fills: list[tuple[float, float, object]],
) -> Order:
    first_fill_time = fills[0][2]
    order = Order(
        symbol=symbol,
        decision_run_id=None,
        risk_check_id=None,
        position_id=None,
        side="exit",
        order_type="market",
        mode="live",
        status="filled",
        external_order_id=f"{label}-order",
        client_order_id=f"{label}-client",
        reduce_only=True,
        close_only=True,
        parent_order_id=None,
        exchange_status="FILLED",
        last_exchange_update_at=first_fill_time,
        requested_quantity=1.0,
        requested_price=70000.0,
        filled_quantity=1.0,
        average_fill_price=70000.0,
        reason_codes=[],
        metadata_json={},
        created_at=first_fill_time,
        updated_at=first_fill_time,
    )
    db_session.add(order)
    db_session.flush()

    for index, (realized_pnl, fee_paid, created_at) in enumerate(fills, start=1):
        db_session.add(
            Execution(
                order_id=order.id,
                position_id=None,
                symbol=symbol,
                status="filled",
                external_trade_id=f"{label}-trade-{index}",
                fill_price=70000.0,
                fill_quantity=0.5,
                fee_paid=fee_paid,
                commission_asset="USDT",
                slippage_pct=0.0,
                realized_pnl=realized_pnl,
                payload={"label": label},
                created_at=created_at,
                updated_at=created_at,
            )
        )
    db_session.flush()
    return order


def _enable_live_entry(settings_row) -> None:
    settings_row.live_trading_enabled = True
    settings_row.manual_live_approval = True
    settings_row.live_execution_armed = True
    settings_row.live_execution_armed_until = utcnow_naive() + timedelta(minutes=15)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")


def _build_entry_decision(symbol: str = "BTCUSDT") -> TradeDecision:
    return TradeDecision(
        decision="long",
        confidence=0.72,
        symbol=symbol,
        timeframe="15m",
        entry_zone_min=70000.0,
        entry_zone_max=70100.0,
        stop_loss=69000.0,
        take_profit=71500.0,
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST"],
        explanation_short="live pnl snapshot test",
        explanation_detailed="deterministic pnl snapshot should feed the deterministic risk gate.",
    )


def test_live_snapshot_uses_execution_net_pnl_and_fees(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    now = utcnow_naive()
    _seed_live_close_order(
        db_session,
        label="net-pnl",
        fills=[
            (10.0, 1.0, now - timedelta(minutes=5)),
            (5.0, 0.5, now - timedelta(minutes=1)),
        ],
    )

    snapshot = create_exchange_pnl_snapshot(db_session, settings_row)

    assert snapshot.gross_realized_pnl == pytest.approx(15.0, rel=1e-9)
    assert snapshot.fee_total == pytest.approx(1.5, rel=1e-9)
    assert snapshot.funding_total == pytest.approx(0.0, rel=1e-9)
    assert snapshot.net_pnl == pytest.approx(13.5, rel=1e-9)
    assert snapshot.realized_pnl == pytest.approx(13.5, rel=1e-9)
    assert snapshot.daily_pnl == pytest.approx(13.5, rel=1e-9)
    assert snapshot.cumulative_pnl == pytest.approx(13.5, rel=1e-9)
    assert snapshot.cash_balance == pytest.approx(settings_row.starting_equity + 13.5, rel=1e-9)
    assert snapshot.wallet_balance == pytest.approx(settings_row.starting_equity + 13.5, rel=1e-9)
    assert snapshot.available_balance == pytest.approx(settings_row.starting_equity + 13.5, rel=1e-9)


def test_exchange_snapshot_prefers_live_balances_and_applies_funding_ledger(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    now = utcnow_naive()
    _seed_live_close_order(
        db_session,
        label="funding-ledger",
        fills=[(12.0, 0.5, now - timedelta(minutes=4))],
    )

    snapshot = create_exchange_pnl_snapshot(
        db_session,
        settings_row,
        {
            "totalWalletBalance": "1250.5",
            "availableBalance": "930.25",
            "totalUnrealizedProfit": "12.75",
            "totalMarginBalance": "1263.25",
        },
        funding_entries=[
            {
                "tranId": "funding-1",
                "symbol": "BTCUSDT",
                "asset": "USDT",
                "income": "-1.25",
                "time": int(now.timestamp() * 1000),
            }
        ],
    )

    assert snapshot.wallet_balance == pytest.approx(1250.5, rel=1e-9)
    assert snapshot.available_balance == pytest.approx(930.25, rel=1e-9)
    assert snapshot.equity == pytest.approx(1263.25, rel=1e-9)
    assert snapshot.gross_realized_pnl == pytest.approx(12.0, rel=1e-9)
    assert snapshot.fee_total == pytest.approx(0.5, rel=1e-9)
    assert snapshot.funding_total == pytest.approx(-1.25, rel=1e-9)
    assert snapshot.net_pnl == pytest.approx(10.25, rel=1e-9)
    assert snapshot.daily_pnl == pytest.approx(10.25, rel=1e-9)


def test_live_snapshot_separates_daily_and_cumulative_pnl(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    now = utcnow_naive()
    yesterday = now - timedelta(days=1)
    _seed_live_close_order(
        db_session,
        label="yesterday-win",
        fills=[(25.0, 1.0, yesterday)],
    )
    _seed_live_close_order(
        db_session,
        label="today-loss",
        fills=[(-5.0, 0.5, now)],
    )

    snapshot = create_exchange_pnl_snapshot(db_session, settings_row)

    assert snapshot.daily_pnl == pytest.approx(-5.5, rel=1e-9)
    assert snapshot.cumulative_pnl == pytest.approx(18.5, rel=1e-9)


def test_consecutive_losses_group_multiple_fills_per_close_order(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    now = utcnow_naive()
    _seed_live_close_order(
        db_session,
        label="winner",
        fills=[(6.0, 0.5, now - timedelta(hours=3))],
    )
    _seed_live_close_order(
        db_session,
        label="loss-a",
        fills=[
            (-2.0, 0.25, now - timedelta(hours=2)),
            (-1.0, 0.25, now - timedelta(hours=2, minutes=-2)),
        ],
    )
    _seed_live_close_order(
        db_session,
        label="loss-b",
        fills=[(-3.0, 0.25, now - timedelta(hours=1))],
    )

    snapshot = create_exchange_pnl_snapshot(db_session, settings_row)

    assert snapshot.consecutive_losses == 2


def test_get_latest_snapshot_repairs_stale_copied_totals_from_executions(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    stale_row = PnLSnapshot(
        snapshot_date=utcnow_naive().date(),
        equity=settings_row.starting_equity,
        cash_balance=settings_row.starting_equity,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        cumulative_pnl=0.0,
        consecutive_losses=0,
    )
    db_session.add(stale_row)
    db_session.flush()

    _seed_live_close_order(
        db_session,
        label="repair-loss",
        fills=[(-10.0, 0.5, utcnow_naive())],
    )

    snapshot = get_latest_pnl_snapshot(db_session, settings_row)

    assert snapshot.id != stale_row.id
    assert snapshot.cumulative_pnl == pytest.approx(-10.5, rel=1e-9)
    assert snapshot.consecutive_losses == 1


def test_get_latest_snapshot_rolls_daily_pnl_into_new_day(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    yesterday = utcnow_naive() - timedelta(days=1)
    stale_row = PnLSnapshot(
        snapshot_date=yesterday.date(),
        equity=99500.0,
        cash_balance=99500.0,
        realized_pnl=-500.0,
        unrealized_pnl=0.0,
        daily_pnl=-500.0,
        cumulative_pnl=-500.0,
        consecutive_losses=1,
        created_at=yesterday,
        updated_at=yesterday,
    )
    db_session.add(stale_row)
    db_session.flush()

    snapshot = get_latest_pnl_snapshot(db_session, settings_row)

    assert snapshot.id != stale_row.id
    assert snapshot.snapshot_date == utcnow_naive().date()
    assert snapshot.daily_pnl == pytest.approx(0.0, rel=1e-9)
    assert snapshot.cumulative_pnl == pytest.approx(0.0, rel=1e-9)


def test_risk_uses_deterministic_snapshot_for_daily_loss_limit(monkeypatch, db_session) -> None:
    class EnabledSettings:
        live_trading_env_enabled = True

    from trading_mvp.services import risk as risk_service

    original_get_settings = risk_service.get_settings
    risk_service.get_settings = lambda: EnabledSettings()  # type: ignore[assignment]
    settings_row = get_or_create_settings(db_session)
    _enable_live_entry(settings_row)
    _seed_live_close_order(
        db_session,
        label="daily-limit",
        fills=[(-6000.0, 0.0, utcnow_naive())],
    )
    create_exchange_pnl_snapshot(db_session, settings_row)

    try:
        result, _ = evaluate_risk(
            db_session,
            settings_row,
            _build_entry_decision(),
            build_market_snapshot("BTCUSDT", "15m", upto_index=140),
        )
    finally:
        risk_service.get_settings = original_get_settings  # type: ignore[assignment]

    assert result.allowed is False
    assert "DAILY_LOSS_LIMIT_REACHED" in result.reason_codes


def test_risk_uses_deterministic_snapshot_for_consecutive_losses(monkeypatch, db_session) -> None:
    class EnabledSettings:
        live_trading_env_enabled = True

    from trading_mvp.services import risk as risk_service

    original_get_settings = risk_service.get_settings
    risk_service.get_settings = lambda: EnabledSettings()  # type: ignore[assignment]
    settings_row = get_or_create_settings(db_session)
    _enable_live_entry(settings_row)
    now = utcnow_naive()
    _seed_live_close_order(db_session, label="loss-1", fills=[(-10.0, 0.1, now - timedelta(minutes=3))])
    _seed_live_close_order(db_session, label="loss-2", fills=[(-8.0, 0.1, now - timedelta(minutes=2))])
    _seed_live_close_order(db_session, label="loss-3", fills=[(-6.0, 0.1, now - timedelta(minutes=1))])
    create_exchange_pnl_snapshot(db_session, settings_row)

    try:
        result, _ = evaluate_risk(
            db_session,
            settings_row,
            _build_entry_decision(),
            build_market_snapshot("BTCUSDT", "15m", upto_index=140),
        )
    finally:
        risk_service.get_settings = original_get_settings  # type: ignore[assignment]

    assert result.allowed is False
    assert "MAX_CONSECUTIVE_LOSSES_REACHED" in result.reason_codes
