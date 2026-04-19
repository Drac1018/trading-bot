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
    data_quality_grade: str = "complete",
    missing_context_flags: list[str] | None = None,
    stale_context_flags: list[str] | None = None,
    derivatives_available: bool | None = None,
    orderbook_available: bool | None = None,
    spread_quality_available: bool | None = None,
) -> AIDecisionContextPacket:
    resolved_derivatives_available = derivatives_available if derivatives_available is not None else data_quality_grade != "unavailable"
    resolved_orderbook_available = orderbook_available if orderbook_available is not None else data_quality_grade in {"complete", "partial"}
    resolved_spread_quality_available = (
        spread_quality_available if spread_quality_available is not None else data_quality_grade != "unavailable"
    )
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
            data_quality_grade=data_quality_grade,  # type: ignore[arg-type]
            missing_context_flags=list(missing_context_flags or []),
            stale_context_flags=list(stale_context_flags or []),
            derivatives_available=resolved_derivatives_available,
            orderbook_available=resolved_orderbook_available,
            spread_quality_available=resolved_spread_quality_available,
            account_state_trustworthy=data_quality_grade != "unavailable",
            market_state_trustworthy=data_quality_grade != "unavailable",
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


def test_unavailable_quality_entry_fail_closed_without_provider_call() -> None:
    class ShouldNotRunProvider:
        name = "openai"

        def __init__(self) -> None:
            self.calls = 0

        def generate(self, role, payload, *, response_model, instructions):  # noqa: ANN001
            self.calls += 1
            raise AssertionError("provider should not be called when quality is unavailable")

    snapshot, features = _features()
    provider = ShouldNotRunProvider()
    agent = TradingDecisionAgent(provider)

    decision, provider_name, metadata = agent.run(
        snapshot,
        features,
        [],
        _risk_context(),
        use_ai=True,
        max_input_candles=16,
        ai_context=_ai_context(
            trigger_type="entry_candidate_event",
            strategy_engine="trend_pullback_engine",
            data_quality_grade="unavailable",
            missing_context_flags=["orderbook_context_unavailable"],
            stale_context_flags=["market_snapshot_stale"],
            derivatives_available=False,
            orderbook_available=False,
            spread_quality_available=False,
        ),
    )

    assert provider.calls == 0
    assert provider_name == "deterministic-mock"
    assert decision.decision == "hold"
    assert decision.fail_closed_applied is True
    assert decision.data_quality_fail_closed_applied is True
    assert decision.provider_not_called_due_to_quality is True
    assert decision.provider_status == "quality_blocked"
    assert "DATA_QUALITY_UNAVAILABLE_FAIL_CLOSED" in decision.fallback_reason_codes
    assert "DATA_QUALITY_UNAVAILABLE_FAIL_CLOSED" in decision.data_quality_block_reason_codes
    assert metadata["provider_not_called_due_to_quality"] is True
    assert metadata["data_quality_fail_closed_applied"] is True
    assert metadata["provider_status"] == "quality_blocked"


def test_degraded_breakout_quality_fail_closed_without_provider_call() -> None:
    class ShouldNotRunProvider:
        name = "openai"

        def __init__(self) -> None:
            self.calls = 0

        def generate(self, role, payload, *, response_model, instructions):  # noqa: ANN001
            self.calls += 1
            raise AssertionError("provider should not be called for degraded breakout entry review")

    snapshot, features = _features()
    provider = ShouldNotRunProvider()
    agent = TradingDecisionAgent(provider)

    decision, provider_name, metadata = agent.run(
        snapshot,
        features,
        [],
        _risk_context(),
        use_ai=True,
        max_input_candles=16,
        ai_context=_ai_context(
            trigger_type="breakout_exception_event",
            strategy_engine="breakout_exception_engine",
            data_quality_grade="degraded",
            stale_context_flags=["market_snapshot_stale"],
        ),
    )

    assert provider.calls == 0
    assert provider_name == "deterministic-mock"
    assert decision.decision == "hold"
    assert decision.fail_closed_applied is True
    assert decision.should_abstain is True
    assert decision.provider_status == "quality_blocked"
    assert "BREAKOUT_QUALITY_INSUFFICIENT" in decision.data_quality_block_reason_codes
    assert metadata["data_quality_fail_closed_applied"] is True
    assert metadata["provider_not_called_due_to_quality"] is True
    assert metadata["minimum_quality_required"] == "partial_or_better_with_breakout_context"


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
        ai_context=_ai_context(
            trigger_type="protection_review_event",
            strategy_engine="protection_reduce_engine",
            data_quality_grade="unavailable",
            missing_context_flags=["orderbook_context_unavailable"],
            stale_context_flags=["market_snapshot_stale"],
            derivatives_available=False,
            orderbook_available=False,
            spread_quality_available=False,
        ),
    )

    assert provider_name == "deterministic-mock"
    assert decision.decision == "long"
    assert decision.fail_closed_applied is False
    assert decision.provider_status == "timeout"
    assert decision.provider_not_called_due_to_quality is False
    assert metadata["fail_closed_applied"] is False
    assert metadata["provider_status"] == "timeout"
