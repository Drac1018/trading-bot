from __future__ import annotations

from datetime import datetime, timedelta
from math import sqrt
from uuid import uuid4

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from trading_mvp.enums import AgentRole, TriggerEvent
from trading_mvp.models import (
    AgentRun,
    Alert,
    Execution,
    MarketSnapshot,
    PendingEntryPlan,
    PnLSnapshot,
    RiskCheck,
    SchedulerRun,
    SystemHealthEvent,
    UIFeedback,
)
from trading_mvp.providers import build_model_provider
from trading_mvp.schemas import (
    MarketSnapshotPayload,
    PendingEntryPlanSnapshot,
    TradeDecision,
    TradeDecisionCandidate,
    TradeDecisionCandidateScore,
)
from trading_mvp.services.account import (
    account_snapshot_to_dict,
    get_latest_pnl_snapshot,
    get_open_positions,
)
from trading_mvp.services.agents import (
    ChiefReviewAgent,
    IntegrationPlannerAgent,
    TradingDecisionAgent,
    UIUXAgent,
    build_trading_decision_input_payload,
    persist_agent_run,
)
from trading_mvp.services.ai_usage import get_openai_call_gate
from trading_mvp.services.adaptive_signal import build_adaptive_signal_context
from trading_mvp.services.audit import (
    create_alert,
    normalize_correlation_ids,
    record_audit_event,
    record_health_event,
)
from trading_mvp.services.execution import apply_position_management, execute_live_trade, sync_live_state
from trading_mvp.services.features import compute_features, persist_feature_snapshot
from trading_mvp.services.market_data import (
    build_market_context,
    build_market_snapshot,
    persist_market_snapshot,
)
from trading_mvp.services.pause_control import attempt_auto_resume
from trading_mvp.services.position_management import build_position_management_context
from trading_mvp.services.risk import (
    HARD_MAX_GLOBAL_LEVERAGE,
    HARD_MAX_RISK_PER_TRADE,
    build_ai_risk_budget_context,
    evaluate_risk,
    get_symbol_leverage_cap,
    get_symbol_risk_tier,
)
from trading_mvp.services.runtime_state import (
    PROTECTION_REQUIRED_STATE,
    build_sync_freshness_summary,
    get_unresolved_submission_guard,
    mark_sync_skipped,
    set_candidate_selection_detail,
    summarize_runtime_state,
)
from trading_mvp.services.settings import (
    build_operational_status_payload,
    get_effective_symbols,
    get_effective_symbol_schedule,
    get_effective_symbol_settings,
    get_or_create_settings,
    get_runtime_credentials,
    serialize_settings,
)
from trading_mvp.time_utils import utcnow_naive

ACTIVE_ENTRY_PLAN_STATUS = "armed"
ENTRY_PLAN_WATCH_TIMEFRAME = "1m"
ENTRY_PLAN_NON_STRUCTURAL_BLOCKERS = {
    "CHASE_LIMIT_EXCEEDED",
    "ENTRY_TRIGGER_NOT_MET",
    "SLIPPAGE_THRESHOLD_EXCEEDED",
}


def _decision_analysis_context(feature_payload) -> dict[str, object]:
    regime = feature_payload.regime
    return {
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
    }


