from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from trading_mvp.database import Base
from trading_mvp.models import AgentRun, Execution, Order, Position, RiskCheck, Setting
from trading_mvp.schemas import (
    MarketCandle,
    MarketSnapshotPayload,
    ReplayBreakdownEntry,
    ReplayComparisonEntry,
    ReplayMetricSummary,
    ReplayParameterRecommendation,
    ReplayUnderperformingBucket,
    ReplayValidationRequest,
    ReplayValidationResponse,
    ReplayVariantReport,
    RiskCheckResult,
    TradeDecision,
)
from trading_mvp.services.account import (
    calculate_unrealized_pnl,
    create_exchange_pnl_snapshot,
    get_latest_pnl_snapshot,
)
from trading_mvp.services.binance import BinanceClient
from trading_mvp.services.execution import _reduce_fraction_for_decision, build_execution_intent
from trading_mvp.services.execution_policy import select_execution_plan
from trading_mvp.services.market_data import generate_seed_candles
from trading_mvp.services.orchestrator import TradingOrchestrator
from trading_mvp.services.position_management import (
    mark_partial_take_profit_taken,
    seed_position_management_metadata,
)
from trading_mvp.time_utils import utcnow_naive

REPLAY_FEE_RATE = 0.0004
LOGIC_VARIANTS = ("baseline_old", "improved")
RECENT_WALK_FORWARD_MIN_CYCLES = 8
UNDERPERFORMING_BUCKET_MIN_SAMPLE = 3


def _load_replay_series(
    request: ReplayValidationRequest,
    *,
    source_settings: Setting,
    symbols: list[str],
    timeframe: str,
) -> tuple[dict[tuple[str, str], list[MarketCandle]], str]:
    required_points = max(request.start_index + request.cycles + 5, 160)
    if request.data_source_type == "synthetic_seed":
        return (
            {(symbol, timeframe): generate_seed_candles(symbol=symbol, timeframe=timeframe, points=required_points) for symbol in symbols},
            "synthetic_seed_candles_from_market_data.generate_seed_candles",
        )
    client = BinanceClient(
        testnet_enabled=source_settings.binance_testnet_enabled,
        futures_enabled=True,
    )
    series_map: dict[tuple[str, str], list[MarketCandle]] = {}
    for symbol in symbols:
        candles = client.fetch_klines(symbol=symbol, interval=timeframe, limit=min(required_points, 1500))
        if len(candles) <= request.start_index:
            raise RuntimeError(
                f"Replay data for {symbol} {timeframe} is insufficient: need index {request.start_index}, got {len(candles)} candles."
            )
        series_map[(symbol, timeframe)] = candles
    basis = "binance_futures_klines_from_services.binance.BinanceClient.fetch_klines"
    return series_map, basis


def _snapshot_from_series(
    *,
    symbol: str,
    timeframe: str,
    series: list[MarketCandle],
    candle_index: int,
    lookback: int = 60,
) -> MarketSnapshotPayload:
    safe_index = max(min(candle_index, len(series) - 1), 0)
    candles = series[max(0, safe_index - lookback + 1) : safe_index + 1]
    latest = candles[-1]
    return MarketSnapshotPayload(
        symbol=symbol,
        timeframe=timeframe,
        snapshot_time=latest.timestamp,
        latest_price=latest.close,
        latest_volume=latest.volume,
        candle_count=len(candles),
        is_stale=False,
        is_complete=len(candles) >= min(lookback, 20),
        candles=candles,
    )


@dataclass(slots=True)
class ReplayDecisionRecord:
    symbol: str
    timeframe: str
    regime: str
    trend_alignment: str
    scenario: str
    entry_mode: str
    execution_policy_profile: str
    cycle_index: int
    decision_time: datetime
    blocked: bool
    held: bool
    rationale_codes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ReplayTradeRecord:
    symbol: str
    timeframe: str
    regime: str
    trend_alignment: str
    scenario: str
    entry_mode: str
    execution_policy_profile: str
    cycle_index: int
    opened_at: datetime | None
    closed_at: datetime | None
    holding_minutes: float
    gross_pnl: float
    net_pnl: float
    fees: float
    rationale_codes: list[str] = field(default_factory=list)
    close_outcome: str = "manual_close"
    arrival_slippage_pct: float = 0.0
    realized_slippage_pct: float = 0.0
    first_fill_latency_seconds: float = 0.0
    cancel_attempted: bool = False
    cancel_succeeded: bool = False
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    mfe_pnl: float = 0.0
    mae_pnl: float = 0.0
    stop_hit: bool = False
    tp_hit: bool = False
    partial_tp_contribution: float = 0.0
    runner_contribution: float = 0.0


@dataclass(slots=True)
class ReplayAccumulator:
    decisions: int = 0
    closed_trades: int = 0
    blocked: int = 0
    held: int = 0
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    fees: float = 0.0
    arrival_slippage_values: list[float] = field(default_factory=list)
    realized_slippage_values: list[float] = field(default_factory=list)
    first_fill_latency_values: list[float] = field(default_factory=list)
    holding_minutes_values: list[float] = field(default_factory=list)
    cancel_attempts: int = 0
    cancel_successes: int = 0
    stop_hits: int = 0
    tp_hits: int = 0
    partial_tp_contribution_total: float = 0.0
    runner_contribution_total: float = 0.0
    trade_nets: list[float] = field(default_factory=list)
    mfe_pct_values: list[float] = field(default_factory=list)
    mae_pct_values: list[float] = field(default_factory=list)


@dataclass(slots=True)
class ReplayVariantState:
    decisions: list[ReplayDecisionRecord] = field(default_factory=list)
    trades: list[ReplayTradeRecord] = field(default_factory=list)
    equity_points: list[float] = field(default_factory=list)


def _copy_settings(
    source: Setting,
    session: Session,
    *,
    timeframe: str,
    symbols: list[str],
    logic_variant: str,
) -> Setting:
    row = Setting()
    for column in Setting.__table__.columns:
        if column.name in {"id", "created_at", "updated_at"}:
            continue
        setattr(row, column.name, getattr(source, column.name))
    row.default_timeframe = timeframe
    row.default_symbol = symbols[0] if symbols else source.default_symbol
    row.tracked_symbols = symbols
    row.ai_enabled = True
    row.live_trading_enabled = False
    row.manual_live_approval = False
    row.live_execution_armed = False
    row.live_execution_armed_until = None
    row.binance_market_data_enabled = False
    row.binance_api_key_encrypted = ""
    row.binance_api_secret_encrypted = ""
    if logic_variant == "baseline_old":
        row.adaptive_signal_enabled = False
        row.position_management_enabled = False
        row.break_even_enabled = False
        row.atr_trailing_stop_enabled = False
        row.partial_take_profit_enabled = False
        row.holding_edge_decay_enabled = False
        row.reduce_on_regime_shift_enabled = False
    session.add(row)
    session.flush()
    return row
def _extract_decision_context(session: Session, decision_run_id: int) -> tuple[str, str]:
    row = session.scalar(select(AgentRun).where(AgentRun.id == decision_run_id))
    if row is None:
        return "unknown", "unknown"
    input_payload = row.input_payload if isinstance(row.input_payload, dict) else {}
    features = input_payload.get("features") if isinstance(input_payload.get("features"), dict) else {}
    regime = features.get("regime") if isinstance(features.get("regime"), dict) else {}
    primary_regime = str(regime.get("primary_regime") or "unknown")
    trend_alignment = str(regime.get("trend_alignment") or "unknown")
    return primary_regime, trend_alignment


