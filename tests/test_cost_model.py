from __future__ import annotations

import pytest
from trading_mvp.models import Execution, Order
from trading_mvp.services.cost_model import (
    CostModelConfig,
    RecentExecutionCostEstimate,
    build_recent_execution_cost_estimate,
    calculate_expected_trade_cost,
)


def test_cost_model_calculates_expected_net_and_fee_ratio() -> None:
    estimate = calculate_expected_trade_cost(
        entry_execution_type="entry_marketable",
        expected_gross_bps=35.0,
        spread_cost_bps=2.0,
        explicit_round_trip_fee_bps=10.0,
        explicit_slippage_bps=3.0,
        config=CostModelConfig(maker_fee_bps=5.0, taker_fee_bps=5.0),
    )

    assert estimate.round_trip_fee_bps == pytest.approx(10.0)
    assert estimate.expected_net_bps == pytest.approx(20.0)
    assert estimate.fee_to_gross_ratio == pytest.approx(10.0 / 35.0)


def test_cost_model_uses_conservative_default_when_recent_data_missing() -> None:
    estimate = calculate_expected_trade_cost(
        entry_execution_type="entry_passive_limit",
        expected_gross_bps=35.0,
        spread_cost_bps=0.0,
        config=CostModelConfig(maker_fee_bps=5.0, taker_fee_bps=5.0),
    )

    assert estimate.entry_fee_bps == pytest.approx(5.0)
    assert estimate.exit_fee_bps == pytest.approx(5.0)
    assert estimate.round_trip_fee_bps == pytest.approx(10.0)
    assert estimate.fee_source == "config_defaults"


def test_cost_model_recent_execution_estimate_overrides_default_conservatively() -> None:
    estimate = calculate_expected_trade_cost(
        entry_execution_type="entry_passive_limit",
        expected_gross_bps=50.0,
        spread_cost_bps=0.0,
        recent_estimate=RecentExecutionCostEstimate(
            fee_bps_per_fill=6.2,
            fee_sample_count=4,
            adverse_slippage_bps=4.5,
            slippage_sample_count=4,
            source="recent_executions",
        ),
        config=CostModelConfig(maker_fee_bps=5.0, taker_fee_bps=5.0, passive_slippage_bps=1.0),
    )

    assert estimate.entry_fee_bps == pytest.approx(6.2)
    assert estimate.exit_fee_bps == pytest.approx(6.2)
    assert estimate.round_trip_fee_bps == pytest.approx(12.4)
    assert estimate.expected_slippage_bps == pytest.approx(4.5)
    assert estimate.fee_source == "recent_executions_conservative_max"
    assert estimate.slippage_source == "recent_executions_conservative_max"


def test_recent_execution_cost_estimate_reads_fee_and_slippage_samples(db_session) -> None:
    order = Order(
        symbol="BTCUSDT",
        side="buy",
        order_type="market",
        mode="live",
        status="filled",
        requested_quantity=0.01,
        requested_price=100.0,
        filled_quantity=0.01,
        average_fill_price=100.0,
        reason_codes=[],
        metadata_json={"entry_execution_type": "entry_marketable"},
    )
    db_session.add(order)
    db_session.flush()
    db_session.add(
        Execution(
            order_id=order.id,
            symbol="BTCUSDT",
            fill_price=100.0,
            fill_quantity=0.01,
            fee_paid=0.0006,
            slippage_pct=0.0,
            realized_pnl=0.0,
            payload={"signed_slippage_bps": 7.5},
        )
    )
    db_session.flush()

    recent = build_recent_execution_cost_estimate(
        db_session,
        symbol="BTCUSDT",
        side="long",
        sample_limit=5,
    )

    assert recent.fee_bps_per_fill == pytest.approx(6.0)
    assert recent.fee_sample_count == 1
    assert recent.adverse_slippage_bps == pytest.approx(7.5)
    assert recent.slippage_sample_count == 1