def _clamp_score(value: float, *, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


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
        self.integration_agent = IntegrationPlannerAgent(provider)
        self.ui_agent = UIUXAgent(provider)

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
        if last_candle is None:
            return {
                "zone_entered": False,
                "confirm_met": False,
                "reason": "NO_1M_CANDLE",
            }
        zone_entered = TradingOrchestrator._plan_zone_interacted(plan, last_candle)
        candle_range = max(last_candle.high - last_candle.low, 1e-9)
        lower_wick_ratio = max(min(last_candle.open, last_candle.close) - last_candle.low, 0.0) / candle_range
        upper_wick_ratio = max(last_candle.high - max(last_candle.open, last_candle.close), 0.0) / candle_range
        if plan.side == "long":
            close_reclaimed = last_candle.close >= plan.entry_zone_max
            structure_break = previous_candle is not None and last_candle.close > previous_candle.high
            wick_reclaim = lower_wick_ratio >= 0.35 and last_candle.close >= (last_candle.low + candle_range * 0.6)
        else:
            close_reclaimed = last_candle.close <= plan.entry_zone_min
            structure_break = previous_candle is not None and last_candle.close < previous_candle.low
            wick_reclaim = upper_wick_ratio >= 0.35 and last_candle.close <= (last_candle.high - candle_range * 0.6)
        confirm_met = zone_entered and (close_reclaimed or structure_break or wick_reclaim)
        return {
            "zone_entered": zone_entered,
            "confirm_met": confirm_met,
            "close_reclaimed": close_reclaimed,
            "structure_break": structure_break,
            "wick_reclaim": wick_reclaim,
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
        decision_rows = self._recent_symbol_decisions(symbol, limit=6)
        if not decision_rows:
            return 0.55
        decision_ids = [row.id for row in decision_rows]
        risk_rows = list(
            self.session.scalars(
                select(RiskCheck)
                .where(RiskCheck.decision_run_id.in_(decision_ids))
                .order_by(desc(RiskCheck.created_at))
            )
        )
        approval_by_decision: dict[int, bool] = {}
        for row in risk_rows:
            if row.decision_run_id is not None and row.decision_run_id not in approval_by_decision:
                approval_by_decision[row.decision_run_id] = bool(row.allowed)
        approvals = sum(1 for decision_id in decision_ids if approval_by_decision.get(decision_id))
        approval_rate = approvals / max(len(decision_ids), 1)
        return _clamp_score(0.35 + (approval_rate * 0.65))

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
        avg_slippage = sum(abs(float(row.slippage_pct or 0.0)) for row in executions) / max(len(executions), 1)
        threshold = max(float(self.settings_row.slippage_threshold_pct or 0.0), 0.001)
        return _clamp_score(1.0 - min(avg_slippage / threshold, 1.0))

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

    def _build_selection_candidate(
        self,
        *,
        symbol: str,
        timeframe: str,
        upto_index: int | None,
        force_stale: bool,
        missing_protection_symbols: set[str],
        total_open_positions: int,
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
        feature_payload = compute_features(market_snapshot, higher_timeframe_context)
        open_positions = get_open_positions(self.session, symbol)
        priority = bool(open_positions) or symbol in missing_protection_symbols

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
        recent_signal_performance = self._recent_signal_performance_score(symbol)
        slippage_sensitivity = self._slippage_sensitivity_score(symbol)
        confidence_consistency = self._confidence_consistency_score(symbol, decision=decision)
        exposure_impact = self._candidate_exposure_impact_score(
            symbol=symbol,
            priority=priority,
            total_open_positions=total_open_positions,
        )
        base_total = (
            (regime_fit * 0.25)
            + (expected_rr * 0.2)
            + (recent_signal_performance * 0.15)
            + (slippage_sensitivity * 0.1)
            + (exposure_impact * 0.1)
            + (confidence_consistency * 0.2)
        )

        candidate = TradeDecisionCandidate(
            candidate_id=f"{symbol}:{timeframe}:{scenario}",
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
                f"REGIME_{regime.primary_regime.upper()}",
                f"TREND_{regime.trend_alignment.upper()}",
            ],
            explanation_short=explanation_short,
            explanation_detailed=(
                f"{symbol} {timeframe} candidate selected from market snapshot context. "
                f"priority={priority}, regime_fit={regime_fit:.3f}, expected_rr={expected_rr_ratio:.3f}."
            ),
        )
        score = TradeDecisionCandidateScore(
            regime_fit=round(regime_fit, 6),
            expected_rr=round(expected_rr, 6),
            recent_signal_performance=round(recent_signal_performance, 6),
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
            "returns": _rolling_returns_from_snapshot(market_snapshot),
            "market_snapshot": market_snapshot,
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
        if not self.settings_row.ai_enabled:
            set_candidate_selection_detail(
                self.settings_row,
                generated_at=generated_at,
                mode="disabled_ai_off",
                max_selected=len(decision_symbols),
                selected_symbols=decision_symbols,
                skipped_symbols=[],
                rankings=[],
            )
            self.session.add(self.settings_row)
            self.session.flush()
            return {
                "mode": "disabled_ai_off",
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

        max_selected = min(max(3, len([item for item in candidate_rows if bool(item.get("priority"))])), len(candidate_rows))
        selected_symbols: list[str] = []
        selected_rows: list[dict[str, object]] = []
        ranking_payloads: list[dict[str, object]] = []
        skipped_symbols: list[str] = []

        for item in candidate_rows:
            candidate = item["candidate"]
            score = item["score"]
            symbol = str(item["symbol"])
            returns = item["returns"]
            priority = bool(item.get("priority"))
            max_abs_correlation = 0.0
            for selected in selected_rows:
                correlation = abs(_pearson_correlation(returns, selected["returns"])) if returns and selected["returns"] else 0.0
                max_abs_correlation = max(max_abs_correlation, correlation)
            correlation_penalty = round(max(0.0, max_abs_correlation - 0.55) * 0.9, 6)
            selected_flag = False
            selection_reason = "capacity_reached"
            if priority:
                selected_flag = True
                selection_reason = "priority_position_or_protection"
            elif len(selected_rows) < max_selected:
                adjusted_total = float(score.total_score) - correlation_penalty
                if max_abs_correlation >= 0.92 and len(selected_rows) > 0:
                    selected_flag = False
                    selection_reason = "correlation_limit"
                else:
                    selected_flag = adjusted_total >= 0.28
                    selection_reason = "ranked_top_n" if selected_flag else "score_below_threshold"
                    score.total_score = round(adjusted_total, 6)
            score.correlation_penalty = correlation_penalty
            ranking_payload = {
                "symbol": symbol,
                "priority": priority,
                "selected": selected_flag,
                "selection_reason": selection_reason,
                "max_abs_correlation": round(max_abs_correlation, 6),
                "candidate": candidate.model_dump(mode="json"),
                "score": score.model_dump(mode="json"),
            }
            ranking_payloads.append(ranking_payload)
            if selected_flag:
                selected_symbols.append(symbol)
                selected_rows.append(item)
            else:
                skipped_symbols.append(symbol)

        set_candidate_selection_detail(
            self.settings_row,
            generated_at=generated_at,
            mode="correlation_aware_top_n",
            max_selected=max_selected,
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
            message="Correlation-aware candidate ranking completed for tracked symbols.",
            payload={
                "mode": "correlation_aware_top_n",
                "max_selected": max_selected,
                "selected_symbols": selected_symbols,
                "skipped_symbols": skipped_symbols,
                "rankings": ranking_payloads,
            },
        )
        return {
            "mode": "correlation_aware_top_n",
            "max_selected": max_selected,
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
                if not bool(confirm_detail.get("confirm_met")):
                    result_item["status"] = "armed_waiting_confirmation"
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
        if not self.settings_row.ai_enabled:
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
                "account": self._account_snapshot_preview(),
                "settings": serialize_settings(self.settings_row),
                "auto_resume": auto_resume_result,
                "exchange_sync": exchange_sync_result,
            }
        feature_payload = compute_features(market_snapshot, higher_timeframe_context)
        feature_row = persist_feature_snapshot(self.session, market_row.id, market_snapshot, feature_payload)
        open_positions = get_open_positions(self.session, symbol)
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
        risk_context = {
            "max_risk_per_trade": min(self.settings_row.max_risk_per_trade, HARD_MAX_RISK_PER_TRADE),
            "max_leverage": effective_leverage_cap,
            "symbol_risk_tier": get_symbol_risk_tier(symbol),
            "daily_pnl": latest_pnl.daily_pnl,
            "consecutive_losses": latest_pnl.consecutive_losses,
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
            "adaptive_signal_context": build_adaptive_signal_context(
                self.session,
                enabled=self.settings_row.adaptive_signal_enabled,
                symbol=symbol,
                timeframe=timeframe,
                regime=feature_payload.regime.primary_regime,
            ),
        }
        openai_gate = get_openai_call_gate(
            self.session,
            self.settings_row,
            AgentRole.TRADING_DECISION.value,
            trigger_event,
            has_openai_key=bool(self.credentials.openai_api_key),
            symbol=symbol,
            cooldown_minutes_override=effective_settings.ai_call_interval_minutes,
            manual_guard_minutes_override=max(2, min(effective_settings.ai_call_interval_minutes, 5)),
        )
        decision, provider_name, decision_metadata = self.trading_agent.run(
            market_snapshot,
            feature_payload,
            open_positions,
            risk_context,
            use_ai=openai_gate.allowed,
            max_input_candles=self.settings_row.ai_max_input_candles,
            logic_variant=logic_variant,
        )
        decision_metadata = {
            **decision_metadata,
            "gate": openai_gate.as_metadata(),
            "logic_variant": logic_variant,
            "symbol": symbol,
            "timeframe": timeframe,
            "effective_cadence": {
                "decision_cycle_interval_minutes": effective_settings.decision_cycle_interval_minutes,
                "ai_call_interval_minutes": effective_settings.ai_call_interval_minutes,
            },
            "analysis_context": _decision_analysis_context(feature_payload),
            "position_management": position_management_result or {"position_management_context": position_management_context},
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
            payload={"provider": provider_name, "decision": decision.model_dump(mode="json")},
            correlation_ids=decision_correlation_ids,
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


    def run_integration_review(self, triggered_by: str = TriggerEvent.SCHEDULED.value) -> dict[str, object]:
        if not self.settings_row.ai_enabled:
            return {"status": "skipped", "reason": "AI_DISABLED"}
        openai_gate = get_openai_call_gate(
            self.session,
            self.settings_row,
            AgentRole.INTEGRATION_PLANNER.value,
            triggered_by,
            has_openai_key=bool(self.credentials.openai_api_key),
        )
        metrics_summary = {
            "agent_runs": int(self.session.scalar(select(func.count()).select_from(AgentRun)) or 0),
            "risk_rejects": int(self.session.scalar(select(func.count()).select_from(RiskCheck).where(RiskCheck.allowed.is_(False))) or 0),
            "scheduler_runs": int(self.session.scalar(select(func.count()).select_from(SchedulerRun)) or 0),
            "tracked_symbols": get_effective_symbols(self.settings_row),
        }
        output, provider_name, metadata = self.integration_agent.run(
            metrics_summary=metrics_summary,
            health_events=self._latest_health_events(),
            use_ai=openai_gate.allowed,
        )
        metadata = {**metadata, "gate": openai_gate.as_metadata()}
        run = persist_agent_run(
            self.session,
            AgentRole.INTEGRATION_PLANNER,
            triggered_by,
            {"metrics_summary": metrics_summary},
            output,
            provider_name=provider_name,
            metadata_json=metadata,
        )
        return {"agent_run_id": run.id, "items": output.model_dump(mode="json")}


    def run_ui_review(self, triggered_by: str = TriggerEvent.SCHEDULED.value) -> dict[str, object]:
        if not self.settings_row.ai_enabled:
            return {"status": "skipped", "reason": "AI_DISABLED"}
        feedback_rows = list(self.session.scalars(select(UIFeedback).order_by(desc(UIFeedback.created_at)).limit(20)))
        openai_gate = get_openai_call_gate(
            self.session,
            self.settings_row,
            AgentRole.UI_UX.value,
            triggered_by,
            has_openai_key=bool(self.credentials.openai_api_key),
        )
        output, provider_name, metadata = self.ui_agent.run(feedback_rows, use_ai=openai_gate.allowed)
        metadata = {**metadata, "gate": openai_gate.as_metadata()}
        run = persist_agent_run(
            self.session,
            AgentRole.UI_UX,
            triggered_by,
            {"feedback_count": len(feedback_rows)},
            output,
            provider_name=provider_name,
            metadata_json=metadata,
        )
        return {"agent_run_id": run.id, "items": output.model_dump(mode="json")}


    def run_daily_review_window(self, triggered_by: str = TriggerEvent.SCHEDULED.value) -> dict[str, object]:
        return {
            "status": "skipped",
            "reason": "DAILY_REVIEW_NO_ACTIVE_WORKFLOW",
            "triggered_by": triggered_by,
        }