def _decision_scenario(decision: TradeDecision) -> str:
    if decision.decision == "hold":
        return "hold"
    if decision.decision == "reduce":
        return "reduce"
    if decision.decision == "exit":
        return "exit"
    if any(str(code).startswith("PROTECTION_") for code in decision.rationale_codes):
        return "protection_restore"
    if decision.entry_mode == "pullback_confirm":
        return "pullback_entry"
    if decision.entry_mode == "breakout_confirm":
        return "breakout_exception"
    return "trend_follow"


def _normalize_entry_mode(entry_mode: str | None) -> str:
    if entry_mode in {"breakout_confirm", "pullback_confirm", "immediate", "none"}:
        return entry_mode
    return "none"


def _extract_decision_row(session: Session, decision_run_id: int) -> AgentRun:
    row = session.scalar(select(AgentRun).where(AgentRun.id == decision_run_id))
    if row is None:
        raise RuntimeError(f"Decision run {decision_run_id} not found in replay session.")
    return row


def _open_position(session: Session, symbol: str) -> Position | None:
    return session.scalar(
        select(Position)
        .where(Position.symbol == symbol, Position.status == "open", Position.mode == "live")
        .order_by(Position.opened_at.desc(), Position.id.desc())
        .limit(1)
    )


def _open_positions(session: Session, symbol: str) -> list[Position]:
    return list(
        session.scalars(
            select(Position)
            .where(Position.symbol == symbol, Position.status == "open", Position.mode == "live")
            .order_by(Position.opened_at.asc(), Position.id.asc())
        )
    )


def _slippage_pct(*, side: str, requested_price: float, fill_price: float) -> float:
    if requested_price <= 0:
        return 0.0
    if side == "buy":
        return max((fill_price - requested_price) / requested_price, 0.0)
    return max((requested_price - fill_price) / requested_price, 0.0)


def _position_replay_metadata(position: Position) -> dict[str, Any]:
    metadata = position.metadata_json if isinstance(position.metadata_json, dict) else {}
    replay = metadata.get("replay") if isinstance(metadata.get("replay"), dict) else {}
    return replay


def _update_position_replay_excursions(position: Position, *, high_price: float, low_price: float) -> None:
    if position.entry_price <= 0 or position.quantity <= 0:
        return
    metadata = position.metadata_json if isinstance(position.metadata_json, dict) else {}
    replay = metadata.get("replay") if isinstance(metadata.get("replay"), dict) else {}
    existing_mfe_pct = float(replay.get("mfe_pct") or 0.0)
    existing_mae_pct = float(replay.get("mae_pct") or 0.0)
    if position.side == "long":
        candidate_mfe_pct = max((high_price - position.entry_price) / position.entry_price, 0.0)
        candidate_mae_pct = max((position.entry_price - low_price) / position.entry_price, 0.0)
    else:
        candidate_mfe_pct = max((position.entry_price - low_price) / position.entry_price, 0.0)
        candidate_mae_pct = max((high_price - position.entry_price) / position.entry_price, 0.0)
    replay["mfe_pct"] = round(max(existing_mfe_pct, candidate_mfe_pct), 8)
    replay["mae_pct"] = round(max(existing_mae_pct, candidate_mae_pct), 8)
    replay["mfe_pnl"] = round(replay["mfe_pct"] * position.entry_price * position.quantity, 8)
    replay["mae_pnl"] = round(replay["mae_pct"] * position.entry_price * position.quantity, 8)
    metadata["replay"] = replay
    position.metadata_json = metadata


def _create_order_row(
    session: Session,
    *,
    symbol: str,
    decision_run_id: int | None,
    risk_check_id: int | None,
    position_id: int | None,
    side: str,
    order_type: str,
    requested_quantity: float,
    requested_price: float,
    reduce_only: bool,
    close_only: bool,
    metadata_json: dict[str, Any],
) -> Order:
    order = Order(
        symbol=symbol,
        decision_run_id=decision_run_id,
        risk_check_id=risk_check_id,
        position_id=position_id,
        side=side,
        order_type=order_type,
        mode="live",
        status="filled",
        external_order_id=f"replay-{symbol}-{utcnow_naive().timestamp()}-{order_type.lower()}",
        client_order_id=None,
        reduce_only=reduce_only,
        close_only=close_only,
        parent_order_id=None,
        exchange_status="FILLED",
        requested_quantity=requested_quantity,
        requested_price=requested_price,
        filled_quantity=requested_quantity,
        average_fill_price=requested_price,
        reason_codes=[],
        metadata_json=metadata_json,
    )
    session.add(order)
    session.flush()
    return order


def _create_execution_row(
    session: Session,
    *,
    order: Order,
    position_id: int | None,
    fill_price: float,
    fill_quantity: float,
    fee_paid: float,
    slippage_pct: float,
    realized_pnl: float,
    payload: dict[str, Any],
) -> Execution:
    execution = Execution(
        order_id=order.id,
        position_id=position_id,
        symbol=order.symbol,
        status="filled",
        external_trade_id=f"replay-trade-{order.id}",
        fill_price=fill_price,
        fill_quantity=fill_quantity,
        fee_paid=fee_paid,
        commission_asset="USDT",
        slippage_pct=slippage_pct,
        realized_pnl=realized_pnl,
        payload=payload,
    )
    session.add(execution)
    session.flush()
    return execution


