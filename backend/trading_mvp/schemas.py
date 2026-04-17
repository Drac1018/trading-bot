from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RolloutMode = Literal["shadow", "live_dry_run", "limited_live", "full_live"]


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TradeDecision(StrictBaseModel):
    decision: Literal["hold", "long", "short", "reduce", "exit"]
    confidence: float = Field(ge=0.0, le=1.0)
    symbol: str = Field(min_length=1, max_length=30)
    timeframe: str = Field(min_length=1, max_length=20)
    entry_zone_min: float | None = None
    entry_zone_max: float | None = None
    entry_mode: Literal["breakout_confirm", "pullback_confirm", "immediate", "none"] | None = None
    invalidation_price: float | None = Field(default=None, gt=0.0)
    max_chase_bps: float | None = Field(default=None, ge=0.0, le=500.0)
    idea_ttl_minutes: int | None = Field(default=None, ge=1, le=1440)
    stop_loss: float | None = None
    take_profit: float | None = None
    max_holding_minutes: int = Field(ge=1, le=10080)
    risk_pct: float = Field(gt=0.0, le=1.0)
    leverage: float = Field(gt=0.0, le=10.0)
    rationale_codes: list[str]
    explanation_short: str = Field(min_length=3, max_length=240)
    explanation_detailed: str = Field(min_length=10, max_length=600)


class TradeDecisionCandidateScore(StrictBaseModel):
    regime_fit: float = 0.0
    expected_rr: float = 0.0
    recent_signal_performance: float = 0.0
    slippage_sensitivity: float = 0.0
    exposure_impact: float = 0.0
    confidence_consistency: float = 0.0
    correlation_penalty: float = 0.0
    total_score: float = 0.0


class TradeDecisionCandidate(StrictBaseModel):
    candidate_id: str = Field(min_length=1, max_length=80)
    scenario: Literal[
        "hold",
        "trend_follow",
        "pullback_entry",
        "reduce",
        "exit",
        "protection_restore",
    ]
    decision: Literal["hold", "long", "short", "reduce", "exit"]
    symbol: str = Field(min_length=1, max_length=30)
    timeframe: str = Field(min_length=1, max_length=20)
    confidence: float = Field(ge=0.0, le=1.0)
    entry_zone_min: float | None = None
    entry_zone_max: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    max_holding_minutes: int = Field(ge=1, le=10080)
    risk_pct: float = Field(ge=0.0, le=1.0)
    leverage: float = Field(ge=0.0, le=10.0)
    rationale_codes: list[str] = Field(default_factory=list)
    explanation_short: str = Field(min_length=3, max_length=240)
    explanation_detailed: str = Field(min_length=10, max_length=600)


class TradeDecisionCandidateBatch(StrictBaseModel):
    items: list[TradeDecisionCandidate] = Field(default_factory=list)


class ChiefReviewSummary(StrictBaseModel):
    summary: str
    recommended_mode: Literal["hold", "monitor", "act"]
    must_do_actions: list[str]
    blockers: list[str]
    priority: Literal["low", "medium", "high", "critical"]


class IntegrationSuggestion(StrictBaseModel):
    title: str
    integration_point: str
    description: str
    automation_opportunity: str
    tech_debt_item: str
    priority: Literal["low", "medium", "high", "critical"]


class IntegrationSuggestionBatch(StrictBaseModel):
    items: list[IntegrationSuggestion]


class UXSuggestion(StrictBaseModel):
    page: str
    title: str
    suggestion: str
    severity: Literal["low", "medium", "high", "critical"]
    improved_copy: str


class UXSuggestionBatch(StrictBaseModel):
    items: list[UXSuggestion]


class SignalPerformanceEntry(StrictBaseModel):
    rationale_code: str
    decisions: int = Field(ge=0)
    approvals: int = Field(ge=0)
    orders: int = Field(ge=0)
    fills: int = Field(ge=0)
    holds: int = Field(ge=0)
    longs: int = Field(ge=0)
    shorts: int = Field(ge=0)
    reduces: int = Field(ge=0)
    exits: int = Field(ge=0)
    wins: int = Field(ge=0)
    losses: int = Field(ge=0)
    realized_pnl_total: float
    fee_total: float = 0.0
    net_realized_pnl_total: float = 0.0
    average_slippage_pct: float = Field(ge=0.0)
    average_arrival_slippage_pct: float = Field(ge=0.0, default=0.0)
    average_realized_slippage_pct: float = Field(ge=0.0, default=0.0)
    average_first_fill_latency_seconds: float = Field(ge=0.0, default=0.0)
    cancel_attempts: int = Field(ge=0, default=0)
    cancel_successes: int = Field(ge=0, default=0)
    cancel_success_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    average_holding_minutes: float = Field(ge=0.0)
    holding_over_plan_count: int = Field(ge=0)
    open_positions: int = Field(ge=0)
    closed_positions: int = Field(ge=0)
    latest_seen_at: datetime


class PerformanceAggregateEntry(StrictBaseModel):
    key: str
    decisions: int = Field(ge=0)
    approvals: int = Field(ge=0)
    orders: int = Field(ge=0)
    fills: int = Field(ge=0)
    holds: int = Field(ge=0)
    longs: int = Field(ge=0)
    shorts: int = Field(ge=0)
    reduces: int = Field(ge=0)
    exits: int = Field(ge=0)
    wins: int = Field(ge=0)
    losses: int = Field(ge=0)
    realized_pnl_total: float = 0.0
    fee_total: float = 0.0
    net_realized_pnl_total: float = 0.0
    average_slippage_pct: float = Field(ge=0.0)
    average_arrival_slippage_pct: float = Field(ge=0.0, default=0.0)
    average_realized_slippage_pct: float = Field(ge=0.0, default=0.0)
    average_first_fill_latency_seconds: float = Field(ge=0.0, default=0.0)
    cancel_attempts: int = Field(ge=0, default=0)
    cancel_successes: int = Field(ge=0, default=0)
    cancel_success_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    average_holding_minutes: float = Field(ge=0.0)
    holding_over_plan_count: int = Field(ge=0)
    open_positions: int = Field(ge=0)
    closed_positions: int = Field(ge=0)
    stop_loss_closes: int = Field(ge=0)
    take_profit_closes: int = Field(ge=0)
    manual_closes: int = Field(ge=0)
    unclassified_closes: int = Field(ge=0)
    latest_seen_at: datetime


class FeatureFlagPerformanceEntry(StrictBaseModel):
    flag_name: str
    enabled: PerformanceAggregateEntry
    disabled: PerformanceAggregateEntry


class DecisionPerformanceEntry(StrictBaseModel):
    decision_run_id: int
    created_at: datetime
    symbol: str
    timeframe: str
    decision: str
    regime: str = "unknown"
    trend_alignment: str = "unknown"
    weak_volume: bool = False
    volatility_expanded: bool = False
    momentum_weakening: bool = False
    rationale_codes: list[str] = Field(default_factory=list)
    approved: bool = False
    approved_risk_pct: float = 0.0
    approved_leverage: float = 0.0
    orders: int = Field(ge=0)
    fills: int = Field(ge=0)
    wins: int = Field(ge=0)
    losses: int = Field(ge=0)
    realized_pnl_total: float = 0.0
    fee_total: float = 0.0
    net_realized_pnl_total: float = 0.0
    average_slippage_pct: float = Field(ge=0.0)
    arrival_slippage_pct: float = Field(ge=0.0, default=0.0)
    realized_slippage_pct: float = Field(ge=0.0, default=0.0)
    first_fill_latency_seconds: float = Field(ge=0.0, default=0.0)
    cancel_attempts: int = Field(ge=0, default=0)
    cancel_successes: int = Field(ge=0, default=0)
    cancel_success_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    max_holding_minutes_planned: int | None = None
    holding_minutes_observed: float = Field(ge=0.0)
    holding_result_status: str = "unlinked"
    stop_loss: float | None = None
    take_profit: float | None = None
    planned_risk_reward_ratio: float | None = None
    close_outcome: str = "not_closed"
    position_ids: list[int] = Field(default_factory=list)
    fill_basis: str = "execution_ledger_truth"
    mfe_pct: float | None = None
    mae_pct: float | None = None
    mfe_pnl: float | None = None
    mae_pnl: float | None = None
    mfe_mae_tracking_status: str = "calculated"
    mfe_mae_tracking_basis: str = "position_window_market_path"


