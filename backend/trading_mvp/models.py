from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from trading_mvp.database import Base
from trading_mvp.time_utils import utcnow_naive


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=utcnow_naive, onupdate=utcnow_naive
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(50), default="admin")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Setting(TimestampMixin, Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    live_trading_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    rollout_mode: Mapped[str] = mapped_column(String(20), default="paper")
    limited_live_max_notional: Mapped[float] = mapped_column(Float, default=500.0)
    manual_live_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    live_execution_armed: Mapped[bool] = mapped_column(Boolean, default=False)
    live_execution_armed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    live_approval_window_minutes: Mapped[int] = mapped_column(Integer, default=0)
    trading_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    pause_reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    pause_origin: Mapped[str | None] = mapped_column(String(30), nullable=True)
    pause_reason_detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    pause_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    auto_resume_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    default_symbol: Mapped[str] = mapped_column(String(30), default="BTCUSDT")
    tracked_symbols: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["BTCUSDT"])
    default_timeframe: Mapped[str] = mapped_column(String(20), default="15m")
    exchange_sync_interval_seconds: Mapped[int] = mapped_column(Integer, default=60)
    market_refresh_interval_minutes: Mapped[int] = mapped_column(Integer, default=1)
    position_management_interval_seconds: Mapped[int] = mapped_column(Integer, default=60)
    schedule_windows: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["1h", "4h", "12h", "24h"])
    symbol_cadence_overrides: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    max_leverage: Mapped[float] = mapped_column(Float, default=5.0)
    max_risk_per_trade: Mapped[float] = mapped_column(Float, default=0.02)
    max_daily_loss: Mapped[float] = mapped_column(Float, default=0.05)
    max_consecutive_losses: Mapped[int] = mapped_column(Integer, default=3)
    max_gross_exposure_pct: Mapped[float] = mapped_column(Float, default=3.0)
    max_largest_position_pct: Mapped[float] = mapped_column(Float, default=1.5)
    max_directional_bias_pct: Mapped[float] = mapped_column(Float, default=2.0)
    max_same_tier_concentration_pct: Mapped[float] = mapped_column(Float, default=2.5)
    stale_market_seconds: Mapped[int] = mapped_column(Integer, default=1800)
    slippage_threshold_pct: Mapped[float] = mapped_column(Float, default=0.003)
    adaptive_signal_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    position_management_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    break_even_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    atr_trailing_stop_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    partial_take_profit_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    partial_tp_rr: Mapped[float] = mapped_column(Float, default=1.5)
    partial_tp_size_pct: Mapped[float] = mapped_column(Float, default=0.25)
    move_stop_to_be_rr: Mapped[float] = mapped_column(Float, default=1.0)
    time_stop_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    time_stop_minutes: Mapped[int] = mapped_column(Integer, default=120)
    time_stop_profit_floor: Mapped[float] = mapped_column(Float, default=0.15)
    holding_edge_decay_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    reduce_on_regime_shift_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    starting_equity: Mapped[float] = mapped_column(Float, default=100000.0)
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_provider: Mapped[str] = mapped_column(String(20), default="openai")
    ai_model: Mapped[str] = mapped_column(String(80), default="gpt-4.1-mini")
    ai_call_interval_minutes: Mapped[int] = mapped_column(Integer, default=30)
    decision_cycle_interval_minutes: Mapped[int] = mapped_column(Integer, default=15)
    ai_max_input_candles: Mapped[int] = mapped_column(Integer, default=32)
    ai_temperature: Mapped[float] = mapped_column(Float, default=0.1)
    openai_api_key_encrypted: Mapped[str] = mapped_column(Text, default="")
    binance_market_data_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    binance_testnet_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    binance_futures_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    binance_api_key_encrypted: Mapped[str] = mapped_column(Text, default="")
    binance_api_secret_encrypted: Mapped[str] = mapped_column(Text, default="")


class MarketSnapshot(TimestampMixin, Base):
    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(30), index=True)
    timeframe: Mapped[str] = mapped_column(String(20))
    snapshot_time: Mapped[datetime] = mapped_column(DateTime(timezone=False), index=True)
    latest_price: Mapped[float] = mapped_column(Float)
    latest_volume: Mapped[float] = mapped_column(Float)
    candle_count: Mapped[int] = mapped_column(Integer)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)
    is_complete: Mapped[bool] = mapped_column(Boolean, default=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class FeatureSnapshot(TimestampMixin, Base):
    __tablename__ = "feature_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(30), index=True)
    timeframe: Mapped[str] = mapped_column(String(20))
    market_snapshot_id: Mapped[int] = mapped_column(Integer, index=True)
    feature_time: Mapped[datetime] = mapped_column(DateTime(timezone=False), index=True)
    trend_score: Mapped[float] = mapped_column(Float)
    volatility_pct: Mapped[float] = mapped_column(Float)
    volume_ratio: Mapped[float] = mapped_column(Float)
    drawdown_pct: Mapped[float] = mapped_column(Float)
    rsi: Mapped[float] = mapped_column(Float)
    atr: Mapped[float] = mapped_column(Float)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class AgentRun(TimestampMixin, Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role: Mapped[str] = mapped_column(String(50), index=True)
    trigger_event: Mapped[str] = mapped_column(String(50), index=True)
    schema_name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(40), default="completed")
    provider_name: Mapped[str] = mapped_column(String(50), default="deterministic-mock")
    summary: Mapped[str] = mapped_column(Text, default="")
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    output_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    schema_valid: Mapped[bool] = mapped_column(Boolean, default=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=utcnow_naive)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=utcnow_naive)