def _close_position_at_price(
    session: Session,
    *,
    position: Position,
    quantity: float,
    price: float,
    order_type: str,
    decision_run_id: int | None,
    risk_check_id: int | None,
    reason_codes: list[str],
    closed_at: datetime | None = None,
    cycle_index: int = -1,
) -> ReplayTradeRecord:
    replay_metadata = _position_replay_metadata(position)
    fee_paid = abs(quantity * price) * REPLAY_FEE_RATE
    gross_pnl = (
        (price - position.entry_price) * quantity
        if position.side == "long"
        else (position.entry_price - price) * quantity
    )
    net_pnl = gross_pnl - fee_paid
    side = "sell" if position.side == "long" else "buy"
    order = _create_order_row(
        session,
        symbol=position.symbol,
        decision_run_id=decision_run_id,
        risk_check_id=risk_check_id,
        position_id=position.id,
        side=side,
        order_type=order_type,
        requested_quantity=quantity,
        requested_price=price,
        reduce_only=True,
        close_only=True,
        metadata_json={"replay": {"synthetic_execution": True, "reason_codes": reason_codes}},
    )
    _create_execution_row(
        session,
        order=order,
        position_id=position.id,
        fill_price=price,
        fill_quantity=quantity,
        fee_paid=fee_paid,
        slippage_pct=0.0,
        realized_pnl=gross_pnl,
        payload={"synthetic_execution": True, "reason_codes": reason_codes},
    )
    remaining_quantity = max(position.quantity - quantity, 0.0)
    close_time = closed_at or utcnow_naive()
    position.realized_pnl += gross_pnl
    if remaining_quantity <= 1e-9:
        position.quantity = 0.0
        position.status = "closed"
        position.closed_at = close_time
        position.mark_price = price
        position.unrealized_pnl = 0.0
    else:
        position.quantity = remaining_quantity
        position.mark_price = price
        position.unrealized_pnl = calculate_unrealized_pnl(position, price)
    session.add(position)
    session.flush()
    management = position.metadata_json.get("position_management") if isinstance(position.metadata_json, dict) else {}
    partial_taken_before = bool(management.get("partial_take_profit_taken")) if isinstance(management, dict) else False
    is_partial_take_profit = (
        "POSITION_MANAGEMENT_PARTIAL_TAKE_PROFIT" in reason_codes
        or "POSITION_MANAGEMENT_LOCK_PARTIAL_PROFIT" in reason_codes
    )
    holding_minutes = 0.0
    if position.opened_at is not None:
        holding_minutes = max((close_time - position.opened_at).total_seconds() / 60.0, 0.0)
    return ReplayTradeRecord(
        symbol=position.symbol,
        timeframe=str(replay_metadata.get("entry_timeframe") or "unknown"),
        regime=str(replay_metadata.get("entry_regime") or "unknown"),
        trend_alignment=str(replay_metadata.get("entry_trend_alignment") or "unknown"),
        scenario=str(replay_metadata.get("entry_scenario") or "hold"),
        entry_mode=str(replay_metadata.get("entry_mode") or "none"),
        execution_policy_profile=str(replay_metadata.get("execution_policy_profile") or "UNSPECIFIED"),
        cycle_index=cycle_index,
        opened_at=position.opened_at,
        closed_at=close_time,
        holding_minutes=round(holding_minutes, 8),
        rationale_codes=[
            str(item)
            for item in (replay_metadata.get("entry_rationale_codes") or [])
            if item not in {None, ""}
        ],
        gross_pnl=round(gross_pnl, 8),
        net_pnl=round(net_pnl, 8),
        fees=round(fee_paid, 8),
        close_outcome=(
            "take_profit"
            if order_type.startswith("TAKE_PROFIT")
            else "stop_loss"
            if order_type.startswith("STOP")
            else "manual_close"
        ),
        arrival_slippage_pct=round(float(replay_metadata.get("entry_arrival_slippage_pct") or 0.0), 8),
        realized_slippage_pct=round(float(replay_metadata.get("entry_realized_slippage_pct") or 0.0), 8),
        first_fill_latency_seconds=round(float(replay_metadata.get("entry_first_fill_latency_seconds") or 0.0), 8),
        mfe_pct=round(float(replay_metadata.get("mfe_pct") or 0.0), 8),
        mae_pct=round(float(replay_metadata.get("mae_pct") or 0.0), 8),
        mfe_pnl=round(float(replay_metadata.get("mfe_pnl") or 0.0), 8),
        mae_pnl=round(float(replay_metadata.get("mae_pnl") or 0.0), 8),
        stop_hit=order_type.startswith("STOP"),
        tp_hit=order_type.startswith("TAKE_PROFIT"),
        partial_tp_contribution=round(net_pnl, 8) if is_partial_take_profit else 0.0,
        runner_contribution=round(net_pnl, 8) if partial_taken_before and not is_partial_take_profit else 0.0,
    )


def _advance_open_positions(
    session: Session,
    *,
    symbol: str,
    candle,
    cycle_index: int,
    variant_state: ReplayVariantState,
) -> None:
    for position in _open_positions(session, symbol):
        _update_position_replay_excursions(
            position,
            high_price=float(candle.high),
            low_price=float(candle.low),
        )
        hit_stop = False
        hit_take_profit = False
        if position.side == "long":
            hit_stop = bool(position.stop_loss and candle.low <= position.stop_loss)
            hit_take_profit = bool(position.take_profit and candle.high >= position.take_profit)
        else:
            hit_stop = bool(position.stop_loss and candle.high >= position.stop_loss)
            hit_take_profit = bool(position.take_profit and candle.low <= position.take_profit)

        if hit_stop or hit_take_profit:
            if hit_stop:
                close_price = float(position.stop_loss)
                order_type = "STOP_MARKET"
                reason_codes = ["REPLAY_STOP_LOSS"]
            else:
                close_price = float(position.take_profit)
                order_type = "TAKE_PROFIT_MARKET"
                reason_codes = ["REPLAY_TAKE_PROFIT"]
            variant_state.trades.append(
                _close_position_at_price(
                    session,
                    position=position,
                    quantity=position.quantity,
                    price=close_price,
                    order_type=order_type,
                    decision_run_id=None,
                    risk_check_id=None,
                    reason_codes=reason_codes,
                    closed_at=candle.timestamp,
                    cycle_index=cycle_index,
                )
            )
            continue

        position.mark_price = candle.close
        position.unrealized_pnl = calculate_unrealized_pnl(position, candle.close)
        session.add(position)
    session.flush()


def _apply_entry(
    session: Session,
    settings_row: Setting,
    *,
    market_snapshot: MarketSnapshotPayload,
    decision: TradeDecision,
    risk_result: RiskCheckResult,
    decision_run_id: int,
    risk_check_id: int | None,
    next_candle,
    regime: str,
    trend_alignment: str,
    logic_variant: str,
) -> None:
    latest_pnl = get_latest_pnl_snapshot(session, settings_row)
    intent = build_execution_intent(
        decision,
        market_snapshot,
        risk_result,
        settings_row,
        latest_pnl.equity,
        existing_position=None,
        operating_state=risk_result.operating_state,
    )
    execution_plan = select_execution_plan(intent, market_snapshot, settings_row)
    scenario = _decision_scenario(decision)
    quantity = intent.quantity
    fill_price = float(next_candle.open)
    side = "buy" if decision.decision == "long" else "sell"
    fee_paid = abs(quantity * fill_price) * REPLAY_FEE_RATE
    requested_price = float(intent.requested_price)
    entry_arrival_slippage_pct = _slippage_pct(
        side=side,
        requested_price=requested_price,
        fill_price=fill_price,
    )
    entry_first_fill_latency_seconds = max(
        (next_candle.timestamp - market_snapshot.snapshot_time).total_seconds(),
        0.0,
    )
    order = _create_order_row(
        session,
        symbol=decision.symbol,
        decision_run_id=decision_run_id,
        risk_check_id=risk_check_id,
        position_id=None,
        side=side,
        order_type="MARKET",
        requested_quantity=quantity,
        requested_price=requested_price,
        reduce_only=False,
        close_only=False,
        metadata_json={"replay": {"synthetic_execution": True, "logic_variant": logic_variant}},
    )
    position = Position(
        symbol=decision.symbol,
        mode="live",
        side=decision.decision,
        status="open",
        quantity=quantity,
        entry_price=fill_price,
        mark_price=fill_price,
        leverage=float(intent.leverage),
        stop_loss=float(decision.stop_loss or 0.0),
        take_profit=float(decision.take_profit or 0.0),
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        metadata_json={
            "replay": {
                "synthetic_execution": True,
                "entry_regime": regime,
                "entry_trend_alignment": trend_alignment,
                "entry_scenario": scenario,
                "entry_timeframe": decision.timeframe,
                "entry_mode": _normalize_entry_mode(decision.entry_mode),
                "logic_variant": logic_variant,
                "entry_decision_run_id": decision_run_id,
                "entry_rationale_codes": list(decision.rationale_codes),
                "execution_policy_profile": execution_plan.policy_profile,
                "execution_policy_name": execution_plan.policy_name,
                "entry_arrival_slippage_pct": round(entry_arrival_slippage_pct, 8),
                "entry_realized_slippage_pct": round(entry_arrival_slippage_pct, 8),
                "entry_first_fill_latency_seconds": round(entry_first_fill_latency_seconds, 8),
                "mfe_pct": 0.0,
                "mae_pct": 0.0,
                "mfe_pnl": 0.0,
                "mae_pnl": 0.0,
            }
        },
        opened_at=next_candle.timestamp,
    )
    session.add(position)
    session.flush()
    order.position_id = position.id
    session.add(order)
    session.flush()
    _create_execution_row(
        session,
        order=order,
        position_id=position.id,
        fill_price=fill_price,
        fill_quantity=quantity,
        fee_paid=fee_paid,
        slippage_pct=entry_arrival_slippage_pct,
        realized_pnl=0.0,
        payload={"synthetic_execution": True, "logic_variant": logic_variant},
    )
    seed_position_management_metadata(
        position,
        max_holding_minutes=decision.max_holding_minutes,
        timeframe=decision.timeframe,
        stop_loss=decision.stop_loss,
        take_profit=decision.take_profit,
        reset_partial_take_profit=True,
    )
    session.add(position)
    session.flush()