class PerformanceWindowSummary(StrictBaseModel):
    decisions: int = Field(ge=0)
    approvals: int = Field(ge=0)
    orders: int = Field(ge=0)
    fills: int = Field(ge=0)
    holds: int = Field(ge=0)
    longs: int = Field(ge=0)
    shorts: int = Field(ge=0)
    reduces: int = Field(ge=0)
    exits: int = Field(ge=0)
    wins: int = Field(ge=0)
    losses: int = Field(ge=0)
    realized_pnl_total: float = 0.0
    fee_total: float = 0.0
    net_realized_pnl_total: float = 0.0
    average_slippage_pct: float = Field(ge=0.0)
    average_arrival_slippage_pct: float = Field(ge=0.0, default=0.0)
    average_realized_slippage_pct: float = Field(ge=0.0, default=0.0)
    average_first_fill_latency_seconds: float = Field(ge=0.0, default=0.0)
    cancel_attempts: int = Field(ge=0, default=0)
    cancel_successes: int = Field(ge=0, default=0)
    cancel_success_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    average_holding_minutes: float = Field(ge=0.0)
    holding_over_plan_count: int = Field(ge=0)
    open_positions: int = Field(ge=0)
    closed_positions: int = Field(ge=0)
    stop_loss_closes: int = Field(ge=0)
    take_profit_closes: int = Field(ge=0)
    manual_closes: int = Field(ge=0)
    unclassified_closes: int = Field(ge=0)
    execution_pnl_basis: str = "execution_ledger_truth"
    snapshot_pnl_basis: str = "pnl_snapshot_delta_estimate"
    snapshot_net_pnl_estimate: float = 0.0
    decision_context_basis: str = "agent_run_input_features_regime"
    stop_take_profit_efficiency_basis: str = "decision_template_vs_close_order_type"
    average_mfe_pct: float = 0.0
    average_mae_pct: float = 0.0
    best_mfe_pct: float = 0.0
    worst_mae_pct: float = 0.0
    mfe_mae_tracking_status: str = "calculated"
    mfe_mae_tracking_note: str = "MFE/MAE is derived from linked position market path highs and lows."


class PerformanceWindowReport(StrictBaseModel):
    window_label: str
    window_hours: int = Field(ge=1, le=24 * 30)
    summary: PerformanceWindowSummary
    decisions: list[DecisionPerformanceEntry] = Field(default_factory=list)
    rationale_codes: list[PerformanceAggregateEntry] = Field(default_factory=list)
    symbols: list[PerformanceAggregateEntry] = Field(default_factory=list)
    timeframes: list[PerformanceAggregateEntry] = Field(default_factory=list)
    regimes: list[PerformanceAggregateEntry] = Field(default_factory=list)
    trend_alignments: list[PerformanceAggregateEntry] = Field(default_factory=list)
    directions: list[PerformanceAggregateEntry] = Field(default_factory=list)
    hold_conditions: list[PerformanceAggregateEntry] = Field(default_factory=list)
    close_outcomes: list[PerformanceAggregateEntry] = Field(default_factory=list)
    feature_flags: list[FeatureFlagPerformanceEntry] = Field(default_factory=list)


class SignalPerformanceReportResponse(StrictBaseModel):
    generated_at: datetime
    window_hours: int = Field(ge=1, le=168)
    items: list[SignalPerformanceEntry] = Field(default_factory=list)
    windows: list[PerformanceWindowReport] = Field(default_factory=list)


class ReplayValidationRequest(StrictBaseModel):
    cycles: int = Field(default=30, ge=5, le=500)
    start_index: int = Field(default=90, ge=20, le=2000)
    timeframe: str = Field(default="15m", min_length=1, max_length=20)
    symbols: list[str] = Field(default_factory=list)
    data_source_type: Literal["synthetic_seed", "binance_futures_klines"] = "synthetic_seed"


class ReplayMetricSummary(StrictBaseModel):
    decisions: int = Field(ge=0)
    closed_trades: int = Field(ge=0)
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    fees: float = 0.0
    max_drawdown: float = Field(ge=0.0, default=0.0)
    win_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    profit_factor: float = Field(ge=0.0, default=0.0)
    hold_ratio: float = Field(ge=0.0, le=1.0, default=0.0)
    blocked_ratio: float = Field(ge=0.0, le=1.0, default=0.0)
    average_arrival_slippage_pct: float = Field(ge=0.0, default=0.0)
    average_realized_slippage_pct: float = Field(ge=0.0, default=0.0)
    average_first_fill_latency_seconds: float = Field(ge=0.0, default=0.0)
    cancel_attempts: int = Field(ge=0, default=0)
    cancel_successes: int = Field(ge=0, default=0)
    cancel_success_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    average_mfe_pct: float = 0.0
    average_mae_pct: float = 0.0
    best_mfe_pct: float = 0.0
    worst_mae_pct: float = 0.0


class ReplayBreakdownEntry(StrictBaseModel):
    key: str
    decisions: int = Field(ge=0)
    closed_trades: int = Field(ge=0)
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    fees: float = 0.0
    max_drawdown: float = Field(ge=0.0, default=0.0)
    win_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    profit_factor: float = Field(ge=0.0, default=0.0)
    hold_ratio: float = Field(ge=0.0, le=1.0, default=0.0)
    blocked_ratio: float = Field(ge=0.0, le=1.0, default=0.0)
    average_arrival_slippage_pct: float = Field(ge=0.0, default=0.0)
    average_realized_slippage_pct: float = Field(ge=0.0, default=0.0)
    average_first_fill_latency_seconds: float = Field(ge=0.0, default=0.0)
    cancel_attempts: int = Field(ge=0, default=0)
    cancel_successes: int = Field(ge=0, default=0)
    cancel_success_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    average_mfe_pct: float = 0.0
    average_mae_pct: float = 0.0
    best_mfe_pct: float = 0.0
    worst_mae_pct: float = 0.0


class ReplayVariantReport(StrictBaseModel):
    logic_variant: Literal["baseline_old", "improved"]
    title: str
    data_source_type: Literal["synthetic_seed", "binance_futures_klines"]
    summary: ReplayMetricSummary
    by_symbol: list[ReplayBreakdownEntry] = Field(default_factory=list)
    by_timeframe: list[ReplayBreakdownEntry] = Field(default_factory=list)
    by_regime: list[ReplayBreakdownEntry] = Field(default_factory=list)
    by_rationale_code: list[ReplayBreakdownEntry] = Field(default_factory=list)


class ReplayComparisonEntry(StrictBaseModel):
    key: str
    baseline_old: ReplayMetricSummary
    improved: ReplayMetricSummary
    net_pnl_delta: float = 0.0
    gross_pnl_delta: float = 0.0
    fees_delta: float = 0.0
    max_drawdown_delta: float = 0.0
    win_rate_delta: float = 0.0
    profit_factor_delta: float = 0.0
    hold_ratio_delta: float = 0.0
    blocked_ratio_delta: float = 0.0


