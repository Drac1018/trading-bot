from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from trading_mvp.models import MarketSnapshot, SkippedTradeEvent
from trading_mvp.schemas import MarketCandle, SkipQualityReasonEntry, SkipQualityReportResponse
from trading_mvp.time_utils import utcnow_naive

GOOD_SKIP_THRESHOLD = 0.62
OVERCONSERVATIVE_SKIP_THRESHOLD = 0.38

NO_TRADE_ZONE_SKIP_REASONS = {"no_trade_zone"}
META_GATE_SKIP_REASONS = {"meta_gate_reject"}
BREADTH_SKIP_REASONS = {"breadth_veto"}
DISABLE_SKIP_REASONS = {"disable_bucket", "setup_cluster_disable"}


@dataclass(slots=True)
class _ReasonAccumulator:
    events: int = 0
    evaluated_events: int = 0
    pending_events: int = 0
    followup_returns: list[float] = field(default_factory=list)
    skip_quality_scores: list[float] = field(default_factory=list)
    tp_hits: int = 0
    sl_hits: int = 0
    reached_half_r_hits: int = 0
    good_skips: int = 0
    overconservative_skips: int = 0


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        if value in {None, ""}:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _timeframe_minutes(timeframe: str) -> int:
    raw = str(timeframe or "").strip().lower()
    if raw.endswith("m") and raw[:-1].isdigit():
        return max(int(raw[:-1]), 1)
    if raw.endswith("h") and raw[:-1].isdigit():
        return max(int(raw[:-1]) * 60, 60)
    if raw.endswith("d") and raw[:-1].isdigit():
        return max(int(raw[:-1]) * 24 * 60, 24 * 60)
    return 60


def infer_skip_horizon_minutes(
    *,
    timeframe: str,
    entry_mode: str | None = None,
    scenario: str | None = None,
) -> int:
    timeframe_minutes = _timeframe_minutes(timeframe)
    normalized_entry_mode = str(entry_mode or "").lower()
    normalized_scenario = str(scenario or "").lower()
    if normalized_entry_mode == "breakout_confirm":
        multiplier = 4
    elif normalized_entry_mode == "pullback_confirm" or normalized_scenario == "pullback_entry":
        multiplier = 8
    elif normalized_entry_mode == "continuation":
        multiplier = 6
    else:
        multiplier = 6
    return max(30, min(timeframe_minutes * multiplier, 12 * 60))


def record_skip_event(
    session: Session,
    *,
    symbol: str,
    timeframe: str,
    scenario: str,
    regime: str,
    trend_alignment: str,
    entry_mode: str,
    skip_reason: str,
    skip_source: str,
    market_snapshot_id: int | None,
    decision_run_id: int | None,
    risk_check_id: int | None = None,
    expected_side: str | None = None,
    rejected_side: str | None = None,
    reference_price: float | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    horizon_minutes: int | None = None,
    payload: dict[str, Any] | None = None,
) -> SkippedTradeEvent:
    row = SkippedTradeEvent(
        symbol=str(symbol or "").upper(),
        timeframe=str(timeframe or ""),
        scenario=str(scenario or "unspecified"),
        regime=str(regime or "unknown"),
        trend_alignment=str(trend_alignment or "unknown"),
        entry_mode=str(entry_mode or "none"),
        skip_reason=str(skip_reason or "unspecified"),
        skip_source=str(skip_source or "decision"),
        market_snapshot_id=market_snapshot_id,
        decision_run_id=decision_run_id,
        risk_check_id=risk_check_id,
        expected_side=str(expected_side or "").lower() or None,
        rejected_side=str(rejected_side or "").lower() or None,
        reference_price=reference_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        horizon_minutes=horizon_minutes
        if horizon_minutes is not None
        else infer_skip_horizon_minutes(timeframe=timeframe, entry_mode=entry_mode, scenario=scenario),
        payload=dict(payload or {}),
    )
    session.add(row)
    session.flush()
    return row


def _load_snapshot(session: Session, snapshot_id: int | None) -> MarketSnapshot | None:
    if snapshot_id is None:
        return None
    return session.scalar(select(MarketSnapshot).where(MarketSnapshot.id == snapshot_id))