def _apply_reduce_or_exit(
    session: Session,
    *,
    position: Position,
    decision: TradeDecision,
    decision_run_id: int,
    risk_check_id: int | None,
    next_candle,
    cycle_index: int,
    variant_state: ReplayVariantState,
) -> None:
    fraction = 1.0 if decision.decision == "exit" else _reduce_fraction_for_decision(decision)
    quantity = min(position.quantity, position.quantity * fraction)
    if quantity <= 0:
        return
    variant_state.trades.append(
        _close_position_at_price(
            session,
            position=position,
            quantity=quantity,
            price=float(next_candle.open),
            order_type="MARKET",
            decision_run_id=decision_run_id,
            risk_check_id=risk_check_id,
            reason_codes=list(decision.rationale_codes),
            closed_at=next_candle.timestamp,
            cycle_index=cycle_index,
        )
    )
    if decision.decision == "reduce" and "POSITION_MANAGEMENT_PARTIAL_TAKE_PROFIT" in decision.rationale_codes:
        refreshed = session.scalar(select(Position).where(Position.id == position.id))
        if refreshed is not None and refreshed.status == "open":
            mark_partial_take_profit_taken(refreshed)
            session.add(refreshed)
            session.flush()


def _record_equity_point(session: Session, settings_row: Setting, variant_state: ReplayVariantState) -> None:
    net_realized = 0.0
    for execution in session.scalars(select(Execution)):
        net_realized += float(execution.realized_pnl) - float(execution.fee_paid)
    unrealized = sum(float(position.unrealized_pnl) for position in session.scalars(select(Position).where(Position.status == "open")))
    variant_state.equity_points.append(round(net_realized + unrealized, 8))


def _accumulate_decision(bucket: ReplayAccumulator, record: ReplayDecisionRecord) -> None:
    bucket.decisions += 1
    bucket.blocked += int(record.blocked)
    bucket.held += int(record.held)


def _accumulate_trade(bucket: ReplayAccumulator, trade: ReplayTradeRecord) -> None:
    bucket.closed_trades += 1
    bucket.gross_pnl += trade.gross_pnl
    bucket.net_pnl += trade.net_pnl
    bucket.fees += trade.fees
    bucket.arrival_slippage_values.append(trade.arrival_slippage_pct)
    bucket.realized_slippage_values.append(trade.realized_slippage_pct)
    bucket.first_fill_latency_values.append(trade.first_fill_latency_seconds)
    bucket.holding_minutes_values.append(trade.holding_minutes)
    bucket.cancel_attempts += int(trade.cancel_attempted)
    bucket.cancel_successes += int(trade.cancel_succeeded)
    bucket.stop_hits += int(trade.stop_hit)
    bucket.tp_hits += int(trade.tp_hit)
    bucket.partial_tp_contribution_total += trade.partial_tp_contribution
    bucket.runner_contribution_total += trade.runner_contribution
    bucket.trade_nets.append(trade.net_pnl)
    bucket.mfe_pct_values.append(trade.mfe_pct)
    bucket.mae_pct_values.append(trade.mae_pct)


def _accumulator_from_variant_state(variant_state: ReplayVariantState) -> ReplayAccumulator:
    accumulator = ReplayAccumulator()
    for decision in variant_state.decisions:
        _accumulate_decision(accumulator, decision)
    for trade in variant_state.trades:
        _accumulate_trade(accumulator, trade)
    return accumulator


def _recent_variant_state(variant_state: ReplayVariantState, *, cycles: int) -> ReplayVariantState:
    if not variant_state.decisions:
        return ReplayVariantState()
    recent_cycles = max(RECENT_WALK_FORWARD_MIN_CYCLES, cycles // 3)
    latest_cycle_index = max(item.cycle_index for item in variant_state.decisions)
    cutoff = max(latest_cycle_index - recent_cycles + 1, 0)
    return ReplayVariantState(
        decisions=[item for item in variant_state.decisions if item.cycle_index >= cutoff],
        trades=[item for item in variant_state.trades if item.cycle_index >= cutoff],
    )


def _bucket_sample_size(item: ReplayBreakdownEntry) -> int:
    return max(item.closed_trades, item.decisions)


def _underperforming_bucket_reasons(item: ReplayBreakdownEntry) -> list[str]:
    if _bucket_sample_size(item) < UNDERPERFORMING_BUCKET_MIN_SAMPLE:
        return []
    reasons: list[str] = []
    if item.expectancy < 0:
        reasons.append("NEGATIVE_EXPECTANCY")
    if item.net_pnl_after_fees < 0:
        reasons.append("NEGATIVE_NET_PNL")
    if item.average_mae_pct > max(item.average_mfe_pct * 1.1, 0.005):
        reasons.append("MAE_DOMINATES_MFE")
    if item.stop_hit_rate > 0.55 and item.tp_hit_rate < 0.35:
        reasons.append("STOP_DOMINANT")
    return reasons


def _best_bucket(items: list[ReplayBreakdownEntry]) -> ReplayBreakdownEntry | None:
    eligible = [
        item
        for item in items
        if item.closed_trades >= UNDERPERFORMING_BUCKET_MIN_SAMPLE and item.key not in {"UNSPECIFIED", "hold", "none"}
    ]
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda item: (
            item.expectancy,
            item.net_pnl_after_fees,
            item.win_rate,
            item.average_mfe_pct - item.average_mae_pct,
            -item.average_hold_time_minutes,
        ),
    )


def _recommendation_context_patch(recommendation: ReplayParameterRecommendation) -> tuple[dict[str, Any], dict[str, Any]]:
    adaptive_patch = {
        "walk_forward_recommendation": {
            "status": recommendation.status,
            "entry_mode_preference": recommendation.entry_mode_preference,
            "trailing_aggressiveness": recommendation.trailing_aggressiveness,
            "max_chase_bps": recommendation.max_chase_bps,
            "recommendation_basis": recommendation.recommendation_basis,
        }
    }
    risk_patch = {
        "walk_forward_recommendation": {
            "status": recommendation.status,
            "risk_pct_multiplier": recommendation.risk_pct_multiplier,
            "leverage_multiplier": recommendation.leverage_multiplier,
            "max_chase_bps": recommendation.max_chase_bps,
            "disable_candidate": recommendation.disable_candidate,
            "recommendation_basis": recommendation.recommendation_basis,
        }
    }
    return adaptive_patch, risk_patch


