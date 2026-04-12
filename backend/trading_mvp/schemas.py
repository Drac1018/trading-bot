from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TradeDecision(StrictBaseModel):
    decision: Literal["hold", "long", "short", "reduce", "exit"]
    confidence: float = Field(ge=0.0, le=1.0)
    symbol: str = Field(min_length=1, max_length=30)
    timeframe: str = Field(min_length=1, max_length=20)
    entry_zone_min: float | None = None
    entry_zone_max: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    max_holding_minutes: int = Field(ge=1, le=10080)
    risk_pct: float = Field(gt=0.0, le=1.0)
    leverage: float = Field(gt=0.0, le=10.0)
    rationale_codes: list[str]
    explanation_short: str = Field(min_length=3, max_length=240)
    explanation_detailed: str = Field(min_length=10, max_length=600)


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


class ProductBacklogItem(StrictBaseModel):
    title: str
    problem: str
    proposal: str
    severity: Literal["low", "medium", "high", "critical"]
    effort: Literal["small", "medium", "large"]
    impact: Literal["low", "medium", "high"]
    priority: Literal["low", "medium", "high", "critical"]
    rationale: str


class ProductBacklogBatch(StrictBaseModel):
    items: list[ProductBacklogItem]


class ProductBacklogResponse(StrictBaseModel):
    id: int
    title: str
    problem: str
    proposal: str
    severity: Literal["low", "medium", "high", "critical"]
    effort: Literal["small", "medium", "large"]
    impact: Literal["low", "medium", "high"]
    priority: Literal["low", "medium", "high", "critical"]
    rationale: str
    source: str
    status: str
    auto_apply_supported: bool = False
    auto_apply_label: str | None = None
    created_at: datetime
    updated_at: datetime


class BacklogCodexDraftResponse(StrictBaseModel):
    available: bool
    title: str
    prompt: str
    generated_at: datetime
    note: str


class UserChangeRequestCreate(StrictBaseModel):
    title: str = Field(min_length=3, max_length=200)
    detail: str = Field(min_length=5)
    status: Literal["requested", "accepted", "applied", "verified"] = "requested"
    linked_backlog_id: int | None = None


class UserChangeRequestResponse(StrictBaseModel):
    id: int
    title: str
    detail: str
    status: Literal["requested", "accepted", "applied", "verified"]
    linked_backlog_id: int | None = None
    linked_backlog_title: str | None = None
    created_at: datetime
    updated_at: datetime


class AppliedChangeRecordCreate(StrictBaseModel):
    title: str = Field(min_length=3, max_length=200)
    summary: str = Field(min_length=5, max_length=500)
    detail: str = Field(min_length=5)
    related_backlog_id: int | None = None
    source_type: Literal["ai", "user", "manual"]
    files_changed: list[str] = Field(default_factory=list)
    verification_summary: str = Field(min_length=3)
    applied_at: datetime | None = None


class AppliedChangeRecordResponse(StrictBaseModel):
    id: int
    title: str
    summary: str
    detail: str
    related_backlog_id: int | None = None
    related_backlog_title: str | None = None
    source_type: Literal["ai", "user", "manual"]
    files_changed: list[str]
    verification_summary: str
    applied_at: datetime
    created_at: datetime
    updated_at: datetime


class ProductBacklogDetailResponse(ProductBacklogResponse):
    user_requests: list[UserChangeRequestResponse] = Field(default_factory=list)
    applied_records: list[AppliedChangeRecordResponse] = Field(default_factory=list)
    codex_prompt_draft: BacklogCodexDraftResponse | None = None


class SignalPerformanceEntry(StrictBaseModel):
    rationale_code: str
    decisions: int = Field(ge=0)
    approvals: int = Field(ge=0)
    orders: int = Field(ge=0)
    fills: int = Field(ge=0)
    wins: int = Field(ge=0)
    losses: int = Field(ge=0)
    realized_pnl_total: float
    average_slippage_pct: float = Field(ge=0.0)
    latest_seen_at: datetime


class SignalPerformanceReportResponse(StrictBaseModel):
    generated_at: datetime
    window_hours: int = Field(ge=1, le=168)
    items: list[SignalPerformanceEntry] = Field(default_factory=list)


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


class BacklogBoardResponse(StrictBaseModel):
    ai_backlog: list[ProductBacklogDetailResponse] = Field(default_factory=list)
    unlinked_user_requests: list[UserChangeRequestResponse] = Field(default_factory=list)
    unlinked_applied_records: list[AppliedChangeRecordResponse] = Field(default_factory=list)
    signal_performance_report: SignalPerformanceReportResponse | None = None
    structured_competitor_notes: StructuredCompetitorNotesResponse | None = None