class ReplayValidationResponse(StrictBaseModel):
    generated_at: datetime
    data_source_type: Literal["synthetic_seed", "binance_futures_klines"]
    data_source_basis: str
    execution_basis: str
    live_execution_guarantee: str
    start_index: int = Field(ge=0)
    end_index: int = Field(ge=0)
    cycles: int = Field(ge=1)
    timeframe: str
    symbols: list[str] = Field(default_factory=list)
    variants: list[ReplayVariantReport] = Field(default_factory=list)
    symbol_comparison: list[ReplayComparisonEntry] = Field(default_factory=list)
    timeframe_comparison: list[ReplayComparisonEntry] = Field(default_factory=list)
    regime_comparison: list[ReplayComparisonEntry] = Field(default_factory=list)
    rationale_comparison: list[ReplayComparisonEntry] = Field(default_factory=list)


class DashboardExecutionProfileSummary(StrictBaseModel):
    policy_profile: str
    symbol: str | None = None
    timeframe: str | None = None
    orders: int = Field(ge=0)
    partial_fill_orders: int = Field(ge=0)
    aggressive_fallback_orders: int = Field(ge=0)
    cancel_attempts: int = Field(ge=0, default=0)
    cancel_successes: int = Field(ge=0, default=0)
    cancel_success_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    average_arrival_slippage_pct: float = Field(ge=0.0, default=0.0)
    average_realized_slippage_pct: float = Field(ge=0.0, default=0.0)
    average_first_fill_latency_seconds: float = Field(ge=0.0, default=0.0)


class DashboardExecutionWindowSummary(StrictBaseModel):
    window: str
    decision_quality_summary: dict[str, int] = Field(default_factory=dict)
    execution_quality_summary: dict[str, float | int] = Field(default_factory=dict)
    worst_profiles: list[DashboardExecutionProfileSummary] = Field(default_factory=list)


class DashboardProfitabilityWindow(StrictBaseModel):
    window_label: str
    window_hours: int = Field(ge=1, le=24 * 30)
    summary: PerformanceWindowSummary
    rationale_winners: list[PerformanceAggregateEntry] = Field(default_factory=list)
    rationale_losers: list[PerformanceAggregateEntry] = Field(default_factory=list)
    top_regimes: list[PerformanceAggregateEntry] = Field(default_factory=list)
    top_symbols: list[PerformanceAggregateEntry] = Field(default_factory=list)
    top_timeframes: list[PerformanceAggregateEntry] = Field(default_factory=list)
    top_hold_conditions: list[PerformanceAggregateEntry] = Field(default_factory=list)


class DashboardHoldBlockedSummary(StrictBaseModel):
    hold_top_conditions: list[PerformanceAggregateEntry] = Field(default_factory=list)
    latest_blocked_reasons: list[str] = Field(default_factory=list)
    auto_resume_blockers: list[str] = Field(default_factory=list)
    guard_mode_reason_code: str | None = None
    guard_mode_reason_message: str | None = None


class DashboardProfitabilityResponse(StrictBaseModel):
    generated_at: datetime
    operating_state: str
    guard_mode_reason_code: str | None = None
    guard_mode_reason_message: str | None = None
    adaptive_signal_summary: dict[str, Any] = Field(default_factory=dict)
    latest_decision: dict[str, Any] | None = None
    latest_risk: dict[str, Any] | None = None
    windows: list[DashboardProfitabilityWindow] = Field(default_factory=list)
    execution_windows: list[DashboardExecutionWindowSummary] = Field(default_factory=list)
    hold_blocked_summary: DashboardHoldBlockedSummary


class ControlStatusSummary(StrictBaseModel):
    exchange_can_trade: bool | None = None
    rollout_mode: RolloutMode = "shadow"
    exchange_submit_allowed: bool = False
    limited_live_max_notional: float | None = None
    app_live_armed: bool = False
    approval_window_open: bool = False
    approval_state: str | None = None
    approval_detail: dict[str, Any] = Field(default_factory=dict)
    paused: bool = False
    degraded: bool = False
    risk_allowed: bool | None = None
    blocked_reasons_current_cycle: list[str] = Field(default_factory=list)


class OperationalStatusPayload(StrictBaseModel):
    live_trading_enabled: bool = False
    rollout_mode: RolloutMode = "shadow"
    exchange_submit_allowed: bool = False
    limited_live_max_notional: float | None = None
    live_trading_env_enabled: bool = False
    live_execution_ready: bool = False
    trading_paused: bool = False
    approval_armed: bool = False
    approval_expires_at: datetime | None = None
    approval_window_minutes: int = 0
    operating_state: str = "TRADABLE"
    guard_mode_reason_category: str | None = None
    guard_mode_reason_code: str | None = None
    guard_mode_reason_message: str | None = None
    pause_reason_code: str | None = None
    pause_origin: str | None = None
    pause_triggered_at: datetime | None = None
    auto_resume_after: datetime | None = None
    auto_resume_status: str = "not_paused"
    auto_resume_eligible: bool = False
    auto_resume_last_blockers: list[str] = Field(default_factory=list)
    pause_severity: str | None = None
    pause_recovery_class: str | None = None
    blocked_reasons: list[str] = Field(default_factory=list)
    latest_blocked_reasons: list[str] = Field(default_factory=list)
    account_sync_summary: dict[str, Any] = Field(default_factory=dict)
    sync_freshness_summary: dict[str, Any] = Field(default_factory=dict)
    market_freshness_summary: dict[str, Any] = Field(default_factory=dict)
    protection_recovery_status: str = "idle"
    protection_recovery_active: bool = False
    protection_recovery_failure_count: int = 0
    missing_protection_symbols: list[str] = Field(default_factory=list)
    missing_protection_items: dict[str, list[str]] = Field(default_factory=dict)
    control_status_summary: ControlStatusSummary = Field(default_factory=ControlStatusSummary)
    user_stream_summary: dict[str, Any] = Field(default_factory=dict)
    reconciliation_summary: dict[str, Any] = Field(default_factory=dict)
    candidate_selection_summary: dict[str, Any] = Field(default_factory=dict)
    can_enter_new_position: bool = False


class DecisionReferencePayload(StrictBaseModel):
    market_snapshot_id: int | None = None
    market_snapshot_at: datetime | None = None
    market_snapshot_source: str | None = None
    market_snapshot_stale: bool = False
    market_snapshot_incomplete: bool = False
    account_sync_at: datetime | None = None
    positions_sync_at: datetime | None = None
    open_orders_sync_at: datetime | None = None
    protective_orders_sync_at: datetime | None = None
    account_sync_status: str | None = None
    sync_freshness_summary: dict[str, Any] = Field(default_factory=dict)
    market_freshness_summary: dict[str, Any] = Field(default_factory=dict)
    freshness_blocking: bool = False
    display_gap: bool = False
    display_gap_reason: str | None = None