def _build_parameter_recommendation(
    *,
    logic_variant: str,
    recent_summary: ReplayMetricSummary,
    by_entry_mode: list[ReplayBreakdownEntry],
    by_scenario: list[ReplayBreakdownEntry],
    by_execution_policy_profile: list[ReplayBreakdownEntry],
) -> ReplayParameterRecommendation:
    sample_size = recent_summary.closed_trades
    best_entry_mode = _best_bucket(by_entry_mode)
    best_scenario = _best_bucket(by_scenario)
    best_policy = _best_bucket(by_execution_policy_profile)
    recommendation_basis = {
        "entry_mode": best_entry_mode.key if best_entry_mode is not None else "UNSPECIFIED",
        "scenario": best_scenario.key if best_scenario is not None else "UNSPECIFIED",
        "execution_policy_profile": best_policy.key if best_policy is not None else "UNSPECIFIED",
    }
    if sample_size < UNDERPERFORMING_BUCKET_MIN_SAMPLE:
        recommendation = ReplayParameterRecommendation(
            status="insufficient_data",
            logic_variant=logic_variant,  # type: ignore[arg-type]
            sample_size=sample_size,
            recommendation_basis=recommendation_basis,
            rationale=["INSUFFICIENT_SAMPLE_SIZE"],
        )
        adaptive_patch, risk_patch = _recommendation_context_patch(recommendation)
        recommendation.adaptive_signal_context_patch = adaptive_patch
        recommendation.risk_context_patch = risk_patch
        return recommendation

    entry_mode_preference = best_entry_mode.key if best_entry_mode is not None else None
    if entry_mode_preference not in {"breakout_confirm", "pullback_confirm", "immediate", "none"}:
        entry_mode_preference = None

    strength = 0
    if recent_summary.expectancy > 0:
        strength += 1
    if recent_summary.net_pnl_after_fees > 0:
        strength += 1
    if recent_summary.average_mfe_pct > max(recent_summary.average_mae_pct * 1.2, 0.0001):
        strength += 1
    if recent_summary.tp_hit_rate >= recent_summary.stop_hit_rate:
        strength += 1

    risk_pct_multiplier = 1.0
    leverage_multiplier = 1.0
    max_chase_bps = 4.0
    partial_tp_rr = 1.5
    partial_tp_size_pct = 0.25
    time_stop_minutes = max(int(round(recent_summary.average_hold_time_minutes or 120.0)), 30)
    trailing_aggressiveness: str = "balanced"
    rationale: list[str] = []
    disable_candidate = False

    if recent_summary.expectancy < 0 or recent_summary.net_pnl_after_fees < 0:
        risk_pct_multiplier = 0.7
        leverage_multiplier = 0.8
        max_chase_bps = 2.0
        partial_tp_rr = 1.2
        partial_tp_size_pct = 0.35
        time_stop_minutes = max(int(round((recent_summary.average_hold_time_minutes or 90.0) * 0.8)), 20)
        trailing_aggressiveness = "defensive"
        disable_candidate = True
        rationale.append("NEGATIVE_EXPECTANCY_REDUCTION")
    elif strength >= 3:
        risk_pct_multiplier = 1.1
        leverage_multiplier = 1.05
        max_chase_bps = 6.0 if entry_mode_preference == "breakout_confirm" else 4.0
        if recent_summary.runner_contribution > recent_summary.partial_tp_contribution:
            partial_tp_rr = 1.8
            partial_tp_size_pct = 0.2
            trailing_aggressiveness = "patient"
            rationale.append("RUNNER_CONTRIBUTION_DOMINANT")
        else:
            partial_tp_rr = 1.4
            partial_tp_size_pct = 0.25
            trailing_aggressiveness = "balanced"
            rationale.append("POSITIVE_EXPECTANCY")
        time_stop_minutes = max(int(round((recent_summary.average_hold_time_minutes or 120.0) * 1.15)), 45)
    else:
        if recent_summary.partial_tp_contribution >= recent_summary.runner_contribution and recent_summary.closed_trades > 0:
            partial_tp_rr = 1.3
            partial_tp_size_pct = 0.3
            trailing_aggressiveness = "defensive"
            rationale.append("PARTIAL_TP_DOMINANT")
        else:
            rationale.append("NEUTRAL_BALANCED_PROFILE")

    recommendation = ReplayParameterRecommendation(
        status="ready",
        logic_variant=logic_variant,  # type: ignore[arg-type]
        sample_size=sample_size,
        recommendation_basis=recommendation_basis,
        risk_pct_multiplier=round(risk_pct_multiplier, 4),
        leverage_multiplier=round(leverage_multiplier, 4),
        max_chase_bps=round(max_chase_bps, 4),
        entry_mode_preference=entry_mode_preference,  # type: ignore[arg-type]
        partial_tp_rr=round(partial_tp_rr, 4),
        partial_tp_size_pct=round(partial_tp_size_pct, 4),
        time_stop_minutes=time_stop_minutes,
        trailing_aggressiveness=trailing_aggressiveness,  # type: ignore[arg-type]
        disable_candidate=disable_candidate,
        rationale=rationale,
    )
    adaptive_patch, risk_patch = _recommendation_context_patch(recommendation)
    recommendation.adaptive_signal_context_patch = adaptive_patch
    recommendation.risk_context_patch = risk_patch
    return recommendation


def _collect_underperforming_buckets(
    *,
    by_symbol: list[ReplayBreakdownEntry],
    by_timeframe: list[ReplayBreakdownEntry],
    by_scenario: list[ReplayBreakdownEntry],
    by_regime: list[ReplayBreakdownEntry],
    by_trend_alignment: list[ReplayBreakdownEntry],
    by_execution_policy_profile: list[ReplayBreakdownEntry],
    by_entry_mode: list[ReplayBreakdownEntry],
) -> list[ReplayUnderperformingBucket]:
    items: list[ReplayUnderperformingBucket] = []
    for bucket_type, entries in (
        ("symbol", by_symbol),
        ("timeframe", by_timeframe),
        ("scenario", by_scenario),
        ("regime", by_regime),
        ("trend_alignment", by_trend_alignment),
        ("execution_policy_profile", by_execution_policy_profile),
        ("entry_mode", by_entry_mode),
    ):
        for entry in entries:
            reasons = _underperforming_bucket_reasons(entry)
            if not reasons:
                continue
            items.append(
                ReplayUnderperformingBucket(
                    bucket_type=bucket_type,
                    key=entry.key,
                    sample_size=_bucket_sample_size(entry),
                    expectancy=entry.expectancy,
                    net_pnl_after_fees=entry.net_pnl_after_fees,
                    average_hold_time_minutes=entry.average_hold_time_minutes,
                    average_mfe_pct=entry.average_mfe_pct,
                    average_mae_pct=entry.average_mae_pct,
                    disable_candidate=True,
                    reasons=reasons,
                )
            )
    return sorted(
        items,
        key=lambda item: (len(item.reasons), item.expectancy, item.net_pnl_after_fees),
    )


