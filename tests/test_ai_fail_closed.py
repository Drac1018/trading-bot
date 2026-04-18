from __future__ import annotations

from datetime import timedelta

import httpx
from trading_mvp.models import Position
from trading_mvp.providers import ProviderResult
from trading_mvp.schemas import (
    AIDecisionContextPacket,
    CompositeRegimePacket,
    DataQualityPacket,
    DerivativesContextPayload,
    MarketCandle,
    MarketSnapshotPayload,
    PreviousThesisDeltaPacket,
)
from trading_mvp.services.agents import TradingDecisionAgent
from trading_mvp.services.features import compute_features
from trading_mvp.time_utils import utcnow_naive


def _snapshot(
    timeframe: str,
    closes: list[float],
) -> MarketSnapshotPayload:
    now = utcnow_naive()
    interval_minutes = 15 if timeframe == "15m" else 60 if timeframe == "1h" else 240
    candles: list[MarketCandle] = []
    for index, close in enumerate(closes):
        previous = closes[index - 1] if index > 0 else close
        timestamp = now - timedelta(minutes=interval_minutes * (len(closes) - index))
        candles.append(
            MarketCandle(
                timestamp=timestamp,
                open=previous,
                high=max(previous, close) * 1.002,
                low=min(previous, close) * 0.998,
                close=close,
                volume=1000.0,
            )
        )
    return MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe=timeframe,
        snapshot_time=now,
        latest_price=closes[-1],
        latest_volume=1000.0,
        candle_count=len(candles),
        is_stale=False,
        is_complete=True,
        candles=candles,
        derivatives_context=DerivativesContextPayload(),
    )


def _risk_context(state: str = "TRADABLE") -> dict[str, object]:
    return {
        "max_risk_per_trade": 0.02,
        "max_leverage": 5.0,
        "symbol_risk_tier": "btc",
        "daily_pnl": 0.0,
        "consecutive_losses": 0,
        "operating_state": state,
        "protection_recovery_status": "idle",
        "missing_protection_symbols": [],
        "missing_protection_items": {},
        "position_management_context": {},
    }


def _ai_context(
    *,
    trigger_type: str,
    strategy_engine: str,
    holding_profile: str = "scalp",
) -> AIDecisionContextPacket:
    return AIDecisionContextPacket(
        symbol="BTCUSDT",
        timeframe="15m",
        trigger_type=trigger_type,  # type: ignore[arg-type]
        composite_regime=CompositeRegimePacket(
            structure_regime="trend",
            direction_regime="bullish",
            volatility_regime="normal",
            participation_regime="strong",
            derivatives_regime="tailwind",
            execution_regime="clean",
            persistence_bars=5,
            persistence_class="established",
            transition_risk="medium",
            regime_reason_codes=["TEST_REGIME"],
        ),
        data_quality=DataQualityPacket(
            data_quality_grade="complete",
            missing_context_flags=[],
            stale_context_flags=[],
            derivatives_available=True,
            orderbook_available=True,
            spread_quality_available=True,
            account_state_trustworthy=True,
            market_state_trustworthy=True,
        ),
        previous_thesis=PreviousThesisDeltaPacket(),
        strategy_engine=strategy_engine,
        strategy_engine_context={"engine_name": strategy_engine},
        holding_profile=holding_profile,  # type: ignore[arg-type]
        hard_stop_active=True,
        stop_widening_allowed=False,
        initial_stop_type="deterministic_hard_stop",
    )


def _features():
    base = _snapshot(
        "15m",
        [100, 100.4, 100.8, 101.2, 101.6, 102.0, 102.4, 102.7, 103.0, 103.4, 103.8, 104.2, 104.6, 105.0, 105.3, 105.8],
    )
    features = compute_features(
        base,
        {
            "1h": _snapshot("1h", [98, 98.8, 99.7, 100.6, 101.5, 102.5, 103.5, 104.6, 105.7, 106.9, 108.1, 109.4, 110.7, 112.1, 113.6, 115.2]),
            "4h": _snapshot("4h", [92, 93.5, 95.1, 96.8, 98.6, 100.5, 102.5, 104.6, 106.8, 109.1, 111.5, 114.0, 116.6, 119.3, 122.1, 125.0]),
        },
    )
    return base, features


def test_entry_path_provider_timeout_fail_closed() -> None:
    class TimeoutProvider:
        name = "openai"

        def generate(self, role, payload, *, response_model, instructions):  # noqa: ANN001
            raise httpx.TimeoutException("provider timed out")

    snapshot, features = _features()
    agent = TradingDecisionAgent(TimeoutProvider())

    decision, provider_name, metadata = agent.run(
        snapshot,
        features,
        [],
        _risk_context(),
        use_ai=True,
        max_input_candles=16,
        ai_context=_ai_context(trigger_type="entry_candidate_event", strategy_engine="trend_pullback_engine"),
    )

    assert provider_name == "deterministic-mock"
    assert decision.decision == "hold"
    assert decision.fail_closed_applied is True
    assert decision.provider_status == "timeout"
    assert "AI_UNAVAILABLE_FAIL_CLOSED" in decision.fallback_reason_codes
    assert metadata["fail_closed_applied"] is True
    assert metadata["provider_status"] == "timeout"


def test_malformed_output_fail_closed() -> None:
    class MalformedProvider:
        name = "openai"

        def generate(self, role, payload, *, response_model, instructions):  # noqa: ANN001
            return ProviderResult(provider="openai", output={"decision": "long"})

    snapshot, features = _features()
    agent = TradingDecisionAgent(MalformedProvider())

    decision, provider_name, metadata = agent.run(
        snapshot,
        features,
        [],
        _risk_context(),
        use_ai=True,
        max_input_candles=16,
        ai_context=_ai_context(trigger_type="entry_candidate_event", strategy_engine="trend_pullback_engine"),
    )

    assert provider_name == "deterministic-mock"
    assert decision.decision == "hold"
    assert decision.fail_closed_applied is True
    assert decision.provider_status == "schema_invalid"
    assert "AI_SCHEMA_INVALID" in decision.fallback_reason_codes
    assert metadata["provider_status"] == "schema_invalid"


def test_protection_review_provider_failure_keeps_deterministic_management() -> None:
    class TimeoutProvider:
        name = "openai"

        def generate(self, role, payload, *, response_model, instructions):  # noqa: ANN001
            raise httpx.TimeoutException("provider timed out")

    snapshot, features = _features()
    agent = TradingDecisionAgent(TimeoutProvider())
    open_position = Position(
        symbol="BTCUSDT",
        mode="paper",
        side="long",
        status="open",
        quantity=0.01,
        entry_price=100.0,
        mark_price=105.0,
        leverage=2.0,
        stop_loss=98.0,
        take_profit=110.0,
        unrealized_pnl=5.0,
        metadata_json={},
    )

    decision, provider_name, metadata = agent.run(
        snapshot,
        features,
        [open_position],
        _risk_context(state="PROTECTION_REQUIRED"),
        use_ai=True,
        max_input_candles=16,
        ai_context=_ai_context(trigger_type="protection_review_event", strategy_engine="protection_reduce_engine"),
    )

    assert provider_name == "deterministic-mock"
    assert decision.decision == "long"
    assert decision.fail_closed_applied is False
    assert decision.provider_status == "timeout"
    assert metadata["fail_closed_applied"] is False
    assert metadata["provider_status"] == "timeout"