class OperatorControlState(StrictBaseModel):
    generated_at: datetime
    operational_status: OperationalStatusPayload
    control_status_summary: ControlStatusSummary | None = None
    can_enter_new_position: bool = False
    mode: str
    rollout_mode: RolloutMode = "shadow"
    exchange_submit_allowed: bool = False
    limited_live_max_notional: float | None = None
    default_symbol: str
    default_timeframe: str
    tracked_symbols: list[str] = Field(default_factory=list)
    tracked_symbol_count: int = 0
    live_trading_enabled: bool = False
    live_execution_ready: bool = False
    approval_armed: bool = False
    approval_expires_at: datetime | None = None
    trading_paused: bool = False
    operating_state: str = "TRADABLE"
    guard_mode_reason_category: str | None = None
    guard_mode_reason_code: str | None = None
    guard_mode_reason_message: str | None = None
    pause_reason_code: str | None = None
    pause_origin: str | None = None
    pause_triggered_at: datetime | None = None
    auto_resume_status: str = "not_paused"
    auto_resume_eligible: bool = False
    auto_resume_after: datetime | None = None
    blocked_reasons: list[str] = Field(default_factory=list)
    auto_resume_last_blockers: list[str] = Field(default_factory=list)
    latest_blocked_reasons: list[str] = Field(default_factory=list)
    market_freshness_summary: dict[str, Any] = Field(default_factory=dict)
    sync_freshness_summary: dict[str, Any] = Field(default_factory=dict)
    protection_recovery_status: str = "idle"
    protected_positions: int = 0
    unprotected_positions: int = 0
    open_positions: int = 0
    pnl_summary: dict[str, Any] = Field(default_factory=dict)
    daily_pnl: float = 0.0
    cumulative_pnl: float = 0.0
    account_sync_summary: dict[str, Any] = Field(default_factory=dict)
    exposure_summary: dict[str, Any] = Field(default_factory=dict)
    scheduler_status: str | None = None
    scheduler_window: str | None = None
    scheduler_triggered_by: str | None = None
    scheduler_last_run_at: datetime | None = None
    scheduler_next_run_at: datetime | None = None
    last_market_refresh_at: datetime | None = None
    last_decision_at: datetime | None = None
    last_decision_snapshot_at: datetime | None = None
    last_decision_reference: DecisionReferencePayload = Field(default_factory=DecisionReferencePayload)
    user_stream_summary: dict[str, Any] = Field(default_factory=dict)
    reconciliation_summary: dict[str, Any] = Field(default_factory=dict)
    candidate_selection_summary: dict[str, Any] = Field(default_factory=dict)


class OperatorDecisionSnapshot(StrictBaseModel):
    decision_run_id: int | None = None
    created_at: datetime | None = None
    provider_name: str | None = None
    trigger_event: str | None = None
    status: str | None = None
    summary: str | None = None
    symbol: str | None = None
    timeframe: str | None = None
    decision: str | None = None
    confidence: float | None = None
    rationale_codes: list[str] = Field(default_factory=list)
    explanation_short: str | None = None
    decision_reference: DecisionReferencePayload = Field(default_factory=DecisionReferencePayload)
    raw_output: dict[str, Any] = Field(default_factory=dict)


PendingEntryPlanStatus = Literal["armed", "triggered", "expired", "canceled"]


class PendingEntryPlanSnapshot(StrictBaseModel):
    plan_id: int | None = None
    symbol: str | None = None
    side: Literal["long", "short"] | None = None
    plan_status: PendingEntryPlanStatus | None = None
    source_decision_run_id: int | None = None
    source_timeframe: str | None = None
    regime: str | None = None
    posture: str | None = None
    rationale_codes: list[str] = Field(default_factory=list)
    entry_mode: Literal["breakout_confirm", "pullback_confirm", "immediate", "none"] | None = None
    entry_zone_min: float | None = None
    entry_zone_max: float | None = None
    invalidation_price: float | None = None
    max_chase_bps: float | None = None
    idea_ttl_minutes: int | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    risk_pct_cap: float | None = None
    leverage_cap: float | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None
    triggered_at: datetime | None = None
    canceled_at: datetime | None = None
    canceled_reason: str | None = None
    idempotency_key: str | None = None
    trigger_details: dict[str, Any] = Field(default_factory=dict)


class OperatorRiskSnapshot(StrictBaseModel):
    risk_check_id: int | None = None
    decision_run_id: int | None = None
    created_at: datetime | None = None
    snapshot_id: int | None = None
    cycle_id: str | None = None
    as_of: datetime | None = None
    allowed: bool | None = None
    decision: str | None = None
    operating_state: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
    blocked_reason_codes: list[str] = Field(default_factory=list)
    adjustment_reason_codes: list[str] = Field(default_factory=list)
    approved_risk_pct: float | None = None
    approved_leverage: float | None = None
    raw_projected_notional: float | None = None
    approved_projected_notional: float | None = None
    approved_quantity: float | None = None
    auto_resized_entry: bool = False
    size_adjustment_ratio: float | None = None
    auto_resize_reason: str | None = None
    exposure_headroom_snapshot: dict[str, float] = Field(default_factory=dict)
    debug_payload: dict[str, Any] = Field(default_factory=dict)
    current_cycle_result: dict[str, Any] = Field(default_factory=dict)
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class OperatorExecutionSnapshot(StrictBaseModel):
    order_id: int | None = None
    execution_id: int | None = None
    decision_run_id: int | None = None
    created_at: datetime | None = None
    execution_created_at: datetime | None = None
    symbol: str | None = None
    side: str | None = None
    order_type: str | None = None
    order_status: str | None = None
    execution_status: str | None = None
    requested_quantity: float | None = None
    filled_quantity: float | None = None
    average_fill_price: float | None = None
    fill_price: float | None = None
    reason_codes: list[str] = Field(default_factory=list)
    execution_policy: dict[str, Any] = Field(default_factory=dict)
    execution_quality: dict[str, Any] = Field(default_factory=dict)
    decision_summary: dict[str, Any] = Field(default_factory=dict)
    recent_fills: list["OperatorExecutionFillSummary"] = Field(default_factory=list)


class OperatorExecutionFillSummary(StrictBaseModel):
    execution_id: int | None = None
    order_id: int | None = None
    external_trade_id: str | None = None
    created_at: datetime | None = None
    status: str | None = None
    fill_price: float | None = None
    fill_quantity: float | None = None
    fee_paid: float | None = None
    commission_asset: str | None = None
    realized_pnl: float | None = None


class OperatorPositionSummary(StrictBaseModel):
    is_open: bool = False
    position_id: int | None = None
    side: str | None = None
    status: str | None = None
    quantity: float | None = None
    entry_price: float | None = None
    mark_price: float | None = None
    unrealized_pnl: float | None = None
    realized_pnl: float | None = None
    leverage: float | None = None
    opened_at: datetime | None = None


class OperatorProtectionSummary(StrictBaseModel):
    status: str = "unknown"
    protected: bool = False
    protective_order_count: int = 0
    has_stop_loss: bool = False
    has_take_profit: bool = False
    missing_components: list[str] = Field(default_factory=list)
    order_ids: list[int] = Field(default_factory=list)
    recovery_status: str | None = None
    auto_recovery_active: bool = False
    failure_count: int = 0
    last_error: str | None = None
    last_transition_at: datetime | None = None
    trigger_source: str | None = None
    lifecycle_state: str | None = None
    verification_status: str | None = None
    last_event_type: str | None = None
    last_event_message: str | None = None
    last_event_at: datetime | None = None