class RiskCheck(TimestampMixin, Base):
    __tablename__ = "risk_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(30), index=True)
    decision_run_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    market_snapshot_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    decision: Mapped[str] = mapped_column(String(30))
    reason_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    approved_risk_pct: Mapped[float] = mapped_column(Float, default=0.0)
    approved_leverage: Mapped[float] = mapped_column(Float, default=0.0)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class PendingEntryPlan(TimestampMixin, Base):
    __tablename__ = "pending_entry_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(30), index=True)
    side: Mapped[str] = mapped_column(String(10), index=True)
    plan_status: Mapped[str] = mapped_column(String(20), default="armed", index=True)
    source_decision_run_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    regime: Mapped[str | None] = mapped_column(String(40), nullable=True)
    posture: Mapped[str | None] = mapped_column(String(80), nullable=True)
    rationale_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_timeframe: Mapped[str | None] = mapped_column(String(20), nullable=True)
    entry_mode: Mapped[str] = mapped_column(String(30), default="pullback_confirm")
    entry_zone_min: Mapped[float] = mapped_column(Float)
    entry_zone_max: Mapped[float] = mapped_column(Float)
    invalidation_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_chase_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    idea_ttl_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_pct_cap: Mapped[float] = mapped_column(Float, default=0.0)
    leverage_cap: Mapped[float] = mapped_column(Float, default=0.0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), index=True)
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    canceled_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Position(TimestampMixin, Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(30), index=True)
    mode: Mapped[str] = mapped_column(String(20), default="live", index=True)
    side: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    quantity: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    mark_price: Mapped[float] = mapped_column(Float)
    leverage: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    take_profit: Mapped[float] = mapped_column(Float)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=utcnow_naive)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Order(TimestampMixin, Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(30), index=True)
    decision_run_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    risk_check_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    position_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    side: Mapped[str] = mapped_column(String(20))
    order_type: Mapped[str] = mapped_column(String(20), default="market")
    mode: Mapped[str] = mapped_column(String(20), default="live")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    external_order_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    client_order_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    reduce_only: Mapped[bool] = mapped_column(Boolean, default=False)
    close_only: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_order_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    exchange_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_exchange_update_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    requested_quantity: Mapped[float] = mapped_column(Float)
    requested_price: Mapped[float] = mapped_column(Float)
    filled_quantity: Mapped[float] = mapped_column(Float, default=0.0)
    average_fill_price: Mapped[float] = mapped_column(Float, default=0.0)
    reason_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Execution(TimestampMixin, Base):
    __tablename__ = "executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    position_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    symbol: Mapped[str] = mapped_column(String(30), index=True)
    status: Mapped[str] = mapped_column(String(20), default="filled")
    external_trade_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    fill_price: Mapped[float] = mapped_column(Float)
    fill_quantity: Mapped[float] = mapped_column(Float)
    fee_paid: Mapped[float] = mapped_column(Float, default=0.0)
    commission_asset: Mapped[str | None] = mapped_column(String(20), nullable=True)
    slippage_pct: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class PnLSnapshot(TimestampMixin, Base):
    __tablename__ = "pnl_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date, index=True)
    equity: Mapped[float] = mapped_column(Float)
    cash_balance: Mapped[float] = mapped_column(Float)
    wallet_balance: Mapped[float] = mapped_column(Float, default=0.0)
    available_balance: Mapped[float] = mapped_column(Float, default=0.0)
    gross_realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    fee_total: Mapped[float] = mapped_column(Float, default=0.0)
    funding_total: Mapped[float] = mapped_column(Float, default=0.0)
    net_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    daily_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    cumulative_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    consecutive_losses: Mapped[int] = mapped_column(Integer, default=0)


class AccountLedgerEntry(TimestampMixin, Base):
    __tablename__ = "account_ledger_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_type: Mapped[str] = mapped_column(String(30), index=True, default="funding")
    asset: Mapped[str] = mapped_column(String(20), default="USDT")
    symbol: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    external_ref_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), index=True, default=utcnow_naive)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Alert(TimestampMixin, Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(String(50), index=True)
    severity: Mapped[str] = mapped_column(String(20), default="info")
    title: Mapped[str] = mapped_column(String(160))
    message: Mapped[str] = mapped_column(Text)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class SchedulerRun(TimestampMixin, Base):
    __tablename__ = "scheduler_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    schedule_window: Mapped[str] = mapped_column(String(20), index=True)
    workflow: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="running")
    triggered_by: Mapped[str] = mapped_column(String(40), default="system")
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    outcome: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class CompetitorNote(TimestampMixin, Base):
    __tablename__ = "competitor_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(120))
    note: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)


class UIFeedback(TimestampMixin, Base):
    __tablename__ = "ui_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_name: Mapped[str] = mapped_column(String(120))
    page: Mapped[str] = mapped_column(String(80), index=True)
    sentiment: Mapped[str] = mapped_column(String(20))
    feedback: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class SystemHealthEvent(TimestampMixin, Base):
    __tablename__ = "system_health_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    component: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AuditEvent(TimestampMixin, Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str] = mapped_column(String(80))
    severity: Mapped[str] = mapped_column(String(20), default="info")
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