class BacklogAutoApplyResult(StrictBaseModel):
    backlog_id: int
    title: str
    backlog_status: str
    auto_apply_supported: bool
    handler_key: str | None = None
    already_applied: bool = False
    message: str
    applied_record: AppliedChangeRecordResponse | None = None


class BacklogAutoApplyBatchResponse(StrictBaseModel):
    items: list[BacklogAutoApplyResult] = Field(default_factory=list)


class RiskCheckResult(StrictBaseModel):
    allowed: bool
    decision: Literal["hold", "long", "short", "reduce", "exit"]
    reason_codes: list[str]
    approved_risk_pct: float = Field(ge=0.0, le=1.0)
    approved_leverage: float = Field(ge=0.0, le=10.0)
    operating_mode: Literal["live", "paused", "hold"]
    effective_leverage_cap: float = Field(gt=0.0, le=10.0)
    symbol_risk_tier: Literal["btc", "major_alt", "alt"]
    exposure_metrics: dict[str, float] = Field(default_factory=dict)


class ExecutionIntent(StrictBaseModel):
    symbol: str
    action: Literal["long", "short", "reduce", "exit"]
    intent_type: Literal["entry", "scale_in", "protection", "reduce_only", "emergency_exit"]
    quantity: float = Field(gt=0.0)
    requested_price: float = Field(gt=0.0)
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


class FeaturePayload(StrictBaseModel):
    symbol: str
    timeframe: str
    trend_score: float
    volatility_pct: float = Field(ge=0.0)
    volume_ratio: float = Field(ge=0.0)
    drawdown_pct: float = Field(ge=0.0)
    rsi: float = Field(ge=0.0, le=100.0)
    atr: float = Field(ge=0.0)
    data_quality_flags: list[str]


class OverviewResponse(StrictBaseModel):
    mode: str
    symbol: str
    tracked_symbols: list[str]
    timeframe: str
    latest_price: float
    latest_decision: dict[str, Any] | None
    latest_risk: dict[str, Any] | None
    open_positions: int
    live_trading_enabled: bool
    live_execution_ready: bool
    trading_paused: bool
    pause_reason_code: str | None = None
    pause_origin: str | None = None
    pause_triggered_at: datetime | None = None
    auto_resume_after: datetime | None = None
    auto_resume_status: str = "not_paused"
    auto_resume_eligible: bool = False
    auto_resume_last_blockers: list[str] = Field(default_factory=list)
    pause_severity: str | None = None
    pause_recovery_class: str | None = None
    daily_pnl: float
    cumulative_pnl: float
    blocked_reasons: list[str]
    protected_positions: int = 0
    unprotected_positions: int = 0
    position_protection_summary: list[dict[str, Any]] = Field(default_factory=list)


class AuditTimelineEntry(StrictBaseModel):
    event_type: str
    entity_type: str
    entity_id: str
    severity: str
    message: str
    payload: dict[str, Any]
    created_at: datetime


class AppSettingsResponse(StrictBaseModel):
    id: int
    live_trading_enabled: bool
    live_trading_env_enabled: bool
    manual_live_approval: bool
    live_execution_armed: bool
    live_execution_armed_until: datetime | None = None
    live_approval_window_minutes: int
    live_execution_ready: bool
    trading_paused: bool
    pause_reason_code: str | None = None
    pause_origin: str | None = None
    pause_reason_detail: dict[str, Any] = Field(default_factory=dict)
    pause_triggered_at: datetime | None = None
    auto_resume_after: datetime | None = None
    auto_resume_whitelisted: bool = False
    auto_resume_eligible: bool = False
    auto_resume_status: str = "not_paused"
    auto_resume_last_blockers: list[str] = Field(default_factory=list)
    pause_severity: str | None = None
    pause_recovery_class: str | None = None
    default_symbol: str
    tracked_symbols: list[str]
    default_timeframe: str
    schedule_windows: list[str]
    max_leverage: float
    max_risk_per_trade: float
    max_daily_loss: float
    max_consecutive_losses: int
    stale_market_seconds: int
    slippage_threshold_pct: float
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
    manual_live_approval: bool
    live_approval_window_minutes: int = Field(ge=1, le=240)
    default_symbol: str = Field(min_length=1, max_length=30)
    tracked_symbols: list[str] = Field(min_length=1)
    default_timeframe: str = Field(min_length=1, max_length=20)
    schedule_windows: list[str]
    max_leverage: float = Field(gt=0.0, le=5.0)
    max_risk_per_trade: float = Field(gt=0.0, le=0.02)
    max_daily_loss: float = Field(gt=0.0, le=0.05)
    max_consecutive_losses: int = Field(ge=1, le=20)
    stale_market_seconds: int = Field(ge=30, le=86400)
    slippage_threshold_pct: float = Field(gt=0.0, le=0.1)
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
    minutes: int | None = Field(default=None, ge=1, le=240)


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