class OperatorSymbolSummary(StrictBaseModel):
    symbol: str
    timeframe: str | None = None
    latest_price: float | None = None
    market_snapshot_time: datetime | None = None
    market_context_summary: dict[str, Any] = Field(default_factory=dict)
    ai_decision: OperatorDecisionSnapshot = Field(default_factory=OperatorDecisionSnapshot)
    pending_entry_plan: PendingEntryPlanSnapshot = Field(default_factory=PendingEntryPlanSnapshot)
    risk_guard: OperatorRiskSnapshot = Field(default_factory=OperatorRiskSnapshot)
    execution: OperatorExecutionSnapshot = Field(default_factory=OperatorExecutionSnapshot)
    open_position: OperatorPositionSummary = Field(default_factory=OperatorPositionSummary)
    protection_status: OperatorProtectionSummary = Field(default_factory=OperatorProtectionSummary)
    blocked_reasons: list[str] = Field(default_factory=list)
    live_execution_ready: bool = False
    stale_flags: list[str] = Field(default_factory=list)
    last_updated_at: datetime | None = None
    candidate_selection: dict[str, Any] = Field(default_factory=dict)
    audit_events: list[AuditTimelineEntry] = Field(default_factory=list)


class OperatorMarketSignalSummary(StrictBaseModel):
    market_context_summary: dict[str, Any] = Field(default_factory=dict)
    performance_windows: list[DashboardProfitabilityWindow] = Field(default_factory=list)
    hold_blocked_summary: DashboardHoldBlockedSummary
    adaptive_signal_summary: dict[str, Any] = Field(default_factory=dict)


class OperatorDashboardResponse(StrictBaseModel):
    generated_at: datetime
    control: OperatorControlState
    symbols: list[OperatorSymbolSummary] = Field(default_factory=list)
    market_signal: OperatorMarketSignalSummary
    execution_windows: list[DashboardExecutionWindowSummary] = Field(default_factory=list)
    audit_events: list[AuditTimelineEntry] = Field(default_factory=list)


class StructuredCompetitorNote(StrictBaseModel):
    id: int
    source: str
    category: str
    differentiation: str
    summary: str
    tags: list[str] = Field(default_factory=list)
    created_at: datetime


class StructuredCompetitorNotesResponse(StrictBaseModel):
    generated_at: datetime
    category_breakdown: dict[str, int] = Field(default_factory=dict)
    items: list[StructuredCompetitorNote] = Field(default_factory=list)


class RiskReasonDetail(StrictBaseModel):
    code: str = Field(min_length=1, max_length=80)
    blocking: bool = True
    resizable: bool = False
    measured_value: float | None = None
    limit_value: float | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class RiskCheckResult(StrictBaseModel):
    allowed: bool
    decision: Literal["hold", "long", "short", "reduce", "exit"]
    reason_codes: list[str] = Field(description="Blocker-only alias. Same meaning as blocked_reason_codes.")
    blocked_reason_codes: list[str] = Field(
        default_factory=list,
        description="Actual blocking reasons for the current risk evaluation cycle.",
    )
    adjustment_reason_codes: list[str] = Field(
        default_factory=list,
        description="Non-blocking adjustment or approval reasons such as successful auto-resize.",
    )
    reason_details: list[RiskReasonDetail] = Field(default_factory=list)
    approved_risk_pct: float = Field(ge=0.0, le=1.0)
    approved_leverage: float = Field(ge=0.0, le=10.0)
    raw_projected_notional: float = Field(ge=0.0, default=0.0)
    approved_notional: float = Field(ge=0.0, default=0.0)
    approved_projected_notional: float = Field(ge=0.0, default=0.0)
    approved_qty: float | None = Field(default=None, ge=0.0)
    approved_quantity: float | None = Field(default=None, ge=0.0)
    resizable: bool = False
    auto_resized_entry: bool = False
    size_adjustment_ratio: float = Field(ge=0.0, le=1.0, default=0.0)
    measured_value: float | None = None
    limit_value: float | None = None
    snapshot_id: int | None = None
    cycle_id: str | None = None
    as_of: datetime | None = None
    exposure_headroom_snapshot: dict[str, float] = Field(default_factory=dict)
    auto_resize_reason: str | None = None
    operating_mode: Literal["live", "paused", "hold"]
    operating_state: str = "TRADABLE"
    effective_leverage_cap: float = Field(gt=0.0, le=10.0)
    symbol_risk_tier: Literal["btc", "major_alt", "alt"]
    exposure_metrics: dict[str, float] = Field(default_factory=dict)
    sync_freshness_summary: dict[str, Any] = Field(default_factory=dict)
    debug_payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _backfill_compatibility_fields(self) -> "RiskCheckResult":
        if not self.blocked_reason_codes and self.reason_codes:
            self.blocked_reason_codes = list(self.reason_codes)
        if not self.reason_codes and self.blocked_reason_codes:
            self.reason_codes = list(self.blocked_reason_codes)
        if self.approved_qty is None and self.approved_quantity is not None:
            self.approved_qty = self.approved_quantity
        if self.approved_quantity is None and self.approved_qty is not None:
            self.approved_quantity = self.approved_qty
        if self.approved_notional <= 0.0 and self.approved_projected_notional > 0.0:
            self.approved_notional = self.approved_projected_notional
        if self.approved_projected_notional <= 0.0 and self.approved_notional > 0.0:
            self.approved_projected_notional = self.approved_notional
        ordered_reason_codes = list(dict.fromkeys([*self.reason_codes, *self.adjustment_reason_codes]))
        if not self.reason_details and ordered_reason_codes:
            self.reason_details = [RiskReasonDetail(code=code) for code in ordered_reason_codes]
        elif ordered_reason_codes:
            detail_by_code = {detail.code: detail for detail in self.reason_details}
            ordered_details: list[RiskReasonDetail] = []
            for code in ordered_reason_codes:
                ordered_details.append(detail_by_code.pop(code, RiskReasonDetail(code=code)))
            ordered_details.extend(detail_by_code.values())
            self.reason_details = ordered_details
        if not self.resizable and self.auto_resized_entry:
            self.resizable = True
        if self.measured_value is None or self.limit_value is None:
            for detail in self.reason_details:
                if self.measured_value is None and detail.measured_value is not None:
                    self.measured_value = detail.measured_value
                if self.limit_value is None and detail.limit_value is not None:
                    self.limit_value = detail.limit_value
                if self.measured_value is not None and self.limit_value is not None:
                    break
        return self


ProtectionLifecycleState = Literal["none", "requested", "placed", "verified", "verify_failed"]


class ProtectionLifecycleTransition(StrictBaseModel):
    from_state: ProtectionLifecycleState | None = None
    to_state: ProtectionLifecycleState
    transition_reason: str = Field(min_length=1, max_length=80)
    transitioned_at: datetime
    detail: dict[str, Any] = Field(default_factory=dict)


class ProtectionLifecycleSnapshot(StrictBaseModel):
    symbol: str = Field(min_length=1, max_length=30)
    trigger_source: str = Field(min_length=1, max_length=120)
    parent_order_id: int | None = None
    state: ProtectionLifecycleState = "none"
    requested_components: list[str] = Field(default_factory=list)
    requested_order_types: list[str] = Field(default_factory=list)
    created_order_ids: list[int] = Field(default_factory=list)
    verification_detail: dict[str, Any] = Field(default_factory=dict)
    transitions: list[ProtectionLifecycleTransition] = Field(default_factory=list)


class ExecutionIntent(StrictBaseModel):
    symbol: str
    action: Literal["long", "short", "reduce", "exit"]
    intent_type: Literal["entry", "scale_in", "protection", "reduce_only", "emergency_exit"]
    quantity: float = Field(gt=0.0)
    requested_price: float = Field(gt=0.0)
    entry_mode: Literal["breakout_confirm", "pullback_confirm", "immediate", "none"] | None = None
    invalidation_price: float | None = Field(default=None, gt=0.0)
    max_chase_bps: float | None = Field(default=None, ge=0.0, le=500.0)
    idea_ttl_minutes: int | None = Field(default=None, ge=1, le=1440)
    stop_loss: float | None = None
    take_profit: float | None = None
    leverage: float = Field(gt=0.0, le=10.0)
    mode: Literal["live"]
    reduce_only: bool = False
    close_only: bool = False


