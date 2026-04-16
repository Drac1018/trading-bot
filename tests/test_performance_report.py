from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from trading_mvp.database import Base, get_db
from trading_mvp.main import app
from trading_mvp.models import AgentRun, Execution, Order, PnLSnapshot, Position, RiskCheck
from trading_mvp.services.backlog_insights import build_signal_performance_report
from trading_mvp.time_utils import utcnow_naive


def _feature_input(
    *,
    primary_regime: str,
    trend_alignment: str,
    volatility_regime: str,
    weak_volume: bool,
    momentum_weakening: bool,
) -> dict[str, object]:
    return {
        "features": {
            "regime": {
                "primary_regime": primary_regime,
                "trend_alignment": trend_alignment,
                "volatility_regime": volatility_regime,
                "weak_volume": weak_volume,
                "momentum_weakening": momentum_weakening,
            }
        }
    }


def _seed_performance_rows(db_session) -> None:
    now = utcnow_naive()
    recent_long = AgentRun(
        role="trading_decision",
        trigger_event="realtime_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="openai",
        summary="recent btc long",
        input_payload=_feature_input(
            primary_regime="bullish",
            trend_alignment="bullish_aligned",
            volatility_regime="normal",
            weak_volume=False,
            momentum_weakening=False,
        ),
        output_payload={
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "decision": "long",
            "rationale_codes": ["TREND_UP", "BREAKOUT"],
            "entry_zone_min": 69950.0,
            "entry_zone_max": 70050.0,
            "stop_loss": 69400.0,
            "take_profit": 70800.0,
            "max_holding_minutes": 60,
        },
        metadata_json={},
        schema_valid=True,
    )
    recent_hold = AgentRun(
        role="trading_decision",
        trigger_event="realtime_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="openai",
        summary="recent eth hold",
        input_payload=_feature_input(
            primary_regime="range",
            trend_alignment="range",
            volatility_regime="normal",
            weak_volume=True,
            momentum_weakening=True,
        ),
        output_payload={
            "symbol": "ETHUSDT",
            "timeframe": "1h",
            "decision": "hold",
            "rationale_codes": ["RANGE_CHOP"],
            "max_holding_minutes": 120,
        },
        metadata_json={},
        schema_valid=True,
    )
    older_short = AgentRun(
        role="trading_decision",
        trigger_event="realtime_cycle",
        schema_name="TradeDecision",
        status="completed",
        provider_name="openai",
        summary="older sol short",
        input_payload=_feature_input(
            primary_regime="bearish",
            trend_alignment="bearish_aligned",
            volatility_regime="expanded",
            weak_volume=False,
            momentum_weakening=False,
        ),
        output_payload={
            "symbol": "SOLUSDT",
            "timeframe": "5m",
            "decision": "short",
            "rationale_codes": ["BREAKDOWN"],
            "entry_zone_min": 180.0,
            "entry_zone_max": 182.0,
            "stop_loss": 186.0,
            "take_profit": 170.0,
            "max_holding_minutes": 90,
        },
        metadata_json={},
        schema_valid=True,
    )
    db_session.add_all([recent_long, recent_hold, older_short])
    db_session.flush()
    recent_long.created_at = now - timedelta(hours=2)
    recent_hold.created_at = now - timedelta(hours=1)
    older_short.created_at = now - timedelta(days=3)

    db_session.add_all(
        [
            RiskCheck(
                symbol="BTCUSDT",
                decision_run_id=recent_long.id,
                allowed=True,
                decision="long",
                reason_codes=[],
                approved_risk_pct=0.01,
                approved_leverage=2.0,
                payload={},
            ),
            RiskCheck(
                symbol="ETHUSDT",
                decision_run_id=recent_hold.id,
                allowed=False,
                decision="hold",
                reason_codes=["HOLD_DECISION"],
                approved_risk_pct=0.0,
                approved_leverage=0.0,
                payload={},
            ),
            RiskCheck(
                symbol="SOLUSDT",
                decision_run_id=older_short.id,
                allowed=True,
                decision="short",
                reason_codes=[],
                approved_risk_pct=0.01,
                approved_leverage=2.0,
                payload={},
            ),
        ]
    )
    db_session.flush()

    btc_position = Position(
        symbol="BTCUSDT",
        mode="live",
        side="long",
        status="closed",
        quantity=0.01,
        entry_price=70000.0,
        mark_price=70800.0,
        leverage=2.0,
        stop_loss=69400.0,
        take_profit=70800.0,
        realized_pnl=12.0,
        unrealized_pnl=0.0,
        metadata_json={"replay": {"mfe_pct": 0.018, "mae_pct": 0.006, "mfe_pnl": 12.6, "mae_pnl": 4.2}},
    )
    sol_position = Position(
        symbol="SOLUSDT",
        mode="live",
        side="short",
        status="closed",
        quantity=1.0,
        entry_price=181.0,
        mark_price=186.0,
        leverage=2.0,
        stop_loss=186.0,
        take_profit=170.0,
        realized_pnl=-5.0,
        unrealized_pnl=0.0,
        metadata_json={"replay": {"mfe_pct": 0.022, "mae_pct": 0.031, "mfe_pnl": 3.98, "mae_pnl": 5.61}},
    )
    db_session.add_all([btc_position, sol_position])
    db_session.flush()
    btc_position.opened_at = now - timedelta(minutes=90)
    btc_position.closed_at = now - timedelta(minutes=15)
    sol_position.opened_at = now - timedelta(days=3, minutes=130)
    sol_position.closed_at = now - timedelta(days=3, minutes=20)

    btc_entry = Order(
        symbol="BTCUSDT",
        decision_run_id=recent_long.id,
        position_id=btc_position.id,
        side="buy",
        order_type="market",
        mode="live",
        status="filled",
        external_order_id="btc-entry",
        requested_quantity=0.01,
        requested_price=70000.0,
        filled_quantity=0.01,
        average_fill_price=70005.0,
        reason_codes=[],
        metadata_json={},
    )
    btc_take_profit = Order(
        symbol="BTCUSDT",
        decision_run_id=recent_long.id,
        position_id=btc_position.id,
        side="sell",
        order_type="TAKE_PROFIT_MARKET",
        mode="live",
        status="filled",
        external_order_id="btc-tp",
        reduce_only=True,
        close_only=True,
        requested_quantity=0.01,
        requested_price=70800.0,
        filled_quantity=0.01,
        average_fill_price=70800.0,
        reason_codes=[],
        metadata_json={},
    )
    sol_entry = Order(
        symbol="SOLUSDT",
        decision_run_id=older_short.id,
        position_id=sol_position.id,
        side="sell",
        order_type="market",
        mode="live",
        status="filled",
        external_order_id="sol-entry",
        requested_quantity=1.0,
        requested_price=181.0,
        filled_quantity=1.0,
        average_fill_price=181.0,
        reason_codes=[],
        metadata_json={},
    )
    sol_stop = Order(
        symbol="SOLUSDT",
        decision_run_id=older_short.id,
        position_id=sol_position.id,
        side="buy",
        order_type="STOP_MARKET",
        mode="live",
        status="filled",
        external_order_id="sol-stop",
        reduce_only=True,
        close_only=True,
        requested_quantity=1.0,
        requested_price=186.0,
        filled_quantity=1.0,
        average_fill_price=186.0,
        reason_codes=[],
        metadata_json={},
    )
    db_session.add_all([btc_entry, btc_take_profit, sol_entry, sol_stop])
    db_session.flush()
    btc_entry.created_at = now - timedelta(minutes=89)
    btc_take_profit.created_at = now - timedelta(minutes=16)
    sol_entry.created_at = now - timedelta(days=3, minutes=129)
    sol_stop.created_at = now - timedelta(days=3, minutes=21)

    db_session.add_all(
        [
            Execution(
                order_id=btc_entry.id,
                position_id=btc_position.id,
                symbol="BTCUSDT",
                status="filled",
                external_trade_id="btc-entry-fill",
                fill_price=70005.0,
                fill_quantity=0.01,
                fee_paid=0.0,
                commission_asset="USDT",
                slippage_pct=0.001,
                realized_pnl=0.0,
                payload={},
            ),
            Execution(
                order_id=btc_take_profit.id,
                position_id=btc_position.id,
                symbol="BTCUSDT",
                status="filled",
                external_trade_id="btc-tp-fill",
                fill_price=70800.0,
                fill_quantity=0.01,
                fee_paid=1.0,
                commission_asset="USDT",
                slippage_pct=0.0015,
                realized_pnl=12.0,
                payload={},
            ),
            Execution(
                order_id=sol_entry.id,
                position_id=sol_position.id,
                symbol="SOLUSDT",
                status="filled",
                external_trade_id="sol-entry-fill",
                fill_price=181.0,
                fill_quantity=1.0,
                fee_paid=0.0,
                commission_asset="USDT",
                slippage_pct=0.001,
                realized_pnl=0.0,
                payload={},
            ),
            Execution(
                order_id=sol_stop.id,
                position_id=sol_position.id,
                symbol="SOLUSDT",
                status="filled",
                external_trade_id="sol-stop-fill",
                fill_price=186.0,
                fill_quantity=1.0,
                fee_paid=0.5,
                commission_asset="USDT",
                slippage_pct=0.003,
                realized_pnl=-5.0,
                payload={},
            ),
        ]
    )
    db_session.flush()
    btc_entry_fill = db_session.query(Execution).filter_by(external_trade_id="btc-entry-fill").one()
    btc_tp_fill = db_session.query(Execution).filter_by(external_trade_id="btc-tp-fill").one()
    sol_entry_fill = db_session.query(Execution).filter_by(external_trade_id="sol-entry-fill").one()
    sol_stop_fill = db_session.query(Execution).filter_by(external_trade_id="sol-stop-fill").one()
    btc_entry_fill.created_at = now - timedelta(minutes=88, seconds=30)
    btc_tp_fill.created_at = now - timedelta(minutes=15)
    sol_entry_fill.created_at = now - timedelta(days=3, minutes=126)
    sol_stop_fill.created_at = now - timedelta(days=3, minutes=20)
    db_session.flush()

    db_session.add_all(
        [
            PnLSnapshot(
                snapshot_date=(now - timedelta(days=2)).date(),
                equity=100100.0,
                cash_balance=100100.0,
                realized_pnl=100.0,
                unrealized_pnl=0.0,
                daily_pnl=10.0,
                cumulative_pnl=100.0,
                consecutive_losses=0,
                created_at=now - timedelta(days=2),
            ),
            PnLSnapshot(
                snapshot_date=now.date(),
                equity=100120.0,
                cash_balance=100120.0,
                realized_pnl=120.0,
                unrealized_pnl=0.0,
                daily_pnl=20.0,
                cumulative_pnl=120.0,
                consecutive_losses=0,
                created_at=now,
            ),
        ]
    )
    db_session.flush()


