from __future__ import annotations

from typing import Any

from trading_mvp.providers import ProviderResult
from trading_mvp.schemas import FeaturePayload, MarketCandle, MarketSnapshotPayload
from trading_mvp.services.agents import TradingDecisionAgent
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
                "explanation_detailed": "조건이 약해 보류합니다. 다음 캔들까지 대기합니다.",
            },
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )

    def test_connection(self) -> dict[str, Any]:
        return {"ok": True}


def test_trading_decision_payload_is_compacted_for_llm_calls() -> None:
    provider = CapturingProvider()
    agent = TradingDecisionAgent(provider)
    now = utcnow_naive()
    snapshot = MarketSnapshotPayload(
        symbol="BTCUSDT",
        timeframe="15m",
        snapshot_time=now,
        latest_price=70000.0,
        latest_volume=1200.0,
        candle_count=40,
        is_stale=False,
        is_complete=True,
        candles=[
            MarketCandle(
                timestamp=now,
                open=69900.0 + index,
                high=70100.0 + index,
                low=69800.0 + index,
                close=70000.0 + index,
                volume=1000.0 + index,
            )
            for index in range(40)
        ],
    )
    features = FeaturePayload(
        symbol="BTCUSDT",
        timeframe="15m",
        trend_score=0.6,
        volatility_pct=0.02,
        volume_ratio=1.1,
        drawdown_pct=0.01,
        rsi=58.0,
        atr=120.0,
        data_quality_flags=[],
    )

    decision, provider_name, metadata = agent.run(
        snapshot,
        features,
        open_positions=[],
        risk_context={"max_risk_per_trade": 0.01, "max_leverage": 3.0, "daily_pnl": 0.0, "consecutive_losses": 0},
        use_ai=True,
        max_input_candles=40,
    )

    payload = provider.calls[0]["payload"]
    assert provider_name == "openai"
    assert metadata["source"] == "llm"
    assert decision.decision == "hold"
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
        "data_quality_flags",
    }
