from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from trading_mvp.models import SkippedTradeEvent
from trading_mvp.schemas import (
    MarketCandle,
    MarketSnapshotPayload,
    TradeDecisionCandidate,
    TradeDecisionCandidateScore,
)
from trading_mvp.services.market_data import persist_market_snapshot
from trading_mvp.services.orchestrator import TradingOrchestrator
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.services.skip_quality import build_skip_quality_report, record_skip_event
from trading_mvp.time_utils import utcnow_naive


def _snapshot(symbol: str, timeframe: str, candles: list[MarketCandle]) -> MarketSnapshotPayload:
    latest = candles[-1]
    return MarketSnapshotPayload(
        symbol=symbol,
        timeframe=timeframe,
        snapshot_time=latest.timestamp,
        latest_price=latest.close,
        latest_volume=latest.volume,
        candle_count=len(candles),
        is_stale=False,
        is_complete=True,
        candles=candles,
    )


def _candle(*, timestamp, open_price: float, high: float, low: float, close: float, volume: float = 100.0) -> MarketCandle:
    return MarketCandle(
        timestamp=timestamp,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def _persist_snapshot_series(
    db_session,
    *,
    symbol: str,
    timeframe: str,
    candles: list[MarketCandle],
    window: int = 4,
):
    rows = []
    for index in range(window - 1, len(candles)):
        rows.append(persist_market_snapshot(db_session, _snapshot(symbol, timeframe, candles[max(0, index - window + 1) : index + 1])))
    return rows


def _selection_candidate_row(
    *,
    symbol: str,
    decision: str,
    scenario: str,
    total_score: float,
    priority: bool,
    weak_volume: bool,
    primary_regime: str,
    trend_alignment: str,
    snapshot: MarketSnapshotPayload,
    entry_mode: str,
) -> dict[str, object]:
    candidate = TradeDecisionCandidate(
        candidate_id=f"{symbol}:15m:{scenario}",
        scenario=scenario,  # type: ignore[arg-type]
        decision=decision,  # type: ignore[arg-type]
        symbol=symbol,
        timeframe="15m",
        confidence=0.64,
        entry_zone_min=99.5 if decision in {"long", "short"} else None,
        entry_zone_max=100.5 if decision in {"long", "short"} else None,
        stop_loss=98.0 if decision == "long" else (102.0 if decision == "short" else None),
        take_profit=104.0 if decision == "long" else (96.0 if decision == "short" else None),
        max_holding_minutes=120,
        risk_pct=0.01,
        leverage=2.0,
        rationale_codes=["TEST_SELECTION_SKIP"],
        explanation_short="selection test",
        explanation_detailed="selection test candidate",
    )
    return {
        "symbol": symbol,
        "priority": priority,
        "candidate": candidate,
        "score": TradeDecisionCandidateScore(total_score=total_score),
        "feature_payload": None,
        "regime_summary": {
            "primary_regime": primary_regime,
            "trend_alignment": trend_alignment,
            "weak_volume": weak_volume,
            "momentum_weakening": weak_volume,
        },
        "performance_summary": {
            "score": 0.46,
            "sample_size": 2,
            "hit_rate": 0.5,
            "expectancy": 0.0,
            "net_pnl_after_fees": 0.0,
            "avg_signed_slippage_bps": 0.0,
            "loss_streak": 0,
            "underperforming": False,
            "components": {},
        },
        "entry_mode": entry_mode,
        "scenario_signature": f"{decision}:{scenario}:{primary_regime}:{trend_alignment}",
        "returns": [0.001, -0.001, 0.0005],
        "market_snapshot": snapshot,
    }


def test_rank_candidate_symbols_records_breadth_skip_event(db_session, monkeypatch) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.ai_enabled = True
    settings_row.tracked_symbols = ["BTCUSDT", "ETHUSDT"]
    db_session.add(settings_row)
    db_session.flush()

    now = utcnow_naive()
    snapshot = _snapshot(
        "ETHUSDT",
        "15m",
        [
            _candle(timestamp=now, open_price=100.0, high=100.2, low=99.8, close=100.0),
        ],
    )
    rows_by_symbol = {
        "BTCUSDT": _selection_candidate_row(
            symbol="BTCUSDT",
            decision="reduce",
            scenario="reduce",
            total_score=0.9,
            priority=True,
            weak_volume=True,
            primary_regime="range",
            trend_alignment="mixed",
            snapshot=snapshot.model_copy(update={"symbol": "BTCUSDT"}),
            entry_mode="manage_only",
        ),
        "ETHUSDT": _selection_candidate_row(
            symbol="ETHUSDT",
            decision="long",
            scenario="pullback_entry",
            total_score=0.47,
            priority=False,
            weak_volume=True,
            primary_regime="range",
            trend_alignment="bullish_aligned",
            snapshot=snapshot,
            entry_mode="pullback_confirm",
        ),
    }
    rows_by_symbol["BTCUSDT"]["returns"] = []

    monkeypatch.setattr(
        TradingOrchestrator,
        "_build_lead_market_features",
        lambda self, **kwargs: {},
    )
    monkeypatch.setattr(
        TradingOrchestrator,
        "_build_selection_candidate",
        lambda self, **kwargs: rows_by_symbol[kwargs["symbol"]],
    )

    result = TradingOrchestrator(db_session)._rank_candidate_symbols(
        decision_symbols=["BTCUSDT", "ETHUSDT"],
        timeframe="15m",
        upto_index=None,
        force_stale=False,
    )

    skip_rows = list(db_session.scalars(select(SkippedTradeEvent).order_by(SkippedTradeEvent.id.asc())))
    assert result["breadth_regime"] == "weak_breadth"
    assert len(skip_rows) == 1
    assert skip_rows[0].symbol == "ETHUSDT"
    assert skip_rows[0].skip_source == "selection"
    assert skip_rows[0].skip_reason == "breadth_veto"
    assert skip_rows[0].market_snapshot_id is not None
    assert skip_rows[0].expected_side == "long"


def test_skip_quality_followup_evaluation_and_report(db_session) -> None:
    now = utcnow_naive().replace(minute=0, second=0, microsecond=0)
    btc_candles = [
        _candle(timestamp=now - timedelta(minutes=45), open_price=100.0, high=100.5, low=99.7, close=100.0),
        _candle(timestamp=now - timedelta(minutes=30), open_price=100.0, high=100.2, low=99.4, close=99.8),
        _candle(timestamp=now - timedelta(minutes=15), open_price=99.8, high=99.9, low=99.0, close=99.4),
        _candle(timestamp=now, open_price=99.4, high=99.6, low=99.0, close=99.2),
        _candle(timestamp=now + timedelta(minutes=15), open_price=99.2, high=99.3, low=98.0, close=98.4),
        _candle(timestamp=now + timedelta(minutes=30), open_price=98.4, high=98.6, low=97.4, close=97.8),
        _candle(timestamp=now + timedelta(minutes=45), open_price=97.8, high=98.0, low=97.0, close=97.2),
        _candle(timestamp=now + timedelta(minutes=60), open_price=97.2, high=97.5, low=96.8, close=97.0),
    ]
    eth_candles = [
        _candle(timestamp=now - timedelta(minutes=45), open_price=100.0, high=100.3, low=99.7, close=100.0),
        _candle(timestamp=now - timedelta(minutes=30), open_price=100.0, high=100.4, low=99.8, close=100.1),
        _candle(timestamp=now - timedelta(minutes=15), open_price=100.1, high=100.5, low=99.9, close=100.2),
        _candle(timestamp=now, open_price=100.2, high=100.6, low=100.0, close=100.3),
        _candle(timestamp=now + timedelta(minutes=15), open_price=100.3, high=101.5, low=100.2, close=101.2),
        _candle(timestamp=now + timedelta(minutes=30), open_price=101.2, high=103.8, low=101.0, close=103.2),
        _candle(timestamp=now + timedelta(minutes=45), open_price=103.2, high=104.6, low=103.0, close=104.2),
        _candle(timestamp=now + timedelta(minutes=60), open_price=104.2, high=105.0, low=104.0, close=104.8),
    ]
    btc_rows = _persist_snapshot_series(db_session, symbol="BTCUSDT", timeframe="15m", candles=btc_candles)
    eth_rows = _persist_snapshot_series(db_session, symbol="ETHUSDT", timeframe="15m", candles=eth_candles)

    record_skip_event(
        db_session,
        symbol="BTCUSDT",
        timeframe="15m",
        scenario="pullback_entry",
        regime="bullish",
        trend_alignment="bullish_aligned",
        entry_mode="pullback_confirm",
        skip_reason="no_trade_zone",
        skip_source="decision",
        market_snapshot_id=btc_rows[0].id,
        decision_run_id=101,
        expected_side="long",
        rejected_side="long",
        reference_price=99.2,
        stop_loss=98.2,
        take_profit=101.2,
        horizon_minutes=60,
        payload={"rationale_codes": ["NO_TRADE_ZONE_RANGE_WEAK_VOLUME"]},
    )
    record_skip_event(
        db_session,
        symbol="ETHUSDT",
        timeframe="15m",
        scenario="trend_follow",
        regime="bullish",
        trend_alignment="bullish_aligned",
        entry_mode="breakout_confirm",
        skip_reason="meta_gate_reject",
        skip_source="risk",
        market_snapshot_id=eth_rows[0].id,
        decision_run_id=202,
        risk_check_id=303,
        expected_side="long",
        rejected_side="long",
        reference_price=100.3,
        stop_loss=99.0,
        take_profit=104.0,
        horizon_minutes=60,
        payload={"reason_codes": ["META_GATE_LOW_HIT_PROBABILITY"]},
    )

    report = build_skip_quality_report(
        db_session,
        lookback_days=21,
        evaluation_time=now + timedelta(hours=3),
    )

    btc_skip = db_session.scalar(
        select(SkippedTradeEvent).where(SkippedTradeEvent.skip_reason == "no_trade_zone")
    )
    eth_skip = db_session.scalar(
        select(SkippedTradeEvent).where(SkippedTradeEvent.skip_reason == "meta_gate_reject")
    )

    assert btc_skip is not None and btc_skip.status == "evaluated"
    assert eth_skip is not None and eth_skip.status == "evaluated"
    assert btc_skip.would_have_hit_sl is True
    assert btc_skip.would_have_hit_tp is False
    assert btc_skip.skip_quality_label == "good_skip"
    assert eth_skip.would_have_hit_tp is True
    assert eth_skip.skip_quality_label == "overconservative_skip"
    assert report.total_events == 2
    assert report.evaluated_events == 2
    assert report.no_trade_zone_summary is not None
    assert report.no_trade_zone_summary.good_skip_rate == 1.0
    assert report.meta_gate_summary is not None
    assert report.meta_gate_summary.overconservative_rate == 1.0


def test_orchestrator_wrapper_returns_skip_quality_report(db_session) -> None:
    now = utcnow_naive()
    snapshot_row = persist_market_snapshot(
        db_session,
        _snapshot(
            "SOLUSDT",
            "15m",
            [
                _candle(timestamp=now, open_price=100.0, high=100.2, low=99.8, close=100.0),
            ],
        ),
    )
    record_skip_event(
        db_session,
        symbol="SOLUSDT",
        timeframe="15m",
        scenario="trend_follow",
        regime="transition",
        trend_alignment="mixed",
        entry_mode="breakout_confirm",
        skip_reason="breadth_veto",
        skip_source="selection",
        market_snapshot_id=snapshot_row.id,
        decision_run_id=None,
        expected_side="long",
        rejected_side="long",
        reference_price=100.0,
        stop_loss=98.0,
        take_profit=103.0,
        horizon_minutes=180,
        payload={},
    )

    report = TradingOrchestrator(db_session).build_skip_quality_report(lookback_days=21, limit=32)

    assert report.total_events == 1
    assert report.pending_events == 1
    assert report.breadth_veto_summary is not None
    assert report.breadth_veto_summary.events == 1