def test_build_signal_performance_report_returns_regime_and_flag_breakdowns(db_session) -> None:
    _seed_performance_rows(db_session)

    report = build_signal_performance_report(db_session)

    assert report.window_hours == 24
    assert len(report.windows) == 3

    by_window = {item.window_label: item for item in report.windows}
    day = by_window["24h"]
    week = by_window["7d"]

    assert day.summary.decisions == 2
    assert day.summary.approvals == 1
    assert day.summary.orders == 2
    assert day.summary.fills == 2
    assert day.summary.holds == 1
    assert day.summary.longs == 1
    assert day.summary.shorts == 0
    assert day.summary.realized_pnl_total == 12.0
    assert day.summary.fee_total == 1.0
    assert day.summary.net_realized_pnl_total == 11.0
    assert day.summary.average_arrival_slippage_pct == pytest.approx(0.001, abs=1e-9)
    assert day.summary.average_realized_slippage_pct == pytest.approx(0.001, abs=1e-9)
    assert day.summary.average_first_fill_latency_seconds == pytest.approx(30.0, abs=1e-9)
    assert day.summary.cancel_attempts == 0
    assert day.summary.cancel_successes == 0
    assert day.summary.cancel_success_rate == 0.0
    assert day.summary.wins == 1
    assert day.summary.losses == 0
    assert day.summary.take_profit_closes == 1
    assert day.summary.stop_loss_closes == 0
    assert day.summary.snapshot_net_pnl_estimate == 20.0
    assert day.summary.average_mfe_pct == 0.018
    assert day.summary.average_mae_pct == 0.006
    assert day.summary.best_mfe_pct == 0.018
    assert day.summary.worst_mae_pct == 0.006

    assert day.decisions[0].symbol == "ETHUSDT"
    assert day.decisions[0].decision == "hold"
    assert day.decisions[0].regime == "range"
    assert day.decisions[0].weak_volume is True
    assert day.decisions[1].symbol == "BTCUSDT"
    assert day.decisions[1].close_outcome == "take_profit"
    assert day.decisions[1].planned_risk_reward_ratio == 1.3333333333333333
    assert day.decisions[1].arrival_slippage_pct == pytest.approx(0.001, abs=1e-9)
    assert day.decisions[1].realized_slippage_pct == pytest.approx(0.001, abs=1e-9)
    assert day.decisions[1].first_fill_latency_seconds == pytest.approx(30.0, abs=1e-9)
    assert day.decisions[1].mfe_pct == 0.018
    assert day.decisions[1].mae_pct == 0.006

    assert {item.key for item in day.regimes} == {"bullish", "range"}
    assert {item.key for item in day.trend_alignments} == {"bullish_aligned", "range"}
    assert {item.key for item in day.directions} == {"hold", "long"}
    assert day.hold_conditions[0].holds == 1
    assert "range | trend=range" in day.hold_conditions[0].key
    assert day.feature_flags[0].flag_name in {"weak_volume", "volatility_expanded", "momentum_weakening"}
    weak_volume = next(item for item in day.feature_flags if item.flag_name == "weak_volume")
    assert weak_volume.enabled.decisions == 1
    assert weak_volume.disabled.decisions == 1
    assert weak_volume.disabled.net_realized_pnl_total == 11.0
    assert {item.key for item in day.close_outcomes} == {"not_closed", "take_profit"}

    assert week.summary.decisions == 3
    assert week.summary.shorts == 1
    assert week.summary.stop_loss_closes == 1
    assert week.summary.average_arrival_slippage_pct == pytest.approx(0.001, abs=1e-9)
    assert week.summary.average_realized_slippage_pct == pytest.approx(0.001, abs=1e-9)
    assert week.summary.average_first_fill_latency_seconds == pytest.approx(105.0, abs=1e-9)
    assert week.summary.average_mfe_pct == pytest.approx(0.02, abs=1e-9)
    assert week.summary.average_mae_pct == pytest.approx(0.0185, abs=1e-9)
    assert week.summary.best_mfe_pct == pytest.approx(0.022, abs=1e-9)
    assert week.summary.worst_mae_pct == pytest.approx(0.031, abs=1e-9)
    assert {item.key for item in week.regimes} == {"bullish", "bearish", "range"}
    assert {item.key for item in week.symbols} == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
    assert {item.key for item in week.timeframes} == {"15m", "1h", "5m"}
    assert {item.key for item in week.directions} == {"long", "hold", "short"}

    assert report.items
    assert report.items[0].fee_total >= 0.0
    assert report.items[0].net_realized_pnl_total >= report.items[0].realized_pnl_total - report.items[0].fee_total


def test_performance_endpoint_returns_extended_report_payload(tmp_path, monkeypatch) -> None:
    test_engine = create_engine(f"sqlite:///{tmp_path / 'performance_api.db'}", future=True)
    TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=test_engine)
    monkeypatch.setattr("trading_mvp.main.engine", test_engine)

    def override_get_db():
        with TestingSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestingSessionLocal() as session:
            _seed_performance_rows(session)
            session.commit()

        with TestClient(app) as client:
            response = client.get("/api/performance")

        assert response.status_code == 200
        payload = response.json()
        assert payload["window_hours"] == 24
        assert len(payload["windows"]) == 3
        assert payload["windows"][0]["summary"]["execution_pnl_basis"] == "execution_ledger_truth"
        assert payload["windows"][0]["summary"]["decision_context_basis"] == "agent_run_input_features_regime"
        assert "average_arrival_slippage_pct" in payload["windows"][0]["summary"]
        assert "average_first_fill_latency_seconds" in payload["windows"][0]["summary"]
        assert "regimes" in payload["windows"][0]
        assert "directions" in payload["windows"][0]
        assert "feature_flags" in payload["windows"][0]
    finally:
        app.dependency_overrides.clear()