def _load_row_candles(row: MarketSnapshot) -> list[MarketCandle]:
    payload = _as_dict(row.payload)
    raw_candles = payload.get("candles")
    if not isinstance(raw_candles, list):
        return []
    candles: list[MarketCandle] = []
    for item in raw_candles:
        raw_item = _as_dict(item)
        raw_timestamp = raw_item.get("timestamp")
        if isinstance(raw_timestamp, datetime):
            timestamp = raw_timestamp
        else:
            try:
                timestamp = datetime.fromisoformat(str(raw_timestamp))
            except (TypeError, ValueError):
                continue
        try:
            candles.append(
                MarketCandle(
                    timestamp=timestamp,
                    open=_safe_float(raw_item.get("open")),
                    high=_safe_float(raw_item.get("high")),
                    low=_safe_float(raw_item.get("low")),
                    close=_safe_float(raw_item.get("close")),
                    volume=_safe_float(raw_item.get("volume")),
                )
            )
        except Exception:
            continue
    return candles


def _collect_future_candles(
    session: Session,
    *,
    symbol: str,
    timeframe: str,
    snapshot_time: datetime,
    horizon_end: datetime,
) -> list[MarketCandle]:
    buffer_minutes = max(_timeframe_minutes(timeframe), 1)
    rows = list(
        session.scalars(
            select(MarketSnapshot)
            .where(
                MarketSnapshot.symbol == symbol,
                MarketSnapshot.timeframe == timeframe,
                MarketSnapshot.snapshot_time > snapshot_time,
                MarketSnapshot.snapshot_time <= (horizon_end + timedelta(minutes=buffer_minutes)),
            )
            .order_by(MarketSnapshot.snapshot_time.asc(), MarketSnapshot.id.asc())
        )
    )
    candle_lookup: dict[datetime, MarketCandle] = {}
    for row in rows:
        for candle in _load_row_candles(row):
            if candle.timestamp <= snapshot_time or candle.timestamp > horizon_end:
                continue
            candle_lookup[candle.timestamp] = candle
    return [candle_lookup[key] for key in sorted(candle_lookup)]


def _first_touch(
    *,
    side: str,
    candles: list[MarketCandle],
    take_profit: float | None,
    stop_loss: float | None,
) -> str | None:
    if take_profit is None and stop_loss is None:
        return None
    normalized_side = str(side or "").lower()
    for candle in candles:
        if normalized_side == "long":
            tp_hit = take_profit is not None and candle.high >= take_profit
            sl_hit = stop_loss is not None and candle.low <= stop_loss
        elif normalized_side == "short":
            tp_hit = take_profit is not None and candle.low <= take_profit
            sl_hit = stop_loss is not None and candle.high >= stop_loss
        else:
            return None
        if tp_hit and sl_hit:
            return "ambiguous"
        if tp_hit:
            return "tp"
        if sl_hit:
            return "sl"
    return None


def _followup_return(
    *,
    side: str,
    reference_price: float,
    future_close: float,
) -> float:
    if reference_price <= 0:
        return 0.0
    normalized_side = str(side or "").lower()
    if normalized_side == "short":
        return (reference_price - future_close) / reference_price
    return (future_close - reference_price) / reference_price


def _half_r_reached(
    *,
    side: str,
    candles: list[MarketCandle],
    reference_price: float | None,
    stop_loss: float | None,
) -> bool:
    if reference_price is None or stop_loss is None:
        return False
    risk_per_unit = abs(reference_price - stop_loss)
    if risk_per_unit <= 0:
        return False
    normalized_side = str(side or "").lower()
    if normalized_side == "short":
        threshold = reference_price - (risk_per_unit * 0.5)
        return any(candle.low <= threshold for candle in candles)
    threshold = reference_price + (risk_per_unit * 0.5)
    return any(candle.high >= threshold for candle in candles)


def _skip_quality_score(
    *,
    followup_return: float,
    first_touch: str | None,
    hit_tp: bool,
    hit_sl: bool,
    reached_half_r: bool,
) -> tuple[float, str]:
    score = 0.5
    if first_touch == "sl":
        score += 0.32
    elif first_touch == "tp":
        score -= 0.32
    elif hit_sl and not hit_tp:
        score += 0.2
    elif hit_tp and not hit_sl:
        score -= 0.2
    if reached_half_r and not hit_sl:
        score -= 0.12
    followup_adjustment = max(-0.18, min(0.18, (-followup_return / 0.015) * 0.12))
    score = max(0.0, min(1.0, score + followup_adjustment))
    if score >= GOOD_SKIP_THRESHOLD:
        return round(score, 6), "good_skip"
    if score <= OVERCONSERVATIVE_SKIP_THRESHOLD:
        return round(score, 6), "overconservative_skip"
    return round(score, 6), "neutral_skip"


