from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timedelta
from math import sqrt
from uuid import uuid4

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from trading_mvp.enums import AgentRole, TriggerEvent
from trading_mvp.models import (
    AgentRun,
    Alert,
    Execution,
    FeatureSnapshot,
    MarketSnapshot,
    Order,
    PendingEntryPlan,
    PnLSnapshot,
    Position,
    RiskCheck,
    SystemHealthEvent,
)
from trading_mvp.providers import build_model_provider
from trading_mvp.schemas import (
    AIReviewTriggerPayload,
    FeaturePayload,
    MarketSnapshotPayload,
    PendingEntryPlanSnapshot,
    RiskCheckResult,
    TradeDecision,
    TradeDecisionCandidate,
    TradeDecisionCandidateScore,
)
from trading_mvp.services.account import (
    account_snapshot_to_dict,
    get_latest_pnl_snapshot,
    get_open_positions,
)
from trading_mvp.services.adaptive_signal import build_adaptive_signal_context
from trading_mvp.services.agents import (
    ChiefReviewAgent,
    TradingDecisionAgent,
    build_trading_decision_input_payload,
    persist_agent_run,
)
from trading_mvp.services.ai_context import build_ai_decision_context
from trading_mvp.services.ai_prior_context import build_ai_prior_context
from trading_mvp.services.ai_usage import get_openai_call_gate
from trading_mvp.services.audit import (
    create_alert,
    normalize_correlation_ids,
    record_audit_event,
    record_health_event,
)
from trading_mvp.services.capital_efficiency import build_capital_efficiency_report
from trading_mvp.services.drawdown_state import (
    DRAWDOWN_STATE_CAUTION,
    DRAWDOWN_STATE_CONTAINMENT,
    build_drawdown_state_snapshot,
)
from trading_mvp.services.execution import (
    apply_position_management,
    execute_live_trade,
    sync_live_state,
)
from trading_mvp.services.features import (
    compute_features,
    persist_feature_snapshot,
    summarize_universe_breadth,
)
from trading_mvp.services.holding_profile import (
    evaluate_holding_profile,
    resolve_holding_profile_cadence_hint,
)
from trading_mvp.services.market_data import (
    build_lead_market_contexts,
    build_market_context,
    build_market_snapshot,
    persist_market_snapshot,
)
from trading_mvp.services.meta_gate import evaluate_meta_gate
from trading_mvp.services.pause_control import attempt_auto_resume
from trading_mvp.services.performance_reporting import _extract_analysis_context
from trading_mvp.services.position_management import build_position_management_context
from trading_mvp.services.risk import (
    HARD_MAX_GLOBAL_LEVERAGE,
    HARD_MAX_RISK_PER_TRADE,
    build_ai_risk_budget_context,
    evaluate_risk,
    get_symbol_leverage_cap,
    get_symbol_risk_tier,
)
from trading_mvp.services.rule_pruning import build_keep_kill_report
from trading_mvp.services.runtime_state import (
    PROTECTION_REQUIRED_STATE,
    build_sync_freshness_summary,
    get_drawdown_state_detail,
    get_unresolved_submission_guard,
    mark_sync_skipped,
    set_candidate_selection_detail,
    set_drawdown_state_detail,
    summarize_runtime_state,
)
from trading_mvp.services.settings import (
    build_operational_status_payload,
    get_effective_symbol_schedule,
    get_effective_symbol_settings,
    get_effective_symbols,
    get_or_create_settings,
    get_runtime_credentials,
    serialize_settings,
)
from trading_mvp.services.skip_quality import build_skip_quality_report, record_skip_event
from trading_mvp.services.strategy_engine_analytics import build_strategy_engine_bucket_report
from trading_mvp.services.strategy_engines import select_strategy_engine
from trading_mvp.time_utils import utcnow_naive

ACTIVE_ENTRY_PLAN_STATUS = "armed"
ENTRY_PLAN_WATCH_TIMEFRAME = "1m"
CADENCE_IDLE_MODE = "idle"
CADENCE_WATCH_MODE = "watch"
CADENCE_ACTIVE_POSITION_MODE = "active_position"
CADENCE_ARMED_ENTRY_PLAN_MODE = "armed_entry_plan"
CADENCE_HIGH_PRIORITY_RECOVERY_MODE = "high_priority_recovery"
ENTRY_PLAN_NON_STRUCTURAL_BLOCKERS = {
    "CHASE_LIMIT_EXCEEDED",
    "ENTRY_TRIGGER_NOT_MET",
    "SLIPPAGE_THRESHOLD_EXCEEDED",
}
SETUP_CLUSTER_DISABLED_REASON_CODE = "SETUP_CLUSTER_DISABLED"
SETUP_CLUSTER_LOOKBACK = 8
SETUP_CLUSTER_MIN_SAMPLE_SIZE = 4
SETUP_CLUSTER_EXPECTANCY_THRESHOLD = 0.0
SETUP_CLUSTER_NET_PNL_THRESHOLD = 0.0
SETUP_CLUSTER_LOSS_STREAK_THRESHOLD = 3
SETUP_CLUSTER_SIGNED_SLIPPAGE_BPS_THRESHOLD = 12.0
SETUP_CLUSTER_COOLDOWN_MINUTES = 180
SETUP_CLUSTER_HISTORY_LIMIT = 128
SETUP_CLUSTER_DISABLE_REASON_CODES = {
    "expectancy": "CLUSTER_NEGATIVE_EXPECTANCY",
    "loss_streak": "CLUSTER_LOSS_STREAK",
    "signed_slippage": "CLUSTER_ADVERSE_SIGNED_SLIPPAGE",
    "net_pnl": "CLUSTER_NET_PNL_AFTER_FEES_NEGATIVE",
}
SETUP_CLUSTER_EXEMPT_RATIONALE_CODES = {
    "PROTECTION_REQUIRED",
    "PROTECTION_RECOVERY",
    "PROTECTION_RESTORE",
}
PORTFOLIO_SLOT_ORDER = ("slot_1", "slot_2", "slot_3")
PORTFOLIO_SLOT_LABELS = {
    "slot_1": "high_conviction",
    "slot_2": "medium_conviction",
    "slot_3": "medium_conviction",
}
PORTFOLIO_SLOT_HIGH_CONVICTION_THRESHOLD = 0.68
PORTFOLIO_SLOT_MEDIUM_CONVICTION_THRESHOLD = 0.54
PORTFOLIO_SLOT_BASE_WEIGHTS = {
    "slot_1": 1.0,
    "slot_2": 0.72,
    "slot_3": 0.58,
}
PORTFOLIO_SLOT_POLICY_BASE = {
    "slot_1": {"risk_pct_multiplier": 1.0, "leverage_multiplier": 1.0, "notional_multiplier": 1.0},
    "slot_2": {"risk_pct_multiplier": 0.82, "leverage_multiplier": 0.9, "notional_multiplier": 0.78},
    "slot_3": {"risk_pct_multiplier": 0.68, "leverage_multiplier": 0.82, "notional_multiplier": 0.64},
}


def _decision_analysis_context(
    feature_payload,
    *,
    universe_breadth: dict[str, object] | None = None,
) -> dict[str, object]:
    regime = feature_payload.regime
    derivatives = feature_payload.derivatives
    context = {
        "regime": {
            "primary_regime": regime.primary_regime,
            "trend_alignment": regime.trend_alignment,
            "volatility_regime": regime.volatility_regime,
        },
        "flags": {
            "weak_volume": regime.weak_volume,
            "volatility_expanded": regime.volatility_regime == "expanded",
            "momentum_weakening": regime.momentum_weakening,
        },
        "derivatives": {
            "available": derivatives.available,
            "source": derivatives.source,
            "top_trader_long_short_ratio": derivatives.top_trader_long_short_ratio,
            "top_trader_crowding_bias": derivatives.top_trader_crowding_bias,
            "taker_flow_alignment": derivatives.taker_flow_alignment,
            "funding_bias": derivatives.funding_bias,
            "spread_bps": derivatives.spread_bps,
            "spread_stress_score": derivatives.spread_stress_score,
            "spread_headwind": derivatives.spread_headwind,
            "spread_stress": derivatives.spread_stress,
            "breakout_spread_headwind": derivatives.breakout_spread_headwind,
            "oi_expanding_with_price": derivatives.oi_expanding_with_price,
            "oi_falling_on_breakout": derivatives.oi_falling_on_breakout,
            "crowded_long_risk": derivatives.crowded_long_risk,
            "crowded_short_risk": derivatives.crowded_short_risk,
            "top_trader_long_crowded": derivatives.top_trader_long_crowded,
            "top_trader_short_crowded": derivatives.top_trader_short_crowded,
            "entry_veto_reason_codes": list(derivatives.entry_veto_reason_codes),
            "breakout_veto_reason_codes": list(derivatives.breakout_veto_reason_codes),
            "long_discount_magnitude": derivatives.long_discount_magnitude,
            "short_discount_magnitude": derivatives.short_discount_magnitude,
            "long_alignment_score": derivatives.long_alignment_score,
            "short_alignment_score": derivatives.short_alignment_score,
        },
        "lead_lag": {
            "available": feature_payload.lead_lag.available,
            "leader_bias": feature_payload.lead_lag.leader_bias,
            "reference_symbols": list(feature_payload.lead_lag.reference_symbols),
            "bullish_alignment_score": feature_payload.lead_lag.bullish_alignment_score,
            "bearish_alignment_score": feature_payload.lead_lag.bearish_alignment_score,
            "bullish_breakout_confirmed": feature_payload.lead_lag.bullish_breakout_confirmed,
            "bearish_breakout_confirmed": feature_payload.lead_lag.bearish_breakout_confirmed,
            "bullish_breakout_ahead": feature_payload.lead_lag.bullish_breakout_ahead,
            "bearish_breakout_ahead": feature_payload.lead_lag.bearish_breakout_ahead,
            "bullish_pullback_supported": feature_payload.lead_lag.bullish_pullback_supported,
            "bearish_pullback_supported": feature_payload.lead_lag.bearish_pullback_supported,
            "bullish_continuation_supported": feature_payload.lead_lag.bullish_continuation_supported,
            "bearish_continuation_supported": feature_payload.lead_lag.bearish_continuation_supported,
        },
    }
    if isinstance(universe_breadth, dict) and universe_breadth:
        context["universe_breadth"] = dict(universe_breadth)
    return context


