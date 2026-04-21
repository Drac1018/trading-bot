from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trading_mvp.time_utils import ensure_utc_aware, parse_utc_datetime

RolloutMode = Literal["paper", "shadow", "live_dry_run", "limited_live", "full_live"]
HoldingProfile = Literal["scalp", "swing", "position"]
ConfidenceBand = Literal["high", "medium", "low", "abstain"]
RecommendedHoldingProfile = Literal["scalp", "swing", "position", "hold_current"]
PriorClassification = Literal["strong", "neutral", "weak", "unavailable"]
CapitalEfficiencyClassification = Literal["efficient", "neutral", "inefficient", "unavailable"]
PriorPenaltyLevel = Literal["none", "light", "medium", "strong"]
IntentFamily = Literal["entry", "management", "protection", "exit", "unknown"]
ManagementAction = Literal["restore_protection", "reduce_only", "exit_only", "tighten_management", "none"]
RegimeStructure = Literal["trend", "range", "squeeze", "expansion", "transition"]
RegimeDirection = Literal["bullish", "bearish", "neutral"]
RegimeVolatility = Literal["calm", "normal", "fast", "shock"]
RegimeParticipation = Literal["strong", "mixed", "weak"]
RegimeDerivatives = Literal["tailwind", "neutral", "headwind", "unavailable"]
RegimeExecution = Literal["clean", "normal", "stress", "unavailable"]
PersistenceClass = Literal["early", "established", "extended"]
TransitionRisk = Literal["low", "medium", "high"]
DataQualityGrade = Literal["complete", "partial", "degraded", "unavailable"]
EventSourceStatus = Literal["fixture", "stub", "external_api", "unavailable", "stale", "incomplete", "error"]
EventSourceProvenance = Literal["fixture", "stub", "external_api"]
EventSourceVendor = Literal["fred", "bls", "bea"]
MacroEventImportance = Literal["low", "medium", "high"]
EventBias = Literal["bullish", "bearish", "neutral"]
OperatorEventBias = Literal["bullish", "bearish", "neutral", "no_trade", "unknown"]
OperatorEventRiskState = Literal["risk_on", "risk_off", "neutral", "unknown"]
OperatorEventAlignmentStatus = Literal["aligned", "partially_aligned", "conflict", "insufficient_data"]
OperatorEventEnforcementMode = Literal[
    "observe_only",
    "approval_required",
    "block_on_conflict",
    "force_no_trade",
]
OperatorEventSourceStatus = Literal["available", "stale", "incomplete", "unavailable", "error"]
OperatorEventImportance = Literal["low", "medium", "high", "critical", "unknown"]
OperatorEffectivePolicyPreview = Literal[
    "allow_normal",
    "allow_with_approval",
    "block_new_entries",
    "force_no_trade_window",
    "insufficient_data",
]
OperatorPolicySource = Literal[
    "manual_no_trade_window",
    "operator_enforcement_mode",
    "operator_bias",
    "alignment_policy",
    "none",
]
AIEventSourceState = Literal["available", "stale", "incomplete", "unavailable", "error", "unknown"]
AITriggerReason = Literal[
    "entry_candidate_event",
    "breakout_exception_event",
    "open_position_recheck_due",
    "protection_review_event",
    "manual_review_event",
    "periodic_backstop_due",
]
AI_CONTEXT_VERSION = "2026-04-context-v1"


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _coerce_aware_datetime(value: object) -> datetime | None:
    if value in {None, ""}:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("datetime must be ISO-8601 with timezone offset or UTC Z suffix")
        return ensure_utc_aware(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if re.search(r"(Z|[+-]\d{2}:\d{2})$", text) is None:
            raise ValueError("datetime must be ISO-8601 with timezone offset or UTC Z suffix")
    parsed = parse_utc_datetime(value)
    if parsed is None:
        raise ValueError("datetime must be ISO-8601 with timezone offset or UTC Z suffix")
    return parsed


def _coerce_required_aware_datetime(value: object) -> datetime:
    parsed = _coerce_aware_datetime(value)
    if parsed is None:
        raise ValueError("datetime is required")
    return parsed


class TradeDecision(StrictBaseModel):
    decision: Literal["hold", "long", "short", "reduce", "exit"]
    confidence: float = Field(ge=0.0, le=1.0)
    symbol: str = Field(min_length=1, max_length=30)
    timeframe: str = Field(min_length=1, max_length=20)
    entry_zone_min: float | None = None
    entry_zone_max: float | None = None
    entry_mode: Literal["breakout_confirm", "pullback_confirm", "immediate", "none"] | None = None
    holding_profile: HoldingProfile = "scalp"
    holding_profile_reason: str | None = None
    invalidation_price: float | None = Field(default=None, gt=0.0)
    max_chase_bps: float | None = Field(default=None, ge=0.0, le=500.0)
    idea_ttl_minutes: int | None = Field(default=None, ge=1, le=1440)
    stop_loss: float | None = None
    take_profit: float | None = None
    max_holding_minutes: int = Field(ge=1, le=10080)
    risk_pct: float = Field(gt=0.0, le=1.0)
    leverage: float = Field(gt=0.0, le=10.0)
    rationale_codes: list[str]
    confidence_band: ConfidenceBand | None = None
    recommended_holding_profile: RecommendedHoldingProfile | None = None
    primary_reason_codes: list[str] = Field(default_factory=list)
    no_trade_reason_codes: list[str] = Field(default_factory=list)
    abstain_reason_codes: list[str] = Field(default_factory=list)
    invalidation_reason_codes: list[str] = Field(default_factory=list)
    expected_time_to_0_25r_minutes: int | None = Field(default=None, ge=0, le=10080)
    expected_time_to_0_5r_minutes: int | None = Field(default=None, ge=0, le=10080)
    expected_mae_r: float | None = None
    regime_transition_risk: TransitionRisk | None = None
    data_quality_penalty_applied: bool = False
    should_abstain: bool = False
    bounded_output_applied: bool = False
    fallback_reason_codes: list[str] = Field(default_factory=list)
    fail_closed_applied: bool = False
    provider_status: str | None = None
    data_quality_fail_closed_applied: bool = False
    data_quality_block_reason_codes: list[str] = Field(default_factory=list)
    minimum_quality_required: str | None = None
    abstain_due_to_data_quality: bool = False
    quality_penalty_level: PriorPenaltyLevel = "none"
    provider_not_called_due_to_quality: bool = False
    engine_prior_classification: PriorClassification | None = None
    capital_efficiency_classification: CapitalEfficiencyClassification | None = None
    session_prior_classification: PriorClassification | None = None
    time_of_day_prior_classification: PriorClassification | None = None
    session_prior_sample_count: int | None = None
    time_of_day_prior_sample_count: int | None = None
    session_prior_recency_minutes: float | None = None
    time_of_day_prior_recency_minutes: float | None = None
    session_time_calibration_reason_codes: list[str] = Field(default_factory=list)
    session_time_penalty_applied: bool = False
    prior_penalty_level: PriorPenaltyLevel = "none"
    prior_reason_codes: list[str] = Field(default_factory=list)
    sample_threshold_satisfied: dict[str, bool] = Field(default_factory=dict)
    confidence_adjustment_applied: bool = False
    abstain_due_to_prior_and_quality: bool = False
    expected_payoff_efficiency_hint_summary: dict[str, float | None] = Field(default_factory=dict)
    intent_family: IntentFamily = "unknown"
    management_action: ManagementAction = "none"
    legacy_semantics_preserved: bool = False
    analytics_excluded_from_entry_stats: bool = False
    prompt_family_hint: str | None = None
    ai_context_version: str = AI_CONTEXT_VERSION
    event_risk_acknowledgement: str | None = None
    confidence_penalty_reason: str | None = None
    scenario_note: str | None = None
    explanation_short: str = Field(min_length=3, max_length=240)
    explanation_detailed: str = Field(min_length=10, max_length=600)

    @model_validator(mode="after")
    def _backfill_optional_ai_fields(self) -> TradeDecision:
        if not self.primary_reason_codes and self.rationale_codes:
            self.primary_reason_codes = list(self.rationale_codes)
        if self.recommended_holding_profile is None:
            self.recommended_holding_profile = (
                "hold_current"
                if self.decision not in {"long", "short"}
                else self.holding_profile
            )
        if self.confidence_band is None:
            if self.should_abstain:
                self.confidence_band = "abstain"
            elif self.confidence >= 0.72:
                self.confidence_band = "high"
            elif self.confidence >= 0.46:
                self.confidence_band = "medium"
            else:
                self.confidence_band = "low"
        if self.decision == "hold" and not self.no_trade_reason_codes and self.primary_reason_codes:
            self.no_trade_reason_codes = list(self.primary_reason_codes)
        if self.should_abstain and not self.abstain_reason_codes:
            self.abstain_reason_codes = list(self.no_trade_reason_codes or self.primary_reason_codes)
        if not self.ai_context_version:
            self.ai_context_version = AI_CONTEXT_VERSION
        return self


class TradeDecisionCandidateScore(StrictBaseModel):
    regime_fit: float = 0.0
    expected_rr: float = 0.0
    recent_signal_performance: float = 0.0
    derivatives_alignment: float = 0.0
    lead_lag_alignment: float = 0.0
    meta_gate_probability: float = 0.0
    agreement_alignment: float = 0.0
    execution_quality: float = 0.0
    slot_conviction: float = 0.0
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
    holding_profile: HoldingProfile = "scalp"
    holding_profile_reason: str | None = None
    strategy_engine: str = "unspecified"
    strategy_engine_context: dict[str, Any] = Field(default_factory=dict)
    derivatives_summary: dict[str, Any] = Field(default_factory=dict)
    lead_lag_summary: dict[str, Any] = Field(default_factory=dict)
    explanation_short: str = Field(min_length=3, max_length=240)
    explanation_detailed: str = Field(min_length=10, max_length=600)


class TradeDecisionCandidateBatch(StrictBaseModel):
    items: list[TradeDecisionCandidate] = Field(default_factory=list)


class AIReviewTriggerPayload(StrictBaseModel):
    trigger_reason: AITriggerReason
    symbol: str = Field(min_length=1, max_length=30)
    timeframe: str = Field(min_length=1, max_length=20)
    strategy_engine: str | None = Field(default=None, min_length=1, max_length=80)
    holding_profile: HoldingProfile | None = None
    assigned_slot: str | None = Field(default=None, min_length=1, max_length=40)
    candidate_weight: float | None = None
    reason_codes: list[str] = Field(default_factory=list)
    trigger_fingerprint: str = Field(min_length=8, max_length=128)
    fingerprint_basis: dict[str, Any] = Field(default_factory=dict)
    fingerprint_changed_fields: list[str] = Field(default_factory=list)
    dedupe_reason: str | None = None
    last_decision_at: datetime | None = None
    last_material_review_at: datetime | None = None
    forced_review_reason: str | None = None
    applied_review_cadence_minutes: int | None = None
    review_cadence_source: str | None = None
    holding_profile_cadence_hint: dict[str, Any] = Field(default_factory=dict)
    cadence_fallback_reason: str | None = None
    max_review_age_minutes: int | None = None
    cadence_profile_summary: dict[str, Any] = Field(default_factory=dict)
    triggered_at: datetime


class CompositeRegimePacket(StrictBaseModel):
    structure_regime: RegimeStructure
    direction_regime: RegimeDirection
    volatility_regime: RegimeVolatility
    participation_regime: RegimeParticipation
    derivatives_regime: RegimeDerivatives
    execution_regime: RegimeExecution
    persistence_bars: int = Field(ge=0, default=0)
    persistence_class: PersistenceClass = "early"
    transition_risk: TransitionRisk = "medium"
    regime_reason_codes: list[str] = Field(default_factory=list)


class DataQualityPacket(StrictBaseModel):
    data_quality_grade: DataQualityGrade = "complete"
    missing_context_flags: list[str] = Field(default_factory=list)
    stale_context_flags: list[str] = Field(default_factory=list)
    derivatives_available: bool = False
    orderbook_available: bool = False
    spread_quality_available: bool = False
    account_state_trustworthy: bool = True
    market_state_trustworthy: bool = True


class PreviousThesisDeltaPacket(StrictBaseModel):
    previous_decision: Literal["hold", "long", "short", "reduce", "exit"] | None = None
    previous_strategy_engine: str | None = None
    previous_holding_profile: HoldingProfile | None = None
    previous_rationale_codes: list[str] = Field(default_factory=list)
    previous_no_trade_reason_codes: list[str] = Field(default_factory=list)
    previous_invalidation_reason_codes: list[str] = Field(default_factory=list)
    previous_regime_packet_summary: dict[str, Any] = Field(default_factory=dict)
    previous_data_quality_grade: DataQualityGrade | None = None
    last_ai_invoked_at: datetime | None = None
    delta_changed_fields: list[str] = Field(default_factory=list)
    delta_reason_codes_added: list[str] = Field(default_factory=list)
    delta_reason_codes_removed: list[str] = Field(default_factory=list)
    thesis_degrade_detected: bool = False
    regime_transition_detected: bool = False
    data_quality_changed: bool = False


class AIPriorContextPacket(StrictBaseModel):
    engine_prior_available: bool = False
    engine_prior_sample_count: int = Field(ge=0, default=0)
    engine_sample_threshold_satisfied: bool = False
    engine_prior_classification: PriorClassification = "unavailable"
    engine_expectancy_hint: float | None = None
    engine_net_pnl_after_fees_hint: float | None = None
    engine_avg_signed_slippage_bps_hint: float | None = None
    engine_time_to_profit_hint_minutes: float | None = None
    engine_drawdown_impact_hint: float | None = None
    capital_efficiency_available: bool = False
    capital_efficiency_sample_count: int = Field(ge=0, default=0)
    capital_efficiency_sample_threshold_satisfied: bool = False
    capital_efficiency_classification: CapitalEfficiencyClassification = "unavailable"
    pnl_per_exposure_hour_hint: float | None = None
    net_pnl_after_fees_per_hour_hint: float | None = None
    time_to_0_25r_hint_minutes: float | None = None
    time_to_0_5r_hint_minutes: float | None = None
    time_to_fail_hint_minutes: float | None = None
    capital_slot_occupancy_efficiency_hint: float | None = None
    session_prior_available: bool = False
    session_prior_sample_count: int = Field(ge=0, default=0)
    session_sample_threshold_satisfied: bool = False
    session_prior_classification: PriorClassification = "unavailable"
    session_prior_recency_minutes: float | None = None
    time_of_day_prior_available: bool = False
    time_of_day_prior_sample_count: int = Field(ge=0, default=0)
    time_of_day_sample_threshold_satisfied: bool = False
    time_of_day_prior_classification: PriorClassification = "unavailable"
    time_of_day_prior_recency_minutes: float | None = None
    session_time_calibration_reason_codes: list[str] = Field(default_factory=list)
    prior_reason_codes: list[str] = Field(default_factory=list)
    prior_penalty_level: PriorPenaltyLevel = "none"
    expected_payoff_efficiency_hint_summary: dict[str, float | None] = Field(default_factory=dict)


class AIDecisionContextPacket(StrictBaseModel):
    ai_context_version: str = AI_CONTEXT_VERSION
    symbol: str = Field(min_length=1, max_length=30)
    timeframe: str = Field(min_length=1, max_length=20)
    trigger_type: AITriggerReason | None = None
    composite_regime: CompositeRegimePacket
    regime_summary: RegimeSummaryPayload = Field(default_factory=lambda: RegimeSummaryPayload())
    derivatives_summary: DerivativesSummaryPayload = Field(default_factory=lambda: DerivativesSummaryPayload())
    lead_lag_summary: LeadLagSummaryPayload = Field(default_factory=lambda: LeadLagSummaryPayload())
    event_context_summary: EventContextSummaryPayload = Field(default_factory=lambda: EventContextSummaryPayload())
    data_quality: DataQualityPacket
    previous_thesis: PreviousThesisDeltaPacket = Field(default_factory=PreviousThesisDeltaPacket)
    prior_context: AIPriorContextPacket = Field(default_factory=AIPriorContextPacket)
    strategy_engine: str | None = None
    strategy_engine_context: dict[str, Any] = Field(default_factory=dict)
    holding_profile: HoldingProfile | None = None
    holding_profile_reason: str | None = None
    assigned_slot: str | None = Field(default=None, min_length=1, max_length=40)
    candidate_weight: float | None = None
    capacity_reason: str | None = None
    blocked_reason_codes: list[str] = Field(default_factory=list)
    hard_stop_active: bool | None = None
    stop_widening_allowed: bool | None = None
    initial_stop_type: str | None = None
    selection_context_summary: dict[str, Any] = Field(default_factory=dict)
    prompt_family_hint: str | None = None


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
    net_pnl_after_fees: float = 0.0
    fees: float = 0.0
    max_drawdown: float = Field(ge=0.0, default=0.0)
    win_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    avg_win: float = 0.0
    avg_loss: float = Field(ge=0.0, default=0.0)
    expectancy: float = 0.0
    profit_factor: float = Field(ge=0.0, default=0.0)
    hold_ratio: float = Field(ge=0.0, le=1.0, default=0.0)
    blocked_ratio: float = Field(ge=0.0, le=1.0, default=0.0)
    average_arrival_slippage_pct: float = Field(ge=0.0, default=0.0)
    average_realized_slippage_pct: float = Field(ge=0.0, default=0.0)
    average_first_fill_latency_seconds: float = Field(ge=0.0, default=0.0)
    average_hold_time_minutes: float = Field(ge=0.0, default=0.0)
    cancel_attempts: int = Field(ge=0, default=0)
    cancel_successes: int = Field(ge=0, default=0)
    cancel_success_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    stop_hit_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    tp_hit_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    partial_tp_contribution: float = 0.0
    runner_contribution: float = 0.0
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
    net_pnl_after_fees: float = 0.0
    fees: float = 0.0
    max_drawdown: float = Field(ge=0.0, default=0.0)
    win_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    avg_win: float = 0.0
    avg_loss: float = Field(ge=0.0, default=0.0)
    expectancy: float = 0.0
    profit_factor: float = Field(ge=0.0, default=0.0)
    hold_ratio: float = Field(ge=0.0, le=1.0, default=0.0)
    blocked_ratio: float = Field(ge=0.0, le=1.0, default=0.0)
    average_arrival_slippage_pct: float = Field(ge=0.0, default=0.0)
    average_realized_slippage_pct: float = Field(ge=0.0, default=0.0)
    average_first_fill_latency_seconds: float = Field(ge=0.0, default=0.0)
    average_hold_time_minutes: float = Field(ge=0.0, default=0.0)
    cancel_attempts: int = Field(ge=0, default=0)
    cancel_successes: int = Field(ge=0, default=0)
    cancel_success_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    stop_hit_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    tp_hit_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    partial_tp_contribution: float = 0.0
    runner_contribution: float = 0.0
    average_mfe_pct: float = 0.0
    average_mae_pct: float = 0.0
    best_mfe_pct: float = 0.0
    worst_mae_pct: float = 0.0


class ReplayUnderperformingBucket(StrictBaseModel):
    bucket_type: str
    key: str
    sample_size: int = Field(ge=0)
    expectancy: float = 0.0
    net_pnl_after_fees: float = 0.0
    average_hold_time_minutes: float = Field(ge=0.0, default=0.0)
    average_mfe_pct: float = 0.0
    average_mae_pct: float = 0.0
    disable_candidate: bool = True
    reasons: list[str] = Field(default_factory=list)


class ReplayParameterRecommendation(StrictBaseModel):
    status: Literal["ready", "fallback_neutral", "insufficient_data"] = "fallback_neutral"
    logic_variant: Literal["baseline_old", "improved"] | None = None
    sample_size: int = Field(ge=0, default=0)
    recommendation_basis: dict[str, str] = Field(default_factory=dict)
    risk_pct_multiplier: float = Field(default=1.0, ge=0.1, le=2.0)
    leverage_multiplier: float = Field(default=1.0, ge=0.1, le=2.0)
    max_chase_bps: float | None = Field(default=None, ge=0.0, le=500.0)
    entry_mode_preference: Literal["breakout_confirm", "pullback_confirm", "immediate", "none"] | None = None
    partial_tp_rr: float | None = Field(default=None, ge=0.1, le=10.0)
    partial_tp_size_pct: float | None = Field(default=None, gt=0.0, le=1.0)
    time_stop_minutes: int | None = Field(default=None, ge=1, le=1440)
    trailing_aggressiveness: Literal["defensive", "balanced", "patient"] = "balanced"
    disable_candidate: bool = False
    rationale: list[str] = Field(default_factory=list)
    adaptive_signal_context_patch: dict[str, Any] = Field(default_factory=dict)
    risk_context_patch: dict[str, Any] = Field(default_factory=dict)


class ReplayVariantReport(StrictBaseModel):
    logic_variant: Literal["baseline_old", "improved"]
    title: str
    data_source_type: Literal["synthetic_seed", "binance_futures_klines"]
    summary: ReplayMetricSummary
    recent_window_summary: ReplayMetricSummary
    by_symbol: list[ReplayBreakdownEntry] = Field(default_factory=list)
    by_timeframe: list[ReplayBreakdownEntry] = Field(default_factory=list)
    by_scenario: list[ReplayBreakdownEntry] = Field(default_factory=list)
    by_regime: list[ReplayBreakdownEntry] = Field(default_factory=list)
    by_trend_alignment: list[ReplayBreakdownEntry] = Field(default_factory=list)
    by_execution_policy_profile: list[ReplayBreakdownEntry] = Field(default_factory=list)
    by_entry_mode: list[ReplayBreakdownEntry] = Field(default_factory=list)
    by_rationale_code: list[ReplayBreakdownEntry] = Field(default_factory=list)
    walk_forward_recommendation: ReplayParameterRecommendation | None = None
    underperforming_buckets: list[ReplayUnderperformingBucket] = Field(default_factory=list)


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
    scenario_comparison: list[ReplayComparisonEntry] = Field(default_factory=list)
    regime_comparison: list[ReplayComparisonEntry] = Field(default_factory=list)
    trend_alignment_comparison: list[ReplayComparisonEntry] = Field(default_factory=list)
    execution_policy_profile_comparison: list[ReplayComparisonEntry] = Field(default_factory=list)
    entry_mode_comparison: list[ReplayComparisonEntry] = Field(default_factory=list)
    rationale_comparison: list[ReplayComparisonEntry] = Field(default_factory=list)
    recent_walk_forward_recommendation: ReplayParameterRecommendation | None = None
    underperforming_buckets: list[ReplayUnderperformingBucket] = Field(default_factory=list)


class RulePruningBucketEntry(StrictBaseModel):
    bucket_key: str
    symbol: str
    timeframe: str
    scenario: str
    regime: str
    entry_mode: str
    execution_policy_profile: str
    decisions: int = Field(ge=0)
    traded_decisions: int = Field(ge=0)
    expectancy: float = 0.0
    net_pnl_after_fees: float = 0.0
    avg_signed_slippage_bps: float = 0.0
    hold_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    late_trigger_ratio: float = Field(ge=0.0, le=1.0, default=0.0)
    failure_cluster_hit_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    classification: Literal["keep", "kill", "simplify"] = "simplify"
    reasons: list[str] = Field(default_factory=list)


class RulePruningCandidate(StrictBaseModel):
    rule_key: str
    sample_size: int = Field(ge=0)
    traded_decisions: int = Field(ge=0)
    expectancy: float = 0.0
    net_pnl_after_fees: float = 0.0
    avg_signed_slippage_bps: float = 0.0
    hold_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    late_trigger_ratio: float = Field(ge=0.0, le=1.0, default=0.0)
    failure_cluster_hit_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    classification: Literal["keep", "kill", "simplify"] = "simplify"
    reasons: list[str] = Field(default_factory=list)
    recommendation: str = ""


class RulePruningReportResponse(StrictBaseModel):
    generated_at: datetime
    lookback_days: int = Field(ge=1, le=365)
    decisions_analyzed: int = Field(ge=0)
    bucket_reports: list[RulePruningBucketEntry] = Field(default_factory=list)
    keep_list: list[RulePruningCandidate] = Field(default_factory=list)
    kill_list: list[RulePruningCandidate] = Field(default_factory=list)
    simplify_list: list[RulePruningCandidate] = Field(default_factory=list)
    next_cycle_candidates: list[RulePruningCandidate] = Field(default_factory=list)


class SkipQualityReasonEntry(StrictBaseModel):
    skip_reason: str
    events: int = Field(ge=0)
    evaluated_events: int = Field(ge=0)
    pending_events: int = Field(ge=0)
    avg_followup_return: float = 0.0
    would_have_hit_tp_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    would_have_hit_sl_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    would_have_reached_0_5r_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    avg_skip_quality_score: float = Field(ge=0.0, le=1.0, default=0.0)
    good_skip_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    overconservative_rate: float = Field(ge=0.0, le=1.0, default=0.0)


class SkipQualityReportResponse(StrictBaseModel):
    generated_at: datetime
    lookback_days: int = Field(ge=1, le=365)
    total_events: int = Field(ge=0)
    evaluated_events: int = Field(ge=0)
    pending_events: int = Field(ge=0)
    reason_reports: list[SkipQualityReasonEntry] = Field(default_factory=list)
    no_trade_zone_summary: SkipQualityReasonEntry | None = None
    meta_gate_summary: SkipQualityReasonEntry | None = None
    breadth_veto_summary: SkipQualityReasonEntry | None = None
    disable_bucket_summary: SkipQualityReasonEntry | None = None


class CapitalEfficiencyBucketEntry(StrictBaseModel):
    bucket_key: str
    symbol: str
    timeframe: str
    scenario: str
    regime: str
    entry_mode: str
    execution_policy_profile: str
    decisions: int = Field(ge=0)
    traded_decisions: int = Field(ge=0)
    total_exposure_hours: float = Field(ge=0.0, default=0.0)
    gross_pnl: float = 0.0
    net_pnl_after_fees: float = 0.0
    pnl_per_exposure_hour: float = 0.0
    net_pnl_after_fees_per_hour: float = 0.0
    average_time_to_0_25r_minutes: float | None = None
    average_time_to_0_5r_minutes: float | None = None
    average_time_to_fail_minutes: float | None = None
    reached_0_25r_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    reached_0_5r_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    fail_before_0_25r_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    capital_slot_occupancy_efficiency: float = Field(ge=0.0, default=0.0)
    efficiency_classification: Literal["efficient", "neutral", "inefficient"] = "neutral"
    reasons: list[str] = Field(default_factory=list)


class CapitalEfficiencyReportResponse(StrictBaseModel):
    generated_at: datetime
    lookback_days: int = Field(ge=1, le=365)
    decisions_analyzed: int = Field(ge=0)
    traded_decisions: int = Field(ge=0)
    total_exposure_hours: float = Field(ge=0.0, default=0.0)
    bucket_reports: list[CapitalEfficiencyBucketEntry] = Field(default_factory=list)
    efficient_bucket_keys: list[str] = Field(default_factory=list)
    inefficient_bucket_keys: list[str] = Field(default_factory=list)


class StrategyEngineBucketEntry(StrictBaseModel):
    bucket_key: str
    strategy_engine: str
    symbol: str
    timeframe: str
    scenario: str
    regime: str
    trend_alignment: str
    entry_mode: str
    execution_policy_profile: str
    session_label: str
    time_of_day_bucket: str
    decisions: int = Field(ge=0)
    traded_decisions: int = Field(ge=0)
    expectancy: float = 0.0
    net_pnl_after_fees: float = 0.0
    avg_signed_slippage_bps: float = 0.0
    average_time_to_profit_minutes: float | None = None
    average_drawdown_impact: float = 0.0
    efficiency_score: float = 0.0
    classification: Literal["strong", "mixed", "weak"] = "mixed"
    reasons: list[str] = Field(default_factory=list)
    latest_decision_at: datetime | None = None


class StrategyEngineReportResponse(StrictBaseModel):
    generated_at: datetime
    lookback_days: int = Field(ge=1, le=365)
    decisions_analyzed: int = Field(ge=0)
    traded_decisions: int = Field(ge=0)
    bucket_reports: list[StrategyEngineBucketEntry] = Field(default_factory=list)
    strong_engine_bucket_keys: list[str] = Field(default_factory=list)
    weak_engine_bucket_keys: list[str] = Field(default_factory=list)


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
    rollout_mode: RolloutMode = "paper"
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
    approval_control_blocked_reasons: list[str] = Field(default_factory=list)
    live_arm_disabled: bool = False
    live_arm_disable_reason_code: str | None = None
    live_arm_disable_reason: str | None = None
    current_drawdown_state: str = "normal"
    drawdown_state_entered_at: datetime | None = None
    drawdown_transition_reason: str | None = None
    drawdown_policy_adjustments: dict[str, Any] = Field(default_factory=dict)


class OperationalStatusPayload(StrictBaseModel):
    live_trading_enabled: bool = False
    rollout_mode: RolloutMode = "paper"
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
    current_drawdown_state: str = "normal"
    drawdown_state_entered_at: datetime | None = None
    drawdown_transition_reason: str | None = None
    drawdown_policy_adjustments: dict[str, Any] = Field(default_factory=dict)
    control_status_summary: ControlStatusSummary = Field(default_factory=ControlStatusSummary)
    user_stream_summary: dict[str, Any] = Field(default_factory=dict)
    reconciliation_summary: dict[str, Any] = Field(default_factory=dict)
    candidate_selection_summary: dict[str, Any] = Field(default_factory=dict)
    operator_alert: dict[str, Any] = Field(default_factory=dict)
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
    rollout_mode: RolloutMode = "paper"
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
    operator_alert: dict[str, Any] = Field(default_factory=dict)


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
    holding_profile: HoldingProfile | None = None
    holding_profile_reason: str | None = None
    assigned_slot: str | None = None
    candidate_weight: float | None = None
    capacity_reason: str | None = None
    portfolio_slot_soft_cap_applied: bool = False
    intent_family: IntentFamily = "unknown"
    management_action: ManagementAction = "none"
    legacy_semantics_preserved: bool = False
    analytics_excluded_from_entry_stats: bool = False
    last_ai_trigger_reason: AITriggerReason | None = None
    last_ai_invoked_at: datetime | None = None
    next_ai_review_due_at: datetime | None = None
    trigger_deduped: bool = False
    trigger_fingerprint: str | None = None
    last_ai_skip_reason: str | None = None
    event_risk_acknowledgement: str | None = None
    confidence_penalty_reason: str | None = None
    scenario_note: str | None = None
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
    holding_profile: HoldingProfile = "scalp"
    holding_profile_reason: str | None = None
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
    blocked_reason: str | None = None
    degraded_reason: str | None = None
    approval_required_reason: str | None = None
    policy_source: OperatorPolicySource = "none"
    evaluated_operator_policy: EvaluatedOperatorPolicyPayload | None = None
    approved_risk_pct: float | None = None
    approved_leverage: float | None = None
    raw_projected_notional: float | None = None
    approved_projected_notional: float | None = None
    approved_quantity: float | None = None
    auto_resized_entry: bool = False
    size_adjustment_ratio: float | None = None
    auto_resize_reason: str | None = None
    holding_profile: HoldingProfile | None = None
    holding_profile_reason: str | None = None
    assigned_slot: str | None = None
    candidate_weight: float | None = None
    capacity_reason: str | None = None
    portfolio_slot_soft_cap_applied: bool = False
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
    recent_fills: list[OperatorExecutionFillSummary] = Field(default_factory=list)


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
    holding_profile: HoldingProfile | None = None
    holding_profile_reason: str | None = None
    initial_stop_type: str | None = None
    ai_stop_management_allowed: bool | None = None
    hard_stop_active: bool | None = None
    stop_widening_allowed: bool | None = None


class OperatorCandidateSelectionSnapshot(StrictBaseModel):
    symbol: str | None = None
    selected: bool | None = None
    selection_reason: str | None = None
    selected_reason: str | None = None
    rejected_reason: str | None = None
    strategy_engine: str | None = None
    holding_profile: HoldingProfile | None = None
    holding_profile_reason: str | None = None
    assigned_slot: str | None = None
    candidate_weight: float | None = None
    capacity_reason: str | None = None
    blocked_reason_codes: list[str] = Field(default_factory=list)
    portfolio_slot_soft_cap_applied: bool = False


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
    market_candle_time: datetime | None = None
    feature_input_delay_minutes: int | None = None
    feature_input_delay_threshold_minutes: int | None = None
    feature_input_delayed: bool = False
    market_context_summary: dict[str, Any] = Field(default_factory=dict)
    derivatives_summary: dict[str, Any] = Field(default_factory=dict)
    event_context_summary: dict[str, Any] = Field(default_factory=dict)
    event_operator_control: EventOperatorControlPayload | None = None
    ai_decision: OperatorDecisionSnapshot = Field(default_factory=OperatorDecisionSnapshot)
    pending_entry_plan: PendingEntryPlanSnapshot = Field(default_factory=PendingEntryPlanSnapshot)
    risk_guard: OperatorRiskSnapshot = Field(default_factory=OperatorRiskSnapshot)
    execution: OperatorExecutionSnapshot = Field(default_factory=OperatorExecutionSnapshot)
    open_position: OperatorPositionSummary = Field(default_factory=OperatorPositionSummary)
    protection_status: OperatorProtectionSummary = Field(default_factory=OperatorProtectionSummary)
    candidate_selection: OperatorCandidateSelectionSnapshot = Field(default_factory=OperatorCandidateSelectionSnapshot)
    blocked_reasons: list[str] = Field(default_factory=list)
    live_execution_ready: bool = False
    stale_flags: list[str] = Field(default_factory=list)
    last_updated_at: datetime | None = None
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


class MetaGateResult(StrictBaseModel):
    gate_decision: Literal["pass", "soft_pass", "reject"] = "pass"
    expected_hit_probability: float = Field(ge=0.0, le=1.0, default=0.5)
    expected_time_to_profit_minutes: int | None = Field(default=None, ge=1, le=1440)
    reject_reason_codes: list[str] = Field(default_factory=list)
    confidence_adjustment: float = 0.0
    risk_multiplier: float = Field(default=1.0, ge=0.0, le=1.0)
    leverage_multiplier: float = Field(default=1.0, ge=0.0, le=1.0)
    notional_multiplier: float = Field(default=1.0, ge=0.0, le=1.0)
    components: dict[str, Any] = Field(default_factory=dict)


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
    blocked_reason: str | None = None
    degraded_reason: str | None = None
    approval_required_reason: str | None = None
    policy_source: OperatorPolicySource = "none"
    evaluated_operator_policy: EvaluatedOperatorPolicyPayload | None = None
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
    def _backfill_compatibility_fields(self) -> RiskCheckResult:
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
    holding_profile: HoldingProfile = "scalp"
    holding_profile_reason: str | None = None
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


class DerivativesContextPayload(StrictBaseModel):
    source: Literal["binance_public", "seed_fallback", "unavailable"] = "unavailable"
    fallback_used: bool = False
    fetch_failed: bool = False
    open_interest: float | None = Field(default=None, ge=0.0)
    open_interest_change_pct: float | None = None
    funding_rate: float | None = None
    taker_buy_sell_imbalance: float | None = None
    perp_basis_bps: float | None = None
    crowding_bias: float | None = None
    top_trader_long_short_ratio: float | None = Field(default=None, ge=0.0)
    best_bid: float | None = Field(default=None, gt=0.0)
    best_ask: float | None = Field(default=None, gt=0.0)
    spread_bps: float | None = Field(default=None, ge=0.0)
    spread_stress_score: float | None = Field(default=None, ge=0.0)


class MacroEventPayload(StrictBaseModel):
    event_at: datetime
    event_name: str
    importance: MacroEventImportance | None = None
    affected_assets: list[str] = Field(default_factory=list)
    event_bias: EventBias | None = None
    minutes_to_event: int | None = None
    risk_window_before_minutes: int = Field(default=60, ge=0, le=10080)
    risk_window_after_minutes: int = Field(default=30, ge=0, le=10080)
    active_risk_window: bool = False
    enrichment_vendors: list[EventSourceVendor] = Field(default_factory=list)
    release_enrichment: dict[str, dict[str, Any]] = Field(default_factory=dict)


class EventContextPayload(StrictBaseModel):
    source_status: EventSourceStatus = "unavailable"
    source_provenance: EventSourceProvenance | None = None
    source_vendor: EventSourceVendor | None = None
    generated_at: datetime
    is_stale: bool = False
    is_complete: bool = False
    next_event_at: datetime | None = None
    next_event_name: str | None = None
    next_event_importance: MacroEventImportance | None = None
    minutes_to_next_event: int | None = None
    active_risk_window: bool = False
    affected_assets: list[str] = Field(default_factory=list)
    event_bias: EventBias | None = None
    enrichment_vendors: list[EventSourceVendor] = Field(default_factory=list)
    events: list[MacroEventPayload] = Field(default_factory=list)


class OperatorEventItemPayload(StrictBaseModel):
    event_at: datetime
    event_name: str
    importance: OperatorEventImportance = "unknown"
    affected_assets: list[str] = Field(default_factory=list)
    minutes_to_event: int | None = None

    _normalize_event_at = field_validator("event_at", mode="before")(_coerce_required_aware_datetime)


class OperatorActiveRiskWindowPayload(StrictBaseModel):
    is_active: bool = False
    event_name: str | None = None
    event_importance: OperatorEventImportance = "unknown"
    start_at: datetime | None = None
    end_at: datetime | None = None
    affected_assets: list[str] = Field(default_factory=list)
    summary_note: str | None = None

    _normalize_start_at = field_validator("start_at", mode="before")(_coerce_aware_datetime)
    _normalize_end_at = field_validator("end_at", mode="before")(_coerce_aware_datetime)


class OperatorEventContextPayload(StrictBaseModel):
    source_status: OperatorEventSourceStatus = "unavailable"
    source_provenance: EventSourceProvenance | None = None
    source_vendor: EventSourceVendor | None = None
    generated_at: datetime
    is_stale: bool = False
    is_complete: bool = False
    active_risk_window: bool = False
    active_risk_window_detail: OperatorActiveRiskWindowPayload | None = None
    next_event_at: datetime | None = None
    next_event_name: str | None = None
    next_event_importance: OperatorEventImportance = "unknown"
    minutes_to_next_event: int | None = None
    upcoming_events: list[OperatorEventItemPayload] = Field(default_factory=list)
    affected_assets: list[str] = Field(default_factory=list)
    enrichment_vendors: list[EventSourceVendor] = Field(default_factory=list)
    summary_note: str | None = None

    _normalize_generated_at = field_validator("generated_at", mode="before")(_coerce_required_aware_datetime)
    _normalize_next_event_at = field_validator("next_event_at", mode="before")(_coerce_aware_datetime)


class AIEventViewPayload(StrictBaseModel):
    ai_bias: OperatorEventBias = "unknown"
    ai_risk_state: OperatorEventRiskState = "unknown"
    ai_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    scenario_note: str | None = None
    confidence_penalty_reason: str | None = None
    source_state: AIEventSourceState = "unknown"


class OperatorEventViewPayload(StrictBaseModel):
    operator_bias: OperatorEventBias = "unknown"
    operator_risk_state: OperatorEventRiskState = "unknown"
    applies_to_symbols: list[str] = Field(default_factory=list)
    horizon: str | None = Field(default=None, min_length=1, max_length=40)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    enforcement_mode: OperatorEventEnforcementMode = "observe_only"
    note: str | None = None
    created_by: str = "unknown"
    updated_at: datetime | None = None

    _normalize_valid_from = field_validator("valid_from", mode="before")(_coerce_aware_datetime)
    _normalize_valid_to = field_validator("valid_to", mode="before")(_coerce_aware_datetime)
    _normalize_updated_at = field_validator("updated_at", mode="before")(_coerce_aware_datetime)

    @model_validator(mode="after")
    def _validate_time_range(self) -> OperatorEventViewPayload:
        if self.valid_from is not None and self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be later than valid_from")
        return self


class ManualNoTradeWindowScopePayload(StrictBaseModel):
    scope_type: Literal["global", "symbols"] = "global"
    symbols: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _normalize_scope(self) -> ManualNoTradeWindowScopePayload:
        normalized = []
        for item in self.symbols:
            symbol = str(item or "").strip().upper()
            if symbol and symbol not in normalized:
                normalized.append(symbol)
        self.symbols = normalized if self.scope_type == "symbols" else []
        if self.scope_type == "symbols" and not self.symbols:
            raise ValueError("symbols scope requires at least one symbol")
        return self


class ManualNoTradeWindowPayload(StrictBaseModel):
    window_id: str = Field(min_length=8, max_length=64)
    scope: ManualNoTradeWindowScopePayload = Field(default_factory=ManualNoTradeWindowScopePayload)
    start_at: datetime
    end_at: datetime
    reason: str = Field(min_length=1, max_length=240)
    auto_resume: bool = False
    require_manual_rearm: bool = False
    created_by: str = "unknown"
    updated_at: datetime | None = None
    is_active: bool = False

    _normalize_start_at = field_validator("start_at", mode="before")(_coerce_required_aware_datetime)
    _normalize_end_at = field_validator("end_at", mode="before")(_coerce_required_aware_datetime)
    _normalize_updated_at = field_validator("updated_at", mode="before")(_coerce_aware_datetime)

    @model_validator(mode="after")
    def _validate_window_range(self) -> ManualNoTradeWindowPayload:
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be later than start_at")
        return self


class AlignmentDecisionPayload(StrictBaseModel):
    ai_bias: OperatorEventBias = "unknown"
    operator_bias: OperatorEventBias = "unknown"
    ai_risk_state: OperatorEventRiskState = "unknown"
    operator_risk_state: OperatorEventRiskState = "unknown"
    alignment_status: OperatorEventAlignmentStatus = "insufficient_data"
    reason_codes: list[str] = Field(default_factory=list)
    effective_policy_preview: OperatorEffectivePolicyPreview = "insufficient_data"
    evaluated_at: datetime

    _normalize_evaluated_at = field_validator("evaluated_at", mode="before")(_coerce_required_aware_datetime)


class EvaluatedOperatorPolicyPayload(StrictBaseModel):
    operator_view_active: bool = False
    matched_window_id: str | None = Field(default=None, min_length=1, max_length=64)
    alignment_status: OperatorEventAlignmentStatus = "insufficient_data"
    enforcement_mode: OperatorEventEnforcementMode = "observe_only"
    reason_codes: list[str] = Field(default_factory=list)
    effective_policy_preview: OperatorEffectivePolicyPreview = "insufficient_data"
    event_source_status: OperatorEventSourceStatus = "unavailable"
    event_source_stale: bool = False
    evaluated_at: datetime

    _normalize_evaluated_at = field_validator("evaluated_at", mode="before")(_coerce_required_aware_datetime)


class EventPolicyEvaluationPayload(StrictBaseModel):
    alignment_decision: AlignmentDecisionPayload
    evaluated_operator_policy: EvaluatedOperatorPolicyPayload
    blocked_reason: str | None = None
    degraded_reason: str | None = None
    approval_required_reason: str | None = None
    policy_source: OperatorPolicySource = "none"


class EventOperatorControlPayload(StrictBaseModel):
    event_context: OperatorEventContextPayload
    ai_event_view: AIEventViewPayload
    operator_event_view: OperatorEventViewPayload
    alignment_decision: AlignmentDecisionPayload
    evaluated_operator_policy: EvaluatedOperatorPolicyPayload | None = None
    blocked_reason: str | None = None
    degraded_reason: str | None = None
    approval_required_reason: str | None = None
    policy_source: OperatorPolicySource = "none"
    manual_no_trade_windows: list[ManualNoTradeWindowPayload] = Field(default_factory=list)
    effective_policy_preview: OperatorEffectivePolicyPreview = "insufficient_data"


class RegimeSummaryPayload(StrictBaseModel):
    primary_regime: Literal["bullish", "bearish", "range", "transition"] = "transition"
    trend_alignment: Literal["bullish_aligned", "bearish_aligned", "mixed", "range"] = "mixed"
    volatility_regime: Literal["compressed", "normal", "expanded"] = "normal"
    volume_regime: Literal["weak", "normal", "strong"] = "normal"
    momentum_state: Literal["strengthening", "stable", "weakening", "overextended"] = "stable"
    weak_volume: bool = False
    momentum_weakening: bool = False


class DerivativesSummaryPayload(StrictBaseModel):
    available: bool = False
    source: Literal["binance_public", "seed_fallback", "unavailable"] = "unavailable"
    funding_bias: Literal["long_headwind", "short_headwind", "neutral", "unknown"] = "unknown"
    basis_bias: Literal["bullish", "bearish", "neutral", "unknown"] = "unknown"
    taker_flow_alignment: Literal["bullish", "bearish", "neutral", "unknown"] = "unknown"
    long_alignment_score: float = Field(ge=0.0, le=1.0, default=0.5)
    short_alignment_score: float = Field(ge=0.0, le=1.0, default=0.5)
    crowded_long_risk: bool = False
    crowded_short_risk: bool = False
    spread_headwind: bool = False
    spread_stress: bool = False
    oi_expanding_with_price: bool = False
    oi_falling_on_breakout: bool = False


class LeadLagSummaryPayload(StrictBaseModel):
    available: bool = False
    leader_bias: Literal["bullish", "bearish", "mixed", "neutral", "unknown"] = "unknown"
    reference_symbols: list[str] = Field(default_factory=list)
    bullish_alignment_score: float = Field(ge=0.0, le=1.0, default=0.5)
    bearish_alignment_score: float = Field(ge=0.0, le=1.0, default=0.5)
    bullish_breakout_confirmed: bool = False
    bearish_breakout_confirmed: bool = False
    bullish_pullback_supported: bool = False
    bearish_pullback_supported: bool = False
    bullish_continuation_supported: bool = False
    bearish_continuation_supported: bool = False
    strong_reference_confirmation: bool = False
    weak_reference_confirmation: bool = False


class EventContextSummaryPayload(StrictBaseModel):
    source_status: EventSourceStatus = "unavailable"
    source_provenance: EventSourceProvenance | None = None
    source_vendor: EventSourceVendor | None = None
    next_event_name: str | None = None
    next_event_importance: MacroEventImportance | None = None
    minutes_to_next_event: int | None = None
    active_risk_window: bool = False
    event_bias: EventBias | None = None
    enrichment_vendors: list[EventSourceVendor] = Field(default_factory=list)


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
    derivatives_context: DerivativesContextPayload = Field(default_factory=DerivativesContextPayload)
    event_context: EventContextPayload = Field(
        default_factory=lambda: EventContextPayload(generated_at=datetime.now(UTC).replace(tzinfo=None))
    )


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


class DerivativesFeatureContext(StrictBaseModel):
    available: bool = False
    source: Literal["binance_public", "seed_fallback", "unavailable"] = "unavailable"
    fallback_used: bool = False
    fetch_failed: bool = False
    open_interest: float | None = Field(default=None, ge=0.0)
    open_interest_change_pct: float | None = None
    funding_rate: float | None = None
    taker_buy_sell_imbalance: float | None = None
    perp_basis_bps: float | None = None
    crowding_bias: float | None = None
    top_trader_long_short_ratio: float | None = Field(default=None, ge=0.0)
    top_trader_crowding_bias: float | None = Field(default=None, ge=-1.0, le=1.0)
    best_bid: float | None = Field(default=None, gt=0.0)
    best_ask: float | None = Field(default=None, gt=0.0)
    spread_bps: float | None = Field(default=None, ge=0.0)
    spread_stress_score: float | None = Field(default=None, ge=0.0)
    oi_expanding_with_price: bool = False
    oi_falling_on_breakout: bool = False
    crowded_long_risk: bool = False
    crowded_short_risk: bool = False
    spread_headwind: bool = False
    breakout_spread_headwind: bool = False
    spread_stress: bool = False
    top_trader_long_crowded: bool = False
    top_trader_short_crowded: bool = False
    taker_flow_alignment: Literal["bullish", "bearish", "neutral", "unknown"] = "unknown"
    funding_bias: Literal["long_headwind", "short_headwind", "neutral", "unknown"] = "unknown"
    basis_bias: Literal["bullish", "bearish", "neutral", "unknown"] = "unknown"
    entry_veto_reason_codes: list[str] = Field(default_factory=list)
    breakout_veto_reason_codes: list[str] = Field(default_factory=list)
    long_discount_magnitude: float = Field(ge=0.0, le=1.0, default=0.0)
    short_discount_magnitude: float = Field(ge=0.0, le=1.0, default=0.0)
    long_alignment_score: float = Field(ge=0.0, le=1.0, default=0.5)
    short_alignment_score: float = Field(ge=0.0, le=1.0, default=0.5)


class LeadMarketReferencePayload(StrictBaseModel):
    symbol: str
    timeframe: str
    trend_score: float = 0.0
    momentum_score: float = 0.0
    breakout_direction: Literal["up", "down", "none"] = "none"
    pullback_state: Literal[
        "bullish_continuation",
        "bearish_continuation",
        "bullish_pullback",
        "bearish_pullback",
        "countertrend",
        "range",
        "unclear",
    ] = "unclear"
    primary_regime: Literal["bullish", "bearish", "range", "transition"] = "transition"
    trend_alignment: Literal["bullish_aligned", "bearish_aligned", "mixed", "range"] = "mixed"
    weak_volume: bool = False
    momentum_state: Literal["strengthening", "stable", "weakening", "overextended"] = "stable"
    volume_ratio: float = Field(ge=0.0, default=0.0)


class LeadLagFeatureContext(StrictBaseModel):
    available: bool = False
    leader_bias: Literal["bullish", "bearish", "mixed", "neutral", "unknown"] = "unknown"
    reference_symbols: list[str] = Field(default_factory=list)
    missing_reference_symbols: list[str] = Field(default_factory=list)
    bullish_alignment_score: float = Field(ge=0.0, le=1.0, default=0.5)
    bearish_alignment_score: float = Field(ge=0.0, le=1.0, default=0.5)
    bullish_breakout_confirmed: bool = False
    bearish_breakout_confirmed: bool = False
    bullish_breakout_ahead: bool = False
    bearish_breakout_ahead: bool = False
    bullish_pullback_supported: bool = False
    bearish_pullback_supported: bool = False
    bullish_continuation_supported: bool = False
    bearish_continuation_supported: bool = False
    strong_reference_confirmation: bool = False
    weak_reference_confirmation: bool = False
    references: dict[str, LeadMarketReferencePayload] = Field(default_factory=dict)


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
    derivatives: DerivativesFeatureContext = Field(default_factory=DerivativesFeatureContext)
    lead_lag: LeadLagFeatureContext = Field(default_factory=LeadLagFeatureContext)
    event_context: EventContextPayload = Field(
        default_factory=lambda: EventContextPayload(generated_at=datetime.now(UTC).replace(tzinfo=None))
    )
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
    operator_alert: dict[str, Any] = Field(default_factory=dict)
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
    ai_backstop_enabled_override: bool | None = None
    ai_backstop_interval_minutes_override: int | None = Field(default=None, ge=15, le=10080)


class SymbolEffectiveCadence(StrictBaseModel):
    symbol: str
    enabled: bool = True
    uses_global_defaults: bool = True
    timeframe: str
    market_refresh_interval_minutes: int = Field(ge=1, le=1440)
    position_management_interval_seconds: int = Field(ge=30, le=86400)
    decision_cycle_interval_minutes: int = Field(ge=1, le=1440)
    ai_call_interval_minutes: int = Field(ge=5, le=1440)
    ai_backstop_enabled: bool = True
    ai_backstop_interval_minutes: int = Field(ge=15, le=10080)
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
    control_status_summary: ControlStatusSummary = Field(default_factory=ControlStatusSummary)
    live_trading_enabled: bool
    rollout_mode: RolloutMode = "paper"
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
    event_operator_control: EventOperatorControlPayload | None = None
    user_stream_summary: dict[str, Any] = Field(default_factory=dict)
    reconciliation_summary: dict[str, Any] = Field(default_factory=dict)
    candidate_selection_summary: dict[str, Any] = Field(default_factory=dict)
    operator_alert: dict[str, Any] = Field(default_factory=dict)
    default_symbol: str
    tracked_symbols: list[str]
    default_timeframe: str
    exchange_sync_interval_seconds: int
    market_refresh_interval_minutes: int
    position_management_interval_seconds: int
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
    event_source_provider: Literal["stub", "fred"] | None = None
    event_source_api_url: str | None = None
    event_source_timeout_seconds: float | None = None
    event_source_default_assets: list[str] = Field(default_factory=list)
    event_source_fred_release_ids: list[int] = Field(default_factory=list)
    mode: str
    openai_api_key_configured: bool
    binance_api_key_configured: bool
    binance_api_secret_configured: bool
    event_source_api_key_configured: bool
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


class AppSettingsViewResponse(StrictBaseModel):
    id: int
    can_enter_new_position: bool = False
    blocked_reasons: list[str] = Field(default_factory=list)
    live_trading_enabled: bool
    rollout_mode: RolloutMode = "paper"
    exchange_submit_allowed: bool = False
    limited_live_max_notional: float | None = None
    live_trading_env_enabled: bool
    manual_live_approval: bool
    live_execution_armed: bool
    live_execution_armed_until: datetime | None = None
    live_approval_window_minutes: int
    live_execution_ready: bool
    trading_paused: bool
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
    auto_resume_last_blockers: list[str] = Field(default_factory=list)
    latest_blocked_reasons: list[str] = Field(default_factory=list)
    control_status_summary: ControlStatusSummary = Field(default_factory=ControlStatusSummary)
    reconciliation_summary: dict[str, Any] = Field(default_factory=dict)
    operator_alert: dict[str, Any] = Field(default_factory=dict)
    mode: str
    operating_state: str = "TRADABLE"
    protection_recovery_status: str = "idle"
    protection_recovery_active: bool = False
    protection_recovery_failure_count: int = 0
    missing_protection_symbols: list[str] = Field(default_factory=list)
    missing_protection_items: dict[str, list[str]] = Field(default_factory=dict)
    pause_severity: str | None = None
    pause_recovery_class: str | None = None
    default_symbol: str
    tracked_symbols: list[str]
    default_timeframe: str
    exchange_sync_interval_seconds: int
    market_refresh_interval_minutes: int
    position_management_interval_seconds: int
    symbol_cadence_overrides: list[SymbolCadenceOverride] = Field(default_factory=list)
    max_leverage: float
    max_risk_per_trade: float
    max_daily_loss: float
    max_consecutive_losses: int
    stale_market_seconds: int
    slippage_threshold_pct: float
    adaptive_signal_enabled: bool
    position_management_enabled: bool
    break_even_enabled: bool
    atr_trailing_stop_enabled: bool
    partial_take_profit_enabled: bool
    holding_edge_decay_enabled: bool
    reduce_on_regime_shift_enabled: bool
    adaptive_signal_summary: dict[str, Any] = Field(default_factory=dict)
    position_management_summary: dict[str, Any] = Field(default_factory=dict)
    event_operator_control: EventOperatorControlPayload | None = None
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
    event_source_provider: Literal["stub", "fred"] | None = None
    event_source_api_url: str | None = None
    event_source_timeout_seconds: float | None = None
    event_source_default_assets: list[str] = Field(default_factory=list)
    event_source_fred_release_ids: list[int] = Field(default_factory=list)
    openai_api_key_configured: bool
    binance_api_key_configured: bool
    binance_api_secret_configured: bool
    event_source_api_key_configured: bool


class AppSettingsCadenceResponse(StrictBaseModel):
    items: list[SymbolEffectiveCadence] = Field(default_factory=list)


class AppSettingsAIUsageResponse(StrictBaseModel):
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
    recent_ai_failure_reasons: list[str] = Field(default_factory=list)
    observed_monthly_ai_calls_projection: int
    observed_monthly_ai_calls_projection_breakdown: dict[str, int] = Field(default_factory=dict)
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
    event_source_provider: Literal["stub", "fred"] | None = None
    event_source_api_url: str | None = Field(default=None, max_length=255)
    event_source_timeout_seconds: float | None = Field(default=None, ge=1.0, le=120.0)
    event_source_default_assets: list[str] = Field(default_factory=list)
    event_source_fred_release_ids: list[int] = Field(default_factory=list)
    openai_api_key: str | None = None
    binance_api_key: str | None = None
    binance_api_secret: str | None = None
    event_source_api_key: str | None = None
    clear_openai_api_key: bool = False
    clear_binance_api_key: bool = False
    clear_binance_api_secret: bool = False
    clear_event_source_api_key: bool = False


class OpenAIConnectionTestRequest(StrictBaseModel):
    api_key: str | None = None
    model: str = Field(min_length=1, max_length=80)


class BinanceConnectionTestRequest(StrictBaseModel):
    api_key: str | None = None
    api_secret: str | None = None
    testnet_enabled: bool = False
    symbol: str = Field(default="BTCUSDT", min_length=1, max_length=30)
    timeframe: str = Field(default="15m", min_length=1, max_length=20)


class FredConnectionTestRequest(StrictBaseModel):
    api_key: str | None = None
    api_url: str | None = Field(default=None, max_length=255)
    timeout_seconds: float | None = Field(default=None, ge=1.0, le=120.0)
    release_ids: list[int] = Field(default_factory=list)
    default_assets: list[str] = Field(default_factory=list)
    symbol: str = Field(default="BTCUSDT", min_length=1, max_length=30)
    timeframe: str = Field(default="15m", min_length=1, max_length=20)


class ConnectionTestResponse(StrictBaseModel):
    ok: bool
    provider: str
    message: str
    details: dict[str, Any]


class ManualLiveApprovalRequest(StrictBaseModel):
    minutes: int | None = Field(default=None, ge=0, le=240)


class OperatorEventViewRequest(StrictBaseModel):
    operator_bias: OperatorEventBias = "unknown"
    operator_risk_state: OperatorEventRiskState = "unknown"
    applies_to_symbols: list[str] = Field(default_factory=list)
    horizon: str | None = Field(default=None, min_length=1, max_length=40)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    enforcement_mode: OperatorEventEnforcementMode = "observe_only"
    note: str | None = None
    created_by: str = Field(default="operator-ui", min_length=1, max_length=80)

    _normalize_valid_from = field_validator("valid_from", mode="before")(_coerce_aware_datetime)
    _normalize_valid_to = field_validator("valid_to", mode="before")(_coerce_aware_datetime)

    @model_validator(mode="after")
    def _validate_time_range(self) -> OperatorEventViewRequest:
        if self.valid_from is not None and self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be later than valid_from")
        normalized_symbols: list[str] = []
        for item in self.applies_to_symbols:
            symbol = str(item or "").strip().upper()
            if symbol and symbol not in normalized_symbols:
                normalized_symbols.append(symbol)
        self.applies_to_symbols = normalized_symbols
        return self


class OperatorEventViewClearRequest(StrictBaseModel):
    created_by: str = Field(default="operator-ui", min_length=1, max_length=80)


class ManualNoTradeWindowRequest(StrictBaseModel):
    scope: ManualNoTradeWindowScopePayload = Field(default_factory=ManualNoTradeWindowScopePayload)
    start_at: datetime
    end_at: datetime
    reason: str = Field(min_length=1, max_length=240)
    auto_resume: bool = False
    require_manual_rearm: bool = False
    created_by: str = Field(default="operator-ui", min_length=1, max_length=80)

    _normalize_start_at = field_validator("start_at", mode="before")(_coerce_required_aware_datetime)
    _normalize_end_at = field_validator("end_at", mode="before")(_coerce_required_aware_datetime)

    @model_validator(mode="after")
    def _validate_window(self) -> ManualNoTradeWindowRequest:
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be later than start_at")
        return self


class ManualNoTradeWindowEndRequest(StrictBaseModel):
    end_at: datetime | None = None
    created_by: str = Field(default="operator-ui", min_length=1, max_length=80)

    _normalize_end_at = field_validator("end_at", mode="before")(_coerce_aware_datetime)


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