def evaluate_skip_event(
    session: Session,
    skip_event: SkippedTradeEvent,
    *,
    evaluation_time: datetime | None = None,
) -> SkippedTradeEvent:
    if skip_event.status == "evaluated":
        return skip_event
    snapshot_row = _load_snapshot(session, skip_event.market_snapshot_id)
    evaluation_time = evaluation_time or utcnow_naive()
    if snapshot_row is None or skip_event.reference_price is None or not skip_event.expected_side:
        skip_event.status = "unevaluable"
        skip_event.evaluated_at = evaluation_time
        session.add(skip_event)
        session.flush()
        return skip_event
    horizon_end = snapshot_row.snapshot_time + timedelta(minutes=max(skip_event.horizon_minutes, 1))
    if evaluation_time < horizon_end:
        return skip_event
    future_candles = _collect_future_candles(
        session,
        symbol=skip_event.symbol,
        timeframe=skip_event.timeframe,
        snapshot_time=snapshot_row.snapshot_time,
        horizon_end=horizon_end,
    )
    if not future_candles:
        return skip_event
    followup_return = _followup_return(
        side=str(skip_event.expected_side or ""),
        reference_price=float(skip_event.reference_price or 0.0),
        future_close=float(future_candles[-1].close),
    )
    hit_tp = False
    hit_sl = False
    normalized_side = str(skip_event.expected_side or "").lower()
    if normalized_side == "long":
        hit_tp = skip_event.take_profit is not None and any(candle.high >= skip_event.take_profit for candle in future_candles)
        hit_sl = skip_event.stop_loss is not None and any(candle.low <= skip_event.stop_loss for candle in future_candles)
    elif normalized_side == "short":
        hit_tp = skip_event.take_profit is not None and any(candle.low <= skip_event.take_profit for candle in future_candles)
        hit_sl = skip_event.stop_loss is not None and any(candle.high >= skip_event.stop_loss for candle in future_candles)
    reached_half_r = _half_r_reached(
        side=normalized_side,
        candles=future_candles,
        reference_price=skip_event.reference_price,
        stop_loss=skip_event.stop_loss,
    )
    first_touch = _first_touch(
        side=normalized_side,
        candles=future_candles,
        take_profit=skip_event.take_profit,
        stop_loss=skip_event.stop_loss,
    )
    skip_quality_score, skip_quality_label = _skip_quality_score(
        followup_return=followup_return,
        first_touch=first_touch,
        hit_tp=hit_tp,
        hit_sl=hit_sl,
        reached_half_r=reached_half_r,
    )
    skip_event.status = "evaluated"
    skip_event.skipped_trade_followup_return = round(followup_return, 6)
    skip_event.would_have_hit_tp = hit_tp
    skip_event.would_have_hit_sl = hit_sl
    skip_event.would_have_reached_0_5r = reached_half_r
    skip_event.skip_quality_score = skip_quality_score
    skip_event.skip_quality_label = skip_quality_label
    skip_event.evaluated_at = evaluation_time
    updated_payload = _as_dict(skip_event.payload)
    updated_payload["evaluation_detail"] = {
        "evaluated_at": evaluation_time.isoformat(),
        "horizon_end": horizon_end.isoformat(),
        "first_touch": first_touch,
        "future_candle_count": len(future_candles),
    }
    skip_event.payload = updated_payload
    session.add(skip_event)
    session.flush()
    return skip_event


def evaluate_pending_skip_events(
    session: Session,
    *,
    evaluation_time: datetime | None = None,
    lookback_days: int = 21,
    limit: int = 512,
) -> list[SkippedTradeEvent]:
    evaluation_time = evaluation_time or utcnow_naive()
    since = evaluation_time - timedelta(days=lookback_days)
    rows = list(
        session.scalars(
            select(SkippedTradeEvent)
            .where(
                SkippedTradeEvent.created_at >= since,
                SkippedTradeEvent.status == "pending_evaluation",
            )
            .order_by(desc(SkippedTradeEvent.created_at))
            .limit(limit)
        )
    )
    for row in rows:
        evaluate_skip_event(session, row, evaluation_time=evaluation_time)
    session.flush()
    return rows


def _reason_entry(*, skip_reason: str, accumulator: _ReasonAccumulator) -> SkipQualityReasonEntry:
    evaluated = max(accumulator.evaluated_events, 1)
    return SkipQualityReasonEntry(
        skip_reason=skip_reason,
        events=accumulator.events,
        evaluated_events=accumulator.evaluated_events,
        pending_events=accumulator.pending_events,
        avg_followup_return=round(sum(accumulator.followup_returns) / max(len(accumulator.followup_returns), 1), 6),
        would_have_hit_tp_rate=round(accumulator.tp_hits / evaluated, 6) if accumulator.evaluated_events else 0.0,
        would_have_hit_sl_rate=round(accumulator.sl_hits / evaluated, 6) if accumulator.evaluated_events else 0.0,
        would_have_reached_0_5r_rate=round(accumulator.reached_half_r_hits / evaluated, 6)
        if accumulator.evaluated_events
        else 0.0,
        avg_skip_quality_score=round(sum(accumulator.skip_quality_scores) / max(len(accumulator.skip_quality_scores), 1), 6),
        good_skip_rate=round(accumulator.good_skips / evaluated, 6) if accumulator.evaluated_events else 0.0,
        overconservative_rate=round(accumulator.overconservative_skips / evaluated, 6)
        if accumulator.evaluated_events
        else 0.0,
    )


