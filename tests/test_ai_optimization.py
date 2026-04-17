from __future__ import annotations

from datetime import timedelta
from typing import Any

from trading_mvp.providers import ProviderResult
from trading_mvp.schemas import MarketCandle, MarketSnapshotPayload
from trading_mvp.services.agents import TradingDecisionAgent
from trading_mvp.services.features import compute_features
from trading_mvp.time_utils import utcnow_naive


class CapturingProvider:
    name = "openai"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def generate(
        self,
        role: str,
        payload: dict[str, Any],
        *,
        response_model,
        instructions: str,
    ) -> ProviderResult:
        self.calls.append({"role": role, "payload": payload, "instructions": instructions})
        return ProviderResult(
            provider="openai",
            output={
                "decision": "hold",
                "confidence": 0.42,
                "symbol": payload["market_snapshot"]["symbol"],
                "timeframe": payload["market_snapshot"]["timeframe"],
                "entry_zone_min": payload["market_snapshot"]["latest_price"],
                "entry_zone_max": payload["market_snapshot"]["latest_price"],
                "stop_loss": None,
                "take_profit": None,
                "max_holding_minutes": 240,
                "risk_pct": 0.01,
                "leverage": 1.0,
                "rationale_codes": ["TEST"],
                "explanation_short": "조건이 약해 보류합니다.",
            },
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )

    def test_connection(self) -> dict[str, Any]:
        return {"ok": True}


def _snapshot(symbol: str, timeframe: str, closes: list[float], volumes: list[float]) -> MarketSnapshotPayload:
    now = utcnow_naive()
    step_minutes = 15 if timeframe == "15m" else 60 if timeframe == "1h" else 240
    candles: list[MarketCandle] = []
    for index, close in enumerate(closes):
        previous = closes[index - 1] if index > 0 else close
        candles.append(
            MarketCandle(
                timestamp=now - timedelta(minutes=step_minutes * (len(closes) - index)),
                open=previous,
                high=max(previous, close) * 1.002,
                low=min(previous, close) * 0.998,
                close=close,
                volume=volumes[index],
            )
        )
    return MarketSnapshotPayload(
        symbol=symbol,
        timeframe=timeframe,
        snapshot_time=now,
        latest_price=closes[-1],
        latest_volume=volumes[-1],
        candle_count=len(candles),
        is_stale=False,
        is_complete=True,
        candles=candles,
    )


def test_trading_decision_payload_is_compacted_for_llm_calls() -> None:
    provider = CapturingProvider()
    agent = TradingDecisionAgent(provider)

    snapshot = _snapshot(
        "BTCUSDT",
        "15m",
        [69900.0, 69980.0, 70010.0, 70060.0, 70040.0, 70110.0, 70130.0, 70180.0, 70220.0, 70280.0, 70310.0, 70390.0, 70420.0, 70480.0, 70550.0, 70620.0],
        [1000.0, 1010.0, 995.0, 1020.0, 1005.0, 1040.0, 1030.0, 1050.0, 1060.0, 1070.0, 1090.0, 1110.0, 1125.0, 1140.0, 1160.0, 1180.0],
    )
    features = compute_features(
        snapshot,
        {
            "1h": _snapshot(
                "BTCUSDT",
                "1h",
                [69500.0, 69650.0, 69780.0, 69920.0, 70040.0, 70120.0, 70280.0, 70410.0, 70560.0, 70700.0, 70840.0, 70950.0, 71120.0, 71260.0, 71410.0, 71550.0],
                [1500.0 for _ in range(16)],
            ),
            "4h": _snapshot(
                "BTCUSDT",
                "4h",
                [68000.0, 68400.0, 68850.0, 69200.0, 69600.0, 70050.0, 70520.0, 70940.0, 71320.0, 71710.0, 72150.0, 72620.0, 73100.0, 73610.0, 74140.0, 74700.0],
                [1800.0 for _ in range(16)],
            ),
        },
    )

    decision, provider_name, metadata = agent.run(
        snapshot,
        features,
        open_positions=[],
        risk_context={
            "max_risk_per_trade": 0.01,
            "max_leverage": 3.0,
            "daily_pnl": 0.0,
            "consecutive_losses": 0,
            "operating_state": "TRADABLE",
            "risk_budget": {
                "max_additional_long_notional": 1250.0,
                "max_additional_short_notional": 980.0,
                "max_new_position_notional_for_symbol": 640.0,
                "max_leverage_for_symbol": 3.0,
                "directional_bias_headroom": 980.0,
                "single_position_headroom": 640.0,
                "total_exposure_headroom": 1500.0,
            },
        },
        use_ai=True,
        max_input_candles=40,
    )

    payload = provider.calls[0]["payload"]
    assert provider_name == "openai"
    assert metadata["source"] == "llm"
    assert metadata["input_token_estimate"] > 0
    assert decision.decision == "hold"
    assert decision.explanation_detailed
    assert len(payload["market_snapshot"]["candles"]) == 16
    assert set(payload["market_snapshot"]["candles"][0].keys()) == {"t", "o", "h", "l", "c", "v"}
    assert "explanation_detailed" not in payload["deterministic_baseline"]
    assert set(payload["features"].keys()) == {
        "trend_score",
        "volatility_pct",
        "volume_ratio",
        "drawdown_pct",
        "rsi",
        "atr",
        "atr_pct",
        "momentum_score",
        "regime",
        "breakout",
        "candle_structure",
        "location",
        "volume_persistence",
        "pullback_context",
        "multi_timeframe",
        "data_quality_flags",
    }
    assert "range_breakout_direction" in payload["features"]["breakout"]
    assert "vwap_distance_pct" in payload["features"]["location"]
    assert "persistence_ratio" in payload["features"]["volume_persistence"]
    assert "state" in payload["features"]["pullback_context"]
    assert payload["risk_context"]["risk_budget"] == {
        "max_additional_long_notional": 1250.0,
        "max_additional_short_notional": 980.0,
        "max_new_position_notional_for_symbol": 640.0,
        "max_leverage_for_symbol": 3.0,
        "directional_bias_headroom": 980.0,
        "single_position_headroom": 640.0,
        "total_exposure_headroom": 1500.0,
    }
    instructions = provider.calls[0]["instructions"]
    assert "Never propose size or leverage beyond the provided risk budget." in instructions
    assert "If the remaining budget is small or zero, prefer hold." in instructions


def test_trading_decision_short_circuit_skips_llm_when_hold_and_budget_exhausted() -> None:
    provider = CapturingProvider()
    agent = TradingDecisionAgent(provider)
    snapshot = _snapshot("BTCUSDT", "15m", [70000.0 for _ in range(16)], [800.0 for _ in range(16)])
    features = compute_features(snapshot, {})

    decision, provider_name, metadata = agent.run(
        snapshot,
        features,
        open_positions=[],
        risk_context={
            "max_risk_per_trade": 0.01,
            "max_leverage": 3.0,
            "daily_pnl": 0.0,
            "consecutive_losses": 0,
            "operating_state": "TRADABLE",
            "risk_budget": {
                "max_additional_long_notional": 0.0,
                "max_additional_short_notional": 0.0,
                "max_new_position_notional_for_symbol": 0.0,
                "max_leverage_for_symbol": 0.0,
            },
        },
        use_ai=True,
        max_input_candles=16,
    )

    assert provider.calls == []
    assert provider_name == "deterministic-mock"
    assert metadata["source"] == "deterministic_short_circuit"
    assert decision.decision == "hold"
    assert "DETERMINISTIC_HOLD_SHORT_CIRCUIT" in decision.rationale_codes