def _summarize_metrics(
    accumulator: ReplayAccumulator,
    *,
    equity_points: list[float] | None = None,
) -> ReplayMetricSummary:
    wins = [value for value in accumulator.trade_nets if value > 0]
    losses = [value for value in accumulator.trade_nets if value < 0]
    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
    win_rate = (len(wins) / accumulator.closed_trades) if accumulator.closed_trades else 0.0
    loss_rate = (len(losses) / accumulator.closed_trades) if accumulator.closed_trades else 0.0
    expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)
    equity_series = equity_points if equity_points else []
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    if equity_series:
        for equity in equity_series:
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
    else:
        for value in accumulator.trade_nets:
            cumulative += value
            peak = max(peak, cumulative)
            max_drawdown = max(max_drawdown, peak - cumulative)
    return ReplayMetricSummary(
        decisions=accumulator.decisions,
        closed_trades=accumulator.closed_trades,
        gross_pnl=round(accumulator.gross_pnl, 8),
        net_pnl=round(accumulator.net_pnl, 8),
        net_pnl_after_fees=round(accumulator.net_pnl, 8),
        fees=round(accumulator.fees, 8),
        max_drawdown=round(max_drawdown, 8),
        win_rate=round(win_rate, 6),
        avg_win=round(avg_win, 8),
        avg_loss=round(avg_loss, 8),
        expectancy=round(expectancy, 8),
        profit_factor=round((sum(wins) / abs(sum(losses))) if losses else (sum(wins) if wins else 0.0), 6),
        hold_ratio=round((accumulator.held / accumulator.decisions) if accumulator.decisions else 0.0, 6),
        blocked_ratio=round((accumulator.blocked / accumulator.decisions) if accumulator.decisions else 0.0, 6),
        average_arrival_slippage_pct=round(
            (sum(accumulator.arrival_slippage_values) / len(accumulator.arrival_slippage_values))
            if accumulator.arrival_slippage_values
            else 0.0,
            8,
        ),
        average_realized_slippage_pct=round(
            (sum(accumulator.realized_slippage_values) / len(accumulator.realized_slippage_values))
            if accumulator.realized_slippage_values
            else 0.0,
            8,
        ),
        average_first_fill_latency_seconds=round(
            (sum(accumulator.first_fill_latency_values) / len(accumulator.first_fill_latency_values))
            if accumulator.first_fill_latency_values
            else 0.0,
            8,
        ),
        average_hold_time_minutes=round(
            (sum(accumulator.holding_minutes_values) / len(accumulator.holding_minutes_values))
            if accumulator.holding_minutes_values
            else 0.0,
            8,
        ),
        cancel_attempts=accumulator.cancel_attempts,
        cancel_successes=accumulator.cancel_successes,
        cancel_success_rate=round(
            (accumulator.cancel_successes / accumulator.cancel_attempts)
            if accumulator.cancel_attempts
            else 0.0,
            8,
        ),
        stop_hit_rate=round(
            (accumulator.stop_hits / accumulator.closed_trades) if accumulator.closed_trades else 0.0,
            8,
        ),
        tp_hit_rate=round(
            (accumulator.tp_hits / accumulator.closed_trades) if accumulator.closed_trades else 0.0,
            8,
        ),
        partial_tp_contribution=round(accumulator.partial_tp_contribution_total, 8),
        runner_contribution=round(accumulator.runner_contribution_total, 8),
        average_mfe_pct=round(
            (sum(accumulator.mfe_pct_values) / len(accumulator.mfe_pct_values))
            if accumulator.mfe_pct_values
            else 0.0,
            8,
        ),
        average_mae_pct=round(
            (sum(accumulator.mae_pct_values) / len(accumulator.mae_pct_values))
            if accumulator.mae_pct_values
            else 0.0,
            8,
        ),
        best_mfe_pct=round(max(accumulator.mfe_pct_values) if accumulator.mfe_pct_values else 0.0, 8),
        worst_mae_pct=round(max(accumulator.mae_pct_values) if accumulator.mae_pct_values else 0.0, 8),
    )


def _build_breakdown(
    variant_state: ReplayVariantState,
    *,
    key_fn_decision,
    key_fn_trade,
) -> list[ReplayBreakdownEntry]:
    def _group_keys(raw_value: object) -> list[str]:
        if isinstance(raw_value, list):
            keys = [str(item) for item in raw_value if item not in {None, ""}]
            return keys or ["UNSPECIFIED"]
        if raw_value in {None, ""}:
            return ["UNSPECIFIED"]
        return [str(raw_value)]

    grouped: dict[str, ReplayAccumulator] = defaultdict(ReplayAccumulator)
    for record in variant_state.decisions:
        for key in _group_keys(key_fn_decision(record)):
            _accumulate_decision(grouped[key], record)
    for trade in variant_state.trades:
        for key in _group_keys(key_fn_trade(trade)):
            _accumulate_trade(grouped[key], trade)
    items: list[ReplayBreakdownEntry] = []
    for key, bucket in grouped.items():
        summary = _summarize_metrics(bucket)
        items.append(
            ReplayBreakdownEntry(
                key=key,
                decisions=summary.decisions,
                closed_trades=summary.closed_trades,
                gross_pnl=summary.gross_pnl,
                net_pnl=summary.net_pnl,
                net_pnl_after_fees=summary.net_pnl_after_fees,
                fees=summary.fees,
                max_drawdown=summary.max_drawdown,
                win_rate=summary.win_rate,
                avg_win=summary.avg_win,
                avg_loss=summary.avg_loss,
                expectancy=summary.expectancy,
                profit_factor=summary.profit_factor,
                hold_ratio=summary.hold_ratio,
                blocked_ratio=summary.blocked_ratio,
                average_arrival_slippage_pct=summary.average_arrival_slippage_pct,
                average_realized_slippage_pct=summary.average_realized_slippage_pct,
                average_first_fill_latency_seconds=summary.average_first_fill_latency_seconds,
                average_hold_time_minutes=summary.average_hold_time_minutes,
                cancel_attempts=summary.cancel_attempts,
                cancel_successes=summary.cancel_successes,
                cancel_success_rate=summary.cancel_success_rate,
                stop_hit_rate=summary.stop_hit_rate,
                tp_hit_rate=summary.tp_hit_rate,
                partial_tp_contribution=summary.partial_tp_contribution,
                runner_contribution=summary.runner_contribution,
                average_mfe_pct=summary.average_mfe_pct,
                average_mae_pct=summary.average_mae_pct,
                best_mfe_pct=summary.best_mfe_pct,
                worst_mae_pct=summary.worst_mae_pct,
            )
    )
    return sorted(items, key=lambda item: (item.net_pnl, item.key), reverse=True)