def _group_summary(
    *,
    skip_reason: str,
    rows: list[SkippedTradeEvent],
) -> SkipQualityReasonEntry | None:
    if not rows:
        return None
    accumulator = _ReasonAccumulator()
    for row in rows:
        accumulator.events += 1
        if row.status == "evaluated":
            accumulator.evaluated_events += 1
            accumulator.followup_returns.append(_safe_float(row.skipped_trade_followup_return))
            accumulator.skip_quality_scores.append(_safe_float(row.skip_quality_score, default=0.5))
            if bool(row.would_have_hit_tp):
                accumulator.tp_hits += 1
            if bool(row.would_have_hit_sl):
                accumulator.sl_hits += 1
            if bool(row.would_have_reached_0_5r):
                accumulator.reached_half_r_hits += 1
            if row.skip_quality_label == "good_skip":
                accumulator.good_skips += 1
            elif row.skip_quality_label == "overconservative_skip":
                accumulator.overconservative_skips += 1
        else:
            accumulator.pending_events += 1
    return _reason_entry(skip_reason=skip_reason, accumulator=accumulator)


def build_skip_quality_report(
    session: Session,
    *,
    lookback_days: int = 21,
    limit: int = 512,
    evaluation_time: datetime | None = None,
) -> SkipQualityReportResponse:
    evaluation_time = evaluation_time or utcnow_naive()
    evaluate_pending_skip_events(
        session,
        evaluation_time=evaluation_time,
        lookback_days=lookback_days,
        limit=limit,
    )
    since = evaluation_time - timedelta(days=lookback_days)
    rows = list(
        session.scalars(
            select(SkippedTradeEvent)
            .where(SkippedTradeEvent.created_at >= since)
            .order_by(desc(SkippedTradeEvent.created_at))
            .limit(limit)
        )
    )
    reason_accumulators: dict[str, _ReasonAccumulator] = defaultdict(_ReasonAccumulator)
    for row in rows:
        accumulator = reason_accumulators[str(row.skip_reason or "unspecified")]
        accumulator.events += 1
        if row.status == "evaluated":
            accumulator.evaluated_events += 1
            accumulator.followup_returns.append(_safe_float(row.skipped_trade_followup_return))
            accumulator.skip_quality_scores.append(_safe_float(row.skip_quality_score, default=0.5))
            if bool(row.would_have_hit_tp):
                accumulator.tp_hits += 1
            if bool(row.would_have_hit_sl):
                accumulator.sl_hits += 1
            if bool(row.would_have_reached_0_5r):
                accumulator.reached_half_r_hits += 1
            if row.skip_quality_label == "good_skip":
                accumulator.good_skips += 1
            elif row.skip_quality_label == "overconservative_skip":
                accumulator.overconservative_skips += 1
        else:
            accumulator.pending_events += 1
    reason_reports = [
        _reason_entry(skip_reason=skip_reason, accumulator=accumulator)
        for skip_reason, accumulator in sorted(reason_accumulators.items(), key=lambda item: item[0])
    ]
    return SkipQualityReportResponse(
        generated_at=evaluation_time,
        lookback_days=lookback_days,
        total_events=len(rows),
        evaluated_events=sum(1 for row in rows if row.status == "evaluated"),
        pending_events=sum(1 for row in rows if row.status == "pending_evaluation"),
        reason_reports=reason_reports,
        no_trade_zone_summary=_group_summary(
            skip_reason="no_trade_zone",
            rows=[row for row in rows if row.skip_reason in NO_TRADE_ZONE_SKIP_REASONS],
        ),
        meta_gate_summary=_group_summary(
            skip_reason="meta_gate_reject",
            rows=[row for row in rows if row.skip_reason in META_GATE_SKIP_REASONS],
        ),
        breadth_veto_summary=_group_summary(
            skip_reason="breadth_veto",
            rows=[row for row in rows if row.skip_reason in BREADTH_SKIP_REASONS],
        ),
        disable_bucket_summary=_group_summary(
            skip_reason="disable_bucket",
            rows=[row for row in rows if row.skip_reason in DISABLE_SKIP_REASONS],
        ),
    )