class AgentRunRecord(StrictBaseModel):
    role: str
    trigger_event: str
    schema_name: str
    status: str
    provider_name: str
    summary: str
    input_payload: dict[str, Any]
    output_payload: dict[str, Any]
    metadata_json: dict[str, Any]
    schema_valid: bool
    started_at: datetime
    completed_at: datetime


class SchedulerRunRecord(StrictBaseModel):
    schedule_window: str
    workflow: str
    status: str
    triggered_by: str
    next_run_at: datetime | None = None
    outcome: dict[str, Any]


class MarketCandle(StrictBaseModel):
    timestamp: datetime
    open: float = Field(gt=0.0)
    high: float = Field(gt=0.0)
    low: float = Field(gt=0.0)
    close: float = Field(gt=0.0)
    volume: float = Field(ge=0.0)


class MarketSnapshotPayload(StrictBaseModel):
    symbol: str
    timeframe: str
    snapshot_time: datetime
    latest_price: float = Field(gt=0.0)
    latest_volume: float = Field(ge=0.0)
    candle_count: int = Field(ge=1)
    is_stale: bool
    is_complete: bool
    candles: list[MarketCandle]


class TimeframeFeatureContext(StrictBaseModel):
    timeframe: str
    trend_score: float
    volatility_pct: float = Field(ge=0.0)
    volume_ratio: float = Field(ge=0.0)
    drawdown_pct: float = Field(ge=0.0)
    rsi: float = Field(ge=0.0, le=100.0)
    atr: float = Field(ge=0.0)
    atr_pct: float = Field(ge=0.0)
    momentum_score: float


class BreakoutFeatureContext(StrictBaseModel):
    lookback_bars: int = Field(ge=1, default=8)
    swing_high: float = Field(ge=0.0, default=0.0)
    swing_low: float = Field(ge=0.0, default=0.0)
    range_high: float = Field(ge=0.0, default=0.0)
    range_low: float = Field(ge=0.0, default=0.0)
    range_width_pct: float = Field(ge=0.0, default=0.0)
    broke_swing_high: bool = False
    broke_swing_low: bool = False
    range_breakout_direction: Literal["up", "down", "none"] = "none"


class CandleStructureFeatureContext(StrictBaseModel):
    body_ratio: float = Field(ge=0.0, le=1.0, default=0.0)
    upper_wick_ratio: float = Field(ge=0.0, le=1.0, default=0.0)
    lower_wick_ratio: float = Field(ge=0.0, le=1.0, default=0.0)
    wick_to_body_ratio: float = Field(ge=0.0, default=0.0)
    bullish_streak: int = Field(ge=0, default=0)
    bearish_streak: int = Field(ge=0, default=0)
    bullish_streak_strength: float = Field(ge=0.0, default=0.0)
    bearish_streak_strength: float = Field(ge=0.0, default=0.0)


class LocationFeatureContext(StrictBaseModel):
    distance_from_recent_high_pct: float = 0.0
    distance_from_recent_low_pct: float = 0.0
    range_position_pct: float = 0.5
    vwap_distance_pct: float = 0.0


class VolumePersistenceFeatureContext(StrictBaseModel):
    recent_window: int = Field(ge=1, default=5)
    persistence_ratio: float = Field(ge=0.0, default=0.0)
    high_volume_bars: int = Field(ge=0, default=0)
    low_volume_bars: int = Field(ge=0, default=0)
    sustained_high_volume: bool = False
    sustained_low_volume: bool = False


class PullbackContinuationFeatureContext(StrictBaseModel):
    higher_timeframe_bias: Literal["bullish", "bearish", "range", "mixed", "unknown"] = "unknown"
    state: Literal[
        "bullish_continuation",
        "bearish_continuation",
        "bullish_pullback",
        "bearish_pullback",
        "countertrend",
        "range",
        "unclear",
    ] = "unclear"
    aligned_with_higher_timeframe: bool = False


class RegimeFeatureContext(StrictBaseModel):
    primary_regime: Literal["bullish", "bearish", "range", "transition"]
    trend_alignment: Literal["bullish_aligned", "bearish_aligned", "mixed", "range"]
    volatility_regime: Literal["compressed", "normal", "expanded"]
    volume_regime: Literal["weak", "normal", "strong"]
    momentum_state: Literal["strengthening", "stable", "weakening", "overextended"]
    weak_volume: bool = False
    momentum_weakening: bool = False


class FeaturePayload(StrictBaseModel):
    symbol: str
    timeframe: str
    trend_score: float
    volatility_pct: float = Field(ge=0.0)
    volume_ratio: float = Field(ge=0.0)
    drawdown_pct: float = Field(ge=0.0)
    rsi: float = Field(ge=0.0, le=100.0)
    atr: float = Field(ge=0.0)
    atr_pct: float = Field(ge=0.0)
    momentum_score: float
    multi_timeframe: dict[str, TimeframeFeatureContext] = Field(default_factory=dict)
    regime: RegimeFeatureContext
    breakout: BreakoutFeatureContext = Field(default_factory=BreakoutFeatureContext)
    candle_structure: CandleStructureFeatureContext = Field(default_factory=CandleStructureFeatureContext)
    location: LocationFeatureContext = Field(default_factory=LocationFeatureContext)
    volume_persistence: VolumePersistenceFeatureContext = Field(default_factory=VolumePersistenceFeatureContext)
    pullback_context: PullbackContinuationFeatureContext = Field(default_factory=PullbackContinuationFeatureContext)
    data_quality_flags: list[str] = Field(default_factory=list)


class OverviewResponse(StrictBaseModel):
    mode: str
    symbol: str
    tracked_symbols: list[str]
    timeframe: str
    latest_price: float
    latest_decision: dict[str, Any] | None
    latest_risk: dict[str, Any] | None
    active_entry_plans: list[PendingEntryPlanSnapshot] = Field(default_factory=list)
    operational_status: OperationalStatusPayload
    last_market_refresh_at: datetime | None = None
    last_decision_at: datetime | None = None
    last_decision_snapshot_at: datetime | None = None
    last_decision_reference: DecisionReferencePayload = Field(default_factory=DecisionReferencePayload)
    open_positions: int
    live_trading_enabled: bool
    live_execution_ready: bool
    trading_paused: bool
    approval_armed: bool = False
    approval_expires_at: datetime | None = None
    can_enter_new_position: bool = False
    guard_mode_reason_category: str | None = None
    guard_mode_reason_code: str | None = None
    guard_mode_reason_message: str | None = None
    pause_reason_code: str | None = None
    pause_origin: str | None = None
    pause_triggered_at: datetime | None = None
    auto_resume_after: datetime | None = None
    auto_resume_status: str = "not_paused"
    auto_resume_eligible: bool = False
    auto_resume_last_blockers: list[str] = Field(default_factory=list)
    pause_severity: str | None = None
    pause_recovery_class: str | None = None
    operating_state: str = "TRADABLE"
    protection_recovery_status: str = "idle"
    protection_recovery_active: bool = False
    protection_recovery_failure_count: int = 0
    missing_protection_symbols: list[str] = Field(default_factory=list)
    missing_protection_items: dict[str, list[str]] = Field(default_factory=dict)
    pnl_summary: dict[str, Any] = Field(default_factory=dict)
    account_sync_summary: dict[str, Any] = Field(default_factory=dict)
    sync_freshness_summary: dict[str, Any] = Field(default_factory=dict)
    exposure_summary: dict[str, Any] = Field(default_factory=dict)
    execution_policy_summary: dict[str, Any] = Field(default_factory=dict)
    market_context_summary: dict[str, Any] = Field(default_factory=dict)
    adaptive_protection_summary: dict[str, Any] = Field(default_factory=dict)
    adaptive_signal_summary: dict[str, Any] = Field(default_factory=dict)
    position_management_summary: dict[str, Any] = Field(default_factory=dict)
    daily_pnl: float
    cumulative_pnl: float
    blocked_reasons: list[str]
    latest_blocked_reasons: list[str] = Field(default_factory=list)
    market_freshness_summary: dict[str, Any] = Field(default_factory=dict)
    user_stream_summary: dict[str, Any] = Field(default_factory=dict)
    reconciliation_summary: dict[str, Any] = Field(default_factory=dict)
    candidate_selection_summary: dict[str, Any] = Field(default_factory=dict)
    protected_positions: int = 0
    unprotected_positions: int = 0
    position_protection_summary: list[dict[str, Any]] = Field(default_factory=list)