def _metric_summary_from_breakdown(item: ReplayBreakdownEntry) -> ReplayMetricSummary:
    return ReplayMetricSummary(
        decisions=item.decisions,
        closed_trades=item.closed_trades,
        gross_pnl=item.gross_pnl,
        net_pnl=item.net_pnl,
        net_pnl_after_fees=item.net_pnl_after_fees,
        fees=item.fees,
        max_drawdown=item.max_drawdown,
        win_rate=item.win_rate,
        avg_win=item.avg_win,
        avg_loss=item.avg_loss,
        expectancy=item.expectancy,
        profit_factor=item.profit_factor,
        hold_ratio=item.hold_ratio,
        blocked_ratio=item.blocked_ratio,
        average_arrival_slippage_pct=item.average_arrival_slippage_pct,
        average_realized_slippage_pct=item.average_realized_slippage_pct,
        average_first_fill_latency_seconds=item.average_first_fill_latency_seconds,
        average_hold_time_minutes=item.average_hold_time_minutes,
        cancel_attempts=item.cancel_attempts,
        cancel_successes=item.cancel_successes,
        cancel_success_rate=item.cancel_success_rate,
        stop_hit_rate=item.stop_hit_rate,
        tp_hit_rate=item.tp_hit_rate,
        partial_tp_contribution=item.partial_tp_contribution,
        runner_contribution=item.runner_contribution,
        average_mfe_pct=item.average_mfe_pct,
        average_mae_pct=item.average_mae_pct,
        best_mfe_pct=item.best_mfe_pct,
        worst_mae_pct=item.worst_mae_pct,
    )


def _comparison_entries(
    baseline_entries: list[ReplayBreakdownEntry],
    improved_entries: list[ReplayBreakdownEntry],
) -> list[ReplayComparisonEntry]:
    baseline_map = {item.key: item for item in baseline_entries}
    improved_map = {item.key: item for item in improved_entries}
    keys = sorted(set(baseline_map) | set(improved_map))
    comparisons: list[ReplayComparisonEntry] = []
    zero = ReplayMetricSummary(decisions=0, closed_trades=0)
    for key in keys:
        baseline_item = baseline_map.get(key)
        improved_item = improved_map.get(key)
        baseline_summary = _metric_summary_from_breakdown(baseline_item) if baseline_item is not None else zero
        improved_summary = _metric_summary_from_breakdown(improved_item) if improved_item is not None else zero
        comparisons.append(
            ReplayComparisonEntry(
                key=key,
                baseline_old=baseline_summary,
                improved=improved_summary,
                net_pnl_delta=round(improved_summary.net_pnl - baseline_summary.net_pnl, 8),
                gross_pnl_delta=round(improved_summary.gross_pnl - baseline_summary.gross_pnl, 8),
                fees_delta=round(improved_summary.fees - baseline_summary.fees, 8),
                max_drawdown_delta=round(improved_summary.max_drawdown - baseline_summary.max_drawdown, 8),
                win_rate_delta=round(improved_summary.win_rate - baseline_summary.win_rate, 8),
                profit_factor_delta=round(improved_summary.profit_factor - baseline_summary.profit_factor, 8),
                hold_ratio_delta=round(improved_summary.hold_ratio - baseline_summary.hold_ratio, 8),
                blocked_ratio_delta=round(improved_summary.blocked_ratio - baseline_summary.blocked_ratio, 8),
            )
        )
    return comparisons