def _clamp_score(value: float, *, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _as_dict(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _setup_cluster_scenario(decision: str, entry_mode: str | None, rationale_codes: list[str]) -> str:
    decision_code = str(decision or "").lower()
    if decision_code in {"reduce", "exit", "hold"}:
        return decision_code
    rationale_set = {str(code) for code in rationale_codes if code}
    if rationale_set & SETUP_CLUSTER_EXEMPT_RATIONALE_CODES:
        return "protection_restore"
    if str(entry_mode or "").lower() == "pullback_confirm" or any("PULLBACK" in code for code in rationale_set):
        return "pullback_entry"
    return "trend_follow"


def _setup_cluster_key(
    *,
    symbol: str,
    timeframe: str,
    scenario: str,
    entry_mode: str,
    regime: str,
    trend_alignment: str,
) -> str:
    return f"{symbol.upper()}|{timeframe}|{scenario}|{entry_mode}|{regime}|{trend_alignment}"


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _strategy_engine_name_from_payload(
    metadata: dict[str, object],
    output_payload: dict[str, object],
) -> str:
    strategy_engine = _as_dict(metadata.get("strategy_engine"))
    selected_engine = _as_dict(strategy_engine.get("selected_engine"))
    engine_name = str(selected_engine.get("engine_name") or "").strip()
    if engine_name:
        return engine_name
    entry_mode = str(output_payload.get("entry_mode") or "").lower()
    decision = str(output_payload.get("decision") or "").lower()
    rationale_codes = {str(code) for code in output_payload.get("rationale_codes") or [] if code}
    if rationale_codes & {"PROTECTION_REQUIRED", "PROTECTION_RECOVERY", "PROTECTION_RESTORE"}:
        return "protection_reduce_engine"
    if entry_mode == "breakout_confirm":
        return "breakout_exception_engine"
    if any("CONTINUATION" in code for code in rationale_codes):
        return "trend_continuation_engine"
    if entry_mode == "pullback_confirm":
        return "trend_pullback_engine"
    if decision in {"reduce", "exit"}:
        return "protection_reduce_engine"
    return "unspecified_engine"


def _coerce_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _safe_str(value: object) -> str:
    if value in {None, ""}:
        return ""
    return str(value)


def _rolling_returns_from_snapshot(snapshot: MarketSnapshotPayload) -> list[float]:
    closes = [float(candle.close) for candle in snapshot.candles if candle.close > 0]
    if len(closes) < 3:
        return []
    returns: list[float] = []
    for previous, current in zip(closes, closes[1:], strict=False):
        if previous <= 0:
            continue
        returns.append((current - previous) / previous)
    return returns


def _pearson_correlation(left: list[float], right: list[float]) -> float:
    sample_size = min(len(left), len(right))
    if sample_size < 3:
        return 0.0
    lhs = left[-sample_size:]
    rhs = right[-sample_size:]
    lhs_mean = sum(lhs) / sample_size
    rhs_mean = sum(rhs) / sample_size
    covariance = sum((lhs_item - lhs_mean) * (rhs_item - rhs_mean) for lhs_item, rhs_item in zip(lhs, rhs, strict=False))
    lhs_variance = sum((item - lhs_mean) ** 2 for item in lhs)
    rhs_variance = sum((item - rhs_mean) ** 2 for item in rhs)
    denominator = sqrt(lhs_variance * rhs_variance)
    if denominator <= 0:
        return 0.0
    return covariance / denominator


class TradingOrchestrator:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.settings_row = get_or_create_settings(session)
        self.credentials = get_runtime_credentials(self.settings_row)
        provider = build_model_provider(
            ai_provider=self.settings_row.ai_provider,
            ai_enabled=self.settings_row.ai_enabled,
            api_key=self.credentials.openai_api_key,
            model=self.settings_row.ai_model,
            temperature=self.settings_row.ai_temperature,
        )
        self.trading_agent = TradingDecisionAgent(provider)
        self.chief_review_agent = ChiefReviewAgent()

    def build_keep_kill_report(
        self,
        *,
        lookback_days: int = 21,
        limit: int = 256,
    ):
        return build_keep_kill_report(
            self.session,
            lookback_days=lookback_days,
            limit=limit,
        )

    def build_skip_quality_report(
        self,
        *,
        lookback_days: int = 21,
        limit: int = 512,
    ):
        return build_skip_quality_report(
            self.session,
            lookback_days=lookback_days,
            limit=limit,
        )

    def build_capital_efficiency_report(
        self,
        *,
        lookback_days: int = 21,
        limit: int = 256,
    ):
        return build_capital_efficiency_report(
            self.session,
            lookback_days=lookback_days,
            limit=limit,
        )

    def build_strategy_engine_bucket_report(
        self,
        *,
        lookback_days: int = 21,
        limit: int = 256,
    ):
        return build_strategy_engine_bucket_report(
            self.session,
            lookback_days=lookback_days,
            limit=limit,
        )

    def _sync_drawdown_state(
        self,
        *,
        now: datetime | None = None,
        record_transition_audit: bool = True,
    ) -> dict[str, object]:
        observed_at = now or utcnow_naive()
        previous_detail = get_drawdown_state_detail(self.settings_row)
        previous_state = str(previous_detail.get("current_drawdown_state") or "normal")
        snapshot = build_drawdown_state_snapshot(
            self.session,
            self.settings_row,
            current_detail=previous_detail,
            now=observed_at,
        )
        if snapshot != previous_detail:
            set_drawdown_state_detail(self.settings_row, snapshot)
            self.session.add(self.settings_row)
            self.session.flush()
        current_state = str(snapshot.get("current_drawdown_state") or "normal")
        if record_transition_audit and previous_state != current_state:
            record_audit_event(
                self.session,
                event_type="drawdown_state_transition",
                entity_type="settings",
                entity_id=str(self.settings_row.id),
                severity="warning" if current_state in {DRAWDOWN_STATE_CAUTION, DRAWDOWN_STATE_CONTAINMENT} else "info",
                message="Account drawdown operating state updated.",
                payload={
                    "previous_drawdown_state": previous_state,
                    "current_drawdown_state": current_state,
                    "entered_at": snapshot.get("entered_at"),
                    "transition_reason": snapshot.get("transition_reason"),
                    "policy_adjustments": dict(snapshot.get("policy_adjustments") or {}),
                    "drawdown_depth_pct": snapshot.get("drawdown_depth_pct"),
                    "recent_net_pnl": snapshot.get("recent_net_pnl"),
                    "recent_net_pnl_pct": snapshot.get("recent_net_pnl_pct"),
                    "consecutive_losses": snapshot.get("consecutive_losses"),
                    "recovery_progress": snapshot.get("recovery_progress"),
                },
            )
        return snapshot

    @staticmethod
    def _should_execute_live(trigger_event: str) -> bool:
        return trigger_event in {
            TriggerEvent.MANUAL.value,
            TriggerEvent.REALTIME.value,
            TriggerEvent.SCHEDULED.value,
            "test",
        }

    @staticmethod
    def _should_poll_exchange_state(trigger_event: str) -> bool:
        return trigger_event in {
            TriggerEvent.MANUAL.value,
            TriggerEvent.REALTIME.value,
            TriggerEvent.SCHEDULED.value,
            "test",
            "background_poll",
        }

    @staticmethod
    def _build_cycle_id(*, trigger_event: str, symbol: str, snapshot_id: int) -> str:
        return f"{trigger_event}:{symbol.upper()}:{snapshot_id}:{uuid4().hex[:8]}"

    def _effective_symbol_settings(self, symbol: str):
        return get_effective_symbol_settings(self.settings_row, symbol.upper())

    def _latest_decision_snapshot_time(self, symbol: str, timeframe: str) -> str | None:
        rows = list(
            self.session.scalars(
                select(AgentRun)
                .where(AgentRun.role == AgentRole.TRADING_DECISION.value)
                .order_by(desc(AgentRun.created_at))
                .limit(100)
            )
        )
        symbol_upper = symbol.upper()
        for row in rows:
            input_payload = row.input_payload if isinstance(row.input_payload, dict) else {}
            market_snapshot = input_payload.get("market_snapshot")
            if not isinstance(market_snapshot, dict):
                continue
            if str(market_snapshot.get("symbol", "")).upper() != symbol_upper:
                continue
            if str(market_snapshot.get("timeframe", "")) != timeframe:
                continue
            snapshot_time = market_snapshot.get("snapshot_time")
            if isinstance(snapshot_time, str) and snapshot_time:
                return snapshot_time
        return None

    @staticmethod
    def _agent_run_matches_symbol(
        row: AgentRun,
        *,
        symbol: str,
        timeframe: str | None = None,
    ) -> bool:
        symbol_upper = symbol.upper()
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        input_payload = row.input_payload if isinstance(row.input_payload, dict) else {}
        output_payload = row.output_payload if isinstance(row.output_payload, dict) else {}
        market_snapshot = input_payload.get("market_snapshot")
        candidates = [
            (
                str(metadata.get("symbol") or "").upper(),
                str(metadata.get("timeframe") or ""),
            ),
            (
                str(output_payload.get("symbol") or "").upper(),
                str(output_payload.get("timeframe") or ""),
            ),
            (
                str(market_snapshot.get("symbol") or "").upper(),
                str(market_snapshot.get("timeframe") or ""),
            )
            if isinstance(market_snapshot, dict)
            else ("", ""),
        ]
        for candidate_symbol, candidate_timeframe in candidates:
            if candidate_symbol != symbol_upper:
                continue
            if timeframe is None or not timeframe or candidate_timeframe in {"", timeframe}:
                return True
        return False

    def _latest_symbol_decision_run(
        self,
        *,
        symbol: str,
        timeframe: str | None = None,
    ) -> AgentRun | None:
        rows = list(
            self.session.scalars(
                select(AgentRun)
                .where(AgentRun.role == AgentRole.TRADING_DECISION.value)
                .order_by(desc(AgentRun.created_at))
                .limit(100)
            )
        )
        for row in rows:
            if self._agent_run_matches_symbol(row, symbol=symbol, timeframe=timeframe):
                return row
        return None

    def _latest_symbol_ai_invoked_at(
        self,
        *,
        symbol: str,
        timeframe: str | None = None,
    ) -> datetime | None:
        rows = list(
            self.session.scalars(
                select(AgentRun)
                .where(AgentRun.role == AgentRole.TRADING_DECISION.value)
                .order_by(desc(AgentRun.created_at))
                .limit(100)
            )
        )
        for row in rows:
            if not self._agent_run_matches_symbol(row, symbol=symbol, timeframe=timeframe):
                continue
            metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
            if row.provider_name == "openai" or str(metadata.get("source") or "") == "llm":
                return row.created_at
        return None

    @staticmethod
    def _selection_ranking_lookup(candidate_selection: dict[str, object]) -> dict[str, dict[str, object]]:
        return {
            str(item.get("symbol") or "").upper(): dict(item)
            for item in candidate_selection.get("rankings", [])
            if isinstance(item, dict) and item.get("symbol")
        }

    def _selection_context_from_candidate_selection(
        self,
        *,
        symbol: str,
        candidate_selection: dict[str, object],
    ) -> dict[str, object]:
        ranking_lookup = self._selection_ranking_lookup(candidate_selection)
        ranking_payload = ranking_lookup.get(symbol.upper(), {})
        breadth_summary = (
            dict(candidate_selection.get("breadth_summary") or {})
            if isinstance(candidate_selection.get("breadth_summary"), dict)
            else {}
        )
        candidate_payload = _as_dict(ranking_payload.get("candidate"))
        slot_allocation = {
            "assigned_slot": ranking_payload.get("assigned_slot"),
            "slot_label": ranking_payload.get("slot_label"),
            "candidate_weight": ranking_payload.get("candidate_weight"),
            "portfolio_weight": ranking_payload.get("portfolio_weight"),
            "slot_conviction_score": ranking_payload.get("slot_conviction_score"),
            "meta_gate_probability": ranking_payload.get("meta_gate_probability"),
            "agreement_alignment_score": ranking_payload.get("agreement_alignment_score"),
            "agreement_level_hint": ranking_payload.get("agreement_level_hint"),
            "execution_quality_score": ranking_payload.get("execution_quality_score"),
            "risk_pct_multiplier": ranking_payload.get("slot_risk_pct_multiplier"),
            "leverage_multiplier": ranking_payload.get("slot_leverage_multiplier"),
            "notional_multiplier": ranking_payload.get("slot_notional_multiplier"),
            "applies_soft_limit": ranking_payload.get("slot_applies_soft_cap"),
        }
        return {
            "universe_breadth": breadth_summary,
            "breadth_regime": candidate_selection.get("breadth_regime"),
            "capacity_reason": candidate_selection.get("capacity_reason"),
            "drawdown_capacity_reason": candidate_selection.get("drawdown_capacity_reason"),
            "drawdown_state": dict(candidate_selection.get("drawdown_state") or {}),
            "portfolio_weight": ranking_payload.get("portfolio_weight"),
            "candidate_weight": ranking_payload.get("candidate_weight"),
            "holding_profile": ranking_payload.get("holding_profile") or candidate_payload.get("holding_profile"),
            "holding_profile_reason": ranking_payload.get("holding_profile_reason")
            or candidate_payload.get("holding_profile_reason"),
            "holding_profile_context": _as_dict(ranking_payload.get("holding_profile_context")),
            "strategy_engine": ranking_payload.get("strategy_engine") or candidate_payload.get("strategy_engine"),
            "strategy_engine_context": _as_dict(ranking_payload.get("strategy_engine_context")),
            "assigned_slot": ranking_payload.get("assigned_slot"),
            "slot_label": ranking_payload.get("slot_label"),
            "slot_reason": ranking_payload.get("slot_reason"),
            "slot_conviction_score": ranking_payload.get("slot_conviction_score"),
            "meta_gate_probability": ranking_payload.get("meta_gate_probability"),
            "agreement_alignment_score": ranking_payload.get("agreement_alignment_score"),
            "agreement_level_hint": ranking_payload.get("agreement_level_hint"),
            "execution_quality_score": ranking_payload.get("execution_quality_score"),
            "slot_risk_pct_multiplier": ranking_payload.get("slot_risk_pct_multiplier"),
            "slot_leverage_multiplier": ranking_payload.get("slot_leverage_multiplier"),
            "slot_notional_multiplier": ranking_payload.get("slot_notional_multiplier"),
            "slot_applies_soft_cap": ranking_payload.get("slot_applies_soft_cap"),
            "slot_allocation": slot_allocation,
            "breadth_score_multiplier": ranking_payload.get("breadth_score_multiplier"),
            "breadth_score_adjustment": ranking_payload.get("breadth_score_adjustment"),
            "breadth_hold_bias": ranking_payload.get("breadth_hold_bias"),
            "breadth_adjustment_reasons": ranking_payload.get("breadth_adjustment_reasons"),
            "selection_reason": ranking_payload.get("selection_reason"),
            "selected_reason": ranking_payload.get("selected_reason"),
            "rejected_reason": ranking_payload.get("rejected_reason"),
            "selected": ranking_payload.get("selected"),
            "entry_mode": ranking_payload.get("entry_mode"),
            "candidate_entry_mode": ranking_payload.get("entry_mode"),
            "scenario": candidate_payload.get("scenario"),
            "expected_scenario": candidate_payload.get("scenario"),
            "candidate": candidate_payload,
            "score": _as_dict(ranking_payload.get("score")),
            "reason_codes": list(candidate_payload.get("rationale_codes") or []),
        }

    def _default_selection_context_for_symbol(
        self,
        *,
        symbol: str,
        timeframe: str,
        upto_index: int | None,
        force_stale: bool,
    ) -> dict[str, object]:
        candidate_selection = self._rank_candidate_symbols(
            decision_symbols=[symbol.upper()],
            timeframe=timeframe,
            upto_index=upto_index,
            force_stale=force_stale,
        )
        return self._selection_context_from_candidate_selection(
            symbol=symbol,
            candidate_selection=candidate_selection,
        )

    def _latest_feature_snapshot_payload(self, *, symbol: str, timeframe: str) -> dict[str, object]:
        row = self.session.scalar(
            select(FeatureSnapshot)
            .where(
                FeatureSnapshot.symbol == symbol.upper(),
                FeatureSnapshot.timeframe == timeframe,
            )
            .order_by(desc(FeatureSnapshot.feature_time), desc(FeatureSnapshot.id))
            .limit(1)
        )
        if row is None or not isinstance(row.payload, dict):
            return {}
        return dict(row.payload)

    @staticmethod
    def _reference_price_from_zone(
        *,
        entry_zone_min: object,
        entry_zone_max: object,
        fallback_price: float,
    ) -> float:
        zone_min = _safe_float(entry_zone_min, default=0.0)
        zone_max = _safe_float(entry_zone_max, default=0.0)
        if zone_min > 0 and zone_max > 0:
            return (zone_min + zone_max) / 2.0
        if zone_max > 0:
            return zone_max
        if zone_min > 0:
            return zone_min
        return fallback_price

    @staticmethod
    def _selection_skip_reason(
        *,
        rejected_reason: str,
        capacity_reason: str,
    ) -> str | None:
        reason = rejected_reason.strip().lower()
        if not reason:
            return None
        if reason == "breadth_hold_bias":
            return "breadth_veto"
        if reason == "capacity_reached" and capacity_reason in {
            "breadth_weak_reduce_capacity",
            "transition_fragile_reduce_capacity",
        }:
            return "breadth_veto"
        if reason in {"underperforming_expectancy_bucket", "expectancy_below_threshold"}:
            return "expectancy_veto"
        if reason == "adverse_signed_slippage":
            return "slippage_veto"
        if reason in {"duplicate_scenario_exposure", "duplicate_directional_exposure", "correlation_limit"}:
            return "correlation_veto"
        if reason == "score_below_threshold":
            return "score_veto"
        if reason == "low_edge_hold_candidate":
            return "low_edge_candidate"
        return reason

    @staticmethod
    def _decision_skip_reason(
        *,
        decision: TradeDecision,
        decision_metadata: dict[str, object],
        ai_skipped_reason: str | None,
    ) -> str | None:
        rationale_codes = {str(code) for code in decision.rationale_codes if code}
        if "NO_TRADE_ZONE_RANGE_WEAK_VOLUME" in rationale_codes or ai_skipped_reason == "CADENCE_IDLE_NO_TRADE_ZONE":
            return "no_trade_zone"
        if "UNDERPERFORMING_SETUP_DISABLED" in rationale_codes:
            return "disable_bucket"
        if SETUP_CLUSTER_DISABLED_REASON_CODE in rationale_codes:
            return "setup_cluster_disable"
        if rationale_codes & {"DERIVATIVES_ALIGNMENT_HEADWIND", "SPREAD_HEADWIND", "BREAKOUT_OI_SPREAD_FILTER"}:
            return "derivatives_filter_veto"
        if rationale_codes & {"LEAD_MARKET_DIVERGENCE", "ALT_BREAKOUT_AHEAD_OF_LEADS"}:
            return "lead_lag_veto"
        if "NEGATIVE_EXPECTANCY_BUCKET" in rationale_codes:
            return "expectancy_veto"
        setup_cluster_state = _as_dict(decision_metadata.get("setup_cluster_state"))
        if bool(setup_cluster_state.get("active")):
            return "setup_cluster_disable"
        return None

    @staticmethod
    def _decision_expected_side(
        *,
        decision: TradeDecision,
        decision_metadata: dict[str, object],
    ) -> str | None:
        if decision.decision in {"long", "short"}:
            return decision.decision
        baseline = _as_dict(decision_metadata.get("deterministic_baseline"))
        baseline_decision = _safe_str(baseline.get("decision")).lower()
        if baseline_decision in {"long", "short"}:
            return baseline_decision
        agreement = _as_dict(decision_metadata.get("decision_agreement"))
        baseline_decision = _safe_str(agreement.get("baseline_decision")).lower()
        if baseline_decision in {"long", "short"}:
            return baseline_decision
        return None

    def _record_selection_skip_event(
        self,
        *,
        symbol: str,
        timeframe: str,
        item: dict[str, object],
        ranking_payload: dict[str, object],
    ) -> None:
        skip_reason = self._selection_skip_reason(
            rejected_reason=_safe_str(ranking_payload.get("rejected_reason")),
            capacity_reason=_safe_str(ranking_payload.get("capacity_reason")),
        )
        if skip_reason is None:
            return
        candidate = item.get("candidate")
        if candidate is None or str(getattr(candidate, "decision", "") or "") not in {"long", "short"}:
            return
        regime_summary = item.get("regime_summary") if isinstance(item.get("regime_summary"), dict) else {}
        performance_summary = _as_dict(item.get("performance_summary"))
        market_snapshot = item.get("market_snapshot")
        market_snapshot_id: int | None = None
        if isinstance(market_snapshot, MarketSnapshotPayload):
            market_snapshot_id = persist_market_snapshot(self.session, market_snapshot).id
        latest_price = _safe_float(getattr(market_snapshot, "latest_price", None))
        reference_price = self._reference_price_from_zone(
            entry_zone_min=getattr(candidate, "entry_zone_min", None),
            entry_zone_max=getattr(candidate, "entry_zone_max", None),
            fallback_price=latest_price,
        )
        record_skip_event(
            self.session,
            symbol=symbol,
            timeframe=timeframe,
            scenario=_safe_str(getattr(candidate, "scenario", "unspecified")) or "unspecified",
            regime=_safe_str(regime_summary.get("primary_regime")) or "unknown",
            trend_alignment=_safe_str(regime_summary.get("trend_alignment")) or "unknown",
            entry_mode=_safe_str(item.get("entry_mode")) or "none",
            skip_reason=skip_reason,
            skip_source="selection",
            market_snapshot_id=market_snapshot_id,
            decision_run_id=None,
            expected_side=_safe_str(getattr(candidate, "decision", None)).lower() or None,
            rejected_side=_safe_str(getattr(candidate, "decision", None)).lower() or None,
            reference_price=reference_price if reference_price > 0 else None,
            stop_loss=_safe_float(getattr(candidate, "stop_loss", None), default=0.0) or None,
            take_profit=_safe_float(getattr(candidate, "take_profit", None), default=0.0) or None,
            payload={
                "candidate_id": _safe_str(getattr(candidate, "candidate_id", "")),
                "selection_reason": ranking_payload.get("selection_reason"),
                "rejected_reason": ranking_payload.get("rejected_reason"),
                "selected_reason": ranking_payload.get("selected_reason"),
                "score": ranking_payload.get("score"),
                "performance_summary": performance_summary,
                "breadth_regime": ranking_payload.get("breadth_regime"),
                "capacity_reason": ranking_payload.get("capacity_reason"),
                "entry_score_threshold": ranking_payload.get("entry_score_threshold"),
                "breadth_adjustment_reasons": ranking_payload.get("breadth_adjustment_reasons"),
                "snapshot_time": getattr(market_snapshot, "snapshot_time", None).isoformat()
                if getattr(market_snapshot, "snapshot_time", None) is not None
                else None,
            },
        )

    def _record_decision_skip_event(
        self,
        *,
        symbol: str,
        timeframe: str,
        market_row: MarketSnapshot,
        market_snapshot: MarketSnapshotPayload,
        decision_run: AgentRun,
        decision: TradeDecision,
        decision_metadata: dict[str, object],
        ai_skipped_reason: str | None,
        selection_context: dict[str, object] | None,
    ) -> None:
        skip_reason = self._decision_skip_reason(
            decision=decision,
            decision_metadata=decision_metadata,
            ai_skipped_reason=ai_skipped_reason,
        )
        if skip_reason is None:
            return
        expected_side = self._decision_expected_side(decision=decision, decision_metadata=decision_metadata)
        if expected_side not in {"long", "short"}:
            return
        baseline = _as_dict(decision_metadata.get("deterministic_baseline"))
        entry_mode = (
            _safe_str(decision.entry_mode)
            or _safe_str(baseline.get("entry_mode"))
            or _safe_str(_as_dict(selection_context).get("entry_mode"))
            or _safe_str(_as_dict(selection_context).get("candidate_entry_mode"))
            or "none"
        )
        scenario = (
            _safe_str(_as_dict(selection_context).get("expected_scenario"))
            or _safe_str(_as_dict(selection_context).get("scenario"))
            or _setup_cluster_scenario(expected_side, entry_mode, decision.rationale_codes)
        )
        reference_price = self._reference_price_from_zone(
            entry_zone_min=baseline.get("entry_zone_min") if baseline else decision.entry_zone_min,
            entry_zone_max=baseline.get("entry_zone_max") if baseline else decision.entry_zone_max,
            fallback_price=market_snapshot.latest_price,
        )
        analysis_context = _as_dict(decision_metadata.get("analysis_context"))
        regime_context = _as_dict(analysis_context.get("regime"))
        record_skip_event(
            self.session,
            symbol=symbol,
            timeframe=timeframe,
            scenario=scenario or "unspecified",
            regime=_safe_str(regime_context.get("primary_regime")) or "unknown",
            trend_alignment=_safe_str(regime_context.get("trend_alignment")) or "unknown",
            entry_mode=entry_mode or "none",
            skip_reason=skip_reason,
            skip_source="decision",
            market_snapshot_id=market_row.id,
            decision_run_id=decision_run.id,
            expected_side=expected_side,
            rejected_side=expected_side,
            reference_price=reference_price if reference_price > 0 else None,
            stop_loss=_safe_float(baseline.get("stop_loss") if baseline else decision.stop_loss, default=0.0) or None,
            take_profit=_safe_float(baseline.get("take_profit") if baseline else decision.take_profit, default=0.0)
            or None,
            payload={
                "decision_rationale_codes": list(decision.rationale_codes),
                "ai_skipped_reason": ai_skipped_reason,
                "selection_context": dict(selection_context) if isinstance(selection_context, dict) else {},
                "decision_agreement": decision_metadata.get("decision_agreement"),
                "setup_cluster_state": decision_metadata.get("setup_cluster_state"),
                "meta_gate": decision_metadata.get("meta_gate"),
            },
        )

    def _record_risk_skip_event(
        self,
        *,
        symbol: str,
        timeframe: str,
        market_row: MarketSnapshot,
        market_snapshot: MarketSnapshotPayload,
        decision_run: AgentRun,
        decision: TradeDecision,
        risk_row: RiskCheck,
        risk_result: RiskCheckResult,
        selection_context: dict[str, object] | None,
    ) -> None:
        if decision.decision not in {"long", "short"}:
            return
        reason_codes = {str(code) for code in risk_result.reason_codes if code}
        if any(code.startswith("META_GATE_") for code in reason_codes):
            skip_reason = "meta_gate_reject"
        elif "UNDERPERFORMING_SETUP_DISABLED" in reason_codes:
            skip_reason = "disable_bucket"
        elif SETUP_CLUSTER_DISABLED_REASON_CODE in reason_codes:
            skip_reason = "setup_cluster_disable"
        else:
            return
        analysis_context = _as_dict(decision_run.metadata_json).get("analysis_context")
        regime_context = _as_dict(_as_dict(analysis_context).get("regime"))
        reference_price = self._reference_price_from_zone(
            entry_zone_min=decision.entry_zone_min,
            entry_zone_max=decision.entry_zone_max,
            fallback_price=market_snapshot.latest_price,
        )
        record_skip_event(
            self.session,
            symbol=symbol,
            timeframe=timeframe,
            scenario=_setup_cluster_scenario(decision.decision, decision.entry_mode, decision.rationale_codes),
            regime=_safe_str(regime_context.get("primary_regime")) or "unknown",
            trend_alignment=_safe_str(regime_context.get("trend_alignment")) or "unknown",
            entry_mode=_safe_str(decision.entry_mode) or "none",
            skip_reason=skip_reason,
            skip_source="risk",
            market_snapshot_id=market_row.id,
            decision_run_id=decision_run.id,
            risk_check_id=risk_row.id,
            expected_side=decision.decision,
            rejected_side=decision.decision,
            reference_price=reference_price if reference_price > 0 else None,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
            payload={
                "reason_codes": list(risk_result.reason_codes),
                "blocked_reason_codes": list(risk_result.blocked_reason_codes),
                "selection_context": dict(selection_context) if isinstance(selection_context, dict) else {},
                "meta_gate": _as_dict(risk_result.debug_payload).get("meta_gate"),
            },
        )

    @staticmethod
    def _cadence_feature_flags(feature_payload: object) -> dict[str, object]:
        if hasattr(feature_payload, "model_dump"):
            payload = feature_payload.model_dump(mode="json")  # type: ignore[assignment]
        else:
            payload = _as_dict(feature_payload)
        regime = _as_dict(payload.get("regime"))
        primary_regime = str(regime.get("primary_regime") or "unknown")
        weak_volume = bool(regime.get("weak_volume", False))
        momentum_weakening = bool(regime.get("momentum_weakening", False))
        return {
            "primary_regime": primary_regime,
            "weak_volume": weak_volume,
            "momentum_weakening": momentum_weakening,
            "no_trade_zone": primary_regime == "range" and weak_volume and momentum_weakening,
        }

    @staticmethod
    def _adaptive_bucket_underperformance(context: object) -> tuple[bool, list[str]]:
        if not isinstance(context, dict):
            return False, []
        windows = context.get("windows")
        if not isinstance(windows, dict):
            return False, []
        reasons: list[str] = []
        for label, payload in windows.items():
            if not isinstance(payload, dict):
                continue
            for dimension in ("symbol_timeframe", "symbol", "regime"):
                bucket = payload.get(dimension)
                if not isinstance(bucket, dict):
                    continue
                status = str(bucket.get("status") or "")
                try:
                    weight = float(bucket.get("weight", 1.0))
                except (TypeError, ValueError):
                    weight = 1.0
                if status == "active" and weight < 0.95:
                    reasons.append(f"{label}:{dimension}")
        return bool(reasons), reasons

    @staticmethod
    def _adaptive_setup_disable_idle_state(context: object) -> tuple[bool, list[str]]:
        if not isinstance(context, dict):
            return False, []
        buckets = context.get("setup_disable_buckets")
        if not isinstance(buckets, list):
            return False, []
        current_regime = str(context.get("regime") or "")
        active_buckets = [
            item
            for item in buckets
            if isinstance(item, dict)
            and bool(item.get("disabled"))
            and str(item.get("status") or "") == "active_disabled"
            and (not current_regime or str(item.get("regime") or "") == current_regime)
        ]
        return bool(active_buckets), (["SETUP_DISABLE_COOLDOWN_ACTIVE"] if active_buckets else [])

    @staticmethod
    def _setup_cluster_idle_state(context: object) -> tuple[bool, list[str]]:
        if not isinstance(context, dict):
            return False, []
        active_cluster_keys = context.get("active_cluster_keys")
        cluster_lookup = context.get("cluster_lookup")
        if not isinstance(active_cluster_keys, list) or not isinstance(cluster_lookup, dict):
            return False, []
        current_symbol = str(context.get("symbol") or "").upper()
        current_timeframe = str(context.get("timeframe") or "")
        current_regime = str(context.get("regime") or "")
        current_trend_alignment = str(context.get("trend_alignment") or "")
        active_clusters = [
            cluster_lookup.get(str(key))
            for key in active_cluster_keys
            if isinstance(cluster_lookup.get(str(key)), dict)
        ]
        matching_clusters = [
            item
            for item in active_clusters
            if str(item.get("symbol") or "").upper() == current_symbol
            and str(item.get("timeframe") or "") == current_timeframe
            and str(item.get("regime") or "") == current_regime
            and str(item.get("trend_alignment") or "") == current_trend_alignment
        ]
        return bool(matching_clusters), (["SETUP_CLUSTER_COOLDOWN_ACTIVE"] if matching_clusters else [])

    @staticmethod
    def _signed_slippage_bps(execution_row: Execution) -> float:
        payload = execution_row.payload if isinstance(execution_row.payload, dict) else {}
        if "signed_slippage_bps" in payload:
            return _safe_float(payload.get("signed_slippage_bps"))
        if "signed_slippage_pct" in payload:
            return _safe_float(payload.get("signed_slippage_pct")) * 10_000.0
        return 0.0

    def _build_setup_cluster_context(
        self,
        *,
        symbol: str,
        timeframe: str,
        regime: str,
        trend_alignment: str,
    ) -> dict[str, object]:
        symbol_key = symbol.upper()
        decision_rows = list(
            self.session.scalars(
                select(AgentRun)
                .where(AgentRun.role == "trading_decision")
                .order_by(desc(AgentRun.created_at))
                .limit(SETUP_CLUSTER_HISTORY_LIMIT)
            )
        )
        exact_rows = [
            row
            for row in decision_rows
            if isinstance(row.output_payload, dict)
            and str(row.output_payload.get("symbol") or "").upper() == symbol_key
            and str(row.output_payload.get("timeframe") or "") == timeframe
            and str(row.output_payload.get("decision") or "").lower() in {"long", "short"}
        ]
        if not exact_rows:
            return {
                "symbol": symbol_key,
                "timeframe": timeframe,
                "regime": regime,
                "trend_alignment": trend_alignment,
                "lookback": SETUP_CLUSTER_LOOKBACK,
                "cluster_lookup": {},
                "active_cluster_keys": [],
                "active_cluster_count": 0,
            }

        decision_ids = [row.id for row in exact_rows]
        risk_rows = list(
            self.session.scalars(
                select(RiskCheck)
                .where(RiskCheck.decision_run_id.in_(decision_ids))
                .order_by(desc(RiskCheck.created_at))
            )
        )
        risk_by_decision: dict[int, RiskCheck] = {}
        for row in risk_rows:
            if row.decision_run_id is not None and row.decision_run_id not in risk_by_decision:
                risk_by_decision[row.decision_run_id] = row

        orders = list(self.session.scalars(select(Order).where(Order.decision_run_id.in_(decision_ids))))
        orders_by_decision: dict[int, list[Order]] = defaultdict(list)
        for row in orders:
            if row.decision_run_id is not None:
                orders_by_decision[row.decision_run_id].append(row)
        order_ids = [row.id for row in orders]
        executions = (
            list(self.session.scalars(select(Execution).where(Execution.order_id.in_(order_ids))))
            if order_ids
            else []
        )
        executions_by_order: dict[int, list[Execution]] = defaultdict(list)
        for row in executions:
            if row.order_id is not None:
                executions_by_order[row.order_id].append(row)

        cluster_samples: dict[str, list[dict[str, object]]] = defaultdict(list)
        for decision_row in exact_rows:
            linked_risk = risk_by_decision.get(decision_row.id)
            if linked_risk is not None and not bool(linked_risk.allowed):
                continue
            output_payload = decision_row.output_payload if isinstance(decision_row.output_payload, dict) else {}
            rationale_codes = (
                [str(code) for code in output_payload.get("rationale_codes", []) if code not in {None, ""}]
                if isinstance(output_payload.get("rationale_codes"), list)
                else []
            )
            entry_mode = str(output_payload.get("entry_mode") or "none").lower()
            scenario = _setup_cluster_scenario(
                str(output_payload.get("decision") or ""),
                entry_mode,
                rationale_codes,
            )
            primary_regime, row_trend_alignment, *_rest = _extract_analysis_context(decision_row)
            linked_orders = orders_by_decision.get(decision_row.id, [])
            linked_executions = [
                execution_row
                for order_row in linked_orders
                for execution_row in executions_by_order.get(order_row.id, [])
            ]
            if not linked_executions:
                continue
            net_pnl_after_fees = sum(
                _safe_float(execution_row.realized_pnl) - _safe_float(execution_row.fee_paid)
                for execution_row in linked_executions
            )
            avg_signed_slippage_bps = sum(
                self._signed_slippage_bps(execution_row)
                for execution_row in linked_executions
            ) / max(len(linked_executions), 1)
            cluster_key = _setup_cluster_key(
                symbol=symbol_key,
                timeframe=timeframe,
                scenario=scenario,
                entry_mode=entry_mode,
                regime=primary_regime,
                trend_alignment=row_trend_alignment,
            )
            cluster_samples[cluster_key].append(
                {
                    "created_at": decision_row.created_at,
                    "symbol": symbol_key,
                    "timeframe": timeframe,
                    "scenario": scenario,
                    "entry_mode": entry_mode,
                    "regime": primary_regime,
                    "trend_alignment": row_trend_alignment,
                    "net_pnl_after_fees": net_pnl_after_fees,
                    "avg_signed_slippage_bps": avg_signed_slippage_bps,
                }
            )

        now = utcnow_naive()
        cluster_lookup: dict[str, dict[str, object]] = {}
        active_cluster_keys: list[str] = []
        for cluster_key, sample_rows in cluster_samples.items():
            recent_rows = sorted(sample_rows, key=lambda item: item["created_at"], reverse=True)[:SETUP_CLUSTER_LOOKBACK]
            sample_size = len(recent_rows)
            wins = [float(item["net_pnl_after_fees"]) for item in recent_rows if float(item["net_pnl_after_fees"]) > 0]
            losses = [abs(float(item["net_pnl_after_fees"])) for item in recent_rows if float(item["net_pnl_after_fees"]) < 0]
            win_rate = len(wins) / max(sample_size, 1)
            loss_rate = len(losses) / max(sample_size, 1)
            avg_win = sum(wins) / max(len(wins), 1) if wins else 0.0
            avg_loss = sum(losses) / max(len(losses), 1) if losses else 0.0
            expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)
            net_pnl_after_fees = sum(float(item["net_pnl_after_fees"]) for item in recent_rows)
            avg_signed_slippage_bps = sum(
                float(item["avg_signed_slippage_bps"])
                for item in recent_rows
            ) / max(sample_size, 1)
            loss_streak = 0
            for row in recent_rows:
                if float(row["net_pnl_after_fees"]) < 0:
                    loss_streak += 1
                    continue
                break
            disable_reason_codes: list[str] = []
            if expectancy < SETUP_CLUSTER_EXPECTANCY_THRESHOLD:
                disable_reason_codes.append(SETUP_CLUSTER_DISABLE_REASON_CODES["expectancy"])
            if loss_streak >= SETUP_CLUSTER_LOSS_STREAK_THRESHOLD:
                disable_reason_codes.append(SETUP_CLUSTER_DISABLE_REASON_CODES["loss_streak"])
            if avg_signed_slippage_bps >= SETUP_CLUSTER_SIGNED_SLIPPAGE_BPS_THRESHOLD:
                disable_reason_codes.append(SETUP_CLUSTER_DISABLE_REASON_CODES["signed_slippage"])
            if net_pnl_after_fees < SETUP_CLUSTER_NET_PNL_THRESHOLD:
                disable_reason_codes.append(SETUP_CLUSTER_DISABLE_REASON_CODES["net_pnl"])
            underperforming = (
                sample_size >= SETUP_CLUSTER_MIN_SAMPLE_SIZE
                and expectancy < SETUP_CLUSTER_EXPECTANCY_THRESHOLD
                and net_pnl_after_fees < SETUP_CLUSTER_NET_PNL_THRESHOLD
                and (
                    loss_streak >= SETUP_CLUSTER_LOSS_STREAK_THRESHOLD
                    or avg_signed_slippage_bps >= SETUP_CLUSTER_SIGNED_SLIPPAGE_BPS_THRESHOLD
                )
            )
            latest_seen_at = recent_rows[0]["created_at"]
            cooldown_expires_at = (
                latest_seen_at + timedelta(minutes=SETUP_CLUSTER_COOLDOWN_MINUTES)
                if underperforming
                else None
            )
            cooldown_active = bool(
                underperforming
                and cooldown_expires_at is not None
                and cooldown_expires_at > now
            )
            metrics_recovered = bool(
                sample_size >= SETUP_CLUSTER_MIN_SAMPLE_SIZE
                and expectancy >= SETUP_CLUSTER_EXPECTANCY_THRESHOLD
                and net_pnl_after_fees >= SETUP_CLUSTER_NET_PNL_THRESHOLD
            )
            recovery_trigger: str | None = None
            status = "monitoring"
            if sample_size < SETUP_CLUSTER_MIN_SAMPLE_SIZE:
                status = "insufficient_data"
            elif cooldown_active:
                status = "active_disabled"
            elif underperforming and cooldown_expires_at is not None and cooldown_expires_at <= now:
                status = "cooldown_elapsed"
                recovery_trigger = "cooldown_elapsed"
            elif metrics_recovered:
                status = "metrics_recovered"
                recovery_trigger = "positive_recent_metrics"
            if cooldown_active:
                active_cluster_keys.append(cluster_key)
            cluster_lookup[cluster_key] = {
                "cluster_key": cluster_key,
                "symbol": recent_rows[0]["symbol"],
                "timeframe": recent_rows[0]["timeframe"],
                "scenario": recent_rows[0]["scenario"],
                "entry_mode": recent_rows[0]["entry_mode"],
                "regime": recent_rows[0]["regime"],
                "trend_alignment": recent_rows[0]["trend_alignment"],
                "sample_size": sample_size,
                "lookback": SETUP_CLUSTER_LOOKBACK,
                "active": cooldown_active,
                "cooldown_active": cooldown_active,
                "underperforming": underperforming,
                "status": status,
                "recovery_trigger": recovery_trigger,
                "disable_reason_codes": disable_reason_codes,
                "disabled_at": latest_seen_at.isoformat() if underperforming else None,
                "cooldown_expires_at": cooldown_expires_at.isoformat() if cooldown_expires_at is not None else None,
                "metrics": {
                    "win_rate": round(win_rate, 4),
                    "avg_win": round(avg_win, 4),
                    "avg_loss": round(avg_loss, 4),
                    "expectancy": round(expectancy, 4),
                    "net_pnl_after_fees": round(net_pnl_after_fees, 4),
                    "avg_signed_slippage_bps": round(avg_signed_slippage_bps, 4),
                    "loss_streak": loss_streak,
                },
                "thresholds": {
                    "expectancy": SETUP_CLUSTER_EXPECTANCY_THRESHOLD,
                    "net_pnl_after_fees": SETUP_CLUSTER_NET_PNL_THRESHOLD,
                    "loss_streak": SETUP_CLUSTER_LOSS_STREAK_THRESHOLD,
                    "avg_signed_slippage_bps": SETUP_CLUSTER_SIGNED_SLIPPAGE_BPS_THRESHOLD,
                },
                "recovery_condition": {
                    "mode": "cooldown_or_positive_recent_metrics",
                    "cooldown_minutes": SETUP_CLUSTER_COOLDOWN_MINUTES,
                    "cooldown_expires_at": cooldown_expires_at.isoformat() if cooldown_expires_at is not None else None,
                    "metrics_recovery_rule": "expectancy >= 0 and net_pnl_after_fees >= 0, or cooldown elapsed",
                },
            }
        return {
            "symbol": symbol_key,
            "timeframe": timeframe,
            "regime": regime,
            "trend_alignment": trend_alignment,
            "lookback": SETUP_CLUSTER_LOOKBACK,
            "cluster_lookup": cluster_lookup,
            "active_cluster_keys": active_cluster_keys,
            "active_cluster_count": len(active_cluster_keys),
        }

    @staticmethod
    def _performance_summary_bucket(
        *,
        label: str,
        sample_rows: list[dict[str, object]],
    ) -> dict[str, object]:
        recent_rows = sorted(
            sample_rows,
            key=lambda item: item.get("created_at") if isinstance(item.get("created_at"), datetime) else datetime.min,
            reverse=True,
        )[:SETUP_CLUSTER_LOOKBACK]
        sample_size = len(recent_rows)
        if sample_size == 0:
            return {
                "label": label,
                "sample_size": 0,
                "hit_rate": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "expectancy": 0.0,
                "net_pnl_after_fees": 0.0,
                "avg_signed_slippage_bps": 0.0,
                "avg_time_to_profit_minutes": 0.0,
                "avg_drawdown_impact": 0.0,
                "loss_streak": 0,
                "score": 0.55,
                "underperforming": False,
                "insufficient_data": True,
            }
        pnls = [float(item["net_pnl_after_fees"]) for item in recent_rows]
        wins = [pnl for pnl in pnls if pnl > 0]
        losses = [abs(pnl) for pnl in pnls if pnl < 0]
        hit_rate = len(wins) / max(sample_size, 1)
        loss_rate = len(losses) / max(sample_size, 1)
        avg_win = sum(wins) / max(len(wins), 1) if wins else 0.0
        avg_loss = sum(losses) / max(len(losses), 1) if losses else 0.0
        expectancy = (hit_rate * avg_win) - (loss_rate * avg_loss)
        net_pnl_after_fees = sum(pnls)
        avg_signed_slippage_bps = sum(float(item["avg_signed_slippage_bps"]) for item in recent_rows) / max(sample_size, 1)
        avg_time_to_profit_minutes = sum(float(item.get("time_to_profit_minutes", 0.0) or 0.0) for item in recent_rows) / max(sample_size, 1)
        avg_drawdown_impact = sum(float(item.get("drawdown_impact", 0.0) or 0.0) for item in recent_rows) / max(sample_size, 1)
        loss_streak = 0
        for pnl in pnls:
            if pnl < 0:
                loss_streak += 1
                continue
            break
        pnl_scale = max(sum(abs(pnl) for pnl in pnls) / max(sample_size, 1), 5.0)
        expectancy_score = _clamp_score(0.5 + ((expectancy / pnl_scale) * 0.28))
        net_pnl_score = _clamp_score(0.5 + ((net_pnl_after_fees / max(pnl_scale * sample_size, 10.0)) * 0.3))
        slippage_score = _clamp_score(
            0.68
            - (min(max(avg_signed_slippage_bps, 0.0), 18.0) / 18.0 * 0.38)
            + (min(max(-avg_signed_slippage_bps, 0.0), 12.0) / 12.0 * 0.08)
        )
        hit_rate_score = _clamp_score(0.35 + (hit_rate * 0.65))
        composite_score = _clamp_score(
            (expectancy_score * 0.45)
            + (net_pnl_score * 0.3)
            + (hit_rate_score * 0.15)
            + (slippage_score * 0.1)
        )
        underperforming = (
            sample_size >= SETUP_CLUSTER_MIN_SAMPLE_SIZE
            and expectancy < 0
            and net_pnl_after_fees < 0
            and (
                loss_streak >= SETUP_CLUSTER_LOSS_STREAK_THRESHOLD
                or avg_signed_slippage_bps >= SETUP_CLUSTER_SIGNED_SLIPPAGE_BPS_THRESHOLD
            )
        )
        return {
            "label": label,
            "sample_size": sample_size,
            "hit_rate": round(hit_rate, 4),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "expectancy": round(expectancy, 4),
            "net_pnl_after_fees": round(net_pnl_after_fees, 4),
            "avg_signed_slippage_bps": round(avg_signed_slippage_bps, 4),
            "avg_time_to_profit_minutes": round(avg_time_to_profit_minutes, 4),
            "avg_drawdown_impact": round(avg_drawdown_impact, 4),
            "loss_streak": loss_streak,
            "score": round(composite_score, 6),
            "underperforming": underperforming,
            "insufficient_data": sample_size < SETUP_CLUSTER_MIN_SAMPLE_SIZE,
        }

    def _recent_signal_performance_summary(
        self,
        *,
        symbol: str,
        timeframe: str,
        scenario: str,
        regime: str,
        trend_alignment: str,
        strategy_engine: str = "",
    ) -> dict[str, object]:
        symbol_key = symbol.upper()
        decision_rows = list(
            self.session.scalars(
                select(AgentRun)
                .where(AgentRun.role == AgentRole.TRADING_DECISION.value)
                .order_by(desc(AgentRun.created_at))
                .limit(SETUP_CLUSTER_HISTORY_LIMIT)
            )
        )
        exact_rows = []
        for row in decision_rows:
            output_payload = row.output_payload if isinstance(row.output_payload, dict) else {}
            if str(output_payload.get("symbol") or "").upper() != symbol_key:
                continue
            if timeframe and str(output_payload.get("timeframe") or "") != timeframe:
                continue
            if str(output_payload.get("decision") or "").lower() not in {"long", "short"}:
                continue
            exact_rows.append(row)
        if not exact_rows:
            neutral_bucket = self._performance_summary_bucket(label="symbol", sample_rows=[])
            return {
                "score": neutral_bucket["score"],
                "sample_size": neutral_bucket["sample_size"],
                "hit_rate": neutral_bucket["hit_rate"],
                "expectancy": neutral_bucket["expectancy"],
                "net_pnl_after_fees": neutral_bucket["net_pnl_after_fees"],
                "avg_signed_slippage_bps": neutral_bucket["avg_signed_slippage_bps"],
                "avg_time_to_profit_minutes": neutral_bucket["avg_time_to_profit_minutes"],
                "avg_drawdown_impact": neutral_bucket["avg_drawdown_impact"],
                "loss_streak": neutral_bucket["loss_streak"],
                "underperforming": False,
                "components": {
                    "symbol": neutral_bucket,
                    "engine": self._performance_summary_bucket(label="engine", sample_rows=[]),
                    "scenario": self._performance_summary_bucket(label="scenario", sample_rows=[]),
                    "regime": self._performance_summary_bucket(label="regime", sample_rows=[]),
                    "bucket": self._performance_summary_bucket(label="bucket", sample_rows=[]),
                },
            }

        decision_ids = [row.id for row in exact_rows]
        risk_rows = list(
            self.session.scalars(
                select(RiskCheck)
                .where(RiskCheck.decision_run_id.in_(decision_ids))
                .order_by(desc(RiskCheck.created_at))
            )
        )
        risk_by_decision: dict[int, RiskCheck] = {}
        for row in risk_rows:
            if row.decision_run_id is not None and row.decision_run_id not in risk_by_decision:
                risk_by_decision[row.decision_run_id] = row
        orders = list(self.session.scalars(select(Order).where(Order.decision_run_id.in_(decision_ids))))
        orders_by_decision: dict[int, list[Order]] = defaultdict(list)
        for row in orders:
            if row.decision_run_id is not None:
                orders_by_decision[row.decision_run_id].append(row)
        order_ids = [row.id for row in orders]
        executions = (
            list(self.session.scalars(select(Execution).where(Execution.order_id.in_(order_ids))))
            if order_ids
            else []
        )
        executions_by_order: dict[int, list[Execution]] = defaultdict(list)
        for row in executions:
            if row.order_id is not None:
                executions_by_order[row.order_id].append(row)
        position_ids = sorted({int(row.position_id) for row in orders if row.position_id is not None})
        positions_by_id: dict[int, Position] = {}
        if position_ids:
            positions_by_id = {
                position.id: position
                for position in self.session.scalars(select(Position).where(Position.id.in_(position_ids)))
            }

        samples: list[dict[str, object]] = []
        for decision_row in exact_rows:
            linked_risk = risk_by_decision.get(decision_row.id)
            if linked_risk is not None and not bool(linked_risk.allowed):
                continue
            output_payload = decision_row.output_payload if isinstance(decision_row.output_payload, dict) else {}
            metadata_payload = decision_row.metadata_json if isinstance(decision_row.metadata_json, dict) else {}
            rationale_codes = (
                [str(code) for code in output_payload.get("rationale_codes", []) if code not in {None, ""}]
                if isinstance(output_payload.get("rationale_codes"), list)
                else []
            )
            row_entry_mode = str(output_payload.get("entry_mode") or "none").lower()
            row_scenario = _setup_cluster_scenario(
                str(output_payload.get("decision") or ""),
                row_entry_mode,
                rationale_codes,
            )
            row_regime, row_trend_alignment, *_rest = _extract_analysis_context(decision_row)
            linked_orders = orders_by_decision.get(decision_row.id, [])
            linked_executions = [
                execution_row
                for order_row in linked_orders
                for execution_row in executions_by_order.get(order_row.id, [])
            ]
            if not linked_executions:
                continue
            net_pnl_after_fees = sum(
                _safe_float(execution_row.realized_pnl) - _safe_float(execution_row.fee_paid)
                for execution_row in linked_executions
            )
            avg_signed_slippage_bps = sum(
                self._signed_slippage_bps(execution_row)
                for execution_row in linked_executions
            ) / max(len(linked_executions), 1)
            positions = [
                positions_by_id[int(order_row.position_id)]
                for order_row in linked_orders
                if order_row.position_id is not None and int(order_row.position_id) in positions_by_id
            ]
            time_to_profit_minutes = 0.0
            drawdown_impact = 0.0
            if positions:
                profit_hints: list[float] = []
                drawdown_hints: list[float] = []
                for position in positions:
                    position_metadata = _as_dict(position.metadata_json)
                    capital_efficiency = _as_dict(position_metadata.get("capital_efficiency"))
                    position_management = _as_dict(position_metadata.get("position_management"))
                    time_to_profit = _safe_float(capital_efficiency.get("time_to_0_25r_minutes"), default=-1.0)
                    if time_to_profit < 0:
                        time_to_profit = _safe_float(capital_efficiency.get("time_to_0_5r_minutes"), default=-1.0)
                    if time_to_profit < 0:
                        time_to_profit = _safe_float(position_management.get("time_to_profit_minutes"), default=-1.0)
                    if time_to_profit >= 0:
                        profit_hints.append(time_to_profit)
                    drawdown_hint = _safe_float(capital_efficiency.get("mae_r"), default=0.0)
                    if drawdown_hint == 0.0:
                        drawdown_hint = _safe_float(position_management.get("mae_r"), default=0.0)
                    if drawdown_hint != 0.0:
                        drawdown_hints.append(abs(drawdown_hint))
                if profit_hints:
                    time_to_profit_minutes = min(profit_hints)
                if drawdown_hints:
                    drawdown_impact = sum(drawdown_hints) / max(len(drawdown_hints), 1)
            samples.append(
                {
                    "created_at": decision_row.created_at,
                    "scenario": row_scenario,
                    "regime": row_regime,
                    "trend_alignment": row_trend_alignment,
                    "strategy_engine": _strategy_engine_name_from_payload(metadata_payload, output_payload),
                    "net_pnl_after_fees": net_pnl_after_fees,
                    "avg_signed_slippage_bps": avg_signed_slippage_bps,
                    "time_to_profit_minutes": time_to_profit_minutes,
                    "drawdown_impact": drawdown_impact,
                }
            )

        if not samples:
            return self._recent_signal_performance_summary(
                symbol=symbol,
                timeframe="",
                scenario="",
                regime="",
                trend_alignment="",
            ) if timeframe else {
                "score": 0.55,
                "sample_size": 0,
                "hit_rate": 0.0,
                "expectancy": 0.0,
                "net_pnl_after_fees": 0.0,
                "avg_signed_slippage_bps": 0.0,
                "avg_time_to_profit_minutes": 0.0,
                "avg_drawdown_impact": 0.0,
                "loss_streak": 0,
                "underperforming": False,
                "components": {
                    "symbol": self._performance_summary_bucket(label="symbol", sample_rows=[]),
                    "engine": self._performance_summary_bucket(label="engine", sample_rows=[]),
                    "scenario": self._performance_summary_bucket(label="scenario", sample_rows=[]),
                    "regime": self._performance_summary_bucket(label="regime", sample_rows=[]),
                    "bucket": self._performance_summary_bucket(label="bucket", sample_rows=[]),
                },
            }

        symbol_summary = self._performance_summary_bucket(label="symbol", sample_rows=samples)
        engine_rows = [row for row in samples if not strategy_engine or str(row["strategy_engine"]) == strategy_engine]
        scenario_rows = [row for row in samples if not scenario or str(row["scenario"]) == scenario]
        regime_rows = [
            row
            for row in samples
            if (not regime or str(row["regime"]) == regime)
            and (not trend_alignment or str(row["trend_alignment"]) == trend_alignment)
        ]
        bucket_rows = [
            row
            for row in samples
            if (not scenario or str(row["scenario"]) == scenario)
            and (not regime or str(row["regime"]) == regime)
            and (not trend_alignment or str(row["trend_alignment"]) == trend_alignment)
        ]
        if strategy_engine:
            bucket_rows = [row for row in bucket_rows if str(row["strategy_engine"]) == strategy_engine]
        scenario_summary = self._performance_summary_bucket(label="scenario", sample_rows=scenario_rows)
        regime_summary = self._performance_summary_bucket(label="regime", sample_rows=regime_rows)
        bucket_summary = self._performance_summary_bucket(label="bucket", sample_rows=bucket_rows)
        engine_summary = self._performance_summary_bucket(label="engine", sample_rows=engine_rows)
        composite_score = _clamp_score(
            (float(bucket_summary["score"]) * 0.34)
            + (float(engine_summary["score"]) * 0.22)
            + (float(scenario_summary["score"]) * 0.18)
            + (float(regime_summary["score"]) * 0.16)
            + (float(symbol_summary["score"]) * 0.1)
        )
        primary_summary = next(
            (
                component
                for component in (bucket_summary, engine_summary, scenario_summary, regime_summary, symbol_summary)
                if int(component["sample_size"]) > 0
            ),
            symbol_summary,
        )
        underperforming = bool(bucket_summary["underperforming"]) or (
            bool(engine_summary["underperforming"])
            or (bool(scenario_summary["underperforming"]) and bool(regime_summary["underperforming"]))
        )
        return {
            "score": round(composite_score, 6),
            "sample_size": int(primary_summary["sample_size"]),
            "hit_rate": float(primary_summary["hit_rate"]),
            "expectancy": float(primary_summary["expectancy"]),
            "net_pnl_after_fees": float(primary_summary["net_pnl_after_fees"]),
            "avg_signed_slippage_bps": float(primary_summary["avg_signed_slippage_bps"]),
            "avg_time_to_profit_minutes": float(primary_summary["avg_time_to_profit_minutes"]),
            "avg_drawdown_impact": float(primary_summary["avg_drawdown_impact"]),
            "loss_streak": int(primary_summary["loss_streak"]),
            "underperforming": underperforming,
            "components": {
                "symbol": symbol_summary,
                "engine": engine_summary,
                "scenario": scenario_summary,
                "regime": regime_summary,
                "bucket": bucket_summary,
            },
        }

    @staticmethod
    def _market_refresh_cadence_minutes(base: int, *, mode: str) -> int:
        if mode == CADENCE_IDLE_MODE:
            return max(base * 2, 2)
        if mode == CADENCE_ARMED_ENTRY_PLAN_MODE:
            return 1
        if mode in {CADENCE_ACTIVE_POSITION_MODE, CADENCE_HIGH_PRIORITY_RECOVERY_MODE}:
            return max(1, min(base, 2))
        return max(base, 1)

    @staticmethod
    def _position_management_cadence_seconds(base: int, *, mode: str) -> int:
        if mode == CADENCE_IDLE_MODE:
            return max(base * 2, 60)
        if mode in {CADENCE_ACTIVE_POSITION_MODE, CADENCE_HIGH_PRIORITY_RECOVERY_MODE}:
            return max(15, min(base, 30))
        return max(base, 15)

    @staticmethod
    def _decision_cadence_minutes(base: int, *, mode: str) -> int:
        if mode in {
            CADENCE_IDLE_MODE,
            CADENCE_ACTIVE_POSITION_MODE,
            CADENCE_HIGH_PRIORITY_RECOVERY_MODE,
        }:
            return max(base * 2, 2)
        return max(base, 1)

    @staticmethod
    def _ai_cadence_minutes(base: int, *, mode: str, decision_cadence_minutes: int) -> int:
        if mode in {
            CADENCE_IDLE_MODE,
            CADENCE_ACTIVE_POSITION_MODE,
            CADENCE_HIGH_PRIORITY_RECOVERY_MODE,
        }:
            return max(base * 2, decision_cadence_minutes)
        return max(base, 1)

    @staticmethod
    def _cadence_holding_profile_context(
        *,
        open_positions: list[object],
        armed_plans: list[PendingEntryPlan],
    ) -> dict[str, object]:
        for position in open_positions:
            metadata = getattr(position, "metadata_json", None)
            metadata_dict = dict(metadata) if isinstance(metadata, dict) else {}
            management = metadata_dict.get("position_management")
            management_dict = dict(management) if isinstance(management, dict) else {}
            profile = str(management_dict.get("holding_profile") or "scalp").strip().lower() or "scalp"
            return {
                "active_holding_profile": profile,
                "active_holding_profile_reason": str(management_dict.get("holding_profile_reason") or "") or None,
                "holding_profile_cadence_source": "open_position",
                "holding_profile_cadence_hint": resolve_holding_profile_cadence_hint(profile),
            }
        for plan in armed_plans:
            metadata = getattr(plan, "metadata_json", None)
            metadata_dict = dict(metadata) if isinstance(metadata, dict) else {}
            profile = str(metadata_dict.get("holding_profile") or "scalp").strip().lower() or "scalp"
            return {
                "active_holding_profile": profile,
                "active_holding_profile_reason": str(metadata_dict.get("holding_profile_reason") or "") or None,
                "holding_profile_cadence_source": "armed_entry_plan",
                "holding_profile_cadence_hint": resolve_holding_profile_cadence_hint(profile),
            }
        return {
            "active_holding_profile": None,
            "active_holding_profile_reason": None,
            "holding_profile_cadence_source": None,
            "holding_profile_cadence_hint": {},
        }

    def get_symbol_cadence_profile(
        self,
        *,
        symbol: str,
        timeframe: str | None = None,
        runtime_state: dict[str, object] | None = None,
        open_positions: list[object] | None = None,
        feature_payload: object | None = None,
        adaptive_signal_context: dict[str, object] | None = None,
        setup_cluster_context: dict[str, object] | None = None,
        armed_plans: list[PendingEntryPlan] | None = None,
        include_adaptive_underperformance: bool = False,
    ) -> dict[str, object]:
        symbol = symbol.upper()
        effective_settings = self._effective_symbol_settings(symbol)
        timeframe = timeframe or effective_settings.timeframe
        runtime_state = runtime_state or summarize_runtime_state(self.settings_row)
        open_positions = list(open_positions) if open_positions is not None else list(get_open_positions(self.session, symbol))
        armed_plans = list(armed_plans) if armed_plans is not None else self._active_pending_entry_plans(symbol=symbol)
        feature_source = "state_only"
        if feature_payload is None:
            feature_payload = self._latest_feature_snapshot_payload(symbol=symbol, timeframe=timeframe)
            if feature_payload:
                feature_source = "latest_feature_snapshot"
        else:
            feature_source = "current_feature_payload"
        feature_flags = self._cadence_feature_flags(feature_payload)
        holding_profile_context = self._cadence_holding_profile_context(
            open_positions=open_positions,
            armed_plans=armed_plans,
        )
        missing_protection_symbols = {
            str(item).upper()
            for item in runtime_state.get("missing_protection_symbols", [])
            if item
        }
        recovery_active = (
            str(runtime_state.get("operating_state") or "") in {
                PROTECTION_REQUIRED_STATE,
                "DEGRADED_MANAGE_ONLY",
                "EMERGENCY_EXIT",
            }
            or symbol in missing_protection_symbols
        )

        mode = CADENCE_WATCH_MODE
        reasons: list[str] = []
        skip_reason: str | None = None
        ai_skipped_reason: str | None = None
        underperforming = False
        underperforming_reasons: list[str] = []
        setup_disable_active = False
        setup_disable_reasons: list[str] = []
        setup_cluster_active = False
        setup_cluster_reasons: list[str] = []
        if recovery_active:
            mode = CADENCE_HIGH_PRIORITY_RECOVERY_MODE
            reasons = ["HIGH_PRIORITY_RECOVERY_ACTIVE"]
            skip_reason = "HIGH_PRIORITY_RECOVERY_ACTIVE"
        elif armed_plans:
            mode = CADENCE_ARMED_ENTRY_PLAN_MODE
            reasons = ["ARMED_ENTRY_PLAN_ACTIVE"]
            skip_reason = "ARMED_ENTRY_PLAN_ACTIVE"
        elif open_positions:
            mode = CADENCE_ACTIVE_POSITION_MODE
            reasons = ["ACTIVE_POSITION_PRIORITY"]
            skip_reason = "ACTIVE_POSITION_PRIORITY"
        else:
            regime_context = getattr(feature_payload, "regime", None)
            primary_regime = str(getattr(regime_context, "primary_regime", "") or "")
            trend_alignment = str(getattr(regime_context, "trend_alignment", "") or "")
            resolved_adaptive_context = adaptive_signal_context
            if (
                resolved_adaptive_context is None
                and self.settings_row.adaptive_signal_enabled
                and primary_regime
            ):
                resolved_adaptive_context = build_adaptive_signal_context(
                    self.session,
                    enabled=self.settings_row.adaptive_signal_enabled,
                    symbol=symbol,
                    timeframe=timeframe,
                    regime=primary_regime,
                    settings_row=self.settings_row,
                )
            underperforming, underperforming_reasons = self._adaptive_bucket_underperformance(
                resolved_adaptive_context,
            )
            setup_disable_active, setup_disable_reasons = self._adaptive_setup_disable_idle_state(
                resolved_adaptive_context,
            )

            resolved_setup_cluster_context = setup_cluster_context
            if resolved_setup_cluster_context is None and primary_regime and trend_alignment:
                resolved_setup_cluster_context = self._build_setup_cluster_context(
                    symbol=symbol,
                    timeframe=timeframe,
                    regime=primary_regime,
                    trend_alignment=trend_alignment,
                )
            setup_cluster_active, setup_cluster_reasons = self._setup_cluster_idle_state(
                resolved_setup_cluster_context,
            )

            idle_reasons: list[str] = []
            if bool(feature_flags.get("no_trade_zone")):
                idle_reasons.append("RANGE_WEAK_VOLUME_NO_TRADE_ZONE")
                ai_skipped_reason = "CADENCE_IDLE_NO_TRADE_ZONE"
            if include_adaptive_underperformance and underperforming:
                idle_reasons.extend(["ADAPTIVE_BUCKET_UNDERPERFORMING", *underperforming_reasons])
                ai_skipped_reason = ai_skipped_reason or "CADENCE_IDLE_UNDERPERFORMING_BUCKET"
            if setup_disable_active:
                idle_reasons.extend(setup_disable_reasons)
                ai_skipped_reason = ai_skipped_reason or "CADENCE_IDLE_SETUP_DISABLE_ACTIVE"
            if setup_cluster_active:
                idle_reasons.extend(setup_cluster_reasons)
                ai_skipped_reason = ai_skipped_reason or "CADENCE_IDLE_SETUP_CLUSTER_ACTIVE"
            if idle_reasons:
                mode = CADENCE_IDLE_MODE
                reasons = list(dict.fromkeys(idle_reasons))
                skip_reason = reasons[0]

        decision_cadence = self._decision_cadence_minutes(
            effective_settings.decision_cycle_interval_minutes,
            mode=mode,
        )
        position_management_cadence = self._position_management_cadence_seconds(
            effective_settings.position_management_interval_seconds,
            mode=mode,
        )
        watcher_cadence = 1 if armed_plans else None
        cadence_hint = holding_profile_context.get("holding_profile_cadence_hint")
        if isinstance(cadence_hint, dict):
            hint_decision = cadence_hint.get("decision_interval_minutes")
            hint_position_management = cadence_hint.get("position_management_interval_seconds")
            hint_watcher = cadence_hint.get("entry_plan_watcher_interval_minutes")
            if mode == CADENCE_ACTIVE_POSITION_MODE:
                if isinstance(hint_decision, int) and hint_decision > 0:
                    decision_cadence = max(decision_cadence, hint_decision)
                if isinstance(hint_position_management, int) and hint_position_management > 0:
                    position_management_cadence = max(position_management_cadence, hint_position_management)
            elif mode == CADENCE_ARMED_ENTRY_PLAN_MODE and isinstance(hint_decision, int) and hint_decision > 0:
                decision_cadence = max(decision_cadence, hint_decision)
            if armed_plans and isinstance(hint_watcher, int) and hint_watcher > 0:
                watcher_cadence = max(watcher_cadence or hint_watcher, hint_watcher)
        ai_cadence = self._ai_cadence_minutes(
            effective_settings.ai_call_interval_minutes,
            mode=mode,
            decision_cadence_minutes=decision_cadence,
        )
        ai_cadence = max(ai_cadence, decision_cadence)
        return {
            "mode": mode,
            "reasons": reasons,
            "skip_reason": skip_reason,
            "ai_skipped_reason": ai_skipped_reason,
            "watcher_enabled": bool(armed_plans),
            "feature_source": feature_source,
            "feature_flags": feature_flags,
            "bucket_underperforming": include_adaptive_underperformance and underperforming,
            "bucket_underperforming_reasons": underperforming_reasons if include_adaptive_underperformance else [],
            "setup_disable_active": setup_disable_active,
            "setup_disable_reasons": setup_disable_reasons,
            "setup_cluster_active": setup_cluster_active,
            "setup_cluster_reasons": setup_cluster_reasons,
            "active_holding_profile": holding_profile_context.get("active_holding_profile"),
            "active_holding_profile_reason": holding_profile_context.get("active_holding_profile_reason"),
            "holding_profile_cadence_source": holding_profile_context.get("holding_profile_cadence_source"),
            "holding_profile_cadence_hint": dict(holding_profile_context.get("holding_profile_cadence_hint") or {}),
            "effective_cadence": {
                "market_refresh_interval_minutes": self._market_refresh_cadence_minutes(
                    effective_settings.market_refresh_interval_minutes,
                    mode=mode,
                ),
                "position_management_interval_seconds": position_management_cadence,
                "decision_cycle_interval_minutes": decision_cadence,
                "ai_call_interval_minutes": ai_cadence,
                "entry_plan_watcher_interval_minutes": watcher_cadence,
            },
        }

    def _should_skip_same_candle_entry(
        self,
        *,
        symbol: str,
        timeframe: str,
        market_snapshot: MarketSnapshotPayload,
        has_open_position: bool,
    ) -> bool:
        if has_open_position:
            return False
        latest_snapshot_time = self._latest_decision_snapshot_time(symbol, timeframe)
        if latest_snapshot_time is None:
            return False
        return latest_snapshot_time == market_snapshot.snapshot_time.isoformat()

    def _latest_alerts(self, limit: int = 5) -> list[Alert]:
        return list(self.session.scalars(select(Alert).order_by(desc(Alert.created_at)).limit(limit)))


    def _latest_health_events(self, limit: int = 10) -> list[SystemHealthEvent]:
        return list(self.session.scalars(select(SystemHealthEvent).order_by(desc(SystemHealthEvent.created_at)).limit(limit)))

    @staticmethod
    def _pending_entry_plan_idempotency_key(
        *,
        symbol: str,
        side: str,
        source_decision_run_id: int,
        expires_at: datetime,
    ) -> str:
        return f"pending-plan:{symbol.upper()}:{side}:{source_decision_run_id}:{expires_at.isoformat()}"

    @staticmethod
    def _pending_entry_plan_metadata(plan: PendingEntryPlan) -> dict[str, object]:
        return dict(plan.metadata_json) if isinstance(plan.metadata_json, dict) else {}

    @staticmethod
    def _pending_entry_plan_snapshot(plan: PendingEntryPlan | None) -> PendingEntryPlanSnapshot:
        if plan is None:
            return PendingEntryPlanSnapshot()
        metadata = (
            dict(plan.metadata_json)
            if isinstance(plan.metadata_json, dict)
            else {}
        )
        trigger_details = metadata.get("trigger_details")
        return PendingEntryPlanSnapshot(
            plan_id=plan.id,
            symbol=plan.symbol,
            side=plan.side if plan.side in {"long", "short"} else None,
            plan_status=plan.plan_status if plan.plan_status in {"armed", "triggered", "expired", "canceled"} else None,
            source_decision_run_id=plan.source_decision_run_id,
            source_timeframe=plan.source_timeframe,
            regime=plan.regime,
            posture=plan.posture,
            rationale_codes=list(plan.rationale_codes or []),
            entry_mode=plan.entry_mode if plan.entry_mode in {"breakout_confirm", "pullback_confirm", "immediate", "none"} else None,
            holding_profile=str(metadata.get("holding_profile") or "scalp"),
            holding_profile_reason=str(metadata.get("holding_profile_reason") or "") or None,
            entry_zone_min=plan.entry_zone_min,
            entry_zone_max=plan.entry_zone_max,
            invalidation_price=plan.invalidation_price,
            max_chase_bps=plan.max_chase_bps,
            idea_ttl_minutes=plan.idea_ttl_minutes,
            stop_loss=plan.stop_loss,
            take_profit=plan.take_profit,
            risk_pct_cap=plan.risk_pct_cap,
            leverage_cap=plan.leverage_cap,
            created_at=plan.created_at,
            expires_at=plan.expires_at,
            triggered_at=plan.triggered_at,
            canceled_at=plan.canceled_at,
            canceled_reason=plan.canceled_reason,
            idempotency_key=plan.idempotency_key,
            trigger_details=dict(trigger_details) if isinstance(trigger_details, dict) else {},
        )

    def _active_pending_entry_plans(
        self,
        *,
        symbol: str | None = None,
    ) -> list[PendingEntryPlan]:
        query = select(PendingEntryPlan).where(PendingEntryPlan.plan_status == ACTIVE_ENTRY_PLAN_STATUS)
        if symbol is not None:
            query = query.where(PendingEntryPlan.symbol == symbol.upper())
        return list(self.session.scalars(query.order_by(PendingEntryPlan.created_at.desc())))

    def _entry_plan_posture(self, *, decision_side: str, feature_payload) -> str:
        state = str(feature_payload.pullback_context.state or "unclear")
        if decision_side == "long":
            if state == "bullish_pullback":
                return "bullish_pullback"
            if state == "bullish_continuation":
                return "bullish_continuation"
            if feature_payload.breakout.range_breakout_direction == "up" or feature_payload.breakout.broke_swing_high:
                return "breakout_exception"
        if decision_side == "short":
            if state == "bearish_pullback":
                return "bearish_pullback"
            if state == "bearish_continuation":
                return "bearish_continuation"
            if feature_payload.breakout.range_breakout_direction == "down" or feature_payload.breakout.broke_swing_low:
                return "breakout_exception"
        return state

    def _cancel_pending_entry_plan(
        self,
        plan: PendingEntryPlan,
        *,
        reason: str,
        cancel_status: str = "canceled",
        detail: dict[str, object] | None = None,
        correlation_ids: dict[str, object] | None = None,
    ) -> PendingEntryPlan:
        if plan.plan_status != ACTIVE_ENTRY_PLAN_STATUS:
            return plan
        now = utcnow_naive()
        metadata = self._pending_entry_plan_metadata(plan)
        metadata["last_transition_at"] = now.isoformat()
        metadata["last_transition_reason"] = reason
        if detail:
            metadata["last_transition_detail"] = dict(detail)
        plan.plan_status = "expired" if cancel_status == "expired" else "canceled"
        plan.canceled_at = now
        plan.canceled_reason = reason
        plan.metadata_json = metadata
        self.session.add(plan)
        self.session.flush()
        record_audit_event(
            self.session,
            event_type="pending_entry_plan_expired" if cancel_status == "expired" else "pending_entry_plan_canceled",
            entity_type="pending_entry_plan",
            entity_id=str(plan.id),
            severity="info" if cancel_status == "expired" else "warning",
            message="Pending entry plan transitioned out of armed status.",
            payload={
                "symbol": plan.symbol,
                "side": plan.side,
                "plan_status": plan.plan_status,
                "reason": reason,
                "source_decision_run_id": plan.source_decision_run_id,
                "detail": dict(detail or {}),
            },
            correlation_ids=correlation_ids,
        )
        return plan

    def _plan_entry_allowed_without_trigger(self, decision: object, risk_result) -> bool:
        decision_side = str(getattr(decision, "decision", "") or "")
        if decision_side not in {"long", "short"}:
            return False
        blockers = set(getattr(risk_result, "blocked_reason_codes", []) or getattr(risk_result, "reason_codes", []))
        return len(blockers - ENTRY_PLAN_NON_STRUCTURAL_BLOCKERS) == 0

    def _arm_pending_entry_plan(
        self,
        *,
        decision,
        decision_run: AgentRun,
        risk_result,
        risk_row_id: int | None,
        feature_payload,
        cycle_id: str,
        snapshot_id: int,
        replace_reason: str = "REPLACED_BY_NEW_APPROVED_PLAN",
    ) -> PendingEntryPlan:
        symbol = decision.symbol.upper()
        side = str(decision.decision)
        expires_at = decision_run.created_at + timedelta(minutes=max(int(decision.idea_ttl_minutes or 15), 1))
        idempotency_key = self._pending_entry_plan_idempotency_key(
            symbol=symbol,
            side=side,
            source_decision_run_id=decision_run.id,
            expires_at=expires_at,
        )
        decision_correlation_ids = normalize_correlation_ids(
            cycle_id=cycle_id,
            snapshot_id=snapshot_id,
            decision_id=decision_run.id,
        )
        for existing in self._active_pending_entry_plans(symbol=symbol):
            cancel_reason = "NEW_AI_HOLD_DECISION"
            if existing.side == side:
                cancel_reason = replace_reason
            elif existing.side != side:
                cancel_reason = "OPPOSITE_AI_PLAN_REPLACED"
            self._cancel_pending_entry_plan(
                existing,
                reason=cancel_reason,
                detail={"replacement_decision_run_id": decision_run.id, "replacement_side": side},
                correlation_ids=decision_correlation_ids,
            )
        plan = PendingEntryPlan(
            symbol=symbol,
            side=side,
            plan_status=ACTIVE_ENTRY_PLAN_STATUS,
            source_decision_run_id=decision_run.id,
            regime=feature_payload.regime.primary_regime,
            posture=self._entry_plan_posture(decision_side=side, feature_payload=feature_payload),
            rationale_codes=list(dict.fromkeys(decision.rationale_codes)),
            source_timeframe=decision.timeframe,
            entry_mode=decision.entry_mode or "pullback_confirm",
            entry_zone_min=float(decision.entry_zone_min or 0.0),
            entry_zone_max=float(decision.entry_zone_max or 0.0),
            invalidation_price=decision.invalidation_price,
            max_chase_bps=decision.max_chase_bps,
            idea_ttl_minutes=decision.idea_ttl_minutes,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
            risk_pct_cap=(
                float(risk_result.approved_risk_pct)
                if float(risk_result.approved_risk_pct or 0.0) > 0.0
                else float(decision.risk_pct)
            ),
            leverage_cap=(
                float(risk_result.approved_leverage)
                if float(risk_result.approved_leverage or 0.0) > 0.0
                else float(decision.leverage)
            ),
            expires_at=expires_at,
            idempotency_key=idempotency_key,
            metadata_json={
                "cycle_id": cycle_id,
                "snapshot_id": snapshot_id,
                "source_risk_check_id": risk_row_id,
                "source_blocked_reason_codes": list(getattr(risk_result, "blocked_reason_codes", [])),
                "source_adjustment_reason_codes": list(getattr(risk_result, "adjustment_reason_codes", [])),
                "holding_profile": str(getattr(decision, "holding_profile", "scalp") or "scalp"),
                "holding_profile_reason": str(getattr(decision, "holding_profile_reason", "") or "") or None,
                "trigger_details": {},
            },
        )
        self.session.add(plan)
        self.session.flush()
        record_audit_event(
            self.session,
            event_type="pending_entry_plan_armed",
            entity_type="pending_entry_plan",
            entity_id=str(plan.id),
            severity="info",
            message="A pending entry plan was armed from the latest AI decision.",
            payload={
                "symbol": plan.symbol,
                "side": plan.side,
                "source_decision_run_id": plan.source_decision_run_id,
                "entry_mode": plan.entry_mode,
                "holding_profile": plan.metadata_json.get("holding_profile") if isinstance(plan.metadata_json, dict) else "scalp",
                "entry_zone_min": plan.entry_zone_min,
                "entry_zone_max": plan.entry_zone_max,
                "expires_at": plan.expires_at.isoformat(),
                "idempotency_key": plan.idempotency_key,
            },
            correlation_ids=decision_correlation_ids,
        )
        return plan

    def _cancel_symbol_entry_plans_from_decision(
        self,
        *,
        symbol: str,
        decision_side: str,
        decision_run_id: int | None,
        cycle_id: str,
        snapshot_id: int,
    ) -> list[PendingEntryPlanSnapshot]:
        decision_correlation_ids = normalize_correlation_ids(
            cycle_id=cycle_id,
            snapshot_id=snapshot_id,
            decision_id=decision_run_id,
        )
        canceled: list[PendingEntryPlanSnapshot] = []
        for plan in self._active_pending_entry_plans(symbol=symbol):
            should_cancel = decision_side == "hold" or plan.side != decision_side
            if not should_cancel:
                continue
            reason = "NEW_AI_HOLD_DECISION" if decision_side == "hold" else "OPPOSITE_AI_PLAN_REPLACED"
            canceled.append(
                self._pending_entry_plan_snapshot(
                    self._cancel_pending_entry_plan(
                        plan,
                        reason=reason,
                        detail={"decision_side": decision_side},
                        correlation_ids=decision_correlation_ids,
                    )
                )
            )
        return canceled

    @staticmethod
    def _plan_zone_interacted(plan: PendingEntryPlan, candle) -> bool:
        return candle.low <= plan.entry_zone_max and candle.high >= plan.entry_zone_min

    @staticmethod
    def _plan_chase_bps(plan: PendingEntryPlan, latest_price: float) -> float:
        if plan.side == "long":
            anchor = max(plan.entry_zone_max, plan.entry_zone_min, 1.0)
            return max(((latest_price - anchor) / anchor) * 10_000, 0.0)
        anchor = max(min(plan.entry_zone_min, plan.entry_zone_max), 1.0)
        return max(((anchor - latest_price) / anchor) * 10_000, 0.0)

    @staticmethod
    def _build_plan_confirm_detail(plan: PendingEntryPlan, market_snapshot: MarketSnapshotPayload) -> dict[str, object]:
        candles = market_snapshot.candles
        last_candle = candles[-1] if candles else None
        previous_candle = candles[-2] if len(candles) >= 2 else None
        quality_threshold = 0.72 if plan.entry_mode == "breakout_confirm" else 0.66 if "continuation" in str(plan.posture or "") else 0.62
        if last_candle is None:
            return {
                "zone_entered": False,
                "confirm_met": False,
                "reason": "NO_1M_CANDLE",
                "quality_score": 0.0,
                "quality_threshold": quality_threshold,
                "quality_state": "waiting",
                "cancel_recommended": False,
                "quality_components": {},
            }
        zone_entered = TradingOrchestrator._plan_zone_interacted(plan, last_candle)
        candle_range = max(last_candle.high - last_candle.low, 1e-9)
        candle_body_ratio = abs(last_candle.close - last_candle.open) / candle_range
        lower_wick_ratio = max(min(last_candle.open, last_candle.close) - last_candle.low, 0.0) / candle_range
        upper_wick_ratio = max(last_candle.high - max(last_candle.open, last_candle.close), 0.0) / candle_range
        if plan.side == "long":
            close_reclaimed = last_candle.close >= plan.entry_zone_max
            structure_break = previous_candle is not None and last_candle.close > previous_candle.high
            wick_reclaim = lower_wick_ratio >= 0.35 and last_candle.close >= (last_candle.low + candle_range * 0.6)
            wick_imbalance = lower_wick_ratio - upper_wick_ratio
            entry_anchor = max(plan.entry_zone_min, plan.entry_zone_max)
        else:
            close_reclaimed = last_candle.close <= plan.entry_zone_min
            structure_break = previous_candle is not None and last_candle.close < previous_candle.low
            wick_reclaim = upper_wick_ratio >= 0.35 and last_candle.close <= (last_candle.high - candle_range * 0.6)
            wick_imbalance = upper_wick_ratio - lower_wick_ratio
            entry_anchor = min(plan.entry_zone_min, plan.entry_zone_max)
        reclaim_signal_score = min(
            (0.55 if close_reclaimed else 0.0)
            + (0.3 if structure_break else 0.0)
            + (0.2 if wick_reclaim else 0.0),
            1.0,
        )
        body_quality = min(max((candle_body_ratio - 0.18) / 0.42, 0.0), 1.0)
        wick_quality = min(max((wick_imbalance + 0.05) / 0.45, 0.0), 1.0)
        observed_chase_bps = TradingOrchestrator._plan_chase_bps(plan, market_snapshot.latest_price)
        if plan.max_chase_bps is not None and plan.max_chase_bps > 0:
            chase_ratio = observed_chase_bps / plan.max_chase_bps
            late_chase = chase_ratio >= 0.8
            severe_late_chase = chase_ratio >= 1.35
            if chase_ratio <= 0.5:
                chase_quality = 1.0
            elif chase_ratio <= 0.8:
                chase_quality = 0.85
            elif chase_ratio <= 1.0:
                chase_quality = 0.55
            elif chase_ratio <= 1.2:
                chase_quality = 0.25
            else:
                chase_quality = 0.0
        else:
            chase_ratio = None
            late_chase = False
            severe_late_chase = False
            chase_quality = 1.0
        baseline_expected_rr = None
        current_expected_rr = None
        expected_rr_deterioration_pct = None
        if plan.invalidation_price is not None and plan.take_profit is not None:
            if plan.side == "long":
                baseline_risk = entry_anchor - plan.invalidation_price
                baseline_reward = plan.take_profit - entry_anchor
                current_risk = market_snapshot.latest_price - plan.invalidation_price
                current_reward = plan.take_profit - market_snapshot.latest_price
            else:
                baseline_risk = plan.invalidation_price - entry_anchor
                baseline_reward = entry_anchor - plan.take_profit
                current_risk = plan.invalidation_price - market_snapshot.latest_price
                current_reward = market_snapshot.latest_price - plan.take_profit
            if baseline_risk > 0 and baseline_reward > 0 and current_risk > 0 and current_reward > 0:
                baseline_expected_rr = baseline_reward / baseline_risk
                current_expected_rr = current_reward / current_risk
                expected_rr_deterioration_pct = min(
                    max((baseline_expected_rr - current_expected_rr) / max(baseline_expected_rr, 1e-9), 0.0),
                    1.0,
                )
        if current_expected_rr is None:
            rr_quality = 0.5
        elif current_expected_rr >= 1.8:
            rr_quality = 1.0
        elif current_expected_rr >= 1.4:
            rr_quality = 0.8
        elif current_expected_rr >= 1.1:
            rr_quality = 0.6
        elif current_expected_rr >= 0.9:
            rr_quality = 0.35
        else:
            rr_quality = 0.1
        if expected_rr_deterioration_pct is not None:
            rr_quality *= max(0.0, 1.0 - expected_rr_deterioration_pct * 0.7)
        quality_components = {
            "zone_entered": 1.0 if zone_entered else 0.0,
            "reclaim_signal_strength": round(reclaim_signal_score, 4),
            "candle_body_quality": round(body_quality, 4),
            "wick_imbalance_quality": round(wick_quality, 4),
            "late_chase_quality": round(chase_quality, 4),
            "expected_rr_quality": round(rr_quality, 4),
        }
        quality_score = 0.0
        if zone_entered:
            quality_score = (
                reclaim_signal_score * 0.34
                + body_quality * 0.18
                + wick_quality * 0.16
                + chase_quality * 0.16
                + rr_quality * 0.16
            )
            if close_reclaimed and structure_break:
                quality_score += 0.04
            quality_score = min(max(quality_score, 0.0), 1.0)
        rr_collapse = bool(
            current_expected_rr is not None
            and current_expected_rr < 0.85
            and (expected_rr_deterioration_pct or 0.0) >= 0.5
        )
        cancel_recommended = bool(
            zone_entered
            and not (
                reclaim_signal_score >= 0.55
                and quality_score >= quality_threshold
            )
            and (
                (
                    severe_late_chase
                    and (
                        current_expected_rr is None
                        or current_expected_rr < 1.0
                        or quality_score < quality_threshold
                    )
                )
                or (
                    rr_collapse
                    and quality_score < max(quality_threshold - 0.08, 0.5)
                )
            )
        )
        confirm_met = bool(
            zone_entered
            and reclaim_signal_score >= 0.55
            and quality_score >= quality_threshold
        )
        if confirm_met:
            quality_state = "trigger"
            reason = "QUALITY_CONFIRMED"
        elif cancel_recommended:
            quality_state = "cancel"
            reason = "QUALITY_REJECTED_LATE_CHASE" if severe_late_chase else "QUALITY_REJECTED_RR_DETERIORATED"
        elif not zone_entered:
            quality_state = "waiting"
            reason = "ZONE_NOT_ENTERED"
        else:
            quality_state = "waiting"
            reason = "QUALITY_BELOW_THRESHOLD"
        return {
            "zone_entered": zone_entered,
            "confirm_met": confirm_met,
            "quality_score": round(quality_score, 4),
            "quality_threshold": quality_threshold,
            "quality_state": quality_state,
            "cancel_recommended": cancel_recommended,
            "quality_components": quality_components,
            "reason": reason,
            "close_reclaimed": close_reclaimed,
            "structure_break": structure_break,
            "wick_reclaim": wick_reclaim,
            "candle_body_ratio": round(candle_body_ratio, 4),
            "wick_imbalance": round(wick_imbalance, 4),
            "lower_wick_ratio": round(lower_wick_ratio, 4),
            "upper_wick_ratio": round(upper_wick_ratio, 4),
            "late_chase": late_chase,
            "late_chase_ratio": round(chase_ratio, 4) if chase_ratio is not None else None,
            "observed_chase_bps": round(observed_chase_bps, 6),
            "baseline_expected_rr": round(baseline_expected_rr, 4) if baseline_expected_rr is not None else None,
            "current_expected_rr": round(current_expected_rr, 4) if current_expected_rr is not None else None,
            "expected_rr_deterioration_pct": (
                round(expected_rr_deterioration_pct, 4) if expected_rr_deterioration_pct is not None else None
            ),
            "last_candle": {
                "timestamp": last_candle.timestamp.isoformat(),
                "open": last_candle.open,
                "high": last_candle.high,
                "low": last_candle.low,
                "close": last_candle.close,
            },
            "previous_candle": (
                {
                    "timestamp": previous_candle.timestamp.isoformat(),
                    "open": previous_candle.open,
                    "high": previous_candle.high,
                    "low": previous_candle.low,
                    "close": previous_candle.close,
                }
                if previous_candle is not None
                else None
            ),
        }

    @staticmethod
    def _plan_invalidation_broken(plan: PendingEntryPlan, market_snapshot: MarketSnapshotPayload) -> bool:
        if plan.invalidation_price is None or not market_snapshot.candles:
            return False
        last_candle = market_snapshot.candles[-1]
        if plan.side == "long":
            return market_snapshot.latest_price <= plan.invalidation_price or last_candle.low <= plan.invalidation_price
        return market_snapshot.latest_price >= plan.invalidation_price or last_candle.high >= plan.invalidation_price

    @staticmethod
    def _trigger_execution_decision_from_plan(
        plan: PendingEntryPlan,
        market_snapshot: MarketSnapshotPayload,
        source_decision: TradeDecision,
    ) -> TradeDecision:
        latest_price = market_snapshot.latest_price
        return source_decision.model_copy(
            update={
                "entry_mode": "immediate",
                "entry_zone_min": latest_price,
                "entry_zone_max": latest_price,
                "risk_pct": plan.risk_pct_cap if plan.risk_pct_cap > 0 else source_decision.risk_pct,
                "leverage": plan.leverage_cap if plan.leverage_cap > 0 else source_decision.leverage,
                "rationale_codes": list(dict.fromkeys([*source_decision.rationale_codes, "PENDING_ENTRY_PLAN_TRIGGERED"])),
            }
        )

    def _mark_pending_entry_plan_triggered(
        self,
        plan: PendingEntryPlan,
        *,
        execution_result: dict[str, object],
        correlation_ids: dict[str, object] | None = None,
    ) -> PendingEntryPlan:
        now = utcnow_naive()
        metadata = self._pending_entry_plan_metadata(plan)
        metadata["last_transition_at"] = now.isoformat()
        metadata["last_transition_reason"] = "PLAN_EXECUTED"
        metadata["execution_result"] = dict(execution_result)
        plan.plan_status = "triggered"
        plan.triggered_at = now
        plan.metadata_json = metadata
        self.session.add(plan)
        self.session.flush()
        record_audit_event(
            self.session,
            event_type="pending_entry_plan_triggered",
            entity_type="pending_entry_plan",
            entity_id=str(plan.id),
            severity="info",
            message="Pending entry plan triggered into live execution.",
            payload={
                "symbol": plan.symbol,
                "side": plan.side,
                "execution_result": dict(execution_result),
            },
            correlation_ids=correlation_ids,
        )
        return plan

    def _latest_decision_run(self, *, decision_run_id: int | None) -> AgentRun | None:
        if decision_run_id is None:
            return None
        return self.session.get(AgentRun, decision_run_id)

    def run_exchange_sync_cycle(
        self,
        *,
        symbol: str | None = None,
        trigger_event: str = "background_poll",
    ) -> dict[str, object]:
        if not self.credentials.binance_api_key or not self.credentials.binance_api_secret:
            skipped_at = utcnow_naive()
            effective_symbol = symbol or self.settings_row.default_symbol
            for scope in ("account", "positions", "open_orders", "protective_orders"):
                mark_sync_skipped(
                    self.settings_row,
                    scope=scope,
                    reason_code="LIVE_CREDENTIALS_MISSING",
                    observed_at=skipped_at,
                    detail={"symbol": effective_symbol, "trigger_event": trigger_event},
                )
            self.session.add(self.settings_row)
            self.session.flush()
            return {
                "status": "skipped",
                "reason": "LIVE_CREDENTIALS_MISSING",
                "symbol": effective_symbol,
                "sync_freshness_summary": build_sync_freshness_summary(self.settings_row),
            }
        try:
            result = sync_live_state(self.session, self.settings_row, symbol=symbol)
        except Exception as exc:
            record_audit_event(
                self.session,
                event_type="live_poll_sync_failed",
                entity_type="binance",
                entity_id=symbol or self.settings_row.default_symbol,
                severity="warning",
                message="Background exchange polling sync failed.",
                payload={"trigger_event": trigger_event, "error": str(exc)},
            )
            record_health_event(
                self.session,
                component="live_sync",
                status="error",
                message="Background exchange polling sync failed.",
                payload={"trigger_event": trigger_event, "error": str(exc)},
            )
            return {
                "status": "error",
                "symbol": symbol or self.settings_row.default_symbol,
                "trigger_event": trigger_event,
                "error": str(exc),
                "sync_freshness_summary": build_sync_freshness_summary(self.settings_row),
            }
        record_audit_event(
            self.session,
            event_type="live_poll_sync",
            entity_type="binance",
            entity_id=symbol or self.settings_row.default_symbol,
            severity="info",
            message="Background exchange polling sync completed.",
            payload={"trigger_event": trigger_event, **result},
        )
        return {"status": "ok", "trigger_event": trigger_event, **result}

    def _account_snapshot_preview(self) -> dict[str, float | int | str]:
        latest = self.session.scalar(select(PnLSnapshot).order_by(desc(PnLSnapshot.created_at)).limit(1))
        if latest is not None:
            return account_snapshot_to_dict(latest)
        return {
            "snapshot_date": utcnow_naive().date().isoformat(),
            "equity": self.settings_row.starting_equity,
            "cash_balance": self.settings_row.starting_equity,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "daily_pnl": 0.0,
            "cumulative_pnl": 0.0,
            "consecutive_losses": 0,
        }

    @staticmethod
    def _should_attempt_auto_resume(trigger_event: str) -> bool:
        return trigger_event != "historical_replay"

    def _ensure_auto_resume(
        self,
        *,
        trigger_event: str,
        auto_resume_checked: bool,
    ) -> dict[str, object] | None:
        if auto_resume_checked or not self._should_attempt_auto_resume(trigger_event):
            return None
        return attempt_auto_resume(
            self.session,
            self.settings_row,
            trigger_source=trigger_event,
        )

    @staticmethod
    def _decision_reference_sync_at(sync_freshness_summary: dict[str, object], scope: str) -> str | None:
        scope_payload = sync_freshness_summary.get(scope)
        if not isinstance(scope_payload, dict):
            return None
        last_sync_at = scope_payload.get("last_sync_at")
        if isinstance(last_sync_at, datetime):
            return last_sync_at.isoformat()
        if isinstance(last_sync_at, str) and last_sync_at:
            return last_sync_at
        return None

    @staticmethod
    def _decision_reference_has_blocking_freshness(
        *,
        market_snapshot: MarketSnapshotPayload,
        sync_freshness_summary: dict[str, object],
    ) -> bool:
        if market_snapshot.is_stale or not market_snapshot.is_complete:
            return True
        for scope_payload in sync_freshness_summary.values():
            if not isinstance(scope_payload, dict):
                continue
            if bool(scope_payload.get("stale")) or bool(scope_payload.get("incomplete")):
                return True
        return False

    def _build_decision_reference_payload(
        self,
        *,
        symbol: str,
        timeframe: str,
        market_snapshot: MarketSnapshotPayload,
        market_row: MarketSnapshot,
        runtime_state: dict[str, object] | None = None,
    ) -> dict[str, object]:
        operational_status = build_operational_status_payload(
            self.settings_row,
            session=self.session,
            runtime_state=runtime_state,
        )
        market_freshness_summary = {
            "symbol": symbol,
            "timeframe": timeframe,
            "source": "decision_cycle",
            "status": "fresh"
            if not market_snapshot.is_stale and market_snapshot.is_complete
            else ("stale" if market_snapshot.is_stale else "incomplete"),
            "snapshot_at": market_snapshot.snapshot_time.isoformat(),
            "stale": market_snapshot.is_stale,
            "incomplete": not market_snapshot.is_complete,
            "latest_price": market_snapshot.latest_price,
            "snapshot_id": market_row.id,
        }
        sync_freshness_summary = {
            str(scope): dict(payload)
            for scope, payload in operational_status.sync_freshness_summary.items()
            if isinstance(payload, dict)
        }
        freshness_blocking = self._decision_reference_has_blocking_freshness(
            market_snapshot=market_snapshot,
            sync_freshness_summary=sync_freshness_summary,
        )
        return {
            "market_snapshot_id": market_row.id,
            "market_snapshot_at": market_snapshot.snapshot_time.isoformat(),
            "market_snapshot_source": "refreshed",
            "market_snapshot_stale": market_snapshot.is_stale,
            "market_snapshot_incomplete": not market_snapshot.is_complete,
            "account_sync_at": (
                str(operational_status.account_sync_summary.get("last_synced_at") or "") or None
            ),
            "positions_sync_at": self._decision_reference_sync_at(sync_freshness_summary, "positions"),
            "open_orders_sync_at": self._decision_reference_sync_at(sync_freshness_summary, "open_orders"),
            "protective_orders_sync_at": self._decision_reference_sync_at(sync_freshness_summary, "protective_orders"),
            "account_sync_status": str(operational_status.account_sync_summary.get("status") or "") or None,
            "sync_freshness_summary": sync_freshness_summary,
            "market_freshness_summary": market_freshness_summary,
            "freshness_blocking": freshness_blocking,
            "display_gap": False,
            "display_gap_reason": (
                "The decision used stale or incomplete market/account/order state, so new entry should remain blocked."
                if freshness_blocking
                else None
            ),
        }

    def _collect_market_snapshot(
        self,
        *,
        symbol: str,
        timeframe: str,
        upto_index: int | None,
        force_stale: bool,
    ) -> tuple[MarketSnapshotPayload, MarketSnapshot]:
        market_snapshot = build_market_snapshot(
            symbol=symbol,
            timeframe=timeframe,
            upto_index=upto_index,
            force_stale=force_stale,
            use_binance=self.settings_row.binance_market_data_enabled,
            binance_testnet_enabled=self.settings_row.binance_testnet_enabled,
            stale_threshold_seconds=self.settings_row.stale_market_seconds,
        )
        market_row = persist_market_snapshot(self.session, market_snapshot)
        if self.settings_row.ai_enabled:
            record_audit_event(
                self.session,
                event_type="market_snapshot",
                entity_type="market_snapshot",
                entity_id=str(market_row.id),
                message="Market snapshot collected.",
                payload={"symbol": symbol, "timeframe": timeframe},
            )
        return market_snapshot, market_row

    def _recent_symbol_decisions(self, symbol: str, *, limit: int = 8) -> list[AgentRun]:
        rows = list(
            self.session.scalars(
                select(AgentRun)
                .where(AgentRun.role == AgentRole.TRADING_DECISION.value)
                .order_by(desc(AgentRun.created_at))
                .limit(max(limit * 6, 24))
            )
        )
        symbol_key = symbol.upper()
        return [
            row
            for row in rows
            if isinstance(row.output_payload, dict)
            and str(row.output_payload.get("symbol") or "").upper() == symbol_key
        ][:limit]

    def _recent_signal_performance_score(self, symbol: str) -> float:
        return _safe_float(
            self._recent_signal_performance_summary(
                symbol=symbol,
                timeframe="",
                scenario="",
                regime="",
                trend_alignment="",
            ).get("score"),
            default=0.55,
        )

    def _slippage_sensitivity_score(self, symbol: str) -> float:
        executions = list(
            self.session.scalars(
                select(Execution)
                .where(Execution.symbol == symbol.upper())
                .order_by(desc(Execution.created_at))
                .limit(8)
            )
        )
        if not executions:
            return 0.65
        avg_adverse_signed_slippage_bps = sum(
            max(self._signed_slippage_bps(row), 0.0)
            for row in executions
        ) / max(len(executions), 1)
        threshold_bps = max(float(self.settings_row.slippage_threshold_pct or 0.0) * 10_000.0, 10.0)
        return _clamp_score(0.92 - (min(avg_adverse_signed_slippage_bps / threshold_bps, 1.0) * 0.55))

    def _confidence_consistency_score(self, symbol: str, *, decision: str) -> float:
        decision_rows = self._recent_symbol_decisions(symbol, limit=6)
        if not decision_rows:
            return 0.55
        confidences: list[float] = []
        same_direction = 0
        directional_rows = 0
        for row in decision_rows:
            payload = row.output_payload if isinstance(row.output_payload, dict) else {}
            recent_decision = str(payload.get("decision") or "")
            confidence = float(payload.get("confidence") or 0.0)
            confidences.append(confidence)
            if recent_decision in {"long", "short"}:
                directional_rows += 1
                if recent_decision == decision:
                    same_direction += 1
        avg_confidence = sum(confidences) / max(len(confidences), 1)
        direction_ratio = same_direction / max(directional_rows, 1) if decision in {"long", "short"} else 0.5
        return _clamp_score((avg_confidence * 0.6) + (direction_ratio * 0.4))

    def _candidate_exposure_impact_score(self, *, symbol: str, priority: bool, total_open_positions: int) -> float:
        if priority:
            return 1.0
        tracked_count = max(len(get_effective_symbols(self.settings_row)), 1)
        crowding_ratio = total_open_positions / tracked_count
        symbol_is_open = bool(get_open_positions(self.session, symbol))
        base = 0.9 if not symbol_is_open else 0.75
        return _clamp_score(base - (crowding_ratio * 0.25), lower=0.2, upper=1.0)

    @staticmethod
    def _derivatives_summary(feature_payload) -> dict[str, object]:
        derivatives = feature_payload.derivatives
        return {
            "available": derivatives.available,
            "source": derivatives.source,
            "fallback_used": derivatives.fallback_used,
            "open_interest_change_pct": derivatives.open_interest_change_pct,
            "funding_rate": derivatives.funding_rate,
            "top_trader_long_short_ratio": derivatives.top_trader_long_short_ratio,
            "top_trader_crowding_bias": derivatives.top_trader_crowding_bias,
            "best_bid": derivatives.best_bid,
            "best_ask": derivatives.best_ask,
            "spread_bps": derivatives.spread_bps,
            "spread_stress_score": derivatives.spread_stress_score,
            "taker_buy_sell_imbalance": derivatives.taker_buy_sell_imbalance,
            "perp_basis_bps": derivatives.perp_basis_bps,
            "crowding_bias": derivatives.crowding_bias,
            "taker_flow_alignment": derivatives.taker_flow_alignment,
            "funding_bias": derivatives.funding_bias,
            "oi_expanding_with_price": derivatives.oi_expanding_with_price,
            "oi_falling_on_breakout": derivatives.oi_falling_on_breakout,
            "crowded_long_risk": derivatives.crowded_long_risk,
            "crowded_short_risk": derivatives.crowded_short_risk,
            "top_trader_long_crowded": derivatives.top_trader_long_crowded,
            "top_trader_short_crowded": derivatives.top_trader_short_crowded,
            "spread_headwind": derivatives.spread_headwind,
            "spread_stress": derivatives.spread_stress,
            "breakout_spread_headwind": derivatives.breakout_spread_headwind,
            "entry_veto_reason_codes": list(derivatives.entry_veto_reason_codes),
            "breakout_veto_reason_codes": list(derivatives.breakout_veto_reason_codes),
            "long_discount_magnitude": derivatives.long_discount_magnitude,
            "short_discount_magnitude": derivatives.short_discount_magnitude,
            "long_alignment_score": derivatives.long_alignment_score,
            "short_alignment_score": derivatives.short_alignment_score,
        }

    @staticmethod
    def _lead_lag_summary(feature_payload) -> dict[str, object]:
        lead_lag = feature_payload.lead_lag
        return {
            "available": lead_lag.available,
            "leader_bias": lead_lag.leader_bias,
            "reference_symbols": list(lead_lag.reference_symbols),
            "missing_reference_symbols": list(lead_lag.missing_reference_symbols),
            "bullish_alignment_score": lead_lag.bullish_alignment_score,
            "bearish_alignment_score": lead_lag.bearish_alignment_score,
            "bullish_breakout_confirmed": lead_lag.bullish_breakout_confirmed,
            "bearish_breakout_confirmed": lead_lag.bearish_breakout_confirmed,
            "bullish_breakout_ahead": lead_lag.bullish_breakout_ahead,
            "bearish_breakout_ahead": lead_lag.bearish_breakout_ahead,
            "bullish_pullback_supported": lead_lag.bullish_pullback_supported,
            "bearish_pullback_supported": lead_lag.bearish_pullback_supported,
            "bullish_continuation_supported": lead_lag.bullish_continuation_supported,
            "bearish_continuation_supported": lead_lag.bearish_continuation_supported,
            "strong_reference_confirmation": lead_lag.strong_reference_confirmation,
            "weak_reference_confirmation": lead_lag.weak_reference_confirmation,
        }

    def _build_lead_market_features(
        self,
        *,
        base_timeframe: str,
        upto_index: int | None,
        force_stale: bool,
    ) -> dict[str, FeaturePayload]:
        lead_contexts = build_lead_market_contexts(
            base_timeframe=base_timeframe,
            upto_index=upto_index,
            force_stale=force_stale,
            use_binance=self.settings_row.binance_market_data_enabled,
            binance_testnet_enabled=self.settings_row.binance_testnet_enabled,
            stale_threshold_seconds=self.settings_row.stale_market_seconds,
        )
        lead_features: dict[str, FeaturePayload] = {}
        for lead_symbol, context in lead_contexts.items():
            snapshot = context.get(base_timeframe)
            if snapshot is None:
                continue
            higher_timeframe_context = {
                timeframe: payload
                for timeframe, payload in context.items()
                if timeframe != base_timeframe
            }
            lead_features[lead_symbol] = compute_features(snapshot, higher_timeframe_context)
        return lead_features

    def _candidate_derivatives_alignment_score(self, feature_payload, *, decision: str, priority: bool) -> float:
        if priority:
            return 1.0
        derivatives = feature_payload.derivatives
        if not derivatives.available:
            return 0.55
        breakout_up = feature_payload.breakout.broke_swing_high or feature_payload.breakout.range_breakout_direction == "up"
        breakout_down = feature_payload.breakout.broke_swing_low or feature_payload.breakout.range_breakout_direction == "down"
        if decision == "long":
            score = float(derivatives.long_alignment_score)
            if derivatives.spread_headwind:
                score -= 0.12
            if derivatives.spread_stress:
                score -= 0.1
            if derivatives.top_trader_long_crowded:
                score -= 0.08
            if derivatives.funding_bias == "long_headwind" and derivatives.spread_headwind:
                score -= 0.06
            if breakout_up and derivatives.breakout_spread_headwind and not derivatives.oi_expanding_with_price:
                score -= 0.16
            if breakout_up and derivatives.breakout_veto_reason_codes:
                score -= 0.05
            return _clamp_score(score)
        if decision == "short":
            score = float(derivatives.short_alignment_score)
            if derivatives.spread_headwind:
                score -= 0.12
            if derivatives.spread_stress:
                score -= 0.1
            if derivatives.top_trader_short_crowded:
                score -= 0.08
            if derivatives.funding_bias == "short_headwind" and derivatives.spread_headwind:
                score -= 0.06
            if breakout_down and derivatives.breakout_spread_headwind and not derivatives.oi_expanding_with_price:
                score -= 0.16
            if breakout_down and derivatives.breakout_veto_reason_codes:
                score -= 0.05
            return _clamp_score(score)
        return 0.55

    @staticmethod
    def _candidate_lead_lag_alignment_score(feature_payload, *, decision: str, priority: bool) -> float:
        if priority:
            return 1.0
        lead_lag = feature_payload.lead_lag
        if not lead_lag.available:
            return 0.55
        breakout_up = feature_payload.breakout.broke_swing_high or feature_payload.breakout.range_breakout_direction == "up"
        breakout_down = feature_payload.breakout.broke_swing_low or feature_payload.breakout.range_breakout_direction == "down"
        if decision == "long":
            score = float(lead_lag.bullish_alignment_score)
            if feature_payload.pullback_context.state == "bullish_pullback" and lead_lag.bullish_pullback_supported:
                score += 0.08
            if feature_payload.pullback_context.state == "bullish_continuation" and lead_lag.bullish_continuation_supported:
                score += 0.06
            if breakout_up and lead_lag.bullish_breakout_confirmed:
                score += 0.06
            elif breakout_up and lead_lag.bullish_breakout_ahead:
                score *= 0.72
            return _clamp_score(score)
        if decision == "short":
            score = float(lead_lag.bearish_alignment_score)
            if feature_payload.pullback_context.state == "bearish_pullback" and lead_lag.bearish_pullback_supported:
                score += 0.08
            if feature_payload.pullback_context.state == "bearish_continuation" and lead_lag.bearish_continuation_supported:
                score += 0.06
            if breakout_down and lead_lag.bearish_breakout_confirmed:
                score += 0.06
            elif breakout_down and lead_lag.bearish_breakout_ahead:
                score *= 0.72
            return _clamp_score(score)
        return 0.55

    def _build_selection_candidate(
        self,
        *,
        symbol: str,
        timeframe: str,
        upto_index: int | None,
        force_stale: bool,
        missing_protection_symbols: set[str],
        total_open_positions: int,
        lead_market_features: dict[str, FeaturePayload] | None = None,
    ) -> dict[str, object]:
        market_context = build_market_context(
            symbol=symbol,
            base_timeframe=timeframe,
            upto_index=upto_index,
            force_stale=force_stale,
            use_binance=self.settings_row.binance_market_data_enabled,
            binance_testnet_enabled=self.settings_row.binance_testnet_enabled,
            stale_threshold_seconds=self.settings_row.stale_market_seconds,
        )
        market_snapshot = market_context[timeframe]
        higher_timeframe_context = {key: value for key, value in market_context.items() if key != timeframe}
        feature_payload = compute_features(
            market_snapshot,
            higher_timeframe_context,
            lead_market_features=lead_market_features,
        )
        open_positions = get_open_positions(self.session, symbol)
        priority = bool(open_positions) or symbol in missing_protection_symbols
        long_breakout_allowed = bool(
            (
                feature_payload.breakout.broke_swing_high
                or feature_payload.breakout.range_breakout_direction == "up"
            )
            and feature_payload.regime.trend_alignment == "bullish_aligned"
            and feature_payload.regime.primary_regime != "range"
            and not feature_payload.regime.weak_volume
            and feature_payload.regime.momentum_state == "strengthening"
            and feature_payload.volume_persistence.persistence_ratio >= 1.05
        )
        short_breakout_allowed = bool(
            (
                feature_payload.breakout.broke_swing_low
                or feature_payload.breakout.range_breakout_direction == "down"
            )
            and feature_payload.regime.trend_alignment == "bearish_aligned"
            and feature_payload.regime.primary_regime != "range"
            and not feature_payload.regime.weak_volume
            and feature_payload.regime.momentum_state == "strengthening"
            and feature_payload.volume_persistence.persistence_ratio >= 1.05
        )
        strategy_engine_selection = select_strategy_engine(
            market_snapshot=market_snapshot,
            features=feature_payload,
            open_positions=open_positions,
            risk_context={},
            long_breakout_allowed=long_breakout_allowed,
            short_breakout_allowed=short_breakout_allowed,
        )
        selected_engine_payload = strategy_engine_selection.selected_engine.to_payload()
        selected_engine_name = str(selected_engine_payload.get("engine_name") or "unspecified_engine")

        decision = "hold"
        scenario = "hold"
        explanation_short = "중립 심볼"
        if priority and symbol in missing_protection_symbols:
            decision = "reduce"
            scenario = "protection_restore"
            explanation_short = "보호주문 복구 우선 심볼"
        elif priority:
            decision = "reduce"
            scenario = "reduce"
            explanation_short = "오픈 포지션 관리 우선 심볼"
        elif feature_payload.trend_score >= 0.15:
            decision = "long"
            scenario = "trend_follow"
            explanation_short = "상승 정렬 진입 후보"
        elif feature_payload.trend_score <= -0.15:
            decision = "short"
            scenario = "trend_follow"
            explanation_short = "하락 정렬 진입 후보"
        elif feature_payload.regime.weak_volume:
            decision = "hold"
            scenario = "hold"
            explanation_short = "유동성 약화 관찰 심볼"
        elif feature_payload.momentum_score > 0:
            decision = "long"
            scenario = "pullback_entry"
            explanation_short = "당김목 진입 후보"

        if not priority and strategy_engine_selection.selected_engine.eligible:
            decision = str(strategy_engine_selection.selected_engine.decision_hint)
            scenario = str(strategy_engine_selection.selected_engine.scenario)
            explanation_short = {
                "trend_pullback_engine": "trend_pullback_candidate",
                "trend_continuation_engine": "trend_continuation_candidate",
                "breakout_exception_engine": "breakout_exception_candidate",
                "range_mean_reversion_engine": "range_mean_reversion_scaffold",
                "protection_reduce_engine": "protection_reduce_priority",
            }.get(selected_engine_name, "strategy_engine_candidate")

        entry_price = float(market_snapshot.latest_price)
        atr = max(float(feature_payload.atr or 0.0), entry_price * 0.0025, 1e-6)
        if decision == "long":
            stop_loss = entry_price - atr
            take_profit = entry_price + (atr * 1.8)
        elif decision == "short":
            stop_loss = entry_price + atr
            take_profit = entry_price - (atr * 1.8)
        else:
            stop_loss = None
            take_profit = None
        expected_rr_ratio = 0.0
        if stop_loss is not None and take_profit is not None:
            risk_distance = abs(entry_price - stop_loss)
            reward_distance = abs(take_profit - entry_price)
            if risk_distance > 0:
                expected_rr_ratio = reward_distance / risk_distance

        regime = feature_payload.regime
        if priority:
            regime_fit = 1.0
        elif decision == "long":
            regime_fit = 1.0 if regime.trend_alignment == "bullish_aligned" else 0.45
        elif decision == "short":
            regime_fit = 1.0 if regime.trend_alignment == "bearish_aligned" else 0.45
        else:
            regime_fit = 0.4
        expected_rr = _clamp_score(expected_rr_ratio / 3.0)
        candidate_entry_mode = (
            "manage_only"
            if priority
            else str(selected_engine_payload.get("entry_mode") or "pullback_confirm")
            if decision in {"long", "short"} and strategy_engine_selection.selected_engine.eligible
            else "pullback_confirm"
            if scenario == "pullback_entry"
            else "breakout_confirm"
            if decision in {"long", "short"} and selected_engine_name == "breakout_exception_engine"
            else "none"
        )
        performance_summary = self._recent_signal_performance_summary(
            symbol=symbol,
            timeframe=timeframe,
            scenario=scenario,
            regime=regime.primary_regime,
            trend_alignment=regime.trend_alignment,
            strategy_engine=selected_engine_name,
        )
        holding_profile_context = evaluate_holding_profile(
            decision=decision,
            features=feature_payload,
            selection_context={},
            strategy_engine=selected_engine_name,
        )
        recent_signal_performance = _safe_float(performance_summary.get("score"), default=0.55)
        derivatives_alignment = self._candidate_derivatives_alignment_score(
            feature_payload,
            decision=decision,
            priority=priority,
        )
        lead_lag_alignment = self._candidate_lead_lag_alignment_score(
            feature_payload,
            decision=decision,
            priority=priority,
        )
        derivatives_summary = self._derivatives_summary(feature_payload)
        if decision == "long":
            side_discount_magnitude = float(feature_payload.derivatives.long_discount_magnitude)
        elif decision == "short":
            side_discount_magnitude = float(feature_payload.derivatives.short_discount_magnitude)
        else:
            side_discount_magnitude = 0.0
        side_veto_reason_codes = (
            list(feature_payload.derivatives.breakout_veto_reason_codes)
            if candidate_entry_mode == "breakout_confirm"
            else list(feature_payload.derivatives.entry_veto_reason_codes)
        )
        derivatives_summary["discount_magnitude"] = round(side_discount_magnitude, 6)
        derivatives_summary["veto_reason_codes"] = side_veto_reason_codes
        lead_lag_summary = self._lead_lag_summary(feature_payload)
        slippage_sensitivity = self._slippage_sensitivity_score(symbol)
        confidence_consistency = self._confidence_consistency_score(symbol, decision=decision)
        exposure_impact = self._candidate_exposure_impact_score(
            symbol=symbol,
            priority=priority,
            total_open_positions=total_open_positions,
        )
        base_total = (
            (recent_signal_performance * 0.34)
            + (expected_rr * 0.13)
            + (regime_fit * 0.11)
            + (derivatives_alignment * 0.11)
            + (lead_lag_alignment * 0.09)
            + (slippage_sensitivity * 0.1)
            + (confidence_consistency * 0.07)
            + (exposure_impact * 0.05)
        )

        candidate = TradeDecisionCandidate(
            candidate_id=f"{symbol}:{timeframe}:{scenario}:{selected_engine_name}",
            scenario=scenario,  # type: ignore[arg-type]
            decision=decision,  # type: ignore[arg-type]
            symbol=symbol,
            timeframe=timeframe,
            confidence=round(_clamp_score((feature_payload.momentum_score + 1.0) / 2.0), 6),
            entry_zone_min=entry_price * (0.999 if decision == "long" else 1.001) if decision in {"long", "short"} else None,
            entry_zone_max=entry_price * (1.001 if decision == "long" else 0.999) if decision in {"long", "short"} else None,
            stop_loss=stop_loss,
            take_profit=take_profit,
            max_holding_minutes=max(30, min(int(timeframe.rstrip("mh")) * 8 if timeframe[:-1].isdigit() else 120, 720)),
            risk_pct=min(self.settings_row.max_risk_per_trade, HARD_MAX_RISK_PER_TRADE),
            leverage=min(self.settings_row.max_leverage, get_symbol_leverage_cap(symbol)),
            rationale_codes=[
                f"ENGINE_{selected_engine_name.upper()}",
                f"REGIME_{regime.primary_regime.upper()}",
                f"TREND_{regime.trend_alignment.upper()}",
                (
                    "EXPECTANCY_UNDERPERFORMING"
                    if bool(performance_summary.get("underperforming"))
                    else "EXPECTANCY_ALIGNED"
                    if recent_signal_performance >= 0.62
                    else "EXPECTANCY_NEUTRAL"
                ),
                "DERIVATIVES_ALIGNED" if derivatives_alignment >= 0.65 else "DERIVATIVES_NEUTRAL",
                "LEAD_MARKETS_ALIGNED" if lead_lag_alignment >= 0.68 else "LEAD_MARKETS_NEUTRAL",
            ]
            + (["DERIVATIVES_HEADWIND"] if derivatives_alignment <= 0.35 else [])
            + (["SPREAD_HEADWIND"] if feature_payload.derivatives.spread_headwind else [])
            + (
                ["SPREAD_STRESS"]
                if feature_payload.derivatives.spread_stress
                else []
            )
            + (
                ["TOP_TRADER_LONG_CROWDED"]
                if decision == "long" and feature_payload.derivatives.top_trader_long_crowded
                else ["TOP_TRADER_SHORT_CROWDED"]
                if decision == "short" and feature_payload.derivatives.top_trader_short_crowded
                else []
            )
            + (
                ["BREAKOUT_OI_SPREAD_FILTER"]
                if (
                    feature_payload.derivatives.breakout_spread_headwind
                    and not feature_payload.derivatives.oi_expanding_with_price
                )
                else []
            )
            + (
                ["ADVERSE_SIGNED_SLIPPAGE"]
                if _safe_float(performance_summary.get("avg_signed_slippage_bps")) >= SETUP_CLUSTER_SIGNED_SLIPPAGE_BPS_THRESHOLD
                else []
            )
            + side_veto_reason_codes,
            holding_profile=str(holding_profile_context["holding_profile"]),
            holding_profile_reason=str(holding_profile_context["holding_profile_reason"]),
            strategy_engine=selected_engine_name,
            strategy_engine_context=strategy_engine_selection.to_payload(),
            lead_lag_summary=lead_lag_summary,
            derivatives_summary=derivatives_summary,
            explanation_short=explanation_short,
            explanation_detailed=(
                f"{symbol} {timeframe} candidate selected from market snapshot context. "
                f"strategy_engine={selected_engine_name}, "
                f"priority={priority}, regime_fit={regime_fit:.3f}, expected_rr={expected_rr_ratio:.3f}, "
                f"expectancy={_safe_float(performance_summary.get('expectancy')):.3f}, "
                f"net_pnl_after_fees={_safe_float(performance_summary.get('net_pnl_after_fees')):.3f}, "
                f"avg_signed_slippage_bps={_safe_float(performance_summary.get('avg_signed_slippage_bps')):.3f}, "
                f"derivatives_alignment={derivatives_alignment:.3f}, lead_lag_alignment={lead_lag_alignment:.3f}, "
                f"derivatives_discount={side_discount_magnitude:.3f}."
            ),
        )
        score = TradeDecisionCandidateScore(
            regime_fit=round(regime_fit, 6),
            expected_rr=round(expected_rr, 6),
            recent_signal_performance=round(recent_signal_performance, 6),
            derivatives_alignment=round(derivatives_alignment, 6),
            lead_lag_alignment=round(lead_lag_alignment, 6),
            slippage_sensitivity=round(slippage_sensitivity, 6),
            exposure_impact=round(exposure_impact, 6),
            confidence_consistency=round(confidence_consistency, 6),
            correlation_penalty=0.0,
            total_score=round(base_total, 6),
        )
        return {
            "symbol": symbol,
            "priority": priority,
            "candidate": candidate,
            "score": score,
            "feature_payload": feature_payload,
            "regime_summary": {
                "primary_regime": regime.primary_regime,
                "trend_alignment": regime.trend_alignment,
                "weak_volume": regime.weak_volume,
                "momentum_weakening": regime.momentum_weakening,
            },
            "performance_summary": performance_summary,
            "strategy_engine": selected_engine_name,
            "strategy_engine_context": strategy_engine_selection.to_payload(),
            "holding_profile": holding_profile_context["holding_profile"],
            "holding_profile_reason": holding_profile_context["holding_profile_reason"],
            "holding_profile_context": holding_profile_context,
            "entry_mode": candidate_entry_mode,
            "scenario_signature": f"{decision}:{scenario}:{regime.primary_regime}:{regime.trend_alignment}",
            "returns": _rolling_returns_from_snapshot(market_snapshot),
            "market_snapshot": market_snapshot,
        }

    @staticmethod
    def _selection_breadth_summary(candidate_rows: list[dict[str, object]]) -> dict[str, object]:
        breadth_inputs: list[object] = []
        decisions: dict[str, str] = {}
        for item in candidate_rows:
            symbol = str(item.get("symbol") or "").upper()
            candidate = item.get("candidate")
            decisions[symbol] = str(getattr(candidate, "decision", "") or "")
            feature_payload = item.get("feature_payload")
            if feature_payload is not None:
                breadth_inputs.append(feature_payload)
                continue
            regime_summary = item.get("regime_summary") if isinstance(item.get("regime_summary"), dict) else {}
            breadth_inputs.append({"symbol": symbol, **regime_summary})
        return summarize_universe_breadth(breadth_inputs, decisions=decisions)

    @staticmethod
    def _selection_capacity_plan(
        *,
        breadth_summary: dict[str, object],
        priority_count: int,
        candidate_count: int,
        drawdown_state: dict[str, object] | None = None,
    ) -> tuple[int, float, str, str | None]:
        breadth_regime = str(breadth_summary.get("breadth_regime") or "mixed")
        if breadth_regime == "weak_breadth":
            non_priority_capacity = 1
            entry_score_threshold = 0.48
            capacity_reason = "breadth_weak_reduce_capacity"
        elif breadth_regime == "transition_fragile":
            non_priority_capacity = 1
            entry_score_threshold = 0.45
            capacity_reason = "transition_fragile_reduce_capacity"
        elif breadth_regime == "trend_expansion":
            non_priority_capacity = 3
            entry_score_threshold = 0.36
            capacity_reason = "trend_expansion_allow_rotation"
        else:
            non_priority_capacity = 2
            entry_score_threshold = 0.4
            capacity_reason = "mixed_breadth_moderate_capacity"
        drawdown_capacity_reason: str | None = None
        drawdown_policy = (
            dict(drawdown_state.get("policy_adjustments") or {})
            if isinstance(drawdown_state, dict)
            else {}
        )
        drawdown_state_code = (
            str(drawdown_state.get("current_drawdown_state") or "normal")
            if isinstance(drawdown_state, dict)
            else "normal"
        )
        max_non_priority_selected = int(drawdown_policy.get("max_non_priority_selected") or non_priority_capacity)
        entry_score_threshold_uplift = _safe_float(drawdown_policy.get("entry_score_threshold_uplift"))
        if drawdown_state_code != "normal":
            adjusted_non_priority_capacity = min(non_priority_capacity, max_non_priority_selected)
            if adjusted_non_priority_capacity < non_priority_capacity:
                non_priority_capacity = adjusted_non_priority_capacity
                drawdown_capacity_reason = f"{drawdown_state_code}_reduce_capacity"
            if entry_score_threshold_uplift > 0:
                entry_score_threshold = round(min(entry_score_threshold + entry_score_threshold_uplift, 0.95), 6)
        max_selected = min(priority_count + non_priority_capacity, candidate_count)
        return max_selected, entry_score_threshold, capacity_reason, drawdown_capacity_reason

    @staticmethod
    def _candidate_overlap_reason(
        *,
        item: dict[str, object],
        selected_rows: list[dict[str, object]],
        max_abs_correlation: float,
        breadth_regime: str,
    ) -> str | None:
        candidate = item.get("candidate")
        decision = str(getattr(candidate, "decision", "") or "")
        scenario_signature = str(item.get("scenario_signature") or "")
        regime_summary = item.get("regime_summary") if isinstance(item.get("regime_summary"), dict) else {}
        trend_alignment = str(regime_summary.get("trend_alignment") or "unknown")
        scenario_duplicate = any(str(selected.get("scenario_signature") or "") == scenario_signature for selected in selected_rows)
        directional_duplicate = any(
            str(getattr(selected.get("candidate"), "decision", "") or "") == decision
            and str((selected.get("regime_summary") or {}).get("trend_alignment") or "unknown") == trend_alignment
            for selected in selected_rows
        )
        if max_abs_correlation >= 0.92 and selected_rows:
            return "correlation_limit"
        if scenario_duplicate and max_abs_correlation >= 0.68:
            return "duplicate_scenario_exposure"
        if breadth_regime in {"weak_breadth", "transition_fragile"} and directional_duplicate and max_abs_correlation >= 0.55:
            return "duplicate_directional_exposure"
        return None

    @staticmethod
    def _candidate_breadth_adjustment(
        *,
        item: dict[str, object],
        breadth_summary: dict[str, object],
    ) -> dict[str, object]:
        candidate = item.get("candidate")
        decision = str(getattr(candidate, "decision", "") or "")
        if bool(item.get("priority")) or decision not in {"long", "short"}:
            return {
                "score_multiplier": 1.0,
                "score_adjustment": 0.0,
                "hold_bias": 1.0,
                "reasons": [],
            }
        breadth_regime = str(breadth_summary.get("breadth_regime") or "mixed")
        directional_bias = str(breadth_summary.get("directional_bias") or "balanced")
        base_multiplier = float(breadth_summary.get("entry_score_multiplier") or 1.0)
        hold_bias = float(breadth_summary.get("hold_bias_multiplier") or 1.0)
        regime_summary = item.get("regime_summary") if isinstance(item.get("regime_summary"), dict) else {}
        primary_regime = str(regime_summary.get("primary_regime") or "unknown")
        trend_alignment = str(regime_summary.get("trend_alignment") or "unknown")
        weak_volume = bool(regime_summary.get("weak_volume", False))
        momentum_weakening = bool(regime_summary.get("momentum_weakening", False))
        aligned_with_bias = (
            (decision == "long" and directional_bias == "bullish")
            or (decision == "short" and directional_bias == "bearish")
        )

        score_multiplier = base_multiplier
        score_adjustment = 0.0
        reasons: list[str] = []
        if breadth_regime == "weak_breadth":
            if weak_volume or primary_regime in {"range", "transition"} or momentum_weakening:
                score_multiplier *= 0.86
                hold_bias = max(hold_bias, 1.25)
                reasons.append("weak_breadth_structure_penalty")
            if directional_bias != "balanced" and not aligned_with_bias:
                score_multiplier *= 0.88
                reasons.append("breadth_direction_mismatch")
        elif breadth_regime == "transition_fragile":
            if primary_regime in {"range", "transition"} or momentum_weakening:
                score_multiplier *= 0.92
                hold_bias = max(hold_bias, 1.12)
                reasons.append("transition_fragile_penalty")
        elif breadth_regime == "trend_expansion":
            if aligned_with_bias and not weak_volume and trend_alignment in {"bullish_aligned", "bearish_aligned"}:
                score_adjustment += 0.04
                hold_bias = min(hold_bias, 0.95)
                reasons.append("breadth_trend_alignment_boost")
            elif directional_bias != "balanced" and not aligned_with_bias:
                score_multiplier *= 0.95
                reasons.append("breadth_secondary_rotation_discount")

        return {
            "score_multiplier": round(score_multiplier, 6),
            "score_adjustment": round(score_adjustment, 6),
            "hold_bias": round(hold_bias, 6),
            "reasons": reasons,
        }

    @staticmethod
    def _breadth_alignment_score(
        *,
        breadth_summary: dict[str, object],
        breadth_adjustment: dict[str, object],
        decision: str,
    ) -> float:
        breadth_regime = str(breadth_summary.get("breadth_regime") or "mixed")
        directional_bias = str(breadth_summary.get("directional_bias") or "neutral")
        base = {
            "weak_breadth": 0.34,
            "transition_fragile": 0.42,
            "mixed": 0.56,
            "trend_expansion": 0.74,
        }.get(breadth_regime, 0.52)
        target_bias = "bullish" if decision == "long" else "bearish"
        if directional_bias == target_bias:
            base += 0.05
        elif directional_bias not in {"neutral", "mixed", "unknown", ""}:
            base -= 0.08
        base += float(breadth_adjustment.get("score_adjustment", 0.0)) * 2.5
        base *= float(breadth_adjustment.get("score_multiplier", 1.0))
        base -= max(float(breadth_adjustment.get("hold_bias", 1.0)) - 1.0, 0.0) * 0.18
        return _clamp_score(base)

    @staticmethod
    def _agreement_alignment_score(
        *,
        confidence_consistency: float,
        recent_signal_performance: float,
    ) -> tuple[float, str]:
        score = _clamp_score((confidence_consistency * 0.62) + (recent_signal_performance * 0.38))
        if score >= 0.7:
            return score, "full_agreement_likely"
        if score >= 0.56:
            return score, "partial_agreement_likely"
        return score, "disagreement_risk"

    @staticmethod
    def _execution_quality_score(
        *,
        slippage_sensitivity: float,
        performance_summary: dict[str, object],
    ) -> float:
        avg_signed_slippage_bps = max(_safe_float(performance_summary.get("avg_signed_slippage_bps")), 0.0)
        slippage_quality = _clamp_score(0.9 - (min(avg_signed_slippage_bps, 18.0) / 18.0 * 0.55))
        score = (slippage_sensitivity * 0.62) + (slippage_quality * 0.38)
        if bool(performance_summary.get("underperforming")) and avg_signed_slippage_bps >= 10.0:
            score -= 0.08
        return _clamp_score(score)

    def _candidate_meta_gate_probability(
        self,
        *,
        candidate: TradeDecisionCandidate,
        entry_mode: str,
        feature_payload: FeaturePayload | None,
        score_payload: TradeDecisionCandidateScore,
        performance_summary: dict[str, object],
        breadth_summary: dict[str, object],
    ) -> float:
        if feature_payload is None:
            fallback = (
                float(score_payload.total_score) * 0.42
                + _safe_float(performance_summary.get("score"), default=float(score_payload.total_score)) * 0.34
                + float(score_payload.lead_lag_alignment) * 0.12
                + float(score_payload.derivatives_alignment) * 0.12
            )
            return _clamp_score(fallback)

        candidate_decision = TradeDecision(
            decision=candidate.decision,
            confidence=candidate.confidence,
            symbol=candidate.symbol,
            timeframe=candidate.timeframe,
            entry_zone_min=candidate.entry_zone_min,
            entry_zone_max=candidate.entry_zone_max,
            entry_mode=entry_mode if entry_mode in {"breakout_confirm", "pullback_confirm", "immediate", "none"} else "none",
            invalidation_price=None,
            max_chase_bps=None,
            idea_ttl_minutes=None,
            stop_loss=candidate.stop_loss,
            take_profit=candidate.take_profit,
            max_holding_minutes=candidate.max_holding_minutes,
            risk_pct=candidate.risk_pct,
            leverage=candidate.leverage,
            rationale_codes=list(candidate.rationale_codes),
            explanation_short=candidate.explanation_short,
            explanation_detailed=candidate.explanation_detailed,
        )
        meta_gate_result = evaluate_meta_gate(
            candidate_decision,
            feature_payload=feature_payload,
            selection_context={
                "score": score_payload.model_dump(mode="json"),
                "performance_summary": performance_summary,
                "universe_breadth": breadth_summary,
            },
            decision_metadata={},
        )
        return float(meta_gate_result.expected_hit_probability)

    @staticmethod
    def _slot_conviction_score(
        *,
        expectancy_score: float,
        meta_gate_probability: float,
        agreement_alignment: float,
        breadth_alignment: float,
        lead_lag_alignment: float,
        execution_quality: float,
        correlation_penalty: float,
    ) -> float:
        score = (
            (expectancy_score * 0.32)
            + (meta_gate_probability * 0.24)
            + (agreement_alignment * 0.12)
            + (breadth_alignment * 0.12)
            + (lead_lag_alignment * 0.1)
            + (execution_quality * 0.1)
        ) - (correlation_penalty * 0.18)
        return _clamp_score(score)

    @staticmethod
    def _available_portfolio_slots(non_priority_capacity: int) -> list[str]:
        if non_priority_capacity <= 0:
            return []
        return list(PORTFOLIO_SLOT_ORDER[: min(non_priority_capacity, len(PORTFOLIO_SLOT_ORDER))])

    @staticmethod
    def _assign_portfolio_slot(
        *,
        slot_conviction_score: float,
        used_slots: set[str],
        available_slots: list[str],
    ) -> dict[str, object]:
        if not available_slots:
            return {
                "assigned_slot": None,
                "slot_label": "unassigned",
                "rejected_reason": "capacity_reached",
                "slot_reason": "slot_capacity_reached",
            }
        if (
            slot_conviction_score >= PORTFOLIO_SLOT_HIGH_CONVICTION_THRESHOLD
            and "slot_1" in available_slots
            and "slot_1" not in used_slots
        ):
            return {
                "assigned_slot": "slot_1",
                "slot_label": PORTFOLIO_SLOT_LABELS["slot_1"],
                "rejected_reason": None,
                "slot_reason": "high_conviction_slot",
            }
        if slot_conviction_score < PORTFOLIO_SLOT_MEDIUM_CONVICTION_THRESHOLD:
            return {
                "assigned_slot": None,
                "slot_label": "unassigned",
                "rejected_reason": "low_conviction_slot_excluded",
                "slot_reason": "slot_conviction_below_threshold",
            }
        for slot_name in available_slots:
            if slot_name == "slot_1":
                continue
            if slot_name not in used_slots:
                return {
                    "assigned_slot": slot_name,
                    "slot_label": PORTFOLIO_SLOT_LABELS[slot_name],
                    "rejected_reason": None,
                    "slot_reason": "medium_conviction_slot",
                }
        return {
            "assigned_slot": None,
            "slot_label": "unassigned",
            "rejected_reason": "capacity_reached",
            "slot_reason": "slot_capacity_reached",
        }

    @staticmethod
    def _slot_policy_profile(*, assigned_slot: str | None, portfolio_weight: float) -> dict[str, object]:
        if assigned_slot not in PORTFOLIO_SLOT_POLICY_BASE:
            return {
                "assigned_slot": assigned_slot,
                "slot_label": "unassigned",
                "candidate_weight": 0.0,
                "risk_pct_multiplier": 1.0,
                "leverage_multiplier": 1.0,
                "notional_multiplier": 1.0,
                "applies_soft_cap": False,
            }
        base_profile = PORTFOLIO_SLOT_POLICY_BASE[assigned_slot]
        weight_factor = _clamp_score(0.6 + portfolio_weight, lower=0.6, upper=1.0)
        return {
            "assigned_slot": assigned_slot,
            "slot_label": PORTFOLIO_SLOT_LABELS[assigned_slot],
            "candidate_weight": round(portfolio_weight, 6),
            "risk_pct_multiplier": round(float(base_profile["risk_pct_multiplier"]) * weight_factor, 6),
            "leverage_multiplier": round(float(base_profile["leverage_multiplier"]) * weight_factor, 6),
            "notional_multiplier": round(float(base_profile["notional_multiplier"]) * weight_factor, 6),
            "applies_soft_cap": True,
        }

    @staticmethod
    def _portfolio_weight_map(selected_rows: list[dict[str, object]]) -> dict[str, float]:
        weighted_rows = [
            row
            for row in selected_rows
            if not bool(row.get("priority"))
            and str(getattr(row.get("candidate"), "decision", "") or "") in {"long", "short"}
            and isinstance(row.get("slot_allocation"), dict)
            and str((row.get("slot_allocation") or {}).get("assigned_slot") or "") in PORTFOLIO_SLOT_BASE_WEIGHTS
        ]
        if not weighted_rows:
            return {}
        weighted_scores = {
            str(row.get("symbol")): (
                PORTFOLIO_SLOT_BASE_WEIGHTS[
                    str((row.get("slot_allocation") or {}).get("assigned_slot") or "slot_3")
                ]
                * max(_safe_float((row.get("slot_allocation") or {}).get("slot_conviction_score")), 0.0) ** 2
                * max(_safe_float((row.get("slot_allocation") or {}).get("meta_gate_probability"), default=0.25), 0.25)
            )
            for row in weighted_rows
        }
        denominator = sum(weighted_scores.values())
        if denominator <= 0:
            equal_weight = round(1.0 / len(weighted_rows), 6)
            return {str(row.get("symbol")): equal_weight for row in weighted_rows}
        return {
            symbol: round(score / denominator, 6)
            for symbol, score in weighted_scores.items()
        }

    def _rank_candidate_symbols(
        self,
        *,
        decision_symbols: list[str],
        timeframe: str | None,
        upto_index: int | None,
        force_stale: bool,
    ) -> dict[str, object]:
        generated_at = utcnow_naive()
        drawdown_state = self._sync_drawdown_state(now=generated_at)
        if not self.settings_row.ai_enabled:
            set_candidate_selection_detail(
                self.settings_row,
                generated_at=generated_at,
                mode="disabled_ai_off",
                max_selected=len(decision_symbols),
                current_drawdown_state=str(drawdown_state.get("current_drawdown_state") or "normal"),
                drawdown_entered_at=_coerce_datetime(drawdown_state.get("entered_at")),
                drawdown_transition_reason=str(drawdown_state.get("transition_reason") or "") or None,
                drawdown_policy_adjustments=dict(drawdown_state.get("policy_adjustments") or {}),
                selected_symbols=decision_symbols,
                skipped_symbols=[],
                rankings=[],
            )
            self.session.add(self.settings_row)
            self.session.flush()
            return {
                "mode": "disabled_ai_off",
                "drawdown_state": drawdown_state,
                "selected_symbols": decision_symbols,
                "skipped_symbols": [],
                "rankings": [],
            }

        runtime_state = summarize_runtime_state(self.settings_row)
        missing_protection_symbols = {
            str(item).upper()
            for item in runtime_state.get("missing_protection_symbols", [])
            if item
        }
        total_open_positions = len(get_open_positions(self.session))
        lead_market_features = self._build_lead_market_features(
            base_timeframe=timeframe or self.settings_row.default_timeframe,
            upto_index=upto_index,
            force_stale=force_stale,
        )
        candidate_rows: list[dict[str, object]] = []
        for symbol in decision_symbols:
            effective_timeframe = timeframe or self._effective_symbol_settings(symbol).timeframe
            try:
                candidate_rows.append(
                    self._build_selection_candidate(
                        symbol=symbol,
                        timeframe=effective_timeframe,
                        upto_index=upto_index,
                        force_stale=force_stale,
                        missing_protection_symbols=missing_protection_symbols,
                        total_open_positions=total_open_positions,
                        lead_market_features=lead_market_features,
                    )
                )
            except Exception as exc:
                fallback_candidate = TradeDecisionCandidate(
                    candidate_id=f"{symbol}:{effective_timeframe}:fallback",
                    scenario="hold",
                    decision="hold",
                    symbol=symbol,
                    timeframe=effective_timeframe,
                    confidence=0.0,
                    entry_zone_min=None,
                    entry_zone_max=None,
                    stop_loss=None,
                    take_profit=None,
                    max_holding_minutes=60,
                    risk_pct=min(self.settings_row.max_risk_per_trade, HARD_MAX_RISK_PER_TRADE),
                    leverage=1.0,
                    rationale_codes=["CANDIDATE_SELECTION_FALLBACK"],
                    explanation_short="후보 선별 fallback",
                    explanation_detailed=f"Candidate selection fell back because market context collection failed: {exc}",
                )
                candidate_rows.append(
                    {
                        "symbol": symbol,
                        "priority": False,
                        "candidate": fallback_candidate,
                        "score": TradeDecisionCandidateScore(total_score=0.15),
                        "regime_summary": {
                            "primary_regime": "unknown",
                            "trend_alignment": "unknown",
                            "weak_volume": False,
                            "momentum_weakening": False,
                        },
                        "performance_summary": {
                            "score": 0.35,
                            "sample_size": 0,
                            "hit_rate": 0.0,
                            "expectancy": 0.0,
                            "net_pnl_after_fees": 0.0,
                            "avg_signed_slippage_bps": 0.0,
                            "loss_streak": 0,
                            "underperforming": False,
                            "components": {},
                        },
                        "scenario_signature": "hold:hold:unknown:unknown",
                        "returns": [],
                        "market_snapshot": None,
                    }
                )

        candidate_rows.sort(
            key=lambda item: (
                bool(item.get("priority")),
                float(getattr(item.get("score"), "total_score", 0.0)),
            ),
            reverse=True,
        )

        breadth_summary = self._selection_breadth_summary(candidate_rows)
        priority_count = len([item for item in candidate_rows if bool(item.get("priority"))])
        max_selected, entry_score_threshold, capacity_reason, drawdown_capacity_reason = self._selection_capacity_plan(
            breadth_summary=breadth_summary,
            priority_count=priority_count,
            candidate_count=len(candidate_rows),
            drawdown_state=drawdown_state,
        )
        non_priority_capacity = max(max_selected - priority_count, 0)
        available_slots = self._available_portfolio_slots(non_priority_capacity)
        breadth_regime = str(breadth_summary.get("breadth_regime") or "mixed")
        selected_symbols: list[str] = []
        selected_rows: list[dict[str, object]] = []
        ranking_payloads: list[dict[str, object]] = []
        skipped_symbols: list[str] = []
        used_slots: set[str] = set()

        for item in candidate_rows:
            candidate = item["candidate"]
            score = item["score"]
            symbol = str(item["symbol"])
            returns = item["returns"]
            priority = bool(item.get("priority"))
            performance_summary = _as_dict(item.get("performance_summary"))
            breadth_adjustment = self._candidate_breadth_adjustment(
                item=item,
                breadth_summary=breadth_summary,
            )
            adjusted_total = (
                float(score.total_score) * float(breadth_adjustment.get("score_multiplier", 1.0))
                + float(breadth_adjustment.get("score_adjustment", 0.0))
            )
            max_abs_correlation = 0.0
            for selected in selected_rows:
                correlation = abs(_pearson_correlation(returns, selected["returns"])) if returns and selected["returns"] else 0.0
                max_abs_correlation = max(max_abs_correlation, correlation)
            correlation_penalty = round(max(0.0, max_abs_correlation - 0.55) * 0.9, 6)
            selected_flag = False
            selection_reason = "capacity_reached"
            rejected_reason: str | None = None
            performance_score = _safe_float(performance_summary.get("score"), default=float(score.total_score))
            performance_sample_size = int(performance_summary.get("sample_size", 0) or 0)
            avg_signed_slippage_bps = _safe_float(performance_summary.get("avg_signed_slippage_bps"))
            underperforming_expectancy = bool(performance_summary.get("underperforming"))
            duplicate_exposure_reason = self._candidate_overlap_reason(
                item=item,
                selected_rows=selected_rows,
                max_abs_correlation=max_abs_correlation,
                breadth_regime=breadth_regime,
            )
            breadth_alignment = self._breadth_alignment_score(
                breadth_summary=breadth_summary,
                breadth_adjustment=breadth_adjustment,
                decision=str(candidate.decision),
            )
            agreement_alignment, agreement_level_hint = self._agreement_alignment_score(
                confidence_consistency=float(score.confidence_consistency),
                recent_signal_performance=float(score.recent_signal_performance),
            )
            execution_quality = self._execution_quality_score(
                slippage_sensitivity=float(score.slippage_sensitivity),
                performance_summary=performance_summary,
            )
            slot_score_payload = score.model_copy(update={"total_score": round(max(adjusted_total, 0.0), 6)})
            meta_gate_probability = self._candidate_meta_gate_probability(
                candidate=candidate,
                entry_mode=str(item.get("entry_mode") or "none"),
                feature_payload=item.get("feature_payload") if isinstance(item.get("feature_payload"), FeaturePayload) else None,
                score_payload=slot_score_payload,
                performance_summary=performance_summary,
                breadth_summary=breadth_summary,
            )
            slot_conviction_score = self._slot_conviction_score(
                expectancy_score=float(score.recent_signal_performance),
                meta_gate_probability=meta_gate_probability,
                agreement_alignment=agreement_alignment,
                breadth_alignment=breadth_alignment,
                lead_lag_alignment=float(score.lead_lag_alignment),
                execution_quality=execution_quality,
                correlation_penalty=correlation_penalty,
            )
            score.meta_gate_probability = round(meta_gate_probability, 6)
            score.agreement_alignment = round(agreement_alignment, 6)
            score.execution_quality = round(execution_quality, 6)
            score.slot_conviction = round(slot_conviction_score, 6)
            if priority:
                selected_flag = True
                selection_reason = "priority_position_or_protection"
            elif str(candidate.decision) not in {"long", "short"}:
                rejected_reason = "low_edge_hold_candidate"
            elif underperforming_expectancy:
                rejected_reason = "underperforming_expectancy_bucket"
            elif performance_sample_size >= SETUP_CLUSTER_MIN_SAMPLE_SIZE and performance_score < 0.42:
                rejected_reason = "expectancy_below_threshold"
            elif (
                performance_sample_size >= SETUP_CLUSTER_MIN_SAMPLE_SIZE
                and avg_signed_slippage_bps >= SETUP_CLUSTER_SIGNED_SLIPPAGE_BPS_THRESHOLD
                and performance_score < 0.5
            ):
                rejected_reason = "adverse_signed_slippage"
            elif duplicate_exposure_reason is not None:
                rejected_reason = duplicate_exposure_reason
            elif len(selected_rows) >= max_selected:
                rejected_reason = "capacity_reached"
            else:
                adjusted_total = adjusted_total - correlation_penalty
                if (
                    float(breadth_adjustment.get("hold_bias", 1.0)) >= 1.15
                    and adjusted_total < (entry_score_threshold + 0.03)
                ):
                    rejected_reason = "breadth_hold_bias"
                elif adjusted_total >= entry_score_threshold:
                    slot_allocation = self._assign_portfolio_slot(
                        slot_conviction_score=slot_conviction_score,
                        used_slots=used_slots,
                        available_slots=available_slots,
                    )
                    assigned_slot = str(slot_allocation.get("assigned_slot") or "")
                    if assigned_slot:
                        selected_flag = True
                        selection_reason = "ranked_portfolio_focus"
                        used_slots.add(assigned_slot)
                        item["slot_allocation"] = {
                            **slot_allocation,
                            "slot_conviction_score": round(slot_conviction_score, 6),
                            "meta_gate_probability": round(meta_gate_probability, 6),
                            "agreement_alignment_score": round(agreement_alignment, 6),
                            "agreement_level_hint": agreement_level_hint,
                            "execution_quality_score": round(execution_quality, 6),
                            "breadth_alignment_score": round(breadth_alignment, 6),
                        }
                    else:
                        rejected_reason = str(slot_allocation.get("rejected_reason") or "capacity_reached")
                else:
                    rejected_reason = "score_below_threshold"
            score.correlation_penalty = correlation_penalty
            score.total_score = round(adjusted_total, 6)
            slot_allocation_payload = (
                dict(item.get("slot_allocation"))
                if isinstance(item.get("slot_allocation"), dict)
                else {
                    "assigned_slot": "priority_management" if priority and selected_flag else None,
                    "slot_label": "priority_management" if priority and selected_flag else "unassigned",
                    "slot_reason": "priority_position_or_protection" if priority and selected_flag else None,
                    "slot_conviction_score": round(slot_conviction_score, 6),
                    "meta_gate_probability": round(meta_gate_probability, 6),
                    "agreement_alignment_score": round(agreement_alignment, 6),
                    "agreement_level_hint": agreement_level_hint,
                    "execution_quality_score": round(execution_quality, 6),
                    "breadth_alignment_score": round(breadth_alignment, 6),
                }
            )
            ranking_payload = {
                "symbol": symbol,
                "priority": priority,
                "selected": selected_flag,
                "selected_reason": selection_reason if selected_flag else None,
                "selection_reason": selection_reason if selected_flag else (rejected_reason or selection_reason),
                "rejected_reason": rejected_reason if not selected_flag else None,
                "max_abs_correlation": round(max_abs_correlation, 6),
                "breadth_regime": breadth_regime,
                "capacity_reason": capacity_reason,
                "drawdown_capacity_reason": drawdown_capacity_reason,
                "current_drawdown_state": str(drawdown_state.get("current_drawdown_state") or "normal"),
                "entry_score_threshold": round(entry_score_threshold, 6),
                "breadth_score_multiplier": breadth_adjustment.get("score_multiplier"),
                "breadth_score_adjustment": breadth_adjustment.get("score_adjustment"),
                "breadth_hold_bias": breadth_adjustment.get("hold_bias"),
                "breadth_adjustment_reasons": breadth_adjustment.get("reasons"),
                "assigned_slot": slot_allocation_payload.get("assigned_slot"),
                "slot_label": slot_allocation_payload.get("slot_label"),
                "slot_reason": slot_allocation_payload.get("slot_reason"),
                "slot_conviction_score": slot_allocation_payload.get("slot_conviction_score"),
                "meta_gate_probability": slot_allocation_payload.get("meta_gate_probability"),
                "agreement_alignment_score": slot_allocation_payload.get("agreement_alignment_score"),
                "agreement_level_hint": slot_allocation_payload.get("agreement_level_hint"),
                "execution_quality_score": slot_allocation_payload.get("execution_quality_score"),
                "breadth_alignment_score": slot_allocation_payload.get("breadth_alignment_score"),
                "performance_summary": performance_summary,
                "entry_mode": item.get("entry_mode"),
                "strategy_engine": item.get("strategy_engine"),
                "strategy_engine_context": item.get("strategy_engine_context"),
                "holding_profile": item.get("holding_profile"),
                "holding_profile_reason": item.get("holding_profile_reason"),
                "holding_profile_context": item.get("holding_profile_context"),
                "candidate": candidate.model_dump(mode="json"),
                "score": score.model_dump(mode="json"),
            }
            ranking_payloads.append(ranking_payload)
            if selected_flag:
                selected_symbols.append(symbol)
                selected_rows.append(item)
            else:
                skipped_symbols.append(symbol)
                self._record_selection_skip_event(
                    symbol=symbol,
                    timeframe=str(getattr(candidate, "timeframe", "") or ""),
                    item=item,
                    ranking_payload=ranking_payload,
                )

        portfolio_weights = self._portfolio_weight_map(selected_rows)
        slot_assignments: dict[str, dict[str, object]] = {}
        allocator_payload = {
            "allocator_mode": "slot_weighted_rotation",
            "slot_mode": "conviction_slots",
            "slot_plan": {
                "available_slots": available_slots,
                "high_conviction_threshold": PORTFOLIO_SLOT_HIGH_CONVICTION_THRESHOLD,
                "medium_conviction_threshold": PORTFOLIO_SLOT_MEDIUM_CONVICTION_THRESHOLD,
                "low_conviction_action": "exclude",
            },
            "selected_entry_symbols": [symbol for symbol, weight in portfolio_weights.items() if weight > 0.0],
            "weights": portfolio_weights,
        }
        for ranking_payload in ranking_payloads:
            symbol = str(ranking_payload.get("symbol") or "")
            priority = bool(ranking_payload.get("priority"))
            portfolio_weight = portfolio_weights.get(symbol, 0.0)
            ranking_payload["portfolio_weight"] = portfolio_weight
            ranking_payload["candidate_weight"] = portfolio_weight
            slot_policy = self._slot_policy_profile(
                assigned_slot=str(ranking_payload.get("assigned_slot") or "") or None,
                portfolio_weight=portfolio_weight,
            )
            ranking_payload["slot_risk_pct_multiplier"] = slot_policy.get("risk_pct_multiplier")
            ranking_payload["slot_leverage_multiplier"] = slot_policy.get("leverage_multiplier")
            ranking_payload["slot_notional_multiplier"] = slot_policy.get("notional_multiplier")
            ranking_payload["slot_applies_soft_cap"] = slot_policy.get("applies_soft_cap")
            ranking_payload["weight_reason"] = (
                "management_priority_unweighted"
                if priority and ranking_payload.get("selected")
                else "slot_weighted_rotation"
                if ranking_payload.get("selected")
                else "not_selected"
            )
            assigned_slot = str(ranking_payload.get("assigned_slot") or "")
            if assigned_slot in PORTFOLIO_SLOT_LABELS and ranking_payload.get("selected"):
                slot_assignments[assigned_slot] = {
                    "label": PORTFOLIO_SLOT_LABELS[assigned_slot],
                    "symbol": symbol,
                    "candidate_weight": portfolio_weight,
                    "slot_conviction_score": ranking_payload.get("slot_conviction_score"),
                    "meta_gate_probability": ranking_payload.get("meta_gate_probability"),
                }
        allocator_payload["slot_assignments"] = slot_assignments

        set_candidate_selection_detail(
            self.settings_row,
            generated_at=generated_at,
            mode="portfolio_rotation_top_n",
            max_selected=max_selected,
            breadth_regime=breadth_regime,
            breadth_summary=breadth_summary,
            capacity_reason=capacity_reason,
            entry_score_threshold=entry_score_threshold,
            portfolio_allocator=allocator_payload,
            current_drawdown_state=str(drawdown_state.get("current_drawdown_state") or "normal"),
            drawdown_entered_at=_coerce_datetime(drawdown_state.get("entered_at")),
            drawdown_transition_reason=str(drawdown_state.get("transition_reason") or "") or None,
            drawdown_policy_adjustments=dict(drawdown_state.get("policy_adjustments") or {}),
            drawdown_capacity_reason=drawdown_capacity_reason,
            selected_symbols=selected_symbols,
            skipped_symbols=skipped_symbols,
            rankings=ranking_payloads,
        )
        self.session.add(self.settings_row)
        self.session.flush()
        record_audit_event(
            self.session,
            event_type="candidate_selection_ranked",
            entity_type="symbol_batch",
            entity_id="tracked_symbols",
            severity="info",
            message="Portfolio rotation candidate ranking completed for tracked symbols.",
            payload={
                "mode": "portfolio_rotation_top_n",
                "max_selected": max_selected,
                "breadth_regime": breadth_regime,
                "breadth_summary": breadth_summary,
                "capacity_reason": capacity_reason,
                "drawdown_capacity_reason": drawdown_capacity_reason,
                "entry_score_threshold": entry_score_threshold,
                "drawdown_state": drawdown_state,
                "portfolio_allocator": allocator_payload,
                "selected_symbols": selected_symbols,
                "skipped_symbols": skipped_symbols,
                "rankings": ranking_payloads,
            },
        )
        return {
            "mode": "portfolio_rotation_top_n",
            "max_selected": max_selected,
            "breadth_regime": breadth_regime,
            "breadth_summary": breadth_summary,
            "capacity_reason": capacity_reason,
            "drawdown_capacity_reason": drawdown_capacity_reason,
            "entry_score_threshold": entry_score_threshold,
            "drawdown_state": drawdown_state,
            "portfolio_allocator": allocator_payload,
            "selected_symbols": selected_symbols,
            "skipped_symbols": skipped_symbols,
            "rankings": ranking_payloads,
        }

    def run_market_refresh_cycle(
        self,
        *,
        symbols: list[str] | None = None,
        timeframe: str | None = None,
        upto_index: int | None = None,
        force_stale: bool = False,
        status: str = "market_refresh",
        trigger_event: str = TriggerEvent.MANUAL.value,
        auto_resume_checked: bool = False,
        include_exchange_sync: bool = False,
    ) -> dict[str, object]:
        auto_resume_result = self._ensure_auto_resume(
            trigger_event=trigger_event,
            auto_resume_checked=auto_resume_checked,
        )
        exchange_sync_result: dict[str, object] | None = None
        if include_exchange_sync and self._should_poll_exchange_state(trigger_event):
            exchange_sync_result = self.run_exchange_sync_cycle(trigger_event=trigger_event)
        selected_symbols = [item.upper() for item in symbols] if symbols else get_effective_symbols(self.settings_row)
        results: list[dict[str, object]] = []
        for symbol in selected_symbols:
            effective_settings = self._effective_symbol_settings(symbol)
            effective_timeframe = timeframe or effective_settings.timeframe
            market_snapshot, market_row = self._collect_market_snapshot(
                symbol=symbol,
                timeframe=effective_timeframe,
                upto_index=upto_index,
                force_stale=force_stale,
            )
            results.append(
                {
                    "symbol": symbol,
                    "timeframe": effective_timeframe,
                    "market_snapshot_id": market_row.id,
                    "snapshot_time": market_snapshot.snapshot_time.isoformat(),
                    "latest_price": market_snapshot.latest_price,
                    "status": status,
                }
            )
        return {
            "symbols": selected_symbols,
            "cycles": len(results),
            "mode": status,
            "results": results,
            "account": self._account_snapshot_preview(),
            "settings": serialize_settings(self.settings_row),
            "auto_resume": auto_resume_result,
            "exchange_sync": exchange_sync_result,
        }

    def run_position_management_cycle(
        self,
        *,
        symbol: str,
        timeframe: str | None = None,
        upto_index: int | None = None,
        force_stale: bool = False,
        trigger_event: str = TriggerEvent.MANUAL.value,
    ) -> dict[str, object]:
        symbol = symbol.upper()
        effective_settings = self._effective_symbol_settings(symbol)
        effective_timeframe = timeframe or effective_settings.timeframe
        open_positions = get_open_positions(self.session, symbol)
        if not open_positions:
            return {
                "symbol": symbol,
                "timeframe": effective_timeframe,
                "status": "no_open_position",
                "new_entries_allowed": False,
                "execution": None,
            }
        market_snapshot, market_row = self._collect_market_snapshot(
            symbol=symbol,
            timeframe=effective_timeframe,
            upto_index=upto_index,
            force_stale=force_stale,
        )
        market_context = build_market_context(
            symbol=symbol,
            base_timeframe=effective_timeframe,
            upto_index=upto_index,
            force_stale=force_stale,
            use_binance=self.settings_row.binance_market_data_enabled,
            binance_testnet_enabled=self.settings_row.binance_testnet_enabled,
            stale_threshold_seconds=self.settings_row.stale_market_seconds,
        )
        higher_timeframe_context = {
            tf: payload for tf, payload in market_context.items() if tf != effective_timeframe
        }
        feature_payload = compute_features(market_snapshot, higher_timeframe_context)
        feature_row = persist_feature_snapshot(self.session, market_row.id, market_snapshot, feature_payload)
        result = apply_position_management(
            self.session,
            self.settings_row,
            symbol=symbol,
            feature_payload=feature_payload,
        )
        return {
            "symbol": symbol,
            "timeframe": effective_timeframe,
            "market_snapshot_id": market_row.id,
            "feature_snapshot_id": feature_row.id,
            "status": str(result.get("status", "monitoring")),
            "new_entries_allowed": False,
            "execution": result.get("position_management_action"),
            "position_management": result,
            "trigger_event": trigger_event,
        }

    def run_entry_plan_watcher_cycle(
        self,
        *,
        symbols: list[str] | None = None,
        trigger_event: str = "entry_plan_watcher",
        upto_index: int | None = None,
        force_stale: bool = False,
        auto_resume_checked: bool = False,
        exchange_sync_checked: bool = False,
        market_snapshot_override: MarketSnapshotPayload | None = None,
    ) -> dict[str, object]:
        auto_resume_result = self._ensure_auto_resume(
            trigger_event=trigger_event,
            auto_resume_checked=auto_resume_checked,
        )
        watched_symbols = (
            [item.upper() for item in symbols if item]
            if symbols
            else sorted({plan.symbol for plan in self._active_pending_entry_plans()})
        )
        results: list[dict[str, object]] = []
        generated_at = utcnow_naive()
        for symbol in watched_symbols:
            active_plans = self._active_pending_entry_plans(symbol=symbol)
            if not active_plans:
                continue
            exchange_sync_result: dict[str, object] | None = None
            if self._should_poll_exchange_state(trigger_event) and not exchange_sync_checked:
                exchange_sync_result = self.run_exchange_sync_cycle(symbol=symbol, trigger_event=trigger_event)
            market_snapshot, market_row = (
                (market_snapshot_override, persist_market_snapshot(self.session, market_snapshot_override))
                if market_snapshot_override is not None
                else self._collect_market_snapshot(
                    symbol=symbol,
                    timeframe=ENTRY_PLAN_WATCH_TIMEFRAME,
                    upto_index=upto_index,
                    force_stale=force_stale,
                )
            )
            runtime_state = summarize_runtime_state(self.settings_row)
            open_positions = get_open_positions(self.session, symbol)
            cadence_profile = self.get_symbol_cadence_profile(
                symbol=symbol,
                timeframe=ENTRY_PLAN_WATCH_TIMEFRAME,
                runtime_state=runtime_state,
                open_positions=open_positions,
                armed_plans=active_plans,
            )
            operational_status = build_operational_status_payload(
                self.settings_row,
                session=self.session,
                runtime_state=runtime_state,
            )
            stale_scopes = [
                scope
                for scope in ("account", "positions", "open_orders", "protective_orders")
                if isinstance(operational_status.sync_freshness_summary.get(scope), dict)
                and (
                    bool(operational_status.sync_freshness_summary[scope].get("stale"))
                    or bool(operational_status.sync_freshness_summary[scope].get("incomplete"))
                )
            ]
            symbol_missing_protection = symbol in runtime_state["missing_protection_symbols"]
            protection_issue = (
                operational_status.operating_state == PROTECTION_REQUIRED_STATE
                or symbol_missing_protection
            )
            control_blocked = (
                operational_status.trading_paused
                or not operational_status.live_execution_ready
            )
            symbol_results: list[dict[str, object]] = []
            for plan in active_plans:
                metadata = self._pending_entry_plan_metadata(plan)
                result_item: dict[str, object] = {
                    "plan": self._pending_entry_plan_snapshot(plan).model_dump(mode="json"),
                    "status": "armed",
                    "execution": None,
                    "risk_result": None,
                }
                if plan.expires_at <= generated_at:
                    self._cancel_pending_entry_plan(
                        plan,
                        reason="PLAN_TTL_EXPIRED",
                        cancel_status="expired",
                        detail={"observed_at": generated_at.isoformat()},
                    )
                    result_item["status"] = "expired"
                    symbol_results.append(result_item)
                    continue
                if self._plan_invalidation_broken(plan, market_snapshot):
                    self._cancel_pending_entry_plan(
                        plan,
                        reason="PLAN_INVALIDATED",
                        detail={
                            "latest_price": market_snapshot.latest_price,
                            "invalidation_price": plan.invalidation_price,
                        },
                    )
                    result_item["status"] = "canceled"
                    symbol_results.append(result_item)
                    continue
                if stale_scopes:
                    self._cancel_pending_entry_plan(
                        plan,
                        reason="PLAN_CANCELED_STALE_SYNC",
                        detail={"stale_scopes": stale_scopes},
                    )
                    result_item["status"] = "canceled"
                    symbol_results.append(result_item)
                    continue
                if protection_issue:
                    self._cancel_pending_entry_plan(
                        plan,
                        reason="PLAN_CANCELED_PROTECTION_BLOCK",
                        detail={"operating_state": operational_status.operating_state},
                    )
                    result_item["status"] = "canceled"
                    symbol_results.append(result_item)
                    continue

                confirm_detail = self._build_plan_confirm_detail(plan, market_snapshot)
                observed_chase_bps = self._plan_chase_bps(plan, market_snapshot.latest_price)
                trigger_details = {
                    **confirm_detail,
                    "observed_chase_bps": round(observed_chase_bps, 6),
                    "max_chase_bps": plan.max_chase_bps,
                    "latest_price": market_snapshot.latest_price,
                    "market_snapshot_id": market_row.id,
                    "market_snapshot_time": market_snapshot.snapshot_time.isoformat(),
                }
                metadata["trigger_details"] = trigger_details
                metadata["last_watch_at"] = generated_at.isoformat()
                metadata["last_watch_snapshot_id"] = market_row.id
                metadata["last_watch_cycle_id"] = f"entry-plan-watch:{plan.id}:{market_row.id}"
                plan.metadata_json = metadata
                self.session.add(plan)
                self.session.flush()
                result_item["plan"] = self._pending_entry_plan_snapshot(plan).model_dump(mode="json")

                if control_blocked:
                    result_item["status"] = "control_blocked"
                    result_item["blocked_reasons"] = list(operational_status.blocked_reasons)
                    symbol_results.append(result_item)
                    continue
                if bool(confirm_detail.get("cancel_recommended")):
                    self._cancel_pending_entry_plan(
                        plan,
                        reason="PLAN_CONFIRM_QUALITY_REJECTED",
                        detail={
                            "quality_score": confirm_detail.get("quality_score"),
                            "quality_threshold": confirm_detail.get("quality_threshold"),
                            "quality_state": confirm_detail.get("quality_state"),
                            "quality_components": confirm_detail.get("quality_components"),
                            "reason": confirm_detail.get("reason"),
                        },
                    )
                    result_item["status"] = "canceled"
                    result_item["blocked_reasons"] = ["PLAN_CONFIRM_QUALITY_REJECTED"]
                    symbol_results.append(result_item)
                    continue
                if not bool(confirm_detail.get("confirm_met")):
                    result_item["status"] = "armed_waiting_confirmation"
                    result_item["blocked_reasons"] = ["PLAN_CONFIRM_QUALITY_LOW"]
                    symbol_results.append(result_item)
                    continue
                if plan.max_chase_bps is not None and observed_chase_bps > plan.max_chase_bps:
                    result_item["status"] = "armed_waiting_reentry"
                    result_item["blocked_reasons"] = ["PLAN_MAX_CHASE_EXCEEDED"]
                    symbol_results.append(result_item)
                    continue

                source_decision_run = self._latest_decision_run(decision_run_id=plan.source_decision_run_id)
                if source_decision_run is None or not isinstance(source_decision_run.output_payload, dict):
                    self._cancel_pending_entry_plan(
                        plan,
                        reason="SOURCE_DECISION_MISSING",
                    )
                    result_item["status"] = "canceled"
                    symbol_results.append(result_item)
                    continue
                source_decision = self._trigger_execution_decision_from_plan(
                    plan,
                    market_snapshot,
                    source_decision=TradeDecision.model_validate(source_decision_run.output_payload),
                )
                trigger_cycle_id = f"entry-plan-trigger:{plan.id}:{market_row.id}"
                correlation_ids = normalize_correlation_ids(
                    cycle_id=trigger_cycle_id,
                    snapshot_id=market_row.id,
                    decision_id=plan.source_decision_run_id,
                )
                risk_result, risk_row = evaluate_risk(
                    self.session,
                    self.settings_row,
                    source_decision,
                    market_snapshot,
                    decision_run_id=plan.source_decision_run_id,
                    market_snapshot_id=market_row.id,
                    execution_mode="live",
                )
                risk_correlation_ids = normalize_correlation_ids(correlation_ids, risk_id=risk_row.id)
                record_audit_event(
                    self.session,
                    event_type="risk_check",
                    entity_type="risk_check",
                    entity_id=str(risk_row.id),
                    severity="warning" if not risk_result.allowed else "info",
                    message="Pending entry plan trigger risk check completed.",
                    payload=risk_result.model_dump(mode="json"),
                    correlation_ids=risk_correlation_ids,
                )
                result_item["risk_result"] = risk_result.model_dump(mode="json")
                if not risk_result.allowed:
                    metadata = self._pending_entry_plan_metadata(plan)
                    metadata["last_blocked_reason_codes"] = list(risk_result.blocked_reason_codes)
                    metadata["last_risk_check_id"] = risk_row.id
                    plan.metadata_json = metadata
                    self.session.add(plan)
                    self.session.flush()
                    result_item["status"] = "risk_blocked"
                    symbol_results.append(result_item)
                    continue
                execution_result = execute_live_trade(
                    self.session,
                    self.settings_row,
                    decision_run_id=plan.source_decision_run_id,
                    decision=source_decision,
                    market_snapshot=market_snapshot,
                    risk_result=risk_result,
                    risk_row=risk_row,
                    cycle_id=trigger_cycle_id,
                    snapshot_id=market_row.id,
                    idempotency_key=plan.idempotency_key,
                )
                result_item["execution"] = execution_result
                success_status = str(execution_result.get("status") or "")
                if success_status in {"filled", "partially_filled", "emergency_exit"} or (
                    success_status == "deduplicated"
                    and str(execution_result.get("dedupe_reason") or "") == "cycle_action_already_completed"
                ):
                    self._mark_pending_entry_plan_triggered(
                        plan,
                        execution_result={
                            "risk_check_id": risk_row.id,
                            **dict(execution_result),
                        },
                        correlation_ids=normalize_correlation_ids(
                            risk_correlation_ids,
                            execution_id=execution_result.get("order_id"),
                        ),
                    )
                    result_item["status"] = "triggered"
                else:
                    result_item["status"] = success_status or "execution_pending"
                symbol_results.append(result_item)
            results.append(
                {
                    "symbol": symbol,
                    "watch_timeframe": ENTRY_PLAN_WATCH_TIMEFRAME,
                    "cadence": cadence_profile,
                    "market_snapshot_id": market_row.id,
                    "market_snapshot_time": market_snapshot.snapshot_time.isoformat(),
                    "latest_price": market_snapshot.latest_price,
                    "exchange_sync": exchange_sync_result,
                    "plans": symbol_results,
                }
            )
        return {
            "workflow": "entry_plan_watcher_cycle",
            "generated_at": generated_at.isoformat(),
            "symbols": watched_symbols,
            "results": results,
            "auto_resume": auto_resume_result,
        }

    @staticmethod
    def _position_review_interval_minutes(
        cadence_profile: dict[str, object],
        *,
        effective_settings: object,
    ) -> int:
        cadence_hint = _as_dict(cadence_profile.get("holding_profile_cadence_hint"))
        hint_minutes = cadence_hint.get("decision_interval_minutes")
        if isinstance(hint_minutes, (int, float)) and int(hint_minutes) > 0:
            return int(hint_minutes)
        effective_cadence = _as_dict(cadence_profile.get("effective_cadence"))
        cadence_minutes = effective_cadence.get("ai_call_interval_minutes")
        if isinstance(cadence_minutes, (int, float)) and int(cadence_minutes) > 0:
            return int(cadence_minutes)
        return max(int(getattr(effective_settings, "ai_call_interval_minutes", 30)), 1)

    @staticmethod
    def _next_due_at(*values: datetime | None) -> datetime | None:
        due_values = [value for value in values if isinstance(value, datetime)]
        return min(due_values) if due_values else None

    @staticmethod
    def _trigger_fingerprint(payload: dict[str, object]) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]

    def _build_review_trigger_payload(
        self,
        *,
        trigger_reason: str,
        symbol: str,
        timeframe: str,
        strategy_engine: str | None,
        holding_profile: str | None,
        assigned_slot: str | None,
        candidate_weight: object,
        reason_codes: list[str],
        last_decision_at: datetime | None,
        triggered_at: datetime,
        fingerprint_material: dict[str, object],
    ) -> AIReviewTriggerPayload:
        return AIReviewTriggerPayload(
            trigger_reason=trigger_reason,  # type: ignore[arg-type]
            symbol=symbol,
            timeframe=timeframe,
            strategy_engine=strategy_engine or None,
            holding_profile=(holding_profile or "scalp"),  # type: ignore[arg-type]
            assigned_slot=assigned_slot or None,
            candidate_weight=_safe_float(candidate_weight, default=0.0)
            if candidate_weight not in {None, ""}
            else None,
            reason_codes=list(dict.fromkeys([str(code) for code in reason_codes if code])),
            trigger_fingerprint=self._trigger_fingerprint(fingerprint_material),
            last_decision_at=last_decision_at,
            triggered_at=triggered_at,
        )

    def build_interval_decision_plan(
        self,
        *,
        symbols: list[str],
        timeframe: str | None = None,
        upto_index: int | None = None,
        force_stale: bool = False,
        triggered_at: datetime | None = None,
    ) -> dict[str, object]:
        generated_at = triggered_at or utcnow_naive()
        tracked_symbols = [item.upper() for item in symbols if item]
        runtime_state = summarize_runtime_state(self.settings_row)
        missing_protection_symbols = {
            str(item).upper()
            for item in runtime_state.get("missing_protection_symbols", [])
            if item
        }
        effective_lookup = {
            item.symbol: item
            for item in get_effective_symbol_schedule(self.settings_row)
            if item.enabled and item.symbol in tracked_symbols
        }
        open_positions_by_symbol = {
            symbol: list(get_open_positions(self.session, symbol))
            for symbol in tracked_symbols
            if symbol in effective_lookup
        }
        flat_symbols = [
            symbol
            for symbol in tracked_symbols
            if symbol in effective_lookup and not open_positions_by_symbol.get(symbol)
        ]
        candidate_selection = (
            self._rank_candidate_symbols(
                decision_symbols=flat_symbols,
                timeframe=timeframe,
                upto_index=upto_index,
                force_stale=force_stale,
            )
            if flat_symbols
            else {
                "mode": "no_flat_symbols",
                "breadth_summary": {},
                "breadth_regime": "mixed",
                "selected_symbols": [],
                "skipped_symbols": [],
                "rankings": [],
            }
        )
        ranking_lookup = self._selection_ranking_lookup(candidate_selection)
        plans: list[dict[str, object]] = []
        for symbol in tracked_symbols:
            effective = effective_lookup.get(symbol)
            if effective is None:
                continue
            effective_timeframe = timeframe or effective.timeframe
            open_positions = list(open_positions_by_symbol.get(symbol) or [])
            latest_decision_run = self._latest_symbol_decision_run(
                symbol=symbol,
                timeframe=effective_timeframe,
            )
            last_decision_at = latest_decision_run.created_at if latest_decision_run is not None else None
            last_ai_invoked_at = self._latest_symbol_ai_invoked_at(
                symbol=symbol,
                timeframe=effective_timeframe,
            )
            cadence_profile = self.get_symbol_cadence_profile(
                symbol=symbol,
                timeframe=effective_timeframe,
                runtime_state=runtime_state,
                open_positions=open_positions,
            )
            review_interval_minutes = self._position_review_interval_minutes(
                cadence_profile,
                effective_settings=effective,
            )
            position_review_due_at = (
                last_decision_at + timedelta(minutes=review_interval_minutes)
                if last_decision_at is not None
                else None
            )
            backstop_due_at = (
                last_decision_at + timedelta(minutes=effective.ai_backstop_interval_minutes)
                if effective.ai_backstop_enabled and last_decision_at is not None
                else None
            )
            next_ai_review_due_at = self._next_due_at(position_review_due_at, backstop_due_at)
            trigger_payload: AIReviewTriggerPayload | None = None
            selection_context: dict[str, object] | None = None
            trigger_deduped = False
            last_ai_skip_reason: str | None = None
            latest_metadata = (
                latest_decision_run.metadata_json
                if latest_decision_run is not None and isinstance(latest_decision_run.metadata_json, dict)
                else {}
            )
            latest_trigger_payload = _as_dict(latest_metadata.get("ai_trigger"))
            latest_output_payload = (
                latest_decision_run.output_payload
                if latest_decision_run is not None and isinstance(latest_decision_run.output_payload, dict)
                else {}
            )

            if open_positions:
                position_row = open_positions[0]
                position_metadata = _as_dict(getattr(position_row, "metadata_json", {}))
                position_management = _as_dict(position_metadata.get("position_management"))
                trigger_reason: str | None = None
                reason_codes: list[str] = []
                if symbol in missing_protection_symbols:
                    trigger_reason = "protection_review_event"
                    reason_codes.append("MISSING_PROTECTIVE_ORDERS")
                elif str(runtime_state.get("operating_state") or "") == PROTECTION_REQUIRED_STATE:
                    trigger_reason = "protection_review_event"
                    reason_codes.append(PROTECTION_REQUIRED_STATE)
                elif backstop_due_at is not None and backstop_due_at <= generated_at:
                    trigger_reason = "periodic_backstop_due"
                    reason_codes.append("PERIODIC_BACKSTOP_DUE")
                elif position_review_due_at is not None and position_review_due_at <= generated_at:
                    trigger_reason = "open_position_recheck_due"
                    reason_codes.append("OPEN_POSITION_RECHECK_DUE")

                if trigger_reason is not None:
                    strategy_engine_name = _strategy_engine_name_from_payload(
                        latest_metadata,
                        latest_output_payload,
                    )
                    slot_allocation = _as_dict(latest_metadata.get("slot_allocation"))
                    holding_profile = (
                        str(position_management.get("holding_profile") or "")
                        or str(cadence_profile.get("active_holding_profile") or "")
                        or str(latest_metadata.get("holding_profile") or "")
                        or "scalp"
                    )
                    trigger_payload = self._build_review_trigger_payload(
                        trigger_reason=trigger_reason,
                        symbol=symbol,
                        timeframe=effective_timeframe,
                        strategy_engine=strategy_engine_name or None,
                        holding_profile=holding_profile,
                        assigned_slot=str(slot_allocation.get("assigned_slot") or "") or None,
                        candidate_weight=slot_allocation.get("candidate_weight"),
                        reason_codes=reason_codes,
                        last_decision_at=last_decision_at,
                        triggered_at=generated_at,
                        fingerprint_material={
                            "trigger_reason": trigger_reason,
                            "symbol": symbol,
                            "timeframe": effective_timeframe,
                            "holding_profile": holding_profile,
                            "strategy_engine": strategy_engine_name,
                            "assigned_slot": slot_allocation.get("assigned_slot"),
                            "candidate_weight": round(
                                _safe_float(slot_allocation.get("candidate_weight"), default=0.0),
                                6,
                            ),
                            "reason_codes": sorted(reason_codes),
                            "position_side": getattr(position_row, "side", None),
                            "position_status": getattr(position_row, "status", None),
                            "position_quantity": round(
                                _safe_float(getattr(position_row, "quantity", None), default=0.0),
                                8,
                            ),
                            "protection_state": bool(symbol in missing_protection_symbols),
                        },
                    )
            else:
                selection_context = self._selection_context_from_candidate_selection(
                    symbol=symbol,
                    candidate_selection=candidate_selection,
                )
                ranking_payload = ranking_lookup.get(symbol, {})
                candidate_payload = _as_dict(selection_context.get("candidate"))
                candidate_decision = str(candidate_payload.get("decision") or "").lower()
                strategy_engine_name = str(
                    selection_context.get("strategy_engine")
                    or candidate_payload.get("strategy_engine")
                    or "unspecified"
                )
                trigger_reason: str | None = None
                reason_codes = [
                    str(code)
                    for code in candidate_payload.get("rationale_codes", [])
                    if code not in {None, ""}
                ] if isinstance(candidate_payload.get("rationale_codes"), list) else []
                if bool(ranking_payload.get("selected")) and candidate_decision in {"long", "short"}:
                    trigger_reason = (
                        "breakout_exception_event"
                        if strategy_engine_name == "breakout_exception_engine"
                        or str(selection_context.get("entry_mode") or "") == "breakout_confirm"
                        else "entry_candidate_event"
                    )
                elif backstop_due_at is not None and backstop_due_at <= generated_at:
                    trigger_reason = "periodic_backstop_due"
                    reason_codes = list(dict.fromkeys(reason_codes + ["PERIODIC_BACKSTOP_DUE"]))

                if trigger_reason is not None:
                    trigger_payload = self._build_review_trigger_payload(
                        trigger_reason=trigger_reason,
                        symbol=symbol,
                        timeframe=effective_timeframe,
                        strategy_engine=strategy_engine_name or None,
                        holding_profile=str(
                            selection_context.get("holding_profile")
                            or candidate_payload.get("holding_profile")
                            or "scalp"
                        ),
                        assigned_slot=str(selection_context.get("assigned_slot") or "") or None,
                        candidate_weight=selection_context.get("candidate_weight"),
                        reason_codes=reason_codes,
                        last_decision_at=last_decision_at,
                        triggered_at=generated_at,
                        fingerprint_material={
                            "trigger_reason": trigger_reason,
                            "symbol": symbol,
                            "timeframe": effective_timeframe,
                            "strategy_engine": strategy_engine_name,
                            "holding_profile": selection_context.get("holding_profile")
                            or candidate_payload.get("holding_profile"),
                            "assigned_slot": selection_context.get("assigned_slot"),
                            "candidate_weight": round(
                                _safe_float(selection_context.get("candidate_weight"), default=0.0),
                                6,
                            ),
                            "reason_codes": sorted(reason_codes),
                            "decision": candidate_payload.get("decision"),
                            "scenario": candidate_payload.get("scenario"),
                            "selection_reason": selection_context.get("selection_reason"),
                            "slot_conviction_score": round(
                                _safe_float(selection_context.get("slot_conviction_score"), default=0.0),
                                6,
                            ),
                            "meta_gate_probability": round(
                                _safe_float(selection_context.get("meta_gate_probability"), default=0.0),
                                6,
                            ),
                            "score_total": round(
                                _safe_float(_as_dict(selection_context.get("score")).get("total_score"), default=0.0),
                                6,
                            ),
                        },
                    )

            if (
                trigger_payload is not None
                and trigger_payload.trigger_reason not in {"periodic_backstop_due", "manual_review_event"}
                and str(latest_trigger_payload.get("trigger_reason") or "") == trigger_payload.trigger_reason
                and str(latest_trigger_payload.get("trigger_fingerprint") or "") == trigger_payload.trigger_fingerprint
            ):
                trigger_deduped = True
                last_ai_skip_reason = "TRIGGER_DEDUPED"

            if trigger_payload is None:
                last_ai_skip_reason = "NO_EVENT"

            plans.append(
                {
                    "symbol": symbol,
                    "timeframe": effective_timeframe,
                    "cadence": cadence_profile,
                    "selection_context": selection_context,
                    "trigger": trigger_payload.model_dump(mode="json") if trigger_payload is not None else None,
                    "trigger_deduped": trigger_deduped,
                    "last_decision_at": last_decision_at.isoformat() if last_decision_at is not None else None,
                    "last_ai_invoked_at": last_ai_invoked_at.isoformat() if last_ai_invoked_at is not None else None,
                    "next_ai_review_due_at": (
                        next_ai_review_due_at.isoformat()
                        if next_ai_review_due_at is not None
                        else None
                    ),
                    "last_ai_skip_reason": last_ai_skip_reason,
                }
            )
        return {
            "generated_at": generated_at.isoformat(),
            "candidate_selection": candidate_selection,
            "plans": plans,
        }


    def run_decision_cycle(
        self,
        symbol: str | None = None,
        timeframe: str | None = None,
        trigger_event: str = TriggerEvent.MANUAL.value,
        upto_index: int | None = None,
        force_stale: bool = False,
        auto_resume_checked: bool = False,
        logic_variant: str = "improved",
        exchange_sync_checked: bool = False,
        include_inline_position_management: bool = False,
        market_snapshot_override: MarketSnapshotPayload | None = None,
        market_context_override: dict[str, MarketSnapshotPayload] | None = None,
        selection_context: dict[str, object] | None = None,
        review_trigger: AIReviewTriggerPayload | dict[str, object] | None = None,
    ) -> dict[str, object]:
        auto_resume_result = self._ensure_auto_resume(
            trigger_event=trigger_event,
            auto_resume_checked=auto_resume_checked,
        )
        symbol = (symbol or self.settings_row.default_symbol).upper()
        effective_settings = self._effective_symbol_settings(symbol)
        timeframe = timeframe or effective_settings.timeframe
        exchange_sync_result: dict[str, object] | None = None
        if self._should_poll_exchange_state(trigger_event) and not exchange_sync_checked:
            exchange_sync_result = self.run_exchange_sync_cycle(symbol=symbol, trigger_event=trigger_event)
        if market_snapshot_override is None:
            market_snapshot, market_row = self._collect_market_snapshot(
                symbol=symbol,
                timeframe=timeframe,
                upto_index=upto_index,
                force_stale=force_stale,
            )
        else:
            market_snapshot = market_snapshot_override
            market_row = persist_market_snapshot(self.session, market_snapshot)
        cycle_id = self._build_cycle_id(
            trigger_event=trigger_event,
            symbol=symbol,
            snapshot_id=market_row.id,
        )
        market_context = (
            dict(market_context_override)
            if market_context_override is not None
            else build_market_context(
                symbol=symbol,
                base_timeframe=timeframe,
                upto_index=upto_index,
                force_stale=force_stale,
                use_binance=self.settings_row.binance_market_data_enabled,
                binance_testnet_enabled=self.settings_row.binance_testnet_enabled,
                stale_threshold_seconds=self.settings_row.stale_market_seconds,
            )
        )
        higher_timeframe_context = {
            tf: payload for tf, payload in market_context.items() if tf != timeframe
        }
        lead_market_features = self._build_lead_market_features(
            base_timeframe=timeframe,
            upto_index=upto_index,
            force_stale=force_stale,
        )
        if not self.settings_row.ai_enabled:
            cadence_profile = self.get_symbol_cadence_profile(
                symbol=symbol,
                timeframe=timeframe,
            )
            return {
                "symbol": symbol,
                "cycle_id": cycle_id,
                "market_snapshot_id": market_row.id,
                "feature_snapshot_id": None,
                "decision_run_id": None,
                "risk_check_id": None,
                "chief_review_run_id": None,
                "decision": None,
                "risk_result": None,
                "execution": None,
                "status": "market_data_only",
                "cadence": cadence_profile,
                "skip_reason": "AI_DISABLED",
                "ai_skipped_reason": "AI_DISABLED",
                "account": self._account_snapshot_preview(),
                "settings": serialize_settings(self.settings_row),
                "auto_resume": auto_resume_result,
                "exchange_sync": exchange_sync_result,
            }
        feature_payload = compute_features(
            market_snapshot,
            higher_timeframe_context,
            lead_market_features=lead_market_features,
        )
        feature_row = persist_feature_snapshot(self.session, market_row.id, market_snapshot, feature_payload)
        open_positions = get_open_positions(self.session, symbol)
        if (
            not open_positions
            and not isinstance(selection_context, dict)
            and trigger_event != TriggerEvent.REPLAY.value
        ):
            selection_context = self._default_selection_context_for_symbol(
                symbol=symbol,
                timeframe=timeframe,
                upto_index=upto_index,
                force_stale=force_stale,
            )
        effective_selection_context = dict(selection_context) if isinstance(selection_context, dict) else {}
        if not effective_selection_context and trigger_event != TriggerEvent.REPLAY.value:
            effective_selection_context = self._default_selection_context_for_symbol(
                symbol=symbol,
                timeframe=timeframe,
                upto_index=upto_index,
                force_stale=force_stale,
            )
        position_management_context = build_position_management_context(
            open_positions[0] if open_positions else None,
            feature_payload=feature_payload,
            settings_row=self.settings_row,
        )
        position_management_result: dict[str, object] | None = None
        if include_inline_position_management and open_positions and self._should_execute_live(trigger_event):
            position_management_result = apply_position_management(
                self.session,
                self.settings_row,
                symbol=symbol,
                feature_payload=feature_payload,
            )
            open_positions = get_open_positions(self.session, symbol)
            position_management_context = dict(
                position_management_result.get("position_management_context") or position_management_context
            )
        latest_pnl = get_latest_pnl_snapshot(self.session, self.settings_row)
        runtime_state = summarize_runtime_state(self.settings_row)
        latest_decision_run = self._latest_symbol_decision_run(
            symbol=symbol,
            timeframe=timeframe,
        )
        last_decision_at = latest_decision_run.created_at if latest_decision_run is not None else None
        previous_ai_invoked_at = self._latest_symbol_ai_invoked_at(
            symbol=symbol,
            timeframe=timeframe,
        )
        decision_reference = self._build_decision_reference_payload(
            symbol=symbol,
            timeframe=timeframe,
            market_snapshot=market_snapshot,
            market_row=market_row,
            runtime_state=runtime_state,
        )
        if self._should_skip_same_candle_entry(
            symbol=symbol,
            timeframe=timeframe,
            market_snapshot=market_snapshot,
            has_open_position=bool(open_positions),
        ):
            return {
                "symbol": symbol,
                "cycle_id": cycle_id,
                "market_snapshot_id": market_row.id,
                "feature_snapshot_id": feature_row.id,
                "decision_run_id": None,
                "risk_check_id": None,
                "chief_review_run_id": None,
                "decision": None,
                "risk_result": None,
                "execution": None,
                "status": "same_candle_skipped",
                "cadence": self.get_symbol_cadence_profile(
                    symbol=symbol,
                    timeframe=timeframe,
                    runtime_state=runtime_state,
                    open_positions=open_positions,
                ),
                "skip_reason": "SAME_CANDLE_ENTRY_GUARD",
                "ai_skipped_reason": None,
                "decision_reference": decision_reference,
                "account": account_snapshot_to_dict(latest_pnl),
                "settings": serialize_settings(self.settings_row),
                "auto_resume": auto_resume_result,
                "exchange_sync": exchange_sync_result,
            }
        effective_leverage_cap = min(
            self.settings_row.max_leverage,
            HARD_MAX_GLOBAL_LEVERAGE,
            get_symbol_leverage_cap(symbol),
        )
        drawdown_state = self._sync_drawdown_state()
        risk_context = {
            "max_risk_per_trade": min(self.settings_row.max_risk_per_trade, HARD_MAX_RISK_PER_TRADE),
            "max_leverage": effective_leverage_cap,
            "symbol_risk_tier": get_symbol_risk_tier(symbol),
            "daily_pnl": latest_pnl.daily_pnl,
            "consecutive_losses": latest_pnl.consecutive_losses,
            "drawdown_state": drawdown_state,
            "operating_state": runtime_state["operating_state"],
            "protection_recovery_status": runtime_state["protection_recovery_status"],
            "missing_protection_symbols": runtime_state["missing_protection_symbols"],
            "missing_protection_items": runtime_state["missing_protection_items"],
            "risk_budget": build_ai_risk_budget_context(
                self.session,
                self.settings_row,
                decision_symbol=symbol,
                equity=latest_pnl.equity,
            ),
            "position_management_context": position_management_context,
        }
        if isinstance(selection_context, dict) and selection_context:
            risk_context["universe_breadth"] = selection_context.get("universe_breadth") or {}
            risk_context["selection_context"] = dict(selection_context)
        adaptive_signal_context = build_adaptive_signal_context(
            self.session,
            enabled=self.settings_row.adaptive_signal_enabled,
            symbol=symbol,
            timeframe=timeframe,
            regime=feature_payload.regime.primary_regime,
            settings_row=self.settings_row,
        )
        risk_context["adaptive_signal_context"] = adaptive_signal_context
        setup_cluster_context = self._build_setup_cluster_context(
            symbol=symbol,
            timeframe=timeframe,
            regime=feature_payload.regime.primary_regime,
            trend_alignment=feature_payload.regime.trend_alignment,
        )
        risk_context["setup_cluster_context"] = setup_cluster_context
        cadence_profile = self.get_symbol_cadence_profile(
            symbol=symbol,
            timeframe=timeframe,
            runtime_state=runtime_state,
            open_positions=open_positions,
            feature_payload=feature_payload,
            adaptive_signal_context=adaptive_signal_context,
            setup_cluster_context=setup_cluster_context,
            include_adaptive_underperformance=True,
        )
        review_interval_minutes = self._position_review_interval_minutes(
            cadence_profile,
            effective_settings=effective_settings,
        )
        next_ai_review_due_at = utcnow_naive() + timedelta(
            minutes=(
                review_interval_minutes
                if open_positions
                else effective_settings.ai_backstop_interval_minutes
                if effective_settings.ai_backstop_enabled
                else max(
                    int(cadence_profile["effective_cadence"]["ai_call_interval_minutes"]),
                    1,
                )
            )
        )
        review_trigger_payload = (
            review_trigger
            if isinstance(review_trigger, AIReviewTriggerPayload)
            else AIReviewTriggerPayload.model_validate(review_trigger)
            if isinstance(review_trigger, dict) and review_trigger
            else None
        )
        latest_decision_metadata = (
            latest_decision_run.metadata_json
            if latest_decision_run is not None and isinstance(latest_decision_run.metadata_json, dict)
            else {}
        )
        latest_decision_output = (
            latest_decision_run.output_payload
            if latest_decision_run is not None and isinstance(latest_decision_run.output_payload, dict)
            else {}
        )
        latest_decision_input = (
            latest_decision_run.input_payload
            if latest_decision_run is not None and isinstance(latest_decision_run.input_payload, dict)
            else {}
        )
        if review_trigger_payload is None and trigger_event == TriggerEvent.MANUAL.value:
            candidate_payload = _as_dict(effective_selection_context.get("candidate"))
            latest_slot_allocation = _as_dict(latest_decision_metadata.get("slot_allocation"))
            strategy_engine_name = (
                str(effective_selection_context.get("strategy_engine") or "")
            ) or _strategy_engine_name_from_payload(
                latest_decision_metadata,
                latest_decision_output,
            )
            holding_profile = (
                str(effective_selection_context.get("holding_profile") or "")
            ) or str(cadence_profile.get("active_holding_profile") or "") or str(
                latest_decision_metadata.get("holding_profile") or "scalp"
            )
            reason_codes = (
                [str(code) for code in candidate_payload.get("rationale_codes", []) if code not in {None, ""}]
                if isinstance(candidate_payload.get("rationale_codes"), list)
                else []
            )
            review_trigger_payload = self._build_review_trigger_payload(
                trigger_reason="manual_review_event",
                symbol=symbol,
                timeframe=timeframe,
                strategy_engine=strategy_engine_name or None,
                holding_profile=holding_profile or "scalp",
                assigned_slot=(
                    str(effective_selection_context.get("assigned_slot") or "")
                )
                or str(latest_slot_allocation.get("assigned_slot") or "")
                or None,
                candidate_weight=(
                    effective_selection_context.get("candidate_weight")
                    if effective_selection_context
                    else latest_slot_allocation.get("candidate_weight")
                ),
                reason_codes=reason_codes,
                last_decision_at=last_decision_at,
                triggered_at=utcnow_naive(),
                fingerprint_material={
                    "trigger_reason": "manual_review_event",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "strategy_engine": strategy_engine_name,
                    "holding_profile": holding_profile or "scalp",
                    "assigned_slot": (
                        effective_selection_context.get("assigned_slot")
                        if effective_selection_context
                        else latest_slot_allocation.get("assigned_slot")
                    ),
                    "candidate_weight": round(
                        _safe_float(
                            effective_selection_context.get("candidate_weight")
                            if effective_selection_context
                            else latest_slot_allocation.get("candidate_weight"),
                            default=0.0,
                        ),
                        6,
                    ),
                    "reason_codes": sorted(reason_codes),
                    "open_position": bool(open_positions),
                },
            )
        openai_gate = get_openai_call_gate(
            self.session,
            self.settings_row,
            AgentRole.TRADING_DECISION.value,
            trigger_event,
            has_openai_key=bool(self.credentials.openai_api_key),
            symbol=symbol,
            cooldown_minutes_override=(
                0
                if review_trigger_payload is not None
                and review_trigger_payload.trigger_reason != "manual_review_event"
                else int(cadence_profile["effective_cadence"]["ai_call_interval_minutes"])
            ),
            manual_guard_minutes_override=max(
                2,
                min(int(cadence_profile["effective_cadence"]["ai_call_interval_minutes"]), 5),
            ),
        )
        ai_skipped_reason = str(cadence_profile.get("ai_skipped_reason") or "") or None
        if review_trigger_payload is not None:
            if review_trigger_payload.trigger_reason == "protection_review_event":
                ai_skipped_reason = "PROTECTION_REVIEW_DETERMINISTIC_ONLY"
            else:
                ai_skipped_reason = None
        if ai_skipped_reason is None and not openai_gate.allowed:
            ai_skipped_reason = str(openai_gate.reason).upper() or None
        use_ai = openai_gate.allowed and ai_skipped_reason is None
        ai_context = build_ai_decision_context(
            market_snapshot=market_snapshot,
            features=feature_payload,
            risk_context=risk_context,
            selection_context=effective_selection_context,
            review_trigger=review_trigger_payload,
            decision_reference=decision_reference,
            previous_decision_output=latest_decision_output,
            previous_decision_metadata=latest_decision_metadata,
            previous_input_payload=latest_decision_input,
            previous_ai_invoked_at=previous_ai_invoked_at,
        )
        ai_prior_context = build_ai_prior_context(
            self.session,
            ai_context=ai_context,
            selection_context=effective_selection_context,
            feature_payload=feature_payload,
        )
        ai_context = ai_context.model_copy(update={"prior_context": ai_prior_context})
        ai_context_payload = ai_context.model_dump(mode="json")
        decision, provider_name, decision_metadata = self.trading_agent.run(
            market_snapshot,
            feature_payload,
            open_positions,
            risk_context,
            use_ai=use_ai,
            max_input_candles=self.settings_row.ai_max_input_candles,
            logic_variant=logic_variant,
            ai_context=ai_context,
        )
        meta_gate_result = evaluate_meta_gate(
            decision,
            feature_payload=feature_payload,
            selection_context=dict(selection_context) if isinstance(selection_context, dict) else {},
            decision_metadata=decision_metadata,
        )
        decision_generated_at = utcnow_naive()
        resolved_last_ai_invoked_at = (
            decision_generated_at
            if str(decision_metadata.get("source") or "") == "llm"
            else previous_ai_invoked_at
        )
        decision_metadata = {
            **decision_metadata,
            "gate": openai_gate.as_metadata(),
            "logic_variant": logic_variant,
            "symbol": symbol,
            "timeframe": timeframe,
            "holding_profile": getattr(decision, "holding_profile", "scalp"),
            "holding_profile_reason": getattr(decision, "holding_profile_reason", None),
            "cadence": cadence_profile,
            "ai_skipped_reason": ai_skipped_reason,
            "effective_cadence": dict(cadence_profile.get("effective_cadence") or {}),
            "analysis_context": _decision_analysis_context(
                feature_payload,
                universe_breadth=effective_selection_context.get("universe_breadth") if effective_selection_context else None,
            ),
            "selection_context": effective_selection_context or None,
            "slot_allocation": (
                dict(effective_selection_context.get("slot_allocation"))
                if isinstance(effective_selection_context.get("slot_allocation"), dict)
                else None
            ),
            "ai_context": ai_context_payload,
            "ai_context_version": ai_context.ai_context_version,
            "ai_trigger": review_trigger_payload.model_dump(mode="json") if review_trigger_payload is not None else None,
            "last_ai_trigger_reason": review_trigger_payload.trigger_reason if review_trigger_payload is not None else None,
            "last_ai_invoked_at": (
                resolved_last_ai_invoked_at.isoformat()
                if resolved_last_ai_invoked_at is not None
                else None
            ),
            "next_ai_review_due_at": next_ai_review_due_at.isoformat(),
            "trigger_deduped": False,
            "trigger_fingerprint": (
                review_trigger_payload.trigger_fingerprint
                if review_trigger_payload is not None
                else None
            ),
            "last_ai_skip_reason": ai_skipped_reason,
            "engine_prior_classification": decision_metadata.get("engine_prior_classification"),
            "capital_efficiency_classification": decision_metadata.get("capital_efficiency_classification"),
            "session_prior_classification": decision_metadata.get("session_prior_classification"),
            "time_of_day_prior_classification": decision_metadata.get("time_of_day_prior_classification"),
            "prior_penalty_level": decision_metadata.get("prior_penalty_level"),
            "prior_reason_codes": decision_metadata.get("prior_reason_codes"),
            "sample_threshold_satisfied": decision_metadata.get("sample_threshold_satisfied"),
            "confidence_adjustment_applied": decision_metadata.get("confidence_adjustment_applied"),
            "abstain_due_to_prior_and_quality": decision_metadata.get("abstain_due_to_prior_and_quality"),
            "expected_payoff_efficiency_hint_summary": decision_metadata.get("expected_payoff_efficiency_hint_summary"),
            "drawdown_state": drawdown_state,
            "position_management": position_management_result or {"position_management_context": position_management_context},
            "holding_profile_context": decision_metadata.get("holding_profile_context"),
            "setup_cluster_state": decision_metadata.get("setup_cluster_state"),
            "meta_gate": meta_gate_result.model_dump(mode="json"),
            "cycle_id": cycle_id,
            "snapshot_id": market_row.id,
        }
        decision_run = persist_agent_run(
            self.session,
            AgentRole.TRADING_DECISION,
            trigger_event,
            build_trading_decision_input_payload(
                market_snapshot=market_snapshot,
                higher_timeframe_context=higher_timeframe_context,
                feature_payload=feature_payload,
                risk_context=risk_context,
                decision_reference=decision_reference,
                ai_trigger=review_trigger_payload.model_dump(mode="json") if review_trigger_payload is not None else None,
                ai_context=ai_context,
            ),
            decision,
            provider_name=provider_name,
            metadata_json=decision_metadata,
        )
        decision_correlation_ids = normalize_correlation_ids(
            cycle_id=cycle_id,
            snapshot_id=market_row.id,
            decision_id=decision_run.id,
        )
        record_audit_event(
            self.session,
            event_type="agent_output",
            entity_type="agent_run",
            entity_id=str(decision_run.id),
            message="Trading decision generated.",
            payload={
                "provider": provider_name,
                "decision": decision.model_dump(mode="json"),
                "analysis_context": _decision_analysis_context(
                    feature_payload,
                    universe_breadth=effective_selection_context.get("universe_breadth") if effective_selection_context else None,
                ),
                "selection_context": effective_selection_context or None,
                "slot_allocation": (
                    dict(effective_selection_context.get("slot_allocation"))
                    if isinstance(effective_selection_context.get("slot_allocation"), dict)
                    else None
                ),
                "holding_profile_context": decision_metadata.get("holding_profile_context"),
                "drawdown_state": drawdown_state,
                "setup_cluster_state": decision_metadata.get("setup_cluster_state"),
                "meta_gate": meta_gate_result.model_dump(mode="json"),
                "ai_trigger": decision_metadata.get("ai_trigger"),
                "prompt_family": decision_metadata.get("prompt_family"),
                "bounded_output_applied": decision_metadata.get("bounded_output_applied"),
                "fallback_reason_codes": decision_metadata.get("fallback_reason_codes"),
                "fail_closed_applied": decision_metadata.get("fail_closed_applied"),
                "engine_prior_classification": decision_metadata.get("engine_prior_classification"),
                "capital_efficiency_classification": decision_metadata.get("capital_efficiency_classification"),
                "session_prior_classification": decision_metadata.get("session_prior_classification"),
                "time_of_day_prior_classification": decision_metadata.get("time_of_day_prior_classification"),
                "prior_penalty_level": decision_metadata.get("prior_penalty_level"),
                "prior_reason_codes": decision_metadata.get("prior_reason_codes"),
                "sample_threshold_satisfied": decision_metadata.get("sample_threshold_satisfied"),
                "confidence_adjustment_applied": decision_metadata.get("confidence_adjustment_applied"),
                "abstain_due_to_prior_and_quality": decision_metadata.get("abstain_due_to_prior_and_quality"),
                "expected_payoff_efficiency_hint_summary": decision_metadata.get("expected_payoff_efficiency_hint_summary"),
            },
            correlation_ids=decision_correlation_ids,
        )
        if str(decision_metadata.get("source") or "") == "llm":
            record_audit_event(
                self.session,
                event_type="decision_ai_invoked",
                entity_type="decision_run",
                entity_id=str(decision_run.id),
                severity="info",
                message="AI inference was invoked for the current decision review.",
                payload={
                    "symbol": symbol,
                    "provider": provider_name,
                    "trigger": decision_metadata.get("ai_trigger"),
                    "next_ai_review_due_at": decision_metadata.get("next_ai_review_due_at"),
                },
                correlation_ids=decision_correlation_ids,
            )
        if ai_skipped_reason is not None:
            record_audit_event(
                self.session,
                event_type="decision_ai_skipped",
                entity_type="decision_run",
                entity_id=str(decision_run.id),
                severity="info",
                message="AI inference was skipped and deterministic decision logic was used.",
                payload={
                    "symbol": symbol,
                    "cadence_mode": cadence_profile.get("mode"),
                    "cadence_reasons": list(cadence_profile.get("reasons") or []),
                    "ai_skipped_reason": ai_skipped_reason,
                    "gate": openai_gate.as_metadata(),
                    "trigger": decision_metadata.get("ai_trigger"),
                },
                correlation_ids=decision_correlation_ids,
            )
        if bool(decision_metadata.get("bounded_output_applied")) or bool(decision_metadata.get("fail_closed_applied")):
            record_audit_event(
                self.session,
                event_type="decision_ai_bounded",
                entity_type="decision_run",
                entity_id=str(decision_run.id),
                severity="warning" if decision_metadata.get("fail_closed_applied") else "info",
                message="AI output was bounded or fail-closed before risk approval.",
                payload={
                    "symbol": symbol,
                    "provider": provider_name,
                    "prompt_family": decision_metadata.get("prompt_family"),
                    "trigger_type": decision_metadata.get("trigger_type"),
                    "provider_status": decision_metadata.get("provider_status"),
                    "fallback_reason_codes": decision_metadata.get("fallback_reason_codes"),
                    "fail_closed_applied": decision_metadata.get("fail_closed_applied"),
                    "should_abstain": decision_metadata.get("should_abstain"),
                    "abstain_reason_codes": decision_metadata.get("abstain_reason_codes"),
                },
                correlation_ids=decision_correlation_ids,
            )
        if not open_positions and decision.decision == "hold":
            self._record_decision_skip_event(
                symbol=symbol,
                timeframe=timeframe,
                market_row=market_row,
                market_snapshot=market_snapshot,
                decision_run=decision_run,
                decision=decision,
                decision_metadata=decision_metadata,
                ai_skipped_reason=ai_skipped_reason,
                selection_context=selection_context,
            )
        if decision.decision in {"long", "short"}:
            unresolved_guard = get_unresolved_submission_guard(
                self.settings_row,
                symbol=symbol,
                action=decision.decision,
            )
            if unresolved_guard is not None and bool(unresolved_guard.get("guard_active", True)):
                reason_code = str(unresolved_guard.get("guard_reason_code") or "UNRESOLVED_SUBMISSION_GUARD_ACTIVE")
                blocked_risk_result = RiskCheckResult(
                    allowed=False,
                    decision=decision.decision,
                    reason_codes=[reason_code],
                    blocked_reason_codes=[reason_code],
                    approved_risk_pct=0.0,
                    approved_leverage=0.0,
                    operating_mode="hold",
                    effective_leverage_cap=get_symbol_leverage_cap(symbol),
                    symbol_risk_tier=get_symbol_risk_tier(symbol),
                    exposure_metrics={},
                    cycle_id=cycle_id,
                    snapshot_id=market_row.id,
                )
                record_audit_event(
                    self.session,
                    event_type="risk_check_skipped",
                    entity_type="decision_run",
                    entity_id=str(decision_run.id),
                    severity="warning",
                    message="Risk and execution were skipped because unresolved submission guard is active.",
                    payload={
                        "symbol": symbol,
                        "decision": decision.decision,
                        "reason_code": reason_code,
                        "unresolved_submission_guard": unresolved_guard,
                    },
                    correlation_ids=decision_correlation_ids,
                )
                execution_result = {
                    "status": "blocked",
                    "reason_codes": [reason_code],
                    "decision": decision.decision,
                    "unresolved_submission_guard": unresolved_guard,
                }
                chief_review, chief_provider_name, chief_metadata = self.chief_review_agent.run(
                    decision=decision,
                    risk_result=blocked_risk_result,
                    health_events=self._latest_health_events(),
                    alerts=self._latest_alerts(),
                    use_ai=False,
                )
                chief_run = persist_agent_run(
                    self.session,
                    AgentRole.CHIEF_REVIEW,
                    TriggerEvent.POST_DECISION.value,
                    {
                        "decision": decision.model_dump(mode="json"),
                        "risk_result": blocked_risk_result.model_dump(mode="json"),
                        "alerts": [alert.payload for alert in self._latest_alerts()],
                    },
                    chief_review,
                    provider_name=chief_provider_name,
                    metadata_json=chief_metadata,
                )
                return {
                    "symbol": symbol,
                    "cycle_id": cycle_id,
                    "market_snapshot_id": market_row.id,
                    "feature_snapshot_id": feature_row.id,
                    "decision_run_id": decision_run.id,
                    "risk_check_id": None,
                    "chief_review_run_id": chief_run.id,
                    "decision": decision.model_dump(mode="json"),
                    "risk_result": blocked_risk_result.model_dump(mode="json"),
                    "execution": execution_result,
                    "entry_plan": None,
                    "canceled_entry_plans": [],
                    "status": "blocked_pre_risk",
                    "cadence": cadence_profile,
                    "skip_reason": str(cadence_profile.get("skip_reason") or reason_code),
                    "ai_skipped_reason": ai_skipped_reason,
                    "last_ai_trigger_reason": decision_metadata.get("last_ai_trigger_reason"),
                    "last_ai_invoked_at": decision_metadata.get("last_ai_invoked_at"),
                    "next_ai_review_due_at": decision_metadata.get("next_ai_review_due_at"),
                    "trigger_deduped": bool(decision_metadata.get("trigger_deduped", False)),
                    "trigger_fingerprint": decision_metadata.get("trigger_fingerprint"),
                    "last_ai_skip_reason": decision_metadata.get("last_ai_skip_reason"),
                    "decision_reference": decision_reference,
                    "logic_variant": logic_variant,
                    "account": account_snapshot_to_dict(get_latest_pnl_snapshot(self.session, self.settings_row)),
                    "settings": serialize_settings(self.settings_row),
                    "auto_resume": auto_resume_result,
                    "exchange_sync": exchange_sync_result,
                }
        risk_result, risk_row = evaluate_risk(
            self.session,
            self.settings_row,
            decision,
            market_snapshot,
            decision_run_id=decision_run.id,
            market_snapshot_id=market_row.id,
            execution_mode="historical_replay" if trigger_event == "historical_replay" else "live",
            decision_context={
                "decision_agreement": decision_metadata.get("decision_agreement"),
                "suppression_context": decision_metadata.get("suppression_context"),
                "setup_cluster_state": decision_metadata.get("setup_cluster_state"),
                "meta_gate": decision_metadata.get("meta_gate"),
                "slot_allocation": decision_metadata.get("slot_allocation"),
                "holding_profile_context": decision_metadata.get("holding_profile_context"),
            },
        )
        risk_correlation_ids = normalize_correlation_ids(
            decision_correlation_ids,
            risk_id=risk_row.id,
        )
        record_audit_event(
            self.session,
            event_type="risk_check",
            entity_type="risk_check",
            entity_id=str(risk_row.id),
            severity="warning" if not risk_result.allowed else "info",
            message="Risk check completed.",
            payload=risk_result.model_dump(mode="json"),
            correlation_ids=risk_correlation_ids,
        )
        if not open_positions and not risk_result.allowed:
            self._record_risk_skip_event(
                symbol=symbol,
                timeframe=timeframe,
                market_row=market_row,
                market_snapshot=market_snapshot,
                decision_run=decision_run,
                decision=decision,
                risk_row=risk_row,
                risk_result=risk_result,
                selection_context=selection_context,
            )

        canceled_entry_plans: list[dict[str, object]] = []
        armed_entry_plan: PendingEntryPlanSnapshot | None = None
        if trigger_event != "historical_replay" and decision.decision in {"hold", "long", "short"}:
            canceled_entry_plans = [
                snapshot.model_dump(mode="json")
                for snapshot in self._cancel_symbol_entry_plans_from_decision(
                    symbol=symbol,
                    decision_side=decision.decision,
                    decision_run_id=decision_run.id,
                    cycle_id=cycle_id,
                    snapshot_id=market_row.id,
                )
            ]
        if (
            trigger_event != "historical_replay"
            and not open_positions
            and self._plan_entry_allowed_without_trigger(decision, risk_result)
        ):
            armed_entry_plan = self._pending_entry_plan_snapshot(
                self._arm_pending_entry_plan(
                    decision=decision,
                    decision_run=decision_run,
                    risk_result=risk_result,
                    risk_row_id=risk_row.id,
                    feature_payload=feature_payload,
                    cycle_id=cycle_id,
                    snapshot_id=market_row.id,
                )
            )

        execution_result: dict[str, object] | None = None
        if (
            armed_entry_plan is None
            and risk_result.allowed
            and decision.decision != "hold"
            and self._should_execute_live(trigger_event)
        ):
            execution_result = execute_live_trade(
                self.session,
                self.settings_row,
                decision_run_id=decision_run.id,
                decision=decision,
                market_snapshot=market_snapshot,
                risk_result=risk_result,
                risk_row=risk_row,
                cycle_id=cycle_id,
                snapshot_id=market_row.id,
            )
        elif armed_entry_plan is None and risk_result.allowed and decision.decision != "hold":
            record_audit_event(
                self.session,
                event_type="live_execution_skipped",
                entity_type="decision_run",
                entity_id=str(decision_run.id),
                severity="info",
                message="Live execution skipped for non-live trigger.",
                payload={"trigger_event": trigger_event, "symbol": symbol},
            )
        elif armed_entry_plan is None and not risk_result.allowed:
            create_alert(self.session, category="risk", severity="warning", title="Trade blocked", message="Deterministic risk policy blocked the execution.", payload={"reason_codes": risk_result.reason_codes, "decision": decision.decision, "symbol": symbol})

        chief_review, chief_provider_name, chief_metadata = self.chief_review_agent.run(
            decision=decision,
            risk_result=risk_result,
            health_events=self._latest_health_events(),
            alerts=self._latest_alerts(),
            use_ai=False,
        )
        chief_run = persist_agent_run(
            self.session,
            AgentRole.CHIEF_REVIEW,
            TriggerEvent.POST_DECISION.value,
            {"decision": decision.model_dump(mode="json"), "risk_result": risk_result.model_dump(mode="json"), "alerts": [alert.payload for alert in self._latest_alerts()]},
            chief_review,
            provider_name=chief_provider_name,
            metadata_json=chief_metadata,
        )
        return {
            "symbol": symbol,
            "cycle_id": cycle_id,
            "market_snapshot_id": market_row.id,
            "feature_snapshot_id": feature_row.id,
            "decision_run_id": decision_run.id,
            "risk_check_id": risk_row.id,
            "chief_review_run_id": chief_run.id,
            "decision": decision.model_dump(mode="json"),
            "risk_result": risk_result.model_dump(mode="json"),
            "execution": execution_result,
            "entry_plan": armed_entry_plan.model_dump(mode="json") if armed_entry_plan is not None else None,
            "canceled_entry_plans": canceled_entry_plans,
            "status": "entry_plan_armed" if armed_entry_plan is not None else "completed",
            "cadence": cadence_profile,
            "skip_reason": str(cadence_profile.get("skip_reason") or "") or None,
            "ai_skipped_reason": ai_skipped_reason,
            "last_ai_trigger_reason": decision_metadata.get("last_ai_trigger_reason"),
            "last_ai_invoked_at": decision_metadata.get("last_ai_invoked_at"),
            "next_ai_review_due_at": decision_metadata.get("next_ai_review_due_at"),
            "trigger_deduped": bool(decision_metadata.get("trigger_deduped", False)),
            "trigger_fingerprint": decision_metadata.get("trigger_fingerprint"),
            "last_ai_skip_reason": decision_metadata.get("last_ai_skip_reason"),
            "decision_reference": decision_reference,
            "logic_variant": logic_variant,
            "account": account_snapshot_to_dict(get_latest_pnl_snapshot(self.session, self.settings_row)),
            "settings": serialize_settings(self.settings_row),
            "auto_resume": auto_resume_result,
            "exchange_sync": exchange_sync_result,
        }


    def run_selected_symbols_cycle(
        self,
        *,
        symbols: list[str] | None = None,
        trigger_event: str = TriggerEvent.MANUAL.value,
        timeframe: str | None = None,
        upto_index: int | None = None,
        force_stale: bool = False,
        auto_resume_checked: bool = False,
        logic_variant: str = "improved",
    ) -> dict[str, object]:
        auto_resume_result = self._ensure_auto_resume(
            trigger_event=trigger_event,
            auto_resume_checked=auto_resume_checked,
        )
        selected_symbols = [item.upper() for item in symbols] if symbols else get_effective_symbols(self.settings_row)
        decision_symbols = [
            effective.symbol
            for effective in get_effective_symbol_schedule(self.settings_row)
            if effective.enabled and effective.symbol in selected_symbols
        ]
        candidate_selection = self._rank_candidate_symbols(
            decision_symbols=decision_symbols,
            timeframe=timeframe,
            upto_index=upto_index,
            force_stale=force_stale,
        )
        selected_cycle_symbols = [
            str(item).upper()
            for item in candidate_selection.get("selected_symbols", decision_symbols)
            if item
        ] or decision_symbols
        results: list[dict[str, object]] = []
        failed_symbols: list[str] = []
        for symbol in selected_cycle_symbols:
            try:
                results.append(
                    self.run_decision_cycle(
                        symbol=symbol,
                        timeframe=timeframe,
                        trigger_event=trigger_event,
                        upto_index=upto_index,
                        force_stale=force_stale,
                        auto_resume_checked=True,
                        logic_variant=logic_variant,
                        exchange_sync_checked=True,
                        selection_context=self._selection_context_from_candidate_selection(
                            symbol=symbol,
                            candidate_selection=candidate_selection,
                        ),
                    )
                )
            except Exception as exc:
                failed_symbols.append(symbol)
                record_audit_event(
                    self.session,
                    event_type="decision_cycle_failed",
                    entity_type="symbol",
                    entity_id=symbol,
                    severity="error",
                    message="Decision cycle failed for tracked symbol.",
                    payload={"trigger_event": trigger_event, "error": str(exc)},
                )
                record_health_event(
                    self.session,
                    component="decision_cycle",
                    status="error",
                    message="Tracked symbol decision cycle failed.",
                    payload={"symbol": symbol, "trigger_event": trigger_event, "error": str(exc)},
                )
                results.append(
                    {
                        "symbol": symbol,
                        "status": "failed",
                        "error": str(exc),
                    }
                )
        return {
            "symbols": selected_cycle_symbols,
            "tracked_symbols": decision_symbols,
            "cycles": len(results),
            "mode": "market_data_only" if not self.settings_row.ai_enabled else "ai_active",
            "failed_symbols": failed_symbols,
            "logic_variant": logic_variant,
            "candidate_selection": candidate_selection,
            "results": results,
            "account": self._account_snapshot_preview() if not self.settings_row.ai_enabled else account_snapshot_to_dict(get_latest_pnl_snapshot(self.session, self.settings_row)),
            "settings": serialize_settings(self.settings_row),
            "auto_resume": auto_resume_result,
            "exchange_sync": None,
        }
