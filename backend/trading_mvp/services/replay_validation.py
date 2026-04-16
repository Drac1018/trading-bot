from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import json
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from trading_mvp.database import Base
from trading_mvp.models import AgentRun, Execution, Order, Position, RiskCheck, Setting
from trading_mvp.schemas import (
    MarketSnapshotPayload,
    ReplayBreakdownEntry,
    ReplayComparisonEntry,
    ReplayMetricSummary,
    ReplayValidationRequest,
    ReplayValidationResponse,
    ReplayVariantReport,
    RiskCheckResult,
    TradeDecision,
)
from trading_mvp.services.account import calculate_unrealized_pnl, create_exchange_pnl_snapshot, get_latest_pnl_snapshot
from trading_mvp.services.binance import BinanceClient
from trading_mvp.services.execution import _reduce_fraction_for_decision, build_execution_intent
from trading_mvp.services.market_data import generate_seed_candles
from trading_mvp.services.orchestrator import TradingOrchestrator
from trading_mvp.services.position_management import mark_partial_take_profit_taken, seed_position_management_metadata
from trading_mvp.time_utils import utcnow_naive

REPLAY_FEE_RATE = 0.0004
LOGIC_VARIANTS = ("baseline_old", "improved")


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
    blocked: bool
    held: bool
    rationale_codes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ReplayTradeRecord:
    symbol: str
    timeframe: str
    regime: str
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
    cancel_attempts: int = 0
    cancel_successes: int = 0
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
def _extract_regime_from_run(session: Session, decision_run_id: int) -> str:
    row = session.scalar(select(AgentRun).where(AgentRun.id == decision_run_id))
    if row is None:
        return "unknown"
    input_payload = row.input_payload if isinstance(row.input_payload, dict) else {}
    features = input_payload.get("features") if isinstance(input_payload.get("features"), dict) else {}
    regime = features.get("regime") if isinstance(features.get("regime"), dict) else {}
    value = regime.get("primary_regime")
    return str(value or "unknown")


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
    position.realized_pnl += gross_pnl
    if remaining_quantity <= 1e-9:
        position.quantity = 0.0
        position.status = "closed"
        position.closed_at = utcnow_naive()
        position.mark_price = price
        position.unrealized_pnl = 0.0
    else:
        position.quantity = remaining_quantity
        position.mark_price = price
        position.unrealized_pnl = calculate_unrealized_pnl(position, price)
    session.add(position)
    session.flush()
    return ReplayTradeRecord(
        symbol=position.symbol,
        timeframe=str(replay_metadata.get("entry_timeframe") or "unknown"),
        regime=str(replay_metadata.get("entry_regime") or "unknown"),
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
    )