class AuditTimelineEntry(StrictBaseModel):
    event_category: str = "health_system"
    event_type: str
    entity_type: str
    entity_id: str
    severity: str
    message: str
    payload: dict[str, Any]
    created_at: datetime


class SymbolCadenceOverride(StrictBaseModel):
    symbol: str = Field(min_length=1, max_length=30)
    enabled: bool = True
    timeframe_override: str | None = Field(default=None, min_length=1, max_length=20)
    market_refresh_interval_minutes_override: int | None = Field(default=None, ge=1, le=1440)
    position_management_interval_seconds_override: int | None = Field(default=None, ge=30, le=86400)
    decision_cycle_interval_minutes_override: int | None = Field(default=None, ge=1, le=1440)
    ai_call_interval_minutes_override: int | None = Field(default=None, ge=5, le=1440)


class SymbolEffectiveCadence(StrictBaseModel):
    symbol: str
    enabled: bool = True
    uses_global_defaults: bool = True
    timeframe: str
    market_refresh_interval_minutes: int = Field(ge=1, le=1440)
    position_management_interval_seconds: int = Field(ge=30, le=86400)
    decision_cycle_interval_minutes: int = Field(ge=1, le=1440)
    ai_call_interval_minutes: int = Field(ge=5, le=1440)
    estimated_monthly_ai_calls: int = Field(ge=0)
    last_market_refresh_at: datetime | None = None
    last_position_management_at: datetime | None = None
    last_decision_at: datetime | None = None
    last_ai_decision_at: datetime | None = None
    next_market_refresh_due_at: datetime | None = None
    next_position_management_due_at: datetime | None = None
    next_decision_due_at: datetime | None = None
    next_ai_call_due_at: datetime | None = None


class AppSettingsResponse(StrictBaseModel):
    id: int
    operational_status: OperationalStatusPayload
    live_trading_enabled: bool
    rollout_mode: RolloutMode = "shadow"
    exchange_submit_allowed: bool = False
    limited_live_max_notional: float | None = None
    live_trading_env_enabled: bool
    manual_live_approval: bool
    live_execution_armed: bool
    live_execution_armed_until: datetime | None = None
    live_approval_window_minutes: int
    live_execution_ready: bool
    trading_paused: bool
    approval_armed: bool = False
    approval_expires_at: datetime | None = None
    can_enter_new_position: bool = False
    guard_mode_reason_category: str | None = None
    guard_mode_reason_code: str | None = None
    guard_mode_reason_message: str | None = None
    pause_reason_code: str | None = None
    pause_origin: str | None = None
    pause_reason_detail: dict[str, Any] = Field(default_factory=dict)
    pause_triggered_at: datetime | None = None
    auto_resume_after: datetime | None = None
    auto_resume_whitelisted: bool = False
    auto_resume_eligible: bool = False
    auto_resume_status: str = "not_paused"
    blocked_reasons: list[str] = Field(default_factory=list)
    auto_resume_last_blockers: list[str] = Field(default_factory=list)
    latest_blocked_reasons: list[str] = Field(default_factory=list)
    pause_severity: str | None = None
    pause_recovery_class: str | None = None
    operating_state: str = "TRADABLE"
    protection_recovery_status: str = "idle"
    protection_recovery_active: bool = False
    protection_recovery_failure_count: int = 0
    missing_protection_symbols: list[str] = Field(default_factory=list)
    missing_protection_items: dict[str, list[str]] = Field(default_factory=dict)
    pnl_summary: dict[str, Any] = Field(default_factory=dict)
    account_sync_summary: dict[str, Any] = Field(default_factory=dict)
    sync_freshness_summary: dict[str, Any] = Field(default_factory=dict)
    market_freshness_summary: dict[str, Any] = Field(default_factory=dict)
    exposure_summary: dict[str, Any] = Field(default_factory=dict)
    execution_policy_summary: dict[str, Any] = Field(default_factory=dict)
    market_context_summary: dict[str, Any] = Field(default_factory=dict)
    adaptive_protection_summary: dict[str, Any] = Field(default_factory=dict)
    adaptive_signal_summary: dict[str, Any] = Field(default_factory=dict)
    position_management_summary: dict[str, Any] = Field(default_factory=dict)
    user_stream_summary: dict[str, Any] = Field(default_factory=dict)
    reconciliation_summary: dict[str, Any] = Field(default_factory=dict)
    candidate_selection_summary: dict[str, Any] = Field(default_factory=dict)
    default_symbol: str
    tracked_symbols: list[str]
    default_timeframe: str
    exchange_sync_interval_seconds: int
    market_refresh_interval_minutes: int
    position_management_interval_seconds: int
    schedule_windows: list[str]
    symbol_cadence_overrides: list[SymbolCadenceOverride] = Field(default_factory=list)
    symbol_effective_cadences: list[SymbolEffectiveCadence] = Field(default_factory=list)
    max_leverage: float
    max_risk_per_trade: float
    max_daily_loss: float
    max_consecutive_losses: int
    max_gross_exposure_pct: float
    max_largest_position_pct: float
    max_directional_bias_pct: float
    max_same_tier_concentration_pct: float
    stale_market_seconds: int
    slippage_threshold_pct: float
    adaptive_signal_enabled: bool
    position_management_enabled: bool
    break_even_enabled: bool
    atr_trailing_stop_enabled: bool
    partial_take_profit_enabled: bool
    partial_tp_rr: float
    partial_tp_size_pct: float
    move_stop_to_be_rr: float
    time_stop_enabled: bool
    time_stop_minutes: int
    time_stop_profit_floor: float
    holding_edge_decay_enabled: bool
    reduce_on_regime_shift_enabled: bool
    starting_equity: float
    ai_enabled: bool
    ai_provider: str
    ai_model: str
    ai_call_interval_minutes: int
    decision_cycle_interval_minutes: int
    ai_max_input_candles: int
    ai_temperature: float
    binance_market_data_enabled: bool
    binance_testnet_enabled: bool
    binance_futures_enabled: bool
    mode: str
    openai_api_key_configured: bool
    binance_api_key_configured: bool
    binance_api_secret_configured: bool
    estimated_monthly_ai_calls: int
    estimated_monthly_ai_calls_breakdown: dict[str, int]
    projected_monthly_ai_calls_if_enabled: int
    projected_monthly_ai_calls_breakdown_if_enabled: dict[str, int]
    recent_ai_calls_24h: int
    recent_ai_calls_7d: int
    recent_ai_successes_24h: int
    recent_ai_successes_7d: int
    recent_ai_failures_24h: int
    recent_ai_failures_7d: int
    recent_ai_tokens_24h: dict[str, int]
    recent_ai_tokens_7d: dict[str, int]
    recent_ai_role_calls_24h: dict[str, int]
    recent_ai_role_calls_7d: dict[str, int]
    recent_ai_role_failures_24h: dict[str, int]
    recent_ai_role_failures_7d: dict[str, int]
    recent_ai_failure_reasons: list[str]
    observed_monthly_ai_calls_projection: int
    observed_monthly_ai_calls_projection_breakdown: dict[str, int]
    manual_ai_guard_minutes: int