def _run_variant(
    source_settings: Setting,
    request: ReplayValidationRequest,
    *,
    logic_variant: str,
) -> ReplayVariantReport:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    variant_state = ReplayVariantState()
    symbols = [symbol.upper() for symbol in (request.symbols or source_settings.tracked_symbols or [source_settings.default_symbol])]
    timeframe = request.timeframe
    series_map, _data_source_basis = _load_replay_series(
        request,
        source_settings=source_settings,
        symbols=symbols,
        timeframe=timeframe,
    )

    with SessionLocal() as session:
        settings_row = _copy_settings(
            source_settings,
            session,
            timeframe=timeframe,
            symbols=symbols,
            logic_variant=logic_variant,
        )
        create_exchange_pnl_snapshot(session, settings_row)
        orchestrator = TradingOrchestrator(session)
        for offset in range(request.cycles):
            candle_index = request.start_index + offset
            for symbol in symbols:
                series = series_map[(symbol, timeframe)]
                if candle_index >= len(series):
                    continue
                _advance_open_positions(
                    session,
                    symbol=symbol,
                    candle=series[candle_index],
                    cycle_index=offset,
                    variant_state=variant_state,
                )
                create_exchange_pnl_snapshot(session, settings_row)
                replay_snapshot = _snapshot_from_series(
                    symbol=symbol,
                    timeframe=timeframe,
                    series=series,
                    candle_index=candle_index,
                )
                result = orchestrator.run_decision_cycle(
                    symbol=symbol,
                    timeframe=timeframe,
                    trigger_event="historical_replay",
                    upto_index=candle_index,
                    logic_variant=logic_variant,
                    market_snapshot_override=replay_snapshot,
                    market_context_override={timeframe: replay_snapshot},
                )
                decision = TradeDecision.model_validate(result["decision"])
                risk_result = RiskCheckResult.model_validate(result["risk_result"])
                regime, trend_alignment = _extract_decision_context(session, int(result["decision_run_id"]))
                scenario = _decision_scenario(decision)
                execution_policy_profile = "UNSPECIFIED"
                if decision.decision in {"long", "short", "reduce", "exit"} and decision.stop_loss is not None:
                    try:
                        intent = build_execution_intent(
                            decision,
                            replay_snapshot,
                            risk_result,
                            settings_row,
                            get_latest_pnl_snapshot(session, settings_row).equity,
                            existing_position=_open_position(session, symbol),
                            operating_state=risk_result.operating_state,
                        )
                        execution_policy_profile = select_execution_plan(intent, replay_snapshot, settings_row).policy_profile
                    except Exception:
                        execution_policy_profile = "UNSPECIFIED"
                variant_state.decisions.append(
                    ReplayDecisionRecord(
                        symbol=symbol,
                        timeframe=timeframe,
                        regime=regime,
                        trend_alignment=trend_alignment,
                        scenario=scenario,
                        entry_mode=_normalize_entry_mode(decision.entry_mode),
                        execution_policy_profile=execution_policy_profile,
                        cycle_index=offset,
                        decision_time=replay_snapshot.snapshot_time,
                        rationale_codes=list(decision.rationale_codes),
                        blocked=not risk_result.allowed,
                        held=decision.decision == "hold",
                    )
                )
                next_candle = series[candle_index + 1] if candle_index + 1 < len(series) else None
                if not risk_result.allowed or decision.decision == "hold" or next_candle is None:
                    _record_equity_point(session, settings_row, variant_state)
                    continue

                decision_row = _extract_decision_row(session, int(result["decision_run_id"]))
                raw_market_snapshot = (
                    (decision_row.input_payload if isinstance(decision_row.input_payload, dict) else {}).get("market_snapshot")
                )
                market_snapshot = MarketSnapshotPayload.model_validate_json(json.dumps(raw_market_snapshot))
                risk_row = session.scalar(select(RiskCheck).where(RiskCheck.id == int(result["risk_check_id"])))
                existing_position = _open_position(session, symbol)

                if decision.decision in {"long", "short"}:
                    if existing_position is not None and existing_position.side != decision.decision:
                        variant_state.trades.append(
                            _close_position_at_price(
                                session,
                                position=existing_position,
                                quantity=existing_position.quantity,
                                price=float(next_candle.open),
                                order_type="MARKET",
                                decision_run_id=int(result["decision_run_id"]),
                                risk_check_id=risk_row.id if risk_row is not None else None,
                                reason_codes=["REPLAY_FLIP_CLOSE"],
                            )
                        )
                        existing_position = None
                    if existing_position is None:
                        _apply_entry(
                            session,
                            settings_row,
                            market_snapshot=market_snapshot,
                            decision=decision,
                            risk_result=risk_result,
                            decision_run_id=int(result["decision_run_id"]),
                            risk_check_id=risk_row.id if risk_row is not None else None,
                            next_candle=next_candle,
                            regime=regime,
                            trend_alignment=trend_alignment,
                            logic_variant=logic_variant,
                        )
                elif decision.decision in {"reduce", "exit"} and existing_position is not None:
                    _apply_reduce_or_exit(
                        session,
                        position=existing_position,
                        decision=decision,
                        decision_run_id=int(result["decision_run_id"]),
                        risk_check_id=risk_row.id if risk_row is not None else None,
                        next_candle=next_candle,
                        cycle_index=offset,
                        variant_state=variant_state,
                    )
                _record_equity_point(session, settings_row, variant_state)
        end_index = request.start_index + request.cycles - 1
        for symbol in symbols:
            series = series_map[(symbol, timeframe)]
            last_candle_index = min(end_index, len(series) - 1)
            if last_candle_index < 0:
                continue
            last_candle = series[last_candle_index]
            for position in _open_positions(session, symbol):
                variant_state.trades.append(
                    _close_position_at_price(
                        session,
                        position=position,
                        quantity=position.quantity,
                        price=float(last_candle.close),
                        order_type="MARKET",
                        decision_run_id=None,
                        risk_check_id=None,
                        reason_codes=["REPLAY_WINDOW_END_CLOSE"],
                        closed_at=last_candle.timestamp,
                        cycle_index=request.cycles,
                    )
                )
        _record_equity_point(session, settings_row, variant_state)
        session.commit()

    overall = _accumulator_from_variant_state(variant_state)
    recent_state = _recent_variant_state(variant_state, cycles=request.cycles)
    recent_summary = _summarize_metrics(_accumulator_from_variant_state(recent_state))
    by_symbol = _build_breakdown(
        variant_state,
        key_fn_decision=lambda item: item.symbol,
        key_fn_trade=lambda item: item.symbol,
    )
    by_timeframe = _build_breakdown(
        variant_state,
        key_fn_decision=lambda item: item.timeframe,
        key_fn_trade=lambda item: item.timeframe,
    )
    by_scenario = _build_breakdown(
        variant_state,
        key_fn_decision=lambda item: item.scenario,
        key_fn_trade=lambda item: item.scenario,
    )
    by_regime = _build_breakdown(
        variant_state,
        key_fn_decision=lambda item: item.regime,
        key_fn_trade=lambda item: item.regime,
    )
    by_trend_alignment = _build_breakdown(
        variant_state,
        key_fn_decision=lambda item: item.trend_alignment,
        key_fn_trade=lambda item: item.trend_alignment,
    )
    by_execution_policy_profile = _build_breakdown(
        variant_state,
        key_fn_decision=lambda item: item.execution_policy_profile,
        key_fn_trade=lambda item: item.execution_policy_profile,
    )
    by_entry_mode = _build_breakdown(
        variant_state,
        key_fn_decision=lambda item: item.entry_mode,
        key_fn_trade=lambda item: item.entry_mode,
    )
    by_rationale_code = _build_breakdown(
        variant_state,
        key_fn_decision=lambda item: item.rationale_codes,
        key_fn_trade=lambda item: item.rationale_codes,
    )
    recommendation = _build_parameter_recommendation(
        logic_variant=logic_variant,
        recent_summary=recent_summary,
        by_entry_mode=by_entry_mode,
        by_scenario=by_scenario,
        by_execution_policy_profile=by_execution_policy_profile,
    )
    underperforming_buckets = _collect_underperforming_buckets(
        by_symbol=by_symbol,
        by_timeframe=by_timeframe,
        by_scenario=by_scenario,
        by_regime=by_regime,
        by_trend_alignment=by_trend_alignment,
        by_execution_policy_profile=by_execution_policy_profile,
        by_entry_mode=by_entry_mode,
    )

    return ReplayVariantReport(
        logic_variant=logic_variant,  # type: ignore[arg-type]
        title="Baseline Old Logic" if logic_variant == "baseline_old" else "Improved Logic",
        data_source_type=request.data_source_type,
        summary=_summarize_metrics(overall, equity_points=variant_state.equity_points),
        recent_window_summary=recent_summary,
        by_symbol=by_symbol,
        by_timeframe=by_timeframe,
        by_scenario=by_scenario,
        by_regime=by_regime,
        by_trend_alignment=by_trend_alignment,
        by_execution_policy_profile=by_execution_policy_profile,
        by_entry_mode=by_entry_mode,
        by_rationale_code=by_rationale_code,
        walk_forward_recommendation=recommendation,
        underperforming_buckets=underperforming_buckets,
    )


def build_replay_validation_report(
    session: Session,
    request: ReplayValidationRequest,
) -> ReplayValidationResponse:
    settings_row = session.scalar(select(Setting).order_by(Setting.id.asc()).limit(1))
    if settings_row is None:
        settings_row = Setting()
        session.add(settings_row)
        session.flush()
    symbols = [symbol.upper() for symbol in (request.symbols or settings_row.tracked_symbols or [settings_row.default_symbol])]
    _, data_source_basis = _load_replay_series(
        request,
        source_settings=settings_row,
        symbols=symbols,
        timeframe=request.timeframe,
    )
    variants = [_run_variant(settings_row, request, logic_variant=variant) for variant in LOGIC_VARIANTS]
    baseline = next(item for item in variants if item.logic_variant == "baseline_old")
    improved = next(item for item in variants if item.logic_variant == "improved")
    end_index = request.start_index + request.cycles - 1
    preferred_variant = max(
        variants,
        key=lambda item: (
            item.recent_window_summary.expectancy,
            item.recent_window_summary.net_pnl_after_fees,
            item.recent_window_summary.win_rate,
        ),
    )
    return ReplayValidationResponse(
        generated_at=utcnow_naive(),
        data_source_type=request.data_source_type,
        data_source_basis=data_source_basis,
        execution_basis="next_bar_open_entry_with_intrabar_stop_take_profit_and_synthetic_fees",
        live_execution_guarantee="Historical replay uses an isolated in-memory session and never submits live orders.",
        start_index=request.start_index,
        end_index=end_index,
        cycles=request.cycles,
        timeframe=request.timeframe,
        symbols=symbols,
        variants=variants,
        symbol_comparison=_comparison_entries(baseline.by_symbol, improved.by_symbol),
        timeframe_comparison=_comparison_entries(baseline.by_timeframe, improved.by_timeframe),
        scenario_comparison=_comparison_entries(baseline.by_scenario, improved.by_scenario),
        regime_comparison=_comparison_entries(baseline.by_regime, improved.by_regime),
        trend_alignment_comparison=_comparison_entries(baseline.by_trend_alignment, improved.by_trend_alignment),
        execution_policy_profile_comparison=_comparison_entries(
            baseline.by_execution_policy_profile,
            improved.by_execution_policy_profile,
        ),
        entry_mode_comparison=_comparison_entries(baseline.by_entry_mode, improved.by_entry_mode),
        rationale_comparison=_comparison_entries(baseline.by_rationale_code, improved.by_rationale_code),
        recent_walk_forward_recommendation=preferred_variant.walk_forward_recommendation,
        underperforming_buckets=preferred_variant.underperforming_buckets,
    )