def _advance_open_positions(
    session: Session,
    *,
    symbol: str,
    candle,
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
                "entry_timeframe": decision.timeframe,
                "logic_variant": logic_variant,
                "entry_decision_run_id": decision_run_id,
                "entry_rationale_codes": list(decision.rationale_codes),
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
    variant_state.equity_points.append(round(float(settings_row.starting_equity) + net_realized + unrealized, 8))


def _summarize_metrics(
    accumulator: ReplayAccumulator,
    *,
    equity_points: list[float] | None = None,
) -> ReplayMetricSummary:
    wins = [value for value in accumulator.trade_nets if value > 0]
    losses = [value for value in accumulator.trade_nets if value < 0]
    equity_series = equity_points if equity_points else []
    cumulative = 0.0
    peak = equity_series[0] if equity_series else 0.0
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
        fees=round(accumulator.fees, 8),
        max_drawdown=round(max_drawdown, 8),
        win_rate=round((len(wins) / accumulator.closed_trades) if accumulator.closed_trades else 0.0, 6),
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
        cancel_attempts=accumulator.cancel_attempts,
        cancel_successes=accumulator.cancel_successes,
        cancel_success_rate=round(
            (accumulator.cancel_successes / accumulator.cancel_attempts)
            if accumulator.cancel_attempts
            else 0.0,
            8,
        ),
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
            bucket = grouped[key]
            bucket.decisions += 1
            bucket.blocked += int(record.blocked)
            bucket.held += int(record.held)
    for trade in variant_state.trades:
        for key in _group_keys(key_fn_trade(trade)):
            bucket = grouped[key]
            bucket.closed_trades += 1
            bucket.gross_pnl += trade.gross_pnl
            bucket.net_pnl += trade.net_pnl
            bucket.fees += trade.fees
            bucket.arrival_slippage_values.append(trade.arrival_slippage_pct)
            bucket.realized_slippage_values.append(trade.realized_slippage_pct)
            bucket.first_fill_latency_values.append(trade.first_fill_latency_seconds)
            bucket.cancel_attempts += int(trade.cancel_attempted)
            bucket.cancel_successes += int(trade.cancel_succeeded)
            bucket.trade_nets.append(trade.net_pnl)
            bucket.mfe_pct_values.append(trade.mfe_pct)
            bucket.mae_pct_values.append(trade.mae_pct)
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
                fees=summary.fees,
                max_drawdown=summary.max_drawdown,
                win_rate=summary.win_rate,
                profit_factor=summary.profit_factor,
                hold_ratio=summary.hold_ratio,
                blocked_ratio=summary.blocked_ratio,
                average_arrival_slippage_pct=summary.average_arrival_slippage_pct,
                average_realized_slippage_pct=summary.average_realized_slippage_pct,
                average_first_fill_latency_seconds=summary.average_first_fill_latency_seconds,
                cancel_attempts=summary.cancel_attempts,
                cancel_successes=summary.cancel_successes,
                cancel_success_rate=summary.cancel_success_rate,
                average_mfe_pct=summary.average_mfe_pct,
                average_mae_pct=summary.average_mae_pct,
                best_mfe_pct=summary.best_mfe_pct,
                worst_mae_pct=summary.worst_mae_pct,
            )
        )
    return sorted(items, key=lambda item: (item.net_pnl, item.key), reverse=True)


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
        baseline_summary = (
            ReplayMetricSummary(
                decisions=baseline_item.decisions,
                closed_trades=baseline_item.closed_trades,
                gross_pnl=baseline_item.gross_pnl,
                net_pnl=baseline_item.net_pnl,
                fees=baseline_item.fees,
                max_drawdown=baseline_item.max_drawdown,
                win_rate=baseline_item.win_rate,
                profit_factor=baseline_item.profit_factor,
                hold_ratio=baseline_item.hold_ratio,
                blocked_ratio=baseline_item.blocked_ratio,
                average_arrival_slippage_pct=baseline_item.average_arrival_slippage_pct,
                average_realized_slippage_pct=baseline_item.average_realized_slippage_pct,
                average_first_fill_latency_seconds=baseline_item.average_first_fill_latency_seconds,
                cancel_attempts=baseline_item.cancel_attempts,
                cancel_successes=baseline_item.cancel_successes,
                cancel_success_rate=baseline_item.cancel_success_rate,
                average_mfe_pct=baseline_item.average_mfe_pct,
                average_mae_pct=baseline_item.average_mae_pct,
                best_mfe_pct=baseline_item.best_mfe_pct,
                worst_mae_pct=baseline_item.worst_mae_pct,
            )
            if baseline_item is not None
            else zero
        )
        improved_summary = (
            ReplayMetricSummary(
                decisions=improved_item.decisions,
                closed_trades=improved_item.closed_trades,
                gross_pnl=improved_item.gross_pnl,
                net_pnl=improved_item.net_pnl,
                fees=improved_item.fees,
                max_drawdown=improved_item.max_drawdown,
                win_rate=improved_item.win_rate,
                profit_factor=improved_item.profit_factor,
                hold_ratio=improved_item.hold_ratio,
                blocked_ratio=improved_item.blocked_ratio,
                average_arrival_slippage_pct=improved_item.average_arrival_slippage_pct,
                average_realized_slippage_pct=improved_item.average_realized_slippage_pct,
                average_first_fill_latency_seconds=improved_item.average_first_fill_latency_seconds,
                cancel_attempts=improved_item.cancel_attempts,
                cancel_successes=improved_item.cancel_successes,
                cancel_success_rate=improved_item.cancel_success_rate,
                average_mfe_pct=improved_item.average_mfe_pct,
                average_mae_pct=improved_item.average_mae_pct,
                best_mfe_pct=improved_item.best_mfe_pct,
                worst_mae_pct=improved_item.worst_mae_pct,
            )
            if improved_item is not None
            else zero
        )
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
                regime = _extract_regime_from_run(session, int(result["decision_run_id"]))
                variant_state.decisions.append(
                    ReplayDecisionRecord(
                        symbol=symbol,
                        timeframe=timeframe,
                        regime=regime,
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
                    )
                )
        _record_equity_point(session, settings_row, variant_state)
        session.commit()

    overall = ReplayAccumulator()
    overall.decisions = len(variant_state.decisions)
    overall.closed_trades = len(variant_state.trades)
    overall.blocked = sum(int(item.blocked) for item in variant_state.decisions)
    overall.held = sum(int(item.held) for item in variant_state.decisions)
    overall.gross_pnl = sum(item.gross_pnl for item in variant_state.trades)
    overall.net_pnl = sum(item.net_pnl for item in variant_state.trades)
    overall.fees = sum(item.fees for item in variant_state.trades)
    overall.arrival_slippage_values = [item.arrival_slippage_pct for item in variant_state.trades]
    overall.realized_slippage_values = [item.realized_slippage_pct for item in variant_state.trades]
    overall.first_fill_latency_values = [item.first_fill_latency_seconds for item in variant_state.trades]
    overall.cancel_attempts = sum(int(item.cancel_attempted) for item in variant_state.trades)
    overall.cancel_successes = sum(int(item.cancel_succeeded) for item in variant_state.trades)
    overall.trade_nets = [item.net_pnl for item in variant_state.trades]
    overall.mfe_pct_values = [item.mfe_pct for item in variant_state.trades]
    overall.mae_pct_values = [item.mae_pct for item in variant_state.trades]

    return ReplayVariantReport(
        logic_variant=logic_variant,  # type: ignore[arg-type]
        title="Baseline Old Logic" if logic_variant == "baseline_old" else "Improved Logic",
        data_source_type=request.data_source_type,
        summary=_summarize_metrics(overall, equity_points=variant_state.equity_points),
        by_symbol=_build_breakdown(
            variant_state,
            key_fn_decision=lambda item: item.symbol,
            key_fn_trade=lambda item: item.symbol,
        ),
        by_timeframe=_build_breakdown(
            variant_state,
            key_fn_decision=lambda item: item.timeframe,
            key_fn_trade=lambda item: item.timeframe,
        ),
        by_regime=_build_breakdown(
            variant_state,
            key_fn_decision=lambda item: item.regime,
            key_fn_trade=lambda item: item.regime,
        ),
        by_rationale_code=_build_breakdown(
            variant_state,
            key_fn_decision=lambda item: item.rationale_codes,
            key_fn_trade=lambda item: item.rationale_codes,
        ),
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
        regime_comparison=_comparison_entries(baseline.by_regime, improved.by_regime),
        rationale_comparison=_comparison_entries(baseline.by_rationale_code, improved.by_rationale_code),
    )