class AppSettingsUpdateRequest(StrictBaseModel):
    live_trading_enabled: bool
    rollout_mode: RolloutMode | None = None
    limited_live_max_notional: float = Field(default=500.0, gt=0.0, le=1000000.0)
    manual_live_approval: bool
    live_approval_window_minutes: int = Field(ge=0, le=240)
    default_symbol: str = Field(min_length=1, max_length=30)
    tracked_symbols: list[str] = Field(min_length=1)
    default_timeframe: str = Field(min_length=1, max_length=20)
    exchange_sync_interval_seconds: int = Field(default=60, ge=30, le=3600)
    market_refresh_interval_minutes: int = Field(default=1, ge=1, le=1440)
    position_management_interval_seconds: int = Field(default=60, ge=30, le=3600)
    schedule_windows: list[str]
    symbol_cadence_overrides: list[SymbolCadenceOverride] = Field(default_factory=list)
    max_leverage: float = Field(gt=0.0, le=5.0)
    max_risk_per_trade: float = Field(gt=0.0, le=0.02)
    max_daily_loss: float = Field(gt=0.0, le=0.05)
    max_consecutive_losses: int = Field(ge=1, le=20)
    max_gross_exposure_pct: float = Field(default=3.0, gt=0.0, le=3.0)
    max_largest_position_pct: float = Field(default=1.5, gt=0.0, le=1.5)
    max_directional_bias_pct: float = Field(default=2.0, gt=0.0, le=2.0)
    max_same_tier_concentration_pct: float = Field(default=2.5, gt=0.0, le=2.5)
    stale_market_seconds: int = Field(ge=30, le=86400)
    slippage_threshold_pct: float = Field(gt=0.0, le=0.1)
    adaptive_signal_enabled: bool = False
    position_management_enabled: bool = True
    break_even_enabled: bool = True
    atr_trailing_stop_enabled: bool = True
    partial_take_profit_enabled: bool = True
    partial_tp_rr: float = Field(default=1.5, ge=0.1, le=10.0)
    partial_tp_size_pct: float = Field(default=0.25, gt=0.0, le=1.0)
    move_stop_to_be_rr: float = Field(default=1.0, ge=0.0, le=10.0)
    time_stop_enabled: bool = False
    time_stop_minutes: int = Field(default=120, ge=1, le=10080)
    time_stop_profit_floor: float = Field(default=0.15, ge=-1.0, le=2.0)
    holding_edge_decay_enabled: bool = True
    reduce_on_regime_shift_enabled: bool = True
    starting_equity: float = Field(gt=0.0)
    ai_enabled: bool
    ai_provider: Literal["openai", "mock"]
    ai_model: str = Field(min_length=1, max_length=80)
    ai_call_interval_minutes: int = Field(ge=5, le=1440)
    decision_cycle_interval_minutes: int = Field(ge=1, le=1440)
    ai_max_input_candles: int = Field(ge=16, le=200)
    ai_temperature: float = Field(ge=0.0, le=1.0)
    binance_market_data_enabled: bool
    binance_testnet_enabled: bool
    binance_futures_enabled: bool
    openai_api_key: str | None = None
    binance_api_key: str | None = None
    binance_api_secret: str | None = None
    clear_openai_api_key: bool = False
    clear_binance_api_key: bool = False
    clear_binance_api_secret: bool = False


class OpenAIConnectionTestRequest(StrictBaseModel):
    api_key: str | None = None
    model: str = Field(min_length=1, max_length=80)


class BinanceConnectionTestRequest(StrictBaseModel):
    api_key: str | None = None
    api_secret: str | None = None
    testnet_enabled: bool = False
    symbol: str = Field(default="BTCUSDT", min_length=1, max_length=30)
    timeframe: str = Field(default="15m", min_length=1, max_length=20)


class ConnectionTestResponse(StrictBaseModel):
    ok: bool
    provider: str
    message: str
    details: dict[str, Any]


class ManualLiveApprovalRequest(StrictBaseModel):
    minutes: int | None = Field(default=None, ge=0, le=240)


class BinanceLiveTestOrderRequest(StrictBaseModel):
    symbol: str = Field(default="BTCUSDT", min_length=1, max_length=30)
    side: Literal["BUY", "SELL"] = "BUY"
    quantity: float | None = Field(default=None, gt=0.0)


class BinanceAccountSummary(StrictBaseModel):
    connected: bool
    message: str
    testnet_enabled: bool
    futures_enabled: bool
    tracked_symbols: list[str] = Field(default_factory=list)
    can_trade: bool = False
    exchange_can_trade: bool = False
    app_live_execution_ready: bool = False
    app_trading_paused: bool = False
    app_operating_state: str = "TRADABLE"
    app_pause_reason_code: str | None = None
    app_pause_origin: str | None = None
    app_auto_resume_last_blockers: list[str] = Field(default_factory=list)
    guard_mode_reason_category: str | None = None
    guard_mode_reason_code: str | None = None
    guard_mode_reason_message: str | None = None
    latest_blocked_reasons: list[str] = Field(default_factory=list)
    fee_tier: int = 0
    total_wallet_balance: float = 0.0
    available_balance: float = 0.0
    total_unrealized_profit: float = 0.0
    total_margin_balance: float = 0.0
    total_position_initial_margin: float = 0.0
    total_open_order_initial_margin: float = 0.0
    total_maint_margin: float = 0.0
    asset_count: int = 0
    open_positions: int = 0
    open_orders: int = 0
    exchange_update_time: datetime | None = None


class BinanceAccountAsset(StrictBaseModel):
    asset: str
    wallet_balance: float = 0.0
    available_balance: float = 0.0
    margin_balance: float = 0.0
    unrealized_profit: float = 0.0
    max_withdraw_amount: float = 0.0


class BinanceAccountPosition(StrictBaseModel):
    symbol: str
    position_side: Literal["long", "short"]
    position_amt: float
    entry_price: float = 0.0
    mark_price: float = 0.0
    liquidation_price: float = 0.0
    leverage: float = 0.0
    unrealized_profit: float = 0.0
    isolated_margin: float = 0.0
    notional: float = 0.0
    margin_type: str = ""


class BinanceOpenOrderSummary(StrictBaseModel):
    symbol: str
    side: str
    type: str
    status: str
    price: float = 0.0
    stop_price: float = 0.0
    orig_qty: float = 0.0
    executed_qty: float = 0.0
    reduce_only: bool = False
    close_position: bool = False
    time_in_force: str = ""
    update_time: datetime | None = None


class BinanceAccountResponse(StrictBaseModel):
    summary: BinanceAccountSummary
    assets: list[BinanceAccountAsset] = Field(default_factory=list)
    positions: list[BinanceAccountPosition] = Field(default_factory=list)
    open_orders: list[BinanceOpenOrderSummary] = Field(default_factory=list)
